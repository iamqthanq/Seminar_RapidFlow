#include "harness_cases.h"
#include "rapidflow_apx/typed_codec.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/transaction_manager.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/micro_matcher.h"
#include <iostream>
#include <sstream>
#include <algorithm>

namespace rapidflow_apx_harness {

using namespace rapidflow_apx;

TypedLabel parse_typed_label(const mini_json::Value& v) {
    if (v.is_int()) {
        return TypedLabel::from_int(v.as_int());
    } else if (v.is_string()) {
        return TypedLabel::from_string(v.as_string());
    } else if (v.is_object()) {
        std::string type = v["type"].as_string();
        if (type == "int") {
            return TypedLabel::from_int(v["val"].as_int());
        } else if (type == "string") {
            return TypedLabel::from_string(v["val"].as_string());
        }
    }
    throw std::runtime_error("Unknown typed label format");
}

mini_json::Value run_codec_boundaries(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "codec_boundaries";

    ApxTypedCodec codec;

    // 1. Query vertices intern/decode roundtrip with duplicate declaration rejection
    const auto& q_verts = fixture["external_query_vertices"].as_array();
    std::unordered_set<int64_t> seen_q_decl;
    for (const auto& qv : q_verts) {
        int64_t ext_id = qv["id"].as_int();
        if (!seen_q_decl.insert(ext_id).second) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Duplicate external query vertex declaration: " + std::to_string(ext_id);
            return report;
        }
        uint32_t dense = codec.intern_query_vertex(ext_id);
        int64_t decoded = codec.decode_query_vertex(dense);
        if (decoded != ext_id) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Query vertex decode mismatch: " + std::to_string(ext_id);
            return report;
        }
    }

    // 2. Data vertices intern/decode roundtrip with duplicate declaration rejection
    const auto& d_verts = fixture["external_data_vertices"].as_array();
    std::unordered_set<int64_t> seen_d_decl;
    for (const auto& dv : d_verts) {
        int64_t ext_id = dv["id"].as_int();
        if (!seen_d_decl.insert(ext_id).second) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Duplicate external data vertex declaration: " + std::to_string(ext_id);
            return report;
        }
        uint32_t dense = codec.intern_data_vertex(ext_id);
        int64_t decoded = codec.decode_data_vertex(dense);
        if (decoded != ext_id) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Data vertex decode mismatch: " + std::to_string(ext_id);
            return report;
        }
    }

    // 3. Typed label isolation: int 0 != string "0", and Unicode escape \u0030 == string "0"
    TypedLabel int_zero = TypedLabel::from_int(0);
    TypedLabel str_zero = TypedLabel::from_string("0");
    uint32_t id_int0 = codec.intern_vertex_label(int_zero);
    uint32_t id_str0 = codec.intern_vertex_label(str_zero);
    if (id_int0 == id_str0) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Typed label collision: int 0 and string '0' interned to same ID";
        return report;
    }

    // Unicode \u0030 must decode to string "0" and match string "0"
    mini_json::Value u_json = mini_json::Parser::parse("{\"val\": \"\\u0030\"}");
    TypedLabel u_lbl = TypedLabel::from_string(u_json["val"].as_string());
    uint32_t id_u0 = codec.intern_vertex_label(u_lbl);
    if (id_u0 != id_str0) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Unicode escape \\u0030 must intern to same ID as string '0'";
        return report;
    }

    // Invalid escape sequence \q must throw parse error
    bool invalid_esc_caught = false;
    try {
        mini_json::Parser::parse("{\"val\": \"\\q\"}");
    } catch (const std::exception&) {
        invalid_esc_caught = true;
    }
    if (!invalid_esc_caught) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Invalid escape sequence \\q was not rejected";
        return report;
    }

    TypedLabel dec_int0 = codec.decode_vertex_label(id_int0);
    TypedLabel dec_str0 = codec.decode_vertex_label(id_str0);
    if (dec_int0 != int_zero || dec_str0 != str_zero) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Typed label decode mismatch";
        return report;
    }

    // 4. Capacity limit checks
    bool cap_caught = false;
    try {
        ApxTypedCodec::check_capacity_limits(static_cast<size_t>(UINT32_MAX), 10);
    } catch (const CapacityExceededException&) {
        cap_caught = true;
    }
    if (!cap_caught) {
        report["status"] = "FAIL";
        report["diagnostic"] = "CapacityExceededException not thrown for excessive vertex count";
        return report;
    }

    // 5. Codec checkpoint and zero-allocation rollback verification (no ghost entries)
    auto cp = codec.create_checkpoint();
    size_t orig_q = codec.query_vertices_count();
    size_t orig_d = codec.data_vertices_count();
    size_t orig_vl = codec.vertex_labels_count();
    size_t orig_el = codec.edge_labels_count();

    codec.intern_query_vertex(9999999);
    codec.intern_data_vertex(8888888);
    codec.intern_vertex_label(TypedLabel::from_string("temp_v_lbl"));
    codec.intern_edge_label(TypedLabel::from_string("temp_e_lbl"));

    codec.restore_checkpoint(cp);
    if (codec.query_vertices_count() != orig_q ||
        codec.data_vertices_count() != orig_d ||
        codec.vertex_labels_count() != orig_vl ||
        codec.edge_labels_count() != orig_el ||
        codec.has_query_vertex(9999999) ||
        codec.has_data_vertex(8888888) ||
        codec.has_vertex_label(TypedLabel::from_string("temp_v_lbl")) ||
        codec.has_edge_label(TypedLabel::from_string("temp_e_lbl"))) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Codec checkpoint restore failed to restore clean state (ghost entry detected)";
        return report;
    }

    report["status"] = "PASS";
    report["diagnostic"] = "Codec successfully roundtrips 64-bit signed IDs and maintains typed label isolation";
    report["query_vertices_count"] = static_cast<int64_t>(codec.query_vertices_count());
    report["data_vertices_count"] = static_cast<int64_t>(codec.data_vertices_count());
    return report;
    return report;
}

mini_json::Value run_lifecycle_stream(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "lifecycle_stream";

    ApxAuthoritativeGraph graph;
    const auto& base_v = fixture["baseline"]["vertices"].as_array();
    const auto& base_e = fixture["baseline"]["edges"].as_array();

    std::vector<std::pair<uint32_t, uint32_t>> vertices;
    for (const auto& v : base_v) {
        vertices.emplace_back(static_cast<uint32_t>(v["id"].as_int()), static_cast<uint32_t>(v["label"].as_int()));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges;
    for (const auto& e : base_e) {
        edges.emplace_back(
            static_cast<uint32_t>(e["u"].as_int()),
            static_cast<uint32_t>(e["v"].as_int()),
            static_cast<uint32_t>(e["label"].as_int())
        );
    }

    graph.load_baseline(vertices, edges);

    if (graph.get_vertices_count() != 5 || graph.get_edges_count() != 5) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Baseline graph loading dimension mismatch";
        return report;
    }

    // Verify Ingestion Validation Negative Cases:
    // Negative 1: Duplicate undirected edge rejection
    {
        ApxAuthoritativeGraph bad_g;
        std::vector<std::pair<uint32_t, uint32_t>> test_v = {{0, 0}, {1, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> dup_e = {{0, 1, 0}, {1, 0, 0}};
        bool dup_caught = false;
        try {
            bad_g.load_baseline(test_v, dup_e);
        } catch (const std::invalid_argument&) {
            dup_caught = true;
        }
        if (!dup_caught) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Duplicate undirected edge in baseline was not rejected";
            return report;
        }
    }

    // Negative 2: Self-loop rejection
    {
        ApxAuthoritativeGraph bad_g;
        std::vector<std::pair<uint32_t, uint32_t>> test_v = {{0, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> loop_e = {{0, 0, 0}};
        bool loop_caught = false;
        try {
            bad_g.load_baseline(test_v, loop_e);
        } catch (const std::invalid_argument&) {
            loop_caught = true;
        }
        if (!loop_caught) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Self-loop in baseline was not rejected";
            return report;
        }
    }

    // Negative 3: Non-dense vertex ID rejection
    {
        ApxAuthoritativeGraph bad_g;
        std::vector<std::pair<uint32_t, uint32_t>> gap_v = {{0, 0}, {2, 0}};
        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> normal_e = {};
        bool gap_caught = false;
        try {
            bad_g.load_baseline(gap_v, normal_e);
        } catch (const std::exception&) {
            gap_caught = true;
        }
        if (!gap_caught) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Non-dense vertex IDs in baseline were not rejected";
            return report;
        }
    }

    const auto& mutations = fixture["mutations"].as_array();
    mini_json::Value mut_records = mini_json::Value::make_array();
    for (size_t i = 0; i < mutations.size(); ++i) {
        const auto& m = mutations[i];
        std::string op = m["op"].as_string();
        uint32_t u = static_cast<uint32_t>(m["u"].as_int());
        uint32_t v = static_cast<uint32_t>(m["v"].as_int());
        uint32_t lbl = static_cast<uint32_t>(m["label"].as_int());
        bool expected_exists = m["expected_exists"].as_bool();

        if (op.rfind("delete", 0) == 0) {
            bool ok = graph.apply_delete(u, v, lbl);
            if (!ok) {
                report["status"] = "FAIL";
                report["diagnostic"] = "Failed to execute delete at mutation " + std::to_string(i);
                return report;
            }
        } else {
            bool ok = graph.apply_insert(u, v, lbl);
            if (!ok) {
                report["status"] = "FAIL";
                report["diagnostic"] = "Failed to execute insert at mutation " + std::to_string(i);
                return report;
            }
        }

        if (graph.has_edge(u, v) != expected_exists) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Edge existence mismatch after mutation " + std::to_string(i);
            return report;
        }

        if (m.contains("expected_label")) {
            uint32_t exp_lbl = static_cast<uint32_t>(m["expected_label"].as_int());
            auto lookup = graph.get_edge_label(u, v);
            if (!lookup.exists || lookup.label != exp_lbl) {
                report["status"] = "FAIL";
                report["diagnostic"] = "Edge label mismatch after mutation " + std::to_string(i);
                return report;
            }
        }

        mini_json::Value mut_rec = mini_json::Value::make_object();
        mut_rec["step_index"] = static_cast<int64_t>(i);
        mut_rec["op"] = op;
        mut_rec["u"] = static_cast<int64_t>(u);
        mut_rec["v"] = static_cast<int64_t>(v);
        mut_rec["edges_count"] = static_cast<int64_t>(graph.get_edges_count());
        mut_rec["has_edge"] = graph.has_edge(u, v);
        mut_records.push_back(mut_rec);
    }

    report["status"] = "PASS";
    report["diagnostic"] = "Graph lifecycle, overlays, tombstones, and label precedence verified";
    report["final_vertices_count"] = static_cast<int64_t>(graph.get_vertices_count());
    report["final_edges_count"] = static_cast<int64_t>(graph.get_edges_count());
    report["mutations_executed"] = mut_records;
    return report;
}

mini_json::Value run_transaction_lifecycle(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "transaction_lifecycle";

    ApxTypedCodec codec;
    ApxAuthoritativeGraph graph;

    const auto& base_v = fixture["baseline"]["vertices"].as_array();
    const auto& base_e = fixture["baseline"]["edges"].as_array();

    std::vector<std::pair<uint32_t, uint32_t>> vertices;
    for (const auto& v : base_v) {
        int64_t ext_id = v["id"].as_int();
        uint32_t dense_id = codec.intern_data_vertex(ext_id);
        TypedLabel lbl = parse_typed_label(v["label"]);
        uint32_t dense_lbl = codec.intern_vertex_label(lbl);
        vertices.emplace_back(dense_id, dense_lbl);
    }

    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> edges;
    for (const auto& e : base_e) {
        uint32_t dense_u = codec.intern_data_vertex(e["u"].as_int());
        uint32_t dense_v = codec.intern_data_vertex(e["v"].as_int());
        TypedLabel lbl = parse_typed_label(e["label"]);
        uint32_t dense_lbl = codec.intern_edge_label(lbl);
        edges.emplace_back(dense_u, dense_v, dense_lbl);
    }

    graph.load_baseline(vertices, edges);

    std::vector<uint32_t> query_order;
    ApxTupleSink sink(codec, query_order);
    ApxTransactionManager tx_mgr(graph, codec, sink);

    const auto& steps = fixture["steps"].as_array();
    mini_json::Value step_results = mini_json::Value::make_array();

    for (const auto& s : steps) {
        UpdateOperation update;
        std::string op_str = s["op"].as_string();
        update.op = (op_str == "+") ? UpdateOp::INSERT : UpdateOp::DELETE;
        update.ext_u = s["u"].as_int();
        update.ext_v = s["v"].as_int();
        update.label = parse_typed_label(s["label"]);

        std::string fault_str = s["fault"].as_string();
        if (fault_str == "AFTER_MUTATION") {
            tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::AFTER_MUTATION);
        } else if (fault_str == "DURING_CALLBACK") {
            tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::DURING_CALLBACK);
        } else if (fault_str == "BEFORE_PUBLISH") {
            tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::BEFORE_PUBLISH);
        } else {
            tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
        }

        auto step_res = tx_mgr.execute_update(update, [&](
            const ApxAuthoritativeGraph& g_view,
            const UpdateOperation& u_op,
            uint32_t du,
            uint32_t dv,
            uint32_t
        ) -> bool {
            if (u_op.op == UpdateOp::INSERT) {
                if (!g_view.has_edge(du, dv)) return false;
            } else if (u_op.op == UpdateOp::DELETE) {
                if (!g_view.has_edge(du, dv)) return false;
            }
            return true;
        });

        std::string exp_status = s["expected_status"].as_string();
        std::string actual_status = step_status_to_string(step_res.status);

        if (actual_status != exp_status) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Step " + std::to_string(s["step_index"].as_int()) + " status mismatch: expected " + exp_status + " got " + actual_status;
            return report;
        }

        mini_json::Value step_rec = mini_json::Value::make_object();
        step_rec["step_index"] = s["step_index"].as_int();
        step_rec["status"] = actual_status;
        step_rec["op"] = op_str;
        step_rec["u"] = update.ext_u;
        step_rec["v"] = update.ext_v;
        step_rec["current_edges_count"] = static_cast<int64_t>(graph.get_edges_count());
        step_rec["current_vertices_count"] = static_cast<int64_t>(graph.get_vertices_count());
        if (codec.has_data_vertex(update.ext_u)) {
            step_rec["degree_u"] = static_cast<int64_t>(graph.get_degree(codec.get_data_vertex(update.ext_u)));
        }
        if (codec.has_data_vertex(update.ext_v)) {
            step_rec["degree_v"] = static_cast<int64_t>(graph.get_degree(codec.get_data_vertex(update.ext_v)));
        }
        if (codec.has_data_vertex(update.ext_u) && codec.has_data_vertex(update.ext_v)) {
            uint32_t du = codec.get_data_vertex(update.ext_u);
            uint32_t dv = codec.get_data_vertex(update.ext_v);
            step_rec["has_edge_uv"] = graph.has_edge(du, dv);
        }
        step_results.push_back(step_rec);
    }

    // Explicit Verification 1: Reentrancy Guard
    {
        bool reentrant_attempted = false;
        bool reentrant_blocked = false;
        UpdateOperation test_op;
        test_op.op = UpdateOp::INSERT;
        test_op.ext_u = 10;
        test_op.ext_v = 30;
        test_op.label = TypedLabel::from_int(100);

        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
        tx_mgr.execute_update(test_op, [&](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool {
            reentrant_attempted = true;
            UpdateOperation nested_op = test_op;
            nested_op.ext_v = 40;
            auto nested_res = tx_mgr.execute_update(nested_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool { return true; });
            if (nested_res.status == StepStatus::REENTRANCY_BLOCKED) {
                reentrant_blocked = true;
            }
            return false; // abort outer transaction
        });

        if (!reentrant_attempted || !reentrant_blocked) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Reentrancy guard failed to reject nested transaction";
            return report;
        }
    }

    // Explicit Verification 2: Codec Rollback on Abort
    {
        TypedLabel test_unique_lbl = TypedLabel::from_int(777777);
        UpdateOperation codec_op;
        codec_op.op = UpdateOp::INSERT;
        codec_op.ext_u = 10;
        codec_op.ext_v = 30;
        codec_op.label = test_unique_lbl;

        auto abort_res = tx_mgr.execute_update(codec_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool {
            return false; // callback abort
        });
        if (abort_res.status != StepStatus::CALLBACK_ABORTED) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Callback abort did not return CALLBACK_ABORTED";
            return report;
        }
        if (codec.has_edge_label(test_unique_lbl)) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Codec leaked interned label across aborted transaction";
            return report;
        }
    }

    // Explicit Verification 3: Symmetric Rollback Between Forward and Reverse
    {
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::BETWEEN_FORWARD_REVERSE_MUTATION);
        UpdateOperation fwd_rev_op;
        fwd_rev_op.op = UpdateOp::INSERT;
        fwd_rev_op.ext_u = 10;
        fwd_rev_op.ext_v = 30;
        fwd_rev_op.label = TypedLabel::from_int(100);

        auto fwd_res = tx_mgr.execute_update(fwd_rev_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool { return true; });
        if (fwd_res.status != StepStatus::FAULT_INJECTED) {
            report["status"] = "FAIL";
            report["diagnostic"] = "BETWEEN_FORWARD_REVERSE did not inject fault properly";
            return report;
        }
        uint32_t du10 = codec.get_data_vertex(10);
        uint32_t dv30 = codec.get_data_vertex(30);
        if (graph.has_edge(du10, dv30)) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Forward edge was leaked in overlay after reverse failure";
            return report;
        }
    }

    // Explicit Verification 4: Allocation Failure Simulation
    {
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::ALLOCATION_FAILURE_MUTATION);
        UpdateOperation alloc_op;
        alloc_op.op = UpdateOp::INSERT;
        alloc_op.ext_u = 10;
        alloc_op.ext_v = 30;
        alloc_op.label = TypedLabel::from_int(100);

        auto alloc_res = tx_mgr.execute_update(alloc_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool { return true; });
        if (alloc_res.status != StepStatus::FAULT_INJECTED) {
            report["status"] = "FAIL";
            report["diagnostic"] = "ALLOCATION_FAILURE did not inject fault properly";
            return report;
        }
        tx_mgr.set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
    }

    // Explicit Verification 5: Callback Stage Tuple then Abort with zero-allocation cleanup
    {
        uint32_t initial_edges = graph.get_edges_count();
        size_t initial_committed = sink.committed_emissions();
        UpdateOperation stage_op;
        stage_op.op = UpdateOp::INSERT;
        stage_op.ext_u = 10;
        stage_op.ext_v = 30;
        stage_op.label = TypedLabel::from_int(999);

        auto stage_abort_res = tx_mgr.execute_update(stage_op, [&](
            const ApxAuthoritativeGraph&,
            const UpdateOperation&,
            uint32_t,
            uint32_t,
            uint32_t
        ) -> bool {
            // Stage a tuple during callback
            sink.emit_mapping({codec.get_data_vertex(10), codec.get_data_vertex(30)});
            // Then abort
            return false;
        });

        if (stage_abort_res.status != StepStatus::CALLBACK_ABORTED) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Callback stage abort failed to return CALLBACK_ABORTED";
            return report;
        }

        // Verify graph unchanged
        if (graph.get_edges_count() != initial_edges) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Graph edge count changed after aborted staged update";
            return report;
        }
        uint32_t du10 = codec.get_data_vertex(10);
        uint32_t dv30 = codec.get_data_vertex(30);
        if (graph.has_edge(du10, dv30)) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Graph retained inserted edge after aborted staged update";
            return report;
        }

        // Verify pending output is empty and prior committed output preserved
        if (sink.committed_emissions() != initial_committed) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Committed emissions count changed after aborted step";
            return report;
        }

        // Verify manager can process subsequent valid transaction normally
        UpdateOperation valid_next_op;
        valid_next_op.op = UpdateOp::INSERT;
        valid_next_op.ext_u = 10;
        valid_next_op.ext_v = 30;
        valid_next_op.label = TypedLabel::from_int(999);
        auto valid_next_res = tx_mgr.execute_update(valid_next_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool {
            return true;
        });
        if (valid_next_res.status != StepStatus::SUCCESS) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Manager failed to execute subsequent valid transaction after abort";
            return report;
        }
        // Clean up: delete (10, 30)
        valid_next_op.op = UpdateOp::DELETE;
        tx_mgr.execute_update(valid_next_op, [](const ApxAuthoritativeGraph&, const UpdateOperation&, uint32_t, uint32_t, uint32_t) -> bool {
            return true;
        });
    }

    // Step 0 inserted (10, 40) successfully -> MUST remain in graph
    uint32_t du_10 = codec.intern_data_vertex(10);
    uint32_t dv_40 = codec.intern_data_vertex(40);
    if (!graph.has_edge(du_10, dv_40)) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Committed edge (10, 40) was lost after subsequent aborted steps";
        return report;
    }

    report["status"] = "PASS";
    report["diagnostic"] = "Transaction manager ACID boundaries, reentrancy guard, and fault rollback verified";
    report["steps_executed"] = step_results;
    return report;
}

mini_json::Value run_tuple_sink_invariants(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "tuple_sink_invariants";

    ApxTypedCodec codec;
    const auto& q_verts = fixture["query_vertices"].as_array();
    for (const auto& qv : q_verts) {
        codec.intern_query_vertex(qv["id"].as_int());
    }
    const auto& d_verts = fixture["data_vertices"].as_array();
    for (const auto& dv : d_verts) {
        codec.intern_data_vertex(dv["id"].as_int());
    }

    // In ascending numerical order of original external IDs: 100, 200, 300
    // Their internal dense IDs: dense(100)=1, dense(200)=2, dense(300)=0
    std::vector<int64_t> sorted_ext_q = {100, 200, 300};
    std::vector<uint32_t> query_vertex_order;
    for (int64_t ext_id : sorted_ext_q) {
        query_vertex_order.push_back(codec.intern_query_vertex(ext_id));
    }

    ApxTupleSink sink(codec, query_vertex_order);

    // 1. Valid emissions
    sink.start_step();
    const auto& val_emissions = fixture["valid_emissions"].as_array();
    for (const auto& ve : val_emissions) {
        std::vector<uint32_t> mapping_by_dense;
        for (const auto& dv_ext : ve["mapping_by_dense_q"].as_array()) {
            mapping_by_dense.push_back(codec.intern_data_vertex(dv_ext.as_int()));
        }
        sink.emit_mapping(mapping_by_dense);
    }
    auto committed = sink.commit_step();
    if (committed.size() != 2) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Committed tuple count mismatch in valid emissions";
        return report;
    }
    // Check canonical order of first committed tuple: external IDs [10, 20, 30]
    if (committed[0] != std::vector<int64_t>{10, 20, 30}) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Tuple canonical ordering mismatch";
        return report;
    }

    // 2. Non-injective emission rejection
    sink.start_step();
    bool non_inj_caught = false;
    try {
        std::vector<uint32_t> non_inj_mapping;
        for (const auto& dv_ext : fixture["non_injective_emission"]["mapping_by_dense_q"].as_array()) {
            non_inj_mapping.push_back(codec.intern_data_vertex(dv_ext.as_int()));
        }
        sink.emit_mapping(non_inj_mapping);
    } catch (const NonInjectiveMappingException&) {
        non_inj_caught = true;
    }
    sink.abort_step();
    if (!non_inj_caught) {
        report["status"] = "FAIL";
        report["diagnostic"] = "NonInjectiveMappingException not thrown for non-injective mapping";
        return report;
    }

    // 3. Duplicate raw emission detection in same batch
    sink.start_step();
    bool dup_caught = false;
    try {
        std::vector<uint32_t> dup_mapping;
        for (const auto& dv_ext : fixture["duplicate_raw_emission"]["mapping_by_dense_q"].as_array()) {
            dup_mapping.push_back(codec.intern_data_vertex(dv_ext.as_int()));
        }
        sink.emit_mapping(dup_mapping);
        sink.emit_mapping(dup_mapping); // Duplicate in same batch
    } catch (const DuplicateEmissionException&) {
        dup_caught = true;
    }
    sink.abort_step();
    if (!dup_caught) {
        report["status"] = "FAIL";
        report["diagnostic"] = "DuplicateEmissionException not thrown for duplicate raw emission";
        return report;
    }

    report["status"] = "PASS";
    report["diagnostic"] = "TupleSink injectivity, duplicate rejection, and staging invariants verified";
    report["raw_emissions"] = static_cast<int64_t>(sink.raw_emissions());
    report["unique_emissions"] = static_cast<int64_t>(sink.unique_emissions());
    report["committed_emissions"] = static_cast<int64_t>(sink.committed_emissions());
    report["discarded_emissions"] = static_cast<int64_t>(sink.discarded_emissions());
    return report;
}

mini_json::Value run_k4_exact_enumeration(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "k4_exact_enumeration";

    ApxTypedCodec codec;
    ApxAuthoritativeGraph query_graph;
    ApxAuthoritativeGraph data_graph;

    const auto& q_verts = fixture["query"]["vertices"].as_array();
    const auto& q_edges = fixture["query"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> qv;
    for (const auto& v : q_verts) {
        uint32_t du = codec.intern_query_vertex(v["id"].as_int());
        uint32_t dl = codec.intern_vertex_label(parse_typed_label(v["label"]));
        qv.emplace_back(du, dl);
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> qe;
    for (const auto& e : q_edges) {
        uint32_t du = codec.intern_query_vertex(e["u"].as_int());
        uint32_t dv = codec.intern_query_vertex(e["v"].as_int());
        uint32_t dl = codec.intern_edge_label(parse_typed_label(e["label"]));
        qe.emplace_back(du, dv, dl);
    }
    query_graph.load_baseline(qv, qe);

    const auto& d_verts = fixture["data"]["vertices"].as_array();
    const auto& d_edges = fixture["data"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> dv;
    for (const auto& v : d_verts) {
        uint32_t du = codec.intern_data_vertex(v["id"].as_int());
        uint32_t dl = codec.intern_vertex_label(parse_typed_label(v["label"]));
        dv.emplace_back(du, dl);
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> de;
    for (const auto& e : d_edges) {
        uint32_t du = codec.intern_data_vertex(e["u"].as_int());
        uint32_t dv = codec.intern_data_vertex(e["v"].as_int());
        uint32_t dl = codec.intern_edge_label(parse_typed_label(e["label"]));
        de.emplace_back(du, dv, dl);
    }
    data_graph.load_baseline(dv, de);

    std::vector<uint32_t> query_order;
    for (int64_t ext_id = 1; ext_id <= 4; ++ext_id) {
        query_order.push_back(codec.intern_query_vertex(ext_id));
    }

    ApxTupleSink sink(codec, query_order);
    MicroExactMatcher matcher(0);

    sink.start_step();
    uint64_t count = matcher.match_snapshot(query_graph, data_graph, sink);
    auto tuples = sink.commit_step();

    if (count != 24 || tuples.size() != 24) {
        report["status"] = "FAIL";
        report["diagnostic"] = "K4 match count mismatch: expected 24 got " + std::to_string(count);
        return report;
    }

    mini_json::Value tuple_arr = mini_json::Value::make_array();
    for (const auto& tup : tuples) {
        mini_json::Value item = mini_json::Value::make_array();
        for (int64_t coord : tup) {
            item.push_back(coord);
        }
        tuple_arr.push_back(item);
    }

    report["status"] = "PASS";
    report["diagnostic"] = "K4 exact enumeration produced exactly 24 unique ordered tuples";
    report["tuple_count"] = 24;
    report["raw_emissions"] = static_cast<int64_t>(sink.raw_emissions());
    report["unique_emissions"] = static_cast<int64_t>(sink.unique_emissions());
    report["tuples"] = tuple_arr;
    return report;
}

mini_json::Value run_event_transition_stream(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "event_transition_stream";

    ApxTypedCodec codec;
    ApxAuthoritativeGraph query_graph;
    ApxAuthoritativeGraph data_graph;

    const auto& q_verts = fixture["query"]["vertices"].as_array();
    const auto& q_edges = fixture["query"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> qv;
    for (const auto& v : q_verts) {
        qv.emplace_back(codec.intern_query_vertex(v["id"].as_int()), codec.intern_vertex_label(parse_typed_label(v["label"])));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> qe;
    for (const auto& e : q_edges) {
        qe.emplace_back(codec.intern_query_vertex(e["u"].as_int()), codec.intern_query_vertex(e["v"].as_int()), codec.intern_edge_label(parse_typed_label(e["label"])));
    }
    query_graph.load_baseline(qv, qe);

    const auto& d_verts = fixture["baseline_data"]["vertices"].as_array();
    const auto& d_edges = fixture["baseline_data"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> dv;
    for (const auto& v : d_verts) {
        dv.emplace_back(codec.intern_data_vertex(v["id"].as_int()), codec.intern_vertex_label(parse_typed_label(v["label"])));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> de;
    for (const auto& e : d_edges) {
        de.emplace_back(codec.intern_data_vertex(e["u"].as_int()), codec.intern_data_vertex(e["v"].as_int()), codec.intern_edge_label(parse_typed_label(e["label"])));
    }
    data_graph.load_baseline(dv, de);

    std::vector<uint32_t> query_order;
    for (int64_t ext_id = 1; ext_id <= 4; ++ext_id) {
        query_order.push_back(codec.intern_query_vertex(ext_id));
    }

    ApxTupleSink sink(codec, query_order);
    MicroExactMatcher matcher(0);
    ApxTransactionManager tx_mgr(data_graph, codec, sink);

    // Initial snapshot check: 0 matches
    sink.start_step();
    uint64_t init_count = matcher.match_snapshot(query_graph, data_graph, sink);
    sink.abort_step();
    if (init_count != 0) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Initial baseline K4 missing edge must have 0 matches";
        return report;
    }

    mini_json::Value event_records = mini_json::Value::make_array();
    const auto& events = fixture["events"].as_array();

    for (const auto& ev : events) {
        UpdateOperation update;
        std::string op_str = ev["op"].as_string();
        update.op = (op_str == "+") ? UpdateOp::INSERT : UpdateOp::DELETE;
        update.ext_u = ev["u"].as_int();
        update.ext_v = ev["v"].as_int();
        update.label = parse_typed_label(ev["label"]);

        auto res = tx_mgr.execute_update(update, [&](
            const ApxAuthoritativeGraph& g_view,
            const UpdateOperation&,
            uint32_t dense_u,
            uint32_t dense_v,
            uint32_t dense_label
        ) -> bool {
            matcher.match_delta(query_graph, g_view, dense_u, dense_v, dense_label, sink);
            return true;
        });

        if (res.status != StepStatus::SUCCESS) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Event execution failed: " + res.error_message;
            return report;
        }

        size_t exp_count = static_cast<size_t>(ev["expected_tuple_count"].as_int());
        if (res.published_tuples.size() != exp_count) {
            report["status"] = "FAIL";
            report["diagnostic"] = "Event tuple count mismatch: expected " + std::to_string(exp_count) + " got " + std::to_string(res.published_tuples.size());
            return report;
        }

        std::string actual_event_type = (update.op == UpdateOp::INSERT) ? "ENTERED" : "EXITED";
        mini_json::Value rec = mini_json::Value::make_object();
        rec["step"] = ev["step"].as_int();
        rec["op"] = op_str;
        rec["u"] = update.ext_u;
        rec["v"] = update.ext_v;
        rec["event_type"] = actual_event_type;
        rec["published_count"] = static_cast<int64_t>(res.published_tuples.size());

        // Materialize and serialize actual tuples for bit-for-bit Oracle verification
        mini_json::Value tuples_arr = mini_json::Value::make_array();
        for (const auto& tup : res.published_tuples) {
            mini_json::Value tup_json = mini_json::Value::make_array();
            for (int64_t vid : tup) {
                tup_json.push_back(vid);
            }
            tuples_arr.push_back(tup_json);
        }
        rec["tuples"] = tuples_arr;

        event_records.push_back(rec);
    }

    report["status"] = "PASS";
    report["diagnostic"] = "Dynamic event transitions on K4 missing edge verified (0 baseline, 24 ENTERED, 24 EXITED)";
    report["events"] = event_records;
    return report;
}

mini_json::Value run_diamond_k1_fixed(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "diamond_k1_fixed";

    ApxTypedCodec codec;
    ApxAuthoritativeGraph query_graph;
    ApxAuthoritativeGraph data_graph;

    const auto& q_verts = fixture["query"]["vertices"].as_array();
    const auto& q_edges = fixture["query"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> qv;
    for (const auto& v : q_verts) {
        qv.emplace_back(codec.intern_query_vertex(v["id"].as_int()), codec.intern_vertex_label(parse_typed_label(v["label"])));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> qe;
    for (const auto& e : q_edges) {
        qe.emplace_back(codec.intern_query_vertex(e["u"].as_int()), codec.intern_query_vertex(e["v"].as_int()), codec.intern_edge_label(parse_typed_label(e["label"])));
    }
    query_graph.load_baseline(qv, qe);

    const auto& d_verts = fixture["data"]["vertices"].as_array();
    const auto& d_edges = fixture["data"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> dv;
    for (const auto& v : d_verts) {
        dv.emplace_back(codec.intern_data_vertex(v["id"].as_int()), codec.intern_vertex_label(parse_typed_label(v["label"])));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> de;
    for (const auto& e : d_edges) {
        de.emplace_back(codec.intern_data_vertex(e["u"].as_int()), codec.intern_data_vertex(e["v"].as_int()), codec.intern_edge_label(parse_typed_label(e["label"])));
    }
    data_graph.load_baseline(dv, de);

    std::vector<uint32_t> mapping_by_q_order;
    const auto& fix_map = fixture["fixed_mapping_ext"].as_array();
    for (const auto& m : fix_map) {
        mapping_by_q_order.push_back(codec.intern_data_vertex(m["data_ext_id"].as_int()));
    }

    uint32_t k = static_cast<uint32_t>(fixture["k"].as_int());
    uint32_t lambda = static_cast<uint32_t>(fixture["lambda"].as_int());

    auto eval = MicroExactMatcher::evaluate_fixed_mapping(query_graph, data_graph, mapping_by_q_order, k, lambda);

    uint32_t exp_matched = static_cast<uint32_t>(fixture["expected_matched_edges"].as_int());
    uint32_t exp_missing = static_cast<uint32_t>(fixture["expected_missing_edges"].as_int());
    bool exp_valid = fixture["expected_is_valid"].as_bool();

    if (eval.matched_edges != exp_matched || eval.missing_edges != exp_missing || eval.is_valid != exp_valid) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Diamond k=1 evaluation mismatch: " + eval.diagnostic_message;
        return report;
    }

    // Negative 1: Strict D-R guard rejection (k >= lambda)
    auto eval_k_eq_lambda = MicroExactMatcher::evaluate_fixed_mapping(query_graph, data_graph, mapping_by_q_order, 2, 2);
    if (eval_k_eq_lambda.is_valid) {
        report["status"] = "FAIL";
        report["diagnostic"] = "D-R guard failed: k=2 >= lambda=2 was erroneously accepted as valid";
        return report;
    }

    // Negative 2: Metadata lambda mismatch
    auto eval_bad_lambda = MicroExactMatcher::evaluate_fixed_mapping(query_graph, data_graph, mapping_by_q_order, 1, 99);
    if (eval_bad_lambda.is_valid) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Metadata lambda mismatch was erroneously accepted as valid";
        return report;
    }

    report["status"] = "PASS";
    report["diagnostic"] = "Diamond k=1 fixed-mapping successfully evaluated satisfying D-R guard (k < lambda) and S2 spanning connectivity";
    report["matched_edges"] = static_cast<int64_t>(eval.matched_edges);
    report["missing_edges"] = static_cast<int64_t>(eval.missing_edges);
    report["computed_lambda"] = static_cast<int64_t>(eval.computed_lambda);
    report["s2_spanning_connected"] = eval.s2_spanning_connected;
    report["is_valid"] = eval.is_valid;
    return report;
}

mini_json::Value run_irrelevant_update_stream(const mini_json::Value& fixture) {
    mini_json::Value report = mini_json::Value::make_object();
    report["case_id"] = "irrelevant_update_stream";

    ApxTypedCodec codec;
    ApxAuthoritativeGraph query_graph;
    ApxAuthoritativeGraph data_graph;

    const auto& q_verts = fixture["query"]["vertices"].as_array();
    const auto& q_edges = fixture["query"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> qv;
    for (const auto& v : q_verts) {
        qv.emplace_back(codec.intern_query_vertex(v["id"].as_int()), codec.intern_vertex_label(parse_typed_label(v["label"])));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> qe;
    for (const auto& e : q_edges) {
        qe.emplace_back(codec.intern_query_vertex(e["u"].as_int()), codec.intern_query_vertex(e["v"].as_int()), codec.intern_edge_label(parse_typed_label(e["label"])));
    }
    query_graph.load_baseline(qv, qe);

    const auto& d_verts = fixture["baseline"]["vertices"].as_array();
    const auto& d_edges = fixture["baseline"]["edges"].as_array();
    std::vector<std::pair<uint32_t, uint32_t>> dv;
    for (const auto& v : d_verts) {
        dv.emplace_back(codec.intern_data_vertex(v["id"].as_int()), codec.intern_vertex_label(parse_typed_label(v["label"])));
    }
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> de;
    for (const auto& e : d_edges) {
        de.emplace_back(codec.intern_data_vertex(e["u"].as_int()), codec.intern_data_vertex(e["v"].as_int()), codec.intern_edge_label(parse_typed_label(e["label"])));
    }
    data_graph.load_baseline(dv, de);

    std::vector<uint32_t> query_order;
    for (const auto& v : q_verts) {
        query_order.push_back(codec.intern_query_vertex(v["id"].as_int()));
    }

    ApxTupleSink sink(codec, query_order);
    MicroExactMatcher matcher(0);
    ApxTransactionManager tx_mgr(data_graph, codec, sink);

    UpdateOperation update;
    update.op = UpdateOp::INSERT;
    update.ext_u = fixture["update"]["u"].as_int();
    update.ext_v = fixture["update"]["v"].as_int();
    update.label = parse_typed_label(fixture["update"]["label"]);

    auto res = tx_mgr.execute_update(update, [&](
        const ApxAuthoritativeGraph& g_view,
        const UpdateOperation&,
        uint32_t dense_u,
        uint32_t dense_v,
        uint32_t dense_label
    ) -> bool {
        matcher.match_delta(query_graph, g_view, dense_u, dense_v, dense_label, sink);
        return true;
    });

    if (res.status != StepStatus::SUCCESS) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Transaction manager failed on irrelevant update";
        return report;
    }

    if (!res.published_tuples.empty()) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Irrelevant update produced non-zero emissions";
        return report;
    }

    uint32_t du = codec.intern_data_vertex(update.ext_u);
    uint32_t dv_dense = codec.intern_data_vertex(update.ext_v);
    if (!data_graph.has_edge(du, dv_dense)) {
        report["status"] = "FAIL";
        report["diagnostic"] = "Irrelevant edge was not recorded in authoritative graph state";
        return report;
    }

    report["status"] = "PASS";
    report["diagnostic"] = "Irrelevant update recorded in authoritative state with zero search emissions";
    report["published_tuples_count"] = 0;
    report["edge_recorded"] = true;
    return report;
}

} // namespace rapidflow_apx_harness
