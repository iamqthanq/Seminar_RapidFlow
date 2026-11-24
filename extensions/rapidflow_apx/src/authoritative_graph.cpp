#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/typed_codec.h"
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
    // 1. Capacity Limits Check
    ApxTypedCodec::check_capacity_limits(vertices.size(), edges.size());

    vertices_count_ = static_cast<uint32_t>(vertices.size());
    vertex_labels_.assign(vertices_count_, 0);

    // 2. Dense Vertex ID Validation [0, |V|-1] without duplicates or gaps
    std::vector<bool> seen_vertex(vertices_count_, false);
    for (const auto& v : vertices) {
        if (v.first >= vertices_count_) {
            throw std::out_of_range("Vertex ID " + std::to_string(v.first) + " out of dense range [0, " + std::to_string(vertices_count_ - 1) + "]");
        }
        if (seen_vertex[v.first]) {
            throw std::invalid_argument("Duplicate vertex ID in baseline: " + std::to_string(v.first));
        }
        seen_vertex[v.first] = true;
        vertex_labels_[v.first] = v.second;
    }
    for (size_t i = 0; i < vertices_count_; ++i) {
        if (!seen_vertex[i]) {
            throw std::invalid_argument("Missing vertex ID in dense range [0, |V|-1]: " + std::to_string(i));
        }
    }

    // 3. Edge Validation: endpoints in range, no self-loops, no duplicate undirected edges
    base_edge_labels_.clear();
    for (const auto& e : edges) {
        uint32_t u = std::get<0>(e);
        uint32_t v = std::get<1>(e);
        uint32_t l = std::get<2>(e);

        if (u >= vertices_count_ || v >= vertices_count_) {
            throw std::out_of_range("Edge endpoint out of range in baseline: (" + std::to_string(u) + ", " + std::to_string(v) + ")");
        }
        if (u == v) {
            throw std::invalid_argument("Self-loops are not permitted in baseline: (" + std::to_string(u) + ", " + std::to_string(v) + ")");
        }
        uint64_t key = make_edge_key(u, v);
        if (base_edge_labels_.find(key) != base_edge_labels_.end()) {
            throw std::invalid_argument("Duplicate undirected edge in baseline: (" + std::to_string(u) + ", " + std::to_string(v) + ")");
        }
        base_edge_labels_[key] = l;
    }
    current_edges_count_ = static_cast<uint32_t>(base_edge_labels_.size());

    // Allocate genuine RapidFlow Graph CSR
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
    in_tx_ = false;
    tx_record_ = TxRecord{};
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

void ApxAuthoritativeGraph::get_neighbors_into(uint32_t u, std::vector<uint32_t>& out) const {
    out.clear();
    if (!has_vertex(u)) {
        return;
    }

    // Match the legacy control's collection and sort/unique steps while
    // retaining the caller's storage across independent retrievals.
    if (base_graph_) {
        ui count = 0;
        const VertexID* base_nbrs = base_graph_->getVertexNeighbors(u, count);
        for (ui i = 0; i < count; ++i) {
            const uint32_t v = base_nbrs[i];
            const uint64_t key = make_edge_key(u, v);
            if (tombstones_.find(key) != tombstones_.end()
                || overlay_labels_.find(key) != overlay_labels_.end()) {
                continue;
            }
            out.push_back(v);
        }
    }

    auto* self = const_cast<ApxAuthoritativeGraph*>(this);
    uint32_t overlay_count = 0;
    const uint32_t* overlay_nbrs = self->fwd_overlay_.get_neighbor(u, overlay_count);
    for (uint32_t i = 0; i < overlay_count; ++i) {
        out.push_back(overlay_nbrs[i]);
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
}

void ApxAuthoritativeGraph::get_neighbors_merged_into(
    uint32_t u,
    std::vector<uint32_t>& out,
    EngineCounters* counters
) const {
    out.clear();
    if (!has_vertex(u)) {
        return;
    }

    ui base_count = 0;
    const VertexID* base_nbrs = nullptr;
    if (base_graph_) {
        base_nbrs = base_graph_->getVertexNeighbors(u, base_count);
    }
    if (base_count > 0 && base_nbrs == nullptr) {
        throw InvariantViolationException("Corrupt base CSR: positive count with null pointer");
    }

    auto* self = const_cast<ApxAuthoritativeGraph*>(this);
    uint32_t overlay_count = 0;
    const uint32_t* overlay_nbrs = self->fwd_overlay_.get_neighbor(u, overlay_count);
    if (overlay_count > 0 && overlay_nbrs == nullptr) {
        throw InvariantViolationException("Corrupt overlay: positive count with null pointer");
    }

    merge_neighbor_ranges(u, base_nbrs, base_count, overlay_nbrs, overlay_count, out, counters);
}

void ApxAuthoritativeGraph::merge_neighbor_ranges(
    uint32_t u,
    const uint32_t* base_nbrs,
    uint32_t base_count,
    const uint32_t* overlay_nbrs,
    uint32_t overlay_count,
    std::vector<uint32_t>& out,
    EngineCounters* counters
) const {
    out.clear();

    if (base_count > 0 && base_nbrs == nullptr) {
        throw InvariantViolationException("merge_neighbor_ranges: base_count > 0 with null pointer");
    }
    if (overlay_count > 0 && overlay_nbrs == nullptr) {
        throw InvariantViolationException("merge_neighbor_ranges: overlay_count > 0 with null pointer");
    }

    // Fast Path 1: Both empty
    if (base_count == 0 && overlay_count == 0) {
        if (counters) {
            ++counters->neighbor_merge_retrieval_calls;
            ++counters->neighbor_merge_fast_path_calls;
        }
        return;
    }

    // Fast Path 2: Empty base, nonempty overlay
    if (base_count == 0 && overlay_count > 0) {
        bool overlay_sorted = true;
        for (uint32_t k = 1; k < overlay_count; ++k) {
            if (overlay_nbrs[k] <= overlay_nbrs[k - 1]) {
                overlay_sorted = false;
                break;
            }
        }
        if (overlay_sorted) {
            out.assign(overlay_nbrs, overlay_nbrs + overlay_count);
            if (counters) {
                ++counters->neighbor_merge_retrieval_calls;
                ++counters->neighbor_merge_fast_path_calls;
            }
            return;
        } else {
            goto do_fallback;
        }
    }

    // Fast Path 3: Nonempty base, empty overlay, no tombstones and no overlay labels
    if (base_count > 0 && overlay_count == 0 && tombstones_.empty() && overlay_labels_.empty()) {
        bool base_sorted = true;
        for (uint32_t k = 1; k < base_count; ++k) {
            if (base_nbrs[k] <= base_nbrs[k - 1]) {
                base_sorted = false;
                break;
            }
        }
        if (base_sorted) {
            out.assign(base_nbrs, base_nbrs + base_count);
            if (counters) {
                ++counters->neighbor_merge_retrieval_calls;
                ++counters->neighbor_merge_fast_path_calls;
            }
            return;
        } else {
            goto do_fallback;
        }
    }

    // Linear 2-way sorted merge with Strategy B fused validation
    {
        for (uint32_t k = 1; k < overlay_count; ++k) {
            if (overlay_nbrs[k] <= overlay_nbrs[k - 1]) {
                goto do_fallback;
            }
        }

        uint32_t i = 0; // index in base_nbrs
        uint32_t j = 0; // index in overlay_nbrs

        // Advance base index i to next eligible base neighbor while verifying raw base sortedness
        auto advance_base = [&]() -> bool {
            while (i < base_count) {
                if (i > 0 && base_nbrs[i] <= base_nbrs[i - 1]) {
                    return false; // raw base not strictly ascending
                }
                uint32_t b = base_nbrs[i];
                uint64_t key = make_edge_key(u, b);
                if (tombstones_.find(key) == tombstones_.end() &&
                    overlay_labels_.find(key) == overlay_labels_.end()) {
                    return true;
                }
                ++i;
            }
            return true;
        };

        if (!advance_base()) goto do_fallback;

        while (i < base_count && j < overlay_count) {
            uint32_t b = base_nbrs[i];
            uint32_t o = overlay_nbrs[j];

            if (b < o) {
                if (!out.empty() && b <= out.back()) goto do_fallback;
                out.push_back(b);
                ++i;
                if (!advance_base()) goto do_fallback;
            } else if (o < b) {
                if (!out.empty() && o <= out.back()) goto do_fallback;
                out.push_back(o);
                ++j;
            } else { // b == o: defensive equality handling
                if (!out.empty() && o <= out.back()) goto do_fallback;
                out.push_back(o);
                ++i;
                ++j;
                if (!advance_base()) goto do_fallback;
            }
        }

        while (i < base_count) {
            uint32_t b = base_nbrs[i];
            if (!out.empty() && b <= out.back()) goto do_fallback;
            out.push_back(b);
            ++i;
            if (!advance_base()) goto do_fallback;
        }

        while (j < overlay_count) {
            uint32_t o = overlay_nbrs[j];
            if (!out.empty() && o <= out.back()) goto do_fallback;
            out.push_back(o);
            ++j;
        }

        if (counters) {
            ++counters->neighbor_merge_retrieval_calls;
            ++counters->neighbor_merge_linear_calls;
        }
        return;
    }

do_fallback:
    out.clear();
    if (base_nbrs) {
        for (uint32_t k = 0; k < base_count; ++k) {
            uint32_t v = base_nbrs[k];
            uint64_t key = make_edge_key(u, v);
            if (tombstones_.find(key) != tombstones_.end() ||
                overlay_labels_.find(key) != overlay_labels_.end()) {
                continue;
            }
            out.push_back(v);
        }
    }
    if (overlay_nbrs) {
        for (uint32_t k = 0; k < overlay_count; ++k) {
            out.push_back(overlay_nbrs[k]);
        }
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    if (counters) {
        ++counters->neighbor_merge_retrieval_calls;
        ++counters->neighbor_merge_fallback_calls;
    }
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

void ApxAuthoritativeGraph::begin_transaction() noexcept {
    in_tx_ = true;
    tx_record_ = TxRecord{};
}

void ApxAuthoritativeGraph::commit_transaction() noexcept {
    if (!in_tx_) return;
    if (tx_record_.op == TxOpType::INSERT) {
        // If inserted edge was previously tombstoned, finalize removal from tombstones
        tombstones_.erase(tx_record_.key);
    } else if (tx_record_.op == TxOpType::DELETE) {
        // Finalize deletion
        if (tx_record_.was_in_overlay) {
            overlay_labels_.erase(tx_record_.key);
            fwd_overlay_.remove(tx_record_.u, tx_record_.v);
            fwd_overlay_.remove(tx_record_.v, tx_record_.u);
            rev_overlay_.remove(tx_record_.v, tx_record_.u);
            rev_overlay_.remove(tx_record_.u, tx_record_.v);
            if (base_edge_labels_.find(tx_record_.key) == base_edge_labels_.end()) {
                tombstones_.erase(tx_record_.key);
            }
        }
    }
    in_tx_ = false;
    tx_record_ = TxRecord{};
}

void ApxAuthoritativeGraph::rollback_transaction() noexcept {
    if (!in_tx_) return;
    if (tx_record_.op == TxOpType::INSERT) {
        // Zero-allocation rollback of overlay insertions
        if (tx_record_.overlay_label_inserted) {
            overlay_labels_.erase(tx_record_.key);
        }
        if (tx_record_.rev_uv_inserted) {
            rev_overlay_.remove(tx_record_.u, tx_record_.v);
        }
        if (tx_record_.rev_vu_inserted) {
            rev_overlay_.remove(tx_record_.v, tx_record_.u);
        }
        if (tx_record_.fwd_vu_inserted) {
            fwd_overlay_.remove(tx_record_.v, tx_record_.u);
        }
        if (tx_record_.fwd_uv_inserted) {
            fwd_overlay_.remove(tx_record_.u, tx_record_.v);
        }
        if (dynamic_edge_count_ > 0) dynamic_edge_count_--;
        if (current_edges_count_ > 0) current_edges_count_--;
    } else if (tx_record_.op == TxOpType::DELETE) {
        // Zero-allocation rollback of deletion shadow
        tombstones_.erase(tx_record_.key);
        current_edges_count_++;
        if (tx_record_.was_in_overlay) {
            dynamic_edge_count_++;
        }
    }
    in_tx_ = false;
    tx_record_ = TxRecord{};
}

bool ApxAuthoritativeGraph::apply_insert(uint32_t u, uint32_t v, uint32_t label, bool simulate_rev_fail) {
    if (!has_vertex(u) || !has_vertex(v) || u == v) {
        return false;
    }
    if (has_edge(u, v)) {
        return false; // Edge already exists
    }
    uint64_t key = make_edge_key(u, v);

    bool fwd_uv = false;
    bool fwd_vu = false;
    bool rev_vu = false;
    bool rev_uv = false;
    bool lbl_ins = false;

    try {
        fwd_uv = fwd_overlay_.insert(u, v);

        // Simulated or real failure point between forward and reverse
        if (simulate_rev_fail) {
            if (fwd_uv) fwd_overlay_.remove(u, v);
            return false;
        }

        fwd_vu = fwd_overlay_.insert(v, u);
        rev_vu = rev_overlay_.insert(v, u);
        rev_uv = rev_overlay_.insert(u, v);
        overlay_labels_[key] = label;
        lbl_ins = true;
    } catch (...) {
        // Strong exception guarantee: undo all partial insertions with zero-allocation remove/erase
        if (lbl_ins) overlay_labels_.erase(key);
        if (rev_uv) rev_overlay_.remove(u, v);
        if (rev_vu) rev_overlay_.remove(v, u);
        if (fwd_vu) fwd_overlay_.remove(v, u);
        if (fwd_uv) fwd_overlay_.remove(u, v);
        throw;
    }

    dynamic_edge_count_++;
    current_edges_count_++;

    if (in_tx_) {
        tx_record_.op = TxOpType::INSERT;
        tx_record_.u = u;
        tx_record_.v = v;
        tx_record_.label = label;
        tx_record_.key = key;
        tx_record_.was_in_tombstones = (tombstones_.find(key) != tombstones_.end());
        tx_record_.fwd_uv_inserted = fwd_uv;
        tx_record_.fwd_vu_inserted = fwd_vu;
        tx_record_.rev_vu_inserted = rev_vu;
        tx_record_.rev_uv_inserted = rev_uv;
        tx_record_.overlay_label_inserted = lbl_ins;
    } else {
        tombstones_.erase(key);
    }

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
    bool in_overlay = (overlay_labels_.find(key) != overlay_labels_.end());

    if (in_tx_) {
        tombstones_.insert(key);
        tx_record_.op = TxOpType::DELETE;
        tx_record_.u = u;
        tx_record_.v = v;
        tx_record_.label = label;
        tx_record_.key = key;
        tx_record_.was_in_overlay = in_overlay;
        if (in_overlay) {
            dynamic_edge_count_--;
        }
        current_edges_count_--;
        return true;
    }

    if (in_overlay) {
        overlay_labels_.erase(key);
        fwd_overlay_.remove(u, v);
        fwd_overlay_.remove(v, u);
        rev_overlay_.remove(v, u);
        rev_overlay_.remove(u, v);
        dynamic_edge_count_--;
    }
    if (base_edge_labels_.find(key) != base_edge_labels_.end()) {
        tombstones_.insert(key);
    }
    current_edges_count_--;
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
