#!/usr/bin/env python3
"""
tests/test_dstar_oracle.py — Comprehensive Test Suite for Independent D* Oracle.

Covers:
1. Hand-calculated cases A through L (strict S1/S2 separation & public domain rejection).
2. Input validation for Q, G, k, IDs, labels, and operations.
3. Strict parser validation (from_dict string key parsing, collision detection, required fields).
4. Strict deletion validation (rejection of None wildcard, wrong-label deletion).
5. Label type distinction (string '0' vs integer 0).
6. Atomic invalid updates (state, graph, matches, and step untouched after rejection).
7. Edge connectivity reference calculations (P4, Star, C4, C6, Diamond, K4, K3,3).
8. Boundary characterization diagnostics (anchor edge + |R|=k, orientations, wrong labels).
9. Resource limits, early guards before exponential cuts, and fail-closed behavior.
10. Query and graph isolation in DStarDynamicOracle.
11. Structured CLI error output (JSON on stderr, exit codes 1 and 2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.dstar_oracle import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
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
    diagnose_mapping_s1_s2,
    enumerate_snapshot,
    evaluate_mapping,
    validate_resource_caps,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dstar01"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "dstar_oracle.py"


class TestDStarHandCases(unittest.TestCase):
    """Hand-calculated cases A through L."""

    def test_case_a_exact_recovery_k0(self) -> None:
        """Case A: Exact recovery k=0 with extra data graph edges/vertices."""
        Q = DStarGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)

        G = DStarGraph()
        for v in (10, 20, 30, 40):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)
        G.add_edge(20, 30, 0)
        G.add_edge(30, 10, 0)
        G.add_edge(30, 40, 0)

        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(len(matches), 6)
        for m in matches:
            self.assertEqual(set(m), {10, 20, 30})

    def test_case_b_extra_internal_chord(self) -> None:
        """Case B: Extra internal chord does not invalidate non-induced match."""
        with open(FIXTURES_DIR / "hand_c4_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_c4_data_chord.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(len(matches), 8)

    def test_case_c_external_tail(self) -> None:
        """Case C: External incident tail does not penalize edge recall."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))

        G = DStarGraph()
        for v, lbl in [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "Z")]:
            G.add_vertex(v, lbl)
        G.add_edge(1, 2, 0)
        G.add_edge(1, 3, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(2, 4, 0)
        G.add_edge(3, 4, 0)
        G.add_edge(4, 5, 0)

        matches = enumerate_snapshot(Q, G, k=1)
        self.assertIn((1, 2, 3, 4), matches)

    def test_case_d_injectivity_violation(self) -> None:
        """Case D: Non-injective mapping rejected."""
        Q = DStarGraph()
        for v in (1, 2):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        G.add_vertex(10, 0)

        eval_res = evaluate_mapping(Q, G, [10, 10], k=0)
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
        G.add_vertex(20, "C")
        G.add_edge(10, 20, 0)

        eval_res = evaluate_mapping(Q, G, [10, 20], k=0)
        self.assertFalse(eval_res.accepted)
        self.assertFalse(eval_res.vertex_labels_ok)

    def test_case_f_edge_label_mismatch(self) -> None:
        """Case F: Edge label mismatch counted as missing edge in R(f)."""
        Q = DStarGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)

        G = DStarGraph()
        for v in (1, 2, 3):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 99)
        G.add_edge(3, 1, 0)

        matches_k0 = enumerate_snapshot(Q, G, k=0)
        self.assertNotIn((1, 2, 3), matches_k0)

        matches_k1 = enumerate_snapshot(Q, G, k=1)
        self.assertIn((1, 2, 3), matches_k1)

    def test_case_g_sparse_query_ids(self) -> None:
        """Case G: Sparse/non-contiguous query vertex IDs preserve ascending tuple order."""
        with open(FIXTURES_DIR / "hand_sparse_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_sparse_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

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

        up = DStarUpdate(op="INSERT", u=5, v=6, label=0)
        delta = oracle.apply_update(up)
        self.assertEqual(delta.entered, [])
        self.assertEqual(delta.exited, [])
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

    def test_case_l_s1_vs_s2_distinction_and_public_rejection(self) -> None:
        """
        Case L: S1 vs S2 distinction.
        G[image] is connected via chord (S1=True), but matched Q-edges are disconnected (S2=False).
        Public evaluate_mapping must reject because k=1 >= lambda(P4)=1.
        Diagnostic helper exposes S1 vs S2 without opening a public bypass.
        """
        with open(FIXTURES_DIR / "hand_s1_vs_s2_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_s1_vs_s2_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        # 1. Public acceptance must strictly reject out-of-domain k=1 >= lambda=1
        with self.assertRaises(InvalidThresholdError):
            evaluate_mapping(Q, G, [1, 2, 3, 4], k=1)

        # 2. Diagnostic helper cleanly isolates S1 vs S2
        diag = diagnose_mapping_s1_s2(Q, G, [1, 2, 3, 4], k=1)
        self.assertTrue(diag.s1_connected, "S1 must be True because G[image] is connected via chord")
        self.assertFalse(diag.s2_connected, "S2 must be False because matched query edges are disconnected")
        self.assertFalse(diag.mvp_accepted, "Mapping must NOT be accepted in D* MVP")
        self.assertFalse(diag.in_mvp_domain, "Must report out of MVP domain")


class TestDStarStrictInputAndLabelValidation(unittest.TestCase):
    """R2 / F02: Strict input types, label handling, and deletion validation."""

    def test_reject_bool_and_float_vertex_ids(self) -> None:
        g = DStarGraph()
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(True, 0)
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(1.5, 0)
        with self.assertRaises(InvalidGraphError):
            g.add_vertex("1", 0)

    def test_reject_unsupported_vertex_labels(self) -> None:
        g = DStarGraph()
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(1, None)
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(1, True)
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(1, 2.5)
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(1, [1, 2])

    def test_reject_unsupported_edge_labels(self) -> None:
        g = DStarGraph()
        g.add_vertex(1, 0)
        g.add_vertex(2, 0)
        with self.assertRaises(InvalidGraphError):
            g.add_edge(1, 2, None)
        with self.assertRaises(InvalidGraphError):
            g.add_edge(1, 2, True)
        with self.assertRaises(InvalidGraphError):
            g.add_edge(1, 2, 3.14)

    def test_string_zero_vs_integer_zero_label_distinction(self) -> None:
        """String '0' and integer 0 are distinct and must not be coerced."""
        Q = DStarGraph()
        Q.add_vertex(1, 0)  # integer 0
        Q.add_vertex(2, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        G.add_vertex(10, "0")  # string '0' -> mismatch!
        G.add_vertex(20, "0")
        G.add_edge(10, 20, "0")

        eval_res = evaluate_mapping(Q, G, [10, 20], k=0)
        self.assertFalse(eval_res.accepted)
        self.assertFalse(eval_res.vertex_labels_ok)

    def test_from_dict_strict_key_parsing(self) -> None:
        # Float key in dict
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({"vertices": {"1.9": 0}, "edges": []})

        # Leading zero key in dict
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({"vertices": {"01": 0}, "edges": []})

        # Non-numeric string key in dict
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({"vertices": {"true": 0}, "edges": []})

        # Missing required field 'label' in edge spec
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({
                "vertices": {"1": 0, "2": 0},
                "edges": [{"u": 1, "v": 2}],  # missing 'label'
            })

    def test_strict_deletion_no_none_wildcard(self) -> None:
        g = DStarGraph()
        g.add_vertex(1, 0)
        g.add_vertex(2, 0)
        g.add_edge(1, 2, 0)

        # Deleting with None label must be rejected
        with self.assertRaises(InvalidGraphError):
            g.remove_edge(1, 2, None)

        # Deleting with wrong label must be rejected
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            g.remove_edge(1, 2, 999)

        # Deleting with string '0' when actual is integer 0 must be rejected
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            g.remove_edge(1, 2, "0")

        # Deleting with correct label succeeds
        g.remove_edge(1, 2, 0)
        self.assertFalse(g.has_edge(1, 2))

    def test_strict_update_dataclass_validation(self) -> None:
        with self.assertRaises(InvalidGraphError):
            DStarUpdate(op="DELETE", u=1, v=2, label=None)
        with self.assertRaises(InvalidGraphError):
            DStarUpdate(op="INSERT", u=True, v=2, label=0)
        with self.assertRaises(InvalidGraphError):
            DStarUpdate(op="INSERT", u=1, v=2, label=1.5)
        with self.assertRaises(SelfLoopError):
            DStarUpdate(op="INSERT", u=1, v=1, label=0)


class TestDStarAtomicInvalidUpdates(unittest.TestCase):
    """Atomic invalid updates: graph, matches, and step remain completely untouched."""

    def setUp(self) -> None:
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            self.Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_full.json", "r") as f:
            self.G = DStarGraph.from_dict(json.load(f))
        self.oracle = DStarDynamicOracle(self.Q, self.G, k=1)
        self.initial_edges = dict(self.oracle.G.edges)
        self.initial_matches = set(self.oracle.current_matches)
        self.initial_step = self.oracle.step

    def _assert_state_untouched(self) -> None:
        self.assertEqual(self.oracle.G.edges, self.initial_edges)
        self.assertEqual(self.oracle.current_matches, self.initial_matches)
        self.assertEqual(self.oracle.step, self.initial_step)

    def test_atomic_unknown_vertex(self) -> None:
        up = DStarUpdate(op="INSERT", u=1, v=999, label=0)
        with self.assertRaises(UnknownVertexError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_duplicate_insertion(self) -> None:
        up = DStarUpdate(op="INSERT", u=1, v=2, label=0)
        with self.assertRaises(DuplicateEdgeInsertionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_nonexistent_deletion(self) -> None:
        up = DStarUpdate(op="DELETE", u=1, v=4, label=0)
        with self.assertRaises(NonexistentEdgeDeletionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_edge_label_mismatch_deletion(self) -> None:
        up = DStarUpdate(op="DELETE", u=1, v=2, label=999)
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()


class TestDStarLambdaReference(unittest.TestCase):
    """Reference checks for global edge connectivity lambda(Q)."""

    def test_lambda_topologies(self) -> None:
        def make_p4():
            g = DStarGraph()
            for v in (1, 2, 3, 4):
                g.add_vertex(v, 0)
            g.add_edge(1, 2, 0)
            g.add_edge(2, 3, 0)
            g.add_edge(3, 4, 0)
            return g

        def make_star4():
            g = DStarGraph()
            for v in (1, 2, 3, 4):
                g.add_vertex(v, 0)
            g.add_edge(1, 2, 0)
            g.add_edge(1, 3, 0)
            g.add_edge(1, 4, 0)
            return g

        def make_c6():
            g = DStarGraph()
            for v in range(1, 7):
                g.add_vertex(v, 0)
            for v in range(1, 6):
                g.add_edge(v, v + 1, 0)
            g.add_edge(6, 1, 0)
            return g

        self.assertEqual(compute_edge_connectivity(make_p4()), 1)
        self.assertEqual(compute_edge_connectivity(make_star4()), 1)
        self.assertEqual(compute_edge_connectivity(make_c6(), max_query_vertices=6), 2)


class TestDStarQueryIsolation(unittest.TestCase):
    """R5 / N01: DStarDynamicOracle must clone and isolate query and data graphs."""

    def test_caller_mutation_of_q_does_not_affect_oracle(self) -> None:
        """
        Reproducer from review:
        Q=C4, G=C4 + isolated vertices 5,6, k=0, initial 8 mappings.
        Caller adds chord (1, 3) to original Q.
        Dynamic update INSERT(5, 6, 0).
        Must emit exactly 0 entered and 0 exited!
        """
        Q = DStarGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 4, 0)
        Q.add_edge(4, 1, 0)

        G = DStarGraph()
        for v in range(1, 7):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(3, 4, 0)
        G.add_edge(4, 1, 0)

        oracle = DStarDynamicOracle(Q, G, k=0)
        self.assertEqual(len(oracle.current_matches), 8)

        # Mutate caller's Q
        Q.add_edge(1, 3, 0)

        # Apply unrelated update to G
        up = DStarUpdate(op="INSERT", u=5, v=6, label=0)
        delta = oracle.apply_update(up)

        # Because oracle isolated Q, it still searches for C4, not Diamond!
        self.assertEqual(delta.entered, [])
        self.assertEqual(delta.exited, [])
        self.assertEqual(len(oracle.current_matches), 8)


class TestDStarEarlyResourceGuards(unittest.TestCase):
    """R4 / F04 + N04: Resource limits must fail closed BEFORE exponential work."""

    @patch("scripts.dstar_oracle.compute_edge_connectivity")
    def test_oversized_query_rejects_before_cut_computation(self, mock_compute_lam) -> None:
        """Query with 7 vertices > default max 6 must raise ResourceLimitError without calling compute_edge_connectivity."""
        Q_oversized = DStarGraph()
        for v in range(1, 8):  # 7 vertices
            Q_oversized.add_vertex(v, 0)
        for v in range(1, 7):
            Q_oversized.add_edge(v, v + 1, 0)

        G_dummy = DStarGraph()
        for v in (1, 2):
            G_dummy.add_vertex(v, 0)

        with self.assertRaises(ResourceLimitError):
            enumerate_snapshot(Q_oversized, G_dummy, k=0, max_query_vertices=6)

        mock_compute_lam.assert_not_called()

    def test_oversized_data_graph_rejects_early(self) -> None:
        Q_ok = DStarGraph()
        for v in (1, 2):
            Q_ok.add_vertex(v, 0)
        Q_ok.add_edge(1, 2, 0)

        G_oversized = DStarGraph()
        for v in range(1, 10):  # 9 vertices > default max 8
            G_oversized.add_vertex(v, 0)

        with self.assertRaises(ResourceLimitError):
            enumerate_snapshot(Q_ok, G_oversized, k=0, max_data_vertices=8)

    def test_invalid_resource_cap_types_rejected(self) -> None:
        for bad_val in [True, -1, 0, 1.5, "100"]:
            with self.assertRaises(ResourceLimitError):
                validate_resource_caps(bad_val, 8, 6)


class TestDStarStructuredCLI(unittest.TestCase):
    """R6 / N02: CLI error handling outputs structured JSON on stderr and exits nonzero."""

    def test_missing_query_file_structured_error(self) -> None:
        res = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "snapshot", "--query", "/nonexistent/q.json", "--graph", str(FIXTURES_DIR / "hand_k4_data.json"), "--k", "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        err_json = json.loads(res.stderr)
        self.assertEqual(err_json["status"], "ERROR")
        self.assertEqual(err_json["error_type"], "FileNotFoundError")

    def test_oversized_input_cli_exits_2(self) -> None:
        # Create temporary oversized query
        tmp_q = Path("/tmp/oversized_q.json")
        q_data = {
            "vertices": {str(v): 0 for v in range(1, 8)},
            "edges": [{"u": v, "v": v + 1, "label": 0} for v in range(1, 7)],
        }
        with open(tmp_q, "w") as f:
            json.dump(q_data, f)

        res = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "snapshot", "--query", str(tmp_q), "--graph", str(FIXTURES_DIR / "hand_k4_data.json"), "--k", "0"],
            capture_output=True,
            text=True,
        )
        tmp_q.unlink(missing_ok=True)
        self.assertEqual(res.returncode, 2)
        err_json = json.loads(res.stderr)
        self.assertEqual(err_json["status"], "RESOURCE_LIMIT")
        self.assertEqual(err_json["error_type"], "ResourceLimitError")


if __name__ == "__main__":
    unittest.main()
