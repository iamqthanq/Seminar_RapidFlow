#include "harness_runner.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/m2_event_engine.h"
#include "rapidflow_apx/nlf_index.h"
#include "rapidflow_apx/nlf_signatures.h"
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/streaming_engine.h"
#include "rapidflow_apx/transaction_manager.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/typed_codec.h"

#include <atomic>
#include <cstdlib>
#include <iostream>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::atomic<uint64_t> g_oom_countdown{0};

bool should_fail_alloc() noexcept {
    uint64_t cur = g_oom_countdown.load(std::memory_order_relaxed);
    while (cur > 0) {
        if (g_oom_countdown.compare_exchange_weak(cur, cur - 1, std::memory_order_acq_rel)) {
            return cur == 1;
        }
    }
    return false;
}

struct ScopedOomAfter {
    explicit ScopedOomAfter(uint64_t nth_alloc) noexcept {
        g_oom_countdown.store(nth_alloc, std::memory_order_release);
    }
    ~ScopedOomAfter() noexcept {
        g_oom_countdown.store(0, std::memory_order_release);
    }
};

} // namespace

void* operator new(std::size_t size) {
    if (should_fail_alloc()) {
        throw std::bad_alloc();
    }
    void* ptr = std::malloc(size ? size : 1);
    if (!ptr) {
        throw std::bad_alloc();
    }
    return ptr;
}

void* operator new[](std::size_t size) {
    return ::operator new(size);
}

void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
    if (should_fail_alloc()) {
        return nullptr;
    }
    return std::malloc(size ? size : 1);
}

void* operator new[](std::size_t size, const std::nothrow_t& nt) noexcept {
    return ::operator new(size, nt);
}

void operator delete(void* ptr) noexcept {
    std::free(ptr);
}

void operator delete[](void* ptr) noexcept {
    std::free(ptr);
}

void operator delete(void* ptr, std::size_t) noexcept {
    std::free(ptr);
}

void operator delete[](void* ptr, std::size_t) noexcept {
    std::free(ptr);
}

namespace rapidflow_apx {
namespace {

struct StepOomObserverCtx {
    uint64_t target_step{1};
    uint64_t nth_alloc{1};
};

void step_oom_observer(MeasurementStage stage, uint64_t step_idx, void* raw_ctx) {
    if (!raw_ctx) return;
    auto* ctx = static_cast<StepOomObserverCtx*>(raw_ctx);
    if (stage == MeasurementStage::STEP_EXECUTE_START && step_idx == ctx->target_step) {
        g_oom_countdown.store(ctx->nth_alloc, std::memory_order_release);
    } else if (stage == MeasurementStage::STEP_EXECUTE_END && step_idx == ctx->target_step) {
        g_oom_countdown.store(0, std::memory_order_release);
    }
}

void build_triangle_fixture(
    ApxAuthoritativeGraph& graph,
    ApxTypedCodec& codec,
    ApxQueryPlan& qplan
) {
    uint32_t dv_lbl0 = codec.intern_vertex_label(TypedLabel::from_int(0));
    std::vector<std::pair<uint32_t, uint32_t>> dverts;
    for (int64_t v = 0; v < 4; ++v) {
        uint32_t dv = codec.intern_data_vertex(v);
        dverts.push_back({dv, dv_lbl0});
    }
    for (int64_t q = 0; q < 3; ++q) {
        codec.intern_query_vertex(q);
    }
    TypedLabel lbl1 = TypedLabel::from_int(1);
    uint32_t dl1 = codec.intern_edge_label(lbl1);

    std::vector<uint32_t> qvlabels{dv_lbl0, dv_lbl0, dv_lbl0};
    std::vector<QueryEdge> qe{{0, 1, dl1}, {1, 2, dl1}, {0, 2, dl1}};
    std::vector<uint32_t> qorder = codec.get_query_canonical_order();
    qplan.build(3, qvlabels, qe, qorder, 0);

    uint32_t d0 = codec.get_data_vertex(0);
    uint32_t d1 = codec.get_data_vertex(1);
    uint32_t d2 = codec.get_data_vertex(2);
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> dedges{
        {d0, d1, dl1},
        {d1, d2, dl1},
    };
    graph.load_baseline(dverts, dedges);
}

int run_verify_paths() {
    TypedLabel lbl1 = TypedLabel::from_int(1);

    // B-W01: INSERT mutation path throws genuine std::bad_alloc inside ApxStreamingEngine::execute_step -> tx_manager_
    {
        const uint64_t edges_before = 2;
        bool saw_insert_oom = false;
        for (uint64_t k = 1; k <= 24; ++k) {
            ApxAuthoritativeGraph graph;
            ApxTypedCodec codec;
            ApxQueryPlan qplan;
            build_triangle_fixture(graph, codec, qplan);
            ApxTupleSink sink(codec, qplan.get_canonical_order());
            ApxStreamingEngine engine(graph, codec, sink, qplan, EngineType::M2, 500000, NlfMode::STATIC, 64ULL << 20, NeighborMode::LEGACY);
            engine.set_profiling_enabled(true);
            const uint64_t elbl_before = codec.edge_labels_count();
            const TypedLabel new_lbl = TypedLabel::from_int(999);
            try {
                ScopedOomAfter oom(k);
                StreamStepResult res = engine.execute_step(UpdateOperation{UpdateOp::INSERT, 0, 2, new_lbl}, 0);
                if (res.status == StepStatus::RESOURCE_EXHAUSTED) {
                    saw_insert_oom = true;
                    if (res.committed || res.error_message != "std::bad_alloc" || !res.entered_tuples.empty()) {
                        throw std::runtime_error("B-W01 resource/rollback payload mismatch");
                    }
                    if (graph.get_edges_count() != edges_before || graph.has_edge(codec.get_data_vertex(0), codec.get_data_vertex(2)) ||
                        codec.edge_labels_count() != elbl_before) {
                        throw std::runtime_error("B-W01 rollback invariant failed");
                    }
                    break;
                }
            } catch (const std::bad_alloc&) {
                // Pre-transaction callback closure copy OOM: graph & codec remain untouched.
                if (graph.get_edges_count() != edges_before || codec.edge_labels_count() != elbl_before) {
                    throw std::runtime_error("B-W01 state mutated before pre-tx throw");
                }
            }
        }
        if (!saw_insert_oom) {
            throw std::runtime_error("B-W01 did not reach StepStatus::RESOURCE_EXHAUSTED during INSERT mutation path");
        }
    }

    // B-W02: Search/callback path throws genuine std::bad_alloc inside M2EventEngine::search_event
    {
        const uint64_t edges_before = 2;
        StreamStepResult res;
        bool saw_resource = false;
        for (uint64_t k = 1; k <= 32; ++k) {
            ApxAuthoritativeGraph g2;
            ApxTypedCodec c2;
            ApxQueryPlan q2;
            build_triangle_fixture(g2, c2, q2);
            ApxTupleSink s2(c2, q2.get_canonical_order());
            ApxStreamingEngine e2(g2, c2, s2, q2, EngineType::M2, 500000, NlfMode::NONE, 64ULL << 20, NeighborMode::LEGACY);
            e2.set_profiling_enabled(true);
            try {
                ScopedOomAfter oom(k);
                res = e2.execute_step(UpdateOperation{UpdateOp::INSERT, 0, 2, lbl1}, 0);
            } catch (const std::bad_alloc&) {
                continue;
            }
            if (res.status == StepStatus::RESOURCE_EXHAUSTED && res.step_counters.work_units > 0) {
                saw_resource = true;
                if (res.committed || res.error_message != "std::bad_alloc" || !res.entered_tuples.empty()) {
                    throw std::runtime_error("B-W02 resource/rollback payload mismatch");
                }
                if (res.step_profiling.search_ns < res.step_profiling.search_core_ns ||
                    res.step_profiling.search_ns < res.step_profiling.neighbor_retrieval_ns) {
                    throw std::runtime_error("B-W02 SearchTimingGuard unwind hierarchy violated");
                }
                if (g2.get_edges_count() != edges_before || g2.has_edge(c2.get_data_vertex(0), c2.get_data_vertex(2))) {
                    throw std::runtime_error("B-W02 graph rollback failed");
                }
                break;
            }
        }
        if (!saw_resource) {
            throw std::runtime_error("B-W02 did not hit in-step search std::bad_alloc");
        }
    }

    // B-W03: DELETE path throws genuine std::bad_alloc in post-callback delete mutation / overlay tombstone
    {
        const uint64_t edges_before_del = 3;
        StreamStepResult del;
        bool saw_del_oom = false;
        for (uint64_t k = 1; k <= 40; ++k) {
            ApxAuthoritativeGraph g3;
            ApxTypedCodec c3;
            ApxQueryPlan q3;
            build_triangle_fixture(g3, c3, q3);
            ApxTupleSink s3(c3, q3.get_canonical_order());
            ApxStreamingEngine e3(g3, c3, s3, q3, EngineType::M2, 500000, NlfMode::RESIDUAL, 64ULL << 20, NeighborMode::LEGACY);
            e3.set_profiling_enabled(true);
            e3.execute_step(UpdateOperation{UpdateOp::INSERT, 0, 2, lbl1}, 0);
            try {
                ScopedOomAfter oom(k);
                del = e3.execute_step(UpdateOperation{UpdateOp::DELETE, 0, 2, lbl1}, 1);
            } catch (const std::bad_alloc&) {
                continue;
            }
            if (del.status == StepStatus::RESOURCE_EXHAUSTED && del.step_counters.work_units > 0) {
                saw_del_oom = true;
                if (del.committed || del.error_message != "std::bad_alloc" || !del.exited_tuples.empty()) {
                    throw std::runtime_error("B-W03 delete resource/rollback payload mismatch");
                }
                if (g3.get_edges_count() != edges_before_del || !g3.has_edge(c3.get_data_vertex(0), c3.get_data_vertex(2))) {
                    throw std::runtime_error("B-W03 original edge disappeared on aborted DELETE");
                }
                auto nb0 = g3.get_neighbors(c3.get_data_vertex(0));
                bool found = false;
                for (const auto& ne : nb0) {
                    if (ne == c3.get_data_vertex(2)) found = true;
                }
                if (!found) throw std::runtime_error("B-W03 neighbor visibility lost after DELETE rollback");
                break;
            }
        }
        if (!saw_del_oom) {
            throw std::runtime_error("B-W03 did not hit DELETE path std::bad_alloc after search");
        }
    }

    // B-W04: Tuple staging path (ApxTupleSink::emit_tuple inside real M2 callback) throws std::bad_alloc
    {
        const uint64_t edges_before = 2;
        bool saw_staging_oom = false;
        for (uint64_t k = 1; k <= 32; ++k) {
            ApxAuthoritativeGraph g4;
            ApxTypedCodec c4;
            ApxQueryPlan q4;
            build_triangle_fixture(g4, c4, q4);
            ApxTupleSink s4(c4, q4.get_canonical_order());
            ApxTransactionManager tx4(g4, c4, s4);
            ApxM2EventEngine m2;
            EngineCounters cnt;
            cnt.max_work = 500000;
            StepResult sr;
            try {
                ScopedOomAfter oom(k);
                sr = tx4.execute_update(
                    UpdateOperation{UpdateOp::INSERT, 0, 2, lbl1},
                    [&](const ApxAuthoritativeGraph& snap, const UpdateOperation&, uint32_t du, uint32_t dv, uint32_t dlbl) {
                        m2.search_event(q4, snap, du, dv, dlbl, s4, cnt, nullptr, NlfMode::NONE, nullptr, nullptr, NeighborMode::LEGACY);
                        return true;
                    }
                );
            } catch (const std::bad_alloc&) {
                continue;
            }
            if (sr.status == StepStatus::RESOURCE_EXHAUSTED && (s4.raw_emissions() > 0 || s4.discarded_emissions() > 0)) {
                saw_staging_oom = true;
                if (sr.committed || !sr.published_tuples.empty() || sr.error_message != "std::bad_alloc") {
                    throw std::runtime_error("B-W04 staging rollback invariant failed");
                }
                if (g4.get_edges_count() != edges_before || g4.has_edge(c4.get_data_vertex(0), c4.get_data_vertex(2))) {
                    throw std::runtime_error("B-W04 graph rollback failed");
                }
                if (!s4.commit_step().empty()) {
                    throw std::runtime_error("B-W04 pending tuples leaked after rollback");
                }
                break;
            }
        }
        if (!saw_staging_oom) {
            throw std::runtime_error("B-W04 did not trigger std::bad_alloc during tuple staging");
        }
    }

    std::cout << "{\"witness_paths\":[\"B-W01\",\"B-W02\",\"B-W03\",\"B-W04\"],\"status\":\"PASS\"}\n";
    return 0;
}

} // namespace
} // namespace rapidflow_apx

int main(int argc, char* argv[]) {
    if (argc == 2 && std::string(argv[1]) == "--verify-paths") {
        try {
            return rapidflow_apx::run_verify_paths();
        } catch (const std::exception& e) {
            std::cerr << "R21 witness failure: " << e.what() << "\n";
            return 1;
        }
    }

    rapidflow_apx::HarnessOptions opts;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--engine" && i + 1 < argc) opts.engine_str = argv[++i];
        else if (arg == "--nlf-mode" && i + 1 < argc) {
            std::string m = argv[++i];
            if (m == "none") opts.nlf_mode = rapidflow_apx::NlfMode::NONE;
            else if (m == "static") opts.nlf_mode = rapidflow_apx::NlfMode::STATIC;
            else if (m == "residual") opts.nlf_mode = rapidflow_apx::NlfMode::RESIDUAL;
            else return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
        } else if (arg == "--neighbor-mode" && i + 1 < argc) {
            std::string m = argv[++i];
            if (m == "legacy") opts.neighbor_mode = rapidflow_apx::NeighborMode::LEGACY;
            else if (m == "buffer") opts.neighbor_mode = rapidflow_apx::NeighborMode::BUFFER;
            else if (m == "merge") opts.neighbor_mode = rapidflow_apx::NeighborMode::MERGE;
            else return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
        } else if (arg == "--max-nlf-memory-mib" && i + 1 < argc) opts.max_nlf_memory_mib = std::stoull(argv[++i]);
        else if (arg == "--max-work" && i + 1 < argc) { opts.cli_max_work = std::stoull(argv[++i]); opts.cli_max_work_set = true; }
        else if (arg == "--execution-purpose" && i + 1 < argc) {
            if (!rapidflow_apx::parse_execution_purpose(argv[++i], opts.execution_purpose)) {
                return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
            }
            opts.explicit_execution_purpose = true;
            opts.profiling_enabled = (opts.execution_purpose == rapidflow_apx::ExecutionPurpose::DIAGNOSTIC);
        } else if (arg == "--wall-timeout-seconds" && i + 1 < argc) { opts.wall_timeout_seconds = std::stoull(argv[++i]); opts.apply_process_limits = false; }
        else if (arg == "--workload" && i + 1 < argc) opts.workload_path = argv[++i];
        else if (arg == "--output" && i + 1 < argc) opts.output_path = argv[++i];
        else if (arg == "--continue-on-error") opts.continue_on_error = true;
        else return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
    }

    if (opts.workload_path.empty() || opts.output_path.empty()) {
        std::cerr << "Missing --workload or --output\n";
        return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
    }

    // Arm deterministic genuine operator new -> std::bad_alloc inside step 1 M2 search_event
    rapidflow_apx::StepOomObserverCtx ctx{1, 10};
    return rapidflow_apx::run_workload_with_options(opts, rapidflow_apx::step_oom_observer, &ctx);
}
