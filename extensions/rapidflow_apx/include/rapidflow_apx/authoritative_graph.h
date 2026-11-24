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

    // Baseline Ingestion via genuine RapidFlow CSR with strict validation
    void load_baseline(
        const std::vector<std::pair<uint32_t, uint32_t>>& vertices,
        const std::vector<std::tuple<uint32_t, uint32_t, uint32_t>>& edges
    );

    // Pristine initial state check (for authoritative G0 NLF initialization)
    bool is_pristine_initial_state() const noexcept {
        return overlay_labels_.empty() && tombstones_.empty() && dynamic_edge_count_ == 0;
    }

    // Topological & Label Queries
    bool has_vertex(uint32_t u) const;
    uint32_t get_vertex_label(uint32_t u) const;
    bool has_edge(uint32_t u, uint32_t v) const;
    EdgeLookupResult get_edge_label(uint32_t u, uint32_t v) const;
    std::vector<uint32_t> get_neighbors(uint32_t u) const;

    // Replace out with the sorted unique effective neighbor IDs, retaining
    // its capacity. On allocation failure the partial output must be discarded.
    void get_neighbors_into(uint32_t u, std::vector<uint32_t>& out) const;
    uint32_t get_degree(uint32_t u) const;
    uint32_t get_vertices_count() const;
    uint32_t get_edges_count() const;

    // Direct access to underlying CSR for read-only inspections
    const Graph* get_base_graph() const;

    // Overlay & Tombstone Inspection (for differential graph-state verification)
    bool is_in_tombstones(uint32_t u, uint32_t v) const {
        if (!has_vertex(u) || !has_vertex(v) || u == v) return false;
        uint64_t key = make_edge_key(u, v);
        return tombstones_.find(key) != tombstones_.end();
    }
    bool is_in_overlay(uint32_t u, uint32_t v) const {
        if (!has_vertex(u) || !has_vertex(v) || u == v) return false;
        uint64_t key = make_edge_key(u, v);
        return overlay_labels_.find(key) != overlay_labels_.end();
    }

    // Mutable Overlay Operations (Called by TransactionManager)
    bool apply_insert(uint32_t u, uint32_t v, uint32_t label, bool simulate_rev_fail = false);
    bool apply_delete(uint32_t u, uint32_t v, uint32_t label);

    // Transaction Management with Zero-Allocation Rollback (noexcept)
    void begin_transaction() noexcept;
    void commit_transaction() noexcept;
    void rollback_transaction() noexcept;

    // Snapshot / Checkpoint for Compatibility
    GraphOverlayCheckpoint create_checkpoint() const;
    void restore_checkpoint(const GraphOverlayCheckpoint& cp);

    static inline uint64_t make_edge_key(uint32_t u, uint32_t v) {
        uint32_t mn = std::min(u, v);
        uint32_t mx = std::max(u, v);
        return (static_cast<uint64_t>(mn) << 32) | static_cast<uint64_t>(mx);
    }

private:
    enum class TxOpType { NONE, INSERT, DELETE };
    struct TxRecord {
        TxOpType op{TxOpType::NONE};
        uint32_t u{0};
        uint32_t v{0};
        uint32_t label{0};
        uint64_t key{0};
        bool was_in_tombstones{false};
        bool was_in_overlay{false};
        bool fwd_uv_inserted{false};
        bool fwd_vu_inserted{false};
        bool rev_vu_inserted{false};
        bool rev_uv_inserted{false};
        bool overlay_label_inserted{false};
    };

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

    // Active transaction state
    bool in_tx_{false};
    TxRecord tx_record_{};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_AUTHORITATIVE_GRAPH_H
