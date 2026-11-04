#!/usr/bin/env python3
"""
tests/test_rf02_oracle.py — Comprehensive Unit & Self-Validation Tests for Independent Oracle.

Validates exact subgraph monomorphism semantics against hand-verified synthetic graphs:
- Empty match
- Disconnected / missing query edge
- Single match (heterogeneous labels)
- Automorphisms of K3 (triangle, 6 mappings)
- Automorphisms of Star graph (center + leaves, 6 mappings)
- Path matching (P3, 2 mappings)
- Cycle matching (C4, 8 mappings)
- Injectivity collision prevention
- Vertex label mismatch rejection
- Edge label mismatch rejection
- Non-induced extra edges (K3 query in K4, 24 mappings)
- Dynamic edge insertion (0 -> 6 mappings)
- Dynamic edge deletion (6 -> 0 mappings)
- Cross-check against brute-force permutation solver
- Parser error handling for invalid graph/stream structures
"""

import unittest
from scripts.rf02_oracle import (
    LabeledGraph,
    Update,
    find_all_embeddings,
    find_all_embeddings_brute_force,
    compute_update_delta,
    parse_stream_text,
)


class TestOracleSubgraphs(unittest.TestCase):

    def test_01_empty_match_label_mismatch(self):
        """Query requires label 1, data graph only has label 2."""
        Q = LabeledGraph.parse_from_text("v 0 1\nv 1 1\ne 0 1 0\n")
        G = LabeledGraph.parse_from_text("v 0 2\nv 1 2\ne 0 1 0\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 0)

    def test_02_missing_query_edge(self):
        """Query requires edge (0, 1), data graph has vertices but no edge."""
        Q = LabeledGraph.parse_from_text("v 0 1\nv 1 1\ne 0 1 0\n")
        G = LabeledGraph.parse_from_text("v 0 1\nv 1 1\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 0)

    def test_03_single_match_heterogeneous_labels(self):
        """Triangle with distinct vertex labels (1, 2, 3) has trivial automorphism |Aut|=1."""
        Q = LabeledGraph.parse_from_text("v 0 1\nv 1 2\nv 2 3\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        G = LabeledGraph.parse_from_text("v 0 1\nv 1 2\nv 2 3\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], (0, 1, 2))

    def test_04_triangle_automorphisms(self):
        """K3 with uniform vertex and edge labels has exactly 3! = 6 automorphisms."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        G = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 6)
        expected = {(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)}
        self.assertEqual(set(matches), expected)

    def test_05_star_graph_automorphisms(self):
        """Star graph K_{1,3} with center (label 1) and 3 leaves (label 2): exactly 3! = 6 automorphisms."""
        Q = LabeledGraph.parse_from_text(
            "v 0 1\nv 1 2\nv 2 2\nv 3 2\ne 0 1 0\ne 0 2 0\ne 0 3 0\n"
        )
        G = LabeledGraph.parse_from_text(
            "v 0 1\nv 1 2\nv 2 2\nv 3 2\ne 0 1 0\ne 0 2 0\ne 0 3 0\n"
        )
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 6)
        for m in matches:
            self.assertEqual(m[0], 0)  # center query vertex 0 must map to center data vertex 0
            self.assertEqual(set(m[1:]), {1, 2, 3})

    def test_06_path_matching(self):
        """P3 (0-1-2) in P3 has 2 embeddings (reversing the path)."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n")
        G = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 2)
        self.assertEqual(set(matches), {(0, 1, 2), (2, 1, 0)})

    def test_07_cycle_matching(self):
        """C4 in C4 has 2 * 4 = 8 automorphisms (Dihedral group D4)."""
        Q = LabeledGraph.parse_from_text(
            "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"
        )
        G = LabeledGraph.parse_from_text(
            "v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 1 2 0\ne 2 3 0\ne 3 0 0\n"
        )
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 8)

    def test_08_injectivity_constraint(self):
        """P3 query (3 vertices) cannot map injectively into a single edge K2 (2 data vertices)."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n")
        G = LabeledGraph.parse_from_text("v 0 0\nv 1 0\ne 0 1 0\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 0)

    def test_09_vertex_label_mismatch(self):
        """Structure matches, but center vertex label differs."""
        Q = LabeledGraph.parse_from_text("v 0 1\nv 1 2\nv 2 2\ne 0 1 0\ne 0 2 0\n")
        G = LabeledGraph.parse_from_text("v 0 99\nv 1 2\nv 2 2\ne 0 1 0\ne 0 2 0\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 0)

    def test_10_edge_label_mismatch(self):
        """Structure and vertex labels match, but edge label differs (0 vs 1)."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\ne 0 1 0\n")
        G = LabeledGraph.parse_from_text("v 0 0\nv 1 0\ne 0 1 1\n")
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 0)

    def test_11_non_induced_extra_edges(self):
        """Non-induced semantics: K3 query in K4 data graph produces 4 * 6 = 24 embeddings."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        G = LabeledGraph.parse_from_text(
            "v 0 0\nv 1 0\nv 2 0\nv 3 0\n"
            "e 0 1 0\ne 0 2 0\ne 0 3 0\ne 1 2 0\ne 1 3 0\ne 2 3 0\n"
        )
        matches = find_all_embeddings(Q, G)
        self.assertEqual(len(matches), 24)

    def test_12_dynamic_edge_insertion(self):
        """Dynamic insertion: adding edge to path P3 completes K3 -> exactly 6 added, 0 removed."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        G0 = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\n")
        self.assertEqual(len(find_all_embeddings(Q, G0)), 0)

        update = Update("+", 0, 2, 0)
        added, removed, G1 = compute_update_delta(Q, G0, update)
        self.assertEqual(len(added), 6)
        self.assertEqual(len(removed), 0)
        self.assertEqual(len(find_all_embeddings(Q, G1)), 6)

    def test_13_dynamic_edge_deletion(self):
        """Dynamic deletion: removing edge from K3 destroys triangle -> exactly 0 added, 6 removed."""
        Q = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        G0 = LabeledGraph.parse_from_text("v 0 0\nv 1 0\nv 2 0\ne 0 1 0\ne 1 2 0\ne 2 0 0\n")
        self.assertEqual(len(find_all_embeddings(Q, G0)), 6)

        update = Update("-", 0, 2, 0)
        added, removed, G1 = compute_update_delta(Q, G0, update)
        self.assertEqual(len(added), 0)
        self.assertEqual(len(removed), 6)
        self.assertEqual(len(find_all_embeddings(Q, G1)), 0)

    def test_14_cross_check_backtracking_vs_brute_force(self):
        """Verify backtracking matches brute-force permutation solver identically on non-trivial graph."""
        Q = LabeledGraph.parse_from_text(
            "v 0 1\nv 1 2\nv 2 1\ne 0 1 0\ne 1 2 0\n"
        )
        G = LabeledGraph.parse_from_text(
            "v 0 1\nv 1 2\nv 2 1\nv 3 2\nv 4 1\n"
            "e 0 1 0\ne 1 2 0\ne 1 4 0\ne 2 3 0\ne 3 4 0\n"
        )
        sol_bt = find_all_embeddings(Q, G)
        sol_bf = find_all_embeddings_brute_force(Q, G)
        self.assertEqual(sol_bt, sol_bf)
        self.assertGreater(len(sol_bt), 0)

    def test_15_parser_validations(self):
        """Verify parser rejects self-loops and duplicate edges."""
        with self.assertRaises(ValueError):
            LabeledGraph.parse_from_text("v 0 0\ne 0 0 0\n")  # Self loop

        with self.assertRaises(ValueError):
            LabeledGraph.parse_from_text("v 0 0\nv 1 0\ne 0 1 0\ne 0 1 0\n")  # Duplicate edge

        stream_updates = parse_stream_text("e 0 1 0\n-e 0 1 0\n")
        self.assertEqual(len(stream_updates), 2)
        self.assertEqual(stream_updates[0].op, "+")
        self.assertEqual(stream_updates[1].op, "-")


if __name__ == "__main__":
    unittest.main()
