#ifndef RAPIDFLOW_APX_HARNESS_RUNNER_H
#define RAPIDFLOW_APX_HARNESS_RUNNER_H

#include <cstdint>
#include <string>

#include "rapidflow_apx/m2_event_engine.h"
#include "rapidflow_apx/nlf_index.h"
#include "rapidflow_apx/streaming_engine.h"

namespace rapidflow_apx {

enum class ExitCode : int {
    SUCCESS = 0,
    CLI_ARG_ERROR = 1,
    INPUT_PARSE_ERROR = 2,
    DOMAIN_REJECTION = 3,
    WORK_BUDGET_EXCEEDED = 4,
    RESOURCE_LIMIT_ERROR = 5,
    STEP_FAILURE = 6,
    IO_ERROR = 7,
    UNCAUGHT_ERROR = 8,
    RESULT_DELIVERY_ERROR = 9
};

enum class ExecutionPurpose {
    CORRECTNESS,
    PERFORMANCE,
    DIAGNOSTIC
};

enum class MeasurementStage {
    PARSE_START,
    PARSE_END,
    INIT_START,
    GRAPH_INIT_START,
    GRAPH_INIT_END,
    QUERY_PREP_START,
    QUERY_PREP_END,
    ENGINE_INIT_START,
    ENGINE_INIT_END,
    INIT_END,
    PRIMARY_TIMER_START,
    STEP_EXECUTE_START,
    STEP_EXECUTE_END,
    CONSTRUCT_GRAPH_DIAGNOSTICS,
    PRIMARY_TIMER_STOP,
    REPORTING_START,
    REPORTING_END
};

using BoundaryObserverFn = void (*)(MeasurementStage stage, uint64_t step_index, void* ctx);

struct HarnessOptions {
    std::string engine_str{"m2"};
    NlfMode nlf_mode{NlfMode::NONE};
    NeighborMode neighbor_mode{NeighborMode::LEGACY};
    uint64_t max_nlf_memory_mib{DEFAULT_NLF_MEMORY_MIB};
    bool cli_max_nlf_memory_mib_set{false};
    std::string workload_path{};
    std::string output_path{};
    bool cli_max_work_set{false};
    uint64_t cli_max_work{500000};
    bool continue_on_error{false};
    bool profiling_enabled{false};
    bool profile_flag_seen{false};
    std::string profile_output_path{};
    ExecutionPurpose execution_purpose{ExecutionPurpose::CORRECTNESS};
    bool explicit_execution_purpose{false};
    uint64_t wall_timeout_seconds{30};
    bool apply_process_limits{true};
};

const char* execution_purpose_name(ExecutionPurpose purpose) noexcept;
bool parse_execution_purpose(const std::string& text, ExecutionPurpose& purpose) noexcept;
int run_workload_with_options(
    const HarnessOptions& opts,
    BoundaryObserverFn observer = nullptr,
    void* observer_ctx = nullptr
);

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_HARNESS_RUNNER_H
