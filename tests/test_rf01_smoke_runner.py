#!/usr/bin/env python3
"""
Unit and regression tests for RF-01 smoke runner (scripts/rf01_smoke_runner.py).
Validates edge cases, parser robustness, and prevents false PASSes.
"""
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
    load_author_expected_counts,
    evaluate_smoke_case,
)

class TestRF01SmokeRunner(unittest.TestCase):

    def test_scenario_1_match_pass(self):
        """Case 1: Actual count equals expected count and exit code is 0 -> PASS_VERIFIED."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587884,
            expected_count=587884
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
        # Even if actual_count is parsed and exit_code is 0, this must NOT be marked case_ok = True
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=587884,
            expected_count=None
        )
        self.assertFalse(eval_res["case_ok"])
        self.assertEqual(eval_res["verdict"], "UNVERIFIED_NO_EXPECTED_COUNT")
        self.assertIsNone(eval_res["count_match"])

        # Also when actual_count is also None
        eval_res2 = evaluate_smoke_case(
            status="completed",
            exit_code=0,
            actual_count=None,
            expected_count=None
        )
        self.assertFalse(eval_res2["case_ok"])
        self.assertEqual(eval_res2["verdict"], "FAIL_MISSING_RESULT_COUNT")

    def test_scenario_6_nonzero_exit_code(self):
        """Case 6: Non-zero exit code (e.g. crash or error) -> FAIL_NONZERO_EXIT."""
        eval_res = evaluate_smoke_case(
            status="completed",
            exit_code=139,  # segmentation fault
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

    def test_author_expected_counts_loader(self):
        """Test parsing of author's expected_output.txt format."""
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("Q_0: 587884\n# comment\nQ_1:12866379\n\nQ_2: 1139364\n")
            tf_path = tf.name
        try:
            expected = load_author_expected_counts(tf_path)
            self.assertEqual(expected.get("Q_0"), 587884)
            self.assertEqual(expected.get("Q_1"), 12866379)
            self.assertEqual(expected.get("Q_2"), 1139364)
        finally:
            pathlib.Path(tf_path).unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
