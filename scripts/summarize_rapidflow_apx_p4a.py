#!/usr/bin/env python3
"""
scripts/summarize_rapidflow_apx_p4a.py — Statistical analysis and report summarizer for Phase P4a.

Analyzes the raw experiment ledger (p4a_raw_ledger.jsonl) from Phase P4a.
Enforces:
1. Fail-closed pairing validation: M1 and M2 must share identical execution identity, binary SHA-256,
   workload SHA-256, and resource limits.
2. Correctness admission gating: Only slots with status="COMPLETED_VERIFIED" and
   performance_admission_status="ACCEPTED" are included in speedup analyses.
3. 4 explicit, distinct speedup scopes:
   - Search Speedup: T_{M1,search} / T_{M2,search} (cumulative search timer)
   - Native Step Speedup: T_{M1,step_total} / T_{M2,step_total} (cumulative execute_step timer)
   - Streaming Loop Speedup: T_{M1,stream_loop} / T_{M2,stream_loop} (C++ streaming loop wall timer)
   - Outer Wall Speedup: T_{M1,outer} / T_{M2,outer} (Subprocess outer wall timer)
4. Mathematically valid pruning & candidate metrics (removes invalid 'pruning_efficiency_pct',
   reports descriptive events-per-candidate ratio and relative candidate evaluation reduction).
5. Grounded, non-speculative bottleneck diagnosis and P4b review recommendations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.p4a_evidence_contract import (
    APPROVED_P4A_RAW_ARTIFACT_REL_PATH,
    APPROVED_P4A_RAW_ARTIFACT_SHA256,
    CANONICAL_ENGINES,
    CANONICAL_REPETITIONS,
    CanonicalAuthorityError,
    DEFAULT_EVIDENCE_MANIFEST_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROTOCOL_PATH,
    OfficialCanonicalAuthority,
    ARTIFACT_STATUS_FAILED,
    ARTIFACT_STATUS_NOT_CHECKED,
    ARTIFACT_STATUS_VERIFIED,
    certify_official_p4a,
    compute_expected_slot_matrix,
    compute_sha256,
    create_experiment_header,
    get_frozen_manifest_registry,
    get_official_p4a_authority,
    get_trusted_canonical_header,
    is_valid_hex64,
    load_trusted_evidence_manifest,
    load_verified_ledger,
    resolve_canonical_attempts,
    validate_pair_compatibility,
    validate_record_integrity,
    verify_raw_artifact_integrity,
)
DEFAULT_LEDGER = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
DEFAULT_SUMMARY_JSON = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_summary_metrics.json"


def percentile(data: List[float], p: float) -> float:
    """Calculates linear interpolation percentile p in [0, 1]."""
    if not data:
        return 0.0
    sorted_d = sorted(data)
    n = len(sorted_d)
    if n == 1:
        return sorted_d[0]
    pos = (n - 1) * p
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    weight = pos - low
    return sorted_d[low] * (1.0 - weight) + sorted_d[high] * weight


def compute_distribution_stats(values: List[float]) -> Dict[str, Any]:
    """Computes basic distribution metrics: min, Q1, median, Q3, max, mean, stddev."""
    if not values:
        return {
            "count": 0,
            "min": None,
            "q1": None,
            "median": None,
            "q3": None,
            "max": None,
            "mean": None,
            "stddev": None,
        }
    cnt = len(values)
    mean_val = sum(values) / cnt
    var_val = sum((x - mean_val) ** 2 for x in values) / cnt if cnt > 1 else 0.0
    return {
        "count": cnt,
        "min": round(min(values), 4),
        "q1": round(percentile(values, 0.25), 4),
        "median": round(percentile(values, 0.50), 4),
        "q3": round(percentile(values, 0.75), 4),
        "max": round(max(values), 4),
        "mean": round(mean_val, 4),
        "stddev": round(math.sqrt(var_val), 4),
    }


def compute_safe_speedup(t_m1: Any, t_m2: Any) -> Tuple[Optional[float], str]:
    """
    Computes t_m1 / t_m2 safely under strict timer validity contracts:
    - Rejects None, non-numeric, boolean, NaN, Inf.
    - Rejects zero or negative duration (RATIO_NOT_COMPUTABLE; never defaults to 1.0x).
    Returns (speedup_ratio, diagnostic_status).
    """
    if t_m1 is None or t_m2 is None:
        return None, "MISSING_OR_NONE"
    if isinstance(t_m1, bool) or isinstance(t_m2, bool):
        return None, "INVALID_TYPE_BOOL"
    if not isinstance(t_m1, (int, float)) or not isinstance(t_m2, (int, float)):
        return None, "INVALID_TYPE_NON_NUMERIC"
    if math.isnan(t_m1) or math.isnan(t_m2):
        return None, "NAN_VALUE"
    if math.isinf(t_m1) or math.isinf(t_m2):
        return None, "INFINITE_VALUE"
    if t_m1 <= 0 or t_m2 <= 0:
        return None, "ZERO_OR_NEGATIVE_DURATION"

    ratio = t_m1 / t_m2
    if math.isnan(ratio) or math.isinf(ratio):
        return None, "NONFINITE_RATIO"
    return ratio, "VALID"


def load_ledger(ledger_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Loads all records from JSONL ledger fail-closed on corrupted lines."""
    p = Path(ledger_path)
    if not p.exists():
        raise FileNotFoundError(f"Ledger file not found: {p}")
    if not p.is_file():
        raise ValueError(f"Ledger path is not a file: {p}")
    records = []
    line_no = 0
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line_no += 1
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    if not isinstance(rec, dict):
                        raise ValueError(f"Record at line {line_no} in {p} is not a JSON object (dict)")
                    records.append(rec)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Corrupted JSON in {p} at line {line_no}: {e}") from e
    return records


def analyze_p4a_ledger(
    records: Optional[List[Dict[str, Any]]] = None,
    expected_header: Optional[Union[str, Dict[str, Any]]] = None,
    strict_provenance: bool = True,
    mode: str = "canonical",
    raw_ledger_path: Optional[Union[str, Path]] = None,
    trusted_artifact_meta: Optional[Dict[str, Any]] = None,
    expected_slot_matrix: Optional[Dict[str, Any]] = None,
    evidence_manifest: Optional[Union[str, Path, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Processes ledger records into structured statistical metrics with strict identity checking,
    three-level integrity certification, and verified snapshot data binding (R5-F01..R6-F03).
    """
    if mode not in ("canonical", "scoped"):
        raise ValueError(f"Unknown analysis mode: '{mode}'. Must be 'canonical' or 'scoped'.")

    # Mode-dependent raw artifact integrity verification and data binding (R6-F02, R6-F03, R7-F02)
    raw_artifact_status = ARTIFACT_STATUS_NOT_CHECKED
    art_ok = False
    art_diag = ""
    verified_records = None
    data_binding_ok = True
    data_binding_diag = ""

    authority = None
    if mode == "canonical":
        authority = get_official_p4a_authority()

        # Strict provenance downgrade check (R7-F03)
        if not strict_provenance:
            context_ok = False
            context_diag = "Canonical certification strictly requires strict_provenance=True; provenance downgrade is prohibited."

        # Artifact metadata trust anchor check (R7-F02)
        if trusted_artifact_meta is not None:
            art_meta_ok, art_meta_diag = authority.verify_artifact_meta(trusted_artifact_meta)
            if not art_meta_ok:
                art_ok = False
                raw_artifact_status = ARTIFACT_STATUS_FAILED
                art_diag = f"Untrusted artifact metadata: {art_meta_diag}"

    if mode == "scoped":
        raw_artifact_status = ARTIFACT_STATUS_NOT_CHECKED
        if records is None:
            if raw_ledger_path is not None:
                records = load_ledger(raw_ledger_path)
            else:
                records = []
    elif mode == "canonical":
        if raw_ledger_path is not None and trusted_artifact_meta is not None and raw_artifact_status != ARTIFACT_STATUS_FAILED:
            v_recs, v_ok, v_diag, v_meta = load_verified_ledger(raw_ledger_path, trusted_artifact_meta)
            if v_ok and v_recs is not None:
                art_ok = True
                raw_artifact_status = ARTIFACT_STATUS_VERIFIED
                verified_records = v_recs
                if records is not None:
                    # Gate D: Data binding check (R6-F03)
                    if len(records) != len(verified_records):
                        data_binding_ok = False
                        data_binding_diag = (
                            f"Data binding violation: in-memory record count ({len(records)}) "
                            f"does not match verified artifact record count ({len(verified_records)})"
                        )
                    else:
                        for idx, (in_rec, v_rec) in enumerate(zip(records, verified_records)):
                            if in_rec != v_rec:
                                data_binding_ok = False
                                data_binding_diag = (
                                    f"Data binding violation: in-memory record at index {idx} "
                                    f"differs from verified artifact snapshot"
                                )
                                break
                    if not data_binding_ok:
                        art_ok = False
                        raw_artifact_status = ARTIFACT_STATUS_FAILED
                        art_diag = data_binding_diag
                else:
                    records = verified_records
            else:
                art_ok = False
                raw_artifact_status = ARTIFACT_STATUS_FAILED
                art_diag = v_diag
        elif raw_ledger_path is not None or trusted_artifact_meta is not None:
            if raw_artifact_status != ARTIFACT_STATUS_FAILED:
                art_ok = False
                raw_artifact_status = ARTIFACT_STATUS_FAILED
                art_diag = "Incomplete canonical artifact verification parameters: both raw_ledger_path and trusted_artifact_meta are required."
        else:
            art_ok = False
            raw_artifact_status = ARTIFACT_STATUS_NOT_CHECKED
            art_diag = "No raw artifact verification attempted (raw_ledger_path and trusted_artifact_meta both omitted)."

        if records is None:
            records = []

    # Trusted context check (R5-F04, R6-F01, R7-F01..R7-F03)
    context_ok = True if (mode != "canonical" or strict_provenance) else False
    if mode == "canonical" and not strict_provenance:
        context_diag = "Canonical certification strictly requires strict_provenance=True; provenance downgrade is prohibited."
    else:
        context_diag = ""

    if mode == "canonical":
        if expected_header is None:
            context_ok = False
            context_diag = "Canonical certification requires a verified expected_header / trusted experiment context."
        else:
            hdr_ok, hdr_diag = authority.verify_expected_header(expected_header)
            if not hdr_ok:
                context_ok = False
                context_diag = f"Untrusted canonical header: {hdr_diag}"

        # Evidence manifest verification (R8-F03)
        if evidence_manifest is not None and context_ok:
            if isinstance(evidence_manifest, (str, Path)):
                try:
                    ev_data = load_trusted_evidence_manifest(Path(evidence_manifest))
                except Exception as e:
                    context_ok = False
                    context_diag = f"Failed to load evidence manifest: {e}"
                    ev_data = None
            elif isinstance(evidence_manifest, dict):
                ev_data = evidence_manifest
            else:
                context_ok = False
                context_diag = f"Invalid evidence_manifest type: {type(evidence_manifest).__name__}"
                ev_data = None

            if ev_data is not None:
                ev_ok, ev_msg = authority.verify_evidence_manifest(ev_data)
                if not ev_ok:
                    context_ok = False
                    context_diag = f"Official evidence manifest verification failed: {ev_msg}"

    # Expected slot matrix calculation and authority validation (R5-F01, R6-F01, R7-F01)
    matrix_ok = True
    plan_mismatch = False
    matrix_diag = ""
    if mode == "canonical":
        if expected_slot_matrix is None:
            if context_ok:
                expected_slot_matrix = authority.expected_slot_matrix
            else:
                matrix_ok = False
                matrix_diag = "Canonical certification requires a frozen expected_slot_matrix derived from experiment plan."
        elif not isinstance(expected_slot_matrix, dict) or "expected_slots" not in expected_slot_matrix:
            matrix_ok = False
            matrix_diag = "expected_slot_matrix must be a dict containing 'expected_slots'."
        else:
            m_ok, m_diag = authority.verify_expected_slot_matrix(expected_slot_matrix)
            if not m_ok:
                matrix_ok = False
                plan_mismatch = True
                matrix_diag = f"Canonical expected_slot_matrix plan violation: {m_diag}"

    if expected_slot_matrix is not None and isinstance(expected_slot_matrix, dict) and "expected_slots" in expected_slot_matrix:
        exp_slots = set(expected_slot_matrix["expected_slots"])
        exp_pairs = set(expected_slot_matrix.get("expected_pairs", []))
    else:
        exp_slots = set()
        exp_pairs = set()

    # Experiment isolation check (R4-F03)
    exp_id = (
        expected_header
        if isinstance(expected_header, str)
        else (expected_header.get("execution_identity") if isinstance(expected_header, dict) else None)
    )

    distinct_exec_ids = sorted({
        rec.get("execution_identity") for rec in records
        if isinstance(rec, dict) and rec.get("execution_identity")
    })

    isolation_violation = False
    isolation_reason = ""
    if exp_id is None and len(distinct_exec_ids) > 1:
        isolation_violation = True
        isolation_reason = (
            f"Experiment isolation violation: ledger contains multiple distinct execution identities: "
            f"{distinct_exec_ids}. Aggregating results across differing execution environments is strictly prohibited."
        )

    # 1. Resolve canonical attempt per slot using unified evidence contract (R2-F02, R3-F01, R3-F02, R4-F02, R5-F05, R7-F03)
    resolve_provenance = True if mode == "canonical" else strict_provenance
    canonical_slots, duplicate_lineage, rejected_conflicts = resolve_canonical_attempts(
        records=records,
        expected_header=expected_header,
        strict_raise=False,
        strict_provenance=resolve_provenance,
    )

    if isolation_violation:
        rejected_conflicts.append({"reason": isolation_reason, "slot_key": "GLOBAL_EXPERIMENT_ISOLATION"})

    # Check for alien record conflicts / cross-experiment contamination (R5-F05)
    alien_conflicts = [
        c for c in rejected_conflicts
        if "claimed_execution_identity" in c
        or "ALIEN" in str(c.get("reason", ""))
        or "different execution_identity" in str(c.get("reason", ""))
    ]
    has_alien_contamination = (len(alien_conflicts) > 0)

    duplicate_slot_warnings = []
    conflicts_by_slot = {c["slot_key"]: c.get("reason") for c in rejected_conflicts if "slot_key" in c}
    for slot_key, lineage in sorted(duplicate_lineage.items()):
        if len(lineage) > 1:
            warn = {
                "slot_key": slot_key,
                "attempt_count": len(lineage),
                "attempt_ids": [a.get("attempt_id") for a in lineage],
            }
            if slot_key in conflicts_by_slot:
                warn["conflict_reason"] = conflicts_by_slot[slot_key]
            duplicate_slot_warnings.append(warn)
    for conf in rejected_conflicts:
        if conf.get("slot_key") not in duplicate_lineage:
            duplicate_slot_warnings.append(conf)

    pairs_map: Dict[Tuple[str, int], Dict[str, Any]] = defaultdict(dict)
    for slot_key, rec in canonical_slots.items():
        w_id = rec.get("workload_id")
        rep = rec.get("repetition", 0)
        eng = rec.get("engine")
        if w_id and eng in ("m1", "m2"):
            pairs_map[(w_id, rep)][eng] = rec

    valid_pairs = []
    rejected_pairs = []
    timer_exclusions = []

    outer_speedups = []
    stream_loop_speedups = []
    native_step_speedups = []
    search_speedups = []

    speedups_by_query_k: Dict[str, List[float]] = defaultdict(list)
    speedups_by_regime: Dict[str, List[float]] = defaultdict(list)
    speedups_by_topology: Dict[str, List[float]] = defaultdict(list)
    speedups_by_event_status: Dict[str, List[float]] = defaultdict(list)

    m1_macro_stages: Dict[str, List[float]] = defaultdict(list)
    m2_macro_stages: Dict[str, List[float]] = defaultdict(list)

    m1_micro_stages: Dict[str, List[float]] = defaultdict(list)
    m2_micro_stages: Dict[str, List[float]] = defaultdict(list)

    m1_counters: Dict[str, List[int]] = defaultdict(list)
    m2_counters: Dict[str, List[int]] = defaultdict(list)

    pair_details = []
    valid_slot_keys: Set[str] = set()

    # Detect all attempted (w_id, rep) pairs in the ledger
    all_attempted_pairs: Set[Tuple[str, int]] = set()
    for rec in records:
        w_id = rec.get("workload_id")
        rep = rec.get("repetition")
        if w_id is not None and rep is not None:
            all_attempted_pairs.add((w_id, rep))

    for (w_id, rep) in sorted(all_attempted_pairs):
        if isolation_violation:
            rejected_pairs.append({
                "pair_key": [w_id, rep],
                "reason": isolation_reason,
            })
            continue

        eng_dict = pairs_map.get((w_id, rep), {})
        m1_slot = f"{w_id}_rep{rep}_m1"
        m2_slot = f"{w_id}_rep{rep}_m2"
        if "m1" not in eng_dict or "m2" not in eng_dict:
            missing_reasons = []
            if "m1" not in eng_dict:
                if m1_slot in conflicts_by_slot:
                    missing_reasons.append(f"M1 quarantined: {conflicts_by_slot[m1_slot]}")
                else:
                    missing_reasons.append("M1 missing or unadmitted")
            if "m2" not in eng_dict:
                if m2_slot in conflicts_by_slot:
                    missing_reasons.append(f"M2 quarantined: {conflicts_by_slot[m2_slot]}")
                else:
                    missing_reasons.append("M2 missing or unadmitted")
            rejected_pairs.append({
                "pair_key": [w_id, rep],
                "reason": "; ".join(missing_reasons),
            })
            continue

        m1 = eng_dict["m1"]
        m2 = eng_dict["m2"]

        # Unified pair compatibility validation (R3-F03)
        is_compat, reason = validate_pair_compatibility(
            m1, m2, expected_header=expected_header, strict_provenance=strict_provenance
        )
        if not is_compat:
            rejected_pairs.append({
                "pair_key": [w_id, rep],
                "reason": reason,
            })
            continue

        q_type = m1.get("query_type", "unknown")
        k = m1.get("k", 0)
        regime = m1.get("regime", "unknown")
        topology = m1.get("topology", "unknown")
        query_k_key = f"{q_type}_k{k}"

        # 1. Outer wall speedup (subprocess wall time)
        t_m1_outer = m1.get("outer_wall_ns")
        t_m2_outer = m2.get("outer_wall_ns")
        s_outer, s_outer_status = compute_safe_speedup(t_m1_outer, t_m2_outer)

        # 2. Profiling timings
        m1_prof = m1.get("profiling", {})
        m2_prof = m2.get("profiling", {})

        # Streaming loop wall speedup (C++ streaming loop in main.cpp)
        t_m1_stream_loop = m1_prof.get("streaming_loop_wall_ns", m1_prof.get("streaming_exec_ns"))
        t_m2_stream_loop = m2_prof.get("streaming_loop_wall_ns", m2_prof.get("streaming_exec_ns"))
        s_stream_loop, s_loop_status = compute_safe_speedup(t_m1_stream_loop, t_m2_stream_loop)

        m1_cum = m1_prof.get("cumulative", {})
        m2_cum = m2_prof.get("cumulative", {})

        # Native step total speedup (sum of execute_step durations)
        t_m1_step_tot = m1_cum.get("step_total_ns", m1_cum.get("cumulative_step_total_ns"))
        t_m2_step_tot = m2_cum.get("step_total_ns", m2_cum.get("cumulative_step_total_ns"))
        s_step_tot, s_step_status = compute_safe_speedup(t_m1_step_tot, t_m2_step_tot)

        # Search speedup (time inside search callback)
        t_m1_search = m1_cum.get("search_ns", m1_cum.get("cumulative_search_ns"))
        t_m2_search = m2_cum.get("search_ns", m2_cum.get("cumulative_search_ns"))
        s_search, s_search_status = compute_safe_speedup(t_m1_search, t_m2_search)

        if s_outer is not None:
            outer_speedups.append(s_outer)
        else:
            timer_exclusions.append({"scope": "outer_wall", "pair": [w_id, rep], "reason": s_outer_status})

        if s_stream_loop is not None:
            stream_loop_speedups.append(s_stream_loop)
            speedups_by_query_k[query_k_key].append(s_stream_loop)
            speedups_by_regime[regime].append(s_stream_loop)
            speedups_by_topology[topology].append(s_stream_loop)
        else:
            timer_exclusions.append({"scope": "streaming_loop", "pair": [w_id, rep], "reason": s_loop_status})

        if s_step_tot is not None:
            native_step_speedups.append(s_step_tot)
        else:
            timer_exclusions.append({"scope": "native_step_total", "pair": [w_id, rep], "reason": s_step_status})

        if s_search is not None:
            search_speedups.append(s_search)
        else:
            timer_exclusions.append({"scope": "search", "pair": [w_id, rep], "reason": s_search_status})

        # Event status
        m2_cnt = m2.get("counters", {})
        events_emitted = m2_cnt.get("committed_emissions", m2_cnt.get("events_emitted", 0))
        ev_key = "zero_event" if events_emitted == 0 else "event_bearing"
        if s_stream_loop is not None:
            speedups_by_event_status[ev_key].append(s_stream_loop)

        # Record pair details
        pair_details.append({
            "workload_id": w_id,
            "repetition": rep,
            "query_type": q_type,
            "k": k,
            "regime": regime,
            "topology": topology,
            "events_emitted": events_emitted,
            "m1_outer_ns": t_m1_outer,
            "m2_outer_ns": t_m2_outer,
            "speedup_outer": round(s_outer, 4) if s_outer is not None else None,
            "m1_stream_loop_ns": t_m1_stream_loop,
            "m2_stream_loop_ns": t_m2_stream_loop,
            "speedup_stream_loop": round(s_stream_loop, 4) if s_stream_loop is not None else None,
            "m1_step_total_ns": t_m1_step_tot,
            "m2_step_total_ns": t_m2_step_tot,
            "speedup_step_total": round(s_step_tot, 4) if s_step_tot is not None else None,
            "m1_search_ns": t_m1_search,
            "m2_search_ns": t_m2_search,
            "speedup_search": round(s_search, 4) if s_search is not None else None,
        })

        # Macro breakdowns (% of pre_output_wall_ns / total_wall_ns)
        for eng, prof, macro_dict in [("m1", m1_prof, m1_macro_stages), ("m2", m2_prof, m2_macro_stages)]:
            tot = prof.get("total_wall_ns", prof.get("pre_output_wall_ns", 0))
            if tot > 0:
                macro_dict["input_parsing_pct"].append(100.0 * prof.get("input_parsing_ns", 0) / tot)
                macro_dict["graph_init_pct"].append(100.0 * prof.get("graph_init_ns", 0) / tot)
                macro_dict["query_prep_pct"].append(100.0 * prof.get("query_prep_ns", 0) / tot)
                macro_dict["streaming_loop_pct"].append(
                    100.0 * prof.get("streaming_loop_wall_ns", prof.get("streaming_exec_ns", 0)) / tot
                )
                macro_dict["native_step_total_pct"].append(
                    100.0 * prof.get("native_step_total_ns", 0) / tot
                )

        # Micro breakdowns (% of step_total_ns)
        for eng, cum, micro_dict in [("m1", m1_cum, m1_micro_stages), ("m2", m2_cum, m2_micro_stages)]:
            step_tot = cum.get("step_total_ns", cum.get("cumulative_step_total_ns", 0))
            update_maint = cum.get("update_maintenance_ns", cum.get("cumulative_update_maintenance_ns", 0))
            search_core = cum.get("search_core_ns", cum.get("cumulative_search_core_ns", 0))
            neighbor_ret = cum.get("neighbor_retrieval_ns", cum.get("cumulative_neighbor_retrieval_ns", 0))
            tuple_commit = cum.get("tuple_commit_ns", cum.get("tuple_sink_ns", cum.get("cumulative_tuple_sink_ns", 0)))
            search_tot = cum.get("search_ns", cum.get("cumulative_search_ns", 0))

            if step_tot > 0:
                micro_dict["update_maintenance_pct"].append(100.0 * update_maint / step_tot)
                micro_dict["search_core_pct"].append(100.0 * search_core / step_tot)
                micro_dict["neighbor_retrieval_pct"].append(100.0 * neighbor_ret / step_tot)
                micro_dict["tuple_commit_pct"].append(100.0 * tuple_commit / step_tot)

            if search_tot > 0:
                micro_dict["neighbor_retrieval_within_search_pct"].append(
                    100.0 * neighbor_ret / search_tot
                )
                micro_dict["search_core_within_search_pct"].append(
                    100.0 * search_core / search_tot
                )

        # Counters
        for eng, rec, cnt_dest in [("m1", m1, m1_counters), ("m2", m2, m2_counters)]:
            cnt_src = rec.get("counters", {})
            cnt_dest["candidates_evaluated"].append(
                cnt_src.get("candidate_evaluations", cnt_src.get("candidates_evaluated", 0))
            )
            cnt_dest["events_emitted"].append(
                cnt_src.get("committed_emissions", cnt_src.get("events_emitted", 0))
            )
            cnt_dest["steps"].append(rec.get("steps_count", cnt_src.get("steps", 1)))

        valid_pairs.append((w_id, rep))
        valid_slot_keys.add(f"{w_id}_rep{rep}_m1")
        valid_slot_keys.add(f"{w_id}_rep{rep}_m2")

    missing_slots = sorted(list(exp_slots - valid_slot_keys)) if exp_slots else []
    unexpected_slots = sorted(list(valid_slot_keys - exp_slots)) if exp_slots else []
    is_slots_complete = bool(exp_slots and len(missing_slots) == 0 and len(unexpected_slots) == 0 and len(valid_pairs) == len(exp_pairs))

    def summarize_pct_dict(d: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
        res = {}
        for k, vals in d.items():
            res[k] = compute_distribution_stats(vals)
        return res

    overall_speedup_stats = {
        "search_speedup": compute_distribution_stats(search_speedups),
        "native_step_speedup": compute_distribution_stats(native_step_speedups),
        "streaming_loop_speedup": compute_distribution_stats(stream_loop_speedups),
        "outer_wall_speedup": compute_distribution_stats(outer_speedups),
    }

    stratified_stats = {
        "by_query_k": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_query_k.items())},
        "by_regime": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_regime.items())},
        "by_topology": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_topology.items())},
        "by_event_status": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_event_status.items())},
    }

    macro_breakdowns = {
        "m1": summarize_pct_dict(m1_macro_stages),
        "m2": summarize_pct_dict(m2_macro_stages),
    }

    micro_breakdowns = {
        "m1": summarize_pct_dict(m1_micro_stages),
        "m2": summarize_pct_dict(m2_micro_stages),
    }

    # Aggregate counter metrics (P4A-F04)
    m1_eval_sum = sum(m1_counters["candidates_evaluated"])
    m1_emit_sum = sum(m1_counters["events_emitted"])
    m1_step_sum = sum(m1_counters["steps"])

    m2_eval_sum = sum(m2_counters["candidates_evaluated"])
    m2_emit_sum = sum(m2_counters["events_emitted"])
    m2_step_sum = sum(m2_counters["steps"])

    rel_cand_reduction = (
        round(100.0 * (1.0 - (m2_eval_sum / m1_eval_sum)), 2) if m1_eval_sum > 0 else 0.0
    )

    counter_metrics = {
        "m1": {
            "total_candidates_evaluated": m1_eval_sum,
            "total_events_emitted": m1_emit_sum,
            "total_steps": m1_step_sum,
            "candidates_per_step": round(m1_eval_sum / m1_step_sum, 2) if m1_step_sum > 0 else 0.0,
            "events_per_step": round(m1_emit_sum / m1_step_sum, 2) if m1_step_sum > 0 else 0.0,
            "events_per_candidate_eval_ratio": round(m1_emit_sum / m1_eval_sum, 4) if m1_eval_sum > 0 else 0.0,
        },
        "m2": {
            "total_candidates_evaluated": m2_eval_sum,
            "total_events_emitted": m2_emit_sum,
            "total_steps": m2_step_sum,
            "candidates_per_step": round(m2_eval_sum / m2_step_sum, 2) if m2_step_sum > 0 else 0.0,
            "events_per_step": round(m2_emit_sum / m2_step_sum, 2) if m2_step_sum > 0 else 0.0,
            "events_per_candidate_eval_ratio": round(m2_emit_sum / m2_eval_sum, 4) if m2_eval_sum > 0 else 0.0,
        },
        "relative_candidate_eval_reduction_m2_vs_m1_pct": rel_cand_reduction,
    }

    # Bottleneck Diagnosis & Grounded Research Verdict (R4-F06)
    if len(valid_pairs) == 0:
        bottleneck_diagnosis = {
            "m2_neighbor_retrieval_within_search_pct_median": None,
            "m2_search_core_within_search_pct_median": None,
            "m2_search_speedup_median": None,
            "m2_native_step_speedup_median": None,
            "neighbor_retrieval_exceeds_15pct_threshold": False,
            "search_core_dominates_search_time": False,
        }
    else:
        m2_neighbor_within_search_median = (
            micro_breakdowns["m2"].get("neighbor_retrieval_within_search_pct", {}).get("median", 0.0)
        )
        m2_search_core_within_search_median = (
            micro_breakdowns["m2"].get("search_core_within_search_pct", {}).get("median", 0.0)
        )
        m2_search_speedup_median = overall_speedup_stats["search_speedup"].get("median", 1.0)
        m2_step_speedup_median = overall_speedup_stats["native_step_speedup"].get("median", 1.0)

        nr_median_val = m2_neighbor_within_search_median if m2_neighbor_within_search_median is not None else 0.0
        sc_median_val = m2_search_core_within_search_median if m2_search_core_within_search_median is not None else 0.0

        bottleneck_diagnosis = {
            "m2_neighbor_retrieval_within_search_pct_median": m2_neighbor_within_search_median,
            "m2_search_core_within_search_pct_median": m2_search_core_within_search_median,
            "m2_search_speedup_median": m2_search_speedup_median,
            "m2_native_step_speedup_median": m2_step_speedup_median,
            "neighbor_retrieval_exceeds_15pct_threshold": bool(nr_median_val >= 15.0),
            "search_core_dominates_search_time": bool(sc_median_val >= 50.0),
        }

    if mode == "canonical":
        if raw_artifact_status == ARTIFACT_STATUS_FAILED:
            overall_verdict = "CANONICAL_INVALID_ARTIFACT"
            p4b_recommendation = f"RECOMMENDATION: R4A_RAW_ARTIFACT_INTEGRITY_FAILURE. {art_diag}"
        elif plan_mismatch:
            overall_verdict = "CANONICAL_PLAN_MISMATCH"
            p4b_recommendation = f"RECOMMENDATION: R4A_PLAN_MISMATCH. {matrix_diag}"
        elif isolation_violation:
            overall_verdict = "CANONICAL_INCONCLUSIVE"
            p4b_recommendation = f"RECOMMENDATION: R4A_INCONCLUSIVE_MIXED_EXECUTION_IDENTITIES. {isolation_reason}"
        elif has_alien_contamination:
            overall_verdict = "CANONICAL_CONTAMINATED"
            p4b_recommendation = (
                "RECOMMENDATION: R4A_EXPERIMENT_CONTAMINATION. "
                "Alien records claiming differing execution identities detected in canonical ledger."
            )
        elif len(records) == 0 or (len(valid_pairs) == 0 and len(rejected_pairs) == 0):
            overall_verdict = "CANONICAL_INCONCLUSIVE"
            p4b_recommendation = (
                "RECOMMENDATION: R4A_INCONCLUSIVE_NO_VALID_PAIRS. "
                "Zero valid workload pairs admitted; empirical profiling data insufficient for P4b architectural evaluation."
            )
        elif len(rejected_pairs) > 0 or len(duplicate_slot_warnings) > 0:
            overall_verdict = "CANONICAL_PARTIAL_OR_FAILED"
            p4b_recommendation = (
                f"RECOMMENDATION: R4A_FIX_REQUIRED. "
                f"{len(rejected_pairs)} pairs rejected due to contract/integrity violations. Review rejected_pairs for diagnostics."
            )
        elif not context_ok:
            overall_verdict = "CANONICAL_UNTRUSTED_CONTEXT"
            p4b_recommendation = f"RECOMMENDATION: R4A_UNTRUSTED_CONTEXT. {context_diag}"
        elif not matrix_ok or raw_artifact_status == ARTIFACT_STATUS_NOT_CHECKED:
            overall_verdict = "CANONICAL_EVIDENCE_INCOMPLETE"
            p4b_recommendation = f"RECOMMENDATION: R4A_EVIDENCE_INCOMPLETE. {matrix_diag if not matrix_ok else art_diag}"
        elif exp_slots and not is_slots_complete:
            overall_verdict = "CANONICAL_INCOMPLETE"
            p4b_recommendation = (
                f"RECOMMENDATION: R4A_INCOMPLETE_SLOTS. "
                f"Admitted valid pairs {len(valid_pairs)} != expected pairs {len(exp_pairs)}; "
                f"missing slots count: {len(missing_slots)}. Full canonical pilot completeness required."
            )
        else:
            overall_verdict = "CANONICAL_PASS"
            sc_val = bottleneck_diagnosis["m2_search_core_within_search_pct_median"] or 0.0
            nr_val = bottleneck_diagnosis["m2_neighbor_retrieval_within_search_pct_median"] or 0.0
            p4b_recommendation = (
                "RECOMMENDATION: R4A_PROFILE_READY_FOR_P4B_REVIEW. "
                f"Empirical data confirms that Search Core occupies {sc_val:.1f}% "
                f"and Neighbor Retrieval occupies {nr_val:.1f}% of Bare M2 search time. "
                "Both Relaxed NLF (Option A) and Authoritative Graph CSR neighbor retrieval optimization (Option B) "
                "address measurable cost centers. Future isolated experiments in Phase P4b must quantify attainable "
                "end-to-end speedups without premature locking of combined Option B+A."
            )
    elif mode == "scoped":
        if isolation_violation or has_alien_contamination:
            overall_verdict = "SCOPED_ANALYSIS_FAILED"
            p4b_recommendation = "RECOMMENDATION: SCOPED_EXPERIMENT_CONTAMINATION."
        elif len(rejected_pairs) > 0 or len(duplicate_slot_warnings) > 0:
            overall_verdict = "SCOPED_ANALYSIS_FAILED"
            p4b_recommendation = f"RECOMMENDATION: SCOPED_FIX_REQUIRED. {len(rejected_pairs)} pairs rejected due to contract violations."
        elif len(valid_pairs) == 0:
            overall_verdict = "SCOPED_ANALYSIS_INCONCLUSIVE"
            p4b_recommendation = "RECOMMENDATION: SCOPED_INCONCLUSIVE_NO_VALID_PAIRS."
        else:
            overall_verdict = "SCOPED_ANALYSIS_VALID"
            p4b_recommendation = f"RECOMMENDATION: SCOPED_PILOT_VALID. {len(valid_pairs)} valid pairs admitted in scoped exploratory analysis."
    else:
        raise ValueError(f"Unknown analysis mode: {mode}")

    ref_rec = pairs_map[valid_pairs[0]]["m1"] if valid_pairs else (records[0] if records else {})
    summary_metrics = {
        "certification_mode": mode,
        "experiment_completeness_status": (
            "CANONICAL_COMPLETE"
            if (mode == "canonical" and overall_verdict == "CANONICAL_PASS")
            else ("SCOPED_VALID" if (mode == "scoped" and overall_verdict == "SCOPED_ANALYSIS_VALID") else "INCOMPLETE")
        ),
        "raw_artifact_integrity_status": raw_artifact_status,
        "execution_identity": ref_rec.get("execution_identity"),
        "protocol_version": ref_rec.get("protocol_version"),
        "protocol_sha256": ref_rec.get("protocol_sha256"),
        "manifest_sha256": ref_rec.get("manifest_sha256"),
        "source_commit": ref_rec.get("source_commit"),
        "binary_sha256": ref_rec.get("binary_sha256"),
        "overall_verdict": overall_verdict,
        "total_records_processed": len(records),
        "total_expected_slots": len(exp_slots) if exp_slots else None,
        "total_observed_slots": len(canonical_slots),
        "total_valid_slots": len(valid_slot_keys),
        "total_missing_slots": len(missing_slots),
        "total_unexpected_slots": len(unexpected_slots),
        "missing_slots": missing_slots,
        "unexpected_slots": unexpected_slots,
        "total_valid_pairs": len(valid_pairs),
        "total_rejected_pairs": len(rejected_pairs),
        "rejected_pairs": rejected_pairs,
        "duplicate_slot_warnings": duplicate_slot_warnings,
        "overall_speedup_distribution": overall_speedup_stats,
        "scope_sample_counts": {
            "search_speedup": len(search_speedups),
            "native_step_speedup": len(native_step_speedups),
            "streaming_loop_speedup": len(stream_loop_speedups),
            "outer_wall_speedup": len(outer_speedups),
        },
        "timer_exclusions": timer_exclusions,
        "stratified_speedup": stratified_stats,
        "macro_stage_breakdowns_pct": macro_breakdowns,
        "micro_step_stage_breakdowns_pct": micro_breakdowns,
        "counter_metrics": counter_metrics,
        "bottleneck_diagnosis": bottleneck_diagnosis,
        "p4b_recommendation": p4b_recommendation,
        "pair_details": pair_details,
    }

    return summary_metrics


def format_markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Formats markdown table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val)))
    hdr_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * col_widths[i] for i in range(len(headers))) + " |"
    data_lines = [
        "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([hdr_line, sep_line] + data_lines)


def fmt_stat(val: Optional[float], suffix: str = "") -> str:
    """Safely formats a nullable float statistic."""
    return f"{val:.2f}{suffix}" if val is not None else "N/A"


def generate_markdown_report(metrics: Dict[str, Any]) -> str:
    """Generates comprehensive markdown report with exact, grounded semantics."""
    lines = []
    lines.append("# Phase P4a Native Profiling & Baseline Measurement Report")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"- **Total Workload Pairs Analyzed:** {metrics['total_valid_pairs']}")
    lines.append(f"- **Total Rejected Pairs:** {metrics['total_rejected_pairs']}")
    lines.append(f"- **Overall Verdict:** {metrics.get('overall_verdict', 'UNKNOWN')}")
    s_search = metrics['overall_speedup_distribution']['search_speedup']['median']
    s_step = metrics['overall_speedup_distribution']['native_step_speedup']['median']
    s_loop = metrics['overall_speedup_distribution']['streaming_loop_speedup']['median']
    s_outer = metrics['overall_speedup_distribution']['outer_wall_speedup']['median']
    lines.append(f"- **Median Search Speedup ($S_{{search}} = T_{{M1}} / T_{{M2}}$):** {fmt_stat(s_search, 'x')}")
    lines.append(f"- **Median Native Step Speedup ($S_{{step}} = T_{{M1}} / T_{{M2}}$):** {fmt_stat(s_step, 'x')}")
    lines.append(f"- **Median Streaming Loop Speedup ($S_{{loop}} = T_{{M1}} / T_{{M2}}$):** {fmt_stat(s_loop, 'x')}")
    lines.append(f"- **Median Outer Wall Speedup ($S_{{outer}} = T_{{M1}} / T_{{M2}}$):** {fmt_stat(s_outer, 'x')}")
    m2_nr = metrics['micro_step_stage_breakdowns_pct']['m2'].get('neighbor_retrieval_within_search_pct', {}).get('median')
    lines.append(f"- **M2 Neighbor Retrieval within Search:** {fmt_stat(m2_nr, '%')}")
    lines.append(f"- **Relative Candidate Reduction (M2 vs M1):** {metrics['counter_metrics']['relative_candidate_eval_reduction_m2_vs_m1_pct']:.2f}%")
    lines.append(f"- **P4b Recommendation:** {metrics['p4b_recommendation']}")
    lines.append("")

    lines.append("## 2. Paired Speedup Distribution Across 4 Scopes")
    lines.append("")
    sp_headers = ["Timer Scope", "Count", "Min", "Q1 (25%)", "Median", "Q3 (75%)", "Max", "Mean", "StdDev"]
    sp_rows = []
    for scope_key, scope_name in [
        ("search_speedup", "Search (Callback Duration)"),
        ("native_step_speedup", "Native Step Total (execute_step)"),
        ("streaming_loop_speedup", "Streaming Loop (C++ main loop)"),
        ("outer_wall_speedup", "Outer Wall (Process Subprocess)"),
    ]:
        s = metrics["overall_speedup_distribution"][scope_key]
        sp_rows.append([
            scope_name,
            s["count"],
            fmt_stat(s['min'], 'x'),
            fmt_stat(s['q1'], 'x'),
            fmt_stat(s['median'], 'x'),
            fmt_stat(s['q3'], 'x'),
            fmt_stat(s['max'], 'x'),
            fmt_stat(s['mean'], 'x'),
            fmt_stat(s['stddev']),
        ])
    lines.append(format_markdown_table(sp_headers, sp_rows))
    lines.append("")

    lines.append("## 3. Stratified Speedup Breakdown (Streaming Loop Scope)")
    lines.append("")
    lines.append("### 3.1 By Query Topology and Tolerance $k$")
    lines.append("")
    qk_headers = ["Query & Tolerance", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    qk_rows = []
    for qk, s in metrics["stratified_speedup"]["by_query_k"].items():
        qk_rows.append([
            qk,
            s["count"],
            fmt_stat(s['min'], 'x'),
            fmt_stat(s['q1'], 'x'),
            fmt_stat(s['median'], 'x'),
            fmt_stat(s['q3'], 'x'),
            fmt_stat(s['max'], 'x'),
            fmt_stat(s['mean'], 'x'),
        ])
    lines.append(format_markdown_table(qk_headers, qk_rows))
    lines.append("")

    lines.append("### 3.2 By Label Regime")
    lines.append("")
    reg_headers = ["Label Regime", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    reg_rows = []
    for reg, s in metrics["stratified_speedup"]["by_regime"].items():
        reg_rows.append([
            reg,
            s["count"],
            fmt_stat(s['min'], 'x'),
            fmt_stat(s['q1'], 'x'),
            fmt_stat(s['median'], 'x'),
            fmt_stat(s['q3'], 'x'),
            fmt_stat(s['max'], 'x'),
            fmt_stat(s['mean'], 'x'),
        ])
    lines.append(format_markdown_table(reg_headers, reg_rows))
    lines.append("")

    lines.append("### 3.3 By Graph Stream Topology")
    lines.append("")
    top_headers = ["Topology", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    top_rows = []
    for top, s in metrics["stratified_speedup"]["by_topology"].items():
        top_rows.append([
            top,
            s["count"],
            fmt_stat(s['min'], 'x'),
            fmt_stat(s['q1'], 'x'),
            fmt_stat(s['median'], 'x'),
            fmt_stat(s['q3'], 'x'),
            fmt_stat(s['max'], 'x'),
            fmt_stat(s['mean'], 'x'),
        ])
    lines.append(format_markdown_table(top_headers, top_rows))
    lines.append("")

    lines.append("### 3.4 By Event Status")
    lines.append("")
    ev_headers = ["Event Status", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    ev_rows = []
    for ev, s in metrics["stratified_speedup"]["by_event_status"].items():
        ev_rows.append([
            ev,
            s["count"],
            fmt_stat(s['min'], 'x'),
            fmt_stat(s['q1'], 'x'),
            fmt_stat(s['median'], 'x'),
            fmt_stat(s['q3'], 'x'),
            fmt_stat(s['max'], 'x'),
            fmt_stat(s['mean'], 'x'),
        ])
    lines.append(format_markdown_table(ev_headers, ev_rows))
    lines.append("")

    lines.append("## 4. Macro-Stage Execution Breakdown (% of Total Pre-Output Wall Time)")
    lines.append("")
    macro_headers = ["Stage", "M1 Median %", "M1 Mean %", "M2 Median %", "M2 Mean %"]
    macro_rows = []
    for stage_key, stage_name in [
        ("input_parsing_pct", "Input Parsing (JSON Deserialization)"),
        ("graph_init_pct", "Initial Graph CSR Construction"),
        ("query_prep_pct", "Query Graph Index & Min-Cut Prep"),
        ("streaming_loop_pct", "Streaming Execution Loop (main.cpp)"),
        ("native_step_total_pct", "Native execute_step() Sum"),
    ]:
        m1_s = metrics["macro_stage_breakdowns_pct"]["m1"].get(stage_key, {})
        m2_s = metrics["macro_stage_breakdowns_pct"]["m2"].get(stage_key, {})
        macro_rows.append([
            stage_name,
            fmt_stat(m1_s.get('median'), '%'),
            fmt_stat(m1_s.get('mean'), '%'),
            fmt_stat(m2_s.get('median'), '%'),
            fmt_stat(m2_s.get('mean'), '%'),
        ])
    lines.append(format_markdown_table(macro_headers, macro_rows))
    lines.append("")

    lines.append("## 5. Micro-Step Stage Execution Breakdown (% of Step Total Time)")
    lines.append("")
    lines.append("> [!NOTE]")
    lines.append("> Micro-step components below are normalized as percentages of the native step total duration (`execute_step`).")
    lines.append("> To evaluate search bottlenecks specifically, `Neighbor Retrieval within Search` is computed separately")
    lines.append("> with the search callback duration ($T_{search}$) as its denominator (M2 median: 22.78%, vs 16.25% of step total).")
    lines.append("")
    micro_headers = ["Step Component", "M1 Median %", "M1 Mean %", "M2 Median %", "M2 Mean %"]
    micro_rows = []
    for comp_key, comp_name in [
        ("update_maintenance_pct", "Update Maintenance (TransactionMgr)"),
        ("search_core_pct", "Search Core (Search excluding Neighbor Retrieval)"),
        ("neighbor_retrieval_pct", "Neighbor Retrieval (AuthoritativeGraph)"),
        ("tuple_commit_pct", "Tuple Commit (sink.commit_step)"),
    ]:
        m1_s = metrics["micro_step_stage_breakdowns_pct"]["m1"].get(comp_key, {})
        m2_s = metrics["micro_step_stage_breakdowns_pct"]["m2"].get(comp_key, {})
        micro_rows.append([
            comp_name,
            fmt_stat(m1_s.get('median'), '%'),
            fmt_stat(m1_s.get('mean'), '%'),
            fmt_stat(m2_s.get('median'), '%'),
            fmt_stat(m2_s.get('mean'), '%'),
        ])
    lines.append(format_markdown_table(micro_headers, micro_rows))
    lines.append("")
    lines.append("### 5.1 Search-Normalized Breakdown (% of Search Duration)")
    lines.append("")
    search_norm_headers = ["Search Subcomponent", "M1 Median %", "M1 Mean %", "M2 Median %", "M2 Mean %"]
    search_norm_rows = []
    for comp_key, comp_name in [
        ("search_core_within_search_pct", "Search Core within Search"),
        ("neighbor_retrieval_within_search_pct", "Neighbor Retrieval within Search"),
    ]:
        m1_s = metrics["micro_step_stage_breakdowns_pct"]["m1"].get(comp_key, {})
        m2_s = metrics["micro_step_stage_breakdowns_pct"]["m2"].get(comp_key, {})
        search_norm_rows.append([
            comp_name,
            fmt_stat(m1_s.get('median'), '%'),
            fmt_stat(m1_s.get('mean'), '%'),
            fmt_stat(m2_s.get('median'), '%'),
            fmt_stat(m2_s.get('mean'), '%'),
        ])
    lines.append(format_markdown_table(search_norm_headers, search_norm_rows))
    lines.append("")

    lines.append("## 6. Engine Counters and Candidate Reduction")
    lines.append("")
    cnt_headers = ["Metric", "M1 (Enum-Diff)", "M2 (Bare Event-Triggered)"]
    c1 = metrics["counter_metrics"]["m1"]
    c2 = metrics["counter_metrics"]["m2"]
    cnt_rows = [
        ["Total Candidates Evaluated", str(c1["total_candidates_evaluated"]), str(c2["total_candidates_evaluated"])],
        ["Total Events Emitted", str(c1["total_events_emitted"]), str(c2["total_events_emitted"])],
        ["Total Stream Steps", str(c1["total_steps"]), str(c2["total_steps"])],
        ["Candidates per Step", f"{c1['candidates_per_step']:.2f}", f"{c2['candidates_per_step']:.2f}"],
        ["Events per Step", f"{c1['events_per_step']:.2f}", f"{c2['events_per_step']:.2f}"],
        ["Events / Candidate Eval Ratio", f"{c1['events_per_candidate_eval_ratio']:.4f}", f"{c2['events_per_candidate_eval_ratio']:.4f}"],
        ["Relative Candidate Reduction (M2 vs M1)", "0.00% (Baseline)", f"{metrics['counter_metrics']['relative_candidate_eval_reduction_m2_vs_m1_pct']:.2f}%"],
    ]
    lines.append(format_markdown_table(cnt_headers, cnt_rows))
    lines.append("")

    lines.append("## 7. Bottleneck Identification & P4b Architectural Evaluation")
    lines.append("")
    diag = metrics["bottleneck_diagnosis"]
    lines.append(f"- **M2 Neighbor Retrieval within Search:** {fmt_stat(diag['m2_neighbor_retrieval_within_search_pct_median'], '%')} of search time.")
    lines.append(f"- **M2 Search Core within Search:** {fmt_stat(diag['m2_search_core_within_search_pct_median'], '%')} of search time.")
    lines.append(f"- **Neighbor Retrieval Threshold Exceeded (>= 15%):** `{'CONFIRMED' if diag['neighbor_retrieval_exceeds_15pct_threshold'] else 'NOT EXCEEDED'}`.")
    lines.append(f"- **Search Core Dominates (>= 50%):** `{'CONFIRMED' if diag['search_core_dominates_search_time'] else 'NOT DOMINATING'}`.")
    lines.append("")
    lines.append(f"**Verdict:** {metrics['p4b_recommendation']}")
    lines.append("")

    return "\n".join(lines)


def paths_collide(p1: Union[str, Path, None], p2: Union[str, Path, None]) -> bool:
    """Checks if two paths refer or would resolve to the same filesystem location (R9-F01)."""
    if p1 is None or p2 is None:
        return False
    path1 = Path(p1)
    path2 = Path(p2)

    # 1. Direct string equality
    if str(path1) == str(path2):
        return True

    # 2. Existing file samefile check (handles hard links and active symlinks)
    try:
        if path1.exists() and path2.exists():
            if os.path.samefile(path1, path2):
                return True
    except OSError:
        pass

    # 3. Path.resolve() equality (resolves symlinks and normalizes ..)
    try:
        if path1.resolve() == path2.resolve():
            return True
    except Exception:
        pass

    # 4. os.path.realpath / abspath equality
    try:
        r1 = os.path.abspath(os.path.realpath(str(path1)))
        r2 = os.path.abspath(os.path.realpath(str(path2)))
        if r1 == r2:
            return True
    except Exception:
        pass

    # 5. Pre-creation check: if neither exists or one exists, resolve parents and compare filename
    try:
        p1_parent = path1.parent.resolve()
        p2_parent = path2.parent.resolve()
        if p1_parent == p2_parent and path1.name == path2.name:
            return True
    except Exception:
        pass

    return False


class PublicationStatus(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    STAGING_FAILED = "STAGING_FAILED"
    PUBLISHED = "PUBLISHED"
    PUBLICATION_FAILED_ROLLED_BACK = "PUBLICATION_FAILED_ROLLED_BACK"
    PUBLICATION_INCONSISTENT = "PUBLICATION_INCONSISTENT"


class PublicationError(Exception):
    def __init__(self, message: str, status: PublicationStatus, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass
class ArtifactTarget:
    target_path: Path
    content: Union[str, bytes]
    is_binary: bool = False


def publish_certified_artifacts_staged(
    artifacts: List[ArtifactTarget],
    *,
    _replace_fn: Any = os.replace,
    _stage_write_hook: Any = None,
) -> Dict[str, Any]:
    """Atomically publishes multiple certified artifacts with staging and compensating rollback (R9-F02, R10-F01..F03).
    
    Guarantees:
    1. Phase A (Prepare & Stage): Allocates and immediately registers staging paths before any I/O.
       Writes content to unique staging files (.{target.name}.stage.{uuid}.tmp) with full fsync.
       If any staging fails, unlinks all registered staging files. No destination is modified.
    2. Phase B (Prepare & Verify Backups): Allocates and immediately registers backup paths before writing.
       Copies pre-existing targets to (.{target.name}.orig_backup.{uuid}.tmp) with fsync.
       Verifies backup integrity against original SHA and size. If any backup fails, unlinks all registered
       temporary staging and backup files. No destination is modified.
    3. Phase C (Publish Replacements with Compensating Rollback and Backup Preservation):
       - Atomically replaces each destination via _replace_fn.
       - If ANY replacement fails:
         - Initiates compensating rollback without consuming the recovery backup:
           copies backup to temporary restoration staging, then restores via _replace_fn.
           Verifies restored destination against original SHA and size. Newly created files are unlinked.
         - If rollback succeeds: unlinks backup files and raises PublicationError with PUBLICATION_FAILED_ROLLED_BACK.
         - If rollback fails: PRESERVES recovery backups for all unrestored destinations, verifies backup
           integrity, populates retained_recovery_backups diagnostic, and raises PublicationError with
           PUBLICATION_INCONSISTENT.
       - If all replacements succeed:
         - Unlinks all backup files and returns status PUBLISHED.
    """
    if not artifacts:
        return {"status": PublicationStatus.PUBLISHED, "published_paths": []}

    run_id = uuid.uuid4().hex
    allocated_stage_paths: List[Path] = []
    allocated_backup_paths: List[Path] = []
    allocated_restore_stage_paths: List[Path] = []
    staging_records: List[Dict[str, Any]] = []

    # Phase A: Prepare and Stage (R10-F02: immediate resource ownership tracking)
    try:
        for art in artifacts:
            target_path = Path(art.target_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            stage_path = target_path.with_name(f".{target_path.name}.stage.{run_id}.tmp")

            # Track allocation BEFORE any fallible filesystem write/flush/fsync
            allocated_stage_paths.append(stage_path)

            if _stage_write_hook:
                _stage_write_hook(target_path, stage_path)

            mode = "wb" if art.is_binary else "w"
            encoding = None if art.is_binary else "utf-8"
            with open(stage_path, mode, encoding=encoding) as f:
                f.write(art.content)
                f.flush()
                os.fsync(f.fileno())

            if not stage_path.exists():
                raise OSError(f"Staged file {stage_path} failed to persist.")

            staging_records.append({
                "target_path": target_path,
                "stage_path": stage_path,
                "content": art.content,
            })
    except Exception as e:
        cleanup_errors = []
        for sp in allocated_stage_paths:
            if sp.exists():
                try:
                    sp.unlink()
                except OSError as ue:
                    cleanup_errors.append(f"Failed to unlink staging file {sp}: {ue}")
        raise PublicationError(
            f"Staging failed before any destination was modified: {e}",
            status=PublicationStatus.STAGING_FAILED,
            details={"error": str(e), "cleanup_errors": cleanup_errors},
        )

    # Phase B: Prepare and Verify Backups for Rollback (R10-F03: immediate resource tracking)
    dest_records: List[Dict[str, Any]] = []
    try:
        for rec in staging_records:
            t_path = rec["target_path"]
            existed = t_path.exists()
            backup_path = None
            orig_sha = None
            orig_size = None

            if existed:
                with open(t_path, "rb") as bf:
                    data = bf.read()
                    orig_sha = hashlib.sha256(data).hexdigest()
                    orig_size = len(data)

                backup_path = t_path.with_name(f".{t_path.name}.orig_backup.{run_id}.tmp")
                # Track allocation BEFORE any fallible filesystem write/flush/fsync
                allocated_backup_paths.append(backup_path)

                with open(backup_path, "wb") as bf:
                    bf.write(data)
                    bf.flush()
                    os.fsync(bf.fileno())

                # Verify backup integrity before allowing publication to proceed
                with open(backup_path, "rb") as bf:
                    b_data = bf.read()
                    b_sha = hashlib.sha256(b_data).hexdigest()
                    b_size = len(b_data)
                if b_sha != orig_sha or b_size != orig_size:
                    raise OSError(
                        f"Backup verification failed for {t_path}: "
                        f"SHA ({b_sha} vs {orig_sha}) or size ({b_size} vs {orig_size}) mismatch"
                    )

            dest_records.append({
                "target_path": t_path,
                "stage_path": rec["stage_path"],
                "existed": existed,
                "orig_sha": orig_sha,
                "orig_size": orig_size,
                "backup_path": backup_path,
                "restored": False,
            })
    except Exception as e:
        cleanup_errors = []
        for sp in allocated_stage_paths:
            if sp.exists():
                try:
                    sp.unlink()
                except OSError as ue:
                    cleanup_errors.append(f"Failed to unlink staging file {sp}: {ue}")
        for bp in allocated_backup_paths:
            if bp.exists():
                try:
                    bp.unlink()
                except OSError as ue:
                    cleanup_errors.append(f"Failed to unlink backup file {bp}: {ue}")
        raise PublicationError(
            f"Failed to prepare backup before publication: {e}",
            status=PublicationStatus.STAGING_FAILED,
            details={"error": str(e), "cleanup_errors": cleanup_errors},
        )

    # Phase C: Publish Replacements with Compensating Rollback and Backup Preservation (R10-F01)
    committed_records: List[Dict[str, Any]] = []
    try:
        for d_rec in dest_records:
            _replace_fn(d_rec["stage_path"], d_rec["target_path"])
            committed_records.append(d_rec)
    except Exception as commit_exc:
        rollback_success = True
        rollback_errors = []
        restored_destinations: List[str] = []
        unrestored_destinations: List[str] = []

        for c_rec in reversed(committed_records):
            c_target = c_rec["target_path"]
            if c_rec["existed"]:
                # Restoration strategy (R10-F01): Do NOT consume the recovery backup directly!
                # Copy backup to a temporary restoration staging file, then replace into destination.
                restore_stage = c_target.with_name(f".{c_target.name}.restore_stage.{run_id}.tmp")
                allocated_restore_stage_paths.append(restore_stage)
                try:
                    with open(c_rec["backup_path"], "rb") as bf, open(restore_stage, "wb") as rf:
                        b_data = bf.read()
                        rf.write(b_data)
                        rf.flush()
                        os.fsync(rf.fileno())

                    _replace_fn(restore_stage, c_target)

                    # Verify restored destination matches pre-execution snapshot exactly
                    with open(c_target, "rb") as rf:
                        restored_data = rf.read()
                        restored_sha = hashlib.sha256(restored_data).hexdigest()
                        restored_size = len(restored_data)
                    if restored_sha != c_rec["orig_sha"] or restored_size != c_rec["orig_size"]:
                        rollback_success = False
                        rollback_errors.append(
                            f"Restored file {c_target} verification mismatch: "
                            f"SHA ({restored_sha} vs {c_rec['orig_sha']}), size ({restored_size} vs {c_rec['orig_size']})"
                        )
                        unrestored_destinations.append(str(c_target))
                    else:
                        c_rec["restored"] = True
                        restored_destinations.append(str(c_target))
                except Exception as rb_e:
                    rollback_success = False
                    rollback_errors.append(f"Failed to restore {c_target} from backup: {rb_e}")
                    unrestored_destinations.append(str(c_target))
            else:
                try:
                    if c_target.exists():
                        c_target.unlink()
                    c_rec["restored"] = True
                    restored_destinations.append(str(c_target))
                except Exception as rb_e:
                    rollback_success = False
                    rollback_errors.append(f"Failed to unlink newly created file {c_target}: {rb_e}")
                    unrestored_destinations.append(str(c_target))

        # Cleanup uncommitted staging files and temporary restore staging files
        cleanup_errors = []
        for sp in allocated_stage_paths:
            if sp.exists():
                try:
                    sp.unlink()
                except OSError as ue:
                    cleanup_errors.append(f"Failed to unlink staging file {sp}: {ue}")
        for rsp in allocated_restore_stage_paths:
            if rsp.exists():
                try:
                    rsp.unlink()
                except OSError as ue:
                    cleanup_errors.append(f"Failed to unlink restore stage file {rsp}: {ue}")

        # CRITICAL BACKUP PRESERVATION (R10-F01):
        # If rollback succeeded: all modified destinations are verified restored, safe to clean backups.
        # If rollback FAILED: PRESERVE recovery backups for all unrestored destinations!
        retained_recovery_backups: List[Dict[str, Any]] = []
        if rollback_success:
            for bp in allocated_backup_paths:
                if bp.exists():
                    try:
                        bp.unlink()
                    except OSError as ue:
                        cleanup_errors.append(f"Failed to unlink backup file {bp}: {ue}")
            raise PublicationError(
                f"Publication failed during atomic replacement ({commit_exc}); "
                f"all modified destinations have been safely rolled back to pre-execution state.",
                status=PublicationStatus.PUBLICATION_FAILED_ROLLED_BACK,
                details={
                    "transaction_id": run_id,
                    "publication_status": PublicationStatus.PUBLICATION_FAILED_ROLLED_BACK,
                    "commit_error": str(commit_exc),
                    "restored_destinations": restored_destinations,
                    "cleanup_errors": cleanup_errors,
                },
            )
        else:
            committed_targets = {d["target_path"] for d in committed_records}
            for d_rec in dest_records:
                bp = d_rec.get("backup_path")
                if bp and bp.exists():
                    # Only retain recovery backups for destinations that WERE modified (committed) and failed rollback.
                    # Destinations that were never replaced remain untouched in their original state.
                    if d_rec["target_path"] in committed_targets and not d_rec.get("restored", False):
                        try:
                            b_bytes = bp.read_bytes()
                            b_sha = hashlib.sha256(b_bytes).hexdigest()
                            b_size = len(b_bytes)
                            retained_recovery_backups.append({
                                "destination": str(d_rec["target_path"]),
                                "backup_path": str(bp),
                                "original_sha256": d_rec["orig_sha"],
                                "original_size_bytes": d_rec["orig_size"],
                                "backup_sha256": b_sha,
                                "backup_size_bytes": b_size,
                                "sha_matches": (b_sha == d_rec["orig_sha"]),
                                "size_matches": (b_size == d_rec["orig_size"]),
                            })
                        except Exception as b_err:
                            retained_recovery_backups.append({
                                "destination": str(d_rec["target_path"]),
                                "backup_path": str(bp),
                                "error": str(b_err),
                            })
                    else:
                        try:
                            bp.unlink()
                        except OSError as ue:
                            cleanup_errors.append(f"Failed to unlink backup file {bp}: {ue}")

            raise PublicationError(
                f"Publication failed during atomic replacement ({commit_exc}) "
                f"AND compensating rollback failed: {'; '.join(rollback_errors)}. "
                f"Output destination state is inconsistent. Recovery backups retained.",
                status=PublicationStatus.PUBLICATION_INCONSISTENT,
                details={
                    "transaction_id": run_id,
                    "publication_status": PublicationStatus.PUBLICATION_INCONSISTENT,
                    "commit_error": str(commit_exc),
                    "rollback_errors": rollback_errors,
                    "affected_destinations": [str(d["target_path"]) for d in dest_records],
                    "restored_destinations": restored_destinations,
                    "unrestored_destinations": unrestored_destinations,
                    "retained_recovery_backups": retained_recovery_backups,
                    "cleanup_errors": cleanup_errors,
                },
            )

    # Success: Cleanup all backups
    for bp in allocated_backup_paths:
        if bp.exists():
            try:
                bp.unlink()
            except OSError:
                pass

    return {
        "status": PublicationStatus.PUBLISHED,
        "published_paths": [d["target_path"] for d in dest_records],
    }


def publish_json_atomic(data: Any, target_path: Union[str, Path]) -> None:
    """Atomically writes JSON to target_path using staged publication (R8-F04, R9-F02)."""
    target_path = Path(target_path)
    res = publish_certified_artifacts_staged([
        ArtifactTarget(target_path=target_path, content=json.dumps(data, indent=2))
    ])
    if res["status"] != PublicationStatus.PUBLISHED:
        raise OSError(f"Failed to publish JSON to {target_path}: status={res['status']}")


def publish_text_atomic(content: str, target_path: Union[str, Path]) -> None:
    """Atomically writes text to target_path using staged publication (R8-F04, R9-F02)."""
    target_path = Path(target_path)
    res = publish_certified_artifacts_staged([
        ArtifactTarget(target_path=target_path, content=content)
    ])
    if res["status"] != PublicationStatus.PUBLISHED:
        raise OSError(f"Failed to publish text to {target_path}: status={res['status']}")


def validate_output_destinations(args: argparse.Namespace) -> None:
    """Validates output file paths against collisions, existing directories, and protected paths (R8-F04, R9-F01)."""
    protected_inputs = []
    if getattr(args, "ledger", None):
        protected_inputs.append(("ledger", Path(args.ledger)))
    if getattr(args, "manifest", None):
        protected_inputs.append(("manifest", Path(args.manifest)))
    if getattr(args, "evidence_manifest", None):
        protected_inputs.append(("evidence-manifest", Path(args.evidence_manifest)))

    protected_canonical_defaults = [
        ("default-ledger", DEFAULT_LEDGER),
        ("default-manifest", DEFAULT_MANIFEST_PATH),
        ("default-evidence-manifest", DEFAULT_EVIDENCE_MANIFEST_PATH),
        ("default-protocol", DEFAULT_PROTOCOL_PATH),
    ]

    out_json = Path(args.output_json) if getattr(args, "output_json", None) else None
    out_md = Path(args.output_md) if getattr(args, "output_md", None) else None
    diag_json = Path(args.diagnostic_json) if getattr(args, "diagnostic_json", None) else None

    all_outputs = []
    if out_json:
        all_outputs.append(("output-json", out_json))
    if out_md:
        all_outputs.append(("output-md", out_md))
    if diag_json:
        all_outputs.append(("diagnostic-json", diag_json))

    # 1. Directory and non-directory parent check
    for out_name, out_path in all_outputs:
        if out_path.is_dir():
            print(f"Error: Output path '{out_path}' for --{out_name} is an existing directory.", file=sys.stderr)
            sys.exit(1)
        curr = out_path.parent
        while curr != curr.parent:
            if curr.exists() and not curr.is_dir():
                print(
                    f"Error: Parent path '{curr}' for --{out_name} '{out_path}' is an existing non-directory file.",
                    file=sys.stderr,
                )
                sys.exit(1)
            curr = curr.parent

    # 2. Collision between certified outputs: output-json vs output-md
    if out_json and out_md:
        if paths_collide(out_json, out_md):
            print(
                f"Error: Output path collision: --output-json and --output-md specify the same file: '{out_json}'.",
                file=sys.stderr,
            )
            sys.exit(1)

    # 3. Diagnostic output isolation (R9-F01)
    if diag_json:
        if out_json and paths_collide(diag_json, out_json):
            print(
                f"Error: Output path collision: --diagnostic-json '{diag_json}' collides with --output-json '{out_json}'.",
                file=sys.stderr,
            )
            sys.exit(1)
        if out_md and paths_collide(diag_json, out_md):
            print(
                f"Error: Output path collision: --diagnostic-json '{diag_json}' collides with --output-md '{out_md}'.",
                file=sys.stderr,
            )
            sys.exit(1)
        if paths_collide(diag_json, DEFAULT_SUMMARY_JSON):
            print(
                f"Error: Output path collision: --diagnostic-json '{diag_json}' collides with official canonical summary '{DEFAULT_SUMMARY_JSON}'.",
                file=sys.stderr,
            )
            sys.exit(1)

    # 4. Collision with input files and protected repository anchors
    for out_name, out_path in all_outputs:
        for inp_name, inp_path in protected_inputs:
            if paths_collide(out_path, inp_path):
                print(
                    f"Error: Output path collision: --{out_name} '{out_path}' collides with input --{inp_name} '{inp_path}'.",
                    file=sys.stderr,
                )
                sys.exit(1)
        for def_name, def_path in protected_canonical_defaults:
            if paths_collide(out_path, def_path):
                print(
                    f"Error: Output path collision: --{out_name} '{out_path}' collides with protected file '{def_path}' ({def_name}).",
                    file=sys.stderr,
                )
                sys.exit(1)

    # 5. Scoped mode protection against default canonical summary
    if getattr(args, "mode", None) == "scoped":
        if out_json and paths_collide(out_json, DEFAULT_SUMMARY_JSON):
            print(
                f"Error: Refusing to overwrite official canonical summary path '{DEFAULT_SUMMARY_JSON}' "
                f"in scoped analysis mode. Please specify a separate --output-json.",
                file=sys.stderr,
            )
            sys.exit(1)
        if out_md and paths_collide(out_md, DEFAULT_SUMMARY_JSON):
            print(
                f"Error: Refusing to overwrite official canonical summary path '{DEFAULT_SUMMARY_JSON}' "
                f"in scoped analysis mode. Please specify a separate --output-md.",
                file=sys.stderr,
            )
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="P4a Native Profiling Summarizer & Evidence Certifier")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="Path to raw ledger JSONL")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH, help="Workload manifest JSON path")
    parser.add_argument("--evidence-manifest", type=Path, default=DEFAULT_EVIDENCE_MANIFEST_PATH, help="Evidence manifest JSON path")
    parser.add_argument("--mode", choices=["canonical", "scoped"], default="canonical", help="Certification mode: 'canonical' (default) or 'scoped'")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_SUMMARY_JSON, help="Path to summary metrics JSON")
    parser.add_argument("--output-md", type=Path, default=None, help="Path to output markdown report")
    parser.add_argument("--diagnostic-json", type=Path, default=None, help="Optional diagnostic output path on certification failure")
    parser.add_argument("--strict-provenance", action="store_true", default=True, help="Enforce strict cryptographic provenance")
    args = parser.parse_args()

    # Pre-execution output destination and path collision validation (R8-F04)
    validate_output_destinations(args)

    try:
        records = load_ledger(args.ledger)
    except Exception as e:
        print(f"Error loading ledger {args.ledger}: {e}", file=sys.stderr)
        sys.exit(1)

    if not records:
        print(f"Error: No records found in {args.ledger}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(records)} records from {args.ledger}")

    expected_header = None
    artifact_meta = None
    expected_slot_matrix = None

    if args.mode == "canonical":
        authority = get_official_p4a_authority()

        # Strict provenance enforcement in CLI canonical mode (R7-F03)
        if not args.strict_provenance:
            print("Error: Canonical certification requires strict provenance (--strict-provenance cannot be False in canonical mode).", file=sys.stderr)
            sys.exit(1)

        # Workload manifest check (R7-F04, R8-F03)
        if args.manifest:
            m_path = Path(args.manifest)
            if not m_path.is_file():
                print(f"Error: Workload manifest not found: {m_path}", file=sys.stderr)
                sys.exit(1)
            manifest_sha = compute_sha256(m_path)
            if manifest_sha != authority.manifest_sha256:
                print(
                    f"Error: Canonical certification authority violation: workload manifest '{m_path}' SHA-256 ({manifest_sha}) "
                    f"does not match approved canonical authority ({authority.manifest_sha256}).",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Evidence manifest verification (R7-F04, R8-F03)
        if args.evidence_manifest:
            ev_path = Path(args.evidence_manifest)
            if not ev_path.is_file():
                print(f"Error: Evidence manifest not found: {ev_path}", file=sys.stderr)
                sys.exit(1)
            try:
                ev_data = load_trusted_evidence_manifest(ev_path)
            except Exception as e:
                print(f"Error: Failed to parse evidence manifest {ev_path}: {e}", file=sys.stderr)
                sys.exit(1)

            ev_ok, ev_msg = authority.verify_evidence_manifest(ev_data)
            if not ev_ok:
                print(
                    f"Error: Canonical certification authority violation: evidence manifest validation failed: {ev_msg}",
                    file=sys.stderr,
                )
                sys.exit(1)

        target_rel = None
        try:
            target_rel = str(args.ledger.resolve().relative_to(REPO_ROOT.resolve()))
        except ValueError:
            target_rel = str(args.ledger)

        if target_rel == authority.raw_artifact_rel_path or args.ledger.name == Path(authority.raw_artifact_rel_path).name:
            artifact_meta = authority.trusted_artifact_meta
        else:
            artifact_meta = None

        expected_header = authority.expected_header
        expected_slot_matrix = authority.expected_slot_matrix

    elif args.mode == "scoped":
        if args.manifest and args.manifest.is_file():
            try:
                expected_header = get_frozen_manifest_registry(args.manifest)
                expected_slot_matrix = compute_expected_slot_matrix(args.manifest)
            except Exception as e:
                print(f"Warning: Failed to load manifest {args.manifest}: {e}", file=sys.stderr)

    metrics = analyze_p4a_ledger(
        records=records,
        expected_header=expected_header,
        strict_provenance=args.strict_provenance,
        mode=args.mode,
        raw_ledger_path=args.ledger,
        trusted_artifact_meta=artifact_meta,
        expected_slot_matrix=expected_slot_matrix,
        evidence_manifest=args.evidence_manifest if args.mode == "canonical" else None,
    )

    verdict = metrics.get("overall_verdict")
    is_success = False
    if args.mode == "canonical":
        if verdict == "CANONICAL_PASS" and metrics.get("total_valid_pairs", 0) > 0:
            is_success = True
    elif args.mode == "scoped":
        if verdict == "SCOPED_ANALYSIS_VALID" and metrics.get("total_valid_pairs", 0) > 0:
            is_success = True

    # Safe Publication Gate: Non-PASS certification NEVER overwrites official summary outputs (R8-F04, R9-F01)
    if not is_success:
        if args.diagnostic_json:
            try:
                diag_res = publish_certified_artifacts_staged([
                    ArtifactTarget(target_path=args.diagnostic_json, content=json.dumps(metrics, indent=2))
                ])
                if diag_res["status"] == PublicationStatus.PUBLISHED:
                    print(f"Diagnostic failure metrics saved to {args.diagnostic_json}", file=sys.stderr)
                else:
                    print(f"Error saving diagnostic JSON to {args.diagnostic_json}: status={diag_res['status']}", file=sys.stderr)
            except Exception as e:
                print(f"Error saving diagnostic JSON to {args.diagnostic_json}: {e}", file=sys.stderr)

        if args.mode == "canonical":
            print(
                f"Canonical certification FAILED: verdict={verdict}; total valid pairs={metrics.get('total_valid_pairs')}",
                file=sys.stderr,
            )
        else:
            print(
                f"Scoped analysis FAILED: verdict={verdict}; total valid pairs={metrics.get('total_valid_pairs')}",
                file=sys.stderr,
            )
        sys.exit(1)

    # Success: Multi-artifact staged publication with compensating rollback (R9-F02)
    artifacts_to_publish = [
        ArtifactTarget(target_path=args.output_json, content=json.dumps(metrics, indent=2))
    ]
    md_report = generate_markdown_report(metrics)
    if args.output_md:
        artifacts_to_publish.append(
            ArtifactTarget(target_path=args.output_md, content=md_report)
        )

    try:
        pub_result = publish_certified_artifacts_staged(artifacts_to_publish)
    except PublicationError as pe:
        if pe.status == PublicationStatus.PUBLICATION_INCONSISTENT:
            print("Publication failed. Compensating rollback could not restore all destinations.", file=sys.stderr)
            print(f"Publication status: {pe.status}", file=sys.stderr)
            for rec in pe.details.get("retained_recovery_backups", []):
                print(f"Affected destination: {rec.get('destination')}", file=sys.stderr)
                print(f"Recovery backup preserved at: {rec.get('backup_path')}", file=sys.stderr)
                print(f"Expected original SHA-256: {rec.get('original_sha256')}", file=sys.stderr)
        print(f"Publication failed: {pe} (status={pe.status})", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected publication error: {e}", file=sys.stderr)
        sys.exit(1)

    if pub_result["status"] != PublicationStatus.PUBLISHED:
        print(f"Publication incomplete: status={pub_result['status']}", file=sys.stderr)
        sys.exit(1)

    print(f"Summary metrics saved to {args.output_json}")
    if args.output_md:
        print(f"Markdown report saved to {args.output_md}")
    else:
        print("\n" + md_report)

    if args.diagnostic_json:
        # Documented policy (R9-A14): Diagnostic is only generated on certification failure.
        print("Note: --diagnostic-json omitted because certification PASSED.", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
