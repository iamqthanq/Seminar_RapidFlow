#include "rapidflow_apx/nlf_signatures.h"
#include <map>

namespace rapidflow_apx {

void ApxQuerySignatures::build(const ApxQueryPlan& query_plan) {
    signatures_.clear();
    vertex_requirements_.clear();
    edge_signatures_.clear();

    uint32_t num_v = query_plan.get_vertices_count();
    uint32_t num_e = query_plan.get_edges_count();

    if (num_v == 0) {
        return;
    }

    // 1. Collect all distinct incident signatures from both orientations of every query edge
    std::vector<IncidentSignature> raw_sigs;
    raw_sigs.reserve(num_e * 2);

    for (uint32_t i = 0; i < num_e; ++i) {
        const auto& qe = query_plan.get_edge(i);
        uint32_t u_vlabel = query_plan.get_vertex_label(qe.u);
        uint32_t v_vlabel = query_plan.get_vertex_label(qe.v);

        raw_sigs.push_back(IncidentSignature{qe.label, v_vlabel}); // seen from u
        raw_sigs.push_back(IncidentSignature{qe.label, u_vlabel}); // seen from v
    }

    std::sort(raw_sigs.begin(), raw_sigs.end());
    raw_sigs.erase(std::unique(raw_sigs.begin(), raw_sigs.end()), raw_sigs.end());

    if (raw_sigs.size() > static_cast<size_t>(std::numeric_limits<SignatureId>::max())) {
        throw InvariantViolationException("Query signature count exceeds SignatureId capacity");
    }

    signatures_ = std::move(raw_sigs);

    // 2. Build per-canonical-edge signature IDs
    edge_signatures_.resize(num_e);
    for (uint32_t i = 0; i < num_e; ++i) {
        const auto& qe = query_plan.get_edge(i);
        uint32_t u_vlabel = query_plan.get_vertex_label(qe.u);
        uint32_t v_vlabel = query_plan.get_vertex_label(qe.v);

        SignatureId from_u = 0;
        SignatureId from_v = 0;
        bool found_u = find_signature_id(qe.label, v_vlabel, from_u);
        bool found_v = find_signature_id(qe.label, u_vlabel, from_v);

        if (!found_u || !found_v) {
            throw InvariantViolationException("Internal query signature resolution failure for edge " + std::to_string(i));
        }

        edge_signatures_[i] = QueryEdgeSignatures{from_u, from_v};
    }

    // 3. Build per-vertex requirements
    vertex_requirements_.resize(num_v);
    for (uint32_t u = 0; u < num_v; ++u) {
        std::map<SignatureId, NlfCount> req_counts;
        const auto& incident = query_plan.get_incident_edges(u);

        for (const auto& inc : incident) {
            uint32_t neighbor_v = inc.first;
            uint32_t edge_idx = inc.second;
            const auto& qe = query_plan.get_edge(edge_idx);
            uint32_t neighbor_vlabel = query_plan.get_vertex_label(neighbor_v);

            SignatureId sig_id = 0;
            if (!find_signature_id(qe.label, neighbor_vlabel, sig_id)) {
                throw InvariantViolationException("Query signature not found during requirement calculation");
            }

            req_counts[sig_id]++;
        }

        // Validate degree invariant: sum of requirements == query degree
        uint64_t sum_req = 0;
        std::vector<SignatureRequirement> reqs;
        reqs.reserve(req_counts.size());
        for (const auto& pair : req_counts) {
            reqs.push_back(SignatureRequirement{pair.first, pair.second});
            sum_req += pair.second;
        }

        if (sum_req != incident.size()) {
            throw InvariantViolationException(
                "Degree invariant violated for query vertex " + std::to_string(u) +
                ": sum of requirements (" + std::to_string(sum_req) +
                ") != degree (" + std::to_string(incident.size()) + ")"
            );
        }

        vertex_requirements_[u] = std::move(reqs);
    }
}

bool ApxQuerySignatures::find_signature_id(
    uint32_t edge_label,
    uint32_t neighbor_vlabel,
    SignatureId& out_id
) const noexcept {
    IncidentSignature target{edge_label, neighbor_vlabel};
    auto it = std::lower_bound(signatures_.begin(), signatures_.end(), target);
    if (it != signatures_.end() && *it == target) {
        out_id = static_cast<SignatureId>(std::distance(signatures_.begin(), it));
        return true;
    }
    return false;
}

} // namespace rapidflow_apx
