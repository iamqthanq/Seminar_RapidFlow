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
#include <charconv>
#include <iomanip>
#include <sys/resource.h>
#include <unistd.h>
#include <openssl/sha.h>

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

static bool parse_positive_uint64_exact(const std::string& s, uint64_t& out, std::string& err) {
    if (s.empty()) {
        err = "Empty numeric string";
        return false;
    }
    for (char c : s) {
        if (c < '0' || c > '9') {
            err = "Invalid character in integer: '" + std::string(1, c) + "'";
            return false;
        }
    }
    const char* start = s.data();
    const char* end = s.data() + s.size();
    auto res = std::from_chars(start, end, out);
    if (res.ec != std::errc{} || res.ptr != end) {
        err = "Failed to parse unsigned integer or overflow";
        return false;
    }
    if (out == 0) {
        err = "Work budget must be strictly positive (got 0)";
        return false;
    }
    return true;
}

static std::string compute_sha256_hex(const std::string& data) {
    unsigned char hash[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(data.data()), data.size(), hash);
    std::ostringstream oss;
    for (int i = 0; i < SHA256_DIGEST_LENGTH; ++i) {
        oss << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(hash[i]);
    }
    return oss.str();
}

static bool write_json_file_safe(const std::string& path, const std::string& content, std::string& err_msg) {
    std::ofstream out(path, std::ios::out | std::ios::trunc);
    if (!out.is_open()) {
        err_msg = "Cannot open output file: " + path;
        return false;
    }
    out << content << "\n";
    out.flush();
    if (!out.good() || out.fail() || out.bad()) {
        err_msg = "Stream write or flush error on: " + path;
        return false;
    }
    out.close();
    if (out.fail()) {
        err_msg = "Stream close error on: " + path;
        return false;
    }
    return true;
}

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

static json::Value build_graph_state_json(const ApxAuthoritativeGraph& graph, const ApxTypedCodec& codec) {
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
    return g_state;
}

int main(int argc, char* argv[]) {
    std::string engine_str = "m2";
    std::string workload_path = "";
    std::string output_path = "";
    bool cli_max_work_set = false;
    uint64_t cli_max_work = 500000;
    bool continue_on_error = false;

    bool seen_engine = false;
    bool seen_workload = false;
    bool seen_output = false;
    bool seen_max_work = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--engine") {
            if (seen_engine) {
                std::cerr << "Error: Duplicate --engine argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --engine\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            engine_str = argv[++i];
            seen_engine = true;
        } else if (arg == "--workload") {
            if (seen_workload) {
                std::cerr << "Error: Duplicate --workload argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --workload\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            workload_path = argv[++i];
            seen_workload = true;
        } else if (arg == "--output") {
            if (seen_output) {
                std::cerr << "Error: Duplicate --output argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --output\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            output_path = argv[++i];
            seen_output = true;
        } else if (arg == "--max-work") {
            if (seen_max_work) {
                std::cerr << "Error: Duplicate --max-work argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --max-work\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            std::string parse_err;
            if (!parse_positive_uint64_exact(argv[++i], cli_max_work, parse_err)) {
                std::cerr << "Error: Invalid --max-work argument: " << parse_err << "\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            cli_max_work_set = true;
            seen_max_work = true;
        } else if (arg == "--continue-on-error") {
            continue_on_error = true;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json> [--max-work <N>] [--continue-on-error]\n";
            return static_cast<int>(ExitCode::SUCCESS);
        } else {
            std::cerr << "Error: Unknown or unsupported option: " << arg << "\n";
            return static_cast<int>(ExitCode::CLI_ARG_ERROR);
        }
    }

    if (!seen_workload || !seen_output) {
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

    try {
        apply_process_caps();
    } catch (const std::exception& e) {
        std::cerr << "Resource containment error: " << e.what() << "\n";
        return static_cast<int>(ExitCode::RESOURCE_LIMIT_ERROR);
    }

    // Read input file
    std::ifstream in_file(workload_path, std::ios::in | std::ios::binary);
    if (!in_file.is_open()) {
        std::cerr << "Error: Cannot open workload file: " << workload_path << "\n";
        return static_cast<int>(ExitCode::IO_ERROR);
    }
    std::string input_src((std::istreambuf_iterator<char>(in_file)), std::istreambuf_iterator<char>());
    in_file.close();

    std::string input_sha256 = compute_sha256_hex(input_src);

    json::Value root_out = json::Value::make_object();
    root_out["schema_version"] = json::Value("1.1.0");
    root_out["semantics_version"] = json::Value("D-R-1.0");
    root_out["raw_input_sha256"] = json::Value(input_sha256);
    root_out["engine_name"] = json::Value(engine_str);
    root_out["engine"] = json::Value(engine_str);
    root_out["case_id"] = json::Value("UNKNOWN");
    root_out["status"] = json::Value("FAIL");
    root_out["error_category"] = json::Value("NONE");
    root_out["error_message"] = json::Value("");
    root_out["steps"] = json::Value::make_array();

    const uint64_t CANONICAL_MAX_WORK_CAP = 500000ULL;

    json::Value exec_init = json::Value::make_object();
    exec_init["total_steps"] = json::Value(static_cast<int64_t>(0));
    exec_init["attempted_steps"] = json::Value(static_cast<int64_t>(0));
    exec_init["completed_steps"] = json::Value(static_cast<int64_t>(0));
    exec_init["failed_steps"] = json::Value(static_cast<int64_t>(0));
    exec_init["unattempted_steps"] = json::Value(static_cast<int64_t>(0));
    root_out["execution"] = exec_init;

    json::Value wb_init = json::Value::make_object();
    wb_init["default_limit"] = json::Value(static_cast<int64_t>(CANONICAL_MAX_WORK_CAP));
    wb_init["canonical_cap"] = json::Value(static_cast<int64_t>(CANONICAL_MAX_WORK_CAP));
    wb_init["workload_requested_limit"] = json::Value(static_cast<int64_t>(CANONICAL_MAX_WORK_CAP));
    wb_init["cli_requested_limit"] = json::Value(static_cast<int64_t>(cli_max_work_set ? cli_max_work : CANONICAL_MAX_WORK_CAP));
    wb_init["effective_limit"] = json::Value(static_cast<int64_t>(cli_max_work_set ? std::min(cli_max_work, CANONICAL_MAX_WORK_CAP) : CANONICAL_MAX_WORK_CAP));
    root_out["work_budget"] = wb_init;

    ExitCode final_exit_code = ExitCode::SUCCESS;

    try {
        json::Value root_in = json::Parser::parse(input_src);

        std::string case_id = root_in.contains("case_id") ? root_in["case_id"].as_string() : "unknown";
        root_out["case_id"] = json::Value(case_id);

        if (root_in.contains("continue_on_error")) {
            if (!root_in["continue_on_error"].is_bool()) {
                throw std::invalid_argument("Field 'continue_on_error' must be boolean, got non-boolean type");
            }
            if (root_in["continue_on_error"].as_bool()) {
                continue_on_error = true;
            }
        }
        if (root_in.contains("allow_recovery")) {
            if (!root_in["allow_recovery"].is_bool()) {
                throw std::invalid_argument("Field 'allow_recovery' must be boolean, got non-boolean type");
            }
            if (root_in["allow_recovery"].as_bool()) {
                continue_on_error = true;
            }
        }

        if (root_in.contains("updates") && root_in["updates"].is_array()) {
            int64_t total_u = static_cast<int64_t>(root_in["updates"].as_array().size());
            root_out["execution"]["total_steps"] = json::Value(total_u);
            root_out["execution"]["unattempted_steps"] = json::Value(total_u);
        }

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

        uint64_t default_limit = CANONICAL_MAX_WORK_CAP;
        uint64_t workload_requested_limit = CANONICAL_MAX_WORK_CAP;
        bool workload_requested_set = false;
        if (root_in.contains("max_work")) {
            const auto& mw_val = root_in["max_work"];
            if (!mw_val.is_int()) {
                throw std::invalid_argument("Field 'max_work' must be integer, got non-integer type");
            }
            int64_t raw_mw = mw_val.as_int();
            if (raw_mw <= 0) {
                throw std::invalid_argument("Negative max_work rejected: " + std::to_string(raw_mw));
            }
            workload_requested_limit = static_cast<uint64_t>(raw_mw);
            workload_requested_set = true;
        }

        uint64_t cli_requested_limit = cli_max_work_set ? cli_max_work : CANONICAL_MAX_WORK_CAP;
        uint64_t effective_limit = CANONICAL_MAX_WORK_CAP;
        if (workload_requested_set) {
            effective_limit = std::min(effective_limit, workload_requested_limit);
        }
        if (cli_max_work_set) {
            effective_limit = std::min(effective_limit, cli_requested_limit);
        }

        json::Value wb_obj = json::Value::make_object();
        wb_obj["default_limit"] = json::Value(static_cast<int64_t>(default_limit));
        wb_obj["canonical_cap"] = json::Value(static_cast<int64_t>(CANONICAL_MAX_WORK_CAP));
        wb_obj["workload_requested_limit"] = json::Value(static_cast<int64_t>(workload_requested_limit));
        wb_obj["cli_requested_limit"] = json::Value(static_cast<int64_t>(cli_requested_limit));
        wb_obj["effective_limit"] = json::Value(static_cast<int64_t>(effective_limit));
        root_out["work_budget"] = wb_obj;

        uint64_t max_work = effective_limit;

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
        for (const auto& v : q_v_arr) {
            if (!v.contains("id") || !v["id"].is_int()) {
                throw std::invalid_argument("Query vertex 'id' must be an integer");
            }
            int64_t vid = v["id"].as_int();
            TypedLabel lbl = parse_typed_label(v["label"]);
            q_ext_vids.push_back(vid);
            q_v_labels.push_back(lbl);
            codec.intern_query_vertex(vid);
            codec.intern_vertex_label(lbl);
        }

        struct ExtEdge { int64_t u; int64_t v; TypedLabel label; };
        std::vector<ExtEdge> q_ext_edges;
        for (const auto& e : q_e_arr) {
            if (!e.contains("u") || !e["u"].is_int() || !e.contains("v") || !e["v"].is_int()) {
                throw std::invalid_argument("Query edge endpoints 'u' and 'v' must be integers");
            }
            int64_t u = e["u"].as_int();
            int64_t v = e["v"].as_int();
            TypedLabel lbl = parse_typed_label(e["label"]);
            q_ext_edges.push_back({u, v, lbl});
            codec.intern_edge_label(lbl);
        }

        // 2. Initialize Data Graph from Initial Graph
        ApxAuthoritativeGraph graph;
        if (!root_in.contains("initial_graph")) {
            throw std::invalid_argument("Workload missing required 'initial_graph' object");
        }
        const auto& init_g = root_in["initial_graph"];
        const auto& init_v_arr = init_g["vertices"].as_array();
        const auto& init_e_arr = init_g["edges"].as_array();

        std::vector<std::pair<uint32_t, uint32_t>> base_vertices;
        for (const auto& v : init_v_arr) {
            if (!v.contains("id") || !v["id"].is_int()) {
                throw std::invalid_argument("Initial graph vertex 'id' must be an integer");
            }
            int64_t vid = v["id"].as_int();
            TypedLabel lbl = parse_typed_label(v["label"]);
            uint32_t dense_u = codec.intern_data_vertex(vid);
            uint32_t dense_lbl = codec.intern_vertex_label(lbl);
            base_vertices.emplace_back(dense_u, dense_lbl);
        }

        std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> base_edges;
        for (const auto& e : init_e_arr) {
            if (!e.contains("u") || !e["u"].is_int() || !e.contains("v") || !e["v"].is_int()) {
                throw std::invalid_argument("Initial graph edge endpoints 'u' and 'v' must be integers");
            }
            int64_t u = e["u"].as_int();
            int64_t v = e["v"].as_int();
            TypedLabel lbl = parse_typed_label(e["label"]);
            uint32_t dense_u = codec.intern_data_vertex(u);
            uint32_t dense_v = codec.intern_data_vertex(v);
            uint32_t dense_lbl = codec.intern_edge_label(lbl);
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
        root_out["computed_lambda"] = json::Value(query_plan.get_lambda());
        root_out["query_canonical_order"] = can_order_arr;

        // 5. Execute Updates
        if (!root_in.contains("updates") || !root_in["updates"].is_array()) {
            throw std::invalid_argument("Workload missing required 'updates' array");
        }
        const auto& upd_arr = root_in["updates"].as_array();
        size_t total_steps = upd_arr.size();
        size_t completed_steps = 0;
        size_t failed_steps = 0;
        json::Value steps_arr = json::Value::make_array();

        bool any_failure = false;
        std::string first_failing_error = "";
        std::string first_failing_category = "NONE";
        ExitCode first_failing_exit = ExitCode::SUCCESS;

        for (size_t step_idx = 0; step_idx < total_steps; ++step_idx) {
            const auto& upd_val = upd_arr[step_idx];
            json::Value step_json = json::Value::make_object();
            step_json["step_index"] = json::Value(static_cast<int64_t>(step_idx));
            step_json["commit_status"] = json::Value("NOT_COMMITTED");
            step_json["status"] = json::Value("NOT_EXECUTED");
            step_json["error"] = json::Value("");
            step_json["error_category"] = json::Value("NONE");
            step_json["entered_tuples"] = json::Value::make_array();
            step_json["exited_tuples"] = json::Value::make_array();

            try {
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

                step_json["op"] = json::Value(op == UpdateOp::INSERT ? "+" : "-");
                step_json["u"] = json::Value(ext_u);
                step_json["v"] = json::Value(ext_v);
                step_json["label"] = typed_label_to_json(lbl);

                // Controlled test-only fault injection hook for active transaction verification (P3-R5-F03)
                if (upd_val.contains("fault") && upd_val["fault"].is_string()) {
                    std::string fault_str = upd_val["fault"].as_string();
                    if (fault_str == "AFTER_MUTATION") {
                        streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::AFTER_MUTATION);
                    } else if (fault_str == "DURING_CALLBACK") {
                        streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::DURING_CALLBACK);
                    } else if (fault_str == "BEFORE_PUBLISH") {
                        streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::BEFORE_PUBLISH);
                    } else {
                        streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
                    }
                } else {
                    streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
                }

                UpdateOperation upd{op, ext_u, ext_v, lbl};
                StreamStepResult step_res = streaming_engine.execute_step(upd, static_cast<uint32_t>(step_idx));

                step_json["status"] = json::Value(step_status_to_string(step_res.status));
                step_json["error"] = json::Value(step_res.error_message);
                step_json["commit_status"] = json::Value(step_res.status == StepStatus::SUCCESS ? "COMMITTED" : "NOT_COMMITTED");

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
                step_cnt["raw_emissions"] = json::Value(step_res.step_counters.raw_emissions);
                step_cnt["unique_emissions"] = json::Value(step_res.step_counters.unique_emissions);
                step_cnt["committed_emissions"] = json::Value(step_res.step_counters.committed_emissions);
                step_cnt["discarded_emissions"] = json::Value(step_res.step_counters.discarded_emissions);
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

                // Deep graph state
                step_json["graph_state"] = build_graph_state_json(graph, codec);

                if (step_res.status != StepStatus::SUCCESS) {
                    any_failure = true;
                    failed_steps++;
                    if (first_failing_error.empty()) {
                        first_failing_error = step_res.error_message;
                        if (step_res.status == StepStatus::CALLBACK_ABORTED &&
                            step_res.error_message.find("Work budget exceeded") != std::string::npos) {
                            first_failing_category = "WORK_BUDGET_EXCEEDED";
                            first_failing_exit = ExitCode::WORK_BUDGET_EXCEEDED;
                        } else if (step_res.status == StepStatus::PREVALIDATION_FAILED) {
                            first_failing_category = "PREVALIDATION_ERROR";
                            first_failing_exit = ExitCode::STEP_FAILURE;
                        } else if (step_res.status == StepStatus::FAULT_INJECTED) {
                            first_failing_category = "FAULT_INJECTED";
                            first_failing_exit = ExitCode::STEP_FAILURE;
                        } else {
                            first_failing_category = "STEP_FAILURE";
                            first_failing_exit = ExitCode::STEP_FAILURE;
                        }
                    }
                    std::string cur_step_cat = "STEP_FAILURE";
                    if (step_res.status == StepStatus::PREVALIDATION_FAILED) {
                        cur_step_cat = "PREVALIDATION_ERROR";
                    } else if (step_res.status == StepStatus::FAULT_INJECTED) {
                        cur_step_cat = "FAULT_INJECTED";
                    } else if (step_res.status == StepStatus::CALLBACK_ABORTED &&
                               step_res.error_message.find("Work budget exceeded") != std::string::npos) {
                        cur_step_cat = "WORK_BUDGET_EXCEEDED";
                    }
                    step_json["error_category"] = json::Value(cur_step_cat);
                    steps_arr.push_back(step_json);

                    if (continue_on_error) {
                        streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
                        continue;
                    }

                    for (size_t rem = step_idx + 1; rem < total_steps; ++rem) {
                        json::Value un_step = json::Value::make_object();
                        un_step["step_index"] = json::Value(static_cast<int64_t>(rem));
                        if (rem < upd_arr.size()) {
                            const auto& un_upd = upd_arr[rem];
                            if (un_upd.is_object() && un_upd.contains("op") && un_upd.contains("u") && un_upd.contains("v") && un_upd.contains("label")) {
                                std::string u_op_str = un_upd["op"].as_string();
                                un_step["op"] = json::Value((u_op_str == "+" || u_op_str == "INSERT" || u_op_str == "INS") ? "+" : "-");
                                un_step["u"] = json::Value(un_upd["u"].as_int());
                                un_step["v"] = json::Value(un_upd["v"].as_int());
                                un_step["label"] = un_upd["label"];
                            }
                        }
                        un_step["status"] = json::Value("UNATTEMPTED");
                        un_step["commit_status"] = json::Value("NOT_COMMITTED");
                        un_step["error"] = json::Value("");
                        un_step["error_category"] = json::Value("NONE");
                        un_step["entered_tuples"] = json::Value::make_array();
                        un_step["exited_tuples"] = json::Value::make_array();
                        steps_arr.push_back(un_step);
                    }
                    break;
                }

                steps_arr.push_back(step_json);
                completed_steps++;
            } catch (const std::exception& e) {
                any_failure = true;
                failed_steps++;
                if (first_failing_error.empty()) {
                    first_failing_error = e.what();
                    first_failing_category = "INPUT_SCHEMA_ERROR";
                    first_failing_exit = ExitCode::INPUT_PARSE_ERROR;
                }

                step_json["status"] = json::Value("ERROR");
                step_json["commit_status"] = json::Value("NOT_COMMITTED");
                step_json["error"] = json::Value(e.what());
                step_json["error_category"] = json::Value("INPUT_SCHEMA_ERROR");
                steps_arr.push_back(step_json);

                if (continue_on_error) {
                    streaming_engine.get_tx_manager().set_fault_injection(ApxTransactionManager::FaultPoint::NONE);
                    continue;
                }

                for (size_t rem = step_idx + 1; rem < total_steps; ++rem) {
                    json::Value un_step = json::Value::make_object();
                    un_step["step_index"] = json::Value(static_cast<int64_t>(rem));
                    if (rem < upd_arr.size()) {
                        const auto& un_upd = upd_arr[rem];
                        if (un_upd.is_object() && un_upd.contains("op") && un_upd.contains("u") && un_upd.contains("v") && un_upd.contains("label")) {
                            std::string u_op_str = un_upd["op"].as_string();
                            un_step["op"] = json::Value((u_op_str == "+" || u_op_str == "INSERT" || u_op_str == "INS") ? "+" : "-");
                            un_step["u"] = json::Value(un_upd["u"].as_int());
                            un_step["v"] = json::Value(un_upd["v"].as_int());
                            un_step["label"] = un_upd["label"];
                        }
                    }
                    un_step["status"] = json::Value("UNATTEMPTED");
                    un_step["commit_status"] = json::Value("NOT_COMMITTED");
                    un_step["error"] = json::Value("");
                    un_step["error_category"] = json::Value("NONE");
                    un_step["entered_tuples"] = json::Value::make_array();
                    un_step["exited_tuples"] = json::Value::make_array();
                    steps_arr.push_back(un_step);
                }
                break;
            }
        }

        root_out["steps"] = steps_arr;

        json::Value exec_obj = json::Value::make_object();
        exec_obj["total_steps"] = json::Value(static_cast<int64_t>(total_steps));
        exec_obj["attempted_steps"] = json::Value(static_cast<int64_t>(completed_steps + failed_steps));
        exec_obj["completed_steps"] = json::Value(static_cast<int64_t>(completed_steps));
        exec_obj["failed_steps"] = json::Value(static_cast<int64_t>(failed_steps));
        exec_obj["unattempted_steps"] = json::Value(static_cast<int64_t>(total_steps - (completed_steps + failed_steps)));
        root_out["execution"] = exec_obj;

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
            root_out["error_category"] = json::Value(first_failing_category);
            root_out["error_message"] = json::Value(first_failing_error);
            final_exit_code = first_failing_exit;
        } else {
            root_out["status"] = json::Value("SUCCESS");
            root_out["error_category"] = json::Value("NONE");
            final_exit_code = ExitCode::SUCCESS;
        }

    } catch (const InvalidThresholdException& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("DOMAIN_ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::DOMAIN_REJECTION;
    } catch (const InvalidQueryException& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("DOMAIN_ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::DOMAIN_REJECTION;
    } catch (const WorkBudgetExceededException& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("WORK_BUDGET_EXCEEDED");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::WORK_BUDGET_EXCEEDED;
    } catch (const std::invalid_argument& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("INPUT_SCHEMA_ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::INPUT_PARSE_ERROR;
    } catch (const std::exception& e) {
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("UNCAUGHT_ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::UNCAUGHT_ERROR;
    }

    // Write output JSON safely
    std::string err_msg;
    std::string serialized = json::serialize(root_out, 0);
    if (!write_json_file_safe(output_path, serialized, err_msg)) {
        std::cerr << "Error: Output write failure: " << err_msg << "\n";
        return static_cast<int>(ExitCode::IO_ERROR);
    }

    return static_cast<int>(final_exit_code);
}
