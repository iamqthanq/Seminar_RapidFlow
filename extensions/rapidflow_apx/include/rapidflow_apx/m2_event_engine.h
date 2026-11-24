#ifndef RAPIDFLOW_APX_M2_EVENT_ENGINE_H
#define RAPIDFLOW_APX_M2_EVENT_ENGINE_H

#include <cstdint>
#include <vector>
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/profiler.h"

namespace rapidflow_apx {

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

class ApxM2EventEngine {
public:
    static constexpr uint32_t UNASSIGNED = 0xFFFFFFFF;

    explicit ApxM2EventEngine() = default;

    // Controlled test-only fault hook
    void set_fault_hook(M2FaultHook hook) noexcept { fault_hook_ = hook; }
    M2FaultHook get_fault_hook() const noexcept { return fault_hook_; }

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
        StepProfiling* profiler = nullptr
    );

private:
    M2FaultHook fault_hook_{M2FaultHook::NONE};

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
        StepProfiling* profiler = nullptr
    );
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_M2_EVENT_ENGINE_H
