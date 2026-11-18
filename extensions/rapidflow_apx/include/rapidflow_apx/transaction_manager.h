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

enum class UpdateOp : char { INSERT = '+', DELETE = '-' };

struct UpdateOperation {
    UpdateOp op;
    int64_t ext_u;
    int64_t ext_v;
    TypedLabel label;
};

enum class StepStatus { SUCCESS, PREVALIDATION_FAILED, CALLBACK_ABORTED, FAULT_INJECTED };

struct StepResult {
    StepStatus status;
    std::string error_message;
    std::vector<std::vector<int64_t>> published_tuples;
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
    enum class FaultPoint { NONE, AFTER_MUTATION, DURING_CALLBACK, BEFORE_PUBLISH };

    ApxTransactionManager(ApxAuthoritativeGraph& graph, ApxTypedCodec& codec, ApxTupleSink& sink);

    StepResult execute_update(const UpdateOperation& update, SearchCallback callback);

    void set_fault_injection(FaultPoint point) noexcept { fault_point_ = point; }
    FaultPoint get_fault_injection() const noexcept { return fault_point_; }

private:
    ApxAuthoritativeGraph& graph_;
    ApxTypedCodec& codec_;
    ApxTupleSink& sink_;
    FaultPoint fault_point_{FaultPoint::NONE};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_TRANSACTION_MANAGER_H
