#ifndef RAPIDFLOW_APX_AUTHORITATIVE_GRAPH_H
#define RAPIDFLOW_APX_AUTHORITATIVE_GRAPH_H

#include <cstdint>
#include <vector>
#include <tuple>
#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <stdexcept>
#include <limits>

// Genuine upstream RapidFlow headers
#include "graph/graph.h"
#include "utility/relation/global_edge_view.h"

namespace rapidflow_apx {

struct EdgeLookupResult {
    bool exists{false};
    uint32_t label{0};
};

struct GraphOverlayCheckpoint {
    GlobalEdgeView fwd_overlay;
    GlobalEdgeView rev_overlay;
    std::unordered_map<uint64_t, uint32_t> overlay_labels;
    std::unordered_set<uint64_t> tombstones;
    uint32_t dynamic_edge_count{0};
    uint32_t current_edges_count{0};
};

class ApxAuthoritativeGraph {
public:
    ApxAuthoritativeGraph();
    ~ApxAuthoritativeGraph();

    // Non-copyable, movable
    ApxAuthoritativeGraph(const ApxAuthoritativeGraph&) = delete;
    ApxAuthoritativeGraph& operator=(const ApxAuthoritativeGraph&) = delete;
    ApxAuthoritativeGraph(ApxAuthoritativeGraph&&) noexcept;
    ApxAuthoritativeGraph& operator=(ApxAuthoritativeGraph&&) noexcept;

    // Baseline Ingestion via genuine RapidFlow CSR
    void load_baseline(
        const std::vector<std::pair<uint32_t, uint32_t>>& vertices,
        const std::vector<std::tuple<uint32_t, uint32_t, uint32_t>>& edges
    );

    // Topological & Label Queries
    bool has_vertex(uint32_t u) const;
    uint32_t get_vertex_label(uint32_t u) const;
    bool has_edge(uint32_t u, uint32_t v) const;
    EdgeLookupResult get_edge_label(uint32_t u, uint32_t v) const;
    std::vector<uint32_t> get_neighbors(uint32_t u) const;
    uint32_t get_degree(uint32_t u) const;
    uint32_t get_vertices_count() const;
    uint32_t get_edges_count() const;

    // Direct access to underlying CSR for read-only inspections
    const Graph* get_base_graph() const;

    // Mutable Overlay Operations (Called by TransactionManager)
    bool apply_insert(uint32_t u, uint32_t v, uint32_t label);
    bool apply_delete(uint32_t u, uint32_t v, uint32_t label);

    // Snapshot / Checkpoint for Strong Exception Guarantee
    GraphOverlayCheckpoint create_checkpoint() const;
    void restore_checkpoint(const GraphOverlayCheckpoint& cp);

    static inline uint64_t make_edge_key(uint32_t u, uint32_t v) {
        uint32_t mn = std::min(u, v);
        uint32_t mx = std::max(u, v);
        return (static_cast<uint64_t>(mn) << 32) | static_cast<uint64_t>(mx);
    }

private:
    std::unique_ptr<Graph> base_graph_;
    std::unordered_map<uint64_t, uint32_t> base_edge_labels_;
    std::vector<uint32_t> vertex_labels_;

    GlobalEdgeView fwd_overlay_;
    GlobalEdgeView rev_overlay_;
    std::unordered_map<uint64_t, uint32_t> overlay_labels_;
    std::unordered_set<uint64_t> tombstones_;

    uint32_t dynamic_edge_count_{0};
    uint32_t current_edges_count_{0};
    uint32_t vertices_count_{0};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_AUTHORITATIVE_GRAPH_H
