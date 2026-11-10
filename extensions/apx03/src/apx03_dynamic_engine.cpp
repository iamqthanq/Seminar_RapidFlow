#include "apx03_dynamic_engine.h"

namespace apx03 {

ApxDynamicEngine::ApxDynamicEngine(
    ApxGraph Q,
    ApxGraph G0,
    uint32_t eps_m,
    bool eps_x_unbounded,
    uint32_t eps_x,
    ConnectivityMode mode,
    uint64_t max_candidates,
    uint64_t max_search_steps
) : Q_(std::move(Q)),
    G_(std::move(G0)),
    eps_m_(eps_m),
    eps_x_unbounded_(eps_x_unbounded),
    eps_x_(eps_x),
    mode_(mode),
    max_candidates_(max_candidates),
    max_search_steps_(max_search_steps)
{
    ApxMatcher initial_matcher(Q_, G_, eps_m_, eps_x_unbounded_, eps_x_, mode_, max_candidates_, max_search_steps_);
    current_matches_ = initial_matcher.find_all_matches();
}

StepDelta ApxDynamicEngine::apply_update(const Update& update) {
    if (is_terminal_error_) {
        throw EngineTerminalErrorException("Engine in terminal error state: " + terminal_error_msg_);
    }

    // 1. Mutate data graph with atomic rollback/exception safety
    G_.apply_update(update);

    // 2. Recompute all matches from scratch under RECOMPUTE_BASELINE
    std::map<std::vector<uint32_t>, EvaluationResult> new_matches;
    try {
        ApxMatcher step_matcher(Q_, G_, eps_m_, eps_x_unbounded_, eps_x_, mode_, max_candidates_, max_search_steps_);
        new_matches = step_matcher.find_all_matches();
    } catch (const std::exception& ex) {
        // Rollback graph update to preserve previous state snapshot
        Update rollback_up = (update.op == '+') ? Update('-', update.u, update.v, update.label)
                                                : Update('+', update.u, update.v, update.label);
        try {
            G_.apply_update(rollback_up);
        } catch (...) {}
        is_terminal_error_ = true;
        terminal_error_msg_ = ex.what();
        throw;
    }

    // 3. Compute delta partitions
    StepDelta delta;

    // Check matches in new_matches
    for (const auto& kv : new_matches) {
        const auto& mapping = kv.first;
        const auto& eval_res = kv.second;

        auto it = current_matches_.find(mapping);
        if (it == current_matches_.end()) {
            // New match entered
            delta.entered[mapping] = eval_res.score;
        } else {
            // Existed in previous snapshot: compare (m, x_out)
            const auto& old_score = it->second.score;
            const auto& new_score = eval_res.score;

            if (old_score.m != new_score.m || old_score.x_out != new_score.x_out) {
                // Relevant score changed (x_in alone does NOT trigger score_changed)
                delta.score_changed[mapping] = {old_score, new_score};
            } else {
                delta.unchanged[mapping] = new_score;
            }
        }
    }

    // Check matches that exited
    for (const auto& kv : current_matches_) {
        const auto& mapping = kv.first;
        if (new_matches.find(mapping) == new_matches.end()) {
            delta.exited[mapping] = kv.second.score;
        }
    }

    // 4. Update state machine
    current_matches_ = std::move(new_matches);
    return delta;
}

} // namespace apx03
