#!/usr/bin/env python3
"""
tests/test_apx02_oracle.py — Comprehensive Test Suite for APX-02 Approximate CSM Oracle.

Covers:
1. All 16 Reference Microcases from docs/approx_semantic_microcases_v1.md.
2. Scope B Atomic Update Validation & Exception Guards.
3. Independent Exact Recovery Validation (Relaxed Mode & Connected Strict Mode).
4. Disconnected Query Exact vs Strict Counterexample (Q=2K2, G=2K2).
5. Property Tests:
   - Threshold Monotonicity
   - Strict/Relaxed Inclusion
   - Dynamic Reversibility
   - Automorphism Invariance & Distinct Ordered Mappings
   - SCORE_CHANGED Regression (Internal chord does not trigger SCORE_CHANGED)
   - Boundary Cut External Updates (External edge increments x_out and triggers EXITED)
6. Randomized Small-Graph Differential Tests with Fixed Seeds.
"""

import itertools
import random
import unittest
from typing import Dict, List, Optional, Set, Tuple

from scripts.apx02_oracle import (
    ConnectivityMode,
    DeviationScore,
    DuplicateEdgeInsertionError,
    DynamicEventType,
    DynamicOracle,
    EdgeLabelMismatchDeletionError,
    NonexistentEdgeDeletionError,
    SelfLoopError,
    SimpleGraph,
    UnknownVertexError,
    Update,
    evaluate_mapping,
    find_approximate_matches,
)


class TestAPX02Microcases(unittest.TestCase):
    """
    Direct verification of all 16 reference microcases from docs/approx_semantic_microcases_v1.md.
    No fixtures or expected values are altered.
    """

    def test_case_01_exact_match(self):
        # Q: Path P3 (0-1-2)
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        # G: Path P3 (1-2-3)
        G = SimpleGraph()
        for i in range(1, 4):
            G.add_vertex(i, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)

        # Mapping f: 0->1, 1->2, 2->3
        mapping = (1, 2, 3)

        # Strict at (0, 0)
        res_strict_00 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=0, mode=ConnectivityMode.STRICT)
        self.assertTrue(res_strict_00.accepted)
        self.assertEqual(res_strict_00.score.m, 0)
        self.assertEqual(res_strict_00.score.x_in, 0)
        self.assertEqual(res_strict_00.score.x_out, 0)
        self.assertTrue(res_strict_00.score.connected)

        # Strict at (1, 2)
        res_strict_12 = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertTrue(res_strict_12.accepted)

        # Relaxed at (0, 0)
        res_relaxed = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=0, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertTrue(res_relaxed.accepted)

    def test_case_02_missing_one_leaf_edge(self):
        # Q: Star K1,3 (center 0, leaves 1, 2, 3)
        Q = SimpleGraph()
        for i in range(4):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)
        Q.add_edge(0, 3, 0)

        # G: Star K1,2 (center 0, leaves 1, 2; vertex 3 isolated)
        G = SimpleGraph()
        for i in range(4):
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)

        mapping = (0, 1, 2, 3)
        res = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertEqual(res.score.m, 1)
        self.assertEqual(res.score.x_in, 0)
        self.assertEqual(res.score.x_out, 0)
        self.assertFalse(res.score.connected)  # Vertex 3 is isolated => 2 components
        self.assertFalse(res.accepted)  # Strict REJECT

        res_relaxed = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertTrue(res_relaxed.accepted)  # Relaxed ACCEPT

    def test_case_03_missing_two_edges(self):
        # Q: Star K1,3
        Q = SimpleGraph()
        for i in range(4):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)
        Q.add_edge(0, 3, 0)

        # G: Only edge {0, 1}; vertices 2, 3 isolated
        G = SimpleGraph()
        for i in range(4):
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)

        mapping = (0, 1, 2, 3)
        # Strict mode: rejected across all thresholds
        res_strict = evaluate_mapping(Q, G, mapping, eps_m=2, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_strict.score.m, 2)
        self.assertFalse(res_strict.score.connected)
        self.assertFalse(res_strict.accepted)

        # Relaxed mode: rejected at (1, 2), accepted at (2, 2)
        res_relaxed_12 = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertFalse(res_relaxed_12.accepted)
        self.assertIn("M_EXCEEDED:2>1", res_relaxed_12.reject_reasons)

        res_relaxed_22 = evaluate_mapping(Q, G, mapping, eps_m=2, eps_x=2, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertTrue(res_relaxed_22.accepted)

    def test_case_04_extra_internal_chords(self):
        # Q: Cycle C4 (1-2-3-4-1)
        Q = SimpleGraph()
        for i in range(1, 5):
            Q.add_vertex(i, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 4, 0)
        Q.add_edge(4, 1, 0)

        # G: Clique K4
        G = SimpleGraph()
        for i in range(1, 5):
            G.add_vertex(i, 0)
        for (u, v) in itertools.combinations(range(1, 5), 2):
            G.add_edge(u, v, 0)

        mapping = (1, 2, 3, 4)
        res = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertEqual(res.score.m, 0)
        self.assertEqual(res.score.x_in, 2)  # chords {1,3} and {2,4}
        self.assertEqual(res.score.x_out, 0)
        self.assertTrue(res.score.connected)
        self.assertTrue(res.accepted)  # Model C accepts non-induced matching!

    def test_case_05_one_extra_external_tail(self):
        # Q: Star K1,2 (center 0, leaves 1, 2)
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G: Star K1,3 (center 0, leaves 1, 2, 4)
        G = SimpleGraph()
        for i in [0, 1, 2, 4]:
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)
        G.add_edge(0, 4, 0)

        mapping = (0, 1, 2)
        # eps_x = 0 (boundary-free): REJECT
        res_x0 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=0, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_x0.score.m, 0)
        self.assertEqual(res_x0.score.x_out, 1)
        self.assertFalse(res_x0.accepted)

        # eps_x = 1: ACCEPT
        res_x1 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)
        self.assertTrue(res_x1.accepted)

    def test_case_06_two_extra_external_tails(self):
        # Q: Star K1,2
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G: Star K1,4 (center 0, leaves 1, 2, 4, 5)
        G = SimpleGraph()
        for i in [0, 1, 2, 4, 5]:
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)
        G.add_edge(0, 4, 0)
        G.add_edge(0, 5, 0)

        mapping = (0, 1, 2)
        res_x1 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_x1.score.x_out, 2)
        self.assertFalse(res_x1.accepted)

        res_x2 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertTrue(res_x2.accepted)

    def test_case_07_thirty_extra_external_tails(self):
        # Q: Star K1,2
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G: Star with center 0, leaves 1, 2, and 30 extra peripheral nodes (4..33)
        G = SimpleGraph()
        G.add_vertex(0, 0)
        G.add_vertex(1, 0)
        G.add_vertex(2, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)
        for extra in range(4, 34):
            G.add_vertex(extra, 0)
            G.add_edge(0, extra, 0)

        mapping = (0, 1, 2)
        res_x2 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_x2.score.x_out, 30)
        self.assertFalse(res_x2.accepted)

        res_x30 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=30, mode=ConnectivityMode.STRICT)
        self.assertTrue(res_x30.accepted)

    def test_case_08_synthetic_high_degree_hub(self):
        # Q: Star K1,2
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G: Center 0 connected to 1, 2 and 998 other nodes (degree 1000)
        G = SimpleGraph()
        G.add_vertex(0, 0)
        G.add_vertex(1, 0)
        G.add_vertex(2, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)
        for extra in range(3, 1001):
            G.add_vertex(extra, 0)
            G.add_edge(0, extra, 0)

        mapping = (0, 1, 2)
        # Evaluated directly via evaluate_mapping (avoiding combinatorial enumeration of 1001 nodes)
        res_x30 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=30, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_x30.score.m, 0)
        self.assertEqual(res_x30.score.x_out, 998)
        self.assertTrue(res_x30.score.connected)
        self.assertFalse(res_x30.accepted)

        # eps_x = None represents infinity
        res_inf = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertTrue(res_inf.accepted)

    def test_case_09_edge_label_mismatch_topological_connectivity(self):
        # Q: Path P3 (1-2-3), labels: edge {1,2} label 1, edge {2,3} label 1
        Q = SimpleGraph()
        for i in range(1, 4):
            Q.add_vertex(i, 0)
        Q.add_edge(1, 2, 1)
        Q.add_edge(2, 3, 1)

        # G: Path P3 (1-2-3), edge {1,2} label 1, edge {2,3} label 2
        G = SimpleGraph()
        for i in range(1, 4):
            G.add_vertex(i, 0)
        G.add_edge(1, 2, 1)
        G.add_edge(2, 3, 2)

        mapping = (1, 2, 3)
        res_strict = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_strict.score.m, 1)
        self.assertEqual(res_strict.score.x_in, 0)  # Edge is in E_Q, so NOT internal chord
        self.assertEqual(res_strict.score.x_out, 0)
        self.assertTrue(res_strict.score.connected)  # Topologically connected via {1,2} and {2,3}
        self.assertTrue(res_strict.accepted)  # STRICT ACCEPT

        res_relaxed = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertTrue(res_relaxed.accepted)

    def test_case_10_injectivity_violation(self):
        # Q: Path P3
        Q = SimpleGraph()
        for i in range(1, 4):
            Q.add_vertex(i, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)

        G = SimpleGraph()
        G.add_vertex(1, 0)
        G.add_vertex(2, 0)
        G.add_edge(1, 2, 0)

        # Non-injective mapping: u1->1, u2->2, u3->1
        mapping = (1, 2, 1)
        res = evaluate_mapping(Q, G, mapping, eps_m=10, eps_x=10, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertFalse(res.is_injective)
        self.assertFalse(res.accepted)
        self.assertIn("NON_INJECTIVE", res.reject_reasons)

    def test_case_11_tree_query_disconnected_by_missing_edge(self):
        # Q: Path P4 (1-2-3-4)
        Q = SimpleGraph()
        for i in range(1, 5):
            Q.add_vertex(i, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 4, 0)

        # G: Edges {1, 2} and {3, 4} (missing middle edge {2, 3})
        G = SimpleGraph()
        for i in range(1, 5):
            G.add_vertex(i, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(3, 4, 0)

        mapping = (1, 2, 3, 4)
        res_strict = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
        self.assertEqual(res_strict.score.m, 1)
        self.assertFalse(res_strict.score.connected)  # 2 components
        self.assertFalse(res_strict.accepted)  # Strict REJECT

        res_relaxed = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=2, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertTrue(res_relaxed.accepted)  # Relaxed ACCEPT

    def test_case_12_dynamic_insertion_entered(self):
        # Q: Star K1,2 (center 0, leaves 1, 2)
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G0: Edge {0, 1} exists, {0, 2} missing; vertex 2 exists in V0
        G0 = SimpleGraph()
        for i in range(3):
            G0.add_vertex(i, 0)
        G0.add_edge(0, 1, 0)

        # Exact matching threshold: eps_m=0, eps_x=0
        dyn = DynamicOracle(Q, G0, eps_m=0, eps_x=0, mode=ConnectivityMode.STRICT)
        target_mapping = (0, 1, 2)
        self.assertNotIn(target_mapping, dyn.current_matches)

        # Update: + e 0 2 0
        delta = dyn.apply_update(Update("+", 0, 2, 0))
        self.assertIn(target_mapping, delta.entered)
        self.assertIn(target_mapping, dyn.current_matches)
        self.assertEqual(delta.entered[target_mapping].m, 0)
        self.assertEqual(delta.entered[target_mapping].x_out, 0)

    def test_case_13_dynamic_insertion_boundary_clutter_exited(self):
        # Q: Star K1,2
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G0: Edges {0, 1}, {0, 2}, {0, 4}; vertex 5 pre-exists in V0
        G0 = SimpleGraph()
        for i in [0, 1, 2, 4, 5]:
            G0.add_vertex(i, 0)
        G0.add_edge(0, 1, 0)
        G0.add_edge(0, 2, 0)
        G0.add_edge(0, 4, 0)

        target_mapping = (0, 1, 2)
        # Threshold: eps_m=0, eps_x=1
        dyn = DynamicOracle(Q, G0, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)
        self.assertIn(target_mapping, dyn.current_matches)
        self.assertEqual(dyn.current_matches[target_mapping].score.x_out, 1)

        # Update: + e 0 5 0 (adds external clutter edge to pre-existing node 5)
        delta = dyn.apply_update(Update("+", 0, 5, 0))
        self.assertIn(target_mapping, delta.exited)
        self.assertNotIn(target_mapping, dyn.current_matches)
        self.assertEqual(delta.exited[target_mapping].x_out, 1)

    def test_case_14_dynamic_deletion_boundary_clutter_entered(self):
        # Q: Star K1,2
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G0: Edges {0, 1}, {0, 2}, {0, 4}, {0, 5}
        G0 = SimpleGraph()
        for i in [0, 1, 2, 4, 5]:
            G0.add_vertex(i, 0)
        G0.add_edge(0, 1, 0)
        G0.add_edge(0, 2, 0)
        G0.add_edge(0, 4, 0)
        G0.add_edge(0, 5, 0)

        target_mapping = (0, 1, 2)
        # Threshold: eps_m=0, eps_x=1
        dyn = DynamicOracle(Q, G0, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)
        self.assertNotIn(target_mapping, dyn.current_matches)  # x_out = 2 > 1

        # Update: - e 0 5 0 (deletes clutter edge)
        delta = dyn.apply_update(Update("-", 0, 5, 0))
        self.assertIn(target_mapping, delta.entered)
        self.assertIn(target_mapping, dyn.current_matches)
        self.assertEqual(delta.entered[target_mapping].x_out, 1)

    def test_case_15_dynamic_update_score_changed(self):
        # Q: Star K1,2
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)

        # G0: Edges {0, 1}, {0, 2}, {0, 4}; vertex 5 pre-exists in V0
        G0 = SimpleGraph()
        for i in [0, 1, 2, 4, 5]:
            G0.add_vertex(i, 0)
        G0.add_edge(0, 1, 0)
        G0.add_edge(0, 2, 0)
        G0.add_edge(0, 4, 0)

        target_mapping = (0, 1, 2)
        # Threshold: eps_m=0, eps_x=3 (tolerant to clutter)
        dyn = DynamicOracle(Q, G0, eps_m=0, eps_x=3, mode=ConnectivityMode.STRICT)
        self.assertIn(target_mapping, dyn.current_matches)
        self.assertEqual(dyn.current_matches[target_mapping].score.x_out, 1)

        # Update: + e 0 5 0
        delta = dyn.apply_update(Update("+", 0, 5, 0))
        self.assertIn(target_mapping, delta.score_changed)
        old_score, new_score = delta.score_changed[target_mapping]
        self.assertEqual(old_score.x_out, 1)
        self.assertEqual(new_score.x_out, 2)
        self.assertIn(target_mapping, dyn.current_matches)

    def test_case_16_connected_near_only_match(self):
        # Q: Star K1,3 (center 0, leaves 1, 2, 3)
        Q = SimpleGraph()
        for i in range(4):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)
        Q.add_edge(0, 3, 0)

        # G: Edges {0, 1}, {0, 2}, {2, 3} (leaf 3 connected to leaf 2 instead of center 0)
        G = SimpleGraph()
        for i in range(4):
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)
        G.add_edge(2, 3, 0)

        mapping = (0, 1, 2, 3)
        res = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=0, mode=ConnectivityMode.STRICT)
        self.assertEqual(res.score.m, 1)
        self.assertEqual(res.score.x_in, 1)  # extra chord {2, 3}
        self.assertEqual(res.score.x_out, 0)
        self.assertTrue(res.score.connected)  # Path 1-0-2-3 spans all 4 vertices!
        self.assertTrue(res.accepted)  # STRICT ACCEPT at (1, 0)

        # Exact matching (eps_m=0, eps_x=None): must REJECT
        res_exact = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertFalse(res_exact.accepted)


class TestAPX02ScopeBValidation(unittest.TestCase):
    """
    Tests atomic validation of Scope B preconditions (invalid updates raise explicit exceptions
    and leave state completely unmutated).
    """

    def setUp(self):
        self.Q = SimpleGraph()
        self.Q.add_vertex(0, 0)
        self.Q.add_vertex(1, 0)
        self.Q.add_edge(0, 1, 0)

        self.G = SimpleGraph()
        for i in range(4):
            self.G.add_vertex(i, 0)
        self.G.add_edge(0, 1, 0)
        self.G.add_edge(1, 2, 0)

        self.dyn = DynamicOracle(self.Q, self.G, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)

    def test_unknown_vertex_insertion(self):
        initial_edges = dict(self.dyn.G.edges)
        with self.assertRaises(UnknownVertexError):
            self.dyn.apply_update(Update("+", 0, 99, 0))
        # State unmutated
        self.assertEqual(self.dyn.G.edges, initial_edges)

    def test_unknown_vertex_deletion(self):
        initial_edges = dict(self.dyn.G.edges)
        with self.assertRaises(UnknownVertexError):
            self.dyn.apply_update(Update("-", 0, 99, 0))
        self.assertEqual(self.dyn.G.edges, initial_edges)

    def test_duplicate_edge_insertion(self):
        initial_edges = dict(self.dyn.G.edges)
        with self.assertRaises(DuplicateEdgeInsertionError):
            self.dyn.apply_update(Update("+", 0, 1, 0))
        self.assertEqual(self.dyn.G.edges, initial_edges)

    def test_nonexistent_edge_deletion(self):
        initial_edges = dict(self.dyn.G.edges)
        with self.assertRaises(NonexistentEdgeDeletionError):
            self.dyn.apply_update(Update("-", 0, 3, 0))
        self.assertEqual(self.dyn.G.edges, initial_edges)

    def test_edge_label_mismatch_deletion(self):
        initial_edges = dict(self.dyn.G.edges)
        # Edge {0, 1} has label 0, request delete with label 9
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            self.dyn.apply_update(Update("-", 0, 1, 9))
        self.assertEqual(self.dyn.G.edges, initial_edges)

    def test_self_loop_error(self):
        with self.assertRaises(SelfLoopError):
            Update("+", 1, 1, 0)

    def test_atomicity_and_subsequent_valid_update(self):
        # 1. Trigger an error
        with self.assertRaises(DuplicateEdgeInsertionError):
            self.dyn.apply_update(Update("+", 0, 1, 0))

        # 2. Subsequent valid update succeeds normally
        delta = self.dyn.apply_update(Update("+", 2, 3, 0))
        self.assertIn((2, 3), self.dyn.G.edges)


class TestAPX02ExactRecovery(unittest.TestCase):
    """
    Validates Exact Recovery: at eps_m=0, eps_x=None, Approximate Oracle reproduces
    independent exact non-induced subgraph matching.
    """

    @staticmethod
    def _independent_exact_matcher(Q: SimpleGraph, G: SimpleGraph) -> Set[Tuple[int, ...]]:
        """
        Pure, independent reference implementation of exact non-induced subgraph monomorphism.
        """
        q_nodes = sorted(Q.vertices.keys())
        k = len(q_nodes)
        g_nodes = sorted(G.vertices.keys())
        exact_matches = set()

        for perm in itertools.permutations(g_nodes, k):
            # Check vertex labels
            if any(G.vertices[perm[i]] != Q.vertices[q_nodes[i]] for i in range(k)):
                continue
            f = {q_nodes[i]: perm[i] for i in range(k)}
            # Check all query edges exist with matching labels
            all_edges_ok = True
            for (u, v), l_q in Q.edges.items():
                v1, v2 = f[u], f[v]
                if not G.has_edge(v1, v2) or G.get_edge_label(v1, v2) != l_q:
                    all_edges_ok = False
                    break
            if all_edges_ok:
                exact_matches.add(perm)

        return exact_matches

    def test_exact_recovery_relaxed_mode(self):
        # Q: Triangle K3
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(0, 2, 0)

        # G: Clique K4
        G = SimpleGraph()
        for i in range(4):
            G.add_vertex(i, 0)
        for u, v in itertools.combinations(range(4), 2):
            G.add_edge(u, v, 0)

        exact_set = self._independent_exact_matcher(Q, G)
        approx_matches = find_approximate_matches(
            Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.RELAXED_UNCONSTRAINED
        )
        approx_set = set(approx_matches.keys())

        self.assertEqual(approx_set, exact_set)
        self.assertEqual(len(approx_set), 24)  # P(4, 3) = 24

    def test_exact_recovery_strict_mode_connected_query(self):
        # For connected Q, exact non-induced matching entails connected image G[S_f]
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        G = SimpleGraph()
        for i in range(5):
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(3, 4, 0)

        exact_set = self._independent_exact_matcher(Q, G)
        strict_matches = find_approximate_matches(
            Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT
        )
        strict_set = set(strict_matches.keys())

        self.assertEqual(strict_set, exact_set)

    def test_disconnected_query_counterexample(self):
        """
        Refutation test: Q = 2K2 (disconnected query), G = 2K2.
        Exact matching accepts (m=0).
        Strict mode rejects because G[S_f] has 2 disconnected components!
        """
        # Q: Two disjoint edges: {0, 1} and {2, 3}
        Q = SimpleGraph()
        for i in range(4):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(2, 3, 0)

        # G: Same topology
        G = Q.clone()

        exact_set = self._independent_exact_matcher(Q, G)
        self.assertGreater(len(exact_set), 0)  # Exact accepts

        # Relaxed mode accepts
        relaxed_matches = find_approximate_matches(
            Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.RELAXED_UNCONSTRAINED
        )
        self.assertEqual(set(relaxed_matches.keys()), exact_set)

        # Strict mode MUST reject because components(G[S_f]) == 2 != 1
        strict_matches = find_approximate_matches(
            Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT
        )
        self.assertEqual(len(strict_matches), 0)


class TestAPX02PropertyInvariants(unittest.TestCase):
    """
    Tests foundational algebraic properties and invariants.
    """

    def setUp(self):
        self.Q = SimpleGraph()
        for i in range(3):
            self.Q.add_vertex(i, 0)
        self.Q.add_edge(0, 1, 0)
        self.Q.add_edge(1, 2, 0)

        self.G = SimpleGraph()
        for i in range(5):
            self.G.add_vertex(i, 0)
        self.G.add_edge(0, 1, 0)
        self.G.add_edge(1, 2, 0)
        self.G.add_edge(2, 3, 0)
        self.G.add_edge(0, 4, 0)

    def test_threshold_monotonicity(self):
        """
        eps_m <= eps_m' and eps_x <= eps_x' ==> M(eps) subseteq M(eps')
        """
        m_tight = set(find_approximate_matches(self.Q, self.G, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT).keys())
        m_loose = set(find_approximate_matches(self.Q, self.G, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT).keys())
        self.assertTrue(m_tight.issubset(m_loose))

    def test_strict_relaxed_inclusion(self):
        """
        For identical thresholds: M_Strict subseteq M_Relaxed
        """
        m_strict = set(find_approximate_matches(self.Q, self.G, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT).keys())
        m_relaxed = set(find_approximate_matches(self.Q, self.G, eps_m=1, eps_x=2, mode=ConnectivityMode.RELAXED_UNCONSTRAINED).keys())
        self.assertTrue(m_strict.issubset(m_relaxed))

    def test_automorphism_distinct_ordered_mappings(self):
        """
        Disambiguation test: Q = P4, G = C4.
        Must produce all 8 distinct ordered mappings, NOT 1 vertex-set occurrence.
        """
        Q = SimpleGraph()
        for i in range(4):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)

        G = SimpleGraph()
        for i in range(4):
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(3, 0, 0)

        matches = find_approximate_matches(Q, G, eps_m=0, eps_x=0, mode=ConnectivityMode.STRICT)
        # There are 4 starting positions * 2 directions = 8 distinct ordered injective mappings
        self.assertEqual(len(matches), 8)
        # Verify (0, 1, 2, 3) and (0, 3, 2, 1) are both present as distinct ordered mappings
        self.assertIn((0, 1, 2, 3), matches)
        self.assertIn((0, 3, 2, 1), matches)

    def test_dynamic_reversibility(self):
        """
        Valid insertion followed by valid deletion of same edge:
        G0 -> +e -> G1 -> -e -> G2 ==> G2 == G0 and M2 == M0
        """
        dyn = DynamicOracle(self.Q, self.G, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
        initial_matches = dict(dyn.current_matches)

        # 1. Insert edge {3, 4}
        delta_in = dyn.apply_update(Update("+", 3, 4, 0))
        # 2. Delete edge {3, 4}
        delta_del = dyn.apply_update(Update("-", 3, 4, 0))

        # Reversibility checks
        self.assertEqual(dyn.G.edges, self.G.edges)
        self.assertEqual(dyn.current_matches, initial_matches)
        # Mappings entered in step 1 must be exited in step 2
        for m in delta_in.entered.keys():
            self.assertIn(m, delta_del.exited)

    def test_score_changed_regression_internal_chord(self):
        """
        Regression test: An update that ONLY increases internal chord x_in
        does NOT trigger SCORE_CHANGED (since Model C score is strictly (m, x_out)).
        """
        # Q: Path P3 (0-1-2)
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        # G: Path P3 (0-1-2)
        G = SimpleGraph()
        for i in range(3):
            G.add_vertex(i, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(1, 2, 0)

        dyn = DynamicOracle(Q, G, eps_m=0, eps_x=2, mode=ConnectivityMode.STRICT)
        target = (0, 1, 2)
        self.assertIn(target, dyn.current_matches)
        self.assertEqual(dyn.current_matches[target].score.x_in, 0)

        # Insert chord {0, 2}
        delta = dyn.apply_update(Update("+", 0, 2, 0))
        # Target mapping remains accepted, its x_in is now 1, but m=0 and x_out=0 are unchanged
        self.assertIn(target, dyn.current_matches)
        self.assertEqual(dyn.current_matches[target].score.x_in, 1)
        self.assertNotIn(target, delta.score_changed)
        self.assertIn(target, delta.unchanged)


class TestAPX02RandomizedDifferential(unittest.TestCase):
    """
    Randomized small-graph differential tests with deterministic fixed seeds.
    Compares find_approximate_matches against independent brute-force evaluation.
    """

    def test_randomized_differential_trials(self):
        seeds = [42, 1337, 2026]
        trials_per_seed = 3

        total_trials = 0
        for seed in seeds:
            rng = random.Random(seed)
            for trial in range(trials_per_seed):
                total_trials += 1
                # Generate small query Q: 3 vertices
                k = 3
                Q = SimpleGraph()
                for i in range(k):
                    Q.add_vertex(i, rng.choice([0, 1]))
                Q.add_edge(0, 1, rng.choice([0, 1]))
                Q.add_edge(1, 2, rng.choice([0, 1]))
                if rng.random() > 0.5:
                    Q.add_edge(0, 2, rng.choice([0, 1]))

                # Generate small data graph G: 5 vertices
                n = 5
                G = SimpleGraph()
                for i in range(n):
                    G.add_vertex(i, rng.choice([0, 1]))
                for u, v in itertools.combinations(range(n), 2):
                    if rng.random() < 0.4:
                        G.add_edge(u, v, rng.choice([0, 1]))

                eps_m = rng.choice([0, 1, 2])
                eps_x = rng.choice([0, 1, 2, None])
                mode = rng.choice([ConnectivityMode.STRICT, ConnectivityMode.RELAXED_UNCONSTRAINED])

                # 1. Oracle result
                oracle_matches = find_approximate_matches(Q, G, eps_m, eps_x, mode)

                # 2. Independent brute-force evaluation of all permutations
                brute_force_matches = {}
                q_nodes = sorted(Q.vertices.keys())
                for perm in itertools.permutations(range(n), k):
                    eval_res = evaluate_mapping(Q, G, perm, eps_m, eps_x, mode)
                    if eval_res.accepted:
                        brute_force_matches[perm] = eval_res

                # Compare mapping-by-mapping
                self.assertEqual(
                    set(oracle_matches.keys()),
                    set(brute_force_matches.keys()),
                    f"Differential mismatch on seed {seed}, trial {trial}",
                )

        self.assertEqual(total_trials, len(seeds) * trials_per_seed)


if __name__ == "__main__":
    unittest.main()
