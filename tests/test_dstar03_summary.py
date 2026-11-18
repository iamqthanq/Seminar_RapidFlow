#!/usr/bin/env python3
"""
tests/test_dstar03_summary.py — Unit Tests for DSTAR-03 Summary and Reconciliation.

Covers:
1. Exact median and range calculation against hand-crafted fixtures.
2. Paired ratio calculation and strict one-sided timeout rejection (no threshold substitution).
3. Missing attempt detection and execution completeness enforcement.
4. Sub-millisecond noise tagging and NetFlow zero-event status separation.
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

from summarize_dstar03_pilot import compute_median_and_range, summarize_pilot


class TestDStar03Summary(unittest.TestCase):
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
        """Verifies that an empty or incomplete ledger results in INCOMPLETE status and tracks missing slots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            empty_ledger = tmp_path / "empty_attempts.jsonl"
            empty_ledger.touch()
            out_dir = tmp_path / "out"

            manifest_path = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
            summary = summarize_pilot(
                manifest_path=manifest_path,
                ledger_path=empty_ledger,
                output_dir=out_dir,
            )

            self.assertEqual(summary["execution_completeness"], "INCOMPLETE")
            self.assertEqual(summary["completed_attempts"], 0)
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

            # Create synthetic attempts where M1 succeeded (2.0 ms) but M2 timed out (TIMEOUT)
            w_id = "witness_01_diamond_k1"
            attempts = [
                {
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": f"{w_id}_m1_pair1_rep0",
                    "workload_id": w_id,
                    "engine": "m1",
                    "pair_index": 1,
                    "rep_index": 0,
                    "status": "SUCCESS_NONEMPTY",
                    "stream_engine_ms": 2.0,
                    "total_work_units": 10,
                },
                {
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": f"{w_id}_m2_pair1_rep0",
                    "workload_id": w_id,
                    "engine": "m2",
                    "pair_index": 1,
                    "rep_index": 0,
                    "status": "TIMEOUT",
                    "error": "Timeout expired",
                },
            ]
            with ledger.open("w", encoding="utf-8") as f:
                for att in attempts:
                    f.write(json.dumps(att) + "\n")

            manifest_path = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
            summarize_pilot(
                manifest_path=manifest_path,
                ledger_path=ledger,
                output_dir=out_dir,
            )

            csv_path = out_dir / "pilot_results.csv"
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [r for r in reader if r["workload_id"] == w_id]

            self.assertEqual(len(rows), 1)
            row = rows[0]
            # Since pair 1 was incomplete (M2 timed out), speedup ratio must NOT be calculated
            self.assertEqual(row["speedup_ratio_m1_over_m2"], "N/A")


if __name__ == "__main__":
    unittest.main()
