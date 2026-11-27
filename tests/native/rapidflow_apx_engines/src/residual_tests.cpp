#include "p4b_tests.h"
#include "rapidflow_apx/residual_nlf_state.h"
#include "rapidflow_apx/streaming_engine.h"

#include <algorithm>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <random>
#include <stdexcept>

namespace rapidflow_apx {
namespace {

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template <typename F>
void expect_invariant(F action) {
    try {
        action();
    } catch (const ResidualInvariantViolation&) {
        return;
    }
    throw std::runtime_error("Expected residual invariant violation");
}

void numeric() {
    using State = ApxResidualMissingState;
    ApxQueryPlan plan;
    // Edge 0-1 is canonical ID 0. Its first endpoint shares its signature
    // with 0-2; its second has capacity one for this signature.
    plan.build(4, {0, 0, 0, 1}, {{3, 2, 1}, {2, 1, 1}, {3, 0, 1}, {2, 0, 0}, {1, 0, 0}},
               {0, 1, 2, 3}, 1);
    ApxQuerySignatures sigs;
    sigs.build(plan);
    const auto& edge = plan.get_edge(0);
    require(edge.u == 0 && edge.v == 1, "Canonical edge order");
    require(sigs.get_vertex_requirements(0).size() == 2, "Multiple signatures per vertex");
    State state(plan, sigs);
    require(state.count(0, 0) == 0 && state.count(1, 0) == 0, "Initial counts");
    state.commit_missing(0);
    require(state.count(0, 0) == 1 && state.count(1, 0) == 1 && state.count(0, 1) == 0, "End slots");
    expect_invariant([&] { state.commit_missing(0); });
    require(state.count(0, 0) == 1 && state.count(1, 0) == 1, "Second capacity failure changes neither slot");
    state.commit_missing(1);
    require(state.count(0, 0) == 2 && state.count(2, 0) == 1, "Shared vertex slot");
    state.undo_missing(1);
    state.undo_missing(0);
    require(state.count(0, 0) == 0 && state.count(1, 0) == 0 && state.count(2, 0) == 0, "Reverse undo");
    expect_invariant([&] { state.undo_missing(0); });
    expect_invariant([&] { state.commit_missing(plan.get_edges_count()); });
    expect_invariant([&] { state.count(0, 2); });
    expect_invariant([&] { State::remaining_requirement(1, 2); });
    const ApxQuerySignatures empty;
    expect_invariant([&] { State missing_registry(plan, empty); });
    require(State::remaining_requirement(3, 1) == 2, "Deduct only incident missing");
    require(State::remaining_requirement(3, 3) == 0, "Zero residual demand");
    require(State::remaining_requirement(std::numeric_limits<uint32_t>::max(), 1) ==
            std::numeric_limits<uint32_t>::max() - 1, "Large requirement arithmetic");

    ApxAuthoritativeGraph graph;
    graph.load_baseline({{0,0},{1,0},{2,0},{3,1}}, {{0,1,0},{0,2,0},{0,3,1}});
    ApxNlfIndex index;
    index.initialize(graph, sigs);
    EngineCounters cnt;
    // A3: both compatible data neighbors contribute even if already used.
    require(state.allows(sigs, index, 0, 0, 0, 0, cnt), "Full authoritative neighbor capacity");
    const auto first = sigs.get_vertex_requirements(0)[0].sig_id;
    const auto second = sigs.get_vertex_requirements(0)[1].sig_id;
    index.set_count_for_testing(0, first, 0);
    index.set_count_for_testing(0, second, 0);
    require(state.allows(sigs, index, 0, 0, 3, 0, cnt), "Multi-signature equality");
    require(!state.allows(sigs, index, 0, 0, 2, 0, cnt), "Multi-signature deficit exceeds by one");
    require(!state.allows(sigs, index, 0, 0, 0, 1, cnt), "Invalid remaining budget");
    state.commit_missing(0);
    index.set_count_for_testing(0, first, 1);
    index.set_count_for_testing(0, second, 1);
    require(state.allows(sigs, index, 0, 0, 1, 1, cnt), "Zero budget with no additional deficit");
    index.set_count_for_testing(0, first, 0);
    require(!state.allows(sigs, index, 0, 0, 1, 1, cnt), "One additional missing edge");
    index.set_count_for_testing(0, first, std::numeric_limits<uint32_t>::max());
    require(state.allows(sigs, index, 0, 0, 1, 1, cnt), "Large available count");
    state.commit_missing(1);
    const auto reads = cnt.nlf_residual_signature_lookups;
    require(state.allows(sigs, index, 0, 0, 2, 2, cnt), "Zero demand after two commits");
    require(cnt.nlf_residual_signature_lookups == reads + 1, "Zero demand skips frequency read");
    state.undo_missing(1);
    state.undo_missing(0);

    // A2 exact shape. Query vertex 1 requires three identical signatures.
    ApxQueryPlan diamond;
    diamond.build(4, {0,0,0,0}, {{0,1,0},{0,2,0},{1,2,0},{1,3,0},{2,3,0}}, {0,1,2,3}, 1);
    ApxQuerySignatures diamond_sigs;
    diamond_sigs.build(diamond);
    ApxAuthoritativeGraph cycle;
    cycle.load_baseline({{0,0},{1,0},{2,0},{3,0}}, {{0,1,0},{0,2,0},{1,3,0},{2,3,0}});
    ApxNlfIndex cycle_index;
    cycle_index.initialize(cycle, diamond_sigs);
    expect_invariant([&] { State incompatible_registry(plan, diamond_sigs); });
    State a2(diamond, diamond_sigs);
    a2.commit_missing(2);
    require(a2.count(1, 0) == 1 && cycle_index.count(1, 0) == 2, "A2 count shape");
    require(a2.allows(diamond_sigs, cycle_index, 1, 1, 1, 1, cnt), "A2 must keep");
    cycle_index.set_count_for_testing(1, 0, 1);
    require(!a2.allows(diamond_sigs, cycle_index, 1, 1, 1, 1, cnt), "A2 one additional deficit");
    a2.undo_missing(2);

    // Distinct endpoint signatures for an asymmetric edge are attached to
    // the canonical endpoints, even when raw input edges are reversed.
    State asymmetric(plan, sigs);
    const auto unequal = sigs.get_edge_signatures(2);
    require(unequal.from_u != unequal.from_v, "Asymmetric signatures");
    asymmetric.commit_missing(2);
    require(asymmetric.count(0, 1) == 1, "Canonical from_u slot");
    const auto& reqs = sigs.get_vertex_requirements(3);
    require(reqs.size() == 1 && reqs[0].sig_id == unequal.from_v && asymmetric.count(3, 0) == 1,
            "Canonical from_v slot");
    std::cout << "Residual numeric, slot and A2/A3 assertions passed\n";
}

#ifdef APX_RESIDUAL_TEST_TRACE
using Point = ResidualTracePoint;
constexpr uint32_t unassigned = ApxM2EventEngine::UNASSIGNED;

struct Parent {
    std::vector<uint32_t> mapping;
    std::vector<EdgeCommitment> status;
    uint32_t missing;
};

struct TraceCounts {
    size_t states = 0;
    size_t resolved = 0;
    size_t deferred = 0;
    size_t restored = 0;
    size_t anchors = 0;
    size_t rejected_after_missing = 0;
    size_t multi_missing_resolution = 0;
    size_t over_budget = 0;
    size_t nested = 0;
    size_t completions = 0;
};

void verify_completion_bound(
    const ResidualTrace& t, const ApxQueryPlan& plan, const ApxAuthoritativeGraph& graph,
    uint64_t bound, TraceCounts& counts
) {
    std::vector<uint32_t> completion(graph.get_vertices_count());
    std::iota(completion.begin(), completion.end(), 0);
    do {
        if (completion[t.candidate_query] != t.candidate_data) continue;
        bool compatible = true;
        for (uint32_t u = 0; u < t.mapping.size(); ++u) {
            compatible &= (t.mapping[u] == unassigned || t.mapping[u] == completion[u])
                && plan.get_vertex_label(u) == graph.get_vertex_label(completion[u]);
        }
        if (!compatible) continue;
        uint32_t missing = 0;
        for (uint32_t e = 0; e < t.status.size(); ++e) {
            const auto& edge = plan.get_edge(e);
            const auto found = graph.get_edge_label(completion[edge.u], completion[edge.v]);
            const bool present = found.exists && found.label == edge.label;
            compatible &= (t.status[e] != EdgeCommitment::MISSING || !present)
                && (t.status[e] != EdgeCommitment::PRESENT || present);
            missing += !present;
        }
        if (compatible) {
            ++counts.completions;
            require(bound <= missing, "Residual bound exceeds a compatible completion");
        }
    } while (std::next_permutation(completion.begin(), completion.end()));
}

void trace_workload(
    const std::string& name, const std::vector<QueryEdge>& qedges, std::vector<uint32_t> labels,
    const std::vector<std::tuple<uint32_t,uint32_t,uint32_t>>& data, uint32_t k,
    int expected_events, TraceCounts& counts, uint32_t extra_data_vertices = 0
) {
    ApxQueryPlan plan;
    std::vector<uint32_t> order(labels.size());
    std::iota(order.begin(), order.end(), 0);
    plan.build(labels.size(), labels, qedges, order, k);
    ApxAuthoritativeGraph graph;
    ApxTypedCodec codec;
    for (const auto label : {0,1}) {
        codec.intern_vertex_label(TypedLabel::from_int(label));
        codec.intern_edge_label(TypedLabel::from_int(label));
    }
    std::vector<std::pair<uint32_t,uint32_t>> vertices;
    for (const auto u : order) {
        codec.intern_data_vertex(u);
        vertices.emplace_back(u, labels[u]);
    }
    for (uint32_t u = order.size(); u < order.size() + extra_data_vertices; ++u) {
        codec.intern_data_vertex(u);
        vertices.emplace_back(u, 0);
    }
    graph.load_baseline(vertices, data);
    ApxTupleSink sink(codec, order);
    ApxStreamingEngine engine(graph, codec, sink, plan, EngineType::M2, 500000, NlfMode::RESIDUAL);
    const auto& sigs = engine.get_query_signatures();
    std::vector<Parent> candidates, deferred;
    uint32_t anchor = unassigned, anchor_data = unassigned;
    bool strict = false, kept_deferred = false;
    std::string operation;
    engine.set_residual_observer([&](const ResidualTrace& t) {
        // Reconstruct identities from query edge labels and opposite vertex
        // labels rather than the production edge-to-slot mapping.
        std::vector<std::map<IncidentSignature, uint32_t>> reference(order.size());
        uint32_t missing = 0;
        for (uint32_t e = 0; e < t.status.size(); ++e) {
            if (t.status[e] != EdgeCommitment::MISSING) continue;
            const auto& edge = plan.get_edge(e);
            ++missing;
            ++reference[edge.u][{edge.label, labels[edge.v]}];
            ++reference[edge.v][{edge.label, labels[edge.u]}];
        }
        require(t.missing_count == missing, "Global missing count mismatch");
        std::vector<bool> used(graph.get_vertices_count());
        for (auto v : t.mapping) {
            if (v == unassigned) continue;
            require(v < used.size() && !used[v], "Mapping must remain injective");
            used[v] = true;
        }
        require(used == t.used_data, "Used-data state differs from mapping");
        uint64_t incidence = 0;
        for (auto u : order) {
            const auto& reqs = sigs.get_vertex_requirements(u);
            for (size_t i = 0; i < reqs.size(); ++i) {
                const auto exp = reference[u][sigs.get_signature(reqs[i].sig_id)];
                const auto actual = t.state.count(u, i);
                require(actual == exp && actual <= reqs[i].required_count, "Residual state reconstruction mismatch");
                incidence += actual;
            }
        }
        require(incidence == 2ULL * missing, "Missing incidence conservation");
        if (t.point == Point::ANCHOR) {
            require(candidates.empty() && deferred.empty() && missing == 0, "Dirty anchor entry");
            anchor = t.edge;
            anchor_data = t.candidate_data;
        }
        if (t.point == Point::ANCHOR_RESTORED) {
            ++counts.anchors;
            require(candidates.empty() && deferred.empty() && missing == 0, "Dirty anchor exit");
            require(std::all_of(t.mapping.begin(), t.mapping.end(), [](auto v){ return v == unassigned; }), "Anchor mapping leaked");
            require(std::all_of(t.status.begin(), t.status.end(), [](auto s){ return s == EdgeCommitment::UNKNOWN; }), "Anchor status leaked");
        }
        if (t.point == Point::DFS_ENTRY) {
            ++counts.states;
            require(t.status[anchor] == EdgeCommitment::PRESENT, "Anchor must be PRESENT");
            counts.nested += missing >= 2;
        }
        if (t.point == Point::RESOLVED_MISSING) ++counts.resolved;
        if (t.point == Point::RESOLVED_INCIDENT) {
            require(!candidates.empty(), "Resolution without candidate");
            const auto added = missing - candidates.back().missing;
            counts.multi_missing_resolution += added >= 2;
            counts.rejected_after_missing += !t.candidate_allowed && added > 0;
        }
        if (t.point == Point::DEFERRED_MISSING) {
            ++counts.deferred;
            const auto& edge = plan.get_edge(t.edge);
            require((t.mapping[edge.u] == unassigned) != (t.mapping[edge.v] == unassigned), "Expected a deferred frontier");
            deferred.push_back({t.mapping, t.status, missing - 1});
            deferred.back().status[t.edge] = EdgeCommitment::UNKNOWN;
        }
        if (t.point == Point::CANDIDATE_RESTORED || t.point == Point::DEFERRED_RESTORED) {
            ++counts.restored;
            auto& stack = t.point == Point::CANDIDATE_RESTORED ? candidates : deferred;
            require(!stack.empty(), "Restoration without parent");
            const auto& before = stack.back();
            require(before.mapping == t.mapping && before.status == t.status && before.missing == missing, "Sibling state differs");
            stack.pop_back();
        }
        counts.over_budget += missing > k;
        if (t.point != Point::CANDIDATE) return;
        require(t.mapping[t.candidate_query] == unassigned, "Filter ran after assignment");
        uint64_t static_deficit = 0, residual_deficit = 0;
        const auto& reqs = sigs.get_vertex_requirements(t.candidate_query);
        uint32_t needed = 0, committed = 0, available = 0;
        for (const auto& req : reqs) {
            const auto& sig = sigs.get_signature(req.sig_id);
            available = 0;
            for (auto v : graph.get_neighbors(t.candidate_data)) {
                const auto lookup = graph.get_edge_label(t.candidate_data,v);
                available += lookup.exists && lookup.label == sig.edge_label_id && graph.get_vertex_label(v) == sig.neighbor_vertex_label_id;
            }
            needed = req.required_count;
            committed = reference[t.candidate_query][sig];
            const uint32_t remaining = needed - committed;
            static_deficit += needed > available ? needed - available : 0;
            residual_deficit += remaining > available ? remaining - available : 0;
        }
        require(t.candidate_allowed == (missing + residual_deficit <= k), "Candidate bound disagrees with independent computation");
        if (t.candidate_allowed) candidates.push_back({t.mapping, t.status, missing});

        if (name == "strict" || name == "deferred") {
            verify_completion_bound(t, plan, graph, missing + residual_deficit, counts);
            const bool is_strict = name == "strict" && !t.candidate_allowed && static_deficit <= k;
            const bool is_kept = name == "deferred" && t.candidate_allowed && missing + static_deficit > k;
            if ((!strict && is_strict) || (!kept_deferred && is_kept)) {
                std::cout << name << ' ' << operation << " anchor=" << anchor
                          << " orientation=" << (anchor_data == 0 ? "forward" : "reverse") << " mapping=";
                for (auto v : t.mapping) {
                    if (v == unassigned) std::cout << "_,";
                    else std::cout << v << ',';
                }
                std::cout << " missing_edges=";
                for (uint32_t e = 0; e < t.status.size(); ++e) {
                    if (t.status[e] == EdgeCommitment::MISSING) std::cout << e << ',';
                }
                std::cout << " candidate=" << t.candidate_query << "->" << t.candidate_data
                          << " m=" << missing << " sig=(edge:0,vertex:0) required=" << needed
                          << " committed=" << committed << " available=" << available
                          << " static_deficit=" << static_deficit << " residual_deficit=" << residual_deficit
                          << " remaining_budget=" << k - missing
                          << " static=KEEP residual=" << (t.candidate_allowed ? "KEEP" : "PRUNE") << '\n';
                strict |= is_strict;
                kept_deferred |= is_kept;
            }
        }
    });

    for (auto op : {UpdateOp::INSERT, UpdateOp::DELETE}) {
        strict = kept_deferred = false;
        operation = op == UpdateOp::INSERT ? "+" : "-";
        const auto result = engine.execute_step({op, 0, 1, TypedLabel::from_int(0)}, op == UpdateOp::INSERT ? 0 : 1);
        require(result.status == StepStatus::SUCCESS && result.committed, "Traced update did not commit");
        require(verify_index_reconstruction(graph, sigs, *engine.get_nlf_index()), "Data index after traced update");
        require(result.step_counters.nlf_residual_missing_commits == result.step_counters.nlf_residual_missing_undos, "Completed search counter imbalance");
        if (name == "strict") require(strict, "No reachable residual-only rejection");
        if (name == "deferred") require(kept_deferred, "No reachable deferred safeguard witness");
        if (expected_events >= 0) {
            require((op == UpdateOp::INSERT ? result.entered_tuples : result.exited_tuples).size()
                    == static_cast<size_t>(expected_events), "Traced witness events");
        }
    }
    if (name == "deferred") {
        engine.set_residual_observer([](const ResidualTrace& t) {
            if (t.point == Point::DEFERRED_MISSING) throw ResidualInvariantViolation("test-only residual corruption");
        });
        auto result = engine.execute_step({UpdateOp::INSERT,0,1,TypedLabel::from_int(0)}, 2);
        require(result.status == StepStatus::INVARIANT_VIOLATION && !result.committed, "Residual invariant outcome");
        require(!graph.has_edge(0,1) && engine.get_tx_manager().is_session_poisoned(), "Residual invariant must roll back and poison");
        require(verify_index_reconstruction(graph, sigs, *engine.get_nlf_index()), "Data index after residual invariant");
        engine.set_residual_observer({});
        result = engine.execute_step({UpdateOp::INSERT,0,1,TypedLabel::from_int(0)}, 3);
        require(result.status == StepStatus::INVARIANT_VIOLATION && !result.committed, "Poisoned session resumed");
    }
}

void trace() {
    TraceCounts counts;
    const std::vector<QueryEdge> c4{{2,3,0},{1,3,0},{0,2,0},{0,1,0}};
    trace_workload("strict", c4, {0,0,0,0}, {{1,3,0}}, 1, 0, counts);
    trace_workload("deferred", c4, {0,0,0,0}, {{1,3,0},{2,3,0}}, 1, 8, counts);
    size_t generated = 0;
    for (auto seed : {1,7,42}) {
        std::mt19937 rng(seed);
        for (unsigned sample = 0; sample < 12; ++sample) {
            std::vector<QueryEdge> qedges;
            std::vector<std::tuple<uint32_t,uint32_t,uint32_t>> data;
            std::vector<uint32_t> labels(5);
            if (sample % 3 == 1) labels[4] = 1;
            for (uint32_t u = 0; u < labels.size(); ++u) {
                for (uint32_t v = u + 1; v < labels.size(); ++v) {
                    const uint32_t ql = sample % 3 == 1 && v == 4 ? 1 : 0;
                    qedges.push_back({u,v,ql});
                    if ((u != 0 || v != 1) && rng() % 4 != 0) {
                        data.emplace_back(u,v, rng() % 9 == 0 ? 1 - ql : ql);
                    }
                }
            }
            for (uint32_t u = 0; u < labels.size(); ++u) {
                for (uint32_t v = labels.size(); v < labels.size() + 2; ++v) {
                    if (rng() % 4 != 0) data.emplace_back(u, v, rng() % 6 == 0 ? 1 : 0);
                }
            }
            trace_workload("generated", qedges, labels, data, sample % 4, -1, counts, 2);
            ++generated;
        }
    }
    std::cout << "Residual reconstruction generated=" << generated << " dfs=" << counts.states
              << " resolved_missing=" << counts.resolved << " deferred_missing=" << counts.deferred
              << " restorations=" << counts.restored << " anchor_restorations=" << counts.anchors
              << " rejected_after_missing=" << counts.rejected_after_missing
              << " multi_missing_resolution=" << counts.multi_missing_resolution
              << " over_budget=" << counts.over_budget << " nested=" << counts.nested
              << " compatible_completions_checked=" << counts.completions << " discrepancies=0\n";
    require(counts.states && counts.resolved && counts.deferred && counts.restored && counts.anchors
            && counts.nested && counts.multi_missing_resolution && counts.rejected_after_missing
            && counts.over_budget && counts.completions, "Trace transition coverage is incomplete");
}
#endif

} // namespace

int run_residual_native_tests(const char* selector) {
    try {
        const std::string suite(selector);
        if (suite == "numeric") {
            numeric();
            return 0;
        }
#ifdef APX_RESIDUAL_TEST_TRACE
        if (suite == "trace") {
            trace();
            return 0;
        }
#endif
        std::cerr << "Unsupported residual suite for this build: " << suite << '\n';
    } catch (const std::exception& error) {
        std::cerr << "Residual verification failed: " << error.what() << '\n';
    }
    return 1;
}

} // namespace rapidflow_apx
