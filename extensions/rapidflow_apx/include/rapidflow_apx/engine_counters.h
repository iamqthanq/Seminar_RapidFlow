#ifndef RAPIDFLOW_APX_ENGINE_COUNTERS_H
#define RAPIDFLOW_APX_ENGINE_COUNTERS_H

#include <cstdint>
#include <string>
#include <stdexcept>
#include <limits>

namespace rapidflow_apx {

class EngineException : public std::runtime_error {
public:
    explicit EngineException(const std::string& msg) : std::runtime_error(msg) {}
};

class WorkBudgetExceededException : public EngineException {
public:
    explicit WorkBudgetExceededException(const std::string& msg) : EngineException(msg) {}
};

class InvariantViolationException : public EngineException {
public:
    explicit InvariantViolationException(const std::string& msg) : EngineException(msg) {}
};

class InvalidThresholdException : public EngineException {
public:
    explicit InvalidThresholdException(const std::string& msg) : EngineException(msg) {}
};

class InvalidQueryException : public EngineException {
public:
    explicit InvalidQueryException(const std::string& msg) : EngineException(msg) {}
};

class FaultInjectedException : public EngineException {
public:
    explicit FaultInjectedException(const std::string& msg) : EngineException(msg) {}
};

struct EngineCounters {
    // Common event search counters
    uint64_t anchors_started{0};
    uint64_t dfs_states{0};
    uint64_t candidate_evaluations{0};
    uint64_t candidate_extensions{0};
    uint64_t edge_lookups{0};
    uint64_t vertex_label_checks{0};

    // Emission counters
    uint64_t raw_emissions{0};
    uint64_t unique_emissions{0};
    uint64_t committed_emissions{0};
    uint64_t discarded_emissions{0};

    // M1-specific counters
    uint64_t variants_started{0};
    uint64_t raw_exact_results{0};
    uint64_t missing_set_rejections{0};

    // M2-specific counters
    uint64_t edge_decisions{0};
    uint64_t missing_branches{0};
    uint64_t commitment_checks{0};
    uint64_t commitment_rejections{0};

    // NLF-specific counters (P4b-1A)
    uint64_t nlf_static_checks{0};
    uint64_t nlf_static_pruned{0};
    uint64_t nlf_signature_lookups{0};
    uint64_t nlf_anchor_checks{0};
    uint64_t nlf_anchor_pruned{0};
    uint64_t nlf_index_cell_updates{0};
    uint64_t nlf_index_rollback_cells{0};
    uint64_t nlf_index_initialization_cells{0};

    // DFS-local residual NLF operations and actual frequency reads.
    uint64_t nlf_residual_checks{0};
    uint64_t nlf_residual_pruned{0};
    uint64_t nlf_residual_anchor_checks{0};
    uint64_t nlf_residual_anchor_pruned{0};
    uint64_t nlf_residual_signature_lookups{0};
    uint64_t nlf_residual_missing_commits{0};
    uint64_t nlf_residual_missing_undos{0};

    // Work limit
    uint64_t work_units{0};
    uint64_t max_work{500000};

    inline void check_work_budget() const {
        if (max_work == 0 || work_units > max_work) {
            throw WorkBudgetExceededException(
                "Work budget exceeded: " + std::to_string(work_units) + " > " + std::to_string(max_work)
            );
        }
    }

    inline void charge_work(uint64_t units = 1) {
        if (units > std::numeric_limits<uint64_t>::max() - work_units) {
            throw WorkBudgetExceededException("Work counter overflow");
        }
        if (work_units + units > max_work) {
            work_units += units;
            throw WorkBudgetExceededException(
                "Work budget exceeded: " + std::to_string(work_units) + " > " + std::to_string(max_work)
            );
        }
        work_units += units;
    }

    void reset_step() noexcept {
        anchors_started = 0;
        dfs_states = 0;
        candidate_evaluations = 0;
        candidate_extensions = 0;
        edge_lookups = 0;
        vertex_label_checks = 0;
        raw_emissions = 0;
        unique_emissions = 0;
        committed_emissions = 0;
        discarded_emissions = 0;
        variants_started = 0;
        raw_exact_results = 0;
        missing_set_rejections = 0;
        edge_decisions = 0;
        missing_branches = 0;
        commitment_checks = 0;
        commitment_rejections = 0;
        nlf_static_checks = 0;
        nlf_static_pruned = 0;
        nlf_signature_lookups = 0;
        nlf_anchor_checks = 0;
        nlf_anchor_pruned = 0;
        nlf_index_cell_updates = 0;
        nlf_index_rollback_cells = 0;
        nlf_index_initialization_cells = 0;
        nlf_residual_checks = 0;
        nlf_residual_pruned = 0;
        nlf_residual_anchor_checks = 0;
        nlf_residual_anchor_pruned = 0;
        nlf_residual_signature_lookups = 0;
        nlf_residual_missing_commits = 0;
        nlf_residual_missing_undos = 0;
        work_units = 0;
    }

    void accumulate(const EngineCounters& other) noexcept {
        anchors_started += other.anchors_started;
        dfs_states += other.dfs_states;
        candidate_evaluations += other.candidate_evaluations;
        candidate_extensions += other.candidate_extensions;
        edge_lookups += other.edge_lookups;
        vertex_label_checks += other.vertex_label_checks;
        raw_emissions += other.raw_emissions;
        unique_emissions += other.unique_emissions;
        committed_emissions += other.committed_emissions;
        discarded_emissions += other.discarded_emissions;
        variants_started += other.variants_started;
        raw_exact_results += other.raw_exact_results;
        missing_set_rejections += other.missing_set_rejections;
        edge_decisions += other.edge_decisions;
        missing_branches += other.missing_branches;
        commitment_checks += other.commitment_checks;
        commitment_rejections += other.commitment_rejections;
        nlf_static_checks += other.nlf_static_checks;
        nlf_static_pruned += other.nlf_static_pruned;
        nlf_signature_lookups += other.nlf_signature_lookups;
        nlf_anchor_checks += other.nlf_anchor_checks;
        nlf_anchor_pruned += other.nlf_anchor_pruned;
        nlf_index_cell_updates += other.nlf_index_cell_updates;
        nlf_index_rollback_cells += other.nlf_index_rollback_cells;
        nlf_index_initialization_cells += other.nlf_index_initialization_cells;
        nlf_residual_checks += other.nlf_residual_checks;
        nlf_residual_pruned += other.nlf_residual_pruned;
        nlf_residual_anchor_checks += other.nlf_residual_anchor_checks;
        nlf_residual_anchor_pruned += other.nlf_residual_anchor_pruned;
        nlf_residual_signature_lookups += other.nlf_residual_signature_lookups;
        nlf_residual_missing_commits += other.nlf_residual_missing_commits;
        nlf_residual_missing_undos += other.nlf_residual_missing_undos;
        work_units += other.work_units;
    }
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_ENGINE_COUNTERS_H
