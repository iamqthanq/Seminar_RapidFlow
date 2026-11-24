#ifndef RAPIDFLOW_APX_NLF_SIGNATURES_H
#define RAPIDFLOW_APX_NLF_SIGNATURES_H

#include <cstdint>
#include <vector>
#include <tuple>
#include <algorithm>
#include <limits>
#include <stdexcept>
#include "rapidflow_apx/query_plan.h"

namespace rapidflow_apx {

using SignatureId = uint32_t;
using NlfCount = uint32_t;

struct IncidentSignature {
    uint32_t edge_label_id{0};
    uint32_t neighbor_vertex_label_id{0};

    bool operator==(const IncidentSignature& o) const noexcept {
        return edge_label_id == o.edge_label_id &&
               neighbor_vertex_label_id == o.neighbor_vertex_label_id;
    }
    bool operator!=(const IncidentSignature& o) const noexcept {
        return !(*this == o);
    }
    bool operator<(const IncidentSignature& o) const noexcept {
        if (edge_label_id != o.edge_label_id) {
            return edge_label_id < o.edge_label_id;
        }
        return neighbor_vertex_label_id < o.neighbor_vertex_label_id;
    }
};

struct SignatureRequirement {
    SignatureId sig_id{0};
    NlfCount required_count{0};

    bool operator==(const SignatureRequirement& o) const noexcept {
        return sig_id == o.sig_id && required_count == o.required_count;
    }
    bool operator!=(const SignatureRequirement& o) const noexcept {
        return !(*this == o);
    }
};

struct QueryEdgeSignatures {
    SignatureId from_u{0};
    SignatureId from_v{0};

    bool operator==(const QueryEdgeSignatures& o) const noexcept {
        return from_u == o.from_u && from_v == o.from_v;
    }
};

class ApxQuerySignatures {
public:
    ApxQuerySignatures() = default;

    // Constructs deterministic bidirectional query signature registry and per-vertex requirements
    void build(const ApxQueryPlan& query_plan);

    bool is_empty() const noexcept { return signatures_.empty(); }
    size_t signature_count() const noexcept { return signatures_.size(); }
    const std::vector<IncidentSignature>& get_signatures() const noexcept { return signatures_; }
    const IncidentSignature& get_signature(SignatureId id) const { return signatures_.at(id); }

    // Fast O(log |Sigma_Q|) binary lookup: returns true if found and writes out_id
    bool find_signature_id(uint32_t edge_label, uint32_t neighbor_vlabel, SignatureId& out_id) const noexcept;

    // Per-query-vertex requirements (sorted by sig_id, unique positive counts)
    const std::vector<SignatureRequirement>& get_vertex_requirements(uint32_t u) const {
        return vertex_requirements_.at(u);
    }

    // Per-canonical-query-edge signatures
    const QueryEdgeSignatures& get_edge_signatures(uint32_t edge_idx) const {
        return edge_signatures_.at(edge_idx);
    }

    bool has_signature(SignatureId id) const noexcept {
        return id < signatures_.size();
    }

private:
    std::vector<IncidentSignature> signatures_; // canonically sorted
    std::vector<std::vector<SignatureRequirement>> vertex_requirements_;
    std::vector<QueryEdgeSignatures> edge_signatures_;
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_NLF_SIGNATURES_H
