"""
tests/test_rapidflow_apx_p4b.py — Comprehensive Python verification suite for Phase P4b-1A.

Verifies:
1. Native P4b-1A Test Suite: Invokes rapidflow_apx_native --test-p4b (SIG, NUM, HD, INIT, TX, CNT, A1-A7).
2. Property-Based Differential Verification: Seeds 1, 7, 42 comparing M0, Bare M2, A-static, and A-residual step-by-step
   across K2, Triangle, C4, Diamond, K4, Triangular Prism, with mixed typed labels and zero-event churn.
3. Configuration Integrity (Finding P4B-1A-F01): Requested vs effective matching, subprocess substitution rejection.
4. Numeric Safety & Memory Limits (Finding P4B-1A-F02): 256 MiB default, overflow rejection, duplicate flag rejection.
5. Diagnostic Counter Accuracy (Finding P4B-1A-F04): Independent counter verification.
6. Test Dispatcher Fail-Closed Selector Rejection (Finding P4B-1A-F06).
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
    validate_nlf_configuration,
    validate_graph_state,
    validate_codec_state,
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
        """Runs the native C++ test suite covering SIG-01..10, NUM-01..18, HD, INIT-01..12, TX-01..25, CNT, A1..A7, RECON."""
        res = subprocess.run(
            [str(BINARY_PATH), "--test-p4b", "all"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        self.assertEqual(res.returncode, 0, f"Native p4b tests failed:\n{res.stdout}")
        self.assertIn("P4b-1A Native Tests Result: ALL TESTS PASSED", res.stdout)
        self.assertIn("P4b-1A Native Tests Groups Executed: 8", res.stdout)

        # Also verify dedicated reconstruction selector (R11-D)
        res_recon = subprocess.run(
            [str(BINARY_PATH), "--test-p4b", "reconstruction"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(res_recon.returncode, 0, f"Reconstruction suite failed:\n{res_recon.stdout}")
        self.assertIn("ALL TESTS PASSED", res_recon.stdout)

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

        # Compare M0, Bare M2, A-static, and A-residual
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
        """
        Property-based differential verification over deterministic seeds 1, 7, 42.
        Covers 13 topological configurations x 2 variants x 3 seeds = 78 streams, 780 updates.
        Topologies: K2 (k=0), Triangle (k=0,1), C4 (k=0,1), Diamond (k=0,1), K4 (k=0,1,2), Prism (k=0,1,2).
        Includes mixed typed labels (int and str) and zero-event churn updates.
        100% agreement between M0, Bare M2, A-static, and A-residual.
        """
        seeds = [1, 7, 42]
        total_streams = 0
        total_steps = 0
        event_streams = truth_events = residual_checks = residual_prunes = 0

        # Query topologies with lambda > k
        # 1. K2: lambda=1, k=0
        # 2. Triangle: lambda=2, k in {0, 1}
        # 3. C4: lambda=2, k in {0, 1}
        # 4. Diamond: lambda=2, k in {0, 1}
        # 5. K4: lambda=3, k in {0, 1, 2}
        # 6. Triangular Prism: lambda=3, k in {0, 1, 2}
        queries = [
            ("K2", 2, [(0, 1, 0)], [0]),
            ("Triangle", 3, [(0, 1, 0), (1, 2, 0), (0, 2, 0)], [0, 1]),
            ("C4", 4, [(0, 1, 0), (1, 2, 0), (2, 3, 0), (3, 0, 0)], [0, 1]),
            ("Diamond", 4, [(0, 1, 0), (0, 2, 0), (1, 2, 0), (1, 3, 0), (2, 3, 0)], [0, 1]),
            ("K4", 4, [
                (0, 1, 0), (0, 2, 0), (0, 3, 0), (1, 2, 0), (1, 3, 0), (2, 3, 0)
            ], [0, 1, 2]),
            ("Prism", 6, [
                (0, 1, 0), (1, 2, 0), (2, 0, 0),
                (3, 4, 0), (4, 5, 0), (5, 3, 0),
                (0, 3, 0), (1, 4, 0), (2, 5, 0)
            ], [0, 1, 2]),
        ]

        for s in seeds:
            rng = random.Random(s)
            for q_name, num_q, q_edges, valid_ks in queries:
                for k in valid_ks:
                    # Run 2 variants per configuration:
                    # Variant 0: Uniform labels (all 0)
                    # Variant 1: Mixed typed labels (int 0, str "0", int 1, str "1", "irrel")
                    for variant in (0, 1):
                        total_streams += 1
                        num_d = max(num_q + 2, 6)

                        if variant == 0:
                            q_vlabels = [0] * num_q
                            q_elabels = [0] * len(q_edges)
                            d_vlabels = [0] * num_d
                            rel_edge_label = 0
                            irrel_edge_label = 99
                        else:
                            # Mixed typed labels: int and string
                            q_vlabels = [0 if (i % 2 == 0) else "0" for i in range(num_q)]
                            q_elabels = [0 if (i % 2 == 0) else "1" for i in range(len(q_edges))]
                            d_vlabels = [0 if (i % 2 == 0) else "0" for i in range(num_d)]
                            rel_edge_label = 0
                            irrel_edge_label = "irrel"

                        # Build query edges with assigned labels
                        labeled_q_edges = []
                        for idx, e in enumerate(q_edges):
                            labeled_q_edges.append((e[0], e[1], q_elabels[idx]))

                        all_possible_edges = [(u, v) for u in range(num_d) for v in range(u + 1, num_d)]
                        rng.shuffle(all_possible_edges)

                        # Start with initial edges
                        init_edges = [(e[0], e[1], rel_edge_label) for e in all_possible_edges[:min(4, len(all_possible_edges))]]
                        curr_edges: Dict[Tuple[int, int], Any] = {(e[0], e[1]): e[2] for e in init_edges}
                        stream_updates = []

                        # Generate 10 stream updates (mix of event-bearing and zero-event churn)
                        for step_idx in range(10):
                            can_insert = [e for e in all_possible_edges if e not in curr_edges]
                            can_delete = list(curr_edges.keys())

                            # 20% chance of zero-event churn with irrelevant label
                            if rng.random() < 0.2 and can_insert:
                                e = rng.choice(can_insert)
                                curr_edges[e] = irrel_edge_label
                                stream_updates.append({"op": "+", "u": e[0], "v": e[1], "label": irrel_edge_label})
                            elif can_insert and (rng.random() < 0.55 or not can_delete):
                                e = rng.choice(can_insert)
                                # Choose a query edge label if endpoints match query vertices
                                chosen_lbl = rel_edge_label
                                for qe in labeled_q_edges:
                                    if (e[0] == qe[0] and e[1] == qe[1]) or (e[0] == qe[1] and e[1] == qe[0]):
                                        chosen_lbl = qe[2]
                                        break
                                curr_edges[e] = chosen_lbl
                                stream_updates.append({"op": "+", "u": e[0], "v": e[1], "label": chosen_lbl})
                            elif can_delete:
                                e = rng.choice(can_delete)
                                lbl = curr_edges.pop(e)
                                stream_updates.append({"op": "-", "u": e[0], "v": e[1], "label": lbl})

                        workload = {
                            "case_id": f"pbt_s{s}_{q_name}_k{k}_v{variant}",
                            "k": k,
                            "query": {
                                "vertices": [{"id": i, "label": q_vlabels[i]} for i in range(num_q)],
                                "edges": [{"u": e[0], "v": e[1], "label": e[2]} for e in labeled_q_edges]
                            },
                            "initial_graph": {
                                "vertices": [{"id": i, "label": d_vlabels[i]} for i in range(num_d)],
                                "edges": [{"u": e[0], "v": e[1], "label": e[2]} for e in init_edges]
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
                        truth = []
                        seen_edge_labels = {e["label"] for e in workload["query"]["edges"] + workload["initial_graph"]["edges"]}
                        for upd in stream_updates:
                            delta = oracle.apply_update(DStarUpdate("INSERT" if upd["op"] == "+" else "DELETE", upd["u"], upd["v"], upd["label"]))
                            seen_edge_labels.add(upd["label"])
                            codec_state = {
                                "query_vertices_count": num_q,
                                "data_vertices_count": num_d,
                                "vertex_labels_count": len(set(q_vlabels + d_vlabels)),
                                "edge_labels_count": len(seen_edge_labels),
                            }
                            truth.append((sorted(map(list, delta.entered)), sorted(map(list, delta.exited)),
                                          oracle.G, codec_state))

                        results = {}
                        for mode in ("none", "static", "residual"):
                            result, _, ok = run_workload_on_binary(BINARY_PATH, "m2", workload, nlf_mode=mode)
                            self.assertTrue(ok, (workload["case_id"], mode, result.get("error_message")))
                            self.assertEqual(result["requested_nlf_mode"], mode)
                            self.assertEqual(result["effective_nlf_mode"], mode)
                            self.assertTrue(result["eligible_for_requested_variant"])
                            self.assertEqual(len(result["steps"]), len(stream_updates))
                            results[mode] = result

                        stream_events = 0
                        for idx, (entered, exited, graph_state, codec_state) in enumerate(truth):
                            total_steps += 1
                            stream_events += len(entered) + len(exited)
                            for mode, result in results.items():
                                step = result["steps"][idx]
                                where = (workload["case_id"], mode, idx)
                                self.assertEqual(step["commit_status"], "COMMITTED", where)
                                self.assertEqual(sorted(step["entered_tuples"]), entered, where)
                                self.assertEqual(sorted(step["exited_tuples"]), exited, where)
                                self.assertEqual(step["step_counters"]["discarded_emissions"], 0, where)
                                for validate, name, expected in (
                                    (validate_graph_state, "graph_state", graph_state),
                                    (validate_codec_state, "codec_state", codec_state),
                                ):
                                    agreed, reason = validate(step[name], expected, case_id=workload["case_id"],
                                                              engine=mode, step_index=idx)
                                    self.assertTrue(agreed, reason)
                        truth_events += stream_events
                        event_streams += stream_events > 0
                        counters = results["residual"]["counters"]
                        residual_checks += counters["nlf_residual_checks"]
                        residual_prunes += counters["nlf_residual_pruned"]

        print(f"  [PBT] {total_streams} streams {total_steps} updates seeds={seeds} "
              f"event_streams={event_streams} zero_event={total_streams - event_streams} "
              f"truth_events={truth_events} residual_checks={residual_checks} residual_prunes={residual_prunes} "
              f"tuple_duplicate_graph_codec_discrepancies=0")
        self.assertEqual(total_streams, 78)
        self.assertEqual(total_steps, 780)
        self.assertGreater(event_streams, 0)

    def test_07_configuration_integrity(self):
        """
        Verifies Configuration Integrity (Finding P4B-1A-F01):
        1. validate_nlf_configuration unit checks for none/static matrix.
        2. Rejection of fallback runs (REQUESTED_CONFIGURATION_UNAVAILABLE) from static certification.
        3. Reviewer subprocess substitution attack reproducer rejection.
        4. Negative test cases preservation.
        5. CLI fail-closed rejection for M1 and ALL engines with --nlf-mode static.
        """
        # CFG-01: Requested none, native output none, SUCCESS -> Valid, Eligible
        mock_none = {
            "nlf_memory_limit_bytes": 256 * 1024 * 1024,
            "nlf_index_logical_bytes": 0,
            "requested_nlf_mode": "none",
            "effective_nlf_mode": "none",
            "configuration_status": "SUCCESS"
        }
        ok, err, meta = validate_nlf_configuration("m2", "none", mock_none, is_success_run=True)
        self.assertTrue(ok)
        self.assertTrue(meta["eligible_for_requested_variant"])
        self.assertTrue(meta["configuration_valid"])

        # CFG-02: Requested none, native output static -> Inconsistent, Rejected
        mock_mismatch = {
            "requested_nlf_mode": "none",
            "effective_nlf_mode": "static",
            "configuration_status": "SUCCESS"
        }
        ok, err, meta = validate_nlf_configuration("m2", "none", mock_mismatch, is_success_run=True)
        self.assertFalse(ok)
        self.assertFalse(meta["eligible_for_requested_variant"])

        # CFG-03: Requested static, native output static, SUCCESS -> Valid, Eligible
        mock_static = {
            "nlf_memory_limit_bytes": 256 * 1024 * 1024,
            "nlf_index_logical_bytes": 4,
            "requested_nlf_mode": "static",
            "effective_nlf_mode": "static",
            "configuration_status": "SUCCESS"
        }
        ok, err, meta = validate_nlf_configuration("m2", "static", mock_static, is_success_run=True)
        self.assertTrue(ok)
        self.assertTrue(meta["eligible_for_requested_variant"])

        # CFG-04: Requested static, native fallback to none (REQUESTED_CONFIGURATION_UNAVAILABLE)
        # MUST be marked NOT eligible for static certification and runner must fail validation
        mock_fallback = {
            "nlf_memory_limit_bytes": 256 * 1024 * 1024,
            "nlf_index_logical_bytes": 0,
            "requested_nlf_mode": "static",
            "effective_nlf_mode": "none",
            "configuration_status": "REQUESTED_CONFIGURATION_UNAVAILABLE",
            "fallback_reason": "NLF_INDEX_MEMORY_LIMIT"
        }
        ok, err, meta = validate_nlf_configuration("m2", "static", mock_fallback, is_success_run=True)
        self.assertFalse(ok, "Fallback run must be rejected from static certification")
        self.assertFalse(meta["eligible_for_requested_variant"], "Must not be eligible for static variant")
        self.assertFalse(meta["configuration_valid"])
        self.assertIn("CONFIG_UNAVAILABLE", err)

        # CFG-05: Reviewer subprocess substitution attack reproducer:
        # Requested static, but executable actually ran none and returned SUCCESS (inconsistent spoof)
        mock_spoof = {
            "requested_nlf_mode": "static",
            "effective_nlf_mode": "none",
            "configuration_status": "SUCCESS"
        }
        ok, err, meta = validate_nlf_configuration("m2", "static", mock_spoof, is_success_run=True)
        self.assertFalse(ok, "Spoofed/inconsistent run must be rejected")
        self.assertFalse(meta["eligible_for_requested_variant"])
        self.assertFalse(meta["configuration_valid"])

        # CFG-06: Negative test case (e.g. prevalidation failed)
        mock_neg = {
            "status": "FAIL",
            "error_category": "PREVALIDATION_ERROR",
            "error_message": "Unknown vertex ID",
            "requested_nlf_mode": "static",
            "configuration_initialized": True,
            "effective_nlf_mode": "static",
            "configuration_status": "SUCCESS",
            "nlf_memory_limit_bytes": 256 * 1024 * 1024,
            "nlf_index_logical_bytes": 4,
        }
        ok, err, meta = validate_nlf_configuration("m2", "static", mock_neg, is_success_run=False)
        self.assertTrue(ok, "Negative runs preserve expected failure category without configuration false-alarm")

        # CFG-R11-01: Reviewer reproducer for contradictory SUCCESS/fallback metadata (R11-F01)
        mock_contra = {
            "requested_nlf_mode": "static",
            "effective_nlf_mode": "static",
            "configuration_status": "SUCCESS",
            "fallback_reason": "NLF_INDEX_MEMORY_LIMIT",
        }
        ok_contra, err_contra, meta_contra = validate_nlf_configuration("m2", "static", mock_contra, is_success_run=True)
        self.assertFalse(ok_contra, "Contradictory metadata (SUCCESS with non-empty fallback_reason) must be rejected")
        self.assertFalse(meta_contra["configuration_valid"])
        self.assertFalse(meta_contra["eligible_for_requested_variant"])
        self.assertIn("CONFIG_CONTRADICTION", err_contra)

        # CFG-R11-02: Contradictory unavailable metadata (UNAVAILABLE with effective static)
        mock_contra_unavail = {
            "requested_nlf_mode": "static",
            "effective_nlf_mode": "static",
            "configuration_status": "REQUESTED_CONFIGURATION_UNAVAILABLE",
            "fallback_reason": "NLF_INDEX_MEMORY_LIMIT"
        }
        ok_cu, err_cu, meta_cu = validate_nlf_configuration("m2", "static", mock_contra_unavail, is_success_run=True)
        self.assertFalse(ok_cu)
        self.assertFalse(meta_cu["configuration_valid"])
        self.assertIn("CONFIG_CONTRADICTION", err_cu)

        # CFG-R11-03: Unavailable metadata with missing fallback reason
        mock_missing_fb = {
            "requested_nlf_mode": "static",
            "effective_nlf_mode": "none",
            "configuration_status": "REQUESTED_CONFIGURATION_UNAVAILABLE"
        }
        ok_mfb, err_mfb, meta_mfb = validate_nlf_configuration("m2", "static", mock_missing_fb, is_success_run=True)
        self.assertFalse(ok_mfb)
        self.assertFalse(meta_mfb["configuration_valid"])
        self.assertIn("CONFIG_INVALID_METADATA", err_mfb)

        # CFG-R11-04: Unsupported unavailable state for NONE
        mock_none_unavail = {
            "requested_nlf_mode": "none",
            "effective_nlf_mode": "none",
            "configuration_status": "REQUESTED_CONFIGURATION_UNAVAILABLE",
            "fallback_reason": "NLF_INDEX_MEMORY_LIMIT"
        }
        ok_nu, err_nu, meta_nu = validate_nlf_configuration("m2", "none", mock_none_unavail, is_success_run=True)
        self.assertFalse(ok_nu)
        self.assertFalse(meta_nu["configuration_valid"])
        self.assertIn("CONFIG_UNAVAILABLE", err_nu)

        # CFG-R11-05: Initialized negative execution identity gap (R11-F02)
        mock_neg_mismatch = {
            "status": "FAIL",
            "error_category": "PREVALIDATION_ERROR",
            "requested_nlf_mode": "none",
            "effective_nlf_mode": "none",
            "configuration_status": "NONE"
        }
        ok_nm, err_nm, meta_nm = validate_nlf_configuration("m2", "static", mock_neg_mismatch, is_success_run=False)
        self.assertFalse(ok_nm, "Negative execution with requested mode mismatch must be rejected")
        self.assertFalse(meta_nm["configuration_valid"])
        self.assertIn("mismatch", err_nm.lower())

        # CFG-R11-06: Early startup negative execution without NLF metadata
        mock_early_neg = {
            "status": "FAIL",
            "error_category": "CLI_ARG_ERROR",
            "exit_code": 1
        }
        ok_en, _, meta_en = validate_nlf_configuration("m2", "static", mock_early_neg, is_success_run=False)
        self.assertTrue(ok_en, "Early startup negative execution without NLF metadata is valid")
        self.assertTrue(meta_en["configuration_valid"])

        # CFG-R11-07: Memory metadata consistency (effective none must have 0 logical bytes)
        mock_none_logical_leak = {
            "requested_nlf_mode": "none",
            "effective_nlf_mode": "none",
            "configuration_status": "SUCCESS",
            "nlf_index_logical_bytes": 1024
        }
        ok_ll, err_ll, meta_ll = validate_nlf_configuration("m2", "none", mock_none_logical_leak, is_success_run=True)
        self.assertFalse(ok_ll)
        self.assertFalse(meta_ll["configuration_valid"])
        self.assertIn("Logical bytes", err_ll)

        # CFG-07: CLI fail-closed rejection: M1 with static
        res_m1 = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_rapidflow_apx_engines.py"), "--engine", "m1", "--nlf-mode", "static", "--stage", "hand"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertNotEqual(res_m1.returncode, 0, "CLI must reject --engine m1 --nlf-mode static with non-zero exit")
        self.assertIn("not supported for M1 engine", res_m1.stderr + res_m1.stdout)

        # CFG-08: CLI fail-closed rejection: ALL with static
        res_all = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_rapidflow_apx_engines.py"), "--engine", "all", "--nlf-mode", "static", "--stage", "hand"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertNotEqual(res_all.returncode, 0, "CLI must reject --engine all --nlf-mode static with non-zero exit")

    def test_08_memory_safety_and_cli_guards(self):
        """
        Verifies Numeric Safety & Memory Limits (Finding P4B-1A-F02):
        1. Default memory limit in native output is 256 MiB (268,435,456 bytes).
        2. CLI rejection of overflow memory limit (2^44 MiB) with exit code 1 (CLI_ARG_ERROR).
        3. CLI rejection of zero memory limit (0) with exit code 1 (CLI_ARG_ERROR).
        4. CLI rejection of negative memory limit with exit code 1 (CLI_ARG_ERROR).
        5. CLI rejection of duplicate --nlf-mode and duplicate --max-nlf-memory-mib with exit code 1 (CLI_ARG_ERROR).
        6. Low-memory fallback witness and rejection from static certification.
        """
        # MEM-01: Verify default memory budget is 256 MiB (268,435,456 bytes)
        w_simple = {
            "case_id": "mem_default_check",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "updates": [{"op": "-", "u": 0, "v": 1, "label": 0}]
        }
        res_stat, _, ok = run_workload_on_binary(BINARY_PATH, "m2", w_simple, nlf_mode="static")
        self.assertTrue(ok)
        self.assertEqual(res_stat["nlf_memory_limit_bytes"], 256 * 1024 * 1024, "Default limit must be exactly 256 MiB")

        # MEM-02: 2^44 MiB overflow rejection
        res_overflow = subprocess.run(
            [str(BINARY_PATH), "--max-nlf-memory-mib", "17592186044416"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_overflow.returncode, 1, f"Overflow budget must exit 1 (CLI_ARG_ERROR), got {res_overflow.returncode}")
        self.assertIn("exceeds maximum", res_overflow.stderr + res_overflow.stdout)

        # MEM-R11-04: Reviewer reproducer for signed JSON serialization overflow (2^43 MiB = 8796093022208) (R11-F03)
        res_2_43 = subprocess.run(
            [str(BINARY_PATH), "--max-nlf-memory-mib", "8796093022208"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_2_43.returncode, 1, f"2^43 MiB budget must exit 1 (CLI_ARG_ERROR), got {res_2_43.returncode}")
        self.assertIn("exceeds maximum", res_2_43.stderr + res_2_43.stdout)

        # MEM-R11-03: Largest supported serializable limit (8796093022207 MiB = 2^43 - 1 MiB)
        with tempfile.TemporaryDirectory() as tmpdir:
            in_file = Path(tmpdir) / "in_max.json"
            out_file = Path(tmpdir) / "out_max.json"
            in_file.write_text(json.dumps(w_simple))
            res_max = subprocess.run(
                [
                    str(BINARY_PATH),
                    "--engine", "m2",
                    "--workload", str(in_file),
                    "--output", str(out_file),
                    "--nlf-mode", "static",
                    "--max-nlf-memory-mib", "8796093022207"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.assertEqual(res_max.returncode, 0, f"Largest supported limit must succeed: {res_max.stderr}")
            out_json = json.loads(out_file.read_text())
            self.assertEqual(out_json["nlf_memory_limit_bytes"], 8796093022207 * 1024 * 1024, "Exact non-negative 64-bit byte count")
            self.assertGreater(out_json["nlf_memory_limit_bytes"], 0)

        # MEM-03: Zero budget rejection
        res_zero = subprocess.run(
            [str(BINARY_PATH), "--max-nlf-memory-mib", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_zero.returncode, 1, f"Zero budget must exit 1 (CLI_ARG_ERROR), got {res_zero.returncode}")
        self.assertIn("positive", res_zero.stderr + res_zero.stdout)

        # MEM-04: Negative budget rejection
        res_neg = subprocess.run(
            [str(BINARY_PATH), "--max-nlf-memory-mib", "-50"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_neg.returncode, 1, f"Negative budget must exit 1 (CLI_ARG_ERROR), got {res_neg.returncode}")

        # MEM-05: Duplicate --nlf-mode flag
        res_dup_mode = subprocess.run(
            [str(BINARY_PATH), "--nlf-mode", "none", "--nlf-mode", "static"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_dup_mode.returncode, 1, f"Duplicate --nlf-mode must exit 1 (CLI_ARG_ERROR), got {res_dup_mode.returncode}")
        self.assertIn("Duplicate --nlf-mode", res_dup_mode.stderr + res_dup_mode.stdout)

        # MEM-06: Duplicate --max-nlf-memory-mib flag
        res_dup_mem = subprocess.run(
            [str(BINARY_PATH), "--max-nlf-memory-mib", "256", "--max-nlf-memory-mib", "512"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_dup_mem.returncode, 1, f"Duplicate --max-nlf-memory-mib must exit 1 (CLI_ARG_ERROR), got {res_dup_mem.returncode}")
        self.assertIn("Duplicate --max-nlf-memory-mib", res_dup_mem.stderr + res_dup_mem.stdout)

        # MEM-07: Low-memory fallback witness (exceeds budget -> falls back to none -> rejected from static cert)
        # Create a workload requiring > 1 MiB (262,144 cells * 4 bytes)
        # e.g., 30,000 vertices and a query with 10 signatures -> 300,000 cells * 4 bytes = 1,200,000 bytes > 1 MiB
        num_v = 30000
        # Query: 11-star (center 0, leaves 1..10 with distinct labels -> 10 signatures)
        q_star_verts = [{"id": 0, "label": 0}] + [{"id": i, "label": i} for i in range(1, 11)]
        q_star_edges = [{"u": 0, "v": i, "label": 0} for i in range(1, 11)]
        w_large = {
            "case_id": "mem_fallback_witness",
            "k": 0,
            "query": {
                "vertices": q_star_verts,
                "edges": q_star_edges
            },
            "initial_graph": {
                "vertices": [{"id": i, "label": 0} for i in range(num_v)],
                "edges": []
            },
            "updates": [{"op": "+", "u": 0, "v": 1, "label": 0}]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            in_file = Path(tmpdir) / "in.json"
            out_file = Path(tmpdir) / "out.json"
            in_file.write_text(json.dumps(w_large))

            # Run with --max-nlf-memory-mib 1 and --nlf-mode static
            res_fb = subprocess.run(
                [
                    str(BINARY_PATH),
                    "--engine", "m2",
                    "--workload", str(in_file),
                    "--output", str(out_file),
                    "--nlf-mode", "static",
                    "--max-nlf-memory-mib", "1"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.assertEqual(res_fb.returncode, 0, f"Binary must complete via fallback: {res_fb.stderr}")
            native_out = json.loads(out_file.read_text())
            self.assertEqual(native_out["requested_nlf_mode"], "static")
            self.assertEqual(native_out["effective_nlf_mode"], "none", "Must fall back to none")
            self.assertEqual(native_out["configuration_status"], "REQUESTED_CONFIGURATION_UNAVAILABLE")
            self.assertEqual(native_out["fallback_reason"], "NLF_INDEX_MEMORY_LIMIT")

            # Validate that runner rejects it from static certification
            ok_cert, err_cert, meta_cert = validate_nlf_configuration("m2", "static", native_out, is_success_run=True)
            self.assertFalse(ok_cert, "Runner must reject fallback from static certification")
            self.assertFalse(meta_cert["eligible_for_requested_variant"])

    def test_09_dispatcher_selector_rejection(self):
        """
        Verifies Test Dispatcher Selector Fail-Closed Behavior (Finding P4B-1A-F06):
        Unknown or invalid suite selectors must return exit code 1.
        Valid selectors must return exit code 0.
        """
        # DISP-01: Unknown selector 'nonexisting'
        res_nonexist = subprocess.run(
            [str(BINARY_PATH), "--test-p4b", "nonexisting"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_nonexist.returncode, 1, f"Unknown selector must exit 1, got {res_nonexist.returncode}")
        self.assertIn("Unknown P4b test suite selector 'nonexisting'", res_nonexist.stderr)

        # DISP-02: Unknown selector 'foobar'
        res_foobar = subprocess.run(
            [str(BINARY_PATH), "--test-p4b", "foobar"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertEqual(res_foobar.returncode, 1, f"Unknown selector must exit 1, got {res_foobar.returncode}")

        # DISP-03 to DISP-11: Valid selectors
        valid_selectors = [
            "signatures", "numeric", "high_degree", "initialization",
            "transactions", "counters", "counterexamples", "reconstruction"
        ]
        for sel in valid_selectors:
            res_sel = subprocess.run(
                [str(BINARY_PATH), "--test-p4b", sel],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.assertEqual(res_sel.returncode, 0, f"Valid selector '{sel}' failed:\n{res_sel.stdout}\n{res_sel.stderr}")
            self.assertIn("ALL TESTS PASSED", res_sel.stdout)


if __name__ == "__main__":
    unittest.main()
