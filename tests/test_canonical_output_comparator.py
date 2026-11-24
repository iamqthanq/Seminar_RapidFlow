"""
tests/test_canonical_output_comparator.py
Unit tests for the strict recursive canonical output comparator (Finding R1-RV01).
"""

import copy
import json
from pathlib import Path
import unittest

from scripts.canonical_output_comparator import (
    diff_canonical_structures,
    compare_3way_canonical_outputs,
    compare_against_starting_p4b2b,
    compute_raw_input_sha256,
)
from scripts.run_rapidflow_apx_engines import build_hand_workloads, run_workload_on_binary

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY = REPO_ROOT / "tests/native/rapidflow_apx_engines/build/rapidflow_apx_native"


class TestCanonicalOutputComparator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w = build_hand_workloads()[0]
        cls.raw_bytes = json.dumps(w).encode("utf-8")
        cls.res_leg, _, _ = run_workload_on_binary(BINARY, "m2", w, nlf_mode="none", neighbor_mode="legacy")
        cls.res_buf, _, _ = run_workload_on_binary(BINARY, "m2", w, nlf_mode="none", neighbor_mode="buffer")
        cls.res_mrg, _, _ = run_workload_on_binary(BINARY, "m2", w, nlf_mode="none", neighbor_mode="merge")

    def test_clean_3way_outputs_agree(self):
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, self.res_mrg)
        self.assertTrue(ok, f"Unexpected diffs: {diffs}")
        self.assertEqual(len(diffs), 0)

    def test_tuple_mutation_detected(self):
        mut = copy.deepcopy(self.res_mrg)
        mut["steps"][0]["entered_tuples"].append([99, 99])
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, mut)
        self.assertFalse(ok)
        self.assertTrue(any("entered_tuples" in d for d in diffs))

    def test_legacy_counter_mutation_detected(self):
        mut = copy.deepcopy(self.res_mrg)
        mut["counters"]["candidate_evaluations"] += 1
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, mut)
        self.assertFalse(ok)
        self.assertTrue(any("candidate_evaluations" in d for d in diffs))

    def test_step_counter_mutation_detected(self):
        mut = copy.deepcopy(self.res_mrg)
        mut["steps"][0]["step_counters"]["candidate_evaluations"] += 1
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, mut)
        self.assertFalse(ok)
        self.assertTrue(any("candidate_evaluations" in d for d in diffs))

    def test_missing_field_detected(self):
        mut = copy.deepcopy(self.res_mrg)
        del mut["query_info"]
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, mut)
        self.assertFalse(ok)
        self.assertTrue(any("query_info" in d for d in diffs))

    def test_type_mismatch_detected(self):
        mut = copy.deepcopy(self.res_mrg)
        mut["status"] = True  # bool instead of string
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, mut)
        self.assertFalse(ok)
        self.assertTrue(any("type mismatch" in d for d in diffs))

    def test_partition_invariant_violation_legacy(self):
        mut = copy.deepcopy(self.res_leg)
        mut["counters"]["neighbor_buffer_retrieval_calls"] = 1
        ok, diffs = compare_3way_canonical_outputs(mut, self.res_buf, self.res_mrg)
        self.assertFalse(ok)
        self.assertTrue(any("LEGACY counters.neighbor_buffer_retrieval_calls must be 0" in d for d in diffs))

    def test_partition_invariant_violation_merge(self):
        mut = copy.deepcopy(self.res_mrg)
        mut["counters"]["neighbor_merge_fallback_calls"] = 1
        ok, diffs = compare_3way_canonical_outputs(self.res_leg, self.res_buf, mut)
        self.assertFalse(ok)
        self.assertTrue(any("unexpected fallback calls" in d or "partition violated" in d for d in diffs))

    def test_raw_input_sha256(self):
        sha = compute_raw_input_sha256(self.raw_bytes)
        self.assertEqual(len(sha), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in sha))


if __name__ == "__main__":
    unittest.main()
