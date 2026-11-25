"""Detached authorization and environment checks for an activated P4b session."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from scripts import p4b_correctness_registry as refs
from scripts import run_rapidflow_apx_p4b as p4b

ANCHORS = "configs/rapidflow_apx_p4b_activation_anchors.json"
AMENDMENT = p4b.REPO_ROOT / "configs/rapidflow_apx_p4b_protocol_3b0_candidate.json"
ENVELOPE = "p4b-detached-activation-v1"
DOCS_ONLY = "docs/"
NATIVE_ENV = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
VALID_PHASES = (("primary",), ("primary", "diagnostic"))
SOURCE_PATHS = (
    "scripts/run_rapidflow_apx_p4b.py", "scripts/summarize_rapidflow_apx_p4b.py",
    "scripts/p4b_correctness_registry.py", "scripts/p4b_activation.py",
    "scripts/p4b_experiment_session.py", "scripts/p4b_native_evidence.py",
    "scripts/run_rapidflow_apx_engines.py",
)


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=p4b.REPO_ROOT).strip()


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _command(*args):
    try:
        return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def environment_snapshot(root, binary, *, details=False):
    cpus = sorted(os.sched_getaffinity(0))
    governor = {str(cpu): _read(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor") for cpu in cpus}
    cpuinfo = _read("/proc/cpuinfo") or ""
    cpu_model = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    mem = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        key, _, val = line.partition(":")
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            mem[key] = int(val.split()[0]) * 1024
    fs = _command("stat", "-f", "-c", "%T", str(root))
    cpu_topology = _command("lscpu", "-p=SOCKET,CORE")
    cores = len({row for row in (cpu_topology or "").splitlines() if row and not row.startswith("#")}) or None
    st_dev = root.stat().st_dev
    stable = {
        "os_release": platform.freedesktop_os_release().get("PRETTY_NAME"),
        "kernel": platform.release(), "architecture": platform.machine(),
        "cpu_model": cpu_model[1] if cpu_model else None, "logical_cpus": os.cpu_count(), "physical_cores": cores,
        "cpu_affinity": cpus, "cpu_governor": governor, "memory_total_bytes": mem.get("MemTotal"),
        "swap_total_bytes": mem.get("SwapTotal"), "python": platform.python_version(),
        "native_environment": NATIVE_ENV, "filesystem": fs, "filesystem_device": st_dev,
    }
    result = {"stable": stable, "dynamic": {"memory": mem, "filesystem_free_bytes": shutil.disk_usage(root).free,
                                           "load_average": list(os.getloadavg())}}
    if details:
        result["toolchain"] = {x: _command(x, "--version") for x in ("g++", "cmake")}
        binary = binary.resolve()
        # Never invoke a loader on an unchecked executable.
        if p4b.compute_sha256(binary) != p4b.NATIVE_SHA256:
            raise p4b.AuthorizationBlockedError("BINARY_HASH_MISMATCH")
        result["native_binary_sha256"] = p4b.NATIVE_SHA256
        result["runtime_dependencies"] = _command("ldd", str(binary))
        result["ambient_locale"] = {k: os.environ.get(k) for k in ("LANG", "LC_ALL", "TZ")}
        result["swap_devices"] = _read("/proc/swaps")
        result["thermal_millidegrees"] = {x.name: _read(x / "temp") for x in sorted(Path("/sys/class/thermal").glob("thermal_zone*"))}
        result["power_supply"] = {x.name: _read(x/"online") or _read(x/"status")
                                  for x in sorted(Path("/sys/class/power_supply").glob("*"))}
    return result


def _reviewed_anchors():
    data = p4b.load_json_bytes(_git("show", f"HEAD:{ANCHORS}"))
    if data["schema"] != "p4b-reviewed-activation-anchors-v1" or not isinstance(data["approved_envelopes"], dict):
        raise p4b.AuthorizationBlockedError("INVALID_REVIEWED_ANCHOR")
    return data["approved_envelopes"]


def _source_state(approved):
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise p4b.AuthorizationBlockedError("DIRTY_REVIEWED_CHECKOUT")
    if not re.fullmatch("[0-9a-f]{40}", approved):
        raise p4b.AuthorizationBlockedError("INVALID_IMPLEMENTATION_REVISION")
    try:
        _git("merge-base", "--is-ancestor", approved, "HEAD")
    except subprocess.CalledProcessError as error:
        raise p4b.AuthorizationBlockedError("IMPLEMENTATION_REVISION_NOT_ANCESTOR") from error
    changed = _git("diff", "--name-only", approved, "HEAD").decode().splitlines()
    if any(path != ANCHORS and not path.startswith(DOCS_ONLY) for path in changed):
        raise p4b.AuthorizationBlockedError("REVIEWED_IMPLEMENTATION_DRIFT")
    upstream = str(p4b.REPO_ROOT / "baselines/rapidflow/source")
    if (_git("-C", upstream, "rev-parse", "HEAD").decode() != p4b.EXPECTED_UPSTREAM_COMMIT
            or _git("-C", upstream, "status", "--porcelain")):
        raise p4b.AuthorizationBlockedError("UNPINNED_UPSTREAM")
    return {p: p4b.compute_sha256(p4b.REPO_ROOT/p) for p in SOURCE_PATHS}


@dataclass(frozen=True)
class Activation:
    envelope: Path
    binary: Path
    registry: Path
    archive: Path
    root: Path
    experiment_id: str
    digest: str
    phases: tuple
    revision: str
    environment_sha256: str
    stratum_rule: str
    resource_regression_rule: str

    def recheck(self, phase=None):
        verified = authorize(self.envelope, self.binary, self.registry, self.archive, self.root, phase=phase)
        if verified != self:
            raise p4b.AuthorizationBlockedError("ACTIVATION_CHANGED_DURING_SESSION")


def authorize(envelope, binary, registry, archive, root, *, phase=None):
    """Re-open the external, byte-pinned envelope on every authorized entry."""
    if envelope is None:
        raise p4b.AuthorizationBlockedError("MEASUREMENT_NOT_AUTHORIZED: no detached activation")
    raw = envelope.read_bytes()
    sha = p4b.sha256_bytes(raw)
    anchor = _reviewed_anchors().get(sha)
    if not anchor:
        raise p4b.AuthorizationBlockedError("MEASUREMENT_NOT_AUTHORIZED: envelope absent from reviewed trust anchor")
    doc = p4b.load_json_bytes(raw)
    if not isinstance(doc, dict) or doc.get("schema") != ENVELOPE or doc.get("approval_status") != "MEASUREMENT_AUTHORIZED":
        raise p4b.AuthorizationBlockedError("INVALID_ACTIVATION_STATE")
    exp = doc.get("experiment_id")
    if not isinstance(exp, str) or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{1,95}", exp):
        raise p4b.AuthorizationBlockedError("INVALID_EXPERIMENT_ID")
    for key in ("experiment_id", "reviewer_approval_ref", "coordinator_approval_ref"):
        if not isinstance(anchor.get(key), str) or not anchor[key].strip() or doc.get(key) != anchor[key]:
            raise p4b.AuthorizationBlockedError(f"APPROVAL_REFERENCE_MISMATCH: {key}")
    protocol, _, _ = p4b.load_frozen_plan()
    env = environment_snapshot(root.parent, binary)
    revision = doc.get("implementation_revision", "")
    sources = _source_state(revision)
    if binary.stat().st_size != 941216 or p4b.compute_sha256(binary) != p4b.NATIVE_SHA256:
        raise p4b.AuthorizationBlockedError("BINARY_HASH_MISMATCH")
    if archive.stat().st_size != refs.ARCHIVE_SIZE or p4b.compute_sha256(archive) != refs.ARCHIVE_SHA:
        raise p4b.AuthorizationBlockedError("ARCHIVE_HASH_MISMATCH")
    expected = {
        "scope": "P4b-3B.1_CONFIRMATORY_MEASUREMENT", "protocol_sha256": p4b.PROTOCOL_SHA256,
        "approved_session_path": str(root.resolve()),
        "manifest_sha256": protocol["manifest_sha256"], "schedule_sha256": protocol["schedule_sha256"],
        "registry_sha256": p4b.compute_sha256(registry), "r1_evidence": refs.ROOT,
        "native_source_commit": p4b.NATIVE_SOURCE_COMMIT, "native_binary_sha256": p4b.NATIVE_SHA256,
        "source_file_sha256": sources, "resource_policy": protocol["resource_policy"],
        "statistical_amendment_sha256": p4b.compute_sha256(AMENDMENT),
        "environment_sha256": p4b.sha256_bytes(p4b.canonical_json_bytes(env["stable"])),
        "correctness_policy": "M0_AND_SAME_VARIANT_DIFFERENTIAL",
        "c3_bare_policy": "ALLOW_LIMITED_BARE_CONTROL_NOT_ORACLE",
        "disc07_disposition": "RETAIN_DISC_07_UNCHANGED",
    }
    for key, value in expected.items():
        if p4b.canonical_json_bytes(doc.get(key)) != p4b.canonical_json_bytes(value):
            raise p4b.AuthorizationBlockedError(f"ACTIVATION_IDENTITY_MISMATCH: {key}")
    phases = doc.get("phases")
    if not isinstance(phases, list) or tuple(phases) not in VALID_PHASES or phase is not None and phase not in phases:
        raise p4b.AuthorizationBlockedError("UNAUTHORIZED_EXECUTION_PHASE")
    strata = doc.get("stratum_rule")
    resource = doc.get("resource_regression_rule")
    if strata not in ("R1_REGISTERED", "CONSERVATIVE_3B0") or resource not in ("R1_REGISTERED", "SAME_CATEGORY"):
        raise p4b.AuthorizationBlockedError("UNREVIEWED_INFERENCE_POLICY")
    if env["dynamic"]["filesystem_free_bytes"] < 12 * 1024**3:
        raise p4b.AuthorizationBlockedError("INSUFFICIENT_EVIDENCE_STORAGE")
    if (env["dynamic"]["memory"].get("MemAvailable") or 0) < 768 * 1024**2:
        raise p4b.AuthorizationBlockedError("INSUFFICIENT_AVAILABLE_MEMORY")
    return Activation(envelope, binary, registry, archive, root, exp, sha, tuple(phases), revision,
                      expected["environment_sha256"], strata, resource)
