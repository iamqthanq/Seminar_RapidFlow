#!/usr/bin/env python3
"""
tests/test_rf03_runner.py — Unit and Negative Adversarial Tests for RF-03 Runner.

Tests all failure modes, parse validation, provenance verification, resource capping,
timeout handling, multi-repeat consistency, and log preservation using isolated mocks
without running heavy NetFlow computations.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import scripts.rf03_netflow_runner as runner


class TestRF03OutputParsing(unittest.TestCase):
    """Tests fail-closed output parsing logic."""

    SAMPLE_VALID_STDOUT = """Command Line:
	Data Graph: /path/to/data.graph
	Data Graph Update: /path/to/stream.graph
	Query Graph: /path/to/query.graph
	Target Number of Embeddings: 18446744073709551615
	Time limit (seconds): 60
--------------------------------------------------------------------
Load stream file time (seconds): 0.010000
Data Graph:
|V|: 3114895, |E|: 2849732, |Σ|: 2
Max Degree: 49014, Max Label Frequency: 3114895
Query Graph:
|V|: 5, |E|: 6, |Σ|: 2
Max Degree: 3, Max Label Frequency: 5
Update:
Updates num: 100
--------------------------------------------------------------------
Preprocess....
Preprocessing time (seconds): 1.850000
--------------------------------------------------------------------
Evaluate the performance of search...
--------------------------------------------------------------------
Execution Summary:
Preprocessing time (seconds): 1.850000
Query time (seconds): 0.050000
Global view update time (seconds): 0.010000
Local view update time (seconds): 0.015000
Search time (seconds): 0.025000
Edge process count: 100
Relevant update count: 42
Search count: 15
Positive update count: 8
Result count: 120
Partial result count (exclude results): 30
Invalid partial count: 5
ISO conflict count: 2
SI empty count: 1
LC empty count: 0
--------------------------------------------------------------------
Done.
"""

    def test_valid_stdout_parsing(self):
        metrics = runner.parse_rapidflow_stdout(self.SAMPLE_VALID_STDOUT)
        self.assertEqual(metrics.updates_num, 100)
        self.assertEqual(metrics.result_count, 120)
        self.assertEqual(metrics.edge_process_count, 100)
        self.assertEqual(metrics.preprocessing_time_sec, 1.85)
        self.assertEqual(metrics.query_time_sec, 0.05)
        self.assertEqual(metrics.gv_update_time_sec, 0.01)
        self.assertEqual(metrics.lv_update_time_sec, 0.015)
        self.assertEqual(metrics.search_time_sec, 0.025)
        self.assertEqual(metrics.relevant_update_count, 42)
        self.assertEqual(metrics.search_count, 15)
        self.assertEqual(metrics.positive_update_count, 8)
        self.assertEqual(metrics.partial_result_count, 30)
        self.assertEqual(metrics.invalid_partial_count, 5)
        self.assertEqual(metrics.iso_conflict_count, 2)
        self.assertEqual(metrics.si_empty_count, 1)
        self.assertEqual(metrics.lc_empty_count, 0)
        self.assertFalse(metrics.internal_timeout)

    def test_missing_result_count_raises(self):
        bad_stdout = self.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "")
        with self.assertRaises(runner.OutputParseError) as ctx:
            runner.parse_rapidflow_stdout(bad_stdout)
        self.assertIn("Missing 'Result count:'", str(ctx.exception))

    def test_duplicate_result_count_raises(self):
        bad_stdout = self.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 120\nResult count: 120\n")
        with self.assertRaises(runner.OutputParseError) as ctx:
            runner.parse_rapidflow_stdout(bad_stdout)
        self.assertIn("Duplicate 'Result count:'", str(ctx.exception))

    def test_missing_updates_num_raises(self):
        bad_stdout = self.SAMPLE_VALID_STDOUT.replace("Updates num: 100\n", "")
        with self.assertRaises(runner.OutputParseError) as ctx:
            runner.parse_rapidflow_stdout(bad_stdout)
        self.assertIn("Missing 'Updates num:'", str(ctx.exception))

    def test_missing_edge_process_count_raises(self):
        bad_stdout = self.SAMPLE_VALID_STDOUT.replace("Edge process count: 100\n", "")
        with self.assertRaises(runner.OutputParseError) as ctx:
            runner.parse_rapidflow_stdout(bad_stdout)
        self.assertIn("Missing 'Edge process count:'", str(ctx.exception))

    def test_internal_timeout_sentinel_detected(self):
        timeout_stdout = self.SAMPLE_VALID_STDOUT + "\nTime out...\nDone.\n"
        metrics = runner.parse_rapidflow_stdout(timeout_stdout)
        self.assertTrue(metrics.internal_timeout)


class TestRF03ProvenanceVerification(unittest.TestCase):
    """Tests fail-closed provenance validation."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp_dir.name)
        self.src = self.base / "source"
        self.src.mkdir(parents=True)
        self.bin = self.src / "RapidFlow.out"
        self.bin.write_bytes(b"dummy_binary")
        self.data = self.base / "Ndata.graph"
        self.data.write_bytes(b"dummy_data")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_missing_source_fails(self):
        ok, msg = runner.verify_provenance(
            source_dir=self.base / "nonexistent",
            binary_path=self.bin,
            data_graph_path=self.data,
            skip_git=True,
        )
        self.assertFalse(ok)
        self.assertIn("source directory missing", msg)

    def test_missing_binary_fails(self):
        ok, msg = runner.verify_provenance(
            source_dir=self.src,
            binary_path=self.base / "nonexistent.out",
            data_graph_path=self.data,
            skip_git=True,
        )
        self.assertFalse(ok)
        self.assertIn("binary executable missing", msg)

    def test_binary_sha_mismatch_fails(self):
        ok, msg = runner.verify_provenance(
            source_dir=self.src,
            binary_path=self.bin,
            data_graph_path=self.data,
            skip_git=True,
        )
        self.assertFalse(ok)
        self.assertIn("Binary SHA mismatch", msg)

    def test_data_graph_sha_mismatch_fails(self):
        # Even if binary matches expected sha, mismatched data graph fails
        with patch("scripts.rf03_netflow_runner.file_sha256", side_effect=[
            runner.EXPECTED_BINARY_SHA256,
            "bad_data_sha"
        ]):
            ok, msg = runner.verify_provenance(
                source_dir=self.src,
                binary_path=self.bin,
                data_graph_path=self.data,
                skip_git=True,
            )
            self.assertFalse(ok)
            self.assertIn("Data graph SHA mismatch", msg)


class TestRF03RunnerFailurePreservation(unittest.TestCase):
    """Tests failure preservation and resource caps in runner executions."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp_dir.name)

        # Create dummy graph, query, stream
        self.data_graph = self.base / "Ndata.graph"
        self.data_graph.write_text("v 0 0\n")
        self.data_sha = runner.file_sha256(self.data_graph)

        self.query_file = self.base / "Q_0"
        self.query_file.write_text("v 0 0\nv 1 0\ne 0 1 0\n")
        self.query_sha = runner.file_sha256(self.query_file)

        self.stream_file = self.base / "stream.graph"
        self.stream_file.write_text("e 0 1 0\n")
        self.stream_sha = runner.file_sha256(self.stream_file)

        self.bin_file = self.base / "RapidFlow.out"
        self.bin_file.write_bytes(b"bin")
        self.bin_sha = runner.file_sha256(self.bin_file)

        # Create mock config
        self.cfg_file = self.base / "rf03_workload.json"
        self.cfg = {
            "schema_version": "1.0.0",
            "id": "test-workload",
            "selection_policy": {
                "repeats_per_case": 2,
                "per_run_timeout_seconds": 5,
                "per_run_rss_cap_mb": 100,
            },
            "data_graph": {
                "path": str(self.data_graph),
                "sha256": self.data_sha,
            },
            "queries": {
                "Q_test": {
                    "path": str(self.query_file),
                    "sha256": self.query_sha,
                    "category": "test",
                    "vertices": 2,
                    "edges": 1,
                }
            },
            "streams": {
                "S_test": {
                    "path": str(self.stream_file),
                    "sha256": self.stream_sha,
                    "prefix_length": 1,
                    "stream_type": "insertion",
                }
            },
            "cases": [
                {"case_id": "c_test_01", "query_id": "Q_test", "stream_id": "S_test"}
            ],
            "pilot_cases": ["c_test_01"],
        }
        self.cfg_file.write_text(json.dumps(self.cfg, indent=2))

        self.summary_csv = self.base / "summary.csv"
        self.logs_dir = self.base / "logs"

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_timeout_preservation_in_csv(self, mock_exec, mock_prov):
        mock_exec.return_value = runner.SubprocessResult(
            returncode=124,
            stdout="",
            stderr="process timed out",
            duration_sec=5.01,
            rss_kb=1024,
            timed_out=True,
            oom_killed=False,
            error_message="External process timeout reached (5s)",
        )

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            summary_csv_path=self.summary_csv,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)  # Had error / timeout
        self.assertTrue(self.summary_csv.exists())
        self.assertEqual(len(rows), 2)  # 2 repeats
        for r in rows:
            self.assertEqual(r["status"], "TIMEOUT")
            self.assertEqual(r["exit_code"], 124)
            self.assertIn("timeout reached", r["error_message"])

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_oom_rss_cap_preservation_in_csv(self, mock_exec, mock_prov):
        mock_exec.return_value = runner.SubprocessResult(
            returncode=0,
            stdout="",
            stderr="Maximum resident set size (kbytes): 200000\n",
            duration_sec=1.5,
            rss_kb=200000,
            timed_out=False,
            oom_killed=True,
            error_message="Process exceeded RSS cap: 200000 KB > 102400 KB",
        )

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            summary_csv_path=self.summary_csv,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["status"], "OOM")
            self.assertIn("exceeded RSS cap", r["error_message"])

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_count_mismatch_across_repeats_flags_error(self, mock_exec, mock_prov):
        # Rep 1 returns Result count: 10, Rep 2 returns Result count: 20
        stdout_rep1 = TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 10\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n")
        stdout_rep2 = TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 20\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n")

        mock_exec.side_effect = [
            runner.SubprocessResult(
                returncode=0,
                stdout=stdout_rep1,
                stderr="",
                duration_sec=0.1,
                rss_kb=5000,
            ),
            runner.SubprocessResult(
                returncode=0,
                stdout=stdout_rep2,
                stderr="",
                duration_sec=0.1,
                rss_kb=5000,
            ),
        ]

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            summary_csv_path=self.summary_csv,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)  # Mismatch across repeats flagged
        self.assertEqual(rows[0]["result_count"], 10)
        self.assertEqual(rows[1]["result_count"], 20)

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_incomplete_stream_updates_num_mismatch(self, mock_exec, mock_prov):
        # Workload prefix length is 1, but RapidFlow processed 0 or mismatched updates
        mismatched_stdout = TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Updates num: 100\n", "Updates num: 0\n").replace("Edge process count: 100\n", "Edge process count: 1\n")
        mock_exec.return_value = runner.SubprocessResult(
            returncode=0,
            stdout=mismatched_stdout,
            stderr="",
            duration_sec=0.1,
            rss_kb=5000,
        )

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            summary_csv_path=self.summary_csv,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["status"], "INCOMPLETE_STREAM")
            self.assertIn("Update count mismatch", r["error_message"])

    @patch("scripts.rf03_netflow_runner.get_available_memory_bytes", return_value=1 * 1024 * 1024 * 1024)
    def test_low_system_memory_fails_closed(self, mock_mem):
        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            summary_csv_path=self.summary_csv,
            logs_base_dir=self.logs_dir,
            skip_memory_check=False,
            skip_git_check=True,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(len(rows), 0)


    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_interrupted_run_checkpoint_durability(self, mock_exec, mock_prov):
        """Verify incremental checkpointing persists rows and permits recovery on interrupted runs."""
        mock_exec.side_effect = [
            runner.SubprocessResult(
                returncode=0,
                stdout=TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 5\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n"),
                stderr="",
                duration_sec=0.1,
                rss_kb=5000,
            ),
            KeyboardInterrupt("Simulated Ctrl+C interruption on attempt 2"),
        ]

        with self.assertRaises(KeyboardInterrupt):
            runner.run_benchmark(
                config_path=self.cfg_file,
                workspace_root=self.base,
                summary_csv_path=self.summary_csv,
                logs_base_dir=self.logs_dir,
                skip_memory_check=True,
                skip_git_check=True,
            )

        # 1. Ensure aggregate summary_csv contains completed attempt 1
        self.assertTrue(self.summary_csv.exists())
        with open(self.summary_csv, newline="", encoding="utf-8") as f:
            saved_rows = list(csv.DictReader(f))
        self.assertEqual(len(saved_rows), 1)
        self.assertEqual(saved_rows[0]["status"], "COMPLETED")
        self.assertEqual(saved_rows[0]["result_count"], "5")

        # 2. Ensure run-local checkpoint_runs.csv was created and contains attempt 1
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(run_dirs), 1)
        checkpoint_file = run_dirs[0] / "checkpoint_runs.csv"
        self.assertTrue(checkpoint_file.exists())
        with open(checkpoint_file, newline="", encoding="utf-8") as f:
            cp_rows = list(csv.DictReader(f))
        self.assertEqual(len(cp_rows), 1)
        self.assertEqual(cp_rows[0]["result_count"], "5")

        # 3. Test log-based recovery function
        recovered = runner.recover_summary_from_logs(run_dirs[0])
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["result_count"], 5)

    def test_recover_summary_from_empty_or_missing_dir(self):
        empty_dir = self.base / "nonexistent_dir"
        recovered = runner.recover_summary_from_logs(empty_dir)
        self.assertEqual(recovered, [])


class TestRF03BenchmarkReportConsistency(unittest.TestCase):
    """Verifies that docs/rf03_benchmark_report.md table strictly matches canonical results/summary/rf03_baseline_runs.csv."""

    def test_markdown_table_matches_canonical_csv(self):
        import re
        import statistics
        from collections import defaultdict

        repo_root = pathlib.Path(__file__).resolve().parent.parent
        csv_path = repo_root / "results" / "summary" / "rf03_baseline_runs.csv"
        report_path = repo_root / "docs" / "rf03_benchmark_report.md"

        self.assertTrue(csv_path.exists(), f"Missing canonical CSV at {csv_path}")
        self.assertTrue(report_path.exists(), f"Missing report at {report_path}")

        with open(csv_path, newline="", encoding="utf-8") as f:
            csv_rows = list(csv.DictReader(f))

        self.assertEqual(len(csv_rows), 30, "Canonical CSV must have exactly 30 runs")
        cases = defaultdict(list)
        for r in csv_rows:
            cases[r["case_id"]].append(r)

        report_text = report_path.read_text(encoding="utf-8")
        row_pattern = re.compile(
            r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*\*\*?([0-9,]+)\*\*?\s*\|\s*([\d.]+)\s*\[([\d.]+)[–-]([\d.]+)\]\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
            re.MULTILINE,
        )
        matches = row_pattern.findall(report_text)
        self.assertEqual(len(matches), 10, f"Expected 10 table rows in report, found {len(matches)}")

        for m in matches:
            cid = m[0]
            self.assertIn(cid, cases, f"Case {cid} not found in CSV")
            crows = cases[cid]
            self.assertEqual(len(crows), 3, f"Expected 3 repeats for case {cid}")

            expected_count = int(m[5].replace(",", ""))
            csv_counts = [int(r["result_count"]) for r in crows]
            self.assertEqual(csv_counts, [expected_count, expected_count, expected_count])

            reported_wall_median = float(m[6])
            reported_wall_min = float(m[7])
            reported_wall_max = float(m[8])
            csv_wall = [float(r["wall_time_sec"]) for r in crows]
            self.assertAlmostEqual(reported_wall_median, statistics.median(csv_wall), places=2)
            self.assertAlmostEqual(reported_wall_min, min(csv_wall), places=2)
            self.assertAlmostEqual(reported_wall_max, max(csv_wall), places=2)

            reported_rss = float(m[9])
            csv_rss = [float(r["rss_kb"]) / 1024.0 for r in crows]
            self.assertAlmostEqual(reported_rss, statistics.median(csv_rss), delta=0.5)

            reported_prep = float(m[10])
            csv_prep = [float(r["preprocessing_time_sec"]) for r in crows]
            self.assertAlmostEqual(reported_prep, statistics.median(csv_prep), places=2)


if __name__ == "__main__":
    unittest.main()
