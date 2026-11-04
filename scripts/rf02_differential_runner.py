#!/usr/bin/env python3
"""
scripts/rf02_differential_runner.py — Differential Correctness Runner for RF-02 / Gate G1.

Executes prefix replay against unmodified RapidFlow baseline and compares inferred per-update
delta against the pure-Python independent Oracle.

Verifies:
1. Provenance: RapidFlow pristine source git commit and binary SHA-256.
2. Fixture SHA-256 integrity.
3. Fail-closed execution: exit 0, updates num == edge process count == prefix length.
4. Exact delta count equality: Delta_k = C_k - C_{k-1} == Oracle delta count.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Any, Dict, List, Optional, Tuple

from scripts.rf02_oracle import (
    LabeledGraph,
    Update,
    compute_update_delta,
    canonical_mapping_hash,
    find_all_embeddings,
    parse_stream_file,
)

EXPECTED_BASELINE_COMMIT = "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1"
EXPECTED_BINARY_SHA256 = "abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6"


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_provenance(workspace_root: Path) -> Tuple[bool, str]:
    source_dir = workspace_root / "baselines" / "rapidflow" / "source"
    binary_path = source_dir / "build" / "streaming" / "RapidFlow.out"

    if not source_dir.exists():
        return False, f"Source directory does not exist: {source_dir}"
    if not binary_path.exists():
        return False, f"Binary does not exist: {binary_path}"

    # Check git commit
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
            stderr=subprocess.PIPE,
            universal_newlines=True,
        ).strip()
    except subprocess.CalledProcessError as e:
        return False, f"Failed to check git commit of RapidFlow source: {e}"

    if commit != EXPECTED_BASELINE_COMMIT:
        return False, f"RapidFlow source commit mismatch: expected {EXPECTED_BASELINE_COMMIT}, got {commit}"

    # Check git status clean
    status = subprocess.check_output(
        ["git", "-C", str(source_dir), "status", "--porcelain"],
        stderr=subprocess.PIPE,
        universal_newlines=True,
    ).strip()
    if status:
        return False, f"RapidFlow source tree is not pristine; dirty status:\n{status}"

    # Check binary SHA-256
    actual_bin_sha = file_sha256(binary_path)
    if actual_bin_sha != EXPECTED_BINARY_SHA256:
        return False, f"Binary SHA-256 mismatch: expected {EXPECTED_BINARY_SHA256}, got {actual_bin_sha}"

    return True, "Provenance verified successfully."


def run_prefix_command(
    binary_path: Path,
    data_path: Path,
    query_path: Path,
    prefix_stream_path: Path,
    timeout_sec: float = 60.0,
) -> Tuple[int, str, str, float, Optional[int]]:
    """
    Executes RapidFlow binary using /usr/bin/time -v and captures stdout, stderr, time, and peak RSS.
    """
    cmd = [
        "/usr/bin/time",
        "-v",
        str(binary_path),
        "-d",
        str(data_path),
        "-q",
        str(query_path),
        "-u",
        str(prefix_stream_path),
        "-num",
        "MAX",
    ]

    start_time = time.perf_counter()
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=timeout_sec,
    )
    duration = time.perf_counter() - start_time

    # Parse peak RSS (KB) from stderr
    rss_kb = None
    for line in proc.stderr.splitlines():
        if "Maximum resident set size (kbytes):" in line:
            try:
                rss_kb = int(line.split(":")[-1].strip())
            except ValueError:
                pass

    return proc.returncode, proc.stdout, proc.stderr, duration, rss_kb


def parse_rapidflow_output(stdout: str) -> Dict[str, Any]:
    """
    Parses Result count, Updates num, and Edge process count from stdout.
    Fail-closed: requires exact single match for each.
    """
    res = {}

    m_res = re.findall(r"^Result count:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_res) != 1:
        raise ValueError(f"Expected exactly 1 'Result count:' line in stdout, found {len(m_res)}")
    res["result_count"] = int(m_res[0])

    m_upd = re.findall(r"^Updates num:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_upd) != 1:
        raise ValueError(f"Expected exactly 1 'Updates num:' line in stdout, found {len(m_upd)}")
    res["updates_num"] = int(m_upd[0])

    m_proc = re.findall(r"^Edge process count:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_proc) != 1:
        raise ValueError(f"Expected exactly 1 'Edge process count:' line in stdout, found {len(m_proc)}")
    res["edge_process_count"] = int(m_proc[0])

    if "Time out" in stdout:
        raise RuntimeError("RapidFlow reported internal timeout in stdout")

    return res


def main() -> None:
    parser = argparse.ArgumentParser(description="RF-02 Differential Testing Runner.")
    parser.add_argument("--config", default="configs/rf02_cases.json", help="Path to cases JSON config")
    parser.add_argument("--output", default="results/summary/rf02_correctness.csv", help="Path to output summary CSV")
    parser.add_argument("--logs-dir", default="results/logs/rf02", help="Directory to save raw execution logs")
    parser.add_argument("--pilot", action="store_true", help="Pilot mode: run only first 3 cases")

    args = parser.parse_args()
    workspace_root = Path.cwd().resolve()

    # 1. Verify provenance
    ok, msg = verify_provenance(workspace_root)
    if not ok:
        print(f"PROVENANCE CHECK FAILED: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] {msg}")

    # 2. Load and validate cases config
    config_path = workspace_root / args.config
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    cases = config_data.get("cases", [])
    if args.pilot:
        print("[INFO] Running in PILOT mode (first 3 cases).")
        cases = cases[:3]

    print(f"[INFO] Loaded {len(cases)} cases to execute.")

    binary_path = workspace_root / "baselines" / "rapidflow" / "source" / "build" / "streaming" / "RapidFlow.out"
    logs_dir = workspace_root / args.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    csv_rows = []
    overall_all_pass = True

    for c in cases:
        case_id = c["id"]
        print(f"\n==================================================")
        print(f"CASE: {case_id} — {c['name']}")
        print(f"==================================================")

        q_path = workspace_root / c["query_path"]
        d_path = workspace_root / c["data_path"]
        s_path = workspace_root / c["stream_path"]

        # Validate file checksums
        if file_sha256(q_path) != c["query_sha256"]:
            raise ValueError(f"Case {case_id}: query file SHA-256 mismatch")
        if file_sha256(d_path) != c["data_sha256"]:
            raise ValueError(f"Case {case_id}: data file SHA-256 mismatch")
        if file_sha256(s_path) != c["stream_sha256"]:
            raise ValueError(f"Case {case_id}: stream file SHA-256 mismatch")

        # Load into Oracle
        Q = LabeledGraph.parse_from_file(str(q_path))
        G0 = LabeledGraph.parse_from_file(str(d_path))
        stream_updates = parse_stream_file(str(s_path))

        initial_mappings = find_all_embeddings(Q, G0)
        print(f"  Initial data graph matches: {len(initial_mappings)}")

        # Compute Oracle progression
        oracle_steps = []
        curr_G = G0
        for u in stream_updates:
            added, removed, next_G = compute_update_delta(Q, curr_G, u)
            curr_matches = find_all_embeddings(Q, next_G)
            oracle_steps.append({
                "update": u,
                "added_count": len(added),
                "removed_count": len(removed),
                "total_count": len(curr_matches),
                "mapping_hash": canonical_mapping_hash(curr_matches),
            })
            curr_G = next_G

        # Execute Prefix Replay for k = 1 .. len(stream_updates)
        prev_rf_count = 0
        case_logs_dir = logs_dir / case_id
        case_logs_dir.mkdir(parents=True, exist_ok=True)

        for step_idx in range(1, len(stream_updates) + 1):
            prefix_slice = stream_updates[:step_idx]
            o_step = oracle_steps[step_idx - 1]
            u = o_step["update"]

            # Write temporary prefix stream
            with tempfile.NamedTemporaryFile("w", suffix=".update", delete=False) as tmp_stream:
                for pu in prefix_slice:
                    tag = "e" if pu.op == "+" else "-e"
                    tmp_stream.write(f"{tag} {pu.u} {pu.v} {pu.label}\n")
                tmp_stream_path = Path(tmp_stream.name)

            log_file = case_logs_dir / f"step_{step_idx:02d}.log"

            try:
                ret_code, stdout, stderr, dur_sec, rss_kb = run_prefix_command(
                    binary_path, d_path, q_path, tmp_stream_path
                )
                with open(log_file, "w", encoding="utf-8") as f_log:
                    f_log.write(f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n")

                if ret_code != 0:
                    status = "ERROR"
                    rf_cum = None
                    inferred_delta = None
                    overall_all_pass = False
                    print(f"  Step {step_idx}: FAILED with exit code {ret_code}")
                else:
                    parsed = parse_rapidflow_output(stdout)
                    rf_cum = parsed["result_count"]
                    if parsed["updates_num"] != step_idx or parsed["edge_process_count"] != step_idx:
                        status = "ERROR"
                        overall_all_pass = False
                        print(f"  Step {step_idx}: Updates/edge process count mismatch")
                    else:
                        inferred_delta = rf_cum - prev_rf_count
                        expected_delta = o_step["added_count"] if u.op == "+" else o_step["removed_count"]

                        if inferred_delta == expected_delta:
                            status = "PASS"
                            print(
                                f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): "
                                f"Oracle Delta={expected_delta}, RF Inferred Delta={inferred_delta} "
                                f"(C_{step_idx}={rf_cum}) -> MATCH [PASS] ({dur_sec:.4f}s, RSS={rss_kb} KB)"
                            )
                        else:
                            status = "FAIL"
                            overall_all_pass = False
                            print(
                                f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): "
                                f"MISMATCH! Oracle Delta={expected_delta}, RF Inferred Delta={inferred_delta} "
                                f"(C_{step_idx}={rf_cum}) -> [FAIL]"
                            )
                        prev_rf_count = rf_cum
            finally:
                if tmp_stream_path.exists():
                    tmp_stream_path.unlink()

            expected_delta = o_step["added_count"] if u.op == "+" else o_step["removed_count"]
            csv_rows.append({
                "case_id": case_id,
                "step_index": step_idx,
                "op": u.op,
                "src": u.u,
                "dst": u.v,
                "edge_label": u.label,
                "oracle_added_count": o_step["added_count"],
                "oracle_removed_count": o_step["removed_count"],
                "oracle_total_count": o_step["total_count"],
                "oracle_mapping_hash": o_step["mapping_hash"],
                "rf_cum_count": rf_cum,
                "rf_inferred_delta": inferred_delta,
                "expected_delta": expected_delta,
                "status": status,
                "wall_time_sec": f"{dur_sec:.6f}",
                "peak_rss_kb": rss_kb,
                "exit_code": ret_code,
                "log_file": str(log_file.relative_to(workspace_root)),
            })

    # Write summary CSV
    output_path = workspace_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id",
        "step_index",
        "op",
        "src",
        "dst",
        "edge_label",
        "oracle_added_count",
        "oracle_removed_count",
        "oracle_total_count",
        "oracle_mapping_hash",
        "rf_cum_count",
        "rf_inferred_delta",
        "expected_delta",
        "status",
        "wall_time_sec",
        "peak_rss_kb",
        "exit_code",
        "log_file",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f_csv:
        writer = csv.DictWriter(f_csv, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\n==================================================")
    print(f"Summary written to: {output_path}")
    print(f"Total step updates evaluated: {len(csv_rows)}")
    pass_count = sum(1 for r in csv_rows if r["status"] == "PASS")
    print(f"Passed: {pass_count} / {len(csv_rows)}")
    if overall_all_pass:
        print("Gate G1-A (COUNT-CORRECTNESS): PASS")
    else:
        print("Gate G1-A (COUNT-CORRECTNESS): FAIL")
        sys.exit(2)


if __name__ == "__main__":
    main()
