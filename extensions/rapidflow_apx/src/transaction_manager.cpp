#include "rapidflow_apx/residual_nlf_state.h"
#include "rapidflow_apx/neighbor_buffer_pool.h"
#include "rapidflow_apx/transaction_manager.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/nlf_index.h"
#include "rapidflow_apx/nlf_signatures.h"
#include <new>
#include <chrono>

namespace rapidflow_apx {

namespace {
struct ReentrancyGuard {
    bool& flag;
    explicit ReentrancyGuard(bool& f) : flag(f) { flag = true; }
    ~ReentrancyGuard() { flag = false; }
};

class TransactionScope {
public:
    TransactionScope(
        ApxAuthoritativeGraph& graph,
        ApxTypedCodec& codec,
        ApxTupleSink& sink,
        ApxNlfIndex* nlf = nullptr,
        EngineCounters* counters = nullptr
    ) : graph_(graph),
        codec_(codec),
        sink_(sink),
        codec_cp_(codec.create_checkpoint()),
        nlf_(nlf),
        counters_(counters),
        committed_(false) {
        graph_.begin_transaction();
        sink_.start_step();
    }

    ~TransactionScope() noexcept {
        rollback_if_needed();
    }

    void rollback_if_needed() noexcept {
        if (!committed_) {
            if (nlf_) {
                nlf_->rollback(nlf_undo_, counters_);
            }
            graph_.rollback_transaction();
            codec_.restore_checkpoint(codec_cp_);
            sink_.abort_step();
        }
    }

    void commit() noexcept {
        graph_.commit_transaction();
        if (nlf_) {
            nlf_->finalize(nlf_undo_);
        }
        committed_ = true;
    }

    bool is_committed() const noexcept { return committed_; }
    NlfUndoRecord& nlf_undo() noexcept { return nlf_undo_; }

private:
    ApxAuthoritativeGraph& graph_;
    ApxTypedCodec& codec_;
    ApxTupleSink& sink_;
    CodecCheckpoint codec_cp_;
    ApxNlfIndex* nlf_{nullptr};
    EngineCounters* counters_{nullptr};
    NlfUndoRecord nlf_undo_{};
    bool committed_{false};
};
} // namespace

ApxTransactionManager::ApxTransactionManager(
    ApxAuthoritativeGraph& graph,
    ApxTypedCodec& codec,
    ApxTupleSink& sink
) : graph_(graph), codec_(codec), sink_(sink) {}

StepResult ApxTransactionManager::execute_update(
    const UpdateOperation& update,
    SearchCallback callback
) {
    // 0. Fail-closed session poison guard & Reentrancy Guard
    if (session_poisoned_) {
        return {StepStatus::INVARIANT_VIOLATION, "Transaction manager session poisoned due to previous invariant violation", {}, 0, 0};
    }
    if (is_in_transaction_) {
        return {StepStatus::REENTRANCY_BLOCKED, "Reentrancy detected: cannot execute transaction within an active transaction", {}};
    }
    ReentrancyGuard guard(is_in_transaction_);

    // 1. Transaction Scope provides RAII containment and zero-allocation rollback
    TransactionScope tx_scope(graph_, codec_, sink_, nlf_index_, counters_);

    auto abort_and_rollback = [&](StepStatus status, const std::string& err) -> StepResult {
        // Run rollback BEFORE creating/returning error payload
        tx_scope.rollback_if_needed();
        return {status, err, {}, 0, 0};
    };

    auto abort_oom_noalloc = [&]() noexcept -> StepResult {
        tx_scope.rollback_if_needed();
        StepResult res;
        res.status = StepStatus::RESOURCE_EXHAUSTED;
        try {
            res.error_message = "std::bad_alloc"; // Fits within SSO buffer (14 <= 15) on libstdc++
        } catch (...) {
        }
        res.committed = false;
        return res;
    };

    // 2. Prevalidation: Vertex existence & endpoints
    if (!codec_.has_data_vertex(update.ext_u)) {
        return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Data vertex u not registered in codec: " + std::to_string(update.ext_u));
    }
    if (!codec_.has_data_vertex(update.ext_v)) {
        return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Data vertex v not registered in codec: " + std::to_string(update.ext_v));
    }
    if (update.ext_u == update.ext_v) {
        return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Self-loops are not permitted: " + std::to_string(update.ext_u));
    }

    uint32_t dense_u = 0;
    uint32_t dense_v = 0;
    uint32_t dense_label = 0;

    try {
        dense_u = codec_.get_data_vertex(update.ext_u);
        dense_v = codec_.get_data_vertex(update.ext_v);
        dense_label = codec_.intern_edge_label(update.label);
    } catch (const std::bad_alloc&) {
        return abort_oom_noalloc();
    } catch (const std::exception& ex) {
        return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, std::string("Codec exception: ") + ex.what());
    }

    // Prevalidation: Graph edge status
    if (update.op == UpdateOp::INSERT) {
        if (graph_.has_edge(dense_u, dense_v)) {
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Edge already exists in graph");
        }
    } else if (update.op == UpdateOp::DELETE) {
        if (!graph_.has_edge(dense_u, dense_v)) {
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Edge does not exist in graph");
        }
        auto lookup = graph_.get_edge_label(dense_u, dense_v);
        if (!lookup.exists || lookup.label != dense_label) {
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Edge label mismatch for deletion");
        }
    } else {
        return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Unknown update operation");
    }

    // Prepare NLF update for INSERT before graph mutation
    PreparedNlfUpdate prepared_insert;
    if (update.op == UpdateOp::INSERT && nlf_index_ && signatures_) {
        try {
            nlf_index_->prepare_edge_update(
                graph_, *signatures_, dense_u, dense_v, dense_label, true,
                prepared_insert, tx_scope.nlf_undo()
            );
        } catch (const InvariantViolationException& ex) {
            session_poisoned_ = true;
            return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("NLF internal invariant violation on insert prepare: ") + ex.what());
        } catch (const std::bad_alloc&) {
            return abort_oom_noalloc();
        } catch (const std::exception& ex) {
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, std::string("NLF prepare insert failure: ") + ex.what());
        }
        if (fault_point_ == FaultPoint::AFTER_NLF_PREPARE) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after NLF prepare");
        }
    }

    // Fault Injection check: BEFORE_MUTATION
    if (fault_point_ == FaultPoint::BEFORE_MUTATION) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected before mutation");
    }

    // 3. Snapshot Sequencing:
    // INSERT sees G_after -> apply mutation before search callback
    uint64_t update_time_ns = 0;
    if (update.op == UpdateOp::INSERT) {
        if (fault_point_ == FaultPoint::ALLOCATION_FAILURE_MUTATION) {
            try {
                throw std::bad_alloc();
            } catch (const std::bad_alloc&) {
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Simulated allocation failure std::bad_alloc during insert mutation");
            }
        }

        bool sim_rev_fail = (fault_point_ == FaultPoint::BETWEEN_FORWARD_REVERSE_MUTATION);
        bool applied = false;
        try {
            if (profiling_enabled_) {
                auto t0 = std::chrono::steady_clock::now();
                applied = graph_.apply_insert(dense_u, dense_v, dense_label, sim_rev_fail);
                auto t1 = std::chrono::steady_clock::now();
                update_time_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
            } else {
                applied = graph_.apply_insert(dense_u, dense_v, dense_label, sim_rev_fail);
            }
        } catch (const InvariantViolationException& ex) {
            session_poisoned_ = true;
            return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("Insert mutation invariant violation: ") + ex.what());
        } catch (const std::bad_alloc&) {
            return abort_oom_noalloc();
        } catch (const std::exception& ex) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, std::string("Insert mutation exception: ") + ex.what());
        }

        if (!applied) {
            if (sim_rev_fail) {
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected between forward and reverse mutation (symmetric rollback verified)");
            }
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Failed to apply insert mutation");
        }

        if (fault_point_ == FaultPoint::AFTER_GRAPH_BEFORE_NLF) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after graph mutation before NLF mutation");
        }

        if (nlf_index_ && signatures_) {
            if (fault_point_ == FaultPoint::AFTER_FIRST_NLF_CELL && prepared_insert.count > 1) {
                PreparedNlfUpdate partial;
                partial.count = 1;
                partial.valid = true;
                partial.mutations[0] = prepared_insert.mutations[0];
                nlf_index_->apply_prepared_update(partial, counters_);
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after first NLF cell write");
            }
            nlf_index_->apply_prepared_update(prepared_insert, counters_);
        }
    }

    // Fault Injection check: AFTER_MUTATION
    if (fault_point_ == FaultPoint::AFTER_MUTATION) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after mutation");
    }

    // 4. Callback execution with strong exception containment
    bool callback_ok = false;
    try {
        callback_ok = callback(graph_, update, dense_u, dense_v, dense_label);
    } catch (const FaultInjectedException& ex) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, std::string("Fault injected in callback: ") + ex.what());
    } catch (const NeighborPoolInvariantViolation& ex) {
        session_poisoned_ = true;
        return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("Neighbor pool invariant violation: ") + ex.what());
    } catch (const ResidualInvariantViolation& ex) {
        session_poisoned_ = true;
        return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("Residual NLF invariant violation: ") + ex.what());
    } catch (const InvariantViolationException& ex) {
        session_poisoned_ = true;
        return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("Internal invariant violation: ") + ex.what());
    } catch (const DuplicateEmissionException& ex) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, std::string("Duplicate raw emission detected in callback: ") + ex.what());
    } catch (const NonInjectiveMappingException& ex) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, std::string("Non-injective mapping detected in callback: ") + ex.what());
    } catch (const WorkBudgetExceededException& ex) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, std::string("Work budget exceeded in callback: ") + ex.what());
    } catch (const std::bad_alloc&) {
        return abort_oom_noalloc();
    } catch (const std::exception& ex) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, std::string("Callback exception: ") + ex.what());
    } catch (...) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, "Unknown callback exception");
    }

    if (!callback_ok) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, "Callback returned false");
    }

    // Fault Injection check: DURING_CALLBACK
    if (fault_point_ == FaultPoint::DURING_CALLBACK) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected during callback");
    }

    // 5. Post-callback finalization
    // DELETE sees G_before -> apply mutation after search callback
    if (update.op == UpdateOp::DELETE) {
        PreparedNlfUpdate prepared_delete;
        if (nlf_index_ && signatures_) {
            try {
                nlf_index_->prepare_edge_update(
                    graph_, *signatures_, dense_u, dense_v, dense_label, false,
                    prepared_delete, tx_scope.nlf_undo()
                );
            } catch (const InvariantViolationException& ex) {
                session_poisoned_ = true;
                return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("NLF internal invariant violation on delete prepare: ") + ex.what());
            } catch (const std::bad_alloc&) {
                return abort_oom_noalloc();
            } catch (const std::exception& ex) {
                return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, std::string("NLF prepare delete failure: ") + ex.what());
            }
            if (fault_point_ == FaultPoint::AFTER_NLF_PREPARE) {
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after NLF prepare");
            }
        }

        if (fault_point_ == FaultPoint::ALLOCATION_FAILURE_MUTATION) {
            try {
                throw std::bad_alloc();
            } catch (const std::bad_alloc&) {
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Simulated allocation failure std::bad_alloc during delete mutation");
            }
        }

        bool applied = false;
        try {
            if (profiling_enabled_) {
                auto t0 = std::chrono::steady_clock::now();
                applied = graph_.apply_delete(dense_u, dense_v, dense_label);
                auto t1 = std::chrono::steady_clock::now();
                update_time_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
            } else {
                applied = graph_.apply_delete(dense_u, dense_v, dense_label);
            }
        } catch (const InvariantViolationException& ex) {
            session_poisoned_ = true;
            return abort_and_rollback(StepStatus::INVARIANT_VIOLATION, std::string("Delete mutation invariant violation: ") + ex.what());
        } catch (const std::bad_alloc&) {
            return abort_oom_noalloc();
        } catch (const std::exception& ex) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, std::string("Delete mutation exception: ") + ex.what());
        }

        if (!applied) {
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Failed to apply delete mutation after callback");
        }

        if (fault_point_ == FaultPoint::AFTER_GRAPH_BEFORE_NLF) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after graph mutation before NLF mutation");
        }

        if (nlf_index_ && signatures_) {
            if (fault_point_ == FaultPoint::AFTER_FIRST_NLF_CELL && prepared_delete.count > 1) {
                PreparedNlfUpdate partial;
                partial.count = 1;
                partial.valid = true;
                partial.mutations[0] = prepared_delete.mutations[0];
                nlf_index_->apply_prepared_update(partial, counters_);
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected after first NLF cell write");
            }
            nlf_index_->apply_prepared_update(prepared_delete, counters_);
        }
    }

    // Fault Injection check: BEFORE_PUBLISH
    if (fault_point_ == FaultPoint::BEFORE_PUBLISH) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected before publish");
    }

    // 6. Commit and publish. Only non-throwing operations follow the graph commit.
    tx_scope.commit();
    uint64_t sink_time_ns = 0;
    std::vector<std::vector<int64_t>> published_tuples;
    if (profiling_enabled_) {
        auto t0 = std::chrono::steady_clock::now();
        published_tuples = sink_.commit_step();
        auto t1 = std::chrono::steady_clock::now();
        sink_time_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
    } else {
        published_tuples = sink_.commit_step();
    }
    return {StepStatus::SUCCESS, {}, std::move(published_tuples), update_time_ns, sink_time_ns, true};
}

} // namespace rapidflow_apx
