#include "harness_runner.h"
#include "p4b_tests.h"
#include "rapidflow_apx/json_mini.h"

#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace rapidflow_apx {
namespace {

struct RecordedStage {
    MeasurementStage stage;
    uint64_t step_idx;
};

struct ObserverTrace {
    std::vector<RecordedStage> events;
    int diag_in_timed{0};
    bool in_timer{false};
};

void record_boundary_stage(MeasurementStage stage, uint64_t step_idx, void* ctx) {
    auto* tr = static_cast<ObserverTrace*>(ctx);
    if (stage == MeasurementStage::PRIMARY_TIMER_START) tr->in_timer = true;
    else if (stage == MeasurementStage::PRIMARY_TIMER_STOP) tr->in_timer = false;
    else if (stage == MeasurementStage::CONSTRUCT_GRAPH_DIAGNOSTICS && tr->in_timer) tr->diag_in_timed++;
    tr->events.push_back({stage, step_idx});
}

std::string read_text(const std::string& path) {
    std::ifstream ifs(path);
    if (!ifs.is_open()) throw std::runtime_error("Cannot read temp file: " + path);
    return std::string((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
}

int stage_pos(const std::vector<RecordedStage>& ev, MeasurementStage target) {
    for (size_t i = 0; i < ev.size(); ++i) {
        if (ev[i].stage == target) return static_cast<int>(i);
    }
    return -1;
}

} // namespace

int run_measurement_seam_tests() {
    std::cout << "Running P4b-3A native measurement boundary & diagnostic isolation tests...\n";
    const pid_t pid = getpid();
    const std::string wl_path = "/tmp/p4b_meas_seam_wl_" + std::to_string(pid) + ".json";
    const std::string wl_fault_path = "/tmp/p4b_meas_seam_fault_" + std::to_string(pid) + ".json";
    const std::string out_perf = "/tmp/p4b_meas_seam_perf_" + std::to_string(pid) + ".json";
    const std::string out_corr = "/tmp/p4b_meas_seam_corr_" + std::to_string(pid) + ".json";
    const std::string out_diag = "/tmp/p4b_meas_seam_diag_" + std::to_string(pid) + ".json";
    const std::string out_flt_c = "/tmp/p4b_meas_seam_fltc_" + std::to_string(pid) + ".json";
    const std::string out_flt_p = "/tmp/p4b_meas_seam_fltp_" + std::to_string(pid) + ".json";

    const auto cleanup = [&]() {
        std::remove(wl_path.c_str());
        std::remove(wl_fault_path.c_str());
        std::remove(out_perf.c_str());
        std::remove(out_corr.c_str());
        std::remove(out_diag.c_str());
        std::remove(out_flt_c.c_str());
        std::remove(out_flt_p.c_str());
    };

    try {
        // Diamond k=1 fixture
        const std::string wl_json = R"({
  "case_id": "SEAM_DIAMOND_K1",
  "k": 1,
  "max_work": 500000,
  "query": {
    "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}, {"id": 3, "label": 1}],
    "edges": [{"u": 0, "v": 1, "label": 1}, {"u": 0, "v": 2, "label": 1}, {"u": 1, "v": 2, "label": 1}, {"u": 1, "v": 3, "label": 1}, {"u": 2, "v": 3, "label": 1}]
  },
  "initial_graph": {
    "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}, {"id": 3, "label": 1}],
    "edges": [{"u": 0, "v": 1, "label": 1}, {"u": 0, "v": 2, "label": 1}, {"u": 1, "v": 3, "label": 1}]
  },
  "updates": [
    {"op": "+", "u": 2, "v": 3, "label": 1},
    {"op": "-", "u": 2, "v": 3, "label": 1}
  ]
})";
        {
            std::ofstream ofs(wl_path);
            ofs << wl_json;
        }

        // Test 1: Boundary order + zero CONSTRUCT_GRAPH_DIAGNOSTICS inside performance timed region
        for (auto cfg : {
            std::make_pair(NlfMode::NONE, NeighborMode::LEGACY),
            std::make_pair(NlfMode::STATIC, NeighborMode::LEGACY),
            std::make_pair(NlfMode::RESIDUAL, NeighborMode::LEGACY),
            std::make_pair(NlfMode::NONE, NeighborMode::MERGE),
        }) {
            ObserverTrace tr_perf;
            HarnessOptions o_perf;
            o_perf.workload_path = wl_path;
            o_perf.output_path = out_perf;
            o_perf.nlf_mode = cfg.first;
            o_perf.neighbor_mode = cfg.second;
            o_perf.execution_purpose = ExecutionPurpose::PERFORMANCE;
            o_perf.explicit_execution_purpose = true;
            o_perf.apply_process_limits = false;

            int rc_p = run_workload_with_options(o_perf, record_boundary_stage, &tr_perf);
            if (rc_p != 0) throw std::runtime_error("Performance mode failed");
            if (tr_perf.diag_in_timed != 0) throw std::runtime_error("Graph diagnostics executed inside timed loop in performance mode");

            int p_init_start = stage_pos(tr_perf.events, MeasurementStage::INIT_START);
            int p_init_end = stage_pos(tr_perf.events, MeasurementStage::INIT_END);
            int p_tstart = stage_pos(tr_perf.events, MeasurementStage::PRIMARY_TIMER_START);
            int p_tstop = stage_pos(tr_perf.events, MeasurementStage::PRIMARY_TIMER_STOP);
            int p_rstart = stage_pos(tr_perf.events, MeasurementStage::REPORTING_START);
            int p_rend = stage_pos(tr_perf.events, MeasurementStage::REPORTING_END);
            if (!(p_init_start < p_init_end && p_init_end < p_tstart && p_tstart < p_tstop && p_tstop < p_rstart && p_rstart < p_rend)) {
                throw std::runtime_error("Invalid macro boundary ordering in performance mode");
            }
            if (stage_pos(tr_perf.events, MeasurementStage::CONSTRUCT_GRAPH_DIAGNOSTICS) != -1) {
                throw std::runtime_error("CONSTRUCT_GRAPH_DIAGNOSTICS unexpectedly invoked in performance mode");
            }

            // Correctness mode parity + diagnostics verification
            ObserverTrace tr_corr;
            HarnessOptions o_corr;
            o_corr.workload_path = wl_path;
            o_corr.output_path = out_corr;
            o_corr.nlf_mode = cfg.first;
            o_corr.neighbor_mode = cfg.second;
            o_corr.execution_purpose = ExecutionPurpose::CORRECTNESS;
            o_corr.explicit_execution_purpose = false; // default frozen compatibility
            o_corr.apply_process_limits = false;

            int rc_c = run_workload_with_options(o_corr, record_boundary_stage, &tr_corr);
            if (rc_c != 0) throw std::runtime_error("Correctness mode failed");
            if (tr_corr.diag_in_timed != 2) throw std::runtime_error("Expected 2 graph diagnostic calls in correctness mode");

            // Diagnostic mode check (PROFILE_ON, no deep graph diagnostics)
            ObserverTrace tr_diag;
            HarnessOptions o_diag;
            o_diag.workload_path = wl_path;
            o_diag.output_path = out_diag;
            o_diag.nlf_mode = cfg.first;
            o_diag.neighbor_mode = cfg.second;
            o_diag.execution_purpose = ExecutionPurpose::DIAGNOSTIC;
            o_diag.explicit_execution_purpose = true;
            o_diag.apply_process_limits = false;

            int rc_d = run_workload_with_options(o_diag, record_boundary_stage, &tr_diag);
            if (rc_d != 0) throw std::runtime_error("Diagnostic mode failed");
            if (tr_diag.diag_in_timed != 0) throw std::runtime_error("Deep graph diagnostics must be excluded in diagnostic mode");

            json::Value jp = json::Parser::parse(read_text(out_perf));
            json::Value jc = json::Parser::parse(read_text(out_corr));
            json::Value jd = json::Parser::parse(read_text(out_diag));

            if (jp["measurement_schema_version"].as_int() != 1 || jp["execution_purpose"].as_string() != "performance" ||
                jp["profiling_enabled"].as_bool() || jp["deep_diagnostics_enabled"].as_bool()) {
                throw std::runtime_error("Performance metadata mismatch");
            }
            if (jp.contains("profiling")) throw std::runtime_error("Performance output must not contain profiling block");
            if (jc.contains("measurement_schema_version")) throw std::runtime_error("Default correctness output must preserve historical JSON root keys");
            if (!jd["profiling_enabled"].as_bool() || !jd.contains("profiling")) {
                throw std::runtime_error("Diagnostic output missing profiling");
            }
            if (jp["macro_timing"]["streaming_loop_wall_ns"].as_int() <= 0) {
                throw std::runtime_error("Performance streaming_loop_wall_ns must be > 0");
            }
            if (jp["macro_timing"]["reporting_wall_ns"].as_int() <= 0) {
                throw std::runtime_error("Performance reporting_wall_ns must be > 0");
            }
            if (jp["counters"]["committed_emissions"].as_int() != jc["counters"]["committed_emissions"].as_int() ||
                jp["counters"]["work_units"].as_int() != jc["counters"]["work_units"].as_int()) {
                throw std::runtime_error("Performance vs correctness counter mismatch");
            }
            if (jp["steps"][0].contains("graph_state") || jp["steps"][0].contains("codec_state")) {
                throw std::runtime_error("Performance step output leaked graph_state or codec_state");
            }
            if (!jc["steps"][0].contains("graph_state") || !jc["steps"][0].contains("codec_state")) {
                throw std::runtime_error("Correctness step output missing graph_state or codec_state");
            }
            if (jp["steps"][0]["entered_tuples"].as_array().size() != 8 || jc["steps"][0]["entered_tuples"].as_array().size() != 8) {
                throw std::runtime_error("Expected 8 ENTERED tuples on A1 Diamond k=1");
            }
        }

        // Test 2: Runtime behavioral proof using REPORT_GRAPH_DIAGNOSTICS fault hook
        // In correctness mode this fails at GRAPH_DIAGNOSTICS (exit 9).
        // In performance mode GRAPH_DIAGNOSTICS never runs, so it succeeds (exit 0)!
        const std::string wl_fault_json = R"({
  "case_id": "SEAM_FAULT_PROOF",
  "k": 1,
  "max_work": 500000,
  "query": {
    "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}, {"id": 3, "label": 1}],
    "edges": [{"u": 0, "v": 1, "label": 1}, {"u": 0, "v": 2, "label": 1}, {"u": 1, "v": 2, "label": 1}, {"u": 1, "v": 3, "label": 1}, {"u": 2, "v": 3, "label": 1}]
  },
  "initial_graph": {
    "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}, {"id": 3, "label": 1}],
    "edges": [{"u": 0, "v": 1, "label": 1}, {"u": 0, "v": 2, "label": 1}, {"u": 1, "v": 3, "label": 1}]
  },
  "updates": [
    {"op": "+", "u": 2, "v": 3, "label": 1, "fault": "REPORT_GRAPH_DIAGNOSTICS"}
  ]
})";
        {
            std::ofstream ofs(wl_fault_path);
            ofs << wl_fault_json;
        }
        HarnessOptions o_fc;
        o_fc.workload_path = wl_fault_path;
        o_fc.output_path = out_flt_c;
        o_fc.execution_purpose = ExecutionPurpose::CORRECTNESS;
        o_fc.apply_process_limits = false;
        int rc_fc = run_workload_with_options(o_fc);
        if (rc_fc != static_cast<int>(ExitCode::RESULT_DELIVERY_ERROR)) {
            throw std::runtime_error("Expected correctness mode to fail at REPORT_GRAPH_DIAGNOSTICS");
        }

        HarnessOptions o_fp;
        o_fp.workload_path = wl_fault_path;
        o_fp.output_path = out_flt_p;
        o_fp.execution_purpose = ExecutionPurpose::PERFORMANCE;
        o_fp.explicit_execution_purpose = true;
        o_fp.apply_process_limits = false;
        int rc_fp = run_workload_with_options(o_fp);
        if (rc_fp != 0) {
            throw std::runtime_error("Performance mode unexpectedly entered graph diagnostics branch");
        }

        // Test 3: CLI conflict rejection (performance + profile)
        HarnessOptions o_bad;
        o_bad.workload_path = wl_path;
        o_bad.output_path = out_perf;
        o_bad.execution_purpose = ExecutionPurpose::PERFORMANCE;
        o_bad.explicit_execution_purpose = true;
        o_bad.profile_flag_seen = true;
        o_bad.apply_process_limits = false;
        if (run_workload_with_options(o_bad) != static_cast<int>(ExitCode::CLI_ARG_ERROR)) {
            throw std::runtime_error("Expected CLI_ARG_ERROR when combining performance mode and --profile");
        }

        cleanup();
        std::cout << "{\"test\": \"p4b_measurement_seam\", \"status\": \"PASS\"}\n";
        return 0;
    } catch (const std::exception& e) {
        cleanup();
        std::cerr << "FAILED p4b measurement seam test: " << e.what() << "\n";
        return 1;
    }
}

} // namespace rapidflow_apx
