#ifndef RAPIDFLOW_APX_AUDIT_PROBES_H
#define RAPIDFLOW_APX_AUDIT_PROBES_H

#include <string>
#include <vector>
#include <cstdint>
#include <map>
#include <sstream>

struct ProbeResult {
    std::string case_id;
    std::string probe_group;
    bool pass;
    int exit_code;
    double duration_ms;
    std::string summary;
    std::map<std::string, std::string> evidence;
};

// JSON helper for string escaping
inline std::string escape_json(const std::string& s) {
    std::ostringstream o;
    for (char c : s) {
        if (c == '"') o << "\\\"";
        else if (c == '\\') o << "\\\\";
        else if (c == '\b') o << "\\b";
        else if (c == '\f') o << "\\f";
        else if (c == '\n') o << "\\n";
        else if (c == '\r') o << "\\r";
        else if (c == '\t') o << "\\t";
        else if (static_cast<unsigned char>(c) <= 0x1f) {
            o << "\\u00" << std::hex << (int)c;
        } else {
            o << c;
        }
    }
    return o.str();
}

// Function declarations for Probes A through F
ProbeResult run_probe_a_provenance(const std::string& binary_path, const std::string& binary_sha);
ProbeResult run_probe_b_typed_boundary(const std::string& fixture_path);
ProbeResult run_probe_c_global_edge_view();
ProbeResult run_probe_d_exact_nlf_counterexample(const std::string& fixture_path);
ProbeResult run_probe_e_snapshot_rollback(const std::string& fixture_path);
ProbeResult run_probe_f_count_tuple_boundary(const std::string& fixture_path);

#endif // RAPIDFLOW_APX_AUDIT_PROBES_H
