"""Exact tuple, lifecycle and configuration regressions for P4b-1B."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_rapidflow_apx_engines as runner
from scripts.dstar_oracle import DStarDynamicOracle, DStarGraph, DStarUpdate
from test_rapidflow_apx_foundation_audit import independent_snapshots, edge, update, vertex


FIXTURES = runner.REPO_ROOT / "tests/fixtures/rapidflow_apx_p4b_residual"
BINARY = runner.REPO_ROOT / "tests/native/rapidflow_apx_engines/build/rapidflow_apx_native"


class TestResidualNlf(unittest.TestCase):
    @staticmethod
    def witness(name):
        return json.loads((FIXTURES / f"residual-{name}.json").read_bytes())

    def native(self, workload, *options):
        with tempfile.TemporaryDirectory() as folder:
            inp, out = Path(folder) / "in.json", Path(folder) / "out.json"
            inp.write_text(json.dumps(workload), encoding="utf-8")
            result = subprocess.run(
                [str(BINARY), "--engine", "m2", "--nlf-mode", "residual",
                 "--workload", str(inp), "--output", str(out), *options],
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertTrue(out.is_file(), result.stderr)
            return result.returncode, json.loads(out.read_text())

    def compare_exact(self, workload, event_count):
        query, graph, updates, k = (workload[x] for x in ("query", "initial_graph", "updates", "k"))
        expected = independent_snapshots(query, graph, updates, k)
        self.assertEqual([list(map(len, step)) for step in expected], [[event_count, 0], [0, event_count]])
        if event_count == 8 and len(query["edges"]) == 5:
            self.assertIn([0, 1, 2, 3], expected[0][0])
        oracle = DStarDynamicOracle(DStarGraph.from_dict(query), DStarGraph.from_dict(graph), k)
        for i, step in enumerate(updates):
            delta = oracle.apply_update(DStarUpdate(
                "INSERT" if step["op"] == "+" else "DELETE", step["u"], step["v"], step["label"]))
            self.assertEqual([sorted(map(list, delta.entered)), sorted(map(list, delta.exited))], expected[i])
        for engine, mode in (("m1", "none"), ("m2", "none"), ("m2", "static"), ("m2", "residual")):
            with self.subTest(engine=engine, mode=mode, case=workload.get("case_id")):
                native, _, accepted = runner.run_workload_on_binary(BINARY, engine, workload, nlf_mode=mode)
                self.assertTrue(accepted, native)
                self.assertEqual(native["effective_nlf_mode"], mode)
                self.assertEqual([[sorted(s["entered_tuples"]), sorted(s["exited_tuples"])] for s in native["steps"]],
                                 expected)
                self.assertEqual(native["counters"]["discarded_emissions"], 0)
                if mode != "residual":
                    self.assertTrue(all(native["counters"][field] == 0 for field in runner.RESIDUAL_COUNTER_FIELDS))

    def test_residual_numeric_and_slot_mapping(self):
        proc = subprocess.run([str(BINARY), "--test-p4b-residual", "numeric"], capture_output=True,
                              text=True, timeout=30, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Residual numeric, slot and A2/A3 assertions passed", proc.stdout)

    def test_exact_tuples_for_a1_chord_and_audit_witnesses(self):
        for name, count in (("strict", 0), ("deferred", 8)):
            w = self.witness(name)
            w["case_id"] = "audit_" + name
            w["updates"].append({**w["updates"][0], "op": "-"})
            self.compare_exact(w, count)

        verts = [vertex(i, 0) for i in range(4)]
        ring = [(0, 1), (0, 2), (1, 3), (2, 3)]
        query = {"vertices": verts, "edges": [edge(u, v, 0) for u, v in [*ring, (1, 2)]]}
        for name, baseline, (u, v), count in (
            ("a1", ring[:-1], ring[-1], 8), ("chord", ring, (1, 2), 16),
        ):
            self.compare_exact({
                "case_id": name, "query": query, "k": 1,
                "initial_graph": {"vertices": verts, "edges": [edge(a, b, 0) for a, b in baseline]},
                "updates": [update("+", u, v, 0), update("-", u, v, 0)],
            }, count)

    def test_exact_mode_legacy_traversal(self):
        lock = json.loads(runner.STREAM_56_LOCK_PATH.read_text())
        excluded = set(runner.RESIDUAL_COUNTER_FIELDS) | {"nlf_static_checks", "nlf_static_pruned"}
        for item in lock["streams"]:
            if item["k"] != 0:
                continue
            w = runner.generate_stream_workload(
                item["seed"], item["q_type"], item["k"], item["lambda"], item["label_mode"])
            static, _, static_ok = runner.run_workload_on_binary(BINARY, "m2", w, nlf_mode="static")
            residual, _, residual_ok = runner.run_workload_on_binary(BINARY, "m2", w, nlf_mode="residual")
            self.assertTrue(static_ok and residual_ok, item["case_id"])
            self.assertEqual(len(static["steps"]), len(residual["steps"]))
            for left, right in [(static["counters"], residual["counters"])] + [
                (a["step_counters"], b["step_counters"]) for a, b in zip(static["steps"], residual["steps"])
            ]:
                self.assertEqual({k:v for k,v in left.items() if k not in excluded},
                                 {k:v for k,v in right.items() if k not in excluded})
                self.assertEqual(left["nlf_static_checks"], right["nlf_residual_checks"])
                self.assertEqual(left["nlf_static_pruned"], right["nlf_residual_pruned"])
            for a, b in zip(static["steps"], residual["steps"]):
                for field in ("entered_tuples", "exited_tuples", "graph_state", "codec_state"):
                    self.assertEqual(a[field], b[field], (item["case_id"], field))

    def test_label_reinsert_signature_identity(self):
        w = self.witness("deferred")
        w["case_id"] = "residual_label_reinsert"
        w["query"]["vertices"] = [vertex(i, "phố" if i % 2 == 0 else "0") for i in (3, 2, 1, 0)]
        w["initial_graph"]["vertices"] = w["query"]["vertices"]
        w["query"]["edges"][2]["label"] = "đường"
        w["initial_graph"]["edges"][0]["label"] = "đường"
        w["updates"] += [update("-", 1, 3, "đường"), update("+", 1, 3, "0"),
                         update("-", 1, 3, "0"), update("+", 1, 3, 0), update("-", 1, 3, 0),
                         update("+", 1, 3, "đường"), update("-", 0, 1, 0)]
        expected = independent_snapshots(w["query"], w["initial_graph"], w["updates"], w["k"])
        self.assertTrue(any(a or b for a, b in expected))
        result, _, ok = runner.run_workload_on_binary(BINARY, "m2", w, nlf_mode="residual")
        self.assertTrue(ok, result)
        self.assertEqual([[sorted(s["entered_tuples"]), sorted(s["exited_tuples"])] for s in result["steps"]],
                         expected)

    def test_configuration_metadata_matrix(self):
        unavailable = "REQUESTED_CONFIGURATION_UNAVAILABLE"
        for req, eff, status, reason, accepted in (
            ("none", "none", "SUCCESS", "", True),
            ("static", "static", "SUCCESS", "", True),
            ("residual", "residual", "SUCCESS", "", True),
            ("residual", "none", "SUCCESS", "", False),
            ("residual", "static", "SUCCESS", "", False),
            ("static", "residual", "SUCCESS", "", False),
            ("none", "residual", "SUCCESS", "", False),
            ("residual", "none", unavailable, "NLF_INDEX_MEMORY_LIMIT", False),
            ("residual", "static", unavailable, "NLF_INDEX_MEMORY_LIMIT", False),
            ("residual", "residual", "SUCCESS", "NLF_INDEX_MEMORY_LIMIT", False),
            ("residual", "residual", "UNKNOWN", "", False),
        ):
            for is_success in (False, True):
                with self.subTest(requested=req, effective=eff, status=status, successful=is_success):
                    result = {
                        "configuration_initialized": True, "requested_nlf_mode": req,
                        "effective_nlf_mode": eff, "configuration_status": status,
                        "nlf_index_logical_bytes": 16 if eff != "none" else 0,
                        "nlf_memory_limit_bytes": 1024, "fallback_reason": reason,
                    }
                    ok, _, meta = runner.validate_nlf_configuration("m2", req, result, is_success)
                    self.assertEqual(ok, accepted)
                    self.assertEqual(meta["eligible_for_requested_variant"], accepted and is_success)
        early = {"status": "FAIL", "configuration_initialized": False, "error_category": "DOMAIN_ERROR", "exit_code": 3}
        self.assertTrue(runner.validate_nlf_configuration("m2", "residual", early, False)[0])

    def test_actual_native_substitution_rejected(self):
        run = runner.execute_contained_subprocess
        for requested, effective in (("residual", "none"), ("residual", "static"), ("static", "residual")):
            for negative in (False, True):
                with self.subTest(requested=requested, effective=effective, negative=negative):
                    w = self.witness("deferred")
                    if negative:
                        w["updates"][0]["v"] = 100
                    def substitute(cmd, **kwargs):
                        args = list(cmd)
                        args[args.index("--nlf-mode") + 1] = effective
                        return run(args, **kwargs)
                    with patch.object(runner, "execute_contained_subprocess", side_effect=substitute):
                        result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", w, nlf_mode=requested)
                    self.assertFalse(accepted)
                    self.assertFalse(result["configuration_valid"])
                    self.assertFalse(result["eligible_for_requested_variant"])
                    self.assertEqual(result["effective_nlf_mode"], effective)
                    self.assertIn("Requested mode mismatch", result["configuration_error"])

    def test_native_cli_and_python_selection(self):
        for args in (("--nlf-mode", "Residual"), ("--nlf-mode", "residual", "--nlf-mode", "none")):
            proc = subprocess.run([str(BINARY), *args], capture_output=True, check=False)
            self.assertEqual(proc.returncode, 1)
            self.assertIn(b"--nlf-mode", proc.stderr)
        script = runner.REPO_ROOT / "scripts/run_rapidflow_apx_engines.py"
        with tempfile.TemporaryDirectory() as tmp:
            for engine in ("m1", "all"):
                proc = subprocess.run([sys.executable, str(script), "--engine", engine, "--nlf-mode",
                                       "residual", "--output", str(Path(tmp) / "rejection.json")],
                                      capture_output=True, check=False)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(b"residual", proc.stderr)
            mock = self.witness("strict")
            inp = Path(tmp) / "input.json"
            inp.write_text(json.dumps(mock))
            proc = subprocess.run([str(BINARY), "--engine", "m1", "--nlf-mode", "residual",
                                   "--workload", str(inp), "--output", str(Path(tmp) / "out.json")],
                                  capture_output=True, check=False)
            self.assertEqual(proc.returncode, 1)
            self.assertIn(b"not supported for M1", proc.stderr)

    def test_real_memory_fallback(self):
        w = self.witness("strict")
        w["query"] = {"vertices": [vertex(i, i) for i in range(11)], "edges": [edge(0, i, 0) for i in range(1, 11)]}
        w["initial_graph"] = {"vertices": [vertex(i, 0) for i in range(30000)], "edges": []}
        w["k"] = 0
        code, result = self.native(w, "--max-nlf-memory-mib", "1")
        self.assertEqual(code, 0)
        self.assertEqual(result["requested_nlf_mode"], "residual")
        self.assertEqual(result["effective_nlf_mode"], "none")
        self.assertEqual(result["fallback_reason"], "NLF_INDEX_MEMORY_LIMIT")
        self.assertEqual(result["nlf_index_logical_bytes"], 0)
        self.assertTrue(all(result["counters"][field] == 0 for field in runner.RESIDUAL_COUNTER_FIELDS))
        ok, reason, meta = runner.validate_nlf_configuration("m2", "residual", result, True)
        self.assertFalse(ok)
        self.assertIn("CONFIG_UNAVAILABLE", reason)
        self.assertFalse(meta["eligible_for_requested_variant"])

    def test_residual_search_faults_and_recovery(self):
        faults = (
            "AFTER_VERTEX_ASSIGNMENT", "AFTER_MISSING_COMMITMENT", "AFTER_NLF_PREPARE",
            "AFTER_GRAPH_BEFORE_NLF", "AFTER_FIRST_NLF_CELL", "BEFORE_PUBLISH", "DURING_CALLBACK",
        )
        for op in ("+", "-"):
            w = self.witness("deferred")
            if op == "-":
                w["initial_graph"]["edges"].append(edge(0, 1, 0))
            for fault in faults:
                with self.subTest(operation=op, fault=fault):
                    w["updates"] = [update(op, 0, 1, 0, fault=fault), update(op, 0, 1, 0)]
                    code, result = self.native(w, "--continue-on-error")
                    self.assertEqual(code, 6)
                    self.assertEqual(result["effective_nlf_mode"], "residual")
                    first, second = result["steps"]
                    self.assertEqual(first["status"], "FAULT_INJECTED")
                    self.assertEqual(first["commit_status"], "NOT_COMMITTED")
                    self.assertIs(first["nlf_full_cell_reconstruction"], True)
                    self.assertEqual(first["entered_tuples"] + first["exited_tuples"], [])
                    self.assertEqual(first["graph_state"]["edges_count"], 2 + (op == "-"))
                    self.assertEqual(first["codec_state"], second["codec_state"])
                    self.assertEqual(second["commit_status"], "COMMITTED")
                    good = independent_snapshots(w["query"], w["initial_graph"], [w["updates"][1]], w["k"])[0]
                    self.assertEqual([sorted(second["entered_tuples"]), sorted(second["exited_tuples"])], good)
                    c = second["step_counters"]
                    self.assertEqual(c["nlf_residual_missing_commits"], c["nlf_residual_missing_undos"])

    def test_actual_residual_work_budget_abort(self):
        for op in ("+", "-"):
            with self.subTest(op=op):
                w = self.witness("deferred")
                if op == "-":
                    w["initial_graph"]["edges"].append(edge(0, 1, 0))
                # An irrelevant-label update is valid at the same stream work cap.
                w["updates"] = [update(op, 0, 1, 0), update("+", 0, 3, "irrelevant")]
                code, result = self.native(w, "--max-work", "9", "--continue-on-error")
                self.assertNotEqual(code, 0)
                self.assertEqual(result["error_category"], "WORK_BUDGET_EXCEEDED")
                first, second = result["steps"]
                self.assertEqual(first["commit_status"], "NOT_COMMITTED")
                self.assertEqual(first["step_counters"]["work_units"], 10)
                self.assertEqual(first["entered_tuples"] + first["exited_tuples"], [])
                self.assertIs(first["nlf_full_cell_reconstruction"], True)
                self.assertEqual(first["graph_state"]["edges_count"], 2 + (op == "-"))
                self.assertEqual(second["commit_status"], "COMMITTED")
