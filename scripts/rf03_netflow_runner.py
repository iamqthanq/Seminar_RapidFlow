#!/usr/bin/env python3
"""
scripts/rf03_netflow_runner.py — Reproducible NetFlow COUNT-ONLY Baseline Runner (RF-03 / Gate G2).

Executes unmodified RapidFlow C++ baseline on NetFlow graph and query/stream workloads under
fail-closed provenance checks, strict resource bounds (external timeout + RSS cap), durable
per-attempt logging, and cross-repeat numerical consistency verification.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

EXPECTED_BASELINE_COMMIT = "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1"
EXPECTED_BINARY_SHA256 = "abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6"
EXPECTED_DATA_GRAPH_SHA256 = "bfedd2cab6d2c460141da945e77b0be39ae24c74d9a28b9394a38ee35b652fc6"
MIN_FREE_RAM_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB threshold


class RunnerError(Exception):
    """Base exception for runner errors."""
    pass


class ProvenanceError(RunnerError):
    """Raised when repository, binary, or dataset fails provenance audit."""
    pass


class OutputParseError(RunnerError):
    """Raised when RapidFlow output format violates expected contract."""
    pass


class ResourceLimitError(RunnerError):
    """Raised when system resources are below safe execution thresholds."""
    pass


@dataclass
class SubprocessResult:
    returncode: Optional[int]
    stdout: str
    stderr: str
    duration_sec: float
    rss_kb: Optional[int]
    timed_out: bool = False
    oom_killed: bool = False
    error_message: Optional[str] = None


@dataclass
class ParsedMetrics:
    preprocessing_time_sec: Optional[float] = None
    query_time_sec: Optional[float] = None
    gv_update_time_sec: Optional[float] = None
    lv_update_time_sec: Optional[float] = None
    search_time_sec: Optional[float] = None
    updates_num: Optional[int] = None
    edge_process_count: Optional[int] = None
    relevant_update_count: Optional[int] = None
    search_count: Optional[int] = None
    positive_update_count: Optional[int] = None
    result_count: Optional[int] = None
    partial_result_count: Optional[int] = None
    invalid_partial_count: Optional[int] = None
    iso_conflict_count: Optional[int] = None
    si_empty_count: Optional[int] = None
    lc_empty_count: Optional[int] = None
    internal_timeout: bool = False


def file_sha256(path: pathlib.Path | str) -> str:
    p = pathlib.Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_available_memory_bytes() -> int:
    """Reads available system memory in bytes from /proc/meminfo if on Linux."""
    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.exists():
        with open(meminfo, "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    return 16 * 1024 * 1024 * 1024  # Default fallback if not /proc/meminfo


def get_host_telemetry() -> Dict[str, Any]:
    """Captures host system telemetry for reproducible environment recording."""
    info: Dict[str, Any] = {
        "platform": sys.platform,
        "python_version": sys.version,
    }
    try:
        uname = os.uname()
        info["uname"] = {
            "sysname": uname.sysname,
            "nodename": uname.nodename,
            "release": uname.release,
            "version": uname.version,
            "machine": uname.machine,
        }
    except AttributeError:
        pass

    try:
        meminfo = pathlib.Path("/proc/meminfo")
        if meminfo.exists():
            mem: Dict[str, int] = {}
            for line in meminfo.read_text().splitlines()[:5]:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = int(parts[1].split()[0])
            info["meminfo_kb"] = mem
    except Exception:
        pass

    return info


def verify_provenance(
    source_dir: pathlib.Path,
    binary_path: pathlib.Path,
    data_graph_path: pathlib.Path,
    skip_git: bool = False,
) -> Tuple[bool, str]:
    """
    Verifies that RapidFlow source and binary are pristine and match pinned SHAs,
    and that the data graph file matches its verified SHA.
    """
    if not source_dir.exists():
        return False, f"RapidFlow source directory missing: {source_dir}"
    if not binary_path.exists():
        return False, f"RapidFlow binary executable missing: {binary_path}"
    if not data_graph_path.exists():
        return False, f"Data graph file missing: {data_graph_path}"

    if not skip_git:
        try:
            commit = subprocess.check_output(
                ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                stderr=subprocess.PIPE,
                universal_newlines=True,
            ).strip()
            if commit != EXPECTED_BASELINE_COMMIT:
                return False, f"RapidFlow commit mismatch: expected {EXPECTED_BASELINE_COMMIT}, got {commit}"

            status = subprocess.check_output(
                ["git", "-C", str(source_dir), "status", "--porcelain"],
                stderr=subprocess.PIPE,
                universal_newlines=True,
            ).strip()
            if status:
                return False, f"RapidFlow source is dirty:\n{status}"
        except Exception as e:
            return False, f"Git provenance verification failed: {e}"

    actual_bin_sha = file_sha256(binary_path)
    if actual_bin_sha != EXPECTED_BINARY_SHA256:
        return False, f"Binary SHA mismatch: expected {EXPECTED_BINARY_SHA256}, got {actual_bin_sha}"

    actual_data_sha = file_sha256(data_graph_path)
    if actual_data_sha != EXPECTED_DATA_GRAPH_SHA256:
        return False, f"Data graph SHA mismatch: expected {EXPECTED_DATA_GRAPH_SHA256}, got {actual_data_sha}"

    return True, "Provenance verified successfully."


def parse_rapidflow_stdout(stdout: str) -> ParsedMetrics:
    """
    Extracts metrics and search counters from RapidFlow stdout.
    Fail-closed: requires exactly one occurrence of each essential field.
    """
    metrics = ParsedMetrics()

    if "Time out..." in stdout:
        metrics.internal_timeout = True

    # Parse Result count
    m_res = re.findall(r"^Result count:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_res) == 0:
        raise OutputParseError("Missing 'Result count:' line in stdout")
    if len(m_res) > 1:
        raise OutputParseError(f"Duplicate 'Result count:' lines in stdout ({len(m_res)} occurrences)")
    metrics.result_count = int(m_res[0])

    # Parse Updates num
    m_upd = re.findall(r"^Updates num:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_upd) == 0:
        raise OutputParseError("Missing 'Updates num:' line in stdout")
    if len(m_upd) > 1:
        raise OutputParseError(f"Duplicate 'Updates num:' lines in stdout ({len(m_upd)} occurrences)")
    metrics.updates_num = int(m_upd[0])

    # Parse Edge process count
    m_proc = re.findall(r"^Edge process count:\s*(\d+)$", stdout, re.MULTILINE)
    if len(m_proc) == 0:
        raise OutputParseError("Missing 'Edge process count:' line in stdout")
    if len(m_proc) > 1:
        raise OutputParseError(f"Duplicate 'Edge process count:' lines in stdout ({len(m_proc)} occurrences)")
    metrics.edge_process_count = int(m_proc[0])

    # Parse timing fields
    def parse_float(label: str) -> Optional[float]:
        pattern = rf"^{re.escape(label)}:\s*([\d.]+)"
        matches = re.findall(pattern, stdout, re.MULTILINE)
        return float(matches[-1]) if len(matches) >= 1 else None

    metrics.preprocessing_time_sec = parse_float("Preprocessing time (seconds)")
    metrics.query_time_sec = parse_float("Query time (seconds)")
    metrics.gv_update_time_sec = parse_float("Global view update time (seconds)")
    metrics.lv_update_time_sec = parse_float("Local view update time (seconds)")
    metrics.search_time_sec = parse_float("Search time (seconds)")

    # Parse counters
    def parse_int(label: str) -> Optional[int]:
        pattern = rf"^{re.escape(label)}:\s*(\d+)"
        matches = re.findall(pattern, stdout, re.MULTILINE)
        return int(matches[-1]) if len(matches) >= 1 else None

    metrics.relevant_update_count = parse_int("Relevant update count")
    metrics.search_count = parse_int("Search count")
    metrics.positive_update_count = parse_int("Positive update count")
    metrics.partial_result_count = parse_int("Partial result count (exclude results)")
    metrics.invalid_partial_count = parse_int("Invalid partial count")
    metrics.iso_conflict_count = parse_int("ISO conflict count")
    metrics.si_empty_count = parse_int("SI empty count")
    metrics.lc_empty_count = parse_int("LC empty count")

    return metrics


def execute_case_run(
    binary_path: pathlib.Path,
    data_path: pathlib.Path,
    query_path: pathlib.Path,
    stream_path: pathlib.Path,
    timeout_sec: float = 60.0,
    rss_cap_mb: int = 3072,
    log_dir: Optional[pathlib.Path] = None,
) -> SubprocessResult:
    """
    Executes RapidFlow binary bounded by external timeout and RSS tracking.
    Guarantees process group cleanup on timeout or interruption.
    """
    cmd = [
        str(binary_path),
        "-d", str(data_path),
        "-q", str(query_path),
        "-u", str(stream_path),
        "-num", "MAX",
        "-time_limit", str(int(timeout_sec)),
    ]

    has_time_cmd = pathlib.Path("/usr/bin/time").exists()
    run_cmd = ["/usr/bin/time", "-v"] + cmd if has_time_cmd else cmd

    start_time = time.perf_counter()
    stdout_data = ""
    stderr_data = ""
    duration = 0.0
    rss_kb: Optional[int] = None
    timed_out = False
    oom_killed = False
    error_msg: Optional[str] = None
    code: Optional[int] = None

    proc = None
    try:
        proc = subprocess.Popen(
            run_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            start_new_session=True,
        )
        try:
            stdout_data, stderr_data = proc.communicate(timeout=timeout_sec)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            error_msg = f"External process timeout reached ({timeout_sec}s)"
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout_data, stderr_data = proc.communicate(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout_data, stderr_data = proc.communicate()
            code = 124
    except Exception as e:
        error_msg = f"Process launch failed: {e}"
        code = -1
    finally:
        duration = time.perf_counter() - start_time

    # Parse RSS from /usr/bin/time stderr
    if stderr_data:
        for line in stderr_data.splitlines():
            if "Maximum resident set size (kbytes):" in line:
                try:
                    rss_kb = int(line.split(":")[-1].strip())
                except ValueError:
                    pass

    # Check post-execution RSS budget
    if rss_kb is not None and rss_cap_mb is not None:
        if rss_kb > (rss_cap_mb * 1024):
            oom_killed = True
            error_msg = f"Process exceeded post-execution RSS budget: {rss_kb} KB > {rss_cap_mb * 1024} KB"

    # Persist raw logs immediately if log_dir provided
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stdout.log").write_text(stdout_data, errors="replace")
        (log_dir / "stderr.log").write_text(stderr_data, errors="replace")

    return SubprocessResult(
        returncode=code,
        stdout=stdout_data,
        stderr=stderr_data,
        duration_sec=duration,
        rss_kb=rss_kb,
        timed_out=timed_out,
        oom_killed=oom_killed,
        error_message=error_msg,
    )


def write_summary_csv(output_path: pathlib.Path, rows: List[Dict[str, Any]]) -> None:
    """Atomic and durable CSV writer ensuring results are persisted."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "case_id",
        "repeat_index",
        "query_id",
        "query_category",
        "query_v",
        "query_e",
        "stream_id",
        "stream_type",
        "prefix_length",
        "status",
        "exit_code",
        "wall_time_sec",
        "rss_kb",
        "preprocessing_time_sec",
        "query_time_sec",
        "gv_update_time_sec",
        "lv_update_time_sec",
        "search_time_sec",
        "updates_num",
        "edge_process_count",
        "relevant_update_count",
        "search_count",
        "positive_update_count",
        "result_count",
        "query_sha256",
        "stream_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "log_dir",
        "error_message",
    ]

    tmp_path = output_path.with_suffix(".tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    tmp_path.replace(output_path)


def recover_summary_from_logs(run_log_dir: pathlib.Path) -> List[Dict[str, Any]]:
    """
    Recover summary rows from individual attempt metadata.json files in a run directory.
    Guarantees auditability and recovery if benchmark execution is interrupted.
    """
    rows = []
    if not run_log_dir.is_dir():
        return rows
    for meta_file in sorted(run_log_dir.glob("*_rep*/metadata.json")):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if "summary_row" in data:
                rows.append(data["summary_row"])
        except Exception:
            continue
    return rows


def run_benchmark(
    config_path: pathlib.Path,
    workspace_root: pathlib.Path,
    dry_run: bool = False,
    pilot_only: bool = False,
    target_case_id: Optional[str] = None,
    repeats_override: Optional[int] = None,
    timeout_override: Optional[float] = None,
    rss_cap_override: Optional[int] = None,
    summary_csv_path: Optional[pathlib.Path] = None,
    logs_base_dir: Optional[pathlib.Path] = None,
    skip_memory_check: bool = False,
    binary_path_override: Optional[pathlib.Path] = None,
    source_dir_override: Optional[pathlib.Path] = None,
    skip_git_check: bool = False,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Main orchestration loop for NetFlow baseline benchmark.
    Returns (exit_code, summary_rows).
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Resolve paths
    source_dir = source_dir_override or (workspace_root.parent / "Seminar_RapidFlow" / "baselines" / "rapidflow" / "source")
    if not source_dir.exists():
        # Fallback to local workspace relative if present
        source_dir = workspace_root / "baselines" / "rapidflow" / "source"

    binary_path = binary_path_override or (source_dir / "build" / "streaming" / "RapidFlow.out")
    data_graph_path = workspace_root / cfg["data_graph"]["path"]

    summary_path = summary_csv_path or (workspace_root / "results" / "summary" / "rf03_baseline_runs.csv")
    logs_dir = logs_base_dir or (workspace_root / "results" / "logs" / "rf03")

    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    run_log_dir = logs_dir / run_id
    host_info = get_host_telemetry()

    print("=" * 60)
    print("RF-03 NETFLOW BASELINE RUNNER (Gate G2)")
    print(f"Run ID:    {run_id}")
    print(f"Config:    {config_path}")
    print(f"Summary:   {summary_path}")
    print(f"Logs:      {logs_dir / run_id}")
    print("=" * 60)

    # Safe memory check
    avail_mem = get_available_memory_bytes()
    avail_mem_gb = avail_mem / (1024**3)
    print(f"[PREFLIGHT] Available RAM: {avail_mem_gb:.2f} GiB")
    if not skip_memory_check and avail_mem < MIN_FREE_RAM_BYTES:
        err = f"Safe memory threshold violated: {avail_mem_gb:.2f} GiB < 4.0 GiB required"
        print(f"[FAIL] {err}", file=sys.stderr)
        return 2, []

    # Verify provenance
    prov_ok, prov_msg = verify_provenance(
        source_dir=source_dir,
        binary_path=binary_path,
        data_graph_path=data_graph_path,
        skip_git=skip_git_check,
    )
    if not prov_ok:
        print(f"[FAIL] Provenance check failed: {prov_msg}", file=sys.stderr)
        return 2, []
    print(f"[PREFLIGHT] {prov_msg}")

    # Verify all query files and stream files
    for q_id, q_info in cfg["queries"].items():
        qp = workspace_root / q_info["path"]
        if not qp.is_file():
            print(f"[FAIL] Query file missing: {qp}", file=sys.stderr)
            return 2, []
        actual_q_sha = file_sha256(qp)
        if actual_q_sha != q_info["sha256"]:
            print(f"[FAIL] Query SHA mismatch for {q_id}: expected {q_info['sha256']}, got {actual_q_sha}", file=sys.stderr)
            return 2, []

    for s_id, s_info in cfg["streams"].items():
        sp = workspace_root / s_info["path"]
        if not sp.is_file():
            print(f"[FAIL] Stream prefix file missing: {sp}", file=sys.stderr)
            return 2, []
        actual_s_sha = file_sha256(sp)
        if actual_s_sha != s_info["sha256"]:
            print(f"[FAIL] Stream SHA mismatch for {s_id}: expected {s_info['sha256']}, got {actual_s_sha}", file=sys.stderr)
            return 2, []

    print("[PREFLIGHT] All query and stream checksums verified successfully.")

    # Select cases to run
    all_cases = cfg["cases"]
    if target_case_id:
        selected_cases = [c for c in all_cases if c["case_id"] == target_case_id]
        if not selected_cases:
            print(f"[FAIL] Target case_id '{target_case_id}' not found in workload config.", file=sys.stderr)
            return 2, []
    elif pilot_only:
        pilot_ids = set(cfg.get("pilot_cases", []))
        selected_cases = [c for c in all_cases if c["case_id"] in pilot_ids]
    else:
        selected_cases = all_cases

    default_repeats = 1 if pilot_only else (repeats_override or cfg["selection_policy"].get("repeats_per_case", 3))
    default_timeout = timeout_override or float(cfg["selection_policy"].get("per_run_timeout_seconds", 60))
    default_rss_cap = rss_cap_override or int(cfg["selection_policy"].get("per_run_rss_cap_mb", 3072))

    print(f"[WORKLOAD] Total selected cases: {len(selected_cases)} (Repeats: {default_repeats}, Timeout: {default_timeout}s, RSS Cap: {default_rss_cap} MB)")
    for sc in selected_cases:
        print(f"  - Case {sc['case_id']}: Query {sc['query_id']} x Stream {sc['stream_id']}")

    if dry_run:
        print("[DRY-RUN] Preflight completed successfully. No executions performed.")
        return 0, []

    # Execution matrix
    summary_rows: List[Dict[str, Any]] = []
    case_results: Dict[str, List[int]] = {}  # case_id -> list of result_count for completed repeats
    had_error = False

    for case_item in selected_cases:
        c_id = case_item["case_id"]
        q_id = case_item["query_id"]
        s_id = case_item["stream_id"]
        q_meta = cfg["queries"][q_id]
        s_meta = cfg["streams"][s_id]

        qp = workspace_root / q_meta["path"]
        sp = workspace_root / s_meta["path"]
        prefix_len = s_meta["prefix_length"]

        case_results[c_id] = []
        print(f"\n---> Running Case: {c_id} ({q_id} x {s_id}, updates: {prefix_len})")

        for rep in range(1, default_repeats + 1):
            rep_label = f"{c_id}_rep{rep}"
            rep_log_dir = run_log_dir / rep_label
            print(f"  [Repeat {rep}/{default_repeats}] Executing...", end="", flush=True)

            sub_res = execute_case_run(
                binary_path=binary_path,
                data_path=data_graph_path,
                query_path=qp,
                stream_path=sp,
                timeout_sec=default_timeout,
                rss_cap_mb=default_rss_cap,
                log_dir=rep_log_dir,
            )

            # Classify execution status
            status = "UNKNOWN"
            error_msg = sub_res.error_message
            parsed = ParsedMetrics()

            if sub_res.timed_out:
                status = "TIMEOUT"
            elif sub_res.oom_killed:
                status = "OOM"
            elif sub_res.returncode != 0:
                status = "ERROR"
                if not error_msg:
                    error_msg = f"Non-zero exit code: {sub_res.returncode}"
            else:
                try:
                    parsed = parse_rapidflow_stdout(sub_res.stdout)
                    if parsed.internal_timeout:
                        status = "INTERNAL_TIMEOUT"
                        error_msg = "RapidFlow reported internal timeout sentinel"
                    elif parsed.updates_num != prefix_len or parsed.edge_process_count != prefix_len:
                        status = "INCOMPLETE_STREAM"
                        error_msg = f"Update count mismatch: updates_num={parsed.updates_num}, edge_process_count={parsed.edge_process_count}, expected={prefix_len}"
                    else:
                        status = "COMPLETED"
                        if parsed.result_count is not None:
                            case_results[c_id].append(parsed.result_count)
                except OutputParseError as ope:
                    status = "PARSE_ERROR"
                    error_msg = str(ope)

            if status != "COMPLETED":
                had_error = True

            # Calculate log SHAs
            stdout_sha = hashlib.sha256(sub_res.stdout.encode("utf-8", errors="replace")).hexdigest()
            stderr_sha = hashlib.sha256(sub_res.stderr.encode("utf-8", errors="replace")).hexdigest()

            row = {
                "run_id": run_id,
                "case_id": c_id,
                "repeat_index": rep,
                "query_id": q_id,
                "query_category": q_meta["category"],
                "query_v": q_meta["vertices"],
                "query_e": q_meta["edges"],
                "stream_id": s_id,
                "stream_type": s_meta["stream_type"],
                "prefix_length": prefix_len,
                "status": status,
                "exit_code": sub_res.returncode,
                "wall_time_sec": round(sub_res.duration_sec, 6),
                "rss_kb": sub_res.rss_kb,
                "preprocessing_time_sec": parsed.preprocessing_time_sec,
                "query_time_sec": parsed.query_time_sec,
                "gv_update_time_sec": parsed.gv_update_time_sec,
                "lv_update_time_sec": parsed.lv_update_time_sec,
                "search_time_sec": parsed.search_time_sec,
                "updates_num": parsed.updates_num,
                "edge_process_count": parsed.edge_process_count,
                "relevant_update_count": parsed.relevant_update_count,
                "search_count": parsed.search_count,
                "positive_update_count": parsed.positive_update_count,
                "result_count": parsed.result_count,
                "query_sha256": q_meta["sha256"],
                "stream_sha256": s_meta["sha256"],
                "stdout_sha256": stdout_sha,
                "stderr_sha256": stderr_sha,
                "log_dir": str(rep_log_dir),
                "error_message": error_msg or "",
            }
            summary_rows.append(row)

            # Write individual run metadata JSON
            rep_log_dir.mkdir(parents=True, exist_ok=True)
            rep_meta = {
                "run_id": run_id,
                "case_id": c_id,
                "repeat_index": rep,
                "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": status,
                "host_telemetry": host_info,
                "summary_row": row,
            }
            (rep_log_dir / "metadata.json").write_text(json.dumps(rep_meta, indent=2) + "\n")

            # Durable incremental checkpoints: update run-local checkpoint CSV and aggregate summary CSV
            run_checkpoint_csv = run_log_dir / "checkpoint_runs.csv"
            write_summary_csv(run_checkpoint_csv, summary_rows)
            write_summary_csv(summary_path, summary_rows)

            rss_str = f"{sub_res.rss_kb} KB" if sub_res.rss_kb else "N/A"
            res_count_str = str(parsed.result_count) if parsed.result_count is not None else "N/A"
            print(f" {status} (wall: {sub_res.duration_sec:.2f}s, RSS: {rss_str}, results: {res_count_str})")

        # Multi-repeat count consistency check for the case
        completed_counts = case_results[c_id]
        if len(completed_counts) > 1:
            if len(set(completed_counts)) != 1:
                print(f"[FAIL] Count mismatch across repeats for case {c_id}: {completed_counts}", file=sys.stderr)
                had_error = True

    # Persist summary CSV
    write_summary_csv(summary_path, summary_rows)
    print(f"\n[DONE] Summary written to {summary_path} ({len(summary_rows)} rows recorded).")

    return (1 if had_error else 0), summary_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/rf03_workload.json", help="Workload JSON config path")
    parser.add_argument("--dry-run", action="store_true", help="Perform preflight checks and print workload plan without running RapidFlow")
    parser.add_argument("--pilot", action="store_true", help="Run only the 3 designated pilot cases with 1 repeat each")
    parser.add_argument("--case-id", default=None, help="Execute only a single designated case ID")
    parser.add_argument("--repeats", type=int, default=None, help="Override repeat count per case")
    parser.add_argument("--timeout", type=float, default=None, help="Override per-run timeout in seconds")
    parser.add_argument("--rss-cap-mb", type=int, default=None, help="Override per-run RSS cap in MB")
    parser.add_argument("--summary-csv", default=None, help="Destination CSV path")
    parser.add_argument("--logs-dir", default=None, help="Base logs directory")
    parser.add_argument("--skip-memory-check", action="store_true", help="Bypass free RAM check (for mock unit tests only)")
    parser.add_argument("--skip-git-check", action="store_true", help="Bypass git commit check (for mock unit tests only)")
    parser.add_argument("--binary-path", default=None, help="Override RapidFlow executable path")
    parser.add_argument("--source-dir", default=None, help="Override RapidFlow source dir")

    args = parser.parse_args()
    workspace_root = pathlib.Path(__file__).resolve().parent.parent

    config_path = pathlib.Path(args.config)
    if not config_path.is_absolute():
        config_path = workspace_root / config_path

    summary_csv = pathlib.Path(args.summary_csv) if args.summary_csv else None
    if summary_csv and not summary_csv.is_absolute():
        summary_csv = workspace_root / summary_csv

    logs_dir = pathlib.Path(args.logs_dir) if args.logs_dir else None
    if logs_dir and not logs_dir.is_absolute():
        logs_dir = workspace_root / logs_dir

    bin_override = pathlib.Path(args.binary_path) if args.binary_path else None
    src_override = pathlib.Path(args.source_dir) if args.source_dir else None

    exit_code, _ = run_benchmark(
        config_path=config_path,
        workspace_root=workspace_root,
        dry_run=args.dry_run,
        pilot_only=args.pilot,
        target_case_id=args.case_id,
        repeats_override=args.repeats,
        timeout_override=args.timeout,
        rss_cap_override=args.rss_cap_mb,
        summary_csv_path=summary_csv,
        logs_base_dir=logs_dir,
        skip_memory_check=args.skip_memory_check,
        binary_path_override=bin_override,
        source_dir_override=src_override,
        skip_git_check=args.skip_git_check,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
