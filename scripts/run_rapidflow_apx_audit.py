#!/usr/bin/env python3
"""
RapidFlow-APX Native Audit Runner (Gate P1 / Issue #28)

Orchestrates fail-closed execution of the native C++ audit harness (rapidflow_apx_audit)
against declared manifest test cases (configs/rapidflow_apx_audit_cases.json).

Enforces resource containment, process group cleanup, fixture SHA-256 validation,
binary provenance tracking, and structured schema verification.
"""

import argparse
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path


def compute_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 of a file."""
    if not file_path.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_commit(repo_dir: Path) -> str:
    """Retrieve current HEAD commit SHA."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        return out
    except Exception:
        return "UNKNOWN_COMMIT"


def is_git_dirty(repo_dir: Path) -> bool:
    """Check if repository has uncommitted changes."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        return len(out) > 0
    except Exception:
        return False


def build_native_harness(root_dir: Path, timeout: int = 120, address_space_mib: int = 1024) -> Path:
    """Build the native C++ harness using portable CMake with resource limits."""
    cmake_bin = root_dir / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"
    if not cmake_bin.exists():
        cmake_bin = Path(shutil.which("cmake") or "cmake")

    build_dir = root_dir / "tests" / "native" / "rapidflow_apx_audit" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    def preexec():
        os.setpgrp()
        as_bytes = address_space_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))

    # Configure
    configure_cmd = [str(cmake_bin), ".."]
    p_conf = subprocess.run(
        configure_cmd,
        cwd=build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        preexec_fn=preexec
    )
    if p_conf.returncode != 0:
        raise RuntimeError(f"CMake configuration failed (code {p_conf.returncode}):\n{p_conf.stderr}")

    # Build
    build_cmd = ["make", "-j2"]
    p_build = subprocess.run(
        build_cmd,
        cwd=build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        preexec_fn=preexec
    )
    if p_build.returncode != 0:
        raise RuntimeError(f"Build compilation failed (code {p_build.returncode}):\n{p_build.stderr}")

    binary_path = build_dir / "rapidflow_apx_audit"
    if not binary_path.exists():
        raise FileNotFoundError(f"Expected binary not found after build: {binary_path}")

    return binary_path


def run_probe_case(
    binary_path: Path,
    case_manifest: dict,
    root_dir: Path,
    binary_sha256: str,
    timeout: int = 30,
    address_space_mib: int = 512
) -> dict:
    """Execute a single audit probe via native binary under strict containment."""
    probe_group = case_manifest["probe_group"].lower().replace("probe_", "")
    fixture_path = root_dir / case_manifest["fixture_path"] if case_manifest.get("fixture_path") else None

    cmd = [
        str(binary_path),
        "--probe", probe_group,
        "--binary-sha", binary_sha256
    ]
    if fixture_path:
        cmd.extend(["--fixture", str(fixture_path)])

    def preexec():
        os.setpgrp()
        as_bytes = address_space_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))

    t0 = time.perf_counter_ns()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            preexec_fn=preexec
        )
        t1 = time.perf_counter_ns()
        duration_ms = (t1 - t0) / 1_000_000.0

        if proc.returncode != 0:
            return {
                "case_id": case_manifest["case_id"],
                "status": "FAIL",
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
                "error": proc.stderr or proc.stdout,
                "evidence": {}
            }

        data = json.loads(proc.stdout)
        probes = data.get("probes", [])
        if not probes:
            return {
                "case_id": case_manifest["case_id"],
                "status": "ERROR",
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
                "error": "Empty probe list in native JSON output",
                "evidence": {}
            }

        probe_res = probes[0]
        # Validate required evidence fields
        req_fields = case_manifest.get("required_evidence_fields", [])
        ev = probe_res.get("evidence", {})
        missing_fields = [f for f in req_fields if f not in ev]

        if missing_fields:
            return {
                "case_id": case_manifest["case_id"],
                "status": "FAIL",
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
                "error": f"Missing required evidence fields: {missing_fields}",
                "evidence": ev
            }

        status = "PASS_EXPECTED" if probe_res.get("pass") else "FAIL"
        return {
            "case_id": case_manifest["case_id"],
            "status": status,
            "exit_code": probe_res.get("exit_code", 0),
            "duration_ms": duration_ms,
            "summary": probe_res.get("summary", ""),
            "evidence": ev
        }

    except subprocess.TimeoutExpired:
        return {
            "case_id": case_manifest["case_id"],
            "status": "TIMEOUT",
            "exit_code": -1,
            "duration_ms": timeout * 1000.0,
            "error": f"Process exceeded wall timeout of {timeout}s",
            "evidence": {}
        }
    except Exception as e:
        return {
            "case_id": case_manifest["case_id"],
            "status": "ERROR",
            "exit_code": -1,
            "duration_ms": 0.0,
            "error": str(e),
            "evidence": {}
        }


def main():
    parser = argparse.ArgumentParser(description="Run RapidFlow-APX Native Audit Harness")
    parser.add_argument("--manifest", default="configs/rapidflow_apx_audit_cases.json", help="Path to manifest JSON")
    parser.add_argument("--output", default="reports/rapidflow_apx_audit/summary.json", help="Path to summary output JSON")
    parser.add_argument("--binary", default=None, help="Path to pre-built native binary")
    parser.add_argument("--clean-build", action="store_true", help="Clean build directory before compiling")
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parent.parent
    manifest_path = (root_dir / args.manifest) if not Path(args.manifest).is_absolute() else Path(args.manifest)
    output_path = (root_dir / args.output) if not Path(args.output).is_absolute() else Path(args.output)

    if not manifest_path.exists():
        sys.stderr.write(f"FATAL: Manifest not found at {manifest_path}\n")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest_sha = compute_sha256(manifest_path)
    git_head = get_git_commit(root_dir)
    git_dirty = is_git_dirty(root_dir)
    upstream_dir = root_dir / "baselines" / "rapidflow" / "source"
    upstream_head = get_git_commit(upstream_dir)

    # 1. Validate fixtures
    for case in manifest.get("cases", []):
        fpath = case.get("fixture_path")
        fsha = case.get("fixture_sha256")
        if fpath and fsha:
            actual_fpath = root_dir / fpath
            actual_sha = compute_sha256(actual_fpath)
            if actual_sha != fsha:
                sys.stderr.write(f"FATAL: Fixture SHA-256 mismatch for {fpath}: expected {fsha}, got {actual_sha}\n")
                sys.exit(1)

    # 2. Build or locate binary
    binary_path = Path(args.binary) if args.binary else None
    if not binary_path or not binary_path.exists():
        binary_path = build_native_harness(root_dir)

    binary_sha = compute_sha256(binary_path)

    # 3. Execute cases
    results = []
    cases = manifest.get("cases", [])
    if not cases:
        sys.stderr.write("FATAL: Manifest contains 0 test cases; fail-closed rejection\n")
        sys.exit(1)

    overall_pass = True
    for case in cases:
        c_res = run_probe_case(
            binary_path=binary_path,
            case_manifest=case,
            root_dir=root_dir,
            binary_sha256=binary_sha,
            timeout=case.get("limits", {}).get("timeout_seconds", 30),
            address_space_mib=case.get("limits", {}).get("max_address_space_mib", 512)
        )
        results.append(c_res)
        if c_res["status"] != "PASS_EXPECTED":
            overall_pass = False

    # 4. Generate summary report
    summary = {
        "run_id": f"RUN_{int(time.time())}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest_path": str(manifest_path.relative_to(root_dir)),
        "manifest_version": manifest.get("manifest_version", "1.0"),
        "manifest_sha256": manifest_sha,
        "execution_head": git_head,
        "dirty": git_dirty,
        "base_commit": manifest.get("base_commit"),
        "upstream_sha": upstream_head,
        "binary_path": str(binary_path.relative_to(root_dir)),
        "binary_sha256": binary_sha,
        "overall_status": "PASS" if overall_pass else "FAIL",
        "total_cases": len(results),
        "passed_cases": sum(1 for r in results if r["status"] == "PASS_EXPECTED"),
        "failed_cases": sum(1 for r in results if r["status"] != "PASS_EXPECTED"),
        "results": results
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
