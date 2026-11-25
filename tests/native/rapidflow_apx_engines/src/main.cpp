#include <algorithm>
#include <charconv>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "harness_runner.h"
#include "p4b_tests.h"
#include "rapidflow_apx/m2_event_engine.h"
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/streaming_engine.h"

using namespace rapidflow_apx;

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
        err = "Value must be strictly positive (got 0)";
        return false;
    }
    return true;
}

static int run_native_empty_frontier_test() {
    std::cout << "Running native empty-frontier invariant witness test...\n";
    try {
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

        std::vector<uint32_t> mapping_neg(4, ApxM2EventEngine::UNASSIGNED);
        mapping_neg[0] = 10;
        mapping_neg[1] = 20;
        std::vector<EdgeCommitment> status_neg(5, EdgeCommitment::MISSING);
        status_neg[0] = EdgeCommitment::PRESENT;

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

static bool parse_nlf_mode_cli(const std::string& text, NlfMode& mode) noexcept {
    if (text == "none") {
        mode = NlfMode::NONE;
        return true;
    }
    if (text == "static") {
        mode = NlfMode::STATIC;
        return true;
    }
    if (text == "residual") {
        mode = NlfMode::RESIDUAL;
        return true;
    }
    return false;
}

int main(int argc, char* argv[]) {
    HarnessOptions opts;

    bool seen_engine = false;
    bool seen_workload = false;
    bool seen_output = false;
    bool seen_max_work = false;
    bool seen_nlf_mode = false;
    bool seen_neighbor_mode = false;
    bool seen_max_nlf_memory_mib = false;
    bool seen_execution_purpose = false;
    bool seen_wall_timeout = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--test-empty-frontier") {
            return run_native_empty_frontier_test();
        } else if (arg == "--test-p4b-neighbor") {
            return run_neighbor_buffer_tests(i + 1 < argc ? argv[i + 1] : "graph");
        } else if (arg == "--test-p4b-residual") {
            return run_residual_native_tests(i + 1 < argc ? argv[i + 1] : "numeric");
        } else if (arg == "--test-p4b-measurement") {
            return run_measurement_seam_tests();
        } else if (arg == "--test-p4b") {
            std::string suite = "all";
            if (i + 1 < argc && argv[i + 1][0] != '-') {
                suite = argv[++i];
            }
            return run_p4b_native_tests(suite.c_str());
        } else if (arg == "--engine") {
            if (seen_engine || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --engine argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.engine_str = argv[++i];
            seen_engine = true;
        } else if (arg == "--workload") {
            if (seen_workload || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --workload argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.workload_path = argv[++i];
            seen_workload = true;
        } else if (arg == "--output") {
            if (seen_output || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --output argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.output_path = argv[++i];
            seen_output = true;
        } else if (arg == "--max-work") {
            if (seen_max_work || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --max-work argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            std::string parse_err;
            if (!parse_positive_uint64_exact(argv[++i], opts.cli_max_work, parse_err)) {
                std::cerr << "Error: Invalid --max-work argument: " << parse_err << "\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.cli_max_work_set = true;
            seen_max_work = true;
        } else if (arg == "--continue-on-error") {
            opts.continue_on_error = true;
        } else if (arg == "--neighbor-mode") {
            if (seen_neighbor_mode) {
                std::cerr << "Error: Duplicate --neighbor-mode argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing --neighbor-mode argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_neighbor_mode = true;
            const std::string mode = argv[++i];
            if (mode == "buffer") opts.neighbor_mode = NeighborMode::BUFFER;
            else if (mode == "merge") opts.neighbor_mode = NeighborMode::MERGE;
            else if (mode == "legacy") opts.neighbor_mode = NeighborMode::LEGACY;
            else {
                std::cerr << "Error: Invalid --neighbor-mode '" << mode << "'. Must be 'legacy', 'buffer', or 'merge'.\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
        } else if (arg == "--nlf-mode") {
            if (seen_nlf_mode) {
                std::cerr << "Error: Duplicate --nlf-mode argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing --nlf-mode argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_nlf_mode = true;
            const std::string mode_text = argv[++i];
            if (!parse_nlf_mode_cli(mode_text, opts.nlf_mode)) {
                std::cerr << "Error: Invalid --nlf-mode '" << mode_text << "'. Must be 'none', 'static' or 'residual'.\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
        } else if (arg == "--max-nlf-memory-mib") {
            if (seen_max_nlf_memory_mib) {
                std::cerr << "Error: Duplicate --max-nlf-memory-mib argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing --max-nlf-memory-mib argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_max_nlf_memory_mib = true;
            std::string parse_err;
            uint64_t val = 0;
            if (!parse_positive_uint64_exact(argv[++i], val, parse_err)) {
                std::cerr << "Error: Invalid --max-nlf-memory-mib: " << parse_err << "\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            if (val > MAX_NLF_MEMORY_MIB) {
                std::cerr << "Error: Invalid --max-nlf-memory-mib: value " << val << " exceeds maximum serializable memory limit\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.max_nlf_memory_mib = val;
            opts.cli_max_nlf_memory_mib_set = true;
        } else if (arg == "--execution-purpose") {
            if (seen_execution_purpose || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --execution-purpose argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_execution_purpose = true;
            opts.explicit_execution_purpose = true;
            const std::string ptxt = argv[++i];
            if (!parse_execution_purpose(ptxt, opts.execution_purpose)) {
                std::cerr << "Error: Invalid --execution-purpose '" << ptxt << "'. Must be 'correctness', 'performance', or 'diagnostic'.\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
        } else if (arg == "--wall-timeout-seconds") {
            if (seen_wall_timeout || i + 1 >= argc) {
                std::cerr << "Error: Duplicate or missing --wall-timeout-seconds argument\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            seen_wall_timeout = true;
            std::string parse_err;
            uint64_t val = 0;
            if (!parse_positive_uint64_exact(argv[++i], val, parse_err) || val > 60) {
                std::cerr << "Error: Invalid --wall-timeout-seconds (expected integer 1..60)\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.wall_timeout_seconds = val;
        } else if (arg == "--profile") {
            opts.profiling_enabled = true;
            opts.profile_flag_seen = true;
        } else if (arg == "--profile-output") {
            if (i + 1 >= argc) {
                std::cerr << "Error: Missing argument for --profile-output\n";
                return static_cast<int>(ExitCode::CLI_ARG_ERROR);
            }
            opts.profile_output_path = argv[++i];
            opts.profiling_enabled = true;
            opts.profile_flag_seen = true;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json> "
                      << "[--execution-purpose <correctness|performance|diagnostic>] [--wall-timeout-seconds <1..60>] "
                      << "[--max-work <N>] [--continue-on-error] [--nlf-mode <none|static|residual>] "
                      << "[--neighbor-mode <legacy|buffer|merge>] [--max-nlf-memory-mib <MiB>] [--profile] [--profile-output <FILE>]\n";
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

    return run_workload_with_options(opts);
}
