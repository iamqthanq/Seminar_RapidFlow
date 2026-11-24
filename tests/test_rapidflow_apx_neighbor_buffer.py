"""Canonical CLI identity, transaction and work controls for M2 BUFFER."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_rapidflow_apx_engines as runner
from test_rapidflow_apx_foundation_audit import edge, independent_snapshots, update


ROOT = runner.REPO_ROOT
BINARY = ROOT / "tests/native/rapidflow_apx_engines/build/rapidflow_apx_native"
C4 = ROOT / "tests/fixtures/rapidflow_apx_p4b_residual/residual-deferred.json"
FAR = ROOT / "tests/fixtures/rapidflow_apx_foundation_audit"
ADDITIONS = set(runner.NEIGHBOR_COUNTER_FIELDS) | {
    "neighbor_configuration_version", "requested_neighbor_mode", "effective_neighbor_mode",
}


def unchanged_output(value):
    if isinstance(value, dict):
        return {k: unchanged_output(v) for k, v in value.items() if k not in ADDITIONS}
    if isinstance(value, list):
        return [unchanged_output(v) for v in value]
    return value


class TestNeighborBuffer(unittest.TestCase):
    def workload(self):
        w = json.loads(C4.read_bytes())
        w["case_id"] = "buffer_deferred"
        return w

    def native(self, workload, mode="buffer", *options):
        raw = workload if isinstance(workload, bytes) else json.dumps(workload).encode()
        with tempfile.TemporaryDirectory() as folder:
            inp, out = Path(folder) / "in.json", Path(folder) / "out.json"
            inp.write_bytes(raw)
            result = subprocess.run(
                [str(BINARY), "--engine", "m2", "--neighbor-mode", mode,
                 "--workload", str(inp), "--output", str(out), *options],
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertTrue(out.is_file(), result.stderr)
            return result.returncode, json.loads(out.read_text())

    def test_native_graph_and_pool(self):
        for suite, expected in (("graph", "vector_discrepancies=0 duplicate_discrepancies=0"),
                                ("pool", "1000 lifetimes passed")):
            with self.subTest(suite=suite):
                result = subprocess.run([str(BINARY), "--test-p4b-neighbor", suite], capture_output=True,
                                        text=True, timeout=30, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_cli_matrix(self):
        native_args = ("--workload", str(C4), "--output", "/dev/null")
        for extra in (
            ("--engine", "m1"), ("--nlf-mode", "static"), ("--nlf-mode", "residual"),
            ("--neighbor-mode", "legacy"),
        ):
            args = [str(BINARY), *native_args, "--neighbor-mode", "buffer", *extra]
            result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 1, (args, result.stderr))
        result = subprocess.run([str(BINARY), *native_args, "--neighbor-mode", "Buffer"], capture_output=True)
        self.assertEqual(result.returncode, 1)
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "out.json"
            for engine, nlf in (("all", "none"), ("m1", "none"), ("m2", "static"), ("m2", "residual")):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/run_rapidflow_apx_engines.py"), "--engine", engine,
                     "--nlf-mode", nlf, "--neighbor-mode", "buffer", "--output", str(summary)], capture_output=True,
                    timeout=20, check=False,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(summary.read_text())["overall_status"], "UNSUPPORTED_CONFIGURATION")

    def test_actual_mode_substitution_and_negative_identity(self):
        real = runner.execute_contained_subprocess
        for requested, actual in (("buffer", "legacy"), ("legacy", "buffer")):
            for negative in (False, True):
                with self.subTest(requested=requested, actual=actual, negative=negative):
                    w = self.workload()
                    if negative:
                        w["updates"][0]["u"] = 100

                    def substitute(cmd, **kwargs):
                        args = list(cmd)
                        if "--neighbor-mode" in args:
                            i = args.index("--neighbor-mode")
                            del args[i:i + 2]
                        args.extend(("--neighbor-mode", actual))
                        return real(args, **kwargs)

                    with patch.object(runner, "execute_contained_subprocess", side_effect=substitute):
                        result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", w, neighbor_mode=requested)
                    self.assertFalse(accepted)
                    self.assertTrue(result["configuration_initialized"])
                    self.assertFalse(result["configuration_valid"])
                    self.assertFalse(result["eligible_for_requested_variant"])
                    self.assertEqual(result["effective_neighbor_mode"], actual)
                    self.assertIn("Requested neighbor mode mismatch", result["configuration_error"])
                    original = result["native_reported_outcome"]
                    self.assertEqual(original["status"], "FAIL" if negative else "SUCCESS")
                    self.assertEqual(original["error_category"], "PREVALIDATION_ERROR" if negative else "NONE")
                    self.assertEqual(result["steps"][0]["commit_status"], "NOT_COMMITTED" if negative else "COMMITTED")

    def test_configuration_metadata_matrix(self):
        base = {
            "engine": "m2", "engine_name": "m2", "configuration_initialized": True,
            "configuration_status": "SUCCESS", "nlf_memory_limit_bytes": 1024, "nlf_index_logical_bytes": 0,
            "requested_nlf_mode": "none", "effective_nlf_mode": "none", "neighbor_configuration_version": 1,
            "requested_neighbor_mode": "buffer", "effective_neighbor_mode": "buffer",
        }
        for positive in (False, True):
            with self.subTest(positive=positive):
                def check(d, asked="buffer", engine="m2", nlf="none"):
                    return runner.validate_nlf_configuration(engine, nlf, d, positive, asked)[0]
                self.assertTrue(check(base))
                for field, value in (("effective_neighbor_mode", "legacy"), ("requested_neighbor_mode", "legacy"),
                                     ("effective_neighbor_mode", "bogus"), ("neighbor_configuration_version", True)):
                    self.assertFalse(check({**base, field: value}))
                for field in ("neighbor_configuration_version", "requested_neighbor_mode", "effective_neighbor_mode"):
                    self.assertFalse(check({k: v for k, v in base.items() if k != field}))
                for nlf in ("static", "residual"):
                    self.assertFalse(check({**base, "requested_nlf_mode": nlf, "effective_nlf_mode": nlf}, nlf=nlf))
                self.assertFalse(check({**base, "engine": "m1", "engine_name": "m1"}, engine="m1"))
                self.assertFalse(check({**base, "requested_neighbor_mode": "legacy"}, asked="legacy"))
        early = {"status": "FAIL", "configuration_initialized": False, "neighbor_configuration_version": 1,
                 "error_category": "DOMAIN_ERROR", "exit_code": 3}
        self.assertTrue(runner.validate_nlf_configuration("m2", "none", early, False, "buffer")[0])
        historical = {k: v for k, v in base.items() if k not in ADDITIONS}
        self.assertTrue(runner.validate_nlf_configuration("m2", "none", historical, True)[0])
        self.assertFalse(runner.validate_nlf_configuration("m2", "none", historical, True, "legacy")[0])

    def test_real_missing_current_metadata_rejected(self):
        actual = runner.execute_contained_subprocess

        def remove_identity(cmd, **kwargs):
            result = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            del body["effective_neighbor_mode"]
            out.write_text(json.dumps(body))
            return result

        for mode in ("legacy", "buffer"):
            with patch.object(runner, "execute_contained_subprocess", side_effect=remove_identity):
                result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode=mode)
            self.assertFalse(accepted)
            self.assertIn("neighbor mode", result["configuration_error"])

    def test_work_budget_and_exposed_order(self):
        w = self.workload()
        for limit in (1, 3, 9, 27, 1000):
            with self.subTest(cap=limit):
                a, before = self.native(w, "legacy", "--max-work", str(limit))
                b, after = self.native(w, "buffer", "--max-work", str(limit))
                self.assertEqual(a, b)
                self.assertEqual(unchanged_output(before), unchanged_output(after))
                steps = after["steps"]
                if limit < 1000:
                    self.assertEqual(after["error_category"], "WORK_BUDGET_EXCEEDED")
                    self.assertEqual(steps[0]["commit_status"], "NOT_COMMITTED")
                    self.assertEqual(steps[0]["step_counters"]["work_units"], limit + 1)
                else:
                    self.assertEqual(a, 0)
                    self.assertEqual(steps[0]["commit_status"], "COMMITTED")
                    self.assertEqual(len(steps[0]["entered_tuples"]), 8)
                    self.assertGreater(after["counters"]["neighbor_buffer_no_growth_calls"], 0)

    def test_search_and_graph_fault_recovery(self):
        for op in ("+", "-"):
            w = self.workload()
            if op == "-":
                w["initial_graph"]["edges"].append(edge(0, 1, 0))
            faults = ["AFTER_VERTEX_ASSIGNMENT", "AFTER_MISSING_COMMITMENT", "BEFORE_MUTATION",
                      "ALLOCATION_FAILURE_MUTATION", "DURING_CALLBACK", "BEFORE_PUBLISH"]
            if op == "+":
                faults.append("BETWEEN_FORWARD_REVERSE_MUTATION")
            delta = independent_snapshots(w["query"], w["initial_graph"], [update(op,0,1,0)], w["k"])[0]
            for fault in faults:
                with self.subTest(op=op, fault=fault):
                    w["updates"] = [update(op,0,1,0,fault=fault), update(op,0,1,0)]
                    code, result = self.native(w, "buffer", "--continue-on-error")
                    self.assertEqual(code, 6)
                    first, later = result["steps"]
                    self.assertEqual(first["status"], "FAULT_INJECTED")
                    self.assertEqual(first["commit_status"], "NOT_COMMITTED")
                    self.assertEqual(first["entered_tuples"] + first["exited_tuples"], [])
                    self.assertEqual(first["graph_state"]["edges_count"], 2 + (op == "-"))
                    self.assertEqual(later["commit_status"], "COMMITTED")
                    self.assertEqual(first["codec_state"], later["codec_state"])
                    self.assertEqual([sorted(later["entered_tuples"]), sorted(later["exited_tuples"])], delta)

    def test_new_label_codec_checkpoint(self):
        w = self.workload()
        w["updates"] = [update("+", 0, 3, "new-label", fault="BEFORE_PUBLISH"),
                        update("+", 0, 1, 0), update("+", 0, 3, "new-label")]
        code, result = self.native(w, "buffer", "--continue-on-error")
        self.assertEqual(code, 6)
        first, search, last = result["steps"]
        self.assertEqual(first["commit_status"], "NOT_COMMITTED")
        self.assertEqual(search["commit_status"], "COMMITTED")
        self.assertEqual(len(search["entered_tuples"]), 8)
        self.assertEqual(last["commit_status"], "COMMITTED")
        self.assertEqual([s["codec_state"]["edge_labels_count"] for s in result["steps"]], [1, 1, 2])

    def test_original_malformed_suffix_preserved(self):
        code, result = self.native((FAR / "unattempted-malformed-suffix.json").read_bytes())
        self.assertEqual(code, 6)
        self.assertEqual(result["first_failing_step_index"], 0)
        self.assertEqual(result["error_category"], "PREVALIDATION_ERROR")
        self.assertEqual([s["status"] for s in result["steps"]], ["PREVALIDATION_FAILED", "UNATTEMPTED"])
        self.assertEqual(result["execution"]["committed_steps"], 0)
