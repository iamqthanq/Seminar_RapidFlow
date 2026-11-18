#include "rapidflow_apx/query_plan.h"
#include <queue>
#include <limits>
#include <stdexcept>
#include <functional>

namespace rapidflow_apx {

uint32_t ApxQueryPlan::compute_edge_connectivity(uint32_t num_vertices, const std::vector<QueryEdge>& edges) {
    if (num_vertices <= 1) return 0;
    if (num_vertices == 2) {
        return edges.empty() ? 0 : 1;
    }

    // Compute vertex degrees
    std::vector<uint32_t> deg(num_vertices, 0);
    for (const auto& e : edges) {
        if (e.u < num_vertices && e.v < num_vertices) {
            deg[e.u]++;
            deg[e.v]++;
        }
    }

    uint32_t min_deg = std::numeric_limits<uint32_t>::max();
    for (uint32_t d : deg) {
        min_deg = std::min(min_deg, d);
    }
    if (min_deg == 0) return 0;

    struct FlowEdge {
        uint32_t to;
        int cap;
        int rev;
    };

    auto max_flow_st = [&](uint32_t s, uint32_t t) -> uint32_t {
        std::vector<std::vector<FlowEdge>> adj(num_vertices);
        for (const auto& e : edges) {
            FlowEdge a{e.v, 1, static_cast<int>(adj[e.v].size())};
            FlowEdge b{e.u, 1, static_cast<int>(adj[e.u].size())};
            adj[e.u].push_back(a);
            adj[e.v].push_back(b);
        }

        uint32_t total_flow = 0;
        while (true) {
            std::vector<int> parent_v(num_vertices, -1);
            std::vector<int> parent_e(num_vertices, -1);
            std::queue<uint32_t> q;

            parent_v[s] = static_cast<int>(s);
            q.push(s);

            while (!q.empty() && parent_v[t] == -1) {
                uint32_t curr = q.front();
                q.pop();

                for (size_t i = 0; i < adj[curr].size(); ++i) {
                    const auto& fe = adj[curr][i];
                    if (parent_v[fe.to] == -1 && fe.cap > 0) {
                        parent_v[fe.to] = static_cast<int>(curr);
                        parent_e[fe.to] = static_cast<int>(i);
                        q.push(fe.to);
                        if (fe.to == t) break;
                    }
                }
            }

            if (parent_v[t] == -1) break;

            int push_flow = 1;
            uint32_t curr = t;
            while (curr != s) {
                uint32_t p = static_cast<uint32_t>(parent_v[curr]);
                int edge_idx = parent_e[curr];
                adj[p][edge_idx].cap -= push_flow;
                int rev_idx = adj[p][edge_idx].rev;
                adj[curr][rev_idx].cap += push_flow;
                curr = p;
            }
            total_flow += push_flow;
        }
        return total_flow;
    };

    uint32_t lambda = min_deg;
    for (uint32_t t = 1; t < num_vertices; ++t) {
        uint32_t flow = max_flow_st(0, t);
        lambda = std::min(lambda, flow);
        if (lambda == 0) break;
    }
    return lambda;
}

void ApxQueryPlan::build(
    uint32_t num_vertices,
    const std::vector<uint32_t>& vertex_labels,
    const std::vector<QueryEdge>& edges,
    const std::vector<uint32_t>& canonical_order,
    uint32_t k
) {
    if (num_vertices < 2) {
        throw InvalidQueryException("Query graph must contain at least 2 vertices: got " + std::to_string(num_vertices));
    }
    if (vertex_labels.size() != num_vertices) {
        throw InvalidQueryException("Query vertex label count mismatch");
    }

    // Validate simple graph
    std::unordered_set<uint64_t> seen_edges;
    for (const auto& e : edges) {
        if (e.u >= num_vertices || e.v >= num_vertices) {
            throw InvalidQueryException("Query edge endpoint out of range");
        }
        if (e.u == e.v) {
            throw InvalidQueryException("Query graph cannot contain self-loops: (" + std::to_string(e.u) + ", " + std::to_string(e.v) + ")");
        }
        uint32_t mn = std::min(e.u, e.v);
        uint32_t mx = std::max(e.u, e.v);
        uint64_t key = (static_cast<uint64_t>(mn) << 32) | static_cast<uint64_t>(mx);
        if (seen_edges.find(key) != seen_edges.end()) {
            throw InvalidQueryException("Query graph cannot contain duplicate edges");
        }
        seen_edges.insert(key);
    }

    uint32_t computed_lambda = compute_edge_connectivity(num_vertices, edges);
    if (computed_lambda == 0) {
        throw InvalidQueryException("Query graph must be connected: computed lambda(Q) is 0");
    }

    // Enforce D-R contract: 0 <= k < lambda(Q)
    if (k >= computed_lambda) {
        throw InvalidThresholdException(
            "D-R contract violated: threshold k=" + std::to_string(k) +
            " must be strictly less than lambda(Q)=" + std::to_string(computed_lambda)
        );
    }

    num_vertices_ = num_vertices;
    k_ = k;
    lambda_ = computed_lambda;
    vertex_labels_ = vertex_labels;
    edges_ = edges;
    canonical_order_ = canonical_order;

    relevant_edge_labels_.clear();
    label_to_edges_.clear();
    vertex_incident_edges_.assign(num_vertices, {});

    for (size_t i = 0; i < edges_.size(); ++i) {
        const auto& e = edges_[i];
        relevant_edge_labels_.insert(e.label);
        label_to_edges_[e.label].push_back(static_cast<uint32_t>(i));
        vertex_incident_edges_[e.u].emplace_back(e.v, static_cast<uint32_t>(i));
        vertex_incident_edges_[e.v].emplace_back(e.u, static_cast<uint32_t>(i));
    }
}

static void combine_subsets(
    const std::vector<uint32_t>& items,
    size_t k,
    size_t start,
    std::vector<uint32_t>& current,
    std::vector<std::vector<uint32_t>>& result
) {
    if (current.size() == k) {
        result.push_back(current);
        return;
    }
    for (size_t i = start; i < items.size(); ++i) {
        current.push_back(items[i]);
        combine_subsets(items, k, i + 1, current, result);
        current.pop_back();
    }
}

std::vector<M1VariantPlan> ApxQueryPlan::generate_m1_variants(
    uint32_t anchor_idx,
    uint32_t qa,
    uint32_t qb
) const {
    std::vector<uint32_t> other_edges;
    other_edges.reserve(edges_.size() - 1);
    for (size_t i = 0; i < edges_.size(); ++i) {
        if (i != anchor_idx) {
            other_edges.push_back(static_cast<uint32_t>(i));
        }
    }

    std::vector<std::vector<uint32_t>> missing_subsets;
    std::vector<uint32_t> cur_subset;
    combine_subsets(other_edges, k_, 0, cur_subset, missing_subsets);

    std::vector<M1VariantPlan> plans;
    plans.reserve(missing_subsets.size());

    for (const auto& D : missing_subsets) {
        M1VariantPlan plan;
        plan.anchor_edge_idx = anchor_idx;
        plan.anchor_qa = qa;
        plan.anchor_qb = qb;
        plan.missing_edges = D;

        std::unordered_set<uint32_t> missing_set(D.begin(), D.end());

        // Build adjacency of kept edges
        std::vector<std::vector<uint32_t>> adj_keep(num_vertices_);
        plan.kept_incident.assign(num_vertices_, {});

        for (size_t i = 0; i < edges_.size(); ++i) {
            if (missing_set.find(static_cast<uint32_t>(i)) == missing_set.end()) {
                const auto& e = edges_[i];
                adj_keep[e.u].push_back(e.v);
                adj_keep[e.v].push_back(e.u);
                plan.kept_incident[e.u].emplace_back(e.v, e.label);
                plan.kept_incident[e.v].emplace_back(e.u, e.label);
            }
        }

        // Build deterministic connected search order starting with [qa, qb]
        std::vector<uint32_t> order;
        order.reserve(num_vertices_);
        order.push_back(qa);
        order.push_back(qb);
        std::vector<bool> in_order(num_vertices_, false);
        in_order[qa] = true;
        in_order[qb] = true;

        while (order.size() < num_vertices_) {
            int best_cand = -1;
            for (uint32_t v = 0; v < num_vertices_; ++v) {
                if (!in_order[v]) {
                    bool has_nbr = false;
                    for (uint32_t nbr : adj_keep[v]) {
                        if (in_order[nbr]) {
                            has_nbr = true;
                            break;
                        }
                    }
                    if (has_nbr) {
                        if (best_cand == -1 || v < static_cast<uint32_t>(best_cand)) {
                            best_cand = static_cast<int>(v);
                        }
                    }
                }
            }
            if (best_cand == -1) {
                throw InvariantViolationException(
                    "M1 variant Q - D is disconnected despite D-R condition k < lambda(Q)"
                );
            }
            order.push_back(static_cast<uint32_t>(best_cand));
            in_order[best_cand] = true;
        }

        plan.search_order = std::move(order);
        plans.push_back(std::move(plan));
    }

    return plans;
}

} // namespace rapidflow_apx
