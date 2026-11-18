#!/usr/bin/env python3
"""
scripts/generate_dstar03_workloads.py — Deterministic Generator for DSTAR-03 Pilot Workloads.

Generates 65 concrete locked workloads:
- 40 Synthetic Matrix Workloads (5 query/k x 2 data_vertices x 2 edge_prob x 2 label_regimes).
- 5 Structured Semantic Witnesses (positive witnesses with planted anchor & external spectator edges).
- 20 NetFlow-Relative Subgraph Probes (4 sample universes x 5 query/k probes).

Features:
- Completely deterministic (reproducible RNG seeds, no randomized hash()).
- Streaming bounded-memory 2-pass NetFlow extraction.
- Cryptographic SHA-256 fingerprinting of all inputs and sub-components.
- Preflight M0 verification for every generated workload.
- Emits locked configs/dstar03_pilot_manifest.json and tests/fixtures/dstar03/workloads/*.json.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dstar_oracle as M0
from dstar_event_common import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DEFAULT_MAX_STREAM_STEPS,
    DEFAULT_MAX_WORK,
    DStarEventGraph,
    DStarEventUpdate,
    validate_run_contract,
)

QUERY_CONDITIONS = [
    ("diamond_k1", "diamond", 1, 2),
    ("k4_k1", "k4", 1, 3),
    ("k4_k2", "k4", 2, 3),
    ("diamond_k0", "diamond", 0, 2),
    ("k4_k0", "k4", 0, 3),
]


def canonical_json_bytes(data: Any) -> bytes:
    """Produces sorted-keys canonical JSON bytes."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_of_obj(data: Any) -> str:
    """Computes SHA-256 hex digest of canonical JSON serialization."""
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


class MaxHeapItem:
    """Wrapper for min-priority max-heap to keep smallest hash values."""
    def __init__(self, key: str, val: int) -> None:
        self.key = key
        self.val = val

    def __lt__(self, other: MaxHeapItem) -> bool:
        # Invert comparison so heapq acts as a max-heap on self.key
        return self.key > other.key


def build_query_graph(shape: str, label_regime: str) -> DStarEventGraph:
    """Builds Diamond or K4 query graph according to label regime."""
    q = DStarEventGraph()
    for v in range(4):
        v_label = 0 if label_regime == "uniform" else (v % 2)
        q.add_vertex(v, v_label)

    if shape == "diamond":
        q_edges = [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]
    else:  # k4
        q_edges = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]

    for u, v in q_edges:
        e_label = 0 if label_regime == "uniform" else ((u + v) % 2)
        q.add_edge(u, v, e_label)

    return q


def generate_synthetic_workloads() -> List[Dict[str, Any]]:
    """
    Generates 40 synthetic matrix workloads:
    5 query/k conditions x 2 n_data ({6, 8}) x 2 p ({0.25, 0.65}) x 2 labels ({uniform, mixed}).
    """
    workloads: List[Dict[str, Any]] = []
    case_idx = 0

    for q_name, q_shape, k, lambda_val in QUERY_CONDITIONS:
        for n_data in [6, 8]:
            for p in [0.25, 0.65]:
                for label_regime in ["uniform", "mixed"]:
                    seed = 20261118 + case_idx
                    rng = random.Random(seed)

                    q = build_query_graph(q_shape, label_regime)

                    # Initial data graph
                    g0 = DStarEventGraph()
                    for v in range(n_data):
                        v_lbl = 0 if label_regime == "uniform" else rng.randrange(2)
                        g0.add_vertex(v, v_lbl)

                    all_pairs = [
                        (u, v) for u in range(n_data) for v in range(u + 1, n_data)
                    ]
                    for u, v in all_pairs:
                        if rng.random() < p:
                            e_lbl = 0 if label_regime == "uniform" else rng.randrange(2)
                            g0.add_edge(u, v, e_lbl)

                    # 12 Updates
                    # 1. Choose deterministic toggle pair
                    toggle_pair = all_pairs[rng.randrange(len(all_pairs))]
                    stream_updates: List[DStarEventUpdate] = []
                    current_edges = dict(g0.edges)

                    if toggle_pair in current_edges:
                        t_lbl = current_edges[toggle_pair]
                        cycle_ops = ["DELETE", "INSERT", "DELETE", "INSERT"]
                        cycle_mode = "DELETE_FIRST"
                    else:
                        t_lbl = 0 if label_regime == "uniform" else rng.randrange(2)
                        cycle_ops = ["INSERT", "DELETE", "INSERT", "DELETE"]
                        cycle_mode = "INSERT_FIRST"

                    for op in cycle_ops:
                        upd = DStarEventUpdate(operation=op, edge=toggle_pair, label=t_lbl)
                        stream_updates.append(upd)
                        if op == "INSERT":
                            current_edges[toggle_pair] = t_lbl
                        else:
                            del current_edges[toggle_pair]

                    # 2. Remaining 8 churn updates
                    for _ in range(8):
                        present = sorted(current_edges.keys())
                        absent = [pair for pair in all_pairs if pair not in current_edges]

                        if not present:
                            chosen_op = "INSERT"
                        elif not absent:
                            chosen_op = "DELETE"
                        else:
                            chosen_op = "INSERT" if rng.random() < 0.5 else "DELETE"

                        if chosen_op == "INSERT":
                            chosen_pair = absent[rng.randrange(len(absent))]
                            lbl = 0 if label_regime == "uniform" else rng.randrange(2)
                            upd = DStarEventUpdate(operation="INSERT", edge=chosen_pair, label=lbl)
                            current_edges[chosen_pair] = lbl
                        else:
                            chosen_pair = present[rng.randrange(len(present))]
                            lbl = current_edges[chosen_pair]
                            upd = DStarEventUpdate(operation="DELETE", edge=chosen_pair, label=lbl)
                            del current_edges[chosen_pair]

                        stream_updates.append(upd)

                    # Realized metrics
                    realized_density = len(g0.edges) / len(all_pairs)
                    degrees = [0] * n_data
                    for u, v in g0.edges.keys():
                        degrees[u] += 1
                        degrees[v] += 1
                    max_degree = max(degrees) if degrees else 0

                    workload_id = f"syn_case_{case_idx:02d}_{q_name}_n{n_data}_p{int(p*100)}_{label_regime}"
                    workload_obj = {
                        "workload_id": workload_id,
                        "case_index": case_idx,
                        "family": "SYNTHETIC_MATRIX",
                        "graph_mode": "ISOLATED_SYNTHETIC",
                        "query_name": q_name,
                        "query_shape": q_shape,
                        "k": k,
                        "lambda_val": lambda_val,
                        "label_regime": label_regime,
                        "n_data": n_data,
                        "target_p": p,
                        "realized_density": round(realized_density, 4),
                        "max_degree": max_degree,
                        "seed": seed,
                        "toggle_pair": list(toggle_pair),
                        "toggle_mode": cycle_mode,
                        "query": q.to_dict(),
                        "initial_graph": g0.to_dict(),
                        "stream": [
                            {"operation": u.operation, "edge": list(u.edge), "label": u.label}
                            for u in stream_updates
                        ],
                    }
                    workloads.append(workload_obj)
                    case_idx += 1

    return workloads


def generate_witness_workloads() -> List[Dict[str, Any]]:
    """
    Generates 5 structured semantic witnesses:
    1 witness for each of the 5 query/k conditions.
    """
    workloads: List[Dict[str, Any]] = []

    for idx, (q_name, q_shape, k, lambda_val) in enumerate(QUERY_CONDITIONS):
        case_idx = 40 + idx
        # Query vertices 0..3 with distinct labels 10, 20, 30, 40
        q = DStarEventGraph()
        for v in range(4):
            q.add_vertex(v, (v + 1) * 10)

        if q_shape == "diamond":
            q_edges = [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]
        else:
            q_edges = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]

        for u, v in q_edges:
            q.add_edge(u, v, 0)

        anchor = (0, 1)
        other_edges = [e for e in q_edges if e != anchor]
        D = other_edges[:k]
        g0_edges = [e for e in other_edges if e not in D]

        # Data graph with 6 vertices: 0..3 for query mapping, 4..5 for external spectator edge
        g0 = DStarEventGraph()
        for v in range(4):
            g0.add_vertex(v, (v + 1) * 10)
        g0.add_vertex(4, 50)
        g0.add_vertex(5, 60)

        for u, v in g0_edges:
            g0.add_edge(u, v, 0)

        stream_updates = [
            DStarEventUpdate(operation="INSERT", edge=anchor, label=0),
            DStarEventUpdate(operation="INSERT", edge=(4, 5), label=0),
            DStarEventUpdate(operation="DELETE", edge=(4, 5), label=0),
            DStarEventUpdate(operation="DELETE", edge=anchor, label=0),
        ]

        workload_id = f"witness_{idx+1:02d}_{q_name}"
        workload_obj = {
            "workload_id": workload_id,
            "case_index": case_idx,
            "family": "STRUCTURED_WITNESS",
            "graph_mode": "STRUCTURED_WITNESS",
            "query_name": q_name,
            "query_shape": q_shape,
            "k": k,
            "lambda_val": lambda_val,
            "label_regime": "distinct_witness",
            "anchor_edge": list(anchor),
            "defect_set": [list(e) for e in D],
            "spectator_edge": [4, 5],
            "query": q.to_dict(),
            "initial_graph": g0.to_dict(),
            "stream": [
                {"operation": u.operation, "edge": list(u.edge), "label": u.label}
                for u in stream_updates
            ],
            "expected_events": [
                {"step": 0, "entered": [[0, 1, 2, 3]], "exited": []},
                {"step": 1, "entered": [], "exited": []},
                {"step": 2, "entered": [], "exited": []},
                {"step": 3, "entered": [], "exited": [[0, 1, 2, 3]]},
            ],
        }
        workloads.append(workload_obj)

    return workloads


def resolve_netflow_data_dir(candidate_dir: Optional[str] = None) -> Optional[Path]:
    """Finds NetFlow data directory containing Ndata.graph and Ndeletion10.graph."""
    candidates: List[Path] = []
    if candidate_dir:
        candidates.append(Path(candidate_dir).resolve())

    candidates.extend([
        REPO_ROOT / "data" / "netflow" / "release_newsp" / "netflow" / "data_graph",
        Path("/home/iamqthanq/Desktop/Seminar_RapidFlow/data/netflow/release_newsp/netflow/data_graph"),
    ])

    for c in candidates:
        if (c / "Ndata.graph").exists() and (c / "Ndeletion10.graph").exists():
            return c
    return None


def generate_netflow_workloads(netflow_dir: Path) -> List[Dict[str, Any]]:
    """
    Extracts 4 sample universes from NetFlow data using bounded-memory streaming
    and creates 20 query/k probe workloads.
    """
    seeds = [20261118, 20261119]
    target_ops = ["INSERT", "DELETE"]

    # 1. Read first 64 operation records from Ndeletion10.graph
    ndeletion_path = netflow_dir / "Ndeletion10.graph"
    with ndeletion_path.open("r", encoding="utf-8") as f:
        prefix_lines = [f.readline().strip() for _ in range(64)]

    prefix_records: List[Tuple[int, str, int, int, int, str]] = []
    for idx, line in enumerate(prefix_lines):
        parts = line.split()
        op_type = "INSERT" if parts[0] == "e" else "DELETE"
        u, v, label = int(parts[1]), int(parts[2]), int(parts[3])
        prefix_records.append((idx, op_type, u, v, label, line))

    # 2. Select 4 target operations
    sample_configs: List[Dict[str, Any]] = []
    sample_targets: Dict[Tuple[int, str], Tuple[int, str, int, int, int, str]] = {}

    for seed in seeds:
        for op in target_ops:
            matching = [r for r in prefix_records if r[1] == op]
            best_target = min(
                matching,
                key=lambda r: hashlib.sha256(f"{seed}:{op}:{r[0]}".encode("utf-8")).hexdigest(),
            )
            sample_targets[(seed, op)] = best_target
            sample_configs.append({
                "seed": seed,
                "op": op,
                "target_record": best_target,
            })

    # 3. Pass 1: Stream Ndata.graph to select bounded candidate vertices for each sample
    # Bounded heaps for each of the 4 samples: top 6 neighbors, top 8 fallbacks
    neighbor_heaps: Dict[Tuple[int, str], List[MaxHeapItem]] = {k: [] for k in sample_targets}
    seen_neighbors: Dict[Tuple[int, str], Set[int]] = {k: set() for k in sample_targets}
    fallback_heaps: Dict[Tuple[int, str], List[MaxHeapItem]] = {k: [] for k in sample_targets}

    ndata_path = netflow_dir / "Ndata.graph"
    with ndata_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                parts = line.split()
                vid = int(parts[1])
                for (s_seed, s_op), target in sample_targets.items():
                    u_t, v_t = target[2], target[3]
                    if vid == u_t or vid == v_t:
                        continue
                    h = hashlib.sha256(f"{s_seed}:{vid}".encode("utf-8")).hexdigest()
                    fb_heap = fallback_heaps[(s_seed, s_op)]
                    if len(fb_heap) < 8:
                        heapq.heappush(fb_heap, MaxHeapItem(h, vid))
                    elif h < fb_heap[0].key:
                        heapq.heappushpop(fb_heap, MaxHeapItem(h, vid))
            elif line.startswith("e "):
                parts = line.split()
                u, v = int(parts[1]), int(parts[2])
                for (s_seed, s_op), target in sample_targets.items():
                    u_t, v_t = target[2], target[3]
                    w = None
                    if u == u_t or u == v_t:
                        w = v
                    elif v == u_t or v == v_t:
                        w = u
                    if w is not None and w != u_t and w != v_t:
                        s_seen = seen_neighbors[(s_seed, s_op)]
                        if w not in s_seen:
                            s_seen.add(w)
                            h = hashlib.sha256(f"{s_seed}:{w}".encode("utf-8")).hexdigest()
                            nbr_heap = neighbor_heaps[(s_seed, s_op)]
                            if len(nbr_heap) < 6:
                                heapq.heappush(nbr_heap, MaxHeapItem(h, w))
                            elif h < nbr_heap[0].key:
                                heapq.heappushpop(nbr_heap, MaxHeapItem(h, w))

    # Form sets S for all 4 samples
    universe_S: Dict[Tuple[int, str], List[int]] = {}
    universe_S_sets: Dict[Tuple[int, str], Set[int]] = {}
    for (s_seed, s_op), target in sample_targets.items():
        u_t, v_t = target[2], target[3]
        nbr_sorted = sorted(
            [item.val for item in neighbor_heaps[(s_seed, s_op)]],
            key=lambda x: hashlib.sha256(f"{s_seed}:{x}".encode("utf-8")).hexdigest(),
        )
        fb_sorted = sorted(
            [item.val for item in fallback_heaps[(s_seed, s_op)]],
            key=lambda x: hashlib.sha256(f"{s_seed}:{x}".encode("utf-8")).hexdigest(),
        )

        S = [u_t, v_t] + nbr_sorted
        for fb in fb_sorted:
            if len(S) == 8:
                break
            if fb not in S:
                S.append(fb)
        universe_S[(s_seed, s_op)] = S
        universe_S_sets[(s_seed, s_op)] = set(S)

    # 4. Pass 2: Stream Ndata.graph to extract labels and edges for S
    sample_g0: Dict[Tuple[int, str], DStarEventGraph] = {}
    for key, S in universe_S.items():
        g = DStarEventGraph()
        for vid in S:
            g.add_vertex(vid, 0)
        sample_g0[key] = g

    with ndata_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                pass  # NetFlow vertices are label 0
            elif line.startswith("e "):
                parts = line.split()
                u, v, label = int(parts[1]), int(parts[2]), int(parts[3])
                for key, S_set in universe_S_sets.items():
                    if u in S_set and v in S_set:
                        sample_g0[key].add_edge(u, v, label)

    # 5. Project stream updates for each sample
    sample_projected_streams: Dict[Tuple[int, str], List[DStarEventUpdate]] = {}
    for key, S_set in universe_S_sets.items():
        projected: List[DStarEventUpdate] = []
        for r in prefix_records:
            if r[2] in S_set and r[3] in S_set:
                projected.append(
                    DStarEventUpdate(operation=r[1], edge=(r[2], r[3]), label=r[4])
                )
        sample_projected_streams[key] = projected

    # 6. Build 20 NetFlow probe workloads (4 samples x 5 query/k conditions)
    workloads: List[Dict[str, Any]] = []
    case_idx = 45

    for (s_seed, s_op), target in sample_targets.items():
        g0_sample = sample_g0[(s_seed, s_op)]
        stream_sample = sample_projected_streams[(s_seed, s_op)]
        target_label = target[4]
        target_op_idx = target[0]

        for q_name, q_shape, k, lambda_val in QUERY_CONDITIONS:
            # Query vertex labels 0, all edge labels equal to target operation label
            q = DStarEventGraph()
            for v in range(4):
                q.add_vertex(v, 0)

            if q_shape == "diamond":
                q_edges = [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]
            else:
                q_edges = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]

            for u, v in q_edges:
                q.add_edge(u, v, target_label)

            workload_id = f"netflow_s{s_seed}_{s_op.lower()}_{q_name}"
            workload_obj = {
                "workload_id": workload_id,
                "case_index": case_idx,
                "family": "NETFLOW_PROBE",
                "graph_mode": "SUBGRAPH_RELATIVE",
                "query_name": q_name,
                "query_shape": q_shape,
                "k": k,
                "lambda_val": lambda_val,
                "netflow_seed": s_seed,
                "target_operation": s_op,
                "target_source_op_index": target_op_idx,
                "target_label": target_label,
                "target_raw_line": target[5],
                "sample_universe_S": universe_S[(s_seed, s_op)],
                "query": q.to_dict(),
                "initial_graph": g0_sample.to_dict(),
                "stream": [
                    {"operation": u.operation, "edge": list(u.edge), "label": u.label}
                    for u in stream_sample
                ],
            }
            workloads.append(workload_obj)
            case_idx += 1

    return workloads


def verify_workload_m0(workload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a workload against independent M0 Ground Truth.
    Returns per-step truth tuples and verification metadata.
    """
    q_data = DStarEventGraph.from_dict(workload["query"])
    g0_data = DStarEventGraph.from_dict(workload["initial_graph"])
    k = workload["k"]
    stream = [
        DStarEventUpdate(operation=s["operation"], edge=tuple(s["edge"]), label=s["label"])
        for s in workload["stream"]
    ]

    validate_run_contract(q_data, g0_data, k, stream_length=len(stream))

    q_m0 = M0.DStarGraph.from_dict(q_data.to_dict())
    g_m0 = M0.DStarGraph.from_dict(g0_data.to_dict())
    oracle = M0.DStarDynamicOracle(q_m0, g_m0, k=k)

    expected_steps: List[Dict[str, Any]] = []
    total_entered = 0
    total_exited = 0

    for step_idx, upd in enumerate(stream):
        upd_m0 = M0.DStarUpdate(op=upd.operation, u=upd.edge[0], v=upd.edge[1], label=upd.label)
        delta = oracle.apply_update(upd_m0)
        ent = delta.entered
        exi = delta.exited
        total_entered += len(ent)
        total_exited += len(exi)
        expected_steps.append({
            "step": step_idx,
            "operation": upd.operation,
            "edge": list(upd.edge),
            "label": upd.label,
            "entered": [list(t) for t in ent],
            "exited": [list(t) for t in exi],
        })

    # Active-set reconstruction check
    active_m0 = set(oracle.current_matches)
    active_reconstructed: Set[Tuple[int, ...]] = set()
    # Initial matches
    init_oracle = M0.DStarDynamicOracle(q_m0, M0.DStarGraph.from_dict(g0_data.to_dict()), k=k)
    active_reconstructed = set(init_oracle.current_matches)
    for s in expected_steps:
        for ex in s["exited"]:
            active_reconstructed.discard(tuple(ex))
        for en in s["entered"]:
            active_reconstructed.add(tuple(en))

    assert active_reconstructed == active_m0, "Active-set reconstruction failed"

    return {
        "verified": True,
        "total_entered_events": total_entered,
        "total_exited_events": total_exited,
        "total_events": total_entered + total_exited,
        "has_events": (total_entered + total_exited) > 0,
        "steps": expected_steps,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate locked DSTAR-03 pilot workloads.")
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=REPO_ROOT / "configs" / "dstar03_pilot_manifest.json",
        help="Path to output manifest JSON.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "dstar03" / "workloads",
        help="Directory to save individual workload JSON fixtures.",
    )
    parser.add_argument(
        "--netflow-dir",
        type=Path,
        default=None,
        help="Path to NetFlow data graph directory containing Ndata.graph and Ndeletion10.graph.",
    )
    parser.add_argument(
        "--skip-m0-verify",
        action="store_true",
        help="Skip M0 verification during generation.",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)

    print("Generating DSTAR-03 Workloads...")

    # 1. Synthetic Matrix (40 workloads)
    print("Generating 40 Synthetic Matrix workloads...")
    syn_workloads = generate_synthetic_workloads()
    assert len(syn_workloads) == 40, f"Expected 40 synthetic workloads, got {len(syn_workloads)}"

    # 2. Structured Witnesses (5 workloads)
    print("Generating 5 Structured Semantic Witness workloads...")
    wit_workloads = generate_witness_workloads()
    assert len(wit_workloads) == 5, f"Expected 5 witness workloads, got {len(wit_workloads)}"

    # 3. NetFlow Probes (20 workloads)
    print("Resolving NetFlow data source...")
    netflow_dir = resolve_netflow_data_dir(str(args.netflow_dir) if args.netflow_dir else None)
    if not netflow_dir:
        raise FileNotFoundError(
            "NetFlow data files (Ndata.graph and Ndeletion10.graph) not found in expected locations."
        )

    print(f"Extracting 20 NetFlow Probe workloads from {netflow_dir}...")
    netflow_workloads = generate_netflow_workloads(netflow_dir)
    assert len(netflow_workloads) == 20, f"Expected 20 NetFlow workloads, got {len(netflow_workloads)}"

    all_workloads = syn_workloads + wit_workloads + netflow_workloads
    assert len(all_workloads) == 65, f"Expected 65 total workloads, got {len(all_workloads)}"

    # 4. M0 Verification and Fixture Saving
    print("Verifying workloads against independent M0 Oracle and saving fixtures...")
    manifest_entries: List[Dict[str, Any]] = []

    for w in all_workloads:
        w_id = w["workload_id"]
        fixture_path = args.output_dir / f"{w_id}.json"

        # Compute component hashes
        w["sha256_query"] = sha256_of_obj(w["query"])
        w["sha256_initial_graph"] = sha256_of_obj(w["initial_graph"])
        w["sha256_stream"] = sha256_of_obj(w["stream"])

        # M0 Verification
        if not args.skip_m0_verify:
            m0_verif = verify_workload_m0(w)
            w["m0_verification"] = m0_verif
        else:
            w["m0_verification"] = {"verified": False}

        w["sha256_workload"] = sha256_of_obj(w)

        # Write workload fixture
        with fixture_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(w, indent=2, sort_keys=True))

        # Build manifest entry
        rel_path = str(fixture_path.relative_to(REPO_ROOT))
        manifest_entries.append({
            "workload_id": w_id,
            "case_index": w["case_index"],
            "family": w["family"],
            "graph_mode": w["graph_mode"],
            "query_name": w["query_name"],
            "k": w["k"],
            "data_vertices": len(w["initial_graph"]["vertices"]),
            "initial_data_edges": len(w["initial_graph"]["edges"]),
            "stream_length": len(w["stream"]),
            "has_events": w["m0_verification"].get("has_events", False),
            "total_events": w["m0_verification"].get("total_events", 0),
            "sha256_workload": w["sha256_workload"],
            "relative_path": rel_path,
        })

    # Read NetFlow source hashes for provenance
    with (netflow_dir / "Ndata.graph").open("rb") as f:
        ndata_sha = hashlib.sha256(f.read()).hexdigest()
    with (netflow_dir / "Ndeletion10.graph").open("rb") as f:
        ndeletion_sha = hashlib.sha256(f.read()).hexdigest()

    manifest_doc = {
        "manifest_id": "dstar03-pilot-manifest-v1",
        "description": "Locked 65 registered workloads for DSTAR-03 S4 tiny paired evaluation.",
        "contract_version": "dstar-v1.0",
        "sink_mode": "COLLECT_VERIFY_SORT_V1",
        "total_registered_workloads": 65,
        "counts_by_family": {
            "SYNTHETIC_MATRIX": 40,
            "STRUCTURED_WITNESS": 5,
            "NETFLOW_PROBE": 20,
        },
        "limits": {
            "max_data_vertices": DEFAULT_MAX_DATA_VERTICES,
            "max_query_vertices": DEFAULT_MAX_QUERY_VERTICES,
            "max_candidates": DEFAULT_MAX_CANDIDATES,
            "max_work": DEFAULT_MAX_WORK,
            "max_stream_steps": DEFAULT_MAX_STREAM_STEPS,
        },
        "resource_safety": {
            "rlimit_as_bytes": 512 * 1024 * 1024,
            "timeout_seconds": 60.0,
            "min_mem_available_bytes": 2 * 1024 * 1024 * 1024,
            "min_disk_free_bytes": 1 * 1024 * 1024 * 1024,
        },
        "dataset_provenance": {
            "source_manifest": "configs/netflow_newsp_manifest.json",
            "Ndata_graph_sha256": ndata_sha,
            "Ndeletion10_graph_sha256": ndeletion_sha,
            "prefix_operations_count": 64,
        },
        "protocol": {
            "repetitions_per_workload": 3,
            "measured_runs_per_workload": 6,
            "timer": "time.perf_counter_ns",
            "timer_scope": "engine.process_update",
            "order_parity_rule": "even_case: (M1->M2, M2->M1, M1->M2); odd_case: (M2->M1, M1->M2, M2->M1)",
        },
        "workloads": manifest_entries,
    }
    manifest_doc["manifest_sha256"] = sha256_of_obj(manifest_doc)

    with args.output_manifest.open("w", encoding="utf-8") as f:
        f.write(json.dumps(manifest_doc, indent=2, sort_keys=True))

    print(f"Manifest written to {args.output_manifest}")
    print(f"Successfully generated and verified all {len(all_workloads)} workloads.")


if __name__ == "__main__":
    main()
