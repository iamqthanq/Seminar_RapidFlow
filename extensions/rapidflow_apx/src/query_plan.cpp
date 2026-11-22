#include "rapidflow_apx/query_plan.h"
#include <queue>
#include <limits>
#include <stdexcept>
#include <functional>
#include <algorithm>

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
    canonical_order_ = canonical_order;

    // Canonicalize and sort query edges deterministically (P3-R2-F11)
    std::vector<uint32_t> vertex_rank(num_vertices, 0);
    for (uint32_t r = 0; r < canonical_order.size(); ++r) {
        if (canonical_order[r] < num_vertices) {
            vertex_rank[canonical_order[r]] = r;
        }
    }

    std::vector<QueryEdge> canonical_edges;
    canonical_edges.reserve(edges.size());
    for (const auto& e : edges) {
        QueryEdge ce = e;
        if (vertex_rank[ce.u] > vertex_rank[ce.v]) {
            std::swap(ce.u, ce.v);
        }
        canonical_edges.push_back(ce);
    }

    std::sort(canonical_edges.begin(), canonical_edges.end(),
        [&](const QueryEdge& a, const QueryEdge& b) {
            uint32_t ra_u = vertex_rank[a.u];
            uint32_t ra_v = vertex_rank[a.v];
            uint32_t rb_u = vertex_rank[b.u];
            uint32_t rb_v = vertex_rank[b.v];
            if (ra_u != rb_u) return ra_u < rb_u;
            if (ra_v != rb_v) return ra_v < rb_v;
            return a.label < b.label;
        }
    );

    edges_ = std::move(canonical_edges);

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

    for (uint32_t u = 0; u < num_vertices; ++u) {
        std::sort(vertex_incident_edges_[u].begin(), vertex_incident_edges_[u].end(),
            [&](const std::pair<uint32_t, uint32_t>& a, const std::pair<uint32_t, uint32_t>& b) {
                return vertex_rank[a.first] < vertex_rank[b.first];
            }
        );
    }
}

void ApxQueryPlan::for_each_m1_variant(
    uint32_t anchor_idx,
    uint32_t qa,
    uint32_t qb,
    EngineCounters& counters,
    const std::function<void(const M1VariantPlan&)>& callback
) const {
    // 1. Upfront charge for variant enumeration setup on this anchor edge
    counters.charge_work(1);

    std::vector<uint32_t> other_edges;
    other_edges.reserve(edges_.size() - 1);
    for (size_t i = 0; i < edges_.size(); ++i) {
        if (i != anchor_idx) {
            other_edges.push_back(static_cast<uint32_t>(i));
        }
    }

    std::vector<uint32_t> cur_subset;
    cur_subset.reserve(k_);

    auto process_subset = [&](const std::vector<uint32_t>& D) {
        // 2. Charge budget for constructing and materializing variant plan Q - D
        counters.charge_work(1);

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
        counters.variants_started++;
        callback(plan);
    };

    std::function<void(size_t, size_t)> recurse = [&](size_t start, size_t remaining_k) {
        if (remaining_k == 0) {
            process_subset(cur_subset);
            return;
        }
        size_t available = other_edges.size() - start;
        if (available < remaining_k) {
            return;
        }
        for (size_t i = start; i <= other_edges.size() - remaining_k; ++i) {
            // Charge budget for exploring combination branch
            counters.charge_work(1);
            cur_subset.push_back(other_edges[i]);
            recurse(i + 1, remaining_k - 1);
            cur_subset.pop_back();
        }
    };

    recurse(0, k_);
}

} // namespace rapidflow_apx
