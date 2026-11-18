#!/usr/bin/env python3
"""
scripts/dstar_m2_event.py — Bare M2-Event-k Event Enumeration Engine.

Implements the D* M2-Event-k algorithm: direct depth-first search on Q
around the updated edge in the relevant snapshot, branching on PRESENT vs MISSING
edges, deferring unmapped vertices, and enforcing the |R| == k event boundary lemma.

Pure Python 3 standard library.
Zero imports from scripts/dstar_oracle.py or legacy code.
"""

from __future__ import annotations

import copy
import itertools
import math
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from dstar_event_common import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DEFAULT_MAX_WORK,
    DStarEventError,
    DStarEventGraph,
    DStarEventUpdate,
    EventCounters,
    EventResult,
    EventSink,
    InvalidGraphError,
    InvalidThresholdError,
    InvariantViolationError,
    ResourceLimitError,
    UnknownVertexError,
    canonical_ordered_tuple,
    compute_edge_connectivity,
    is_connected,
    validate_resource_caps,
)


class M2EventEngine:
    """
    Bare M2-Event-k event enumeration engine.
    Performs direct incremental search on snapshot around update edge.
    """

    def __init__(self, limits: Optional[Dict[str, Any]] = None) -> None:
        limits = limits or {}
        self.max_candidates = limits.get("max_candidates", DEFAULT_MAX_CANDIDATES)
        self.max_data_vertices = limits.get("max_data_vertices", DEFAULT_MAX_DATA_VERTICES)
        self.max_query_vertices = limits.get("max_query_vertices", DEFAULT_MAX_QUERY_VERTICES)
        self.max_work = limits.get("max_work", DEFAULT_MAX_WORK)
        validate_resource_caps(
            self.max_candidates,
            self.max_data_vertices,
            self.max_query_vertices,
            self.max_work,
        )

    def find_events(
        self,
        query: DStarEventGraph,
        snapshot: DStarEventGraph,
        update: DStarEventUpdate,
        k: int,
        limits: Optional[Dict[str, Any]] = None,
        _skip_domain_check: bool = False,
    ) -> EventResult:
        """
        Executes anchored bare M2-Event-k search for a single update.
        snapshot must be G_after for INSERT and G_before for DELETE.
        In both cases, the anchor edge update.edge must exist in snapshot with update.label.
        """
        # Merge limits if provided
        max_work = self.max_work
        max_candidates = self.max_candidates
        max_data_vertices = self.max_data_vertices
        max_query_vertices = self.max_query_vertices
        if limits:
            max_work = limits.get("max_work", max_work)
            max_candidates = limits.get("max_candidates", max_candidates)
            max_data_vertices = limits.get("max_data_vertices", max_data_vertices)
            max_query_vertices = limits.get("max_query_vertices", max_query_vertices)
            validate_resource_caps(max_candidates, max_data_vertices, max_query_vertices, max_work)

        # 1. Validate query graph
        n_q = len(query.vertices)
        if n_q == 0:
            raise InvalidGraphError("Query graph has no vertices")
        if not is_connected(query):
            raise InvalidGraphError("Query graph must be connected")

        # 2. Validate domain 0 <= k < lambda(Q)
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise InvalidThresholdError(f"Threshold k must be a non-negative integer, got {k!r}")

        lambda_q = compute_edge_connectivity(query)
        if not _skip_domain_check and k >= lambda_q:
            raise InvalidThresholdError(
                f"Threshold k={k} violates domain condition k < lambda(Q)={lambda_q}"
            )

        # 3. Validate snapshot and resource bounds
        n_g = len(snapshot.vertices)
        if n_q > max_query_vertices:
            raise ResourceLimitError(
                f"Query vertex count {n_q} exceeds cap {max_query_vertices}"
            )
        if n_g > max_data_vertices:
            raise ResourceLimitError(
                f"Data graph vertex count {n_g} exceeds cap {max_data_vertices}"
            )

        # Candidate permutation space upper bound
        if n_g >= n_q:
            perm_count = math.perm(n_g, n_q)
            if perm_count > max_candidates:
                raise ResourceLimitError(
                    f"Candidate space P({n_g}, {n_q})={perm_count} exceeds limit {max_candidates}"
                )
        else:
            # Not enough data vertices to map query
            counters = EventCounters()
            return EventResult(
                operation=update.operation,
                edge=update.edge,
                label=update.label,
                entered=[],
                exited=[],
                counters=counters,
                status="SUCCESS",
            )

        # 4. Verify anchor edge exists in snapshot
        u_upd, v_upd = update.edge
        if not snapshot.has_edge(u_upd, v_upd):
            raise UnknownVertexError(
                f"Snapshot missing update edge ({u_upd}, {v_upd})"
            )
        if snapshot.get_edge_label(u_upd, v_upd) != update.label:
            raise InvalidGraphError(
                f"Snapshot edge ({u_upd}, {v_upd}) label mismatch: "
                f"expected {update.label!r}, got {snapshot.get_edge_label(u_upd, v_upd)!r}"
            )

        # 5. Prepare indexing for query elements
        sorted_q_vertices = sorted(query.vertices.keys())
        v_to_idx = {v: i for i, v in enumerate(sorted_q_vertices)}
        q_edges = sorted(query.edges.keys())
        QE: List[Tuple[int, int]] = [(v_to_idx[e[0]], v_to_idx[e[1]]) for e in q_edges]
        qel = [query.edges[e] for e in q_edges]
        qvl = [query.vertices[v] for v in sorted_q_vertices]
        m = len(QE)

        counters = EventCounters()
        sink = EventSink()

        # Precompute query incident edge indices for each query vertex index
        incident_edges: List[List[int]] = [[] for _ in range(n_q)]
        for i, (a, b) in enumerate(QE):
            incident_edges[a].append(i)
            incident_edges[b].append(i)

        # Recursive M2 DFS
        def dfs(
            f: List[Optional[int]],
            status: List[Optional[str]],
            Rp: FrozenSet[int],
        ) -> None:
            counters.check_work_limit(max_work)

            # Pruning if missing count exceeds k
            if len(Rp) > k:
                return

            # Leaf condition: all query vertices mapped
            if all(v is not None for v in f):
                # Verify commitments on all query edges
                for j, (a, b) in enumerate(QE):
                    counters.edge_lookups += 1
                    da, db = f[a], f[b]
                    assert da is not None and db is not None
                    present = snapshot.has_edge(da, db) and snapshot.get_edge_label(da, db) == qel[j]
                    if status[j] == "MISSING":
                        counters.commitment_checks += 1
                        if present:
                            return
                    elif status[j] == "PRESENT":
                        if not present:
                            return

                # Strict event boundary lemma: |R| == k
                if len(Rp) == k:
                    # Construct canonical ordered tuple (f[0], f[1], ..., f[n-1])
                    mapping_tuple = tuple(f[i] for i in range(n_q))
                    sink.emit(mapping_tuple)
                return

            # Frontier: query edges with UNKNOWN status across cut (one endpoint mapped, one unmapped)
            frontier = [
                i for i, (a, b) in enumerate(QE)
                if status[i] is None and ((f[a] is None) != (f[b] is None))
            ]

            # Invariant Guard: empty frontier when unmapped vertices remain and |R_p| <= k
            if not frontier:
                raise InvariantViolationError(
                    f"M2 invariant violation: empty frontier while unmapped vertices remain "
                    f"and |R_p|={len(Rp)} <= k={k}. Domain condition k < lambda(Q) required."
                )

            # Deterministic selection: pick edge with minimum index
            e = min(frontier)
            counters.edge_decisions += 1
            a, b = QE[e]
            u, v = (a, b) if f[a] is not None else (b, a)
            u_data = f[u]
            assert u_data is not None

            used = set(z for z in f if z is not None)

            # --- PRESENT (CO) Branch ---
            # Explore candidate data vertices for unmapped endpoint v
            candidates = snapshot.get_neighbors(u_data)
            for (cand_data, edge_lbl) in candidates:
                counters.edge_lookups += 1
                if edge_lbl != qel[e]:
                    continue
                if cand_data in used:
                    continue

                counters.vertex_label_checks += 1
                if snapshot.vertices[cand_data] != qvl[v]:
                    continue

                counters.candidate_extensions += 1
                counters.check_work_limit(max_work)

                # Tentatively assign v -> cand_data
                f_next = list(f)
                f_next[v] = cand_data
                status_next = list(status)
                status_next[e] = "PRESENT"
                Rp_next = set(Rp)
                ok = True

                # Two-endpoint edge resolution for incident edges to v
                for j in incident_edges[v]:
                    if j == e:
                        continue
                    c, d = QE[j]
                    other = c if d == v else d
                    if f_next[other] is not None:
                        # Edge j now has both endpoints mapped
                        counters.edge_lookups += 1
                        other_data = f_next[other]
                        assert other_data is not None
                        present = (
                            snapshot.has_edge(cand_data, other_data)
                            and snapshot.get_edge_label(cand_data, other_data) == qel[j]
                        )

                        if status_next[j] == "MISSING":
                            counters.commitment_checks += 1
                            if present:
                                ok = False
                                break
                        elif status_next[j] == "PRESENT":
                            if not present:
                                ok = False
                                break
                        elif status_next[j] is None:
                            if present:
                                status_next[j] = "PRESENT"
                            else:
                                status_next[j] = "MISSING"
                                Rp_next.add(j)
                                if len(Rp_next) > k:
                                    ok = False
                                    break

                if ok:
                    dfs(f_next, status_next, frozenset(Rp_next))

            # --- MISSING (THIEU) Branch ---
            counters.missing_branches += 1
            status_missing = list(status)
            status_missing[e] = "MISSING"
            new_Rp_size = len(Rp) + 1
            if new_Rp_size <= k:
                # Defer vertex assignment: f remains unchanged
                dfs(list(f), status_missing, Rp | {e})

        # Anchoring loop
        u_upd, v_upd = update.edge
        for qi, (qa, qb) in enumerate(QE):
            if qel[qi] != update.label:
                continue

            for pa, pb in ((u_upd, v_upd), (v_upd, u_upd)):
                counters.vertex_label_checks += 2
                if snapshot.vertices[pa] != qvl[qa]:
                    continue
                if snapshot.vertices[pb] != qvl[qb]:
                    continue

                counters.anchors_started += 1

                # Initialize partial mapping
                f_init: List[Optional[int]] = [None] * n_q
                f_init[qa] = pa
                f_init[qb] = pb

                status_init: List[Optional[str]] = [None] * m
                status_init[qi] = "PRESENT"

                dfs(f_init, status_init, frozenset())

        # 6. Finalize emissions
        raw_events = sink.finalize()
        counters.emitted_tuples = len(raw_events)

        if update.operation == "INSERT":
            return EventResult(
                operation=update.operation,
                edge=update.edge,
                label=update.label,
                entered=raw_events,
                exited=[],
                counters=counters,
                status="SUCCESS",
            )
        else:
            return EventResult(
                operation=update.operation,
                edge=update.edge,
                label=update.label,
                entered=[],
                exited=raw_events,
                counters=counters,
                status="SUCCESS",
            )

    def process_update(
        self,
        query: DStarEventGraph,
        graph_before: DStarEventGraph,
        update: DStarEventUpdate,
        k: int,
        limits: Optional[Dict[str, Any]] = None,
        _skip_domain_check: bool = False,
    ) -> Tuple[EventResult, DStarEventGraph]:
        """
        Transactionally applies update to graph_before, selects proper snapshot,
        executes find_events, and returns (result, graph_after).
        """
        g_before, g_after = update.apply_transactional(graph_before)
        target_snapshot = g_after if update.operation == "INSERT" else g_before
        result = self.find_events(query, target_snapshot, update, k, limits, _skip_domain_check)
        return result, g_after


class M2DynamicEngine:
    """Stateful dynamic streaming wrapper around M2EventEngine."""

    def __init__(
        self,
        query: DStarEventGraph,
        initial_graph: DStarEventGraph,
        k: int,
        limits: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.query = query
        self.graph = initial_graph.clone()
        self.k = k
        self.limits = limits or {}
        self.engine = M2EventEngine(self.limits)

    def step(self, update: DStarEventUpdate) -> EventResult:
        result, self.graph = self.engine.process_update(
            self.query, self.graph, update, self.k, self.limits
        )
        return result
