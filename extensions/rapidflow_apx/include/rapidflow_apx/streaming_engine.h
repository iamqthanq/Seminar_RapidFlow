#ifndef RAPIDFLOW_APX_STREAMING_ENGINE_H
#define RAPIDFLOW_APX_STREAMING_ENGINE_H

#include <cstdint>
#include <vector>
#include <string>
#include "rapidflow_apx/query_plan.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/typed_codec.h"
#include "rapidflow_apx/transaction_manager.h"
#include "rapidflow_apx/tuple_sink.h"
#include "rapidflow_apx/m1_enum_engine.h"
#include "rapidflow_apx/m2_event_engine.h"
#include "rapidflow_apx/engine_counters.h"

namespace rapidflow_apx {

enum class EngineType {
    M1,
    M2
};

struct StreamStepResult {
    uint32_t step_index{0};
    UpdateOp op{UpdateOp::INSERT};
    int64_t ext_u{0};
    int64_t ext_v{0};
    TypedLabel label;
    StepStatus status{StepStatus::SUCCESS};
    std::string error_message{};
    std::vector<std::vector<int64_t>> entered_tuples{};
    std::vector<std::vector<int64_t>> exited_tuples{};
    EngineCounters step_counters{};
};

class ApxStreamingEngine {
public:
    ApxStreamingEngine(
        ApxAuthoritativeGraph& graph,
        ApxTypedCodec& codec,
        ApxTupleSink& sink,
        const ApxQueryPlan& query_plan,
        EngineType engine_type = EngineType::M2,
        uint64_t max_work = 500000
    );

    StreamStepResult execute_step(const UpdateOperation& update, uint32_t step_index);

    EngineCounters& get_counters() noexcept { return cumulative_counters_; }
    const EngineCounters& get_counters() const noexcept { return cumulative_counters_; }
    const EngineCounters& get_cumulative_counters() const noexcept { return cumulative_counters_; }
    const EngineCounters& get_step_counters() const noexcept { return step_counters_; }
    EngineType get_engine_type() const noexcept { return engine_type_; }

    ApxTransactionManager& get_tx_manager() noexcept { return tx_manager_; }
    void set_m2_fault_hook(M2FaultHook hook) noexcept { m2_engine_.set_fault_hook(hook); }

private:
    ApxAuthoritativeGraph& graph_;
    ApxTypedCodec& codec_;
    ApxTupleSink& sink_;
    const ApxQueryPlan& query_plan_;
    EngineType engine_type_;
    uint64_t max_work_;

    ApxM1EnumEngine m1_engine_;
    ApxM2EventEngine m2_engine_;
    ApxTransactionManager tx_manager_;
    EngineCounters cumulative_counters_;
    EngineCounters step_counters_;
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_STREAMING_ENGINE_H
