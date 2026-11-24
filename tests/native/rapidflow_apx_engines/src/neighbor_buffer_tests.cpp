#include "p4b_tests.h"
#include "rapidflow_apx/streaming_engine.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <new>
#include <random>
#include <sstream>
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
    } catch (const NeighborPoolInvariantViolation&) {
        return;
    }
    throw std::runtime_error("Expected neighbor pool invariant");
}

using Vertex = std::pair<uint32_t, uint32_t>;
using Edge = std::tuple<uint32_t, uint32_t, uint32_t>;
using EdgeMap = std::map<std::pair<uint32_t, uint32_t>, uint32_t>;

struct GraphCounts {
    size_t snapshots = 0;
    size_t comparisons = 0;
    size_t nonempty_reuse = 0;
    size_t committed = 0;
    size_t aborted = 0;
};

void verify_snapshot(const ApxAuthoritativeGraph& graph, const EdgeMap& ref,
                     std::vector<uint32_t>& out, GraphCounts& counts) {
    const auto n = graph.get_vertices_count();
    require(graph.get_edges_count() == ref.size(), "Effective edge count differs");
    ++counts.snapshots;
    for (uint32_t u = 0; u < n; ++u) {
        std::vector<uint32_t> expected;
        for (uint32_t v = 0; v < n; ++v) {
            const auto expected_edge = ref.find(std::minmax(u, v));
            const bool exists = expected_edge != ref.end();
            const auto actual = graph.get_edge_label(u, v);
            require(actual.exists == exists && graph.has_edge(u, v) == exists, "Asymmetric or stale edge");
            if (exists) {
                expected.push_back(v);
                require(actual.label == expected_edge->second, "Effective edge label differs");
            }
        }
        const auto capacity = out.capacity();
        graph.get_neighbors_into(u, out);
        std::vector<uint32_t> merged;
        EngineCounters mc;
        graph.get_neighbors_merged_into(u, merged, &mc);
        require(out == expected && graph.get_neighbors(u) == expected, "Ordered neighbor output differs");
        require(merged == expected, "Merged neighbor output differs");
        require(mc.neighbor_merge_retrieval_calls == 1, "Merge retrieval count differs");
        require(mc.neighbor_merge_linear_calls + mc.neighbor_merge_fast_path_calls + mc.neighbor_merge_fallback_calls == 1,
                "Exclusive merge path count differs");
        require(graph.get_degree(u) == expected.size(), "Degree differs");
        ++counts.comparisons;
        counts.nonempty_reuse += !out.empty() && out.capacity() == capacity;
    }
}

void pool_test() {
    expect_invariant([] { ApxNeighborBufferPool pool(1, 0); });
    expect_invariant([] { ApxNeighborBufferPool pool(3, std::numeric_limits<size_t>::max()); });
    expect_invariant([] { ApxNeighborBufferPool pool(2, std::numeric_limits<size_t>::max() - 1); });
    ApxNeighborBufferPool k2(2, 0);
    require(k2.size() == 1 && k2.at(0).empty(), "K2 has one frame");
    expect_invariant([&] { k2.at(1); });
    for (int i = 0; i < 1000; ++i) {
        ApxNeighborBufferPool pool(6, 2);
        require(pool.size() == 7, "Incorrect prism frame bound");
        auto& parent = pool.at(2);
        parent.push_back(42);
        const auto* original = parent.data();
        pool.at(3).resize(64);
        require(parent.data() == original && parent == std::vector<uint32_t>{42}, "Child growth moved parent");
    }
    std::cout << "Pool frame guards and 1000 lifetimes passed. vector_object_bytes="
              << sizeof(std::vector<uint32_t>) << '\n';
}

void graph_test() {
    ApxTypedCodec codec;
    std::vector<Vertex> vertices;
    for (int64_t ext : {-100, 3, 18, 75, 900, 25000, 200000}) {
        vertices.emplace_back(codec.intern_data_vertex(ext),
                              codec.intern_vertex_label(TypedLabel::from_string(u8"đỉnh")));
    }
    require(codec.get_data_vertex(75) == 3, "Sparse external ID mapping");
    const auto left_label = codec.intern_edge_label(TypedLabel::from_int(0));
    const auto new_label = codec.intern_edge_label(TypedLabel::from_string(u8"đường"));
    const auto str_zero = codec.intern_edge_label(TypedLabel::from_string("0"));
    require(left_label != str_zero && left_label != new_label && str_zero != new_label, "Typed labels lost identity");

    ApxAuthoritativeGraph graph;
    graph.load_baseline(vertices, {{0,4,left_label},{4,3,left_label},{0,1,str_zero}});
    EdgeMap ref{{{0,4},left_label}, {{3,4},left_label}, {{0,1},str_zero}};
    GraphCounts direct;
    std::vector<uint32_t> out{91, 92, 93};
    for (auto invalid : {7u, 8u, std::numeric_limits<uint32_t>::max()}) {
        const auto capacity = out.capacity();
        graph.get_neighbors_into(invalid, out);
        require(out.empty() && out.capacity() == capacity, "Invalid vertex left stale output");
        out.push_back(92);
    }
    verify_snapshot(graph, ref, out, direct);
    require(graph.apply_insert(0, 2, new_label), "Overlay insert");
    ref[{0,2}] = new_label;
    require(graph.apply_insert(2, 6, str_zero), "Overlay-only insert");
    ref[{2,6}] = str_zero;
    verify_snapshot(graph, ref, out, direct);
    require(graph.apply_delete(0, 4, left_label), "Base deletion");
    ref.erase({0,4});
    verify_snapshot(graph, ref, out, direct);
    graph.begin_transaction();
    require(graph.apply_insert(4, 0, new_label), "Tentative replacement of base edge");
    ref[{0,4}] = new_label;
    verify_snapshot(graph, ref, out, direct);
    graph.commit_transaction();
    verify_snapshot(graph, ref, out, direct);
    graph.begin_transaction();
    require(graph.apply_delete(0, 4, new_label), "Tentative delete");
    graph.rollback_transaction();
    verify_snapshot(graph, ref, out, direct);
    for (auto u : {1u, 2u, 4u}) {
        require(graph.apply_delete(0, u, ref.at({0,u})), "Delete all effective neighbors");
        ref.erase({0,u});
    }
    verify_snapshot(graph, ref, out, direct);
    require(graph.get_neighbors(0).empty(), "All effective neighbors removed");

    constexpr uint32_t high_degree = 65536;
    std::vector<Vertex> high_vertices;
    std::vector<Edge> high_edges;
    for (uint32_t i = 0; i <= high_degree + 1; ++i) high_vertices.emplace_back(i, 0);
    for (uint32_t i = high_degree; i > 0; --i) high_edges.emplace_back(0, i, 0);
    ApxAuthoritativeGraph high;
    high.load_baseline(high_vertices, high_edges);
    std::vector<uint32_t> expected(high_degree);
    std::iota(expected.begin(), expected.end(), 1);
    const auto assert_fill = [&](uint32_t u, const std::vector<uint32_t>& want, bool grows) {
        const auto before = out.capacity();
        high.get_neighbors_into(u, out);
        require(out == want && high.get_neighbors(u) == want, "High-degree or reused contents wrong");
        require((out.capacity() > before) == grows, "Expected capacity change differs");
        std::cout << "direct vertex=" << u << " size=" << out.size() << " before=" << before
                  << " after=" << out.capacity() << '\n';
    };
    assert_fill(0, expected, true);
    assert_fill(0, expected, false);
    assert_fill(1, {0}, false);
    assert_fill(high_degree + 1, {}, false);
    assert_fill(0, expected, false);

    GraphCounts generated;
    for (auto seed : {1, 7, 42}) {
        std::mt19937 rng(seed);
        ApxAuthoritativeGraph test;
        test.load_baseline(vertices, {{0,1,left_label},{0,3,str_zero},{2,4,new_label}});
        EdgeMap model{{{0,1},left_label}, {{0,3},str_zero}, {{2,4},new_label}};
        verify_snapshot(test, model, out, generated);
        for (int update = 0; update < 72; ++update) {
            uint32_t u = rng() % vertices.size();
            uint32_t v = (u + 1 + rng() % (vertices.size() - 1)) % vertices.size();
            const std::pair<uint32_t,uint32_t> key = std::minmax(u, v);
            const auto prior = model.find(key);
            const bool valid_insert = prior == model.end();
            const auto label = valid_insert ? std::array{left_label,new_label,str_zero}[rng() % 3] : prior->second;
            const bool invalid = update % 9 == 0;
            const bool rollback = update % 9 == 1;
            const bool partial_insert = update % 9 == 2 && valid_insert;
            test.begin_transaction();
            if (invalid) {
                const bool changed = valid_insert ? test.apply_delete(u, v, label) : test.apply_insert(u, v, label);
                require(!changed, "Invalid update changed graph");
                test.rollback_transaction();
                ++generated.aborted;
            } else if (partial_insert) {
                require(!test.apply_insert(u, v, label, true), "Partial insert should fail");
                test.rollback_transaction();
                ++generated.aborted;
            } else {
                require(valid_insert ? test.apply_insert(u, v, label) : test.apply_delete(u, v, label), "Valid update failed");
                if (valid_insert) {
                    auto tentative = model;
                    tentative[key] = label;
                    verify_snapshot(test, tentative, out, generated);
                }
                if (rollback) {
                    test.rollback_transaction();
                    ++generated.aborted;
                } else {
                    test.commit_transaction();
                    if (valid_insert) model[key] = label;
                    else model.erase(key);
                    ++generated.committed;
                }
            }
            verify_snapshot(test, model, out, generated);
        }
    }
    std::cout << "direct_snapshots=" << direct.snapshots << " direct_vertex_comparisons=" << direct.comparisons
              << " generated_seeds=1,7,42 attempted=216 committed=" << generated.committed
              << " aborted=" << generated.aborted << " snapshots=" << generated.snapshots
              << " vertex_comparisons=" << generated.comparisons << " nonempty_no_growth=" << generated.nonempty_reuse
              << " vector_discrepancies=0 duplicate_discrepancies=0\n";
}

#ifdef APX_NEIGHBOR_TEST_TRACE
using Point = NeighborTracePoint;

struct SearchFixture {
    ApxAuthoritativeGraph graph;
    ApxTypedCodec codec;
    ApxQueryPlan plan;
    std::vector<uint32_t> order;
    ApxTupleSink sink;

    SearchFixture(uint32_t q, std::vector<QueryEdge> edges, uint32_t k, uint32_t n,
                  const std::vector<Edge>& initial)
        : order(make_order(q)), sink(codec, order) {
        plan.build(q, std::vector<uint32_t>(q, 0), edges, order, k);
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));
        std::vector<Vertex> verts;
        for (uint32_t i = 0; i < n; ++i) {
            verts.emplace_back(codec.intern_data_vertex(i), 0);
        }
        graph.load_baseline(verts, initial);
    }

    static std::vector<uint32_t> make_order(uint32_t q) {
        std::vector<uint32_t> values(q);
        std::iota(values.begin(), values.end(), 0);
        return values;
    }
};

void append(std::ostream& out, const std::vector<uint32_t>& values) {
    for (auto value : values) {
        if (value == ApxM2EventEngine::UNASSIGNED) out << "_,";
        else out << value << ',';
    }
}

struct LiveParent {
    Point branch;
    size_t depth;
    uint32_t assigned;
    uint32_t missing;
    const std::vector<uint32_t>* slot;
    const uint32_t* storage;
    std::vector<uint32_t> contents;
    size_t capacity;
};

struct TraceCounts {
    size_t states = 0;
    size_t anchors = 0;
    size_t present = 0;
    size_t missing = 0;
    size_t child_growth = 0;
    size_t sibling_reuse = 0;
    size_t cross_anchor_reuse = 0;
    size_t candidates = 0;
    size_t extensions = 0;
    size_t fills = 0;
    size_t max_depth = 0;
    size_t initialized = 0;
    size_t growth = 0;
    size_t no_growth = 0;
    size_t changed_size_same_slot = 0;
    std::vector<size_t> capacities;
    std::vector<std::string> witnesses;
};

void run_trace(const std::string& name, uint32_t q, const std::vector<QueryEdge>& edges, uint32_t k, uint32_t n,
               const std::vector<Edge>& initial, std::ostream* saved_trace, uint64_t work = 500000) {
    std::vector<std::string> legacy_path;
    StreamStepResult legacy;
    std::vector<std::string> buffer_path;
    std::vector<std::string> merge_path;

    for (const auto mode : {NeighborMode::LEGACY, NeighborMode::BUFFER, NeighborMode::MERGE}) {
        const bool uses_pool = (mode == NeighborMode::BUFFER || mode == NeighborMode::MERGE);
        SearchFixture f(q, edges, k, n, initial);
        ApxStreamingEngine engine(f.graph, f.codec, f.sink, f.plan, EngineType::M2, work, NlfMode::NONE, 0, mode);
        TraceCounts c;
        const size_t frames = q - 1 + k;
        std::vector<const std::vector<uint32_t>*> slots(frames, nullptr);
        std::vector<size_t> last_anchor(frames, 0), last_size(frames, 0);
        c.capacities.resize(frames);
        std::vector<LiveParent> parents;
        bool next_child = false;
        unsigned seen_search = 0;
        std::vector<std::string> current_path;
        engine.set_neighbor_observer([&](const NeighborTrace& t) {
            if (t.point == Point::BEFORE_POOL) {
                ++c.initialized;
                ++seen_search;
                std::fill(slots.begin(), slots.end(), nullptr);
                std::fill(last_anchor.begin(), last_anchor.end(), 0);
                std::fill(last_size.begin(), last_size.end(), 0);
                std::fill(c.capacities.begin(), c.capacities.end(), 0);
                return;
            }
            if (t.point == Point::BEFORE_FILL) return;
            require(t.depth < frames, "Traced depth exceeds proof bound");
            require(t.pool_frames == (uses_pool ? frames : 0), "Incorrect pool size or disabled allocation");
            std::ostringstream row;
            row << static_cast<int>(t.point) << ' ' << t.depth << ' ' << t.assigned_count << ' '
                << t.missing_count << ' ' << t.edge << ' ' << t.candidate << ' ';
            append(row, t.mapping);
            for (auto s : t.status) row << static_cast<int>(s);
            if (t.point == Point::FILLED) {
                row << " neighbors=";
                append(row, *t.neighbors);
            }
            current_path.push_back(row.str());

            if (t.point == Point::ANCHOR) ++c.anchors;
            if (t.point == Point::CANDIDATE) ++c.candidates;
            if (t.point == Point::EXTEND) ++c.extensions;
            if (t.point == Point::DFS) {
                ++c.states;
                c.max_depth = std::max(c.max_depth, t.depth);
                if (t.depth == 0) {
                    require(t.assigned_count == 2 && t.missing_count == 0, "Invalid anchor root");
                }
                if (uses_pool) {
                    require(slots[t.depth] == nullptr || slots[t.depth] == t.neighbors, "Outer pool relocated");
                    slots[t.depth] = t.neighbors;
                    for (const auto& p : parents) require(p.slot != t.neighbors, "Live parent-child slot alias");
                }
                if (next_child) {
                    const auto& p = parents.back();
                    const bool missing = p.branch == Point::MISSING_DESCEND;
                    require(t.depth == p.depth + 1, "Child depth did not increase");
                    require(t.assigned_count == p.assigned + !missing, "Incorrect child assignment count");
                    require(t.missing_count == p.missing + missing, "Incorrect child missing count");
                    if (uses_pool && missing && !p.contents.empty() && c.missing == 1) {
                        std::ostringstream witness;
                        witness << "deferred parent_depth=" << p.depth << " child_depth=" << t.depth
                                << " assigned=" << p.assigned << ',' << t.assigned_count
                                << " missing=" << p.missing << ',' << t.missing_count
                                << " distinct_slots=true parent_neighbors=";
                        append(witness, p.contents);
                        c.witnesses.push_back(witness.str());
                    }
                    next_child = false;
                }
            }
            if (t.point == Point::FILLED) {
                ++c.fills;
                if (uses_pool) {
                    require(t.neighbors == slots[t.depth], "Fill used the wrong slot");
                    const auto previous = t.previous_capacity;
                    const auto capacity = t.neighbors->capacity();
                    const auto size = t.neighbors->size();
                    const bool grew = capacity > previous;
                    const bool reuse = size && !grew && previous > 0;
                    require(previous == c.capacities[t.depth], "Previous capacity disagrees with search-local slot");
                    if (grew && !parents.empty() && !parents.back().contents.empty()) {
                        if (++c.child_growth == 1) {
                            const auto& p = parents.back();
                            std::ostringstream witness;
                            witness << "child_growth parent_depth=" << p.depth << " child_depth=" << t.depth
                                    << " parent_assigned=" << p.assigned << " child_assigned=" << t.assigned_count
                                    << " parent_missing=" << p.missing << " child_missing=" << t.missing_count
                                    << " before=" << previous << " after=" << capacity << " parent_neighbors=";
                            append(witness, p.contents);
                            witness << " child_neighbors=";
                            append(witness, *t.neighbors);
                            c.witnesses.push_back(witness.str());
                        }
                    }
                    if (reuse && last_anchor[t.depth] == c.anchors && t.depth > 0) {
                        if (++c.sibling_reuse == 1) {
                            c.witnesses.push_back("sibling depth=" + std::to_string(t.depth)
                                + " size=" + std::to_string(size) + " before=" + std::to_string(previous)
                                + " after=" + std::to_string(capacity));
                        }
                    }
                    if (reuse && last_anchor[t.depth] && last_anchor[t.depth] != c.anchors) ++c.cross_anchor_reuse;
                    if (last_anchor[t.depth] && size != last_size[t.depth]) ++c.changed_size_same_slot;
                    c.growth += grew;
                    c.no_growth += !grew;
                    c.capacities[t.depth] = capacity;
                    last_anchor[t.depth] = c.anchors;
                    last_size[t.depth] = size;
                }
            }
            if (t.point == Point::PRESENT_DESCEND || t.point == Point::MISSING_DESCEND) {
                c.present += t.point == Point::PRESENT_DESCEND;
                c.missing += t.point == Point::MISSING_DESCEND;
                parents.push_back({t.point, t.depth, t.assigned_count, t.missing_count, t.neighbors,
                                   t.neighbors->data(), *t.neighbors, t.neighbors->capacity()});
                next_child = true;
            }
            if (t.point == Point::PRESENT_RETURN || t.point == Point::MISSING_RETURN) {
                require(!parents.empty(), "Returned without a parent");
                const auto& p = parents.back();
                require(t.depth == p.depth && t.neighbors == p.slot && t.neighbors->data() == p.storage
                        && *t.neighbors == p.contents && t.neighbors->capacity() == p.capacity,
                        "Child altered live parent candidate storage");
                parents.pop_back();
            }
        });
        const auto step = engine.execute_step({UpdateOp::INSERT,0,1,TypedLabel::from_int(0)}, 0);
        if (mode == NeighborMode::LEGACY) {
            require(step.step_counters.neighbor_buffer_retrieval_calls == 0 && seen_search == 0, "Legacy buffer operations");
            legacy = step;
            legacy_path = current_path;
            continue;
        } else if (mode == NeighborMode::BUFFER) {
            buffer_path = current_path;
            require(buffer_path == legacy_path, "Ordered DFS trace changed between legacy and buffer");
            require(step.step_counters.neighbor_merge_retrieval_calls == 0, "Nonzero merge counter in buffer mode");
        } else { // MERGE
            merge_path = current_path;
            require(merge_path == legacy_path, "Ordered DFS trace changed between legacy and merge");
            require(step.step_counters.neighbor_merge_retrieval_calls == step.step_counters.neighbor_buffer_retrieval_calls,
                    "Merge retrievals match buffer retrievals");
            require(step.step_counters.neighbor_merge_retrieval_calls ==
                    step.step_counters.neighbor_merge_linear_calls +
                    step.step_counters.neighbor_merge_fast_path_calls +
                    step.step_counters.neighbor_merge_fallback_calls,
                    "Exclusive merge path accounting in trace");
        }
        if (saved_trace && mode == NeighborMode::MERGE) {
            for (const auto& [label, path] : {std::pair{"LEGACY", &legacy_path}, std::pair{"BUFFER", &buffer_path}, std::pair{"MERGE", &merge_path}}) {
                *saved_trace << "case " << name << ' ' << label << '\n';
                for (const auto& row : *path) *saved_trace << row << '\n';
            }
            require(static_cast<bool>(*saved_trace), "Writing normalized trace failed");
        }
        require(step.committed == legacy.committed && step.status == legacy.status
                && step.entered_tuples == legacy.entered_tuples && step.exited_tuples == legacy.exited_tuples
                && step.step_counters.work_units == legacy.step_counters.work_units, "Trace workload result changed");
        require(c.initialized == 1 && c.fills == step.step_counters.neighbor_buffer_retrieval_calls
                && c.growth == step.step_counters.neighbor_buffer_growth_calls
                && c.no_growth == step.step_counters.neighbor_buffer_no_growth_calls, "Buffer counter accounting");
        if (step.committed) require(parents.empty() && !next_child, "Completed search left live child");
        if (name == "K2") require(c.fills == 0 && c.max_depth == 0, "K2 must be a root leaf");
        if (name == "C4") require(c.missing && !step.entered_tuples.empty(), "Deferred event-bearing witness");
        if (name == "Diamond") require(c.child_growth && c.sibling_reuse && c.cross_anchor_reuse
            && c.changed_size_same_slot && c.candidates > c.extensions && !step.entered_tuples.empty(),
            "Diamond must witness live growth, reuse and rejected candidates");
        if (name == "C4-budget") require(!step.committed && c.max_depth > 0
            && step.error_message.find("Work budget exceeded") != std::string::npos, "Expected deep work budget abort");
        std::cout << name << " trace_rows=" << buffer_path.size() << " depth_max=" << c.max_depth
                  << " frames=" << frames << " dfs=" << c.states << " anchors=" << c.anchors
                  << " present=" << c.present << " missing=" << c.missing << " fills=" << c.fills
                  << " growth=" << c.growth << " no_growth=" << c.no_growth << " child_growth=" << c.child_growth
                  << " sibling_reuse=" << c.sibling_reuse << " cross_anchor_reuse=" << c.cross_anchor_reuse
                  << " changed_size_same_slot=" << c.changed_size_same_slot << " capacity=";
        for (auto capacity : c.capacities) std::cout << capacity << ',';
        std::cout << " events=" << step.entered_tuples.size() << " trace_parity=true\n";
        for (const auto& witness : c.witnesses) std::cout << "  " << witness << '\n';
        if (name == "C4") {
            bool first_fill = true;
            engine.set_neighbor_observer([&](const NeighborTrace& t) {
                if (t.point == Point::BEFORE_FILL && first_fill) {
                    require(t.depth == 0 && t.previous_capacity == 0, "New update reused a prior search pool");
                    first_fill = false;
                }
            });
            const auto inverse = engine.execute_step({UpdateOp::DELETE,0,1,TypedLabel::from_int(0)}, 1);
            require(!first_fill && inverse.committed && inverse.exited_tuples == step.entered_tuples,
                    "Inverse update or new search pool differs");
            std::cout << "C4 inverse DELETE used fresh root capacity and matched 8 EXITED tuples\n";
        }
    }
}

void injected_failures() {
    const std::vector<QueryEdge> c4{{0,1,0},{0,2,0},{1,3,0},{2,3,0}};
    for (auto test_mode : {NeighborMode::BUFFER, NeighborMode::MERGE}) {
        for (auto op : {UpdateOp::INSERT, UpdateOp::DELETE}) {
            const bool insert = op == UpdateOp::INSERT;
            for (int fault = 0; fault < 5; ++fault) {
                std::vector<Edge> start{{1,3,0},{2,3,0}};
                if (!insert) start.emplace_back(0,1,0);
                SearchFixture f(4, c4, 1, 4, start);
                ApxStreamingEngine engine(f.graph, f.codec, f.sink, f.plan, EngineType::M2, 500000,
                                          NlfMode::NONE, 0, test_mode);
            bool reached = false;
            unsigned emissions = 0;
            engine.set_neighbor_observer([&](const NeighborTrace& t) {
                emissions += t.point == Point::EMIT;
                const bool before_fill = t.point == Point::BEFORE_FILL;
                if (fault == 4 && before_fill && t.depth > 0) {
                    reached = true;
                    ApxNeighborBufferPool guard(2, 0);
                    guard.at(t.depth); // Exercise the actual runtime guard through callback containment.
                }
                if ((fault == 0 && t.point == Point::BEFORE_POOL)
                    || (fault == 1 && before_fill)
                    || (fault == 2 && before_fill && emissions != 0)
                    || (fault == 3 && before_fill && t.depth > 0)) {
                    reached = true;
                    throw std::bad_alloc();
                }
            });
            const auto failed = engine.execute_step({op,0,1,TypedLabel::from_int(0)}, 0);
            require(reached && !failed.committed && failed.entered_tuples.empty() && failed.exited_tuples.empty(),
                    "Injected buffer failure did not abort");
            const bool poison = fault == 4;
            require(failed.status == (poison ? StepStatus::INVARIANT_VIOLATION : StepStatus::CALLBACK_ABORTED),
                    "Wrong injected failure status");
            require(f.graph.has_edge(0,1) == !insert && f.graph.get_edges_count() == start.size(),
                    "Injected buffer failure changed graph");
            require(failed.step_counters.committed_emissions == 0 && engine.get_tx_manager().is_session_poisoned() == poison,
                    "Invalid transaction containment");
            if (fault == 2) require(failed.step_counters.discarded_emissions > 0, "Late hook did not discard staged tuples");
            bool fresh = false;
            engine.set_neighbor_observer([&](const NeighborTrace& t) {
                if (t.point == Point::BEFORE_FILL && !fresh) {
                    require(t.previous_capacity == 0, "Next search reused an aborted pool");
                    fresh = true;
                }
            });
            const auto next = engine.execute_step({op,0,1,TypedLabel::from_int(0)}, 1);
            const std::vector<std::vector<int64_t>> expected{
                {0,1,2,3},{0,2,1,3},{1,0,3,2},{1,3,0,2},
                {2,0,3,1},{2,3,0,1},{3,1,2,0},{3,2,1,0}
            };
            if (!poison) {
                auto actual = insert ? next.entered_tuples : next.exited_tuples;
                std::sort(actual.begin(), actual.end());
                require(fresh && next.committed && actual == expected, "Retry did not recover with exact events");
            } else {
                require(!fresh && !next.committed && next.status == StepStatus::INVARIANT_VIOLATION,
                        "Poisoned session resumed");
            }
            std::cout << "injected mode=" << (test_mode == NeighborMode::BUFFER ? "BUFFER" : "MERGE")
                      << " op=" << (insert ? "INSERT" : "DELETE") << " seam=" << fault
                      << " emissions_before_abort=" << emissions << " discarded=" << failed.step_counters.discarded_emissions
                      << " restored=true fresh_retry=" << fresh << " poisoned=" << poison << '\n';
        }
    }
    }
}

void trace_test() {
    std::ofstream saved;
    if (const char* path = std::getenv("APX_NEIGHBOR_TRACE_FILE")) {
        saved.open(path);
        require(static_cast<bool>(saved), "Cannot open normalized trace file");
    }
    std::ostream* trace_output = saved.is_open() ? &saved : nullptr;
    const std::vector<QueryEdge> triangle{{0,1,0},{0,2,0},{1,2,0}};
    const std::vector<QueryEdge> c4{{0,1,0},{0,2,0},{1,3,0},{2,3,0}};
    const std::vector<QueryEdge> diamond{{0,1,0},{0,2,0},{1,2,0},{1,3,0},{2,3,0}};
    auto k4 = diamond;
    k4.push_back({0,3,0});
    const std::vector<QueryEdge> prism{{0,1,0},{1,2,0},{0,2,0},{3,4,0},{4,5,0},{3,5,0},
                                       {0,3,0},{1,4,0},{2,5,0}};
    run_trace("K2", 2, {{0,1,0}}, 0, 2, {}, trace_output);
    run_trace("Triangle-k0", 3, triangle, 0, 4, {{0,2,0},{1,2,0},{2,3,0}}, trace_output);
    run_trace("Triangle-k1", 3, triangle, 1, 4, {{0,2,0},{2,3,0}}, trace_output);
    run_trace("C4", 4, c4, 1, 4, {{1,3,0},{2,3,0}}, trace_output);
    run_trace("C4-budget", 4, c4, 1, 4, {{1,3,0},{2,3,0}}, trace_output, 9);
    run_trace("Diamond", 4, diamond, 1, 6, {{0,2,0},{0,3,0},{1,2,0},{1,3,0},{2,3,0},{1,4,0},{3,4,0},{3,5,0}}, trace_output);
    run_trace("K4-k2", 4, k4, 2, 5, {{0,2,0},{1,3,0},{2,3,0},{1,4,0},{3,4,0}}, trace_output);
    run_trace("Prism-k2", 6, prism, 2, 6, {{1,2,0},{0,2,0},{3,4,0},{4,5,0},{0,3,0},{1,4,0},{2,5,0}}, trace_output);
    injected_failures();
}
#endif

void merge_direct_and_fallback_tests() {
    // -------------------------------------------------------------------------
    // Direct MG-G01 to MG-G24 scenarios on ApxAuthoritativeGraph
    // -------------------------------------------------------------------------

    // MG-G01: Invalid vertex with populated output (clears output, returns empty)
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}}, {{0, 1, 0}});
        std::vector<uint32_t> out{42, 99};
        g.get_neighbors_merged_into(999, out);
        require(out.empty(), "MG-G01: Invalid vertex returns empty");
    }

    // MG-G02: Isolated valid vertex (returns empty)
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}}, {});
        std::vector<uint32_t> out{10};
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out.empty(), "MG-G02: Isolated vertex returns empty");
        require(mc.neighbor_merge_fast_path_calls == 1, "MG-G02: Fast path empty");
    }

    // MG-G03: Both ranges empty
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}}, {});
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out.empty(), "MG-G03: Both ranges empty");
        require(mc.neighbor_merge_fast_path_calls == 1, "MG-G03: Fast path empty");
    }

    // MG-G04: Base-only clean graph (Fast path 3)
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 1, 0}, {0, 2, 0}, {0, 3, 0}});
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3}), "MG-G04: Base-only clean graph");
        require(mc.neighbor_merge_fast_path_calls == 1, "MG-G04: Fast path base-only");
    }

    // MG-G05: Overlay-only adjacency (Fast path 2)
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}}, {});
        g.begin_transaction();
        g.apply_insert(0, 1, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2}), "MG-G05: Overlay-only adjacency");
        require(mc.neighbor_merge_fast_path_calls == 1, "MG-G05: Fast path overlay-only");
    }

    // MG-G06: Base plus overlay (Linear merge)
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 1, 0}});
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2}), "MG-G06: Base plus overlay");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G06: Linear merge path");
    }

    // MG-G07: Interleaved base and overlay
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}, {5, 0}}, {{0, 1, 0}, {0, 3, 0}, {0, 5, 0}});
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 4, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3, 4, 5}), "MG-G07: Interleaved base and overlay");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G07: Linear merge");
    }

    // MG-G08: All base values precede overlay
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}}, {{0, 1, 0}, {0, 2, 0}});
        g.begin_transaction();
        g.apply_insert(0, 3, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 4, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3, 4}), "MG-G08: Base precedes overlay");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G08: Linear merge");
    }

    // MG-G09: All overlay values precede base
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}}, {{0, 3, 0}, {0, 4, 0}});
        g.begin_transaction();
        g.apply_insert(0, 1, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3, 4}), "MG-G09: Overlay precedes base");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G09: Linear merge");
    }

    // MG-G10: Base exhausted before overlay
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 1, 0}});
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 3, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3}), "MG-G10: Base exhausted first");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G10: Linear merge");
    }

    // MG-G11: Overlay exhausted before base
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 2, 0}, {0, 3, 0}});
        g.begin_transaction();
        g.apply_insert(0, 1, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3}), "MG-G11: Overlay exhausted first");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G11: Linear merge");
    }

    // MG-G12: Tombstone at first base element
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 1, 0}, {0, 3, 0}});
        g.begin_transaction();
        g.apply_delete(0, 1, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{2, 3}), "MG-G12: First base tombstoned");
    }

    // MG-G13: Tombstone at last base element
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 1, 0}, {0, 3, 0}});
        g.begin_transaction();
        g.apply_delete(0, 3, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2}), "MG-G13: Last base tombstoned");
    }

    // MG-G14: Consecutive tombstones
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}}, {{0, 1, 0}, {0, 2, 0}, {0, 3, 0}, {0, 4, 0}});
        g.begin_transaction();
        g.apply_delete(0, 2, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_delete(0, 3, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 4}), "MG-G14: Consecutive tombstones");
    }

    // MG-G15: Every base neighbor tombstoned
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {{0, 1, 0}, {0, 2, 0}});
        g.begin_transaction();
        g.apply_delete(0, 1, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_delete(0, 2, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 3, 0);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{3}), "MG-G15: Every base neighbor tombstoned");
    }

    // MG-G16: Overlay replaces base neighbor
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}}, {{0, 1, 0}, {0, 2, 0}});
        g.begin_transaction();
        g.apply_delete(0, 2, 0);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 2, 9);
        g.commit_transaction();
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2}), "MG-G16: Neighbor 2 appears exactly once");
        require(g.get_edge_label(0, 2).label == 9, "MG-G16: Label comes from overlay");
    }

    // MG-G17: Delete/reinsert with same label
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}}, {{0, 1, 4}});
        g.begin_transaction();
        g.apply_delete(0, 1, 4);
        g.commit_transaction();
        require(!g.has_edge(0, 1), "MG-G17: Edge deleted");
        g.begin_transaction();
        g.apply_insert(0, 1, 4);
        g.commit_transaction();
        std::vector<uint32_t> out;
        g.get_neighbors_merged_into(0, out);
        require(out == (std::vector<uint32_t>{1}), "MG-G17: Neighbor reinserted");
        require(g.get_edge_label(0, 1).label == 4, "MG-G17: Label 4 preserved");
    }

    // MG-G18: Delete/reinsert with different label
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}}, {{0, 1, 4}});
        g.begin_transaction();
        g.apply_delete(0, 1, 4);
        g.commit_transaction();
        g.begin_transaction();
        g.apply_insert(0, 1, 8);
        g.commit_transaction();
        std::vector<uint32_t> out;
        g.get_neighbors_merged_into(0, out);
        require(out == (std::vector<uint32_t>{1}), "MG-G18: Neighbor reinserted");
        require(g.get_edge_label(0, 1).label == 8, "MG-G18: New label 8");
    }

    // MG-G19: Critical Tentative INSERT with retained tombstone (Section XXI)
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}}, {{0, 1, 10}});
        g.begin_transaction();
        g.apply_delete(0, 1, 10);
        g.commit_transaction();
        require(g.is_in_tombstones(0, 1), "MG-G19: Tombstone active before insert");

        // Tentative uncommitted reinsert
        g.begin_transaction();
        g.apply_insert(0, 1, 20);

        require(g.is_in_tombstones(0, 1), "MG-G19: Tombstone retained during tentative insert");
        require(g.is_in_overlay(0, 1), "MG-G19: Overlay active during tentative insert");
        require(g.has_edge(0, 1), "MG-G19: has_edge is true");
        require(g.get_edge_label(0, 1).label == 20, "MG-G19: Edge label is 20");

        std::vector<uint32_t> legacy = g.get_neighbors(0);
        std::vector<uint32_t> buffer;
        g.get_neighbors_into(0, buffer);
        std::vector<uint32_t> merged;
        g.get_neighbors_merged_into(0, merged);
        std::vector<uint32_t> merged1;
        g.get_neighbors_merged_into(1, merged1);

        require(legacy == (std::vector<uint32_t>{1}), "MG-G19: Legacy sees reinserted edge");
        require(buffer == (std::vector<uint32_t>{1}), "MG-G19: Buffer sees reinserted edge");
        require(merged == (std::vector<uint32_t>{1}), "MG-G19: Merge sees reinserted edge on endpoint 0");
        require(merged1 == (std::vector<uint32_t>{0}), "MG-G19: Merge sees reinserted edge on endpoint 1");

        // Rollback tentative insert
        g.rollback_transaction();
        require(!g.has_edge(0, 1), "MG-G19: Edge absent after rollback");
        std::vector<uint32_t> post_rollback;
        g.get_neighbors_merged_into(0, post_rollback);
        require(post_rollback.empty(), "MG-G19: Post-rollback neighbors 0 empty");
        std::vector<uint32_t> post_rollback1;
        g.get_neighbors_merged_into(1, post_rollback1);
        require(post_rollback1.empty(), "MG-G19: Post-rollback neighbors 1 empty");
    }

    // MG-G20: Rollback-restored snapshot equivalence
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}}, {{0, 1, 0}});
        g.begin_transaction();
        g.apply_insert(0, 2, 0);
        g.rollback_transaction();
        std::vector<uint32_t> leg = g.get_neighbors(0);
        std::vector<uint32_t> buf;
        g.get_neighbors_into(0, buf);
        std::vector<uint32_t> mrg;
        g.get_neighbors_merged_into(0, mrg);
        require(leg == (std::vector<uint32_t>{1}), "MG-G20: Legacy 1");
        require(buf == (std::vector<uint32_t>{1}), "MG-G20: Buffer 1");
        require(mrg == (std::vector<uint32_t>{1}), "MG-G20: Merge 1");
    }

    // MG-G21: Repeated output reuse with varying capacities
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}}, {{0, 1, 0}, {0, 2, 0}, {0, 3, 0}, {0, 4, 0}});
        std::vector<uint32_t> out;
        g.get_neighbors_merged_into(0, out);
        require(out.size() == 4, "MG-G21: 4 neighbors");
        const size_t cap = out.capacity();
        g.get_neighbors_merged_into(1, out);
        require(out.size() == 1 && out[0] == 0, "MG-G21: 1 neighbor");
        require(out.capacity() == cap, "MG-G21: Capacity retained across reuse");
    }

    // MG-G22: High-degree adjacency (1024 base + 1024 overlay interleaved = 2048)
    {
        ApxAuthoritativeGraph g;
        std::vector<Vertex> verts;
        std::vector<Edge> base_edges;
        verts.emplace_back(0, 0);
        for (uint32_t i = 1; i <= 2048; ++i) {
            verts.emplace_back(i, 0);
            if (i % 2 == 1) { // odd IDs in base
                base_edges.emplace_back(0, i, 0);
            }
        }
        g.load_baseline(verts, base_edges);
        for (uint32_t i = 1; i <= 2048; ++i) {
            if (i % 2 == 0) { // even IDs in overlay
                g.begin_transaction();
                g.apply_insert(0, i, 0);
                g.commit_transaction();
            }
        }
        std::vector<uint32_t> out;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, out, &mc);
        require(out.size() == 2048, "MG-G22: High degree count matches 2048");
        for (uint32_t i = 0; i < 2048; ++i) {
            require(out[i] == i + 1, "MG-G22: High degree strictly sorted");
        }
        require(mc.neighbor_merge_linear_calls == 1, "MG-G22: Linear merge called for interleaved high degree");
    }

    // MG-G23: Sparse external IDs
    {
        ApxTypedCodec codec;
        std::vector<Vertex> verts;
        for (int64_t ext : {-9999, -50, 0, 100, 500000}) {
            verts.emplace_back(codec.intern_data_vertex(ext), 0);
        }
        ApxAuthoritativeGraph g;
        g.load_baseline(verts, {{0, 1, 0}, {0, 3, 0}});
        std::vector<uint32_t> out;
        g.get_neighbors_merged_into(0, out);
        require(out == (std::vector<uint32_t>{1, 3}), "MG-G23: Sparse external IDs");
    }

    // MG-G24: Typed and Unicode edge labels
    {
        ApxTypedCodec codec;
        const auto l_str = codec.intern_edge_label(TypedLabel::from_string(u8"đường"));
        const auto l_int = codec.intern_edge_label(TypedLabel::from_int(0));
        require(l_str != l_int, "MG-G24: Typed labels distinct");
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}}, {{0, 1, l_str}, {0, 2, l_int}});
        std::vector<uint32_t> out;
        g.get_neighbors_merged_into(0, out);
        require(out == (std::vector<uint32_t>{1, 2}), "MG-G24: Unicode and typed neighbors");
        require(g.get_edge_label(0, 1).label == l_str, "MG-G24: Unicode label preserved");
    }

    // -------------------------------------------------------------------------
    // Fallback and Prerequisite Violations (Section XXII)
    // -------------------------------------------------------------------------

    // 1. Unsorted base: [5, 1, 3] and sorted overlay: [2, 4]
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}, {5, 0}}, {});
        uint32_t base[3] = {5, 1, 3};
        uint32_t overlay[2] = {2, 4};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 3, overlay, 2, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3, 4, 5}), "Fallback unsorted base");
        require(mc.neighbor_merge_fallback_calls == 1, "Fallback counted for unsorted base");
    }

    // 2. Sorted base: [1, 3, 5] and unsorted overlay: [6, 2, 4]
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}, {5, 0}, {6, 0}}, {});
        uint32_t base[3] = {1, 3, 5};
        uint32_t overlay[3] = {6, 2, 4};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 3, overlay, 3, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3, 4, 5, 6}), "Fallback unsorted overlay");
        require(mc.neighbor_merge_fallback_calls == 1, "Fallback counted for unsorted overlay");
    }

    // 3. Both ranges unsorted: base [5, 1], overlay [4, 2]
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}, {5, 0}}, {});
        uint32_t base[2] = {5, 1};
        uint32_t overlay[2] = {4, 2};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 2, overlay, 2, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 4, 5}), "Fallback both unsorted");
        require(mc.neighbor_merge_fallback_calls == 1, "Fallback counted for both unsorted");
    }

    // 4. Duplicate within base: [1, 2, 2, 3]
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {});
        uint32_t base[4] = {1, 2, 2, 3};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 4, nullptr, 0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3}), "Fallback duplicate base");
        require(mc.neighbor_merge_fallback_calls == 1, "Fallback counted for duplicate base");
    }

    // 5. Duplicate within overlay: [2, 3, 3, 4]
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}}, {});
        uint32_t overlay[4] = {2, 3, 3, 4};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, overlay, 4, out, &mc);
        require(out == (std::vector<uint32_t>{2, 3, 4}), "Fallback duplicate overlay");
        require(mc.neighbor_merge_fallback_calls == 1, "Fallback counted for duplicate overlay");
    }

    // 6. Fused guard late violation: base [1, 5, 3], overlay [2, 4]
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}, {5, 0}}, {});
        uint32_t base[3] = {1, 5, 3};
        uint32_t overlay[2] = {2, 4};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 3, overlay, 2, out, &mc);
        require(out == (std::vector<uint32_t>{1, 2, 3, 4, 5}), "Fused guard late violation fallback");
        require(mc.neighbor_merge_fallback_calls == 1, "Late violation counted as fallback");
    }

    // 7. Filtered element violation: base [10, 5, 20] with 10 tombstoned
    {
        ApxAuthoritativeGraph g;
        std::vector<Vertex> verts;
        for (uint32_t i = 0; i <= 20; ++i) verts.emplace_back(i, 0);
        g.load_baseline(verts, {{0, 10, 0}});
        g.begin_transaction();
        g.apply_delete(0, 10, 0);
        g.commit_transaction();
        require(g.is_in_tombstones(0, 10), "10 is tombstoned");
        uint32_t base[3] = {10, 5, 20};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 3, nullptr, 0, out, &mc);
        require(out == (std::vector<uint32_t>{5, 20}), "Filtered element violation fallback");
        require(mc.neighbor_merge_fallback_calls == 1, "Filtered element violation counted as fallback");
    }

    // 8. Fast-path prerequisite failure: base [3, 1], overlay=0, no tombstones
    {
        ApxAuthoritativeGraph g;
        std::vector<Vertex> verts;
        for (uint32_t i = 0; i <= 3; ++i) verts.emplace_back(i, 0);
        g.load_baseline(verts, {});
        uint32_t base[2] = {3, 1};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 2, nullptr, 0, out, &mc);
        require(out == (std::vector<uint32_t>{1, 3}), "Fast path prerequisite fallback");
        require(mc.neighbor_merge_fallback_calls == 1, "Fast path unsorted counted as fallback");
    }

    // 9. Defensive equality handling: base [1, 3, 5], overlay [3, 7]
    {
        ApxAuthoritativeGraph g;
        std::vector<Vertex> verts;
        for (uint32_t i = 0; i <= 7; ++i) verts.emplace_back(i, 0);
        g.load_baseline(verts, {});
        uint32_t base[3] = {1, 3, 5};
        uint32_t overlay[2] = {3, 7};
        std::vector<uint32_t> out;
        EngineCounters mc;
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 3, overlay, 2, out, &mc);
        require(out == (std::vector<uint32_t>{1, 3, 5, 7}), "Equality deduplication");
        require(mc.neighbor_merge_linear_calls == 1, "Linear merge with equality");
    }

    // 10. Structural fail-closed: base_count > 0 with null pointer
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}}, {});
        std::vector<uint32_t> out;
        bool caught = false;
        try {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 5, nullptr, 0, out);
        } catch (const InvariantViolationException&) {
            caught = true;
        }
        require(caught, "Structural null pointer base threw InvariantViolationException");
    }

    // 11. Structural fail-closed: overlay_count > 0 with null pointer
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}}, {});
        std::vector<uint32_t> out;
        bool caught = false;
        try {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, nullptr, 3, out);
        } catch (const InvariantViolationException&) {
            caught = true;
        }
        require(caught, "Structural null pointer overlay threw InvariantViolationException");
    }

    // -------------------------------------------------------------------------
    // MG-G19B: Event-bearing tentative INSERT witness & query event execution
    // -------------------------------------------------------------------------
    {
        ApxAuthoritativeGraph g;
        ApxTypedCodec codec;
        codec.intern_vertex_label(TypedLabel::from_int(0));
        codec.intern_edge_label(TypedLabel::from_int(0));
        std::vector<Vertex> verts;
        for (uint32_t i = 0; i < 4; ++i) {
            verts.emplace_back(codec.intern_data_vertex(i), 0);
        }
        // Baseline graph: 4 vertices, edges (1, 3) and (2, 3), missing (0, 1) and (0, 2)
        g.load_baseline(verts, {{1, 3, 0}, {2, 3, 0}});
        const std::vector<QueryEdge> c4{{0, 1, 0}, {0, 2, 0}, {1, 3, 0}, {2, 3, 0}};
        std::vector<uint32_t> qorder = {0, 1, 2, 3};
        ApxQueryPlan plan;
        plan.build(4, std::vector<uint32_t>(4, 0), c4, qorder, 1);

        // 1. Begin transaction
        g.begin_transaction();

        // 2. Perform tentative INSERT of edge (0, 1)
        g.apply_insert(0, 1, 0);

        // 3. Keep transaction active! Verify BOTH endpoints see tentative neighbor in merged view
        std::vector<uint32_t> nbrs0, nbrs1;
        g.get_neighbors_merged_into(0, nbrs0);
        g.get_neighbors_merged_into(1, nbrs1);
        require(std::find(nbrs0.begin(), nbrs0.end(), 1) != nbrs0.end(), "MG-G19B: Endpoint 0 sees tentative neighbor 1 while tx active");
        require(std::find(nbrs1.begin(), nbrs1.end(), 0) != nbrs1.end(), "MG-G19B: Endpoint 1 sees tentative neighbor 0 while tx active");

        // 4. Execute actual query event WHILE tentative overlay remains active
        ApxTupleSink sink(codec, qorder);
        sink.start_step();
        EngineCounters step_counters;
        ApxM2EventEngine m2_engine;
        m2_engine.search_event(plan, g, 0, 1, 0, sink, step_counters, nullptr, NlfMode::NONE, nullptr, nullptr, NeighborMode::MERGE);
        auto published = sink.commit_step();

        // 5. Capture event output and verify EXACT expected 8 canonical ENTERED tuples
        require(published.size() == 8, "MG-G19B: Exactly 8 tuples emitted while tentative INSERT is active");

        const std::vector<std::vector<int64_t>> expected_tuples = {
            {0, 1, 2, 3},
            {1, 0, 3, 2},
            {0, 2, 1, 3},
            {1, 3, 0, 2},
            {2, 0, 3, 1},
            {3, 1, 2, 0},
            {2, 3, 0, 1},
            {3, 2, 1, 0}
        };
        for (size_t i = 0; i < 8; ++i) {
            require(published[i] == expected_tuples[i], "MG-G19B: Tuple matches canonical expected order and endpoint identity");
        }

        // 6. Rollback transaction
        g.rollback_transaction();

        // 7. Verify BOTH endpoints no longer see tentative edge after rollback
        nbrs0.clear();
        nbrs1.clear();
        g.get_neighbors_merged_into(0, nbrs0);
        g.get_neighbors_merged_into(1, nbrs1);
        require(std::find(nbrs0.begin(), nbrs0.end(), 1) == nbrs0.end(), "MG-G19B: Endpoint 0 does not see 1 after rollback");
        require(std::find(nbrs1.begin(), nbrs1.end(), 0) == nbrs1.end(), "MG-G19B: Endpoint 1 does not see 0 after rollback");

        // 8. Execute query again on rolled-back graph: verify NO tentative INSERT emissions remain
        ApxTupleSink sink_after(codec, qorder);
        sink_after.start_step();
        EngineCounters step_counters_after;
        m2_engine.search_event(plan, g, 0, 1, 0, sink_after, step_counters_after, nullptr, NlfMode::NONE, nullptr, nullptr, NeighborMode::MERGE);
        auto published_after = sink_after.commit_step();
        require(published_after.empty(), "MG-G19B: Zero emissions after rollback");
    }

    // -------------------------------------------------------------------------
    // MG-G25: 65,536-neighbor MERGE witness & capacity reuse sequence
    // -------------------------------------------------------------------------
    {
        ApxAuthoritativeGraph g;
        const uint32_t N = 65538;
        std::vector<Vertex> verts;
        verts.reserve(N);
        for (uint32_t i = 0; i < N; ++i) {
            verts.emplace_back(i, 0);
        }
        std::vector<Edge> base_edges;
        base_edges.reserve(32768);
        for (uint32_t i = 1; i <= 32768; ++i) {
            base_edges.push_back({0, 2 * i, 0});
        }
        g.load_baseline(verts, base_edges);

        g.begin_transaction();
        for (uint32_t i = 0; i < 32768; ++i) {
            g.apply_insert(0, 2 * i + 1, 0);
        }
        g.commit_transaction();

        std::vector<uint32_t> reusable_buf;
        EngineCounters mc;
        g.get_neighbors_merged_into(0, reusable_buf, &mc);
        require(reusable_buf.size() == 65536, "MG-G25: 65,536 neighbors merged");
        require(mc.neighbor_merge_linear_calls == 1, "MG-G25: Linear merge called for 65536 neighbors");
        for (uint32_t i = 0; i < 65536; ++i) {
            require(reusable_buf[i] == i + 1, "MG-G25: Strictly sorted order preserved");
        }
        size_t cap = reusable_buf.capacity();
        require(cap >= 65536, "MG-G25: Buffer capacity >= 65536");

        // Capacity reuse on small vertex (vertex 1 has degree 1: neighbor 0)
        g.get_neighbors_merged_into(1, reusable_buf, &mc);
        require(reusable_buf.size() == 1, "MG-G25: Small vertex size 1");
        require(reusable_buf[0] == 0, "MG-G25: Small vertex neighbor 0");
        require(reusable_buf.capacity() == cap, "MG-G25: Capacity preserved on small retrieval");

        // Capacity reuse on empty vertex: vertex 65537 is completely isolated
        g.get_neighbors_merged_into(65537, reusable_buf, &mc);
        require(reusable_buf.empty(), "MG-G25: Empty vertex size 0");
        require(reusable_buf.capacity() == cap, "MG-G25: Capacity preserved on empty retrieval");

        // Capacity reuse on large vertex again
        g.get_neighbors_merged_into(0, reusable_buf, &mc);
        require(reusable_buf.size() == 65536, "MG-G25: Large vertex retrieved again");
        require(reusable_buf.capacity() == cap, "MG-G25: Capacity unchanged without realloc");
    }

    // -------------------------------------------------------------------------
    // SC-01..SC-20: Structural Fail-Closed Witness Matrix (Section XII)
    // -------------------------------------------------------------------------
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}}, {});

        auto assert_invariant_violation = [&](const char* label, auto&& fn) {
            std::vector<uint32_t> out;
            out.push_back(12345); // sentinel to verify clear on fail
            EngineCounters c;
            bool caught = false;
            try {
                fn(out, c);
            } catch (const InvariantViolationException&) {
                caught = true;
            }
            require(caught, label);
            require(out.empty(), "Atomic clear: out vector cleared on exception");
            require(c.neighbor_merge_retrieval_calls == 0, "No counter update on exception");
        };

        // SC-01: Invalid u (not in graph)
        assert_invariant_violation("SC-01: Invalid u rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 9999, nullptr, 0, nullptr, 0, out, &c);
        });

        // SC-02: base_count > 0 with null pointer
        assert_invariant_violation("SC-02: Base count > 0 with null ptr rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 5, nullptr, 0, out, &c);
        });

        // SC-03: overlay_count > 0 with null pointer
        assert_invariant_violation("SC-03: Overlay count > 0 with null ptr rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, nullptr, 3, out, &c);
        });

        // SC-04: Fast Path 2: self-loop in overlay (v == u)
        uint32_t self_loop[1] = {0};
        assert_invariant_violation("SC-04: Fast Path 2 self-loop rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, self_loop, 1, out, &c);
        });

        // SC-05: Fast Path 2: out-of-universe in overlay (v >= 3)
        uint32_t oou[1] = {99};
        assert_invariant_violation("SC-05: Fast Path 2 out-of-universe rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, oou, 1, out, &c);
        });

        // SC-06: Fast Path 2: UINT32_MAX in overlay
        uint32_t sent[1] = {UINT32_MAX};
        assert_invariant_violation("SC-06: Fast Path 2 UINT32_MAX rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, sent, 1, out, &c);
        });

        // SC-07: Fast Path 3: self-loop in base (v == u)
        assert_invariant_violation("SC-07: Fast Path 3 self-loop rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, self_loop, 1, nullptr, 0, out, &c);
        });

        // SC-08: Fast Path 3: out-of-universe in base
        assert_invariant_violation("SC-08: Fast Path 3 out-of-universe rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, oou, 1, nullptr, 0, out, &c);
        });

        // SC-09: Fast Path 3: UINT32_MAX in base
        assert_invariant_violation("SC-09: Fast Path 3 UINT32_MAX rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, sent, 1, nullptr, 0, out, &c);
        });

        // SC-10: Linear merge: self-loop in base
        uint32_t base_self[2] = {0, 2};
        uint32_t over_valid[1] = {1};
        assert_invariant_violation("SC-10: Linear merge self-loop in base rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base_self, 2, over_valid, 1, out, &c);
        });

        // SC-11: Linear merge: self-loop in overlay
        uint32_t base_valid[1] = {1};
        uint32_t over_self[2] = {0, 2};
        assert_invariant_violation("SC-11: Linear merge self-loop in overlay rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base_valid, 1, over_self, 2, out, &c);
        });

        // SC-12: Linear merge: out-of-universe in base
        uint32_t base_oou[2] = {1, 99};
        assert_invariant_violation("SC-12: Linear merge out-of-universe in base rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base_oou, 2, over_valid, 1, out, &c);
        });

        // SC-13: Linear merge: out-of-universe in overlay
        uint32_t over_oou[2] = {1, 99};
        assert_invariant_violation("SC-13: Linear merge out-of-universe in overlay rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base_valid, 1, over_oou, 2, out, &c);
        });

        // SC-14: Linear merge: UINT32_MAX in base
        uint32_t base_sent[2] = {1, UINT32_MAX};
        assert_invariant_violation("SC-14: Linear merge UINT32_MAX in base rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base_sent, 2, over_valid, 1, out, &c);
        });

        // SC-15: Linear merge: UINT32_MAX in overlay
        uint32_t over_sent[2] = {1, UINT32_MAX};
        assert_invariant_violation("SC-15: Linear merge UINT32_MAX in overlay rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base_valid, 1, over_sent, 2, out, &c);
        });

        // SC-16: Fallback: invalid ID in fallback base (unsorted + invalid)
        uint32_t fb_base_inv[2] = {99, 1};
        assert_invariant_violation("SC-16: Fallback invalid base rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, fb_base_inv, 2, nullptr, 0, out, &c);
        });

        // SC-17: Fallback: invalid ID in fallback overlay (unsorted + invalid)
        uint32_t fb_over_inv[2] = {99, 1};
        assert_invariant_violation("SC-17: Fallback invalid overlay rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, fb_over_inv, 2, out, &c);
        });

        // SC-18: Fallback: UINT32_MAX in fallback (unsorted + UINT32_MAX)
        uint32_t fb_sent[2] = {UINT32_MAX, 1};
        assert_invariant_violation("SC-18: Fallback UINT32_MAX rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, fb_sent, 2, out, &c);
        });

        // SC-19: Tail element invalid ID in base
        uint32_t tail_base_inv[2] = {1, 99};
        assert_invariant_violation("SC-19: Tail base invalid rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, tail_base_inv, 2, nullptr, 0, out, &c);
        });

        // SC-20: Tail element invalid ID in overlay
        uint32_t tail_over_inv[2] = {1, 99};
        assert_invariant_violation("SC-20: Tail overlay invalid rejected", [&](auto& out, auto& c) {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, tail_over_inv, 2, out, &c);
        });
    }

#ifdef APX_NEIGHBOR_TEST_TRACE
    // -------------------------------------------------------------------------
    // Section XVI: Partial merge and fallback failure test seams
    // -------------------------------------------------------------------------
    {
        ApxAuthoritativeGraph g;
        g.load_baseline({{0, 0}, {1, 0}, {2, 0}, {3, 0}}, {});
        uint32_t base[2] = {1, 3};
        uint32_t overlay[1] = {2};
        std::vector<uint32_t> out;

        // 1. Fail after first emission in linear merge
        ApxAuthoritativeGraph::set_merge_fault_hook(MergeFaultHook::FAIL_AFTER_FIRST_EMISSION);
        bool caught1 = false;
        try {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, base, 2, overlay, 1, out);
        } catch (const std::runtime_error&) {
            caught1 = true;
        }
        require(caught1, "FAIL_AFTER_FIRST_EMISSION threw runtime_error");
        require(out.empty(), "FAIL_AFTER_FIRST_EMISSION out vector is cleared");

        // 2. Fail during fast path
        ApxAuthoritativeGraph::set_merge_fault_hook(MergeFaultHook::FAIL_DURING_FAST_PATH);
        bool caught2 = false;
        try {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, nullptr, 0, overlay, 1, out);
        } catch (const std::runtime_error&) {
            caught2 = true;
        }
        require(caught2, "FAIL_DURING_FAST_PATH threw runtime_error");
        require(out.empty(), "FAIL_DURING_FAST_PATH out vector is cleared");

        // 3. Fail during fallback
        ApxAuthoritativeGraph::set_merge_fault_hook(MergeFaultHook::FAIL_DURING_FALLBACK);
        uint32_t unsorted[2] = {3, 1};
        bool caught3 = false;
        try {
            ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, unsorted, 2, nullptr, 0, out);
        } catch (const std::runtime_error&) {
            caught3 = true;
        }
        require(caught3, "FAIL_DURING_FALLBACK threw runtime_error");
        require(out.empty(), "FAIL_DURING_FALLBACK out vector is cleared");

        // Reset hook and verify subsequent call succeeds normally
        ApxAuthoritativeGraph::set_merge_fault_hook(MergeFaultHook::NONE);
        out.clear();
        ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges(g, 0, unsorted, 2, nullptr, 0, out);
        require(out.size() == 2 && out[0] == 1 && out[1] == 3, "merge_neighbor_ranges succeeds normally after resetting hook to NONE");
    }
#endif

    std::cout << "merge_direct_and_fallback_tests passed. All MG-G01..G25 scenarios, tentative insert, and fallback guards verified.\n";
}

} // namespace

int run_neighbor_buffer_tests(const char* suite_name) {
    try {
        const std::string suite(suite_name);
        if (suite == "pool") {
            pool_test();
            return 0;
        }
        if (suite == "graph") {
            graph_test();
            merge_direct_and_fallback_tests();
            return 0;
        }
        if (suite == "merge") {
            merge_direct_and_fallback_tests();
            return 0;
        }
#ifdef APX_NEIGHBOR_TEST_TRACE
        if (suite == "trace") {
            trace_test();
            return 0;
        }
#endif
        if (suite == "all") {
            pool_test();
            graph_test();
            merge_direct_and_fallback_tests();
#ifdef APX_NEIGHBOR_TEST_TRACE
            trace_test();
#endif
            return 0;
        }
        std::cerr << "Unsupported neighbor buffer suite: " << suite << '\n';
    } catch (const std::exception& ex) {
        std::cerr << "Neighbor buffer verification failed: " << ex.what() << '\n';
    }
    return 1;
}

} // namespace rapidflow_apx
