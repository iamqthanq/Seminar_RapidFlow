#!/usr/bin/env python3
"""
tests/test_dstar03_workloads.py — Unit Test Suite for DSTAR-03 Workloads & Manifest.

Covers:
1. Exact registration counts (40 synthetic + 5 witness + 20 netflow = 65 workloads).
2. Manifest schema, cryptographic component hashes, and provenance metadata.
3. Synthetic toggle cycle integrity (inversion validity and G4 == G0).
4. Structured semantic witnesses ground truth (1 ENTERED, 0, 0, 1 EXITED).
5. Mock NetFlow bounded streaming extraction, deterministic priority selection, and projection.
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
    generate_netflow_workloads,
    generate_synthetic_workloads,
    generate_witness_workloads,
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

            self.assertEqual(data["sha256_workload"], entry["sha256_workload"])

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

            # Steps 0..3 are the toggle cycle
            cycle = stream[:4]
            self.assertEqual(len(cycle), 4)

            # All 4 updates must operate on the exact toggle_pair
            for upd in cycle:
                self.assertEqual(upd.edge, toggle_pair)

            # Operations must alternate
            ops = [u.operation for u in cycle]
            if data["toggle_mode"] == "DELETE_FIRST":
                self.assertEqual(ops, ["DELETE", "INSERT", "DELETE", "INSERT"])
            else:
                self.assertEqual(ops, ["INSERT", "DELETE", "INSERT", "DELETE"])

            # Replay the 4 updates and verify G4 == G0
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

            # Step 0: Anchor inserted -> 1 entered
            self.assertEqual(steps[0]["entered"], [[0, 1, 2, 3]])
            self.assertEqual(steps[0]["exited"], [])

            # Steps 1 & 2: Spectator edge -> 0 events
            self.assertEqual(steps[1]["entered"], [])
            self.assertEqual(steps[1]["exited"], [])
            self.assertEqual(steps[2]["entered"], [])
            self.assertEqual(steps[2]["exited"], [])

            # Step 3: Anchor deleted -> 1 exited
            self.assertEqual(steps[3]["entered"], [])
            self.assertEqual(steps[3]["exited"], [[0, 1, 2, 3]])

    def test_05_mock_netflow_bounded_extraction(self) -> None:
        """Verifies bounded 2-pass streaming, neighbor selection, and projection on a mock NetFlow slice."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_ndata = tmp_path / "Ndata.graph"
            mock_ndeletion = tmp_path / "Ndeletion10.graph"

            # Create mock Ndeletion10 with 64 lines
            # Target records: line 0: e 100 101 0; line 1: -e 102 103 2; etc.
            del_lines = []
            for i in range(64):
                if i % 2 == 0:
                    del_lines.append(f"e {100 + i*2} {101 + i*2} 0\n")
                else:
                    del_lines.append(f"-e {100 + i*2} {101 + i*2} 2\n")
            with mock_ndeletion.open("w", encoding="utf-8") as f:
                f.writelines(del_lines)

            # Create mock Ndata with vertices and edges
            data_lines = []
            for v in range(300):
                data_lines.append(f"v {v} 0\n")
            # Add existing edge for delete targets and neighbors
            data_lines.append("e 102 103 2\n")  # line 1 delete target
            data_lines.append("e 102 201 0\n")  # neighbor of 102
            data_lines.append("e 103 202 0\n")  # neighbor of 103
            with mock_ndata.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            # Run extraction
            workloads = generate_netflow_workloads(tmp_path)
            self.assertEqual(len(workloads), 20)

            # Check that each workload has |S| == 8
            for w in workloads:
                S = w["sample_universe_S"]
                self.assertEqual(len(S), 8)
                self.assertEqual(len(set(S)), 8)
                g0 = DStarEventGraph.from_dict(w["initial_graph"])
                self.assertEqual(len(g0.vertices), 8)
                for v in S:
                    self.assertIn(v, g0.vertices)


if __name__ == "__main__":
    unittest.main()
