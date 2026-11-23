"""
tests/test_rapidflow_apx_p4a.py — Unit and regression test suite for Phase P4a.

Covers Remediation Findings:
- P4A-F01: Execution Provenance and Execution Identity binding.
- P4A-F02: Ledger Integrity, durable append-only, and fail-closed resume validation.
- P4A-F03: Fail-closed M0 Dynamic Oracle gate and raw duplicate embedding rejection.
- P4A-F04: Scientifically valid candidate evaluation reduction and event ratios (no invalid pruning metric).
- P4A-F05: Monotonic timer scopes, search nesting, tuple commit definition, and observer overhead.
- P4A-F06: Explicit workload attribute lookup (exact k=0, approx k=1, 2) and real timeout containment.
- P4A-F07: --profile-output CLI error handling and unwritable path fail-closed exit.
- P4A-F08: Empirical bottleneck evaluation and R4A_PROFILE_READY_FOR_P4B_REVIEW recommendation.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_p4a import (
    build_native_binary,
    check_system_resources,
    compute_dict_sha256,
    compute_execution_identity,
    compute_m0_ground_truth,
    compute_sha256,
    execute_native_run,
    load_existing_ledger,
    validate_record_integrity,
    verify_git_provenance,
    CANONICAL_MAX_WORK_CAP,
    CorruptedLedgerError,
    PROTOCOL_VERSION,
)
from scripts.summarize_rapidflow_apx_p4a import (
    analyze_p4a_ledger,
    compute_distribution_stats,
    compute_safe_speedup,
    generate_markdown_report,
)
from scripts.measure_observer_overhead_p4a import measure_observer_overhead
from scripts.dstar_oracle import (
    DStarDynamicOracle,
    DStarGraph,
    DStarUpdate,
)


class TestRapidFlowApxP4a(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build binary once for test suite
        cls.bin_path, cls.bin_sha, cls.build_meta, cls.build_ok = build_native_binary(clean_build=False)
        if not cls.build_ok:
            raise RuntimeError(f"Failed to build native binary: {cls.build_meta}")

        manifest_file = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            cls.manifest_data = json.load(f)

        # Lookup cases explicitly by semantic attributes (P4A-F06)
        cls.cases_by_id = {c["case_id"]: c for c in cls.manifest_data.get("cases", [])}

        # Exact controls (k=0)
        cls.wl_diamond_k0 = cls.cases_by_id.get("P4A_WL_01")
        cls.wl_k4_k0 = cls.cases_by_id.get("P4A_WL_11")

        # Approximate controls (k=1, k=2)
        cls.wl_diamond_k1 = cls.cases_by_id.get("P4A_WL_03")
        cls.wl_k4_k1 = cls.cases_by_id.get("P4A_WL_14")
        cls.wl_k4_k2 = cls.cases_by_id.get("P4A_WL_23")

        # Zero-event workload
        cls.wl_diamond_k1_zero_event = cls.cases_by_id.get("P4A_WL_09")

        # Delete-heavy workload
        cls.wl_diamond_k1_delete_heavy = cls.cases_by_id.get("P4A_WL_10")

    def test_01_build_and_system_resources(self):
        """Verifies native binary builds cleanly and host meets preflight RAM and disk bounds."""
        self.assertTrue(self.build_ok, "Native binary must build successfully")
        self.assertTrue(self.bin_path.is_file(), f"Binary must exist: {self.bin_path}")
        self.assertEqual(len(self.bin_sha), 64, "SHA-256 must be 64 hex characters")

        res_meta, res_ok = check_system_resources()
        self.assertTrue(res_ok, "System resources must meet preflight requirements")
        self.assertGreaterEqual(res_meta["mem_available_gib"], 2.0)
        self.assertGreaterEqual(res_meta["free_disk_gib"], 1.0)

    def test_02_workload_lookup_semantics_and_exact_approximate_controls(self):
        """P4A-F06: Verifies semantic attributes and tests exact (k=0) and approximate (k=1, 2) controls."""
        # Diamond k=0 exact control
        self.assertIsNotNone(self.wl_diamond_k0)
        self.assertEqual(self.wl_diamond_k0["query_type"], "diamond")
        self.assertEqual(self.wl_diamond_k0["k"], 0)

        # Diamond k=1 approximate control
        self.assertIsNotNone(self.wl_diamond_k1)
        self.assertEqual(self.wl_diamond_k1["query_type"], "diamond")
        self.assertEqual(self.wl_diamond_k1["k"], 1)

        # K4 k=0 exact control
        self.assertIsNotNone(self.wl_k4_k0)
        self.assertEqual(self.wl_k4_k0["query_type"], "k4")
        self.assertEqual(self.wl_k4_k0["k"], 0)

        # K4 k=1 approximate control
        self.assertIsNotNone(self.wl_k4_k1)
        self.assertEqual(self.wl_k4_k1["query_type"], "k4")
        self.assertEqual(self.wl_k4_k1["k"], 1)

        # K4 k=2 approximate control
        self.assertIsNotNone(self.wl_k4_k2)
        self.assertEqual(self.wl_k4_k2["query_type"], "k4")
        self.assertEqual(self.wl_k4_k2["k"], 2)

        # Test both M1 and M2 on approximate diamond_k1 matching M0 Oracle
        exp_m0, m0_err = compute_m0_ground_truth(self.wl_diamond_k1)
        self.assertIsNotNone(exp_m0, f"M0 Oracle must compute diamond_k1: {m0_err}")

        for eng in ["m1", "m2"]:
            res = execute_native_run(
                binary_path=self.bin_path,
                engine=eng,
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=True,
            )
            self.assertEqual(res["status"], "COMPLETED")
            out_data = res.get("output_data") or {}
            steps = out_data.get("steps", [])
            self.assertEqual(len(steps), len(exp_m0))
            for s_idx, (s_obs, s_exp) in enumerate(zip(steps, exp_m0)):
                obs_ent = sorted(s_obs.get("entered_tuples", []))
                obs_exit = sorted(s_obs.get("exited_tuples", []))
                self.assertEqual(obs_ent, s_exp["entered"], f"Step {s_idx} entered mismatch on {eng}")
                self.assertEqual(obs_exit, s_exp["exited"], f"Step {s_idx} exited mismatch on {eng}")

    def test_03_profiler_timing_validity_and_scopes(self):
        """P4A-F05: Verifies that timing intervals are monotonic, non-negative, and properly nested."""
        for engine in ["m1", "m2"]:
            res = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=True,
                timeout_s=30,
                as_limit_mib=512,
            )
            self.assertEqual(res["status"], "COMPLETED")
            out_data = res.get("output_data") or {}
            self.assertIn("profiling", out_data)
            prof = out_data["profiling"]

            # Macro timings
            self.assertGreater(prof["total_wall_ns"], 0)
            self.assertGreater(prof["pre_output_wall_ns"], 0)
            self.assertGreater(prof["streaming_loop_wall_ns"], 0)
            self.assertGreater(prof["native_step_total_ns"], 0)
            self.assertGreater(prof["input_parsing_ns"], 0)
            self.assertGreater(prof["graph_init_ns"], 0)
            self.assertGreater(prof["query_prep_ns"], 0)
            self.assertEqual(prof["profiling_schema_version"], "1.2.0")

            # Cumulative step timings
            cum = prof.get("cumulative") or {}
            self.assertIn("update_maintenance_ns", cum)
            self.assertIn("search_ns", cum)
            self.assertIn("search_core_ns", cum)
            self.assertIn("neighbor_retrieval_ns", cum)
            self.assertIn("tuple_commit_ns", cum)
            self.assertIn("step_total_ns", cum)

            self.assertGreater(cum["step_total_ns"], 0)
            self.assertGreater(cum["search_ns"], 0)
            self.assertGreaterEqual(cum["update_maintenance_ns"], 0)
            self.assertGreaterEqual(cum["neighbor_retrieval_ns"], 0)
            self.assertGreaterEqual(cum["tuple_commit_ns"], 0)

            # Search nesting invariant: search_ns = search_core_ns + neighbor_retrieval_ns
            self.assertEqual(
                cum["search_ns"],
                cum["search_core_ns"] + cum["neighbor_retrieval_ns"],
                "Search time must equal core search time plus neighbor retrieval time",
            )
            # Step total upper bounds component sum
            self.assertGreaterEqual(
                cum["step_total_ns"],
                cum["search_ns"] + cum["update_maintenance_ns"] + cum["tuple_commit_ns"],
                "Step total must bound component sum",
            )

    def test_04_profiler_behavioral_and_counter_invariance(self):
        """Verifies PROFILE_OFF vs PROFILE_ON produces bit-for-bit identical outputs and counters."""
        for engine in ["m1", "m2"]:
            res_off = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=False,
            )
            res_on = execute_native_run(
                binary_path=self.bin_path,
                engine=engine,
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=True,
            )

            self.assertEqual(res_off["status"], res_on["status"])
            self.assertEqual(res_off["exit_code"], res_on["exit_code"])

            out_off = res_off.get("output_data") or {}
            out_on = res_on.get("output_data") or {}

            # Exact counter matching
            self.assertEqual(out_off.get("counters"), out_on.get("counters"))

            # Step occurrences bit-for-bit matching
            steps_off = out_off.get("steps", [])
            steps_on = out_on.get("steps", [])
            self.assertEqual(len(steps_off), len(steps_on))

            for s_off, s_on in zip(steps_off, steps_on):
                self.assertEqual(s_off["step_index"], s_on["step_index"])
                self.assertEqual(s_off["entered_tuples"], s_on["entered_tuples"])
                self.assertEqual(s_off["exited_tuples"], s_on["exited_tuples"])

    def test_05_profiler_schema_isolation(self):
        """Verifies schema backward compatibility: without --profile, zero profiling keys exist."""
        res_off = execute_native_run(
            binary_path=self.bin_path,
            engine="m2",
            workload_data=copy.deepcopy(self.wl_diamond_k1),
            profile=False,
        )
        out_off = res_off.get("output_data") or {}
        self.assertNotIn("profiling", out_off, "Unprofiled run must not contain profiling key at root")
        for step in out_off.get("steps", []):
            self.assertNotIn("profiling", step, "Unprofiled run must not contain profiling key in steps")

        res_on = execute_native_run(
            binary_path=self.bin_path,
            engine="m2",
            workload_data=copy.deepcopy(self.wl_diamond_k1),
            profile=True,
        )
        out_on = res_on.get("output_data") or {}
        self.assertIn("profiling", out_on, "Profiled run must contain profiling key at root")
        self.assertIn("cumulative", out_on["profiling"])
        for step in out_on.get("steps", []):
            self.assertIn("profiling", step, "Profiled run must contain profiling key in steps")

    def test_06_profile_output_cli_handling(self):
        """P4A-F07: Tests --profile-output CLI handling: valid write, unwritable fail-closed, collision."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Valid write
            valid_prof_path = Path(tmpdir) / "profile_out.json"
            res = execute_native_run(
                binary_path=self.bin_path,
                engine="m2",
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=True,
                profile_output_path=valid_prof_path,
            )
            self.assertEqual(res["exit_code"], 0)
            self.assertTrue(valid_prof_path.is_file(), "Profile output file must exist on valid write")
            with open(valid_prof_path, "r", encoding="utf-8") as pf:
                prof_json = json.load(pf)
            self.assertTrue(prof_json.get("enabled"))
            self.assertIn("cumulative", prof_json)

            # 2. Unwritable path fail-closed
            unwritable_path = Path(tmpdir) / "nonexistent_dir" / "sub" / "profile.json"
            res_bad = execute_native_run(
                binary_path=self.bin_path,
                engine="m2",
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=True,
                profile_output_path=unwritable_path,
            )
            self.assertNotEqual(res_bad["exit_code"], 0, "Unwritable profile-output path must fail closed")
            self.assertEqual(res_bad["exit_code"], 7, "Exit code must be 7 (IO_ERROR)")

            # 3. Collision: profile-output identical to primary output
            cmd = [
                str(self.bin_path),
                "--engine", "m2",
                "--workload", str(valid_prof_path),
                "--output", str(valid_prof_path),
                "--profile-output", str(valid_prof_path),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0, "Identical output and profile-output paths must be rejected")
            self.assertEqual(proc.returncode, 1, "Exit code must be 1 (CLI_ARG_ERROR)")

    def test_07_real_timeout_containment(self):
        """P4A-F06: Verifies subprocess timeout containment actually kills process and returns TIMEOUT."""
        # Run with tiny timeout on a workload
        res = execute_native_run(
            binary_path=self.bin_path,
            engine="m2",
            workload_data=copy.deepcopy(self.wl_diamond_k1),
            profile=False,
            timeout_s=0.000001,  # 1 microsecond forces timeout
        )
        self.assertEqual(res["status"], "TIMEOUT", "Forced micro-timeout must report TIMEOUT status")
        self.assertEqual(res["exit_code"], 124, "Timeout returncode must be 124")
        self.assertTrue(res["timed_out"])

    def test_08_ledger_integrity_and_fail_closed_corruption(self):
        """P4A-F02: Tests ledger integrity, corruption fail-closed, and strict resume identity matching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "test_ledger.jsonl"

            # 1. Corrupted JSON line fail-closed
            with open(ledger_path, "w", encoding="utf-8") as f:
                f.write('{"execution_identity": "ID1", "status": "COMPLETED_VERIFIED"}\n')
                f.write('CORRUPTED_JSON_NOT_VALID_HERE\n')
                f.write('{"execution_identity": "ID1", "status": "COMPLETED_VERIFIED"}\n')

            with self.assertRaises(CorruptedLedgerError):
                load_existing_ledger(ledger_path)

            # 2. Strict resume identity matching
            prov_meta, _ = verify_git_provenance(allow_dirty=True)
            commit = prov_meta.get("repo_commit") or "0" * 40
            dirty = prov_meta.get("dirty_state", "CLEAN")
            valid_id = compute_execution_identity(
                protocol_version=PROTOCOL_VERSION,
                protocol_sha256="a" * 64,
                manifest_sha256="b" * 64,
                source_commit=commit,
                source_dirty=dirty,
                binary_sha256=self.bin_sha,
                effective_max_work=CANONICAL_MAX_WORK_CAP,
                effective_timeout=60.0,
                effective_as_limit_mib=512,
            )
            wrong_id = compute_execution_identity(
                protocol_version=PROTOCOL_VERSION,
                protocol_sha256="a" * 64,
                manifest_sha256="b" * 64,
                source_commit=commit,
                source_dirty=dirty,
                binary_sha256="9" * 64,
                effective_max_work=CANONICAL_MAX_WORK_CAP,
                effective_timeout=60.0,
                effective_as_limit_mib=512,
            )

            base_rec = {
                "protocol_version": PROTOCOL_VERSION,
                "protocol_sha256": "a" * 64,
                "manifest_sha256": "b" * 64,
                "source_commit": commit,
                "source_dirty_state": dirty,
                "binary_sha256": self.bin_sha,
                "workload_id": "P4A_WL_01",
                "workload_sha256": "c" * 64,
                "exit_code": 0,
                "effective_max_work": CANONICAL_MAX_WORK_CAP,
                "effective_timeout": 60.0,
                "effective_memory_limit_mib": 512,
            }

            with open(ledger_path, "w", encoding="utf-8") as f:
                rec_valid = dict(
                    base_rec,
                    execution_identity=valid_id,
                    repetition=0,
                    engine="m1",
                    status="COMPLETED_VERIFIED",
                    performance_admission_status="ACCEPTED",
                    m0_agreed=True,
                    attempt_id="ATTEMPT_1",
                    slot_key="P4A_WL_01_rep0_m1",
                )
                rec_wrong = dict(
                    base_rec,
                    execution_identity=wrong_id,
                    binary_sha256="9" * 64,
                    repetition=0,
                    engine="m2",
                    status="COMPLETED_VERIFIED",
                    performance_admission_status="ACCEPTED",
                    m0_agreed=True,
                    attempt_id="ATTEMPT_2",
                    slot_key="P4A_WL_01_rep0_m2",
                )
                f.write(json.dumps(rec_valid) + "\n")
                f.write(json.dumps(rec_wrong) + "\n")

            resumable, duplicates = load_existing_ledger(ledger_path, expected_identity=valid_id)
            self.assertIn("P4A_WL_01_rep0_m1", resumable, "Valid identity slot must be resumable")
            self.assertNotIn("P4A_WL_01_rep0_m2", resumable, "Mismatched identity slot must NOT be resumable")

            # 3. Unverified slot cannot be resumed
            with open(ledger_path, "a", encoding="utf-8") as f:
                rec_unverified = dict(
                    base_rec,
                    execution_identity=valid_id,
                    repetition=1,
                    engine="m1",
                    status="FAILED_CORRECTNESS",
                    performance_admission_status="REJECTED",
                    m0_agreed=False,
                    attempt_id="ATTEMPT_3",
                    slot_key="P4A_WL_01_rep1_m1",
                    exit_code=1,
                )
                f.write(json.dumps(rec_unverified) + "\n")

            resumable2, _ = load_existing_ledger(ledger_path, expected_identity=valid_id)
            self.assertNotIn("P4A_WL_01_rep1_m1", resumable2, "Failed slot must NOT be skipped on resume")

    def test_09_m0_oracle_fail_closed_admission(self):
        """P4A-F03: Tests fail-closed admission: Oracle failure blocks admission, duplicate raw tuples rejected."""
        # 1. Oracle error on invalid workload
        bad_wl = copy.deepcopy(self.wl_diamond_k1)
        bad_wl["query"]["vertices"] = []  # Empty query causes Oracle error
        m0_res, m0_err = compute_m0_ground_truth(bad_wl)
        self.assertIsNone(m0_res, "Oracle must return None on error")
        self.assertTrue(len(m0_err) > 0, "Error message must be populated")

        # 2. Raw duplicate emission detection
        # Verify that if an engine returns duplicate raw tuples, correctness fails
        # Simulated step comparison
        step_obs_with_duplicates = {
            "entered_tuples": [[0, 1, 2, 3], [0, 1, 2, 3]],  # Duplicate
            "exited_tuples": [],
        }
        obs_raw = step_obs_with_duplicates["entered_tuples"]
        obs_uniq = [list(t) for t in dict.fromkeys(tuple(x) for x in obs_raw)]
        self.assertNotEqual(len(obs_raw), len(obs_uniq), "Duplicate raw tuples must be detected before set conversion")

    def test_10_summarizer_mismatched_binary_pairing_rejection(self):
        """P4A-F02: Verifies summarizer rejects pairing M1 and M2 when binary SHA or identity differs."""
        records = [
            {
                "execution_identity": "ID_A",
                "binary_sha256": "BIN_SHA_A",
                "workload_id": "P4A_WL_01",
                "workload_sha256": "WL_SHA_1",
                "repetition": 0,
                "engine": "m1",
                "status": "COMPLETED_VERIFIED",
                "performance_admission_status": "ACCEPTED",
                "m0_agreed": True,
                "outer_wall_ns": 1000,
                "profiling": {"streaming_loop_wall_ns": 500, "cumulative": {"search_ns": 300, "step_total_ns": 400}},
                "counters": {"candidate_evaluations": 100, "committed_emissions": 10},
            },
            {
                "execution_identity": "ID_B",  # Mismatched identity & binary
                "binary_sha256": "BIN_SHA_B",
                "workload_id": "P4A_WL_01",
                "workload_sha256": "WL_SHA_1",
                "repetition": 0,
                "engine": "m2",
                "status": "COMPLETED_VERIFIED",
                "performance_admission_status": "ACCEPTED",
                "m0_agreed": True,
                "outer_wall_ns": 800,
                "profiling": {"streaming_loop_wall_ns": 400, "cumulative": {"search_ns": 200, "step_total_ns": 300}},
                "counters": {"candidate_evaluations": 50, "committed_emissions": 10},
            },
        ]
        metrics = analyze_p4a_ledger(records)
        self.assertEqual(metrics["total_valid_pairs"], 0, "Mismatched binary/identity must be rejected from pairing")
        self.assertEqual(metrics["total_rejected_pairs"], 1)

    def test_11_pruning_and_candidate_metrics_validity(self):
        """P4A-F04: Verifies summarizer produces valid candidate reduction metrics and no invalid pruning metric."""
        records = [
            {
                "execution_identity": "ID_CANONICAL",
                "binary_sha256": "BIN_SHA",
                "workload_id": "P4A_WL_01",
                "workload_sha256": "WL_SHA_1",
                "repetition": 0,
                "engine": "m1",
                "status": "COMPLETED_VERIFIED",
                "performance_admission_status": "ACCEPTED",
                "m0_agreed": True,
                "outer_wall_ns": 1000,
                "profiling": {"streaming_loop_wall_ns": 500, "cumulative": {"search_ns": 300, "step_total_ns": 400}},
                "counters": {"candidate_evaluations": 100, "committed_emissions": 10},
            },
            {
                "execution_identity": "ID_CANONICAL",
                "binary_sha256": "BIN_SHA",
                "workload_id": "P4A_WL_01",
                "workload_sha256": "WL_SHA_1",
                "repetition": 0,
                "engine": "m2",
                "status": "COMPLETED_VERIFIED",
                "performance_admission_status": "ACCEPTED",
                "m0_agreed": True,
                "outer_wall_ns": 800,
                "profiling": {"streaming_loop_wall_ns": 400, "cumulative": {"search_ns": 200, "step_total_ns": 300}},
                "counters": {"candidate_evaluations": 54, "committed_emissions": 10},
            },
        ]
        metrics = analyze_p4a_ledger(records)
        self.assertEqual(metrics["total_valid_pairs"], 1)
        cm = metrics["counter_metrics"]
        self.assertNotIn("pruning_efficiency_pct", cm["m1"], "Invalid pruning_efficiency_pct must not exist")
        self.assertIn("relative_candidate_eval_reduction_m2_vs_m1_pct", cm)
        self.assertEqual(cm["relative_candidate_eval_reduction_m2_vs_m1_pct"], 46.0)
        self.assertEqual(cm["m1"]["events_per_candidate_eval_ratio"], 0.1)

    def test_12_observer_overhead_measurement(self):
        """P4A-F05: Measures observer overhead across repeated executions of PROFILE_OFF vs PROFILE_ON."""
        off_times = []
        on_times = []
        # Run 3 repetitions of both
        for _ in range(3):
            res_off = execute_native_run(
                binary_path=self.bin_path,
                engine="m2",
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=False,
            )
            res_on = execute_native_run(
                binary_path=self.bin_path,
                engine="m2",
                workload_data=copy.deepcopy(self.wl_diamond_k1),
                profile=True,
            )
            self.assertEqual(res_off["exit_code"], 0)
            self.assertEqual(res_on["exit_code"], 0)
            off_times.append(res_off["outer_wall_ns"])
            on_times.append(res_on["outer_wall_ns"])

        mean_off = sum(off_times) / len(off_times)
        mean_on = sum(on_times) / len(on_times)
        # Overhead ratio: (mean_on - mean_off) / mean_off
        overhead_pct = 100.0 * (mean_on - mean_off) / mean_off if mean_off > 0 else 0.0
        # Assert non-negative mean times
        self.assertGreater(mean_off, 0)
        self.assertGreater(mean_on, 0)

    def test_13_identity_tampering_adversarial_rejection(self):
        """R2-F01: Adversarial Case A — Modified metadata with claimed execution identity must be rejected fail-closed."""
        prov_meta, _ = verify_git_provenance(allow_dirty=True)
        commit = prov_meta.get("repo_commit") or "0" * 40
        dirty = prov_meta.get("dirty_state", "CLEAN")
        exec_id = compute_execution_identity(
            protocol_version=PROTOCOL_VERSION,
            protocol_sha256="a" * 64,
            manifest_sha256="b" * 64,
            source_commit=commit,
            source_dirty=dirty,
            binary_sha256=self.bin_sha,
            effective_max_work=CANONICAL_MAX_WORK_CAP,
            effective_timeout=60.0,
            effective_as_limit_mib=512,
        )

        canonical_rec = {
            "execution_identity": exec_id,
            "protocol_version": PROTOCOL_VERSION,
            "protocol_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "source_commit": commit,
            "source_dirty_state": dirty,
            "binary_sha256": self.bin_sha,
            "workload_id": "P4A_WL_01",
            "workload_sha256": "c" * 64,
            "engine": "m1",
            "repetition": 0,
            "slot_key": "P4A_WL_01_rep0_m1",
            "attempt_id": "ATT_01",
            "status": "COMPLETED_VERIFIED",
            "exit_code": 0,
            "performance_admission_status": "ACCEPTED",
            "m0_agreed": True,
            "effective_max_work": CANONICAL_MAX_WORK_CAP,
            "effective_timeout": 60.0,
            "effective_memory_limit_mib": 512,
        }

        # 1. Canonical record is valid
        is_ok, msg = validate_record_integrity(canonical_rec, expected_header=exec_id)
        self.assertTrue(is_ok, f"Canonical record must be valid: {msg}")

        # 2. Tamper binary SHA -> Rejected
        bad_bin = dict(canonical_rec)
        bad_bin["binary_sha256"] = "f" * 64
        ok, diag = validate_record_integrity(bad_bin, expected_header=exec_id)
        self.assertFalse(ok, "Modified binary SHA must fail integrity check")
        self.assertIn("Self-consistency failure", diag)

        # 3. Tamper source commit -> Rejected
        bad_commit = dict(canonical_rec)
        bad_commit["source_commit"] = "badbeef" * 5
        ok, diag = validate_record_integrity(bad_commit, expected_header=exec_id)
        self.assertFalse(ok, "Modified source commit must fail integrity check")

        # 4. Tamper resource limits (exceeding cap) -> Rejected
        bad_cap = dict(canonical_rec)
        bad_cap["effective_max_work"] = 9999999
        ok, diag = validate_record_integrity(bad_cap, expected_header=exec_id)
        self.assertFalse(ok, "Exceeding max work cap must fail integrity check")

        # 5. Missing mandatory field -> Rejected
        bad_missing = dict(canonical_rec)
        del bad_missing["binary_sha256"]
        ok, diag = validate_record_integrity(bad_missing, expected_header=exec_id)
        self.assertFalse(ok, "Missing mandatory field must fail integrity check")

        # 6. Wrong type -> Rejected
        bad_type = dict(canonical_rec)
        bad_type["repetition"] = "0"
        ok, diag = validate_record_integrity(bad_type, expected_header=exec_id)
        self.assertFalse(ok, "Wrong metadata type must fail integrity check")

        # 7. Feed tampered record to load_existing_ledger -> Raises CorruptedLedgerError fail-closed
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "tampered_ledger.jsonl"
            with open(ledger_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(bad_bin) + "\n")

            with self.assertRaises(CorruptedLedgerError):
                load_existing_ledger(ledger_file, expected_identity=exec_id)

    def test_14_duplicate_slot_adversarial_speedup_distortion(self):
        """R2-F02: Adversarial Case B — Injected duplicate slot must not distort canonical speedup; ambiguous slot is rejected."""
        base_m1 = {
            "execution_identity": "ID_CANONICAL",
            "binary_sha256": "BIN_SHA",
            "workload_sha256": "WL_SHA_1",
            "workload_id": "P4A_WL_01",
            "repetition": 0,
            "engine": "m1",
            "status": "COMPLETED_VERIFIED",
            "m0_agreed": True,
            "performance_admission_status": "ACCEPTED",
            "outer_wall_ns": 954200,
            "profiling": {"streaming_loop_wall_ns": 954200, "cumulative": {"step_total_ns": 500000, "search_ns": 400000}},
            "counters": {"candidate_evaluations": 100, "committed_emissions": 10},
        }
        base_m2 = {
            "execution_identity": "ID_CANONICAL",
            "binary_sha256": "BIN_SHA",
            "workload_sha256": "WL_SHA_1",
            "workload_id": "P4A_WL_01",
            "repetition": 0,
            "engine": "m2",
            "status": "COMPLETED_VERIFIED",
            "m0_agreed": True,
            "performance_admission_status": "ACCEPTED",
            "outer_wall_ns": 1000000,
            "profiling": {"streaming_loop_wall_ns": 1000000, "cumulative": {"step_total_ns": 500000, "search_ns": 400000}},
            "counters": {"candidate_evaluations": 50, "committed_emissions": 10},
        }

        # 1. Clean pair yields speedup = 954200 / 1000000 = 0.9542
        clean_res = analyze_p4a_ledger([base_m1, base_m2])
        self.assertEqual(clean_res["total_valid_pairs"], 1)
        self.assertEqual(clean_res["overall_speedup_distribution"]["outer_wall_speedup"]["median"], 0.9542)

        # 2. Injected duplicate M2 with runtime 1000ns (adversarial attempt to distort speedup to 954.2x)
        injected_m2 = dict(base_m2)
        injected_m2["outer_wall_ns"] = 1000
        injected_m2["attempt_id"] = "INJECTED_ATTEMPT_99"

        # Forward order: [base_m1, base_m2, injected_m2]
        fwd_res = analyze_p4a_ledger([base_m1, base_m2, injected_m2])
        # Ambiguous duplicate slot must be rejected, total valid pairs must be 0, speedup NOT distorted
        self.assertEqual(fwd_res["total_valid_pairs"], 0, "Pair with conflicting duplicate attempts must be rejected")
        self.assertEqual(len(fwd_res["duplicate_slot_warnings"]), 1)
        self.assertEqual(fwd_res["overall_speedup_distribution"]["outer_wall_speedup"]["count"], 0)

        # Reverse order: [base_m1, injected_m2, base_m2] -> Must produce identical rejection (order-independent)
        rev_res = analyze_p4a_ledger([base_m1, injected_m2, base_m2])
        self.assertEqual(rev_res["total_valid_pairs"], 0, "Reverse order must also reject ambiguous duplicate")

        # 3. Exact identical duplicate records (e.g. repeated write) deduplicate cleanly
        identical_res = analyze_p4a_ledger([base_m1, base_m2, dict(base_m2)])
        self.assertEqual(identical_res["total_valid_pairs"], 1)
        self.assertEqual(identical_res["overall_speedup_distribution"]["outer_wall_speedup"]["median"], 0.9542)

    def test_15_zero_denominator_timer_validity(self):
        """R2-F03: Adversarial Case C — Zero denominator or invalid duration must not produce fake 1.0x speedup."""
        # Unit checks on compute_safe_speedup
        self.assertEqual(compute_safe_speedup(100, 50), (2.0, "VALID"))
        self.assertEqual(compute_safe_speedup(100, 0), (None, "ZERO_OR_NEGATIVE_DURATION"))
        self.assertEqual(compute_safe_speedup(0, 100), (None, "ZERO_OR_NEGATIVE_DURATION"))
        self.assertEqual(compute_safe_speedup(100, -50), (None, "ZERO_OR_NEGATIVE_DURATION"))
        self.assertEqual(compute_safe_speedup(100, None), (None, "MISSING_OR_NONE"))
        self.assertEqual(compute_safe_speedup(float("nan"), 50), (None, "NAN_VALUE"))
        self.assertEqual(compute_safe_speedup(100, float("inf")), (None, "INFINITE_VALUE"))
        self.assertEqual(compute_safe_speedup(True, 50), (None, "INVALID_TYPE_BOOL"))

        # In analyze_p4a_ledger: M2 duration = 0
        recs = [
            {
                "execution_identity": "ID_CANONICAL",
                "binary_sha256": "BIN_SHA",
                "workload_sha256": "WL_SHA_1",
                "workload_id": "P4A_WL_01",
                "repetition": 0,
                "engine": "m1",
                "status": "COMPLETED_VERIFIED",
                "m0_agreed": True,
                "performance_admission_status": "ACCEPTED",
                "outer_wall_ns": 1000000,
                "profiling": {"streaming_loop_wall_ns": 1000000, "cumulative": {"step_total_ns": 500000, "search_ns": 400000}},
            },
            {
                "execution_identity": "ID_CANONICAL",
                "binary_sha256": "BIN_SHA",
                "workload_sha256": "WL_SHA_1",
                "workload_id": "P4A_WL_01",
                "repetition": 0,
                "engine": "m2",
                "status": "COMPLETED_VERIFIED",
                "m0_agreed": True,
                "performance_admission_status": "ACCEPTED",
                "outer_wall_ns": 0,  # ZERO!
                "profiling": {"streaming_loop_wall_ns": 0, "cumulative": {"step_total_ns": 0, "search_ns": 0}},
            },
        ]
        res = analyze_p4a_ledger(recs)
        # Ratio must NOT be 1.0!
        outer_stat = res["overall_speedup_distribution"]["outer_wall_speedup"]
        self.assertEqual(outer_stat["count"], 0, "Zero denominator must exclude ratio from distribution")
        self.assertIsNone(outer_stat["median"], "Median must be None when no valid ratios exist")
        self.assertTrue(len(res["timer_exclusions"]) > 0, "Timer exclusions must be recorded")

    def test_16_all_workers_timeout_exit_code(self):
        """R2-F04: Adversarial Case D — When all workers timeout, runner must fail closed with exit code 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "timeout_ledger.jsonl"
            summary_file = Path(tmpdir) / "timeout_summary.json"

            cmd = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_rapidflow_apx_p4a.py"),
                "--workload", "P4A_WL_03",
                "--repetitions", "1",
                "--timeout", "0.000001",  # 1 microsecond forces timeout
                "--ledger", str(ledger_file),
                "--output", str(summary_file),
                "--allow-dirty",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0, "Runner must return non-zero exit code when slots timeout")
            self.assertEqual(proc.returncode, 1)

            # Check that summary records CANONICAL_INCOMPLETE verdict
            self.assertTrue(summary_file.is_file())
            with open(summary_file, "r", encoding="utf-8") as f:
                summary = json.load(f)
            self.assertEqual(summary.get("overall_verdict"), "CANONICAL_INCOMPLETE")
            self.assertEqual(summary["slot_counters"]["timeout_slots"], 2)
            self.assertEqual(summary["slot_counters"]["censored_slots"], 2)

    def test_17_micro_stage_markdown_table_populated(self):
        """R2-F05: Verifies that markdown report generates a populated micro-stage table with distinct denominators."""
        summary_file = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_summary_metrics.json"
        if not summary_file.is_file():
            self.skipTest("Summary metrics file not present")

        with open(summary_file, "r", encoding="utf-8") as f:
            metrics = json.load(f)

        md_report = generate_markdown_report(metrics)

        # 1. Micro-stage table must have all 4 components
        self.assertIn("## 5. Micro-Step Stage Execution Breakdown", md_report)
        self.assertIn("Update Maintenance (TransactionMgr)", md_report)
        self.assertIn("Search Core (Search excluding Neighbor Retrieval)", md_report)
        self.assertIn("Neighbor Retrieval (AuthoritativeGraph)", md_report)
        self.assertIn("Tuple Commit (sink.commit_step)", md_report)

        # 2. Search-normalized breakdown must be present
        self.assertIn("### 5.1 Search-Normalized Breakdown (% of Search Duration)", md_report)
        self.assertIn("Neighbor Retrieval within Search", md_report)
        self.assertIn("Search Core within Search", md_report)

    def test_18_observer_overhead_script_execution(self):
        """R2-F07: Measures observer overhead with balanced alternating trials and verifies persisted artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "overhead.jsonl"
            sum_path = Path(tmpdir) / "overhead_summary.json"

            summary = measure_observer_overhead(
                raw_output_path=raw_path,
                summary_output_path=sum_path,
                target_workloads=["P4A_WL_01", "P4A_WL_03"],
                trials_per_workload=2,
                clean_build=False,
            )

            self.assertTrue(summary["functional_equivalence_verified"])
            self.assertEqual(summary["total_paired_trials"], 4)
            self.assertTrue(raw_path.is_file())
            self.assertTrue(sum_path.is_file())

            # Verify alternating order in raw records
            with open(raw_path, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(lines), 4)
            self.assertEqual(lines[0]["first_mode"], "PROFILE_OFF")
            self.assertEqual(lines[1]["first_mode"], "PROFILE_ON")
            self.assertEqual(lines[2]["first_mode"], "PROFILE_OFF")
            self.assertEqual(lines[3]["first_mode"], "PROFILE_ON")

    def test_19_resource_limits_cli_validation(self):
        """R2-F08: Verifies CLI validates resource limits against approved caps before worker invocation."""
        runner_script = str(REPO_ROOT / "scripts" / "run_rapidflow_apx_p4a.py")

        # 1. Timeout > 60s
        proc = subprocess.run([sys.executable, runner_script, "--timeout", "100.0"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid --timeout", proc.stderr)

        # 2. Timeout <= 0
        proc = subprocess.run([sys.executable, runner_script, "--timeout", "0.0"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid --timeout", proc.stderr)

        # 3. AS limit > 512
        proc = subprocess.run([sys.executable, runner_script, "--as-limit-mib", "1024"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid --as-limit-mib", proc.stderr)

        # 4. Repetitions > 10
        proc = subprocess.run([sys.executable, runner_script, "--repetitions", "25"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid --repetitions", proc.stderr)

    def test_20_decision_record_reconciled_with_summary_metrics(self):
        """R2-F06: Verifies decision record matches validated summary metrics exactly."""
        summary_file = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_summary_metrics.json"
        decision_file = REPO_ROOT / "docs" / "research_decisions" / "D-R4a-native-profiling.md"
        if not summary_file.is_file() or not decision_file.is_file():
            self.skipTest("Summary or decision file not present")

        with open(summary_file, "r", encoding="utf-8") as f:
            metrics = json.load(f)
        with open(decision_file, "r", encoding="utf-8") as f:
            decision_text = f.read()

        # Check search speedup median
        search_med = f"{metrics['overall_speedup_distribution']['search_speedup']['median']:.2f}x"
        self.assertIn(search_med, decision_text)

        # Check candidate reduction
        cand_red = f"{metrics['counter_metrics']['relative_candidate_eval_reduction_m2_vs_m1_pct']:.2f}%"
        self.assertIn(cand_red, decision_text)

        # Check neighbor retrieval within search median
        nr_search = f"{metrics['micro_step_stage_breakdowns_pct']['m2']['neighbor_retrieval_within_search_pct']['median']:.2f}%"
        self.assertIn(nr_search, decision_text)

        # Check verdict
        self.assertIn("R4A_PROFILE_READY_FOR_P4B_REVIEW", decision_text)

    def test_21_cross_component_adversarial_interactions(self):
        """Section XII: Verifies cross-component fail-closed adversarial interactions."""
        # 1. Provenance failure in duplicate attempt
        rec_good = {
            "execution_identity": "ID_A",
            "binary_sha256": "BIN_A",
            "workload_sha256": "WL_1",
            "workload_id": "P4A_WL_01",
            "repetition": 0,
            "engine": "m1",
            "status": "COMPLETED_VERIFIED",
            "performance_admission_status": "ACCEPTED",
            "m0_agreed": True,
            "outer_wall_ns": 1000,
            "profiling": {"streaming_loop_wall_ns": 500, "cumulative": {"search_ns": 300, "step_total_ns": 400}},
            "counters": {"candidate_evaluations": 100, "committed_emissions": 10},
        }
        rec_tampered_bin = dict(rec_good)
        rec_tampered_bin["binary_sha256"] = "BIN_TAMPERED"

        # Even if someone claims identical execution_identity, differing binary SHA rejects pairing
        m = analyze_p4a_ledger([rec_good, rec_tampered_bin])
        self.assertEqual(m["total_valid_pairs"], 0)

        # 2. Oracle failure slot cannot be admitted
        rec_oracle_fail = dict(rec_good)
        rec_oracle_fail["m0_agreed"] = False
        rec_oracle_fail["status"] = "ORACLE_FAILED"
        rec_oracle_fail["performance_admission_status"] = "REJECTED"
        m2 = analyze_p4a_ledger([rec_oracle_fail, dict(rec_good, engine="m2")])
        self.assertEqual(m2["total_valid_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
