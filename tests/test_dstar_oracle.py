#!/usr/bin/env python3
"""
tests/test_dstar_oracle.py — Comprehensive Test Suite for Independent D* Oracle.

Covers:
1. Hand-calculated cases A through L.
2. Input validation for Q, G, k, labels, and operations.
3. Atomic invalid updates (state unchanged after rejection).
4. Edge connectivity reference calculations (P4, Star, C4, C6, Diamond, K4, K3,3).
5. Boundary characterization diagnostics (anchor edge + |R|=k for INSERT/DELETE).
6. Uniqueness of output tuples.
7. Resource limits and fail-closed behavior.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Dict, List, Set, Tuple

from scripts.dstar_oracle import (
    DStarDynamicOracle,
    DStarGraph,
    DStarUpdate,
    DuplicateEdgeInsertionError,
    EdgeLabelMismatchDeletionError,
    InvalidGraphError,
    InvalidThresholdError,
    NonexistentEdgeDeletionError,
    ResourceLimitError,
    SelfLoopError,
    UnknownVertexError,
    UnsupportedOperationError,
    apply_update,
    check_anchored_boundary_property,
    compute_edge_connectivity,
    enumerate_snapshot,
    evaluate_mapping,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dstar01"


class TestDStarHandCases(unittest.TestCase):
    """Hand-calculated cases A through L."""

    def test_case_a_exact_recovery_k0(self) -> None:
        """Case A: Exact recovery k=0 with extra data graph edges/vertices."""
        Q = DStarGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)  # Triangle, lambda=2

        G = DStarGraph()
        for v in (10, 20, 30, 40):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)
        G.add_edge(20, 30, 0)
        G.add_edge(30, 10, 0)
        # Extra edge and vertex
        G.add_edge(30, 40, 0)

        matches = enumerate_snapshot(Q, G, k=0)
        # Triangle has 6 automorphisms on {10, 20, 30}
        self.assertEqual(len(matches), 6)
        for m in matches:
            self.assertEqual(set(m), {10, 20, 30})

    def test_case_b_extra_internal_chord(self) -> None:
        """Case B: Extra internal chord does not invalidate non-induced match."""
        with open(FIXTURES_DIR / "hand_c4_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_c4_data_chord.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        # C4 query has 8 automorphisms on C4 data graph
        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(len(matches), 8)

    def test_case_c_external_tail(self) -> None:
        """Case C: External incident tail does not penalize edge recall."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))

        # Diamond in G plus an external tail vertex
        G = DStarGraph()
        for v, lbl in [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "Z")]:
            G.add_vertex(v, lbl)
        G.add_edge(1, 2, 0)
        G.add_edge(1, 3, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(2, 4, 0)
        G.add_edge(3, 4, 0)
        G.add_edge(4, 5, 0)  # External tail

        matches = enumerate_snapshot(Q, G, k=1)
        # Unique match f: 1->1, 2->2, 3->3, 4->4
        self.assertIn((1, 2, 3, 4), matches)

    def test_case_d_injectivity_violation(self) -> None:
        """Case D: Non-injective mapping rejected."""
        Q = DStarGraph()
        for v in (1, 2):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        G.add_vertex(10, 0)

        eval_res = evaluate_mapping(Q, G, [10, 10], k=0, enforce_k_bound=False)
        self.assertFalse(eval_res.accepted)
        self.assertFalse(eval_res.is_injective)
        self.assertIn("NON_INJECTIVE", eval_res.reject_reasons)

    def test_case_e_vertex_label_mismatch(self) -> None:
        """Case E: Vertex label mismatch rejected."""
        Q = DStarGraph()
        Q.add_vertex(1, "A")
        Q.add_vertex(2, "B")
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        G.add_vertex(10, "A")
        G.add_vertex(20, "C")  # Mismatch: should be "B"
        G.add_edge(10, 20, 0)

        eval_res = evaluate_mapping(Q, G, [10, 20], k=0, enforce_k_bound=False)
        self.assertFalse(eval_res.accepted)
        self.assertFalse(eval_res.vertex_labels_ok)

    def test_case_f_edge_label_mismatch(self) -> None:
        """Case F: Edge label mismatch counted as missing edge in R(f)."""
        Q = DStarGraph()
        Q.add_vertex(1, 0)
        Q.add_vertex(2, 0)
        Q.add_vertex(3, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)  # Triangle, lambda=2

        G = DStarGraph()
        for v in (1, 2, 3):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 99)  # Label mismatch!
        G.add_edge(3, 1, 0)

        # For k=0: rejected because |R| = 1 > 0
        matches_k0 = enumerate_snapshot(Q, G, k=0)
        self.assertNotIn((1, 2, 3), matches_k0)

        # For k=1: accepted because |R| = 1 <= 1, and matched edges {1-2, 3-1} span {1,2,3} and are connected
        matches_k1 = enumerate_snapshot(Q, G, k=1)
        self.assertIn((1, 2, 3), matches_k1)

    def test_case_g_sparse_query_ids(self) -> None:
        """Case G: Sparse/non-contiguous query vertex IDs preserve ascending tuple order."""
        with open(FIXTURES_DIR / "hand_sparse_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_sparse_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        # Query vertices are {25: 'X', 3: 'Y', 10: 'Z'}
        # Sorted query order is (3, 10, 25).
        # q3('Y')->100, q10('Z')->200, q25('X')->300.
        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(matches, [(100, 200, 300)])

    def test_case_h_automorphism_k4(self) -> None:
        """Case H: Full K4 query and K4 data automorphism yields exactly 24 ordered tuples."""
        with open(FIXTURES_DIR / "hand_k4_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_k4_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(len(matches), 24)
        self.assertEqual(len(set(matches)), 24)

    def test_case_i_diamond_event(self) -> None:
        """Case I: Diamond event: insert bridge triggers ENTERED; delete bridge triggers EXITED."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_before.json", "r") as f:
            G_before = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_stream_event.json", "r") as f:
            stream_data = json.load(f)

        oracle = DStarDynamicOracle(Q, G_before, k=1)
        self.assertEqual(len(oracle.current_matches), 0)

        # Step 1: INSERT (2, 3) label 0
        up1 = DStarUpdate(
            op=stream_data[0]["op"],
            u=stream_data[0]["u"],
            v=stream_data[0]["v"],
            label=stream_data[0]["label"],
        )
        delta1 = oracle.apply_update(up1)
        self.assertEqual(delta1.entered, [(1, 2, 3, 4)])
        self.assertEqual(delta1.exited, [])
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Step 2: DELETE (2, 3) label 0
        up2 = DStarUpdate(
            op=stream_data[1]["op"],
            u=stream_data[1]["u"],
            v=stream_data[1]["v"],
            label=stream_data[1]["label"],
        )
        delta2 = oracle.apply_update(up2)
        self.assertEqual(delta2.entered, [])
        self.assertEqual(delta2.exited, [(1, 2, 3, 4)])
        self.assertEqual(oracle.current_matches, set())

    def test_case_j_diamond_no_event(self) -> None:
        """Case J: Diamond no-event: deleting edge leaves mapping within budget (no SCORE_CHANGED)."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_full.json", "r") as f:
            G_full = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_stream_no_event.json", "r") as f:
            stream_data = json.load(f)

        oracle = DStarDynamicOracle(Q, G_full, k=1)
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Step 1: DELETE (2, 4) label 0 -> |R| becomes 1 <= 1. Still valid!
        up1 = DStarUpdate(
            op=stream_data[0]["op"],
            u=stream_data[0]["u"],
            v=stream_data[0]["v"],
            label=stream_data[0]["label"],
        )
        delta1 = oracle.apply_update(up1)
        self.assertEqual(delta1.entered, [])
        self.assertEqual(delta1.exited, [])
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Step 2: DELETE (2, 3) label 0 -> |R| becomes 2 > 1. Disqualified!
        up2 = DStarUpdate(
            op=stream_data[1]["op"],
            u=stream_data[1]["u"],
            v=stream_data[1]["v"],
            label=stream_data[1]["label"],
        )
        delta2 = oracle.apply_update(up2)
        self.assertEqual(delta2.entered, [])
        self.assertEqual(delta2.exited, [(1, 2, 3, 4)])
        self.assertEqual(oracle.current_matches, set())

    def test_case_k_unrelated_update(self) -> None:
        """Case K: Unrelated edge update produces no entered or exited events."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))

        # Data graph with Diamond and an isolated edge between vertices 5 and 6
        G = DStarGraph()
        for v, lbl in [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "Z"), (6, "Z")]:
            G.add_vertex(v, lbl)
        G.add_edge(1, 2, 0)
        G.add_edge(1, 3, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(2, 4, 0)
        G.add_edge(3, 4, 0)

        oracle = DStarDynamicOracle(Q, G, k=1)
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Insert unrelated edge (5, 6)
        up = DStarUpdate(op="INSERT", u=5, v=6, label=0)
        delta = oracle.apply_update(up)
        self.assertEqual(delta.entered, [])
        self.assertEqual(delta.exited, [])
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

    def test_case_l_s1_vs_s2_distinction(self) -> None:
        """Case L: S1 vs S2: G[image] connected via extra chord, but matched Q-edges disconnected."""
        with open(FIXTURES_DIR / "hand_s1_vs_s2_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_s1_vs_s2_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        # Query is P4 (1-2-3-4). lambda(P4) = 1.
        # Data graph has edges 1-2, 3-4, and extra chord 1-4. Edge 2-3 is missing.
        # For mapping f: 1->1, 2->2, 3->3, 4->4:
        # Matched edges in Q are {1-2, 3-4}. R(f) = {2-3}.
        # Q[E_Q \ R(f)] is disconnected: {1,2} and {3,4} -> S2 is False!
        # But G[image(f)] has edges 1-2, 3-4, 1-4 -> 2-1-4-3 is connected -> S1 is True!
        eval_res = evaluate_mapping(Q, G, [1, 2, 3, 4], k=1, enforce_k_bound=False)
        self.assertTrue(eval_res.s1_connected, "S1 must be True because G[image] is connected via chord")
        self.assertFalse(eval_res.s2_connected, "S2 must be False because matched query edges are disconnected")
        self.assertFalse(eval_res.accepted, "Mapping must be rejected under D* because S2 is False")


class TestDStarInputValidation(unittest.TestCase):
    """Input validation for Q, G, k, labels, and operations."""

    def test_disconnected_query_rejected(self) -> None:
        Q = DStarGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(3, 4, 0)  # Two disconnected components
        with self.assertRaises(InvalidGraphError):
            Q.validate_as_query()

    def test_empty_query_rejected(self) -> None:
        Q = DStarGraph()
        with self.assertRaises(InvalidGraphError):
            Q.validate_as_query()

    def test_single_vertex_query_rejected(self) -> None:
        Q = DStarGraph()
        Q.add_vertex(1, 0)
        with self.assertRaises(InvalidGraphError):
            Q.validate_as_query()

    def test_invalid_k_values(self) -> None:
        Q = DStarGraph()
        for v in (1, 2):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        G = DStarGraph()
        for v in (10, 20):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)

        # k negative
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(Q, G, k=-1)

        # k bool
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(Q, G, k=True)

        # k fractional
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(Q, G, k=0.5)  # type: ignore

        # k >= lambda(Q): for K2, lambda=1. k=1 is invalid!
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(Q, G, k=1)

    def test_graph_self_loop_rejected(self) -> None:
        G = DStarGraph()
        G.add_vertex(1, 0)
        with self.assertRaises(SelfLoopError):
            G.add_edge(1, 1, 0)

    def test_duplicate_edge_rejected(self) -> None:
        G = DStarGraph()
        G.add_vertex(1, 0)
        G.add_vertex(2, 0)
        G.add_edge(1, 2, 0)
        with self.assertRaises(DuplicateEdgeInsertionError):
            G.add_edge(1, 2, 0)

    def test_undeclared_endpoint_rejected(self) -> None:
        G = DStarGraph()
        G.add_vertex(1, 0)
        with self.assertRaises(UnknownVertexError):
            G.add_edge(1, 999, 0)

    def test_conflicting_vertex_labels_rejected(self) -> None:
        G = DStarGraph()
        G.add_vertex(1, "A")
        with self.assertRaises(InvalidGraphError):
            G.add_vertex(1, "B")

    def test_unsupported_operation_rejected(self) -> None:
        with self.assertRaises(UnsupportedOperationError):
            DStarUpdate(op="BATCH_INSERT", u=1, v=2, label=0)


class TestDStarAtomicInvalidUpdates(unittest.TestCase):
    """Atomic invalid updates: graph and active mapping set remain unchanged."""

    def setUp(self) -> None:
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            self.Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_full.json", "r") as f:
            self.G = DStarGraph.from_dict(json.load(f))
        self.oracle = DStarDynamicOracle(self.Q, self.G, k=1)
        self.initial_edges = dict(self.oracle.G.edges)
        self.initial_matches = set(self.oracle.current_matches)

    def _assert_state_untouched(self) -> None:
        self.assertEqual(self.oracle.G.edges, self.initial_edges)
        self.assertEqual(self.oracle.current_matches, self.initial_matches)

    def test_atomic_unknown_vertex(self) -> None:
        up = DStarUpdate(op="INSERT", u=1, v=999, label=0)
        with self.assertRaises(UnknownVertexError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_self_loop(self) -> None:
        with self.assertRaises(SelfLoopError):
            DStarUpdate(op="INSERT", u=1, v=1, label=0)
        self._assert_state_untouched()

    def test_atomic_duplicate_insertion(self) -> None:
        up = DStarUpdate(op="INSERT", u=1, v=2, label=0)
        with self.assertRaises(DuplicateEdgeInsertionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_nonexistent_deletion(self) -> None:
        up = DStarUpdate(op="DELETE", u=1, v=4, label=0)  # (1, 4) does not exist in Diamond
        with self.assertRaises(NonexistentEdgeDeletionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_edge_label_mismatch_deletion(self) -> None:
        up = DStarUpdate(op="DELETE", u=1, v=2, label=999)  # actual label is 0
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()


class TestDStarLambdaReference(unittest.TestCase):
    """Reference checks for global edge connectivity lambda(Q)."""

    def test_lambda_p4(self) -> None:
        Q = DStarGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 4, 0)
        self.assertEqual(compute_edge_connectivity(Q), 1)

    def test_lambda_star4(self) -> None:
        Q = DStarGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(1, 3, 0)
        Q.add_edge(1, 4, 0)
        self.assertEqual(compute_edge_connectivity(Q), 1)

    def test_lambda_c4(self) -> None:
        Q = DStarGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 4, 0)
        Q.add_edge(4, 1, 0)
        self.assertEqual(compute_edge_connectivity(Q), 2)

    def test_lambda_c6(self) -> None:
        Q = DStarGraph()
        for v in range(1, 7):
            Q.add_vertex(v, 0)
        for v in range(1, 6):
            Q.add_edge(v, v + 1, 0)
        Q.add_edge(6, 1, 0)
        self.assertEqual(compute_edge_connectivity(Q), 2)

    def test_lambda_diamond(self) -> None:
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        self.assertEqual(compute_edge_connectivity(Q), 2)

    def test_lambda_k4(self) -> None:
        with open(FIXTURES_DIR / "hand_k4_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        self.assertEqual(compute_edge_connectivity(Q), 3)

    def test_lambda_k3_3(self) -> None:
        Q = DStarGraph()
        for v in range(1, 7):
            Q.add_vertex(v, 0)
        # Bipartite: {1, 2, 3} to {4, 5, 6}
        for u in (1, 2, 3):
            for v in (4, 5, 6):
                Q.add_edge(u, v, 0)
        self.assertEqual(compute_edge_connectivity(Q), 3)


class TestDStarBoundaryDiagnostics(unittest.TestCase):
    """Boundary characterization diagnostics: anchor edge + |R|=k."""

    def test_boundary_property_diamond(self) -> None:
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_before.json", "r") as f:
            G_before = DStarGraph.from_dict(json.load(f))

        # Update: INSERT (2, 3) label 0
        up_insert = DStarUpdate(op="INSERT", u=2, v=3, label=0)
        G_after = G_before.clone()
        apply_update(G_after, up_insert)

        matches_before = set(enumerate_snapshot(Q, G_before, k=1))
        matches_after = set(enumerate_snapshot(Q, G_after, k=1))
        entered = matches_after - matches_before
        exited = matches_before - matches_after

        # Verify property for INSERT
        diag_insert = check_anchored_boundary_property(
            Q, G_before, G_after, up_insert, k=1, true_entered=entered, true_exited=exited
        )
        self.assertTrue(diag_insert["property_holds"])
        self.assertEqual(diag_insert["target_event_count"], 1)

        # Update: DELETE (2, 3) label 0
        up_delete = DStarUpdate(op="DELETE", u=2, v=3, label=0)
        diag_delete = check_anchored_boundary_property(
            Q, G_after, G_before, up_delete, k=1, true_entered=set(), true_exited=entered
        )
        self.assertTrue(diag_delete["property_holds"])
        self.assertEqual(diag_delete["target_event_count"], 1)


class TestDStarResourceLimits(unittest.TestCase):
    """Resource limits and fail-closed behavior."""

    def test_candidate_guard_fails_closed(self) -> None:
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_full.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        # Max candidates set to 10 (P(4, 4) = 24 > 10)
        with self.assertRaises(ResourceLimitError):
            enumerate_snapshot(Q, G, k=1, max_candidates=10)


if __name__ == "__main__":
    unittest.main()
