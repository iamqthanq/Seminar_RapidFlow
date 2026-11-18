#ifndef RAPIDFLOW_APX_MICRO_MATCHER_H
#define RAPIDFLOW_APX_MICRO_MATCHER_H

#include <cstdint>
#include <vector>
#include <string>
#include <stdexcept>
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"

namespace rapidflow_apx {

struct FixedMappingEvaluation {
    bool is_valid{false};
    uint32_t matched_edges{0};
    uint32_t missing_edges{0};
    uint32_t computed_lambda{0};
    bool s2_spanning_connected{false};
    std::string diagnostic_message;
};

class MicroExactMatcher {
public:
    // k must be 0 for exact matching; k > 0 throws std::invalid_argument
    explicit MicroExactMatcher(uint32_t k = 0);

    // Snapshot matching: finds all embeddings of Q in G
    uint64_t match_snapshot(
        const ApxAuthoritativeGraph& query,
        const ApxAuthoritativeGraph& data,
        ApxTupleSink& sink
    );

    // Delta matching: finds all embeddings of Q in G that contain the updated edge (u, v)
    uint64_t match_delta(
        const ApxAuthoritativeGraph& query,
        const ApxAuthoritativeGraph& data,
        uint32_t dense_u,
        uint32_t dense_v,
        uint32_t dense_label,
        ApxTupleSink& sink
    );

    // Exact edge connectivity calculation for undirected query graph lambda(Q)
    static uint32_t compute_edge_connectivity(const ApxAuthoritativeGraph& query);

    // Fixed-mapping evaluator with strict D-R guard (k < lambda) and S2 spanning connectivity
    static FixedMappingEvaluation evaluate_fixed_mapping(
        const ApxAuthoritativeGraph& query,
        const ApxAuthoritativeGraph& data,
        const std::vector<uint32_t>& mapping_by_query_order,
        uint32_t k,
        uint32_t lambda
    );

private:
    uint32_t k_{0};

    void dfs_snapshot(
        const ApxAuthoritativeGraph& query,
        const ApxAuthoritativeGraph& data,
        std::vector<uint32_t>& mapping,
        std::vector<bool>& query_mapped,
        std::vector<bool>& data_used,
        uint32_t mapped_count,
        ApxTupleSink& sink,
        uint64_t& match_count
    );

    void dfs_delta(
        const ApxAuthoritativeGraph& query,
        const ApxAuthoritativeGraph& data,
        std::vector<uint32_t>& mapping,
        std::vector<bool>& query_mapped,
        std::vector<bool>& data_used,
        uint32_t mapped_count,
        ApxTupleSink& sink,
        uint64_t& match_count
    );

    static bool check_all_edges(
        const ApxAuthoritativeGraph& query,
        const ApxAuthoritativeGraph& data,
        const std::vector<uint32_t>& mapping
    );
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_MICRO_MATCHER_H
