#include "rapidflow_apx/streaming_engine.h"

namespace rapidflow_apx {

namespace {
class SearchTimingGuard {
public:
    SearchTimingGuard(StepProfiling& prof, bool enabled) noexcept
        : prof_(prof),
          enabled_(enabled),
          t0_(enabled ? ProfilingClock::now() : ProfilingTimePoint{}) {}

    SearchTimingGuard(const SearchTimingGuard&) = delete;
    SearchTimingGuard& operator=(const SearchTimingGuard&) = delete;

    ~SearchTimingGuard() noexcept {
        if (enabled_) {
            const auto dt = std::chrono::duration_cast<ProfilingNanoseconds>(
                ProfilingClock::now() - t0_
            ).count();
            prof_.search_ns = dt > 0 ? static_cast<uint64_t>(dt) : 0ULL;
            if (prof_.search_ns >= prof_.neighbor_retrieval_ns) {
                prof_.search_core_ns = prof_.search_ns - prof_.neighbor_retrieval_ns;
            } else {
                prof_.search_core_ns = 0ULL;
            }
        }
    }

private:
    StepProfiling& prof_;
    bool enabled_;
    ProfilingTimePoint t0_;
};
} // namespace

ApxStreamingEngine::ApxStreamingEngine(
    ApxAuthoritativeGraph& graph,
    ApxTypedCodec& codec,
    ApxTupleSink& sink,
    const ApxQueryPlan& query_plan,
    EngineType engine_type,
    uint64_t max_work,
    NlfMode nlf_mode,
    size_t max_nlf_memory_bytes,
    NeighborMode neighbor_mode
) : graph_(graph),
    codec_(codec),
    sink_(sink),
    query_plan_(query_plan),
    engine_type_(engine_type),
    max_work_(max_work),
    requested_nlf_mode_(nlf_mode),
    effective_nlf_mode_(NlfMode::NONE),
    neighbor_mode_(neighbor_mode),
    configuration_status_("SUCCESS"),
    fallback_reason_(""),
    max_nlf_memory_bytes_(max_nlf_memory_bytes),
    tx_manager_(graph, codec, sink)
{
    if (!is_supported_neighbor_mode(engine_type_, requested_nlf_mode_, neighbor_mode_)) {
        throw EngineException("Neighbor BUFFER requires M2 with NLF NONE");
    }

    cumulative_counters_.max_work = max_work_;
    step_counters_.max_work = max_work_;

    if (engine_type_ == EngineType::M1) {
        if (requested_nlf_mode_ != NlfMode::NONE) {
            throw EngineException("NLF optimization is not supported for M1 engine");
        }
        effective_nlf_mode_ = NlfMode::NONE;
    } else {
        if (requested_nlf_mode_ == NlfMode::STATIC || requested_nlf_mode_ == NlfMode::RESIDUAL) {
            if (max_nlf_memory_bytes_ == 0) {
                max_nlf_memory_bytes_ = DEFAULT_NLF_MEMORY_BYTES;
            }
            try {
                query_signatures_.build(query_plan_);
                auto idx = std::make_unique<ApxNlfIndex>();
                idx->initialize(graph_, query_signatures_, max_nlf_memory_bytes_);
                nlf_index_ = std::move(idx);
                tx_manager_.set_nlf_context(nlf_index_.get(), &query_signatures_);
                effective_nlf_mode_ = requested_nlf_mode_;
                configuration_status_ = "SUCCESS";
                cumulative_counters_.nlf_index_initialization_cells = nlf_index_->total_cells();
            } catch (const NlfMemoryLimitException&) {
                nlf_index_.reset();
                tx_manager_.set_nlf_context(nullptr, nullptr);
                effective_nlf_mode_ = NlfMode::NONE;
                configuration_status_ = "REQUESTED_CONFIGURATION_UNAVAILABLE";
                fallback_reason_ = "NLF_INDEX_MEMORY_LIMIT";
            } catch (const std::bad_alloc&) {
                nlf_index_.reset();
                tx_manager_.set_nlf_context(nullptr, nullptr);
                effective_nlf_mode_ = NlfMode::NONE;
                configuration_status_ = "REQUESTED_CONFIGURATION_UNAVAILABLE";
                fallback_reason_ = "NLF_INDEX_ALLOCATION_FAILED";
            }
        } else {
            effective_nlf_mode_ = NlfMode::NONE;
            configuration_status_ = "SUCCESS";
        }
    }
}

StreamStepResult ApxStreamingEngine::execute_step(const UpdateOperation& update, uint32_t step_index) {
    step_counters_.reset_step();
    step_counters_.max_work = max_work_;

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
        res.step_counters = step_counters_;
        return res;
    }

    uint32_t dense_u = codec_.get_data_vertex(update.ext_u);
    uint32_t dense_v = codec_.get_data_vertex(update.ext_v);

    if (dense_u == dense_v) {
        res.status = StepStatus::PREVALIDATION_FAILED;
        res.error_message = "Self-loop edge update rejected";
        res.step_counters = step_counters_;
        return res;
    }

    // Pre-validation: topological checks
    bool existing_edge = graph_.has_edge(dense_u, dense_v);
    if (update.op == UpdateOp::INSERT) {
        if (existing_edge) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge already exists in graph for INSERT";
            res.step_counters = step_counters_;
            return res;
        }
    } else { // DELETE
        if (!existing_edge) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge does not exist in graph for DELETE";
            res.step_counters = step_counters_;
            return res;
        }
        if (!codec_.has_edge_label(update.label)) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge label mismatch for DELETE (label unknown in codec)";
            res.step_counters = step_counters_;
            return res;
        }
        uint32_t dense_lbl = codec_.get_edge_label_id(update.label);
        auto actual_lbl = graph_.get_edge_label(dense_u, dense_v);
        if (!actual_lbl.exists || actual_lbl.label != dense_lbl) {
            res.status = StepStatus::PREVALIDATION_FAILED;
            res.error_message = "Edge label mismatch for DELETE";
            res.step_counters = step_counters_;
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

    StepProfiling step_prof;
    ProfilingTimePoint step_t0;
    if (profiling_enabled_) {
        step_t0 = ProfilingClock::now();
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
            StepProfiling* prof_ptr = profiling_enabled_ ? &step_prof : nullptr;
            SearchTimingGuard search_timing(step_prof, profiling_enabled_);
            if (engine_type_ == EngineType::M1) {
                m1_engine_.search_event(query_plan_, snapshot, du, dv, dlbl, sink_, step_counters_, prof_ptr);
            } else {
                m2_engine_.search_event(
                    query_plan_, snapshot, du, dv, dlbl, sink_, step_counters_, prof_ptr,
                    effective_nlf_mode_, nlf_index_.get(), &query_signatures_, neighbor_mode_
                );
            }
        } catch (const EngineException& e) {
            // Re-throw to trigger transaction abort (SearchTimingGuard closes timer on unwind)
            throw;
        }
        return true;
    };

    uint64_t raw_before = sink_.raw_emissions();
    uint64_t uniq_before = sink_.unique_emissions();
    uint64_t com_before = sink_.committed_emissions();
    uint64_t disc_before = sink_.discarded_emissions();

    tx_manager_.set_counters(&step_counters_);
    StepResult step_res = tx_manager_.execute_update(update, callback);

    if (profiling_enabled_) {
        auto step_t1 = ProfilingClock::now();
        step_prof.step_total_ns = std::chrono::duration_cast<ProfilingNanoseconds>(step_t1 - step_t0).count();
        step_prof.update_maintenance_ns = step_res.update_maintenance_ns;
        step_prof.tuple_sink_ns = step_res.tuple_sink_ns;
        step_prof.tuple_commit_ns = step_res.tuple_sink_ns;
        res.step_profiling = step_prof;
        cumulative_profiling_.accumulate(step_prof);
    }

    step_counters_.raw_emissions = sink_.raw_emissions() - raw_before;
    step_counters_.unique_emissions = sink_.unique_emissions() - uniq_before;
    step_counters_.committed_emissions = sink_.committed_emissions() - com_before;
    step_counters_.discarded_emissions = sink_.discarded_emissions() - disc_before;

    res.status = step_res.status;
    res.committed = step_res.committed;
    res.error_message = std::move(step_res.error_message);
    res.step_counters = step_counters_;
    cumulative_counters_.accumulate(step_counters_);

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
