#ifndef APX03_MATCHER_H
#define APX03_MATCHER_H

#include <cstdint>
#include <vector>
#include <map>
#include <unordered_set>
#include "apx03_types.h"
#include "apx03_graph.h"

namespace apx03 {

class ApxMatcher {
public:
    ApxMatcher(
        const ApxGraph& Q,
        const ApxGraph& G,
        uint32_t eps_m,
        bool eps_x_unbounded,
        uint32_t eps_x,
        ConnectivityMode mode,
        uint64_t max_candidates = 10000000ULL,
        uint64_t max_search_steps = 10000000ULL
    );

    // Direct Evaluation Mode (single prescribed mapping)
    EvaluationResult evaluate_single_mapping(const std::vector<uint32_t>& mapping) const;

    // Full Enumeration Mode (complete backtracking)
    std::map<std::vector<uint32_t>, EvaluationResult> find_all_matches();

    uint64_t get_candidates_evaluated() const { return candidates_evaluated_; }
    uint64_t get_search_steps() const { return search_steps_; }

private:
    void backtrack(
        size_t depth,
        std::vector<uint32_t>& current_mapping,
        std::unordered_set<uint32_t>& used_data_nodes,
        uint32_t current_m_partial,
        std::map<std::vector<uint32_t>, EvaluationResult>& matches
    );

    bool check_connectivity_and_score(
        const std::vector<uint32_t>& mapping,
        DeviationScore& score
    ) const;

private:
    const ApxGraph& Q_;
    const ApxGraph& G_;
    uint32_t eps_m_;
    bool eps_x_unbounded_;
    uint32_t eps_x_;
    ConnectivityMode mode_;
    uint64_t max_candidates_;
    uint64_t max_search_steps_;

    std::vector<uint32_t> query_vertices_;
    std::vector<uint32_t> data_vertices_;
    uint64_t candidates_evaluated_ = 0;
    uint64_t search_steps_ = 0;
};

} // namespace apx03

#endif // APX03_MATCHER_H
