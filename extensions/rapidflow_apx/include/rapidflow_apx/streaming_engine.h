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
#include "rapidflow_apx/profiler.h"
#include "rapidflow_apx/nlf_signatures.h"
#include "rapidflow_apx/nlf_index.h"
#include <memory>

namespace rapidflow_apx {

enum class EngineType {
    M1,
    M2
};

inline bool is_supported_neighbor_mode(EngineType engine, NlfMode nlf, NeighborMode mode) noexcept {
    return mode == NeighborMode::LEGACY
        || ((mode == NeighborMode::BUFFER || mode == NeighborMode::MERGE) && engine == EngineType::M2 && nlf == NlfMode::NONE);
}

struct StreamStepResult {
    bool committed{false};
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
    StepProfiling step_profiling{};
};

class ApxStreamingEngine {
public:
    ApxStreamingEngine(
        ApxAuthoritativeGraph& graph,
        ApxTypedCodec& codec,
        ApxTupleSink& sink,
        const ApxQueryPlan& query_plan,
        EngineType engine_type = EngineType::M2,
        uint64_t max_work = 500000,
        NlfMode nlf_mode = NlfMode::NONE,
        size_t max_nlf_memory_bytes = 0,
        NeighborMode neighbor_mode = NeighborMode::LEGACY
    );

    StreamStepResult execute_step(const UpdateOperation& update, uint32_t step_index);

    EngineCounters& get_counters() noexcept { return cumulative_counters_; }
    const EngineCounters& get_counters() const noexcept { return cumulative_counters_; }
    const EngineCounters& get_cumulative_counters() const noexcept { return cumulative_counters_; }
    const EngineCounters& get_step_counters() const noexcept { return step_counters_; }
    EngineType get_engine_type() const noexcept { return engine_type_; }

    NlfMode get_requested_nlf_mode() const noexcept { return requested_nlf_mode_; }
    NlfMode get_effective_nlf_mode() const noexcept { return effective_nlf_mode_; }
    NeighborMode get_neighbor_mode() const noexcept { return neighbor_mode_; }
    const std::string& get_configuration_status() const noexcept { return configuration_status_; }
    const std::string& get_fallback_reason() const noexcept { return fallback_reason_; }
    const ApxNlfIndex* get_nlf_index() const noexcept { return nlf_index_.get(); }
    const ApxQuerySignatures& get_query_signatures() const noexcept { return query_signatures_; }

    ApxTransactionManager& get_tx_manager() noexcept { return tx_manager_; }
    void set_m2_fault_hook(M2FaultHook hook) noexcept { m2_engine_.set_fault_hook(hook); }

#ifdef APX_RESIDUAL_TEST_TRACE
    void set_residual_observer(ApxM2EventEngine::ResidualObserver observer) {
        m2_engine_.set_residual_observer(std::move(observer));
    }
#endif

#ifdef APX_NEIGHBOR_TEST_TRACE
    void set_neighbor_observer(ApxM2EventEngine::NeighborObserver observer) {
        m2_engine_.set_neighbor_observer(std::move(observer));
    }
#endif

    void set_profiling_enabled(bool enabled) noexcept {
        profiling_enabled_ = enabled;
        tx_manager_.set_profiling_enabled(enabled);
    }
    bool is_profiling_enabled() const noexcept { return profiling_enabled_; }
    const CumulativeProfiling& get_cumulative_profiling() const noexcept { return cumulative_profiling_; }

private:
    ApxAuthoritativeGraph& graph_;
    ApxTypedCodec& codec_;
    ApxTupleSink& sink_;
    const ApxQueryPlan& query_plan_;
    EngineType engine_type_;
    uint64_t max_work_;
    NlfMode requested_nlf_mode_{NlfMode::NONE};
    NlfMode effective_nlf_mode_{NlfMode::NONE};
    NeighborMode neighbor_mode_{NeighborMode::LEGACY};
    std::string configuration_status_{"SUCCESS"};
    std::string fallback_reason_{""};
    size_t max_nlf_memory_bytes_{0};

    ApxQuerySignatures query_signatures_{};
    std::unique_ptr<ApxNlfIndex> nlf_index_{nullptr};

    ApxM1EnumEngine m1_engine_;
    ApxM2EventEngine m2_engine_;
    ApxTransactionManager tx_manager_;
    EngineCounters cumulative_counters_;
    EngineCounters step_counters_;
    bool profiling_enabled_{false};
    CumulativeProfiling cumulative_profiling_{};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_STREAMING_ENGINE_H
