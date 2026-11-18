#ifndef RAPIDFLOW_APX_TYPED_CODEC_H
#define RAPIDFLOW_APX_TYPED_CODEC_H

#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <stdexcept>
#include <algorithm>
#include <functional>
#include <limits>

namespace rapidflow_apx {

class CodecException : public std::runtime_error {
public:
    explicit CodecException(const std::string& msg) : std::runtime_error(msg) {}
};

class EntityNotFoundException : public CodecException {
public:
    explicit EntityNotFoundException(const std::string& msg) : CodecException(msg) {}
};

class CapacityExceededException : public CodecException {
public:
    explicit CapacityExceededException(const std::string& msg) : CodecException(msg) {}
};

enum class LabelType : uint8_t {
    INT64 = 0,
    STRING = 1
};

struct TypedLabel {
    LabelType type{LabelType::INT64};
    int64_t int_val{0};
    std::string str_val{};

    static TypedLabel from_int(int64_t val) {
        TypedLabel l;
        l.type = LabelType::INT64;
        l.int_val = val;
        return l;
    }

    static TypedLabel from_string(const std::string& val) {
        TypedLabel l;
        l.type = LabelType::STRING;
        l.str_val = val;
        return l;
    }

    bool operator==(const TypedLabel& other) const {
        if (type != other.type) return false;
        if (type == LabelType::INT64) return int_val == other.int_val;
        return str_val == other.str_val;
    }

    bool operator!=(const TypedLabel& other) const {
        return !(*this == other);
    }

    bool operator<(const TypedLabel& other) const {
        if (type != other.type) {
            return static_cast<uint8_t>(type) < static_cast<uint8_t>(other.type);
        }
        if (type == LabelType::INT64) {
            return int_val < other.int_val;
        }
        return str_val < other.str_val;
    }
};

} // namespace rapidflow_apx

namespace std {
template <>
struct hash<rapidflow_apx::TypedLabel> {
    size_t operator()(const rapidflow_apx::TypedLabel& l) const noexcept {
        size_t h1 = std::hash<uint8_t>{}(static_cast<uint8_t>(l.type));
        size_t h2 = (l.type == rapidflow_apx::LabelType::INT64)
                    ? std::hash<int64_t>{}(l.int_val)
                    : std::hash<std::string>{}(l.str_val);
        return h1 ^ (h2 + 0x9e3779b9 + (h1 << 6) + (h1 >> 2));
    }
};
} // namespace std

namespace rapidflow_apx {

class ApxTypedCodec {
private:
    // Separate vertex ID mapping tables
    std::unordered_map<int64_t, uint32_t> query_ext_to_dense_;
    std::vector<int64_t> query_dense_to_ext_;

    std::unordered_map<int64_t, uint32_t> data_ext_to_dense_;
    std::vector<int64_t> data_dense_to_ext_;

    // Separate label intern dictionaries
    std::unordered_map<TypedLabel, uint32_t> vertex_label_to_id_;
    std::vector<TypedLabel> vertex_id_to_label_;

    std::unordered_map<TypedLabel, uint32_t> edge_label_to_id_;
    std::vector<TypedLabel> edge_id_to_label_;

public:
    ApxTypedCodec() = default;

    // --- Vertex ID Management ---
    uint32_t intern_query_vertex(int64_t ext_id);
    uint32_t intern_data_vertex(int64_t ext_id);

    uint32_t get_query_vertex(int64_t ext_id) const;
    uint32_t get_data_vertex(int64_t ext_id) const;

    bool has_query_vertex(int64_t ext_id) const;
    bool has_data_vertex(int64_t ext_id) const;

    int64_t decode_query_vertex(uint32_t dense_id) const;
    int64_t decode_data_vertex(uint32_t dense_id) const;

    size_t query_vertices_count() const { return query_dense_to_ext_.size(); }
    size_t data_vertices_count() const { return data_dense_to_ext_.size(); }

    // Returns dense IDs of all query vertices sorted by ascending external ID
    std::vector<uint32_t> get_query_canonical_order() const;

    // --- Typed Label Management ---
    uint32_t intern_vertex_label(const TypedLabel& label);
    uint32_t intern_edge_label(const TypedLabel& label);

    uint32_t get_vertex_label_id(const TypedLabel& label) const;
    uint32_t get_edge_label_id(const TypedLabel& label) const;

    bool has_vertex_label(const TypedLabel& label) const;
    bool has_edge_label(const TypedLabel& label) const;

    TypedLabel decode_vertex_label(uint32_t label_id) const;
    TypedLabel decode_edge_label(uint32_t label_id) const;

    size_t vertex_labels_count() const { return vertex_id_to_label_.size(); }
    size_t edge_labels_count() const { return edge_id_to_label_.size(); }

    // --- Capacity Limits ---
    static void check_capacity_limits(size_t vertex_count, size_t edge_count);
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_TYPED_CODEC_H
