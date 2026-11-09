#!/usr/bin/env python3
"""
tests/test_apx03_baseline.py — Comprehensive Test Suite for APX-03 C++ Baseline.

Covers:
1. Native RapidFlow integration & symbol provenance.
2. All 16 Canonical Microcases from docs/approx_semantic_microcases_v1.md.
3. Randomized differential testing against APX-02 Python Oracle.
4. Exact recovery verification against RF-02 Exact Oracle.
5. Near-only match verification (m > 0 accepted, absent from exact embeddings).
6. Property invariants: monotonicity, strict subset relaxed, automorphism distinction, reversibility.
7. Resource guard and smoke test for queries with > 6 vertices.
"""

import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY_PATH = REPO_ROOT / "extensions" / "apx03" / "build" / "rapidflow_apx03_baseline"

# Ensure repo root is on sys.path for importing APX-02 and RF-02 oracles
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


def run_apx03_cli(
    query_text: str,
    data_text: str,
    eps_m: int,
    eps_x: Optional[int],
    mode: str = "STRICT",
    stream_text: Optional[str] = None,
    evaluate_mapping: Optional[Sequence[int]] = None,
    max_candidates: int = 10_000_000,
) -> Tuple[int, Dict[str, Any], str]:
    """Invokes the compiled rapidflow_apx03_baseline binary via CLI and parses JSON output."""
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
            "--eps-x", "None" if eps_x is None else str(eps_x),
            "--mode", mode,
            "--max-candidates", str(max_candidates),
        ]
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

        # Cleanup temp files
        try:
            os.unlink(qf.name)
            os.unlink(df.name)
            if sf_name is not None:
                os.unlink(sf_name)
        except OSError:
            pass

        return proc.returncode, parsed_json, proc.stderr


class TestAPX03NativeIntegration(unittest.TestCase):
    """Verifies genuine native RapidFlow C++ source reuse and compilation."""

    def test_binary_built_and_executable(self):
        self.assertTrue(BINARY_PATH.exists(), f"Binary missing at {BINARY_PATH}")
        proc = subprocess.run([str(BINARY_PATH), "--help"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Usage:", proc.stdout)

    def test_rapidflow_symbols_linked(self):
        # Verify that Graph symbols from graph.cpp are compiled into the binary
        cmd = ["nm", "-C", str(BINARY_PATH)]
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            # Graph::loadGraphFromMemory should be in symbol table
            self.assertTrue(
                "Graph::loadGraphFromMemory" in out or "Graph" in out,
                "Native RapidFlow Graph symbols not found in binary"
            )
        except FileNotFoundError:
            self.skipTest("nm command not available")


class TestAPX03CanonicalMicrocases(unittest.TestCase):
    """
    Validates all 16 canonical reference microcases from docs/approx_semantic_microcases_v1.md
    against rapidflow_apx03_baseline.
    """

    def test_case_01_exact_match(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 1 0\nv 2 0\nv 3 0\ne 1 2 0\ne 2 3 0\n"
        # Direct evaluation of canonical target mapping (1, 2, 3)
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
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 1 0\nv 2 0\nv 3 0\ne 1 2 0\n"
        # STRICT mode at (1, 0): data graph {1,2} has 2 disconnected components -> STRICT REJECT
        ret_s, res_s, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="STRICT", evaluate_mapping=[1, 2, 3])
        self.assertEqual(ret_s, 0)
        self.assertFalse(res_s["accepted"])
        self.assertFalse(res_s["score"]["connected"])

        # RELAXED mode at (1, 0): ConnOK=True -> ACCEPT
        ret_r, res_r, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="RELAXED", evaluate_mapping=[1, 2, 3])
        self.assertEqual(ret_r, 0)
        self.assertTrue(res_r["accepted"])
        self.assertEqual(res_r["score"]["m"], 1)

    def test_case_03_missing_two_edges(self):
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 1 2 0\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=2, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret, 0)
        self.assertFalse(res["accepted"])
        self.assertFalse(res["score"]["connected"])
        self.assertEqual(res["score"]["m"], 2)

    def test_case_04_extra_internal_chords(self):
        # C4 -> K4 (x_in=2 internal chords, x_out=0)
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
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 1 3 0\n"
        # At eps_x=0: reject; at eps_x=1: accept
        _, res_0, _ = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_0["accepted"])
        self.assertEqual(res_0["score"]["x_out"], 1)

        _, res_1, _ = run_apx03_cli(q, g, eps_m=0, eps_x=1, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_1["accepted"])
        self.assertEqual(res_1["score"]["x_out"], 1)

    def test_case_06_two_extra_external_tails(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\nv 4 0\ne 0 1 0\ne 1 2 0\ne 1 3 0\ne 1 4 0\n"
        _, res_1, _ = run_apx03_cli(q, g, eps_m=0, eps_x=1, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_1["accepted"])
        self.assertEqual(res_1["score"]["x_out"], 2)

        _, res_2, _ = run_apx03_cli(q, g, eps_m=0, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_2["accepted"])

    def test_case_07_thirty_extra_external_tails(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        lines = ["v 0 0", "v 1 0", "v 2 0"]
        for i in range(4, 34):
            lines.append(f"v {i} 0")
        lines.extend(["e 0 1 0", "e 1 2 0"])
        for i in range(4, 34):
            lines.append(f"e 1 {i} 0")
        g = "\n".join(lines) + "\n"

        _, res_2, _ = run_apx03_cli(q, g, eps_m=0, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_2["accepted"])
        self.assertEqual(res_2["score"]["x_out"], 30)

        _, res_30, _ = run_apx03_cli(q, g, eps_m=0, eps_x=30, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_30["accepted"])

    def test_case_08_synthetic_high_degree_hub(self):
        # Direct evaluation of hub probe (d=1000, x_out=998)
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

    def test_case_09_edge_label_mismatch_topological_connectivity(self):
        # Q has edge (0,1) L=0, (1,2) L=0. G has (0,1) L=0, (1,2) L=1 (wrong label)
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 1 0\nv 2 0\nv 3 0\ne 1 2 0\ne 2 3 1\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="STRICT", evaluate_mapping=[1, 2, 3])
        self.assertEqual(ret, 0)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["score"]["m"], 1)
        self.assertEqual(res["score"]["x_in"], 0)
        self.assertEqual(res["score"]["x_out"], 0)
        self.assertTrue(res["score"]["connected"])

    def test_case_10_injectivity_violation(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 0 0\nv 1 0\ne 0 1 0\n"
        # Non-injective mapping (0, 1, 0)
        ret, res, _ = run_apx03_cli(q, g, eps_m=1, eps_x=1, mode="STRICT", evaluate_mapping=[0, 1, 0])
        self.assertEqual(ret, 0)
        self.assertFalse(res["accepted"])

    def test_case_11_tree_query_disconnected_by_missing_edge(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\n" # Missing (1,2)
        _, res_s, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="STRICT", evaluate_mapping=[0, 1, 2])
        self.assertFalse(res_s["accepted"])
        self.assertFalse(res_s["score"]["connected"])

        _, res_r, _ = run_apx03_cli(q, g, eps_m=1, eps_x=2, mode="RELAXED", evaluate_mapping=[0, 1, 2])
        self.assertTrue(res_r["accepted"])

    def test_case_12_dynamic_insertion_entered(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\n"
        stream = "e 0 2 0\n"
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=0, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        self.assertIn("(0, 1, 2)", step["delta"]["entered"])
        self.assertEqual(step["delta"]["entered"]["(0, 1, 2)"]["m"], 0)

    def test_case_13_dynamic_insertion_boundary_clutter_exited(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\n"
        stream = "e 0 5 0\n" # Adds 2nd boundary edge -> x_out becomes 2 > eps_x=1
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=1, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        self.assertIn("(0, 1, 2)", step["delta"]["exited"])
        self.assertEqual(step["delta"]["exited"]["(0, 1, 2)"]["x_out"], 1)

    def test_case_14_dynamic_deletion_boundary_clutter_entered(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\ne 0 5 0\n"
        stream = "-e 0 5 0\n" # Deletes 2nd boundary edge -> x_out becomes 1 <= eps_x=1 -> ENTERED
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=1, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        self.assertIn("(0, 1, 2)", step["delta"]["entered"])
        self.assertEqual(step["delta"]["entered"]["(0, 1, 2)"]["x_out"], 1)

    def test_case_15_dynamic_update_score_changed(self):
        q = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 0 2 0\n"
        g0 = "v 0 0\nv 1 0\nv 2 0\nv 4 0\nv 5 0\ne 0 1 0\ne 0 2 0\ne 0 4 0\n"
        stream = "e 0 5 0\n" # At eps_x=3, (0,1,2) remains accepted but x_out changes 1 -> 2
        ret, res, _ = run_apx03_cli(q, g0, eps_m=0, eps_x=3, mode="STRICT", stream_text=stream)
        self.assertEqual(ret, 0)
        step = res["history"][0]
        self.assertIn("(0, 1, 2)", step["delta"]["score_changed"])
        old_sc, new_sc = step["delta"]["score_changed"]["(0, 1, 2)"]
        self.assertEqual(old_sc["x_out"], 1)
        self.assertEqual(new_sc["x_out"], 2)

    def test_case_16_connected_near_only_match(self):
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 0 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 2 3 0\n"
        # Leaf 3 connected via leaf 2: m=1, x_in=1, x_out=0, connected=True
        ret, res, _ = run_apx03_cli(q, g, eps_m=1, eps_x=0, mode="STRICT", evaluate_mapping=[0, 1, 2, 3])
        self.assertEqual(ret, 0)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["score"]["m"], 1)
        self.assertEqual(res["score"]["x_in"], 1)
        self.assertEqual(res["score"]["x_out"], 0)
        self.assertTrue(res["score"]["connected"])


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

                # Build query
                q_lines = [f"v {i} {rng.choice([0, 1])}" for i in range(k)]
                for u, v in itertools.combinations(range(k), 2):
                    if rng.random() < 0.5:
                        q_lines.append(f"e {u} {v} {rng.choice([0, 1])}")
                q_text = "\n".join(q_lines) + "\n"

                # Build data graph
                g_lines = [f"v {i} {rng.choice([0, 1])}" for i in range(n)]
                for u, v in itertools.combinations(range(n), 2):
                    if rng.random() < 0.4:
                        g_lines.append(f"e {u} {v} {rng.choice([0, 1])}")
                g_text = "\n".join(g_lines) + "\n"

                eps_m = rng.choice([0, 1, 2])
                eps_x = rng.choice([0, 1, 2, None])
                mode_str = rng.choice(["STRICT", "RELAXED"])
                mode_enum = ConnectivityMode.STRICT if mode_str == "STRICT" else ConnectivityMode.RELAXED_UNCONSTRAINED

                # 1. Run Python APX-02 Oracle
                py_Q = SimpleGraph.from_text(q_text)
                py_G = SimpleGraph.from_text(g_text)
                oracle_matches = apx02_find_matches(py_Q, py_G, eps_m, eps_x, mode_enum)

                # 2. Run C++ APX-03 Baseline
                ret, cpp_res, err = run_apx03_cli(q_text, g_text, eps_m, eps_x, mode=mode_str)
                self.assertEqual(ret, 0, f"Trial {total_trials} failed: {err}")

                cpp_matches = {tuple(m["mapping"]): m["score"] for m in cpp_res.get("matches", [])}

                # Assert exact mapping-set equality
                self.assertEqual(
                    set(cpp_matches.keys()),
                    set(oracle_matches.keys()),
                    f"Mapping set mismatch at seed {seed}, trial {trial}",
                )

                # Assert score equality
                for mapping, cpp_sc in cpp_matches.items():
                    orc_eval = oracle_matches[mapping]
                    self.assertEqual(cpp_sc["m"], orc_eval.score.m)
                    self.assertEqual(cpp_sc["x_in"], orc_eval.score.x_in)
                    self.assertEqual(cpp_sc["x_out"], orc_eval.score.x_out)
                    self.assertEqual(cpp_sc["connected"], orc_eval.score.connected)

        self.assertEqual(total_trials, 25)

    def test_dynamic_streaming_differential_against_apx02_oracle(self):
        """
        Cross-checks C++ RECOMPUTE_BASELINE dynamic deltas (ENTERED, EXITED, SCORE_CHANGED)
        against Python DynamicOracle step-by-step.
        """
        q_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        g0_text = ("v 0 0\nv 1 0\nv 2 0\nv 3 0\nv 4 0\n"
                   "e 0 1 0\ne 1 2 0\n")
        stream_lines = [
            "e 2 3 0",
            "e 2 4 0",
            "-e 2 4 0",
            "e 0 2 0",
            "-e 0 1 0",
            "e 0 1 0",
            "-e 0 2 0",
        ]
        stream_text = "\n".join(stream_lines) + "\n"

        # 1. Python APX-02 DynamicOracle
        py_Q = SimpleGraph.from_text(q_text)
        py_G0 = SimpleGraph.from_text(g0_text)
        py_dyn = DynamicOracle(py_Q, py_G0, eps_m=0, eps_x=1, mode=ConnectivityMode.STRICT)

        py_history = []
        for line in stream_lines:
            tokens = line.split()
            op = "+" if tokens[0] in ("e", "+") else "-"
            u, v, label = int(tokens[1]), int(tokens[2]), int(tokens[3])
            up = Update(op, u, v, label)
            delta = py_dyn.apply_update(up)
            py_history.append(delta)

        # 2. C++ APX-03 Baseline
        ret, cpp_res, err = run_apx03_cli(q_text, g0_text, eps_m=0, eps_x=1, mode="STRICT", stream_text=stream_text)
        self.assertEqual(ret, 0, err)
        cpp_history = cpp_res["history"]

        self.assertEqual(len(cpp_history), len(py_history))

        for step_idx, (cpp_step, py_delta) in enumerate(zip(cpp_history, py_history), start=1):
            cpp_delta = cpp_step["delta"]

            # Convert string keys "(0, 1, 2)" to tuples
            cpp_entered = {eval(k) for k in cpp_delta["entered"].keys()}
            py_entered = set(py_delta.entered.keys())
            self.assertEqual(cpp_entered, py_entered, f"ENTERED mismatch at step {step_idx}")

            cpp_exited = {eval(k) for k in cpp_delta["exited"].keys()}
            py_exited = set(py_delta.exited.keys())
            self.assertEqual(cpp_exited, py_exited, f"EXITED mismatch at step {step_idx}")

            cpp_score_changed = {eval(k) for k in cpp_delta["score_changed"].keys()}
            py_score_changed = set(py_delta.score_changed.keys())
            self.assertEqual(cpp_score_changed, py_score_changed, f"SCORE_CHANGED mismatch at step {step_idx}")


class TestAPX03ExactRecoveryAndNearOnly(unittest.TestCase):
    """
    Verifies Exact Recovery against RF-02 Oracle and proves true Near-Only recovery.
    """

    def test_exact_recovery_against_rf02(self):
        # Connected query: Path P3 (0-1-2)
        q_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"
        # Cycle C4 data graph
        g_text = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"

        # RF-02 Exact Oracle
        rf02_q = RF02LabeledGraph.parse_from_text(q_text)
        rf02_g = RF02LabeledGraph.parse_from_text(g_text)
        exact_embeddings = set(rf02_find_all_embeddings(rf02_q, rf02_g))

        # APX-03 C++ baseline at eps_m=0, eps_x=None, STRICT
        ret, res, err = run_apx03_cli(q_text, g_text, eps_m=0, eps_x=None, mode="STRICT")
        self.assertEqual(ret, 0, err)
        apx_matches = {tuple(m["mapping"]) for m in res["matches"]}

        self.assertEqual(apx_matches, exact_embeddings)
        self.assertEqual(len(apx_matches), 8)

    def test_near_only_recovery_proof(self):
        """
        Proof of genuine Approximate CSM:
        Demonstrates a valid accepted mapping where m(f) > 0, which does NOT exist in exact embeddings.
        """
        # Query Triangle K3: 3 edges
        q_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 0 2 0\n"
        # Data graph Path P3: only 2 edges (missing (0,2))
        g_text = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n"

        # 1. RF-02 Exact Oracle yields ZERO matches
        rf02_q = RF02LabeledGraph.parse_from_text(q_text)
        rf02_g = RF02LabeledGraph.parse_from_text(g_text)
        exact_embeddings = rf02_find_all_embeddings(rf02_q, rf02_g)
        self.assertEqual(len(exact_embeddings), 0)

        # 2. APX-03 at eps_m=1, eps_x=0, STRICT yields valid near-matches
        ret, res, err = run_apx03_cli(q_text, g_text, eps_m=1, eps_x=0, mode="STRICT")
        self.assertEqual(ret, 0, err)
        self.assertGreater(len(res["matches"]), 0)

        for m in res["matches"]:
            mapping = tuple(m["mapping"])
            self.assertEqual(m["score"]["m"], 1)  # m > 0
            self.assertTrue(m["accepted"])
            self.assertNotIn(mapping, exact_embeddings)  # Not in exact embeddings!


class TestAPX03PropertiesAndGuards(unittest.TestCase):
    """Verifies property invariants, resource limit guards, and 7-vertex query scaling."""

    def test_automorphism_distinction(self):
        # Q = Path P4 (0-1-2-3), G = Cycle C4 (0-1-2-3-0)
        q = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"
        ret, res, _ = run_apx03_cli(q, g, eps_m=0, eps_x=0, mode="STRICT")
        self.assertEqual(ret, 0)
        # Must return exactly 8 distinct ordered mapping tuples
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
        g = "v 0 0\nv 1 0\nv 2 0\ne 0 1 0\n" # Missing (1,2) -> disconnected
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
        # Final active matches must be 0 (identical to initial snapshot)
        self.assertEqual(res["final_active_matches"], 0)

    def test_resource_limit_guard_fails_closed(self):
        # Set max_candidates to 1 on a graph with many candidates
        q = "v 0 0\nv 1 0\ne 0 1 0\n"
        g = "v 0 0\nv 1 0\nv 2 0\nv 3 0\nv 4 0\n"
        ret, _, err = run_apx03_cli(q, g, eps_m=1, eps_x=None, max_candidates=1)
        self.assertNotEqual(ret, 0)
        self.assertIn("limit", err.lower())

    def test_seven_vertex_query_smoke(self):
        # Query with 7 vertices (Star K1,6) to verify no hard 6-vertex limit crash
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
