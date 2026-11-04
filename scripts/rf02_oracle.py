#!/usr/bin/env python3
"""
scripts/rf02_oracle.py — Independent Ground Truth Oracle for Exact CSM.

Semantics:
- Continuous Subgraph Matching (CSM).
- Fixed query Q, dynamic data graph G.
- Exact, non-induced, injective, vertex- and edge-labeled subgraph monomorphism.
- Automorphisms are preserved: every distinct injective mapping tuple is enumerated.
- Pure Python 3 Standard Library (no external dependencies, no NetworkX).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Update:
    op: str  # '+' or '-'
    u: int
    v: int
    label: int

    def __post_init__(self):
        if self.op not in ("+", "-"):
            raise ValueError(f"Invalid update op: {self.op}")
        if self.u == self.v:
            raise ValueError(f"Self-loop update not allowed: {self.u} -> {self.v}")


class LabeledGraph:
    """Represents a labeled, simple, undirected graph."""

    def __init__(self):
        self.vertices: Dict[int, int] = {}  # vid -> vertex_label
        self.edges: Dict[Tuple[int, int], int] = {}  # (min(u, v), max(u, v)) -> edge_label
        self.adj: Dict[int, Dict[int, int]] = {}  # u -> {v: edge_label}

    def copy(self) -> LabeledGraph:
        new_g = LabeledGraph()
        new_g.vertices = dict(self.vertices)
        new_g.edges = dict(self.edges)
        new_g.adj = {u: dict(nbrs) for u, nbrs in self.adj.items()}
        return new_g

    def add_vertex(self, vid: int, label: int) -> None:
        if vid in self.vertices:
            if self.vertices[vid] != label:
                raise ValueError(f"Vertex {vid} redefined with conflicting label {label} vs {self.vertices[vid]}")
            return
        self.vertices[vid] = label
        if vid not in self.adj:
            self.adj[vid] = {}

    def add_edge(self, u: int, v: int, label: int) -> None:
        if u == v:
            raise ValueError(f"Self-loops are unsupported: ({u}, {v})")
        if u not in self.vertices or v not in self.vertices:
            raise ValueError(f"Edge ({u}, {v}) references non-existent vertex (vertices must be pre-declared)")

        edge_key = (min(u, v), max(u, v))
        if edge_key in self.edges:
            raise ValueError(f"Duplicate edge addition: {edge_key}")

        self.edges[edge_key] = label
        self.adj[u][v] = label
        self.adj[v][u] = label

    def remove_edge(self, u: int, v: int) -> None:
        edge_key = (min(u, v), max(u, v))
        if edge_key not in self.edges:
            raise ValueError(f"Cannot delete non-existent edge: ({u}, {v})")

        del self.edges[edge_key]
        del self.adj[u][v]
        del self.adj[v][u]

    def has_edge(self, u: int, v: int) -> bool:
        return (min(u, v), max(u, v)) in self.edges

    def get_edge_label(self, u: int, v: int) -> Optional[int]:
        return self.edges.get((min(u, v), max(u, v)))

    def apply_update(self, update: Update) -> None:
        if update.op == "+":
            self.add_edge(update.u, update.v, update.label)
        elif update.op == "-":
            edge_label = self.get_edge_label(update.u, update.v)
            if edge_label is None:
                raise ValueError(f"Cannot delete non-existent edge ({update.u}, {update.v})")
            if edge_label != update.label:
                raise ValueError(
                    f"Edge label mismatch on deletion: requested {update.label}, actual {edge_label}"
                )
            self.remove_edge(update.u, update.v)

    @classmethod
    def parse_from_text(cls, text: str) -> LabeledGraph:
        g = cls()
        for line_num, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            tag = tokens[0]
            if tag == "v":
                if len(tokens) < 3:
                    raise ValueError(f"Malformed vertex line {line_num}: {line}")
                vid = int(tokens[1])
                label = int(tokens[2])
                g.add_vertex(vid, label)
            elif tag == "e":
                if len(tokens) < 4:
                    raise ValueError(f"Malformed edge line {line_num}: {line}")
                u = int(tokens[1])
                v = int(tokens[2])
                label = int(tokens[3])
                g.add_edge(u, v, label)
            else:
                raise ValueError(f"Unsupported record tag '{tag}' at line {line_num}: {line}")
        return g

    @classmethod
    def parse_from_file(cls, path: str) -> LabeledGraph:
        with open(path, "r", encoding="utf-8") as f:
            return cls.parse_from_text(f.read())


def parse_stream_text(text: str) -> List[Update]:
    updates: List[Update] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        tag = tokens[0]
        if tag == "e":
            if len(tokens) < 4:
                raise ValueError(f"Malformed stream insertion line {line_num}: {line}")
            u = int(tokens[1])
            v = int(tokens[2])
            label = int(tokens[3])
            updates.append(Update("+", u, v, label))
        elif tag == "-e":
            if len(tokens) < 4:
                raise ValueError(f"Malformed stream deletion line {line_num}: {line}")
            u = int(tokens[1])
            v = int(tokens[2])
            label = int(tokens[3])
            updates.append(Update("-", u, v, label))
        else:
            raise ValueError(f"Unsupported stream tag '{tag}' at line {line_num}: {line}")
    return updates


def parse_stream_file(path: str) -> List[Update]:
    with open(path, "r", encoding="utf-8") as f:
        return parse_stream_text(f.read())


def find_all_embeddings(Q: LabeledGraph, G: LabeledGraph) -> List[Tuple[int, ...]]:
    """
    Find all exact non-induced subgraph monomorphisms from Q to G.
    Returns canonical sorted tuples: (f(q_0), f(q_1), ..., f(q_{k-1}))
    where q_0 < q_1 < ... < q_{k-1} are the sorted query vertex IDs.
    """
    q_vertices = sorted(Q.vertices.keys())
    k = len(q_vertices)
    if k == 0:
        return []
    if len(G.vertices) < k:
        return []

    # Precompute candidate sets for each query vertex based on vertex label and degree
    candidates: Dict[int, List[int]] = {}
    for q in q_vertices:
        q_label = Q.vertices[q]
        q_deg = len(Q.adj.get(q, {}))
        cands = [
            v for v, label in G.vertices.items()
            if label == q_label and len(G.adj.get(v, {})) >= q_deg
        ]
        if not cands:
            return []
        candidates[q] = cands

    # Determine matching order: most constrained query vertex first (fewest candidates, highest degree)
    # Start with the vertex with minimal candidate count
    ordered_q: List[int] = []
    remaining_q = set(q_vertices)

    # First vertex
    first_q = min(remaining_q, key=lambda q: (len(candidates[q]), -len(Q.adj.get(q, {}))))
    ordered_q.append(first_q)
    remaining_q.remove(first_q)

    # Greedily pick adjacent vertices to already ordered vertices
    while remaining_q:
        best_cand = None
        best_score = None
        for q in remaining_q:
            # count backward edges (edges to already ordered vertices)
            backward_edges = sum(1 for nbr in ordered_q if Q.has_edge(q, nbr))
            score = (-backward_edges, len(candidates[q]), -len(Q.adj.get(q, {})))
            if best_score is None or score < best_score:
                best_score = score
                best_cand = q
        ordered_q.append(best_cand)
        remaining_q.remove(best_cand)

    # Precompute backward neighbors and edge labels for each step in ordered_q
    step_info = []
    for idx, q in enumerate(ordered_q):
        backward = []
        for prev_idx in range(idx):
            prev_q = ordered_q[prev_idx]
            if Q.has_edge(q, prev_q):
                edge_label = Q.get_edge_label(q, prev_q)
                backward.append((prev_q, edge_label))
        step_info.append((q, backward))

    results: List[Tuple[int, ...]] = []
    current_mapping: Dict[int, int] = {}
    used_g_vertices: Set[int] = set()

    def backtrack(depth: int) -> None:
        if depth == k:
            # Form canonical tuple in sorted query vertex order
            res_tuple = tuple(current_mapping[q] for q in q_vertices)
            results.append(res_tuple)
            return

        q, backward_constraints = step_info[depth]
        cand_list = candidates[q]

        for v in cand_list:
            if v in used_g_vertices:
                continue

            # Check all backward edge constraints
            valid = True
            v_nbrs = G.adj.get(v, {})
            for prev_q, expected_edge_label in backward_constraints:
                prev_v = current_mapping[prev_q]
                if prev_v not in v_nbrs or v_nbrs[prev_v] != expected_edge_label:
                    valid = False
                    break

            if valid:
                current_mapping[q] = v
                used_g_vertices.add(v)
                backtrack(depth + 1)
                used_g_vertices.remove(v)
                del current_mapping[q]

    backtrack(0)
    results.sort()
    return results


def find_all_embeddings_brute_force(Q: LabeledGraph, G: LabeledGraph) -> List[Tuple[int, ...]]:
    """
    Exhaustive secondary permutation solver used strictly for cross-validation on small graphs.
    """
    q_vertices = sorted(Q.vertices.keys())
    k = len(q_vertices)
    g_vertices = sorted(G.vertices.keys())
    if len(g_vertices) < k:
        return []

    q_edges = list(Q.edges.items())
    results: List[Tuple[int, ...]] = []

    for perm in itertools.permutations(g_vertices, k):
        f = dict(zip(q_vertices, perm))

        # Check vertex labels
        if any(G.vertices[f[q]] != Q.vertices[q] for q in q_vertices):
            continue

        # Check all query edges
        valid = True
        for (u, v), edge_label in q_edges:
            fu, fv = f[u], f[v]
            if not G.has_edge(fu, fv) or G.get_edge_label(fu, fv) != edge_label:
                valid = False
                break

        if valid:
            results.append(tuple(f[q] for q in q_vertices))

    results.sort()
    return results


def canonical_mapping_hash(mappings: List[Tuple[int, ...]]) -> str:
    """Computes a deterministic SHA-256 hash over canonical mapping tuples."""
    canonical_str = json.dumps(mappings, separators=(",", ":"))
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def compute_update_delta(
    Q: LabeledGraph, G_before: LabeledGraph, update: Update
) -> Tuple[Set[Tuple[int, ...]], Set[Tuple[int, ...]], LabeledGraph]:
    """
    Applies single update to G_before to produce G_after, computing exact added and removed embeddings.
    """
    m_before = set(find_all_embeddings(Q, G_before))
    G_after = G_before.copy()
    G_after.apply_update(update)
    m_after = set(find_all_embeddings(Q, G_after))

    added = m_after - m_before
    removed = m_before - m_after

    if update.op == "+" and len(removed) > 0:
        raise RuntimeError(
            f"Edge insertion unexpectedly removed {len(removed)} mappings: non-induced monomorphism must be monotonic!"
        )
    if update.op == "-" and len(added) > 0:
        raise RuntimeError(
            f"Edge deletion unexpectedly added {len(added)} mappings: non-induced monomorphism must be monotonic!"
        )

    return added, removed, G_after


def main() -> None:
    parser = argparse.ArgumentParser(description="RF-02 Independent Oracle for Subgraph Monomorphism.")
    parser.add_argument("--query", required=True, help="Path to query.graph")
    parser.add_argument("--data", required=True, help="Path to initial data.graph")
    parser.add_argument("--stream", required=False, help="Path to stream.update")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    Q = LabeledGraph.parse_from_file(args.query)
    G = LabeledGraph.parse_from_file(args.data)

    initial_matches = find_all_embeddings(Q, G)

    if not args.stream:
        if args.json:
            print(json.dumps({
                "count": len(initial_matches),
                "mappings": initial_matches,
                "hash": canonical_mapping_hash(initial_matches),
            }, indent=2))
        else:
            print(f"Initial matches: {len(initial_matches)}")
            print(f"Mapping hash: {canonical_mapping_hash(initial_matches)}")
        return

    updates = parse_stream_file(args.stream)
    step_records = []
    current_G = G

    for idx, u in enumerate(updates, start=1):
        added, removed, next_G = compute_update_delta(Q, current_G, u)
        all_current = find_all_embeddings(Q, next_G)
        step_records.append({
            "step": idx,
            "op": u.op,
            "u": u.u,
            "v": u.v,
            "label": u.label,
            "added_count": len(added),
            "removed_count": len(removed),
            "total_count": len(all_current),
            "added_sample": sorted(list(added))[:5],
            "removed_sample": sorted(list(removed))[:5],
            "mapping_hash": canonical_mapping_hash(all_current),
        })
        current_G = next_G

    if args.json:
        print(json.dumps({
            "initial_count": len(initial_matches),
            "steps": step_records,
        }, indent=2))
    else:
        print(f"Initial matches: {len(initial_matches)}")
        for rec in step_records:
            print(
                f"Step {rec['step']} ({rec['op']} {rec['u']}-{rec['v']} L{rec['label']}): "
                f"+{rec['added_count']} / -{rec['removed_count']} -> total {rec['total_count']}"
            )


if __name__ == "__main__":
    main()
