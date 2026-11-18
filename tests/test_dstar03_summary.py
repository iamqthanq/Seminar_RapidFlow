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
        with (REPO_ROOT / "reports" / "dstar03" / "attempts.jsonl").open("r", encoding="utf-8") as f:
            cls.valid_header = json.loads(f.readline())

    def _make_slot(
        self,
        attempt_id: Optional[str] = None,
        workload_id: str = "witness_01_diamond_k1",
        engine: str = "m1",
        pair_index: int = 1,
        rep_index: int = 0,
        order_in_pair: int = 1,
        status: str = "SUCCESS_NONEMPTY",
        exit_code: int = 0,
        subprocess_status: str = "OK",
        warmup_status: str = "SUCCESS",
        stream_engine_ms: float = 2.0,
        worker_wall_ms: float = 3.0,
        total_budget_units: int = 40,
        counters: Optional[Dict[str, int]] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        git_commit: Optional[str] = None,
    ) -> Dict[str, Any]:
        if attempt_id is None:
            attempt_id = f"{workload_id}_{engine}_pair{pair_index}_rep{rep_index}"
        if steps is None:
            steps = [{"step": i, "budget_units": 10} for i in range(4)]
        if counters is None:
            counters = {"candidate_evaluations": 10}
        if git_commit is None:
            git_commit = self.valid_header["git_commit"]
        return {
            "record_type": "ATTEMPT_COMPLETED",
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "engine": engine,
            "pair_index": pair_index,
            "rep_index": rep_index,
            "order_in_pair": order_in_pair,
            "status": status,
            "exit_code": exit_code,
            "subprocess_status": subprocess_status,
            "warmup_status": warmup_status,
            "stream_engine_ms": stream_engine_ms,
            "worker_wall_ms": worker_wall_ms,
            "total_budget_units": total_budget_units,
            "counters": counters,
            "steps": steps,
            "git_provenance": {"commit": git_commit},
        }

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
                self.valid_header,
                {"record_type": "ATTEMPT_STARTED", "attempt_id": m1_id},
                self._make_slot(workload_id=w_id, engine="m1", pair_index=1, rep_index=0, order_in_pair=1),
                {"record_type": "ATTEMPT_STARTED", "attempt_id": m2_id},
                {
                    "record_type": "ATTEMPT_COMPLETED",
                    "attempt_id": m2_id,
                    "workload_id": w_id,
                    "engine": "m2",
                    "pair_index": 1,
                    "rep_index": 0,
                    "order_in_pair": 2,
                    "status": "TIMEOUT",
                    "error": "Timeout expired",
                    "git_provenance": {"commit": self.valid_header["git_commit"]},
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
                f.write(json.dumps(self.valid_header) + "\n")
                for w in self.manifest["workloads"]:
                    w_id = w["workload_id"]
                    case_idx = w["case_index"]
                    pair_patterns = [
                        ("m1", "m2") if (case_idx % 2 == 0) else ("m2", "m1"),
                        ("m2", "m1") if (case_idx % 2 == 0) else ("m1", "m2"),
                        ("m1", "m2") if (case_idx % 2 == 0) else ("m2", "m1"),
                    ]
                    for p in (1, 2, 3):
                        rep = p - 1
                        for sub_rep, eng in enumerate(pair_patterns[p - 1], start=1):
                            att_id = f"{w_id}_{eng}_pair{p}_rep{rep}"
                            f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                            f.write(json.dumps({
                                "record_type": "ATTEMPT_COMPLETED",
                                "attempt_id": att_id,
                                "workload_id": w_id,
                                "engine": eng,
                                "pair_index": p,
                                "rep_index": rep,
                                "order_in_pair": sub_rep,
                                "status": "TIMEOUT",
                                "error": "Timed out",
                                "git_provenance": {"commit": self.valid_header["git_commit"]},
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
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot()) + "\n")
                f.write(json.dumps(self._make_slot()) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("duplicate completion", str(ctx.exception).lower())

    def test_07_corrupt_jsonl_rejected(self) -> None:
        """F01: Verifies that corrupt/malformed JSON in ledger raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "corrupt_ledger.jsonl"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
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
                f.write(json.dumps(self.valid_header) + "\n")
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

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps(self._make_slot()) + "\n")

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

    def test_11_f01_reject_success_with_exit_code_1(self) -> None:
        """F01 regression: Verifies slot claiming SUCCESS with non-zero exit_code raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "bad_exit.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot(exit_code=1)) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("exit_code is 1", str(ctx.exception))

    def test_12_f01_reject_success_with_bad_subprocess_status(self) -> None:
        """F01 regression: Verifies slot claiming SUCCESS with subprocess_status != OK raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            for bad_st in ("ERROR", "TIMEOUT", "RESOURCE_LIMIT"):
                ledger = tmp_path / f"bad_sub_{bad_st}.jsonl"
                att_id = "witness_01_diamond_k1_m1_pair1_rep0"

                with ledger.open("w", encoding="utf-8") as f:
                    f.write(json.dumps(self.valid_header) + "\n")
                    f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                    f.write(json.dumps(self._make_slot(subprocess_status=bad_st)) + "\n")

                with self.assertRaises(ValueError) as ctx:
                    summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
                self.assertIn(f"subprocess_status is '{bad_st}'", str(ctx.exception))

    def test_13_f01_reject_success_with_failed_warmup(self) -> None:
        """F01 regression: Verifies slot claiming SUCCESS with warmup_status != SUCCESS raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "failed_warmup.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot(warmup_status="FAILED")) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("warmup_status is 'FAILED'", str(ctx.exception))

    def test_14_f01_reject_payload_wrong_engine_or_workload(self) -> None:
        """F01 regression: Verifies slot completion with conflicting engine or workload raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Conflict 1: scheduled m1, record says m2 inside payload
            ledger1 = tmp_path / "wrong_engine.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"
            with ledger1.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot(attempt_id=att_id, engine="m2")) + "\n")

            with self.assertRaises(ValueError) as ctx1:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger1, output_dir=tmp_path / "out")
            self.assertIn("scheduled", str(ctx1.exception))
            self.assertIn("recorded", str(ctx1.exception))

            # Conflict 2: scheduled witness_01, record says witness_02 inside payload
            ledger2 = tmp_path / "wrong_workload.jsonl"
            with ledger2.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot(attempt_id=att_id, workload_id="witness_02_diamond_k2")) + "\n")

            with self.assertRaises(ValueError) as ctx2:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger2, output_dir=tmp_path / "out")
            self.assertIn("scheduled", str(ctx2.exception))
            self.assertIn("recorded", str(ctx2.exception))

    def test_15_f01_reject_payload_wrong_step_count(self) -> None:
        """F01 regression: Verifies slot claiming SUCCESS with wrong step count raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "wrong_steps.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                # witness_01 stream_length is 4, provide 2 steps
                f.write(json.dumps(self._make_slot(steps=[{"step": 0}, {"step": 1}])) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("payload steps count", str(ctx.exception))

    def test_16_f02_reject_fake_header_hash(self) -> None:
        """F02 regression: Verifies fake header hash is caught by provenance git show check."""
        import copy
        bad_header = copy.deepcopy(self.valid_header)
        bad_header["source_hashes"]["scripts/dstar_oracle.py"] = "0" * 64

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "fake_header_hash.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(bad_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot()) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("header source hash mismatch", str(ctx.exception))

    def test_17_f02_reject_header_missing_source_hashes(self) -> None:
        """F02 regression: Verifies header with missing/empty source_hashes raises ValueError."""
        import copy
        bad_header = copy.deepcopy(self.valid_header)
        bad_header["source_hashes"] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "empty_source_hashes.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(bad_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot()) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("missing or empty 'source_hashes'", str(ctx.exception))

    def test_18_f02_reject_recorded_commit_mismatch(self) -> None:
        """F02 regression: Verifies non-existent recorded commit raises ValueError on git show."""
        import copy
        bad_header = copy.deepcopy(self.valid_header)
        bad_header["git_commit"] = "0" * 40

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "bad_commit.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(bad_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot()) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("cannot retrieve", str(ctx.exception))

    def test_19_f02_reject_worker_commit_mismatch_with_header(self) -> None:
        """F02 regression: Verifies worker git commit mismatch with header commit raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ledger = tmp_path / "worker_commit_mismatch.jsonl"
            att_id = "witness_01_diamond_k1_m1_pair1_rep0"

            with ledger.open("w", encoding="utf-8") as f:
                f.write(json.dumps(self.valid_header) + "\n")
                f.write(json.dumps({"record_type": "ATTEMPT_STARTED", "attempt_id": att_id}) + "\n")
                f.write(json.dumps(self._make_slot(git_commit="different_commit_hash_12345")) + "\n")

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=self.manifest_path, ledger_path=ledger, output_dir=tmp_path / "out")
            self.assertIn("worker commit", str(ctx.exception))

    def test_20_f02_reject_fixture_fingerprint_mismatch(self) -> None:
        """F02 regression: Verifies summarize raises ValueError if fixture fingerprint mismatches manifest."""
        fixtures_dir = REPO_ROOT / "tests" / "fixtures"
        with tempfile.TemporaryDirectory(dir=str(fixtures_dir)) as tmpdir:
            tmp_m_path = Path(tmpdir) / "manifest.json"
            tmp_fix_path = Path(tmpdir) / "bad_fixture.json"

            with (REPO_ROOT / "tests" / "fixtures" / "dstar03" / "workloads" / "witness_01_diamond_k1.json").open("r", encoding="utf-8") as f:
                fix_data = json.load(f)
            fix_data["sha256_workload"] = "mismatch_fingerprint_value"
            with tmp_fix_path.open("w", encoding="utf-8") as f:
                json.dump(fix_data, f)

            with self.manifest_path.open("r", encoding="utf-8") as f:
                man_data = json.load(f)

            man_data["workloads"][0]["relative_path"] = str(tmp_fix_path.relative_to(REPO_ROOT))

            with tmp_m_path.open("w", encoding="utf-8") as f:
                json.dump(man_data, f)

            empty_ledger = Path(tmpdir) / "empty.jsonl"
            empty_ledger.touch()

            with self.assertRaises(ValueError) as ctx:
                summarize_pilot(manifest_path=tmp_m_path, ledger_path=empty_ledger, output_dir=Path(tmpdir) / "out")
            self.assertIn("fixture fingerprint mismatch", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
