#ifndef RAPIDFLOW_APX_M2_EVENT_ENGINE_H
#define RAPIDFLOW_APX_M2_EVENT_ENGINE_H

#include <cstdint>
#include <vector>
#ifdef APX_RESIDUAL_TEST_TRACE
#include <functional>
#endif
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/profiler.h"
#include "rapidflow_apx/nlf_signatures.h"
#include "rapidflow_apx/nlf_index.h"

namespace rapidflow_apx {

enum class NlfMode : uint8_t {
    NONE = 0,
    STATIC = 1,
    RESIDUAL = 2
};

enum class EdgeCommitment : uint8_t {
    UNKNOWN = 0,
    PRESENT = 1,
    MISSING = 2
};

enum class M2FaultHook : uint8_t {
    NONE = 0,
    AFTER_MISSING_COMMITMENT = 1,
    AFTER_VERTEX_ASSIGNMENT = 2
};

class ApxResidualMissingState;

#ifdef APX_RESIDUAL_TEST_TRACE
enum class ResidualTracePoint {
    ANCHOR, DFS_ENTRY, CANDIDATE, RESOLVED_MISSING, RESOLVED_INCIDENT, CHILD_RETURN,
    CANDIDATE_RESTORED, DEFERRED_MISSING, DEFERRED_RESTORED, ANCHOR_RESTORED
};

struct ResidualTrace {
    ResidualTracePoint point;
    const std::vector<uint32_t>& mapping;
    const std::vector<bool>& used_data;
    const std::vector<EdgeCommitment>& status;
    const ApxResidualMissingState& state;
    uint32_t missing_count;
    uint32_t edge;
    uint32_t candidate_query;
    uint32_t candidate_data;
    bool candidate_allowed;
};
#endif

class ApxM2EventEngine {
public:
    static constexpr uint32_t UNASSIGNED = 0xFFFFFFFF;

    explicit ApxM2EventEngine() = default;

    // Controlled test-only fault hook
    void set_fault_hook(M2FaultHook hook) noexcept { fault_hook_ = hook; }
    M2FaultHook get_fault_hook() const noexcept { return fault_hook_; }

#ifdef APX_RESIDUAL_TEST_TRACE
    using ResidualObserver = std::function<void(const ResidualTrace&)>;
    void set_residual_observer(ResidualObserver observer) { residual_observer_ = std::move(observer); }
#endif

    // Helper used by dfs_m2 to find the best frontier edge across the cut.
    // If no frontier edge exists while unmapped vertices remain and missing_count <= k,
    // it enforces the empty frontier invariant and throws InvariantViolationException.
    static int find_frontier_edge(
        const std::vector<uint32_t>& mapping,
        const std::vector<EdgeCommitment>& status,
        uint32_t missing_count,
        uint32_t assigned_count,
        const ApxQueryPlan& query_plan
    );

    // Searches for entered or exited events around the updated edge
    // snapshot must be G_after for INSERT and G_before for DELETE
    uint64_t search_event(
        const ApxQueryPlan& query_plan,
        const ApxAuthoritativeGraph& snapshot,
        uint32_t u_upd,
        uint32_t v_upd,
        uint32_t label_upd,
        ApxTupleSink& sink,
        EngineCounters& counters,
        StepProfiling* profiler = nullptr,
        NlfMode nlf_mode = NlfMode::NONE,
        const ApxNlfIndex* nlf_index = nullptr,
        const ApxQuerySignatures* signatures = nullptr
    );

private:
    M2FaultHook fault_hook_{M2FaultHook::NONE};
#ifdef APX_RESIDUAL_TEST_TRACE
    ResidualObserver residual_observer_;
#endif

    void dfs_m2(
        std::vector<uint32_t>& mapping,
        std::vector<bool>& used_data,
        std::vector<EdgeCommitment>& status,
        uint32_t missing_count,
        uint32_t assigned_count,
        const ApxQueryPlan& query_plan,
        const ApxAuthoritativeGraph& snapshot,
        ApxTupleSink& sink,
        EngineCounters& counters,
        StepProfiling* profiler,
        NlfMode nlf_mode,
        const ApxNlfIndex* nlf_index,
        const ApxQuerySignatures* signatures,
        ApxResidualMissingState* residual
    );
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_M2_EVENT_ENGINE_H
