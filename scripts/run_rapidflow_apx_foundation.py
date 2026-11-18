#!/usr/bin/env python3
"""
scripts/run_rapidflow_apx_foundation.py — Canonical runner and test executor for Phase P2 Native Foundation.

Enforces:
1. Fail-closed Git provenance verification (repo HEAD and pristine upstream commit 2ea4f4c...).
2. Preflight system resource checks (MemAvailable >= 2 GiB from /proc/meminfo, free disk >= 1 GiB).
3. Strict manifest schema and ceiling validation (max AS <= 512 MiB, timeout <= 30s, required canonical case registry).
4. Process containment with resource limits (RLIMIT_AS and process group SIGKILL).
5. Independent Python reference cross-validation with genuine scripts.dstar_oracle (M0 Oracle).
6. Atomic summary reporting with incremental checkpointing preserving partial failure artifacts.
7. Diagnostic isolation: --case writes to diagnostic_summary.json, never overwriting canonical report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_UPSTREAM_COMMIT = "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1"
CMAKE_PATH = REPO_ROOT / "external/tools/cmake-3.28.3-linux-x86_64/bin/cmake"
if not CMAKE_PATH.exists():
    CMAKE_PATH = Path(shutil.which("cmake") or "cmake")

# Add REPO_ROOT to sys.path to import genuine dstar_oracle
sys.path.insert(0, str(REPO_ROOT))
from scripts.dstar_oracle import (
    DStarGraph,
    enumerate_snapshot,
    DStarDynamicOracle,
    DStarUpdate,
    diagnose_mapping_s1_s2,
)

CANONICAL_REQUIRED_CASE_IDS = [
    "codec_boundaries",
    "lifecycle_stream",
    "transaction_lifecycle",
    "tuple_sink_invariants",
    "k4_exact_enumeration",
    "event_transition_stream",
    "diamond_k1_fixed",
    "irrelevant_update_stream",
]

REQUIRED_CASE_OBSERVATIONS = {
    "codec_boundaries": [
        ("query_vertices_count", int),
        ("data_vertices_count", int),
        ("query_vertex_ids", list),
        ("data_vertex_ids", list),
        ("vertex_labels_count", int),
        ("edge_labels_count", int),
        ("status", str),
        ("diagnostic", str),
    ],
    "lifecycle_stream": [
        ("final_vertices_count", int),
        ("final_edges_count", int),
        ("mutations_executed", list),
        ("status", str),
        ("diagnostic", str),
    ],
    "transaction_lifecycle": [
        ("steps_executed", list),
        ("status", str),
        ("diagnostic", str),
    ],
    "tuple_sink_invariants": [
        ("raw_emissions", int),
        ("unique_emissions", int),
        ("committed_emissions", int),
        ("discarded_emissions", int),
        ("status", str),
        ("diagnostic", str),
    ],
    "k4_exact_enumeration": [
        ("tuple_count", int),
        ("raw_emissions", int),
        ("unique_emissions", int),
        ("tuples", list),
        ("status", str),
        ("diagnostic", str),
    ],
    "event_transition_stream": [
        ("events", list),
        ("status", str),
        ("diagnostic", str),
    ],
    "diamond_k1_fixed": [
        ("matched_edges", int),
        ("missing_edges", int),
        ("computed_lambda", int),
        ("s2_spanning_connected", bool),
        ("is_valid", bool),
        ("status", str),
        ("diagnostic", str),
    ],
    "irrelevant_update_stream": [
        ("published_tuples_count", int),
        ("edge_recorded", bool),
        ("status", str),
        ("diagnostic", str),
    ],
}


class ProvenanceError(Exception):
    """Raised when repository or upstream provenance fails validation."""
    pass


class ResourceLimitError(Exception):
    """Raised when system resources fail preflight limits."""
    pass


class ManifestValidationError(Exception):
    """Raised when test manifest fails schema or safety validation."""
    pass


# ==============================================================================
# Provenance Verification
# ==============================================================================

def get_git_commit(cwd: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception as e:
        raise ProvenanceError(f"Failed to obtain git commit in {cwd}: {e}") from e


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_git_status_clean(cwd: Path, ignore_reports: bool = False) -> Tuple[bool, str, List[str]]:
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        all_lines = [line for line in res.stdout.splitlines() if line.strip()]
        if ignore_reports:
            filtered_lines = [line for line in all_lines if "reports/" not in line]
        else:
            filtered_lines = all_lines
        out = "\n".join(filtered_lines).strip()
        return len(out) == 0, out, all_lines
    except Exception as e:
        raise ProvenanceError(f"Failed to check git status in {cwd}: {e}") from e


def get_tool_versions() -> Dict[str, str]:
    versions = {}
    try:
        cmake_res = subprocess.run([str(CMAKE_PATH), "--version"], stdout=subprocess.PIPE, text=True, check=True)
        versions["cmake"] = cmake_res.stdout.splitlines()[0]
    except Exception:
        versions["cmake"] = "UNKNOWN"

    try:
        cxx_res = subprocess.run(["c++", "--version"], stdout=subprocess.PIPE, text=True, check=True)
        versions["cxx"] = cxx_res.stdout.splitlines()[0]
    except Exception:
        versions["cxx"] = "UNKNOWN"

    versions["python"] = sys.version.replace("\n", " ")
    return versions


def collect_source_hashes() -> Dict[str, str]:
    tracked_sources = [
        "extensions/rapidflow_apx/include/rapidflow_apx/typed_codec.h",
        "extensions/rapidflow_apx/include/rapidflow_apx/authoritative_graph.h",
        "extensions/rapidflow_apx/include/rapidflow_apx/transaction_manager.h",
        "extensions/rapidflow_apx/include/rapidflow_apx/tuple_sink.h",
        "extensions/rapidflow_apx/include/rapidflow_apx/micro_matcher.h",
        "extensions/rapidflow_apx/src/typed_codec.cpp",
        "extensions/rapidflow_apx/src/authoritative_graph.cpp",
        "extensions/rapidflow_apx/src/transaction_manager.cpp",
        "extensions/rapidflow_apx/src/tuple_sink.cpp",
        "extensions/rapidflow_apx/src/micro_matcher.cpp",
        "tests/native/rapidflow_apx_foundation/src/main.cpp",
        "tests/native/rapidflow_apx_foundation/src/harness_cases.h",
        "tests/native/rapidflow_apx_foundation/src/harness_cases.cpp",
        "tests/native/rapidflow_apx_foundation/src/json_mini.h",
        "tests/native/rapidflow_apx_foundation/CMakeLists.txt",
        "extensions/rapidflow_apx/CMakeLists.txt",
        "scripts/run_rapidflow_apx_foundation.py",
        "scripts/dstar_oracle.py",
    ]
    hashes = {}
    for rel_path in tracked_sources:
        p = REPO_ROOT / rel_path
        if p.exists():
            hashes[rel_path] = compute_sha256(p)
    return hashes


def verify_provenance(require_clean_repo: bool = False) -> Dict[str, Any]:
    repo_commit = get_git_commit(REPO_ROOT)
    if not repo_commit or "UNKNOWN" in repo_commit:
        raise ProvenanceError("Repository commit cannot be verified")

    upstream_dir = REPO_ROOT / "baselines/rapidflow/source"
    if not upstream_dir.exists():
        raise ProvenanceError(f"Upstream RapidFlow directory not found: {upstream_dir}")

    upstream_commit = get_git_commit(upstream_dir)
    if upstream_commit != EXPECTED_UPSTREAM_COMMIT:
        raise ProvenanceError(
            f"Upstream commit mismatch: expected {EXPECTED_UPSTREAM_COMMIT}, got {upstream_commit}"
        )

    upstream_clean, upstream_status, _ = check_git_status_clean(upstream_dir, ignore_reports=False)
    if not upstream_clean:
        raise ProvenanceError(f"Upstream RapidFlow directory has uncommitted modifications; must be pristine:\n{upstream_status}")

    repo_clean_strict, repo_status_strict, all_modified = check_git_status_clean(REPO_ROOT, ignore_reports=False)
    repo_clean_code, repo_status_code, _ = check_git_status_clean(REPO_ROOT, ignore_reports=True)

    if require_clean_repo and not repo_clean_code:
        raise ProvenanceError(f"Repository working tree has uncommitted code modifications during canonical verification:\n{repo_status_code}")

    dirty_state = "CLEAN" if repo_clean_strict else ("DIRTY_REPORTS_ONLY" if repo_clean_code else "DIRTY")

    return {
        "repo_commit": repo_commit,
        "repo_clean": repo_clean_code,
        "dirty_state": dirty_state,
        "modified_paths": all_modified,
        "upstream_commit": upstream_commit,
        "upstream_pristine": True,
        "tools": get_tool_versions(),
        "source_hashes": collect_source_hashes(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


# ==============================================================================
# Host System Resources Preflight
# ==============================================================================

def check_system_resources() -> Dict[str, Any]:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        raise ResourceLimitError("/proc/meminfo not accessible to verify host RAM")

    mem_avail_kb: Optional[int] = None
    with open(meminfo_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2:
                    mem_avail_kb = int(parts[1])
                break

    if mem_avail_kb is None:
        raise ResourceLimitError("Could not parse MemAvailable from /proc/meminfo")

    mem_avail_gib = mem_avail_kb / (1024 * 1024)
    if mem_avail_gib < 2.0:
        raise ResourceLimitError(
            f"Insufficient available RAM: {mem_avail_gib:.2f} GiB available < 2.0 GiB required"
        )

    disk_stat = shutil.disk_usage(REPO_ROOT)
    disk_free_gib = disk_stat.free / (1024 * 1024 * 1024)
    if disk_free_gib < 1.0:
        raise ResourceLimitError(
            f"Insufficient free disk space: {disk_free_gib:.2f} GiB free < 1.0 GiB required"
        )

    return {
        "available_ram_gib": round(mem_avail_gib, 2),
        "free_disk_gib": round(disk_free_gib, 2),
    }


# ==============================================================================
# Manifest Validation
# ==============================================================================

def validate_manifest(manifest_path: Path, is_canonical: bool = True) -> Dict[str, Any]:
    if not manifest_path.exists():
        raise ManifestValidationError(f"Manifest not found at {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as e:
            raise ManifestValidationError(f"Failed to parse manifest JSON: {e}") from e

    if not isinstance(data, dict):
        raise ManifestValidationError("Manifest root must be an object")

    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) == 0:
        raise ManifestValidationError("Manifest 'cases' must be a non-empty list")

    seen_ids = set()
    for c in cases:
        if not isinstance(c, dict):
            raise ManifestValidationError("Case definition must be an object")
        cid = c.get("case_id")
        if not isinstance(cid, str) or not cid:
            raise ManifestValidationError("Case missing valid 'case_id'")
        if cid in seen_ids:
            raise ManifestValidationError(f"Duplicate case_id in manifest: {cid}")
        seen_ids.add(cid)

    for c in cases:
        cid = c["case_id"]
        # Enforce ceiling limits and valid positive types
        limits = c.get("limits")
        if not isinstance(limits, dict):
            raise ManifestValidationError(f"Case {cid} missing 'limits' object")
        timeout = limits.get("timeout_seconds")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ManifestValidationError(f"Case {cid} timeout_seconds must be a positive number")
        if timeout > 30.0:
            raise ManifestValidationError(f"Case {cid} timeout {timeout}s exceeds ceiling of 30s")
        as_mib = limits.get("max_address_space_mib")
        if not isinstance(as_mib, int) or isinstance(as_mib, bool) or as_mib <= 0:
            raise ManifestValidationError(f"Case {cid} max_address_space_mib must be a positive integer")
        if as_mib > 512:
            raise ManifestValidationError(f"Case {cid} address space {as_mib} MiB exceeds ceiling of 512 MiB")

        # Enforce fixture existence and fixture hashes
        fix_path_str = c.get("fixture_path")
        if not isinstance(fix_path_str, str) or not fix_path_str:
            raise ManifestValidationError(f"Case {cid} missing valid 'fixture_path'")
        fixture_path = REPO_ROOT / fix_path_str
        if not fixture_path.exists():
            raise ManifestValidationError(f"Case '{cid}' declared fixture path does not exist: {fixture_path}")

        expected_sha = c.get("fixture_sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ManifestValidationError(f"Case '{cid}' missing valid 64-char hex 'fixture_sha256'")
        actual_sha = compute_sha256(fixture_path)
        if actual_sha != expected_sha:
            raise ManifestValidationError(f"Case '{cid}' fixture SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")

    # For canonical run, manifest MUST contain ALL canonical required cases
    if is_canonical:
        for req_id in CANONICAL_REQUIRED_CASE_IDS:
            if req_id not in seen_ids:
                raise ManifestValidationError(f"Manifest missing canonical required case: '{req_id}'")

    return data


# ==============================================================================
# Build Native Harness with Containment
# ==============================================================================

def build_native_harness(clean: bool = False) -> Tuple[Path, str]:
    build_dir = REPO_ROOT / "tests/native/rapidflow_apx_foundation/build"
    harness_binary = build_dir / "rapidflow_apx_foundation_harness"

    if clean and build_dir.exists():
        shutil.rmtree(build_dir)

    build_dir.mkdir(parents=True, exist_ok=True)
    build_as_bytes = 1024 * 1024 * 1024  # 1024 MiB limit for build

    def preexec_build():
        try:
            resource.setrlimit(resource.RLIMIT_AS, (build_as_bytes, build_as_bytes))
        except Exception as e:
            sys.stderr.write(f"FATAL: Failed to enforce build RLIMIT_AS: {e}\n")
            sys.stderr.flush()
            os._exit(127)
        os.setpgrp()

    cmake_args = [
        str(CMAKE_PATH),
        "..",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    proc_cmake = subprocess.Popen(
        cmake_args,
        cwd=str(build_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=preexec_build,
    )
    try:
        out_cmake, err_cmake = proc_cmake.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc_cmake.pid), signal.SIGKILL)
        except Exception:
            pass
        proc_cmake.communicate()
        raise RuntimeError("CMake configuration timed out after 120s")

    if proc_cmake.returncode != 0:
        raise RuntimeError(f"CMake configuration failed:\n{err_cmake}\n{out_cmake}")

    make_args = ["make", "-j2"]
    proc_make = subprocess.Popen(
        make_args,
        cwd=str(build_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=preexec_build,
    )
    try:
        out_make, err_make = proc_make.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc_make.pid), signal.SIGKILL)
        except Exception:
            pass
        proc_make.communicate()
        raise RuntimeError("Make build timed out after 120s")

    if proc_make.returncode != 0:
        raise RuntimeError(f"Make build failed:\n{err_make}\n{out_make}")

    if not harness_binary.exists():
        raise FileNotFoundError(f"Native harness binary was not created at {harness_binary}")

    binary_sha = compute_sha256(harness_binary)
    return harness_binary, binary_sha


# ==============================================================================
# Independent Python Reference Models & Genuine M0 Oracle
# ==============================================================================

def simulate_lifecycle_stream(fixture_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Independent pure-Python reference model that simulates graph lifecycle mutations
    (baseline CSR, dynamic overlay additions, deletions, tombstones, and label precedence).
    Computes exact expected topological and label state after each mutation.
    """
    base_v = {v["id"]: v["label"] for v in fixture_data["baseline"]["vertices"]}
    base_edges = {}
    for e in fixture_data["baseline"]["edges"]:
        u, v = min(e["u"], e["v"]), max(e["u"], e["v"])
        base_edges[(u, v)] = e["label"]

    tombstones = set()
    overlay = {}

    expected_records = []
    for i, m in enumerate(fixture_data["mutations"]):
        op = m["op"]
        u, v = m["u"], m["v"]
        lbl = m["label"]
        key = (min(u, v), max(u, v))

        # Determine operation type from semantics
        is_delete = op.startswith("delete")
        if is_delete:
            if key in overlay:
                del overlay[key]
            if key in base_edges:
                tombstones.add(key)
        else:
            if key in tombstones:
                tombstones.remove(key)
            overlay[key] = lbl

        # Authoritative edge view calculation
        current_edges = {}
        for (bu, bv), blbl in base_edges.items():
            if (bu, bv) not in tombstones and (bu, bv) not in overlay:
                current_edges[(bu, bv)] = blbl
        for (ou, ov), olbl in overlay.items():
            current_edges[(ou, ov)] = olbl

        has_uv = key in current_edges
        edge_lbl = current_edges.get(key, -1)
        in_tomb = key in tombstones
        in_over = key in overlay

        # Degrees and sorted unique neighbor lists
        neighbors = {str(vx): [] for vx in sorted(base_v.keys())}
        for (eu, ev) in current_edges:
            neighbors[str(eu)].append(ev)
            neighbors[str(ev)].append(eu)
        for vx in neighbors:
            neighbors[vx] = sorted(list(set(neighbors[vx])))
        degrees = {vx: len(neighbors[vx]) for vx in neighbors}

        sorted_edges = [
            {"label": current_edges[(eu, ev)], "u": eu, "v": ev}
            for (eu, ev) in sorted(current_edges.keys())
        ]

        expected_records.append({
            "step_index": i,
            "op": op,
            "u": u,
            "v": v,
            "edges_count": len(current_edges),
            "vertices_count": len(base_v),
            "has_edge": has_uv,
            "edge_label": edge_lbl,
            "in_tombstones": in_tomb,
            "in_overlay": in_over,
            "edges": sorted_edges,
            "neighbors": neighbors,
            "degrees": degrees,
        })

    return expected_records


def simulate_transaction_stream(fixture_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Independent pure-Python reference model that simulates graph transactions
    step by step and tracks expected edges, degrees, and statuses.
    Independently derives prevalidation, fault-injection rollback, and committed states.
    """
    base_v = {v["id"]: v["label"]["val"] for v in fixture_data["baseline"]["vertices"]}
    committed_edges = {}
    for e in fixture_data["baseline"]["edges"]:
        u, v = min(e["u"], e["v"]), max(e["u"], e["v"])
        committed_edges[(u, v)] = e["label"]["val"]

    # Codec tracker: external data vertex IDs
    data_vertices = set(base_v.keys())

    expected_steps = []
    for s in fixture_data["steps"]:
        step_idx = s["step_index"]
        op = s["op"]
        u, v = s["u"], s["v"]
        lbl = s["label"]["val"]
        key = (min(u, v), max(u, v))
        fault = s["fault"]
        expected_status_decl = s["expected_status"]

        # 1. Independent Prevalidation
        status = "SUCCESS"
        if u == v:
            status = "PREVALIDATION_FAILED"
        elif op == "+":
            if key in committed_edges:
                status = "PREVALIDATION_FAILED"
        elif op == "-":
            if u not in data_vertices or v not in data_vertices:
                status = "PREVALIDATION_FAILED"
            elif key not in committed_edges:
                status = "PREVALIDATION_FAILED"
            elif committed_edges[key] != lbl:
                status = "PREVALIDATION_FAILED"

        # 2. If prevalidation passed, check fault injection
        effective_edges = dict(committed_edges)
        effective_data_vertices = set(data_vertices)

        if status == "SUCCESS":
            # Apply temporary mutation
            temp_edges = dict(committed_edges)
            temp_vertices = set(data_vertices)
            if op == "+":
                temp_edges[key] = lbl
                temp_vertices.add(u)
                temp_vertices.add(v)
            elif op == "-":
                del temp_edges[key]

            if fault in ("AFTER_MUTATION", "DURING_CALLBACK", "BEFORE_PUBLISH"):
                status = "FAULT_INJECTED"
                # Rollback! State remains committed_edges
                effective_edges = dict(committed_edges)
                effective_data_vertices = set(data_vertices)
            else:
                # Commit!
                committed_edges = temp_edges
                data_vertices = temp_vertices
                effective_edges = committed_edges
                effective_data_vertices = data_vertices

        # Check against expectation declared in fixture
        if status != expected_status_decl:
            raise ValueError(
                f"Step {step_idx}: model computed status '{status}' does not match fixture '{expected_status_decl}'"
            )

        u_exists = u in effective_data_vertices
        v_exists = v in effective_data_vertices
        has_uv = key in effective_edges

        # Build neighbors and degrees for all data vertices
        neighbors = {str(vx): [] for vx in sorted(effective_data_vertices)}
        for (eu, ev) in effective_edges:
            if str(eu) in neighbors:
                neighbors[str(eu)].append(ev)
            if str(ev) in neighbors:
                neighbors[str(ev)].append(eu)
        for vx in neighbors:
            neighbors[vx] = sorted(list(set(neighbors[vx])))
        degrees = {vx: len(neighbors[vx]) for vx in neighbors}

        deg_u = degrees.get(str(u), 0)
        deg_v = degrees.get(str(v), 0)

        sorted_edges = [
            {"label": {"type": "int", "val": effective_edges[(eu, ev)]}, "u": eu, "v": ev}
            for (eu, ev) in sorted(effective_edges.keys())
        ]

        expected_steps.append({
            "step_index": step_idx,
            "status": status,
            "op": op,
            "u": u,
            "v": v,
            "current_edges_count": len(effective_edges),
            "current_vertices_count": len(effective_data_vertices),
            "edges_count": len(effective_edges),
            "vertices_count": len(effective_data_vertices),
            "vertex_u_exists": u_exists,
            "vertex_v_exists": v_exists,
            "degree_u": deg_u,
            "degree_v": deg_v,
            "has_edge_uv": has_uv,
            "edges": sorted_edges,
            "neighbors": neighbors,
            "degrees": degrees,
            "codec_data_vertices_count": len(effective_data_vertices),
        })

    return expected_steps


def validate_case_observations(
    case_id: str,
    fixture_data: Dict[str, Any],
    native_report: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Validates observation schema, value boundaries, and nested observation structures
    with strict type discrimination (rejecting bool for int).
    """
    # 1. Top-level observations check
    req_obs = REQUIRED_CASE_OBSERVATIONS.get(case_id, [])
    for field_name, expected_type in req_obs:
        if field_name not in native_report:
            return False, f"Case '{case_id}': Missing required observation field '{field_name}' in native report"
        val = native_report[field_name]
        if expected_type is int:
            if isinstance(val, bool) or not isinstance(val, int):
                return False, f"Case '{case_id}': observation '{field_name}' must be integer, got {type(val).__name__}"
            if val < 0:
                return False, f"Case '{case_id}': observation '{field_name}' must be non-negative, got {val}"
        elif expected_type is bool:
            if not isinstance(val, bool):
                return False, f"Case '{case_id}': observation '{field_name}' must be boolean, got {type(val).__name__}"
        elif expected_type is list:
            if not isinstance(val, list):
                return False, f"Case '{case_id}': observation '{field_name}' must be list, got {type(val).__name__}"
        elif expected_type is dict:
            if not isinstance(val, dict):
                return False, f"Case '{case_id}': observation '{field_name}' must be dict, got {type(val).__name__}"
        elif not isinstance(val, expected_type):
            return False, f"Case '{case_id}': observation '{field_name}' must be {expected_type.__name__}, got {type(val).__name__}"

    # 2. Nested observations and domain-specific structure checks
    if case_id == "codec_boundaries":
        q_ids = native_report.get("query_vertex_ids", [])
        if any(isinstance(x, bool) or not isinstance(x, int) for x in q_ids):
            return False, f"Case '{case_id}': query_vertex_ids must be a list of integers"
        d_ids = native_report.get("data_vertex_ids", [])
        if any(isinstance(x, bool) or not isinstance(x, int) for x in d_ids):
            return False, f"Case '{case_id}': data_vertex_ids must be a list of integers"

    elif case_id == "lifecycle_stream":
        muts = native_report.get("mutations_executed", [])
        exp_len = len(fixture_data.get("mutations", []))
        if len(muts) != exp_len:
            return False, f"Case '{case_id}': mutations_executed count {len(muts)} != expected {exp_len}"

        req_mut_fields = [
            ("step_index", int),
            ("op", str),
            ("u", int),
            ("v", int),
            ("edges_count", int),
            ("vertices_count", int),
            ("has_edge", bool),
            ("edge_label", int),
            ("in_tombstones", bool),
            ("in_overlay", bool),
            ("edges", list),
            ("neighbors", dict),
            ("degrees", dict),
        ]
        for i, m in enumerate(muts):
            if not isinstance(m, dict):
                return False, f"Case '{case_id}' step {i}: mutation record must be an object"
            for f_name, f_type in req_mut_fields:
                if f_name not in m:
                    return False, f"Case '{case_id}' step {i}: missing required nested observation '{f_name}'"
                f_val = m[f_name]
                if f_type is int:
                    if isinstance(f_val, bool) or not isinstance(f_val, int):
                        return False, f"Case '{case_id}' step {i}: field '{f_name}' must be integer, got {type(f_val).__name__}"
                elif f_type is bool:
                    if not isinstance(f_val, bool):
                        return False, f"Case '{case_id}' step {i}: field '{f_name}' must be boolean, got {type(f_val).__name__}"
                elif not isinstance(f_val, f_type):
                    return False, f"Case '{case_id}' step {i}: field '{f_name}' must be {f_type.__name__}, got {type(f_val).__name__}"

            if m["edges_count"] < 0:
                return False, f"Case '{case_id}' step {i}: edges_count must be non-negative, got {m['edges_count']}"
            if m["vertices_count"] < 0:
                return False, f"Case '{case_id}' step {i}: vertices_count must be non-negative, got {m['vertices_count']}"

    elif case_id == "transaction_lifecycle":
        steps = native_report.get("steps_executed", [])
        exp_len = len(fixture_data.get("steps", []))
        if len(steps) != exp_len:
            return False, f"Case '{case_id}': steps_executed count {len(steps)} != expected {exp_len}"

        req_step_fields = [
            ("step_index", int),
            ("status", str),
            ("op", str),
            ("u", int),
            ("v", int),
            ("current_edges_count", int),
            ("current_vertices_count", int),
            ("vertex_u_exists", bool),
            ("vertex_v_exists", bool),
            ("degree_u", int),
            ("degree_v", int),
            ("has_edge_uv", bool),
            ("edges", list),
            ("neighbors", dict),
            ("degrees", dict),
            ("codec_state", dict),
            ("sink_counters", dict),
            ("published_tuples", list),
        ]
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                return False, f"Case '{case_id}' step {i}: step record must be an object"
            for f_name, f_type in req_step_fields:
                if f_name not in s:
                    return False, f"Case '{case_id}' step {i}: missing required nested observation '{f_name}'"
                f_val = s[f_name]
                if f_type is int:
                    if isinstance(f_val, bool) or not isinstance(f_val, int):
                        return False, f"Case '{case_id}' step {i}: field '{f_name}' must be integer, got {type(f_val).__name__}"
                elif f_type is bool:
                    if not isinstance(f_val, bool):
                        return False, f"Case '{case_id}' step {i}: field '{f_name}' must be boolean, got {type(f_val).__name__}"
                elif not isinstance(f_val, f_type):
                    return False, f"Case '{case_id}' step {i}: field '{f_name}' must be {f_type.__name__}, got {type(f_val).__name__}"

            if s["current_edges_count"] < 0:
                return False, f"Case '{case_id}' step {i}: current_edges_count must be non-negative, got {s['current_edges_count']}"
            if s["current_vertices_count"] < 0:
                return False, f"Case '{case_id}' step {i}: current_vertices_count must be non-negative, got {s['current_vertices_count']}"
            if s["degree_u"] < 0:
                return False, f"Case '{case_id}' step {i}: degree_u must be non-negative, got {s['degree_u']}"
            if s["degree_v"] < 0:
                return False, f"Case '{case_id}' step {i}: degree_v must be non-negative, got {s['degree_v']}"

    elif case_id == "tuple_sink_invariants":
        raw = native_report["raw_emissions"]
        uniq = native_report["unique_emissions"]
        comm = native_report["committed_emissions"]
        disc = native_report["discarded_emissions"]
        if raw < uniq:
            return False, f"Case '{case_id}': raw_emissions ({raw}) < unique_emissions ({uniq})"
        if comm + disc != uniq:
            return False, f"Case '{case_id}': committed ({comm}) + discarded ({disc}) != unique ({uniq})"

    elif case_id == "k4_exact_enumeration":
        tup_count = native_report["tuple_count"]
        tups = native_report["tuples"]
        if tup_count != len(tups):
            return False, f"Case '{case_id}': tuple_count ({tup_count}) != len(tuples) ({len(tups)})"

    elif case_id == "event_transition_stream":
        evs = native_report["events"]
        for j, ev in enumerate(evs):
            if not isinstance(ev, dict):
                return False, f"Case '{case_id}' event {j}: event record must be an object"
            for f_name, f_type in [("step", int), ("op", str), ("u", int), ("v", int), ("event_type", str), ("published_count", int), ("tuples", list)]:
                if f_name not in ev:
                    return False, f"Case '{case_id}' event {j}: missing nested field '{f_name}'"
                f_val = ev[f_name]
                if f_type is int and (isinstance(f_val, bool) or not isinstance(f_val, int)):
                    return False, f"Case '{case_id}' event {j}: field '{f_name}' must be int, got {type(f_val).__name__}"
            if ev["event_type"] not in ("ENTERED", "EXITED"):
                return False, f"Case '{case_id}' event {j}: unknown event_type '{ev['event_type']}'"
            if ev["published_count"] != len(ev["tuples"]):
                return False, f"Case '{case_id}' event {j}: published_count ({ev['published_count']}) != len(tuples) ({len(ev['tuples'])})"

    return True, "Observation schema and structure verified"


def verify_case_with_oracle(
    case_id: str,
    fixture_data: Dict[str, Any],
    native_report: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Cross-checks native execution against genuine Python reference models
    and scripts.dstar_oracle (M0 Oracle).
    """
    if case_id == "codec_boundaries":
        exp_q = len(fixture_data["external_query_vertices"])
        exp_d = len(fixture_data["external_data_vertices"])
        act_q = native_report.get("query_vertices_count")
        act_d = native_report.get("data_vertices_count")
        if act_q != exp_q:
            return False, f"Case '{case_id}' query_vertices_count mismatch: expected {exp_q}, got {act_q}"
        if act_d != exp_d:
            return False, f"Case '{case_id}' data_vertices_count mismatch: expected {exp_d}, got {act_d}"

        exp_q_ids = sorted([v["id"] for v in fixture_data["external_query_vertices"]])
        exp_d_ids = sorted([v["id"] for v in fixture_data["external_data_vertices"]])
        act_q_ids = sorted(native_report.get("query_vertex_ids", []))
        act_d_ids = sorted(native_report.get("data_vertex_ids", []))
        if act_q_ids != exp_q_ids:
            return False, f"Case '{case_id}' query_vertex_ids mismatch: expected {exp_q_ids}, got {act_q_ids}"
        if act_d_ids != exp_d_ids:
            return False, f"Case '{case_id}' data_vertex_ids mismatch: expected {exp_d_ids}, got {act_d_ids}"

        return True, "Codec boundaries verified against fixture metadata and roundtrip checks"

    if case_id == "k4_exact_enumeration":
        Q = DStarGraph()
        for v in fixture_data["query"]["vertices"]:
            Q.add_vertex(v["id"], v["label"]["val"])
        for e in fixture_data["query"]["edges"]:
            Q.add_edge(e["u"], e["v"], e["label"]["val"])

        G = DStarGraph()
        for v in fixture_data["data"]["vertices"]:
            G.add_vertex(v["id"], v["label"]["val"])
        for e in fixture_data["data"]["edges"]:
            G.add_edge(e["u"], e["v"], e["label"]["val"])

        oracle_tuples_raw = enumerate_snapshot(Q, G, k=0)
        oracle_tuples = sorted([list(t) for t in oracle_tuples_raw])

        actual_tuples = native_report.get("tuples", [])
        actual_tuples_sorted = sorted(actual_tuples)

        if len(actual_tuples) != 24 or len(oracle_tuples) != 24:
            return False, f"Tuple count mismatch: oracle={len(oracle_tuples)}, actual={len(actual_tuples)}"

        if actual_tuples_sorted != oracle_tuples:
            return False, "Bit-for-bit tuple mismatch between native K4 and genuine M0 Oracle"

        return True, "Bit-for-bit match with genuine scripts.dstar_oracle (24 unique ordered tuples)"

    if case_id == "event_transition_stream":
        Q = DStarGraph()
        for v in fixture_data["query"]["vertices"]:
            Q.add_vertex(v["id"], v["label"]["val"])
        for e in fixture_data["query"]["edges"]:
            Q.add_edge(e["u"], e["v"], e["label"]["val"])

        G = DStarGraph()
        for v in fixture_data["baseline_data"]["vertices"]:
            G.add_vertex(v["id"], v["label"]["val"])
        for e in fixture_data["baseline_data"]["edges"]:
            G.add_edge(e["u"], e["v"], e["label"]["val"])

        oracle = DStarDynamicOracle(Q, G, k=0)
        native_events = native_report.get("events", [])
        if len(native_events) != 2:
            return False, f"Expected 2 dynamic events, got {len(native_events)}"

        ev1 = native_events[0]
        if ev1.get("event_type") != "ENTERED" or ev1.get("op") != "+":
            return False, f"Step 1 event type/op mismatch: expected ENTERED/+, got {ev1.get('event_type')}/{ev1.get('op')}"

        ev2 = native_events[1]
        if ev2.get("event_type") != "EXITED" or ev2.get("op") != "-":
            return False, f"Step 2 event type/op mismatch: expected EXITED/-, got {ev2.get('event_type')}/{ev2.get('op')}"

        # Step 1: INSERT (30, 40, 0) -> ENTERED 24 tuples
        delta1 = oracle.apply_update(DStarUpdate("INSERT", 30, 40, 0))
        if len(delta1.entered) != 24 or len(delta1.exited) != 0:
            return False, f"Oracle step 1 delta mismatch: entered={len(delta1.entered)}, exited={len(delta1.exited)}"

        exp_entered = sorted([list(t) for t in delta1.entered])
        act_entered = sorted(ev1.get("tuples", []))
        if act_entered != exp_entered:
            return False, "Step 1 ENTERED tuples mismatch with DStarDynamicOracle"

        # Step 2: DELETE (30, 40, 0) -> EXITED 24 tuples
        delta2 = oracle.apply_update(DStarUpdate("DELETE", 30, 40, 0))
        if len(delta2.entered) != 0 or len(delta2.exited) != 24:
            return False, f"Oracle step 2 delta mismatch: entered={len(delta2.entered)}, exited={len(delta2.exited)}"

        exp_exited = sorted([list(t) for t in delta2.exited])
        act_exited = sorted(ev2.get("tuples", []))
        if act_exited != exp_exited:
            return False, "Step 2 EXITED tuples mismatch with DStarDynamicOracle"

        return True, "Dynamic delta events verified bit-for-bit against scripts.dstar_oracle with strict event_type validation"

    if case_id == "transaction_lifecycle":
        ref_steps = simulate_transaction_stream(fixture_data)
        act_steps = native_report.get("steps_executed", [])
        if len(act_steps) != len(ref_steps):
            return False, f"Case '{case_id}' steps count mismatch: expected {len(ref_steps)}, got {len(act_steps)}"

        for i, (ref, act) in enumerate(zip(ref_steps, act_steps)):
            if act.get("step_index") != ref["step_index"]:
                return False, f"Case '{case_id}' step {i} field 'step_index' mismatch: expected {ref['step_index']}, got {act.get('step_index')}"
            if act.get("status") != ref["status"]:
                return False, f"Case '{case_id}' step {i} field 'status' mismatch: expected {ref['status']}, got {act.get('status')}"
            if act.get("op") != ref["op"]:
                return False, f"Case '{case_id}' step {i} field 'op' mismatch: expected {ref['op']}, got {act.get('op')}"
            if act.get("u") != ref["u"]:
                return False, f"Case '{case_id}' step {i} field 'u' mismatch: expected {ref['u']}, got {act.get('u')}"
            if act.get("v") != ref["v"]:
                return False, f"Case '{case_id}' step {i} field 'v' mismatch: expected {ref['v']}, got {act.get('v')}"
            if act.get("current_edges_count") != ref["edges_count"]:
                return False, f"Case '{case_id}' step {i} field 'current_edges_count' mismatch: expected {ref['edges_count']}, got {act.get('current_edges_count')}"
            if act.get("current_vertices_count") != ref["vertices_count"]:
                return False, f"Case '{case_id}' step {i} field 'current_vertices_count' mismatch: expected {ref['vertices_count']}, got {act.get('current_vertices_count')}"
            if act.get("vertex_u_exists") != ref["vertex_u_exists"]:
                return False, f"Case '{case_id}' step {i} field 'vertex_u_exists' mismatch: expected {ref['vertex_u_exists']}, got {act.get('vertex_u_exists')}"
            if act.get("vertex_v_exists") != ref["vertex_v_exists"]:
                return False, f"Case '{case_id}' step {i} field 'vertex_v_exists' mismatch: expected {ref['vertex_v_exists']}, got {act.get('vertex_v_exists')}"
            if act.get("degree_u") != ref["degree_u"]:
                return False, f"Case '{case_id}' step {i} field 'degree_u' mismatch: expected {ref['degree_u']}, got {act.get('degree_u')}"
            if act.get("degree_v") != ref["degree_v"]:
                return False, f"Case '{case_id}' step {i} field 'degree_v' mismatch: expected {ref['degree_v']}, got {act.get('degree_v')}"
            if act.get("has_edge_uv") != ref["has_edge_uv"]:
                return False, f"Case '{case_id}' step {i} field 'has_edge_uv' mismatch: expected {ref['has_edge_uv']}, got {act.get('has_edge_uv')}"

            # Full edges comparison with labels
            act_edges = sorted(act.get("edges", []), key=lambda x: (x["u"], x["v"]))
            ref_edges = sorted(ref["edges"], key=lambda x: (x["u"], x["v"]))
            if act_edges != ref_edges:
                return False, f"Case '{case_id}' step {i} field 'edges' mismatch: expected {ref_edges}, got {act_edges}"

            # Degrees and neighbors comparison
            if act.get("degrees") != ref["degrees"]:
                return False, f"Case '{case_id}' step {i} field 'degrees' mismatch: expected {ref['degrees']}, got {act.get('degrees')}"
            if act.get("neighbors") != ref["neighbors"]:
                return False, f"Case '{case_id}' step {i} field 'neighbors' mismatch: expected {ref['neighbors']}, got {act.get('neighbors')}"

            codec_st = act.get("codec_state", {})
            if codec_st.get("data_vertices_count") != ref["codec_data_vertices_count"]:
                return False, f"Case '{case_id}' step {i} codec data_vertices_count mismatch: expected {ref['codec_data_vertices_count']}, got {codec_st.get('data_vertices_count')}"

        return True, "Transaction lifecycle steps verified against Python reference state model"

    if case_id == "lifecycle_stream":
        if native_report.get("final_vertices_count") != 5:
            return False, f"Case '{case_id}' final_vertices_count mismatch: expected 5, got {native_report.get('final_vertices_count')}"
        if native_report.get("final_edges_count") != 4:
            return False, f"Case '{case_id}' final_edges_count mismatch: expected 4, got {native_report.get('final_edges_count')}"

        ref_records = simulate_lifecycle_stream(fixture_data)
        act_records = native_report.get("mutations_executed", [])
        if len(act_records) != len(ref_records):
            return False, f"Case '{case_id}' mutations count mismatch: expected {len(ref_records)}, got {len(act_records)}"

        for i, (ref, act) in enumerate(zip(ref_records, act_records)):
            for fld in ("step_index", "op", "u", "v", "edges_count", "vertices_count", "has_edge", "edge_label", "in_tombstones", "in_overlay"):
                if act.get(fld) != ref[fld]:
                    return False, f"Case '{case_id}' step {i} field '{fld}' mismatch: expected {ref[fld]}, got {act.get(fld)}"

            act_edges = sorted(act.get("edges", []), key=lambda x: (x["u"], x["v"]))
            ref_edges = sorted(ref["edges"], key=lambda x: (x["u"], x["v"]))
            if act_edges != ref_edges:
                return False, f"Case '{case_id}' step {i} field 'edges' mismatch: expected {ref_edges}, got {act_edges}"
            if act.get("degrees") != ref["degrees"]:
                return False, f"Case '{case_id}' step {i} field 'degrees' mismatch: expected {ref['degrees']}, got {act.get('degrees')}"
            if act.get("neighbors") != ref["neighbors"]:
                return False, f"Case '{case_id}' step {i} field 'neighbors' mismatch: expected {ref['neighbors']}, got {act.get('neighbors')}"

        return True, "Lifecycle stream verified step-by-step against independent reference model"

    if case_id == "diamond_k1_fixed":
        if native_report.get("is_valid") is not True:
            return False, "Expected Diamond k=1 fixed mapping to be valid"
        if native_report.get("matched_edges") != 4 or native_report.get("missing_edges") != 1:
            return False, "Edge count mismatch in Diamond k=1 evaluation"
        if native_report.get("computed_lambda") != 2:
            return False, f"Expected lambda=2, got {native_report.get('computed_lambda')}"
        if native_report.get("s2_spanning_connected") is not True:
            return False, "Expected s2_spanning_connected to be true"
        return True, "Diamond k=1 evaluation verified against D-R guard contract"

    if case_id == "tuple_sink_invariants":
        if native_report.get("raw_emissions") != 4:
            return False, f"TupleSink raw_emissions mismatch: expected 4, got {native_report.get('raw_emissions')}"
        if native_report.get("unique_emissions") != 3:
            return False, f"TupleSink unique_emissions mismatch: expected 3, got {native_report.get('unique_emissions')}"
        if native_report.get("committed_emissions") != 2:
            return False, f"TupleSink committed_emissions mismatch: expected 2, got {native_report.get('committed_emissions')}"
        if native_report.get("discarded_emissions") != 1:
            return False, f"TupleSink discarded_emissions mismatch: expected 1, got {native_report.get('discarded_emissions')}"
        return True, "TupleSink invariants verified"

    if case_id == "irrelevant_update_stream":
        if native_report.get("published_tuples_count") != 0 or native_report.get("edge_recorded") is not True:
            return False, "Irrelevant update state check failed"
        return True, "Irrelevant update verified"

    return True, "Case verified"


# ==============================================================================
# Execution with Containment
# ==============================================================================

def run_isolated_case(
    harness_binary: Path,
    binary_sha: str,
    case_def: Dict[str, Any],
) -> Dict[str, Any]:
    case_id = case_def["case_id"]
    fixture_path = REPO_ROOT / case_def["fixture_path"]
    expected_fixture_sha = case_def["fixture_sha256"]

    if not fixture_path.exists():
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": f"Fixture file not found: {fixture_path}",
        }

    actual_fixture_sha = compute_sha256(fixture_path)
    if actual_fixture_sha != expected_fixture_sha:
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": f"Fixture SHA-256 mismatch: expected {expected_fixture_sha}, got {actual_fixture_sha}",
        }

    with open(fixture_path, "r", encoding="utf-8") as f:
        fixture_data = json.load(f)

    limits = case_def.get("limits", {})
    timeout_sec = min(limits.get("timeout_seconds", 15), 30)
    as_mib = min(limits.get("max_address_space_mib", 256), 512)
    as_bytes = as_mib * 1024 * 1024

    def preexec():
        try:
            resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        except Exception as e:
            sys.stderr.write(f"FATAL: Failed to enforce RLIMIT_AS ({as_bytes} bytes): {e}\n")
            sys.stderr.flush()
            os._exit(127)
        os.setpgrp()

    cmd = [
        str(harness_binary),
        "--case", case_id,
        "--fixture", str(fixture_path),
        "--binary-sha", binary_sha,
    ]

    start_time = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=preexec,
        )
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        elapsed_sec = time.monotonic() - start_time
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.communicate()
        return {
            "case_id": case_id,
            "status": "TIMEOUT",
            "diagnostic": f"Process exceeded limit of {timeout_sec}s",
            "elapsed_seconds": timeout_sec,
        }

    if proc.returncode == 127 and "FATAL: Failed to enforce RLIMIT_AS" in stderr:
        return {
            "case_id": case_id,
            "status": "FAIL",
            "exit_code": 127,
            "diagnostic": f"Resource limit containment could not be enforced: {stderr.strip()}",
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    if proc.returncode != 0:
        diag = f"Non-zero exit code {proc.returncode}"
        try:
            native_report = json.loads(stdout.strip())
            diag = native_report.get("diagnostic", diag)
        except Exception:
            pass
        return {
            "case_id": case_id,
            "status": "FAIL",
            "exit_code": proc.returncode,
            "diagnostic": diag,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    try:
        native_report = json.loads(stdout.strip())
    except json.JSONDecodeError as e:
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": f"Failed to parse JSON output from harness: {e}",
            "raw_output": stdout,
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    if not isinstance(native_report, dict):
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": "Harness output JSON root is not an object",
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    if native_report.get("case_id") != case_id:
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": f"Harness reported case_id '{native_report.get('case_id')}' does not match expected '{case_id}'",
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    native_status = native_report.get("status")
    if native_status != "PASS":
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": native_report.get("diagnostic", f"Native status is '{native_status}'"),
            "native_report": native_report,
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    # Strict observation schema and structure check
    obs_ok, obs_msg = validate_case_observations(case_id, fixture_data, native_report)
    if not obs_ok:
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": obs_msg,
            "native_report": native_report,
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    oracle_ok, oracle_msg = verify_case_with_oracle(case_id, fixture_data, native_report)
    if not oracle_ok:
        return {
            "case_id": case_id,
            "status": "FAIL",
            "diagnostic": f"Independent Oracle check failed: {oracle_msg}",
            "native_report": native_report,
            "elapsed_seconds": round(elapsed_sec, 4),
        }

    return {
        "case_id": case_id,
        "status": "PASS",
        "diagnostic": native_report.get("diagnostic", oracle_msg),
        "oracle_verification": oracle_msg,
        "elapsed_seconds": round(elapsed_sec, 4),
        "native_report": native_report,
    }


def write_summary_checkpoint(output_path: Path, summary_data: Dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = output_path.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    temp_file.replace(output_path)


# ==============================================================================
# Main Runner Entry Point
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="RapidFlow-APX Native Foundation Runner (Phase P2)")
    parser.add_argument("--case", type=str, help="Run single case by ID (diagnostic mode)")
    parser.add_argument("--clean-build", action="store_true", help="Force clean build before testing")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifest, fixtures, and preflight without execution")
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "configs/rapidflow_apx_foundation_cases.json", help="Path to manifest")
    parser.add_argument("--output", type=Path, default=None, help="Path to summary output")
    args = parser.parse_args()

    is_subset = bool(args.case)
    if args.output is None:
        if is_subset:
            output_path = REPO_ROOT / "reports/rapidflow_apx_foundation/diagnostic_summary.json"
        else:
            output_path = REPO_ROOT / "reports/rapidflow_apx_foundation/summary.json"
    else:
        output_path = args.output

    summary: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "runner_mode": "DIAGNOSTIC_SUBSET" if is_subset else ("DRY_RUN" if args.dry_run else "CANONICAL"),
        "overall_status": "RUNNING",
        "stages": {
            "PROVENANCE": "PENDING",
            "PREFLIGHT": "PENDING",
            "MANIFEST": "PENDING",
            "BUILD": "PENDING",
            "CASES": "PENDING",
        },
        "results": [],
    }
    write_summary_checkpoint(output_path, summary)

    # Stage 1: Provenance
    print("[1/5] Verifying fail-closed Git provenance...")
    try:
        provenance = verify_provenance(require_clean_repo=not is_subset and not args.dry_run)
        summary["provenance"] = provenance
        summary["stages"]["PROVENANCE"] = "PASS"
        write_summary_checkpoint(output_path, summary)
        print(f"  Repository Commit: {provenance['repo_commit']}")
        print(f"  Upstream Commit:   {provenance['upstream_commit']} (pristine)")
    except ProvenanceError as e:
        summary["stages"]["PROVENANCE"] = "FAIL"
        summary["overall_status"] = "PROVENANCE_FAILED"
        summary["diagnostic"] = str(e)
        write_summary_checkpoint(output_path, summary)
        print(f"ERROR: Provenance check failed: {e}", file=sys.stderr)
        return 1

    # Stage 2: System Resources Preflight
    print("[2/5] Checking host system resources...")
    try:
        sys_res = check_system_resources()
        summary["system_resources"] = sys_res
        summary["stages"]["PREFLIGHT"] = "PASS"
        write_summary_checkpoint(output_path, summary)
        print(f"  Available RAM: {sys_res['available_ram_gib']} GiB | Free Disk: {sys_res['free_disk_gib']} GiB")
    except ResourceLimitError as e:
        summary["stages"]["PREFLIGHT"] = "FAIL"
        summary["overall_status"] = "PREFLIGHT_FAILED"
        summary["diagnostic"] = str(e)
        write_summary_checkpoint(output_path, summary)
        print(f"ERROR: Resource limit error: {e}", file=sys.stderr)
        return 1

    # Stage 3: Manifest Validation
    print("[3/5] Loading and validating manifest...")
    try:
        manifest_data = validate_manifest(args.manifest, is_canonical=not is_subset)
        summary["stages"]["MANIFEST"] = "PASS"
        summary["manifest_sha256"] = compute_sha256(args.manifest)
        summary["fixture_hashes"] = {c["case_id"]: c["fixture_sha256"] for c in manifest_data["cases"]}
        write_summary_checkpoint(output_path, summary)
    except ManifestValidationError as e:
        summary["stages"]["MANIFEST"] = "FAIL"
        summary["overall_status"] = "MANIFEST_FAILED"
        summary["diagnostic"] = str(e)
        write_summary_checkpoint(output_path, summary)
        print(f"ERROR: Manifest validation error: {e}", file=sys.stderr)
        return 1

    cases = manifest_data.get("cases", [])
    if is_subset:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            summary["stages"]["MANIFEST"] = "FAIL"
            summary["overall_status"] = "CASE_NOT_FOUND"
            summary["diagnostic"] = f"Case '{args.case}' not found in manifest"
            write_summary_checkpoint(output_path, summary)
            print(f"ERROR: Case '{args.case}' not found in manifest", file=sys.stderr)
            return 1

    print(f"  Loaded {len(cases)} test cases from manifest")

    if args.dry_run:
        summary["overall_status"] = "DRY_RUN_PASSED"
        summary["stages"]["BUILD"] = "SKIPPED"
        summary["stages"]["CASES"] = "SKIPPED"
        write_summary_checkpoint(output_path, summary)
        print("[DRY-RUN] Manifest and fixtures validated. Skipping execution.")
        return 0

    # Stage 4: Build Native Test Harness
    print("[4/5] Building native test harness with resource limits...")
    try:
        harness_binary, binary_sha = build_native_harness(clean=args.clean_build)
        summary["binary_path"] = str(harness_binary)
        summary["binary_sha256"] = binary_sha
        summary["stages"]["BUILD"] = "PASS"
        summary["build_provenance"] = {
            "cmake_command": [str(CMAKE_PATH), "..", "-DCMAKE_BUILD_TYPE=Release"],
            "make_command": ["make", "-j2"],
            "binary_sha256": binary_sha,
        }
        write_summary_checkpoint(output_path, summary)
        print(f"  Harness Binary: {harness_binary}")
        print(f"  Binary SHA-256: {binary_sha}")
    except Exception as e:
        summary["stages"]["BUILD"] = "FAIL"
        summary["overall_status"] = "BUILD_FAILED"
        summary["diagnostic"] = str(e)
        write_summary_checkpoint(output_path, summary)
        print(f"ERROR: Failed to build native test harness: {e}", file=sys.stderr)
        return 1

    # Stage 5: Execution under Containment
    print("[5/5] Executing test cases under containment...")
    results = []
    overall_pass = True

    for c in cases:
        case_id = c["case_id"]
        print(f"  -> Running {case_id}...", end=" ", flush=True)
        try:
            res = run_isolated_case(harness_binary, binary_sha, c)
        except Exception as e:
            res = {
                "case_id": case_id,
                "status": "ERROR",
                "diagnostic": f"Unexpected exception executing case {case_id}: {type(e).__name__}: {e}",
                "elapsed_seconds": 0.0,
            }

        results.append(res)
        summary["results"] = results
        summary["completed_cases"] = len(results)
        summary["passed_cases"] = sum(1 for r in results if r["status"] == "PASS")
        summary["failed_cases"] = sum(1 for r in results if r["status"] != "PASS")
        summary["unattempted_cases"] = len(cases) - len(results)

        status = res["status"]
        elapsed = res.get("elapsed_seconds", 0.0)
        print(f"[{status}] ({elapsed:.3f}s)")
        if status != "PASS":
            overall_pass = False
            summary["stages"]["CASES"] = "FAIL"
            summary["failing_stage"] = "CASES"
            summary["failing_case"] = case_id
            summary["overall_status"] = "FAIL"
            write_summary_checkpoint(output_path, summary)
            print(f"     Diagnostic: {res.get('diagnostic')}")
            if res["status"] == "ERROR":
                print(f"ERROR: Execution aborted due to unexpected error in {case_id}", file=sys.stderr)
                return 1
        else:
            write_summary_checkpoint(output_path, summary)

    summary["stages"]["CASES"] = "PASS" if overall_pass else "FAIL"
    summary["total_cases"] = len(cases)
    summary["completed_cases"] = len(results)
    summary["passed_cases"] = sum(1 for r in results if r["status"] == "PASS")
    summary["failed_cases"] = sum(1 for r in results if r["status"] != "PASS")
    summary["unattempted_cases"] = len(cases) - len(results)

    if is_subset:
        summary["overall_status"] = "DIAGNOSTIC_SUBSET_RUN"
        write_summary_checkpoint(output_path, summary)
        print(f"\nDiagnostic subset summary written to: {output_path}")
        return 0 if overall_pass else 1

    if overall_pass:
        summary["overall_status"] = "P2_FOUNDATION_VERIFIED"
    else:
        summary["overall_status"] = "FAIL"

    write_summary_checkpoint(output_path, summary)
    print(f"\nCanonical summary written to: {output_path}")
    print(f"Overall status: {summary['overall_status']}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
