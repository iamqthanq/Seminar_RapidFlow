#ifndef RAPIDFLOW_APX_PROFILER_H
#define RAPIDFLOW_APX_PROFILER_H

#include <chrono>
#include <cstdint>
#include <string>

namespace rapidflow_apx {

using ProfilingClock = std::chrono::steady_clock;
using ProfilingTimePoint = ProfilingClock::time_point;
using ProfilingNanoseconds = std::chrono::nanoseconds;

struct StepProfiling {
    uint64_t step_total_ns{0};          // Inclusive duration of execute_step()
    uint64_t update_maintenance_ns{0};  // Topological update (apply_insert / apply_delete in transaction)
    uint64_t search_ns{0};              // Total duration of search_event callback
    uint64_t neighbor_retrieval_ns{0};  // Nested in search: time spent in get_neighbors()
    uint64_t search_core_ns{0};         // search_ns - neighbor_retrieval_ns
    uint64_t tuple_commit_ns{0};        // Time spent committing step in tuple sink (commit_step)
    uint64_t tuple_sink_ns{0};          // Alias for tuple_commit_ns (backward compatibility)

    void reset() noexcept {
        step_total_ns = 0;
        update_maintenance_ns = 0;
        search_ns = 0;
        neighbor_retrieval_ns = 0;
        search_core_ns = 0;
        tuple_commit_ns = 0;
        tuple_sink_ns = 0;
    }
};

struct CumulativeProfiling {
    uint64_t cumulative_step_total_ns{0};
    uint64_t cumulative_update_maintenance_ns{0};
    uint64_t cumulative_search_ns{0};
    uint64_t cumulative_neighbor_retrieval_ns{0};
    uint64_t cumulative_search_core_ns{0};
    uint64_t cumulative_tuple_commit_ns{0};
    uint64_t cumulative_tuple_sink_ns{0};

    void reset() noexcept {
        cumulative_step_total_ns = 0;
        cumulative_update_maintenance_ns = 0;
        cumulative_search_ns = 0;
        cumulative_neighbor_retrieval_ns = 0;
        cumulative_search_core_ns = 0;
        cumulative_tuple_commit_ns = 0;
        cumulative_tuple_sink_ns = 0;
    }

    void accumulate(const StepProfiling& step) noexcept {
        cumulative_step_total_ns += step.step_total_ns;
        cumulative_update_maintenance_ns += step.update_maintenance_ns;
        cumulative_search_ns += step.search_ns;
        cumulative_neighbor_retrieval_ns += step.neighbor_retrieval_ns;
        cumulative_search_core_ns += step.search_core_ns;
        cumulative_tuple_commit_ns += step.tuple_commit_ns;
        cumulative_tuple_sink_ns += step.tuple_sink_ns;
    }
};

struct RunProfiling {
    bool enabled{false};
    uint64_t total_wall_ns{0};
    uint64_t input_parsing_ns{0};
    uint64_t graph_init_ns{0};
    uint64_t query_prep_ns{0};
    uint64_t engine_and_nlf_init_ns{0};
    uint64_t streaming_exec_ns{0};
    uint64_t output_serialization_ns{0};
};

struct MacroTiming {
    uint64_t input_parsing_ns{0};
    uint64_t initialization_wall_ns{0};
    uint64_t graph_init_ns{0};
    uint64_t query_prep_ns{0};
    uint64_t engine_and_nlf_init_ns{0};
    uint64_t streaming_loop_wall_ns{0};
    uint64_t reporting_wall_ns{0};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_PROFILER_H
