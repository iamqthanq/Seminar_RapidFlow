#!/usr/bin/env python3
"""
tests/test_apx04_preflight.py

Comprehensive test suite for APX-04 Gate A3-00 (Round 2 Adjudicated):
- Adversarial query parser validation (simple graphs, no self-loops, no duplicate edges, connectivity)
- Adversarial stream parser validation (edge-only, no vertex updates, no self-loops, syntax)
- Stream provenance & cryptographic source lineage verification
- Read-only, bounded-memory sequential update validity on initial G0
- Strict manifest schema validation (strict types, no bool eps, valid enums, pilot stage limits)
- Resource policy and execution readiness verification
- Direct APX-02 Oracle scientific tests:
  * Hub degree lower bound verification
  * Boundary-cut identity verification
  * Mandatory internal chord counterexample (deletion triggers EXITED via connectivity)
  * Dynamic threshold event non-monotonicity
  * Near-only vs exact non-induced comparator verification
  * Subgraph sampling drift verification
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.apx02_oracle import (
    ConnectivityMode,
    DeviationScore,
    DynamicOracle,
    SimpleGraph,
    Update,
    evaluate_mapping,
    find_approximate_matches,
)
from scripts.apx04_preflight import (
    PreflightChecker,
    compute_sha256,
    get_system_resources,
    is_strict_int,
    parse_query_file,
    parse_stream_prefix_file,
)


class TestAPX04ManifestAndFiles(unittest.TestCase):
    """Verifies workload manifest schema, integrity, and basic file validation."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.manifest_path = self.repo_root / "configs" / "apx04_workload_manifest.json"

    def test_canonical_manifest_passes_preflight(self) -> None:
        """The checked-in workload manifest must pass all preflight audits cleanly."""
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=False,
        )
        passed = checker.run_all()
        self.assertTrue(passed, f"Preflight failed with errors: {checker.errors}")
        self.assertEqual(len(checker.errors), 0)
        self.assertEqual(checker.audit_log["status"], "PASS")
        self.assertIn(checker.audit_log["audit_verdict"], ("AUDIT_PASS", "AUDIT_PASS_WITH_WARNINGS"))
        self.assertEqual(checker.audit_log["execution_readiness"], "SAFETY_BLOCKED")
        self.assertEqual(checker.audit_log["memory_enforcement"], "NOT_VERIFIED")
        self.assertEqual(checker.audit_log["execution_authorization"], False)
        self.assertEqual(checker.audit_log["initial_safe_pilot_count"], 3)
        self.assertEqual(checker.audit_log["pilot_cases_count"], 10)

    def test_missing_manifest_fails_closed(self) -> None:
        """Non-existent manifest path must fail closed."""
        checker = PreflightChecker(
            manifest_path=self.repo_root / "non_existent_manifest.json",
            repo_root=self.repo_root,
        )
        passed = checker.run_all()
        self.assertFalse(passed)
        self.assertTrue(any("not found" in e.lower() for e in checker.errors))

    def test_execution_authorization_safety_lock(self) -> None:
        """Setting execution_authorization=True in Gate A3-00 must fail validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["execution_authorization"] = True
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(
                manifest_path=tmp_manifest_path,
                repo_root=self.repo_root,
            )
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("safety violation" in e.lower() for e in checker.errors))

    def test_invalid_status_rejected(self) -> None:
        """Manifest status other than DRAFT_PENDING_USER_APPROVAL must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = "APPROVED_READY_TO_RUN"
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(
                manifest_path=tmp_manifest_path,
                repo_root=self.repo_root,
            )
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("status must be" in e.lower() for e in checker.errors))


class TestQueryValidationAdversarial(unittest.TestCase):
    """Adversarial negative tests for query file parsing and validation."""

    def test_query_without_header_passes(self) -> None:
        """Valid queries may contain only 'v' and 'e' records; 't # 1' header is not required."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 1 0\ne 0 1 0\n")
            f_path = Path(f.name)

        try:
            parsed = parse_query_file(f_path)
            self.assertEqual(parsed["num_vertices"], 2)
            self.assertEqual(parsed["num_edges"], 1)
            self.assertTrue(parsed["is_connected"])
        finally:
            f_path.unlink()

    def test_query_self_loop_rejected(self) -> None:
        """Self-loops (e u u label) must be strictly rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 1 0\ne 0 0 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_query_file(f_path)
            self.assertIn("self-loop", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_query_duplicate_undirected_edge_rejected(self) -> None:
        """Duplicate undirected edges (both (u, v) and (v, u)) must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 1 0\ne 0 1 0\ne 1 0 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_query_file(f_path)
            self.assertIn("duplicate undirected edge", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_query_conflicting_vertex_label_rejected(self) -> None:
        """Conflicting vertex label redefinition for the same vertex must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 0 1\nv 1 0\ne 0 1 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_query_file(f_path)
            self.assertIn("conflicting vertex label", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_query_duplicate_vertex_declaration_rejected(self) -> None:
        """Duplicate vertex declaration with the same label must also be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 0 0\nv 1 0\ne 0 1 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_query_file(f_path)
            self.assertIn("duplicate vertex declaration", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_query_edge_undeclared_vertex_rejected(self) -> None:
        """Edge records referencing undeclared vertex IDs must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 1 0\ne 0 2 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_query_file(f_path)
            self.assertIn("undeclared vertex", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_query_negative_vertex_id_rejected(self) -> None:
        """Negative vertex ID in query must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v -1 0\nv 0 0\ne -1 0 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_query_file(f_path)
            self.assertIn("negative vertex id", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_query_disconnected_rejected(self) -> None:
        """Disconnected query graph must fail the connectivity check."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 0 0\nv 1 0\nv 2 0\nv 3 0\ne 0 1 0\ne 2 3 0\n")
            f_path = Path(f.name)

        try:
            parsed = parse_query_file(f_path)
            self.assertFalse(parsed["is_connected"])
        finally:
            f_path.unlink()


class TestStreamValidationAdversarial(unittest.TestCase):
    """Adversarial negative tests for stream prefix file parsing."""

    def test_stream_vertex_update_strictly_forbidden(self) -> None:
        """Vertex update (v 7 0) is forbidden in Model C (fixed vertex universe)."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("v 7 0\ne 10 20 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_stream_prefix_file(f_path)
            self.assertIn("vertex update/declaration", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_stream_self_loop_rejected(self) -> None:
        """Self-loop in stream update must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("e 10 10 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_stream_prefix_file(f_path)
            self.assertIn("self-loop", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_stream_negative_vertex_id_rejected(self) -> None:
        """Negative vertex ID in stream update must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("e -1 2 0\n")
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_stream_prefix_file(f_path)
            self.assertIn("negative vertex id", str(ctx.exception).lower())
        finally:
            f_path.unlink()

    def test_stream_malformed_tokens_rejected(self) -> None:
        """Malformed stream records with incorrect token counts must be rejected."""
        with tempfile.NamedTemporaryFile("w", suffix=".graph", delete=False) as f:
            f.write("e 10 20\n")  # Missing label
            f_path = Path(f.name)

        try:
            with self.assertRaises(ValueError):
                parse_stream_prefix_file(f_path)
        finally:
            f_path.unlink()


class TestStreamProvenanceAndLineage(unittest.TestCase):
    """Verifies that preflight validates source stream SHA and prefix lineage."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.manifest_path = self.repo_root / "configs" / "apx04_workload_manifest.json"

    def test_missing_source_stream_detected(self) -> None:
        """Non-existent declared source_stream must trigger preflight failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["streams"]["insert_p10"]["source_stream"] = "data/streams/non_existent.graph"
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("not found" in e.lower() for e in checker.errors))

    def test_tampered_source_hash_detected(self) -> None:
        """Tampered source_stream_sha256 must be detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["streams"]["insert_p10"]["source_stream_sha256"] = "f" * 64
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("source_stream sha-256 mismatch" in e.lower() for e in checker.errors))

    def test_tampered_prefix_lineage_detected(self) -> None:
        """Fabricated/reordered prefix records differing from source must fail lineage check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake source stream with 10 records
            src_file = Path(tmpdir) / "fake_src.graph"
            with open(src_file, "w") as f:
                for i in range(10):
                    f.write(f"e {i} {i+1} 0\n")
            src_hash = compute_sha256(src_file)

            # Create a tampered prefix (swapped lines 1 and 2)
            prefix_file = Path(tmpdir) / "fake_prefix.graph"
            with open(prefix_file, "w") as f:
                f.write("e 1 2 0\n")  # Out of order
                f.write("e 0 1 0\n")
            prefix_hash = compute_sha256(prefix_file)

            manifest_data = {
                "schema_version": "1.1.0",
                "document_type": "apx04_workload_manifest",
                "status": "DRAFT_PENDING_USER_APPROVAL",
                "execution_authorization": False,
                "data_graph": {
                    "name": "Dummy",
                    "path": str(src_file.relative_to(tmpdir)),
                    "sha256": src_hash,
                    "vertices": 100,
                },
                "queries": {},
                "streams": {
                    "test_stream": {
                        "path": str(prefix_file.relative_to(tmpdir)),
                        "source_stream": str(src_file.relative_to(tmpdir)),
                        "source_stream_sha256": src_hash,
                        "prefix_length": 2,
                        "sha256": prefix_hash,
                    }
                },
                "proposed_pilot_cases_a3_01": [],
                "exclusions": [],
            }
            tmp_manifest = Path(tmpdir) / "manifest.json"
            with open(tmp_manifest, "w") as f:
                json.dump(manifest_data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest, repo_root=Path(tmpdir))
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("lineage mismatch" in e.lower() for e in checker.errors))


class TestManifestValidationStrict(unittest.TestCase):
    """Verifies strict manifest schema validation (types, enums, near-only rules)."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.manifest_path = self.repo_root / "configs" / "apx04_workload_manifest.json"

    def test_strict_int_rejects_bool(self) -> None:
        """Helper is_strict_int correctly rejects True/False."""
        self.assertTrue(is_strict_int(0))
        self.assertTrue(is_strict_int(10))
        self.assertFalse(is_strict_int(True))
        self.assertFalse(is_strict_int(False))
        self.assertFalse(is_strict_int("10"))
        self.assertFalse(is_strict_int(1.5))

    def test_manifest_rejects_bool_eps_m(self) -> None:
        """Manifest validator must reject eps_m: true (boolean instead of int)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["proposed_pilot_cases_a3_01"][0]["eps_m"] = True
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("invalid eps_m" in e.lower() for e in checker.errors))

    def test_manifest_rejects_near_only_with_zero_eps_m(self) -> None:
        """Workload category NEAR_ONLY_MATCH strictly requires eps_m >= 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Set eps_m = 0 on NEAR_ONLY_MATCH
            data["proposed_pilot_cases_a3_01"][1]["eps_m"] = 0
            data["proposed_pilot_cases_a3_01"][1]["workload_category"] = "NEAR_ONLY_MATCH"
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("invalid near-only classification" in e.lower() for e in checker.errors))

    def test_manifest_rejects_eps_m_2_on_small_query(self) -> None:
        """Main protocol restriction: eps_m=2 is rejected on queries with < 6 edges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Q05 star4 has 3 edges (< 6); set eps_m = 2
            data["proposed_pilot_cases_a3_01"][0]["eps_m"] = 2
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("violates main benchmark threshold constraint" in e.lower() for e in checker.errors))

    def test_manifest_rejects_query_source_mismatch(self) -> None:
        """pilot case query_source must match declared query category."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Q05 is SYNTHETIC_PROBE; declare AUTHOR_NEWSP
            data["proposed_pilot_cases_a3_01"][0]["query_source"] = "AUTHOR_NEWSP"
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("query_source mismatch" in e.lower() for e in checker.errors))

    def test_manifest_rejects_excessive_initial_safe_pilots(self) -> None:
        """INITIAL_SAFE_PILOT stage allows maximum 3 workloads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_manifest_path = Path(tmpdir) / "manifest.json"
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Mark a 4th case as INITIAL_SAFE_PILOT
            data["proposed_pilot_cases_a3_01"][3]["pilot_stage"] = "INITIAL_SAFE_PILOT"
            with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            checker = PreflightChecker(manifest_path=tmp_manifest_path, repo_root=self.repo_root)
            passed = checker.run_all()
            self.assertFalse(passed)
            self.assertTrue(any("too many cases declared in initial_safe_pilot" in e.lower() for e in checker.errors))


class TestSystemResourceAudits(unittest.TestCase):
    """Verifies host resource checks and explicit separation of audit vs execution readiness."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.manifest_path = self.repo_root / "configs" / "apx04_workload_manifest.json"

    @patch("scripts.apx04_preflight.get_system_resources")
    def test_adequate_resources_pass_as_audit_pass(self, mock_res) -> None:
        """Adequate RAM (8 GiB) yields AUDIT_PASS, but execution readiness is SAFETY_BLOCKED."""
        mock_res.return_value = {
            "ram_total_gib": 16.0,
            "ram_available_gib": 8.0,
            "disk_total_gib": 200.0,
            "disk_free_gib": 50.0,
        }
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=True,
        )
        passed = checker.run_all()
        self.assertTrue(passed)
        self.assertEqual(checker.audit_log["audit_verdict"], "AUDIT_PASS")
        self.assertEqual(checker.audit_log["execution_readiness"], "SAFETY_BLOCKED")
        self.assertEqual(checker.audit_log["memory_enforcement"], "NOT_VERIFIED")

    @patch("scripts.apx04_preflight.get_system_resources")
    def test_low_ram_unenforced_yields_audit_pass_with_warnings(self, mock_res) -> None:
        """When enforce_ram=False and RAM < 4.0 GiB, audit passes with warnings, readiness SAFETY_BLOCKED."""
        mock_res.return_value = {
            "ram_total_gib": 11.5,
            "ram_available_gib": 3.4,
            "disk_total_gib": 150.0,
            "disk_free_gib": 80.0,
        }
        checker = PreflightChecker(
            manifest_path=self.manifest_path,
            repo_root=self.repo_root,
            min_ram_gib=4.0,
            min_disk_gib=10.0,
            enforce_ram=False,
        )
        passed = checker.run_all()
        self.assertTrue(passed)
        self.assertEqual(checker.audit_log["audit_verdict"], "AUDIT_PASS_WITH_WARNINGS")
        self.assertEqual(checker.audit_log["execution_readiness"], "SAFETY_BLOCKED")


class TestOracleScientificVerification(unittest.TestCase):
    """
    Direct scientific verification calling the verified APX-02 Ground-Truth Oracle.
    Covers Theorems 1, 2, 3, internal chord counterexample, and near-only comparator.
    """

    def test_oracle_hub_degree_lower_bound(self) -> None:
        """
        Theorem 1: x_out(f) >= max_{v in S_f} max(0, deg_G(v) - (k-1)).
        Directly verified using APX-02 find_approximate_matches().
        """
        # Query: Star K_{1,3} (k=4)
        Q = SimpleGraph()
        for v in range(4):
            Q.add_vertex(v, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(0, 2, 0)
        Q.add_edge(0, 3, 0)

        # Data graph G: center 0 with degree d=7 (connected to 1..7)
        G = SimpleGraph()
        for v in range(8):
            G.add_vertex(v, 0)
        for leaf in range(1, 8):
            G.add_edge(0, leaf, 0)

        # Evaluate via APX-02 Oracle
        matches = find_approximate_matches(Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertGreater(len(matches), 0)

        k = 4
        deg_center = 7
        theoretical_lower_bound = deg_center - (k - 1)  # 7 - 3 = 4

        for mapping_tuple, eval_res in matches.items():
            self.assertTrue(eval_res.accepted)
            self.assertIn(0, mapping_tuple)  # Center node must map to hub 0
            self.assertGreaterEqual(eval_res.score.x_out, theoretical_lower_bound)
            self.assertEqual(eval_res.score.x_out, 4)

    def test_oracle_boundary_cut_identity(self) -> None:
        """
        Theorem 2: x_out(S) = sum_{v in S} deg_G(v) - 2 * |E_G[S]|.
        Directly verified using APX-02 evaluate_mapping().
        """
        Q = SimpleGraph()
        for v in range(3):
            Q.add_vertex(v, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        # Graph with internal chord {0, 2} and external boundary edge {1, 3}
        G = SimpleGraph()
        for v in range(4):
            G.add_vertex(v, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(0, 2, 0)  # Internal chord
        G.add_edge(1, 3, 0)  # External edge from S={0,1,2} to {3}

        # Candidate mapping: (0, 1, 2)
        eval_res = evaluate_mapping(Q, G, (0, 1, 2), eps_m=1, eps_x=None, mode=ConnectivityMode.STRICT)
        # S = {0, 1, 2}. deg(0)=2, deg(1)=3, deg(2)=2. sum = 7.
        # Internal edges: {0,1}, {1,2}, {0,2} = 3.
        # x_out = 7 - 2(3) = 1.
        self.assertEqual(eval_res.score.x_out, 1)

    def test_oracle_mandatory_internal_chord_counterexample(self) -> None:
        """
        Mandatory Internal Chord Counterexample:
        Query: Path 0-1-2.
        Initial Graph G: edges {0, 1} and chord {0, 2}. Edge {1, 2} is missing.
        Config: eps_m=1, eps_x=None, STRICT.
        Mapping (0, 1, 2) is accepted initially (m=1, x_in=1, x_out=0, connected=True).
        When chord {0, 2} is deleted, the induced subgraph becomes disconnected,
        causing mapping (0, 1, 2) to trigger an EXITED event, even though x_out remains 0.
        """
        Q = SimpleGraph()
        for v in [0, 1, 2]:
            Q.add_vertex(v, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        G = SimpleGraph()
        for v in [0, 1, 2]:
            G.add_vertex(v, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(0, 2, 0)  # Chord

        oracle = DynamicOracle(Q, G, eps_m=1, eps_x=None, mode=ConnectivityMode.STRICT)
        # Verify initial acceptance
        self.assertIn((0, 1, 2), oracle.current_matches)
        initial_score = oracle.current_matches[(0, 1, 2)].score
        self.assertEqual(initial_score.m, 1)
        self.assertEqual(initial_score.x_in, 1)
        self.assertEqual(initial_score.x_out, 0)
        self.assertTrue(initial_score.connected)

        # Apply update: delete internal chord {0, 2}
        delta = oracle.apply_update(Update(op="-", u=0, v=2, label=0))

        # Crucial assertion: (0, 1, 2) MUST be in delta.exited
        self.assertIn((0, 1, 2), delta.exited)
        self.assertNotIn((0, 1, 2), oracle.current_matches)

        # After deletion, evaluate mapping directly to prove x_out is still 0
        after_eval = evaluate_mapping(Q, oracle.G, (0, 1, 2), eps_m=1, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertEqual(after_eval.score.x_out, 0)
        self.assertFalse(after_eval.score.connected)  # Disconnected!
        self.assertFalse(after_eval.accepted)

    def test_oracle_near_only_vs_exact_comparator(self) -> None:
        """
        Near-only definition: M_near-only = M_approx(eps_m, eps_x) \\ M_exact(0, None).
        - When eps_m = 0, M_near-only is strictly empty.
        - When eps_m >= 1, any mapping in M_near-only strictly has m(f) >= 1.
        """
        # Query 1: Path 0-1-2 (k=3, |E|=2)
        Q = SimpleGraph()
        for v in [0, 1, 2]:
            Q.add_vertex(v, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        G = SimpleGraph()
        for v in range(4):
            G.add_vertex(v, 0)
        # Graph with path 0-1-2 and an extra leaf 3 attached to 1 (making deg(1)=3)
        G.add_edge(0, 1, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(1, 3, 0)

        # Baseline exact non-induced matches (eps_m=0, eps_x=None)
        m_exact = find_approximate_matches(Q, G, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)

        # Clutter-filtered matches with eps_m=0, eps_x=2
        m_clutter = find_approximate_matches(Q, G, eps_m=0, eps_x=2, mode=ConnectivityMode.STRICT)

        # When eps_m=0: near-only MUST be empty set
        near_only_zero = set(m_clutter.keys()) - set(m_exact.keys())
        self.assertEqual(len(near_only_zero), 0)

        # Query 2: Triangle K3 (k=3, |E|=3: edges {0,1}, {1,2}, {0,2})
        Q_tri = SimpleGraph()
        for v in [0, 1, 2]:
            Q_tri.add_vertex(v, 0)
        Q_tri.add_edge(0, 1, 0)
        Q_tri.add_edge(1, 2, 0)
        Q_tri.add_edge(0, 2, 0)

        # Data: Path P3 on vertices 0-1-2 (edges {0,1}, {1,2}; edge {0,2} is missing)
        G_path = SimpleGraph()
        for v in [0, 1, 2]:
            G_path.add_vertex(v, 0)
        G_path.add_edge(0, 1, 0)
        G_path.add_edge(1, 2, 0)

        # Exact matching on Triangle Q_tri against Path G_path yields 0 matches
        m_exact_tri = find_approximate_matches(Q_tri, G_path, eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertEqual(len(m_exact_tri), 0)

        # Approximate matching under eps_m=1 on Triangle Q_tri against Path G_path (connected=True)
        m_approx_tri = find_approximate_matches(Q_tri, G_path, eps_m=1, eps_x=None, mode=ConnectivityMode.STRICT)
        self.assertGreater(len(m_approx_tri), 0)

        # Near-only match set
        near_only_tri = set(m_approx_tri.keys()) - set(m_exact_tri.keys())
        self.assertEqual(len(near_only_tri), len(m_approx_tri))
        for m_tuple in near_only_tri:
            eval_res = m_approx_tri[m_tuple]
            self.assertEqual(eval_res.score.m, 1)  # All near-only matches strictly have m(f) >= 1
            self.assertTrue(eval_res.score.connected)

    def test_oracle_subgraph_sampling_drift(self) -> None:
        """
        Theorem 3: x_out^{G'}(S) <= x_out^G(S) for any induced subgraph G' subset of G.
        Directly verified using APX-02 evaluate_mapping().
        """
        Q = SimpleGraph()
        for v in [0, 1, 2]:
            Q.add_vertex(v, 0)
        Q.add_edge(0, 1, 0)
        Q.add_edge(1, 2, 0)

        # Full graph G: vertices 0, 1, 2, 3. Edges (0,1), (1,2), external (2,3)
        G = SimpleGraph()
        for v in range(4):
            G.add_vertex(v, 0)
        G.add_edge(0, 1, 0)
        G.add_edge(1, 2, 0)
        G.add_edge(2, 3, 0)  # External cut edge

        # Sampled subgraph G' omitting external node 3
        G_prime = SimpleGraph()
        for v in range(3):
            G_prime.add_vertex(v, 0)
        G_prime.add_edge(0, 1, 0)
        G_prime.add_edge(1, 2, 0)

        eval_full = evaluate_mapping(Q, G, (0, 1, 2), eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)
        eval_sampled = evaluate_mapping(Q, G_prime, (0, 1, 2), eps_m=0, eps_x=None, mode=ConnectivityMode.STRICT)

        self.assertEqual(eval_full.score.x_out, 1)
        self.assertEqual(eval_sampled.score.x_out, 0)
        self.assertLessEqual(eval_sampled.score.x_out, eval_full.score.x_out)


if __name__ == "__main__":
    unittest.main()
