#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <cstring>
#include "json_mini.h"
#include "harness_cases.h"

int main(int argc, char* argv[]) {
    std::string case_id;
    std::string fixture_path;
    std::string binary_sha;
    std::string out_path;

    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--case") == 0 && i + 1 < argc) {
            case_id = argv[++i];
        } else if (std::strcmp(argv[i], "--fixture") == 0 && i + 1 < argc) {
            fixture_path = argv[++i];
        } else if (std::strcmp(argv[i], "--binary-sha") == 0 && i + 1 < argc) {
            binary_sha = argv[++i];
        } else if (std::strcmp(argv[i], "--out") == 0 && i + 1 < argc) {
            out_path = argv[++i];
        }
    }

    if (case_id.empty() || fixture_path.empty()) {
        std::cerr << "Usage: " << argv[0] << " --case <case_id> --fixture <path> [--out <out_file>] [--binary-sha <sha>]" << std::endl;
        return 1;
    }

    std::ifstream f(fixture_path);
    if (!f.is_open()) {
        std::cerr << "Error: Cannot open fixture file: " << fixture_path << std::endl;
        return 1;
    }

    std::stringstream buffer;
    buffer << f.rdbuf();
    std::string content = buffer.str();

    mini_json::Value fixture;
    try {
        fixture = mini_json::Parser::parse(content);
    } catch (const std::exception& e) {
        std::cerr << "Error: Failed to parse fixture JSON: " << e.what() << std::endl;
        return 1;
    }

    mini_json::Value report;
    try {
        if (case_id == "codec_boundaries") {
            report = rapidflow_apx_harness::run_codec_boundaries(fixture);
        } else if (case_id == "lifecycle_stream") {
            report = rapidflow_apx_harness::run_lifecycle_stream(fixture);
        } else if (case_id == "transaction_lifecycle") {
            report = rapidflow_apx_harness::run_transaction_lifecycle(fixture);
        } else if (case_id == "tuple_sink_invariants") {
            report = rapidflow_apx_harness::run_tuple_sink_invariants(fixture);
        } else if (case_id == "k4_exact_enumeration") {
            report = rapidflow_apx_harness::run_k4_exact_enumeration(fixture);
        } else if (case_id == "event_transition_stream") {
            report = rapidflow_apx_harness::run_event_transition_stream(fixture);
        } else if (case_id == "diamond_k1_fixed") {
            report = rapidflow_apx_harness::run_diamond_k1_fixed(fixture);
        } else if (case_id == "irrelevant_update_stream") {
            report = rapidflow_apx_harness::run_irrelevant_update_stream(fixture);
        } else {
            report = mini_json::Value::make_object();
            report["case_id"] = case_id;
            report["status"] = "FAIL";
            report["diagnostic"] = "Unknown case_id: " + case_id;
        }
    } catch (const std::exception& e) {
        report = mini_json::Value::make_object();
        report["case_id"] = case_id;
        report["status"] = "FAIL";
        report["diagnostic"] = std::string("Unhandled exception: ") + e.what();
    }

    if (!binary_sha.empty()) {
        report["binary_sha256"] = binary_sha;
    }

    std::string json_str = mini_json::serialize(report);

    if (!out_path.empty()) {
        std::ofstream out_file(out_path);
        if (out_file.is_open()) {
            out_file << json_str << std::endl;
        } else {
            std::cerr << "Error: Could not open output file: " << out_path << std::endl;
        }
    }

    std::cout << json_str << std::endl;

    if (report["status"].as_string() == "PASS") {
        return 0;
    } else {
        return 2;
    }
}
