#ifndef APX03_DYNAMIC_ENGINE_H
#define APX03_DYNAMIC_ENGINE_H

#include <map>
#include <vector>
#include "apx03_types.h"
#include "apx03_graph.h"
#include "apx03_matcher.h"

namespace apx03 {

class ApxDynamicEngine {
public:
    ApxDynamicEngine(
        ApxGraph Q,
        ApxGraph G0,
        uint32_t eps_m,
        bool eps_x_unbounded,
        uint32_t eps_x,
        ConnectivityMode mode,
        uint64_t max_candidates = 10000000ULL,
        uint64_t max_search_steps = 10000000ULL
    );

    // Dynamic step execution
    StepDelta apply_update(const Update& update);

    // Current state access
    const std::map<std::vector<uint32_t>, EvaluationResult>& get_current_matches() const {
        return current_matches_;
    }

    const ApxGraph& get_current_data_graph() const { return G_; }
    const ApxGraph& get_query_graph() const { return Q_; }

    bool is_terminal_error() const { return is_terminal_error_; }
    const std::string& get_terminal_error_msg() const { return terminal_error_msg_; }

private:
    ApxGraph Q_;
    ApxGraph G_;
    uint32_t eps_m_;
    bool eps_x_unbounded_;
    uint32_t eps_x_;
    ConnectivityMode mode_;
    uint64_t max_candidates_;
    uint64_t max_search_steps_;

    bool is_terminal_error_ = false;
    std::string terminal_error_msg_;

    std::map<std::vector<uint32_t>, EvaluationResult> current_matches_;
};

} // namespace apx03

#endif // APX03_DYNAMIC_ENGINE_H
