#!/usr/bin/env python3
"""
RF-01 Smoke runner: executes author insertion/deletion smoke cases using scripts/run_case.py,
validates result counts against author reference outputs with fail-closed provenance checks,
tracks telemetry evidence completeness, and records comprehensive summary metrics.
"""
import argparse
import csv
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

class ReferenceVerificationError(Exception):
    """Raised when author reference file is missing, corrupt, or contradicts configuration."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

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

def verify_and_load_author_reference(file_path, expected_sha256=None):
    """
    Validates author reference file existence, SHA-256 integrity, and parses expected counts.
    Fails closed if file is missing, hash mismatches, or file contains invalid integer values.
    Returns dict mapping query_name to int count.
    """
    p = pathlib.Path(file_path)
    if not p.is_file():
        raise ReferenceVerificationError("FILE_NOT_FOUND", f"Author reference file missing: {p}")

    if expected_sha256:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual_sha = h.hexdigest().lower()
        if actual_sha != expected_sha256.lower():
            raise ReferenceVerificationError(
                "SHA256_MISMATCH",
                f"Author reference file SHA-256 mismatch for {p.name}: expected {expected_sha256}, got {actual_sha}"
            )

    results = {}
    lines = p.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        q_name, count_str = line.split(":", 1)
        q_name = q_name.strip()
        count_str = count_str.strip()
        if not count_str.isdigit():
            raise ReferenceVerificationError(
                "MALFORMED_INTEGER",
                f"Non-integer value '{count_str}' for query '{q_name}' at line {idx} in {p.name}"
            )
        results[q_name] = int(count_str)
    return results

def evaluate_telemetry(elapsed_seconds, max_rss_kb, raw_dir):
    """
    F2: Evaluates telemetry evidence completeness required for Gate G0 acceptance.
    Separates runtime metrics availability from matching count verification.
    """
    if elapsed_seconds is not None and max_rss_kb is not None and max_rss_kb > 0 and raw_dir:
        return "COMPLETE_METRICS"
    return "INCOMPLETE_METRICS"

def evaluate_smoke_case(status, exit_code, actual_count, expected_count,
                        ref_expected_count=None, ref_error_code=None, ref_error_msg=None):
    """
    Pure evaluation function to determine if a smoke test run is a verified PASS or FAIL.
    F1: Fail closed if reference provenance verification fails or config contradicts reference.
    F4: Never false PASS when actual_count is missing or when expected_count is unprovided.

    Returns dict:
      - case_ok (bool): True ONLY if execution succeeded (completed, exit 0),
                        reference verification succeeded, actual_count is valid integer,
                        and actual_count == expected_count == ref_expected_count.
      - verdict (str): Status code describing the outcome.
      - count_match (Optional[bool]): True/False if compared; None if unverified/missing.
      - details (str): Descriptive text.
    """
    # F1: Fail closed on reference provenance defects
    if ref_error_code:
        return {
            "case_ok": False,
            "verdict": f"FAIL_REFERENCE_{ref_error_code}",
            "count_match": False if actual_count is not None and expected_count is not None else None,
            "details": ref_error_msg or f"Author reference verification failed: {ref_error_code}"
        }

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

    # F1: Fail-closed verification and loading of author reference files
    author_references = config.get("author_reference_files", {})
    loaded_references = {}
    reference_errors = {}

    for stream_type, ref_info in author_references.items():
        ref_path = (repo_root / ref_info["path"]).resolve()
        expected_sha = ref_info.get("sha256")
        try:
            loaded_references[stream_type] = verify_and_load_author_reference(ref_path, expected_sha)
        except ReferenceVerificationError as e:
            reference_errors[stream_type] = e
            print(f"ERROR in author reference for '{stream_type}': {e.message}", file=sys.stderr)

    run_case_script = repo_root / "scripts/run_case.py"
    summary_rows = []
    all_passed = True
    all_telemetry_complete = True

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

        # F1: Fail-closed check against author reference
        ref_error_code = None
        ref_error_msg = None
        ref_expected_count = None

        if stream_type in reference_errors:
            ref_err = reference_errors[stream_type]
            ref_error_code = ref_err.code
            ref_error_msg = ref_err.message
        elif stream_type in loaded_references:
            stream_ref = loaded_references[stream_type]
            if query_name not in stream_ref:
                ref_error_code = "QUERY_NOT_FOUND"
                ref_error_msg = f"Query {query_name} not found in author reference for {stream_type}"
            else:
                ref_expected_count = stream_ref[query_name]
                if expected_count is not None and expected_count != ref_expected_count:
                    ref_error_code = "CONFIG_MISMATCH"
                    ref_error_msg = (
                        f"Config expected count ({expected_count}) contradicts "
                        f"author reference ({ref_expected_count}) for {query_name}"
                    )
        elif case.get("expected_source"):
            ref_error_code = "REF_STREAM_MISSING"
            ref_error_msg = f"Author reference definition missing for stream_type '{stream_type}'"

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

        # F1 evaluation
        evaluation = evaluate_smoke_case(
            status=status,
            exit_code=exit_code,
            actual_count=actual_count,
            expected_count=expected_count,
            ref_expected_count=ref_expected_count,
            ref_error_code=ref_error_code,
            ref_error_msg=ref_error_msg
        )
        case_ok = evaluation["case_ok"]
        verdict = evaluation["verdict"]
        count_match = evaluation["count_match"]

        # F2 evaluation
        evidence_status = evaluate_telemetry(elapsed_seconds, max_rss_kb, raw_dir)

        if not case_ok:
            all_passed = False
        if evidence_status != "COMPLETE_METRICS":
            all_telemetry_complete = False

        print(f"Verdict: {verdict} | Metrics: {evidence_status} | Actual: {actual_count} | Expected: {expected_count} | Match: {count_match} | Elapsed: {elapsed_seconds}s | RSS: {max_rss_kb} KB")

        summary_rows.append({
            "case_id": case_id,
            "stream_type": stream_type,
            "query": query_name,
            "status": status,
            "exit_code": exit_code,
            "verdict": verdict,
            "evidence_status": evidence_status,
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
        "evidence_status", "wall_seconds", "elapsed_seconds", "max_rss_kb",
        "expected_count", "actual_count", "count_match",
        "output_mode", "raw_log_dir"
    ]

    with output_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nSummary CSV saved to: {output_csv_path}")
    print(f"Suite verification status: {'PASS' if all_passed else 'FAIL'}")
    print(f"G0 telemetry evidence completeness: {'COMPLETE' if all_telemetry_complete else 'INCOMPLETE'}")
    return 0 if (all_passed and all_telemetry_complete) else 1

if __name__ == "__main__":
    sys.exit(main())
