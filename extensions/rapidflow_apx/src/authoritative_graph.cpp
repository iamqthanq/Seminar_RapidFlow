#include "rapidflow_apx/authoritative_graph.h"
#include <iostream>

namespace rapidflow_apx {

ApxAuthoritativeGraph::ApxAuthoritativeGraph() = default;

ApxAuthoritativeGraph::~ApxAuthoritativeGraph() = default;

ApxAuthoritativeGraph::ApxAuthoritativeGraph(ApxAuthoritativeGraph&&) noexcept = default;

ApxAuthoritativeGraph& ApxAuthoritativeGraph::operator=(ApxAuthoritativeGraph&&) noexcept = default;

void ApxAuthoritativeGraph::load_baseline(
    const std::vector<std::pair<uint32_t, uint32_t>>& vertices,
    const std::vector<std::tuple<uint32_t, uint32_t, uint32_t>>& edges
) {
    vertices_count_ = static_cast<uint32_t>(vertices.size());
    vertex_labels_.resize(vertices_count_, 0);
    for (const auto& v : vertices) {
        if (v.first >= vertices_count_) {
            throw std::out_of_range("Vertex ID exceeds declared count in baseline");
        }
        vertex_labels_[v.first] = v.second;
    }

    base_edge_labels_.clear();
    for (const auto& e : edges) {
        uint32_t u = std::get<0>(e);
        uint32_t v = std::get<1>(e);
        uint32_t l = std::get<2>(e);
        if (u >= vertices_count_ || v >= vertices_count_) {
            throw std::out_of_range("Edge endpoint out of range in baseline");
        }
        base_edge_labels_[make_edge_key(u, v)] = l;
    }
    current_edges_count_ = static_cast<uint32_t>(base_edge_labels_.size());

    // Allocate genuine RapidFlow Graph CSR (pass enable_label_offset = false)
    base_graph_ = std::make_unique<Graph>(false);
    base_graph_->is_edge_labeled = true;

    // loadGraphFromMemory takes non-const references
    std::vector<std::pair<uint32_t, uint32_t>> v_list = vertices;
    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> e_list = edges;
    base_graph_->loadGraphFromMemory(v_list, e_list);

    // Reset overlay and tombstones
    fwd_overlay_ = GlobalEdgeView();
    rev_overlay_ = GlobalEdgeView();
    overlay_labels_.clear();
    tombstones_.clear();
    dynamic_edge_count_ = 0;
}

bool ApxAuthoritativeGraph::has_vertex(uint32_t u) const {
    return u < vertices_count_;
}

uint32_t ApxAuthoritativeGraph::get_vertex_label(uint32_t u) const {
    if (!has_vertex(u)) {
        throw std::out_of_range("Vertex ID out of range in get_vertex_label");
    }
    return vertex_labels_[u];
}

bool ApxAuthoritativeGraph::has_edge(uint32_t u, uint32_t v) const {
    if (!has_vertex(u) || !has_vertex(v) || u == v) {
        return false;
    }
    uint64_t key = make_edge_key(u, v);
    if (overlay_labels_.find(key) != overlay_labels_.end()) {
        return true;
    }
    if (tombstones_.find(key) != tombstones_.end()) {
        return false;
    }
    if (base_graph_) {
        return base_graph_->checkEdgeExistence(u, v);
    }
    return false;
}

EdgeLookupResult ApxAuthoritativeGraph::get_edge_label(uint32_t u, uint32_t v) const {
    if (!has_vertex(u) || !has_vertex(v) || u == v) {
        return {false, 0};
    }
    uint64_t key = make_edge_key(u, v);
    auto it = overlay_labels_.find(key);
    if (it != overlay_labels_.end()) {
        return {true, it->second};
    }
    if (tombstones_.find(key) != tombstones_.end()) {
        return {false, 0};
    }
    auto base_it = base_edge_labels_.find(key);
    if (base_it != base_edge_labels_.end()) {
        return {true, base_it->second};
    }
    return {false, 0};
}

std::vector<uint32_t> ApxAuthoritativeGraph::get_neighbors(uint32_t u) const {
    if (!has_vertex(u)) {
        return {};
    }
    std::vector<uint32_t> neighbors;
    if (base_graph_) {
        ui count = 0;
        const VertexID* base_nbrs = base_graph_->getVertexNeighbors(u, count);
        for (ui i = 0; i < count; ++i) {
            uint32_t v = base_nbrs[i];
            uint64_t key = make_edge_key(u, v);
            if (tombstones_.find(key) != tombstones_.end()) {
                continue; // deleted
            }
            if (overlay_labels_.find(key) != overlay_labels_.end()) {
                continue; // replaced in overlay
            }
            neighbors.push_back(v);
        }
    }

    auto* self = const_cast<ApxAuthoritativeGraph*>(this);
    uint32_t overlay_count = 0;
    uint32_t* overlay_nbrs = self->fwd_overlay_.get_neighbor(u, overlay_count);
    for (uint32_t i = 0; i < overlay_count; ++i) {
        neighbors.push_back(overlay_nbrs[i]);
    }

    std::sort(neighbors.begin(), neighbors.end());
    neighbors.erase(std::unique(neighbors.begin(), neighbors.end()), neighbors.end());
    return neighbors;
}

uint32_t ApxAuthoritativeGraph::get_degree(uint32_t u) const {
    return static_cast<uint32_t>(get_neighbors(u).size());
}

uint32_t ApxAuthoritativeGraph::get_vertices_count() const {
    return vertices_count_;
}

uint32_t ApxAuthoritativeGraph::get_edges_count() const {
    return current_edges_count_;
}

const Graph* ApxAuthoritativeGraph::get_base_graph() const {
    return base_graph_.get();
}

bool ApxAuthoritativeGraph::apply_insert(uint32_t u, uint32_t v, uint32_t label) {
    if (!has_vertex(u) || !has_vertex(v) || u == v) {
        return false;
    }
    if (has_edge(u, v)) {
        return false; // Edge already exists
    }
    uint64_t key = make_edge_key(u, v);
    tombstones_.erase(key);
    fwd_overlay_.insert(u, v);
    fwd_overlay_.insert(v, u);
    rev_overlay_.insert(v, u);
    rev_overlay_.insert(u, v);
    overlay_labels_[key] = label;
    dynamic_edge_count_++;
    current_edges_count_++;
    return true;
}

bool ApxAuthoritativeGraph::apply_delete(uint32_t u, uint32_t v, uint32_t label) {
    if (!has_vertex(u) || !has_vertex(v) || u == v) {
        return false;
    }
    auto lookup = get_edge_label(u, v);
    if (!lookup.exists || lookup.label != label) {
        return false; // Edge not found or label mismatch
    }
    uint64_t key = make_edge_key(u, v);
    if (overlay_labels_.erase(key) > 0) {
        fwd_overlay_.remove(u, v);
        fwd_overlay_.remove(v, u);
        rev_overlay_.remove(v, u);
        rev_overlay_.remove(u, v);
    }
    if (base_edge_labels_.find(key) != base_edge_labels_.end()) {
        tombstones_.insert(key);
    }
    if (dynamic_edge_count_ > 0) {
        dynamic_edge_count_--;
    }
    if (current_edges_count_ > 0) {
        current_edges_count_--;
    }
    return true;
}

GraphOverlayCheckpoint ApxAuthoritativeGraph::create_checkpoint() const {
    GraphOverlayCheckpoint cp;
    cp.fwd_overlay = fwd_overlay_;
    cp.rev_overlay = rev_overlay_;
    cp.overlay_labels = overlay_labels_;
    cp.tombstones = tombstones_;
    cp.dynamic_edge_count = dynamic_edge_count_;
    cp.current_edges_count = current_edges_count_;
    return cp;
}

void ApxAuthoritativeGraph::restore_checkpoint(const GraphOverlayCheckpoint& cp) {
    fwd_overlay_ = cp.fwd_overlay;
    rev_overlay_ = cp.rev_overlay;
    overlay_labels_ = cp.overlay_labels;
    tombstones_ = cp.tombstones;
    dynamic_edge_count_ = cp.dynamic_edge_count;
    current_edges_count_ = cp.current_edges_count;
}

} // namespace rapidflow_apx
