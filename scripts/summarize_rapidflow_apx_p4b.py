#!/usr/bin/env python3
"""
scripts/summarize_rapidflow_apx_p4b.py — Statistical Analysis & Mutually Exclusive Decision Engine (Phase P4b-3A).

Implements AUD-P4B-001 closed protocol:
- Workload as strict experimental unit (complete 5-of-5 repeated pair admission)
- Median within-workload aggregation; speedup S = median(BARE) / median(Variant)
- Across-workload primary median speedup + secondary geometric mean
- Stratified block bootstrap (10,000 resamples, seed=380302, keeping DISC k=0/k=1 group pairs intact)
- Unadjusted 95% intervals and Holm adjustment of supplemental bootstrap p-values
- Coverage (>=90% overall/suite, >=9/10 complete DISC blocks), noise (J>1.50, <20% noisy pairs),
  and worst-workload peak RSS (<= +20%) enforcement
- 11-priority mutually exclusive decision classifier & conditional A+B trigger evaluation
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_p4b import (
    ALL_VARIANTS,
    BOOTSTRAP_SEED,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROTOCOL_PATH,
    DEFAULT_SCHEDULE_PATH,
    PRIMARY_VARIANTS,
    PROTOCOL_VERSION,
    LedgerIntegrityError,
    resolve_p4b_ledger_records,
    EVIDENCE_ERROR_REASONS, RESOURCE_CENSORS, SYNTHETIC_BINARY, SYNTHETIC_EXPERIMENT,
    SYNTHETIC_KIND, SYNTHETIC_SOURCE, is_valid_hex64, load_frozen_plan, load_json_bytes,
    slot_identity, validate_slot_identity,
)

# Mutually exclusive primary decisions
DECISION_UNSOUND = "UNSOUND"
DECISION_INCONCLUSIVE = "INCONCLUSIVE"
DECISION_NO_GO = "NO_GO"
DECISION_QUERY_DEPENDENT = "QUERY_DEPENDENT"
DECISION_ADVANTAGE_OBSERVED = "ADVANTAGE_OBSERVED"
DECISION_PARITY = "PARITY"
DECISION_MARGINAL = "MARGINAL"

VALID_DECISIONS = frozenset({
    DECISION_UNSOUND,
    DECISION_INCONCLUSIVE,
    DECISION_NO_GO,
    DECISION_QUERY_DEPENDENT,
    DECISION_ADVANTAGE_OBSERVED,
    DECISION_PARITY,
    DECISION_MARGINAL,
})


def linear_percentile(sorted_values: Sequence[float], q: float) -> float:
    """Compute quantile q in [0, 1] using linear interpolation on a non-empty pre-sorted list."""
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty sample")
    if math.isnan(q) or math.isinf(q) or q < 0.0 or q > 1.0:
        raise ValueError(f"Percentile q must lie in [0, 1], got {q!r}")
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    w = pos - lo
    return float(sorted_values[lo]) * (1.0 - w) + float(sorted_values[hi]) * w


def validate_positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite positive number, got {value!r}")
    f = float(value)
    if math.isnan(f) or math.isinf(f) or f <= 0.0:
        raise ValueError(f"{name} must be finite positive number, got {value!r}")
    return f


def validate_finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite number, got {value!r}")
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        raise ValueError(f"{name} must be finite number, got {value!r}")
    return f


def classify_variant_decision(
    *,
    speedup_median: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    semantic_mismatch: bool = False,
    evidence_integrity_ok: bool = True,
    missing_rss: bool = False,
    variant_only_resource_regression: bool = False,
    overall_coverage_ok: bool = True,
    continuity_coverage_ok: bool = True,
    disc_coverage_ok: bool = True,
    complete_disc_blocks_ok: bool = True,
    excessive_noise: bool = False,
    worst_rss_overhead: Optional[float] = 0.0,
    eligible_strata_results: Optional[Sequence[Mapping[str, Any]]] = None,
    rss_limit_exceeded: Optional[bool] = None,
    thresholds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """
    Deterministic, mutually exclusive 11-priority decision classifier (Section XXXIII).
    Rejects non-finite, non-positive or inverted bounds. Percentile CIs may exclude the point.
    """
    th = {
        "advantage_min_speedup": 1.15,
        "parity_lower": 0.95,
        "parity_upper": 1.05,
        "marginal_lower": 1.05,
        "marginal_upper": 1.15,
        "regression_upper": 0.95,
        "null_effect_boundary": 1.00,
        "max_rss_overhead": 0.20,
        "conditional_ab_min_speedup": 1.05,
        "conditional_ab_requires_ci_lower_gt": 1.0,
    }
    if thresholds:
        th.update(thresholds)

    # Strict validation of numeric metrics whenever provided
    s_val: Optional[float] = None
    l_val: Optional[float] = None
    u_val: Optional[float] = None
    if speedup_median is not None or ci_lower is not None or ci_upper is not None:
        if speedup_median is None or ci_lower is None or ci_upper is None:
            if evidence_integrity_ok and not semantic_mismatch:
                raise ValueError("Partial confidence interval supplied while evidence_integrity_ok=True")
        else:
            s_val = validate_positive_finite(speedup_median, "speedup_median")
            l_val = validate_positive_finite(ci_lower, "ci_lower")
            u_val = validate_positive_finite(ci_upper, "ci_upper")
            if l_val > u_val:
                raise ValueError(f"Invalid confidence interval ordering: L={l_val} > U={u_val}")

    rss_val: Optional[float] = None
    if worst_rss_overhead is not None:
        rss_val = validate_finite_number(worst_rss_overhead, "worst_rss_overhead")
        if rss_val <= -1.0:
            raise ValueError(f"Invalid relative RSS overhead <= -1.0: {rss_val}")
    elif s_val is not None and not missing_rss and evidence_integrity_ok and not semantic_mismatch:
        raise ValueError("worst_rss_overhead cannot be None when missing_rss=False and evidence_integrity_ok=True")

    coverage_ok = bool(
        overall_coverage_ok
        and continuity_coverage_ok
        and disc_coverage_ok
        and complete_disc_blocks_ok
    )

    gate_diagnostics = {
        "semantic_mismatch": semantic_mismatch,
        "evidence_integrity_ok": evidence_integrity_ok,
        "missing_rss": missing_rss,
        "variant_only_resource_regression": variant_only_resource_regression,
        "coverage_ok": coverage_ok,
        "overall_coverage_ok": overall_coverage_ok,
        "continuity_coverage_ok": continuity_coverage_ok,
        "disc_coverage_ok": disc_coverage_ok,
        "complete_disc_blocks_ok": complete_disc_blocks_ok,
        "excessive_noise": excessive_noise,
        "worst_rss_overhead": rss_val,
        "point_estimate_outside_ci": s_val is not None and not l_val <= s_val <= u_val,
    }

    def _result(
        decision: str,
        priority: int,
        reason_code: str,
        *,
        r4b_disposition: Optional[str] = None,
        cond_ab_eligible: bool = False,
    ) -> Dict[str, Any]:
        return {
            "primary_decision": decision,
            "r4b_disposition": r4b_disposition or decision,
            "priority_triggered": priority,
            "reason_code": reason_code,
            "conditional_ab_trigger_met": cond_ab_eligible,
            "requires_written_proposal_and_coordinator_approval": True,
            "gate_diagnostics": gate_diagnostics,
        }

    # Priority 1 — UNSOUND
    if semantic_mismatch:
        return _result(DECISION_UNSOUND, 1, "SEMANTIC_DISCREPANCY")

    # Priority 2 — INCONCLUSIVE (evidence / measurement / RSS missing)
    if not evidence_integrity_ok or missing_rss:
        return _result(DECISION_INCONCLUSIVE, 2, "EVIDENCE_INTEGRITY_OR_MEASUREMENT_INCOMPLETE")

    # Priority 3 — NO_GO (variant-only resource failure)
    if variant_only_resource_regression:
        return _result(DECISION_NO_GO, 3, "RESOURCE_FEASIBILITY_REGRESSION")

    # Priority 4 — INCONCLUSIVE (insufficient coverage or excessive instability)
    if not coverage_ok or excessive_noise or s_val is None:
        return _result(DECISION_INCONCLUSIVE, 4, "INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE")

    # Priority 5 — NO_GO (workload-level memory gate > 20%)
    over_memory_limit = rss_val > th["max_rss_overhead"] if rss_limit_exceeded is None else rss_limit_exceeded
    if over_memory_limit:
        return _result(DECISION_NO_GO, 5, "MEMORY_OVERHEAD_EXCEEDS_REGISTERED_LIMIT")

    # Conditional A+B trigger check (only after Priorities 1..5 are all satisfied!)
    cond_ab = bool(s_val >= th["conditional_ab_min_speedup"] and l_val > th["conditional_ab_requires_ci_lower_gt"])

    # Priority 6 — QUERY_DEPENDENT (conflicting eligible predeclared strata)
    if eligible_strata_results:
        valid_strata = [st for st in eligible_strata_results if st.get("eligible_for_stratum_decision") is True]
        for stratum in valid_strata:
            low = validate_positive_finite(stratum["ci_lower"], "stratum_l")
            high = validate_positive_finite(stratum["ci_upper"], "stratum_u")
            if low > high:
                raise ValueError("Inverted stratum interval")
        has_adv_stratum = any(
            validate_positive_finite(st["median_speedup"], "stratum_s") >= th["advantage_min_speedup"]
            and validate_positive_finite(st["ci_lower"], "stratum_l") > th["null_effect_boundary"]
            for st in valid_strata
        )
        has_reg_stratum = any(
            validate_positive_finite(st["median_speedup"], "stratum_s") < th["regression_upper"]
            and validate_positive_finite(st["ci_upper"], "stratum_u") < th["null_effect_boundary"]
            for st in valid_strata
        )
        if has_adv_stratum and has_reg_stratum:
            return _result(DECISION_QUERY_DEPENDENT, 6, "CONFLICTING_ELIGIBLE_STRATA", cond_ab_eligible=cond_ab)

    # Priority 7 — ADVANTAGE_OBSERVED
    if s_val >= th["advantage_min_speedup"] and l_val > th["null_effect_boundary"]:
        return _result(DECISION_ADVANTAGE_OBSERVED, 7, "PRACTICAL_ADVANTAGE_SUPPORTED", cond_ab_eligible=cond_ab)

    # Priority 8 — PARITY (must take precedence over overlapping MARGINAL at S = 1.05 when CI inside [0.95, 1.05])
    if (th["parity_lower"] <= s_val <= th["parity_upper"]) and (l_val >= th["parity_lower"]) and (u_val <= th["parity_upper"]):
        return _result(DECISION_PARITY, 8, "STATISTICAL_EQUIVALENCE_WITHIN_BAND", cond_ab_eligible=cond_ab)

    # Priority 9 — NO_GO (confirmed runtime regression)
    if s_val < th["regression_upper"] and u_val < th["null_effect_boundary"]:
        return _result(DECISION_NO_GO, 9, "CONFIRMED_RUNTIME_REGRESSION", cond_ab_eligible=False)

    # Priority 10 — MARGINAL (mapped cleanly for R4b vocabulary without equating to ADVANTAGE_OBSERVED)
    if (th["marginal_lower"] <= s_val < th["marginal_upper"]) and (l_val > th["null_effect_boundary"]):
        return _result(
            DECISION_MARGINAL,
            10,
            "POSITIVE_BUT_BELOW_PRACTICAL_THRESHOLD",
            r4b_disposition="INCONCLUSIVE_FOR_PRACTICAL_ADVANTAGE",
            cond_ab_eligible=cond_ab,
        )

    # Priority 11 — Default INCONCLUSIVE
    return _result(DECISION_INCONCLUSIVE, 11, "INDETERMINATE_INTERVAL_OR_EFFECT", cond_ab_eligible=cond_ab)


def is_slot_eligible(rec, expected, workload):
    """Authenticate identity first, then classify execution separately from pairing."""
    validate_slot_identity(rec, expected)
    if expected["slot_purpose"] != "primary" or expected["execution_purpose"] != "performance":
        return False, "NON_PRIMARY_SLOT"
    if rec.get("attempt_outcome") != "COMPLETED":
        reason = rec.get("censoring_reason")
        if rec["attempt_outcome"] == "CENSORED" and reason in RESOURCE_CENSORS:
            return False, reason
        return False, "ENVIRONMENT_INTERRUPTION" if rec["attempt_outcome"] == "INTERRUPTED" else (
            reason if reason in EVIDENCE_ERROR_REASONS else rec["attempt_outcome"])
    if rec.get("censoring_reason") != "NONE":
        reason = rec.get("censoring_reason")
        return False, reason if reason in EVIDENCE_ERROR_REASONS else "PROVENANCE_ERROR"
    if rec.get("native_status") != "SUCCESS" or type(rec.get("process_exit_code")) is not int or (
        rec["process_exit_code"] != 0 or "process_signal" not in rec or rec["process_signal"] is not None
    ):
        return False, "EXECUTION_FAILURE"
    if (rec.get("profiling_enabled") is not False or rec.get("deep_diagnostics_enabled") is not False
            or type(rec.get("measurement_schema_version")) is not int or rec["measurement_schema_version"] != 1
            or type(rec.get("neighbor_configuration_version")) is not int or rec["neighbor_configuration_version"] != 2):
        return False, "SCHEMA_ERROR"
    if (rec.get("configuration_initialized") is not True or rec.get("configuration_valid") is not True
            or rec.get("configuration_status") != "SUCCESS" or rec.get("eligible_for_requested_variant") is not True
            or rec.get("effective_nlf_mode") != expected["requested_nlf_mode"]
            or rec.get("effective_neighbor_mode") != expected["requested_neighbor_mode"]):
        return False, "CONFIGURATION_IDENTITY_ERROR"
    planned = workload["update_count"]
    if (any(type(rec.get(key)) is not int or rec[key] != planned for key in ("updates_completed", "updates_planned"))
            or rec.get("results_delivered") is not True):
        return False, "INCOMPLETE_UPDATES_OR_DELIVERY"
    admitted = {"M0_VERIFIED", "DIFFERENTIAL_M2_VERIFIED", "WITNESS_VERIFIED"}
    if expected["evidence_kind"] == "CONFIRMATORY_MEASUREMENT" and expected["variant_id"] == "BARE":
        admitted.add("LIMITED_C3_BARE_CONTROL")
    if rec.get("correctness_status") not in admitted:
        return False, "CORRECTNESS_UNVERIFIED"
    digest = rec.get("raw_output_sha256")
    if not isinstance(digest, str) or not is_valid_hex64(digest):
        return False, "PROVENANCE_ERROR"
    for field, minimum in (("streaming_loop_wall_ns", 1), ("outer_wall_ns", 1), ("initialization_wall_ns", 0)):
        if type(rec.get(field)) is not int or rec[field] < minimum or rec[field] > (1 << 63) - 1:
            return False, "MEASUREMENT_ERROR"
    rss = rec.get("peak_rss_bytes")
    kib = rec.get("peak_rss_original_value")
    if (type(rss) is not int or not 0 < rss <= (1 << 63) - 1 or type(kib) is not int or rss != kib * 1024
            or rec.get("peak_rss_original_unit") != "KiB"
            or rec.get("resource_measurement_method") != "linux_proc_self_status_vmhwm"):
        return False, "MEASUREMENT_ERROR_MISSING_RSS"
    return True, "ELIGIBLE"


def stratified_block_bootstrap_median(
    continuity_speedups: Sequence[float],
    complete_disc_group_speedups: Sequence[Sequence[float]],
    *,
    resamples: int = 10000,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
    sensitivity_family_size: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Stratified block bootstrap over workload units (Section XXIX):
    - Continuity suite: resampled with replacement at workload level
    - Discrimination suite: resampled with replacement by complete DISC group (keeping matched k=0/k=1 block intact)
    Maintains suite proportions without inflating incomplete repetitions.
    """
    n_cont = len(continuity_speedups)
    n_disc_blocks = len(complete_disc_group_speedups)
    if n_cont == 0 and n_disc_blocks == 0:
        raise ValueError("Cannot run block bootstrap with zero eligible units")
    if type(seed) is not int or type(resamples) is not int or not 0 < validate_positive_finite(alpha, "alpha") < 1:
        raise ValueError("Invalid bootstrap parameters")
    for value in continuity_speedups:
        validate_positive_finite(value, "continuity speedup")
    for block in complete_disc_group_speedups:
        if len(block) not in (1, 2):
            raise ValueError("Invalid DISC contribution size")
        for value in block:
            validate_positive_finite(value, "DISC speedup")
    if resamples < 100:
        raise ValueError(f"Bootstrap resamples must be >= 100, got {resamples}")

    rng = random.Random(seed)
    boot_medians: List[float] = []
    for _ in range(resamples):
        sample: List[float] = []
        if n_cont > 0:
            for _ in range(n_cont):
                sample.append(float(continuity_speedups[rng.randrange(n_cont)]))
        if n_disc_blocks > 0:
            for _ in range(n_disc_blocks):
                pair = complete_disc_group_speedups[rng.randrange(n_disc_blocks)]
                sample.extend(pair)
        sample.sort()
        boot_medians.append(linear_percentile(sample, 0.50))

    boot_medians.sort()
    lo_q = alpha / 2.0
    hi_q = 1.0 - alpha / 2.0
    result = {
        "bootstrap_seed": seed,
        "resamples": resamples,
        "continuity_units": n_cont,
        "complete_disc_blocks": n_disc_blocks,
        "alpha": alpha,
        "ci_lower": linear_percentile(boot_medians, lo_q),
        "ci_upper": linear_percentile(boot_medians, hi_q),
        "bootstrap_median_of_medians": linear_percentile(boot_medians, 0.50),
    }

    if sensitivity_family_size is not None:
        adjusted_q = alpha / (2 * sensitivity_family_size)
        result["bonferroni_interval"] = {
            "alpha": alpha / sensitivity_family_size,
            "ci_lower": linear_percentile(boot_medians, adjusted_q),
            "ci_upper": linear_percentile(boot_medians, 1 - adjusted_q),
        }
        point = statistics.median([*continuity_speedups, *(v for block in complete_disc_group_speedups for v in block)])
        # Center the bootstrap distribution at the observed median; use logs
        # because speedup is a ratio and the null is one.
        extreme = sum(abs(math.log(x) - math.log(point)) >= abs(math.log(point)) for x in boot_medians)
        result["centered_log_bootstrap_p"] = (1 + extreme) / (1 + resamples)
    return result


def _holm(p_values, family_size):
    # Keep the preregistered family size even if one contrast has no valid estimate.
    adjusted = {}
    previous = 0.0
    for index, (name, p) in enumerate(sorted(p_values.items(), key=lambda item: (item[1], item[0]))):
        previous = min(1.0, max(previous, (family_size - index) * p))
        adjusted[name] = previous
    return adjusted


def _stratum_registry(workloads, protocol):
    members = defaultdict(set)
    for wid, w in workloads.items():
        k, size = w["missing_budget_k"], w["data_vertex_count"]
        deletes, updates = w["delete_count"], w["update_count"]
        keys = (
            w["suite"], w["label_regime"],
            "small_le_16" if size <= 16 else "medium_17_to_64" if size <= 64 else "large_ge_65_descriptive_only",
            "insert_only" if deletes == 0 else "has_deletes",
            "high_churn_del_gt_25pct" if 4 * deletes > updates else "low_churn_del_le_25pct",
            "k2_descriptive_only" if k == 2 else f"k{k}",
        )
        for key in keys + (("k_ge_1",) if k >= 1 else ()):
            members[key].add(wid)
    expected = {name: count for group in protocol["predeclared_strata"].values() for name, count in group.items()}
    if {key: len(ids) for key, ids in members.items()} != expected:
        raise LedgerIntegrityError("FROZEN_STRATUM_REGISTRY_MISMATCH")
    return members


def _stratum_reports(registry, cont, blocks, minimum, resamples, seed, alpha, *, conservative=False, disqualified=()):
    reports = []
    for name, nominal in sorted(registry.items()):
        c = [p["speedup"] for p in cont if p["workload_id"] in nominal]
        d = [[p["speedup"] for p in block if p["workload_id"] in nominal] for block in blocks]
        d = [block for block in d if block]
        observations = c + [v for block in d for v in block]
        eligible = len(observations) >= minimum and not name.endswith("_descriptive_only")
        if conservative:
            eligible = eligible and len(c) + len(d) >= 5 and 10 * len(observations) >= 9 * len(nominal)
            eligible = eligible and not (nominal & set(disqualified))
        report = {
            "name": name, "planned": len(nominal), "observed": len(observations),
            "coverage_fraction": len(observations) / len(nominal),
            "resampling_units": len(c) + len(d),
            "continuity_units": len(c), "disc_blocks": len(d), "eligible_for_stratum_decision": eligible,
            "median_speedup": statistics.median(observations) if observations else None,
        }
        if eligible:
            report.update(stratified_block_bootstrap_median(c, d, resamples=resamples, seed=seed, alpha=alpha))
        reports.append(report)
    return reports




def analyze_p4b_ledger(
    ledger_records: Sequence[Mapping[str, Any]],
    *,
    protocol_path: Path = DEFAULT_PROTOCOL_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    allow_synthetic_mode: bool = False,
) -> Dict[str, Any]:
    """
    Full statistical analysis & fail-closed validation pipeline over P4b ledger records.
    During P4b-3A verification tests, allow_synthetic_mode=True lets synthetic JSONL fixtures run
    explicitly labeled under synthetic test mode while preserving exact schedule/integrity enforcement.
    """
    protocol, manifest, schedule = load_frozen_plan(protocol_path, manifest_path, schedule_path)
    if allow_synthetic_mode is not True:
        raise LedgerIntegrityError("CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED")

    return _analyze_observations(
        ledger_records, protocol, manifest, schedule, _synthetic_identity, "SYNTHETIC_FIXTURE_VERIFICATION")


def _synthetic_identity(slot, protocol):
    return slot_identity(slot, protocol, experiment_id=SYNTHETIC_EXPERIMENT, evidence_kind=SYNTHETIC_KIND,
                         binary_sha=SYNTHETIC_BINARY, source_revision=SYNTHETIC_SOURCE)


def _analyze_reviewed_records(outcome, protocol, manifest, schedule, context):
    """Called by production_command after locked, independent raw-evidence ingestion."""
    context.recheck()
    records = outcome["records"]
    if outcome["state"] != "SESSION_COMPLETED":
        return {"state": outcome["state"], "validated_attempts": len(records),
                "confirmatory_analysis": None, "incident": outcome.get("incident")}
    if any(r.get("activation_sha256") != context.digest for r in records):
        raise LedgerIntegrityError("UNTRUSTED_ACTIVATION_PROVENANCE")

    def production_identity(slot, protocol):
        from scripts.run_rapidflow_apx_p4b import NATIVE_SHA256
        return slot_identity(slot, protocol, experiment_id=context.experiment_id,
                             evidence_kind="CONFIRMATORY_MEASUREMENT", binary_sha=NATIVE_SHA256,
                             source_revision=context.revision)

    report = _analyze_observations(
        records, protocol, manifest, schedule, production_identity, "CONFIRMATORY_ANALYSIS",
        same_category=context.resource_regression_rule == "SAME_CATEGORY",
        conservative_strata=context.stratum_rule == "CONSERVATIVE_3B0",
    )
    from scripts.p4b_experiment_session import summarize_diagnostics
    report["diagnostic_attribution"] = summarize_diagnostics(records)
    return report


def _analyze_observations(ledger_records, protocol, manifest, schedule, expected_identity, analysis_mode,
                          *, same_category=False, conservative_strata=False):
    w_by_id = {w["workload_id"]: w for w in manifest["workloads"]}
    registry = _stratum_registry(w_by_id, protocol)
    planned_primary = {s["slot_id"]: s for s in schedule["primary_slots"]}
    planned_all = {s["slot_id"]: s for key in ("primary_slots", "warmup_slots", "diagnostic_slots")
                   for s in schedule[key]}
    for rec in ledger_records:
        if not isinstance(rec, Mapping) or not isinstance(rec.get("slot_id"), str):
            raise LedgerIntegrityError("INVALID_LEDGER_RECORD")
        plan = planned_all.get(rec["slot_id"])
        if plan is None:
            raise LedgerIntegrityError(f"UNPLANNED_SLOT: {rec['slot_id']}")
        expected = expected_identity(plan, protocol)
        validate_slot_identity(rec, expected)
        if rec.get("synthetic_test_fixture") is not (analysis_mode == "SYNTHETIC_FIXTURE_VERIFICATION"):
            raise LedgerIntegrityError("EVIDENCE_ORIGIN_MISMATCH")
    resolved = resolve_p4b_ledger_records(ledger_records)
    canon_slots = resolved["canonical_slots"]
    missing = planned_primary.keys() - canon_slots.keys()
    if missing:
        raise LedgerIntegrityError(f"MISSING_PRIMARY_SLOT: {len(missing)} absent; first {min(missing)}")

    semantic_mismatch_variants = set()
    integrity_error_variants = set()
    missing_rss_variants = set()
    elig_reps = defaultdict(dict)
    censor_by_cell = defaultdict(Counter)
    ineligible_reasons = defaultdict(Counter)
    semantic_failure_codes = {"TUPLE_MISMATCH", "DUPLICATE_EMISSION", "STEP_COUNT_MISMATCH",
                              "GRAPH_STATE_MISMATCH", "SEMANTIC_UNSOUND"}
    for sid, history in resolved["all_attempts_by_slot"].items():
        plan = planned_all[sid]
        if any(rec.get("correctness_status") in semantic_failure_codes for rec in history):
            semantic_mismatch_variants.add(plan["variant_id"])

    for sid, plan in planned_primary.items():
        rec = canon_slots[sid]
        wid, var_id, rep = (plan[key] for key in ("workload_id", "variant_id", "repeat_id"))
        expected = expected_identity(plan, protocol)
        ok, reason = is_slot_eligible(rec, expected, w_by_id[wid])
        if ok:
            elig_reps[(wid, var_id)][rep] = rec
        else:
            ineligible_reasons[var_id][reason] += 1
            if reason in RESOURCE_CENSORS:
                censor_by_cell[(wid, var_id)][reason] += 1
            elif reason == "MEASUREMENT_ERROR_MISSING_RSS":
                missing_rss_variants.add(var_id)
            else:
                integrity_error_variants.add(var_id)

    policy = protocol["statistical_policy"]
    coverage = protocol["coverage_and_noise_policy"]
    repeats = protocol["repetitions"]["primary_proposed_r"]
    resamples = policy["bootstrap_resamples"]
    b_seed = protocol["seeds"]["bootstrap_seed"]
    alpha = 2 * policy["ci_percentiles"][0]
    rs_cfg = protocol["decision_thresholds"]
    family_size = len(PRIMARY_VARIANTS) - 1

    variant_reports: Dict[str, Any] = {}
    bare_complete_set = {wid for wid in w_by_id if len(elig_reps[(wid, "BARE")]) == repeats}

    for var_id in ("A_STATIC", "A_RESIDUAL", "B_MERGE"):
        both_complete = 0
        bare_only = 0
        var_only = 0
        both_incomplete = 0
        variant_only_resource_reg = False
        per_workload_pairs: List[Dict[str, Any]] = []
        noisy_pair_count = 0

        for wid, wmeta in sorted(w_by_id.items()):
            b_reps = elig_reps[(wid, "BARE")]
            v_reps = elig_reps[(wid, var_id)]
            b_ok = (len(b_reps) == repeats)
            v_ok = (len(v_reps) == repeats)

            if b_ok and v_ok:
                both_complete += 1
            elif b_ok and not v_ok:
                bare_only += 1
                failures = censor_by_cell[(wid, var_id)]
                repeated = max(failures.values(), default=0) if same_category else sum(failures.values())
                if repeated >= coverage["variant_only_resource_regression_min_reps"]:
                    variant_only_resource_reg = True
                continue
            elif (not b_ok) and v_ok:
                var_only += 1
                continue
            else:
                both_incomplete += 1
                continue

            # Strict 5-of-5 aggregation
            t_b = [b_reps[r]["streaming_loop_wall_ns"] for r in range(1, repeats + 1)]
            t_v = [v_reps[r]["streaming_loop_wall_ns"] for r in range(1, repeats + 1)]
            m_b = [b_reps[r]["peak_rss_bytes"] for r in range(1, repeats + 1)]
            m_v = [v_reps[r]["peak_rss_bytes"] for r in range(1, repeats + 1)]
            o_b = [float(b_reps[r]["outer_wall_ns"]) for r in range(1, repeats + 1)]
            o_v = [float(v_reps[r]["outer_wall_ns"]) for r in range(1, repeats + 1)]
            i_b = [float(b_reps[r]["initialization_wall_ns"]) for r in range(1, repeats + 1)]
            i_v = [float(v_reps[r]["initialization_wall_ns"]) for r in range(1, repeats + 1)]

            med_tb = statistics.median(t_b)
            med_tv = statistics.median(t_v)
            med_mb = statistics.median(m_b)
            med_mv = statistics.median(m_v)
            speedup_i = med_tb / med_tv
            rss_ovh_i = (med_mv / med_mb) - 1.0
            j_b = max(t_b) / min(t_b)
            j_v = max(t_v) / min(t_v)
            jitter_limit = Fraction(str(coverage["cell_jitter_flag_threshold"]))
            is_noisy = max(Fraction(max(t_b), min(t_b)), Fraction(max(t_v), min(t_v))) > jitter_limit
            exceeds_rss = Fraction(med_mv, med_mb) - 1 > Fraction(str(rs_cfg["max_rss_overhead"]))
            if is_noisy:
                noisy_pair_count += 1

            per_workload_pairs.append({
                "workload_id": wid,
                "suite": wmeta["suite"],
                "group_id": wmeta["group_id"],
                "k": wmeta["missing_budget_k"],
                "label_regime": wmeta["label_regime"],
                "data_vertex_count": wmeta["data_vertex_count"],
                "bare_median_ns": med_tb,
                "variant_median_ns": med_tv,
                "abs_time_saved_ns": med_tb - med_tv,
                "bare_outer_median_ns": statistics.median(o_b),
                "variant_outer_median_ns": statistics.median(o_v),
                "bare_init_median_ns": statistics.median(i_b),
                "variant_init_median_ns": statistics.median(i_v),
                "speedup": speedup_i,
                "bare_median_rss_bytes": med_mb,
                "variant_median_rss_bytes": med_mv,
                "rss_overhead": rss_ovh_i,
                "rss_limit_exceeded": exceeds_rss,
                "max_observed_rss_bytes": max(m_v),
                "jitter_bare": j_b,
                "jitter_variant": j_v,
                "noisy_flagged": is_noisy,
            })

        cont_pairs = [p for p in per_workload_pairs if p["suite"] == "continuity"]
        disc_pairs = [p for p in per_workload_pairs if p["suite"] == "discrimination"]

        disc_by_group = defaultdict(dict)
        for pair in disc_pairs:
            disc_by_group[pair["group_id"]][pair["k"]] = pair
        complete_disc_blocks = [(kv[0], kv[1]) for _, kv in sorted(disc_by_group.items()) if set(kv) == {0, 1}]
        primary_pairs = cont_pairs + [p for block in complete_disc_blocks for p in block]
        primary_ids = {p["workload_id"] for p in primary_pairs}
        nominal_cont = len(registry["continuity"])
        nominal_disc = len(registry["discrimination"])

        def reaches(count, nominal, threshold):
            return Fraction(count, nominal) >= Fraction(str(coverage[threshold]))

        overall_cov_ok = reaches(len(primary_pairs), len(w_by_id), "min_overall_eligible_fraction")
        cont_cov_ok = reaches(len(cont_pairs), nominal_cont, "min_continuity_eligible_fraction")
        disc_cov_ok = reaches(2 * len(complete_disc_blocks), nominal_disc, "min_discrimination_eligible_fraction")
        disc_blocks_ok = len(complete_disc_blocks) >= coverage["min_complete_disc_blocks"]
        noisy_pair_count = sum(p["noisy_flagged"] for p in primary_pairs)
        excessive_noise = bool(primary_pairs) and (
            Fraction(noisy_pair_count, len(primary_pairs)) >= Fraction(str(coverage["max_noisy_pair_fraction_exclusive"]))
        )

        speedups = sorted(p["speedup"] for p in primary_pairs)
        rss_overheads = [p["rss_overhead"] for p in per_workload_pairs]
        if speedups:
            med_s = statistics.median(speedups)
            boot = stratified_block_bootstrap_median(
                [p["speedup"] for p in cont_pairs],
                [[p["speedup"] for p in block] for block in complete_disc_blocks],
                resamples=resamples, seed=b_seed, alpha=alpha, sensitivity_family_size=family_size,
            )
            ci_l, ci_u = boot["ci_lower"], boot["ci_upper"]
            dist = {
                "min": speedups[0], "q25": linear_percentile(speedups, 0.25), "median": med_s,
                "q75": linear_percentile(speedups, 0.75), "max": speedups[-1],
                "geometric_mean": math.exp(math.fsum(math.log(x) for x in speedups) / len(speedups)),
                "eligible_pairs": len(primary_pairs), "excluded_pairs": len(w_by_id) - len(primary_pairs),
                "estimator_status": "AVAILABLE",
            }
        else:
            med_s = ci_l = ci_u = boot = None
            dist = {"eligible_pairs": 0, "excluded_pairs": len(w_by_id), "estimator_status": "UNAVAILABLE",
                    "median": None, "geometric_mean": None}
        disqualified = {p["workload_id"] for p in per_workload_pairs
                        if p["noisy_flagged"] or p["rss_limit_exceeded"]}
        disqualified.update(wid for wid in w_by_id if censor_by_cell[(wid, var_id)] or censor_by_cell[(wid, "BARE")])
        strata = _stratum_reports(registry, cont_pairs, complete_disc_blocks,
                                 coverage["min_stratum_eligible_count"], resamples, b_seed, alpha,
                                 conservative=conservative_strata, disqualified=disqualified)
        mem_summary = {
            "median_rss_overhead": linear_percentile(sorted(rss_overheads), 0.50) if rss_overheads else None,
            "worst_workload_rss_overhead": max(rss_overheads) if rss_overheads else None,
            "max_observed_rss_bytes": max((p["max_observed_rss_bytes"] for p in per_workload_pairs), default=None),
            "workloads_exceeding_20pct": sum(p["rss_limit_exceeded"] for p in per_workload_pairs),
        }

        decision = classify_variant_decision(
            speedup_median=med_s,
            ci_lower=ci_l,
            ci_upper=ci_u,
            semantic_mismatch=(var_id in semantic_mismatch_variants),
            evidence_integrity_ok=not ({var_id, "BARE"} & integrity_error_variants)
            and "BARE" not in semantic_mismatch_variants,
            missing_rss=bool({var_id, "BARE"} & missing_rss_variants),
            variant_only_resource_regression=variant_only_resource_reg,
            overall_coverage_ok=overall_cov_ok,
            continuity_coverage_ok=cont_cov_ok,
            disc_coverage_ok=disc_cov_ok,
            complete_disc_blocks_ok=disc_blocks_ok,
            excessive_noise=excessive_noise,
            worst_rss_overhead=mem_summary["worst_workload_rss_overhead"],
            thresholds=rs_cfg, eligible_strata_results=strata,
            rss_limit_exceeded=bool(mem_summary["workloads_exceeding_20pct"]),
        )

        variant_reports[var_id] = {
            "censoring_table": {
                "both_complete": both_complete,
                "bare_complete_variant_incomplete": bare_only,
                "bare_incomplete_variant_complete": var_only,
                "both_incomplete": both_incomplete,
            },
            "coverage": {
                "overall": len(primary_pairs),
                "all_complete_timing_pairs": len(per_workload_pairs),
                "continuity": len(cont_pairs),
                "discrimination": 2 * len(complete_disc_blocks),
                "complete_disc_blocks": len(complete_disc_blocks),
                "noisy_pairs": noisy_pair_count,
            },
            "speedup_distribution": dist,
            "bootstrap_95": boot,
            "strata": strata,
            "bootstrap_bonferroni_sensitivity": boot["bonferroni_interval"] if boot else None,
            "primary_workload_ids": sorted(primary_ids),
            "descriptive_only_workloads": sorted(p["workload_id"] for p in per_workload_pairs if p["workload_id"] not in primary_ids),
            "workload_pairs": per_workload_pairs,
            "ineligible_slot_reasons": {
                "BARE": dict(ineligible_reasons["BARE"]), var_id: dict(ineligible_reasons[var_id]),
            },
            "memory_summary": mem_summary,
            "decision": decision,
        }

    sensitivity = _holm({
        name: report["bootstrap_95"]["centered_log_bootstrap_p"]
        for name, report in variant_reports.items()
        if report["bootstrap_95"] is not None and report["decision"]["priority_triggered"] > 4
    }, family_size)
    return {
        "analysis_mode": analysis_mode,
        "holm_sensitivity": {
            "method": "Holm adjusted centered log bootstrap p-values",
            "family_size": family_size, "adjusted_p_values": sensitivity,
        },
        "protocol_version": protocol.get("protocol_version", PROTOCOL_VERSION),
        "manifest_workloads": len(w_by_id),
        "attempt_accounting": {
            "raw_attempts": resolved["total_raw_records"],
            "unique_slots": len(canon_slots),
            "slots_with_prelaunch_attempt_history": len(resolved["duplicate_attempts"]),
        },
        "bare_complete_workloads": len(bare_complete_set),
        "variants": variant_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RapidFlow-APX P4b Statistical Analyzer")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_PATH)
    parser.add_argument("--synthetic", action="store_true", help="Explicitly label analysis over synthetic fixtures")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    records = []
    with args.ledger.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if line:
                records.append(load_json_bytes(line.encode("utf-8")))
    summary = analyze_p4b_ledger(
        records,
        protocol_path=args.protocol,
        manifest_path=args.manifest,
        schedule_path=args.schedule,
        allow_synthetic_mode=args.synthetic,
    )
    txt = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.output:
        args.output.write_text(txt + "\n", encoding="utf-8")
    else:
        print(txt)


if __name__ == "__main__":
    main()
