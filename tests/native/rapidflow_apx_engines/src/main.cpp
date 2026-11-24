#include <iostream>
#include <cstdio>
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
#include "rapidflow_apx/profiler.h"
#include "p4b_tests.h"

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
    UNCAUGHT_ERROR = 8,
    RESULT_DELIVERY_ERROR = 9
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

static const char* canonical_update_op(const std::string& op) noexcept {
    if (op == "+" || op == "INSERT" || op == "INS") return "+";
    if (op == "-" || op == "DELETE" || op == "DEL") return "-";
    return nullptr;
}

static json::Value make_step_record(size_t index, const char* status) {
    json::Value record = json::Value::make_object();
    record["step_index"] = json::Value(static_cast<int64_t>(index));
    record["status"] = json::Value(status);
    record["commit_status"] = json::Value("NOT_COMMITTED");
    record["error"] = json::Value("");
    record["error_category"] = json::Value("NONE");
    record["entered_tuples"] = json::Value::make_array();
    record["exited_tuples"] = json::Value::make_array();
    return record;
}

static json::Value unattempted_update(size_t index, const json::Value& update) {
    auto record = make_step_record(index, "UNATTEMPTED");
    if (!update.is_object()) return record;
    if (update.contains("op") && update["op"].is_string()) {
        if (const char* op = canonical_update_op(update["op"].as_string())) {
            record["op"] = json::Value(op);
        }
    }
    for (const char* endpoint : {"u", "v"}) {
        if (update.contains(endpoint) && update[endpoint].is_int()) {
            record[endpoint] = update[endpoint];
        }
    }
    if (update.contains("label") && (update["label"].is_int() || update["label"].is_string())) {
        record["label"] = update["label"];
    }
    return record;
}

struct StepFailure {
    const char* category;
    ExitCode exit_code;
};

static StepFailure step_failure(StepStatus status, const std::string& error) noexcept {
    if (status == StepStatus::PREVALIDATION_FAILED) return {"PREVALIDATION_ERROR", ExitCode::STEP_FAILURE};
    if (status == StepStatus::FAULT_INJECTED) return {"FAULT_INJECTED", ExitCode::STEP_FAILURE};
    if (status == StepStatus::CALLBACK_ABORTED && error.find("Work budget exceeded") != std::string::npos) {
        return {"WORK_BUDGET_EXCEEDED", ExitCode::WORK_BUDGET_EXCEEDED};
    }
    return {"STEP_FAILURE", ExitCode::STEP_FAILURE};
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

int run_native_empty_frontier_test() {
    std::cout << "Running native empty-frontier invariant witness test...\n";
    try {
        // 1. Build a valid Diamond query plan: 4 vertices, 5 edges (diamond with chord (1, 2)), k=1, lambda=2
        // Edges: 0: (0, 1), 1: (0, 2), 2: (1, 2), 3: (1, 3), 4: (2, 3)
        ApxQueryPlan qp;
        std::vector<uint32_t> v_labels = {0, 0, 0, 0};
        std::vector<QueryEdge> q_edges = {
            {0, 1, 0}, {0, 2, 0}, {1, 2, 0}, {1, 3, 0}, {2, 3, 0}
        };
        std::vector<uint32_t> can_order = {0, 1, 2, 3};
        qp.build(4, v_labels, q_edges, can_order, 1);

        if (qp.get_k() != 1 || qp.get_lambda() != 2) {
            std::cerr << "FAILED: Diamond query plan build error\n";
            return 1;
        }

        // --- Positive Control ---
        // S = {0, 1} mapped, T = {2, 3} unmapped.
        // Status of edges: 0 (0, 1) is PRESENT; edges 1, 2, 3, 4 are UNKNOWN.
        std::vector<uint32_t> mapping_pos(4, ApxM2EventEngine::UNASSIGNED);
        mapping_pos[0] = 10;
        mapping_pos[1] = 20;
        std::vector<EdgeCommitment> status_pos(5, EdgeCommitment::UNKNOWN);
        status_pos[0] = EdgeCommitment::PRESENT;

        int pos_edge = ApxM2EventEngine::find_frontier_edge(mapping_pos, status_pos, 0, 2, qp);
        if (pos_edge < 0 || pos_edge >= 5) {
            std::cerr << "FAILED: Positive control returned invalid frontier edge: " << pos_edge << "\n";
            return 1;
        }

        // --- Negative Control ---
        // Artificial internal state:
        // S = {0, 1} mapped, T = {2, 3} unmapped.
        // All cut edges (1, 2, 3) are marked MISSING or PRESENT, so NO UNKNOWN edges exist across the cut.
        // missing_count = 1 <= k = 1.
        std::vector<uint32_t> mapping_neg(4, ApxM2EventEngine::UNASSIGNED);
        mapping_neg[0] = 10;
        mapping_neg[1] = 20;
        std::vector<EdgeCommitment> status_neg(5, EdgeCommitment::MISSING);
        status_neg[0] = EdgeCommitment::PRESENT; // internal mapped edge

        bool threw_invariant = false;
        try {
            ApxM2EventEngine::find_frontier_edge(mapping_neg, status_neg, 1, 2, qp);
        } catch (const InvariantViolationException& ex) {
            threw_invariant = true;
            std::string msg = ex.what();
            if (msg.find("M2 invariant violation: empty frontier while unmapped vertices remain") == std::string::npos) {
                std::cerr << "FAILED: Exception message unexpected: " << msg << "\n";
                return 1;
            }
        }

        if (!threw_invariant) {
            std::cerr << "FAILED: Negative control did NOT throw InvariantViolationException!\n";
            return 1;
        }

        // --- State Integrity Verification ---
        if (qp.get_vertices_count() != 4 || qp.get_edges_count() != 5 || qp.get_k() != 1 || qp.get_lambda() != 2) {
            std::cerr << "FAILED: Query plan state corrupted after seam test\n";
            return 1;
        }

        std::cout << "{\"test\": \"native_empty_frontier_invariant\", \"status\": \"PASS\", \"positive_control\": \"PASS\", \"negative_control\": \"PASS\", \"state_integrity\": \"PASS\"}\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAILED: Unexpected exception: " << e.what() << "\n";
        return 1;
    }
}

static const char* nlf_mode_name(NlfMode mode) {
    switch (mode) {
        case NlfMode::NONE: return "none";
        case NlfMode::STATIC: return "static";
        case NlfMode::RESIDUAL: return "residual";
    }
    throw std::logic_error("Unknown native NLF mode");
}

static bool parse_nlf_mode(const std::string& text, NlfMode& mode) {
    for (auto candidate : {NlfMode::NONE, NlfMode::STATIC, NlfMode::RESIDUAL}) {
        if (text == nlf_mode_name(candidate)) {
            mode = candidate;
            return true;
        }
    }
    return false;
}

static const char* neighbor_mode_name(NeighborMode mode) noexcept {
    switch (mode) {
        case NeighborMode::BUFFER: return "buffer";
        case NeighborMode::MERGE: return "merge";
        case NeighborMode::LEGACY:
        default: return "legacy";
    }
}

static void add_neighbor_counters(json::Value& out, const EngineCounters& counters) {
    out["neighbor_buffer_retrieval_calls"] = json::Value(counters.neighbor_buffer_retrieval_calls);
    out["neighbor_buffer_growth_calls"] = json::Value(counters.neighbor_buffer_growth_calls);
    out["neighbor_buffer_no_growth_calls"] = json::Value(counters.neighbor_buffer_no_growth_calls);
    out["neighbor_merge_retrieval_calls"] = json::Value(counters.neighbor_merge_retrieval_calls);
    out["neighbor_merge_linear_calls"] = json::Value(counters.neighbor_merge_linear_calls);
    out["neighbor_merge_fast_path_calls"] = json::Value(counters.neighbor_merge_fast_path_calls);
    out["neighbor_merge_fallback_calls"] = json::Value(counters.neighbor_merge_fallback_calls);
}

static void add_residual_counters(json::Value& out, const EngineCounters& counters) {
    out["nlf_residual_checks"] = json::Value(counters.nlf_residual_checks);
    out["nlf_residual_pruned"] = json::Value(counters.nlf_residual_pruned);
    out["nlf_residual_anchor_checks"] = json::Value(counters.nlf_residual_anchor_checks);
    out["nlf_residual_anchor_pruned"] = json::Value(counters.nlf_residual_anchor_pruned);
    out["nlf_residual_signature_lookups"] = json::Value(counters.nlf_residual_signature_lookups);
    out["nlf_residual_missing_commits"] = json::Value(counters.nlf_residual_missing_commits);
    out["nlf_residual_missing_undos"] = json::Value(counters.nlf_residual_missing_undos);
}

int main(int argc, char* argv[]) {
    std::string engine_str = "m2";
    NlfMode nlf_mode = NlfMode::NONE;
    NeighborMode neighbor_mode = NeighborMode::LEGACY;
    uint64_t max_nlf_memory_mib = DEFAULT_NLF_MEMORY_MIB; // 256 MiB default
    bool cli_max_nlf_memory_mib_set = false;
    std::string workload_path = "";
    std::string output_path = "";
    bool cli_max_work_set = false;
    uint64_t cli_max_work = 500000;
    bool continue_on_error = false;

    bool seen_engine = false;
    bool seen_workload = false;
    bool seen_output = false;
    bool seen_max_work = false;
    bool seen_nlf_mode = false;
    bool seen_neighbor_mode = false;
    bool seen_max_nlf_memory_mib = false;
    bool profiling_enabled = false;
    std::string profile_output_path = "";
    RunProfiling run_profiling;
    ProfilingTimePoint run_wall_t0 = ProfilingClock::now();

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--test-empty-frontier") {
            return run_native_empty_frontier_test();
        } else if (arg == "--test-p4b-neighbor") {
            return run_neighbor_buffer_tests(i + 1 < argc ? argv[i + 1] : "graph");
        } else if (arg == "--test-p4b-residual") {
            return run_residual_native_tests(i + 1 < argc ? argv[i + 1] : "numeric");
        } else if (arg == "--test-p4b") {
            std::string suite = "all";
            if (i + 1 < argc && argv[i + 1][0] != '-') {
                suite = argv[++i];
            }
            return run_p4b_native_tests(suite.c_str());
        } else if (arg == "--engine") {
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
        } else if (arg == "--neighbor-mode") {
            if (seen_neighbor_mode || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --neighbor-mode argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_neighbor_mode = true;
            const std::string mode = argv[++i];
            if (mode == "buffer") {
                neighbor_mode = NeighborMode::BUFFER;
            } else if (mode == "merge") {
                neighbor_mode = NeighborMode::MERGE;
            } else if (mode == "legacy") {
                neighbor_mode = NeighborMode::LEGACY;
            } else {
                std::cerr << "Error: Invalid --neighbor-mode '" << mode << "'. Must be 'legacy', 'buffer', or 'merge'.\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
        } else if (arg == "--nlf-mode") {
            if (seen_nlf_mode) {
                std::cerr << "Error: Duplicate --nlf-mode argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_nlf_mode = true;
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --nlf-mode\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            const std::string mode_text = argv[++i];
            if (!parse_nlf_mode(mode_text, nlf_mode)) {
                std::cerr << "Error: Invalid --nlf-mode '" << mode_text << "'. Must be 'none', 'static' or 'residual'.\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
        } else if (arg == "--max-nlf-memory-mib") {
            if (seen_max_nlf_memory_mib) {
                std::cerr << "Error: Duplicate --max-nlf-memory-mib argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_max_nlf_memory_mib = true;
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --max-nlf-memory-mib\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            std::string parse_err;
            uint64_t val = 0;
            if (!parse_positive_uint64_exact(argv[++i], val, parse_err)) {
                std::cerr << "Error: Invalid --max-nlf-memory-mib: " << parse_err << "\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (val == 0) {
                std::cerr << "Error: Invalid --max-nlf-memory-mib: value must be positive (got 0)\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (val > MAX_NLF_MEMORY_MIB) {
                std::cerr << "Error: Invalid --max-nlf-memory-mib: value " << val << " exceeds maximum serializable memory limit\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            max_nlf_memory_mib = val;
            cli_max_nlf_memory_mib_set = true;
        } else if (arg == "--profile") {
            profiling_enabled = true;
        } else if (arg == "--profile-output") {
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --profile-output\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            profile_output_path = argv[++i];
            profiling_enabled = true;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json> [--max-work <N>] [--continue-on-error] [--nlf-mode <none|static|residual>] [--neighbor-mode <legacy|buffer|merge>] [--max-nlf-memory-mib <MiB>] [--profile] [--profile-output <FILE>]\n";
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

    if (!profile_output_path.empty() && profile_output_path == output_path) {
        std::cerr << "Error: --profile-output path cannot be identical to --output path\n";
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

    if (engine_type == EngineType::M1 && nlf_mode != NlfMode::NONE) {
        std::cerr << "Error: NLF optimization is not supported for M1 engine.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    if (!is_supported_neighbor_mode(engine_type, nlf_mode, neighbor_mode)) {
        std::cerr << "Error: Neighbor buffer/merge mode requires M2 with NLF NONE.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    try {
        apply_process_caps();
    } catch (const std::exception& e) {
        std::cerr << "Resource containment error: " << e.what() << "\n";
        return static_cast<int>(ExitCode::RESOURCE_LIMIT_ERROR);
    }

    // Read input file
    ProfilingTimePoint parse_t0 = ProfilingClock::now();
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
    root_out["configuration_initialized"] = json::Value(false);
    root_out["neighbor_configuration_version"] = json::Value(1);
    root_out["error_category"] = json::Value("NONE");
    root_out["error_message"] = json::Value("");
    root_out["steps"] = json::Value::make_array();

    const uint64_t CANONICAL_MAX_WORK_CAP = 500000ULL;

    json::Value exec_init = json::Value::make_object();
    exec_init["total_steps"] = json::Value(static_cast<int64_t>(0));
    exec_init["attempted_steps"] = json::Value(static_cast<int64_t>(0));
    exec_init["committed_steps"] = json::Value(0);
    exec_init["delivered_steps"] = json::Value(0);
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
    size_t attempted_steps = 0;
    size_t committed_steps = 0;
    const auto fatal_delivery = [&](const char* detail) {
        std::fprintf(stderr, "Fatal RESULT_DELIVERY_ERROR: %s (attempted_steps=%zu committed_steps=%zu)\n",
                     detail, attempted_steps, committed_steps);
    };

    try {
        json::Value root_in = json::Parser::parse(input_src);
        ProfilingTimePoint parse_t1 = ProfilingClock::now();
        run_profiling.input_parsing_ns = std::chrono::duration_cast<ProfilingNanoseconds>(parse_t1 - parse_t0).count();

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
        ProfilingTimePoint init_t0 = ProfilingClock::now();
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
        ProfilingTimePoint init_t1 = ProfilingClock::now();
        run_profiling.graph_init_ns = std::chrono::duration_cast<ProfilingNanoseconds>(init_t1 - init_t0).count();

        // 3. Build Query Plan
        ProfilingTimePoint qp_t0 = ProfilingClock::now();
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
        ProfilingTimePoint qp_t1 = ProfilingClock::now();
        run_profiling.query_prep_ns = std::chrono::duration_cast<ProfilingNanoseconds>(qp_t1 - qp_t0).count();

        // 4. Initialize Sink and Streaming Engine
        size_t max_nlf_memory_bytes = static_cast<size_t>(max_nlf_memory_mib) * BYTES_PER_MIB;
        ApxTupleSink sink(codec, canonical_order);
        ApxStreamingEngine streaming_engine(graph, codec, sink, query_plan, engine_type, max_work, nlf_mode, max_nlf_memory_bytes, neighbor_mode);
        streaming_engine.set_profiling_enabled(profiling_enabled);

        root_out["configuration_initialized"] = json::Value(true);
        root_out["requested_neighbor_mode"] = json::Value(neighbor_mode_name(neighbor_mode));
        root_out["effective_neighbor_mode"] = json::Value(neighbor_mode_name(streaming_engine.get_neighbor_mode()));
        root_out["requested_nlf_mode"] = json::Value(nlf_mode_name(streaming_engine.get_requested_nlf_mode()));
        root_out["effective_nlf_mode"] = json::Value(nlf_mode_name(streaming_engine.get_effective_nlf_mode()));
        root_out["configuration_status"] = json::Value(streaming_engine.get_configuration_status());
        if (!streaming_engine.get_fallback_reason().empty()) {
            root_out["fallback_reason"] = json::Value(streaming_engine.get_fallback_reason());
        }
        root_out["nlf_memory_limit_bytes"] = json::Value(static_cast<int64_t>(max_nlf_memory_bytes));
        if (streaming_engine.get_nlf_index()) {
            root_out["nlf_index_logical_bytes"] = json::Value(static_cast<int64_t>(streaming_engine.get_nlf_index()->logical_bytes()));
        } else {
            root_out["nlf_index_logical_bytes"] = json::Value(static_cast<int64_t>(0));
        }

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
        size_t delivered_steps = 0;
        size_t completed_steps = 0;
        size_t failed_steps = 0;
        json::Value steps_arr = json::Value::make_array();
        steps_arr.arr.reserve(total_steps);

        std::string first_failing_error;
        const char* first_failing_category = "NONE";
        ExitCode first_failing_exit = ExitCode::SUCCESS;
        size_t first_failing_step = total_steps;
        bool delivery_failed = false;
        std::string delivery_error;
        const auto remember_failure = [&](size_t index, StepFailure failure, const std::string& message) {
            if (first_failing_step != total_steps) return;
            first_failing_step = index;
            first_failing_category = failure.category;
            first_failing_exit = failure.exit_code;
            first_failing_error = message;
        };

        ProfilingTimePoint stream_t0 = ProfilingClock::now();
        for (size_t step_idx = 0; step_idx < total_steps; ++step_idx) {
            const auto& upd_val = upd_arr[step_idx];
            auto step_json = make_step_record(step_idx, "NOT_EXECUTED");
            const char* execution_status = nullptr;
            const char* delivery_stage = nullptr;
            bool committed = false;
            bool failed = false;
            ++attempted_steps;
            try {
                if (!upd_val.is_object() || !upd_val.contains("op") || !upd_val["op"].is_string()) {
                    throw std::invalid_argument("Update 'op' must be a string");
                }
                const auto& op_str = upd_val["op"].as_string();
                const char* canonical_op = canonical_update_op(op_str);
                if (!canonical_op) {
                    throw std::invalid_argument("Invalid update operation '" + op_str + "': must be '+', '-', 'INSERT', or 'DELETE'");
                }
                const UpdateOp op = *canonical_op == '+' ? UpdateOp::INSERT : UpdateOp::DELETE;

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

                // These workload fields are deterministic fault hooks for the test harness.
                using FaultPoint = ApxTransactionManager::FaultPoint;
                const auto fault = upd_val.contains("fault") && upd_val["fault"].is_string()
                    ? upd_val["fault"].as_string() : std::string{};
                auto tx_fault = FaultPoint::NONE;
                auto m2_fault = M2FaultHook::NONE;
                if (fault == "BEFORE_MUTATION") tx_fault = FaultPoint::BEFORE_MUTATION;
                else if (fault == "BETWEEN_FORWARD_REVERSE_MUTATION") tx_fault = FaultPoint::BETWEEN_FORWARD_REVERSE_MUTATION;
                else if (fault == "ALLOCATION_FAILURE_MUTATION") tx_fault = FaultPoint::ALLOCATION_FAILURE_MUTATION;
                else if (fault == "AFTER_MUTATION") tx_fault = FaultPoint::AFTER_MUTATION;
                else if (fault == "DURING_CALLBACK") tx_fault = FaultPoint::DURING_CALLBACK;
                else if (fault == "AFTER_NLF_PREPARE") tx_fault = FaultPoint::AFTER_NLF_PREPARE;
                else if (fault == "AFTER_GRAPH_BEFORE_NLF") tx_fault = FaultPoint::AFTER_GRAPH_BEFORE_NLF;
                else if (fault == "AFTER_FIRST_NLF_CELL") tx_fault = FaultPoint::AFTER_FIRST_NLF_CELL;
                else if (fault == "BEFORE_PUBLISH") tx_fault = FaultPoint::BEFORE_PUBLISH;
                else if (fault == "AFTER_MISSING_COMMITMENT") m2_fault = M2FaultHook::AFTER_MISSING_COMMITMENT;
                else if (fault == "AFTER_VERTEX_ASSIGNMENT") m2_fault = M2FaultHook::AFTER_VERTEX_ASSIGNMENT;
                streaming_engine.get_tx_manager().set_fault_injection(tx_fault);
                streaming_engine.set_m2_fault_hook(m2_fault);

                UpdateOperation upd{op, ext_u, ext_v, lbl};
                StreamStepResult step_res = streaming_engine.execute_step(upd, static_cast<uint32_t>(step_idx));
                committed = step_res.committed;
                if (committed) ++committed_steps;
                execution_status = step_status_to_string(step_res.status);
                failed = step_res.status != StepStatus::SUCCESS;
                if (failed) {
                    auto failure = step_failure(step_res.status, step_res.error_message);
                    remember_failure(step_idx, failure, step_res.error_message);
                    step_json["error_category"] = json::Value(failure.category);
                }

                delivery_stage = "AFTER_EXECUTION";
                if (fault == "REPORT_AFTER_COMMIT") throw std::bad_alloc();
                step_json["status"] = json::Value(execution_status);
                step_json["error"] = json::Value(step_res.error_message);
                step_json["commit_status"] = json::Value(committed ? "COMMITTED" : "NOT_COMMITTED");

                delivery_stage = "RESULT_JSON";
                if (fault == "REPORT_RESULT_JSON") throw std::bad_alloc();
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
                add_neighbor_counters(step_cnt, step_res.step_counters);
                add_residual_counters(step_cnt, step_res.step_counters);
                step_cnt["nlf_static_checks"] = json::Value(step_res.step_counters.nlf_static_checks);
                step_cnt["nlf_static_pruned"] = json::Value(step_res.step_counters.nlf_static_pruned);
                step_cnt["nlf_signature_lookups"] = json::Value(step_res.step_counters.nlf_signature_lookups);
                step_cnt["nlf_anchor_checks"] = json::Value(step_res.step_counters.nlf_anchor_checks);
                step_cnt["nlf_anchor_pruned"] = json::Value(step_res.step_counters.nlf_anchor_pruned);
                step_cnt["nlf_index_cell_updates"] = json::Value(step_res.step_counters.nlf_index_cell_updates);
                step_cnt["nlf_index_rollback_cells"] = json::Value(step_res.step_counters.nlf_index_rollback_cells);
                step_cnt["nlf_index_initialization_cells"] = json::Value(step_res.step_counters.nlf_index_initialization_cells);
                step_json["step_counters"] = step_cnt;

                // Codec state
                json::Value c_state = json::Value::make_object();
                c_state["query_vertices_count"] = json::Value(codec.query_vertices_count());
                c_state["data_vertices_count"] = json::Value(codec.data_vertices_count());
                c_state["vertex_labels_count"] = json::Value(codec.vertex_labels_count());
                c_state["edge_labels_count"] = json::Value(codec.edge_labels_count());
                step_json["codec_state"] = c_state;

                // Deep graph state
                delivery_stage = "GRAPH_DIAGNOSTICS";
                if (fault == "REPORT_GRAPH_DIAGNOSTICS") throw std::bad_alloc();
                step_json["graph_state"] = build_graph_state_json(graph, codec);
                if (tx_fault != FaultPoint::NONE || m2_fault != M2FaultHook::NONE
                    || (nlf_mode == NlfMode::RESIDUAL && step_res.status == StepStatus::CALLBACK_ABORTED
                        && step_res.error_message.find("Work budget exceeded") != std::string::npos)) {
                    if (const auto* index = streaming_engine.get_nlf_index()) {
                        step_json["nlf_full_cell_reconstruction"] = json::Value(
                            verify_index_reconstruction(graph, streaming_engine.get_query_signatures(), *index));
                    }
                }

                if (profiling_enabled) {
                    json::Value p_step = json::Value::make_object();
                    p_step["step_total_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.step_total_ns));
                    p_step["update_maintenance_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.update_maintenance_ns));
                    p_step["search_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.search_ns));
                    p_step["neighbor_retrieval_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.neighbor_retrieval_ns));
                    p_step["search_core_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.search_core_ns));
                    p_step["tuple_commit_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.tuple_commit_ns));
                    p_step["tuple_sink_ns"] = json::Value(static_cast<int64_t>(step_res.step_profiling.tuple_sink_ns));
                    step_json["profiling"] = p_step;
                }

                delivery_stage = "APPEND_STEP";
                if (fault == "REPORT_APPEND_STEP") throw std::bad_alloc();
                steps_arr.arr.push_back(std::move(step_json));
                ++delivered_steps;
            } catch (const std::exception& e) {
                failed = true;
                if (execution_status) {
                    delivery_failed = true;
                    delivery_error = e.what();
                    remember_failure(step_idx, {"RESULT_DELIVERY_ERROR", ExitCode::RESULT_DELIVERY_ERROR}, delivery_error);
                    auto failure_record = make_step_record(step_idx, "DELIVERY_FAILED");
                    failure_record["commit_status"] = json::Value(committed ? "COMMITTED" : "NOT_COMMITTED");
                    failure_record["execution_status"] = json::Value(execution_status);
                    failure_record["delivery_status"] = json::Value("FAILED");
                    failure_record["delivery_stage"] = json::Value(delivery_stage ? delivery_stage : "RESULT_JSON");
                    failure_record["error"] = json::Value(delivery_error);
                    failure_record["error_category"] = json::Value("RESULT_DELIVERY_ERROR");
                    // For explicit test faults, inspect the actual graph and every NLF cell
                    // after the real transaction and the simulated reporting failure.
                    if (upd_val.contains("fault") && upd_val["fault"].is_string()
                        && upd_val["fault"].as_string().rfind("REPORT_", 0) == 0) {
                        failure_record["graph_state"] = build_graph_state_json(graph, codec);
                        if (const auto* index = streaming_engine.get_nlf_index()) {
                            failure_record["nlf_full_cell_reconstruction"] = json::Value(
                                verify_index_reconstruction(graph, streaming_engine.get_query_signatures(), *index));
                        }
                    }
                    steps_arr.arr.push_back(std::move(failure_record));
                } else {
                    const bool exhausted = dynamic_cast<const std::bad_alloc*>(&e) != nullptr;
                    const StepFailure failure = exhausted
                        ? StepFailure{"RESOURCE_LIMIT_ERROR", ExitCode::RESOURCE_LIMIT_ERROR}
                        : StepFailure{"INPUT_SCHEMA_ERROR", ExitCode::INPUT_PARSE_ERROR};
                    remember_failure(step_idx, failure, e.what());
                    step_json["status"] = json::Value("ERROR");
                    step_json["error"] = json::Value(e.what());
                    step_json["error_category"] = json::Value(failure.category);
                    steps_arr.arr.push_back(std::move(step_json));
                    ++delivered_steps;
                }
            }
            if (failed) ++failed_steps;
            else ++completed_steps;

            const bool poisoned_optimized = (nlf_mode == NlfMode::RESIDUAL || neighbor_mode == NeighborMode::BUFFER || neighbor_mode == NeighborMode::MERGE)
                && streaming_engine.get_tx_manager().is_session_poisoned();
            if (delivery_failed || (failed && !continue_on_error) || poisoned_optimized) {
                for (size_t rem = step_idx + 1; rem < total_steps; ++rem) {
                    steps_arr.arr.push_back(unattempted_update(rem, upd_arr[rem]));
                }
                break;
            }
        }
        ProfilingTimePoint stream_t1 = ProfilingClock::now();
        run_profiling.streaming_exec_ns = std::chrono::duration_cast<ProfilingNanoseconds>(stream_t1 - stream_t0).count();

        root_out["steps"] = std::move(steps_arr);

        json::Value exec_obj = json::Value::make_object();
        exec_obj["total_steps"] = json::Value(static_cast<int64_t>(total_steps));
        exec_obj["attempted_steps"] = json::Value(static_cast<int64_t>(attempted_steps));
        exec_obj["committed_steps"] = json::Value(static_cast<int64_t>(committed_steps));
        exec_obj["delivered_steps"] = json::Value(static_cast<int64_t>(delivered_steps));
        exec_obj["completed_steps"] = json::Value(static_cast<int64_t>(completed_steps));
        exec_obj["failed_steps"] = json::Value(static_cast<int64_t>(failed_steps));
        exec_obj["unattempted_steps"] = json::Value(static_cast<int64_t>(total_steps - attempted_steps));
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
        add_neighbor_counters(cnt_json, counters);
        add_residual_counters(cnt_json, counters);
        cnt_json["nlf_static_checks"] = json::Value(counters.nlf_static_checks);
        cnt_json["nlf_static_pruned"] = json::Value(counters.nlf_static_pruned);
        cnt_json["nlf_signature_lookups"] = json::Value(counters.nlf_signature_lookups);
        cnt_json["nlf_anchor_checks"] = json::Value(counters.nlf_anchor_checks);
        cnt_json["nlf_anchor_pruned"] = json::Value(counters.nlf_anchor_pruned);
        cnt_json["nlf_index_cell_updates"] = json::Value(counters.nlf_index_cell_updates);
        cnt_json["nlf_index_rollback_cells"] = json::Value(counters.nlf_index_rollback_cells);
        cnt_json["nlf_index_initialization_cells"] = json::Value(counters.nlf_index_initialization_cells);
        root_out["counters"] = cnt_json;

        if (profiling_enabled) {
            json::Value p_root = json::Value::make_object();
            p_root["enabled"] = json::Value(true);
            p_root["profiling_schema_version"] = json::Value("1.2.0");
            p_root["input_parsing_ns"] = json::Value(static_cast<int64_t>(run_profiling.input_parsing_ns));
            p_root["graph_init_ns"] = json::Value(static_cast<int64_t>(run_profiling.graph_init_ns));
            p_root["query_prep_ns"] = json::Value(static_cast<int64_t>(run_profiling.query_prep_ns));
            p_root["streaming_loop_wall_ns"] = json::Value(static_cast<int64_t>(run_profiling.streaming_exec_ns));
            p_root["streaming_exec_ns"] = json::Value(static_cast<int64_t>(run_profiling.streaming_exec_ns)); // alias for backward compatibility

            const auto& cp = streaming_engine.get_cumulative_profiling();
            p_root["native_step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));

            json::Value p_cum = json::Value::make_object();
            p_cum["step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));
            p_cum["update_maintenance_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_update_maintenance_ns));
            p_cum["search_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_ns));
            p_cum["neighbor_retrieval_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_neighbor_retrieval_ns));
            p_cum["search_core_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_core_ns));
            p_cum["tuple_commit_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
            p_cum["tuple_sink_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns)); // alias for backward compatibility
            p_root["cumulative"] = p_cum;

            p_root["cumulative_step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));
            p_root["cumulative_update_maintenance_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_update_maintenance_ns));
            p_root["cumulative_search_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_ns));
            p_root["cumulative_neighbor_retrieval_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_neighbor_retrieval_ns));
            p_root["cumulative_search_core_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_core_ns));
            p_root["cumulative_tuple_commit_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
            p_root["cumulative_tuple_sink_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
            auto cur_t = ProfilingClock::now();
            int64_t pre_output_ns = static_cast<int64_t>(std::chrono::duration_cast<ProfilingNanoseconds>(cur_t - run_wall_t0).count());
            p_root["pre_output_wall_ns"] = json::Value(pre_output_ns);
            p_root["total_wall_ns"] = json::Value(pre_output_ns);
            root_out["profiling"] = p_root;
        }

        if (first_failing_step != total_steps) {
            root_out["first_failing_step_index"] = json::Value(first_failing_step);
            root_out["first_failure_category"] = json::Value(first_failing_category);
            root_out["first_failure_message"] = json::Value(first_failing_error);
            root_out["status"] = json::Value("FAIL");
            root_out["error_category"] = json::Value(first_failing_category);
            root_out["error_message"] = json::Value(first_failing_error);
            final_exit_code = first_failing_exit;
            if (delivery_failed) {
                root_out["error_category"] = json::Value("RESULT_DELIVERY_ERROR");
                root_out["error_message"] = json::Value(delivery_error);
                final_exit_code = ExitCode::RESULT_DELIVERY_ERROR;
            }
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
        if (attempted_steps != 0) {
            fatal_delivery(e.what());
            return static_cast<int>(ExitCode::RESULT_DELIVERY_ERROR);
        }
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("UNCAUGHT_ERROR");
        root_out["error_message"] = json::Value(e.what());
        final_exit_code = ExitCode::UNCAUGHT_ERROR;
    }

    try {
        std::string err_msg;
        if (!write_json_file_safe(output_path, json::serialize(root_out, 0), err_msg)) {
            std::fprintf(stderr, "Output write failure: ");
            fatal_delivery(err_msg.c_str());
            return static_cast<int>(ExitCode::IO_ERROR);
        }
        if (!profile_output_path.empty() && profiling_enabled && root_out.contains("profiling")) {
            if (!write_json_file_safe(profile_output_path, json::serialize(root_out["profiling"], 2), err_msg)) {
                std::fprintf(stderr, "Profile output write failure: ");
                fatal_delivery(err_msg.c_str());
                return static_cast<int>(ExitCode::IO_ERROR);
            }
        }
    } catch (const std::exception& e) {
        fatal_delivery(e.what());
        return static_cast<int>(ExitCode::RESULT_DELIVERY_ERROR);
    }

    return static_cast<int>(final_exit_code);
}
