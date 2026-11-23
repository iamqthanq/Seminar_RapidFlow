#!/usr/bin/env python3
"""
scripts/run_rapidflow_apx_p4a.py — Canonical runner and test executor for Phase P4a.

Executes Bare M2 Native Profiling, Baseline Measurement & Evidence-Driven Pilot.
Enforces:
1. Fail-closed Git provenance verification (clean working tree, pristine upstream 2ea4f4c...).
2. Bound Execution Identity (protocol_version, protocol_sha, manifest_sha, workload_sha, source_commit, binary_sha, limits).
3. Preflight host resource checks (MemAvailable >= 2.0 GiB, free disk >= 1.0 GiB).
4. Subprocess resource containment (RLIMIT_AS = 512 MiB, 60s timeout with process group kill, max-work = 500000).
5. Paired M1 vs Bare M2 execution on identical workloads with AB/BA alternating order.
6. Disposable warm-up replay, state reset to fresh G0 before timed runs.
7. Fail-closed independent Python M0 Dynamic Oracle cross-validation (blocks admission on failure).
8. Append-only, crash-resilient raw experiment ledger with durable fsync.
9. Strict resume validation requiring exact Execution Identity match.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.dstar_oracle import (
    DStarGraph,
    DStarDynamicOracle,
    DStarUpdate,
)

EXPECTED_UPSTREAM_COMMIT = "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1"
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "docs" / "rapidflow_apx_p4a_protocol.md"
DEFAULT_MANIFEST = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
DEFAULT_LEDGER = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
DEFAULT_SUMMARY = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_canonical_summary.json"

from scripts.p4a_evidence_contract import (
    PROTOCOL_MEASUREMENT_VERSION,
    PROTOCOL_ANALYSIS_VERSION,
    PROTOCOL_SPECIFICATION_REVISION,
    CANONICAL_MAX_WORK_CAP,
    CANONICAL_TIMEOUT_CEILING,
    CANONICAL_AS_LIMIT_MIB,
    CANONICAL_REPETITIONS_CEILING,
    compute_sha256,
    compute_dict_sha256,
    compute_execution_identity,
    get_frozen_manifest_registry,
    create_experiment_header,
    validate_record_integrity,
    resolve_canonical_attempts,
    validate_pair_compatibility,
    CorruptedLedgerError,
    IntegrityConflictError,
    AmbiguousSlotError,
)

PROTOCOL_VERSION = PROTOCOL_MEASUREMENT_VERSION


class OracleExecutionError(Exception):
    """Raised when the independent M0 Ground Truth Oracle fails during step execution."""
    pass


class IdentityMismatchError(Exception):
    """Raised on resume if existing records do not match the current execution identity."""
    pass


def compute_str_sha256(data: str) -> str:
    """Computes SHA-256 hash of a string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def verify_git_provenance(allow_dirty: bool = False) -> Tuple[Dict[str, Any], bool]:
    """Verifies repository commit, working tree cleanliness, and pristine upstream state."""
    meta: Dict[str, Any] = {
        "repo_commit": None,
        "repo_clean": False,
        "dirty_state": "UNKNOWN",
        "upstream_commit": None,
        "upstream_pristine": False,
    }
    try:
        r_head = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        meta["repo_commit"] = r_head
        r_status = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"], text=True
        ).strip()

        if not r_status:
            meta["repo_clean"] = True
            meta["dirty_state"] = "CLEAN"
        else:
            meta["repo_clean"] = False
            meta["dirty_state"] = "DIRTY"
            if allow_dirty:
                meta["repo_clean"] = True
                meta["dirty_state"] = "DIRTY_ALLOWED"
    except Exception as e:
        meta["dirty_state"] = f"ERROR_STATUS: {e}"
        return meta, False

    upstream_dir = REPO_ROOT / "baselines" / "rapidflow" / "source"
    if not upstream_dir.is_dir():
        return meta, False

    try:
        u_head = subprocess.check_output(
            ["git", "-C", str(upstream_dir), "rev-parse", "HEAD"], text=True
        ).strip()
        meta["upstream_commit"] = u_head
        u_status = subprocess.check_output(
            ["git", "-C", str(upstream_dir), "status", "--porcelain"], text=True
        ).strip()
        meta["upstream_pristine"] = (u_head == EXPECTED_UPSTREAM_COMMIT and not u_status)
    except Exception:
        meta["upstream_pristine"] = False
        return meta, False

    valid = (
        meta["repo_commit"] is not None
        and meta["repo_clean"] is True
        and meta["upstream_commit"] == EXPECTED_UPSTREAM_COMMIT
        and meta["upstream_pristine"] is True
    )
    return meta, valid


def check_system_resources() -> Tuple[Dict[str, Any], bool]:
    """Verifies host memory and free disk requirements (>= 2.0 GiB RAM, >= 1.0 GiB disk)."""
    res: Dict[str, Any] = {
        "mem_available_gib": 0.0,
        "free_disk_gib": 0.0,
        "preflight_passed": False,
    }
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    res["mem_available_gib"] = kb / (1024 * 1024)
                    break
    except Exception:
        pass

    try:
        usage = shutil.disk_usage(REPO_ROOT)
        res["free_disk_gib"] = usage.free / (1024 * 1024 * 1024)
    except Exception:
        pass

    ok = (res["mem_available_gib"] >= 2.0 and res["free_disk_gib"] >= 1.0)
    res["preflight_passed"] = ok
    return res, ok


def find_cmake_bin() -> str:
    """Finds pinned CMake binary or system CMake."""
    custom_cmake = REPO_ROOT / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"
    if custom_cmake.is_file() and os.access(str(custom_cmake), os.X_OK):
        return str(custom_cmake)
    which_cmake = shutil.which("cmake")
    if which_cmake:
        return which_cmake
    raise RuntimeError("CMake executable not found (neither in external/tools nor system PATH)")


def build_native_binary(clean_build: bool = False) -> Tuple[Path, str, Dict[str, Any], bool]:
    """Builds the native C++ testing harness under resource containment with pipe-safe subprocess."""
    build_dir = REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build"
    binary_path = build_dir / "rapidflow_apx_native"
    cmake_bin = find_cmake_bin()

    meta: Dict[str, Any] = {
        "clean_build": clean_build,
        "binary_path": str(binary_path),
        "binary_sha256": None,
        "build_success": False,
    }

    if clean_build and build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [cmake_bin, ".."],
            cwd=str(build_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        subprocess.run(
            ["make", "-j4"],
            cwd=str(build_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        if not binary_path.is_file():
            meta["build_error"] = "Binary file not created"
            return binary_path, "", meta, False
        sha = compute_sha256(binary_path)
        meta["binary_sha256"] = sha
        meta["build_success"] = True
        return binary_path, sha, meta, True
    except subprocess.CalledProcessError as e:
        meta["build_error"] = f"Command {e.cmd} failed with exit {e.returncode}:\n{e.stderr}"
        return binary_path, "", meta, False
    except Exception as e:
        meta["build_error"] = str(e)
        return binary_path, "", meta, False


def execute_native_run(
    binary_path: Path,
    engine: str,
    workload_data: Dict[str, Any],
    profile: bool = True,
    profile_output_path: Optional[Path] = None,
    timeout_s: float = 60.0,
    as_limit_mib: int = 512,
) -> Dict[str, Any]:
    """
    Executes a single native engine run in an isolated child subprocess with strict containment.
    Enforces process-group cleanup on timeout kill to prevent orphan processes.
    """
    tmp_dir = Path("/tmp") / f"p4a_exec_{os.getpid()}_{time.time_ns()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wl_path = tmp_dir / "workload.json"
    out_path = tmp_dir / "output.json"

    with open(wl_path, "w", encoding="utf-8") as f:
        json.dump(workload_data, f)

    cmd = [
        str(binary_path),
        "--engine", engine,
        "--workload", str(wl_path),
        "--output", str(out_path),
    ]
    if profile:
        cmd.append("--profile")
    if profile_output_path is not None:
        cmd.extend(["--profile-output", str(profile_output_path)])

    def preexec_fn():
        as_bytes = as_limit_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        os.setpgrp()

    t_wall_start = time.perf_counter_ns()
    exit_code = -1
    raw_stdout = ""
    raw_stderr = ""
    timed_out = False
    oom_censored = False

    proc = subprocess.Popen(
        cmd,
        preexec_fn=preexec_fn,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        raw_stdout, raw_stderr = proc.communicate(timeout=timeout_s)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = 124
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            raw_stdout, raw_stderr = proc.communicate(timeout=2.0)
        except Exception:
            pass

    t_wall_end = time.perf_counter_ns()
    outer_wall_ns = t_wall_end - t_wall_start

    output_data: Optional[Dict[str, Any]] = None
    if out_path.is_file():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                output_data = json.load(f)
        except Exception:
            output_data = None

    # Clean up temporary directory
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # Classify execution failure accurately without speculative inference
    status = "COMPLETED"
    if timed_out:
        status = "TIMEOUT"
    elif exit_code in (-9, 137):
        # Terminated by SIGKILL (could be OOM killer or external kill)
        status = "SIGNAL_KILL_CENSORED"
    elif exit_code == 4 or (output_data and output_data.get("error_category") == "WORK_BUDGET_EXCEEDED"):
        status = "WORK_LIMIT"
    elif exit_code == 5 or (output_data and output_data.get("error_category") == "RESOURCE_LIMIT_ERROR"):
        err_msg = output_data.get("error_message", "") if output_data else ""
        if "bad_alloc" in err_msg or "Cannot allocate memory" in err_msg:
            status = "OOM_CENSORED"
            oom_censored = True
        else:
            status = "RESOURCE_FAILURE_UNCLASSIFIED"
    elif exit_code != 0:
        status = "EXECUTION_ERROR"

    return {
        "engine": engine,
        "exit_code": exit_code,
        "status": status,
        "timed_out": timed_out,
        "oom_censored": oom_censored,
        "outer_wall_ns": outer_wall_ns,
        "output_data": output_data,
        "raw_stdout": raw_stdout,
        "raw_stderr": raw_stderr,
    }


def compute_m0_ground_truth(workload_data: Dict[str, Any]) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """
    Computes exact ground truth entered and exited tuples for each step using independent M0 Oracle.
    Returns (m0_steps, error_message). On failure, returns (None, error_str).
    """
    try:
        Q = DStarGraph()
        for v in workload_data["query"]["vertices"]:
            Q.add_vertex(v["id"], v["label"])
        for e in workload_data["query"]["edges"]:
            Q.add_edge(e["u"], e["v"], e["label"])

        G = DStarGraph()
        for v in workload_data["initial_graph"]["vertices"]:
            G.add_vertex(v["id"], v["label"])
        for e in workload_data["initial_graph"]["edges"]:
            G.add_edge(e["u"], e["v"], e["label"])

        oracle = DStarDynamicOracle(Q, G, k=workload_data["k"], max_data_vertices=64)
        m0_steps: List[Dict[str, Any]] = []

        for upd in workload_data["updates"]:
            op = "INSERT" if upd["op"] in ("+", "INSERT", "INS") else "DELETE"
            delta = oracle.apply_update(DStarUpdate(op, upd["u"], upd["v"], upd["label"]))
            entered = sorted([list(t) for t in delta.entered])
            exited = sorted([list(t) for t in delta.exited])
            m0_steps.append({
                "entered": entered,
                "exited": exited,
                "entered_count": len(entered),
                "exited_count": len(exited),
            })
        return m0_steps, ""
    except Exception as e:
        return None, str(e)


def load_existing_ledger(
    ledger_path: Path,
    expected_identity: Optional[str] = None,
    expected_header: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Loads raw JSONL experiment ledger fail-closed:
    - Raises CorruptedLedgerError on any unparseable JSON line or non-dict record.
    - Validates complete record integrity (field schema, types, limits, and self-consistency).
    - If expected_identity or expected_header is provided, verifies that every resumed slot matches.
    - If a record claims expected_identity but fails integrity/self-consistency, raises CorruptedLedgerError fail-closed.
    - Resolves duplicate attempt lineage using unified resolve_canonical_attempts policy.
    - Disallows arbitrary / silent selection of conflicting verified attempts.
    Returns (resumable_slots, duplicate_attempts).
    """
    resumable_slots: Dict[str, Dict[str, Any]] = {}
    duplicate_attempts: Dict[str, List[Dict[str, Any]]] = {}

    if not ledger_path.is_file():
        return resumable_slots, duplicate_attempts

    target_header = expected_header if expected_header is not None else expected_identity

    records: List[Dict[str, Any]] = []
    line_no = 0
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line_no += 1
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as jde:
                raise CorruptedLedgerError(f"Corrupted JSON in ledger {ledger_path} at line {line_no}: {jde}") from jde
            if not isinstance(rec, dict):
                raise CorruptedLedgerError(f"Invalid record type (not dict) at line {line_no} in {ledger_path}")
            records.append(rec)

    canonical_slots, duplicate_lineage, conflicts = resolve_canonical_attempts(
        records=records,
        expected_header=target_header,
        strict_raise=True,
    )
    return canonical_slots, duplicate_lineage


def main():
    parser = argparse.ArgumentParser(description="RapidFlow-APX Phase P4a Native Profiler Runner")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Workload manifest JSON")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH, help="Protocol specification MD")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="Append-only raw ledger JSONL")
    parser.add_argument("--output", type=Path, default=DEFAULT_SUMMARY, help="Canonical summary JSON")
    parser.add_argument("--clean-build", action="store_true", help="Clean rebuild of native harness")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow dirty working tree for development")
    parser.add_argument("--workload", type=str, default=None, help="Filter to specific workload case_id")
    parser.add_argument("--repetitions", type=int, default=3, help="Number of measured pairs per workload")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-run timeout in seconds")
    parser.add_argument("--as-limit-mib", type=int, default=512, help="RLIMIT_AS limit in MiB")
    parser.add_argument("--warmup", action="store_true", help="Explicit disposable warm-up check")
    args = parser.parse_args()

    # Strict resource limits and configuration validation (R2-F08)
    if (
        args.timeout is None
        or math.isnan(args.timeout)
        or math.isinf(args.timeout)
        or args.timeout <= 0
        or args.timeout > 60.0
    ):
        print(f"ERROR: Invalid --timeout: {args.timeout}. Must be > 0 and <= 60.0 seconds.", file=sys.stderr)
        sys.exit(1)
    if args.as_limit_mib is None or args.as_limit_mib <= 0 or args.as_limit_mib > 512:
        print(f"ERROR: Invalid --as-limit-mib: {args.as_limit_mib}. Must be > 0 and <= 512 MiB.", file=sys.stderr)
        sys.exit(1)
    if args.repetitions is None or args.repetitions <= 0 or args.repetitions > 10:
        print(f"ERROR: Invalid --repetitions: {args.repetitions}. Must be > 0 and <= 10.", file=sys.stderr)
        sys.exit(1)
    if CANONICAL_MAX_WORK_CAP > 500000:
        print(f"ERROR: CANONICAL_MAX_WORK_CAP exceeds approved ceiling: {CANONICAL_MAX_WORK_CAP} > 500000", file=sys.stderr)
        sys.exit(1)

    print("=" * 80)
    print("      RapidFlow-APX Native Profiling & Measurement Runner (Phase P4a)     ")
    print("=" * 80)

    # 1. Git Provenance
    print("[1/5] Verifying fail-closed Git provenance...")
    prov_meta, prov_ok = verify_git_provenance(allow_dirty=args.allow_dirty)
    print(f"  Repository Commit: {prov_meta.get('repo_commit')} (State: {prov_meta.get('dirty_state')})")
    print(f"  Upstream Commit:   {prov_meta.get('upstream_commit')} (Pristine: {prov_meta.get('upstream_pristine')})")
    if not prov_ok and not args.allow_dirty:
        print("ERROR: Fail-closed Git provenance verification failed!", file=sys.stderr)
        sys.exit(1)

    measurement_mode = "CANONICAL_TIMED" if prov_meta.get("dirty_state") == "CLEAN" else "EXPLORATORY_DIRTY"

    # 2. System Resources
    print("[2/5] Checking host system resources...")
    res_meta, res_ok = check_system_resources()
    print(f"  Available RAM: {res_meta['mem_available_gib']:.2f} GiB | Free Disk: {res_meta['free_disk_gib']:.2f} GiB")
    if not res_ok:
        print("ERROR: Preflight resource requirements not met (RAM >= 2.0 GiB, Disk >= 1.0 GiB)!", file=sys.stderr)
        sys.exit(1)

    # 3. Build Native Harness
    print("[3/5] Building native test harness with resource limits...")
    bin_path, bin_sha, build_meta, build_ok = build_native_binary(clean_build=args.clean_build)
    if not build_ok:
        print(f"ERROR: Failed to build native binary: {build_meta.get('build_error')}", file=sys.stderr)
        sys.exit(1)
    print(f"  Harness Binary: {bin_path}")
    print(f"  Binary SHA-256: {bin_sha}")

    # 4. Load Manifest and Protocol
    print(f"[4/5] Loading manifest from {args.manifest}...")
    if not args.manifest.is_file():
        print(f"ERROR: Manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)
    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    manifest_sha256 = compute_sha256(args.manifest)

    protocol_sha256 = compute_sha256(args.protocol) if args.protocol.is_file() else "UNKNOWN_PROTOCOL_SHA"

    experiment_header = create_experiment_header(
        manifest_path=args.manifest,
        protocol_version=PROTOCOL_VERSION,
        protocol_sha256=protocol_sha256,
        source_commit=prov_meta.get("repo_commit") or "UNKNOWN_COMMIT",
        source_dirty_state=prov_meta.get("dirty_state", "UNKNOWN"),
        binary_sha256=bin_sha,
        effective_max_work=CANONICAL_MAX_WORK_CAP,
        effective_timeout=args.timeout,
        effective_memory_limit_mib=args.as_limit_mib,
    )
    execution_identity = experiment_header["execution_identity"]
    print(f"  Execution Identity: {execution_identity[:16]}... (Full: {execution_identity})")

    cases = manifest_data.get("cases", [])
    if args.workload:
        cases = [c for c in cases if c.get("case_id") == args.workload]
        if not cases:
            print(f"ERROR: Workload '{args.workload}' not found in manifest!", file=sys.stderr)
            sys.exit(1)
    print(f"  Loaded {len(cases)} workloads from manifest (SHA: {manifest_sha256[:16]}...)")

    # 5. Execute Paired Pilot Matrix
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    try:
        existing_ledger, duplicate_lineage = load_existing_ledger(args.ledger, expected_header=experiment_header)
    except CorruptedLedgerError as cle:
        print(f"ERROR: Fail-closed rejection of corrupted ledger: {cle}", file=sys.stderr)
        sys.exit(1)

    print(f"[5/5] Executing Paired Pilot Matrix ({len(cases)} workloads x {args.repetitions} pairs x 2 engines = {len(cases) * args.repetitions * 2} slots)...")
    if existing_ledger:
        print(f"  Resuming from existing verified ledger: {len(existing_ledger)} slots already completed")

    ledger_file = open(args.ledger, "a", encoding="utf-8")
    run_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_id = f"P4A_RUN_{int(time.time())}"

    all_results: List[Dict[str, Any]] = []
    total_slots = len(cases) * args.repetitions * 2
    executed_slots = 0
    skipped_slots = 0
    correctness_violations = 0

    slot_counters: Dict[str, int] = {
        "planned_slots": total_slots,
        "attempted_slots": 0,
        "completed_slots": 0,
        "verified_slots": 0,
        "admitted_slots": 0,
        "rejected_slots": 0,
        "censored_slots": 0,
        "timeout_slots": 0,
        "work_limit_slots": 0,
        "oom_slots": 0,
        "oracle_failed_slots": 0,
        "execution_failed_slots": 0,
        "warmup_failed_slots": 0,
        "skipped_verified_slots": 0,
        "correctness_violations": 0,
    }

    for w_idx, wl in enumerate(cases):
        case_id = wl["case_id"]
        q_type = wl.get("query_type", "unknown")
        k = wl.get("k", 0)
        regime = wl.get("regime", "unknown")
        topology = wl.get("topology", "unknown")
        wl_sha256 = compute_dict_sha256(wl)

        # Compute M0 ground truth fail-closed once per workload
        m0_ground_truth, m0_error = compute_m0_ground_truth(wl)
        if m0_ground_truth is None:
            print(f"  [M0] WARNING: Oracle failed on {case_id}: {m0_error}", file=sys.stderr)

        for rep in range(args.repetitions):
            # AB/BA alternating order:
            # rep % 2 == 0: M1 first, then M2
            # rep % 2 == 1: M2 first, then M1
            order = ["m1", "m2"] if (rep % 2 == 0) else ["m2", "m1"]

            for pos, engine in enumerate(order):
                slot_key = f"{case_id}_rep{rep}_{engine}"
                attempt_id = f"{run_id}_{case_id}_rep{rep}_{engine}"

                if slot_key in existing_ledger:
                    skipped_slots += 1
                    slot_counters["skipped_verified_slots"] += 1
                    all_results.append(existing_ledger[slot_key])
                    continue

                slot_counters["attempted_slots"] += 1

                # 1. Disposable Warm-Up Run (untimed state, discard)
                warm_res = execute_native_run(
                    binary_path=bin_path,
                    engine=engine,
                    workload_data=copy.deepcopy(wl),
                    profile=False,
                    timeout_s=args.timeout,
                    as_limit_mib=args.as_limit_mib,
                )

                failure_stage = "NONE"
                failure_category = "NONE"
                m0_agreed = False
                correctness_status = "NOT_CHECKED"
                performance_admission_status = "REJECTED"
                correctness_notes = ""

                if warm_res["status"] != "COMPLETED":
                    # Warm-up failed! Fail-closed: block measured replay for this slot (R2-F08, R3-F06)
                    print(
                        f"  [WARN] Warm-up failed for {slot_key} with status {warm_res['status']} "
                        f"(exit={warm_res['exit_code']})",
                        file=sys.stderr,
                    )
                    run_status = warm_res["status"] if warm_res["status"] in ("TIMEOUT", "WORK_LIMIT", "OOM_CENSORED", "SIGNAL_KILL_CENSORED") else "WARMUP_FAILED"
                    failure_stage = "WARMUP"
                    failure_category = warm_res["status"]
                    correctness_status = "NOT_CHECKED"
                    correctness_notes = f"Disposable warm-up execution failed during stage WARMUP with status {warm_res['status']}"
                    performance_admission_status = "REJECTED"
                    run_res = warm_res
                    out_data = warm_res.get("output_data") or {}
                    executed_slots += 1
                    slot_counters["warmup_failed_slots"] += 1
                else:
                    # 2. Timed Measured Run (fresh state)
                    run_res = execute_native_run(
                        binary_path=bin_path,
                        engine=engine,
                        workload_data=copy.deepcopy(wl),
                        profile=True,
                        timeout_s=args.timeout,
                        as_limit_mib=args.as_limit_mib,
                    )
                    executed_slots += 1
                    out_data = run_res.get("output_data") or {}

                    # Correctness & Admission Verification (P4A-F03)
                    if m0_ground_truth is None:
                        failure_stage = "ORACLE"
                        failure_category = "ORACLE_FAILED"
                        correctness_status = "ORACLE_FAILED"
                        correctness_notes = f"M0 Oracle error: {m0_error}"
                        run_status = "ORACLE_FAILED"
                    elif run_res["status"] == "COMPLETED" and "steps" in out_data:
                        steps = out_data["steps"]
                        if len(steps) != len(m0_ground_truth):
                            failure_stage = "MEASURED_RUN"
                            failure_category = "STEP_COUNT_MISMATCH"
                            correctness_status = "STEP_COUNT_MISMATCH"
                            correctness_notes = f"Step count mismatch: obs={len(steps)} vs exp={len(m0_ground_truth)}"
                            run_status = "FAILED_CORRECTNESS"
                        else:
                            match_all = True
                            for s_idx, (s_obs, s_exp) in enumerate(zip(steps, m0_ground_truth)):
                                # Check raw duplicates before unique/set comparison (P4A-F03)
                                obs_entered_raw = s_obs.get("entered_tuples", [])
                                obs_entered_uniq = [list(t) for t in dict.fromkeys(tuple(x) for x in obs_entered_raw)]
                                if len(obs_entered_raw) != len(obs_entered_uniq):
                                    match_all = False
                                    failure_stage = "MEASURED_RUN"
                                    failure_category = "DUPLICATE_EMISSION"
                                    correctness_status = "DUPLICATE_EMISSION"
                                    correctness_notes = f"Step {s_idx} contains duplicate entered tuples"
                                    break

                                obs_exited_raw = s_obs.get("exited_tuples", [])
                                obs_exited_uniq = [list(t) for t in dict.fromkeys(tuple(x) for x in obs_exited_raw)]
                                if len(obs_exited_raw) != len(obs_exited_uniq):
                                    match_all = False
                                    failure_stage = "MEASURED_RUN"
                                    failure_category = "DUPLICATE_EMISSION"
                                    correctness_status = "DUPLICATE_EMISSION"
                                    correctness_notes = f"Step {s_idx} contains duplicate exited tuples"
                                    break

                                if sorted(obs_entered_uniq) != s_exp["entered"]:
                                    match_all = False
                                    failure_stage = "MEASURED_RUN"
                                    failure_category = "TUPLE_MISMATCH"
                                    correctness_status = "TUPLE_MISMATCH"
                                    correctness_notes = f"Step {s_idx} entered tuples mismatch against M0"
                                    break
                                if sorted(obs_exited_uniq) != s_exp["exited"]:
                                    match_all = False
                                    failure_stage = "MEASURED_RUN"
                                    failure_category = "TUPLE_MISMATCH"
                                    correctness_status = "TUPLE_MISMATCH"
                                    correctness_notes = f"Step {s_idx} exited tuples mismatch against M0"
                                    break

                            if match_all:
                                m0_agreed = True
                                correctness_status = "M0_VERIFIED"
                                performance_admission_status = "ACCEPTED"
                                run_status = "COMPLETED_VERIFIED"
                            else:
                                run_status = "FAILED_CORRECTNESS"
                    else:
                        run_status = run_res["status"]
                        failure_stage = "MEASURED_RUN"
                        failure_category = run_res["status"]
                        correctness_notes = f"Subprocess terminated with status {run_res['status']}"

                # Update slot counters (R2-F04)
                if run_status == "COMPLETED_VERIFIED":
                    slot_counters["completed_slots"] += 1
                    slot_counters["verified_slots"] += 1
                    slot_counters["admitted_slots"] += 1
                else:
                    slot_counters["rejected_slots"] += 1
                    if run_status == "TIMEOUT":
                        slot_counters["timeout_slots"] += 1
                        slot_counters["censored_slots"] += 1
                    elif run_status == "WORK_LIMIT":
                        slot_counters["work_limit_slots"] += 1
                        slot_counters["censored_slots"] += 1
                    elif run_status in ("OOM_CENSORED", "SIGNAL_KILL_CENSORED"):
                        slot_counters["oom_slots"] += 1
                        slot_counters["censored_slots"] += 1
                    elif run_status == "ORACLE_FAILED":
                        slot_counters["oracle_failed_slots"] += 1
                    elif run_status == "FAILED_CORRECTNESS":
                        slot_counters["correctness_violations"] += 1
                        correctness_violations += 1
                        print(f"  [FAIL] Correctness violation on {slot_key}: {correctness_notes} ({correctness_status})")
                    elif run_status == "WARMUP_FAILED":
                        slot_counters["warmup_failed_slots"] += 1
                    else:
                        slot_counters["execution_failed_slots"] += 1

                # Extract profiling & counters
                profiling_info = out_data.get("profiling", {})
                counters_info = out_data.get("counters", {})

                record: Dict[str, Any] = {
                    "execution_identity": execution_identity,
                    "protocol_version": PROTOCOL_VERSION,
                    "protocol_sha256": protocol_sha256,
                    "manifest_sha256": manifest_sha256,
                    "workload_id": case_id,
                    "workload_sha256": wl_sha256,
                    "source_commit": prov_meta.get("repo_commit"),
                    "source_dirty": prov_meta.get("dirty_state"),
                    "source_dirty_state": prov_meta.get("dirty_state"),
                    "upstream_commit": prov_meta.get("upstream_commit"),
                    "upstream_pristine": prov_meta.get("upstream_pristine"),
                    "binary_sha256": bin_sha,
                    "engine": engine,
                    "measurement_mode": measurement_mode,
                    "effective_max_work": CANONICAL_MAX_WORK_CAP,
                    "effective_timeout": args.timeout,
                    "effective_memory_limit_mib": args.as_limit_mib,
                    "effective_as_limit_mib": args.as_limit_mib,
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "slot_key": slot_key,
                    "timestamp": run_timestamp,
                    "query_type": q_type,
                    "k": k,
                    "regime": regime,
                    "topology": topology,
                    "repetition": rep,
                    "order_position": pos,
                    "status": run_status,
                    "exit_code": run_res["exit_code"],
                    "failure_stage": failure_stage,
                    "failure_category": failure_category,
                    "outer_wall_ns": run_res["outer_wall_ns"],
                    "steps_count": len(out_data.get("steps", [])),
                    "correctness_status": correctness_status,
                    "performance_admission_status": performance_admission_status,
                    "m0_agreed": m0_agreed,
                    "correctness_notes": correctness_notes,
                    "profiling": profiling_info,
                    "counters": counters_info,
                }

                all_results.append(record)
                ledger_file.write(json.dumps(record, sort_keys=True) + "\n")
                ledger_file.flush()
                os.fsync(ledger_file.fileno())

        print(f"  -> Completed workload [{w_idx+1}/{len(cases)}]: {case_id} ({q_type}, k={k}, {regime}, {topology})")

    ledger_file.close()

    # 6. Generate Canonical Summary & Determine Overall Verdict (R2-F04)
    effective_admitted = slot_counters["admitted_slots"] + slot_counters["skipped_verified_slots"]
    has_critical_failures = (
        slot_counters["timeout_slots"] > 0
        or slot_counters["work_limit_slots"] > 0
        or slot_counters["oom_slots"] > 0
        or slot_counters["oracle_failed_slots"] > 0
        or slot_counters["execution_failed_slots"] > 0
        or slot_counters["warmup_failed_slots"] > 0
        or slot_counters["correctness_violations"] > 0
    )

    if (
        not has_critical_failures
        and effective_admitted == slot_counters["planned_slots"]
        and slot_counters["planned_slots"] > 0
    ):
        overall_verdict = "CANONICAL_PASS"
    elif slot_counters["correctness_violations"] > 0 or slot_counters["oracle_failed_slots"] > 0:
        overall_verdict = "CANONICAL_FAILED"
    else:
        overall_verdict = "CANONICAL_INCOMPLETE"

    summary: Dict[str, Any] = {
        "execution_identity": execution_identity,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_sha256": protocol_sha256,
        "experiment_header": experiment_header,
        "run_id": run_id,
        "timestamp": run_timestamp,
        "manifest_path": str(args.manifest),
        "manifest_sha256": manifest_sha256,
        "binary_sha256": bin_sha,
        "git_provenance": prov_meta,
        "system_resources": res_meta,
        "overall_verdict": overall_verdict,
        "slot_counters": slot_counters,
        "total_slots": total_slots,
        "executed_slots": executed_slots,
        "skipped_slots": skipped_slots,
        "correctness_violations": slot_counters["correctness_violations"],
        "total_workloads": len(cases),
        "repetitions_per_workload": args.repetitions,
        "results": all_results,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("=" * 80)
    print("                      Phase P4a Pilot Execution Finished                      ")
    print(f"  Slots Planned:  {slot_counters['planned_slots']} | Admitted: {effective_admitted} | Verdict: {overall_verdict}")
    print(f"  Timeouts:       {slot_counters['timeout_slots']} | Violations: {slot_counters['correctness_violations']}")
    print(f"  Execution ID:   {execution_identity[:16]}... (Full: {execution_identity})")
    print(f"  Raw Ledger:     {args.ledger}")
    print(f"  Summary Report: {args.output}")
    print("=" * 80)

    if overall_verdict != "CANONICAL_PASS":
        print(f"ERROR: Experiment concluded with non-canonical verdict: {overall_verdict}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
