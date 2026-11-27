#ifndef RAPIDFLOW_APX_NLF_INDEX_H
#define RAPIDFLOW_APX_NLF_INDEX_H

#include <cstdint>
#include <vector>
#include <array>
#include <limits>
#include <string>
#include <stdexcept>
#include "rapidflow_apx/authoritative_graph.h"
#include "rapidflow_apx/nlf_signatures.h"
#include "rapidflow_apx/engine_counters.h"

namespace rapidflow_apx {

inline constexpr size_t DEFAULT_NLF_MEMORY_BYTES = size_t{256} * size_t{1024} * size_t{1024}; // 256 MiB
inline constexpr uint64_t DEFAULT_NLF_MEMORY_MIB = 256;
inline constexpr uint64_t BYTES_PER_MIB = 1024ULL * 1024ULL;

inline constexpr uint64_t MAX_SIZE_T_BYTES = static_cast<uint64_t>(std::numeric_limits<size_t>::max());
inline constexpr uint64_t MAX_JSON_INT_BYTES = static_cast<uint64_t>(std::numeric_limits<int64_t>::max());
inline constexpr uint64_t MAX_SERIALIZABLE_NLF_BYTES = MAX_SIZE_T_BYTES < MAX_JSON_INT_BYTES ? MAX_SIZE_T_BYTES : MAX_JSON_INT_BYTES;
inline constexpr uint64_t MAX_NLF_MEMORY_MIB = MAX_SERIALIZABLE_NLF_BYTES / BYTES_PER_MIB;
inline constexpr uint64_t MAX_SERIALIZABLE_NLF_MIB = MAX_NLF_MEMORY_MIB;
inline constexpr uint64_t MAX_REPRESENTABLE_NLF_MIB = MAX_NLF_MEMORY_MIB;

class NlfMemoryLimitException : public EngineException {
public:
    explicit NlfMemoryLimitException(const std::string& msg) : EngineException(msg) {}
};

struct NlfUndoEntry {
    size_t cell_index{0};
    NlfCount old_value{0};
    bool armed{false};
};

struct NlfUndoRecord {
    std::array<NlfUndoEntry, 2> entries{};
    uint8_t count{0};
    bool active{false};

    void reset() noexcept {
        entries[0].armed = false;
        entries[1].armed = false;
        count = 0;
        active = false;
    }
};

struct PreparedCellMutation {
    size_t cell_index{0};
    NlfCount old_value{0};
    NlfCount new_value{0};
};

struct PreparedNlfUpdate {
    std::array<PreparedCellMutation, 2> mutations{};
    uint8_t count{0};
    bool valid{false};

    void reset() noexcept {
        count = 0;
        valid = false;
    }
};

class ApxNlfIndex {
public:
    ApxNlfIndex() = default;

    // Checks dimensions, validates memory budget, initializes from G0 baseline
    void initialize(
        const ApxAuthoritativeGraph& graph,
        const ApxQuerySignatures& signatures,
        size_t max_memory_bytes = 0 // 0 = unlimited
    );

    bool is_initialized() const noexcept { return initialized_; }
    uint32_t vertex_count() const noexcept { return num_vertices_; }
    uint32_t signature_count() const noexcept { return num_signatures_; }
    size_t total_cells() const noexcept { return table_.size(); }
    size_t logical_bytes() const noexcept { return table_.size() * sizeof(NlfCount); }

    // Exact frequency lookup with bounds checking
    inline NlfCount count(uint32_t data_vertex, SignatureId sig_id) const {
        if (data_vertex >= num_vertices_ || sig_id >= num_signatures_) {
            throw InvariantViolationException(
                "NLF index out of bounds: vertex=" + std::to_string(data_vertex) +
                " (max=" + std::to_string(num_vertices_) + "), sig=" + std::to_string(sig_id) +
                " (max=" + std::to_string(num_signatures_) + ")"
            );
        }
        return table_[static_cast<size_t>(data_vertex) * num_signatures_ + sig_id];
    }

    // Direct unchecked lookup for verified hot paths
    inline NlfCount count_unchecked(uint32_t data_vertex, SignatureId sig_id) const noexcept {
        return table_[static_cast<size_t>(data_vertex) * num_signatures_ + sig_id];
    }

    // Transactional maintenance:
    // Step A: Prepare mutations, resolve affected cells (0, 1, or 2), arm undo record with old values.
    // Does NOT mutate the table. Throws on arithmetic/boundary error without changing state.
    void prepare_edge_update(
        const ApxAuthoritativeGraph& graph,
        const ApxQuerySignatures& signatures,
        uint32_t dense_u,
        uint32_t dense_v,
        uint32_t dense_edge_label,
        bool is_insert,
        PreparedNlfUpdate& prepared,
        NlfUndoRecord& undo
    );

    // Step B: Apply prepared mutations to table. Guaranteed noexcept and non-allocating.
    void apply_prepared_update(
        const PreparedNlfUpdate& prepared,
        EngineCounters* counters = nullptr
    ) noexcept;

    // Step C: Rollback in case transaction aborts. Restores old values of armed entries.
    // Guaranteed noexcept, non-allocating, safe under repeated invocation.
    void rollback(NlfUndoRecord& undo, EngineCounters* counters = nullptr) noexcept;

    // Step D: Finalize on transaction commit. Disarms undo record without modifying table.
    // Guaranteed noexcept.
    void finalize(NlfUndoRecord& undo) noexcept;

    // Direct cell setter for testing only
    void set_count_for_testing(uint32_t data_vertex, SignatureId sig_id, NlfCount val) {
        if (data_vertex >= num_vertices_ || sig_id >= num_signatures_) {
            throw InvariantViolationException("Out of bounds in set_count_for_testing");
        }
        table_[static_cast<size_t>(data_vertex) * num_signatures_ + sig_id] = val;
    }

    // Clear state safely without throwing (guaranteed non-allocating release)
    void clear() noexcept {
        std::vector<NlfCount>().swap(table_);
        num_vertices_ = 0;
        num_signatures_ = 0;
        initialized_ = false;
    }

    // Static deficit calculation helper
    static inline uint32_t compute_static_deficit(uint32_t required, uint32_t available) noexcept {
        return required > available ? required - available : 0;
    }

private:
    std::vector<NlfCount> table_;
    uint32_t num_vertices_{0};
    uint32_t num_signatures_{0};
    bool initialized_{false};
};

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_NLF_INDEX_H
