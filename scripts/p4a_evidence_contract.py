#!/usr/bin/env python3
"""
scripts/p4a_evidence_contract.py — Unified Experimental Evidence & Provenance Contract.

Defines the shared data integrity, record schema, cryptographic self-consistency,
duplicate attempt lineage resolution, and pair compatibility contracts for Phase P4a.

Shared by:
- scripts/run_rapidflow_apx_p4a.py (Runner & Resume supervisor)
- scripts/summarize_rapidflow_apx_p4a.py (Ledger analyzer & Profile reporter)
- tests/test_rapidflow_apx_p4a.py (Comprehensive validation & adversarial tests)
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent

# Protocol & Versioning Standards
PROTOCOL_MEASUREMENT_VERSION = "1.2.0"
PROTOCOL_ANALYSIS_VERSION = "1.4.0"
PROTOCOL_SPECIFICATION_REVISION = "1.4.0"

# Artifact Integrity Status Taxonomy (R6-F02)
ARTIFACT_STATUS_NOT_CHECKED = "NOT_CHECKED"
ARTIFACT_STATUS_VERIFIED = "VERIFIED"
ARTIFACT_STATUS_FAILED = "FAILED"

# Approved Hard Ceilings
CANONICAL_MAX_WORK_CAP = 500000
CANONICAL_TIMEOUT_CEILING = 60.0
CANONICAL_AS_LIMIT_MIB = 512
CANONICAL_REPETITIONS_CEILING = 10

# Approved Historical Canonical Authority Constants (R7-F01..R7-F04)
APPROVED_P4A_EXECUTION_IDENTITY = "262279a9d9ea026e07231f3ba511863c383b98133db40e69f603a235ad792135"
APPROVED_P4A_CANONICAL_COMMIT = "cf97a7840dff0c0f2bf791834263f3911545110c"
APPROVED_P4A_MANIFEST_SHA256 = "f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18"
APPROVED_P4A_PROTOCOL_SHA256 = "dd3f39cae4325b3577adec8fa19ff5f52844e9338b1d97aa542d58b6c09d4ebd"
APPROVED_P4A_PROTOCOL_VERSION = "1.2.0"
APPROVED_P4A_BINARY_SHA256 = "6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b"
APPROVED_P4A_RAW_ARTIFACT_SHA256 = "34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5"
APPROVED_P4A_RAW_ARTIFACT_SIZE_BYTES = 441311
APPROVED_P4A_RAW_ARTIFACT_LINE_COUNT = 180
APPROVED_P4A_RAW_ARTIFACT_REL_PATH = "results/logs/rf36-p4a/p4a_raw_ledger.jsonl"
APPROVED_P4A_WORKLOAD_COUNT = 30
APPROVED_P4A_REPETITIONS = 3
APPROVED_P4A_ENGINES = ("m1", "m2")
APPROVED_P4A_TOTAL_SLOTS = 180
APPROVED_P4A_TOTAL_PAIRS = 90

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "docs" / "rapidflow_apx_p4a_protocol.md"
DEFAULT_EVIDENCE_MANIFEST_PATH = REPO_ROOT / "docs" / "evidence" / "p4a_evidence_manifest.json"
CANONICAL_REPETITIONS = 3
CANONICAL_ENGINES = ("m1", "m2")


class CorruptedLedgerError(Exception):
    """Raised when ledger contains unparseable lines, corrupted schemas, or tampered records."""
    pass


class CanonicalAuthorityError(CorruptedLedgerError):
    """Raised when canonical certification authority is violated or untrusted (R7-F01..R7-F04)."""
    pass


class IntegrityConflictError(CorruptedLedgerError):
    """Raised when multiple attempts for a slot have conflicting immutable metadata."""
    pass


class AmbiguousSlotError(CorruptedLedgerError):
    """Raised when multiple verified attempts exist with conflicting runtimes and no deterministic lineage."""
    pass


class ArtifactIntegrityError(CorruptedLedgerError):
    """Raised when raw ledger file does not match trusted evidence manifest checksum or size."""
    pass


class UntrustedExperimentContextError(CorruptedLedgerError):
    """Raised when canonical certification is attempted without verified trusted experiment context."""
    pass


def compute_sha256(file_path: Union[str, Path]) -> str:
    """Computes SHA-256 of a file using 64KB chunking."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def is_valid_hex64(val: Any) -> bool:
    """Returns True if val is a 64-character hexadecimal string."""
    if not isinstance(val, str) or len(val) != 64:
        return False
    try:
        int(val, 16)
        return True
    except ValueError:
        return False


def compute_dict_sha256(data: Any) -> str:
    """Computes deterministic SHA-256 of JSON-serializable structure with sorted keys."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_execution_identity(
    protocol_version: str,
    protocol_sha256: str,
    manifest_sha256: str,
    source_commit: str,
    source_dirty: str,
    binary_sha256: str,
    effective_max_work: int,
    effective_timeout: float,
    effective_as_limit_mib: int,
) -> str:
    """Computes canonical 64-char hex execution identity binding the experiment environment."""
    payload = {
        "protocol_version": str(protocol_version).strip(),
        "protocol_sha256": str(protocol_sha256).strip().lower(),
        "manifest_sha256": str(manifest_sha256).strip().lower(),
        "source_commit": str(source_commit).strip(),
        "source_dirty": str(source_dirty).strip(),
        "binary_sha256": str(binary_sha256).strip().lower(),
        "effective_max_work": int(effective_max_work),
        "effective_timeout": round(float(effective_timeout), 4),
        "effective_as_limit_mib": int(effective_as_limit_mib),
    }
    return compute_dict_sha256(payload)


def get_frozen_manifest_registry(manifest_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """
    Loads workload manifest, computes manifest_sha256, and builds workload registry.
    Enforces that all case_ids are non-empty and unique.
    """
    m_path = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    if not m_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {m_path}")

    with open(m_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest_sha = compute_sha256(m_path)
    cases = data.get("cases", [])
    if not cases:
        raise ValueError(f"Manifest {m_path} contains zero cases")

    workload_hashes: Dict[str, str] = {}
    cases_by_id: Dict[str, Dict[str, Any]] = {}

    for idx, c in enumerate(cases):
        cid = c.get("case_id")
        if not cid or not isinstance(cid, str):
            raise ValueError(f"Case at index {idx} in {m_path} has missing or non-string case_id")
        if cid in workload_hashes:
            raise ValueError(f"Duplicate case_id {cid} detected in manifest {m_path}")
        whash = compute_dict_sha256(c)
        workload_hashes[cid] = whash
        cases_by_id[cid] = c

    return {
        "manifest_path": str(m_path),
        "manifest_sha256": manifest_sha,
        "cases_count": len(cases),
        "workload_hashes": workload_hashes,
        "cases_by_id": cases_by_id,
        "cases": cases,
    }


def create_experiment_header(
    manifest_path: Optional[Union[str, Path]] = None,
    protocol_version: str = PROTOCOL_MEASUREMENT_VERSION,
    protocol_sha256: Optional[str] = None,
    source_commit: str = "",
    source_dirty_state: str = "CLEAN",
    binary_sha256: str = "",
    effective_max_work: int = CANONICAL_MAX_WORK_CAP,
    effective_timeout: float = CANONICAL_TIMEOUT_CEILING,
    effective_memory_limit_mib: int = CANONICAL_AS_LIMIT_MIB,
) -> Dict[str, Any]:
    """Creates a complete frozen experiment header including workload registry."""
    reg = get_frozen_manifest_registry(manifest_path)
    p_sha = protocol_sha256
    if not p_sha and DEFAULT_PROTOCOL_PATH.is_file():
        p_sha = compute_sha256(DEFAULT_PROTOCOL_PATH)
    if not p_sha:
        p_sha = "0" * 64

    exec_id = compute_execution_identity(
        protocol_version=protocol_version,
        protocol_sha256=p_sha,
        manifest_sha256=reg["manifest_sha256"],
        source_commit=source_commit,
        source_dirty=source_dirty_state,
        binary_sha256=binary_sha256,
        effective_max_work=effective_max_work,
        effective_timeout=effective_timeout,
        effective_as_limit_mib=effective_memory_limit_mib,
    )

    return {
        "execution_identity": exec_id,
        "protocol_version": protocol_version,
        "protocol_sha256": p_sha,
        "manifest_sha256": reg["manifest_sha256"],
        "source_commit": source_commit,
        "source_dirty_state": source_dirty_state,
        "binary_sha256": binary_sha256,
        "effective_max_work": effective_max_work,
        "effective_timeout": effective_timeout,
        "effective_memory_limit_mib": effective_memory_limit_mib,
        "workload_hashes": reg["workload_hashes"],
        "cases_count": reg["cases_count"],
    }


def load_verified_ledger(
    ledger_path: Optional[Union[str, Path]],
    trusted_artifact_meta: Optional[Dict[str, Any]],
) -> Tuple[Optional[List[Dict[str, Any]]], bool, str, Dict[str, Any]]:
    """
    Loads and verifies raw ledger records against trusted external reference metadata in a single snapshot (R6-F02, R6-F03).
    Reads raw bytes once, computes SHA-256 and byte length, verifies against trusted metadata,
    and parses JSONL records directly from the verified bytes.
    Returns: (records, is_valid, diagnostic_message, verification_meta)
    """
    if ledger_path is None:
        return None, False, "No ledger_path provided", {}
    if trusted_artifact_meta is None:
        return None, False, "No trusted_artifact_meta provided", {}

    p = Path(ledger_path)
    if not p.exists():
        return None, False, f"Raw artifact file does not exist: {p}", {}
    if not p.is_file():
        return None, False, f"Raw artifact path is not a file: {p}", {}

    if not isinstance(trusted_artifact_meta, dict):
        return None, False, f"trusted_artifact_meta must be a dict, got {type(trusted_artifact_meta).__name__}", {}

    expected_sha = trusted_artifact_meta.get("sha256")
    if not expected_sha or not is_valid_hex64(expected_sha):
        return None, False, f"Invalid or missing trusted sha256 in artifact reference: {expected_sha}", {}

    expected_size = trusted_artifact_meta.get("size_bytes")
    expected_lines = trusted_artifact_meta.get("line_count")


    # Read raw bytes once (atomic snapshot)
    try:
        raw_bytes = p.read_bytes()
    except Exception as e:
        return None, False, f"Failed to read raw artifact bytes from {p}: {e}", {}

    actual_size = len(raw_bytes)
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()

    if actual_sha.lower() != expected_sha.lower():
        return None, False, f"Raw artifact SHA-256 mismatch for {p}: actual {actual_sha} != expected {expected_sha}", {
            "actual_sha256": actual_sha,
            "expected_sha256": expected_sha,
            "actual_size_bytes": actual_size,
        }

    if expected_size is not None and actual_size != expected_size:
        return None, False, f"Raw artifact byte size mismatch for {p}: actual {actual_size} != expected {expected_size}", {
            "actual_sha256": actual_sha,
            "actual_size_bytes": actual_size,
            "expected_size_bytes": expected_size,
        }

    # Parse JSONL records directly from verified bytes
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, False, f"Raw artifact UTF-8 decode error in {p}: {e}", {}

    records = []
    non_empty_lines = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        non_empty_lines += 1
        try:
            rec = json.loads(stripped)
            if not isinstance(rec, dict):
                return None, False, f"Record at line {line_no} in {p} is not a JSON object", {}
            records.append(rec)
        except Exception as e:
            return None, False, f"Corrupted JSON in {p} at line {line_no}: {e}", {}

    if expected_lines is not None and non_empty_lines != expected_lines:
        return None, False, f"Raw artifact line count mismatch for {p}: actual {non_empty_lines} != expected {expected_lines}", {
            "actual_lines": non_empty_lines,
            "expected_lines": expected_lines,
        }

    meta = {
        "actual_sha256": actual_sha,
        "actual_size_bytes": actual_size,
        "sha256": actual_sha,
        "size_bytes": actual_size,
        "record_count": len(records),
        "status": ARTIFACT_STATUS_VERIFIED,
    }
    return records, True, "", meta


def verify_raw_artifact_integrity(
    ledger_path: Optional[Union[str, Path]],
    trusted_artifact_meta: Optional[Dict[str, Any]],
) -> Tuple[bool, str, str]:
    """
    Verifies raw artifact integrity against trusted external reference metadata (R5-F02, R6-F02, R6-F03).
    Delegates to load_verified_ledger to enforce single-snapshot verification.
    Returns: (is_valid, status, diagnostic_message)
    where status is one of ARTIFACT_STATUS_NOT_CHECKED, ARTIFACT_STATUS_VERIFIED, ARTIFACT_STATUS_FAILED.
    """
    if ledger_path is None and trusted_artifact_meta is None:
        return False, ARTIFACT_STATUS_NOT_CHECKED, "No artifact verification attempted"
    if ledger_path is None or trusted_artifact_meta is None:
        return False, ARTIFACT_STATUS_NOT_CHECKED, "Incomplete artifact verification parameters"
    _, ok, diag, _ = load_verified_ledger(ledger_path, trusted_artifact_meta)
    status = ARTIFACT_STATUS_VERIFIED if ok else ARTIFACT_STATUS_FAILED
    return ok, status, diag



def compute_expected_slot_matrix(
    manifest_registry: Union[Dict[str, Any], Path, str],
    repetitions: int = CANONICAL_REPETITIONS,
    engines: Tuple[str, ...] = CANONICAL_ENGINES,
) -> Dict[str, Any]:
    """
    Computes deterministic expected slot and pair matrix from manifest registry (R5-F01).
    Returns:
      - workload_ids: sorted list of case_ids
      - repetitions: repetition count
      - engines: engine tuple
      - expected_slots: set of slot keys (e.g. 'P4A_WL_01_rep0_m1')
      - expected_pairs: set of (workload_id, repetition) tuples
      - total_expected_slots: int
      - total_expected_pairs: int
    """
    if isinstance(manifest_registry, (str, Path)):
        reg = get_frozen_manifest_registry(manifest_registry)
        w_hashes = reg.get("workload_hashes", {})
    elif isinstance(manifest_registry, dict):
        if "workload_hashes" in manifest_registry:
            w_hashes = manifest_registry["workload_hashes"]
        elif "cases_by_id" in manifest_registry:
            w_hashes = manifest_registry["cases_by_id"]
        elif "cases" in manifest_registry and isinstance(manifest_registry["cases"], list):
            w_hashes = {c["case_id"]: c for c in manifest_registry["cases"] if isinstance(c, dict) and "case_id" in c}
        else:
            w_hashes = manifest_registry
    else:
        raise ValueError(f"Invalid manifest_registry type: {type(manifest_registry)}")

    workload_ids = sorted(w_hashes.keys())
    expected_slots = {
        f"{wid}_rep{r}_{e}"
        for wid in workload_ids
        for r in range(repetitions)
        for e in engines
    }
    expected_pairs = {
        (wid, r)
        for wid in workload_ids
        for r in range(repetitions)
    }
    return {
        "workload_ids": workload_ids,
        "repetitions": repetitions,
        "engines": list(engines),
        "expected_slots": expected_slots,
        "expected_pairs": expected_pairs,
        "total_expected_slots": len(expected_slots),
        "total_expected_pairs": len(expected_pairs),
    }


def load_trusted_evidence_manifest(
    manifest_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Loads and validates the trusted evidence manifest (R5-F02, R5-F04)."""
    p = Path(manifest_path) if manifest_path is not None else DEFAULT_EVIDENCE_MANIFEST_PATH
    if not p.is_file():
        raise FileNotFoundError(f"Evidence manifest not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "execution_identity" not in data:
        raise ValueError(f"Evidence manifest {p} missing execution_identity")
    if "artifacts" not in data:
        raise ValueError(f"Evidence manifest {p} missing artifacts registry")
    return data


def get_trusted_canonical_header(
    evidence_manifest_path: Optional[Union[str, Path]] = None,
    workload_manifest_path: Optional[Union[str, Path]] = None,
    protocol_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Constructs authoritative expected header for canonical certification (R5-F04).
    Binds:
    - execution_identity from trusted evidence manifest
    - canonical_execution_commit from evidence manifest
    - protocol_sha256 from protocol specification
    - manifest_sha256 and workload_hashes from workload manifest
    - raw artifact reference for raw ledger
    """
    ev_manifest = load_trusted_evidence_manifest(evidence_manifest_path)
    reg = get_frozen_manifest_registry(workload_manifest_path)
    proto_p = Path(protocol_path) if protocol_path is not None else DEFAULT_PROTOCOL_PATH
    proto_sha = compute_sha256(proto_p) if proto_p.is_file() else "0" * 64

    canon_exec_id = ev_manifest["execution_identity"]
    canon_commit = ev_manifest.get("canonical_execution_commit", "")
    historical_proto_sha = (
        ev_manifest.get("protocol_version_distinction", {}).get("historical_measurement_protocol_sha256")
        or proto_sha
    )

    raw_ledger_rel = "results/logs/rf36-p4a/p4a_raw_ledger.jsonl"
    artifact_meta = ev_manifest.get("artifacts", {}).get(raw_ledger_rel, {})

    return {
        "execution_identity": canon_exec_id,
        "protocol_version": PROTOCOL_MEASUREMENT_VERSION,
        "protocol_sha256": historical_proto_sha,
        "manifest_sha256": reg["manifest_sha256"],
        "source_commit": canon_commit,
        "source_dirty_state": "CLEAN",
        "binary_sha256": "",
        "effective_max_work": CANONICAL_MAX_WORK_CAP,
        "effective_timeout": CANONICAL_TIMEOUT_CEILING,
        "effective_memory_limit_mib": CANONICAL_AS_LIMIT_MIB,
        "workload_hashes": reg["workload_hashes"],
        "cases_count": reg["cases_count"],
        "trusted_raw_artifact": artifact_meta,
    }


def get_trusted_raw_artifact_meta(
    evidence_manifest_path: Optional[Union[str, Path]] = None,
    target_rel_or_name: str = "results/logs/rf36-p4a/p4a_raw_ledger.jsonl",
) -> Dict[str, Any]:
    """Helper to retrieve trusted raw artifact metadata from evidence manifest (R6-F02)."""
    if evidence_manifest_path is None:
        authority = get_official_p4a_authority()
        if target_rel_or_name == authority.raw_artifact_rel_path or Path(target_rel_or_name).name == Path(authority.raw_artifact_rel_path).name:
            return dict(authority.trusted_artifact_meta)
    ev_manifest = load_trusted_evidence_manifest(evidence_manifest_path)
    artifacts = ev_manifest.get("artifacts", {})
    if target_rel_or_name in artifacts:
        return artifacts[target_rel_or_name]
    target_name = Path(target_rel_or_name).name
    for rel_path, meta in artifacts.items():
        if Path(rel_path).name == target_name:
            return meta
    raise KeyError(f"Artifact '{target_rel_or_name}' not found in evidence manifest artifacts: {list(artifacts.keys())}")


class OfficialCanonicalAuthority:
    """
    Immutable trust anchor for Phase P4a canonical certification (R7-F01..R7-F04).
    Anchors the human-approved, peer-reviewed historical canonical pilot specification:
    - Target revision / commit: cf97a7840dff0c0f2bf791834263f3911545110c
    - Execution identity: 262279a9d9ea026e07231f3ba511863c383b98133db40e69f603a235ad792135
    - Raw artifact SHA-256: 34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5 (441311 bytes, 180 records)
    - Workload manifest SHA-256: f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18 (30 workloads)
    - Measurement protocol SHA-256: dd3f39cae4325b3577adec8fa19ff5f52844e9338b1d97aa542d58b6c09d4ebd (version 1.2.0)
    - Resource limits: max_work=500000, timeout=60.0s, as_limit=512MiB
    - Expected slot matrix: 30 workloads x 3 repetitions x 2 engines = 180 slots, 90 pairs
    """
    def __init__(
        self,
        workload_manifest_path: Optional[Union[str, Path]] = None,
    ):
        self.execution_identity = APPROVED_P4A_EXECUTION_IDENTITY
        self.canonical_execution_commit = APPROVED_P4A_CANONICAL_COMMIT
        self.manifest_sha256 = APPROVED_P4A_MANIFEST_SHA256
        self.protocol_sha256 = APPROVED_P4A_PROTOCOL_SHA256
        self.protocol_version = APPROVED_P4A_PROTOCOL_VERSION
        self.binary_sha256 = APPROVED_P4A_BINARY_SHA256
        self.raw_artifact_sha256 = APPROVED_P4A_RAW_ARTIFACT_SHA256
        self.raw_artifact_size_bytes = APPROVED_P4A_RAW_ARTIFACT_SIZE_BYTES
        self.raw_artifact_line_count = APPROVED_P4A_RAW_ARTIFACT_LINE_COUNT
        self.raw_artifact_rel_path = APPROVED_P4A_RAW_ARTIFACT_REL_PATH
        self.workload_count = APPROVED_P4A_WORKLOAD_COUNT
        self.repetitions = APPROVED_P4A_REPETITIONS
        self.engines = APPROVED_P4A_ENGINES
        self.total_expected_slots = APPROVED_P4A_TOTAL_SLOTS
        self.total_expected_pairs = APPROVED_P4A_TOTAL_PAIRS

        # Compute approved expected slot matrix from frozen manifest
        m_path = Path(workload_manifest_path) if workload_manifest_path is not None else DEFAULT_MANIFEST_PATH
        self.workload_manifest_registry = get_frozen_manifest_registry(m_path)
        if self.workload_manifest_registry["manifest_sha256"] != self.manifest_sha256:
            raise CanonicalAuthorityError(
                f"Workload manifest SHA-256 mismatch: {self.workload_manifest_registry['manifest_sha256']} "
                f"!= approved {self.manifest_sha256}"
            )
        self.expected_slot_matrix = compute_expected_slot_matrix(
            self.workload_manifest_registry,
            repetitions=self.repetitions,
            engines=self.engines,
        )

        # Expected trusted artifact meta
        self.trusted_artifact_meta = {
            "sha256": self.raw_artifact_sha256,
            "size_bytes": self.raw_artifact_size_bytes,
            "line_count": self.raw_artifact_line_count,
            "git_tracked": False,
            "accessibility_status": "LOCAL_WORKTREE_REPRODUCIBLE",
            "description": "Primary raw experiment ledger",
        }

        # Expected canonical header
        self.expected_header = {
            "execution_identity": self.execution_identity,
            "protocol_version": self.protocol_version,
            "protocol_sha256": self.protocol_sha256,
            "manifest_sha256": self.manifest_sha256,
            "source_commit": self.canonical_execution_commit,
            "source_dirty_state": "CLEAN",
            "binary_sha256": self.binary_sha256,
            "effective_max_work": CANONICAL_MAX_WORK_CAP,
            "effective_timeout": CANONICAL_TIMEOUT_CEILING,
            "effective_memory_limit_mib": CANONICAL_AS_LIMIT_MIB,
            "workload_hashes": self.workload_manifest_registry["workload_hashes"],
            "cases_count": self.workload_count,
            "trusted_raw_artifact": self.trusted_artifact_meta,
        }

    def verify_expected_header(self, header: Any) -> Tuple[bool, str]:
        """Strictly validates caller-supplied expected_header against approved authority."""
        if not isinstance(header, dict):
            return False, f"expected_header must be a dict, got {type(header).__name__}"
        required_fields = [
            "execution_identity", "source_commit", "manifest_sha256",
            "protocol_sha256", "protocol_version"
        ]
        for rf in required_fields:
            if not header.get(rf):
                return False, f"expected_header missing required field: '{rf}'"
        if header["execution_identity"] != self.execution_identity:
            return False, f"execution_identity {header['execution_identity']} != approved {self.execution_identity}"
        if header["source_commit"] != self.canonical_execution_commit:
            return False, f"source_commit {header['source_commit']} != approved {self.canonical_execution_commit}"
        if header["manifest_sha256"].lower() != self.manifest_sha256.lower():
            return False, f"manifest_sha256 {header['manifest_sha256']} != approved {self.manifest_sha256}"
        if header["protocol_sha256"].lower() != self.protocol_sha256.lower():
            return False, f"protocol_sha256 {header['protocol_sha256']} != approved {self.protocol_sha256}"
        if str(header["protocol_version"]).strip() != self.protocol_version:
            return False, f"protocol_version {header['protocol_version']} != approved {self.protocol_version}"
        if "effective_max_work" in header and header["effective_max_work"] != CANONICAL_MAX_WORK_CAP:
            return False, f"effective_max_work {header['effective_max_work']} != approved {CANONICAL_MAX_WORK_CAP}"
        if "trusted_raw_artifact" in header and isinstance(header["trusted_raw_artifact"], dict):
            art_sha = header["trusted_raw_artifact"].get("sha256", "")
            if art_sha.lower() != self.raw_artifact_sha256.lower():
                return False, f"trusted_raw_artifact SHA {art_sha} in header != approved {self.raw_artifact_sha256}"
        return True, ""

    def verify_expected_slot_matrix(self, matrix: Any) -> Tuple[bool, str]:
        """Strictly validates caller-supplied expected_slot_matrix against approved authority (R7-F01)."""
        if not isinstance(matrix, dict):
            return False, f"expected_slot_matrix must be a dict, got {type(matrix).__name__}"
        if "expected_slots" not in matrix:
            return False, "expected_slot_matrix missing 'expected_slots'"

        # Repetitions check
        if "repetitions" in matrix:
            rep = matrix["repetitions"]
            if type(rep) is not int or isinstance(rep, bool) or rep != self.repetitions:
                return False, f"Invalid repetitions {rep}: must be int {self.repetitions}"

        # Engines check
        if "engines" in matrix:
            engs = set(matrix["engines"])
            if engs != set(self.engines):
                return False, f"Invalid engines {engs}: must be {set(self.engines)}"

        # Workload IDs check
        if "workload_ids" in matrix:
            wids = list(matrix["workload_ids"])
            if wids != self.expected_slot_matrix["workload_ids"]:
                return False, "workload_ids do not match approved workload registry"

        caller_slots = set(matrix["expected_slots"])
        if caller_slots != self.expected_slot_matrix["expected_slots"]:
            diff_missing = self.expected_slot_matrix["expected_slots"] - caller_slots
            diff_extra = caller_slots - self.expected_slot_matrix["expected_slots"]
            return False, (
                f"expected_slots mismatch: {len(caller_slots)} slots provided != {self.total_expected_slots} approved slots. "
                f"Missing {len(diff_missing)}, extra {len(diff_extra)}."
            )

        if "expected_pairs" in matrix:
            caller_pairs = set(matrix["expected_pairs"])
            normalized_pairs = {tuple(p) for p in caller_pairs}
            if normalized_pairs != self.expected_slot_matrix["expected_pairs"]:
                return False, f"expected_pairs mismatch: {len(normalized_pairs)} pairs != {self.total_expected_pairs} approved pairs."

        if matrix.get("total_expected_slots") is not None and matrix.get("total_expected_slots") != self.total_expected_slots:
            return False, f"total_expected_slots {matrix.get('total_expected_slots')} != {self.total_expected_slots}"

        if matrix.get("total_expected_pairs") is not None and matrix.get("total_expected_pairs") != self.total_expected_pairs:
            return False, f"total_expected_pairs {matrix.get('total_expected_pairs')} != {self.total_expected_pairs}"

        return True, ""

    def verify_artifact_meta(self, meta: Any) -> Tuple[bool, str]:
        """Strictly validates caller-supplied artifact metadata against approved authority (R7-F02)."""
        if not isinstance(meta, dict):
            return False, f"trusted_artifact_meta must be a dict, got {type(meta).__name__}"
        sha = meta.get("sha256")
        if not sha or not isinstance(sha, str) or not is_valid_hex64(sha):
            return False, f"Invalid or missing sha256 in artifact reference: {sha}"
        if sha.lower() != self.raw_artifact_sha256.lower():
            return False, f"Artifact sha256 '{sha}' does not match approved historical artifact sha256 '{self.raw_artifact_sha256}'"
        size = meta.get("size_bytes")
        if size is not None:
            if type(size) is not int or isinstance(size, bool) or size != self.raw_artifact_size_bytes:
                return False, f"Artifact size_bytes {size} does not match approved size {self.raw_artifact_size_bytes}"
        lines = meta.get("line_count")
        if lines is not None:
            if type(lines) is not int or isinstance(lines, bool) or lines != self.raw_artifact_line_count:
                return False, f"Artifact line_count {lines} does not match approved count {self.raw_artifact_line_count}"
        return True, ""


_DEFAULT_AUTHORITY: Optional[OfficialCanonicalAuthority] = None


def get_official_p4a_authority() -> OfficialCanonicalAuthority:
    """Returns singleton instance of OfficialCanonicalAuthority."""
    global _DEFAULT_AUTHORITY
    if _DEFAULT_AUTHORITY is None:
        _DEFAULT_AUTHORITY = OfficialCanonicalAuthority()
    return _DEFAULT_AUTHORITY


def certify_official_p4a(
    ledger_path: Optional[Union[str, Path]] = None,
    workload_manifest_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Official top-level canonical certification entrypoint for Phase P4a (Section XIII.1).
    Anchors certification directly to the immutable approved historical authority.
    """
    authority = get_official_p4a_authority()
    p = Path(ledger_path) if ledger_path is not None else REPO_ROOT / APPROVED_P4A_RAW_ARTIFACT_REL_PATH
    if not p.is_file():
        raise FileNotFoundError(f"Canonical raw ledger not found: {p}")

    from scripts.summarize_rapidflow_apx_p4a import analyze_p4a_ledger
    return analyze_p4a_ledger(
        records=None,
        expected_header=authority.expected_header,
        strict_provenance=True,
        mode="canonical",
        raw_ledger_path=p,
        trusted_artifact_meta=authority.trusted_artifact_meta,
        expected_slot_matrix=authority.expected_slot_matrix,
    )



def validate_record_integrity(
    record: Dict[str, Any],
    expected_header: Optional[Union[str, Dict[str, Any]]] = None,
    workload_registry: Optional[Dict[str, str]] = None,
    strict_provenance: bool = True,
) -> Tuple[bool, str]:
    """
    Validates a single ledger record for structural integrity, field types,
    cryptographic self-consistency, and agreement with the expected experiment configuration.
    Enforces fail-closed rejection on tampered, corrupted, or out-of-bounds metadata.

    strict_provenance defaults to True. All production pipelines enforce strict cryptographic
    provenance without exception. Lightweight synthetic validation is only permitted
    when strict_provenance=False is explicitly specified in isolated test fixtures.
    """
    if not isinstance(record, dict):
        return False, "Record is not a dict"

    if not strict_provenance:
        # Lightweight validation for synthetic unit test fixtures
        synthetic_required = ["execution_identity", "workload_id", "engine", "repetition", "status"]
        for rf in synthetic_required:
            if rf not in record:
                return False, f"Missing synthetic test field: {rf}"
        if record["engine"] not in ("m1", "m2"):
            return False, f"Invalid engine: {record['engine']}"
        if type(record["repetition"]) is not int or isinstance(record["repetition"], bool) or record["repetition"] < 0:
            return False, f"Invalid repetition: {record.get('repetition')}"
        if expected_header is not None:
            if isinstance(expected_header, str) and record["execution_identity"] != expected_header:
                return False, f"Execution identity mismatch: {record['execution_identity']} vs {expected_header}"
        return True, ""

    required_fields = [
        "execution_identity", "protocol_version", "protocol_sha256", "manifest_sha256",
        "source_commit", "source_dirty_state", "binary_sha256",
        "workload_id", "workload_sha256", "engine", "repetition",
        "status", "exit_code", "slot_key", "attempt_id"
    ]
    for rf in required_fields:
        if rf not in record:
            return False, f"Missing required metadata field: {rf}"

    # String & hex type validation
    if not isinstance(record["attempt_id"], str) or not record["attempt_id"]:
        return False, "Invalid attempt_id: must be non-empty string"
    if not is_valid_hex64(record["execution_identity"]):
        return False, "Invalid execution_identity: must be 64-char hex string"
    if not is_valid_hex64(record["protocol_sha256"]):
        return False, "Invalid protocol_sha256: must be 64-char hex string"
    if not is_valid_hex64(record["manifest_sha256"]):
        return False, "Invalid manifest_sha256: must be 64-char hex string"
    if not is_valid_hex64(record["binary_sha256"]):
        return False, "Invalid binary_sha256: must be 64-char hex string"
    if not is_valid_hex64(record["workload_sha256"]):
        return False, "Invalid workload_sha256: must be 64-char hex string"
    if not isinstance(record["source_commit"], str) or not record["source_commit"]:
        return False, "Invalid source_commit: must be non-empty string"
    if record["source_dirty_state"] not in ("CLEAN", "DIRTY", "DIRTY_ALLOWED", "DIRTY_REPORTS_ONLY"):
        return False, f"Invalid source_dirty_state: {record['source_dirty_state']}"
    if record["engine"] not in ("m1", "m2"):
        return False, f"Invalid engine: {record['engine']}"

    # Strict integer type checks (rejecting booleans: bool is subclass of int in Python)
    if type(record["repetition"]) is not int or isinstance(record["repetition"], bool) or record["repetition"] < 0 or record["repetition"] > CANONICAL_REPETITIONS_CEILING:
        return False, f"Invalid repetition: {record.get('repetition')} (must be int in [0, {CANONICAL_REPETITIONS_CEILING}])"
    if type(record["exit_code"]) is not int or isinstance(record["exit_code"], bool):
        return False, "Invalid exit_code: must be int"

    # Cross-field status invariants (R4-F04)
    st = record.get("status")
    ec = record.get("exit_code")
    perf_status = record.get("performance_admission_status")
    m0_agreed = record.get("m0_agreed")
    corr_status = record.get("correctness_status")
    fail_stage = record.get("failure_stage")
    fail_cat = record.get("failure_category")

    if st == "COMPLETED_VERIFIED":
        if ec != 0:
            return False, f"Status COMPLETED_VERIFIED requires exit_code == 0, got {ec}"
        if perf_status != "ACCEPTED":
            return False, f"Status COMPLETED_VERIFIED requires performance_admission_status == 'ACCEPTED', got {perf_status}"
        if m0_agreed is not True:
            return False, f"Status COMPLETED_VERIFIED requires m0_agreed is True, got {m0_agreed}"
        if corr_status != "M0_VERIFIED":
            return False, f"Status COMPLETED_VERIFIED requires correctness_status == 'M0_VERIFIED', got {corr_status}"
        if fail_stage not in (None, "", "NONE"):
            return False, f"Status COMPLETED_VERIFIED requires empty failure_stage, got {fail_stage}"
        if fail_cat not in (None, "", "NONE"):
            return False, f"Status COMPLETED_VERIFIED requires empty failure_category, got {fail_cat}"
    else:
        if perf_status == "ACCEPTED":
            return False, f"Non-verified status {st} cannot have performance_admission_status == 'ACCEPTED'"
        if corr_status == "M0_VERIFIED":
            return False, f"Non-verified status {st} cannot have correctness_status == 'M0_VERIFIED'"

    if ec != 0:
        if st == "COMPLETED_VERIFIED":
            return False, f"Non-zero exit_code {ec} cannot have status COMPLETED_VERIFIED"
        if perf_status == "ACCEPTED":
            return False, f"Non-zero exit_code {ec} cannot have performance_admission_status == 'ACCEPTED'"
        if corr_status == "M0_VERIFIED":
            return False, f"Non-zero exit_code {ec} cannot have correctness_status == 'M0_VERIFIED'"

    if perf_status == "ACCEPTED":
        if st != "COMPLETED_VERIFIED" or ec != 0 or m0_agreed is not True:
            return False, "performance_admission_status 'ACCEPTED' requires COMPLETED_VERIFIED, exit_code 0, and m0_agreed True"

    if m0_agreed is not True and (st == "COMPLETED_VERIFIED" or perf_status == "ACCEPTED"):
        return False, "m0_agreed is not True incompatible with COMPLETED_VERIFIED / ACCEPTED"

    # Effective resource limits
    max_work = record.get("effective_max_work")
    if type(max_work) is not int or isinstance(max_work, bool) or max_work <= 0 or max_work > CANONICAL_MAX_WORK_CAP:
        return False, f"Invalid effective_max_work: {max_work} (approved ceiling={CANONICAL_MAX_WORK_CAP})"

    timeout = record.get("effective_timeout")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or math.isnan(timeout)
        or math.isinf(timeout)
        or timeout <= 0
        or timeout > CANONICAL_TIMEOUT_CEILING
    ):
        return False, f"Invalid effective_timeout: {timeout} (must be finite, >0 and <={CANONICAL_TIMEOUT_CEILING}s)"

    mem_limit = record.get("effective_memory_limit_mib")
    if mem_limit is None:
        mem_limit = record.get("effective_as_limit_mib")
    if type(mem_limit) is not int or isinstance(mem_limit, bool) or mem_limit <= 0 or mem_limit > CANONICAL_AS_LIMIT_MIB:
        return False, f"Invalid effective_memory_limit_mib: {mem_limit} (must be >0 and <={CANONICAL_AS_LIMIT_MIB} MiB)"

    # Slot key check
    expected_slot_key = f"{record['workload_id']}_rep{record['repetition']}_{record['engine']}"
    if record.get("slot_key") != expected_slot_key:
        return False, f"Slot key mismatch: claimed {record.get('slot_key')} vs expected {expected_slot_key}"

    # Self-consistency check: recompute execution_identity from record metadata
    dirty_val = record.get("source_dirty_state") or record.get("source_dirty", "CLEAN")
    derived_id = compute_execution_identity(
        protocol_version=record["protocol_version"],
        protocol_sha256=record["protocol_sha256"],
        manifest_sha256=record["manifest_sha256"],
        source_commit=record["source_commit"],
        source_dirty=dirty_val,
        binary_sha256=record["binary_sha256"],
        effective_max_work=max_work,
        effective_timeout=float(timeout),
        effective_as_limit_mib=mem_limit,
    )
    if derived_id != record["execution_identity"]:
        return False, f"Self-consistency failure: execution_identity {record['execution_identity']} != derived {derived_id}"

    # Resolve workload registry for cross-verification
    registry = workload_registry
    if registry is None and isinstance(expected_header, dict) and "workload_hashes" in expected_header:
        registry = expected_header["workload_hashes"]
    if registry is None and DEFAULT_MANIFEST_PATH.is_file():
        # Fallback to local default manifest if manifest_sha matches
        try:
            cur_reg = get_frozen_manifest_registry(DEFAULT_MANIFEST_PATH)
            if cur_reg["manifest_sha256"] == record["manifest_sha256"]:
                registry = cur_reg["workload_hashes"]
        except Exception:
            pass

    # Check workload identity against registry
    wid = record["workload_id"]
    if registry is not None:
        if wid not in registry:
            return False, f"Workload ID {wid} not found in registered manifest"
        expected_whash = registry[wid]
        if record["workload_sha256"] != expected_whash:
            return False, f"Workload SHA-256 mismatch for {wid}: record has {record['workload_sha256']} vs registered {expected_whash}"

    # Agreement with expected experiment header if provided
    if expected_header is not None:
        if isinstance(expected_header, str):
            if record["execution_identity"] != expected_header:
                return False, f"Execution identity mismatch: record={record['execution_identity']} vs expected={expected_header}"
        elif isinstance(expected_header, dict):
            exp_id = expected_header.get("execution_identity")
            if exp_id and record["execution_identity"] != exp_id:
                return False, f"Execution identity mismatch: record={record['execution_identity']} vs header={exp_id}"
            if expected_header.get("binary_sha256") and record["binary_sha256"] != expected_header["binary_sha256"]:
                return False, f"Binary SHA-256 mismatch: record={record['binary_sha256']} vs header={expected_header['binary_sha256']}"
            if expected_header.get("source_commit") and record["source_commit"] != expected_header["source_commit"]:
                return False, f"Source commit mismatch: record={record['source_commit']} vs header={expected_header['source_commit']}"
            if expected_header.get("protocol_sha256") and record["protocol_sha256"] != expected_header["protocol_sha256"]:
                return False, f"Protocol SHA-256 mismatch: record={record['protocol_sha256']} vs header={expected_header['protocol_sha256']}"
            if expected_header.get("manifest_sha256") and record["manifest_sha256"] != expected_header["manifest_sha256"]:
                return False, f"Manifest SHA-256 mismatch: record={record['manifest_sha256']} vs header={expected_header['manifest_sha256']}"
            if "workload_hashes" in expected_header:
                whashes = expected_header["workload_hashes"]
                if wid not in whashes:
                    return False, f"Workload ID {wid} not present in header workload registry"
                if record["workload_sha256"] != whashes[wid]:
                    return False, f"Workload SHA-256 mismatch for {wid}: record={record['workload_sha256']} vs header={whashes[wid]}"

    return True, ""


def resolve_canonical_attempts(
    records: List[Dict[str, Any]],
    expected_header: Optional[Union[str, Dict[str, Any]]] = None,
    strict_raise: bool = False,
    strict_provenance: bool = True,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    Unified Attempt Resolution Policy for Runner Resume and Summarizer.

    Processes a collection of ledger records:
    1. Validates each record fail-closed using validate_record_integrity.
    2. Groups records by slot_key.
    3. Detects metadata conflicts (workload hash, binary hash, source commit tampering).
    4. Slot-Level Integrity Quarantine (R4-F02): If ANY attempt claiming a slot_key fails record
       integrity, that entire slot is permanently quarantined and excluded from canonical statistics.
    5. Evaluates duplicate attempts:
       - Identical duplicate writes: canonical preserved.
       - Failed attempt + valid verified retry: selects verified retry (preserving failed history).
       - Ambiguous multiple verified attempts with conflicting runtimes: marks AMBIGUOUS_DUPLICATE_SLOT and excludes fail-closed.
    Returns (canonical_slots, duplicate_lineage, rejected_conflicts).
    If strict_raise is True, raises CorruptedLedgerError / AmbiguousSlotError on conflict.
    """
    canonical_slots: Dict[str, Dict[str, Any]] = {}
    duplicate_lineage: Dict[str, List[Dict[str, Any]]] = {}
    rejected_conflicts: List[Dict[str, Any]] = []

    exp_id = (
        expected_header
        if isinstance(expected_header, str)
        else (expected_header.get("execution_identity") if isinstance(expected_header, dict) else None)
    )

    # Step 1: Validate individual records and track quarantined slots
    valid_records_by_slot: Dict[str, List[Dict[str, Any]]] = {}
    quarantined_slots: Dict[str, str] = {}

    for idx, rec in enumerate(records):
        line_no = idx + 1
        rec_id = rec.get("execution_identity") if isinstance(rec, dict) else None
        slot_key = (
            rec.get("slot_key")
            or f"{rec.get('workload_id')}_rep{rec.get('repetition')}_{rec.get('engine')}"
            if isinstance(rec, dict)
            else "UNKNOWN"
        )

        if exp_id and rec_id != exp_id:
            alien_reason = (
                f"Alien experiment record detected at line {line_no} (slot {slot_key}): "
                f"claimed execution_identity {rec_id} != expected {exp_id}"
            )
            conflict_info = {
                "line": line_no,
                "slot_key": slot_key,
                "reason": alien_reason,
                "claimed_execution_identity": rec_id,
                "expected_execution_identity": exp_id,
                "action_taken": "REJECT_AND_CONTAMINATE",
                "record": rec,
            }
            rejected_conflicts.append(conflict_info)
            if slot_key != "UNKNOWN":
                quarantined_slots[slot_key] = alien_reason
            continue

        is_ok, diag = validate_record_integrity(
            rec,
            expected_header=expected_header,
            strict_provenance=strict_provenance,
        )
        if not is_ok:
            conflict_info = {
                "line": line_no,
                "slot_key": slot_key,
                "reason": f"Record integrity failure: {diag}",
                "record": rec,
            }
            rejected_conflicts.append(conflict_info)
            if slot_key != "UNKNOWN":
                quarantined_slots[slot_key] = f"Record integrity failure at line {line_no}: {diag}"
            if strict_raise:
                raise CorruptedLedgerError(f"Corrupted or invalid record at line {line_no}: {diag}")
            continue

        valid_records_by_slot.setdefault(slot_key, []).append(rec)

    # Step 2: Resolve attempts per slot with Slot-Level Quarantine enforcement (R4-F02)
    for slot_key, attempts in valid_records_by_slot.items():
        if slot_key in quarantined_slots:
            reason = f"Slot {slot_key} quarantined: {quarantined_slots[slot_key]}"
            duplicate_lineage[slot_key] = attempts
            rejected_conflicts.append({"slot_key": slot_key, "reason": reason})
            if strict_raise:
                raise CorruptedLedgerError(reason)
            continue

        if len(attempts) == 1:
            rec = attempts[0]
            if (
                rec.get("status") == "COMPLETED_VERIFIED"
                and rec.get("performance_admission_status") == "ACCEPTED"
                and rec.get("m0_agreed") is True
            ):
                canonical_slots[slot_key] = rec
            else:
                # Valid record structure, but not admitted for speedup analysis
                duplicate_lineage[slot_key] = [rec]
            continue

        # Multiple attempts for this slot
        duplicate_lineage[slot_key] = attempts

        # Check metadata consistency across attempts
        first_whash = attempts[0].get("workload_sha256")
        first_bin = attempts[0].get("binary_sha256")
        first_commit = attempts[0].get("source_commit")
        metadata_conflict = False

        for a in attempts[1:]:
            if a.get("workload_sha256") != first_whash:
                reason = f"Slot {slot_key} contains conflicting workload_sha256 across attempts"
                rejected_conflicts.append({"slot_key": slot_key, "reason": reason})
                metadata_conflict = True
                quarantined_slots[slot_key] = reason
                if strict_raise:
                    raise IntegrityConflictError(reason)
                break
            if a.get("binary_sha256") != first_bin or a.get("source_commit") != first_commit:
                reason = f"Slot {slot_key} contains conflicting binary or source commit across attempts"
                rejected_conflicts.append({"slot_key": slot_key, "reason": reason})
                metadata_conflict = True
                quarantined_slots[slot_key] = reason
                if strict_raise:
                    raise IntegrityConflictError(reason)
                break

        if metadata_conflict:
            continue

        # Separate verified from non-verified attempts
        verified_attempts = [
            a for a in attempts
            if a.get("status") == "COMPLETED_VERIFIED"
            and a.get("performance_admission_status") == "ACCEPTED"
            and a.get("m0_agreed") is True
        ]

        if len(verified_attempts) == 0:
            # All attempts were non-verified / failed; slot remains unadmitted
            continue

        if len(verified_attempts) == 1:
            # Case B: Exactly one verified attempt (e.g. earlier attempt failed, retry succeeded)
            canonical_slots[slot_key] = verified_attempts[0]
            continue

        # Case C / Multiple verified attempts
        # Check if they are identical writes
        ref = verified_attempts[0]
        all_identical = True
        for other in verified_attempts[1:]:
            if (
                other.get("attempt_id") != ref.get("attempt_id")
                or other.get("outer_wall_ns") != ref.get("outer_wall_ns")
                or other.get("counters") != ref.get("counters")
                or other.get("profiling") != ref.get("profiling")
            ):
                all_identical = False
                break

        if all_identical:
            # Byte-identical duplicate write: safe to keep one canonical
            canonical_slots[slot_key] = ref
        else:
            # Conflicting verified attempts with different timings / payloads: AMBIGUOUS!
            reason = (
                f"Slot {slot_key} has {len(verified_attempts)} conflicting verified attempts with "
                f"differing runtimes (e.g. {ref.get('outer_wall_ns')} vs {verified_attempts[1].get('outer_wall_ns')}). "
                f"Arbitrary selection disallowed."
            )
            rejected_conflicts.append({"slot_key": slot_key, "reason": reason})
            quarantined_slots[slot_key] = reason
            if strict_raise:
                raise AmbiguousSlotError(reason)
            # Fail-closed: do not admit into canonical_slots!

    return canonical_slots, duplicate_lineage, rejected_conflicts


def validate_pair_compatibility(
    m1: Dict[str, Any],
    m2: Dict[str, Any],
    expected_header: Optional[Union[str, Dict[str, Any]]] = None,
    strict_provenance: bool = True,
) -> Tuple[bool, str]:
    """
    Verifies that M1 and M2 records form a valid, verifiable paired speedup sample:
    - Both pass record integrity.
    - Both share identical execution_identity, binary_sha256, source_commit, manifest_sha256, workload_sha256.
    - Repetitions and workload_ids match.
    - Both are COMPLETED_VERIFIED, ACCEPTED, and m0_agreed == True.
    """
    ok_1, diag_1 = validate_record_integrity(m1, expected_header=expected_header, strict_provenance=strict_provenance)
    if not ok_1:
        return False, f"M1 record integrity failed: {diag_1}"
    ok_2, diag_2 = validate_record_integrity(m2, expected_header=expected_header, strict_provenance=strict_provenance)
    if not ok_2:
        return False, f"M2 record integrity failed: {diag_2}"

    if m1.get("workload_id") != m2.get("workload_id"):
        return False, f"Workload ID mismatch: {m1.get('workload_id')} vs {m2.get('workload_id')}"
    if m1.get("repetition") != m2.get("repetition"):
        return False, f"Repetition mismatch: {m1.get('repetition')} vs {m2.get('repetition')}"
    if m1.get("execution_identity") != m2.get("execution_identity"):
        return False, "Execution identity mismatch between M1 and M2"
    if m1.get("binary_sha256") != m2.get("binary_sha256"):
        return False, "Binary SHA-256 mismatch between M1 and M2"
    if m1.get("source_commit") != m2.get("source_commit"):
        return False, "Source commit mismatch between M1 and M2"
    if m1.get("protocol_sha256") != m2.get("protocol_sha256"):
        return False, "Protocol SHA-256 mismatch between M1 and M2"
    if m1.get("manifest_sha256") != m2.get("manifest_sha256"):
        return False, "Manifest SHA-256 mismatch between M1 and M2"
    if m1.get("workload_sha256") != m2.get("workload_sha256"):
        return False, "Workload SHA-256 mismatch between M1 and M2"

    if m1.get("status") != "COMPLETED_VERIFIED" or m2.get("status") != "COMPLETED_VERIFIED":
        return False, f"Status not COMPLETED_VERIFIED: m1={m1.get('status')}, m2={m2.get('status')}"
    if m1.get("performance_admission_status") != "ACCEPTED" or m2.get("performance_admission_status") != "ACCEPTED":
        return False, f"Admission status not ACCEPTED: m1={m1.get('performance_admission_status')}, m2={m2.get('performance_admission_status')}"
    if m1.get("m0_agreed") is not True or m2.get("m0_agreed") is not True:
        return False, "m0_agreed is not True on both engines"

    return True, ""
