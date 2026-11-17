#!/usr/bin/env python3
"""
Unit and regression tests for scripts/generate_dstar01_report.py.

Addresses R1 / F01, R2-F02, and R2-F05:
Verifies that the report generator fails closed (nonzero exit code and
overall_status != PASS) under all fault conditions:
1. Zero updates executed (ChatGPT review reproduction)
2. Short updates per configuration (< 4 updates)
3. Missing configurations (< 56 configurations)
4. Zero non-empty ENTERED events
5. Zero non-empty EXITED events
6. Tuple mismatch between D* Oracle and Legacy Adapter
7. Uncaught fatal exception mid-matrix
8. Normal complete run (exits 0, overall_status == PASS)
9. Manifest reconciliation mutations (duplicate IDs, missing IDs, unknown IDs,
   missing fixtures, threshold drift, expected-count drift, lambda drift,
   caps drift, matrix drift)
10. Provenance fallback tests (detached HEAD, no-Git mode)
11. Partial failure evidence preservation (completed stages retained in ERROR artifact).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDStarReportGeneratorRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_path = Path(self.temp_dir.name) / "test_report.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_generator_code(self, python_code: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            [sys.executable, "-c", python_code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_normal_complete_run(self) -> None:
        """Normal execution with --output writes valid PASS report and exits 0."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/generate_dstar01_report.py",
                "--output",
                str(self.output_path),
            ],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertTrue(self.output_path.is_file(), "Report file was not created")

        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "PASS")
        summary = data.get("summary", {})
        self.assertEqual(summary.get("legacy_configurations_count"), 56)
        self.assertGreaterEqual(summary.get("legacy_updates_count", 0), 224)
        self.assertGreater(summary.get("legacy_entered_events", 0), 0)
        self.assertGreater(summary.get("legacy_exited_events", 0), 0)
        self.assertGreater(summary.get("legacy_entered_tuples", 0), 0)
        self.assertGreater(summary.get("legacy_exited_tuples", 0), 0)
        self.assertEqual(summary.get("boundary_diagnostics_status"), "PASS")
        self.assertEqual(summary.get("manifest_reconciliation_status"), "PASS")

    def test_zero_updates_fault(self) -> None:
        """
        R1 reproduction: If generate_valid_updates returns [],
        the generator must exit nonzero and record FAIL, not PASS.
        """
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

rep.generate_valid_updates = lambda **kwargs: []

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Zero updates must exit nonzero")
        self.assertTrue(self.output_path.is_file(), "Report artifact must be written on failure")

        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "FAIL")
        leg = data.get("legacy_compatibility", {})
        self.assertEqual(leg.get("status"), "FAIL")
        self.assertEqual(leg.get("total_updates"), 0)
        reasons = leg.get("failure_reasons", [])
        self.assertTrue(
            any("224" in r for r in reasons),
            f"Expected 224 updates error in failure_reasons: {reasons}",
        )

    def test_short_updates_fault(self) -> None:
        """If fewer than 4 updates per config are executed, report must FAIL and exit nonzero."""
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

orig_gen = rep.generate_valid_updates
rep.generate_valid_updates = lambda **kwargs: orig_gen(**kwargs)[:1]

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Short updates must exit nonzero")
        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "FAIL")
        leg = data.get("legacy_compatibility", {})
        self.assertEqual(leg.get("status"), "FAIL")
        self.assertLess(leg.get("total_updates", 0), 224)

    def test_missing_configurations_fault(self) -> None:
        """If fewer than 56 configurations are executed, report must FAIL and exit nonzero."""
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

orig_run_matrix = rep.run_legacy_compatibility_matrix

def mock_run_matrix():
    summary, _ = orig_run_matrix()
    summary["configs"] = summary["configs"][:2]
    summary["total_configurations"] = 2
    is_success, reasons = rep.evaluate_legacy_matrix_success(summary)
    summary["status"] = "PASS" if is_success else "FAIL"
    summary["failure_reasons"] = reasons
    return summary, is_success

rep.run_legacy_compatibility_matrix = mock_run_matrix

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Missing configurations must exit nonzero")
        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "FAIL")
        leg = data.get("legacy_compatibility", {})
        self.assertEqual(leg.get("status"), "FAIL")
        reasons = leg.get("failure_reasons", [])
        self.assertTrue(any("56" in r for r in reasons), f"Expected 56 configs error in {reasons}")

    def test_zero_nonempty_entered_events_fault(self) -> None:
        """If zero non-empty ENTERED events occur across matrix, report must FAIL and exit nonzero."""
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

orig_run_matrix = rep.run_legacy_compatibility_matrix

def mock_run_matrix():
    summary, _ = orig_run_matrix()
    summary["total_entered_events"] = 0
    is_success, reasons = rep.evaluate_legacy_matrix_success(summary)
    summary["status"] = "PASS" if is_success else "FAIL"
    summary["failure_reasons"] = reasons
    return summary, is_success

rep.run_legacy_compatibility_matrix = mock_run_matrix

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Zero ENTERED events must exit nonzero")
        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "FAIL")
        leg = data.get("legacy_compatibility", {})
        reasons = leg.get("failure_reasons", [])
        self.assertTrue(
            any("ENTERED" in r for r in reasons),
            f"Expected zero ENTERED error in {reasons}",
        )

    def test_zero_nonempty_exited_events_fault(self) -> None:
        """If zero non-empty EXITED events occur across matrix, report must FAIL and exit nonzero."""
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

orig_run_matrix = rep.run_legacy_compatibility_matrix

def mock_run_matrix():
    summary, _ = orig_run_matrix()
    summary["total_exited_events"] = 0
    is_success, reasons = rep.evaluate_legacy_matrix_success(summary)
    summary["status"] = "PASS" if is_success else "FAIL"
    summary["failure_reasons"] = reasons
    return summary, is_success

rep.run_legacy_compatibility_matrix = mock_run_matrix

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Zero EXITED events must exit nonzero")
        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "FAIL")
        leg = data.get("legacy_compatibility", {})
        reasons = leg.get("failure_reasons", [])
        self.assertTrue(
            any("EXITED" in r for r in reasons),
            f"Expected zero EXITED error in {reasons}",
        )

    def test_mismatch_tuple_fault(self) -> None:
        """If legacy oracle and D* oracle produce mismatch tuples, report must FAIL and exit nonzero."""
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

orig_apx_oracle_cls = rep.APXDynamicOracle

class CorruptingAPXOracle(orig_apx_oracle_cls):
    def apply_update(self, update):
        res = super().apply_update(update)
        res.entered[(9999, 9999)] = None
        return res

rep.APXDynamicOracle = CorruptingAPXOracle

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Tuple mismatch must exit nonzero")
        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "FAIL")
        leg = data.get("legacy_compatibility", {})
        self.assertEqual(leg.get("status"), "FAIL")

    def test_exception_mid_matrix(self) -> None:
        """If uncaught exception occurs mid-execution, writes ERROR artifact and exits nonzero."""
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

def bomb():
    raise RuntimeError("Simulated mid-matrix hardware fault")

rep.run_legacy_compatibility_matrix = bomb

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        proc = self._run_generator_code(code)
        self.assertNotEqual(proc.returncode, 0, "Exception must exit nonzero")
        self.assertTrue(self.output_path.is_file(), "Error report must be written on fatal exception")

        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "ERROR")
        self.assertIn("Simulated mid-matrix hardware fault", data.get("fatal_error", ""))


class TestDStarManifestReconciliationNegative(unittest.TestCase):
    """
    R2-F02: Negative mutation tests verifying that reconcile_manifest fails closed
    and report generator exits nonzero with status != PASS on any manifest drift.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temp_dir.name) / "test_manifest.json"
        self.output_path = Path(self.temp_dir.name) / "test_report.json"
        with open(REPO_ROOT / "configs" / "dstar01_cases.json", "r", encoding="utf-8") as f:
            self.valid_manifest = json.load(f)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_generator_with_manifest(self, mutated_manifest: dict) -> subprocess.CompletedProcess:
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(mutated_manifest, f, indent=2)

        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

orig_reconcile = rep.reconcile_manifest
rep.reconcile_manifest = lambda p, h, l: orig_reconcile(Path("{self.manifest_path}"), h, l)

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_duplicate_hand_case_id_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["hand_cases"].append(m["hand_cases"][0])  # Duplicate CASE_HAND_A
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertEqual(data.get("manifest_reconciliation", {}).get("status"), "FAIL")
        self.assertTrue(any("Duplicate" in e for e in data["manifest_reconciliation"]["errors"]))

    def test_missing_required_hand_case_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["hand_cases"] = m["hand_cases"][1:]  # Drop CASE_HAND_A
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("missing" in e.lower() for e in data["manifest_reconciliation"]["errors"]))

    def test_unknown_hand_case_id_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["hand_cases"].append({"id": "CASE_HAND_UNKNOWN_99", "k": 0})
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("unknown" in e.lower() for e in data["manifest_reconciliation"]["errors"]))

    def test_missing_fixture_reference_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        # Point Case B to non-existent fixture
        m["hand_cases"][1]["query"] = "tests/fixtures/dstar01/nonexistent_query.json"
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("missing on disk" in e or "path mismatch" in e for e in data["manifest_reconciliation"]["errors"]))

    def test_threshold_drift_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["hand_cases"][0]["k"] = 999  # Case A k changed from 0 to 999
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("k mismatch" in e for e in data["manifest_reconciliation"]["errors"]))

    def test_expected_count_drift_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        # Find Case H (expected_count=24) and mutate to 999
        for c in m["hand_cases"]:
            if c["id"] == "CASE_HAND_H":
                c["expected_count"] = 999
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("expected_count mismatch" in e for e in data["manifest_reconciliation"]["errors"]))

    def test_lambda_value_drift_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["lambda_reference"]["K4"]["lambda"] = 99  # Should be 3
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("lambda mismatch" in e for e in data["manifest_reconciliation"]["errors"]))

    def test_caps_drift_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["resource_guards"]["max_query_vertices"] = 999  # Should be 6
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("max_query_vertices mismatch" in e for e in data["manifest_reconciliation"]["errors"]))

    def test_seed_matrix_drift_rejected(self) -> None:
        m = json.loads(json.dumps(self.valid_manifest))
        m["seeded_compatibility"]["total_configurations"] = 999  # Should be 56
        proc = self._run_generator_with_manifest(m)
        self.assertNotEqual(proc.returncode, 0)
        with open(self.output_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("overall_status"), "FAIL")
        self.assertTrue(any("total_configurations mismatch" in e for e in data["manifest_reconciliation"]["errors"]))


class TestDStarProvenanceAndPartialFailure(unittest.TestCase):
    """
    R2-F05: Tests for verified Git observations (no hardcoded fallbacks)
    and preservation of partial execution evidence on fatal exception.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_path = Path(self.temp_dir.name) / "test_report.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_git_unavailable_provenance(self) -> None:
        """When Git is unavailable, metadata records explicit UNKNOWN/UNAVAILABLE reasons."""
        from scripts.generate_dstar01_report import get_git_metadata

        with patch("subprocess.check_output", side_effect=FileNotFoundError("git binary not found")):
            meta = get_git_metadata()

        self.assertIn("UNKNOWN", meta["execution_head_sha"])
        self.assertIn("UNKNOWN", meta["branch"])
        self.assertIn("UNKNOWN", str(meta["is_dirty_at_runtime"]))
        self.assertIn("UNAVAILABLE", meta["observed_merge_base_sha"])
        self.assertIn("UNAVAILABLE", meta["observed_origin_quangthang_sha"])
        self.assertEqual(meta["configured_historical_base_sha"], "89f65b1490ba2e31d8b599d295065dbe694460aa")

    def test_fault_after_completed_stages_preserves_partial_evidence(self) -> None:
        """
        When a fatal exception occurs in stage 5 (BOUNDARY_DIAGNOSTICS),
        the error report must preserve partial results from stages 1-4.
        """
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

def crash_boundary(*args, **kwargs):
    raise RuntimeError("Simulated crash in boundary diagnostics")

rep.run_boundary_diagnostics = crash_boundary

sys.argv = ["generate_dstar01_report.py", "--output", "{self.output_path}"]
rep.main()
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.output_path.is_file())

        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "ERROR")
        self.assertEqual(data.get("failing_stage"), "BOUNDARY_DIAGNOSTICS")
        self.assertIn("Simulated crash in boundary diagnostics", data.get("fatal_error", ""))
        self.assertTrue(bool(data.get("run_id")))

        partial = data.get("partial_results", {})
        self.assertIn("hand_cases", partial)
        self.assertIn("lambda_topologies", partial)
        self.assertIn("atomic_checks", partial)
        self.assertIn("legacy_compatibility", partial)
        self.assertNotIn("boundary_diagnostics", partial)


if __name__ == "__main__":
    unittest.main()
