#!/usr/bin/env python3
"""
scripts/dstar03_pilot_worker.py — Isolated Execution Worker for DSTAR-03 Pilot (v2).

Executes a single repetition of M1 or M2 on a given workload:
1. Loads workload JSON and validates schema, manifest binding, and component hashes.
2. Mandatory M0 verification check: rejects missing or truncated M0 ground truth.
3. Performs untimed warm-up replay on disposable state.
4. Resets to fresh independent state and new engine instance.
5. Performs single measured replay timing strictly engine.process_update with perf_counter_ns.
6. Verifies outside the timer:
   - Resulting graph edge state matches applied update.
   - Entered and exited tuple arrays are strictly canonical sorted and deduplicated.
   - Output tuples strictly match independent M0 ground truth step-for-step.
7. Measures peak RSS and worker wall time.
8. Emits structured JSON result to stdout and/or output file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dstar_event_common import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DEFAULT_MAX_STREAM_STEPS,
    DEFAULT_MAX_WORK,
    DStarEventError,
    DStarEventGraph,
    DStarEventUpdate,
    EventCounters,
    EventResult,
    canonical_ordered_tuple,
    validate_run_contract,
)
from dstar_m1_enum import M1EnumEngine
from dstar_m2_event import M2EventEngine


def canonical_json_bytes(data: Any) -> bytes:
    """Produces sorted-keys canonical JSON bytes."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_of_obj(data: Any) -> str:
    """Computes SHA-256 hex digest of canonical JSON serialization."""
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def compute_workload_fingerprint(workload: Dict[str, Any]) -> str:
    """Computes SHA-256 fingerprint of workload dictionary excluding self-hash field."""
    obj_for_hash = {k: v for k, v in workload.items() if k != "sha256_workload"}
    return sha256_of_obj(obj_for_hash)


def get_git_provenance() -> Dict[str, Any]:
    """Retrieves current Git HEAD and dirty status."""
    prov: Dict[str, Any] = {"commit": "UNKNOWN", "dirty": False}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True
        ).strip()
        prov["commit"] = commit
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), text=True
        ).strip()
        prov["dirty"] = bool(status_out)
    except Exception:
        pass
    return prov


def run_worker(
    workload_path: Path,
    engine_name: str,
    attempt_id: str,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    worker_t0 = time.perf_counter_ns()
    git_prov = get_git_provenance()

    # 1. Load workload
    if not workload_path.exists():
        return {
            "attempt_id": attempt_id,
            "status": "INPUT_INVALID",
            "error": f"Workload file not found: {workload_path}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    try:
        with workload_path.open("r", encoding="utf-8") as f:
            workload = json.load(f)
    except Exception as exc:
        return {
            "attempt_id": attempt_id,
            "status": "INPUT_INVALID",
            "error": f"Failed to parse workload JSON: {exc}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    # 2. Schema and Component Hash Validation
    workload_id = workload.get("workload_id", "UNKNOWN")
    k = workload.get("k")
    q_dict = workload.get("query")
    g0_dict = workload.get("initial_graph")
    stream_dicts = workload.get("stream")

    if k is None or q_dict is None or g0_dict is None or stream_dicts is None:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "status": "INPUT_INVALID",
            "error": "Workload missing required top-level fields (k, query, initial_graph, stream)",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    # Component fingerprints are MANDATORY and non-empty (F02)
    for comp_field in ("sha256_query", "sha256_initial_graph", "sha256_stream", "sha256_workload"):
        comp_val = workload.get(comp_field)
        if not isinstance(comp_val, str) or not comp_val.strip():
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "status": "INPUT_INVALID",
                "error": f"Workload missing mandatory component fingerprint: {comp_field}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }

    declared_q_sha = workload["sha256_query"]
    actual_q_sha = sha256_of_obj(q_dict)
    if declared_q_sha != actual_q_sha:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "status": "INPUT_INVALID",
            "error": f"Query component hash mismatch: declared {declared_q_sha} != actual {actual_q_sha}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    declared_g0_sha = workload["sha256_initial_graph"]
    actual_g0_sha = sha256_of_obj(g0_dict)
    if declared_g0_sha != actual_g0_sha:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "status": "INPUT_INVALID",
            "error": f"Initial graph component hash mismatch: declared {declared_g0_sha} != actual {actual_g0_sha}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    declared_s_sha = workload["sha256_stream"]
    actual_s_sha = sha256_of_obj(stream_dicts)
    if declared_s_sha != actual_s_sha:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "status": "INPUT_INVALID",
            "error": f"Stream component hash mismatch: declared {declared_s_sha} != actual {actual_s_sha}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    declared_w_sha = workload["sha256_workload"]
    actual_w_sha = compute_workload_fingerprint(workload)
    if declared_w_sha != actual_w_sha:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "status": "INPUT_INVALID",
            "error": f"Workload fingerprint mismatch: declared {declared_w_sha} != actual {actual_w_sha}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    # 3. Mandatory M0 Ground Truth Verification Check (F02)
    m0_info = workload.get("m0_verification")
    if not m0_info or not isinstance(m0_info, dict) or not m0_info.get("verified"):
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "engine": engine_name,
            "status": "CORRECTNESS_FAIL",
            "error": "Mandatory M0 verification missing or unverified in workload fixture",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    expected_m0_steps = m0_info.get("steps")
    if not isinstance(expected_m0_steps, list) or len(expected_m0_steps) != len(stream_dicts):
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "engine": engine_name,
            "status": "CORRECTNESS_FAIL",
            "error": (
                f"M0 expected steps count ({len(expected_m0_steps) if isinstance(expected_m0_steps, list) else 0}) "
                f"does not match stream length ({len(stream_dicts)})"
            ),
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    try:
        q = DStarEventGraph.from_dict(q_dict)
        g0 = DStarEventGraph.from_dict(g0_dict)
        stream = [
            DStarEventUpdate(
                operation=s["operation"],
                edge=tuple(s["edge"]),
                label=s["label"],
            )
            for s in stream_dicts
        ]
    except Exception as exc:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "status": "INPUT_INVALID",
            "error": f"Failed to construct graphs/updates: {exc}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    # Validate M0 steps metadata against stream updates
    for step_idx, (exp_s, upd) in enumerate(zip(expected_m0_steps, stream)):
        if exp_s.get("step") != step_idx:
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": f"M0 step index mismatch at step {step_idx}: expected {exp_s.get('step')}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }
        if exp_s.get("operation") != upd.operation or tuple(exp_s.get("edge", ())) != upd.edge or exp_s.get("label") != upd.label:
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": f"M0 step operation mismatch at step {step_idx}: {exp_s} != {upd}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }

    limits = {
        "max_data_vertices": DEFAULT_MAX_DATA_VERTICES,
        "max_query_vertices": DEFAULT_MAX_QUERY_VERTICES,
        "max_candidates": DEFAULT_MAX_CANDIDATES,
        "max_work": DEFAULT_MAX_WORK,
        "max_stream_steps": DEFAULT_MAX_STREAM_STEPS,
    }

    # Contract validation
    try:
        validate_run_contract(q, g0, k, limits, stream_length=len(stream))
    except Exception as exc:
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "engine": engine_name,
            "status": "INPUT_INVALID",
            "error": f"Run contract violation: {exc}",
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    def create_engine() -> Any:
        if engine_name.lower() == "m1":
            return M1EnumEngine(limits)
        elif engine_name.lower() == "m2":
            return M2EventEngine(limits)
        else:
            raise ValueError(f"Unknown engine: {engine_name}")

    # 4. Untimed Warm-up Replay on disposable G0 clone
    warmup_status = "NOT_RUN"
    try:
        warmup_engine = create_engine()
        g_warm = g0.clone()
        for upd in stream:
            warmup_res, next_gw = warmup_engine.process_update(q, g_warm, upd, k, limits)
            g_warm = next_gw
        warmup_status = "SUCCESS"
    except Exception as exc:
        warmup_status = "FAILED"
        return {
            "attempt_id": attempt_id,
            "workload_id": workload_id,
            "engine": engine_name,
            "status": "ERROR",
            "error": f"Warmup replay failed: {exc}",
            "warmup_status": warmup_status,
            "worker_wall_ns": time.perf_counter_ns() - worker_t0,
            "git_provenance": git_prov,
        }

    # 5. State Reset & Fresh Engine Construction
    engine = create_engine()
    g_measured = g0.clone()

    # 6. Measured Replay
    per_step_records: List[Dict[str, Any]] = []
    stream_engine_ns = 0
    total_budget_units = 0
    total_entered = 0
    total_exited = 0

    agg_counters = {
        "candidate_evaluations": 0,
        "candidate_extensions": 0,
        "anchors_started": 0,
        "edge_lookups": 0,
        "vertex_label_checks": 0,
        "emitted_tuples": 0,
        "variants_started": 0,
        "raw_exact_results": 0,
        "edge_decisions": 0,
        "missing_branches": 0,
        "commitment_checks": 0,
    }

    for step_idx, upd in enumerate(stream):
        # Precise boundary around process_update call
        t0 = time.perf_counter_ns()
        try:
            res, next_gm = engine.process_update(q, g_measured, upd, k, limits)
        except Exception as exc:
            t1 = time.perf_counter_ns()
            stream_engine_ns += (t1 - t0)
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "ERROR",
                "error": f"Engine failed at step {step_idx}: {exc}",
                "failing_step": step_idx,
                "warmup_status": warmup_status,
                "stream_engine_ns": stream_engine_ns,
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }
        t1 = time.perf_counter_ns()
        step_ns = t1 - t0
        stream_engine_ns += step_ns
        g_measured = next_gm

        # Outside timer verification
        ent = res.entered
        exi = res.exited
        total_entered += len(ent)
        total_exited += len(exi)
        step_budget_units = (
            res.counters.candidate_evaluations
            + res.counters.edge_decisions
            + res.counters.variants_started
        )
        total_budget_units += step_budget_units

        # Canonical sort and zero duplicate check
        ent_set = set(ent)
        exi_set = set(exi)
        if len(ent) != len(ent_set):
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": f"Duplicate entered tuples detected at step {step_idx}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }
        if len(exi) != len(exi_set):
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": f"Duplicate exited tuples detected at step {step_idx}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }
        if ent != sorted(ent):
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": f"Entered tuples not in canonical sorted order at step {step_idx}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }
        if exi != sorted(exi):
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": f"Exited tuples not in canonical sorted order at step {step_idx}",
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }

        # Verify against independent M0 ground truth step
        exp_step = expected_m0_steps[step_idx]
        exp_ent = [tuple(t) for t in exp_step.get("entered", [])]
        exp_exi = [tuple(t) for t in exp_step.get("exited", [])]
        if ent != exp_ent or exi != exp_exi:
            return {
                "attempt_id": attempt_id,
                "workload_id": workload_id,
                "engine": engine_name,
                "status": "CORRECTNESS_FAIL",
                "error": (
                    f"M0 ground truth mismatch at step {step_idx}: "
                    f"engine entered={ent}, m0={exp_ent}; "
                    f"engine exited={exi}, m0={exp_exi}"
                ),
                "failing_step": step_idx,
                "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                "git_provenance": git_prov,
            }

        # Verify resulting graph edge state outside timer
        u_edge = tuple(sorted(upd.edge))
        if upd.operation == "INSERT":
            if u_edge not in g_measured.edges or g_measured.edges[u_edge] != upd.label:
                return {
                    "attempt_id": attempt_id,
                    "workload_id": workload_id,
                    "engine": engine_name,
                    "status": "CORRECTNESS_FAIL",
                    "error": f"Graph state mismatch after INSERT at step {step_idx}: edge {u_edge} not present with label {upd.label}",
                    "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                    "git_provenance": git_prov,
                }
        elif upd.operation == "DELETE":
            if u_edge in g_measured.edges:
                return {
                    "attempt_id": attempt_id,
                    "workload_id": workload_id,
                    "engine": engine_name,
                    "status": "CORRECTNESS_FAIL",
                    "error": f"Graph state mismatch after DELETE at step {step_idx}: edge {u_edge} still present",
                    "worker_wall_ns": time.perf_counter_ns() - worker_t0,
                    "git_provenance": git_prov,
                }

        # Accumulate counters
        c = res.counters
        agg_counters["candidate_evaluations"] += c.candidate_evaluations
        agg_counters["candidate_extensions"] += c.candidate_extensions
        agg_counters["anchors_started"] += c.anchors_started
        agg_counters["edge_lookups"] += c.edge_lookups
        agg_counters["vertex_label_checks"] += c.vertex_label_checks
        agg_counters["emitted_tuples"] += c.emitted_tuples
        agg_counters["variants_started"] += c.variants_started
        agg_counters["raw_exact_results"] += c.raw_exact_results
        agg_counters["edge_decisions"] += c.edge_decisions
        agg_counters["missing_branches"] += c.missing_branches
        agg_counters["commitment_checks"] += c.commitment_checks

        per_step_records.append({
            "step": step_idx,
            "operation": upd.operation,
            "edge": list(upd.edge),
            "label": upd.label,
            "step_ns": step_ns,
            "budget_units": step_budget_units,
            "entered_count": len(ent),
            "exited_count": len(exi),
        })

    worker_wall_ns = time.perf_counter_ns() - worker_t0
    ru = resource.getrusage(resource.RUSAGE_SELF)
    peak_rss_kib = ru.ru_maxrss

    total_events = total_entered + total_exited
    final_status = "SUCCESS_NONEMPTY" if total_events > 0 else "ZERO_RESULT_COMPLETE"

    result_payload: Dict[str, Any] = {
        "attempt_id": attempt_id,
        "workload_id": workload_id,
        "family": workload.get("family"),
        "graph_mode": workload.get("graph_mode"),
        "query_name": workload.get("query_name"),
        "k": k,
        "engine": engine_name,
        "status": final_status,
        "warmup_status": warmup_status,
        "stream_length": len(stream),
        "stream_engine_ns": stream_engine_ns,
        "stream_engine_ms": round(stream_engine_ns / 1_000_000, 4),
        "worker_wall_ns": worker_wall_ns,
        "worker_wall_ms": round(worker_wall_ns / 1_000_000, 4),
        "peak_rss_kib": peak_rss_kib,
        "peak_rss_mib": round(peak_rss_kib / 1024, 2),
        "total_entered": total_entered,
        "total_exited": total_exited,
        "total_events": total_events,
        "total_budget_units": total_budget_units,
        "counters": agg_counters,
        "steps": per_step_records,
        "git_provenance": git_prov,
    }

    if output_path:
        with output_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(result_payload, indent=2) + "\n")

    return result_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run single DSTAR-03 worker repetition.")
    parser.add_argument("--workload-path", type=Path, required=True, help="Path to workload JSON fixture.")
    parser.add_argument("--engine", type=str, required=True, choices=["m1", "m2", "M1", "M2"], help="Engine name.")
    parser.add_argument("--attempt-id", type=str, required=True, help="Unique attempt identifier.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output path for result JSON.")
    args = parser.parse_args()

    result = run_worker(
        workload_path=args.workload_path,
        engine_name=args.engine,
        attempt_id=args.attempt_id,
        output_path=args.output_json,
    )

    # Always print JSON to stdout so supervisor can parse even if exit code is non-zero
    print(json.dumps(result))

    sys.exit(0 if result.get("status") in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE") else 1)


if __name__ == "__main__":
    main()
