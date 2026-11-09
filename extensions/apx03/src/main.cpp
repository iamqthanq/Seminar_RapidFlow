#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <chrono>
#include <iomanip>
#include "apx03_types.h"
#include "apx03_graph.h"
#include "apx03_matcher.h"
#include "apx03_dynamic_engine.h"

using namespace apx03;

namespace {

void print_usage(const char* prog) {
    std::cout << "Usage: " << prog << " [options]\n"
              << "Options:\n"
              << "  --query <file>              Query graph path (required)\n"
              << "  --data <file>               Data graph path (required)\n"
              << "  --eps-m <int>               Missing edge threshold (>= 0)\n"
              << "  --eps-x <int|inf|None>      External boundary threshold (>= 0 or inf/None)\n"
              << "  --mode <STRICT|RELAXED>     Connectivity mode (default: STRICT)\n"
              << "  --stream <file>             Dynamic update stream path (optional)\n"
              << "  --evaluate-mapping <m>      Direct mapping evaluation (e.g. '0,1,2')\n"
              << "  --max-candidates <int>      Candidate search budget (default: 10000000)\n"
              << "  --output <file>             Output JSON file path (optional)\n"
              << "  --help                      Show this help message\n";
}

std::vector<Update> parse_stream_file(const std::string& path) {
    std::ifstream infile(path);
    if (!infile.is_open()) {
        throw ApxException("Cannot open stream file: " + path);
    }

    std::vector<Update> updates;
    std::string line;
    uint32_t line_num = 0;

    while (std::getline(infile, line)) {
        line_num++;
        size_t first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos || line[first] == '#') {
            continue;
        }
        size_t last = line.find_last_not_of(" \t\r\n");
        std::string trimmed = line.substr(first, (last - first + 1));

        std::istringstream ls(trimmed);
        std::string tag;
        ls >> tag;

        char op = '+';
        if (tag == "e" || tag == "+" || tag == "+e") {
            op = '+';
        } else if (tag == "-e" || tag == "-") {
            op = '-';
        } else {
            throw ApxException("Unsupported stream update tag '" + tag + "' at line " + std::to_string(line_num));
        }

        uint32_t u = 0, v = 0, label = 0;
        if (!(ls >> u >> v >> label)) {
            throw ApxException("Malformed stream update at line " + std::to_string(line_num) + ": " + line);
        }
        updates.push_back({op, u, v, label});
    }
    return updates;
}

std::vector<uint32_t> parse_mapping_string(const std::string& str) {
    std::vector<uint32_t> mapping;
    std::string clean = str;
    for (char& c : clean) {
        if (c == '(' || c == ')' || c == '[' || c == ']' || c == ',') {
            c = ' ';
        }
    }
    std::istringstream iss(clean);
    uint32_t val = 0;
    while (iss >> val) {
        mapping.push_back(val);
    }
    return mapping;
}

std::string score_to_json(const DeviationScore& sc) {
    std::ostringstream oss;
    oss << "{\"m\": " << sc.m
        << ", \"x_in\": " << sc.x_in
        << ", \"x_out\": " << sc.x_out
        << ", \"connected\": " << (sc.connected ? "true" : "false") << "}";
    return oss.str();
}

} // anonymous namespace

int main(int argc, char* argv[]) {
    std::string query_path;
    std::string data_path;
    std::string stream_path;
    std::string eval_mapping_str;
    std::string output_path;

    int64_t eps_m_in = 0;
    bool eps_m_set = false;
    bool eps_x_unbounded = true;
    uint32_t eps_x_val = 0;
    ConnectivityMode mode = ConnectivityMode::STRICT;
    uint64_t max_candidates = 10000000ULL;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            return 0;
        } else if (arg == "--query" && i + 1 < argc) {
            query_path = argv[++i];
        } else if (arg == "--data" && i + 1 < argc) {
            data_path = argv[++i];
        } else if (arg == "--eps-m" && i + 1 < argc) {
            std::string val = argv[++i];
            try {
                eps_m_in = std::stoll(val);
                eps_m_set = true;
            } catch (...) {
                std::cerr << "[ERROR] Invalid eps-m argument: " << val << "\n";
                return 1;
            }
        } else if (arg == "--eps-x" && i + 1 < argc) {
            std::string val = argv[++i];
            if (val == "inf" || val == "None" || val == "-1" || val == "null") {
                eps_x_unbounded = true;
            } else {
                try {
                    int64_t v = std::stoll(val);
                    if (v < 0) {
                        std::cerr << "[ERROR] Negative eps-x argument: " << val << "\n";
                        return 1;
                    }
                    eps_x_unbounded = false;
                    eps_x_val = static_cast<uint32_t>(v);
                } catch (...) {
                    std::cerr << "[ERROR] Invalid eps-x argument: " << val << "\n";
                    return 1;
                }
            }
        } else if (arg == "--mode" && i + 1 < argc) {
            std::string val = argv[++i];
            try {
                mode = mode_from_string(val);
            } catch (const std::exception& ex) {
                std::cerr << "[ERROR] " << ex.what() << "\n";
                return 1;
            }
        } else if (arg == "--stream" && i + 1 < argc) {
            stream_path = argv[++i];
        } else if (arg == "--evaluate-mapping" && i + 1 < argc) {
            eval_mapping_str = argv[++i];
        } else if (arg == "--max-candidates" && i + 1 < argc) {
            std::string val = argv[++i];
            try {
                int64_t v = std::stoll(val);
                if (v <= 0) {
                    std::cerr << "[ERROR] max-candidates must be strictly positive: " << val << "\n";
                    return 1;
                }
                max_candidates = static_cast<uint64_t>(v);
            } catch (...) {
                std::cerr << "[ERROR] Invalid max-candidates: " << val << "\n";
                return 1;
            }
        } else if (arg == "--output" && i + 1 < argc) {
            output_path = argv[++i];
        } else {
            std::cerr << "[ERROR] Unknown option or missing argument: " << arg << "\n";
            print_usage(argv[0]);
            return 1;
        }
    }

    if (query_path.empty() || data_path.empty()) {
        std::cerr << "[ERROR] Both --query and --data are required.\n";
        print_usage(argv[0]);
        return 1;
    }

    if (!eps_m_set || eps_m_in < 0) {
        std::cerr << "[ERROR] --eps-m must be specified as a non-negative integer.\n";
        return 1;
    }
    uint32_t eps_m = static_cast<uint32_t>(eps_m_in);

    try {
        ApxGraph Q;
        Q.load_from_file(query_path);

        ApxGraph G0;
        G0.load_from_file(data_path);

        if (!eval_mapping_str.empty()) {
            // Direct Mapping Evaluation Mode
            auto target_mapping = parse_mapping_string(eval_mapping_str);
            ApxMatcher matcher(Q, G0, eps_m, eps_x_unbounded, eps_x_val, mode, max_candidates);
            auto res = matcher.evaluate_single_mapping(target_mapping);

            std::ostringstream json_out;
            json_out << "{\n"
                     << "  \"query\": \"" << query_path << "\",\n"
                     << "  \"data\": \"" << data_path << "\",\n"
                     << "  \"eps_m\": " << eps_m << ",\n"
                     << "  \"eps_x\": " << (eps_x_unbounded ? "null" : std::to_string(eps_x_val)) << ",\n"
                     << "  \"mode\": \"" << to_string(mode) << "\",\n"
                     << "  \"evaluation_type\": \"DIRECT_MAPPING_EVALUATION\",\n"
                     << "  \"target_mapping\": [";
            for (size_t i = 0; i < target_mapping.size(); ++i) {
                json_out << target_mapping[i] << (i + 1 < target_mapping.size() ? ", " : "");
            }
            json_out << "],\n"
                     << "  \"score\": " << score_to_json(res.score) << ",\n"
                     << "  \"accepted\": " << (res.accepted ? "true" : "false") << "\n"
                     << "}\n";

            if (!output_path.empty()) {
                std::ofstream ofs(output_path);
                ofs << json_out.str();
            } else {
                std::cout << json_out.str();
            }
            return 0;
        }

        if (stream_path.empty()) {
            // Static Full Enumeration Mode
            ApxMatcher matcher(Q, G0, eps_m, eps_x_unbounded, eps_x_val, mode, max_candidates);
            auto matches = matcher.find_all_matches();

            std::ostringstream json_out;
            json_out << "{\n"
                     << "  \"query\": \"" << query_path << "\",\n"
                     << "  \"data\": \"" << data_path << "\",\n"
                     << "  \"eps_m\": " << eps_m << ",\n"
                     << "  \"eps_x\": " << (eps_x_unbounded ? "null" : std::to_string(eps_x_val)) << ",\n"
                     << "  \"mode\": \"" << to_string(mode) << "\",\n"
                     << "  \"total_accepted\": " << matches.size() << ",\n"
                     << "  \"matches\": [\n";

            size_t idx = 0;
            for (const auto& kv : matches) {
                const auto& m = kv.first;
                const auto& eval_res = kv.second;
                json_out << "    {\n"
                         << "      \"mapping\": [";
                for (size_t i = 0; i < m.size(); ++i) {
                    json_out << m[i] << (i + 1 < m.size() ? ", " : "");
                }
                json_out << "],\n"
                         << "      \"score\": " << score_to_json(eval_res.score) << ",\n"
                         << "      \"accepted\": " << (eval_res.accepted ? "true" : "false") << "\n"
                         << "    }" << (idx + 1 < matches.size() ? "," : "") << "\n";
                idx++;
            }
            json_out << "  ]\n}\n";

            if (!output_path.empty()) {
                std::ofstream ofs(output_path);
                ofs << json_out.str();
            } else {
                std::cout << json_out.str();
            }
            return 0;
        } else {
            // Dynamic Stream Mode (RECOMPUTE_BASELINE)
            auto updates = parse_stream_file(stream_path);
            ApxDynamicEngine engine(std::move(Q), std::move(G0), eps_m, eps_x_unbounded, eps_x_val, mode, max_candidates);

            std::ostringstream json_out;
            json_out << "{\n"
                     << "  \"query\": \"" << query_path << "\",\n"
                     << "  \"initial_data\": \"" << data_path << "\",\n"
                     << "  \"stream\": \"" << stream_path << "\",\n"
                     << "  \"eps_m\": " << eps_m << ",\n"
                     << "  \"eps_x\": " << (eps_x_unbounded ? "null" : std::to_string(eps_x_val)) << ",\n"
                     << "  \"mode\": \"" << to_string(mode) << "\",\n"
                     << "  \"total_steps\": " << updates.size() << ",\n"
                     << "  \"history\": [\n";

            for (size_t s = 0; s < updates.size(); ++s) {
                const auto& u = updates[s];
                auto start_time = std::chrono::high_resolution_clock::now();
                StepDelta delta = engine.apply_update(u);
                auto end_time = std::chrono::high_resolution_clock::now();
                double duration_ms = std::chrono::duration<double, std::milli>(end_time - start_time).count();

                json_out << "    {\n"
                         << "      \"step\": " << (s + 1) << ",\n"
                         << "      \"update\": {\"op\": \"" << u.op << "\", \"u\": " << u.u << ", \"v\": " << u.v << ", \"label\": " << u.label << "},\n"
                         << "      \"duration_ms\": " << std::fixed << std::setprecision(4) << duration_ms << ",\n"
                         << "      \"total_active\": " << engine.get_current_matches().size() << ",\n"
                         << "      \"delta\": {\n";

                // entered
                json_out << "        \"entered\": {";
                size_t e_idx = 0;
                for (const auto& kv : delta.entered) {
                    json_out << "\"" << mapping_to_tuple_string(kv.first) << "\": " << score_to_json(kv.second)
                             << (e_idx + 1 < delta.entered.size() ? ", " : "");
                    e_idx++;
                }
                json_out << "},\n";

                // exited
                json_out << "        \"exited\": {";
                size_t x_idx = 0;
                for (const auto& kv : delta.exited) {
                    json_out << "\"" << mapping_to_tuple_string(kv.first) << "\": " << score_to_json(kv.second)
                             << (x_idx + 1 < delta.exited.size() ? ", " : "");
                    x_idx++;
                }
                json_out << "},\n";

                // score_changed
                json_out << "        \"score_changed\": {";
                size_t sc_idx = 0;
                for (const auto& kv : delta.score_changed) {
                    json_out << "\"" << mapping_to_tuple_string(kv.first) << "\": ["
                             << score_to_json(kv.second.first) << ", "
                             << score_to_json(kv.second.second) << "]"
                             << (sc_idx + 1 < delta.score_changed.size() ? ", " : "");
                    sc_idx++;
                }
                json_out << "},\n";

                // unchanged
                json_out << "        \"unchanged\": {";
                size_t uc_idx = 0;
                for (const auto& kv : delta.unchanged) {
                    json_out << "\"" << mapping_to_tuple_string(kv.first) << "\": " << score_to_json(kv.second)
                             << (uc_idx + 1 < delta.unchanged.size() ? ", " : "");
                    uc_idx++;
                }
                json_out << "}\n"
                         << "      }\n"
                         << "    }" << (s + 1 < updates.size() ? "," : "") << "\n";
            }

            json_out << "  ],\n"
                     << "  \"final_active_matches\": " << engine.get_current_matches().size() << "\n"
                     << "}\n";

            if (!output_path.empty()) {
                std::ofstream ofs(output_path);
                ofs << json_out.str();
            } else {
                std::cout << json_out.str();
            }
            return 0;
        }

    } catch (const ResourceLimitExceededException& ex) {
        std::cerr << "[ERROR] Resource limit exceeded: " << ex.what() << "\n";
        return 2;
    } catch (const ApxException& ex) {
        std::cerr << "[ERROR] APX Execution Error: " << ex.what() << "\n";
        return 1;
    } catch (const std::exception& ex) {
        std::cerr << "[ERROR] Exception: " << ex.what() << "\n";
        return 1;
    }
}
