"""Supplemental native input and transaction reporting regressions for Issue #38."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.dstar_oracle import DStarDynamicOracle, DStarGraph, DStarUpdate
from scripts import run_rapidflow_apx_engines as runner


FIXTURES = runner.REPO_ROOT / "tests/fixtures/rapidflow_apx_foundation_audit"
MODES = (("m1", "none"), ("m2", "none"), ("m2", "static"))


def vertex(id_, label):
    return {"id": id_, "label": label}


def edge(u, v, label):
    return {"u": u, "v": v, "label": label}


def update(op, u, v, label, **more):
    return {"op": op, **edge(u, v, label), **more}


def independent_snapshots(query, graph, updates, k):
    """Enumerate typed permutations and use explicit matched-edge connectivity."""
    vertices = {item["id"]: item["label"] for item in graph["vertices"]}
    q_vertices = sorted(query["vertices"], key=lambda item: item["id"])
    q_positions = {item["id"]: i for i, item in enumerate(q_vertices)}
    pairs = {tuple(sorted((e["u"], e["v"]))): e["label"] for e in graph["edges"]}

    def snapshot():
        matches = set()
        for mapped in itertools.permutations(vertices, len(q_vertices)):
            if any(vertices[v] != qv["label"] for qv, v in zip(q_vertices, mapped)):
                continue
            present = []
            for e in query["edges"]:
                a = q_positions[e["u"]]
                b = q_positions[e["v"]]
                if pairs.get(tuple(sorted((mapped[a], mapped[b])))) == e["label"]:
                    present.append((a, b))
            if len(query["edges"]) - len(present) > k:
                continue
            reachable = {0}
            for _ in mapped:
                reachable.update(v for (a, b) in present if {a, b} & reachable for v in (a, b))
            if len(reachable) == len(mapped):
                matches.add(mapped)
        return matches

    before = snapshot()
    result = []
    for step in updates:
        pair = tuple(sorted((step["u"], step["v"])))
        if step["op"] == "+":
            pairs[pair] = step["label"]
        else:
            del pairs[pair]
        after = snapshot()
        result.append([sorted(map(list, after - before)), sorted(map(list, before - after))])
        before = after
    return result


class FoundationAuditRemediation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary, _, build, ok = runner.build_native_binary(clean_build=False)
        if not ok:
            raise RuntimeError(f"Native build failed: {build}")
        cls.evidence_dir = os.environ.get("APX_FAR_EVIDENCE_DIR")

    def native(self, workload, engine, mode, *args, output_device=None):
        raw = workload if isinstance(workload, bytes) else json.dumps(workload).encode()
        with tempfile.TemporaryDirectory() as d:
            path, out = Path(d) / "input.json", Path(d) / "output.json"
            path.write_bytes(raw)
            cmd = [str(self.binary), "--engine", engine, "--nlf-mode", mode,
                   "--workload", str(path), "--output", str(output_device or out), *args]
            timing = Path(d) / "time"
            proc = subprocess.run(
                ["/usr/bin/time", "-f", "elapsed=%e rss_kib=%M exit=%x", "-o", str(timing), *cmd],
                capture_output=True, text=True, timeout=10, check=False,
            )
            result = json.loads(out.read_text()) if out.exists() else {}
            if self.evidence_dir:
                ledger = Path(self.evidence_dir)
                ledger.mkdir(parents=True, exist_ok=True)
                idx = len(list(ledger.glob("*.json")))
                (ledger / f"{idx:04}.json").write_text(json.dumps({
                    "test": self._testMethodName, "argv": cmd, "input_sha256": hashlib.sha256(raw).hexdigest(),
                    "exit": proc.returncode, "timing": timing.read_text().strip(),
                    "stdout": proc.stdout, "stderr": proc.stderr, "result": result,
                }, ensure_ascii=True))
                (ledger / f"{idx:04}.input").write_bytes(raw)
            return proc, result

    def assert_matching(self, raw, expected=None):
        workload = json.loads(raw)
        query, graph, updates = (workload[k] for k in ("query", "initial_graph", "updates"))
        oracle_expected = independent_snapshots(query, graph, updates, workload["k"])
        if expected is not None:
            self.assertEqual(oracle_expected, expected)
        m0 = DStarDynamicOracle(DStarGraph.from_dict(query), DStarGraph.from_dict(graph), workload["k"])
        for index, step in enumerate(updates):
            delta = m0.apply_update(DStarUpdate(
                "INSERT" if step["op"] == "+" else "DELETE", step["u"], step["v"], step["label"],
            ))
            self.assertEqual([sorted(map(list, delta.entered)), sorted(map(list, delta.exited))],
                             oracle_expected[index])
        for engine, mode in MODES:
            with self.subTest(engine=engine, mode=mode):
                proc, res = self.native(raw, engine, mode)
                self.assertEqual(proc.returncode, 0, (proc.stderr, res))
                self.assertEqual(res["effective_nlf_mode"], mode)
                self.assertEqual(
                    [[sorted(step["entered_tuples"]), sorted(step["exited_tuples"])] for step in res["steps"]],
                    oracle_expected,
                )
                self.assertEqual(res["execution"]["committed_steps"], len(updates))
                self.assertEqual(res["execution"]["delivered_steps"], len(updates))
        return oracle_expected

    def test_parser_unicode_matrix(self):
        proc = subprocess.run([str(self.binary.with_name("rapidflow_apx_json_strings"))],
                              capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("25 JSON string assertions passed", proc.stdout)

    def test_original_unicode_witnesses(self):
        for name, expectation in (
            ("typed-unicode-mismatch", [[[], []], [[], []]]),
            ("typed-unicode-equivalent", [[[[0, 1]], []], [[], [[0, 1]]]]),
        ):
            with self.subTest(name=name):
                self.assert_matching((FIXTURES / f"{name}.json").read_bytes(), expectation)

    def test_distinct_scalars_exact_type_and_embedded_nul(self):
        for q_label, g_label, expect_match in (
            ("á", "á", True), ("😀", "😁", False), ("?", "á", False),
            ("?", "?", True), ("A", "A", True), (0, "0", False),
            ("ắ", "a\u0306\u0301", False), ("a\0b", "a\0b", True),
        ):
            with self.subTest(query=q_label, data=g_label):
                w = {
                    "query": {"vertices": [vertex(0, q_label), vertex(1, "peer")], "edges": [edge(0, 1, 0)]},
                    "initial_graph": {"vertices": [vertex(0, g_label), vertex(1, "peer")], "edges": []},
                    "k": 0, "updates": [update("+", 0, 1, 0), update("-", 0, 1, 0)],
                }
                raw = json.dumps(w).encode()
                for label, escaped in (("?", b"\\u003f"), ("A", b"\\u0041")):
                    if q_label == label:
                        raw = raw.replace(json.dumps(label).encode(), b'"' + escaped + b'"', 1)
                events = [[0, 1]] if expect_match else []
                self.assert_matching(raw, [[events, []], [[], events]])
                if "\0" in str(q_label):
                    proc, output = self.native(raw, "m2", "static")
                    self.assertEqual(proc.returncode, 0)
                    self.assertEqual(output["steps"][0]["graph_state"]["vertices"][0]["label"], q_label)

    def test_unicode_edge_labels_initial_insert_delete_relabel(self):
        vertices = [vertex(0, "phố"), vertex(1, "đỏ"), vertex(2, "😀")]
        query = {"vertices": vertices, "edges": [edge(0, 1, "đường"), edge(1, 2, "é"), edge(0, 2, "😀")]}
        for initial_label in ("đường", "đương"):
            with self.subTest(initial_label=initial_label):
                workload = {
                    "query": query, "k": 1,
                    "initial_graph": {"vertices": vertices,
                                      "edges": [edge(0, 1, initial_label), edge(1, 2, "é")]},
                    "updates": [
                        update("+", 0, 2, "😀"), update("-", 0, 2, "😀"),
                        update("-", 0, 1, initial_label), update("+", 0, 1, "đương"),
                        update("+", 0, 2, "😀"), update("-", 0, 1, "đương"),
                        update("+", 0, 1, "đường"), update("-", 0, 2, "😀"),
                        update("+", 0, 2, "😁"), update("-", 0, 2, "😁"),
                    ],
                }
                # Alternate native UTF-8 and JSON escape spellings node-by-node;
                # json.dumps of the whole workload would erase this distinction.
                raw = json.dumps(workload).encode()
                for label in ("é", "😀", "đỏ", "đường", "đương"):
                    escaped = json.dumps(label).encode()
                    parts = raw.split(escaped)
                    raw = b"".join(part + (
                        (json.dumps(label, ensure_ascii=False).encode() if idx % 2 else escaped)
                        if idx < len(parts) - 1 else b""
                    ) for idx, part in enumerate(parts))
                events = self.assert_matching(raw)
                self.assertEqual(events[0], [[[0, 1, 2]], []] if initial_label == "đương" else [[], []])
                self.assertEqual(events[1], [[], [[0, 1, 2]]] if initial_label == "đương" else [[], []])

    def test_malformed_unicode_rejected_before_any_update(self):
        raw = (FIXTURES / "diagnostics-after-commit.json").read_bytes()
        start = raw.rfind(b'"label": 0')
        self.assertGreater(start, 0)
        for invalid in (b'"\\ud800"', b'"\\uZZZZ"', b'"\xed\xa0\x80"', b'"a\x00b"'):
            inp = raw[:start] + raw[start:].replace(b'"label": 0', b'"label": ' + invalid, 1)
            for engine, mode in MODES:
                with self.subTest(invalid=invalid, engine=engine, mode=mode):
                    proc, result = self.native(inp, engine, mode, "--continue-on-error")
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(result["error_category"], "INPUT_SCHEMA_ERROR")
                    self.assertFalse(result["configuration_initialized"])
                    self.assertEqual(result["steps"], [])
                    self.assertEqual(result["execution"]["committed_steps"], 0)

    def test_precommit_and_delivery_fault_matrix(self):
        base = json.loads((FIXTURES / "diagnostics-after-commit.json").read_bytes())
        precommit = ("BEFORE_MUTATION", "ALLOCATION_FAILURE_MUTATION", "DURING_CALLBACK", "BEFORE_PUBLISH")
        reporting = {
            "REPORT_AFTER_COMMIT": "AFTER_EXECUTION",
            "REPORT_RESULT_JSON": "RESULT_JSON",
            "REPORT_GRAPH_DIAGNOSTICS": "GRAPH_DIAGNOSTICS",
            "REPORT_APPEND_STEP": "APPEND_STEP",
        }
        for engine, mode in MODES:
            for op in ("+", "-"):
                for fault in (*precommit, *reporting):
                    with self.subTest(engine=engine, mode=mode, op=op, fault=fault):
                        workload = {**base, "initial_graph": {**base["initial_graph"], "edges": (
                            [edge(0, 1, 0)] if op == "-" else []
                        )}, "updates": [update(op, 0, 1, 0, fault=fault), update(op, 0, 1, 0)]}
                        proc, result = self.native(workload, engine, mode, "--continue-on-error")
                        first, second = result["steps"]
                        if fault in precommit:
                            self.assertEqual(proc.returncode, 6)
                            self.assertEqual(first["status"], "FAULT_INJECTED")
                            self.assertEqual(first["commit_status"], "NOT_COMMITTED")
                            self.assertEqual(first["graph_state"]["edges_count"], int(op == "-"))
                            self.assertEqual(second["status"], "SUCCESS")
                            self.assertEqual(second["graph_state"]["edges_count"], int(op == "+"))
                            event_type = "entered_tuples" if op == "+" else "exited_tuples"
                            self.assertEqual(sorted(second[event_type]), [[0, 1], [1, 0]])
                            self.assertEqual(result["execution"]["delivered_steps"], 2)
                            if mode == "static":
                                self.assertIs(first["nlf_full_cell_reconstruction"], True)
                        else:
                            self.assertEqual(proc.returncode, 9)
                            self.assertEqual(result["error_category"], "RESULT_DELIVERY_ERROR")
                            self.assertEqual(first["status"], "DELIVERY_FAILED")
                            self.assertEqual(first["commit_status"], "COMMITTED")
                            self.assertEqual(first["execution_status"], "SUCCESS")
                            self.assertEqual(first["delivery_status"], "FAILED")
                            self.assertEqual(first["delivery_stage"], reporting[fault])
                            self.assertEqual(first["graph_state"]["edges_count"], int(op == "+"))
                            self.assertEqual(second["status"], "UNATTEMPTED")
                            self.assertEqual(result["execution"], {
                                "total_steps": 2, "attempted_steps": 1, "completed_steps": 0,
                                "committed_steps": 1, "failed_steps": 1, "delivered_steps": 0,
                                "unattempted_steps": 1,
                            })
                            if mode == "static":
                                self.assertIs(first["nlf_full_cell_reconstruction"], True)
                        self.assertEqual(result["execution"]["committed_steps"], 1)
                        self.assertEqual(first["entered_tuples"], [])
                        self.assertEqual(first["exited_tuples"], [])

    def test_original_postcommit_witness_and_output_write_failure(self):
        w = json.loads((FIXTURES / "diagnostics-after-commit.json").read_bytes())
        for engine, mode in MODES:
            with self.subTest(engine=engine, mode=mode):
                injected = {**w, "updates": [
                    {**w["updates"][0], "fault": "REPORT_GRAPH_DIAGNOSTICS"}, *w["updates"][1:],
                ]}
                proc, result = self.native(injected, engine, mode, "--continue-on-error")
                self.assertEqual(proc.returncode, 9)
                self.assertEqual([s["status"] for s in result["steps"]],
                                 ["DELIVERY_FAILED", "UNATTEMPTED", "UNATTEMPTED"])
                self.assertEqual(result["steps"][0]["commit_status"], "COMMITTED")
        if Path("/dev/full").exists():
            proc, _ = self.native(w, "m2", "static", output_device="/dev/full")
            self.assertEqual(proc.returncode, 7)
            self.assertIn("RESULT_DELIVERY_ERROR", proc.stderr)
            self.assertIn("committed_steps=1", proc.stderr)

    def test_unattempted_and_recoverable_malformed_updates(self):
        raw = (FIXTURES / "unattempted-malformed-suffix.json").read_bytes()
        base = json.loads(raw)
        suffix = [
            update("+", "invalid", 1, 0), update("+", 0, [], 0),
            update({}, 0, 1, 0), update("unknown", 0, 1, 0), {}, False, update("INS", 0, 1, 0),
        ]
        for first, category, code in (
            (base["updates"][0], "PREVALIDATION_ERROR", 6),
            ({"op": {}, "u": 0, "v": 1, "label": 0}, "INPUT_SCHEMA_ERROR", 2),
        ):
            for engine, mode in MODES:
                for workload in ({**base, "updates": [first, *suffix]}, *([raw] if code == 6 else [])):
                    with self.subTest(category=category, engine=engine, mode=mode):
                        proc, result = self.native(workload, engine, mode)
                        self.assertEqual(proc.returncode, code)
                        self.assertEqual(result["error_category"], category)
                        self.assertEqual(result["first_failing_step_index"], 0)
                        size = 2 if isinstance(workload, bytes) else len(suffix) + 1
                        self.assertEqual(len(result["steps"]), size)
                        self.assertEqual(result["execution"]["attempted_steps"], 1)
                        self.assertEqual(result["execution"]["unattempted_steps"], size - 1)
                        self.assertEqual(result["execution"]["committed_steps"], 0)
                        for index, step in enumerate(result["steps"]):
                            self.assertEqual(step["step_index"], index)
                            self.assertEqual(step["commit_status"], "NOT_COMMITTED")
                            self.assertEqual(step["entered_tuples"], [])
                            self.assertEqual(step["exited_tuples"], [])
                            if index:
                                self.assertEqual(step["status"], "UNATTEMPTED")
                        if not isinstance(workload, bytes):
                            self.assertNotIn("u", result["steps"][1])
                            self.assertNotIn("v", result["steps"][2])
                            self.assertNotIn("op", result["steps"][3])
                            self.assertNotIn("op", result["steps"][4])
                            self.assertEqual(result["steps"][-1]["op"], "+")
        for engine, mode in MODES:
            proc, result = self.native({**base, "updates": [False, update("+", 0, 1, 0)]},
                                       engine, mode, "--continue-on-error")
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(result["error_category"], "INPUT_SCHEMA_ERROR")
            self.assertEqual([s["status"] for s in result["steps"]], ["ERROR", "SUCCESS"])
            self.assertEqual(result["execution"]["committed_steps"], 1)
            self.assertEqual(result["execution"]["delivered_steps"], 2)

    def test_hard_stop_preserves_an_earlier_recoverable_error(self):
        w = json.loads((FIXTURES / "diagnostics-after-commit.json").read_bytes())
        w["updates"] = [False, update("+", 0, 1, 0, fault="REPORT_APPEND_STEP"), update("-", 0, 1, 0)]
        proc, result = self.native(w, "m2", "static", "--continue-on-error")
        self.assertEqual(proc.returncode, 9)
        self.assertEqual(result["error_category"], "RESULT_DELIVERY_ERROR")
        self.assertEqual(result["first_failing_step_index"], 0)
        self.assertEqual(result["first_failure_category"], "INPUT_SCHEMA_ERROR")
        self.assertEqual([s["status"] for s in result["steps"]], ["ERROR", "DELIVERY_FAILED", "UNATTEMPTED"])

    def test_configuration_validation_at_native_runner_boundary(self):
        w = next(w for w in runner.build_hand_workloads() if w["case_id"] == "CASE_HAND_22")
        actual, _, okay = runner.run_workload_on_binary(self.binary, "m2", w, nlf_mode="static")
        self.assertFalse(okay)
        self.assertTrue(actual["configuration_valid"])
        self.assertTrue(actual["configuration_initialized"])
        startup = next(w for w in runner.build_hand_workloads() if w["case_id"] == "CASE_HAND_21")
        early, _, okay = runner.run_workload_on_binary(self.binary, "m2", startup, nlf_mode="static")
        self.assertFalse(okay)
        self.assertTrue(early["configuration_valid"])
        self.assertFalse(early["configuration_initialized"])
        real_execute = runner.execute_contained_subprocess

        def mutate(field, value):
            def execute(cmd, **kwargs):
                run = real_execute(cmd, **kwargs)
                output_path = Path(cmd[cmd.index("--output") + 1])
                report = json.loads(output_path.read_text())
                if value is None:
                    report.pop(field)
                else:
                    report[field] = value
                output_path.write_text(json.dumps(report))
                return run
            return execute

        for field, value in (
            ("effective_nlf_mode", "none"), ("configuration_status", "UNKNOWN"),
            ("requested_nlf_mode", None), ("nlf_index_logical_bytes", None),
            ("nlf_memory_limit_bytes", 0),
        ):
            with self.subTest(field=field, value=value), patch.object(
                runner, "execute_contained_subprocess", side_effect=mutate(field, value),
            ):
                case_results, outcome = runner.run_hand_and_witness_cases(
                    self.binary, "m2", "CASE_HAND_22", nlf_mode="static",
                )
                self.assertFalse(outcome)
                self.assertIn("configuration integrity failure", case_results[0]["diagnostic"])
                report, _, _ = runner.run_workload_on_binary(self.binary, "m2", w, nlf_mode="static")
                self.assertFalse(report["configuration_valid"])
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["native_reported_outcome"]["error_category"], "PREVALIDATION_ERROR")

        positive = next(w for w in runner.build_hand_workloads() if w["case_id"] == "CASE_HAND_01")
        with patch.object(runner, "execute_contained_subprocess",
                          side_effect=mutate("nlf_memory_limit_bytes", 0)):
            report, _, okay = runner.run_workload_on_binary(self.binary, "m2", positive, nlf_mode="static")
            self.assertFalse(okay)
            self.assertFalse(report["configuration_valid"])
            self.assertEqual(report["native_reported_outcome"]["status"], "SUCCESS")

        good = {**actual, "status": "SUCCESS", "error_category": "NONE", "exit_code": 0}
        for flags in (
            {"nlf_memory_limit_bytes": 0},
            {"effective_nlf_mode": "none", "configuration_status": "REQUESTED_CONFIGURATION_UNAVAILABLE",
             "fallback_reason": "NLF_INDEX_MEMORY_LIMIT", "nlf_index_logical_bytes": 4},
        ):
            self.assertFalse(runner.validate_nlf_configuration("m2", "static", {**good, **flags}, True)[0])
