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

import scripts.run_rapidflow_apx_engines as runner_mod

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


def make_disk_intercepting_executor(mutator):
    orig_exec = runner_mod.execute_contained_subprocess
    def intercepted_exec(cmd, *args, **kwargs):
        ret, out, err, timed_out = orig_exec(cmd, *args, **kwargs)
        if "--output" in cmd:
            out_idx = cmd.index("--output") + 1
            out_path = Path(cmd[out_idx])
            if out_path.is_file():
                try:
                    with open(out_path, "r", encoding="utf-8") as rf:
                        data = json.load(rf)
                    mutated = mutator(data)
                    with open(out_path, "w", encoding="utf-8") as wf:
                        json.dump(mutated, wf)
                except Exception:
                    pass
        return ret, out, err, timed_out
    return intercepted_exec


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
                "step_counters": {
                    "work_units": 0,
                    "max_work": 500000,
                    "dfs_states": 0,
                    "candidate_evaluations": 0,
                    "candidate_extensions": 0,
                    "edge_lookups": 0,
                    "vertex_label_checks": 0,
                    "anchors_started": 0,
                    "raw_emissions": 0,
                    "unique_emissions": 0,
                    "committed_emissions": 0,
                    "discarded_emissions": 0,
                    "variants_started": 0,
                    "raw_exact_results": 0,
                    "missing_set_rejections": 0,
                },
            }],
            "counters": {
                "raw_emissions": 0,
                "unique_emissions": 0,
                "committed_emissions": 0,
                "discarded_emissions": 0,
                "work_units": 0,
                "max_work": 500000,
                "anchors_started": 0,
                "dfs_states": 0,
                "candidate_evaluations": 0,
                "candidate_extensions": 0,
                "edge_lookups": 0,
                "vertex_label_checks": 0,
                "variants_started": 0,
                "raw_exact_results": 0,
                "missing_set_rejections": 0,
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
        # Level A: Registry v2 Strictness & Downgrade Resistance (R5-F02)
        # -----------------------------------------------------------------
        # 35. Registry version downgrade to "1.0.0" rejected fail-closed
        with open(REQUIRED_HAND_CASES_PATH, "r", encoding="utf-8") as f:
            valid_reg_data = json.load(f)
        mut_reg = copy.deepcopy(valid_reg_data)
        mut_reg["registry_version"] = "1.0.0"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Registry version downgrade to 1.0.0 rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 36. Registry total_required_cases not integer 25 rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        mut_reg["total_required_cases"] = 24
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Registry total_required_cases != 25 rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 37. Missing mandatory field from registry case rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        del mut_reg["cases"][0]["canonical_workload_sha256"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Missing canonical_workload_sha256 in registry rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 38. Float k in registry case rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        mut_reg["cases"][0]["k"] = 0.0
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Float k in registry rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 39. Boolean k in registry case rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        mut_reg["cases"][0]["k"] = False
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Boolean k in registry rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 40. Negative case with null expected_error_category in registry rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        for c in mut_reg["cases"]:
            if c["is_negative"]:
                c["expected_error_category"] = None
                break
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Negative case with null expected_error_category rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 41. Positive case with non-null expected_error_category in registry rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        mut_reg["cases"][0]["expected_error_category"] = "UNEXPECTED_ERROR"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Positive case with non-null expected_error_category rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # 42. Expected step events sum mismatch with expected_entered_count in registry rejected
        mut_reg = copy.deepcopy(valid_reg_data)
        mut_reg["cases"][0]["expected_step_events"][0]["entered_count"] = 999
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(mut_reg, tf)
            tf.flush()
            tf_path = Path(tf.name)
        try:
            reg_ok, _ = validate_required_hand_cases(hand_wls, tf_path)
            record_mutation("Level A: Expected step events sum mismatch rejected", not reg_ok)
        finally:
            if tf_path.exists(): tf_path.unlink()

        # -----------------------------------------------------------------
        # Level B: Stage Integration Mutation Tests
        # -----------------------------------------------------------------
        # 43. Corrupted entered tuple in hand cases stage transitions stage to FAIL (Level B)
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

        # 44. Dropped required hand case fails hand stage (Level B)
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

        # 45. Tampered stream lock file fails 56-stream matrix stage (Level B)
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

        # 46. Workload query label mismatch fails hand stage via denominator check (Level B)
        def label_mutating_build_fn():
            wls = orig_build_fn()
            wls[0]["query"]["vertices"][0]["label"] = "CORRUPTED_LABEL"
            return wls

        runner_mod.build_hand_workloads = label_mutating_build_fn
        try:
            e2e_results, e2e_ok = run_hand_and_witness_cases(self.binary_path, engine_filter="m1")
            record_mutation("Level B: Workload hash mismatch fails hand stage", not e2e_ok)
        finally:
            runner_mod.build_hand_workloads = orig_build_fn

        # -----------------------------------------------------------------
        # Level C: Runner End-to-End Interception & Subprocess Rejection Tests (R5-F04)
        # -----------------------------------------------------------------
        # Genuine disk interception: intercepts output JSON on disk produced by
        # real native binary subprocess before runner parses and validates it.
        orig_exec = runner_mod.execute_contained_subprocess

        def make_disk_intercepting_executor(mutator):
            def intercepted_exec(cmd, *args, **kwargs):
                ret, out, err, timed_out = orig_exec(cmd, *args, **kwargs)
                if "--output" in cmd:
                    out_idx = cmd.index("--output") + 1
                    out_path = Path(cmd[out_idx])
                    if out_path.is_file():
                        try:
                            with open(out_path, "r", encoding="utf-8") as rf:
                                data = json.load(rf)
                            mutated = mutator(data)
                            with open(out_path, "w", encoding="utf-8") as wf:
                                json.dump(mutated, wf)
                        except Exception:
                            pass
                return ret, out, err, timed_out
            return intercepted_exec

        wl_hand01 = next(w for w in hand_wls if w["case_id"] == "CASE_HAND_01")
        wl_hand22 = next(w for w in hand_wls if w["case_id"] == "CASE_HAND_22")

        # Positive and negative controls with clean execution
        res_ctrl_pos, _, ok_ctrl_pos = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
        self.assertTrue(ok_ctrl_pos and res_ctrl_pos.get("schema_valid"), "Positive control must pass cleanly")

        res_ctrl_neg, _, _ = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
        self.assertTrue(res_ctrl_neg.get("schema_valid") and res_ctrl_neg.get("exit_code") == 6, "Negative control must pass schema validation")

        # Interceptions on disk for positive workload output
        # 47. Missing schema_version on disk rejected fail-closed
        def mut_47(d): d.pop("schema_version", None); return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_47)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Missing schema_version on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 48. Unsupported schema_version "2.0.0" on disk rejected fail-closed
        def mut_48(d): d["schema_version"] = "2.0.0"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_48)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Unsupported schema_version 2.0.0 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 49. Float schema_version 1.1 on disk rejected fail-closed
        def mut_49(d): d["schema_version"] = 1.1; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_49)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Float schema_version on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 50. Missing semantics_version on disk rejected fail-closed
        def mut_50(d): d.pop("semantics_version", None); return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_50)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Missing semantics_version on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 51. Engine mismatch on disk ("m99") rejected fail-closed
        def mut_51(d): d["engine_name"] = "m99"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_51)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Engine mismatch on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 52. Lifecycle sum violation on disk (completed_steps = 99) rejected fail-closed
        def mut_52(d): d["execution"]["completed_steps"] = 99; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_52)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Lifecycle sum violation on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 53. Float step_index on disk (step_index = 0.0) rejected fail-closed
        def mut_53(d): d["steps"][0]["step_index"] = 0.0; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_53)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Float step_index on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 54. Boolean endpoint u on disk (u = True) rejected fail-closed
        def mut_54(d): d["steps"][0]["u"] = True; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_54)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Boolean endpoint u on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 55. Boolean endpoint v on disk (v = False) rejected fail-closed
        def mut_55(d): d["steps"][0]["v"] = False; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_55)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Boolean endpoint v on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 56. Root SUCCESS with step status CALLBACK_ABORTED on disk rejected fail-closed
        def mut_56(d): d["steps"][0]["status"] = "CALLBACK_ABORTED"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_56)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Root SUCCESS with CALLBACK_ABORTED step on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 57. Root SUCCESS with step status UNATTEMPTED on disk rejected fail-closed
        def mut_57(d): d["steps"][0]["status"] = "UNATTEMPTED"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_57)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Root SUCCESS with UNATTEMPTED step on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 58. Root SUCCESS with PREVALIDATION_ERROR on disk rejected fail-closed
        def mut_58(d): d["error_category"] = "PREVALIDATION_ERROR"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_58)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Root SUCCESS with non-NONE error_category on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 59. Duplicate tuple in entered_tuples on disk rejected fail-closed
        def mut_59(d): d["steps"][0]["entered_tuples"] = [[0, 1, 2, 3], [0, 1, 2, 3]]; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_59)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Duplicate tuple in entered_tuples on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 60. Non-injective tuple in entered_tuples on disk rejected fail-closed
        def mut_60(d): d["steps"][0]["entered_tuples"] = [[0, 0, 2, 3]]; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_60)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Non-injective tuple on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 61. Asymmetric adjacency in graph_state on disk rejected fail-closed
        def mut_61(d):
            fk = next(iter(d["steps"][0]["graph_state"]["neighbors"].keys()))
            d["steps"][0]["graph_state"]["neighbors"][fk].append(999)
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_61)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Asymmetric adjacency in graph_state on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 62. Extra neighbor key in graph_state on disk rejected fail-closed
        def mut_62(d): d["steps"][0]["graph_state"]["neighbors"]["999999999"] = []; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_62)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Extra neighbor key in graph_state on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 63. Float string neighbor key in graph_state on disk rejected fail-closed
        def mut_63(d):
            first_k = next(iter(d["steps"][0]["graph_state"]["neighbors"].keys()))
            d["steps"][0]["graph_state"]["neighbors"][f"{first_k}.0"] = d["steps"][0]["graph_state"]["neighbors"].pop(first_k)
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_63)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Float string neighbor key on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 64. Neighbor key > INT64_MAX in graph_state on disk rejected fail-closed
        def mut_64(d):
            first_k = next(iter(d["steps"][0]["graph_state"]["neighbors"].keys()))
            d["steps"][0]["graph_state"]["neighbors"]["999999999999999999999"] = d["steps"][0]["graph_state"]["neighbors"].pop(first_k)
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_64)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Neighbor key > INT64_MAX on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 65. Corrupted committed_emissions counter on disk rejected fail-closed
        def mut_65(d): d["counters"]["committed_emissions"] = 99999; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_65)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Corrupted committed_emissions counter on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 66. Canonical cap loosened in work_budget on disk (> 500000) rejected fail-closed
        def mut_66(d): d["work_budget"]["canonical_cap"] = 9999999; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_66)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand01)
            record_mutation("Level C: Canonical cap loosened in work_budget on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # Interceptions on disk for negative workload output (CASE_HAND_22)
        # 67. Negative root status mutated to "SUCCESS" on disk rejected fail-closed
        def mut_67(d): d["status"] = "SUCCESS"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_67)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Negative root status SUCCESS on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 68. Negative error_category cleared to "NONE" on disk rejected fail-closed
        def mut_68(d): d["error_category"] = "NONE"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_68)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Negative error_category NONE on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 69. Failing step commit_status mutated to "COMMITTED" on disk rejected fail-closed
        def mut_69(d): d["steps"][0]["commit_status"] = "COMMITTED"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_69)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Failing step commit_status COMMITTED on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 70. Failing step status mutated to "SUCCESS" on disk rejected fail-closed
        def mut_70(d): d["steps"][0]["status"] = "SUCCESS"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_70)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Failing step status SUCCESS on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 71. Failing step emitted non-zero entered_tuples on disk rejected fail-closed
        def mut_71(d): d["steps"][0]["entered_tuples"] = [[1, 2]]; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_71)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Failing step emitted tuples on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 72. Failing step graph_state corrupted on disk rejected fail-closed
        def mut_72(d): d["steps"][0]["graph_state"]["edges_count"] = 999; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_72)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Failing step graph_state corrupted on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 73. Stage-level disk interception: mutating entered_tuples on disk fails hand stage
        def mut_73(d): d["steps"][0]["entered_tuples"] = [[-999, -888]]; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_73)
        try:
            stage_results, stage_ok = run_hand_and_witness_cases(self.binary_path, engine_filter="m1", case_filter="CASE_HAND_01")
            record_mutation("Level C: Disk interception in stage execution fails hand stage", not stage_ok)
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 74. Negative root status mutated to "BANANA" on disk rejected fail-closed (R6-F02)
        def mut_74(d): d["status"] = "BANANA"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_74)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Root status BANANA on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 75. Negative step status mutated to "BANANA" on disk rejected fail-closed (R6-F02)
        def mut_75(d): d["steps"][0]["status"] = "BANANA"; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_75)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Step status BANANA on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 76. Negative committed_emissions mutated to 1 on failure on disk rejected fail-closed (R6-F01)
        def mut_76(d): d["counters"]["committed_emissions"] = 1; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_76)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: committed_emissions=1 on failure on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 77. Negative raw_emissions mutated to True on disk rejected fail-closed (R6-F01)
        def mut_77(d): d["counters"]["raw_emissions"] = True; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_77)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: raw_emissions=True on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 78. Negative raw_emissions mutated to -1 on disk rejected fail-closed (R6-F01)
        def mut_78(d): d["counters"]["raw_emissions"] = -1; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_78)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: raw_emissions=-1 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 79. Negative step work_units mutated to 2**62 on disk rejected fail-closed (R6-F01)
        def mut_79(d): d["steps"][0]["step_counters"]["work_units"] = 2**62; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_79)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: work_units=2**62 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 80. Negative step max_work mutated to -1 on disk rejected fail-closed (R6-F01)
        def mut_80(d): d["steps"][0]["step_counters"]["max_work"] = -1; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_80)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: max_work=-1 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 81. Negative step dfs_states mutated to True on disk rejected fail-closed (R6-F01)
        def mut_81(d): d["steps"][0]["step_counters"]["dfs_states"] = True; return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_81)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: dfs_states=True on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 82. Stage-level disk interception on negative case: mutating committed_emissions=1 fails hand stage (Level D, R6-F06)
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_76)
        try:
            stage_results, stage_ok = run_hand_and_witness_cases(self.binary_path, engine_filter="m1", case_filter="CASE_HAND_22")
            record_mutation("Level D: Negative case corruption on disk fails hand stage", not stage_ok)
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # CLI invocations
        # 83. CLI: Runner invoked with unknown case ID rejected fail-closed (Level C)
        runner_script = REPO_ROOT / "scripts" / "run_rapidflow_apx_engines.py"
        cmd_unknown = [sys.executable, str(runner_script), "--case", "NON_EXISTENT_CASE_999"]
        proc_unk = subprocess.run(cmd_unknown, capture_output=True, text=True, cwd=REPO_ROOT)
        record_mutation("Level C: Unknown case filter rejected with non-zero exit", proc_unk.returncode != 0)

        # 84. CLI: Runner invoked with stage mismatch rejected fail-closed (Level C)
        cmd_mismatch = [sys.executable, str(runner_script), "--stage", "stream", "--case", "CASE_HAND_01"]
        proc_mis = subprocess.run(cmd_mismatch, capture_output=True, text=True, cwd=REPO_ROOT)
        record_mutation("Level C: Case stage mismatch rejected with non-zero exit", proc_mis.returncode != 0)

        # 85. CLI: Runner invoked with invalid engine option rejected fail-closed (Level C)
        cmd_invalid_eng = [sys.executable, str(runner_script), "--engine", "invalid_engine_name"]
        proc_eng = subprocess.run(cmd_invalid_eng, capture_output=True, text=True, cwd=REPO_ROOT)
        record_mutation("Level C: Invalid engine option rejected with non-zero exit", proc_eng.returncode != 0)

        # -----------------------------------------------------------------
        # Round 7 Remediation Mutations (R7-F01, R7-F02, R7-F03, R7-F04)
        # -----------------------------------------------------------------
        # 86. Negative step with fake emissions on disk rejected fail-closed (R7-F01)
        def mut_86(d):
            d["counters"]["raw_emissions"] = 10
            d["counters"]["unique_emissions"] = 10
            d["counters"]["committed_emissions"] = 0
            d["counters"]["discarded_emissions"] = 10
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_86)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: fake emissions (10,10,0,10) on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 87. Negative step with fake work units on disk rejected fail-closed (R7-F01)
        def mut_87(d):
            d["counters"]["work_units"] = 1
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_87)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: fake work_units=1 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 88. Negative root max_work mutated to 1 on disk rejected fail-closed (R7-F01)
        def mut_88(d):
            d["counters"]["max_work"] = 1
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_88)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: root max_work=1 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 89. Recovery execution summary mismatch on disk rejected fail-closed (R7-F02)
        def mut_89(d):
            d["execution"]["failed_steps"] = 1
            d["execution"]["completed_steps"] = 2
            d["steps"][0]["status"] = "FAULT_INJECTED"
            d["steps"][0]["commit_status"] = "NOT_COMMITTED"
            d["steps"][0]["error_category"] = "FAULT_INJECTED"
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_89)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Recovery execution summary mismatch on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 90. Status FAULT_INJECTED with incompatible error_category ERROR on disk rejected (R7-F02)
        def mut_90(d):
            d["steps"][0]["error_category"] = "INPUT_SCHEMA_ERROR"
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_90)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Incompatible status-category on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 91. Status PREVALIDATION_FAILED with incompatible error_category on disk rejected (R7-F02)
        def mut_91(d):
            d["steps"][0]["error_category"] = "CALLBACK_ABORTED"
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_91)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: PREVALIDATION_FAILED with CALLBACK_ABORTED rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 92. Workload with string continue_on_error rejected fail-closed (R7-F03)
        wl_str_coe = copy.deepcopy(wl_hand01)
        wl_str_coe["continue_on_error"] = "true"
        r_str, _, _ = run_workload_on_binary(self.binary_path, "m1", wl_str_coe)
        record_mutation("Level C: String continue_on_error rejected with containment error", r_str.get("status") == "CONTAINMENT_VIOLATION")

        # 93. Workload with int continue_on_error rejected fail-closed (R7-F03)
        wl_int_coe = copy.deepcopy(wl_hand01)
        wl_int_coe["continue_on_error"] = 1
        r_int, _, _ = run_workload_on_binary(self.binary_path, "m1", wl_int_coe)
        record_mutation("Level C: Integer continue_on_error rejected with containment error", r_int.get("status") == "CONTAINMENT_VIOLATION")

        # UNATTEMPTED workload for R8-F02 tests
        wl_unattempted = {
            "case_id": "test_unattempted_mutation",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": []
            },
            "updates": [
                {"op": "+", "u": 0, "v": 1, "label": 0},
                {"op": "+", "u": 1, "v": 1, "label": 0},
                {"op": "+", "u": 0, "v": 1, "label": 0}
            ]
        }

        # 94. UNATTEMPTED step with candidate_evaluations=7 on disk rejected fail-closed (R8-F02)
        def mut_94(d):
            if len(d.get("steps", [])) > 2:
                d["steps"][2]["step_counters"] = {"candidate_evaluations": 7}
                if "counters" in d: d["counters"]["candidate_evaluations"] = d["counters"].get("candidate_evaluations", 0) + 7
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_94)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_unattempted)
            record_mutation("Level C: UNATTEMPTED candidate_evaluations=7 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 95. UNATTEMPTED step with candidate_extensions=7 on disk rejected fail-closed (R8-F02)
        def mut_95(d):
            if len(d.get("steps", [])) > 2:
                d["steps"][2]["step_counters"] = {"candidate_extensions": 7}
                if "counters" in d: d["counters"]["candidate_extensions"] = d["counters"].get("candidate_extensions", 0) + 7
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_95)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_unattempted)
            record_mutation("Level C: UNATTEMPTED candidate_extensions=7 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 96. UNATTEMPTED step with work_units=7 on disk rejected fail-closed (R8-F02)
        def mut_96(d):
            if len(d.get("steps", [])) > 2:
                d["steps"][2]["step_counters"] = {"work_units": 7}
                if "counters" in d: d["counters"]["work_units"] = d["counters"].get("work_units", 0) + 7
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_96)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_unattempted)
            record_mutation("Level C: UNATTEMPTED work_units=7 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 97. UNATTEMPTED step with raw_emissions=7 on disk rejected fail-closed (R8-F02)
        def mut_97(d):
            if len(d.get("steps", [])) > 2:
                d["steps"][2]["step_counters"] = {"raw_emissions": 7}
                if "counters" in d: d["counters"]["raw_emissions"] = d["counters"].get("raw_emissions", 0) + 7
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_97)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_unattempted)
            record_mutation("Level C: UNATTEMPTED raw_emissions=7 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 98. UNATTEMPTED step with empty step_counters on disk rejected fail-closed (R8-F02 Option A)
        def mut_98(d):
            if len(d.get("steps", [])) > 2:
                d["steps"][2]["step_counters"] = {}
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_98)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_unattempted)
            record_mutation("Level C: UNATTEMPTED empty step_counters on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 99. Negative CASE_HAND_22 with k=999 on disk rejected fail-closed (R8-F01)
        def mut_99(d):
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d["query_info"]["k"] = 999
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_99)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Negative output k=999 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 100. Negative CASE_HAND_22 with computed_lambda=777 on disk rejected fail-closed (R8-F01)
        def mut_100(d):
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d["query_info"]["computed_lambda"] = 777
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_100)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Negative output computed_lambda=777 on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 101. Negative CASE_HAND_22 with query_canonical_order=[999, 998] on disk rejected fail-closed (R8-F01)
        def mut_101(d):
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d["query_info"]["query_canonical_order"] = [999, 998]
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_101)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Negative output query_canonical_order=[999, 998] on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 102. Negative CASE_HAND_22 with missing query_info on disk rejected fail-closed (R8-F01)
        def mut_102(d):
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d.pop("query_info", None)
            return d
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_102)
        try:
            r, _, ok = run_workload_on_binary(self.binary_path, "m1", wl_hand22)
            record_mutation("Level C: Negative output missing query_info on disk rejected", not r.get("schema_valid"))
        finally: runner_mod.execute_contained_subprocess = orig_exec

        # 103. Level D (D-FULL-01): Full canonical runner (CANONICAL_FULL_RUN) rejects negative query metadata corruption on CASE_HAND_22 (R8-F01, R8-F03)
        def mut_dfull01(d):
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d["query_info"]["k"] = 999
                d["query_info"]["computed_lambda"] = 777
                d["query_info"]["query_canonical_order"] = [999, 998]
            return d
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            d01_sum_path = Path(tf.name)
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_dfull01)
        runner_mod.check_git_provenance = lambda: ({"dirty_state": "CLEAN", "repo_commit": "HEAD", "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1", "upstream_pristine": True}, True)
        d01_exit_code = 0
        try:
            sys.argv = ["run_rapidflow_apx_engines.py", "--output", str(d01_sum_path)]
            try:
                runner_mod.main()
            except SystemExit as se:
                d01_exit_code = se.code
            d01_sum = json.loads(d01_sum_path.read_text()) if d01_sum_path.is_file() else {}
            d01_failed = (d01_exit_code != 0 and d01_sum.get("runner_mode") == "CANONICAL_FULL_RUN" and d01_sum.get("stages", {}).get("hand_cases", {}).get("status") == "FAIL" and d01_sum.get("overall_status") != "P3_ENGINES_VERIFIED")
            record_mutation("Level D: Full canonical runner rejects negative query metadata corruption on disk", d01_failed)
        finally:
            runner_mod.execute_contained_subprocess = orig_exec
            runner_mod.check_git_provenance = check_git_provenance
            if d01_sum_path.exists(): d01_sum_path.unlink()

        # 104. Level D (D-FULL-02): Full canonical runner (CANONICAL_FULL_RUN) rejects missing negative query_info on CASE_HAND_22 (R8-F01, R8-F03)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            d02_sum_path = Path(tf.name)
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_102)
        runner_mod.check_git_provenance = lambda: ({"dirty_state": "CLEAN", "repo_commit": "HEAD", "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1", "upstream_pristine": True}, True)
        d02_exit_code = 0
        try:
            sys.argv = ["run_rapidflow_apx_engines.py", "--output", str(d02_sum_path)]
            try:
                runner_mod.main()
            except SystemExit as se:
                d02_exit_code = se.code
            d02_sum = json.loads(d02_sum_path.read_text()) if d02_sum_path.is_file() else {}
            d02_failed = (d02_exit_code != 0 and d02_sum.get("runner_mode") == "CANONICAL_FULL_RUN" and d02_sum.get("stages", {}).get("hand_cases", {}).get("status") == "FAIL" and d02_sum.get("overall_status") != "P3_ENGINES_VERIFIED")
            record_mutation("Level D: Full canonical runner rejects missing negative query_info on disk", d02_failed)
        finally:
            runner_mod.execute_contained_subprocess = orig_exec
            runner_mod.check_git_provenance = check_git_provenance
            if d02_sum_path.exists(): d02_sum_path.unlink()

        # 105. Level D (D03): Diagnostic runner rejects corrupted recovery lifecycle on disk (R7-F04)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            d03_sum_path = Path(tf.name)
        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_89)
        runner_mod.check_git_provenance = lambda: ({"dirty_state": "CLEAN", "repo_commit": "HEAD", "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1", "upstream_pristine": True}, True)
        d03_exit_code = 0
        try:
            sys.argv = ["run_rapidflow_apx_engines.py", "--stage", "hand", "--case", "CASE_HAND_22", "--output", str(d03_sum_path)]
            try:
                runner_mod.main()
            except SystemExit as se:
                d03_exit_code = se.code
            d03_sum = json.loads(d03_sum_path.read_text()) if d03_sum_path.is_file() else {}
            d03_failed = (d03_exit_code != 0 and d03_sum.get("overall_status") != "DIAGNOSTIC_SUBSET_PASS")
            record_mutation("Level D: Recovery lifecycle mismatch fails diagnostic runner and exits nonzero", d03_failed)
        finally:
            runner_mod.execute_contained_subprocess = orig_exec
            runner_mod.check_git_provenance = check_git_provenance
            if d03_sum_path.exists(): d03_sum_path.unlink()

        # 106. Level D (D04): Positive canonical control uncorrupted execution passes cleanly (R7-F04)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            d04_sum_path = Path(tf.name)
        runner_mod.check_git_provenance = lambda: ({"dirty_state": "CLEAN", "repo_commit": "HEAD", "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1", "upstream_pristine": True}, True)
        d04_exit_code = 0
        try:
            sys.argv = ["run_rapidflow_apx_engines.py", "--stage", "hand", "--case", "CASE_HAND_01", "--output", str(d04_sum_path)]
            try:
                runner_mod.main()
            except SystemExit as se:
                d04_exit_code = se.code
            d04_sum = json.loads(d04_sum_path.read_text()) if d04_sum_path.is_file() else {}
            d04_passed = (d04_exit_code == 0 and d04_sum.get("overall_status") == "DIAGNOSTIC_SUBSET_PASS")
            record_mutation("Level D: Positive canonical control passes with exit code 0", d04_passed)
        finally:
            runner_mod.check_git_provenance = check_git_provenance
            if d04_sum_path.exists(): d04_sum_path.unlink()

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

    # =========================================================================
    # Group V: Multi-Step Transaction Rollback & Report Consistency Audit
    # =========================================================================
    def test_multistep_prevalidation_failure_preserves_prior_commits(self):
        """Verifies multi-step transaction rollback preservation on prevalidation failure (R4-F07, R5-F03 Test A).
        Step 0: Valid insertion (+edge) commits successfully and is preserved in graph state.
        Step 1: Invalid update (self-loop) fails prevalidation, rolls back, and does NOT contaminate graph state.
        Step 2: Subsequent update remains UNATTEMPTED with NOT_COMMITTED status.
        Verifies bit-for-bit graph state preservation against DStarGraph reference model at every step."""
        wl = {
            "case_id": "test_multistep_rollback_preservation",
            "query": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 1, "v": 2, "label": 0}]
            },
            "k": 0,
            "initial_graph": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}]
            },
            "updates": [
                {"op": "+", "u": 2, "v": 3, "label": 0},
                {"op": "+", "u": 0, "v": 0, "label": 0},
                {"op": "+", "u": 1, "v": 2, "label": 0}
            ]
        }
        for eng in ["m1", "m2"]:
            res, dur, _ = run_workload_on_binary(self.binary_path, eng, wl)
            self.assertEqual(res.get("status"), "FAIL")
            self.assertEqual(res.get("exit_code"), 6)
            self.assertTrue(res.get("schema_valid"), f"Schema must be valid on negative run: {res.get('schema_error')}")

            exec_info = res.get("execution", {})
            self.assertEqual(exec_info.get("total_steps"), 3)
            self.assertEqual(exec_info.get("completed_steps"), 1)
            self.assertEqual(exec_info.get("failed_steps"), 1)
            self.assertEqual(exec_info.get("unattempted_steps"), 1)
            self.assertEqual(exec_info.get("attempted_steps"), 2)

            steps = res.get("steps", [])
            self.assertEqual(len(steps), 3)

            # Step 0: Committed
            self.assertEqual(steps[0]["status"], "SUCCESS")
            self.assertEqual(steps[0]["commit_status"], "COMMITTED")
            self.assertEqual(steps[0]["graph_state"]["edges_count"], 4)

            # Step 1: Prevalidation failed, transaction rolled back
            self.assertEqual(steps[1]["status"], "PREVALIDATION_FAILED")
            self.assertEqual(steps[1]["commit_status"], "NOT_COMMITTED")
            self.assertEqual(steps[1]["graph_state"]["edges_count"], 4)
            self.assertEqual(steps[1]["graph_state"]["vertices_count"], 4)
            self.assertNotIn(0, steps[1]["graph_state"]["neighbors"].get("0", []))

            # Step 2: Unattempted
            self.assertEqual(steps[2]["status"], "UNATTEMPTED")
            self.assertEqual(steps[2]["commit_status"], "NOT_COMMITTED")

    def test_active_transaction_rollback_after_mutation(self):
        """Verifies active transaction rollback occurring AFTER graph/codec mutation and BEFORE commit (R5-F03 Test B).
        Step 0: Valid update commits 4th edge and emits entered tuples.
        Step 1: Active transaction mutates graph (adds 5th edge), then fault injection (AFTER_MUTATION) fires.
                Transaction manager aborts transaction, undos graph & codec mutations, and restores graph state.
                Step 1 is marked FAULT_INJECTED with commit_status NOT_COMMITTED and 0 published tuples.
        Step 2: Subsequent step remains UNATTEMPTED with NOT_COMMITTED status."""
        wl = {
            "case_id": "test_active_tx_rollback_after_mutation",
            "query": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}, {"u": 2, "v": 3, "label": 0}]
            },
            "k": 0,
            "initial_graph": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0}]
            },
            "updates": [
                {"op": "+", "u": 2, "v": 3, "label": 0},
                {"op": "+", "u": 1, "v": 2, "label": 0, "fault": "AFTER_MUTATION"},
                {"op": "+", "u": 0, "v": 3, "label": 0}
            ]
        }
        for eng in ["m1", "m2"]:
            res, dur, _ = run_workload_on_binary(self.binary_path, eng, wl)
            self.assertEqual(res.get("status"), "FAIL")
            self.assertEqual(res.get("exit_code"), 6)
            self.assertEqual(res.get("error_category"), "FAULT_INJECTED")
            self.assertTrue(res.get("schema_valid"), f"Schema must be valid: {res.get('schema_error')}")

            exec_info = res.get("execution", {})
            self.assertEqual(exec_info.get("total_steps"), 3)
            self.assertEqual(exec_info.get("completed_steps"), 1)
            self.assertEqual(exec_info.get("failed_steps"), 1)
            self.assertEqual(exec_info.get("unattempted_steps"), 1)

            steps = res.get("steps", [])
            self.assertEqual(len(steps), 3)

            # Step 0: Committed successfully
            self.assertEqual(steps[0]["status"], "SUCCESS")
            self.assertEqual(steps[0]["commit_status"], "COMMITTED")
            self.assertEqual(steps[0]["graph_state"]["edges_count"], 4)
            self.assertGreater(len(steps[0]["entered_tuples"]), 0)

            # Step 1: Active transaction rolled back after mutation
            self.assertEqual(steps[1]["status"], "FAULT_INJECTED")
            self.assertEqual(steps[1]["commit_status"], "NOT_COMMITTED")
            self.assertEqual(len(steps[1]["entered_tuples"]), 0)
            self.assertEqual(len(steps[1]["exited_tuples"]), 0)
            # Edge count restored to 4 (the mutated (1,2) edge was undone)
            self.assertEqual(steps[1]["graph_state"]["edges_count"], 4)
            self.assertEqual(steps[1]["graph_state"]["vertices_count"], 4)
            # Neighbor list of 1 does not contain 2
            self.assertNotIn(2, steps[1]["graph_state"]["neighbors"].get("1", []))

            # Step 2: Unattempted
            self.assertEqual(steps[2]["status"], "UNATTEMPTED")
            self.assertEqual(steps[2]["commit_status"], "NOT_COMMITTED")

    def test_active_transaction_rollback_before_publish_staged_tuples(self):
        """Verifies active transaction rollback occurring BEFORE publish, proving staged tuples are discarded (R6-F03).
        Workload: 4-cycle Diamond witness:
          Query: (0,1), (0,2), (1,3), (2,3), labels 0, k=0.
          Initial: 3 edges (0,1), (0,2), (1,3).
          Step 0: INSERT (1,2) -> commits, graph has 4 edges, 0 entered tuples.
          Step 1: INSERT (2,3) completes C4 cycle -> hypothetical success yields 8 entered mappings.
                  With fault: BEFORE_PUBLISH, all 8 candidate mappings are staged in tuple sink,
                  then transaction manager aborts transaction, discards all 8 staged tuples,
                  restores graph state to Step 0 state (4 edges), and commits 0 tuples!
                  Tuple sink counters: raw_emissions >= 8, unique_emissions >= 8, committed_emissions == 0, discarded_emissions >= 8."""
        wl = {
            "case_id": "test_active_tx_rollback_before_publish",
            "query": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0},
                    {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 3, "label": 0},
                ]
            },
            "k": 0,
            "initial_graph": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0},
                    {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0},
                ]
            },
            "updates": [
                {"op": "+", "u": 1, "v": 2, "label": 0},
                {"op": "+", "u": 2, "v": 3, "label": 0, "fault": "BEFORE_PUBLISH"},
                {"op": "+", "u": 0, "v": 3, "label": 0}
            ]
        }
        # First verify positive control on Step 1 (without fault) yields 8 entered tuples
        wl_pos = copy.deepcopy(wl)
        del wl_pos["updates"][1]["fault"]
        wl_pos["updates"] = wl_pos["updates"][:2]
        for eng in ["m1", "m2"]:
            res_pos, _, ok_pos = run_workload_on_binary(self.binary_path, eng, wl_pos)
            self.assertTrue(ok_pos, f"Positive witness must succeed on {eng}")
            self.assertEqual(len(res_pos["steps"][1]["entered_tuples"]), 8, f"{eng} positive control must yield 8 entered tuples")
            self.assertEqual(res_pos["counters"]["committed_emissions"], 8)
            self.assertEqual(res_pos["counters"]["discarded_emissions"], 0)

        # Now verify faulted run: staged tuples are discarded, graph rolled back
        for eng in ["m1", "m2"]:
            res, dur, _ = run_workload_on_binary(self.binary_path, eng, wl)
            self.assertEqual(res.get("status"), "FAIL")
            self.assertEqual(res.get("exit_code"), 6)
            self.assertEqual(res.get("error_category"), "FAULT_INJECTED")

            steps = res.get("steps", [])
            self.assertEqual(len(steps), 3)

            # Step 0: Committed
            self.assertEqual(steps[0]["status"], "SUCCESS")
            self.assertEqual(steps[0]["commit_status"], "COMMITTED")
            self.assertEqual(len(steps[0]["entered_tuples"]), 0)
            self.assertEqual(steps[0]["graph_state"]["edges_count"], 4)

            # Step 1: Staged tuples discarded from sink on abort
            self.assertEqual(steps[1]["status"], "FAULT_INJECTED")
            self.assertEqual(steps[1]["commit_status"], "NOT_COMMITTED")
            self.assertEqual(len(steps[1]["entered_tuples"]), 0, "Staged tuples must be aborted, not published")
            self.assertEqual(len(steps[1]["exited_tuples"]), 0)
            self.assertEqual(steps[1]["graph_state"]["edges_count"], 4, "Graph must be restored to Step 0 state")

            # Check emission counters in root: raw >= 8, unique >= 8, committed == 0, discarded >= 8
            cnt = res.get("counters", {})
            self.assertGreaterEqual(cnt.get("raw_emissions", 0), 8)
            self.assertGreaterEqual(cnt.get("unique_emissions", 0), 8)
            self.assertEqual(cnt.get("committed_emissions"), 0)
            self.assertGreaterEqual(cnt.get("discarded_emissions", 0), 8)
            self.assertEqual(cnt["unique_emissions"], cnt["committed_emissions"] + cnt["discarded_emissions"])

            # Step 2: Unattempted
            self.assertEqual(steps[2]["status"], "UNATTEMPTED")

    def test_active_transaction_recovery_subsequent_step(self):
        """Verifies full transaction lifecycle recovery on the SAME transaction manager and streaming engine instance (R6-F04):
        Step 0 (Step A): Valid transaction executes and commits -> graph has 4 edges, 0 entered.
        Step 1 (Step B): Active transaction mutates graph, stages candidates, then aborts (BEFORE_PUBLISH fault) ->
                         rolls back graph, restores codec, discards staged tuples, commit_status NOT_COMMITTED.
        Step 2 (Step C): Subsequent valid transaction executed on the SAME manager instance without reallocating engine ->
                         successfully applies mutation, computes 8 entered tuples matching DStar Oracle, and commits!"""
        wl = {
            "case_id": "test_active_tx_recovery_lifecycle",
            "query": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0},
                    {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 3, "label": 0},
                ]
            },
            "k": 0,
            "initial_graph": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0},
                    {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0},
                ]
            },
            "continue_on_error": True,
            "updates": [
                {"op": "+", "u": 1, "v": 2, "label": 0},
                {"op": "+", "u": 2, "v": 3, "label": 0, "fault": "BEFORE_PUBLISH"},
                {"op": "+", "u": 2, "v": 3, "label": 0}
            ]
        }
        for eng in ["m1", "m2"]:
            res, dur, _ = run_workload_on_binary(self.binary_path, eng, wl)
            self.assertEqual(res.get("status"), "FAIL", "Overall status is FAIL because Step 1 had fault injection")
            self.assertEqual(res.get("exit_code"), 6)
            self.assertEqual(res.get("error_category"), "FAULT_INJECTED")

            exec_info = res.get("execution", {})
            self.assertEqual(exec_info.get("total_steps"), 3)
            self.assertEqual(exec_info.get("attempted_steps"), 3)
            self.assertEqual(exec_info.get("completed_steps"), 2)
            self.assertEqual(exec_info.get("failed_steps"), 1)
            self.assertEqual(exec_info.get("unattempted_steps"), 0)

            steps = res.get("steps", [])
            self.assertEqual(len(steps), 3)

            # Step 0 (Step A): Successfully committed
            self.assertEqual(steps[0]["status"], "SUCCESS")
            self.assertEqual(steps[0]["commit_status"], "COMMITTED")
            self.assertEqual(len(steps[0]["entered_tuples"]), 0)
            self.assertEqual(steps[0]["graph_state"]["edges_count"], 4)

            # Step 1 (Step B): Aborted, rolled back, 0 published
            self.assertEqual(steps[1]["status"], "FAULT_INJECTED")
            self.assertEqual(steps[1]["commit_status"], "NOT_COMMITTED")
            self.assertEqual(len(steps[1]["entered_tuples"]), 0)
            self.assertEqual(steps[1]["graph_state"]["edges_count"], 4)

            # Step 2 (Step C): Successfully committed on the SAME engine instance!
            self.assertEqual(steps[2]["status"], "SUCCESS")
            self.assertEqual(steps[2]["commit_status"], "COMMITTED")
            self.assertEqual(len(steps[2]["entered_tuples"]), 8)
            self.assertEqual(steps[2]["graph_state"]["edges_count"], 5)

            # Cumulative counters
            cnt = res.get("counters", {})
            self.assertGreaterEqual(cnt.get("raw_emissions", 0), 16)
            self.assertGreaterEqual(cnt.get("unique_emissions", 0), 16)
            self.assertEqual(cnt.get("committed_emissions"), 8)
            self.assertGreaterEqual(cnt.get("discarded_emissions", 0), 8)
            self.assertEqual(cnt["unique_emissions"], cnt["committed_emissions"] + cnt["discarded_emissions"])

    def test_postcommit_output_error_classification(self):
        """Verifies that output transport failures (e.g. unwritable output file path) return ExitCode 7 (IO_ERROR),
        which is classified as an output serialization/transport error distinct from domain, prevalidation, or transaction abort errors (R6-F05).
        Workload: Valid matching C4 Diamond witness that produces 8 entered tuples on normal execution."""
        wl = {
            "case_id": "test_postcommit_io_error",
            "query": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0},
                    {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0},
                    {"u": 2, "v": 3, "label": 0},
                ]
            },
            "k": 0,
            "initial_graph": {
                "vertices": [{"id": i, "label": 0} for i in range(4)],
                "edges": [
                    {"u": 0, "v": 1, "label": 0},
                    {"u": 0, "v": 2, "label": 0},
                    {"u": 1, "v": 3, "label": 0},
                ]
            },
            "updates": [
                {"op": "+", "u": 2, "v": 3, "label": 0}
            ]
        }
        # First verify positive control on normal writable path succeeds with ExitCode 0 and 8 entered tuples
        res_pos, _, ok_pos = run_workload_on_binary(self.binary_path, "m1", wl)
        self.assertTrue(ok_pos, f"Workload must be a valid positive matching workload: {res_pos.get('error_message')}")
        self.assertEqual(res_pos.get("exit_code"), 0)
        self.assertEqual(len(res_pos["steps"][0]["entered_tuples"]), 8)

        # Now run with unwritable destination (/dev/full or non-existent path)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as wf:
            json.dump(wl, wf)
            wf.flush()
            wf_path = wf.name

        try:
            # Test unwritable output path
            cmd = [str(self.binary_path), "--engine", "m1", "--workload", wf_path, "--output", "/nonexistent/invalid_dir/output.json"]
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
            self.assertEqual(proc.returncode, 7, f"Output transport failure must exit with code 7 (IO_ERROR), got {proc.returncode}")
            self.assertIn("Output write failure", proc.stderr)
            self.assertNotIn("DOMAIN_ERROR", proc.stderr)
            self.assertNotIn("Pre-validation", proc.stderr)

            # If /dev/full exists and is writable, also test /dev/full (ENOSPC simulation)
            if os.path.exists("/dev/full"):
                try:
                    cmd_full = [str(self.binary_path), "--engine", "m1", "--workload", wf_path, "--output", "/dev/full"]
                    proc_full = subprocess.run(cmd_full, capture_output=True, text=True, cwd=REPO_ROOT)
                    self.assertEqual(proc_full.returncode, 7, f"/dev/full write failure must exit with code 7 (IO_ERROR), got {proc_full.returncode}")
                    self.assertIn("Output write failure", proc_full.stderr)
                except PermissionError:
                    pass
        finally:
            if Path(wf_path).exists():
                Path(wf_path).unlink()

    def test_validation_report_consistency_audit(self):
        """Audits docs/rapidflow_apx_native_engines_validation_report.md Section 4.1 against
        configs/rapidflow_apx_required_cases.json: ensures 0 mismatched names, 0 mismatched k,
        0 missing IDs, and 0 obsolete IDs (R4-F08)."""
        report_path = REPO_ROOT / "docs" / "rapidflow_apx_native_engines_validation_report.md"
        self.assertTrue(report_path.exists(), "Validation report must exist")
        with open(report_path, "r", encoding="utf-8") as f:
            report_text = f.read()

        import re
        sec_match = re.search(r"### 4\.1\..*?\n(.*?)(?=\n### 4\.2|\Z)", report_text, re.DOTALL)
        self.assertIsNotNone(sec_match, "Section 4.1 must exist in validation report")

        table_lines = [l.strip() for l in sec_match.group(1).splitlines() if l.strip().startswith("| `CASE_HAND_")]
        self.assertEqual(len(table_lines), 25, f"Section 4.1 table must have exactly 25 rows, found {len(table_lines)}")

        with open(REQUIRED_HAND_CASES_PATH, "r", encoding="utf-8") as f:
            registry_data = json.load(f)
        reg_cases = {c["case_id"]: c for c in registry_data["cases"]}
        self.assertEqual(len(reg_cases), 25, "Registry must contain exactly 25 cases")

        parsed_case_ids = set()
        for line in table_lines:
            cols = [col.strip() for col in line.split("|")[1:-1]]
            case_id = cols[0].replace("`", "")
            case_name = cols[1].replace("`", "")
            k_val = int(cols[4])

            self.assertIn(case_id, reg_cases, f"Report has obsolete or unapproved case_id: {case_id}")
            reg_entry = reg_cases[case_id]
            self.assertEqual(case_name, reg_entry["case_name"], f"Case name mismatch for {case_id}: report={case_name} vs reg={reg_entry['case_name']}")
            self.assertEqual(k_val, reg_entry["k"], f"k value mismatch for {case_id}: report={k_val} vs reg={reg_entry['k']}")
            parsed_case_ids.add(case_id)

        missing_from_report = set(reg_cases.keys()) - parsed_case_ids
        self.assertEqual(len(missing_from_report), 0, f"Cases missing from report: {missing_from_report}")
        obsolete_in_report = parsed_case_ids - set(reg_cases.keys())
        self.assertEqual(len(obsolete_in_report), 0, f"Obsolete cases in report: {obsolete_in_report}")

    # =========================================================================
    # Group W: Round 8 Genuine Full Canonical Level D & Lifecycle Tests (R8-F01, R8-F02, R8-F03)
    # =========================================================================
    def test_full_canonical_rejects_negative_query_metadata_corruption(self):
        """Genuine Level D (D-FULL-01): Verifies that full canonical runner (CANONICAL_FULL_RUN)
        intercepting mutated negative query metadata on CASE_HAND_22 fails hand stage, fails overall status,
        and terminates with non-zero exit code (R8-F01, R8-F03)."""
        orig_exec = runner_mod.execute_contained_subprocess
        orig_prov = runner_mod.check_git_provenance
        mutation_count = 0

        def mut_query_meta(d):
            nonlocal mutation_count
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d["query_info"]["k"] = 999
                d["query_info"]["computed_lambda"] = 777
                d["query_info"]["query_canonical_order"] = [999, 998]
                mutation_count += 1
            return d

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            sum_path = Path(tf.name)

        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_query_meta)
        runner_mod.check_git_provenance = lambda: ({"dirty_state": "CLEAN", "repo_commit": "HEAD", "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1", "upstream_pristine": True}, True)
        exit_code = 0
        try:
            sys.argv = ["run_rapidflow_apx_engines.py", "--output", str(sum_path)]
            try:
                runner_mod.main()
            except SystemExit as se:
                exit_code = se.code
            sum_data = json.loads(sum_path.read_text()) if sum_path.is_file() else {}
            self.assertEqual(mutation_count, 2, "Both M1 and M2 outputs must be intercepted on CASE_HAND_22")
            self.assertEqual(sum_data.get("runner_mode"), "CANONICAL_FULL_RUN", "Runner must operate in full canonical mode")
            self.assertNotEqual(exit_code, 0, "Corrupted negative query metadata must cause non-zero process exit")
            self.assertEqual(sum_data.get("stages", {}).get("hand_cases", {}).get("status"), "FAIL", "Hand cases stage must FAIL")
            self.assertEqual(sum_data.get("overall_status"), "FAIL", "Overall status must be FAIL")
            self.assertNotIn("corpus_840", sum_data.get("stages", {}), "Corpus stage must not run after hand stage failure (fail-fast)")
            self.assertNotIn("stream_56_matrix", sum_data.get("stages", {}), "Stream stage must not run after hand stage failure (fail-fast)")
        finally:
            runner_mod.execute_contained_subprocess = orig_exec
            runner_mod.check_git_provenance = orig_prov
            if sum_path.exists():
                sum_path.unlink()

    def test_full_canonical_rejects_missing_negative_query_info(self):
        """Genuine Level D (D-FULL-02): Verifies that full canonical runner (CANONICAL_FULL_RUN)
        intercepting missing query_info on CASE_HAND_22 fails hand stage, fails overall status,
        and terminates with non-zero exit code (R8-F01, R8-F03)."""
        orig_exec = runner_mod.execute_contained_subprocess
        orig_prov = runner_mod.check_git_provenance
        mutation_count = 0

        def mut_missing_qinfo(d):
            nonlocal mutation_count
            if d.get("case_id") == "CASE_HAND_22" and "query_info" in d:
                d.pop("query_info", None)
                mutation_count += 1
            return d

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            sum_path = Path(tf.name)

        runner_mod.execute_contained_subprocess = make_disk_intercepting_executor(mut_missing_qinfo)
        runner_mod.check_git_provenance = lambda: ({"dirty_state": "CLEAN", "repo_commit": "HEAD", "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1", "upstream_pristine": True}, True)
        exit_code = 0
        try:
            sys.argv = ["run_rapidflow_apx_engines.py", "--output", str(sum_path)]
            try:
                runner_mod.main()
            except SystemExit as se:
                exit_code = se.code
            sum_data = json.loads(sum_path.read_text()) if sum_path.is_file() else {}
            self.assertEqual(mutation_count, 2, "Both M1 and M2 outputs must be intercepted on CASE_HAND_22")
            self.assertEqual(sum_data.get("runner_mode"), "CANONICAL_FULL_RUN", "Runner must operate in full canonical mode")
            self.assertNotEqual(exit_code, 0, "Missing query_info must cause non-zero process exit")
            self.assertEqual(sum_data.get("stages", {}).get("hand_cases", {}).get("status"), "FAIL", "Hand cases stage must FAIL")
            self.assertEqual(sum_data.get("overall_status"), "FAIL", "Overall status must be FAIL")
        finally:
            runner_mod.execute_contained_subprocess = orig_exec
            runner_mod.check_git_provenance = orig_prov
            if sum_path.exists():
                sum_path.unlink()

    def test_unattempted_step_counters_strict_rejection(self):
        """Verifies strict fail-closed rejection of fabricated counters or step_counters objects on UNATTEMPTED steps (R8-F02)."""
        wl = {
            "case_id": "test_unattempted_strict_contract",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": []
            },
            "updates": [
                {"op": "+", "u": 0, "v": 1, "label": 0},
                {"op": "+", "u": 1, "v": 1, "label": 0},
                {"op": "+", "u": 0, "v": 1, "label": 0}
            ]
        }
        for eng in ("m1", "m2"):
            res, _, _ = run_workload_on_binary(self.binary_path, eng, wl)
            self.assertEqual(res.get("exit_code"), 6, f"Expected exit code 6 on self-loop update for {eng}")
            self.assertTrue(res.get("schema_valid"), f"Uncorrupted multi-step execution must be schema valid: {res.get('schema_error')}")
            steps = res.get("steps", [])
            self.assertEqual(len(steps), 3)
            self.assertEqual(steps[2]["status"], "UNATTEMPTED")
            self.assertNotIn("step_counters", steps[2], "Native UNATTEMPTED step must not emit step_counters")

            # 1. Mutate: inject candidate_evaluations = 7
            d_ce = copy.deepcopy(res)
            d_ce["steps"][2]["step_counters"] = {"candidate_evaluations": 7}
            d_ce["counters"]["candidate_evaluations"] = d_ce["counters"].get("candidate_evaluations", 0) + 7
            v_ok, v_diag = runner_mod.validate_native_output_schema(d_ce, eng, wl["case_id"], is_success=False, workload=wl)
            self.assertFalse(v_ok, "UNATTEMPTED step with candidate_evaluations=7 must be rejected")
            self.assertIn("candidate_evaluations", v_diag)

            # 2. Mutate: inject raw_emissions = 7
            d_re = copy.deepcopy(res)
            d_re["steps"][2]["step_counters"] = {"raw_emissions": 7}
            d_re["counters"]["raw_emissions"] = d_re["counters"].get("raw_emissions", 0) + 7
            v_ok, v_diag = runner_mod.validate_native_output_schema(d_re, eng, wl["case_id"], is_success=False, workload=wl)
            self.assertFalse(v_ok, "UNATTEMPTED step with raw_emissions=7 must be rejected")

            # 3. Mutate: inject empty step_counters = {}
            d_em = copy.deepcopy(res)
            d_em["steps"][2]["step_counters"] = {}
            v_ok, v_diag = runner_mod.validate_native_output_schema(d_em, eng, wl["case_id"], is_success=False, workload=wl)
            self.assertFalse(v_ok, "UNATTEMPTED step with empty step_counters must be rejected under Option A")
            self.assertIn("must not contain 'step_counters'", v_diag)

    # =========================================================================
    # Group X: Final Acceptance Verification & Seam Tests (FC-03, FC-04)
    # =========================================================================

    def test_native_empty_frontier_invariant_witness(self):
        """FC-03: Verifies native C++ empty frontier invariant witness via --test-empty-frontier.
        Asserts positive control passes, artificial empty frontier throws InvariantViolationException,
        state integrity is preserved, and D-R public guard rejects k >= lambda(Q) pre-search."""
        # 1. Native C++ seam execution
        proc = subprocess.run(
            [str(self.binary_path), "--test-empty-frontier"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT)
        )
        self.assertEqual(proc.returncode, 0, f"Native empty frontier test failed: {proc.stderr}")
        self.assertIn('"test": "native_empty_frontier_invariant"', proc.stdout)
        self.assertIn('"status": "PASS"', proc.stdout)
        self.assertIn('"positive_control": "PASS"', proc.stdout)
        self.assertIn('"negative_control": "PASS"', proc.stdout)
        self.assertIn('"state_integrity": "PASS"', proc.stdout)

        # 2. D-R isolation: public workload with k >= lambda(Q) rejected at domain preflight (ExitCode 3)
        cases = runner_mod.build_hand_workloads()
        c21 = [c for c in cases if c["case_id"] == "CASE_HAND_21"][0]
        res, _, _ = run_workload_on_binary(self.binary_path, "m2", c21)
        self.assertEqual(res.get("exit_code"), 3, "CASE_HAND_21 with k >= lambda(Q) must exit with code 3 (DOMAIN_ERROR)")
        self.assertEqual(res.get("error_category"), "DOMAIN_ERROR")

    def test_m2_fault_isolation_after_missing_commitment(self):
        """FC-04: Verifies fault isolation immediately after MISSING commitment in M2 DFS.
        Positive control: CASE_HAND_15 executes with missing_branches > 0, producing 8 entered tuples.
        Faulted run: injects AFTER_MISSING_COMMITMENT fault hook ->
                     transaction aborts, 0 tuples committed, graph restored, codec restored,
                     honest work counters recorded."""
        cases = runner_mod.build_hand_workloads()
        c15 = [c for c in cases if c["case_id"] == "CASE_HAND_15"][0]

        # Positive control
        res_pos, _, _ = run_workload_on_binary(self.binary_path, "m2", c15)
        self.assertEqual(res_pos.get("exit_code"), 0)
        self.assertEqual(res_pos.get("status"), "SUCCESS")
        self.assertGreater(res_pos.get("counters", {}).get("missing_branches", 0), 0)
        self.assertEqual(len(res_pos["steps"][0]["entered_tuples"]), 8)

        # Faulted run
        c15_fault = copy.deepcopy(c15)
        c15_fault["updates"][0]["fault"] = "AFTER_MISSING_COMMITMENT"
        res_fault, _, _ = run_workload_on_binary(self.binary_path, "m2", c15_fault)
        self.assertEqual(res_fault.get("status"), "FAIL")
        self.assertEqual(res_fault.get("exit_code"), 6)
        self.assertEqual(res_fault.get("error_category"), "FAULT_INJECTED")
        self.assertTrue(res_fault.get("schema_valid"))

        step = res_fault["steps"][0]
        self.assertEqual(step["status"], "FAULT_INJECTED")
        self.assertEqual(step["commit_status"], "NOT_COMMITTED")
        self.assertIn("Simulated fault injected AFTER_MISSING_COMMITMENT", step["error"])
        self.assertEqual(len(step["entered_tuples"]), 0)
        self.assertEqual(step["graph_state"]["edges_count"], 2)  # Restored to 2 edges
        self.assertEqual(step["step_counters"]["missing_branches"], 1)

    def test_m2_fault_isolation_after_vertex_assignment(self):
        """FC-04: Verifies fault isolation immediately after vertex assignment in M2 DFS.
        Injects AFTER_VERTEX_ASSIGNMENT fault hook ->
        transaction aborts, 0 tuples committed, graph restored, codec restored."""
        cases = runner_mod.build_hand_workloads()
        c15 = [c for c in cases if c["case_id"] == "CASE_HAND_15"][0]

        c15_fault = copy.deepcopy(c15)
        c15_fault["updates"][0]["fault"] = "AFTER_VERTEX_ASSIGNMENT"
        res_fault, _, _ = run_workload_on_binary(self.binary_path, "m2", c15_fault)
        self.assertEqual(res_fault.get("status"), "FAIL")
        self.assertEqual(res_fault.get("exit_code"), 6)
        self.assertEqual(res_fault.get("error_category"), "FAULT_INJECTED")
        self.assertTrue(res_fault.get("schema_valid"))

        step = res_fault["steps"][0]
        self.assertEqual(step["status"], "FAULT_INJECTED")
        self.assertEqual(step["commit_status"], "NOT_COMMITTED")
        self.assertIn("Simulated fault injected AFTER_VERTEX_ASSIGNMENT", step["error"])
        self.assertEqual(len(step["entered_tuples"]), 0)
        self.assertEqual(step["graph_state"]["edges_count"], 2)  # Restored to 2 edges
        self.assertEqual(step["step_counters"]["anchors_started"], 1)

    def test_m2_recovery_lifecycle_after_missing_commitment_fault(self):
        """FC-04: Verifies full transaction lifecycle recovery on the SAME engine instance after MISSING commitment fault:
        Step 0: Valid transaction commits -> graph has 2 edges, 0 entered.
        Step 1: Active transaction enters MISSING branch and aborts (AFTER_MISSING_COMMITMENT fault) ->
                rolls back graph, restores codec, discards staged tuples, commit_status NOT_COMMITTED.
        Step 2: Subsequent valid transaction on SAME engine instance without reinitialization ->
                successfully applies mutation, computes 8 entered tuples matching DStar Oracle, and commits!"""
        cases = runner_mod.build_hand_workloads()
        c15 = [c for c in cases if c["case_id"] == "CASE_HAND_15"][0]
        wl = {
            "case_id": "test_m2_recovery_lifecycle",
            "query": c15["query"],
            "k": c15["k"],
            "initial_graph": {
                "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
                "edges": [{"u": 10, "v": 20, "label": 0}]
            },
            "continue_on_error": True,
            "updates": [
                {"op": "+", "u": 20, "v": 30, "label": 0},
                {"op": "+", "u": 30, "v": 40, "label": 0, "fault": "AFTER_MISSING_COMMITMENT"},
                {"op": "+", "u": 30, "v": 40, "label": 0}
            ]
        }
        res, _, _ = run_workload_on_binary(self.binary_path, "m2", wl)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("exit_code"), 6)
        exec_info = res.get("execution", {})
        self.assertEqual(exec_info.get("total_steps"), 3)
        self.assertEqual(exec_info.get("completed_steps"), 2)
        self.assertEqual(exec_info.get("failed_steps"), 1)

        s0, s1, s2 = res["steps"]
        self.assertEqual(s0["status"], "SUCCESS")
        self.assertEqual(s0["commit_status"], "COMMITTED")
        self.assertEqual(s0["graph_state"]["edges_count"], 2)

        self.assertEqual(s1["status"], "FAULT_INJECTED")
        self.assertEqual(s1["commit_status"], "NOT_COMMITTED")
        self.assertEqual(s1["graph_state"]["edges_count"], 2)  # Rolled back
        self.assertEqual(len(s1["entered_tuples"]), 0)

        self.assertEqual(s2["status"], "SUCCESS")
        self.assertEqual(s2["commit_status"], "COMMITTED")
        self.assertEqual(s2["graph_state"]["edges_count"], 3)
        self.assertEqual(len(s2["entered_tuples"]), 8)

    def test_m2_dfs_sibling_rollback_isolation(self):
        """FC-04: Verifies DFS sibling branch rollback isolation in M2 (CASE_HAND_16).
        M2 commits MISSING on chord, extension encounters chord presence in data graph, branch rolls back commitments,
        and sibling branches continue exploring to find all 16 automorphisms without contamination."""
        cases = runner_mod.build_hand_workloads()
        c16 = [c for c in cases if c["case_id"] == "CASE_HAND_16"][0]
        res, _, _ = run_workload_on_binary(self.binary_path, "m2", c16)
        self.assertEqual(res.get("status"), "SUCCESS")
        self.assertEqual(res.get("exit_code"), 0)
        counters = res.get("counters", {})
        self.assertGreater(counters.get("commitment_checks", 0), 0)
        self.assertGreater(counters.get("missing_branches", 0), 0)
        self.assertEqual(len(res["steps"][0]["entered_tuples"]), 16)
        self.assertEqual(counters.get("raw_emissions"), 16)
        self.assertEqual(counters.get("committed_emissions"), 16)
        self.assertEqual(counters.get("discarded_emissions"), 0)

    def test_work_limit_exhaustion_approximate_path(self):
        """FC-04: Verifies work-limit exhaustion on approximate path (k=1) with genuine search.
        Workload CASE_HAND_15 (108 work units) executed with max_work=10 ->
        aborts with exit code 4 (WORK_BUDGET_EXCEEDED), 0 partial tuples committed,
        graph state rolled back, honest work units reported (work_units=11 > max_work=10)."""
        cases = runner_mod.build_hand_workloads()
        c15 = [c for c in cases if c["case_id"] == "CASE_HAND_15"][0]
        c15_lim = copy.deepcopy(c15)
        c15_lim["max_work"] = 10
        res, _, _ = run_workload_on_binary(self.binary_path, "m2", c15_lim)
        self.assertEqual(res.get("exit_code"), 4, "Expected exit code 4 (WORK_BUDGET_EXCEEDED)")
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error_category"), "WORK_BUDGET_EXCEEDED")
        self.assertTrue(res.get("schema_valid"))

        step = res["steps"][0]
        self.assertEqual(step["status"], "CALLBACK_ABORTED")
        self.assertEqual(step["commit_status"], "NOT_COMMITTED")
        self.assertEqual(len(step["entered_tuples"]), 0)
        self.assertEqual(step["graph_state"]["edges_count"], 2)  # Rolled back to 2 edges
        self.assertEqual(res["counters"]["work_units"], 11)
        self.assertEqual(res["work_budget"]["effective_limit"], 10)

    def test_m1_lazy_variant_generation_k7_k5_preemption(self):
        """Issue #34 Part B: K7, k=5 with --max-work 1 must halt at variant 1, not building all 15,504 plans."""
        query_vertices = [{"id": 0, "label": 10}, {"id": 1, "label": 20}] + [{"id": i, "label": 0} for i in range(2, 7)]
        edges = []
        for i in range(7):
            for j in range(i + 1, 7):
                edges.append({"u": i, "v": j, "label": 0})

        workload = {
            "case_id": "K7_K5_LAZY_BUDGET",
            "k": 5,
            "query": {"vertices": query_vertices, "edges": edges},
            "initial_graph": {"vertices": query_vertices, "edges": []},
            "updates": [{"op": "+", "u": 0, "v": 1, "label": 0}]
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_in, \
             tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_out:
            json.dump(workload, f_in)
            in_path = f_in.name
            out_path = f_out.name

        try:
            # 1. max-work 1: preemption occurs during combination traversal BEFORE constructing plan 1 (variants_started == 0)
            cmd1 = [str(self.binary_path), "--workload", in_path, "--output", out_path, "--engine", "m1", "--max-work", "1"]
            proc1 = subprocess.run(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc1.returncode, 4, "Expected exit code 4 (WORK_BUDGET_EXCEEDED)")

            with open(out_path, "r", encoding="utf-8") as f:
                res1 = json.load(f)

            self.assertEqual(res1.get("status"), "FAIL")
            self.assertEqual(res1.get("error_category"), "WORK_BUDGET_EXCEEDED")
            step1 = res1["steps"][0]
            self.assertEqual(step1["status"], "CALLBACK_ABORTED")
            self.assertEqual(step1["commit_status"], "NOT_COMMITTED")
            self.assertEqual(len(step1["entered_tuples"]), 0)
            self.assertEqual(step1["step_counters"]["variants_started"], 0)
            self.assertEqual(step1["step_counters"]["work_units"], 2)

            # 2. max-work 7: preemption occurs after constructing plan 1 and BEFORE plan 2 (variants_started == 1)
            cmd2 = [str(self.binary_path), "--workload", in_path, "--output", out_path, "--engine", "m1", "--max-work", "7"]
            proc2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc2.returncode, 4, "Expected exit code 4 (WORK_BUDGET_EXCEEDED)")

            with open(out_path, "r", encoding="utf-8") as f:
                res2 = json.load(f)

            self.assertEqual(res2.get("status"), "FAIL")
            self.assertEqual(res2.get("error_category"), "WORK_BUDGET_EXCEEDED")
            step2 = res2["steps"][0]
            self.assertEqual(step2["status"], "CALLBACK_ABORTED")
            self.assertEqual(step2["commit_status"], "NOT_COMMITTED")
            self.assertEqual(len(step2["entered_tuples"]), 0)
            self.assertEqual(step2["step_counters"]["variants_started"], 1)
            self.assertEqual(step2["step_counters"]["work_units"], 8)
        finally:
            for p in [in_path, out_path]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_m1_exact_work_threshold_n_and_n_minus_1(self):
        """Issue #34 Part B: Sharp threshold test on fixed fixture P3: N=10 succeeds, N-1=9 fails."""
        workload = {
            "case_id": "P3_THRESHOLD_TEST",
            "query": {
                "k": 0,
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "updates": [{"op": "+", "u": 1, "v": 2, "label": 0}]
        }

        # Run with max_work = 10 (exact N) -> must succeed
        res_pass, _, _ = run_workload_on_binary(self.binary_path, "m1", workload, max_work=10)
        self.assertEqual(res_pass.get("exit_code"), 0)
        self.assertEqual(res_pass.get("status"), "SUCCESS")
        self.assertEqual(res_pass["steps"][0]["commit_status"], "COMMITTED")
        self.assertEqual(res_pass["steps"][0]["step_counters"]["work_units"], 10)

        # Run with max_work = 9 (N - 1) -> must fail with WORK_BUDGET_EXCEEDED
        res_fail, _, _ = run_workload_on_binary(self.binary_path, "m1", workload, max_work=9)
        self.assertEqual(res_fail.get("exit_code"), 4)
        self.assertEqual(res_fail.get("status"), "FAIL")
        self.assertEqual(res_fail.get("error_category"), "WORK_BUDGET_EXCEEDED")
        self.assertEqual(res_fail["steps"][0]["commit_status"], "NOT_COMMITTED")

    def test_m1_staged_tuples_rollback_continue_on_error_insert_and_delete(self):
        """Issue #34 Part B: Rollback of staged emissions on budget exhaustion with --continue-on-error.

        Evaluates reviewer fixture for both INSERT and DELETE directions:
        - Query: Triangle (homolabeled).
        - Graph part 1: two triangles sharing edge (0,1) -> (0,1,2) and (0,1,3).
        - Graph part 2: triangle (4,5,6) completed in Step 0 (commits 6 tuples).
        - Step 1: inserts/deletes (0,1) with max_work N-1=23, exhausting budget after staging raw tuples.
        - Step 2: inserts (2,7) with string label "0" (recovery under --continue-on-error).
        """
        for op in ("+", "-"):
            is_insert = (op == "+")
            init_edges = [
                {"u": 0, "v": 2, "label": 0}, {"u": 1, "v": 2, "label": 0},
                {"u": 0, "v": 3, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 4, "v": 5, "label": 0}, {"u": 5, "v": 6, "label": 0}
            ]
            if not is_insert:
                init_edges.insert(0, {"u": 0, "v": 1, "label": 0})

            workload = {
                "case_id": f"TEST_ROLLBACK_{'INSERT' if is_insert else 'DELETE'}",
                "k": 0,
                "query": {
                    "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}],
                    "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0}, {"u": 0, "v": 2, "label": 0}]
                },
                "initial_graph": {
                    "vertices": [{"id": v, "label": 0} for v in range(8)],
                    "edges": init_edges
                },
                "updates": [
                    {"op": "+", "u": 4, "v": 6, "label": 0},
                    {"op": op, "u": 0, "v": 1, "label": 0},
                    {"op": "+", "u": 2, "v": 7, "label": "0"}
                ]
            }

            # Baseline skipping Step 1
            wl_baseline = copy.deepcopy(workload)
            wl_baseline["updates"] = [
                {"op": "+", "u": 4, "v": 6, "label": 0},
                {"op": "+", "u": 2, "v": 7, "label": "0"}
            ]

            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_in, \
                 tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_out, \
                 tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_b_in, \
                 tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_b_out:
                json.dump(workload, f_in)
                json.dump(wl_baseline, f_b_in)
                in_path, out_path = f_in.name, f_out.name
                b_in_path, b_out_path = f_b_in.name, f_b_out.name

            try:
                # Run with N-1 = 23 (budget exhausted after staging 11 emissions)
                cmd = [str(self.binary_path), "--workload", in_path, "--output", out_path, "--engine", "m1", "--max-work", "23", "--continue-on-error"]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(proc.returncode, 4, f"Overall exit code must be 4 on budget error ({op})")

                # Run baseline
                cmd_b = [str(self.binary_path), "--workload", b_in_path, "--output", b_out_path, "--engine", "m1", "--max-work", "1000"]
                subprocess.run(cmd_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                with open(out_path, "r", encoding="utf-8") as f: res = json.load(f)
                with open(b_out_path, "r", encoding="utf-8") as f: res_b = json.load(f)

                s0 = res["steps"][0]
                s1 = res["steps"][1]
                s2 = res["steps"][2]
                s_b1 = res_b["steps"][1]

                # Step 0: completed triangle (4,5,6) committed 6 tuples
                self.assertEqual(s0["status"], "SUCCESS")
                self.assertEqual(s0["commit_status"], "COMMITTED")
                self.assertEqual(len(s0["entered_tuples"]), 6)

                # Step 1: aborted on budget after staging tuples
                self.assertEqual(s1["status"], "CALLBACK_ABORTED")
                self.assertEqual(s1["error_category"], "WORK_BUDGET_EXCEEDED")
                self.assertEqual(s1["commit_status"], "NOT_COMMITTED")
                self.assertEqual(s1["entered_tuples"], [])
                self.assertEqual(s1["exited_tuples"], [])

                sc1 = s1["step_counters"]
                self.assertGreater(sc1["raw_emissions"], 0, "Must have staged emissions before budget exhaustion")
                self.assertEqual(sc1["discarded_emissions"], sc1["raw_emissions"], "All staged emissions must be discarded on rollback")
                self.assertEqual(sc1["committed_emissions"], 0, "Committed emissions must be zero on aborted step")

                # Graph and codec state before and after step 1 on the same instance
                self.assertEqual(s1["graph_state"], s0["graph_state"], "Graph state after abort must match pre-step state")
                self.assertEqual(s1["codec_state"], s0["codec_state"], "Codec state after abort must match pre-step state")

                # Step 2: recovery step executed under --continue-on-error
                self.assertEqual(s2["status"], "SUCCESS")
                self.assertEqual(s2["commit_status"], "COMMITTED")
                self.assertEqual(s2["graph_state"], s_b1["graph_state"], "Recovered graph state must match baseline run skipping step 1")
                self.assertEqual(s2["codec_state"], s_b1["codec_state"], "Recovered codec state must match baseline run skipping step 1")
            finally:
                for p in [in_path, out_path, b_in_path, b_out_path]:
                    if os.path.exists(p):
                        os.unlink(p)

    def test_occurrence_witnesses_triangle_diamond_k2(self):
        """Issue #34 Part D: Explicit occurrence witness validation across M1 and M2.

        Witnesses:
          1. Triangle k=0: INSERT gives 6 ENTERED, DELETE gives 6 EXITED.
          2. Diamond chordal cycle k=1: 1 ENTERED, 0, 0, 1 EXITED.
          3. K2 k=0: single-edge query gives exactly 2 mappings (both orientations).
        """
        # 1. Triangle k=0: all 6 permutations of (0, 1, 2)
        expected_tri_tuples = sorted([
            [0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]
        ])
        tri_wl = {
            "case_id": "WITNESS_TRIANGLE_K0",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0}, {"u": 0, "v": 2, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}, {"id": 2, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}, {"u": 1, "v": 2, "label": 0}]
            },
            "updates": [
                {"op": "+", "u": 0, "v": 2, "label": 0},
                {"op": "-", "u": 0, "v": 2, "label": 0}
            ]
        }
        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, tri_wl)
            self.assertTrue(ok, f"Triangle k=0 failed on {eng}: {res.get('error_message')}")
            steps = res.get("steps", [])
            self.assertEqual(len(steps), 2)

            # Step 0: INSERT -> exactly 6 ENTERED matching all permutations, 0 EXITED
            s0_entered = sorted(steps[0]["entered_tuples"])
            self.assertEqual(s0_entered, expected_tri_tuples)
            self.assertEqual(len(steps[0]["entered_tuples"]), len(set(tuple(t) for t in steps[0]["entered_tuples"])), "Zero duplicates allowed")
            self.assertEqual(len(steps[0]["exited_tuples"]), 0)

            # Step 1: DELETE -> 0 ENTERED, exactly 6 EXITED matching all permutations
            self.assertEqual(len(steps[1]["entered_tuples"]), 0)
            s1_exited = sorted(steps[1]["exited_tuples"])
            self.assertEqual(s1_exited, expected_tri_tuples)
            self.assertEqual(len(steps[1]["exited_tuples"]), len(set(tuple(t) for t in steps[1]["exited_tuples"])), "Zero duplicates allowed")

        # 2. Diamond chordal cycle k=1 (from witness_01_diamond_k1 fixture)
        witness_fixture = REPO_ROOT / "tests" / "fixtures" / "dstar03" / "workloads" / "witness_01_diamond_k1.json"
        with open(witness_fixture, "r", encoding="utf-8") as f:
            d = json.load(f)
        diamond_wl = {
            "case_id": "WITNESS_DIAMOND_CHORDAL_CYCLE_K1",
            "k": 1,
            "query": {
                "vertices": [{"id": int(v), "label": lbl} for v, lbl in d["query"]["vertices"].items()],
                "edges": [{"u": u, "v": v, "label": lbl} for u, v, lbl in d["query"]["edges"]]
            },
            "initial_graph": {
                "vertices": [{"id": int(v), "label": lbl} for v, lbl in d["initial_graph"]["vertices"].items()],
                "edges": [{"u": u, "v": v, "label": lbl} for u, v, lbl in d["initial_graph"]["edges"]]
            },
            "updates": [
                {"op": "+" if s["operation"] == "INSERT" else "-", "u": s["edge"][0], "v": s["edge"][1], "label": s["label"]}
                for s in d["stream"]
            ]
        }
        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, diamond_wl)
            self.assertTrue(ok, f"Diamond chordal cycle failed on {eng}: {res.get('error_message')}")
            steps = res.get("steps", [])
            self.assertEqual(len(steps), 4)
            # Step 0: 1 ENTERED, 0 EXITED
            self.assertEqual(steps[0]["entered_tuples"], [[0, 1, 2, 3]])
            self.assertEqual(steps[0]["exited_tuples"], [])
            # Steps 1-2: Spectator updates -> delta strictly empty
            self.assertEqual(steps[1]["entered_tuples"], [])
            self.assertEqual(steps[1]["exited_tuples"], [])
            self.assertEqual(steps[2]["entered_tuples"], [])
            self.assertEqual(steps[2]["exited_tuples"], [])
            # Step 3: 0 ENTERED, 1 EXITED
            self.assertEqual(steps[3]["entered_tuples"], [])
            self.assertEqual(steps[3]["exited_tuples"], [[0, 1, 2, 3]])

        # 3. K2 k=0 (homolabeled: 2 vertex permutations)
        expected_k2_tuples = sorted([[10, 20], [20, 10]])
        k2_wl = {
            "case_id": "WITNESS_K2_K0",
            "k": 0,
            "query": {
                "vertices": [{"id": 0, "label": 0}, {"id": 1, "label": 0}],
                "edges": [{"u": 0, "v": 1, "label": 0}]
            },
            "initial_graph": {
                "vertices": [{"id": 10, "label": 0}, {"id": 20, "label": 0}],
                "edges": []
            },
            "updates": [
                {"op": "+", "u": 10, "v": 20, "label": 0},
                {"op": "-", "u": 10, "v": 20, "label": 0}
            ]
        }
        for eng in ("m1", "m2"):
            res, _, ok = run_workload_on_binary(self.binary_path, eng, k2_wl)
            self.assertTrue(ok, f"K2 k=0 failed on {eng}: {res.get('error_message')}")
            steps = res.get("steps", [])
            self.assertEqual(len(steps), 2)
            # Step 0: INSERT -> 2 ENTERED (both orientations), 0 EXITED
            self.assertEqual(sorted(steps[0]["entered_tuples"]), expected_k2_tuples)
            self.assertEqual(len(steps[0]["entered_tuples"]), len(set(tuple(t) for t in steps[0]["entered_tuples"])), "Zero duplicates allowed")
            self.assertEqual(steps[0]["exited_tuples"], [])
            # Step 1: DELETE -> 0 ENTERED, 2 EXITED (both orientations)
            self.assertEqual(steps[1]["entered_tuples"], [])
            self.assertEqual(sorted(steps[1]["exited_tuples"]), expected_k2_tuples)
            self.assertEqual(len(steps[1]["exited_tuples"]), len(set(tuple(t) for t in steps[1]["exited_tuples"])), "Zero duplicates allowed")


if __name__ == "__main__":
    unittest.main()
