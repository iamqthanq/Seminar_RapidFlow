#ifndef RAPIDFLOW_APX_TRANSACTION_MANAGER_H
#define RAPIDFLOW_APX_TRANSACTION_MANAGER_H

#include <cstdint>
#include <string>
#include <vector>
#include <functional>
#include "rapidflow_apx/typed_codec.h"
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/tuple_sink.h"

namespace rapidflow_apx {

class ApxNlfIndex;
class ApxQuerySignatures;
struct EngineCounters;

enum class UpdateOp : char { INSERT = '+', DELETE = '-' };

struct UpdateOperation {
    UpdateOp op;
    int64_t ext_u;
    int64_t ext_v;
    TypedLabel label;
};

enum class StepStatus {
    SUCCESS,
    PREVALIDATION_FAILED,
    CALLBACK_ABORTED,
    FAULT_INJECTED,
    REENTRANCY_BLOCKED,
    INVARIANT_VIOLATION
};

inline const char* step_status_to_string(StepStatus s) {
    switch (s) {
        case StepStatus::SUCCESS: return "SUCCESS";
        case StepStatus::PREVALIDATION_FAILED: return "PREVALIDATION_FAILED";
        case StepStatus::CALLBACK_ABORTED: return "CALLBACK_ABORTED";
        case StepStatus::FAULT_INJECTED: return "FAULT_INJECTED";
        case StepStatus::REENTRANCY_BLOCKED: return "REENTRANCY_BLOCKED";
        case StepStatus::INVARIANT_VIOLATION: return "INVARIANT_VIOLATION";
    }
    return "UNKNOWN";
}

struct StepResult {
    StepStatus status;
    std::string error_message;
    std::vector<std::vector<int64_t>> published_tuples;
    uint64_t update_maintenance_ns{0};
    uint64_t tuple_sink_ns{0};
};

using SearchCallback = std::function<bool(
    const ApxAuthoritativeGraph& graph_view,
    const UpdateOperation& update,
    uint32_t dense_u,
    uint32_t dense_v,
    uint32_t dense_label
)>;

class ApxTransactionManager {
public:
    enum class FaultPoint {
        NONE,
        BEFORE_MUTATION,
        BETWEEN_FORWARD_REVERSE_MUTATION,
        AFTER_MUTATION,
        DURING_CALLBACK,
        BEFORE_PUBLISH,
        ALLOCATION_FAILURE_MUTATION,
        AFTER_NLF_PREPARE,
        AFTER_GRAPH_BEFORE_NLF,
        AFTER_FIRST_NLF_CELL
    };

    ApxTransactionManager(ApxAuthoritativeGraph& graph, ApxTypedCodec& codec, ApxTupleSink& sink);

    StepResult execute_update(const UpdateOperation& update, SearchCallback callback);

    void set_fault_injection(FaultPoint point) noexcept { fault_point_ = point; }
    FaultPoint get_fault_injection() const noexcept { return fault_point_; }

    void set_profiling_enabled(bool enabled) noexcept { profiling_enabled_ = enabled; }
    bool is_profiling_enabled() const noexcept { return profiling_enabled_; }

    void set_nlf_context(ApxNlfIndex* nlf, const ApxQuerySignatures* sigs) noexcept {
        nlf_index_ = nlf;
        signatures_ = sigs;
    }
    ApxNlfIndex* get_nlf_index() const noexcept { return nlf_index_; }
    const ApxQuerySignatures* get_query_signatures() const noexcept { return signatures_; }

    void set_counters(EngineCounters* counters) noexcept { counters_ = counters; }
    EngineCounters* get_counters() const noexcept { return counters_; }

    bool is_in_transaction() const noexcept { return is_in_transaction_; }
    bool is_session_poisoned() const noexcept { return session_poisoned_; }
    void reset_poison_for_testing() noexcept { session_poisoned_ = false; }

private:
    ApxAuthoritativeGraph& graph_;
    ApxTypedCodec& codec_;
    ApxTupleSink& sink_;
    FaultPoint fault_point_{FaultPoint::NONE};
    bool is_in_transaction_{false};
    bool session_poisoned_{false};
    bool profiling_enabled_{false};
    ApxNlfIndex* nlf_index_{nullptr};
    const ApxQuerySignatures* signatures_{nullptr};
    EngineCounters* counters_{nullptr};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_TRANSACTION_MANAGER_H
