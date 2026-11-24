#include "rapidflow_apx/m2_event_engine.h"
#include <algorithm>

namespace rapidflow_apx {

int ApxM2EventEngine::find_frontier_edge(
    const std::vector<uint32_t>& mapping,
    const std::vector<EdgeCommitment>& status,
    uint32_t missing_count,
    uint32_t assigned_count,
    const ApxQueryPlan& query_plan
) {
    uint32_t num_q = query_plan.get_vertices_count();
    uint32_t num_m = query_plan.get_edges_count();
    if (assigned_count >= num_q) {
        return -1;
    }

    int best_frontier_edge = -1;
    for (uint32_t j = 0; j < num_m; ++j) {
        if (status[j] == EdgeCommitment::UNKNOWN) {
            const auto& q_edge = query_plan.get_edge(j);
            bool u_mapped = (mapping[q_edge.u] != UNASSIGNED);
            bool v_mapped = (mapping[q_edge.v] != UNASSIGNED);
            if (u_mapped != v_mapped) {
                best_frontier_edge = static_cast<int>(j);
                break;
            }
        }
    }

    // Invariant guard: empty frontier when unmapped vertices remain and missing_count <= k
    uint32_t k = query_plan.get_k();
    if (best_frontier_edge == -1 && missing_count <= k) {
        throw InvariantViolationException(
            "M2 invariant violation: empty frontier while unmapped vertices remain and missing_count=" +
            std::to_string(missing_count) + " <= k=" + std::to_string(k) +
            ". Domain condition k < lambda(Q) required."
        );
    }
    return best_frontier_edge;
}

uint64_t ApxM2EventEngine::search_event(
    const ApxQueryPlan& query_plan,
    const ApxAuthoritativeGraph& snapshot,
    uint32_t u_upd,
    uint32_t v_upd,
    uint32_t label_upd,
    ApxTupleSink& sink,
    EngineCounters& counters,
    StepProfiling* profiler
) {
    uint32_t num_q = query_plan.get_vertices_count();
    uint32_t num_m = query_plan.get_edges_count();
    uint32_t num_d = snapshot.get_vertices_count();
    if (num_d < num_q) {
        return 0;
    }

    const auto& candidate_edges = query_plan.get_edges_with_label(label_upd);
    if (candidate_edges.empty()) {
        return 0;
    }

    std::vector<uint32_t> mapping(num_q, UNASSIGNED);
    std::vector<bool> used_data(num_d, false);
    std::vector<EdgeCommitment> status(num_m, EdgeCommitment::UNKNOWN);
    uint64_t initial_emissions = sink.raw_emissions();

    for (uint32_t edge_idx : candidate_edges) {
        const auto& q_edge = query_plan.get_edge(edge_idx);
        uint32_t qa = q_edge.u;
        uint32_t qb = q_edge.v;

        // Try both orientations
        for (int orient = 0; orient < 2; ++orient) {
            uint32_t pa = (orient == 0) ? u_upd : v_upd;
            uint32_t pb = (orient == 0) ? v_upd : u_upd;

            counters.vertex_label_checks += 2;
            if (snapshot.get_vertex_label(pa) != query_plan.get_vertex_label(qa)) {
                continue;
            }
            if (snapshot.get_vertex_label(pb) != query_plan.get_vertex_label(qb)) {
                continue;
            }

            counters.anchors_started++;
            counters.charge_work();

            // Initialize anchor state
            mapping[qa] = pa;
            mapping[qb] = pb;
            used_data[pa] = true;
            used_data[pb] = true;
            status[edge_idx] = EdgeCommitment::PRESENT;

            dfs_m2(
                mapping,
                used_data,
                status,
                0, // missing_count
                2, // assigned_count
                query_plan,
                snapshot,
                sink,
                counters,
                profiler
            );

            // Undo anchor state
            status[edge_idx] = EdgeCommitment::UNKNOWN;
            used_data[pa] = false;
            used_data[pb] = false;
            mapping[qa] = UNASSIGNED;
            mapping[qb] = UNASSIGNED;
        }
    }

    return sink.raw_emissions() - initial_emissions;
}

void ApxM2EventEngine::dfs_m2(
    std::vector<uint32_t>& mapping,
    std::vector<bool>& used_data,
    std::vector<EdgeCommitment>& status,
    uint32_t missing_count,
    uint32_t assigned_count,
    const ApxQueryPlan& query_plan,
    const ApxAuthoritativeGraph& snapshot,
    ApxTupleSink& sink,
    EngineCounters& counters,
    StepProfiling* profiler
) {
    counters.dfs_states++;
    uint32_t k = query_plan.get_k();
    if (missing_count > k) {
        return;
    }

    uint32_t num_q = query_plan.get_vertices_count();
    uint32_t num_m = query_plan.get_edges_count();

    // Leaf condition: all query vertices mapped
    if (assigned_count == num_q) {
        // Verify commitments on all query edges
        for (uint32_t j = 0; j < num_m; ++j) {
            counters.edge_lookups++;
            const auto& q_edge = query_plan.get_edge(j);
            uint32_t da = mapping[q_edge.u];
            uint32_t db = mapping[q_edge.v];
            auto lookup = snapshot.get_edge_label(da, db);
            bool present = lookup.exists && lookup.label == q_edge.label;

            if (status[j] == EdgeCommitment::MISSING) {
                counters.commitment_checks++;
                if (present) {
                    counters.commitment_rejections++;
                    return; // Committed MISSING edge actually exists with matching label!
                }
            } else if (status[j] == EdgeCommitment::PRESENT) {
                if (!present) {
                    return; // Committed PRESENT edge missing or label mismatch
                }
            }
        }

        // Strict event boundary lemma: |R| == k
        if (missing_count == k) {
            sink.emit_mapping(mapping);
            counters.raw_emissions++;
        }
        return;
    }

    // Frontier: query edges with UNKNOWN status across cut
    int best_frontier_edge = find_frontier_edge(mapping, status, missing_count, assigned_count, query_plan);
    if (best_frontier_edge == -1) {
        return;
    }

    uint32_t e = static_cast<uint32_t>(best_frontier_edge);
    counters.edge_decisions++;
    counters.charge_work();

    const auto& frontier_edge = query_plan.get_edge(e);
    uint32_t u_q = (mapping[frontier_edge.u] != UNASSIGNED) ? frontier_edge.u : frontier_edge.v;
    uint32_t v_q = (mapping[frontier_edge.u] != UNASSIGNED) ? frontier_edge.v : frontier_edge.u;
    uint32_t u_data = mapping[u_q];
    uint32_t v_q_label = query_plan.get_vertex_label(v_q);

    // --- PRESENT Branch ---
    std::vector<uint32_t> cand_storage;
    if (profiler) {
        auto t0 = ProfilingClock::now();
        cand_storage = snapshot.get_neighbors(u_data);
        auto t1 = ProfilingClock::now();
        profiler->neighbor_retrieval_ns += std::chrono::duration_cast<ProfilingNanoseconds>(t1 - t0).count();
    } else {
        cand_storage = snapshot.get_neighbors(u_data);
    }
    const auto& candidates = cand_storage;
    for (uint32_t cand : candidates) {
        counters.candidate_evaluations++;
        counters.charge_work();

        counters.edge_lookups++;
        auto lookup = snapshot.get_edge_label(u_data, cand);
        if (!lookup.exists || lookup.label != frontier_edge.label) {
            continue;
        }

        if (used_data[cand]) {
            continue;
        }

        counters.vertex_label_checks++;
        if (snapshot.get_vertex_label(cand) != v_q_label) {
            continue;
        }

        counters.candidate_extensions++;

        // Tentatively assign v_q -> cand
        mapping[v_q] = cand;
        used_data[cand] = true;
        status[e] = EdgeCommitment::PRESENT;

        if (fault_hook_ == M2FaultHook::AFTER_VERTEX_ASSIGNMENT) {
            throw FaultInjectedException("Simulated fault injected AFTER_VERTEX_ASSIGNMENT in M2 DFS");
        }

        std::vector<std::pair<uint32_t, EdgeCommitment>> status_undo;
        status_undo.emplace_back(e, EdgeCommitment::UNKNOWN);
        uint32_t old_missing_count = missing_count;
        bool ok = true;

        // Two-endpoint edge resolution for incident edges to v_q
        for (const auto& inc : query_plan.get_incident_edges(v_q)) {
            uint32_t other_q = inc.first;
            uint32_t inc_j = inc.second;
            if (inc_j == e) continue;

            if (mapping[other_q] != UNASSIGNED) {
                counters.edge_lookups++;
                uint32_t other_data = mapping[other_q];
                const auto& other_edge = query_plan.get_edge(inc_j);
                auto inc_lookup = snapshot.get_edge_label(cand, other_data);
                bool present = inc_lookup.exists && inc_lookup.label == other_edge.label;

                if (status[inc_j] == EdgeCommitment::MISSING) {
                    counters.commitment_checks++;
                    if (present) {
                        ok = false;
                        break;
                    }
                } else if (status[inc_j] == EdgeCommitment::PRESENT) {
                    if (!present) {
                        ok = false;
                        break;
                    }
                } else if (status[inc_j] == EdgeCommitment::UNKNOWN) {
                    if (present) {
                        status_undo.emplace_back(inc_j, EdgeCommitment::UNKNOWN);
                        status[inc_j] = EdgeCommitment::PRESENT;
                    } else {
                        status_undo.emplace_back(inc_j, EdgeCommitment::UNKNOWN);
                        status[inc_j] = EdgeCommitment::MISSING;
                        missing_count++;
                        if (missing_count > k) {
                            ok = false;
                            break;
                        }
                    }
                }
            }
        }

        if (ok) {
            dfs_m2(
                mapping,
                used_data,
                status,
                missing_count,
                assigned_count + 1,
                query_plan,
                snapshot,
                sink,
                counters,
                profiler
            );
        }

        // Rollback PRESENT branch
        for (auto it = status_undo.rbegin(); it != status_undo.rend(); ++it) {
            status[it->first] = it->second;
        }
        missing_count = old_missing_count;
        used_data[cand] = false;
        mapping[v_q] = UNASSIGNED;
    }

    // --- MISSING Branch ---
    counters.missing_branches++;
    counters.charge_work();

    status[e] = EdgeCommitment::MISSING;
    uint32_t new_missing_count = missing_count + 1;

    if (fault_hook_ == M2FaultHook::AFTER_MISSING_COMMITMENT) {
        throw FaultInjectedException("Simulated fault injected AFTER_MISSING_COMMITMENT in M2 DFS");
    }

    if (new_missing_count <= k) {
        // Defer vertex assignment: mapping and assigned_count remain unchanged
        dfs_m2(
            mapping,
            used_data,
            status,
            new_missing_count,
            assigned_count,
            query_plan,
            snapshot,
            sink,
            counters,
            profiler
        );
    }

    // Rollback MISSING branch
    status[e] = EdgeCommitment::UNKNOWN;
}

} // namespace rapidflow_apx
