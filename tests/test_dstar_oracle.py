#!/usr/bin/env python3
"""
tests/test_dstar_oracle.py — Comprehensive Test Suite for Independent D* Oracle.

==============================================================================
BEHAVIOR COVERAGE MAP
==============================================================================
1. Structural Query Contract:
   - Disconnected query rejected: test_disconnected_query_rejected
   - Empty query rejected: test_empty_query_rejected
   - Single-vertex query rejected: test_single_vertex_query_rejected
   - Self-loop rejected: test_graph_self_loop_rejected
   - Duplicate edge rejected: test_duplicate_edge_rejected
   - Undeclared endpoint rejected: test_undeclared_endpoint_rejected
   - Conflicting vertex label rejected: test_conflicting_vertex_labels_rejected

2. Threshold and Contract Domain:
   - Negative, boolean, and float k rejected: test_invalid_k_values
   - k >= lambda(Q) rejected: test_invalid_k_values, test_evaluate_mapping_strict_bound
   - Public evaluate_mapping strictly validates 0 <= k < lambda: test_evaluate_mapping_strict_bound

3. Strict Input Parsing and Label Typing:
   - Rejection of bool and float vertex IDs: test_reject_bool_and_float_vertex_ids
   - Rejection of unsupported vertex labels: test_reject_unsupported_vertex_labels
   - Rejection of unsupported edge labels: test_reject_unsupported_edge_labels
   - Distinction between string '0' and integer 0: test_string_zero_vs_integer_zero_label_distinction
   - Strict from_dict key parsing (no float keys, no leading zeros): test_from_dict_strict_key_parsing
   - Strict deletion requiring explicit label (no None wildcard): test_strict_deletion_no_none_wildcard
   - Strict DStarUpdate dataclass validation: test_strict_update_dataclass_validation

4. Early Zero-Result Return Validation (R2-F01):
   - Valid Q, valid k, small G returns complete empty list: test_valid_q_valid_k_small_g_returns_empty_list
   - Valid Q, k >= lambda, small G rejected: test_valid_q_invalid_k_small_g_rejected
   - Disconnected Q with small G rejected: test_disconnected_q_small_g_rejected
   - Empty/singleton Q with small G rejected: test_empty_query_small_g_rejected, test_singleton_query_small_g_rejected
   - Empty data graph rejected: test_empty_data_graph_rejected
   - DynamicOracle init rejects invalid domain with small G0: test_dynamic_oracle_init_rejects_invalid_inputs_on_small_g0
   - CLI snapshot rejects invalid k with small G: test_cli_snapshot_small_g_rejects_invalid_k

5. Hand-Calculated Cases (A through L):
   - Case A: Exact recovery k=0 with extra data graph edges/vertices
   - Case B: Extra internal chord does not invalidate non-induced match
   - Case C: External incident tail does not penalize edge recall
   - Case D: Injectivity violation rejected
   - Case E: Vertex label mismatch rejected
   - Case F: Edge label mismatch counted in R(f)
   - Case G: Sparse query vertex IDs preserve ascending tuple order
   - Case H: Full K4 query and K4 data automorphism yields 24 ordered tuples
   - Case I: Diamond bridge event (INSERT -> ENTERED, DELETE -> EXITED)
   - Case J: Diamond within-threshold delete produces no event
   - Case K: Unrelated edge update produces no entered/exited events
   - Case L: S1 vs S2 distinction & public out-of-domain rejection

6. Global Edge Connectivity Reference Verification:
   - P4 (lambda=1), Star4 (lambda=1), C4 (lambda=2), C6 (lambda=2),
     Diamond (lambda=2), K4 (lambda=3), K3_3 (lambda=3): test_lambda_all_topologies

7. Dynamic Oracle Atomicity & State Isolation:
   - Atomic unknown vertex: test_atomic_unknown_vertex
   - Atomic self-loop: test_atomic_self_loop
   - Atomic duplicate insertion: test_atomic_duplicate_insertion
   - Atomic nonexistent deletion: test_atomic_nonexistent_deletion
   - Atomic label mismatch deletion: test_atomic_edge_label_mismatch_deletion
   - Resource failure in update rolls back state: test_update_resource_limit_failure_rolls_back_state
   - Caller Q mutation does not affect oracle: test_caller_mutation_of_q_does_not_affect_oracle
   - Caller G mutation does not affect oracle: test_caller_mutation_of_g_does_not_affect_oracle

8. Resource Limit Guards:
   - Oversized query rejected before cut computation: test_oversized_query_rejects_before_cut_computation
   - Oversized data graph rejected early: test_oversized_data_graph_rejects_early
   - Candidate injection bound guard fails closed: test_candidate_guard_fails_closed
   - Invalid resource cap types rejected: test_invalid_resource_cap_types_rejected

9. CLI Structured Error Handling:
   - Missing query file: test_missing_query_file_structured_error
   - Oversized input: test_oversized_input_cli_exits_2
   - Malformed JSON input: test_malformed_json_cli_exits_1
   - Malformed graph schema: test_malformed_schema_cli_exits_1
   - Stream missing required label field: test_stream_missing_label_cli_exits_1
==============================================================================
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
        """Case D: Mapping must be strictly injective."""
        Q = DStarGraph()
        for v in (1, 2):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        G.add_vertex(10, 0)

        eval_res = evaluate_mapping(Q, G, [10, 10], k=0)
        self.assertFalse(eval_res.accepted)
        self.assertIn("NON_INJECTIVE", eval_res.reject_reasons)

    def test_case_e_vertex_label_mismatch(self) -> None:
        """Case E: Vertex label mismatch rejects candidate completely."""
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
        """Case F: Edge label mismatch is counted as a missing edge in R(f)."""
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
        G.add_edge(2, 3, 99)  # Label mismatch
        G.add_edge(3, 1, 0)

        m0 = enumerate_snapshot(Q, G, k=0)
        self.assertNotIn((1, 2, 3), m0)

        m1 = enumerate_snapshot(Q, G, k=1)
        self.assertIn((1, 2, 3), m1)

    def test_case_g_sparse_query_ids(self) -> None:
        """Case G: Non-contiguous query IDs preserve canonical tuple order."""
        with open(FIXTURES_DIR / "hand_sparse_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_sparse_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(matches, [(100, 200, 300)])

    def test_case_h_automorphism_k4(self) -> None:
        """Case H: Full K4 query on K4 data graph produces exactly 24 ordered tuples."""
        with open(FIXTURES_DIR / "hand_k4_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_k4_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        matches = enumerate_snapshot(Q, G, k=0)
        self.assertEqual(len(matches), 24)
        self.assertEqual(len(set(matches)), 24)

    def test_case_i_diamond_event(self) -> None:
        """Case I: Diamond dynamic update event."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_before.json", "r") as f:
            G_before = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_stream_event.json", "r") as f:
            stream = json.load(f)

        oracle = DStarDynamicOracle(Q, G_before, k=1)
        self.assertEqual(len(oracle.current_matches), 0)

        # Step 1: Insert bridge edge (2, 3, 0)
        up1 = DStarUpdate(op=stream[0]["op"], u=stream[0]["u"], v=stream[0]["v"], label=stream[0]["label"])
        d1 = oracle.apply_update(up1)
        self.assertEqual(d1.entered, [(1, 2, 3, 4)])
        self.assertEqual(d1.exited, [])
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Step 2: Delete bridge edge (2, 3, 0)
        up2 = DStarUpdate(op=stream[1]["op"], u=stream[1]["u"], v=stream[1]["v"], label=stream[1]["label"])
        d2 = oracle.apply_update(up2)
        self.assertEqual(d2.entered, [])
        self.assertEqual(d2.exited, [(1, 2, 3, 4)])
        self.assertEqual(oracle.current_matches, set())

    def test_case_j_diamond_no_event(self) -> None:
        """Case J: Deleting edge leaves mapping within budget (no event emitted)."""
        with open(FIXTURES_DIR / "hand_diamond_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_data_full.json", "r") as f:
            G_full = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_diamond_stream_no_event.json", "r") as f:
            stream = json.load(f)

        oracle = DStarDynamicOracle(Q, G_full, k=1)
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Step 1: Delete edge (2, 4, 0). Mapping still has |R|=1 <= 1, connected.
        up1 = DStarUpdate(op=stream[0]["op"], u=stream[0]["u"], v=stream[0]["v"], label=stream[0]["label"])
        d1 = oracle.apply_update(up1)
        self.assertEqual(d1.entered, [])
        self.assertEqual(d1.exited, [])
        self.assertEqual(oracle.current_matches, {(1, 2, 3, 4)})

        # Step 2: Delete bridge (2, 3, 0). |R|=2 > 1 -> EXITED
        up2 = DStarUpdate(op=stream[1]["op"], u=stream[1]["u"], v=stream[1]["v"], label=stream[1]["label"])
        d2 = oracle.apply_update(up2)
        self.assertEqual(d2.entered, [])
        self.assertEqual(d2.exited, [(1, 2, 3, 4)])

    def test_case_k_unrelated_update(self) -> None:
        """Case K: Unrelated update produces no entered or exited events."""
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
        up = DStarUpdate(op="INSERT", u=5, v=6, label=0)
        delta = oracle.apply_update(up)
        self.assertEqual(delta.entered, [])
        self.assertEqual(delta.exited, [])

    def test_case_l_s1_vs_s2_distinction_and_public_rejection(self) -> None:
        """Case L: S1 vs S2 separation & public domain rejection."""
        with open(FIXTURES_DIR / "hand_s1_vs_s2_query.json", "r") as f:
            Q = DStarGraph.from_dict(json.load(f))
        with open(FIXTURES_DIR / "hand_s1_vs_s2_data.json", "r") as f:
            G = DStarGraph.from_dict(json.load(f))

        # Query is P4 (lambda=1). k=1 >= lambda(P4) must be rejected publicly
        with self.assertRaises(InvalidThresholdError):
            evaluate_mapping(Q, G, [1, 2, 3, 4], k=1)

        # Research diagnostic inspects S1 and S2
        diag = diagnose_mapping_s1_s2(Q, G, [1, 2, 3, 4], k=1)
        self.assertTrue(diag.s1_connected)
        self.assertFalse(diag.s2_connected)
        self.assertFalse(diag.mvp_accepted)
        self.assertFalse(diag.in_mvp_domain)


class TestDStarInputValidation(unittest.TestCase):
    """Behavior tests for query graph structure, invalid threshold, and graph properties."""

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
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)

        G = DStarGraph()
        for v in (1, 2, 3):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(3, 1, 0)

        # Negative k
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(Q, G, k=-1)

        # Non-integer / boolean k
        for bad_k in [True, False, 1.5, "1", None]:
            with self.subTest(bad_k=bad_k):
                with self.assertRaises(InvalidThresholdError):
                    enumerate_snapshot(Q, G, k=bad_k)

        # k >= lambda(Triangle) = 2
        for invalid_k in [2, 3, 99]:
            with self.subTest(invalid_k=invalid_k):
                with self.assertRaises(InvalidThresholdError):
                    enumerate_snapshot(Q, G, k=invalid_k)

    def test_evaluate_mapping_strict_bound(self) -> None:
        """Public evaluate_mapping must strictly enforce 0 <= k < lambda(Q)."""
        Q = DStarGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)
        G = Q.clone()

        with self.assertRaises(InvalidThresholdError):
            evaluate_mapping(Q, G, [1, 2, 3], k=2)

    def test_graph_self_loop_rejected(self) -> None:
        g = DStarGraph()
        g.add_vertex(1, 0)
        with self.assertRaises(SelfLoopError):
            g.add_edge(1, 1, 0)

    def test_duplicate_edge_rejected(self) -> None:
        g = DStarGraph()
        g.add_vertex(1, 0)
        g.add_vertex(2, 0)
        g.add_edge(1, 2, 0)
        with self.assertRaises(DuplicateEdgeInsertionError):
            g.add_edge(1, 2, 0)

    def test_undeclared_endpoint_rejected(self) -> None:
        g = DStarGraph()
        g.add_vertex(1, 0)
        with self.assertRaises(UnknownVertexError):
            g.add_edge(1, 999, 0)

    def test_conflicting_vertex_labels_rejected(self) -> None:
        g = DStarGraph()
        g.add_vertex(1, "A")
        with self.assertRaises(InvalidGraphError):
            g.add_vertex(1, "B")

    def test_unsupported_operation_rejected(self) -> None:
        with self.assertRaises(UnsupportedOperationError):
            DStarUpdate(op="UPDATE", u=1, v=2, label=0)


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
        Q.add_vertex(1, 0)
        Q.add_vertex(2, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        G.add_vertex(10, "0")
        G.add_vertex(20, "0")
        G.add_edge(10, 20, "0")

        eval_res = evaluate_mapping(Q, G, [10, 20], k=0)
        self.assertFalse(eval_res.accepted)
        self.assertFalse(eval_res.vertex_labels_ok)

    def test_from_dict_strict_key_parsing(self) -> None:
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({"vertices": {"1.9": 0}, "edges": []})
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({"vertices": {"01": 0}, "edges": []})
        with self.assertRaises(InvalidGraphError):
            DStarGraph.from_dict({"vertices": {"true": 0}, "edges": []})
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

        with self.assertRaises(InvalidGraphError):
            g.remove_edge(1, 2, None)
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            g.remove_edge(1, 2, 999)
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            g.remove_edge(1, 2, "0")

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


class TestDStarEarlyZeroReturnValidation(unittest.TestCase):
    """
    R2-F01: Input and domain validation must execute BEFORE successful zero-result return.
    """

    def setUp(self) -> None:
        # Q = C4 (lambda = 2)
        self.Q_c4 = DStarGraph()
        for v in (1, 2, 3, 4):
            self.Q_c4.add_vertex(v, 0)
        self.Q_c4.add_edge(1, 2, 0)
        self.Q_c4.add_edge(2, 3, 0)
        self.Q_c4.add_edge(3, 4, 0)
        self.Q_c4.add_edge(4, 1, 0)

        # G with only 1 vertex (< 4 vertices in Q)
        self.G_1 = DStarGraph()
        self.G_1.add_vertex(10, 0)

    def test_valid_q_valid_k_small_g_returns_empty_list(self) -> None:
        """Valid query, valid k=1 (< lambda=2), data graph with 1 vertex returns complete []."""
        res = enumerate_snapshot(self.Q_c4, self.G_1, k=1)
        self.assertEqual(res, [])

    def test_valid_q_invalid_k_small_g_rejected(self) -> None:
        """Out-of-domain k >= lambda on small G must raise InvalidThresholdError, not return []."""
        # Reproducer 1: k=2 >= lambda(C4)=2
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(self.Q_c4, self.G_1, k=2)

        # Reproducer 2: k=99
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(self.Q_c4, self.G_1, k=99)

    def test_disconnected_q_small_g_rejected(self) -> None:
        """Disconnected Q on small G must raise InvalidGraphError, not return []."""
        Q_disc = DStarGraph()
        for v in (1, 2, 3, 4):
            Q_disc.add_vertex(v, 0)
        Q_disc.add_edge(1, 2, 0)
        Q_disc.add_edge(3, 4, 0)

        with self.assertRaises(InvalidGraphError):
            enumerate_snapshot(Q_disc, self.G_1, k=0)

    def test_empty_query_small_g_rejected(self) -> None:
        Q_empty = DStarGraph()
        with self.assertRaises(InvalidGraphError):
            enumerate_snapshot(Q_empty, self.G_1, k=0)

    def test_singleton_query_small_g_rejected(self) -> None:
        Q_single = DStarGraph()
        Q_single.add_vertex(1, 0)
        with self.assertRaises(InvalidGraphError):
            enumerate_snapshot(Q_single, self.G_1, k=0)

    def test_empty_data_graph_rejected(self) -> None:
        """Empty data graph violates D* contract (vertex universe cannot be empty)."""
        G_empty = DStarGraph()
        with self.assertRaises(InvalidGraphError):
            enumerate_snapshot(self.Q_c4, G_empty, k=0)

    def test_dynamic_oracle_init_rejects_invalid_inputs_on_small_g0(self) -> None:
        """DStarDynamicOracle must not accept invalid domain when G0 is smaller than Q."""
        with self.assertRaises(InvalidThresholdError):
            DStarDynamicOracle(self.Q_c4, self.G_1, k=2)

    def test_cli_snapshot_small_g_rejects_invalid_k(self) -> None:
        """CLI snapshot with small G and k >= lambda must exit 1 with structured ERROR."""
        tmp_q = Path("/tmp/test_q_c4.json")
        tmp_g = Path("/tmp/test_g_1.json")
        with open(tmp_q, "w") as f:
            json.dump(self.Q_c4.to_dict(), f)
        with open(tmp_g, "w") as f:
            json.dump(self.G_1.to_dict(), f)

        res = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "snapshot", "--query", str(tmp_q), "--graph", str(tmp_g), "--k", "2"],
            capture_output=True,
            text=True,
        )
        tmp_q.unlink(missing_ok=True)
        tmp_g.unlink(missing_ok=True)

        self.assertEqual(res.returncode, 1)
        err_json = json.loads(res.stderr)
        self.assertEqual(err_json["status"], "ERROR")
        self.assertEqual(err_json["error_type"], "InvalidThresholdError")


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
        up = DStarUpdate(op="DELETE", u=1, v=4, label=0)
        with self.assertRaises(NonexistentEdgeDeletionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_atomic_edge_label_mismatch_deletion(self) -> None:
        up = DStarUpdate(op="DELETE", u=1, v=2, label=999)
        with self.assertRaises(EdgeLabelMismatchDeletionError):
            self.oracle.apply_update(up)
        self._assert_state_untouched()

    def test_update_resource_limit_failure_rolls_back_state(self) -> None:
        """Resource limit failure during update re-enumeration must not alter oracle state."""
        # Restrict max_candidates on an oracle so that candidate bound is exceeded on update
        Q = DStarGraph()
        for v in (1, 2):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        for v in range(1, 5):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)

        # max_candidates=10: P(4, 2) = 12 > 10, so initial snapshot raises ResourceLimitError
        with self.assertRaises(ResourceLimitError):
            DStarDynamicOracle(Q, G, k=0, max_candidates=10)

        # Create oracle with cap 20: initial P(4, 2) = 12 <= 20 succeeds
        oracle = DStarDynamicOracle(Q, G, k=0, max_candidates=20, max_data_vertices=5)
        init_edges = dict(oracle.G.edges)
        init_matches = set(oracle.current_matches)
        init_step = oracle.step

        # Attempt to insert an edge that pushes data vertices beyond cap max_data_vertices=5
        # Add vertex 5 to data graph first
        oracle.G.add_vertex(5, 0)
        oracle.G.add_vertex(6, 0)  # Now 6 vertices > max_data_vertices=5
        up = DStarUpdate(op="INSERT", u=5, v=6, label=0)
        with self.assertRaises(ResourceLimitError):
            oracle.apply_update(up)

        # State should be rolled back to test_g clone, oracle.current_matches and step intact
        self.assertEqual(oracle.current_matches, init_matches)
        self.assertEqual(oracle.step, init_step)


class TestDStarLambdaReference(unittest.TestCase):
    """Reference checks for global edge connectivity lambda(Q) across all 7 topologies."""

    def test_lambda_all_topologies(self) -> None:
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

        def make_c4():
            g = DStarGraph()
            for v in (1, 2, 3, 4):
                g.add_vertex(v, 0)
            g.add_edge(1, 2, 0)
            g.add_edge(2, 3, 0)
            g.add_edge(3, 4, 0)
            g.add_edge(4, 1, 0)
            return g

        def make_c6():
            g = DStarGraph()
            for v in range(1, 7):
                g.add_vertex(v, 0)
            for v in range(1, 6):
                g.add_edge(v, v + 1, 0)
            g.add_edge(6, 1, 0)
            return g

        def make_diamond():
            g = DStarGraph()
            for v in (1, 2, 3, 4):
                g.add_vertex(v, 0)
            g.add_edge(1, 2, 0)
            g.add_edge(1, 3, 0)
            g.add_edge(2, 3, 0)
            g.add_edge(2, 4, 0)
            g.add_edge(3, 4, 0)
            return g

        def make_k4():
            g = DStarGraph()
            for v in (1, 2, 3, 4):
                g.add_vertex(v, 0)
            for u in (1, 2, 3, 4):
                for v in (1, 2, 3, 4):
                    if u < v:
                        g.add_edge(u, v, 0)
            return g

        def make_k3_3():
            g = DStarGraph()
            for v in range(1, 7):
                g.add_vertex(v, 0)
            for u in (1, 2, 3):
                for v in (4, 5, 6):
                    g.add_edge(u, v, 0)
            return g

        topologies = [
            ("P4", make_p4(), 1),
            ("Star4", make_star4(), 1),
            ("C4", make_c4(), 2),
            ("C6", make_c6(), 2),
            ("Diamond", make_diamond(), 2),
            ("K4", make_k4(), 3),
            ("K3_3", make_k3_3(), 3),
        ]
        for name, g, expected_lam in topologies:
            with self.subTest(topology=name):
                self.assertEqual(compute_edge_connectivity(g, max_query_vertices=6), expected_lam)


class TestDStarQueryAndGraphIsolation(unittest.TestCase):
    """R5 / N01: DStarDynamicOracle must clone and isolate query and data graphs."""

    def test_caller_mutation_of_q_does_not_affect_oracle(self) -> None:
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

        self.assertEqual(delta.entered, [])
        self.assertEqual(delta.exited, [])
        self.assertEqual(len(oracle.current_matches), 8)

    def test_caller_mutation_of_g_does_not_affect_oracle(self) -> None:
        """Mutating the caller's data graph G after oracle init must not alter oracle state."""
        Q = DStarGraph()
        for v in (1, 2):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)

        G = DStarGraph()
        for v in (1, 2, 3):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)

        oracle = DStarDynamicOracle(Q, G, k=0)
        self.assertEqual(len(oracle.current_matches), 2)

        # Mutate caller's G by adding edge 2-3
        G.add_edge(2, 3, 0)

        # Oracle's G must not have edge 2-3
        self.assertFalse(oracle.G.has_edge(2, 3))
        self.assertEqual(len(oracle.current_matches), 2)


class TestDStarEarlyResourceGuards(unittest.TestCase):
    """R4 / F04 + N04: Resource limits must fail closed BEFORE exponential work."""

    @patch("scripts.dstar_oracle.compute_edge_connectivity")
    def test_oversized_query_rejects_before_cut_computation(self, mock_compute_lam) -> None:
        Q_oversized = DStarGraph()
        for v in range(1, 8):  # 7 vertices > default max 6
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

    def test_candidate_guard_fails_closed(self) -> None:
        G = DStarGraph()
        for v in range(1, 9):  # 8 vertices
            G.add_vertex(v, 0)

        Q = DStarGraph()
        for v in range(1, 7):  # 6 vertices, P(8, 6) = 20160
            Q.add_vertex(v, 0)
        for v in range(1, 6):
            Q.add_edge(v, v + 1, 0)

        # Cap candidates at 1000 (< 20160)
        with self.assertRaises(ResourceLimitError):
            enumerate_snapshot(Q, G, k=0, max_candidates=1000)

    def test_invalid_resource_cap_types_rejected(self) -> None:
        for bad_val in [True, -1, 0, 1.5, "100"]:
            with self.subTest(bad_val=bad_val):
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

    def test_malformed_json_cli_exits_1(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{invalid_json: true")
            f_path = f.name
        try:
            res = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "snapshot", "--query", f_path, "--graph", str(FIXTURES_DIR / "hand_k4_data.json"), "--k", "0"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            err_json = json.loads(res.stderr)
            self.assertEqual(err_json["status"], "ERROR")
            self.assertEqual(err_json["error_type"], "JSONDecodeError")
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_malformed_schema_cli_exits_1(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"random_key": 123}, f)
            f_path = f.name
        try:
            res = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "snapshot", "--query", f_path, "--graph", str(FIXTURES_DIR / "hand_k4_data.json"), "--k", "0"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            err_json = json.loads(res.stderr)
            self.assertEqual(err_json["status"], "ERROR")
            self.assertEqual(err_json["error_type"], "InvalidGraphError")
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_stream_missing_label_cli_exits_1(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"op": "INSERT", "u": 1, "v": 2}], f)  # missing 'label'
            f_path = f.name
        try:
            res = subprocess.run(
                [
                    sys.executable, str(SCRIPT_PATH), "stream",
                    "--query", str(FIXTURES_DIR / "hand_k4_query.json"),
                    "--graph", str(FIXTURES_DIR / "hand_k4_data.json"),
                    "--stream", f_path,
                    "--k", "0",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            err_json = json.loads(res.stderr)
            self.assertEqual(err_json["status"], "ERROR")
            self.assertEqual(err_json["error_type"], "InvalidGraphError")
            self.assertIn("Missing required field 'label'", err_json["error_message"])
        finally:
            Path(f_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
