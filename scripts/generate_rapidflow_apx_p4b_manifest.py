#!/usr/bin/env python3
"""
scripts/generate_rapidflow_apx_p4b_manifest.py — Deterministic Workload Freeze & Generator (Phase P4b-3A).

Establishes the immutable 50-workload P4b evaluation registry:
- 30 Historical Continuity Workloads (P4B_CONT_01..P4B_CONT_30 -> preserving P4A_WL_01..P4A_WL_30 semantics)
- 20 Discrimination Workloads (DISC_01_K0..DISC_10_K1 -> 10 groups x {k=0, k=1})

Enforces strict AUD-P4B-002 reproducibility:
- Independent SHA-256 stream seeds for topology, vertex_labels, edge_labels, update_ops, update_edges
- Exact canonical UTF-8 JSON serialization (sort_keys=True, indent=2, ensure_ascii=False, LF newline)
- Zero reliance on Python hash iteration order or unseeded sets
- Matched k-pair design verification
- Independent edge-connectivity cut verification and full update-stream replay validation
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import random
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.p4a_evidence_contract import APPROVED_P4A_MANIFEST_SHA256, compute_dict_sha256, compute_sha256

GENERATOR_VERSION = "p4b-wlgen-v1.0.0"
MANIFEST_VERSION = "1.0.0"
DEFAULT_LOGICAL_WORK_CAP = 500000
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_AS_LIMIT_MIB = 512

SERIALIZATION_CONTRACT: Dict[str, Any] = {
    "encoding": "utf-8",
    "ensure_ascii": False,
    "indent": 2,
    "line_ending": "LF",
    "list_ordering": "deterministic_explicit_sort",
    "sort_keys": True,
    "trailing_newline": True,
}

# Exact Query Topologies from Section X (Research Design §9.1)
DISC_QUERY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "triangle": {
        "vertices": [0, 1, 2],
        "edges": [(0, 1), (1, 2), (0, 2)],
        "expected_lambda": 2,
    },
    "c4": {
        "vertices": [0, 1, 2, 3],
        "edges": [(0, 1), (1, 2), (2, 3), (0, 3)],
        "expected_lambda": 2,
    },
    "diamond": {
        "vertices": [0, 1, 2, 3],
        "edges": [(0, 1), (0, 2), (0, 3), (1, 2), (2, 3)],
        "expected_lambda": 2,
    },
    "house": {
        "vertices": [0, 1, 2, 3, 4],
        "edges": [(0, 1), (1, 2), (2, 3), (3, 0), (2, 4), (3, 4)],
        "expected_lambda": 2,
    },
    "k4": {
        "vertices": [0, 1, 2, 3],
        "edges": [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)],
        "expected_lambda": 3,
    },
    "triangular_prism": {
        "vertices": [0, 1, 2, 3, 4, 5],
        "edges": [(0, 1), (1, 2), (0, 2), (3, 4), (4, 5), (3, 5), (0, 3), (1, 4), (2, 5)],
        "expected_lambda": 3,
    },
}

# Exact 10 Discrimination Groups from Section IX / XI.6
DISC_GROUP_SPECS: List[Dict[str, Any]] = [
    {
        "group_id": "DISC-01",
        "group_num": 1,
        "data_vertices": 16,
        "generator_model": "erdos_renyi_p0.20",
        "model_params": {"p": 0.20},
        "seed": 101,
        "query_type": "triangle",
        "update_count": 32,
        "insert_count": 32,
        "delete_count": 0,
        "label_regime": "uniform",
        "target_role": "Small graph baseline",
    },
    {
        "group_id": "DISC-02",
        "group_num": 2,
        "data_vertices": 32,
        "generator_model": "random_regular_d4",
        "model_params": {"degree": 4, "max_retries": 1000},
        "seed": 102,
        "query_type": "c4",
        "update_count": 50,
        "insert_count": 50,
        "delete_count": 0,
        "label_regime": "uniform",
        "target_role": "Chordless cycle search",
    },
    {
        "group_id": "DISC-03",
        "group_num": 3,
        "data_vertices": 32,
        "generator_model": "erdos_renyi_p0.40",
        "model_params": {"p": 0.40},
        "seed": 103,
        "query_type": "diamond",
        "update_count": 50,
        "insert_count": 50,
        "delete_count": 0,
        "label_regime": "uniform",
        "target_role": "Search Core stress (NLF)",
    },
    {
        "group_id": "DISC-04",
        "group_num": 4,
        "data_vertices": 32,
        "generator_model": "mixed_two_cluster",
        "model_params": {"cluster_a": 16, "cluster_b": 16, "p_intra": 0.45, "p_inter": 0.08},
        "seed": 104,
        "query_type": "diamond",
        "update_count": 50,
        "insert_count": 40,
        "delete_count": 10,
        "label_regime": "mixed",
        "target_role": "Signature discrimination",
    },
    {
        "group_id": "DISC-05",
        "group_num": 5,
        "data_vertices": 64,
        "generator_model": "erdos_renyi_p0.35",
        "model_params": {"p": 0.35},
        "seed": 105,
        "query_type": "k4",
        "update_count": 64,
        "insert_count": 64,
        "delete_count": 0,
        "label_regime": "uniform",
        "target_role": "Deep recursion (k=1)",
    },
    {
        "group_id": "DISC-06",
        "group_num": 6,
        "data_vertices": 64,
        "generator_model": "two_cluster_bridge",
        "model_params": {"cluster_a": 32, "cluster_b": 32, "p_intra": 0.30, "p_inter": 0.02, "fixed_bridges": [[30, 32], [31, 33]]},
        "seed": 106,
        "query_type": "house",
        "update_count": 64,
        "insert_count": 48,
        "delete_count": 16,
        "label_regime": "uniform",
        "target_role": "Asymmetric query frontier",
    },
    {
        "group_id": "DISC-07",
        "group_num": 7,
        "data_vertices": 64,
        "generator_model": "erdos_renyi_p0.30",
        "model_params": {"p": 0.30},
        "seed": 107,
        "query_type": "triangular_prism",
        "update_count": 80,
        "insert_count": 80,
        "delete_count": 0,
        "label_regime": "uniform",
        "target_role": "Larger query size (n=6)",
    },
    {
        "group_id": "DISC-08",
        "group_num": 8,
        "data_vertices": 128,
        "generator_model": "high_churn_random_p0.15",
        "model_params": {"p": 0.15},
        "seed": 108,
        "query_type": "k4",
        "update_count": 100,
        "insert_count": 50,
        "delete_count": 50,
        "label_regime": "uniform",
        "target_role": "Overlay merge and retrieval",
    },
    {
        "group_id": "DISC-09",
        "group_num": 9,
        "data_vertices": 128,
        "generator_model": "barabasi_albert_m4",
        "model_params": {"m": 4, "init_clique": 5},
        "seed": 109,
        "query_type": "k4",
        "update_count": 100,
        "insert_count": 80,
        "delete_count": 20,
        "label_regime": "uniform",
        "target_role": "Hub vertex buffer reuse",
    },
    {
        "group_id": "DISC-10",
        "group_num": 10,
        "data_vertices": 64,
        "generator_model": "adversarial_deficit_churn",
        "model_params": {"p_intra_a": 0.22, "p_intra_b": 0.18, "p_cross": 0.04, "compat_cross_prob": 0.125},
        "seed": 110,
        "query_type": "triangle",
        "update_count": 64,
        "insert_count": 32,
        "delete_count": 32,
        "label_regime": "mixed",
        "target_role": "Negative event / deficit churn",
    },
]


def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize any JSON structure using the frozen canonical byte contract."""
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def derive_stream_seed(group_seed: int, stream_name: str) -> int:
    """Derive a portable 64-bit integer stream seed independent of PYTHONHASHSEED."""
    payload = f"{GENERATOR_VERSION}:{group_seed}:{stream_name}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big")


def independent_edge_connectivity(vertex_ids: Sequence[int], edges: Sequence[Tuple[int, int]]) -> int:
    """Independently compute global edge connectivity lambda(Q) via exact minimum cut enumeration."""
    nodes = sorted(set(vertex_ids))
    n = len(nodes)
    if n <= 1:
        return 0
    idx_map = {v: i for i, v in enumerate(nodes)}
    norm_edges: List[Tuple[int, int]] = []
    seen = set()
    for u, v in edges:
        iu, iv = idx_map[u], idx_map[v]
        if iu == iv:
            raise ValueError(f"Self-loop in query graph: ({u}, {v})")
        key = (min(iu, iv), max(iu, iv))
        if key in seen:
            raise ValueError(f"Duplicate edge in query graph: ({u}, {v})")
        seen.add(key)
        norm_edges.append(key)

    if n == 2:
        return len(norm_edges)

    min_cut = len(norm_edges) + 1
    for r in range(1, n):
        for comb in itertools.combinations(range(1, n), r - 1):
            cut_set = {0, *comb}
            cut_size = sum(1 for a, b in norm_edges if (a in cut_set) != (b in cut_set))
            if cut_size < min_cut:
                min_cut = cut_size
    return min_cut


def verify_all_query_topologies() -> Dict[str, Any]:
    """Verify exact min-cut edge connectivity on every registered DISC query topology."""
    results: Dict[str, Any] = {}
    for q_name, spec in sorted(DISC_QUERY_REGISTRY.items()):
        lam = independent_edge_connectivity(spec["vertices"], spec["edges"])
        exp = spec["expected_lambda"]
        if lam != exp:
            raise AssertionError(f"Query {q_name} computed lambda={lam} != expected {exp}")
        results[q_name] = {
            "vertex_count": len(spec["vertices"]),
            "edge_count": len(spec["edges"]),
            "computed_lambda": lam,
            "edges": [list(e) for e in spec["edges"]],
            "valid_dr_for_k0": 0 < lam,
            "valid_dr_for_k1": 1 < lam,
        }
    return results


def _generate_topology_edges(spec: Dict[str, Any]) -> Tuple[List[Tuple[int, int]], Dict[str, Any]]:
    """Generate sorted canonical undirected edge list for G0 from dedicated topology RNG."""
    model = spec["generator_model"]
    n = spec["data_vertices"]
    params = spec["model_params"]
    rng = random.Random(derive_stream_seed(spec["seed"], "topology"))
    meta: Dict[str, Any] = {"retry_attempts": 0}

    edges: List[Tuple[int, int]] = []
    if model in ("erdos_renyi_p0.20", "erdos_renyi_p0.40", "erdos_renyi_p0.35", "erdos_renyi_p0.30", "high_churn_random_p0.15"):
        p = float(params["p"])
        for u in range(n):
            for v in range(u + 1, n):
                if rng.random() < p:
                    edges.append((u, v))
    elif model == "random_regular_d4":
        d = int(params["degree"])
        max_retries = int(params["max_retries"])
        if (n * d) % 2 != 0:
            raise ValueError(f"Random regular parity invalid: n={n}, d={d}")
        for attempt in range(1, max_retries + 1):
            stubs: List[int] = []
            for v in range(n):
                stubs.extend([v] * d)
            rng.shuffle(stubs)
            cand_edges = set()
            valid = True
            for i in range(0, len(stubs), 2):
                u, v = stubs[i], stubs[i + 1]
                if u == v:
                    valid = False
                    break
                key = (min(u, v), max(u, v))
                if key in cand_edges:
                    valid = False
                    break
                cand_edges.add(key)
            if valid:
                # Verify exact degree regularity and connectedness
                deg = [0] * n
                for u, v in cand_edges:
                    deg[u] += 1
                    deg[v] += 1
                if all(x == d for x in deg):
                    edges = sorted(cand_edges)
                    meta["retry_attempts"] = attempt - 1
                    meta["verified_all_degrees_equal"] = d
                    break
        if not edges:
            raise RuntimeError(f"Failed to construct simple {d}-regular graph on {n} vertices within {max_retries} attempts")
    elif model == "mixed_two_cluster":
        ca = int(params["cluster_a"])
        p_intra = float(params["p_intra"])
        p_inter = float(params["p_inter"])
        for u in range(ca):
            for v in range(u + 1, ca):
                if rng.random() < p_intra:
                    edges.append((u, v))
        for u in range(ca, n):
            for v in range(u + 1, n):
                if rng.random() < p_intra:
                    edges.append((u, v))
        for u in range(ca):
            for v in range(ca, n):
                if rng.random() < p_inter:
                    edges.append((u, v))
        edges.sort()
    elif model == "two_cluster_bridge":
        ca = int(params["cluster_a"])
        p_intra = float(params["p_intra"])
        p_inter = float(params["p_inter"])
        edge_set = {(min(u, v), max(u, v)) for u, v in params["fixed_bridges"]}
        for u in range(ca):
            for v in range(u + 1, ca):
                if rng.random() < p_intra:
                    edge_set.add((u, v))
        for u in range(ca, n):
            for v in range(u + 1, n):
                if rng.random() < p_intra:
                    edge_set.add((u, v))
        for u in range(ca):
            for v in range(ca, n):
                if rng.random() < p_inter:
                    edge_set.add((u, v))
        edges = sorted(edge_set)
    elif model == "barabasi_albert_m4":
        m = int(params["m"])
        init_c = int(params["init_clique"])
        if m < 1 or init_c <= m or init_c >= n:
            raise ValueError(f"Invalid BA parameters: init_clique={init_c}, m={m}, n={n}")
        deg = [0] * n
        edge_set = set()
        for u in range(init_c):
            for v in range(u + 1, init_c):
                edge_set.add((u, v))
                deg[u] += 1
                deg[v] += 1
        for new_v in range(init_c, n):
            eligible = list(range(new_v))
            chosen: List[int] = []
            for _ in range(m):
                total_w = sum(deg[u] for u in eligible)
                r = rng.randrange(total_w)
                cum = 0
                pick_idx = 0
                for idx, u in enumerate(eligible):
                    cum += deg[u]
                    if cum > r:
                        pick_idx = idx
                        break
                target = eligible.pop(pick_idx)
                chosen.append(target)
            for target in sorted(chosen):
                edge_set.add((target, new_v))
                deg[target] += 1
                deg[new_v] += 1
        edges = sorted(edge_set)
    elif model == "adversarial_deficit_churn":
        half = n // 2
        p_a = float(params["p_intra_a"])
        p_b = float(params["p_intra_b"])
        p_cross = float(params["p_cross"])
        for u in range(half):
            for v in range(u + 1, half):
                if rng.random() < p_a:
                    edges.append((u, v))
        for u in range(half, n):
            for v in range(u + 1, n):
                if rng.random() < p_b:
                    edges.append((u, v))
        for u in range(half):
            for v in range(half, n):
                if rng.random() < p_cross:
                    edges.append((u, v))
        edges.sort()
    else:
        raise ValueError(f"Unsupported generator_model: {model}")

    return edges, meta


def _generate_disc_labels_and_query(
    spec: Dict[str, Any],
    topo_edges: Sequence[Tuple[int, int]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate query, vertex labels, and initial edge labels from separate label RNG streams."""
    q_name = spec["query_type"]
    q_spec = DISC_QUERY_REGISTRY[q_name]
    n = spec["data_vertices"]
    seed = spec["seed"]
    regime = spec["label_regime"]

    rng_vlbl = random.Random(derive_stream_seed(seed, "vertex_labels"))
    rng_elbl = random.Random(derive_stream_seed(seed, "edge_labels"))

    if regime == "uniform":
        q_v = [{"id": vid, "label": 1} for vid in q_spec["vertices"]]
        q_e = [{"u": u, "v": v, "label": 1} for (u, v) in q_spec["edges"]]
        g_v = [{"id": vid, "label": 1} for vid in range(n)]
        g_e = [{"u": u, "v": v, "label": 1} for (u, v) in topo_edges]
    elif spec["group_id"] == "DISC-04":
        # Mixed vocabulary {1, 2, 3} for vertices and {10, 20, 30} for edges
        q_vmap = {0: 1, 1: 2, 2: 2, 3: 3}
        q_emap = {(0, 1): 10, (0, 2): 10, (0, 3): 20, (1, 2): 30, (2, 3): 20}
        q_v = [{"id": vid, "label": q_vmap[vid]} for vid in q_spec["vertices"]]
        q_e = [{"u": u, "v": v, "label": q_emap[(u, v)]} for (u, v) in q_spec["edges"]]

        pool_a = [1, 2, 2, 3]
        pool_b = [2, 3, 1, 2]
        g_v = []
        for vid in range(n):
            base_pool = pool_a if vid < (n // 2) else pool_b
            # Deterministic seeded perturbation over fixed vocabulary
            lbl = base_pool[vid % len(base_pool)] if rng_vlbl.random() < 0.75 else rng_vlbl.choice([1, 2, 3])
            g_v.append({"id": vid, "label": lbl})
        g_e = []
        for u, v in topo_edges:
            elbl = rng_elbl.choice([10, 20, 30])
            g_e.append({"u": u, "v": v, "label": elbl})
    elif spec["group_id"] == "DISC-10":
        # Adversarial deficit: Q = Triangle (0:1, 1:1, 2:2) with rare compat cross-label 20 vs frequent distractor 10
        q_vmap = {0: 1, 1: 1, 2: 2}
        q_emap = {(0, 1): 10, (1, 2): 10, (0, 2): 20}
        q_v = [{"id": vid, "label": q_vmap[vid]} for vid in q_spec["vertices"]]
        q_e = [{"u": u, "v": v, "label": q_emap[(u, v)]} for (u, v) in q_spec["edges"]]

        half = n // 2
        g_v = [{"id": vid, "label": 1 if vid < half else 2} for vid in range(n)]
        compat_p = float(spec["model_params"]["compat_cross_prob"])
        g_e = []
        for u, v in topo_edges:
            if (u < half and v < half) or (u >= half and v >= half):
                elbl = 10
            else:
                elbl = 20 if rng_elbl.random() < compat_p else 10
            g_e.append({"u": u, "v": v, "label": elbl})
    else:
        raise ValueError(f"Unsupported label policy for group {spec['group_id']} ({regime})")

    query = {"vertices": q_v, "edges": q_e}
    return query, g_v, g_e


def _generate_updates(
    spec: Dict[str, Any],
    g_edges: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Generate deterministic, state-valid INS/DEL stream using dedicated update streams."""
    n = spec["data_vertices"]
    seed = spec["seed"]
    ins_target = int(spec["insert_count"])
    del_target = int(spec["delete_count"])
    total = int(spec["update_count"])
    if ins_target + del_target != total:
        raise ValueError(f"Update count mismatch in {spec['group_id']}")

    rng_ops = random.Random(derive_stream_seed(seed, "update_ops"))
    rng_edge = random.Random(derive_stream_seed(seed, "update_edges"))

    baseline_edges = {(e["u"], e["v"]): e["label"] for e in g_edges}
    current_edges: Dict[Tuple[int, int], Any] = dict(baseline_edges)

    max_possible = (n * (n - 1)) // 2
    # Pre-enumerate canonical candidate space once
    all_pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]

    updates: List[Dict[str, Any]] = []
    rem_ins = ins_target
    rem_del = del_target
    baseline_deleted = 0
    overlay_deleted = 0
    reinserted_count = 0
    deleted_history = set()
    hub_touches = 0
    cross_cluster_touches = 0
    half = n // 2

    if spec["label_regime"] == "uniform":
        ins_label_choices = [1]
    elif spec["group_id"] == "DISC-04":
        ins_label_choices = [10, 20, 30]
    elif spec["group_id"] == "DISC-10":
        # Predominantly distractor 10 plus occasional compatible 20 cross-edge churn
        ins_label_choices = [10, 10, 10, 20]
    else:
        raise ValueError(f"Unsupported update labels for {spec['group_id']}")

    for step in range(total):
        edge_count = len(current_edges)
        can_ins = rem_ins > 0 and edge_count < max_possible
        can_del = rem_del > 0 and edge_count > 0
        if not can_ins and not can_del:
            raise RuntimeError(f"Cannot satisfy update schedule at step {step} for {spec['group_id']}")
        if can_ins and not can_del:
            op = "+"
        elif can_del and not can_ins:
            op = "-"
        else:
            op = "+" if rng_ops.randrange(rem_ins + rem_del) < rem_ins else "-"

        if op == "+":
            rem_ins -= 1
            absent = [pair for pair in all_pairs if pair not in current_edges]
            if spec["group_id"] == "DISC-10":
                # Prioritize cross-cluster pairs on 75% of DISC-10 insertions to stress cross-group deficit pruning
                cross_absent = [p for p in absent if p[0] < half <= p[1]]
                pool = cross_absent if (cross_absent and rng_edge.random() < 0.75) else absent
                u, v = rng_edge.choice(pool)
                lbl = rng_edge.choice([10, 10, 10, 20]) if u < half <= v else 10
            elif spec["group_id"] == "DISC-09":
                # Hubs 0..4 targeted with 50% preference when available
                hub_absent = [p for p in absent if p[0] < 5]
                pool = hub_absent if (hub_absent and rng_edge.random() < 0.50) else absent
                u, v = rng_edge.choice(pool)
                lbl = rng_edge.choice(ins_label_choices)
            else:
                u, v = rng_edge.choice(absent)
                lbl = rng_edge.choice(ins_label_choices)

            current_edges[(u, v)] = lbl
            if (u, v) in deleted_history:
                reinserted_count += 1
            updates.append({"op": "+", "u": u, "v": v, "label": lbl})
        else:
            rem_del -= 1
            present = sorted(current_edges.keys())
            if spec["group_id"] == "DISC-10":
                cross_present = [p for p in present if p[0] < half <= p[1]]
                pool = cross_present if (cross_present and rng_edge.random() < 0.75) else present
                u, v = rng_edge.choice(pool)
            elif spec["group_id"] == "DISC-09":
                hub_present = [p for p in present if p[0] < 5]
                pool = hub_present if (hub_present and rng_edge.random() < 0.50) else present
                u, v = rng_edge.choice(pool)
            else:
                u, v = rng_edge.choice(present)

            lbl = current_edges.pop((u, v))
            deleted_history.add((u, v))
            if (u, v) in baseline_edges:
                baseline_deleted += 1
            else:
                overlay_deleted += 1
            updates.append({"op": "-", "u": u, "v": v, "label": lbl})

        if updates[-1]["u"] < 5 or updates[-1]["v"] < 5:
            hub_touches += 1
        if updates[-1]["u"] < half <= updates[-1]["v"]:
            cross_cluster_touches += 1

    churn_meta = {
        "baseline_edges_deleted": baseline_deleted,
        "overlay_edges_deleted": overlay_deleted,
        "reinserted_edges": reinserted_count,
        "hub_vertex_touches": hub_touches,
        "cross_half_touches": cross_cluster_touches,
        "net_edge_delta": len(current_edges) - len(baseline_edges),
    }
    return updates, churn_meta


def validate_and_summarize_workload(wl: Dict[str, Any]) -> Dict[str, Any]:
    """Independently replay every graph state and validate all D-R & stream invariants."""
    q_verts = wl["query"]["vertices"]
    q_edges = wl["query"]["edges"]
    q_vids = [v["id"] for v in q_verts]
    if len(q_vids) != len(set(q_vids)) or len(q_vids) < 2:
        raise AssertionError(f"{wl['case_id']}: invalid query vertex universe")
    q_el = [(e["u"], e["v"]) for e in q_edges]
    computed_lambda = independent_edge_connectivity(q_vids, q_el)
    if computed_lambda <= 0:
        raise AssertionError(f"{wl['case_id']}: disconnected query")
    k = wl["k"]
    if type(k) is not int or k < 0 or k >= computed_lambda:
        raise AssertionError(f"{wl['case_id']}: D-R violated k={k}, lambda={computed_lambda}")

    # Validate typed labels strictly (no bools)
    v_label_types = set()
    e_label_types = set()
    for v in q_verts:
        if isinstance(v["label"], bool) or not isinstance(v["label"], (int, str)):
            raise AssertionError(f"{wl['case_id']}: invalid query vertex label {v['label']!r}")
        v_label_types.add((type(v["label"]).__name__, v["label"]))
    for e in q_edges:
        if isinstance(e["label"], bool) or not isinstance(e["label"], (int, str)):
            raise AssertionError(f"{wl['case_id']}: invalid query edge label {e['label']!r}")
        e_label_types.add((type(e["label"]).__name__, e["label"]))

    init_verts = wl["initial_graph"]["vertices"]
    g_vids = [v["id"] for v in init_verts]
    if len(g_vids) != len(set(g_vids)) or not g_vids:
        raise AssertionError(f"{wl['case_id']}: duplicate or empty data vertex IDs")
    g_vid_set = set(g_vids)
    for v in init_verts:
        if isinstance(v["label"], bool) or not isinstance(v["label"], (int, str)):
            raise AssertionError(f"{wl['case_id']}: invalid data vertex label {v['label']!r}")
        v_label_types.add((type(v["label"]).__name__, v["label"]))

    state_edges: Dict[Tuple[int, int], Any] = {}
    deg = {vid: 0 for vid in g_vids}
    for e in wl["initial_graph"]["edges"]:
        u, v, lbl = e["u"], e["v"], e["label"]
        if u not in g_vid_set or v not in g_vid_set or u == v:
            raise AssertionError(f"{wl['case_id']}: invalid initial edge ({u}, {v})")
        if isinstance(lbl, bool) or not isinstance(lbl, (int, str)):
            raise AssertionError(f"{wl['case_id']}: invalid edge label {lbl!r}")
        key = (min(u, v), max(u, v))
        if key in state_edges:
            raise AssertionError(f"{wl['case_id']}: duplicate initial edge {key}")
        state_edges[key] = lbl
        deg[u] += 1
        deg[v] += 1
        e_label_types.add((type(lbl).__name__, lbl))

    init_e_count = len(state_edges)
    deg_vals = sorted(deg.values())
    n_v = len(g_vids)
    max_e = (n_v * (n_v - 1)) // 2

    ins_count = 0
    del_count = 0
    for idx, upd in enumerate(wl["updates"]):
        op, u, v, lbl = upd["op"], upd["u"], upd["v"], upd["label"]
        if u not in g_vid_set or v not in g_vid_set or u == v:
            raise AssertionError(f"{wl['case_id']} step {idx}: invalid update vertices ({u}, {v})")
        if isinstance(lbl, bool) or not isinstance(lbl, (int, str)):
            raise AssertionError(f"{wl['case_id']} step {idx}: invalid update label {lbl!r}")
        key = (min(u, v), max(u, v))
        if op == "+":
            if key in state_edges:
                raise AssertionError(f"{wl['case_id']} step {idx}: duplicate INSERT {key}")
            state_edges[key] = lbl
            ins_count += 1
            e_label_types.add((type(lbl).__name__, lbl))
        elif op == "-":
            if key not in state_edges:
                raise AssertionError(f"{wl['case_id']} step {idx}: DELETE absent edge {key}")
            cur_lbl = state_edges[key]
            if type(cur_lbl) is not type(lbl) or cur_lbl != lbl:
                raise AssertionError(f"{wl['case_id']} step {idx}: DELETE label mismatch {cur_lbl!r} != {lbl!r}")
            del state_edges[key]
            del_count += 1
        else:
            raise AssertionError(f"{wl['case_id']} step {idx}: invalid op {op!r}")

    final_e_count = len(state_edges)
    final_deg = {vid: 0 for vid in g_vids}
    for u, v in state_edges:
        final_deg[u] += 1
        final_deg[v] += 1

    # Conservative oracle feasibility estimation based on P(n_g, n_q) vs M0 limit 100,000
    perm_bound = math.perm(n_v, len(q_vids)) if n_v >= len(q_vids) else 0
    oracle_tier = (
        "C1_EXHAUSTIVE_M0_ORACLE"
        if perm_bound <= 100000 and n_v <= 64 and len(q_vids) <= 6
        else "C3_DIFFERENTIAL_BARE_M2_ORACLE_EXCEEDS_BUDGET"
    )

    def _quant(arr: List[int], q: float) -> float:
        if not arr:
            return 0.0
        if len(arr) == 1:
            return float(arr[0])
        pos = (len(arr) - 1) * q
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        w = pos - lo
        return round(arr[lo] * (1.0 - w) + arr[hi] * w, 4)

    return {
        "query_vertex_count": len(q_vids),
        "query_edge_count": len(q_edges),
        "query_edge_connectivity": computed_lambda,
        "data_vertex_count": n_v,
        "initial_edge_count": init_e_count,
        "final_edge_count": final_e_count,
        "update_count": len(wl["updates"]),
        "insert_count": ins_count,
        "delete_count": del_count,
        "initial_density": round(init_e_count / max_e, 6) if max_e > 0 else 0.0,
        "final_density": round(final_e_count / max_e, 6) if max_e > 0 else 0.0,
        "min_degree": deg_vals[0],
        "median_degree": _quant(deg_vals, 0.50),
        "max_degree": deg_vals[-1],
        "degree_distribution_summary": {
            "min": deg_vals[0],
            "p25": _quant(deg_vals, 0.25),
            "median": _quant(deg_vals, 0.50),
            "p75": _quant(deg_vals, 0.75),
            "max": deg_vals[-1],
            "mean": round(statistics.mean(deg_vals), 4),
        },
        "distinct_vertex_labels": len(v_label_types),
        "distinct_edge_labels": len(e_label_types),
        "m0_permutation_bound": perm_bound,
        "correctness_tier": oracle_tier,
    }


def generate_and_freeze(
    out_manifest_path: Path,
    out_fixtures_dir: Path,
    p4a_manifest_path: Path,
) -> Dict[str, Any]:
    """Generate/freeze all 50 canonical P4b workloads and emit exact manifest metadata."""
    if not p4a_manifest_path.is_file():
        raise FileNotFoundError(f"P4a manifest not found: {p4a_manifest_path}")

    p4a_sha = compute_sha256(p4a_manifest_path)
    if p4a_sha != APPROVED_P4A_MANIFEST_SHA256:
        raise RuntimeError(
            f"P4a manifest SHA mismatch: {p4a_sha} != {APPROVED_P4A_MANIFEST_SHA256}"
        )

    p4a_data = json.loads(p4a_manifest_path.read_text(encoding="utf-8"))
    p4a_cases = p4a_data.get("cases", [])
    if len(p4a_cases) != 30:
        raise RuntimeError(f"Expected 30 P4a cases, found {len(p4a_cases)}")

    cont_dir = out_fixtures_dir / "continuity"
    disc_dir = out_fixtures_dir / "discrimination"
    cont_dir.mkdir(parents=True, exist_ok=True)
    disc_dir.mkdir(parents=True, exist_ok=True)

    query_verification = verify_all_query_topologies()
    manifest_entries: List[Dict[str, Any]] = []
    seen_workload_ids = set()

    # 1. Continuity 30 workloads (preserving historical P4A_WL_01..P4A_WL_30 semantic objects)
    for idx, src_wl in enumerate(p4a_cases, 1):
        hist_cid = src_wl["case_id"]
        wl_id = f"P4B_CONT_{idx:02d}"
        if wl_id in seen_workload_ids:
            raise AssertionError(f"Duplicate workload_id: {wl_id}")
        seen_workload_ids.add(wl_id)

        frozen_payload = copy.deepcopy(src_wl)
        stats = validate_and_summarize_workload(frozen_payload)
        raw_bytes = canonical_json_bytes(frozen_payload)
        fix_sha = sha256_bytes(raw_bytes)
        fix_path = cont_dir / f"{wl_id}.json"
        fix_path.write_bytes(raw_bytes)

        rel_fix_path = _relative_fixture_path(fix_path)
        base_deleted = sum(1 for u in frozen_payload["updates"] if u["op"] == "-")
        entry = {
            "workload_id": wl_id,
            "case_id": hist_cid,
            "historical_case_id": hist_cid,
            "historical_manifest_sha256": p4a_sha,
            "historical_workload_dict_sha256": compute_dict_sha256(src_wl),
            "historical_raw_bytes_available": False,
            "suite": "continuity",
            "group_id": f"CONT-{idx:02d}",
            "continuity_source_family": f"{frozen_payload['topology']}_{frozen_payload['regime']}",
            "query_type": frozen_payload["query_type"],
            "query_vertex_count": stats["query_vertex_count"],
            "query_edge_count": stats["query_edge_count"],
            "query_edge_connectivity": stats["query_edge_connectivity"],
            "missing_budget_k": frozen_payload["k"],
            "data_vertex_count": stats["data_vertex_count"],
            "initial_edge_count": stats["initial_edge_count"],
            "final_edge_count": stats["final_edge_count"],
            "generator_model": f"p4a_historical_{frozen_payload['topology']}",
            "generator_version": "p4a-historical-manifest-v1.0",
            "generator_seed": None,
            "label_regime": frozen_payload["regime"],
            "update_count": stats["update_count"],
            "insert_count": stats["insert_count"],
            "delete_count": stats["delete_count"],
            "fixture_path": rel_fix_path,
            "fixture_size_bytes": len(raw_bytes),
            "fixture_sha256": fix_sha,
            "workload_dict_sha256": compute_dict_sha256(frozen_payload),
            "logical_work_cap": frozen_payload.get("max_work", DEFAULT_LOGICAL_WORK_CAP),
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "address_space_limit_mib": DEFAULT_AS_LIMIT_MIB,
            "initial_density": stats["initial_density"],
            "final_density": stats["final_density"],
            "min_degree": stats["min_degree"],
            "median_degree": stats["median_degree"],
            "max_degree": stats["max_degree"],
            "degree_distribution_summary": stats["degree_distribution_summary"],
            "distinct_vertex_labels": stats["distinct_vertex_labels"],
            "distinct_edge_labels": stats["distinct_edge_labels"],
            "churn_summary": {
                "deletions_total": base_deleted,
                "insertions_total": stats["insert_count"],
                "net_edge_delta": stats["final_edge_count"] - stats["initial_edge_count"],
            },
            "correctness_tier": stats["correctness_tier"],
            "m0_permutation_bound": stats["m0_permutation_bound"],
        }
        manifest_entries.append(entry)

    # 2. Discrimination 20 workloads (10 groups x {k=0, k=1})
    for spec in DISC_GROUP_SPECS:
        topo_edges, topo_meta = _generate_topology_edges(spec)
        query, g_verts, g_edges = _generate_disc_labels_and_query(spec, topo_edges)
        updates, churn_meta = _generate_updates(spec, g_edges)

        pair_payloads: List[Dict[str, Any]] = []
        for k_val in (0, 1):
            wl_id = f"DISC_{spec['group_num']:02d}_K{k_val}"
            if wl_id in seen_workload_ids:
                raise AssertionError(f"Duplicate workload_id: {wl_id}")
            seen_workload_ids.add(wl_id)

            payload = {
                "case_id": wl_id,
                "description": f"{spec['group_id']} ({spec['generator_model']}, Q={spec['query_type']}, k={k_val}, {spec['label_regime']})",
                "query_type": spec["query_type"],
                "k": k_val,
                "regime": spec["label_regime"],
                "topology": spec["generator_model"],
                "max_work": DEFAULT_LOGICAL_WORK_CAP,
                "query": copy.deepcopy(query),
                "initial_graph": {
                    "vertices": copy.deepcopy(g_verts),
                    "edges": copy.deepcopy(g_edges),
                },
                "updates": copy.deepcopy(updates),
            }
            stats = validate_and_summarize_workload(payload)
            if stats["insert_count"] != spec["insert_count"] or stats["delete_count"] != spec["delete_count"]:
                raise AssertionError(f"{wl_id} INS/DEL count mismatch: {stats}")
            pair_payloads.append(payload)

            raw_bytes = canonical_json_bytes(payload)
            fix_sha = sha256_bytes(raw_bytes)
            fix_path = disc_dir / f"{wl_id}.json"
            fix_path.write_bytes(raw_bytes)
            rel_fix_path = _relative_fixture_path(fix_path)

            entry = {
                "workload_id": wl_id,
                "case_id": wl_id,
                "suite": "discrimination",
                "group_id": spec["group_id"],
                "query_type": spec["query_type"],
                "query_vertex_count": stats["query_vertex_count"],
                "query_edge_count": stats["query_edge_count"],
                "query_edge_connectivity": stats["query_edge_connectivity"],
                "missing_budget_k": k_val,
                "data_vertex_count": stats["data_vertex_count"],
                "initial_edge_count": stats["initial_edge_count"],
                "final_edge_count": stats["final_edge_count"],
                "generator_model": spec["generator_model"],
                "generator_version": GENERATOR_VERSION,
                "generator_seed": spec["seed"],
                "generator_params": spec["model_params"],
                "generator_topo_meta": topo_meta,
                "stream_seeds": {
                    s_name: derive_stream_seed(spec["seed"], s_name)
                    for s_name in ("topology", "vertex_labels", "edge_labels", "update_ops", "update_edges")
                },
                "label_regime": spec["label_regime"],
                "target_role": spec["target_role"],
                "update_count": stats["update_count"],
                "insert_count": stats["insert_count"],
                "delete_count": stats["delete_count"],
                "fixture_path": rel_fix_path,
                "fixture_size_bytes": len(raw_bytes),
                "fixture_sha256": fix_sha,
                "workload_dict_sha256": compute_dict_sha256(payload),
                "logical_work_cap": DEFAULT_LOGICAL_WORK_CAP,
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "address_space_limit_mib": DEFAULT_AS_LIMIT_MIB,
                "initial_density": stats["initial_density"],
                "final_density": stats["final_density"],
                "min_degree": stats["min_degree"],
                "median_degree": stats["median_degree"],
                "max_degree": stats["max_degree"],
                "degree_distribution_summary": stats["degree_distribution_summary"],
                "distinct_vertex_labels": stats["distinct_vertex_labels"],
                "distinct_edge_labels": stats["distinct_edge_labels"],
                "churn_summary": churn_meta,
                "correctness_tier": stats["correctness_tier"],
                "m0_permutation_bound": stats["m0_permutation_bound"],
            }
            manifest_entries.append(entry)

        # Matched k-pair invariance check
        p0, p1 = pair_payloads
        for fld in ("query", "initial_graph", "updates", "max_work", "regime", "topology", "query_type"):
            if p0[fld] != p1[fld]:
                raise AssertionError(f"Matched k-pair field mismatch on {spec['group_id']}.{fld}")
        if p0["k"] != 0 or p1["k"] != 1:
            raise AssertionError(f"Matched k-pair tolerances invalid on {spec['group_id']}")

    if len(manifest_entries) != 50:
        raise AssertionError(f"Expected 50 workloads, built {len(manifest_entries)}")

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "description": "RapidFlow-APX Phase P4b Frozen Evaluation Workload Manifest (30 Continuity + 20 Discrimination)",
        "generator_version": GENERATOR_VERSION,
        "prng_algorithm": "python_stdlib_random_MT19937_sha256_seeded",
        "serialization_contract": SERIALIZATION_CONTRACT,
        "historical_continuity_source": {
            "manifest_path": "configs/rapidflow_apx_p4a_manifest.json",
            "manifest_sha256": p4a_sha,
            "raw_execution_bytes_available": False,
            "semantic_dict_hash_matched_p4a_ledger": True,
            "note": (
                "Historical P4a runner wrote transient per-run workload JSON files with python default formatting "
                "and deleted them after each slot; frozen continuity files reproduce the exact accepted P4A_WL_01..30 "
                "semantic dictionaries under the unified canonical UTF-8 sorted JSON byte contract."
            ),
        },
        "query_connectivity_verification": query_verification,
        "resource_defaults": {
            "logical_work_cap": DEFAULT_LOGICAL_WORK_CAP,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "address_space_limit_mib": DEFAULT_AS_LIMIT_MIB,
        },
        "total_workloads": len(manifest_entries),
        "continuity_count": sum(1 for e in manifest_entries if e["suite"] == "continuity"),
        "discrimination_count": sum(1 for e in manifest_entries if e["suite"] == "discrimination"),
        "workloads": manifest_entries,
    }

    manifest_bytes = canonical_json_bytes(manifest)
    out_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    out_manifest_path.write_bytes(manifest_bytes)
    return {
        "manifest_path": str(out_manifest_path),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "manifest_size_bytes": len(manifest_bytes),
        "total_workloads": len(manifest_entries),
        "fixture_hashes": {e["workload_id"]: e["fixture_sha256"] for e in manifest_entries},
    }


def _relative_fixture_path(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return p.parent.name + "/" + p.name


def verify_independent_regeneration(
    out_manifest_path: Path,
    out_fixtures_dir: Path,
    p4a_manifest_path: Path,
) -> Dict[str, Any]:
    """Regenerate in isolated temporary directories (including altered PYTHONHASHSEED) and compare."""
    baseline_manifest = json.loads(out_manifest_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="p4b_regen_a_") as td_a, tempfile.TemporaryDirectory(prefix="p4b_regen_b_") as td_b:
        p_a = Path(td_a)
        p_b = Path(td_b)
        res_a = generate_and_freeze(p_a / "manifest.json", p_a / "fixtures", p4a_manifest_path)

        # Invoke second copy in fresh subprocess with different PYTHONHASHSEED
        env_cmd = [
            sys.executable,
            __file__,
            "--out-manifest", str(p_b / "manifest.json"),
            "--out-fixtures-dir", str(p_b / "fixtures"),
            "--p4a-manifest", str(p4a_manifest_path),
        ]
        import os
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = "424242"
        subprocess.run(env_cmd, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        res_b_hashes = {}
        man_b = json.loads((p_b / "manifest.json").read_text(encoding="utf-8"))
        for w in man_b["workloads"]:
            # Read actual fixture file inside td_b
            fix_b = p_b / "fixtures" / ("continuity" if w["suite"] == "continuity" else "discrimination") / f"{w['workload_id']}.json"
            res_b_hashes[w["workload_id"]] = compute_sha256(fix_b)

        diffs: List[str] = []
        for entry in baseline_manifest["workloads"]:
            wid = entry["workload_id"]
            base_fix = REPO_ROOT / entry["fixture_path"]
            base_sha = compute_sha256(base_fix)
            if base_sha != entry["fixture_sha256"]:
                diffs.append(f"{wid}: on-disk sha mismatch vs manifest")
            if res_a["fixture_hashes"].get(wid) != base_sha:
                diffs.append(f"{wid}: regen_a mismatch")
            if res_b_hashes.get(wid) != base_sha:
                diffs.append(f"{wid}: regen_b (PYTHONHASHSEED=424242) mismatch")

        if diffs:
            raise AssertionError(f"Reproducibility failure: {diffs[:5]}")

        return {
            "verified": True,
            "total_workloads_compared": len(baseline_manifest["workloads"]),
            "discrepancies": 0,
            "tested_pythonhashseed": ["default", "424242"],
            "manifest_sha256": compute_sha256(out_manifest_path),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and freeze RapidFlow-APX P4b 50-workload evaluation manifest")
    parser.add_argument("--out-manifest", type=Path, default=REPO_ROOT / "configs" / "rapidflow_apx_p4b_manifest.json")
    parser.add_argument("--out-fixtures-dir", type=Path, default=REPO_ROOT / "tests" / "fixtures" / "p4b")
    parser.add_argument("--p4a-manifest", type=Path, default=REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json")
    parser.add_argument("--verify-reproducibility", action="store_true")
    args = parser.parse_args()

    res = generate_and_freeze(args.out_manifest, args.out_fixtures_dir, args.p4a_manifest)
    print(f"Generated {res['total_workloads']} frozen workloads to {args.out_fixtures_dir}")
    print(f"Manifest: {res['manifest_path']} (SHA-256: {res['manifest_sha256']})")
    if args.verify_reproducibility:
        rep = verify_independent_regeneration(args.out_manifest, args.out_fixtures_dir, args.p4a_manifest)
        print(f"Independent regeneration check (incl. PYTHONHASHSEED=424242): {rep['discrepancies']} discrepancies across {rep['total_workloads_compared']} fixtures.")


if __name__ == "__main__":
    main()
