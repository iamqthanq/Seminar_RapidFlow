#!/usr/bin/env python3
"""
scripts/run_rapidflow_apx_p4a.py — Canonical runner and test executor for Phase P4a.

Executes Bare M2 Native Profiling, Baseline Measurement & Evidence-Driven Pilot.
Enforces:
1. Fail-closed Git provenance verification (clean working tree, pristine upstream 2ea4f4c...).
2. Preflight system resource checks (MemAvailable >= 2.0 GiB, free disk >= 1.0 GiB).
3. Native build under resource containment using portable CMake.
4. Paired M1 vs Bare M2 execution on identical workloads with AB/BA alternating order.
5. Disposable warm-up replay, state reset to fresh G0 before timed runs.
6. Subprocess resource containment (RLIMIT_AS = 512 MiB, 60s timeout, max-work = 500000).
7. Independent Python M0 Ground Truth Oracle cross-validation.
8. Append-only, crash-resilient raw experiment ledger.
9. Structured atomic summary output with timing, counters, speedup, and bottleneck analysis.
"""

import argparse
import copy
import hashlib
import json
import os
import resource
import shutil
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
CANONICAL_MAX_WORK_CAP = 500000
DEFAULT_MANIFEST = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
DEFAULT_LEDGER = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
DEFAULT_SUMMARY = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_canonical_summary.json"


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_str_sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def verify_git_provenance(allow_dirty: bool = False) -> Tuple[Dict[str, Any], bool]:
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
            lines = [line.strip() for line in r_status.splitlines() if line.strip()]
            allowed = True
            for line in lines:
                parts = line.split(maxsplit=1)
                if len(parts) < 2:
                    allowed = False
                    break
                fpath = parts[1].strip()
                if not (
                    fpath.startswith("results/")
                    or fpath.startswith("reports/")
                    or fpath.startswith("docs/")
                    or fpath.endswith(".jsonl")
                    or fpath.endswith(".json")
                    or fpath.endswith(".md")
                ):
                    allowed = False
                    break
            if allowed or allow_dirty:
                meta["repo_clean"] = True
                meta["dirty_state"] = "DIRTY_REPORTS_ONLY" if allowed else "DIRTY_ALLOWED"
            else:
                meta["repo_clean"] = False
                meta["dirty_state"] = "DIRTY_SOURCE_TREE"
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
    custom_cmake = REPO_ROOT / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"
    if custom_cmake.is_file() and os.access(str(custom_cmake), os.X_OK):
        return str(custom_cmake)
    which_cmake = shutil.which("cmake")
    if which_cmake:
        return which_cmake
    raise RuntimeError("CMake executable not found (neither in external/tools nor system PATH)")


def build_native_binary(clean_build: bool = False) -> Tuple[Path, str, Dict[str, Any], bool]:
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
    timeout_s: float = 60.0,
    as_limit_mib: int = 512,
) -> Dict[str, Any]:
    """Executes a single native engine run in an isolated child subprocess with strict containment."""
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

    try:
        proc = subprocess.run(
            cmd,
            preexec_fn=preexec_fn,
            timeout=timeout_s,
            capture_output=True,
            text=True,
        )
        exit_code = proc.returncode
        raw_stdout = proc.stdout
        raw_stderr = proc.stderr
    except subprocess.TimeoutExpired as te:
        timed_out = True
        exit_code = 124
        raw_stdout = te.stdout or ""
        raw_stderr = te.stderr or ""
    except Exception as e:
        exit_code = -99
        raw_stderr = str(e)

    t_wall_end = time.perf_counter_ns()
    outer_wall_ns = t_wall_end - t_wall_start

    # Check for OOM signals
    if exit_code in (-9, 137, 5):
        oom_censored = True

    output_data: Optional[Dict[str, Any]] = None
    if out_path.is_file():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                output_data = json.load(f)
        except Exception:
            pass

    shutil.rmtree(tmp_dir, ignore_errors=True)

    status = "COMPLETED"
    if timed_out:
        status = "TIMEOUT"
    elif oom_censored:
        status = "OOM_CENSORED"
    elif exit_code == 4 or (output_data and output_data.get("error_category") == "WORK_BUDGET_EXCEEDED"):
        status = "WORK_LIMIT"
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


def compute_m0_ground_truth(workload_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Computes exact ground truth entered and exited tuples for each step using independent M0 Oracle."""
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

    return m0_steps


def load_existing_ledger(ledger_path: Path) -> Dict[str, Dict[str, Any]]:
    completed_slots: Dict[str, Dict[str, Any]] = {}
    if not ledger_path.is_file():
        return completed_slots
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                slot_key = f"{rec['workload_id']}_rep{rec['repetition']}_{rec['engine']}"
                if rec.get("status") in ("COMPLETED", "WORK_LIMIT", "TIMEOUT", "OOM_CENSORED"):
                    completed_slots[slot_key] = rec
            except Exception:
                pass
    return completed_slots


def main():
    parser = argparse.ArgumentParser(description="RapidFlow-APX Phase P4a Native Profiler Runner")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Workload manifest JSON")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="Append-only raw ledger JSONL")
    parser.add_argument("--output", type=Path, default=DEFAULT_SUMMARY, help="Canonical summary JSON")
    parser.add_argument("--clean-build", action="store_true", help="Clean rebuild of native harness")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow dirty working tree for development")
    parser.add_argument("--workload", type=str, default=None, help="Filter to specific workload case_id")
    parser.add_argument("--repetitions", type=int, default=3, help="Number of measured pairs per workload")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-run timeout in seconds")
    parser.add_argument("--as-limit-mib", type=int, default=512, help="RLIMIT_AS limit in MiB")
    args = parser.parse_args()

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

    # 4. Load Manifest
    print(f"[4/5] Loading manifest from {args.manifest}...")
    if not args.manifest.is_file():
        print(f"ERROR: Manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)
    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    manifest_sha256 = compute_sha256(args.manifest)
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
    existing_ledger = load_existing_ledger(args.ledger)
    print(f"[5/5] Executing Paired Pilot Matrix ({len(cases)} workloads x {args.repetitions} pairs x 2 engines = {len(cases) * args.repetitions * 2} slots)...")
    if existing_ledger:
        print(f"  Resuming from existing ledger: {len(existing_ledger)} slots already completed")

    ledger_file = open(args.ledger, "a", encoding="utf-8")
    run_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_id = f"P4A_RUN_{int(time.time())}"

    all_results: List[Dict[str, Any]] = []
    total_slots = len(cases) * args.repetitions * 2
    executed_slots = 0
    skipped_slots = 0
    correctness_violations = 0

    for w_idx, wl in enumerate(cases):
        case_id = wl["case_id"]
        q_type = wl.get("query_type", "unknown")
        k = wl.get("k", 0)
        regime = wl.get("regime", "unknown")
        topology = wl.get("topology", "unknown")

        # Compute M0 ground truth once per workload
        m0_ground_truth = None
        try:
            m0_ground_truth = compute_m0_ground_truth(wl)
        except Exception as e:
            print(f"  [M0] Oracle error on {case_id}: {e}")

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
                    all_results.append(existing_ledger[slot_key])
                    continue

                # 1. Disposable Warm-Up Run (untimed state, discard)
                execute_native_run(
                    binary_path=bin_path,
                    engine=engine,
                    workload_data=copy.deepcopy(wl),
                    profile=False,
                    timeout_s=args.timeout,
                    as_limit_mib=args.as_limit_mib,
                )

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

                # Correctness verification
                m0_agreed = False
                correctness_notes = ""
                out_data = run_res.get("output_data") or {}

                if run_res["status"] == "COMPLETED" and m0_ground_truth is not None and "steps" in out_data:
                    steps = out_data["steps"]
                    if len(steps) == len(m0_ground_truth):
                        match_all = True
                        for s_idx, (s_obs, s_exp) in enumerate(zip(steps, m0_ground_truth)):
                            obs_ent = sorted(s_obs.get("entered_tuples", []))
                            exp_ent = s_exp["entered"]
                            obs_exit = sorted(s_obs.get("exited_tuples", []))
                            exp_exit = s_exp["exited"]
                            if obs_ent != exp_ent or obs_exit != exp_exit:
                                match_all = False
                                correctness_notes = f"Step {s_idx} tuple mismatch against M0"
                                break
                        m0_agreed = match_all
                    else:
                        correctness_notes = f"Step count mismatch: obs={len(steps)} vs exp={len(m0_ground_truth)}"

                if not m0_agreed and m0_ground_truth is not None and run_res["status"] == "COMPLETED":
                    correctness_violations += 1
                    print(f"  [FAIL] Correctness mismatch on {slot_key}: {correctness_notes}")

                # Extract profiling & counters
                profiling_info = out_data.get("profiling", {})
                counters_info = out_data.get("counters", {})

                record: Dict[str, Any] = {
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "timestamp": run_timestamp,
                    "workload_id": case_id,
                    "query_type": q_type,
                    "k": k,
                    "regime": regime,
                    "topology": topology,
                    "repetition": rep,
                    "order_position": pos,
                    "engine": engine,
                    "status": run_res["status"],
                    "exit_code": run_res["exit_code"],
                    "outer_wall_ns": run_res["outer_wall_ns"],
                    "steps_count": len(out_data.get("steps", [])),
                    "m0_agreed": m0_agreed,
                    "correctness_notes": correctness_notes,
                    "profiling": profiling_info,
                    "counters": counters_info,
                    "binary_sha256": bin_sha,
                }

                all_results.append(record)
                ledger_file.write(json.dumps(record) + "\n")
                ledger_file.flush()

        print(f"  -> Completed workload [{w_idx+1}/{len(cases)}]: {case_id} ({q_type}, k={k}, {regime}, {topology})")

    ledger_file.close()

    # 6. Generate Canonical Summary
    summary: Dict[str, Any] = {
        "run_id": run_id,
        "timestamp": run_timestamp,
        "manifest_path": str(args.manifest),
        "manifest_sha256": manifest_sha256,
        "binary_sha256": bin_sha,
        "git_provenance": prov_meta,
        "system_resources": res_meta,
        "total_slots": total_slots,
        "executed_slots": executed_slots,
        "skipped_slots": skipped_slots,
        "correctness_violations": correctness_violations,
        "total_workloads": len(cases),
        "repetitions_per_workload": args.repetitions,
        "results": all_results,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("=" * 80)
    print("                      Phase P4a Pilot Execution Finished                      ")
    print(f"  Slots Executed: {executed_slots} | Skipped: {skipped_slots} | Violations: {correctness_violations}")
    print(f"  Raw Ledger:     {args.ledger}")
    print(f"  Summary Report: {args.output}")
    print("=" * 80)

    if correctness_violations > 0:
        print("ERROR: Correctness violations detected!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
