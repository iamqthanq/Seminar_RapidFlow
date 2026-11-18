#!/usr/bin/env python3
"""
RapidFlow-APX Native Audit Runner (Gate P1 / Issue #28 Remediation F01-F06)

Orchestrates fail-closed execution of the native C++ audit harness (rapidflow_apx_audit)
against strictly locked manifest test cases (configs/rapidflow_apx_audit_cases.json).

Enforces:
- Exact 6 mandatory cases with rigid ID-to-group-to-fixture mapping.
- Subprocess process group containment and signal termination.
- Preflight resource checks (RAM >= 2 GiB, free disk >= 1 GiB).
- Rigid payload verification (boolean pass, internal exit code 0, evidence values verified).
- Independent M0 Oracle cross-validation on Probe D and Probe F fixtures.
- Upstream pristine state tracking and structured error reporting under partial failure.
"""

import argparse
import hashlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Add repo root to import oracle
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.dstar_oracle import DStarGraph, enumerate_snapshot

MANDATORY_CASES = {
    "CASE_PROBE_A_PROVENANCE": {
        "probe_group": "PROBE_A",
        "required_native_component": "baselines/rapidflow/source/graph/graph.cpp",
        "fixture_path": None,
        "evidence_checks": {
            "graph_accessors_verified": "true",
            "runtime_exit_code": "0"
        }
    },
    "CASE_PROBE_B_TYPED_BOUNDARY": {
        "probe_group": "PROBE_B",
        "required_native_component": "baselines/rapidflow/source/graph/graph.cpp",
        "fixture_path": "tests/fixtures/rapidflow_apx_audit/typed_boundary_fixture.json",
        "evidence_checks": {
            "sparse_id_mapped": "true",
            "negative_id_mapped": "true",
            "large_id_mapped": "true",
            "int_zero_vs_str_zero_separated": "true",
            "codec_roundtrip_verified": "true",
            "graph_native_loaded": "true",
            "fixture_bound": "true"
        }
    },
    "CASE_PROBE_C_GLOBAL_EDGE_VIEW": {
        "probe_group": "PROBE_C",
        "required_native_component": "baselines/rapidflow/source/utility/relation/global_edge_view.h",
        "fixture_path": None,
        "evidence_checks": {
            "insertion_sorted": "true",
            "duplicate_insert_rejected": "true",
            "absent_delete_rejected": "true",
            "cardinality_consistent": "true",
            "edge_label_absent_in_container": "true",
            "pointer_invalidation_noted": "true"
        }
    },
    "CASE_PROBE_D_EXACT_NLF_COUNTEREXAMPLE": {
        "probe_group": "PROBE_D",
        "required_native_component": "baselines/rapidflow/source/streaming/global_view_manager.h",
        "fixture_path": "tests/fixtures/rapidflow_apx_audit/diamond_k1_fixture.json",
        "evidence_checks": {
            "m0_ground_truth_accepted": "true",
            "s2_spanning_connected": "true",
            "rapidflow_nlf_check_result": "false",
            "candidate_filter_excluded_vertices": "[1, 2]",
            "raw_view_edges_present": "true",
            "incompatibility_proven": "true",
            "native_gvm_executed": "true",
            "fixture_bound": "true"
        }
    },
    "CASE_PROBE_E_SNAPSHOT_ROLLBACK": {
        "probe_group": "PROBE_E",
        "required_native_component": "baselines/rapidflow/source/streaming/streaming_engine.cpp",
        "fixture_path": "tests/fixtures/rapidflow_apx_audit/transaction_stream_fixture.json",
        "evidence_checks": {
            "insert_snapshot_verified": "true",
            "delete_snapshot_verified": "true",
            "fault_injected": "true",
            "rollback_executed": "true",
            "pre_fault_state_preserved": "true",
            "fixture_bound": "true",
            "adapter_evidence_level": "NEW_AUDIT_ADAPTER_RUNTIME",
            "upstream_engine_evidence_level": "SOURCE_INSPECTED"
        }
    },
    "CASE_PROBE_F_COUNT_TUPLE_BOUNDARY": {
        "probe_group": "PROBE_F",
        "required_native_component": "baselines/rapidflow/source/streaming/search_engine.cpp",
        "fixture_path": "tests/fixtures/rapidflow_apx_audit/k4_k0_fixture.json",
        "evidence_checks": {
            "m0_distinct_tuples_count": "24",
            "rapidflow_output_mode": "COUNT_ONLY",
            "leaf_shortcut_line": "search_engine.cpp:161",
            "automorphism_factor_line": "streaming_engine.cpp:181",
            "p2_tuple_path_design": "UNROLL_LEAF_EXPAND_AUTOMORPHISMS_WITH_CANONICAL_PRUNING",
            "fixture_bound": "true",
            "evidence_level": "SOURCE_INSPECTED"
        }
    }
}


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
    """Retrieve current commit SHA."""
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
    """Check if repository or submodule has uncommitted changes."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        return len(out) > 0
    except Exception:
        return False


def check_preflight_resources(root_dir: Path, min_ram_bytes: int = 2 * 1024 * 1024 * 1024, min_disk_bytes: int = 1024 * 1024 * 1024) -> None:
    """Preflight check: available RAM >= 2 GiB and free disk >= 1 GiB."""
    mem_avail = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    mem_avail = kb * 1024
                    break
    except Exception:
        pass
    if mem_avail is None:
        try:
            mem_avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        except Exception:
            pass

    if mem_avail is not None and mem_avail < min_ram_bytes:
        raise RuntimeError(f"Preflight RAM check failed: available {mem_avail / (1024*1024):.1f} MiB < required {min_ram_bytes / (1024*1024):.1f} MiB")

    disk_free = shutil.disk_usage(root_dir).free
    if disk_free < min_disk_bytes:
        raise RuntimeError(f"Preflight disk check failed: free {disk_free / (1024*1024):.1f} MiB < required {min_disk_bytes / (1024*1024):.1f} MiB")


def execute_contained_subprocess(cmd: list, cwd: Path = None, timeout: int = 30, address_space_mib: int = 512) -> tuple:
    """Execute subprocess under process group session with strict resource limits and cleanup."""
    def preexec():
        as_bytes = address_space_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))

    try:
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            preexec_fn=preexec
        )
    except Exception as e:
        return -1, "", str(e), False

    try:
        stdout, stderr = p.communicate(timeout=timeout)
        return p.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(p.pid)
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            pass
        try:
            stdout, stderr = p.communicate(timeout=2)
        except Exception:
            stdout, stderr = "", ""
        return -1, stdout, stderr, True
    except Exception as e:
        try:
            pgid = os.getpgid(p.pid)
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            pass
        return -1, "", str(e), False


def validate_manifest(manifest: dict, root_dir: Path) -> list:
    """Validate locked manifest schema, cases, fixture hashes, and resource limits."""
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest must be a JSON object, got {type(manifest).__name__}")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != len(MANDATORY_CASES):
        raise ValueError(f"Manifest must contain exactly {len(MANDATORY_CASES)} mandatory cases, got {len(cases) if isinstance(cases, list) else type(cases).__name__}")

    seen_ids = set()
    validated_cases = []

    for case in cases:
        if not isinstance(case, dict):
            raise ValueError(f"Case entry must be a dict, got {type(case).__name__}")

        cid = case.get("case_id")
        if not cid or cid not in MANDATORY_CASES:
            raise ValueError(f"Unknown or invalid case_id: {cid}")
        if cid in seen_ids:
            raise ValueError(f"Duplicate case_id in manifest: {cid}")
        seen_ids.add(cid)

        spec = MANDATORY_CASES[cid]
        if case.get("probe_group") != spec["probe_group"]:
            raise ValueError(f"Probe group mismatch for {cid}: expected {spec['probe_group']}, got {case.get('probe_group')}")
        if case.get("required_native_component") != spec["required_native_component"]:
            raise ValueError(f"Required component mismatch for {cid}: expected {spec['required_native_component']}")
        if case.get("fixture_path") != spec["fixture_path"]:
            raise ValueError(f"Fixture path mismatch for {cid}: expected {spec['fixture_path']}")

        # Validate limits
        limits = case.get("limits")
        if not isinstance(limits, dict):
            raise ValueError(f"Limits must be a dict for case {cid}")
        timeout = limits.get("timeout_seconds")
        as_mib = limits.get("max_address_space_mib")

        if type(timeout) is not int or timeout <= 0 or timeout > 120:
            raise ValueError(f"Invalid timeout_seconds for {cid}: {timeout} (must be int in 1..120)")
        if type(as_mib) is not int or as_mib <= 0 or as_mib > 1024:
            raise ValueError(f"Invalid max_address_space_mib for {cid}: {as_mib} (must be int in 1..1024)")

        # Validate required evidence field keys
        req_fields = case.get("required_evidence_fields")
        if not isinstance(req_fields, list) or not req_fields:
            raise ValueError(f"required_evidence_fields must be a non-empty list for {cid}")
        for k in spec["evidence_checks"].keys():
            if k not in req_fields:
                raise ValueError(f"Missing mandatory evidence field declaration '{k}' in case {cid}")

        # Validate fixture file and hash if present
        if spec["fixture_path"]:
            fpath = root_dir / spec["fixture_path"]
            if not fpath.exists():
                raise FileNotFoundError(f"Fixture file missing for {cid}: {fpath}")
            expected_sha = case.get("fixture_sha256")
            actual_sha = compute_sha256(fpath)
            if actual_sha != expected_sha:
                raise ValueError(f"Fixture SHA-256 mismatch for {cid}: expected {expected_sha}, got {actual_sha}")

        validated_cases.append(case)

    return validated_cases


def build_native_harness(root_dir: Path, clean_build: bool = False, timeout: int = 120, address_space_mib: int = 1024) -> Path:
    """Build the native C++ harness using portable CMake under containment."""
    cmake_bin = root_dir / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"
    if not cmake_bin.exists():
        cmake_bin = Path(shutil.which("cmake") or "cmake")

    build_dir = root_dir / "tests" / "native" / "rapidflow_apx_audit" / "build"
    if clean_build and build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    # Configure
    configure_cmd = [str(cmake_bin), ".."]
    ret, out, err, timed_out = execute_contained_subprocess(
        configure_cmd, cwd=build_dir, timeout=timeout, address_space_mib=address_space_mib
    )
    if timed_out or ret != 0:
        raise RuntimeError(f"CMake configuration failed (ret={ret}, timed_out={timed_out}):\n{err or out}")

    # Build
    build_cmd = ["make", "-j2"]
    ret, out, err, timed_out = execute_contained_subprocess(
        build_cmd, cwd=build_dir, timeout=timeout, address_space_mib=address_space_mib
    )
    if timed_out or ret != 0:
        raise RuntimeError(f"CMake compilation failed (ret={ret}, timed_out={timed_out}):\n{err or out}")

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
    """Execute a single audit probe via native binary with strict payload and evidence verification."""
    cid = case_manifest["case_id"]
    spec = MANDATORY_CASES[cid]
    probe_group = spec["probe_group"].lower().replace("probe_", "")

    fpath_str = case_manifest.get("fixture_path")
    if fpath_str is not None:
        fixture_path = root_dir / fpath_str
        if not fixture_path.exists():
            return {
                "case_id": cid,
                "status": "FAIL",
                "exit_code": -1,
                "duration_ms": 0.0,
                "error": f"Declared fixture file does not exist: {fixture_path}",
                "evidence": {}
            }
    else:
        fixture_path = None

    cmd = [
        str(binary_path),
        "--probe", probe_group,
        "--binary-sha", binary_sha256
    ]
    if fixture_path:
        cmd.extend(["--fixture", str(fixture_path)])

    t0 = time.perf_counter_ns()
    ret, stdout, stderr, timed_out = execute_contained_subprocess(
        cmd, cwd=root_dir, timeout=timeout, address_space_mib=address_space_mib
    )
    t1 = time.perf_counter_ns()
    duration_ms = (t1 - t0) / 1_000_000.0

    if timed_out:
        return {
            "case_id": cid,
            "status": "TIMEOUT",
            "exit_code": -1,
            "duration_ms": duration_ms,
            "error": f"Process exceeded wall timeout of {timeout}s",
            "evidence": {}
        }

    if ret != 0:
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": stderr or stdout or f"Process exited with non-zero code {ret}",
            "evidence": {}
        }

    try:
        data = json.loads(stdout)
    except Exception as e:
        return {
            "case_id": cid,
            "status": "ERROR",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Failed to parse JSON stdout: {e}\nRaw stdout: {stdout[:500]}",
            "evidence": {}
        }

    if not isinstance(data, dict):
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": "Payload root must be a JSON object",
            "evidence": {}
        }

    if data.get("execution_status") != "PASS":
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Native execution_status is {data.get('execution_status')}, expected PASS",
            "evidence": {}
        }

    probes = data.get("probes")
    if not isinstance(probes, list) or len(probes) != 1:
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Expected exactly 1 probe in payload list, got {len(probes) if isinstance(probes, list) else type(probes).__name__}",
            "evidence": {}
        }

    probe_res = probes[0]
    if probe_res.get("case_id") != cid:
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Payload case_id mismatch: expected {cid}, got {probe_res.get('case_id')}",
            "evidence": {}
        }
    if probe_res.get("probe_group") != spec["probe_group"]:
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Payload probe_group mismatch: expected {spec['probe_group']}, got {probe_res.get('probe_group')}",
            "evidence": {}
        }

    pass_val = probe_res.get("pass")
    if type(pass_val) is not bool or pass_val is not True:
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Field 'pass' must be boolean True, got {type(pass_val).__name__} ({pass_val})",
            "evidence": {}
        }

    int_exit = probe_res.get("exit_code")
    if type(int_exit) is not int or int_exit != 0:
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": f"Internal probe exit_code must be integer 0, got {type(int_exit).__name__} ({int_exit})",
            "evidence": {}
        }

    ev = probe_res.get("evidence")
    if not isinstance(ev, dict):
        return {
            "case_id": cid,
            "status": "FAIL",
            "exit_code": ret,
            "duration_ms": duration_ms,
            "error": "Probe evidence must be a JSON object",
            "evidence": {}
        }

    # Verify each expected evidence value strictly
    for field, exp_val in spec["evidence_checks"].items():
        if field not in ev:
            return {
                "case_id": cid,
                "status": "FAIL",
                "exit_code": ret,
                "duration_ms": duration_ms,
                "error": f"Missing required evidence field: '{field}'",
                "evidence": ev
            }
        actual_val = ev[field]
        if actual_val != exp_val:
            return {
                "case_id": cid,
                "status": "FAIL",
                "exit_code": ret,
                "duration_ms": duration_ms,
                "error": f"Evidence value mismatch for '{field}': expected '{exp_val}', got '{actual_val}'",
                "evidence": ev
            }

    # Independent Oracle cross-validation
    if cid == "CASE_PROBE_D_EXACT_NLF_COUNTEREXAMPLE":
        # Cross-validate M0 Oracle on Diamond fixture
        with open(fixture_path, "r", encoding="utf-8") as f:
            f_data = json.load(f)
        q_dict = {
            "vertices": f_data["query"]["vertices"],
            "edges": [(e["src"], e["dst"], e["label"]) for e in f_data["query"]["edges"]]
        }
        d_dict = {
            "vertices": f_data["data_graph"]["vertices"],
            "edges": [(e["src"], e["dst"], e["label"]) for e in f_data["data_graph"]["edges"]]
        }
        q_g = DStarGraph.from_dict(q_dict)
        d_g = DStarGraph.from_dict(d_dict)
        k = f_data["query"]["k"]
        matches = list(enumerate_snapshot(q_g, d_g, k=k))
        if (0, 1, 2, 3) not in matches:
            return {
                "case_id": cid,
                "status": "FAIL",
                "exit_code": ret,
                "duration_ms": duration_ms,
                "error": f"Independent M0 Oracle did not accept identity match (0,1,2,3). Matches: {matches}",
                "evidence": ev
            }

    if cid == "CASE_PROBE_F_COUNT_TUPLE_BOUNDARY":
        # Cross-validate M0 Oracle on K4 fixture
        with open(fixture_path, "r", encoding="utf-8") as f:
            f_data = json.load(f)
        q_dict = {
            "vertices": f_data["query"]["vertices"],
            "edges": [(e["src"], e["dst"], e["label"]) for e in f_data["query"]["edges"]]
        }
        d_dict = {
            "vertices": f_data["data_graph"]["vertices"],
            "edges": [(e["src"], e["dst"], e["label"]) for e in f_data["data_graph"]["edges"]]
        }
        q_g = DStarGraph.from_dict(q_dict)
        d_g = DStarGraph.from_dict(d_dict)
        k = f_data["query"]["k"]
        matches = list(enumerate_snapshot(q_g, d_g, k=k))
        if len(matches) != 24:
            return {
                "case_id": cid,
                "status": "FAIL",
                "exit_code": ret,
                "duration_ms": duration_ms,
                "error": f"Independent M0 Oracle found {len(matches)} matches on K4, expected 24",
                "evidence": ev
            }

    return {
        "case_id": cid,
        "status": "PASS_EXPECTED",
        "exit_code": 0,
        "duration_ms": duration_ms,
        "summary": probe_res.get("summary", ""),
        "evidence": ev
    }


def main():
    parser = argparse.ArgumentParser(description="Run RapidFlow-APX Native Audit Harness")
    parser.add_argument("--manifest", default="configs/rapidflow_apx_audit_cases.json", help="Path to manifest JSON")
    parser.add_argument("--output", default="reports/rapidflow_apx_audit/summary.json", help="Path to summary output JSON")
    parser.add_argument("--binary", default=None, help="Path to pre-built native binary")
    parser.add_argument("--clean-build", action="store_true", help="Clean build directory before compiling")
    args = parser.parse_args()

    root_dir = REPO_ROOT
    manifest_path = (root_dir / args.manifest) if not Path(args.manifest).is_absolute() else Path(args.manifest)
    output_path = (root_dir / args.output) if not Path(args.output).is_absolute() else Path(args.output)
    upstream_dir = root_dir / "baselines" / "rapidflow" / "source"

    run_id = f"RUN_{int(time.time())}"
    git_head = get_git_commit(root_dir)
    git_dirty = is_git_dirty(root_dir)
    upstream_head = get_git_commit(upstream_dir)
    upstream_dirty = is_git_dirty(upstream_dir)

    summary = {
        "run_id": run_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest_path": str(manifest_path.relative_to(root_dir)) if manifest_path.is_relative_to(root_dir) else str(manifest_path),
        "manifest_version": "1.0",
        "manifest_sha256": "PENDING",
        "execution_head": git_head,
        "dirty": git_dirty,
        "base_commit": "03be8b56a9479609adf87d213122eabefc9162fb",
        "upstream_sha": upstream_head,
        "upstream_dirty": upstream_dirty,
        "binary_path": None,
        "binary_sha256": None,
        "preflight_status": "PENDING",
        "failing_stage": None,
        "overall_status": "ERROR",
        "total_cases": len(MANDATORY_CASES),
        "attempted_cases": 0,
        "passed_cases": 0,
        "failed_cases": 0,
        "results": []
    }

    def write_summary_and_exit(exit_code: int):
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
        except Exception as e:
            sys.stderr.write(f"WARNING: Could not write summary to {output_path}: {e}\n")
        print(json.dumps(summary, indent=2))
        sys.exit(exit_code)

    # 1. Preflight check
    try:
        check_preflight_resources(root_dir)
        summary["preflight_status"] = "PASSED"
    except Exception as e:
        sys.stderr.write(f"FATAL PREFLIGHT: {e}\n")
        summary["preflight_status"] = f"FAILED: {e}"
        summary["failing_stage"] = "PREFLIGHT"
        write_summary_and_exit(1)

    # 2. Validate manifest
    if not manifest_path.exists():
        sys.stderr.write(f"FATAL: Manifest not found at {manifest_path}\n")
        summary["failing_stage"] = "MANIFEST_NOT_FOUND"
        write_summary_and_exit(1)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        manifest_sha = compute_sha256(manifest_path)
        summary["manifest_sha256"] = manifest_sha
        summary["manifest_version"] = manifest_data.get("manifest_version", "1.0")
        validated_cases = validate_manifest(manifest_data, root_dir)
    except Exception as e:
        sys.stderr.write(f"FATAL MANIFEST VALIDATION: {e}\n")
        summary["failing_stage"] = "MANIFEST_VALIDATION"
        summary["error"] = str(e)
        write_summary_and_exit(1)

    # 3. Build or locate binary
    binary_path = Path(args.binary) if args.binary else None
    if not binary_path or not binary_path.exists():
        try:
            binary_path = build_native_harness(root_dir, clean_build=args.clean_build)
        except Exception as e:
            sys.stderr.write(f"FATAL BUILD: {e}\n")
            summary["failing_stage"] = "BUILD"
            summary["error"] = str(e)
            write_summary_and_exit(1)

    binary_sha = compute_sha256(binary_path)
    summary["binary_path"] = str(binary_path.relative_to(root_dir)) if binary_path.is_relative_to(root_dir) else str(binary_path)
    summary["binary_sha256"] = binary_sha

    # 4. Execute validated cases
    results = []
    overall_pass = True

    for case in validated_cases:
        summary["attempted_cases"] += 1
        c_res = run_probe_case(
            binary_path=binary_path,
            case_manifest=case,
            root_dir=root_dir,
            binary_sha256=binary_sha,
            timeout=case["limits"]["timeout_seconds"],
            address_space_mib=case["limits"]["max_address_space_mib"]
        )
        results.append(c_res)
        summary["results"].append(c_res)

        if c_res["status"] == "PASS_EXPECTED":
            summary["passed_cases"] += 1
        else:
            summary["failed_cases"] += 1
            overall_pass = False
            if summary["failing_stage"] is None:
                summary["failing_stage"] = f"CASE_{case['case_id']}"

    summary["overall_status"] = "PASS" if overall_pass else "FAIL"
    write_summary_and_exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
