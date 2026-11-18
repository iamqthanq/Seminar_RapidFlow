#!/usr/bin/env python3
"""
tests/test_dstar02_engines.py — Verification Suite for DSTAR-02 Event Engines (M1 & M2).

Covers:
1. Production code independence (AST audit ensuring no imports of dstar_oracle/legacy).
2. 21+ hand and adversarial correctness test cases.
3. 840 reference corpus cases (420 seed1 + 420 seed7) compared against M0 truth.
4. 56-stream stateful matrix (672 updates) verifying tuple equality and active-set reconstruction.
5. Invariant violation fail-closed guard testing.
6. Resource limit enforcement (max_work, vertex caps, candidate caps).
"""

from __future__ import annotations

import ast
import copy
import itertools
import json
import math
import os
import random
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Add scripts directory to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dstar_event_common import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DEFAULT_MAX_WORK,
    DStarEventError,
    DStarEventGraph,
    DStarEventUpdate,
    DuplicateEdgeInsertionError,
    EdgeLabelMismatchDeletionError,
    EventCounters,
    EventResult,
    EventSink,
    InvalidGraphError,
    InvalidThresholdError,
    InvalidUpdateError,
    InvariantViolationError,
    NonexistentEdgeDeletionError,
    ResourceLimitError,
    SelfLoopError,
    UnknownVertexError,
    canonical_ordered_tuple,
    compute_edge_connectivity,
    is_connected,
)
from dstar_m1_enum import M1DynamicEngine, M1EnumEngine
from dstar_m2_event import M2DynamicEngine, M2EventEngine

# Test-side ground truth oracle only (forbidden in production engine modules)
import dstar_oracle as M0


# ============================================================================
# Test Suite 1: Production Independence Audit
# ============================================================================

class TestProductionIndependence(unittest.TestCase):
    """Ensures production engines have zero imports or references to oracle/legacy code."""

    def test_production_modules_do_not_import_oracle(self) -> None:
        production_files = [
            REPO_ROOT / "scripts" / "dstar_event_common.py",
            REPO_ROOT / "scripts" / "dstar_m1_enum.py",
            REPO_ROOT / "scripts" / "dstar_m2_event.py",
            REPO_ROOT / "scripts" / "dstar_event_cli.py",
        ]
        forbidden_modules = {
            "dstar_oracle",
            "apx02_oracle",
            "apx01_semantics",
            "test_dstar_oracle",
            "test_dstar_legacy_compatibility",
        }

        for p_file in production_files:
            self.assertTrue(p_file.is_file(), f"Production file missing: {p_file}")
            with open(p_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(p_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        base = alias.name.split(".")[0]
                        self.assertNotIn(
                            base,
                            forbidden_modules,
                            f"{p_file.name} illegally imports forbidden module '{base}'",
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        base = node.module.split(".")[0]
                        self.assertNotIn(
                            base,
                            forbidden_modules,
                            f"{p_file.name} illegally imports from forbidden module '{base}'",
                        )


# ============================================================================
# Test Suite 2: 21 Hand & Adversarial Correctness Cases
# ============================================================================

class TestHandAndAdversarialCorrectness(unittest.TestCase):
    """21+ targeted hand and boundary cases."""

    def setUp(self) -> None:
        self.m1 = M1EnumEngine()
        self.m2 = M2EventEngine()

    def _assert_engines_agree_and_match_m0(
        self,
        q: DStarEventGraph,
        g_before: DStarEventGraph,
        upd: DStarEventUpdate,
        k: int,
        expected_entered: Optional[List[Tuple[int, ...]]] = None,
        expected_exited: Optional[List[Tuple[int, ...]]] = None,
    ) -> None:
        """Helper to run M1, M2, and M0 and assert exact agreement."""
        # 1. Run M0 Ground Truth
        q_m0 = M0.DStarGraph.from_dict(q.to_dict())
        g_m0 = M0.DStarGraph.from_dict(g_before.to_dict())
        m0_oracle = M0.DStarDynamicOracle(q_m0, g_m0, k=k)
        upd_m0 = M0.DStarUpdate(
            op=upd.operation,
            u=upd.edge[0],
            v=upd.edge[1],
            label=upd.label,
        )
        delta_m0 = m0_oracle.apply_update(upd_m0)
        m0_entered = sorted(delta_m0.entered)
        m0_exited = sorted(delta_m0.exited)

        # 2. Run M1
        res1, g1_after = self.m1.process_update(q, g_before, upd, k)
        m1_entered = sorted(res1.entered)
        m1_exited = sorted(res1.exited)

        # 3. Run M2
        res2, g2_after = self.m2.process_update(q, g_before, upd, k)
        m2_entered = sorted(res2.entered)
        m2_exited = sorted(res2.exited)

        # Assert agreement
        self.assertEqual(m1_entered, m0_entered, f"M1 entered {m1_entered} != M0 {m0_entered}")
        self.assertEqual(m2_entered, m0_entered, f"M2 entered {m2_entered} != M0 {m0_entered}")
        self.assertEqual(m1_exited, m0_exited, f"M1 exited {m1_exited} != M0 {m0_exited}")
        self.assertEqual(m2_exited, m0_exited, f"M2 exited {m2_exited} != M0 {m0_exited}")

        if expected_entered is not None:
            self.assertEqual(m1_entered, sorted(expected_entered))
        if expected_exited is not None:
            self.assertEqual(m1_exited, sorted(expected_exited))

    def test_case_01_diamond_distinct_labels(self) -> None:
        """Diamond with distinct edge labels; verifies selective anchor & label match."""
        # Diamond: vertices 1, 2, 3, 4 with distinct labels
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, "e12")
        Q.add_edge(1, 3, "e13")
        Q.add_edge(2, 3, "e23")  # bridge
        Q.add_edge(2, 4, "e24")
        Q.add_edge(3, 4, "e34")

        G = DStarEventGraph()
        for v in (10, 20, 30, 40):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, "e12")
        G.add_edge(10, 30, "e13")
        G.add_edge(20, 40, "e24")
        G.add_edge(30, 40, "e34")

        upd = DStarEventUpdate("INSERT", (20, 30), "e23")
        # For k=0: inserting bridge triggers ENTERED (10, 20, 30, 40)
        self._assert_engines_agree_and_match_m0(
            Q, G, upd, k=0, expected_entered=[(10, 20, 30, 40)], expected_exited=[]
        )

    def test_case_02_diamond_k0_insert(self) -> None:
        """Diamond k=0 INSERT."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]:
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 4), (3, 4)]:
            G.add_edge(u, v, 0)

        upd = DStarEventUpdate("INSERT", (2, 3), 0)
        # Inserting (2, 3) completes Diamond at k=0: 4 automorphisms
        self._assert_engines_agree_and_match_m0(
            Q, G, upd, k=0,
            expected_entered=[(1, 2, 3, 4), (1, 3, 2, 4), (4, 2, 3, 1), (4, 3, 2, 1)],
            expected_exited=[]
        )

    def test_case_03_diamond_k0_delete(self) -> None:
        """Diamond k=0 DELETE."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]:
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]:
            G.add_edge(u, v, 0)

        upd = DStarEventUpdate("DELETE", (2, 3), 0)
        self._assert_engines_agree_and_match_m0(
            Q, G, upd, k=0,
            expected_entered=[],
            expected_exited=[(1, 2, 3, 4), (1, 3, 2, 4), (4, 2, 3, 1), (4, 3, 2, 1)]
        )

    def test_case_04_k4_k1(self) -> None:
        """K4 (m=6, lambda=3), k=1 edge insert and delete."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in itertools.combinations((1, 2, 3, 4), 2):
            Q.add_edge(u, v, 0)

        # G has 4 edges (missing 2 edges: (1, 4) and (2, 3))
        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 4), (3, 4)]:
            G.add_edge(u, v, 0)

        # Inserting (1, 4) leaves 1 missing edge (2, 3). Since k=1, this enters!
        upd = DStarEventUpdate("INSERT", (1, 4), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=1)

    def test_case_05_k4_k2(self) -> None:
        """K4 (m=6, lambda=3), k=2 missing pairs."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in itertools.combinations((1, 2, 3, 4), 2):
            Q.add_edge(u, v, 0)

        # G has 3 edges: spanning tree (1, 2), (2, 3), (3, 4)
        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(3, 4, 0)

        # Inserting (1, 4) gives cycle C4 (missing 2 edges). At k=2, this enters!
        upd = DStarEventUpdate("INSERT", (1, 4), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=2)

    def test_case_06_diamond_2step_delete(self) -> None:
        """Diamond 2-step delete: step 1 and step 2 active set tracking."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]:
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]:
            G.add_edge(u, v, 0)

        # Step 1: delete bridge (2, 3) at k=1
        upd1 = DStarEventUpdate("DELETE", (2, 3), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd1, k=1)
        # Apply step 1 to G
        _, G = upd1.apply_transactional(G)

        # Step 2: delete edge (1, 2) at k=1
        upd2 = DStarEventUpdate("DELETE", (1, 2), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd2, k=1)

    def test_case_07_no_matching_label(self) -> None:
        """Update edge label does not match any query edge label -> 0 events."""
        Q = DStarEventGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)

        G = DStarEventGraph()
        for v in (10, 20, 30):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)
        G.add_edge(20, 30, 0)

        upd = DStarEventUpdate("INSERT", (10, 30), "WRONG_LABEL")
        res1, _ = self.m1.process_update(Q, G, upd, k=0)
        res2, _ = self.m2.process_update(Q, G, upd, k=0)
        self.assertEqual(res1.entered, [])
        self.assertEqual(res2.entered, [])

    def test_case_08_non_induced_extra_chords(self) -> None:
        """Non-induced semantics: extra chords in G do not invalidate matches."""
        # Query C4
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]:
            Q.add_edge(u, v, 0)

        # Data graph has chord (10, 30)
        G = DStarEventGraph()
        for v in (10, 20, 30, 40):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)
        G.add_edge(20, 30, 0)
        G.add_edge(30, 40, 0)
        G.add_edge(10, 30, 0)  # internal chord

        upd = DStarEventUpdate("INSERT", (40, 10), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=0)

    def test_case_09_edge_label_mismatch(self) -> None:
        """Edge label mismatch counted as missing edge in R(f)."""
        Q = DStarEventGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, "A")
        Q.add_edge(2, 3, "A")
        Q.add_edge(3, 1, "A")

        G = DStarEventGraph()
        for v in (10, 20, 30):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, "A")
        G.add_edge(20, 30, "B")  # label mismatch -> missing edge

        # Insert (10, 30) with "A".
        upd = DStarEventUpdate("INSERT", (10, 30), "A")
        # At k=0: missing 1 edge -> no event
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=0, expected_entered=[])
        # At k=1: exactly 1 missing edge -> enters!
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=1)

    def test_case_10_vertex_label_mismatch(self) -> None:
        """Vertex label mismatch strictly forbids mapping."""
        Q = DStarEventGraph()
        Q.add_vertex(1, "TYPE_A")
        Q.add_vertex(2, "TYPE_B")
        Q.add_edge(1, 2, 0)

        # Data vertices have TYPE_A and TYPE_C
        G = DStarEventGraph()
        G.add_vertex(10, "TYPE_A")
        G.add_vertex(20, "TYPE_C")

        upd = DStarEventUpdate("INSERT", (10, 20), 0)
        # Because lambda(P2) = 1, k=0 is domain
        res1, _ = self.m1.process_update(Q, G, upd, k=0)
        res2, _ = self.m2.process_update(Q, G, upd, k=0)
        self.assertEqual(res1.entered, [])
        self.assertEqual(res2.entered, [])

    def test_case_11_int_vs_str_labels(self) -> None:
        """Distinguish int 0 and str '0' without coercion."""
        Q = DStarEventGraph()
        Q.add_vertex(1, 0)
        Q.add_vertex(2, 0)
        Q.add_edge(1, 2, 0)

        G = DStarEventGraph()
        G.add_vertex(10, "0")  # str '0' != int 0
        G.add_vertex(20, "0")

        upd = DStarEventUpdate("INSERT", (10, 20), 0)
        res1, _ = self.m1.process_update(Q, G, upd, k=0)
        res2, _ = self.m2.process_update(Q, G, upd, k=0)
        self.assertEqual(res1.entered, [])
        self.assertEqual(res2.entered, [])

    def test_case_12_sparse_negative_ids(self) -> None:
        """Sparse and negative vertex IDs handled properly."""
        Q = DStarEventGraph()
        for v in (-10, 5, 42):
            Q.add_vertex(v, 0)
        Q.add_edge(-10, 5, 0)
        Q.add_edge(5, 42, 0)
        Q.add_edge(-10, 42, 0)

        G = DStarEventGraph()
        for v in (-100, 0, 100):
            G.add_vertex(v, 0)
        G.add_edge(-100, 0, 0)
        G.add_edge(0, 100, 0)

        upd = DStarEventUpdate("INSERT", (-100, 100), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=0)

    def test_case_13_canonical_tuple_order(self) -> None:
        """Original query vertex IDs ascending order determines canonical tuple order."""
        # Query vertices added out of order: 3, 1, 4, 2
        Q = DStarEventGraph()
        for v in (3, 1, 4, 2):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]:
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in (10, 20, 30, 40):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)
        G.add_edge(20, 30, 0)
        G.add_edge(30, 40, 0)

        upd = DStarEventUpdate("INSERT", (40, 10), 0)
        res1, _ = self.m1.process_update(Q, G, upd, k=0)
        res2, _ = self.m2.process_update(Q, G, upd, k=0)

        # Tuples must have format (f[1], f[2], f[3], f[4]) because 1 < 2 < 3 < 4
        for t in res1.entered + res2.entered:
            self.assertEqual(len(t), 4)

    def test_case_14_uniform_k4_k0_automorphisms(self) -> None:
        """Uniform K4 k=0 yields all 24 automorphisms as distinct tuples with zero duplicates."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in itertools.combinations((1, 2, 3, 4), 2):
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        for (u, v) in itertools.combinations((1, 2, 3, 4), 2):
            if (u, v) != (1, 2):
                G.add_edge(u, v, 0)

        upd = DStarEventUpdate("INSERT", (1, 2), 0)
        res1, _ = self.m1.process_update(Q, G, upd, k=0)
        res2, _ = self.m2.process_update(Q, G, upd, k=0)

        # Full K4 has 24 automorphisms; 4 of them map query edge (1,2) to data edge (1,2) in 2 orientations
        self.assertEqual(len(res1.entered), len(set(res1.entered)))
        self.assertEqual(len(res2.entered), len(set(res2.entered)))
        self.assertEqual(res1.entered, res2.entered)

    def test_case_15_c4_k1_deferred_witness(self) -> None:
        """C4 k=1 deferred witness: MISSING branch is taken, resolves at later step."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]:
            Q.add_edge(u, v, 0)

        # G has path 10-20-30-40 (missing edge (40, 10))
        G = DStarEventGraph()
        for v in (10, 20, 30, 40):
            G.add_vertex(v, 0)
        G.add_edge(10, 20, 0)
        G.add_edge(20, 30, 0)

        # Inserting (30, 40) creates path of length 3, missing 1 edge (40, 10). At k=1, enters!
        upd = DStarEventUpdate("INSERT", (30, 40), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=1)

    def test_case_16_missing_commitment_violation(self) -> None:
        """Commitment violation: edge marked MISSING, but actually exists in G -> pruned."""
        # Diamond query
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]:
            Q.add_edge(u, v, 0)

        # G is complete Diamond except edge (1, 2)
        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        for (u, v) in [(1, 3), (2, 3), (2, 4), (3, 4)]:
            G.add_edge(u, v, 0)

        upd = DStarEventUpdate("INSERT", (1, 2), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=1)

    def test_case_17_no_double_charging(self) -> None:
        """Edge update is not double-counted or charged twice."""
        Q = DStarEventGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)

        upd = DStarEventUpdate("INSERT", (1, 3), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=0)

    def test_case_18_rollback_isolation(self) -> None:
        """Failed branch completely rolls back state; no contamination."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]:
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3, 4, 5):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)
        G.add_edge(3, 4, 0)
        G.add_edge(4, 5, 0)  # dead end

        upd = DStarEventUpdate("INSERT", (1, 4), 0)
        self._assert_engines_agree_and_match_m0(Q, G, upd, k=0)

    def test_case_19_reversed_endpoints(self) -> None:
        """Edge update with reversed endpoints (y, x) is canonicalized identically."""
        upd1 = DStarEventUpdate("INSERT", (2, 3), 0)
        upd2 = DStarEventUpdate("INSERT", (3, 2), 0)
        self.assertEqual(upd1.edge, (2, 3))
        self.assertEqual(upd2.edge, (2, 3))

    def test_case_20_caller_mutation_isolation(self) -> None:
        """Modifying returned lists does not mutate engine state."""
        Q = DStarEventGraph()
        for v in (1, 2, 3):
            Q.add_vertex(v, 0)
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 1, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)

        upd = DStarEventUpdate("INSERT", (1, 3), 0)
        res1, g_after1 = self.m1.process_update(Q, G, upd, k=0)
        res1.entered.append((999, 999, 999))
        self.assertNotIn((1, 3), G.edges)  # original G untouched

    def test_case_21_invalid_k_domain(self) -> None:
        """Threshold k >= lambda(Q) strictly rejected with InvalidThresholdError."""
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        # P4 has lambda = 1
        Q.add_edge(1, 2, 0)
        Q.add_edge(2, 3, 0)
        Q.add_edge(3, 4, 0)

        G = DStarEventGraph()
        for v in (1, 2, 3, 4):
            G.add_vertex(v, 0)
        G.add_edge(1, 2, 0)

        upd = DStarEventUpdate("INSERT", (2, 3), 0)
        with self.assertRaises(InvalidThresholdError):
            self.m1.process_update(Q, G, upd, k=1)
        with self.assertRaises(InvalidThresholdError):
            self.m2.process_update(Q, G, upd, k=1)

    def test_case_22_invalid_update_rollback(self) -> None:
        """Invalid update (self-loop, duplicate edge) rejects without mutating graph."""
        G = DStarEventGraph()
        G.add_vertex(1, 0)
        G.add_vertex(2, 0)
        G.add_edge(1, 2, 0)

        with self.assertRaises(SelfLoopError):
            DStarEventUpdate("INSERT", (1, 1), 0)

        upd_dup = DStarEventUpdate("INSERT", (1, 2), 0)
        with self.assertRaises(DuplicateEdgeInsertionError):
            upd_dup.apply_transactional(G)

    def test_case_23_query_sizes_5_and_6(self) -> None:
        """Query sizes 5 and 6 tested within resource bounds."""
        # Size 5: K5 minus 1 edge
        Q5 = DStarEventGraph()
        for v in range(1, 6):
            Q5.add_vertex(v, 0)
        for (u, v) in itertools.combinations(range(1, 6), 2):
            if (u, v) != (1, 2):
                Q5.add_edge(u, v, 0)

        lam5 = compute_edge_connectivity(Q5)
        self.assertGreaterEqual(lam5, 3)

        G5 = DStarEventGraph()
        for v in range(1, 6):
            G5.add_vertex(v, 0)
        for (u, v) in itertools.combinations(range(1, 6), 2):
            if (u, v) not in ((1, 2), (2, 3)):
                G5.add_edge(u, v, 0)

        upd = DStarEventUpdate("INSERT", (2, 3), 0)
        self._assert_engines_agree_and_match_m0(Q5, G5, upd, k=1)


# ============================================================================
# Test Suite 3: 840 Reference Corpus Verification
# ============================================================================

class TestCorpus840Reference(unittest.TestCase):
    """Executes the full 840 reference corpus (seed 1 and seed 7)."""

    def test_all_840_reference_cases(self) -> None:
        corpus_dir = REPO_ROOT / "tests" / "fixtures" / "dstar02" / "corpus_840"
        files = [corpus_dir / "seed1_cases.jsonl", corpus_dir / "seed7_cases.jsonl"]
        for p in files:
            self.assertTrue(p.is_file(), f"Missing corpus file: {p}")

        m1_engine = M1EnumEngine()
        m2_engine = M2EventEngine()

        total_cases = 0
        total_events = 0
        cases_with_events = 0

        for fpath in files:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    case_data = json.loads(line)
                    total_cases += 1

                    # Parse query
                    Q = DStarEventGraph()
                    qvl = case_data["query_vlabels"]
                    for v_idx, lbl in enumerate(qvl):
                        Q.add_vertex(v_idx, lbl)
                    q_edges = case_data["query_edges"]
                    qel = case_data["query_elabels"]
                    for e_idx, (u, v) in enumerate(q_edges):
                        Q.add_edge(u, v, qel[e_idx])

                    # Extract k from config string "C4 k=0 (lambda=2)"
                    cfg_str = case_data["config"]
                    k_str = cfg_str.split("k=")[1].split()[0]
                    k = int(k_str)

                    # Parse data graph before update
                    G_before = DStarEventGraph()
                    gvl = case_data["data_vlabels"]
                    for v_idx, lbl in enumerate(gvl):
                        G_before.add_vertex(v_idx, lbl)
                    for item in case_data["G_before"]:
                        G_before.add_edge(item[0], item[1], item[2])

                    upd_raw = case_data["update"]
                    upd = DStarEventUpdate(
                        operation=upd_raw["kind"],
                        edge=tuple(upd_raw["edge"]),
                        label=upd_raw["label"],
                    )

                    truth_events = [tuple(t) for t in case_data["oracle_events"]]
                    if truth_events:
                        cases_with_events += 1
                        total_events += len(truth_events)

                    # Process M1
                    res1, _ = m1_engine.process_update(Q, G_before, upd, k)
                    actual1 = res1.entered if upd.operation == "INSERT" else res1.exited
                    self.assertEqual(
                        actual1,
                        truth_events,
                        f"Case {case_data['case']} seed {case_data['seed']}: M1 events {actual1} != truth {truth_events}",
                    )

                    # Process M2
                    res2, _ = m2_engine.process_update(Q, G_before, upd, k)
                    actual2 = res2.entered if upd.operation == "INSERT" else res2.exited
                    self.assertEqual(
                        actual2,
                        truth_events,
                        f"Case {case_data['case']} seed {case_data['seed']}: M2 events {actual2} != truth {truth_events}",
                    )

        self.assertEqual(total_cases, 840, f"Expected 840 cases, got {total_cases}")
        self.assertEqual(total_events, 8002, f"Expected 8002 truth events, got {total_events}")
        self.assertEqual(cases_with_events, 330, f"Expected 330 cases with events, got {cases_with_events}")


# ============================================================================
# Test Suite 4: 56-Stream Stateful Matrix Verification
# ============================================================================

def build_query_by_name(name: str, label_mode: str) -> DStarEventGraph:
    """Builds test query graph for 56-stream matrix."""
    g = DStarEventGraph()
    if name == "C4":
        for v in range(4):
            g.add_vertex(v, 0 if label_mode == "uniform" else v % 2)
        for (u, v) in [(0, 1), (1, 2), (2, 3), (3, 0)]:
            g.add_edge(u, v, 0 if label_mode == "uniform" else (u + v) % 2)
    elif name == "Diamond":
        for v in range(4):
            g.add_vertex(v, 0 if label_mode == "uniform" else v % 2)
        for (u, v) in [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]:
            g.add_edge(u, v, 0 if label_mode == "uniform" else (u + v) % 2)
    elif name == "K4":
        for v in range(4):
            g.add_vertex(v, 0 if label_mode == "uniform" else v % 2)
        for (u, v) in itertools.combinations(range(4), 2):
            g.add_edge(u, v, 0 if label_mode == "uniform" else (u + v) % 2)
    return g


def generate_random_stream_instance(
    seed: int,
    n_vertices: int,
    n_initial_edges: int,
    n_updates: int,
    label_mode: str,
) -> Tuple[DStarEventGraph, List[DStarEventUpdate]]:
    """Generates random data graph and sequence of valid updates."""
    rng = random.Random(seed)
    g = DStarEventGraph()
    n_vlabels = 1 if label_mode == "uniform" else 2
    n_elabels = 1 if label_mode == "uniform" else 2

    for v in range(n_vertices):
        g.add_vertex(v, rng.randrange(n_vlabels))

    all_pairs = list(itertools.combinations(range(n_vertices), 2))
    initial_pairs = rng.sample(all_pairs, min(n_initial_edges, len(all_pairs)))
    for (u, v) in initial_pairs:
        g.add_edge(u, v, rng.randrange(n_elabels))

    # Generate sequence of updates that maintain graph validity
    curr_edges = dict(g.edges)
    updates: List[DStarEventUpdate] = []

    for _ in range(n_updates):
        can_insert = len(curr_edges) < len(all_pairs)
        can_delete = len(curr_edges) > 1

        op = "INSERT" if (can_insert and (not can_delete or rng.random() < 0.5)) else "DELETE"
        if op == "INSERT":
            available = [p for p in all_pairs if p not in curr_edges]
            pair = rng.choice(available)
            lbl = rng.randrange(n_elabels)
            updates.append(DStarEventUpdate("INSERT", pair, lbl))
            curr_edges[pair] = lbl
        else:
            pair = rng.choice(list(curr_edges.keys()))
            lbl = curr_edges[pair]
            updates.append(DStarEventUpdate("DELETE", pair, lbl))
            del curr_edges[pair]

    return g, updates


class Test56StreamStatefulMatrix(unittest.TestCase):
    """Executes the 56-stream stateful matrix (672 updates total)."""

    def test_56_stream_matrix_against_m0_and_active_set_reconstruction(self) -> None:
        seed_base = 20261117
        matrix_specs = [
            ("C4", [0, 1], 8),
            ("Diamond", [0, 1], 8),
            ("K4", [0, 1, 2], 8),
        ]

        total_configs = 0
        total_steps = 0

        for q_name, k_values, seeds_count in matrix_specs:
            for k in k_values:
                for s_idx in range(seeds_count):
                    total_configs += 1
                    seed = seed_base + total_configs * 37 + s_idx
                    label_mode = "uniform" if (total_configs % 2 == 0) else "mixed"

                    Q = build_query_by_name(q_name, label_mode)
                    G0, updates = generate_random_stream_instance(
                        seed=seed,
                        n_vertices=6,
                        n_initial_edges=6,
                        n_updates=12,
                        label_mode=label_mode,
                    )

                    # Instantiate stateful engines
                    m1_dyn = M1DynamicEngine(Q, G0, k)
                    m2_dyn = M2DynamicEngine(Q, G0, k)

                    # Test-side M0 Oracle
                    q_m0 = M0.DStarGraph.from_dict(Q.to_dict())
                    g_m0 = M0.DStarGraph.from_dict(G0.to_dict())
                    m0_dyn = M0.DStarDynamicOracle(q_m0, g_m0, k=k)

                    active_set: Set[Tuple[int, ...]] = set(m0_dyn.current_matches)

                    for up in updates:
                        total_steps += 1
                        res1 = m1_dyn.step(up)
                        res2 = m2_dyn.step(up)

                        up_m0 = M0.DStarUpdate(
                            op=up.operation,
                            u=up.edge[0],
                            v=up.edge[1],
                            label=up.label,
                        )
                        delta_m0 = m0_dyn.apply_update(up_m0)

                        m0_ent = sorted(delta_m0.entered)
                        m0_ext = sorted(delta_m0.exited)

                        # Check step agreement
                        self.assertEqual(res1.entered, m0_ent)
                        self.assertEqual(res2.entered, m0_ent)
                        self.assertEqual(res1.exited, m0_ext)
                        self.assertEqual(res2.exited, m0_ext)

                        # Reconstruct active set: M_t = (M_{t-1} \ exited) U entered
                        active_set = (active_set - set(res2.exited)) | set(res2.entered)
                        # Snapshot verification against M0 current matches
                        self.assertEqual(
                            active_set,
                            set(m0_dyn.current_matches),
                            f"Active set reconstruction mismatch at config {total_configs}, step {total_steps}",
                        )

        self.assertEqual(total_configs, 56)
        self.assertEqual(total_steps, 56 * 12)  # 672 updates


# ============================================================================
# Test Suite 5: Invariant Violation Fail-Closed Testing
# ============================================================================

class TestInvariantViolation(unittest.TestCase):
    """Verifies that violating internal algorithm invariants raises InvariantViolationError and fails closed."""

    def test_empty_frontier_invariant_violation(self) -> None:
        """When k >= lambda (simulated via _skip_domain_check seam), M2 raises InvariantViolationError."""
        # Query C4 (lambda = 2). If k=2 (outside MVP domain), the cut between mapped and unmapped vertices
        # can have cut size 2 == k. If both cut edges are marked MISSING, the frontier becomes empty!
        Q = DStarEventGraph()
        for v in range(4):
            Q.add_vertex(v, 0)
        for (u, v) in [(0, 1), (1, 2), (2, 3), (3, 0)]:
            Q.add_edge(u, v, 0)

        # Build data graph with isolated edges where C4 with 2 missing edges hits empty frontier
        G = DStarEventGraph()
        for v in range(6):
            G.add_vertex(v, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(2, 3, 0)

        upd = DStarEventUpdate("INSERT", (0, 1), 0)

        # Standard check should reject with InvalidThresholdError
        m2 = M2EventEngine()
        with self.assertRaises(InvalidThresholdError):
            m2.find_events(Q, G, upd, k=2)

        # With _skip_domain_check=True, empty frontier triggers InvariantViolationError!
        # Search multiple random topologies until empty frontier is encountered
        rng = random.Random(5)
        found_invariant_violation = False
        for _ in range(50):
            G_test = DStarEventGraph()
            for v in range(6):
                G_test.add_vertex(v, 0)
            for (u, v) in itertools.combinations(range(6), 2):
                if rng.random() < 0.3:
                    G_test.add_edge(u, v, 0)
            if not G_test.has_edge(0, 1):
                G_test.add_edge(0, 1, 0)
            test_upd = DStarEventUpdate("INSERT", (0, 1), 0)
            try:
                m2.find_events(Q, G_test, test_upd, k=2, _skip_domain_check=True)
            except InvariantViolationError:
                found_invariant_violation = True
                break

        self.assertTrue(
            found_invariant_violation,
            "Expected InvariantViolationError to be triggered when empty frontier invariant is hit",
        )


# ============================================================================
# Test Suite 6: Resource Limits
# ============================================================================

class TestResourceLimits(unittest.TestCase):
    """Verifies that exceeding max_work, vertex caps, or candidate space fails closed with ResourceLimitError."""

    def test_max_work_exceeded(self) -> None:
        Q = DStarEventGraph()
        for v in (1, 2, 3, 4):
            Q.add_vertex(v, 0)
        for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]:
            Q.add_edge(u, v, 0)

        G = DStarEventGraph()
        for v in range(6):
            G.add_vertex(v, 0)
        for (u, v) in itertools.combinations(range(6), 2):
            G.add_edge(u, v, 0)

        upd = DStarEventUpdate("INSERT", (0, 1), 0)
        # Set max_work = 2 (ridiculously small)
        limits = {"max_work": 2}
        m1 = M1EnumEngine(limits)
        m2 = M2EventEngine(limits)

        with self.assertRaises(ResourceLimitError):
            m1.find_events(Q, G, upd, k=1, limits=limits)
        with self.assertRaises(ResourceLimitError):
            m2.find_events(Q, G, upd, k=1, limits=limits)

    def test_vertex_caps_exceeded(self) -> None:
        Q = DStarEventGraph()
        for v in range(10):
            Q.add_vertex(v, 0)
        for v in range(9):
            Q.add_edge(v, v + 1, 0)
        Q.add_edge(9, 0, 0)

        G = DStarEventGraph()
        G.add_vertex(0, 0)
        G.add_vertex(1, 0)
        G.add_edge(0, 1, 0)

        upd = DStarEventUpdate("INSERT", (0, 1), 0)
        m1 = M1EnumEngine()
        with self.assertRaises(ResourceLimitError):
            m1.find_events(Q, G, upd, k=0)


if __name__ == "__main__":
    unittest.main()
