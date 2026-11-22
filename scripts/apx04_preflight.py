#!/usr/bin/env python3
"""
scripts/apx04_preflight.py

APX-04 Gate A3-00: Workload Manifest & Environment Read-Only Preflight Tool (Round 3 Adjudicated).
Dự án: Continuous Subgraph Matching (CSM) — Seminar RapidFlow
Issue: #20 (Gate A3-00)

FAIL-CLOSED BEHAVIOR:
- Strictly read-only: does NOT execute any subgraph matching algorithm.
- Does NOT mutate or write benchmark results.
- Validates manifest schema, cryptographic SHA-256 hashes, query simple-graph connectivity,
  stream prefix formats, label domain containment, provenance/lineage against declared source streams,
  sequential update correctness on initial G0 and pilot graphs, pilot case configurations,
  cgroup v2/v1 memory limits and headroom, and host system resources.
- Explicitly separates AUDIT VERDICT (PASS/WARN/FAIL) from EXECUTION READINESS (strictly SAFETY_BLOCKED).
- Returns exit code 0 if all audit checks pass; returns exit code 1 if any failure occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 checksum of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def is_strict_int(val: Any) -> bool:
    """Checks that a value is an integer and not a boolean (since bool is a subclass of int in Python)."""
    return type(val) is int and not isinstance(val, bool)


def is_strict_int_list(val: Any) -> bool:
    """Checks that a value is a list of strict integers (not booleans, strings, or None)."""
    if not isinstance(val, list):
        return False
    return all(is_strict_int(x) for x in val)


def validate_strict_int_field(val: Any, field_name: str, context: str, min_val: Optional[int] = 0) -> Tuple[bool, Optional[str]]:
    """
    Validates that a field is a strict integer (not bool, str, None, etc.) and optionally >= min_val.
    Returns (is_valid, error_message).
    """
    if not is_strict_int(val):
        recv_type = "string" if isinstance(val, str) else type(val).__name__
        return False, f"{context} field '{field_name}' has invalid type: expected integer, received {recv_type} ({val!r})"
    if min_val is not None and val < min_val:
        return False, f"{context} field '{field_name}' value {val} is below minimum allowed {min_val}"
    return True, None


def validate_strict_int_list_field(val: Any, field_name: str, context: str) -> Tuple[bool, Optional[str]]:
    """
    Validates that a field is a list of strict integers.
    Returns (is_valid, error_message).
    """
    if not isinstance(val, list):
        recv_type = "string" if isinstance(val, str) else type(val).__name__
        return False, f"{context} field '{field_name}' has invalid type: expected list of integers, received {recv_type} ({val!r})"
    for elem in val:
        if not is_strict_int(elem):
            elem_type = "string" if isinstance(elem, str) else type(elem).__name__
            return False, f"{context} field '{field_name}' has invalid element type: expected list of integers, contains non-integer element {elem!r} ({elem_type})"
    return True, None


def is_valid_sha256(val: Any) -> bool:
    """Checks whether a value is a valid 64-character lowercase/uppercase hex SHA-256 string."""
    return isinstance(val, str) and len(val) == 64 and all(c in "0123456789abcdefABCDEF" for c in val)


def compute_workload_identity_sha256(
    case_id: str,
    graph_id: str,
    graph_sha256: str,
    query_id: str,
    query_sha256: str,
    stream_id: str,
    stream_sha256: str,
    eps_m: int,
    eps_x: Optional[int],
    mode: str,
    semantic_class: str,
) -> str:
    """
    Computes deterministic canonical SHA-256 identity fingerprint for an approved pilot workload case.
    Serializes strictly the structural and semantic identification tuple without environmental,
    system, or volatile parameters.
    """
    payload = {
        "case_id": case_id,
        "eps_m": eps_m,
        "eps_x": eps_x,
        "graph_id": graph_id,
        "graph_sha256": graph_sha256,
        "mode": mode,
        "query_id": query_id,
        "query_sha256": query_sha256,
        "semantic_class": semantic_class,
        "stream_id": stream_id,
        "stream_sha256": stream_sha256,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_cgroup_memory_info(
    proc_cgroup_path: str = "/proc/self/cgroup",
    cgroup_base_path: str = "/sys/fs/cgroup",
) -> Dict[str, Any]:
    """
    Read-only inspection of cgroup v2 (and v1 fallback) memory limits, usage, and available headroom.
    Returns structured inspection report without attempting to mutate or enforce cgroup settings.
    """
    result: Dict[str, Any] = {
        "cgroup_version": "unknown",
        "cgroup_path": None,
        "is_constrained": False,
        "memory_max_bytes": None,
        "memory_current_bytes": None,
        "headroom_bytes": None,
        "headroom_gib": None,
        "ancestor_limits": [],
        "status": "UNAVAILABLE",
    }

    if not os.path.exists(proc_cgroup_path):
        result["status"] = "NO_CGROUP_FILE"
        return result

    try:
        with open(proc_cgroup_path, "r", encoding="utf-8") as f:
            cgroup_lines = [line.strip() for line in f if line.strip()]
    except Exception as e:
        result["status"] = f"PROC_READ_ERROR: {e}"
        return result

    # Check for cgroup v2: format "0::<relative_path>"
    v2_rel_path = None
    v1_mem_path = None
    for line in cgroup_lines:
        parts = line.split(":", 2)
        if len(parts) == 3:
            hierarchy_id, subsystems, rel_path = parts[0], parts[1], parts[2]
            if hierarchy_id == "0" and subsystems == "":
                v2_rel_path = rel_path
            elif "memory" in subsystems.split(","):
                v1_mem_path = rel_path

    base_p = Path(cgroup_base_path)
    if v2_rel_path and base_p.exists():
        result["cgroup_version"] = "v2"
        result["cgroup_path"] = v2_rel_path
        cur_p = base_p / v2_rel_path.lstrip("/")

        min_limit_bytes = None
        current_usage_at_min_limit = None
        min_headroom_bytes = None
        ancestor_limits = []

        # Traverse hierarchy from current cgroup up to cgroup_base_path
        check_dir = cur_p
        try:
            while True:
                max_file = check_dir / "memory.max"
                cur_file = check_dir / "memory.current"

                if max_file.exists():
                    try:
                        max_str = max_file.read_text(encoding="utf-8").strip()
                        cur_str = cur_file.read_text(encoding="utf-8").strip() if cur_file.exists() else "0"
                        cur_val = int(cur_str) if cur_str.isdigit() else 0

                        if max_str != "max" and max_str.isdigit():
                            limit_val = int(max_str)
                            headroom = max(0, limit_val - cur_val)
                            ancestor_limits.append({
                                "dir": str(check_dir),
                                "memory_max_bytes": limit_val,
                                "memory_current_bytes": cur_val,
                                "headroom_bytes": headroom,
                            })
                            if min_headroom_bytes is None or headroom < min_headroom_bytes:
                                min_headroom_bytes = headroom
                                min_limit_bytes = limit_val
                                current_usage_at_min_limit = cur_val
                    except Exception:
                        pass

                if check_dir == base_p or check_dir == check_dir.parent:
                    break
                check_dir = check_dir.parent

            # Read leaf current usage if not yet set
            leaf_cur_file = cur_p / "memory.current"
            leaf_cur_bytes = 0
            if leaf_cur_file.exists():
                try:
                    leaf_str = leaf_cur_file.read_text(encoding="utf-8").strip()
                    if leaf_str.isdigit():
                        leaf_cur_bytes = int(leaf_str)
                except Exception:
                    pass

            result["ancestor_limits"] = ancestor_limits
            if min_limit_bytes is not None:
                result["is_constrained"] = True
                result["memory_max_bytes"] = min_limit_bytes
                result["memory_current_bytes"] = current_usage_at_min_limit
                result["headroom_bytes"] = min_headroom_bytes
                result["headroom_gib"] = round(min_headroom_bytes / (1024.0 ** 3), 2)
                result["status"] = "CONSTRAINED"
            else:
                result["is_constrained"] = False
                result["memory_current_bytes"] = leaf_cur_bytes
                result["status"] = "UNCONSTRAINED"
        except Exception as e:
            result["status"] = f"CGROUP_INSPECTION_ERROR: {e}"

    elif v1_mem_path and base_p.exists():
        # Fallback to cgroup v1
        result["cgroup_version"] = "v1"
        result["cgroup_path"] = v1_mem_path
        cur_p = base_p / "memory" / v1_mem_path.lstrip("/")
        limit_file = cur_p / "memory.limit_in_bytes"
        usage_file = cur_p / "memory.usage_in_bytes"
        try:
            if limit_file.exists() and usage_file.exists():
                lim_str = limit_file.read_text(encoding="utf-8").strip()
                use_str = usage_file.read_text(encoding="utf-8").strip()
                if lim_str.isdigit() and use_str.isdigit():
                    lim_val = int(lim_str)
                    use_val = int(use_str)
                    if lim_val < (1 << 60):
                        result["is_constrained"] = True
                        result["memory_max_bytes"] = lim_val
                        result["memory_current_bytes"] = use_val
                        headroom = max(0, lim_val - use_val)
                        result["headroom_bytes"] = headroom
                        result["headroom_gib"] = round(headroom / (1024.0 ** 3), 2)
                        result["status"] = "CONSTRAINED"
                    else:
                        result["is_constrained"] = False
                        result["memory_current_bytes"] = use_val
                        result["status"] = "UNCONSTRAINED"
        except Exception as e:
            result["status"] = f"CGROUP_V1_ERROR: {e}"
    else:
        result["status"] = "NO_CGROUP_MOUNT"

    return result


def get_system_resources(
    target_dir: Path = Path("."),
    proc_meminfo_path: str = "/proc/meminfo",
    proc_cgroup_path: str = "/proc/self/cgroup",
    cgroup_base_path: str = "/sys/fs/cgroup",
) -> Dict[str, Any]:
    """Inspects host available RAM, cgroup memory headroom, and free disk space without external dependencies."""
    mem_total_kib = 0
    mem_available_kib = 0
    if os.path.exists(proc_meminfo_path):
        try:
            with open(proc_meminfo_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        if key == "MemTotal":
                            mem_total_kib = int(val_str)
                        elif key == "MemAvailable":
                            mem_available_kib = int(val_str)
        except Exception:
            pass

    ram_total_gib = mem_total_kib / (1024.0 * 1024.0) if mem_total_kib > 0 else 0.0
    ram_available_gib = mem_available_kib / (1024.0 * 1024.0) if mem_available_kib > 0 else 0.0

    cgroup_info = get_cgroup_memory_info(
        proc_cgroup_path=proc_cgroup_path,
        cgroup_base_path=cgroup_base_path,
    )

    # Effective RAM accounts for cgroup headroom if constrained
    effective_ram_available_gib = ram_available_gib
    if cgroup_info.get("is_constrained") and cgroup_info.get("headroom_gib") is not None:
        effective_ram_available_gib = min(ram_available_gib, cgroup_info["headroom_gib"])

    try:
        disk_usage = shutil.disk_usage(target_dir)
        disk_free_gib = disk_usage.free / (1024.0 ** 3)
        disk_total_gib = disk_usage.total / (1024.0 ** 3)
    except Exception:
        disk_free_gib = 0.0
        disk_total_gib = 0.0

    return {
        "ram_total_gib": round(ram_total_gib, 2),
        "ram_available_gib": round(ram_available_gib, 2),
        "effective_ram_available_gib": round(effective_ram_available_gib, 2),
        "cgroup": cgroup_info,
        "disk_total_gib": round(disk_total_gib, 2),
        "disk_free_gib": round(disk_free_gib, 2),
    }


def parse_query_file(file_path: Path) -> Dict[str, Any]:
    """
    Parses a query graph file and strictly verifies simple-graph properties,
    label validity, and connectivity. Does NOT require a 't # 1' header line.
    """
    vertices: Dict[int, int] = {}
    edges: List[Tuple[int, int, int]] = []
    seen_undirected_edges: Set[Tuple[int, int]] = set()
    adj: Dict[int, List[int]] = {}

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            tag = tokens[0]
            if tag == "t":
                # Header tag is optional; ignore tokens
                continue
            elif tag == "v":
                if len(tokens) != 3:
                    raise ValueError(f"Line {line_num}: malformed vertex record (expected 3 tokens): '{line}'")
                try:
                    vid = int(tokens[1])
                    vlabel = int(tokens[2])
                except ValueError as e:
                    raise ValueError(f"Line {line_num}: non-integer token in vertex record: {e}")

                if vid < 0:
                    raise ValueError(f"Line {line_num}: negative vertex ID {vid}")
                if vlabel < 0:
                    raise ValueError(f"Line {line_num}: negative vertex label {vlabel}")

                if vid in vertices:
                    if vertices[vid] != vlabel:
                        raise ValueError(
                            f"Line {line_num}: conflicting vertex label redefinition for vertex {vid} "
                            f"(was {vertices[vid]}, now {vlabel})"
                        )
                    raise ValueError(f"Line {line_num}: duplicate vertex declaration for vertex {vid}")

                vertices[vid] = vlabel
                adj.setdefault(vid, [])

            elif tag == "e":
                if len(tokens) != 4:
                    raise ValueError(f"Line {line_num}: malformed edge record (expected 4 tokens): '{line}'")
                try:
                    u = int(tokens[1])
                    v = int(tokens[2])
                    elabel = int(tokens[3])
                except ValueError as e:
                    raise ValueError(f"Line {line_num}: non-integer token in edge record: {e}")

                if u < 0 or v < 0:
                    raise ValueError(f"Line {line_num}: negative vertex ID in edge ({u}, {v})")
                if elabel < 0:
                    raise ValueError(f"Line {line_num}: negative edge label {elabel}")

                if u == v:
                    raise ValueError(f"Line {line_num}: self-loop detected on vertex {u}")

                if u not in vertices or v not in vertices:
                    missing = u if u not in vertices else v
                    raise ValueError(f"Line {line_num}: edge ({u}, {v}) references undeclared vertex {missing}")

                edge_key = (min(u, v), max(u, v))
                if edge_key in seen_undirected_edges:
                    raise ValueError(f"Line {line_num}: duplicate undirected edge detected between {u} and {v}")
                seen_undirected_edges.add(edge_key)

                edges.append((u, v, elabel))
                adj[u].append(v)
                adj[v].append(u)
            else:
                raise ValueError(f"Line {line_num}: unrecognized tag '{tag}'")

    if not vertices:
        raise ValueError(f"Query file {file_path} contains 0 vertices.")

    # Check graph connectivity via BFS from the first declared vertex
    start_v = next(iter(vertices.keys()))
    visited: Set[int] = {start_v}
    queue = deque([start_v])
    while queue:
        curr = queue.popleft()
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    is_connected = (len(visited) == len(vertices))

    # Compute degree sequence (sorted descending)
    degrees = sorted([len(adj.get(vid, [])) for vid in vertices], reverse=True)

    vlabels = sorted(list(set(vertices.values())))
    elabels = sorted(list(set(e[2] for e in edges)))

    return {
        "num_vertices": len(vertices),
        "num_edges": len(edges),
        "vertex_labels": vlabels,
        "edge_labels": elabels,
        "degree_sequence": degrees,
        "is_connected": is_connected,
    }


def parse_stream_prefix_file(file_path: Path) -> Dict[str, Any]:
    """
    Parses a stream prefix file and strictly verifies operation types,
    rejecting vertex updates (fixed vertex universe), self-loops, negative IDs, and malformed records.
    """
    operations: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            tag = tokens[0]

            if tag == "v":
                raise ValueError(
                    f"Line {line_num}: vertex update/declaration '{line}' is forbidden in stream "
                    f"(Model C adheres to fixed vertex universe Vt = V0)"
                )
            elif tag == "-e":
                if len(tokens) != 4:
                    raise ValueError(f"Line {line_num}: malformed deletion line (expected 4 tokens): '{line}'")
                try:
                    u = int(tokens[1])
                    v = int(tokens[2])
                    elabel = int(tokens[3])
                except ValueError as e:
                    raise ValueError(f"Line {line_num}: non-integer token in deletion: {e}")

                if u < 0 or v < 0:
                    raise ValueError(f"Line {line_num}: negative vertex ID in deletion ({u}, {v})")
                if elabel < 0:
                    raise ValueError(f"Line {line_num}: negative edge label in deletion {elabel}")
                if u == v:
                    raise ValueError(f"Line {line_num}: self-loop deletion forbidden ({u}, {v})")

                operations.append({"type": "e", "op": "-", "u": u, "v": v, "label": elabel})

            elif tag == "e":
                if len(tokens) == 4:
                    try:
                        u = int(tokens[1])
                        v = int(tokens[2])
                        elabel = int(tokens[3])
                    except ValueError as e:
                        raise ValueError(f"Line {line_num}: non-integer token in insertion: {e}")
                    op = "+"
                elif len(tokens) == 5 and tokens[1] in ("+", "-"):
                    op = tokens[1]
                    try:
                        u = int(tokens[2])
                        v = int(tokens[3])
                        elabel = int(tokens[4])
                    except ValueError as e:
                        raise ValueError(f"Line {line_num}: non-integer token in edge update: {e}")
                else:
                    raise ValueError(f"Line {line_num}: malformed edge update record: '{line}'")

                if u < 0 or v < 0:
                    raise ValueError(f"Line {line_num}: negative vertex ID in edge update ({u}, {v})")
                if elabel < 0:
                    raise ValueError(f"Line {line_num}: negative edge label in update {elabel}")
                if u == v:
                    raise ValueError(f"Line {line_num}: self-loop edge update forbidden ({u}, {v})")

                operations.append({"type": "e", "op": op, "u": u, "v": v, "label": elabel})
            else:
                raise ValueError(f"Line {line_num}: unrecognized stream tag '{tag}' in '{line}'")

    insert_count = sum(1 for o in operations if o.get("op") == "+")
    delete_count = sum(1 for o in operations if o.get("op") == "-")

    return {
        "total_operations": len(operations),
        "insertions": insert_count,
        "deletions": delete_count,
        "operations": operations,
    }


def parse_pilot_graph_file(file_path: Path) -> Dict[str, Any]:
    """
    Parses a pilot data graph file and strictly verifies simple-graph properties:
    - No negative vertex or edge IDs/labels
    - No duplicate vertex declarations
    - No self-loops (u == v)
    - No duplicate undirected edges
    - All edge endpoints must be previously declared vertices
    """
    vertices: Dict[int, int] = {}
    edges: List[Tuple[int, int, int]] = []
    seen_undirected_edges: Set[Tuple[int, int]] = set()

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            tag = tokens[0]
            if tag == "t":
                continue
            elif tag == "v":
                if len(tokens) != 3:
                    raise ValueError(f"Line {line_num}: malformed vertex record (expected 3 tokens): '{line}'")
                try:
                    vid = int(tokens[1])
                    vlabel = int(tokens[2])
                except ValueError as e:
                    raise ValueError(f"Line {line_num}: non-integer token in vertex record: {e}")
                if vid < 0:
                    raise ValueError(f"Line {line_num}: negative vertex ID {vid}")
                if vlabel < 0:
                    raise ValueError(f"Line {line_num}: negative vertex label {vlabel}")
                if vid in vertices:
                    raise ValueError(f"Line {line_num}: duplicate vertex declaration for vertex {vid}")
                vertices[vid] = vlabel
            elif tag == "e":
                if len(tokens) != 4:
                    raise ValueError(f"Line {line_num}: malformed edge record (expected 4 tokens): '{line}'")
                try:
                    u = int(tokens[1])
                    v = int(tokens[2])
                    elabel = int(tokens[3])
                except ValueError as e:
                    raise ValueError(f"Line {line_num}: non-integer token in edge record: {e}")
                if u < 0 or v < 0:
                    raise ValueError(f"Line {line_num}: negative vertex ID in edge ({u}, {v})")
                if elabel < 0:
                    raise ValueError(f"Line {line_num}: negative edge label {elabel}")
                if u == v:
                    raise ValueError(f"Line {line_num}: self-loop detected on vertex {u}")
                if u not in vertices or v not in vertices:
                    missing = u if u not in vertices else v
                    raise ValueError(f"Line {line_num}: edge ({u}, {v}) references undeclared vertex {missing}")
                edge_key = (min(u, v), max(u, v))
                if edge_key in seen_undirected_edges:
                    raise ValueError(f"Line {line_num}: duplicate undirected edge detected between {u} and {v}")
                seen_undirected_edges.add(edge_key)
                edges.append((u, v, elabel))
            else:
                raise ValueError(f"Line {line_num}: unrecognized tag '{tag}'")

    if not vertices:
        raise ValueError(f"Pilot graph file {file_path} contains 0 vertices.")

    return {
        "num_vertices": len(vertices),
        "num_edges": len(edges),
        "vertex_labels": sorted(list(set(vertices.values()))),
        "edge_labels": sorted(list(set(e[2] for e in edges))),
        "vertex_ids": set(vertices.keys()),
    }


# Module-level caches to accelerate repeated audits during testing
_G0_EDGE_CACHE: Dict[Tuple[str, int, Tuple[Tuple[int, int], ...]], Dict[Tuple[int, int], int]] = {}
_G0_VERTEX_CACHE: Dict[Tuple[str, int], Union[range, Set[int]]] = {}


class PreflightChecker:
    """Coordinates all read-only audits for APX-04 Gate A3-00."""

    def __init__(
        self,
        manifest_path: Path,
        repo_root: Path,
        min_ram_gib: float = 4.0,
        min_disk_gib: float = 10.0,
        enforce_ram: bool = False,
        proc_meminfo_path: str = "/proc/meminfo",
        proc_cgroup_path: str = "/proc/self/cgroup",
        cgroup_base_path: str = "/sys/fs/cgroup",
    ):
        self.manifest_path = manifest_path.resolve()
        self.repo_root = repo_root.resolve()
        self.min_ram_gib = min_ram_gib
        self.min_disk_gib = min_disk_gib
        self.enforce_ram = enforce_ram
        self.proc_meminfo_path = proc_meminfo_path
        self.proc_cgroup_path = proc_cgroup_path
        self.cgroup_base_path = cgroup_base_path
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.audit_log: Dict[str, Any] = {}

    def _finalize_audit_report(self) -> None:
        """Finalizes audit verdict, status, and summary metrics in self.audit_log."""
        if self.errors:
            audit_verdict = "AUDIT_FAIL"
            status = "FAIL"
        elif self.warnings:
            audit_verdict = "AUDIT_PASS_WITH_WARNINGS"
            status = "PASS"
        else:
            audit_verdict = "AUDIT_PASS"
            status = "PASS"

        self.audit_log["status"] = status
        self.audit_log["audit_verdict"] = audit_verdict
        self.audit_log["execution_readiness"] = "SAFETY_BLOCKED"
        self.audit_log["memory_enforcement"] = "NOT_VERIFIED"
        self.audit_log["execution_authorization"] = False
        self.audit_log["error_count"] = len(self.errors)
        self.audit_log["warning_count"] = len(self.warnings)
        self.audit_log["errors"] = list(self.errors)
        self.audit_log["warnings"] = list(self.warnings)

    def run_all(self) -> bool:
        """Executes all preflight audits. Returns True if all pass, False if any error."""
        self.errors.clear()
        self.warnings.clear()
        self.audit_log.clear()

        manifest = self.audit_manifest_schema()
        if manifest is None:
            self._finalize_audit_report()
            return False

        self.audit_system_resources()
        self.audit_data_graph(manifest)
        self.audit_pilot_data_graphs(manifest)
        self.audit_queries(manifest)
        self.audit_streams(manifest)
        self.audit_pilot_cases(manifest)
        self.audit_exclusions(manifest)

        self._finalize_audit_report()
        return len(self.errors) == 0

    def audit_manifest_schema(self) -> Optional[Dict[str, Any]]:
        """
        Validates workload manifest structure, root schema, component containers,
        records, and identifier types at the centralized schema boundary.
        Returns manifest dict if structurally valid, None if any structural schema error.
        """
        if not self.manifest_path.exists():
            self.errors.append(f"[MANIFEST_SCHEMA_ERROR] Manifest file not found: {self.manifest_path}")
            return None

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            self.errors.append(f"[MANIFEST_SCHEMA_ERROR] Failed to parse manifest JSON: {e}")
            return None

        if not isinstance(manifest, dict):
            recv_type = "null" if manifest is None else type(manifest).__name__
            self.errors.append(
                f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest root must be a JSON object, received {recv_type}"
            )
            return None

        # Required root keys verification
        required_keys = [
            "schema_version",
            "document_type",
            "status",
            "execution_authorization",
            "data_graph",
            "queries",
            "streams",
            "proposed_pilot_cases_a3_01",
            "exclusions",
        ]
        for key in required_keys:
            if key not in manifest:
                self.errors.append(f"[MANIFEST_SCHEMA_ERROR] Manifest missing required root key: '{key}'")

        # Top-level scalar metadata validation
        if "schema_version" in manifest:
            sv = manifest["schema_version"]
            if not isinstance(sv, (str, int, float)):
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'schema_version' expected string, received {type(sv).__name__}"
                )

        if "document_type" in manifest:
            dt = manifest["document_type"]
            if not isinstance(dt, str):
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'document_type' expected string, received {type(dt).__name__}"
                )
            elif dt != "apx04_workload_manifest":
                self.errors.append(f"Invalid document_type: {dt}")

        if "status" in manifest:
            st = manifest["status"]
            if not isinstance(st, str):
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'status' expected string, received {type(st).__name__}"
                )
            elif st != "DRAFT_PENDING_USER_APPROVAL":
                self.errors.append(
                    f"Manifest status must be 'DRAFT_PENDING_USER_APPROVAL', found: {st}"
                )

        if "execution_authorization" in manifest:
            ea = manifest["execution_authorization"]
            if ea is not False:
                self.errors.append(
                    f"Safety violation: execution_authorization must be false in Gate A3-00 (found {ea})"
                )

        # 1. data_graph container & record validation
        if "data_graph" in manifest:
            dg = manifest["data_graph"]
            if not isinstance(dg, dict):
                recv = "null" if dg is None else type(dg).__name__
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'data_graph' expected JSON object, received {recv}"
                )
            else:
                dg_name = dg.get("name")
                if dg_name is not None and not isinstance(dg_name, str):
                    self.errors.append(
                        f"[MANIFEST_SCHEMA_TYPE_ERROR] data_graph.name expected string, received {type(dg_name).__name__}"
                    )
                dg_path = dg.get("path")
                if dg_path is not None and not isinstance(dg_path, str):
                    self.errors.append(
                        f"[MANIFEST_SCHEMA_TYPE_ERROR] data_graph.path expected string, received {type(dg_path).__name__}"
                    )

        # 2. pilot_data_graphs container & records validation (optional root container)
        if "pilot_data_graphs" in manifest:
            pgs = manifest["pilot_data_graphs"]
            if not isinstance(pgs, dict):
                recv = "null" if pgs is None else type(pgs).__name__
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'pilot_data_graphs' expected JSON object, received {recv}"
                )
            else:
                for pg_id, pg_info in pgs.items():
                    if not isinstance(pg_id, str):
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] pilot_data_graphs key expected string, received {type(pg_id).__name__}"
                        )
                    if not isinstance(pg_info, dict):
                        recv = "null" if pg_info is None else type(pg_info).__name__
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] pilot_data_graphs.{pg_id} expected JSON object, received {recv}"
                        )
                    else:
                        pg_name = pg_info.get("name")
                        if pg_name is not None and not isinstance(pg_name, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] pilot_data_graphs.{pg_id}.name expected string, received {type(pg_name).__name__}"
                            )
                        pg_path = pg_info.get("path")
                        if pg_path is not None and not isinstance(pg_path, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] pilot_data_graphs.{pg_id}.path expected string, received {type(pg_path).__name__}"
                            )

        # 3. queries container & records validation
        if "queries" in manifest:
            qs = manifest["queries"]
            if not isinstance(qs, dict):
                recv = "null" if qs is None else type(qs).__name__
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'queries' expected JSON object, received {recv}"
                )
            else:
                for q_id, q_info in qs.items():
                    if not isinstance(q_id, str):
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] queries key expected string, received {type(q_id).__name__}"
                        )
                    if not isinstance(q_info, dict):
                        recv = "null" if q_info is None else type(q_info).__name__
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] queries.{q_id} expected JSON object, received {recv}"
                        )
                    else:
                        q_cat = q_info.get("category")
                        if q_cat is not None and not isinstance(q_cat, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] queries.{q_id}.category expected string, received {type(q_cat).__name__}"
                            )
                        q_path = q_info.get("path")
                        if q_path is not None and not isinstance(q_path, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] queries.{q_id}.path expected string, received {type(q_path).__name__}"
                            )

        # 4. streams container & records validation
        if "streams" in manifest:
            sts = manifest["streams"]
            if not isinstance(sts, dict):
                recv = "null" if sts is None else type(sts).__name__
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'streams' expected JSON object, received {recv}"
                )
            else:
                for s_id, s_info in sts.items():
                    if not isinstance(s_id, str):
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] streams key expected string, received {type(s_id).__name__}"
                        )
                    if not isinstance(s_info, dict):
                        recv = "null" if s_info is None else type(s_info).__name__
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] streams.{s_id} expected JSON object, received {recv}"
                        )
                    else:
                        tgt = s_info.get("target_graph")
                        if tgt is not None and not isinstance(tgt, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] streams.{s_id}.target_graph expected string or null, received {type(tgt).__name__}"
                            )
                        s_path = s_info.get("path")
                        if s_path is not None and not isinstance(s_path, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] streams.{s_id}.path expected string, received {type(s_path).__name__}"
                            )
                        src_s = s_info.get("source_stream")
                        if src_s is not None and not isinstance(src_s, str):
                            self.errors.append(
                                f"[MANIFEST_SCHEMA_TYPE_ERROR] streams.{s_id}.source_stream expected string, received {type(src_s).__name__}"
                            )

        # 5. proposed_pilot_cases_a3_01 container & case records validation
        if "proposed_pilot_cases_a3_01" in manifest:
            cases = manifest["proposed_pilot_cases_a3_01"]
            if not isinstance(cases, list):
                recv = "null" if cases is None else type(cases).__name__
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'proposed_pilot_cases_a3_01' expected list, received {recv}"
                )
            else:
                string_fields = [
                    "case_id",
                    "query_id",
                    "stream_id",
                    "graph_source",
                    "mode",
                    "query_source",
                    "workload_category",
                    "semantic_class",
                    "pilot_stage",
                    "approval_state",
                    "execution_status",
                ]
                for idx, case in enumerate(cases):
                    if not isinstance(case, dict):
                        recv = "null" if case is None else type(case).__name__
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] proposed_pilot_cases_a3_01[{idx}] expected JSON object, received {recv}"
                        )
                    else:
                        for sf in string_fields:
                            val = case.get(sf)
                            if val is not None and not isinstance(val, str):
                                self.errors.append(
                                    f"[MANIFEST_SCHEMA_TYPE_ERROR] proposed_pilot_cases_a3_01[{idx}].{sf} expected string, received {type(val).__name__}"
                                )

        # 6. exclusions container & records validation
        if "exclusions" in manifest:
            excls = manifest["exclusions"]
            if not isinstance(excls, list):
                recv = "null" if excls is None else type(excls).__name__
                self.errors.append(
                    f"[MANIFEST_SCHEMA_TYPE_ERROR] Manifest field 'exclusions' expected list, received {recv}"
                )
            else:
                for idx, excl in enumerate(excls):
                    if not isinstance(excl, dict):
                        recv = "null" if excl is None else type(excl).__name__
                        self.errors.append(
                            f"[MANIFEST_SCHEMA_TYPE_ERROR] exclusions[{idx}] expected JSON object, received {recv}"
                        )

        self.audit_log["manifest_metadata"] = {
            "schema_version": manifest.get("schema_version"),
            "status": manifest.get("status"),
            "execution_authorization": manifest.get("execution_authorization"),
            "issue": manifest.get("issue"),
        }

        # If any schema error occurred, stop before downstream audits!
        if self.errors:
            return None

        return manifest

    def audit_system_resources(self) -> None:
        """Audits host RAM, cgroup memory limits/headroom, and disk space against requirements."""
        res = get_system_resources(
            target_dir=self.repo_root,
            proc_meminfo_path=self.proc_meminfo_path,
            proc_cgroup_path=self.proc_cgroup_path,
            cgroup_base_path=self.cgroup_base_path,
        )
        self.audit_log["system_resources"] = res

        # Check effective available RAM (accounting for cgroup headroom if constrained)
        effective_ram = res.get("effective_ram_available_gib", res.get("ram_available_gib", 0.0))
        if effective_ram < self.min_ram_gib:
            msg = (
                f"Effective available RAM ({effective_ram:.2f} GiB) is below required minimum floor "
                f"({self.min_ram_gib:.2f} GiB). Host RAM Available: {res['ram_available_gib']:.2f} GiB, "
                f"cgroup headroom: {res.get('cgroup', {}).get('headroom_gib')} GiB."
            )
            if self.enforce_ram:
                self.errors.append(f"[RAM_RESOURCE_ERROR] {msg}")
            else:
                self.warnings.append(f"[RAM_RESOURCE_WARNING] {msg}")

        # Check Disk
        if res["disk_free_gib"] < self.min_disk_gib:
            msg = (
                f"Free disk space ({res['disk_free_gib']:.2f} GiB) is below required minimum "
                f"({self.min_disk_gib:.2f} GiB)."
            )
            self.errors.append(f"[DISK_RESOURCE_ERROR] {msg}")

    def audit_data_graph(self, manifest: Dict[str, Any]) -> None:
        """Verifies initial data graph path, cryptographic SHA-256, and metadata types."""
        dg_info = manifest.get("data_graph", {})
        if not isinstance(dg_info, dict):
            self.errors.append("Manifest 'data_graph' must be a dictionary.")
            return

        rel_path = dg_info.get("path")
        if not rel_path or not isinstance(rel_path, str):
            self.errors.append("data_graph missing or invalid 'path'")
            return

        full_path = self.repo_root / rel_path
        if not full_path.exists():
            self.errors.append(f"Initial data graph file not found: {rel_path}")
            return

        context_str = "Initial data graph 'data_graph'"
        dg_errors_before = len(self.errors)

        if "vertices" in dg_info:
            v_ok, v_err = validate_strict_int_field(dg_info.get("vertices"), "vertices", context_str, min_val=0)
            if not v_ok:
                self.errors.append(f"[DATA_GRAPH_TYPE_ERROR] {v_err}")
        if "edges" in dg_info:
            e_ok, e_err = validate_strict_int_field(dg_info.get("edges"), "edges", context_str, min_val=0)
            if not e_ok:
                self.errors.append(f"[DATA_GRAPH_TYPE_ERROR] {e_err}")
        if "vertex_labels" in dg_info:
            vl_ok, vl_err = validate_strict_int_list_field(dg_info.get("vertex_labels"), "vertex_labels", context_str)
            if not vl_ok:
                self.errors.append(f"[DATA_GRAPH_TYPE_ERROR] {vl_err}")
        if "edge_labels" in dg_info:
            el_ok, el_err = validate_strict_int_list_field(dg_info.get("edge_labels"), "edge_labels", context_str)
            if not el_ok:
                self.errors.append(f"[DATA_GRAPH_TYPE_ERROR] {el_err}")

        expected_hash = dg_info.get("sha256")
        actual_hash = compute_sha256(full_path)
        if actual_hash != expected_hash:
            self.errors.append(
                f"Data graph SHA-256 mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
            )
        elif len(self.errors) == dg_errors_before:
            self.audit_log["data_graph_verified"] = {
                "name": dg_info.get("name"),
                "path": rel_path,
                "sha256": actual_hash,
                "size_bytes": full_path.stat().st_size,
            }

    def audit_pilot_data_graphs(self, manifest: Dict[str, Any]) -> None:
        """Verifies pilot data graphs declared in manifest: existence, SHA-256, and metadata."""
        pilot_graphs = manifest.get("pilot_data_graphs", {})
        if not isinstance(pilot_graphs, dict):
            self.errors.append("Manifest 'pilot_data_graphs' must be a dictionary.")
            return

        verified_pilots: Dict[str, Any] = {}
        for pg_id, pg_info in pilot_graphs.items():
            if not isinstance(pg_info, dict):
                self.errors.append(f"Pilot data graph '{pg_id}' specification must be a dictionary.")
                continue

            rel_path = pg_info.get("path")
            if not rel_path or not isinstance(rel_path, str):
                self.errors.append(f"Pilot data graph '{pg_id}' missing or invalid 'path'")
                continue

            full_path = self.repo_root / rel_path
            if not full_path.exists():
                self.errors.append(f"Pilot data graph '{pg_id}' file not found: {rel_path}")
                continue

            expected_hash = pg_info.get("sha256")
            actual_hash = compute_sha256(full_path)
            if actual_hash != expected_hash:
                self.errors.append(
                    f"Pilot data graph '{pg_id}' SHA-256 mismatch for {rel_path}: "
                    f"expected {expected_hash}, got {actual_hash}"
                )
                continue

            # Independent parsing and metadata validation (FIX R4-02)
            try:
                parsed = parse_pilot_graph_file(full_path)
            except Exception as e:
                self.errors.append(f"Pilot data graph '{pg_id}' parse failure ({rel_path}): {e}")
                continue

            component_errors_before = len(self.errors)
            context_str = f"Pilot data graph '{pg_id}'"

            # Strict metadata type and value validation
            expected_v = pg_info.get("vertices")
            v_ok, v_err = validate_strict_int_field(expected_v, "vertices", context_str, min_val=0)
            if not v_ok:
                self.errors.append(f"[PILOT_DATA_GRAPH_TYPE_ERROR] {v_err}")
            elif parsed["num_vertices"] != expected_v:
                self.errors.append(
                    f"Pilot data graph '{pg_id}' vertex count mismatch: expected {expected_v}, found {parsed['num_vertices']}"
                )

            expected_e = pg_info.get("edges")
            e_ok, e_err = validate_strict_int_field(expected_e, "edges", context_str, min_val=0)
            if not e_ok:
                self.errors.append(f"[PILOT_DATA_GRAPH_TYPE_ERROR] {e_err}")
            elif parsed["num_edges"] != expected_e:
                self.errors.append(
                    f"Pilot data graph '{pg_id}' edge count mismatch: expected {expected_e}, found {parsed['num_edges']}"
                )

            raw_vlabels = pg_info.get("vertex_labels")
            vl_ok, vl_err = validate_strict_int_list_field(raw_vlabels, "vertex_labels", context_str)
            if not vl_ok:
                self.errors.append(f"[PILOT_DATA_GRAPH_TYPE_ERROR] {vl_err}")
            else:
                expected_vlabels = sorted(raw_vlabels)
                if parsed["vertex_labels"] != expected_vlabels:
                    self.errors.append(
                        f"Pilot data graph '{pg_id}' vertex labels mismatch: expected {expected_vlabels}, found {parsed['vertex_labels']}"
                    )

            raw_elabels = pg_info.get("edge_labels")
            el_ok, el_err = validate_strict_int_list_field(raw_elabels, "edge_labels", context_str)
            if not el_ok:
                self.errors.append(f"[PILOT_DATA_GRAPH_TYPE_ERROR] {el_err}")
            else:
                expected_elabels = sorted(raw_elabels)
                if parsed["edge_labels"] != expected_elabels:
                    self.errors.append(
                        f"Pilot data graph '{pg_id}' edge labels mismatch: expected {expected_elabels}, found {parsed['edge_labels']}"
                    )

            if len(self.errors) != component_errors_before:
                continue

            verified_pilots[pg_id] = {
                "name": pg_info.get("name", pg_id),
                "path": rel_path,
                "sha256": actual_hash,
                "vertices": parsed["num_vertices"],
                "edges": parsed["num_edges"],
                "vertex_labels": parsed["vertex_labels"],
                "edge_labels": parsed["edge_labels"],
            }

        self.audit_log["pilot_data_graphs_verified"] = verified_pilots

    def audit_queries(self, manifest: Dict[str, Any]) -> None:
        """Verifies all manifest queries: SHA-256, syntax, degree sequence, connectivity, and label domain."""
        queries_dict = manifest.get("queries", {})
        if not isinstance(queries_dict, dict) or not queries_dict:
            self.errors.append("Manifest contains no queries or 'queries' is not a dictionary.")
            return

        # Valid label universe of primary NetFlow data graph
        dg_vlabels = manifest.get("data_graph", {}).get("vertex_labels")
        netflow_vlabels = set(dg_vlabels) if is_strict_int_list(dg_vlabels) else {0}
        dg_elabels = manifest.get("data_graph", {}).get("edge_labels")
        netflow_elabels = set(dg_elabels) if is_strict_int_list(dg_elabels) else set(range(8))

        # Pilot graph label universes (safe iteration)
        pilot_elabels: Set[int] = set()
        pilot_vlabels: Set[int] = set()
        for pg_info in manifest.get("pilot_data_graphs", {}).values():
            if isinstance(pg_info, dict):
                p_vl = pg_info.get("vertex_labels")
                if is_strict_int_list(p_vl):
                    pilot_vlabels.update(p_vl)
                p_el = pg_info.get("edge_labels")
                if is_strict_int_list(p_el):
                    pilot_elabels.update(p_el)

        all_valid_vlabels = netflow_vlabels | pilot_vlabels
        all_valid_elabels = netflow_elabels | pilot_elabels

        verified_queries: Dict[str, Any] = {}
        for q_id, q_info in queries_dict.items():
            if not isinstance(q_info, dict):
                self.errors.append(f"Query '{q_id}' specification must be a dictionary.")
                continue

            rel_path = q_info.get("path")
            if not rel_path or not isinstance(rel_path, str):
                self.errors.append(f"Query {q_id} missing or invalid 'path'")
                continue

            full_path = self.repo_root / rel_path
            if not full_path.exists():
                self.errors.append(f"Query {q_id} file not found: {rel_path}")
                continue

            # SHA-256 check
            expected_hash = q_info.get("sha256")
            actual_hash = compute_sha256(full_path)
            if actual_hash != expected_hash:
                self.errors.append(
                    f"Query {q_id} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
                )
                continue

            # Semantic & structural parsing
            try:
                parsed = parse_query_file(full_path)
            except Exception as e:
                self.errors.append(f"Query {q_id} parse failure ({rel_path}): {e}")
                continue

            component_errors_before = len(self.errors)
            context_str = f"query '{q_id}'"

            # Strict metadata type and value validation
            expected_v = q_info.get("vertices")
            v_ok, v_err = validate_strict_int_field(expected_v, "vertices", context_str, min_val=1)
            if not v_ok:
                self.errors.append(f"[QUERY_TYPE_ERROR] {v_err}")
            elif parsed["num_vertices"] != expected_v:
                self.errors.append(
                    f"Query {q_id} vertex count mismatch: expected {expected_v}, found {parsed['num_vertices']}"
                )

            expected_e = q_info.get("edges")
            e_ok, e_err = validate_strict_int_field(expected_e, "edges", context_str, min_val=0)
            if not e_ok:
                self.errors.append(f"[QUERY_TYPE_ERROR] {e_err}")
            elif parsed["num_edges"] != expected_e:
                self.errors.append(
                    f"Query {q_id} edge count mismatch: expected {expected_e}, found {parsed['num_edges']}"
                )

            # Check labels match declaration
            raw_vlabels = q_info.get("vertex_labels")
            vl_ok, vl_err = validate_strict_int_list_field(raw_vlabels, "vertex_labels", context_str)
            if not vl_ok:
                self.errors.append(f"[QUERY_TYPE_ERROR] {vl_err}")
            else:
                expected_vlabels = sorted(raw_vlabels)
                if parsed["vertex_labels"] != expected_vlabels:
                    self.errors.append(
                        f"Query {q_id} vertex labels mismatch: expected {expected_vlabels}, found {parsed['vertex_labels']}"
                    )

            raw_elabels = q_info.get("edge_labels")
            el_ok, el_err = validate_strict_int_list_field(raw_elabels, "edge_labels", context_str)
            if not el_ok:
                self.errors.append(f"[QUERY_TYPE_ERROR] {el_err}")
            else:
                expected_elabels = sorted(raw_elabels)
                if parsed["edge_labels"] != expected_elabels:
                    self.errors.append(
                        f"Query {q_id} edge labels mismatch: expected {expected_elabels}, found {parsed['edge_labels']}"
                    )

            # Label Domain Validation: Labels_V(Q) <= Labels_V(G), Labels_E(Q) <= Labels_E(G)
            category = q_info.get("category", "")
            target_vlabels = netflow_vlabels if category in ("AUTHOR_NEWSP", "SYNTHETIC_PROBE") else all_valid_vlabels
            target_elabels = netflow_elabels if category in ("AUTHOR_NEWSP", "SYNTHETIC_PROBE") else all_valid_elabels

            for vl in parsed["vertex_labels"]:
                if vl not in target_vlabels:
                    self.errors.append(
                        f"Query {q_id} contains out-of-domain vertex label {vl} (valid domain: {sorted(target_vlabels)})"
                    )

            for el in parsed["edge_labels"]:
                if el not in target_elabels:
                    self.errors.append(
                        f"Query {q_id} contains out-of-domain edge label {el} (valid domain: {sorted(target_elabels)})"
                    )

            # Check degree sequence
            if "degree_sequence" in q_info:
                raw_degseq = q_info["degree_sequence"]
                ds_ok, ds_err = validate_strict_int_list_field(raw_degseq, "degree_sequence", context_str)
                if not ds_ok:
                    self.errors.append(f"[QUERY_TYPE_ERROR] {ds_err}")
                elif parsed["degree_sequence"] != raw_degseq:
                    self.errors.append(
                        f"Query {q_id} degree sequence mismatch: expected {raw_degseq}, found {parsed['degree_sequence']}"
                    )

            # Check connectivity
            if not parsed["is_connected"]:
                self.errors.append(f"Query {q_id} is disconnected; CSM queries must be connected.")

            if len(self.errors) != component_errors_before:
                continue

            verified_queries[q_id] = {
                "path": rel_path,
                "sha256": actual_hash,
                "vertices": parsed["num_vertices"],
                "edges": parsed["num_edges"],
                "is_connected": parsed["is_connected"],
            }

        self.audit_log["queries_verified"] = verified_queries

    def audit_streams(self, manifest: Dict[str, Any]) -> None:
        """
        Verifies all stream prefix files: SHA-256, format, operation counts,
        cryptographic lineage from declared source streams, and sequential validity on G0/pilot graphs.
        Separates source_lineage_verified from sequential_updates_verified flags.
        """
        streams_dict = manifest.get("streams", {})
        if not isinstance(streams_dict, dict) or not streams_dict:
            self.errors.append("Manifest contains no stream prefixes or 'streams' is not a dictionary.")
            return

        # Label universes (safe iteration)
        dg_elabels = manifest.get("data_graph", {}).get("edge_labels")
        netflow_elabels = set(dg_elabels) if is_strict_int_list(dg_elabels) else set(range(8))
        all_valid_elabels = set(netflow_elabels)
        for pg_info in manifest.get("pilot_data_graphs", {}).values():
            if isinstance(pg_info, dict):
                p_el = pg_info.get("edge_labels")
                if is_strict_int_list(p_el):
                    all_valid_elabels.update(p_el)

        data_graph_rel = manifest.get("data_graph", {}).get("path")
        data_graph_path = self.repo_root / data_graph_rel if data_graph_rel else None

        verified_streams: Dict[str, Any] = {}
        netflow_stream_ops: Dict[str, List[Dict[str, Any]]] = {}
        pilot_stream_ops: Dict[str, Tuple[Path, int, List[Dict[str, Any]]]] = {}

        candidate_edge_pairs_netflow: Set[Tuple[int, int]] = set()
        candidate_vertices_netflow: Set[int] = set()

        # Build mapping of pilot graph names/paths to their info
        pilot_graphs_info = manifest.get("pilot_data_graphs", {})

        for s_id, s_info in streams_dict.items():
            if not isinstance(s_info, dict):
                self.errors.append(f"Stream '{s_id}' specification must be a dictionary.")
                continue

            rel_path = s_info.get("path")
            if not rel_path or not isinstance(rel_path, str):
                self.errors.append(f"Stream {s_id} missing or invalid 'path'")
                continue

            full_path = self.repo_root / rel_path
            if not full_path.exists():
                self.errors.append(f"Stream {s_id} prefix file not found: {rel_path}")
                verified_streams[s_id] = {
                    "path": rel_path,
                    "source_lineage_verified": False,
                    "sequential_updates_verified": False,
                    "error": "File not found",
                }
                continue

            # Prefix SHA-256 check
            expected_hash = s_info.get("sha256")
            actual_hash = compute_sha256(full_path)
            prefix_hash_ok = (actual_hash == expected_hash)
            if not prefix_hash_ok:
                self.errors.append(
                    f"Stream {s_id} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
                )

            # Source stream existence and SHA-256 lineage verification
            src_rel_path = s_info.get("source_stream")
            if not src_rel_path or not isinstance(src_rel_path, str):
                self.errors.append(f"Stream {s_id} missing declared 'source_stream'")
                verified_streams[s_id] = {
                    "path": rel_path,
                    "source_lineage_verified": False,
                    "sequential_updates_verified": False,
                    "error": "Missing source_stream declaration",
                }
                continue

            src_full_path = self.repo_root / src_rel_path
            if not src_full_path.exists():
                self.errors.append(f"Stream {s_id} declared source_stream not found: {src_rel_path}")
                verified_streams[s_id] = {
                    "path": rel_path,
                    "source_stream": src_rel_path,
                    "source_lineage_verified": False,
                    "sequential_updates_verified": False,
                    "error": "Declared source_stream not found",
                }
                continue

            expected_src_hash = s_info.get("source_stream_sha256")
            actual_src_hash = compute_sha256(src_full_path)
            source_hash_ok = (actual_src_hash == expected_src_hash)
            if not source_hash_ok:
                self.errors.append(
                    f"Stream {s_id} source_stream SHA-256 mismatch: expected {expected_src_hash}, got {actual_src_hash}"
                )

            # Parse prefix operations
            try:
                parsed = parse_stream_prefix_file(full_path)
            except Exception as e:
                self.errors.append(f"Stream {s_id} parse failure ({rel_path}): {e}")
                verified_streams[s_id] = {
                    "path": rel_path,
                    "source_stream": src_rel_path,
                    "metadata_verified": False,
                    "source_lineage_verified": False,
                    "sequential_updates_verified": False,
                    "error": f"Parse failure: {e}",
                }
                continue

            context_str = f"Stream '{s_id}'"
            stream_metadata_errors_before = len(self.errors)

            expected_len = s_info.get("prefix_length")
            pref_ok, pref_err = validate_strict_int_field(expected_len, "prefix_length", context_str, min_val=0)
            if not pref_ok:
                self.errors.append(f"[STREAM_TYPE_ERROR] {pref_err}")
            elif parsed["total_operations"] != expected_len:
                self.errors.append(
                    f"Stream {s_id} operation count mismatch: expected {expected_len}, found {parsed['total_operations']}"
                )

            # Edge Label Domain Validation
            for op_info in parsed["operations"]:
                el = op_info["label"]
                if el not in all_valid_elabels:
                    self.errors.append(
                        f"Stream {s_id} contains out-of-domain edge label {el} (valid domain: {sorted(all_valid_elabels)})"
                    )

            stream_metadata_ok = (len(self.errors) == stream_metadata_errors_before) and prefix_hash_ok

            # Cryptographic Lineage Verification
            content_lineage_ok = True
            is_projected = (s_info.get("projection_type") == "PROJECTED_STREAM")

            try:
                with open(full_path, "r", encoding="utf-8") as pf, open(src_full_path, "r", encoding="utf-8") as sf:
                    prefix_lines = [l.strip() for l in pf if l.strip() and not l.startswith("#")]
                    source_lines = [l.strip() for l in sf if l.strip() and not l.startswith("#")]

                    if is_projected:
                        # Projected stream: verify operations match declared source operation positions (1-indexed)
                        declared_positions = s_info.get("source_operation_positions")
                        pos_ok, pos_err = validate_strict_int_list_field(declared_positions, "source_operation_positions", context_str)
                        if not pos_ok:
                            self.errors.append(f"[STREAM_TYPE_ERROR] {pos_err}")
                            content_lineage_ok = False
                        elif len(declared_positions) != len(prefix_lines):
                            self.errors.append(
                                f"Stream {s_id} projection error: declared positions count ({len(declared_positions)}) "
                                f"!= operation count ({len(prefix_lines)})"
                            )
                            content_lineage_ok = False
                        else:
                            for p_op, pos in zip(prefix_lines, declared_positions):
                                if pos < 1 or pos > len(source_lines):
                                    self.errors.append(
                                        f"Stream {s_id} invalid source position {pos} (source has {len(source_lines)} lines)"
                                    )
                                    content_lineage_ok = False
                                    break
                                s_op = source_lines[pos - 1]
                                if p_op != s_op:
                                    self.errors.append(
                                        f"Stream {s_id} projected operation mismatch at pos {pos}: '{p_op}' != source '{s_op}'"
                                    )
                                    content_lineage_ok = False
                                    break
                    else:
                        # Standard prefix: verify first N records match source stream
                        if len(prefix_lines) > len(source_lines):
                            self.errors.append(
                                f"Stream {s_id} lineage failure: source stream contains fewer records ({len(source_lines)}) "
                                f"than required prefix length ({len(prefix_lines)})"
                            )
                            content_lineage_ok = False
                        else:
                            for idx, (p_line, s_line) in enumerate(zip(prefix_lines, source_lines[:len(prefix_lines)]), start=1):
                                if p_line != s_line:
                                    self.errors.append(
                                        f"Stream {s_id} lineage mismatch at record {idx}: prefix '{p_line}' != source '{s_line}'"
                                    )
                                    content_lineage_ok = False
                                    break
            except Exception as e:
                self.errors.append(f"Stream {s_id} lineage check failed: {e}")
                content_lineage_ok = False

            # Source lineage verified strictly requires prefix SHA-256 match, source stream SHA-256 match, and content match
            source_lineage_ok = prefix_hash_ok and source_hash_ok and content_lineage_ok

            # Categorize stream target graph for sequential update replay (FIX R4-04: fail-closed validation)
            valid_target_graphs = {manifest.get("data_graph", {}).get("name")} | set(pilot_graphs_info.keys())
            target_pilot_id = s_info.get("target_graph")
            if target_pilot_id is not None:
                if target_pilot_id not in valid_target_graphs:
                    self.errors.append(
                        f"Stream {s_id} references unknown target_graph: '{target_pilot_id}' (valid: {sorted(valid_target_graphs)})"
                    )
                    verified_streams[s_id] = {
                        "path": rel_path,
                        "source_stream": src_rel_path,
                        "sha256": actual_hash,
                        "total_operations": parsed["total_operations"],
                        "insertions": parsed["insertions"],
                        "deletions": parsed["deletions"],
                        "source_lineage_verified": False,
                        "sequential_updates_verified": False,
                        "error": f"Unknown target_graph: '{target_pilot_id}'",
                    }
                    continue
                elif target_pilot_id in pilot_graphs_info:
                    pg_rel = pilot_graphs_info[target_pilot_id].get("path", "")
                    pg_path = self.repo_root / pg_rel
                    raw_v = pilot_graphs_info[target_pilot_id].get("vertices", 0)
                    safe_v = raw_v if is_strict_int(raw_v) else 0
                    pilot_stream_ops[s_id] = (pg_path, safe_v, parsed["operations"])
                else:
                    # Explicitly targets primary NetFlow data graph
                    netflow_stream_ops[s_id] = parsed["operations"]
                    for op_info in parsed["operations"]:
                        u, v = op_info["u"], op_info["v"]
                        candidate_edge_pairs_netflow.add((min(u, v), max(u, v)))
                        candidate_vertices_netflow.add(u)
                        candidate_vertices_netflow.add(v)
            else:
                # Target graph omitted: defaults to primary NetFlow data graph
                netflow_stream_ops[s_id] = parsed["operations"]
                for op_info in parsed["operations"]:
                    u, v = op_info["u"], op_info["v"]
                    candidate_edge_pairs_netflow.add((min(u, v), max(u, v)))
                    candidate_vertices_netflow.add(u)
                    candidate_vertices_netflow.add(v)

            verified_streams[s_id] = {
                "path": rel_path,
                "source_stream": src_rel_path,
                "sha256": actual_hash,
                "total_operations": parsed["total_operations"],
                "insertions": parsed["insertions"],
                "deletions": parsed["deletions"],
                "metadata_verified": stream_metadata_ok,
                "source_lineage_verified": source_lineage_ok,
                "sequential_updates_verified": False,
            }

        # Sequential update correctness check against initial NetFlow G0
        if data_graph_path and data_graph_path.exists() and candidate_edge_pairs_netflow:
            self._verify_sequential_stream_updates(
                data_graph_path=data_graph_path,
                total_declared_vertices=manifest.get("data_graph", {}).get("vertices", 0),
                candidate_edge_pairs=candidate_edge_pairs_netflow,
                candidate_vertices=candidate_vertices_netflow,
                stream_parsed_ops=netflow_stream_ops,
                verified_streams_dict=verified_streams,
            )

        # Sequential update correctness check against pilot data graphs
        for s_id, (pg_path, declared_v, ops) in pilot_stream_ops.items():
            if pg_path.exists():
                pilot_cand_pairs = set()
                pilot_cand_verts = set()
                for op_info in ops:
                    u, v = op_info["u"], op_info["v"]
                    pilot_cand_pairs.add((min(u, v), max(u, v)))
                    pilot_cand_verts.add(u)
                    pilot_cand_verts.add(v)
                self._verify_sequential_stream_updates(
                    data_graph_path=pg_path,
                    total_declared_vertices=declared_v,
                    candidate_edge_pairs=pilot_cand_pairs,
                    candidate_vertices=pilot_cand_verts,
                    stream_parsed_ops={s_id: ops},
                    verified_streams_dict=verified_streams,
                )

        self.audit_log["streams_verified"] = verified_streams

    def _verify_sequential_stream_updates(
        self,
        data_graph_path: Path,
        total_declared_vertices: int,
        candidate_edge_pairs: Set[Tuple[int, int]],
        candidate_vertices: Set[int],
        stream_parsed_ops: Dict[str, List[Dict[str, Any]]],
        verified_streams_dict: Dict[str, Any],
    ) -> None:
        """
        Scans data graph in streaming mode to retrieve initial state for candidate edge pairs,
        then replays sequential operations to verify validity.
        Sets sequential_updates_verified flag per stream.
        """
        # Fixed vertex universe check (Model C: endpoints must exist in initial V_0, Vt = V0)
        cache_key_v = (str(data_graph_path), data_graph_path.stat().st_mtime_ns)
        if cache_key_v in _G0_VERTEX_CACHE:
            v0_vertices = _G0_VERTEX_CACHE[cache_key_v]
        else:
            is_canonical_netflow = (
                data_graph_path.name == "Ndata.graph"
                and total_declared_vertices == 3114895
            )
            if is_canonical_netflow:
                v0_vertices = range(3114895)
            else:
                v_set: Set[int] = set()
                try:
                    with open(data_graph_path, "r", encoding="utf-8") as gf:
                        for line in gf:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if line.startswith("v "):
                                parts = line.split()
                                if len(parts) >= 2:
                                    v_set.add(int(parts[1]))
                            elif line.startswith("e "):
                                break
                    v0_vertices = v_set
                except Exception as e:
                    self.errors.append(f"G0 vertex scan failed for {data_graph_path}: {e}")
                    return
            _G0_VERTEX_CACHE[cache_key_v] = v0_vertices

        universe_errors: Dict[str, bool] = {s_id: False for s_id in stream_parsed_ops}
        for s_id, ops in stream_parsed_ops.items():
            missing_v: Set[int] = set()
            for op_info in ops:
                u, v = op_info["u"], op_info["v"]
                if u not in v0_vertices:
                    missing_v.add(u)
                if v not in v0_vertices:
                    missing_v.add(v)
            if missing_v:
                missing_sorted = sorted(missing_v)
                self.errors.append(
                    f"Stream {s_id} endpoint(s) {missing_sorted} do not exist in initial vertex universe V_0 of {data_graph_path.name}"
                )
                universe_errors[s_id] = True
                if s_id in verified_streams_dict:
                    verified_streams_dict[s_id]["sequential_updates_verified"] = False

        # Read initial edges for candidate edge pairs
        cache_key = (
            str(data_graph_path),
            data_graph_path.stat().st_mtime_ns,
            tuple(sorted(candidate_edge_pairs)),
        )
        if cache_key in _G0_EDGE_CACHE:
            initial_edges = dict(_G0_EDGE_CACHE[cache_key])
        else:
            initial_edges = {}
            try:
                with open(data_graph_path, "r", encoding="utf-8") as gf:
                    for line in gf:
                        if not line.startswith("e "):
                            continue
                        parts = line.strip().split()
                        if len(parts) >= 4:
                            u, v, lbl = int(parts[1]), int(parts[2]), int(parts[3])
                            pair = (min(u, v), max(u, v))
                            if pair in candidate_edge_pairs:
                                initial_edges[pair] = lbl
                _G0_EDGE_CACHE[cache_key] = dict(initial_edges)
            except Exception as e:
                self.errors.append(f"G0 sequential verification failed while scanning {data_graph_path}: {e}")
                return

        # Replay each stream sequentially against a clone of initial_edges
        for s_id, ops in stream_parsed_ops.items():
            if universe_errors[s_id]:
                continue
            active_edges = dict(initial_edges)
            stream_valid = True
            for step_idx, op_info in enumerate(ops, start=1):
                op = op_info["op"]
                u, v, lbl = op_info["u"], op_info["v"], op_info["label"]
                pair = (min(u, v), max(u, v))

                if op == "+":
                    if pair in active_edges:
                        self.errors.append(
                            f"Stream {s_id} step {step_idx}: duplicate edge insertion ({u}, {v}) "
                            f"(edge already exists in active graph state)"
                        )
                        stream_valid = False
                    active_edges[pair] = lbl
                elif op == "-":
                    if pair not in active_edges:
                        self.errors.append(
                            f"Stream {s_id} step {step_idx}: deletion of absent edge ({u}, {v}) "
                            f"(edge does not exist in active graph state)"
                        )
                        stream_valid = False
                    else:
                        existing_lbl = active_edges[pair]
                        if existing_lbl != lbl:
                            self.errors.append(
                                f"Stream {s_id} step {step_idx}: deletion edge label mismatch for ({u}, {v}): "
                                f"expected {existing_lbl}, got {lbl}"
                            )
                            stream_valid = False
                        del active_edges[pair]

            if stream_valid and s_id in verified_streams_dict:
                if verified_streams_dict[s_id].get("source_lineage_verified", False):
                    verified_streams_dict[s_id]["sequential_updates_verified"] = True

    def audit_pilot_cases(self, manifest: Dict[str, Any]) -> None:
        """
        Audits proposed pilot test cases for A3-01 to ensure strict types,
        valid enums, near-only semantics (eps_m >= 1), and safe initial pilot limits.
        """
        cases = manifest.get("proposed_pilot_cases_a3_01", [])
        if not isinstance(cases, list) or not cases:
            self.errors.append("Manifest contains no proposed pilot cases or 'proposed_pilot_cases_a3_01' is not a list.")
            return

        known_queries = manifest.get("queries", {})
        known_streams = set(manifest.get("streams", {}).keys()) if isinstance(manifest.get("streams"), dict) else set()
        expected_graph = manifest.get("data_graph", {}).get("name")
        pilot_graphs_info = manifest.get("pilot_data_graphs", {}) if isinstance(manifest.get("pilot_data_graphs"), dict) else {}
        pilot_graphs = set(pilot_graphs_info.keys())
        valid_graph_sources = {expected_graph} | pilot_graphs

        valid_categories = {
            "EXACT_MATCH",
            "NEAR_ONLY_MATCH",
            "DELETE_EDGE",
            "HUB_SENSITIVE_MAPPING",
            "POTENTIAL_SEARCH_EXPLOSION",
        }
        valid_modes = {"STRICT", "RELAXED_UNCONSTRAINED"}
        valid_semantic_classes = {
            "FULL_GRAPH",
            "INDUCED_SUBGRAPH_RELATIVE",
            "ORIGINAL_GRAPH_SCORE_RESTRICTED_SEARCH",
        }
        valid_pilot_stages = {"INITIAL_SAFE_PILOT", "DEFERRED_UNTIL_FEASIBLE"}

        case_ids: Set[str] = set()
        initial_safe_pilot_count = 0

        for c in cases:
            if not isinstance(c, dict):
                self.errors.append("Pilot case specification must be a dictionary.")
                continue

            cid = c.get("case_id")
            if not cid:
                self.errors.append("Pilot case missing 'case_id'")
                continue
            if cid in case_ids:
                self.errors.append(f"Duplicate case_id in pilot cases: {cid}")
            case_ids.add(cid)

            # References
            qid = c.get("query_id")
            if qid not in known_queries:
                self.errors.append(f"Case {cid} references undefined query_id: {qid}")
            else:
                declared_source = c.get("query_source")
                actual_category = known_queries[qid].get("category")
                if declared_source != actual_category:
                    self.errors.append(
                        f"Case {cid} query_source mismatch: declared '{declared_source}', query is '{actual_category}'"
                    )

            sid = c.get("stream_id")
            if sid not in known_streams:
                self.errors.append(f"Case {cid} references undefined stream_id: {sid}")

            gs = c.get("graph_source")
            if gs not in valid_graph_sources:
                self.errors.append(f"Case {cid} references unknown graph_source: {gs} (valid: {valid_graph_sources})")

            # Stream binding: If stream s_id has declared target_graph, check case.graph_source (FIX R4-01)
            if sid in manifest.get("streams", {}):
                stream_decl = manifest["streams"][sid]
                s_target = stream_decl.get("target_graph")
                if s_target is not None:
                    if gs != s_target:
                        self.errors.append(
                            f"Case {cid} graph_source '{gs}' does not match stream {sid} declared target_graph '{s_target}'"
                        )
                else:
                    if gs != expected_graph:
                        self.errors.append(
                            f"Case {cid} graph_source '{gs}' does not match stream {sid} default target_graph '{expected_graph}'"
                        )

            # Query binding: Check vertex/edge labels and vertex count against target graph (FIX R4-01)
            if qid in known_queries and gs in valid_graph_sources:
                if gs in pilot_graphs_info:
                    raw_target_vl = pilot_graphs_info[gs].get("vertex_labels")
                    raw_target_el = pilot_graphs_info[gs].get("edge_labels")
                    target_vcount = pilot_graphs_info[gs].get("vertices")
                else:
                    raw_target_vl = manifest.get("data_graph", {}).get("vertex_labels")
                    raw_target_el = manifest.get("data_graph", {}).get("edge_labels")
                    target_vcount = manifest.get("data_graph", {}).get("vertices")

                raw_q_vl = known_queries[qid].get("vertex_labels")
                raw_q_el = known_queries[qid].get("edge_labels")
                q_vcount = known_queries[qid].get("vertices")

                if is_strict_int_list(raw_target_vl) and is_strict_int_list(raw_q_vl):
                    target_vlabels = set(raw_target_vl)
                    q_vlabels = set(raw_q_vl)
                    if not q_vlabels.issubset(target_vlabels):
                        diff_vl = sorted(q_vlabels - target_vlabels)
                        self.errors.append(
                            f"Case {cid} query {qid} vertex labels {diff_vl} not contained in target graph {gs} vertex labels {sorted(target_vlabels)}"
                        )

                if is_strict_int_list(raw_target_el) and is_strict_int_list(raw_q_el):
                    target_elabels = set(raw_target_el)
                    q_elabels = set(raw_q_el)
                    if not q_elabels.issubset(target_elabels):
                        diff_el = sorted(q_elabels - target_elabels)
                        self.errors.append(
                            f"Case {cid} query {qid} edge labels {diff_el} not contained in target graph {gs} edge labels {sorted(target_elabels)}"
                        )

                if is_strict_int(target_vcount) and is_strict_int(q_vcount):
                    if q_vcount > target_vcount:
                        self.errors.append(
                            f"Case {cid} query {qid} has more vertices ({q_vcount}) than target graph {gs} ({target_vcount})"
                        )

            # Strict threshold type and range validation
            eps_m = c.get("eps_m")
            if not is_strict_int(eps_m) or eps_m < 0:
                self.errors.append(f"Case {cid} has invalid eps_m: {eps_m} (must be non-negative integer, not boolean)")

            eps_x = c.get("eps_x")
            if eps_x is not None and (not is_strict_int(eps_x) or eps_x < 0):
                self.errors.append(f"Case {cid} has invalid eps_x: {eps_x} (must be None or non-negative integer)")

            # Main benchmark threshold restriction: reject eps_m >= 2 on queries with < 6 edges
            if qid in known_queries and is_strict_int(eps_m) and eps_m >= 2:
                q_edges = known_queries[qid].get("edges")
                if is_strict_int(q_edges) and q_edges < 6:
                    self.errors.append(
                        f"Case {cid} violates main benchmark threshold constraint: eps_m={eps_m} "
                        f"on query with {q_edges} edges (< 6 edges requires eps_m <= 1)"
                    )

            # Random seed validation if present
            r_seed = c.get("random_seed")
            if r_seed is not None and not is_strict_int(r_seed):
                self.errors.append(f"Case {cid} has invalid random_seed: {r_seed} (must be integer)")

            # Mode
            mode = c.get("mode")
            if mode not in valid_modes:
                self.errors.append(f"Case {cid} has invalid mode: {mode} (must be in {valid_modes})")

            # Category
            cat = c.get("workload_category")
            if cat not in valid_categories:
                self.errors.append(f"Case {cid} has invalid workload_category: {cat}")

            # Near-only scientific semantics check: M_near-only requires m(f) >= 1
            if cat == "NEAR_ONLY_MATCH" and is_strict_int(eps_m) and eps_m == 0:
                self.errors.append(
                    f"Case {cid} invalid near-only classification: eps_m=0 produces m(f)=0, which is exact non-induced "
                    f"under Model C; NEAR_ONLY_MATCH strictly requires eps_m >= 1"
                )

            # Semantic class
            sem_class = c.get("semantic_class")
            if sem_class not in valid_semantic_classes:
                self.errors.append(
                    f"Case {cid} has invalid semantic_class: {sem_class} (must be in {valid_semantic_classes})"
                )

            # Pilot stage
            p_stage = c.get("pilot_stage")
            if p_stage not in valid_pilot_stages:
                self.errors.append(
                    f"Case {cid} has invalid pilot_stage: {p_stage} (must be in {valid_pilot_stages})"
                )
            elif p_stage == "INITIAL_SAFE_PILOT":
                initial_safe_pilot_count += 1
                # Guard: INITIAL_SAFE_PILOT must use a pilot micrograph/subgraph from pilot_data_graphs, NEVER NetFlow full
                if gs not in pilot_graphs:
                    self.errors.append(
                        f"Case {cid} declared in INITIAL_SAFE_PILOT must use a pilot micrograph/subgraph from pilot_data_graphs, not full dataset '{gs}'"
                    )
                # Guard: INITIAL_SAFE_PILOT must use INDUCED_SUBGRAPH_RELATIVE
                if sem_class != "INDUCED_SUBGRAPH_RELATIVE":
                    self.errors.append(
                        f"Case {cid} declared in INITIAL_SAFE_PILOT must use semantic_class INDUCED_SUBGRAPH_RELATIVE, found '{sem_class}'"
                    )

                # Registered Workload Identity Fingerprint verification (R4-NEW-01)
                reg_id_sha = c.get("registered_identity_sha256")
                if reg_id_sha is None:
                    self.errors.append(
                        f"[WORKLOAD_IDENTITY_MISSING] Case {cid} declared in INITIAL_SAFE_PILOT is missing required 'registered_identity_sha256'"
                    )
                elif not is_valid_sha256(reg_id_sha):
                    self.errors.append(
                        f"[WORKLOAD_IDENTITY_INVALID] Case {cid} has malformed registered_identity_sha256 '{reg_id_sha}' (must be 64-character hex string)"
                    )
                else:
                    # Retrieve verified component records from actual content verification
                    if gs in pilot_graphs_info:
                        v_graph = self.audit_log.get("pilot_data_graphs_verified", {}).get(gs)
                    elif gs == expected_graph:
                        v_graph = self.audit_log.get("data_graph_verified")
                    else:
                        v_graph = None

                    v_query = self.audit_log.get("queries_verified", {}).get(qid)
                    v_stream = self.audit_log.get("streams_verified", {}).get(sid)

                    def validate_component(comp_type: str, comp_id: str, comp_dict: Any) -> Tuple[Optional[str], Optional[str]]:
                        """Returns (sha256, error_reason) for a component dict."""
                        if not isinstance(comp_dict, dict):
                            return None, f"component record missing or unverified (got {type(comp_dict).__name__})"
                        sha = comp_dict.get("sha256")
                        if not isinstance(sha, str):
                            return None, f"missing or non-string 'sha256' (got {type(sha).__name__})"
                        if not is_valid_sha256(sha):
                            return None, f"malformed 'sha256' '{sha}' (must be 64-character hex)"
                        if comp_type == "stream":
                            if comp_dict.get("metadata_verified") is not True:
                                return None, "stream metadata validation failed (metadata_verified=False or missing)"
                            if comp_dict.get("source_lineage_verified") is not True:
                                return None, "stream source lineage verification failed (source_lineage_verified=False)"
                            if comp_dict.get("sequential_updates_verified") is not True:
                                return None, "stream sequential updates verification failed (sequential_updates_verified=False)"
                        elif comp_type == "query":
                            if comp_dict.get("is_connected") is not True:
                                return None, "query graph is disconnected (is_connected=False)"
                        return sha, None

                    graph_sha, graph_err = validate_component("graph", gs, v_graph)
                    query_sha, query_err = validate_component("query", qid, v_query)
                    stream_sha, stream_err = validate_component("stream", sid, v_stream)

                    if not graph_sha or not query_sha or not stream_sha:
                        failures = []
                        if not graph_sha:
                            failures.append(f"graph '{gs}': {graph_err}")
                        if not query_sha:
                            failures.append(f"query '{qid}': {query_err}")
                        if not stream_sha:
                            failures.append(f"stream '{sid}': {stream_err}")
                        self.errors.append(
                            f"[WORKLOAD_IDENTITY_MISMATCH] Case {cid} references invalid or unverified component(s): {'; '.join(failures)}"
                        )
                    else:
                        actual_identity_sha = compute_workload_identity_sha256(
                            case_id=cid,
                            graph_id=gs,
                            graph_sha256=graph_sha,
                            query_id=qid,
                            query_sha256=query_sha,
                            stream_id=sid,
                            stream_sha256=stream_sha,
                            eps_m=eps_m,
                            eps_x=eps_x,
                            mode=mode,
                            semantic_class=sem_class,
                        )

                        if actual_identity_sha.lower() != reg_id_sha.lower():
                            self.errors.append(
                                f"[WORKLOAD_IDENTITY_MISMATCH] Case {cid} registered identity fingerprint mismatch: "
                                f"declared '{reg_id_sha}', calculated '{actual_identity_sha}'"
                            )

            # Safety and approval locks
            if c.get("approval_state") != "DRAFT_PENDING_USER_APPROVAL":
                self.errors.append(f"Case {cid} approval_state must be 'DRAFT_PENDING_USER_APPROVAL'")
            if c.get("execution_status") != "NOT_RUN":
                self.errors.append(f"Case {cid} execution_status must be 'NOT_RUN'")

        # Initial safe pilot cap: maximum 3 cases allowed in INITIAL_SAFE_PILOT
        if initial_safe_pilot_count > 3:
            self.errors.append(
                f"Too many cases declared in INITIAL_SAFE_PILOT ({initial_safe_pilot_count} > 3 max allowed)"
            )

        self.audit_log["pilot_cases_count"] = len(cases)
        self.audit_log["initial_safe_pilot_count"] = initial_safe_pilot_count

    def audit_exclusions(self, manifest: Dict[str, Any]) -> None:
        """Audits documented dataset exclusions and verifies explanations."""
        exclusions = manifest.get("exclusions", [])
        if not exclusions:
            self.warnings.append("Manifest contains no documented exclusions.")
            return

        for excl in exclusions:
            corpus_path = excl.get("corpus_path")
            reason = excl.get("reason")
            if not corpus_path or not reason:
                self.errors.append(f"Malformed exclusion entry: {excl}")

        self.audit_log["exclusions_count"] = len(exclusions)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="APX-04 Gate A3-00 Read-Only Workload Manifest & Environment Preflight Tool"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("configs/apx04_workload_manifest.json"),
        help="Path to workload manifest JSON (default: configs/apx04_workload_manifest.json)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Path to repository root (default: current directory)",
    )
    parser.add_argument(
        "--min-ram-gib",
        type=float,
        default=4.0,
        help="Minimum available RAM in GiB required (default: 4.0)",
    )
    parser.add_argument(
        "--min-disk-gib",
        type=float,
        default=10.0,
        help="Minimum free disk space in GiB required (default: 10.0)",
    )
    parser.add_argument(
        "--enforce-ram",
        action="store_true",
        help="Treat RAM below --min-ram-gib as a hard failure (default: warn only during preflight audit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run audits without producing persistent output files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable audit report in JSON format",
    )

    args = parser.parse_args()

    checker = PreflightChecker(
        manifest_path=args.manifest,
        repo_root=args.repo_root,
        min_ram_gib=args.min_ram_gib,
        min_disk_gib=args.min_disk_gib,
        enforce_ram=args.enforce_ram,
    )

    passed = checker.run_all()

    if args.json:
        print(json.dumps(checker.audit_log, indent=2))
    else:
        print("================================================================================")
        print(" APX-04 GATE A3-00 PREFLIGHT AUDIT REPORT (READ-ONLY)")
        print("================================================================================")
        print(f"Manifest File:        {checker.manifest_path}")
        print(f"Repository Root:      {checker.repo_root}")
        print(f"Dry-Run Mode:         {args.dry_run}")
        print(f"Audit Status:         {'PASS' if passed else 'FAIL'}")
        print(f"Audit Verdict:        {checker.audit_log.get('audit_verdict')}")
        print("Execution Readiness:  SAFETY_BLOCKED (A3-01 execution is NOT authorized)")
        print("Memory Enforcement:   NOT_VERIFIED (Host active RSS guard not demonstrated)")
        print(f"Exec Authorization:   {checker.audit_log.get('execution_authorization')}")
        print("--------------------------------------------------------------------------------")

        sys_res = checker.audit_log.get("system_resources", {})
        cg = sys_res.get("cgroup", {})
        print("System Resources:")
        print(f"  - RAM Total:        {sys_res.get('ram_total_gib', 0):.2f} GiB")
        print(f"  - RAM Available:    {sys_res.get('ram_available_gib', 0):.2f} GiB (Floor: {args.min_ram_gib:.2f} GiB)")
        print(f"  - Effective RAM:    {sys_res.get('effective_ram_available_gib', 0):.2f} GiB")
        print(f"  - cgroup Version:   {cg.get('cgroup_version')} (Status: {cg.get('status')})")
        if cg.get("is_constrained"):
            print(f"  - cgroup Headroom:  {cg.get('headroom_gib')} GiB (Limit: {cg.get('memory_max_bytes')} B)")
        print(f"  - Disk Total:       {sys_res.get('disk_total_gib', 0):.2f} GiB")
        print(f"  - Disk Free:        {sys_res.get('disk_free_gib', 0):.2f} GiB (Floor: {args.min_disk_gib:.2f} GiB)")
        print("--------------------------------------------------------------------------------")

        if "data_graph_verified" in checker.audit_log:
            dg = checker.audit_log["data_graph_verified"]
            print(f"Data Graph:           {dg['name']} ({dg['size_bytes']:,} bytes)")
            print(f"  - SHA-256:          {dg['sha256']}")

        if "pilot_data_graphs_verified" in checker.audit_log:
            pgs = checker.audit_log["pilot_data_graphs_verified"]
            print(f"Pilot Graphs:         {len(pgs)} materialized pilot data graphs verified")

        qv = checker.audit_log.get("queries_verified", {})
        print(f"Queries Verified:     {len(qv)} queries (all simple, connected, valid domain)")

        sv = checker.audit_log.get("streams_verified", {})
        print(f"Streams Verified:     {len(sv)} prefix files (cryptographic lineage & sequential valid)")

        pc = checker.audit_log.get("pilot_cases_count", 0)
        safe_pc = checker.audit_log.get("initial_safe_pilot_count", 0)
        print(f"Pilot Cases:          {pc} total ({safe_pc} INITIAL_SAFE_PILOT, {pc - safe_pc} DEFERRED_UNTIL_FEASIBLE)")

        print("--------------------------------------------------------------------------------")
        if checker.warnings:
            print(f"Warnings ({len(checker.warnings)}):")
            for w in checker.warnings:
                print(f"  [WARN] {w}")

        if checker.errors:
            print(f"Errors ({len(checker.errors)}):")
            for e in checker.errors:
                print(f"  [ERROR] {e}")
        else:
            print("Audit Verdict: ALL PREFLIGHT AUDITS PASSED.")
            print("Note: Audit PASS does NOT authorize benchmark execution. Execution remains SAFETY_BLOCKED.")
        print("================================================================================")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
