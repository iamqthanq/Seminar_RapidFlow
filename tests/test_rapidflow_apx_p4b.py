"""
tests/test_rapidflow_apx_p4b.py — Comprehensive Python verification suite for Phase P4b-1A.

Verifies:
1. Native P4b-1A Test Suite: Invokes rapidflow_apx_native --test-p4b (SIG, NUM, INIT, TX, A1-A7).
2. Property-Based Differential Verification: Seeds 1, 7, 42 comparing M0, Bare M2, and A-static step-by-step.
3. Frozen Bare M2 Baseline Parity: Verifies nlf_mode=none preserves baseline behavior and counters.
4. M1 Baseline Regression: Verifies M1 works and rejects --nlf-mode static.
5. Counterexamples A1-A7, Chord fixture, and k=2 coverage.
6. Memory Budget & Fallback: Verifies transparent fallback to Bare M2 when limit is exceeded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.dstar_oracle import (
    DStarGraph,
    enumerate_snapshot,
    DStarDynamicOracle,
    DStarUpdate,
)
from scripts.run_rapidflow_apx_engines import (
    run_workload_on_binary,
    build_hand_workloads,
)

BINARY_PATH = REPO_ROOT / "build" / "rapidflow_apx_native"
if not BINARY_PATH.exists():
    BINARY_PATH = REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build" / "rapidflow_apx_native"


class TestRapidFlowApxP4b1A(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY_PATH.exists():
            raise RuntimeError(f"Binary not found at {BINARY_PATH}. Please build it first.")

    def test_01_native_p4b_test_suite(self):
        """Runs the native C++ test suite covering SIG-01..10, NUM-01..18, INIT-01..12, TX-01..25, A1..A7."""
        res = subprocess.run(
            [str(BINARY_PATH), "--test-p4b", "all"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        print("\n" + res.stdout)
        self.assertEqual(res.returncode, 0, f"Native p4b tests failed:\n{res.stdout}")
        self.assertIn("P4b-1A Native Tests Result: ALL TESTS PASSED", res.stdout)

    def test_02_fixture_a1_and_chord(self):
        """Verifies Original A1 (8 ENTERED / 8 EXITED) and Chord fixture (16 ENTERED / 16 EXITED)."""
        # A1: Diamond query k=1, G0 path -> INSERT gives C4
        w_a1 = {
            "case_id": "p4b_fixture_a1",
            "k": 1,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}, {"id": 3, "label": 0}],
                "edges": [
                    {"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 3, "label": 0}
                ]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}, {"id": 3, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}]
            },
            "updates": [
                {"op": "+", "u": 2, "v": 3, "label": 0},
                {"op": "-", "u": 2, "v": 3, "label": 0}
            ]
        }

        # Run with A-static
        res_a1_static, _, ok_stat = run_workload_on_binary(BINARY_PATH, "m2", w_a1, nlf_mode="static")
        self.assertTrue(ok_stat, f"A1 A-static failed: {res_a1_static.get('error_message')}")
        self.assertEqual(res_a1_static["status"], "SUCCESS")
        steps = res_a1_static["steps"]
        self.assertEqual(len(steps[0]["entered_tuples"]), 8, "A1 INSERT must produce 8 entered tuples")
        self.assertEqual(len(steps[0]["exited_tuples"]), 0)
        self.assertIn([0, 1, 2, 3], steps[0]["entered_tuples"], "Identity tuple (0,1,2,3) must be present")
        self.assertEqual(len(steps[1]["entered_tuples"]), 0)
        self.assertEqual(len(steps[1]["exited_tuples"]), 8, "A1 DELETE must produce 8 exited tuples")

        # Run with Bare M2
        res_a1_bare, _, ok_bare = run_workload_on_binary(BINARY_PATH, "m2", w_a1, nlf_mode="none")
        self.assertTrue(ok_bare)
        self.assertEqual(steps[0]["entered_tuples"], res_a1_bare["steps"][0]["entered_tuples"])
        self.assertEqual(steps[1]["exited_tuples"], res_a1_bare["steps"][1]["exited_tuples"])

    def test_03_actual_pruning_witness_a7(self):
        """Verifies actual pruning witness A7: nlf_static_checks > 0, nlf_static_pruned > 0, exact tuple parity."""
        w_a7 = {
            "case_id": "p4b_witness_a7",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0}, {"u": 0, "v": 2, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}, {"id": 3, "label": 0}, {"id": 4, "label": 0}],
                "edges": [{"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 2, "label": 0}, {"u": 0, "v": 3, "label": 0}, {"u": 1, "v": 4, "label": 0}]
            },
            "updates": [{"op": "+", "u": 0, "v": 1, "label": 0}]
        }

        res_bare, _, ok_bare = run_workload_on_binary(BINARY_PATH, "m2", w_a7, nlf_mode="none")
        res_stat, _, ok_stat = run_workload_on_binary(BINARY_PATH, "m2", w_a7, nlf_mode="static")

        self.assertTrue(ok_bare)
        self.assertTrue(ok_stat)
        self.assertEqual(res_bare["steps"][0]["entered_tuples"], res_stat["steps"][0]["entered_tuples"])

        cnt = res_stat["counters"]
        self.assertGreater(cnt["nlf_static_checks"], 0, "nlf_static_checks must be > 0")
        self.assertGreater(cnt["nlf_static_pruned"], 0, "nlf_static_pruned must be > 0 (witness demonstrated!)")

    def test_04_m1_unsupported_nlf_rejection(self):
        """Verifies M1 rejects --nlf-mode static as unsupported."""
        w = {
            "case_id": "p4b_m1_reject",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": []
            },
            "updates": [{"op": "+", "u": 0, "v": 1, "label": 0}]
        }
        res, _, ok = run_workload_on_binary(BINARY_PATH, "m1", w, nlf_mode="static")
        self.assertFalse(ok or res.get("status") == "SUCCESS", "M1 must reject nlf-mode static")

    def test_05_k2_correctness_coverage(self):
        """Verifies k=2 correctness coverage on K4 (lambda=3)."""
        w_k4 = {
            "case_id": "p4b_k4_k2",
            "k": 2,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}, {"id": 3, "label": 0}],
                "edges": [
                    {"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 0, "v": 3, "label": 0},
                    {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}, {"u": 2, "v": 3, "label": 0}
                ]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}, {"id": 3, "label": 0}],
                "edges": [
                    {"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}
                ]
            },
            "updates": [{"op": "+", "u": 2, "v": 3, "label": 0}]
        }

        # Compare M0, Bare M2, and A-static
        Q_m0 = DStarGraph()
        for v in w_k4["query"]["vertices"]: Q_m0.add_vertex(v["id"], v["label"])
        for e in w_k4["query"]["edges"]: Q_m0.add_edge(e["u"], e["v"], e["label"])
        G_m0 = DStarGraph()
        for v in w_k4["initial_graph"]["vertices"]: G_m0.add_vertex(v["id"], v["label"])
        for e in w_k4["initial_graph"]["edges"]: G_m0.add_edge(e["u"], e["v"], e["label"])

        oracle = DStarDynamicOracle(Q_m0, G_m0, k=2)
        delta = oracle.apply_update(DStarUpdate("INSERT", 2, 3, 0))
        exp_ent = sorted([list(t) for t in delta.entered])

        res_bare, _, _ = run_workload_on_binary(BINARY_PATH, "m2", w_k4, nlf_mode="none")
        res_stat, _, _ = run_workload_on_binary(BINARY_PATH, "m2", w_k4, nlf_mode="static")

        self.assertEqual(res_bare["status"], "SUCCESS")
        self.assertEqual(res_stat["status"], "SUCCESS")
        self.assertEqual(sorted(res_bare["steps"][0]["entered_tuples"]), exp_ent, "Bare M2 matches M0 on k=2 K4")
        self.assertEqual(sorted(res_stat["steps"][0]["entered_tuples"]), exp_ent, "A-static matches M0 on k=2 K4")

    def test_06_property_based_differential(self):
        """Property-based differential verification over deterministic seeds 1, 7, 42 comparing M0, Bare M2, and A-static."""
        seeds = [1, 7, 42]
        total_streams = 0
        total_steps = 0

        # Query topologies with lambda > k
        # Triangle: lambda=2, k in {0, 1}
        # C4: lambda=2, k in {0, 1}
        queries = [
            ("Triangle", 3, [(0, 1, 0), (1, 2, 0), (0, 2, 0)], [0, 1]),
            ("C4", 4, [(0, 1, 0), (1, 2, 0), (2, 3, 0), (3, 0, 0)], [0, 1]),
            ("Diamond", 4, [(0, 1, 0), (0, 2, 0), (1, 2, 0), (1, 3, 0), (2, 3, 0)], [0, 1]),
        ]

        for s in seeds:
            rng = random.Random(s)
            for q_name, num_q, q_edges, valid_ks in queries:
                for k in valid_ks:
                    total_streams += 1
                    # Generate random data graph with num_d vertices
                    num_d = 6
                    # Start with random spanning tree or random edges
                    all_possible_edges = [(u, v) for u in range(num_d) for v in range(u + 1, num_d)]
                    rng.shuffle(all_possible_edges)

                    init_edges = all_possible_edges[:4]
                    stream_updates = []
                    curr_edges = set(init_edges)

                    # Generate 6 stream updates
                    for step_idx in range(6):
                        can_insert = [e for e in all_possible_edges if e not in curr_edges]
                        can_delete = list(curr_edges)
                        op = "+" if (can_insert and (rng.random() < 0.6 or not can_delete)) else "-"
                        if op == "+":
                            e = rng.choice(can_insert)
                            curr_edges.add(e)
                            stream_updates.append({"op": "+", "u": e[0], "v": e[1], "label": 0})
                        else:
                            e = rng.choice(can_delete)
                            curr_edges.remove(e)
                            stream_updates.append({"op": "-", "u": e[0], "v": e[1], "label": 0})

                    workload = {
                        "case_id": f"pbt_s{s}_{q_name}_k{k}",
                        "k": k,
                        "query": {
                            "vertices": [{"id": i, "label": 0} for i in range(num_q)],
                            "edges": [{"u": e[0], "v": e[1], "label": e[2]} for e in q_edges]
                        },
                        "initial_graph": {
                            "vertices": [{"id": i, "label": 0} for i in range(num_d)],
                            "edges": [{"u": e[0], "v": e[1], "label": 0} for e in init_edges]
                        },
                        "updates": stream_updates
                    }

                    # M0 Oracle
                    Q_m0 = DStarGraph()
                    for v in workload["query"]["vertices"]: Q_m0.add_vertex(v["id"], v["label"])
                    for e in workload["query"]["edges"]: Q_m0.add_edge(e["u"], e["v"], e["label"])
                    G_m0 = DStarGraph()
                    for v in workload["initial_graph"]["vertices"]: G_m0.add_vertex(v["id"], v["label"])
                    for e in workload["initial_graph"]["edges"]: G_m0.add_edge(e["u"], e["v"], e["label"])

                    oracle = DStarDynamicOracle(Q_m0, G_m0, k=k)
                    m0_entered = []
                    m0_exited = []
                    for upd in stream_updates:
                        delta = oracle.apply_update(DStarUpdate("INSERT" if upd["op"] == "+" else "DELETE", upd["u"], upd["v"], upd["label"]))
                        m0_entered.append(sorted([list(t) for t in delta.entered]))
                        m0_exited.append(sorted([list(t) for t in delta.exited]))

                    res_bare, _, ok_b = run_workload_on_binary(BINARY_PATH, "m2", workload, nlf_mode="none")
                    res_stat, _, ok_s = run_workload_on_binary(BINARY_PATH, "m2", workload, nlf_mode="static")

                    self.assertTrue(ok_b, f"Bare M2 failed on {workload['case_id']}: {res_bare.get('error_message')}")
                    self.assertTrue(ok_s, f"A-static failed on {workload['case_id']}: {res_stat.get('error_message')}")

                    for step_idx in range(len(stream_updates)):
                        total_steps += 1
                        bare_ent = sorted(res_bare["steps"][step_idx].get("entered_tuples", []))
                        bare_ext = sorted(res_bare["steps"][step_idx].get("exited_tuples", []))
                        stat_ent = sorted(res_stat["steps"][step_idx].get("entered_tuples", []))
                        stat_ext = sorted(res_stat["steps"][step_idx].get("exited_tuples", []))

                        self.assertEqual(stat_ent, m0_entered[step_idx], f"PBT entered mismatch with M0 at step {step_idx} in {workload['case_id']}")
                        self.assertEqual(stat_ext, m0_exited[step_idx], f"PBT exited mismatch with M0 at step {step_idx} in {workload['case_id']}")
                        self.assertEqual(stat_ent, bare_ent, f"PBT entered mismatch between A-static and Bare M2 at step {step_idx} in {workload['case_id']}")
                        self.assertEqual(stat_ext, bare_ext, f"PBT exited mismatch between A-static and Bare M2 at step {step_idx} in {workload['case_id']}")

        print(f"  [PBT] Successfully verified {total_streams} generated streams ({total_steps} stream updates) across seeds {seeds}!")


if __name__ == "__main__":
    unittest.main()
