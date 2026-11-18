"""
tests/test_rapidflow_apx_foundation.py — Python regression test suite for RapidFlow-APX Native Foundation (Phase P2).

Tests Groups A through I:
- Group A: Codec boundaries (signed 64-bit IDs, typed labels isolation, capacity limits)
- Group B: Authoritative graph lifecycle (CSR, overlays, tombstones, label precedence)
- Group C: Transaction manager ACID boundaries (prevalidation, snapshots, rollback, commit preservation)
- Group D: Tuple sink invariants (canonical coordinate sorting, injectivity, duplicate raw rejection)
- Group E: Micro exact matcher & M0 oracle on K4 (24 unique ordered tuples, k>0 rejection)
- Group F: Dynamic event transitions (0 baseline, 24 ENTERED on INSERT, 24 EXITED on DELETE)
- Group G: Diamond k=1 fixed-mapping evaluation without exact NLF filtering
- Group H: Irrelevant updates (state updated, zero search emissions)
- Group I: Runner negatives, provenance gate, and process containment
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_foundation import (
    ProvenanceError,
    build_native_harness,
    check_system_resources,
    compute_sha256,
    python_m0_k4_oracle,
    run_isolated_case,
    verify_provenance,
)


class TestRapidFlowApxFoundation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify provenance and build native harness once for tests
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
    # Group B: Authoritative Graph Lifecycle
    # =========================================================================
    def test_group_b_lifecycle_stream(self):
        case_def = self.cases_by_id["lifecycle_stream"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertEqual(report["final_vertices_count"], 5)
        self.assertEqual(report["final_edges_count"], 4)

    # =========================================================================
    # Group C: Transaction Manager ACID Boundaries
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
    # Group E: Micro Exact Matcher & M0 Oracle
    # =========================================================================
    def test_group_e_k4_exact_enumeration(self):
        case_def = self.cases_by_id["k4_exact_enumeration"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertEqual(report["tuple_count"], 24)
        self.assertEqual(report["raw_emissions"], 24)
        self.assertEqual(report["unique_emissions"], 24)

        # Cross-validate against independent Python M0 Oracle
        with open(REPO_ROOT / case_def["fixture_path"], "r", encoding="utf-8") as f:
            fixture_data = json.load(f)
        oracle_tuples = python_m0_k4_oracle(fixture_data)
        self.assertEqual(len(oracle_tuples), 24)
        actual_tuples = sorted(report["tuples"])
        self.assertEqual(actual_tuples, oracle_tuples)

    # =========================================================================
    # Group F: Dynamic Event Transitions
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
        self.assertEqual(events[1]["event_type"], "EXITED")
        self.assertEqual(events[1]["published_count"], 24)

    # =========================================================================
    # Group G: Diamond k=1 Fixed Mapping
    # =========================================================================
    def test_group_g_diamond_k1_fixed(self):
        case_def = self.cases_by_id["diamond_k1_fixed"]
        res = run_isolated_case(self.harness_bin, self.binary_sha, case_def)
        self.assertEqual(res["status"], "PASS")
        report = res["native_report"]
        self.assertTrue(report["is_valid"])
        self.assertEqual(report["matched_edges"], 4)
        self.assertEqual(report["missing_edges"], 1)

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
    # Group I: Runner Negatives & Provenance Gate
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

    def test_group_i_system_resources_preflight(self):
        res = check_system_resources()
        self.assertGreaterEqual(res["total_ram_gib"], 2.0)
        self.assertGreaterEqual(res["free_disk_gib"], 1.0)

    def test_group_i_provenance_verification_passes(self):
        prov = verify_provenance()
        self.assertEqual(prov["upstream_commit"], "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1")
        self.assertTrue(prov["upstream_pristine"])


if __name__ == "__main__":
    unittest.main()
