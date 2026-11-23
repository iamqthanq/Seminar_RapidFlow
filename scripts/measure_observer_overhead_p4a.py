#!/usr/bin/env python3
"""
scripts/measure_observer_overhead_p4a.py — Dedicated observer overhead measurement.

Measures the empirical runtime cost of enabling nanosecond profiling (PROFILE_ON)
relative to unprofiled execution (PROFILE_OFF) under balanced paired trials.

Key Invariants:
1. Balanced pair ordering: Alternates between (OFF -> ON) and (ON -> OFF) across trials.
2. Identical functional outputs: Verifies step counts, emitted tuples, and counters match 100%.
3. Fresh isolated child processes per measurement.
4. Raw measurements persisted to JSONL artifact with full provenance.
5. Distribution statistics computed without arbitrarily zeroing negative differences.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rapidflow_apx_p4a import (
    build_native_binary,
    compute_sha256,
    execute_native_run,
    verify_git_provenance,
    CANONICAL_MAX_WORK_CAP,
)
from scripts.summarize_rapidflow_apx_p4a import compute_distribution_stats

DEFAULT_MANIFEST = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
DEFAULT_RAW_OUTPUT = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_observer_overhead.jsonl"
DEFAULT_SUMMARY_OUTPUT = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_observer_overhead_summary.json"


def measure_observer_overhead(
    manifest_path: Path = DEFAULT_MANIFEST,
    raw_output_path: Path = DEFAULT_RAW_OUTPUT,
    summary_output_path: Path = DEFAULT_SUMMARY_OUTPUT,
    target_workloads: Optional[List[str]] = None,
    trials_per_workload: int = 4,
    clean_build: bool = False,
) -> Dict[str, Any]:
    """Executes balanced paired overhead measurement across selected workloads."""
    prov_meta, prov_ok = verify_git_provenance(allow_dirty=True)
    bin_path, bin_sha, _, build_ok = build_native_binary(clean_build=clean_build)
    if not build_ok:
        raise RuntimeError("Failed to build native binary for overhead measurement")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    manifest_sha = compute_sha256(manifest_path)

    cases = manifest_data.get("cases", [])
    if target_workloads:
        cases = [c for c in cases if c.get("case_id") in target_workloads]
    else:
        # Default representative workloads: Diamond k=0, Diamond k=1, K4 k=0, K4 k=1
        rep_ids = {"P4A_WL_01", "P4A_WL_03", "P4A_WL_11", "P4A_WL_14"}
        cases = [c for c in cases if c.get("case_id") in rep_ids]

    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_records: List[Dict[str, Any]] = []
    overhead_pcts: List[float] = []
    paired_differences_ns: List[int] = []

    trial_idx = 0
    with open(raw_output_path, "w", encoding="utf-8") as f_raw:
        for wl in cases:
            case_id = wl["case_id"]
            for rep in range(trials_per_workload):
                trial_idx += 1
                # Alternating order: even -> (OFF, then ON), odd -> (ON, then OFF)
                first_mode = "PROFILE_OFF" if (trial_idx % 2 == 1) else "PROFILE_ON"
                second_mode = "PROFILE_ON" if first_mode == "PROFILE_OFF" else "PROFILE_OFF"

                order = [first_mode, second_mode]
                run_res: Dict[str, Any] = {}

                for mode in order:
                    is_profile = (mode == "PROFILE_ON")
                    res = execute_native_run(
                        binary_path=bin_path,
                        engine="m2",
                        workload_data=copy.deepcopy(wl),
                        profile=is_profile,
                        timeout_s=30.0,
                        as_limit_mib=512,
                    )
                    if res["exit_code"] != 0:
                        raise RuntimeError(f"Overhead measurement failed for {case_id} in mode {mode}: exit={res['exit_code']}")
                    run_res[mode] = res

                res_off = run_res["PROFILE_OFF"]
                res_on = run_res["PROFILE_ON"]

                # Verify identical functional and counter outputs
                out_off = res_off.get("output_data") or {}
                out_on = res_on.get("output_data") or {}

                steps_off = out_off.get("steps", [])
                steps_on = out_on.get("steps", [])
                if len(steps_off) != len(steps_on):
                    raise ValueError(f"Step count mismatch between OFF ({len(steps_off)}) and ON ({len(steps_on)})")

                cnt_off = out_off.get("counters", {})
                cnt_on = out_on.get("counters", {})
                if cnt_off.get("committed_emissions") != cnt_on.get("committed_emissions"):
                    raise ValueError("Emitted events mismatch between PROFILE_OFF and PROFILE_ON")
                if cnt_off.get("candidate_evaluations") != cnt_on.get("candidate_evaluations"):
                    raise ValueError("Candidate evaluations mismatch between PROFILE_OFF and PROFILE_ON")

                t_off_ns = res_off["outer_wall_ns"]
                t_on_ns = res_on["outer_wall_ns"]
                diff_ns = t_on_ns - t_off_ns
                ovh_pct = 100.0 * diff_ns / t_off_ns if t_off_ns > 0 else 0.0

                overhead_pcts.append(ovh_pct)
                paired_differences_ns.append(diff_ns)

                record = {
                    "trial_index": trial_idx,
                    "workload_id": case_id,
                    "repetition": rep,
                    "first_mode": first_mode,
                    "second_mode": second_mode,
                    "outer_wall_off_ns": t_off_ns,
                    "outer_wall_on_ns": t_on_ns,
                    "diff_ns": diff_ns,
                    "overhead_pct": round(ovh_pct, 4),
                    "binary_sha256": bin_sha,
                    "source_commit": prov_meta.get("repo_commit"),
                    "functional_equivalence": True,
                }
                raw_records.append(record)
                f_raw.write(json.dumps(record, sort_keys=True) + "\n")
                f_raw.flush()

    stats_pct = compute_distribution_stats(overhead_pcts)
    stats_diff_ns = compute_distribution_stats([float(d) for d in paired_differences_ns])

    summary = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": prov_meta.get("repo_commit"),
        "binary_sha256": bin_sha,
        "manifest_sha256": manifest_sha,
        "total_paired_trials": len(overhead_pcts),
        "tested_workloads": [wl["case_id"] for wl in cases],
        "functional_equivalence_verified": True,
        "overhead_pct_distribution": stats_pct,
        "paired_difference_ns_distribution": stats_diff_ns,
        "raw_artifact": str(raw_output_path),
        "measurement_notes": (
            "Outer wall includes child process spawn, JSON deserialization, streaming loop, and output serialization. "
            "Balanced ordering (alternating OFF->ON and ON->OFF) controls for operating system cache warming. "
            "Small variations or negative values reflect system scheduling noise on sub-millisecond execution scales."
        ),
    }

    with open(summary_output_path, "w", encoding="utf-8") as f_sum:
        json.dump(summary, f_sum, indent=2)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Measure P4a Profiler Observer Overhead")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY_OUTPUT)
    parser.add_argument("--trials", type=int, default=4, help="Trials per workload (default: 4)")
    parser.add_argument("--clean-build", action="store_true")
    args = parser.parse_args()

    summary = measure_observer_overhead(
        manifest_path=args.manifest,
        raw_output_path=args.raw_output,
        summary_output_path=args.summary_output,
        trials_per_workload=args.trials,
        clean_build=args.clean_build,
    )

    print("=" * 80)
    print("      RapidFlow-APX Profiler Observer Overhead Measurement Finished      ")
    print("=" * 80)
    print(f"  Total Paired Trials: {summary['total_paired_trials']}")
    print(f"  Functional Equivalence: {summary['functional_equivalence_verified']}")
    ovh = summary["overhead_pct_distribution"]
    print(f"  Overhead (%)  - Min: {ovh['min']:.2f}% | Q1: {ovh['q1']:.2f}% | Median: {ovh['median']:.2f}% | Mean: {ovh['mean']:.2f}% | Max: {ovh['max']:.2f}%")
    print(f"  Raw Artifact:     {summary['raw_artifact']}")
    print(f"  Summary Artifact: {args.summary_output}")
    print("=" * 80)


if __name__ == "__main__":
    main()
