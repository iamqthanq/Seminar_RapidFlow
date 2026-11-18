#ifndef RAPIDFLOW_APX_M2_EVENT_ENGINE_H
#define RAPIDFLOW_APX_M2_EVENT_ENGINE_H

#include <cstdint>
#include <vector>
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/engine_counters.h"

namespace rapidflow_apx {

enum class EdgeCommitment : uint8_t {
    UNKNOWN = 0,
    PRESENT = 1,
    MISSING = 2
};

class ApxM2EventEngine {
public:
    static constexpr uint32_t UNASSIGNED = 0xFFFFFFFF;

    explicit ApxM2EventEngine() = default;

    // Searches for entered or exited events around the updated edge
    // snapshot must be G_after for INSERT and G_before for DELETE
    uint64_t search_event(
        const ApxQueryPlan& query_plan,
        const ApxAuthoritativeGraph& snapshot,
        uint32_t u_upd,
        uint32_t v_upd,
        uint32_t label_upd,
        ApxTupleSink& sink,
        EngineCounters& counters
    );

private:
    void dfs_m2(
        std::vector<uint32_t>& mapping,
        std::vector<bool>& used_data,
        std::vector<EdgeCommitment>& status,
        uint32_t missing_count,
        uint32_t assigned_count,
        const ApxQueryPlan& query_plan,
        const ApxAuthoritativeGraph& snapshot,
        ApxTupleSink& sink,
        EngineCounters& counters
    );
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_M2_EVENT_ENGINE_H
