#!/usr/bin/env python3
"""
scripts/run_rapidflow_apx_engines.py — Canonical runner and test executor for Phase P3 Native Engines.

Verifies:
1. Fail-closed Git provenance verification (repo commit, pristine upstream RapidFlow commit 2ea4f4c...).
2. Preflight system resource checks (MemAvailable >= 2 GiB from /proc/meminfo, free disk >= 1 GiB).
3. Native build under resource containment using portable CMake.
4. Hand & adversarial witness validation (24 cases) for both M1 and M2 engines against M0 Oracle.
5. 840-case correctness corpus verification (seed1 + seed7) with dynamic M0 recomputation.
6. 56-stream stateful matrix verification (672 updates) with step-by-step M0 active-set reconstruction.
7. Invariant guards (k < lambda(Q), connectivity, self-loops) and resource limits (work budget caps).
8. Atomic canonical reporting: summary.json (canonical) vs diagnostic_summary.json (subset).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import os
import random
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_UPSTREAM_COMMIT = "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1"
CMAKE_PATH = REPO_ROOT / "external/tools/cmake-3.28.3-linux-x86_64/bin/cmake"
if not CMAKE_PATH.exists():
    CMAKE_PATH = Path(shutil.which("cmake") or "cmake")

# Ensure REPO_ROOT is in sys.path
sys.path.insert(0, str(REPO_ROOT))
from scripts.dstar_oracle import (
    DStarGraph,
    enumerate_snapshot,
    DStarDynamicOracle,
    DStarUpdate,
)

CANONICAL_MANIFEST_PATH = REPO_ROOT / "configs" / "rapidflow_apx_engines_cases.json"
CANONICAL_SUMMARY_PATH = REPO_ROOT / "reports" / "rapidflow_apx_engines" / "summary.json"
DIAGNOSTIC_SUMMARY_PATH = REPO_ROOT / "reports" / "rapidflow_apx_engines" / "diagnostic_summary.json"


def compute_sha256(path: Path) -> str:
    """Computes SHA-256 digest of a file; returns 'FILE_NOT_FOUND' if missing."""
    if not path.is_file():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def execute_contained_subprocess(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 30,
    address_space_mib: int = 512,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str, bool]:
    """
    Executes a subprocess under memory and wall-clock containment.
    Returns: (exit_code, stdout, stderr, timed_out)
    """
    def preexec():
        os.setpgrp()
        as_bytes = address_space_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))

    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=preexec,
            env=proc_env,
            text=True,
        )
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        return -1, out, err, True
    except Exception as e:
        return -1, "", str(e), False


def check_git_provenance() -> Tuple[Dict[str, Any], bool]:
    """Verifies fail-closed repository and upstream provenance."""
    meta: Dict[str, Any] = {
        "repo_commit": None,
        "repo_clean": False,
        "dirty_state": "UNKNOWN",
        "upstream_commit": None,
        "upstream_pristine": False,
    }

    try:
        r_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        meta["repo_commit"] = r_head
    except Exception as e:
        return meta, False

    try:
        status_raw = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True
        ).strip()
        if not status_raw:
            meta["repo_clean"] = True
            meta["dirty_state"] = "CLEAN"
        else:
            lines = [l for l in status_raw.splitlines() if l.strip()]
            all_reports = all("reports/" in l or "reports\\" in l for l in lines)
            if all_reports:
                meta["repo_clean"] = True
                meta["dirty_state"] = "DIRTY_REPORTS_ONLY"
            else:
                meta["repo_clean"] = False
                meta["dirty_state"] = "DIRTY_SOURCE_TREE"
    except Exception:
        meta["dirty_state"] = "ERROR_STATUS"
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

    valid = meta["repo_commit"] is not None and meta["upstream_pristine"]
    return meta, valid


def check_system_resources() -> Tuple[Dict[str, Any], bool]:
    """Checks host system resources: MemAvailable >= 2 GiB, free disk >= 1 GiB."""
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


def build_native_binary(clean_build: bool = False) -> Tuple[Path, str, Dict[str, Any], bool]:
    """Builds rapidflow_apx_native using portable CMake under containment."""
    build_dir = REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build"
    binary_path = build_dir / "rapidflow_apx_native"

    build_info: Dict[str, Any] = {
        "clean_build": clean_build,
        "configure_command": [str(CMAKE_PATH), ".."],
        "configure_exit_code": 0,
        "build_command": ["make", "-j2"],
        "build_exit_code": 0,
        "duration_ms": 0.0,
    }

    if clean_build and build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()

    # Configure
    ret_c, out_c, err_c, to_c = execute_contained_subprocess(
        [str(CMAKE_PATH), ".."],
        cwd=build_dir,
        timeout=120,
        address_space_mib=1024,
    )
    build_info["configure_exit_code"] = ret_c
    if to_c or ret_c != 0:
        return binary_path, "", build_info, False

    # Build
    ret_b, out_b, err_b, to_b = execute_contained_subprocess(
        ["make", "-j2"],
        cwd=build_dir,
        timeout=120,
        address_space_mib=1024,
    )
    build_info["build_exit_code"] = ret_b
    build_info["duration_ms"] = (time.perf_counter() - t0) * 1000.0

    if to_b or ret_b != 0 or not binary_path.is_file():
        return binary_path, "", build_info, False

    sha = compute_sha256(binary_path)
    return binary_path, sha, build_info, True


def run_workload_on_binary(
    binary_path: Path,
    engine: str,
    workload: Dict[str, Any],
    max_work: int = 500000,
    timeout: int = 30,
    address_space_mib: int = 512,
) -> Tuple[Dict[str, Any], float, bool]:
    """Runs a single workload JSON through rapidflow_apx_native."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as wf, \
         tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as of:
        json.dump(workload, wf)
        wf.flush()

        cmd = [
            str(binary_path),
            "--engine", engine,
            "--workload", wf.name,
            "--output", of.name,
            "--max-work", str(max_work),
        ]

        t0 = time.perf_counter()
        ret, out, err, timed_out = execute_contained_subprocess(
            cmd, cwd=REPO_ROOT, timeout=timeout, address_space_mib=address_space_mib
        )
        duration_ms = (time.perf_counter() - t0) * 1000.0

        res_data: Dict[str, Any] = {}
        if not timed_out and ret == 0 and os.path.exists(of.name):
            try:
                with open(of.name, "r", encoding="utf-8") as rf:
                    res_data = json.load(rf)
            except Exception as e:
                res_data = {"status": "ERROR", "error_message": f"Output parse error: {e}"}
        else:
            status = "TIMEOUT" if timed_out else "EXEC_ERROR"
            res_data = {"status": status, "error_message": err or out}

        try:
            os.remove(wf.name)
            os.remove(of.name)
        except OSError:
            pass

        return res_data, duration_ms, (ret == 0 and not timed_out and res_data.get("status") == "SUCCESS")


# ============================================================================
# STAGE 2: Hand and Adversarial Witness Cases (24 cases)
# ============================================================================

def build_hand_workloads() -> List[Dict[str, Any]]:
    """Builds the 24 canonical hand and adversarial workloads."""
    workloads = []

    # CASE_HAND_01: Diamond distinct labels
    workloads.append({
        "case_id": "CASE_HAND_01",
        "name": "diamond_distinct_labels",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": "e12"},
                {"u": 1, "v": 3, "label": "e13"},
                {"u": 2, "v": 3, "label": "e23"},
                {"u": 2, "v": 4, "label": "e24"},
                {"u": 3, "v": 4, "label": "e34"},
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
            "edges": [
                {"u": 10, "v": 20, "label": "e12"},
                {"u": 10, "v": 30, "label": "e13"},
                {"u": 20, "v": 40, "label": "e24"},
                {"u": 30, "v": 40, "label": "e34"},
            ],
        },
        "updates": [{"op": "+", "u": 20, "v": 30, "label": "e23"}],
    })

    # CASE_HAND_02: Diamond k=0 insert (4 automorphisms)
    workloads.append({
        "case_id": "CASE_HAND_02",
        "name": "diamond_k0_insert",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 2, "v": 3, "label": 0}],
    })

    # CASE_HAND_03: Diamond k=0 delete (4 automorphisms)
    workloads.append({
        "case_id": "CASE_HAND_03",
        "name": "diamond_k0_delete",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "-", "u": 2, "v": 3, "label": 0}],
    })

    # CASE_HAND_04: K4 k=1
    k4_q_edges = [{"u": u, "v": v, "label": 0} for (u, v) in itertools.combinations((1, 2, 3, 4), 2)]
    workloads.append({
        "case_id": "CASE_HAND_04",
        "name": "k4_k1",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": k4_q_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 1, "v": 4, "label": 0}],
    })

    # CASE_HAND_05: K4 k=2
    workloads.append({
        "case_id": "CASE_HAND_05",
        "name": "k4_k2",
        "k": 2,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": k4_q_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 1, "v": 4, "label": 0}],
    })

    # CASE_HAND_06_STEP1: Diamond 2-step delete step 1
    workloads.append({
        "case_id": "CASE_HAND_06_STEP1",
        "name": "diamond_2step_delete_step1",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "-", "u": 2, "v": 3, "label": 0}],
    })

    # CASE_HAND_06_STEP2: Diamond 2-step delete step 2
    workloads.append({
        "case_id": "CASE_HAND_06_STEP2",
        "name": "diamond_2step_delete_step2",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "-", "u": 1, "v": 2, "label": 0}],
    })

    # CASE_HAND_07: No matching label
    workloads.append({
        "case_id": "CASE_HAND_07",
        "name": "no_matching_label",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3)],
            "edges": [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 1, "v": 3, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30)],
            "edges": [{"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0}],
        },
        "updates": [{"op": "+", "u": 10, "v": 30, "label": "WRONG"}],
    })

    # CASE_HAND_08: Non-induced extra chords
    workloads.append({
        "case_id": "CASE_HAND_08",
        "name": "non_induced_extra_chords",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0},
                {"u": 3, "v": 4, "label": 0}, {"u": 1, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
            "edges": [
                {"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0},
                {"u": 30, "v": 40, "label": 0}, {"u": 10, "v": 30, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 10, "v": 40, "label": 0}],
    })

    # CASE_HAND_09: Edge label mismatch
    workloads.append({
        "case_id": "CASE_HAND_09",
        "name": "edge_label_mismatch",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3)],
            "edges": [{"u": 1, "v": 2, "label": "A"}, {"u": 2, "v": 3, "label": "A"}, {"u": 1, "v": 3, "label": "A"}],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30)],
            "edges": [{"u": 10, "v": 20, "label": "A"}, {"u": 20, "v": 30, "label": "B"}],
        },
        "updates": [{"op": "+", "u": 10, "v": 30, "label": "A"}],
    })

    # CASE_HAND_10: Vertex label mismatch
    workloads.append({
        "case_id": "CASE_HAND_10",
        "name": "vertex_label_mismatch",
        "k": 0,
        "query": {
            "vertices": [{"id": 1, "label": "TYPE_A"}, {"id": 2, "label": "TYPE_B"}],
            "edges": [{"u": 1, "v": 2, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": 10, "label": "TYPE_A"}, {"id": 20, "label": "TYPE_C"}],
            "edges": [],
        },
        "updates": [{"op": "+", "u": 10, "v": 20, "label": 0}],
    })

    # CASE_HAND_11: Int vs str labels
    workloads.append({
        "case_id": "CASE_HAND_11",
        "name": "int_vs_str_labels",
        "k": 0,
        "query": {
            "vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}],
            "edges": [{"u": 1, "v": 2, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": 10, "label": "0"}, {"id": 20, "label": "0"}],
            "edges": [],
        },
        "updates": [{"op": "+", "u": 10, "v": 20, "label": 0}],
    })

    # CASE_HAND_12: Sparse negative IDs
    workloads.append({
        "case_id": "CASE_HAND_12",
        "name": "sparse_negative_ids",
        "k": 0,
        "query": {
            "vertices": [{"id": -10, "label": 0}, {"id": 5, "label": 0}, {"id": 42, "label": 0}],
            "edges": [{"u": -10, "v": 5, "label": 0}, {"u": 5, "v": 42, "label": 0}, {"u": -10, "v": 42, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": -100, "label": 0}, {"id": 0, "label": 0}, {"id": 100, "label": 0}],
            "edges": [{"u": -100, "v": 0, "label": 0}, {"u": 0, "v": 100, "label": 0}],
        },
        "updates": [{"op": "+", "u": -100, "v": 100, "label": 0}],
    })

    # CASE_HAND_13: Canonical tuple order
    workloads.append({
        "case_id": "CASE_HAND_13",
        "name": "canonical_tuple_order",
        "k": 0,
        "query": {
            "vertices": [{"id": 3, "label": 30}, {"id": 1, "label": 10}, {"id": 4, "label": 40}, {"id": 2, "label": 20}],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0},
                {"u": 3, "v": 4, "label": 0}, {"u": 1, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": 10, "label": 10}, {"id": 20, "label": 20}, {"id": 30, "label": 30}, {"id": 40, "label": 40}],
            "edges": [{"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0}, {"u": 30, "v": 40, "label": 0}],
        },
        "updates": [{"op": "+", "u": 10, "v": 40, "label": 0}],
    })

    # CASE_HAND_14: Uniform K4 k=0 automorphisms (24 permutations)
    workloads.append({
        "case_id": "CASE_HAND_14",
        "name": "uniform_k4_k0_automorphisms",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": k4_q_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 3, "label": 0}, {"u": 1, "v": 4, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 1, "v": 2, "label": 0}],
    })

    # CASE_HAND_15: C4 k=1 deferred witness
    c4_edges = [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 3, "v": 4, "label": 0}, {"u": 1, "v": 4, "label": 0}]
    workloads.append({
        "case_id": "CASE_HAND_15",
        "name": "c4_k1_deferred_witness",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": c4_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
            "edges": [{"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0}],
        },
        "updates": [{"op": "+", "u": 30, "v": 40, "label": 0}],
    })

    # CASE_HAND_16: Missing commitment violation
    workloads.append({
        "case_id": "CASE_HAND_16",
        "name": "missing_commitment_violation",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 3, "label": 0}, {"u": 2, "v": 3, "label": 0},
                {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 1, "v": 2, "label": 0}],
    })

    # CASE_HAND_17: No double charging
    workloads.append({
        "case_id": "CASE_HAND_17",
        "name": "no_double_charging",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3)],
            "edges": [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 1, "v": 3, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30)],
            "edges": [{"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0}],
        },
        "updates": [{"op": "+", "u": 10, "v": 30, "label": 0}],
    })

    # CASE_HAND_18: Rollback isolation
    workloads.append({
        "case_id": "CASE_HAND_18",
        "name": "rollback_isolation",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": c4_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40, 50)],
            "edges": [
                {"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0},
                {"u": 30, "v": 40, "label": 0}, {"u": 10, "v": 30, "label": 0},
                {"u": 40, "v": 50, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 10, "v": 40, "label": 0}],
    })

    # CASE_HAND_19: Reversed endpoints
    workloads.append({
        "case_id": "CASE_HAND_19",
        "name": "reversed_endpoints",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 3, "label": 0}, {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [
                {"u": 1, "v": 2, "label": 0}, {"u": 1, "v": 3, "label": 0},
                {"u": 2, "v": 4, "label": 0}, {"u": 3, "v": 4, "label": 0}
            ],
        },
        "updates": [{"op": "+", "u": 3, "v": 2, "label": 0}],
    })

    # CASE_HAND_20: Churn cycle (Delete -> Insert -> Delete)
    workloads.append({
        "case_id": "CASE_HAND_20",
        "name": "caller_mutation_isolation",
        "k": 0,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3)],
            "edges": [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 1, "v": 3, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30)],
            "edges": [{"u": 10, "v": 20, "label": 0}, {"u": 20, "v": 30, "label": 0}, {"u": 10, "v": 30, "label": 0}],
        },
        "updates": [
            {"op": "-", "u": 10, "v": 30, "label": 0},
            {"op": "+", "u": 10, "v": 30, "label": 0},
            {"op": "-", "u": 10, "v": 30, "label": 0}
        ],
    })

    # CASE_HAND_21: Invalid k domain (k >= lambda, should be rejected)
    workloads.append({
        "case_id": "CASE_HAND_21",
        "name": "invalid_k_domain",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in (1, 2, 3, 4)],
            "edges": [{"u": 1, "v": 2, "label": 0}, {"u": 2, "v": 3, "label": 0}, {"u": 3, "v": 4, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in (10, 20, 30, 40)],
            "edges": [],
        },
        "updates": [{"op": "+", "u": 10, "v": 20, "label": 0}],
        "expected_error": True,
    })

    # CASE_HAND_22: Invalid update rollback (self-loop rejected)
    workloads.append({
        "case_id": "CASE_HAND_22",
        "name": "invalid_update_rollback",
        "k": 0,
        "query": {
            "vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}],
            "edges": [{"u": 1, "v": 2, "label": 0}],
        },
        "initial_graph": {
            "vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}],
            "edges": [],
        },
        "updates": [{"op": "+", "u": 1, "v": 1, "label": 0}],
        "expected_error": True,
    })

    # CASE_HAND_23: Query size 5 (K5 minus edge, lambda=3, k=1)
    k5_q_edges = [{"u": u, "v": v, "label": 0} for (u, v) in itertools.combinations(range(1, 6), 2) if (u, v) != (1, 2)]
    g23_edges = [{"u": u, "v": v, "label": 0} for (u, v) in itertools.combinations(range(1, 6), 2) if (u, v) not in ((1, 2), (2, 3))]
    workloads.append({
        "case_id": "CASE_HAND_23",
        "name": "query_size_5_k5_minus_edge",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in range(1, 6)],
            "edges": k5_q_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in range(1, 6)],
            "edges": g23_edges,
        },
        "updates": [{"op": "+", "u": 2, "v": 3, "label": 0}],
    })

    # CASE_HAND_24_Q6: Query size 6 (C6 cycle, lambda=2, k=1)
    c6_q_edges = [{"u": v, "v": v + 1, "label": 0} for v in range(5)] + [{"u": 0, "v": 5, "label": 0}]
    g24_edges = [{"u": v, "v": v + 1, "label": 0} for v in range(4)]
    workloads.append({
        "case_id": "CASE_HAND_24_Q6",
        "name": "c6_cycle_size_6",
        "k": 1,
        "query": {
            "vertices": [{"id": v, "label": 0} for v in range(6)],
            "edges": c6_q_edges,
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": 0} for v in range(6)],
            "edges": g24_edges,
        },
        "updates": [{"op": "+", "u": 4, "v": 5, "label": 0}],
    })

    return workloads


def run_hand_and_witness_cases(binary_path: Path) -> Tuple[List[Dict[str, Any]], bool]:
    """Executes all 24 hand cases on both M1 and M2, verifying against M0 Oracle."""
    workloads = build_hand_workloads()
    results = []
    all_ok = True

    for w in workloads:
        cid = w["case_id"]
        expected_err = w.get("expected_error", False)

        # Build M0 Oracle for expected events
        m0_entered_by_step: List[List[List[int]]] = []
        m0_exited_by_step: List[List[List[int]]] = []

        if not expected_err:
            Q_m0 = DStarGraph()
            for v_obj in w["query"]["vertices"]:
                Q_m0.add_vertex(v_obj["id"], v_obj["label"])
            for e_obj in w["query"]["edges"]:
                Q_m0.add_edge(e_obj["u"], e_obj["v"], e_obj["label"])

            G_m0 = DStarGraph()
            for v_obj in w["initial_graph"]["vertices"]:
                G_m0.add_vertex(v_obj["id"], v_obj["label"])
            for e_obj in w["initial_graph"]["edges"]:
                G_m0.add_edge(e_obj["u"], e_obj["v"], e_obj["label"])

            oracle = DStarDynamicOracle(Q_m0, G_m0, k=w["k"])

            for upd_obj in w["updates"]:
                op = "INSERT" if upd_obj["op"] in ("+", "INSERT", "INS") else "DELETE"
                delta = oracle.apply_update(DStarUpdate(op, upd_obj["u"], upd_obj["v"], upd_obj["label"]))
                m0_entered_by_step.append(sorted([list(t) for t in delta.entered]))
                m0_exited_by_step.append(sorted([list(t) for t in delta.exited]))

        # Run M1 and M2
        res_m1, dur_m1, ok_m1 = run_workload_on_binary(binary_path, "m1", w)
        res_m2, dur_m2, ok_m2 = run_workload_on_binary(binary_path, "m2", w)

        case_ok = True
        diag = ""

        if expected_err:
            # For invalid inputs (e.g. k >= lambda or self-loop), both must report error or fail
            m1_failed = (not ok_m1 or res_m1.get("status") in ("FAIL", "ERROR") or any(s.get("status") != "SUCCESS" for s in res_m1.get("steps", [])))
            m2_failed = (not ok_m2 or res_m2.get("status") in ("FAIL", "ERROR") or any(s.get("status") != "SUCCESS" for s in res_m2.get("steps", [])))
            case_ok = m1_failed and m2_failed
            if not case_ok:
                diag = f"Expected error not triggered: M1_failed={m1_failed}, M2_failed={m2_failed}"
        else:
            if not ok_m1 or not ok_m2:
                case_ok = False
                diag = f"Execution failed: M1_ok={ok_m1}, M2_ok={ok_m2}"
            else:
                steps_m1 = res_m1.get("steps", [])
                steps_m2 = res_m2.get("steps", [])
                if len(steps_m1) != len(m0_entered_by_step) or len(steps_m2) != len(m0_entered_by_step):
                    case_ok = False
                    diag = f"Steps count mismatch: M1={len(steps_m1)}, M2={len(steps_m2)}, M0={len(m0_entered_by_step)}"
                else:
                    for s_idx in range(len(m0_entered_by_step)):
                        ent_m1 = sorted(steps_m1[s_idx].get("entered_tuples", []))
                        ext_m1 = sorted(steps_m1[s_idx].get("exited_tuples", []))
                        ent_m2 = sorted(steps_m2[s_idx].get("entered_tuples", []))
                        ext_m2 = sorted(steps_m2[s_idx].get("exited_tuples", []))
                        ent_m0 = m0_entered_by_step[s_idx]
                        ext_m0 = m0_exited_by_step[s_idx]

                        if ent_m1 != ent_m0 or ent_m2 != ent_m0:
                            case_ok = False
                            diag = f"Step {s_idx} entered mismatch: M1={ent_m1}, M2={ent_m2}, M0={ent_m0}"
                            break
                        if ext_m1 != ext_m0 or ext_m2 != ext_m0:
                            case_ok = False
                            diag = f"Step {s_idx} exited mismatch: M1={ext_m1}, M2={ext_m2}, M0={ext_m0}"
                            break

                    # Check zero raw duplicate emissions in counters
                    cnt_m1 = res_m1.get("counters", {})
                    cnt_m2 = res_m2.get("counters", {})
                    if cnt_m1.get("discarded_emissions", 0) != 0 or cnt_m2.get("discarded_emissions", 0) != 0:
                        case_ok = False
                        diag = "Duplicate raw emissions detected in sink counters"

        if not case_ok:
            all_ok = False

        results.append({
            "case_id": cid,
            "name": w["name"],
            "status": "PASS" if case_ok else "FAIL",
            "diagnostic": diag,
            "duration_ms_m1": dur_m1,
            "duration_ms_m2": dur_m2,
        })

    return results, all_ok


# ============================================================================
# STAGE 3: Corpus 840 Reference Verification (F01 - Real M0 Recomputation)
# ============================================================================

def run_corpus_840(binary_path: Path) -> Tuple[Dict[str, Any], bool]:
    """Runs all 840 cases on M1 and M2, dynamically recomputing M0 Oracle."""
    corpus_dir = REPO_ROOT / "tests" / "fixtures" / "dstar02" / "corpus_840"
    files = [corpus_dir / "seed1_cases.jsonl", corpus_dir / "seed7_cases.jsonl"]

    stats = {
        "total_cases": 0,
        "total_truth_events": 0,
        "cases_with_events": 0,
        "cases_with_events_k_ge_1": 0,
        "m1_agreed": 0,
        "m2_agreed": 0,
        "m1_zero_duplicates": 0,
        "m2_zero_duplicates": 0,
        "status": "PASS",
        "error": None,
    }

    t0 = time.perf_counter()

    for fpath in files:
        if not fpath.is_file():
            stats["status"] = "FAIL"
            stats["error"] = f"Missing corpus file: {fpath}"
            return stats, False

        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                case = json.loads(line)
                stats["total_cases"] += 1

                k = int(case["config"].split("k=")[1].split()[0])

                workload = {
                    "case_id": f"corpus_s{case['seed']}_c{case['case']}",
                    "k": k,
                    "max_work": 500000,
                    "query": {
                        "vertices": [{"id": i, "label": lbl} for i, lbl in enumerate(case["query_vlabels"])],
                        "edges": [{"u": e[0], "v": e[1], "label": case["query_elabels"][i]} for i, e in enumerate(case["query_edges"])],
                    },
                    "initial_graph": {
                        "vertices": [{"id": i, "label": lbl} for i, lbl in enumerate(case["data_vlabels"])],
                        "edges": [{"u": e[0], "v": e[1], "label": e[2]} for e in case["G_before"]],
                    },
                    "updates": [
                        {
                            "op": "+" if case["update"]["kind"] in ("INS", "INSERT") else "-",
                            "u": case["update"]["edge"][0],
                            "v": case["update"]["edge"][1],
                            "label": case["update"]["label"],
                        }
                    ],
                }

                # Dynamically recompute M0 Ground Truth Oracle
                Q_m0 = DStarGraph()
                for i, lbl in enumerate(case["query_vlabels"]):
                    Q_m0.add_vertex(i, lbl)
                for i, e in enumerate(case["query_edges"]):
                    Q_m0.add_edge(e[0], e[1], case["query_elabels"][i])

                G_m0 = DStarGraph()
                for i, lbl in enumerate(case["data_vlabels"]):
                    G_m0.add_vertex(i, lbl)
                for e in case["G_before"]:
                    G_m0.add_edge(e[0], e[1], e[2])

                oracle = DStarDynamicOracle(Q_m0, G_m0, k=k)
                upd_op = "INSERT" if case["update"]["kind"] in ("INS", "INSERT") else "DELETE"
                delta = oracle.apply_update(DStarUpdate(upd_op, case["update"]["edge"][0], case["update"]["edge"][1], case["update"]["label"]))

                exp_ent = sorted([list(t) for t in delta.entered])
                exp_ext = sorted([list(t) for t in delta.exited])
                event_count = len(exp_ent) + len(exp_ext)
                stats["total_truth_events"] += event_count
                if event_count > 0:
                    stats["cases_with_events"] += 1
                    if k >= 1:
                        stats["cases_with_events_k_ge_1"] += 1

                # Execute Native M1
                res_m1, _, ok_m1 = run_workload_on_binary(binary_path, "m1", workload)
                if ok_m1:
                    s0 = res_m1["steps"][0]
                    if sorted(s0.get("entered_tuples", [])) == exp_ent and sorted(s0.get("exited_tuples", [])) == exp_ext:
                        stats["m1_agreed"] += 1
                    if res_m1.get("counters", {}).get("discarded_emissions", 0) == 0:
                        stats["m1_zero_duplicates"] += 1

                # Execute Native M2
                res_m2, _, ok_m2 = run_workload_on_binary(binary_path, "m2", workload)
                if ok_m2:
                    s0 = res_m2["steps"][0]
                    if sorted(s0.get("entered_tuples", [])) == exp_ent and sorted(s0.get("exited_tuples", [])) == exp_ext:
                        stats["m2_agreed"] += 1
                    if res_m2.get("counters", {}).get("discarded_emissions", 0) == 0:
                        stats["m2_zero_duplicates"] += 1

    stats["duration_seconds"] = time.perf_counter() - t0

    all_passed = (
        stats["total_cases"] == 840
        and stats["m1_agreed"] == 840
        and stats["m2_agreed"] == 840
        and stats["m1_zero_duplicates"] == 840
        and stats["m2_zero_duplicates"] == 840
        and stats["total_truth_events"] == 8002
        and stats["cases_with_events"] == 330
        and stats["cases_with_events_k_ge_1"] == 239
    )

    if not all_passed:
        stats["status"] = "FAIL"

    return stats, all_passed


# ============================================================================
# STAGE 4: 56-Stream Matrix Verification (672 updates)
# ============================================================================

def generate_stream_workload(seed: int, q_type: str, k: int, lambda_val: int, label_mode: str) -> Dict[str, Any]:
    """Generates a reproducible 12-update stream workload following DSTAR-02 generator protocol."""
    rng = random.Random(seed)
    n_vertices = 6
    n_vlabels = 1 if label_mode == "uniform" else 2
    n_elabels = 1 if label_mode == "uniform" else 2

    # Build query
    if q_type == "C4":
        q_v = 4
        q_edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    elif q_type == "Diamond":
        q_v = 4
        q_edges = [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]
    elif q_type == "K4":
        q_v = 4
        q_edges = list(itertools.combinations(range(4), 2))
    else:
        raise ValueError(f"Unknown query type: {q_type}")

    q_vlabels = [0] * q_v
    q_elabels = [0] * len(q_edges)

    # Initial data graph
    data_vlabels = [rng.randrange(n_vlabels) for _ in range(n_vertices)]
    all_pairs = list(itertools.combinations(range(n_vertices), 2))
    initial_pairs = rng.sample(all_pairs, 6)
    initial_edges = {pair: rng.randrange(n_elabels) for pair in initial_pairs}
    curr_edges = dict(initial_edges)

    updates: List[Dict[str, Any]] = []

    # Step 0 & 1: Explicit Inverse Pair
    avail_inv = [p for p in all_pairs if p not in curr_edges]
    if avail_inv:
        p_inv = rng.choice(avail_inv)
        lbl_inv = rng.randrange(n_elabels)
        updates.append({"op": "+", "u": p_inv[0], "v": p_inv[1], "label": lbl_inv})
        curr_edges[p_inv] = lbl_inv
        updates.append({"op": "-", "u": p_inv[0], "v": p_inv[1], "label": lbl_inv})
        del curr_edges[p_inv]
    else:
        p_inv = rng.choice(list(curr_edges.keys()))
        lbl_inv = curr_edges[p_inv]
        updates.append({"op": "-", "u": p_inv[0], "v": p_inv[1], "label": lbl_inv})
        del curr_edges[p_inv]
        updates.append({"op": "+", "u": p_inv[0], "v": p_inv[1], "label": lbl_inv})
        curr_edges[p_inv] = lbl_inv

    # Step 2, 3, 4: Explicit Churn on Same Edge Pair
    p_churn = rng.choice(list(curr_edges.keys()))
    lbl_churn = curr_edges[p_churn]
    updates.append({"op": "-", "u": p_churn[0], "v": p_churn[1], "label": lbl_churn})
    del curr_edges[p_churn]
    updates.append({"op": "+", "u": p_churn[0], "v": p_churn[1], "label": lbl_churn})
    curr_edges[p_churn] = lbl_churn
    updates.append({"op": "-", "u": p_churn[0], "v": p_churn[1], "label": lbl_churn})
    del curr_edges[p_churn]

    # Steps 5 to 11: 7 randomized valid updates
    for _ in range(7):
        can_insert = len(curr_edges) < len(all_pairs)
        can_delete = len(curr_edges) > 1
        op = "+" if (can_insert and (not can_delete or rng.random() < 0.5)) else "-"
        if op == "+":
            avail = [p for p in all_pairs if p not in curr_edges]
            pair = rng.choice(avail)
            lbl = rng.randrange(n_elabels)
            updates.append({"op": "+", "u": pair[0], "v": pair[1], "label": lbl})
            curr_edges[pair] = lbl
        else:
            pair = rng.choice(list(curr_edges.keys()))
            lbl = curr_edges[pair]
            updates.append({"op": "-", "u": pair[0], "v": pair[1], "label": lbl})
            del curr_edges[pair]

    assert len(updates) == 12

    return {
        "case_id": f"stream_{q_type}_k{k}_{seed}_{label_mode}",
        "k": k,
        "max_work": 500000,
        "query": {
            "vertices": [{"id": v, "label": q_vlabels[v]} for v in range(q_v)],
            "edges": [{"u": e[0], "v": e[1], "label": q_elabels[idx]} for idx, e in enumerate(q_edges)],
        },
        "initial_graph": {
            "vertices": [{"id": v, "label": data_vlabels[v]} for v in range(n_vertices)],
            "edges": [{"u": p[0], "v": p[1], "label": lbl} for p, lbl in initial_edges.items()],
        },
        "updates": updates,
    }


def run_56_stream_matrix(binary_path: Path) -> Tuple[Dict[str, Any], bool]:
    """Runs the 56-stream matrix (672 updates) on M1 and M2, verifying active set reconstruction."""
    matrix_seeds = [20261118, 20261119, 20261120, 20261121, 20261122, 20261123, 20261124, 20261125]
    matrix_specs = [
        ("C4", 0, 2),
        ("C4", 1, 2),
        ("Diamond", 0, 2),
        ("Diamond", 1, 2),
        ("K4", 0, 3),
        ("K4", 1, 3),
        ("K4", 2, 3),
    ]

    stats = {
        "total_configurations": 56,
        "total_updates": 672,
        "agreed_steps_m1": 0,
        "agreed_steps_m2": 0,
        "reconstruction_agreed_steps_m1": 0,
        "reconstruction_agreed_steps_m2": 0,
        "total_entered_events": 0,
        "total_exited_events": 0,
        "status": "PASS",
        "error": None,
    }

    t0 = time.perf_counter()

    for seed_idx, seed in enumerate(matrix_seeds):
        label_mode = "uniform" if seed_idx < 4 else "mixed"
        for (q_type, k, lambda_val) in matrix_specs:
            workload = generate_stream_workload(seed, q_type, k, lambda_val, label_mode)

            # Build M0 Oracle
            Q_m0 = DStarGraph()
            for v_obj in workload["query"]["vertices"]:
                Q_m0.add_vertex(v_obj["id"], v_obj["label"])
            for e_obj in workload["query"]["edges"]:
                Q_m0.add_edge(e_obj["u"], e_obj["v"], e_obj["label"])

            G_m0 = DStarGraph()
            for v_obj in workload["initial_graph"]["vertices"]:
                G_m0.add_vertex(v_obj["id"], v_obj["label"])
            for e_obj in workload["initial_graph"]["edges"]:
                G_m0.add_edge(e_obj["u"], e_obj["v"], e_obj["label"])

            oracle = DStarDynamicOracle(Q_m0, G_m0, k=k)

            # Precompute M0 sequence
            m0_deltas = []
            m0_active_sets = []
            for upd in workload["updates"]:
                op = "INSERT" if upd["op"] == "+" else "DELETE"
                delta = oracle.apply_update(DStarUpdate(op, upd["u"], upd["v"], upd["label"]))
                m0_deltas.append((
                    sorted([list(t) for t in delta.entered]),
                    sorted([list(t) for t in delta.exited]),
                ))
                m0_active_sets.append(copy.deepcopy(oracle.current_matches))
                stats["total_entered_events"] += len(delta.entered)
                stats["total_exited_events"] += len(delta.exited)

            # Execute M1 and M2 on native
            res_m1, _, ok_m1 = run_workload_on_binary(binary_path, "m1", workload)
            res_m2, _, ok_m2 = run_workload_on_binary(binary_path, "m2", workload)

            if not ok_m1 or not ok_m2:
                stats["status"] = "FAIL"
                stats["error"] = f"Native run failed for {workload['case_id']}"
                return stats, False

            steps_m1 = res_m1.get("steps", [])
            steps_m2 = res_m2.get("steps", [])

            # Step-by-step verification and active set reconstruction
            # Initial active set for reconstruction
            active_m1: Set[Tuple[int, ...]] = set(enumerate_snapshot(Q_m0, G_m0, k))
            active_m2: Set[Tuple[int, ...]] = set(enumerate_snapshot(Q_m0, G_m0, k))

            for s_idx in range(12):
                exp_ent, exp_ext = m0_deltas[s_idx]
                target_m0_active = m0_active_sets[s_idx]

                ent_m1 = sorted(steps_m1[s_idx].get("entered_tuples", []))
                ext_m1 = sorted(steps_m1[s_idx].get("exited_tuples", []))
                if ent_m1 == exp_ent and ext_m1 == exp_ext:
                    stats["agreed_steps_m1"] += 1

                ent_m2 = sorted(steps_m2[s_idx].get("entered_tuples", []))
                ext_m2 = sorted(steps_m2[s_idx].get("exited_tuples", []))
                if ent_m2 == exp_ent and ext_m2 == exp_ext:
                    stats["agreed_steps_m2"] += 1

                # Reconstruct active set M1
                for tup in ext_m1:
                    active_m1.discard(tuple(tup))
                for tup in ent_m1:
                    active_m1.add(tuple(tup))
                if active_m1 == target_m0_active:
                    stats["reconstruction_agreed_steps_m1"] += 1

                # Reconstruct active set M2
                for tup in ext_m2:
                    active_m2.discard(tuple(tup))
                for tup in ent_m2:
                    active_m2.add(tuple(tup))
                if active_m2 == target_m0_active:
                    stats["reconstruction_agreed_steps_m2"] += 1

    stats["duration_seconds"] = time.perf_counter() - t0

    all_passed = (
        stats["agreed_steps_m1"] == 672
        and stats["agreed_steps_m2"] == 672
        and stats["reconstruction_agreed_steps_m1"] == 672
        and stats["reconstruction_agreed_steps_m2"] == 672
    )

    if not all_passed:
        stats["status"] = "FAIL"

    return stats, all_passed


# ============================================================================
# MAIN RUNNER ORCHESTRATION
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="RapidFlow-APX Native Engines Test Runner (Phase P3)")
    parser.add_argument("--clean-build", action="store_true", help="Force clean rebuild of native binary")
    parser.add_argument("--engine", choices=["m1", "m2", "all"], default="all", help="Target engine")
    parser.add_argument("--stage", choices=["provenance", "hand", "corpus", "stream", "all"], default="all", help="Target execution stage")
    parser.add_argument("--case", type=str, default=None, help="Diagnostic single case ID to run")
    parser.add_argument("--output", type=str, default=None, help="Output summary report path")
    args = parser.parse_args()

    is_diagnostic = (args.case is not None or args.stage != "all")
    output_path = Path(args.output) if args.output else (DIAGNOSTIC_SUMMARY_PATH if is_diagnostic else CANONICAL_SUMMARY_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("================================================================================")
    print("      RapidFlow-APX Native Engines Verification Runner (Phase P3 / D-INT-03)     ")
    print("================================================================================")

    # 1. Provenance check
    print("[1/5] Verifying fail-closed Git provenance...")
    provenance, prov_ok = check_git_provenance()
    print(f"  Repo Commit:     {provenance.get('repo_commit')} (Dirty: {provenance.get('dirty_state')})")
    print(f"  Upstream Commit: {provenance.get('upstream_commit')} (Pristine: {provenance.get('upstream_pristine')})")
    if not prov_ok:
        print("ERROR: Fail-closed Git provenance check failed!")
        sys.exit(1)

    # 2. Host resources check
    print("[2/5] Checking host system resources...")
    res_info, res_ok = check_system_resources()
    print(f"  Available RAM:   {res_info.get('mem_available_gib'):.2f} GiB | Free Disk: {res_info.get('free_disk_gib'):.2f} GiB")
    if not res_ok:
        print("ERROR: Host resources insufficient (< 2 GiB RAM or < 1 GiB disk)!")
        sys.exit(1)

    # 3. Build native binary
    print("[3/5] Building native test harness with resource limits...")
    bin_path, bin_sha, build_info, build_ok = build_native_binary(clean_build=args.clean_build)
    print(f"  Harness Binary:  {bin_path}")
    print(f"  Binary SHA-256:  {bin_sha}")
    if not build_ok:
        print("ERROR: Native compilation or linking failed!")
        sys.exit(1)

    # Load Manifest
    with open(CANONICAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest_sha = compute_sha256(CANONICAL_MANIFEST_PATH)

    summary_data: Dict[str, Any] = {
        "runner_mode": "DIAGNOSTIC_SUBSET_RUN" if is_diagnostic else "CANONICAL_FULL_RUN",
        "overall_status": "PENDING",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(CANONICAL_MANIFEST_PATH.relative_to(REPO_ROOT)),
        "manifest_sha256": manifest_sha,
        "binary_path": str(bin_path.relative_to(REPO_ROOT)),
        "binary_sha256": bin_sha,
        "provenance": provenance,
        "system_resources": res_info,
        "build_provenance": build_info,
        "stages": {},
    }

    hand_ok = True
    corpus_ok = True
    stream_ok = True

    # Execute Hand Cases
    if args.stage in ("all", "hand"):
        print("[4/5] Executing 24 Hand and Adversarial Witness Cases...")
        hand_results, hand_ok = run_hand_and_witness_cases(bin_path)
        hand_passed = sum(1 for c in hand_results if c["status"] == "PASS")
        print(f"  Hand cases: {hand_passed}/{len(hand_results)} passed ({'PASS' if hand_ok else 'FAIL'})")
        summary_data["stages"]["hand_cases"] = {
            "status": "PASS" if hand_ok else "FAIL",
            "total_cases": len(hand_results),
            "passed_cases": hand_passed,
            "cases": hand_results,
        }
        if not hand_ok and not is_diagnostic:
            summary_data["overall_status"] = "FAIL"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2)
            sys.exit(1)

    # Execute Corpus 840
    if args.stage in ("all", "corpus"):
        print("[5/5] Executing 840-Case Correctness Corpus (Real M0 Recomputed)...")
        corpus_stats, corpus_ok = run_corpus_840(bin_path)
        print(f"  Corpus 840: {corpus_stats['m1_agreed']}/840 M1 agreed, {corpus_stats['m2_agreed']}/840 M2 agreed ({'PASS' if corpus_ok else 'FAIL'})")
        print(f"  Truth Events: {corpus_stats['total_truth_events']} total ({corpus_stats['cases_with_events']} cases with events, {corpus_stats['cases_with_events_k_ge_1']} k>=1)")
        summary_data["stages"]["corpus_840"] = corpus_stats
        if not corpus_ok and not is_diagnostic:
            summary_data["overall_status"] = "FAIL"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2)
            sys.exit(1)

    # Execute 56-Stream Matrix
    if args.stage in ("all", "stream"):
        print("[+] Executing 56-Stream Stateful Matrix (672 updates)...")
        stream_stats, stream_ok = run_56_stream_matrix(bin_path)
        print(f"  Stream 56: M1 agreed {stream_stats['agreed_steps_m1']}/672, M2 agreed {stream_stats['agreed_steps_m2']}/672 ({'PASS' if stream_ok else 'FAIL'})")
        print(f"  Active Set Reconstruction: M1 {stream_stats['reconstruction_agreed_steps_m1']}/672, M2 {stream_stats['reconstruction_agreed_steps_m2']}/672")
        summary_data["stages"]["stream_56_matrix"] = stream_stats
        if not stream_ok and not is_diagnostic:
            summary_data["overall_status"] = "FAIL"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2)
            sys.exit(1)

    all_stages_passed = hand_ok and corpus_ok and stream_ok
    if is_diagnostic:
        summary_data["overall_status"] = "DIAGNOSTIC_SUBSET_PASS" if all_stages_passed else "DIAGNOSTIC_SUBSET_FAIL"
    else:
        summary_data["overall_status"] = "P3_ENGINES_VERIFIED" if all_stages_passed else "FAIL"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    print("================================================================================")
    print(f"  Final Verification Status: {summary_data['overall_status']}")
    print(f"  Canonical Summary Report:  {output_path}")
    print("================================================================================")

    if not all_stages_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
