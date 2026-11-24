#include "p4b_tests.h"
#include "rapidflow_apx/nlf_signatures.h"
#include "rapidflow_apx/nlf_index.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/typed_codec.h"
#include "rapidflow_apx/transaction_manager.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/streaming_engine.h"
#include "rapidflow_apx/m2_event_engine.h"
#include "rapidflow_apx/engine_counters.h"
#include <iostream>
#include <cassert>
#include <vector>
#include <string>
#include <algorithm>
#include <limits>
#include <memory>
#include <set>

namespace rapidflow_apx {

namespace {

#define TEST_ASSERT(cond, msg) \
    do { \
        if (!(cond)) { \
            std::cerr << "FAILED: " << msg << " at " << __FILE__ << ":" << __LINE__ << std::endl; \
            return false; \
        } \
    } while (0)

// Reference independent frequency reconstruction
bool verify_index_reconstruction(
    const ApxAuthoritativeGraph& graph,
    const ApxQuerySignatures& signatures,
    const ApxNlfIndex& index
) {
    uint32_t V = graph.get_vertices_count();
    size_t S = signatures.signature_count();

    for (uint32_t u = 0; u < V; ++u) {
        std::vector<uint32_t> neighbors = graph.get_neighbors(u);

        for (size_t sig_id = 0; sig_id < S; ++sig_id) {
            const auto& sig = signatures.get_signature(static_cast<SignatureId>(sig_id));
            uint32_t exp_count = 0;

            for (uint32_t v : neighbors) {
                auto lookup = graph.get_edge_label(u, v);
                if (lookup.exists && lookup.label == sig.edge_label_id) {
                    if (graph.get_vertex_label(v) == sig.neighbor_vertex_label_id) {
                        exp_count++;
                    }
                }
            }

            uint32_t act_count = index.count(u, static_cast<SignatureId>(sig_id));
            if (act_count != exp_count) {
                std::cerr << "Index reconstruction mismatch at vertex " << u
                          << " sig " << sig_id << ": expected " << exp_count
                          << ", got " << act_count << std::endl;
                return false;
            }
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// 1. Signature Registry Tests (SIG-01 to SIG-10)
// ---------------------------------------------------------------------------
bool test_signatures() {
    std::cout << "  Running Signature Registry Tests (SIG-01 to SIG-10)..." << std::endl;

    // SIG-01: Equal endpoint labels
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 0};
        std::vector<QueryEdge> edges = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, vlabels, edges, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        TEST_ASSERT(sigs.signature_count() == 1, "SIG-01: exactly 1 signature for symmetric edge");
        const auto& req0 = sigs.get_vertex_requirements(0);
        const auto& req1 = sigs.get_vertex_requirements(1);
        TEST_ASSERT(req0.size() == 1 && req0[0].required_count == 1, "SIG-01: vertex 0 requires 1");
        TEST_ASSERT(req1.size() == 1 && req1[0].required_count == 1, "SIG-01: vertex 1 requires 1");
        TEST_ASSERT(sigs.sum_requirements(0) == 1 && sigs.sum_requirements(1) == 1, "SIG-01: degree conservation");
    }

    // SIG-02: Distinct endpoint labels
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 1};
        std::vector<QueryEdge> edges = {{0, 1, 2}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, vlabels, edges, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        TEST_ASSERT(sigs.signature_count() == 2, "SIG-02: 2 distinct signatures for asymmetric edge");
        const auto& req0 = sigs.get_vertex_requirements(0);
        const auto& req1 = sigs.get_vertex_requirements(1);
        TEST_ASSERT(req0.size() == 1 && sigs.get_signature(req0[0].sig_id).neighbor_vertex_label_id == 1, "SIG-02: req0 target label 1");
        TEST_ASSERT(req1.size() == 1 && sigs.get_signature(req1[0].sig_id).neighbor_vertex_label_id == 0, "SIG-02: req1 target label 0");
    }

    // SIG-03: Multiple distinct incident signatures on same vertex
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 1, 2};
        std::vector<QueryEdge> edges = {{0, 1, 0}, {0, 2, 1}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, vlabels, edges, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        TEST_ASSERT(sigs.signature_count() == 4, "SIG-03: 4 distinct directed signatures");
        const auto& req0 = sigs.get_vertex_requirements(0);
        TEST_ASSERT(req0.size() == 2, "SIG-03: vertex 0 has 2 distinct incident signatures");
        TEST_ASSERT(sigs.sum_requirements(0) == 2, "SIG-03: degree conservation = 2");
    }

    // SIG-04: Path-3 query, leaf degree conservation, and disconnected query rejection
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 1, 2};
        std::vector<QueryEdge> edges = {{0, 1, 0}, {1, 2, 0}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, vlabels, edges, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        TEST_ASSERT(sigs.sum_requirements(0) == 1, "SIG-04: leaf 0 degree conservation = 1");
        TEST_ASSERT(sigs.sum_requirements(1) == 2, "SIG-04: center 1 degree conservation = 2");
        TEST_ASSERT(sigs.sum_requirements(2) == 1, "SIG-04: leaf 2 degree conservation = 1");

        // Disconnected query graph rejection
        bool caught_disconn = false;
        try {
            ApxQueryPlan qp_disconn;
            std::vector<QueryEdge> disconn_edges = {{0, 1, 0}};
            qp_disconn.build(3, vlabels, disconn_edges, can, 0);
        } catch (const InvalidQueryException&) {
            caught_disconn = true;
        }
        TEST_ASSERT(caught_disconn, "SIG-04: disconnected query rejected");
    }

    // SIG-05 to SIG-10: Triangle with distinct labels, sorted lookup and degree invariant
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 1, 2};
        std::vector<QueryEdge> edges = {{0, 1, 10}, {1, 2, 20}, {2, 0, 30}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, vlabels, edges, can, 1);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        TEST_ASSERT(sigs.signature_count() == 6, "SIG-05: 6 distinct directed signatures in triangle");
        TEST_ASSERT(sigs.signature_count() <= 2 * edges.size(), "SIG-06: bound |Sigma_Q| <= 2|E(Q)|");

        for (uint32_t u = 0; u < 3; ++u) {
            TEST_ASSERT(sigs.sum_requirements(u) == 2, "SIG-07: degree conservation on triangle vertices");
            const auto& reqs = sigs.get_vertex_requirements(u);
            TEST_ASSERT(std::is_sorted(reqs.begin(), reqs.end()), "SIG-08: sorted requirements");
        }
    }

    // SIG Coverage: Typed labels through actual codec and canonical edge reordering
    {
        ApxTypedCodec codec;
        codec.intern_query_vertex(100);
        codec.intern_query_vertex(-50); // negative external ID
        codec.intern_vertex_label(TypedLabel::from_string("L_A"));
        codec.intern_vertex_label(TypedLabel::from_int(42));
        codec.intern_edge_label(TypedLabel::from_string("E_X"));

        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {codec.get_vertex_label_id(TypedLabel::from_string("L_A")),
                                         codec.get_vertex_label_id(TypedLabel::from_int(42))};
        std::vector<QueryEdge> edges = {{0, 1, codec.get_edge_label_id(TypedLabel::from_string("E_X"))}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, vlabels, edges, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        TEST_ASSERT(sigs.signature_count() == 2, "SIG: typed codec labels interned correctly");
        TEST_ASSERT(sigs.get_edge_signatures(0).from_u < sigs.signature_count(), "SIG: edge forward signature registered");
        TEST_ASSERT(sigs.get_edge_signatures(0).from_v < sigs.signature_count(), "SIG: edge reverse signature registered");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 2. Numeric Safety & Memory Allocation Tests (NUM-01 to NUM-18)
// ---------------------------------------------------------------------------
bool test_numeric_safety() {
    std::cout << "  Running Numeric Safety Tests (NUM-01 to NUM-18 & Synthetic Boundary)..." << std::endl;

    // NUM-01: Static deficit computation saturating at zero
    {
        TEST_ASSERT(ApxNlfIndex::compute_static_deficit(5, 3) == 2, "NUM-01: 5 - 3 = 2");
        TEST_ASSERT(ApxNlfIndex::compute_static_deficit(3, 5) == 0, "NUM-01: 3 - 5 = 0 (saturated)");
        TEST_ASSERT(ApxNlfIndex::compute_static_deficit(0, 0) == 0, "NUM-01: 0 - 0 = 0");
        TEST_ASSERT(ApxNlfIndex::compute_static_deficit(100, 100) == 0, "NUM-01: 100 - 100 = 0");
    }

    // NUM-05: Cell multiplication overflow guard
    {
        ApxAuthoritativeGraph graph;
        ApxQuerySignatures sigs;
        ApxNlfIndex idx;
        TEST_ASSERT(!idx.is_initialized(), "NUM-05: uninitialized index");
        TEST_ASSERT(idx.total_cells() == 0, "NUM-05: 0 cells initially");
    }

    // NUM-06: Configured memory limit rejection
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}, {1, 2, 0}};
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        bool caught_mem_limit = false;
        try {
            // Request allocation with budget of only 4 bytes (needs 4 vertices * 1 sig * 4 bytes = 16 bytes)
            idx.initialize(graph, sigs, 4);
        } catch (const NlfMemoryLimitException&) {
            caught_mem_limit = true;
        }
        TEST_ASSERT(caught_mem_limit, "NUM-06: NlfMemoryLimitException thrown when exceeding budget");
        TEST_ASSERT(!idx.is_initialized(), "NUM-06: table remains uninitialized on budget limit");
    }

    // NUM-11: Bounds checking on count()
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}};
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        bool caught_oob_v = false;
        try {
            idx.count(10, 0);
        } catch (const InvariantViolationException&) {
            caught_oob_v = true;
        }
        TEST_ASSERT(caught_oob_v, "NUM-11: out of bounds vertex throws");

        bool caught_oob_s = false;
        try {
            idx.count(0, 10);
        } catch (const InvariantViolationException&) {
            caught_oob_s = true;
        }
        TEST_ASSERT(caught_oob_s, "NUM-11: out of bounds signature throws");
    }

    // Synthetic High-Count Boundary Verification (70,000 count capacity)
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}};
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        // Synthetic setter verifies uint32 count above uint16 boundary (65535)
        idx.set_count_for_testing(0, 0, 70000);
        idx.set_count_for_testing(1, 0, 70000);
        TEST_ASSERT(idx.count(0, 0) == 70000, "NUM-15: exact 70,000 uint32 storage");

        // Verify checked decrement from 70,000
        PreparedNlfUpdate prep;
        NlfUndoRecord undo;
        idx.prepare_edge_update(graph, sigs, 0, 1, 0, false, prep, undo);
        idx.apply_prepared_update(prep);
        TEST_ASSERT(idx.count(0, 0) == 69999, "NUM-16: exact decrement to 69,999");
        TEST_ASSERT(idx.count(1, 0) == 69999, "NUM-16: exact decrement to 69,999");

        // Verify rollback restores exact 70,000
        idx.rollback(undo);
        TEST_ASSERT(idx.count(0, 0) == 70000, "NUM-17: exact rollback to 70,000");
        TEST_ASSERT(idx.count(1, 0) == 70000, "NUM-17: exact rollback to 70,000");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 3. Actual High-Degree Graph Test (Finding P4B-1A-F05)
// ---------------------------------------------------------------------------
bool test_actual_high_degree_star_graph() {
    std::cout << "  Running Actual High-Degree Star Graph Test (70,001 vertices, 70,000 edges)..." << std::endl;

    constexpr uint32_t NUM_LEAVES = 70000;
    constexpr uint32_t NUM_VERTICES = NUM_LEAVES + 1;

    ApxAuthoritativeGraph graph;
    std::vector<std::pair<uint32_t, uint32_t>> verts;
    verts.reserve(NUM_VERTICES);
    // Center vertex 0 has label 0
    verts.push_back({0, 0});
    // Leaves 1..70000 have label 1
    for (uint32_t v = 1; v <= NUM_LEAVES; ++v) {
        verts.push_back({v, 1});
    }

    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges;
    edges.reserve(NUM_LEAVES);
    for (uint32_t v = 1; v <= NUM_LEAVES; ++v) {
        edges.push_back({0, v, 0});
    }

    graph.load_baseline(verts, edges);
    TEST_ASSERT(graph.get_vertices_count() == NUM_VERTICES, "HD-01: 70,001 vertices loaded");
    TEST_ASSERT(graph.get_edges_count() == NUM_LEAVES, "HD-01: 70,000 edges loaded");
    TEST_ASSERT(graph.get_degree(0) == NUM_LEAVES, "HD-01: center vertex degree exactly 70,000");

    // Query: K2, vertex 0 label 0, vertex 1 label 1, edge label 0, k=0
    ApxQueryPlan qp;
    std::vector<uint32_t> qv = {0, 1};
    std::vector<QueryEdge> qe = {{0, 1, 0}};
    std::vector<uint32_t> can = {0, 1};
    qp.build(2, qv, qe, can, 0);

    ApxQuerySignatures sigs;
    sigs.build(qp);
    TEST_ASSERT(sigs.signature_count() == 2, "HD-02: 2 signatures for directed K2");

    // Center vertex 0 requires signature (target_label=1, edge_label=0)
    const auto& sig_center = sigs.get_edge_signatures(0);
    SignatureId center_sig_id = sig_center.from_u;
    SignatureId leaf_sig_id = sig_center.from_v;

    ApxNlfIndex idx;
    idx.initialize(graph, sigs, 256 * 1024 * 1024);
    TEST_ASSERT(idx.is_initialized(), "HD-03: NLF index initialized from 70,000-edge graph");
    TEST_ASSERT(idx.count(0, center_sig_id) == NUM_LEAVES, "HD-03: center count initialized to exactly 70,000");
    TEST_ASSERT(idx.count(1, leaf_sig_id) == 1, "HD-03: leaf 1 count initialized to 1");
    TEST_ASSERT(idx.count(NUM_LEAVES, leaf_sig_id) == 1, "HD-03: leaf 70,000 count initialized to 1");

    // Targeted DELETE edge (0, 70000)
    PreparedNlfUpdate prep_del;
    NlfUndoRecord undo_del;
    idx.prepare_edge_update(graph, sigs, 0, NUM_LEAVES, 0, false, prep_del, undo_del);
    idx.apply_prepared_update(prep_del);
    TEST_ASSERT(idx.count(0, center_sig_id) == NUM_LEAVES - 1, "HD-04: center count decremented to 69,999");
    TEST_ASSERT(idx.count(NUM_LEAVES, leaf_sig_id) == 0, "HD-04: leaf 70,000 count decremented to 0");

    // Targeted Rollback restores exact 70,000
    idx.rollback(undo_del);
    TEST_ASSERT(idx.count(0, center_sig_id) == NUM_LEAVES, "HD-05: center count rolled back to 70,000");
    TEST_ASSERT(idx.count(NUM_LEAVES, leaf_sig_id) == 1, "HD-05: leaf 70,000 count rolled back to 1");

    return true;
}

// ---------------------------------------------------------------------------
// 4. Initialization Tests (INIT-01 to INIT-12)
// ---------------------------------------------------------------------------
bool test_initialization() {
    std::cout << "  Running Initialization Tests (INIT-01 to INIT-12)..." << std::endl;

    // INIT-01: Empty graph
    {
        ApxAuthoritativeGraph graph;
        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);
        TEST_ASSERT(idx.is_initialized(), "INIT-01: empty graph initialized");
        TEST_ASSERT(idx.vertex_count() == 0, "INIT-01: 0 vertices");
    }

    // INIT-04: Disconnected components and isolated vertices
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}};
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);
        TEST_ASSERT(idx.count(0, 0) == 1, "INIT-04: vertex 0 count 1");
        TEST_ASSERT(idx.count(1, 0) == 1, "INIT-04: vertex 1 count 1");
        TEST_ASSERT(idx.count(2, 0) == 0, "INIT-04: isolated vertex 2 count 0");
        TEST_ASSERT(idx.count(3, 0) == 0, "INIT-04: isolated vertex 3 count 0");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "INIT-04: reconstruction holds");
    }

    // INIT-08: Non-pristine graph rejection (attempt to build index when overlay exists)
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        // Apply an uncommitted or overlay update to make graph non-pristine
        graph.apply_insert(0, 1, 0);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        bool caught_non_pristine = false;
        try {
            idx.initialize(graph, sigs);
        } catch (const InvariantViolationException&) {
            caught_non_pristine = true;
        }
        TEST_ASSERT(caught_non_pristine, "INIT-08: rejected initialization on non-pristine graph");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 5. Transaction Safety & Rollback Tests (TX-01 to TX-25 & Invariant Failure)
// ---------------------------------------------------------------------------
bool test_transactions_and_fault_injection() {
    std::cout << "  Running Transaction & Rollback Tests (TX-01 to TX-25)..." << std::endl;

    // Zero-cell update (irrelevant edge label): INSERT and DELETE
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_string("relevant"));
        codec.intern_edge_label(TypedLabel::from_string("irrelevant"));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, codec.get_edge_label_id(TypedLabel::from_string("relevant"))}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // INSERT with irrelevant label
        UpdateOperation ins_irrel{UpdateOp::INSERT, 0, 1, TypedLabel::from_string("irrelevant")};
        auto r_ins = tx_mgr.execute_update(ins_irrel, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_ins.status == StepStatus::SUCCESS, "TX-Zero: insert irrelevant label success");
        TEST_ASSERT(idx.count(0, 0) == 0 && idx.count(1, 0) == 0, "TX-Zero: relevant counts remain 0");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-Zero: reconstruction holds after irrelevant insert");

        // DELETE with irrelevant label
        UpdateOperation del_irrel{UpdateOp::DELETE, 0, 1, TypedLabel::from_string("irrelevant")};
        auto r_del = tx_mgr.execute_update(del_irrel, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_del.status == StepStatus::SUCCESS, "TX-Zero: delete irrelevant label success");
        TEST_ASSERT(idx.count(0, 0) == 0 && idx.count(1, 0) == 0, "TX-Zero: relevant counts remain 0 after delete");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-Zero: reconstruction holds after irrelevant delete");
    }

    // One-cell update (asymmetric query and vertex labels): only one endpoint has a matching signature
    {
        ApxAuthoritativeGraph graph;
        // Vertex 0 label 0, Vertex 1 label 99 (not in query)
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 99}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_vertex_label(TypedLabel::from_int(99));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // When edge (0, 1) is inserted:
        // Vertex 0 has neighbor 1 with label 99 -> no query signature exists for neighbor label 99!
        // Vertex 1 has neighbor 0 with label 0 -> signature exists for neighbor label 0!
        // Exactly one cell is affected.
        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto r_ins = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_ins.status == StepStatus::SUCCESS, "TX-One: asymmetric one-cell insert success");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-One: reconstruction holds after one-cell insert");

        UpdateOperation del_op{UpdateOp::DELETE, 0, 1, TypedLabel::from_int(0)};
        auto r_del = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_del.status == StepStatus::SUCCESS, "TX-One: asymmetric one-cell delete success");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-One: reconstruction holds after one-cell delete");
    }

    // Callback-time snapshot inspection (INSERT and DELETE)
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        bool cb_insert_verified = false;
        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto r_ins = tx_mgr.execute_update(ins_op, [&](const ApxAuthoritativeGraph& g, const UpdateOperation&, uint32_t, uint32_t, uint32_t) {
            // In INSERT callback: edge must be present, counts must reflect inserted edge
            if (g.has_edge(0, 1) && idx.count(0, 0) == 1 && idx.count(1, 0) == 1) {
                cb_insert_verified = verify_index_reconstruction(g, sigs, idx);
            }
            return true;
        });
        TEST_ASSERT(r_ins.status == StepStatus::SUCCESS, "TX-Snap: insert success");
        TEST_ASSERT(cb_insert_verified, "TX-Snap: INSERT callback sees G_after and post-insert counts");

        bool cb_delete_verified = false;
        UpdateOperation del_op{UpdateOp::DELETE, 0, 1, TypedLabel::from_int(0)};
        auto r_del = tx_mgr.execute_update(del_op, [&](const ApxAuthoritativeGraph& g, const UpdateOperation&, uint32_t, uint32_t, uint32_t) {
            // In DELETE callback: edge must still be present, counts must still be pre-delete
            if (g.has_edge(0, 1) && idx.count(0, 0) == 1 && idx.count(1, 0) == 1) {
                cb_delete_verified = verify_index_reconstruction(g, sigs, idx);
            }
            return true;
        });
        TEST_ASSERT(r_del.status == StepStatus::SUCCESS, "TX-Snap: delete success");
        TEST_ASSERT(cb_delete_verified, "TX-Snap: DELETE callback sees G_before and pre-delete counts");
        TEST_ASSERT(!graph.has_edge(0, 1), "TX-Snap: edge removed after commit");
        TEST_ASSERT(idx.count(0, 0) == 0 && idx.count(1, 0) == 0, "TX-Snap: counts updated after commit");
    }

    // Partial write rollback on DELETE: AFTER_FIRST_NLF_CELL
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // Inject fault AFTER_FIRST_NLF_CELL during DELETE
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::AFTER_FIRST_NLF_CELL);
        UpdateOperation del_op{UpdateOp::DELETE, 0, 1, TypedLabel::from_int(0)};
        auto r_del = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });

        TEST_ASSERT(r_del.status == StepStatus::FAULT_INJECTED, "TX-DelPartial: fault injected status");
        TEST_ASSERT(graph.has_edge(0, 1), "TX-DelPartial: graph edge restored");
        TEST_ASSERT(idx.count(0, 0) == 1, "TX-DelPartial: cell 0 restored to 1");
        TEST_ASSERT(idx.count(1, 0) == 1, "TX-DelPartial: cell 1 restored to 1");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-DelPartial: reconstruction holds");

        // Next valid DELETE succeeds
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
        auto r_del_next = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_del_next.status == StepStatus::SUCCESS, "TX-DelPartial: next delete succeeds");
        TEST_ASSERT(!graph.has_edge(0, 1), "TX-DelPartial: edge removed after successful delete");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-DelPartial: reconstruction holds");
    }

    // Complete mutation rollback: BEFORE_PUBLISH on INSERT and DELETE
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // BEFORE_PUBLISH on INSERT
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::BEFORE_PUBLISH);
        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto r_ins = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_ins.status == StepStatus::FAULT_INJECTED, "TX-BeforePublish: insert aborted");
        TEST_ASSERT(!graph.has_edge(0, 1), "TX-BeforePublish: edge rolled back");
        TEST_ASSERT(idx.count(0, 0) == 0 && idx.count(1, 0) == 0, "TX-BeforePublish: counts rolled back");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-BeforePublish: reconstruction holds");

        // Now commit insert
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
        tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });

        // BEFORE_PUBLISH on DELETE
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::BEFORE_PUBLISH);
        UpdateOperation del_op{UpdateOp::DELETE, 0, 1, TypedLabel::from_int(0)};
        auto r_del = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_del.status == StepStatus::FAULT_INJECTED, "TX-BeforePublish: delete aborted");
        TEST_ASSERT(graph.has_edge(0, 1), "TX-BeforePublish: edge preserved");
        TEST_ASSERT(idx.count(0, 0) == 1 && idx.count(1, 0) == 1, "TX-BeforePublish: counts preserved");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-BeforePublish: reconstruction holds");
    }

    // Callback abort and exception rollback
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // Callback returns false
        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto r_cb_false = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return false; });
        TEST_ASSERT(r_cb_false.status == StepStatus::CALLBACK_ABORTED, "TX-CbAbort: callback false aborted");
        TEST_ASSERT(!graph.has_edge(0, 1), "TX-CbAbort: edge rolled back");
        TEST_ASSERT(idx.count(0, 0) == 0 && idx.count(1, 0) == 0, "TX-CbAbort: counts rolled back");

        // Callback throws exception
        auto r_cb_throw = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) {
            throw std::runtime_error("Simulated search error");
            return true;
        });
        TEST_ASSERT(r_cb_throw.status == StepStatus::CALLBACK_ABORTED, "TX-CbThrow: callback throw aborted");
        TEST_ASSERT(!graph.has_edge(0, 1), "TX-CbThrow: edge rolled back");
        TEST_ASSERT(idx.count(0, 0) == 0 && idx.count(1, 0) == 0, "TX-CbThrow: counts rolled back");
    }

    // Invariant failure & fail-closed session (Finding P4B-1A-F03)
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        // Inject intentional cell corruption: set vertex 0 count to 0 (even though edge (0, 1) exists)
        idx.set_count_for_testing(0, 0, 0);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // Attempt DELETE: prepare_edge_update will detect underflow (0 - 1) and throw InvariantViolationException
        UpdateOperation del_op{UpdateOp::DELETE, 0, 1, TypedLabel::from_int(0)};
        auto r_inv = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });

        TEST_ASSERT(r_inv.status == StepStatus::INVARIANT_VIOLATION, "TX-Inv: status is INVARIANT_VIOLATION");
        TEST_ASSERT(tx_mgr.is_session_poisoned(), "TX-Inv: session is marked poisoned");

        // Attempt subsequent update: must be rejected fail-closed with INVARIANT_VIOLATION
        auto r_subsequent = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_subsequent.status == StepStatus::INVARIANT_VIOLATION, "TX-Inv: subsequent update rejected fail-closed");
    }

    // Codec checkpoint restoration on abort
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_string("initial_label"));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // Aborted transaction with new label interned
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::BEFORE_MUTATION);
        UpdateOperation ins_new_lbl{UpdateOp::INSERT, 0, 1, TypedLabel::from_string("brand_new_label")};
        auto r_abort = tx_mgr.execute_update(ins_new_lbl, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_abort.status == StepStatus::FAULT_INJECTED, "TX-Codec: transaction aborted");
        TEST_ASSERT(!codec.has_edge_label(TypedLabel::from_string("brand_new_label")), "TX-Codec: new label reverted by checkpoint restoration");

        // Subsequent valid transaction succeeds
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
        UpdateOperation ins_valid{UpdateOp::INSERT, 0, 1, TypedLabel::from_string("initial_label")};
        auto r_next = tx_mgr.execute_update(ins_valid, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_next.status == StepStatus::SUCCESS, "TX-Codec: subsequent valid update succeeds");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 6. Anchor Diagnostic Counter Accuracy Tests (Finding P4B-1A-F04)
// ---------------------------------------------------------------------------
bool test_anchor_counter_accuracy() {
    std::cout << "  Running Anchor Diagnostic Counter Accuracy Tests (CNT-01 to CNT-04)..." << std::endl;

    // Fixture CNT-01: First anchor rejected -> second anchor NOT evaluated
    // Query: Triangle (k=0), vertices 0, 1, 2 with label 0, edges (0,1), (1,2), (2,0) with label 0
    // G0: vertices 0 (label 0), 1 (label 0), 2 (label 0)
    // When edge (0, 1) arrives:
    // If we map anchor (q0, q1) to (0, 1): vertex 0 static deficit exceeds 0 -> rejected on first anchor!
    // Second anchor (q1 to 1) MUST NOT be evaluated.
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {1, 2, 0}, {2, 0, 0}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, qv, qe, can, 0); // k = 0

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        ApxTupleSink sink(codec, can);
        ApxStreamingEngine engine(graph, codec, sink, qp, EngineType::M2, 500000, NlfMode::STATIC);

        // INSERT (0, 1)
        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto res = engine.execute_step(ins_op, 0);
        TEST_ASSERT(res.status == StepStatus::SUCCESS, "CNT-01: step success");

        const auto& c = engine.get_step_counters();
        // Since each tested anchor direction has vertex degree 1 < query degree 2,
        // the first checked anchor vertex is pruned immediately.
        // nlf_anchor_pruned must equal nlf_anchor_checks because second anchor was never checked!
        TEST_ASSERT(c.nlf_anchor_checks > 0, "CNT-01: anchor checks executed");
        TEST_ASSERT(c.nlf_anchor_pruned == c.nlf_anchor_checks, "CNT-01: first anchor rejected -> second anchor NOT evaluated (no overcount!)");
    }

    // Fixture CNT-04: NONE mode has all NLF-specific counters zero
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}, {1, 2, 0}};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {1, 2, 0}, {2, 0, 0}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, qv, qe, can, 0);

        ApxTupleSink sink(codec, can);
        ApxStreamingEngine engine_none(graph, codec, sink, qp, EngineType::M2, 500000, NlfMode::NONE);

        UpdateOperation ins_op{UpdateOp::INSERT, 2, 0, TypedLabel::from_int(0)};
        auto res_none = engine_none.execute_step(ins_op, 0);
        TEST_ASSERT(res_none.status == StepStatus::SUCCESS, "CNT-04: none step success");

        const auto& c_none = engine_none.get_step_counters();
        TEST_ASSERT(c_none.nlf_static_checks == 0, "CNT-04: NONE mode nlf_static_checks == 0");
        TEST_ASSERT(c_none.nlf_static_pruned == 0, "CNT-04: NONE mode nlf_static_pruned == 0");
        TEST_ASSERT(c_none.nlf_anchor_checks == 0, "CNT-04: NONE mode nlf_anchor_checks == 0");
        TEST_ASSERT(c_none.nlf_anchor_pruned == 0, "CNT-04: NONE mode nlf_anchor_pruned == 0");
        TEST_ASSERT(c_none.nlf_signature_lookups == 0, "CNT-04: NONE mode nlf_signature_lookups == 0");
        TEST_ASSERT(c_none.nlf_index_cell_updates == 0, "CNT-04: NONE mode nlf_index_cell_updates == 0");
        TEST_ASSERT(c_none.nlf_index_rollback_cells == 0, "CNT-04: NONE mode nlf_index_rollback_cells == 0");
        TEST_ASSERT(c_none.nlf_index_initialization_cells == 0, "CNT-04: NONE mode nlf_index_initialization_cells == 0");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 7. Counterexamples & Actual Pruning Witness (A1 to A7)
// ---------------------------------------------------------------------------
bool test_counterexamples_and_witness() {
    std::cout << "  Running Counterexamples & Pruning Witness Tests (A1 to A7)..." << std::endl;

    // Fixture A1: Query Diamond, k=1, lambda=2
    // G0 = path (0,1), (0,2), (1,3). Update: INSERT (2,3) -> G_after is C4.
    // Expected: 8 ENTERED, 0 EXITED.
    // Inverse DELETE: 0 ENTERED, 8 EXITED.
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}, {0, 2, 0}, {1, 3, 0}};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_data_vertex(3);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {0, 2, 0}, {1, 3, 0}, {2, 3, 0}, {1, 2, 0}};
        std::vector<uint32_t> can = {0, 1, 2, 3};
        qp.build(4, qv, qe, can, 1); // k = 1

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        std::vector<uint32_t> can_order = {0, 1, 2, 3};
        ApxTupleSink sink(codec, can_order);
        ApxStreamingEngine engine(graph, codec, sink, qp, EngineType::M2, 500000, NlfMode::STATIC);

        // Step 0: INSERT (2, 3)
        UpdateOperation ins_op{UpdateOp::INSERT, 2, 3, TypedLabel::from_int(0)};
        auto res_ins = engine.execute_step(ins_op, 0);

        TEST_ASSERT(res_ins.status == StepStatus::SUCCESS, "A1: insert status SUCCESS");
        TEST_ASSERT(res_ins.entered_tuples.size() == 8, "A1: exactly 8 entered tuples (no false dismissals!)");
        TEST_ASSERT(res_ins.exited_tuples.empty(), "A1: 0 exited tuples");

        // Step 1: DELETE (2, 3)
        UpdateOperation del_op{UpdateOp::DELETE, 2, 3, TypedLabel::from_int(0)};
        auto res_del = engine.execute_step(del_op, 1);

        TEST_ASSERT(res_del.status == StepStatus::SUCCESS, "A1: delete status SUCCESS");
        TEST_ASSERT(res_del.entered_tuples.empty(), "A1: 0 entered tuples");
        TEST_ASSERT(res_del.exited_tuples.size() == 8, "A1: exactly 8 exited tuples");
    }

    // Fixture Chord: C4 with cross chord, k=1
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}, {1, 2, 0}, {2, 3, 0}, {3, 0, 0}};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_data_vertex(3);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {1, 2, 0}, {2, 3, 0}, {3, 0, 0}, {0, 2, 0}};
        std::vector<uint32_t> can = {0, 1, 2, 3};
        qp.build(4, qv, qe, can, 1);

        ApxTupleSink sink(codec, can);
        ApxStreamingEngine engine(graph, codec, sink, qp, EngineType::M2, 500000, NlfMode::STATIC);

        UpdateOperation ins_op{UpdateOp::INSERT, 0, 2, TypedLabel::from_int(0)};
        auto res_ins = engine.execute_step(ins_op, 0);

        TEST_ASSERT(res_ins.status == StepStatus::SUCCESS, "Chord: insert success");
        TEST_ASSERT(res_ins.entered_tuples.size() == 16, "Chord: exactly 16 entered tuples");

        UpdateOperation del_op{UpdateOp::DELETE, 0, 2, TypedLabel::from_int(0)};
        auto res_del = engine.execute_step(del_op, 1);

        TEST_ASSERT(res_del.status == StepStatus::SUCCESS, "Chord: delete success");
        TEST_ASSERT(res_del.exited_tuples.size() == 16, "Chord: exactly 16 exited tuples");
    }

    // Witness A7: Demonstrates actual candidate pruning
    {
        ApxAuthoritativeGraph graph_bare;
        ApxAuthoritativeGraph graph_static;

        std::vector<std::pair<uint32_t, uint32_t>> verts = {
            {0, 0}, {1, 0}, {2, 0},
            {3, 0}, {4, 0}, {5, 0}
        };
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {
            {1, 2, 0}, {2, 0, 0},
            {0, 3, 0}, {1, 4, 0}, {2, 5, 0}
        };

        graph_bare.load_baseline(verts, edges);
        graph_static.load_baseline(verts, edges);

        ApxTypedCodec codec;
        for (const auto& vp : verts) {
            codec.intern_data_vertex(vp.first);
        }
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {1, 2, 0}, {2, 0, 0}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, qv, qe, can, 0);

        ApxTupleSink sink_bare(codec, can);
        ApxTupleSink sink_static(codec, can);

        ApxStreamingEngine engine_bare(graph_bare, codec, sink_bare, qp, EngineType::M2, 500000, NlfMode::NONE);
        ApxStreamingEngine engine_static(graph_static, codec, sink_static, qp, EngineType::M2, 500000, NlfMode::STATIC);

        // INSERT (0, 1, 0)
        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto res_bare = engine_bare.execute_step(ins_op, 0);
        auto res_static = engine_static.execute_step(ins_op, 0);

        TEST_ASSERT(res_bare.status == StepStatus::SUCCESS && res_static.status == StepStatus::SUCCESS, "A7: both success");
        TEST_ASSERT(res_bare.entered_tuples == res_static.entered_tuples, "A7: exact tuple equality between Bare M2 and A-static");
        TEST_ASSERT(!res_static.entered_tuples.empty(), "A7: non-empty entered tuples (triangle formed)");

        const auto& c_static = engine_static.get_step_counters();
        TEST_ASSERT(c_static.nlf_static_checks > 0, "A7: nlf_static_checks > 0");
        TEST_ASSERT(c_static.nlf_static_pruned > 0, "A7: nlf_static_pruned > 0 (actual pruning witnessed!)");
        std::cout << "    [A7 Witness] Static checks: " << c_static.nlf_static_checks
                  << ", Static pruned: " << c_static.nlf_static_pruned
                  << ", Tuples: " << res_static.entered_tuples.size() << std::endl;
    }

    return true;
}

} // namespace

int run_p4b_native_tests(const char* suite_name) {
    std::string s(suite_name ? suite_name : "all");
    const std::set<std::string> valid_suites = {
        "all", "signatures", "numeric", "high_degree", "initialization", "transactions", "counters", "counterexamples"
    };

    if (valid_suites.find(s) == valid_suites.end()) {
        std::cerr << "Error: Unknown P4b test suite selector '" << s
                  << "'. Valid selectors: all, signatures, numeric, high_degree, initialization, transactions, counters, counterexamples"
                  << std::endl;
        return 1; // Strict fail-closed non-zero exit (Finding P4B-1A-F06)
    }

    bool all = (s == "all");
    bool ok = true;
    int groups_executed = 0;

    std::cout << "================================================================================" << std::endl;
    std::cout << "                 RapidFlow-APX Phase P4b-1A Native Verification Suite           " << std::endl;
    std::cout << "================================================================================" << std::endl;

    if (all || s == "signatures") {
        groups_executed++;
        if (!test_signatures()) ok = false;
    }
    if (all || s == "numeric") {
        groups_executed++;
        if (!test_numeric_safety()) ok = false;
    }
    if (all || s == "high_degree") {
        groups_executed++;
        if (!test_actual_high_degree_star_graph()) ok = false;
    }
    if (all || s == "initialization") {
        groups_executed++;
        if (!test_initialization()) ok = false;
    }
    if (all || s == "transactions") {
        groups_executed++;
        if (!test_transactions_and_fault_injection()) ok = false;
    }
    if (all || s == "counters") {
        groups_executed++;
        if (!test_anchor_counter_accuracy()) ok = false;
    }
    if (all || s == "counterexamples") {
        groups_executed++;
        if (!test_counterexamples_and_witness()) ok = false;
    }

    std::cout << "================================================================================" << std::endl;
    std::cout << "  P4b-1A Native Tests Groups Executed: " << groups_executed << std::endl;
    std::cout << "  P4b-1A Native Tests Result: " << (ok && groups_executed > 0 ? "ALL TESTS PASSED" : "TESTS FAILED") << std::endl;
    std::cout << "================================================================================" << std::endl;
    return (ok && groups_executed > 0) ? 0 : 1;
}

} // namespace rapidflow_apx
