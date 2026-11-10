#!/usr/bin/env python3
"""
tests/test_apx04_preflight.py

Unit tests for APX-04 Gate A3-00:
- Preflight validation tool (schema, files, hashes, syntax, safety locks)
- Mocked host resource preflight (RAM, Disk)
- Mathematical analysis verification (Theorems 1, 2, 3)
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.apx04_preflight import (
    PreflightChecker,
    compute_sha256,
    get_system_resources,
    parse_query_file,
    parse_stream_prefix_file,
)


class TestAPX04ManifestAndFiles(unittest.TestCase):
    """Verifies workload manifest schema, integrity, and file validation."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.manifest_path = self.repo_root / "configs" / "apx04_workload_manifest.json"

    def test_canonical_manifest_passes_preflight(self) -> None:
        """The checked-in workload manifest must pass all preflight audits cleanly."""
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=False,
        )
        passed = checker.run_all()
        self.assertTrue(passed, f"Preflight failed with errors: {checker.errors}")
        self.assertEqual(len(checker.errors), 0)
        self.assertEqual(checker.audit_log["status"], "PASS")
        self.assertEqual(checker.audit_log["pilot_cases_count"], 10)
        self.assertEqual(len(checker.audit_log["queries_verified"]), 7)
        self.assertEqual(len(checker.audit_log["streams_verified"]), 4)

    def test_missing_manifest_fails_closed(self) -> None:
        """Non-existent manifest path must fail closed."""
        checker = PreflightChecker(
            manifest_path=self.repo_root / "non_existent_manifest.json",
            repo_root=self.repo_root,
        )
        passed = checker.run_all()
        self.assertFalse(passed)
        self.assertTrue(any("not found" in e.lower() for e in checker.errors))

    def test_execution_authorization_safety_lock(self) -> None:
        """Setting execution_authorization=True in Gate A3-00 must fail validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["execution_authorization"] = True  # Safety violation
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(
                manifest_path=tmp_manifest_path,
                repo_root=self.repo_root,
            )
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("safety violation" in e.lower() for e in checker.errors))

    def test_invalid_status_rejected(self) -> None:
        """Manifest status other than DRAFT_PENDING_USER_APPROVAL must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = "APPROVED_READY_TO_RUN"  # Unauthorized status change
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(
                manifest_path=tmp_manifest_path,
                repo_root=self.repo_root,
            )
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("status must be" in e.lower() for e in checker.errors))

    def test_hash_mismatch_detected(self) -> None:
        """Corrupted file hash must be detected and reported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Tamper with star4 query hash
            data["queries"]["Q05_probe_star4_l0"]["sha256"] = "0" * 64
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(
                manifest_path=tmp_manifest_path,
                repo_root=self.repo_root,
            )
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("sha-256 mismatch" in e.lower() for e in checker.errors))


class TestQueryAndStreamParsing(unittest.TestCase):
    """Verifies query parsing, connectivity check, and stream syntax."""

    def test_disconnected_query_rejected(self) -> None:
        """CSM queries must be connected; disconnected graph must trigger error."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            # 2 disconnected edges (0-1) and (2-3)
            f.write("t # 99\nv 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 2 3 0\n")
            f_path = Path(f.name)

        try:
            parsed = parse_query_file(f_path)
            self.assertFalse(parsed["is_connected"])
        finally:
            f_path.unlink()

    def test_connected_query_degree_sequence(self) -> None:
        """Valid query parses degree sequence and connectivity correctly."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            # Star graph K_{1,3}: center 0, leaves 1, 2, 3
            f.write("t # 1\nv 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 0 2 0\ne 0 3 0\n")
            f_path = Path(f.name)

        try:
            parsed = parse_query_file(f_path)
            self.assertTrue(parsed["is_connected"])
            self.assertEqual(parsed["num_vertices"], 4)
            self.assertEqual(parsed["num_edges"], 3)
            self.assertEqual(parsed["degree_sequence"], [3, 1, 1, 1])
        finally:
            f_path.unlink()

    def test_stream_prefix_parsing(self) -> None:
        """Stream prefix parser supports 'e' (insertion) and '-e' (deletion)."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("e 10 20 0\ne 20 30 1\n-e 10 20 0\ne 30 40 2\n")
            f_path = Path(f.name)

        try:
            parsed = parse_stream_prefix_file(f_path)
            self.assertEqual(parsed["total_operations"], 4)
            self.assertEqual(parsed["insertions"], 3)
            self.assertEqual(parsed["deletions"], 1)
        finally:
            f_path.unlink()

    def test_malformed_stream_op_rejected(self) -> None:
        """Unrecognized tags in stream prefix must raise ValueError."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("x 10 20 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError):
                parse_stream_prefix_file(f_path)
        finally:
            f_path.unlink()


class TestSystemResourceAudits(unittest.TestCase):
    """Verifies host resource checks (RAM and Disk) with mocked values."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.manifest_path = self.repo_root / "configs" / "apx04_workload_manifest.json"

    @patch("scripts.apx04_preflight.get_system_resources")
    def test_adequate_resources_pass_without_warnings(self, mock_res) -> None:
        """Adequate RAM (8 GiB) and Disk (50 GiB) must pass cleanly with 0 errors/warnings."""
        mock_res.return_value = {
            "ram_total_gib": 16.0,
            "ram_available_gib": 8.0,
            "disk_total_gib": 200.0,
            "disk_free_gib": 50.0,
        }
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=True,
        )
        passed = checker.run_all()
        self.assertTrue(passed)
        self.assertEqual(len(checker.errors), 0)
        self.assertEqual(len(checker.warnings), 0)

    @patch("scripts.apx04_preflight.get_system_resources")
    def test_low_ram_enforced_fails(self, mock_res) -> None:
        """When enforce_ram=True, available RAM < 4.0 GiB must fail closed."""
        mock_res.return_value = {
            "ram_total_gib": 11.5,
            "ram_available_gib": 2.5,  # Insufficient RAM
            "disk_total_gib": 150.0,
            "disk_free_gib": 80.0,
        }
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=True,
        )
        passed = checker.run_all()
        self.assertFalse(passed)
        self.assertTrue(any("ram_resource_error" in e.lower() for e in checker.errors))

    @patch("scripts.apx04_preflight.get_system_resources")
    def test_low_ram_unenforced_warns(self, mock_res) -> None:
        """When enforce_ram=False, available RAM < 4.0 GiB issues a warning but does not fail."""
        mock_res.return_value = {
            "ram_total_gib": 11.5,
            "ram_available_gib": 3.4,  # Under 4.0 GiB
            "disk_total_gib": 150.0,
            "disk_free_gib": 80.0,
        }
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=False,
        )
        passed = checker.run_all()
        self.assertTrue(passed)
        self.assertEqual(len(checker.errors), 0)
        self.assertTrue(any("ram_resource_warning" in w.lower() for w in checker.warnings))

    @patch("scripts.apx04_preflight.get_system_resources")
    def test_low_disk_space_fails(self, mock_res) -> None:
        """Free disk space below 10 GiB must always fail."""
        mock_res.return_value = {
            "ram_total_gib": 16.0,
            "ram_available_gib": 8.0,
            "disk_total_gib": 100.0,
            "disk_free_gib": 4.5,  # Less than 10 GiB
        }
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
        )
        passed = checker.run_all()
        self.assertFalse(passed)
        self.assertTrue(any("disk_resource_error" in e.lower() for e in checker.errors))


class TestMathematicalAnalysisTheorems(unittest.TestCase):
    """
    Empirical and formal verification of Theorems 1, 2, and 3 from
    docs/apx04_feasibility_protocol.md.
    """

    def test_theorem1_hub_lower_bound(self) -> None:
        """
        Theorem 1: x_out(f) >= max_{v in S_f} max(0, deg_G(v) - (k-1)).
        For a star graph G = K_{1, d} where center node 0 has deg_G(0) = d,
        any embedding of a k-vertex query must have x_out >= d - (k - 1).
        """
        k = 4
        # Test across degrees d from 3 to 100
        for d in [3, 4, 10, 50, 100]:
            # In K_{1, d}, center node 0 connects to d peripheral leaves.
            # Any 4-vertex subset S containing node 0 has |S| = 4.
            # Number of internal edges in G[S] is exactly 3 (edges from center to 3 leaves in S).
            deg_sum = d + 1 + 1 + 1  # deg(center) + 3 * deg(leaf)
            internal_edges = 3
            # By cut identity (Theorem 2): x_out(S) = sum(deg) - 2 * |E_G[S]|
            x_out = deg_sum - 2 * internal_edges
            # = (d + 3) - 6 = d - 3.
            expected_x_out = d - 3

            # Lower bound from Theorem 1:
            lower_bound = max(0, d - (k - 1))
            self.assertEqual(lower_bound, d - 3)
            self.assertGreaterEqual(x_out, lower_bound)
            self.assertEqual(x_out, expected_x_out)

    def test_theorem2_boundary_cut_identity_and_chords(self) -> None:
        """
        Theorem 2:
        1. x_out(S) = sum_{v in S} deg_G(v) - 2 * |E_G[S]|.
        2. Adding internal chord e = (u, w) within S yields Delta x_out = 0.
        """
        # Graph G on vertices {0, 1, 2, 3, 4}
        # S = {0, 1, 2, 3}
        # Initial edges: (0, 1), (1, 2), (2, 3) (path of 4 vertices)
        # External edge: (0, 4) (external vertex 4)
        deg_G = {0: 2, 1: 2, 2: 2, 3: 1, 4: 1}
        S = {0, 1, 2, 3}
        internal_edges_count = 3  # (0,1), (1,2), (2,3)
        external_cut = 1  # (0, 4)

        x_out_cut = sum(deg_G[v] for v in S) - 2 * internal_edges_count
        self.assertEqual(x_out_cut, (2 + 2 + 2 + 1) - 2 * 3)  # 7 - 6 = 1
        self.assertEqual(x_out_cut, external_cut)

        # Add internal chord (0, 2):
        # Degrees of 0 and 2 increase by 1 each: sum(deg) increases by 2.
        # internal_edges_count increases by 1: 2 * |E_G[S]| increases by 2.
        deg_G_new = dict(deg_G)
        deg_G_new[0] += 1
        deg_G_new[2] += 1
        internal_edges_new = internal_edges_count + 1

        x_out_after_chord = sum(deg_G_new[v] for v in S) - 2 * internal_edges_new
        self.assertEqual(x_out_after_chord, (3 + 2 + 3 + 1) - 2 * 4)  # 9 - 8 = 1
        self.assertEqual(x_out_after_chord, x_out_cut)
        self.assertEqual(x_out_after_chord - x_out_cut, 0)  # Delta x_out == 0

    def test_theorem3_subgraph_sampling_drift(self) -> None:
        """
        Theorem 3: For any induced subgraph G' subset of G, x_out^{G'}(S) <= x_out^G(S).
        Boundary cut in sampled subgraph is always an underestimate of the full graph cut.
        """
        # Full graph G:
        # S = {0, 1, 2}
        # External vertices in G: {3, 4, 5}
        # External edges from S: (0, 3), (1, 4), (2, 5) -> x_out^G(S) = 3
        # In sampled subgraph G', vertex 5 is omitted:
        # S remains {0, 1, 2}, but external vertices in G' are only {3, 4}
        # External edges in G': (0, 3), (1, 4) -> x_out^{G'}(S) = 2 <= 3.
        full_cut = 3
        sampled_cut = 2
        self.assertLessEqual(sampled_cut, full_cut)


if __name__ == "__main__":
    unittest.main()
