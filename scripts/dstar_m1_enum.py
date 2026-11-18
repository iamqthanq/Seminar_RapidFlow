#!/usr/bin/env python3
"""
scripts/dstar_m1_enum.py — M1-enum-Exact-k Baseline Event Enumeration Engine.

Implements the D* M1-enum-Exact-k baseline: decomposes the delta event search
around the updated edge into exact subgraph matching across all (m-1 choose k)
variant queries Q - D where D is a subset of E_Q \\ {q} of size k.

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
    ResourceLimitError,
    UnknownVertexError,
    canonical_ordered_tuple,
    compute_edge_connectivity,
    is_connected,
    validate_resource_caps,
)


class M1EnumEngine:
    """
    M1-enum-Exact-k event enumeration engine.
    Finds entered/exited embeddings directly on the relevant snapshot graph
    anchored at the updated edge.
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
    ) -> EventResult:
        """
        Executes anchored M1-enum search for a single update.
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
        if k >= lambda_q:
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

        # 5. Execute M1 search
        counters = EventCounters()
        sink = EventSink()

        # Query edge list and lookup
        q_edges = sorted(query.edges.keys())
        q_edge_labels = {e: query.edges[e] for e in q_edges}
        sorted_q_vertices = sorted(query.vertices.keys())

        for q_edge in q_edges:
            if q_edge_labels[q_edge] != update.label:
                continue

            qa, qb = q_edge
            for pa, pb in ((u_upd, v_upd), (v_upd, u_upd)):
                # Check anchor vertex label compatibility
                counters.vertex_label_checks += 2
                if snapshot.vertices[pa] != query.vertices[qa]:
                    continue
                if snapshot.vertices[pb] != query.vertices[qb]:
                    continue

                counters.anchors_started += 1

                # Generate all k-subsets of remaining query edges
                other_edges = [e for e in q_edges if e != q_edge]
                for missing_subset in itertools.combinations(other_edges, k):
                    counters.variants_started += 1
                    D = set(missing_subset)
                    keep_edges = [e for e in q_edges if e not in D]

                    # Build deterministic connected query search order starting from qa, qb
                    order = self._build_connected_search_order(sorted_q_vertices, qa, qb, keep_edges)

                    # Prepare kept incident edges for each vertex along search order
                    order_set: Set[int] = set()
                    kept_incident: Dict[int, List[Tuple[int, Any]]] = {v: [] for v in sorted_q_vertices}
                    for e in keep_edges:
                        kept_incident[e[0]].append((e[1], q_edge_labels[e]))
                        kept_incident[e[1]].append((e[0], q_edge_labels[e]))

                    # Initial mapping state
                    f: Dict[int, int] = {qa: pa, qb: pb}
                    used: Set[int] = {pa, pb}

                    # Recursive matching DFS for variant Q - D
                    self._match_variant_dfs(
                        order=order,
                        idx=2,
                        f=f,
                        used=used,
                        kept_incident=kept_incident,
                        D=D,
                        q_edge_labels=q_edge_labels,
                        query=query,
                        snapshot=snapshot,
                        counters=counters,
                        max_work=max_work,
                        sink=sink,
                    )

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
    ) -> Tuple[EventResult, DStarEventGraph]:
        """
        Transactionally applies update to graph_before, selects proper snapshot,
        executes find_events, and returns (result, graph_after).
        """
        g_before, g_after = update.apply_transactional(graph_before)
        target_snapshot = g_after if update.operation == "INSERT" else g_before
        result = self.find_events(query, target_snapshot, update, k, limits)
        return result, g_after

    def _build_connected_search_order(
        self,
        all_vertices: List[int],
        v0: int,
        v1: int,
        keep_edges: List[Tuple[int, int]],
    ) -> List[int]:
        """Constructs a deterministic connected search order on V_Q starting with [v0, v1]."""
        order = [v0, v1]
        order_set = {v0, v1}
        adj_keep: Dict[int, List[int]] = {v: [] for v in all_vertices}
        for (u, v) in keep_edges:
            adj_keep[u].append(v)
            adj_keep[v].append(u)

        while len(order) < len(all_vertices):
            # Pick unmapped vertex with at least one neighbor in order_set, tie-breaking by smallest ID
            best_candidate: Optional[int] = None
            for v in all_vertices:
                if v not in order_set:
                    has_nbr = any(nbr in order_set for nbr in adj_keep[v])
                    if has_nbr:
                        if best_candidate is None or v < best_candidate:
                            best_candidate = v
            if best_candidate is None:
                # Should be impossible when k < lambda(Q) because Q - D is connected
                raise InvariantViolationError(
                    f"Variant Q - D disconnected with keep_edges: {keep_edges}"
                )
            order.append(best_candidate)
            order_set.add(best_candidate)
        return order

    def _match_variant_dfs(
        self,
        order: List[int],
        idx: int,
        f: Dict[int, int],
        used: Set[int],
        kept_incident: Dict[int, List[Tuple[int, Any]]],
        D: Set[Tuple[int, int]],
        q_edge_labels: Dict[Tuple[int, int], Any],
        query: DStarEventGraph,
        snapshot: DStarEventGraph,
        counters: EventCounters,
        max_work: int,
        sink: EventSink,
    ) -> None:
        """DFS recursive step for exact subgraph matching on Q - D."""
        if idx == len(order):
            counters.raw_exact_results += 1
            # Equivalence verification: verify all edges in D are strictly missing in snapshot
            for d_edge in D:
                counters.edge_lookups += 1
                da, db = d_edge
                pa, pb = f[da], f[db]
                if snapshot.has_edge(pa, pb) and snapshot.get_edge_label(pa, pb) == q_edge_labels[d_edge]:
                    # Not missing! Mapping does not have R(f) == D
                    return
            # Valid embedding!
            mapping_tuple = canonical_ordered_tuple(f, query)
            sink.emit(mapping_tuple)
            return

        curr_q = order[idx]
        curr_q_label = query.vertices[curr_q]

        # Invariant: curr_q has at least one neighbor already mapped in order[0..idx-1]
        # Intersect candidate sets from mapped neighbors in kept_incident
        mapped_nbrs = [(nbr, lbl) for (nbr, lbl) in kept_incident[curr_q] if nbr in f]

        # Use first mapped neighbor's adjacency in snapshot to generate candidate pool
        first_nbr, first_lbl = mapped_nbrs[0]
        first_mapped_data = f[first_nbr]

        candidates = snapshot.get_neighbors(first_mapped_data)
        for (cand_data, edge_lbl) in candidates:
            counters.edge_lookups += 1
            if edge_lbl != first_lbl:
                continue
            if cand_data in used:
                continue

            counters.vertex_label_checks += 1
            if snapshot.vertices[cand_data] != curr_q_label:
                continue

            # Check remaining mapped neighbors
            compatible = True
            for other_nbr, other_lbl in mapped_nbrs[1:]:
                counters.edge_lookups += 1
                other_mapped_data = f[other_nbr]
                if not snapshot.has_edge(cand_data, other_mapped_data):
                    compatible = False
                    break
                if snapshot.get_edge_label(cand_data, other_mapped_data) != other_lbl:
                    compatible = False
                    break

            if not compatible:
                continue

            counters.candidate_extensions += 1
            counters.check_work_limit(max_work)

            # Assign and recurse
            f[curr_q] = cand_data
            used.add(cand_data)
            self._match_variant_dfs(
                order, idx + 1, f, used, kept_incident, D,
                q_edge_labels, query, snapshot, counters, max_work, sink
            )
            # Backtrack
            del f[curr_q]
            used.remove(cand_data)


class M1DynamicEngine:
    """Stateful dynamic streaming wrapper around M1EnumEngine."""

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
        self.engine = M1EnumEngine(self.limits)

    def step(self, update: DStarEventUpdate) -> EventResult:
        result, self.graph = self.engine.process_update(
            self.query, self.graph, update, self.k, self.limits
        )
        return result
