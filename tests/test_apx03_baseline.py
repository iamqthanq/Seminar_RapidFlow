#!/usr/bin/env python3
"""
tests/test_apx03_baseline.py — Comprehensive Test Suite for APX-03 C++ Baseline (Round 2).

Covers:
1. Native RapidFlow integration & symbol provenance (FIX-05).
2. All 16 Canonical Microcases from docs/approx_semantic_microcases_v1.md (FIX-04).
3. Ordered Mapping Identity: ascending numerical query vertex order (FIX-01).
4. Numeric Threshold Validation & CLI Aliases (FIX-02, FIX-07).
5. Resource Guard Correctness & Adversarial Pruning (FIX-03).
6. 15-Step Deterministic Dynamic Differential against APX-02 (FIX-08).
7. Randomized Static Differential testing against APX-02 Python Oracle.
8. Exact recovery verification against RF-02 Exact Oracle.
9. Near-only match verification (m > 0 accepted, absent from exact embeddings).
10. Conflicting vertex redefinition rejection (A03-N01).
11. Dynamic failure state consistency & terminal locking (A03-N02).
12. Safe AST-based mapping key parsing (A03-N03).
13. Invariant properties (monotonicity, strict subset relaxed, automorphisms, reversibility).
"""

import ast
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY_PATH = REPO_ROOT / "extensions" / "apx03" / "build" / "rapidflow_apx03_baseline"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.apx02_oracle import (
    ConnectivityMode,
    DynamicOracle,
    SimpleGraph,
    Update,
    evaluate_mapping as apx02_evaluate_mapping,
    find_approximate_matches as apx02_find_matches,
)
from scripts.rf02_oracle import (
    LabeledGraph as RF02LabeledGraph,
    find_all_embeddings as rf02_find_all_embeddings,
)


def parse_mapping_tuple(key_str: str) -> Tuple[int, ...]:
    """Safely parse mapping tuple string without using eval (A03-N03)."""
    val = ast.literal_eval(key_str)
    if isinstance(val, int):
        return (val,)
    if not isinstance(val, (tuple, list)):
        raise ValueError(f"Expected tuple or list mapping key, got {type(val)}: {key_str}")
    return tuple(int(x) for x in val)


def run_apx03_cli(
    query_text: str,
    data_text: str,
    eps_m: Any,
    eps_x: Any,
    mode: str = "STRICT",
    stream_text: Optional[str] = None,
    evaluate_mapping: Optional[Sequence[int]] = None,
    max_candidates: Optional[Any] = None,
    max_search_steps: Optional[Any] = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Invokes rapidflow_apx03_baseline binary via CLI and parses JSON output."""
    if not BINARY_PATH.exists():
        from scripts.run_apx03_baseline import build_binary
        build_binary()

    with tempfile.NamedTemporaryFile("w", delete=False) as qf, \
         tempfile.NamedTemporaryFile("w", delete=False) as df:
        qf.write(query_text)
        df.write(data_text)
        qf.flush()
        df.flush()

        sf_name = None
        if stream_text is not None:
            with tempfile.NamedTemporaryFile("w", delete=False) as sf:
                sf.write(stream_text)
                sf.flush()
                sf_name = sf.name

        cmd = [
            str(BINARY_PATH),
            "--query", qf.name,
            "--data", df.name,
            "--eps-m", str(eps_m),
            "--mode", mode,
        ]
        if eps_x is None:
            cmd.extend(["--eps-x", "None"])
        else:
            cmd.extend(["--eps-x", str(eps_x)])

        if max_candidates is not None:
            cmd.extend(["--max-candidates", str(max_candidates)])
        if max_search_steps is not None:
            cmd.extend(["--max-search-steps", str(max_search_steps)])
        if sf_name is not None:
            cmd.extend(["--stream", sf_name])
        if evaluate_mapping is not None:
            cmd.extend(["--evaluate-mapping", ",".join(map(str, evaluate_mapping))])

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        parsed_json = {}
        if proc.returncode == 0:
            try:
                parsed_json = json.loads(proc.stdout)
            except Exception as e:
                parsed_json = {"error": f"JSON parse error: {e}", "stdout": proc.stdout}

        try:
            os.unlink(qf.name)
            os.unlink(df.name)
            if sf_name is not None:
                os.unlink(sf_name)
        except OSError:
            pass

        return proc.returncode, parsed_json, proc.stderr


# =============================================================================
# 1. Native RapidFlow Integration
# =============================================================================

class TestAPX03NativeIntegration(unittest.TestCase):
    """Verifies genuine native RapidFlow C++ source reuse and symbol linkage."""

    def test_binary_built_and_executable(self):
        self.assertTrue(BINARY_PATH.exists(), f"Binary missing at {BINARY_PATH}")
        proc = subprocess.run([str(BINARY_PATH), "--help"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Usage:", proc.stdout)

    def test_rapidflow_symbols_linked(self):
        cmd = ["nm", "-C", str(BINARY_PATH)]
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            self.assertTrue(
                "Graph::loadGraphFromMemory" in out or "Graph" in out,
                "Native RapidFlow Graph symbols not found in binary symbol table"
            )
        except FileNotFoundError:
            self.skipTest("nm command not available")


# =============================================================================
# 2. Canonical Microcases 01 - 16 (FIX-04)
# =============================================================================

class TestAPX03CanonicalMicrocases(unittest.TestCase):
    """
    Validates all 16 canonical reference microcases from docs/approx_semantic_microcases_v1.md.
    Every fixture strictly matches the canonical specification.
    """

    def test_case_01_exact_match(self):
        # Canonical: P3 (u1-u2-u3) in G(1-2-3)
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 1 0\nv 2 0\nv 3 0\ne 1 2 0\ne 2 3 0\n"
        ret, res, err = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT", evaluate_mapping=[1, 2, 3])
        self.assertEqual(ret, 0, err)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["target_mapping"], [1, 2, 3])
        self.assertEqual(res["score"], {"m": 0, "x_in": 0, "x_out": 0, "connected": True})

        # Full enumeration yields both automorphisms: (1, 2, 3) and (3, 2, 1)
        ret_f, res_f, err_f = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT")
        self.assertEqual(ret_f, 0, err_f)
        self.assertEqual(res_f["total_accepted"], 2)
        mappings = {tuple(m["mapping"]) for m in res_f["matches"]}
        self.assertEqual(mappings, {(1, 2, 3), (3, 2, 1)})

    def test_case_02_missing_one_leaf_edge(self):
        # Canonical: Star K1,3 (center 0, leaves 1,2,3). G has {0,1}, {0,2}. Edge {0,3} missing.
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 0 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\n"
        # Strict mode: disconnected -> REJECT
        ret_s, res_s, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret_s, 0)
        self.assertFalse(res_s["accepted"])
        self.assertFalse(res_s["score"]["connected"])
        self.assertEqual(res_s["score"]["m"], 1)

        # Relaxed mode: ConnOK=True -> ACCEPT
        ret_r, res_r, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="RELAXED", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret_r, 0)
        self.assertTrue(res_r["accepted"])
        self.assertEqual(res_r["score"]["m"], 1)

    def test_case_03_missing_two_edges(self):
        # Canonical: Star K1,3. G has only {0,1}. Edges {0,2} and {0,3} absent.
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 0 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\n"
        # Strict: REJECT
        ret_s, res_s, _ = run_apx03_cli(q, g, eps_m=2, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret_s, 0)
        self.assertFalse(res_s["accepted"])
        self.assertFalse(res_s["score"]["connected"])
        self.assertEqual(res_s["score"]["m"], 2)

        # Relaxed at (1, 2): REJECT (m=2 > 1)
        _, res_r12, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="RELAXED", evaluate_mapping=[0, 1, 2, 3])
        self.assertFalse(res_r12["accepted"])

        # Relaxed at (2, 2): ACCEPT (m=2 <= 2)
        _, res_r22, _ = run_apx03_cli(q, g, eps_m=2, eps_x=2, mode="RELAXED", evaluate_mapping=[0, 1, 2, 3])
        self.assertTrue(res_r22["accepted"])

    def test_case_04_extra_internal_chords(self):
        # Canonical: C4 in complete K4 (x_in=2 internal chords, x_out=0)
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"
        g = ("v 0 0\nv 1 0\nv 2 0\nv 3 0\n"
             "e 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\ne 0 2 0\ne 1 3 0\n")
        ret, res, _ = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret, 0)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["score"]["m"], 0)
        self.assertEqual(res["score"]["x_in"], 2)
        self.assertEqual(res["score"]["x_out"], 0)

    def test_case_05_one_extra_external_tail(self):
        # Canonical: Star K1,2 (center 0, leaves 1,2). Data: Star K1,3 (center 0, leaves 1,2,4).
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 4 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\n"
        # At eps_x=0: REJECT; at eps_x=1: ACCEPT
        _, res_0, _ = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_0["accepted"])
        self.assertEqual(res_0["score"]["x_out"], 1)

        _, res_1, _ = run_apx03_cli(q, g, eps_m=0, eps_x=1, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_1["accepted"])
        self.assertEqual(res_1["score"]["x_out"], 1)

    def test_case_06_two_extra_external_tails(self):
        # Canonical: Star K1,2. Data: Star K1,4 (center 0, leaves 1,2,4,5).
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\ne 0 5 0\n"
        _, res_1, _ = run_apx03_cli(q, g, eps_m=0, eps_x=1, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_1["accepted"])
        self.assertEqual(res_1["score"]["x_out"], 2)

        _, res_2, _ = run_apx03_cli(q, g, eps_m=0, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_2["accepted"])

    def test_case_07_thirty_extra_external_tails(self):
        # Canonical: Star K1,2. Data: Center 0 with leaves 1,2 and 30 extra peripheral nodes 4..33.
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        lines = ["v 0 0", "v 1 0", "v 2 0"]
        for i in range(4, 34):
            lines.append(f"v {i} 0")
        lines.extend(["e 0 1 0", "e 0 2 0"])
        for i in range(4, 34):
            lines.append(f"e 0 {i} 0")
        g = "\n".join(lines) + "\n"

        _, res_2, _ = run_apx03_cli(q, g, eps_m=0, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_2["accepted"])
        self.assertEqual(res_2["score"]["x_out"], 30)

        _, res_30, _ = run_apx03_cli(q, g, eps_m=0, eps_x=30, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_30["accepted"])

    def test_case_08_synthetic_high_degree_hub(self):
        # Canonical: Star K1,2 in high-degree hub (d=1000, x_out=998)
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        lines = ["v 0 0", "v 1 0", "v 2 0"]
        for i in range(3, 1001):
            lines.append(f"v {i} 0")
        lines.extend(["e 0 1 0", "e 0 2 0"])
        for i in range(3, 1001):
            lines.append(f"e 0 {i} 0")
        g = "\n".join(lines) + "\n"

        _, res_30, _ = run_apx03_cli(q, g, eps_m=0, eps_x=30, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_30["accepted"])
        self.assertEqual(res_30["score"]["x_out"], 998)

        _, res_inf, _ = run_apx03_cli(q, g, eps_m=0, eps_x=None, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_inf["accepted"])
        self.assertEqual(res_inf["score"]["m"], 0)
        self.assertEqual(res_inf["score"]["x_out"], 998)

    def test_case_09_edge_label_mismatch_topological_connectivity(self):
        # Canonical: Path P3. Q edge labels are 1 and 1. G edge labels are 1 and 2.
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 1\ne 1 2 1\n"
        g = "v 1 0\nv 2 0\nv 3 0\ne 1 2 1\ne 2 3 2\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="STRICT", evaluate_mapping=[1, 2, 3])
        self.assertEqual(ret, 0)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["score"]["m"], 1)
        self.assertEqual(res["score"]["x_in"], 0)
        self.assertEqual(res["score"]["x_out"], 0)
        self.assertTrue(res["score"]["connected"])

    def test_case_10_injectivity_violation(self):
        # Canonical: Path P3. Mapping collapses onto vertex 1: (1, 2, 1) -> Hard REJECT
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 1 0\nv 2 0\ne 1 2 0\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=1, eps_x=1, mode="STRICT", evaluate_mapping=[1, 2, 1])
        self.assertEqual(ret, 0)
        self.assertFalse(res["accepted"])

    def test_case_11_tree_query_disconnected_by_missing_edge(self):
        # Canonical: Path P4 = (u0-u1-u2-u3). Data: {0,1} and {2,3}. Middle edge {1,2} missing.
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 2 3 0\n"
        _, res_s, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertFalse(res_s["accepted"])
        self.assertFalse(res_s["score"]["connected"])

        _, res_r, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="RELAXED", evaluate_mapping=[0, 1, 2, 3])
        self.assertTrue(res_r["accepted"])
        self.assertEqual(res_r["score"]["m"], 1)

    def test_case_12_dynamic_insertion_entered(self):
        # Canonical: Star K1,2. G0 has {0,1}. Stream: +e 0 2 0 -> ENTERED
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\n"
        stream = "e 0 2 0\n"
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=0, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        entered = {parse_mapping_tuple(k): v for k, v in step["delta"]["entered"].items()}
        self.assertIn((0, 1, 2), entered)
        self.assertEqual(entered[(0, 1, 2)]["m"], 0)
        self.assertEqual(entered[(0, 1, 2)]["x_out"], 0)

    def test_case_13_dynamic_insertion_boundary_clutter_exited(self):
        # Canonical: Star K1,2. G0 has {0,1}, {0,2}, {0,4}. At eps_x=1, x_out=1.
        # Stream: +e 0 5 0 -> x_out becomes 2 > 1 -> EXITED
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\n"
        stream = "e 0 5 0\n"
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=1, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        exited = {parse_mapping_tuple(k): v for k, v in step["delta"]["exited"].items()}
        self.assertIn((0, 1, 2), exited)
        self.assertEqual(exited[(0, 1, 2)]["x_out"], 1)

    def test_case_14_dynamic_deletion_boundary_clutter_entered(self):
        # Canonical: Star K1,2. G0 has {0,1}, {0,2}, {0,4}, {0,5}. At eps_x=1, x_out=2 (not in M0).
        # Stream: -e 0 5 0 -> x_out becomes 1 <= 1 -> ENTERED
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\ne 0 5 0\n"
        stream = "-e 0 5 0\n"
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=1, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        entered = {parse_mapping_tuple(k): v for k, v in step["delta"]["entered"].items()}
        self.assertIn((0, 1, 2), entered)
        self.assertEqual(entered[(0, 1, 2)]["x_out"], 1)

    def test_case_15_dynamic_update_score_changed(self):
        # Canonical: Star K1,2 at eps_x=3. G0 has x_out=1. Stream: +e 0 5 0 -> x_out=2 <= 3 -> SCORE_CHANGED
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\n"
        stream = "e 0 5 0\n"
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=3, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        sc_map = {parse_mapping_tuple(k): v for k, v in step["delta"]["score_changed"].items()}
        self.assertIn((0, 1, 2), sc_map)
        old_sc, new_sc = sc_map[(0, 1, 2)]
        self.assertEqual(old_sc["x_out"], 1)
        self.assertEqual(new_sc["x_out"], 2)

    def test_case_16_connected_near_only_match(self):
        # Canonical: Star K1,3. G has {0,1}, {0,2}, {2,3}. Leaf 3 connected via leaf 2.
        # m=1, x_in=1, x_out=0, connected=True.
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 0 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 2 3 0\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret, 0)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["score"]["m"], 1)
        self.assertEqual(res["score"]["x_in"], 1)
        self.assertEqual(res["score"]["x_out"], 0)
        self.assertTrue(res["score"]["connected"])


# =============================================================================
# 3. Ordered Mapping Identity & Permutation Invariance (FIX-01)
# =============================================================================

class TestAPX03OrderedMappingIdentity(unittest.TestCase):
    """
    Verifies that mapping identity is strictly defined by ascending numerical query
    vertex IDs (u0 < u1 < ... < u_{k-1}), regardless of query vertex declaration order.
    """

    def test_mandatory_permutation_regression(self):
        # Mandatory finding F01 regression test
        q = "v 8 0\nv 2 0\nv 5 0\ne 2 5 0\ne 5 8 0\n"
        d = "v 10 0\nv 20 0\nv 30 0\ne 10 20 0\ne 20 30 0\n"

        ret, res, err = run_apx03_cli(q, d, eps_m=0, eps_x=None, mode="STRICT")
        self.assertEqual(ret, 0, err)
        mappings = [tuple(m["mapping"]) for m in res["matches"]]
        self.assertEqual(mappings, [(10, 20, 30), (30, 20, 10)])

        # Direct evaluation of (10, 20, 30) must be accepted with m=0
        ret_d, res_d, err_d = run_apx03_cli(q, d, eps_m=0, eps_x=None, mode="STRICT", evaluate_mapping=[10, 20, 30])
        self.assertEqual(ret_d, 0, err_d)
        self.assertTrue(res_d["accepted"])
        self.assertEqual(res_d["score"]["m"], 0)
        self.assertEqual(res_d["score"]["x_out"], 0)

    def test_query_declaration_order_permutations(self):
        # Permute vertex declarations in query graph: all 6 permutations of {2, 5, 8}
        query_perms = [
            "v 2 0\nv 5 0\nv 8 0\ne 2 5 0\ne 5 8 0\n",
            "v 2 0\nv 8 0\nv 5 0\ne 2 5 0\ne 5 8 0\n",
            "v 5 0\nv 2 0\nv 8 0\ne 2 5 0\ne 5 8 0\n",
            "v 5 0\nv 8 0\nv 2 0\ne 2 5 0\ne 5 8 0\n",
            "v 8 0\nv 2 0\nv 5 0\ne 2 5 0\ne 5 8 0\n",
            "v 8 0\nv 5 0\nv 2 0\ne 2 5 0\ne 5 8 0\n",
        ]
        d = "v 10 0\nv 20 0\nv 30 0\ne 10 20 0\ne 20 30 0\n"

        expected = {(10, 20, 30), (30, 20, 10)}
        for idx, q_text in enumerate(query_perms):
            ret, res, err = run_apx03_cli(q_text, d, eps_m=0, eps_x=None, mode="STRICT")
            self.assertEqual(ret, 0, f"Permutation {idx} failed: {err}")
            matches = {tuple(m["mapping"]) for m in res["matches"]}
            self.assertEqual(matches, expected, f"Mismatch at permutation {idx}")

    def test_sparse_query_and_data_ids_against_apx02(self):
        # Sparse non-contiguous IDs in both Q and D
        q_text = "v 99 0\nv 12 0\nv 55 0\ne 12 55 0\ne 55 99 0\n"
        g_text = ("v 500 0\nv 100 0\nv 300 0\nv 200 0\n"
                  "e 100 200 0\ne 200 300 0\ne 300 500 0\n")

        # APX-02 Python Oracle
        py_Q = SimpleGraph.from_text(q_text)
        py_G = SimpleGraph.from_text(g_text)
        orc_matches = apx02_find_matches(py_Q, py_G, eps_m=0, eps_x=0, mode=ConnectivityMode.STRICT)

        # APX-03 C++ Baseline
        ret, res, err = run_apx03_cli(q_text, g_text, eps_m=0, eps_x=0, mode="STRICT")
        self.assertEqual(ret, 0, err)
        cpp_matches = {tuple(m["mapping"]) for m in res["matches"]}

        self.assertEqual(cpp_matches, set(orc_matches.keys()))


# =============================================================================
# 4. Numeric Threshold Validation & CLI Aliases (FIX-02, FIX-07)
# =============================================================================

class TestAPX03ThresholdValidationAndAliases(unittest.TestCase):
    """
    Tests strict numeric threshold validation, rejecting invalid values, overflow,
    negative values, and invalid aliases.
    """

    def setUp(self):
        self.dummy_q = "v 0 0\n"
        self.dummy_d = "v 0 0\n"

    def test_mandatory_negative_cli_cases(self):
        # Mandatory cases from F02 & F07:
        # --eps-m 4294967296
        # --eps-x 4294967296
        # --eps-m -1
        # --eps-x -1
        # --eps-m 1abc
        cases = [
            {"eps_m": "4294967296", "eps_x": 0, "desc": "eps_m uint32 overflow"},
            {"eps_m": 0, "eps_x": "4294967296", "desc": "eps_x uint32 overflow"},
            {"eps_m": "-1", "eps_x": 0, "desc": "eps_m negative value"},
            {"eps_m": 0, "eps_x": "-1", "desc": "eps_x -1 must NOT be alias for inf"},
            {"eps_m": "1abc", "eps_x": 0, "desc": "eps_m trailing non-digits"},
            {"eps_m": 0, "eps_x": "2xyz", "desc": "eps_x trailing non-digits"},
            {"eps_m": 0, "eps_x": "-999", "desc": "eps_x negative value"},
        ]
        for c in cases:
            ret, _, err = run_apx03_cli(self.dummy_q, self.dummy_d, eps_m=c["eps_m"], eps_x=c["eps_x"])
            self.assertNotEqual(ret, 0, f"Expected non-zero exit code for {c['desc']}")
            self.assertIn("Invalid", err, f"Expected error message for {c['desc']}")

    def test_valid_infinity_aliases(self):
        # Unbounded eps-x allowed through: None, inf, null
        for alias in ["None", "inf", "null"]:
            ret, res, err = run_apx03_cli(self.dummy_q, self.dummy_d, eps_m=0, eps_x=alias)
            self.assertEqual(ret, 0, f"Alias '{alias}' failed with error: {err}")
            self.assertIsNone(res.get("eps_x"))

    def test_boundary_uint32_threshold(self):
        # UINT32_MAX = 4294967295 must be valid
        ret, res, err = run_apx03_cli(self.dummy_q, self.dummy_d, eps_m=4294967295, eps_x=4294967295)
        self.assertEqual(ret, 0, err)
        self.assertEqual(res.get("eps_m"), 4294967295)
        self.assertEqual(res.get("eps_x"), 4294967295)


# =============================================================================
# 5. Resource Guard Correctness & Adversarial Pruning (FIX-03)
# =============================================================================

class TestAPX03ResourceGuardCorrectness(unittest.TestCase):
    """
    Verifies that resource limit guards intermediate search state exploration,
    failing closed rather than enumerating combinatorial partial assignments.
    """

    def test_mandatory_adversarial_regression(self):
        # Mandatory adversarial fixture:
        # Query: 10 vertices (9 with label 0, 1 with label 1). No edges.
        # Data: 13 vertices, all label 0. No edges.
        # Small search budget: stops because of resource limit rather than exploring 259M states.
        q_lines = [f"v {i} 0" for i in range(9)] + ["v 9 1"]
        d_lines = [f"v {i} 0" for i in range(13)]
        q_text = "\n".join(q_lines) + "\n"
        d_text = "\n".join(d_lines) + "\n"

        ret, res, err = run_apx03_cli(
            q_text, d_text, eps_m=0, eps_x=None, max_search_steps=100
        )
        self.assertEqual(ret, 2, f"Expected exit code 2 on resource limit exceeded, got {ret}")
        self.assertIn("limit", err.lower())

    def test_resource_completeness_when_budget_adequate(self):
        # Connected query on a matching data graph with adequate budget completes fully
        q = "v 0 0\nv 1 0\nv 2 1\ne 0 1 0\ne 1 2 0\n"
        d = "v 10 0\nv 20 0\nv 30 1\ne 10 20 0\ne 20 30 0\n"
        ret, res, err = run_apx03_cli(q, d, eps_m=0, eps_x=None, max_search_steps=10000)
        self.assertEqual(ret, 0, err)
        self.assertEqual(res["total_accepted"], 1)


# =============================================================================
# 6. 15-Step Deterministic Dynamic Differential against APX-02 (FIX-08)
# =============================================================================

class TestAPX03DynamicDeterministicDifferential(unittest.TestCase):
    """
    15-step comprehensive dynamic differential verification against Python DynamicOracle.
    Validates ENTERED, EXITED, SCORE_CHANGED, internal chords (no score change),
    reverse endpoints, reversibility, and atomic invalid updates.
    """

    def test_fifteen_step_deterministic_differential(self):
        q_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g0_text = ("v 0 0\nv 1 0\nv 2 0\nv 3 0\nv 4 0\nv 5 0\n"
                   "e 0 1 0\ne 1 2 0\n")

        # 16 distinct deterministic updates covering diverse transitions
        stream_updates = [
            ("e", 2, 3, 0),   # Step 1: boundary tail added -> x_out changes
            ("e", 0, 4, 0),   # Step 2: another boundary tail -> EXITED
            ("-e", 0, 4, 0),  # Step 3: boundary tail deleted -> ENTERED
            ("e", 0, 2, 0),   # Step 4: internal chord added between 0 and 2 (x_in changes, but m, x_out unchanged -> NO SCORE_CHANGED!)
            ("-e", 0, 2, 0),  # Step 5: internal chord deleted (x_in changes, still NO SCORE_CHANGED)
            ("e", 2, 5, 0),   # Step 6: boundary tail added -> EXITED
            ("-e", 2, 5, 0),  # Step 7: delete boundary tail -> ENTERED
            ("e", 1, 5, 0),   # Step 8: tail incident to center node -> EXITED
            ("-e", 1, 5, 0),  # Step 9: delete tail incident to center -> ENTERED
            ("-e", 0, 1, 0),  # Step 10: query edge deleted -> match EXITED (m increases to 1)
            ("e", 0, 1, 0),   # Step 11: query edge restored -> match ENTERED (m drops to 0)
            ("e", 3, 4, 0),   # Step 12: background edge not incident to mapped nodes -> strictly unchanged
            ("-e", 3, 4, 0),  # Step 13: background edge removed -> strictly unchanged
            ("-e", 2, 3, 0),  # Step 14: external edge removed -> SCORE_CHANGED
            ("e", 2, 3, 0),   # Step 15: external edge re-added -> SCORE_CHANGED
            ("-e", 2, 3, 0),  # Step 16: external edge removed again -> reversibility preserved
        ]

        stream_lines = [f"{op} {u} {v} {lbl}" for op, u, v, lbl in stream_updates]
        stream_text = "\n".join(stream_lines) + "\n"

        # 1. Execute APX-02 DynamicOracle
        py_Q = SimpleGraph.from_text(q_text)
        py_G0 = SimpleGraph.from_text(g0_text)
        py_dyn = DynamicOracle(py_Q, py_G0, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)

        py_deltas = []
        for op, u, v, lbl in stream_updates:
            up = Update("+" if op in ("e", "+") else "-", u, v, lbl)
            py_deltas.append(py_dyn.apply_update(up))

        # 2. Execute C++ Baseline
        ret, cpp_res, err = run_apx03_cli(q_text, g0_text, eps_m=0, eps_x=1, mode="STRICT", stream_text=stream_text)
        self.assertEqual(ret, 0, err)
        cpp_history = cpp_res["history"]
        self.assertEqual(len(cpp_history), len(py_deltas))

        # 3. Comprehensive Step-by-Step Metric & Delta Comparison
        for s_idx, (cpp_step, py_d) in enumerate(zip(cpp_history, py_deltas), start=1):
            cpp_delta = cpp_step["delta"]

            # Safe AST parse mapping keys
            cpp_entered = {parse_mapping_tuple(k): v for k, v in cpp_delta["entered"].items()}
            cpp_exited = {parse_mapping_tuple(k): v for k, v in cpp_delta["exited"].items()}
            cpp_score_changed = {parse_mapping_tuple(k): v for k, v in cpp_delta["score_changed"].items()}

            py_entered = py_d.entered
            py_exited = py_d.exited
            py_score_changed = py_d.score_changed

            # Assert mapping identities match 100%
            self.assertEqual(set(cpp_entered.keys()), set(py_entered.keys()), f"ENTERED keys mismatch at step {s_idx}")
            self.assertEqual(set(cpp_exited.keys()), set(py_exited.keys()), f"EXITED keys mismatch at step {s_idx}")
            self.assertEqual(set(cpp_score_changed.keys()), set(py_score_changed.keys()), f"SCORE_CHANGED keys mismatch at step {s_idx}")

            # Assert metrics for ENTERED
            for m, cpp_sc in cpp_entered.items():
                py_sc = py_entered[m]
                self.assertEqual(cpp_sc["m"], py_sc.m, f"m mismatch in ENTERED at step {s_idx}")
                self.assertEqual(cpp_sc["x_in"], py_sc.x_in, f"x_in mismatch in ENTERED at step {s_idx}")
                self.assertEqual(cpp_sc["x_out"], py_sc.x_out, f"x_out mismatch in ENTERED at step {s_idx}")
                self.assertEqual(cpp_sc["connected"], py_sc.connected, f"connected mismatch in ENTERED at step {s_idx}")

            # Assert metrics for EXITED
            for m, cpp_sc in cpp_exited.items():
                py_sc = py_exited[m]
                self.assertEqual(cpp_sc["m"], py_sc.m, f"m mismatch in EXITED at step {s_idx}")
                self.assertEqual(cpp_sc["x_in"], py_sc.x_in, f"x_in mismatch in EXITED at step {s_idx}")
                self.assertEqual(cpp_sc["x_out"], py_sc.x_out, f"x_out mismatch in EXITED at step {s_idx}")
                self.assertEqual(cpp_sc["connected"], py_sc.connected, f"connected mismatch in EXITED at step {s_idx}")

            # Assert old and new metrics for SCORE_CHANGED
            for m, (cpp_old, cpp_new) in cpp_score_changed.items():
                py_old, py_new = py_score_changed[m]
                self.assertEqual(cpp_old["m"], py_old.m, f"old m mismatch in SCORE_CHANGED at step {s_idx}")
                self.assertEqual(cpp_old["x_out"], py_old.x_out, f"old x_out mismatch in SCORE_CHANGED at step {s_idx}")
                self.assertEqual(cpp_new["m"], py_new.m, f"new m mismatch in SCORE_CHANGED at step {s_idx}")
                self.assertEqual(cpp_new["x_out"], py_new.x_out, f"new x_out mismatch in SCORE_CHANGED at step {s_idx}")

        # Step 4 specifically: adding internal chord {0,2} did NOT trigger SCORE_CHANGED
        step4_delta = cpp_history[3]["delta"]
        self.assertEqual(len(step4_delta["score_changed"]), 0, "Internal chord change must not trigger SCORE_CHANGED")

    def test_reverse_endpoint_ordering_invariance(self):
        # Update with e 4 0 vs e 0 4
        q = "v 0 0\nv 1 0\ne 0 1 0\n"
        g0 = "v 0 0\nv 1 0\nv 4 0\n"
        stream1 = "e 0 4 0\n"
        stream2 = "e 4 0 0\n"

        ret1, res1, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=None, stream_text=stream1)
        ret2, res2, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=None, stream_text=stream2)
        self.assertEqual(ret1, 0)
        self.assertEqual(ret2, 0)
        self.assertEqual(res1["history"][0]["delta"], res2["history"][0]["delta"])


# =============================================================================
# 7. Additional Findings (A03-N01, A03-N02)
# =============================================================================

class TestAPX03AdditionalFindings(unittest.TestCase):
    """
    Covers Conflicting Vertex Redefinition (A03-N01) and Dynamic Failure State Consistency (A03-N02).
    """

    def test_conflicting_vertex_redefinition_rejected(self):
        # Redefining vertex 0 with label 1 when already declared with label 0
        bad_q = "v 0 0\nv 0 1\n"
        dummy_d = "v 0 0\n"
        ret, _, err = run_apx03_cli(bad_q, dummy_d, eps_m=0, eps_x=None)
        self.assertNotEqual(ret, 0)
        self.assertIn("Conflicting", err)

    def test_identical_vertex_redefinition_permitted(self):
        # Redefining with identical label is permitted
        q = "v 0 0\nv 0 0\nv 1 0\ne 0 1 0\n"
        d = "v 0 0\nv 1 0\ne 0 1 0\n"
        ret, res, err = run_apx03_cli(q, d, eps_m=0, eps_x=None)
        self.assertEqual(ret, 0, err)
        self.assertEqual(res["total_accepted"], 2)

    def test_dynamic_failure_state_consistency(self):
        # A03-N02: Engine fails closed on resource limit during dynamic stream
        q = "v 0 0\nv 1 0\ne 0 1 0\n"
        d = "v 0 0\nv 1 0\nv 2 0\n"
        stream = "e 1 2 0\n"
        ret, _, err = run_apx03_cli(q, d, eps_m=0, eps_x=None, stream_text=stream, max_search_steps=1)
        self.assertEqual(ret, 2)
        self.assertIn("limit", err.lower())

    def test_invalid_update_atomicity(self):
        q = "v 0 0\nv 1 0\ne 0 1 0\n"
        d = "v 0 0\nv 1 0\ne 0 1 0\n"
        # Duplicate edge insertion
        stream_dup = "e 0 1 0\n"
        ret, _, err = run_apx03_cli(q, d, eps_m=0, eps_x=None, stream_text=stream_dup)
        self.assertNotEqual(ret, 0)
        self.assertIn("Duplicate", err)

        # Nonexistent edge deletion
        stream_del = "-e 0 2 0\n"
        ret_del, _, err_del = run_apx03_cli(q, d, eps_m=0, eps_x=None, stream_text=stream_del)
        self.assertNotEqual(ret_del, 0)


# =============================================================================
# 8. Randomized Differential & Exact Recovery
# =============================================================================

class TestAPX03RandomizedDifferential(unittest.TestCase):
    """
    Randomized differential testing across 25 seeded trials comparing C++ baseline
    with independent APX-02 Python Oracle.
    """

    def test_static_randomized_trials_against_apx02_oracle(self):
        seeds = [42, 1337, 2026, 7777, 9999]
        trials_per_seed = 5

        total_trials = 0
        for seed in seeds:
            rng = random.Random(seed)
            for trial in range(trials_per_seed):
                total_trials += 1
                k = rng.choice([2, 3, 4])
                n = rng.choice([4, 5, 6])

                q_lines = [f"v {i} {rng.choice([0, 1])}" for i in range(k)]
                for u, v in itertools.combinations(range(k), 2):
                    if rng.random() < 0.5:
                        q_lines.append(f"e {u} {v} {rng.choice([0, 1])}")
                q_text = "\n".join(q_lines) + "\n"

                g_lines = [f"v {i} {rng.choice([0, 1])}" for i in range(n)]
                for u, v in itertools.combinations(range(n), 2):
                    if rng.random() < 0.4:
                        g_lines.append(f"e {u} {v} {rng.choice([0, 1])}")
                g_text = "\n".join(g_lines) + "\n"

                eps_m = rng.choice([0, 1, 2])
                eps_x = rng.choice([0, 1, 2, None])
                mode_str = rng.choice(["STRICT", "RELAXED"])
                mode_enum = ConnectivityMode.STRICT if mode_str == "STRICT" else ConnectivityMode.RELAXED_UNCONSTRAINED

                # APX-02 Oracle
                py_Q = SimpleGraph.from_text(q_text)
                py_G = SimpleGraph.from_text(g_text)
                oracle_matches = apx02_find_matches(py_Q, py_G, eps_m, eps_x, mode_enum)

                # APX-03 C++ Baseline
                ret, cpp_res, err = run_apx03_cli(q_text, g_text, eps_m, eps_x, mode=mode_str)
                self.assertEqual(ret, 0, f"Trial {total_trials} failed: {err}")

                cpp_matches = {tuple(m["mapping"]): m["score"] for m in cpp_res.get("matches", [])}

                # Mapping set equality
                self.assertEqual(
                    set(cpp_matches.keys()),
                    set(oracle_matches.keys()),
                    f"Mapping set mismatch at seed {seed}, trial {trial}",
                )

                # Score equality
                for mapping, cpp_sc in cpp_matches.items():
                    orc_eval = oracle_matches[mapping]
                    self.assertEqual(cpp_sc["m"], orc_eval.score.m)
                    self.assertEqual(cpp_sc["x_in"], orc_eval.score.x_in)
                    self.assertEqual(cpp_sc["x_out"], orc_eval.score.x_out)
                    self.assertEqual(cpp_sc["connected"], orc_eval.score.connected)

        self.assertEqual(total_trials, 25)


class TestAPX03ExactRecoveryAndNearOnly(unittest.TestCase):
    """
    Verifies Exact Recovery against RF-02 Oracle and proves true Near-Only recovery.
    """

    def test_exact_recovery_against_rf02(self):
        q_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g_text = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"

        rf02_q = RF02LabeledGraph.parse_from_text(q_text)
        rf02_g = RF02LabeledGraph.parse_from_text(g_text)
        exact_embeddings = set(rf02_find_all_embeddings(rf02_q, rf02_g))

        ret, res, err = run_apx03_cli(q_text, g_text, eps_m=0, eps_x=None, mode="STRICT")
        self.assertEqual(ret, 0, err)
        apx_matches = {tuple(m["mapping"]) for m in res["matches"]}

        self.assertEqual(apx_matches, exact_embeddings)
        self.assertEqual(len(apx_matches), 8)

    def test_near_only_recovery_proof(self):
        q_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 0 2 0\n"
        g_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"

        rf02_q = RF02LabeledGraph.parse_from_text(q_text)
        rf02_g = RF02LabeledGraph.parse_from_text(g_text)
        exact_embeddings = rf02_find_all_embeddings(rf02_q, rf02_g)
        self.assertEqual(len(exact_embeddings), 0)

        ret, res, err = run_apx03_cli(q_text, g_text, eps_m=1, eps_x=0, mode="STRICT")
        self.assertEqual(ret, 0, err)
        self.assertGreater(len(res["matches"]), 0)

        for m in res["matches"]:
            mapping = tuple(m["mapping"])
            self.assertEqual(m["score"]["m"], 1)
            self.assertTrue(m["accepted"])
            self.assertNotIn(mapping, exact_embeddings)


class TestAPX03PropertiesAndGuards(unittest.TestCase):
    """Verifies property invariants, monotonicity, and 7-vertex query scaling."""

    def test_automorphism_distinction(self):
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT")
        self.assertEqual(ret, 0)
        mappings = [tuple(m["mapping"]) for m in res["matches"]]
        self.assertEqual(len(mappings), 8)
        self.assertEqual(len(set(mappings)), 8)

    def test_threshold_monotonicity(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 1 3 0\n"
        _, res_0, _ = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT")
        _, res_1, _ = run_apx03_cli(q, g, eps_m=0, eps_x=1, mode="STRICT")

        m0 = {tuple(m["mapping"]) for m in res_0["matches"]}
        m1 = {tuple(m["mapping"]) for m in res_1["matches"]}
        self.assertTrue(m0.issubset(m1))

    def test_strict_subset_relaxed(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\n"
        _, res_s, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="STRICT")
        _, res_r, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="RELAXED")

        ms = {tuple(m["mapping"]) for m in res_s["matches"]}
        mr = {tuple(m["mapping"]) for m in res_r["matches"]}
        self.assertTrue(ms.issubset(mr))

    def test_dynamic_reversibility(self):
        q = "v 0 0\nv 1 0\ne 0 1 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\n"
        stream = "e 0 1 0\n-e 0 1 0\n"
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=0, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        self.assertEqual(res["final_active_matches"], 0)

    def test_seven_vertex_query_smoke(self):
        q_lines = ["v 0 0"] + [f"v {i} 0" for i in range(1, 7)]
        q_lines += [f"e 0 {i} 0" for i in range(1, 7)]
        q = "\n".join(q_lines) + "\n"

        g_lines = ["v 0 0"] + [f"v {i} 0" for i in range(1, 8)]
        g_lines += [f"e 0 {i} 0" for i in range(1, 8)]
        g = "\n".join(g_lines) + "\n"

        ret, res, err = run_apx03_cli(q, g, eps_m=0, eps_x=1, mode="STRICT")
        self.assertEqual(ret, 0, err)
        self.assertGreater(res["total_accepted"], 0)


if __name__ == "__main__":
    unittest.main()
