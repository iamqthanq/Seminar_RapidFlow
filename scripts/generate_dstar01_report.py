#!/usr/bin/env python3
"""
scripts/generate_dstar01_report.py — Truthful Evidence and Report Generator for DSTAR-01.

Executes all DSTAR-01 correctness suites directly:
1. Hand-calculated cases A through L (with strict S1/S2 separation and public domain rejection).
2. Lambda(Q) global edge connectivity reference checks.
3. Atomic invalid update checks (rejection + unchanged graph + unchanged mappings).
4. Legacy APX-02 Compatibility Matrix: 56 configurations, 224 valid dynamic updates.
5. Boundary characterization diagnostics (anchor edge + |R|=k for INSERT/DELETE, event/no-event, orientations).
6. Resource limit fail-closed guard checks (cheap guards before cut calculation).
7. Manifest reconciliation with configs/dstar01_cases.json.

Outputs:
- Configurable output path via --output PATH (default: reports/dstar01/correctness_summary.json).
- Exits 0 only if ALL required cases and matrix conditions PASS; exits nonzero on any FAIL/ERROR/INCOMPLETE.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Project root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Independent D* Oracle
from scripts.dstar_oracle import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DStarDynamicOracle,
    DStarGraph,
    DStarUpdate,
    DuplicateEdgeInsertionError,
    EdgeLabelMismatchDeletionError,
    InvalidGraphError,
    InvalidThresholdError,
    NonexistentEdgeDeletionError,
    ResourceLimitError,
    SelfLoopError,
    UnknownVertexError,
    UnsupportedOperationError,
    apply_update,
    check_anchored_boundary_property,
    compute_edge_connectivity,
    diagnose_mapping_s1_s2,
    enumerate_snapshot,
    evaluate_mapping,
    validate_resource_caps,
)

# Test adapter helpers from test_dstar_legacy_compatibility
from tests.test_dstar_legacy_compatibility import (
    APXDynamicOracle,
    APXGraph,
    ConnectivityMode,
    build_c4_query,
    build_diamond_query,
    build_k4_query,
    dstar_to_apx_graph,
    dstar_to_apx_update,
    generate_random_data_graph,
    generate_valid_updates,
)


class LegacyMatrixExecutionError(Exception):
    """
    R3-F02: Raised when an exception occurs during legacy matrix execution.
    Carries the partial matrix results and failing step details.
    """
    def __init__(self, message: str, partial_matrix: Dict[str, Any]):
        super().__init__(message)
        self.message = message
        self.partial_matrix = partial_matrix


_ACTIVE_MATRIX_TRACKER: Optional[Dict[str, Any]] = None


def compute_file_sha256(path: Path) -> str:
    if not path.is_file():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_metadata() -> Dict[str, Any]:
    # 1. Execution HEAD
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception as e:
        head_sha = f"UNKNOWN (git rev-parse HEAD failed: {e})"

    # 2. Branch name or detached HEAD
    try:
        branch = subprocess.check_output(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception:
        try:
            curr = subprocess.check_output(
                ["git", "branch", "--show-current"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
            ).strip()
            if curr:
                branch = curr
            else:
                branch = "DETACHED_HEAD"
        except Exception as e:
            branch = f"UNKNOWN (git branch check failed: {e})"

    # 3. Dirty status
    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
        is_dirty = bool(status_out)
    except Exception as e:
        is_dirty = f"UNKNOWN (git status failed: {e})"

    # 4. Observed origin/QuangThang SHA
    try:
        origin_qt_sha = subprocess.check_output(
            ["git", "rev-parse", "origin/QuangThang"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception as e:
        origin_qt_sha = f"UNAVAILABLE (rev-parse origin/QuangThang failed: {e})"

    # 5. Observed merge-base SHA
    try:
        merge_base = subprocess.check_output(
            ["git", "merge-base", "origin/QuangThang", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
        ).strip()
    except Exception as e:
        merge_base = f"UNAVAILABLE (merge-base failed: {e})"

    return {
        "configured_historical_base_sha": "89f65b1490ba2e31d8b599d295065dbe694460aa",
        "observed_origin_quangthang_sha": origin_qt_sha,
        "observed_merge_base_sha": merge_base,
        "execution_head_sha": head_sha,
        "is_dirty_at_runtime": is_dirty,
        "branch": branch,
    }


def run_hand_cases(fixtures_dir: Path) -> Tuple[List[Dict[str, Any]], bool]:
    cases: List[Dict[str, Any]] = []
    all_ok = True

    # Case A: Exact recovery k=0
    try:
        Q_a = DStarGraph()
        for v in (1, 2, 3):
            Q_a.add_vertex(v, 0)
        Q_a.add_edge(1, 2, 0)
        Q_a.add_edge(2, 3, 0)
        Q_a.add_edge(3, 1, 0)
        G_a = DStarGraph()
        for v in (10, 20, 30, 40):
            G_a.add_vertex(v, 0)
        G_a.add_edge(10, 20, 0)
        G_a.add_edge(20, 30, 0)
        G_a.add_edge(30, 10, 0)
        G_a.add_edge(30, 40, 0)
        matches_a = enumerate_snapshot(Q_a, G_a, k=0)
        ok_a = len(matches_a) == 6
        cases.append({
            "case_id": "CASE_HAND_A",
            "name": "Exact recovery k=0 with extra chord/tail",
            "status": "PASS" if ok_a else "FAIL",
            "matches_count": len(matches_a),
            "expected_count": 6,
        })
        if not ok_a:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_A", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case B: Extra internal chord
    try:
        with open(fixtures_dir / "hand_c4_query.json", "r") as f:
            Q_b = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_c4_data_chord.json", "r") as f:
            G_b = DStarGraph.from_dict(json.load(f))
        matches_b = enumerate_snapshot(Q_b, G_b, k=0)
        ok_b = len(matches_b) == 8
        cases.append({
            "case_id": "CASE_HAND_B",
            "name": "Extra internal chord does not invalidate match",
            "status": "PASS" if ok_b else "FAIL",
            "matches_count": len(matches_b),
            "expected_count": 8,
        })
        if not ok_b:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_B", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case C: External tail
    try:
        with open(fixtures_dir / "hand_diamond_query.json", "r") as f:
            Q_c = DStarGraph.from_dict(json.load(f))
        G_c = DStarGraph()
        for v, lbl in [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "Z")]:
            G_c.add_vertex(v, lbl)
        G_c.add_edge(1, 2, 0)
        G_c.add_edge(1, 3, 0)
        G_c.add_edge(2, 3, 0)
        G_c.add_edge(2, 4, 0)
        G_c.add_edge(3, 4, 0)
        G_c.add_edge(4, 5, 0)
        matches_c = enumerate_snapshot(Q_c, G_c, k=1)
        expected_match_c = tuple(EXECUTABLE_CASE_SPEC["hand_cases"]["CASE_HAND_C"]["expected_match"])
        ok_c = expected_match_c in matches_c
        cases.append({
            "case_id": "CASE_HAND_C",
            "name": "External tail does not penalize edge recall",
            "status": "PASS" if ok_c else "FAIL",
            "expected_match": list(expected_match_c),
            "match_found": ok_c,
            "accepted": ok_c,
        })
        if not ok_c:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_C", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case D: Injectivity violation
    try:
        Q_d = DStarGraph()
        for v in (1, 2):
            Q_d.add_vertex(v, 0)
        Q_d.add_edge(1, 2, 0)
        G_d = DStarGraph()
        G_d.add_vertex(10, 0)
        eval_d = evaluate_mapping(Q_d, G_d, [10, 10], k=0)
        ok_d = (not eval_d.accepted) and ("NON_INJECTIVE" in eval_d.reject_reasons)
        cases.append({
            "case_id": "CASE_HAND_D",
            "name": "Non-injective mapping rejected",
            "status": "PASS" if ok_d else "FAIL",
            "rejected_properly": ok_d,
        })
        if not ok_d:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_D", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case E: Vertex label mismatch
    try:
        Q_e = DStarGraph()
        Q_e.add_vertex(1, "A")
        Q_e.add_vertex(2, "B")
        Q_e.add_edge(1, 2, 0)
        G_e = DStarGraph()
        G_e.add_vertex(10, "A")
        G_e.add_vertex(20, "C")
        G_e.add_edge(10, 20, 0)
        eval_e = evaluate_mapping(Q_e, G_e, [10, 20], k=0)
        ok_e = (not eval_e.accepted) and (not eval_e.vertex_labels_ok)
        cases.append({
            "case_id": "CASE_HAND_E",
            "name": "Vertex label mismatch rejected",
            "status": "PASS" if ok_e else "FAIL",
            "rejected_properly": ok_e,
        })
        if not ok_e:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_E", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case F: Edge label mismatch
    try:
        Q_f = DStarGraph()
        for v in (1, 2, 3):
            Q_f.add_vertex(v, 0)
        Q_f.add_edge(1, 2, 0)
        Q_f.add_edge(2, 3, 0)
        Q_f.add_edge(3, 1, 0)
        G_f = DStarGraph()
        for v in (1, 2, 3):
            G_f.add_vertex(v, 0)
        G_f.add_edge(1, 2, 0)
        G_f.add_edge(2, 3, 99)
        G_f.add_edge(3, 1, 0)
        matches_f0 = enumerate_snapshot(Q_f, G_f, k=0)
        matches_f1 = enumerate_snapshot(Q_f, G_f, k=1)
        ok_f = ((1, 2, 3) not in matches_f0) and ((1, 2, 3) in matches_f1)
        cases.append({
            "case_id": "CASE_HAND_F",
            "name": "Edge label mismatch counted as missing edge in R(f)",
            "status": "PASS" if ok_f else "FAIL",
            "rejected_at_k0": (1, 2, 3) not in matches_f0,
            "accepted_at_k1": (1, 2, 3) in matches_f1,
        })
        if not ok_f:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_F", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case G: Sparse IDs
    try:
        with open(fixtures_dir / "hand_sparse_query.json", "r") as f:
            Q_g = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_sparse_data.json", "r") as f:
            G_g = DStarGraph.from_dict(json.load(f))
        matches_g = enumerate_snapshot(Q_g, G_g, k=0)
        expected_matches_g = [tuple(t) for t in EXECUTABLE_CASE_SPEC["hand_cases"]["CASE_HAND_G"]["expected_matches"]]
        ok_g = matches_g == expected_matches_g
        cases.append({
            "case_id": "CASE_HAND_G",
            "name": "Sparse query IDs preserve ascending canonical order",
            "status": "PASS" if ok_g else "FAIL",
            "expected_matches": [list(t) for t in expected_matches_g],
            "matches": [list(t) for t in matches_g],
        })
        if not ok_g:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_G", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case H: Automorphism K4
    try:
        with open(fixtures_dir / "hand_k4_query.json", "r") as f:
            Q_h = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_k4_data.json", "r") as f:
            G_h = DStarGraph.from_dict(json.load(f))
        matches_h = enumerate_snapshot(Q_h, G_h, k=0)
        ok_h = len(matches_h) == 24 and len(set(matches_h)) == 24
        cases.append({
            "case_id": "CASE_HAND_H",
            "name": "Automorphism on K4 yields exactly 24 ordered tuples",
            "status": "PASS" if ok_h else "FAIL",
            "matches_count": len(matches_h),
            "expected_count": 24,
        })
        if not ok_h:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_H", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case I: Diamond event
    try:
        with open(fixtures_dir / "hand_diamond_query.json", "r") as f:
            Q_i = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_diamond_data_before.json", "r") as f:
            G_i_before = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_diamond_stream_event.json", "r") as f:
            stream_i = json.load(f)

        oracle_i = DStarDynamicOracle(Q_i, G_i_before, k=1)
        up_i1 = DStarUpdate(op=stream_i[0]["op"], u=stream_i[0]["u"], v=stream_i[0]["v"], label=stream_i[0]["label"])
        delta_i1 = oracle_i.apply_update(up_i1)
        up_i2 = DStarUpdate(op=stream_i[1]["op"], u=stream_i[1]["u"], v=stream_i[1]["v"], label=stream_i[1]["label"])
        delta_i2 = oracle_i.apply_update(up_i2)

        ok_i = (delta_i1.entered == [(1, 2, 3, 4)]) and (delta_i1.exited == []) and \
               (delta_i2.entered == []) and (delta_i2.exited == [(1, 2, 3, 4)])
        cases.append({
            "case_id": "CASE_HAND_I",
            "name": "Diamond event: bridge INSERT triggers ENTERED, DELETE triggers EXITED",
            "status": "PASS" if ok_i else "FAIL",
            "step1_entered": [list(t) for t in delta_i1.entered],
            "step2_exited": [list(t) for t in delta_i2.exited],
        })
        if not ok_i:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_I", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case J: Diamond within-threshold no-event
    try:
        with open(fixtures_dir / "hand_diamond_query.json", "r") as f:
            Q_j = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_diamond_data_full.json", "r") as f:
            G_j_full = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_diamond_stream_no_event.json", "r") as f:
            stream_j = json.load(f)

        oracle_j = DStarDynamicOracle(Q_j, G_j_full, k=1)
        up_j1 = DStarUpdate(op=stream_j[0]["op"], u=stream_j[0]["u"], v=stream_j[0]["v"], label=stream_j[0]["label"])
        delta_j1 = oracle_j.apply_update(up_j1)
        up_j2 = DStarUpdate(op=stream_j[1]["op"], u=stream_j[1]["u"], v=stream_j[1]["v"], label=stream_j[1]["label"])
        delta_j2 = oracle_j.apply_update(up_j2)

        ok_j = (delta_j1.entered == []) and (delta_j1.exited == []) and \
               (delta_j2.entered == []) and (delta_j2.exited == [(1, 2, 3, 4)])
        cases.append({
            "case_id": "CASE_HAND_J",
            "name": "Diamond within-threshold delete produces no event (no SCORE_CHANGED)",
            "status": "PASS" if ok_j else "FAIL",
            "step1_no_event": (delta_j1.entered == [] and delta_j1.exited == []),
            "step2_exited": [list(t) for t in delta_j2.exited],
        })
        if not ok_j:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_J", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case K: Unrelated update
    try:
        with open(fixtures_dir / "hand_diamond_query.json", "r") as f:
            Q_k = DStarGraph.from_dict(json.load(f))
        G_k = DStarGraph()
        for v, lbl in [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "Z"), (6, "Z")]:
            G_k.add_vertex(v, lbl)
        G_k.add_edge(1, 2, 0)
        G_k.add_edge(1, 3, 0)
        G_k.add_edge(2, 3, 0)
        G_k.add_edge(2, 4, 0)
        G_k.add_edge(3, 4, 0)
        oracle_k = DStarDynamicOracle(Q_k, G_k, k=1)
        up_k = DStarUpdate(op="INSERT", u=5, v=6, label=0)
        delta_k = oracle_k.apply_update(up_k)
        ok_k = (delta_k.entered == []) and (delta_k.exited == [])
        cases.append({
            "case_id": "CASE_HAND_K",
            "name": "Unrelated edge update produces no entered/exited events",
            "status": "PASS" if ok_k else "FAIL",
            "entered_count": len(delta_k.entered),
            "exited_count": len(delta_k.exited),
        })
        if not ok_k:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_K", "status": "ERROR", "error": str(e)})
        all_ok = False

    # Case L: S1 vs S2 distinction & public domain rejection
    try:
        with open(fixtures_dir / "hand_s1_vs_s2_query.json", "r") as f:
            Q_l = DStarGraph.from_dict(json.load(f))
        with open(fixtures_dir / "hand_s1_vs_s2_data.json", "r") as f:
            G_l = DStarGraph.from_dict(json.load(f))

        # Query is P4 (lambda=1). For k=1, k >= lambda(P4) -> public acceptance MUST reject with InvalidThresholdError!
        public_rejected = False
        try:
            evaluate_mapping(Q_l, G_l, [1, 2, 3, 4], k=1)
        except InvalidThresholdError:
            public_rejected = True

        # Diagnostic helper separates S1 and S2
        diag_l = diagnose_mapping_s1_s2(Q_l, G_l, [1, 2, 3, 4], k=1)
        expected_pub_rej = EXECUTABLE_CASE_SPEC["hand_cases"]["CASE_HAND_L"]["expected_public_rejection"]
        ok_l = (
            (public_rejected is expected_pub_rej)
            and (diag_l.s1_connected is True)
            and (diag_l.s2_connected is False)
            and (diag_l.mvp_accepted is False)
            and (diag_l.in_mvp_domain is False)
        )
        cases.append({
            "case_id": "CASE_HAND_L",
            "name": "S1 vs S2 distinction & public out-of-domain rejection",
            "status": "PASS" if ok_l else "FAIL",
            "expected_public_rejection": expected_pub_rej,
            "public_rejected_properly": public_rejected,
            "diagnostic_s1": diag_l.s1_connected,
            "diagnostic_s2": diag_l.s2_connected,
            "in_mvp_domain": diag_l.in_mvp_domain,
        })
        if not ok_l:
            all_ok = False
    except Exception as e:
        cases.append({"case_id": "CASE_HAND_L", "status": "ERROR", "error": str(e)})
        all_ok = False

    return cases, all_ok


def run_lambda_checks() -> Tuple[List[Dict[str, Any]], bool]:
    cases: List[Dict[str, Any]] = []
    all_ok = True

    def make_p4():
        g = DStarGraph()
        for v in (1, 2, 3, 4):
            g.add_vertex(v, 0)
        g.add_edge(1, 2, 0)
        g.add_edge(2, 3, 0)
        g.add_edge(3, 4, 0)
        return g

    def make_star4():
        g = DStarGraph()
        for v in (1, 2, 3, 4):
            g.add_vertex(v, 0)
        g.add_edge(1, 2, 0)
        g.add_edge(1, 3, 0)
        g.add_edge(1, 4, 0)
        return g

    def make_c6():
        g = DStarGraph()
        for v in range(1, 7):
            g.add_vertex(v, 0)
        for v in range(1, 6):
            g.add_edge(v, v + 1, 0)
        g.add_edge(6, 1, 0)
        return g

    def make_k3_3():
        g = DStarGraph()
        for v in range(1, 7):
            g.add_vertex(v, 0)
        for u in (1, 2, 3):
            for v in (4, 5, 6):
                g.add_edge(u, v, 0)
        return g

    test_graphs = [
        ("P4", make_p4(), 1),
        ("Star4", make_star4(), 1),
        ("C4", build_c4_query(), 2),
        ("C6", make_c6(), 2),
        ("Diamond", build_diamond_query(), 2),
        ("K4", build_k4_query(), 3),
        ("K3_3", make_k3_3(), 3),
    ]

    for name, g, expected_lam in test_graphs:
        lam = compute_edge_connectivity(g, max_query_vertices=6)
        ok = (lam == expected_lam)
        cases.append({
            "topology": name,
            "vertices": len(g.vertices),
            "edges": len(g.edges),
            "expected_lambda": expected_lam,
            "computed_lambda": lam,
            "status": "PASS" if ok else "FAIL",
        })
        if not ok:
            all_ok = False

    return cases, all_ok


def run_atomic_update_checks(fixtures_dir: Path) -> Tuple[List[Dict[str, Any]], bool]:
    cases: List[Dict[str, Any]] = []
    all_ok = True

    with open(fixtures_dir / "hand_diamond_query.json", "r") as f:
        Q = DStarGraph.from_dict(json.load(f))
    with open(fixtures_dir / "hand_diamond_data_full.json", "r") as f:
        G = DStarGraph.from_dict(json.load(f))

    oracle = DStarDynamicOracle(Q, G, k=1)
    init_edges = dict(oracle.G.edges)
    init_matches = set(oracle.current_matches)

    invalid_test_ops = [
        ("UNKNOWN_VERTEX", DStarUpdate(op="INSERT", u=1, v=999, label=0), UnknownVertexError),
        ("DUPLICATE_INSERTION", DStarUpdate(op="INSERT", u=1, v=2, label=0), DuplicateEdgeInsertionError),
        ("NONEXISTENT_DELETION", DStarUpdate(op="DELETE", u=1, v=4, label=0), NonexistentEdgeDeletionError),
        ("LABEL_MISMATCH_DELETION", DStarUpdate(op="DELETE", u=1, v=2, label=999), EdgeLabelMismatchDeletionError),
    ]

    for name, up, exc_cls in invalid_test_ops:
        try:
            oracle.apply_update(up)
            cases.append({"check": name, "status": "FAIL", "reason": "Exception not raised"})
            all_ok = False
        except exc_cls:
            edges_ok = (oracle.G.edges == init_edges)
            matches_ok = (oracle.current_matches == init_matches)
            ok = edges_ok and matches_ok
            cases.append({
                "check": name,
                "status": "PASS" if ok else "FAIL",
                "graph_untouched": edges_ok,
                "matches_untouched": matches_ok,
            })
            if not ok:
                all_ok = False
        except Exception as e:
            cases.append({"check": name, "status": "FAIL", "reason": f"Wrong exception: {type(e).__name__}"})
            all_ok = False

    try:
        DStarUpdate(op="INSERT", u=1, v=1, label=0)
        cases.append({"check": "SELF_LOOP", "status": "FAIL", "reason": "SelfLoopError not raised"})
        all_ok = False
    except SelfLoopError:
        cases.append({"check": "SELF_LOOP", "status": "PASS", "graph_untouched": True, "matches_untouched": True})

    return cases, all_ok


def evaluate_legacy_matrix_success(matrix_summary: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    R1 / F01: Single aggregate success predicate for the legacy compatibility matrix.
    Enforces that matrix executed completely, with all required configurations, updates, and events.
    """
    errors: List[str] = []
    total_configs = matrix_summary.get("total_configurations", 0)
    total_updates = matrix_summary.get("total_updates", 0)
    total_entered_events = matrix_summary.get("total_entered_events", 0)
    total_exited_events = matrix_summary.get("total_exited_events", 0)
    total_entered_tuples = matrix_summary.get("total_entered_tuples", 0)
    total_exited_tuples = matrix_summary.get("total_exited_tuples", 0)
    total_insert_updates = matrix_summary.get("total_insert_updates", 0)
    total_delete_updates = matrix_summary.get("total_delete_updates", 0)

    if total_configs < 56:
        errors.append(f"Configurations count {total_configs} < 56 required")
    if total_updates < 224:
        errors.append(f"Total updates count {total_updates} < 224 required")
    if total_insert_updates <= 0:
        errors.append("Zero INSERT updates observed")
    if total_delete_updates <= 0:
        errors.append("Zero DELETE updates observed")
    if total_entered_events <= 0:
        errors.append("Zero non-empty ENTERED events observed")
    if total_exited_events <= 0:
        errors.append("Zero non-empty EXITED events observed")
    if total_entered_tuples <= 0:
        errors.append("Zero entered tuples observed")
    if total_exited_tuples <= 0:
        errors.append("Zero exited tuples observed")

    for cfg in matrix_summary.get("configs", []):
        if cfg.get("status") != "PASS":
            errors.append(f"Config {cfg.get('config_id')} status is {cfg.get('status')}")
        if cfg.get("updates_attempted", 0) < 4:
            errors.append(f"Config {cfg.get('config_id')} attempted only {cfg.get('updates_attempted')} updates (< 4)")
        if cfg.get("updates_agreed", 0) != cfg.get("updates_attempted", 0):
            errors.append(f"Config {cfg.get('config_id')} agreed {cfg.get('updates_agreed')}/{cfg.get('updates_attempted')}")

    is_pass = (len(errors) == 0)
    return is_pass, errors


def run_legacy_compatibility_matrix(
    progress_sink: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    sink = progress_sink if progress_sink is not None else _ACTIVE_MATRIX_TRACKER
    seed_base = 20261117
    matrix_specs = [
        ("C4", build_c4_query, [0, 1], 8),
        ("Diamond", build_diamond_query, [0, 1], 8),
        ("K4", build_k4_query, [0, 1, 2], 8),
    ]

    configs: List[Dict[str, Any]] = []
    step_records: List[Dict[str, Any]] = []
    total_configs = 0
    total_updates_attempted = 0
    total_updates_completed = 0
    total_updates_agreed = 0
    total_insert_updates = 0
    total_delete_updates = 0
    total_entered_events = 0
    total_exited_events = 0
    total_entered_tuples = 0
    total_exited_tuples = 0

    if sink is not None:
        sink.update({
            "status": "INCOMPLETE",
            "stage": "LEGACY_MATRIX",
            "total_configurations": 0,
            "total_configurations_completed": 0,
            "total_updates": 0,
            "total_updates_attempted": 0,
            "total_updates_completed": 0,
            "total_updates_agreed": 0,
            "completed_steps_count": 0,
            "configs": configs,
            "step_records": step_records,
            "failing_step": None,
        })

    for q_name, q_builder, k_values, seeds_count in matrix_specs:
        for k in k_values:
            for s_idx in range(seeds_count):
                total_configs += 1
                config_id = f"CONFIG_{total_configs:02d}_{q_name}_k{k}_s{s_idx}"
                seed = seed_base + total_configs * 31 + s_idx
                label_mode = "uniform" if (total_configs % 2 == 0) else "mixed"

                if sink is not None:
                    sink["total_configurations"] = total_configs
                    sink["current_config"] = {
                        "config_id": config_id,
                        "query": q_name,
                        "k": k,
                        "seed": seed,
                        "label_mode": label_mode,
                    }

                Q_dstar = q_builder(label_mode=label_mode)
                G_dstar = generate_random_data_graph(
                    seed=seed,
                    n_vertices=6,
                    n_initial_edges=7,
                    label_mode=label_mode,
                )
                updates = generate_valid_updates(
                    G=G_dstar,
                    seed=seed + 100,
                    n_updates=4,
                    label_mode=label_mode,
                )

                try:
                    dstar_oracle = DStarDynamicOracle(Q_dstar, G_dstar, k=k)
                    Q_apx = dstar_to_apx_graph(Q_dstar)
                    G_apx = dstar_to_apx_graph(G_dstar)
                    apx_oracle = APXDynamicOracle(
                        Q=Q_apx,
                        G0=G_apx,
                        eps_m=k,
                        eps_x=None,
                        mode=ConnectivityMode.STRICT,
                    )
                    init_match = (set(dstar_oracle.current_matches) == set(apx_oracle.current_matches))
                except Exception as e:
                    failing_step = {
                        "config_id": config_id,
                        "query": q_name,
                        "k": k,
                        "seed": seed,
                        "label_mode": label_mode,
                        "update_index": 0,
                        "operation": "INIT",
                        "endpoints": None,
                        "label": None,
                        "dstar_applied": False,
                        "legacy_applied": False,
                        "agreed": False,
                        "status": "ERROR",
                        "error_message": str(e),
                    }
                    step_records.append(failing_step)
                    partial_summary = {
                        "status": "ERROR",
                        "stage": "LEGACY_MATRIX",
                        "config_id": config_id,
                        "query": q_name,
                        "k": k,
                        "seed": seed,
                        "update_index": 0,
                        "operation": "INIT",
                        "endpoints": None,
                        "label": None,
                        "total_configurations_completed": len(configs),
                        "total_updates_attempted": total_updates_attempted,
                        "total_updates_completed": total_updates_completed,
                        "total_updates_agreed": total_updates_agreed,
                        "completed_steps_count": total_updates_completed,
                        "configs": list(configs),
                        "step_records": list(step_records),
                        "failing_step": failing_step,
                        "error_message": str(e),
                    }
                    if sink is not None:
                        sink.update(partial_summary)
                    raise LegacyMatrixExecutionError(
                        f"Exception during legacy matrix initialization at config {config_id}: {e}",
                        partial_matrix=partial_summary,
                    ) from e

                config_entered = 0
                config_exited = 0
                config_updates_agreed = 0

                for up_idx, up_dstar in enumerate(updates):
                    total_updates_attempted += 1
                    if sink is not None:
                        sink["total_updates_attempted"] = total_updates_attempted

                    dstar_applied = False
                    legacy_applied = False
                    try:
                        if up_dstar.op == "INSERT":
                            total_insert_updates += 1
                        elif up_dstar.op == "DELETE":
                            total_delete_updates += 1

                        delta_dstar = dstar_oracle.apply_update(up_dstar)
                        dstar_applied = True

                        up_apx = dstar_to_apx_update(up_dstar)
                        delta_apx = apx_oracle.apply_update(up_apx)
                        legacy_applied = True

                        entered_agreed = (set(delta_dstar.entered) == set(delta_apx.entered))
                        exited_agreed = (set(delta_dstar.exited) == set(delta_apx.exited))
                        snapshot_agreed = (set(dstar_oracle.current_matches) == set(apx_oracle.current_matches))

                        step_agreed = (entered_agreed and exited_agreed and snapshot_agreed)
                        if step_agreed:
                            config_updates_agreed += 1
                            total_updates_agreed += 1

                        total_updates_completed += 1

                        step_rec = {
                            "config_id": config_id,
                            "query": q_name,
                            "k": k,
                            "seed": seed,
                            "update_index": up_idx,
                            "operation": up_dstar.op,
                            "endpoints": [up_dstar.u, up_dstar.v],
                            "label": up_dstar.label,
                            "dstar_applied": True,
                            "legacy_applied": True,
                            "agreed": step_agreed,
                            "status": "PASS" if step_agreed else "FAIL",
                        }
                        step_records.append(step_rec)

                        if delta_dstar.entered:
                            total_entered_events += 1
                            total_entered_tuples += len(delta_dstar.entered)
                            config_entered += len(delta_dstar.entered)
                        if delta_dstar.exited:
                            total_exited_events += 1
                            total_exited_tuples += len(delta_dstar.exited)
                            config_exited += len(delta_dstar.exited)

                        if sink is not None:
                            sink["total_updates_completed"] = total_updates_completed
                            sink["total_updates"] = total_updates_completed
                            sink["total_updates_agreed"] = total_updates_agreed
                            sink["completed_steps_count"] = total_updates_completed

                    except Exception as e:
                        failing_step = {
                            "config_id": config_id,
                            "query": q_name,
                            "k": k,
                            "seed": seed,
                            "update_index": up_idx,
                            "operation": up_dstar.op,
                            "endpoints": [up_dstar.u, up_dstar.v],
                            "label": up_dstar.label,
                            "dstar_applied": dstar_applied,
                            "legacy_applied": legacy_applied,
                            "agreed": False,
                            "status": "ERROR",
                            "error_message": str(e),
                        }
                        step_records.append(failing_step)
                        partial_summary = {
                            "status": "ERROR",
                            "stage": "LEGACY_MATRIX",
                            "config_id": config_id,
                            "query": q_name,
                            "k": k,
                            "seed": seed,
                            "update_index": up_idx,
                            "operation": up_dstar.op,
                            "endpoints": [up_dstar.u, up_dstar.v],
                            "label": up_dstar.label,
                            "total_configurations_completed": len(configs),
                            "total_updates_attempted": total_updates_attempted,
                            "total_updates_completed": total_updates_completed,
                            "total_updates_agreed": total_updates_agreed,
                            "completed_steps_count": total_updates_completed,
                            "configs": list(configs),
                            "step_records": list(step_records),
                            "failing_step": failing_step,
                            "error_message": str(e),
                        }
                        if sink is not None:
                            sink.update(partial_summary)
                        raise LegacyMatrixExecutionError(
                            f"Exception in legacy matrix at config {config_id}, update {up_idx}: {e}",
                            partial_matrix=partial_summary,
                        ) from e

                config_pass = (config_updates_agreed == len(updates) and init_match and len(updates) >= 4)
                cfg_dict = {
                    "config_id": config_id,
                    "query": q_name,
                    "k": k,
                    "seed": seed,
                    "label_mode": label_mode,
                    "updates_attempted": len(updates),
                    "updates_agreed": config_updates_agreed,
                    "entered_tuples": config_entered,
                    "exited_tuples": config_exited,
                    "status": "PASS" if config_pass else "FAIL",
                }
                configs.append(cfg_dict)
                if sink is not None:
                    sink["total_configurations_completed"] = len(configs)

    matrix_summary: Dict[str, Any] = {
        "status": "INCOMPLETE",
        "total_configurations": total_configs,
        "total_configurations_completed": len(configs),
        "total_updates": total_updates_completed,
        "total_updates_attempted": total_updates_attempted,
        "total_updates_completed": total_updates_completed,
        "total_updates_agreed": total_updates_agreed,
        "total_insert_updates": total_insert_updates,
        "total_delete_updates": total_delete_updates,
        "total_entered_events": total_entered_events,
        "total_exited_events": total_exited_events,
        "total_entered_tuples": total_entered_tuples,
        "total_exited_tuples": total_exited_tuples,
        "configs": configs,
        "step_records": step_records,
    }

    is_success, failure_reasons = evaluate_legacy_matrix_success(matrix_summary)
    matrix_summary["status"] = "PASS" if is_success else "FAIL"
    matrix_summary["failure_reasons"] = failure_reasons
    if sink is not None:
        sink.update(matrix_summary)

    return matrix_summary, is_success


def run_boundary_diagnostics(fixtures_dir: Path) -> Tuple[Dict[str, Any], bool]:
    """
    R2-F03 / R7: Expanded boundary diagnostics matrix.
    Tests anchor edge + |R|=k characterization for:
    - k=0 (Exact recovery boundary) for INSERT and DELETE events
    - k=1 (Approximate boundary) for INSERT and DELETE events
    - reverse orientation: INSERT (v, u)
    - within-threshold no-event: DELETE leaving mapping within budget
    - non-matching label anchor: INSERT with non-matching edge label.
    """
    cases: List[Dict[str, Any]] = []
    all_ok = True

    with open(fixtures_dir / "hand_diamond_query.json", "r") as f:
        Q = DStarGraph.from_dict(json.load(f))
    with open(fixtures_dir / "hand_diamond_data_before.json", "r") as f:
        G_before = DStarGraph.from_dict(json.load(f))
    with open(fixtures_dir / "hand_diamond_data_full.json", "r") as f:
        G_full = DStarGraph.from_dict(json.load(f))

    # Test 1 (k=0): INSERT event at exact recovery boundary
    # G_almost has 4 of 5 edges of Diamond (edge 2-3 missing).
    # At k=0, m_bef is empty (|R|=1 > 0). Inserting (2, 3) yields full Diamond (|R|=0).
    G_almost = G_full.clone()
    G_almost.remove_edge(2, 3, 0)
    up_ins_k0 = DStarUpdate(op="INSERT", u=2, v=3, label=0)
    m_bef_k0 = set(enumerate_snapshot(Q, G_almost, k=0))
    m_aft_k0 = set(enumerate_snapshot(Q, G_full, k=0))
    diag_k0_ins = check_anchored_boundary_property(
        Q, G_almost, G_full, up_ins_k0, k=0,
        true_entered=m_aft_k0 - m_bef_k0, true_exited=m_bef_k0 - m_aft_k0
    )
    ok_k0_ins = diag_k0_ins["property_holds"] and diag_k0_ins["target_event_count"] == 1
    cases.append({
        "case_id": "BOUNDARY_INSERT_EVENT_K0",
        "name": "INSERT_bridge_event_k0",
        "topology": "Diamond",
        "k": 0,
        "operation": "INSERT",
        "expectation": "EVENT",
        "actual_event_count": diag_k0_ins["target_event_count"],
        "property_holds": diag_k0_ins["property_holds"],
        "status": "PASS" if ok_k0_ins else "FAIL",
    })
    if not ok_k0_ins:
        all_ok = False

    # Test 2 (k=0): DELETE event at exact recovery boundary
    up_del_k0 = DStarUpdate(op="DELETE", u=2, v=3, label=0)
    diag_k0_del = check_anchored_boundary_property(
        Q, G_full, G_almost, up_del_k0, k=0,
        true_entered=set(), true_exited=m_aft_k0 - m_bef_k0
    )
    ok_k0_del = diag_k0_del["property_holds"] and diag_k0_del["target_event_count"] == 1
    cases.append({
        "case_id": "BOUNDARY_DELETE_EVENT_K0",
        "name": "DELETE_bridge_event_k0",
        "topology": "Diamond",
        "k": 0,
        "operation": "DELETE",
        "expectation": "EVENT",
        "actual_event_count": diag_k0_del["target_event_count"],
        "property_holds": diag_k0_del["property_holds"],
        "status": "PASS" if ok_k0_del else "FAIL",
    })
    if not ok_k0_del:
        all_ok = False

    # Test 3 (k=1): INSERT bridge event (edge 2-3)
    up_ins = DStarUpdate(op="INSERT", u=2, v=3, label=0)
    G_after = G_before.clone()
    apply_update(G_after, up_ins)
    m_bef = set(enumerate_snapshot(Q, G_before, k=1))
    m_aft = set(enumerate_snapshot(Q, G_after, k=1))
    diag1 = check_anchored_boundary_property(
        Q, G_before, G_after, up_ins, k=1,
        true_entered=m_aft - m_bef, true_exited=m_bef - m_aft
    )
    ok1 = diag1["property_holds"] and diag1["target_event_count"] == 1
    cases.append({
        "case_id": "BOUNDARY_INSERT_EVENT_K1",
        "name": "INSERT_bridge_event_k1",
        "topology": "Diamond",
        "k": 1,
        "operation": "INSERT",
        "expectation": "EVENT",
        "actual_event_count": diag1["target_event_count"],
        "property_holds": diag1["property_holds"],
        "status": "PASS" if ok1 else "FAIL",
    })
    if not ok1:
        all_ok = False

    # Test 4 (k=1): DELETE bridge event (edge 2-3)
    up_del = DStarUpdate(op="DELETE", u=2, v=3, label=0)
    diag2 = check_anchored_boundary_property(
        Q, G_after, G_before, up_del, k=1,
        true_entered=set(), true_exited=m_aft - m_bef
    )
    ok2 = diag2["property_holds"] and diag2["target_event_count"] == 1
    cases.append({
        "case_id": "BOUNDARY_DELETE_EVENT_K1",
        "name": "DELETE_bridge_event_k1",
        "topology": "Diamond",
        "k": 1,
        "operation": "DELETE",
        "expectation": "EVENT",
        "actual_event_count": diag2["target_event_count"],
        "property_holds": diag2["property_holds"],
        "status": "PASS" if ok2 else "FAIL",
    })
    if not ok2:
        all_ok = False

    # Test 5 (k=1): Reverse orientation: INSERT (3, 2)
    up_ins_rev = DStarUpdate(op="INSERT", u=3, v=2, label=0)
    diag3 = check_anchored_boundary_property(
        Q, G_before, G_after, up_ins_rev, k=1,
        true_entered=m_aft - m_bef, true_exited=m_bef - m_aft
    )
    ok3 = diag3["property_holds"] and diag3["target_event_count"] == 1
    cases.append({
        "case_id": "BOUNDARY_REVERSE_ORIENTATION_K1",
        "name": "INSERT_reverse_orientation_k1",
        "topology": "Diamond",
        "k": 1,
        "operation": "INSERT",
        "expectation": "EVENT",
        "actual_event_count": diag3["target_event_count"],
        "property_holds": diag3["property_holds"],
        "status": "PASS" if ok3 else "FAIL",
    })
    if not ok3:
        all_ok = False

    # Test 6 (k=1): DELETE no-event within threshold (edge 2-4 from G_full, k=1)
    up_del_noev = DStarUpdate(op="DELETE", u=2, v=4, label=0)
    G_after_noev = G_full.clone()
    apply_update(G_after_noev, up_del_noev)
    m_bef_noev = set(enumerate_snapshot(Q, G_full, k=1))
    m_aft_noev = set(enumerate_snapshot(Q, G_after_noev, k=1))
    diag4 = check_anchored_boundary_property(
        Q, G_full, G_after_noev, up_del_noev, k=1,
        true_entered=m_aft_noev - m_bef_noev, true_exited=m_bef_noev - m_aft_noev
    )
    ok4 = diag4["property_holds"] and diag4["target_event_count"] == 0
    cases.append({
        "case_id": "BOUNDARY_NO_EVENT_DELETE_K1",
        "name": "DELETE_within_threshold_no_event_k1",
        "topology": "Diamond",
        "k": 1,
        "operation": "DELETE",
        "expectation": "NO_EVENT",
        "actual_event_count": diag4["target_event_count"],
        "property_holds": diag4["property_holds"],
        "status": "PASS" if ok4 else "FAIL",
    })
    if not ok4:
        all_ok = False

    # Test 7 (k=1): Wrong-label anchor: INSERT (2, 3) label "WRONG"
    up_wrong = DStarUpdate(op="INSERT", u=2, v=3, label="WRONG")
    G_wrong = G_before.clone()
    apply_update(G_wrong, up_wrong)
    m_bef_w = set(enumerate_snapshot(Q, G_before, k=1))
    m_aft_w = set(enumerate_snapshot(Q, G_wrong, k=1))
    diag5 = check_anchored_boundary_property(
        Q, G_before, G_wrong, up_wrong, k=1,
        true_entered=m_aft_w - m_bef_w, true_exited=m_bef_w - m_aft_w
    )
    ok5 = diag5["property_holds"] and diag5["target_event_count"] == 0
    cases.append({
        "case_id": "BOUNDARY_WRONG_LABEL_ANCHOR_K1",
        "name": "INSERT_wrong_label_anchor_k1",
        "topology": "Diamond",
        "k": 1,
        "operation": "INSERT",
        "expectation": "NO_EVENT",
        "actual_event_count": diag5["target_event_count"],
        "property_holds": diag5["property_holds"],
        "status": "PASS" if ok5 else "FAIL",
    })
    if not ok5:
        all_ok = False

    summary = {
        "status": "PASS" if all_ok else "FAIL",
        "cases": cases,
    }
    return summary, all_ok
    return summary, all_ok


def run_resource_guard_checks() -> Tuple[List[Dict[str, Any]], bool]:
    """
    R4 / F04 + N04: Validates cheap structural guards before exponential preprocessing.
    """
    cases: List[Dict[str, Any]] = []
    all_ok = True

    # Check 1: Oversized query rejected before cut computation
    Q_big = DStarGraph()
    for v in range(1, 8):  # 7 vertices > default max 6
        Q_big.add_vertex(v, 0)
    for v in range(1, 7):
        Q_big.add_edge(v, v + 1, 0)
    G_dummy = DStarGraph()
    for v in range(1, 5):
        G_dummy.add_vertex(v, 0)

    try:
        enumerate_snapshot(Q_big, G_dummy, k=0)
        cases.append({"check": "oversized_query_guard", "status": "FAIL", "reason": "ResourceLimitError not raised"})
        all_ok = False
    except ResourceLimitError as e:
        cases.append({"check": "oversized_query_guard", "status": "PASS", "message": str(e)})

    # Check 2: Oversized data graph rejected
    Q_ok = DStarGraph()
    for v in (1, 2):
        Q_ok.add_vertex(v, 0)
    Q_ok.add_edge(1, 2, 0)
    G_big = DStarGraph()
    for v in range(1, 10):  # 9 vertices > default max 8
        G_big.add_vertex(v, 0)

    try:
        enumerate_snapshot(Q_ok, G_big, k=0)
        cases.append({"check": "oversized_data_guard", "status": "FAIL", "reason": "ResourceLimitError not raised"})
        all_ok = False
    except ResourceLimitError as e:
        cases.append({"check": "oversized_data_guard", "status": "PASS", "message": str(e)})

    # Check 3: Candidate space bound rejected
    G_med = DStarGraph()
    for v in range(1, 9):  # 8 vertices
        G_med.add_vertex(v, 0)
    Q_med = DStarGraph()
    for v in range(1, 7):  # 6 vertices, P(8, 6) = 20160
        Q_med.add_vertex(v, 0)
    for v in range(1, 6):
        Q_med.add_edge(v, v + 1, 0)

    try:
        enumerate_snapshot(Q_med, G_med, k=0, max_candidates=1000)
        cases.append({"check": "candidate_bound_guard", "status": "FAIL", "reason": "ResourceLimitError not raised"})
        all_ok = False
    except ResourceLimitError as e:
        cases.append({"check": "candidate_bound_guard", "status": "PASS", "message": str(e)})

    # Check 4: Invalid cap values (bool, nonpositive)
    invalid_caps = [-1, 0, True, 2.5]
    for c in invalid_caps:
        try:
            validate_resource_caps(c, 8, 6)
            cases.append({"check": f"invalid_cap_{c}", "status": "FAIL", "reason": "ResourceLimitError not raised"})
            all_ok = False
        except ResourceLimitError:
            cases.append({"check": f"invalid_cap_{c}", "status": "PASS"})

    return cases, all_ok


EXECUTABLE_CASE_SPEC: Dict[str, Any] = {
    "contract_version": "dstar-v1.0",
    "resource_guards": {
        "max_data_vertices": 8,
        "max_query_vertices": 6,
        "max_candidates": 100000,
    },
    "lambda_reference": {
        "P4": {"vertices": 4, "edges": 3, "lambda": 1},
        "Star4": {"vertices": 4, "edges": 3, "lambda": 1},
        "C4": {"vertices": 4, "edges": 4, "lambda": 2},
        "C6": {"vertices": 6, "edges": 6, "lambda": 2},
        "Diamond": {"vertices": 4, "edges": 5, "lambda": 2},
        "K4": {"vertices": 4, "edges": 6, "lambda": 3},
        "K3_3": {"vertices": 6, "edges": 9, "lambda": 3},
    },
    "hand_cases": {
        "CASE_HAND_A": {"k": 0, "recipe": "python:triangle_k3_with_tail", "expected_count": 6},
        "CASE_HAND_B": {"k": 0, "query": "tests/fixtures/dstar01/hand_c4_query.json", "data": "tests/fixtures/dstar01/hand_c4_data_chord.json", "expected_count": 8},
        "CASE_HAND_C": {"k": 1, "query": "tests/fixtures/dstar01/hand_diamond_query.json", "recipe_data": "python:diamond_external_tail", "expected_match": [1, 2, 3, 4]},
        "CASE_HAND_D": {"k": 0, "recipe_query": "python:p2", "recipe_data": "python:v10", "expected_rejected": "NON_INJECTIVE"},
        "CASE_HAND_E": {"k": 0, "recipe_query": "python:edge_ab", "recipe_data": "python:edge_ac", "expected_rejected": "VERTEX_LABEL_MISMATCH"},
        "CASE_HAND_F": {"k_values": [0, 1], "recipe_query": "python:triangle_k3", "recipe_data": "python:triangle_k3_label_mismatch"},
        "CASE_HAND_G": {"k": 0, "query": "tests/fixtures/dstar01/hand_sparse_query.json", "data": "tests/fixtures/dstar01/hand_sparse_data.json", "expected_matches": [[100, 200, 300]]},
        "CASE_HAND_H": {"k": 0, "query": "tests/fixtures/dstar01/hand_k4_query.json", "data": "tests/fixtures/dstar01/hand_k4_data.json", "expected_count": 24},
        "CASE_HAND_I": {"k": 1, "query": "tests/fixtures/dstar01/hand_diamond_query.json", "data_before": "tests/fixtures/dstar01/hand_diamond_data_before.json", "stream": "tests/fixtures/dstar01/hand_diamond_stream_event.json"},
        "CASE_HAND_J": {"k": 1, "query": "tests/fixtures/dstar01/hand_diamond_query.json", "data_before": "tests/fixtures/dstar01/hand_diamond_data_full.json", "stream": "tests/fixtures/dstar01/hand_diamond_stream_no_event.json"},
        "CASE_HAND_K": {"k": 1, "query": "tests/fixtures/dstar01/hand_diamond_query.json", "recipe_data": "python:diamond_unrelated"},
        "CASE_HAND_L": {"k": 1, "query": "tests/fixtures/dstar01/hand_s1_vs_s2_query.json", "data": "tests/fixtures/dstar01/hand_s1_vs_s2_data.json", "expected_public_rejection": True},
    },
    "seeded_compatibility": {
        "seed_base": 20261117,
        "total_configurations": 56,
        "min_updates_per_configuration": 4,
        "total_valid_updates": 224,
        "data_vertices": 6,
        "label_modes": ["uniform", "mixed"],
        "configurations": [
            {"query": "C4", "k_values": [0, 1], "seeds_per_k": 8},
            {"query": "Diamond", "k_values": [0, 1], "seeds_per_k": 8},
            {"query": "K4", "k_values": [0, 1, 2], "seeds_per_k": 8},
        ],
    },
}


def reconcile_manifest(
    manifest_path: Path,
    hand_cases: List[Dict[str, Any]],
    lambda_topologies: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], bool]:
    """
    R2-F02: Rigorous, bidirectional reconciliation between manifest and executable specification.
    Fails closed on duplicate IDs, missing/unknown IDs, parameter drift, cap drift,
    matrix drift, and nonexistent fixture paths.
    """
    errors: List[str] = []
    if not manifest_path.is_file():
        return {
            "status": "FAIL",
            "errors": [f"Manifest file not found: {manifest_path}"],
        }, False

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        return {
            "status": "FAIL",
            "errors": [f"Malformed manifest JSON: {e}"],
        }, False

    # 1. Duplicate ID check in hand_cases
    raw_cases = manifest.get("hand_cases", [])
    if not isinstance(raw_cases, list):
        errors.append("manifest 'hand_cases' must be a list")
        raw_cases = []
    case_ids = [c.get("id") for c in raw_cases if isinstance(c, dict) and "id" in c]
    if len(case_ids) != len(set(case_ids)):
        duplicates = [cid for cid in case_ids if case_ids.count(cid) > 1]
        errors.append(f"Duplicate hand_cases IDs found in manifest: {sorted(set(duplicates))}")

    # 2. Missing and unknown hand case IDs
    manifest_hand_set = set(case_ids)
    spec_hand_set = set(EXECUTABLE_CASE_SPEC["hand_cases"].keys())
    missing_in_manifest = spec_hand_set - manifest_hand_set
    if missing_in_manifest:
        errors.append(f"Required hand cases missing from manifest: {sorted(missing_in_manifest)}")
    unknown_in_manifest = manifest_hand_set - spec_hand_set
    if unknown_in_manifest:
        errors.append(f"Unknown hand cases present in manifest: {sorted(unknown_in_manifest)}")

    # 3. Parameter and fixture reconciliation for each hand case
    for c in raw_cases:
        if not isinstance(c, dict):
            errors.append(f"Invalid hand case item in manifest: {c!r}")
            continue
        cid = c.get("id")
        if cid not in EXECUTABLE_CASE_SPEC["hand_cases"]:
            continue
        spec_c = EXECUTABLE_CASE_SPEC["hand_cases"][cid]

        # Check k
        if "k" in spec_c and c.get("k") != spec_c["k"]:
            errors.append(f"Case {cid} k mismatch: manifest={c.get('k')} vs spec={spec_c['k']}")
        if "k_values" in spec_c and c.get("k_values") != spec_c["k_values"]:
            errors.append(f"Case {cid} k_values mismatch: manifest={c.get('k_values')} vs spec={spec_c['k_values']}")

        # Check expected_count
        if "expected_count" in spec_c:
            val = c.get("expected_count")
            if val is None:
                errors.append(f"Case {cid} missing required expected_count in manifest")
            elif type(val) is not int or isinstance(val, bool):
                errors.append(f"Case {cid} expected_count has invalid type: {type(val).__name__} (expected int)")
            elif val != spec_c["expected_count"]:
                errors.append(
                    f"Case {cid} expected_count mismatch: manifest={val} vs spec={spec_c['expected_count']}"
                )
        elif "expected_count" in c:
            errors.append(f"Case {cid} unexpectedly declared expected_count in manifest")

        # R3-F01: Check expected_match (single ordered tuple)
        if "expected_match" in spec_c:
            val = c.get("expected_match")
            if val is None:
                errors.append(f"Case {cid} missing required expected_match in manifest")
            elif not isinstance(val, (list, tuple)) or isinstance(val, (str, bytes)):
                errors.append(f"Case {cid} expected_match has invalid type: {type(val).__name__} (expected list/tuple)")
            elif any(type(x) is not int or isinstance(x, bool) for x in val):
                errors.append(f"Case {cid} expected_match elements must be integers")
            elif list(val) != list(spec_c["expected_match"]):
                errors.append(
                    f"Case {cid} expected_match mismatch: manifest={val} vs spec={spec_c['expected_match']}"
                )
        elif "expected_match" in c:
            errors.append(f"Case {cid} unexpectedly declared expected_match in manifest")

        # R3-F01: Check expected_matches (set of ordered tuples)
        if "expected_matches" in spec_c:
            val = c.get("expected_matches")
            if val is None:
                errors.append(f"Case {cid} missing required expected_matches in manifest")
            elif not isinstance(val, (list, tuple)) or isinstance(val, (str, bytes)):
                errors.append(f"Case {cid} expected_matches has invalid type: {type(val).__name__} (expected list)")
            elif any(not isinstance(sub, (list, tuple)) or any(type(x) is not int or isinstance(x, bool) for x in sub) for sub in val):
                errors.append(f"Case {cid} expected_matches must be a list of lists/tuples of integers")
            elif [list(x) for x in val] != [list(x) for x in spec_c["expected_matches"]]:
                errors.append(
                    f"Case {cid} expected_matches mismatch: manifest={val} vs spec={spec_c['expected_matches']}"
                )
        elif "expected_matches" in c:
            errors.append(f"Case {cid} unexpectedly declared expected_matches in manifest")

        # R3-F01: Check expected_public_rejection (boolean flag)
        if "expected_public_rejection" in spec_c:
            val = c.get("expected_public_rejection")
            if val is None:
                errors.append(f"Case {cid} missing required expected_public_rejection in manifest")
            elif type(val) is not bool:
                errors.append(f"Case {cid} expected_public_rejection has invalid type: {type(val).__name__} (expected bool)")
            elif val != spec_c["expected_public_rejection"]:
                errors.append(
                    f"Case {cid} expected_public_rejection mismatch: manifest={val} vs spec={spec_c['expected_public_rejection']}"
                )
        elif "expected_public_rejection" in c:
            errors.append(f"Case {cid} unexpectedly declared expected_public_rejection in manifest")

        # Check expected_rejected (rejection error reason string)
        if "expected_rejected" in spec_c:
            val = c.get("expected_rejected")
            if val is None:
                errors.append(f"Case {cid} missing required expected_rejected in manifest")
            elif not isinstance(val, str):
                errors.append(f"Case {cid} expected_rejected has invalid type: {type(val).__name__} (expected str)")
            elif val != spec_c["expected_rejected"]:
                errors.append(
                    f"Case {cid} expected_rejected mismatch: manifest={val} vs spec={spec_c['expected_rejected']}"
                )
        elif "expected_rejected" in c:
            errors.append(f"Case {cid} unexpectedly declared expected_rejected in manifest")

        # Check declared fixtures vs recipes
        for field in ("query", "data", "data_before", "stream"):
            if field in spec_c:
                manifest_val = c.get(field)
                if manifest_val != spec_c[field]:
                    errors.append(f"Case {cid} {field} path mismatch: manifest={manifest_val} vs spec={spec_c[field]}")
                elif manifest_val:
                    p = REPO_ROOT / manifest_val
                    if not p.is_file():
                        errors.append(f"Case {cid} {field} fixture missing on disk: {manifest_val}")
            elif field in c:
                errors.append(f"Case {cid} unexpectedly declared {field} in manifest when spec uses python recipe")

        for r_field in ("recipe", "recipe_query", "recipe_data"):
            if r_field in spec_c and c.get(r_field) != spec_c[r_field]:
                errors.append(f"Case {cid} {r_field} mismatch: manifest={c.get(r_field)} vs spec={spec_c[r_field]}")

    # 4. Resource guards caps check
    m_caps = manifest.get("resource_guards", {})
    s_caps = EXECUTABLE_CASE_SPEC["resource_guards"]
    for cap_key in ("max_data_vertices", "max_query_vertices", "max_candidates"):
        if m_caps.get(cap_key) != s_caps[cap_key]:
            errors.append(
                f"Resource cap {cap_key} mismatch: manifest={m_caps.get(cap_key)} vs canonical={s_caps[cap_key]}"
            )

    # 5. Lambda reference topology check
    m_lambda = manifest.get("lambda_reference", {})
    s_lambda = EXECUTABLE_CASE_SPEC["lambda_reference"]
    if set(m_lambda.keys()) != set(s_lambda.keys()):
        errors.append(
            f"Lambda reference topologies mismatch: manifest={sorted(m_lambda.keys())} vs spec={sorted(s_lambda.keys())}"
        )
    for top_name, top_spec in s_lambda.items():
        m_top = m_lambda.get(top_name, {})
        for f_name in ("vertices", "edges", "lambda"):
            if m_top.get(f_name) != top_spec[f_name]:
                errors.append(
                    f"Lambda topology {top_name} {f_name} mismatch: manifest={m_top.get(f_name)} vs spec={top_spec[f_name]}"
                )

    # 6. Seeded compatibility matrix check
    m_seed = manifest.get("seeded_compatibility", {})
    s_seed = EXECUTABLE_CASE_SPEC["seeded_compatibility"]
    for s_key in (
        "seed_base",
        "total_configurations",
        "min_updates_per_configuration",
        "total_valid_updates",
        "data_vertices",
        "label_modes",
    ):
        if m_seed.get(s_key) != s_seed[s_key]:
            errors.append(f"Seeded compatibility {s_key} mismatch: manifest={m_seed.get(s_key)} vs spec={s_seed[s_key]}")
    if m_seed.get("configurations") != s_seed["configurations"]:
        errors.append(
            f"Seeded compatibility configurations matrix mismatch: manifest={m_seed.get('configurations')} vs spec={s_seed['configurations']}"
        )

    # 7. Check executed cases match specification
    executed_hand_ids = {c.get("case_id") for c in hand_cases if isinstance(c, dict)}
    if executed_hand_ids != spec_hand_set:
        errors.append(
            f"Executed hand cases {sorted(executed_hand_ids)} do not match spec {sorted(spec_hand_set)}"
        )
    for c in hand_cases:
        if isinstance(c, dict) and c.get("status") != "PASS":
            errors.append(f"Executed hand case {c.get('case_id')} failed with status {c.get('status')}")
    executed_lambda_names = {c.get("topology") for c in lambda_topologies if isinstance(c, dict)}
    if executed_lambda_names != set(s_lambda.keys()):
        errors.append(
            f"Executed lambda topologies {sorted(executed_lambda_names)} do not match spec {sorted(s_lambda.keys())}"
        )
    for c in lambda_topologies:
        if isinstance(c, dict) and c.get("status") != "PASS":
            errors.append(f"Executed lambda topology {c.get('topology')} failed with status {c.get('status')}")

    is_reconciled = (len(errors) == 0)
    return {
        "status": "PASS" if is_reconciled else "FAIL",
        "manifest_cases_count": len(manifest_hand_set),
        "executed_cases_count": len(executed_hand_ids),
        "errors": errors,
    }, is_reconciled


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DSTAR-01 correctness report")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "reports" / "dstar01" / "correctness_summary.json",
        help="Path to output JSON report",
    )
    args = parser.parse_args()

    run_id = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}"
    t_start = time.time()
    completed_stages: Dict[str, Any] = {}
    current_stage = "INITIALIZATION"
    git_meta: Dict[str, Any] = {}

    fixtures_dir = REPO_ROOT / "tests" / "fixtures" / "dstar01"
    manifest_path = REPO_ROOT / "configs" / "dstar01_cases.json"

    try:
        current_stage = "PROVENANCE_AND_HASHING"
        tracked_source_files = [
            REPO_ROOT / "scripts" / "dstar_oracle.py",
            REPO_ROOT / "scripts" / "generate_dstar01_report.py",
            REPO_ROOT / "tests" / "test_dstar_oracle.py",
            REPO_ROOT / "tests" / "test_dstar_legacy_compatibility.py",
            REPO_ROOT / "tests" / "test_dstar_report_generator.py",
            REPO_ROOT / "configs" / "dstar01_cases.json",
            REPO_ROOT / "docs" / "dstar_problem_contract_v1.md",
        ]
        if fixtures_dir.is_dir():
            for fix_p in sorted(fixtures_dir.glob("*.json")):
                tracked_source_files.append(fix_p)

        source_hashes = {str(p.relative_to(REPO_ROOT)): compute_file_sha256(p) for p in tracked_source_files}
        git_meta = get_git_metadata()

        print("=" * 70)
        print("DSTAR-01 VERIFICATION SUITE — EXECUTING INDEPENDENT M0 ORACLE CHECKS")
        print(f"Run ID: {run_id}")
        print(f"Historical Base: {git_meta['configured_historical_base_sha']}")
        print(f"Execution HEAD: {git_meta['execution_head_sha']} (dirty={git_meta['is_dirty_at_runtime']})")
        print(f"Output Target: {args.output}")
        print("=" * 70)

        # 1. Hand cases A-L
        current_stage = "HAND_CASES"
        hand_cases, hand_ok = run_hand_cases(fixtures_dir)
        completed_stages["hand_cases"] = hand_cases
        print(f"[1/7] Hand cases A-L: {'PASS' if hand_ok else 'FAIL'} ({len(hand_cases)} cases)")

        # 2. Lambda connectivity
        current_stage = "LAMBDA_CHECKS"
        lambda_cases, lambda_ok = run_lambda_checks()
        completed_stages["lambda_topologies"] = lambda_cases
        print(f"[2/7] Lambda(Q) reference checks: {'PASS' if lambda_ok else 'FAIL'} ({len(lambda_cases)} topologies)")

        # 3. Atomic invalid updates
        current_stage = "ATOMIC_UPDATES"
        atomic_cases, atomic_ok = run_atomic_update_checks(fixtures_dir)
        completed_stages["atomic_checks"] = atomic_cases
        print(f"[3/7] Atomic invalid updates: {'PASS' if atomic_ok else 'FAIL'} ({len(atomic_cases)} checks)")

        # 4. Legacy compatibility matrix
        current_stage = "LEGACY_MATRIX"
        matrix_tracker: Dict[str, Any] = {
            "status": "INCOMPLETE",
            "stage": "LEGACY_MATRIX",
            "total_configurations": 0,
            "total_configurations_completed": 0,
            "total_updates": 0,
            "total_updates_attempted": 0,
            "total_updates_completed": 0,
            "total_updates_agreed": 0,
            "completed_steps_count": 0,
            "configs": [],
            "step_records": [],
            "failing_step": None,
        }
        completed_stages["legacy_compatibility"] = matrix_tracker
        global _ACTIVE_MATRIX_TRACKER
        _ACTIVE_MATRIX_TRACKER = matrix_tracker
        try:
            legacy_matrix, legacy_ok = run_legacy_compatibility_matrix()
        finally:
            _ACTIVE_MATRIX_TRACKER = None
        completed_stages["legacy_compatibility"] = legacy_matrix
        print(
            f"[4/7] Legacy compatibility (56 configs, 224 updates): {'PASS' if legacy_ok else 'FAIL'} "
            f"(Entered events={legacy_matrix['total_entered_events']}, Exited events={legacy_matrix['total_exited_events']})"
        )

        # 5. Boundary diagnostics
        current_stage = "BOUNDARY_DIAGNOSTICS"
        boundary_diag, boundary_ok = run_boundary_diagnostics(fixtures_dir)
        completed_stages["boundary_diagnostics"] = boundary_diag
        print(
            f"[5/7] Boundary diagnostics (|R|=k anchor matrix): {'PASS' if boundary_ok else 'FAIL'} "
            f"({len(boundary_diag['cases'])} cases, covering k=0 and k=1)"
        )

        # 6. Resource guard checks
        current_stage = "RESOURCE_GUARDS"
        resource_cases, resource_ok = run_resource_guard_checks()
        completed_stages["resource_guards"] = resource_cases
        print(f"[6/7] Resource guard checks: {'PASS' if resource_ok else 'FAIL'} ({len(resource_cases)} checks)")

        # 7. Manifest reconciliation
        current_stage = "MANIFEST_RECONCILIATION"
        manifest_res, manifest_ok = reconcile_manifest(manifest_path, hand_cases, lambda_cases)
        completed_stages["manifest_reconciliation"] = manifest_res
        print(f"[7/7] Manifest reconciliation: {'PASS' if manifest_ok else 'FAIL'}")

        all_passed = (
            hand_ok
            and lambda_ok
            and atomic_ok
            and legacy_ok
            and boundary_ok
            and resource_ok
            and manifest_ok
        )
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
                "invocation": f"python3 scripts/generate_dstar01_report.py --output {args.output}",
            },
            "overall_status": "PASS" if all_passed else "FAIL",
            "summary": {
                "hand_cases_count": len(hand_cases),
                "hand_cases_passed": sum(1 for c in hand_cases if c["status"] == "PASS"),
                "lambda_topologies_count": len(lambda_cases),
                "lambda_topologies_passed": sum(1 for c in lambda_cases if c["status"] == "PASS"),
                "atomic_checks_count": len(atomic_cases),
                "atomic_checks_passed": sum(1 for c in atomic_cases if c["status"] == "PASS"),
                "legacy_configurations_count": legacy_matrix["total_configurations"],
                "legacy_updates_count": legacy_matrix["total_updates"],
                "legacy_entered_events": legacy_matrix["total_entered_events"],
                "legacy_exited_events": legacy_matrix["total_exited_events"],
                "legacy_entered_tuples": legacy_matrix["total_entered_tuples"],
                "legacy_exited_tuples": legacy_matrix["total_exited_tuples"],
                "boundary_diagnostics_status": boundary_diag["status"],
                "resource_guards_status": "PASS" if resource_ok else "FAIL",
                "manifest_reconciliation_status": manifest_res["status"],
            },
            "hand_cases": hand_cases,
            "lambda_topologies": lambda_cases,
            "atomic_checks": atomic_cases,
            "boundary_diagnostics": boundary_diag,
            "resource_guards": resource_cases,
            "manifest_reconciliation": manifest_res,
            "legacy_compatibility": legacy_matrix,
        }

        # Write report atomically to target output path
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp_output = args.output.with_suffix(".tmp")
        with open(tmp_output, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        tmp_output.replace(args.output)

        print("-" * 70)
        print(f"Summary written to: {args.output}")
        print(f"Overall status: {'PASS' if all_passed else 'FAIL'} in {t_elapsed:.2f}s")
        print("=" * 70)

        if not all_passed:
            sys.exit(1)

    except Exception as e:
        print(f"[FATAL] Uncaught exception during report generation in stage '{current_stage}': {e}", file=sys.stderr)
        err_report: Dict[str, Any] = {
            "contract_version": "dstar-v1.0",
            "run_id": run_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "overall_status": "ERROR",
            "fatal_error": str(e),
            "failing_stage": current_stage,
            "partial_results": completed_stages,
            "provenance": git_meta,
        }
        if isinstance(e, LegacyMatrixExecutionError):
            err_report["config_id"] = e.partial_matrix.get("config_id")
            err_report["query"] = e.partial_matrix.get("query")
            err_report["k"] = e.partial_matrix.get("k")
            err_report["seed"] = e.partial_matrix.get("seed")
            err_report["update_index"] = e.partial_matrix.get("update_index")
            err_report["operation"] = e.partial_matrix.get("operation")
            err_report["endpoints"] = e.partial_matrix.get("endpoints")
            err_report["label"] = e.partial_matrix.get("label")
            err_report["failing_step"] = e.partial_matrix.get("failing_step")
            err_report["completed_steps_count"] = e.partial_matrix.get("total_updates_completed")
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            tmp_err = args.output.with_suffix(".tmp")
            with open(tmp_err, "w", encoding="utf-8") as f:
                json.dump(err_report, f, indent=2)
            tmp_err.replace(args.output)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
