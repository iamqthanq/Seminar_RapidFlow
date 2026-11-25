import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_rapidflow_apx_p4b_manifest as wlgen
from scripts import run_rapidflow_apx_p4b as p4b_runner
from scripts.p4a_evidence_contract import APPROVED_P4A_MANIFEST_SHA256, compute_dict_sha256, compute_sha256


class TestP4bWorkloadFreezeAndSchedule(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = REPO_ROOT / "configs" / "rapidflow_apx_p4b_manifest.json"
        cls.protocol_path = REPO_ROOT / "configs" / "rapidflow_apx_p4b_protocol.json"
        cls.schedule_path = REPO_ROOT / "configs" / "rapidflow_apx_p4b_schedule.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.protocol = json.loads(cls.protocol_path.read_text(encoding="utf-8"))
        cls.schedule = json.loads(cls.schedule_path.read_text(encoding="utf-8"))

    def test_01_fifty_unique_workloads_and_exact_suite_counts(self) -> None:
        workloads = self.manifest["workloads"]
        self.assertEqual(len(workloads), 50)
        self.assertEqual(self.manifest["continuity_count"], 30)
        self.assertEqual(self.manifest["discrimination_count"], 20)
        wids = [w["workload_id"] for w in workloads]
        self.assertEqual(len(set(wids)), 50)
        cont = [w for w in workloads if w["suite"] == "continuity"]
        disc = [w for w in workloads if w["suite"] == "discrimination"]
        self.assertEqual(len(cont), 30)
        self.assertEqual(len(disc), 20)

    def test_02_continuity_preserves_exact_p4a_semantics_and_hashes(self) -> None:
        p4a_path = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
        self.assertEqual(compute_sha256(p4a_path), APPROVED_P4A_MANIFEST_SHA256)
        p4a_cases = {c["case_id"]: c for c in json.loads(p4a_path.read_text(encoding="utf-8"))["cases"]}
        for entry in self.manifest["workloads"]:
            if entry["suite"] != "continuity":
                continue
            fpath = REPO_ROOT / entry["fixture_path"]
            raw = fpath.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["fixture_sha256"])
            self.assertEqual(len(raw), entry["fixture_size_bytes"])
            wl = json.loads(raw.decode("utf-8"))
            hist = p4a_cases[entry["historical_case_id"]]
            self.assertEqual(wl, hist)
            self.assertEqual(compute_dict_sha256(wl), entry["historical_workload_dict_sha256"])

    def test_03_independent_query_edge_connectivity_and_dr_contract(self) -> None:
        qv = wlgen.verify_all_query_topologies()
        expected = {"triangle": 2, "c4": 2, "diamond": 2, "house": 2, "k4": 3, "triangular_prism": 3}
        for name, exp_lam in expected.items():
            self.assertEqual(qv[name]["computed_lambda"], exp_lam)
            self.assertTrue(qv[name]["valid_dr_for_k0"])
            self.assertTrue(qv[name]["valid_dr_for_k1"])

        for entry in self.manifest["workloads"]:
            fpath = REPO_ROOT / entry["fixture_path"]
            wl = json.loads(fpath.read_text(encoding="utf-8"))
            summary = wlgen.validate_and_summarize_workload(wl)
            self.assertEqual(summary["query_edge_connectivity"], entry["query_edge_connectivity"])
            self.assertEqual(summary["insert_count"], entry["insert_count"])
            self.assertEqual(summary["delete_count"], entry["delete_count"])
            self.assertLess(entry["missing_budget_k"], entry["query_edge_connectivity"])
            self.assertGreaterEqual(entry["missing_budget_k"], 0)

    def test_04_matched_k_pair_invariance_and_random_regular_exact_degrees(self) -> None:
        disc_by_group = {}
        for entry in self.manifest["workloads"]:
            if entry["suite"] == "discrimination":
                disc_by_group.setdefault(entry["group_id"], {})[entry["missing_budget_k"]] = entry

        self.assertEqual(len(disc_by_group), 10)
        for gid, pair in disc_by_group.items():
            self.assertEqual(set(pair.keys()), {0, 1})
            w0 = json.loads((REPO_ROOT / pair[0]["fixture_path"]).read_text(encoding="utf-8"))
            w1 = json.loads((REPO_ROOT / pair[1]["fixture_path"]).read_text(encoding="utf-8"))
            for field in ("query", "initial_graph", "updates", "max_work", "regime", "topology", "query_type"):
                self.assertEqual(w0[field], w1[field], f"{gid}.{field} differed between k=0 and k=1")
            self.assertEqual(w0["k"], 0)
            self.assertEqual(w1["k"], 1)

        # Verify DISC-02 is strictly 4-regular on every vertex
        disc02 = json.loads((REPO_ROOT / disc_by_group["DISC-02"][0]["fixture_path"]).read_text(encoding="utf-8"))
        deg = {v["id"]: 0 for v in disc02["initial_graph"]["vertices"]}
        for e in disc02["initial_graph"]["edges"]:
            deg[e["u"]] += 1
            deg[e["v"]] += 1
        self.assertTrue(all(d == 4 for d in deg.values()), f"Non-regular DISC-02 degrees: {deg}")

    def test_05_deterministic_regeneration_independent_of_pythonhashseed(self) -> None:
        rep = wlgen.verify_independent_regeneration(
            self.manifest_path,
            REPO_ROOT / "tests" / "fixtures" / "p4b",
            REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json",
        )
        self.assertTrue(rep["verified"])
        self.assertEqual(rep["discrepancies"], 0)
        self.assertEqual(rep["total_workloads_compared"], 50)

    def test_06_balanced_williams_schedule_and_protocol_hashes(self) -> None:
        p4b_runner.verify_williams_sequences()
        val = p4b_runner.validate_schedule(self.schedule, self.manifest)
        self.assertEqual(val["primary_slots"], 1000)
        self.assertEqual(val["warmup_slots"], 200)
        self.assertEqual(val["diagnostic_slots"], 250)
        self.assertEqual(self.protocol["manifest_sha256"], compute_sha256(self.manifest_path))
        self.assertEqual(self.protocol["schedule_sha256"], compute_sha256(self.schedule_path))
        self.assertEqual(self.protocol["approval_state"], "PREREGISTRATION_CANDIDATE")
        self.assertFalse(self.protocol["execution_authorized"])

    def test_07_fail_closed_authorization_and_zero_launch_dry_run(self) -> None:
        auth_ok, msg = p4b_runner.check_execution_authorization(self.protocol)
        self.assertFalse(auth_ok)
        self.assertIn("MEASUREMENT_NOT_AUTHORIZED", msg)

        # CLI --execute-primary MUST exit non-zero in P4b-3A
        res_exec = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_rapidflow_apx_p4b.py"), "--execute-primary"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_exec.returncode, 2)
        self.assertIn("MEASUREMENT_NOT_AUTHORIZED", res_exec.stderr)

        # --dry-run MUST NOT call subprocess.Popen
        with patch.object(subprocess, "Popen", side_effect=AssertionError("Dry-run launched subprocess!")):
            res_dry = p4b_runner.run_dry_run_inspection(
                self.protocol_path,
                self.manifest_path,
                self.schedule_path,
                p4b_runner.DEFAULT_BINARY_PATH,
            )
        self.assertTrue(res_dry["dry_run"])
        self.assertEqual(res_dry["launched_subprocess_count"], 0)
        self.assertEqual(res_dry["planned_totals"]["primary_slots"], 1000)


if __name__ == "__main__":
    unittest.main()
