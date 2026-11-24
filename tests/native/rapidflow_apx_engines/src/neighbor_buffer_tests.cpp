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
        require(out == expected && graph.get_neighbors(u) == expected, "Ordered neighbor output differs");
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
    for (const auto mode : {NeighborMode::LEGACY, NeighborMode::BUFFER}) {
        const bool buffer = mode == NeighborMode::BUFFER;
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
        std::vector<std::string> buffer_path;
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
            require(t.pool_frames == (buffer ? frames : 0), "Incorrect pool size or disabled allocation");
            std::ostringstream row;
            row << static_cast<int>(t.point) << ' ' << t.depth << ' ' << t.assigned_count << ' '
                << t.missing_count << ' ' << t.edge << ' ' << t.candidate << ' ';
            append(row, t.mapping);
            for (auto s : t.status) row << static_cast<int>(s);
            if (t.point == Point::FILLED) {
                row << " neighbors=";
                append(row, *t.neighbors);
            }
            (buffer ? buffer_path : legacy_path).push_back(row.str());

            if (t.point == Point::ANCHOR) ++c.anchors;
            if (t.point == Point::CANDIDATE) ++c.candidates;
            if (t.point == Point::EXTEND) ++c.extensions;
            if (t.point == Point::DFS) {
                ++c.states;
                c.max_depth = std::max(c.max_depth, t.depth);
                if (t.depth == 0) {
                    require(t.assigned_count == 2 && t.missing_count == 0, "Invalid anchor root");
                }
                if (buffer) {
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
                    if (buffer && missing && !p.contents.empty() && c.missing == 1) {
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
                if (buffer) {
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
        if (!buffer) {
            require(step.step_counters.neighbor_buffer_retrieval_calls == 0 && seen_search == 0, "Legacy buffer operations");
            legacy = step;
            continue;
        }
        require(buffer_path == legacy_path, "Ordered DFS trace changed");
        if (saved_trace) {
            for (const auto& [label, path] : {std::pair{"LEGACY", &legacy_path}, std::pair{"BUFFER", &buffer_path}}) {
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
    for (auto op : {UpdateOp::INSERT, UpdateOp::DELETE}) {
        const bool insert = op == UpdateOp::INSERT;
        for (int fault = 0; fault < 5; ++fault) {
            std::vector<Edge> start{{1,3,0},{2,3,0}};
            if (!insert) start.emplace_back(0,1,0);
            SearchFixture f(4, c4, 1, 4, start);
            ApxStreamingEngine engine(f.graph, f.codec, f.sink, f.plan, EngineType::M2, 500000,
                                      NlfMode::NONE, 0, NeighborMode::BUFFER);
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
            std::cout << "injected op=" << (insert ? "INSERT" : "DELETE") << " seam=" << fault
                      << " emissions_before_abort=" << emissions << " discarded=" << failed.step_counters.discarded_emissions
                      << " restored=true fresh_retry=" << fresh << " poisoned=" << poison << '\n';
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
            return 0;
        }
#ifdef APX_NEIGHBOR_TEST_TRACE
        if (suite == "trace") {
            trace_test();
            return 0;
        }
#endif
        std::cerr << "Unsupported neighbor buffer suite: " << suite << '\n';
    } catch (const std::exception& ex) {
        std::cerr << "Neighbor buffer verification failed: " << ex.what() << '\n';
    }
    return 1;
}

} // namespace rapidflow_apx
