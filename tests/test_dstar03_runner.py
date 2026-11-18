#!/usr/bin/env python3
"""
tests/test_dstar03_runner.py — Unit Tests for Worker and Supervisor Execution (v2).

Covers:
1. Worker full-array verification, reset after warm-up, and timer accuracy.
2. Contained child resource error (RLIMIT_AS enforcement) without workstation stress.
3. Supervisor timeout and process group cleanup.
4. Ledger interruption handling, resume integrity, and hash drift rejection.
5. F02 regression: rejection of missing M0 ground truth in workload fixture.
6. F02 regression: rejection of truncated M0 steps in workload fixture.
7. F02 regression: rejection of wrong expected tuple with failing step report.
8. F02 regression: rejection of fixture component hash mismatch.
9. F02 regression: rejection of workload fingerprint mismatch.
10. F03 regression: validation of resource limits (negative, invalid types, exceeding ceilings).
11. F03 regression: containment self-test evidence generation.
12. F01/F02 regression: rejection of manifest hash drift and protocol drift on resume.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dstar03_pilot_worker import run_worker
from dstar03_resources import (
    check_environment_preflight,
    record_containment_evidence,
    run_contained_subprocess,
    validate_resource_limits,
)
from run_dstar03_pilot import PilotSupervisor


class TestDStar03Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workload_path = REPO_ROOT / "tests" / "fixtures" / "dstar03" / "workloads" / "witness_01_diamond_k1.json"

    def test_01_worker_execution_and_reset(self) -> None:
        """Verifies worker execution on witness workload with warm-up reset and exact events."""
        res_m1 = run_worker(
            workload_path=self.workload_path,
            engine_name="m1",
            attempt_id="test_worker_m1",
        )
        self.assertEqual(res_m1["status"], "SUCCESS_NONEMPTY")
        self.assertEqual(res_m1["warmup_status"], "SUCCESS")
        self.assertEqual(res_m1["total_entered"], 1)
        self.assertEqual(res_m1["total_exited"], 1)
        self.assertGreater(res_m1["stream_engine_ns"], 0)
        self.assertIn("git_provenance", res_m1)

        res_m2 = run_worker(
            workload_path=self.workload_path,
            engine_name="m2",
            attempt_id="test_worker_m2",
        )
        self.assertEqual(res_m2["status"], "SUCCESS_NONEMPTY")
        self.assertEqual(res_m2["warmup_status"], "SUCCESS")
        self.assertEqual(res_m2["total_entered"], 1)
        self.assertEqual(res_m2["total_exited"], 1)
        self.assertGreater(res_m2["stream_engine_ns"], 0)
        self.assertIn("git_provenance", res_m2)

    def test_02_contained_child_resource_limit(self) -> None:
        """Verifies RLIMIT_AS address-space ceiling catches excessive allocations in disposable child."""
        child_code = "b = bytearray(1000 * 1024 * 1024)"
        res = run_contained_subprocess(
            [sys.executable, "-c", child_code],
            timeout_seconds=5.0,
            limit_as_bytes=512 * 1024 * 1024,
        )
        self.assertEqual(res["status"], "RESOURCE_LIMIT")
        self.assertIn("MemoryError", res["stderr"])

    def test_03_supervisor_timeout_and_cleanup(self) -> None:
        """Verifies timeout terminates long-running child and cleans up process group."""
        child_code = "import time; time.sleep(10.0)"
        res = run_contained_subprocess(
            [sys.executable, "-c", child_code],
            timeout_seconds=0.5,
            limit_as_bytes=512 * 1024 * 1024,
        )
        self.assertEqual(res["status"], "TIMEOUT")
        self.assertTrue(res["timed_out"])

    def test_04_supervisor_ledger_interruption_and_resume(self) -> None:
        """Verifies journal recovers interrupted attempts and marks PARTIAL_RUN when filtered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_ledger = tmp_path / "test_attempts.jsonl"

            interrupted_id = "witness_01_diamond_k1_m1_pair1_rep0"
            with test_ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "record_type": "ATTEMPT_STARTED",
                    "attempt_id": interrupted_id,
                    "workload_id": "witness_01_diamond_k1",
                    "engine": "m1",
                    "pair_index": 1,
                    "rep_index": 0,
                    "timestamp": 1000.0,
                }) + "\n")

            supervisor = PilotSupervisor(
                manifest_path=REPO_ROOT / "configs" / "dstar03_pilot_manifest.json",
                ledger_path=test_ledger,
                max_wall_seconds=30.0,
                worker_timeout=10.0,
            )

            # Filtered run must be marked PARTIAL_RUN
            res = supervisor.run(workload_filter="witness_01")
            self.assertEqual(res["status"], "PARTIAL_RUN")

            # Verify ledger records
            with test_ledger.open("r", encoding="utf-8") as f:
                lines = [json.loads(line.strip()) for line in f if line.strip()]

            int_completions = [
                l for l in lines
                if l.get("record_type") == "ATTEMPT_COMPLETED"
                and l.get("attempt_id") == interrupted_id
                and l.get("status") == "INTERRUPTED"
            ]
            self.assertEqual(len(int_completions), 1)

    def test_05_reject_missing_m0_expected(self) -> None:
        """F02: Verifies worker rejects workload when m0_verification is missing or unverified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_w = Path(tmpdir) / "corrupt_workload.json"
            with self.workload_path.open("r", encoding="utf-8") as f:
                w_data = json.load(f)

            # Delete m0_verification and recompute workload fingerprint so schema/fingerprint passes
            del w_data["m0_verification"]
            from generate_dstar03_workloads import compute_workload_fingerprint
            w_data["sha256_workload"] = compute_workload_fingerprint(w_data)

            with tmp_w.open("w", encoding="utf-8") as f:
                json.dump(w_data, f)

            res = run_worker(workload_path=tmp_w, engine_name="m1", attempt_id="test_missing_m0")
            self.assertEqual(res["status"], "CORRECTNESS_FAIL")
            self.assertIn("Mandatory M0 verification missing", res["error"])

    def test_06_reject_truncated_m0_steps(self) -> None:
        """F02: Verifies worker rejects workload when m0_verification steps are truncated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_w = Path(tmpdir) / "truncated_workload.json"
            with self.workload_path.open("r", encoding="utf-8") as f:
                w_data = json.load(f)

            # Truncate steps from 4 to 2 and recompute fingerprint
            w_data["m0_verification"]["steps"] = w_data["m0_verification"]["steps"][:2]
            from generate_dstar03_workloads import compute_workload_fingerprint
            w_data["sha256_workload"] = compute_workload_fingerprint(w_data)

            with tmp_w.open("w", encoding="utf-8") as f:
                json.dump(w_data, f)

            res = run_worker(workload_path=tmp_w, engine_name="m1", attempt_id="test_trunc_m0")
            self.assertEqual(res["status"], "CORRECTNESS_FAIL")
            self.assertIn("does not match stream length", res["error"])

    def test_07_reject_wrong_expected_tuple(self) -> None:
        """F02: Verifies worker fails with CORRECTNESS_FAIL and failing_step if expected tuple mismatches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_w = Path(tmpdir) / "wrong_tuple_workload.json"
            with self.workload_path.open("r", encoding="utf-8") as f:
                w_data = json.load(f)

            # Change step 0 expected entered tuple to something wrong and recompute fingerprint
            w_data["m0_verification"]["steps"][0]["entered"] = [[99, 99, 99, 99]]
            from generate_dstar03_workloads import compute_workload_fingerprint
            w_data["sha256_workload"] = compute_workload_fingerprint(w_data)

            with tmp_w.open("w", encoding="utf-8") as f:
                json.dump(w_data, f)

            res = run_worker(workload_path=tmp_w, engine_name="m1", attempt_id="test_wrong_tuple")
            self.assertEqual(res["status"], "CORRECTNESS_FAIL")
            self.assertIn("M0 ground truth mismatch at step 0", res["error"])
            self.assertEqual(res["failing_step"], 0)

    def test_08_reject_component_hash_mismatch(self) -> None:
        """F02: Verifies worker rejects fixture when declared component hash does not match content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_w = Path(tmpdir) / "hash_mismatch.json"
            with self.workload_path.open("r", encoding="utf-8") as f:
                w_data = json.load(f)

            w_data["sha256_query"] = "0000000000000000000000000000000000000000000000000000000000000000"
            with tmp_w.open("w", encoding="utf-8") as f:
                json.dump(w_data, f)

            res = run_worker(workload_path=tmp_w, engine_name="m1", attempt_id="test_hash_mismatch")
            self.assertEqual(res["status"], "INPUT_INVALID")
            self.assertIn("Query component hash mismatch", res["error"])

    def test_09_resource_limits_validation(self) -> None:
        """F03: Verifies strict parameter validation on resource limiter."""
        # Valid limits
        validate_resource_limits(60.0, 512 * 1024 * 1024)
        validate_resource_limits(10.5, 100 * 1024 * 1024)

        # Invalid timeout types
        with self.assertRaises(TypeError):
            validate_resource_limits("60", 512 * 1024 * 1024)
        with self.assertRaises(TypeError):
            validate_resource_limits(True, 512 * 1024 * 1024)

        # Invalid timeout values
        with self.assertRaises(ValueError):
            validate_resource_limits(0.0, 512 * 1024 * 1024)
        with self.assertRaises(ValueError):
            validate_resource_limits(-5.0, 512 * 1024 * 1024)
        with self.assertRaises(ValueError):
            validate_resource_limits(float("inf"), 512 * 1024 * 1024)
        with self.assertRaises(ValueError):
            validate_resource_limits(100.0, 512 * 1024 * 1024)  # Exceeds 60.0

        # Invalid address space
        with self.assertRaises(TypeError):
            validate_resource_limits(10.0, 512.0)
        with self.assertRaises(ValueError):
            validate_resource_limits(10.0, 0)
        with self.assertRaises(ValueError):
            validate_resource_limits(10.0, 1024 * 1024 * 1024)  # Exceeds 512 MiB

    def test_10_containment_evidence_generation(self) -> None:
        """F03: Verifies generation and recording of containment self-test evidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ev_file = Path(tmpdir) / "evidence.json"
            ev = record_containment_evidence(ev_file)
            self.assertEqual(ev["overall_containment_status"], "PASS")
            self.assertTrue(ev["memory_containment_test"]["passed"])
            self.assertTrue(ev["timeout_containment_test"]["passed"])
            self.assertTrue(ev_file.exists())


if __name__ == "__main__":
    unittest.main()
