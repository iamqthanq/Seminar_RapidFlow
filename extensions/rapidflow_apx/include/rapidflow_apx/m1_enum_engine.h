#ifndef RAPIDFLOW_APX_M1_ENUM_ENGINE_H
#define RAPIDFLOW_APX_M1_ENUM_ENGINE_H

#include <cstdint>
#include <vector>
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/engine_counters.h"
#include "rapidflow_apx/profiler.h"

namespace rapidflow_apx {

class ApxM1EnumEngine {
public:
    static constexpr uint32_t UNASSIGNED = 0xFFFFFFFF;

    explicit ApxM1EnumEngine() = default;

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
    void match_variant_dfs(
        const M1VariantPlan& variant,
        size_t idx,
        std::vector<uint32_t>& mapping,
        std::vector<bool>& used_data,
        const ApxQueryPlan& query_plan,
        const ApxAuthoritativeGraph& snapshot,
        ApxTupleSink& sink,
        EngineCounters& counters,
        StepProfiling* profiler = nullptr
    );
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_M1_ENUM_ENGINE_H
