#include "rapidflow_apx/m1_enum_engine.h"
#include <algorithm>

namespace rapidflow_apx {

uint64_t ApxM1EnumEngine::search_event(
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

            // Stream M1 variants for Q - D with anchor (qa, qb) lazily, charging work budget upfront
            query_plan.for_each_m1_variant(edge_idx, qa, qb, counters, [&](const M1VariantPlan& variant) {
                mapping[qa] = pa;
                mapping[qb] = pb;
                used_data[pa] = true;
                used_data[pb] = true;

                match_variant_dfs(
                    variant,
                    2, // search order starts with [qa, qb], so next is index 2
                    mapping,
                    used_data,
                    query_plan,
                    snapshot,
                    sink,
                    counters,
                    profiler
                );

                used_data[pa] = false;
                used_data[pb] = false;
                mapping[qa] = UNASSIGNED;
                mapping[qb] = UNASSIGNED;
            });
        }
    }

    return sink.raw_emissions() - initial_emissions;
}

void ApxM1EnumEngine::match_variant_dfs(
    const M1VariantPlan& variant,
    size_t idx,
    std::vector<uint32_t>& mapping,
    std::vector<bool>& used_data,
    const ApxQueryPlan& query_plan,
    const ApxAuthoritativeGraph& snapshot,
    ApxTupleSink& sink,
    EngineCounters& counters,
    StepProfiling* profiler
) {
    counters.dfs_states++;
    if (idx == variant.search_order.size()) {
        counters.raw_exact_results++;

        // Verify that all edges in missing set D are strictly missing in snapshot
        for (uint32_t missing_edge_idx : variant.missing_edges) {
            counters.edge_lookups++;
            counters.charge_work();

            const auto& d_edge = query_plan.get_edge(missing_edge_idx);
            uint32_t da = mapping[d_edge.u];
            uint32_t db = mapping[d_edge.v];
            auto lookup = snapshot.get_edge_label(da, db);
            if (lookup.exists && lookup.label == d_edge.label) {
                counters.missing_set_rejections++;
                return; // An edge in D is actually present with matching label
            }
        }

        // Emit mapping (sink handles conversion to canonical ordered tuple)
        sink.emit_mapping(mapping);
        counters.raw_emissions++;
        return;
    }

    uint32_t curr_q = variant.search_order[idx];
    uint32_t curr_q_label = query_plan.get_vertex_label(curr_q);

    // Collect already mapped neighbors along kept edges
    std::vector<std::pair<uint32_t, uint32_t>> mapped_nbrs;
    for (const auto& kv : variant.kept_incident[curr_q]) {
        uint32_t nbr_q = kv.first;
        uint32_t kept_lbl = kv.second;
        if (mapping[nbr_q] != UNASSIGNED) {
            mapped_nbrs.emplace_back(nbr_q, kept_lbl);
        }
    }

    if (mapped_nbrs.empty()) {
        throw InvariantViolationException("Disconnected search state encountered in M1 DFS");
    }

    uint32_t p1_q = mapped_nbrs[0].first;
    uint32_t lbl1 = mapped_nbrs[0].second;
    uint32_t p1_data = mapping[p1_q];

    std::vector<uint32_t> cand_storage;
    if (profiler) {
        auto t0 = ProfilingClock::now();
        cand_storage = snapshot.get_neighbors(p1_data);
        auto t1 = ProfilingClock::now();
        profiler->neighbor_retrieval_ns += std::chrono::duration_cast<ProfilingNanoseconds>(t1 - t0).count();
    } else {
        cand_storage = snapshot.get_neighbors(p1_data);
    }
    const auto& candidates = cand_storage;
    for (uint32_t cand : candidates) {
        counters.candidate_evaluations++;
        counters.charge_work();

        counters.edge_lookups++;
        auto lookup = snapshot.get_edge_label(p1_data, cand);
        if (!lookup.exists || lookup.label != lbl1) {
            continue;
        }

        if (used_data[cand]) {
            continue;
        }

        counters.vertex_label_checks++;
        if (snapshot.get_vertex_label(cand) != curr_q_label) {
            continue;
        }

        // Verify compatibility with remaining mapped neighbors
        bool compatible = true;
        for (size_t i = 1; i < mapped_nbrs.size(); ++i) {
            counters.edge_lookups++;
            uint32_t other_data = mapping[mapped_nbrs[i].first];
            uint32_t other_lbl = mapped_nbrs[i].second;
            auto other_lookup = snapshot.get_edge_label(cand, other_data);
            if (!other_lookup.exists || other_lookup.label != other_lbl) {
                compatible = false;
                break;
            }
        }

        if (!compatible) {
            continue;
        }

        counters.candidate_extensions++;

        // Tentative assignment
        mapping[curr_q] = cand;
        used_data[cand] = true;

        match_variant_dfs(
            variant,
            idx + 1,
            mapping,
            used_data,
            query_plan,
            snapshot,
            sink,
            counters,
            profiler
        );

        // Backtrack
        used_data[cand] = false;
        mapping[curr_q] = UNASSIGNED;
    }
}

} // namespace rapidflow_apx
