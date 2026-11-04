#!/usr/bin/env python3
"""
tests/test_rf03_runner.py — Unit and Negative Adversarial Tests for RF-03 Runner.

Tests all failure modes, parse validation, provenance verification, resource capping,
timeout handling, multi-repeat consistency, and log preservation using isolated mocks
without running heavy NetFlow computations.
"""

from __future__ import annotations

import csv
import hashlib
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
        # Check that divergence is persisted in CSV rows and case verdict!
        self.assertEqual(rows[0]["status"], "COUNT_MISMATCH_ACROSS_REPEATS")
        self.assertEqual(rows[1]["status"], "COUNT_MISMATCH_ACROSS_REPEATS")
        self.assertIn("COUNT_MISMATCH_ACROSS_REPEATS", rows[0]["error_message"])
        self.assertIn("COUNT_MISMATCH_ACROSS_REPEATS", rows[1]["error_message"])

        # Also inspect on-disk summary CSV
        with open(self.summary_csv, newline="", encoding="utf-8") as f:
            disk_rows = list(csv.DictReader(f))
        self.assertEqual(disk_rows[0]["status"], "COUNT_MISMATCH_ACROSS_REPEATS")
        self.assertEqual(disk_rows[1]["status"], "COUNT_MISMATCH_ACROSS_REPEATS")

        # Also inspect case_verdict.json
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(run_dirs), 1)
        verdict_file = run_dirs[0] / "c_test_01_verdict.json"
        self.assertTrue(verdict_file.exists())
        verdict_data = json.loads(verdict_file.read_text(encoding="utf-8"))
        self.assertEqual(verdict_data["verdict"], "FAIL_COUNT_MISMATCH")
        self.assertTrue(verdict_data["is_divergent"])

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
        recovered, warnings = runner.recover_summary_from_logs(run_dirs[0])
        self.assertEqual(warnings, [])
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["result_count"], 5)

    def test_recover_summary_from_empty_or_missing_dir(self):
        empty_dir = self.base / "nonexistent_dir"
        recovered, warnings = runner.recover_summary_from_logs(empty_dir)
        self.assertEqual(recovered, [])
        self.assertTrue(any("Directory not found" in w for w in warnings))

    def test_recover_summary_reports_corrupt_files(self):
        """Verify recover_summary_from_logs detects corrupt and duplicate files."""
        run_dir = self.base / "corrupt_run_dir"
        run_dir.mkdir(parents=True, exist_ok=True)

        rep1_dir = run_dir / "c1_rep1"
        rep1_dir.mkdir()
        (rep1_dir / "metadata.json").write_text("NOT_VALID_JSON{")

        rep2_dir = run_dir / "c1_rep2"
        rep2_dir.mkdir()
        rep2_meta = {"summary_row": {"case_id": "c1", "repeat_index": 2, "result_count": 42}}
        (rep2_dir / "metadata.json").write_text(json.dumps(rep2_meta))

        recovered, warnings = runner.recover_summary_from_logs(run_dir)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["result_count"], 42)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Corrupt or unreadable metadata", warnings[0])

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_canonical_csv_protected_from_pilot_and_case_overwrites(self, mock_exec, mock_prov):
        """Verify that running --pilot or partial runs does NOT overwrite canonical baseline CSV."""
        mock_exec.return_value = runner.SubprocessResult(
            returncode=0,
            stdout=TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 0\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n"),
            stderr="",
            duration_sec=0.1,
            rss_kb=5000,
        )

        # Create mock canonical CSV with sentinel rows in workspace results/summary/
        canonical_file = self.base / "results" / "summary" / "rf03_baseline_runs.csv"
        canonical_file.parent.mkdir(parents=True, exist_ok=True)
        sentinel_rows = [{"run_id": "sentinel_canonical_run", "case_id": f"c_{i}", "status": "COMPLETED"} for i in range(30)]
        runner.write_summary_csv(canonical_file, sentinel_rows)
        sentinel_sha = runner.file_sha256(canonical_file)

        # Run benchmark with default summary_csv_path (None) and pilot_only=True
        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            pilot_only=True,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 0)
        # Check that canonical CSV was NOT touched
        self.assertEqual(runner.file_sha256(canonical_file), sentinel_sha)

        # Check that run-scoped summary was written inside run directory
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(run_dirs), 1)
        run_summary = run_dirs[0] / "summary_runs.csv"
        self.assertTrue(run_summary.exists())
        with open(run_summary, newline="", encoding="utf-8") as f:
            run_rows = list(csv.DictReader(f))
        self.assertEqual(len(run_rows), 1)

    def _create_synthetic_10case_workload(self) -> Tuple[pathlib.Path, Dict[str, Any]]:
        """Sets up a complete 10-case synthetic workload matching schema."""
        cfg_dir = self.base / "configs"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / "rf03_workload.json"

        dg_file = self.base / "data" / "data.graph"
        dg_file.parent.mkdir(parents=True, exist_ok=True)
        dg_file.write_text("v 0 0\n")
        dg_sha = runner.file_sha256(dg_file)

        q_file = self.base / "data" / "query.graph"
        q_file.parent.mkdir(parents=True, exist_ok=True)
        q_file.write_text("v 0 0\nv 1 0\ne 0 1 0\n")
        q_sha = runner.file_sha256(q_file)

        s_file = self.base / "data" / "stream.graph"
        s_file.parent.mkdir(parents=True, exist_ok=True)
        s_file.write_text("e 0 1 0\n")
        s_sha = runner.file_sha256(s_file)

        cases = []
        for i in range(1, 11):
            cases.append({
                "case_id": f"c{i:02d}_test",
                "query_id": "Q_test",
                "stream_id": "S_test",
            })

        cfg = {
            "schema_version": "1.0.0",
            "id": "synthetic-10case-workload",
            "selection_policy": {
                "repeats_per_case": 3,
                "per_run_timeout_seconds": 5,
                "per_run_rss_cap_mb": 100,
            },
            "source_git_commit": runner.EXPECTED_BASELINE_COMMIT,
            "binary_sha256": self.bin_sha,
            "data_graph": {
                "path": str(dg_file),
                "sha256": dg_sha,
            },
            "queries": {
                "Q_test": {
                    "path": str(q_file),
                    "sha256": q_sha,
                    "category": "test",
                    "vertices": 2,
                    "edges": 1,
                }
            },
            "streams": {
                "S_test": {
                    "path": str(s_file),
                    "sha256": s_sha,
                    "prefix_length": 100,
                    "stream_type": "insertion",
                }
            },
            "cases": cases,
            "pilot_cases": ["c01_test", "c02_test", "c03_test"],
        }
        cfg_file.write_text(json.dumps(cfg, indent=2))
        return cfg_file, cfg

    def _create_synthetic_run_suite(
        self,
        cfg_path: pathlib.Path,
        cfg: Dict[str, Any],
        run_id: str = "run_synth_valid",
    ) -> pathlib.Path:
        """Constructs a fully valid, auditable 30-run suite on disk."""
        run_dir = self.logs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "run_id": run_id,
            "source_git_commit": runner.EXPECTED_BASELINE_COMMIT,
            "binary_sha256": self.bin_sha,
            "data_graph_sha256": cfg["data_graph"]["sha256"],
            "config_sha256": runner.file_sha256(cfg_path),
            "queries": {
                qid: {"sha256": qinfo["sha256"]} for qid, qinfo in cfg["queries"].items()
            },
            "streams": {
                sid: {"sha256": sinfo["sha256"]} for sid, sinfo in cfg["streams"].items()
            },
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        summary_rows = []
        for c in cfg["cases"]:
            cid = c["case_id"]
            qid = c["query_id"]
            sid = c["stream_id"]
            prefix_len = cfg["streams"][sid]["prefix_length"]

            for rep in range(1, 4):
                rep_dir = run_dir / f"{cid}_rep{rep}"
                rep_dir.mkdir(parents=True, exist_ok=True)

                stdout_text = f"Result count: 100\nUpdates num: {prefix_len}\nEdge process count: {prefix_len}\n"
                stderr_text = "Elapsed (wall clock) time: 0:00.10\nMaximum resident set size (kbytes): 5000\n"
                (rep_dir / "stdout.log").write_text(stdout_text)
                (rep_dir / "stderr.log").write_text(stderr_text)

                stdout_sha = hashlib.sha256(stdout_text.encode("utf-8")).hexdigest()
                stderr_sha = hashlib.sha256(stderr_text.encode("utf-8")).hexdigest()

                row = {
                    "run_id": run_id,
                    "case_id": cid,
                    "repeat_index": str(rep),
                    "query_id": qid,
                    "stream_id": sid,
                    "prefix_length": str(prefix_len),
                    "status": "COMPLETED",
                    "exit_code": "0",
                    "wall_time_sec": "0.100000",
                    "rss_kb": "5000",
                    "preprocessing_time_sec": "0.010000",
                    "query_time_sec": "0.001000",
                    "gv_update_time_sec": "0.000000",
                    "lv_update_time_sec": "0.000000",
                    "search_time_sec": "0.001000",
                    "updates_num": str(prefix_len),
                    "edge_process_count": str(prefix_len),
                    "result_count": "100",
                    "stdout_sha256": stdout_sha,
                    "stderr_sha256": stderr_sha,
                    "error_message": "",
                }
                summary_rows.append(row)

                rep_meta = {
                    "run_id": run_id,
                    "case_id": cid,
                    "repeat_index": rep,
                    "status": "COMPLETED",
                    "summary_row": row,
                }
                (rep_dir / "metadata.json").write_text(json.dumps(rep_meta, indent=2) + "\n")

            verdict_data = {
                "case_id": cid,
                "query_id": qid,
                "stream_id": sid,
                "verdict": "PASS_CONSISTENT",
                "is_pass": True,
                "failure_reasons": [],
                "expected_repeats": 3,
                "attempted_repeats": 3,
                "completed_repeats": 3,
                "repeat_statuses": ["COMPLETED", "COMPLETED", "COMPLETED"],
                "result_counts": ["100", "100", "100"],
                "is_divergent": False,
            }
            (run_dir / f"{cid}_verdict.json").write_text(json.dumps(verdict_data, indent=2) + "\n")

        runner.write_summary_csv(run_dir / "summary_runs.csv", summary_rows)
        return run_dir

    # ---------------------------------------------------------
    # Task E Regression Tests (15 Comprehensive Verification Cases)
    # ---------------------------------------------------------

    def test_01_promotion_rejects_nonexistent_case_id(self):
        """1. Promotion with non-existent case ID."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e01")

        # Tamper summary CSV with unknown case ID
        with open(run_dir / "summary_runs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        rows[0]["case_id"] = "c99_unknown"
        runner.write_summary_csv(run_dir / "summary_runs.csv", rows)

        ok, msg = runner.promote_run_to_canonical("run_e01", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("unexpected case_id", msg)

    def test_02_promotion_rejects_duplicate_repeat_index(self):
        """2. Promotion with duplicate repeat index."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e02")

        with open(run_dir / "summary_runs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        # Case c01 has repeats 1, 2, 3 -> set repeat 2 to 1 (duplicate)
        rows[1]["repeat_index"] = "1"
        runner.write_summary_csv(run_dir / "summary_runs.csv", rows)

        ok, msg = runner.promote_run_to_canonical("run_e02", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("duplicate case attempt", msg)

    def test_03_promotion_rejects_missing_repeat(self):
        """3. Promotion missing repeat (only 29 rows instead of 30)."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e03")

        with open(run_dir / "summary_runs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        rows.pop()  # Drop one row
        runner.write_summary_csv(run_dir / "summary_runs.csv", rows)

        ok, msg = runner.promote_run_to_canonical("run_e03", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("expected exactly 30 rows", msg)

    def test_04_promotion_rejects_incomplete_updates_count(self):
        """4. Promotion with incomplete update count."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e04")

        with open(run_dir / "summary_runs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        rows[0]["updates_num"] = "99"  # Expected 100
        runner.write_summary_csv(run_dir / "summary_runs.csv", rows)

        ok, msg = runner.promote_run_to_canonical("run_e04", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("updates_num", msg)

    def test_05_promotion_rejects_missing_raw_logs(self):
        """5. Promotion missing raw logs."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e05")

        # Delete stdout.log for c01_test rep 1
        (run_dir / "c01_test_rep1" / "stdout.log").unlink()

        ok, msg = runner.promote_run_to_canonical("run_e05", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("stdout.log missing", msg)

    def test_06_promotion_rejects_raw_log_sha_mismatch(self):
        """6. Promotion with SHA mismatch between raw log and recorded hash."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e06")

        # Tamper stdout.log content
        (run_dir / "c01_test_rep1" / "stdout.log").write_text("TAMPERED DATA\n")

        ok, msg = runner.promote_run_to_canonical("run_e06", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("stdout SHA mismatch", msg)

    def test_07_promotion_rejects_missing_manifest(self):
        """7. Promotion without run_manifest.json."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e07")

        (run_dir / "run_manifest.json").unlink()

        ok, msg = runner.promote_run_to_canonical("run_e07", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("run_manifest.json missing", msg)

    def test_08_promotion_rejects_incorrect_binary_sha(self):
        """8. Promotion with incorrect binary SHA."""
        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e08")

        mfile = run_dir / "run_manifest.json"
        mdata = json.loads(mfile.read_text())
        mdata["binary_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
        mfile.write_text(json.dumps(mdata))

        ok, msg = runner.promote_run_to_canonical("run_e08", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertIn("manifest binary SHA", msg)

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    def test_09_canonical_path_guard_unconditional_nonexistent(self, mock_prov):
        """9. Canonical path guard blocks direct targeting even when canonical DOES NOT exist."""
        canonical_file = self.base / "results" / "summary" / "rf03_baseline_runs.csv"
        if canonical_file.exists():
            canonical_file.unlink()

        with self.assertRaises(RuntimeError) as ctx:
            runner.run_benchmark(
                config_path=self.cfg_file,
                workspace_root=self.base,
                summary_csv_path=canonical_file,
                logs_base_dir=self.logs_dir,
                skip_memory_check=True,
                skip_git_check=True,
            )
        self.assertIn("Directly targeting the canonical baseline CSV", str(ctx.exception))

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    def test_10_canonical_path_guard_unconditional_existent(self, mock_prov):
        """10. Canonical path guard blocks direct targeting when canonical DOES exist."""
        canonical_file = self.base / "results" / "summary" / "rf03_baseline_runs.csv"
        canonical_file.parent.mkdir(parents=True, exist_ok=True)
        canonical_file.write_text("existing canonical\n")

        with self.assertRaises(RuntimeError) as ctx:
            runner.run_benchmark(
                config_path=self.cfg_file,
                workspace_root=self.base,
                summary_csv_path=canonical_file,
                logs_base_dir=self.logs_dir,
                skip_memory_check=True,
                skip_git_check=True,
            )
        self.assertIn("Directly targeting the canonical baseline CSV", str(ctx.exception))

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_11_case_verdict_one_completed_one_timeout(self, mock_exec, mock_prov):
        """11. Case verdict: One repeat COMPLETED, one repeat TIMEOUT -> INCONCLUSIVE_INCOMPLETE_REPEATS."""
        mock_exec.side_effect = [
            runner.SubprocessResult(
                returncode=0,
                stdout=TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 10\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n"),
                stderr="",
                duration_sec=0.1,
                rss_kb=5000,
            ),
            runner.SubprocessResult(
                returncode=124,
                stdout="",
                stderr="",
                duration_sec=5.0,
                rss_kb=1000,
                timed_out=True,
            ),
        ]

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        vfile = run_dirs[0] / "c_test_01_verdict.json"
        self.assertTrue(vfile.exists())
        vdata = json.loads(vfile.read_text())
        self.assertEqual(vdata["verdict"], "INCONCLUSIVE_INCOMPLETE_REPEATS")
        self.assertFalse(vdata["is_pass"])
        self.assertIn("INCOMPLETE_REPEATS", vdata["failure_reasons"])
        self.assertIn("TIMEOUT", vdata["failure_reasons"])
        self.assertNotEqual(vdata["verdict"], "PASS_CONSISTENT")

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_12_case_verdict_count_mismatch_and_timeout_multiple_failures(self, mock_exec, mock_prov):
        """12. Case verdict: Two repeats count different and one repeat TIMEOUT -> records multiple failure reasons."""
        mock_exec.side_effect = [
            runner.SubprocessResult(
                returncode=0,
                stdout=TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 10\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n"),
                stderr="",
                duration_sec=0.1,
                rss_kb=5000,
            ),
            runner.SubprocessResult(
                returncode=0,
                stdout=TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Result count: 120\n", "Result count: 20\n").replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n"),
                stderr="",
                duration_sec=0.1,
                rss_kb=5000,
            ),
            runner.SubprocessResult(
                returncode=124,
                stdout="",
                stderr="",
                duration_sec=5.0,
                rss_kb=1000,
                timed_out=True,
            ),
        ]

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            repeats_override=3,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        vfile = run_dirs[0] / "c_test_01_verdict.json"
        vdata = json.loads(vfile.read_text())
        self.assertEqual(vdata["verdict"], "FAIL_COUNT_MISMATCH")
        self.assertFalse(vdata["is_pass"])
        self.assertIn("COUNT_MISMATCH", vdata["failure_reasons"])
        self.assertIn("INCOMPLETE_REPEATS", vdata["failure_reasons"])
        self.assertIn("TIMEOUT", vdata["failure_reasons"])

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_13_case_verdict_all_repeats_timeout(self, mock_exec, mock_prov):
        """13. Case verdict: All repeats TIMEOUT -> FAIL_ALL_REPEATS_FAILED."""
        mock_exec.side_effect = [
            runner.SubprocessResult(returncode=124, stdout="", stderr="", duration_sec=5.0, rss_kb=1000, timed_out=True),
            runner.SubprocessResult(returncode=124, stdout="", stderr="", duration_sec=5.0, rss_kb=1000, timed_out=True),
        ]

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 1)
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        vfile = run_dirs[0] / "c_test_01_verdict.json"
        vdata = json.loads(vfile.read_text())
        self.assertEqual(vdata["verdict"], "FAIL_ALL_REPEATS_FAILED")
        self.assertFalse(vdata["is_pass"])
        self.assertIn("ALL_REPEATS_FAILED", vdata["failure_reasons"])
        self.assertIn("TIMEOUT", vdata["failure_reasons"])

    def test_14_promotion_creates_unique_backups_without_overwriting(self):
        """14. Backup not overwritten when promoted twice."""
        canonical_file = self.base / "results" / "summary" / "rf03_baseline_runs.csv"
        canonical_file.parent.mkdir(parents=True, exist_ok=True)
        canonical_file.write_text("original canonical\n")

        cfg_path, cfg = self._create_synthetic_10case_workload()
        run1 = self._create_synthetic_run_suite(cfg_path, cfg, "run_e14_1")
        run2 = self._create_synthetic_run_suite(cfg_path, cfg, "run_e14_2")

        ok1, msg1 = runner.promote_run_to_canonical("run_e14_1", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertTrue(ok1)

        ok2, msg2 = runner.promote_run_to_canonical("run_e14_2", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertTrue(ok2)

        backups = list(canonical_file.parent.glob("rf03_baseline_runs_*.csv.bak"))
        self.assertEqual(len(backups), 2, "Expected 2 distinct timestamp+UUID backup files")
        # Ensure backup names are unique
        self.assertEqual(len({b.name for b in backups}), 2)

    def test_15_promotion_failure_leaves_canonical_untouched(self):
        """15. Promotion failure leaves canonical completely unchanged."""
        canonical_file = self.base / "results" / "summary" / "rf03_baseline_runs.csv"
        canonical_file.parent.mkdir(parents=True, exist_ok=True)
        canonical_file.write_text("IMMUTABLE_SENTINEL_DATA\n")

        cfg_path, cfg = self._create_synthetic_10case_workload()
        run_dir = self._create_synthetic_run_suite(cfg_path, cfg, "run_e15")
        (run_dir / "run_manifest.json").unlink()  # Corrupt the run

        ok, msg = runner.promote_run_to_canonical("run_e15", workspace_root=self.base, logs_base_dir=self.logs_dir, workload_config_rel=cfg_path)
        self.assertFalse(ok)
        self.assertEqual(canonical_file.read_text(), "IMMUTABLE_SENTINEL_DATA\n")

    @patch("scripts.rf03_netflow_runner.verify_provenance", return_value=(True, "OK"))
    @patch("scripts.rf03_netflow_runner.execute_case_run")
    def test_run_manifest_created(self, mock_exec, mock_prov):
        """Verify immutable run_manifest.json created with full provenance."""
        mock_exec.return_value = runner.SubprocessResult(
            returncode=0,
            stdout=TestRF03OutputParsing.SAMPLE_VALID_STDOUT.replace("Updates num: 100\n", "Updates num: 1\n").replace("Edge process count: 100\n", "Edge process count: 1\n"),
            stderr="",
            duration_sec=0.1,
            rss_kb=5000,
        )

        exit_code, rows = runner.run_benchmark(
            config_path=self.cfg_file,
            workspace_root=self.base,
            logs_base_dir=self.logs_dir,
            skip_memory_check=True,
            skip_git_check=True,
        )

        self.assertEqual(exit_code, 0)
        run_dirs = [d for d in self.logs_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(run_dirs), 1)
        manifest_file = run_dirs[0] / "run_manifest.json"
        self.assertTrue(manifest_file.exists())
        mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertIn("command_argv", mdata)
        self.assertIn("resource_policy", mdata)
        self.assertEqual(mdata["resource_policy"]["runtime_rss_enforcement"], False)


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
