import copy
import json
import math
import statistics
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import run_rapidflow_apx_p4b as p4b_runner
from scripts import summarize_rapidflow_apx_p4b as stats_mod


def _make_synthetic_eligible_ledger(
    *,
    bare_ns: int = 10_000_000,
    bare_rss: int = 100 * 1024 * 1024,
    variant_ns_by_variant: Dict[str, int] | None = None,
    variant_rss_by_variant: Dict[str, int] | None = None,
) -> List[Dict[str, Any]]:
    """
    Build a clearly labeled SYNTHETIC FIXTURE ledger conforming strictly to the 1,000-slot schedule.
    Never used as benchmark evidence; strictly for statistical software boundary tests.
    """
    v_ns = variant_ns_by_variant or {"A_STATIC": 8_000_000, "A_RESIDUAL": 10_000_000, "B_MERGE": 12_000_000}
    v_rss = variant_rss_by_variant or {"A_STATIC": 110 * 1024 * 1024, "A_RESIDUAL": 105 * 1024 * 1024, "B_MERGE": 102 * 1024 * 1024}

    protocol = json.loads((REPO_ROOT / "configs" / "rapidflow_apx_p4b_protocol.json").read_text(encoding="utf-8"))
    schedule = json.loads((REPO_ROOT / "configs" / "rapidflow_apx_p4b_schedule.json").read_text(encoding="utf-8"))

    manifest = json.loads(p4b_runner.DEFAULT_MANIFEST_PATH.read_text())
    updates = {entry["workload_id"]: entry["update_count"] for entry in manifest["workloads"]}
    records: List[Dict[str, Any]] = []
    for slot in schedule["primary_slots"]:
        vid = slot["variant_id"]
        vspec = p4b_runner.ALL_VARIANTS[vid]
        t_ns = bare_ns if vid == "BARE" else v_ns[vid]
        rss = bare_rss if vid == "BARE" else v_rss[vid]
        records.append({
            **p4b_runner.slot_identity(
                slot, protocol, experiment_id=p4b_runner.SYNTHETIC_EXPERIMENT,
                evidence_kind=p4b_runner.SYNTHETIC_KIND, binary_sha=p4b_runner.SYNTHETIC_BINARY,
                source_revision=p4b_runner.SYNTHETIC_SOURCE,
            ),
            "synthetic_test_fixture": True,
            "attempt_number": 1,
            "attempt_id": slot["slot_id"] + "/1",
            "attempt_outcome": "COMPLETED",
            "raw_output_sha256": "a" * 64,
            "effective_nlf_mode": vspec["nlf_mode"],
            "requested_neighbor_mode": vspec["neighbor_mode"],
            "effective_neighbor_mode": vspec["neighbor_mode"],
            "configuration_initialized": True,
            "configuration_status": "SUCCESS",
            "configuration_valid": True,
            "measurement_schema_version": 1,
            "neighbor_configuration_version": 2,
            "deep_diagnostics_enabled": False,
            "profiling_enabled": False,
            "execution_purpose": "performance",
            "eligible_for_requested_variant": True,
            "streaming_loop_wall_ns": t_ns,
            "initialization_wall_ns": 500_000,
            "reporting_wall_ns": 100_000,
            "outer_wall_ns": t_ns + 1_000_000,
            "user_cpu_ns": t_ns,
            "system_cpu_ns": 50_000,
            "peak_rss_bytes": rss,
            "peak_rss_original_value": rss // 1024,
            "peak_rss_original_unit": "KiB",
            "resource_measurement_method": "linux_proc_self_status_vmhwm",
            "process_exit_code": 0,
            "process_signal": None,
            "native_status": "SUCCESS",
            "updates_planned": updates[slot["workload_id"]],
            "updates_completed": updates[slot["workload_id"]],
            "results_delivered": True,
            "correctness_status": "DIFFERENTIAL_M2_VERIFIED",
            "measurement_eligible": True,
            "censoring_reason": "NONE",
        })
    return records


class TestP4bDecisionBoundariesAndStatistics(unittest.TestCase):
    def test_01_all_19_synthetic_decision_boundary_cases(self) -> None:
        """Verify DEC-01 through DEC-19 exact mutually exclusive outcomes (Section XXXIV)."""
        cases = [
            # (ID, kwargs, expected_decision, expected_priority)
            ("DEC-01", {"speedup_median": 1.20, "ci_lower": 1.10, "ci_upper": 1.30}, "ADVANTAGE_OBSERVED", 7),
            ("DEC-02", {"speedup_median": 1.15, "ci_lower": 1.01, "ci_upper": 1.25}, "ADVANTAGE_OBSERVED", 7),
            ("DEC-03", {"speedup_median": 1.15, "ci_lower": 1.00, "ci_upper": 1.25}, "INCONCLUSIVE", 11),
            ("DEC-04", {"speedup_median": 1.20, "ci_lower": 0.90, "ci_upper": 1.40}, "INCONCLUSIVE", 11),
            ("DEC-05", {"speedup_median": 1.00, "ci_lower": 0.95, "ci_upper": 1.05}, "PARITY", 8),
            ("DEC-06", {"speedup_median": 1.05, "ci_lower": 1.01, "ci_upper": 1.05}, "PARITY", 8),
            ("DEC-07", {"speedup_median": 1.05, "ci_lower": 1.01, "ci_upper": 1.10}, "MARGINAL", 10),
            ("DEC-08", {"speedup_median": 1.14, "ci_lower": 1.02, "ci_upper": 1.20}, "MARGINAL", 10),
            ("DEC-09", {"speedup_median": 0.90, "ci_lower": 0.80, "ci_upper": 0.98}, "NO_GO", 9),
            ("DEC-10", {"speedup_median": 0.90, "ci_lower": 0.80, "ci_upper": 1.02}, "INCONCLUSIVE", 11),
            ("DEC-11", {"speedup_median": 0.95, "ci_lower": 0.95, "ci_upper": 1.02}, "PARITY", 8),
            ("DEC-12", {"speedup_median": 1.25, "ci_lower": 1.10, "ci_upper": 1.40, "worst_rss_overhead": 0.25}, "NO_GO", 5),
            ("DEC-13", {"speedup_median": 1.25, "ci_lower": 1.10, "ci_upper": 1.40, "worst_rss_overhead": 0.20}, "ADVANTAGE_OBSERVED", 7),
            ("DEC-14", {"speedup_median": 1.30, "ci_lower": 1.20, "ci_upper": 1.40, "semantic_mismatch": True}, "UNSOUND", 1),
            ("DEC-15", {"speedup_median": 1.30, "ci_lower": 1.20, "ci_upper": 1.40, "overall_coverage_ok": False}, "INCONCLUSIVE", 4),
            ("DEC-16", {"speedup_median": 1.25, "ci_lower": 1.10, "ci_upper": 1.40, "missing_rss": True, "worst_rss_overhead": None}, "INCONCLUSIVE", 2),
            ("DEC-17", {"speedup_median": 1.30, "ci_lower": 1.10, "ci_upper": 1.50, "variant_only_resource_regression": True}, "NO_GO", 3),
            ("DEC-18", {"speedup_median": 1.10, "ci_lower": 0.99, "ci_upper": 1.20}, "INCONCLUSIVE", 11),
            (
                "DEC-19",
                {
                    "speedup_median": 1.20,
                    "ci_lower": 1.01,
                    "ci_upper": 1.40,
                    "eligible_strata_results": [
                        {"name": "mixed", "eligible_for_stratum_decision": True, "median_speedup": 1.25, "ci_lower": 1.10, "ci_upper": 1.40},
                        {"name": "uniform", "eligible_for_stratum_decision": True, "median_speedup": 0.85, "ci_lower": 0.78, "ci_upper": 0.93},
                    ],
                },
                "QUERY_DEPENDENT",
                6,
            ),
        ]
        for cid, kw, exp_dec, exp_prio in cases:
            out = stats_mod.classify_variant_decision(**kw)
            self.assertEqual(out["primary_decision"], exp_dec, f"{cid} got {out}")
            self.assertEqual(out["priority_triggered"], exp_prio, f"{cid} priority mismatch")
            self.assertIn(out["primary_decision"], stats_mod.VALID_DECISIONS)
            if cid == "DEC-07":
                self.assertEqual(out["r4b_disposition"], "INCONCLUSIVE_FOR_PRACTICAL_ADVANTAGE")

        # Additional exact equality checks (S=0.95/1.05/1.15, U=1.00, RSS=0.20001)
        out_u1 = stats_mod.classify_variant_decision(speedup_median=0.90, ci_lower=0.80, ci_upper=1.00)
        self.assertEqual(out_u1["primary_decision"], "INCONCLUSIVE")
        out_rss_over = stats_mod.classify_variant_decision(speedup_median=1.25, ci_lower=1.10, ci_upper=1.40, worst_rss_overhead=0.20001)
        self.assertEqual(out_rss_over["primary_decision"], "NO_GO")

        # Noise failure test (Priority 4) & Evidence integrity failure (Priority 2) & UNSOUND overrides all
        self.assertEqual(
            stats_mod.classify_variant_decision(speedup_median=1.25, ci_lower=1.10, ci_upper=1.40, excessive_noise=True)["primary_decision"],
            "INCONCLUSIVE",
        )
        self.assertEqual(
            stats_mod.classify_variant_decision(speedup_median=1.25, ci_lower=1.10, ci_upper=1.40, evidence_integrity_ok=False)["primary_decision"],
            "INCONCLUSIVE",
        )
        self.assertEqual(
            stats_mod.classify_variant_decision(
                speedup_median=1.25, ci_lower=1.10, ci_upper=1.40, semantic_mismatch=True, evidence_integrity_ok=False, variant_only_resource_regression=True
            )["primary_decision"],
            "UNSOUND",
        )

    def test_02_invalid_numeric_inputs_fail_closed(self) -> None:
        bad_inputs = [
            {"speedup_median": float("nan"), "ci_lower": 1.0, "ci_upper": 1.2},
            {"speedup_median": float("inf"), "ci_lower": 1.0, "ci_upper": 1.2},
            {"speedup_median": 0.0, "ci_lower": 0.0, "ci_upper": 1.2},
            {"speedup_median": -1.1, "ci_lower": -1.2, "ci_upper": 1.0},
            {"speedup_median": 1.1, "ci_lower": 1.2, "ci_upper": 1.0},   # L > U
            {"speedup_median": 1.1, "ci_lower": 1.0, "ci_upper": 1.2, "worst_rss_overhead": float("nan")},
        ]
        for bad in bad_inputs:
            with self.assertRaises(ValueError, msg=f"Failed to reject {bad}"):
                stats_mod.classify_variant_decision(**bad)

        outside = stats_mod.classify_variant_decision(speedup_median=1.3, ci_lower=1.0, ci_upper=1.2)
        self.assertTrue(outside["gate_diagnostics"]["point_estimate_outside_ci"])

    def test_03_synthetic_full_ledger_pipeline_bootstrap_and_anti_pseudo_replication(self) -> None:
        recs = _make_synthetic_eligible_ledger(
            bare_ns=10_000_000,
            bare_rss=100 * 1024 * 1024,
            variant_ns_by_variant={"A_STATIC": 8_000_000, "A_RESIDUAL": 10_000_000, "B_MERGE": 12_000_000},
            variant_rss_by_variant={"A_STATIC": 110 * 1024 * 1024, "A_RESIDUAL": 105 * 1024 * 1024, "B_MERGE": 102 * 1024 * 1024},
        )
        # Verify stratified bootstrap determinism & equal workload weight
        s1 = stats_mod.analyze_p4b_ledger(recs, allow_synthetic_mode=True)
        s2 = stats_mod.analyze_p4b_ledger(recs, allow_synthetic_mode=True)
        self.assertEqual(s1, s2)
        self.assertEqual(s1["analysis_mode"], "SYNTHETIC_FIXTURE_VERIFICATION")
        self.assertEqual(s1["variants"]["A_STATIC"]["decision"]["primary_decision"], "ADVANTAGE_OBSERVED")  # S=1.25 [1.25,1.25]
        self.assertEqual(s1["variants"]["A_RESIDUAL"]["decision"]["primary_decision"], "PARITY")             # S=1.00 [1.00,1.00]
        self.assertEqual(s1["variants"]["B_MERGE"]["decision"]["primary_decision"], "NO_GO")                 # S=0.833 [0.833,0.833]
        self.assertAlmostEqual(s1["variants"]["A_STATIC"]["speedup_distribution"]["median"], 1.25, places=6)
        self.assertAlmostEqual(s1["variants"]["A_STATIC"]["speedup_distribution"]["geometric_mean"], 1.25, places=6)
        self.assertEqual(s1["variants"]["A_STATIC"]["bootstrap_95"]["resamples"], 10000)
        self.assertEqual(s1["variants"]["A_STATIC"]["bootstrap_95"]["complete_disc_blocks"], 10)

    def test_04_censoring_incomplete_pair_noise_and_variant_only_resource_regression(self) -> None:
        # 1. Incomplete pair: if 1 repeat of A_STATIC fails on a workload, that workload is excluded from primary median
        recs = _make_synthetic_eligible_ledger(
            bare_ns=10_000_000,
            variant_ns_by_variant={"A_STATIC": 8_000_000, "A_RESIDUAL": 10_000_000, "B_MERGE": 12_000_000},
        )
        # Make rep 5 of P4B_CONT_01 A_STATIC TIMEOUT, while A_RESIDUAL has 2 reps timeout -> resource regression!
        res_fail_count = 0
        for r in recs:
            if r["workload_id"] == "P4B_CONT_01" and r["variant_id"] == "A_STATIC" and r["repeat_id"] == 5:
                r["censoring_reason"] = "TIMEOUT"
                r["measurement_eligible"] = False
                r["attempt_outcome"] = "CENSORED"
                r["native_status"] = "TIMEOUT"
                r["process_exit_code"] = -9
            if r["workload_id"] == "P4B_CONT_02" and r["variant_id"] == "A_RESIDUAL" and r["repeat_id"] in (4, 5):
                r["censoring_reason"] = "WORK_BUDGET_EXCEEDED"
                r["measurement_eligible"] = False
                r["attempt_outcome"] = "CENSORED"
                r["native_status"] = "FAIL"
                r["process_exit_code"] = 4
                res_fail_count += 1
        self.assertEqual(res_fail_count, 2)

        out = stats_mod.analyze_p4b_ledger(recs, allow_synthetic_mode=True)
        # A_STATIC has 49/50 pairs (still >= 45, 29/30 cont, 20/20 disc), single timeout rep 1 (< 2 reps) excluded cleanly
        self.assertEqual(out["variants"]["A_STATIC"]["censoring_table"]["both_complete"], 49)
        self.assertEqual(out["variants"]["A_STATIC"]["censoring_table"]["bare_complete_variant_incomplete"], 1)
        # A_RESIDUAL has 2 reps resource failure on P4B_CONT_02 while BARE completed 5/5 -> Priority 3 NO_GO
        self.assertEqual(out["variants"]["A_RESIDUAL"]["decision"]["primary_decision"], "NO_GO")
        self.assertEqual(out["variants"]["A_RESIDUAL"]["decision"]["reason_code"], "RESOURCE_FEASIBILITY_REGRESSION")

        # 2. Missing RSS invalidates B_MERGE via Priority 2
        recs2 = _make_synthetic_eligible_ledger()
        for r in recs2:
            if r["workload_id"] == "DISC_01_K0" and r["variant_id"] == "B_MERGE" and r["repeat_id"] == 1:
                r["peak_rss_bytes"] = None
                r["censoring_reason"] = "MEASUREMENT_ERROR_MISSING_RSS"
        out2 = stats_mod.analyze_p4b_ledger(recs2, allow_synthetic_mode=True)
        self.assertEqual(out2["variants"]["B_MERGE"]["decision"]["primary_decision"], "INCONCLUSIVE")
        self.assertEqual(out2["variants"]["B_MERGE"]["decision"]["reason_code"], "EVIDENCE_INTEGRITY_OR_MEASUREMENT_INCOMPLETE")

    def test_05_schedule_integrity_tamper_rejections(self) -> None:
        base = _make_synthetic_eligible_ledger()

        # Missing planned slot
        with self.assertRaises(p4b_runner.LedgerIntegrityError):
            stats_mod.analyze_p4b_ledger(base[:-1], allow_synthetic_mode=True)

        # Unplanned extra slot
        extra = copy.deepcopy(base[-1])
        extra["slot_id"] = "PRM_EXTRA_FAKE"
        with self.assertRaises(p4b_runner.LedgerIntegrityError):
            stats_mod.analyze_p4b_ledger(base + [extra], allow_synthetic_mode=True)

        # Duplicate eligible attempt
        dup = copy.deepcopy(base[0])
        with self.assertRaises(p4b_runner.LedgerIntegrityError):
            stats_mod.analyze_p4b_ledger(base + [dup], allow_synthetic_mode=True)

        # Incorrect workload hash
        bad_hash = copy.deepcopy(base)
        bad_hash[0]["workload_sha256"] = "0" * 64
        with self.assertRaises(p4b_runner.LedgerIntegrityError):
            stats_mod.analyze_p4b_ledger(bad_hash, allow_synthetic_mode=True)

        # Unknown workload id
        bad_wl = copy.deepcopy(base)
        bad_wl[0]["workload_id"] = "UNKNOWN_WL"
        with self.assertRaises(p4b_runner.LedgerIntegrityError):
            stats_mod.analyze_p4b_ledger(bad_wl, allow_synthetic_mode=True)

        # Configuration mismatch on primary slot forces CONFIGURATION_UNAVAILABLE exclusion
        cfg_bad = copy.deepcopy(base)
        for r in cfg_bad:
            if r["workload_id"] == "P4B_CONT_03" and r["variant_id"] == "A_STATIC":
                r["effective_nlf_mode"] = "none"
                r["configuration_status"] = "REQUESTED_CONFIGURATION_UNAVAILABLE"
                r["eligible_for_requested_variant"] = False
        cfg_res = stats_mod.analyze_p4b_ledger(cfg_bad, allow_synthetic_mode=True)
        self.assertEqual(cfg_res["variants"]["A_STATIC"]["censoring_table"]["bare_complete_variant_incomplete"], 1)

    def test_06_bootstrap_block_dependence_and_noise_pseudo_replication(self) -> None:
        # 1. If one DISC group has half k=0/k=1 missing, complete_disc_blocks decreases by 1
        disc_blocks = [(1.2, 1.3)] * 9
        b1 = stats_mod.stratified_block_bootstrap_median([1.1] * 28, disc_blocks, resamples=1000, seed=380302)
        b2 = stats_mod.stratified_block_bootstrap_median([1.1] * 28, disc_blocks, resamples=1000, seed=380302)
        self.assertEqual(b1, b2)
        self.assertEqual(b1["continuity_units"], 28)
        self.assertEqual(b1["complete_disc_blocks"], 9)

        # 2. Noise rule: J = max/min > 1.50 on >= 20% (10/50) of eligible pairs forces Priority 4 INCONCLUSIVE
        noisy_recs = _make_synthetic_eligible_ledger(
            bare_ns=10_000_000,
            variant_ns_by_variant={"A_STATIC": 8_000_000, "A_RESIDUAL": 10_000_000, "B_MERGE": 12_000_000},
        )
        noisy_targets = {f"P4B_CONT_{i:02d}" for i in range(1, 11)}
        for r in noisy_recs:
            if r["workload_id"] in noisy_targets and r["variant_id"] == "A_STATIC" and r["repeat_id"] == 5:
                r["streaming_loop_wall_ns"] = 13_000_000  # J = 13/8 = 1.625 > 1.50, while median stays 8M!
        noisy_out = stats_mod.analyze_p4b_ledger(noisy_recs, allow_synthetic_mode=True)
        self.assertEqual(noisy_out["variants"]["A_STATIC"]["coverage"]["noisy_pairs"], 10)
        self.assertEqual(noisy_out["variants"]["A_STATIC"]["decision"]["primary_decision"], "INCONCLUSIVE")
        self.assertEqual(noisy_out["variants"]["A_STATIC"]["decision"]["priority_triggered"], 4)

        # 3. Anti-pseudo-replication: workloads are weighted equally (not by extra retries)
        # Even if a workload has extra INELIGIBLE failed attempt rows alongside its 5 eligible reps, weight stays 1
        pseudo_recs = _make_synthetic_eligible_ledger(
            bare_ns=10_000_000,
            variant_ns_by_variant={"A_STATIC": 8_000_000, "A_RESIDUAL": 10_000_000, "B_MERGE": 12_000_000},
        )
        target = next(r for r in pseudo_recs if r["workload_id"] == "P4B_CONT_01"
                      and r["variant_id"] == "A_STATIC" and r["repeat_id"] == 1)
        prelaunch = dict(target, attempt_outcome="NOT_LAUNCHED", prelaunch_verified=True,
                         raw_output_sha256=None, process_exit_code=None, correctness_status=None)
        target.update(attempt_number=2, attempt_id=f"{target['slot_id']}/2", previous_attempt_id=prelaunch["attempt_id"])
        pseudo_recs.insert(0, prelaunch)
        p_out = stats_mod.analyze_p4b_ledger(pseudo_recs, allow_synthetic_mode=True)
        self.assertEqual(p_out["variants"]["A_STATIC"]["coverage"]["overall"], 50)
        self.assertEqual(p_out["variants"]["A_STATIC"]["bootstrap_95"]["continuity_units"], 30)


if __name__ == "__main__":
    unittest.main()
