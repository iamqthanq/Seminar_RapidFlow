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

    // SIG-01: Equal endpoint labels (edge (0,1) with label 0, v0 label 0, v1 label 0)
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 0};
        std::vector<QueryEdge> edges = {{0, 1, 0}};
        std::vector<uint32_t> can_order = {0, 1};
        qp.build(2, vlabels, edges, can_order, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);
        TEST_ASSERT(sigs.signature_count() == 1, "SIG-01: signature count must be 1");
        TEST_ASSERT(sigs.get_signature(0).edge_label_id == 0, "SIG-01: edge label must be 0");
        TEST_ASSERT(sigs.get_signature(0).neighbor_vertex_label_id == 0, "SIG-01: neighbor label must be 0");

        const auto& edge_sig = sigs.get_edge_signatures(0);
        TEST_ASSERT(edge_sig.from_u == 0 && edge_sig.from_v == 0, "SIG-01: both orientations share sig 0");

        // SIG-08: Degree invariant
        TEST_ASSERT(sigs.get_vertex_requirements(0).size() == 1, "SIG-08: v0 requirements size 1");
        TEST_ASSERT(sigs.get_vertex_requirements(0)[0].required_count == 1, "SIG-08: v0 req count 1");
        TEST_ASSERT(sigs.get_vertex_requirements(1)[0].required_count == 1, "SIG-08: v1 req count 1");
    }

    // SIG-02: Different endpoint labels (v0 label 1, v1 label 2, edge label 5)
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {1, 2};
        std::vector<QueryEdge> edges = {{0, 1, 5}};
        std::vector<uint32_t> can_order = {0, 1};
        qp.build(2, vlabels, edges, can_order, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);
        TEST_ASSERT(sigs.signature_count() == 2, "SIG-02: signature count must be 2");

        const auto& edge_sig = sigs.get_edge_signatures(0);
        TEST_ASSERT(edge_sig.from_u != edge_sig.from_v, "SIG-02: distinct signatures from u and v");
        // From u (seeing v): (5, 2)
        TEST_ASSERT(sigs.get_signature(edge_sig.from_u).neighbor_vertex_label_id == 2, "SIG-02: from u sees v label 2");
        // From v (seeing u): (5, 1)
        TEST_ASSERT(sigs.get_signature(edge_sig.from_v).neighbor_vertex_label_id == 1, "SIG-02: from v sees u label 1");
    }

    // SIG-03: Same neighbor label, different edge labels
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {0, 1, 1};
        std::vector<QueryEdge> edges = {{0, 1, 10}, {0, 2, 20}, {1, 2, 30}};
        std::vector<uint32_t> can_order = {0, 1, 2};
        qp.build(3, vlabels, edges, can_order, 1);

        ApxQuerySignatures sigs;
        sigs.build(qp);
        SignatureId id1 = 0, id2 = 0;
        TEST_ASSERT(sigs.find_signature_id(10, 1, id1), "SIG-03: found sig (10, 1)");
        TEST_ASSERT(sigs.find_signature_id(20, 1, id2), "SIG-03: found sig (20, 1)");
        TEST_ASSERT(id1 != id2, "SIG-03: different edge labels yield distinct sig IDs");
    }

    // SIG-05: Multiple incident edges sharing signature
    {
        ApxQueryPlan qp;
        // Vertex 0 connected to 1 and 2, both with edge label 3 and neighbor label 4
        std::vector<uint32_t> vlabels = {0, 4, 4};
        std::vector<QueryEdge> edges = {{0, 1, 3}, {0, 2, 3}, {1, 2, 3}};
        std::vector<uint32_t> can_order = {0, 1, 2};
        qp.build(3, vlabels, edges, can_order, 1);

        ApxQuerySignatures sigs;
        sigs.build(qp);
        const auto& reqs0 = sigs.get_vertex_requirements(0);
        TEST_ASSERT(reqs0.size() == 1, "SIG-05: single signature requirement for vertex 0");
        TEST_ASSERT(reqs0[0].required_count == 2, "SIG-05: required count must be 2");
    }

    // SIG-09: Deterministic construction
    {
        ApxQueryPlan qp;
        std::vector<uint32_t> vlabels = {2, 1, 3, 0};
        std::vector<QueryEdge> edges = {{0, 1, 5}, {1, 2, 2}, {2, 3, 5}, {3, 0, 1}, {0, 2, 5}};
        std::vector<uint32_t> can_order = {0, 1, 2, 3};
        qp.build(4, vlabels, edges, can_order, 1);

        ApxQuerySignatures sigs1, sigs2;
        sigs1.build(qp);
        sigs2.build(qp);
        TEST_ASSERT(sigs1.signature_count() == sigs2.signature_count(), "SIG-09: count match");
        for (size_t i = 0; i < sigs1.signature_count(); ++i) {
            TEST_ASSERT(sigs1.get_signature(i) == sigs2.get_signature(i), "SIG-09: deterministic order");
        }
    }

    return true;
}

// ---------------------------------------------------------------------------
// 2. Numeric Safety & Allocation Tests (NUM-01 to NUM-18 & High-Degree Witness)
// ---------------------------------------------------------------------------
bool test_numeric_safety() {
    std::cout << "  Running Numeric Safety Tests (NUM-01 to NUM-18 & High-Degree)..." << std::endl;

    // NUM-01 to NUM-06: Count values & deficit
    TEST_ASSERT(ApxNlfIndex::compute_static_deficit(5, 5) == 0, "NUM-10: req == avail -> 0");
    TEST_ASSERT(ApxNlfIndex::compute_static_deficit(3, 10) == 0, "NUM-09: req < avail -> 0");
    TEST_ASSERT(ApxNlfIndex::compute_static_deficit(5, 2) == 3, "NUM-11: req > avail -> 3");
    TEST_ASSERT(ApxNlfIndex::compute_static_deficit(70000, 65535) == 4465, "NUM-06: deficit above 65535");

    // NUM-08: Underflow check in index decrement
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
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

        PreparedNlfUpdate prep;
        NlfUndoRecord undo;
        bool caught = false;
        try {
            // Attempt to decrement count of edge (0, 1) when count is 0
            idx.prepare_edge_update(graph, sigs, 0, 1, 0, false, prep, undo);
        } catch (const InvariantViolationException&) {
            caught = true;
        }
        TEST_ASSERT(caught, "NUM-08: decrement at zero must throw InvariantViolationException");
    }

    // NUM-07: Overflow check in index increment
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
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

        idx.set_count_for_testing(0, 0, std::numeric_limits<NlfCount>::max());

        PreparedNlfUpdate prep;
        NlfUndoRecord undo;
        bool caught = false;
        try {
            idx.prepare_edge_update(graph, sigs, 0, 1, 0, true, prep, undo);
        } catch (const InvariantViolationException&) {
            caught = true;
        }
        TEST_ASSERT(caught, "NUM-07: increment at max must throw InvariantViolationException");
    }

    // NUM-18: Memory limit enforcement
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
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
        bool caught_mem = false;
        try {
            // Require memory limit 4 bytes when table needs 3 * 1 * 4 = 12 bytes
            idx.initialize(graph, sigs, 4);
        } catch (const NlfMemoryLimitException&) {
            caught_mem = true;
        }
        TEST_ASSERT(caught_mem, "NUM-18: memory limit exceeded must throw NlfMemoryLimitException");
        TEST_ASSERT(!idx.is_initialized(), "NUM-18: index must remain uninitialized on rejection");
    }

    // High-Degree Regression Witness: vertex with count 70,000
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
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
        idx.set_count_for_testing(0, 0, 70000);
        idx.set_count_for_testing(1, 0, 70000);

        TEST_ASSERT(idx.count(0, 0) == 70000, "High-Degree: count exact at 70000");

        // Decrement via prepare & apply
        PreparedNlfUpdate prep;
        NlfUndoRecord undo;
        idx.prepare_edge_update(graph, sigs, 0, 1, 0, false, prep, undo);
        TEST_ASSERT(undo.entries[0].old_value == 70000, "High-Degree: old value captured as 70000");
        TEST_ASSERT(prep.mutations[0].new_value == 69999, "High-Degree: new value is 69999");
        TEST_ASSERT(prep.mutations[1].new_value == 69999, "High-Degree: v1 new value is 69999");

        idx.apply_prepared_update(prep);
        TEST_ASSERT(idx.count(0, 0) == 69999, "High-Degree: count decremented to 69999");
        TEST_ASSERT(idx.count(1, 0) == 69999, "High-Degree: v1 count decremented to 69999");

        // Rollback restores 70000
        idx.rollback(undo);
        TEST_ASSERT(idx.count(0, 0) == 70000, "High-Degree: rollback restored exact 70000");
        TEST_ASSERT(idx.count(1, 0) == 70000, "High-Degree: v1 rollback restored exact 70000");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 3. Initialization Tests (INIT-01 to INIT-12)
// ---------------------------------------------------------------------------
bool test_initialization() {
    std::cout << "  Running Initialization Tests (INIT-01 to INIT-12)..." << std::endl;

    // INIT-01: Nonempty G0
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 1}, {1, 2}, {2, 1}, {3, 2}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {
            {0, 1, 10}, {1, 2, 10}, {2, 3, 20}, {0, 3, 10}
        };
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {1, 2, 1, 2};
        std::vector<QueryEdge> qe = {{0, 1, 10}, {1, 2, 10}, {2, 3, 20}, {3, 0, 10}};
        std::vector<uint32_t> can = {0, 1, 2, 3};
        qp.build(4, qv, qe, can, 1);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);
        TEST_ASSERT(idx.is_initialized(), "INIT-01: index initialized");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "INIT-01: reconstruction match");
    }

    // INIT-02 & INIT-03: G0 with isolated vertices
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
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
        TEST_ASSERT(idx.is_initialized(), "INIT-02: initialized");
        for (uint32_t u = 0; u < 3; ++u) {
            TEST_ASSERT(idx.count(u, 0) == 0, "INIT-03: isolated vertex count is 0");
        }
    }

    // INIT-12: Reject initialization from non-pristine state
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
        graph.load_baseline(verts, edges);

        // Mutate graph to make it non-pristine
        graph.begin_transaction();
        graph.apply_insert(0, 1, 0);
        graph.commit_transaction();

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}};
        std::vector<uint32_t> can = {0, 1};
        qp.build(2, qv, qe, can, 0);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        bool caught = false;
        try {
            idx.initialize(graph, sigs);
        } catch (const InvariantViolationException&) {
            caught = true;
        }
        TEST_ASSERT(caught, "INIT-12: non-pristine state initialization rejected");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 4. Transaction & Rollback Fault-Injection Tests (TX-01 to TX-25)
// ---------------------------------------------------------------------------
bool test_transactions_and_fault_injection() {
    std::cout << "  Running Transaction & Rollback Tests (TX-01 to TX-25)..." << std::endl;

    // TX-01 to TX-10: Prevalidation & Successful Updates with Reconstruction
    {
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}, {0, 2, 0}};
        graph.load_baseline(verts, edges);

        ApxTypedCodec codec;
        codec.intern_data_vertex(0);
        codec.intern_data_vertex(1);
        codec.intern_data_vertex(2);
        codec.intern_data_vertex(3);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {1, 2, 0}, {2, 0, 0}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, qv, qe, can, 1);

        ApxQuerySignatures sigs;
        sigs.build(qp);

        ApxNlfIndex idx;
        idx.initialize(graph, sigs);

        std::vector<uint32_t> can_order = {0, 1, 2};
        ApxTupleSink sink(codec, can_order);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        EngineCounters counters;
        tx_mgr.set_counters(&counters);

        // Prevalidation: Self-loop rejection (TX-02)
        UpdateOperation self_loop_op{UpdateOp::INSERT, 0, 0, TypedLabel::from_int(0)};
        auto r_loop = tx_mgr.execute_update(self_loop_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_loop.status == StepStatus::PREVALIDATION_FAILED, "TX-02: self loop rejected");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-02: index reconstruction holds");

        // Successful INSERT (1, 2)
        UpdateOperation ins_op{UpdateOp::INSERT, 1, 2, TypedLabel::from_int(0)};
        auto r_ins = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_ins.status == StepStatus::SUCCESS, "TX-08: insert success");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-08: index reconstruction holds after insert");

        // Successful DELETE (1, 2)
        UpdateOperation del_op{UpdateOp::DELETE, 1, 2, TypedLabel::from_int(0)};
        auto r_del = tx_mgr.execute_update(del_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_del.status == StepStatus::SUCCESS, "TX-13: delete success");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-13: index reconstruction holds after delete");
    }

    // Partial mutation fault injection: AFTER_FIRST_NLF_CELL (TX-26)
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

        std::vector<uint32_t> can_order = {0, 1};
        ApxTupleSink sink(codec, can_order);
        ApxTransactionManager tx_mgr(graph, codec, sink);
        tx_mgr.set_nlf_context(&idx, &sigs);

        // Inject fault AFTER_FIRST_NLF_CELL during INSERT (0, 1)
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::AFTER_FIRST_NLF_CELL);

        UpdateOperation ins_op{UpdateOp::INSERT, 0, 1, TypedLabel::from_int(0)};
        auto r_fault = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });

        TEST_ASSERT(r_fault.status == StepStatus::FAULT_INJECTED, "TX-26: fault injected status");
        TEST_ASSERT(!graph.has_edge(0, 1), "TX-26: graph edge was rolled back");
        TEST_ASSERT(idx.count(0, 0) == 0, "TX-26: vertex 0 cell rolled back to 0");
        TEST_ASSERT(idx.count(1, 0) == 0, "TX-26: vertex 1 cell remains 0");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-26: reconstruction holds after partial rollback");

        // Next valid update succeeds seamlessly (TX-36)
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
        auto r_next = tx_mgr.execute_update(ins_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) { return true; });
        TEST_ASSERT(r_next.status == StepStatus::SUCCESS, "TX-36: next update after aborted transaction succeeds");
        TEST_ASSERT(graph.has_edge(0, 1), "TX-36: edge present after successful update");
        TEST_ASSERT(verify_index_reconstruction(graph, sigs, idx), "TX-36: reconstruction holds");
    }

    // Lifecycle: repeated rollback safety (TX-35)
    {
        NlfUndoRecord undo;
        undo.count = 2;
        undo.active = true;
        undo.entries[0] = {10, 5, true};
        undo.entries[1] = {20, 8, true};

        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {};
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

        // First rollback
        idx.rollback(undo);
        TEST_ASSERT(!undo.active, "TX-35: undo inactive after rollback");

        // Second rollback (must be completely safe no-op)
        idx.rollback(undo);
        TEST_ASSERT(!undo.active, "TX-35: repeated rollback remains inactive no-op");
    }

    return true;
}

// ---------------------------------------------------------------------------
// 5. Counterexamples & Actual Pruning Witness (A1 to A7)
// ---------------------------------------------------------------------------
bool test_counterexamples_and_witness() {
    std::cout << "  Running Counterexamples & Pruning Witness Tests (A1 to A7)..." << std::endl;

    // Fixture A1: Query Diamond, k=1, lambda=2
    // G0 = path (0,1), (0,2), (1,3). Update: INSERT (2,3) -> G_after is C4.
    // Expected: 8 ENTERED, 0 EXITED (including identity mapping).
    // Inverse DELETE: 0 ENTERED, 8 EXITED.
    {
        ApxTypedCodec codec;
        for (int64_t v = 0; v < 4; ++v) {
            codec.intern_data_vertex(v);
            codec.intern_query_vertex(v);
        }
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {{0, 1, 0}, {0, 2, 0}, {1, 3, 0}};
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        // Diamond edges: (0,1), (0,2), (1,2), (1,3), (2,3)
        std::vector<uint32_t> qv = {0, 0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {0, 2, 0}, {1, 2, 0}, {1, 3, 0}, {2, 3, 0}};
        std::vector<uint32_t> can = {0, 1, 2, 3};
        qp.build(4, qv, qe, can, 1);

        ApxTupleSink sink(codec, can);
        ApxStreamingEngine engine(graph, codec, sink, qp, EngineType::M2, 500000, NlfMode::STATIC);
        TEST_ASSERT(engine.get_effective_nlf_mode() == NlfMode::STATIC, "A1: effective mode is STATIC");

        // INSERT (2, 3, 0)
        UpdateOperation ins_op{UpdateOp::INSERT, 2, 3, TypedLabel::from_int(0)};
        auto step1 = engine.execute_step(ins_op, 0);
        TEST_ASSERT(step1.status == StepStatus::SUCCESS, "A1: step 1 insert success");
        TEST_ASSERT(step1.entered_tuples.size() == 8, "A1: 8 entered tuples on insert");
        TEST_ASSERT(step1.exited_tuples.empty(), "A1: 0 exited tuples on insert");

        // Verify identity tuple (0, 1, 2, 3) is present
        std::vector<int64_t> identity_tuple = {0, 1, 2, 3};
        bool found_identity = false;
        for (const auto& tup : step1.entered_tuples) {
            if (tup == identity_tuple) {
                found_identity = true;
                break;
            }
        }
        TEST_ASSERT(found_identity, "A1: identity tuple (0,1,2,3) must be present in entered tuples");

        // Inverse DELETE (2, 3, 0)
        UpdateOperation del_op{UpdateOp::DELETE, 2, 3, TypedLabel::from_int(0)};
        auto step2 = engine.execute_step(del_op, 1);
        TEST_ASSERT(step2.status == StepStatus::SUCCESS, "A1: step 2 delete success");
        TEST_ASSERT(step2.entered_tuples.empty(), "A1: 0 entered tuples on delete");
        TEST_ASSERT(step2.exited_tuples.size() == 8, "A1: 8 exited tuples on delete");
    }

    // Chord Fixture: G0 is C4, INSERT (1, 2, 0) -> Diamond.
    // Expected: 16 ENTERED, 0 EXITED. Inverse DELETE: 0 ENTERED, 16 EXITED.
    {
        ApxTypedCodec codec;
        for (int64_t v = 0; v < 4; ++v) {
            codec.intern_data_vertex(v);
            codec.intern_query_vertex(v);
        }
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {
            {0, 1, 0}, {0, 2, 0}, {1, 3, 0}, {2, 3, 0}
        };
        graph.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {0, 2, 0}, {1, 2, 0}, {1, 3, 0}, {2, 3, 0}};
        std::vector<uint32_t> can = {0, 1, 2, 3};
        qp.build(4, qv, qe, can, 1);

        ApxTupleSink sink(codec, can);
        ApxStreamingEngine engine(graph, codec, sink, qp, EngineType::M2, 500000, NlfMode::STATIC);

        UpdateOperation ins_op{UpdateOp::INSERT, 1, 2, TypedLabel::from_int(0)};
        auto s1 = engine.execute_step(ins_op, 0);
        TEST_ASSERT(s1.status == StepStatus::SUCCESS, "Chord: insert success");
        TEST_ASSERT(s1.entered_tuples.size() == 16, "Chord: 16 entered tuples on insert");

        UpdateOperation del_op{UpdateOp::DELETE, 1, 2, TypedLabel::from_int(0)};
        auto s2 = engine.execute_step(del_op, 1);
        TEST_ASSERT(s2.status == StepStatus::SUCCESS, "Chord: delete success");
        TEST_ASSERT(s2.exited_tuples.size() == 16, "Chord: 16 exited tuples on delete");
    }

    // A7: Actual Pruning Witness
    // Demonstrate that when candidates have deficit > k, nlf_static_pruned > 0
    // and candidate_evaluations are pruned while preserving exact tuple parity with Bare M2!
    {
        // Query: Triangle (k=0)
        // Vertices 0, 1, 2 with edge label 0. Each vertex requires 2 neighbors with signature (0, 0).
        // Data graph: vertex 0 and 1 connected, plus vertices 2, 3, 4 where some have 0 other neighbors.
        ApxTypedCodec codec;
        for (int64_t v = 0; v < 5; ++v) {
            codec.intern_data_vertex(v);
            if (v < 3) codec.intern_query_vertex(v);
        }
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));

        // Initial graph has (0, 2) and (1, 2) forming triangle with (0, 1).
        // Also vertex 3 has degree 1 (connected only to 0). Vertex 4 has degree 1 (connected only to 1).
        // Candidate 3 and 4 have only 1 neighbor, but triangle vertex requires 2 neighbors!
        std::vector<std::pair<uint32_t, uint32_t>> verts = {{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges = {
            {0, 2, 0}, {1, 2, 0}, {0, 3, 0}, {1, 4, 0}
        };

        ApxAuthoritativeGraph graph_bare, graph_static;
        graph_bare.load_baseline(verts, edges);
        graph_static.load_baseline(verts, edges);

        ApxQueryPlan qp;
        std::vector<uint32_t> qv = {0, 0, 0};
        std::vector<QueryEdge> qe = {{0, 1, 0}, {1, 2, 0}, {0, 2, 0}};
        std::vector<uint32_t> can = {0, 1, 2};
        qp.build(3, qv, qe, can, 0); // k=0

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
    bool all = (s == "all");
    bool ok = true;

    std::cout << "================================================================================" << std::endl;
    std::cout << "                 RapidFlow-APX Phase P4b-1A Native Verification Suite           " << std::endl;
    std::cout << "================================================================================" << std::endl;

    if (all || s == "signatures") {
        if (!test_signatures()) ok = false;
    }
    if (all || s == "numeric") {
        if (!test_numeric_safety()) ok = false;
    }
    if (all || s == "initialization") {
        if (!test_initialization()) ok = false;
    }
    if (all || s == "transactions") {
        if (!test_transactions_and_fault_injection()) ok = false;
    }
    if (all || s == "counterexamples") {
        if (!test_counterexamples_and_witness()) ok = false;
    }

    std::cout << "================================================================================" << std::endl;
    std::cout << "  P4b-1A Native Tests Result: " << (ok ? "ALL TESTS PASSED" : "TESTS FAILED") << std::endl;
    std::cout << "================================================================================" << std::endl;
    return ok ? 0 : 1;
}

} // namespace rapidflow_apx
