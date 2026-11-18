#include "rapidflow_apx/micro_matcher.h"
#include <algorithm>
#include <queue>
#include <limits>

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
        uint32_t pivot_nbr_q = 0;
        for (uint32_t nbr : query.get_neighbors(w_q)) {
            if (query_mapped[nbr]) {
                pivot_nbr_q = nbr;
                break;
            }
        }
        uint32_t pivot_data_v = mapping[pivot_nbr_q];
        auto pivot_q_edge = query.get_edge_label(w_q, pivot_nbr_q);

        for (uint32_t cand_d : data.get_neighbors(pivot_data_v)) {
            if (data_used[cand_d]) continue;
            if (data.get_vertex_label(cand_d) != w_q_label) continue;

            auto d_edge = data.get_edge_label(cand_d, pivot_data_v);
            if (!d_edge.exists || d_edge.label != pivot_q_edge.label) continue;

            bool compat = true;
            for (uint32_t other_q : query.get_neighbors(w_q)) {
                if (query_mapped[other_q] && other_q != pivot_nbr_q) {
                    auto exp_e = query.get_edge_label(w_q, other_q);
                    auto act_e = data.get_edge_label(cand_d, mapping[other_q]);
                    if (!act_e.exists || act_e.label != exp_e.label) {
                        compat = false;
                        break;
                    }
                }
            }
            if (!compat) continue;

            mapping[w_q] = cand_d;
            query_mapped[w_q] = true;
            data_used[cand_d] = true;

            dfs_snapshot(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);

            query_mapped[w_q] = false;
            data_used[cand_d] = false;
        }
    } else {
        for (uint32_t cand_d = 0; cand_d < num_d; ++cand_d) {
            if (data_used[cand_d]) continue;
            if (data.get_vertex_label(cand_d) != w_q_label) continue;

            mapping[w_q] = cand_d;
            query_mapped[w_q] = true;
            data_used[cand_d] = true;

            dfs_snapshot(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);

            query_mapped[w_q] = false;
            data_used[cand_d] = false;
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
    if (num_q == 0 || num_d < num_q) {
        return 0;
    }

    uint64_t total_matches = 0;

    for (uint32_t q_u = 0; q_u < num_q; ++q_u) {
        for (uint32_t q_v : query.get_neighbors(q_u)) {
            if (q_u >= q_v) continue;
            auto q_edge = query.get_edge_label(q_u, q_v);
            if (q_edge.label != dense_label) continue;

            // Direction 1: q_u -> dense_u, q_v -> dense_v
            if (query.get_vertex_label(q_u) == data.get_vertex_label(dense_u) &&
                query.get_vertex_label(q_v) == data.get_vertex_label(dense_v)) {
                std::vector<uint32_t> mapping(num_q, 0);
                std::vector<bool> query_mapped(num_q, false);
                std::vector<bool> data_used(num_d, false);

                mapping[q_u] = dense_u;
                query_mapped[q_u] = true;
                data_used[dense_u] = true;

                mapping[q_v] = dense_v;
                query_mapped[q_v] = true;
                data_used[dense_v] = true;

                dfs_delta(query, data, mapping, query_mapped, data_used, 2, sink, total_matches);
            }

            // Direction 2: q_u -> dense_v, q_v -> dense_u
            if (query.get_vertex_label(q_u) == data.get_vertex_label(dense_v) &&
                query.get_vertex_label(q_v) == data.get_vertex_label(dense_u)) {
                std::vector<uint32_t> mapping(num_q, 0);
                std::vector<bool> query_mapped(num_q, false);
                std::vector<bool> data_used(num_d, false);

                mapping[q_u] = dense_v;
                query_mapped[q_u] = true;
                data_used[dense_v] = true;

                mapping[q_v] = dense_u;
                query_mapped[q_v] = true;
                data_used[dense_u] = true;

                dfs_delta(query, data, mapping, query_mapped, data_used, 2, sink, total_matches);
            }
        }
    }

    return total_matches;
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
            try {
                sink.emit_mapping(mapping);
                match_count++;
            } catch (const DuplicateEmissionException&) {
                // Ignore duplicates across symmetric query edge assignments in same step
            }
        }
        return;
    }

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
        uint32_t pivot_data_v = mapping[pivot_nbr_q];
        auto pivot_q_edge = query.get_edge_label(w_q, pivot_nbr_q);

        for (uint32_t cand_d : data.get_neighbors(pivot_data_v)) {
            if (data_used[cand_d]) continue;
            if (data.get_vertex_label(cand_d) != w_q_label) continue;

            auto d_edge = data.get_edge_label(cand_d, pivot_data_v);
            if (!d_edge.exists || d_edge.label != pivot_q_edge.label) continue;

            bool compat = true;
            for (uint32_t other_q : query.get_neighbors(w_q)) {
                if (query_mapped[other_q] && other_q != pivot_nbr_q) {
                    auto exp_e = query.get_edge_label(w_q, other_q);
                    auto act_e = data.get_edge_label(cand_d, mapping[other_q]);
                    if (!act_e.exists || act_e.label != exp_e.label) {
                        compat = false;
                        break;
                    }
                }
            }
            if (!compat) continue;

            mapping[w_q] = cand_d;
            query_mapped[w_q] = true;
            data_used[cand_d] = true;

            dfs_delta(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);

            query_mapped[w_q] = false;
            data_used[cand_d] = false;
        }
    } else {
        for (uint32_t cand_d = 0; cand_d < num_d; ++cand_d) {
            if (data_used[cand_d]) continue;
            if (data.get_vertex_label(cand_d) != w_q_label) continue;

            mapping[w_q] = cand_d;
            query_mapped[w_q] = true;
            data_used[cand_d] = true;

            dfs_delta(query, data, mapping, query_mapped, data_used, mapped_count + 1, sink, match_count);

            query_mapped[w_q] = false;
            data_used[cand_d] = false;
        }
    }
}

uint32_t MicroExactMatcher::compute_edge_connectivity(const ApxAuthoritativeGraph& query) {
    uint32_t n = query.get_vertices_count();
    if (n <= 1) return 0;
    if (n == 2) {
        return query.has_edge(0, 1) ? 1 : 0;
    }

    uint32_t min_deg = std::numeric_limits<uint32_t>::max();
    for (uint32_t u = 0; u < n; ++u) {
        min_deg = std::min(min_deg, query.get_degree(u));
    }
    if (min_deg == 0) return 0;

    struct Edge {
        uint32_t to;
        int cap;
        int rev;
    };

    auto max_flow_st = [&](uint32_t s, uint32_t t) -> uint32_t {
        std::vector<std::vector<Edge>> adj(n);
        auto add_undirected = [&](uint32_t u, uint32_t v) {
            Edge a{v, 1, static_cast<int>(adj[v].size())};
            Edge b{u, 1, static_cast<int>(adj[u].size())};
            adj[u].push_back(a);
            adj[v].push_back(b);
        };

        for (uint32_t u = 0; u < n; ++u) {
            for (uint32_t v : query.get_neighbors(u)) {
                if (u < v) {
                    add_undirected(u, v);
                }
            }
        }

        uint32_t flow = 0;
        while (true) {
            std::vector<int> parent_v(n, -1);
            std::vector<int> parent_e(n, -1);
            std::vector<uint32_t> q;
            q.reserve(n);
            q.push_back(s);
            parent_v[s] = static_cast<int>(s);

            size_t head = 0;
            while (head < q.size()) {
                uint32_t curr = q[head++];
                if (curr == t) break;
                for (size_t i = 0; i < adj[curr].size(); ++i) {
                    const auto& e = adj[curr][i];
                    if (e.cap > 0 && parent_v[e.to] == -1) {
                        parent_v[e.to] = static_cast<int>(curr);
                        parent_e[e.to] = static_cast<int>(i);
                        q.push_back(e.to);
                    }
                }
            }

            if (parent_v[t] == -1) break;

            uint32_t curr = t;
            while (curr != s) {
                uint32_t prev = static_cast<uint32_t>(parent_v[curr]);
                int e_idx = parent_e[curr];
                adj[prev][e_idx].cap -= 1;
                int rev_idx = adj[prev][e_idx].rev;
                adj[curr][rev_idx].cap += 1;
                curr = prev;
            }
            flow += 1;
        }
        return flow;
    };

    uint32_t global_min_cut = min_deg;
    for (uint32_t t = 1; t < n; ++t) {
        uint32_t cut = max_flow_st(0, t);
        global_min_cut = std::min(global_min_cut, cut);
        if (global_min_cut == 0) return 0;
    }
    return global_min_cut;
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

    // 1. Verify and compute edge connectivity lambda(Q)
    eval.computed_lambda = compute_edge_connectivity(query);
    if (lambda != eval.computed_lambda) {
        eval.is_valid = false;
        eval.diagnostic_message = "Metadata lambda mismatch: claimed " + std::to_string(lambda) +
                                  " but computed " + std::to_string(eval.computed_lambda);
        return eval;
    }

    // 2. Strict D-R Guard: 0 <= k < lambda(Q)
    if (k >= eval.computed_lambda) {
        eval.is_valid = false;
        eval.diagnostic_message = "D-R guard violated: threshold k=" + std::to_string(k) +
                                  " must be strictly less than lambda(Q)=" + std::to_string(eval.computed_lambda);
        return eval;
    }

    // 3. Check injectivity of mapping
    std::vector<bool> seen_data(data.get_vertices_count(), false);
    for (uint32_t d_v : mapping_by_query_order) {
        if (!data.has_vertex(d_v)) {
            eval.is_valid = false;
            eval.diagnostic_message = "Data vertex does not exist in graph: " + std::to_string(d_v);
            return eval;
        }
        if (seen_data[d_v]) {
            eval.is_valid = false;
            eval.diagnostic_message = "Mapping is non-injective: duplicate data vertex " + std::to_string(d_v);
            return eval;
        }
        seen_data[d_v] = true;
    }

    // 4. Check vertex labels
    for (uint32_t u = 0; u < num_q; ++u) {
        if (query.get_vertex_label(u) != data.get_vertex_label(mapping_by_query_order[u])) {
            eval.is_valid = false;
            eval.diagnostic_message = "Vertex label mismatch at query vertex " + std::to_string(u);
            return eval;
        }
    }

    // 5. Count matched and missing edges, collect matched edge graph
    eval.matched_edges = 0;
    eval.missing_edges = 0;
    std::vector<std::vector<uint32_t>> matched_adj(num_q);

    for (uint32_t u = 0; u < num_q; ++u) {
        for (uint32_t v : query.get_neighbors(u)) {
            if (u < v) {
                auto q_edge = query.get_edge_label(u, v);
                auto d_edge = data.get_edge_label(mapping_by_query_order[u], mapping_by_query_order[v]);
                if (d_edge.exists && d_edge.label == q_edge.label) {
                    eval.matched_edges++;
                    matched_adj[u].push_back(v);
                    matched_adj[v].push_back(u);
                } else {
                    eval.missing_edges++;
                }
            }
        }
    }

    // 6. Check missing edges against threshold k
    if (eval.missing_edges > k) {
        eval.is_valid = false;
        eval.diagnostic_message = "Missing edges " + std::to_string(eval.missing_edges) +
                                  " exceeds threshold k=" + std::to_string(k);
        return eval;
    }

    // 7. S2 Spanning Connectivity on matched query edges
    eval.s2_spanning_connected = false;
    if (num_q > 0) {
        std::vector<bool> visited(num_q, false);
        std::vector<uint32_t> bfs_q;
        bfs_q.reserve(num_q);
        bfs_q.push_back(0);
        visited[0] = true;

        size_t head = 0;
        while (head < bfs_q.size()) {
            uint32_t curr = bfs_q[head++];
            for (uint32_t nbr : matched_adj[curr]) {
                if (!visited[nbr]) {
                    visited[nbr] = true;
                    bfs_q.push_back(nbr);
                }
            }
        }

        if (bfs_q.size() == num_q) {
            eval.s2_spanning_connected = true;
        }
    }

    if (!eval.s2_spanning_connected) {
        eval.is_valid = false;
        eval.diagnostic_message = "S2 spanning connectivity violated: matched edges do not form a spanning connected subgraph of Q";
        return eval;
    }

    // All conditions satisfied
    eval.is_valid = true;
    eval.diagnostic_message = "Valid approximate embedding satisfying D-R guard (k=" + std::to_string(k) +
                              " < lambda=" + std::to_string(eval.computed_lambda) + ") and S2 spanning connectivity";
    return eval;
}

} // namespace rapidflow_apx
