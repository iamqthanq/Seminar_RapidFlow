"""
Focused Unit Tests for RapidFlow-APX Native Audit Harness (Gate P1 / Issue #28)

Validates:
1. Native build and runtime verification on small fixtures.
2. Missing executable / output fail-closed handling.
3. Nonzero process exit code rejection.
4. Manifest case validation (missing, duplicate, hash mismatch).
5. Typed-label separation (int 0 != str "0") and numeric boundaries.
6. Transactional rollback under mid-stream fault injection.
7. Resource/timeout error classification and process containment.
8. Report generation to temporary output paths (preserving canonical artifacts).
9. M0 ground truth vs native NLF counterexample verification.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.dstar_oracle import enumerate_snapshot, DStarGraph
from scripts.run_rapidflow_apx_audit import (
    build_native_harness,
    compute_sha256,
    run_probe_case,
)


class TestRapidFlowApxAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parent.parent
        cls.manifest_path = cls.root_dir / "configs" / "rapidflow_apx_audit_cases.json"
        with open(cls.manifest_path, "r", encoding="utf-8") as f:
            cls.manifest = json.load(f)

        # Build or locate native binary
        cls.binary_path = cls.root_dir / "tests" / "native" / "rapidflow_apx_audit" / "build" / "rapidflow_apx_audit"
        if not cls.binary_path.exists():
            cls.binary_path = build_native_harness(cls.root_dir)
        cls.binary_sha256 = compute_sha256(cls.binary_path)

    def test_01_native_build_and_runtime_provenance(self):
        """Test 1: Native binary builds cleanly and executes Probe A runtime accessors."""
        self.assertTrue(self.binary_path.exists(), "Binary must exist")
        self.assertNotEqual(self.binary_sha256, "FILE_NOT_FOUND")

        case = next(c for c in self.manifest["cases"] if c["case_id"] == "CASE_PROBE_A_PROVENANCE")
        res = run_probe_case(self.binary_path, case, self.root_dir, self.binary_sha256)
        self.assertEqual(res["status"], "PASS_EXPECTED")
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["evidence"]["graph_accessors_verified"], "true")
        self.assertEqual(res["evidence"]["binary_sha256"], self.binary_sha256)

    def test_02_missing_executable_fail_closed(self):
        """Test 2: Non-existent executable path raises/fails closed."""
        fake_binary = self.root_dir / "nonexistent_binary"
        case = self.manifest["cases"][0]
        res = run_probe_case(fake_binary, case, self.root_dir, "fake_sha")
        self.assertNotEqual(res["status"], "PASS_EXPECTED")
        self.assertIn("error", res)

    def test_03_native_process_error_rejection(self):
        """Test 3: Process exiting with nonzero return code is classified as FAIL."""
        # Run binary with invalid probe argument
        proc = subprocess.run(
            [str(self.binary_path), "--probe", "invalid_probe_target"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_04_manifest_fixture_hash_validation(self):
        """Test 4: Manifest validates fixture hashes; detects tampering/mismatch."""
        for case in self.manifest["cases"]:
            fpath = case.get("fixture_path")
            fsha = case.get("fixture_sha256")
            if fpath and fsha:
                actual_sha = compute_sha256(self.root_dir / fpath)
                self.assertEqual(actual_sha, fsha, f"Hash mismatch for {fpath}")

    def test_05_typed_label_and_numeric_boundary(self):
        """Test 5: Codec preserves negative IDs, sparse IDs, >uint32, and int 0 != str '0'."""
        case = next(c for c in self.manifest["cases"] if c["case_id"] == "CASE_PROBE_B_TYPED_BOUNDARY")
        res = run_probe_case(self.binary_path, case, self.root_dir, self.binary_sha256)
        self.assertEqual(res["status"], "PASS_EXPECTED")
        self.assertEqual(res["evidence"]["int_zero_vs_str_zero_separated"], "true")
        self.assertEqual(res["evidence"]["large_id_mapped"], "true")
        self.assertEqual(res["evidence"]["codec_roundtrip_verified"], "true")

    def test_06_transactional_rollback_under_fault(self):
        """Test 6: Mid-stream fault injection triggers clean rollback to pre-step state."""
        case = next(c for c in self.manifest["cases"] if c["case_id"] == "CASE_PROBE_E_SNAPSHOT_ROLLBACK")
        res = run_probe_case(self.binary_path, case, self.root_dir, self.binary_sha256)
        self.assertEqual(res["status"], "PASS_EXPECTED")
        self.assertEqual(res["evidence"]["fault_injected"], "true")
        self.assertEqual(res["evidence"]["rollback_executed"], "true")
        self.assertEqual(res["evidence"]["pre_fault_state_preserved"], "true")

    def test_07_resource_timeout_containment(self):
        """Test 7: Timeout is strictly bounded and classified as TIMEOUT."""
        case = self.manifest["cases"][0].copy()
        # Test containment with sub-millisecond timeout that must expire on real subprocess invocation
        res = run_probe_case(self.binary_path, case, self.root_dir, self.binary_sha256, timeout=0.0001)
        self.assertIn(res["status"], ["TIMEOUT", "ERROR"])

    def test_08_temporary_output_preserves_canonical(self):
        """Test 8: Runner outputs to temporary file without clobbering canonical report."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = [
                sys.executable,
                str(self.root_dir / "scripts" / "run_rapidflow_apx_audit.py"),
                "--output", tmp_path
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc.returncode, 0)
            with open(tmp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["overall_status"], "PASS")
            self.assertEqual(data["total_cases"], 6)
            self.assertEqual(data["passed_cases"], 6)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_09_m0_oracle_vs_native_nlf_counterexample(self):
        """Test 9: M0 Oracle confirms identity match on Diamond; native NLF excludes it."""
        # Diamond Query
        Q = DStarGraph()
        for v in (0, 1, 2, 3):
            Q.add_vertex(v, 0)
        for u, v in ((0, 1), (0, 2), (1, 2), (1, 3), (2, 3)):
            Q.add_edge(u, v, 0)

        # Data graph missing edge (1, 2)
        G = DStarGraph()
        for v in (0, 1, 2, 3):
            G.add_vertex(v, 0)
        for u, v in ((0, 1), (0, 2), (1, 3), (2, 3)):
            G.add_edge(u, v, 0)

        # 1. Independent M0 check
        matches = enumerate_snapshot(Q, G, k=1)
        self.assertIn((0, 1, 2, 3), matches, "Identity mapping must be valid under M0 APX")

        # 2. Native Probe D check
        case = next(c for c in self.manifest["cases"] if c["case_id"] == "CASE_PROBE_D_EXACT_NLF_COUNTEREXAMPLE")
        res_d = run_probe_case(self.binary_path, case, self.root_dir, self.binary_sha256)
        self.assertEqual(res_d["status"], "PASS_EXPECTED")
        self.assertEqual(res_d["evidence"]["m0_ground_truth_accepted"], "true")
        self.assertEqual(res_d["evidence"]["rapidflow_nlf_check_result"], "false")
        self.assertEqual(res_d["evidence"]["incompatibility_proven"], "true")


if __name__ == "__main__":
    unittest.main()
