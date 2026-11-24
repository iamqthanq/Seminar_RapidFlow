#ifndef RAPIDFLOW_APX_NEIGHBOR_BUFFER_POOL_H
#define RAPIDFLOW_APX_NEIGHBOR_BUFFER_POOL_H

#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>
#include "rapidflow_apx/engine_counters.h"

namespace rapidflow_apx {

class NeighborPoolInvariantViolation : public InvariantViolationException {
public:
    using InvariantViolationException::InvariantViolationException;
};

enum class NeighborMode : uint8_t { LEGACY, BUFFER };

// A search owns this pool across its anchors. The outer vector is sized once
// so recursive frames may safely hold references to distinct inner vectors.
class ApxNeighborBufferPool {
public:
    ApxNeighborBufferPool(size_t query_vertices, size_t missing_budget) {
        if (query_vertices < 2
            || missing_budget > std::numeric_limits<size_t>::max() - (query_vertices - 1)) {
            throw NeighborPoolInvariantViolation("Invalid M2 neighbor pool frame bound");
        }
        const size_t frames = (query_vertices - 1) + missing_budget;
        if (frames > slots_.max_size()) {
            throw NeighborPoolInvariantViolation("M2 neighbor pool frame bound exceeds vector capacity");
        }
        slots_.resize(frames);
    }

    ApxNeighborBufferPool(const ApxNeighborBufferPool&) = delete;
    ApxNeighborBufferPool& operator=(const ApxNeighborBufferPool&) = delete;

    std::vector<uint32_t>& at(size_t depth) {
        if (depth >= slots_.size()) {
            throw NeighborPoolInvariantViolation("M2 recursion depth exceeds neighbor pool frame bound");
        }
        return slots_[depth];
    }

    size_t size() const noexcept { return slots_.size(); }

private:
    std::vector<std::vector<uint32_t>> slots_;
};

} // namespace rapidflow_apx

#endif
