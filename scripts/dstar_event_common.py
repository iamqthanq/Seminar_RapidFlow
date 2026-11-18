#!/usr/bin/env python3
"""
scripts/dstar_event_common.py — Shared Primitives for D* Event Enumeration Engines.

Implements shared data structures, validations, update transactions, counters,
run-level contract verification, and duplicate-checking event sinks for M1-enum
and M2-Event-k engines.
Zero dependencies outside the Python 3 standard library.
Strictly isolated from legacy or oracle modules.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

# Default resource safety caps
DEFAULT_MAX_DATA_VERTICES = 8
DEFAULT_MAX_QUERY_VERTICES = 6
DEFAULT_MAX_CANDIDATES = 100000
DEFAULT_MAX_WORK = 500000
DEFAULT_MAX_STREAM_STEPS = 100


# ============================================================================
# Exceptions (Strict, Atomic & Fail-Closed)
# ============================================================================

class DStarEventError(Exception):
    """Base exception for all D* event engine errors."""
    pass


class UnknownVertexError(DStarEventError):
    """Raised when an update or mapping references a vertex not in V_0."""
    pass


class DuplicateEdgeInsertionError(DStarEventError):
    """Raised when attempting to insert an edge that already exists."""
    pass


class NonexistentEdgeDeletionError(DStarEventError):
    """Raised when attempting to delete an edge that does not exist."""
    pass


class EdgeLabelMismatchDeletionError(DStarEventError):
    """Raised when deleting an edge whose existing label does not match."""
    pass


class SelfLoopError(DStarEventError):
    """Raised when an edge update or graph definition contains a self-loop."""
    pass


class InvalidGraphError(DStarEventError):
    """Raised when query or data graph violates topological or labeling preconditions."""
    pass


class InvalidUpdateError(DStarEventError):
    """Raised when an update specification is structurally or semantically invalid."""
    pass


class InvalidThresholdError(DStarEventError):
    """Raised when threshold k violates the contract 0 <= k < lambda(Q)."""
    pass


class ResourceLimitError(DStarEventError):
    """Raised when candidate space, graph size, stream length, or work counters exceed safety bounds."""
    pass


class InvariantViolationError(DStarEventError):
    """Raised when an internal algorithm invariant is violated (e.g. empty frontier when k < lambda)."""
    pass


class UnsupportedOperationError(DStarEventError):
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
    max_work: int = DEFAULT_MAX_WORK,
    max_stream_steps: int = DEFAULT_MAX_STREAM_STEPS,
) -> None:
    """Validates that resource safety caps are positive finite integers."""
    for name, val in [
        ("max_candidates", max_candidates),
        ("max_data_vertices", max_data_vertices),
        ("max_query_vertices", max_query_vertices),
        ("max_work", max_work),
        ("max_stream_steps", max_stream_steps),
    ]:
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ResourceLimitError(f"{name} must be a positive integer, got {type(val).__name__}: {val!r}")


# ============================================================================
# Graph Data Structure
# ============================================================================

class DStarEventGraph:
    """
    Undirected, simple, labeled graph representation.
    Vertices have immutable IDs (int) and labels (int or str).
    Edges are stored canonically as (min(u, v), max(u, v)) -> label.
    Adj maps vertex -> {neighbor -> edge_label} for O(1) neighbor/edge lookups.
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
        if edge_key not in self.edges:
            raise NonexistentEdgeDeletionError(f"Edge {edge_key} does not exist in graph")

        existing_label = self.edges[edge_key]
        if existing_label != lbl:
            raise EdgeLabelMismatchDeletionError(
                f"Edge {edge_key} label mismatch on deletion: expected {existing_label!r}, got {lbl!r}"
            )

        del self.edges[edge_key]
        del self.adj[u_id][v_id]
        del self.adj[v_id][u_id]

    def has_edge(self, u: int, v: int) -> bool:
        if u not in self.adj:
            return False
        return v in self.adj[u]

    def get_edge_label(self, u: int, v: int) -> Optional[Any]:
        if u not in self.adj:
            return None
        return self.adj[u].get(v)

    def get_neighbors(self, u: int) -> List[Tuple[int, Any]]:
        """Returns sorted list of (neighbor_id, edge_label) for deterministic exploration."""
        if u not in self.adj:
            return []
        return sorted(self.adj[u].items(), key=lambda item: item[0])

    def clone(self) -> DStarEventGraph:
        """Deep clone of graph structure with immutable elements."""
        g = DStarEventGraph()
        g.vertices = dict(self.vertices)
        g.edges = dict(self.edges)
        g.adj = {u: dict(nbrs) for u, nbrs in self.adj.items()}
        return g

    def to_dict(self) -> Dict[str, Any]:
        """Serializes graph to dict with string vertex keys for vertices and edge list."""
        sorted_v_keys = sorted(self.vertices.keys())
        sorted_edges = [
            [u, v, self.edges[(u, v)]]
            for (u, v) in sorted(self.edges.keys())
        ]
        return {
            "vertices": {str(k): self.vertices[k] for k in sorted_v_keys},
            "edges": sorted_edges,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DStarEventGraph:
        """Parses a graph from dictionary format."""
        if not isinstance(data, dict):
            raise InvalidGraphError(f"Graph data must be a dict, got {type(data).__name__}")
        if "vertices" not in data or "edges" not in data:
            raise InvalidGraphError("Graph data missing 'vertices' or 'edges' key")

        g = cls()
        v_data = data["vertices"]
        if isinstance(v_data, dict):
            for k, lbl in v_data.items():
                v_id = parse_vertex_id_string(k)
                g.add_vertex(v_id, lbl)
        elif isinstance(v_data, list):
            for item in v_data:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    v_id = validate_vertex_id(item[0])
                    g.add_vertex(v_id, item[1])
                elif isinstance(item, dict) and "id" in item and "label" in item:
                    v_id = validate_vertex_id(item["id"])
                    g.add_vertex(v_id, item["label"])
                else:
                    raise InvalidGraphError(f"Invalid vertex item format: {item!r}")
        else:
            raise InvalidGraphError(f"Vertices must be dict or list, got {type(v_data).__name__}")

        e_data = data["edges"]
        if not isinstance(e_data, list):
            raise InvalidGraphError(f"Edges must be a list, got {type(e_data).__name__}")
        for item in e_data:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                u, v, lbl = item
                g.add_edge(u, v, lbl)
            elif isinstance(item, dict) and "u" in item and "v" in item and "label" in item:
                g.add_edge(item["u"], item["v"], item["label"])
            else:
                raise InvalidGraphError(f"Invalid edge item format: {item!r}")
        return g

    @classmethod
    def from_file(cls, path: str) -> DStarEventGraph:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Graph file does not exist: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ============================================================================
# Graph Connectivity & Topological Computations
# ============================================================================

def is_connected(graph: DStarEventGraph) -> bool:
    """Checks if graph is connected using BFS."""
    if not graph.vertices:
        return True
    start = next(iter(graph.vertices))
    visited: Set[int] = set()
    queue = [start]
    visited.add(start)
    while queue:
        curr = queue.pop(0)
        for nbr in graph.adj.get(curr, {}):
            if nbr not in visited:
                visited.add(nbr)
                queue.append(nbr)
    return len(visited) == len(graph.vertices)


def compute_edge_connectivity(graph: DStarEventGraph) -> int:
    """
    Computes global edge-connectivity lambda(Q) by brute-force cut analysis.
    For small query graphs (|V_Q| <= 6), enumerates all nonempty proper subsets S of V_Q
    and finds the minimum number of crossing edges.
    """
    n = len(graph.vertices)
    if n <= 1:
        return 0
    if not is_connected(graph):
        return 0

    v_list = list(graph.vertices.keys())
    m = len(graph.edges)
    min_cut = m

    # Subsets of size 1 to n // 2
    for r in range(1, (n // 2) + 1):
        for cut_s in itertools.combinations(v_list, r):
            cut_set = set(cut_s)
            crossing = 0
            for (u, v) in graph.edges:
                if (u in cut_set) != (v in cut_set):
                    crossing += 1
            if crossing < min_cut:
                min_cut = crossing

    return min_cut


# ============================================================================
# Run-Level Contract Validation (F02.2, F03.1, F03.3)
# ============================================================================

def validate_run_contract(
    query: DStarEventGraph,
    graph: DStarEventGraph,
    k: Any,
    limits: Optional[Dict[str, Any]] = None,
    stream_length: Optional[int] = None,
) -> int:
    """
    Validates the complete run-level contract for D* event engines and CLI.
    Enforces cheap size and resource guards BEFORE any combinatorial min-cut computation.
    Returns lambda(Q).
    """
    limits = limits or {}
    max_candidates = limits.get("max_candidates", DEFAULT_MAX_CANDIDATES)
    max_data_vertices = limits.get("max_data_vertices", DEFAULT_MAX_DATA_VERTICES)
    max_query_vertices = limits.get("max_query_vertices", DEFAULT_MAX_QUERY_VERTICES)
    max_work = limits.get("max_work", DEFAULT_MAX_WORK)
    max_stream_steps = limits.get("max_stream_steps", DEFAULT_MAX_STREAM_STEPS)

    validate_resource_caps(
        max_candidates,
        max_data_vertices,
        max_query_vertices,
        max_work,
        max_stream_steps,
    )

    # 1. Cheap Query Size Cap check FIRST before any cut computation (F03.1)
    n_q = len(query.vertices)
    if n_q == 0:
        raise InvalidGraphError("Query graph has no vertices")
    if n_q > max_query_vertices:
        raise ResourceLimitError(f"Query vertex count {n_q} exceeds cap {max_query_vertices}")

    # 2. Cheap Data Size Cap check
    n_g = len(graph.vertices)
    if n_g > max_data_vertices:
        raise ResourceLimitError(f"Data graph vertex count {n_g} exceeds cap {max_data_vertices}")

    # 3. Stream length cap check (F03.3)
    if stream_length is not None:
        if isinstance(stream_length, bool) or not isinstance(stream_length, int) or stream_length < 0:
            raise InvalidUpdateError(f"Stream length must be a non-negative integer, got {stream_length!r}")
        if stream_length > max_stream_steps:
            raise ResourceLimitError(
                f"Stream length {stream_length} exceeds max_stream_steps cap {max_stream_steps}"
            )

    # 4. Query topology validation
    if not is_connected(query):
        raise InvalidGraphError("Query graph must be connected")

    # 5. Threshold k validation (integer, non-negative)
    if isinstance(k, bool) or not isinstance(k, int) or k < 0:
        raise InvalidThresholdError(f"Threshold k must be a non-negative integer, got {k!r}")

    # 6. Candidate space cap check
    if n_g >= n_q:
        perm_count = math.perm(n_g, n_q)
        if perm_count > max_candidates:
            raise ResourceLimitError(
                f"Candidate space P({n_g}, {n_q})={perm_count} exceeds limit {max_candidates}"
            )

    # 7. Compute edge connectivity lambda(Q) (only reached after cheap guards pass)
    lambda_q = compute_edge_connectivity(query)
    if k >= lambda_q:
        raise InvalidThresholdError(
            f"Threshold k={k} violates domain condition k < lambda(Q)={lambda_q}"
        )

    return lambda_q


# ============================================================================
# Update Data Structure & Transaction Support
# ============================================================================

@dataclass
class DStarEventUpdate:
    """
    Discrete edge update representation for stream processing.
    operation: 'INSERT' or 'DELETE'
    edge: canonical (min(u, v), max(u, v))
    label: edge label (int or str) - MANDATORY (F02.1)
    """
    operation: str
    edge: Tuple[int, int]
    label: Any

    def __post_init__(self) -> None:
        op = str(self.operation).upper()
        if op in ("INS", "INSERT"):
            self.operation = "INSERT"
        elif op in ("DEL", "DELETE"):
            self.operation = "DELETE"
        else:
            raise InvalidUpdateError(f"Unsupported operation: {self.operation!r}")

        if not isinstance(self.edge, (list, tuple)) or len(self.edge) != 2:
            raise InvalidUpdateError(f"Edge must be a 2-tuple, got {self.edge!r}")

        u = validate_vertex_id(self.edge[0])
        v = validate_vertex_id(self.edge[1])
        if u == v:
            raise SelfLoopError(f"Self-loop update is forbidden: ({u}, {v})")
        self.edge = (min(u, v), max(u, v))
        self.label = validate_label(self.label, "Update edge label")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "edge": [self.edge[0], self.edge[1]],
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DStarEventUpdate:
        if not isinstance(data, dict):
            raise InvalidUpdateError(f"Update must be a dict, got {type(data).__name__}")
        op = data.get("operation", data.get("kind", data.get("op")))
        if op is None:
            raise InvalidUpdateError(f"Missing operation field in update: {data!r}")
        edge = data.get("edge")
        if edge is None:
            raise InvalidUpdateError(f"Missing edge field in update: {data!r}")
        # F02.1: Label is mandatory. Reject if missing or None!
        if "label" not in data or data["label"] is None:
            raise InvalidUpdateError(f"Missing required 'label' field in update: {data!r}")
        label = data["label"]
        return cls(operation=op, edge=tuple(edge), label=label)

    def apply(self, graph: DStarEventGraph) -> None:
        """Applies update in-place to graph with strict validation."""
        u, v = self.edge
        if self.operation == "INSERT":
            graph.add_edge(u, v, self.label)
        elif self.operation == "DELETE":
            graph.remove_edge(u, v, self.label)

    def apply_transactional(self, graph: DStarEventGraph) -> Tuple[DStarEventGraph, DStarEventGraph]:
        """
        Clones graph and produces (G_before, G_after) transactionally.
        Validates preconditions without mutating caller's original graph if invalid.
        """
        u, v = self.edge
        # Precondition check before any modification
        if u not in graph.vertices:
            raise UnknownVertexError(f"Endpoint vertex {u} not in graph")
        if v not in graph.vertices:
            raise UnknownVertexError(f"Endpoint vertex {v} not in graph")

        edge_key = (min(u, v), max(u, v))
        if self.operation == "INSERT":
            if edge_key in graph.edges:
                raise DuplicateEdgeInsertionError(f"Edge {edge_key} already exists in graph")
            g_before = graph.clone()
            g_after = graph.clone()
            g_after.add_edge(u, v, self.label)
            return g_before, g_after
        elif self.operation == "DELETE":
            if edge_key not in graph.edges:
                raise NonexistentEdgeDeletionError(f"Edge {edge_key} does not exist in graph")
            if graph.edges[edge_key] != self.label:
                raise EdgeLabelMismatchDeletionError(
                    f"Edge {edge_key} label mismatch on deletion: expected {graph.edges[edge_key]!r}, got {self.label!r}"
                )
            g_before = graph.clone()
            g_after = graph.clone()
            g_after.remove_edge(u, v, self.label)
            return g_before, g_after
        else:
            raise InvalidUpdateError(f"Unknown operation: {self.operation}")


# ============================================================================
# Work Counters (F03.2)
# ============================================================================

@dataclass
class EventCounters:
    """
    Instrumentation counters tracking search effort and emissions.
    Distinguishes attempted candidate expansions from accepted extensions (F03.2).
    """
    candidate_evaluations: int = 0  # Attempted candidate evaluations (work units)
    candidate_extensions: int = 0   # Accepted candidate extensions
    anchors_started: int = 0
    edge_lookups: int = 0
    vertex_label_checks: int = 0
    emitted_tuples: int = 0
    # M1 specific
    variants_started: int = 0
    raw_exact_results: int = 0
    # M2 specific
    edge_decisions: int = 0
    missing_branches: int = 0
    commitment_checks: int = 0

    def check_work_limit(self, max_work: int) -> None:
        """
        Enforces execution work limit and fails closed.
        Total budget work units = attempted candidate evaluations + edge decisions + variants started.
        """
        total_work = self.candidate_evaluations + self.edge_decisions + self.variants_started
        if total_work > max_work:
            raise ResourceLimitError(
                f"Work limit exceeded: budget work units ({total_work}) > max_work ({max_work})"
            )

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


# ============================================================================
# Output Sink & Result Dataclass
# ============================================================================

@dataclass
class EventResult:
    """Encapsulates the event enumeration result for a single update step."""
    operation: str
    edge: Tuple[int, int]
    label: Any
    entered: List[Tuple[int, ...]]
    exited: List[Tuple[int, ...]]
    counters: EventCounters
    status: str = "SUCCESS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "edge": [self.edge[0], self.edge[1]],
            "label": self.label,
            "entered": [list(t) for t in self.entered],
            "exited": [list(t) for t in self.exited],
            "counters": self.counters.to_dict(),
            "status": self.status,
        }


class EventSink:
    """
    Output sink for event tuples.
    Collects raw emissions and strictly verifies that raw emissions contain
    ZERO duplicate tuples upon finalization.
    """

    def __init__(self) -> None:
        self.raw_emissions: List[Tuple[int, ...]] = []

    def emit(self, mapping_tuple: Tuple[int, ...]) -> None:
        self.raw_emissions.append(mapping_tuple)

    def finalize(self) -> List[Tuple[int, ...]]:
        """
        Finalizes results, verifying uniqueness assertion.
        Returns canonically sorted list of distinct tuples.
        """
        raw_len = len(self.raw_emissions)
        unique_len = len(set(self.raw_emissions))
        if raw_len != unique_len:
            raise InvariantViolationError(
                f"DUPLICATE EMISSION DETECTED: {raw_len} raw emissions vs {unique_len} unique tuples"
            )
        return sorted(self.raw_emissions)


# ============================================================================
# Canonical Ordered Tuple Conversion
# ============================================================================

def canonical_ordered_tuple(
    f: Dict[int, int] | Sequence[Optional[int]],
    query: DStarEventGraph,
) -> Tuple[int, ...]:
    """
    Constructs canonical ordered tuple T(f) according to ascending sorted order of
    original query vertex IDs: (f[u_1], f[u_2], ..., f[u_n]) where u_1 < u_2 < ... < u_n.
    Supports either dict mapping {query_vertex_id: data_vertex_id} or sequence mapping.
    """
    sorted_q_vertices = sorted(query.vertices.keys())
    if isinstance(f, dict):
        return tuple(f[u] for u in sorted_q_vertices)
    elif isinstance(f, (list, tuple)):
        return tuple(f[i] for i in range(len(sorted_q_vertices)))
    else:
        raise InvalidGraphError(f"Unsupported mapping type: {type(f).__name__}")
