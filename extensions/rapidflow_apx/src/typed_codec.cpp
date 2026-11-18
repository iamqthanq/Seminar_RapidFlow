#include "rapidflow_apx/typed_codec.h"

namespace rapidflow_apx {

CodecCheckpoint ApxTypedCodec::create_checkpoint() const noexcept {
    CodecCheckpoint cp;
    cp.query_vertices = query_dense_to_ext_.size();
    cp.data_vertices = data_dense_to_ext_.size();
    cp.vertex_labels = vertex_id_to_label_.size();
    cp.edge_labels = edge_id_to_label_.size();
    return cp;
}

void ApxTypedCodec::restore_checkpoint(const CodecCheckpoint& cp) noexcept {
    while (query_dense_to_ext_.size() > cp.query_vertices) {
        int64_t ext = query_dense_to_ext_.back();
        query_ext_to_dense_.erase(ext);
        query_dense_to_ext_.pop_back();
    }
    while (data_dense_to_ext_.size() > cp.data_vertices) {
        int64_t ext = data_dense_to_ext_.back();
        data_ext_to_dense_.erase(ext);
        data_dense_to_ext_.pop_back();
    }
    while (vertex_id_to_label_.size() > cp.vertex_labels) {
        const auto& lbl = vertex_id_to_label_.back();
        vertex_label_to_id_.erase(lbl);
        vertex_id_to_label_.pop_back();
    }
    while (edge_id_to_label_.size() > cp.edge_labels) {
        const auto& lbl = edge_id_to_label_.back();
        edge_label_to_id_.erase(lbl);
        edge_id_to_label_.pop_back();
    }
}

uint32_t ApxTypedCodec::intern_query_vertex(int64_t ext_id) {
    auto it = query_ext_to_dense_.find(ext_id);
    if (it != query_ext_to_dense_.end()) {
        return it->second;
    }
    if (query_dense_to_ext_.size() >= std::numeric_limits<uint32_t>::max() - 1) {
        throw CapacityExceededException("Query vertex capacity exceeded uint32_t limit");
    }
    uint32_t dense_id = static_cast<uint32_t>(query_dense_to_ext_.size());
    auto ins = query_ext_to_dense_.insert({ext_id, dense_id});
    if (!ins.second) {
        return ins.first->second;
    }
    try {
        query_dense_to_ext_.push_back(ext_id);
    } catch (...) {
        query_ext_to_dense_.erase(ins.first);
        throw;
    }
    return dense_id;
}

uint32_t ApxTypedCodec::intern_data_vertex(int64_t ext_id) {
    auto it = data_ext_to_dense_.find(ext_id);
    if (it != data_ext_to_dense_.end()) {
        return it->second;
    }
    if (data_dense_to_ext_.size() >= std::numeric_limits<uint32_t>::max() - 1) {
        throw CapacityExceededException("Data vertex capacity exceeded uint32_t limit");
    }
    uint32_t dense_id = static_cast<uint32_t>(data_dense_to_ext_.size());
    auto ins = data_ext_to_dense_.insert({ext_id, dense_id});
    if (!ins.second) {
        return ins.first->second;
    }
    try {
        data_dense_to_ext_.push_back(ext_id);
    } catch (...) {
        data_ext_to_dense_.erase(ins.first);
        throw;
    }
    return dense_id;
}

uint32_t ApxTypedCodec::get_query_vertex(int64_t ext_id) const {
    auto it = query_ext_to_dense_.find(ext_id);
    if (it == query_ext_to_dense_.end()) {
        throw EntityNotFoundException("Query vertex not found: " + std::to_string(ext_id));
    }
    return it->second;
}

uint32_t ApxTypedCodec::get_data_vertex(int64_t ext_id) const {
    auto it = data_ext_to_dense_.find(ext_id);
    if (it == data_ext_to_dense_.end()) {
        throw EntityNotFoundException("Data vertex not found: " + std::to_string(ext_id));
    }
    return it->second;
}

bool ApxTypedCodec::has_query_vertex(int64_t ext_id) const {
    return query_ext_to_dense_.find(ext_id) != query_ext_to_dense_.end();
}

bool ApxTypedCodec::has_data_vertex(int64_t ext_id) const {
    return data_ext_to_dense_.find(ext_id) != data_ext_to_dense_.end();
}

int64_t ApxTypedCodec::decode_query_vertex(uint32_t dense_id) const {
    if (dense_id >= query_dense_to_ext_.size()) {
        throw EntityNotFoundException("Query dense vertex index out of bounds: " + std::to_string(dense_id));
    }
    return query_dense_to_ext_[dense_id];
}

int64_t ApxTypedCodec::decode_data_vertex(uint32_t dense_id) const {
    if (dense_id >= data_dense_to_ext_.size()) {
        throw EntityNotFoundException("Data dense vertex index out of bounds: " + std::to_string(dense_id));
    }
    return data_dense_to_ext_[dense_id];
}

std::vector<uint32_t> ApxTypedCodec::get_query_canonical_order() const {
    // Collect all query dense IDs and sort by ascending external ID
    std::vector<uint32_t> order(query_dense_to_ext_.size());
    for (size_t i = 0; i < order.size(); ++i) {
        order[i] = static_cast<uint32_t>(i);
    }
    std::sort(order.begin(), order.end(), [this](uint32_t a, uint32_t b) {
        return query_dense_to_ext_[a] < query_dense_to_ext_[b];
    });
    return order;
}

uint32_t ApxTypedCodec::intern_vertex_label(const TypedLabel& label) {
    auto it = vertex_label_to_id_.find(label);
    if (it != vertex_label_to_id_.end()) {
        return it->second;
    }
    if (vertex_id_to_label_.size() >= std::numeric_limits<uint32_t>::max() - 1) {
        throw CapacityExceededException("Vertex label capacity exceeded uint32_t limit");
    }
    uint32_t label_id = static_cast<uint32_t>(vertex_id_to_label_.size());
    auto ins = vertex_label_to_id_.insert({label, label_id});
    if (!ins.second) {
        return ins.first->second;
    }
    try {
        vertex_id_to_label_.push_back(label);
    } catch (...) {
        vertex_label_to_id_.erase(ins.first);
        throw;
    }
    return label_id;
}

uint32_t ApxTypedCodec::intern_edge_label(const TypedLabel& label) {
    auto it = edge_label_to_id_.find(label);
    if (it != edge_label_to_id_.end()) {
        return it->second;
    }
    if (edge_id_to_label_.size() >= std::numeric_limits<uint32_t>::max() - 1) {
        throw CapacityExceededException("Edge label capacity exceeded uint32_t limit");
    }
    uint32_t label_id = static_cast<uint32_t>(edge_id_to_label_.size());
    auto ins = edge_label_to_id_.insert({label, label_id});
    if (!ins.second) {
        return ins.first->second;
    }
    try {
        edge_id_to_label_.push_back(label);
    } catch (...) {
        edge_label_to_id_.erase(ins.first);
        throw;
    }
    return label_id;
}

uint32_t ApxTypedCodec::get_vertex_label_id(const TypedLabel& label) const {
    auto it = vertex_label_to_id_.find(label);
    if (it == vertex_label_to_id_.end()) {
        throw EntityNotFoundException("Vertex label not found in dictionary");
    }
    return it->second;
}

uint32_t ApxTypedCodec::get_edge_label_id(const TypedLabel& label) const {
    auto it = edge_label_to_id_.find(label);
    if (it == edge_label_to_id_.end()) {
        throw EntityNotFoundException("Edge label not found in dictionary");
    }
    return it->second;
}

bool ApxTypedCodec::has_vertex_label(const TypedLabel& label) const {
    return vertex_label_to_id_.find(label) != vertex_label_to_id_.end();
}

bool ApxTypedCodec::has_edge_label(const TypedLabel& label) const {
    return edge_label_to_id_.find(label) != edge_label_to_id_.end();
}

TypedLabel ApxTypedCodec::decode_vertex_label(uint32_t label_id) const {
    if (label_id >= vertex_id_to_label_.size()) {
        throw EntityNotFoundException("Vertex label ID out of bounds: " + std::to_string(label_id));
    }
    return vertex_id_to_label_[label_id];
}

TypedLabel ApxTypedCodec::decode_edge_label(uint32_t label_id) const {
    if (label_id >= edge_id_to_label_.size()) {
        throw EntityNotFoundException("Edge label ID out of bounds: " + std::to_string(label_id));
    }
    return edge_id_to_label_[label_id];
}

void ApxTypedCodec::check_capacity_limits(size_t vertex_count, size_t edge_count) {
    if (vertex_count >= static_cast<size_t>(std::numeric_limits<uint32_t>::max() - 1)) {
        throw CapacityExceededException("Vertex count exceeds RapidFlow uint32 CSR limit");
    }
    // Since CSR stores undirected edges as 2 directed edges: 2*E must not overflow uint32
    if (edge_count > static_cast<size_t>(std::numeric_limits<uint32_t>::max() / 2)) {
        throw CapacityExceededException("Edge count exceeds RapidFlow directed edge capacity (2E > UINT32_MAX)");
    }
}

} // namespace rapidflow_apx
