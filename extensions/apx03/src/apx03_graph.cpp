#include "apx03_graph.h"
#include <fstream>
#include <sstream>
#include <algorithm>
#include <tuple>

namespace apx03 {

const std::unordered_map<uint32_t, uint32_t> ApxGraph::empty_neighbors_;

ApxGraph::ApxGraph() : rf_graph_(nullptr) {}

ApxGraph::~ApxGraph() = default;

ApxGraph::ApxGraph(ApxGraph&&) noexcept = default;
ApxGraph& ApxGraph::operator=(ApxGraph&&) noexcept = default;

void ApxGraph::load_from_file(const std::string& path) {
    std::ifstream infile(path);
    if (!infile.is_open()) {
        throw ApxException("Cannot open graph file: " + path);
    }
    std::stringstream buffer;
    buffer << infile.rdbuf();
    load_from_text(buffer.str());
}

void ApxGraph::load_from_text(const std::string& text) {
    raw_to_dense_.clear();
    dense_to_raw_.clear();
    dense_labels_.clear();
    raw_labels_.clear();
    edge_labels_.clear();
    adj_.clear();
    degrees_.clear();
    edges_list_.clear();

    struct RawEdge {
        uint32_t u;
        uint32_t v;
        uint32_t label;
    };
    std::vector<RawEdge> parsed_edges;

    std::istringstream stream(text);
    std::string line;
    uint32_t line_num = 0;

    while (std::getline(stream, line)) {
        line_num++;
        // Trim whitespace
        size_t first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos || line[first] == '#') {
            continue;
        }
        size_t last = line.find_last_not_of(" \t\r\n");
        std::string trimmed = line.substr(first, (last - first + 1));

        std::istringstream ls(trimmed);
        std::string tag;
        ls >> tag;

        if (tag == "v") {
            uint32_t vid = 0;
            uint32_t label = 0;
            if (!(ls >> vid >> label)) {
                throw ApxException("Malformed vertex record at line " + std::to_string(line_num) + ": " + line);
            }
            if (raw_labels_.find(vid) == raw_labels_.end()) {
                uint32_t dense_id = static_cast<uint32_t>(dense_to_raw_.size());
                raw_to_dense_[vid] = dense_id;
                dense_to_raw_.push_back(vid);
                dense_labels_.push_back(label);
                raw_labels_[vid] = label;
                adj_[vid] = {};
                degrees_[vid] = 0;
            } else {
                if (raw_labels_[vid] != label) {
                    throw ConflictingVertexRedefinitionException(
                        "Conflicting label redefinition for vertex " + std::to_string(vid) +
                        " (existing=" + std::to_string(raw_labels_[vid]) +
                        ", new=" + std::to_string(label) + ") at line " + std::to_string(line_num)
                    );
                }
            }
        } else if (tag == "e") {
            uint32_t u = 0, v = 0, label = 0;
            if (!(ls >> u >> v >> label)) {
                throw ApxException("Malformed edge record at line " + std::to_string(line_num) + ": " + line);
            }
            if (raw_labels_.find(u) == raw_labels_.end()) {
                throw UnknownVertexException("Endpoint " + std::to_string(u) + " not declared prior to edge record at line " + std::to_string(line_num));
            }
            if (raw_labels_.find(v) == raw_labels_.end()) {
                throw UnknownVertexException("Endpoint " + std::to_string(v) + " not declared prior to edge record at line " + std::to_string(line_num));
            }
            if (u == v) {
                throw SelfLoopException("Self-loops not permitted in simple graph at line " + std::to_string(line_num));
            }

            uint64_t key = edge_key(u, v);
            if (edge_labels_.find(key) != edge_labels_.end()) {
                throw DuplicateEdgeInsertionException("Duplicate edge detected at line " + std::to_string(line_num));
            }

            edge_labels_[key] = label;
            adj_[u][v] = label;
            adj_[v][u] = label;
            degrees_[u]++;
            degrees_[v]++;
            parsed_edges.push_back({u, v, label});
        } else if (tag == "t") {
            continue; // Header tag
        } else {
            throw ApxException("Unsupported record tag '" + tag + "' at line " + std::to_string(line_num));
        }
    }

    // Build unique edges list
    edges_list_.clear();
    for (const auto& pe : parsed_edges) {
        uint32_t mn = std::min(pe.u, pe.v);
        uint32_t mx = std::max(pe.u, pe.v);
        edges_list_.push_back({mn, mx});
    }

    // Native RapidFlow CSR Graph Instantiation
    // Genuinely compiles and links RapidFlow Graph from baselines/rapidflow/source/graph/graph.cpp
    std::vector<std::pair<uint32_t, uint32_t>> rf_vertices;
    rf_vertices.reserve(dense_to_raw_.size());
    for (size_t i = 0; i < dense_to_raw_.size(); ++i) {
        rf_vertices.push_back({static_cast<uint32_t>(i), dense_labels_[i]});
    }

    std::vector<std::tuple<uint32_t, uint32_t, uint32_t>> rf_edges;
    rf_edges.reserve(edges_list_.size());
    for (const auto& pe : parsed_edges) {
        uint32_t du = raw_to_dense_[pe.u];
        uint32_t dv = raw_to_dense_[pe.v];
        rf_edges.push_back(std::make_tuple(du, dv, pe.label));
    }

    rf_graph_ = std::make_unique<Graph>(false);
    rf_graph_->is_edge_labeled = true;
    rf_graph_->loadGraphFromMemory(rf_vertices, rf_edges);
}

void ApxGraph::rebuild_edges_list() {
    edges_list_.clear();
    for (const auto& kv : edge_labels_) {
        uint32_t u = static_cast<uint32_t>(kv.first >> 32);
        uint32_t v = static_cast<uint32_t>(kv.first & 0xFFFFFFFFULL);
        edges_list_.push_back({u, v});
    }
}

void ApxGraph::apply_update(const Update& update) {
    if (raw_labels_.find(update.u) == raw_labels_.end()) {
        throw UnknownVertexException("Update endpoint u=" + std::to_string(update.u) + " not in vertex universe");
    }
    if (raw_labels_.find(update.v) == raw_labels_.end()) {
        throw UnknownVertexException("Update endpoint v=" + std::to_string(update.v) + " not in vertex universe");
    }
    if (update.u == update.v) {
        throw SelfLoopException("Update introduces self-loop on vertex " + std::to_string(update.u));
    }

    uint64_t key = edge_key(update.u, update.v);

    if (update.op == '+') {
        if (edge_labels_.find(key) != edge_labels_.end()) {
            throw DuplicateEdgeInsertionException("Duplicate edge insertion: (" + std::to_string(update.u) + ", " + std::to_string(update.v) + ")");
        }
        edge_labels_[key] = update.label;
        adj_[update.u][update.v] = update.label;
        adj_[update.v][update.u] = update.label;
        degrees_[update.u]++;
        degrees_[update.v]++;
        rebuild_edges_list();
    } else if (update.op == '-') {
        auto it = edge_labels_.find(key);
        if (it == edge_labels_.end()) {
            throw NonexistentEdgeDeletionException("Cannot delete nonexistent edge: (" + std::to_string(update.u) + ", " + std::to_string(update.v) + ")");
        }
        if (it->second != update.label) {
            throw EdgeLabelMismatchDeletionException("Edge label mismatch on deletion: expected " + std::to_string(it->second) + ", got " + std::to_string(update.label));
        }
        edge_labels_.erase(it);
        adj_[update.u].erase(update.v);
        adj_[update.v].erase(update.u);
        degrees_[update.u]--;
        degrees_[update.v]--;
        rebuild_edges_list();
    } else {
        throw ApxException("Unsupported update operation: " + std::string(1, update.op));
    }
}

bool ApxGraph::has_vertex(uint32_t v) const {
    return raw_labels_.find(v) != raw_labels_.end();
}

uint32_t ApxGraph::get_vertex_label(uint32_t v) const {
    auto it = raw_labels_.find(v);
    if (it == raw_labels_.end()) {
        throw UnknownVertexException("Vertex not found: " + std::to_string(v));
    }
    return it->second;
}

bool ApxGraph::has_edge(uint32_t u, uint32_t v) const {
    return edge_labels_.find(edge_key(u, v)) != edge_labels_.end();
}

uint32_t ApxGraph::get_edge_label(uint32_t u, uint32_t v) const {
    auto it = edge_labels_.find(edge_key(u, v));
    if (it == edge_labels_.end()) {
        throw ApxException("Edge not found: (" + std::to_string(u) + ", " + std::to_string(v) + ")");
    }
    return it->second;
}

uint32_t ApxGraph::get_degree(uint32_t v) const {
    auto it = degrees_.find(v);
    if (it == degrees_.end()) {
        return 0;
    }
    return it->second;
}

const std::unordered_map<uint32_t, uint32_t>& ApxGraph::get_neighbors(uint32_t v) const {
    auto it = adj_.find(v);
    if (it == adj_.end()) {
        return empty_neighbors_;
    }
    return it->second;
}

bool ApxGraph::verify_against_native_csr() const {
    if (!rf_graph_) {
        return false;
    }
    if (rf_graph_->getVerticesCount() != dense_to_raw_.size()) {
        return false;
    }
    if (rf_graph_->getEdgesCount() != edges_list_.size()) {
        return false;
    }
    for (size_t du = 0; du < dense_to_raw_.size(); ++du) {
        uint32_t raw_u = dense_to_raw_[du];
        if (rf_graph_->getVertexLabel(static_cast<VertexID>(du)) != dense_labels_[du]) {
            return false;
        }
        ui count = 0;
        const VertexID* neighbors = rf_graph_->getVertexNeighbors(static_cast<VertexID>(du), count);
        auto it = adj_.find(raw_u);
        size_t expected_count = (it == adj_.end()) ? 0 : it->second.size();
        if (static_cast<size_t>(count) != expected_count) {
            return false;
        }
        for (ui i = 0; i < count; ++i) {
            uint32_t dv = neighbors[i];
            uint32_t raw_v = dense_to_raw_[dv];
            if (it->second.find(raw_v) == it->second.end()) {
                return false;
            }
            uint32_t expected_label = it->second.at(raw_v);
            uint32_t rf_label = rf_graph_->getEdgeLabelByVertex(static_cast<uint32_t>(du), dv);
            if (rf_label != expected_label) {
                return false;
            }
        }
    }
    return true;
}

} // namespace apx03
