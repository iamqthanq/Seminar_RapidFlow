#ifndef RAPIDFLOW_APX_QUERY_PLAN_H
#define RAPIDFLOW_APX_QUERY_PLAN_H

#include <cstdint>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <tuple>
#include <algorithm>
#include "rapidflow_apx/typed_codec.h"
#include "rapidflow_apx/engine_counters.h"

namespace rapidflow_apx {

struct QueryEdge {
    uint32_t u{0};
    uint32_t v{0};
    uint32_t label{0};

    bool operator==(const QueryEdge& o) const noexcept {
        return u == o.u && v == o.v && label == o.label;
    }
};

struct M1VariantPlan {
    uint32_t anchor_edge_idx{0};
    uint32_t anchor_qa{0};
    uint32_t anchor_qb{0};
    std::vector<uint32_t> missing_edges; // indices into QueryPlan::edges_
    std::vector<uint32_t> search_order;  // dense query vertex IDs starting with [qa, qb]
    // For each query vertex: list of (neighbor_dense_u, kept_edge_label)
    std::vector<std::vector<std::pair<uint32_t, uint32_t>>> kept_incident;
};

class ApxQueryPlan {
public:
    ApxQueryPlan() = default;

    // Constructs query plan from dense query graph definition and validates D-R contract
    void build(
        uint32_t num_vertices,
        const std::vector<uint32_t>& vertex_labels,
        const std::vector<QueryEdge>& edges,
        const std::vector<uint32_t>& canonical_order,
        uint32_t k
    );

    // Queries
    uint32_t get_vertices_count() const noexcept { return num_vertices_; }
    uint32_t get_edges_count() const noexcept { return static_cast<uint32_t>(edges_.size()); }
    uint32_t get_k() const noexcept { return k_; }
    uint32_t get_lambda() const noexcept { return lambda_; }
    uint32_t get_vertex_label(uint32_t u) const { return vertex_labels_.at(u); }
    const QueryEdge& get_edge(uint32_t idx) const { return edges_.at(idx); }
    const std::vector<QueryEdge>& get_edges() const noexcept { return edges_; }
    const std::vector<uint32_t>& get_canonical_order() const noexcept { return canonical_order_; }

    bool is_relevant_edge_label(uint32_t label_id) const noexcept {
        return relevant_edge_labels_.find(label_id) != relevant_edge_labels_.end();
    }

    const std::vector<uint32_t>& get_edges_with_label(uint32_t label_id) const {
        static const std::vector<uint32_t> empty_edges{};
        auto it = label_to_edges_.find(label_id);
        if (it != label_to_edges_.end()) return it->second;
        return empty_edges;
    }

    const std::vector<std::pair<uint32_t, uint32_t>>& get_incident_edges(uint32_t u) const {
        return vertex_incident_edges_.at(u);
    }

    // M1 Variants for a specific anchor edge index
    std::vector<M1VariantPlan> generate_m1_variants(uint32_t anchor_idx, uint32_t qa, uint32_t qb) const;

    // Exact edge connectivity calculation
    static uint32_t compute_edge_connectivity(uint32_t num_vertices, const std::vector<QueryEdge>& edges);

private:
    uint32_t num_vertices_{0};
    uint32_t k_{0};
    uint32_t lambda_{0};
    std::vector<uint32_t> vertex_labels_;
    std::vector<QueryEdge> edges_;
    std::vector<uint32_t> canonical_order_;

    std::unordered_set<uint32_t> relevant_edge_labels_;
    std::unordered_map<uint32_t, std::vector<uint32_t>> label_to_edges_;
    // For each vertex u: list of (neighbor_v, edge_index)
    std::vector<std::vector<std::pair<uint32_t, uint32_t>>> vertex_incident_edges_;
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_QUERY_PLAN_H
