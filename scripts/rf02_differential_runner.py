#!/usr/bin/env python3
"""
scripts/rf02_differential_runner.py — Differential Correctness Runner for RF-02 / Gate G1.

Executes prefix replay against unmodified RapidFlow baseline and compares inferred per-update
delta against the pure-Python independent Oracle.

Hardenings (F1–F4):
1. Error handling and failure preservation:
   - Catches TimeoutExpired, OSError, parse errors, update count mismatches.
   - Preserves raw evidence for EVERY attempted step, even when failed.
   - Prevents UnboundLocalError by strictly initializing all result variables.
   - Handles predecessor failure: blocks delta inference if previous prefix failed.
2. Provenance and run isolation:
   - Unique run_id per invocation (UTC timestamp + UUID).
   - Dedicated directory per step: stdout.log, stderr.log, prefix.update, metadata.json.
   - Records input/binary/source/prefix checksums in CSV and JSON metadata.
3. Portable path handling:
   - Gracefully handles external --output and --logs-dir (e.g. /tmp).
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
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


class RunnerError(Exception):
    """Base exception for differential runner errors."""
    pass


class OutputParseError(RunnerError):
    """Raised when RapidFlow output format violates contract."""
    pass


class InternalTimeoutError(RunnerError):
    """Raised when RapidFlow reports an internal timeout in stdout."""
    pass


@dataclass
class SubprocessResult:
    returncode: Optional[int]
    stdout: str
    stderr: str
    duration_sec: float
    rss_kb: Optional[int]
    timed_out: bool = False
    error_message: Optional[str] = None


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

    status = subprocess.check_output(
        ["git", "-C", str(source_dir), "status", "--porcelain"],
        stderr=subprocess.PIPE,
        universal_newlines=True,
    ).strip()
    if status:
        return False, f"RapidFlow source tree is not pristine; dirty status:\n{status}"

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
) -> SubprocessResult:
    """
    Executes RapidFlow binary using /usr/bin/time -v and safely captures stdout, stderr, time, and peak RSS.
    Catches subprocess.TimeoutExpired and OSError without crashing.
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
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout_sec,
        )
        duration = time.perf_counter() - start_time

        rss_kb = None
        for line in proc.stderr.splitlines():
            if "Maximum resident set size (kbytes):" in line:
                try:
                    rss_kb = int(line.split(":")[-1].strip())
                except ValueError:
                    pass

        return SubprocessResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_sec=duration,
            rss_kb=rss_kb,
            timed_out=False,
            error_message=None,
        )
    except subprocess.TimeoutExpired as e:
        duration = time.perf_counter() - start_time
        stdout_str = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode("utf-8", errors="replace") if e.stdout else "")
        stderr_str = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode("utf-8", errors="replace") if e.stderr else "")
        return SubprocessResult(
            returncode=None,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_sec=duration,
            rss_kb=None,
            timed_out=True,
            error_message=f"Subprocess timed out after {timeout_sec} seconds",
        )
    except Exception as e:
        duration = time.perf_counter() - start_time
        return SubprocessResult(
            returncode=None,
            stdout="",
            stderr=str(e),
            duration_sec=duration,
            rss_kb=None,
            timed_out=False,
            error_message=f"Subprocess launch failed: {e}",
        )


def parse_rapidflow_output(stdout: str) -> Dict[str, int]:
    """
    Parses Result count, Updates num, and Edge process count from stdout.
    Fail-closed: requires exact single match for each.
    """
    if "Time out" in stdout:
        raise InternalTimeoutError("RapidFlow reported internal timeout in stdout")

    m_res = re.findall(r"^Result count:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_res) == 0:
        raise OutputParseError("Missing 'Result count:' line in stdout")
    if len(m_res) > 1:
        raise OutputParseError(f"Duplicate 'Result count:' lines in stdout ({len(m_res)} occurrences)")

    m_upd = re.findall(r"^Updates num:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_upd) == 0:
        raise OutputParseError("Missing 'Updates num:' line in stdout")
    if len(m_upd) > 1:
        raise OutputParseError(f"Duplicate 'Updates num:' lines in stdout ({len(m_upd)} occurrences)")

    m_proc = re.findall(r"^Edge process count:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_proc) == 0:
        raise OutputParseError("Missing 'Edge process count:' line in stdout")
    if len(m_proc) > 1:
        raise OutputParseError(f"Duplicate 'Edge process count:' lines in stdout ({len(m_proc)} occurrences)")

    return {
        "result_count": int(m_res[0]),
        "updates_num": int(m_upd[0]),
        "edge_process_count": int(m_proc[0]),
    }


def write_summary_csv(output_path: Path, rows: List[Dict[str, Any]]) -> None:
    """Durable CSV writer ensuring all attempted rows are persisted."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
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
        "failure_reason",
        "wall_time_sec",
        "peak_rss_kb",
        "exit_code",
        "binary_sha256",
        "prefix_stream_sha256",
        "log_file",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f_csv:
        writer = csv.DictWriter(f_csv, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="RF-02 Differential Testing Runner.")
    parser.add_argument("--config", default="configs/rf02_cases.json", help="Path to cases JSON config")
    parser.add_argument("--output", default="results/summary/rf02_correctness.csv", help="Path to output summary CSV")
    parser.add_argument("--logs-dir", default="results/logs/rf02", help="Directory to save raw execution logs")
    parser.add_argument("--pilot", action="store_true", help="Pilot mode: run only first 3 cases")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-prefix timeout in seconds")

    args = parser.parse_args()
    workspace_root = Path.cwd().resolve()

    # 1. Verify provenance
    ok, msg = verify_provenance(workspace_root)
    if not ok:
        print(f"PROVENANCE CHECK FAILED: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] {msg}")

    # 2. Load and validate cases config
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = workspace_root / config_path

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    cases = config_data.get("cases", [])
    if args.pilot:
        print("[INFO] Running in PILOT mode (first 3 cases).")
        cases = cases[:3]

    print(f"[INFO] Loaded {len(cases)} cases to execute.")

    binary_path = workspace_root / "baselines" / "rapidflow" / "source" / "build" / "streaming" / "RapidFlow.out"
    actual_bin_sha = file_sha256(binary_path)

    # 3. Initialize unique run ID and directory structure
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_absolute():
        logs_dir = workspace_root / logs_dir

    run_logs_dir = logs_dir / run_id
    run_logs_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Run ID: {run_id}")
    print(f"[INFO] Logging to: {run_logs_dir}")

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = workspace_root / output_path

    csv_rows: List[Dict[str, Any]] = []
    overall_all_pass = True

    try:
        for c in cases:
            case_id = c["id"]
            print(f"\n==================================================")
            print(f"CASE: {case_id} — {c['name']}")
            print(f"==================================================")

            q_path = Path(c["query_path"])
            if not q_path.is_absolute():
                q_path = workspace_root / q_path

            d_path = Path(c["data_path"])
            if not d_path.is_absolute():
                d_path = workspace_root / d_path

            s_path = Path(c["stream_path"])
            if not s_path.is_absolute():
                s_path = workspace_root / s_path

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

            # Track cumulative counts and predecessor failure status
            prev_valid_rf_cum: Optional[int] = 0
            predecessor_failed: bool = False

            for step_idx in range(1, len(stream_updates) + 1):
                prefix_slice = stream_updates[:step_idx]
                o_step = oracle_steps[step_idx - 1]
                u = o_step["update"]
                expected_delta = o_step["added_count"] if u.op == "+" else o_step["removed_count"]

                # Strictly initialize all step variables before running
                ret_code: Optional[int] = None
                stdout: str = ""
                stderr: str = ""
                dur_sec: float = 0.0
                rss_kb: Optional[int] = None
                rf_cum: Optional[int] = None
                inferred_delta: Optional[int] = None
                status: str = "PENDING"
                failure_reason: Optional[str] = None
                prefix_sha: str = ""

                # Step-specific log directory
                step_log_dir = run_logs_dir / case_id / f"step_{step_idx:02d}"
                step_log_dir.mkdir(parents=True, exist_ok=True)
                prefix_file_path = step_log_dir / "prefix.update"
                stdout_file_path = step_log_dir / "stdout.log"
                stderr_file_path = step_log_dir / "stderr.log"
                meta_file_path = step_log_dir / "metadata.json"

                # Save durable prefix stream file
                with open(prefix_file_path, "w", encoding="utf-8") as f_prefix:
                    for pu in prefix_slice:
                        tag = "e" if pu.op == "+" else "-e"
                        f_prefix.write(f"{tag} {pu.u} {pu.v} {pu.label}\n")
                prefix_sha = file_sha256(prefix_file_path)

                # Execute prefix replay
                sub_res = run_prefix_command(
                    binary_path, d_path, q_path, prefix_file_path, timeout_sec=args.timeout
                )

                ret_code = sub_res.returncode
                stdout = sub_res.stdout
                stderr = sub_res.stderr
                dur_sec = sub_res.duration_sec
                rss_kb = sub_res.rss_kb

                # Durably save stdout and stderr logs
                with open(stdout_file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(stdout)
                with open(stderr_file_path, "w", encoding="utf-8") as f_err:
                    f_err.write(stderr)

                cmd_executed = [
                    "/usr/bin/time", "-v", str(binary_path),
                    "-d", str(d_path), "-q", str(q_path), "-u", str(prefix_file_path), "-num", "MAX"
                ]

                # Classify result
                if sub_res.timed_out:
                    status = "TIMEOUT"
                    failure_reason = sub_res.error_message or "Execution timed out"
                    overall_all_pass = False
                    predecessor_failed = True
                    prev_valid_rf_cum = None
                    print(f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): TIMEOUT after {dur_sec:.2f}s -> [TIMEOUT]")
                elif ret_code is None or ret_code != 0:
                    status = "ERROR"
                    failure_reason = sub_res.error_message or f"Non-zero exit code: {ret_code}"
                    overall_all_pass = False
                    predecessor_failed = True
                    prev_valid_rf_cum = None
                    print(f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): FAILED with exit code {ret_code} -> [ERROR]")
                else:
                    try:
                        parsed = parse_rapidflow_output(stdout)
                        rf_cum = parsed["result_count"]
                        upd_num = parsed["updates_num"]
                        proc_count = parsed["edge_process_count"]

                        if upd_num != step_idx or proc_count != step_idx:
                            status = "ERROR"
                            failure_reason = (
                                f"Update count mismatch: updates_num={upd_num}, "
                                f"edge_process_count={proc_count}, expected={step_idx}"
                            )
                            overall_all_pass = False
                            predecessor_failed = True
                            prev_valid_rf_cum = None
                            print(f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): {failure_reason} -> [ERROR]")
                        elif predecessor_failed or prev_valid_rf_cum is None:
                            status = "BLOCKED_PREDECESSOR"
                            failure_reason = "Predecessor prefix failed or missing; per-update delta calculation blocked"
                            overall_all_pass = False
                            inferred_delta = None
                            print(f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): Predecessor failed -> [BLOCKED_PREDECESSOR]")
                        else:
                            inferred_delta = rf_cum - prev_valid_rf_cum
                            if inferred_delta == expected_delta:
                                status = "PASS"
                                print(
                                    f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): "
                                    f"Oracle Delta={expected_delta}, RF Inferred Delta={inferred_delta} "
                                    f"(C_{step_idx}={rf_cum}) -> MATCH [PASS] ({dur_sec:.4f}s, RSS={rss_kb} KB)"
                                )
                            else:
                                status = "FAIL"
                                failure_reason = f"Delta count mismatch: expected {expected_delta}, got {inferred_delta}"
                                overall_all_pass = False
                                print(
                                    f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): "
                                    f"MISMATCH! Oracle Delta={expected_delta}, RF Inferred Delta={inferred_delta} "
                                    f"(C_{step_idx}={rf_cum}) -> [FAIL]"
                                )
                            prev_valid_rf_cum = rf_cum
                    except InternalTimeoutError as e:
                        status = "TIMEOUT"
                        failure_reason = str(e)
                        overall_all_pass = False
                        predecessor_failed = True
                        prev_valid_rf_cum = None
                        print(f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): Internal timeout: {e} -> [TIMEOUT]")
                    except RunnerError as e:
                        status = "ERROR"
                        failure_reason = str(e)
                        overall_all_pass = False
                        predecessor_failed = True
                        prev_valid_rf_cum = None
                        print(f"  Step {step_idx:02d} ({u.op} {u.u}-{u.v} L{u.label}): Output parse error: {e} -> [ERROR]")

                # Portable display path
                try:
                    display_log_dir = str(step_log_dir.relative_to(workspace_root))
                except ValueError:
                    display_log_dir = str(step_log_dir.resolve())

                # Save step metadata JSON
                metadata = {
                    "run_id": run_id,
                    "case_id": case_id,
                    "step_index": step_idx,
                    "operation": u.op,
                    "edge": [u.u, u.v],
                    "edge_label": u.label,
                    "status": status,
                    "failure_reason": failure_reason,
                    "exit_code": ret_code,
                    "wall_time_sec": dur_sec,
                    "peak_rss_kb": rss_kb,
                    "oracle_added_count": o_step["added_count"],
                    "oracle_removed_count": o_step["removed_count"],
                    "oracle_total_count": o_step["total_count"],
                    "oracle_mapping_hash": o_step["mapping_hash"],
                    "expected_delta": expected_delta,
                    "rf_cum_count": rf_cum,
                    "rf_inferred_delta": inferred_delta,
                    "command": cmd_executed,
                    "rapidflow_source_commit": EXPECTED_BASELINE_COMMIT,
                    "rapidflow_binary_sha256": actual_bin_sha,
                    "query_sha256": c["query_sha256"],
                    "data_sha256": c["data_sha256"],
                    "stream_sha256": c["stream_sha256"],
                    "prefix_stream_sha256": prefix_sha,
                    "stdout_sha256": file_sha256(stdout_file_path),
                    "stderr_sha256": file_sha256(stderr_file_path),
                }
                with open(meta_file_path, "w", encoding="utf-8") as f_meta:
                    json.dump(metadata, f_meta, indent=2)

                csv_rows.append({
                    "run_id": run_id,
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
                    "rf_cum_count": rf_cum if rf_cum is not None else "",
                    "rf_inferred_delta": inferred_delta if inferred_delta is not None else "",
                    "expected_delta": expected_delta,
                    "status": status,
                    "failure_reason": failure_reason if failure_reason is not None else "",
                    "wall_time_sec": f"{dur_sec:.6f}",
                    "peak_rss_kb": rss_kb if rss_kb is not None else "",
                    "exit_code": ret_code if ret_code is not None else "",
                    "binary_sha256": actual_bin_sha,
                    "prefix_stream_sha256": prefix_sha,
                    "log_file": display_log_dir,
                })

                # Write incremental summary CSV so partial runs are never lost
                write_summary_csv(output_path, csv_rows)

    finally:
        # Guarantee CSV is written on exit
        if csv_rows:
            write_summary_csv(output_path, csv_rows)

    print(f"\n==================================================")
    print(f"Summary written to: {output_path}")
    print(f"Total step updates evaluated: {len(csv_rows)}")
    pass_count = sum(1 for r in csv_rows if r["status"] == "PASS")
    fail_count = sum(1 for r in csv_rows if r["status"] == "FAIL")
    error_count = sum(1 for r in csv_rows if r["status"] == "ERROR")
    timeout_count = sum(1 for r in csv_rows if r["status"] == "TIMEOUT")
    blocked_count = sum(1 for r in csv_rows if r["status"] == "BLOCKED_PREDECESSOR")
    print(f"Passed: {pass_count} / {len(csv_rows)}")
    if fail_count:
        print(f"Failed: {fail_count}")
    if error_count:
        print(f"Errors: {error_count}")
    if timeout_count:
        print(f"Timeouts: {timeout_count}")
    if blocked_count:
        print(f"Blocked Predecessors: {blocked_count}")

    if overall_all_pass:
        print("Gate G1-A (COUNT-CORRECTNESS): PASS")
    else:
        print("Gate G1-A (COUNT-CORRECTNESS): FAIL / INCONCLUSIVE")
        sys.exit(2)


if __name__ == "__main__":
    main()
