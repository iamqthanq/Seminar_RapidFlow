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
import fcntl
import hashlib
import json
import math
import os
import random
import resource
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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
PROTOCOL_SHA256 = "70836770663c32a1a53e52e048be1dd8268fd2e3f02f66859737eef308c4acbb"
NATIVE_SHA256 = "f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75"
NATIVE_SOURCE_COMMIT = "97d8636b9567d53d3e083c48d61b46feb40ae540"
SCHEDULE_SESSION = "P4B_PLANNED_SESSION_01"

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
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json_bytes(raw: bytes) -> Any:
    """Reject duplicate keys and non-finite JSON, including exponent overflow."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def number(token):
        result = float(token)
        if not math.isfinite(result):
            raise ValueError(f"Non-finite JSON number: {token}")
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                      parse_float=number, parse_constant=number)


def load_frozen_plan(protocol_path=DEFAULT_PROTOCOL_PATH, manifest_path=DEFAULT_MANIFEST_PATH,
                     schedule_path=DEFAULT_SCHEDULE_PATH):
    # An exact byte lock also rejects semantically ambiguous types and caller-supplied
    # self-consistent changes to all three files. Changes require a new reviewed lock.
    protocol_bytes = protocol_path.read_bytes()
    if sha256_bytes(protocol_bytes) != PROTOCOL_SHA256:
        raise ScheduleIntegrityError("PROTOCOL_HASH_MISMATCH")
    protocol = load_json_bytes(protocol_bytes)
    manifest_bytes = manifest_path.read_bytes()
    schedule_bytes = schedule_path.read_bytes()
    if sha256_bytes(manifest_bytes) != protocol["manifest_sha256"]:
        raise ScheduleIntegrityError("MANIFEST_HASH_MISMATCH")
    if sha256_bytes(schedule_bytes) != protocol["schedule_sha256"]:
        raise ScheduleIntegrityError("SCHEDULE_HASH_MISMATCH")
    manifest = load_json_bytes(manifest_bytes)
    schedule = load_json_bytes(schedule_bytes)
    validate_schedule(schedule, manifest)
    for entry in manifest["workloads"]:
        raw = (REPO_ROOT / entry["fixture_path"]).read_bytes()
        if len(raw) != entry["fixture_size_bytes"] or sha256_bytes(raw) != entry["fixture_sha256"]:
            raise ScheduleIntegrityError(f"FIXTURE_IDENTITY_MISMATCH: {entry['workload_id']}")
    return protocol, manifest, schedule


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
    session_id: str = SCHEDULE_SESSION,
    schedule_seed: int = SCHEDULE_SEED,
    diag_seed: int = DIAGNOSTIC_SCHEDULE_SEED,
) -> Dict[str, Any]:
    """Build deterministic Williams 4x4 crossover schedule (1,000 primary + 200 warmup + 250 diagnostic = 1,450 slots)."""
    manifest = load_json_bytes(manifest_path.read_bytes())
    schedule = _generate_schedule(manifest, compute_sha256(manifest_path), session_id, schedule_seed, diag_seed)
    if out_schedule_path is not None:
        raw = canonical_json_bytes(schedule)
        if out_schedule_path.exists():
            if out_schedule_path.read_bytes() != raw:
                raise ScheduleIntegrityError("Refusing to replace a different schedule")
        else:
            with out_schedule_path.open("xb") as stream:
                stream.write(raw)
    return schedule


def _generate_schedule(manifest, manifest_sha, session_id, schedule_seed, diag_seed):
    verify_williams_sequences()
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
    return schedule


def validate_schedule(schedule: Mapping[str, Any], manifest: Mapping[str, Any]) -> Dict[str, Any]:
    # Replay assignment with the registered RNG seeds. This checks local and
    # global order, positions, purpose and every slot, not only aggregate totals.
    expected = _generate_schedule(manifest, sha256_bytes(canonical_json_bytes(manifest)),
                                  SCHEDULE_SESSION, SCHEDULE_SEED, DIAGNOSTIC_SCHEDULE_SEED)
    if canonical_json_bytes(schedule) != canonical_json_bytes(expected):
        for kind in ("warmup_slots", "primary_slots", "diagnostic_slots"):
            if canonical_json_bytes(schedule.get(kind)) != canonical_json_bytes(expected[kind]):
                raise ScheduleIntegrityError(f"Exact frozen schedule mismatch: {kind}")
        raise ScheduleIntegrityError("Frozen schedule metadata mismatch")
    primary = expected["primary_slots"]
    return {
        "valid": True,
        **expected["planned_counts"],
        "rep5_sequence_counts": dict(Counter(s["sequence_id"] for s in primary
                                              if s["repeat_id"] == 5 and s["execution_position"] == 0)),
        "position_counts_by_variant": {
            variant: dict(Counter(s["execution_position"] for s in primary if s["variant_id"] == variant))
            for variant in PRIMARY_VARIANTS
        },
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

    if type(res_data.get("measurement_schema_version")) is not int or res_data["measurement_schema_version"] != 1:
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

    if is_success or "child_vm_hwm_kib" in mt or "child_vm_hwm_bytes" in mt:
        if mt.get("child_rss_method") != "linux_proc_self_status_vmhwm":
            return False, "Invalid native child VmHWM method", {}
        kib = mt.get("child_vm_hwm_kib")
        byt = mt.get("child_vm_hwm_bytes")
        if (
            type(kib) is not int or isinstance(kib, bool) or kib <= 0
            or type(byt) is not int or isinstance(byt, bool) or byt != kib * 1024
        ):
            return False, f"Invalid macro_timing VmHWM fields: kib={kib!r}, bytes={byt!r}", {}

    # Ensure deep diagnostic fields were omitted from step objects in measurement mode
    if not isinstance(res_data.get("steps"), list):
        return False, "Measurement steps must be an array", {}
    for idx, step in enumerate(res_data["steps"]):
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
    expected_binary_sha256: Optional[str] = None,
    expected_fixture_sha256: Optional[str] = None,
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
    if expected_fixture_sha256 is not None and fixture_sha != expected_fixture_sha256:
        raise LedgerIntegrityError("WORKLOAD_HASH_MISMATCH")
    binary_sha = compute_sha256(binary_path)
    if expected_binary_sha256 is not None and binary_sha != expected_binary_sha256:
        raise LedgerIntegrityError("BINARY_HASH_MISMATCH")
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 60:
        raise ValueError("Invalid wall timeout")
    for limit in (as_limit_mib, max_work, max_nlf_memory_mib):
        if type(limit) is not int or limit <= 0:
            raise ValueError("Resource limits must be positive integers")
    wl = load_json_bytes(fixture_bytes)
    if workload_data is not None and canonical_json_bytes(wl) != canonical_json_bytes(workload_data):
        raise LedgerIntegrityError("WORKLOAD_DATA_MISMATCH")

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
    # Claim a fresh path before launch. An interrupted attempt keeps its partial
    # output (or this empty file), which cannot become a later attempt's result.
    with output_path.open("xb"):
        pass
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

    deadline = t0_ns + int(timeout_s * 1_000_000_000)
    timed_out = False
    rusage = None
    wait_status = 0
    try:
        while True:
            pid_done, status, ru = os.wait4(proc.pid, os.WNOHANG)
            if pid_done != 0:
                wait_status = status
                rusage = ru
                break
            if time.perf_counter_ns() >= deadline:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _, wait_status, rusage = os.wait4(proc.pid, 0)
                break
            time.sleep(0.002)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _, status, _ = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        for thread in (t_out, t_err):
            thread.join()
        proc.stdout.close()
        proc.stderr.close()
        raise

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
            decoded = load_json_bytes(output_path.read_bytes())
            if isinstance(decoded, dict):
                out_data = decoded
        except (ValueError, UnicodeError):
            out_data = None

    child_mt = (out_data or {}).get("macro_timing") if isinstance((out_data or {}).get("macro_timing"), dict) else {}
    vmhwm_kib = child_mt.get("child_vm_hwm_kib")
    vmhwm_bytes = child_mt.get("child_vm_hwm_bytes")
    if (
        child_mt.get("child_rss_method") == "linux_proc_self_status_vmhwm"
        and type(vmhwm_kib) is int and vmhwm_kib > 0
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
    if compute_sha256(binary_path) != binary_sha or compute_sha256(fixture_path) != fixture_sha:
        censor_class = "PROVENANCE_ERROR"
    elif timed_out or term_signal == signal.SIGALRM:
        censor_class = "TIMEOUT"
    elif exit_code == 4 and err_cat == "WORK_BUDGET_EXCEEDED":
        censor_class = "WORK_BUDGET_EXCEEDED"
    elif exit_code != 0 and ("std::bad_alloc" in str(err_msg) or "std::bad_alloc" in raw_stderr):
        censor_class = "MEMORY_LIMIT_FAILURE"
    elif err_cat == "RESOURCE_LIMIT_ERROR":
        censor_class = "RESOURCE_UNAVAILABLE"
    elif (out_data or {}).get("configuration_status") == "REQUESTED_CONFIGURATION_UNAVAILABLE":
        censor_class = "CONFIGURATION_UNAVAILABLE"
    elif term_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGKILL):
        censor_class = "ENVIRONMENT_INTERRUPTION"
    elif term_signal is not None:
        censor_class = "EXECUTION_FAILURE"
    elif out_data is None:
        censor_class = "MISSING_RESULT" if output_path.stat().st_size == 0 else "SCHEMA_ERROR"
    elif exit_code != 0:
        censor_class = "EXECUTION_FAILURE"
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
        "binary_sha256": binary_sha,
        "command": cmd,
        "output_path": str(output_path),
        "output_size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "output_sha256": compute_sha256(output_path) if output_path.is_file() else None,
        "output_data": out_data,
        "raw_stdout": raw_stdout,
        "raw_stderr": raw_stderr,
    }


ATTEMPT_OUTCOMES = frozenset({
    "COMPLETED", "CENSORED", "FAILED", "INTERRUPTED", "INVALID", "NOT_LAUNCHED", "INELIGIBLE",
})
RESOURCE_CENSORS = frozenset({
    "TIMEOUT", "WORK_BUDGET_EXCEEDED", "MEMORY_LIMIT_FAILURE",
    "RESOURCE_UNAVAILABLE", "CONFIGURATION_UNAVAILABLE",
})
EVIDENCE_ERROR_REASONS = frozenset({
    "SCHEMA_ERROR", "PROVENANCE_ERROR", "MEASUREMENT_ERROR", "MISSING_RESULT",
    "MEASUREMENT_ERROR_MISSING_RSS", "ENVIRONMENT_INTERRUPTION", "EXECUTION_FAILURE",
})
SYNTHETIC_KIND = "SYNTHETIC_VALIDATION"
SYNTHETIC_EXPERIMENT = "P4B_R1_SYNTHETIC"
SYNTHETIC_BINARY = sha256_bytes(b"P4b synthetic binary placeholder, never native evidence")
SYNTHETIC_SOURCE = hashlib.sha1(b"P4b synthetic source placeholder, never a Git revision").hexdigest()


def slot_identity(plan, protocol, *, experiment_id, evidence_kind, binary_sha, source_revision):
    """Build expected identity from a validated plan and experiment-level bindings."""
    seed_key = "diagnostic_schedule_seed" if plan["slot_purpose"] == "diagnostic" else "schedule_seed"
    spec = ALL_VARIANTS[plan["variant_id"]]
    return {
        **plan, "experiment_id": experiment_id, "evidence_kind": evidence_kind,
        "schedule_seed": protocol["seeds"][seed_key], "protocol_sha256": PROTOCOL_SHA256,
        "manifest_sha256": protocol["manifest_sha256"], "schedule_sha256": protocol["schedule_sha256"],
        "binary_sha256": binary_sha, "source_revision": source_revision, "engine": spec["engine"],
        "requested_nlf_mode": spec["nlf_mode"], "requested_neighbor_mode": spec["neighbor_mode"],
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
    }


def validate_slot_identity(record, expected):
    for key, value in expected.items():
        if type(record.get(key)) is not type(value) or record[key] != value:
            code = "SYNTHETIC_EVIDENCE_REJECTED" if key == "evidence_kind" else "SLOT_IDENTITY_MISMATCH"
            raise LedgerIntegrityError(f"{code}: {expected['slot_id']} field {key}")


def resolve_p4b_ledger_records(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Validate complete attempt lineage before selecting an observation."""
    attempts = defaultdict(list)
    seen_ids = set()
    canonical = {}
    for record in records:
        if not isinstance(record, Mapping) or record.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION:
            raise LedgerIntegrityError("INVALID_ATTEMPT_SCHEMA")
        sid, aid, number = (record.get(k) for k in ("slot_id", "attempt_id", "attempt_number"))
        if not isinstance(sid, str) or not sid or type(number) is not int or number < 1:
            raise LedgerIntegrityError("INVALID_ATTEMPT_IDENTITY")
        if aid != f"{sid}/{number}" or aid in seen_ids:
            raise LedgerIntegrityError(f"DUPLICATE_OR_INVALID_ATTEMPT_ID: {sid}")
        history = attempts[sid]
        if number != len(history) + 1:
            raise LedgerIntegrityError(f"MISSING_OR_DISORDERED_ATTEMPT: {sid}")
        outcome = record.get("attempt_outcome")
        if outcome not in ATTEMPT_OUTCOMES:
            raise LedgerIntegrityError(f"INVALID_ATTEMPT_OUTCOME: {sid}")
        if number == 1:
            if record.get("previous_attempt_id") is not None:
                raise LedgerIntegrityError(f"INVALID_FIRST_ATTEMPT_PARENT: {sid}")
        else:
            previous = history[-1]
            # The candidate registers no retry of an execution. A proven
            # prelaunch cancellation can be followed by the first execution.
            if (record.get("previous_attempt_id") != previous["attempt_id"]
                    or previous["attempt_outcome"] != "NOT_LAUNCHED"
                    or previous.get("prelaunch_verified") is not True
                    or previous.get("raw_output_sha256") is not None
                    or previous.get("process_exit_code") is not None
                    or previous.get("correctness_status") is not None):
                raise LedgerIntegrityError(f"UNREGISTERED_RETRY: {sid}")
        if outcome == "NOT_LAUNCHED" and (
            record.get("raw_output_sha256") is not None or record.get("process_exit_code") is not None
            or record.get("correctness_status") is not None
        ):
            raise LedgerIntegrityError(f"CONTRADICTORY_PRELAUNCH_EVIDENCE: {sid}")
        history.append(record)
        seen_ids.add(aid)
        canonical[sid] = record
    return {"canonical_slots": canonical, "all_attempts_by_slot": dict(attempts),
            "duplicate_attempts": {sid: group for sid, group in attempts.items() if len(group) > 1},
            "total_raw_records": len(records)}


def ordered_planned_slots(schedule):
    """Interleave warmups immediately before each first-repetition block."""
    warmups = defaultdict(list)
    for slot in schedule["warmup_slots"]:
        warmups[slot["workload_id"]].append(slot)
    for slot in schedule["primary_slots"]:
        if slot["repeat_id"] == 1 and slot["execution_position"] == 0:
            yield from warmups[slot["workload_id"]]
        yield slot
    yield from schedule["diagnostic_slots"]


def verify_raw_artifact(path, digest):
    if not isinstance(digest, str) or not is_valid_hex64(digest):
        raise LedgerIntegrityError("MISSING_RAW_EVIDENCE_HASH")
    if not path.is_file() or compute_sha256(path) != digest:
        raise LedgerIntegrityError(f"RAW_EVIDENCE_MISMATCH: {path.name}")


def validate_native_measurement_evidence(record, expected, workload, raw_path,
                                         reference_path, reference_sha256):
    """Bind a real measurement's bytes and semantics to a same-variant correctness reference.

    The reviewed reference digest is a separate input, not taken from the
    measurement row. This validates a record without authorizing execution or
    certifying that a reference was issued by a future reviewed experiment.
    """
    validate_slot_identity(record, expected)
    if expected["evidence_kind"] != "CONFIRMATORY_MEASUREMENT" or record.get("synthetic_test_fixture") is not False:
        raise LedgerIntegrityError("INVALID_REAL_EVIDENCE_ORIGIN")
    verify_raw_artifact(raw_path, record.get("raw_output_sha256"))
    verify_raw_artifact(reference_path, reference_sha256)
    native = load_json_bytes(raw_path.read_bytes())
    reference = load_json_bytes(reference_path.read_bytes())
    if not isinstance(reference, dict) or reference.get("execution_purpose") != "correctness":
        raise LedgerIntegrityError("INVALID_CORRECTNESS_REFERENCE")
    if type(record.get("process_exit_code")) is not int or record["process_exit_code"] != 0 or record.get("process_signal") is not None:
        raise LedgerIntegrityError("NON_SUCCESSFUL_NATIVE_MEASUREMENT")
    spec = ALL_VARIANTS[expected["variant_id"]]
    with_context = dict(workload=workload, is_success=True, expected_raw_input_sha256=expected["workload_sha256"])
    ok, reason = validate_native_output_schema(reference, spec["engine"], workload["case_id"], **with_context)
    if not ok:
        raise LedgerIntegrityError(f"INVALID_CORRECTNESS_REFERENCE: {reason}")
    ok, reason, _ = validate_nlf_configuration(
        spec["engine"], spec["nlf_mode"], reference, True, requested_neighbor_mode=spec["neighbor_mode"],
        native_exit_code=0,
    )
    if not ok:
        raise LedgerIntegrityError(f"INVALID_REFERENCE_VARIANT: {reason}")
    ok, reason, _ = validate_measurement_output_schema(
        native, spec["engine"], workload["case_id"], expected["execution_purpose"],
        spec["nlf_mode"], spec["neighbor_mode"], workload, expected["workload_sha256"],
        True, native_exit_code=0,
    )
    if not ok:
        raise LedgerIntegrityError(f"INVALID_MEASUREMENT_SCHEMA: {reason}")
    for key in ("status", "execution", "query_info", "work_budget", "counters"):
        if canonical_json_bytes(native.get(key)) != canonical_json_bytes(reference.get(key)):
            raise LedgerIntegrityError(f"MEASUREMENT_SEMANTIC_MISMATCH: {key}")
    for index, (actual, wanted) in enumerate(zip(native["steps"], reference["steps"], strict=True)):
        for key in ("step_index", "status", "commit_status", "op", "u", "v", "label",
                    "entered_tuples", "exited_tuples", "step_counters"):
            if canonical_json_bytes(actual.get(key)) != canonical_json_bytes(wanted.get(key)):
                raise LedgerIntegrityError(f"MEASUREMENT_SEMANTIC_MISMATCH: step {index} field {key}")
    timing = native["macro_timing"]
    for key, value in {
        "streaming_loop_wall_ns": timing["streaming_loop_wall_ns"],
        "initialization_wall_ns": timing["initialization_wall_ns"],
        "peak_rss_bytes": timing["child_vm_hwm_bytes"],
        "peak_rss_original_value": timing["child_vm_hwm_kib"],
        "resource_measurement_method": "linux_proc_self_status_vmhwm",
        "peak_rss_original_unit": "KiB",
    }.items():
        if type(record.get(key)) is not type(value) or record[key] != value:
            raise LedgerIntegrityError(f"MEASUREMENT_VALUE_NOT_BOUND_TO_RAW_EVIDENCE: {key}")


def exercise_synthetic_journal(root, simulate, *, stop_after=None):
    """Exercise durable execution/resume using a synthetic backend only.

    A slot with START and no FINISH was interrupted. The existing protocol
    does not register a retry or order recovery for such a session, so resume
    fails closed at that slot and retains partial artifacts for audit.
    """
    protocol, _, schedule = load_frozen_plan()
    plans = list(ordered_planned_slots(schedule))
    root.mkdir(parents=True, exist_ok=True)

    def identity(plan):
        return slot_identity(plan, protocol, experiment_id=SYNTHETIC_EXPERIMENT, evidence_kind=SYNTHETIC_KIND,
                             binary_sha=SYNTHETIC_BINARY, source_revision=SYNTHETIC_SOURCE)

    with (root / "synthetic-attempts.jsonl").open("a+b") as log:
        try:
            fcntl.flock(log, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LedgerIntegrityError("JOURNAL_ALREADY_IN_USE") from error

        def append(row):
            log.write((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
            log.flush()
            os.fsync(log.fileno())

        log.seek(0)
        terminal = []
        pending = None
        for line in log:
            if not line.endswith(b"\n"):
                raise LedgerIntegrityError("TORN_JOURNAL_RECORD")
            row = load_json_bytes(line)
            if not isinstance(row, dict) or len(terminal) >= len(plans):
                raise LedgerIntegrityError("UNPLANNED_JOURNAL_RECORD")
            expected = identity(plans[len(terminal)])
            validate_slot_identity(row, expected)
            if row.get("attempt_number") != 1 or type(row["attempt_number"]) is not int or row.get("attempt_id") != f"{expected['slot_id']}/1":
                raise LedgerIntegrityError("INVALID_JOURNAL_ATTEMPT")
            if row.get("journal_event") == "START" and pending is None:
                pending = row
            elif row.get("journal_event") == "FINISH" and pending is not None:
                if row.get("attempt_outcome") not in ("COMPLETED", "FAILED", "CENSORED", "INVALID", "INELIGIBLE"):
                    raise LedgerIntegrityError("INVALID_JOURNAL_TERMINAL_STATE")
                verify_raw_artifact(root / f"{expected['slot_id']}.json", row.get("raw_output_sha256"))
                pending = None
                terminal.append(row)
            else:
                raise LedgerIntegrityError("JOURNAL_TRANSITION_VIOLATION")
        if pending is not None:
            return {"state": "INTERRUPTED", "slot_id": pending["slot_id"], "completed_records": terminal}
        limit = len(plans) if stop_after is None else min(stop_after, len(plans))
        for plan in plans[len(terminal):limit]:
            expected = identity(plan)
            start = dict(expected, attempt_number=1, attempt_id=f"{plan['slot_id']}/1", journal_event="START")
            append(start)
            payload = simulate(plan)
            if payload.get("evidence_kind") != SYNTHETIC_KIND:
                raise LedgerIntegrityError("NON_SYNTHETIC_JOURNAL_BACKEND")
            outcome = payload.get("attempt_outcome")
            if outcome not in ("COMPLETED", "FAILED", "CENSORED", "INVALID", "INELIGIBLE"):
                raise LedgerIntegrityError("INVALID_JOURNAL_TERMINAL_STATE")
            raw = canonical_json_bytes(payload)
            with (root / f"{plan['slot_id']}.json").open("xb") as artifact:
                artifact.write(raw)
                artifact.flush()
                os.fsync(artifact.fileno())
            row = dict(start, journal_event="FINISH", raw_output_sha256=sha256_bytes(raw),
                       attempt_outcome=outcome)
            append(row)
            terminal.append(row)
        return {"state": "FINISHED" if len(terminal) == len(plans) else "PAUSED", "completed_records": terminal}


def check_execution_authorization(protocol: Mapping[str, Any]) -> Tuple[bool, str]:
    """R1 contains no reviewed production authorization, irrespective of local input."""
    return False, (
        f"MEASUREMENT_NOT_AUTHORIZED: approval_state={protocol.get('approval_state')!r}, "
        f"execution_authorized={protocol.get('execution_authorized')!r}. "
        "P4b-3B requires separately reviewed authorization and evidence bindings."
    )


def run_dry_run_inspection(
    protocol_path: Path,
    manifest_path: Path,
    schedule_path: Path,
    binary_path: Path,
) -> Dict[str, Any]:
    """Verify and print planned execution inventory without spawning any benchmark run."""
    protocol, manifest, schedule = load_frozen_plan(protocol_path, manifest_path, schedule_path)
    bin_sha = compute_sha256(binary_path) if binary_path.is_file() else "MISSING_BINARY"
    if bin_sha != NATIVE_SHA256:
        raise LedgerIntegrityError("BINARY_HASH_MISMATCH")
    val_res = validate_schedule(schedule, manifest)
    return {
        "dry_run": True,
        "launched_subprocess_count": 0,
        "approval_state": protocol.get("approval_state"),
        "execution_authorized": protocol.get("execution_authorized"),
        "protocol_sha256": PROTOCOL_SHA256,
        "manifest_sha256": protocol["manifest_sha256"],
        "schedule_sha256": protocol["schedule_sha256"],
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

    if args.execute_primary or args.execute_diagnostic:
        _, message = check_execution_authorization({})
        parser.exit(2, f"{message}\n")

    if args.build_schedule:
        _, _, schedule = load_frozen_plan(args.protocol, args.manifest, args.schedule)
        print(f"Verified frozen schedule at {args.schedule}: {schedule['planned_counts']}")
        return

    # Safe default or explicit --dry-run: never launches native benchmark measurements
    info = run_dry_run_inspection(args.protocol, args.manifest, args.schedule, args.binary)
    print(json.dumps(info, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
