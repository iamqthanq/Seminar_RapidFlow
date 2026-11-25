import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import replay_rapidflow_apx_p4b as replay
from scripts import run_rapidflow_apx_p4b as runner
from scripts import summarize_rapidflow_apx_p4b as stats
from tests.test_rapidflow_apx_p4b_statistics import _make_synthetic_eligible_ledger


class TestPreregistrationR1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.workloads = {w["workload_id"]: w for w in cls.manifest["workloads"]}

    def test_f01_untrusted_authorization_and_synthetic_boundary(self):
        records = _make_synthetic_eligible_ledger()
        for alteration in (records, [dict(r, synthetic_test_fixture=False) for r in records]):
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED"):
                stats.analyze_p4b_ledger(alteration)
        for mutation in (dict(synthetic_test_fixture=False), dict(evidence_kind="REAL_CORRECTNESS"),
                         dict(evidence_kind="CONFIRMATORY_MEASUREMENT")):
            with self.subTest(mutation=mutation), self.assertRaises(runner.LedgerIntegrityError):
                stats.analyze_p4b_ledger([dict(records[0], **mutation), *records[1:]], allow_synthetic_mode=True)
        with tempfile.TemporaryDirectory() as td:
            forged = Path(td) / "protocol.json"
            forged.write_bytes(runner.canonical_json_bytes(dict(self.protocol, approval_state="MEASUREMENT_AUTHORIZED",
                                                                execution_authorized=True)))
            for flag in ("--execute-primary", "--execute-diagnostic"):
                result = subprocess.run([sys.executable, runner.__file__, "--protocol", str(forged), flag],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn("MEASUREMENT_NOT_AUTHORIZED", result.stderr)
            self.assertFalse(runner.check_execution_authorization(json.loads(forged.read_bytes()))[0])
            with self.assertRaisesRegex(runner.ScheduleIntegrityError, "PROTOCOL_HASH_MISMATCH"):
                runner.load_frozen_plan(forged)

    def test_f02_identity_and_eligibility_mutations(self):
        records = _make_synthetic_eligible_ledger()
        sample = records[0]
        planned = self.schedule["primary_slots"][0]
        workload = self.workloads[sample["workload_id"]]
        expected = runner.slot_identity(planned, self.protocol, experiment_id=runner.SYNTHETIC_EXPERIMENT,
                                        evidence_kind=runner.SYNTHETIC_KIND, binary_sha=runner.SYNTHETIC_BINARY,
                                        source_revision=runner.SYNTHETIC_SOURCE)
        self.assertEqual(stats.is_slot_eligible(sample, expected, workload), (True, "ELIGIBLE"))
        for field in expected:
            with self.subTest(field=field), self.assertRaises(runner.LedgerIntegrityError):
                stats.is_slot_eligible(dict(sample, **{field: None}), expected, workload)
        for field, value, reason in (
            ("repeat_id", True, None), ("source_revision", "b" * 40, None),
            ("binary_sha256", "b" * 64, None), ("experiment_id", "OTHER", None),
            ("updates_planned", 0, "INCOMPLETE_UPDATES_OR_DELIVERY"),
            ("updates_completed", True, "INCOMPLETE_UPDATES_OR_DELIVERY"),
            ("neighbor_configuration_version", 2.0, "SCHEMA_ERROR"),
            ("process_exit_code", False, "EXECUTION_FAILURE"),
            ("raw_output_sha256", "not-a-hash", "PROVENANCE_ERROR"),
            ("streaming_loop_wall_ns", math.nan, "MEASUREMENT_ERROR"),
            ("initialization_wall_ns", math.inf, "MEASUREMENT_ERROR"),
            ("resource_measurement_method", "linux_wait4_rusage_ru_maxrss", "MEASUREMENT_ERROR_MISSING_RSS"),
            ("peak_rss_bytes", None, "MEASUREMENT_ERROR_MISSING_RSS"),
        ):
            with self.subTest(field=field):
                bad = dict(sample, **{field: value})
                if reason is None:
                    with self.assertRaises(runner.LedgerIntegrityError):
                        stats.is_slot_eligible(bad, expected, workload)
                else:
                    self.assertEqual(stats.is_slot_eligible(bad, expected, workload), (False, reason))
        for raw in (b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": Infinity}', b'{"a": 1e999}'):
            with self.assertRaises(ValueError):
                runner.load_json_bytes(raw)

    def test_f03_zero_pair_censor_and_classifier_precedence(self):
        records = _make_synthetic_eligible_ledger()
        reasons = tuple(sorted(runner.RESOURCE_CENSORS))
        for row in records:
            if row["variant_id"] == "A_STATIC":
                row.update(attempt_outcome="CENSORED", censoring_reason=reasons[row["repeat_id"] - 1],
                           native_status="FAIL", process_exit_code=4, streaming_loop_wall_ns=None)
        result = stats.analyze_p4b_ledger(records, allow_synthetic_mode=True)["variants"]["A_STATIC"]
        self.assertEqual(result["ineligible_slot_reasons"]["A_STATIC"], dict.fromkeys(reasons, 50))
        self.assertEqual(result["censoring_table"]["bare_complete_variant_incomplete"], 50)
        self.assertEqual(result["speedup_distribution"]["estimator_status"], "UNAVAILABLE")
        self.assertIsNone(result["bootstrap_95"])
        self.assertIsNone(result["memory_summary"]["worst_workload_rss_overhead"])
        self.assertEqual(result["decision"]["priority_triggered"], 3)
        for row in records:
            if row["variant_id"] == "BARE":
                row.update(attempt_outcome="CENSORED", censoring_reason="TIMEOUT", native_status="FAIL")
        result = stats.analyze_p4b_ledger(records, allow_synthetic_mode=True)["variants"]["A_STATIC"]
        self.assertEqual(result["decision"]["priority_triggered"], 4)

    def test_f04_strata_feed_classifier_and_f05_primary_cohort(self):
        records = _make_synthetic_eligible_ledger(bare_ns=10_000_000)
        for row in records:
            if row["variant_id"] != "A_STATIC":
                continue
            w = self.workloads[row["workload_id"]]
            row["streaming_loop_wall_ns"] = 5_000_000 if w["label_regime"] == "mixed" else 20_000_000
            if w["workload_id"] == "DISC_07_K0":
                row["streaming_loop_wall_ns"] = 1
            if w["workload_id"] == "DISC_07_K1" and row["repeat_id"] == 3:
                row.update(attempt_outcome="CENSORED", censoring_reason="WORK_BUDGET_EXCEEDED", process_exit_code=4)
        result = stats.analyze_p4b_ledger(records, allow_synthetic_mode=True)
        report = result["variants"]["A_STATIC"]
        self.assertEqual(report["coverage"]["overall"], 48)
        self.assertEqual(report["coverage"]["all_complete_timing_pairs"], 49)
        self.assertEqual(report["descriptive_only_workloads"], ["DISC_07_K0"])
        self.assertEqual(report["speedup_distribution"]["max"], 2.0)
        self.assertEqual(report["decision"]["priority_triggered"], 6)
        self.assertEqual(len(report["strata"]), 15)
        self.assertAlmostEqual(stats._holm({"a": .01, "b": .03, "c": .04}, 3)["b"], .06)
        self.assertEqual(stats._holm({"a": .01, "b": .03}, 3)["a"], .03)
        self.assertEqual(result["holm_sensitivity"]["family_size"], 3)
        bad = copy.deepcopy(self.workloads)
        bad["DISC_01_K0"]["label_regime"] = "foreign"
        with self.assertRaisesRegex(runner.LedgerIntegrityError, "STRATUM"):
            stats._stratum_registry(bad, self.protocol)

    def test_f05_exact_rss_and_priority(self):
        for amount, priority in ((120 * 1024**2, 7), (120 * 1024**2 + 1024, 5)):
            rows = _make_synthetic_eligible_ledger(variant_rss_by_variant={
                "A_STATIC": amount, "A_RESIDUAL": 100 * 1024**2, "B_MERGE": 100 * 1024**2})
            report = stats.analyze_p4b_ledger(rows, allow_synthetic_mode=True)["variants"]["A_STATIC"]
            self.assertEqual(report["decision"]["priority_triggered"], priority)

    def test_f07_position_aggregate_preserving_mutation(self):
        for key in ("primary_slots", "warmup_slots", "diagnostic_slots"):
            modified = copy.deepcopy(self.schedule)
            one = modified[key][0]
            two = next(s for s in modified[key] if s["variant_id"] == one["variant_id"]
                       and s["execution_position"] != one["execution_position"])
            one["execution_position"], two["execution_position"] = two["execution_position"], one["execution_position"]
            with self.subTest(key=key), self.assertRaisesRegex(runner.ScheduleIntegrityError, key):
                runner.validate_schedule(modified, self.manifest)
        with tempfile.TemporaryDirectory() as td:
            mutation = Path(td) / "schedule.json"
            mutation.write_bytes(runner.DEFAULT_SCHEDULE_PATH.read_bytes() + b"\n")
            with self.assertRaisesRegex(runner.ScheduleIntegrityError, "SCHEDULE_HASH_MISMATCH"):
                runner.load_frozen_plan(schedule_path=mutation)

    def test_r09_local_comparator_checks_sequences_duplicates_and_semantics(self):
        a = {"steps": [{"entered_tuples": [[0, 1], [1, 0]], "step_counters": {"work_units": 6}}],
             "macro_timing": {"streaming_loop_wall_ns": 10, "clock_source": "steady"}}
        b = copy.deepcopy(a)
        b["macro_timing"]["streaming_loop_wall_ns"] = 200
        self.assertEqual(replay.compare_option_b(a, b), [])
        for mutation in (
            {"entered_tuples": [[1, 0], [0, 1]]}, {"entered_tuples": [[0, 1]]},
            {"entered_tuples": [[0, 1], [0, 1]]}, {"step_counters": {"work_units": 7}},
        ):
            with self.subTest(mutation=mutation):
                modified = copy.deepcopy(b)
                modified["steps"][0].update(mutation)
                self.assertTrue(replay.compare_option_b(a, modified))
        b["macro_timing"]["new_semantic_field"] = "unexpected"
        self.assertTrue(replay.compare_option_b(a, b))
        self.assertEqual(a["macro_timing"]["streaming_loop_wall_ns"], 10)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            replay.ordered_events({"entered_tuples": [[0, 1], [0, 1]]}, "entered_tuples", 2)
        with self.assertRaisesRegex(ValueError, "Invalid ordered"):
            replay.ordered_events({"entered_tuples": [[True, 1]]}, "entered_tuples", 2)

    def test_r10_synthetic_journal_full_plan_and_resume(self):
        calls = []
        inputs = {r["slot_id"]: r for r in _make_synthetic_eligible_ledger()}
        def simulate(slot):
            calls.append(slot["slot_id"])
            if slot["slot_id"] in inputs:
                return {key: value for key, value in inputs[slot["slot_id"]].items() if key != "raw_output_sha256"}
            return {"evidence_kind": runner.SYNTHETIC_KIND, "attempt_outcome": "COMPLETED"}

        with tempfile.TemporaryDirectory() as td, patch.object(runner.subprocess, "Popen") as spawn:
            root = Path(td)
            first = runner.exercise_synthetic_journal(root, simulate, stop_after=217)
            self.assertEqual(first["state"], "PAUSED")
            self.assertEqual(len(first["completed_records"]), 217)
            second = runner.exercise_synthetic_journal(root, simulate)
            self.assertEqual(second["state"], "FINISHED")
            self.assertEqual(len(second["completed_records"]), 1450)
            self.assertEqual(calls, [s["slot_id"] for s in runner.ordered_planned_slots(self.schedule)])
            self.assertEqual(len(set(calls)), 1450)
            self.assertEqual([r["raw_output_sha256"] for r in first["completed_records"]],
                             [r["raw_output_sha256"] for r in second["completed_records"][:217]])
            self.assertEqual(runner.exercise_synthetic_journal(root, simulate), second)
            summary = stats.analyze_p4b_ledger(second["completed_records"], allow_synthetic_mode=True)
            self.assertEqual(summary["attempt_accounting"]["unique_slots"], 1450)
            self.assertEqual(summary["variants"]["A_STATIC"]["decision"]["priority_triggered"], 7)
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED"):
                stats.analyze_p4b_ledger(second["completed_records"])
            spawn.assert_not_called()
            first_file = root / f"{calls[0]}.json"
            second_file = root / f"{calls[1]}.json"
            data = second_file.read_bytes()
            first_file.write_bytes(data)
            log = root / "synthetic-attempts.jsonl"
            original = log.read_bytes()
            rows = original.splitlines()
            first_finish = json.loads(rows[1])
            first_finish["raw_output_sha256"] = runner.sha256_bytes(data)
            rows[1] = json.dumps(first_finish).encode()
            log.write_bytes(b"\n".join(rows) + b"\n")
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "RAW_EVIDENCE_SLOT_MISMATCH"):
                runner.exercise_synthetic_journal(root, simulate)
            log.write_bytes(original)
            first_file.write_text("tampered")
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "RAW_EVIDENCE_MISMATCH"):
                runner.exercise_synthetic_journal(root, simulate)

    def test_r10_interruption_and_torn_record_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def interrupted(slot):
                (root / f"{slot['slot_id']}.json").write_text("partial output")
                raise KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                runner.exercise_synthetic_journal(root, interrupted)
            result = runner.exercise_synthetic_journal(root, lambda _: self.fail("Retry of interrupted execution"))
            self.assertEqual(result["state"], "INTERRUPTED")
            self.assertEqual(result["completed_records"], [])
            self.assertEqual((root / f"{result['slot_id']}.json").read_text(), "partial output")
            with (root / "synthetic-attempts.jsonl").open("ab") as stream:
                stream.write(b'{"truncated"')
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "TORN_JOURNAL_RECORD"):
                runner.exercise_synthetic_journal(root, interrupted)

    def test_r10_native_child_resource_outcome_and_stale_output(self):
        fixture = runner.REPO_ROOT / self.workloads["P4B_CONT_01"]["fixture_path"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = [
                ("timeout", "import time; time.sleep(10)", "TIMEOUT", 0.15),
                ("memory", "import sys\ntry:\n    bytearray(64 << 20)\nexcept MemoryError:\n    sys.exit('std::bad_alloc')",
                 "MEMORY_LIMIT_FAILURE", 2),
                ("interruption", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)", "ENVIRONMENT_INTERRUPTION", 2),
                ("missing", "pass", "MISSING_RESULT", 2),
            ]
            for name, script, reason, timeout in cases:
                with self.subTest(name=name):
                    child = root / name
                    child.write_text(f"#!{sys.executable}\n{script}\n")
                    child.chmod(0o700)
                    output = root / f"{name}.out"
                    result = runner.execute_p4b_native_slot(child, fixture, "BARE", "correctness", output,
                                                            timeout_s=timeout, as_limit_mib=48)
                    self.assertEqual(result["censoring_reason"], reason)
                    self.assertEqual(result["output_size_bytes"], 0)
                    with self.assertRaises(FileExistsError):
                        runner.execute_p4b_native_slot(child, fixture, "BARE", "correctness", output)
                    with self.assertRaisesRegex(runner.LedgerIntegrityError, "BINARY_HASH_MISMATCH"):
                        runner.execute_p4b_native_slot(child, fixture, "BARE", "correctness", root / "unused",
                                                       expected_binary_sha256="f" * 64)

    @unittest.skipUnless(runner.DEFAULT_BINARY_PATH.is_file(), "Native measurement binary is unavailable")
    def test_r10_raw_measurement_requires_exact_same_variant_correctness(self):
        selected = next(s for s in self.schedule["primary_slots"] if
                        s["workload_id"] == "P4B_CONT_01" and s["variant_id"] == "BARE")
        entry = self.workloads[selected["workload_id"]]
        fixture = runner.REPO_ROOT / entry["fixture_path"]
        workload = runner.load_json_bytes(fixture.read_bytes())
        binary_digest = runner.compute_sha256(runner.DEFAULT_BINARY_PATH)
        identity = runner.slot_identity(selected, self.protocol, experiment_id="BOUNDED_NATIVE_SMOKE",
                                        evidence_kind="REAL_FUNCTIONAL_SMOKE", binary_sha=binary_digest,
                                        source_revision=runner.NATIVE_SOURCE_COMMIT)
        with tempfile.TemporaryDirectory() as td:
            correct_path, measured_path = Path(td) / "correct.json", Path(td) / "measure.json"
            correctness = runner.execute_p4b_native_slot(runner.DEFAULT_BINARY_PATH, fixture, "BARE",
                                                        "correctness", correct_path)
            measurement = runner.execute_p4b_native_slot(runner.DEFAULT_BINARY_PATH, fixture, "BARE",
                                                        "performance", measured_path)
            self.assertEqual(correctness["exit_code"], 0)
            self.assertEqual(measurement["censoring_reason"], "NONE")
            timing = measurement["output_data"]["macro_timing"]
            row = dict(identity, synthetic_test_fixture=False, process_exit_code=0, process_signal=None,
                       updates_planned=len(workload["updates"]), updates_completed=len(workload["updates"]),
                       results_delivered=True, native_status="SUCCESS",
                       raw_output_sha256=measurement["output_sha256"],
                       streaming_loop_wall_ns=timing["streaming_loop_wall_ns"],
                       initialization_wall_ns=timing["initialization_wall_ns"],
                       **{key: measurement[key] for key in ("peak_rss_bytes", "peak_rss_original_value",
                                                            "peak_rss_original_unit", "resource_measurement_method")})
            def verify(candidate):
                runner.validate_native_measurement_evidence(candidate, identity, workload, measured_path,
                                                            correct_path, correctness["output_sha256"])
            verify(row)
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "VALUE_NOT_BOUND"):
                verify(dict(row, streaming_loop_wall_ns=row["streaming_loop_wall_ns"] + 1))
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "RAW_EVIDENCE_MISMATCH"):
                verify(dict(row, raw_output_sha256="a" * 64))
            native = measurement["output_data"]
            change = next(s for s in native["steps"] if len(s["entered_tuples"]) > 1)
            change["entered_tuples"].reverse()
            measured_path.write_bytes(runner.canonical_json_bytes(native))
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "SEMANTIC_MISMATCH"):
                verify(dict(row, raw_output_sha256=runner.compute_sha256(measured_path)))
