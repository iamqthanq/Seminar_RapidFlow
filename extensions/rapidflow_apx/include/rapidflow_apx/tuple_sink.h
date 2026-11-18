#ifndef RAPIDFLOW_APX_TUPLE_SINK_H
#define RAPIDFLOW_APX_TUPLE_SINK_H

#include <cstdint>
#include <vector>
#include <set>
#include <stdexcept>
#include "rapidflow_apx/typed_codec.h"

namespace rapidflow_apx {

class DuplicateEmissionException : public std::runtime_error {
public:
    explicit DuplicateEmissionException(const std::string& msg) : std::runtime_error(msg) {}
};

class NonInjectiveMappingException : public std::runtime_error {
public:
    explicit NonInjectiveMappingException(const std::string& msg) : std::runtime_error(msg) {}
};

class ApxTupleSink {
public:
    ApxTupleSink(const ApxTypedCodec& codec, const std::vector<uint32_t>& query_vertex_order);

    // Staging Interface
    // Accepts mapping where coords are already ordered by ascending query external ID
    bool emit_tuple(const std::vector<uint32_t>& mapping_by_query_order);

    // Accepts mapping where array is indexed by internal dense query ID: f[dense_u]
    bool emit_mapping(const std::vector<uint32_t>& f_by_dense_u);

    // Transaction Interface
    void start_step();
    std::vector<std::vector<int64_t>> commit_step();
    void abort_step();

    // Counters
    uint64_t raw_emissions() const noexcept { return raw_emissions_; }
    uint64_t unique_emissions() const noexcept { return unique_emissions_; }
    uint64_t committed_emissions() const noexcept { return committed_emissions_; }
    uint64_t discarded_emissions() const noexcept { return discarded_emissions_; }

    const std::vector<uint32_t>& get_query_vertex_order() const noexcept {
        return query_vertex_order_;
    }

private:
    const ApxTypedCodec& codec_;
    std::vector<uint32_t> query_vertex_order_;

    std::set<std::vector<int64_t>> current_batch_seen_;
    std::vector<std::vector<int64_t>> pending_tuples_;

    uint64_t raw_emissions_{0};
    uint64_t unique_emissions_{0};
    uint64_t committed_emissions_{0};
    uint64_t discarded_emissions_{0};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_TUPLE_SINK_H
