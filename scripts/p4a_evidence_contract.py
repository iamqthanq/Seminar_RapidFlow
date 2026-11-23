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
PROTOCOL_ANALYSIS_VERSION = "1.3.0"
PROTOCOL_SPECIFICATION_REVISION = "1.3.0"

# Approved Hard Ceilings
CANONICAL_MAX_WORK_CAP = 500000
CANONICAL_TIMEOUT_CEILING = 60.0
CANONICAL_AS_LIMIT_MIB = 512
CANONICAL_REPETITIONS_CEILING = 10

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "docs" / "rapidflow_apx_p4a_protocol.md"


class CorruptedLedgerError(Exception):
    """Raised when ledger contains unparseable lines, corrupted schemas, or tampered records."""
    pass


class IntegrityConflictError(CorruptedLedgerError):
    """Raised when multiple attempts for a slot have conflicting immutable metadata."""
    pass


class AmbiguousSlotError(CorruptedLedgerError):
    """Raised when multiple verified attempts exist with conflicting runtimes and no deterministic lineage."""
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
        "status", "exit_code", "slot_key"
    ]
    for rf in required_fields:
        if rf not in record:
            return False, f"Missing required metadata field: {rf}"

    # String & hex type validation
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
        if corr_status is not None and corr_status != "M0_VERIFIED":
            return False, f"Status COMPLETED_VERIFIED requires correctness_status == 'M0_VERIFIED', got {corr_status}"
        if fail_stage not in (None, "", "NONE"):
            return False, f"Status COMPLETED_VERIFIED requires empty failure_stage, got {fail_stage}"
        if fail_cat not in (None, "", "NONE"):
            return False, f"Status COMPLETED_VERIFIED requires empty failure_category, got {fail_cat}"
    else:
        if perf_status == "ACCEPTED":
            return False, f"Non-verified status {st} cannot have performance_admission_status == 'ACCEPTED'"

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
            if "binary_sha256" in expected_header and record["binary_sha256"] != expected_header["binary_sha256"]:
                return False, f"Binary SHA-256 mismatch: record={record['binary_sha256']} vs header={expected_header['binary_sha256']}"
            if "source_commit" in expected_header and record["source_commit"] != expected_header["source_commit"]:
                return False, f"Source commit mismatch: record={record['source_commit']} vs header={expected_header['source_commit']}"
            if "protocol_sha256" in expected_header and record["protocol_sha256"] != expected_header["protocol_sha256"]:
                return False, f"Protocol SHA-256 mismatch: record={record['protocol_sha256']} vs header={expected_header['protocol_sha256']}"
            if "manifest_sha256" in expected_header and record["manifest_sha256"] != expected_header["manifest_sha256"]:
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
        if exp_id and rec_id != exp_id:
            # Slot belongs to a different experiment run; skip for this experiment
            continue

        slot_key = (
            rec.get("slot_key")
            or f"{rec.get('workload_id')}_rep{rec.get('repetition')}_{rec.get('engine')}"
            if isinstance(rec, dict)
            else "UNKNOWN"
        )

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
