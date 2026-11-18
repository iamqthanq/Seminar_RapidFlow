#include "rapidflow_apx/transaction_manager.h"
#include <new>

namespace rapidflow_apx {

namespace {
struct ReentrancyGuard {
    bool& flag;
    explicit ReentrancyGuard(bool& f) : flag(f) { flag = true; }
    ~ReentrancyGuard() { flag = false; }
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
    // 0. Reentrancy Guard
    if (is_in_transaction_) {
        return {StepStatus::REENTRANCY_BLOCKED, "Reentrancy detected: cannot execute transaction within an active transaction", {}};
    }
    ReentrancyGuard guard(is_in_transaction_);

    // 1. Checkpoint state before any mutation or codec change
    auto codec_cp = codec_.create_checkpoint();
    auto graph_cp = graph_.create_checkpoint();
    graph_.begin_transaction();
    sink_.start_step();

    auto abort_and_rollback = [&](StepStatus status, const std::string& err) -> StepResult {
        graph_.rollback_transaction();
        graph_.restore_checkpoint(graph_cp);
        codec_.restore_checkpoint(codec_cp);
        sink_.abort_step();
        return {status, err, {}};
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

    // Fault Injection check: BEFORE_MUTATION
    if (fault_point_ == FaultPoint::BEFORE_MUTATION) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected before mutation");
    }

    // 3. Snapshot Sequencing:
    // INSERT sees G_after -> apply mutation before search callback
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
            applied = graph_.apply_insert(dense_u, dense_v, dense_label, sim_rev_fail);
        } catch (const std::exception& ex) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, std::string("Insert mutation exception: ") + ex.what());
        }

        if (!applied) {
            if (sim_rev_fail) {
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected between forward and reverse mutation (symmetric rollback verified)");
            }
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Failed to apply insert mutation");
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
    } catch (const DuplicateEmissionException& ex) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, std::string("Duplicate raw emission detected in callback: ") + ex.what());
    } catch (const NonInjectiveMappingException& ex) {
        return abort_and_rollback(StepStatus::CALLBACK_ABORTED, std::string("Non-injective mapping detected in callback: ") + ex.what());
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
        if (fault_point_ == FaultPoint::ALLOCATION_FAILURE_MUTATION) {
            try {
                throw std::bad_alloc();
            } catch (const std::bad_alloc&) {
                return abort_and_rollback(StepStatus::FAULT_INJECTED, "Simulated allocation failure std::bad_alloc during delete mutation");
            }
        }

        bool applied = false;
        try {
            applied = graph_.apply_delete(dense_u, dense_v, dense_label);
        } catch (const std::exception& ex) {
            return abort_and_rollback(StepStatus::FAULT_INJECTED, std::string("Delete mutation exception: ") + ex.what());
        }

        if (!applied) {
            return abort_and_rollback(StepStatus::PREVALIDATION_FAILED, "Failed to apply delete mutation after callback");
        }
    }

    // Fault Injection check: BEFORE_PUBLISH
    if (fault_point_ == FaultPoint::BEFORE_PUBLISH) {
        return abort_and_rollback(StepStatus::FAULT_INJECTED, "Fault injected before publish");
    }

    // 6. Commit and publish
    graph_.commit_transaction();
    auto published_tuples = sink_.commit_step();
    return {StepStatus::SUCCESS, "", std::move(published_tuples)};
}

} // namespace rapidflow_apx
