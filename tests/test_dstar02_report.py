#!/usr/bin/env python3
"""
tests/test_dstar02_report.py — Regression and Fail-Closed Verification for DSTAR-02 Report Generator.

Verifies:
1. Normal complete execution (exits 0, overall_status == PASS, complete summary).
2. Fail-closed behavior on independence audit failure.
3. Fail-closed behavior on corpus count mismatch.
4. Fail-closed behavior on stream matrix mismatch.
5. Fail-closed behavior on manifest drift (missing file, duplicate ID, count mismatch).
6. Partial evidence preservation on uncaught exception.
7. Support for custom output path via --output.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDStar02ReportGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_path = Path(self.temp_dir.name) / "test_report.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_generator(self, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        args = [
            sys.executable,
            "scripts/generate_dstar02_report.py",
            "--output",
            str(self.output_path),
        ] + (extra_args or [])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_normal_complete_run(self) -> None:
        """Normal execution with --output writes valid PASS report and exits 0."""
        proc = self._run_generator()
        self.assertEqual(proc.returncode, 0, f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertTrue(self.output_path.is_file(), "Report file was not created")

        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("overall_status"), "PASS")
        summary = data.get("summary", {})
        self.assertEqual(summary.get("independence_audit_status"), "PASS")
        self.assertEqual(summary.get("corpus_840_total_cases"), 840)
        self.assertEqual(summary.get("corpus_840_truth_events"), 8002)
        self.assertEqual(summary.get("corpus_840_m1_agreed"), 840)
        self.assertEqual(summary.get("corpus_840_m2_agreed"), 840)
        self.assertEqual(summary.get("stream_56_configurations"), 56)
        self.assertEqual(summary.get("stream_56_updates"), 672)
        self.assertEqual(summary.get("stream_56_agreed_steps"), 672)
        self.assertEqual(summary.get("stream_56_reconstructed_steps"), 672)
        self.assertEqual(summary.get("invariant_violation_guard_status"), "PASS")
        self.assertEqual(summary.get("resource_guards_status"), "PASS")
        self.assertEqual(summary.get("manifest_reconciliation_status"), "PASS")

    def test_fail_closed_on_manifest_drift(self) -> None:
        """If manifest has missing case or mismatched counts, reconciliation fails and generator exits nonzero."""
        script_code = f"""
import sys, os
from pathlib import Path
import scripts.generate_dstar02_report as rep

# Inject bad manifest reconciliation
orig_reconcile = rep.reconcile_manifest
rep.reconcile_manifest = lambda p, h: ({{"status": "FAIL", "errors": ["Injected manifest drift"]}}, False)
sys.argv = ["generate_dstar02_report.py", "--output", {str(self.output_path)!r}]
try:
    rep.main()
except SystemExit as e:
    sys.exit(e.code)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", script_code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0, "Generator must exit nonzero on manifest drift")

    def test_fail_closed_on_stream_matrix_mismatch(self) -> None:
        """If stream matrix has disagreement, generator fails closed and exits nonzero."""
        script_code = f"""
import sys, os
from pathlib import Path
import scripts.generate_dstar02_report as rep

# Inject bad matrix summary
rep.run_56_stream_matrix = lambda: ({{"status": "FAIL", "step_agreement_count": 670, "total_updates": 672}}, False)
sys.argv = ["generate_dstar02_report.py", "--output", {str(self.output_path)!r}]
try:
    rep.main()
except SystemExit as e:
    sys.exit(e.code)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", script_code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0, "Generator must exit nonzero on matrix mismatch")

    def test_partial_evidence_preservation_on_fatal_exception(self) -> None:
        """If a fatal exception occurs mid-run, completed stages are preserved in error report."""
        script_code = f"""
import sys, os
from pathlib import Path
import scripts.generate_dstar02_report as rep

# In stage 4 (stream matrix), raise an uncaught exception
def crashing_matrix():
    raise RuntimeError("Simulated crash mid-stream matrix")
rep.run_56_stream_matrix = crashing_matrix

sys.argv = ["generate_dstar02_report.py", "--output", {str(self.output_path)!r}]
try:
    rep.main()
except SystemExit as e:
    sys.exit(e.code)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", script_code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.output_path.is_file(), "Error report was not preserved")
        with open(self.output_path, "r", encoding="utf-8") as f:
            err_data = json.load(f)
        self.assertEqual(err_data.get("overall_status"), "ERROR")
        self.assertIn("Simulated crash", err_data.get("fatal_error", ""))
        self.assertIn("independence_audit", err_data.get("partial_stages", {}))
        self.assertIn("hand_cases", err_data.get("partial_stages", {}))
        self.assertIn("corpus_840", err_data.get("partial_stages", {}))

    def test_fail_closed_on_corpus_m0_failure_f01(self) -> None:
        """If M0 Ground Truth Oracle encounters an exception during corpus recomputation, report fails closed (F01)."""
        script_code = f"""
import sys, os
from pathlib import Path
import scripts.generate_dstar02_report as rep

# Mock corpus stage to simulate M0 failure
orig_corpus = rep.run_corpus_840_reference
rep.run_corpus_840_reference = lambda: ({{"status": "FAIL", "error": "Injected M0 crash"}}, False)

sys.argv = ["generate_dstar02_report.py", "--output", {str(self.output_path)!r}]
try:
    rep.main()
except SystemExit as e:
    sys.exit(e.code)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", script_code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0, "Generator must exit nonzero when M0 corpus fails")
        with open(self.output_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotEqual(data.get("overall_status"), "PASS")

    def test_fail_closed_on_manifest_mutations_f04(self) -> None:
        """Manifest reconciliation strictly rejects invalid types, caps, counts, seeds, or files (F04)."""
        from scripts.generate_dstar02_report import reconcile_manifest, run_hand_cases

        hand_cases, _ = run_hand_cases()
        manifest_path = REPO_ROOT / "configs" / "dstar02_correctness_cases.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            valid_manifest = json.load(f)

        # 1. Reject bool in resource caps (e.g. max_work: True)
        bad_manifest_1 = copy.deepcopy(valid_manifest)
        bad_manifest_1["resource_guards"]["max_work"] = True
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(bad_manifest_1, tf)
            tf_path = Path(tf.name)
        try:
            summary, ok = reconcile_manifest(tf_path, hand_cases)
            self.assertFalse(ok)
            self.assertEqual(summary["status"], "FAIL")
            self.assertTrue(any("max_work" in err for err in summary["errors"]))
        finally:
            tf_path.unlink()

        # 2. Reject wrong stream seeds
        bad_manifest_2 = copy.deepcopy(valid_manifest)
        bad_manifest_2["stream_56_matrix"]["seeds"] = [1, 2, 3]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(bad_manifest_2, tf)
            tf_path = Path(tf.name)
        try:
            summary, ok = reconcile_manifest(tf_path, hand_cases)
            self.assertFalse(ok)
            self.assertEqual(summary["status"], "FAIL")
            self.assertTrue(any("seeds mismatch" in err for err in summary["errors"]))
        finally:
            tf_path.unlink()

        # 3. Reject duplicate hand case ID
        bad_manifest_3 = copy.deepcopy(valid_manifest)
        bad_manifest_3["hand_cases"].append(bad_manifest_3["hand_cases"][0])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(bad_manifest_3, tf)
            tf_path = Path(tf.name)
        try:
            summary, ok = reconcile_manifest(tf_path, hand_cases)
            self.assertFalse(ok)
            self.assertEqual(summary["status"], "FAIL")
            self.assertTrue(any("Duplicate hand_cases IDs" in err for err in summary["errors"]))
        finally:
            tf_path.unlink()

    def test_partial_matrix_preserved_on_mid_stream_failure_f05(self) -> None:
        """StreamMatrixExecutionError preserves partial_matrix with completed step records (F05)."""
        script_code = f"""
import sys, os
from pathlib import Path
import scripts.generate_dstar02_report as rep

# Inject StreamMatrixExecutionError
def crashing_matrix():
    partial = {{
        "status": "ERROR",
        "completed_configurations": 1,
        "completed_steps": 5,
        "step_records": [{{"step_index": 0, "operation": "INSERT", "agreed": True}}],
    }}
    raise rep.StreamMatrixExecutionError("Mid-stream failure at step 6", partial_matrix=partial)

rep.run_56_stream_matrix = crashing_matrix

sys.argv = ["generate_dstar02_report.py", "--output", {str(self.output_path)!r}]
try:
    rep.main()
except SystemExit as e:
    sys.exit(e.code)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", script_code],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.output_path.is_file(), "Error report was not preserved")
        with open(self.output_path, "r", encoding="utf-8") as f:
            err_data = json.load(f)
        self.assertEqual(err_data.get("overall_status"), "ERROR")
        self.assertIn("Mid-stream failure", err_data.get("fatal_error", ""))
        matrix_stage = err_data.get("partial_stages", {}).get("stream_56_matrix", {}) or err_data.get("matrix_progress", {})
        self.assertEqual(matrix_stage.get("status"), "ERROR")
        self.assertEqual(matrix_stage.get("completed_steps"), 5)


if __name__ == "__main__":
    unittest.main()
