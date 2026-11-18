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
    DEFAULT_MAX_STREAM_STEPS,
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
    validate_run_contract,
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
        self.max_stream_steps = limits.get("max_stream_steps", DEFAULT_MAX_STREAM_STEPS)
        validate_resource_caps(
            self.max_candidates,
            self.max_data_vertices,
            self.max_query_vertices,
            self.max_work,
            self.max_stream_steps,
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
        Executes anchored bare M2-Event-k search for a single update.
        snapshot must be G_after for INSERT and G_before for DELETE.
        In both cases, the anchor edge update.edge must exist in snapshot with update.label.
        """
        effective_limits = {
            "max_work": self.max_work,
            "max_candidates": self.max_candidates,
            "max_data_vertices": self.max_data_vertices,
            "max_query_vertices": self.max_query_vertices,
            "max_stream_steps": self.max_stream_steps,
        }
        if limits:
            effective_limits.update(limits)
            validate_resource_caps(
                effective_limits["max_candidates"],
                effective_limits["max_data_vertices"],
                effective_limits["max_query_vertices"],
                effective_limits["max_work"],
                effective_limits["max_stream_steps"],
            )

        max_work = effective_limits["max_work"]

        # Run-level contract validation (cheap resource guards run before min-cut)
        validate_run_contract(query, snapshot, k, effective_limits)

        # Candidate permutation space upper bound / early size check
        n_q = len(query.vertices)
        n_g = len(snapshot.vertices)
        if n_g < n_q:
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

        # Verify anchor edge exists in snapshot
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

        # Prepare indexing for query elements
        sorted_q_vertices = sorted(query.vertices.keys())
        v_to_idx = {v: i for i, v in enumerate(sorted_q_vertices)}
        q_edges = sorted(query.edges.keys())
        QE: List[Tuple[int, int]] = [(v_to_idx[e[0]], v_to_idx[e[1]]) for e in q_edges]
        qel = [query.edges[e] for e in q_edges]
        qvl = [query.vertices[v] for v in sorted_q_vertices]
        m = len(QE)

        counters = EventCounters()
        sink = EventSink()

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
                    mapping_tuple = tuple(f[i] for i in range(n_q))
                    sink.emit(mapping_tuple)
                return

            # Frontier: query edges with UNKNOWN status across cut
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
            counters.check_work_limit(max_work)

            a, b = QE[e]
            u, v = (a, b) if f[a] is not None else (b, a)
            u_data = f[u]
            assert u_data is not None

            used = set(z for z in f if z is not None)

            # --- PRESENT (CO) Branch ---
            candidates = snapshot.get_neighbors(u_data)
            for (cand_data, edge_lbl) in candidates:
                # F03.2: Count attempted candidate evaluation and check budget immediately
                counters.candidate_evaluations += 1
                counters.check_work_limit(max_work)

                counters.edge_lookups += 1
                if edge_lbl != qel[e]:
                    continue
                if cand_data in used:
                    continue

                counters.vertex_label_checks += 1
                if snapshot.vertices[cand_data] != qvl[v]:
                    continue

                counters.candidate_extensions += 1

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

                f_init: List[Optional[int]] = [None] * n_q
                f_init[qa] = pa
                f_init[qb] = pb

                status_init: List[Optional[str]] = [None] * m
                status_init[qi] = "PRESENT"

                dfs(f_init, status_init, frozenset())

        # Finalize emissions
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

    def _private_test_seam_check_empty_frontier(
        self,
        query: DStarEventGraph,
        snapshot: DStarEventGraph,
        f: List[Optional[int]],
        status: List[Optional[str]],
        Rp: FrozenSet[int],
        k: int,
    ) -> None:
        """
        Private testing seam (F02.3): allows test code to invoke the frontier invariant check
        directly on an internal state without bypassing public run contract.
        """
        sorted_q_vertices = sorted(query.vertices.keys())
        v_to_idx = {v: i for i, v in enumerate(sorted_q_vertices)}
        q_edges = sorted(query.edges.keys())
        QE = [(v_to_idx[e[0]], v_to_idx[e[1]]) for e in q_edges]

        frontier = [
            i for i, (a, b) in enumerate(QE)
            if status[i] is None and ((f[a] is None) != (f[b] is None))
        ]
        if not frontier and any(v is None for v in f) and len(Rp) <= k:
            raise InvariantViolationError(
                f"M2 invariant violation: empty frontier while unmapped vertices remain "
                f"and |R_p|={len(Rp)} <= k={k}. Domain condition k < lambda(Q) required."
            )


class M2DynamicEngine:
    """
    Stateful dynamic streaming wrapper around M2EventEngine.
    Enforces deep isolation from caller mutations (F06).
    """

    def __init__(
        self,
        query: DStarEventGraph,
        initial_graph: DStarEventGraph,
        k: int,
        limits: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.limits = dict(limits) if limits else {}
        self.k = k
        # Validate run-level contract at engine initialization
        validate_run_contract(query, initial_graph, k, self.limits)
        # Deep clone query and graph to isolate from caller mutation (F06)
        self.query = query.clone()
        self.graph = initial_graph.clone()
        self.engine = M2EventEngine(self.limits)
        self.step_count = 0

    def step(self, update: DStarEventUpdate) -> EventResult:
        result, new_graph = self.engine.process_update(
            self.query, self.graph, update, self.k, self.limits
        )
        self.graph = new_graph
        self.step_count += 1
        return result
