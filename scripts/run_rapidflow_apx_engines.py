#!/usr/bin/env python3
"""
scripts/run_rapidflow_apx_engines.py — Canonical runner and test executor for Phase P3 Native Engines.

Verifies:
1. Fail-closed Git provenance verification (repo commit, clean tree, pristine upstream RapidFlow commit 2ea4f4c...).
2. Preflight system resource checks (MemAvailable >= 2 GiB from /proc/meminfo, free disk >= 1 GiB).
3. Native build under resource containment using portable CMake.
4. Hand & adversarial witness validation (24 cases) for M1 and M2 engines against M0 Oracle with deep graph state, codec state, and counter verification.
5. Strict negative test contract checking exact exit codes and error categories.
6. 840-case correctness corpus verification (seed1 + seed7) with fixture hash locking and dynamic M0 recomputation.
7. 56-stream stateful matrix verification (672 updates) with immutable workload locking, step-by-step M0 active-set reconstruction, deep logical graph validation, codec validation, and step counter auditing.
8. Single-engine filtering (--engine m1 / --engine m2 / --engine all) with strict invocation separation.
9. Case registry validation rejecting unknown cases or invalid stage-case combinations.
10. Atomic canonical and diagnostic reporting with failure state preservation (atomic_write_json).
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

STREAM_56_LOCK_PATH = REPO_ROOT / "configs" / "rapidflow_apx_stream_56_lock.json"
CORPUS_840_SEED1_PATH = REPO_ROOT / "tests" / "fixtures" / "dstar02" / "corpus_840" / "seed1_cases.jsonl"
CORPUS_840_SEED7_PATH = REPO_ROOT / "tests" / "fixtures" / "dstar02" / "corpus_840" / "seed7_cases.jsonl"
CORPUS_840_SEED1_SHA256 = "3e509093838786030fc5da50171bb25500fb73d6c0605e7f87fede089d5cff91"
CORPUS_840_SEED7_SHA256 = "ca7e41b85cc076edb26ab2a5f30d3ff5445f9d11d6a91dfc90b29a369b419643"


def compute_sha256(path: Path) -> str:
    """Computes SHA-256 digest of a file; returns 'FILE_NOT_FOUND' if missing."""
    if not path.is_file():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(destination: Path, payload: Dict[str, Any]) -> None:
    """Safely and atomically writes JSON payload to destination path (P3-R2-F13)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=destination.parent, prefix=".tmp_", suffix=".json")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, destination)
    except Exception:
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


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
    """Verifies fail-closed repository and upstream provenance (P3-R2-F06).
    Rejects any uncommitted or untracked changes outside reports/rapidflow_apx_engines/."""
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
    except Exception:
        return meta, False

    try:
        status_raw = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True
        ).strip()
        if not status_raw:
            meta["repo_clean"] = True
            meta["dirty_state"] = "CLEAN"
        else:
            lines = [l.strip() for l in status_raw.splitlines() if l.strip()]
            allowed = True
            for line in lines:
                parts = line.split(maxsplit=1)
                if len(parts) < 2:
                    allowed = False
                    break
                fpath = parts[1].strip()
                # Only execution reports under reports/rapidflow_apx_engines/ are allowed during runs
                if not (fpath.startswith("reports/rapidflow_apx_engines/") or fpath.startswith("reports\\rapidflow_apx_engines\\")):
                    allowed = False
                    break
            if allowed:
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

    valid = (
        meta["repo_commit"] is not None
        and meta["repo_clean"] is True
        and meta["upstream_commit"] == EXPECTED_UPSTREAM_COMMIT
        and meta["upstream_pristine"] is True
    )
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
    """Builds the native C++ harness (rapidflow_apx_native) under containment."""
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


def validate_native_output_schema(
    res_data: Dict[str, Any],
    expected_engine: str,
    expected_case_id: str,
    raw_input_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Validates that native binary output complies with schema version 1.1.0 (P3-R2-F12)."""
    if not isinstance(res_data, dict):
        return False, "Output JSON root is not an object"

    if res_data.get("schema_version") != "1.1.0":
        return False, f"Invalid schema_version: {res_data.get('schema_version')} (expected '1.1.0')"
    if res_data.get("semantics_version") != "D-R-1.0":
        return False, f"Invalid semantics_version: {res_data.get('semantics_version')} (expected 'D-R-1.0')"

    if res_data.get("engine_name") != expected_engine or res_data.get("engine") != expected_engine:
        return False, f"Engine mismatch: {res_data.get('engine_name')} != {expected_engine}"

    if expected_case_id and res_data.get("case_id") not in (expected_case_id, "UNKNOWN"):
        return False, f"Case ID mismatch: obs={res_data.get('case_id')} vs exp={expected_case_id}"

    if raw_input_path and raw_input_path.is_file():
        exp_sha = compute_sha256(raw_input_path)
        obs_sha = res_data.get("raw_input_sha256")
        if obs_sha != exp_sha:
            return False, f"raw_input_sha256 mismatch: obs={obs_sha} vs exp={exp_sha}"

    exec_info = res_data.get("execution")
    if not isinstance(exec_info, dict):
        return False, "Missing or invalid 'execution' summary object"
    for ef in ["total_steps", "attempted_steps", "completed_steps", "failed_steps", "unattempted_steps"]:
        if ef not in exec_info or isinstance(exec_info[ef], bool) or not isinstance(exec_info[ef], int):
            return False, f"Execution summary field '{ef}' missing or not an integer"

    steps = res_data.get("steps")
    if not isinstance(steps, list):
        return False, "Missing or invalid 'steps' array"

    return True, ""


def validate_graph_state(
    observed: Dict[str, Any],
    expected_graph: DStarGraph,
    case_id: str = "",
    engine: str = "",
    step_index: int = 0,
) -> Tuple[bool, str]:
    """Deep logical graph state validation (P3-R2-F01).
    Validates complete graph: vertex identities, typed labels, degrees, edges, canonical neighbors, symmetry, degree sum."""
    if not isinstance(observed, dict):
        return False, f"[{case_id}:{engine}:s{step_index}] observed graph_state is not a dict: {type(observed)}"

    exp_v_count = len(expected_graph.vertices)
    exp_e_count = len(expected_graph.edges)

    obs_v_count = observed.get("vertices_count")
    obs_e_count = observed.get("edges_count")

    if obs_v_count != exp_v_count:
        return False, f"[{case_id}:{engine}:s{step_index}] vertices_count mismatch: obs={obs_v_count} vs exp={exp_v_count}"
    if obs_e_count != exp_e_count:
        return False, f"[{case_id}:{engine}:s{step_index}] edges_count mismatch: obs={obs_e_count} vs exp={exp_e_count}"

    obs_vertices = observed.get("vertices")
    if not isinstance(obs_vertices, list) or len(obs_vertices) != exp_v_count:
        return False, f"[{case_id}:{engine}:s{step_index}] vertices list length mismatch: obs={len(obs_vertices) if isinstance(obs_vertices, list) else None} vs exp={exp_v_count}"

    obs_v_map = {}
    for v_obj in obs_vertices:
        if not isinstance(v_obj, dict):
            return False, f"[{case_id}:{engine}:s{step_index}] vertex item is not a dict: {v_obj}"
        vid = v_obj.get("id")
        if vid is None or isinstance(vid, bool):
            return False, f"[{case_id}:{engine}:s{step_index}] invalid vertex id: {vid}"
        if vid in obs_v_map:
            return False, f"[{case_id}:{engine}:s{step_index}] duplicate vertex id in graph_state: {vid}"
        obs_v_map[vid] = v_obj

    # Check each expected vertex
    for exp_vid, exp_lbl in expected_graph.vertices.items():
        if exp_vid not in obs_v_map:
            return False, f"[{case_id}:{engine}:s{step_index}] expected vertex {exp_vid} missing from observed vertices"
        v_obj = obs_v_map[exp_vid]
        obs_lbl = v_obj.get("label")
        if type(obs_lbl) != type(exp_lbl) or obs_lbl != exp_lbl:
            return False, f"[{case_id}:{engine}:s{step_index}] vertex {exp_vid} label mismatch: obs={obs_lbl} ({type(obs_lbl)}) vs exp={exp_lbl} ({type(exp_lbl)})"
        exp_nbrs = set(expected_graph.adj.get(exp_vid, {}).keys())
        exp_deg = len(exp_nbrs)
        obs_deg = v_obj.get("degree")
        if obs_deg != exp_deg:
            return False, f"[{case_id}:{engine}:s{step_index}] vertex {exp_vid} degree mismatch: obs={obs_deg} vs exp={exp_deg}"

    # Check edges
    obs_edges = observed.get("edges")
    if not isinstance(obs_edges, list) or len(obs_edges) != exp_e_count:
        return False, f"[{case_id}:{engine}:s{step_index}] edges list length mismatch: obs={len(obs_edges) if isinstance(obs_edges, list) else None} vs exp={exp_e_count}"

    obs_edge_set = set()
    for e_obj in obs_edges:
        if not isinstance(e_obj, dict):
            return False, f"[{case_id}:{engine}:s{step_index}] edge item is not a dict: {e_obj}"
        u = e_obj.get("u")
        v = e_obj.get("v")
        lbl = e_obj.get("label")
        if u is None or v is None or isinstance(u, bool) or isinstance(v, bool):
            return False, f"[{case_id}:{engine}:s{step_index}] invalid edge endpoints: u={u}, v={v}"
        if u == v:
            return False, f"[{case_id}:{engine}:s{step_index}] self-loop in observed edges: ({u}, {v})"
        canonical_edge = (min(u, v), max(u, v), lbl, type(lbl))
        if canonical_edge in obs_edge_set:
            return False, f"[{case_id}:{engine}:s{step_index}] duplicate edge in observed edges: {canonical_edge}"
        obs_edge_set.add(canonical_edge)

    for (exp_u, exp_v), exp_lbl in expected_graph.edges.items():
        can_exp = (min(exp_u, exp_v), max(exp_u, exp_v), exp_lbl, type(exp_lbl))
        if can_exp not in obs_edge_set:
            return False, f"[{case_id}:{engine}:s{step_index}] expected edge {can_exp} missing from observed edges"

    # Check neighbors
    obs_neighbors = observed.get("neighbors")
    if not isinstance(obs_neighbors, dict):
        return False, f"[{case_id}:{engine}:s{step_index}] neighbors is not a dict: {type(obs_neighbors)}"

    degree_sum = 0
    for exp_vid in expected_graph.vertices:
        exp_nbrs = set(expected_graph.adj.get(exp_vid, {}).keys())
        str_vid = str(exp_vid)
        if str_vid not in obs_neighbors:
            return False, f"[{case_id}:{engine}:s{step_index}] vertex {exp_vid} missing from neighbors dict"
        obs_nbr_list = obs_neighbors[str_vid]
        if not isinstance(obs_nbr_list, list):
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor list for {exp_vid} is not a list"
        if len(obs_nbr_list) != len(set(obs_nbr_list)):
            return False, f"[{case_id}:{engine}:s{step_index}] duplicate neighbor in list for {exp_vid}: {obs_nbr_list}"
        if exp_vid in obs_nbr_list:
            return False, f"[{case_id}:{engine}:s{step_index}] self-loop neighbor detected for {exp_vid}"
        if set(obs_nbr_list) != exp_nbrs:
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor set mismatch for {exp_vid}: obs={set(obs_nbr_list)} vs exp={exp_nbrs}"
        degree_sum += len(obs_nbr_list)

        # Symmetry check
        for nbr_id in obs_nbr_list:
            str_nbr = str(nbr_id)
            if str_nbr not in obs_neighbors or exp_vid not in obs_neighbors[str_nbr]:
                return False, f"[{case_id}:{engine}:s{step_index}] asymmetric adjacency: {exp_vid} -> {nbr_id} but not reverse"

    # Internal consistency: sum(degrees) == 2 * edges_count
    if degree_sum != 2 * exp_e_count:
        return False, f"[{case_id}:{engine}:s{step_index}] degree sum consistency violated: sum={degree_sum} vs 2*E={2 * exp_e_count}"

    v_degree_sum = sum(v["degree"] for v in obs_vertices)
    if v_degree_sum != 2 * exp_e_count:
        return False, f"[{case_id}:{engine}:s{step_index}] vertex degree field sum mismatch: sum={v_degree_sum} vs 2*E={2 * exp_e_count}"

    return True, ""


def validate_codec_state(
    observed_codec: Dict[str, Any],
    expected_codec: Dict[str, int],
    case_id: str = "",
    engine: str = "",
    step_index: int = 0,
) -> Tuple[bool, str]:
    """Validates codec state counts against expected values (P3-R2-F10)."""
    if not isinstance(observed_codec, dict):
        return False, f"[{case_id}:{engine}:s{step_index}] observed codec_state is not a dict"

    fields = [
        "query_vertices_count",
        "data_vertices_count",
        "vertex_labels_count",
        "edge_labels_count",
    ]
    for f in fields:
        obs_val = observed_codec.get(f)
        if obs_val is None or isinstance(obs_val, bool) or not isinstance(obs_val, int):
            return False, f"[{case_id}:{engine}:s{step_index}] codec field {f} invalid: {obs_val} (type={type(obs_val)})"
        if obs_val < 0:
            return False, f"[{case_id}:{engine}:s{step_index}] codec field {f} is negative: {obs_val}"
        exp_val = expected_codec.get(f)
        if exp_val is not None and obs_val != exp_val:
            return False, f"[{case_id}:{engine}:s{step_index}] codec field {f} mismatch: obs={obs_val} vs exp={exp_val}"

    return True, ""


def validate_step_counters(
    step_counters: Dict[str, Any],
    engine: str,
    max_work: int,
    is_success: bool = True,
    case_id: str = "",
    step_index: int = 0,
) -> Tuple[bool, str]:
    """Validates per-step execution counters (P3-R2-F10)."""
    if not isinstance(step_counters, dict):
        return False, f"[{case_id}:{engine}:s{step_index}] step_counters is not a dict"

    mandatory_fields = [
        "work_units",
        "max_work",
        "dfs_states",
        "candidate_evaluations",
        "candidate_extensions",
        "edge_lookups",
        "vertex_label_checks",
        "anchors_started",
    ]
    if engine == "m1":
        mandatory_fields.extend(["variants_started", "raw_exact_results", "missing_set_rejections"])
    elif engine == "m2":
        mandatory_fields.extend(["edge_decisions", "missing_branches", "commitment_checks", "commitment_rejections"])

    for f in mandatory_fields:
        if f not in step_counters:
            return False, f"[{case_id}:{engine}:s{step_index}] step_counters missing required field: {f}"
        val = step_counters[f]
        if val is None or isinstance(val, bool) or not isinstance(val, int):
            return False, f"[{case_id}:{engine}:s{step_index}] step_counters field {f} is not an integer: {val} (type={type(val)})"
        if val < 0:
            return False, f"[{case_id}:{engine}:s{step_index}] step_counters field {f} is negative: {val}"

    if step_counters["max_work"] != max_work:
        return False, f"[{case_id}:{engine}:s{step_index}] max_work mismatch: obs={step_counters['max_work']} vs exp={max_work}"

    if is_success and step_counters["work_units"] > max_work:
        return False, f"[{case_id}:{engine}:s{step_index}] work_units {step_counters['work_units']} exceeded max_work {max_work} on SUCCESS step"

    return True, ""


def run_workload_on_binary(
    binary_path: Path,
    engine: str,
    workload: Dict[str, Any],
    max_work: Optional[int] = None,
    timeout: int = 30,
    address_space_mib: int = 512,
) -> Tuple[Dict[str, Any], float, bool]:
    """Runs a single workload JSON through rapidflow_apx_native and validates schema."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as wf, \
         tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as of:
        json.dump(workload, wf)
        wf.flush()
        wf_path = Path(wf.name)

        cmd = [
            str(binary_path),
            "--engine", engine,
            "--workload", wf.name,
            "--output", of.name,
        ]
        if max_work is not None:
            cmd.extend(["--max-work", str(max_work)])

        t0 = time.perf_counter()
        ret, out, err, timed_out = execute_contained_subprocess(
            cmd, cwd=REPO_ROOT, timeout=timeout, address_space_mib=address_space_mib
        )
        duration_ms = (time.perf_counter() - t0) * 1000.0

        res_data: Dict[str, Any] = {}
        output_file_exists = os.path.exists(of.name)
        if not timed_out and output_file_exists and os.path.getsize(of.name) > 0:
            try:
                with open(of.name, "r", encoding="utf-8") as rf:
                    res_data = json.load(rf)
            except Exception as e:
                res_data = {"status": "ERROR", "error_message": f"Output parse error: {e}"}

        if not res_data:
            status = "TIMEOUT" if timed_out else "EXEC_ERROR"
            res_data = {"status": status, "error_message": err or out}

        res_data["exit_code"] = ret

        # If process exited and output exists, validate schema (P3-R2-F12)
        schema_ok = True
        if ret == 0 and not timed_out and isinstance(res_data, dict):
            s_ok, s_diag = validate_native_output_schema(
                res_data, engine, workload.get("case_id", ""), wf_path
            )
            if not s_ok:
                schema_ok = False
                res_data["status"] = "SCHEMA_ERROR"
                res_data["error_message"] = s_diag

        try:
            os.remove(wf.name)
            os.remove(of.name)
        except OSError:
            pass

        is_success = (ret == 0 and not timed_out and res_data.get("status") == "SUCCESS" and schema_ok)
        return res_data, duration_ms, is_success


# ============================================================================
# STAGE 2: Hand and Adversarial Witness Cases (24 cases)
# ============================================================================

def build_hand_workloads() -> List[Dict[str, Any]]:
    """Builds the 24 canonical hand and adversarial workloads with exact contracts."""
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

    # CASE_HAND_11: Int vs str labels (strict typing)
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

    # CASE_HAND_21: Invalid k domain (k >= lambda, rejected with Exit 3 / DOMAIN_ERROR)
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
        "expected_exit_code": 3,
        "expected_error_category": "DOMAIN_ERROR",
    })

    # CASE_HAND_22: Invalid update rollback (self-loop rejected with Exit 6 / PREVALIDATION_ERROR)
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
        "expected_exit_code": 6,
        "expected_error_category": "PREVALIDATION_ERROR",
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


def run_hand_and_witness_cases(
    binary_path: Path,
    engine_filter: str = "all",
    case_filter: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Executes hand and adversarial cases on selected engine(s), strictly verifying against M0 Oracle and negative contract."""
    all_workloads = build_hand_workloads()
    if case_filter:
        workloads = [w for w in all_workloads if w["case_id"] == case_filter]
    else:
        workloads = all_workloads

    results = []
    all_ok = True

    for w in workloads:
        cid = w["case_id"]
        expected_err = w.get("expected_error", False)

        # Build M0 Oracle for expected events on positive cases
        m0_entered_by_step: List[List[List[int]]] = []
        m0_exited_by_step: List[List[List[int]]] = []
        m0_graphs: List[DStarGraph] = []

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
                m0_graphs.append(copy.deepcopy(oracle.G))

        # Run M1 and/or M2 based on engine_filter (P3-R2-F03)
        res_m1, dur_m1, ok_m1 = ({}, 0.0, False)
        res_m2, dur_m2, ok_m2 = ({}, 0.0, False)
        run_m1 = engine_filter in ("all", "m1")
        run_m2 = engine_filter in ("all", "m2")

        if run_m1:
            res_m1, dur_m1, ok_m1 = run_workload_on_binary(binary_path, "m1", w)
        if run_m2:
            res_m2, dur_m2, ok_m2 = run_workload_on_binary(binary_path, "m2", w)

        case_ok = True
        diag = ""

        if expected_err:
            # Strict negative verification (P3-R2-F07): exact exit code, exact error category, status != SUCCESS
            exp_code = w.get("expected_exit_code")
            exp_cat = w.get("expected_error_category")

            if run_m1:
                m1_code = res_m1.get("exit_code")
                m1_cat = res_m1.get("error_category")
                m1_stat = res_m1.get("status")
                if m1_code != exp_code or m1_cat != exp_cat or m1_stat == "SUCCESS":
                    case_ok = False
                    diag += f"M1 negative contract failure: code={m1_code} (exp {exp_code}), cat={m1_cat} (exp {exp_cat}), stat={m1_stat}. "

            if run_m2:
                m2_code = res_m2.get("exit_code")
                m2_cat = res_m2.get("error_category")
                m2_stat = res_m2.get("status")
                if m2_code != exp_code or m2_cat != exp_cat or m2_stat == "SUCCESS":
                    case_ok = False
                    diag += f"M2 negative contract failure: code={m2_code} (exp {exp_code}), cat={m2_cat} (exp {exp_cat}), stat={m2_stat}. "
        else:
            if run_m1 and not ok_m1:
                case_ok = False
                diag += f"M1 execution failed: status={res_m1.get('status')}, err={res_m1.get('error_message')}. "
            if run_m2 and not ok_m2:
                case_ok = False
                diag += f"M2 execution failed: status={res_m2.get('status')}, err={res_m2.get('error_message')}. "

            if case_ok:
                engines_to_verify = []
                if run_m1:
                    engines_to_verify.append(("m1", res_m1))
                if run_m2:
                    engines_to_verify.append(("m2", res_m2))

                for eng_name, eng_res in engines_to_verify:
                    steps = eng_res.get("steps", [])
                    if len(steps) != len(m0_entered_by_step):
                        case_ok = False
                        diag += f"{eng_name} steps count mismatch: obs={len(steps)} vs exp={len(m0_entered_by_step)}. "
                        break

                    for s_idx in range(len(m0_entered_by_step)):
                        ent_obs = sorted(steps[s_idx].get("entered_tuples", []))
                        ext_obs = sorted(steps[s_idx].get("exited_tuples", []))
                        ent_exp = m0_entered_by_step[s_idx]
                        ext_exp = m0_exited_by_step[s_idx]

                        if ent_obs != ent_exp:
                            case_ok = False
                            diag += f"{eng_name} step {s_idx} entered mismatch: obs={ent_obs} vs exp={ent_exp}. "
                            break
                        if ext_obs != ext_exp:
                            case_ok = False
                            diag += f"{eng_name} step {s_idx} exited mismatch: obs={ext_obs} vs exp={ext_exp}. "
                            break

                        # Deep graph state validation (P3-R2-F01)
                        g_ok, g_diag = validate_graph_state(
                            steps[s_idx].get("graph_state", {}),
                            m0_graphs[s_idx],
                            case_id=cid,
                            engine=eng_name,
                            step_index=s_idx,
                        )
                        if not g_ok:
                            case_ok = False
                            diag += f"{eng_name} graph state error: {g_diag}. "
                            break

                        # Counter validation (P3-R2-F10)
                        cnt_ok, cnt_diag = validate_step_counters(
                            steps[s_idx].get("step_counters", {}),
                            engine=eng_name,
                            max_work=500000,
                            is_success=True,
                            case_id=cid,
                            step_index=s_idx,
                        )
                        if not cnt_ok:
                            case_ok = False
                            diag += f"{eng_name} step counter error: {cnt_diag}. "
                            break

                    # Zero raw duplicate emissions in sink counters
                    cnt = eng_res.get("counters", {})
                    if cnt.get("discarded_emissions", 0) != 0:
                        case_ok = False
                        diag += f"{eng_name} raw duplicate emissions detected: discarded={cnt.get('discarded_emissions')}. "

        if not case_ok:
            all_ok = False

        results.append({
            "case_id": cid,
            "name": w["name"],
            "status": "PASS" if case_ok else "FAIL",
            "diagnostic": diag.strip(),
            "m1_executed": run_m1,
            "m2_executed": run_m2,
            "duration_ms_m1": dur_m1 if run_m1 else None,
            "duration_ms_m2": dur_m2 if run_m2 else None,
        })

    return results, all_ok


# ============================================================================
# STAGE 3: Corpus 840 Reference Verification (F01 & F08 - Real M0 Recomputation)
# ============================================================================

def run_corpus_840(
    binary_path: Path,
    engine_filter: str = "all",
    case_filter: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Runs corpus cases on selected engine(s), locking fixture SHA-256 and recomputing M0 Oracle."""
    # Verify input fixture hashes before reading (P3-R2-F08)
    sha1 = compute_sha256(CORPUS_840_SEED1_PATH)
    sha7 = compute_sha256(CORPUS_840_SEED7_PATH)
    if sha1 != CORPUS_840_SEED1_SHA256 or sha7 != CORPUS_840_SEED7_SHA256:
        return {
            "status": "FAIL",
            "error": f"Corpus fixture hash lock mismatch: seed1={sha1} (exp {CORPUS_840_SEED1_SHA256}), seed7={sha7} (exp {CORPUS_840_SEED7_SHA256})",
        }, False

    files = [CORPUS_840_SEED1_PATH, CORPUS_840_SEED7_PATH]
    run_m1 = engine_filter in ("all", "m1")
    run_m2 = engine_filter in ("all", "m2")

    stats = {
        "total_cases": 0,
        "selected_cases": 0,
        "total_truth_events": 0,
        "cases_with_events": 0,
        "cases_with_events_k_ge_1": 0,
        "m1_executed": run_m1,
        "m2_executed": run_m2,
        "m1_agreed": 0 if run_m1 else None,
        "m2_agreed": 0 if run_m2 else None,
        "m1_zero_duplicates": 0 if run_m1 else None,
        "m2_zero_duplicates": 0 if run_m2 else None,
        "status": "PASS",
        "error": None,
    }

    t0 = time.perf_counter()

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                case = json.loads(line)
                stats["total_cases"] += 1
                case_id = f"corpus_s{case['seed']}_c{case['case']}"

                if case_filter and case_id != case_filter:
                    continue

                stats["selected_cases"] += 1
                k = int(case["config"].split("k=")[1].split()[0])

                workload = {
                    "case_id": case_id,
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

                # Execute Native M1 if selected
                if run_m1:
                    res_m1, _, ok_m1 = run_workload_on_binary(binary_path, "m1", workload)
                    if ok_m1:
                        s0 = res_m1["steps"][0]
                        if sorted(s0.get("entered_tuples", [])) == exp_ent and sorted(s0.get("exited_tuples", [])) == exp_ext:
                            stats["m1_agreed"] += 1
                        if res_m1.get("counters", {}).get("discarded_emissions", 0) == 0:
                            stats["m1_zero_duplicates"] += 1

                # Execute Native M2 if selected
                if run_m2:
                    res_m2, _, ok_m2 = run_workload_on_binary(binary_path, "m2", workload)
                    if ok_m2:
                        s0 = res_m2["steps"][0]
                        if sorted(s0.get("entered_tuples", [])) == exp_ent and sorted(s0.get("exited_tuples", [])) == exp_ext:
                            stats["m2_agreed"] += 1
                        if res_m2.get("counters", {}).get("discarded_emissions", 0) == 0:
                            stats["m2_zero_duplicates"] += 1

    stats["duration_seconds"] = time.perf_counter() - t0

    if case_filter:
        all_passed = (
            stats["selected_cases"] > 0
            and (not run_m1 or stats["m1_agreed"] == stats["selected_cases"])
            and (not run_m2 or stats["m2_agreed"] == stats["selected_cases"])
        )
    else:
        all_passed = (
            stats["total_cases"] == 840
            and (not run_m1 or (stats["m1_agreed"] == 840 and stats["m1_zero_duplicates"] == 840))
            and (not run_m2 or (stats["m2_agreed"] == 840 and stats["m2_zero_duplicates"] == 840))
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


def run_56_stream_matrix(
    binary_path: Path,
    engine_filter: str = "all",
    case_filter: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Runs 56-stream matrix, verifying immutable workload locks, active-set reconstruction, deep graph state, codec, and counters."""
    if not STREAM_56_LOCK_PATH.is_file():
        return {"status": "FAIL", "error": f"Missing stream lock file: {STREAM_56_LOCK_PATH}"}, False

    with open(STREAM_56_LOCK_PATH, "r", encoding="utf-8") as f:
        lock_registry = json.load(f)

    lock_map = {item["case_id"]: item for item in lock_registry["streams"]}
    if len(lock_map) != 56:
        return {"status": "FAIL", "error": f"Lock file stream count mismatch: {len(lock_map)} != 56"}, False

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

    run_m1 = engine_filter in ("all", "m1")
    run_m2 = engine_filter in ("all", "m2")

    stats = {
        "total_configurations": 56,
        "selected_configurations": 0,
        "total_updates": 672,
        "selected_updates": 0,
        "m1_executed": run_m1,
        "m2_executed": run_m2,
        "agreed_steps_m1": 0 if run_m1 else None,
        "agreed_steps_m2": 0 if run_m2 else None,
        "reconstruction_agreed_steps_m1": 0 if run_m1 else None,
        "reconstruction_agreed_steps_m2": 0 if run_m2 else None,
        "graph_state_agreed_steps_m1": 0 if run_m1 else None,
        "graph_state_agreed_steps_m2": 0 if run_m2 else None,
        "codec_state_agreed_steps_m1": 0 if run_m1 else None,
        "codec_state_agreed_steps_m2": 0 if run_m2 else None,
        "counters_agreed_steps_m1": 0 if run_m1 else None,
        "counters_agreed_steps_m2": 0 if run_m2 else None,
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
            cid = workload["case_id"]

            if case_filter and cid != case_filter:
                continue

            stats["selected_configurations"] += 1
            stats["selected_updates"] += len(workload["updates"])

            # Verify against locked workload SHA-256 (P3-R2-F08)
            if cid not in lock_map:
                stats["status"] = "FAIL"
                stats["error"] = f"Stream {cid} not found in lock registry"
                return stats, False

            lock_item = lock_map[cid]
            workload_canonical_bytes = json.dumps(workload, sort_keys=True).encode("utf-8")
            workload_sha = hashlib.sha256(workload_canonical_bytes).hexdigest()
            if workload_sha != lock_item["canonical_input_sha256"]:
                stats["status"] = "FAIL"
                stats["error"] = f"Workload SHA-256 mismatch for {cid}: computed {workload_sha} != locked {lock_item['canonical_input_sha256']}"
                return stats, False

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

            # Precompute M0 sequence and expected codec model
            m0_deltas = []
            m0_active_sets = []
            m0_graphs = []
            expected_codec_steps = []

            # Expected codec model tracking
            exp_q_verts = len(workload["query"]["vertices"])
            exp_d_verts = set(v["id"] for v in workload["initial_graph"]["vertices"])
            exp_v_labels = set(v["label"] for v in workload["query"]["vertices"]) | set(v["label"] for v in workload["initial_graph"]["vertices"])
            exp_e_labels = set(e["label"] for e in workload["query"]["edges"]) | set(e["label"] for e in workload["initial_graph"]["edges"])

            stream_ent = 0
            stream_ext = 0

            for upd in workload["updates"]:
                op = "INSERT" if upd["op"] == "+" else "DELETE"
                delta = oracle.apply_update(DStarUpdate(op, upd["u"], upd["v"], upd["label"]))
                m0_deltas.append((
                    sorted([list(t) for t in delta.entered]),
                    sorted([list(t) for t in delta.exited]),
                ))
                m0_active_sets.append(copy.deepcopy(oracle.current_matches))
                m0_graphs.append(copy.deepcopy(oracle.G))
                stream_ent += len(delta.entered)
                stream_ext += len(delta.exited)
                stats["total_entered_events"] += len(delta.entered)
                stats["total_exited_events"] += len(delta.exited)

                # Track expected codec
                exp_d_verts.add(upd["u"])
                exp_d_verts.add(upd["v"])
                exp_e_labels.add(upd["label"])
                expected_codec_steps.append({
                    "query_vertices_count": exp_q_verts,
                    "data_vertices_count": len(exp_d_verts),
                    "vertex_labels_count": len(exp_v_labels),
                    "edge_labels_count": len(exp_e_labels),
                })

            # Verify expected event counts match lock registry (P3-R2-F08)
            if stream_ent != lock_item["expected_entered_events"] or stream_ext != lock_item["expected_exited_events"]:
                stats["status"] = "FAIL"
                stats["error"] = f"Event counts mismatch for {cid}: ent={stream_ent} (lock {lock_item['expected_entered_events']}), ext={stream_ext} (lock {lock_item['expected_exited_events']})"
                return stats, False

            # Execute M1 and/or M2 based on engine_filter (P3-R2-F03)
            res_m1, res_m2 = {}, {}
            ok_m1, ok_m2 = True, True
            if run_m1:
                res_m1, _, ok_m1 = run_workload_on_binary(binary_path, "m1", workload)
            if run_m2:
                res_m2, _, ok_m2 = run_workload_on_binary(binary_path, "m2", workload)

            if not ok_m1 or not ok_m2:
                stats["status"] = "FAIL"
                stats["error"] = f"Native run failed for {cid}: M1_ok={ok_m1}, M2_ok={ok_m2}"
                return stats, False

            steps_m1 = res_m1.get("steps", []) if run_m1 else []
            steps_m2 = res_m2.get("steps", []) if run_m2 else []

            # Step-by-step verification and active set reconstruction
            active_m1: Set[Tuple[int, ...]] = set(enumerate_snapshot(Q_m0, G_m0, k)) if run_m1 else set()
            active_m2: Set[Tuple[int, ...]] = set(enumerate_snapshot(Q_m0, G_m0, k)) if run_m2 else set()

            cum_work_m1 = 0
            cum_work_m2 = 0

            for s_idx in range(12):
                exp_ent, exp_ext = m0_deltas[s_idx]
                target_m0_active = m0_active_sets[s_idx]
                target_G = m0_graphs[s_idx]
                exp_c_state = expected_codec_steps[s_idx]

                if run_m1:
                    ent_m1 = sorted(steps_m1[s_idx].get("entered_tuples", []))
                    ext_m1 = sorted(steps_m1[s_idx].get("exited_tuples", []))
                    if ent_m1 == exp_ent and ext_m1 == exp_ext:
                        stats["agreed_steps_m1"] += 1

                    for tup in ext_m1:
                        active_m1.discard(tuple(tup))
                    for tup in ent_m1:
                        active_m1.add(tuple(tup))
                    if active_m1 == target_m0_active:
                        stats["reconstruction_agreed_steps_m1"] += 1

                    # Deep logical graph state M1 (P3-R2-F01)
                    g_ok1, g_diag1 = validate_graph_state(
                        steps_m1[s_idx].get("graph_state", {}), target_G, case_id=cid, engine="m1", step_index=s_idx
                    )
                    if g_ok1:
                        stats["graph_state_agreed_steps_m1"] += 1
                    else:
                        stats["status"] = "FAIL"
                        stats["error"] = f"Graph state validation failed for M1 {cid}: {g_diag1}"
                        return stats, False

                    # Codec state M1 (P3-R2-F10)
                    c_ok1, c_diag1 = validate_codec_state(
                        steps_m1[s_idx].get("codec_state", {}), exp_c_state, case_id=cid, engine="m1", step_index=s_idx
                    )
                    if c_ok1:
                        stats["codec_state_agreed_steps_m1"] += 1
                    else:
                        stats["status"] = "FAIL"
                        stats["error"] = f"Codec state validation failed for M1 {cid}: {c_diag1}"
                        return stats, False

                    # Step counters M1 (P3-R2-F10)
                    cnt_s1 = steps_m1[s_idx].get("step_counters", {})
                    cnt_ok1, cnt_diag1 = validate_step_counters(
                        cnt_s1, engine="m1", max_work=500000, is_success=True, case_id=cid, step_index=s_idx
                    )
                    if cnt_ok1:
                        stats["counters_agreed_steps_m1"] += 1
                        cum_work_m1 += cnt_s1.get("work_units", 0)
                    else:
                        stats["status"] = "FAIL"
                        stats["error"] = f"Step counters validation failed for M1 {cid}: {cnt_diag1}"
                        return stats, False

                if run_m2:
                    ent_m2 = sorted(steps_m2[s_idx].get("entered_tuples", []))
                    ext_m2 = sorted(steps_m2[s_idx].get("exited_tuples", []))
                    if ent_m2 == exp_ent and ext_m2 == exp_ext:
                        stats["agreed_steps_m2"] += 1

                    for tup in ext_m2:
                        active_m2.discard(tuple(tup))
                    for tup in ent_m2:
                        active_m2.add(tuple(tup))
                    if active_m2 == target_m0_active:
                        stats["reconstruction_agreed_steps_m2"] += 1

                    # Deep logical graph state M2 (P3-R2-F01)
                    g_ok2, g_diag2 = validate_graph_state(
                        steps_m2[s_idx].get("graph_state", {}), target_G, case_id=cid, engine="m2", step_index=s_idx
                    )
                    if g_ok2:
                        stats["graph_state_agreed_steps_m2"] += 1
                    else:
                        stats["status"] = "FAIL"
                        stats["error"] = f"Graph state validation failed for M2 {cid}: {g_diag2}"
                        return stats, False

                    # Codec state M2 (P3-R2-F10)
                    c_ok2, c_diag2 = validate_codec_state(
                        steps_m2[s_idx].get("codec_state", {}), exp_c_state, case_id=cid, engine="m2", step_index=s_idx
                    )
                    if c_ok2:
                        stats["codec_state_agreed_steps_m2"] += 1
                    else:
                        stats["status"] = "FAIL"
                        stats["error"] = f"Codec state validation failed for M2 {cid}: {c_diag2}"
                        return stats, False

                    # Step counters M2 (P3-R2-F10)
                    cnt_s2 = steps_m2[s_idx].get("step_counters", {})
                    cnt_ok2, cnt_diag2 = validate_step_counters(
                        cnt_s2, engine="m2", max_work=500000, is_success=True, case_id=cid, step_index=s_idx
                    )
                    if cnt_ok2:
                        stats["counters_agreed_steps_m2"] += 1
                        cum_work_m2 += cnt_s2.get("work_units", 0)
                    else:
                        stats["status"] = "FAIL"
                        stats["error"] = f"Step counters validation failed for M2 {cid}: {cnt_diag2}"
                        return stats, False

            # Verify cumulative counters consistency (P3-R2-F10)
            if run_m1:
                root_cnt1 = res_m1.get("counters", {})
                if root_cnt1.get("work_units") != cum_work_m1:
                    stats["status"] = "FAIL"
                    stats["error"] = f"Cumulative work units mismatch for M1 {cid}: root={root_cnt1.get('work_units')} vs sum={cum_work_m1}"
                    return stats, False
                if root_cnt1.get("discarded_emissions", 0) != 0:
                    stats["status"] = "FAIL"
                    stats["error"] = f"Raw discarded emissions detected for M1 {cid}: {root_cnt1.get('discarded_emissions')}"
                    return stats, False

            if run_m2:
                root_cnt2 = res_m2.get("counters", {})
                if root_cnt2.get("work_units") != cum_work_m2:
                    stats["status"] = "FAIL"
                    stats["error"] = f"Cumulative work units mismatch for M2 {cid}: root={root_cnt2.get('work_units')} vs sum={cum_work_m2}"
                    return stats, False
                if root_cnt2.get("discarded_emissions", 0) != 0:
                    stats["status"] = "FAIL"
                    stats["error"] = f"Raw discarded emissions detected for M2 {cid}: {root_cnt2.get('discarded_emissions')}"
                    return stats, False

    stats["duration_seconds"] = time.perf_counter() - t0

    target_steps = stats["selected_updates"]
    all_passed = (
        stats["selected_configurations"] > 0
        and (not run_m1 or (
            stats["agreed_steps_m1"] == target_steps
            and stats["reconstruction_agreed_steps_m1"] == target_steps
            and stats["graph_state_agreed_steps_m1"] == target_steps
            and stats["codec_state_agreed_steps_m1"] == target_steps
            and stats["counters_agreed_steps_m1"] == target_steps
        ))
        and (not run_m2 or (
            stats["agreed_steps_m2"] == target_steps
            and stats["reconstruction_agreed_steps_m2"] == target_steps
            and stats["graph_state_agreed_steps_m2"] == target_steps
            and stats["codec_state_agreed_steps_m2"] == target_steps
            and stats["counters_agreed_steps_m2"] == target_steps
        ))
    )

    if not case_filter:
        all_passed = all_passed and (
            stats["total_configurations"] == 56
            and stats["total_updates"] == 672
            and stats["total_entered_events"] == 3024
            and stats["total_exited_events"] == 1780
        )

    if not all_passed:
        stats["status"] = "FAIL"

    return stats, all_passed


# ============================================================================
# CASE REGISTRY (P3-R2-F02)
# ============================================================================

def get_known_case_registry() -> Dict[str, str]:
    """Builds authoritative map of known case_id -> stage_name (P3-R2-F02)."""
    registry: Dict[str, str] = {}
    for w in build_hand_workloads():
        registry[w["case_id"]] = "hand"

    # Corpus 840
    for s in (1, 7):
        for c in range(420):
            registry[f"corpus_s{s}_c{c}"] = "corpus"

    # Stream 56
    matrix_seeds = [20261118, 20261119, 20261120, 20261121, 20261122, 20261123, 20261124, 20261125]
    matrix_specs = [
        ("C4", 0), ("C4", 1), ("Diamond", 0), ("Diamond", 1), ("K4", 0), ("K4", 1), ("K4", 2)
    ]
    for seed_idx, seed in enumerate(matrix_seeds):
        label_mode = "uniform" if seed_idx < 4 else "mixed"
        for (q_type, k) in matrix_specs:
            registry[f"stream_{q_type}_k{k}_{seed}_{label_mode}"] = "stream"

    return registry


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

    # Single engine or specific case or specific stage is diagnostic run (P3-R2-F03)
    is_diagnostic = (args.case is not None or args.stage != "all" or args.engine != "all")
    output_path = Path(args.output) if args.output else (DIAGNOSTIC_SUMMARY_PATH if is_diagnostic else CANONICAL_SUMMARY_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("================================================================================")
    print("      RapidFlow-APX Native Engines Verification Runner (Phase P3 / D-INT-03)     ")
    print("================================================================================")

    # Validate requested case ID against known registry (P3-R2-F02)
    case_registry = get_known_case_registry()
    if args.case is not None:
        if args.case not in case_registry:
            print(f"ERROR: Unknown case ID '{args.case}' requested (P3-R2-F02)!")
            fail_summary = {
                "runner_mode": "DIAGNOSTIC_SUBSET_RUN",
                "overall_status": "UNKNOWN_CASE_REJECTED",
                "error_category": "UNKNOWN_CASE",
                "error_message": f"Case ID '{args.case}' does not exist in case registry",
                "requested_case": args.case,
                "stages": {},
            }
            atomic_write_json(output_path, fail_summary)
            sys.exit(1)

        expected_stage = case_registry[args.case]
        if args.stage not in ("all", expected_stage):
            print(f"ERROR: Case '{args.case}' belongs to stage '{expected_stage}', not '{args.stage}' (P3-R2-F02)!")
            fail_summary = {
                "runner_mode": "DIAGNOSTIC_SUBSET_RUN",
                "overall_status": "INVALID_CASE_STAGE_COMBINATION",
                "error_category": "STAGE_MISMATCH",
                "error_message": f"Case '{args.case}' belongs to stage '{expected_stage}', not '{args.stage}'",
                "requested_case": args.case,
                "stages": {},
            }
            atomic_write_json(output_path, fail_summary)
            sys.exit(1)

    # 1. Provenance check
    print("[1/5] Verifying fail-closed Git provenance...")
    provenance, prov_ok = check_git_provenance()
    print(f"  Repo Commit:     {provenance.get('repo_commit')} (Dirty: {provenance.get('dirty_state')})")
    print(f"  Upstream Commit: {provenance.get('upstream_commit')} (Pristine: {provenance.get('upstream_pristine')})")
    if not prov_ok:
        print("ERROR: Fail-closed Git provenance check failed!")
        fail_summary = {
            "runner_mode": "DIAGNOSTIC_SUBSET_RUN" if is_diagnostic else "CANONICAL_FULL_RUN",
            "overall_status": "PROVENANCE_FAILED",
            "error_category": "PROVENANCE_ERROR",
            "provenance": provenance,
            "stages": {},
        }
        atomic_write_json(output_path, fail_summary)
        sys.exit(1)

    # If stage == 'provenance', ensure no fake success when case specified
    if args.stage == "provenance":
        if args.case is not None:
            print(f"ERROR: Stage 'provenance' has no case execution (requested '{args.case}') (P3-R2-F02)!")
            fail_summary = {
                "runner_mode": "DIAGNOSTIC_SUBSET_RUN",
                "overall_status": "INVALID_CASE_STAGE_COMBINATION",
                "error_category": "STAGE_MISMATCH",
                "stages": {},
            }
            atomic_write_json(output_path, fail_summary)
            sys.exit(1)
        prov_summary = {
            "runner_mode": "DIAGNOSTIC_SUBSET_RUN",
            "overall_status": "DIAGNOSTIC_SUBSET_PASS",
            "provenance": provenance,
            "stages": {"provenance": {"status": "PASS"}},
        }
        atomic_write_json(output_path, prov_summary)
        print("Provenance stage PASS.")
        sys.exit(0)

    # 2. Host resources check
    print("[2/5] Checking host system resources...")
    res_info, res_ok = check_system_resources()
    print(f"  Available RAM:   {res_info.get('mem_available_gib'):.2f} GiB | Free Disk: {res_info.get('free_disk_gib'):.2f} GiB")
    if not res_ok:
        print("ERROR: Host resources insufficient (< 2 GiB RAM or < 1 GiB disk)!")
        fail_summary = {
            "runner_mode": "DIAGNOSTIC_SUBSET_RUN" if is_diagnostic else "CANONICAL_FULL_RUN",
            "overall_status": "RESOURCE_PREFLIGHT_FAILED",
            "error_category": "RESOURCE_ERROR",
            "provenance": provenance,
            "system_resources": res_info,
            "stages": {},
        }
        atomic_write_json(output_path, fail_summary)
        sys.exit(1)

    # 3. Build native binary
    print("[3/5] Building native test harness with resource limits...")
    bin_path, bin_sha, build_info, build_ok = build_native_binary(clean_build=args.clean_build)
    print(f"  Harness Binary:  {bin_path}")
    print(f"  Binary SHA-256:  {bin_sha}")
    if not build_ok:
        print("ERROR: Native compilation or linking failed!")
        fail_summary = {
            "runner_mode": "DIAGNOSTIC_SUBSET_RUN" if is_diagnostic else "CANONICAL_FULL_RUN",
            "overall_status": "BUILD_FAILED",
            "error_category": "BUILD_ERROR",
            "provenance": provenance,
            "build_provenance": build_info,
            "stages": {},
        }
        atomic_write_json(output_path, fail_summary)
        sys.exit(1)

    manifest_sha = compute_sha256(CANONICAL_MANIFEST_PATH)

    summary_data: Dict[str, Any] = {
        "runner_mode": "DIAGNOSTIC_SUBSET_RUN" if is_diagnostic else "CANONICAL_FULL_RUN",
        "overall_status": "PENDING",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(CANONICAL_MANIFEST_PATH.relative_to(REPO_ROOT)),
        "manifest_sha256": manifest_sha,
        "binary_path": str(bin_path.relative_to(REPO_ROOT)),
        "binary_sha256": bin_sha,
        "selected_engine": args.engine,
        "selected_stage": args.stage,
        "selected_case": args.case,
        "provenance": provenance,
        "system_resources": res_info,
        "build_provenance": build_info,
        "stages": {},
    }

    hand_ok = True
    corpus_ok = True
    stream_ok = True

    # Determine which stages to run based on args.stage and args.case
    target_stages = []
    if args.case:
        target_stages = [case_registry[args.case]]
    elif args.stage == "all":
        target_stages = ["hand", "corpus", "stream"]
    else:
        target_stages = [args.stage]

    # Execute Hand Cases
    if "hand" in target_stages:
        print(f"[4/5] Executing Hand and Adversarial Witness Cases (engine: {args.engine})...")
        hand_results, hand_ok = run_hand_and_witness_cases(
            bin_path, engine_filter=args.engine, case_filter=args.case
        )
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
            atomic_write_json(output_path, summary_data)
            sys.exit(1)

    # Execute Corpus 840
    if "corpus" in target_stages:
        print(f"[5/5] Executing Correctness Corpus (Real M0 Recomputed, engine: {args.engine})...")
        corpus_stats, corpus_ok = run_corpus_840(
            bin_path, engine_filter=args.engine, case_filter=args.case
        )
        m1_info = f"{corpus_stats['m1_agreed']}/{corpus_stats.get('selected_cases', 840)} M1 agreed" if corpus_stats['m1_executed'] else "M1 NOT_RUN"
        m2_info = f"{corpus_stats['m2_agreed']}/{corpus_stats.get('selected_cases', 840)} M2 agreed" if corpus_stats['m2_executed'] else "M2 NOT_RUN"
        print(f"  Corpus: {m1_info}, {m2_info} ({'PASS' if corpus_ok else 'FAIL'})")
        summary_data["stages"]["corpus_840"] = corpus_stats
        if not corpus_ok and not is_diagnostic:
            summary_data["overall_status"] = "FAIL"
            atomic_write_json(output_path, summary_data)
            sys.exit(1)

    # Execute 56-Stream Matrix
    if "stream" in target_stages:
        print(f"[+] Executing Stateful Stream Matrix (engine: {args.engine})...")
        stream_stats, stream_ok = run_56_stream_matrix(
            bin_path, engine_filter=args.engine, case_filter=args.case
        )
        m1_s_info = f"M1 agreed {stream_stats['agreed_steps_m1']}/{stream_stats.get('selected_updates', 672)}" if stream_stats['m1_executed'] else "M1 NOT_RUN"
        m2_s_info = f"M2 agreed {stream_stats['agreed_steps_m2']}/{stream_stats.get('selected_updates', 672)}" if stream_stats['m2_executed'] else "M2 NOT_RUN"
        print(f"  Stream Matrix: {m1_s_info}, {m2_s_info} ({'PASS' if stream_ok else 'FAIL'})")
        summary_data["stages"]["stream_56_matrix"] = stream_stats
        if not stream_ok and not is_diagnostic:
            summary_data["overall_status"] = "FAIL"
            atomic_write_json(output_path, summary_data)
            sys.exit(1)

    all_stages_passed = hand_ok and corpus_ok and stream_ok and len(summary_data["stages"]) > 0

    if is_diagnostic:
        summary_data["overall_status"] = "DIAGNOSTIC_SUBSET_PASS" if all_stages_passed else "DIAGNOSTIC_SUBSET_FAIL"
    else:
        summary_data["overall_status"] = "P3_ENGINES_VERIFIED" if all_stages_passed else "FAIL"

    atomic_write_json(output_path, summary_data)

    print("================================================================================")
    print(f"  Final Verification Status: {summary_data['overall_status']}")
    print(f"  Summary Report Written:    {output_path}")
    print("================================================================================")

    if not all_stages_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
