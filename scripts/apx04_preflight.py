#!/usr/bin/env python3
"""
scripts/apx04_preflight.py

APX-04 Gate A3-00: Workload Manifest & Environment Read-Only Preflight Tool.
Dự án: Continuous Subgraph Matching (CSM) — Seminar RapidFlow
Issue: #20 (Gate A3-00)

FAIL-CLOSED BEHAVIOR:
- Strictly read-only: does NOT execute any subgraph matching algorithm.
- Does NOT mutate or write benchmark results.
- Validates manifest schema, cryptographic SHA-256 hashes, query connectivity,
  stream prefix formats, pilot case configurations, and system resources.
- Returns exit code 0 if all checks pass; returns exit code 1 if any failure occurs.
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
from typing import Any, Dict, List, Optional, Set, Tuple


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 checksum of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_system_resources(target_dir: Path = Path(".")) -> Dict[str, Any]:
    """Inspects host available RAM and free disk space without external dependencies."""
    # RAM check from /proc/meminfo (Linux)
    mem_total_kib = 0
    mem_available_kib = 0
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        if key == "MemTotal":
                            mem_total_kib = int(val_str)
                        elif key == "MemAvailable":
                            mem_available_kib = int(val_str)
        except Exception as e:
            pass

    ram_total_gib = mem_total_kib / (1024.0 * 1024.0) if mem_total_kib > 0 else 0.0
    ram_available_gib = mem_available_kib / (1024.0 * 1024.0) if mem_available_kib > 0 else 0.0

    # Disk check via shutil.disk_usage
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
        "disk_total_gib": round(disk_total_gib, 2),
        "disk_free_gib": round(disk_free_gib, 2),
    }


def parse_query_file(file_path: Path) -> Dict[str, Any]:
    """Parses a query graph file and verifies structure, labels, and connectivity."""
    vertices: Dict[int, int] = {}
    edges: List[Tuple[int, int, int]] = []
    adj: Dict[int, List[int]] = {}

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
                if len(tokens) < 3:
                    raise ValueError(f"Line {line_num}: malformed vertex line: {line}")
                vid = int(tokens[1])
                vlabel = int(tokens[2])
                vertices[vid] = vlabel
                if vid not in adj:
                    adj[vid] = []
            elif tag == "e":
                if len(tokens) < 4:
                    raise ValueError(f"Line {line_num}: malformed edge line: {line}")
                u = int(tokens[1])
                v = int(tokens[2])
                elabel = int(tokens[3])
                edges.append((u, v, elabel))
                adj.setdefault(u, []).append(v)
                adj.setdefault(v, []).append(u)
            else:
                raise ValueError(f"Line {line_num}: unrecognized tag '{tag}'")

    if not vertices:
        raise ValueError(f"Query file {file_path} contains 0 vertices.")

    # Check graph connectivity via BFS from arbitrary vertex
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
    """Parses a stream prefix file and verifies operation types and line counts."""
    operations: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            tag = tokens[0]
            if tag == "v":
                # Vertex update or declaration: v <u_label> <v_label>
                operations.append({"type": "v", "tokens": tokens[1:]})
            elif tag == "-e":
                # Deletion: -e <u> <v> <label>
                if len(tokens) < 4:
                    raise ValueError(f"Line {line_num}: malformed deletion line: {line}")
                u = int(tokens[1])
                v = int(tokens[2])
                elabel = int(tokens[3])
                operations.append({"type": "e", "op": "-", "u": u, "v": v, "label": elabel})
            elif tag == "e":
                if len(tokens) == 4:
                    # Insertion: e <u> <v> <label>
                    u = int(tokens[1])
                    v = int(tokens[2])
                    elabel = int(tokens[3])
                    operations.append({"type": "e", "op": "+", "u": u, "v": v, "label": elabel})
                elif len(tokens) >= 5 and tokens[1] in ("+", "-"):
                    # Explicit op format: e + <u> <v> <label> or e - <u> <v> <label>
                    op = tokens[1]
                    u = int(tokens[2])
                    v = int(tokens[3])
                    elabel = int(tokens[4])
                    operations.append({"type": "e", "op": op, "u": u, "v": v, "label": elabel})
                else:
                    raise ValueError(f"Line {line_num}: malformed edge update: {line}")
            else:
                raise ValueError(f"Line {line_num}: unrecognized stream tag '{tag}'")

    insert_count = sum(1 for o in operations if o.get("op") == "+")
    delete_count = sum(1 for o in operations if o.get("op") == "-")

    return {
        "total_operations": len(operations),
        "insertions": insert_count,
        "deletions": delete_count,
    }


class PreflightChecker:
    """Coordinates all read-only audits for APX-04 Gate A3-00."""

    def __init__(
        self,
        manifest_path: Path,
        repo_root: Path,
        min_ram_gib: float = 4.0,
        min_disk_gib: float = 10.0,
        enforce_ram: bool = False,
    ):
        self.manifest_path = manifest_path.resolve()
        self.repo_root = repo_root.resolve()
        self.min_ram_gib = min_ram_gib
        self.min_disk_gib = min_disk_gib
        self.enforce_ram = enforce_ram
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.audit_log: Dict[str, Any] = {}

    def run_all(self) -> bool:
        """Executes all preflight audits. Returns True if all pass, False if any error."""
        self.errors.clear()
        self.warnings.clear()

        manifest = self.audit_manifest_schema()
        if manifest is None:
            return False

        self.audit_system_resources()
        self.audit_data_graph(manifest)
        self.audit_queries(manifest)
        self.audit_streams(manifest)
        self.audit_pilot_cases(manifest)
        self.audit_exclusions(manifest)

        self.audit_log["status"] = "PASS" if not self.errors else "FAIL"
        self.audit_log["error_count"] = len(self.errors)
        self.audit_log["warning_count"] = len(self.warnings)
        self.audit_log["errors"] = self.errors
        self.audit_log["warnings"] = self.warnings

        return len(self.errors) == 0

    def audit_manifest_schema(self) -> Optional[Dict[str, Any]]:
        """Validates workload manifest structure, draft state, and safety lock."""
        if not self.manifest_path.exists():
            self.errors.append(f"Manifest file not found: {self.manifest_path}")
            return None

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            self.errors.append(f"Failed to parse manifest JSON: {e}")
            return None

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
                self.errors.append(f"Manifest missing required root key: '{key}'")

        if manifest.get("document_type") != "apx04_workload_manifest":
            self.errors.append(f"Invalid document_type: {manifest.get('document_type')}")

        if manifest.get("status") != "DRAFT_PENDING_USER_APPROVAL":
            self.errors.append(
                f"Manifest status must be 'DRAFT_PENDING_USER_APPROVAL', found: {manifest.get('status')}"
            )

        if manifest.get("execution_authorization") is not False:
            self.errors.append(
                f"Safety violation: execution_authorization must be false in Gate A3-00 (found {manifest.get('execution_authorization')})"
            )

        self.audit_log["manifest_metadata"] = {
            "schema_version": manifest.get("schema_version"),
            "status": manifest.get("status"),
            "execution_authorization": manifest.get("execution_authorization"),
            "issue": manifest.get("issue"),
        }
        return manifest

    def audit_system_resources(self) -> None:
        """Audits host RAM and disk space against minimum requirements."""
        res = get_system_resources(self.repo_root)
        self.audit_log["system_resources"] = res

        # Check RAM
        if res["ram_available_gib"] < self.min_ram_gib:
            msg = (
                f"Available RAM ({res['ram_available_gib']:.2f} GiB) is below required minimum "
                f"({self.min_ram_gib:.2f} GiB). Total RAM: {res['ram_total_gib']:.2f} GiB."
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
        """Verifies initial data graph path and cryptographic SHA-256."""
        dg_info = manifest.get("data_graph", {})
        rel_path = dg_info.get("path")
        if not rel_path:
            self.errors.append("data_graph missing 'path'")
            return

        full_path = self.repo_root / rel_path
        if not full_path.exists():
            self.errors.append(f"Initial data graph file not found: {rel_path}")
            return

        expected_hash = dg_info.get("sha256")
        actual_hash = compute_sha256(full_path)
        if actual_hash != expected_hash:
            self.errors.append(
                f"Data graph SHA-256 mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
            )
        else:
            self.audit_log["data_graph_verified"] = {
                "name": dg_info.get("name"),
                "path": rel_path,
                "sha256": actual_hash,
                "size_bytes": full_path.stat().st_size,
            }

    def audit_queries(self, manifest: Dict[str, Any]) -> None:
        """Verifies all manifest queries: SHA-256, syntax, degree sequence, and connectivity."""
        queries_dict = manifest.get("queries", {})
        if not queries_dict:
            self.errors.append("Manifest contains no queries.")
            return

        verified_queries: Dict[str, Any] = {}
        for q_id, q_info in queries_dict.items():
            rel_path = q_info.get("path")
            if not rel_path:
                self.errors.append(f"Query {q_id} missing 'path'")
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

            # Check vertex count
            if parsed["num_vertices"] != q_info.get("vertices"):
                self.errors.append(
                    f"Query {q_id} vertex count mismatch: expected {q_info.get('vertices')}, found {parsed['num_vertices']}"
                )

            # Check edge count
            if parsed["num_edges"] != q_info.get("edges"):
                self.errors.append(
                    f"Query {q_id} edge count mismatch: expected {q_info.get('edges')}, found {parsed['num_edges']}"
                )

            # Check labels
            if parsed["vertex_labels"] != sorted(q_info.get("vertex_labels", [])):
                self.errors.append(
                    f"Query {q_id} vertex labels mismatch: expected {q_info.get('vertex_labels')}, found {parsed['vertex_labels']}"
                )
            if parsed["edge_labels"] != sorted(q_info.get("edge_labels", [])):
                self.errors.append(
                    f"Query {q_id} edge labels mismatch: expected {q_info.get('edge_labels')}, found {parsed['edge_labels']}"
                )

            # Check degree sequence
            if "degree_sequence" in q_info:
                if parsed["degree_sequence"] != q_info["degree_sequence"]:
                    self.errors.append(
                        f"Query {q_id} degree sequence mismatch: expected {q_info['degree_sequence']}, found {parsed['degree_sequence']}"
                    )

            # Check connectivity
            if not parsed["is_connected"]:
                self.errors.append(f"Query {q_id} is disconnected; CSM queries must be connected.")

            verified_queries[q_id] = {
                "path": rel_path,
                "sha256": actual_hash,
                "vertices": parsed["num_vertices"],
                "edges": parsed["num_edges"],
                "is_connected": parsed["is_connected"],
            }

        self.audit_log["queries_verified"] = verified_queries

    def audit_streams(self, manifest: Dict[str, Any]) -> None:
        """Verifies all stream prefix files: SHA-256, format, and operation counts."""
        streams_dict = manifest.get("streams", {})
        if not streams_dict:
            self.errors.append("Manifest contains no stream prefixes.")
            return

        verified_streams: Dict[str, Any] = {}
        for s_id, s_info in streams_dict.items():
            rel_path = s_info.get("path")
            if not rel_path:
                self.errors.append(f"Stream {s_id} missing 'path'")
                continue

            full_path = self.repo_root / rel_path
            if not full_path.exists():
                self.errors.append(f"Stream {s_id} prefix file not found: {rel_path}")
                continue

            expected_hash = s_info.get("sha256")
            actual_hash = compute_sha256(full_path)
            if actual_hash != expected_hash:
                self.errors.append(
                    f"Stream {s_id} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
                )
                continue

            # Parse updates
            try:
                parsed = parse_stream_prefix_file(full_path)
            except Exception as e:
                self.errors.append(f"Stream {s_id} parse failure ({rel_path}): {e}")
                continue

            expected_len = s_info.get("prefix_length")
            if parsed["total_operations"] != expected_len:
                self.errors.append(
                    f"Stream {s_id} operation count mismatch: expected {expected_len}, found {parsed['total_operations']}"
                )

            verified_streams[s_id] = {
                "path": rel_path,
                "sha256": actual_hash,
                "total_operations": parsed["total_operations"],
                "insertions": parsed["insertions"],
                "deletions": parsed["deletions"],
            }

        self.audit_log["streams_verified"] = verified_streams

    def audit_pilot_cases(self, manifest: Dict[str, Any]) -> None:
        """Audits proposed pilot test cases for A3-01 to ensure semantic validity."""
        cases = manifest.get("proposed_pilot_cases_a3_01", [])
        if not cases:
            self.errors.append("Manifest contains no proposed pilot cases.")
            return

        known_queries = set(manifest.get("queries", {}).keys())
        known_streams = set(manifest.get("streams", {}).keys())
        expected_graph = manifest.get("data_graph", {}).get("name")

        valid_categories = {
            "EXACT_MATCH",
            "NEAR_ONLY_MATCH",
            "DELETE_EDGE",
            "HUB_SENSITIVE_MAPPING",
            "POTENTIAL_SEARCH_EXPLOSION",
        }
        valid_modes = {"STRICT", "RELAXED_UNCONSTRAINED"}

        case_ids: Set[str] = set()
        for c in cases:
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

            sid = c.get("stream_id")
            if sid not in known_streams:
                self.errors.append(f"Case {cid} references undefined stream_id: {sid}")

            gs = c.get("graph_source")
            if gs != expected_graph:
                self.errors.append(f"Case {cid} references mismatched graph_source: {gs} (expected {expected_graph})")

            # Threshold validation
            eps_m = c.get("eps_m")
            if not isinstance(eps_m, int) or eps_m < 0:
                self.errors.append(f"Case {cid} has invalid eps_m: {eps_m} (must be non-negative int)")

            eps_x = c.get("eps_x")
            if eps_x is not None and (not isinstance(eps_x, int) or eps_x < 0):
                self.errors.append(f"Case {cid} has invalid eps_x: {eps_x} (must be None or non-negative int)")

            # Mode
            mode = c.get("mode")
            if mode not in valid_modes:
                self.errors.append(f"Case {cid} has invalid mode: {mode} (must be in {valid_modes})")

            # Category
            cat = c.get("workload_category")
            if cat not in valid_categories:
                self.errors.append(f"Case {cid} has invalid workload_category: {cat}")

            # Status check
            if c.get("approval_state") != "DRAFT_PENDING_USER_APPROVAL":
                self.errors.append(f"Case {cid} approval_state must be 'DRAFT_PENDING_USER_APPROVAL'")
            if c.get("execution_status") != "NOT_RUN":
                self.errors.append(f"Case {cid} execution_status must be 'NOT_RUN'")

        self.audit_log["pilot_cases_count"] = len(cases)

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
        print(f"Manifest File:     {checker.manifest_path}")
        print(f"Repository Root:   {checker.repo_root}")
        print(f"Dry-Run Mode:      {args.dry_run}")
        print(f"Audit Status:      {'PASS' if passed else 'FAIL'}")
        print("--------------------------------------------------------------------------------")

        sys_res = checker.audit_log.get("system_resources", {})
        print("System Resources:")
        print(f"  - RAM Total:     {sys_res.get('ram_total_gib', 0):.2f} GiB")
        print(f"  - RAM Available: {sys_res.get('ram_available_gib', 0):.2f} GiB (Threshold: {args.min_ram_gib:.2f} GiB)")
        print(f"  - Disk Total:    {sys_res.get('disk_total_gib', 0):.2f} GiB")
        print(f"  - Disk Free:     {sys_res.get('disk_free_gib', 0):.2f} GiB (Threshold: {args.min_disk_gib:.2f} GiB)")
        print("--------------------------------------------------------------------------------")

        if "data_graph_verified" in checker.audit_log:
            dg = checker.audit_log["data_graph_verified"]
            print(f"Data Graph:        {dg['name']} ({dg['size_bytes']:,} bytes)")
            print(f"  - SHA-256:       {dg['sha256']}")

        qv = checker.audit_log.get("queries_verified", {})
        print(f"Queries Verified:  {len(qv)} queries (all connected, valid schema)")

        sv = checker.audit_log.get("streams_verified", {})
        print(f"Streams Verified:  {len(sv)} prefix files (all operations valid)")

        pc = checker.audit_log.get("pilot_cases_count", 0)
        print(f"Pilot Cases:       {pc} proposed cases (all DRAFT / NOT_RUN)")

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
            print("Audit Verdict: ALL PREFLIGHT CHECKS PASSED.")
        print("================================================================================")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
