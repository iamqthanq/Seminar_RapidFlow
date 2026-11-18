#!/usr/bin/env python3
"""
tests/test_dstar03_summary.py — Unit Tests for DSTAR-03 Summary and Reconciliation (v2).

Covers:
1. Exact median and range calculation against hand-crafted fixtures.
2. Missing attempt detection and execution completeness enforcement.
3. Paired ratio calculation and strict one-sided timeout rejection (no threshold substitution).
4. F01 regression: all-timeout ledger rejects PILOT_COMPLETED.
5. F01 regression: zero-workload manifest rejected with error.
6. F01 regression: duplicate completion record in ledger rejected with error.
7. F01 regression: corrupt JSONL line in ledger rejected with error.
8. F01 regression: unknown attempt ID in ledger rejected with error.
9. F01 regression: completion record missing matching start record rejected with error.
10. F01 regression: supervisor filter matching 0 workloads returns ERROR.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_dstar03_pilot import PilotSupervisor
from summarize_dstar03_pilot import compute_median_and_range, summarize_pilot


class TestDStar03Summary(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
        with cls.manifest_path.open("r", encoding="utf-8") as f:
            cls.manifest = json.load(f)

    def test_01_median_and_range_calculation(self) -> None:
        """Verifies median and range against hand-calculated values."""
        med, mn, mx = compute_median_and_range([1.0, 2.0, 5.0])
        self.assertEqual(med, 2.0)
        self.assertEqual(mn, 1.0)
        self.assertEqual(mx, 5.0)

        med, mn, mx = compute_median_and_range([10.0, 20.0, 30.0, 40.0])
        self.assertEqual(med, 25.0)
        self.assertEqual(mn, 10.0)
        self.assertEqual(mx, 40.0)

        med, mn, mx = compute_median_and_range([])
        self.assertEqual(med, 0.0)
        self.assertEqual(mn, 0.0)
        self.assertEqual(mx, 0.0)

    def test_02_summary_reconciliation_missing_slots(self) -> None:
        """Verifies that an empty ledger results in INCOMPLETE status and tracks 390 missing slots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            empty_ledger = tmp_path / "empty_attempts.jsonl"
            empty_ledger.touch()
            out_dir = tmp_path / "out"

            summary = summarize_pilot(
                manifest_path=self.manifest_path,
                ledger_path=empty_ledger,
                output_dir=out_dir,
            )

            self.assertEqual(summary["execution_completeness"], "INCOMPLETE")
            self.assertEqual(summary["attempts_recorded"], 0)
            self.assertEqual(summary["successful_measured_slots"], 0)
            self.assertEqual(summary["missing_slots_count"], 65 * 6)
            self.assertTrue((out_dir / "correctness_summary.json").exists())
            self.assertTrue((out_dir / "pilot_summary.json").exists())
            self.assertTrue((out_dir / "pilot_results.csv").exists())

    def test_03_one_sided_timeout_rejection(self) -> None:
        """Verifies that if one side of a pair timed out, no speedup ratio is manufactured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "attempts.jsonl"
            out_dir = tmp_path / "out"

            w_id = "witness_01_diamond_k1"
            m1_id = f"{w_id}_m1_pair1_rep0"
            m2_id = f"{w_id}_m2_pair1_rep0"

            records = [
                {"record_type": "ATTEMPT_STARTED", "attempt_id": m1_id},
                {
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": m1_id,
                    "workload_id": w_id,
                    "engine": "m1",
                    "pair_index": 1,
                    "rep_index": 0,
                    "status": "SUCCESS_NONEMPTY",
                    "stream_engine_ms": 2.0,
                    "total_budget_units": 10,
                },
                {"record_type": "ATTEMPT_STARTED", "attempt_id": m2_id},
                {
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": m2_id,
                    "workload_id": w_id,
                    "engine": "m2",
                    "pair_index": 1,
                    "rep_index": 0,
                    "status": "TIMEOUT",
                    "error": "Timeout expired",
                },
            ]
            with ledger.open("w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")

            summarize_pilot(
                manifest_path=self.manifest_path,
                ledger_path=ledger,
                output_dir=out_dir,
            )

            csv_path = out_dir / "pilot_results.csv"
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [r for r in reader if r["workload_id"] == w_id]

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["speedup_ratio_m1_over_m2"], "N/A")

    def test_04_all_timeout_ledger_not_pilot_completed(self) -> None:
        """F01: Verifies that a ledger with 390 TIMEOUT completions does NOT yield PILOT_COMPLETED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "all_timeout.jsonl"
            out_dir = tmp_path / "out"

            with ledger.open("w", encoding="utf-8") as f:
                for w in self.manifest["workloads"]:
                    w_id = w["workload_id"]
                    for p in (1, 2, 3):
                        rep = p - 1
                        for eng in ("m1", "m2"):
                            att_id = f"{w_id}_{eng}_pair{p}_rep{rep}"
                            f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                            f.write(json.dumps({
                                "record_type": "ATTEMPT_COMPLETED",
                                "attempt_id": att_id,
                                "workload_id": w_id,
                                "engine": eng,
                                "pair_index": p,
                                "rep_index": rep,
                                "status": "TIMEOUT",
                                "error": "Timed out",
                            }) + "\n")

            summary = summarize_pilot(
                manifest_path=self.manifest_path,
                ledger_path=ledger,
                output_dir=out_dir,
            )

            # Must NOT be PILOT_COMPLETED
            self.assertEqual(summary["execution_completeness"], "INCOMPLETE")
            self.assertEqual(summary["successful_measured_slots"], 0)
            self.assertEqual(summary["failed_slots_count"], 390)

    def test_05_zero_workload_manifest_rejected(self) -> None:
        """F01: Verifies that a manifest with 0 workloads raises ValueError during summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            empty_m = tmp_path / "empty_manifest.json"
            ledger = tmp_path / "ledger.jsonl"
            ledger.touch()

            with empty_m.open("w", encoding="utf-8") as f:
                json.dump({"manifest_id": "empty", "workloads": []}, f)

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=empty_m, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("65 required", str(ctx.exception))

    def test_06_duplicate_completion_rejected(self) -> None:
        """F01: Verifies that duplicate completion records for the same slot raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "dup_ledger.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps({
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": att_id,
                    "status": "SUCCESS_NONEMPTY",
                    "stream_engine_ms": 1.0,
                }) + "\n")
                f.write(json.dumps({
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": att_id,
                    "status": "SUCCESS_NONEMPTY",
                    "stream_engine_ms": 1.2,
                }) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("duplicate completion", str(ctx.exception).lower())

    def test_07_corrupt_jsonl_rejected(self) -> None:
        """F01: Verifies that corrupt/malformed JSON in ledger raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "corrupt_ledger.jsonl"

            with ledger.open("w", encoding="utf-8") as f:
                f.write('{"record_type": "ATTEMPT_STARTED", "attempt_id": "witness_01_diamond_k1_m1_pair1_rep0"}\n')
                f.write('{"corrupted_json: incomplete\n')

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("Corrupt JSON", str(ctx.exception))

    def test_08_unknown_attempt_id_rejected(self) -> None:
        """F01: Verifies that unknown attempt IDs not belonging to manifest raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "unknown_ledger.jsonl"
            fake_id = "fake_workload_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": fake_id}) + "\n")
                f.write(json.dumps({
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": fake_id,
                    "status": "SUCCESS_NONEMPTY",
                }) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("unknown attempt", str(ctx.exception).lower())

    def test_09_completion_missing_start_rejected(self) -> None:
        """F01: Verifies that completion records lacking an earlier ATTEMPT_STARTED raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "missing_start.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": att_id,
                    "status": "SUCCESS_NONEMPTY",
                    "stream_engine_ms": 1.0,
                }) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("no matching attempt_started", str(ctx.exception).lower())

    def test_10_filter_zero_workloads_supervisor(self) -> None:
        """F01: Verifies supervisor returns ERROR when filter matches 0 workloads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_ledger = Path(tmpdir) / "test_ledger.jsonl"
            supervisor = PilotSupervisor(
                manifest_path=self.manifest_path,
                ledger_path=test_ledger,
            )
            res = supervisor.run(workload_filter="non_existent_workload_string_12345")
            self.assertEqual(res["status"], "ERROR")
            self.assertEqual(res["total_slots"], 0)


if __name__ == "__main__":
    unittest.main()
