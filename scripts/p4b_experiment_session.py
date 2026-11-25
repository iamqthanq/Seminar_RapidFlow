"""Production-shared P4b experiment scheduling, persistence and independent ingestion."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import tempfile
import time

from scripts import p4b_activation as auth
from scripts import p4b_correctness_registry as refs
from scripts import p4b_native_evidence as evidence
from scripts import run_rapidflow_apx_p4b as p4b

SCHEMA = "p4b-execution-journal-v2"
PROCESS_METRICS = ("outer_wall_ns", "user_cpu_ns", "system_cpu_ns", "wait4_ru_maxrss_bytes")
FILES = frozenset({"start.json", "native-output.json", "stdout.log", "stderr.log", "process.json",
                   "invocation.json", "validation.json"})
HALT_STATES = {"FAILED", "INVALID", "INELIGIBLE", "INTERRUPTED"}
BOUNDED_CASE = "P4B_CONT_01"
BOUNDED_VARIANT = "BARE"


def require(condition, error):
    if not condition:
        raise p4b.LedgerIntegrityError(error)


def digest(value):
    return p4b.sha256_bytes(p4b.canonical_json_bytes(value))


def read(path):
    return p4b.load_json_bytes(path.read_bytes())


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path, raw):
    """Atomically publish new bytes without replacing any previously created name."""
    fd, temp = tempfile.mkstemp(prefix=".publishing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        os.unlink(temp)
        sync_dir(path.parent)


def write(path, value):
    publish(path, p4b.canonical_json_bytes(value))


def file_info(path):
    require(path.is_file() and not path.is_symlink(), f"MISSING_OR_UNSAFE_ARTIFACT: {path.name}")
    return {"sha256": p4b.compute_sha256(path), "size": path.stat().st_size}


def require_uint(value, name, *, positive=False):
    require(type(value) is int and int(positive) <= value <= (1 << 63) - 1,
            f"INVALID_NUMERIC_EVIDENCE: {name}")
    return value


class SyntheticBackend:
    origin = p4b.SYNTHETIC_KIND
    binary_sha = p4b.SYNTHETIC_BINARY
    source = p4b.SYNTHETIC_SOURCE

    def __init__(self, simulate):
        self.simulate = simulate

    def authorize(self, slot):
        return None

    def execute(self, slot, entry, path):
        return self.simulate(dict(slot))


class NativeBackend:
    origin = "REAL_FUNCTIONAL_SMOKE"
    binary_sha = p4b.NATIVE_SHA256
    source = p4b.NATIVE_SOURCE_COMMIT

    def __init__(self, binary, registry, store, policy, context=None, *, binary_sha=None, source=None):
        self.binary, self.registry, self.store, self.policy = binary, registry, store, policy
        self.context = context
        if binary_sha is not None:
            self.binary_sha = binary_sha
        if source is not None:
            self.source = source

    def authorize(self, slot):
        require(slot["workload_id"] == BOUNDED_CASE and slot["variant_id"] == BOUNDED_VARIANT
                and slot["execution_purpose"] in ("correctness", "performance", "diagnostic"),
                "WITNESS_NOT_IN_BOUNDED_INVENTORY")

    def execute(self, slot, entry, path):
        self.authorize(slot)
        require(entry["workload_id"] == slot["workload_id"], "INVOCATION_WORKLOAD_MISMATCH")
        if self.origin == NativeBackend.origin:
            require(entry["fixture_sha256"] == p4b.BOUNDED_FIXTURE_SHA256, "WITNESS_FIXTURE_MISMATCH")
        out = p4b.execute_p4b_native_slot(
            self.binary, p4b.REPO_ROOT / entry["fixture_path"], slot["variant_id"],
            slot["execution_purpose"], path / "native-output.json",
            timeout_s=self.policy["wall_timeout_seconds"], as_limit_mib=self.policy["address_space_limit_mib"],
            max_work=self.policy["logical_work_cap"], max_nlf_memory_mib=self.policy["max_nlf_memory_mib"],
            expected_binary_sha256=self.binary_sha, expected_fixture_sha256=entry["fixture_sha256"],
            evidence={
                "environment": auth.NATIVE_ENV,
                "on_start": lambda pid, argv: write(path / "invocation.json", {
                    "pid": pid, "argv": argv, "environment": auth.NATIVE_ENV}),
                "save_streams": lambda out, err: (
                    publish(path / "stdout.log", out), publish(path / "stderr.log", err)),
            },
            production_attempt=self if self.origin == ProductionBackend.origin else None,
        )
        return {k: v for k, v in out.items() if k not in ("output_data", "raw_stdout", "raw_stderr")}


class ProductionBackend(NativeBackend):
    origin = "CONFIRMATORY_MEASUREMENT"

    def execute(self, slot, entry, path):
        self.authorize(slot)
        require(getattr(self, "_active_attempt", None) == (slot, entry, path),
                "PRODUCTION_LAUNCH_OUTSIDE_ACTIVE_SESSION")
        held = read(path / "start.json")
        require(held["identity"]["slot_id"] == slot["slot_id"]
                and held["identity"]["activation_sha256"] == self.context.digest,
                "PRODUCTION_START_IDENTITY_MISMATCH")
        return super().execute(slot, entry, path)

    def verify_launch(self, binary, fixture, fixture_sha, variant, purpose, output, limits):
        active = getattr(self, "_active_attempt", None)
        if active is None:
            raise p4b.AuthorizationBlockedError("PRODUCTION_LAUNCH_OUTSIDE_ACTIVE_SESSION")
        slot, entry, path = active
        self.authorize(slot)
        expected_limits = tuple(self.policy[key] for key in (
            "wall_timeout_seconds", "address_space_limit_mib", "logical_work_cap", "max_nlf_memory_mib"))
        expected = (self.binary, p4b.REPO_ROOT / entry["fixture_path"], entry["fixture_sha256"],
                    slot["variant_id"], slot["execution_purpose"], path / "native-output.json", expected_limits)
        if (binary, fixture, fixture_sha, variant, purpose, output, limits) != expected:
            raise p4b.AuthorizationBlockedError("PRODUCTION_INVOCATION_DOES_NOT_MATCH_ACTIVE_ATTEMPT")

    def authorize(self, slot):
        if type(self.context) is not auth.Activation:
            raise p4b.AuthorizationBlockedError("VERIFIED_PRODUCTION_CONTEXT_REQUIRED")
        if self.context.binary != self.binary:
            raise p4b.AuthorizationBlockedError("PRODUCTION_BINARY_PATH_MISMATCH")
        self.context.recheck("diagnostic" if slot["slot_purpose"] == "diagnostic" else "primary")
        require(slot["execution_purpose"] == ("diagnostic" if slot["slot_purpose"] == "diagnostic" else "performance"),
                "PRODUCTION_SLOT_PURPOSE_MISMATCH")


def _lifecycle(native, planned, success):
    require(isinstance(native, dict), "NATIVE_RESULT_SCHEMA")
    life = native.get("execution")
    require(isinstance(life, dict), "NATIVE_RESULT_SCHEMA")
    steps = native.get("steps")
    require(isinstance(steps, list) and all(isinstance(s, dict) for s in steps), "NATIVE_RESULT_SCHEMA")
    keys = ("total_steps", "attempted_steps", "committed_steps", "completed_steps",
            "delivered_steps", "failed_steps", "unattempted_steps")
    for key in keys:
        require(key in life, f"INVALID_NUMERIC_EVIDENCE: {key}")
        require_uint(life[key], key)
    require(life["total_steps"] == len(steps) == planned, "STEP_INVENTORY_MISMATCH")
    for step in steps:
        require(
            isinstance(step.get("status"), str)
            and isinstance(step.get("commit_status"), str)
            and isinstance(step.get("entered_tuples"), list)
            and isinstance(step.get("exited_tuples"), list),
            "NATIVE_RESULT_SCHEMA",
        )
    outcomes = [s["status"] for s in steps]
    if success:
        require(outcomes == ["SUCCESS"] * planned and all(life[k] == planned for k in keys[:5])
                and life["failed_steps"] == life["unattempted_steps"] == 0, "SUCCESS_LIFECYCLE_MISMATCH")
    else:
        committed = life["committed_steps"]
        remaining = planned - committed - 1
        require(remaining >= 0 and outcomes[:committed] == ["SUCCESS"] * committed
                and outcomes[committed] not in ("SUCCESS", "UNATTEMPTED")
                and outcomes[committed + 1:] == ["UNATTEMPTED"] * remaining
                and life["attempted_steps"] == life["delivered_steps"] == committed + 1
                and life["completed_steps"] == committed and life["failed_steps"] == 1
                and life["unattempted_steps"] == remaining, "CENSORED_LIFECYCLE_MISMATCH")
        for step in steps[committed:]:
            require(step["entered_tuples"] == step["exited_tuples"] == [], "EVENTS_FROM_UNCOMMITTED_STEP")
    return life


def _diagnostics(native):
    require(isinstance(native, dict), "TIMING_PROFILE_SCHEMA")
    profile = native.get("profiling")
    require(isinstance(profile, dict), "TIMING_PROFILE_SCHEMA")
    values = profile.get("cumulative")
    require(isinstance(values, dict), "TIMING_CUMULATIVE_SCHEMA")
    counters = native.get("counters")
    require(isinstance(counters, dict), "NATIVE_RESULT_SCHEMA")
    execution = native.get("execution")
    require(isinstance(execution, dict) and "completed_steps" in execution, "NATIVE_RESULT_SCHEMA")
    timings = {name: require_uint(values.get(name), name) for name in evidence.PROFILING_METRICS}
    return {"timing_ns": timings, "scope": "native diagnostic nested stages",
            "counters": {k: require_uint(v, k) for k, v in counters.items()},
            "denominator": require_uint(execution["completed_steps"], "completed_steps")}


def _invalid(identity, backend, reason, detail):
    return {**identity, "attempt_outcome": "INVALID", "censoring_reason": reason,
            "error_detail": detail, "correctness_status": None, "measurement_eligible": False,
            "synthetic_test_fixture": backend.origin == p4b.SYNTHETIC_KIND}


def _provenance_error(process, backend):
    require(isinstance(process, dict), "INVALID_PROCESS_EVIDENCE_SCHEMA")
    supervisor = process.get("supervisor")
    require(isinstance(supervisor, dict), "INVALID_SUPERVISOR_SCHEMA")
    if process.get("censoring_reason") == "PROVENANCE_ERROR":
        return "Supervisor reported post-execution provenance drift"
    post = supervisor.get("post_execution_identity")
    if post is None and "post_execution_identity" not in supervisor and backend.origin == NativeBackend.origin:
        # Historical bounded R0 evidence retains the supervisor classification
        # but predates explicit post-launch digests. It cannot become a real slot.
        return None if "censoring_reason" in process else "Missing supervisor provenance disposition"
    expected = {key: process.get(key) for key in ("binary_sha256", "fixture_sha256")}
    if not isinstance(post, dict) or post != expected:
        return "Missing or contradictory post-execution binary/fixture identity"
    return None


def _native_schema(native, slot, entry, workload, mode, exit_code, *, unavailable=False, resource=False):
    if slot["execution_purpose"] == "correctness":
        from scripts.run_rapidflow_apx_engines import validate_native_output_schema, validate_nlf_configuration
        ok, reason = validate_native_output_schema(
            native, mode["engine"], entry["case_id"], workload=workload, is_success=exit_code == 0,
            expected_raw_input_sha256=entry["fixture_sha256"], resource_preexec=resource)
        require(ok, f"NATIVE_OUTPUT_SCHEMA: {reason}")
        ok, reason, _ = validate_nlf_configuration(
            mode["engine"], mode["nlf_mode"], native, exit_code == 0, mode["neighbor_mode"],
            native_exit_code=exit_code, accept_unavailable=unavailable)
    else:
        ok, reason, _ = p4b.validate_measurement_output_schema(
            native, mode["engine"], entry["case_id"], slot["execution_purpose"], mode["nlf_mode"],
            mode["neighbor_mode"], workload, entry["fixture_sha256"], exit_code == 0, native_exit_code=exit_code,
            accept_unavailable=unavailable, resource_preexec=resource)
    require(ok, f"NATIVE_OUTPUT_SCHEMA: {reason}")


def _native_identity(native, slot, entry, mode, reference, caps, *, partial=False):
    expected = {
        "case_id": entry["case_id"], "raw_input_sha256": entry["fixture_sha256"],
        "execution_purpose": slot["execution_purpose"], "engine": mode["engine"], "engine_name": mode["engine"],
        "schema_version": reference["schema_version"], "semantics_version": reference["semantics_version"],
        "query_info": reference["query_info"], "work_budget": reference["work_budget"],
        "requested_nlf_mode": mode["nlf_mode"], "requested_neighbor_mode": mode["neighbor_mode"],
        "effective_neighbor_mode": mode["neighbor_mode"], "nlf_memory_limit_bytes": caps["max_nlf_memory_mib"] * 1024**2,
    }
    for key, value in expected.items():
        if not partial or key in native:
            require(key in native and p4b.canonical_json_bytes(native[key]) == p4b.canonical_json_bytes(value),
                    f"NATIVE_RESULT_IDENTITY_MISMATCH: {key}")


def _reference_prefix(native, reference, *, fallback=False):
    committed = [s for s in native["steps"] if s["commit_status"] == "COMMITTED"]
    require(len(committed) <= reference["execution"]["committed_steps"], "INSUFFICIENT_REFERENCE_PREFIX")
    fields = ("entered_tuples", "exited_tuples", "step_index", "status", "commit_status")
    if not fallback:
        fields += ("step_counters",)
    for step in committed:
        wanted = reference["steps"][step["step_index"]]
        for key in fields:
            require(key in step and p4b.canonical_json_bytes(step[key]) == p4b.canonical_json_bytes(wanted[key]),
                    f"RESOURCE_REFERENCE_PREFIX_MISMATCH: step {step['step_index']} {key}")


def _fallback_reference(backend, slot):
    row = backend.registry[slot["workload_id"], "BARE"]
    return p4b.load_json_bytes(refs.read_reference(backend.store, row))


def _check_rss(process, wait4_kib, native=None):
    require(isinstance(process, dict), "INVALID_PROCESS_EVIDENCE_SCHEMA")
    macro = {}
    if native is not None:
        require(isinstance(native, dict), "NATIVE_RESULT_SCHEMA")
        if "macro_timing" in native:
            require(isinstance(native["macro_timing"], dict), "TIMING_CLOCK_OR_SCHEMA")
            macro = native["macro_timing"]
    fields = {"child_rss_method", "child_vm_hwm_kib", "child_vm_hwm_bytes"}
    if fields & macro.keys():
        kib = require_uint(macro.get("child_vm_hwm_kib"), "child_vm_hwm_kib", positive=True)
        require(macro.get("child_rss_method") == "linux_proc_self_status_vmhwm"
                and require_uint(macro.get("child_vm_hwm_bytes"), "child_vm_hwm_bytes") == kib * 1024,
                "NATIVE_RSS_MISMATCH")
        method = "linux_proc_self_status_vmhwm"
    else:
        kib = wait4_kib or None
        method = "linux_wait4_rusage_ru_maxrss"
    expected = {"resource_measurement_method": method, "peak_rss_original_unit": "KiB",
                "peak_rss_original_value": kib, "peak_rss_bytes": kib * 1024 if kib is not None else None}
    require(all(key in process and type(process[key]) is type(value) and process[key] == value
                for key, value in expected.items()), "SUPERVISOR_CHILD_RSS_MISMATCH")


def _check_interrupted_output(raw, slot, entry, workload, mode, reference, caps, elapsed, backend):
    native, extent = evidence.interrupted_output(raw)
    if extent == "ABSENT":
        return extent
    complete = extent == "COMPLETE"
    allowed = reference.keys() | {"profiling", "first_failing_step_index", "first_failure_category",
                                  "first_failure_message", "fallback_reason"}
    require(native.keys() <= allowed, "INTERRUPTED_OUTPUT_SCHEMA: unexpected native field")
    _native_identity(native, slot, entry, mode, reference, caps, partial=not complete)
    steps = native.get("steps", [])
    evidence.frozen_updates(steps, workload["updates"], complete=complete)
    if slot["execution_purpose"] == "performance":
        require("profiling" not in native, "INTERRUPTED_MEASUREMENT_MODE: unexpected profiling root")
        require(all(isinstance(s, dict) and "profiling" not in s for s in steps),
                "INTERRUPTED_MEASUREMENT_MODE: unexpected step profiling")
    macro = evidence.timings(native, elapsed, complete=complete)
    evidence.profile_timings(native, macro, elapsed, has_complete_steps=complete)
    for key, expected in (("measurement_schema_version", 1),
                          ("profiling_enabled", slot["execution_purpose"] == "diagnostic"),
                          ("deep_diagnostics_enabled", slot["execution_purpose"] == "correctness")):
        if key in native:
            require(type(native[key]) is type(expected) and native[key] == expected, f"INTERRUPTED_MEASUREMENT_MODE: {key}")
    unavailable = native.get("configuration_status") == "REQUESTED_CONFIGURATION_UNAVAILABLE"
    effective = "none" if unavailable else mode["nlf_mode"]
    if "effective_nlf_mode" in native:
        require(native["effective_nlf_mode"] == effective, "INTERRUPTED_CONFIGURATION_MISMATCH")
    expected_state = {"SUCCESS", "REQUESTED_CONFIGURATION_UNAVAILABLE"}
    if "configuration_status" in native:
        require(native["configuration_status"] in expected_state, "INTERRUPTED_CONFIGURATION_MISMATCH")
    if "status" in native:
        require(native["status"] in ("SUCCESS", "FAIL"), "INTERRUPTED_NATIVE_STATUS")
    error = native.get("error_category")
    if "error_category" in native:
        require(error in ("NONE", "WORK_BUDGET_EXCEEDED", "RESOURCE_LIMIT_ERROR"), "INTERRUPTED_FAILURE_IDENTITY")
        if "status" in native:
            require(native["status"] == ("SUCCESS" if error == "NONE" else "FAIL"), "INTERRUPTED_NATIVE_STATUS")
    failed = False
    for step in steps:
        require(isinstance(step, dict), "INTERRUPTED_STEP_LIFECYCLE")
        status = step.get("status")
        commit = step.get("commit_status")
        step_err_cat = step.get("error_category")
        if status == "SUCCESS":
            require(not failed and commit == "COMMITTED" and step_err_cat == "NONE",
                    "INTERRUPTED_STEP_LIFECYCLE")
        else:
            require(commit == "NOT_COMMITTED" and step.get("entered_tuples") == [] and step.get("exited_tuples") == []
                    and (status == "UNATTEMPTED" and failed and step_err_cat == "NONE"
                         or not failed and status in ("CALLBACK_ABORTED", "ERROR", "RESOURCE_EXHAUSTED")
                         and step_err_cat in ("WORK_BUDGET_EXCEEDED", "RESOURCE_LIMIT_ERROR")),
                    "INTERRUPTED_STEP_LIFECYCLE")
            if status != "UNATTEMPTED":
                step_error = step_err_cat
                if step_error == "WORK_BUDGET_EXCEEDED":
                    sc = step.get("step_counters")
                    require(isinstance(sc, dict) and status == "CALLBACK_ABORTED" and require_uint(
                        sc.get("work_units"), "interrupted work_units") == caps["logical_work_cap"] + 1,
                        "INTERRUPTED_WORK_BUDGET_MISMATCH")
                else:
                    if status == "ERROR":
                        require("step_counters" not in step and step.get("error") == "std::bad_alloc",
                                "INTERRUPTED_RESOURCE_STEP_MISMATCH")
                    else:
                        require(status == "RESOURCE_EXHAUSTED" and isinstance(step.get("step_counters"), dict)
                                and step.get("error") == "std::bad_alloc",
                                "INTERRUPTED_RESOURCE_STEP_MISMATCH")
                for key, expected in (("first_failing_step_index", step["step_index"]),
                                      ("first_failure_category", step_error), ("first_failure_message", step.get("error"))):
                    if key in native:
                        require(type(native[key]) is type(expected) and native[key] == expected,
                                f"INTERRUPTED_FAILURE_IDENTITY: {key}")
            failed = True
    if "execution" in native:
        life = native["execution"]
        require(isinstance(life, dict), "INTERRUPTED_LIFECYCLE_MISMATCH")
        for key in ("total_steps", "attempted_steps", "committed_steps", "completed_steps",
                    "delivered_steps", "failed_steps", "unattempted_steps"):
            require(key in life, f"INVALID_NUMERIC_EVIDENCE: {key}")
            require_uint(life[key], key)
        require(life["total_steps"] == entry["update_count"] == life["attempted_steps"] + life["unattempted_steps"]
                and life["attempted_steps"] == life["completed_steps"] + life["failed_steps"] == life["delivered_steps"]
                and life["completed_steps"] == life["committed_steps"]
                and sum(s["status"] not in ("SUCCESS", "UNATTEMPTED") for s in steps) <= life["failed_steps"] <= 1
                and sum(s["status"] == "SUCCESS" for s in steps) <= life["committed_steps"]
                and sum(s["status"] == "UNATTEMPTED" for s in steps) <= life["unattempted_steps"],
                "INTERRUPTED_LIFECYCLE_MISMATCH")
    if complete:
        native_exit = {"NONE": 0, "WORK_BUDGET_EXCEEDED": 4, "RESOURCE_LIMIT_ERROR": 5}[error]
        _native_schema(native, slot, entry, workload, mode, native_exit,
                       unavailable=unavailable, resource=native_exit == 5)
        _lifecycle(native, entry["update_count"], not failed)
    if unavailable:
        require(mode["nlf_mode"] in ("static", "residual")
                and ("fallback_reason" not in native or native["fallback_reason"] in (
                    "NLF_INDEX_MEMORY_LIMIT", "NLF_INDEX_ALLOCATION_FAILED")), "INTERRUPTED_CONFIGURATION_MISMATCH")
        reference = _fallback_reference(backend, slot)
    _reference_prefix({"steps": steps}, reference, fallback=unavailable)
    return extent


def _validate_native(slot, entry, identity, backend, path, process):
    provenance = _provenance_error(process, backend)
    if provenance:
        return _invalid(identity, backend, "PROVENANCE_ERROR", provenance)
    reference_row = backend.registry[slot["workload_id"], slot["variant_id"]]
    raw_ref = refs.read_reference(backend.store, reference_row)
    reference = p4b.load_json_bytes(raw_ref)
    raw_file = path / "native-output.json"
    invocation = read(path / "invocation.json")
    require(isinstance(invocation, dict), "NATIVE_INVOCATION_MISMATCH")
    supervisor = process["supervisor"]
    resource_caps = supervisor.get("resource_caps")
    require(isinstance(resource_caps, dict), "INVALID_PROCESS_EVIDENCE_SCHEMA")
    caps = backend.policy
    for key, value in {"timeout_seconds": caps["wall_timeout_seconds"], "address_space_mib": caps["address_space_limit_mib"],
                       "max_work": caps["logical_work_cap"], "max_nlf_memory_mib": caps["max_nlf_memory_mib"]}.items():
        observed = resource_caps.get(key)
        require(type(observed) in (float, int) and (key == "timeout_seconds" or type(observed) is int)
                and observed == value, f"RESOURCE_POLICY_MISMATCH: {key}")
    require(type(supervisor["deadline_fired"]) is bool, "INVALID_DEADLINE_EVIDENCE")
    pid = require_uint(supervisor["pid"], "pid", positive=True)
    status = require_uint(supervisor["wait_status"], "wait_status")
    require(status <= 65535 and (os.WIFEXITED(status) or os.WIFSIGNALED(status)), "INVALID_PROCESS_WAIT_STATUS")
    exit_code = os.waitstatus_to_exitcode(status)
    process_signal = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
    require(type(process["exit_code"]) is int and process["exit_code"] == exit_code
            and process["process_signal"] == process_signal, "PROCESS_WAIT_STATUS_MISMATCH")
    mode = p4b.ALL_VARIANTS[slot["variant_id"]]
    expected_command = [
        str(backend.binary), "--engine", mode["engine"], "--nlf-mode", mode["nlf_mode"],
        "--neighbor-mode", mode["neighbor_mode"], "--max-nlf-memory-mib", str(caps["max_nlf_memory_mib"]),
        "--max-work", str(caps["logical_work_cap"]), "--execution-purpose", slot["execution_purpose"],
        "--wall-timeout-seconds", str(math.ceil(caps["wall_timeout_seconds"])),
        "--workload", str(p4b.REPO_ROOT / entry["fixture_path"]), "--output", str(raw_file),
    ]
    require(invocation == {"pid": pid, "argv": expected_command, "environment": auth.NATIVE_ENV}
            and process["command"] == expected_command, "NATIVE_INVOCATION_MISMATCH")
    for key, pinned in (("binary_sha256", backend.binary_sha), ("fixture_sha256", entry["fixture_sha256"])):
        if process.get(key) != pinned or reference_row[key] != pinned:
            return _invalid(identity, backend, "PROVENANCE_ERROR", f"Pre-launch/reference identity mismatch: {key}")
    raw = raw_file.read_bytes() if raw_file.is_file() else None
    require(process["variant_id"] == slot["variant_id"]
            and process["execution_purpose"] == slot["execution_purpose"]
            and process["output_sha256"] == (p4b.sha256_bytes(raw) if raw is not None else None)
            and type(process["output_size_bytes"]) is int
            and process["output_size_bytes"] == (len(raw) if raw is not None else 0), "PROCESS_ARTIFACT_MISMATCH")
    metrics = {key: require_uint(process[key], key, positive=key == "outer_wall_ns")
               if process[key] is not None or key != "wait4_ru_maxrss_bytes" else None for key in PROCESS_METRICS}
    started = require_uint(supervisor["started_monotonic_ns"], "start")
    reaped = require_uint(supervisor["reaped_monotonic_ns"], "reap")
    require(reaped - started == metrics["outer_wall_ns"], "SUPERVISOR_WALL_TIME_MISMATCH")
    for metric, raw_key in (("user_cpu_ns", "wait4_user_seconds"), ("system_cpu_ns", "wait4_system_seconds")):
        seconds = supervisor[raw_key]
        require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds < 10**9
                and metrics[metric] == round(seconds * 10**9), "SUPERVISOR_CPU_TIME_MISMATCH")
    raw_rss = require_uint(supervisor["wait4_maxrss_kib"], "wait4_maxrss_kib")
    require(metrics["wait4_ru_maxrss_bytes"] == (raw_rss * 1024 if raw_rss else None),
            "SUPERVISOR_RSS_MISMATCH")
    synthetic = backend.origin == p4b.SYNTHETIC_KIND
    row = {**identity, **metrics, "process_exit_code": exit_code, "process_signal": process_signal,
           "raw_output_sha256": process["output_sha256"], "reference_sha256": reference_row["raw_output_sha256"],
           "reference_scope": reference_row["certification_scope"], "synthetic_test_fixture": synthetic,
           "measurement_eligible": False, "native_status": None, "correctness_status": None}
    stderr = (path / "stderr.log").read_bytes()
    workload_bytes = (p4b.REPO_ROOT / entry["fixture_path"]).read_bytes()
    if p4b.sha256_bytes(workload_bytes) != entry["fixture_sha256"]:
        return _invalid(identity, backend, "PROVENANCE_ERROR", "Frozen workload differs during ingestion")
    workload = p4b.load_json_bytes(workload_bytes)
    reported = process.get("censoring_reason")
    elapsed = metrics["outer_wall_ns"]

    def complete(outcome, reason, **values):
        return {**row, "attempt_outcome": outcome, "censoring_reason": reason, **values}

    def reconcile(*allowed):
        require(reported in allowed, f"SUPERVISOR_OUTCOME_MISMATCH: {reported!r}; expected {allowed}")

    deadline = supervisor["deadline_fired"]
    if deadline or process_signal == signal.SIGALRM:
        reconcile("TIMEOUT")
        require(elapsed >= caps["wall_timeout_seconds"] * 10**9
                and process_signal in (signal.SIGKILL, signal.SIGALRM), "UNSUPPORTED_TIMEOUT_CLAIM")
        extent = _check_interrupted_output(raw, slot, entry, workload, mode, reference, caps, elapsed, backend)
        _check_rss(process, raw_rss, p4b.load_json_bytes(raw) if extent == "COMPLETE" else None)
        return complete("CENSORED", "TIMEOUT", raw_output_extent=extent)
    if process_signal:
        reconcile("ENVIRONMENT_INTERRUPTION", "EXECUTION_FAILURE")
        extent = _check_interrupted_output(raw, slot, entry, workload, mode, reference, caps, elapsed, backend)
        return complete("INTERRUPTED", "ENVIRONMENT_INTERRUPTION", raw_output_extent=extent)
    if not raw:
        if exit_code == 5 and stderr.startswith(b"Resource containment error: "):
            reconcile("RESOURCE_UNAVAILABLE")
            require(bool(stderr.removeprefix(b"Resource containment error: ").strip()), "MISSING_CONTAINMENT_DIAGNOSTIC")
            _check_rss(process, raw_rss)
            return complete("CENSORED", "RESOURCE_UNAVAILABLE", raw_output_extent="ABSENT")
        return _invalid(identity, backend, "MISSING_RESULT", "No structured native result or supported supervisor resource outcome")

    native = p4b.load_json_bytes(raw)
    require(isinstance(native, dict), "NATIVE_RESULT_SCHEMA")
    if slot["execution_purpose"] == "performance":
        steps_list = native.get("steps")
        require("profiling" not in native
                and (not isinstance(steps_list, list)
                     or all(isinstance(s, dict) and "profiling" not in s for s in steps_list)),
                "TIMING_PROFILE_SCHEMA")
    pre_step_resource = (
        exit_code == 5
        and native.get("error_category") == "RESOURCE_LIMIT_ERROR"
        and isinstance(native.get("execution"), dict)
        and native["execution"].get("attempted_steps") == 0
    )
    if pre_step_resource:
        startup_partial = native.get("configuration_initialized") is not True
        startup_keys = {
            "raw_input_sha256": entry["fixture_sha256"],
            "execution_purpose": slot["execution_purpose"],
            "engine": mode["engine"],
            "engine_name": mode["engine"],
            "schema_version": reference["schema_version"],
            "semantics_version": reference["semantics_version"],
        }
        if not startup_partial or native.get("case_id") != "UNKNOWN":
            startup_keys["case_id"] = entry["case_id"]
        for key, value in startup_keys.items():
            require(key in native and p4b.canonical_json_bytes(native[key]) == p4b.canonical_json_bytes(value),
                    f"NATIVE_RESULT_IDENTITY_MISMATCH: {key}")
        if not startup_partial:
            _native_identity(native, slot, entry, mode, reference, caps, partial=False)
    else:
        _native_identity(native, slot, entry, mode, reference, caps, partial=True)
    metric_values = evidence.timings(native, elapsed)
    evidence.profile_timings(native, metric_values, elapsed)
    _check_rss(process, raw_rss, native)
    error = native.get("error_category")
    if error == "RESULT_DELIVERY_ERROR":
        reconcile("EXECUTION_FAILURE")
        require(exit_code == 9 and native.get("status") == "FAIL", "DELIVERY_EXIT_MISMATCH")
        return complete("FAILED", error)
    unavailable = native.get("configuration_status") == "REQUESTED_CONFIGURATION_UNAVAILABLE"
    resource = error == "RESOURCE_LIMIT_ERROR"
    work = error == "WORK_BUDGET_EXCEEDED"
    if not (exit_code == 0 or work or resource or unavailable):
        reconcile("EXECUTION_FAILURE")
        require(native.get("status") == "FAIL" and error != "NONE", "NATIVE_PROCESS_STATUS_CONTRADICTION")
        return complete("FAILED", "PREVALIDATION_ERROR" if error == "PREVALIDATION_ERROR" else "UNKNOWN_FAILURE")
    require((exit_code, native.get("status"), error) in (
        (0, "SUCCESS", "NONE"), (4, "FAIL", "WORK_BUDGET_EXCEEDED"), (5, "FAIL", "RESOURCE_LIMIT_ERROR")),
        "NATIVE_PROCESS_STATUS_CONTRADICTION")
    if pre_step_resource:
        life = native["execution"]
        for key in ("total_steps", "attempted_steps", "committed_steps", "completed_steps",
                    "delivered_steps", "failed_steps", "unattempted_steps"):
            require(key in life, f"INVALID_NUMERIC_EVIDENCE: {key}")
            require_uint(life[key], key)
        require(life["total_steps"] in (0, entry["update_count"])
                and life["attempted_steps"] == life["committed_steps"] == life["completed_steps"] == life["delivered_steps"] == life["failed_steps"] == 0
                and life["unattempted_steps"] == life["total_steps"]
                and native.get("steps") == [],
                "CENSORED_LIFECYCLE_MISMATCH")
        _native_schema(native, slot, entry, workload, mode, exit_code, unavailable=unavailable, resource=True)
        reconcile("RESOURCE_UNAVAILABLE", "MEMORY_LIMIT_FAILURE")
        require(native.get("error_message") == "std::bad_alloc", "INVALID_NATIVE_RESOURCE_EVIDENCE")
        reason = "MEMORY_LIMIT_FAILURE" if reported == "MEMORY_LIMIT_FAILURE" else "RESOURCE_UNAVAILABLE"
        return complete("CENSORED", reason, lifecycle=life, native_status=native["status"])
    _native_identity(native, slot, entry, mode, reference, caps)
    evidence.frozen_updates(native.get("steps"), workload["updates"])
    life = _lifecycle(native, entry["update_count"], exit_code == 0)
    if unavailable or resource or work:
        _native_schema(native, slot, entry, workload, mode, exit_code, unavailable=unavailable, resource=resource)
        if slot["execution_purpose"] == "diagnostic":
            require(isinstance(native.get("profiling"), dict), "TIMING_PROFILE_SCHEMA")
        require(native["work_budget"]["effective_limit"] == caps["logical_work_cap"], "CENSOR_WORK_CAP_MISMATCH")
        if work or resource:
            index = life["committed_steps"]
            failed_step = native["steps"][index]
            require(type(native.get("first_failing_step_index")) is int and native["first_failing_step_index"] == index
                    and native.get("first_failure_category") == failed_step["error_category"] == error,
                    "RESOURCE_FAILURE_STEP_MISMATCH")
            if work:
                reconcile("WORK_BUDGET_EXCEEDED")
                sc = failed_step.get("step_counters")
                require(isinstance(sc, dict) and failed_step["status"] == "CALLBACK_ABORTED"
                        and sc.get("work_units") == caps["logical_work_cap"] + 1,
                        "INVALID_WORK_BUDGET_EVIDENCE")
            else:
                reconcile("RESOURCE_UNAVAILABLE", "MEMORY_LIMIT_FAILURE")
                is_preexec_err = failed_step["status"] == "ERROR" and "step_counters" not in failed_step
                is_instep_oom = failed_step["status"] == "RESOURCE_EXHAUSTED" and isinstance(failed_step.get("step_counters"), dict)
                require((is_preexec_err or is_instep_oom)
                        and failed_step.get("error") == native.get("error_message") == native.get("first_failure_message")
                        and native.get("error_message") == "std::bad_alloc", "INVALID_NATIVE_RESOURCE_EVIDENCE")
                if is_instep_oom and slot["execution_purpose"] == "diagnostic":
                    require(isinstance(failed_step.get("profiling"), dict), "TIMING_STEP_SCHEMA")
        else:
            reconcile("CONFIGURATION_UNAVAILABLE")
        if unavailable:
            reference = _fallback_reference(backend, slot)
        _reference_prefix(native, reference, fallback=unavailable)
        if work and not unavailable and reference_row["native_exit_code"] == 4:
            for key in ("execution", "error_category", "counters"):
                require(p4b.canonical_json_bytes(native[key]) == p4b.canonical_json_bytes(reference[key]),
                        f"WORK_BUDGET_REFERENCE_MISMATCH: {key}")
        reason = "CONFIGURATION_UNAVAILABLE" if unavailable else (
            "WORK_BUDGET_EXCEEDED" if work else "MEMORY_LIMIT_FAILURE" if reported == "MEMORY_LIMIT_FAILURE" else "RESOURCE_UNAVAILABLE")
        return complete("CENSORED", reason, lifecycle=life, native_status=native["status"])

    reconcile("NONE")
    if reference_row["native_exit_code"] != 0:
        return complete("INELIGIBLE", "REFERENCE_COVERAGE_INSUFFICIENT")
    if slot["execution_purpose"] == "correctness":
        from scripts.replay_rapidflow_apx_p4b import MEASUREMENT_PATHS
        from scripts.canonical_output_comparator import diff_canonical_structures
        require(not diff_canonical_structures(native, reference, excluded_set=MEASUREMENT_PATHS, is_3way=False),
                "SEMANTIC_DISCREPANCY: correctness witness")
        return complete("COMPLETED", "NONE", native_status="SUCCESS", lifecycle=life)

    for key in ("peak_rss_bytes", "peak_rss_original_value", "resource_measurement_method", "peak_rss_original_unit"):
        row[key] = process[key]
    row.update(**metric_values, native_status=native["status"], updates_planned=entry["update_count"],
               updates_completed=life["completed_steps"], results_delivered=True)
    p4b.validate_measurement_payload(row, identity, workload, native, reference)
    cert = reference_row["certification_scope"]
    if cert == "CORRECTNESS_NOT_FULLY_CERTIFIED":
        require((backend.origin == ProductionBackend.origin or synthetic) and slot["variant_id"] == "BARE", "UNAPPROVED_C3_BARE_POLICY")
        cert = "LIMITED_C3_BARE_CONTROL"
    return complete("COMPLETED", "NONE", **{
        "configuration_initialized": True, "configuration_valid": True, "configuration_status": "SUCCESS",
        "eligible_for_requested_variant": True, "effective_nlf_mode": native["effective_nlf_mode"],
        "effective_neighbor_mode": native["effective_neighbor_mode"], "measurement_schema_version": native["measurement_schema_version"],
        "neighbor_configuration_version": native["neighbor_configuration_version"],
        "profiling_enabled": native["profiling_enabled"], "deep_diagnostics_enabled": native["deep_diagnostics_enabled"],
        "lifecycle": life, "correctness_status": cert,
        "measurement_eligible": backend.origin == ProductionBackend.origin and slot["slot_purpose"] == "primary",
        "diagnostic": _diagnostics(native) if slot["execution_purpose"] == "diagnostic" else None,
    })


def _validate_synthetic(slot, identity, path):
    payload = read(path / "native-output.json")
    require(isinstance(payload, dict) and payload.get("evidence_kind") == p4b.SYNTHETIC_KIND,
            "NON_SYNTHETIC_PAYLOAD")
    for name in payload.keys() & identity.keys():
        require(p4b.canonical_json_bytes(payload[name]) == p4b.canonical_json_bytes(identity[name]),
                f"SYNTHETIC_PAYLOAD_CROSS_SLOT: {name}")
    require(payload.get("attempt_outcome") in p4b.ATTEMPT_OUTCOMES - {"NOT_LAUNCHED"}, "INVALID_SYNTHETIC_OUTCOME")
    return {**payload, **identity, "synthetic_test_fixture": True,
            "raw_output_sha256": p4b.compute_sha256(path / "native-output.json")}


def validate_attempt(slot, entry, identity, backend, path):
    if not isinstance(backend, NativeBackend):
        return _validate_synthetic(slot, identity, path)
    try:
        return _validate_native(slot, entry, identity, backend, path, read(path / "process.json"))
    except (p4b.LedgerIntegrityError, KeyError, ValueError, TypeError, OSError) as error:
        detail = str(error)
        semantic = detail.startswith("SEMANTIC_DISCREPANCY:") or detail.startswith("MEASUREMENT_SEMANTIC_MISMATCH:")
        return {**identity, "attempt_outcome": "FAILED" if semantic else "INVALID",
                "censoring_reason": "CORRECTNESS_FAILURE" if semantic else "MEASUREMENT_ERROR",
                "correctness_status": "SEMANTIC_UNSOUND" if semantic else None, "error_detail": detail,
                "synthetic_test_fixture": backend.origin == p4b.SYNTHETIC_KIND, "measurement_eligible": False}


@contextmanager
def session_lock(root, create):
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise p4b.LedgerIntegrityError("SESSION_NOT_FOUND")
    with (root / ".lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise p4b.LedgerIntegrityError("SESSION_ALREADY_OWNED") from error
        yield lock


def _plan_identity(protocol, plans, backend, root):
    context = getattr(backend, "context", None)
    # Synthetic uses the existing R1 identity constants so both paths share
    # the established slot-level accounting and statistical contract.
    exp = context.experiment_id if context else (
        p4b.SYNTHETIC_EXPERIMENT if backend.origin == p4b.SYNTHETIC_KIND else "P4B3B0_BOUNDED_WITNESS")
    synthetic = backend.origin == p4b.SYNTHETIC_KIND
    source_revision = context.revision if context else p4b.SYNTHETIC_SOURCE if synthetic else backend.source
    return {
        "schema": SCHEMA, "experiment_id": exp, "backend": backend.origin,
        "activation_sha256": context.digest if context else None,
        "registry_sha256": p4b.compute_sha256(context.registry) if context else (
            digest({"/".join(k): v for k, v in backend.registry.items()})
            if isinstance(backend, NativeBackend) else None),
        "environment_sha256": context.environment_sha256 if context else None,
        "native_binary_sha256": p4b.SYNTHETIC_BINARY if synthetic else backend.binary_sha, "source_revision": source_revision,
        "native_source_commit": backend.source, "protocol_sha256": p4b.PROTOCOL_SHA256,
        "manifest_sha256": protocol["manifest_sha256"], "schedule_sha256": protocol["schedule_sha256"],
        "plan_slots_sha256": digest(plans), "registered_slots": len(plans),
        "session_id": digest([exp, str(root.resolve()), digest(plans), backend.origin]),
    }


def _identity(slot, protocol, header, number=1):
    # scheduled session_id is a frozen field. execution_session_id binds the
    # run directory and activation independently of the schedule's label.
    result = p4b.slot_identity(slot, protocol, experiment_id=header["experiment_id"],
                               evidence_kind=header["backend"], binary_sha=header["native_binary_sha256"],
                               source_revision=header["source_revision"])
    return dict(result, execution_session_id=header["session_id"], activation_sha256=header["activation_sha256"],
                attempt_number=number, attempt_id=f"{slot['slot_id']}/{number}", previous_attempt_id=None)


def _path(root, slot_index):
    return root / "attempts" / f"slot-{slot_index:04d}" / "attempt-001"


def _append(log, event):
    log.write((json.dumps(event, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode())
    log.flush()
    os.fsync(log.fileno())
    return digest(event)


def _required_files(backend):
    return FILES if isinstance(backend, NativeBackend) else FILES - {"invocation.json"}


def _finish_payload(path, outcome, backend):
    names = _required_files(backend)
    require(set(p.name for p in path.iterdir()) == names, "ATTEMPT_ARTIFACT_INVENTORY_MISMATCH")
    return {"outcome": outcome, "artifacts": {key: file_info(path / key) for key in sorted(names)}}


def _read_journal(root, log, plans, entries, protocol, header, backend):
    log.seek(0)
    terminal = []
    previous = digest(header)
    start = None
    for line in log:
        require(line.endswith(b"\n"), "TORN_LEDGER_APPEND")
        require(len(terminal) < len(plans), "EXTRA_JOURNAL_EVENT")
        event = p4b.load_json_bytes(line)
        require(isinstance(event, dict) and event["previous_sha256"] == previous, "LEDGER_CHAIN_MISMATCH")
        if terminal:
            require(terminal[-1]["attempt_outcome"] not in HALT_STATES, "ATTEMPT_AFTER_HALT")
        slot = plans[len(terminal)]
        identity = _identity(slot, protocol, header)
        path = _path(root, len(terminal))
        require(event["identity"] == identity and event["index"] == len(terminal), "LEDGER_SLOT_OR_SESSION_MISMATCH")
        if event["event"] == "START" and start is None:
            start = event
            require_uint(event["time_ns"], "start_time", positive=True)
            if (path / "start.json").exists():
                require(read(path / "start.json") == event, "START_ARTIFACT_MISMATCH")
        elif event["event"] == "FINISH" and start is not None:
            require_uint(event["time_ns"], "finish_time", positive=True)
            require(event["time_ns"] >= start["time_ns"], "REVERSED_EVENT_TIME")
            require(read(path / "start.json") == start, "MISSING_START_ARTIFACT")
            finish_obj = event.get("finish")
            require(isinstance(finish_obj, dict) and isinstance(finish_obj.get("artifacts"), dict),
                    "ILLEGAL_JOURNAL_TRANSITION")
            for name, expected in finish_obj["artifacts"].items():
                require(name in _required_files(backend) and file_info(path / name) == expected,
                        "FINISH_RAW_DIGEST_MISMATCH")
            result = validate_attempt(slot, entries[slot["workload_id"]], identity, backend, path)
            require(read(path / "validation.json") == result
                    and finish_obj == _finish_payload(path, result, backend),
                    "PERSISTED_OUTCOME_NOT_REPRODUCIBLE")
            terminal.append(result)
            start = None
        else:
            raise p4b.LedgerIntegrityError("ILLEGAL_JOURNAL_TRANSITION")
        previous = digest(event)
    attempt_root = root / "attempts"
    expected_names = {f"slot-{i:04d}" for i in range(len(terminal))}
    actual_names = {p.name for p in attempt_root.iterdir()} if attempt_root.is_dir() else set()
    if start is not None:
        expected_names.add(f"slot-{len(terminal):04d}")
    require(actual_names <= expected_names, "UNREGISTERED_ATTEMPT_DIRECTORY")
    return terminal, previous, start


def session(root, protocol, manifest, plans, backend, *, execute=False, stop_after=None, fault=None):
    """Shared scheduler and evidence replay. The production CLI supplies no fault hook."""
    entries = {w["workload_id"]: w for w in manifest["workloads"]}
    header = _plan_identity(protocol, plans, backend, root)
    production = backend.origin == ProductionBackend.origin
    if production:
        require(type(backend) is ProductionBackend and type(backend.context) is auth.Activation,
                "VERIFIED_PRODUCTION_CONTEXT_REQUIRED")
        backend.context.recheck()
        require(root.resolve() == backend.context.root.resolve(), "UNAPPROVED_SESSION_DIRECTORY")
        locked_protocol, locked_manifest, locked_schedule = p4b.load_frozen_plan()
        expected = [s for s in p4b.ordered_planned_slots(locked_schedule)
                    if s["slot_purpose"] != "diagnostic" or "diagnostic" in backend.context.phases]
        require(digest(plans) == digest(expected) and digest(protocol) == digest(locked_protocol)
                and digest(manifest) == digest(locked_manifest) and backend.policy == locked_protocol["resource_policy"],
                "PRODUCTION_PLAN_OR_CAPS_MISMATCH")
    trigger = fault or (lambda boundary: None)
    if stop_after is not None:
        require(type(stop_after) is int and 0 <= stop_after <= len(plans), "INVALID_PLANNED_STOP")
    with session_lock(root, create=execute):
        meta = root / "session-manifest.json"
        ledger = root / "ledger.jsonl"
        previously_created = meta.exists()
        if previously_created:
            existing = read(meta)
            require({k: v for k, v in existing.items() if k != "created_ns"} == header,
                    "SESSION_IDENTITY_CHANGED")
            header = existing
        else:
            require(execute and not ledger.exists(), "MISSING_SESSION_MANIFEST")
            header["created_ns"] = time.time_ns()
            write(meta, header)
        if production and execute and not (root / "environment-start.json").exists():
            write(root / "environment-start.json", auth.environment_snapshot(root, backend.binary, details=True))
        if not ledger.exists():
            require(execute and not previously_created and not (root / "attempts").exists(), "MISSING_SESSION_LEDGER")
            publish(ledger, b"")
        with ledger.open("a+b" if execute else "rb") as log:
            trigger("before_resume")
            terminal, previous, pending = _read_journal(root, log, plans, entries, protocol, header, backend)
            if pending:
                path = _path(root, len(terminal))
                return {"state": "SESSION_INTERRUPTED", "records": terminal, "next_slot": pending["identity"]["slot_id"],
                        "recovery": "STARTED_WITHOUT_FINISH_REVIEW_REQUIRED", "observed_process": (
                            read(path / "invocation.json") if (path / "invocation.json").is_file() else None)}
            if terminal and terminal[-1]["attempt_outcome"] in HALT_STATES:
                return {"state": "SESSION_HALTED", "records": terminal, "incident": terminal[-1]}
            if execute and len(terminal) < len(plans):
                end = len(plans) if stop_after is None else stop_after
                require(end >= len(terminal), "STALE_SCHEDULE_CURSOR")
                for index in range(len(terminal), end):
                    slot = plans[index]
                    backend.authorize(slot)
                    identity = _identity(slot, protocol, header)
                    trigger("before_start")
                    start = {"event": "START", "identity": identity, "index": index,
                             "previous_sha256": previous, "time_ns": time.time_ns()}
                    previous = _append(log, start)
                    trigger("after_start")
                    path = _path(root, index)
                    path.parent.parent.mkdir(exist_ok=True)
                    path.parent.mkdir()
                    path.mkdir()
                    sync_dir(root)
                    sync_dir(path.parent.parent)
                    sync_dir(path.parent)
                    write(path / "start.json", start)
                    trigger("before_launch")
                    entry = entries[slot["workload_id"]]
                    if production:
                        backend._active_attempt = (slot, entry, path)
                    try:
                        process = backend.execute(slot, entry, path)
                    finally:
                        if production:
                            del backend._active_attempt
                    trigger("after_exit")
                    if not isinstance(backend, NativeBackend):
                        write(path / "native-output.json", process)
                        publish(path / "stdout.log", b"")
                        publish(path / "stderr.log", b"")
                        write(path / "process.json", {"adapter": "synthetic", "native_process": False})
                    else:
                        with (path / "native-output.json").open("rb") as raw:
                            os.fsync(raw.fileno())
                        write(path / "process.json", process)
                    trigger("after_raw")
                    trigger("before_validation")
                    result = validate_attempt(slot, entries[slot["workload_id"]], identity, backend, path)
                    trigger("before_publication")
                    write(path / "validation.json", result)
                    finish = {"event": "FINISH", "identity": identity, "index": index,
                              "previous_sha256": previous, "time_ns": time.time_ns(),
                              "finish": _finish_payload(path, result, backend)}
                    trigger("before_finish")
                    previous = _append(log, finish)
                    trigger("after_finish")
                    terminal.append(result)
                    if result["attempt_outcome"] in HALT_STATES:
                        return {"state": "SESSION_HALTED", "records": terminal, "incident": result}
            finished = len(terminal) == len(plans)
            if production and execute and finished and not (root / "environment-end.json").exists():
                write(root / "environment-end.json", auth.environment_snapshot(root, backend.binary, details=True))
            return {"state": "SESSION_COMPLETED" if finished else "SESSION_PAUSED",
                    "records": terminal, "next_slot": None if finished else plans[len(terminal)]["slot_id"]}


def summarize_diagnostics(records):
    """Descriptive scope. Each timer has its own denominator and may nest."""
    result = {}
    for row in records:
        if row["slot_purpose"] != "diagnostic":
            continue
        variant = row["variant_id"]
        entry = result.setdefault(variant, {"completed": 0, "unavailable": 0,
                                            "per_slot": [], "execution_purpose": "diagnostic"})
        if row["attempt_outcome"] == "COMPLETED" and row.get("diagnostic"):
            entry["completed"] += 1
            entry["per_slot"].append({"slot_id": row["slot_id"], **row["diagnostic"]})
        else:
            entry["unavailable"] += 1
    return {"interpretation": "Descriptive nested stages. Observer cost is not calibrated by this session.",
            "variants": result}


def production_command(phase, envelope, binary, registry, archive, root, *, analyze=False):
    # Authorize ahead of mkdir, registry extraction, session locks or subprocess execution.
    if None in (envelope, archive, root):
        raise p4b.AuthorizationBlockedError("MEASUREMENT_NOT_AUTHORIZED: activation, archive and session required")
    if not root.parent.is_dir():
        raise p4b.AuthorizationBlockedError("SESSION_PARENT_NOT_FOUND")
    context = auth.authorize(envelope, binary, registry, archive, root, phase=None if analyze else phase)
    protocol, manifest, schedule = p4b.load_frozen_plan()
    plans = [s for s in p4b.ordered_planned_slots(schedule)
             if s["slot_purpose"] != "diagnostic" or "diagnostic" in context.phases]
    primary_end = len(schedule["warmup_slots"]) + len(schedule["primary_slots"])
    with tempfile.TemporaryDirectory(prefix="p4b-trusted-refs-", dir=root.parent) as temp:
        store = Path(temp)
        reference_map, _ = refs.validate_registry(
            registry, archive, store, protocol, manifest, bundle=context.bundle_id
        )
        context.recheck()
        backend = ProductionBackend(binary, reference_map, store, protocol["resource_policy"], context)
        if analyze or phase == "diagnostic":
            pre = session(root, protocol, manifest, plans, backend)
            if analyze:
                from scripts.summarize_rapidflow_apx_p4b import _analyze_reviewed_records
                context.recheck()
                return _analyze_reviewed_records(pre, protocol, manifest, schedule, context)
            require(len(pre["records"]) >= primary_end and pre["state"] not in ("SESSION_HALTED", "SESSION_INTERRUPTED"),
                    "DIAGNOSTIC_REQUIRES_VALID_PRIMARY")
        limit = primary_end if phase == "primary" else len(plans)
        outcome = session(root, protocol, manifest, plans, backend, execute=True, stop_after=limit)
        return {k: v for k, v in outcome.items() if k != "records"} | {"terminal_records": len(outcome["records"])}
