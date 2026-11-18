#!/usr/bin/env python3
"""
scripts/dstar03_resources.py — Subprocess Safety, Limiter Guards, and Containment Evidence.

Provides:
1. Environmental preflight verification (MemAvailable >= 2 GiB, DiskFree >= 1 GiB).
2. Subprocess execution with hard Linux RLIMIT_AS address-space ceiling (512 MiB) and wall timeout (60s).
3. Process group isolation (os.setsid) and clean termination (SIGKILL to process group).
4. Truthful signal classification (never misrepresenting SIGSEGV/SIGBUS as memory limit without evidence).
5. Chunked cryptographic hashing of files (hash_file_sha256).
6. Strict validation of resource parameters (finite, positive, within approved ceilings).
7. Contained disposable self-tests and structured containment evidence recording.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_RLIMIT_AS_BYTES = 512 * 1024 * 1024  # 512 MiB (hard address-space cap)
DEFAULT_TIMEOUT_SECONDS = 60.0  # 60 seconds (wall-clock timeout)
DEFAULT_MIN_MEM_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_MIN_DISK_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB

MAX_APPROVED_RLIMIT_AS_BYTES = 512 * 1024 * 1024
MAX_APPROVED_TIMEOUT_SECONDS = 60.0


def hash_file_sha256(path: Path | str, chunk_size: int = 65536) -> str:
    """Computes SHA-256 hex digest of a file using bounded chunked reads."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found for hashing: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def validate_resource_limits(
    timeout_seconds: Any,
    address_space_bytes: Any,
) -> None:
    """
    Validates that resource limits are positive, finite numbers within approved pilot ceilings.
    Raises ValueError or TypeError if limits are invalid.
    """
    # Validate timeout_seconds
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TypeError(f"timeout_seconds must be a number, got {type(timeout_seconds).__name__}")
    if math.isnan(timeout_seconds) or math.isinf(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be positive and finite, got {timeout_seconds}")
    if timeout_seconds > MAX_APPROVED_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout_seconds {timeout_seconds} exceeds maximum approved limit {MAX_APPROVED_TIMEOUT_SECONDS}"
        )

    # Validate address_space_bytes
    if isinstance(address_space_bytes, bool) or not isinstance(address_space_bytes, int):
        raise TypeError(
            f"address_space_bytes must be an integer, got {type(address_space_bytes).__name__}"
        )
    if address_space_bytes <= 0:
        raise ValueError(f"address_space_bytes must be positive, got {address_space_bytes}")
    if address_space_bytes > MAX_APPROVED_RLIMIT_AS_BYTES:
        raise ValueError(
            f"address_space_bytes {address_space_bytes} exceeds maximum approved ceiling {MAX_APPROVED_RLIMIT_AS_BYTES}"
        )


def check_environment_preflight(
    min_mem_bytes: int = DEFAULT_MIN_MEM_BYTES,
    min_disk_bytes: int = DEFAULT_MIN_DISK_BYTES,
    target_dir: str = ".",
) -> Dict[str, Any]:
    """
    Verifies that system memory and disk free space meet minimum safety thresholds.
    Fails closed if /proc/meminfo or statvfs cannot be read.
    """
    result: Dict[str, Any] = {
        "status": "PASS",
        "mem_available_bytes": None,
        "mem_available_gib": None,
        "disk_free_bytes": None,
        "disk_free_gib": None,
        "reason": None,
    }

    # 1. Check MemAvailable
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        result["status"] = "FAIL"
        result["reason"] = "/proc/meminfo does not exist"
        return result

    try:
        mem_available = None
        with meminfo_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    mem_available = int(parts[1]) * 1024  # kB to bytes
                    break
        if mem_available is None:
            result["status"] = "FAIL"
            result["reason"] = "MemAvailable field not found in /proc/meminfo"
            return result

        result["mem_available_bytes"] = mem_available
        result["mem_available_gib"] = round(mem_available / (1024**3), 3)

        if mem_available < min_mem_bytes:
            result["status"] = "FAIL"
            result["reason"] = (
                f"MemAvailable ({result['mem_available_gib']} GiB) is below required floor "
                f"({round(min_mem_bytes / (1024**3), 2)} GiB)"
            )
            return result
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"Failed to read memory info: {exc}"
        return result

    # 2. Check Disk Free
    try:
        stat = os.statvfs(target_dir)
        disk_free = stat.f_bavail * stat.f_frsize
        result["disk_free_bytes"] = disk_free
        result["disk_free_gib"] = round(disk_free / (1024**3), 3)

        if disk_free < min_disk_bytes:
            result["status"] = "FAIL"
            result["reason"] = (
                f"DiskFree ({result['disk_free_gib']} GiB) is below required floor "
                f"({round(min_disk_bytes / (1024**3), 2)} GiB)"
            )
            return result
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"Failed to check disk free space: {exc}"
        return result

    return result


def _child_preexec(limit_bytes: int) -> None:
    """Pre-exec function executed in the forked child prior to exec."""
    os.setsid()  # Create new session/process group for clean killing
    # Hard address-space ceiling (RLIMIT_AS), not RSS
    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))


def run_contained_subprocess(
    cmd: List[str],
    input_str: Optional[str] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    limit_as_bytes: int = DEFAULT_RLIMIT_AS_BYTES,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Executes a command in a child subprocess with hard address-space limits (RLIMIT_AS)
    and wall timeout. Guarantees process group cleanup if timeout triggers.
    Provides truthful signal and termination classification.
    """
    validate_resource_limits(timeout_seconds, limit_as_bytes)

    t0 = time.perf_counter_ns()
    sub_env = dict(os.environ)
    if env:
        sub_env.update(env)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input_str is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=sub_env,
            preexec_fn=lambda: _child_preexec(limit_as_bytes),
        )
    except Exception as exc:
        t1 = time.perf_counter_ns()
        return {
            "status": "ERROR",
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Failed to spawn subprocess: {exc}",
            "wall_ns": t1 - t0,
            "timed_out": False,
            "resource_limited": False,
            "termination_reason": f"Spawn failure: {exc}",
        }

    timed_out = False
    resource_limited = False
    stdout = ""
    stderr = ""
    exit_code = -1
    term_signal = None

    try:
        stdout, stderr = proc.communicate(input=input_str, timeout=timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        stdout, stderr = proc.communicate()
        exit_code = -signal.SIGKILL
        term_signal = signal.SIGKILL
    except Exception as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        stderr = f"Subprocess exception: {exc}"
        exit_code = -1

    t1 = time.perf_counter_ns()
    wall_ns = t1 - t0

    # Classify termination truthfully without assuming SIGSEGV/SIGBUS is always OOM
    termination_reason = "NORMAL_EXIT"
    if timed_out:
        status = "TIMEOUT"
        termination_reason = f"Process exceeded wall timeout of {timeout_seconds}s"
    elif "MemoryError" in stderr or "std::bad_alloc" in stderr:
        resource_limited = True
        status = "RESOURCE_LIMIT"
        termination_reason = "Explicit memory allocation failure under RLIMIT_AS (MemoryError/bad_alloc)"
    elif exit_code < 0:
        sig = -exit_code
        term_signal = sig
        sig_name = signal.Signals(sig).name if sig in signal.Signals.__members__.values() else f"SIG_{sig}"
        if sig == signal.SIGKILL:
            status = "KILLED"
            termination_reason = f"Process terminated by {sig_name}"
        elif sig == signal.SIGSEGV:
            status = "SEGMENTATION_FAULT"
            termination_reason = f"Process terminated by {sig_name} (signal {sig})"
        elif sig == signal.SIGBUS:
            status = "BUS_ERROR"
            termination_reason = f"Process terminated by {sig_name} (signal {sig})"
        else:
            status = "SIGNAL_TERMINATION"
            termination_reason = f"Process terminated by {sig_name} (signal {sig})"
    elif exit_code == 0:
        status = "OK"
    else:
        status = "ERROR"
        termination_reason = f"Process exited with non-zero exit code {exit_code}"

    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "wall_ns": wall_ns,
        "timed_out": timed_out,
        "resource_limited": resource_limited,
        "term_signal": term_signal,
        "termination_reason": termination_reason,
        "limits": {
            "rlimit_as_bytes": limit_as_bytes,
            "timeout_seconds": timeout_seconds,
            "limiter_type": "RLIMIT_AS_ADDRESS_SPACE",
        },
    }


def record_containment_evidence(output_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """
    Executes disposable contained child self-tests and records containment evidence.
    Verifies that address space ceiling (RLIMIT_AS) and timeout are strictly enforced.
    Does NOT stress the host machine.
    """
    evidence: Dict[str, Any] = {
        "protocol_version": "dstar03-containment-v2",
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "preflight": None,
        "memory_containment_test": None,
        "timeout_containment_test": None,
        "overall_containment_status": "FAIL",
    }

    # 1. Preflight
    pf = check_environment_preflight()
    evidence["preflight"] = pf
    if pf["status"] != "PASS":
        evidence["reason"] = f"Preflight failed: {pf['reason']}"
        if output_path:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", encoding="utf-8") as f:
                json.dump(evidence, f, indent=2, sort_keys=True)
        return evidence

    # 2. Memory containment test (child attempts 1000 MiB under 512 MiB RLIMIT_AS)
    child_mem_code = "b = bytearray(1000 * 1024 * 1024)"
    mem_res = run_contained_subprocess(
        [sys.executable, "-c", child_mem_code],
        timeout_seconds=5.0,
        limit_as_bytes=512 * 1024 * 1024,
    )
    mem_pass = mem_res["status"] == "RESOURCE_LIMIT" or ("MemoryError" in mem_res["stderr"])
    evidence["memory_containment_test"] = {
        "passed": mem_pass,
        "status": mem_res["status"],
        "exit_code": mem_res["exit_code"],
        "stderr_snippet": mem_res["stderr"][:300],
        "termination_reason": mem_res.get("termination_reason"),
    }

    # 3. Timeout containment test (child sleeps 5.0s under 0.5s timeout)
    child_time_code = "import time; time.sleep(5.0)"
    time_res = run_contained_subprocess(
        [sys.executable, "-c", child_time_code],
        timeout_seconds=0.5,
        limit_as_bytes=512 * 1024 * 1024,
    )
    time_pass = time_res["status"] == "TIMEOUT" and time_res["timed_out"] is True
    evidence["timeout_containment_test"] = {
        "passed": time_pass,
        "status": time_res["status"],
        "exit_code": time_res["exit_code"],
        "wall_ns": time_res["wall_ns"],
        "termination_reason": time_res.get("termination_reason"),
    }

    if mem_pass and time_pass:
        evidence["overall_containment_status"] = "PASS"

    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2, sort_keys=True)

    return evidence


def run_self_tests() -> bool:
    """Runs containment verification and prints summary."""
    evidence = record_containment_evidence()
    status = evidence["overall_containment_status"]
    if status == "PASS":
        print("SELF-TEST PASSED: Preflight, RLIMIT_AS containment, and process group timeout verified.")
        return True
    else:
        print(f"SELF-TEST FAILED: {evidence}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="DSTAR-03 Resource limiter and containment verification.")
    parser.add_argument("--record", type=Path, default=None, help="Path to write containment evidence JSON.")
    args = parser.parse_args()

    evidence = record_containment_evidence(args.record)
    if args.record:
        print(f"Containment evidence recorded to {args.record}")
    success = evidence["overall_containment_status"] == "PASS"
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
