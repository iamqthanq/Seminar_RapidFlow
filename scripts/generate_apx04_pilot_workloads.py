#!/usr/bin/env python3
"""
scripts/generate_apx04_pilot_workloads.py

APX-04 Gate A3-00: Deterministic Generator for Bounded Pilot Workloads.
Generates and materializes:
1. Pilot A (Exact Micrograph): Small 6-vertex fixture graph, query, and stream.
2. Pilot B (Near-Only Micrograph): Small 5-vertex fixture graph (C5), triangle query, and stream (chord insertion).
3. Pilot C (Tiny NetFlow-Derived Workload): 34-vertex induced subgraph extracted from Ndata.graph,
   projected update stream from Ninsertion.graph, and 3-vertex query.

All generated artifacts adhere to simple-graph invariants, fixed vertex universes,
and produce deterministic SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_pilot_a(output_dir: Path) -> Dict[str, str]:
    """Generates Pilot A: Small 6-vertex exact matching fixture."""
    data_path = output_dir / "pilot_a_exact_data.graph"
    stream_path = output_dir / "pilot_a_exact_stream.graph"
    query_path = output_dir / "pilot_a_exact_query.graph"

    # Data graph: 6 vertices (0..5), all label 0
    # Edges: (0,1,0), (1,2,0), (2,3,0), (3,4,0), (4,5,0), (0,2,1)
    with open(data_path, "w", encoding="utf-8") as f:
        for v in range(6):
            f.write(f"v {v} 0\n")
        f.write("e 0 1 0\n")
        f.write("e 1 2 0\n")
        f.write("e 2 3 0\n")
        f.write("e 3 4 0\n")
        f.write("e 4 5 0\n")
        f.write("e 0 2 1\n")

    # Stream: 2 insertions
    # Step 1: e 2 5 0
    # Step 2: e 3 5 1
    with open(stream_path, "w", encoding="utf-8") as f:
        f.write("e 2 5 0\n")
        f.write("e 3 5 1\n")

    # Query: 3-vertex triangle with edges (0,1,0), (1,2,0), (0,2,1)
    with open(query_path, "w", encoding="utf-8") as f:
        f.write("v 0 0\n")
        f.write("v 1 0\n")
        f.write("v 2 0\n")
        f.write("e 0 1 0\n")
        f.write("e 1 2 0\n")
        f.write("e 0 2 1\n")

    return {
        "data_graph": str(data_path),
        "data_graph_sha256": sha256_file(data_path),
        "stream": str(stream_path),
        "stream_sha256": sha256_file(stream_path),
        "query": str(query_path),
        "query_sha256": sha256_file(query_path),
    }


def generate_pilot_b(output_dir: Path) -> Dict[str, str]:
    """Generates Pilot B: Small 5-vertex near-only fixture (C5 graph, K3 query)."""
    data_path = output_dir / "pilot_b_nearonly_data.graph"
    stream_path = output_dir / "pilot_b_nearonly_stream.graph"
    query_path = output_dir / "pilot_b_nearonly_query.graph"

    # Data graph: 5 vertices (0..4), all label 0
    # Edges: 5-cycle (0,1,0), (1,2,0), (2,3,0), (3,4,0), (4,0,0)
    with open(data_path, "w", encoding="utf-8") as f:
        for v in range(5):
            f.write(f"v {v} 0\n")
        f.write("e 0 1 0\n")
        f.write("e 1 2 0\n")
        f.write("e 2 3 0\n")
        f.write("e 3 4 0\n")
        f.write("e 4 0 0\n")

    # Stream: 2 updates: add chord e 0 2 0, then delete e 2 3 0
    with open(stream_path, "w", encoding="utf-8") as f:
        f.write("e 0 2 0\n")
        f.write("-e 2 3 0\n")

    # Query: 3-vertex triangle K3, all label 0
    with open(query_path, "w", encoding="utf-8") as f:
        f.write("v 0 0\n")
        f.write("v 1 0\n")
        f.write("v 2 0\n")
        f.write("e 0 1 0\n")
        f.write("e 1 2 0\n")
        f.write("e 0 2 0\n")

    return {
        "data_graph": str(data_path),
        "data_graph_sha256": sha256_file(data_path),
        "stream": str(stream_path),
        "stream_sha256": sha256_file(stream_path),
        "query": str(query_path),
        "query_sha256": sha256_file(query_path),
    }


def generate_pilot_c(
    output_dir: Path,
    repo_root: Path,
) -> Dict[str, Any]:
    """Generates Pilot C: 34-vertex induced subgraph extracted from Ndata.graph and projected stream."""
    data_path = output_dir / "pilot_c_tiny_netflow_data.graph"
    stream_path = output_dir / "pilot_c_tiny_netflow_stream.graph"
    query_path = output_dir / "pilot_c_tiny_netflow_query.graph"

    ndata_path = repo_root / "data/netflow/release_newsp/netflow/data_graph/Ndata.graph"
    ninsert_path = repo_root / "data/netflow/release_newsp/netflow/data_graph/Ninsertion.graph"

    # Seed nodes: 1590515 and its stream partner 2940315
    first_hop = {1590514, 1590515, 1590530, 1590564, 1592956, 1606375, 1610074, 1788734, 2940315}
    second_hop = set(first_hop)

    # 1. Expand to 2-hop neighborhood in Ndata.graph
    with open(ndata_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("e "):
                parts = line.strip().split()
                u, v = int(parts[1]), int(parts[2])
                if u in first_hop or v in first_hop:
                    second_hop.add(u)
                    second_hop.add(v)

    selected_vertices = sorted(list(second_hop))

    # 2. Extract induced edges
    induced_edges: List[Tuple[int, int, int]] = []
    with open(ndata_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("e "):
                parts = line.strip().split()
                u, v, lbl = int(parts[1]), int(parts[2]), int(parts[3])
                if u in second_hop and v in second_hop:
                    induced_edges.append((min(u, v), max(u, v), lbl))

    # Sort edges for deterministic file generation
    induced_edges.sort()

    with open(data_path, "w", encoding="utf-8") as f:
        for v in selected_vertices:
            f.write(f"v {v} 0\n")
        for u, v, lbl in induced_edges:
            f.write(f"e {u} {v} {lbl}\n")

    # 3. Project updates from Ninsertion.graph
    # Operation position 15 in Ninsertion.graph is: e 1590515 2940315 2
    projected_updates: List[Tuple[int, str]] = []
    with open(ninsert_path, "r", encoding="utf-8") as f:
        for pos, line in enumerate(f, start=1):
            if pos > 1000:
                break
            line_s = line.strip()
            if line_s.startswith("e ") or line_s.startswith("-e "):
                parts = line_s.split()
                tag = parts[0]
                if tag == "e":
                    u, v = int(parts[1]), int(parts[2])
                elif tag == "-e":
                    u, v = int(parts[1]), int(parts[2])
                else:
                    continue
                if u in second_hop and v in second_hop:
                    projected_updates.append((pos, line_s))

    with open(stream_path, "w", encoding="utf-8") as f:
        for pos, op_line in projected_updates:
            f.write(f"{op_line}\n")

    # 4. Query: 3-vertex path / star with edge label 2
    with open(query_path, "w", encoding="utf-8") as f:
        f.write("v 0 0\n")
        f.write("v 1 0\n")
        f.write("v 2 0\n")
        f.write("e 0 1 2\n")
        f.write("e 0 2 2\n")

    return {
        "data_graph": str(data_path),
        "data_graph_sha256": sha256_file(data_path),
        "stream": str(stream_path),
        "stream_sha256": sha256_file(stream_path),
        "query": str(query_path),
        "query_sha256": sha256_file(query_path),
        "vertex_count": len(selected_vertices),
        "edge_count": len(induced_edges),
        "vertex_ids": selected_vertices,
        "projected_positions": [p[0] for p in projected_updates],
    }


def main():
    repo_root = Path(__file__).resolve().parent.parent
    pilots_dir = repo_root / "data" / "pilots"
    pilots_dir.mkdir(parents=True, exist_ok=True)

    print("Generating Pilot A...")
    res_a = generate_pilot_a(pilots_dir)
    print("Pilot A:", json.dumps(res_a, indent=2))

    print("\nGenerating Pilot B...")
    res_b = generate_pilot_b(pilots_dir)
    print("Pilot B:", json.dumps(res_b, indent=2))

    print("\nGenerating Pilot C...")
    res_c = generate_pilot_c(pilots_dir, repo_root)
    print("Pilot C:", json.dumps(res_c, indent=2))


if __name__ == "__main__":
    main()
