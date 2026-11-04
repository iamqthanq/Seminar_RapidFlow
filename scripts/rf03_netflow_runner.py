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
import math
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

EXPECTED_BASELINE_COMMIT = "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1"
EXPECTED_BINARY_SHA256 = "abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6"
EXPECTED_DATA_GRAPH_SHA256 = "bfedd2cab6d2c460141da945e77b0be39ae24c74d9a28b9394a38ee35b652fc6"
MIN_FREE_RAM_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB threshold
CANONICAL_BASELINE_CSV_REL = pathlib.Path("results/summary/rf03_baseline_runs.csv")


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
    command: List[str] = field(default_factory=list)


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
        command=list(cmd),
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


def recover_summary_from_logs(run_log_dir: pathlib.Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Recover summary rows from individual attempt metadata.json files in a run directory.
    Audited recovery: detects corrupt files, missing fields, and duplicate (case_id, repeat_index).
    Returns (recovered_rows, warnings_or_errors).
    """
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not run_log_dir.is_dir():
        return rows, [f"Directory not found: {run_log_dir}"]

    seen_keys = set()
    for meta_file in sorted(run_log_dir.glob("*_rep*/metadata.json")):
        try:
            content = meta_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as e:
            warnings.append(f"Corrupt or unreadable metadata in {meta_file}: {e}")
            continue

        if not isinstance(data, dict) or "summary_row" not in data or not isinstance(data["summary_row"], dict):
            warnings.append(f"Missing summary_row dictionary in {meta_file}")
            continue

        srow = data["summary_row"]
        key = (srow.get("case_id"), srow.get("repeat_index"))
        if key in seen_keys:
            warnings.append(f"Duplicate attempt metadata for case {key[0]} repeat {key[1]} at {meta_file}")
            continue
        seen_keys.add(key)
        rows.append(srow)

    return rows, warnings


def promote_run_to_canonical(
    run_id: str,
    workspace_root: pathlib.Path,
    logs_base_dir: Optional[pathlib.Path] = None,
    canonical_csv_rel: pathlib.Path = CANONICAL_BASELINE_CSV_REL,
    workload_config_rel: pathlib.Path = pathlib.Path("configs/rf03_workload.json"),
    dry_run: bool = False,
    expected_commit: str = EXPECTED_BASELINE_COMMIT,
    expected_binary_sha: str = EXPECTED_BINARY_SHA256,
) -> Tuple[bool, str]:
    """
    Explicitly promote a validated full benchmark run into the canonical baseline CSV.
    Strict promotion gates:
    1. Workload identity: Exact match against pinned cases in workload configuration.
    2. Repeat identity: Exact set of repeat indices (1..repeats_per_case) without gaps or duplicates.
    3. Completion: All rows status == 'COMPLETED', exit_code == 0, updates_num == prefix_length.
    4. Numerical consistency: Valid counts, 100% agreement across repeats, case verdict PASS_CONSISTENT.
    5. Provenance & Integrity: Verified run_manifest.json (commit, binary SHA, data SHA, query/stream SHAs, config SHA).
    6. Raw logs audit: Verified existence and SHA-256 match for stdout.log, stderr.log, metadata.json for every attempt.
    7. Safe non-destructive atomic replacement with unique timestamp+UUID backup.
    """
    logs_dir = logs_base_dir or (workspace_root / "results" / "logs" / "rf03")
    run_dir = logs_dir / run_id
    if not run_dir.is_dir():
        return False, f"Run directory not found: {run_dir}"

    cfg_path = workload_config_rel if workload_config_rel.is_absolute() else (workspace_root / workload_config_rel)
    if not cfg_path.is_file():
        return False, f"Workload configuration missing: {cfg_path}"

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Failed to read workload configuration {cfg_path}: {e}"

    expected_cases = {c["case_id"]: c for c in cfg.get("cases", [])}
    if not expected_cases:
        return False, f"Workload configuration contains no cases: {cfg_path}"

    expected_repeats = cfg.get("selection_policy", {}).get("repeats_per_case", 3)
    expected_total_rows = len(expected_cases) * expected_repeats
    expected_repeat_indices = {str(i) for i in range(1, expected_repeats + 1)}

    target_commit = cfg.get("source_git_commit", expected_commit)
    target_bin_sha = cfg.get("binary_sha256", expected_binary_sha)

    # 1. Provenance check: run_manifest.json
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return False, f"Promotion rejected: run_manifest.json missing in {run_dir}"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Promotion rejected: corrupt run_manifest.json in {run_dir}: {e}"

    if manifest.get("run_id") != run_id:
        return False, f"Promotion rejected: manifest run_id '{manifest.get('run_id')}' != '{run_id}'"
    if manifest.get("source_git_commit") != target_commit:
        return False, f"Promotion rejected: manifest commit '{manifest.get('source_git_commit')}' != expected '{target_commit}'"
    if manifest.get("binary_sha256") != target_bin_sha:
        return False, f"Promotion rejected: manifest binary SHA '{manifest.get('binary_sha256')}' != expected '{target_bin_sha}'"

    expected_data_sha = cfg.get("data_graph", {}).get("sha256")
    if expected_data_sha and manifest.get("data_graph_sha256") != expected_data_sha:
        return False, f"Promotion rejected: manifest data graph SHA '{manifest.get('data_graph_sha256')}' != expected '{expected_data_sha}'"

    actual_cfg_sha = file_sha256(cfg_path)
    if manifest.get("config_sha256") != actual_cfg_sha:
        return False, f"Promotion rejected: manifest config SHA '{manifest.get('config_sha256')}' != actual config SHA '{actual_cfg_sha}'"

    manifest_queries = manifest.get("queries", {})
    for qid, qinfo in cfg.get("queries", {}).items():
        mq = manifest_queries.get(qid, {})
        mq_sha = mq.get("sha256") if isinstance(mq, dict) else None
        if mq_sha != qinfo.get("sha256"):
            return False, f"Promotion rejected: manifest query SHA mismatch for {qid}: {mq_sha} != {qinfo.get('sha256')}"

    manifest_streams = manifest.get("streams", {})
    for sid, sinfo in cfg.get("streams", {}).items():
        ms = manifest_streams.get(sid, {})
        ms_sha = ms.get("sha256") if isinstance(ms, dict) else None
        if ms_sha != sinfo.get("sha256"):
            return False, f"Promotion rejected: manifest stream SHA mismatch for {sid}: {ms_sha} != {sinfo.get('sha256')}"

    # 2. Summary CSV read & verification
    summary_file = run_dir / "summary_runs.csv"
    if not summary_file.exists():
        summary_file = run_dir / "checkpoint_runs.csv"
    if not summary_file.exists():
        return False, f"No summary CSV found in {run_dir}"

    try:
        with open(summary_file, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        return False, f"Failed to read summary file {summary_file}: {e}"

    if len(rows) != expected_total_rows:
        return False, f"Promotion rejected: expected exactly {expected_total_rows} rows, got {len(rows)}"

    from collections import defaultdict
    case_map = defaultdict(list)
    observed_keys = set()

    for r in rows:
        if r.get("run_id") != run_id:
            return False, f"Promotion rejected: row run_id '{r.get('run_id')}' != '{run_id}'"

        cid = r.get("case_id")
        rep_idx = str(r.get("repeat_index"))
        key = (cid, rep_idx)
        if key in observed_keys:
            return False, f"Promotion rejected: duplicate case attempt {cid} rep {rep_idx}"
        observed_keys.add(key)

        if cid not in expected_cases:
            return False, f"Promotion rejected: unexpected case_id '{cid}' not declared in workload configuration"

        cspec = expected_cases[cid]
        expected_qid = cspec["query_id"]
        expected_sid = cspec["stream_id"]
        expected_prefix_len = str(cfg["streams"][expected_sid]["prefix_length"])

        if r.get("query_id") != expected_qid:
            return False, f"Promotion rejected: case {cid} query_id '{r.get('query_id')}' != expected '{expected_qid}'"
        if r.get("stream_id") != expected_sid:
            return False, f"Promotion rejected: case {cid} stream_id '{r.get('stream_id')}' != expected '{expected_sid}'"

        if r.get("status") != "COMPLETED":
            return False, f"Promotion rejected: case {cid} rep {rep_idx} status '{r.get('status')}' != COMPLETED"
        if str(r.get("exit_code")) != "0":
            return False, f"Promotion rejected: case {cid} rep {rep_idx} exit_code '{r.get('exit_code')}' != 0"
        if str(r.get("updates_num")) != expected_prefix_len:
            return False, f"Promotion rejected: case {cid} rep {rep_idx} updates_num '{r.get('updates_num')}' != {expected_prefix_len}"
        if str(r.get("edge_process_count")) != expected_prefix_len:
            return False, f"Promotion rejected: case {cid} rep {rep_idx} edge_process_count '{r.get('edge_process_count')}' != {expected_prefix_len}"

        res_cnt = r.get("result_count")
        if res_cnt is None:
            return False, f"Promotion rejected: case {cid} rep {rep_idx} missing result_count"
        try:
            cnt_val = int(res_cnt)
            if cnt_val < 0:
                return False, f"Promotion rejected: case {cid} rep {rep_idx} negative result_count: {cnt_val}"
        except ValueError:
            return False, f"Promotion rejected: case {cid} rep {rep_idx} non-integer result_count: '{res_cnt}'"

        case_map[cid].append(r)

    if set(case_map.keys()) != set(expected_cases.keys()):
        missing = set(expected_cases.keys()) - set(case_map.keys())
        return False, f"Promotion rejected: missing cases {missing}"

    # 3. Multi-repeat consistency, case verdicts, and raw log verification
    for cid, crows in case_map.items():
        if len(crows) != expected_repeats:
            return False, f"Promotion rejected: case {cid} has {len(crows)} repeats, expected {expected_repeats}"

        observed_rep_indices = {str(r.get("repeat_index")) for r in crows}
        if observed_rep_indices != expected_repeat_indices:
            return False, f"Promotion rejected: case {cid} repeat indices {observed_rep_indices} != expected {expected_repeat_indices}"

        counts = [r["result_count"] for r in crows]
        if len(set(counts)) != 1:
            return False, f"Promotion rejected: case {cid} divergent counts across repeats: {counts}"

        verdict_file = run_dir / f"{cid}_verdict.json"
        if not verdict_file.is_file():
            return False, f"Promotion rejected: verdict file missing for {cid}: {verdict_file}"
        try:
            vdata = json.loads(verdict_file.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"Promotion rejected: corrupt verdict file for {cid}: {e}"
        if vdata.get("verdict") != "PASS_CONSISTENT":
            return False, f"Promotion rejected: case {cid} verdict '{vdata.get('verdict')}' != PASS_CONSISTENT"
        if vdata.get("is_pass") is not True:
            return False, f"Promotion rejected: case {cid} is_pass is not True"
        if vdata.get("failure_reasons") != []:
            return False, f"Promotion rejected: case {cid} has failure reasons: {vdata.get('failure_reasons')}"
        if vdata.get("completed_repeats") != expected_repeats:
            return False, f"Promotion rejected: case {cid} verdict completed_repeats {vdata.get('completed_repeats')} != expected {expected_repeats}"

        # Reconcile case verdict result_counts with CSV rows (sorted by repeat_index)
        sorted_crows = sorted(crows, key=lambda x: int(x["repeat_index"]))
        csv_counts = [int(x["result_count"]) for x in sorted_crows]
        verdict_counts = [int(x) for x in vdata.get("result_counts", [])]
        if csv_counts != verdict_counts:
            return False, (
                f"Promotion rejected: case {cid} result_counts in verdict {verdict_counts} "
                f"!= CSV counts {csv_counts}"
            )

        for r in crows:
            rep = r["repeat_index"]
            rep_dir = run_dir / f"{cid}_rep{rep}"
            if not rep_dir.is_dir():
                return False, f"Promotion rejected: attempt log directory missing: {rep_dir}"
            stdout_file = rep_dir / "stdout.log"
            stderr_file = rep_dir / "stderr.log"
            meta_file = rep_dir / "metadata.json"
            if not stdout_file.is_file():
                return False, f"Promotion rejected: stdout.log missing in {rep_dir}"
            if not stderr_file.is_file():
                return False, f"Promotion rejected: stderr.log missing in {rep_dir}"
            if not meta_file.is_file():
                return False, f"Promotion rejected: metadata.json missing in {rep_dir}"

            act_stdout_sha = file_sha256(stdout_file)
            act_stderr_sha = file_sha256(stderr_file)
            if act_stdout_sha != r.get("stdout_sha256"):
                return False, f"Promotion rejected: stdout SHA mismatch for {cid} rep {rep}: {act_stdout_sha} != {r.get('stdout_sha256')}"
            if act_stderr_sha != r.get("stderr_sha256"):
                return False, f"Promotion rejected: stderr SHA mismatch for {cid} rep {rep}: {act_stderr_sha} != {r.get('stderr_sha256')}"

            # Cross-Artifact Reconciliation: CSV ↔ stdout.log
            stdout_content = stdout_file.read_text(encoding="utf-8", errors="replace")
            try:
                parsed_stdout = parse_rapidflow_stdout(stdout_content)
            except OutputParseError as ope:
                return False, f"Promotion rejected: stdout parsing failed for case {cid} rep {rep}: {ope}"

            if parsed_stdout.result_count is None:
                return False, f"Promotion rejected: stdout parsed result_count is None for case {cid} rep {rep}"
            if int(r["result_count"]) != parsed_stdout.result_count:
                return False, (
                    f"Promotion rejected: result_count mismatch for case {cid} rep {rep}: "
                    f"CSV={r.get('result_count')} != stdout={parsed_stdout.result_count}"
                )
            if int(r["updates_num"]) != parsed_stdout.updates_num:
                return False, (
                    f"Promotion rejected: updates_num mismatch for case {cid} rep {rep}: "
                    f"CSV={r.get('updates_num')} != stdout={parsed_stdout.updates_num}"
                )
            if int(r["edge_process_count"]) != parsed_stdout.edge_process_count:
                return False, (
                    f"Promotion rejected: edge_process_count mismatch for case {cid} rep {rep}: "
                    f"CSV={r.get('edge_process_count')} != stdout={parsed_stdout.edge_process_count}"
                )

            # Cross-Artifact Reconciliation: CSV ↔ metadata.json
            try:
                mdata = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception as e:
                return False, f"Promotion rejected: corrupt metadata in {rep_dir}: {e}"

            if mdata.get("status") != "COMPLETED":
                return False, f"Promotion rejected: metadata status '{mdata.get('status')}' != COMPLETED in {rep_dir}"
            if mdata.get("run_id") != run_id:
                return False, f"Promotion rejected: metadata run_id '{mdata.get('run_id')}' != '{run_id}' in {rep_dir}"
            if mdata.get("case_id") != cid:
                return False, f"Promotion rejected: metadata case_id '{mdata.get('case_id')}' != '{cid}' in {rep_dir}"
            if str(mdata.get("repeat_index")) != str(rep):
                return False, f"Promotion rejected: metadata repeat_index '{mdata.get('repeat_index')}' != '{rep}' in {rep_dir}"

            summary_row = mdata.get("summary_row")
            if not isinstance(summary_row, dict):
                return False, f"Promotion rejected: metadata missing valid summary_row dictionary in {rep_dir}"

            # String identity fields match
            str_fields = ["run_id", "case_id", "repeat_index", "query_id", "stream_id", "status"]
            for fld in str_fields:
                if str(r.get(fld)) != str(summary_row.get(fld)):
                    return False, (
                        f"Promotion rejected: {fld} mismatch for case {cid} rep {rep}: "
                        f"CSV={r.get(fld)} != metadata={summary_row.get(fld)}"
                    )

            # Integer fields match
            int_fields = ["result_count", "updates_num", "edge_process_count", "prefix_length", "exit_code", "rss_kb"]
            for fld in int_fields:
                csv_val = r.get(fld)
                meta_val = summary_row.get(fld)
                if csv_val is None or meta_val is None:
                    return False, f"Promotion rejected: field '{fld}' missing in CSV or metadata for case {cid} rep {rep}"
                try:
                    if int(csv_val) != int(meta_val):
                        return False, (
                            f"Promotion rejected: {fld} mismatch for case {cid} rep {rep}: "
                            f"CSV={csv_val} != metadata={meta_val}"
                        )
                except (ValueError, TypeError):
                    return False, f"Promotion rejected: invalid integer for {fld} in case {cid} rep {rep}"

            # Wall time float match
            csv_wall_str = r.get("wall_time_sec")
            meta_wall_str = summary_row.get("wall_time_sec")
            if csv_wall_str is None or meta_wall_str is None:
                return False, f"Promotion rejected: wall_time_sec missing in CSV or metadata for case {cid} rep {rep}"
            try:
                csv_wall = float(csv_wall_str)
                meta_wall = float(meta_wall_str)
                if math.isnan(csv_wall) or math.isnan(meta_wall) or math.isinf(csv_wall) or math.isinf(meta_wall) or csv_wall < 0 or meta_wall < 0:
                    return False, f"Promotion rejected: invalid wall_time_sec for case {cid} rep {rep}"
                if abs(csv_wall - meta_wall) > 1e-4:
                    return False, (
                        f"Promotion rejected: wall_time_sec mismatch for case {cid} rep {rep}: "
                        f"CSV={csv_wall} != metadata={meta_wall}"
                    )
            except (ValueError, TypeError):
                return False, f"Promotion rejected: non-float wall_time_sec for case {cid} rep {rep}"

            # Task C: Reconcile RSS from stderr if /usr/bin/time output is available
            stderr_content = stderr_file.read_text(encoding="utf-8", errors="replace")
            for line in stderr_content.splitlines():
                if "Maximum resident set size (kbytes):" in line:
                    try:
                        parsed_rss = int(line.split(":")[-1].strip())
                        if parsed_rss > 0:
                            csv_rss = int(r.get("rss_kb"))
                            if csv_rss != parsed_rss:
                                return False, (
                                    f"Promotion rejected: rss_kb mismatch for case {cid} rep {rep}: "
                                    f"CSV={csv_rss} != stderr={parsed_rss}"
                                )
                    except (ValueError, TypeError):
                        pass

    canonical_dest = (canonical_csv_rel if canonical_csv_rel.is_absolute() else (workspace_root / canonical_csv_rel)).resolve()
    if not dry_run:
        canonical_dest.parent.mkdir(parents=True, exist_ok=True)
        if canonical_dest.exists():
            ts_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            uuid_suffix = uuid.uuid4().hex[:8]
            backup_path = canonical_dest.parent / f"{canonical_dest.stem}_{ts_str}_{uuid_suffix}.csv.bak"
            shutil.copy2(canonical_dest, backup_path)
            if not backup_path.exists() or file_sha256(backup_path) != file_sha256(canonical_dest):
                return False, f"Promotion aborted: backup creation verification failed at {backup_path}"

        tmp_uuid = uuid.uuid4().hex[:8]
        tmp_dest = canonical_dest.parent / f".tmp_{canonical_dest.name}_{tmp_uuid}"
        shutil.copy2(summary_file, tmp_dest)
        tmp_dest.replace(canonical_dest)

    return True, f"Run {run_id} successfully promoted to canonical baseline CSV at {canonical_dest}"


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

    logs_dir = logs_base_dir or (workspace_root / "results" / "logs" / "rf03")

    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    run_log_dir = logs_dir / run_id
    host_info = get_host_telemetry()

    canonical_csv_path = workspace_root / CANONICAL_BASELINE_CSV_REL
    if summary_csv_path is not None:
        summary_path = summary_csv_path
        # Safety guard: prevent accidental direct overwrite of committed canonical CSV during benchmark execution
        if summary_path.resolve() == canonical_csv_path.resolve():
            raise RuntimeError(
                f"Directly targeting the canonical baseline CSV ({canonical_csv_path}) during benchmark execution is prohibited. "
                "Execute the benchmark to its isolated run directory, then invoke promote_run_to_canonical() after validating completeness."
            )
    else:
        # Default behavior: run summary is isolated strictly inside its own immutable run directory
        summary_path = run_log_dir / "summary_runs.csv"

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

    # Write immutable run-level manifest for linked provenance
    run_log_dir.mkdir(parents=True, exist_ok=True)
    bin_sha = file_sha256(binary_path) if binary_path.is_file() else None
    data_sha = file_sha256(data_graph_path) if data_graph_path.is_file() else None
    cfg_sha = file_sha256(config_path) if config_path.is_file() else None
    manifest = {
        "run_id": run_id,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command_argv": list(sys.argv),
        "workspace_root": str(workspace_root),
        "config_path": str(config_path),
        "config_sha256": cfg_sha,
        "source_dir": str(source_dir),
        "source_git_commit": EXPECTED_BASELINE_COMMIT,
        "binary_path": str(binary_path),
        "binary_sha256": bin_sha,
        "data_graph_path": str(data_graph_path),
        "data_graph_sha256": data_sha,
        "queries": {
            qid: {
                "path": str(workspace_root / q["path"]),
                "sha256": q["sha256"],
                "vertices": q.get("vertices"),
                "edges": q.get("edges"),
                "category": q.get("category"),
            }
            for qid, q in cfg.get("queries", {}).items()
        },
        "streams": {
            sid: {
                "path": str(workspace_root / s["path"]),
                "sha256": s["sha256"],
                "prefix_length": s.get("prefix_length"),
                "stream_type": s.get("stream_type"),
            }
            for sid, s in cfg.get("streams", {}).items()
        },
        "host_telemetry": host_info,
        "resource_policy": {
            "default_repeats": default_repeats,
            "default_timeout_sec": default_timeout,
            "default_rss_cap_mb": default_rss_cap,
            "runtime_rss_enforcement": False,
            "rss_check_mode": "post_execution_time_v",
            "safe_memory_floor_gb": 4.0,
        },
    }
    manifest_path = run_log_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

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
                "manifest_path": str(manifest_path),
                "rapidflow_command": sub_res.command,
                "command_argv": list(sys.argv),
                "binary_sha256": bin_sha,
                "data_graph_sha256": data_sha,
                "query_sha256": q_meta["sha256"],
                "stream_sha256": s_meta["sha256"],
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

        # Multi-repeat count consistency and completion check for the case
        case_rows = [r for r in summary_rows if r["case_id"] == c_id]
        completed_rows = [r for r in case_rows if r["status"] == "COMPLETED"]
        completed_repeats = len(completed_rows)
        attempted_repeats = len(case_rows)
        repeat_statuses = [r["status"] for r in case_rows]
        repeat_counts = [r["result_count"] for r in completed_rows if r.get("result_count") is not None]

        failure_reasons = []

        # 1. Repeat completion check
        if completed_repeats == 0:
            failure_reasons.append("ALL_REPEATS_FAILED")
        elif completed_repeats < default_repeats:
            failure_reasons.append("INCOMPLETE_REPEATS")

        # 2. Specific failure statuses directly emitted by runner attempts
        for st in repeat_statuses:
            if st != "COMPLETED" and st not in failure_reasons:
                failure_reasons.append(st)

        # 3. Result count validity & consistency among completed repeats
        if len(repeat_counts) > 0:
            try:
                int_counts = [int(cnt) for cnt in repeat_counts]
                if any(cnt < 0 for cnt in int_counts):
                    if "INVALID_RESULT_COUNT" not in failure_reasons:
                        failure_reasons.append("INVALID_RESULT_COUNT")
                if len(set(int_counts)) > 1:
                    if "COUNT_MISMATCH" not in failure_reasons:
                        failure_reasons.append("COUNT_MISMATCH")
            except (ValueError, TypeError):
                if "INVALID_RESULT_COUNT" not in failure_reasons:
                    failure_reasons.append("INVALID_RESULT_COUNT")

        # 4. Incomplete update stream processing on completed rows
        for r in completed_rows:
            if r.get("updates_num") != prefix_len or r.get("edge_process_count") != prefix_len:
                if "INCOMPLETE_STREAM" not in failure_reasons:
                    failure_reasons.append("INCOMPLETE_STREAM")

        is_divergent = "COUNT_MISMATCH" in failure_reasons
        if len(failure_reasons) == 0:
            verdict = "PASS_CONSISTENT"
            is_pass = True
        else:
            is_pass = False
            had_error = True
            if completed_repeats == 0:
                verdict = "FAIL_ALL_REPEATS_FAILED"
            elif is_divergent:
                verdict = "FAIL_COUNT_MISMATCH"
            else:
                verdict = "INCONCLUSIVE_INCOMPLETE_REPEATS"

        case_verdict = {
            "case_id": c_id,
            "query_id": q_id,
            "stream_id": s_id,
            "verdict": verdict,
            "is_pass": is_pass,
            "failure_reasons": failure_reasons,
            "expected_repeats": default_repeats,
            "attempted_repeats": attempted_repeats,
            "completed_repeats": completed_repeats,
            "repeat_statuses": repeat_statuses,
            "result_counts": repeat_counts,
            "is_divergent": is_divergent,
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        if not is_pass:
            had_error = True
            if is_divergent:
                mismatch_msg = f"COUNT_MISMATCH_ACROSS_REPEATS: completed repeats produced divergent result counts {repeat_counts}"
                print(f"[FAIL] {mismatch_msg} for case {c_id}", file=sys.stderr)
                for r in summary_rows:
                    if r["case_id"] == c_id:
                        if r["status"] == "COMPLETED":
                            r["status"] = "COUNT_MISMATCH_ACROSS_REPEATS"
                        r["error_message"] = mismatch_msg if not r.get("error_message") else f"{r.get('error_message')}; {mismatch_msg}"
            else:
                failure_summary = f"Case execution not fully consistent/completed: {', '.join(failure_reasons)}"
                print(f"[FAIL] {failure_summary} for case {c_id}", file=sys.stderr)
                for r in summary_rows:
                    if r["case_id"] == c_id:
                        if not r.get("error_message"):
                            r["error_message"] = failure_summary

            # Update on-disk metadata.json files for this case
            for rep in range(1, default_repeats + 1):
                rep_meta_file = run_log_dir / f"{c_id}_rep{rep}" / "metadata.json"
                if rep_meta_file.exists():
                    try:
                        m_data = json.loads(rep_meta_file.read_text(encoding="utf-8"))
                        matching_rows = [r for r in summary_rows if r["case_id"] == c_id and str(r["repeat_index"]) == str(rep)]
                        if matching_rows:
                            m_data["summary_row"] = matching_rows[0]
                            m_data["status"] = matching_rows[0]["status"]
                        m_data["case_verdict"] = case_verdict
                        rep_meta_file.write_text(json.dumps(m_data, indent=2) + "\n")
                    except Exception:
                        pass
        else:
            # Case passed: ensure metadata files record passing case_verdict
            for rep in range(1, default_repeats + 1):
                rep_meta_file = run_log_dir / f"{c_id}_rep{rep}" / "metadata.json"
                if rep_meta_file.exists():
                    try:
                        m_data = json.loads(rep_meta_file.read_text(encoding="utf-8"))
                        m_data["case_verdict"] = case_verdict
                        rep_meta_file.write_text(json.dumps(m_data, indent=2) + "\n")
                    except Exception:
                        pass

        # Write durable case verdict file
        (run_log_dir / f"{c_id}_verdict.json").write_text(json.dumps(case_verdict, indent=2) + "\n")

        # Immediately update the checkpoint CSV with the updated status
        run_checkpoint_csv = run_log_dir / "checkpoint_runs.csv"
        write_summary_csv(run_checkpoint_csv, summary_rows)
        if summary_path != run_checkpoint_csv:
            write_summary_csv(summary_path, summary_rows)

    # Persist summary CSV
    run_checkpoint_csv = run_log_dir / "checkpoint_runs.csv"
    write_summary_csv(run_checkpoint_csv, summary_rows)
    write_summary_csv(summary_path, summary_rows)
    print(f"\n[DONE] Summary written to {summary_path} ({len(summary_rows)} rows recorded).")
    if summary_path != run_checkpoint_csv:
        print(f"[DONE] Checkpoint preserved in {run_checkpoint_csv}.")

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
    parser.add_argument("--promote-run-id", default=None, help="Explicitly promote a validated 30-run suite from logs into canonical baseline CSV")

    args = parser.parse_args()
    workspace_root = pathlib.Path(__file__).resolve().parent.parent

    logs_dir = pathlib.Path(args.logs_dir) if args.logs_dir else None
    if logs_dir and not logs_dir.is_absolute():
        logs_dir = workspace_root / logs_dir

    if args.promote_run_id:
        cfg_path = pathlib.Path(args.config)
        ok, msg = promote_run_to_canonical(args.promote_run_id, workspace_root=workspace_root, logs_base_dir=logs_dir, workload_config_rel=cfg_path)
        if ok:
            print(f"[PROMOTION SUCCESS] {msg}")
            return 0
        else:
            print(f"[PROMOTION REJECTED] {msg}", file=sys.stderr)
            return 1

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
