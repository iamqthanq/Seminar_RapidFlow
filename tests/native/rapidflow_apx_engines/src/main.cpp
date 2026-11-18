#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <memory>
#include <chrono>
#include <cstring>
#include <sys/resource.h>
#include <unistd.h>

#include "rapidflow_apx/json_mini.h"
#include "rapidflow_apx/typed_codec.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/streaming_engine.h"
#include "rapidflow_apx/engine_counters.h"

using namespace rapidflow_apx;

static TypedLabel parse_typed_label(const json::Value& v) {
    if (v.is_int()) {
        return TypedLabel::from_int(v.as_int());
    } else if (v.is_string()) {
        return TypedLabel::from_string(v.as_string());
    } else if (v.is_bool()) {
        // Distinguish bool from int
        return TypedLabel::from_string(v.as_bool() ? "true" : "false");
    } else {
        throw std::runtime_error("Invalid label type in JSON: must be int or string");
    }
}

static json::Value typed_label_to_json(const TypedLabel& lbl) {
    if (lbl.type == LabelType::INT64) {
        return json::Value(lbl.int_val);
    } else {
        return json::Value(lbl.str_val);
    }
}

static void apply_process_caps() {
    // 512 MiB address space cap
    struct rlimit rl;
    rl.rlim_cur = 512ULL * 1024ULL * 1024ULL;
    rl.rlim_max = 512ULL * 1024ULL * 1024ULL;
    setrlimit(RLIMIT_AS, &rl);

    // 30 seconds CPU time cap
    alarm(30);
}

int main(int argc, char* argv[]) {
    std::string engine_str = "m2";
    std::string workload_path = "";
    std::string output_path = "";
    uint64_t max_work = 500000;
    bool enable_caps = true;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--engine" && i + 1 < argc) {
            engine_str = argv[++i];
        } else if (arg == "--workload" && i + 1 < argc) {
            workload_path = argv[++i];
        } else if (arg == "--output" && i + 1 < argc) {
            output_path = argv[++i];
        } else if (arg == "--max-work" && i + 1 < argc) {
            max_work = std::stoull(argv[++i]);
        } else if (arg == "--no-caps") {
            enable_caps = false;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json> [--max-work <N>]\n";
            return 0;
        }
    }

    if (workload_path.empty() || output_path.empty()) {
        std::cerr << "Error: --workload and --output are required.\n";
        return 1;
    }

    EngineType engine_type = EngineType::M2;
    if (engine_str == "m1") {
        engine_type = EngineType::M1;
    } else if (engine_str == "m2") {
        engine_type = EngineType::M2;
    } else {
        std::cerr << "Error: Unknown engine '" << engine_str << "'. Must be 'm1' or 'm2'.\n";
        return 1;
    }

    if (enable_caps) {
        apply_process_caps();
    }

    // Read input file
    std::ifstream in_file(workload_path);
    if (!in_file.is_open()) {
        std::cerr << "Error: Cannot open workload file: " << workload_path << "\n";
        return 2;
    }
    std::stringstream buffer;
    buffer << in_file.rdbuf();
    std::string input_src = buffer.str();
    in_file.close();

    json::Value root_out = json::Value::make_object();
    root_out["engine"] = json::Value(engine_str);
    root_out["status"] = json::Value("FAIL");
    root_out["error_message"] = json::Value("");

    try {
        json::Value root_in = json::Parser::parse(input_src);

        std::string case_id = root_in.contains("case_id") ? root_in["case_id"].as_string() : "unknown";
        root_out["case_id"] = json::Value(case_id);

        uint32_t k = 0;
        if (root_in.contains("k")) {
            k = static_cast<uint32_t>(root_in["k"].as_int());
        }

        if (root_in.contains("max_work")) {
            max_work = static_cast<uint64_t>(root_in["max_work"].as_int());
        }

        // 1. Initialize Codec
        ApxTypedCodec codec;

        // Parse Query
        const auto& q_obj = root_in["query"];
        const auto& q_v_arr = q_obj["vertices"].as_array();
        const auto& q_e_arr = q_obj["edges"].as_array();

        std::vector<int64_t> q_ext_vids;
        std::vector<TypedLabel> q_v_labels;
        for (const auto& v_val : q_v_arr) {
            int64_t ext_v = v_val["id"].as_int();
            TypedLabel lbl = parse_typed_label(v_val["label"]);
            q_ext_vids.push_back(ext_v);
            q_v_labels.push_back(lbl);
            codec.intern_query_vertex(ext_v);
            codec.intern_vertex_label(lbl);
        }

        struct ExtEdge {
            int64_t u;
            int64_t v;
            TypedLabel label;
        };
        std::vector<ExtEdge> q_ext_edges;
        for (const auto& e_val : q_e_arr) {
            int64_t u = e_val["u"].as_int();
            int64_t v = e_val["v"].as_int();
            TypedLabel lbl = parse_typed_label(e_val["label"]);
            q_ext_edges.push_back({u, v, lbl});
            codec.intern_edge_label(lbl);
        }

        // Parse Initial Data Graph
        const auto& g_obj = root_in["initial_graph"];
        const auto& g_v_arr = g_obj["vertices"].as_array();
        const auto& g_e_arr = g_obj["edges"].as_array();

        std::vector<int64_t> g_ext_vids;
        std::vector<TypedLabel> g_v_labels;
        for (const auto& v_val : g_v_arr) {
            int64_t ext_v = v_val["id"].as_int();
            TypedLabel lbl = parse_typed_label(v_val["label"]);
            g_ext_vids.push_back(ext_v);
            g_v_labels.push_back(lbl);
            codec.intern_data_vertex(ext_v);
            codec.intern_vertex_label(lbl);
        }

        std::vector<ExtEdge> g_ext_edges;
        for (const auto& e_val : g_e_arr) {
            int64_t u = e_val["u"].as_int();
            int64_t v = e_val["v"].as_int();
            TypedLabel lbl = parse_typed_label(e_val["label"]);
            g_ext_edges.push_back({u, v, lbl});
            codec.intern_edge_label(lbl);
        }

        // 2. Build Authoritative Baseline Graph
        ApxAuthoritativeGraph graph;
        std::vector<std::pair<uint32_t, uint32_t>> base_vertices;
        for (size_t i = 0; i < g_ext_vids.size(); ++i) {
            uint32_t dense_u = codec.get_data_vertex(g_ext_vids[i]);
            uint32_t dense_lbl = codec.get_vertex_label_id(g_v_labels[i]);
            base_vertices.emplace_back(dense_u, dense_lbl);
        }

        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> base_edges;
        for (const auto& e : g_ext_edges) {
            uint32_t dense_u = codec.get_data_vertex(e.u);
            uint32_t dense_v = codec.get_data_vertex(e.v);
            uint32_t dense_lbl = codec.get_edge_label_id(e.label);
            base_edges.emplace_back(dense_u, dense_v, dense_lbl);
        }
        graph.load_baseline(base_vertices, base_edges);

        // 3. Build Query Plan
        uint32_t num_q = static_cast<uint32_t>(q_ext_vids.size());
        std::vector<uint32_t> q_dense_vlabels(num_q, 0);
        for (size_t i = 0; i < q_ext_vids.size(); ++i) {
            uint32_t dense_u = codec.get_query_vertex(q_ext_vids[i]);
            q_dense_vlabels[dense_u] = codec.get_vertex_label_id(q_v_labels[i]);
        }

        std::vector<QueryEdge> q_dense_edges;
        for (const auto& e : q_ext_edges) {
            uint32_t dense_u = codec.get_query_vertex(e.u);
            uint32_t dense_v = codec.get_query_vertex(e.v);
            uint32_t dense_lbl = codec.get_edge_label_id(e.label);
            q_dense_edges.push_back({dense_u, dense_v, dense_lbl});
        }

        std::vector<uint32_t> canonical_order = codec.get_query_canonical_order();

        ApxQueryPlan query_plan;
        query_plan.build(num_q, q_dense_vlabels, q_dense_edges, canonical_order, k);

        // 4. Initialize Sink and Streaming Engine
        ApxTupleSink sink(codec, canonical_order);
        ApxStreamingEngine streaming_engine(graph, codec, sink, query_plan, engine_type, max_work);

        // Record Query Info
        json::Value q_info = json::Value::make_object();
        q_info["vertices_count"] = json::Value(query_plan.get_vertices_count());
        q_info["edges_count"] = json::Value(query_plan.get_edges_count());
        q_info["computed_lambda"] = json::Value(query_plan.get_lambda());
        q_info["k"] = json::Value(query_plan.get_k());

        json::Value can_order_arr = json::Value::make_array();
        for (uint32_t dense_u : canonical_order) {
            can_order_arr.push_back(json::Value(codec.decode_query_vertex(dense_u)));
        }
        q_info["query_canonical_order"] = can_order_arr;
        root_out["query_info"] = q_info;

        // 5. Execute Updates
        const auto& upd_arr = root_in["updates"].as_array();
        json::Value steps_arr = json::Value::make_array();
        bool any_failure = false;

        for (size_t step_idx = 0; step_idx < upd_arr.size(); ++step_idx) {
            const auto& upd_val = upd_arr[step_idx];
            std::string op_str = upd_val["op"].as_string();
            UpdateOp op = (op_str == "+" || op_str == "INSERT" || op_str == "INS") ? UpdateOp::INSERT : UpdateOp::DELETE;
            int64_t ext_u = upd_val["u"].as_int();
            int64_t ext_v = upd_val["v"].as_int();
            TypedLabel lbl = parse_typed_label(upd_val["label"]);

            UpdateOperation upd{op, ext_u, ext_v, lbl};
            StreamStepResult step_res = streaming_engine.execute_step(upd, static_cast<uint32_t>(step_idx));

            json::Value step_json = json::Value::make_object();
            step_json["step_index"] = json::Value(step_res.step_index);
            step_json["op"] = json::Value(op == UpdateOp::INSERT ? "+" : "-");
            step_json["u"] = json::Value(ext_u);
            step_json["v"] = json::Value(ext_v);
            step_json["label"] = typed_label_to_json(lbl);
            step_json["status"] = json::Value(step_status_to_string(step_res.status));
            step_json["error"] = json::Value(step_res.error_message);

            json::Value ent_arr = json::Value::make_array();
            for (const auto& tup : step_res.entered_tuples) {
                json::Value t_arr = json::Value::make_array();
                for (int64_t coord : tup) t_arr.push_back(json::Value(coord));
                ent_arr.push_back(t_arr);
            }
            step_json["entered_tuples"] = ent_arr;

            json::Value exit_arr = json::Value::make_array();
            for (const auto& tup : step_res.exited_tuples) {
                json::Value t_arr = json::Value::make_array();
                for (int64_t coord : tup) t_arr.push_back(json::Value(coord));
                exit_arr.push_back(t_arr);
            }
            step_json["exited_tuples"] = exit_arr;

            // Codec state
            json::Value c_state = json::Value::make_object();
            c_state["query_vertices_count"] = json::Value(codec.query_vertices_count());
            c_state["data_vertices_count"] = json::Value(codec.data_vertices_count());
            c_state["vertex_labels_count"] = json::Value(codec.vertex_labels_count());
            c_state["edge_labels_count"] = json::Value(codec.edge_labels_count());
            step_json["codec_state"] = c_state;

            // Graph state
            json::Value g_state = json::Value::make_object();
            g_state["vertices_count"] = json::Value(graph.get_vertices_count());
            g_state["edges_count"] = json::Value(graph.get_edges_count());
            step_json["graph_state"] = g_state;

            steps_arr.push_back(step_json);

            if (step_res.status != StepStatus::SUCCESS) {
                any_failure = true;
                break; // Stop after first failing step, preserve prior steps
            }
        }

        root_out["steps"] = steps_arr;

        // Counters
        const auto& counters = streaming_engine.get_counters();
        json::Value cnt_json = json::Value::make_object();
        cnt_json["anchors_started"] = json::Value(counters.anchors_started);
        cnt_json["dfs_states"] = json::Value(counters.dfs_states);
        cnt_json["candidate_evaluations"] = json::Value(counters.candidate_evaluations);
        cnt_json["candidate_extensions"] = json::Value(counters.candidate_extensions);
        cnt_json["edge_lookups"] = json::Value(counters.edge_lookups);
        cnt_json["vertex_label_checks"] = json::Value(counters.vertex_label_checks);
        cnt_json["raw_emissions"] = json::Value(sink.raw_emissions());
        cnt_json["unique_emissions"] = json::Value(sink.unique_emissions());
        cnt_json["committed_emissions"] = json::Value(sink.committed_emissions());
        cnt_json["discarded_emissions"] = json::Value(sink.discarded_emissions());
        cnt_json["work_units"] = json::Value(counters.work_units);
        cnt_json["max_work"] = json::Value(counters.max_work);

        if (engine_type == EngineType::M1) {
            cnt_json["variants_started"] = json::Value(counters.variants_started);
            cnt_json["raw_exact_results"] = json::Value(counters.raw_exact_results);
            cnt_json["missing_set_rejections"] = json::Value(counters.missing_set_rejections);
        } else {
            cnt_json["edge_decisions"] = json::Value(counters.edge_decisions);
            cnt_json["missing_branches"] = json::Value(counters.missing_branches);
            cnt_json["commitment_checks"] = json::Value(counters.commitment_checks);
            cnt_json["commitment_rejections"] = json::Value(counters.commitment_rejections);
        }
        root_out["counters"] = cnt_json;

        root_out["status"] = json::Value(any_failure ? "FAIL" : "SUCCESS");

    } catch (const std::exception& e) {
        root_out["status"] = json::Value("ERROR");
        root_out["error_message"] = json::Value(e.what());
    }

    // Write output JSON
    std::ofstream out_file(output_path);
    if (!out_file.is_open()) {
        std::cerr << "Error: Cannot open output file: " << output_path << "\n";
        return 3;
    }
    out_file << json::serialize(root_out, 0) << "\n";
    out_file.close();

    return 0;
}
