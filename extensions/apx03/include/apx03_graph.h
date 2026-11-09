#ifndef APX03_GRAPH_H
#define APX03_GRAPH_H

#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <memory>
#include "graph/graph.h"
#include "apx03_types.h"

namespace apx03 {

class ApxGraph {
public:
    ApxGraph();
    ~ApxGraph();

    // Disable copy, allow move
    ApxGraph(const ApxGraph&) = delete;
    ApxGraph& operator=(const ApxGraph&) = delete;
    ApxGraph(ApxGraph&&) noexcept;
    ApxGraph& operator=(ApxGraph&&) noexcept;

    // Load graph from formatted text or file
    void load_from_text(const std::string& text);
    void load_from_file(const std::string& path);

    // Dynamic streaming mutation
    void apply_update(const Update& update);

    // Query methods (using raw vertex IDs)
    bool has_vertex(uint32_t v) const;
    uint32_t get_vertex_label(uint32_t v) const;
    bool has_edge(uint32_t u, uint32_t v) const;
    uint32_t get_edge_label(uint32_t u, uint32_t v) const;
    uint32_t get_degree(uint32_t v) const;
    const std::unordered_map<uint32_t, uint32_t>& get_neighbors(uint32_t v) const;

    // Collection accessors
    const std::vector<uint32_t>& get_all_vertices() const { return dense_to_raw_; }
    const std::vector<std::pair<uint32_t, uint32_t>>& get_all_edges() const { return edges_list_; }
    uint32_t num_vertices() const { return static_cast<uint32_t>(dense_to_raw_.size()); }
    uint32_t num_edges() const { return static_cast<uint32_t>(edges_list_.size()); }

    // Direct access to native RapidFlow Graph
    const Graph* get_native_rf_graph() const { return rf_graph_.get(); }

private:
    uint64_t edge_key(uint32_t u, uint32_t v) const {
        uint32_t mn = std::min(u, v);
        uint32_t mx = std::max(u, v);
        return (static_cast<uint64_t>(mn) << 32) | static_cast<uint64_t>(mx);
    }

    void rebuild_edges_list();

private:
    // Native RapidFlow Graph CSR representation (compiled directly from RapidFlow source)
    std::unique_ptr<Graph> rf_graph_;

    // Bidirectional vertex ID mapping (raw external ID <-> dense 0..N-1)
    std::unordered_map<uint32_t, uint32_t> raw_to_dense_;
    std::vector<uint32_t> dense_to_raw_;
    std::vector<uint32_t> dense_labels_;
    std::unordered_map<uint32_t, uint32_t> raw_labels_;

    // Mutable adjacency structures for dynamic streaming
    std::unordered_map<uint64_t, uint32_t> edge_labels_;
    std::unordered_map<uint32_t, std::unordered_map<uint32_t, uint32_t>> adj_;
    std::unordered_map<uint32_t, uint32_t> degrees_;
    std::vector<std::pair<uint32_t, uint32_t>> edges_list_;

    static const std::unordered_map<uint32_t, uint32_t> empty_neighbors_;
};

} // namespace apx03

#endif // APX03_GRAPH_H
