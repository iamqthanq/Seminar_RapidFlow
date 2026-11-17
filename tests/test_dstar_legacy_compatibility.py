#!/usr/bin/env python3
"""
tests/test_dstar_legacy_compatibility.py — Legacy APX-02 Compatibility Test Suite.

Cross-checks independent D* M0 Oracle against frozen legacy APX-02 Oracle:
- APX-02 Config: eps_m = k, eps_x = None, mode = ConnectivityMode.STRICT.
- Legacy SCORE_CHANGED events are filtered out at the test adapter boundary.
- Mathematical Domain: 0 <= k < lambda(Q).
  Under this condition, Q - R(f) is spanning connected, guaranteeing G[image(f)] is connected.
  With eps_x = None, Model C acceptance is mathematically equivalent to D* Edge Recall S2.
- Seeded Matrix: 56 configurations, 224 valid dynamic updates.
  C4 (k=0, 1), Diamond (k=0, 1), K4 (k=0, 1, 2), 8 seeds each (seed base 20261117).
"""

from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Legacy APX-02 imports (Test-only adapter boundary)
from scripts.apx02_oracle import (
    ConnectivityMode,
    DynamicOracle as APXDynamicOracle,
    SimpleGraph as APXGraph,
    Update as APXUpdate,
)

# Independent D* Oracle imports
from scripts.dstar_oracle import (
    DStarDynamicOracle,
    DStarGraph,
    DStarUpdate,
    InvalidThresholdError,
    compute_edge_connectivity,
    enumerate_snapshot,
)


def build_c4_query(label_mode: str = "uniform") -> DStarGraph:
    Q = DStarGraph()
    lbls = [0, 1, 0, 1] if label_mode == "mixed" else [0, 0, 0, 0]
    for i, v in enumerate((1, 2, 3, 4)):
        Q.add_vertex(v, lbls[i])
    e_lbls = [0, 1, 0, 1] if label_mode == "mixed" else [0, 0, 0, 0]
    Q.add_edge(1, 2, e_lbls[0])
    Q.add_edge(2, 3, e_lbls[1])
    Q.add_edge(3, 4, e_lbls[2])
    Q.add_edge(4, 1, e_lbls[3])
    return Q


def build_diamond_query(label_mode: str = "uniform") -> DStarGraph:
    Q = DStarGraph()
    lbls = [0, 1, 0, 1] if label_mode == "mixed" else [0, 0, 0, 0]
    for i, v in enumerate((1, 2, 3, 4)):
        Q.add_vertex(v, lbls[i])
    e_lbls = [0, 1, 0, 1, 0] if label_mode == "mixed" else [0, 0, 0, 0, 0]
    Q.add_edge(1, 2, e_lbls[0])
    Q.add_edge(1, 3, e_lbls[1])
    Q.add_edge(2, 3, e_lbls[2])
    Q.add_edge(2, 4, e_lbls[3])
    Q.add_edge(3, 4, e_lbls[4])
    return Q


def build_k4_query(label_mode: str = "uniform") -> DStarGraph:
    Q = DStarGraph()
    lbls = [0, 1, 0, 1] if label_mode == "mixed" else [0, 0, 0, 0]
    for i, v in enumerate((1, 2, 3, 4)):
        Q.add_vertex(v, lbls[i])
    for u in (1, 2, 3, 4):
        for v in (1, 2, 3, 4):
            if u < v:
                Q.add_edge(u, v, 0)
    return Q


def dstar_to_apx_graph(d_g: DStarGraph) -> APXGraph:
    apx_g = APXGraph()
    for v, lbl in d_g.vertices.items():
        apx_g.add_vertex(v, int(lbl))
    for (u, v), lbl in d_g.edges.items():
        apx_g.add_edge(u, v, int(lbl))
    return apx_g


def dstar_to_apx_update(up: DStarUpdate) -> APXUpdate:
    op = "+" if up.op == "INSERT" else "-"
    return APXUpdate(op=op, u=up.u, v=up.v, label=int(up.label))


def generate_random_data_graph(
    seed: int,
    n_vertices: int = 6,
    n_initial_edges: int = 7,
    label_mode: str = "uniform",
) -> DStarGraph:
    rng = random.Random(seed)
    G = DStarGraph()
    for v in range(1, n_vertices + 1):
        lbl = rng.choice([0, 1]) if label_mode == "mixed" else 0
        G.add_vertex(v, lbl)

    # Possible edges
    all_pairs = [
        (u, v) for u in range(1, n_vertices + 1) for v in range(u + 1, n_vertices + 1)
    ]
    rng.shuffle(all_pairs)
    initial_pairs = all_pairs[:n_initial_edges]
    for u, v in initial_pairs:
        e_lbl = rng.choice([0, 1]) if label_mode == "mixed" else 0
        G.add_edge(u, v, e_lbl)
    return G


def generate_valid_updates(
    G: DStarGraph,
    seed: int,
    n_updates: int = 4,
    label_mode: str = "uniform",
) -> List[DStarUpdate]:
    rng = random.Random(seed)
    current_g = G.clone()
    updates: List[DStarUpdate] = []
    nodes = list(current_g.vertices.keys())

    for _ in range(n_updates):
        # Decide op: if edges exist, can DELETE or INSERT
        can_delete = len(current_g.edges) > 3
        can_insert = len(current_g.edges) < (len(nodes) * (len(nodes) - 1)) // 2

        if can_delete and (not can_insert or rng.random() < 0.5):
            # Choose random existing edge to delete
            edge_key = rng.choice(list(current_g.edges.keys()))
            lbl = current_g.edges[edge_key]
            up = DStarUpdate(op="DELETE", u=edge_key[0], v=edge_key[1], label=lbl)
            current_g.remove_edge(edge_key[0], edge_key[1], lbl)
            updates.append(up)
        else:
            # Choose random non-existing pair to insert
            non_edges = [
                (u, v)
                for u in nodes
                for v in nodes
                if u < v and not current_g.has_edge(u, v)
            ]
            if not non_edges:
                break
            edge_key = rng.choice(non_edges)
            lbl = rng.choice([0, 1]) if label_mode == "mixed" else 0
            up = DStarUpdate(op="INSERT", u=edge_key[0], v=edge_key[1], label=lbl)
            current_g.add_edge(edge_key[0], edge_key[1], lbl)
            updates.append(up)

    return updates


class TestDStarLegacyCompatibility(unittest.TestCase):
    """
    Test suite executing the 56-configuration legacy compatibility matrix.
    """

    def test_seeded_matrix_56_configurations(self) -> None:
        """
        Runs at least 56 configurations (C4 k=0,1; Diamond k=0,1; K4 k=0,1,2 with 8 seeds each).
        Applies at least 4 updates per configuration (>= 224 valid updates).
        Asserts 100% agreement between D* M0 and APX-02 at tuple level.
        """
        seed_base = 20261117
        matrix_specs = [
            ("C4", build_c4_query, [0, 1], 8),
            ("Diamond", build_diamond_query, [0, 1], 8),
            ("K4", build_k4_query, [0, 1, 2], 8),
        ]

        total_configs = 0
        total_updates = 0
        total_entered_events = 0
        total_exited_events = 0
        total_entered_tuples = 0
        total_exited_tuples = 0

        for q_name, q_builder, k_values, seeds_count in matrix_specs:
            for k in k_values:
                for s_idx in range(seeds_count):
                    total_configs += 1
                    seed = seed_base + total_configs * 31 + s_idx
                    # Alternate label modes for diverse coverage
                    label_mode = "uniform" if (total_configs % 2 == 0) else "mixed"

                    Q_dstar = q_builder(label_mode=label_mode)
                    G_dstar = generate_random_data_graph(
                        seed=seed,
                        n_vertices=6,
                        n_initial_edges=7,
                        label_mode=label_mode,
                    )
                    updates = generate_valid_updates(
                        G=G_dstar,
                        seed=seed + 100,
                        n_updates=4,
                        label_mode=label_mode,
                    )
                    self.assertGreaterEqual(len(updates), 4)

                    # Initialize D* Oracle
                    dstar_oracle = DStarDynamicOracle(Q_dstar, G_dstar, k=k)

                    # Initialize APX-02 Oracle
                    Q_apx = dstar_to_apx_graph(Q_dstar)
                    G_apx = dstar_to_apx_graph(G_dstar)
                    apx_oracle = APXDynamicOracle(
                        Q=Q_apx,
                        G0=G_apx,
                        eps_m=k,
                        eps_x=None,
                        mode=ConnectivityMode.STRICT,
                    )

                    # 1. Verify initial snapshot agreement
                    dstar_initial = set(dstar_oracle.current_matches)
                    apx_initial = set(apx_oracle.current_matches)
                    self.assertEqual(
                        dstar_initial,
                        apx_initial,
                        f"Initial snapshot mismatch for {q_name} k={k} seed={seed}",
                    )

                    # 2. Apply updates and check step-by-step
                    for up_dstar in updates:
                        total_updates += 1
                        delta_dstar = dstar_oracle.apply_update(up_dstar)

                        up_apx = dstar_to_apx_update(up_dstar)
                        delta_apx = apx_oracle.apply_update(up_apx)

                        # Filter adapter: compare entered and exited sets
                        dstar_entered = set(delta_dstar.entered)
                        apx_entered = set(delta_apx.entered)
                        dstar_exited = set(delta_dstar.exited)
                        apx_exited = set(delta_apx.exited)

                        self.assertEqual(
                            dstar_entered,
                            apx_entered,
                            f"ENTERED mismatch at step {total_updates} for {q_name} k={k} op={up_dstar.op}",
                        )
                        self.assertEqual(
                            dstar_exited,
                            apx_exited,
                            f"EXITED mismatch at step {total_updates} for {q_name} k={k} op={up_dstar.op}",
                        )

                        # Check snapshot agreement after update
                        self.assertEqual(
                            set(dstar_oracle.current_matches),
                            set(apx_oracle.current_matches),
                            f"Snapshot mismatch after step {total_updates}",
                        )

                        if dstar_entered:
                            total_entered_events += 1
                            total_entered_tuples += len(dstar_entered)
                        if dstar_exited:
                            total_exited_events += 1
                            total_exited_tuples += len(dstar_exited)

        # Assert matrix coverage requirements
        self.assertGreaterEqual(total_configs, 56, "Must test at least 56 configurations")
        self.assertGreaterEqual(total_updates, 224, "Must test at least 224 updates")
        self.assertGreater(
            total_entered_events, 0, "Must observe non-empty ENTERED events in random stream"
        )
        self.assertGreater(
            total_exited_events, 0, "Must observe non-empty EXITED events in random stream"
        )

    def test_rejection_of_unsupported_k_exceeding_lambda(self) -> None:
        """
        Confirms that D* M0 explicitly rejects k >= lambda(Q), preventing unsound
        extension of S1 equivalence into the disconnected domain.
        """
        Q = build_c4_query()  # lambda(C4) = 2
        G = generate_random_data_graph(seed=42, n_vertices=6)

        # k=2 is lambda(C4), so it must be rejected by D* M0
        with self.assertRaises(InvalidThresholdError):
            enumerate_snapshot(Q, G, k=2)

        # Legacy APX-02 would accept eps_m=2 without complaining about lambda
        Q_apx = dstar_to_apx_graph(Q)
        G_apx = dstar_to_apx_graph(G)
        apx_matches = APXDynamicOracle(
            Q=Q_apx,
            G0=G_apx,
            eps_m=2,
            eps_x=None,
            mode=ConnectivityMode.STRICT,
        )
        self.assertIsNotNone(apx_matches)


if __name__ == "__main__":
    unittest.main()
