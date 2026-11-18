"""
tests/test_rapidflow_apx_foundation.py — Python regression test suite for RapidFlow-APX Native Foundation (Phase P2).

Tests Groups A through J:
- Group A: Codec boundaries (signed 64-bit IDs, typed labels isolation, capacity limits, checkpoint rollback)
- Group B: Authoritative graph lifecycle (CSR, overlays, tombstones, label precedence, ingestion checks)
- Group C: Transaction manager ACID boundaries (prevalidation, snapshots, rollback, commit preservation, reentrancy, bad_alloc)
- Group D: Tuple sink invariants (canonical coordinate sorting, injectivity, duplicate raw rejection)
- Group E: Micro exact matcher & M0 oracle on K4 (24 unique ordered tuples matching scripts.dstar_oracle bit-for-bit)
- Group F: Dynamic event transitions (0 baseline, 24 ENTERED on INSERT, 24 EXITED on DELETE matching DStarDynamicOracle)
- Group G: Diamond k=1 fixed-mapping evaluation (D-R guard k < lambda, S2 spanning connectivity, metadata verification)
- Group H: Irrelevant updates (state updated in authoritative graph, zero search emissions)
- Group I: Runner negatives, provenance gate, manifest validation, process containment, and atomic reporting
- Group J: Targeted remediation verifications (P2-F01 to P2-F08 root cause regression tests)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_foundation import (
    ProvenanceError,
    ResourceLimitError,
    ManifestValidationError,
    build_native_harness,
    check_system_resources,
    compute_sha256,
    run_isolated_case,
    validate_manifest,
    verify_provenance,
    verify_case_with_oracle,
)
from scripts.dstar_oracle import (
    DStarGraph,
    enumerate_snapshot,
    DStarDynamicOracle,
    DStarUpdate,
)


class TestRapidFlowApxFoundation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provenance = verify_provenance()
        cls.harness_bin, cls.binary_sha = build_native_harness(clean=False)
        cls.manifest_path = REPO_ROOT / "configs/rapidflow_apx_foundation_cases.json"
        with open(cls.manifest_path, "r", encoding="utf-8") as f:
            cls.manifest = json.load(f)
        cls.cases_by_id = {c["case_id"]: c for c in cls.manifest["cases"]}

    # =========================================================================
    # Group A: Codec Boundaries
    # =========================================================================
    def test_group_a_codec_boundaries(self):
        case_def = self.cases_by_id["codec_boundaries"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertEqual(report["query_vertices_count"], 3)
        self.assertEqual(report["data_vertices_count"], 6)

    # =========================================================================
    # Group B: Authoritative Graph Lifecycle & Ingestion Checks
    # =========================================================================
    def test_group_b_lifecycle_stream(self):
        case_def = self.cases_by_id["lifecycle_stream"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertEqual(report["final_vertices_count"], 5)
        self.assertEqual(report["final_edges_count"], 4)

    # =========================================================================
    # Group C: Transaction Manager ACID Boundaries & Strong Exception Safety
    # =========================================================================
    def test_group_c_transaction_lifecycle(self):
        case_def = self.cases_by_id["transaction_lifecycle"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        steps = report["steps_executed"]
        expected_statuses = [
            "SUCCESS",
            "PREVALIDATION_FAILED",
            "FAULT_INJECTED",
            "FAULT_INJECTED",
            "PREVALIDATION_FAILED",
            "PREVALIDATION_FAILED",
            "FAULT_INJECTED",
            "SUCCESS",
        ]
        self.assertEqual(len(steps), len(expected_statuses))
        for step, exp in zip(steps, expected_statuses):
            self.assertEqual(step["status"], exp)

    # =========================================================================
    # Group D: Tuple Sink Invariants
    # =========================================================================
    def test_group_d_tuple_sink_invariants(self):
        case_def = self.cases_by_id["tuple_sink_invariants"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertEqual(report["committed_emissions"], 2)
        self.assertEqual(report["raw_emissions"], 4)
        self.assertEqual(report["unique_emissions"], 3)
        self.assertEqual(report["discarded_emissions"], 1)

    # =========================================================================
    # Group E: Micro Exact Matcher & Genuine M0 Oracle
    # =========================================================================
    def test_group_e_k4_exact_enumeration(self):
        case_def = self.cases_by_id["k4_exact_enumeration"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertEqual(report["tuple_count"], 24)
        self.assertEqual(report["raw_emissions"], 24)
        self.assertEqual(report["unique_emissions"], 24)

        # Cross-validate bit-for-bit against genuine scripts.dstar_oracle
        with open(REPO_ROOT / case_def["fixture_path"], "r", encoding="utf-8") as f:
            fixture_data = json.load(f)

        Q = DStarGraph()
        for v in fixture_data["query"]["vertices"]:
            Q.add_vertex(v["id"], v["label"]["val"])
        for e in fixture_data["query"]["edges"]:
            Q.add_edge(e["u"], e["v"], e["label"]["val"])

        G = DStarGraph()
        for v in fixture_data["data"]["vertices"]:
            G.add_vertex(v["id"], v["label"]["val"])
        for e in fixture_data["data"]["edges"]:
            G.add_edge(e["u"], e["v"], e["label"]["val"])

        oracle_tuples_raw = enumerate_snapshot(Q, G, k=0)
        oracle_tuples = sorted([list(t) for t in oracle_tuples_raw])
        self.assertEqual(len(oracle_tuples), 24)
        actual_tuples = sorted(report["tuples"])
        self.assertEqual(actual_tuples, oracle_tuples)

    # =========================================================================
    # Group F: Dynamic Event Transitions with Full Tuple Verification
    # =========================================================================
    def test_group_f_event_transition_stream(self):
        case_def = self.cases_by_id["event_transition_stream"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        events = report["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "ENTERED")
        self.assertEqual(events[0]["published_count"], 24)
        self.assertEqual(len(events[0]["tuples"]), 24)
        self.assertEqual(events[1]["event_type"], "EXITED")
        self.assertEqual(events[1]["published_count"], 24)
        self.assertEqual(len(events[1]["tuples"]), 24)

        # Verify against DStarDynamicOracle
        with open(REPO_ROOT / case_def["fixture_path"], "r", encoding="utf-8") as f:
            fixture_data = json.load(f)

        ok, msg = verify_case_with_oracle("event_transition_stream", fixture_data, report)
        self.assertTrue(ok, msg)

    # =========================================================================
    # Group G: Diamond k=1 Fixed Mapping & D-R Guard
    # =========================================================================
    def test_group_g_diamond_k1_fixed(self):
        case_def = self.cases_by_id["diamond_k1_fixed"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertTrue(report["is_valid"])
        self.assertEqual(report["matched_edges"], 4)
        self.assertEqual(report["missing_edges"], 1)
        self.assertEqual(report["computed_lambda"], 2)
        self.assertTrue(report["s2_spanning_connected"])

    # =========================================================================
    # Group H: Irrelevant Updates
    # =========================================================================
    def test_group_h_irrelevant_update_stream(self):
        case_def = self.cases_by_id["irrelevant_update_stream"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertTrue(report["edge_recorded"])
        self.assertEqual(report["published_tuples_count"], 0)

    # =========================================================================
    # Group I: Runner Negatives, Provenance Gate, Manifest & Preflight
    # =========================================================================
    def test_group_i_tampered_fixture_sha_fails(self):
        tampered_case = dict(self.cases_by_id["codec_boundaries"])
        tampered_case["fixture_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
        res = run_isolated_case(self.harness_bin, self.binary_sha, tampered_case)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("Fixture SHA-256 mismatch", res["diagnostic"])

    def test_group_i_unknown_case_fails(self):
        unknown_case = {
            "case_id": "non_existent_case",
            "fixture_path": "tests/fixtures/rapidflow_apx_foundation/codec_boundaries_fixture.json",
            "fixture_sha256": compute_sha256(REPO_ROOT / "tests/fixtures/rapidflow_apx_foundation/codec_boundaries_fixture.json"),
        }
        res = run_isolated_case(self.harness_bin, self.binary_sha, unknown_case)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("Unknown case_id", res.get("diagnostic", ""))

    def test_group_i_manifest_validation_ceiling_timeout(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            bad_manifest = {
                "manifest_version": "1.0.0",
                "required_case_ids": ["c1"],
                "cases": [
                    {
                        "case_id": "c1",
                        "limits": {"timeout_seconds": 60, "max_address_space_mib": 128},
                    }
                ],
            }
            json.dump(bad_manifest, f)
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ManifestValidationError) as ctx:
                validate_manifest(temp_path)
            self.assertIn("exceeds ceiling of 30s", str(ctx.exception))
        finally:
            temp_path.unlink()

    def test_group_i_manifest_validation_duplicate_case(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            bad_manifest = {
                "manifest_version": "1.0.0",
                "required_case_ids": ["c1"],
                "cases": [
                    {"case_id": "c1", "limits": {"timeout_seconds": 10, "max_address_space_mib": 128}},
                    {"case_id": "c1", "limits": {"timeout_seconds": 10, "max_address_space_mib": 128}},
                ],
            }
            json.dump(bad_manifest, f)
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ManifestValidationError) as ctx:
                validate_manifest(temp_path)
            self.assertIn("Duplicate case_id in manifest", str(ctx.exception))
        finally:
            temp_path.unlink()

    def test_group_i_system_resources_preflight(self):
        res = check_system_resources()
        self.assertGreaterEqual(res["available_ram_gib"], 2.0)
        self.assertGreaterEqual(res["free_disk_gib"], 1.0)

    def test_group_i_provenance_verification_passes(self):
        prov = verify_provenance()
        self.assertEqual(prov["upstream_commit"], "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1")
        self.assertTrue(prov["upstream_pristine"])

    # =========================================================================
    # Group J: Targeted Remediation Findings Verifications (P2-F01 to P2-F08)
    # =========================================================================
    def test_group_j_p2_f01_payload_validation_rejects_string_false(self):
        # A payload with status != "PASS" must fail
        bad_report = {"case_id": "test", "status": "false"}
        ok, _ = verify_case_with_oracle("diamond_k1_fixed", {}, bad_report)
        self.assertFalse(ok)

    def test_group_j_p2_f01_shrunk_manifest_rejected_by_canonical_registry(self):
        # Even if manifest specifies only 1 case and required_case_ids=["codec_boundaries"],
        # canonical manifest validation must reject it because canonical suite requires all 8 cases.
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            shrunk = {
                "manifest_version": "1.0.0",
                "required_case_ids": ["codec_boundaries"],
                "cases": [self.cases_by_id["codec_boundaries"]],
            }
            json.dump(shrunk, f)
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ManifestValidationError) as ctx:
                validate_manifest(temp_path, is_canonical=True)
            self.assertIn("missing canonical required case", str(ctx.exception))
        finally:
            temp_path.unlink()

    def test_group_j_p2_f01_omitted_required_case_ids_rejected(self):
        # If required_case_ids is omitted entirely from manifest, canonical validation still enforces canonical cases.
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            shrunk = {
                "manifest_version": "1.0.0",
                "cases": [self.cases_by_id["codec_boundaries"]],
            }
            json.dump(shrunk, f)
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ManifestValidationError) as ctx:
                validate_manifest(temp_path, is_canonical=True)
            self.assertIn("missing canonical required case", str(ctx.exception))
        finally:
            temp_path.unlink()

    def test_group_j_p2_f01_missing_fixture_fails_dry_run(self):
        # If fixture path does not exist, validate_manifest and dry-run must fail.
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            corrupt = dict(self.manifest)
            corrupt["cases"] = [dict(c) for c in corrupt["cases"]]
            corrupt["cases"][0]["fixture_path"] = "non_existent_fixture_path_xyz.json"
            json.dump(corrupt, f)
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ManifestValidationError) as ctx:
                validate_manifest(temp_path, is_canonical=True)
            self.assertIn("declared fixture path does not exist", str(ctx.exception))
        finally:
            temp_path.unlink()

    def test_group_j_p2_f01_payload_missing_observations_rejected(self):
        # A payload with status PASS but missing observations must be rejected by run_isolated_case
        case_def = self.cases_by_id["codec_boundaries"]
        # run_isolated_case on real binary returns all observations
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")

        # Mock a minimal payload missing query_vertices_count
        minimal_report = {"case_id": "codec_boundaries", "status": "PASS", "diagnostic": "ok"}
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.return_value = (json.dumps(minimal_report), "")
            mock_proc.returncode = 0
            bad_res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
            self.assertEqual(bad_res["status"], "FAIL")
            self.assertIn("Missing required observation field 'query_vertices_count'", bad_res["diagnostic"])

    def test_group_j_p2_f03_duplicate_external_vertices_rejected(self):
        # Feeding duplicate external IDs in query vertices must be rejected by native codec
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            fixture = {
                "external_query_vertices": [{"id": 10}, {"id": 10}, {"id": 10}],
                "external_data_vertices": [{"id": 100}, {"id": 200}],
            }
            json.dump(fixture, f)
            temp_path = Path(f.name)

        try:
            proc = subprocess.run(
                [str(self.harness_bin), "--case", "codec_boundaries", "--fixture", str(temp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out = json.loads(proc.stdout.strip())
            self.assertEqual(out["status"], "FAIL")
            self.assertIn("Duplicate external query vertex declaration", out["diagnostic"])
        finally:
            temp_path.unlink()

    def test_group_j_p2_f04_swapped_events_rejected(self):
        # Swapped ENTERED/EXITED events must fail validation
        with open(REPO_ROOT / self.cases_by_id["event_transition_stream"]["fixture_path"]) as f:
            fix_data = json.load(f)

        swapped_report = {
            "case_id": "event_transition_stream",
            "status": "PASS",
            "events": [
                {"step": 1, "op": "+", "event_type": "EXITED", "published_count": 24, "tuples": []},
                {"step": 2, "op": "-", "event_type": "ENTERED", "published_count": 24, "tuples": []},
            ],
        }
        ok, msg = verify_case_with_oracle("event_transition_stream", fix_data, swapped_report)
        self.assertFalse(ok)
        self.assertIn("event type/op mismatch", msg)

    def test_group_j_p2_f04_step_state_reference_mismatch_rejected(self):
        # A divergent edge count in transaction lifecycle must be caught by reference model
        with open(REPO_ROOT / self.cases_by_id["transaction_lifecycle"]["fixture_path"]) as f:
            fix_data = json.load(f)

        from scripts.run_rapidflow_apx_foundation import simulate_transaction_stream
        ref_steps = simulate_transaction_stream(fix_data)
        corrupt_report = {
            "case_id": "transaction_lifecycle",
            "status": "PASS",
            "steps_executed": [dict(s) for s in ref_steps],
        }
        corrupt_report["steps_executed"][0]["current_edges_count"] = 9999
        ok, msg = verify_case_with_oracle("transaction_lifecycle", fix_data, corrupt_report)
        self.assertFalse(ok)
        self.assertIn("edges_count mismatch", msg)

    def test_group_j_p2_f06_low_ram_fails_preflight(self):
        # Mocking low RAM raises ResourceLimitError
        with patch("scripts.run_rapidflow_apx_foundation.open", create=True) as mock_open:
            import io
            mock_open.return_value.__enter__.return_value = io.StringIO("MemAvailable:     500000 kB\n")
            with self.assertRaises(ResourceLimitError) as ctx:
                check_system_resources()
            self.assertIn("Insufficient available RAM", str(ctx.exception))

    def test_group_j_p2_f06_setrlimit_failure_causes_case_failure(self):
        # If setrlimit fails in preexec, child exits with 127 and parent records containment failure
        case_def = self.cases_by_id["codec_boundaries"]
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.return_value = ("", "FATAL: Failed to enforce RLIMIT_AS: Operation not permitted")
            mock_proc.returncode = 127
            res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["exit_code"], 127)
            self.assertIn("Resource limit containment could not be enforced", res["diagnostic"])

    def test_group_j_p2_f07_case_exception_creates_terminal_failure_record(self):
        # If an unexpected exception occurs on a case, main() writes terminal FAIL summary
        from scripts.run_rapidflow_apx_foundation import main
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            summary_path = Path(f.name)

        try:
            with patch("sys.argv", ["run_rapidflow_apx_foundation.py", "--case", "codec_boundaries", "--output", str(summary_path)]):
                with patch("scripts.run_rapidflow_apx_foundation.run_isolated_case", side_effect=RuntimeError("Unexpected fault")):
                    ret = main()
                    self.assertNotEqual(ret, 0)
                    with open(summary_path, "r") as f:
                        data = json.load(f)
                    self.assertEqual(data["overall_status"], "FAIL")
                    self.assertEqual(data["stages"]["CASES"], "FAIL")
                    self.assertGreater(data["failed_cases"], 0)
        finally:
            if summary_path.exists():
                summary_path.unlink()

    def test_group_j_p2_f07_diagnostic_mode_does_not_overwrite_canonical(self):
        canonical_summary = REPO_ROOT / "reports/rapidflow_apx_foundation/summary.json"
        diagnostic_summary = REPO_ROOT / "reports/rapidflow_apx_foundation/diagnostic_summary.json"

        # Record mtime of canonical if it exists
        orig_mtime = canonical_summary.stat().st_mtime if canonical_summary.exists() else None

        # Run subset case
        proc = subprocess.run(
            [sys.executable, "scripts/run_rapidflow_apx_foundation.py", "--case", "codec_boundaries"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        self.assertTrue(diagnostic_summary.exists())
        with open(diagnostic_summary, "r") as f:
            diag_data = json.load(f)
        self.assertEqual(diag_data["runner_mode"], "DIAGNOSTIC_SUBSET")
        self.assertEqual(diag_data["overall_status"], "DIAGNOSTIC_SUBSET_RUN")

        if orig_mtime is not None:
            self.assertEqual(canonical_summary.stat().st_mtime, orig_mtime)


if __name__ == "__main__":
    unittest.main()
