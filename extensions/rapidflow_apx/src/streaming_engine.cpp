#include "rapidflow_apx/streaming_engine.h"

namespace rapidflow_apx {

ApxStreamingEngine::ApxStreamingEngine(
    ApxAuthoritativeGraph& graph,
    ApxTypedCodec& codec,
    ApxTupleSink& sink,
    const ApxQueryPlan& query_plan,
    EngineType engine_type,
    uint64_t max_work
) : graph_(graph),
    codec_(codec),
    sink_(sink),
    query_plan_(query_plan),
    engine_type_(engine_type),
    max_work_(max_work),
    tx_manager_(graph, codec, sink)
{
    counters_.max_work = max_work_;
}

StreamStepResult ApxStreamingEngine::execute_step(const UpdateOperation& update, uint32_t step_index) {
    StreamStepResult res;
    res.step_index = step_index;
    res.op = update.op;
    res.ext_u = update.ext_u;
    res.ext_v = update.ext_v;
    res.label = update.label;

    // Pre-validation: check endpoints exist in codec
    if (!codec_.has_data_vertex(update.ext_u) || !codec_.has_data_vertex(update.ext_v)) {
        res.status = StepStatus::PREVALIDATION_FAILED;
        res.error_message = "Unknown data vertex ID: endpoints must exist in data graph";
        return res;
    }

    uint32_t dense_u = codec_.get_data_vertex(update.ext_u);
    uint32_t dense_v = codec_.get_data_vertex(update.ext_v);

    if (dense_u == dense_v) {
        res.status = StepStatus::PREVALIDATION_FAILED;
        res.error_message = "Self-loop edge update rejected";
        return res;
    }

    // Pre-validation: topological checks
    bool existing_edge = graph_.has_edge(dense_u, dense_v);
    if (update.op == UpdateOp::INSERT) {
        if (existing_edge) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge already exists in graph for INSERT";
            return res;
        }
    } else { // DELETE
        if (!existing_edge) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge does not exist in graph for DELETE";
            return res;
        }
        if (!codec_.has_edge_label(update.label)) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge label mismatch for DELETE (label unknown in codec)";
            return res;
        }
        uint32_t dense_lbl = codec_.get_edge_label_id(update.label);
        auto actual_lbl = graph_.get_edge_label(dense_u, dense_v);
        if (!actual_lbl.exists || actual_lbl.label != dense_lbl) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge label mismatch for DELETE";
            return res;
        }
    }

    // Check query relevancy:
    // If the label is not present in query_plan, the update is topologically applied
    // to the authoritative graph and codec, but search is bypassed (0 tuples emitted).
    bool is_relevant = false;
    if (codec_.has_edge_label(update.label)) {
        uint32_t dense_lbl = codec_.get_edge_label_id(update.label);
        is_relevant = query_plan_.is_relevant_edge_label(dense_lbl);
    }

    SearchCallback callback = [&](
        const ApxAuthoritativeGraph& snapshot,
        const UpdateOperation&,
        uint32_t du,
        uint32_t dv,
        uint32_t dlbl
    ) -> bool {
        if (!is_relevant) {
            return true;
        }

        try {
            if (engine_type_ == EngineType::M1) {
                m1_engine_.search_event(query_plan_, snapshot, du, dv, dlbl, sink_, counters_);
            } else {
                m2_engine_.search_event(query_plan_, snapshot, du, dv, dlbl, sink_, counters_);
            }
        } catch (const EngineException& e) {
            // Re-throw to trigger transaction abort
            throw;
        }
        return true;
    };

    StepResult step_res = tx_manager_.execute_update(update, callback);

    res.status = step_res.status;
    res.error_message = step_res.error_message;

    if (res.status == StepStatus::SUCCESS) {
        if (update.op == UpdateOp::INSERT) {
            res.entered_tuples = std::move(step_res.published_tuples);
        } else {
            res.exited_tuples = std::move(step_res.published_tuples);
        }
    }

    return res;
}

} // namespace rapidflow_apx
