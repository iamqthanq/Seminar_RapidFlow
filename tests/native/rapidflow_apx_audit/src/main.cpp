#include "audit_probes.h"
#include <iostream>
#include <fstream>
#include <vector>
#include <string>

int main(int argc, char* argv[]) {
    std::string probe_target = "all";
    std::string output_file = "";
    std::string fixture_path = "";
    std::string binary_sha = "";
    std::string binary_path = argv[0];

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--probe" && i + 1 < argc) {
            probe_target = argv[++i];
        } else if (arg == "--output" && i + 1 < argc) {
            output_file = argv[++i];
        } else if (arg == "--fixture" && i + 1 < argc) {
            fixture_path = argv[++i];
        } else if (arg == "--binary-sha" && i + 1 < argc) {
            binary_sha = argv[++i];
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: " << argv[0] << " [--probe <all|a|b|c|d|e|f>] [--output <json_path>] [--fixture <path>] [--binary-sha <sha>]\n";
            return 0;
        } else {
            std::cerr << "Unknown argument: " << arg << "\n";
            return 2;
        }
    }

    std::vector<ProbeResult> results;
    bool all_pass = true;

    auto run_and_record = [&](const ProbeResult& r) {
        results.push_back(r);
        if (!r.pass) all_pass = false;
    };

    if (probe_target == "all" || probe_target == "a" || probe_target == "probe_a") {
        run_and_record(run_probe_a_provenance(binary_path, binary_sha));
    }
    if (probe_target == "all" || probe_target == "b" || probe_target == "probe_b") {
        run_and_record(run_probe_b_typed_boundary(fixture_path));
    }
    if (probe_target == "all" || probe_target == "c" || probe_target == "probe_c") {
        run_and_record(run_probe_c_global_edge_view());
    }
    if (probe_target == "all" || probe_target == "d" || probe_target == "probe_d") {
        run_and_record(run_probe_d_exact_nlf_counterexample(fixture_path));
    }
    if (probe_target == "all" || probe_target == "e" || probe_target == "probe_e") {
        run_and_record(run_probe_e_snapshot_rollback(fixture_path));
    }
    if (probe_target == "all" || probe_target == "f" || probe_target == "probe_f") {
        run_and_record(run_probe_f_count_tuple_boundary(fixture_path));
    }

    if (results.empty()) {
        std::cerr << "Error: No matching probes found for: " << probe_target << "\n";
        return 1;
    }

    std::ostringstream json;
    json << "{\n";
    json << "  \"execution_status\": \"" << (all_pass ? "PASS" : "FAIL") << "\",\n";
    json << "  \"total_probes\": " << results.size() << ",\n";
    json << "  \"passed_probes\": " << (all_pass ? results.size() : 0) << ",\n";
    json << "  \"probes\": [\n";

    for (size_t i = 0; i < results.size(); ++i) {
        const auto& r = results[i];
        json << "    {\n";
        json << "      \"case_id\": \"" << escape_json(r.case_id) << "\",\n";
        json << "      \"probe_group\": \"" << escape_json(r.probe_group) << "\",\n";
        json << "      \"pass\": " << (r.pass ? "true" : "false") << ",\n";
        json << "      \"exit_code\": " << r.exit_code << ",\n";
        json << "      \"duration_ms\": " << r.duration_ms << ",\n";
        json << "      \"summary\": \"" << escape_json(r.summary) << "\",\n";
        json << "      \"evidence\": {\n";

        size_t ev_idx = 0;
        for (const auto& kv : r.evidence) {
            json << "        \"" << escape_json(kv.first) << "\": \"" << escape_json(kv.second) << "\"";
            if (++ev_idx < r.evidence.size()) json << ",";
            json << "\n";
        }
        json << "      }\n";
        json << "    }" << (i + 1 < results.size() ? "," : "") << "\n";
    }

    json << "  ]\n";
    json << "}\n";

    std::string out_str = json.str();
    if (!output_file.empty()) {
        std::ofstream out(output_file);
        if (!out.is_open()) {
            std::cerr << "Failed to open output file: " << output_file << "\n";
            return 1;
        }
        out << out_str;
    } else {
        std::cout << out_str;
    }

    return all_pass ? 0 : 1;
}
