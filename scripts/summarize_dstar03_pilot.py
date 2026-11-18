#!/usr/bin/env python3
"""
scripts/summarize_dstar03_pilot.py — Reconciler and Summary Generator for DSTAR-03 Pilot.

Reads:
- configs/dstar03_pilot_manifest.json
- reports/dstar03/attempts.jsonl

Produces:
1. reports/dstar03/correctness_summary.json (M0 verification audit)
2. reports/dstar03/pilot_summary.json (execution completeness, latency medians, work ratios)
3. reports/dstar03/pilot_results.csv (comprehensive tabular metrics per workload)

Enforces:
- Strict reconciliation: exactly 390 slots accounted for (65 workloads x 6 runs).
- Zero-event and structured witness separation.
- Noise tagging for sub-millisecond latencies.
- Truthful reporting: no timeout substitution, no universal speedup claims.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
DEFAULT_LEDGER_PATH = REPO_ROOT / "reports" / "dstar03" / "attempts.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "reports" / "dstar03"


def compute_median_and_range(values: List[float]) -> Tuple[float, float, float]:
    """Returns (median, min, max)."""
    if not values:
        return 0.0, 0.0, 0.0
    return (
        round(statistics.median(values), 4),
        round(min(values), 4),
        round(max(values), 4),
    )


def summarize_pilot(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    output_dir: Path = DEFAULT_OUT_DIR,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    workload_entries = manifest["workloads"]
    workload_ids = [w["workload_id"] for w in workload_entries]
    workload_map = {w["workload_id"]: w for w in workload_entries}

    total_workloads = len(workload_entries)
    required_slots_count = total_workloads * 6

    # 1. Read attempts ledger
    attempts_by_id: Dict[str, Dict[str, Any]] = {}
    started_ids: Set[str] = set()
    raw_records_count = 0

    if ledger_path.exists():
        with ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw_records_count += 1
                try:
                    rec = json.loads(line)
                    rec_type = rec.get("record_type")
                    att_id = rec.get("attempt_id")
                    if rec_type == "ATTEMPT_STARTED":
                        started_ids.add(att_id)
                    elif rec_type == "ATTEMPT_COMPLETED":
                        attempts_by_id[att_id] = rec
                except Exception:
                    pass

    # 2. Reconcile slots
    status_counts: Dict[str, int] = {}
    workload_attempts: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {
        w_id: {"m1": {}, "m2": {}} for w_id in workload_ids
    }
    missing_slots: List[str] = []

    for w in workload_entries:
        w_id = w["workload_id"]
        for pair_idx in (1, 2, 3):
            rep_idx = pair_idx - 1
            for eng in ("m1", "m2"):
                att_id = f"{w_id}_{eng}_pair{pair_idx}_rep{rep_idx}"
                if att_id not in attempts_by_id:
                    missing_slots.append(att_id)
                    status_counts["MISSING"] = status_counts.get("MISSING", 0) + 1
                else:
                    rec = attempts_by_id[att_id]
                    st = rec.get("status", "UNKNOWN")
                    status_counts[st] = status_counts.get(st, 0) + 1
                    workload_attempts[w_id][eng][pair_idx] = rec

    # 3. M0 Correctness Summary
    correctness_rows: List[Dict[str, Any]] = []
    total_entered_all = 0
    total_exited_all = 0
    workloads_with_events = 0
    workloads_zero_events = 0

    for w in workload_entries:
        w_id = w["workload_id"]
        fixture_path = REPO_ROOT / w["relative_path"]
        with fixture_path.open("r", encoding="utf-8") as f:
            w_fixture = json.load(f)

        m0_info = w_fixture.get("m0_verification", {})
        verified = m0_info.get("verified", False)
        ent = m0_info.get("total_entered_events", 0)
        exi = m0_info.get("total_exited_events", 0)
        tot = ent + exi
        if tot > 0:
            workloads_with_events += 1
        else:
            workloads_zero_events += 1
        total_entered_all += ent
        total_exited_all += exi

        correctness_rows.append({
            "workload_id": w_id,
            "family": w["family"],
            "m0_verified": verified,
            "total_entered": ent,
            "total_exited": exi,
            "total_events": tot,
            "has_events": tot > 0,
        })

    correctness_summary = {
        "status": "CORRECTNESS_VERIFIED" if all(r["m0_verified"] for r in correctness_rows) else "FAIL",
        "total_registered_workloads": total_workloads,
        "workloads_with_events": workloads_with_events,
        "workloads_zero_events": workloads_zero_events,
        "total_entered_events": total_entered_all,
        "total_exited_events": total_exited_all,
        "total_events": total_entered_all + total_exited_all,
        "per_workload": correctness_rows,
    }

    with (output_dir / "correctness_summary.json").open("w", encoding="utf-8") as f:
        json.dump(correctness_summary, f, indent=2)

    # 4. Detailed Per-Workload Metric Aggregation & CSV Generation
    csv_rows: List[Dict[str, Any]] = []
    family_stats: Dict[str, Dict[str, Any]] = {}
    overall_speedup_ratios: List[float] = []

    for w in workload_entries:
        w_id = w["workload_id"]
        fam = w["family"]
        m1_runs = workload_attempts[w_id]["m1"]
        m2_runs = workload_attempts[w_id]["m2"]

        # Collect latency ms
        m1_times = [
            rec["stream_engine_ms"]
            for rec in m1_runs.values()
            if rec.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
        ]
        m2_times = [
            rec["stream_engine_ms"]
            for rec in m2_runs.values()
            if rec.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
        ]

        m1_med, m1_min, m1_max = compute_median_and_range(m1_times)
        m2_med, m2_min, m2_max = compute_median_and_range(m2_times)

        # Work units
        m1_work = [
            rec.get("total_work_units", 0)
            for rec in m1_runs.values()
            if rec.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
        ]
        m2_work = [
            rec.get("total_work_units", 0)
            for rec in m2_runs.values()
            if rec.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
        ]
        m1_w_med = statistics.median(m1_work) if m1_work else 0
        m2_w_med = statistics.median(m2_work) if m2_work else 0
        work_ratio = round(m1_w_med / m2_w_med, 4) if m2_w_med > 0 else 0.0

        # Paired ratios (for each matched pair where both sides succeeded)
        paired_ratios: List[float] = []
        for p in (1, 2, 3):
            if (
                p in m1_runs
                and p in m2_runs
                and m1_runs[p].get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
                and m2_runs[p].get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
            ):
                t1 = m1_runs[p]["stream_engine_ms"]
                t2 = m2_runs[p]["stream_engine_ms"]
                if t2 > 0:
                    paired_ratios.append(t1 / t2)

        speedup_ratio = round(statistics.median(paired_ratios), 4) if paired_ratios else None
        if speedup_ratio is not None:
            overall_speedup_ratios.append(speedup_ratio)

        # Detailed counters (sum across reps for analysis)
        m1_evals = sum(rec.get("counters", {}).get("candidate_evaluations", 0) for rec in m1_runs.values())
        m2_evals = sum(rec.get("counters", {}).get("candidate_evaluations", 0) for rec in m2_runs.values())
        m2_edge_decisions = sum(rec.get("counters", {}).get("edge_decisions", 0) for rec in m2_runs.values())
        m2_missing_branches = sum(rec.get("counters", {}).get("missing_branches", 0) for rec in m2_runs.values())

        # Status and tags
        tags: List[str] = []
        if w["has_events"]:
            tags.append("WITH_EVENTS")
        else:
            tags.append("ZERO_EVENTS")

        if fam == "STRUCTURED_WITNESS":
            tags.append("STRUCTURED_WITNESS")
        elif fam == "NETFLOW_PROBE" and not w["has_events"]:
            tags.append("NETFLOW_COVERAGE_INSUFFICIENT")

        if m1_med < 1.0 or m2_med < 1.0:
            tags.append("SUB_MILLISECOND_NOISY")

        row_status = "COMPLETED" if len(paired_ratios) == 3 else "INCOMPLETE"

        csv_row = {
            "workload_id": w_id,
            "family": fam,
            "graph_mode": w["graph_mode"],
            "query_name": w["query_name"],
            "k": w["k"],
            "data_vertices": w["data_vertices"],
            "initial_data_edges": w["initial_data_edges"],
            "stream_length": w["stream_length"],
            "total_events": w["total_events"],
            "m1_median_ms": m1_med,
            "m1_min_ms": m1_min,
            "m1_max_ms": m1_max,
            "m2_median_ms": m2_med,
            "m2_min_ms": m2_min,
            "m2_max_ms": m2_max,
            "speedup_ratio_m1_over_m2": speedup_ratio if speedup_ratio is not None else "N/A",
            "m1_work_units_median": m1_w_med,
            "m2_work_units_median": m2_w_med,
            "work_ratio_m1_over_m2": work_ratio,
            "m1_evals_total": m1_evals,
            "m2_evals_total": m2_evals,
            "m2_edge_decisions_total": m2_edge_decisions,
            "m2_missing_branches_total": m2_missing_branches,
            "status": row_status,
            "tags": ";".join(tags),
        }
        csv_rows.append(csv_row)

        # Family stats
        fam_stat = family_stats.setdefault(
            fam,
            {
                "workload_count": 0,
                "completed_workloads": 0,
                "speedup_ratios": [],
                "work_ratios": [],
            },
        )
        fam_stat["workload_count"] += 1
        if row_status == "COMPLETED":
            fam_stat["completed_workloads"] += 1
            if speedup_ratio is not None:
                fam_stat["speedup_ratios"].append(speedup_ratio)
            if work_ratio > 0:
                fam_stat["work_ratios"].append(work_ratio)

    # Write CSV
    csv_path = output_dir / "pilot_results.csv"
    if csv_rows:
        fieldnames = list(csv_rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    # 5. Overall Pilot Summary
    completed_attempts_count = len(attempts_by_id)
    execution_completeness = (
        "PILOT_COMPLETED"
        if completed_attempts_count == required_slots_count and not missing_slots
        else "INCOMPLETE"
    )

    per_family_summary: Dict[str, Any] = {}
    for fam, st in family_stats.items():
        ratios = st["speedup_ratios"]
        w_ratios = st["work_ratios"]
        per_family_summary[fam] = {
            "workload_count": st["workload_count"],
            "completed_workloads": st["completed_workloads"],
            "speedup_ratio_median": round(statistics.median(ratios), 4) if ratios else None,
            "speedup_ratio_min": round(min(ratios), 4) if ratios else None,
            "speedup_ratio_max": round(max(ratios), 4) if ratios else None,
            "work_ratio_median": round(statistics.median(w_ratios), 4) if w_ratios else None,
        }

    pilot_summary = {
        "manifest_id": manifest["manifest_id"],
        "execution_completeness": execution_completeness,
        "total_registered_workloads": total_workloads,
        "total_required_attempts": required_slots_count,
        "completed_attempts": completed_attempts_count,
        "missing_slots_count": len(missing_slots),
        "status_breakdown": status_counts,
        "family_summary": per_family_summary,
        "scientific_scope": {
            "tiny_pilot_caps": manifest["limits"],
            "no_speedup_threshold_mandate": True,
            "exploratory_status": "Valid negative/inconclusive pilot outcomes retained truthfully",
            "netflow_zero_event_coverage_noted": True,
        },
    }

    with (output_dir / "pilot_summary.json").open("w", encoding="utf-8") as f:
        json.dump(pilot_summary, f, indent=2)

    return pilot_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile and summarize DSTAR-03 pilot.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH, help="Path to manifest JSON.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="Path to attempts ledger JSONL.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for reports.")
    args = parser.parse_args()

    summary = summarize_pilot(manifest_path=args.manifest, ledger_path=args.ledger, output_dir=args.out_dir)
    print(json.dumps(summary, indent=2))

    sys.exit(0 if summary.get("execution_completeness") == "PILOT_COMPLETED" else 1)


if __name__ == "__main__":
    main()
