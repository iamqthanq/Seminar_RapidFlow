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
REQUIRED_HAND_CASES_PATH = REPO_ROOT / "configs" / "rapidflow_apx_required_cases.json"
CANONICAL_MAX_WORK_CAP = 500000
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
                # Only execution reports under reports/rapidflow_apx_engines/ and the validation report are allowed during runs
                if not (
                    fpath.startswith("reports/rapidflow_apx_engines/")
                    or fpath.startswith("reports\\rapidflow_apx_engines\\")
                    or fpath == "docs/rapidflow_apx_native_engines_validation_report.md"
                ):
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


def independent_edge_connectivity_cut_oracle(num_vertices: int, edges: List[Tuple[int, int]]) -> int:
    """Computes exact edge connectivity lambda(Q) by min-cut enumeration over all non-empty proper vertex cuts."""
    if num_vertices <= 1:
        return 0
    if num_vertices == 2:
        return len(edges)
    min_cut = len(edges)
    nodes = list(range(num_vertices))
    for r in range(1, num_vertices):
        for combo in itertools.combinations(nodes[1:], r - 1):
            S = set((0,) + combo)
            cut_edges = sum(1 for (u, v) in edges if (u in S) != (v in S))
            if cut_edges < min_cut:
                min_cut = cut_edges
    return min_cut


def validate_micro_workload_bounds(workload: Dict[str, Any]) -> Tuple[bool, str]:
    """Validates that a canonical test workload strictly adheres to micro-workload containment guards (P3-R3-F08):
    |V(Q)| <= 6, |V(G_0)| <= 8, len(updates) <= 12, max_work <= 500000."""
    if not isinstance(workload, dict):
        return False, "Workload is not an object"

    q_obj = workload.get("query")
    if not isinstance(q_obj, dict):
        return False, "Workload missing 'query' object"
    q_v = q_obj.get("vertices", [])
    if len(q_v) > 6:
        return False, f"Micro-workload containment violation: |V(Q)|={len(q_v)} exceeds cap 6"
    if len(q_v) < 1:
        return False, f"Micro-workload containment violation: |V(Q)|={len(q_v)} < 1"

    init_g = workload.get("initial_graph")
    if not isinstance(init_g, dict):
        return False, "Workload missing 'initial_graph' object"
    g_v = init_g.get("vertices", [])
    if len(g_v) > 8:
        return False, f"Micro-workload containment violation: |V(G_0)|={len(g_v)} exceeds cap 8"

    updates = workload.get("updates", [])
    if not isinstance(updates, list):
        return False, "Workload missing 'updates' array"
    if len(updates) > 12:
        return False, f"Micro-workload containment violation: updates count={len(updates)} exceeds cap 12"

    mw = workload.get("max_work")
    if mw is not None and (type(mw) is not int or mw > CANONICAL_MAX_WORK_CAP):
        return False, f"Micro-workload containment violation: max_work={mw} exceeds cap {CANONICAL_MAX_WORK_CAP}"

    return True, ""


def compute_canonical_workload_sha256(workload: Dict[str, Any]) -> str:
    """Computes stable cryptographic SHA-256 hash of a workload dictionary (P3-R4-F04)."""
    canonical_bytes = json.dumps(workload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def validate_required_hand_cases(
    workloads: List[Dict[str, Any]],
    registry_path: Path = REQUIRED_HAND_CASES_PATH,
) -> Tuple[bool, str]:
    """Validates that hand and witness workloads strictly match the immutable required registry (P3-R3-F03, P3-R4-F04).
    Ensures exact set match, canonical input content hashes, and prevents self-authorizing denominators or dropped cases."""
    if not registry_path.is_file():
        return False, f"Required hand cases registry not found: {registry_path}"

    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
    except Exception as e:
        return False, f"Required hand cases registry JSON error: {e}"

    if not isinstance(reg, dict):
        return False, "Required hand cases registry is not an object"

    if reg.get("registry_version") not in ("1.0.0", "1.0", "2.0.0"):
        return False, f"Registry version mismatch: {reg.get('registry_version')}"

    if reg.get("total_required_cases") != 25:
        return False, f"Registry total_required_cases mismatch: {reg.get('total_required_cases')} (expected 25)"

    cases = reg.get("cases")
    if not isinstance(cases, list) or len(cases) != 25:
        return False, f"Registry cases array length mismatch: {len(cases) if isinstance(cases, list) else None} != 25"

    reg_map = {}
    for c in cases:
        if not isinstance(c, dict):
            return False, f"Registry case element is not a dict: {c}"
        cid = c.get("case_id")
        if not isinstance(cid, str) or not cid:
            return False, f"Registry case missing or invalid case_id: {cid}"
        if cid in reg_map:
            return False, f"Duplicate case_id in required registry: {cid}"
        reg_map[cid] = c

    wl_ids = [w.get("case_id") for w in workloads if isinstance(w, dict)]
    if len(wl_ids) != 25:
        return False, f"Workloads count mismatch: obs={len(wl_ids)} vs exp=25"
    if len(set(wl_ids)) != 25:
        return False, f"Duplicate case_id in workloads: unique={len(set(wl_ids))} vs total={len(wl_ids)}"

    missing = set(reg_map.keys()) - set(wl_ids)
    if missing:
        return False, f"Required cases missing from generated workloads: {sorted(missing)}"

    extra = set(wl_ids) - set(reg_map.keys())
    if extra:
        return False, f"Unexpected extra cases in generated workloads: {sorted(extra)}"

    for w in workloads:
        cid = w["case_id"]
        req = reg_map[cid]
        c_name = req.get("case_name") or req.get("name")
        if w.get("name") != c_name:
            return False, f"Case {cid} name mismatch: obs='{w.get('name')}' vs exp='{c_name}'"
        if w.get("k") != req.get("k"):
            return False, f"Case {cid} threshold k mismatch: obs={w.get('k')} vs exp={req.get('k')}"
        if "query_vertices_count" in req:
            exp_qv = req["query_vertices_count"]
            obs_qv = len(w.get("query", {}).get("vertices", []))
            if obs_qv != exp_qv:
                return False, f"Case {cid} query vertices count mismatch: obs={obs_qv} vs exp={exp_qv}"
        if "query_edges_count" in req:
            exp_qe = req["query_edges_count"]
            obs_qe = len(w.get("query", {}).get("edges", []))
            if obs_qe != exp_qe:
                return False, f"Case {cid} query edges count mismatch: obs={obs_qe} vs exp={exp_qe}"
        if "updates_count" in req:
            exp_upd = req["updates_count"]
            obs_upd = len(w.get("updates", []))
            if obs_upd != exp_upd:
                return False, f"Case {cid} updates count mismatch: obs={obs_upd} vs exp={exp_upd}"

        # Cryptographic content locking (P3-R4-F04)
        if "canonical_workload_sha256" in req:
            exp_w_sha = req["canonical_workload_sha256"]
            obs_w_sha = compute_canonical_workload_sha256(w)
            if obs_w_sha != exp_w_sha:
                return False, f"Case {cid} canonical workload SHA-256 mismatch: obs={obs_w_sha} vs locked={exp_w_sha}"

        is_neg = w.get("expected_error", False)
        if is_neg != req.get("is_negative"):
            return False, f"Case {cid} is_negative mismatch: obs={is_neg} vs exp={req.get('is_negative')}"
        if is_neg:
            if w.get("expected_exit_code") != req.get("expected_exit_code"):
                return False, f"Case {cid} expected_exit_code mismatch: obs={w.get('expected_exit_code')} vs exp={req.get('expected_exit_code')}"
            if w.get("expected_error_category") != req.get("expected_error_category"):
                return False, f"Case {cid} expected_error_category mismatch: obs={w.get('expected_error_category')} vs exp={req.get('expected_error_category')}"

    return True, ""


def validate_stream_lock_registry(lock_path: Path = STREAM_56_LOCK_PATH) -> Tuple[bool, str]:
    """Validates the complete integrity of the stream 56 lock registry (P3-R3-F05, P3-R4-F05).
    Verifies cardinality (56), uniqueness (no duplicates), metadata fields, exact canonical matrix identity relations, and global consistency sums."""
    if not lock_path.is_file():
        return False, f"Stream lock registry file not found: {lock_path}"

    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            lock_registry = json.load(f)
    except Exception as e:
        return False, f"Stream lock registry JSON parse error: {e}"

    if not isinstance(lock_registry, dict):
        return False, "Stream lock registry root is not an object"

    if lock_registry.get("version") not in ("1.0", "1.0.0"):
        return False, f"Lock registry version mismatch: {lock_registry.get('version')} (expected '1.0' or '1.0.0')"
    if lock_registry.get("total_streams") != 56:
        return False, f"Lock registry total_streams mismatch: {lock_registry.get('total_streams')} (expected 56)"
    if lock_registry.get("total_updates") != 672:
        return False, f"Lock registry total_updates mismatch: {lock_registry.get('total_updates')} (expected 672)"
    if lock_registry.get("expected_entered_events") != 3024:
        return False, f"Lock registry expected_entered_events mismatch: {lock_registry.get('expected_entered_events')} (expected 3024)"
    if lock_registry.get("expected_exited_events") != 1780:
        return False, f"Lock registry expected_exited_events mismatch: {lock_registry.get('expected_exited_events')} (expected 1780)"

    streams = lock_registry.get("streams")
    if not isinstance(streams, list):
        return False, "Lock registry 'streams' is not a list"
    if len(streams) != 56:
        return False, f"Lock registry streams array length mismatch: {len(streams)} != 56"

    stream_ids = [s.get("case_id") for s in streams if isinstance(s, dict)]
    if len(stream_ids) != 56:
        return False, f"Lock registry streams contains non-dict elements: {len(stream_ids)} != 56"
    if len(set(stream_ids)) != 56:
        return False, f"Lock registry contains duplicate case_ids: unique={len(set(stream_ids))} vs total=56"

    matrix_seeds_list = [20261118, 20261119, 20261120, 20261121, 20261122, 20261123, 20261124, 20261125]
    matrix_seeds = set(matrix_seeds_list)
    valid_configs = {
        ("C4", 0): 2,
        ("C4", 1): 2,
        ("Diamond", 0): 2,
        ("Diamond", 1): 2,
        ("K4", 0): 3,
        ("K4", 1): 3,
        ("K4", 2): 3,
    }

    # Strict canonical matrix identity relation check (P3-R4-F05)
    expected_stream_ids = set()
    for (qt, qk) in valid_configs.keys():
        for s_idx, s_val in enumerate(matrix_seeds_list):
            l_mode = "uniform" if s_idx < 4 else "mixed"
            expected_stream_ids.add(f"stream_{qt}_k{qk}_{s_val}_{l_mode}")

    if set(stream_ids) != expected_stream_ids:
        missing_ids = expected_stream_ids - set(stream_ids)
        extra_ids = set(stream_ids) - expected_stream_ids
        return False, f"Stream IDs set mismatch with canonical matrix specification: missing={missing_ids}, extra={extra_ids}"

    sum_updates = 0
    sum_entered = 0
    sum_exited = 0

    for idx, s in enumerate(streams):
        cid = s.get("case_id")
        if not isinstance(cid, str) or not cid:
            return False, f"Stream {idx} invalid case_id: {cid}"
        q_type = s.get("q_type")
        k = s.get("k")
        if (q_type, k) not in valid_configs:
            return False, f"Stream {cid} invalid (q_type, k) configuration: ({q_type}, {k})"
        seed = s.get("seed")
        if seed not in matrix_seeds:
            return False, f"Stream {cid} unknown seed: {seed}"
        seed_idx = matrix_seeds_list.index(seed)
        exp_label_mode = "uniform" if seed_idx < 4 else "mixed"
        label_mode = s.get("label_mode")
        if label_mode != exp_label_mode:
            return False, f"Stream {cid} label_mode mismatch: obs='{label_mode}' vs exp='{exp_label_mode}'"
        exp_cid = f"stream_{q_type}_k{k}_{seed}_{label_mode}"
        if cid != exp_cid:
            return False, f"Stream {cid} case_id mismatch with metadata: obs='{cid}' vs exp='{exp_cid}'"
        lam = s.get("lambda")
        exp_lam = valid_configs[(q_type, k)]
        if lam != exp_lam:
            return False, f"Stream {cid} lambda mismatch: obs={lam} vs exp={exp_lam}"
        if k >= lam:
            return False, f"Stream {cid} violates Domain D-R: k={k} >= lambda={lam}"
        upd_cnt = s.get("update_count")
        if upd_cnt != 12:
            return False, f"Stream {cid} update_count mismatch: {upd_cnt} != 12"
        h = s.get("canonical_input_sha256")
        if not isinstance(h, str) or len(h) != 64:
            return False, f"Stream {cid} invalid canonical_input_sha256: {h}"
        ent = s.get("expected_entered_events")
        if type(ent) is not int or ent < 0:
            return False, f"Stream {cid} invalid expected_entered_events: {ent}"
        ext = s.get("expected_exited_events")
        if type(ext) is not int or ext < 0:
            return False, f"Stream {cid} invalid expected_exited_events: {ext}"

        sum_updates += upd_cnt
        sum_entered += ent
        sum_exited += ext

    if sum_updates != 672:
        return False, f"Sum of stream updates mismatch: {sum_updates} != 672"
    if sum_entered != 3024:
        return False, f"Sum of entered events mismatch: {sum_entered} != 3024"
    if sum_exited != 1780:
        return False, f"Sum of exited events mismatch: {sum_exited} != 1780"

    return True, ""


def validate_graph_state(
    observed: Dict[str, Any],
    expected_graph: DStarGraph,
    case_id: str = "",
    engine: str = "",
    step_index: int = 0,
) -> Tuple[bool, str]:
    """Deep logical graph state validation with strict typed assertions (P3-R2-F01, P3-R3-F06).
    Enforces int not bool, int not float, round-trip neighbor strings, int64 ranges, symmetry, and degree conservation."""
    if not isinstance(observed, dict):
        return False, f"[{case_id}:{engine}:s{step_index}] observed graph_state is not a dict: {type(observed)}"

    exp_v_count = len(expected_graph.vertices)
    exp_e_count = len(expected_graph.edges)

    obs_v_count = observed.get("vertices_count")
    obs_e_count = observed.get("edges_count")

    if type(obs_v_count) is not int or obs_v_count < 0:
        return False, f"[{case_id}:{engine}:s{step_index}] vertices_count is not non-negative integer: {obs_v_count} (type={type(obs_v_count)})"
    if type(obs_e_count) is not int or obs_e_count < 0:
        return False, f"[{case_id}:{engine}:s{step_index}] edges_count is not non-negative integer: {obs_e_count} (type={type(obs_e_count)})"

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
        if type(vid) is not int or isinstance(vid, bool) or vid < -9223372036854775808 or vid > 9223372036854775807:
            return False, f"[{case_id}:{engine}:s{step_index}] invalid vertex id: {vid} (type={type(vid)})"
        if vid in obs_v_map:
            return False, f"[{case_id}:{engine}:s{step_index}] duplicate vertex id in graph_state: {vid}"
        obs_v_map[vid] = v_obj

    # Check each expected vertex
    for exp_vid, exp_lbl in expected_graph.vertices.items():
        if exp_vid not in obs_v_map:
            return False, f"[{case_id}:{engine}:s{step_index}] expected vertex {exp_vid} missing from observed vertices"
        v_obj = obs_v_map[exp_vid]
        obs_lbl = v_obj.get("label")
        if type(obs_lbl) is not type(exp_lbl) or (isinstance(obs_lbl, bool) != isinstance(exp_lbl, bool)) or obs_lbl != exp_lbl:
            return False, f"[{case_id}:{engine}:s{step_index}] vertex {exp_vid} label mismatch: obs={obs_lbl} ({type(obs_lbl)}) vs exp={exp_lbl} ({type(exp_lbl)})"
        exp_nbrs = set(expected_graph.adj.get(exp_vid, {}).keys())
        exp_deg = len(exp_nbrs)
        obs_deg = v_obj.get("degree")
        if type(obs_deg) is not int or isinstance(obs_deg, bool) or obs_deg < 0:
            return False, f"[{case_id}:{engine}:s{step_index}] vertex {exp_vid} degree is not non-negative int: {obs_deg} (type={type(obs_deg)})"
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
        if type(u) is not int or type(v) is not int or isinstance(u, bool) or isinstance(v, bool) or u < -9223372036854775808 or u > 9223372036854775807 or v < -9223372036854775808 or v > 9223372036854775807:
            return False, f"[{case_id}:{engine}:s{step_index}] invalid edge endpoints: u={u} (type={type(u)}), v={v} (type={type(v)})"
        if u == v:
            return False, f"[{case_id}:{engine}:s{step_index}] self-loop in observed edges: ({u}, {v})"
        if type(lbl) is int and isinstance(lbl, bool):
            return False, f"[{case_id}:{engine}:s{step_index}] edge label is boolean: {lbl}"
        canonical_edge = (min(u, v), max(u, v), lbl, type(lbl))
        if canonical_edge in obs_edge_set:
            return False, f"[{case_id}:{engine}:s{step_index}] duplicate edge in observed edges: {canonical_edge}"
        obs_edge_set.add(canonical_edge)

    for (exp_u, exp_v), exp_lbl in expected_graph.edges.items():
        can_exp = (min(exp_u, exp_v), max(exp_u, exp_v), exp_lbl, type(exp_lbl))
        if can_exp not in obs_edge_set:
            return False, f"[{case_id}:{engine}:s{step_index}] expected edge {can_exp} missing from observed edges"

    # Check neighbors (P3-R4-F06: exact equality of neighbor keys set, int64 range, no extra vertices)
    obs_neighbors = observed.get("neighbors")
    if not isinstance(obs_neighbors, dict):
        return False, f"[{case_id}:{engine}:s{step_index}] neighbors is not a dict: {type(obs_neighbors)}"

    exp_nbr_keys = {str(vid) for vid in expected_graph.vertices}
    obs_nbr_keys = set(obs_neighbors.keys())
    if obs_nbr_keys != exp_nbr_keys:
        extra_keys = obs_nbr_keys - exp_nbr_keys
        missing_keys = exp_nbr_keys - obs_nbr_keys
        return False, f"[{case_id}:{engine}:s{step_index}] neighbor keys mismatch with graph vertices: extra={extra_keys}, missing={missing_keys}"

    for k_str, obs_nbr_list in obs_neighbors.items():
        if not isinstance(k_str, str):
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor key is not string: {k_str} (type={type(k_str)})"
        try:
            k_int = int(k_str)
        except (ValueError, TypeError):
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor key not an integer string: {k_str}"
        if str(k_int) != k_str:
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor key round-trip failed: '{k_str}' vs '{str(k_int)}'"
        if k_int < -9223372036854775808 or k_int > 9223372036854775807:
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor key out of int64 range: {k_int}"
        if not isinstance(obs_nbr_list, list):
            return False, f"[{case_id}:{engine}:s{step_index}] neighbor list for {k_str} is not a list"
        for nid in obs_nbr_list:
            if type(nid) is not int or isinstance(nid, bool) or nid < -9223372036854775808 or nid > 9223372036854775807:
                return False, f"[{case_id}:{engine}:s{step_index}] neighbor ID is not int64: {nid} (type={type(nid)})"
            if nid == k_int:
                return False, f"[{case_id}:{engine}:s{step_index}] self-loop neighbor detected for {k_str}"
        if len(obs_nbr_list) != len(set(obs_nbr_list)):
            return False, f"[{case_id}:{engine}:s{step_index}] duplicate neighbor in list for {k_str}: {obs_nbr_list}"

    degree_sum = 0
    for exp_vid in expected_graph.vertices:
        exp_nbrs = set(expected_graph.adj.get(exp_vid, {}).keys())
        str_vid = str(exp_vid)
        if str_vid not in obs_neighbors:
            return False, f"[{case_id}:{engine}:s{step_index}] vertex {exp_vid} missing from neighbors dict"
        obs_nbr_list = obs_neighbors[str_vid]
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
    """Validates codec state counts against expected values with strict integer checks (P3-R2-F10)."""
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
        if type(obs_val) is not int:
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
    """Validates per-step execution counters with strict integer types (P3-R2-F10)."""
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
        if type(val) is not int:
            return False, f"[{case_id}:{engine}:s{step_index}] step_counters field {f} is not an integer: {val} (type={type(val)})"
        if val < 0:
            return False, f"[{case_id}:{engine}:s{step_index}] step_counters field {f} is negative: {val}"

    if step_counters["max_work"] != max_work:
        return False, f"[{case_id}:{engine}:s{step_index}] max_work mismatch: obs={step_counters['max_work']} vs exp={max_work}"

    if is_success and step_counters["work_units"] > max_work:
        return False, f"[{case_id}:{engine}:s{step_index}] work_units {step_counters['work_units']} exceeded max_work {max_work} on SUCCESS step"

    return True, ""


def validate_native_output_schema(
    res_data: Dict[str, Any],
    expected_engine: str,
    expected_case_id: str,
    raw_input_path: Optional[Path] = None,
    workload: Optional[Dict[str, Any]] = None,
    is_success: bool = True,
) -> Tuple[bool, str]:
    """Multi-layered strict output schema and semantic invariant validator (P3-R3-F01, P3-R3-F04, P3-R3-F07)."""
    if not isinstance(res_data, dict):
        return False, "Output JSON root is not an object"

    # 1. Identity & Versions (P3-R4-F01)
    if "schema_version" not in res_data:
        return False, "Missing required field 'schema_version' in native output"
    if type(res_data["schema_version"]) is not str or res_data["schema_version"] != "1.1.0":
        return False, f"Invalid schema_version: {res_data.get('schema_version')} (expected string '1.1.0')"

    if "semantics_version" not in res_data:
        return False, "Missing required field 'semantics_version' in native output"
    if type(res_data["semantics_version"]) is not str or res_data["semantics_version"] != "D-R-1.0":
        return False, f"Invalid semantics_version: {res_data.get('semantics_version')} (expected string 'D-R-1.0')"

    if "engine_name" not in res_data or type(res_data["engine_name"]) is not str:
        return False, f"Missing or invalid engine_name: {res_data.get('engine_name')}"
    if res_data["engine_name"] != expected_engine or res_data.get("engine") != expected_engine:
        return False, f"Engine mismatch: {res_data.get('engine_name')} != {expected_engine}"

    if "case_id" not in res_data or type(res_data["case_id"]) is not str:
        return False, f"Missing or invalid case_id: {res_data.get('case_id')}"
    obs_case_id = res_data["case_id"]
    if is_success:
        if obs_case_id != expected_case_id:
            return False, f"Case ID mismatch on SUCCESS: obs={obs_case_id} vs exp={expected_case_id}"
    else:
        if expected_case_id and obs_case_id not in (expected_case_id, "UNKNOWN"):
            return False, f"Case ID mismatch on failure: obs={obs_case_id} vs exp={expected_case_id}"

    if "raw_input_sha256" not in res_data or type(res_data["raw_input_sha256"]) is not str:
        return False, "Missing or invalid 'raw_input_sha256' in native output"
    if raw_input_path and raw_input_path.is_file():
        exp_sha = compute_sha256(raw_input_path)
        obs_sha = res_data.get("raw_input_sha256")
        if obs_sha != exp_sha:
            return False, f"raw_input_sha256 mismatch: obs={obs_sha} vs exp={exp_sha}"

    # 2. Execution Lifecycle Invariants (P3-R4-F02)
    exec_info = res_data.get("execution")
    if not isinstance(exec_info, dict):
        return False, "Missing or invalid 'execution' summary object"
    for ef in ["total_steps", "attempted_steps", "completed_steps", "failed_steps", "unattempted_steps"]:
        if ef not in exec_info:
            return False, f"Execution summary missing required field '{ef}'"
        val = exec_info.get(ef)
        if type(val) is not int or isinstance(val, bool) or val < 0:
            return False, f"Execution summary field '{ef}' missing or not non-negative integer: {val} (type={type(val)})"

    total_steps = exec_info["total_steps"]
    attempted_steps = exec_info["attempted_steps"]
    completed_steps = exec_info["completed_steps"]
    failed_steps = exec_info["failed_steps"]
    unattempted_steps = exec_info["unattempted_steps"]

    if completed_steps + failed_steps != attempted_steps:
        return False, f"Lifecycle invariant violated: completed ({completed_steps}) + failed ({failed_steps}) != attempted ({attempted_steps})"
    if attempted_steps + unattempted_steps != total_steps:
        return False, f"Lifecycle invariant violated: attempted ({attempted_steps}) + unattempted ({unattempted_steps}) != total ({total_steps})"

    if workload and "updates" in workload:
        exp_total = len(workload["updates"])
        if total_steps != exp_total:
            return False, f"Execution total_steps mismatch: obs={total_steps} vs exp={exp_total}"

    root_status = res_data.get("status")
    if type(root_status) is not str:
        return False, f"Root status is not string: {root_status}"

    if is_success:
        if root_status != "SUCCESS":
            return False, f"Expected root status SUCCESS, got: {root_status}"
        if res_data.get("error_category") != "NONE":
            return False, f"Expected error_category NONE on SUCCESS, got: {res_data.get('error_category')}"
        if completed_steps != total_steps or failed_steps != 0 or unattempted_steps != 0 or attempted_steps != total_steps:
            return False, f"Inconsistent execution lifecycle on SUCCESS: completed={completed_steps}, failed={failed_steps}, unattempted={unattempted_steps}, attempted={attempted_steps}, total={total_steps}"
    else:
        if root_status == "SUCCESS":
            return False, "Expected failing status, got SUCCESS"
        if res_data.get("error_category") in (None, "", "NONE"):
            return False, f"Expected non-NONE error_category on failure, got: {res_data.get('error_category')}"
        if attempted_steps > 0 and failed_steps == 0:
            return False, f"Failing execution with attempted_steps={attempted_steps} must have failed_steps > 0"

    # 3. Steps Array Structural Integrity & Strict Typing (P3-R4-F02, P3-R4-F06)
    steps = res_data.get("steps")
    if not isinstance(steps, list):
        return False, "Missing or invalid 'steps' array"
    if is_success:
        if len(steps) != total_steps:
            return False, f"Steps array length mismatch on SUCCESS: obs={len(steps)} vs total_steps={total_steps}"
    else:
        if attempted_steps > 0 and len(steps) != total_steps:
            return False, f"Steps array length mismatch on step failure: obs={len(steps)} vs total_steps={total_steps}"

    workload_updates = workload.get("updates", []) if workload else []
    for s_idx, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, f"Step item {s_idx} is not an object: {step}"
        s_step_index = step.get("step_index")
        if type(s_step_index) is not int or isinstance(s_step_index, bool) or s_step_index != s_idx:
            return False, f"Step item {s_idx} has mismatched or non-integer step_index: {s_step_index} (type={type(s_step_index)})"

        step_stat = step.get("status")
        commit_stat = step.get("commit_status")
        err_cat = step.get("error_category")

        if is_success:
            if step_stat != "SUCCESS":
                return False, f"Root status is SUCCESS but step {s_idx} status is not SUCCESS: {step_stat}"
            if commit_stat != "COMMITTED":
                return False, f"Root status is SUCCESS but step {s_idx} commit_status is not COMMITTED: {commit_stat}"
            if err_cat != "NONE":
                return False, f"Root status is SUCCESS but step {s_idx} error_category is not NONE: {err_cat}"
        else:
            if s_idx < completed_steps:
                if step_stat != "SUCCESS":
                    return False, f"Prior step {s_idx} before failure must have status SUCCESS: {step_stat}"
                if commit_stat != "COMMITTED":
                    return False, f"Prior step {s_idx} before failure must have commit_status COMMITTED: {commit_stat}"
            elif s_idx == completed_steps:
                if step_stat == "SUCCESS":
                    return False, f"Failing step {s_idx} must not have status SUCCESS"
                if commit_stat != "NOT_COMMITTED":
                    return False, f"Failing step {s_idx} must have commit_status NOT_COMMITTED: {commit_stat}"
                if err_cat in (None, "", "NONE"):
                    return False, f"Failing step {s_idx} missing error_category: {err_cat}"
            else:
                if step_stat != "UNATTEMPTED":
                    return False, f"Future step {s_idx} after failure must have status UNATTEMPTED: {step_stat}"
                if commit_stat != "NOT_COMMITTED":
                    return False, f"Future step {s_idx} after failure must have commit_status NOT_COMMITTED: {commit_stat}"

        if s_idx < len(workload_updates) and step_stat != "UNATTEMPTED":
            upd = workload_updates[s_idx]
            exp_op = "+" if upd.get("op") in ("+", "INSERT", "INS") else "-"
            if step.get("op") != exp_op:
                return False, f"Step {s_idx} operation mismatch: obs={step.get('op')} vs exp={exp_op}"
            u_val = step.get("u")
            v_val = step.get("v")
            if type(u_val) is not int or isinstance(u_val, bool) or u_val < -9223372036854775808 or u_val > 9223372036854775807:
                return False, f"Step {s_idx} endpoint u not signed int64: {u_val} (type={type(u_val)})"
            if type(v_val) is not int or isinstance(v_val, bool) or v_val < -9223372036854775808 or v_val > 9223372036854775807:
                return False, f"Step {s_idx} endpoint v not signed int64: {v_val} (type={type(v_val)})"
            if u_val != upd.get("u") or v_val != upd.get("v"):
                return False, f"Step {s_idx} endpoint mismatch: obs=({u_val}, {v_val}) vs exp=({upd.get('u')}, {upd.get('v')})"

            obs_lbl = step.get("label")
            exp_lbl = upd.get("label")
            if type(obs_lbl) is not type(exp_lbl) or (isinstance(obs_lbl, bool) != isinstance(exp_lbl, bool)) or obs_lbl != exp_lbl:
                return False, f"Step {s_idx} label mismatch: obs={obs_lbl} ({type(obs_lbl)}) vs exp={exp_lbl} ({type(exp_lbl)})"

        # Event tuples strict validation (P3-R4-F02, P3-R4-F06)
        for event_type in ["entered_tuples", "exited_tuples"]:
            t_list = step.get(event_type)
            if not isinstance(t_list, list):
                return False, f"Step {s_idx} '{event_type}' is not a list: {type(t_list)}"
            seen_tuples = set()
            for t_idx, tup in enumerate(t_list):
                if not isinstance(tup, list):
                    return False, f"Step {s_idx} {event_type}[{t_idx}] is not a list: {tup}"
                if workload and "query" in workload:
                    exp_q_len = len(workload["query"].get("vertices", []))
                    if len(tup) != exp_q_len:
                        return False, f"Step {s_idx} {event_type}[{t_idx}] length mismatch: {len(tup)} != |V(Q)| ({exp_q_len})"
                for el_idx, el in enumerate(tup):
                    if type(el) is not int or isinstance(el, bool) or el < -9223372036854775808 or el > 9223372036854775807:
                        return False, f"Step {s_idx} {event_type}[{t_idx}][{el_idx}] not signed int64: {el} (type={type(el)})"
                if len(set(tup)) != len(tup):
                    return False, f"Step {s_idx} {event_type}[{t_idx}] non-injective (duplicate vertex in tuple): {tup}"
                t_tuple = tuple(tup)
                if t_tuple in seen_tuples:
                    return False, f"Step {s_idx} duplicate tuple in {event_type}: {tup}"
                seen_tuples.add(t_tuple)

    # 4. Query Metadata & Independent Cut Oracle (P3-R3-F01)
    if is_success and workload and "query" in workload:
        q_info = res_data.get("query_info")
        if not isinstance(q_info, dict):
            return False, "Missing or invalid 'query_info' object on SUCCESS"
        if q_info.get("k") != workload.get("k", 0):
            return False, f"query_info k mismatch: obs={q_info.get('k')} vs exp={workload.get('k', 0)}"

        q_v = workload["query"].get("vertices", [])
        q_vids = [v["id"] for v in q_v]
        expected_can_order = sorted(q_vids)
        obs_can_order = q_info.get("query_canonical_order")
        if obs_can_order != expected_can_order:
            return False, f"query_canonical_order mismatch: obs={obs_can_order} vs exp={expected_can_order}"

        q_e = workload["query"].get("edges", [])
        v_to_idx = {vid: i for i, vid in enumerate(expected_can_order)}
        mapped_edges = [(v_to_idx[e["u"]], v_to_idx[e["v"]]) for e in q_e if e["u"] in v_to_idx and e["v"] in v_to_idx]
        oracle_lambda = independent_edge_connectivity_cut_oracle(len(q_vids), mapped_edges)
        obs_lambda = q_info.get("computed_lambda")
        if obs_lambda != oracle_lambda:
            return False, f"computed_lambda mismatch with independent cut oracle: obs={obs_lambda} vs exp={oracle_lambda}"

    # 5. Emission Accounting (P3-R3-F01)
    if is_success:
        cnt = res_data.get("counters")
        if not isinstance(cnt, dict):
            return False, "Missing 'counters' object on SUCCESS"
        for ef in ["raw_emissions", "unique_emissions", "committed_emissions", "discarded_emissions"]:
            val = cnt.get(ef)
            if type(val) is not int or val < 0:
                return False, f"Emission counter '{ef}' invalid: {val} (type={type(val)})"
        if cnt["raw_emissions"] != cnt["unique_emissions"] + cnt["discarded_emissions"]:
            return False, f"Emission invariant violated: raw ({cnt['raw_emissions']}) != unique ({cnt['unique_emissions']}) + discarded ({cnt['discarded_emissions']})"
        total_committed_tuples = sum(len(s.get("entered_tuples", [])) + len(s.get("exited_tuples", [])) for s in steps)
        if total_committed_tuples != cnt["committed_emissions"]:
            return False, f"Committed emissions mismatch: step sum={total_committed_tuples} vs counter={cnt['committed_emissions']}"
        if cnt["discarded_emissions"] != 0:
            return False, f"Canonical run must have 0 discarded emissions (zero duplicates), got: {cnt['discarded_emissions']}"

    # 6. Work Budget Precedence & Authority (P3-R3-F07)
    wb = res_data.get("work_budget")
    if not isinstance(wb, dict):
        return False, "Missing 'work_budget' object in native output"
    for wbf in ["default_limit", "canonical_cap", "workload_requested_limit", "cli_requested_limit", "effective_limit"]:
        val = wb.get(wbf)
        if type(val) is not int or val <= 0:
            return False, f"work_budget field '{wbf}' invalid: {val} (type={type(val)})"
    if wb["default_limit"] != 500000 or wb["canonical_cap"] != 500000:
        return False, f"Canonical cap loosened: default={wb['default_limit']}, cap={wb['canonical_cap']}"
    if wb["effective_limit"] > 500000:
        return False, f"Effective limit exceeds canonical cap: {wb['effective_limit']} > 500000"
    exp_eff = min(wb["workload_requested_limit"], wb["cli_requested_limit"], wb["canonical_cap"])
    if wb["effective_limit"] != exp_eff:
        return False, f"Effective limit precedence violated: obs={wb['effective_limit']} vs exp={exp_eff}"

    return True, ""


def run_workload_on_binary(
    binary_path: Path,
    engine: str,
    workload: Dict[str, Any],
    max_work: Optional[int] = None,
    timeout: int = 30,
    address_space_mib: int = 512,
) -> Tuple[Dict[str, Any], float, bool]:
    """Runs a single workload JSON through rapidflow_apx_native with micro-containment and strict schema validation."""
    # Preflight micro-workload containment check (P3-R3-F08)
    bound_ok, bound_diag = validate_micro_workload_bounds(workload)
    if not bound_ok:
        return {
            "status": "CONTAINMENT_VIOLATION",
            "error_category": "CONTAINMENT_VIOLATION",
            "error_message": bound_diag,
            "exit_code": -1,
        }, 0.0, False

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
        json_parse_ok = False
        if not timed_out and output_file_exists and os.path.getsize(of.name) > 0:
            try:
                with open(of.name, "r", encoding="utf-8") as rf:
                    res_data = json.load(rf)
                json_parse_ok = isinstance(res_data, dict)
            except Exception as e:
                res_data = {"status": "ERROR", "error_category": "JSON_PARSE_ERROR", "error_message": f"Output parse error: {e}"}
                json_parse_ok = False

        if not res_data or not json_parse_ok:
            status = "TIMEOUT" if timed_out else "EXEC_ERROR"
            cat = "TIMEOUT" if timed_out else ("JSON_PARSE_ERROR" if output_file_exists and not json_parse_ok else "SUBPROCESS_ERROR")
            res_data = {"status": status, "error_category": cat, "error_message": err or out}

        res_data["exit_code"] = ret

        # Validate schema on both success and failure (P3-R3-F01, P3-R3-F04, P3-R4-F01, P3-R4-F03)
        is_success_run = (ret == 0 and not timed_out and res_data.get("status") == "SUCCESS")
        schema_ok = True
        schema_diag = ""

        if not timed_out and json_parse_ok:
            s_ok, s_diag = validate_native_output_schema(
                res_data, engine, workload.get("case_id", ""), wf_path, workload=workload, is_success=is_success_run
            )
            if not s_ok:
                schema_ok = False
                schema_diag = s_diag
                res_data["schema_error"] = s_diag
                if is_success_run:
                    res_data["status"] = "SCHEMA_ERROR"
                    res_data["error_message"] = s_diag
        else:
            schema_ok = False
            schema_diag = f"Execution failed before valid output JSON (timed_out={timed_out}, json_parse_ok={json_parse_ok})"
            res_data["schema_error"] = schema_diag

        res_data["schema_valid"] = schema_ok
        res_data["contract_valid"] = schema_ok

        try:
            os.remove(wf.name)
            os.remove(of.name)
        except OSError:
            pass

        final_ok = is_success_run and schema_ok
        return res_data, duration_ms, final_ok


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

    # Strict denominator integrity validation (P3-R3-F03, P3-R4-F04)
    val_hand_ok, val_hand_diag = validate_required_hand_cases(all_workloads, REQUIRED_HAND_CASES_PATH)
    if not val_hand_ok:
        return [{
            "case_id": "DENOMINATOR_INTEGRITY_CHECK",
            "name": "hand_cases_denominator_lock",
            "status": "FAIL",
            "diagnostic": f"Hand cases denominator integrity failure: {val_hand_diag}",
            "m1_executed": False,
            "m2_executed": False,
            "duration_ms_m1": None,
            "duration_ms_m2": None,
        }], False

    reg_map = {}
    if REQUIRED_HAND_CASES_PATH.is_file():
        try:
            with open(REQUIRED_HAND_CASES_PATH, "r", encoding="utf-8") as rf:
                reg_json = json.load(rf)
                reg_map = {c["case_id"]: c for c in reg_json.get("cases", []) if isinstance(c, dict) and "case_id" in c}
        except Exception:
            pass

    if case_filter:
        workloads = [w for w in all_workloads if w["case_id"] == case_filter]
    else:
        workloads = all_workloads

    results = []
    all_ok = True

    for w in workloads:
        cid = w["case_id"]
        req_entry = reg_map.get(cid)
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
            # Strict negative verification (P3-R2-F07, P3-R3-F04, P3-R4-F03): exact exit code, error category, schema, rollback
            exp_code = w.get("expected_exit_code")
            exp_cat = w.get("expected_error_category")

            engines_neg = []
            if run_m1:
                engines_neg.append(("m1", res_m1))
            if run_m2:
                engines_neg.append(("m2", res_m2))

            for eng_name, eng_res in engines_neg:
                if not eng_res.get("schema_valid", False):
                    case_ok = False
                    diag += f"{eng_name} schema validation failed on negative test: {eng_res.get('schema_error')}. "

                c_code = eng_res.get("exit_code")
                c_cat = eng_res.get("error_category")
                c_stat = eng_res.get("status")
                if c_code != exp_code or c_cat != exp_cat or c_stat == "SUCCESS":
                    case_ok = False
                    diag += f"{eng_name} negative contract failure: code={c_code} (exp {exp_code}), cat={c_cat} (exp {exp_cat}), stat={c_stat}. "

                if eng_res.get("schema_version") != "1.1.0":
                    case_ok = False
                    diag += f"{eng_name} invalid schema_version: {eng_res.get('schema_version')}. "
                if eng_res.get("semantics_version") != "D-R-1.0":
                    case_ok = False
                    diag += f"{eng_name} invalid semantics_version: {eng_res.get('semantics_version')}. "
                if eng_res.get("case_id") != cid:
                    case_ok = False
                    diag += f"{eng_name} case_id mismatch on negative test: obs={eng_res.get('case_id')} vs exp={cid}. "
                if eng_res.get("engine_name") != eng_name:
                    case_ok = False
                    diag += f"{eng_name} engine_name mismatch on negative test: obs={eng_res.get('engine_name')}. "

                # If the case failed at a step (e.g. CASE_HAND_22), check rollback and commit_status
                if cid == "CASE_HAND_22":
                    steps = eng_res.get("steps", [])
                    if len(steps) != 1:
                        case_ok = False
                        diag += f"{eng_name} CASE_HAND_22 steps length mismatch: {len(steps)} != 1. "
                    else:
                        s0 = steps[0]
                        if s0.get("commit_status") != "NOT_COMMITTED":
                            case_ok = False
                            diag += f"{eng_name} CASE_HAND_22 failing step not marked NOT_COMMITTED: {s0.get('commit_status')}. "
                        # Check graph state was not mutated (initial graph had 2 vertices, 0 edges)
                        g_state = s0.get("graph_state", {})
                        if g_state.get("vertices_count") != 2 or g_state.get("edges_count") != 0:
                            case_ok = False
                            diag += f"{eng_name} CASE_HAND_22 graph mutated despite rollback: v={g_state.get('vertices_count')}, e={g_state.get('edges_count')}. "
        else:
            if run_m1 and (not ok_m1 or not res_m1.get("schema_valid", False)):
                case_ok = False
                diag += f"M1 execution or schema failed: status={res_m1.get('status')}, err={res_m1.get('error_message')}, schema_err={res_m1.get('schema_error')}. "
            if run_m2 and (not ok_m2 or not res_m2.get("schema_valid", False)):
                case_ok = False
                diag += f"M2 execution or schema failed: status={res_m2.get('status')}, err={res_m2.get('error_message')}, schema_err={res_m2.get('schema_error')}. "

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

                        # Independent lock against required registry expected_step_events (P3-R4-F04)
                        if req_entry and req_entry.get("expected_step_events"):
                            exp_steps_ev = req_entry["expected_step_events"]
                            if s_idx < len(exp_steps_ev):
                                exp_step_ev = exp_steps_ev[s_idx]
                                if len(ent_obs) != exp_step_ev.get("entered_count", 0):
                                    case_ok = False
                                    diag += f"{eng_name} step {s_idx} entered count mismatch with locked registry: obs={len(ent_obs)} vs locked={exp_step_ev.get('entered_count')}. "
                                    break
                                if len(ext_obs) != exp_step_ev.get("exited_count", 0):
                                    case_ok = False
                                    diag += f"{eng_name} step {s_idx} exited count mismatch with locked registry: obs={len(ext_obs)} vs locked={exp_step_ev.get('exited_count')}. "
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
                    if ok_m1 and res_m1.get("schema_valid", False):
                        s0 = res_m1["steps"][0]
                        if sorted(s0.get("entered_tuples", [])) == exp_ent and sorted(s0.get("exited_tuples", [])) == exp_ext:
                            stats["m1_agreed"] += 1
                        if res_m1.get("counters", {}).get("discarded_emissions", 0) == 0:
                            stats["m1_zero_duplicates"] += 1

                # Execute Native M2 if selected
                if run_m2:
                    res_m2, _, ok_m2 = run_workload_on_binary(binary_path, "m2", workload)
                    if ok_m2 and res_m2.get("schema_valid", False):
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
    v_ok, v_diag = validate_stream_lock_registry(STREAM_56_LOCK_PATH)
    if not v_ok:
        return {"status": "FAIL", "error": f"Stream lock registry validation failed: {v_diag}"}, False

    with open(STREAM_56_LOCK_PATH, "r", encoding="utf-8") as f:
        lock_registry = json.load(f)

    lock_map = {item["case_id"]: item for item in lock_registry["streams"]}

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
            if lock_item.get("q_type") != q_type:
                stats["status"] = "FAIL"
                stats["error"] = f"Stream {cid} q_type mismatch in lock: obs={lock_item.get('q_type')} vs spec={q_type}"
                return stats, False
            if lock_item.get("k") != k:
                stats["status"] = "FAIL"
                stats["error"] = f"Stream {cid} k mismatch in lock: obs={lock_item.get('k')} vs spec={k}"
                return stats, False
            if lock_item.get("seed") != seed:
                stats["status"] = "FAIL"
                stats["error"] = f"Stream {cid} seed mismatch in lock: obs={lock_item.get('seed')} vs spec={seed}"
                return stats, False
            if lock_item.get("label_mode") != label_mode:
                stats["status"] = "FAIL"
                stats["error"] = f"Stream {cid} label_mode mismatch in lock: obs={lock_item.get('label_mode')} vs spec={label_mode}"
                return stats, False
            if lock_item.get("lambda") != lambda_val:
                stats["status"] = "FAIL"
                stats["error"] = f"Lambda mismatch for {cid}: locked {lock_item.get('lambda')} != spec {lambda_val}"
                return stats, False

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

            # Verify expected event counts and lambda match lock registry (P3-R2-F08, P3-R3-F05)
            if lock_item.get("lambda") != lambda_val:
                stats["status"] = "FAIL"
                stats["error"] = f"Lambda mismatch for {cid}: locked {lock_item.get('lambda')} != spec {lambda_val}"
                return stats, False
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

            if not ok_m1 or not ok_m2 or (run_m1 and not res_m1.get("schema_valid", False)) or (run_m2 and not res_m2.get("schema_valid", False)):
                stats["status"] = "FAIL"
                stats["error"] = f"Native run or schema failed for {cid}: M1_ok={ok_m1} (schema={res_m1.get('schema_valid')}), M2_ok={ok_m2} (schema={res_m2.get('schema_valid')})"
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
