#!/usr/bin/env python3
"""
scripts/generate_p4a_manifest.py — Deterministic Workload Manifest Generator for Phase P4a.

Generates 30 bounded, structured pilot workloads covering:
- Queries: Diamond (k=0, 1), K4 (k=0, 1, 2)
- Regimes: Uniform labels and Mixed labels
- Topologies: Micro (N=8), Cycle (N=12), Dense Hub (N=16), Scale-Free (N=16)
- Operations: Mixed INSERT and DELETE operations, zero-event fast-paths
- Safety: Strictly within Domain D-R (k < lambda(Q)) and work cap (500000).
"""

import json
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def build_diamond_query():
    # Diamond: 4 vertices, 5 edges, lambda=2
    return {
        "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}, {"id": 3, "label": 1}],
        "edges": [
            {"u": 0, "v": 1, "label": 1},
            {"u": 0, "v": 2, "label": 1},
            {"u": 1, "v": 3, "label": 1},
            {"u": 2, "v": 3, "label": 1},
            {"u": 1, "v": 2, "label": 1}
        ]
    }

def build_diamond_query_mixed():
    # Diamond with mixed vertex labels and edge labels, lambda=2
    return {
        "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 2}, {"id": 2, "label": 2}, {"id": 3, "label": 3}],
        "edges": [
            {"u": 0, "v": 1, "label": 10},
            {"u": 0, "v": 2, "label": 10},
            {"u": 1, "v": 3, "label": 20},
            {"u": 2, "v": 3, "label": 20},
            {"u": 1, "v": 2, "label": 30}
        ]
    }

def build_k4_query():
    # K4: 4 vertices, 6 edges, lambda=3
    return {
        "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}, {"id": 3, "label": 1}],
        "edges": [
            {"u": 0, "v": 1, "label": 1},
            {"u": 0, "v": 2, "label": 1},
            {"u": 0, "v": 3, "label": 1},
            {"u": 1, "v": 2, "label": 1},
            {"u": 1, "v": 3, "label": 1},
            {"u": 2, "v": 3, "label": 1}
        ]
    }

def build_k4_query_mixed():
    # K4 with mixed vertex and edge labels, lambda=3
    return {
        "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 2}, {"id": 2, "label": 1}, {"id": 3, "label": 2}],
        "edges": [
            {"u": 0, "v": 1, "label": 10},
            {"u": 0, "v": 2, "label": 10},
            {"u": 0, "v": 3, "label": 20},
            {"u": 1, "v": 2, "label": 20},
            {"u": 1, "v": 3, "label": 10},
            {"u": 2, "v": 3, "label": 10}
        ]
    }

def generate_workloads():
    workloads = []

    # 1. Micro Graph Topology (N=8)
    def make_micro_graph(uniform=True):
        vlabels = [1]*8 if uniform else [1, 2, 2, 3, 1, 2, 3, 1]
        base_edges = [
            (0, 1), (0, 2), (1, 3), (2, 3),
            (4, 5), (4, 6), (5, 7), (6, 7),
            (0, 4), (1, 5)
        ]
        vertices = [{"id": i, "label": vlabels[i]} for i in range(8)]
        elbl = 1 if uniform else 10
        edges = [{"u": u, "v": v, "label": elbl} for u, v in base_edges]
        return {"vertices": vertices, "edges": edges}

    # 2. Cycle / Chordal Graph Topology (N=12)
    def make_cycle_graph(uniform=True):
        vlabels = [1]*12 if uniform else [1, 2, 2, 3] * 3
        vertices = [{"id": i, "label": vlabels[i]} for i in range(12)]
        edges = []
        for i in range(12):
            nxt = (i + 1) % 12
            elbl = 1 if uniform else (10 if i % 2 == 0 else 20)
            edges.append({"u": i, "v": nxt, "label": elbl})
        # Add chords
        chords = [(0, 3), (3, 6), (6, 9), (9, 0), (1, 7), (4, 10)]
        for u, v in chords:
            elbl = 1 if uniform else 30
            edges.append({"u": u, "v": v, "label": elbl})
        return {"vertices": vertices, "edges": edges}

    # 3. Dense Hub Graph Topology (N=16, Hub vertex 0 with high degree)
    def make_hub_graph(uniform=True):
        vlabels = [1]*16 if uniform else [1, 2, 2, 3, 1, 2, 1, 3, 2, 1, 3, 2, 1, 2, 3, 1]
        vertices = [{"id": i, "label": vlabels[i]} for i in range(16)]
        edges = []
        # Hub vertex 0 connected to 1..10
        for i in range(1, 11):
            elbl = 1 if uniform else (10 if i % 2 == 0 else 20)
            edges.append({"u": 0, "v": i, "label": elbl})
        # Ring among spokes 1..10
        for i in range(1, 10):
            elbl = 1 if uniform else 10
            edges.append({"u": i, "v": i + 1, "label": elbl})
        edges.append({"u": 10, "v": 1, "label": 1 if uniform else 10})
        # Outer ring 11..15
        for i in range(11, 15):
            edges.append({"u": i, "v": i + 1, "label": 1 if uniform else 20})
        edges.append({"u": 15, "v": 11, "label": 1 if uniform else 20})
        # Connections between hub and outer
        edges.append({"u": 0, "v": 11, "label": 1 if uniform else 30})
        edges.append({"u": 2, "v": 12, "label": 1 if uniform else 30})
        edges.append({"u": 4, "v": 13, "label": 1 if uniform else 30})
        edges.append({"u": 6, "v": 14, "label": 1 if uniform else 30})
        edges.append({"u": 8, "v": 15, "label": 1 if uniform else 30})
        return {"vertices": vertices, "edges": edges}

    # 4. Scale-Free Graph Topology (N=16)
    def make_scalefree_graph(uniform=True):
        vlabels = [1]*16 if uniform else [1, 2, 1, 2, 3, 1, 2, 3, 1, 2, 1, 3, 2, 1, 2, 3]
        vertices = [{"id": i, "label": vlabels[i]} for i in range(16)]
        base_edges = [
            (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (0, 7), (0, 8),
            (1, 2), (1, 3), (1, 9), (1, 10),
            (2, 4), (2, 11), (2, 12),
            (3, 5), (3, 13),
            (4, 6), (4, 14),
            (5, 7), (5, 15),
            (6, 8), (7, 9), (8, 10), (9, 11), (10, 12), (11, 13), (12, 14), (13, 15)
        ]
        edges = []
        for u, v in base_edges:
            elbl = 1 if uniform else (10 if (u + v) % 2 == 0 else 20)
            edges.append({"u": u, "v": v, "label": elbl})
        return {"vertices": vertices, "edges": edges}

    # Streams specifically valid for each topology:
    def stream_micro(uniform=True):
        elbl = 1 if uniform else 10
        elbl_cross = 1 if uniform else 30
        return [
            {"op": "+", "u": 1, "v": 2, "label": elbl_cross},
            {"op": "+", "u": 5, "v": 6, "label": elbl_cross},
            {"op": "+", "u": 2, "v": 4, "label": elbl},
            {"op": "+", "u": 3, "v": 7, "label": elbl},
            {"op": "-", "u": 1, "v": 2, "label": elbl_cross},
            {"op": "-", "u": 5, "v": 6, "label": elbl_cross},
            {"op": "-", "u": 2, "v": 4, "label": elbl},
            {"op": "-", "u": 3, "v": 7, "label": elbl}
        ]

    def stream_cycle(uniform=True):
        # Non-existing edges in cycle graph: (1, 4), (2, 5), (5, 8), (7, 10)
        elbl = 1 if uniform else 10
        elbl2 = 1 if uniform else 20
        return [
            {"op": "+", "u": 1, "v": 4, "label": elbl},
            {"op": "+", "u": 2, "v": 5, "label": elbl2},
            {"op": "+", "u": 5, "v": 8, "label": elbl},
            {"op": "+", "u": 7, "v": 10, "label": elbl2},
            {"op": "-", "u": 1, "v": 4, "label": elbl},
            {"op": "-", "u": 2, "v": 5, "label": elbl2},
            {"op": "-", "u": 5, "v": 8, "label": elbl},
            {"op": "-", "u": 7, "v": 10, "label": elbl2}
        ]

    def stream_hub(uniform=True):
        # Non-existing edges in hub graph: (1, 6), (2, 7), (3, 8), (4, 9), (0, 12), (1, 14)
        elbl = 1 if uniform else 10
        elbl2 = 1 if uniform else 20
        elbl3 = 1 if uniform else 30
        return [
            {"op": "+", "u": 1, "v": 6, "label": elbl},
            {"op": "+", "u": 2, "v": 7, "label": elbl2},
            {"op": "+", "u": 3, "v": 8, "label": elbl3},
            {"op": "+", "u": 4, "v": 9, "label": elbl},
            {"op": "+", "u": 0, "v": 12, "label": elbl2},
            {"op": "+", "u": 1, "v": 14, "label": elbl3},
            {"op": "-", "u": 1, "v": 6, "label": elbl},
            {"op": "-", "u": 2, "v": 7, "label": elbl2},
            {"op": "-", "u": 3, "v": 8, "label": elbl3},
            {"op": "-", "u": 4, "v": 9, "label": elbl},
            {"op": "-", "u": 0, "v": 12, "label": elbl2},
            {"op": "-", "u": 1, "v": 14, "label": elbl3}
        ]

    def stream_scalefree(uniform=True):
        # Non-existing edges: (0, 9), (0, 10), (1, 4), (2, 5), (3, 6), (5, 8)
        elbl = 1 if uniform else 10
        elbl2 = 1 if uniform else 20
        return [
            {"op": "+", "u": 0, "v": 9, "label": elbl},
            {"op": "+", "u": 0, "v": 10, "label": elbl2},
            {"op": "+", "u": 1, "v": 4, "label": elbl},
            {"op": "+", "u": 2, "v": 5, "label": elbl2},
            {"op": "+", "u": 3, "v": 6, "label": elbl},
            {"op": "+", "u": 5, "v": 8, "label": elbl2},
            {"op": "-", "u": 0, "v": 9, "label": elbl},
            {"op": "-", "u": 0, "v": 10, "label": elbl2},
            {"op": "-", "u": 1, "v": 4, "label": elbl},
            {"op": "-", "u": 2, "v": 5, "label": elbl2},
            {"op": "-", "u": 3, "v": 6, "label": elbl},
            {"op": "-", "u": 5, "v": 8, "label": elbl2}
        ]

    def stream_zero_event():
        return [
            {"op": "+", "u": 0, "v": 7, "label": 999},
            {"op": "+", "u": 1, "v": 6, "label": 999},
            {"op": "+", "u": 2, "v": 5, "label": 999},
            {"op": "+", "u": 3, "v": 4, "label": 999},
            {"op": "-", "u": 0, "v": 7, "label": 999},
            {"op": "-", "u": 1, "v": 6, "label": 999},
            {"op": "-", "u": 2, "v": 5, "label": 999},
            {"op": "-", "u": 3, "v": 4, "label": 999}
        ]

    def stream_delete_heavy(uniform=True):
        elbl = 1 if uniform else 10
        return [
            {"op": "-", "u": 0, "v": 1, "label": elbl},
            {"op": "-", "u": 1, "v": 3, "label": elbl},
            {"op": "+", "u": 0, "v": 1, "label": elbl},
            {"op": "+", "u": 1, "v": 3, "label": elbl},
            {"op": "-", "u": 4, "v": 5, "label": elbl},
            {"op": "-", "u": 5, "v": 7, "label": elbl},
            {"op": "+", "u": 4, "v": 5, "label": elbl},
            {"op": "+", "u": 5, "v": 7, "label": elbl}
        ]

    configs = [
        # Diamond k=0
        ("P4A_WL_01", "diamond", 0, True, "micro", make_micro_graph(True), stream_micro(True), "Diamond k=0 exact control uniform micro"),
        ("P4A_WL_02", "diamond", 0, False, "micro", make_micro_graph(False), stream_micro(False), "Diamond k=0 exact control mixed micro"),
        # Diamond k=1
        ("P4A_WL_03", "diamond", 1, True, "micro", make_micro_graph(True), stream_micro(True), "Diamond k=1 approximate uniform micro"),
        ("P4A_WL_04", "diamond", 1, False, "micro", make_micro_graph(False), stream_micro(False), "Diamond k=1 approximate mixed micro"),
        ("P4A_WL_05", "diamond", 1, True, "cycle", make_cycle_graph(True), stream_cycle(True), "Diamond k=1 approximate uniform cycle"),
        ("P4A_WL_06", "diamond", 1, False, "cycle", make_cycle_graph(False), stream_cycle(False), "Diamond k=1 approximate mixed cycle"),
        ("P4A_WL_07", "diamond", 1, True, "hub", make_hub_graph(True), stream_hub(True), "Diamond k=1 approximate uniform dense hub"),
        ("P4A_WL_08", "diamond", 1, False, "hub", make_hub_graph(False), stream_hub(False), "Diamond k=1 approximate mixed dense hub"),
        ("P4A_WL_09", "diamond", 1, True, "micro", make_micro_graph(True), stream_zero_event(), "Diamond k=1 zero-event fast-path control"),
        ("P4A_WL_10", "diamond", 1, True, "micro", make_micro_graph(True), stream_delete_heavy(True), "Diamond k=1 delete-heavy stream"),

        # K4 k=0
        ("P4A_WL_11", "k4", 0, True, "micro", make_micro_graph(True), stream_micro(True), "K4 k=0 exact control uniform micro"),
        ("P4A_WL_12", "k4", 0, False, "micro", make_micro_graph(False), stream_micro(False), "K4 k=0 exact control mixed micro"),
        ("P4A_WL_13", "k4", 0, True, "hub", make_hub_graph(True), stream_hub(True), "K4 k=0 exact control uniform dense hub"),

        # K4 k=1
        ("P4A_WL_14", "k4", 1, True, "micro", make_micro_graph(True), stream_micro(True), "K4 k=1 approximate uniform micro"),
        ("P4A_WL_15", "k4", 1, False, "micro", make_micro_graph(False), stream_micro(False), "K4 k=1 approximate mixed micro"),
        ("P4A_WL_16", "k4", 1, True, "cycle", make_cycle_graph(True), stream_cycle(True), "K4 k=1 approximate uniform cycle"),
        ("P4A_WL_17", "k4", 1, False, "cycle", make_cycle_graph(False), stream_cycle(False), "K4 k=1 approximate mixed cycle"),
        ("P4A_WL_18", "k4", 1, True, "hub", make_hub_graph(True), stream_hub(True), "K4 k=1 approximate uniform dense hub"),
        ("P4A_WL_19", "k4", 1, False, "hub", make_hub_graph(False), stream_hub(False), "K4 k=1 approximate mixed dense hub"),
        ("P4A_WL_20", "k4", 1, True, "scalefree", make_scalefree_graph(True), stream_scalefree(True), "K4 k=1 approximate uniform scale-free"),
        ("P4A_WL_21", "k4", 1, True, "micro", make_micro_graph(True), stream_zero_event(), "K4 k=1 zero-event fast-path control"),
        ("P4A_WL_22", "k4", 1, True, "micro", make_micro_graph(True), stream_delete_heavy(True), "K4 k=1 delete-heavy stream"),

        # K4 k=2
        ("P4A_WL_23", "k4", 2, True, "micro", make_micro_graph(True), stream_micro(True), "K4 k=2 approximate uniform micro defect=2"),
        ("P4A_WL_24", "k4", 2, False, "micro", make_micro_graph(False), stream_micro(False), "K4 k=2 approximate mixed micro defect=2"),
        ("P4A_WL_25", "k4", 2, True, "cycle", make_cycle_graph(True), stream_cycle(True), "K4 k=2 approximate uniform cycle defect=2"),
        ("P4A_WL_26", "k4", 2, False, "cycle", make_cycle_graph(False), stream_cycle(False), "K4 k=2 approximate mixed cycle defect=2"),
        ("P4A_WL_27", "k4", 2, True, "hub", make_hub_graph(True), stream_hub(True), "K4 k=2 approximate uniform dense hub defect=2"),
        ("P4A_WL_28", "k4", 2, False, "hub", make_hub_graph(False), stream_hub(False), "K4 k=2 approximate mixed dense hub defect=2"),
        ("P4A_WL_29", "k4", 2, True, "scalefree", make_scalefree_graph(True), stream_scalefree(True), "K4 k=2 approximate uniform scale-free defect=2"),
        ("P4A_WL_30", "k4", 2, True, "micro", make_micro_graph(True), stream_delete_heavy(True), "K4 k=2 delete-heavy stream defect=2")
    ]

    for cid, qtype, k, uniform, topo, init_g, updates, desc in configs:
        if qtype == "diamond":
            q = build_diamond_query() if uniform else build_diamond_query_mixed()
        else:
            q = build_k4_query() if uniform else build_k4_query_mixed()

        wl = {
            "case_id": cid,
            "description": desc,
            "query_type": qtype,
            "k": k,
            "regime": "uniform" if uniform else "mixed",
            "topology": topo,
            "max_work": 500000,
            "query": q,
            "initial_graph": init_g,
            "updates": updates
        }
        workloads.append(wl)

    manifest = {
        "manifest_version": "1.0",
        "description": "RapidFlow-APX Phase P4a Bounded Native Profiling & Measurement Workloads",
        "total_cases": len(workloads),
        "cases": workloads
    }
    return manifest

def main():
    manifest = generate_workloads()
    out_path = REPO_ROOT / "configs" / "rapidflow_apx_p4a_manifest.json"
    content = json.dumps(manifest, indent=2)
    out_path.write_text(content, encoding="utf-8")
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    print(f"Generated {manifest['total_cases']} workloads to {out_path}")
    print(f"Manifest SHA-256: {sha256}")

if __name__ == "__main__":
    main()
