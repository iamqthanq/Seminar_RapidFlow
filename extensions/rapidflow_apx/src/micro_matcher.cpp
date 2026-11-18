#include "rapidflow_apx/micro_matcher.h"
#include <algorithm>

namespace rapidflow_apx {

MicroExactMatcher::MicroExactMatcher(uint32_t k) : k_(k) {
    if (k_ != 0) {
        throw std::invalid_argument("MicroExactMatcher only supports k=0 (exact matching). k > 0 rejected.");
    }
}

bool MicroExactMatcher::check_all_edges(
    const ApxAuthoritativeGraph& query,
    const ApxAuthoritativeGraph& data,
    const std::vector<uint32_t>& mapping
) {
    uint32_t num_q = query.get_vertices_count();
    for (uint32_t u = 0; u < num_q; ++u) {
        for (uint32_t v : query.get_neighbors(u)) {
            if (u < v) {
                auto q_lbl = query.get_edge_label(u, v);
                auto d_lbl = data.get_edge_label(mapping[u], mapping[v]);
                if (!d_lbl.exists || d_lbl.label != q_lbl.label) {
                    return false;
                }
            }
        }
    }
    return true;
}

uint64_t MicroExactMatcher::match_snapshot(
    const ApxAuthoritativeGraph& query,
    const ApxAuthoritativeGraph& data,
    ApxTupleSink& sink
) {
    uint32_t num_q = query.get_vertices_count();
    uint32_t num_d = data.get_vertices_count();
    if (num_q == 0 || num_d < num_q) {
        return 0;
    }

    std::vector<uint32_t> mapping(num_q, 0);
    std::vector<bool> query_mapped(num_q, false);
    std::vector<bool> data_used(num_d, false);
    uint64_t match_count = 0;

    dfs_snapshot(query, data, mapping, query_mapped, data_used, 0, sink, match_count);
    return match_count;
}

void MicroExactMatcher::dfs_snapshot(
    const ApxAuthoritativeGraph& query,
    const ApxAuthoritativeGraph& data,
    std::vector<uint32_t>& mapping,
    std::vector<bool>& query_mapped,
    std::vector<bool>& data_used,
    uint32_t mapped_count,
    ApxTupleSink& sink,
    uint64_t& match_count
) {
    uint32_t num_q = query.get_vertices_count();
    uint32_t num_d = data.get_vertices_count();

    if (mapped_count == num_q) {
        if (check_all_edges(query, data, mapping)) {
            sink.emit_mapping(mapping);
            match_count++;
        }
        return;
    }

    // Pick next unmapped query vertex with highest number of mapped neighbors
    int best_w_q = -1;
    int max_mapped_nbrs = -1;
    for (uint32_t u = 0; u < num_q; ++u) {
        if (!query_mapped[u]) {
            int mapped_nbrs = 0;
            for (uint32_t nbr : query.get_neighbors(u)) {
                if (query_mapped[nbr]) {
                    mapped_nbrs++;
                }
            }
            if (mapped_nbrs > max_mapped_nbrs) {
                max_mapped_nbrs = mapped_nbrs;
                best_w_q = static_cast<int>(u);
            }
        }
    }

    uint32_t w_q = static_cast<uint32_t>(best_w_q);
    uint32_t w_q_label = query.get_vertex_label(w_q);

    if (max_mapped_nbrs > 0) {
        // Collect candidates from neighbors of the first mapped neighbor
        uint32_t pivot_nbr_q = 0;
        for (uint32_t nbr : query.get_neighbors(w_q)) {
            if (query_mapped[nbr]) {
                pivot_nbr_q = nbr;
                break;
            }
        }
        uint32_t pivot_nbr_d = mapping[pivot_nbr_q];
        for (uint32_t cand : data.get_neighbors(pivot_nbr_d)) {
            if (!data_used[cand] && data.get_vertex_label(cand) == w_q_label) {
                bool ok = true;
                for (uint32_t other_nbr_q : query.get_neighbors(w_q)) {
                    if (query_mapped[other_nbr_q]) {
                        uint32_t other_nbr_d = mapping[other_nbr_q];
                        auto q_lbl = query.get_edge_label(w_q, other_nbr_q);
                        auto d_lbl = data.get_edge_label(cand, other_nbr_d);
                        if (!d_lbl.exists || d_lbl.label != q_lbl.label) {
                            ok = false;
                            break;
                        }
                    }
                }
                if (ok) {
                    mapping[w_q] = cand;
                    query_mapped[w_q] = true;
                    data_used[cand] = true;
                    dfs_snapshot(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);
                    query_mapped[w_q] = false;
                    data_used[cand] = false;
                }
            }
        }
    } else {
        // No mapped neighbors: scan all candidate data vertices
        for (uint32_t cand = 0; cand < num_d; ++cand) {
            if (!data_used[cand] && data.get_vertex_label(cand) == w_q_label) {
                mapping[w_q] = cand;
                query_mapped[w_q] = true;
                data_used[cand] = true;
                dfs_snapshot(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);
                query_mapped[w_q] = false;
                data_used[cand] = false;
            }
        }
    }
}

uint64_t MicroExactMatcher::match_delta(
    const ApxAuthoritativeGraph& query,
    const ApxAuthoritativeGraph& data,
    uint32_t dense_u,
    uint32_t dense_v,
    uint32_t dense_label,
    ApxTupleSink& sink
) {
    uint32_t num_q = query.get_vertices_count();
    uint32_t num_d = data.get_vertices_count();
    if (num_q < 2 || num_d < num_q) {
        return 0;
    }

    std::vector<uint32_t> mapping(num_q, 0);
    std::vector<bool> query_mapped(num_q, false);
    std::vector<bool> data_used(num_d, false);
    uint64_t match_count = 0;

    // Iterate through all query edges matching dense_label
    for (uint32_t u_q = 0; u_q < num_q; ++u_q) {
        for (uint32_t v_q : query.get_neighbors(u_q)) {
            if (u_q < v_q) {
                auto q_edge = query.get_edge_label(u_q, v_q);
                if (q_edge.label != dense_label) {
                    continue;
                }

                // Binding 1: u_q -> dense_u, v_q -> dense_v
                if (query.get_vertex_label(u_q) == data.get_vertex_label(dense_u) &&
                    query.get_vertex_label(v_q) == data.get_vertex_label(dense_v)) {
                    mapping[u_q] = dense_u;
                    mapping[v_q] = dense_v;
                    query_mapped[u_q] = true;
                    query_mapped[v_q] = true;
                    data_used[dense_u] = true;
                    data_used[dense_v] = true;

                    dfs_delta(query, data, mapping, query_mapped, data_used, 2, sink, match_count);

                    query_mapped[u_q] = false;
                    query_mapped[v_q] = false;
                    data_used[dense_u] = false;
                    data_used[dense_v] = false;
                }

                // Binding 2: u_q -> dense_v, v_q -> dense_u
                if (query.get_vertex_label(u_q) == data.get_vertex_label(dense_v) &&
                    query.get_vertex_label(v_q) == data.get_vertex_label(dense_u)) {
                    mapping[u_q] = dense_v;
                    mapping[v_q] = dense_u;
                    query_mapped[u_q] = true;
                    query_mapped[v_q] = true;
                    data_used[dense_v] = true;
                    data_used[dense_u] = true;

                    dfs_delta(query, data, mapping, query_mapped, data_used, 2, sink, match_count);

                    query_mapped[u_q] = false;
                    query_mapped[v_q] = false;
                    data_used[dense_v] = false;
                    data_used[dense_u] = false;
                }
            }
        }
    }

    return match_count;
}

void MicroExactMatcher::dfs_delta(
    const ApxAuthoritativeGraph& query,
    const ApxAuthoritativeGraph& data,
    std::vector<uint32_t>& mapping,
    std::vector<bool>& query_mapped,
    std::vector<bool>& data_used,
    uint32_t mapped_count,
    ApxTupleSink& sink,
    uint64_t& match_count
) {
    uint32_t num_q = query.get_vertices_count();
    uint32_t num_d = data.get_vertices_count();

    if (mapped_count == num_q) {
        if (check_all_edges(query, data, mapping)) {
            sink.emit_mapping(mapping);
            match_count++;
        }
        return;
    }

    // Pick next unmapped query vertex with highest number of mapped neighbors
    int best_w_q = -1;
    int max_mapped_nbrs = -1;
    for (uint32_t u = 0; u < num_q; ++u) {
        if (!query_mapped[u]) {
            int mapped_nbrs = 0;
            for (uint32_t nbr : query.get_neighbors(u)) {
                if (query_mapped[nbr]) {
                    mapped_nbrs++;
                }
            }
            if (mapped_nbrs > max_mapped_nbrs) {
                max_mapped_nbrs = mapped_nbrs;
                best_w_q = static_cast<int>(u);
            }
        }
    }

    uint32_t w_q = static_cast<uint32_t>(best_w_q);
    uint32_t w_q_label = query.get_vertex_label(w_q);

    if (max_mapped_nbrs > 0) {
        uint32_t pivot_nbr_q = 0;
        for (uint32_t nbr : query.get_neighbors(w_q)) {
            if (query_mapped[nbr]) {
                pivot_nbr_q = nbr;
                break;
            }
        }
        uint32_t pivot_nbr_d = mapping[pivot_nbr_q];
        for (uint32_t cand : data.get_neighbors(pivot_nbr_d)) {
            if (!data_used[cand] && data.get_vertex_label(cand) == w_q_label) {
                bool ok = true;
                for (uint32_t other_nbr_q : query.get_neighbors(w_q)) {
                    if (query_mapped[other_nbr_q]) {
                        uint32_t other_nbr_d = mapping[other_nbr_q];
                        auto q_lbl = query.get_edge_label(w_q, other_nbr_q);
                        auto d_lbl = data.get_edge_label(cand, other_nbr_d);
                        if (!d_lbl.exists || d_lbl.label != q_lbl.label) {
                            ok = false;
                            break;
                        }
                    }
                }
                if (ok) {
                    mapping[w_q] = cand;
                    query_mapped[w_q] = true;
                    data_used[cand] = true;
                    dfs_delta(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);
                    query_mapped[w_q] = false;
                    data_used[cand] = false;
                }
            }
        }
    } else {
        for (uint32_t cand = 0; cand < num_d; ++cand) {
            if (!data_used[cand] && data.get_vertex_label(cand) == w_q_label) {
                mapping[w_q] = cand;
                query_mapped[w_q] = true;
                data_used[cand] = true;
                dfs_delta(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);
                query_mapped[w_q] = false;
                data_used[cand] = false;
            }
        }
    }
}

FixedMappingEvaluation MicroExactMatcher::evaluate_fixed_mapping(
    const ApxAuthoritativeGraph& query,
    const ApxAuthoritativeGraph& data,
    const std::vector<uint32_t>& mapping_by_query_order,
    uint32_t k,
    uint32_t lambda
) {
    FixedMappingEvaluation eval;
    uint32_t num_q = query.get_vertices_count();
    if (mapping_by_query_order.size() != num_q) {
        eval.is_valid = false;
        eval.diagnostic_message = "Mapping size does not match query vertex count";
        return eval;
    }

    // Check injectivity
    std::vector<bool> seen_data(data.get_vertices_count(), false);
    for (uint32_t d_v : mapping_by_query_order) {
        if (!data.has_vertex(d_v)) {
            eval.is_valid = false;
            eval.diagnostic_message = "Data vertex does not exist in graph";
            return eval;
        }
        if (seen_data[d_v]) {
            eval.is_valid = false;
            eval.diagnostic_message = "Mapping is non-injective";
            return eval;
        }
        seen_data[d_v] = true;
    }

    // Check vertex labels
    for (uint32_t u = 0; u < num_q; ++u) {
        if (query.get_vertex_label(u) != data.get_vertex_label(mapping_by_query_order[u])) {
            eval.is_valid = false;
            eval.diagnostic_message = "Vertex label mismatch";
            return eval;
        }
    }

    // Count matched and missing edges
    eval.matched_edges = 0;
    eval.missing_edges = 0;
    for (uint32_t u = 0; u < num_q; ++u) {
        for (uint32_t v : query.get_neighbors(u)) {
            if (u < v) {
                auto q_edge = query.get_edge_label(u, v);
                auto d_edge = data.get_edge_label(mapping_by_query_order[u], mapping_by_query_order[v]);
                if (d_edge.exists && d_edge.label == q_edge.label) {
                    eval.matched_edges++;
                } else {
                    eval.missing_edges++;
                }
            }
        }
    }

    // Evaluate APX condition: missing_edges <= k and missing_edges <= lambda
    if (eval.missing_edges <= k && eval.missing_edges <= lambda) {
        eval.is_valid = true;
        eval.diagnostic_message = "Valid approximate embedding without exact NLF interference";
    } else {
        eval.is_valid = false;
        eval.diagnostic_message = "Missing edges exceed threshold k or lambda";
    }

    return eval;
}

} // namespace rapidflow_apx
