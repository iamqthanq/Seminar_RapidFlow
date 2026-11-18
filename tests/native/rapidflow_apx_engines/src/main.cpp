#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <memory>
#include <chrono>
#include <cstring>
#include <algorithm>
#include <limits>
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

enum class ExitCode : int {
    SUCCESS = 0,
    CLI_ARG_ERROR = 1,
    INPUT_PARSE_ERROR = 2,
    DOMAIN_REJECTION = 3,
    WORK_BUDGET_EXCEEDED = 4,
    RESOURCE_LIMIT_ERROR = 5,
    STEP_FAILURE = 6,
    IO_ERROR = 7,
    UNCAUGHT_ERROR = 8
};

static TypedLabel parse_typed_label(const json::Value& v) {
    if (v.is_int()) {
        return TypedLabel::from_int(v.as_int());
    } else if (v.is_string()) {
        return TypedLabel::from_string(v.as_string());
    } else {
        throw std::invalid_argument("Invalid label type in JSON: must be int or string (boolean, float, null, etc. are rejected)");
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
    if (setrlimit(RLIMIT_AS, &rl) != 0) {
        throw std::runtime_error("Failed to set RLIMIT_AS address space limit: " + std::string(strerror(errno)));
    }

    // 30 seconds CPU time cap
    alarm(30);
}

int main(int argc, char* argv[]) {
    std::string engine_str = "m2";
    std::string workload_path = "";
    std::string output_path = "";
    uint64_t max_work = 500000;
    bool cli_max_work_set = false;
    uint64_t cli_max_work = 500000;
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
            try {
                cli_max_work = std::stoull(argv[++i]);
                cli_max_work_set = true;
            } catch (const std::exception& e) {
                std::cerr << "Error: Invalid --max-work argument: " << e.what() << "\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
        } else if (arg == "--no-caps") {
            enable_caps = false;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json> [--max-work <N>]\n";
            return static_cast<int>(ExitCode::SUCCESS);
        }
    }

    if (workload_path.empty() || output_path.empty()) {
        std::cerr << "Error: --workload and --output are required.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    EngineType engine_type = EngineType::M2;
    if (engine_str == "m1") {
        engine_type = EngineType::M1;
    } else if (engine_str == "m2") {
        engine_type = EngineType::M2;
    } else {
        std::cerr << "Error: Unknown engine '" << engine_str << "'. Must be 'm1' or 'm2'.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    if (enable_caps) {
        try {
            apply_process_caps();
        } catch (const std::exception& e) {
            std::cerr << "Resource containment error: " << e.what() << "\n";
            return static_cast<int>(ExitCode::RESOURCE_LIMIT_ERROR);
        }
    }

    // Read input file
    std::ifstream in_file(workload_path);
    if (!in_file.is_open()) {
        std::cerr << "Error: Cannot open workload file: " << workload_path << "\n";
        return static_cast<int>(ExitCode::IO_ERROR);
    }
    std::stringstream buffer;
    buffer << in_file.rdbuf();
    std::string input_src = buffer.str();
    in_file.close();

    json::Value root_out = json::Value::make_object();
    root_out["engine"] = json::Value(engine_str);
    root_out["status"] = json::Value("FAIL");
    root_out["error_message"] = json::Value("");

    ExitCode final_exit_code = ExitCode::SUCCESS;

    try {
        json::Value root_in = json::Parser::parse(input_src);

        std::string case_id = root_in.contains("case_id") ? root_in["case_id"].as_string() : "unknown";
        root_out["case_id"] = json::Value(case_id);

        uint32_t k = 0;
        if (root_in.contains("k")) {
            const auto& k_val = root_in["k"];
            if (!k_val.is_int()) {
                throw std::invalid_argument("Field 'k' must be integer, got non-integer type (boolean/float/string rejected)");
            }
            int64_t raw_k = k_val.as_int();
            if (raw_k < 0) {
                throw std::invalid_argument("Negative threshold k rejected: " + std::to_string(raw_k));
            }
            if (static_cast<uint64_t>(raw_k) > std::numeric_limits<uint32_t>::max()) {
                throw std::invalid_argument("Threshold k exceeds uint32 range: " + std::to_string(raw_k));
            }
            k = static_cast<uint32_t>(raw_k);
        }

        if (root_in.contains("max_work")) {
            const auto& mw_val = root_in["max_work"];
            if (!mw_val.is_int()) {
                throw std::invalid_argument("Field 'max_work' must be integer, got non-integer type");
            }
            int64_t raw_mw = mw_val.as_int();
            if (raw_mw < 0) {
                throw std::invalid_argument("Negative max_work rejected: " + std::to_string(raw_mw));
            }
            uint64_t workload_mw = static_cast<uint64_t>(raw_mw);
            if (cli_max_work_set) {
                max_work = std::min(cli_max_work, workload_mw);
            } else {
                max_work = workload_mw;
            }
        } else if (cli_max_work_set) {
            max_work = cli_max_work;
        }

        // 1. Initialize Codec
        ApxTypedCodec codec;

        // Parse Query
        if (!root_in.contains("query")) {
            throw std::invalid_argument("Workload missing required 'query' object");
        }
        const auto& q_obj = root_in["query"];
        const auto& q_v_arr = q_obj["vertices"].as_array();
        const auto& q_e_arr = q_obj["edges"].as_array();

        std::vector<int64_t> q_ext_vids;
        std::vector<TypedLabel> q_v_labels;
        for (const auto& v_val : q_v_arr) {
            if (!v_val.contains("id") || !v_val["id"].is_int()) {
                throw std::invalid_argument("Query vertex 'id' must be integer");
            }
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
            if (!e_val.contains("u") || !e_val["u"].is_int() || !e_val.contains("v") || !e_val["v"].is_int()) {
                throw std::invalid_argument("Query edge endpoints 'u' and 'v' must be integers");
            }
            int64_t u = e_val["u"].as_int();
            int64_t v = e_val["v"].as_int();
            TypedLabel lbl = parse_typed_label(e_val["label"]);
            q_ext_edges.push_back({u, v, lbl});
            codec.intern_edge_label(lbl);
        }

        // Parse Initial Data Graph
        if (!root_in.contains("initial_graph")) {
            throw std::invalid_argument("Workload missing required 'initial_graph' object");
        }
        const auto& g_obj = root_in["initial_graph"];
        const auto& g_v_arr = g_obj["vertices"].as_array();
        const auto& g_e_arr = g_obj["edges"].as_array();

        std::vector<int64_t> g_ext_vids;
        std::vector<TypedLabel> g_v_labels;
        for (const auto& v_val : g_v_arr) {
            if (!v_val.contains("id") || !v_val["id"].is_int()) {
                throw std::invalid_argument("Data vertex 'id' must be integer");
            }
            int64_t ext_v = v_val["id"].as_int();
            TypedLabel lbl = parse_typed_label(v_val["label"]);
            g_ext_vids.push_back(ext_v);
            g_v_labels.push_back(lbl);
            codec.intern_data_vertex(ext_v);
            codec.intern_vertex_label(lbl);
        }

        std::vector<ExtEdge> g_ext_edges;
        for (const auto& e_val : g_e_arr) {
            if (!e_val.contains("u") || !e_val["u"].is_int() || !e_val.contains("v") || !e_val["v"].is_int()) {
                throw std::invalid_argument("Data edge endpoints 'u' and 'v' must be integers");
            }
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
        if (!root_in.contains("updates")) {
            throw std::invalid_argument("Workload missing required 'updates' array");
        }
        const auto& upd_arr = root_in["updates"].as_array();
        json::Value steps_arr = json::Value::make_array();
        bool any_failure = false;
        StepStatus first_failing_status = StepStatus::SUCCESS;
        std::string first_failing_error = "";

        for (size_t step_idx = 0; step_idx < upd_arr.size(); ++step_idx) {
            const auto& upd_val = upd_arr[step_idx];
            if (!upd_val.contains("op") || !upd_val["op"].is_string()) {
                throw std::invalid_argument("Update 'op' must be a string");
            }
            std::string op_str = upd_val["op"].as_string();
            UpdateOp op;
            if (op_str == "+" || op_str == "INSERT" || op_str == "INS") {
                op = UpdateOp::INSERT;
            } else if (op_str == "-" || op_str == "DELETE" || op_str == "DEL") {
                op = UpdateOp::DELETE;
            } else {
                throw std::invalid_argument("Invalid update operation '" + op_str + "': must be '+', '-', 'INSERT', or 'DELETE'");
            }

            if (!upd_val.contains("u") || !upd_val["u"].is_int() || !upd_val.contains("v") || !upd_val["v"].is_int()) {
                throw std::invalid_argument("Update edge endpoints 'u' and 'v' must be integers");
            }
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

            // Per-step counters
            json::Value step_cnt = json::Value::make_object();
            step_cnt["work_units"] = json::Value(step_res.step_counters.work_units);
            step_cnt["max_work"] = json::Value(step_res.step_counters.max_work);
            step_cnt["dfs_states"] = json::Value(step_res.step_counters.dfs_states);
            step_cnt["candidate_evaluations"] = json::Value(step_res.step_counters.candidate_evaluations);
            step_cnt["candidate_extensions"] = json::Value(step_res.step_counters.candidate_extensions);
            step_cnt["edge_lookups"] = json::Value(step_res.step_counters.edge_lookups);
            step_cnt["vertex_label_checks"] = json::Value(step_res.step_counters.vertex_label_checks);
            step_cnt["anchors_started"] = json::Value(step_res.step_counters.anchors_started);
            if (engine_type == EngineType::M1) {
                step_cnt["variants_started"] = json::Value(step_res.step_counters.variants_started);
                step_cnt["raw_exact_results"] = json::Value(step_res.step_counters.raw_exact_results);
                step_cnt["missing_set_rejections"] = json::Value(step_res.step_counters.missing_set_rejections);
            } else {
                step_cnt["edge_decisions"] = json::Value(step_res.step_counters.edge_decisions);
                step_cnt["missing_branches"] = json::Value(step_res.step_counters.missing_branches);
                step_cnt["commitment_checks"] = json::Value(step_res.step_counters.commitment_checks);
                step_cnt["commitment_rejections"] = json::Value(step_res.step_counters.commitment_rejections);
            }
            step_json["step_counters"] = step_cnt;

            // Codec state
            json::Value c_state = json::Value::make_object();
            c_state["query_vertices_count"] = json::Value(codec.query_vertices_count());
            c_state["data_vertices_count"] = json::Value(codec.data_vertices_count());
            c_state["vertex_labels_count"] = json::Value(codec.vertex_labels_count());
            c_state["edge_labels_count"] = json::Value(codec.edge_labels_count());
            step_json["codec_state"] = c_state;

            // Deep graph state: vertices, degrees, sorted edges, neighbors
            json::Value g_state = json::Value::make_object();
            g_state["vertices_count"] = json::Value(graph.get_vertices_count());
            g_state["edges_count"] = json::Value(graph.get_edges_count());

            uint32_t num_dverts = codec.data_vertices_count();
            struct VertDiag {
                int64_t id;
                TypedLabel label;
                uint32_t degree;
                std::vector<int64_t> neighbors;
            };
            std::vector<VertDiag> sorted_verts;
            for (uint32_t du = 0; du < num_dverts; ++du) {
                if (graph.has_vertex(du)) {
                    int64_t ext_id = codec.decode_data_vertex(du);
                    TypedLabel vlbl = codec.decode_vertex_label(graph.get_vertex_label(du));
                    uint32_t deg = graph.get_degree(du);
                    std::vector<uint32_t> d_nbrs = graph.get_neighbors(du);
                    std::vector<int64_t> ext_nbrs;
                    ext_nbrs.reserve(d_nbrs.size());
                    for (uint32_t dn : d_nbrs) {
                        ext_nbrs.push_back(codec.decode_data_vertex(dn));
                    }
                    std::sort(ext_nbrs.begin(), ext_nbrs.end());
                    sorted_verts.push_back({ext_id, vlbl, deg, std::move(ext_nbrs)});
                }
            }
            std::sort(sorted_verts.begin(), sorted_verts.end(), [](const VertDiag& a, const VertDiag& b) {
                return a.id < b.id;
            });

            json::Value v_arr = json::Value::make_array();
            json::Value nbr_obj = json::Value::make_object();
            for (const auto& vd : sorted_verts) {
                json::Value v_item = json::Value::make_object();
                v_item["id"] = json::Value(vd.id);
                v_item["label"] = typed_label_to_json(vd.label);
                v_item["degree"] = json::Value(vd.degree);
                v_arr.push_back(v_item);

                json::Value n_list = json::Value::make_array();
                for (int64_t nid : vd.neighbors) {
                    n_list.push_back(json::Value(nid));
                }
                nbr_obj[std::to_string(vd.id)] = n_list;
            }
            g_state["vertices"] = v_arr;
            g_state["neighbors"] = nbr_obj;

            struct EdgeDiag {
                int64_t u;
                int64_t v;
                TypedLabel label;
            };
            std::vector<EdgeDiag> sorted_edges;
            for (uint32_t du = 0; du < num_dverts; ++du) {
                if (!graph.has_vertex(du)) continue;
                int64_t ext_u_node = codec.decode_data_vertex(du);
                for (uint32_t dv : graph.get_neighbors(du)) {
                    if (du < dv) {
                        int64_t ext_v_node = codec.decode_data_vertex(dv);
                        auto el = graph.get_edge_label(du, dv);
                        if (el.exists) {
                            TypedLabel elbl = codec.decode_edge_label(el.label);
                            int64_t mn = std::min(ext_u_node, ext_v_node);
                            int64_t mx = std::max(ext_u_node, ext_v_node);
                            sorted_edges.push_back({mn, mx, elbl});
                        }
                    }
                }
            }
            std::sort(sorted_edges.begin(), sorted_edges.end(), [](const EdgeDiag& a, const EdgeDiag& b) {
                if (a.u != b.u) return a.u < b.u;
                return a.v < b.v;
            });

            json::Value e_arr = json::Value::make_array();
            for (const auto& ed : sorted_edges) {
                json::Value e_item = json::Value::make_object();
                e_item["u"] = json::Value(ed.u);
                e_item["v"] = json::Value(ed.v);
                e_item["label"] = typed_label_to_json(ed.label);
                e_arr.push_back(e_item);
            }
            g_state["edges"] = e_arr;
            step_json["graph_state"] = g_state;

            steps_arr.push_back(step_json);

            if (step_res.status != StepStatus::SUCCESS) {
                any_failure = true;
                first_failing_status = step_res.status;
                first_failing_error = step_res.error_message;
                break; // Stop after first failing step, preserve prior steps
            }
        }

        root_out["steps"] = steps_arr;

        // Cumulative Counters across run
        const auto& counters = streaming_engine.get_cumulative_counters();
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

        if (any_failure) {
            root_out["status"] = json::Value("FAIL");
            root_out["error_message"] = json::Value(first_failing_error);
            if (first_failing_status == StepStatus::CALLBACK_ABORTED &&
                first_failing_error.find("Work budget exceeded") != std::string::npos) {
                final_exit_code = ExitCode::WORK_BUDGET_EXCEEDED;
            } else {
                final_exit_code = ExitCode::STEP_FAILURE;
            }
        } else {
            root_out["status"] = json::Value("SUCCESS");
            final_exit_code = ExitCode::SUCCESS;
        }

    } catch (const InvalidThresholdException& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::DOMAIN_REJECTION;
    } catch (const InvalidQueryException& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::DOMAIN_REJECTION;
    } catch (const WorkBudgetExceededException& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::WORK_BUDGET_EXCEEDED;
    } catch (const std::invalid_argument& e) {
        root_out["status"] = json::Value("ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::INPUT_PARSE_ERROR;
    } catch (const std::exception& e) {
        root_out["status"] = json::Value("ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::UNCAUGHT_ERROR;
    }

    // Write output JSON
    std::ofstream out_file(output_path);
    if (!out_file.is_open()) {
        std::cerr << "Error: Cannot open output file: " << output_path << "\n";
        return static_cast<int>(ExitCode::IO_ERROR);
    }
    out_file << json::serialize(root_out, 0) << "\n";
    out_file.close();

    return static_cast<int>(final_exit_code);
}
