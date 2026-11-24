"""
scripts/canonical_output_comparator.py
Strict recursive whole-output canonical comparator for RapidFlow-APX (Finding R1-RV01).
Recursively compares all semantic fields across M2 LEGACY, BUFFER, and MERGE outputs,
and between current R1.1 MERGE and preserved starting P4b-2B MERGE outputs.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
import hashlib
import json

from scripts.run_rapidflow_apx_engines import (
    ALL_NEIGHBOR_COUNTER_FIELDS,
    NEIGHBOR_COUNTER_FIELDS,
    NEIGHBOR_MERGE_COUNTER_FIELDS,
)

# Precise allowlist of excluded paths for 3-way parity (LEGACY vs BUFFER vs MERGE)
EXCLUDED_3WAY_PATHS: Set[Tuple[str, ...]] = {
    ("requested_neighbor_mode",),
    ("effective_neighbor_mode",),
}
for f in ALL_NEIGHBOR_COUNTER_FIELDS:
    EXCLUDED_3WAY_PATHS.add(("counters", f))

# Precise allowlist of excluded paths when comparing against starting P4b-2B binary
# Only the schema version changed from 1 to 2; all 7 neighbor counters MUST match!
EXCLUDED_STARTING_P4B2B_PATHS: Set[Tuple[str, ...]] = {
    ("neighbor_configuration_version",),
}


def is_path_excluded(path: Tuple[Any, ...], excluded_set: Set[Tuple[str, ...]], is_3way: bool = True) -> bool:
    if path in excluded_set:
        return True
    if is_3way:
        # Check wildcard step counters: ("steps", ANY_INT, "step_counters", <neighbor_counter>)
        if (
            len(path) == 4
            and path[0] == "steps"
            and isinstance(path[1], int)
            and path[2] == "step_counters"
            and path[3] in ALL_NEIGHBOR_COUNTER_FIELDS
        ):
            return True
    return False


def diff_canonical_structures(
    a: Any,
    b: Any,
    path: Tuple[Any, ...] = (),
    excluded_set: Optional[Set[Tuple[str, ...]]] = None,
    is_3way: bool = True,
) -> List[str]:
    """Recursively diffs two JSON-serializable structures with exact allowlist matching."""
    if excluded_set is None:
        excluded_set = EXCLUDED_3WAY_PATHS

    if is_path_excluded(path, excluded_set, is_3way=is_3way):
        return []

    if type(a) != type(b):
        return [f"{path}: type mismatch ({type(a).__name__} vs {type(b).__name__})"]

    if isinstance(a, dict):
        diffs = []
        all_keys = sorted(set(a.keys()) | set(b.keys()))
        for k in all_keys:
            subpath = path + (k,)
            if is_path_excluded(subpath, excluded_set, is_3way=is_3way):
                continue
            if k not in a:
                diffs.append(f"{subpath}: missing in left")
            elif k not in b:
                diffs.append(f"{subpath}: missing in right")
            else:
                diffs.extend(diff_canonical_structures(a[k], b[k], subpath, excluded_set, is_3way=is_3way))
        return diffs

    elif isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: list length mismatch ({len(a)} vs {len(b)})"]
        diffs = []
        for idx in range(len(a)):
            diffs.extend(diff_canonical_structures(a[idx], b[idx], path + (idx,), excluded_set, is_3way=is_3way))
        return diffs

    else:
        if a != b:
            return [f"{path}: value mismatch ({a!r} != {b!r})"]
        return []


def verify_neighbor_partition_invariants(out: Dict[str, Any], mode: str, is_negative: bool = False) -> List[str]:
    """Strictly checks counter isolation and partition identities for the given mode."""
    issues = []
    is_neg = is_negative or out.get("status") == "FAIL"
    counters = out.get("counters")
    if is_neg and counters is None:
        return []
    counters = counters or {}

    if mode == "legacy":
        for f in ALL_NEIGHBOR_COUNTER_FIELDS:
            val = counters.get(f)
            if val is None or val != 0:
                issues.append(f"LEGACY counters.{f} must be 0, observed {val}")
        for s_idx, step in enumerate(out.get("steps", [])):
            sc = step.get("step_counters", {})
            for f in ALL_NEIGHBOR_COUNTER_FIELDS:
                val = sc.get(f)
                if val is not None and val != 0:
                    issues.append(f"LEGACY step[{s_idx}].step_counters.{f} must be 0, observed {val}")

    elif mode == "buffer":
        for f in NEIGHBOR_MERGE_COUNTER_FIELDS:
            val = counters.get(f)
            if val is None or val != 0:
                issues.append(f"BUFFER counters.{f} must be 0, observed {val}")
        ret = counters.get("neighbor_buffer_retrieval_calls", 0)
        growth = counters.get("neighbor_buffer_growth_calls", 0)
        no_growth = counters.get("neighbor_buffer_no_growth_calls", 0)
        if ret != growth + no_growth:
            issues.append(f"BUFFER partition violated: retrieval ({ret}) != growth ({growth}) + no_growth ({no_growth})")
        for s_idx, step in enumerate(out.get("steps", [])):
            sc = step.get("step_counters", {})
            for f in NEIGHBOR_MERGE_COUNTER_FIELDS:
                val = sc.get(f)
                if val is not None and val != 0:
                    issues.append(f"BUFFER step[{s_idx}].step_counters.{f} must be 0, observed {val}")

    elif mode == "merge":
        ret = counters.get("neighbor_merge_retrieval_calls", 0)
        buf_ret = counters.get("neighbor_buffer_retrieval_calls", 0)
        lin = counters.get("neighbor_merge_linear_calls", 0)
        fast = counters.get("neighbor_merge_fast_path_calls", 0)
        fall = counters.get("neighbor_merge_fallback_calls", 0)

        if ret != buf_ret:
            issues.append(f"MERGE retrieval ({ret}) != buffer retrieval ({buf_ret})")
        if ret != lin + fast + fall:
            issues.append(f"MERGE partition violated: retrieval ({ret}) != linear ({lin}) + fast ({fast}) + fallback ({fall})")
        if not is_neg and fall != 0:
            issues.append(f"MERGE unexpected fallback calls in valid workload: {fall}")

    return issues


def compare_3way_canonical_outputs(
    leg: Dict[str, Any],
    buf: Dict[str, Any],
    mrg: Dict[str, Any],
    is_negative: bool = False,
) -> Tuple[bool, List[str]]:
    """Strictly compares M2 LEGACY, BUFFER, and MERGE outputs. Returns (is_ok, diffs)."""
    is_neg = is_negative or any(x.get("status") == "FAIL" for x in (leg, buf, mrg))
    diffs = []

    # 1. Recursive whole-output diffs
    diff_leg_buf = diff_canonical_structures(leg, buf, path=(), excluded_set=EXCLUDED_3WAY_PATHS, is_3way=True)
    if diff_leg_buf:
        diffs.extend([f"[LEGACY vs BUFFER] {d}" for d in diff_leg_buf])

    diff_buf_mrg = diff_canonical_structures(buf, mrg, path=(), excluded_set=EXCLUDED_3WAY_PATHS, is_3way=True)
    if diff_buf_mrg:
        diffs.extend([f"[BUFFER vs MERGE] {d}" for d in diff_buf_mrg])

    # 2. Partition and isolation invariants
    diffs.extend(verify_neighbor_partition_invariants(leg, "legacy", is_negative=is_neg))
    diffs.extend(verify_neighbor_partition_invariants(buf, "buffer", is_negative=is_neg))
    diffs.extend(verify_neighbor_partition_invariants(mrg, "merge", is_negative=is_neg))

    # 3. Mode cross-check: merge retrieval must equal buffer retrieval
    c_mrg = mrg.get("counters") or {}
    c_buf = buf.get("counters") or {}
    if not is_neg or (c_mrg and c_buf):
        if c_mrg.get("neighbor_merge_retrieval_calls") != c_buf.get("neighbor_buffer_retrieval_calls"):
            diffs.append(
                f"Retrieval cross-mode mismatch: MERGE={c_mrg.get('neighbor_merge_retrieval_calls')} "
                f"vs BUFFER={c_buf.get('neighbor_buffer_retrieval_calls')}"
            )

    return (len(diffs) == 0, diffs)


def compare_against_starting_p4b2b(
    curr_mrg: Dict[str, Any],
    starting_mrg: Dict[str, Any],
    is_negative: bool = False,
) -> Tuple[bool, List[str]]:
    """Strictly compares current R1.1 MERGE output against starting P4b-2B MERGE output."""
    is_neg = is_negative or any(x.get("status") == "FAIL" for x in (curr_mrg, starting_mrg))
    diffs = diff_canonical_structures(
        starting_mrg,
        curr_mrg,
        path=(),
        excluded_set=EXCLUDED_STARTING_P4B2B_PATHS,
        is_3way=False,
    )
    # Check that all 7 neighbor counters match exactly between starting and current
    c_start = starting_mrg.get("counters") or {}
    c_curr = curr_mrg.get("counters") or {}
    if not is_neg or (c_start and c_curr):
        for f in ALL_NEIGHBOR_COUNTER_FIELDS:
            if c_start.get(f) != c_curr.get(f):
                diffs.append(f"Neighbor counter {f} mismatch with starting P4b-2B: start={c_start.get(f)} vs curr={c_curr.get(f)}")

    return (len(diffs) == 0, diffs)


def compute_raw_input_sha256(raw_bytes: bytes) -> str:
    """Computes SHA-256 directly from raw workload bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()
