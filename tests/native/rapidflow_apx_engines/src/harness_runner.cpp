#include "harness_runner.h"

#include <algorithm>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <sys/resource.h>
#include <unistd.h>
#include <vector>
#include <openssl/sha.h>

#include "p4b_tests.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/json_mini.h"
#include "rapidflow_apx/profiler.h"
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/typed_codec.h"

namespace rapidflow_apx {

const char* execution_purpose_name(ExecutionPurpose purpose) noexcept {
    switch (purpose) {
        case ExecutionPurpose::CORRECTNESS: return "correctness";
        case ExecutionPurpose::PERFORMANCE: return "performance";
        case ExecutionPurpose::DIAGNOSTIC: return "diagnostic";
    }
    return "correctness";
}

bool parse_execution_purpose(const std::string& text, ExecutionPurpose& purpose) noexcept {
    if (text == "correctness") {
        purpose = ExecutionPurpose::CORRECTNESS;
        return true;
    }
    if (text == "performance") {
        purpose = ExecutionPurpose::PERFORMANCE;
        return true;
    }
    if (text == "diagnostic") {
        purpose = ExecutionPurpose::DIAGNOSTIC;
        return true;
    }
    return false;
}

namespace {

inline void notify_observer(
    BoundaryObserverFn observer,
    MeasurementStage stage,
    uint64_t step_idx,
    void* ctx
) {
    if (observer) {
        observer(stage, step_idx, ctx);
    }
}

std::string compute_sha256_hex(const std::string& data) {
    unsigned char hash[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(data.data()), data.size(), hash);
    std::ostringstream oss;
    for (int i = 0; i < SHA256_DIGEST_LENGTH; ++i) {
        oss << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(hash[i]);
    }
    return oss.str();
}

bool write_json_file_safe(const std::string& path, const std::string& content, std::string& err_msg) {
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

void append_tuple_list_json(std::string& out, const std::vector<std::vector<int64_t>>& tuples) {
    out.push_back('[');
    char num_buf[32];
    for (size_t i = 0; i < tuples.size(); ++i) {
        if (i > 0) out.push_back(',');
        out.push_back('[');
        const auto& tup = tuples[i];
        for (size_t j = 0; j < tup.size(); ++j) {
            if (j > 0) out.push_back(',');
            auto res = std::to_chars(num_buf, num_buf + sizeof(num_buf), tup[j]);
            out.append(num_buf, static_cast<size_t>(res.ptr - num_buf));
        }
        out.push_back(']');
    }
    out.push_back(']');
}

std::string serialize_step_json_with_tuples(
    json::Value step_meta,
    const std::vector<std::vector<int64_t>>& entered,
    const std::vector<std::vector<int64_t>>& exited
) {
    step_meta.obj.erase("entered_tuples");
    step_meta.obj.erase("exited_tuples");
    std::string meta_str = json::serialize(step_meta, 0);
    // Strip trailing '}' (or whitespace before '}') so tuples are injected inline cleanly
    size_t close_pos = meta_str.rfind('}');
    if (close_pos == std::string::npos) {
        throw std::logic_error("Expected JSON object serialization ending with '}'");
    }
    bool empty_obj = true;
    for (size_t k = 1; k < close_pos; ++k) {
        if (!std::isspace(static_cast<unsigned char>(meta_str[k]))) {
            empty_obj = false;
            break;
        }
    }
    meta_str.resize(close_pos);
    meta_str.reserve(meta_str.size() + 48 + (entered.size() + exited.size()) * 16);
    if (!empty_obj) meta_str.push_back(',');
    meta_str.append("\"entered_tuples\":");
    append_tuple_list_json(meta_str, entered);
    meta_str.append(",\"exited_tuples\":");
    append_tuple_list_json(meta_str, exited);
    meta_str.push_back('}');
    return meta_str;
}

bool write_root_with_steps_streamed(
    const std::string& path,
    json::Value& root_out,
    std::vector<std::string>& serialized_steps,
    std::string& err_msg
) {
    root_out.obj.erase("steps");
    std::string root_str = json::serialize(root_out, 0);
    size_t close_pos = root_str.rfind('}');
    if (close_pos == std::string::npos) {
        err_msg = "Malformed root JSON object during serialization";
        return false;
    }
    bool empty_obj = true;
    for (size_t k = 1; k < close_pos; ++k) {
        if (!std::isspace(static_cast<unsigned char>(root_str[k]))) {
            empty_obj = false;
            break;
        }
    }
    root_str.resize(close_pos);

    std::ofstream out(path, std::ios::out | std::ios::trunc);
    if (!out.is_open()) {
        err_msg = "Cannot open output file: " + path;
        return false;
    }
    out.write(root_str.data(), static_cast<std::streamsize>(root_str.size()));
    std::string().swap(root_str);
    if (!empty_obj) out.put(',');
    out.write("\"steps\":[", 9);
    for (size_t i = 0; i < serialized_steps.size(); ++i) {
        if (i > 0) out.put(',');
        out.write(serialized_steps[i].data(), static_cast<std::streamsize>(serialized_steps[i].size()));
        std::string().swap(serialized_steps[i]);
    }
    out.write("]}\n", 3);
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

const char* canonical_update_op(const std::string& op) noexcept {
    if (op == "+" || op == "INSERT" || op == "INS") return "+";
    if (op == "-" || op == "DELETE" || op == "DEL") return "-";
    return nullptr;
}

json::Value make_step_record(size_t index, const char* status) {
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

json::Value unattempted_update(size_t index, const json::Value& update) {
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

StepFailure step_failure(StepStatus status, const std::string& error) noexcept {
    if (status == StepStatus::PREVALIDATION_FAILED) return {"PREVALIDATION_ERROR", ExitCode::STEP_FAILURE};
    if (status == StepStatus::FAULT_INJECTED) return {"FAULT_INJECTED", ExitCode::STEP_FAILURE};
    if (status == StepStatus::INVARIANT_VIOLATION) return {"INVARIANT_VIOLATION", ExitCode::STEP_FAILURE};
    if (status == StepStatus::RESOURCE_EXHAUSTED) return {"RESOURCE_LIMIT_ERROR", ExitCode::RESOURCE_LIMIT_ERROR};
    if (status == StepStatus::CALLBACK_ABORTED && error.find("Work budget exceeded") != std::string::npos) {
        return {"WORK_BUDGET_EXCEEDED", ExitCode::WORK_BUDGET_EXCEEDED};
    }
    return {"STEP_FAILURE", ExitCode::STEP_FAILURE};
}

TypedLabel parse_typed_label(const json::Value& v) {
    if (v.is_int()) {
        return TypedLabel::from_int(v.as_int());
    }
    if (v.is_string()) {
        return TypedLabel::from_string(v.as_string());
    }
    throw std::invalid_argument("Invalid label type in JSON: must be int or string (boolean, float, null, etc. are rejected)");
}

json::Value typed_label_to_json(const TypedLabel& lbl) {
    if (lbl.type == LabelType::INT64) {
        return json::Value(lbl.int_val);
    }
    return json::Value(lbl.str_val);
}

void apply_process_caps(uint64_t wall_timeout_seconds) {
    struct rlimit rl;
    rl.rlim_cur = 512ULL * 1024ULL * 1024ULL;
    rl.rlim_max = 512ULL * 1024ULL * 1024ULL;
    if (setrlimit(RLIMIT_AS, &rl) != 0) {
        throw std::runtime_error("Failed to set RLIMIT_AS address space limit: " + std::string(strerror(errno)));
    }
    alarm(static_cast<unsigned int>(wall_timeout_seconds));
}

json::Value build_graph_state_json(const ApxAuthoritativeGraph& graph, const ApxTypedCodec& codec) {
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

const char* nlf_mode_name(NlfMode mode) {
    switch (mode) {
        case NlfMode::NONE: return "none";
        case NlfMode::STATIC: return "static";
        case NlfMode::RESIDUAL: return "residual";
    }
    throw std::logic_error("Unknown native NLF mode");
}

const char* neighbor_mode_name(NeighborMode mode) noexcept {
    switch (mode) {
        case NeighborMode::BUFFER: return "buffer";
        case NeighborMode::MERGE: return "merge";
        case NeighborMode::LEGACY:
        default: return "legacy";
    }
}

void add_neighbor_counters(json::Value& out, const EngineCounters& counters) {
    out["neighbor_buffer_retrieval_calls"] = json::Value(counters.neighbor_buffer_retrieval_calls);
    out["neighbor_buffer_growth_calls"] = json::Value(counters.neighbor_buffer_growth_calls);
    out["neighbor_buffer_no_growth_calls"] = json::Value(counters.neighbor_buffer_no_growth_calls);
    out["neighbor_merge_retrieval_calls"] = json::Value(counters.neighbor_merge_retrieval_calls);
    out["neighbor_merge_linear_calls"] = json::Value(counters.neighbor_merge_linear_calls);
    out["neighbor_merge_fast_path_calls"] = json::Value(counters.neighbor_merge_fast_path_calls);
    out["neighbor_merge_fallback_calls"] = json::Value(counters.neighbor_merge_fallback_calls);
}

void add_residual_counters(json::Value& out, const EngineCounters& counters) {
    out["nlf_residual_checks"] = json::Value(counters.nlf_residual_checks);
    out["nlf_residual_pruned"] = json::Value(counters.nlf_residual_pruned);
    out["nlf_residual_anchor_checks"] = json::Value(counters.nlf_residual_anchor_checks);
    out["nlf_residual_anchor_pruned"] = json::Value(counters.nlf_residual_anchor_pruned);
    out["nlf_residual_signature_lookups"] = json::Value(counters.nlf_residual_signature_lookups);
    out["nlf_residual_missing_commits"] = json::Value(counters.nlf_residual_missing_commits);
    out["nlf_residual_missing_undos"] = json::Value(counters.nlf_residual_missing_undos);
}

void populate_counters_json(json::Value& cnt_json, const EngineCounters& c, EngineType engine_type) {
    cnt_json["work_units"] = json::Value(c.work_units);
    cnt_json["max_work"] = json::Value(c.max_work);
    cnt_json["dfs_states"] = json::Value(c.dfs_states);
    cnt_json["candidate_evaluations"] = json::Value(c.candidate_evaluations);
    cnt_json["candidate_extensions"] = json::Value(c.candidate_extensions);
    cnt_json["edge_lookups"] = json::Value(c.edge_lookups);
    cnt_json["vertex_label_checks"] = json::Value(c.vertex_label_checks);
    cnt_json["anchors_started"] = json::Value(c.anchors_started);
    cnt_json["raw_emissions"] = json::Value(c.raw_emissions);
    cnt_json["unique_emissions"] = json::Value(c.unique_emissions);
    cnt_json["committed_emissions"] = json::Value(c.committed_emissions);
    cnt_json["discarded_emissions"] = json::Value(c.discarded_emissions);
    if (engine_type == EngineType::M1) {
        cnt_json["variants_started"] = json::Value(c.variants_started);
        cnt_json["raw_exact_results"] = json::Value(c.raw_exact_results);
        cnt_json["missing_set_rejections"] = json::Value(c.missing_set_rejections);
    } else {
        cnt_json["edge_decisions"] = json::Value(c.edge_decisions);
        cnt_json["missing_branches"] = json::Value(c.missing_branches);
        cnt_json["commitment_checks"] = json::Value(c.commitment_checks);
        cnt_json["commitment_rejections"] = json::Value(c.commitment_rejections);
    }
    add_neighbor_counters(cnt_json, c);
    add_residual_counters(cnt_json, c);
    cnt_json["nlf_static_checks"] = json::Value(c.nlf_static_checks);
    cnt_json["nlf_static_pruned"] = json::Value(c.nlf_static_pruned);
    cnt_json["nlf_signature_lookups"] = json::Value(c.nlf_signature_lookups);
    cnt_json["nlf_anchor_checks"] = json::Value(c.nlf_anchor_checks);
    cnt_json["nlf_anchor_pruned"] = json::Value(c.nlf_anchor_pruned);
    cnt_json["nlf_index_cell_updates"] = json::Value(c.nlf_index_cell_updates);
    cnt_json["nlf_index_rollback_cells"] = json::Value(c.nlf_index_rollback_cells);
    cnt_json["nlf_index_initialization_cells"] = json::Value(c.nlf_index_initialization_cells);
}

struct RetainedStepOutcome {
    size_t step_index{0};
    bool has_op{false};
    const char* canonical_op{nullptr};
    int64_t ext_u{0};
    int64_t ext_v{0};
    bool has_label{false};
    TypedLabel label{};
    bool pre_exec_error{false};
    StepFailure pre_exec_failure{"NONE", ExitCode::SUCCESS};
    std::string pre_exec_message{};
    bool has_step_res{false};
    StreamStepResult step_res{};
    std::string fault_tag{};
};

int64_t read_self_vmhwm_kib() noexcept {
    std::ifstream ifs("/proc/self/status");
    if (!ifs.is_open()) return -1;
    std::string line;
    while (std::getline(ifs, line)) {
        if (line.compare(0, 6, "VmHWM:") == 0) {
            const char* p = line.c_str() + 6;
            while (*p == ' ' || *p == '\t') ++p;
            char* endp = nullptr;
            long long val = std::strtoll(p, &endp, 10);
            if (endp != p && val > 0) {
                return static_cast<int64_t>(val);
            }
            return -1;
        }
    }
    return -1;
}

void attach_measurement_metadata(
    json::Value& root_out,
    ExecutionPurpose purpose,
    bool profiling_enabled,
    bool deep_diagnostics_enabled,
    const MacroTiming& macro_timing
) {
    root_out["measurement_schema_version"] = json::Value(1);
    root_out["execution_purpose"] = json::Value(execution_purpose_name(purpose));
    root_out["profiling_enabled"] = json::Value(profiling_enabled);
    root_out["deep_diagnostics_enabled"] = json::Value(deep_diagnostics_enabled);
    json::Value mt = json::Value::make_object();
    mt["clock_source"] = json::Value("std::chrono::steady_clock");
    mt["input_parsing_ns"] = json::Value(static_cast<int64_t>(macro_timing.input_parsing_ns));
    mt["initialization_wall_ns"] = json::Value(static_cast<int64_t>(macro_timing.initialization_wall_ns));
    mt["graph_init_ns"] = json::Value(static_cast<int64_t>(macro_timing.graph_init_ns));
    mt["query_prep_ns"] = json::Value(static_cast<int64_t>(macro_timing.query_prep_ns));
    mt["engine_and_nlf_init_ns"] = json::Value(static_cast<int64_t>(macro_timing.engine_and_nlf_init_ns));
    mt["streaming_loop_wall_ns"] = json::Value(static_cast<int64_t>(macro_timing.streaming_loop_wall_ns));
    mt["reporting_wall_ns"] = json::Value(static_cast<int64_t>(macro_timing.reporting_wall_ns));
    const int64_t vmhwm_kib = read_self_vmhwm_kib();
    if (vmhwm_kib > 0) {
        mt["child_vm_hwm_kib"] = json::Value(vmhwm_kib);
        mt["child_vm_hwm_bytes"] = json::Value(vmhwm_kib * 1024LL);
        mt["child_rss_method"] = json::Value("linux_proc_self_status_vmhwm");
    }
    root_out["macro_timing"] = mt;
}

} // namespace

int run_workload_with_options(
    const HarnessOptions& opts,
    BoundaryObserverFn observer,
    void* observer_ctx
) {
    if (opts.workload_path.empty() || opts.output_path.empty()) {
        std::cerr << "Error: --workload and --output are required.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }
    if (!opts.profile_output_path.empty() && opts.profile_output_path == opts.output_path) {
        std::cerr << "Error: --profile-output path cannot be identical to --output path\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }
    if (opts.wall_timeout_seconds == 0 || opts.wall_timeout_seconds > 60) {
        std::cerr << "Error: --wall-timeout-seconds must be in range 1..60\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    EngineType engine_type = EngineType::M2;
    if (opts.engine_str == "m1") {
        engine_type = EngineType::M1;
    } else if (opts.engine_str == "m2") {
        engine_type = EngineType::M2;
    } else {
        std::cerr << "Error: Unknown engine '" << opts.engine_str << "'. Must be 'm1' or 'm2'.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    if (engine_type == EngineType::M1 && opts.nlf_mode != NlfMode::NONE) {
        std::cerr << "Error: NLF optimization is not supported for M1 engine.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    if (!is_supported_neighbor_mode(engine_type, opts.nlf_mode, opts.neighbor_mode)) {
        std::cerr << "Error: Neighbor buffer/merge mode requires M2 with NLF NONE.\n";
        return static_cast<int>(ExitCode::CLI_ARG_ERROR);
    }

    bool profiling_enabled = opts.profiling_enabled;
    bool deep_diagnostics_enabled = true;
    if (opts.execution_purpose == ExecutionPurpose::PERFORMANCE) {
        if (opts.profile_flag_seen || !opts.profile_output_path.empty()) {
            std::cerr << "Error: --profile / --profile-output cannot be used with --execution-purpose performance (PROFILE_OFF required)\n";
            return static_cast<int>(ExitCode::CLI_ARG_ERROR);
        }
        profiling_enabled = false;
        deep_diagnostics_enabled = false;
    } else if (opts.execution_purpose == ExecutionPurpose::DIAGNOSTIC) {
        profiling_enabled = true;
        deep_diagnostics_enabled = false;
    } else {
        deep_diagnostics_enabled = true;
    }

    if (opts.apply_process_limits) {
        try {
            apply_process_caps(opts.wall_timeout_seconds);
        } catch (const std::exception& e) {
            std::cerr << "Resource containment error: " << e.what() << "\n";
            return static_cast<int>(ExitCode::RESOURCE_LIMIT_ERROR);
        }
    }

    RunProfiling run_profiling;
    run_profiling.enabled = profiling_enabled;
    MacroTiming macro_timing;
    ProfilingTimePoint run_wall_t0 = ProfilingClock::now();

    notify_observer(observer, MeasurementStage::PARSE_START, 0, observer_ctx);
    ProfilingTimePoint parse_t0 = ProfilingClock::now();
    std::ifstream in_file(opts.workload_path, std::ios::in | std::ios::binary);
    if (!in_file.is_open()) {
        std::cerr << "Error: Cannot open workload file: " << opts.workload_path << "\n";
        return static_cast<int>(ExitCode::IO_ERROR);
    }
    std::string input_src((std::istreambuf_iterator<char>(in_file)), std::istreambuf_iterator<char>());
    in_file.close();

    std::string input_sha256 = compute_sha256_hex(input_src);

    json::Value root_out = json::Value::make_object();
    root_out["schema_version"] = json::Value("1.1.0");
    root_out["semantics_version"] = json::Value("D-R-1.0");
    root_out["raw_input_sha256"] = json::Value(input_sha256);
    root_out["engine_name"] = json::Value(opts.engine_str);
    root_out["engine"] = json::Value(opts.engine_str);
    root_out["case_id"] = json::Value("UNKNOWN");
    root_out["status"] = json::Value("FAIL");
    root_out["configuration_initialized"] = json::Value(false);
    root_out["neighbor_configuration_version"] = json::Value(2);
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
    wb_init["cli_requested_limit"] = json::Value(static_cast<int64_t>(opts.cli_max_work_set ? opts.cli_max_work : CANONICAL_MAX_WORK_CAP));
    wb_init["effective_limit"] = json::Value(static_cast<int64_t>(opts.cli_max_work_set ? std::min(opts.cli_max_work, CANONICAL_MAX_WORK_CAP) : CANONICAL_MAX_WORK_CAP));
    root_out["work_budget"] = wb_init;

    ExitCode final_exit_code = ExitCode::SUCCESS;
    size_t attempted_steps = 0;
    size_t committed_steps = 0;
    std::vector<std::string> serialized_steps;
    bool continue_on_error = opts.continue_on_error;
    const auto fatal_delivery = [&](const char* detail) {
        std::fprintf(stderr, "Fatal RESULT_DELIVERY_ERROR: %s (attempted_steps=%zu committed_steps=%zu)\n",
                     detail, attempted_steps, committed_steps);
    };

    try {
        json::Value root_in = json::Parser::parse(input_src);
        ProfilingTimePoint parse_t1 = ProfilingClock::now();
        run_profiling.input_parsing_ns = std::chrono::duration_cast<ProfilingNanoseconds>(parse_t1 - parse_t0).count();
        macro_timing.input_parsing_ns = run_profiling.input_parsing_ns;
        notify_observer(observer, MeasurementStage::PARSE_END, 0, observer_ctx);

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

        uint64_t cli_requested_limit = opts.cli_max_work_set ? opts.cli_max_work : CANONICAL_MAX_WORK_CAP;
        uint64_t effective_limit = CANONICAL_MAX_WORK_CAP;
        if (workload_requested_set) {
            effective_limit = std::min(effective_limit, workload_requested_limit);
        }
        if (opts.cli_max_work_set) {
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

        // Pre-loop Initialization (Codec + Graph + Query Plan + Sink + Streaming Engine + NLF Index)
        notify_observer(observer, MeasurementStage::INIT_START, 0, observer_ctx);
        ProfilingTimePoint init_wall_t0 = ProfilingClock::now();

        ApxTypedCodec codec;

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

        notify_observer(observer, MeasurementStage::GRAPH_INIT_START, 0, observer_ctx);
        ProfilingTimePoint ginit_t0 = ProfilingClock::now();
        ApxAuthoritativeGraph graph;
        if (!root_in.contains("initial_graph")) {
            throw std::invalid_argument("Workload missing required 'initial_graph' object");
        }
        const auto& init_g = root_in["initial_graph"];
        const auto& init_v_arr = init_g["vertices"].as_array();
        const auto& init_e_arr = init_g["edges"].as_array();

        std::vector<std::pair<uint32_t, uint32_t>> base_vertices;
        base_vertices.reserve(init_v_arr.size());
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
        base_edges.reserve(init_e_arr.size());
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
        ProfilingTimePoint ginit_t1 = ProfilingClock::now();
        run_profiling.graph_init_ns = std::chrono::duration_cast<ProfilingNanoseconds>(ginit_t1 - ginit_t0).count();
        macro_timing.graph_init_ns = run_profiling.graph_init_ns;
        notify_observer(observer, MeasurementStage::GRAPH_INIT_END, 0, observer_ctx);

        notify_observer(observer, MeasurementStage::QUERY_PREP_START, 0, observer_ctx);
        ProfilingTimePoint qp_t0 = ProfilingClock::now();
        uint32_t num_q = static_cast<uint32_t>(q_ext_vids.size());
        std::vector<uint32_t> q_dense_vlabels(num_q, 0);
        for (size_t i = 0; i < q_ext_vids.size(); ++i) {
            uint32_t dense_u = codec.get_query_vertex(q_ext_vids[i]);
            q_dense_vlabels[dense_u] = codec.get_vertex_label_id(q_v_labels[i]);
        }

        std::vector<QueryEdge> q_dense_edges;
        q_dense_edges.reserve(q_ext_edges.size());
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
        macro_timing.query_prep_ns = run_profiling.query_prep_ns;
        notify_observer(observer, MeasurementStage::QUERY_PREP_END, 0, observer_ctx);

        notify_observer(observer, MeasurementStage::ENGINE_INIT_START, 0, observer_ctx);
        ProfilingTimePoint eng_t0 = ProfilingClock::now();
        size_t max_nlf_memory_bytes = static_cast<size_t>(opts.max_nlf_memory_mib) * BYTES_PER_MIB;
        ApxTupleSink sink(codec, canonical_order);
        ApxStreamingEngine streaming_engine(
            graph, codec, sink, query_plan, engine_type, max_work,
            opts.nlf_mode, max_nlf_memory_bytes, opts.neighbor_mode
        );
        streaming_engine.set_profiling_enabled(profiling_enabled);
        ProfilingTimePoint eng_t1 = ProfilingClock::now();
        run_profiling.engine_and_nlf_init_ns = std::chrono::duration_cast<ProfilingNanoseconds>(eng_t1 - eng_t0).count();
        macro_timing.engine_and_nlf_init_ns = run_profiling.engine_and_nlf_init_ns;
        notify_observer(observer, MeasurementStage::ENGINE_INIT_END, 0, observer_ctx);

        ProfilingTimePoint init_wall_t1 = ProfilingClock::now();
        macro_timing.initialization_wall_ns = std::chrono::duration_cast<ProfilingNanoseconds>(init_wall_t1 - init_wall_t0).count();
        notify_observer(observer, MeasurementStage::INIT_END, 0, observer_ctx);

        root_out["configuration_initialized"] = json::Value(true);
        root_out["requested_neighbor_mode"] = json::Value(neighbor_mode_name(opts.neighbor_mode));
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

        if (!root_in.contains("updates") || !root_in["updates"].is_array()) {
            throw std::invalid_argument("Workload missing required 'updates' array");
        }
        const auto& upd_arr = root_in["updates"].as_array();
        size_t total_steps = upd_arr.size();
        size_t delivered_steps = 0;
        size_t completed_steps = 0;
        size_t failed_steps = 0;
        serialized_steps.reserve(total_steps);

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

        auto parse_fault_hooks = [&](const json::Value& upd_val, std::string& fault_out) {
            using FaultPoint = ApxTransactionManager::FaultPoint;
            fault_out.clear();
            if (upd_val.contains("fault") && upd_val["fault"].is_string()) {
                fault_out = upd_val["fault"].as_string();
            }
            auto tx_fault = FaultPoint::NONE;
            auto m2_fault = M2FaultHook::NONE;
            if (fault_out == "BEFORE_MUTATION") tx_fault = FaultPoint::BEFORE_MUTATION;
            else if (fault_out == "BETWEEN_FORWARD_REVERSE_MUTATION") tx_fault = FaultPoint::BETWEEN_FORWARD_REVERSE_MUTATION;
            else if (fault_out == "ALLOCATION_FAILURE_MUTATION") tx_fault = FaultPoint::ALLOCATION_FAILURE_MUTATION;
            else if (fault_out == "AFTER_MUTATION") tx_fault = FaultPoint::AFTER_MUTATION;
            else if (fault_out == "DURING_CALLBACK") tx_fault = FaultPoint::DURING_CALLBACK;
            else if (fault_out == "AFTER_NLF_PREPARE") tx_fault = FaultPoint::AFTER_NLF_PREPARE;
            else if (fault_out == "AFTER_GRAPH_BEFORE_NLF") tx_fault = FaultPoint::AFTER_GRAPH_BEFORE_NLF;
            else if (fault_out == "AFTER_FIRST_NLF_CELL") tx_fault = FaultPoint::AFTER_FIRST_NLF_CELL;
            else if (fault_out == "BEFORE_PUBLISH") tx_fault = FaultPoint::BEFORE_PUBLISH;
            else if (fault_out == "AFTER_MISSING_COMMITMENT") m2_fault = M2FaultHook::AFTER_MISSING_COMMITMENT;
            else if (fault_out == "AFTER_VERTEX_ASSIGNMENT") m2_fault = M2FaultHook::AFTER_VERTEX_ASSIGNMENT;
            streaming_engine.get_tx_manager().set_fault_injection(tx_fault);
            streaming_engine.set_m2_fault_hook(m2_fault);
            return tx_fault != FaultPoint::NONE || m2_fault != M2FaultHook::NONE;
        };

        if (deep_diagnostics_enabled) {
            // Correctness mode: exact legacy per-step behavior with full graph_state and codec_state reconstruction
            notify_observer(observer, MeasurementStage::PRIMARY_TIMER_START, 0, observer_ctx);
            ProfilingTimePoint stream_t0 = ProfilingClock::now();

            for (size_t step_idx = 0; step_idx < total_steps; ++step_idx) {
                notify_observer(observer, MeasurementStage::STEP_EXECUTE_START, step_idx, observer_ctx);
                const auto& upd_val = upd_arr[step_idx];
                auto step_json = make_step_record(step_idx, "NOT_EXECUTED");
                const char* execution_status = nullptr;
                const char* delivery_stage = nullptr;
                bool committed = false;
                bool failed = false;
                ++attempted_steps;
                using FaultPoint = ApxTransactionManager::FaultPoint;
                std::string fault;
                bool has_active_fault = false;
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

                    has_active_fault = parse_fault_hooks(upd_val, fault);
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

                    json::Value step_cnt = json::Value::make_object();
                    populate_counters_json(step_cnt, step_res.step_counters, engine_type);
                    step_json["step_counters"] = step_cnt;

                    json::Value c_state = json::Value::make_object();
                    c_state["query_vertices_count"] = json::Value(codec.query_vertices_count());
                    c_state["data_vertices_count"] = json::Value(codec.data_vertices_count());
                    c_state["vertex_labels_count"] = json::Value(codec.vertex_labels_count());
                    c_state["edge_labels_count"] = json::Value(codec.edge_labels_count());
                    step_json["codec_state"] = c_state;

                    delivery_stage = "GRAPH_DIAGNOSTICS";
                    if (fault == "REPORT_GRAPH_DIAGNOSTICS") throw std::bad_alloc();
                    notify_observer(observer, MeasurementStage::CONSTRUCT_GRAPH_DIAGNOSTICS, step_idx, observer_ctx);
                    step_json["graph_state"] = build_graph_state_json(graph, codec);
                    if (has_active_fault
                        || (opts.nlf_mode == NlfMode::RESIDUAL && step_res.status == StepStatus::CALLBACK_ABORTED
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
                    serialized_steps.push_back(serialize_step_json_with_tuples(
                        std::move(step_json), step_res.entered_tuples, step_res.exited_tuples));
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
                        if (upd_val.contains("fault") && upd_val["fault"].is_string()
                            && upd_val["fault"].as_string().rfind("REPORT_", 0) == 0) {
                            notify_observer(observer, MeasurementStage::CONSTRUCT_GRAPH_DIAGNOSTICS, step_idx, observer_ctx);
                            failure_record["graph_state"] = build_graph_state_json(graph, codec);
                            if (const auto* index = streaming_engine.get_nlf_index()) {
                                failure_record["nlf_full_cell_reconstruction"] = json::Value(
                                    verify_index_reconstruction(graph, streaming_engine.get_query_signatures(), *index));
                            }
                        }
                        serialized_steps.push_back(json::serialize(failure_record, 0));
                    } else {
                        const bool exhausted = dynamic_cast<const std::bad_alloc*>(&e) != nullptr;
                        const StepFailure failure = exhausted
                            ? StepFailure{"RESOURCE_LIMIT_ERROR", ExitCode::RESOURCE_LIMIT_ERROR}
                            : StepFailure{"INPUT_SCHEMA_ERROR", ExitCode::INPUT_PARSE_ERROR};
                        remember_failure(step_idx, failure, e.what());
                        step_json["status"] = json::Value("ERROR");
                        step_json["error"] = json::Value(e.what());
                        step_json["error_category"] = json::Value(failure.category);
                        serialized_steps.push_back(json::serialize(step_json, 0));
                        ++delivered_steps;
                    }
                }
                if (failed) ++failed_steps;
                else ++completed_steps;
                notify_observer(observer, MeasurementStage::STEP_EXECUTE_END, step_idx, observer_ctx);

                const bool poisoned_or_oom = streaming_engine.get_tx_manager().is_session_poisoned()
                    || (first_failing_exit == ExitCode::RESOURCE_LIMIT_ERROR);
                if (delivery_failed || (failed && !continue_on_error) || poisoned_or_oom) {
                    for (size_t rem = step_idx + 1; rem < total_steps; ++rem) {
                        serialized_steps.push_back(json::serialize(unattempted_update(rem, upd_arr[rem]), 0));
                    }
                    break;
                }
            }

            ProfilingTimePoint stream_t1 = ProfilingClock::now();
            notify_observer(observer, MeasurementStage::PRIMARY_TIMER_STOP, total_steps, observer_ctx);
            run_profiling.streaming_exec_ns = std::chrono::duration_cast<ProfilingNanoseconds>(stream_t1 - stream_t0).count();
            macro_timing.streaming_loop_wall_ns = run_profiling.streaming_exec_ns;

            notify_observer(observer, MeasurementStage::REPORTING_START, 0, observer_ctx);
            ProfilingTimePoint report_t0 = ProfilingClock::now();

            json::Value exec_obj = json::Value::make_object();
            exec_obj["total_steps"] = json::Value(static_cast<int64_t>(total_steps));
            exec_obj["attempted_steps"] = json::Value(static_cast<int64_t>(attempted_steps));
            exec_obj["committed_steps"] = json::Value(static_cast<int64_t>(committed_steps));
            exec_obj["delivered_steps"] = json::Value(static_cast<int64_t>(delivered_steps));
            exec_obj["completed_steps"] = json::Value(static_cast<int64_t>(completed_steps));
            exec_obj["failed_steps"] = json::Value(static_cast<int64_t>(failed_steps));
            exec_obj["unattempted_steps"] = json::Value(static_cast<int64_t>(total_steps - attempted_steps));
            root_out["execution"] = exec_obj;

            const auto& counters = streaming_engine.get_cumulative_counters();
            json::Value cnt_json = json::Value::make_object();
            populate_counters_json(cnt_json, counters, engine_type);
            cnt_json["raw_emissions"] = json::Value(sink.raw_emissions());
            cnt_json["unique_emissions"] = json::Value(sink.unique_emissions());
            cnt_json["committed_emissions"] = json::Value(sink.committed_emissions());
            cnt_json["discarded_emissions"] = json::Value(sink.discarded_emissions());
            root_out["counters"] = cnt_json;

            if (profiling_enabled) {
                json::Value p_root = json::Value::make_object();
                p_root["enabled"] = json::Value(true);
                p_root["profiling_schema_version"] = json::Value("1.2.0");
                p_root["input_parsing_ns"] = json::Value(static_cast<int64_t>(run_profiling.input_parsing_ns));
                p_root["graph_init_ns"] = json::Value(static_cast<int64_t>(run_profiling.graph_init_ns));
                p_root["query_prep_ns"] = json::Value(static_cast<int64_t>(run_profiling.query_prep_ns));
                p_root["streaming_loop_wall_ns"] = json::Value(static_cast<int64_t>(run_profiling.streaming_exec_ns));
                p_root["streaming_exec_ns"] = json::Value(static_cast<int64_t>(run_profiling.streaming_exec_ns));

                const auto& cp = streaming_engine.get_cumulative_profiling();
                p_root["native_step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));

                json::Value p_cum = json::Value::make_object();
                p_cum["step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));
                p_cum["update_maintenance_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_update_maintenance_ns));
                p_cum["search_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_ns));
                p_cum["neighbor_retrieval_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_neighbor_retrieval_ns));
                p_cum["search_core_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_core_ns));
                p_cum["tuple_commit_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
                p_cum["tuple_sink_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
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

            ProfilingTimePoint report_t1 = ProfilingClock::now();
            macro_timing.reporting_wall_ns = std::chrono::duration_cast<ProfilingNanoseconds>(report_t1 - report_t0).count();
            notify_observer(observer, MeasurementStage::REPORTING_END, 0, observer_ctx);
        } else {
            // PERFORMANCE (PROFILE_OFF) or DIAGNOSTIC (PROFILE_ON) mode:
            // Exclude deep graph/codec diagnostics and per-step JSON DOM construction from the timed loop.
            std::vector<RetainedStepOutcome> retained_steps;
            retained_steps.reserve(total_steps);

            notify_observer(observer, MeasurementStage::PRIMARY_TIMER_START, 0, observer_ctx);
            ProfilingTimePoint stream_t0 = ProfilingClock::now();

            for (size_t step_idx = 0; step_idx < total_steps; ++step_idx) {
                notify_observer(observer, MeasurementStage::STEP_EXECUTE_START, step_idx, observer_ctx);
                const auto& upd_val = upd_arr[step_idx];
                RetainedStepOutcome outcome;
                outcome.step_index = step_idx;
                bool failed = false;
                bool stop_for_delivery_fault = false;
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
                    outcome.has_op = true;
                    outcome.canonical_op = (*canonical_op == '+' ? "+" : "-");

                    if (!upd_val.contains("u") || !upd_val["u"].is_int() || !upd_val.contains("v") || !upd_val["v"].is_int()) {
                        throw std::invalid_argument("Update edge endpoints 'u' and 'v' must be integers");
                    }
                    int64_t ext_u = upd_val["u"].as_int();
                    int64_t ext_v = upd_val["v"].as_int();
                    TypedLabel lbl = parse_typed_label(upd_val["label"]);
                    outcome.ext_u = ext_u;
                    outcome.ext_v = ext_v;
                    outcome.has_label = true;
                    outcome.label = lbl;

                    if (upd_val.contains("fault") && upd_val["fault"].is_string()) {
                        parse_fault_hooks(upd_val, outcome.fault_tag);
                    }

                    UpdateOperation upd{op, ext_u, ext_v, lbl};
                    outcome.step_res = streaming_engine.execute_step(upd, static_cast<uint32_t>(step_idx));
                    outcome.has_step_res = true;

                    if (outcome.step_res.committed) {
                        ++committed_steps;
                    }
                    failed = (outcome.step_res.status != StepStatus::SUCCESS);
                    if (failed) {
                        auto failure = step_failure(outcome.step_res.status, outcome.step_res.error_message);
                        remember_failure(step_idx, failure, outcome.step_res.error_message);
                    }
                    // Post-commit reporting faults tested outside graph diagnostics still abort step delivery
                    if (outcome.fault_tag == "REPORT_AFTER_COMMIT" || outcome.fault_tag == "REPORT_RESULT_JSON" || outcome.fault_tag == "REPORT_APPEND_STEP") {
                        failed = true;
                        stop_for_delivery_fault = true;
                    }
                } catch (const std::exception& e) {
                    failed = true;
                    outcome.pre_exec_error = true;
                    const bool exhausted = dynamic_cast<const std::bad_alloc*>(&e) != nullptr;
                    outcome.pre_exec_failure = exhausted
                        ? StepFailure{"RESOURCE_LIMIT_ERROR", ExitCode::RESOURCE_LIMIT_ERROR}
                        : StepFailure{"INPUT_SCHEMA_ERROR", ExitCode::INPUT_PARSE_ERROR};
                    outcome.pre_exec_message = e.what();
                    remember_failure(step_idx, outcome.pre_exec_failure, e.what());
                }

                if (failed) ++failed_steps;
                else ++completed_steps;

                retained_steps.push_back(std::move(outcome));
                notify_observer(observer, MeasurementStage::STEP_EXECUTE_END, step_idx, observer_ctx);

                const bool poisoned_or_oom = streaming_engine.get_tx_manager().is_session_poisoned()
                    || (first_failing_exit == ExitCode::RESOURCE_LIMIT_ERROR);
                if (stop_for_delivery_fault || (failed && !continue_on_error) || poisoned_or_oom) {
                    break;
                }
            }

            ProfilingTimePoint stream_t1 = ProfilingClock::now();
            notify_observer(observer, MeasurementStage::PRIMARY_TIMER_STOP, total_steps, observer_ctx);
            run_profiling.streaming_exec_ns = std::chrono::duration_cast<ProfilingNanoseconds>(stream_t1 - stream_t0).count();
            macro_timing.streaming_loop_wall_ns = run_profiling.streaming_exec_ns;

            // Deferred Output Construction & Serialization DOM Assembly (strictly AFTER timer stop)
            notify_observer(observer, MeasurementStage::REPORTING_START, 0, observer_ctx);
            ProfilingTimePoint report_t0 = ProfilingClock::now();

            for (auto& rstep : retained_steps) {
                const size_t s_idx = rstep.step_index;
                if (rstep.pre_exec_error) {
                    auto step_json = make_step_record(s_idx, "ERROR");
                    if (rstep.has_op) step_json["op"] = json::Value(rstep.canonical_op);
                    step_json["u"] = json::Value(rstep.ext_u);
                    step_json["v"] = json::Value(rstep.ext_v);
                    if (rstep.has_label) step_json["label"] = typed_label_to_json(rstep.label);
                    step_json["error"] = json::Value(rstep.pre_exec_message);
                    step_json["error_category"] = json::Value(rstep.pre_exec_failure.category);
                    serialized_steps.push_back(json::serialize(step_json, 0));
                    ++delivered_steps;
                    continue;
                }

                const char* execution_status = step_status_to_string(rstep.step_res.status);
                const char* delivery_stage = nullptr;
                auto step_json = make_step_record(s_idx, "NOT_EXECUTED");
                try {
                    step_json["op"] = json::Value(rstep.canonical_op);
                    step_json["u"] = json::Value(rstep.ext_u);
                    step_json["v"] = json::Value(rstep.ext_v);
                    step_json["label"] = typed_label_to_json(rstep.label);

                    delivery_stage = "AFTER_EXECUTION";
                    if (rstep.fault_tag == "REPORT_AFTER_COMMIT") throw std::bad_alloc();

                    step_json["status"] = json::Value(execution_status);
                    step_json["error"] = json::Value(rstep.step_res.error_message);
                    step_json["commit_status"] = json::Value(rstep.step_res.committed ? "COMMITTED" : "NOT_COMMITTED");
                    if (rstep.step_res.status != StepStatus::SUCCESS) {
                        auto failure = step_failure(rstep.step_res.status, rstep.step_res.error_message);
                        step_json["error_category"] = json::Value(failure.category);
                    }

                    delivery_stage = "RESULT_JSON";
                    if (rstep.fault_tag == "REPORT_RESULT_JSON") throw std::bad_alloc();

                    json::Value step_cnt = json::Value::make_object();
                    populate_counters_json(step_cnt, rstep.step_res.step_counters, engine_type);
                    step_json["step_counters"] = std::move(step_cnt);

                    if (profiling_enabled) {
                        json::Value p_step = json::Value::make_object();
                        p_step["step_total_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.step_total_ns));
                        p_step["update_maintenance_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.update_maintenance_ns));
                        p_step["search_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.search_ns));
                        p_step["neighbor_retrieval_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.neighbor_retrieval_ns));
                        p_step["search_core_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.search_core_ns));
                        p_step["tuple_commit_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.tuple_commit_ns));
                        p_step["tuple_sink_ns"] = json::Value(static_cast<int64_t>(rstep.step_res.step_profiling.tuple_sink_ns));
                        step_json["profiling"] = std::move(p_step);
                    }

                    delivery_stage = "APPEND_STEP";
                    if (rstep.fault_tag == "REPORT_APPEND_STEP") throw std::bad_alloc();
                    serialized_steps.push_back(serialize_step_json_with_tuples(
                        std::move(step_json), rstep.step_res.entered_tuples, rstep.step_res.exited_tuples));
                    std::vector<std::vector<int64_t>>().swap(rstep.step_res.entered_tuples);
                    std::vector<std::vector<int64_t>>().swap(rstep.step_res.exited_tuples);
                    ++delivered_steps;
                } catch (const std::exception& e) {
                    delivery_failed = true;
                    delivery_error = e.what();
                    remember_failure(s_idx, {"RESULT_DELIVERY_ERROR", ExitCode::RESULT_DELIVERY_ERROR}, delivery_error);
                    auto failure_record = make_step_record(s_idx, "DELIVERY_FAILED");
                    failure_record["commit_status"] = json::Value(rstep.step_res.committed ? "COMMITTED" : "NOT_COMMITTED");
                    failure_record["execution_status"] = json::Value(execution_status);
                    failure_record["delivery_status"] = json::Value("FAILED");
                    failure_record["delivery_stage"] = json::Value(delivery_stage ? delivery_stage : "RESULT_JSON");
                    failure_record["error"] = json::Value(delivery_error);
                    failure_record["error_category"] = json::Value("RESULT_DELIVERY_ERROR");
                    serialized_steps.push_back(json::serialize(failure_record, 0));
                }
            }

            for (size_t rem = retained_steps.size(); rem < total_steps; ++rem) {
                serialized_steps.push_back(json::serialize(unattempted_update(rem, upd_arr[rem]), 0));
            }

            json::Value exec_obj = json::Value::make_object();
            exec_obj["total_steps"] = json::Value(static_cast<int64_t>(total_steps));
            exec_obj["attempted_steps"] = json::Value(static_cast<int64_t>(attempted_steps));
            exec_obj["committed_steps"] = json::Value(static_cast<int64_t>(committed_steps));
            exec_obj["delivered_steps"] = json::Value(static_cast<int64_t>(delivered_steps));
            exec_obj["completed_steps"] = json::Value(static_cast<int64_t>(completed_steps));
            exec_obj["failed_steps"] = json::Value(static_cast<int64_t>(failed_steps));
            exec_obj["unattempted_steps"] = json::Value(static_cast<int64_t>(total_steps - attempted_steps));
            root_out["execution"] = exec_obj;

            const auto& counters = streaming_engine.get_cumulative_counters();
            json::Value cnt_json = json::Value::make_object();
            populate_counters_json(cnt_json, counters, engine_type);
            cnt_json["raw_emissions"] = json::Value(sink.raw_emissions());
            cnt_json["unique_emissions"] = json::Value(sink.unique_emissions());
            cnt_json["committed_emissions"] = json::Value(sink.committed_emissions());
            cnt_json["discarded_emissions"] = json::Value(sink.discarded_emissions());
            root_out["counters"] = cnt_json;

            if (profiling_enabled) {
                json::Value p_root = json::Value::make_object();
                p_root["enabled"] = json::Value(true);
                p_root["profiling_schema_version"] = json::Value("1.2.0");
                p_root["input_parsing_ns"] = json::Value(static_cast<int64_t>(run_profiling.input_parsing_ns));
                p_root["graph_init_ns"] = json::Value(static_cast<int64_t>(run_profiling.graph_init_ns));
                p_root["query_prep_ns"] = json::Value(static_cast<int64_t>(run_profiling.query_prep_ns));
                p_root["streaming_loop_wall_ns"] = json::Value(static_cast<int64_t>(run_profiling.streaming_exec_ns));
                p_root["streaming_exec_ns"] = json::Value(static_cast<int64_t>(run_profiling.streaming_exec_ns));

                const auto& cp = streaming_engine.get_cumulative_profiling();
                p_root["native_step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));

                json::Value p_cum = json::Value::make_object();
                p_cum["step_total_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_step_total_ns));
                p_cum["update_maintenance_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_update_maintenance_ns));
                p_cum["search_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_ns));
                p_cum["neighbor_retrieval_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_neighbor_retrieval_ns));
                p_cum["search_core_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_search_core_ns));
                p_cum["tuple_commit_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
                p_cum["tuple_sink_ns"] = json::Value(static_cast<int64_t>(cp.cumulative_tuple_sink_ns));
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

            ProfilingTimePoint report_t1 = ProfilingClock::now();
            macro_timing.reporting_wall_ns = std::chrono::duration_cast<ProfilingNanoseconds>(report_t1 - report_t0).count();
            notify_observer(observer, MeasurementStage::REPORTING_END, 0, observer_ctx);
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
    } catch (const std::bad_alloc&) {
        if (attempted_steps != 0) {
            fatal_delivery("std::bad_alloc");
            return static_cast<int>(ExitCode::RESULT_DELIVERY_ERROR);
        }
        root_out["status"] = json::Value("FAIL");
        root_out["error_category"] = json::Value("RESOURCE_LIMIT_ERROR");
        root_out["error_message"] = json::Value("std::bad_alloc");
        final_exit_code = ExitCode::RESOURCE_LIMIT_ERROR;
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

    if (opts.explicit_execution_purpose) {
        attach_measurement_metadata(
            root_out, opts.execution_purpose, profiling_enabled, deep_diagnostics_enabled, macro_timing
        );
    }

    try {
        std::string err_msg;
        if (!write_root_with_steps_streamed(opts.output_path, root_out, serialized_steps, err_msg)) {
            std::fprintf(stderr, "Output write failure: ");
            fatal_delivery(err_msg.c_str());
            return static_cast<int>(ExitCode::IO_ERROR);
        }
        if (!opts.profile_output_path.empty() && profiling_enabled && root_out.contains("profiling")) {
            if (!write_json_file_safe(opts.profile_output_path, json::serialize(root_out["profiling"], 2), err_msg)) {
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

} // namespace rapidflow_apx
