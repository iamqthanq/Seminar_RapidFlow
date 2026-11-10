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

bool parse_uint32_strict(const std::string& str, uint32_t& out_val) {
    if (str.empty()) return false;
    for (char c : str) {
        if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    }
    try {
        unsigned long long val = std::stoull(str);
        if (val > static_cast<unsigned long long>(std::numeric_limits<uint32_t>::max())) {
            return false;
        }
        out_val = static_cast<uint32_t>(val);
        return true;
    } catch (...) {
        return false;
    }
}

bool parse_uint64_strict(const std::string& str, uint64_t& out_val) {
    if (str.empty()) return false;
    for (char c : str) {
        if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    }
    try {
        out_val = std::stoull(str);
        return true;
    } catch (...) {
        return false;
    }
}

void print_usage(const char* prog) {
    std::cout << "Usage: " << prog << " [options]\n"
              << "Options:\n"
              << "  --query <file>              Query graph path (required for matching)\n"
              << "  --data <file>               Data graph path (required)\n"
              << "  --eps-m <int>               Missing edge threshold (>= 0)\n"
              << "  --eps-x <int|inf|None>      External boundary threshold (>= 0 or inf/None)\n"
              << "  --mode <STRICT|RELAXED>     Connectivity mode (default: STRICT)\n"
              << "  --stream <file>             Dynamic update stream path (optional)\n"
              << "  --evaluate-mapping <m>      Direct mapping evaluation (e.g. '0,1,2')\n"
              << "  --max-candidates <int>      Candidate search budget (default: 10000000)\n"
              << "  --max-search-steps <int>    Search exploration steps budget (default: 10000000)\n"
              << "  --output <file>             Output JSON file path (optional)\n"
              << "  --verify-csr                Verify native CSR structural equivalence\n"
              << "  --test-csr                  Run internal C++ CSR equivalence test suite\n"
              << "  --help                      Show this help message\n";
}

std::string json_escape(const std::string& s) {
    std::ostringstream o;
    for (char c : s) {
        switch (c) {
            case '"': o << "\\\""; break;
            case '\\': o << "\\\\"; break;
            case '\b': o << "\\b"; break;
            case '\f': o << "\\f"; break;
            case '\n': o << "\\n"; break;
            case '\r': o << "\\r"; break;
            case '\t': o << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) <= 0x1f) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
                    o << buf;
                } else {
                    o << c;
                }
        }
    }
    return o.str();
}

bool run_internal_csr_tests(std::string& out_report) {
    // Test 1: Graph with vertex labels and edge labels
    {
        ApxGraph g;
        std::string text = "v 0 10\nv 1 20\nv 2 30\ne 0 1 100\ne 1 2 200\n";
        g.load_from_text(text);
        if (!g.verify_against_native_csr()) {
            out_report = "Failed CSR verification on labeled graph";
            return false;
        }
    }

    // Test 2: Graph with isolated vertices
    {
        ApxGraph g;
        std::string text = "v 0 1\nv 1 1\nv 2 2\nv 3 2\ne 0 1 5\n";
        g.load_from_text(text);
        if (!g.verify_against_native_csr()) {
            out_report = "Failed CSR verification on graph with isolated vertices";
            return false;
        }
        if (g.get_degree(2) != 0 || g.get_degree(3) != 0) {
            out_report = "Isolated vertices degree mismatch";
            return false;
        }
    }

    // Test 3: Graph with sparse non-contiguous IDs
    {
        ApxGraph g;
        std::string text = "v 10 1\nv 500 2\nv 99999 3\ne 10 99999 42\n";
        g.load_from_text(text);
        if (!g.verify_against_native_csr()) {
            out_report = "Failed CSR verification on graph with sparse IDs";
            return false;
        }
    }

    // Test 4: G0 CSR vs Gt mutable graph separation
    {
        ApxGraph g0;
        std::string text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\n";
        g0.load_from_text(text);
        if (!g0.verify_against_native_csr()) {
            out_report = "Failed initial CSR verification on G0";
            return false;
        }
        const Graph* native_rf = g0.get_native_rf_graph();
        if (!native_rf || native_rf->getVerticesCount() != 3 || native_rf->getEdgesCount() != 1) {
            out_report = "Native RF CSR initial size mismatch";
            return false;
        }

        ApxGraph gt;
        gt.load_from_text(text);
        gt.apply_update({'+', 1, 2, 0});
        if (gt.num_edges() != 2) {
            out_report = "Mutable Gt failed to update edge count";
            return false;
        }
        if (native_rf->getEdgesCount() != 1) {
            out_report = "G0 native CSR was unexpectedly mutated by Gt update";
            return false;
        }
    }

    out_report = "Verified vertex labels, edge labels, isolated vertices, sparse IDs, and G0 CSR vs Gt mutable graph separation";
    return true;
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

std::vector<uint32_t> parse_mapping_string(const std::string& raw_str) {
    size_t first = raw_str.find_first_not_of(" \t\n\r");
    if (first == std::string::npos) {
        throw ApxException("Malformed mapping string: input is empty");
    }
    size_t last = raw_str.find_last_not_of(" \t\n\r");
    std::string str = raw_str.substr(first, last - first + 1);

    if ((str.front() == '(' && str.back() == ')') ||
        (str.front() == '[' && str.back() == ']')) {
        str = str.substr(1, str.size() - 2);
        first = str.find_first_not_of(" \t\n\r");
        if (first == std::string::npos) {
            throw ApxException("Malformed mapping string: empty bracketed expression");
        }
        last = str.find_last_not_of(" \t\n\r");
        str = str.substr(first, last - first + 1);
    } else if (str.front() == '(' || str.front() == '[' ||
               str.back() == ')' || str.back() == ']') {
        throw ApxException("Malformed mapping string: mismatched enclosing brackets in '" + raw_str + "'");
    }

    if (str.find_first_of("()[]") != std::string::npos) {
        throw ApxException("Malformed mapping string: unexpected bracket in '" + raw_str + "'");
    }

    std::vector<std::string> tokens;
    size_t start = 0;
    while (true) {
        size_t comma = str.find(',', start);
        if (comma == std::string::npos) {
            tokens.push_back(str.substr(start));
            break;
        } else {
            tokens.push_back(str.substr(start, comma - start));
            start = comma + 1;
        }
    }

    if (tokens.empty()) {
        throw ApxException("Malformed mapping string: no tokens in '" + raw_str + "'");
    }

    std::vector<uint32_t> mapping;
    for (const auto& tok : tokens) {
        size_t t_first = tok.find_first_not_of(" \t\n\r");
        if (t_first == std::string::npos) {
            throw ApxException("Malformed mapping string: empty element (e.g. trailing/consecutive commas) in '" + raw_str + "'");
        }
        size_t t_last = tok.find_last_not_of(" \t\n\r");
        std::string clean_tok = tok.substr(t_first, t_last - t_first + 1);

        for (char c : clean_tok) {
            if (!std::isdigit(static_cast<unsigned char>(c))) {
                throw ApxException("Malformed mapping string: invalid character '" + std::string(1, c) + "' in token '" + clean_tok + "' of '" + raw_str + "'");
            }
        }

        try {
            size_t idx = 0;
            unsigned long long val = std::stoull(clean_tok, &idx);
            if (idx != clean_tok.size() || val > 4294967295ULL) {
                throw ApxException("Malformed mapping string: integer overflow in token '" + clean_tok + "'");
            }
            mapping.push_back(static_cast<uint32_t>(val));
        } catch (const std::exception& e) {
            throw ApxException("Malformed mapping string: failed to parse integer in token '" + clean_tok + "': " + e.what());
        }
    }

    if (mapping.empty()) {
        throw ApxException("Malformed mapping string: empty mapping");
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

    uint32_t eps_m = 0;
    bool eps_m_set = false;
    bool eps_x_unbounded = true;
    uint32_t eps_x_val = 0;
    ConnectivityMode mode = ConnectivityMode::STRICT;
    uint64_t max_candidates = 10000000ULL;
    uint64_t max_search_steps = 10000000ULL;
    bool verify_csr_flag = false;
    bool test_csr_flag = false;

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
            if (!parse_uint32_strict(val, eps_m)) {
                std::cerr << "[ERROR] Invalid eps-m argument: '" << val << "'. Must be a non-negative 32-bit integer.\n";
                return 1;
            }
            eps_m_set = true;
        } else if (arg == "--eps-x" && i + 1 < argc) {
            std::string val = argv[++i];
            if (val == "inf" || val == "None" || val == "null") {
                eps_x_unbounded = true;
            } else if (parse_uint32_strict(val, eps_x_val)) {
                eps_x_unbounded = false;
            } else {
                std::cerr << "[ERROR] Invalid eps-x argument: '" << val << "'. Must be a non-negative 32-bit integer, 'inf', or 'None'.\n";
                return 1;
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
            if (!parse_uint64_strict(val, max_candidates) || max_candidates == 0) {
                std::cerr << "[ERROR] Invalid max-candidates: '" << val << "'. Must be a strictly positive integer.\n";
                return 1;
            }
        } else if (arg == "--max-search-steps" && i + 1 < argc) {
            std::string val = argv[++i];
            if (!parse_uint64_strict(val, max_search_steps) || max_search_steps == 0) {
                std::cerr << "[ERROR] Invalid max-search-steps: '" << val << "'. Must be a strictly positive integer.\n";
                return 1;
            }
        } else if (arg == "--output" && i + 1 < argc) {
            output_path = argv[++i];
        } else if (arg == "--verify-csr") {
            verify_csr_flag = true;
        } else if (arg == "--test-csr") {
            test_csr_flag = true;
        } else {
            std::cerr << "[ERROR] Unknown option or missing argument: " << arg << "\n";
            print_usage(argv[0]);
            return 1;
        }
    }

    if (test_csr_flag) {
        std::string report;
        bool ok = run_internal_csr_tests(report);
        if (ok) {
            std::cout << "{\n"
                      << "  \"test_csr\": \"PASS\",\n"
                      << "  \"details\": \"" << json_escape(report) << "\"\n"
                      << "}\n";
            return 0;
        } else {
            std::cerr << "[ERROR] CSR internal tests failed: " << report << "\n";
            std::cout << "{\n"
                      << "  \"test_csr\": \"FAIL\",\n"
                      << "  \"error\": \"" << json_escape(report) << "\"\n"
                      << "}\n";
            return 1;
        }
    }

    if (verify_csr_flag && query_path.empty()) {
        if (data_path.empty()) {
            std::cerr << "[ERROR] --verify-csr requires --data <file>.\n";
            return 1;
        }
        try {
            ApxGraph G0;
            G0.load_from_file(data_path);
            bool ok = G0.verify_against_native_csr();
            if (ok) {
                std::cout << "{\n"
                          << "  \"data\": \"" << json_escape(data_path) << "\",\n"
                          << "  \"csr_verified\": true,\n"
                          << "  \"num_vertices\": " << G0.num_vertices() << ",\n"
                          << "  \"num_edges\": " << G0.num_edges() << "\n"
                          << "}\n";
                return 0;
            } else {
                std::cerr << "[ERROR] Native CSR verification failed for data graph: " << data_path << "\n";
                std::cout << "{\n"
                          << "  \"data\": \"" << json_escape(data_path) << "\",\n"
                          << "  \"csr_verified\": false\n"
                          << "}\n";
                return 1;
            }
        } catch (const std::exception& e) {
            std::cerr << "[ERROR] " << e.what() << "\n";
            return 1;
        }
    }

    if (query_path.empty() || data_path.empty()) {
        std::cerr << "[ERROR] Both --query and --data are required.\n";
        print_usage(argv[0]);
        return 1;
    }

    if (!eps_m_set) {
        std::cerr << "[ERROR] --eps-m must be specified as a non-negative integer.\n";
        return 1;
    }

    try {
        ApxGraph Q;
        Q.load_from_file(query_path);

        ApxGraph G0;
        G0.load_from_file(data_path);

        if (verify_csr_flag) {
            if (!Q.verify_against_native_csr()) {
                std::cerr << "[ERROR] Native CSR verification failed for query graph: " << query_path << "\n";
                return 1;
            }
            if (!G0.verify_against_native_csr()) {
                std::cerr << "[ERROR] Native CSR verification failed for data graph: " << data_path << "\n";
                return 1;
            }
        }

        if (!eval_mapping_str.empty()) {
            // Direct Mapping Evaluation Mode
            auto target_mapping = parse_mapping_string(eval_mapping_str);
            ApxMatcher matcher(Q, G0, eps_m, eps_x_unbounded, eps_x_val, mode, max_candidates, max_search_steps);
            auto res = matcher.evaluate_single_mapping(target_mapping);

            std::ostringstream json_out;
            json_out << "{\n"
                     << "  \"query\": \"" << json_escape(query_path) << "\",\n"
                     << "  \"data\": \"" << json_escape(data_path) << "\",\n"
                     << "  \"eps_m\": " << eps_m << ",\n"
                     << "  \"eps_x\": " << (eps_x_unbounded ? "null" : std::to_string(eps_x_val)) << ",\n"
                     << "  \"mode\": \"" << json_escape(to_string(mode)) << "\",\n"
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
            ApxMatcher matcher(Q, G0, eps_m, eps_x_unbounded, eps_x_val, mode, max_candidates, max_search_steps);
            auto matches = matcher.find_all_matches();

            std::ostringstream json_out;
            json_out << "{\n"
                     << "  \"query\": \"" << json_escape(query_path) << "\",\n"
                     << "  \"data\": \"" << json_escape(data_path) << "\",\n"
                     << "  \"eps_m\": " << eps_m << ",\n"
                     << "  \"eps_x\": " << (eps_x_unbounded ? "null" : std::to_string(eps_x_val)) << ",\n"
                     << "  \"mode\": \"" << json_escape(to_string(mode)) << "\",\n"
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
            ApxDynamicEngine engine(std::move(Q), std::move(G0), eps_m, eps_x_unbounded, eps_x_val, mode, max_candidates, max_search_steps);

            std::ostringstream json_out;
            json_out << "{\n"
                     << "  \"query\": \"" << json_escape(query_path) << "\",\n"
                     << "  \"initial_data\": \"" << json_escape(data_path) << "\",\n"
                     << "  \"stream\": \"" << json_escape(stream_path) << "\",\n"
                     << "  \"eps_m\": " << eps_m << ",\n"
                     << "  \"eps_x\": " << (eps_x_unbounded ? "null" : std::to_string(eps_x_val)) << ",\n"
                     << "  \"mode\": \"" << json_escape(to_string(mode)) << "\",\n"
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
