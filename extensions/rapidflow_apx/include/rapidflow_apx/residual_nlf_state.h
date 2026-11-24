#ifndef RAPIDFLOW_APX_RESIDUAL_NLF_STATE_H
#define RAPIDFLOW_APX_RESIDUAL_NLF_STATE_H

#include <array>
#include <cstddef>
#include <vector>
#include "rapidflow_apx/nlf_index.h"

namespace rapidflow_apx {

class ResidualInvariantViolation : public InvariantViolationException {
public:
    using InvariantViolationException::InvariantViolationException;
};

// One instance per RESIDUAL search. Mutable slots follow the query registry's
// per-vertex requirement order. Each canonical edge addresses two distinct slots.
class ApxResidualMissingState {
public:
    ApxResidualMissingState(const ApxQueryPlan& plan, const ApxQuerySignatures& signatures);

    void commit_missing(uint32_t edge);
    void undo_missing(uint32_t edge);

    NlfCount count(uint32_t vertex, size_t requirement) const;

    static NlfCount remaining_requirement(NlfCount required, NlfCount committed);

    bool allows(
        const ApxQuerySignatures& signatures,
        const ApxNlfIndex& index,
        uint32_t query_vertex,
        uint32_t data_vertex,
        uint32_t k,
        uint32_t missing_count,
        EngineCounters& counters
    ) const;

private:
    struct Cell {
        NlfCount capacity;
        NlfCount missing;
    };

    std::vector<size_t> offsets_;
    std::vector<Cell> cells_;
    std::vector<std::array<size_t, 2>> edge_slots_;

    const std::array<size_t, 2>& checked_slots(uint32_t edge) const;
};

} // namespace rapidflow_apx

#endif
