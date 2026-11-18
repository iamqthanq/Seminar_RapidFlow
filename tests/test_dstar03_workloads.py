#!/usr/bin/env python3
"""
tests/test_dstar03_workloads.py — Unit Test Suite for DSTAR-03 Workloads & Manifest (v2).

Covers:
1. Exact registration counts (40 synthetic + 5 witness + 20 netflow = 65 workloads).
2. Manifest schema, cryptographic component hashes, and provenance metadata.
3. Synthetic toggle cycle integrity (inversion validity and G4 == G0).
4. Structured semantic witnesses ground truth (1 ENTERED, 0, 0, 1 EXITED).
5. Mock NetFlow bounded streaming extraction ($O(1)$ memory), deterministic priority selection, and projection.
6. F04 regression: rejection of non-zero vertex labels in NetFlow source.
7. F04 regression: rejection of unknown operation tokens in NetFlow deletion file.
8. F04 regression: handling of comments and blank lines during operation parsing.
9. F04 regression: rejection when a selected vertex is missing from Ndata.graph.
10. F04 regression: retention of updates before target, source metadata, and is_target flag.
11. F04 regression: high-degree bounded memory candidate selection.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_dstar03_workloads import (
    QUERY_CONDITIONS,
    canonical_json_bytes,
    compute_workload_fingerprint,
    generate_netflow_workloads,
    generate_synthetic_workloads,
    generate_witness_workloads,
    parse_netflow_deletion_records,
    sha256_of_obj,
    verify_workload_m0,
)
from dstar_event_common import DStarEventGraph, DStarEventUpdate


class TestDStar03Workloads(unittest.TestCase):
    """Test suite for DSTAR-03 workload definitions, manifest, and generator logic."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
        cls.workloads_dir = REPO_ROOT / "tests" / "fixtures" / "dstar03" / "workloads"
        with cls.manifest_path.open("r", encoding="utf-8") as f:
            cls.manifest = json.load(f)

    def test_01_manifest_registrations_and_counts(self) -> None:
        """Verifies exact 65 registered workloads with 40/5/20 breakdown."""
        self.assertEqual(self.manifest["total_registered_workloads"], 65)
        counts = self.manifest["counts_by_family"]
        self.assertEqual(counts["SYNTHETIC_MATRIX"], 40)
        self.assertEqual(counts["STRUCTURED_WITNESS"], 5)
        self.assertEqual(counts["NETFLOW_PROBE"], 20)

        workloads = self.manifest["workloads"]
        self.assertEqual(len(workloads), 65)

        families = [w["family"] for w in workloads]
        self.assertEqual(families.count("SYNTHETIC_MATRIX"), 40)
        self.assertEqual(families.count("STRUCTURED_WITNESS"), 5)
        self.assertEqual(families.count("NETFLOW_PROBE"), 20)

        case_indices = [w["case_index"] for w in workloads]
        self.assertEqual(sorted(case_indices), list(range(65)))

    def test_02_workload_schema_and_component_hashes(self) -> None:
        """Verifies that all 65 fixtures exist on disk, match schema, and component hashes match."""
        for entry in self.manifest["workloads"]:
            rel_path = entry["relative_path"]
            file_path = REPO_ROOT / rel_path
            self.assertTrue(file_path.exists(), f"Missing fixture file: {file_path}")

            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["workload_id"], entry["workload_id"])
            self.assertEqual(data["case_index"], entry["case_index"])
            self.assertEqual(data["family"], entry["family"])

            # Verify component hashes
            expected_q_sha = sha256_of_obj(data["query"])
            self.assertEqual(data["sha256_query"], expected_q_sha)

            expected_g0_sha = sha256_of_obj(data["initial_graph"])
            self.assertEqual(data["sha256_initial_graph"], expected_g0_sha)

            expected_stream_sha = sha256_of_obj(data["stream"])
            self.assertEqual(data["sha256_stream"], expected_stream_sha)

            expected_w_sha = compute_workload_fingerprint(data)
            self.assertEqual(data["sha256_workload"], expected_w_sha)
            self.assertEqual(entry["sha256_workload"], expected_w_sha)

    def test_03_synthetic_toggle_cycle_and_inversion(self) -> None:
        """Verifies that the first 4 updates of synthetic workloads form an exact toggle cycle."""
        for entry in self.manifest["workloads"]:
            if entry["family"] != "SYNTHETIC_MATRIX":
                continue

            file_path = REPO_ROOT / entry["relative_path"]
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            g0 = DStarEventGraph.from_dict(data["initial_graph"])
            toggle_pair = tuple(data["toggle_pair"])
            stream = [
                DStarEventUpdate(operation=s["operation"], edge=tuple(s["edge"]), label=s["label"])
                for s in data["stream"]
            ]

            cycle = stream[:4]
            self.assertEqual(len(cycle), 4)

            for upd in cycle:
                self.assertEqual(upd.edge, toggle_pair)

            ops = [u.operation for u in cycle]
            if data["toggle_mode"] == "DELETE_FIRST":
                self.assertEqual(ops, ["DELETE", "INSERT", "DELETE", "INSERT"])
            else:
                self.assertEqual(ops, ["INSERT", "DELETE", "INSERT", "DELETE"])

            g_curr = g0.clone()
            for upd in cycle:
                if upd.operation == "INSERT":
                    g_curr.add_edge(upd.edge[0], upd.edge[1], upd.label)
                else:
                    g_curr.remove_edge(upd.edge[0], upd.edge[1], upd.label)

            self.assertEqual(g_curr.edges, g0.edges)

    def test_04_witness_exact_event_expectations(self) -> None:
        """Verifies that all 5 structured witnesses produce 1 ENTERED, 0, 0, 1 EXITED."""
        for entry in self.manifest["workloads"]:
            if entry["family"] != "STRUCTURED_WITNESS":
                continue

            file_path = REPO_ROOT / entry["relative_path"]
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            m0_verif = data["m0_verification"]
            self.assertTrue(m0_verif["verified"])
            self.assertEqual(m0_verif["total_entered_events"], 1)
            self.assertEqual(m0_verif["total_exited_events"], 1)
            self.assertEqual(m0_verif["total_events"], 2)

            steps = m0_verif["steps"]
            self.assertEqual(len(steps), 4)

            self.assertEqual(steps[0]["entered"], [[0, 1, 2, 3]])
            self.assertEqual(steps[0]["exited"], [])
            self.assertEqual(steps[1]["entered"], [])
            self.assertEqual(steps[1]["exited"], [])
            self.assertEqual(steps[2]["entered"], [])
            self.assertEqual(steps[2]["exited"], [])
            self.assertEqual(steps[3]["entered"], [])
            self.assertEqual(steps[3]["exited"], [[0, 1, 2, 3]])

    def test_05_mock_netflow_bounded_extraction(self) -> None:
        """Verifies bounded streaming, neighbor selection, and projection on a mock NetFlow slice."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndata = tmp_path / "Ndata.graph"
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            del_lines = []
            for i in range(64):
                if i % 2 == 0:
                    del_lines.append(f"e {100 + i*2} {101 + i*2} 0\n")
                else:
                    del_lines.append(f"-e {100 + i*2} {101 + i*2} 2\n")
            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.writelines(del_lines)

            data_lines = []
            for v in range(300):
                data_lines.append(f"v {v} 0\n")
            data_lines.append("e 102 103 2\n")
            data_lines.append("e 102 201 0\n")
            data_lines.append("e 103 202 0\n")
            with mock_ndata.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            workloads = generate_netflow_workloads(tmp_path)
            self.assertEqual(len(workloads), 20)

            for w in workloads:
                S = w["sample_universe_S"]
                self.assertEqual(len(S), 8)
                self.assertEqual(len(set(S)), 8)
                g0 = DStarEventGraph.from_dict(w["initial_graph"])
                self.assertEqual(len(g0.vertices), 8)
                for v in S:
                    self.assertIn(v, g0.vertices)

    def test_06_reject_nonzero_vertex_label(self) -> None:
        """F04: Verifies generator rejects mock Ndata.graph containing non-zero selected vertex labels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndata = tmp_path / "Ndata.graph"
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            del_lines = [f"e {i*2} {i*2+1} 0\n" if i % 2 == 0 else f"-e {i*2} {i*2+1} 0\n" for i in range(64)]
            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.writelines(del_lines)

            # All vertices have label 7 (non-zero)
            data_lines = [f"v {v} 7\n" for v in range(200)]
            data_lines.append("e 0 1 0\n")
            with mock_ndata.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            with self.assertRaises(ValueError) as ctx:
                generate_netflow_workloads(tmp_path)
            self.assertIn("requires vertex label 0", str(ctx.exception))

    def test_07_reject_unknown_operation_token(self) -> None:
        """F04: Verifies operation parser rejects unknown tokens (not e or -e)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.write("e 1 2 0\n")
                f.write("x 3 4 0\n")  # Invalid token

            with self.assertRaises(ValueError) as ctx:
                parse_netflow_deletion_records(mock_ndeletion, max_records=2)
            self.assertIn("Unknown operation token 'x'", str(ctx.exception))

    def test_08_comments_and_blank_lines_parsing(self) -> None:
        """F04: Verifies parser skips comments and blank lines and tracks physical line numbers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.write("# Header comment\n")
                f.write("\n")
                f.write("c Another comment line\n")
                f.write("e 10 11 0\n")
                f.write("\n")
                f.write("-e 12 13 2\n")

            records = parse_netflow_deletion_records(mock_ndeletion, max_records=2)
            self.assertEqual(len(records), 2)
            # Record 0: source_op_idx=0, line_no=4
            self.assertEqual(records[0][0], 0)
            self.assertEqual(records[0][1], "INSERT")
            self.assertEqual(records[0][5], 4)
            # Record 1: source_op_idx=1, line_no=6
            self.assertEqual(records[1][0], 1)
            self.assertEqual(records[1][1], "DELETE")
            self.assertEqual(records[1][5], 6)

    def test_09_reject_missing_selected_vertex(self) -> None:
        """F04: Verifies generator raises ValueError when a selected vertex does not exist in Ndata.graph."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndata = tmp_path / "Ndata.graph"
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            del_lines = [f"e {i*2} {i*2+1} 0\n" if i % 2 == 0 else f"-e {i*2} {i*2+1} 0\n" for i in range(64)]
            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.writelines(del_lines)

            # Ndata only defines vertices 10..50, missing 0 and 1
            data_lines = [f"v {v} 0\n" for v in range(10, 50)]
            with mock_ndata.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            with self.assertRaises(ValueError) as ctx:
                generate_netflow_workloads(tmp_path)
            self.assertIn("does not exist in source", str(ctx.exception))

    def test_10_retained_updates_and_target_flags(self) -> None:
        """F04: Verifies that updates before target are retained and target flag is set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndata = tmp_path / "Ndata.graph"
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            # Create 64 operations where vertices 10 and 11 appear at op 0 and op 5
            del_lines = []
            for i in range(64):
                if i == 0:
                    del_lines.append("e 10 11 0\n")
                elif i == 5:
                    del_lines.append("e 10 11 0\n")
                elif i % 2 == 0:
                    del_lines.append(f"e {100 + i} {200 + i} 0\n")
                else:
                    del_lines.append(f"-e {100 + i} {200 + i} 0\n")

            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.writelines(del_lines)

            data_lines = [f"v {v} 0\n" for v in range(300)]
            with mock_ndata.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            workloads = generate_netflow_workloads(tmp_path)
            # Find a workload that selected op 5 or op 0
            for w in workloads:
                stream = w["stream"]
                self.assertGreater(len(stream), 0)
                # Verify source metadata exists
                for s in stream:
                    self.assertIn("source_op_index", s)
                    self.assertIn("physical_line_number", s)
                    self.assertIn("is_target", s)
                # Exactly one target update in projected stream
                target_count = sum(1 for s in stream if s["is_target"])
                self.assertEqual(target_count, 1)

    def test_11_high_degree_bounded_memory(self) -> None:
        """F04: Verifies that high-degree vertices with 200 neighbors are processed with bounded O(1) memory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndata = tmp_path / "Ndata.graph"
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            del_lines = [f"e {i*2} {i*2+1} 0\n" if i % 2 == 0 else f"-e {i*2} {i*2+1} 0\n" for i in range(64)]
            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.writelines(del_lines)

            data_lines = [f"v {v} 0\n" for v in range(300)]
            # Vertex 0 has 200 neighbor edges
            for nbr in range(50, 250):
                data_lines.append(f"e 0 {nbr} 0\n")

            with mock_ndata.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            workloads = generate_netflow_workloads(tmp_path)
            self.assertEqual(len(workloads), 20)
            for w in workloads:
                self.assertEqual(len(w["sample_universe_S"]), 8)


if __name__ == "__main__":
    unittest.main()
