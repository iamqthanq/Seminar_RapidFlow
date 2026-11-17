#!/usr/bin/env python3
"""
scripts/dstar_oracle.py — Independent Ground-Truth Oracle for D* CSM.

D* Reference Implementation:
- Edge Recall objective: S_E(f) = (m - |R(f)|) / m, missing budget |R(f)| <= k.
- S2 Connectivity: matched query edges Q - R(f) must be spanning and connected on V_Q.
- Theoretical Domain: 0 <= k < lambda(Q), where lambda(Q) is global edge connectivity.
- Dynamic Events: ENTERED = M_after - M_before, EXITED = M_before - M_after. No SCORE_CHANGED.
- Pure Python 3 Standard Library only (zero external matching dependencies).
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

# Default resource caps as specified in Issue #22
DEFAULT_MAX_DATA_VERTICES = 8
DEFAULT_MAX_QUERY_VERTICES = 6
DEFAULT_MAX_CANDIDATES = 100000


# ============================================================================
# Exceptions (Strict, Atomic & Fail-Closed)
# ============================================================================

class DStarError(Exception):
    """Base exception for all D* Oracle errors."""
    pass


class UnknownVertexError(DStarError):
    """Raised when an update or mapping references a vertex not in V_0."""
    pass


class DuplicateEdgeInsertionError(DStarError):
    """Raised when attempting to insert an edge that already exists."""
    pass


class NonexistentEdgeDeletionError(DStarError):
    """Raised when attempting to delete an edge that does not exist."""
    pass


class EdgeLabelMismatchDeletionError(DStarError):
    """Raised when deleting an edge whose existing label does not match."""
    pass


class SelfLoopError(DStarError):
    """Raised when an edge update or graph definition contains a self-loop."""
    pass


class InvalidGraphError(DStarError):
    """Raised when query or data graph violates topological or labeling preconditions."""
    pass


class InvalidThresholdError(DStarError):
    """Raised when threshold k violates the contract 0 <= k < lambda(Q)."""
    pass


class ResourceLimitError(DStarError):
    """Raised when candidate space or graph size exceeds resource safety bounds."""
    pass


class UnsupportedOperationError(DStarError):
    """Raised for unsupported operations (e.g. batch updates, vertex updates)."""
    pass


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_vertex_id(v: Any) -> int:
    """Strictly validates that a vertex ID is an integer (rejecting bool, float, str, None)."""
    if isinstance(v, bool) or not isinstance(v, int):
        raise InvalidGraphError(f"Vertex ID must be an integer, got {type(v).__name__}: {v!r}")
    return v


def validate_label(lbl: Any, field_name: str = "Label") -> Any:
    """Strictly validates that a label is int or str (rejecting bool, None, float, list, dict)."""
    if isinstance(lbl, bool) or not isinstance(lbl, (int, str)):
        raise InvalidGraphError(f"{field_name} must be an int or str, got {type(lbl).__name__}: {lbl!r}")
    return lbl


def parse_vertex_id_string(s: Any) -> int:
    """
    Parses a vertex ID from a JSON object string key.
    Strictly accepts canonical integer representations like '0', '1', '-2'.
    Rejects floats ('1.5'), bool ('True'), empty string, leading zeros ('01', '-0').
    """
    if not isinstance(s, str):
        raise InvalidGraphError(f"Dict key for vertex ID must be a string, got {type(s).__name__}: {s!r}")
    if not s:
        raise InvalidGraphError("Empty string cannot be a vertex ID")
    if s != "0" and s.startswith("0"):
        raise InvalidGraphError(f"Invalid integer string format (leading zero forbidden): {s!r}")
    if s.startswith("-0"):
        raise InvalidGraphError(f"Invalid integer string format (-0 forbidden): {s!r}")
    try:
        val = int(s)
    except ValueError:
        raise InvalidGraphError(f"Cannot parse vertex ID from string: {s!r}")
    if str(val) != s:
        raise InvalidGraphError(f"Non-canonical integer string in vertex key: {s!r}")
    return val


def validate_resource_caps(
    max_candidates: int,
    max_data_vertices: int,
    max_query_vertices: int,
) -> None:
    """Validates that resource safety caps are positive finite integers (rejecting bool, float, nonpositive)."""
    for name, val in [
        ("max_candidates", max_candidates),
        ("max_data_vertices", max_data_vertices),
        ("max_query_vertices", max_query_vertices),
    ]:
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ResourceLimitError(f"{name} must be a positive integer, got {type(val).__name__}: {val!r}")


# ============================================================================
# Graph Data Structure
# ============================================================================

class DStarGraph:
    """
    Undirected, simple, labeled graph representation using Python stdlib.
    Vertices have immutable IDs (int) and labels (int or str).
    Edges are stored canonically as (min(u, v), max(u, v)) -> label.
    """

    def __init__(self) -> None:
        self.vertices: Dict[int, Any] = {}
        self.edges: Dict[Tuple[int, int], Any] = {}
        self.adj: Dict[int, Dict[int, Any]] = {}

    def add_vertex(self, v: int, label: Any) -> None:
        v_id = validate_vertex_id(v)
        lbl = validate_label(label, "Vertex label")
        if v_id in self.vertices:
            if self.vertices[v_id] != lbl:
                raise InvalidGraphError(f"Conflicting label for vertex {v_id}: {self.vertices[v_id]!r} vs {lbl!r}")
            return
        self.vertices[v_id] = lbl
        self.adj[v_id] = {}

    def add_edge(self, u: int, v: int, label: Any) -> None:
        u_id = validate_vertex_id(u)
        v_id = validate_vertex_id(v)
        lbl = validate_label(label, "Edge label")

        if u_id == v_id:
            raise SelfLoopError(f"Self-loops are forbidden: ({u_id}, {v_id})")
        if u_id not in self.vertices:
            raise UnknownVertexError(f"Undeclared endpoint vertex {u_id}")
        if v_id not in self.vertices:
            raise UnknownVertexError(f"Undeclared endpoint vertex {v_id}")

        edge_key = (min(u_id, v_id), max(u_id, v_id))
        if edge_key in self.edges:
            raise DuplicateEdgeInsertionError(f"Edge {edge_key} already exists in graph")

        self.edges[edge_key] = lbl
        self.adj[u_id][v_id] = lbl
        self.adj[v_id][u_id] = lbl

    def remove_edge(self, u: int, v: int, label: Any) -> None:
        u_id = validate_vertex_id(u)
        v_id = validate_vertex_id(v)
        lbl = validate_label(label, "Edge label")

        if u_id not in self.vertices:
            raise UnknownVertexError(f"Endpoint vertex {u_id} not in graph")
        if v_id not in self.vertices:
            raise UnknownVertexError(f"Endpoint vertex {v_id} not in graph")

        edge_key = (min(u_id, v_id), max(u_id, v_id))
        if not self.has_edge(u_id, v_id):
            raise NonexistentEdgeDeletionError(f"Edge {edge_key} does not exist in graph")

        existing_label = self.edges[edge_key]
        if existing_label != lbl:
            raise EdgeLabelMismatchDeletionError(
                f"Edge {edge_key} has label {existing_label!r}, delete requested {lbl!r}"
            )

        del self.edges[edge_key]
        del self.adj[u_id][v_id]
        del self.adj[v_id][u_id]

    def has_edge(self, u: int, v: int) -> bool:
        if isinstance(u, bool) or not isinstance(u, int) or isinstance(v, bool) or not isinstance(v, int):
            return False
        return (min(u, v), max(u, v)) in self.edges

    def get_edge_label(self, u: int, v: int) -> Optional[Any]:
        if isinstance(u, bool) or not isinstance(u, int) or isinstance(v, bool) or not isinstance(v, int):
            return None
        return self.edges.get((min(u, v), max(u, v)))

    def degree(self, v: int) -> int:
        return len(self.adj.get(v, {}))

    def clone(self) -> DStarGraph:
        g = DStarGraph()
        g.vertices = dict(self.vertices)
        g.edges = dict(self.edges)
        g.adj = {u: dict(nbrs) for u, nbrs in self.adj.items()}
        return g

    def validate_as_query(self) -> None:
        """Validates that this graph satisfies the D* query contract."""
        n = len(self.vertices)
        if n < 2:
            raise InvalidGraphError(f"Query must have at least 2 vertices, got {n}")

        if not is_connected_graph(set(self.vertices.keys()), self.edges.keys()):
            raise InvalidGraphError("Query graph must be connected")

    def validate_as_data(self) -> None:
        """Validates that this graph satisfies the D* data graph contract."""
        if not self.vertices:
            raise InvalidGraphError("Data graph vertex universe cannot be empty")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vertices": {str(v): lbl for v, lbl in sorted(self.vertices.items())},
            "edges": [{"u": u, "v": v, "label": lbl} for (u, v), lbl in sorted(self.edges.items())],
        }

    @classmethod
    def from_dict(cls, data: Any) -> DStarGraph:
        if not isinstance(data, dict):
            raise InvalidGraphError(f"Graph data must be a dict, got {type(data).__name__}")

        g = cls()
        if "vertices" not in data:
            raise InvalidGraphError("Missing required field 'vertices' in graph dict")
        raw_vertices = data["vertices"]

        if isinstance(raw_vertices, dict):
            seen_ids: Set[int] = set()
            for v_str, lbl in raw_vertices.items():
                v_id = parse_vertex_id_string(v_str)
                if v_id in seen_ids:
                    raise InvalidGraphError(f"Collision in vertex ID normalization: {v_id}")
                seen_ids.add(v_id)
                g.add_vertex(v_id, lbl)
        elif isinstance(raw_vertices, list):
            seen_ids = set()
            for item in raw_vertices:
                if isinstance(item, dict):
                    if "id" not in item:
                        raise InvalidGraphError("Missing required field 'id' in vertex spec")
                    if "label" not in item:
                        raise InvalidGraphError("Missing required field 'label' in vertex spec")
                    v_id = validate_vertex_id(item["id"])
                    lbl = item["label"]
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    v_id = validate_vertex_id(item[0])
                    lbl = item[1]
                else:
                    raise InvalidGraphError(f"Invalid vertex spec in list: {item!r}")
                if v_id in seen_ids:
                    raise InvalidGraphError(f"Duplicate vertex ID in vertex list: {v_id}")
                seen_ids.add(v_id)
                g.add_vertex(v_id, lbl)
        else:
            raise InvalidGraphError(f"Unexpected vertices type: {type(raw_vertices).__name__}")

        if "edges" not in data:
            raise InvalidGraphError("Missing required field 'edges' in graph dict")
        raw_edges = data["edges"]
        if not isinstance(raw_edges, list):
            raise InvalidGraphError(f"Edges must be a list, got {type(raw_edges).__name__}")

        for e in raw_edges:
            if isinstance(e, dict):
                if "u" not in e or "v" not in e:
                    raise InvalidGraphError("Missing required endpoint fields 'u' or 'v' in edge spec")
                if "label" not in e:
                    raise InvalidGraphError("Missing required field 'label' in edge spec")
                g.add_edge(e["u"], e["v"], e["label"])
            elif isinstance(e, (list, tuple)):
                if len(e) != 3:
                    raise InvalidGraphError(f"Edge tuple must have exactly 3 elements (u, v, label), got len {len(e)}: {e!r}")
                g.add_edge(e[0], e[1], e[2])
            else:
                raise InvalidGraphError(f"Invalid edge spec: {e!r}")
        return g


# ============================================================================
# Connectivity & Topology Utilities
# ============================================================================

def is_connected_graph(V: Set[int], edges: Iterable[Tuple[int, int]]) -> bool:
    """
    Checks if the undirected graph defined by vertices V and given edges is connected.
    Returns True if and only if |V| == 0 or all vertices in V belong to 1 component.
    """
    if not V:
        return True
    if len(V) == 1:
        return True

    adj: Dict[int, List[int]] = {v: [] for v in V}
    for u, v in edges:
        if u in adj and v in adj:
            adj[u].append(v)
            adj[v].append(u)

    start = next(iter(V))
    visited: Set[int] = {start}
    queue: List[int] = [start]
    while queue:
        curr = queue.pop(0)
        for nbr in adj[curr]:
            if nbr not in visited:
                visited.add(nbr)
                queue.append(nbr)

    return len(visited) == len(V)


def is_spanning_connected(V: Set[int], edges: Iterable[Tuple[int, int]]) -> bool:
    """
    Checks the S2 condition: graph with vertices V and edges must be spanning and connected.
    Specifically:
    1. Every vertex in V has at least 1 incident edge if |V| >= 2.
    2. All vertices in V belong to a single connected component.
    """
    if len(V) < 2:
        return True

    incident_vertices: Set[int] = set()
    adj: Dict[int, List[int]] = {v: [] for v in V}
    for u, v in edges:
        if u in V and v in V:
            incident_vertices.add(u)
            incident_vertices.add(v)
            adj[u].append(v)
            adj[v].append(u)

    if incident_vertices != V:
        return False

    start = next(iter(V))
    visited: Set[int] = {start}
    queue: List[int] = [start]
    while queue:
        curr = queue.pop(0)
        for nbr in adj[curr]:
            if nbr not in visited:
                visited.add(nbr)
                queue.append(nbr)

    return len(visited) == len(V)


def compute_edge_connectivity(graph: DStarGraph, max_query_vertices: int = DEFAULT_MAX_QUERY_VERTICES) -> int:
    """
    Computes global edge connectivity lambda(Q) by brute-force cut enumeration.
    Enforces max_query_vertices cap BEFORE performing exponential cut calculation.
    Exact, standard library only, valid for small query graphs.
    """
    nodes = list(graph.vertices.keys())
    n = len(nodes)
    if n > max_query_vertices:
        raise ResourceLimitError(
            f"Query vertex count {n} exceeds max_query_vertices cap ({max_query_vertices})"
        )
    if n < 2:
        return 0

    if not is_connected_graph(set(nodes), graph.edges.keys()):
        return 0

    min_cut = len(graph.edges)
    fixed_v = nodes[0]
    other_nodes = nodes[1:]

    for r in range(0, n - 1):
        for combo in itertools.combinations(other_nodes, r):
            S = set(combo)
            S.add(fixed_v)
            if len(S) == n:
                continue
            cut_size = 0
            for (u, v) in graph.edges.keys():
                if (u in S and v not in S) or (v in S and u not in S):
                    cut_size += 1
            if cut_size < min_cut:
                min_cut = cut_size

    return min_cut


# ============================================================================
# Evaluation & Predicate Checking
# ============================================================================

@dataclass
class MappingEvaluation:
    accepted: bool
    m: int
    r_count: int
    r_edges: List[Tuple[int, int]]
    s2_connected: bool
    s1_connected: bool
    is_injective: bool
    vertex_labels_ok: bool
    reject_reasons: List[str]


def _score_mapping_internal(
    Q: DStarGraph,
    G: DStarGraph,
    mapping: Sequence[int],
    k: int,
    q_nodes: Optional[List[int]] = None,
) -> MappingEvaluation:
    """
    Internal fast mapping scorer for pre-validated inputs.
    Scores R(f), checks S2 (matched query edges) and diagnostic S1 (data induced edges).
    """
    if q_nodes is None:
        q_nodes = sorted(Q.vertices.keys())
    n = len(q_nodes)

    if len(mapping) != n:
        raise ValueError(f"Mapping length ({len(mapping)}) does not match query size ({n})")

    reject_reasons: List[str] = []

    # 1. Injectivity check
    mapped_nodes = list(mapping)
    for v in mapped_nodes:
        validate_vertex_id(v)

    is_injective = (len(set(mapped_nodes)) == n)
    if not is_injective:
        reject_reasons.append("NON_INJECTIVE")

    f: Dict[int, int] = {q_nodes[i]: mapped_nodes[i] for i in range(n)}
    img_V = set(mapped_nodes)

    # 2. Hard vertex label check
    vertex_labels_ok = True
    for q_u in q_nodes:
        g_v = f[q_u]
        if g_v not in G.vertices:
            vertex_labels_ok = False
            reject_reasons.append(f"VERTEX_NOT_IN_GRAPH:{g_v}")
        elif G.vertices[g_v] != Q.vertices[q_u]:
            vertex_labels_ok = False
            reject_reasons.append(
                f"VERTEX_LABEL_MISMATCH:q{q_u}({Q.vertices[q_u]!r})!=g{g_v}({G.vertices[g_v]!r})"
            )

    # 3. Query edge matching & missing edge set R(f)
    r_edges: List[Tuple[int, int]] = []
    matched_q_edges: List[Tuple[int, int]] = []

    for (u1, u2), q_label in Q.edges.items():
        v1, v2 = f[u1], f[u2]
        if v1 == v2:
            r_edges.append((u1, u2))
            continue
        if not G.has_edge(v1, v2):
            r_edges.append((u1, u2))
        else:
            g_label = G.get_edge_label(v1, v2)
            if g_label != q_label:
                r_edges.append((u1, u2))
            else:
                matched_q_edges.append((u1, u2))

    r_count = len(r_edges)
    m = len(Q.edges)

    # 4. S2 connectivity: Q[E_Q \ R(f)] must be spanning and connected on V_Q
    s2_connected = is_spanning_connected(set(q_nodes), matched_q_edges)
    if not s2_connected:
        reject_reasons.append("S2_DISCONNECTED")

    # 5. Diagnostic S1 connectivity: G[img_V] topological connectivity
    s1_edges: List[Tuple[int, int]] = []
    if is_injective:
        for v1 in img_V:
            for v2 in G.adj.get(v1, {}).keys():
                if v2 in img_V and v1 < v2:
                    s1_edges.append((v1, v2))
    s1_connected = is_connected_graph(img_V, s1_edges)

    accepted = True
    if not is_injective:
        accepted = False
    if not vertex_labels_ok:
        accepted = False
    if r_count > k:
        accepted = False
        reject_reasons.append(f"BUDGET_EXCEEDED:|R|={r_count}>{k}")
    if not s2_connected:
        accepted = False

    return MappingEvaluation(
        accepted=accepted,
        m=m,
        r_count=r_count,
        r_edges=r_edges,
        s2_connected=s2_connected,
        s1_connected=s1_connected,
        is_injective=is_injective,
        vertex_labels_ok=vertex_labels_ok,
        reject_reasons=reject_reasons,
    )


def evaluate_mapping(
    Q: DStarGraph,
    G: DStarGraph,
    mapping: Sequence[int],
    k: int,
) -> MappingEvaluation:
    """
    PUBLIC MVP mapping acceptance evaluation.
    ALWAYS validates Q, G, and strictly enforces 0 <= k < lambda(Q).
    Rejects out-of-domain k >= lambda(Q) with InvalidThresholdError.
    NO BYPASS ALLOWED.
    """
    if isinstance(k, bool) or not isinstance(k, int):
        raise InvalidThresholdError(f"k must be an integer, got {type(k).__name__}: {k!r}")
    if k < 0:
        raise InvalidThresholdError(f"k cannot be negative: {k}")

    Q.validate_as_query()
    G.validate_as_data()

    lam = compute_edge_connectivity(Q)
    if k >= lam:
        raise InvalidThresholdError(
            f"k={k} violates contract precondition k < lambda(Q) (lambda={lam})"
        )

    return _score_mapping_internal(Q, G, mapping, k)


@dataclass
class DiagnosticEvaluation:
    s1_connected: bool
    s2_connected: bool
    r_count: int
    r_edges: List[Tuple[int, int]]
    m: int
    lambda_q: int
    in_mvp_domain: bool
    mvp_accepted: bool
    is_injective: bool
    vertex_labels_ok: bool
    reject_reasons: List[str]


def diagnose_mapping_s1_s2(
    Q: DStarGraph,
    G: DStarGraph,
    mapping: Sequence[int],
    k: int,
) -> DiagnosticEvaluation:
    """
    DIAGNOSTIC ONLY helper for comparative research (e.g. comparing S1 vs S2).
    Evaluates topological S1 and query S2 properties even outside MVP domain (k >= lambda).
    Explicitly reports whether mapping is in MVP domain. Does NOT bypass public evaluate_mapping.
    """
    if isinstance(k, bool) or not isinstance(k, int):
        raise InvalidThresholdError(f"k must be an integer, got {type(k).__name__}: {k!r}")
    if k < 0:
        raise InvalidThresholdError(f"k cannot be negative: {k}")

    lam = compute_edge_connectivity(Q)
    in_mvp_domain = (k < lam)

    eval_res = _score_mapping_internal(Q, G, mapping, k)
    mvp_accepted = in_mvp_domain and eval_res.accepted

    reasons = list(eval_res.reject_reasons)
    if not in_mvp_domain:
        reasons.append(f"OUT_OF_DOMAIN:k={k}>=lambda={lam}")

    return DiagnosticEvaluation(
        s1_connected=eval_res.s1_connected,
        s2_connected=eval_res.s2_connected,
        r_count=eval_res.r_count,
        r_edges=eval_res.r_edges,
        m=eval_res.m,
        lambda_q=lam,
        in_mvp_domain=in_mvp_domain,
        mvp_accepted=mvp_accepted,
        is_injective=eval_res.is_injective,
        vertex_labels_ok=eval_res.vertex_labels_ok,
        reject_reasons=reasons,
    )


# ============================================================================
# Exhaustive Enumeration (M0 Oracle Engine)
# ============================================================================

def enumerate_snapshot(
    Q: DStarGraph,
    G: DStarGraph,
    k: int,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_data_vertices: int = DEFAULT_MAX_DATA_VERTICES,
    max_query_vertices: int = DEFAULT_MAX_QUERY_VERTICES,
) -> List[Tuple[int, ...]]:
    """
    Exhaustively enumerates all valid ordered tuples f: V_Q -> V_G.
    Validates cheap schema/caps/sizes BEFORE computing edge connectivity.
    Fails closed with ResourceLimitError if candidate upper bound exceeds safety caps.
    Returns list of accepted tuples ordered by canonical sorted(V_Q).
    """
    # 1. Validate resource caps
    validate_resource_caps(max_candidates, max_data_vertices, max_query_vertices)

    # 2. Validate k
    if isinstance(k, bool) or not isinstance(k, int):
        raise InvalidThresholdError(f"k must be an integer, got {type(k).__name__}: {k!r}")
    if k < 0:
        raise InvalidThresholdError(f"k cannot be negative: {k}")

    # 3. Cheap query size check BEFORE computing cuts
    n_q = len(Q.vertices)
    if n_q > max_query_vertices:
        raise ResourceLimitError(
            f"Query size {n_q} exceeds max allowed ({max_query_vertices})"
        )

    # 4. Cheap data graph size check
    n_g = len(G.vertices)
    if n_g > max_data_vertices:
        raise ResourceLimitError(
            f"Data graph size {n_g} exceeds max allowed ({max_data_vertices})"
        )

    # 5. Candidate injection bound check
    if n_g < n_q:
        return []

    perm_count = math.perm(n_g, n_q)
    if perm_count > max_candidates:
        raise ResourceLimitError(
            f"Candidate injection bound {perm_count} exceeds limit {max_candidates}"
        )

    # 6. Basic topology validation
    Q.validate_as_query()
    G.validate_as_data()

    # 7. Compute lambda and enforce k < lambda(Q)
    lam = compute_edge_connectivity(Q, max_query_vertices=max_query_vertices)
    if k >= lam:
        raise InvalidThresholdError(
            f"k={k} violates contract precondition k < lambda(Q) (lambda={lam})"
        )

    q_nodes = sorted(Q.vertices.keys())
    data_nodes = sorted(G.vertices.keys())

    results: List[Tuple[int, ...]] = []

    # Exhaustive label-compatible injective search
    for perm in itertools.permutations(data_nodes, n_q):
        # Quick check for label compatibility
        label_match = True
        for i in range(n_q):
            if G.vertices[perm[i]] != Q.vertices[q_nodes[i]]:
                label_match = False
                break
        if not label_match:
            continue

        eval_res = _score_mapping_internal(Q, G, perm, k, q_nodes)
        if eval_res.accepted:
            results.append(perm)

    return results


# ============================================================================
# Dynamic Updates & Incremental Oracle
# ============================================================================

@dataclass(frozen=True)
class DStarUpdate:
    op: str  # "INSERT" or "DELETE"
    u: int
    v: int
    label: Any

    def __post_init__(self) -> None:
        if self.op not in ("INSERT", "DELETE"):
            raise UnsupportedOperationError(f"Unsupported update op: {self.op!r}")
        validate_vertex_id(self.u)
        validate_vertex_id(self.v)
        if self.u == self.v:
            raise SelfLoopError(f"Self-loops are forbidden in updates: ({self.u}, {self.v})")
        validate_label(self.label, "Update edge label")


def apply_update(G: DStarGraph, update: DStarUpdate) -> None:
    """
    Applies an update to graph G in-place with strict atomic validation.
    If the update is invalid, raises typed error and leaves G completely unchanged.
    """
    if update.u not in G.vertices:
        raise UnknownVertexError(f"Endpoint vertex {update.u} not in data graph")
    if update.v not in G.vertices:
        raise UnknownVertexError(f"Endpoint vertex {update.v} not in data graph")

    if update.op == "INSERT":
        G.add_edge(update.u, update.v, update.label)
    elif update.op == "DELETE":
        G.remove_edge(update.u, update.v, update.label)
    else:
        raise UnsupportedOperationError(f"Unknown operation {update.op}")


@dataclass
class DynamicDelta:
    step: int
    op: str
    edge: Tuple[int, int]
    label: Any
    entered: List[Tuple[int, ...]]
    exited: List[Tuple[int, ...]]


class DStarDynamicOracle:
    """
    Stateful incremental oracle for D*.
    Maintains isolated copies of Q and G_t, along with snapshot M_t.
    On valid update:
      M_next = enumerate_snapshot(Q, G_next, k)
      ENTERED = M_next - M_curr
      EXITED = M_curr - M_next
    On invalid update:
      Fails closed, state untouched.
    """

    def __init__(
        self,
        Q: DStarGraph,
        G: DStarGraph,
        k: int,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        max_data_vertices: int = DEFAULT_MAX_DATA_VERTICES,
        max_query_vertices: int = DEFAULT_MAX_QUERY_VERTICES,
    ) -> None:
        validate_resource_caps(max_candidates, max_data_vertices, max_query_vertices)
        # R5 / N01: Deep-clone and isolate query and data graphs
        self.Q = Q.clone()
        self.G = G.clone()
        self.k = k
        self.max_candidates = max_candidates
        self.max_data_vertices = max_data_vertices
        self.max_query_vertices = max_query_vertices
        self.step = 0

        # Initial snapshot
        initial_matches = enumerate_snapshot(
            self.Q,
            self.G,
            self.k,
            max_candidates=self.max_candidates,
            max_data_vertices=self.max_data_vertices,
            max_query_vertices=self.max_query_vertices,
        )
        self.current_matches: Set[Tuple[int, ...]] = set(initial_matches)

    def apply_update(self, update: DStarUpdate) -> DynamicDelta:
        """
        Applies update atomically, computes ENTERED/EXITED, updates state.
        Leaves state intact if an error occurs.
        """
        # Validate update on a clone first for strict atomicity
        test_g = self.G.clone()
        apply_update(test_g, update)

        # Enumerate snapshot on new graph
        next_matches_list = enumerate_snapshot(
            self.Q,
            test_g,
            self.k,
            max_candidates=self.max_candidates,
            max_data_vertices=self.max_data_vertices,
            max_query_vertices=self.max_query_vertices,
        )
        next_matches = set(next_matches_list)

        entered = sorted(list(next_matches - self.current_matches))
        exited = sorted(list(self.current_matches - next_matches))

        # Commit state
        self.G = test_g
        self.current_matches = next_matches
        self.step += 1

        return DynamicDelta(
            step=self.step,
            op=update.op,
            edge=(min(update.u, update.v), max(update.u, update.v)),
            label=update.label,
            entered=entered,
            exited=exited,
        )


# ============================================================================
# Boundary Characterization Diagnostics
# ============================================================================

def check_anchored_boundary_property(
    Q: DStarGraph,
    G_before: DStarGraph,
    G_after: DStarGraph,
    update: DStarUpdate,
    k: int,
    true_entered: Set[Tuple[int, ...]],
    true_exited: Set[Tuple[int, ...]],
) -> Dict[str, Any]:
    """
    Checks the analytical boundary property:
    - INSERT: evaluated on G_after, anchored mappings enter iff |R_after(f)| == k.
    - DELETE: evaluated on G_before, anchored mappings exit iff |R_before(f)| == k.
    """
    q_nodes = sorted(Q.vertices.keys())
    u_up, v_up = update.u, update.v
    up_lbl = update.label

    if update.op == "INSERT":
        active_G = G_after
        target_event_set = true_entered
    else:
        active_G = G_before
        target_event_set = true_exited

    all_matches = enumerate_snapshot(Q, active_G, k)
    anchored_at_boundary: Set[Tuple[int, ...]] = set()

    for tup in all_matches:
        f = {q_nodes[i]: tup[i] for i in range(len(q_nodes))}
        is_anchored = False
        for (q_u, q_v), q_lbl in Q.edges.items():
            if q_lbl == up_lbl:
                if (f[q_u] == u_up and f[q_v] == v_up) or (f[q_u] == v_up and f[q_v] == u_up):
                    is_anchored = True
                    break
        if is_anchored:
            eval_res = _score_mapping_internal(Q, active_G, tup, k, q_nodes)
            if eval_res.r_count == k:
                anchored_at_boundary.add(tup)

    anchored_in_target = {t for t in target_event_set if t in anchored_at_boundary}
    return {
        "op": update.op,
        "k": k,
        "anchored_at_boundary_count": len(anchored_at_boundary),
        "target_event_count": len(target_event_set),
        "property_holds": (anchored_at_boundary == target_event_set),
    }


# ============================================================================
# CLI Implementation with Structured Error Handling
# ============================================================================

def emit_cli_error(status: str, exc: Exception, exit_code: int) -> None:
    """Emits deterministic structured error JSON to stderr and exits with given code."""
    out = {
        "semantics_version": "dstar-v1.0",
        "status": status,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
    json.dump(out, sys.stderr, indent=2)
    sys.stderr.write("\n")
    sys.exit(exit_code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent D* CSM Oracle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Snapshot subcommand
    snap_p = subparsers.add_parser("snapshot", help="Run snapshot enumeration")
    snap_p.add_argument("--query", required=True, help="Path to query JSON")
    snap_p.add_argument("--graph", required=True, help="Path to data graph JSON")
    snap_p.add_argument("--k", type=int, required=True, help="Missing edge budget k")
    snap_p.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="Max candidates guard")
    snap_p.add_argument("--max-data-vertices", type=int, default=DEFAULT_MAX_DATA_VERTICES, help="Max data vertices guard")
    snap_p.add_argument("--max-query-vertices", type=int, default=DEFAULT_MAX_QUERY_VERTICES, help="Max query vertices guard")

    # Stream subcommand
    stream_p = subparsers.add_parser("stream", help="Run dynamic stream updates")
    stream_p.add_argument("--query", required=True, help="Path to query JSON")
    stream_p.add_argument("--graph", required=True, help="Path to data graph JSON")
    stream_p.add_argument("--stream", required=True, help="Path to stream JSON")
    stream_p.add_argument("--k", type=int, required=True, help="Missing edge budget k")
    stream_p.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="Max candidates guard")
    stream_p.add_argument("--max-data-vertices", type=int, default=DEFAULT_MAX_DATA_VERTICES, help="Max data vertices guard")
    stream_p.add_argument("--max-query-vertices", type=int, default=DEFAULT_MAX_QUERY_VERTICES, help="Max query vertices guard")

    args = parser.parse_args()

    try:
        # 1. Load files with structured error trapping
        try:
            with open(args.query, "r", encoding="utf-8") as f:
                q_data = json.load(f)
        except Exception as e:
            emit_cli_error("ERROR", e, 1)

        try:
            with open(args.graph, "r", encoding="utf-8") as f:
                g_data = json.load(f)
        except Exception as e:
            emit_cli_error("ERROR", e, 1)

        # 2. Parse graphs
        try:
            Q = DStarGraph.from_dict(q_data)
            G = DStarGraph.from_dict(g_data)
        except Exception as e:
            emit_cli_error("ERROR", e, 1)

        if args.command == "snapshot":
            matches = enumerate_snapshot(
                Q,
                G,
                args.k,
                max_candidates=args.max_candidates,
                max_data_vertices=args.max_data_vertices,
                max_query_vertices=args.max_query_vertices,
            )
            lam = compute_edge_connectivity(Q, max_query_vertices=args.max_query_vertices)
            out = {
                "semantics_version": "dstar-v1.0",
                "status": "SUCCESS",
                "query_vertex_order": sorted(Q.vertices.keys()),
                "k": args.k,
                "m": len(Q.edges),
                "lambda": lam,
                "snapshot": [list(t) for t in sorted(matches)],
            }
            json.dump(out, sys.stdout, indent=2)
            sys.stdout.write("\n")

        elif args.command == "stream":
            try:
                with open(args.stream, "r", encoding="utf-8") as f:
                    stream_data = json.load(f)
            except Exception as e:
                emit_cli_error("ERROR", e, 1)

            if not isinstance(stream_data, list):
                emit_cli_error("ERROR", InvalidGraphError("Stream must be a JSON array of update objects"), 1)

            oracle = DStarDynamicOracle(
                Q,
                G,
                args.k,
                max_candidates=args.max_candidates,
                max_data_vertices=args.max_data_vertices,
                max_query_vertices=args.max_query_vertices,
            )
            lam = compute_edge_connectivity(oracle.Q, max_query_vertices=args.max_query_vertices)
            deltas_out = []
            for idx, item in enumerate(stream_data):
                if not isinstance(item, dict):
                    emit_cli_error("ERROR", InvalidGraphError(f"Stream item at index {idx} must be a dict: {item!r}"), 1)
                for req_f in ("op", "u", "v", "label"):
                    if req_f not in item:
                        emit_cli_error("ERROR", InvalidGraphError(f"Missing required field {req_f!r} in stream item {idx}"), 1)

                try:
                    up = DStarUpdate(
                        op=item["op"],
                        u=item["u"],
                        v=item["v"],
                        label=item["label"],
                    )
                    delta = oracle.apply_update(up)
                except Exception as e:
                    emit_cli_error("ERROR", e, 1)

                deltas_out.append({
                    "step": delta.step,
                    "op": delta.op,
                    "edge": list(delta.edge),
                    "label": delta.label,
                    "entered": [list(t) for t in delta.entered],
                    "exited": [list(t) for t in delta.exited],
                })
            out = {
                "semantics_version": "dstar-v1.0",
                "status": "SUCCESS",
                "query_vertex_order": sorted(oracle.Q.vertices.keys()),
                "k": args.k,
                "m": len(oracle.Q.edges),
                "lambda": lam,
                "updates": deltas_out,
            }
            json.dump(out, sys.stdout, indent=2)
            sys.stdout.write("\n")

    except ResourceLimitError as e:
        emit_cli_error("RESOURCE_LIMIT", e, 2)
    except Exception as e:
        emit_cli_error("ERROR", e, 1)


if __name__ == "__main__":
    main()
