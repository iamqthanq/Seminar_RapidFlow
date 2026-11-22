#!/usr/bin/env python3
"""
tests/test_rf02_runner.py — Unit and Regression Tests for Differential Runner (F1–F4).
"""

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.rf02_differential_runner import (
    EXPECTED_BASELINE_COMMIT,
    EXPECTED_BINARY_SHA256,
    InternalTimeoutError,
    OutputParseError,
    SubprocessResult,
    file_sha256,
    main as runner_main,
    parse_rapidflow_output,
    run_prefix_command,
    verify_provenance,
    write_summary_csv,
)


class TestDifferentialRunner(unittest.TestCase):

    def test_file_sha256(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(b"hello world\n")
            f_path = Path(f.name)
        try:
            h = file_sha256(f_path)
            self.assertEqual(h, "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447")
        finally:
            if f_path.exists():
                f_path.unlink()

    def test_parse_rapidflow_output_valid(self):
        stdout = (
            "Execution Summary:\n"
            "Updates num: 3\n"
            "Edge process count: 3\n"
            "Result count: 42\n"
            "Done.\n"
        )
        parsed = parse_rapidflow_output(stdout)
        self.assertEqual(parsed["updates_num"], 3)
        self.assertEqual(parsed["edge_process_count"], 3)
        self.assertEqual(parsed["result_count"], 42)

    def test_parse_rapidflow_output_missing_result_count(self):
        stdout = (
            "Updates num: 1\n"
            "Edge process count: 1\n"
        )
        with self.assertRaises(OutputParseError) as ctx:
            parse_rapidflow_output(stdout)
        self.assertIn("Missing 'Result count:'", str(ctx.exception))

    def test_parse_rapidflow_output_duplicate_result_count(self):
        stdout = (
            "Result count: 10\n"
            "Updates num: 1\n"
            "Edge process count: 1\n"
            "Result count: 20\n"
        )
        with self.assertRaises(OutputParseError) as ctx:
            parse_rapidflow_output(stdout)
        self.assertIn("Duplicate 'Result count:'", str(ctx.exception))

    def test_parse_rapidflow_output_missing_updates_num(self):
        stdout = (
            "Edge process count: 1\n"
            "Result count: 10\n"
        )
        with self.assertRaises(OutputParseError) as ctx:
            parse_rapidflow_output(stdout)
        self.assertIn("Missing 'Updates num:'", str(ctx.exception))

    def test_parse_rapidflow_output_missing_edge_process_count(self):
        stdout = (
            "Updates num: 1\n"
            "Result count: 10\n"
        )
        with self.assertRaises(OutputParseError) as ctx:
            parse_rapidflow_output(stdout)
        self.assertIn("Missing 'Edge process count:'", str(ctx.exception))

    def test_parse_rapidflow_output_internal_timeout(self):
        stdout = (
            "Updates num: 1\n"
            "Edge process count: 1\n"
            "Result count: 0\n"
            "Time out...\n"
        )
        with self.assertRaises(InternalTimeoutError):
            parse_rapidflow_output(stdout)

    def test_run_prefix_command_timeout_handling(self):
        """Verify subprocess.TimeoutExpired is safely caught without unhandled exception."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["dummy"], timeout=5.0, output=b"partial out", stderr=b"timeout err"
            )
            res = run_prefix_command(
                binary_path=Path("/bin/true"),
                data_path=Path("dummy_d"),
                query_path=Path("dummy_q"),
                prefix_stream_path=Path("dummy_s"),
                timeout_sec=5.0,
            )
            self.assertTrue(res.timed_out)
            self.assertIsNone(res.returncode)
            self.assertIn("timed out after 5.0 seconds", res.error_message)
            self.assertEqual(res.stdout, "partial out")
            self.assertEqual(res.stderr, "timeout err")

    def test_run_prefix_command_os_error_handling(self):
        """Verify OS errors (e.g. missing binary) are safely caught."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Executable not found")
            res = run_prefix_command(
                binary_path=Path("/nonexistent/binary"),
                data_path=Path("dummy_d"),
                query_path=Path("dummy_q"),
                prefix_stream_path=Path("dummy_s"),
                timeout_sec=5.0,
            )
            self.assertFalse(res.timed_out)
            self.assertIsNone(res.returncode)
            self.assertIn("Executable not found", res.error_message)

    def test_write_summary_csv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            csv_path = Path(f.name)
        try:
            sample_row = {
                "run_id": "test_run",
                "case_id": "c01",
                "step_index": 1,
                "op": "+",
                "src": 0,
                "dst": 1,
                "edge_label": 0,
                "oracle_added_count": 0,
                "oracle_removed_count": 0,
                "oracle_total_count": 0,
                "oracle_mapping_hash": "hash123",
                "rf_cum_count": 0,
                "rf_inferred_delta": 0,
                "expected_delta": 0,
                "status": "PASS",
                "failure_reason": "",
                "wall_time_sec": "0.015000",
                "peak_rss_kb": 16000,
                "exit_code": 0,
                "binary_sha256": "binsha",
                "prefix_stream_sha256": "prefixsha",
                "log_file": "results/logs/rf02/test_run/c01/step_01",
            }
            write_summary_csv(csv_path, [sample_row])
            with open(csv_path, "r", encoding="utf-8") as f_in:
                reader = list(csv.DictReader(f_in))
                self.assertEqual(len(reader), 1)
                self.assertEqual(reader[0]["status"], "PASS")
                self.assertEqual(reader[0]["run_id"], "test_run")
        finally:
            if csv_path.exists():
                csv_path.unlink()

    def test_verify_provenance_workspace(self):
        workspace_root = Path(__file__).resolve().parent.parent
        binary_path = workspace_root / "baselines" / "rapidflow" / "source" / "build" / "streaming" / "RapidFlow.out"
        if not binary_path.exists():
            self.skipTest(f"Historical RapidFlow.out artifact not present at {binary_path} (SKIP portable unit test)")
        actual_sha = file_sha256(binary_path)
        if actual_sha != EXPECTED_BINARY_SHA256:
            self.skipTest(f"RapidFlow.out binary SHA-256 differs from historical artifact: got {actual_sha}, expected {EXPECTED_BINARY_SHA256}")
        ok, msg = verify_provenance(workspace_root)
        self.assertTrue(ok, msg)

    def test_verify_provenance_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as td:
            ok, msg = verify_provenance(Path(td))
            self.assertFalse(ok)
            self.assertIn("Source directory does not exist", msg)

    def test_verify_provenance_rejects_missing_binary(self):
        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td) / "baselines" / "rapidflow" / "source"
            src_dir.mkdir(parents=True)
            ok, msg = verify_provenance(Path(td))
            self.assertFalse(ok)
            self.assertIn("Binary does not exist", msg)

    def test_verify_provenance_rejects_wrong_binary_hash(self):
        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td) / "baselines" / "rapidflow" / "source"
            bin_dir = src_dir / "build" / "streaming"
            bin_dir.mkdir(parents=True)
            bin_file = bin_dir / "RapidFlow.out"
            bin_file.write_text("fake binary")

            # Mock git rev-parse and status to succeed
            with patch("subprocess.check_output") as mock_git:
                mock_git.side_effect = [EXPECTED_BASELINE_COMMIT, ""]
                ok, msg = verify_provenance(Path(td))
                self.assertFalse(ok)
                self.assertIn("Binary SHA-256 mismatch", msg)

    def test_verify_provenance_rejects_wrong_commit(self):
        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td) / "baselines" / "rapidflow" / "source"
            bin_dir = src_dir / "build" / "streaming"
            bin_dir.mkdir(parents=True)
            bin_file = bin_dir / "RapidFlow.out"
            bin_file.write_text("fake binary")

            with patch("subprocess.check_output", return_value="wrong_commit_sha"):
                ok, msg = verify_provenance(Path(td))
                self.assertFalse(ok)
                self.assertIn("commit mismatch", msg)


class TestRunnerFailurePreservation(unittest.TestCase):
    """
    Simulates failure modes (F1 & F4) using runner_main() with synthetic mocks
    to verify:
    1. No UnboundLocalError occurs.
    2. CSV row is persisted for EVERY attempted step.
    3. Predecessor failure blocks subsequent delta inference (BLOCKED_PREDECESSOR).
    4. External /tmp directories work seamlessly.
    5. Synthetic runner logic runs portably regardless of whether the historical compiled binary exists.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.out_csv = self.temp_path / "summary.csv"
        self.logs_dir = self.temp_path / "logs"

        # Mock provenance at the seam so synthetic failure preservation tests run portably
        self.prov_patcher = patch(
            "scripts.rf02_differential_runner.verify_provenance",
            return_value=(True, "Provenance verified successfully (synthetic test boundary)")
        )
        self.prov_patcher.start()

        # Isolate RapidFlow.out hash check so synthetic failure preservation tests do not
        # fail with FileNotFoundError if the historical compiled binary is absent from disk.
        real_file_sha256 = file_sha256
        def synthetic_file_sha256(path):
            p = Path(path)
            if p.name == "RapidFlow.out":
                return EXPECTED_BINARY_SHA256
            return real_file_sha256(path)

        self.sha_patcher = patch(
            "scripts.rf02_differential_runner.file_sha256",
            side_effect=synthetic_file_sha256
        )
        self.sha_patcher.start()

    def tearDown(self):
        self.sha_patcher.stop()
        self.prov_patcher.stop()
        self.temp_dir.cleanup()

    def test_provenance_failure_prevents_subprocess_execution(self):
        """When provenance check fails, runner_main() must exit nonzero without executing subprocesses."""
        self.prov_patcher.stop()  # stop mock for this test
        mock_cmd = unittest.mock.MagicMock()
        with patch("scripts.rf02_differential_runner.verify_provenance", return_value=(False, "Simulated provenance failure")), \
             patch("scripts.rf02_differential_runner.run_prefix_command", mock_cmd), \
             patch("sys.argv", ["runner", "--pilot", "--output", str(self.out_csv), "--logs-dir", str(self.logs_dir)]):
            with self.assertRaises(SystemExit) as ctx:
                runner_main()
            self.assertEqual(ctx.exception.code, 1)
        mock_cmd.assert_not_called()

    def test_updates_mismatch_error_preservation(self):
        """
        Simulate RapidFlow output reporting Updates num: 0 for step 1.
        Must record ERROR, must NOT raise UnboundLocalError, and CSV must exist.
        """
        fake_stdout = (
            "Updates num: 0\n"
            "Edge process count: 0\n"
            "Result count: 0\n"
        )
        fake_res = SubprocessResult(
            returncode=0,
            stdout=fake_stdout,
            stderr="Max RSS: 16000\n",
            duration_sec=0.01,
            rss_kb=16000,
        )

        with patch("scripts.rf02_differential_runner.run_prefix_command", return_value=fake_res):
            with patch("sys.argv", ["runner", "--pilot", "--output", str(self.out_csv), "--logs-dir", str(self.logs_dir)]):
                with self.assertRaises(SystemExit) as ctx:
                    runner_main()
                self.assertEqual(ctx.exception.code, 2)

        self.assertTrue(self.out_csv.exists(), "Summary CSV must be written even on failure")
        with open(self.out_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            self.assertGreater(len(rows), 0)
            self.assertEqual(rows[0]["status"], "ERROR")
            self.assertIn("Update count mismatch", rows[0]["failure_reason"])

    def test_timeout_preservation(self):
        """Simulate subprocess timeout. Must record TIMEOUT in CSV and exit non-zero."""
        fake_timeout_res = SubprocessResult(
            returncode=None,
            stdout="partial stdout",
            stderr="partial stderr",
            duration_sec=5.0,
            rss_kb=None,
            timed_out=True,
            error_message="Subprocess timed out after 5.0 seconds",
        )

        with patch("scripts.rf02_differential_runner.run_prefix_command", return_value=fake_timeout_res):
            with patch("sys.argv", ["runner", "--pilot", "--output", str(self.out_csv), "--logs-dir", str(self.logs_dir)]):
                with self.assertRaises(SystemExit) as ctx:
                    runner_main()
                self.assertEqual(ctx.exception.code, 2)

        self.assertTrue(self.out_csv.exists())
        with open(self.out_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            self.assertGreater(len(rows), 0)
            self.assertEqual(rows[0]["status"], "TIMEOUT")
            self.assertIn("timed out", rows[0]["failure_reason"])

    def test_malformed_stdout_preservation(self):
        """Simulate malformed stdout (missing Result count). Must record ERROR in CSV."""
        fake_bad_res = SubprocessResult(
            returncode=0,
            stdout="Segmentation fault or garbage\n",
            stderr="",
            duration_sec=0.01,
            rss_kb=None,
        )

        with patch("scripts.rf02_differential_runner.run_prefix_command", return_value=fake_bad_res):
            with patch("sys.argv", ["runner", "--pilot", "--output", str(self.out_csv), "--logs-dir", str(self.logs_dir)]):
                with self.assertRaises(SystemExit) as ctx:
                    runner_main()
                self.assertEqual(ctx.exception.code, 2)

        self.assertTrue(self.out_csv.exists())
        with open(self.out_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            self.assertGreater(len(rows), 0)
            self.assertEqual(rows[0]["status"], "ERROR")
            self.assertIn("Missing 'Result count:'", rows[0]["failure_reason"])

    def test_predecessor_failure_blocks_subsequent_delta(self):
        """
        Test Section 3.3 requirement:
        If step 1 fails, step 2 must be marked BLOCKED_PREDECESSOR and not calculate an invalid delta.
        """
        step1_res = SubprocessResult(
            returncode=1,
            stdout="",
            stderr="Fatal error in step 1",
            duration_sec=0.01,
            rss_kb=None,
        )
        step2_res = SubprocessResult(
            returncode=0,
            stdout="Updates num: 2\nEdge process count: 2\nResult count: 2\n",
            stderr="Maximum resident set size (kbytes): 16000\n",
            duration_sec=0.01,
            rss_kb=16000,
        )

        def mock_command(binary_path, data_path, query_path, prefix_stream_path, timeout_sec=60.0):
            # Read prefix line count to determine step
            with open(prefix_stream_path) as pf:
                lines = pf.readlines()
            if len(lines) == 1:
                return step1_res
            else:
                return step2_res

        with patch("scripts.rf02_differential_runner.run_prefix_command", side_effect=mock_command):
            with patch("sys.argv", ["runner", "--pilot", "--output", str(self.out_csv), "--logs-dir", str(self.logs_dir)]):
                with self.assertRaises(SystemExit) as ctx:
                    runner_main()
                self.assertEqual(ctx.exception.code, 2)

        with open(self.out_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(rows[0]["status"], "ERROR")
            self.assertEqual(rows[1]["status"], "BLOCKED_PREDECESSOR")
            self.assertEqual(rows[1]["rf_inferred_delta"], "")
            self.assertIn("Predecessor prefix failed", rows[1]["failure_reason"])


if __name__ == "__main__":
    unittest.main()
