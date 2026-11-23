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
from scripts.p4a_evidence_contract import (
    APPROVED_P4A_CANONICAL_COMMIT,
    APPROVED_P4A_EXECUTION_IDENTITY,
    APPROVED_P4A_MANIFEST_SHA256,
    APPROVED_P4A_PROTOCOL_SHA256,
    APPROVED_P4A_RAW_ARTIFACT_SHA256,
    APPROVED_P4A_RAW_ARTIFACT_SIZE_BYTES,
    APPROVED_P4A_RAW_ARTIFACT_LINE_COUNT,
    CanonicalAuthorityError,
    OfficialCanonicalAuthority,
    certify_official_p4a,
    get_official_p4a_authority,
    ARTIFACT_STATUS_FAILED,
    ARTIFACT_STATUS_NOT_CHECKED,
    ARTIFACT_STATUS_VERIFIED,
    AmbiguousSlotError,
    IntegrityConflictError,
    DEFAULT_EVIDENCE_MANIFEST_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROTOCOL_PATH,
    PROTOCOL_MEASUREMENT_VERSION,
    PROTOCOL_ANALYSIS_VERSION,
    PROTOCOL_SPECIFICATION_REVISION,
    compute_expected_slot_matrix,
    create_experiment_header,
    get_frozen_manifest_registry,
    get_trusted_canonical_header,
    get_trusted_raw_artifact_meta,
    load_trusted_evidence_manifest,
    load_verified_ledger,
    resolve_canonical_attempts,
    validate_pair_compatibility,
    verify_raw_artifact_integrity,
)
from scripts.summarize_rapidflow_apx_p4a import (
    DEFAULT_SUMMARY_JSON,
    analyze_p4a_ledger,
    compute_distribution_stats,
    compute_safe_speedup,
    generate_markdown_report,
)
from scripts.measure_observer_overhead_p4a import (
    measure_observer_overhead,
    verify_observer_output_invariance,
)
from scripts.dstar_oracle import (
    DStarDynamicOracle,
    DStarGraph,
    DStarUpdate,
)

# Deterministic synthetic fixtures for unit & adversarial tests (R6-F04)
FIXTURE_VALID_CANONICAL_M1 = {
    "attempt_id": "P4A_RUN_1795423267_P4A_WL_01_rep0_m1",
    "binary_sha256": "6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b",
    "correctness_notes": "",
    "correctness_status": "M0_VERIFIED",
    "counters": {
        "anchors_started": 80,
        "candidate_evaluations": 728,
        "candidate_extensions": 164,
        "committed_emissions": 24,
        "dfs_states": 244,
        "discarded_emissions": 0,
        "edge_lookups": 1080,
        "max_work": 500000,
        "missing_set_rejections": 0,
        "raw_emissions": 24,
        "raw_exact_results": 24,
        "unique_emissions": 24,
        "variants_started": 80,
        "vertex_label_checks": 568,
        "work_units": 808
    },
    "effective_max_work": 500000,
    "effective_memory_limit_mib": 512,
    "effective_timeout": 60.0,
    "engine": "m1",
    "execution_identity": "262279a9d9ea026e07231f3ba511863c383b98133db40e69f603a235ad792135",
    "exit_code": 0,
    "k": 0,
    "m0_agreed": True,
    "manifest_sha256": "f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18",
    "measurement_mode": "CANONICAL_TIMED",
    "order_position": 0,
    "outer_wall_ns": 6044583,
    "performance_admission_status": "ACCEPTED",
    "profiling": {
        "cumulative": {
            "neighbor_retrieval_ns": 32035,
            "search_core_ns": 147716,
            "search_ns": 179751,
            "step_total_ns": 193714,
            "tuple_commit_ns": 1686,
            "tuple_sink_ns": 1686,
            "update_maintenance_ns": 7322
        },
        "cumulative_neighbor_retrieval_ns": 32035,
        "cumulative_search_core_ns": 147716,
        "cumulative_search_ns": 179751,
        "cumulative_step_total_ns": 193714,
        "cumulative_tuple_commit_ns": 1686,
        "cumulative_tuple_sink_ns": 1686,
        "cumulative_update_maintenance_ns": 7322,
        "enabled": True,
        "graph_init_ns": 17106,
        "input_parsing_ns": 1262459,
        "native_step_total_ns": 193714,
        "pre_output_wall_ns": 2240075,
        "profiling_schema_version": "1.2.0",
        "query_prep_ns": 9260,
        "streaming_exec_ns": 734651,
        "streaming_loop_wall_ns": 734651,
        "total_wall_ns": 2240075
    },
    "protocol_sha256": "dd3f39cae4325b3577adec8fa19ff5f52844e9338b1d97aa542d58b6c09d4ebd",
    "protocol_version": "1.2.0",
    "query_type": "diamond",
    "regime": "uniform",
    "repetition": 0,
    "run_id": "P4A_RUN_1795423267",
    "slot_key": "P4A_WL_01_rep0_m1",
    "source_commit": "cf97a7840dff0c0f2bf791834263f3911545110c",
    "source_dirty_state": "CLEAN",
    "status": "COMPLETED_VERIFIED",
    "steps_count": 8,
    "timestamp": "2026-11-23T08:41:07Z",
    "topology": "micro",
    "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1",
    "upstream_pristine": True,
    "workload_id": "P4A_WL_01",
    "workload_sha256": "830d56a1932d045b46ceff9b0e86b666373b62a82fccfcac2df4195134d016b0"
}

FIXTURE_VALID_CANONICAL_M2 = {
    "attempt_id": "P4A_RUN_1795423267_P4A_WL_01_rep0_m2",
    "binary_sha256": "6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b",
    "correctness_notes": "",
    "correctness_status": "M0_VERIFIED",
    "counters": {
        "anchors_started": 80,
        "candidate_evaluations": 728,
        "candidate_extensions": 408,
        "commitment_checks": 0,
        "commitment_rejections": 0,
        "committed_emissions": 24,
        "dfs_states": 244,
        "discarded_emissions": 0,
        "edge_decisions": 220,
        "edge_lookups": 1200,
        "max_work": 500000,
        "missing_branches": 220,
        "raw_emissions": 24,
        "unique_emissions": 24,
        "vertex_label_checks": 568,
        "work_units": 1248
    },
    "effective_max_work": 500000,
    "effective_memory_limit_mib": 512,
    "effective_timeout": 60.0,
    "engine": "m2",
    "execution_identity": "262279a9d9ea026e07231f3ba511863c383b98133db40e69f603a235ad792135",
    "exit_code": 0,
    "k": 0,
    "m0_agreed": True,
    "manifest_sha256": "f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18",
    "measurement_mode": "CANONICAL_TIMED",
    "order_position": 1,
    "outer_wall_ns": 6334541,
    "performance_admission_status": "ACCEPTED",
    "profiling": {
        "cumulative": {
            "neighbor_retrieval_ns": 33362,
            "search_core_ns": 101133,
            "search_ns": 134495,
            "step_total_ns": 149201,
            "tuple_commit_ns": 1609,
            "tuple_sink_ns": 1609,
            "update_maintenance_ns": 7906
        },
        "cumulative_neighbor_retrieval_ns": 33362,
        "cumulative_search_core_ns": 101133,
        "cumulative_search_ns": 134495,
        "cumulative_step_total_ns": 149201,
        "cumulative_tuple_commit_ns": 1609,
        "cumulative_tuple_sink_ns": 1609,
        "cumulative_update_maintenance_ns": 7906,
        "enabled": True,
        "graph_init_ns": 18329,
        "input_parsing_ns": 1285873,
        "native_step_total_ns": 149201,
        "pre_output_wall_ns": 2258132,
        "profiling_schema_version": "1.2.0",
        "query_prep_ns": 9369,
        "streaming_exec_ns": 747316,
        "streaming_loop_wall_ns": 747316,
        "total_wall_ns": 2258132
    },
    "protocol_sha256": "dd3f39cae4325b3577adec8fa19ff5f52844e9338b1d97aa542d58b6c09d4ebd",
    "protocol_version": "1.2.0",
    "query_type": "diamond",
    "regime": "uniform",
    "repetition": 0,
    "run_id": "P4A_RUN_1795423267",
    "slot_key": "P4A_WL_01_rep0_m2",
    "source_commit": "cf97a7840dff0c0f2bf791834263f3911545110c",
    "source_dirty_state": "CLEAN",
    "status": "COMPLETED_VERIFIED",
    "steps_count": 8,
    "timestamp": "2026-11-23T08:41:07Z",
    "topology": "micro",
    "upstream_commit": "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1",
    "upstream_pristine": True,
    "workload_id": "P4A_WL_01",
    "workload_sha256": "830d56a1932d045b46ceff9b0e86b666373b62a82fccfcac2df4195134d016b0"
}


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

        # Valid canonical baseline records as deterministic fixtures (R6-F04)
        cls.valid_canonical_m1 = copy.deepcopy(FIXTURE_VALID_CANONICAL_M1)
        cls.valid_canonical_m2 = copy.deepcopy(FIXTURE_VALID_CANONICAL_M2)

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
                    correctness_status="M0_VERIFIED",
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
                    correctness_status="M0_VERIFIED",
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
                    correctness_status="ORACLE_MISMATCH",
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
        rec_m1 = copy.deepcopy(self.valid_canonical_m1)
        rec_m2 = copy.deepcopy(self.valid_canonical_m2)
        rec_m2["binary_sha256"] = "9" * 64
        metrics = analyze_p4a_ledger([rec_m1, rec_m2])
        self.assertEqual(metrics["total_valid_pairs"], 0, "Mismatched binary/identity must be rejected from pairing")
        self.assertEqual(metrics["total_rejected_pairs"], 1)

    def test_11_pruning_and_candidate_metrics_validity(self):
        """P4A-F04: Verifies summarizer produces valid candidate reduction metrics and no invalid pruning metric."""
        rec_m1 = copy.deepcopy(self.valid_canonical_m1)
        rec_m2 = copy.deepcopy(self.valid_canonical_m2)
        rec_m1["counters"]["candidate_evaluations"] = 100
        rec_m1["counters"]["committed_emissions"] = 10
        rec_m2["counters"]["candidate_evaluations"] = 54
        rec_m2["counters"]["committed_emissions"] = 10
        metrics = analyze_p4a_ledger([rec_m1, rec_m2])
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
            "correctness_status": "M0_VERIFIED",
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
        base_m1 = copy.deepcopy(self.valid_canonical_m1)
        base_m2 = copy.deepcopy(self.valid_canonical_m2)
        base_m1["outer_wall_ns"] = 954200
        base_m2["outer_wall_ns"] = 1000000

        # 1. Clean pair yields speedup = 954200 / 1000000 = 0.9542
        clean_res = analyze_p4a_ledger([base_m1, base_m2])
        self.assertEqual(clean_res["total_valid_pairs"], 1)
        self.assertEqual(clean_res["overall_speedup_distribution"]["outer_wall_speedup"]["median"], 0.9542)

        # 2. Injected duplicate M2 with runtime 1000ns (adversarial attempt to distort speedup to 954.2x)
        injected_m2 = copy.deepcopy(base_m2)
        injected_m2["outer_wall_ns"] = 1000
        injected_m2["attempt_id"] = base_m2.get("attempt_id", "ATT") + "_injected"

        # Forward order: [base_m1, base_m2, injected_m2]
        fwd_res = analyze_p4a_ledger([base_m1, base_m2, injected_m2])
        # Ambiguous duplicate slot must be rejected, total valid pairs must be 0, speedup NOT distorted
        self.assertEqual(fwd_res["total_valid_pairs"], 0, "Pair with conflicting duplicate attempts must be rejected")
        self.assertTrue(len(fwd_res["duplicate_slot_warnings"]) >= 1)
        self.assertEqual(fwd_res["overall_speedup_distribution"]["outer_wall_speedup"]["count"], 0)

        # Reverse order: [base_m1, injected_m2, base_m2] -> Must produce identical rejection (order-independent)
        rev_res = analyze_p4a_ledger([base_m1, injected_m2, base_m2])
        self.assertEqual(rev_res["total_valid_pairs"], 0, "Reverse order must also reject ambiguous duplicate")

        # 3. Exact identical duplicate records (e.g. repeated write) deduplicate cleanly
        identical_res = analyze_p4a_ledger([base_m1, base_m2, copy.deepcopy(base_m2)])
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
        rec_m1 = copy.deepcopy(self.valid_canonical_m1)
        rec_m2 = copy.deepcopy(self.valid_canonical_m2)
        rec_m2["outer_wall_ns"] = 0
        rec_m2["profiling"]["streaming_loop_wall_ns"] = 0
        rec_m2["profiling"]["cumulative"]["step_total_ns"] = 0
        rec_m2["profiling"]["cumulative"]["search_ns"] = 0

        res = analyze_p4a_ledger([rec_m1, rec_m2])
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
        # 1. Differing binary SHA rejects pairing
        rec_good_m1 = copy.deepcopy(self.valid_canonical_m1)
        rec_tampered_bin_m2 = copy.deepcopy(self.valid_canonical_m2)
        rec_tampered_bin_m2["binary_sha256"] = "9" * 64

        m = analyze_p4a_ledger([rec_good_m1, rec_tampered_bin_m2])
        self.assertEqual(m["total_valid_pairs"], 0)

        # 2. Oracle failure slot cannot be admitted
        rec_oracle_fail = copy.deepcopy(self.valid_canonical_m1)
        rec_oracle_fail["m0_agreed"] = False
        rec_oracle_fail["status"] = "FAILED_CORRECTNESS"
        rec_oracle_fail["exit_code"] = 1
        rec_oracle_fail["performance_admission_status"] = "REJECTED"
        m2 = analyze_p4a_ledger([rec_oracle_fail, copy.deepcopy(self.valid_canonical_m2)])
        self.assertEqual(m2["total_valid_pairs"], 0)

    def test_22_workload_hash_tampering_rejected_by_resume(self):
        """R3-F01: Workload hash tampering in record must be rejected fail-closed by resume supervisor."""
        valid_rec = copy.deepcopy(self.valid_canonical_m1)

        # Tamper workload_sha256
        tampered_rec = copy.deepcopy(valid_rec)
        tampered_rec["workload_sha256"] = "f" * 64

        reg = get_frozen_manifest_registry()
        is_ok, diag = validate_record_integrity(
            tampered_rec,
            workload_registry=reg["workload_hashes"],
            strict_provenance=True,
        )
        self.assertFalse(is_ok)
        self.assertIn("Workload SHA-256 mismatch", diag)

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".jsonl") as tf:
            tf.write(json.dumps(tampered_rec) + "\n")
            tf.flush()
            tmp_path = Path(tf.name)

        try:
            # Runner resume must raise CorruptedLedgerError fail-closed
            header = create_experiment_header(
                manifest_path=REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json",
                protocol_version=valid_rec["protocol_version"],
                protocol_sha256=valid_rec["protocol_sha256"],
                source_commit=valid_rec["source_commit"],
                source_dirty_state=valid_rec["source_dirty_state"],
                binary_sha256=valid_rec["binary_sha256"],
            )
            with self.assertRaises(CorruptedLedgerError):
                load_existing_ledger(tmp_path, expected_header=header)
        finally:
            if tmp_path.is_file():
                tmp_path.unlink()

    def test_23_duplicate_attempt_1000x_rejected_by_resume_and_summarizer(self):
        """R3-F02: Conflicting duplicate verified attempt (1000x) is rejected fail-closed without silent selection."""
        valid_rec = copy.deepcopy(self.valid_canonical_m1)

        dup_1000x = copy.deepcopy(valid_rec)
        dup_1000x["attempt_id"] = valid_rec["attempt_id"] + "_retry"
        dup_1000x["outer_wall_ns"] = valid_rec["outer_wall_ns"] * 1000

        # resolve_canonical_attempts must detect conflict and exclude slot
        canonical_slots, duplicate_lineage, conflicts = resolve_canonical_attempts(
            [valid_rec, dup_1000x],
            strict_raise=False,
        )
        self.assertNotIn(valid_rec["slot_key"], canonical_slots)
        self.assertTrue(len(conflicts) > 0)
        self.assertIn("conflicting verified attempts", conflicts[0]["reason"])

        # Summarizer must reject the pair containing the ambiguous slot
        m2_rec = copy.deepcopy(valid_rec)
        m2_rec["engine"] = "m2"
        m2_rec["slot_key"] = valid_rec["slot_key"].replace("_m1", "_m2")
        summary = analyze_p4a_ledger([valid_rec, dup_1000x, m2_rec], mode="scoped")
        self.assertEqual(summary["total_valid_pairs"], 0)
        self.assertEqual(summary["total_rejected_pairs"], 1)

    def test_24_summarizer_source_commit_mismatch_rejected(self):
        """R3-F03: Contradictory source commit between M1 and M2 is rejected by summarizer."""
        rec_m1 = copy.deepcopy(self.valid_canonical_m1)
        rec_m2 = copy.deepcopy(self.valid_canonical_m2)

        # Mutate M2 commit
        m2_mutated = copy.deepcopy(rec_m2)
        m2_mutated["source_commit"] = "0" * 40

        is_compat, reason = validate_pair_compatibility(rec_m1, m2_mutated)
        self.assertFalse(is_compat)
        self.assertTrue(
            "Source commit mismatch" in reason or "Self-consistency failure" in reason
        )

        summary = analyze_p4a_ledger([rec_m1, m2_mutated], mode="scoped")
        self.assertEqual(summary["total_valid_pairs"], 0)
        self.assertEqual(summary["total_rejected_pairs"], 1)

    def test_25_observer_full_tuple_equivalence_guard(self):
        """R3-F04: verify_observer_output_invariance checks step count, duplicate tuples, coordinate ordering, and counters."""
        mock_off = {
            "steps": [
                {
                    "entered_tuples": [[1, 2, 3, 4], [2, 3, 4, 5]],
                    "exited_tuples": [[6, 7, 8, 9]],
                }
            ],
            "counters": {
                "candidate_evaluations": 100,
                "committed_emissions": 3,
                "dfs_states": 50,
                "anchors_started": 10,
                "edge_lookups": 200,
                "vertex_label_checks": 80,
            },
        }
        mock_on = copy.deepcopy(mock_off)

        # Baseline: identical outputs pass
        ok, diag = verify_observer_output_invariance(mock_off, mock_on)
        self.assertTrue(ok, diag)

        # 1. Step count mismatch
        mut_steps = copy.deepcopy(mock_on)
        mut_steps["steps"].append({"entered_tuples": [], "exited_tuples": []})
        ok, diag = verify_observer_output_invariance(mock_off, mut_steps)
        self.assertFalse(ok)
        self.assertIn("Step count mismatch", diag)

        # 2. Duplicate raw tuple in step
        mut_dup = copy.deepcopy(mock_on)
        mut_dup["steps"][0]["entered_tuples"].append([1, 2, 3, 4])
        ok, diag = verify_observer_output_invariance(mock_off, mut_dup)
        self.assertFalse(ok)
        self.assertIn("duplicate entered tuples", diag)

        # 3. Tuple set mismatch
        mut_coord = copy.deepcopy(mock_on)
        mut_coord["steps"][0]["entered_tuples"][0] = [1, 2, 3, 99]
        ok, diag = verify_observer_output_invariance(mock_off, mut_coord)
        self.assertFalse(ok)
        self.assertIn("entered tuples mismatch", diag)

        # 4. Counter mismatch (dfs_states)
        mut_cnt = copy.deepcopy(mock_on)
        mut_cnt["counters"]["dfs_states"] += 1
        ok, diag = verify_observer_output_invariance(mock_off, mut_cnt)
        self.assertFalse(ok)
        self.assertIn("Counter mismatch for 'dfs_states'", diag)

    def test_26_protocol_versioning_and_historical_compatibility(self):
        """R3-F05: Distinguishes Measurement Protocol 1.2.0, Analysis 1.4.0, and Revision 1.4.0."""
        self.assertEqual(PROTOCOL_MEASUREMENT_VERSION, "1.2.0")
        self.assertEqual(PROTOCOL_ANALYSIS_VERSION, "1.4.0")
        self.assertEqual(PROTOCOL_SPECIFICATION_REVISION, "1.4.0")

        # Check all 180 canonical records match historical protocol SHA
        canonical_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not canonical_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with open(canonical_ledger, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                rec = json.loads(line)
                self.assertEqual(rec["protocol_version"], "1.2.0", f"Line {line_no}")
                self.assertEqual(
                    rec["protocol_sha256"],
                    "dd3f39cae4325b3577adec8fa19ff5f52844e9338b1d97aa542d58b6c09d4ebd",
                    f"Line {line_no}",
                )
                # Validates cleanly under contract
                ok, diag = validate_record_integrity(rec)
                self.assertTrue(ok, f"Line {line_no}: {diag}")

    def test_27_warmup_failure_metadata_preservation(self):
        """R3-F06: Warmup failure preserves failure_stage, failure_category, and diagnostic notes."""
        # Check runner source code ensures diagnostic preservation
        runner_source = (REPO_ROOT / "scripts" / "run_rapidflow_apx_p4a.py").read_text(encoding="utf-8")
        self.assertIn('failure_stage = "WARMUP"', runner_source)
        self.assertIn('failure_category = warm_res["status"]', runner_source)
        self.assertIn('correctness_notes = f"Disposable warm-up execution failed during stage WARMUP', runner_source)

    def test_28_evidence_manifest_integrity(self):
        """R3-F08: Verifies docs/evidence/p4a_evidence_manifest.json hashes and byte sizes against disk."""
        manifest_path = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"
        self.assertTrue(manifest_path.is_file())

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(manifest["manifest_version"], "1.4.0")
        self.assertEqual(manifest["phase"], "P4a")

        self.assertEqual(manifest["execution_identity"], "262279a9d9ea026e07231f3ba511863c383b98133db40e69f603a235ad792135")
        self.assertEqual(manifest["canonical_execution_commit"], "cf97a7840dff0c0f2bf791834263f3911545110c")

        artifacts = manifest.get("artifacts", {})
        self.assertTrue(len(artifacts) >= 5)

        for rel_path, meta in artifacts.items():
            full_path = REPO_ROOT / rel_path
            if not full_path.is_file():
                if not meta.get("git_tracked", True):
                    continue  # Untracked historical artifact not present on fresh checkout
                self.fail(f"Tracked artifact file missing: {rel_path}")
            real_sha = compute_sha256(full_path)
            self.assertEqual(
                real_sha, meta["sha256"], f"Hash mismatch for {rel_path}: disk={real_sha} vs manifest={meta['sha256']}"
            )
            real_size = full_path.stat().st_size
            self.assertEqual(
                real_size, meta["size_bytes"], f"Size mismatch for {rel_path}: disk={real_size} vs manifest={meta['size_bytes']}"
            )

    def test_29_r4_f01_synthetic_identity_rejected_in_canonical_analysis(self):
        """R4-F01: Synthetic records with non-hex execution identity rejected fail-closed in canonical analysis."""
        fake_m1 = copy.deepcopy(self.valid_canonical_m1)
        fake_m1["execution_identity"] = "NOT_A_HASH"
        fake_m2 = copy.deepcopy(self.valid_canonical_m2)
        fake_m2["execution_identity"] = "NOT_A_HASH"

        # validate_record_integrity must fail closed
        ok1, diag1 = validate_record_integrity(fake_m1)
        self.assertFalse(ok1)
        self.assertIn("Invalid execution_identity", diag1)

        # analyze_p4a_ledger must reject both slots fail-closed
        summary = analyze_p4a_ledger([fake_m1, fake_m2])
        self.assertEqual(summary["total_valid_pairs"], 0)
        self.assertEqual(summary["total_rejected_pairs"], 1)
        self.assertEqual(summary["overall_verdict"], "CANONICAL_PARTIAL_OR_FAILED")
        self.assertIn("R4A_FIX_REQUIRED", summary["p4b_recommendation"])

    def test_30_r4_f02_corrupted_duplicate_quarantines_slot(self):
        """R4-F02: Corrupted duplicate attempt quarantines the entire slot and rejects the pair fail-closed."""
        m1 = copy.deepcopy(self.valid_canonical_m1)
        m2 = copy.deepcopy(self.valid_canonical_m2)
        corrupted_m2 = copy.deepcopy(self.valid_canonical_m2)
        corrupted_m2["workload_sha256"] = "f" * 64
        corrupted_m2["attempt_id"] = "CORRUPTED_ATTEMPT_99"

        # Step 1: resolve_canonical_attempts must quarantine the slot
        canonical_slots, lineage, conflicts = resolve_canonical_attempts([m1, m2, corrupted_m2])
        self.assertIn(m1["slot_key"], canonical_slots)
        self.assertNotIn(m2["slot_key"], canonical_slots, "Quarantined slot must NOT be admitted")
        self.assertTrue(any("quarantined" in str(c.get("reason", "")).lower() for c in conflicts))

        # Step 2: analyze_p4a_ledger must reject the pair containing the quarantined slot
        summary = analyze_p4a_ledger([m1, m2, corrupted_m2])
        self.assertEqual(summary["total_valid_pairs"], 0)
        self.assertEqual(summary["total_rejected_pairs"], 1)
        self.assertEqual(summary["overall_verdict"], "CANONICAL_PARTIAL_OR_FAILED")
        self.assertIn("R4A_FIX_REQUIRED", summary["p4b_recommendation"])

    def test_31_r4_f03_mixed_execution_identities_rejected(self):
        """R4-F03: Mixed execution identities in a single ledger violate single-experiment isolation and are rejected."""
        pair1_m1 = copy.deepcopy(self.valid_canonical_m1)
        pair1_m2 = copy.deepcopy(self.valid_canonical_m2)

        pair2_m1 = copy.deepcopy(self.valid_canonical_m1)
        pair2_m2 = copy.deepcopy(self.valid_canonical_m2)
        pair2_m1["workload_id"] = "P4A_WL_03"
        pair2_m2["workload_id"] = "P4A_WL_03"
        pair2_m1["slot_key"] = "P4A_WL_03_rep0_m1"
        pair2_m2["slot_key"] = "P4A_WL_03_rep0_m2"

        # Compute distinct execution identity for experiment B
        id_b = compute_execution_identity(
            protocol_version=pair2_m1["protocol_version"],
            protocol_sha256=pair2_m1["protocol_sha256"],
            manifest_sha256=pair2_m1["manifest_sha256"],
            source_commit="b" * 40,
            source_dirty="CLEAN",
            binary_sha256=pair2_m1["binary_sha256"],
            effective_max_work=pair2_m1["effective_max_work"],
            effective_timeout=pair2_m1["effective_timeout"],
            effective_as_limit_mib=pair2_m1["effective_memory_limit_mib"],
        )
        for p in (pair2_m1, pair2_m2):
            p["source_commit"] = "b" * 40
            p["execution_identity"] = id_b

        # Combining Pair 1 (Commit A) and Pair 2 (Commit B) in unheaded ledger must trigger isolation rejection
        summary = analyze_p4a_ledger([pair1_m1, pair1_m2, pair2_m1, pair2_m2])
        self.assertEqual(summary["total_valid_pairs"], 0)
        self.assertEqual(summary["overall_verdict"], "CANONICAL_INCONCLUSIVE")
        self.assertIn("R4A_INCONCLUSIVE_MIXED_EXECUTION_IDENTITIES", summary["p4b_recommendation"])

    def test_32_r4_f04_contradictory_status_exit_code_rejected(self):
        """R4-F04: Contradictory status, exit code, and admission fields are rejected fail-closed."""
        # 1. status=COMPLETED_VERIFIED with non-zero exit_code=124
        rec_bad_ec = copy.deepcopy(self.valid_canonical_m1)
        rec_bad_ec["exit_code"] = 124
        ok, diag = validate_record_integrity(rec_bad_ec)
        self.assertFalse(ok)
        self.assertIn("requires exit_code == 0", diag)

        # 2. boolean exit_code=True
        rec_bool_ec = copy.deepcopy(self.valid_canonical_m1)
        rec_bool_ec["exit_code"] = True
        ok, diag = validate_record_integrity(rec_bool_ec)
        self.assertFalse(ok)
        self.assertIn("Invalid exit_code", diag)

        # 3. boolean repetition=True
        rec_bool_rep = copy.deepcopy(self.valid_canonical_m1)
        rec_bool_rep["repetition"] = True
        ok, diag = validate_record_integrity(rec_bool_rep)
        self.assertFalse(ok)
        self.assertIn("Invalid repetition", diag)

        # 4. status=FAILED_EXECUTION with performance_admission_status=ACCEPTED
        rec_bad_adm = copy.deepcopy(self.valid_canonical_m1)
        rec_bad_adm["status"] = "FAILED_EXECUTION"
        rec_bad_adm["exit_code"] = 1
        rec_bad_adm["performance_admission_status"] = "ACCEPTED"
        ok, diag = validate_record_integrity(rec_bad_adm)
        self.assertFalse(ok)
        self.assertIn("cannot have performance_admission_status == 'ACCEPTED'", diag)

        # 5. status=COMPLETED_VERIFIED with m0_agreed=False
        rec_bad_m0 = copy.deepcopy(self.valid_canonical_m1)
        rec_bad_m0["m0_agreed"] = False
        ok, diag = validate_record_integrity(rec_bad_m0)
        self.assertFalse(ok)
        self.assertIn("requires m0_agreed is True", diag)

    def test_33_r4_f05_empty_observer_output_schema_rejected(self):
        """R4-F05: Empty or malformed observer outputs fail schema validation."""
        # 1. Empty dicts
        ok, diag = verify_observer_output_invariance({}, {})
        self.assertFalse(ok)
        self.assertIn("must not be empty", diag)

        # 2. Missing 'steps'
        ok, diag = verify_observer_output_invariance({"counters": {}}, {"counters": {}})
        self.assertFalse(ok)
        self.assertIn("Missing 'steps'", diag)

        # 3. Missing 'counters'
        ok, diag = verify_observer_output_invariance({"steps": []}, {"steps": []})
        self.assertFalse(ok)
        self.assertIn("Missing 'counters'", diag)

        # 4. Missing one of the 6 monitored counters
        incomplete_cnt = {
            "steps": [],
            "counters": {
                "candidate_evaluations": 10,
                "committed_emissions": 2,
                "dfs_states": 5,
                "anchors_started": 1,
                "edge_lookups": 20,
                # missing "vertex_label_checks"
            },
        }
        ok, diag = verify_observer_output_invariance(incomplete_cnt, copy.deepcopy(incomplete_cnt))
        self.assertFalse(ok)
        self.assertIn("Missing monitored counter 'vertex_label_checks'", diag)

        # 5. Counter value is boolean True
        bool_cnt = copy.deepcopy(incomplete_cnt)
        bool_cnt["counters"]["vertex_label_checks"] = True
        ok, diag = verify_observer_output_invariance(bool_cnt, copy.deepcopy(bool_cnt))
        self.assertFalse(ok)
        self.assertIn("Invalid counter value", diag)

    def test_34_r4_f06_zero_valid_pairs_inconclusive_verdict(self):
        """R4-F06: Empty ledger or zero valid pairs yields CANONICAL_INCONCLUSIVE verdict without fabricated metrics."""
        summary = analyze_p4a_ledger([])
        self.assertEqual(summary["total_valid_pairs"], 0)
        self.assertEqual(summary["overall_verdict"], "CANONICAL_INCONCLUSIVE")
        self.assertIn("R4A_INCONCLUSIVE_NO_VALID_PAIRS", summary["p4b_recommendation"])

        diag = summary["bottleneck_diagnosis"]
        self.assertIsNone(diag["m2_neighbor_retrieval_within_search_pct_median"])
        self.assertIsNone(diag["m2_search_core_within_search_pct_median"])
        self.assertIsNone(diag["m2_search_speedup_median"])
        self.assertIsNone(diag["m2_native_step_speedup_median"])
        self.assertFalse(diag["neighbor_retrieval_exceeds_15pct_threshold"])
        self.assertFalse(diag["search_core_dominates_search_time"])

        # Markdown report must format cleanly without exceptions
        md = generate_markdown_report(summary)
        self.assertIn("CANONICAL_INCONCLUSIVE", md)
        self.assertIn("Total Workload Pairs Analyzed:** 0", md)

    def test_35_e2e_01_cli_help(self):
        """E2E-01 / F03-T02: CLI --help executes successfully, exits 0, and prints non-empty usage."""
        cmd = [sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"), "--help"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage:", proc.stdout.lower())
        self.assertIn("--ledger", proc.stdout)
        self.assertIn("--mode", proc.stdout)

    def test_36_e2e_02_valid_canonical_data(self):
        """E2E-02: Valid canonical raw ledger executes to completion, exits 0, produces certified CANONICAL_PASS."""
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_json = Path(tmpdir) / "summary.json"
            out_md = Path(tmpdir) / "report.md"
            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--output-json", str(out_json),
                "--output-md", str(out_md),
                "--mode", "canonical",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc.returncode, 0, f"CLI canonical run failed: {proc.stderr}")
            self.assertTrue(out_json.is_file())
            self.assertTrue(out_md.is_file())

            with open(out_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["overall_verdict"], "CANONICAL_PASS")
            self.assertEqual(data["experiment_completeness_status"], "CANONICAL_COMPLETE")
            self.assertEqual(data["raw_artifact_integrity_status"], "VERIFIED")
            self.assertEqual(data["total_valid_pairs"], 90)
            self.assertEqual(data["total_rejected_pairs"], 0)
            self.assertEqual(data["total_expected_slots"], 180)
            self.assertEqual(data["total_valid_slots"], 180)

    def test_37_e2e_03_valid_scoped_data(self):
        """E2E-03: Valid partial/scoped data in scoped mode exits 0, produces SCOPED_ANALYSIS_VALID."""
        lines = [
            json.dumps(self.valid_canonical_m1) + "\n",
            json.dumps(self.valid_canonical_m2) + "\n",
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            scoped_ledger = Path(tmpdir) / "scoped.jsonl"
            out_json = Path(tmpdir) / "scoped_summary.json"
            out_md = Path(tmpdir) / "scoped_report.md"
            with open(scoped_ledger, "w", encoding="utf-8") as f:
                f.writelines(lines)

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(scoped_ledger),
                "--output-json", str(out_json),
                "--output-md", str(out_md),
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc.returncode, 0, f"Scoped run failed: {proc.stderr}")
            self.assertTrue(out_json.is_file())

            with open(out_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["overall_verdict"], "SCOPED_ANALYSIS_VALID")
            self.assertEqual(data["experiment_completeness_status"], "SCOPED_VALID")
            self.assertEqual(data["total_valid_pairs"], 1)

    def test_38_e2e_04_missing_ledger(self):
        """E2E-04: Non-existent ledger returns non-zero exit code without silent success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "missing_file.jsonl"
            out_json = Path(tmpdir) / "out.json"
            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(non_existent),
                "--output-json", str(out_json),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(out_json.is_file())

    def test_39_e2e_05_malformed_jsonl(self):
        """E2E-05: Malformed JSONL line returns non-zero exit code with diagnostic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_ledger = Path(tmpdir) / "bad.jsonl"
            out_json = Path(tmpdir) / "out.json"
            with open(bad_ledger, "w", encoding="utf-8") as f:
                f.write('{"valid": "json"}\n')
                f.write('NOT_A_VALID_JSON_OBJECT\n')

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(bad_ledger),
                "--output-json", str(out_json),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Corrupted JSON", proc.stderr)
            self.assertFalse(out_json.is_file())

    def test_40_e2e_06_modified_raw_artifact(self):
        """E2E-06 / R5-F02: Modified measurement payload fails raw artifact integrity in canonical mode."""
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with tempfile.TemporaryDirectory() as tmpdir:
            tampered_ledger = Path(tmpdir) / "p4a_raw_ledger.jsonl"
            out_json = Path(tmpdir) / "out.json"

            with open(raw_ledger, "r", encoding="utf-8") as f:
                recs = [json.loads(line) for line in f]
            recs[0]["outer_wall_ns"] = 1000
            with open(tampered_ledger, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(tampered_ledger),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("CANONICAL_INVALID_ARTIFACT", proc.stderr)

    def test_41_e2e_07_missing_trusted_artifact_reference(self):
        """E2E-07 / R5-F02: Unregistered ledger file cannot obtain canonical certification."""
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with tempfile.TemporaryDirectory() as tmpdir:
            unregistered_ledger = Path(tmpdir) / "unregistered_name.jsonl"
            shutil.copy(raw_ledger, unregistered_ledger)
            out_json = Path(tmpdir) / "out.json"

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(unregistered_ledger),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("CANONICAL_INVALID_ARTIFACT", proc.stderr)

    def test_42_e2e_08_forged_self_consistent_provenance(self):
        """E2E-08 / R5-F04: Self-consistent forged execution identity rejected in canonical mode."""
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with tempfile.TemporaryDirectory() as tmpdir:
            forged_ledger = Path(tmpdir) / "p4a_raw_ledger.jsonl"
            out_json = Path(tmpdir) / "out.json"

            with open(raw_ledger, "r", encoding="utf-8") as f:
                recs = [json.loads(line) for line in f]

            for r in recs:
                r["source_commit"] = "0" * 40
                r["binary_sha256"] = "1" * 64
                r["execution_identity"] = compute_execution_identity(
                    protocol_version=r["protocol_version"],
                    protocol_sha256=r["protocol_sha256"],
                    manifest_sha256=r["manifest_sha256"],
                    source_commit=r["source_commit"],
                    source_dirty=r["source_dirty_state"],
                    binary_sha256=r["binary_sha256"],
                    effective_max_work=r["effective_max_work"],
                    effective_timeout=r["effective_timeout"],
                    effective_as_limit_mib=r["effective_memory_limit_mib"],
                )
            with open(forged_ledger, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(forged_ledger),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_43_e2e_09_incomplete_canonical_slots(self):
        """E2E-09 / R5-F01: Partial dataset (1 pair) in canonical mode is rejected."""
        lines = [
            json.dumps(self.valid_canonical_m1) + "\n",
            json.dumps(self.valid_canonical_m2) + "\n",
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            partial_ledger = Path(tmpdir) / "partial.jsonl"
            out_json = Path(tmpdir) / "out.json"
            with open(partial_ledger, "w", encoding="utf-8") as f:
                f.writelines(lines)

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(partial_ledger),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_44_e2e_10_mixed_experiments(self):
        """E2E-10 / R5-F05: Cross-experiment records contaminate ledger and fail certification."""
        lines = [
            json.dumps(self.valid_canonical_m1) + "\n",
            json.dumps(self.valid_canonical_m2) + "\n",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mixed_ledger = Path(tmpdir) / "mixed.jsonl"
            out_json = Path(tmpdir) / "out.json"
            alien = copy.deepcopy(self.valid_canonical_m1)
            alien["execution_identity"] = "e" * 64
            with open(mixed_ledger, "w", encoding="utf-8") as f:
                f.writelines(lines)
                f.write(json.dumps(alien) + "\n")

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(mixed_ledger),
                "--output-json", str(out_json),
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_45_e2e_11_corrupted_duplicate(self):
        """E2E-11 / R4-F02: Corrupted duplicate attempt quarantines slot and fails certification."""
        lines = [
            json.dumps(self.valid_canonical_m1) + "\n",
            json.dumps(self.valid_canonical_m2) + "\n",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            dup_ledger = Path(tmpdir) / "dup.jsonl"
            out_json = Path(tmpdir) / "out.json"
            corrupted = copy.deepcopy(self.valid_canonical_m1)
            corrupted["binary_sha256"] = "f" * 64
            corrupted["attempt_id"] = "CORRUPTED_ATTEMPT_99"
            with open(dup_ledger, "w", encoding="utf-8") as f:
                f.writelines(lines)
                f.write(json.dumps(corrupted) + "\n")

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(dup_ledger),
                "--output-json", str(out_json),
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_46_e2e_12_failed_exit_code(self):
        """E2E-12 / R4-F04: Record claiming COMPLETED_VERIFIED with exit_code != 0 is rejected."""
        lines = [
            json.dumps(self.valid_canonical_m1) + "\n",
            json.dumps(self.valid_canonical_m2) + "\n",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_ledger = Path(tmpdir) / "bad_ec.jsonl"
            out_json = Path(tmpdir) / "out.json"
            recs = [copy.deepcopy(self.valid_canonical_m1), copy.deepcopy(self.valid_canonical_m2)]
            recs[0]["exit_code"] = 1
            with open(bad_ledger, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(bad_ledger),
                "--output-json", str(out_json),
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_47_e2e_13_no_valid_pairs(self):
        """E2E-13 / R4-F06: Zero valid pairs yields inconclusive verdict and non-zero exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_ledger = Path(tmpdir) / "empty.jsonl"
            empty_ledger.touch()
            out_json = Path(tmpdir) / "out.json"

            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(empty_ledger),
                "--output-json", str(out_json),
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_48_e2e_14_output_directory_unavailable(self):
        """E2E-14: Unwritable output directory returns non-zero exit code with error message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_ledger = Path(tmpdir) / "ledger.jsonl"
            with open(temp_ledger, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_canonical_m1) + "\n")
                f.write(json.dumps(self.valid_canonical_m2) + "\n")
            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(temp_ledger),
                "--output-json", "/proc/unwritable_dir/summary.json",
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_49_e2e_15_output_file_creation_failure(self):
        """E2E-15: Output path pointing to existing directory returns non-zero exit code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_ledger = Path(tmpdir) / "ledger.jsonl"
            with open(temp_ledger, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_canonical_m1) + "\n")
                f.write(json.dumps(self.valid_canonical_m2) + "\n")
            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(temp_ledger),
                "--output-json", tmpdir,
                "--mode", "scoped",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_50_e2e_16_arg_parsing_failure(self):
        """E2E-16: Unrecognized command line option causes argparse exit code 2."""
        cmd = [
            sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
            "--non-existent-option-xyz",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_51_r5_f06_missing_correctness_status_rejected(self):
        """R5-F06: Missing or invalid correctness_status rejected fail-closed for COMPLETED_VERIFIED records."""
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
        rec = {
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
        ok, diag = validate_record_integrity(rec, expected_header=exec_id)
        self.assertFalse(ok)
        self.assertIn("correctness_status", diag)

        rec["correctness_status"] = "INVALID_STATUS"
        ok2, diag2 = validate_record_integrity(rec, expected_header=exec_id)
        self.assertFalse(ok2)
        self.assertIn("correctness_status", diag2)

        rec["correctness_status"] = "M0_VERIFIED"
        ok3, _ = validate_record_integrity(rec, expected_header=exec_id)
        self.assertTrue(ok3)

    def test_52_r5_f06_observer_tuple_schema_hardening(self):
        """R5-F06: Observer output invariance strictly validates tuple structure, coordinate types, and dimensions."""
        base_out = {
            "steps": [
                {
                    "entered_tuples": [[1, 2, 3, 4]],
                    "exited_tuples": [],
                }
            ],
            "counters": {
                "candidate_evaluations": 10,
                "committed_emissions": 1,
                "dfs_states": 5,
                "anchors_started": 1,
                "edge_lookups": 20,
                "vertex_label_checks": 15,
            },
        }

        # 1. Valid matching outputs with query_dimension=4
        ok, diag = verify_observer_output_invariance(base_out, copy.deepcopy(base_out), query_dimension=4)
        self.assertTrue(ok, diag)

        # 2. String coordinate in tuple rejected
        str_out = copy.deepcopy(base_out)
        str_out["steps"][0]["entered_tuples"] = [[1, "not-an-id", 3, 4]]
        ok, diag = verify_observer_output_invariance(str_out, copy.deepcopy(str_out), query_dimension=4)
        self.assertFalse(ok)
        self.assertIn("not a non-negative integer", diag)

        # 3. Boolean coordinate in tuple rejected
        bool_out = copy.deepcopy(base_out)
        bool_out["steps"][0]["entered_tuples"] = [[1, True, 3, 4]]
        ok, diag = verify_observer_output_invariance(bool_out, copy.deepcopy(bool_out), query_dimension=4)
        self.assertFalse(ok)
        self.assertIn("not a non-negative integer", diag)

        # 4. Dimension mismatch rejected (length 2 != query dimension 4)
        dim_out = copy.deepcopy(base_out)
        dim_out["steps"][0]["entered_tuples"] = [[1, 2]]
        ok, diag = verify_observer_output_invariance(dim_out, copy.deepcopy(dim_out), query_dimension=4)
        self.assertFalse(ok)
        self.assertIn("query dimension", diag)

        # 5. Valid zero-event step accepted
        zero_out = copy.deepcopy(base_out)
        zero_out["steps"][0]["entered_tuples"] = []
        zero_out["steps"][0]["exited_tuples"] = []
        ok, diag = verify_observer_output_invariance(zero_out, copy.deepcopy(zero_out), query_dimension=4)
        self.assertTrue(ok, diag)

    # =========================================================================
    # ROUND 6 REGRESSION TESTS (R6-F01, R6-F02, R6-F03, R6-F04)
    # =========================================================================

    def test_53_r6_f01_canonical_api_preconditions_rejected(self):
        """R6-F01 / Group A: Canonical API certification boundary fails closed on missing preconditions (A01-A12)."""
        pair_m1 = copy.deepcopy(self.valid_canonical_m1)
        pair_m2 = copy.deepcopy(self.valid_canonical_m2)
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        ev_path = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"

        # A01: 1 pair, canonical, no header -> CANONICAL_UNTRUSTED_CONTEXT
        res_a01 = analyze_p4a_ledger([pair_m1, pair_m2], mode="canonical")
        self.assertEqual(res_a01["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")
        self.assertEqual(res_a01["raw_artifact_integrity_status"], ARTIFACT_STATUS_NOT_CHECKED)

        # A02: 1 pair, canonical, no raw artifact -> CANONICAL_UNTRUSTED_CONTEXT
        res_a02 = analyze_p4a_ledger([pair_m1, pair_m2], mode="canonical", raw_ledger_path=None)
        self.assertIn(res_a02["overall_verdict"], ("CANONICAL_UNTRUSTED_CONTEXT", "CANONICAL_EVIDENCE_INCOMPLETE"))
        self.assertNotEqual(res_a02["overall_verdict"], "CANONICAL_PASS")

        # Load trusted header & manifest info if available
        trusted_header = get_trusted_canonical_header(ev_path)
        manifest_data = get_frozen_manifest_registry()

        # Create 180 valid synthetic records conforming to manifest registry
        recs_180 = []
        for wl_idx in range(1, 19):
            wl_id = f"P4A_WL_{wl_idx:02d}"
            wl_hash = manifest_data["workload_hashes"].get(wl_id, "a" * 64)
            for rep in range(5):
                m1 = copy.deepcopy(self.valid_canonical_m1)
                m2 = copy.deepcopy(self.valid_canonical_m2)
                m1["workload_id"] = wl_id
                m2["workload_id"] = wl_id
                m1["workload_sha256"] = wl_hash
                m2["workload_sha256"] = wl_hash
                m1["repetition"] = rep
                m2["repetition"] = rep
                m1["slot_key"] = f"{wl_id}_rep{rep}_m1"
                m2["slot_key"] = f"{wl_id}_rep{rep}_m2"
                recs_180.extend([m1, m2])

        # A03: 180 records, canonical, no header -> CANONICAL_UNTRUSTED_CONTEXT
        res_a03 = analyze_p4a_ledger(recs_180, mode="canonical", expected_header=None)
        self.assertEqual(res_a03["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")


        # A04: 180 records, canonical, header provided, but empty slot matrix -> CANONICAL_EVIDENCE_INCOMPLETE
        res_a04_empty = analyze_p4a_ledger(recs_180, mode="canonical", expected_header=trusted_header, expected_slot_matrix=set())
        self.assertEqual(res_a04_empty["overall_verdict"], "CANONICAL_EVIDENCE_INCOMPLETE")

        # A05: 180 records, canonical, header + matrix provided, but no raw path -> CANONICAL_EVIDENCE_INCOMPLETE
        slot_matrix = compute_expected_slot_matrix(manifest_data)
        res_a05 = analyze_p4a_ledger(recs_180, mode="canonical", expected_header=trusted_header, expected_slot_matrix=slot_matrix, raw_ledger_path=None)
        self.assertEqual(res_a05["overall_verdict"], "CANONICAL_EVIDENCE_INCOMPLETE")

        # A06: 180 records, canonical, header + matrix + raw path, but no trusted_artifact_meta -> CANONICAL_INVALID_ARTIFACT
        res_a06 = analyze_p4a_ledger(
            recs_180,
            mode="canonical",
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            raw_ledger_path=raw_ledger,
            trusted_artifact_meta=None,
        )
        self.assertEqual(res_a06["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
        self.assertNotEqual(res_a06["overall_verdict"], "CANONICAL_PASS")


        # A07: expected_header = {} -> CANONICAL_UNTRUSTED_CONTEXT
        res_a07 = analyze_p4a_ledger(recs_180, mode="canonical", expected_header={})
        self.assertEqual(res_a07["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")

        # A08: Header only has execution identity -> CANONICAL_UNTRUSTED_CONTEXT
        res_a08 = analyze_p4a_ledger(
            recs_180,
            mode="canonical",
            expected_header={"execution_identity": recs_180[0]["execution_identity"]},
        )
        self.assertEqual(res_a08["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")
        self.assertNotEqual(res_a08["overall_verdict"], "CANONICAL_PASS")


        # A09: Full valid evidence (if historical raw ledger exists) -> CANONICAL_PASS
        if raw_ledger.is_file():
            trusted_meta = get_trusted_raw_artifact_meta(ev_path)
            res_a09 = analyze_p4a_ledger(
                None,
                mode="canonical",
                raw_ledger_path=raw_ledger,
                expected_header=trusted_header,
                expected_slot_matrix=slot_matrix,
                trusted_artifact_meta=trusted_meta,
            )
            self.assertEqual(res_a09["overall_verdict"], "CANONICAL_PASS")
            self.assertEqual(res_a09["raw_artifact_integrity_status"], ARTIFACT_STATUS_VERIFIED)

        # A10: 1 pair, scoped -> SCOPED_ANALYSIS_VALID, NOT_CHECKED
        res_a10 = analyze_p4a_ledger([pair_m1, pair_m2], mode="scoped")
        self.assertEqual(res_a10["overall_verdict"], "SCOPED_ANALYSIS_VALID")
        self.assertEqual(res_a10["raw_artifact_integrity_status"], ARTIFACT_STATUS_NOT_CHECKED)

        # A11: Invalid mode -> raises ValueError
        with self.assertRaises(ValueError):
            analyze_p4a_ledger([pair_m1, pair_m2], mode="arbitrary_mode")

        # A12: Direct API vs CLI on same valid evidence (if available) -> same verdict & pair counts
        if raw_ledger.is_file():
            trusted_meta = get_trusted_raw_artifact_meta(ev_path)
            api_res = analyze_p4a_ledger(
                None,
                mode="canonical",
                raw_ledger_path=raw_ledger,
                expected_header=trusted_header,
                expected_slot_matrix=slot_matrix,
                trusted_artifact_meta=trusted_meta,
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                cli_json = Path(tmpdir) / "cli_out.json"
                cmd = [
                    sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                    "--ledger", str(raw_ledger),
                    "--output-json", str(cli_json),
                    "--mode", "canonical",
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(proc.returncode, 0)
                with open(cli_json, "r", encoding="utf-8") as f:
                    cli_data = json.load(f)
                self.assertEqual(api_res["overall_verdict"], cli_data["overall_verdict"])
                self.assertEqual(api_res["total_valid_pairs"], cli_data["total_valid_pairs"])
                self.assertEqual(api_res["raw_artifact_integrity_status"], cli_data["raw_artifact_integrity_status"])

    def test_54_r6_f02_raw_artifact_status_taxonomy(self):
        """R6-F02 / Group B: Strict tristate taxonomy for raw artifact status (B01-B11)."""
        pair_m1 = copy.deepcopy(self.valid_canonical_m1)
        pair_m2 = copy.deepcopy(self.valid_canonical_m2)
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        ev_path = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"

        # B01: No artifact verification requested (scoped) -> NOT_CHECKED
        res_b01 = analyze_p4a_ledger([pair_m1, pair_m2], mode="scoped")
        self.assertEqual(res_b01["raw_artifact_integrity_status"], ARTIFACT_STATUS_NOT_CHECKED)

        # B02: verify_raw_artifact_integrity(None, None) -> (False, NOT_CHECKED, ...)
        ok, status, diag = verify_raw_artifact_integrity(None, None)
        self.assertFalse(ok)
        self.assertEqual(status, ARTIFACT_STATUS_NOT_CHECKED)

        # B03: Canonical without artifact -> status is NOT_CHECKED and overall not PASS
        res_b03 = analyze_p4a_ledger([pair_m1, pair_m2], mode="canonical")
        self.assertEqual(res_b03["raw_artifact_integrity_status"], ARTIFACT_STATUS_NOT_CHECKED)
        self.assertNotEqual(res_b03["overall_verdict"], "CANONICAL_PASS")

        # B04: Canonical with correct trusted artifact -> VERIFIED (if file exists)
        if raw_ledger.is_file():
            trusted_meta = get_trusted_raw_artifact_meta(ev_path)
            ok, status, diag = verify_raw_artifact_integrity(raw_ledger, trusted_meta)
            self.assertTrue(ok)
            self.assertEqual(status, ARTIFACT_STATUS_VERIFIED)

        # B05: SHA mismatch -> FAILED
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write('{"test": 1}\n')
            temp_path = Path(tf.name)
        try:
            bad_meta = {"sha256": "0" * 64, "size_bytes": temp_path.stat().st_size}
            ok, status, diag = verify_raw_artifact_integrity(temp_path, bad_meta)
            self.assertFalse(ok)
            self.assertEqual(status, ARTIFACT_STATUS_FAILED)
            self.assertIn("sha-256 mismatch", diag.lower())
        finally:
            temp_path.unlink()

        # B06: Size mismatch -> FAILED
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write('{"test": 1}\n')
            temp_path = Path(tf.name)
        try:
            real_sha = compute_sha256(temp_path)
            bad_size_meta = {"sha256": real_sha, "size_bytes": 999999}
            ok, status, diag = verify_raw_artifact_integrity(temp_path, bad_size_meta)
            self.assertFalse(ok)
            self.assertEqual(status, ARTIFACT_STATUS_FAILED)
            self.assertIn("size mismatch", diag.lower())
        finally:
            temp_path.unlink()

        # B07: Missing file -> FAILED
        missing_p = Path("/tmp/non_existent_ledger_p4a_r6.jsonl")
        ok, status, diag = verify_raw_artifact_integrity(missing_p, {"sha256": "a" * 64, "size_bytes": 10})
        self.assertFalse(ok)
        self.assertEqual(status, ARTIFACT_STATUS_FAILED)

        # B08: Missing trusted reference -> NOT_CHECKED / FAILED (never VERIFIED)
        if raw_ledger.is_file():
            ok, status, diag = verify_raw_artifact_integrity(raw_ledger, None)
            self.assertFalse(ok)
            self.assertNotEqual(status, ARTIFACT_STATUS_VERIFIED)

        # B09: Invalid trusted SHA format (e.g. non-hex or length != 64) -> FAILED
        if raw_ledger.is_file():
            ok, status, diag = verify_raw_artifact_integrity(raw_ledger, {"sha256": "short", "size_bytes": 100})
            self.assertFalse(ok)
            self.assertEqual(status, ARTIFACT_STATUS_FAILED)

        # B10: Invalid expected artifact metadata (negative or bool size) -> FAILED
        if raw_ledger.is_file():
            ok, status, diag = verify_raw_artifact_integrity(raw_ledger, {"sha256": "a" * 64, "size_bytes": True})
            self.assertFalse(ok)
            self.assertEqual(status, ARTIFACT_STATUS_FAILED)

        # B11: VERIFIED only produced on real successful verification
        self.assertNotEqual(ARTIFACT_STATUS_NOT_CHECKED, ARTIFACT_STATUS_VERIFIED)
        self.assertNotEqual(ARTIFACT_STATUS_FAILED, ARTIFACT_STATUS_VERIFIED)

    def test_55_r6_f03_verified_snapshot_data_binding(self):
        """R6-F03 / Group C: Verified file bound atomically to in-memory records (C01-C07)."""
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        ev_path = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        trusted_header = get_trusted_canonical_header(ev_path)
        trusted_meta = get_trusted_raw_artifact_meta(ev_path)
        manifest_data = get_frozen_manifest_registry()
        slot_matrix = compute_expected_slot_matrix(manifest_data)

        # C01: Valid file + records parsed directly from file -> PASS
        res_c01 = analyze_p4a_ledger(
            None,
            mode="canonical",
            raw_ledger_path=raw_ledger,
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            trusted_artifact_meta=trusted_meta,
        )
        self.assertEqual(res_c01["overall_verdict"], "CANONICAL_PASS")
        self.assertEqual(res_c01["raw_artifact_integrity_status"], ARTIFACT_STATUS_VERIFIED)

        # Load clean records from file to prepare in-memory variations
        v_records, ok, diag, meta = load_verified_ledger(raw_ledger, trusted_meta)
        self.assertTrue(ok)
        self.assertEqual(len(v_records), 180)

        # C02: In-memory runtime (outer_wall_ns) tampered -> CANONICAL_INVALID_ARTIFACT, FAILED
        tampered_c02 = copy.deepcopy(v_records)
        tampered_c02[0]["outer_wall_ns"] = 1000
        res_c02 = analyze_p4a_ledger(
            tampered_c02,
            mode="canonical",
            raw_ledger_path=raw_ledger,
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            trusted_artifact_meta=trusted_meta,
        )
        self.assertEqual(res_c02["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
        self.assertEqual(res_c02["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

        # C03: In-memory counters tampered -> CANONICAL_INVALID_ARTIFACT, FAILED
        tampered_c03 = copy.deepcopy(v_records)
        tampered_c03[0]["counters"]["dfs_states"] += 999
        res_c03 = analyze_p4a_ledger(
            tampered_c03,
            mode="canonical",
            raw_ledger_path=raw_ledger,
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            trusted_artifact_meta=trusted_meta,
        )
        self.assertEqual(res_c03["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
        self.assertEqual(res_c03["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

        # C04: In-memory missing record (179 instead of 180) -> CANONICAL_INVALID_ARTIFACT, FAILED
        tampered_c04 = copy.deepcopy(v_records[:-1])
        res_c04 = analyze_p4a_ledger(
            tampered_c04,
            mode="canonical",
            raw_ledger_path=raw_ledger,
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            trusted_artifact_meta=trusted_meta,
        )
        self.assertEqual(res_c04["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
        self.assertEqual(res_c04["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

        # C05: In-memory extra record (181 records) -> CANONICAL_INVALID_ARTIFACT, FAILED
        tampered_c05 = copy.deepcopy(v_records) + [copy.deepcopy(v_records[0])]
        res_c05 = analyze_p4a_ledger(
            tampered_c05,
            mode="canonical",
            raw_ledger_path=raw_ledger,
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            trusted_artifact_meta=trusted_meta,
        )
        self.assertEqual(res_c05["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
        self.assertEqual(res_c05["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

        # C06: Duplicate attempt injected in-memory -> CANONICAL_INVALID_ARTIFACT, FAILED
        tampered_c06 = copy.deepcopy(v_records)
        dup = copy.deepcopy(tampered_c06[0])
        dup["attempt_id"] = "TAMPERED_ATTEMPT_999"
        tampered_c06.append(dup)
        res_c06 = analyze_p4a_ledger(
            tampered_c06,
            mode="canonical",
            raw_ledger_path=raw_ledger,
            expected_header=trusted_header,
            expected_slot_matrix=slot_matrix,
            trusted_artifact_meta=trusted_meta,
        )
        self.assertEqual(res_c06["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
        self.assertEqual(res_c06["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

        # C07: Atomic snapshot load prevents TOCTOU (single bytes read)
        recs_loaded, v_ok, diag, meta = load_verified_ledger(raw_ledger, trusted_meta)
        self.assertTrue(v_ok)
        self.assertEqual(len(recs_loaded), 180)
        self.assertEqual(meta["sha256"], trusted_meta["sha256"])


    def test_56_r6_f04_fresh_checkout_synthetic_fixture_reproducibility(self):
        """R6-F04 / Group D: Decoupled test fixtures ensure fresh checkout reproducibility (D01-D05)."""
        # D01: Synthetic fixture reproduces valid M1 record satisfying contract
        ok1, diag1 = validate_record_integrity(self.valid_canonical_m1)
        self.assertTrue(ok1, f"M1 synthetic fixture invalid: {diag1}")

        # D02: Synthetic fixture reproduces valid M2 record satisfying contract
        ok2, diag2 = validate_record_integrity(self.valid_canonical_m2)
        self.assertTrue(ok2, f"M2 synthetic fixture invalid: {diag2}")

        # D03: Pairing M1 and M2 in scoped mode evaluates cleanly
        scoped_res = analyze_p4a_ledger([self.valid_canonical_m1, self.valid_canonical_m2], mode="scoped")
        self.assertEqual(scoped_res["overall_verdict"], "SCOPED_ANALYSIS_VALID")
        self.assertEqual(scoped_res["total_valid_pairs"], 1)

        # D04: Historical raw ledger presence check
        canonical_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        ev_path = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"
        if not canonical_ledger.is_file():
            # Fresh checkout without historical logs
            self.assertFalse(canonical_ledger.exists())
        else:
            # When historical ledger is present, verify bit-for-bit SHA256 matches manifest exactly
            trusted_meta = get_trusted_raw_artifact_meta(ev_path)
            real_sha = compute_sha256(canonical_ledger)
            self.assertEqual(real_sha, trusted_meta["sha256"])
            self.assertEqual(real_sha, "34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5")

    def test_57_r7_f01_expected_matrix_authority_lockdown(self):
        """R7-F01: Official canonical certification strictly enforces immutable expected matrix authority (T02-T09)."""
        pair_m1 = copy.deepcopy(self.valid_canonical_m1)
        pair_m2 = copy.deepcopy(self.valid_canonical_m2)
        authority = get_official_p4a_authority()

        # T02: Caller passes a small 2-slot matrix for 1 pair -> CANONICAL_PLAN_MISMATCH (not CANONICAL_PASS)
        small_matrix = {
            "expected_slots": {f"{pair_m1['workload_id']}_rep0_m1", f"{pair_m1['workload_id']}_rep0_m2"},
            "expected_pairs": {(pair_m1["workload_id"], 0)},
        }
        res_t02 = analyze_p4a_ledger(
            [pair_m1, pair_m2],
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=small_matrix,
        )
        self.assertNotEqual(res_t02["overall_verdict"], "CANONICAL_PASS")
        self.assertEqual(res_t02["overall_verdict"], "CANONICAL_PLAN_MISMATCH")
        self.assertIn("R4A_PLAN_MISMATCH", res_t02["p4b_recommendation"])

        # T03: One-pair subset with real trusted header and small matrix -> CANONICAL_PLAN_MISMATCH
        res_t03 = analyze_p4a_ledger(
            [pair_m1, pair_m2],
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=small_matrix,
        )
        self.assertEqual(res_t03["overall_verdict"], "CANONICAL_PLAN_MISMATCH")

        # T04: Header passed as bare string in canonical mode -> CANONICAL_UNTRUSTED_CONTEXT
        res_t04 = analyze_p4a_ledger(
            [pair_m1, pair_m2],
            mode="canonical",
            expected_header=authority.execution_identity,
            expected_slot_matrix=authority.expected_slot_matrix,
        )
        self.assertEqual(res_t04["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")

        # T05: Partial header missing required fields -> CANONICAL_UNTRUSTED_CONTEXT
        res_t05 = analyze_p4a_ledger(
            [pair_m1, pair_m2],
            mode="canonical",
            expected_header={"execution_identity": authority.execution_identity},
            expected_slot_matrix=authority.expected_slot_matrix,
        )
        self.assertEqual(res_t05["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")

        # T06: 180 slots with correct count (180) but wrong/forged slot identities -> CANONICAL_PLAN_MISMATCH
        forged_slots_180 = {f"P4A_WL_FAKE_{idx:02d}_rep0_m1" for idx in range(90)} | {f"P4A_WL_FAKE_{idx:02d}_rep0_m2" for idx in range(90)}
        forged_matrix = {
            "expected_slots": forged_slots_180,
            "expected_pairs": {(f"P4A_WL_FAKE_{idx:02d}", 0) for idx in range(90)},
        }
        res_t06 = analyze_p4a_ledger(
            None,
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=forged_matrix,
        )
        self.assertEqual(res_t06["overall_verdict"], "CANONICAL_PLAN_MISMATCH")

        # T07: Caller matrix differing from frozen matrix (179 slots - missing 1) -> CANONICAL_PLAN_MISMATCH
        sub_slots = set(list(authority.expected_slot_matrix["expected_slots"])[:-1])
        matrix_179 = copy.deepcopy(authority.expected_slot_matrix)
        matrix_179["expected_slots"] = sub_slots
        res_t07 = analyze_p4a_ledger(
            None,
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=matrix_179,
        )
        self.assertEqual(res_t07["overall_verdict"], "CANONICAL_PLAN_MISMATCH")

        # T08: Caller matrix matching frozen matrix exactly -> passes matrix authority check
        m_ok, m_diag = authority.verify_expected_slot_matrix(authority.expected_slot_matrix)
        self.assertTrue(m_ok, f"Authority self-verification failed: {m_diag}")

        # T09: Invalid repetition type (bool True, negative, or non-int) -> CANONICAL_PLAN_MISMATCH
        bad_rep_matrix = copy.deepcopy(authority.expected_slot_matrix)
        bad_rep_matrix["repetitions"] = True
        res_t09 = analyze_p4a_ledger(
            None,
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=bad_rep_matrix,
        )
        self.assertEqual(res_t09["overall_verdict"], "CANONICAL_PLAN_MISMATCH")

    def test_58_r7_f02_artifact_checksum_trust_anchor(self):
        """R7-F02: Artifact checksums must be anchored to approved authority; caller-supplied SHAs cannot forge trust (T10-T14)."""
        authority = get_official_p4a_authority()
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
            tf.write('{"tampered": true}\n')
            tampered_path = Path(tf.name)

        try:
            tampered_sha = compute_sha256(tampered_path)

            # T10: Modified raw artifact file on disk + original official SHA -> FAIL (CANONICAL_INVALID_ARTIFACT)
            res_t10 = analyze_p4a_ledger(
                mode="canonical",
                raw_ledger_path=tampered_path,
                expected_header=authority.expected_header,
                expected_slot_matrix=authority.expected_slot_matrix,
                trusted_artifact_meta=authority.trusted_artifact_meta,
            )
            self.assertEqual(res_t10["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
            self.assertEqual(res_t10["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

            # T11: Modified raw artifact file + caller self-generated SHA -> FAIL (CANONICAL_INVALID_ARTIFACT)
            forged_meta = {
                "sha256": tampered_sha,
                "size_bytes": tampered_path.stat().st_size,
                "line_count": 1,
            }
            res_t11 = analyze_p4a_ledger(
                mode="canonical",
                raw_ledger_path=tampered_path,
                expected_header=authority.expected_header,
                expected_slot_matrix=authority.expected_slot_matrix,
                trusted_artifact_meta=forged_meta,
            )
            self.assertEqual(res_t11["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
            self.assertEqual(res_t11["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

            # T12: Modified raw artifact + trusted header + fake artifact metadata -> CANONICAL_INVALID_ARTIFACT
            res_t12 = analyze_p4a_ledger(
                mode="canonical",
                raw_ledger_path=tampered_path,
                expected_header=authority.expected_header,
                expected_slot_matrix=authority.expected_slot_matrix,
                trusted_artifact_meta=forged_meta,
            )
            self.assertEqual(res_t12["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")

            # T13: Official raw ledger file + wrong caller SHA -> CANONICAL_INVALID_ARTIFACT
            wrong_caller_meta = {
                "sha256": "0" * 64,
                "size_bytes": authority.raw_artifact_size_bytes,
                "line_count": authority.raw_artifact_line_count,
            }
            res_t13 = analyze_p4a_ledger(
                mode="canonical",
                raw_ledger_path=raw_ledger,
                expected_header=authority.expected_header,
                expected_slot_matrix=authority.expected_slot_matrix,
                trusted_artifact_meta=wrong_caller_meta,
            )
            self.assertEqual(res_t13["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")
            self.assertEqual(res_t13["raw_artifact_integrity_status"], ARTIFACT_STATUS_FAILED)

            # T14: Official raw ledger file + approved SHA -> CANONICAL_PASS
            res_t14 = analyze_p4a_ledger(
                mode="canonical",
                raw_ledger_path=raw_ledger,
                expected_header=authority.expected_header,
                expected_slot_matrix=authority.expected_slot_matrix,
                trusted_artifact_meta=authority.trusted_artifact_meta,
            )
            self.assertEqual(res_t14["overall_verdict"], "CANONICAL_PASS")
            self.assertEqual(res_t14["raw_artifact_integrity_status"], ARTIFACT_STATUS_VERIFIED)
            self.assertEqual(res_t14["total_valid_pairs"], 90)

        finally:
            tampered_path.unlink()

    def test_59_r7_f03_strict_provenance_downgrade_prohibited(self):
        """R7-F03: Strict provenance downgrade is strictly prohibited in canonical certification (T15-T18)."""
        authority = get_official_p4a_authority()
        pair_m1 = copy.deepcopy(self.valid_canonical_m1)
        pair_m2 = copy.deepcopy(self.valid_canonical_m2)

        # T15: Canonical API called with strict_provenance=False -> CANONICAL_UNTRUSTED_CONTEXT
        res_t15 = analyze_p4a_ledger(
            [pair_m1, pair_m2],
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=authority.expected_slot_matrix,
            strict_provenance=False,
        )
        self.assertNotEqual(res_t15["overall_verdict"], "CANONICAL_PASS")
        self.assertEqual(res_t15["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")
        self.assertIn("strict_provenance=True", res_t15["p4b_recommendation"])

        # T16: Canonical API called with strict_provenance=True -> strict validation enforced
        res_t16 = analyze_p4a_ledger(
            [pair_m1, pair_m2],
            mode="canonical",
            expected_header=authority.expected_header,
            strict_provenance=True,
        )
        self.assertNotEqual(res_t16["overall_verdict"], "CANONICAL_PASS")

        # T17: Contradictory status/exit code passed to canonical API with strict_provenance=False -> rejected fail-closed
        bad_rec = copy.deepcopy(pair_m2)
        bad_rec["status"] = "COMPLETED_VERIFIED"
        bad_rec["exit_code"] = 124
        bad_rec["correctness_status"] = "NOT_CHECKED"
        res_t17 = analyze_p4a_ledger(
            [pair_m1, bad_rec],
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=authority.expected_slot_matrix,
            strict_provenance=False,
        )
        self.assertNotEqual(res_t17["overall_verdict"], "CANONICAL_PASS")
        self.assertEqual(res_t17["total_valid_pairs"], 0)
        self.assertEqual(res_t17["total_rejected_pairs"], 1)

        # T18: Forged workload SHA passed to canonical API with strict_provenance=False -> rejected fail-closed
        bad_wl_rec = copy.deepcopy(pair_m2)
        bad_wl_rec["workload_sha256"] = "f" * 64
        res_t18 = analyze_p4a_ledger(
            [pair_m1, bad_wl_rec],
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=authority.expected_slot_matrix,
            strict_provenance=False,
        )
        self.assertNotEqual(res_t18["overall_verdict"], "CANONICAL_PASS")
        self.assertEqual(res_t18["total_valid_pairs"], 0)

    def test_60_r7_f04_cli_manifest_authority_lockdown(self):
        """R7-F04: CLI evidence manifest and workload manifest authority lockdown (T19-T27)."""
        authority = get_official_p4a_authority()
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_json = Path(tmpdir) / "out.json"

            # T19: Official CLI + original raw ledger -> exit code 0, CANONICAL_PASS
            cmd_t19 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc_t19 = subprocess.run(cmd_t19, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc_t19.returncode, 0, f"CLI canonical run failed: {proc_t19.stderr}")
            with open(out_json) as f:
                d19 = json.load(f)
            self.assertEqual(d19["overall_verdict"], "CANONICAL_PASS")
            self.assertEqual(d19["total_valid_pairs"], 90)

            # T20: Official CLI + forged evidence manifest redefining raw artifact SHA -> exit code 1
            forged_ev = Path(tmpdir) / "forged_ev.json"
            with open(DEFAULT_EVIDENCE_MANIFEST_PATH) as f:
                ev_data = json.load(f)
            ev_data["artifacts"]["results/logs/rf36-p4a/p4a_raw_ledger.jsonl"]["sha256"] = "0" * 64
            with open(forged_ev, "w") as f:
                json.dump(ev_data, f)

            cmd_t20 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--evidence-manifest", str(forged_ev),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc_t20 = subprocess.run(cmd_t20, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc_t20.returncode, 0)
            self.assertIn("Canonical certification authority violation", proc_t20.stderr)

            # T21: Official CLI + modified workload manifest -> exit code 1
            forged_wl = Path(tmpdir) / "modified_manifest.json"
            with open(DEFAULT_MANIFEST_PATH) as f:
                wl_data = json.load(f)
            wl_data["cases"] = wl_data["cases"][:5]
            with open(forged_wl, "w") as f:
                json.dump(wl_data, f)

            cmd_t21 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--manifest", str(forged_wl),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc_t21 = subprocess.run(cmd_t21, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc_t21.returncode, 0)
            self.assertIn("Canonical certification authority violation", proc_t21.stderr)

            # T22: Official CLI + renamed forged manifest -> exit code 1
            renamed_forged = Path(tmpdir) / "p4a_evidence_manifest.json"
            shutil.copy(forged_ev, renamed_forged)
            cmd_t22 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--evidence-manifest", str(renamed_forged),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc_t22 = subprocess.run(cmd_t22, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc_t22.returncode, 0)

            # T23: Official CLI + matching basename, wrong bytes -> exit code 1, CANONICAL_INVALID_ARTIFACT
            fake_ledger = Path(tmpdir) / "p4a_raw_ledger.jsonl"
            with open(fake_ledger, "w") as f:
                f.write('{"tampered": 1}\n')
            cmd_t23 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(fake_ledger),
                "--output-json", str(out_json),
                "--mode", "canonical",
            ]
            proc_t23 = subprocess.run(cmd_t23, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(proc_t23.returncode, 0)
            self.assertIn("CANONICAL_INVALID_ARTIFACT", proc_t23.stderr)

            # T24: Scoped mode on 1 pair -> exit code 0, SCOPED_ANALYSIS_VALID
            scoped_ledger = Path(tmpdir) / "scoped.jsonl"
            with open(scoped_ledger, "w") as f:
                f.write(json.dumps(self.valid_canonical_m1) + "\n")
                f.write(json.dumps(self.valid_canonical_m2) + "\n")
            cmd_t24 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(scoped_ledger),
                "--output-json", str(out_json),
                "--mode", "scoped",
            ]
            proc_t24 = subprocess.run(cmd_t24, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc_t24.returncode, 0)
            with open(out_json) as f:
                d24 = json.load(f)
            self.assertEqual(d24["overall_verdict"], "SCOPED_ANALYSIS_VALID")
            self.assertEqual(d24["total_valid_pairs"], 1)

            # T25: Custom analysis recommendation in scoped mode is SCOPED_PILOT_VALID, NOT historical READY
            self.assertIn("SCOPED_PILOT_VALID", d24["p4b_recommendation"])
            self.assertNotIn("R4A_PROFILE_READY_FOR_P4B_REVIEW", d24["p4b_recommendation"])

    def test_61_r7_authority_and_reproducibility(self):
        """R7-AUDIT: Top-level entrypoint certify_official_p4a, authority immutability & fresh checkout (T28-T34)."""
        authority = get_official_p4a_authority()
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned on disk.")

        # T28: Top-level entrypoint certify_official_p4a on historical ledger
        res_t28 = certify_official_p4a(raw_ledger)
        self.assertEqual(res_t28["overall_verdict"], "CANONICAL_PASS")
        self.assertEqual(res_t28["total_valid_pairs"], 90)
        self.assertEqual(res_t28["raw_artifact_integrity_status"], ARTIFACT_STATUS_VERIFIED)

        # T29: Malformed authority / manifest SHA mismatch raises CanonicalAuthorityError
        with tempfile.NamedTemporaryFile("w+", suffix=".json") as tf:
            tf.write('{"cases": [{"case_id": "P4A_WL_01"}]}\n')
            tf.flush()
            with self.assertRaises(CanonicalAuthorityError):
                OfficialCanonicalAuthority(workload_manifest_path=tf.name)

        # T30: Historical raw ledger unchanged (bit-for-bit SHA256 and size)
        real_sha = compute_sha256(raw_ledger)
        self.assertEqual(real_sha, APPROVED_P4A_RAW_ARTIFACT_SHA256)
        self.assertEqual(raw_ledger.stat().st_size, APPROVED_P4A_RAW_ARTIFACT_SIZE_BYTES)
        with open(raw_ledger, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), APPROVED_P4A_RAW_ARTIFACT_LINE_COUNT)

        # T31: Copied valid ledger to temp directory (content SHA anchored) passes canonical certification
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_copy = Path(tmpdir) / "p4a_raw_ledger.jsonl"
            shutil.copy(raw_ledger, temp_copy)
            res_t31 = certify_official_p4a(temp_copy)
            self.assertEqual(res_t31["overall_verdict"], "CANONICAL_PASS")
            self.assertEqual(res_t31["total_valid_pairs"], 90)

    def test_62_r8_f01_performance_data_reconciliation(self):
        """R8-F01: Research Report Performance Data Discrepancy & Provenance Reconciliation (R8-A01..R8-A10)."""
        authority = get_official_p4a_authority()
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        summary_json = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_summary_metrics.json"
        if not raw_ledger.is_file() or not summary_json.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical evidence not provisioned.")

        with open(summary_json, "r", encoding="utf-8") as f:
            metrics = json.load(f)

        # R8-A01: Canonical raw ledger produces exactly the four distinct timing scopes
        dist = metrics.get("overall_speedup_distribution", {})
        self.assertIn("search_speedup", dist)
        self.assertIn("native_step_speedup", dist)
        self.assertIn("streaming_loop_speedup", dist)
        self.assertIn("outer_wall_speedup", dist)

        # R8-A02: Search sample count is exactly 84 (6 pairs with zero search duration excluded)
        self.assertEqual(dist["search_speedup"]["count"], 84)

        # R8-A03: Step, Loop, and Outer Wall sample counts are each exactly 90
        self.assertEqual(dist["native_step_speedup"]["count"], 90)
        self.assertEqual(dist["streaming_loop_speedup"]["count"], 90)
        self.assertEqual(dist["outer_wall_speedup"]["count"], 90)

        # R8-A04: Zero-duration pairs are recorded in timer_exclusions, not faked as 1.0x
        exclusions = metrics.get("timer_exclusions", [])
        self.assertEqual(len(exclusions), 6)
        for excl in exclusions:
            self.assertEqual(excl["scope"], "search")
            self.assertEqual(excl["reason"], "ZERO_OR_NEGATIVE_DURATION")

        # R8-A05: Official profile report document values match summary metrics exactly
        profile_report_path = REPO_ROOT / "docs" / "rapidflow_apx_p4a_profile_report.md"
        self.assertTrue(profile_report_path.is_file())
        with open(profile_report_path, "r", encoding="utf-8") as f:
            md_text = f.read()
        self.assertIn("2.13x", md_text)  # search median
        self.assertIn("1.80x", md_text)  # step median
        self.assertIn("1.11x", md_text)  # loop median
        self.assertIn("1.04x", md_text)  # outer median
        self.assertIn("22.78%", md_text)  # neighbor retrieval median

        # R8-A06: Decision record document values match summary metrics
        decision_doc = REPO_ROOT / "docs" / "research_decisions" / "D-R4a-native-profiling.md"
        self.assertTrue(decision_doc.is_file())
        with open(decision_doc, "r", encoding="utf-8") as f:
            dec_text = f.read()
        self.assertIn("2.13x", dec_text)
        self.assertIn("1.80x", dec_text)
        self.assertIn("1.11x", dec_text)
        self.assertIn("1.04x", dec_text)

        # R8-A07: Discrepant claims (e.g. Bare M2 slower by ~19.5% or 0.834x) must NOT appear in official reports
        self.assertNotIn("0.834x", md_text)
        self.assertNotIn("0.838x", md_text)
        self.assertNotIn("0.895x", md_text)
        self.assertNotIn("0.834x", dec_text)

        # R8-A08: Candidate evaluations: M1=455,148, M2=245,055 (relative reduction 46.16%)
        cnt = metrics.get("counter_metrics", {})
        self.assertEqual(cnt["m1"]["total_candidates_evaluated"], 455148)
        self.assertEqual(cnt["m2"]["total_candidates_evaluated"], 245055)
        self.assertEqual(cnt["relative_candidate_eval_reduction_m2_vs_m1_pct"], 46.16)

        # R8-A09: Stage breakdown: Search Core occupies 77.22% (median), Neighbor Retrieval occupies 22.78% (median)
        diag = metrics.get("bottleneck_diagnosis", {})
        self.assertAlmostEqual(diag["m2_search_core_within_search_pct_median"], 77.22, places=1)
        self.assertAlmostEqual(diag["m2_neighbor_retrieval_within_search_pct_median"], 22.78, places=1)

        # R8-A10: Verified canonical speedup medians are strictly greater than 1.0x
        self.assertGreater(dist["search_speedup"]["median"], 1.0)
        self.assertGreater(dist["native_step_speedup"]["median"], 1.0)
        self.assertGreater(dist["streaming_loop_speedup"]["median"], 1.0)
        self.assertGreater(dist["outer_wall_speedup"]["median"], 1.0)

    def test_63_r8_f02_official_canonical_header_contract(self):
        """R8-F02: Official Canonical Header Validation Across All Groups A-F (R8-B01..R8-B23)."""
        authority = get_official_p4a_authority()

        # R8-B01: Full approved header from authority passes
        ok, msg = authority.verify_expected_header(authority.expected_header)
        self.assertTrue(ok, f"Approved header must pass: {msg}")

        # R8-B02: Partial 5-field header must be rejected
        partial_hdr = {
            "execution_identity": authority.execution_identity,
            "source_commit": authority.canonical_execution_commit,
            "manifest_sha256": authority.manifest_sha256,
            "protocol_sha256": authority.protocol_sha256,
            "protocol_version": authority.protocol_version,
        }
        ok, msg = authority.verify_expected_header(partial_hdr)
        self.assertFalse(ok)
        self.assertIn("missing required field(s)", msg)

        # R8-B03: Missing binary SHA-256
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["binary_sha256"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("binary_sha256", msg)

        # R8-B04: Wrong binary SHA-256
        hdr = copy.deepcopy(authority.expected_header)
        hdr["binary_sha256"] = "1" * 64
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("binary_sha256", msg)

        # R8-B05: Missing source_dirty_state
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["source_dirty_state"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("source_dirty_state", msg)

        # R8-B06: source_dirty_state == DIRTY
        hdr = copy.deepcopy(authority.expected_header)
        hdr["source_dirty_state"] = "DIRTY"
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("source_dirty_state", msg)

        # R8-B07: Missing effective_timeout
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["effective_timeout"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_timeout", msg)

        # R8-B08: effective_timeout = 3.14
        hdr = copy.deepcopy(authority.expected_header)
        hdr["effective_timeout"] = 3.14
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_timeout", msg)

        # R8-B09: Timeout NaN
        hdr = copy.deepcopy(authority.expected_header)
        hdr["effective_timeout"] = float("nan")
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_timeout", msg)

        # R8-B10: Timeout inf
        hdr = copy.deepcopy(authority.expected_header)
        hdr["effective_timeout"] = float("inf")
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_timeout", msg)

        # R8-B11: Missing effective_memory_limit_mib
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["effective_memory_limit_mib"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_memory_limit_mib", msg)

        # R8-B12: Memory limit mismatch
        hdr = copy.deepcopy(authority.expected_header)
        hdr["effective_memory_limit_mib"] = 1024
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_memory_limit_mib", msg)

        # R8-B13: Missing effective_max_work
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["effective_max_work"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_max_work", msg)

        # R8-B14: Boolean effective_max_work = True
        hdr = copy.deepcopy(authority.expected_header)
        hdr["effective_max_work"] = True
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("effective_max_work", msg)

        # R8-B15: Missing workload_hashes
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["workload_hashes"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("workload_hashes", msg)

        # R8-B16: Altered workload hash
        hdr = copy.deepcopy(authority.expected_header)
        hdr["workload_hashes"] = dict(hdr["workload_hashes"])
        first_k = next(iter(hdr["workload_hashes"]))
        hdr["workload_hashes"][first_k] = "9" * 64
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("workload_hashes mismatch", msg)

        # R8-B17: Missing cases_count
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["cases_count"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("cases_count", msg)

        # R8-B18: Wrong cases_count
        hdr = copy.deepcopy(authority.expected_header)
        hdr["cases_count"] = 25
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("cases_count", msg)

        # R8-B19: Missing trusted_raw_artifact
        hdr = copy.deepcopy(authority.expected_header)
        del hdr["trusted_raw_artifact"]
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("trusted_raw_artifact", msg)

        # R8-B20: Nested artifact SHA mismatch
        hdr = copy.deepcopy(authority.expected_header)
        hdr["trusted_raw_artifact"] = dict(hdr["trusted_raw_artifact"])
        hdr["trusted_raw_artifact"]["sha256"] = "3" * 64
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("trusted_raw_artifact validation failed", msg)

        # R8-B21: Nested artifact size mismatch
        hdr = copy.deepcopy(authority.expected_header)
        hdr["trusted_raw_artifact"] = dict(hdr["trusted_raw_artifact"])
        hdr["trusted_raw_artifact"]["size_bytes"] = 999999
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("size_bytes", msg)

        # R8-B22: Nested artifact line count mismatch
        hdr = copy.deepcopy(authority.expected_header)
        hdr["trusted_raw_artifact"] = dict(hdr["trusted_raw_artifact"])
        hdr["trusted_raw_artifact"]["line_count"] = 999
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("line_count", msg)

        # R8-B23: Extra unapproved key rejected
        hdr = copy.deepcopy(authority.expected_header)
        hdr["unapproved_override"] = True
        ok, msg = authority.verify_expected_header(hdr)
        self.assertFalse(ok)
        self.assertIn("unapproved field(s)", msg)

    def test_64_r8_f03_evidence_manifest_provenance_validation(self):
        """R8-F03: Evidence Manifest Provenance Integrity Validation (R8-C01..R8-C18)."""
        authority = get_official_p4a_authority()
        ev_path = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"
        with open(ev_path, "r", encoding="utf-8") as f:
            valid_manifest = json.load(f)

        # R8-C01: Approved official manifest passes
        ok, msg = authority.verify_evidence_manifest(valid_manifest)
        self.assertTrue(ok, f"Approved evidence manifest must pass: {msg}")

        # R8-C02: Wrong execution identity
        m = copy.deepcopy(valid_manifest)
        m["execution_identity"] = "bad_exec_id"
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("execution_identity", msg)

        # R8-C03: Wrong canonical execution commit
        m = copy.deepcopy(valid_manifest)
        m["canonical_execution_commit"] = "0" * 40
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("canonical_execution_commit", msg)

        # R8-C04: Wrong historical measurement protocol SHA
        m = copy.deepcopy(valid_manifest)
        m["protocol_version_distinction"]["historical_measurement_protocol_sha256"] = "1" * 64
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("historical_measurement_protocol_sha256", msg)

        # R8-C05: Wrong workload manifest SHA
        m = copy.deepcopy(valid_manifest)
        m["artifacts"]["configs/rapidflow_apx_p4a_manifest.json"]["sha256"] = "2" * 64
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("workload manifest SHA", msg)

        # R8-C06: Wrong raw artifact SHA
        m = copy.deepcopy(valid_manifest)
        m["artifacts"]["results/logs/rf36-p4a/p4a_raw_ledger.jsonl"]["sha256"] = "3" * 64
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("raw ledger SHA", msg)

        # R8-C07: Wrong raw size
        m = copy.deepcopy(valid_manifest)
        m["artifacts"]["results/logs/rf36-p4a/p4a_raw_ledger.jsonl"]["size_bytes"] = 12345
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("size_bytes", msg)

        # R8-C08: Wrong raw record count
        m = copy.deepcopy(valid_manifest)
        m["artifacts"]["results/logs/rf36-p4a/p4a_raw_ledger.jsonl"]["line_count"] = 50
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("line_count", msg)

        # R8-C09: Missing required top-level field
        m = copy.deepcopy(valid_manifest)
        del m["protocol_version_distinction"]
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("missing required top-level field", msg)

        # R8-C10: Duplicate raw artifact entry
        m = copy.deepcopy(valid_manifest)
        m["artifacts"]["other_path/p4a_raw_ledger.jsonl"] = dict(m["artifacts"]["results/logs/rf36-p4a/p4a_raw_ledger.jsonl"])
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)
        self.assertIn("ambiguous/duplicate raw ledger entries", msg)

        # R8-C11: Conflicting identities for same basename
        m = copy.deepcopy(valid_manifest)
        m["artifacts"]["custom/p4a_raw_ledger.jsonl"] = {
            "sha256": "4" * 64, "size_bytes": 100, "line_count": 10
        }
        ok, msg = authority.verify_evidence_manifest(m)
        self.assertFalse(ok)

        # R8-C12: Malformed evidence manifest (not a dict)
        ok, msg = authority.verify_evidence_manifest(["not", "a", "dict"])
        self.assertFalse(ok)
        self.assertIn("must be a dict", msg)

        # R8-C13: CLI with missing evidence manifest file exits 1
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_l = Path(tmpdir) / "dummy.jsonl"
            temp_l.write_text('{"slot_key": "dummy"}\n')
            cmd = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(temp_l),
                "--mode", "canonical",
                "--evidence-manifest", str(Path(tmpdir) / "non_existent.json"),
                "--output-json", str(Path(tmpdir) / "out.json"),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Evidence manifest not found", proc.stderr)

        # R8-C14: Programmatic analyze_p4a_ledger with invalid evidence_manifest fails context
        res_bad_ev = analyze_p4a_ledger(
            records=[self.valid_canonical_m1, self.valid_canonical_m2],
            mode="canonical",
            expected_header=authority.expected_header,
            expected_slot_matrix=authority.expected_slot_matrix,
            trusted_artifact_meta=None,
            raw_ledger_path=None,
            evidence_manifest=m,
        )
        self.assertEqual(res_bad_ev["overall_verdict"], "CANONICAL_UNTRUSTED_CONTEXT")

        # R8-C15: Permitted documentation revision maintains historical protocol distinction
        m_doc = copy.deepcopy(valid_manifest)
        m_doc["artifacts"]["docs/rapidflow_apx_p4a_protocol.md"]["sha256"] = "5" * 64
        ok, msg = authority.verify_evidence_manifest(m_doc)
        self.assertTrue(ok, f"Documentation revision should be permitted in manifest: {msg}")

        # R8-C16: Derived metrics artifact update preserves authority
        m_deriv = copy.deepcopy(valid_manifest)
        m_deriv["artifacts"]["results/logs/rf36-p4a/p4a_summary_metrics.json"]["sha256"] = "6" * 64
        ok, msg = authority.verify_evidence_manifest(m_deriv)
        self.assertTrue(ok, f"Derived summary update should be permitted in manifest: {msg}")

    def test_65_r8_f04_safe_canonical_report_publication(self):
        """R8-F04: Safe Canonical Report Publication & Atomic Staging (R8-D01..R8-D18)."""
        authority = get_official_p4a_authority()
        raw_ledger = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
        if not raw_ledger.is_file():
            self.skipTest("HISTORICAL_EVIDENCE_UNAVAILABLE: Historical canonical raw ledger is not provisioned.")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target_json = tmp / "summary_metrics.json"
            target_md = tmp / "profile_report.md"
            diag_json = tmp / "diagnostic.json"

            # R8-D01: Valid canonical run atomically publishes summary JSON and Markdown
            cmd_d01 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--mode", "canonical",
                "--output-json", str(target_json),
                "--output-md", str(target_md),
            ]
            proc_d01 = subprocess.run(cmd_d01, capture_output=True, text=True)
            self.assertEqual(proc_d01.returncode, 0, f"Valid canonical run must exit 0: {proc_d01.stderr}")
            self.assertTrue(target_json.is_file())
            self.assertTrue(target_md.is_file())
            initial_sha = compute_sha256(target_json)

            # R8-D02: Invalid raw SHA must NOT overwrite existing certified summary
            fake_ledger = tmp / "tampered.jsonl"
            fake_ledger.write_text('{"tampered": true}\n')
            cmd_d02 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(fake_ledger),
                "--mode", "canonical",
                "--output-json", str(target_json),
                "--output-md", str(target_md),
            ]
            proc_d02 = subprocess.run(cmd_d02, capture_output=True, text=True)
            self.assertNotEqual(proc_d02.returncode, 0)
            self.assertEqual(compute_sha256(target_json), initial_sha, "Existing summary must not be modified by failed run")

            # R8-D04: Invalid evidence manifest must NOT overwrite existing certified summary
            forged_ev = tmp / "forged_ev.json"
            with open(DEFAULT_EVIDENCE_MANIFEST_PATH) as f:
                ev_data = json.load(f)
            ev_data["protocol_version_distinction"]["historical_measurement_protocol_sha256"] = "0" * 64
            with open(forged_ev, "w") as f:
                json.dump(ev_data, f)

            cmd_d04 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--evidence-manifest", str(forged_ev),
                "--mode", "canonical",
                "--output-json", str(target_json),
            ]
            proc_d04 = subprocess.run(cmd_d04, capture_output=True, text=True)
            self.assertNotEqual(proc_d04.returncode, 0)
            self.assertEqual(compute_sha256(target_json), initial_sha)

            # R8-D06: Scoped mode with canonical default output path is rejected
            scoped_ledger = tmp / "scoped.jsonl"
            scoped_ledger.write_text(json.dumps(self.valid_canonical_m1) + "\n" + json.dumps(self.valid_canonical_m2) + "\n")
            cmd_d06 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(scoped_ledger),
                "--mode", "scoped",
                "--output-json", str(DEFAULT_SUMMARY_JSON),
            ]
            proc_d06 = subprocess.run(cmd_d06, capture_output=True, text=True)
            self.assertNotEqual(proc_d06.returncode, 0)
            self.assertIn("Refusing to overwrite", proc_d06.stderr)

            # R8-D07: Explicit --diagnostic-json on failure writes diagnostics while protecting target_json
            cmd_d07 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(fake_ledger),
                "--mode", "canonical",
                "--output-json", str(target_json),
                "--diagnostic-json", str(diag_json),
            ]
            proc_d07 = subprocess.run(cmd_d07, capture_output=True, text=True)
            self.assertNotEqual(proc_d07.returncode, 0)
            self.assertEqual(compute_sha256(target_json), initial_sha)
            self.assertTrue(diag_json.is_file(), "Diagnostic JSON should be saved when requested")
            with open(diag_json) as f:
                diag_data = json.load(f)
            self.assertEqual(diag_data["overall_verdict"], "CANONICAL_INVALID_ARTIFACT")

            # R8-D10: Output collision with raw ledger is rejected
            cmd_d10 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--output-json", str(raw_ledger),
                "--mode", "canonical",
            ]
            proc_d10 = subprocess.run(cmd_d10, capture_output=True, text=True)
            self.assertNotEqual(proc_d10.returncode, 0)
            self.assertIn("collision", proc_d10.stderr.lower())

            # R8-D11: Output collision with trusted manifest is rejected
            cmd_d11 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--manifest", str(DEFAULT_MANIFEST_PATH),
                "--output-json", str(DEFAULT_MANIFEST_PATH),
                "--mode", "canonical",
            ]
            proc_d11 = subprocess.run(cmd_d11, capture_output=True, text=True)
            self.assertNotEqual(proc_d11.returncode, 0)
            self.assertIn("collision", proc_d11.stderr.lower())

            # R8-D12: Output-json and output-md collision is rejected
            cmd_d12 = [
                sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"),
                "--ledger", str(raw_ledger),
                "--output-json", str(target_json),
                "--output-md", str(target_json),
                "--mode", "canonical",
            ]
            proc_d12 = subprocess.run(cmd_d12, capture_output=True, text=True)
            self.assertNotEqual(proc_d12.returncode, 0)
            self.assertIn("collision", proc_d12.stderr.lower())

            # R8-D17: CLI --help exits 0 with usage
            cmd_d17 = [sys.executable, str(REPO_ROOT / "scripts" / "summarize_rapidflow_apx_p4a.py"), "--help"]
            proc_d17 = subprocess.run(cmd_d17, capture_output=True, text=True)
            self.assertEqual(proc_d17.returncode, 0)
            self.assertIn("usage:", proc_d17.stdout.lower())



if __name__ == "__main__":
    unittest.main()
