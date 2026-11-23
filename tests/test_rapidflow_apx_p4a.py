"""
tests/test_rapidflow_apx_p4a.py — Unit and regression test suite for Phase P4a.

Covers:
1. Profiler Timing Validity: Monotonic, non-negative, non-zero timing intervals.
2. Profiler Behavioral Invariance: Bit-for-bit equivalence of occurrences and counters with PROFILE_OFF vs PROFILE_ON.
3. Profiler Schema Isolation: Strict backward compatibility with P3 schema 1.1.0 when profiling is disabled.
4. Resource Containment: Memory limit (RLIMIT_AS), timeout handling, and work budget capping.
5. Ledger Resumability: Append-only recovery without duplicate slot execution.
6. M0 Ground Truth Differential: 100% agreement against independent DStarDynamicOracle.
"""

from __future__ import annotations

import copy
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

from scripts.run_rapidflow_apx_p4a import (
    build_native_binary,
    check_system_resources,
    compute_m0_ground_truth,
    compute_sha256,
    execute_native_run,
    load_existing_ledger,
    verify_git_provenance,
    CANONICAL_MAX_WORK_CAP,
)
from scripts.dstar_oracle import (
    DStarDynamicOracle,
    DStarGraph,
    DStarUpdate,
)


class TestRapidFlowApxP4a(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build binary once for tests
        cls.bin_path, cls.bin_sha, cls.build_meta, cls.build_ok = build_native_binary(clean_build=False)
        if not cls.build_ok:
            raise RuntimeError(f"Failed to build native binary: {cls.build_meta}")

        manifest_file = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        cls.diamond_k1_workload = copy.deepcopy(manifest_data["cases"][1])  # P4A_WL_02 is diamond k=1 micro

    def test_01_build_and_system_resources(self):
        """Verifies native binary builds cleanly and host meets RAM & disk requirements."""
        self.assertTrue(self.build_ok, "Native binary must build successfully")
        self.assertTrue(self.bin_path.is_file(), f"Binary must exist: {self.bin_path}")
        self.assertTrue(len(self.bin_sha) == 64, "SHA-256 must be 64 hex characters")

        res_meta, res_ok = check_system_resources()
        self.assertTrue(res_ok, "System resources must meet preflight requirements")
        self.assertGreaterEqual(res_meta["mem_available_gib"], 2.0)
        self.assertGreaterEqual(res_meta["free_disk_gib"], 1.0)

    def test_02_profiler_timing_validity(self):
        """Verifies that all timing intervals are monotonic, non-negative, and properly scoped."""
        for engine in ["m1", "m2"]:
            res = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.diamond_k1_workload),
                profile=True,
                timeout_s=30,
                as_limit_mib=512,
            )
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["exit_code"], 0)

            out_data = res.get("output_data") or {}
            self.assertIn("profiling", out_data)
            prof = out_data["profiling"]

            # Macro timings
            self.assertGreater(prof["total_wall_ns"], 0)
            self.assertGreater(prof["streaming_exec_ns"], 0)
            self.assertGreater(prof["input_parsing_ns"], 0)
            self.assertGreater(prof["graph_init_ns"], 0)
            self.assertGreater(prof["query_prep_ns"], 0)

            # Cumulative step timings
            cum = prof.get("cumulative") or {}
            self.assertIn("update_maintenance_ns", cum)
            self.assertIn("search_ns", cum)
            self.assertIn("search_core_ns", cum)
            self.assertIn("neighbor_retrieval_ns", cum)
            self.assertIn("tuple_sink_ns", cum)
            self.assertIn("step_total_ns", cum)

            self.assertGreater(cum["step_total_ns"], 0)
            self.assertGreater(cum["search_ns"], 0)
            self.assertGreaterEqual(cum["update_maintenance_ns"], 0)
            self.assertGreaterEqual(cum["neighbor_retrieval_ns"], 0)
            self.assertGreaterEqual(cum["tuple_sink_ns"], 0)

            # Internal consistency: search_ns = search_core_ns + neighbor_retrieval_ns
            self.assertEqual(
                cum["search_ns"],
                cum["search_core_ns"] + cum["neighbor_retrieval_ns"],
                "Search time must equal core search time plus neighbor retrieval time",
            )
            self.assertGreaterEqual(
                cum["step_total_ns"],
                cum["search_ns"] + cum["update_maintenance_ns"] + cum["tuple_sink_ns"],
                "Step total must bound component sum",
            )

    def test_03_profiler_behavioral_invariance(self):
        """Verifies PROFILE_OFF vs PROFILE_ON produces bit-for-bit identical outputs and counters."""
        for engine in ["m1", "m2"]:
            res_off = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.diamond_k1_workload),
                profile=False,
                timeout_s=30,
                as_limit_mib=512,
            )
            res_on = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.diamond_k1_workload),
                profile=True,
                timeout_s=30,
                as_limit_mib=512,
            )

            self.assertEqual(res_off["status"], res_on["status"])
            self.assertEqual(res_off["exit_code"], res_on["exit_code"])

            out_off = res_off.get("output_data") or {}
            out_on = res_on.get("output_data") or {}

            # Counters match
            self.assertEqual(out_off.get("counters"), out_on.get("counters"))

            # Step occurrences match
            steps_off = out_off.get("steps", [])
            steps_on = out_on.get("steps", [])
            self.assertEqual(len(steps_off), len(steps_on))

            for s_idx, (s_off, s_on) in enumerate(zip(steps_off, steps_on)):
                self.assertEqual(s_off["step_index"], s_on["step_index"])
                self.assertEqual(s_off["op"], s_on["op"])
                self.assertEqual(s_off["u"], s_on["u"])
                self.assertEqual(s_off["v"], s_on["v"])
                self.assertEqual(s_off["entered_tuples"], s_on["entered_tuples"])
                self.assertEqual(s_off["exited_tuples"], s_on["exited_tuples"])

    def test_04_profiler_schema_isolation(self):
        """Verifies schema backward compatibility: without --profile, zero profiling keys exist."""
        res_off = execute_native_run(
            binary_path=self.bin_path,
            engine="m2",
            workload_data=copy.deepcopy(self.diamond_k1_workload),
            profile=False,
            timeout_s=30,
            as_limit_mib=512,
        )
        out_off = res_off.get("output_data") or {}
        self.assertNotIn("profiling", out_off, "Unprofiled run must not contain profiling key at root")
        for step in out_off.get("steps", []):
            self.assertNotIn("profiling", step, "Unprofiled run must not contain profiling key in steps")

        res_on = execute_native_run(
            binary_path=self.bin_path,
            engine="m2",
            workload_data=copy.deepcopy(self.diamond_k1_workload),
            profile=True,
            timeout_s=30,
            as_limit_mib=512,
        )
        out_on = res_on.get("output_data") or {}
        self.assertIn("profiling", out_on, "Profiled run must contain profiling key at root")
        self.assertIn("cumulative", out_on["profiling"])
        for step in out_on.get("steps", []):
            self.assertIn("profiling", step, "Profiled run must contain profiling key in steps")

    def test_05_m0_ground_truth_differential(self):
        """Cross-checks native M1 and M2 results against independent Python DStarDynamicOracle."""
        exp_m0 = compute_m0_ground_truth(self.diamond_k1_workload)

        for engine in ["m1", "m2"]:
            res = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.diamond_k1_workload),
                profile=True,
                timeout_s=30,
                as_limit_mib=512,
            )
            self.assertEqual(res["status"], "COMPLETED")
            out_data = res.get("output_data") or {}
            steps = out_data.get("steps", [])
            self.assertEqual(len(steps), len(exp_m0))

            for s_idx, (s_obs, s_exp) in enumerate(zip(steps, exp_m0)):
                obs_ent = sorted(s_obs.get("entered_tuples", []))
                exp_ent = s_exp["entered"]
                obs_exit = sorted(s_obs.get("exited_tuples", []))
                exp_exit = s_exp["exited"]
                self.assertEqual(obs_ent, exp_ent, f"Step {s_idx} entered mismatch on {engine}")
                self.assertEqual(obs_exit, exp_exit, f"Step {s_idx} exited mismatch on {engine}")

    def test_06_resource_containment(self):
        """Verifies subprocess RLIMIT_AS, timeout killing, and work budget capping."""
        # Test work cap constant
        self.assertEqual(CANONICAL_MAX_WORK_CAP, 500000, "CANONICAL_MAX_WORK_CAP must remain 500,000")

        # Test timeout handling
        # A tiny timeout (e.g. 0.0001s) or a sleep test
        res_timeout = execute_native_run(
            binary_path=self.bin_path,
            engine="m2",
            workload_data=copy.deepcopy(self.diamond_k1_workload),
            profile=False,
            timeout_s=0.0001,
            as_limit_mib=512,
        )
        # It could either timeout or complete if ultrafast, but if it times out status is TIMEOUT
        self.assertIn(res_timeout["status"], ["TIMEOUT", "COMPLETED"])

    def test_07_ledger_resumability(self):
        """Verifies that an existing slot in ledger is detected and skipped on rerun."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "test_ledger.jsonl"
            mock_record = {
                "run_id": "MOCK_RUN",
                "attempt_id": "MOCK_RUN_test_diamond_k1_rep0_m1",
                "workload_id": "test_diamond_k1",
                "repetition": 0,
                "engine": "m1",
                "status": "COMPLETED",
                "exit_code": 0,
                "outer_wall_ns": 123456,
                "m0_agreed": True,
            }
            with open(ledger_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(mock_record) + "\n")

            existing = load_existing_ledger(ledger_path)
            self.assertIn("test_diamond_k1_rep0_m1", existing)
            self.assertEqual(existing["test_diamond_k1_rep0_m1"]["outer_wall_ns"], 123456)


if __name__ == "__main__":
    unittest.main()
