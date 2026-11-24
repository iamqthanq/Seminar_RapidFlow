#include "rapidflow_apx/residual_nlf_state.h"

#include <algorithm>
#include <new>

namespace rapidflow_apx {

ApxResidualMissingState::ApxResidualMissingState(
    const ApxQueryPlan& plan,
    const ApxQuerySignatures& signatures
) try {
    const size_t vertices = plan.get_vertices_count();
    const size_t edges = plan.get_edges_count();
    if (vertices >= offsets_.max_size() || edges > edge_slots_.max_size()) {
        throw std::bad_alloc();
    }

    offsets_.reserve(vertices + 1);
    offsets_.push_back(0);
    for (uint32_t u = 0; u < vertices; ++u) {
        const auto& reqs = signatures.get_vertex_requirements(u);
        if (reqs.size() > cells_.max_size() - offsets_.back()) {
            throw std::bad_alloc();
        }
        offsets_.push_back(offsets_.back() + reqs.size());
    }
    cells_.reserve(offsets_.back());
    for (uint32_t u = 0; u < vertices; ++u) {
        for (const auto& req : signatures.get_vertex_requirements(u)) {
            cells_.push_back({req.required_count, 0});
        }
    }

    const auto slot = [&](uint32_t u, SignatureId sig) {
        const auto& reqs = signatures.get_vertex_requirements(u);
        const auto it = std::lower_bound(reqs.begin(), reqs.end(), SignatureRequirement{sig, 0});
        if (it == reqs.end() || it->sig_id != sig || it->required_count == 0) {
            throw ResidualInvariantViolation("Residual edge signature has no endpoint requirement");
        }
        return offsets_[u] + static_cast<size_t>(it - reqs.begin());
    };

    edge_slots_.reserve(edges);
    for (uint32_t e = 0; e < edges; ++e) {
        const auto& edge = plan.get_edge(e);
        const auto& sigs = signatures.get_edge_signatures(e);
        const IncidentSignature from_u{edge.label, plan.get_vertex_label(edge.v)};
        const IncidentSignature from_v{edge.label, plan.get_vertex_label(edge.u)};
        if (signatures.get_signature(sigs.from_u) != from_u || signatures.get_signature(sigs.from_v) != from_v) {
            throw ResidualInvariantViolation("Residual edge signatures do not match canonical endpoints");
        }
        edge_slots_.push_back({slot(edge.u, sigs.from_u), slot(edge.v, sigs.from_v)});
    }
} catch (const std::out_of_range&) {
    throw ResidualInvariantViolation("Residual query signature registry does not match the query plan");
}

const std::array<size_t, 2>& ApxResidualMissingState::checked_slots(uint32_t edge) const {
    if (edge >= edge_slots_.size()) {
        throw ResidualInvariantViolation("Residual edge ID out of bounds");
    }
    const auto& slots = edge_slots_[edge];
    if (slots[0] >= cells_.size() || slots[1] >= cells_.size() || slots[0] == slots[1]) {
        throw ResidualInvariantViolation("Invalid residual endpoint slots");
    }
    return slots;
}

void ApxResidualMissingState::commit_missing(uint32_t edge) {
    const auto& slots = checked_slots(edge);
    auto& u = cells_[slots[0]];
    auto& v = cells_[slots[1]];
    if (u.missing >= u.capacity || v.missing >= v.capacity) {
        throw ResidualInvariantViolation("Residual MISSING commitment exceeds endpoint capacity");
    }
    ++u.missing;
    ++v.missing;
}

void ApxResidualMissingState::undo_missing(uint32_t edge) {
    const auto& slots = checked_slots(edge);
    auto& u = cells_[slots[0]];
    auto& v = cells_[slots[1]];
    if (u.missing == 0 || v.missing == 0) {
        throw ResidualInvariantViolation("Residual MISSING undo would underflow");
    }
    --u.missing;
    --v.missing;
}

NlfCount ApxResidualMissingState::count(uint32_t vertex, size_t requirement) const {
    if (vertex >= offsets_.size() - 1 || requirement >= offsets_[vertex + 1] - offsets_[vertex]) {
        throw ResidualInvariantViolation("Residual requirement slot out of bounds");
    }
    return cells_[offsets_[vertex] + requirement].missing;
}

NlfCount ApxResidualMissingState::remaining_requirement(NlfCount required, NlfCount committed) {
    if (committed > required) {
        throw ResidualInvariantViolation("Residual count exceeds query requirement");
    }
    return required - committed;
}

bool ApxResidualMissingState::allows(
    const ApxQuerySignatures& signatures,
    const ApxNlfIndex& index,
    uint32_t query_vertex,
    uint32_t data_vertex,
    uint32_t k,
    uint32_t missing_count,
    EngineCounters& counters
) const {
    if (missing_count > k) {
        return false;
    }
    uint64_t remaining_budget = static_cast<uint64_t>(k) - missing_count;
    const auto& reqs = signatures.get_vertex_requirements(query_vertex);
    for (size_t i = 0; i < reqs.size(); ++i) {
        const auto& req = reqs[i];
        const NlfCount need = remaining_requirement(req.required_count, count(query_vertex, i));
        if (need == 0) {
            continue;
        }
        ++counters.nlf_signature_lookups;
        ++counters.nlf_residual_signature_lookups;
        const NlfCount available = index.count_unchecked(data_vertex, req.sig_id);
        const NlfCount deficit = ApxNlfIndex::compute_static_deficit(need, available);
        if (deficit > remaining_budget) {
            return false;
        }
        remaining_budget -= deficit;
    }
    return true;
}

} // namespace rapidflow_apx
