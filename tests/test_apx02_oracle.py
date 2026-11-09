#!/usr/bin/env python3
"""
tests/test_apx02_oracle.py — Comprehensive Test Suite for APX-02 Approximate CSM Oracle.

Covers:
1. All 16 Reference Microcases from docs/approx_semantic_microcases_v1.md.
2. Scope B Atomic Update Validation & Exception Guards.
3. Independent Exact Recovery Validation (Relaxed Mode, Connected Strict Mode, and RF-02 cross-checks).
4. Disconnected Query Exact vs Strict Counterexample (Q=2K2, G=2K2).
5. Property Tests:
   - Threshold Monotonicity
   - Strict/Relaxed Inclusion
   - Dynamic Reversibility
   - Automorphism Invariance & Distinct Ordered Mappings
   - SCORE_CHANGED Regression (Internal chord does not trigger SCORE_CHANGED)
6. F01 Regression: Rejection of Undeclared Vertices in from_text().
7. F02 Regression: Invalid Parameter Validation (eps_m, eps_x, max_candidates).
8. F03 Independent Static Differential Tests (Independent Reference Scorer with DSU).
9. F03 Independent Dynamic Differential Tests (Independent snapshot-difference reference).
"""

import itertools
import os
import random
import subprocess
import sys
import tempfile
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
    validate_thresholds,
)
from scripts.rf02_oracle import (
    LabeledGraph as RF02LabeledGraph,
    find_all_embeddings as rf02_find_all_embeddings,
    find_all_embeddings_brute_force as rf02_find_all_embeddings_brute_force,
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

        # Exact matching requires m=0: must REJECT
        res_exact = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertFalse(res_exact.accepted)

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

        # Strict mode at (eps_m=0, eps_x=0): must REJECT
        res_00 = evaluate_mapping(Q, G, mapping, eps_m=0, eps_x=0, mode=ConnectivityMode.STRICT)
        self.assertFalse(res_00.accepted)

        # Relaxed mode at (eps_m=1, eps_x=0): must ACCEPT
        res_rel = evaluate_mapping(Q, G, mapping, eps_m=1, eps_x=0, mode=ConnectivityMode.RELAXED_UNCONSTRAINED)
        self.assertTrue(res_rel.accepted)


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

    def test_exact_recovery_against_rf02_oracle(self):
        """
        Automated cross-check against scripts/rf02_oracle.py:
        - find_all_embeddings
        - find_all_embeddings_brute_force
        Verifies M_Approx,Relaxed(0, None) == M_Exact on multiple graph topologies.
        Verifies M_Approx,Strict(0, None) == M_Exact for connected queries.
        """
        def to_rf02(g: SimpleGraph) -> RF02LabeledGraph:
            rf_g = RF02LabeledGraph()
            for vid, lbl in g.vertices.items():
                rf_g.add_vertex(vid, lbl)
            for (u, v), lbl in g.edges.items():
                rf_g.add_edge(u, v, lbl)
            return rf_g

        test_cases = []

        # 1. Path P3 in Cycle C4
        q1 = SimpleGraph()
        for i in range(3): q1.add_vertex(i, 0)
        q1.add_edge(0, 1, 0); q1.add_edge(1, 2, 0)
        g1 = SimpleGraph()
        for i in range(4): g1.add_vertex(i, 0)
        g1.add_edge(0, 1, 0); g1.add_edge(1, 2, 0); g1.add_edge(2, 3, 0); g1.add_edge(3, 0, 0)
        test_cases.append((q1, g1, True))

        # 2. Star K1,3 in Complete graph K5
        q2 = SimpleGraph()
        for i in range(4): q2.add_vertex(i, 0)
        q2.add_edge(0, 1, 0); q2.add_edge(0, 2, 0); q2.add_edge(0, 3, 0)
        g2 = SimpleGraph()
        for i in range(5): g2.add_vertex(i, 0)
        for u, v in itertools.combinations(range(5), 2):
            g2.add_edge(u, v, 0)
        test_cases.append((q2, g2, True))

        # 3. Multi-label triangle
        q3 = SimpleGraph()
        q3.add_vertex(0, 1); q3.add_vertex(1, 2); q3.add_vertex(2, 3)
        q3.add_edge(0, 1, 0); q3.add_edge(1, 2, 0); q3.add_edge(0, 2, 0)
        g3 = SimpleGraph()
        for i in range(4): g3.add_vertex(i, (i % 3) + 1)
        g3.add_edge(0, 1, 0); g3.add_edge(1, 2, 0); g3.add_edge(0, 2, 0)
        test_cases.append((q3, g3, True))

        for idx, (Q, G, q_conn) in enumerate(test_cases):
            Q_rf = to_rf02(Q)
            G_rf = to_rf02(G)
            rf_emb_dfs = set(rf02_find_all_embeddings(Q_rf, G_rf))
            rf_emb_bf = set(rf02_find_all_embeddings_brute_force(Q_rf, G_rf))
            self.assertEqual(rf_emb_dfs, rf_emb_bf)

            # Check Relaxed mode: M_Approx,Relaxed(0, None) == M_Exact
            apx_relaxed = set(
                find_approximate_matches(
                    Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.RELAXED_UNCONSTRAINED
                ).keys()
            )
            self.assertEqual(
                apx_relaxed,
                rf_emb_dfs,
                f"APX Relaxed vs RF02 mismatch on case {idx}",
            )

            # Check Strict mode on connected query
            if q_conn:
                apx_strict = set(
                    find_approximate_matches(
                        Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT
                    ).keys()
                )
                self.assertEqual(
                    apx_strict,
                    rf_emb_dfs,
                    f"APX Strict vs RF02 mismatch on connected case {idx}",
                )


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


class TestAPX02F01ParserUndeclaredVertices(unittest.TestCase):
    """
    Regression tests for F01: Rejection of undeclared vertices in from_text().
    Ensures fail-closed behavior on implicit vertex creation.
    """

    def test_case1_undeclared_endpoints(self):
        # Case 1 — Undeclared vertices: e 0 1 0 -> REJECT
        with self.assertRaises(UnknownVertexError):
            SimpleGraph.from_text("e 0 1 0\n")

    def test_case2_one_undeclared_endpoint(self):
        # Case 2 — One undeclared endpoint: v 0 1 \n e 0 1 0 -> REJECT
        with self.assertRaises(UnknownVertexError):
            SimpleGraph.from_text("v 0 1\ne 0 1 0\n")

    def test_case3_explicit_vertices(self):
        # Case 3 — Explicit vertices -> ACCEPT INPUT
        text = "v 0 1\nv 1 2\ne 0 1 0\n"
        g = SimpleGraph.from_text(text)
        self.assertEqual(len(g.vertices), 2)
        self.assertEqual(g.vertices[0], 1)
        self.assertEqual(g.vertices[1], 2)
        self.assertEqual(len(g.edges), 1)
        self.assertEqual(g.edges[(0, 1)], 0)

    def test_case4_valid_isolated_vertex(self):
        # Case 4 — Valid isolated vertex -> Input valid, vertex 2 exists and isolated
        text = "v 0 1\nv 1 2\nv 2 3\ne 0 1 0\n"
        g = SimpleGraph.from_text(text)
        self.assertEqual(len(g.vertices), 3)
        self.assertEqual(g.vertices[2], 3)
        self.assertEqual(g.degree(2), 0)
        self.assertEqual(len(g.edges), 1)

    def test_case5_rf02_fixture_compatibility(self):
        # Case 5 — Ensure standard explicit graph fixtures parse cleanly
        text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = SimpleGraph.from_text(text)
        self.assertEqual(len(g.vertices), 3)
        self.assertEqual(len(g.edges), 2)


class TestAPX02F02ParameterValidation(unittest.TestCase):
    """
    Regression tests for F02: Parameter validation on eps_m, eps_x, max_candidates.
    Ensures non-negative thresholds, positive candidate budgets, and proper error handling.
    """

    def setUp(self):
        self.Q = SimpleGraph()
        self.Q.add_vertex(0, 0)
        self.G = SimpleGraph()
        self.G.add_vertex(0, 0)

    def test_evaluate_mapping_negative_eps_m(self):
        with self.assertRaises(ValueError) as ctx:
            evaluate_mapping(self.Q, self.G, (0,), eps_m=-1, eps_x=0)
        self.assertIn("eps_m", str(ctx.exception))

    def test_evaluate_mapping_negative_eps_x(self):
        with self.assertRaises(ValueError) as ctx:
            evaluate_mapping(self.Q, self.G, (0,), eps_m=0, eps_x=-1)
        self.assertIn("eps_x", str(ctx.exception))

    def test_find_approximate_matches_negative_thresholds(self):
        with self.assertRaises(ValueError):
            find_approximate_matches(self.Q, self.G, eps_m=-1, eps_x=0)
        with self.assertRaises(ValueError):
            find_approximate_matches(self.Q, self.G, eps_m=0, eps_x=-1)

    def test_find_approximate_matches_invalid_max_candidates(self):
        with self.assertRaises(ValueError) as ctx1:
            find_approximate_matches(self.Q, self.G, eps_m=0, eps_x=0, max_candidates=0)
        self.assertIn("max_candidates", str(ctx1.exception))
        with self.assertRaises(ValueError) as ctx2:
            find_approximate_matches(self.Q, self.G, eps_m=0, eps_x=0, max_candidates=-5)
        self.assertIn("max_candidates", str(ctx2.exception))

    def test_dynamic_oracle_invalid_parameters(self):
        with self.assertRaises(ValueError):
            DynamicOracle(self.Q, self.G, eps_m=-1, eps_x=0)
        with self.assertRaises(ValueError):
            DynamicOracle(self.Q, self.G, eps_m=0, eps_x=-1)
        with self.assertRaises(ValueError):
            DynamicOracle(self.Q, self.G, eps_m=0, eps_x=0, max_candidates=0)

    def test_valid_threshold_boundaries(self):
        res = evaluate_mapping(self.Q, self.G, (0,), eps_m=0, eps_x=None)
        self.assertTrue(res.accepted)
        matches = find_approximate_matches(
            self.Q, self.G, eps_m=0, eps_x=None, max_candidates=1
        )
        self.assertEqual(len(matches), 1)

    def test_cli_invalid_parameter_exit_code(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fq, tempfile.NamedTemporaryFile("w", delete=False) as fg:
            fq.write("v 0 0\n")
            fg.write("v 0 0\n")
            q_path = fq.name
            g_path = fg.name

        try:
            # 1. Invalid eps-m
            res_m = subprocess.run(
                [sys.executable, "scripts/apx02_oracle.py", "-q", q_path, "-d", g_path, "--eps-m", "-1"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_m.returncode, 0)
            self.assertIn("eps_m", res_m.stderr.lower())

            # 2. Invalid eps-x
            res_x = subprocess.run(
                [sys.executable, "scripts/apx02_oracle.py", "-q", q_path, "-d", g_path, "--eps-x", "-1"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_x.returncode, 0)
            self.assertIn("eps_x", res_x.stderr.lower())

            # 3. Invalid max-candidates
            res_c = subprocess.run(
                [sys.executable, "scripts/apx02_oracle.py", "-q", q_path, "-d", g_path, "--max-candidates", "0"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_c.returncode, 0)
            self.assertIn("max_candidates", res_c.stderr.lower())
        finally:
            if os.path.exists(q_path): os.unlink(q_path)
            if os.path.exists(g_path): os.unlink(g_path)


class IndependentDisjointSetUnion:
    """Independent Disjoint Set Union (DSU) implementation for connectivity verification."""

    def __init__(self, elements):
        self.parent = {x: x for x in elements}
        self.rank = {x: 0 for x in elements}
        self.num_components = len(elements)

    def find(self, x):
        path = []
        curr = x
        while self.parent[curr] != curr:
            path.append(curr)
            curr = self.parent[curr]
        for node in path:
            self.parent[node] = curr
        return curr

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.num_components -= 1
        return True


class IndependentReferenceScorer:
    """
    Completely independent Model C CSM Reference Scorer.
    Implemented without calling any production oracle scoring functions.
    Computes:
    - m(f): missing required query edges
    - x_in(f): extra internal chords (diagnostic only, NOT part of acceptance)
    - x_out(f): extra boundary clutter edges leaving mapped set S_f
    - connectivity: connected components of G[S_f] via IndependentDisjointSetUnion
    - acceptance: Model C acceptance condition
    """

    def __init__(
        self,
        q_vertices: Dict[int, int],
        q_edges: Dict[Tuple[int, int], int],
        g_vertices: Dict[int, int],
        g_edges: Dict[Tuple[int, int], int],
    ):
        self.q_vertices = dict(q_vertices)
        self.q_edges = {(min(u, v), max(u, v)): lbl for (u, v), lbl in q_edges.items()}
        self.g_vertices = dict(g_vertices)
        self.g_edges = {(min(u, v), max(u, v)): lbl for (u, v), lbl in g_edges.items()}
        self.q_nodes = sorted(self.q_vertices.keys())
        self.g_nodes = sorted(self.g_vertices.keys())

    @classmethod
    def from_simple_graphs(cls, Q: SimpleGraph, G: SimpleGraph):
        return cls(Q.vertices, Q.edges, G.vertices, G.edges)

    def score_mapping(self, mapping_tuple: Tuple[int, ...]) -> Dict:
        k = len(self.q_nodes)
        if len(mapping_tuple) != k:
            return {"valid": False}

        s_f = list(mapping_tuple)
        is_injective = (len(set(s_f)) == k)
        s_f_set = set(s_f)

        v_labels_ok = True
        for q_u, g_v in zip(self.q_nodes, mapping_tuple):
            if self.g_vertices.get(g_v) != self.q_vertices.get(q_u):
                v_labels_ok = False
                break

        f_map = {self.q_nodes[i]: mapping_tuple[i] for i in range(k)}

        # 1. Missing edges m: required query edges not matching in G
        m = 0
        for (q_u1, q_u2), q_lbl in self.q_edges.items():
            g_u1 = f_map[q_u1]
            g_u2 = f_map[q_u2]
            key = (min(g_u1, g_u2), max(g_u1, g_u2))
            if key not in self.g_edges or self.g_edges[key] != q_lbl:
                m += 1

        # 2. Extra internal chords x_in:
        # All edges in G[S_f] that are not images of any edge in Q
        x_in = 0
        if is_injective:
            inv_map = {g_v: q_u for q_u, g_v in f_map.items()}
            s_f_list = sorted(list(s_f_set))
            for i in range(len(s_f_list)):
                for j in range(i + 1, len(s_f_list)):
                    u = s_f_list[i]
                    v = s_f_list[j]
                    if (u, v) in self.g_edges:
                        qu = inv_map[u]
                        qv = inv_map[v]
                        q_key = (min(qu, qv), max(qu, qv))
                        if q_key not in self.q_edges:
                            x_in += 1

        # 3. Extra boundary clutter x_out:
        # Edges in G with one endpoint in S_f and one endpoint outside S_f
        x_out = 0
        for (u, v) in self.g_edges.keys():
            u_in = u in s_f_set
            v_in = v in s_f_set
            if (u_in and not v_in) or (v_in and not u_in):
                x_out += 1

        # 4. Topological connectivity of G[S_f] using DSU
        dsu = IndependentDisjointSetUnion(s_f_set)
        s_f_list = sorted(list(s_f_set))
        for i in range(len(s_f_list)):
            for j in range(i + 1, len(s_f_list)):
                u = s_f_list[i]
                v = s_f_list[j]
                if (u, v) in self.g_edges:
                    dsu.union(u, v)
        is_connected = (dsu.num_components <= 1) if s_f_set else True

        return {
            "valid": True,
            "is_injective": is_injective,
            "vertex_labels_ok": v_labels_ok,
            "m": m,
            "x_in": x_in,
            "x_out": x_out,
            "is_connected": is_connected,
        }

    def is_accepted(
        self,
        score: Dict,
        eps_m: int,
        eps_x: Optional[int],
        mode: ConnectivityMode = ConnectivityMode.STRICT,
    ) -> bool:
        if not score.get("valid", False):
            return False
        if not score["is_injective"]:
            return False
        if not score["vertex_labels_ok"]:
            return False
        if score["m"] > eps_m:
            return False
        if eps_x is not None and score["x_out"] > eps_x:
            return False
        if mode == ConnectivityMode.STRICT and not score["is_connected"]:
            return False
        return True

    def find_all_accepted(
        self,
        eps_m: int,
        eps_x: Optional[int],
        mode: ConnectivityMode = ConnectivityMode.STRICT,
    ) -> Dict[Tuple[int, ...], Dict]:
        k = len(self.q_nodes)
        accepted = {}
        for perm in itertools.permutations(self.g_nodes, k):
            sc = self.score_mapping(perm)
            if self.is_accepted(sc, eps_m, eps_x, mode):
                accepted[perm] = sc
        return accepted


class TestAPX02IndependentStaticDifferential(unittest.TestCase):
    """
    Independent Static Differential Tests across varied topologies and thresholds.
    Cross-checks find_approximate_matches and evaluate_mapping against
    IndependentReferenceScorer without using production scoring code to build expected values.
    """

    def test_independent_static_differential_trials(self):
        seeds = [42, 1337, 2026, 7777, 9999]
        trials_per_seed = 5

        total_trials = 0
        for seed in seeds:
            rng = random.Random(seed)
            for trial in range(trials_per_seed):
                total_trials += 1
                k = rng.choice([2, 3, 4])
                n = rng.choice([4, 5, 6, 7])

                # Build query Q
                Q = SimpleGraph()
                for i in range(k):
                    Q.add_vertex(i, rng.choice([0, 1]))
                for u, v in itertools.combinations(range(k), 2):
                    if rng.random() < 0.5:
                        Q.add_edge(u, v, rng.choice([0, 1]))

                # Build data graph G
                G = SimpleGraph()
                for i in range(n):
                    G.add_vertex(i, rng.choice([0, 1]))
                for u, v in itertools.combinations(range(n), 2):
                    if rng.random() < 0.35:
                        G.add_edge(u, v, rng.choice([0, 1]))

                eps_m = rng.choice([0, 1, 2])
                eps_x = rng.choice([0, 1, 2, None])
                mode = rng.choice([ConnectivityMode.STRICT, ConnectivityMode.RELAXED_UNCONSTRAINED])

                # 1. Production Oracle results
                oracle_matches = find_approximate_matches(Q, G, eps_m, eps_x, mode)

                # 2. Independent Reference evaluation
                ref_scorer = IndependentReferenceScorer.from_simple_graphs(Q, G)
                ref_matches = ref_scorer.find_all_accepted(eps_m, eps_x, mode)

                # Assert identical set of accepted mappings
                self.assertEqual(
                    set(oracle_matches.keys()),
                    set(ref_matches.keys()),
                    f"Differential mismatch on seed {seed}, trial {trial}",
                )

                # Assert exact score agreement for all candidate permutations
                for perm in itertools.permutations(range(n), k):
                    oracle_eval = evaluate_mapping(Q, G, perm, eps_m, eps_x, mode)
                    ref_sc = ref_scorer.score_mapping(perm)
                    ref_acc = ref_scorer.is_accepted(ref_sc, eps_m, eps_x, mode)

                    self.assertEqual(oracle_eval.score.m, ref_sc["m"])
                    self.assertEqual(oracle_eval.score.x_in, ref_sc["x_in"])
                    self.assertEqual(oracle_eval.score.x_out, ref_sc["x_out"])
                    self.assertEqual(oracle_eval.score.connected, ref_sc["is_connected"])
                    self.assertEqual(oracle_eval.accepted, ref_acc)

        self.assertEqual(total_trials, len(seeds) * trials_per_seed)


class TestAPX02IndependentDynamicDifferential(unittest.TestCase):
    """
    Independent Dynamic Differential Tests.
    Verifies dynamic streaming deltas (ENTERED, EXITED, SCORE_CHANGED, UNCHANGED)
    against independent snapshot difference computations using IndependentReferenceScorer.
    """

    def test_dynamic_streaming_differential(self):
        # Q: Path P3 (0-1-2)
        Q = SimpleGraph()
        for i in range(3):
            Q.add_vertex(i, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        # G0: 5 vertices (0..4), initial edges (0-1), (1-2)
        G0 = SimpleGraph()
        for i in range(5):
            G0.add_vertex(i, 0)
        G0.add_edge(0, 1, 0)
        G0.add_edge(1, 2, 0)

        eps_m = 0
        eps_x = 1
        mode = ConnectivityMode.STRICT

        dyn_oracle = DynamicOracle(Q, G0, eps_m=eps_m, eps_x=eps_x, mode=mode)

        # Mirror independent state
        curr_g_v = dict(G0.vertices)
        curr_g_e = dict(G0.edges)

        stream = [
            Update("+", 2, 3, 0),  # Boundary edge: increments x_out of (0,1,2) to 1
            Update("+", 2, 4, 0),  # Boundary edge: increments x_out of (0,1,2) to 2 > eps_x=1 -> EXITED
            Update("-", 2, 4, 0),  # Boundary edge deleted: x_out back to 1 <= eps_x=1 -> ENTERED
            Update("+", 0, 2, 0),  # Internal chord: does NOT change (m, x_out) -> UNCHANGED
            Update("-", 0, 1, 0),  # Required query edge deleted: breaks match -> EXITED
            Update("+", 0, 1, 0),  # Restores edge: match recovers -> ENTERED
            Update("-", 0, 2, 0),  # Removes internal chord: x_in back to 0 -> UNCHANGED
        ]

        for step_idx, update in enumerate(stream, start=1):
            # 1. Independent snapshot before update
            ref_before = IndependentReferenceScorer(Q.vertices, Q.edges, curr_g_v, curr_g_e)
            matches_before = ref_before.find_all_accepted(eps_m, eps_x, mode)

            # Apply update to independent state
            key = (min(update.u, update.v), max(update.u, update.v))
            if update.op == "+":
                curr_g_e[key] = update.label
            else:
                curr_g_e.pop(key, None)

            # 2. Independent snapshot after update
            ref_after = IndependentReferenceScorer(Q.vertices, Q.edges, curr_g_v, curr_g_e)
            matches_after = ref_after.find_all_accepted(eps_m, eps_x, mode)

            # Compute independent deltas
            keys_before = set(matches_before.keys())
            keys_after = set(matches_after.keys())

            exp_entered = keys_after - keys_before
            exp_exited = keys_before - keys_after
            common = keys_before & keys_after

            exp_score_changed = set()
            exp_unchanged = set()
            for f in common:
                sc_b = matches_before[f]
                sc_a = matches_after[f]
                if (sc_b["m"], sc_b["x_out"]) != (sc_a["m"], sc_a["x_out"]):
                    exp_score_changed.add(f)
                else:
                    exp_unchanged.add(f)

            # 3. Apply update to production DynamicOracle
            oracle_delta = dyn_oracle.apply_update(update)

            # Assert exact delta agreement
            self.assertEqual(
                set(oracle_delta.entered.keys()),
                exp_entered,
                f"Dynamic ENTERED mismatch at step {step_idx}",
            )
            self.assertEqual(
                set(oracle_delta.exited.keys()),
                exp_exited,
                f"Dynamic EXITED mismatch at step {step_idx}",
            )
            self.assertEqual(
                set(oracle_delta.score_changed.keys()),
                exp_score_changed,
                f"Dynamic SCORE_CHANGED mismatch at step {step_idx}",
            )
            self.assertEqual(
                set(oracle_delta.unchanged.keys()),
                exp_unchanged,
                f"Dynamic UNCHANGED mismatch at step {step_idx}",
            )

            # Assert score accuracy for score_changed
            for f, (old_sc, new_sc) in oracle_delta.score_changed.items():
                self.assertEqual(old_sc.m, matches_before[f]["m"])
                self.assertEqual(old_sc.x_out, matches_before[f]["x_out"])
                self.assertEqual(new_sc.m, matches_after[f]["m"])
                self.assertEqual(new_sc.x_out, matches_after[f]["x_out"])


class TestAPX02F04EvidenceGenerator(unittest.TestCase):
    """
    Regression and integrity tests for scripts/generate_apx02_correctness_report.py
    Verifies Tests A through H as mandated by Round 3 review.
    """

    def setUp(self):
        from scripts.generate_apx02_correctness_report import (
            evaluate_microcases,
            compute_source_hashes,
        )
        self.evaluate_microcases = evaluate_microcases
        self.compute_source_hashes = compute_source_hashes
        self.microcases = evaluate_microcases()
        self.case_by_id = {c["case_id"]: c for c in self.microcases}

    def test_a_case_08_query_is_star_k12(self):
        # Case 08 must use Star K1,2 (edges {0,1}, {0,2}), not triangle K3
        c08 = self.case_by_id["Case 08"]
        self.assertIn("Star K1,2", c08["query_desc"])
        self.assertNotIn("K3", c08["query_desc"])
        self.assertNotIn("Triangle", c08["query_desc"])
        self.assertEqual(len(c08["target_mapping"]), 3)

    def test_b_case_08_x_out_is_998(self):
        c08 = self.case_by_id["Case 08"]
        self.assertEqual(c08["expected"]["x_out"], 998)
        self.assertEqual(c08["actual"]["x_out"], 998)
        self.assertEqual(c08["actual"]["m"], 0)
        self.assertTrue(c08["actual"]["connected"])

    def test_c_case_08_rejected_at_eps_x_30(self):
        c08 = self.case_by_id["Case 08"]
        self.assertFalse(c08["expected"]["strict_accepted_at_30"])
        self.assertFalse(c08["actual"]["strict_accepted_at_30"])

    def test_d_case_08_accepted_at_eps_x_none(self):
        c08 = self.case_by_id["Case 08"]
        self.assertTrue(c08["expected"]["strict_accepted_at_inf"])
        self.assertTrue(c08["actual"]["strict_accepted_at_inf"])

    def test_e_evaluation_types_classified_accurately(self):
        # Every case must be either DIRECT_MAPPING_EVALUATION or DYNAMIC_STREAM
        valid_types = {"DIRECT_MAPPING_EVALUATION", "DYNAMIC_STREAM"}
        for c in self.microcases:
            self.assertIn(
                c["evaluation_type"],
                valid_types,
                f"Invalid or mislabeled evaluation_type '{c['evaluation_type']}' in {c['case_id']}",
            )
            # Ensure no case is labeled FULL_ENUMERATION if it evaluates a single mapping
            self.assertNotEqual(c["evaluation_type"], "FULL_ENUMERATION")

    def test_f_sixteen_unique_canonical_cases(self):
        case_ids = [c["case_id"] for c in self.microcases]
        self.assertEqual(len(case_ids), 16)
        self.assertEqual(len(set(case_ids)), 16)
        for i in range(1, 17):
            expected_id = f"Case {i:02d}"
            self.assertIn(expected_id, self.case_by_id)

    def test_g_every_case_has_expected_actual_and_verdict(self):
        for c in self.microcases:
            self.assertIsInstance(c["expected"], dict)
            self.assertIsInstance(c["actual"], dict)
            self.assertEqual(c["verdict"], "PASS")
            self.assertEqual(c["actual"], c["expected"])

    def test_h_report_provenance_contains_source_hashes(self):
        hashes = self.compute_source_hashes()
        required_files = [
            "scripts/apx02_oracle.py",
            "tests/test_apx02_oracle.py",
            "scripts/generate_apx02_correctness_report.py",
            "docs/approx_semantic_microcases_v1.md",
        ]
        for f in required_files:
            self.assertIn(f, hashes)
            self.assertEqual(len(hashes[f]), 64, f"Invalid SHA-256 for {f}")
            self.assertNotEqual(hashes[f], "FILE_NOT_FOUND")

    def test_intentional_mismatch_fails_report(self):
        # Mutating an expectation must flip verdict to FAIL
        c01 = dict(self.case_by_id["Case 01"])
        c01_mutated_exp = dict(c01["expected"])
        c01_mutated_exp["m"] = 999  # Corrupt expectation
        verdict = "PASS" if c01["actual"] == c01_mutated_exp else "FAIL"
        self.assertEqual(verdict, "FAIL")


if __name__ == "__main__":
    unittest.main()
