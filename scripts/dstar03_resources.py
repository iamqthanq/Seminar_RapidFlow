#!/usr/bin/env python3
"""
scripts/dstar03_resources.py — Subprocess Safety and Environmental Guardrails.

Provides:
1. Environmental preflight verification (MemAvailable >= 2 GiB, DiskFree >= 1 GiB).
2. Subprocess execution with hard Linux RLIMIT_AS ceiling (512 MiB) and wall timeout (60s).
3. Process group isolation (os.setsid) and clean termination (SIGKILL to process group).
4. Contained disposable self-tests verifying memory limit and timeout enforcement.
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_RLIMIT_AS_BYTES = 512 * 1024 * 1024  # 512 MiB
DEFAULT_TIMEOUT_SECONDS = 60.0  # 60 seconds
DEFAULT_MIN_MEM_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_MIN_DISK_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB


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
    # Hard address-space ceiling
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
    Executes a command in a child subprocess with hard address-space limits and wall timeout.
    Guarantees process group cleanup if timeout triggers.
    """
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
        }

    timed_out = False
    resource_limited = False
    stdout = ""
    stderr = ""
    exit_code = -1

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
    except Exception as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        stderr = f"Subprocess exception: {exc}"
        exit_code = -1

    t1 = time.perf_counter_ns()
    wall_ns = t1 - t0

    if timed_out:
        status = "TIMEOUT"
    elif "MemoryError" in stderr or exit_code in (-signal.SIGSEGV, -signal.SIGBUS, 137, 139):
        resource_limited = True
        status = "RESOURCE_LIMIT"
    elif exit_code == 0:
        status = "OK"
    else:
        status = "ERROR"

    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "wall_ns": wall_ns,
        "timed_out": timed_out,
        "resource_limited": resource_limited,
    }


def run_self_tests() -> bool:
    """
    Demonstrates resource limiter in a disposable child allocation test
    and timeout child test. Does not allocate outside contained child.
    """
    # 1. Preflight
    preflight = check_environment_preflight()
    if preflight["status"] != "PASS":
        print(f"SELF-TEST FAILED: Preflight failed: {preflight['reason']}")
        return False

    # 2. Memory limit test (child attempts 1000 MiB allocation under 512 MiB RLIMIT_AS)
    child_mem_code = "b = bytearray(1000 * 1024 * 1024)"
    mem_res = run_contained_subprocess(
        [sys.executable, "-c", child_mem_code],
        timeout_seconds=5.0,
        limit_as_bytes=512 * 1024 * 1024,
    )
    if mem_res["status"] != "RESOURCE_LIMIT" and not ("MemoryError" in mem_res["stderr"]):
        print(f"SELF-TEST FAILED: Child allocation did not fail as expected: {mem_res}")
        return False

    # 3. Timeout test (child sleeps 5s with timeout 0.5s)
    child_time_code = "import time; time.sleep(5.0)"
    time_res = run_contained_subprocess(
        [sys.executable, "-c", child_time_code],
        timeout_seconds=0.5,
        limit_as_bytes=512 * 1024 * 1024,
    )
    if time_res["status"] != "TIMEOUT":
        print(f"SELF-TEST FAILED: Child timeout did not trigger: {time_res}")
        return False

    print("SELF-TEST PASSED: Preflight, RLIMIT_AS containment, and process group timeout verified.")
    return True


if __name__ == "__main__":
    success = run_self_tests()
    sys.exit(0 if success else 1)
