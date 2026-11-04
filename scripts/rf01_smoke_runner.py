#!/usr/bin/env python3
"""
RF-01 Smoke runner: executes author insertion/deletion smoke cases using scripts/run_case.py,
validates result counts against expected author output, and records full summary metrics.
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
    if not resources_file.exists():
        return res
    for line in resources_file.read_text().splitlines():
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

def parse_result_count(stdout_file):
    if not stdout_file.exists():
        return None
    for line in stdout_file.read_text().splitlines():
        if "Result count:" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    return int(parts[1].strip())
                except ValueError:
                    pass
    return None

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
        count_match = False

        if raw_dir:
            raw_path = pathlib.Path(raw_dir)
            res = parse_resources(raw_path / "resources.txt")
            elapsed_seconds = res["elapsed_seconds"]
            max_rss_kb = res["max_rss_kb"]
            actual_count = parse_result_count(raw_path / "stdout.log")

            if expected_count is not None and actual_count is not None:
                count_match = (actual_count == expected_count)
            elif expected_count is None:
                count_match = True

        case_ok = (status == "completed" and exit_code == 0 and count_match)
        if not case_ok:
            all_passed = False

        print(f"Status: {status} | Actual: {actual_count} | Expected: {expected_count} | Match: {count_match} | Elapsed: {elapsed_seconds}s | RSS: {max_rss_kb} KB")

        summary_rows.append({
            "case_id": case_id,
            "stream_type": stream_type,
            "query": query_name,
            "status": status,
            "exit_code": exit_code,
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
        "case_id", "stream_type", "query", "status", "exit_code",
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
