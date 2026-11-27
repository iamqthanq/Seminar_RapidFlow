#!/usr/bin/env python3
"""Programmatic integrity check for P4b-1A acceptance matrix (R11-F07)."""
import re
from pathlib import Path

def main():
    matrix_path = Path(__file__).resolve().parent.parent / "docs" / "rapidflow_apx_p4b_1a_acceptance_matrix.md"
    if not matrix_path.is_file():
        raise FileNotFoundError(f"Matrix not found at {matrix_path}")

    with open(matrix_path, "r", encoding="utf-8") as f:
        text = f.read()

    rows = re.findall(
        r"^\|\s*([A-Za-z0-9_\-]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|",
        text,
        re.MULTILINE
    )

    gate_rows = [
        [c.strip() for c in r]
        for r in rows
        if r[0] not in ("Gate ID", "Gate Category", "Original P4b-1A Matrix", "Hardening Patch R1 Matrix", "Final Closure Patch R1.1 Matrix", "Grand Total")
        and not r[0].startswith("-")
    ]

    orig_ids = [r[0] for r in gate_rows if not r[0].startswith("R1-") and not r[0].startswith("R11-")]
    r1_ids = [r[0] for r in gate_rows if r[0].startswith("R1-")]
    r11_ids = [r[0] for r in gate_rows if r[0].startswith("R11-")]

    print(f"Total gate rows: {len(gate_rows)}")
    print(f"Original IDs: {len(orig_ids)} (unique: {len(set(orig_ids))})")
    print(f"R1 IDs:       {len(r1_ids)} (unique: {len(set(r1_ids))})")
    print(f"R11 IDs:      {len(r11_ids)} (unique: {len(set(r11_ids))})")

    assert len(orig_ids) == 110, f"Expected 110 orig IDs, got {len(orig_ids)}"
    assert len(set(orig_ids)) == 110, "Duplicate orig IDs found"
    assert len(r1_ids) == 78, f"Expected 78 R1 IDs, got {len(r1_ids)}"
    assert len(set(r1_ids)) == 78, "Duplicate R1 IDs found"
    assert len(r11_ids) == 32, f"Expected 32 R11 IDs, got {len(r11_ids)}"
    assert len(set(r11_ids)) == 32, "Duplicate R11 IDs found"

    valid_statuses = {"PASS", "PARTIAL", "BLOCKED", "NOT_APPLICABLE"}
    valid_types = {
        "IMPLEMENTATION_AGENT_EXECUTED",
        "INDEPENDENT_REVIEW_EXECUTED",
        "SOURCE_INSPECTION",
        "HISTORICAL_ACCEPTED_EVIDENCE",
    }

    for r in gate_rows:
        gid, req, ev_type, ev_ref, status, limitation = r
        assert status in valid_statuses, f"Invalid status {status} for {gid}"
        assert ev_type in valid_types, f"Invalid ev_type {ev_type} for {gid}"
        if status == "PASS":
            assert len(ev_ref.strip()) > 0, f"Missing evidence reference for PASS gate {gid}"

    print("SUCCESS: 220 / 220 gates programmatically verified!")

if __name__ == "__main__":
    main()
