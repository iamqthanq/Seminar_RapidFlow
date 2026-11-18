#include "rapidflow_apx/tuple_sink.h"
#include <algorithm>
#include <unordered_set>

namespace rapidflow_apx {

ApxTupleSink::ApxTupleSink(const ApxTypedCodec& codec, const std::vector<uint32_t>& query_vertex_order)
    : codec_(codec), query_vertex_order_(query_vertex_order) {}

void ApxTupleSink::start_step() {
    current_batch_seen_.clear();
    pending_tuples_.clear();
}

bool ApxTupleSink::emit_tuple(const std::vector<uint32_t>& mapping_by_query_order) {
    if (mapping_by_query_order.size() != query_vertex_order_.size()) {
        throw std::invalid_argument("TupleSink: mapping size does not match query vertex count");
    }

    // 1. Injectivity Check
    std::unordered_set<uint32_t> seen_data_vertices;
    for (uint32_t dv : mapping_by_query_order) {
        if (!seen_data_vertices.insert(dv).second) {
            throw NonInjectiveMappingException("TupleSink: non-injective mapping detected");
        }
    }

    // 2. Convert to external IDs
    std::vector<int64_t> ext_tuple;
    ext_tuple.reserve(mapping_by_query_order.size());
    for (uint32_t dv : mapping_by_query_order) {
        ext_tuple.push_back(codec_.decode_data_vertex(dv));
    }

    raw_emissions_++;

    // 3. Batch Duplicate Detection
    if (current_batch_seen_.find(ext_tuple) != current_batch_seen_.end()) {
        throw DuplicateEmissionException("TupleSink: duplicate raw emission detected in same batch");
    }

    current_batch_seen_.insert(ext_tuple);
    unique_emissions_++;
    pending_tuples_.push_back(std::move(ext_tuple));
    return true;
}

bool ApxTupleSink::emit_mapping(const std::vector<uint32_t>& f_by_dense_u) {
    std::vector<uint32_t> ordered_mapping;
    ordered_mapping.reserve(query_vertex_order_.size());
    for (uint32_t q_dense : query_vertex_order_) {
        if (q_dense >= f_by_dense_u.size()) {
            throw std::out_of_range("TupleSink: dense query vertex out of range in mapping");
        }
        ordered_mapping.push_back(f_by_dense_u[q_dense]);
    }
    return emit_tuple(ordered_mapping);
}

std::vector<std::vector<int64_t>> ApxTupleSink::commit_step() {
    committed_emissions_ += pending_tuples_.size();
    auto result = std::move(pending_tuples_);
    pending_tuples_.clear();
    current_batch_seen_.clear();
    return result;
}

void ApxTupleSink::abort_step() {
    discarded_emissions_ += pending_tuples_.size();
    pending_tuples_.clear();
    current_batch_seen_.clear();
}

} // namespace rapidflow_apx
