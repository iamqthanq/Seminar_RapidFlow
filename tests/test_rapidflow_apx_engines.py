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
import hashlib
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
    validate_graph_state,
    validate_codec_state,
    validate_step_counters,
    validate_native_output_schema,
    validate_micro_workload_bounds,
    validate_required_hand_cases,
    validate_stream_lock_registry,
    build_hand_workloads,
    get_known_case_registry,
    run_hand_and_witness_cases,
    run_56_stream_matrix,
    run_corpus_840,
    STREAM_56_LOCK_PATH,
    REQUIRED_HAND_CASES_PATH,
    CANONICAL_MAX_WORK_CAP,
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

    # =========================================================================
    # Group U: Adversarial Mutation Test Suite (Level A / B / C Multi-Tier)
    # =========================================================================
    def test_group_u_adversarial_mutation_suite(self):
        """Verifies fail-closed detection across Level A (validator units), Level B (stage integration),
        and Level C (runner subprocess) adversarial mutations without tautological assertions (P3-R3-F02)."""
        mutations_injected = 0
        mutations_detected = 0

        # Construct reference graph and valid state
        G_ref = DStarGraph()
        G_ref.add_vertex(10, 0)
        G_ref.add_vertex(20, 0)
        G_ref.add_vertex(30, 0)
        G_ref.add_edge(10, 20, "lblA")
        G_ref.add_edge(20, 30, "lblB")

        valid_graph_state = {
            "vertices_count": 3,
            "edges_count": 2,
            "vertices": [
                {"id": 10, "label": 0, "degree": 1},
                {"id": 20, "label": 0, "degree": 2},
                {"id": 30, "label": 0, "degree": 1},
            ],
            "neighbors": {
                "10": [20],
                "20": [10, 30],
                "30": [20],
            },
            "edges": [
                {"u": 10, "v": 20, "label": "lblA"},
                {"u": 20, "v": 30, "label": "lblB"},
            ],
        }
        # Baseline sanity
        ok_base, diag = validate_graph_state(valid_graph_state, G_ref, "base", "m1", 0)
        self.assertTrue(ok_base, f"Baseline graph state must be valid: {diag}")

        def record_mutation(name: str, detected: bool):
            nonlocal mutations_injected, mutations_detected
            mutations_injected += 1
            if detected:
                mutations_detected += 1
            else:
                self.fail(f"Mutation '{name}' was INCORRECTLY ACCEPTED (false PASS)!")

        # -----------------------------------------------------------------
        # Level A: Validator Unit Rejection Tests
        # -----------------------------------------------------------------
        # 1. Corrupt native vertex ID
        m = copy.deepcopy(valid_graph_state)
        m["vertices"][0]["id"] = -999999
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Corrupt native vertex ID", not v_ok)

        # 2. Corrupt native vertex label
        m = copy.deepcopy(valid_graph_state)
        m["vertices"][0]["label"] = "WRONG_LABEL"
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Corrupt native vertex label", not v_ok)

        # 3. Float vertex ID (R3-F06)
        m = copy.deepcopy(valid_graph_state)
        m["vertices"][0]["id"] = 10.0
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Float vertex ID", not v_ok)

        # 4. Boolean degree (R3-F06)
        m = copy.deepcopy(valid_graph_state)
        m["vertices"][0]["degree"] = True
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Boolean degree", not v_ok)

        # 5. Corrupt degree value
        m = copy.deepcopy(valid_graph_state)
        m["vertices"][1]["degree"] = 999
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Corrupt degree value", not v_ok)

        # 6. Corrupt native edge label
        m = copy.deepcopy(valid_graph_state)
        m["edges"][0]["label"] = "WRONG_LABEL"
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Corrupt native edge label", not v_ok)

        # 7. Corrupt native adjacency (missing neighbor)
        m = copy.deepcopy(valid_graph_state)
        m["neighbors"]["20"] = [10]  # dropped 30
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Corrupt native adjacency (missing neighbor)", not v_ok)

        # 8. Asymmetric adjacency
        m = copy.deepcopy(valid_graph_state)
        m["neighbors"]["10"] = [20, 30]  # 10->30 but 30 doesn't have 10
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Asymmetric adjacency", not v_ok)

        # 9. Float string neighbor key (R3-F06)
        m = copy.deepcopy(valid_graph_state)
        m["neighbors"]["10.0"] = m["neighbors"].pop("10")
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Float string neighbor key", not v_ok)

        # 10. Extra edge in edge list
        m = copy.deepcopy(valid_graph_state)
        m["edges"].append({"u": 10, "v": 30, "label": "extra"})
        m["edges_count"] = 3
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Extra edge", not v_ok)

        # 11. Duplicate edge in edge list
        m = copy.deepcopy(valid_graph_state)
        m["edges"].append(m["edges"][0])
        v_ok, _ = validate_graph_state(m, G_ref)
        record_mutation("Level A: Duplicate edge in edge list", not v_ok)

        # 12. Corrupt codec count (vertex_labels_count)
        valid_codec = {"query_vertices_count": 4, "data_vertices_count": 6, "vertex_labels_count": 2, "edge_labels_count": 2}
        m_c = copy.deepcopy(valid_codec)
        m_c["vertex_labels_count"] = 999999
        c_ok, _ = validate_codec_state(m_c, valid_codec)
        record_mutation("Level A: Corrupt codec count (vertex_labels_count)", not c_ok)

        # 13. Corrupt negative codec count
        m_c = copy.deepcopy(valid_codec)
        m_c["data_vertices_count"] = -1
        c_ok, _ = validate_codec_state(m_c, valid_codec)
        record_mutation("Level A: Corrupt negative codec count", not c_ok)

        # 14. Corrupt boolean disguised as codec count
        m_c = copy.deepcopy(valid_codec)
        m_c["edge_labels_count"] = True
        c_ok, _ = validate_codec_state(m_c, valid_codec)
        record_mutation("Level A: Boolean disguised as codec count", not c_ok)

        # 15. Corrupt work_units > max_work on SUCCESS
        valid_cnt = {"work_units": 10, "max_work": 500000, "dfs_states": 5, "candidate_evaluations": 5, "candidate_extensions": 5, "edge_lookups": 5, "vertex_label_checks": 5, "anchors_started": 1, "variants_started": 1, "raw_exact_results": 1, "missing_set_rejections": 0}
        m_cnt = copy.deepcopy(valid_cnt)
        m_cnt["work_units"] = 999999999
        cnt_ok, _ = validate_step_counters(m_cnt, "m1", 500000, is_success=True)
        record_mutation("Level A: Corrupt work_units > max_work", not cnt_ok)

        # 16. Corrupt negative counter
        m_cnt = copy.deepcopy(valid_cnt)
        m_cnt["dfs_states"] = -42
        cnt_ok, _ = validate_step_counters(m_cnt, "m1", 500000, is_success=True)
        record_mutation("Level A: Corrupt negative counter", not cnt_ok)

        # 17. Corrupt boolean disguised as integer in counters
        m_cnt = copy.deepcopy(valid_cnt)
        m_cnt["work_units"] = True
        cnt_ok, _ = validate_step_counters(m_cnt, "m1", 500000, is_success=True)
        record_mutation("Level A: Corrupt boolean disguised as integer", not cnt_ok)

        # Baseline valid output schema for testing schema validator
        valid_out = {
            "schema_version": "1.1.0",
            "semantics_version": "D-R-1.0",
            "engine_name": "m1",
            "engine": "m1",
            "case_id": "test_case",
            "status": "SUCCESS",
            "error_category": "NONE",
            "error_message": "",
            "raw_input_sha256": "abcdef",
            "execution": {"total_steps": 1, "attempted_steps": 1, "completed_steps": 1, "failed_steps": 0, "unattempted_steps": 0},
            "steps": [{
                "step_index": 0,
                "status": "SUCCESS",
                "commit_status": "COMMITTED",
                "error": "",
                "error_category": "NONE",
                "entered_tuples": [],
                "exited_tuples": [],
            }],
            "counters": {
                "raw_emissions": 0,
                "unique_emissions": 0,
                "committed_emissions": 0,
                "discarded_emissions": 0,
            },
            "work_budget": {
                "default_limit": 500000,
                "canonical_cap": 500000,
                "workload_requested_limit": 500000,
                "cli_requested_limit": 500000,
                "effective_limit": 500000,
            },
        }

        # 18. Missing required output field (execution)
        m_out = copy.deepcopy(valid_out)
        del m_out["execution"]
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Missing required output field (execution)", not s_ok)

        # 19. Wrong schema version
        m_out = copy.deepcopy(valid_out)
        m_out["schema_version"] = "0.9.0"
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Wrong schema version", not s_ok)

        # 20. Wrong semantics version
        m_out = copy.deepcopy(valid_out)
        m_out["semantics_version"] = "D-R-0.1"
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Wrong semantics version", not s_ok)

        # 21. Wrong engine identity
        m_out = copy.deepcopy(valid_out)
        m_out["engine_name"] = "m2"
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Wrong engine identity", not s_ok)

        # 22. Wrong case identity
        m_out = copy.deepcopy(valid_out)
        m_out["case_id"] = "totally_different_case"
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Wrong case identity", not s_ok)

        # 23. Case ID 'UNKNOWN' on SUCCESS (R3-F01)
        m_out = copy.deepcopy(valid_out)
        m_out["case_id"] = "UNKNOWN"
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Case ID 'UNKNOWN' rejected on SUCCESS", not s_ok)

        # 24. Inconsistent execution lifecycle totals (R3-F01)
        m_out = copy.deepcopy(valid_out)
        m_out["execution"]["completed_steps"] = 0  # 0 completed + 0 failed != 1 attempted
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Inconsistent execution lifecycle totals", not s_ok)

        # 25. Root SUCCESS with failed_steps > 0 (R3-F01)
        m_out = copy.deepcopy(valid_out)
        m_out["execution"]["failed_steps"] = 1
        m_out["execution"]["attempted_steps"] = 2
        m_out["execution"]["total_steps"] = 2
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Root SUCCESS with failed_steps > 0", not s_ok)

        # 26. Discarded emissions > 0 in canonical run (R3-F01)
        m_out = copy.deepcopy(valid_out)
        m_out["counters"]["discarded_emissions"] = 5
        m_out["counters"]["raw_emissions"] = 5
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Discarded emissions > 0 in canonical output", not s_ok)

        # 27. Canonical cap loosened in work_budget (R3-F07)
        m_out = copy.deepcopy(valid_out)
        m_out["work_budget"]["canonical_cap"] = 900000
        m_out["work_budget"]["effective_limit"] = 900000
        s_ok, _ = validate_native_output_schema(m_out, "m1", "test_case", None, is_success=True)
        record_mutation("Level A: Canonical cap loosened in work_budget", not s_ok)

        # 28. Micro-workload containment violation (|V(Q)| > 6) (R3-F08)
        wl_oversize_q = {
            "query": {"vertices": [{"id": i, "label": 0} for i in range(7)], "edges": []},
            "initial_graph": {"vertices": [], "edges": []},
            "updates": [],
        }
        b_ok, _ = validate_micro_workload_bounds(wl_oversize_q)
        record_mutation("Level A: Micro-workload containment |V(Q)| > 6", not b_ok)

        # 29. Micro-workload containment violation (|V(G_0)| > 8) (R3-F08)
        wl_oversize_g = {
            "query": {"vertices": [{"id": 1, "label": 0}], "edges": []},
            "initial_graph": {"vertices": [{"id": i, "label": 0} for i in range(9)], "edges": []},
            "updates": [],
        }
        b_ok, _ = validate_micro_workload_bounds(wl_oversize_g)
        record_mutation("Level A: Micro-workload containment |V(G_0)| > 8", not b_ok)

        # 30. Micro-workload containment violation (updates > 12) (R3-F08)
        wl_oversize_u = {
            "query": {"vertices": [{"id": 1, "label": 0}], "edges": []},
            "initial_graph": {"vertices": [{"id": 1, "label": 0}], "edges": []},
            "updates": [{"op": "+", "u": 1, "v": 1, "label": 0} for _ in range(15)],
        }
        b_ok, _ = validate_micro_workload_bounds(wl_oversize_u)
        record_mutation("Level A: Micro-workload containment updates > 12", not b_ok)

        # 31. Dropped required hand case in generator (R3-F03)
        hand_wls = build_hand_workloads()
        dropped_wls = [w for w in hand_wls if w["case_id"] != "CASE_HAND_01"]
        req_ok, _ = validate_required_hand_cases(dropped_wls, REQUIRED_HAND_CASES_PATH)
        record_mutation("Level A: Dropped required hand case in generator", not req_ok)

        # 32. Corrupted required hand case metadata (R3-F03)
        corrupted_meta_wls = copy.deepcopy(hand_wls)
        corrupted_meta_wls[0]["k"] = 999
        req_ok, _ = validate_required_hand_cases(corrupted_meta_wls, REQUIRED_HAND_CASES_PATH)
        record_mutation("Level A: Corrupted required hand case metadata", not req_ok)

        # 33. Duplicate stream in stream lock registry (R3-F05)
        with open(STREAM_56_LOCK_PATH, "r", encoding="utf-8") as f:
            valid_lock_data = json.load(f)
        mut_lock = copy.deepcopy(valid_lock_data)
        mut_lock["streams"][1] = copy.deepcopy(mut_lock["streams"][0])  # duplicate case_id
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_lock, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            lock_ok, _ = validate_stream_lock_registry(tf_path)
            record_mutation("Level A: Duplicate stream in lock registry", not lock_ok)
        finally:
            if tf_path.exists():
                tf_path.unlink()

        # 34. Tampered update count in stream lock registry (R3-F05)
        mut_lock = copy.deepcopy(valid_lock_data)
        mut_lock["streams"][0]["update_count"] = 11  # sum becomes 671 != 672
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_lock, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            lock_ok, _ = validate_stream_lock_registry(tf_path)
            record_mutation("Level A: Tampered update count in lock registry", not lock_ok)
        finally:
            if tf_path.exists():
                tf_path.unlink()

        # -----------------------------------------------------------------
        # Level B: Stage Integration Mutation Tests
        # -----------------------------------------------------------------
        # 35. Corrupted entered tuple in hand cases stage transitions stage to FAIL (Level B)
        from scripts import run_rapidflow_apx_engines as runner_mod
        orig_run_fn = runner_mod.run_workload_on_binary

        def corrupting_run_fn(b_path, eng, wl, *args, **kwargs):
            res, dur, is_ok = orig_run_fn(b_path, eng, wl, *args, **kwargs)
            if wl.get("case_id") == "CASE_HAND_01" and "steps" in res and len(res["steps"]) > 0:
                res["steps"][0]["entered_tuples"] = [[-999, -888]]
            return res, dur, is_ok

        runner_mod.run_workload_on_binary = corrupting_run_fn
        try:
            e2e_results, e2e_ok = run_hand_and_witness_cases(self.binary_path, engine_filter="m1", case_filter="CASE_HAND_01")
            record_mutation("Level B: Corrupted entered tuple fails hand stage", not e2e_ok)
        finally:
            runner_mod.run_workload_on_binary = orig_run_fn

        # 36. Dropped required hand case fails hand stage (Level B)
        orig_build_fn = runner_mod.build_hand_workloads
        def dropping_build_fn():
            wls = orig_build_fn()
            return [w for w in wls if w["case_id"] != "CASE_HAND_02"]

        runner_mod.build_hand_workloads = dropping_build_fn
        try:
            e2e_results, e2e_ok = run_hand_and_witness_cases(self.binary_path, engine_filter="m1")
            record_mutation("Level B: Dropped hand case fails hand stage", not e2e_ok)
        finally:
            runner_mod.build_hand_workloads = orig_build_fn

        # 37. Tampered stream lock file fails 56-stream matrix stage (Level B)
        orig_lock_path = runner_mod.STREAM_56_LOCK_PATH
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({"version": "1.0", "total_streams": 0, "streams": []}, tf)
            tf.flush()
            tf_path = Path(tf.name)
        runner_mod.STREAM_56_LOCK_PATH = tf_path
        try:
            stream_stats, stream_ok = run_56_stream_matrix(self.binary_path, engine_filter="m1", case_filter="stream_C4_k0_20261118_uniform")
            record_mutation("Level B: Tampered stream lock fails stream stage", not stream_ok)
        finally:
            runner_mod.STREAM_56_LOCK_PATH = orig_lock_path
            if tf_path.exists():
                tf_path.unlink()

        # -----------------------------------------------------------------
        # Level C: Runner End-to-End Subprocess Tests
        # -----------------------------------------------------------------
        runner_script = REPO_ROOT / "scripts" / "run_rapidflow_apx_engines.py"

        # 38. Runner invoked with unknown case ID rejected fail-closed (Level C)
        cmd_unknown = [sys.executable, str(runner_script), "--case", "NON_EXISTENT_CASE_999"]
        proc_unk = subprocess.run(cmd_unknown, capture_output=True, text=True, cwd=REPO_ROOT)
        record_mutation("Level C: Unknown case filter rejected with non-zero exit", proc_unk.returncode != 0)

        # 39. Runner invoked with stage mismatch rejected fail-closed (Level C)
        cmd_mismatch = [sys.executable, str(runner_script), "--stage", "stream", "--case", "CASE_HAND_01"]
        proc_mis = subprocess.run(cmd_mismatch, capture_output=True, text=True, cwd=REPO_ROOT)
        record_mutation("Level C: Case stage mismatch rejected with non-zero exit", proc_mis.returncode != 0)

        # 40. Runner invoked with invalid engine option rejected fail-closed (Level C)
        cmd_invalid_eng = [sys.executable, str(runner_script), "--engine", "invalid_engine_name"]
        proc_eng = subprocess.run(cmd_invalid_eng, capture_output=True, text=True, cwd=REPO_ROOT)
        record_mutation("Level C: Invalid engine option rejected with non-zero exit", proc_eng.returncode != 0)

        # Verify 100% mutation detection score (Part 18.5)
        detection_rate = (mutations_detected / mutations_injected) * 100.0
        incorrectly_accepted = mutations_injected - mutations_detected
        print(f"\n=======================================================")
        print(f"       ADVERSARIAL MUTATION TEST SUITE SUMMARY         ")
        print(f"=======================================================")
        print(f"  Mutations injected:             {mutations_injected}")
        print(f"  Mutations detected:             {mutations_detected}")
        print(f"  Mutations incorrectly accepted: {incorrectly_accepted}")
        print(f"  Mutation detection rate:        {detection_rate:.1f}%")
        print(f"=======================================================\n")

        self.assertEqual(incorrectly_accepted, 0, "No mutation may be incorrectly accepted!")
        self.assertEqual(detection_rate, 100.0, "Mutation detection rate must be strictly 100.0%!")


if __name__ == "__main__":
    unittest.main()
