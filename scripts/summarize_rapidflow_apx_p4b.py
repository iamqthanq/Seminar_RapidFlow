#!/usr/bin/env python3
"""
scripts/summarize_rapidflow_apx_p4b.py — Statistical Analysis & Mutually Exclusive Decision Engine (Phase P4b-3A).

Implements AUD-P4B-001 closed protocol:
- Workload as strict experimental unit (complete 5-of-5 repeated pair admission)
- Median within-workload aggregation; speedup S = median(BARE) / median(Variant)
- Across-workload primary median speedup + secondary geometric mean
- Stratified block bootstrap (10,000 resamples, seed=380302, keeping DISC k=0/k=1 group pairs intact)
- Unadjusted 95% intervals + supplementary Holm-Bonferroni sensitivity bounds
- Coverage (>=90% overall/suite, >=9/10 complete DISC blocks), noise (J>1.50, <20% noisy pairs),
  and worst-workload peak RSS (<= +20%) enforcement
- 11-priority mutually exclusive decision classifier & conditional A+B trigger evaluation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_p4b import (
    ALL_VARIANTS,
    BOOTSTRAP_SEED,
    CANONICAL_REPETITIONS,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROTOCOL_PATH,
    DEFAULT_SCHEDULE_PATH,
    PRIMARY_VARIANTS,
    PROTOCOL_VERSION,
    LedgerIntegrityError,
    resolve_p4b_ledger_records,
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
    thresholds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """
    Deterministic, mutually exclusive 11-priority decision classifier (Section XXXIII).
    Rejects invalid numeric inputs (NaN, Inf, non-positive speedup/bounds, point outside CI).
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
            if s_val < l_val - 1e-12 or s_val > u_val + 1e-12:
                raise ValueError(f"Point estimate S={s_val} outside reported interval [{l_val}, {u_val}]")

    rss_val: Optional[float] = None
    if worst_rss_overhead is not None:
        rss_val = validate_finite_number(worst_rss_overhead, "worst_rss_overhead")
        if rss_val <= -1.0:
            raise ValueError(f"Invalid relative RSS overhead <= -1.0: {rss_val}")
    elif not missing_rss and evidence_integrity_ok and not semantic_mismatch:
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
    if (not evidence_integrity_ok) or missing_rss or s_val is None or l_val is None or u_val is None:
        return _result(DECISION_INCONCLUSIVE, 2, "EVIDENCE_INTEGRITY_OR_MEASUREMENT_INCOMPLETE")

    # Priority 3 — NO_GO (variant-only resource failure)
    if variant_only_resource_regression:
        return _result(DECISION_NO_GO, 3, "RESOURCE_FEASIBILITY_REGRESSION")

    # Priority 4 — INCONCLUSIVE (insufficient coverage or excessive instability)
    if (not coverage_ok) or excessive_noise:
        return _result(DECISION_INCONCLUSIVE, 4, "INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE")

    # Priority 5 — NO_GO (workload-level memory gate > 20%)
    if rss_val is None or rss_val > th["max_rss_overhead"]:
        return _result(DECISION_NO_GO, 5, "MEMORY_OVERHEAD_EXCEEDS_REGISTERED_LIMIT")

    # Conditional A+B trigger check (only after Priorities 1..5 are all satisfied!)
    cond_ab = bool(s_val >= th["conditional_ab_min_speedup"] and l_val > th["null_effect_boundary"])

    # Priority 6 — QUERY_DEPENDENT (conflicting eligible predeclared strata)
    if eligible_strata_results:
        valid_strata = [st for st in eligible_strata_results if st.get("eligible_for_stratum_decision")]
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


def is_slot_eligible(rec: Mapping[str, Any], variant_id: str) -> Tuple[bool, str]:
    """Enforce Section XXVII.1 eligibility criteria strictly on one primary slot record."""
    if rec.get("slot_purpose") != "primary":
        return False, "NON_PRIMARY_SLOT"
    if rec.get("execution_purpose") != "performance":
        return False, "NON_PERFORMANCE_PURPOSE"
    if rec.get("profiling_enabled") is not False:
        return False, "PROFILING_ENABLED"
    if rec.get("measurement_schema_version") != 1:
        return False, "INVALID_MEASUREMENT_SCHEMA"
    if rec.get("censoring_reason") != "NONE":
        return False, str(rec.get("censoring_reason") or "CENSORED")
    if rec.get("native_status") != "SUCCESS" or rec.get("process_exit_code") != 0 or rec.get("process_signal") is not None:
        return False, "EXECUTION_FAILURE"
    vspec = ALL_VARIANTS.get(variant_id)
    if not vspec:
        return False, "UNKNOWN_VARIANT"
    if (
        rec.get("configuration_initialized") is not True
        or rec.get("configuration_valid") is not True
        or rec.get("configuration_status") != "SUCCESS"
        or rec.get("eligible_for_requested_variant") is not True
        or rec.get("requested_nlf_mode") != vspec["nlf_mode"]
        or rec.get("effective_nlf_mode") != vspec["nlf_mode"]
        or rec.get("requested_neighbor_mode") != vspec["neighbor_mode"]
        or rec.get("effective_neighbor_mode") != vspec["neighbor_mode"]
    ):
        return False, "CONFIGURATION_UNAVAILABLE"
    if rec.get("updates_completed") != rec.get("updates_planned") or rec.get("results_delivered") is not True:
        return False, "INCOMPLETE_UPDATES_OR_DELIVERY"
    if rec.get("correctness_status") not in ("M0_VERIFIED", "DIFFERENTIAL_M2_VERIFIED", "WITNESS_VERIFIED"):
        return False, "CORRECTNESS_UNVERIFIED"
    t = rec.get("streaming_loop_wall_ns")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or math.isnan(t) or math.isinf(t) or t <= 0:
        return False, "MEASUREMENT_ERROR_INVALID_TIME"
    rss = rec.get("peak_rss_bytes")
    if isinstance(rss, bool) or not isinstance(rss, (int, float)) or math.isnan(rss) or math.isinf(rss) or rss <= 0:
        return False, "MEASUREMENT_ERROR_MISSING_RSS"
    return True, "ELIGIBLE"


def stratified_block_bootstrap_median(
    continuity_speedups: Sequence[float],
    complete_disc_group_speedups: Sequence[Tuple[float, float]],
    *,
    resamples: int = 10000,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
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
                sample.extend((float(pair[0]), float(pair[1])))
        sample.sort()
        boot_medians.append(linear_percentile(sample, 0.50))

    boot_medians.sort()
    lo_q = alpha / 2.0
    hi_q = 1.0 - alpha / 2.0
    return {
        "bootstrap_seed": seed,
        "resamples": resamples,
        "continuity_units": n_cont,
        "complete_disc_blocks": n_disc_blocks,
        "alpha": alpha,
        "ci_lower": linear_percentile(boot_medians, lo_q),
        "ci_upper": linear_percentile(boot_medians, hi_q),
        "bootstrap_median_of_medians": linear_percentile(boot_medians, 0.50),
    }


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
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protocol = json.loads(protocol_path.read_text(encoding="utf-8")) if protocol_path.is_file() else {}
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))

    resolved = resolve_p4b_ledger_records(ledger_records)
    canon_slots = resolved["canonical_slots"]

    w_by_id = {w["workload_id"]: w for w in manifest["workloads"]}
    planned_primary = {s["slot_id"]: s for s in schedule["primary_slots"]}

    # Schedule / Provenance Audit across supplied records
    schedule_errors: List[str] = []
    seen_primary_ids = set()
    semantic_mismatch_variants: set[str] = set()
    integrity_error_variants: set[str] = set()
    missing_rss_variants: set[str] = set()

    for sid, rec in canon_slots.items():
        if sid not in planned_primary:
            schedule_errors.append(f"Unplanned extra slot in ledger: {sid}")
            continue
        seen_primary_ids.add(sid)
        plan = planned_primary[sid]
        var_id = rec.get("variant_id")
        if rec.get("workload_id") != plan["workload_id"]:
            schedule_errors.append(f"{sid}: workload_id mismatch ({rec.get('workload_id')} != {plan['workload_id']})")
        if rec.get("workload_sha256") != plan["workload_sha256"]:
            schedule_errors.append(f"{sid}: workload_sha256 mismatch")
        if var_id != plan["variant_id"]:
            schedule_errors.append(f"{sid}: variant_id mismatch ({var_id} != {plan['variant_id']})")
        if rec.get("repeat_id") != plan["repeat_id"]:
            schedule_errors.append(f"{sid}: repeat_id mismatch ({rec.get('repeat_id')} != {plan['repeat_id']})")

        if not allow_synthetic_mode and protocol:
            exp_msha = protocol.get("manifest_sha256")
            if exp_msha and rec.get("manifest_sha256") != exp_msha:
                schedule_errors.append(f"{sid}: ledger manifest_sha256 mismatch")

        if rec.get("correctness_status") in ("TUPLE_MISMATCH", "DUPLICATE_EMISSION", "STEP_COUNT_MISMATCH", "GRAPH_STATE_MISMATCH", "SEMANTIC_UNSOUND"):
            semantic_mismatch_variants.add(str(var_id))
        if rec.get("censoring_reason") in ("SCHEMA_ERROR", "PROVENANCE_ERROR", "MEASUREMENT_ERROR"):
            integrity_error_variants.add(str(var_id))
        if rec.get("censoring_reason") == "MEASUREMENT_ERROR_MISSING_RSS" or (
            rec.get("native_status") == "SUCCESS" and (rec.get("peak_rss_bytes") is None or rec.get("peak_rss_bytes") <= 0)
        ):
            missing_rss_variants.add(str(var_id))

    missing_planned = set(planned_primary.keys()) - seen_primary_ids
    if missing_planned:
        schedule_errors.append(f"Missing {len(missing_planned)} planned primary slots (sample: {sorted(missing_planned)[:3]})")

    if schedule_errors:
        raise LedgerIntegrityError("; ".join(schedule_errors[:5]))

    # Group eligible primary repetitions by (workload_id, variant_id) and enforce exact rep set {1..5}
    elig_reps: Dict[Tuple[str, str], Dict[int, Mapping[str, Any]]] = defaultdict(dict)
    raw_reps_by_cell: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for sid, plan in planned_primary.items():
        rec = canon_slots[sid]
        wid = plan["workload_id"]
        var_id = plan["variant_id"]
        rep = plan["repeat_id"]
        raw_reps_by_cell[(wid, var_id)].append(rec)
        ok, _ = is_slot_eligible(rec, var_id)
        if ok:
            if rep in elig_reps[(wid, var_id)]:
                raise LedgerIntegrityError(f"Duplicate eligible repeat {rep} for ({wid}, {var_id})")
            elig_reps[(wid, var_id)][rep] = rec

    resamples = int(protocol.get("statistical_policy", {}).get("bootstrap_resamples", 10000))
    b_seed = int(protocol.get("seeds", {}).get("bootstrap_seed", BOOTSTRAP_SEED))
    rs_cfg = protocol.get("decision_thresholds", {})

    variant_reports: Dict[str, Any] = {}
    bare_complete_set = {wid for wid in w_by_id if len(elig_reps[(wid, "BARE")]) == CANONICAL_REPETITIONS}

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
            b_ok = (len(b_reps) == CANONICAL_REPETITIONS)
            v_ok = (len(v_reps) == CANONICAL_REPETITIONS)

            if b_ok and v_ok:
                both_complete += 1
            elif b_ok and not v_ok:
                bare_only += 1
                res_fail_reps = sum(
                    1
                    for r in raw_reps_by_cell[(wid, var_id)]
                    if r.get("censoring_reason") in (
                        "TIMEOUT", "WORK_BUDGET_EXCEEDED", "MEMORY_LIMIT_FAILURE",
                        "RESOURCE_UNAVAILABLE", "CONFIGURATION_UNAVAILABLE",
                    )
                )
                if res_fail_reps >= 2:
                    variant_only_resource_reg = True
                continue
            elif (not b_ok) and v_ok:
                var_only += 1
                continue
            else:
                both_incomplete += 1
                continue

            # Strict 5-of-5 aggregation
            t_b = [float(b_reps[r]["streaming_loop_wall_ns"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            t_v = [float(v_reps[r]["streaming_loop_wall_ns"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            m_b = [float(b_reps[r]["peak_rss_bytes"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            m_v = [float(v_reps[r]["peak_rss_bytes"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            o_b = [float(b_reps[r]["outer_wall_ns"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            o_v = [float(v_reps[r]["outer_wall_ns"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            i_b = [float(b_reps[r]["initialization_wall_ns"]) for r in range(1, CANONICAL_REPETITIONS + 1)]
            i_v = [float(v_reps[r]["initialization_wall_ns"]) for r in range(1, CANONICAL_REPETITIONS + 1)]

            med_tb = statistics.median(t_b)
            med_tv = statistics.median(t_v)
            med_mb = statistics.median(m_b)
            med_mv = statistics.median(m_v)
            speedup_i = med_tb / med_tv
            rss_ovh_i = (med_mv / med_mb) - 1.0
            j_b = max(t_b) / min(t_b)
            j_v = max(t_v) / min(t_v)
            is_noisy = (j_b > 1.50) or (j_v > 1.50)
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
                "max_observed_rss_bytes": max(m_v),
                "jitter_bare": j_b,
                "jitter_variant": j_v,
                "noisy_flagged": is_noisy,
            })

        cont_pairs = [p for p in per_workload_pairs if p["suite"] == "continuity"]
        disc_pairs = [p for p in per_workload_pairs if p["suite"] == "discrimination"]

        # Complete DISC group blocks (both k=0 and k=1 eligible)
        disc_by_group: Dict[str, Dict[int, float]] = defaultdict(dict)
        for p in disc_pairs:
            disc_by_group[p["group_id"]][p["k"]] = p["speedup"]
        complete_disc_blocks: List[Tuple[float, float]] = [
            (kv[0], kv[1]) for gid, kv in sorted(disc_by_group.items()) if 0 in kv and 1 in kv
        ]

        overall_cov_ok = len(per_workload_pairs) >= 45
        cont_cov_ok = len(cont_pairs) >= 27
        disc_cov_ok = len(disc_pairs) >= 18
        disc_blocks_ok = len(complete_disc_blocks) >= 9
        excessive_noise = (len(per_workload_pairs) > 0) and ((noisy_pair_count / len(per_workload_pairs)) >= 0.20)

        speedups = sorted(p["speedup"] for p in per_workload_pairs)
        rss_overheads = [p["rss_overhead"] for p in per_workload_pairs]

        if speedups and (cont_pairs or complete_disc_blocks):
            med_s = linear_percentile(speedups, 0.50)
            geo_s = math.exp(sum(math.log(x) for x in speedups) / len(speedups))
            boot = stratified_block_bootstrap_median(
                [p["speedup"] for p in cont_pairs],
                complete_disc_blocks,
                resamples=resamples,
                seed=b_seed,
                alpha=0.05,
            )
            # Supplementary Holm-Bonferroni sensitivity interval (3 primary contrasts -> alpha=0.05/3)
            boot_holm = stratified_block_bootstrap_median(
                [p["speedup"] for p in cont_pairs],
                complete_disc_blocks,
                resamples=resamples,
                seed=b_seed,
                alpha=0.05 / 3.0,
            )
            ci_l = boot["ci_lower"]
            ci_u = boot["ci_upper"]
            dist = {
                "min": speedups[0],
                "q25": linear_percentile(speedups, 0.25),
                "median": med_s,
                "q75": linear_percentile(speedups, 0.75),
                "max": speedups[-1],
                "geometric_mean": geo_s,
                "eligible_pairs": len(per_workload_pairs),
                "excluded_pairs": len(w_by_id) - len(per_workload_pairs),
            }
        else:
            med_s = ci_l = ci_u = None
            boot = None
            boot_holm = None
            dist = {"eligible_pairs": 0, "excluded_pairs": len(w_by_id)}

        mem_summary = {
            "median_rss_overhead": linear_percentile(sorted(rss_overheads), 0.50) if rss_overheads else None,
            "worst_workload_rss_overhead": max(rss_overheads) if rss_overheads else None,
            "max_observed_rss_bytes": max((p["max_observed_rss_bytes"] for p in per_workload_pairs), default=None),
            "workloads_exceeding_20pct": sum(1 for r in rss_overheads if r > 0.20),
        }

        decision = classify_variant_decision(
            speedup_median=med_s,
            ci_lower=ci_l,
            ci_upper=ci_u,
            semantic_mismatch=(var_id in semantic_mismatch_variants),
            evidence_integrity_ok=(var_id not in integrity_error_variants) and (len(schedule_errors) == 0),
            missing_rss=(var_id in missing_rss_variants),
            variant_only_resource_regression=variant_only_resource_reg,
            overall_coverage_ok=overall_cov_ok,
            continuity_coverage_ok=cont_cov_ok,
            disc_coverage_ok=disc_cov_ok,
            complete_disc_blocks_ok=disc_blocks_ok,
            excessive_noise=excessive_noise,
            worst_rss_overhead=mem_summary["worst_workload_rss_overhead"],
            thresholds=rs_cfg,
        )

        variant_reports[var_id] = {
            "censoring_table": {
                "both_complete": both_complete,
                "bare_complete_variant_incomplete": bare_only,
                "bare_incomplete_variant_complete": var_only,
                "both_incomplete": both_incomplete,
            },
            "coverage": {
                "overall": len(per_workload_pairs),
                "continuity": len(cont_pairs),
                "discrimination": len(disc_pairs),
                "complete_disc_blocks": len(complete_disc_blocks),
                "noisy_pairs": noisy_pair_count,
            },
            "speedup_distribution": dist,
            "bootstrap_95": boot,
            "bootstrap_holm_sensitivity": boot_holm,
            "memory_summary": mem_summary,
            "decision": decision,
        }

    return {
        "analysis_mode": "SYNTHETIC_FIXTURE_VERIFICATION" if allow_synthetic_mode else "CONFIRMATORY_ANALYSIS",
        "protocol_version": protocol.get("protocol_version", PROTOCOL_VERSION),
        "manifest_workloads": len(w_by_id),
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
                records.append(json.loads(line))
    summary = analyze_p4b_ledger(
        records,
        protocol_path=args.protocol,
        manifest_path=args.manifest,
        schedule_path=args.schedule,
        allow_synthetic_mode=args.synthetic,
    )
    txt = json.dumps(summary, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(txt + "\n", encoding="utf-8")
    else:
        print(txt)


if __name__ == "__main__":
    main()
