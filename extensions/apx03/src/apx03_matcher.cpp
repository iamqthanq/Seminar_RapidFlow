#include "apx03_matcher.h"
#include <algorithm>
#include <numeric>

namespace apx03 {

namespace {

class DisjointSetUnion {
public:
    explicit DisjointSetUnion(size_t n) : parent_(n), rank_(n, 0), components_(n) {
        std::iota(parent_.begin(), parent_.end(), 0);
    }

    size_t find(size_t i) {
        if (parent_[i] == i)
            return i;
        return parent_[i] = find(parent_[i]);
    }

    bool unite(size_t i, size_t j) {
        size_t root_i = find(i);
        size_t root_j = find(j);
        if (root_i != root_j) {
            if (rank_[root_i] < rank_[root_j]) {
                parent_[root_i] = root_j;
            } else if (rank_[root_i] > rank_[root_j]) {
                parent_[root_j] = root_i;
            } else {
                parent_[root_j] = root_i;
                rank_[root_i]++;
            }
            components_--;
            return true;
        }
        return false;
    }

    size_t count_components() const {
        return components_;
    }

private:
    std::vector<size_t> parent_;
    std::vector<size_t> rank_;
    size_t components_;
};

} // anonymous namespace

ApxMatcher::ApxMatcher(
    const ApxGraph& Q,
    const ApxGraph& G,
    uint32_t eps_m,
    bool eps_x_unbounded,
    uint32_t eps_x,
    ConnectivityMode mode,
    uint64_t max_candidates,
    uint64_t max_search_steps
) : Q_(Q),
    G_(G),
    eps_m_(eps_m),
    eps_x_unbounded_(eps_x_unbounded),
    eps_x_(eps_x),
    mode_(mode),
    max_candidates_(max_candidates),
    max_search_steps_(max_search_steps),
    candidates_evaluated_(0),
    search_steps_(0)
{
    query_vertices_ = Q_.get_all_vertices();
    std::sort(query_vertices_.begin(), query_vertices_.end());
    data_vertices_ = G_.get_all_vertices();
    std::sort(data_vertices_.begin(), data_vertices_.end());
}

bool ApxMatcher::check_connectivity_and_score(
    const std::vector<uint32_t>& mapping,
    DeviationScore& score
) const {
    size_t k = query_vertices_.size();
    score.m = 0;
    score.x_in = 0;
    score.x_out = 0;

    // 1. Missing query edges m(f)
    for (const auto& q_edge : Q_.get_all_edges()) {
        uint32_t u_idx = 0;
        uint32_t v_idx = 0;
        for (size_t i = 0; i < k; ++i) {
            if (query_vertices_[i] == q_edge.first) u_idx = static_cast<uint32_t>(i);
            if (query_vertices_[i] == q_edge.second) v_idx = static_cast<uint32_t>(i);
        }
        uint32_t data_u = mapping[u_idx];
        uint32_t data_v = mapping[v_idx];
        uint32_t q_label = Q_.get_edge_label(q_edge.first, q_edge.second);

        if (!G_.has_edge(data_u, data_v) || G_.get_edge_label(data_u, data_v) != q_label) {
            score.m++;
        }
    }

    // 2. Internal chords x_in(f)
    for (size_t i = 0; i < k; ++i) {
        for (size_t j = i + 1; j < k; ++j) {
            uint32_t qu = query_vertices_[i];
            uint32_t qv = query_vertices_[j];
            if (!Q_.has_edge(qu, qv)) {
                if (G_.has_edge(mapping[i], mapping[j])) {
                    score.x_in++;
                }
            }
        }
    }

    // 3. External boundary cut x_out(f)
    std::unordered_set<uint32_t> s_f(mapping.begin(), mapping.end());
    for (uint32_t u : mapping) {
        const auto& neighbors = G_.get_neighbors(u);
        for (const auto& edge_pair : neighbors) {
            uint32_t v = edge_pair.first;
            if (s_f.find(v) == s_f.end()) {
                score.x_out++;
            }
        }
    }

    // 4. Topological connectivity of G[S_f]
    DisjointSetUnion dsu(k);
    for (size_t i = 0; i < k; ++i) {
        for (size_t j = i + 1; j < k; ++j) {
            if (G_.has_edge(mapping[i], mapping[j])) {
                dsu.unite(i, j);
            }
        }
    }
    bool is_connected = (k <= 1) || (dsu.count_components() == 1);
    score.connected = is_connected;

    // 5. Acceptance determination
    bool conn_ok = (mode_ == ConnectivityMode::STRICT) ? is_connected : true;
    bool threshold_ok = (score.m <= eps_m_) && (eps_x_unbounded_ || score.x_out <= eps_x_);
    return threshold_ok && conn_ok;
}

EvaluationResult ApxMatcher::evaluate_single_mapping(const std::vector<uint32_t>& mapping) const {
    size_t k = query_vertices_.size();
    EvaluationResult res;
    res.mapping = mapping;
    res.accepted = false;

    if (mapping.size() != k) {
        return res;
    }

    // Injective check
    std::unordered_set<uint32_t> unique_nodes(mapping.begin(), mapping.end());
    if (unique_nodes.size() != k) {
        res.accepted = false;
        return res;
    }

    // Hard vertex label check
    for (size_t i = 0; i < k; ++i) {
        if (!G_.has_vertex(mapping[i])) {
            return res;
        }
        if (G_.get_vertex_label(mapping[i]) != Q_.get_vertex_label(query_vertices_[i])) {
            return res;
        }
    }

    // Calculate metrics and acceptance
    res.accepted = check_connectivity_and_score(mapping, res.score);
    return res;
}

std::map<std::vector<uint32_t>, EvaluationResult> ApxMatcher::find_all_matches() {
    std::map<std::vector<uint32_t>, EvaluationResult> matches;
    size_t k = query_vertices_.size();
    if (k == 0) {
        return matches;
    }

    std::vector<uint32_t> current_mapping(k, 0);
    std::unordered_set<uint32_t> used_data_nodes;
    candidates_evaluated_ = 0;
    search_steps_ = 0;

    backtrack(0, current_mapping, used_data_nodes, 0, matches);
    return matches;
}

void ApxMatcher::backtrack(
    size_t depth,
    std::vector<uint32_t>& current_mapping,
    std::unordered_set<uint32_t>& used_data_nodes,
    uint32_t current_m_partial,
    std::map<std::vector<uint32_t>, EvaluationResult>& matches
) {
    search_steps_++;
    if (search_steps_ > max_search_steps_) {
        throw ResourceLimitExceededException(
            "Search step budget exceeded limit (" + std::to_string(max_search_steps_) + ")"
        );
    }

    size_t k = query_vertices_.size();
    if (depth == k) {
        candidates_evaluated_++;
        if (candidates_evaluated_ > max_candidates_) {
            throw ResourceLimitExceededException(
                "Candidate exploration budget exceeded limit (" + std::to_string(max_candidates_) + ")"
            );
        }

        DeviationScore score;
        bool accepted = check_connectivity_and_score(current_mapping, score);
        if (accepted) {
            matches[current_mapping] = {current_mapping, score, accepted};
        }
        return;
    }

    uint32_t q_vertex = query_vertices_[depth];
    uint32_t q_label = Q_.get_vertex_label(q_vertex);

    for (uint32_t data_v : data_vertices_) {
        // Injective check
        if (used_data_nodes.find(data_v) != used_data_nodes.end()) {
            continue;
        }

        // Hard vertex label check
        if (G_.get_vertex_label(data_v) != q_label) {
            continue;
        }

        // Admissible early prune on missing query edges between already-assigned query vertices
        uint32_t violations_added = 0;
        for (size_t prev = 0; prev < depth; ++prev) {
            uint32_t prev_q_v = query_vertices_[prev];
            if (Q_.has_edge(prev_q_v, q_vertex)) {
                uint32_t q_edge_label = Q_.get_edge_label(prev_q_v, q_vertex);
                uint32_t prev_data_v = current_mapping[prev];
                if (!G_.has_edge(prev_data_v, data_v) || G_.get_edge_label(prev_data_v, data_v) != q_edge_label) {
                    violations_added++;
                }
            }
        }

        if (current_m_partial + violations_added > eps_m_) {
            continue; // Safe prune: partial m already exceeds allowed eps_m
        }

        // Assign and recurse
        current_mapping[depth] = data_v;
        used_data_nodes.insert(data_v);

        backtrack(depth + 1, current_mapping, used_data_nodes, current_m_partial + violations_added, matches);

        used_data_nodes.erase(data_v);
    }
}

} // namespace apx03
