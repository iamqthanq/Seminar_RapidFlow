#!/usr/bin/env python3
"""
scripts/generate_dstar02_report.py — Truthful Evidence and Report Generator for DSTAR-02.

Executes all DSTAR-02 correctness suites and outputs structured report:
1. Production code independence audit (zero imports of oracle/legacy in engines).
2. 23 hand and adversarial correctness cases.
3. 840 reference corpus cases (420 seed1 + 420 seed7; 8002 truth event tuples).
4. 56-stream stateful matrix: 56 configurations x 12 updates = 672 steps with M0 agreement
   and active-set reconstruction.
5. Invariant violation fail-closed guard checks.
6. Resource limit fail-closed guard checks.
7. Manifest reconciliation with configs/dstar02_correctness_cases.json.

Outputs:
- Configurable output path via --output PATH (default: reports/dstar02/correctness_summary.json).
- Exits 0 only if ALL required cases, corpus checks, and matrix conditions PASS.
- Exits nonzero on any FAIL/ERROR/INCOMPLETE.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import itertools
import json
import os
import platform
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Production event engines
from dstar_event_common import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DEFAULT_MAX_WORK,
    DStarEventError,
    DStarEventGraph,
    DStarEventUpdate,
    DuplicateEdgeInsertionError,
    EdgeLabelMismatchDeletionError,
    EventCounters,
    EventResult,
    EventSink,
    InvalidGraphError,
    InvalidThresholdError,
    InvalidUpdateError,
    InvariantViolationError,
    NonexistentEdgeDeletionError,
    ResourceLimitError,
    SelfLoopError,
    UnknownVertexError,
    canonical_ordered_tuple,
    compute_edge_connectivity,
    is_connected,
    validate_resource_caps,
)
from dstar_m1_enum import M1DynamicEngine, M1EnumEngine
from dstar_m2_event import M2DynamicEngine, M2EventEngine

# Test-side M0 Ground Truth Oracle
import dstar_oracle as M0


# ============================================================================
# Provenance Helpers
# ============================================================================

def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def collect_git_provenance() -> Dict[str, Any]:
    """Collects Git provenance metadata."""
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception as e:
        head_sha = f"UNKNOWN ({e})"

    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip() or "DETACHED_HEAD"
    except Exception as e:
        branch = f"UNKNOWN ({e})"

    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
        is_dirty = bool(status_out)
    except Exception as e:
        is_dirty = f"UNKNOWN ({e})"

    try:
        origin_qt_sha = subprocess.check_output(
            ["git", "rev-parse", "origin/QuangThang"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception as e:
        origin_qt_sha = f"UNAVAILABLE ({e})"

    try:
        merge_base = subprocess.check_output(
            ["git", "merge-base", "origin/QuangThang", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception as e:
        merge_base = f"UNAVAILABLE ({e})"

    return {
        "configured_historical_base_sha": "e6cc1dba15955ce9f7f05ec0ca2a1d749181218c",
        "observed_origin_quangthang_sha": origin_qt_sha,
        "observed_merge_base_sha": merge_base,
        "execution_head_sha": head_sha,
        "is_dirty_at_runtime": is_dirty,
        "branch": branch,
    }


# ============================================================================
# Stage 1: Independence Audit
# ============================================================================

def run_independence_audit() -> Tuple[Dict[str, Any], bool]:
    """Verifies that production files have zero imports of oracle or legacy modules."""
    production_files = [
        REPO_ROOT / "scripts" / "dstar_event_common.py",
        REPO_ROOT / "scripts" / "dstar_m1_enum.py",
        REPO_ROOT / "scripts" / "dstar_m2_event.py",
        REPO_ROOT / "scripts" / "dstar_event_cli.py",
    ]
    forbidden_modules = {
        "dstar_oracle",
        "apx02_oracle",
        "apx01_semantics",
        "test_dstar_oracle",
        "test_dstar_legacy_compatibility",
    }

    audit_records: List[Dict[str, Any]] = []
    all_clean = True

    for p_file in production_files:
        if not p_file.is_file():
            audit_records.append({
                "file": str(p_file.relative_to(REPO_ROOT)),
                "status": "FAIL",
                "error": "File does not exist",
            })
            all_clean = False
            continue

        with open(p_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(p_file))

        file_violations: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".")[0]
                    if base in forbidden_modules:
                        file_violations.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base = node.module.split(".")[0]
                    if base in forbidden_modules:
                        file_violations.append(f"from {node.module} import ...")

        ok = len(file_violations) == 0
        if not ok:
            all_clean = False
        audit_records.append({
            "file": str(p_file.relative_to(REPO_ROOT)),
            "status": "PASS" if ok else "FAIL",
            "forbidden_imports": file_violations,
        })

    summary = {
        "status": "PASS" if all_clean else "FAIL",
        "files_audited": len(production_files),
        "audit_records": audit_records,
    }
    return summary, all_clean


# ============================================================================
# Stage 2: Hand and Adversarial Cases
# ============================================================================

def run_hand_cases() -> Tuple[List[Dict[str, Any]], bool]:
    """Executes the 23 hand and adversarial test cases."""
    m1 = M1EnumEngine()
    m2 = M2EventEngine()
    cases: List[Dict[str, Any]] = []
    all_ok = True

    def check_case(
        cid: str,
        name: str,
        q: DStarEventGraph,
        g_before: DStarEventGraph,
        upd: DStarEventUpdate,
        k: int,
    ) -> bool:
        nonlocal all_ok
        try:
            # Run M0
            q_m0 = M0.DStarGraph.from_dict(q.to_dict())
            g_m0 = M0.DStarGraph.from_dict(g_before.to_dict())
            m0_oracle = M0.DStarDynamicOracle(q_m0, g_m0, k=k)
            upd_m0 = M0.DStarUpdate(op=upd.operation, u=upd.edge[0], v=upd.edge[1], label=upd.label)
            delta_m0 = m0_oracle.apply_update(upd_m0)
            m0_ent = sorted(delta_m0.entered)
            m0_ext = sorted(delta_m0.exited)

            # Run M1
            res1, _ = m1.process_update(q, g_before, upd, k)
            m1_ent = sorted(res1.entered)
            m1_ext = sorted(res1.exited)

            # Run M2
            res2, _ = m2.process_update(q, g_before, upd, k)
            m2_ent = sorted(res2.entered)
            m2_ext = sorted(res2.exited)

            agreed = (m1_ent == m0_ent == m2_ent) and (m1_ext == m0_ext == m2_ext)
            cases.append({
                "case_id": cid,
                "name": name,
                "status": "PASS" if agreed else "FAIL",
                "k": k,
                "m0_entered": len(m0_ent),
                "m0_exited": len(m0_ext),
                "m1_entered": len(m1_ent),
                "m1_exited": len(m1_ext),
                "m2_entered": len(m2_ent),
                "m2_exited": len(m2_ext),
                "agreed": agreed,
            })
            if not agreed:
                all_ok = False
            return agreed
        except Exception as e:
            cases.append({"case_id": cid, "name": name, "status": "ERROR", "error": str(e)})
            all_ok = False
            return False

    # Case 01: Diamond distinct labels
    Q1 = DStarEventGraph()
    for v in (1, 2, 3, 4): Q1.add_vertex(v, 0)
    Q1.add_edge(1, 2, "e12"); Q1.add_edge(1, 3, "e13"); Q1.add_edge(2, 3, "e23")
    Q1.add_edge(2, 4, "e24"); Q1.add_edge(3, 4, "e34")
    G1 = DStarEventGraph()
    for v in (10, 20, 30, 40): G1.add_vertex(v, 0)
    G1.add_edge(10, 20, "e12"); G1.add_edge(10, 30, "e13")
    G1.add_edge(20, 40, "e24"); G1.add_edge(30, 40, "e34")
    check_case("CASE_HAND_01", "diamond_distinct_labels", Q1, G1, DStarEventUpdate("INSERT", (20, 30), "e23"), k=0)

    # Case 02: Diamond k=0 insert
    Q2 = DStarEventGraph()
    for v in (1, 2, 3, 4): Q2.add_vertex(v, 0)
    for (u, v) in [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]: Q2.add_edge(u, v, 0)
    G2 = DStarEventGraph()
    for v in (1, 2, 3, 4): G2.add_vertex(v, 0)
    for (u, v) in [(1, 2), (1, 3), (2, 4), (3, 4)]: G2.add_edge(u, v, 0)
    check_case("CASE_HAND_02", "diamond_k0_insert", Q2, G2, DStarEventUpdate("INSERT", (2, 3), 0), k=0)

    # Case 03: Diamond k=0 delete
    G3 = G2.clone(); G3.add_edge(2, 3, 0)
    check_case("CASE_HAND_03", "diamond_k0_delete", Q2, G3, DStarEventUpdate("DELETE", (2, 3), 0), k=0)

    # Case 04: K4 k=1
    Q4 = DStarEventGraph()
    for v in (1, 2, 3, 4): Q4.add_vertex(v, 0)
    for (u, v) in itertools.combinations((1, 2, 3, 4), 2): Q4.add_edge(u, v, 0)
    G4 = DStarEventGraph()
    for v in (1, 2, 3, 4): G4.add_vertex(v, 0)
    for (u, v) in [(1, 2), (1, 3), (2, 4), (3, 4)]: G4.add_edge(u, v, 0)
    check_case("CASE_HAND_04", "k4_k1", Q4, G4, DStarEventUpdate("INSERT", (1, 4), 0), k=1)

    # Case 05: K4 k=2
    G5 = DStarEventGraph()
    for v in (1, 2, 3, 4): G5.add_vertex(v, 0)
    G5.add_edge(1, 2, 0); G5.add_edge(2, 3, 0); G5.add_edge(3, 4, 0)
    check_case("CASE_HAND_05", "k4_k2", Q4, G5, DStarEventUpdate("INSERT", (1, 4), 0), k=2)

    # Case 06: Diamond 2-step delete
    G6 = Q2.clone()
    upd6_1 = DStarEventUpdate("DELETE", (2, 3), 0)
    check_case("CASE_HAND_06_STEP1", "diamond_2step_delete_step1", Q2, G6, upd6_1, k=1)
    _, G6_after1 = upd6_1.apply_transactional(G6)
    upd6_2 = DStarEventUpdate("DELETE", (1, 2), 0)
    check_case("CASE_HAND_06_STEP2", "diamond_2step_delete_step2", Q2, G6_after1, upd6_2, k=1)

    # Case 07: No matching label
    Q7 = DStarEventGraph()
    for v in (1, 2, 3): Q7.add_vertex(v, 0)
    Q7.add_edge(1, 2, 0); Q7.add_edge(2, 3, 0); Q7.add_edge(3, 1, 0)
    G7 = DStarEventGraph()
    for v in (10, 20, 30): G7.add_vertex(v, 0)
    G7.add_edge(10, 20, 0); G7.add_edge(20, 30, 0)
    check_case("CASE_HAND_07", "no_matching_label", Q7, G7, DStarEventUpdate("INSERT", (10, 30), "WRONG"), k=0)

    # Case 08: Non-induced extra chords
    Q8 = DStarEventGraph()
    for v in (1, 2, 3, 4): Q8.add_vertex(v, 0)
    for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]: Q8.add_edge(u, v, 0)
    G8 = DStarEventGraph()
    for v in (10, 20, 30, 40): G8.add_vertex(v, 0)
    G8.add_edge(10, 20, 0); G8.add_edge(20, 30, 0); G8.add_edge(30, 40, 0); G8.add_edge(10, 30, 0)
    check_case("CASE_HAND_08", "non_induced_extra_chords", Q8, G8, DStarEventUpdate("INSERT", (40, 10), 0), k=0)

    # Case 09: Edge label mismatch
    Q9 = DStarEventGraph()
    for v in (1, 2, 3): Q9.add_vertex(v, 0)
    Q9.add_edge(1, 2, "A"); Q9.add_edge(2, 3, "A"); Q9.add_edge(3, 1, "A")
    G9 = DStarEventGraph()
    for v in (10, 20, 30): G9.add_vertex(v, 0)
    G9.add_edge(10, 20, "A"); G9.add_edge(20, 30, "B")
    check_case("CASE_HAND_09", "edge_label_mismatch", Q9, G9, DStarEventUpdate("INSERT", (10, 30), "A"), k=1)

    # Case 10: Vertex label mismatch
    Q10 = DStarEventGraph(); Q10.add_vertex(1, "TYPE_A"); Q10.add_vertex(2, "TYPE_B"); Q10.add_edge(1, 2, 0)
    G10 = DStarEventGraph(); G10.add_vertex(10, "TYPE_A"); G10.add_vertex(20, "TYPE_C")
    check_case("CASE_HAND_10", "vertex_label_mismatch", Q10, G10, DStarEventUpdate("INSERT", (10, 20), 0), k=0)

    # Case 11: Int vs str labels
    Q11 = DStarEventGraph(); Q11.add_vertex(1, 0); Q11.add_vertex(2, 0); Q11.add_edge(1, 2, 0)
    G11 = DStarEventGraph(); G11.add_vertex(10, "0"); G11.add_vertex(20, "0")
    check_case("CASE_HAND_11", "int_vs_str_labels", Q11, G11, DStarEventUpdate("INSERT", (10, 20), 0), k=0)

    # Case 12: Sparse negative IDs
    Q12 = DStarEventGraph()
    for v in (-10, 5, 42): Q12.add_vertex(v, 0)
    Q12.add_edge(-10, 5, 0); Q12.add_edge(5, 42, 0); Q12.add_edge(-10, 42, 0)
    G12 = DStarEventGraph()
    for v in (-100, 0, 100): G12.add_vertex(v, 0)
    G12.add_edge(-100, 0, 0); G12.add_edge(0, 100, 0)
    check_case("CASE_HAND_12", "sparse_negative_ids", Q12, G12, DStarEventUpdate("INSERT", (-100, 100), 0), k=0)

    # Case 13: Canonical tuple order
    Q13 = DStarEventGraph()
    for v in (3, 1, 4, 2): Q13.add_vertex(v, 0)
    for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]: Q13.add_edge(u, v, 0)
    G13 = DStarEventGraph()
    for v in (10, 20, 30, 40): G13.add_vertex(v, 0)
    G13.add_edge(10, 20, 0); G13.add_edge(20, 30, 0); G13.add_edge(30, 40, 0)
    check_case("CASE_HAND_13", "canonical_tuple_order", Q13, G13, DStarEventUpdate("INSERT", (40, 10), 0), k=0)

    # Case 14: Uniform K4 k=0 automorphisms
    G14 = Q4.clone(); G14.remove_edge(1, 2, 0)
    check_case("CASE_HAND_14", "uniform_k4_k0_automorphisms", Q4, G14, DStarEventUpdate("INSERT", (1, 2), 0), k=0)

    # Case 15: C4 k=1 deferred witness
    Q15 = Q8.clone()
    G15 = DStarEventGraph()
    for v in (10, 20, 30, 40): G15.add_vertex(v, 0)
    G15.add_edge(10, 20, 0); G15.add_edge(20, 30, 0)
    check_case("CASE_HAND_15", "c4_k1_deferred_witness", Q15, G15, DStarEventUpdate("INSERT", (30, 40), 0), k=1)

    # Case 16: Missing commitment violation
    G16 = Q2.clone(); G16.remove_edge(1, 2, 0)
    check_case("CASE_HAND_16", "missing_commitment_violation", Q2, G16, DStarEventUpdate("INSERT", (1, 2), 0), k=1)

    # Case 17: No double charging
    check_case("CASE_HAND_17", "no_double_charging", Q7, G7, DStarEventUpdate("INSERT", (10, 30), 0), k=0)

    # Case 18: Rollback isolation
    G18 = G8.clone(); G18.add_vertex(50, 0); G18.add_edge(40, 50, 0)
    check_case("CASE_HAND_18", "rollback_isolation", Q8, G18, DStarEventUpdate("INSERT", (40, 10), 0), k=0)

    # Case 19: Reversed endpoints
    up19_1 = DStarEventUpdate("INSERT", (2, 3), 0)
    up19_2 = DStarEventUpdate("INSERT", (3, 2), 0)
    ok19 = (up19_1.edge == (2, 3) and up19_2.edge == (2, 3))
    cases.append({"case_id": "CASE_HAND_19", "name": "reversed_endpoints", "status": "PASS" if ok19 else "FAIL"})
    if not ok19: all_ok = False

    # Case 20: Caller mutation isolation
    res20, _ = m1.process_update(Q7, G7, DStarEventUpdate("INSERT", (10, 30), 0), k=0)
    res20.entered.append((999, 999, 999))
    ok20 = (10, 30) not in G7.edges
    cases.append({"case_id": "CASE_HAND_20", "name": "caller_mutation_isolation", "status": "PASS" if ok20 else "FAIL"})
    if not ok20: all_ok = False

    # Case 21: Invalid k domain
    Q21 = DStarEventGraph()
    for v in (1, 2, 3, 4): Q21.add_vertex(v, 0)
    Q21.add_edge(1, 2, 0); Q21.add_edge(2, 3, 0); Q21.add_edge(3, 4, 0)
    rejected_m1 = False; rejected_m2 = False
    try: m1.find_events(Q21, G7, DStarEventUpdate("INSERT", (10, 30), 0), k=1)
    except InvalidThresholdError: rejected_m1 = True
    try: m2.find_events(Q21, G7, DStarEventUpdate("INSERT", (10, 30), 0), k=1)
    except InvalidThresholdError: rejected_m2 = True
    ok21 = rejected_m1 and rejected_m2
    cases.append({"case_id": "CASE_HAND_21", "name": "invalid_k_domain", "status": "PASS" if ok21 else "FAIL"})
    if not ok21: all_ok = False

    # Case 22: Invalid update rollback
    G22 = DStarEventGraph(); G22.add_vertex(1, 0); G22.add_vertex(2, 0); G22.add_edge(1, 2, 0)
    ok22 = True
    try: DStarEventUpdate("INSERT", (1, 1), 0)
    except SelfLoopError: pass
    else: ok22 = False
    cases.append({"case_id": "CASE_HAND_22", "name": "invalid_update_rollback", "status": "PASS" if ok22 else "FAIL"})
    if not ok22: all_ok = False

    # Case 23: Query sizes 5 and 6
    Q23 = DStarEventGraph()
    for v in range(1, 6): Q23.add_vertex(v, 0)
    for (u, v) in itertools.combinations(range(1, 6), 2):
        if (u, v) != (1, 2): Q23.add_edge(u, v, 0)
    G23 = DStarEventGraph()
    for v in range(1, 6): G23.add_vertex(v, 0)
    for (u, v) in itertools.combinations(range(1, 6), 2):
        if (u, v) not in ((1, 2), (2, 3)): G23.add_edge(u, v, 0)
    check_case("CASE_HAND_23", "query_sizes_5_and_6", Q23, G23, DStarEventUpdate("INSERT", (2, 3), 0), k=1)

    return cases, all_ok


# ============================================================================
# Stage 3: Corpus 840 Reference Verification
# ============================================================================

def run_corpus_840_reference() -> Tuple[Dict[str, Any], bool]:
    """Executes the full 840 reference cases against M0 truth."""
    corpus_dir = REPO_ROOT / "tests" / "fixtures" / "dstar02" / "corpus_840"
    files = [corpus_dir / "seed1_cases.jsonl", corpus_dir / "seed7_cases.jsonl"]

    m1_engine = M1EnumEngine()
    m2_engine = M2EventEngine()

    total_cases = 0
    total_events = 0
    cases_with_events = 0
    cases_with_events_k_ge_1 = 0
    m1_agreed = 0
    m2_agreed = 0
    m1_zero_duplicates = 0
    m2_zero_duplicates = 0
    total_m2_states = 0

    for fpath in files:
        if not fpath.is_file():
            return {
                "status": "FAIL",
                "error": f"Missing corpus file: {fpath}",
            }, False

        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                case_data = json.loads(line)
                total_cases += 1

                # Parse query
                Q = DStarEventGraph()
                qvl = case_data["query_vlabels"]
                for v_idx, lbl in enumerate(qvl):
                    Q.add_vertex(v_idx, lbl)
                q_edges = case_data["query_edges"]
                qel = case_data["query_elabels"]
                for e_idx, (u, v) in enumerate(q_edges):
                    Q.add_edge(u, v, qel[e_idx])

                cfg_str = case_data["config"]
                k_str = cfg_str.split("k=")[1].split()[0]
                k = int(k_str)

                # Parse data graph
                G_before = DStarEventGraph()
                gvl = case_data["data_vlabels"]
                for v_idx, lbl in enumerate(gvl):
                    G_before.add_vertex(v_idx, lbl)
                for item in case_data["G_before"]:
                    G_before.add_edge(item[0], item[1], item[2])

                upd_raw = case_data["update"]
                upd = DStarEventUpdate(
                    operation=upd_raw["kind"],
                    edge=tuple(upd_raw["edge"]),
                    label=upd_raw["label"],
                )

                truth_events = [tuple(t) for t in case_data["oracle_events"]]
                if truth_events:
                    cases_with_events += 1
                    total_events += len(truth_events)
                    if k >= 1:
                        cases_with_events_k_ge_1 += 1

                # Process M1
                res1, _ = m1_engine.process_update(Q, G_before, upd, k)
                act1 = res1.entered if upd.operation == "INSERT" else res1.exited
                if act1 == truth_events:
                    m1_agreed += 1
                if len(act1) == len(set(act1)):
                    m1_zero_duplicates += 1

                # Process M2
                res2, _ = m2_engine.process_update(Q, G_before, upd, k)
                act2 = res2.entered if upd.operation == "INSERT" else res2.exited
                if act2 == truth_events:
                    m2_agreed += 1
                if len(act2) == len(set(act2)):
                    m2_zero_duplicates += 1

                total_m2_states += (res2.counters.candidate_extensions + res2.counters.missing_branches)

    all_ok = (
        total_cases == 840
        and total_events == 8002
        and cases_with_events == 330
        and cases_with_events_k_ge_1 == 239
        and m1_agreed == 840
        and m2_agreed == 840
        and m1_zero_duplicates == 840
        and m2_zero_duplicates == 840
    )

    summary = {
        "status": "PASS" if all_ok else "FAIL",
        "total_cases": total_cases,
        "total_truth_events": total_events,
        "cases_with_events": cases_with_events,
        "cases_with_events_k_ge_1": cases_with_events_k_ge_1,
        "m1_matching_truth": m1_agreed,
        "m2_matching_truth": m2_agreed,
        "m1_zero_duplicates_cases": m1_zero_duplicates,
        "m2_zero_duplicates_cases": m2_zero_duplicates,
        "total_m2_search_states": total_m2_states,
    }
    return summary, all_ok


# ============================================================================
# Stage 4: 56-Stream Stateful Matrix
# ============================================================================

def build_query_by_name(name: str, label_mode: str) -> DStarEventGraph:
    g = DStarEventGraph()
    if name == "C4":
        for v in range(4): g.add_vertex(v, 0 if label_mode == "uniform" else v % 2)
        for (u, v) in [(0, 1), (1, 2), (2, 3), (3, 0)]: g.add_edge(u, v, 0 if label_mode == "uniform" else (u + v) % 2)
    elif name == "Diamond":
        for v in range(4): g.add_vertex(v, 0 if label_mode == "uniform" else v % 2)
        for (u, v) in [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]: g.add_edge(u, v, 0 if label_mode == "uniform" else (u + v) % 2)
    elif name == "K4":
        for v in range(4): g.add_vertex(v, 0 if label_mode == "uniform" else v % 2)
        for (u, v) in itertools.combinations(range(4), 2): g.add_edge(u, v, 0 if label_mode == "uniform" else (u + v) % 2)
    return g


def generate_random_stream_instance(
    seed: int,
    n_vertices: int,
    n_initial_edges: int,
    n_updates: int,
    label_mode: str,
) -> Tuple[DStarEventGraph, List[DStarEventUpdate]]:
    rng = random.Random(seed)
    g = DStarEventGraph()
    n_vlabels = 1 if label_mode == "uniform" else 2
    n_elabels = 1 if label_mode == "uniform" else 2

    for v in range(n_vertices):
        g.add_vertex(v, rng.randrange(n_vlabels))

    all_pairs = list(itertools.combinations(range(n_vertices), 2))
    initial_pairs = rng.sample(all_pairs, min(n_initial_edges, len(all_pairs)))
    for (u, v) in initial_pairs:
        g.add_edge(u, v, rng.randrange(n_elabels))

    curr_edges = dict(g.edges)
    updates: List[DStarEventUpdate] = []

    for _ in range(n_updates):
        can_insert = len(curr_edges) < len(all_pairs)
        can_delete = len(curr_edges) > 1

        op = "INSERT" if (can_insert and (not can_delete or rng.random() < 0.5)) else "DELETE"
        if op == "INSERT":
            available = [p for p in all_pairs if p not in curr_edges]
            pair = rng.choice(available)
            lbl = rng.randrange(n_elabels)
            updates.append(DStarEventUpdate("INSERT", pair, lbl))
            curr_edges[pair] = lbl
        else:
            pair = rng.choice(list(curr_edges.keys()))
            lbl = curr_edges[pair]
            updates.append(DStarEventUpdate("DELETE", pair, lbl))
            del curr_edges[pair]

    return g, updates


def run_56_stream_matrix() -> Tuple[Dict[str, Any], bool]:
    """Executes the 56-stream stateful matrix (672 updates)."""
    seed_base = 20261117
    matrix_specs = [
        ("C4", [0, 1], 8),
        ("Diamond", [0, 1], 8),
        ("K4", [0, 1, 2], 8),
    ]

    total_configs = 0
    total_steps = 0
    agreed_steps = 0
    reconstruction_agreed_steps = 0
    total_entered_tuples = 0
    total_exited_tuples = 0
    configs_summary: List[Dict[str, Any]] = []

    for q_name, k_values, seeds_count in matrix_specs:
        for k in k_values:
            for s_idx in range(seeds_count):
                total_configs += 1
                config_id = f"CONFIG_{total_configs:02d}_{q_name}_k{k}_s{s_idx}"
                seed = seed_base + total_configs * 37 + s_idx
                label_mode = "uniform" if (total_configs % 2 == 0) else "mixed"

                Q = build_query_by_name(q_name, label_mode)
                G0, updates = generate_random_stream_instance(
                    seed=seed,
                    n_vertices=6,
                    n_initial_edges=6,
                    n_updates=12,
                    label_mode=label_mode,
                )

                m1_dyn = M1DynamicEngine(Q, G0, k)
                m2_dyn = M2DynamicEngine(Q, G0, k)

                q_m0 = M0.DStarGraph.from_dict(Q.to_dict())
                g_m0 = M0.DStarGraph.from_dict(G0.to_dict())
                m0_dyn = M0.DStarDynamicOracle(q_m0, g_m0, k=k)

                active_set: Set[Tuple[int, ...]] = set(m0_dyn.current_matches)
                cfg_steps_agreed = 0

                for up in updates:
                    total_steps += 1
                    res1 = m1_dyn.step(up)
                    res2 = m2_dyn.step(up)

                    up_m0 = M0.DStarUpdate(op=up.operation, u=up.edge[0], v=up.edge[1], label=up.label)
                    delta_m0 = m0_dyn.apply_update(up_m0)

                    m0_ent = sorted(delta_m0.entered)
                    m0_ext = sorted(delta_m0.exited)

                    total_entered_tuples += len(m0_ent)
                    total_exited_tuples += len(m0_ext)

                    step_ok = (res1.entered == m0_ent == res2.entered) and (res1.exited == m0_ext == res2.exited)
                    if step_ok:
                        agreed_steps += 1
                        cfg_steps_agreed += 1

                    active_set = (active_set - set(res2.exited)) | set(res2.entered)
                    recon_ok = (active_set == set(m0_dyn.current_matches))
                    if recon_ok:
                        reconstruction_agreed_steps += 1

                configs_summary.append({
                    "config_id": config_id,
                    "query": q_name,
                    "k": k,
                    "seed": seed,
                    "steps_agreed": cfg_steps_agreed,
                    "status": "PASS" if cfg_steps_agreed == 12 else "FAIL",
                })

    all_ok = (
        total_configs == 56
        and total_steps == 672
        and agreed_steps == 672
        and reconstruction_agreed_steps == 672
    )

    summary = {
        "status": "PASS" if all_ok else "FAIL",
        "total_configurations": total_configs,
        "total_updates": total_steps,
        "step_agreement_count": agreed_steps,
        "reconstruction_agreement_count": reconstruction_agreed_steps,
        "total_entered_tuples": total_entered_tuples,
        "total_exited_tuples": total_exited_tuples,
        "configs": configs_summary,
    }
    return summary, all_ok


# ============================================================================
# Stage 5: Invariant Violation Guard
# ============================================================================

def run_invariant_violation_check() -> Tuple[Dict[str, Any], bool]:
    """Tests that internal invariant violations (empty frontier) fail closed with InvariantViolationError."""
    Q = DStarEventGraph()
    for v in range(4): Q.add_vertex(v, 0)
    for (u, v) in [(0, 1), (1, 2), (2, 3), (3, 0)]: Q.add_edge(u, v, 0)

    m2 = M2EventEngine()
    rng = random.Random(5)
    found_invariant_violation = False

    for _ in range(50):
        G_test = DStarEventGraph()
        for v in range(6): G_test.add_vertex(v, 0)
        for (u, v) in itertools.combinations(range(6), 2):
            if rng.random() < 0.3: G_test.add_edge(u, v, 0)
        if not G_test.has_edge(0, 1):
            G_test.add_edge(0, 1, 0)
        test_upd = DStarEventUpdate("INSERT", (0, 1), 0)
        try:
            m2.find_events(Q, G_test, test_upd, k=2, _skip_domain_check=True)
        except InvariantViolationError:
            found_invariant_violation = True
            break

    summary = {
        "status": "PASS" if found_invariant_violation else "FAIL",
        "invariant_tested": "EMPTY_FRONTIER_INVARIANT",
        "raised_invariant_violation_error": found_invariant_violation,
    }
    return summary, found_invariant_violation


# ============================================================================
# Stage 6: Resource Guards
# ============================================================================

def run_resource_guard_checks() -> Tuple[List[Dict[str, Any]], bool]:
    """Tests resource limit fail-closed enforcement."""
    cases: List[Dict[str, Any]] = []
    all_ok = True

    # 1. max_work check
    Q = DStarEventGraph()
    for v in (1, 2, 3, 4): Q.add_vertex(v, 0)
    for (u, v) in [(1, 2), (2, 3), (3, 4), (4, 1)]: Q.add_edge(u, v, 0)
    G = DStarEventGraph()
    for v in range(6): G.add_vertex(v, 0)
    for (u, v) in itertools.combinations(range(6), 2): G.add_edge(u, v, 0)
    upd = DStarEventUpdate("INSERT", (0, 1), 0)

    limits = {"max_work": 2}
    m1 = M1EnumEngine(limits)
    m2 = M2EventEngine(limits)

    m1_caught = False; m2_caught = False
    try: m1.find_events(Q, G, upd, k=1, limits=limits)
    except ResourceLimitError: m1_caught = True
    try: m2.find_events(Q, G, upd, k=1, limits=limits)
    except ResourceLimitError: m2_caught = True

    ok_work = m1_caught and m2_caught
    cases.append({"check": "MAX_WORK_EXCEEDED", "status": "PASS" if ok_work else "FAIL"})
    if not ok_work: all_ok = False

    # 2. vertex caps check
    Q_big = DStarEventGraph()
    for v in range(10): Q_big.add_vertex(v, 0)
    for v in range(9): Q_big.add_edge(v, v + 1, 0)
    Q_big.add_edge(9, 0, 0)
    vcap_caught = False
    try: m1.find_events(Q_big, G, upd, k=0)
    except ResourceLimitError: vcap_caught = True
    cases.append({"check": "VERTEX_CAP_EXCEEDED", "status": "PASS" if vcap_caught else "FAIL"})
    if not vcap_caught: all_ok = False

    return cases, all_ok


# ============================================================================
# Stage 7: Manifest Reconciliation
# ============================================================================

def reconcile_manifest(manifest_path: Path, hand_cases: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
    """Reconciles configs/dstar02_correctness_cases.json against executable spec."""
    errors: List[str] = []
    if not manifest_path.is_file():
        return {"status": "FAIL", "errors": [f"Manifest file not found: {manifest_path}"]}, False

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        return {"status": "FAIL", "errors": [f"Malformed manifest JSON: {e}"]}, False

    # Check corpus declaration
    corpus_decl = manifest.get("corpus_840", {})
    if corpus_decl.get("total_cases") != 840:
        errors.append(f"Corpus total_cases mismatch: {corpus_decl.get('total_cases')} != 840")
    if corpus_decl.get("total_truth_events") != 8002:
        errors.append(f"Corpus total_truth_events mismatch: {corpus_decl.get('total_truth_events')} != 8002")

    for fpath_str in corpus_decl.get("files", []):
        fpath = REPO_ROOT / fpath_str
        if not fpath.is_file():
            errors.append(f"Declared corpus file missing on disk: {fpath_str}")

    # Check stream matrix declaration
    matrix_decl = manifest.get("stream_56_matrix", {})
    if matrix_decl.get("total_configurations") != 56:
        errors.append(f"Matrix total_configurations mismatch: {matrix_decl.get('total_configurations')} != 56")
    if matrix_decl.get("total_updates") != 672:
        errors.append(f"Matrix total_updates mismatch: {matrix_decl.get('total_updates')} != 672")

    # Check hand cases
    raw_cases = manifest.get("hand_cases", [])
    manifest_cids = [c.get("id") for c in raw_cases if isinstance(c, dict) and "id" in c]
    spec_cids = [c["case_id"].split("_STEP")[0] for c in hand_cases]
    missing = set(spec_cids) - set(manifest_cids)
    if missing:
        errors.append(f"Missing hand cases in manifest: {sorted(missing)}")

    ok = len(errors) == 0
    return {"status": "PASS" if ok else "FAIL", "errors": errors}, ok


# ============================================================================
# Main Orchestration
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DSTAR-02 correctness report")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "reports" / "dstar02" / "correctness_summary.json",
        help="Path to output JSON report",
    )
    args = parser.parse_args()

    run_id = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}"
    t_start = time.time()
    completed_stages: Dict[str, Any] = {}
    git_meta = collect_git_provenance()

    tracked_source_files = [
        REPO_ROOT / "scripts" / "dstar_event_common.py",
        REPO_ROOT / "scripts" / "dstar_m1_enum.py",
        REPO_ROOT / "scripts" / "dstar_m2_event.py",
        REPO_ROOT / "scripts" / "dstar_event_cli.py",
        REPO_ROOT / "scripts" / "generate_dstar02_report.py",
        REPO_ROOT / "configs" / "dstar02_correctness_cases.json",
        REPO_ROOT / "docs" / "dstar02_engine_contract.md",
    ]
    source_hashes = {
        str(p.relative_to(REPO_ROOT)): compute_file_sha256(p)
        for p in tracked_source_files if p.is_file()
    }

    print(f"=== Starting DSTAR-02 Report Generation [{run_id}] ===")
    print(f"Base commit: {git_meta['configured_historical_base_sha']}")
    print(f"Current branch: {git_meta['branch']} (HEAD={git_meta['execution_head_sha'][:8]})")

    try:
        # 1. Independence Audit
        audit_res, audit_ok = run_independence_audit()
        completed_stages["independence_audit"] = audit_res
        print(f"[1/7] Independence audit: {'PASS' if audit_ok else 'FAIL'}")

        # 2. Hand cases
        hand_cases, hand_ok = run_hand_cases()
        completed_stages["hand_cases"] = hand_cases
        print(f"[2/7] Hand and adversarial cases: {'PASS' if hand_ok else 'FAIL'} ({len(hand_cases)} cases)")

        # 3. Corpus 840
        corpus_res, corpus_ok = run_corpus_840_reference()
        completed_stages["corpus_840"] = corpus_res
        print(
            f"[3/7] Corpus 840 reference: {'PASS' if corpus_ok else 'FAIL'} "
            f"({corpus_res.get('total_cases')} cases, {corpus_res.get('total_truth_events')} truth events)"
        )

        # 4. Stream 56 matrix
        stream_res, stream_ok = run_56_stream_matrix()
        completed_stages["stream_56_matrix"] = stream_res
        print(
            f"[4/7] 56-Stream stateful matrix: {'PASS' if stream_ok else 'FAIL'} "
            f"({stream_res.get('total_configurations')} configs, {stream_res.get('total_updates')} updates)"
        )

        # 5. Invariant violation guard
        inv_res, inv_ok = run_invariant_violation_check()
        completed_stages["invariant_violation"] = inv_res
        print(f"[5/7] Invariant violation guard: {'PASS' if inv_ok else 'FAIL'}")

        # 6. Resource guards
        res_cases, res_ok = run_resource_guard_checks()
        completed_stages["resource_guards"] = res_cases
        print(f"[6/7] Resource limit guards: {'PASS' if res_ok else 'FAIL'}")

        # 7. Manifest reconciliation
        manifest_path = REPO_ROOT / "configs" / "dstar02_correctness_cases.json"
        man_res, man_ok = reconcile_manifest(manifest_path, hand_cases)
        completed_stages["manifest_reconciliation"] = man_res
        print(f"[7/7] Manifest reconciliation: {'PASS' if man_ok else 'FAIL'}")

        all_passed = audit_ok and hand_ok and corpus_ok and stream_ok and inv_ok and res_ok and man_ok
        t_elapsed = time.time() - t_start

        report_data = {
            "contract_version": "dstar-v1.0",
            "run_id": run_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "runtime_seconds": round(t_elapsed, 4),
            "environment": {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
            },
            "provenance": {
                **git_meta,
                "source_hashes": source_hashes,
                "invocation": f"python3 scripts/generate_dstar02_report.py --output {args.output}",
            },
            "overall_status": "PASS" if all_passed else "FAIL",
            "summary": {
                "independence_audit_status": audit_res["status"],
                "hand_cases_count": len(hand_cases),
                "hand_cases_passed": sum(1 for c in hand_cases if c["status"] == "PASS"),
                "corpus_840_total_cases": corpus_res.get("total_cases", 0),
                "corpus_840_truth_events": corpus_res.get("total_truth_events", 0),
                "corpus_840_m1_agreed": corpus_res.get("m1_matching_truth", 0),
                "corpus_840_m2_agreed": corpus_res.get("m2_matching_truth", 0),
                "stream_56_configurations": stream_res.get("total_configurations", 0),
                "stream_56_updates": stream_res.get("total_updates", 0),
                "stream_56_agreed_steps": stream_res.get("step_agreement_count", 0),
                "stream_56_reconstructed_steps": stream_res.get("reconstruction_agreement_count", 0),
                "invariant_violation_guard_status": inv_res["status"],
                "resource_guards_status": "PASS" if res_ok else "FAIL",
                "manifest_reconciliation_status": man_res["status"],
            },
            "stages": completed_stages,
        }

        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp_output = args.output.with_suffix(".tmp")
        with open(tmp_output, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        tmp_output.replace(args.output)

        print(f"\nReport written successfully to: {args.output}")
        print(f"Overall status: {'PASS' if all_passed else 'FAIL'} in {t_elapsed:.2f}s")

        if not all_passed:
            sys.exit(1)

    except Exception as e:
        print(f"[FATAL] Error generating report: {e}", file=sys.stderr)
        err_report = {
            "contract_version": "dstar-v1.0",
            "run_id": run_id,
            "overall_status": "ERROR",
            "fatal_error": str(e),
            "partial_stages": completed_stages,
            "provenance": git_meta,
        }
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            tmp_output = args.output.with_suffix(".tmp")
            with open(tmp_output, "w", encoding="utf-8") as f:
                json.dump(err_report, f, indent=2)
            tmp_output.replace(args.output)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
