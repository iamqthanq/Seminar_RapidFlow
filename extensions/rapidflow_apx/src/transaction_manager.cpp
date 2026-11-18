#include "rapidflow_apx/transaction_manager.h"

namespace rapidflow_apx {

ApxTransactionManager::ApxTransactionManager(
    ApxAuthoritativeGraph& graph,
    ApxTypedCodec& codec,
    ApxTupleSink& sink
) : graph_(graph), codec_(codec), sink_(sink) {}

StepResult ApxTransactionManager::execute_update(
    const UpdateOperation& update,
    SearchCallback callback
) {
    // 1. Prevalidation: Vertex existence
    if (!codec_.has_data_vertex(update.ext_u)) {
        return {StepStatus::PREVALIDATION_FAILED, "Data vertex u not registered in codec", {}};
    }
    if (!codec_.has_data_vertex(update.ext_v)) {
        return {StepStatus::PREVALIDATION_FAILED, "Data vertex v not registered in codec", {}};
    }
    if (update.ext_u == update.ext_v) {
        return {StepStatus::PREVALIDATION_FAILED, "Self-loops are not permitted", {}};
    }

    uint32_t dense_u = codec_.intern_data_vertex(update.ext_u);
    uint32_t dense_v = codec_.intern_data_vertex(update.ext_v);
    uint32_t dense_label = codec_.intern_edge_label(update.label);

    // Prevalidation: Graph edge status
    if (update.op == UpdateOp::INSERT) {
        if (graph_.has_edge(dense_u, dense_v)) {
            return {StepStatus::PREVALIDATION_FAILED, "Edge already exists in graph", {}};
        }
    } else if (update.op == UpdateOp::DELETE) {
        if (!graph_.has_edge(dense_u, dense_v)) {
            return {StepStatus::PREVALIDATION_FAILED, "Edge does not exist in graph", {}};
        }
        auto lookup = graph_.get_edge_label(dense_u, dense_v);
        if (!lookup.exists || lookup.label != dense_label) {
            return {StepStatus::PREVALIDATION_FAILED, "Edge label mismatch for deletion", {}};
        }
    } else {
        return {StepStatus::PREVALIDATION_FAILED, "Unknown update operation", {}};
    }

    // 2. Snapshot checkpointing and stage setup
    auto checkpoint = graph_.create_checkpoint();
    sink_.start_step();

    // Snapshot Sequencing:
    // INSERT sees G_after -> apply mutation before search callback
    if (update.op == UpdateOp::INSERT) {
        bool applied = graph_.apply_insert(dense_u, dense_v, dense_label);
        if (!applied) {
            graph_.restore_checkpoint(checkpoint);
            sink_.abort_step();
            return {StepStatus::PREVALIDATION_FAILED, "Failed to apply insert mutation", {}};
        }
    }

    // Fault Injection check: AFTER_MUTATION
    if (fault_point_ == FaultPoint::AFTER_MUTATION) {
        graph_.restore_checkpoint(checkpoint);
        sink_.abort_step();
        return {StepStatus::FAULT_INJECTED, "Fault injected after mutation", {}};
    }

    // 3. Callback execution
    bool callback_ok = false;
    try {
        callback_ok = callback(graph_, update, dense_u, dense_v, dense_label);
    } catch (const std::exception& ex) {
        graph_.restore_checkpoint(checkpoint);
        sink_.abort_step();
        return {StepStatus::CALLBACK_ABORTED, std::string("Callback exception: ") + ex.what(), {}};
    } catch (...) {
        graph_.restore_checkpoint(checkpoint);
        sink_.abort_step();
        return {StepStatus::CALLBACK_ABORTED, "Unknown callback exception", {}};
    }

    if (!callback_ok) {
        graph_.restore_checkpoint(checkpoint);
        sink_.abort_step();
        return {StepStatus::CALLBACK_ABORTED, "Callback returned false", {}};
    }

    // Fault Injection check: DURING_CALLBACK
    if (fault_point_ == FaultPoint::DURING_CALLBACK) {
        graph_.restore_checkpoint(checkpoint);
        sink_.abort_step();
        return {StepStatus::FAULT_INJECTED, "Fault injected during callback", {}};
    }

    // 4. Post-callback finalization
    // DELETE sees G_before -> apply mutation after search callback
    if (update.op == UpdateOp::DELETE) {
        bool applied = graph_.apply_delete(dense_u, dense_v, dense_label);
        if (!applied) {
            graph_.restore_checkpoint(checkpoint);
            sink_.abort_step();
            return {StepStatus::PREVALIDATION_FAILED, "Failed to apply delete mutation after callback", {}};
        }
    }

    // Fault Injection check: BEFORE_PUBLISH
    if (fault_point_ == FaultPoint::BEFORE_PUBLISH) {
        graph_.restore_checkpoint(checkpoint);
        sink_.abort_step();
        return {StepStatus::FAULT_INJECTED, "Fault injected before publish", {}};
    }

    // 5. Commit and publish
    auto published_tuples = sink_.commit_step();
    return {StepStatus::SUCCESS, "", std::move(published_tuples)};
}

} // namespace rapidflow_apx
