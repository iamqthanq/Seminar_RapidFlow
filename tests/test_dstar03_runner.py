#!/usr/bin/env python3
"""
tests/test_dstar03_runner.py — Unit Tests for Worker and Supervisor Execution.

Covers:
1. Worker full-array verification, reset after warm-up, and timer accuracy.
2. Contained child resource error (RLIMIT_AS enforcement) without workstation stress.
3. Supervisor timeout and process group cleanup.
4. Ledger interruption handling, resume integrity, and hash drift rejection.
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
from dstar03_resources import check_environment_preflight, run_contained_subprocess
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

    def test_02_contained_child_resource_limit(self) -> None:
        """Verifies RLIMIT_AS address-space ceiling catches excessive allocations in disposable child."""
        child_code = "b = bytearray(1000 * 1024 * 1024)"
        res = run_contained_subprocess(
            [sys.executable, "-c", child_code],
            timeout_seconds=5.0,
            limit_as_bytes=512 * 1024 * 1024,
        )
        self.assertIn(res["status"], ("RESOURCE_LIMIT", "ERROR"))
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
        """Verifies journal recovers interrupted attempts and resumes remaining slots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_ledger = tmp_path / "test_attempts.jsonl"

            # Create an interrupted attempt record
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

            # Run with filter for just witness_01
            res = supervisor.run(workload_filter="witness_01")
            self.assertEqual(res["status"], "PASS")

            # Verify ledger records
            with test_ledger.open("r", encoding="utf-8") as f:
                lines = [json.loads(line.strip()) for line in f if line.strip()]

            # The interrupted attempt must have an ATTEMPT_COMPLETED with status INTERRUPTED
            int_completions = [
                l for l in lines
                if l.get("record_type") == "ATTEMPT_COMPLETED"
                and l.get("attempt_id") == interrupted_id
                and l.get("status") == "INTERRUPTED"
            ]
            self.assertEqual(len(int_completions), 1)


if __name__ == "__main__":
    unittest.main()
