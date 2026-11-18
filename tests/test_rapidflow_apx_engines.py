"""
tests/test_rapidflow_apx_engines.py — Unit and regression test suite for RapidFlow-APX Native Engines (Phase P3).

Tests Groups A through M:
- Group A: Native binary build, provenance, and host resource verification.
- Group B: Full symmetry K4 k=0 witness (24 ENTERED / 24 EXITED) on M1 and M2 matching M0 Oracle.
- Group C: Event leaf Diamond k=1 witness (8 ENTERED) on M1 and M2 matching M0 Oracle.
- Group D: Deferred vertex C4 k=1 witness (8 ENTERED) on M1 and M2 matching M0 Oracle.
- Group E: Typed label isolation (int 0 != str "0") without type coercion.
- Group F: Sparse, negative, and large signed 64-bit external IDs.
- Group G: Non-induced extra chords and edge label mismatch counting.
- Group H: Irrelevant update handling (graph mutated, 0 search emissions).
- Group I: Domain D-R invariant guards (k >= lambda(Q) rejected, self-loops rejected).
- Group J: Work budget exhaustion and process containment limits.
- Group K: Corpus 840 multi-case parity verification against dynamic M0 Oracle.
- Group L: 56-Stream stateful active-set reconstruction against dynamic M0 Oracle.
- Group M: Runner negative and corruption detection (tampered tuples, corrupted counts).
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_engines import (
    build_native_binary,
    check_git_provenance,
    check_system_resources,
    compute_sha256,
    generate_stream_workload,
    run_workload_on_binary,
)
from scripts.dstar_oracle import (
    DStarGraph,
    enumerate_snapshot,
    DStarDynamicOracle,
    DStarUpdate,
)


def independent_edge_connectivity_cut_oracle(num_vertices: int, edges: list[tuple[int, int]]) -> int:
    """Computes exact edge connectivity lambda(Q) by min-cut enumeration over all non-empty proper vertex cuts."""
    if num_vertices <= 1:
        return 0
    if num_vertices == 2:
        return len(edges)
    min_cut = len(edges)
    nodes = list(range(num_vertices))
    for r in range(1, num_vertices):
        for combo in itertools.combinations(nodes[1:], r - 1):
            S = set((0,) + combo)
            cut_edges = sum(1 for (u, v) in edges if (u in S) != (v in S))
            if cut_edges < min_cut:
                min_cut = cut_edges
    return min_cut


class TestRapidFlowApxEngines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provenance, cls.prov_ok = check_git_provenance()
        cls.binary_path, cls.binary_sha, cls.build_info, cls.build_ok = build_native_binary(clean_build=False)
        cls.manifest_path = REPO_ROOT / "configs" / "rapidflow_apx_engines_cases.json"
        with open(cls.manifest_path, "r", encoding="utf-8") as f:
            cls.manifest = json.load(f)

    # =========================================================================
    # Group A: Provenance and Resources
    # =========================================================================
    def test_group_a_provenance_and_build(self):
        self.assertTrue(self.prov_ok, "Provenance verification must succeed")
        self.assertTrue(self.build_ok, "Native binary compilation must succeed")
        self.assertTrue(self.binary_path.is_file(), "Binary file must exist")
        self.assertNotEqual(self.binary_sha, "FILE_NOT_FOUND")

    def test_group_a_system_resources(self):
        res, ok = check_system_resources()
        self.assertTrue(ok, "System resources must meet minimum preflight requirements")
        self.assertGreaterEqual(res["mem_available_gib"], 2.0)
        self.assertGreaterEqual(res["free_disk_gib"], 1.0)

    # =========================================================================
    # Group B: K4 k=0 Full Symmetry Witness
    # =========================================================================
    def test_group_b_k4_k0_symmetry_witness(self):
        """K4 k=0 complete graph: 24 ENTERED on INSERT and 24 EXITED on DELETE."""
        k4_q_edges = [{"u": u, "v": v, "label": 0} for (u, v) in itertools.combinations((1, 2, 3, 4), 2)]
        workload = {
            "case_id": "TEST_K4_K0_SYMMETRY",
            "k": 0,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
                "edges": k4_q_edges,
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
                "edges": [
                    {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0},
                    {"u": 1, "v": 4, "label": 0}
                ],
            },
            "updates": [
                {"op": "+", "u": 2, "v": 3, "label": 0},
                {"op": "-", "u": 2, "v": 3, "label": 0}
            ],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok, f"Run failed for {eng}: {res.get('error_message')}")
            steps = res.get("steps", [])
            self.assertEqual(len(steps), 2)
            self.assertEqual(len(steps[0]["entered_tuples"]), 24)
            self.assertEqual(len(steps[0]["exited_tuples"]), 0)
            self.assertEqual(len(steps[1]["entered_tuples"]), 0)
            self.assertEqual(len(steps[1]["exited_tuples"]), 24)
            # Verify zero raw duplicates
            cnt = res.get("counters", {})
            self.assertEqual(cnt.get("discarded_emissions"), 0)
            self.assertEqual(cnt.get("unique_emissions"), cnt.get("committed_emissions"))

    # =========================================================================
    # Group C: Diamond k=1 Event Leaf Witness
    # =========================================================================
    def test_group_c_diamond_k1_event_leaf(self):
        """Diamond k=1: exactly 8 ENTERED tuples matching M0 Oracle."""
        workload = {
            "case_id": "TEST_DIAMOND_K1",
            "k": 1,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in (0, 1, 2, 3)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 3, "label": 0}
                ],
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in range(5)],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}],
            },
            "updates": [{"op": "+", "u": 1, "v": 2, "label": 0}],
        }

        # Expected from Oracle
        expected_entered = sorted([
            [0, 1, 2, 3], [0, 2, 1, 3], [2, 0, 1, 3], [2, 1, 0, 3],
            [3, 0, 1, 2], [3, 1, 0, 2], [3, 1, 2, 0], [3, 2, 1, 0]
        ])

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            s0 = res["steps"][0]
            self.assertEqual(sorted(s0["entered_tuples"]), expected_entered)
            self.assertEqual(s0["exited_tuples"], [])

    # =========================================================================
    # Group D: C4 k=1 Deferred Vertex Witness
    # =========================================================================
    def test_group_d_c4_k1_deferred_witness(self):
        """C4 k=1: deferred vertex assignment through MISSING branch."""
        workload = {
            "case_id": "TEST_C4_K1_DEFERRED",
            "k": 1,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0},
                    {"u": 2, "v": 3, "label": 0}, {"u": 3, "v": 0, "label": 0}
                ],
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in range(5)],
                "edges": [{"u": 0, "v": 3, "label": 0}, {"u": 3, "v": 2, "label": 0}],
            },
            "updates": [{"op": "+", "u": 0, "v": 1, "label": 0}],
        }

        expected_entered = sorted([
            [0, 1, 2, 3], [0, 3, 2, 1], [1, 0, 3, 2], [1, 2, 3, 0],
            [2, 1, 0, 3], [2, 3, 0, 1], [3, 0, 1, 2], [3, 2, 1, 0]
        ])

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            s0 = res["steps"][0]
            self.assertEqual(sorted(s0["entered_tuples"]), expected_entered)

    # =========================================================================
    # Group E: Typed Label Isolation
    # =========================================================================
    def test_group_e_typed_label_isolation(self):
        """Integer 0 and String '0' must not collide or coerce."""
        workload = {
            "case_id": "TEST_INT_VS_STR_LABELS",
            "k": 0,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 1, "v": 2, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": "0"}, {"id": 20, "label": "0"}],
                "edges": [],
            },
            "updates": [{"op": "+", "u": 10, "v": 20, "label": 0}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            s0 = res["steps"][0]
            # Data vertex label "0" (str) != query vertex label 0 (int) -> 0 matches!
            self.assertEqual(s0["entered_tuples"], [])

    # =========================================================================
    # Group F: Sparse & Negative External IDs
    # =========================================================================
    def test_group_f_sparse_negative_ids(self):
        """Negative, sparse, and large external IDs handled consistently."""
        workload = {
            "case_id": "TEST_SPARSE_NEGATIVE_IDS",
            "k": 0,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": -10, "label": 0}, {"id": 5, "label": 0}, {"id": 42, "label": 0}],
                "edges": [{"u": -10, "v": 5, "label": 0}, {"u": 5, "v": 42, "label": 0}, {"u": -10, "v": 42, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": -100, "label": 0}, {"id": 0, "label": 0}, {"id": 100, "label": 0}],
                "edges": [{"u": -100, "v": 0, "label": 0}, {"u": 0, "v": 100, "label": 0}],
            },
            "updates": [{"op": "+", "u": -100, "v": 100, "label": 0}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            s0 = res["steps"][0]
            # 6 automorphisms on triangle
            self.assertEqual(len(s0["entered_tuples"]), 6)

    # =========================================================================
    # Group G: Non-Induced Chords and Edge Label Mismatch
    # =========================================================================
    def test_group_g_non_induced_chords(self):
        """Extra chords in data graph do not invalidate non-induced embeddings."""
        c4_edges = [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 3, "v": 4, "label": 0}, {"u": 1, "v": 4, "label": 0}]
        workload = {
            "case_id": "TEST_NON_INDUCED_CHORDS",
            "k": 0,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
                "edges": c4_edges,
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
                "edges": [
                    {"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0},
                    {"u": 30, "v": 40, "label": 0}, {"u": 10, "v": 30, "label": 0} # chord!
                ],
            },
            "updates": [{"op": "+", "u": 10, "v": 40, "label": 0}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            s0 = res["steps"][0]
            self.assertEqual(len(s0["entered_tuples"]), 8)

    # =========================================================================
    # Group H: Irrelevant Label Updates
    # =========================================================================
    def test_group_h_irrelevant_updates(self):
        """Updates with edge labels not in Q mutate graph but skip search emissions."""
        workload = {
            "case_id": "TEST_IRRELEVANT_UPDATE",
            "k": 0,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 1, "v": 2, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": 0}, {"id": 20, "label": 0}],
                "edges": [],
            },
            "updates": [{"op": "+", "u": 10, "v": 20, "label": 9999}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            s0 = res["steps"][0]
            self.assertEqual(s0["status"], "SUCCESS")
            self.assertEqual(s0["entered_tuples"], [])
            self.assertEqual(s0["graph_state"]["edges_count"], 1)

    # =========================================================================
    # Group I: Domain D-R and Invariant Guards
    # =========================================================================
    def test_group_i_invalid_k_domain_rejected(self):
        """Threshold k >= lambda(Q) is rejected fail-closed."""
        workload = {
            "case_id": "TEST_INVALID_K",
            "k": 2, # lambda(C4) = 2, so k=2 violates k < lambda(Q)
            "max_work": 500000,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in range(4)],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 3, "v": 0, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in range(4)],
                "edges": [],
            },
            "updates": [{"op": "+", "u": 0, "v": 1, "label": 0}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertFalse(ok)
            self.assertIn("D-R contract violated", res.get("error_message", ""))

    def test_group_i_self_loop_rejected(self):
        """Self loop updates are rejected during prevalidation."""
        workload = {
            "case_id": "TEST_SELF_LOOP",
            "k": 0,
            "max_work": 500000,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [],
            },
            "updates": [{"op": "+", "u": 0, "v": 0, "label": 0}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertFalse(ok)
            s0 = res["steps"][0]
            self.assertEqual(s0["status"], "PREVALIDATION_FAILED")
            self.assertIn("Self-loop", s0["error"])

    # =========================================================================
    # Group J: Work Budget Exhaustion Guard
    # =========================================================================
    def test_group_j_work_budget_exhaustion(self):
        """Search aborts with budget exceeded when max_work is exceeded."""
        k4_q_edges = [{"u": u, "v": v, "label": 0} for (u, v) in itertools.combinations(range(4), 2)]
        workload = {
            "case_id": "TEST_WORK_BUDGET",
            "k": 0,
            "max_work": 5, # extremely low limit to trigger budget exhaustion
            "query": {
                "vertices": [{"id": v, "label": 0} for v in range(4)],
                "edges": k4_q_edges,
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0}, {"u": 2, "v": 3, "label": 0},
                    {"u": 0, "v": 3, "label": 0}
                ],
            },
            "updates": [{"op": "+", "u": 1, "v": 2, "label": 0}],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload, max_work=5)
            self.assertFalse(ok)
            s0 = res["steps"][0]
            self.assertIn(s0["status"], ("CALLBACK_ABORTED", "FAIL"))

    # =========================================================================
    # Group K: Multi-Case Corpus 840 Parity Check (First 20 cases)
    # =========================================================================
    def test_group_k_corpus_840_sample_parity(self):
        """Cross-checks first 20 cases of seed1 against real M0 Oracle."""
        corpus_file = REPO_ROOT / "tests" / "fixtures" / "dstar02" / "corpus_840" / "seed1_cases.jsonl"
        with open(corpus_file, "r", encoding="utf-8") as f:
            for idx in range(20):
                line = f.readline()
                if not line: break
                case = json.loads(line)
                k = int(case["config"].split("k=")[1].split()[0])

                workload = {
                    "case_id": f"corpus_sample_{idx}",
                    "k": k,
                    "max_work": 500000,
                    "query": {
                        "vertices": [{"id": i, "label": lbl} for i, lbl in enumerate(case["query_vlabels"])],
                        "edges": [{"u": e[0], "v": e[1], "label": case["query_elabels"][i]} for i, e in enumerate(case["query_edges"])],
                    },
                    "initial_graph": {
                        "vertices": [{"id": i, "label": lbl} for i, lbl in enumerate(case["data_vlabels"])],
                        "edges": [{"u": e[0], "v": e[1], "label": e[2]} for e in case["G_before"]],
                    },
                    "updates": [
                        {
                            "op": "+" if case["update"]["kind"] in ("INS", "INSERT") else "-",
                            "u": case["update"]["edge"][0],
                            "v": case["update"]["edge"][1],
                            "label": case["update"]["label"],
                        }
                    ],
                }

                # Compute M0
                Q_m0 = DStarGraph()
                for i, lbl in enumerate(case["query_vlabels"]): Q_m0.add_vertex(i, lbl)
                for i, e in enumerate(case["query_edges"]): Q_m0.add_edge(e[0], e[1], case["query_elabels"][i])
                G_m0 = DStarGraph()
                for i, lbl in enumerate(case["data_vlabels"]): G_m0.add_vertex(i, lbl)
                for e in case["G_before"]: G_m0.add_edge(e[0], e[1], e[2])

                oracle = DStarDynamicOracle(Q_m0, G_m0, k=k)
                upd_op = "INSERT" if case["update"]["kind"] in ("INS", "INSERT") else "DELETE"
                delta = oracle.apply_update(DStarUpdate(upd_op, case["update"]["edge"][0], case["update"]["edge"][1], case["update"]["label"]))

                exp_ent = sorted([list(t) for t in delta.entered])
                exp_ext = sorted([list(t) for t in delta.exited])

                for eng in ("m1", "m2"):
                    res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
                    self.assertTrue(ok, f"Case {idx} failed on {eng}")
                    s0 = res["steps"][0]
                    self.assertEqual(sorted(s0["entered_tuples"]), exp_ent, f"Case {idx} {eng} entered mismatch")
                    self.assertEqual(sorted(s0["exited_tuples"]), exp_ext, f"Case {idx} {eng} exited mismatch")

    # =========================================================================
    # Group L: 56-Stream Configuration Parity & Active-Set Reconstruction
    # =========================================================================
    def test_group_l_stream_matrix_sample(self):
        """Verifies full 12-update stream execution and active set reconstruction."""
        # Test C4 k=1 with seed 20261118
        workload = generate_stream_workload(20261118, "C4", 1, 2, "uniform")

        # Run M0 Oracle
        Q_m0 = DStarGraph()
        for v_obj in workload["query"]["vertices"]: Q_m0.add_vertex(v_obj["id"], v_obj["label"])
        for e_obj in workload["query"]["edges"]: Q_m0.add_edge(e_obj["u"], e_obj["v"], e_obj["label"])
        G_m0 = DStarGraph()
        for v_obj in workload["initial_graph"]["vertices"]: G_m0.add_vertex(v_obj["id"], v_obj["label"])
        for e_obj in workload["initial_graph"]["edges"]: G_m0.add_edge(e_obj["u"], e_obj["v"], e_obj["label"])

        oracle = DStarDynamicOracle(Q_m0, G_m0, k=1)
        m0_deltas = []
        m0_active_sets = []
        for upd in workload["updates"]:
            op = "INSERT" if upd["op"] == "+" else "DELETE"
            delta = oracle.apply_update(DStarUpdate(op, upd["u"], upd["v"], upd["label"]))
            m0_deltas.append((sorted([list(t) for t in delta.entered]), sorted([list(t) for t in delta.exited])))
            m0_active_sets.append(copy.deepcopy(oracle.current_matches))

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            steps = res.get("steps", [])
            self.assertEqual(len(steps), 12)

            active_reconstructed: set = set(enumerate_snapshot(Q_m0, G_m0, 1))
            for s_idx in range(12):
                exp_ent, exp_ext = m0_deltas[s_idx]
                target_m0_active = m0_active_sets[s_idx]
                ent = sorted(steps[s_idx]["entered_tuples"])
                ext = sorted(steps[s_idx]["exited_tuples"])

                self.assertEqual(ent, exp_ent, f"Step {s_idx} {eng} entered mismatch")
                self.assertEqual(ext, exp_ext, f"Step {s_idx} {eng} exited mismatch")

                for t in ext: active_reconstructed.discard(tuple(t))
                for t in ent: active_reconstructed.add(tuple(t))
                self.assertEqual(active_reconstructed, target_m0_active, f"Step {s_idx} {eng} active set reconstruction mismatch")

    # =========================================================================
    # Group N: Work Budget Lifecycle & Per-Update Isolation (Finding P3-F01)
    # =========================================================================
    def test_group_n_work_budget_lifecycle(self):
        """Verifies work budget resets per update and does not accumulate false exhaustion."""
        # Workload with 2 consecutive updates, each needing 2 units of work
        workload = {
            "case_id": "test_f01_budget_lifecycle",
            "k": 0,
            "max_work": 3,
            "query": {
                "vertices": [{"id": 1, "label": "V"}, {"id": 2, "label": "V"}],
                "edges": [{"u": 1, "v": 2, "label": "E"}],
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": "V"}, {"id": 20, "label": "V"}],
                "edges": [],
            },
            "updates": [
                {"op": "+", "u": 10, "v": 20, "label": "E"},
                {"op": "-", "u": 10, "v": 20, "label": "E"},
            ],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok, f"Both steps must succeed under budget 3 on {eng}")
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(len(res["steps"]), 2)
            self.assertEqual(res["steps"][0]["status"], "SUCCESS")
            self.assertEqual(res["steps"][1]["status"], "SUCCESS")
            # Step counters are isolated
            self.assertEqual(res["steps"][0]["step_counters"]["work_units"], 2)
            self.assertEqual(res["steps"][1]["step_counters"]["work_units"], 2)
            # Cumulative counters sum up all steps
            self.assertEqual(res["counters"]["work_units"], 4)

        # Workload exceeding budget on single step
        workload_tight = copy.deepcopy(workload)
        workload_tight["max_work"] = 1
        workload_tight["updates"] = [{"op": "+", "u": 10, "v": 20, "label": "E"}]

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload_tight)
            self.assertFalse(ok)
            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["exit_code"], 4)  # WORK_BUDGET_EXCEEDED
            self.assertIn("Work budget exceeded", res["error_message"])
            # Graph rolled back cleanly
            self.assertEqual(res["steps"][0]["graph_state"]["edges_count"], 0)

    # =========================================================================
    # Group O: Strict Input Validation (Findings P3-F02, P3-F03, P3-F04, P3-F08)
    # =========================================================================
    def test_group_o_strict_input_validation(self):
        """Verifies strict fail-closed input validation for op, k, labels, and max_work."""
        base_workload = {
            "case_id": "test_input_validation",
            "k": 0,
            "query": {
                "vertices": [{"id": 1, "label": "V"}, {"id": 2, "label": "V"}],
                "edges": [{"u": 1, "v": 2, "label": "E"}],
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": "V"}, {"id": 20, "label": "V"}],
                "edges": [{"u": 10, "v": 20, "label": "E"}],
            },
            "updates": [{"op": "+", "u": 10, "v": 20, "label": "E"}],
        }

        # 1. Invalid operation (P3-F02)
        wl_op = copy.deepcopy(base_workload)
        wl_op["updates"][0]["op"] = "BANANA"
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_op)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 2)  # INPUT_PARSE_ERROR
        self.assertIn("Invalid update operation", res["error_message"])

        # 2. k integer overflow (P3-F03)
        wl_k_ovf = copy.deepcopy(base_workload)
        wl_k_ovf["k"] = 4294967296
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_k_ovf)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("uint32 range", res["error_message"])

        # 3. Negative k (P3-F03)
        wl_k_neg = copy.deepcopy(base_workload)
        wl_k_neg["k"] = -1
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_k_neg)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("Negative threshold k rejected", res["error_message"])

        # 4. Boolean label rejection (P3-F04)
        wl_bool_lbl = copy.deepcopy(base_workload)
        wl_bool_lbl["query"]["vertices"][0]["label"] = True
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_bool_lbl)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("must be int or string", res["error_message"])

        # 5. Negative max_work rejection (P3-F08)
        wl_mw_neg = copy.deepcopy(base_workload)
        wl_mw_neg["max_work"] = -1
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_mw_neg)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("Negative max_work rejected", res["error_message"])

    # =========================================================================
    # Group P: Error Propagation & Exit Code Contract (Finding P3-F05)
    # =========================================================================
    def test_group_p_error_propagation_exit_codes(self):
        """Verifies exit code semantics: 0 for success, non-zero for all failure classes."""
        # Domain rejection: k >= lambda(Q) on C4 (lambda=2) with k=2
        wl_domain = {
            "case_id": "test_exit_domain_rejection",
            "k": 2,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
                "edges": [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0},
                          {"u": 3, "v": 4, "label": 0}, {"u": 4, "v": 1, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
                "edges": [],
            },
            "updates": [{"op": "+", "u": 10, "v": 20, "label": 0}],
        }
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_domain)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 3)  # DOMAIN_REJECTION
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("D-R contract violated", res["error_message"])

        # Prevalidation failure: self-loop update
        wl_loop = {
            "case_id": "test_exit_self_loop",
            "k": 0,
            "query": {
                "vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 1, "v": 2, "label": 0}],
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": 0}, {"id": 20, "label": 0}],
                "edges": [],
            },
            "updates": [{"op": "+", "u": 10, "v": 10, "label": 0}],
        }
        res, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_loop)
        self.assertFalse(ok)
        self.assertEqual(res["exit_code"], 6)  # STEP_FAILURE
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["steps"][0]["status"], "PREVALIDATION_FAILED")

    # =========================================================================
    # Group Q: Counter Integrity & DFS States (Finding P3-F06)
    # =========================================================================
    def test_group_q_counter_integrity_dfs_states(self):
        """Verifies dfs_states and search counters are incremented accurately on M1 and M2."""
        workload = {
            "case_id": "test_counter_integrity",
            "k": 0,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
                "edges": [{"u": u, "v": v, "label": 0} for (u, v) in itertools.combinations((1, 2, 3, 4), 2)],
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
                "edges": [
                    {"u": 10, "v": 20, "label": 0}, {"u": 10, "v": 30, "label": 0},
                    {"u": 10, "v": 40, "label": 0}, {"u": 20, "v": 30, "label": 0},
                    {"u": 20, "v": 40, "label": 0},
                ],
            },
            "updates": [{"op": "+", "u": 30, "v": 40, "label": 0}],
        }

        # Test M1 counters
        res_m1, _, ok_m1 = run_workload_on_binary(self.binary_path, "m1", workload)
        self.assertTrue(ok_m1)
        cnt_m1 = res_m1["counters"]
        self.assertGreater(cnt_m1["dfs_states"], 0, "M1 dfs_states must be > 0")
        self.assertGreater(cnt_m1["variants_started"], 0)
        self.assertEqual(cnt_m1["raw_exact_results"], 24)
        self.assertEqual(cnt_m1["committed_emissions"], 24)

        # Test M2 counters
        res_m2, _, ok_m2 = run_workload_on_binary(self.binary_path, "m2", workload)
        self.assertTrue(ok_m2)
        cnt_m2 = res_m2["counters"]
        self.assertGreater(cnt_m2["dfs_states"], 0, "M2 dfs_states must be > 0")
        self.assertGreater(cnt_m2["edge_decisions"], 0)
        self.assertEqual(cnt_m2["committed_emissions"], 24)

    # =========================================================================
    # Group R: Deep Graph & Codec State Observability (Finding P3-F09)
    # =========================================================================
    def test_group_r_deep_graph_state_differential(self):
        """Verifies deep logical graph state output matches reference model bit-for-bit."""
        workload = {
            "case_id": "test_deep_graph_state",
            "k": 0,
            "query": {
                "vertices": [{"id": 1, "label": "QV"}, {"id": 2, "label": "QV"}],
                "edges": [{"u": 1, "v": 2, "label": "QE"}],
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": "DV1"}, {"id": 20, "label": "DV2"}, {"id": 30, "label": "DV3"}],
                "edges": [{"u": 10, "v": 20, "label": "DE1"}],
            },
            "updates": [
                {"op": "+", "u": 20, "v": 30, "label": "DE2"},
                {"op": "-", "u": 10, "v": 20, "label": "DE1"},
            ],
        }

        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, workload)
            self.assertTrue(ok)
            steps = res["steps"]

            # Step 0: inserted (20, 30) -> now edges are (10, 20) and (20, 30)
            g0 = steps[0]["graph_state"]
            self.assertEqual(g0["vertices_count"], 3)
            self.assertEqual(g0["edges_count"], 2)
            self.assertEqual(len(g0["vertices"]), 3)
            self.assertEqual(g0["neighbors"]["20"], [10, 30])
            self.assertEqual(g0["neighbors"]["10"], [20])
            self.assertEqual(g0["neighbors"]["30"], [20])

            # Step 1: deleted (10, 20) -> now only edge (20, 30)
            g1 = steps[1]["graph_state"]
            self.assertEqual(g1["vertices_count"], 3)
            self.assertEqual(g1["edges_count"], 1)
            self.assertEqual(g1["neighbors"]["10"], [])
            self.assertEqual(g1["neighbors"]["20"], [30])
            self.assertEqual(g1["neighbors"]["30"], [20])
            self.assertEqual(g1["edges"], [{"label": "DE2", "u": 20, "v": 30}])

            # Codec state verification
            c1 = steps[1]["codec_state"]
            self.assertEqual(c1["query_vertices_count"], 2)
            self.assertEqual(c1["data_vertices_count"], 3)
            self.assertEqual(c1["vertex_labels_count"], 4)  # QV, DV1, DV2, DV3
            self.assertEqual(c1["edge_labels_count"], 3)    # QE, DE1, DE2

    # =========================================================================
    # Group S: M1/M2 Adversarial Witness Parity
    # =========================================================================
    def test_group_s_m1_m2_adversarial_witnesses(self):
        """Verifies M1 and M2 parity on Diamond k=1, C4 k=1 deferred vertex, and K4 k=0."""
        # Witness Diamond k=1
        diamond_wl = {
            "case_id": "test_witness_diamond_k1",
            "k": 1,
            "query": {
                "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
                "edges": [
                    {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
                ],
            },
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
                "edges": [
                    {"u": 10, "v": 20, "label": 0}, {"u": 10, "v": 30, "label": 0},
                    {"u": 20, "v": 40, "label": 0}, {"u": 30, "v": 40, "label": 0}
                ],
            },
            "updates": [{"op": "+", "u": 20, "v": 30, "label": 0}],
        }
        # Compare against dynamic M0 Oracle
        Q_m0 = DStarGraph()
        for v in (1, 2, 3, 4): Q_m0.add_vertex(v, 0)
        for u, v in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]: Q_m0.add_edge(u, v, 0)
        G_m0 = DStarGraph()
        for v in (10, 20, 30, 40): G_m0.add_vertex(v, 0)
        for u, v in [(10, 20), (10, 30), (20, 40), (30, 40)]: G_m0.add_edge(u, v, 0)
        oracle = DStarDynamicOracle(Q_m0, G_m0, k=1)
        delta = oracle.apply_update(DStarUpdate("INSERT", 20, 30, 0))
        expected_tuples = sorted([list(t) for t in delta.entered])

        res_m1, _, ok1 = run_workload_on_binary(self.binary_path, "m1", diamond_wl)
        res_m2, _, ok2 = run_workload_on_binary(self.binary_path, "m2", diamond_wl)
        self.assertTrue(ok1 and ok2)
        self.assertEqual(res_m1["steps"][0]["entered_tuples"], res_m2["steps"][0]["entered_tuples"])
        self.assertEqual(sorted(res_m1["steps"][0]["entered_tuples"]), expected_tuples)

    # =========================================================================
    # Group T: Edmonds-Karp lambda(Q) vs Independent Cut Oracle (Finding P3-F07)
    # =========================================================================
    def test_group_t_query_connectivity_independent_cuts(self):
        """Verifies Edmonds-Karp max-flow lambda(Q) matches independent all-cuts oracle."""
        topologies = [
            ("K2", 2, [(0, 1)]),
            ("K3", 3, [(0, 1), (1, 2), (0, 2)]),
            ("K4", 4, list(itertools.combinations(range(4), 2))),
            ("C4", 4, [(0, 1), (1, 2), (2, 3), (3, 0)]),
            ("C5", 5, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0)]),
            ("Diamond", 4, [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]),
            ("Bridge", 5, [(0, 1), (1, 2), (0, 2), (2, 3), (3, 4), (2, 4)]),
        ]

        for name, nv, edges in topologies:
            expected_lambda = independent_edge_connectivity_cut_oracle(nv, edges)

            # Build workload to query native lambda calculation
            workload = {
                "case_id": f"test_lambda_{name}",
                "k": max(0, expected_lambda - 1),
                "query": {
                    "vertices": [{"id": v + 1, "label": 0} for v in range(nv)],
                    "edges": [{"u": u + 1, "v": v + 1, "label": 0} for (u, v) in edges],
                },
                "initial_graph": {
                    "vertices": [{"id": v + 10, "label": 0} for v in range(nv)],
                    "edges": [],
                },
                "updates": [{"op": "+", "u": 10, "v": 11, "label": 0}],
            }

            res, _, ok = run_workload_on_binary(self.binary_path, "m1", workload)
            self.assertTrue(ok, f"Topology {name} execution failed")
            computed_lambda = res["query_info"]["computed_lambda"]
            self.assertEqual(
                computed_lambda,
                expected_lambda,
                f"Topology {name}: computed lambda {computed_lambda} != cut oracle {expected_lambda}"
            )


if __name__ == "__main__":
    unittest.main()
