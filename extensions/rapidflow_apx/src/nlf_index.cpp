#include "rapidflow_apx/nlf_index.h"

namespace rapidflow_apx {

void ApxNlfIndex::initialize(
    const ApxAuthoritativeGraph& graph,
    const ApxQuerySignatures& signatures,
    size_t max_memory_bytes
) {
    clear();

    if (!graph.is_pristine_initial_state()) {
        throw InvariantViolationException(
            "NLF index initialization rejected: graph is not in pristine initial G0 state (overlay or tombstones present)"
        );
    }

    const uint32_t V = graph.get_vertices_count();
    const uint32_t S = static_cast<uint32_t>(signatures.signature_count());

    if (V == 0 || S == 0) {
        num_vertices_ = V;
        num_signatures_ = S;
        initialized_ = true;
        return;
    }

    // Checked arithmetic for dimension multiplication
    const size_t v_size = static_cast<size_t>(V);
    const size_t s_size = static_cast<size_t>(S);

    if (s_size != 0 && v_size > std::numeric_limits<size_t>::max() / s_size) {
        throw InvariantViolationException(
            "Cell multiplication overflow in NLF index: V=" + std::to_string(V) + " * S=" + std::to_string(S)
        );
    }

    const size_t total_cells_count = v_size * s_size;

    if (total_cells_count > table_.max_size()) {
        throw InvariantViolationException(
            "Total cells exceeds vector max_size in NLF index: " + std::to_string(total_cells_count)
        );
    }

    if (total_cells_count > std::numeric_limits<size_t>::max() / sizeof(NlfCount)) {
        throw InvariantViolationException(
            "Byte multiplication overflow in NLF index: cells=" + std::to_string(total_cells_count)
        );
    }

    const size_t total_bytes = total_cells_count * sizeof(NlfCount);

    if (max_memory_bytes > 0 && total_bytes > max_memory_bytes) {
        throw NlfMemoryLimitException(
            "NLF index logical memory (" + std::to_string(total_bytes) +
            " bytes) exceeds configured limit (" + std::to_string(max_memory_bytes) + " bytes)"
        );
    }

    // Allocate temporary table locally to guarantee atomic publication
    std::vector<NlfCount> temp_table(total_cells_count, 0);

    // Enumerate every undirected edge at G0 boundary exactly once
    for (uint32_t u = 0; u < V; ++u) {
        std::vector<uint32_t> neighbors = graph.get_neighbors(u);
        uint32_t u_vlabel = graph.get_vertex_label(u);

        for (uint32_t v : neighbors) {
            if (u < v) {
                auto lookup = graph.get_edge_label(u, v);
                if (!lookup.exists) {
                    throw InvariantViolationException(
                        "G0 authoritative label lookup failed for existing edge (" +
                        std::to_string(u) + ", " + std::to_string(v) + ")"
                    );
                }

                uint32_t edge_label = lookup.label;
                uint32_t v_vlabel = graph.get_vertex_label(v);

                // Incident signature at u: (edge_label, v_vlabel)
                SignatureId sig_u = 0;
                if (signatures.find_signature_id(edge_label, v_vlabel, sig_u)) {
                    size_t idx_u = static_cast<size_t>(u) * s_size + sig_u;
                    if (temp_table[idx_u] == std::numeric_limits<NlfCount>::max()) {
                        throw InvariantViolationException("NLF frequency overflow during G0 initialization");
                    }
                    temp_table[idx_u]++;
                }

                // Incident signature at v: (edge_label, u_vlabel)
                SignatureId sig_v = 0;
                if (signatures.find_signature_id(edge_label, u_vlabel, sig_v)) {
                    size_t idx_v = static_cast<size_t>(v) * s_size + sig_v;
                    if (temp_table[idx_v] == std::numeric_limits<NlfCount>::max()) {
                        throw InvariantViolationException("NLF frequency overflow during G0 initialization");
                    }
                    temp_table[idx_v]++;
                }
            }
        }
    }

    // Publish completed state atomically
    table_ = std::move(temp_table);
    num_vertices_ = V;
    num_signatures_ = S;
    initialized_ = true;
}

void ApxNlfIndex::prepare_edge_update(
    const ApxAuthoritativeGraph& graph,
    const ApxQuerySignatures& signatures,
    uint32_t dense_u,
    uint32_t dense_v,
    uint32_t dense_edge_label,
    bool is_insert,
    PreparedNlfUpdate& prepared,
    NlfUndoRecord& undo
) {
    prepared.reset();
    undo.reset();

    if (!initialized_) {
        throw InvariantViolationException("Cannot prepare update on uninitialized NLF index");
    }

    if (dense_u >= num_vertices_ || dense_v >= num_vertices_) {
        throw InvariantViolationException(
            "NLF update endpoint out of bounds: u=" + std::to_string(dense_u) +
            ", v=" + std::to_string(dense_v) + ", max=" + std::to_string(num_vertices_)
        );
    }

    if (dense_u == dense_v) {
        throw InvariantViolationException("Self-loop updates not permitted in NLF index");
    }

    uint32_t u_vlabel = graph.get_vertex_label(dense_u);
    uint32_t v_vlabel = graph.get_vertex_label(dense_v);
    const size_t S = num_signatures_;
    uint8_t count = 0;

    // Check signature at u: (edge_label, v_vlabel)
    SignatureId sig_u = 0;
    if (signatures.find_signature_id(dense_edge_label, v_vlabel, sig_u)) {
        size_t cell_u = static_cast<size_t>(dense_u) * S + sig_u;
        NlfCount old_u = table_[cell_u];
        NlfCount new_u = 0;

        if (is_insert) {
            if (old_u == std::numeric_limits<NlfCount>::max()) {
                throw InvariantViolationException("NLF count overflow on insert at cell " + std::to_string(cell_u));
            }
            new_u = old_u + 1;
        } else {
            if (old_u == 0) {
                throw InvariantViolationException("NLF count underflow on delete at cell " + std::to_string(cell_u));
            }
            new_u = old_u - 1;
        }

        prepared.mutations[count] = PreparedCellMutation{cell_u, old_u, new_u};
        undo.entries[count] = NlfUndoEntry{cell_u, old_u, true};
        count++;
    }

    // Check signature at v: (edge_label, u_vlabel)
    SignatureId sig_v = 0;
    if (signatures.find_signature_id(dense_edge_label, u_vlabel, sig_v)) {
        size_t cell_v = static_cast<size_t>(dense_v) * S + sig_v;
        NlfCount old_v = table_[cell_v];
        NlfCount new_v = 0;

        if (is_insert) {
            if (old_v == std::numeric_limits<NlfCount>::max()) {
                throw InvariantViolationException("NLF count overflow on insert at cell " + std::to_string(cell_v));
            }
            new_v = old_v + 1;
        } else {
            if (old_v == 0) {
                throw InvariantViolationException("NLF count underflow on delete at cell " + std::to_string(cell_v));
            }
            new_v = old_v - 1;
        }

        prepared.mutations[count] = PreparedCellMutation{cell_v, old_v, new_v};
        undo.entries[count] = NlfUndoEntry{cell_v, old_v, true};
        count++;
    }

    prepared.count = count;
    prepared.valid = true;
    undo.count = count;
    undo.active = true;
}

void ApxNlfIndex::apply_prepared_update(
    const PreparedNlfUpdate& prepared,
    EngineCounters* counters
) noexcept {
    if (!prepared.valid) {
        return;
    }
    for (uint8_t i = 0; i < prepared.count; ++i) {
        table_[prepared.mutations[i].cell_index] = prepared.mutations[i].new_value;
        if (counters) {
            counters->nlf_index_cell_updates++;
        }
    }
}

void ApxNlfIndex::rollback(
    NlfUndoRecord& undo,
    EngineCounters* counters
) noexcept {
    if (!undo.active) {
        return;
    }
    for (uint8_t i = 0; i < undo.count; ++i) {
        if (undo.entries[i].armed) {
            table_[undo.entries[i].cell_index] = undo.entries[i].old_value;
            undo.entries[i].armed = false;
            if (counters) {
                counters->nlf_index_rollback_cells++;
            }
        }
    }
    undo.count = 0;
    undo.active = false;
}

void ApxNlfIndex::finalize(NlfUndoRecord& undo) noexcept {
    undo.reset();
}

} // namespace rapidflow_apx
