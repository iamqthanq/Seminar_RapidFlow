#!/usr/bin/env python3
"""
RapidFlow-APX Native Audit Runner (Gate P1 / Issue #28 Remediation Closure F01-F06)

Orchestrates fail-closed execution of the native C++ audit harness (rapidflow_apx_audit)
against strictly locked manifest test cases (configs/rapidflow_apx_audit_cases.json).

Enforces:
- Exact 6 mandatory cases with rigid ID-to-group-to-fixture mapping.
- Runtime probe limits strictly capped at <= 30s timeout and <= 512 MiB address space.
- Build limits capped at <= 120s timeout and <= 1024 MiB address space.
- Validation of ALL declared required evidence fields (rejecting null or empty strings).
- Subprocess process group containment and signal termination.
- Preflight resource checks (RAM >= 2 GiB, free disk >= 1 GiB).
- Independent M0 Oracle cross-validation on Probe D and Probe F fixtures.
- Upstream pristine state tracking and comprehensive provenance logging.
- Atomic artifact writing and partial failure preservation.
"""

import argparse
import hashlib
import json
import os
import platform
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

MAX_RUNTIME_TIMEOUT_SECONDS = 30
MAX_RUNTIME_ADDRESS_SPACE_MIB = 512
MAX_BUILD_TIMEOUT_SECONDS = 120
MAX_BUILD_ADDRESS_SPACE_MIB = 1024

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

SOURCE_FILES_TO_HASH = [
    "tests/native/rapidflow_apx_audit/src/audit_probes.cpp",
    "tests/native/rapidflow_apx_audit/src/audit_probes.h",
    "tests/native/rapidflow_apx_audit/src/simple_json.h",
    "tests/native/rapidflow_apx_audit/src/main.cpp",
    "tests/native/rapidflow_apx_audit/CMakeLists.txt",
    "baselines/rapidflow/source/graph/graph.cpp",
    "baselines/rapidflow/source/utility/graphoperations.cpp",
    "baselines/rapidflow/source/streaming/global_view_manager.cpp",
    "scripts/run_rapidflow_apx_audit.py",
    "scripts/dstar_oracle.py"
]


def compute_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 of a file."""
    if not file_path.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_info(repo_dir: Path) -> tuple:
    """Retrieve (commit_sha, dirty_state) where dirty_state is 'CLEAN', 'DIRTY', or 'UNKNOWN_ERROR'."""
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
    except Exception:
        commit = "UNKNOWN_COMMIT"

    try:
        res = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if res.returncode != 0:
            dirty_state = "UNKNOWN_ERROR"
        else:
            dirty_state = "DIRTY" if len(res.stdout.strip()) > 0 else "CLEAN"
    except Exception:
        dirty_state = "UNKNOWN_ERROR"

    return commit, dirty_state


def get_tool_versions(root_dir: Path) -> dict:
    """Extract compiler and CMake version information."""
    cmake_bin = root_dir / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"
    if not cmake_bin.exists():
        cmake_bin = Path(shutil.which("cmake") or "cmake")
    try:
        cmake_v = subprocess.check_output([str(cmake_bin), "--version"], text=True).splitlines()[0]
    except Exception as e:
        cmake_v = f"UNKNOWN ({e})"

    try:
        gcc_v = subprocess.check_output(["g++", "--version"], text=True).splitlines()[0]
    except Exception as e:
        gcc_v = f"UNKNOWN ({e})"

    return {
        "cmake_version": cmake_v,
        "compiler_version": gcc_v,
        "compiler_flags": "-O2 -Wall -Wextra -pthread -std=c++17"
    }


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


def atomic_write_summary(summary_data: dict, out_path: Path) -> None:
    """Atomically write summary data to disk via temporary file rename."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = out_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)
        tmp_file.replace(out_path)
    except Exception as e:
        sys.stderr.write(f"WARNING: Could not atomically write summary to {out_path}: {e}\n")
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass


def validate_manifest(manifest: dict, root_dir: Path) -> list:
    """Validate locked manifest schema, cases, fixture hashes, and resource limits strictly against contract."""
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest must be a JSON object, got {type(manifest).__name__}")

    # Validate top-level resource limits
    r_limits = manifest.get("resource_limits")
    if isinstance(r_limits, dict):
        mc_time = r_limits.get("max_compile_time_seconds")
        mc_as = r_limits.get("max_compile_address_space_mib")
        mr_time = r_limits.get("max_runtime_seconds")
        mr_as = r_limits.get("max_runtime_address_space_mib")

        if type(mc_time) is not int or mc_time <= 0 or mc_time > MAX_BUILD_TIMEOUT_SECONDS:
            raise ValueError(f"Invalid max_compile_time_seconds in manifest: {mc_time} (must be int in 1..{MAX_BUILD_TIMEOUT_SECONDS})")
        if type(mc_as) is not int or mc_as <= 0 or mc_as > MAX_BUILD_ADDRESS_SPACE_MIB:
            raise ValueError(f"Invalid max_compile_address_space_mib in manifest: {mc_as} (must be int in 1..{MAX_BUILD_ADDRESS_SPACE_MIB})")
        if type(mr_time) is not int or mr_time <= 0 or mr_time > MAX_RUNTIME_TIMEOUT_SECONDS:
            raise ValueError(f"Invalid max_runtime_seconds in manifest: {mr_time} (must be int in 1..{MAX_RUNTIME_TIMEOUT_SECONDS})")
        if type(mr_as) is not int or mr_as <= 0 or mr_as > MAX_RUNTIME_ADDRESS_SPACE_MIB:
            raise ValueError(f"Invalid max_runtime_address_space_mib in manifest: {mr_as} (must be int in 1..{MAX_RUNTIME_ADDRESS_SPACE_MIB})")

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

        # Validate case limits strictly against runtime contract (<= 30s, <= 512 MiB)
        limits = case.get("limits")
        if not isinstance(limits, dict):
            raise ValueError(f"Limits must be a dict for case {cid}")
        timeout = limits.get("timeout_seconds")
        as_mib = limits.get("max_address_space_mib")

        if type(timeout) is not int or timeout <= 0 or timeout > MAX_RUNTIME_TIMEOUT_SECONDS:
            raise ValueError(f"Invalid timeout_seconds for {cid}: {timeout} (must be int in 1..{MAX_RUNTIME_TIMEOUT_SECONDS})")
        if type(as_mib) is not int or as_mib <= 0 or as_mib > MAX_RUNTIME_ADDRESS_SPACE_MIB:
            raise ValueError(f"Invalid max_address_space_mib for {cid}: {as_mib} (must be int in 1..{MAX_RUNTIME_ADDRESS_SPACE_MIB})")

        # Validate required evidence field keys
        req_fields = case.get("required_evidence_fields")
        if not isinstance(req_fields, list) or not req_fields:
            raise ValueError(f"required_evidence_fields must be a non-empty list for {cid}")
        for f in req_fields:
            if not isinstance(f, str) or not f.strip():
                raise ValueError(f"Invalid field name in required_evidence_fields for {cid}: {f!r}")
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


def build_native_harness(root_dir: Path, clean_build: bool = False, timeout: int = 120, address_space_mib: int = 1024) -> tuple:
    """Build the native C++ harness using portable CMake under containment; returns (binary_path, build_log_dict)."""
    cmake_bin = root_dir / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"
    if not cmake_bin.exists():
        cmake_bin = Path(shutil.which("cmake") or "cmake")

    build_dir = root_dir / "tests" / "native" / "rapidflow_apx_audit" / "build"
    if clean_build and build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter_ns()

    # Configure
    configure_cmd = [str(cmake_bin), ".."]
    ret_c, out_c, err_c, timed_out_c = execute_contained_subprocess(
        configure_cmd, cwd=build_dir, timeout=timeout, address_space_mib=address_space_mib
    )
    if timed_out_c or ret_c != 0:
        raise RuntimeError(f"CMake configuration failed (ret={ret_c}, timed_out={timed_out_c}):\n{err_c or out_c}")

    # Build
    build_cmd = ["make", "-j2"]
    ret_b, out_b, err_b, timed_out_b = execute_contained_subprocess(
        build_cmd, cwd=build_dir, timeout=timeout, address_space_mib=address_space_mib
    )
    if timed_out_b or ret_b != 0:
        raise RuntimeError(f"CMake compilation failed (ret={ret_b}, timed_out={timed_out_b}):\n{err_b or out_b}")

    t1 = time.perf_counter_ns()
    duration_ms = (t1 - t0) / 1_000_000.0

    binary_path = build_dir / "rapidflow_apx_audit"
    if not binary_path.exists():
        raise FileNotFoundError(f"Expected binary not found after build: {binary_path}")

    build_info = {
        "clean_build": clean_build,
        "configure_command": configure_cmd,
        "configure_exit_code": ret_c,
        "build_command": build_cmd,
        "build_exit_code": ret_b,
        "duration_ms": duration_ms,
        "configure_stdout": out_c.strip(),
        "configure_stderr": err_c.strip(),
        "build_stdout": out_b.strip()[-1000:],  # keep last 1000 chars of build log
        "build_stderr": err_b.strip()
    }

    return binary_path, build_info


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
                "cmd_executed": [],
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

    base_res = {
        "case_id": cid,
        "cmd_executed": cmd,
        "timeout_seconds": timeout,
        "address_space_mib": address_space_mib,
        "duration_ms": duration_ms,
        "raw_stdout": stdout.strip(),
        "raw_stderr": stderr.strip()
    }

    if timed_out:
        return {
            **base_res,
            "status": "TIMEOUT",
            "exit_code": -1,
            "error": f"Process exceeded wall timeout of {timeout}s",
            "evidence": {}
        }

    if ret != 0:
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": stderr or stdout or f"Process exited with non-zero code {ret}",
            "evidence": {}
        }

    try:
        data = json.loads(stdout)
    except Exception as e:
        return {
            **base_res,
            "status": "ERROR",
            "exit_code": ret,
            "error": f"Failed to parse JSON stdout: {e}\nRaw stdout: {stdout[:500]}",
            "evidence": {}
        }

    if not isinstance(data, dict):
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": "Payload root must be a JSON object",
            "evidence": {}
        }

    if data.get("execution_status") != "PASS":
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": f"Native execution_status is {data.get('execution_status')}, expected PASS",
            "evidence": {}
        }

    probes = data.get("probes")
    if not isinstance(probes, list) or len(probes) != 1:
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": f"Expected exactly 1 probe in payload list, got {len(probes) if isinstance(probes, list) else type(probes).__name__}",
            "evidence": {}
        }

    probe_res = probes[0]
    if probe_res.get("case_id") != cid:
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": f"Payload case_id mismatch: expected {cid}, got {probe_res.get('case_id')}",
            "evidence": {}
        }
    if probe_res.get("probe_group") != spec["probe_group"]:
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": f"Payload probe_group mismatch: expected {spec['probe_group']}, got {probe_res.get('probe_group')}",
            "evidence": {}
        }

    pass_val = probe_res.get("pass")
    if type(pass_val) is not bool or pass_val is not True:
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": f"Field 'pass' must be boolean True, got {type(pass_val).__name__} ({pass_val})",
            "evidence": {}
        }

    int_exit = probe_res.get("exit_code")
    if type(int_exit) is not int or int_exit != 0:
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": f"Internal probe exit_code must be integer 0, got {type(int_exit).__name__} ({int_exit})",
            "evidence": {}
        }

    ev = probe_res.get("evidence")
    if not isinstance(ev, dict):
        return {
            **base_res,
            "status": "FAIL",
            "exit_code": ret,
            "error": "Probe evidence must be a JSON object",
            "evidence": {}
        }

    # Verify ALL fields declared in case_manifest["required_evidence_fields"] exist and are non-empty
    for field in case_manifest.get("required_evidence_fields", []):
        if field not in ev:
            return {
                **base_res,
                "status": "FAIL",
                "exit_code": ret,
                "error": f"Missing required evidence field declared in manifest: '{field}'",
                "evidence": ev
            }
        val = ev[field]
        if val is None or (isinstance(val, str) and not val.strip()):
            return {
                **base_res,
                "status": "FAIL",
                "exit_code": ret,
                "error": f"Required evidence field '{field}' must not be null or empty string",
                "evidence": ev
            }

    # Verify each expected evidence value strictly against spec
    for field, exp_val in spec["evidence_checks"].items():
        actual_val = ev[field]
        if actual_val != exp_val:
            return {
                **base_res,
                "status": "FAIL",
                "exit_code": ret,
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
                **base_res,
                "status": "FAIL",
                "exit_code": ret,
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
                **base_res,
                "status": "FAIL",
                "exit_code": ret,
                "error": f"Independent M0 Oracle found {len(matches)} matches on K4, expected 24",
                "evidence": ev
            }

    return {
        **base_res,
        "status": "PASS_EXPECTED",
        "exit_code": 0,
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
    git_head, git_dirty = get_git_info(root_dir)
    upstream_head, upstream_dirty = get_git_info(upstream_dir)
    tool_versions = get_tool_versions(root_dir)

    summary = {
        "run_id": run_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest_path": str(manifest_path.relative_to(root_dir)) if manifest_path.is_relative_to(root_dir) else str(manifest_path),
        "manifest_version": "1.0",
        "manifest_sha256": "PENDING",
        "execution_head": git_head,
        "dirty_state": git_dirty,
        "base_commit": "03be8b56a9479609adf87d213122eabefc9162fb",
        "upstream_sha": upstream_head,
        "upstream_dirty_state": upstream_dirty,
        "binary_origin": "PRE_BUILT_PROVIDED" if args.binary else "BUILT_BY_RUNNER",
        "binary_path": None,
        "binary_sha256": None,
        "host_environment": {
            "platform": platform.platform(),
            "os": platform.system(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "python_version": sys.version.split()[0],
            "cpu_count": os.cpu_count()
        },
        "tool_versions": tool_versions,
        "source_hashes": {
            f: compute_sha256(root_dir / f) for f in SOURCE_FILES_TO_HASH
        },
        "fixture_hashes": {},
        "build_provenance": None,
        "preflight_status": "PENDING",
        "failing_stage": None,
        "overall_status": "ERROR",
        "total_cases": len(MANDATORY_CASES),
        "attempted_cases": 0,
        "completed_cases": 0,
        "passed_cases": 0,
        "failed_cases": 0,
        "unattempted_cases": len(MANDATORY_CASES),
        "results": []
    }

    def write_summary_and_exit(exit_code: int):
        summary["unattempted_cases"] = summary["total_cases"] - summary["completed_cases"]
        if exit_code != 0:
            summary["overall_status"] = "FAIL"
        atomic_write_summary(summary, output_path)
        print(json.dumps(summary, indent=2))
        sys.exit(exit_code)

    # 0. Check Git provenance fail-closed
    provenance_errors = []
    if git_head == "UNKNOWN_COMMIT":
        provenance_errors.append("repository Git commit SHA could not be determined")
    if git_dirty == "UNKNOWN_ERROR":
        provenance_errors.append("repository Git status could not be determined")
    if upstream_head == "UNKNOWN_COMMIT":
        provenance_errors.append("upstream RapidFlow Git commit SHA could not be determined")
    if upstream_dirty == "UNKNOWN_ERROR":
        provenance_errors.append("upstream RapidFlow Git status could not be determined")

    if provenance_errors:
        err_msg = "; ".join(provenance_errors)
        sys.stderr.write(f"FATAL PROVENANCE: {err_msg}\n")
        summary["failing_stage"] = "PROVENANCE"
        summary["error"] = f"Provenance check failed: {err_msg}"
        write_summary_and_exit(1)

    # 1. Preflight check
    try:
        check_preflight_resources(root_dir)
        summary["preflight_status"] = "PASSED"
    except Exception as e:
        sys.stderr.write(f"FATAL PREFLIGHT: {e}\n")
        summary["preflight_status"] = f"FAILED: {e}"
        summary["failing_stage"] = "PREFLIGHT"
        summary["error"] = str(e)
        write_summary_and_exit(1)

    # 2. Validate manifest
    if not manifest_path.exists():
        sys.stderr.write(f"FATAL: Manifest not found at {manifest_path}\n")
        summary["failing_stage"] = "MANIFEST_NOT_FOUND"
        summary["error"] = f"Manifest file does not exist at {manifest_path}"
        write_summary_and_exit(1)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    except Exception as e:
        sys.stderr.write(f"FATAL MANIFEST PARSE: {e}\n")
        summary["failing_stage"] = "MANIFEST_PARSE_ERROR"
        summary["error"] = f"Malformed JSON in manifest: {e}"
        write_summary_and_exit(1)

    try:
        manifest_sha = compute_sha256(manifest_path)
        summary["manifest_sha256"] = manifest_sha
        summary["manifest_version"] = manifest_data.get("manifest_version", "1.0")
        validated_cases = validate_manifest(manifest_data, root_dir)
        # Record fixture hashes
        for case in validated_cases:
            fpath = case.get("fixture_path")
            if fpath:
                summary["fixture_hashes"][fpath] = compute_sha256(root_dir / fpath)
    except Exception as e:
        sys.stderr.write(f"FATAL MANIFEST VALIDATION: {e}\n")
        summary["failing_stage"] = "MANIFEST_VALIDATION"
        summary["error"] = str(e)
        write_summary_and_exit(1)

    # 3. Build or locate binary
    binary_path = Path(args.binary) if args.binary else None
    if not binary_path or not binary_path.exists():
        try:
            binary_path, b_info = build_native_harness(root_dir, clean_build=args.clean_build)
            summary["build_provenance"] = b_info
        except Exception as e:
            sys.stderr.write(f"FATAL BUILD: {e}\n")
            summary["failing_stage"] = "BUILD"
            summary["error"] = str(e)
            write_summary_and_exit(1)
    else:
        summary["build_provenance"] = {"binary_origin": "PRE_BUILT_PROVIDED", "path": str(binary_path)}

    binary_sha = compute_sha256(binary_path)
    summary["binary_path"] = str(binary_path.relative_to(root_dir)) if binary_path.is_relative_to(root_dir) else str(binary_path)
    summary["binary_sha256"] = binary_sha

    # 4. Execute validated cases
    overall_pass = True

    for case in validated_cases:
        cid = case["case_id"]
        summary["attempted_cases"] += 1

        try:
            c_res = run_probe_case(
                binary_path=binary_path,
                case_manifest=case,
                root_dir=root_dir,
                binary_sha256=binary_sha,
                timeout=case["limits"]["timeout_seconds"],
                address_space_mib=case["limits"]["max_address_space_mib"]
            )
        except Exception as e:
            c_res = {
                "case_id": cid,
                "status": "ERROR",
                "exit_code": -1,
                "duration_ms": 0.0,
                "error": f"Unexpected exception executing case {cid}: {type(e).__name__}: {e}",
                "evidence": {}
            }

        summary["results"].append(c_res)
        summary["completed_cases"] += 1

        if c_res["status"] == "PASS_EXPECTED":
            summary["passed_cases"] += 1
        else:
            summary["failed_cases"] += 1
            overall_pass = False
            if summary["failing_stage"] is None:
                summary["failing_stage"] = cid if cid.startswith("CASE_") else f"CASE_{cid}"

        # Write atomic checkpoint after each case completes
        atomic_write_summary(summary, output_path)

        # If unexpected unhandled exception in case runner, halt loop to protect state
        if c_res["status"] == "ERROR" and "Unexpected exception" in c_res.get("error", ""):
            break

    summary["overall_status"] = "PASS" if overall_pass else "FAIL"
    write_summary_and_exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
