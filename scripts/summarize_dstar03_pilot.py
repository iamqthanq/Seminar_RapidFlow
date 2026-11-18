#!/usr/bin/env python3
"""
scripts/summarize_dstar03_pilot.py — Reconciler and Summary Generator for DSTAR-03 Pilot (v2).

Reads:
- configs/dstar03_pilot_manifest.json
- reports/dstar03/attempts.jsonl

Produces:
1. reports/dstar03/correctness_summary.json (independent M0 verification audit)
2. reports/dstar03/pilot_summary.json (execution completeness, latency medians, budget ratios)
3. reports/dstar03/pilot_results.csv (comprehensive tabular metrics per workload)

Enforces (Remediation F01, F05, F06):
- Strict reconciliation: exactly 390 slots accounted for (65 workloads x 6 runs).
- Manifest integrity: exactly 65 workloads (40 syn, 5 wit, 20 netflow) with contiguous case indices 0..64.
- Zero swallowed errors: malformed JSON lines, duplicate/conflicting completions, unknown IDs,
  or completions missing start records cause reconciliation failure.
- Failures (TIMEOUT, RESOURCE_LIMIT, ERROR, INTERRUPTED, CORRECTNESS_FAIL) never become successful slots.
- PILOT_COMPLETED requires all 390 slots to be successful measured slots; never passes on all-timeout or empty ledger.
- Correctness never uses all([]) on empty list; requires exactly 65 verified workloads.
- Metric renamed to budget_units (engine safety budget counters, not invariant algorithmic work).
- CSV formatting with lineterminator='\\n' to prevent trailing carriage returns and whitespace issues.
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

    # 1. Validate Manifest
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    workload_entries = manifest.get("workloads", [])
    total_workloads = len(workload_entries)
    if total_workloads != 65:
        raise ValueError(
            f"Manifest registered workloads count {total_workloads} != 65 required"
        )

    counts = {"SYNTHETIC_MATRIX": 0, "STRUCTURED_WITNESS": 0, "NETFLOW_PROBE": 0}
    case_indices: Set[int] = set()
    workload_ids: List[str] = []
    workload_map: Dict[str, Dict[str, Any]] = {}

    for w in workload_entries:
        fam = w.get("family")
        if fam in counts:
            counts[fam] += 1
        else:
            raise ValueError(f"Unknown workload family {fam} in {w.get('workload_id')}")

        c_idx = w.get("case_index")
        if c_idx is None or c_idx in case_indices:
            raise ValueError(f"Duplicate or missing case_index: {c_idx}")
        case_indices.add(c_idx)

        w_id = w.get("workload_id")
        if not w_id or w_id in workload_map:
            raise ValueError(f"Duplicate or missing workload_id: {w_id}")
        workload_ids.append(w_id)
        workload_map[w_id] = w

    if counts != {"SYNTHETIC_MATRIX": 40, "STRUCTURED_WITNESS": 5, "NETFLOW_PROBE": 20}:
        raise ValueError(f"Workload counts breakdown mismatch: {counts}")

    if sorted(case_indices) != list(range(65)):
        raise ValueError(f"Case indices not contiguous 0..64: {sorted(case_indices)}")

    required_slots_count = 65 * 6  # exactly 390 slots
    expected_slot_ids: Set[str] = set()
    for w in workload_entries:
        w_id = w["workload_id"]
        for p in (1, 2, 3):
            rep = p - 1
            for eng in ("m1", "m2"):
                expected_slot_ids.add(f"{w_id}_{eng}_pair{p}_rep{rep}")

    assert len(expected_slot_ids) == required_slots_count

    # 2. Read and reconcile attempts ledger
    attempts_by_id: Dict[str, Dict[str, Any]] = {}
    started_ids: Set[str] = set()
    header_record: Optional[Dict[str, Any]] = None
    ledger_records_count = 0
    duplicate_completions: List[str] = []
    unknown_completions: List[str] = []
    completions_missing_start: List[str] = []

    if not ledger_path.exists():
        raise FileNotFoundError(f"Ledger file not found: {ledger_path}")

    with ledger_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ledger_records_count += 1
            try:
                rec = json.loads(line)
            except Exception as exc:
                raise ValueError(f"Corrupt JSON at line {line_no} in ledger {ledger_path}: {exc}")

            rec_type = rec.get("record_type")
            att_id = rec.get("attempt_id")

            if rec_type == "SUPERVISOR_HEADER":
                if header_record is None:
                    header_record = rec

            elif rec_type == "ATTEMPT_STARTED":
                if not att_id:
                    raise ValueError(f"Line {line_no}: ATTEMPT_STARTED missing attempt_id")
                started_ids.add(att_id)

            elif rec_type == "ATTEMPT_COMPLETED":
                if not att_id:
                    raise ValueError(f"Line {line_no}: ATTEMPT_COMPLETED missing attempt_id")
                if att_id not in expected_slot_ids:
                    unknown_completions.append(att_id)
                if att_id in attempts_by_id:
                    duplicate_completions.append(att_id)
                if att_id not in started_ids:
                    completions_missing_start.append(att_id)

                attempts_by_id[att_id] = rec

    # Check for ledger anomalies
    if duplicate_completions:
        raise ValueError(
            f"Ledger contains duplicate completion records for attempts: {duplicate_completions[:5]}"
        )
    if unknown_completions:
        raise ValueError(
            f"Ledger contains unknown attempt IDs not in manifest: {unknown_completions[:5]}"
        )
    if completions_missing_start:
        raise ValueError(
            f"Ledger contains completions with no matching ATTEMPT_STARTED record: {completions_missing_start[:5]}"
        )

    # Header Provenance Check (F02)
    header_commit: Optional[str] = None
    if header_record is not None or len(attempts_by_id) > 0:
        if header_record is None:
            raise ValueError(
                f"Ledger {ledger_path} contains {len(attempts_by_id)} completion records but is missing required SUPERVISOR_HEADER"
            )

        header_commit = header_record.get("git_commit")
        if not header_commit or not isinstance(header_commit, str):
            raise ValueError("Ledger SUPERVISOR_HEADER missing or invalid 'git_commit'")

        header_source_hashes = header_record.get("source_hashes")
        if not header_source_hashes or not isinstance(header_source_hashes, dict) or len(header_source_hashes) == 0:
            raise ValueError("Ledger SUPERVISOR_HEADER missing or empty 'source_hashes'")

        # Verify source_hashes against git show <header_commit>:<path>
        import hashlib
        import subprocess

        for rel_path, expected_hash in header_source_hashes.items():
            if not isinstance(expected_hash, str) or not expected_hash.strip():
                raise ValueError(f"Empty hash in SUPERVISOR_HEADER source_hashes for {rel_path}")
            proc = subprocess.run(
                ["git", "show", f"{header_commit}:{rel_path}"],
                capture_output=True,
                cwd=REPO_ROOT,
            )
            if proc.returncode != 0:
                err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
                raise ValueError(
                    f"Provenance verification failed: cannot retrieve {rel_path} from commit {header_commit} via git show: {err_msg}"
                )
            calc_hash = hashlib.sha256(proc.stdout).hexdigest()
            if calc_hash != expected_hash:
                raise ValueError(
                    f"Provenance verification failed: header source hash mismatch for {rel_path}: "
                    f"declared {expected_hash} != git show blob {calc_hash}"
                )

        # Verify manifest hash against current manifest
        if header_record.get("manifest_sha256") != manifest.get("manifest_sha256"):
            raise ValueError(
                f"Ledger header manifest_sha256 {header_record.get('manifest_sha256')} != manifest {manifest.get('manifest_sha256')}"
            )

    # 3. Reconcile slots
    status_counts: Dict[str, int] = {}
    workload_attempts: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {
        w_id: {"m1": {}, "m2": {}} for w_id in workload_ids
    }
    missing_slots: List[str] = []
    successful_measured_slots_count = 0
    failed_slots_count = 0

    for w in workload_entries:
        w_id = w["workload_id"]
        case_idx = w["case_index"]
        stream_len = w["stream_length"]

        pair_patterns = [
            ("m1", "m2") if (case_idx % 2 == 0) else ("m2", "m1"),
            ("m2", "m1") if (case_idx % 2 == 0) else ("m1", "m2"),
            ("m1", "m2") if (case_idx % 2 == 0) else ("m2", "m1"),
        ]

        for pair_idx, (eng_a, eng_b) in enumerate(pair_patterns, start=1):
            rep_idx = pair_idx - 1
            for sub_rep, eng in enumerate([eng_a, eng_b], start=1):
                att_id = f"{w_id}_{eng}_pair{pair_idx}_rep{rep_idx}"
                expected_order = sub_rep

                if att_id not in attempts_by_id:
                    missing_slots.append(att_id)
                    status_counts["MISSING"] = status_counts.get("MISSING", 0) + 1
                    continue

                rec = attempts_by_id[att_id]
                st = rec.get("status", "UNKNOWN")

                # Identity check with scheduled slot
                rec_w_id = rec.get("workload_id")
                rec_eng = rec.get("engine")
                rec_pair = rec.get("pair_index")
                rec_rep = rec.get("rep_index")
                rec_order = rec.get("order_in_pair")

                if (
                    rec_w_id != w_id
                    or rec_eng != eng
                    or rec_pair != pair_idx
                    or rec_rep != rep_idx
                    or rec_order != expected_order
                ):
                    raise ValueError(
                        f"Integrity error for slot {att_id}: scheduled (workload={w_id}, engine={eng}, "
                        f"pair={pair_idx}, rep={rep_idx}, order={expected_order}) != recorded "
                        f"(workload={rec_w_id}, engine={rec_eng}, pair={rec_pair}, rep={rec_rep}, order={rec_order})"
                    )

                # Check worker provenance against header commit (F02)
                if header_commit:
                    worker_prov = rec.get("git_provenance")
                    worker_commit = worker_prov.get("commit") if isinstance(worker_prov, dict) else None
                    if worker_commit != header_commit:
                        raise ValueError(
                            f"Integrity error for slot {att_id}: worker commit {worker_commit} != header commit {header_commit}"
                        )

                # Successful slot integrity verification (F01)
                if st in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE"):
                    # 1. exit_code == 0
                    rec_exit = rec.get("exit_code")
                    if rec_exit != 0:
                        raise ValueError(
                            f"Integrity error for slot {att_id}: status is '{st}' but exit_code is {rec_exit}"
                        )

                    # 2. subprocess_status == "OK"
                    rec_sub_st = rec.get("subprocess_status")
                    if rec_sub_st != "OK":
                        raise ValueError(
                            f"Integrity error for slot {att_id}: status is '{st}' but subprocess_status is '{rec_sub_st}'"
                        )

                    # 3. warmup_status == "SUCCESS"
                    rec_warmup_st = rec.get("warmup_status")
                    if rec_warmup_st != "SUCCESS":
                        raise ValueError(
                            f"Integrity error for slot {att_id}: status is '{st}' but warmup_status is '{rec_warmup_st}'"
                        )

                    # 4. Payload step count matches stream length
                    steps = rec.get("steps")
                    if not isinstance(steps, list) or len(steps) != stream_len:
                        raise ValueError(
                            f"Integrity error for slot {att_id}: payload steps count "
                            f"{len(steps) if isinstance(steps, list) else None} != workload stream length {stream_len}"
                        )

                    # 5. Timing and counter validation
                    t_engine = rec.get("stream_engine_ms")
                    if not isinstance(t_engine, (int, float)) or isinstance(t_engine, bool) or t_engine < 0:
                        raise ValueError(
                            f"Integrity error for slot {att_id}: invalid stream_engine_ms {t_engine}"
                        )

                    t_worker = rec.get("worker_wall_ms")
                    if not isinstance(t_worker, (int, float)) or isinstance(t_worker, bool) or t_worker < 0:
                        raise ValueError(
                            f"Integrity error for slot {att_id}: invalid worker_wall_ms {t_worker}"
                        )

                    b_units = rec.get("total_budget_units")
                    if not isinstance(b_units, int) or isinstance(b_units, bool) or b_units < 0:
                        raise ValueError(
                            f"Integrity error for slot {att_id}: invalid total_budget_units {b_units}"
                        )

                    counters = rec.get("counters")
                    if not isinstance(counters, dict):
                        raise ValueError(
                            f"Integrity error for slot {att_id}: missing or invalid counters {type(counters)}"
                        )

                    status_counts[st] = status_counts.get(st, 0) + 1
                    workload_attempts[w_id][eng][pair_idx] = rec
                    successful_measured_slots_count += 1

                elif st in ("TIMEOUT", "RESOURCE_LIMIT", "ERROR", "INTERRUPTED", "CORRECTNESS_FAIL", "INPUT_INVALID"):
                    status_counts[st] = status_counts.get(st, 0) + 1
                    workload_attempts[w_id][eng][pair_idx] = rec
                    failed_slots_count += 1

                else:
                    raise ValueError(
                        f"Integrity error for slot {att_id}: unrecognized status '{st}'"
                    )

    # 4. M0 Correctness Summary Audit
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

        # Check fixture fingerprint against manifest (F02)
        declared_w_sha = w.get("sha256_workload")
        fixture_w_sha = w_fixture.get("sha256_workload")
        if not fixture_w_sha or fixture_w_sha != declared_w_sha:
            raise ValueError(
                f"Workload fixture fingerprint mismatch for {w_id}: "
                f"fixture {fixture_w_sha} != manifest {declared_w_sha}"
            )

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

    all_verified = (
        len(correctness_rows) == 65 and all(r["m0_verified"] for r in correctness_rows)
    )
    correctness_status = "CORRECTNESS_VERIFIED" if all_verified else "CORRECTNESS_FAIL"

    correctness_summary = {
        "status": correctness_status,
        "total_registered_workloads": total_workloads,
        "workloads_with_events": workloads_with_events,
        "workloads_zero_events": workloads_zero_events,
        "total_entered_events": total_entered_all,
        "total_exited_events": total_exited_all,
        "total_events": total_entered_all + total_exited_all,
        "per_workload": correctness_rows,
    }

    with (output_dir / "correctness_summary.json").open("w", encoding="utf-8") as f:
        json.dump(correctness_summary, f, indent=2, sort_keys=True)

    # 5. Detailed Per-Workload Metric Aggregation & CSV Generation
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

        # Budget units (engine safety counters)
        m1_budget = [
            rec.get("total_budget_units", rec.get("total_work_units", 0))
            for rec in m1_runs.values()
            if rec.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
        ]
        m2_budget = [
            rec.get("total_budget_units", rec.get("total_work_units", 0))
            for rec in m2_runs.values()
            if rec.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE")
        ]
        m1_b_med = statistics.median(m1_budget) if m1_budget else 0
        m2_b_med = statistics.median(m2_budget) if m2_budget else 0
        budget_ratio = round(m1_b_med / m2_b_med, 4) if m2_b_med > 0 else 0.0

        # Paired speedup ratios (matched pair where both sides succeeded)
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

        # Detailed counters sum across reps
        m1_evals = sum(rec.get("counters", {}).get("candidate_evaluations", 0) for rec in m1_runs.values())
        m2_evals = sum(rec.get("counters", {}).get("candidate_evaluations", 0) for rec in m2_runs.values())
        m2_edge_decisions = sum(rec.get("counters", {}).get("edge_decisions", 0) for rec in m2_runs.values())
        m2_missing_branches = sum(rec.get("counters", {}).get("missing_branches", 0) for rec in m2_runs.values())

        # Tags
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
            "m1_budget_units_median": m1_b_med,
            "m2_budget_units_median": m2_b_med,
            "budget_ratio_m1_over_m2": budget_ratio,
            "m1_evals_total": m1_evals,
            "m2_evals_total": m2_evals,
            "m2_edge_decisions_total": m2_edge_decisions,
            "m2_missing_branches_total": m2_missing_branches,
            "status": row_status,
            "tags": ";".join(tags),
        }
        csv_rows.append(csv_row)

        fam_stat = family_stats.setdefault(
            fam,
            {
                "workload_count": 0,
                "completed_workloads": 0,
                "speedup_ratios": [],
                "budget_ratios": [],
            },
        )
        fam_stat["workload_count"] += 1
        if row_status == "COMPLETED":
            fam_stat["completed_workloads"] += 1
            if speedup_ratio is not None:
                fam_stat["speedup_ratios"].append(speedup_ratio)
            if budget_ratio > 0:
                fam_stat["budget_ratios"].append(budget_ratio)

    # Write CSV with strict LF lineterminator to prevent git whitespace errors
    csv_path = output_dir / "pilot_results.csv"
    if csv_rows:
        fieldnames = list(csv_rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(csv_rows)

    # 6. Overall Pilot Summary & Completeness
    completed_attempts_count = len(attempts_by_id)
    # PILOT_COMPLETED strictly requires all 390 slots to have succeeded
    is_fully_completed = (
        completed_attempts_count == required_slots_count
        and not missing_slots
        and successful_measured_slots_count == required_slots_count
        and failed_slots_count == 0
    )
    execution_completeness = "PILOT_COMPLETED" if is_fully_completed else "INCOMPLETE"

    per_family_summary: Dict[str, Any] = {}
    for fam, st in family_stats.items():
        ratios = st["speedup_ratios"]
        b_ratios = st["budget_ratios"]
        per_family_summary[fam] = {
            "workload_count": st["workload_count"],
            "completed_workloads": st["completed_workloads"],
            "speedup_ratio_median": round(statistics.median(ratios), 4) if ratios else None,
            "speedup_ratio_min": round(min(ratios), 4) if ratios else None,
            "speedup_ratio_max": round(max(ratios), 4) if ratios else None,
            "budget_ratio_median": round(statistics.median(b_ratios), 4) if b_ratios else None,
        }

    pilot_summary = {
        "manifest_id": manifest["manifest_id"],
        "execution_completeness": execution_completeness,
        "correctness_status": correctness_status,
        "total_registered_workloads": total_workloads,
        "total_required_attempts": required_slots_count,
        "attempts_recorded": completed_attempts_count,
        "successful_measured_slots": successful_measured_slots_count,
        "failed_slots_count": failed_slots_count,
        "missing_slots_count": len(missing_slots),
        "status_breakdown": status_counts,
        "family_summary": per_family_summary,
        "scientific_scope": {
            "tiny_pilot_caps": manifest["limits"],
            "no_speedup_threshold_mandate": True,
            "exploratory_status": "Valid negative/inconclusive pilot outcomes retained truthfully",
            "netflow_zero_event_coverage_noted": True,
            "state_management_overhead_hypothesis": (
                "Differences in runtime between M1 and M2 may relate to Python state management "
                "overhead; this pilot does not isolate language overhead from algorithmic complexity."
            ),
        },
    }

    with (output_dir / "pilot_summary.json").open("w", encoding="utf-8") as f:
        json.dump(pilot_summary, f, indent=2, sort_keys=True)

    return pilot_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile and summarize DSTAR-03 pilot.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH, help="Path to manifest JSON.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="Path to attempts ledger JSONL.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for reports.")
    args = parser.parse_args()

    summary = summarize_pilot(manifest_path=args.manifest, ledger_path=args.ledger, output_dir=args.out_dir)
    print(json.dumps(summary, indent=2))

    success = (
        summary.get("execution_completeness") == "PILOT_COMPLETED"
        and summary.get("correctness_status") == "CORRECTNESS_VERIFIED"
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
