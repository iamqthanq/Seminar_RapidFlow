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
#include <fstream>
#include <iostream>
#include <new>
#include <openssl/evp.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

std::atomic<uint64_t> g_oom_countdown{0};
std::atomic<uint64_t> g_oom_triggered_count{0};
std::atomic<uint64_t> g_armed_alloc_attempts{0};

bool should_fail_alloc() noexcept {
    uint64_t cur = g_oom_countdown.load(std::memory_order_relaxed);
    while (cur > 0) {
        if (g_oom_countdown.compare_exchange_weak(cur, cur - 1, std::memory_order_acq_rel)) {
            g_armed_alloc_attempts.fetch_add(1, std::memory_order_relaxed);
            if (cur == 1) {
                g_oom_triggered_count.fetch_add(1, std::memory_order_relaxed);
                return true;
            }
            return false;
        }
    }
    return false;
}

struct ScopedOomAfter {
    explicit ScopedOomAfter(uint64_t nth_alloc) noexcept {
        g_oom_triggered_count.store(0, std::memory_order_relaxed);
        g_armed_alloc_attempts.store(0, std::memory_order_relaxed);
        g_oom_countdown.store(nth_alloc, std::memory_order_release);
    }
    ~ScopedOomAfter() noexcept {
        g_oom_countdown.store(0, std::memory_order_release);
    }
};

struct PostCallbackOomGuard {
    ~PostCallbackOomGuard() noexcept {
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
    uint64_t nth_alloc{10};
};

void step_oom_observer(MeasurementStage stage, uint64_t step_idx, void* raw_ctx) {
    if (!raw_ctx) return;
    auto* ctx = static_cast<StepOomObserverCtx*>(raw_ctx);
    if (stage == MeasurementStage::STEP_EXECUTE_START && step_idx == ctx->target_step) {
        g_oom_triggered_count.store(0, std::memory_order_relaxed);
        g_armed_alloc_attempts.store(0, std::memory_order_relaxed);
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

struct GraphSnapshotSummary {
    uint64_t edges_count{0};
    bool has_edge_02{false};
    bool has_edge_20{false};
    bool label_exists{false};
    uint32_t label_id{0};
    uint32_t deg_0{0};
    uint32_t deg_2{0};
    bool in_overlay_02{false};
    bool in_tombstones_02{false};
    uint64_t codec_edge_labels{0};
    std::vector<uint32_t> nbrs_0;
    std::vector<uint32_t> nbrs_2;
};

GraphSnapshotSummary capture_graph_snapshot(
    const ApxAuthoritativeGraph& graph,
    const ApxTypedCodec& codec
) {
    uint32_t d0 = codec.get_data_vertex(0);
    uint32_t d2 = codec.get_data_vertex(2);
    GraphSnapshotSummary snap;
    snap.edges_count = graph.get_edges_count();
    snap.has_edge_02 = graph.has_edge(d0, d2);
    snap.has_edge_20 = graph.has_edge(d2, d0);
    EdgeLookupResult lbl = graph.get_edge_label(d0, d2);
    snap.label_exists = lbl.exists;
    snap.label_id = lbl.label;
    snap.deg_0 = graph.get_degree(d0);
    snap.deg_2 = graph.get_degree(d2);
    snap.in_overlay_02 = graph.is_in_overlay(d0, d2);
    snap.in_tombstones_02 = graph.is_in_tombstones(d0, d2);
    snap.codec_edge_labels = codec.edge_labels_count();
    snap.nbrs_0 = graph.get_neighbors(d0);
    snap.nbrs_2 = graph.get_neighbors(d2);

    // Cross-check get_neighbors_into & get_neighbors_merged_into parity directly
    std::vector<uint32_t> into_0, merged_0, into_2, merged_2;
    graph.get_neighbors_into(d0, into_0);
    graph.get_neighbors_merged_into(d0, merged_0);
    graph.get_neighbors_into(d2, into_2);
    graph.get_neighbors_merged_into(d2, merged_2);
    if (into_0 != snap.nbrs_0 || merged_0 != snap.nbrs_0 ||
        into_2 != snap.nbrs_2 || merged_2 != snap.nbrs_2) {
        throw std::runtime_error("Graph neighbor accessor parity mismatch (get_neighbors / into / merged_into)");
    }
    return snap;
}

bool snapshots_equal(const GraphSnapshotSummary& a, const GraphSnapshotSummary& b) {
    return a.edges_count == b.edges_count &&
           a.has_edge_02 == b.has_edge_02 &&
           a.has_edge_20 == b.has_edge_20 &&
           a.label_exists == b.label_exists &&
           a.label_id == b.label_id &&
           a.deg_0 == b.deg_0 &&
           a.deg_2 == b.deg_2 &&
           a.in_overlay_02 == b.in_overlay_02 &&
           a.in_tombstones_02 == b.in_tombstones_02 &&
           a.codec_edge_labels == b.codec_edge_labels &&
           a.nbrs_0 == b.nbrs_0 &&
           a.nbrs_2 == b.nbrs_2;
}

std::string snapshot_to_json(const GraphSnapshotSummary& s) {
    std::ostringstream os;
    os << "{\"edges_count\":" << s.edges_count
       << ",\"has_edge_02\":" << (s.has_edge_02 ? "true" : "false")
       << ",\"has_edge_20\":" << (s.has_edge_20 ? "true" : "false")
       << ",\"edge_label_exists\":" << (s.label_exists ? "true" : "false")
       << ",\"edge_label_id\":" << s.label_id
       << ",\"degree_0\":" << s.deg_0
       << ",\"degree_2\":" << s.deg_2
       << ",\"is_in_overlay\":" << (s.in_overlay_02 ? "true" : "false")
       << ",\"is_in_tombstones\":" << (s.in_tombstones_02 ? "true" : "false")
       << ",\"codec_edge_labels\":" << s.codec_edge_labels
       << ",\"neighbor_apis_checked\":[\"has_edge\",\"get_edge_label\",\"get_degree\",\"get_edges_count\",\"get_neighbors\",\"get_neighbors_into\",\"get_neighbors_merged_into\"]"
       << "}";
    return os.str();
}

const char* step_status_str(StepStatus st) {
    switch (st) {
        case StepStatus::SUCCESS: return "SUCCESS";
        case StepStatus::RESOURCE_EXHAUSTED: return "RESOURCE_EXHAUSTED";
        case StepStatus::CALLBACK_ABORTED: return "CALLBACK_ABORTED";
        case StepStatus::INVARIANT_VIOLATION: return "INVARIANT_VIOLATION";
        case StepStatus::PREVALIDATION_FAILED: return "PREVALIDATION_FAILED";
        case StepStatus::FAULT_INJECTED: return "FAULT_INJECTED";
        default: return "UNKNOWN";
    }
}

std::string self_exe_sha256_hex() {
    std::ifstream f("/proc/self/exe", std::ios::binary);
    if (!f.is_open()) return "";
    EVP_MD_CTX* mdctx = EVP_MD_CTX_new();
    if (!mdctx) return "";
    unsigned char hash[EVP_MAX_MD_SIZE];
    unsigned int hash_len = 0;
    if (EVP_DigestInit_ex(mdctx, EVP_sha256(), nullptr) == 1) {
        char buf[65536];
        while (f.read(buf, sizeof(buf)) || f.gcount() > 0) {
            EVP_DigestUpdate(mdctx, buf, static_cast<size_t>(f.gcount()));
        }
        EVP_DigestFinal_ex(mdctx, hash, &hash_len);
    }
    EVP_MD_CTX_free(mdctx);
    static const char hex[] = "0123456789abcdef";
    std::string out;
    out.reserve(hash_len * 2);
    for (unsigned int i = 0; i < hash_len; ++i) {
        out.push_back(hex[(hash[i] >> 4) & 0xf]);
        out.push_back(hex[hash[i] & 0xf]);
    }
    return out;
}

std::string run_bw03b_post_callback_delete_witness() {
    TypedLabel lbl1 = TypedLabel::from_int(1);
    ApxAuthoritativeGraph g;
    ApxTypedCodec c;
    ApxQueryPlan q;
    build_triangle_fixture(g, c, q);
    ApxTupleSink sink(c, q.get_canonical_order());
    // Use NlfMode::NONE (nlf_index_ = nullptr) so transaction_manager skips NLF prepare
    // and proceeds directly from callback return -> ApxAuthoritativeGraph::apply_delete() -> tombstones_.insert(key).
    ApxTransactionManager tx(g, c, sink);
    ApxM2EventEngine m2;
    EngineCounters cnt;
    cnt.max_work = 500000;
    tx.set_counters(&cnt);

    // Step 0: insert & commit target overlay edge (0, 2) cleanly
    StepResult prep = tx.execute_update(
        UpdateOperation{UpdateOp::INSERT, 0, 2, lbl1},
        [&](const ApxAuthoritativeGraph& snap, const UpdateOperation&, uint32_t du, uint32_t dv, uint32_t dlbl) {
            m2.search_event(q, snap, du, dv, dlbl, sink, cnt, nullptr, NlfMode::NONE, nullptr, nullptr, NeighborMode::LEGACY);
            return true;
        }
    );
    if (prep.status != StepStatus::SUCCESS || !prep.committed) {
        throw std::runtime_error("B-W03b setup INSERT failed");
    }

    const GraphSnapshotSummary before = capture_graph_snapshot(g, c);
    const uint64_t sink_raw_before = sink.raw_emissions();
    const uint64_t sink_com_before = sink.committed_emissions();
    const uint64_t sink_disc_before = sink.discarded_emissions();

    bool callback_reached = false;
    bool callback_completed = false;
    bool edge_visible_in_callback = false;
    bool allocation_armed_after_search = false;
    uint64_t search_work_units = 0;
    StepResult del_res;
    uint64_t triggered = 0;

    {
        PostCallbackOomGuard guard;
        del_res = tx.execute_update(
            UpdateOperation{UpdateOp::DELETE, 0, 2, lbl1},
            [&](const ApxAuthoritativeGraph& snap, const UpdateOperation&, uint32_t du, uint32_t dv, uint32_t dlbl) {
                callback_reached = true;
                edge_visible_in_callback = snap.has_edge(du, dv) && snap.get_edge_label(du, dv).exists;
                cnt.reset_step();
                cnt.max_work = 500000;
                m2.search_event(q, snap, du, dv, dlbl, sink, cnt, nullptr, NlfMode::NONE, nullptr, nullptr, NeighborMode::LEGACY);
                search_work_units = cnt.work_units;
                callback_completed = true;
                g_oom_triggered_count.store(0, std::memory_order_relaxed);
                g_armed_alloc_attempts.store(0, std::memory_order_relaxed);
                allocation_armed_after_search = true;
                // Arm nth_alloc=1 right before returning true -> next heap alloc is tombstones_.insert(key) in apply_delete()
                g_oom_countdown.store(1, std::memory_order_release);
                return true;
            }
        );
        triggered = g_oom_triggered_count.load(std::memory_order_acquire);
    }

    const GraphSnapshotSummary after_fail = capture_graph_snapshot(g, c);
    const bool rollback_equal = snapshots_equal(before, after_fail);
    const uint64_t sink_raw_after = sink.raw_emissions();
    const uint64_t sink_com_after = sink.committed_emissions();
    const uint64_t sink_disc_after = sink.discarded_emissions();

    if (!callback_reached || !edge_visible_in_callback || !callback_completed || !allocation_armed_after_search || triggered == 0) {
        throw std::runtime_error("B-W03b did not complete callback before post-callback OOM trigger");
    }
    if (del_res.status != StepStatus::RESOURCE_EXHAUSTED || del_res.committed || del_res.error_message != "std::bad_alloc") {
        throw std::runtime_error("B-W03b did not produce RESOURCE_EXHAUSTED / std::bad_alloc");
    }
    if (!del_res.published_tuples.empty() || !rollback_equal || !sink.commit_step().empty()) {
        throw std::runtime_error("B-W03b rollback invariant failed");
    }

    // Positive control: repeat same DELETE with injection off -> succeeds and removes edge cleanly
    StepResult pos_res = tx.execute_update(
        UpdateOperation{UpdateOp::DELETE, 0, 2, lbl1},
        [&](const ApxAuthoritativeGraph& snap, const UpdateOperation&, uint32_t du, uint32_t dv, uint32_t dlbl) {
            m2.search_event(q, snap, du, dv, dlbl, sink, cnt, nullptr, NlfMode::NONE, nullptr, nullptr, NeighborMode::LEGACY);
            return true;
        }
    );
    const GraphSnapshotSummary after_pos = capture_graph_snapshot(g, c);
    if (pos_res.status != StepStatus::SUCCESS || !pos_res.committed || after_pos.has_edge_02 || after_pos.edges_count != before.edges_count - 1) {
        throw std::runtime_error("B-W03b positive control DELETE failed");
    }

    const std::string wit_sha = self_exe_sha256_hex();
    std::ostringstream os;
    os << "{\n"
       << "  \"witness_id\": \"B-W03b\",\n"
       << "  \"native_source_commit\": \"115ce2844d91f6a6588fe0ac50b1179bc3b38012\",\n"
       << "  \"witness_binary_sha256\": \"" << wit_sha << "\",\n"
       << "  \"production_binary_sha256\": \"3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876\",\n"
       << "  \"fixture_identity\": \"synthetic_triangle_4v_overlay_edge_0_2_nlf_none\",\n"
       << "  \"callback_reached\": " << (callback_reached ? "true" : "false") << ",\n"
       << "  \"edge_visible_in_callback\": " << (edge_visible_in_callback ? "true" : "false") << ",\n"
       << "  \"callback_completed\": " << (callback_completed ? "true" : "false") << ",\n"
       << "  \"search_work_units_in_callback\": " << search_work_units << ",\n"
       << "  \"allocation_armed_after_search\": " << (allocation_armed_after_search ? "true" : "false") << ",\n"
       << "  \"allocation_triggered\": " << triggered << ",\n"
       << "  \"identified_failure_phase\": \"post_callback_apply_delete_tombstones_insert_key\",\n"
       << "  \"graph_before\": " << snapshot_to_json(before) << ",\n"
       << "  \"graph_after_failure\": " << snapshot_to_json(after_fail) << ",\n"
       << "  \"rollback_equal\": " << (rollback_equal ? "true" : "false") << ",\n"
       << "  \"status\": \"" << step_status_str(del_res.status) << "\",\n"
       << "  \"committed\": " << (del_res.committed ? "true" : "false") << ",\n"
       << "  \"error_message\": \"" << del_res.error_message << "\",\n"
       << "  \"tuple_sink_before\": {\"raw_emissions\":" << sink_raw_before << ",\"committed_emissions\":" << sink_com_before << ",\"discarded_emissions\":" << sink_disc_before << "},\n"
       << "  \"tuple_sink_after\": {\"raw_emissions\":" << sink_raw_after << ",\"committed_emissions\":" << sink_com_after << ",\"discarded_emissions\":" << sink_disc_after << "},\n"
       << "  \"positive_control_status\": \"" << step_status_str(pos_res.status) << "\",\n"
       << "  \"positive_control_graph_after\": " << snapshot_to_json(after_pos) << "\n"
       << "}\n";
    return os.str();
}

int run_verify_paths(const std::string& write_delete_path) {
    TypedLabel lbl1 = TypedLabel::from_int(1);

    // B-W01: INSERT transaction encounters genuine std::bad_alloc during step execution (tx_manager_ scope)
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
                if (graph.get_edges_count() != edges_before || codec.edge_labels_count() != elbl_before) {
                    throw std::runtime_error("B-W01 state mutated before pre-tx throw");
                }
            }
        }
        if (!saw_insert_oom) {
            throw std::runtime_error("B-W01 did not reach StepStatus::RESOURCE_EXHAUSTED during INSERT step");
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

    // B-W03a: DELETE transaction allocation failure during search/NLF-enabled execution (preserved R2.1 witness)
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
                    throw std::runtime_error("B-W03a delete resource/rollback payload mismatch");
                }
                if (g3.get_edges_count() != edges_before_del || !g3.has_edge(c3.get_data_vertex(0), c3.get_data_vertex(2))) {
                    throw std::runtime_error("B-W03a original edge disappeared on aborted DELETE");
                }
                break;
            }
        }
        if (!saw_del_oom) {
            throw std::runtime_error("B-W03a did not hit DELETE search std::bad_alloc");
        }
    }

    // B-W03b: Post-callback DELETE graph mutation allocation failure (tombstones_.insert(key)) + positive control
    std::string bw03b_json = run_bw03b_post_callback_delete_witness();
    if (!write_delete_path.empty()) {
        std::ofstream out(write_delete_path, std::ios::out | std::ios::binary);
        if (!out.is_open()) {
            throw std::runtime_error("Cannot write B-W03b output file: " + write_delete_path);
        }
        out << bw03b_json;
    }

    // B-W04: Allocation failure after tuple emission has begun (sink rollback cleanly discards pending tuples)
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
                    throw std::runtime_error("B-W04 post-emission rollback invariant failed");
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
            throw std::runtime_error("B-W04 did not trigger std::bad_alloc after tuple emission began");
        }
    }

    std::cout << "{\"witness_paths\":[\"B-W01\",\"B-W02\",\"B-W03a\",\"B-W03b\",\"B-W04\"],\"bw03b_phase\":\"post_callback_apply_delete_tombstones_insert_key\",\"status\":\"PASS\"}\n";
    return 0;
}

} // namespace
} // namespace rapidflow_apx

int main(int argc, char* argv[]) {
    std::string witness_mode = "early-search";
    uint64_t inject_step = 1;
    uint64_t inject_after = 10;
    bool inject_after_set = false;
    bool verify_paths = false;
    std::string delete_out_path;

    bool witness_mode_explicit = false;
    rapidflow_apx::HarnessOptions opts;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--verify-paths") verify_paths = true;
        else if (arg == "--write-delete-witness" && i + 1 < argc) delete_out_path = argv[++i];
        else if (arg == "--witness-mode" && i + 1 < argc) { witness_mode = argv[++i]; witness_mode_explicit = true; }
        else if (arg == "--inject-oom-step" && i + 1 < argc) inject_step = std::stoull(argv[++i]);
        else if (arg == "--inject-oom-after" && i + 1 < argc) { inject_after = std::stoull(argv[++i]); inject_after_set = true; }
        else if (arg == "--engine" && i + 1 < argc) opts.engine_str = argv[++i];
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

    if (verify_paths) {
        try {
            return rapidflow_apx::run_verify_paths(delete_out_path);
        } catch (const std::exception& e) {
            std::cerr << "R21 witness failure: " << e.what() << "\n";
            return 1;
        }
    }

    if (witness_mode == "delete-post-callback") {
        try {
            std::string json = rapidflow_apx::run_bw03b_post_callback_delete_witness();
            if (!opts.output_path.empty()) {
                std::ofstream out(opts.output_path, std::ios::out | std::ios::binary);
                if (!out.is_open()) return static_cast<int>(rapidflow_apx::ExitCode::IO_ERROR);
                out << json;
            } else {
                std::cout << json;
            }
            return 0;
        } catch (const std::exception& e) {
            std::cerr << "R21 delete-post-callback failure: " << e.what() << "\n";
            return 1;
        }
    }

    if (!witness_mode_explicit && !inject_after_set) {
        std::string prog = argc > 0 && argv[0] ? argv[0] : "";
        if (opts.workload_path.find("P4B_CONT_07.json") != std::string::npos) {
            witness_mode = "late-search";
            inject_after = 500;
            inject_after_set = true;
        } else if (prog.find("late") != std::string::npos) {
            witness_mode = "late-search";
            inject_after = 60;
            inject_after_set = true;
        }
    }

    if (witness_mode == "late-search") {
        if (!inject_after_set) inject_after = 500;
    } else if (witness_mode != "early-search") {
        std::cerr << "Unsupported --witness-mode: " << witness_mode << "\n";
        return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
    }

    if (opts.workload_path.empty() || opts.output_path.empty()) {
        std::cerr << "Missing --workload or --output\n";
        return static_cast<int>(rapidflow_apx::ExitCode::CLI_ARG_ERROR);
    }

    rapidflow_apx::StepOomObserverCtx ctx{inject_step, inject_after};
    return rapidflow_apx::run_workload_with_options(opts, rapidflow_apx::step_oom_observer, &ctx);
}
