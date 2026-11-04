#!/usr/bin/env python3
"""
Unit and regression tests for RF-01 smoke runner (scripts/rf01_smoke_runner.py).
Validates edge cases, parser robustness, reference provenance fail-closed checks (F1),
and telemetry evidence completeness evaluation (F2).
"""
import hashlib
import pathlib
import sys
import tempfile
import unittest

# Add project root to sys.path
repo_root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "scripts"))

from rf01_smoke_runner import (
    parse_result_count_from_text,
    parse_result_count,
    parse_resources,
    verify_and_load_author_reference,
    evaluate_telemetry,
    evaluate_smoke_case,
    ReferenceVerificationError,
)

class TestRF01SmokeRunner(unittest.TestCase):

    def test_scenario_1_match_pass(self):
        """Case 1: Actual count equals expected count and exit code is 0 -> PASS_VERIFIED."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587884,
            expected_count=587884,
            ref_expected_count=587884
        )
        self.assertTrue(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "PASS_VERIFIED")
        self.assertIs(eval_res["count_match"], True)

    def test_scenario_2_count_mismatch(self):
        """Case 2: Actual count differs from expected count -> FAIL_COUNT_MISMATCH."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587880,
            expected_count=587884
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_COUNT_MISMATCH")
        self.assertIs(eval_res["count_match"], False)

    def test_scenario_3_missing_result_count(self):
        """Case 3: Stdout contains no parseable Result count -> FAIL_MISSING_RESULT_COUNT."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=None,
            expected_count=587884
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_MISSING_RESULT_COUNT")
        self.assertIs(eval_res["count_match"], False)

    def test_scenario_4_malformed_result_count(self):
        """Case 4: Malformed Result count string in stdout cannot be parsed."""
        raw_stdout = "Execution Summary:\nResult count: NOT_A_NUMBER\nDone.\n"
        actual = parse_result_count_from_text(raw_stdout)
        self.assertIsNone(actual)
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=actual,
            expected_count=587884
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_MISSING_RESULT_COUNT")

    def test_scenario_5_missing_expected_count_no_false_pass(self):
        """Case 5: Expected count is None -> UNVERIFIED_NO_EXPECTED_COUNT, NOT case_ok."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587884,
            expected_count=None
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "UNVERIFIED_NO_EXPECTED_COUNT")
        self.assertIsNone(eval_res["count_match"])

        eval_res2 = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=None,
            expected_count=None
        )
        self.assertFalse(eval_res2["case_ok"])
        self.assertEqual(eval_res2["verdict"], "FAIL_MISSING_RESULT_COUNT")

    def test_scenario_6_nonzero_exit_code(self):
        """Case 6: Non-zero exit code -> FAIL_NONZERO_EXIT."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=139,
            actual_count=587884,
            expected_count=587884
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_NONZERO_EXIT")
        self.assertIs(eval_res["count_match"], False)

    def test_scenario_7_timeout(self):
        """Case 7: Execution timeout -> FAIL_PROCESS_STATUS_TIMEOUT."""
        eval_res = evaluate_smoke_case(
            status="timeout",
            exit_code=124,
            actual_count=None,
            expected_count=587884
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_PROCESS_STATUS_TIMEOUT")
        self.assertIs(eval_res["count_match"], False)

    def test_scenario_8_unreadable_missing_log_files(self):
        """Case 8: Unreadable or missing stdout/resources files return safe defaults."""
        missing_file = pathlib.Path("/nonexistent/stdout.log")
        self.assertIsNone(parse_result_count(missing_file))
        res = parse_resources(missing_file)
        self.assertIsNone(res["elapsed_seconds"])
        self.assertIsNone(res["max_rss_kb"])

    # --- F1: Fail-closed Author Reference Provenance Tests ---

    def test_f1_reference_file_missing(self):
        """F1: Missing author reference file raises FILE_NOT_FOUND."""
        missing_ref = pathlib.Path("/nonexistent/expected_output.txt")
        with self.assertRaises(ReferenceVerificationError) as ctx:
            verify_and_load_author_reference(missing_ref)
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")

    def test_f1_reference_sha256_mismatch(self):
        """F1: Reference file with mismatched SHA-256 raises SHA256_MISMATCH."""
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("Q_0: 100\n")
            tf_path = tf.name
        try:
            wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"
            with self.assertRaises(ReferenceVerificationError) as ctx:
                verify_and_load_author_reference(tf_path, expected_sha256=wrong_sha)
            self.assertEqual(ctx.exception.code, "SHA256_MISMATCH")
        finally:
            pathlib.Path(tf_path).unlink(missing_ok=True)

    def test_f1_reference_malformed_integer(self):
        """F1: Reference file containing non-integer count raises MALFORMED_INTEGER."""
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("Q_0: NOT_A_NUMBER\n")
            tf_path = tf.name
        try:
            with self.assertRaises(ReferenceVerificationError) as ctx:
                verify_and_load_author_reference(tf_path)
            self.assertEqual(ctx.exception.code, "MALFORMED_INTEGER")
        finally:
            pathlib.Path(tf_path).unlink(missing_ok=True)

    def test_f1_reference_query_not_found(self):
        """F1: Query missing from author reference fails closed with FAIL_REFERENCE_QUERY_NOT_FOUND."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587884,
            expected_count=587884,
            ref_error_code="QUERY_NOT_FOUND",
            ref_error_msg="Query Q_999 not found in author reference"
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_REFERENCE_QUERY_NOT_FOUND")
        self.assertIn("Q_999", eval_res["details"])

    def test_f1_reference_config_contradiction_counterexample(self):
        """
        F1: Counterexample from review: config expected count contradicts author reference.
        Even if actual count matches config, verdict MUST be FAIL_REFERENCE_CONFIG_MISMATCH.
        """
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587884,
            expected_count=587884,
            ref_expected_count=0,
            ref_error_code="CONFIG_MISMATCH",
            ref_error_msg="Config expected count (587884) contradicts author reference (0)"
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "FAIL_REFERENCE_CONFIG_MISMATCH")
        self.assertIn("contradicts", eval_res["details"])

    def test_f1_valid_reference_loading(self):
        """F1: Valid author reference file with correct SHA-256 parses successfully."""
        content = "Q_0: 587884\n# comment\nQ_1: 12866379\n\nQ_2: 1139364\n"
        expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write(content)
            tf_path = tf.name
        try:
            res = verify_and_load_author_reference(tf_path, expected_sha256=expected_sha)
            self.assertEqual(res.get("Q_0"), 587884)
            self.assertEqual(res.get("Q_1"), 12866379)
            self.assertEqual(res.get("Q_2"), 1139364)
        finally:
            pathlib.Path(tf_path).unlink(missing_ok=True)

    # --- F2: Telemetry Completeness Evaluation Tests ---

    def test_f2_telemetry_complete(self):
        """F2: Telemetry with valid wall time, peak RSS > 0, and raw_dir -> COMPLETE_METRICS."""
        self.assertEqual(
            evaluate_telemetry(elapsed_seconds=3.25, max_rss_kb=265000, raw_dir="/tmp/test"),
            "COMPLETE_METRICS"
        )

    def test_f2_telemetry_missing_rss(self):
        """F2: Missing RSS -> INCOMPLETE_METRICS."""
        self.assertEqual(
            evaluate_telemetry(elapsed_seconds=3.25, max_rss_kb=None, raw_dir="/tmp/test"),
            "INCOMPLETE_METRICS"
        )
        self.assertEqual(
            evaluate_telemetry(elapsed_seconds=3.25, max_rss_kb=0, raw_dir="/tmp/test"),
            "INCOMPLETE_METRICS"
        )

    def test_f2_telemetry_missing_elapsed(self):
        """F2: Missing elapsed seconds -> INCOMPLETE_METRICS."""
        self.assertEqual(
            evaluate_telemetry(elapsed_seconds=None, max_rss_kb=265000, raw_dir="/tmp/test"),
            "INCOMPLETE_METRICS"
        )

    def test_f2_telemetry_missing_raw_dir(self):
        """F2: Missing raw_dir -> INCOMPLETE_METRICS."""
        self.assertEqual(
            evaluate_telemetry(elapsed_seconds=3.25, max_rss_kb=265000, raw_dir=None),
            "INCOMPLETE_METRICS"
        )

if __name__ == "__main__":
    unittest.main()
