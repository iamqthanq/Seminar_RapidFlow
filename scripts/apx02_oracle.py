#!/usr/bin/env python3
"""
scripts/apx02_oracle.py — Independent Ground-Truth Oracle for Approximate CSM.

APX-02 Reference Implementation:
- Implements Model C Approximate Continuous Subgraph Matching (CSM).
- Formal Acceptance Predicate:
    Accept_t(f) = Injective(f)
                and VertexLabelsOK(f)
                and [m_t(f) <= eps_m]
                and [x_out_t(f) <= eps_x]
                and ConnOK(f)
- Connectivity Modes:
    * STRICT: ConnOK_strict(f) = C_t(f) <=> components(G_t[S_f]) == 1
    * RELAXED_UNCONSTRAINED: ConnOK_relaxed(f) = True
- Scope Preconditions (v1):
    * Fixed Vertex Universe: V_t = V_0, for all t
    * Valid Updates Only: Atomic validation; explicit exceptions on invalid updates.
- Pure Python 3 Standard Library (no external matching dependencies, no RapidFlow C++ coupling).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


# ============================================================================
# Exceptions (Scope B — Atomic Validation & Explicit Errors)
# ============================================================================

class OracleError(Exception):
    """Base exception for all Oracle errors."""
    pass


class UnknownVertexError(OracleError):
    """Raised when an update or query references a vertex not in V_0."""
    pass


class DuplicateEdgeInsertionError(OracleError):
    """Raised when attempting to insert an edge that already exists."""
    pass


class NonexistentEdgeDeletionError(OracleError):
    """Raised when attempting to delete an edge that does not exist."""
    pass


class EdgeLabelMismatchDeletionError(OracleError):
    """Raised when attempting to delete an edge with mismatched label."""
    pass


class SelfLoopError(OracleError):
    """Raised when attempting to add or update a self-loop."""
    pass


class ResourceLimitError(OracleError):
    """Raised when search space exceeds configured combinatorial budget."""
    pass


class UnsupportedOperationError(OracleError):
    """Raised when encountering an unsupported stream operation."""
    pass


# ============================================================================
# Enums and Data Structures
# ============================================================================

class ConnectivityMode(str, Enum):
    STRICT = "STRICT"
    RELAXED_UNCONSTRAINED = "RELAXED_UNCONSTRAINED"


class DynamicEventType(str, Enum):
    ENTERED = "ENTERED"
    EXITED = "EXITED"
    SCORE_CHANGED = "SCORE_CHANGED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class Update:
    op: str  # '+' (insert) or '-' (delete)
    u: int
    v: int
    label: int

    def __post_init__(self):
        if self.op not in ("+", "-"):
            raise UnsupportedOperationError(f"Invalid update op '{self.op}', must be '+' or '-'")
        if self.u == self.v:
            raise SelfLoopError(f"Self-loops are unsupported: ({self.u}, {self.v})")

    @property
    def edge_key(self) -> Tuple[int, int]:
        return (min(self.u, self.v), max(self.u, self.v))


@dataclass(frozen=True)
class DeviationScore:
    m: int          # Missing query edges or query edges with mismatched labels
    x_in: int       # Extraneous internal chords (diagnostic; NOT penalized in Model C)
    x_out: int      # Extraneous external boundary cut edges
    connected: bool # Topological connectivity of G[f(V_Q)]

    def to_dict(self) -> dict:
        return {
            "m": self.m,
            "x_in": self.x_in,
            "x_out": self.x_out,
            "connected": self.connected,
        }


@dataclass(frozen=True)
class MappingEvaluation:
    mapping: Tuple[int, ...]  # Ordered mapping: (f(u_1), ..., f(u_k)) for sorted V_Q
    score: DeviationScore
    accepted: bool
    is_injective: bool
    vertex_labels_ok: bool
    reject_reasons: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "mapping": list(self.mapping),
            "score": self.score.to_dict(),
            "accepted": self.accepted,
            "is_injective": self.is_injective,
            "vertex_labels_ok": self.vertex_labels_ok,
            "reject_reasons": list(self.reject_reasons),
        }


# ============================================================================
# Simple Labeled Graph Representation
# ============================================================================

class SimpleGraph:
    """
    Represents an undirected, simple, labeled graph G = (V, E, l).
    Endpoints are stored canonically with min(u, v) < max(u, v).
    """

    def __init__(self):
        self.vertices: Dict[int, int] = {}  # vid -> vertex_label
        self.edges: Dict[Tuple[int, int], int] = {}  # (min(u,v), max(u,v)) -> edge_label
        self.adj: Dict[int, Dict[int, int]] = {}  # u -> {v: edge_label}

    def clone(self) -> SimpleGraph:
        g = SimpleGraph()
        g.vertices = dict(self.vertices)
        g.edges = dict(self.edges)
        g.adj = {u: dict(nbrs) for u, nbrs in self.adj.items()}
        return g

    def add_vertex(self, vid: int, label: int = 0) -> None:
        if vid in self.vertices:
            if self.vertices[vid] != label:
                raise ValueError(f"Vertex {vid} redefined with conflicting label {label} vs {self.vertices[vid]}")
            return
        self.vertices[vid] = label
        if vid not in self.adj:
            self.adj[vid] = {}

    def add_edge(self, u: int, v: int, label: int = 0) -> None:
        if u == v:
            raise SelfLoopError(f"Self-loops are unsupported: ({u}, {v})")
        if u not in self.vertices:
            raise UnknownVertexError(f"Endpoint {u} not in vertex universe")
        if v not in self.vertices:
            raise UnknownVertexError(f"Endpoint {v} not in vertex universe")

        key = (min(u, v), max(u, v))
        if key in self.edges:
            raise DuplicateEdgeInsertionError(f"Edge {key} already exists with label {self.edges[key]}")

        self.edges[key] = label
        self.adj[u][v] = label
        self.adj[v][u] = label

    def remove_edge(self, u: int, v: int, expected_label: Optional[int] = None) -> None:
        if u not in self.vertices or v not in self.vertices:
            raise UnknownVertexError(f"Endpoint {u} or {v} not in vertex universe")

        key = (min(u, v), max(u, v))
        if key not in self.edges:
            raise NonexistentEdgeDeletionError(f"Edge {key} does not exist")

        actual_label = self.edges[key]
        if expected_label is not None and actual_label != expected_label:
            raise EdgeLabelMismatchDeletionError(
                f"Edge {key} has label {actual_label}, expected {expected_label} for deletion"
            )

        del self.edges[key]
        del self.adj[u][v]
        del self.adj[v][u]

    def has_edge(self, u: int, v: int) -> bool:
        return (min(u, v), max(u, v)) in self.edges

    def get_edge_label(self, u: int, v: int) -> Optional[int]:
        return self.edges.get((min(u, v), max(u, v)))

    def degree(self, u: int) -> int:
        return len(self.adj.get(u, {}))

    def neighbors(self, u: int) -> Set[int]:
        return set(self.adj.get(u, {}).keys())

    @classmethod
    def from_text(cls, text: str) -> SimpleGraph:
        g = cls()
        for line_num, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            tag = tokens[0]
            if tag == "v":
                if len(tokens) < 3:
                    raise ValueError(f"Malformed vertex record at line {line_num}: '{line}'")
                vid = int(tokens[1])
                label = int(tokens[2])
                g.add_vertex(vid, label)
            elif tag == "e":
                if len(tokens) < 4:
                    raise ValueError(f"Malformed edge record at line {line_num}: '{line}'")
                u = int(tokens[1])
                v = int(tokens[2])
                label = int(tokens[3])
                if u not in g.vertices:
                    raise UnknownVertexError(
                        f"Endpoint {u} not declared prior to edge record at line {line_num}: '{line}'"
                    )
                if v not in g.vertices:
                    raise UnknownVertexError(
                        f"Endpoint {v} not declared prior to edge record at line {line_num}: '{line}'"
                    )
                g.add_edge(u, v, label)
            elif tag == "t":
                # Header tag in some graph formats, ignore or log
                continue
            else:
                raise ValueError(f"Unsupported record tag '{tag}' at line {line_num}: '{line}'")
        return g

    @classmethod
    def from_file(cls, path: str) -> SimpleGraph:
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_text(f.read())


def parse_stream_text(text: str) -> List[Update]:
    updates: List[Update] = []
    for line_num, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        tag = tokens[0]
        if tag == "e":
            if len(tokens) < 4:
                raise ValueError(f"Malformed stream insertion line {line_num}: '{line}'")
            u = int(tokens[1])
            v = int(tokens[2])
            label = int(tokens[3])
            updates.append(Update("+", u, v, label))
        elif tag == "-e":
            if len(tokens) < 4:
                raise ValueError(f"Malformed stream deletion line {line_num}: '{line}'")
            u = int(tokens[1])
            v = int(tokens[2])
            label = int(tokens[3])
            updates.append(Update("-", u, v, label))
        else:
            raise ValueError(f"Unsupported stream tag '{tag}' at line {line_num}: '{line}'")
    return updates


def parse_stream_file(path: str) -> List[Update]:
    with open(path, "r", encoding="utf-8") as f:
        return parse_stream_text(f.read())


# ============================================================================
# Core Scoring & Evaluation Logic
# ============================================================================

def validate_thresholds(
    eps_m: int,
    eps_x: Optional[int],
    max_candidates: Optional[int] = None,
) -> None:
    """
    Validates parameter domain according to APX-01 / APX-02 specifications:
    - eps_m in N_0 (non-negative integer)
    - eps_x in N_0 union {None} (non-negative integer or None for infinity)
    - max_candidates in N^+ (strictly positive integer)
    """
    if isinstance(eps_m, bool) or not isinstance(eps_m, int) or eps_m < 0:
        raise ValueError(f"eps_m must be a non-negative integer, got {eps_m!r}")
    if eps_x is not None:
        if isinstance(eps_x, bool) or not isinstance(eps_x, int) or eps_x < 0:
            raise ValueError(f"eps_x must be None or a non-negative integer, got {eps_x!r}")
    if max_candidates is not None:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates <= 0:
            raise ValueError(f"max_candidates must be a positive integer, got {max_candidates!r}")


def evaluate_mapping(
    Q: SimpleGraph,
    G: SimpleGraph,
    mapping: Sequence[int],
    eps_m: int,
    eps_x: Optional[int],
    mode: ConnectivityMode = ConnectivityMode.STRICT,
) -> MappingEvaluation:
    """
    Evaluates a specific ordered mapping tuple f: V_Q -> V_G.
    Query vertices V_Q are indexed in canonical sorted order: q_0 < q_1 < ... < q_{k-1}.
    Mapping sequence: mapping[i] = f(q_i).

    Computes:
    - Injectivity & hard vertex labels.
    - m(f): count of required query edges not matching in G.
    - x_in(f): extraneous internal edges between mapped vertices not in E_Q.
    - x_out(f): extraneous boundary edges leaving mapped set S_f.
    - C_t(f): topological connectivity of G[S_f].
    - Acceptance according to Model C:
        Accept_t(f) = Injective(f)
                    and VertexLabelsOK(f)
                    and m_t(f) <= eps_m
                    and (eps_x is None or x_out_t(f) <= eps_x)
                    and ConnOK(f)
    """
    validate_thresholds(eps_m, eps_x)
    q_nodes = sorted(Q.vertices.keys())
    k = len(q_nodes)

    if len(mapping) != k:
        raise ValueError(f"Mapping length ({len(mapping)}) does not match query size ({k})")

    reject_reasons: List[str] = []

    # 1. Injectivity Check
    mapped_nodes = list(mapping)
    is_injective = (len(set(mapped_nodes)) == k)
    if not is_injective:
        reject_reasons.append("NON_INJECTIVE")

    # Mapping dictionary: q_node -> g_node
    f = {q_nodes[i]: mapped_nodes[i] for i in range(k)}
    img_V = set(mapped_nodes)

    # 2. Hard Vertex Labels Check
    vertex_labels_ok = True
    for q_u in q_nodes:
        g_v = f[q_u]
        if g_v not in G.vertices:
            vertex_labels_ok = False
            reject_reasons.append(f"VERTEX_NOT_IN_GRAPH:{g_v}")
        elif G.vertices[g_v] != Q.vertices[q_u]:
            vertex_labels_ok = False
            reject_reasons.append(f"VERTEX_LABEL_MISMATCH:q{q_u}({Q.vertices[q_u]})!=g{g_v}({G.vertices[g_v]})")

    # 3. Missing-Edge Deviation m(f)
    # required_edge_matches(u, v, f, t) <=> {f(u), f(v)} in E_t and l_t({f(u), f(v)}) == l_Q({u, v})
    # m_t(f) = sum_{e in E_Q} 1[not required_edge_matches]
    m = 0
    for (u1, u2), q_label in Q.edges.items():
        v1, v2 = f[u1], f[u2]
        if v1 == v2:
            # Non-injective collapse on an edge
            m += 1
            continue
        g_label = G.get_edge_label(v1, v2)
        if g_label is None:
            # Edge absent in data graph
            m += 1
        elif g_label != q_label:
            # Edge present in topology but label mismatch
            m += 1

    # 4. Extraneous Internal Extra Chords x_in(f) (Diagnostic only)
    # Count edges in G[S_f] that do NOT correspond to any required query edge in E_Q.
    # Note: If an edge in E_Q exists in G but has a wrong label, it was counted in m(f),
    # but does NOT count towards x_in because {u, v} IS in E_Q (validated by Case 09).
    x_in = 0
    if is_injective:
        # Precompute set of query edge pairs mapped to data vertices
        q_edge_pairs = set()
        for (u1, u2) in Q.edges.keys():
            q_edge_pairs.add((min(f[u1], f[u2]), max(f[u1], f[u2])))

        for v1 in img_V:
            for v2, _ in G.adj.get(v1, {}).items():
                if v2 in img_V and v1 < v2:
                    if (v1, v2) not in q_edge_pairs:
                        x_in += 1

    # 5. Extraneous External Boundary Edges x_out(f)
    # Edges crossing cut (S_f, V_G \ S_f).
    # Summing over v in S_f counts each boundary edge exactly once.
    x_out = 0
    for v in img_V:
        for nbr in G.adj.get(v, {}).keys():
            if nbr not in img_V:
                x_out += 1

    # 6. Topological Connectivity C_t(f)
    # Evaluated on induced data graph G[S_f] using topological edges (regardless of edge label).
    is_connected = False
    if not img_V:
        is_connected = False
    else:
        # BFS traversal within S_f
        start_node = next(iter(img_V))
        visited: Set[int] = {start_node}
        queue = [start_node]
        while queue:
            curr = queue.pop(0)
            for nbr in G.adj.get(curr, {}).keys():
                if nbr in img_V and nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)
        is_connected = (len(visited) == len(img_V))

    score = DeviationScore(
        m=m,
        x_in=x_in,
        x_out=x_out,
        connected=is_connected,
    )

    # 7. Model C Acceptance Filter
    # Accept_t(f) = Injective(f) and VertexLabelsOK(f) and [m <= eps_m] and [x_out <= eps_x] and ConnOK(f)
    accepted = True

    if not is_injective:
        accepted = False
    if not vertex_labels_ok:
        accepted = False
    if m > eps_m:
        accepted = False
        reject_reasons.append(f"M_EXCEEDED:{m}>{eps_m}")
    if eps_x is not None and x_out > eps_x:
        accepted = False
        reject_reasons.append(f"X_OUT_EXCEEDED:{x_out}>{eps_x}")

    if mode == ConnectivityMode.STRICT:
        if not is_connected:
            accepted = False
            reject_reasons.append("STRICT_CONNECTIVITY_VIOLATED")
    elif mode == ConnectivityMode.RELAXED_UNCONSTRAINED:
        # ConnOK_relaxed(f) = True unconditionally
        pass
    else:
        raise ValueError(f"Unknown connectivity mode: {mode}")

    return MappingEvaluation(
        mapping=tuple(mapped_nodes),
        score=score,
        accepted=accepted,
        is_injective=is_injective,
        vertex_labels_ok=vertex_labels_ok,
        reject_reasons=tuple(reject_reasons),
    )


def find_approximate_matches(
    Q: SimpleGraph,
    G: SimpleGraph,
    eps_m: int,
    eps_x: Optional[int],
    mode: ConnectivityMode = ConnectivityMode.STRICT,
    max_candidates: int = 10_000_000,
) -> Dict[Tuple[int, ...], MappingEvaluation]:
    """
    Exhaustively finds all accepted ordered injective mappings f: V_Q -> V_G
    under Model C semantics.

    Query vertices V_Q are indexed in canonical sorted order: q_0 < q_1 < ... < q_{k-1}.
    Returns a dictionary mapping candidate tuple (f(q_0), ..., f(q_{k-1})) -> MappingEvaluation.
    Only accepted mappings are included in the return dict (sorted deterministically).
    """
    validate_thresholds(eps_m, eps_x, max_candidates)
    q_nodes = sorted(Q.vertices.keys())
    k = len(q_nodes)

    if k == 0:
        return {}

    g_nodes = sorted(G.vertices.keys())
    n = len(g_nodes)

    if n < k:
        return {}

    # Candidate pre-filtering: ONLY hard vertex labels are pre-filtered.
    # No degree filtering! (A near-match vertex in G can have degree < degree in Q due to missing edges).
    candidate_pools: List[List[int]] = []
    for q_u in q_nodes:
        req_label = Q.vertices[q_u]
        pool = [v for v in g_nodes if G.vertices[v] == req_label]
        if not pool:
            return {}
        candidate_pools.append(pool)

    # Combinatorial explosion guard
    # Theoretical upper bound on permutations of candidate pool sizes
    # If all vertices share the same label: P(n, k) = n! / (n - k)!
    upper_bound = 1
    for i in range(k):
        upper_bound *= len(candidate_pools[i])
        if upper_bound > max_candidates:
            raise ResourceLimitError(
                f"Candidate space upper bound ({upper_bound}) exceeds safety budget ({max_candidates}). "
                f"Use evaluate_mapping() for large probe graphs."
            )

    accepted_results: Dict[Tuple[int, ...], MappingEvaluation] = {}

    def backtrack(depth: int, current_assignment: List[int], used_vertices: Set[int]):
        if depth == k:
            candidate_tuple = tuple(current_assignment)
            eval_res = evaluate_mapping(Q, G, candidate_tuple, eps_m, eps_x, mode)
            if eval_res.accepted:
                accepted_results[candidate_tuple] = eval_res
            return

        for v in candidate_pools[depth]:
            if v not in used_vertices:
                used_vertices.add(v)
                current_assignment.append(v)
                backtrack(depth + 1, current_assignment, used_vertices)
                current_assignment.pop()
                used_vertices.remove(v)

    backtrack(0, [], set())

    # Return deterministically sorted by mapping tuple
    return dict(sorted(accepted_results.items()))


# ============================================================================
# Dynamic Oracle (Recompute-and-Diff with Atomic Validation)
# ============================================================================

@dataclass
class DynamicDelta:
    entered: Dict[Tuple[int, ...], DeviationScore]
    exited: Dict[Tuple[int, ...], DeviationScore]
    score_changed: Dict[Tuple[int, ...], Tuple[DeviationScore, DeviationScore]]  # mapping -> (old_score, new_score)
    unchanged: Dict[Tuple[int, ...], DeviationScore]

    def to_dict(self) -> dict:
        return {
            "ENTERED": {str(k): v.to_dict() for k, v in self.entered.items()},
            "EXITED": {str(k): v.to_dict() for k, v in self.exited.items()},
            "SCORE_CHANGED": {
                str(k): {"old": old.to_dict(), "new": new.to_dict()}
                for k, (old, new) in self.score_changed.items()
            },
            "UNCHANGED": {str(k): v.to_dict() for k, v in self.unchanged.items()},
        }


class DynamicOracle:
    r"""
    Maintains graph G_t and precomputed accepted mappings M_t.
    Updates are applied one-by-one with atomic precondition validation.
    Delta is calculated via exact Recompute-and-Diff:
    - ENTERED: M_t \ M_{t-1}
    - EXITED: M_{t-1} \ M_t
    - SCORE_CHANGED: {f in M_{t-1} intersect M_t : (m_{t-1}, x_out_{t-1}) != (m_t, x_out_t)}
    """

    def __init__(
        self,
        Q: SimpleGraph,
        G0: SimpleGraph,
        eps_m: int,
        eps_x: Optional[int],
        mode: ConnectivityMode = ConnectivityMode.STRICT,
        max_candidates: int = 10_000_000,
    ):
        validate_thresholds(eps_m, eps_x, max_candidates)
        self.Q = Q
        self.G = G0.clone()
        self.eps_m = eps_m
        self.eps_x = eps_x
        self.mode = mode
        self.max_candidates = max_candidates

        # Compute initial state M_0
        self.current_matches = find_approximate_matches(
            self.Q, self.G, self.eps_m, self.eps_x, self.mode, self.max_candidates
        )
        self.step = 0

    def validate_update(self, update: Update) -> None:
        """
        Atomic validation (Scope B).
        Validates update preconditions without modifying graph state.
        Raises OracleError on any invalid update.
        """
        if update.u not in self.G.vertices:
            raise UnknownVertexError(f"Endpoint {update.u} does not exist in V_0")
        if update.v not in self.G.vertices:
            raise UnknownVertexError(f"Endpoint {update.v} does not exist in V_0")
        if update.u == update.v:
            raise SelfLoopError(f"Self-loop update ({update.u}, {update.v}) is unsupported")

        edge_key = update.edge_key
        if update.op == "+":
            if edge_key in self.G.edges:
                raise DuplicateEdgeInsertionError(
                    f"Cannot insert edge {edge_key}: already exists with label {self.G.edges[edge_key]}"
                )
        elif update.op == "-":
            if edge_key not in self.G.edges:
                raise NonexistentEdgeDeletionError(f"Cannot delete non-existent edge {edge_key}")
            actual_label = self.G.edges[edge_key]
            if actual_label != update.label:
                raise EdgeLabelMismatchDeletionError(
                    f"Cannot delete edge {edge_key}: expected label {update.label}, actual {actual_label}"
                )

    def apply_update(self, update: Update) -> DynamicDelta:
        """
        Applies a single valid edge update and returns the exact differential event delta.
        Validates atomically before making any mutation.
        """
        # 1. Atomic validation
        self.validate_update(update)

        # 2. State before update
        old_matches = self.current_matches

        # 3. Apply mutation
        if update.op == "+":
            self.G.add_edge(update.u, update.v, update.label)
        elif update.op == "-":
            self.G.remove_edge(update.u, update.v, update.label)

        # 4. Recompute new accepted mappings M_t
        new_matches = find_approximate_matches(
            self.Q, self.G, self.eps_m, self.eps_x, self.mode, self.max_candidates
        )

        # 5. Differential Analysis (Recompute-and-Diff)
        entered: Dict[Tuple[int, ...], DeviationScore] = {}
        exited: Dict[Tuple[int, ...], DeviationScore] = {}
        score_changed: Dict[Tuple[int, ...], Tuple[DeviationScore, DeviationScore]] = {}
        unchanged: Dict[Tuple[int, ...], DeviationScore] = {}

        for mapping, eval_res in new_matches.items():
            if mapping not in old_matches:
                entered[mapping] = eval_res.score
            else:
                old_score = old_matches[mapping].score
                new_score = eval_res.score
                # SCORE_CHANGED is strictly defined by change in Model C score tuple (m, x_out)
                # Changes in internal chords x_in alone do NOT cause SCORE_CHANGED
                if (old_score.m, old_score.x_out) != (new_score.m, new_score.x_out):
                    score_changed[mapping] = (old_score, new_score)
                else:
                    unchanged[mapping] = new_score

        for mapping, eval_res in old_matches.items():
            if mapping not in new_matches:
                exited[mapping] = eval_res.score

        self.current_matches = new_matches
        self.step += 1

        return DynamicDelta(
            entered=entered,
            exited=exited,
            score_changed=score_changed,
            unchanged=unchanged,
        )


# ============================================================================
# Command Line Interface (CLI)
# ============================================================================

def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independent Approximate CSM Ground-Truth Oracle (APX-02)"
    )
    parser.add_argument("--query", "-q", required=True, help="Path to query graph file")
    parser.add_argument("--data", "-d", required=True, help="Path to initial data graph file")
    parser.add_argument("--eps-m", type=int, default=1, help="Missing edge threshold epsilon_m (default: 1)")
    parser.add_argument(
        "--eps-x",
        type=str,
        default="2",
        help="External clutter threshold epsilon_x ('none' or integer, default: 2)",
    )
    parser.add_argument(
        "--mode",
        choices=["STRICT", "RELAXED_UNCONSTRAINED"],
        default="STRICT",
        help="Connectivity mode (default: STRICT)",
    )
    parser.add_argument("--stream", "-s", help="Optional path to dynamic stream update file")
    parser.add_argument("--output", "-o", help="Optional output JSON path")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=10_000_000,
        help="Maximum candidate space upper bound (default: 10,000,000)",
    )
    return parser


def parse_eps_x(val_str: str) -> Optional[int]:
    val = val_str.strip().lower()
    if val in ("none", "null", "inf", "infinity"):
        return None
    try:
        res = int(val)
    except ValueError:
        raise ValueError(f"Invalid eps_x value: '{val_str}'. Expected integer or 'none'/'inf'")
    if res < 0:
        raise ValueError(f"eps_x must be non-negative, got {res}")
    return res


def main():
    parser = build_cli_parser()
    args = parser.parse_args()

    try:
        eps_x = parse_eps_x(args.eps_x)
        validate_thresholds(args.eps_m, eps_x, args.max_candidates)
    except ValueError as ex:
        sys.stderr.write(f"Parameter Error: {ex}\n")
        sys.exit(2)

    try:
        mode = ConnectivityMode(args.mode)
    except ValueError as ex:
        sys.stderr.write(f"Invalid mode: {ex}\n")
        sys.exit(2)

    try:
        print(f"[APX-02 ORACLE] Loading Query from {args.query}...")
        Q = SimpleGraph.from_file(args.query)
        print(f"[APX-02 ORACLE] Loading Data Graph from {args.data}...")
        G0 = SimpleGraph.from_file(args.data)
    except Exception as ex:
        sys.stderr.write(f"Graph Load Error: {ex}\n")
        sys.exit(1)

    print(
        f"[APX-02 ORACLE] Configuration: eps_m={args.eps_m}, eps_x={eps_x}, mode={mode.value}"
    )

    try:
        if not args.stream:
            # Static Oracle evaluation
            matches = find_approximate_matches(
                Q, G0, args.eps_m, eps_x, mode, args.max_candidates
            )
            print(f"[APX-02 ORACLE] Static Evaluation Complete: {len(matches)} accepted matches found.")
            for mapping, eval_res in matches.items():
                print(f"  Mapping {mapping} -> m={eval_res.score.m}, x_in={eval_res.score.x_in}, x_out={eval_res.score.x_out}, conn={eval_res.score.connected}")

            if args.output:
                out_data = {
                    "query": args.query,
                    "data": args.data,
                    "eps_m": args.eps_m,
                    "eps_x": eps_x,
                    "mode": mode.value,
                    "total_accepted": len(matches),
                    "matches": [eval_res.to_dict() for eval_res in matches.values()],
                }
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(out_data, f, indent=2)
                print(f"[APX-02 ORACLE] Output serialized to {args.output}")

        else:
            # Dynamic Oracle evaluation
            print(f"[APX-02 ORACLE] Loading Stream from {args.stream}...")
            stream_updates = parse_stream_file(args.stream)
            dyn_oracle = DynamicOracle(
                Q, G0, args.eps_m, eps_x, mode, args.max_candidates
            )
            print(f"[APX-02 ORACLE] Step 0 (Initial Snapshot): {len(dyn_oracle.current_matches)} matches.")

            history = []
            for step_idx, update in enumerate(stream_updates, start=1):
                delta = dyn_oracle.apply_update(update)
                print(
                    f"[Step {step_idx}] {update.op} ({update.u}, {update.v}, L={update.label}) -> "
                    f"ENTERED={len(delta.entered)}, EXITED={len(delta.exited)}, "
                    f"SCORE_CHANGED={len(delta.score_changed)}, TOTAL={len(dyn_oracle.current_matches)}"
                )
                history.append({
                    "step": step_idx,
                    "update": asdict(update),
                    "delta": delta.to_dict(),
                    "total_active": len(dyn_oracle.current_matches),
                })

            if args.output:
                out_data = {
                    "query": args.query,
                    "initial_data": args.data,
                    "stream": args.stream,
                    "eps_m": args.eps_m,
                    "eps_x": eps_x,
                    "mode": mode.value,
                    "total_steps": len(stream_updates),
                    "final_active_matches": len(dyn_oracle.current_matches),
                    "history": history,
                }
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(out_data, f, indent=2)
                print(f"[APX-02 ORACLE] Dynamic history serialized to {args.output}")
    except OracleError as ex:
        sys.stderr.write(f"Oracle Execution Error: {ex}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
