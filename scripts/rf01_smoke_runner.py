#!/usr/bin/env python3
"""
RF-01 Smoke runner: executes author insertion/deletion smoke cases using scripts/run_case.py,
validates result counts against author reference outputs, prevents false passes,
and records comprehensive summary metrics.
"""
import argparse
import csv
import json
import os
import pathlib
import re
import subprocess
import sys

def parse_resources(resources_file):
    res = {"elapsed_seconds": None, "max_rss_kb": None, "exit_status": None}
    resources_path = pathlib.Path(resources_file)
    if not resources_path.is_file():
        return res
    try:
        content = resources_path.read_text(encoding="utf-8")
    except OSError:
        return res

    for line in content.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "elapsed_seconds":
                try: res["elapsed_seconds"] = float(v)
                except ValueError: pass
            elif k == "max_rss_kb":
                try: res["max_rss_kb"] = int(v)
                except ValueError: pass
            elif k == "exit_status":
                try: res["exit_status"] = int(v)
                except ValueError: pass
    return res

def parse_result_count_from_text(text):
    """
    Extracts the integer result count from stdout content.
    Returns int if a valid 'Result count: <int>' line is found, else None.
    """
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Result count:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                val = parts[1].strip()
                if val.isdigit():
                    return int(val)
    return None

def parse_result_count(stdout_file):
    stdout_path = pathlib.Path(stdout_file)
    if not stdout_path.is_file():
        return None
    try:
        content = stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_result_count_from_text(content)

def load_author_expected_counts(expected_file_path):
    """
    Reads author's expected_output.txt formatted as:
      Q_0:587884
      Q_1:12866379
    Returns dict mapping query_name to int count.
    """
    p = pathlib.Path(expected_file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Author expected output file not found: {p}")
    results = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            q_name, count_str = line.split(":", 1)
            q_name = q_name.strip()
            count_str = count_str.strip()
            if count_str.isdigit():
                results[q_name] = int(count_str)
    return results

def evaluate_smoke_case(status, exit_code, actual_count, expected_count):
    """
    Pure evaluation function to determine if a smoke test run is a verified PASS or FAIL.
    Prevents false PASS when actual_count is missing or when expected_count is unprovided.

    Returns dict:
      - case_ok (bool): True ONLY if execution succeeded (completed, exit 0),
                        actual_count is a valid integer, expected_count is provided,
                        and actual_count == expected_count.
      - verdict (str): Status code describing the outcome.
      - count_match (Optional[bool]): True/False if compared; None if unverified/missing.
      - details (str): Descriptive text.
    """
    if status != "completed":
        return {
            "case_ok": False,
            "verdict": f"FAIL_PROCESS_STATUS_{status.upper()}",
            "count_match": False,
            "details": f"Process execution ended with status '{status}' (expected 'completed')"
        }

    if exit_code != 0:
        return {
            "case_ok": False,
            "verdict": "FAIL_NONZERO_EXIT",
            "count_match": False,
            "details": f"Process exited with non-zero exit code {exit_code}"
        }

    if actual_count is None:
        return {
            "case_ok": False,
            "verdict": "FAIL_MISSING_RESULT_COUNT",
            "count_match": False,
            "details": "Process exited 0 but stdout contained no parseable 'Result count: <int>'"
        }

    if expected_count is None:
        # Crucial fix for R4: never mark unverified executions as verified PASS
        return {
            "case_ok": False,
            "verdict": "UNVERIFIED_NO_EXPECTED_COUNT",
            "count_match": None,
            "details": f"Executed successfully with Result count={actual_count}, but no expected count provided for reference verification"
        }

    if actual_count != expected_count:
        return {
            "case_ok": False,
            "verdict": "FAIL_COUNT_MISMATCH",
            "count_match": False,
            "details": f"Count mismatch: actual {actual_count} != expected {expected_count}"
        }

    return {
        "case_ok": True,
        "verdict": "PASS_VERIFIED",
        "count_match": True,
        "details": f"Result count {actual_count} matches author reference {expected_count}"
    }

def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(repo_root / "configs/rf01_smoke_cases.json"),
                    help="Path to smoke test configuration JSON")
    ap.add_argument("--output-csv", default=str(repo_root / "results/summary/rf01_smoke_summary.csv"),
                    help="Path to output summary CSV")
    args = ap.parse_args()

    config_path = pathlib.Path(args.config).resolve()
    if not config_path.is_file():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        return 1

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    source_commit = config.get("source_commit", "2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1")
    binary_path = (repo_root / config["binary_path"]).resolve()
    if not binary_path.is_file():
        print(f"ERROR: RapidFlow binary not found: {binary_path}", file=sys.stderr)
        print("Please build pristine RapidFlow first.", file=sys.stderr)
        return 1

    cases = config.get("cases", [])
    if not cases:
        print("ERROR: No test cases found in config.", file=sys.stderr)
        return 1

    # Optional pre-validation: check author reference expected output files if specified
    author_references = config.get("author_reference_files", {})
    loaded_references = {}
    for stream_type, ref_info in author_references.items():
        ref_path = (repo_root / ref_info["path"]).resolve()
        if ref_path.is_file():
            loaded_references[stream_type] = load_author_expected_counts(ref_path)

    run_case_script = repo_root / "scripts/run_case.py"
    summary_rows = []
    all_passed = True

    print(f"=== Starting RF-01 Smoke Suite ({len(cases)} cases) ===")
    print(f"Binary: {binary_path}")
    print(f"Source Commit: {source_commit}\n")

    for case in cases:
        case_id = case["case_id"]
        stream_type = case["stream_type"]
        query_name = case["query_name"]
        d_path = str(repo_root / case["dataset_path"])
        s_path = str(repo_root / case["stream_path"])
        q_path = str(repo_root / case["query_path"])
        expected_count = case.get("expected_count")

        # Cross-verify expected count with author reference file if available
        if stream_type in loaded_references and query_name in loaded_references[stream_type]:
            ref_expected = loaded_references[stream_type][query_name]
            if expected_count is not None and expected_count != ref_expected:
                print(f"WARNING: Config expected_count ({expected_count}) does not match author reference file ({ref_expected}) for {query_name}!", file=sys.stderr)

        num = str(case.get("num", "MAX"))
        time_limit = str(case.get("time_limit", 3600))
        timeout = case.get("timeout", 60)

        cmd = [
            sys.executable,
            str(run_case_script),
            "--case-id", case_id,
            "--dataset-path", d_path,
            "--query-path", q_path,
            "--stream-path", s_path,
            "--timeout", str(timeout),
            "--mode", "count_only",
            "--source-commit", source_commit,
            "--",
            str(binary_path),
            "-d", d_path,
            "-q", q_path,
            "-u", s_path,
            "-num", num,
            "-time_limit", time_limit
        ]

        print(f"Running {case_id} [{stream_type} | {query_name}]...", end=" ", flush=True)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(repo_root))

        raw_dir = None
        status = "error"
        exit_code = proc.returncode
        wall_seconds = None

        try:
            run_meta = json.loads(proc.stdout.strip())
            raw_dir = run_meta.get("dir")
            status = run_meta.get("status", status)
            exit_code = run_meta.get("exit_code", exit_code)
            wall_seconds = run_meta.get("wall_seconds")
        except Exception:
            pass

        elapsed_seconds = None
        max_rss_kb = None
        actual_count = None

        if raw_dir:
            raw_path = pathlib.Path(raw_dir)
            res = parse_resources(raw_path / "resources.txt")
            elapsed_seconds = res["elapsed_seconds"]
            max_rss_kb = res["max_rss_kb"]
            actual_count = parse_result_count(raw_path / "stdout.log")

        # Rigorous evaluation via pure evaluate_smoke_case
        evaluation = evaluate_smoke_case(status, exit_code, actual_count, expected_count)
        case_ok = evaluation["case_ok"]
        verdict = evaluation["verdict"]
        count_match = evaluation["count_match"]

        if not case_ok:
            all_passed = False

        print(f"Verdict: {verdict} | Actual: {actual_count} | Expected: {expected_count} | Match: {count_match} | Elapsed: {elapsed_seconds}s | RSS: {max_rss_kb} KB")

        summary_rows.append({
            "case_id": case_id,
            "stream_type": stream_type,
            "query": query_name,
            "status": status,
            "exit_code": exit_code,
            "verdict": verdict,
            "wall_seconds": wall_seconds,
            "elapsed_seconds": elapsed_seconds,
            "max_rss_kb": max_rss_kb,
            "expected_count": expected_count,
            "actual_count": actual_count,
            "count_match": count_match,
            "output_mode": "count_only",
            "raw_log_dir": raw_dir
        })

    # Write summary CSV
    output_csv_path = pathlib.Path(args.output_csv).resolve()
    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id", "stream_type", "query", "status", "exit_code", "verdict",
        "wall_seconds", "elapsed_seconds", "max_rss_kb",
        "expected_count", "actual_count", "count_match",
        "output_mode", "raw_log_dir"
    ]

    with output_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nSummary CSV saved to: {output_csv_path}")
    print(f"Overall suite status: {'PASS' if all_passed else 'FAIL'}")
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
