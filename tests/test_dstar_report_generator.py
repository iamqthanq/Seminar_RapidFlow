#!/usr/bin/env python3
"""
Unit and regression tests for scripts/generate_dstar01_report.py.

Addresses R1 / F01:
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

Both process exit code and output JSON artifact are verified.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_zero_updates_fault(self) -> None:
        """
        R1 reproduction: If generate_valid_updates returns [],
        the generator must exit nonzero and record FAIL, not PASS.
        """
        code = f"""
import sys
from pathlib import Path
import scripts.generate_dstar01_report as rep

# Fault injection: zero updates generated
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
# Fault injection: only 1 update instead of 4
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

# Fast run with only 2 configs
orig_run_matrix = rep.run_legacy_compatibility_matrix

def mock_run_matrix():
    summary, _ = orig_run_matrix()
    # Truncate configs to 2
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
        # Corrupt entered mapping to cause tuple mismatch
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


if __name__ == "__main__":
    unittest.main()
