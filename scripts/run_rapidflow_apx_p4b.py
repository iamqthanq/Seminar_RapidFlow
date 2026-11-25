#!/usr/bin/env python3
"""
scripts/run_rapidflow_apx_p4b.py — P4b Preregistered Benchmark Orchestrator, Schedule & Measurement Validator.

Fail-closed lifecycle rules:
- During P4b-3A (approval_state == 'PREREGISTRATION_CANDIDATE', execution_authorized == False),
  confirmatory benchmark execution is strictly blocked fail-closed.
- Invoking with no execution flag or with --dry-run inspects and prints the validated
  plan without launching native benchmark measurements.
- Provides:
  * Deterministic balanced Williams 4x4 crossover schedule generation & validation
  * Machine-readable protocol & manifest hash lock verification
  * Strict separate measurement-output validator (preserving existing correctness validator untouched)
  * Per-child wait4 resource & peak RSS accounting (Linux KiB -> exact bytes)
  * Append-only JSONL ledger schema & no-silent-overwrite lineage resolver
  * Bounded untimed correctness / functional smoke harness (never comparative timing)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.p4a_evidence_contract import compute_sha256, is_valid_hex64
from scripts.run_rapidflow_apx_engines import (
    EXPECTED_UPSTREAM_COMMIT,
    validate_native_output_schema,
    validate_nlf_configuration,
)

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "rapidflow_apx_p4b_manifest.json"
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "configs" / "rapidflow_apx_p4b_protocol.json"
DEFAULT_SCHEDULE_PATH = REPO_ROOT / "configs" / "rapidflow_apx_p4b_schedule.json"
DEFAULT_LEDGER_PATH = REPO_ROOT / "results" / "logs" / "rf38-p4b" / "p4b_raw_ledger.jsonl"
DEFAULT_BINARY_PATH = REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build" / "rapidflow_apx_native"

LEDGER_SCHEMA_VERSION = "p4b-ledger-v1.0"
PROTOCOL_VERSION = "1.0.0"

SCHEDULE_SEED = 380301
BOOTSTRAP_SEED = 380302
DIAGNOSTIC_SCHEDULE_SEED = 380303

CANONICAL_WALL_TIMEOUT_SECONDS = 60.0
CANONICAL_AS_LIMIT_MIB = 512
CANONICAL_MAX_WORK_CAP = 500000
CANONICAL_MAX_NLF_MEMORY_MIB = 256
CANONICAL_REPETITIONS = 5

PRIMARY_VARIANTS: Dict[str, Dict[str, str]] = {
    "BARE": {"engine": "m2", "nlf_mode": "none", "neighbor_mode": "legacy", "role": "primary_control"},
    "A_STATIC": {"engine": "m2", "nlf_mode": "static", "neighbor_mode": "legacy", "role": "isolated_option_a_static"},
    "A_RESIDUAL": {"engine": "m2", "nlf_mode": "residual", "neighbor_mode": "legacy", "role": "isolated_option_a_residual"},
    "B_MERGE": {"engine": "m2", "nlf_mode": "none", "neighbor_mode": "merge", "role": "isolated_option_b_final"},
}

AUXILIARY_VARIANTS: Dict[str, Dict[str, Any]] = {
    "B_BUFFER": {
        "engine": "m2",
        "nlf_mode": "none",
        "neighbor_mode": "buffer",
        "role": "diagnostic_attribution_only",
        "included_in_primary_confirmatory": False,
    },
}

ALL_VARIANTS: Dict[str, Dict[str, Any]] = {**PRIMARY_VARIANTS, **AUXILIARY_VARIANTS}

WILLIAMS_SEQUENCES: Dict[str, Tuple[str, str, str, str]] = {
    "S1": ("BARE", "A_STATIC", "B_MERGE", "A_RESIDUAL"),
    "S2": ("A_STATIC", "A_RESIDUAL", "BARE", "B_MERGE"),
    "S3": ("A_RESIDUAL", "B_MERGE", "A_STATIC", "BARE"),
    "S4": ("B_MERGE", "BARE", "A_RESIDUAL", "A_STATIC"),
}

DIAG_VARIANTS: Tuple[str, str, str, str, str] = ("BARE", "A_STATIC", "A_RESIDUAL", "B_MERGE", "B_BUFFER")

INELIGIBLE_OUTCOMES = frozenset({
    "TIMEOUT",
    "WORK_BUDGET_EXCEEDED",
    "RESOURCE_UNAVAILABLE",
    "MEMORY_LIMIT_FAILURE",
    "CONFIGURATION_UNAVAILABLE",
    "CORRECTNESS_FAILURE",
    "SCHEMA_ERROR",
    "PROVENANCE_ERROR",
    "MEASUREMENT_ERROR",
    "ENVIRONMENT_INTERRUPTION",
    "MISSING_SLOT",
})


class AuthorizationBlockedError(RuntimeError):
    """Raised when benchmark execution is attempted before Coordinator approval."""


class ScheduleIntegrityError(ValueError):
    """Raised when schedule construction or validation detects an invariant violation."""


class LedgerIntegrityError(ValueError):
    """Raised on corrupt or silently overwritten P4b execution ledger records."""


def canonical_json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_williams_sequences() -> Dict[str, Any]:
    """Verify exact position balance and first-order transition balance across S1..S4."""
    seqs = list(WILLIAMS_SEQUENCES.values())
    variants = list(PRIMARY_VARIANTS.keys())
    pos_counts: Dict[str, List[int]] = {v: [0, 0, 0, 0] for v in variants}
    transitions: Counter[Tuple[str, str]] = Counter()
    for seq in seqs:
        if sorted(seq) != sorted(variants):
            raise ScheduleIntegrityError(f"Sequence {seq} does not contain all primary variants")
        for pos, var in enumerate(seq):
            pos_counts[var][pos] += 1
            if pos + 1 < len(seq):
                transitions[(var, seq[pos + 1])] += 1

    for v, counts in pos_counts.items():
        if counts != [1, 1, 1, 1]:
            raise ScheduleIntegrityError(f"Williams position imbalance for {v}: {counts}")

    for v1 in variants:
        for v2 in variants:
            if v1 != v2 and transitions[(v1, v2)] != 1:
                raise ScheduleIntegrityError(f"Williams transition imbalance for {v1}->{v2}: {transitions[(v1, v2)]}")

    return {"position_counts_per_sequence": pos_counts, "directed_transitions_count": len(transitions)}


def build_p4b_schedule(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    out_schedule_path: Optional[Path] = DEFAULT_SCHEDULE_PATH,
    session_id: str = "P4B_PLANNED_SESSION_01",
    schedule_seed: int = SCHEDULE_SEED,
    diag_seed: int = DIAGNOSTIC_SCHEDULE_SEED,
) -> Dict[str, Any]:
    """Build deterministic Williams 4x4 crossover schedule (1,000 primary + 200 warmup + 250 diagnostic = 1,450 slots)."""
    verify_williams_sequences()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha = compute_sha256(manifest_path)
    workloads = manifest.get("workloads", [])
    if len(workloads) != 50:
        raise ScheduleIntegrityError(f"Expected 50 workloads in manifest, found {len(workloads)}")

    rng_sched = random.Random(schedule_seed)
    rng_diag = random.Random(diag_seed)
    seq_names = ["S1", "S2", "S3", "S4"]

    # Assign 4 distinct Williams sequences to reps 1..4 for every workload
    first4_assignments: Dict[str, List[str]] = {}
    for w in workloads:
        wid = w["workload_id"]
        perm = list(seq_names)
        rng_sched.shuffle(perm)
        first4_assignments[wid] = perm

    # Assign 5th repetition sequences nearly evenly: 13 / 13 / 12 / 12
    rep5_pool = ["S1"] * 13 + ["S2"] * 13 + ["S3"] * 12 + ["S4"] * 12
    rng_sched.shuffle(rep5_pool)
    rep5_assignments: Dict[str, str] = {
        w["workload_id"]: rep5_pool[idx] for idx, w in enumerate(workloads)
    }

    warmup_slots: List[Dict[str, Any]] = []
    primary_slots: List[Dict[str, Any]] = []
    diagnostic_slots: List[Dict[str, Any]] = []

    global_order = 0
    for rep in range(1, CANONICAL_REPETITIONS + 1):
        round_workloads = list(workloads)
        rng_sched.shuffle(round_workloads)
        for w in round_workloads:
            wid = w["workload_id"]
            wsha = w["fixture_sha256"]
            seq_id = first4_assignments[wid][rep - 1] if rep <= 4 else rep5_assignments[wid]
            seq_vars = WILLIAMS_SEQUENCES[seq_id]

            # Warmup run per (workload, primary_variant) immediately prior to rep 1 in that block's sequence order
            if rep == 1:
                for pos, var_id in enumerate(seq_vars):
                    warmup_slots.append({
                        "slot_id": f"WUP_{wid}_{var_id}",
                        "session_id": session_id,
                        "workload_id": wid,
                        "workload_sha256": wsha,
                        "variant_id": var_id,
                        "repeat_id": 0,
                        "sequence_id": seq_id,
                        "execution_position": pos,
                        "slot_purpose": "warmup",
                        "execution_purpose": "performance",
                        "resource_policy_id": "P4B_STD_RES_V1",
                    })

            for pos, var_id in enumerate(seq_vars):
                global_order += 1
                primary_slots.append({
                    "slot_id": f"PRM_{wid}_R{rep}_{var_id}",
                    "session_id": session_id,
                    "workload_id": wid,
                    "workload_sha256": wsha,
                    "variant_id": var_id,
                    "repeat_id": rep,
                    "sequence_id": seq_id,
                    "execution_position": pos,
                    "global_execution_index": global_order,
                    "slot_purpose": "primary",
                    "execution_purpose": "performance",
                    "resource_policy_id": "P4B_STD_RES_V1",
                })

    # Diagnostic PROFILE_ON schedule: 50 workloads x 5 variants = 250 slots (separate cyclic balanced order)
    diag_workloads = list(workloads)
    rng_diag.shuffle(diag_workloads)
    base_diag_seq = list(DIAG_VARIANTS)
    for w_idx, w in enumerate(diag_workloads):
        wid = w["workload_id"]
        wsha = w["fixture_sha256"]
        shift = (w_idx + rng_diag.randrange(5)) % 5
        seq = base_diag_seq[shift:] + base_diag_seq[:shift]
        for pos, var_id in enumerate(seq):
            diagnostic_slots.append({
                "slot_id": f"DIAG_{wid}_D1_{var_id}",
                "session_id": f"{session_id}_DIAG",
                "workload_id": wid,
                "workload_sha256": wsha,
                "variant_id": var_id,
                "repeat_id": 1,
                "sequence_id": f"DIAG_ROT_{shift}",
                "execution_position": pos,
                "slot_purpose": "diagnostic",
                "execution_purpose": "diagnostic",
                "resource_policy_id": "P4B_STD_RES_V1",
            })

    schedule = {
        "schedule_version": "1.0.0",
        "manifest_path": "configs/rapidflow_apx_p4b_manifest.json",
        "manifest_sha256": manifest_sha,
        "schedule_seed": schedule_seed,
        "diagnostic_schedule_seed": diag_seed,
        "session_id": session_id,
        "resource_policy_id": "P4B_STD_RES_V1",
        "williams_sequences": {k: list(v) for k, v in WILLIAMS_SEQUENCES.items()},
        "planned_counts": {
            "primary_slots": len(primary_slots),
            "warmup_slots": len(warmup_slots),
            "diagnostic_slots": len(diagnostic_slots),
            "total_slots": len(primary_slots) + len(warmup_slots) + len(diagnostic_slots),
        },
        "warmup_slots": warmup_slots,
        "primary_slots": primary_slots,
        "diagnostic_slots": diagnostic_slots,
    }
    validate_schedule(schedule, manifest)
    if out_schedule_path is not None:
        out_schedule_path.parent.mkdir(parents=True, exist_ok=True)
        out_schedule_path.write_bytes(canonical_json_bytes(schedule))
    return schedule


def validate_schedule(schedule: Mapping[str, Any], manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Enforce exact slot counts, Williams crossover balance, unique IDs, and fixture hashes."""
    w_by_id = {w["workload_id"]: w for w in manifest.get("workloads", [])}
    if len(w_by_id) != 50:
        raise ScheduleIntegrityError(f"Expected 50 manifest workloads, got {len(w_by_id)}")

    primary = schedule.get("primary_slots", [])
    warmup = schedule.get("warmup_slots", [])
    diag = schedule.get("diagnostic_slots", [])
    if len(primary) != 1000:
        raise ScheduleIntegrityError(f"Expected 1000 primary slots, got {len(primary)}")
    if len(warmup) != 200:
        raise ScheduleIntegrityError(f"Expected 200 warmup slots, got {len(warmup)}")
    if len(diag) != 250:
        raise ScheduleIntegrityError(f"Expected 250 diagnostic slots, got {len(diag)}")

    all_ids: set[str] = set()
    for bucket_name, bucket in (("primary", primary), ("warmup", warmup), ("diagnostic", diag)):
        for s in bucket:
            sid = s.get("slot_id", "")
            if not sid or sid in all_ids:
                raise ScheduleIntegrityError(f"Duplicate or empty slot_id {sid!r} in {bucket_name}")
            all_ids.add(sid)
            wid = s.get("workload_id")
            if wid not in w_by_id:
                raise ScheduleIntegrityError(f"Unknown workload_id {wid!r} in slot {sid}")
            if s.get("workload_sha256") != w_by_id[wid]["fixture_sha256"]:
                raise ScheduleIntegrityError(f"Hash mismatch in slot {sid} for {wid}")
            if s.get("variant_id") not in ALL_VARIANTS:
                raise ScheduleIntegrityError(f"Unknown variant {s.get('variant_id')!r} in slot {sid}")

    var_counts: Counter[str] = Counter()
    wl_counts: Counter[str] = Counter()
    cell_counts: Counter[Tuple[str, str]] = Counter()
    pos_counts: Dict[str, Counter[int]] = {v: Counter() for v in PRIMARY_VARIANTS}
    first4_seqs: Dict[Tuple[str, int], str] = {}
    rep5_seqs: Dict[str, str] = {}

    for s in primary:
        if s.get("slot_purpose") != "primary":
            raise ScheduleIntegrityError(f"Unexpected slot_purpose in primary list: {s.get('slot_purpose')}")
        v = s["variant_id"]
        if v not in PRIMARY_VARIANTS:
            raise ScheduleIntegrityError(f"Non-primary variant {v!r} in primary slot list")
        w = s["workload_id"]
        r = s["repeat_id"]
        pos = s["execution_position"]
        var_counts[v] += 1
        wl_counts[w] += 1
        cell_counts[(w, v)] += 1
        pos_counts[v][pos] += 1
        if 1 <= r <= 4:
            first4_seqs[(w, r)] = s["sequence_id"]
        elif r == 5:
            rep5_seqs[w] = s["sequence_id"]
        else:
            raise ScheduleIntegrityError(f"Invalid repeat_id {r} in primary slot {s['slot_id']}")

    for v in PRIMARY_VARIANTS:
        if var_counts[v] != 250:
            raise ScheduleIntegrityError(f"Variant {v} has {var_counts[v]} primary slots != 250")
    for w in w_by_id:
        if wl_counts[w] != 20:
            raise ScheduleIntegrityError(f"Workload {w} has {wl_counts[w]} primary slots != 20")
        for v in PRIMARY_VARIANTS:
            if cell_counts[(w, v)] != 5:
                raise ScheduleIntegrityError(f"Pair ({w},{v}) has {cell_counts[(w, v)]} slots != 5")
        if sorted(first4_seqs[(w, r)] for r in range(1, 5)) != ["S1", "S2", "S3", "S4"]:
            raise ScheduleIntegrityError(f"Reps 1..4 for {w} do not cover S1..S4 once each")

    rep5_dist = Counter(rep5_seqs.values())
    if sorted(rep5_dist.values()) != [12, 12, 13, 13]:
        raise ScheduleIntegrityError(f"Rep 5 sequence balance invalid: {dict(rep5_dist)}")

    for v, pc in pos_counts.items():
        vals = [pc[p] for p in range(4)]
        if sum(vals) != 250 or max(vals) - min(vals) > 1:
            raise ScheduleIntegrityError(f"Global position imbalance for {v}: {vals}")

    # Diagnostic checks: B_BUFFER allowed strictly in diagnostic, excluded from primary
    diag_vars = Counter(s["variant_id"] for s in diag)
    for v in DIAG_VARIANTS:
        if diag_vars[v] != 50:
            raise ScheduleIntegrityError(f"Diagnostic variant {v} count {diag_vars[v]} != 50")

    return {
        "valid": True,
        "primary_slots": len(primary),
        "warmup_slots": len(warmup),
        "diagnostic_slots": len(diag),
        "total_slots": len(all_ids),
        "rep5_sequence_counts": dict(rep5_dist),
        "position_counts_by_variant": {v: dict(pos_counts[v]) for v in PRIMARY_VARIANTS},
    }


def validate_measurement_output_schema(
    res_data: Dict[str, Any],
    expected_engine: str,
    expected_case_id: str,
    expected_purpose: str,
    expected_nlf_mode: str,
    expected_neighbor_mode: str,
    workload: Dict[str, Any],
    expected_raw_input_sha256: str,
    is_success: bool,
    native_exit_code: Optional[int] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Distinct strict measurement-output validator (Section XVII.2).
    Does NOT weaken the existing correctness validator (which continues to require graph_state/codec_state).
    Rejects performance runs if profiling_enabled=True, if deep_diagnostics_enabled=True,
    if deep diagnostic fields leak into steps, or if macro_timing fields are invalid/missing.
    """
    if expected_purpose not in ("performance", "diagnostic"):
        return False, f"Invalid expected_purpose for measurement validator: {expected_purpose!r}", {}

    if not isinstance(res_data, dict):
        return False, "Measurement output root is not a JSON object", {}

    if res_data.get("measurement_schema_version") != 1 or isinstance(res_data.get("measurement_schema_version"), bool):
        return False, f"Invalid or missing measurement_schema_version: {res_data.get('measurement_schema_version')!r}", {}

    obs_purpose = res_data.get("execution_purpose")
    if obs_purpose != expected_purpose:
        return False, f"Execution purpose mismatch: obs={obs_purpose!r} vs exp={expected_purpose!r}", {}

    prof_flag = res_data.get("profiling_enabled")
    if type(prof_flag) is not bool:
        return False, f"Missing or non-bool profiling_enabled: {prof_flag!r}", {}
    if expected_purpose == "performance" and prof_flag is not False:
        return False, "Measurement eligibility violation: performance mode must have profiling_enabled=False (PROFILE_OFF)", {}
    if expected_purpose == "diagnostic" and prof_flag is not True:
        return False, "Diagnostic mode must have profiling_enabled=True (PROFILE_ON)", {}

    diag_flag = res_data.get("deep_diagnostics_enabled")
    if type(diag_flag) is not bool or diag_flag is not False:
        return False, f"deep_diagnostics_enabled must be False in measurement mode, got {diag_flag!r}", {}

    if expected_purpose == "performance" and "profiling" in res_data:
        return False, "Unexpected 'profiling' root object in performance (PROFILE_OFF) output", {}
    if expected_purpose == "diagnostic" and is_success and not isinstance(res_data.get("profiling"), dict):
        return False, "Missing 'profiling' root object in successful diagnostic (PROFILE_ON) output", {}

    # Validate macro_timing block
    mt = res_data.get("macro_timing")
    if not isinstance(mt, dict):
        return False, "Missing 'macro_timing' object in measurement output", {}
    if mt.get("clock_source") != "std::chrono::steady_clock":
        return False, f"Invalid macro_timing.clock_source: {mt.get('clock_source')!r}", {}

    for t_field in (
        "input_parsing_ns",
        "initialization_wall_ns",
        "graph_init_ns",
        "query_prep_ns",
        "engine_and_nlf_init_ns",
        "streaming_loop_wall_ns",
        "reporting_wall_ns",
    ):
        val = mt.get(t_field)
        if type(val) is not int or isinstance(val, bool) or val < 0:
            return False, f"Invalid macro_timing.{t_field}: {val!r}", {}

    if is_success and workload.get("updates") and mt["streaming_loop_wall_ns"] <= 0:
        return False, f"Successful nonempty workload must have positive streaming_loop_wall_ns, got {mt['streaming_loop_wall_ns']}", {}

    if "child_vm_hwm_kib" in mt or "child_vm_hwm_bytes" in mt:
        kib = mt.get("child_vm_hwm_kib")
        byt = mt.get("child_vm_hwm_bytes")
        if (
            type(kib) is not int or isinstance(kib, bool) or kib <= 0
            or type(byt) is not int or isinstance(byt, bool) or byt != kib * 1024
        ):
            return False, f"Invalid macro_timing VmHWM fields: kib={kib!r}, bytes={byt!r}", {}

    # Ensure deep diagnostic fields were omitted from step objects in measurement mode
    for idx, step in enumerate(res_data.get("steps", [])):
        if isinstance(step, dict):
            if "graph_state" in step or "codec_state" in step:
                return False, f"Step {idx} leaked deep diagnostics ('graph_state'/'codec_state') in {expected_purpose} mode", {}
            if expected_purpose == "performance" and "profiling" in step:
                return False, f"Step {idx} leaked per-step 'profiling' in performance mode", {}

    # Full semantic validation using strict native schema check
    sem_ok, sem_diag = validate_native_output_schema(
        res_data=res_data,
        expected_engine=expected_engine,
        expected_case_id=expected_case_id,
        workload=workload,
        is_success=is_success,
        expected_raw_input_sha256=expected_raw_input_sha256,
    )
    if not sem_ok:
        return False, sem_diag, {}

    # Strict live configuration version 2 validation
    cfg_ok, cfg_diag, cfg_meta = validate_nlf_configuration(
        requested_engine=expected_engine,
        requested_nlf_mode=expected_nlf_mode,
        native_result=res_data,
        is_success_run=is_success,
        requested_neighbor_mode=expected_neighbor_mode,
        historical_auth=None,
        native_exit_code=native_exit_code,
    )
    if not cfg_ok:
        return False, cfg_diag, cfg_meta

    return True, "", cfg_meta


def execute_p4b_native_slot(
    binary_path: Path,
    fixture_path: Path,
    variant_id: str,
    execution_purpose: str,
    output_path: Path,
    workload_data: Optional[Dict[str, Any]] = None,
    timeout_s: float = CANONICAL_WALL_TIMEOUT_SECONDS,
    as_limit_mib: int = CANONICAL_AS_LIMIT_MIB,
    max_work: int = CANONICAL_MAX_WORK_CAP,
    max_nlf_memory_mib: int = CANONICAL_MAX_NLF_MEMORY_MIB,
) -> Dict[str, Any]:
    """
    Execute one P4b slot on the frozen fixture bytes directly using POSIX wait4 rusage
    so peak_rss_bytes, user_cpu_ns, and system_cpu_ns are strictly per-child (no cumulative RUSAGE_CHILDREN aliasing).
    """
    if variant_id not in ALL_VARIANTS:
        raise ValueError(f"Unknown variant_id: {variant_id!r}")
    if execution_purpose not in ("performance", "diagnostic", "correctness"):
        raise ValueError(f"Unsupported execution_purpose: {execution_purpose!r}")

    vspec = ALL_VARIANTS[variant_id]
    fixture_bytes = fixture_path.read_bytes()
    fixture_sha = sha256_bytes(fixture_bytes)
    wl = workload_data if workload_data is not None else json.loads(fixture_bytes.decode("utf-8"))

    cmd = [
        str(binary_path),
        "--engine", vspec["engine"],
        "--nlf-mode", vspec["nlf_mode"],
        "--neighbor-mode", vspec["neighbor_mode"],
        "--max-nlf-memory-mib", str(int(max_nlf_memory_mib)),
        "--max-work", str(int(max_work)),
        "--execution-purpose", execution_purpose,
        "--wall-timeout-seconds", str(int(math.ceil(timeout_s))),
        "--workload", str(fixture_path),
        "--output", str(output_path),
    ]

    def preexec() -> None:
        os.setsid()
        as_bytes = int(as_limit_mib) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    t0_ns = time.perf_counter_ns()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=preexec,
    )

    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []

    def _drain(pipe: Any, sink: List[bytes]) -> None:
        try:
            sink.append(pipe.read())
        except Exception:
            pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + timeout_s
    timed_out = False
    rusage = None
    wait_status = 0
    while True:
        pid_done, status, ru = os.wait4(proc.pid, os.WNOHANG)
        if pid_done != 0:
            wait_status = status
            rusage = ru
            break
        if time.monotonic() >= deadline:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            _, wait_status, rusage = os.wait4(proc.pid, 0)
            break
        time.sleep(0.002)

    t1_ns = time.perf_counter_ns()
    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)
    if proc.stdout:
        proc.stdout.close()
    if proc.stderr:
        proc.stderr.close()

    outer_wall_ns = t1_ns - t0_ns
    raw_stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    raw_stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    exit_code: Optional[int] = None
    term_signal: Optional[int] = None
    if os.WIFEXITED(wait_status):
        exit_code = os.WEXITSTATUS(wait_status)
    elif os.WIFSIGNALED(wait_status):
        term_signal = os.WTERMSIG(wait_status)
        exit_code = -term_signal

    proc.returncode = exit_code

    user_cpu_ns: Optional[int] = int(round(rusage.ru_utime * 1_000_000_000)) if rusage is not None else None
    system_cpu_ns: Optional[int] = int(round(rusage.ru_stime * 1_000_000_000)) if rusage is not None else None
    # On Linux, rusage.ru_maxrss (from wait4) can inherit pre-execve parent fork RSS;
    # prefer native child's post-execve /proc/self/status VmHWM when available, falling back to wait4 ru_maxrss on abnormal exit.
    wait4_rss_kib = rusage.ru_maxrss if (rusage is not None and rusage.ru_maxrss > 0) else None
    wait4_rss_bytes = int(wait4_rss_kib) * 1024 if wait4_rss_kib is not None else None

    out_data: Optional[Dict[str, Any]] = None
    if output_path.is_file():
        try:
            out_data = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            out_data = None

    child_mt = (out_data or {}).get("macro_timing") if isinstance((out_data or {}).get("macro_timing"), dict) else {}
    vmhwm_kib = child_mt.get("child_vm_hwm_kib")
    vmhwm_bytes = child_mt.get("child_vm_hwm_bytes")
    if (
        type(vmhwm_kib) is int and not isinstance(vmhwm_kib, bool) and vmhwm_kib > 0
        and type(vmhwm_bytes) is int and not isinstance(vmhwm_bytes, bool) and vmhwm_bytes == vmhwm_kib * 1024
    ):
        peak_rss_kib: Optional[int] = vmhwm_kib
        peak_rss_bytes: Optional[int] = vmhwm_bytes
        rss_method = "linux_proc_self_status_vmhwm"
    else:
        peak_rss_kib = wait4_rss_kib
        peak_rss_bytes = wait4_rss_bytes
        rss_method = "linux_wait4_rusage_ru_maxrss"

    err_msg = (out_data or {}).get("error_message", "")
    err_cat = (out_data or {}).get("error_category", "NONE")
    if timed_out or term_signal == signal.SIGALRM:
        censor_class = "TIMEOUT"
    elif exit_code == 4 or err_cat == "WORK_BUDGET_EXCEEDED":
        censor_class = "WORK_BUDGET_EXCEEDED"
    elif "std::bad_alloc" in err_msg or "std::bad_alloc" in raw_stderr or err_cat == "RESOURCE_LIMIT_ERROR":
        censor_class = "MEMORY_LIMIT_FAILURE"
    elif (out_data or {}).get("configuration_status") == "REQUESTED_CONFIGURATION_UNAVAILABLE":
        censor_class = "CONFIGURATION_UNAVAILABLE"
    elif term_signal is not None:
        censor_class = "RESOURCE_UNAVAILABLE"
    elif exit_code != 0 or out_data is None:
        censor_class = "SCHEMA_ERROR"
    else:
        ok, diag, cfg_meta = (
            validate_measurement_output_schema(
                out_data,
                vspec["engine"],
                wl["case_id"],
                execution_purpose,
                vspec["nlf_mode"],
                vspec["neighbor_mode"],
                wl,
                fixture_sha,
                True,
                native_exit_code=exit_code,
            )
            if execution_purpose in ("performance", "diagnostic")
            else (
                *validate_native_output_schema(
                    out_data, vspec["engine"], wl["case_id"], workload=wl, is_success=True, expected_raw_input_sha256=fixture_sha
                ),
                {},
            )
        )
        censor_class = "NONE" if ok else "MEASUREMENT_ERROR"
        if not ok:
            err_msg = diag

    return {
        "variant_id": variant_id,
        "execution_purpose": execution_purpose,
        "exit_code": exit_code,
        "process_signal": term_signal,
        "censoring_reason": censor_class,
        "error_message": err_msg,
        "outer_wall_ns": outer_wall_ns,
        "user_cpu_ns": user_cpu_ns,
        "system_cpu_ns": system_cpu_ns,
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_original_value": peak_rss_kib,
        "peak_rss_original_unit": "KiB",
        "resource_measurement_method": rss_method,
        "wait4_ru_maxrss_bytes": wait4_rss_bytes,
        "fixture_sha256": fixture_sha,
        "output_path": str(output_path),
        "output_size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "output_sha256": compute_sha256(output_path) if output_path.is_file() else None,
        "output_data": out_data,
        "raw_stdout": raw_stdout,
        "raw_stderr": raw_stderr,
    }


def resolve_p4b_ledger_records(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Resolve raw execution ledger fail-closed without silent overwrites:
    preserves full attempt history and rejects any duplicate conflicting completed attempt
    on the same slot_id.
    """
    attempts_by_slot: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    canonical_slots: Dict[str, Mapping[str, Any]] = {}
    duplicate_attempts: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)

    for idx, rec in enumerate(records):
        if not isinstance(rec, Mapping):
            raise LedgerIntegrityError(f"Record index {idx} is not a dict")
        sid = rec.get("slot_id")
        if not isinstance(sid, str) or not sid:
            raise LedgerIntegrityError(f"Record index {idx} missing valid slot_id")
        if rec.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION:
            raise LedgerIntegrityError(f"Invalid ledger_schema_version on {sid}: {rec.get('ledger_schema_version')!r}")
        attempts_by_slot[sid].append(rec)

    for sid, group in attempts_by_slot.items():
        if len(group) > 1:
            duplicate_attempts[sid] = list(group)
            valid_or_primary = [g for g in group if g.get("measurement_eligible") is True]
            if len(valid_or_primary) > 1:
                raise LedgerIntegrityError(
                    f"Ambiguous duplicate eligible attempts for slot {sid}: silent overwrite/cherry-pick forbidden"
                )
        canonical_slots[sid] = group[-1] if group[-1].get("measurement_eligible") is True else next(
            (g for g in group if g.get("measurement_eligible") is True), group[0]
        )

    return {
        "canonical_slots": canonical_slots,
        "all_attempts_by_slot": dict(attempts_by_slot),
        "duplicate_attempts": dict(duplicate_attempts),
        "total_raw_records": len(records),
    }


def check_execution_authorization(protocol: Mapping[str, Any]) -> Tuple[bool, str]:
    """Fail-closed authorization gate blocking confirmatory execution during P4b-3A."""
    state = protocol.get("approval_state")
    authorized = protocol.get("execution_authorized")
    if state != "MEASUREMENT_AUTHORIZED" or authorized is not True:
        return (
            False,
            f"MEASUREMENT_NOT_AUTHORIZED: approval_state={state!r}, execution_authorized={authorized!r}. "
            "P4b-3B execution requires independent review and Coordinator authorization.",
        )
    return True, ""


def run_dry_run_inspection(
    protocol_path: Path,
    manifest_path: Path,
    schedule_path: Path,
    binary_path: Path,
) -> Dict[str, Any]:
    """Verify and print planned execution inventory without spawning any benchmark run."""
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))

    proto_sha = compute_sha256(protocol_path)
    man_sha = compute_sha256(manifest_path)
    sched_sha = compute_sha256(schedule_path)
    bin_sha = compute_sha256(binary_path) if binary_path.is_file() else "MISSING_BINARY"

    if protocol.get("manifest_sha256") != man_sha:
        raise ValueError(f"Protocol manifest_sha256 mismatch: {protocol.get('manifest_sha256')} != {man_sha}")
    if protocol.get("schedule_sha256") != sched_sha:
        raise ValueError(f"Protocol schedule_sha256 mismatch: {protocol.get('schedule_sha256')} != {sched_sha}")
    val_res = validate_schedule(schedule, manifest)

    return {
        "dry_run": True,
        "launched_subprocess_count": 0,
        "approval_state": protocol.get("approval_state"),
        "execution_authorized": protocol.get("execution_authorized"),
        "protocol_sha256": proto_sha,
        "manifest_sha256": man_sha,
        "schedule_sha256": sched_sha,
        "binary_path": str(binary_path),
        "binary_sha256": bin_sha,
        "planned_totals": schedule["planned_counts"],
        "schedule_balance": val_res,
        "primary_variants": list(PRIMARY_VARIANTS.keys()),
        "auxiliary_variants": list(AUXILIARY_VARIANTS.keys()),
        "resource_policy": protocol.get("resource_policy"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RapidFlow-APX P4b Experiment Orchestrator")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_PATH)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Inspect planned slots without running benchmarks")
    parser.add_argument("--build-schedule", action="store_true", help="Regenerate and verify schedule JSON")
    parser.add_argument("--execute-primary", action="store_true", help="Run primary 1,000 benchmark slots (BLOCKED in P4b-3A)")
    parser.add_argument("--execute-diagnostic", action="store_true", help="Run diagnostic 250 slots (BLOCKED in P4b-3A)")
    args = parser.parse_args()

    if args.build_schedule:
        sched = build_p4b_schedule(args.manifest, args.schedule)
        print(f"Generated verified schedule at {args.schedule}: {sched['planned_counts']}")
        return

    if args.execute_primary or args.execute_diagnostic:
        protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
        auth_ok, auth_msg = check_execution_authorization(protocol)
        if not auth_ok:
            print(f"ERROR: {auth_msg}", file=sys.stderr)
            sys.exit(2)
        print("Execution gate check passed (execution should be launched only under approved P4b-3B run).")
        return

    # Safe default or explicit --dry-run: never launches native benchmark measurements
    info = run_dry_run_inspection(args.protocol, args.manifest, args.schedule, args.binary)
    print(json.dumps(info, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
