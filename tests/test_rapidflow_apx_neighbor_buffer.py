"""Canonical CLI identity, transaction and work controls for M2 BUFFER."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_rapidflow_apx_engines as runner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rapidflow_apx_foundation_audit import edge, independent_snapshots, update


ROOT = runner.REPO_ROOT
BINARY = ROOT / "tests/native/rapidflow_apx_engines/build/rapidflow_apx_native"
C4 = ROOT / "tests/fixtures/rapidflow_apx_p4b_residual/residual-deferred.json"
FAR = ROOT / "tests/fixtures/rapidflow_apx_foundation_audit"
ADDITIONS = set(runner.NEIGHBOR_COUNTER_FIELDS) | set(runner.NEIGHBOR_MERGE_COUNTER_FIELDS) | {
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
                                ("pool", "1000 lifetimes passed"),
                                ("merge", "All MG-G01..G25 scenarios, tentative insert, and fallback guards verified.")):
            with self.subTest(suite=suite):
                result = subprocess.run([str(BINARY), "--test-p4b-neighbor", suite], capture_output=True,
                                        text=True, timeout=30, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_cli_matrix(self):
        native_args = ("--workload", str(C4), "--output", "/dev/null")
        for mode in ("buffer", "merge"):
            for extra in (
                ("--engine", "m1"), ("--nlf-mode", "static"), ("--nlf-mode", "residual"),
                ("--neighbor-mode", "legacy"),
            ):
                args = [str(BINARY), *native_args, "--neighbor-mode", mode, *extra]
                result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 1, (args, result.stderr))
            result = subprocess.run([str(BINARY), *native_args, "--neighbor-mode", mode.capitalize()], capture_output=True)
            self.assertEqual(result.returncode, 1)
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "out.json"
            for mode in ("buffer", "merge"):
                for engine, nlf in (("all", "none"), ("m1", "none"), ("m2", "static"), ("m2", "residual")):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "scripts/run_rapidflow_apx_engines.py"), "--engine", engine,
                         "--nlf-mode", nlf, "--neighbor-mode", mode, "--output", str(summary)], capture_output=True,
                        timeout=20, check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(json.loads(summary.read_text())["overall_status"], "UNSUPPORTED_CONFIGURATION")

    def test_actual_mode_substitution_and_negative_identity(self):
        real = runner.execute_contained_subprocess
        for requested, actual in (
            ("buffer", "legacy"), ("legacy", "buffer"),
            ("merge", "legacy"), ("legacy", "merge"),
            ("merge", "buffer"), ("buffer", "merge"),
        ):
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
            "requested_nlf_mode": "none", "effective_nlf_mode": "none", "neighbor_configuration_version": 2,
            "requested_neighbor_mode": "buffer", "effective_neighbor_mode": "buffer",
        }
        for positive in (False, True):
            with self.subTest(positive=positive):
                def check(d, asked="buffer", engine="m2", nlf="none"):
                    return runner.validate_nlf_configuration(engine, nlf, d, positive, asked)[0]
                self.assertTrue(check(base))
                for field, value in (("effective_neighbor_mode", "legacy"), ("requested_neighbor_mode", "legacy"),
                                     ("effective_neighbor_mode", "bogus"), ("neighbor_configuration_version", True),
                                     ("neighbor_configuration_version", 1)):
                    self.assertFalse(check({**base, field: value}))
                for field in ("neighbor_configuration_version", "requested_neighbor_mode", "effective_neighbor_mode"):
                    self.assertFalse(check({k: v for k, v in base.items() if k != field}))
                for nlf in ("static", "residual"):
                    self.assertFalse(check({**base, "requested_nlf_mode": nlf, "effective_nlf_mode": nlf}, nlf=nlf))
                self.assertFalse(check({**base, "engine": "m1", "engine_name": "m1"}, engine="m1"))
                self.assertFalse(check({**base, "requested_neighbor_mode": "legacy"}, asked="legacy"))
        early = {"status": "FAIL", "configuration_initialized": False, "neighbor_configuration_version": 2,
                 "error_category": "DOMAIN_ERROR", "exit_code": 3}
        self.assertTrue(runner.validate_nlf_configuration("m2", "none", early, False, "buffer")[0])
        self.assertTrue(runner.validate_nlf_configuration("m2", "none", early, False, "merge")[0])
        historical = {k: v for k, v in base.items() if k not in ADDITIONS}
        self.assertTrue(runner.validate_nlf_configuration("m2", "none", historical, True)[0])
        self.assertFalse(runner.validate_nlf_configuration("m2", "none", historical, True, "legacy")[0])
        # Historical mode accepts version 1 when requested_neighbor_mode is not supplied:
        self.assertTrue(runner.validate_nlf_configuration("m2", "none", {**base, "neighbor_configuration_version": 1}, True)[0])
        # Live mode rejects version 1:
        self.assertFalse(runner.validate_nlf_configuration("m2", "none", {**base, "neighbor_configuration_version": 1}, True, "buffer")[0])
        for mode in ("buffer", "merge"):
            base_m = {**base, "requested_neighbor_mode": mode, "effective_neighbor_mode": mode}
            self.assertTrue(runner.validate_nlf_configuration("m2", "none", base_m, True, mode)[0])
            self.assertFalse(runner.validate_nlf_configuration("m2", "none", {**base_m, "effective_neighbor_mode": "legacy"}, True, mode)[0])

    def test_real_missing_current_metadata_rejected(self):
        actual = runner.execute_contained_subprocess

        def remove_identity(cmd, **kwargs):
            result = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            del body["effective_neighbor_mode"]
            out.write_text(json.dumps(body))
            return result

        for mode in ("legacy", "buffer", "merge"):
            with patch.object(runner, "execute_contained_subprocess", side_effect=remove_identity):
                result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode=mode)
            self.assertFalse(accepted)
            self.assertIn("neighbor mode", result["configuration_error"])

    def test_work_budget_and_exposed_order(self):
        w = self.workload()
        for limit in (1, 3, 9, 27, 1000):
            with self.subTest(cap=limit):
                a, legacy_out = self.native(w, "legacy", "--max-work", str(limit))
                b, buffer_out = self.native(w, "buffer", "--max-work", str(limit))
                c, merge_out = self.native(w, "merge", "--max-work", str(limit))
                self.assertEqual(a, b)
                self.assertEqual(b, c)
                self.assertEqual(unchanged_output(legacy_out), unchanged_output(buffer_out))
                self.assertEqual(unchanged_output(buffer_out), unchanged_output(merge_out))
                steps = merge_out["steps"]
                if limit < 1000:
                    self.assertEqual(merge_out["error_category"], "WORK_BUDGET_EXCEEDED")
                    self.assertEqual(steps[0]["commit_status"], "NOT_COMMITTED")
                    self.assertEqual(steps[0]["step_counters"]["work_units"], limit + 1)
                else:
                    self.assertEqual(a, 0)
                    self.assertEqual(steps[0]["commit_status"], "COMMITTED")
                    self.assertEqual(len(steps[0]["entered_tuples"]), 8)
                    mc = merge_out["counters"]
                    bc = buffer_out["counters"]
                    self.assertGreater(bc["neighbor_buffer_no_growth_calls"], 0)
                    self.assertEqual(mc["neighbor_merge_retrieval_calls"], bc["neighbor_buffer_retrieval_calls"])
                    self.assertEqual(
                        mc["neighbor_merge_retrieval_calls"],
                        mc["neighbor_merge_linear_calls"] + mc["neighbor_merge_fast_path_calls"] + mc["neighbor_merge_fallback_calls"],
                    )

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
            for mode in ("buffer", "merge"):
                for fault in faults:
                    with self.subTest(op=op, fault=fault, mode=mode):
                        w["updates"] = [update(op,0,1,0,fault=fault), update(op,0,1,0)]
                        code, result = self.native(w, mode, "--continue-on-error")
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
        for mode in ("buffer", "merge"):
            with self.subTest(mode=mode):
                code, result = self.native(w, mode, "--continue-on-error")
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

    def test_strict_neighbor_counters_schema_helpers(self):
        # 1. Valid merge mode counters under version 2
        c = {f: 0 for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS}
        c["neighbor_buffer_retrieval_calls"] = 10
        c["neighbor_buffer_growth_calls"] = 3
        c["neighbor_buffer_no_growth_calls"] = 7
        c["neighbor_merge_retrieval_calls"] = 10
        c["neighbor_merge_linear_calls"] = 6
        c["neighbor_merge_fast_path_calls"] = 4
        c["neighbor_merge_fallback_calls"] = 0
        res = {"neighbor_configuration_version": 2, "effective_neighbor_mode": "merge"}
        self.assertEqual(runner.validate_neighbor_counters(c, res), "")

        # 2. Missing field under version 2
        for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS:
            bad = {k: v for k, v in c.items() if k != f}
            err = runner.validate_neighbor_counters(bad, res)
            self.assertIn("Missing required neighbor counter", err)

        # 3. Bad types / bounds under version 2
        for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS:
            for bad_val in (None, True, False, 1.5, "10", -1, 500000000000001):
                err = runner.validate_neighbor_counters({**c, f: bad_val}, res)
                self.assertIn(f"Invalid neighbor counter {f}", err)

        # 4. Partition validation for buffer mode
        res_b = {"neighbor_configuration_version": 2, "effective_neighbor_mode": "buffer"}
        cb = {f: 0 for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS}
        cb["neighbor_buffer_retrieval_calls"] = 10
        cb["neighbor_buffer_growth_calls"] = 4
        cb["neighbor_buffer_no_growth_calls"] = 6
        self.assertEqual(runner.validate_neighbor_counters(cb, res_b), "")
        for mf in runner.NEIGHBOR_MERGE_COUNTER_FIELDS:
            self.assertIn("Nonzero merge counter", runner.validate_neighbor_counters({**cb, mf: 1}, res_b))
        self.assertIn("differ from growth plus no-growth",
                      runner.validate_neighbor_counters({**cb, "neighbor_buffer_growth_calls": 3}, res_b))

        # 5. Partition validation for merge mode
        self.assertIn("differ from sum of linear, fast-path, and fallback",
                      runner.validate_neighbor_counters({**c, "neighbor_merge_linear_calls": 5}, res))
        self.assertIn("differ from buffer retrieval calls",
                      runner.validate_neighbor_counters({**c, "neighbor_merge_retrieval_calls": 11, "neighbor_merge_linear_calls": 7}, res))
        self.assertIn("differ from growth plus no-growth calls in merge mode",
                      runner.validate_neighbor_counters({**c, "neighbor_buffer_growth_calls": 2}, res))

        # 6. Legacy mode partition validation
        res_l = {"neighbor_configuration_version": 2, "effective_neighbor_mode": "legacy"}
        cl = {f: 0 for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS}
        self.assertEqual(runner.validate_neighbor_counters(cl, res_l), "")
        for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS:
            self.assertIn("Nonzero neighbor counter", runner.validate_neighbor_counters({**cl, f: 1}, res_l))

    def test_subprocess_counter_mutations_and_reconciliation(self):
        actual = runner.execute_contained_subprocess

        # Mutation 1: Remove all 7 counters from output
        def remove_all_counters(cmd, **kwargs):
            res = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS:
                body.get("counters", {}).pop(f, None)
            out.write_text(json.dumps(body))
            return res

        def get_diag(res_dict):
            return res_dict.get("schema_error") or res_dict.get("configuration_error") or res_dict.get("error_message") or ""

        with patch.object(runner, "execute_contained_subprocess", side_effect=remove_all_counters):
            result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode="merge")
        self.assertFalse(accepted)
        self.assertIn("Missing required neighbor counter", get_diag(result))
        self.assertEqual(result["native_reported_outcome"]["status"], "SUCCESS")

        # Mutation 2: Remove 1 counter (neighbor_merge_linear_calls)
        def remove_one_counter(cmd, **kwargs):
            res = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            body.get("counters", {}).pop("neighbor_merge_linear_calls", None)
            out.write_text(json.dumps(body))
            return res

        with patch.object(runner, "execute_contained_subprocess", side_effect=remove_one_counter):
            result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode="merge")
        self.assertFalse(accepted)
        self.assertIn("Missing required neighbor counter 'neighbor_merge_linear_calls'", get_diag(result))
        self.assertEqual(result["native_reported_outcome"]["status"], "SUCCESS")

        # Mutation 3: Set counter to string
        def string_counter(cmd, **kwargs):
            res = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            body["counters"]["neighbor_merge_linear_calls"] = "10"
            out.write_text(json.dumps(body))
            return res

        with patch.object(runner, "execute_contained_subprocess", side_effect=string_counter):
            result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode="merge")
        self.assertFalse(accepted)
        self.assertIn("Invalid neighbor counter neighbor_merge_linear_calls", get_diag(result))
        self.assertEqual(result["native_reported_outcome"]["status"], "SUCCESS")

        # Mutation 4: Cumulative reconciliation violation (RB-01..RB-12 root linear/fast redistribution)
        def redistribute_root_counters(cmd, **kwargs):
            res = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            cnt = body["counters"]
            if cnt["neighbor_merge_linear_calls"] > 0:
                cnt["neighbor_merge_linear_calls"] -= 1
                cnt["neighbor_merge_fast_path_calls"] += 1
            out.write_text(json.dumps(body))
            return res

        with patch.object(runner, "execute_contained_subprocess", side_effect=redistribute_root_counters):
            result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode="merge")
        self.assertFalse(accepted)
        self.assertIn("mismatch with sum of step counters", get_diag(result))
        self.assertEqual(result["native_reported_outcome"]["status"], "SUCCESS")

    def test_historical_p4b2a_baseline_compatibility(self):
        baseline_bin = Path("/tmp/rapidflow-p4b2b-r1-ot7fz8/binaries/p4b2a-baseline-native")
        if not baseline_bin.is_file():
            self.skipTest("Historical baseline binary not found")
        # Run workload directly on historical baseline binary
        with tempfile.TemporaryDirectory() as tmp:
            inp, out = Path(tmp) / "in.json", Path(tmp) / "out.json"
            inp.write_text(json.dumps(self.workload()))
            res = subprocess.run([str(baseline_bin), "--engine", "m2", "--neighbor-mode", "buffer",
                                  "--workload", str(inp), "--output", str(out)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            hist_out = json.loads(out.read_text())
            self.assertEqual(hist_out.get("neighbor_configuration_version"), 1)
            # Historical validation without requested_neighbor_mode accepts version 1
            ok, diag, meta = runner.validate_nlf_configuration("m2", "none", hist_out, True)
            self.assertTrue(ok, diag)
            # Live validation with requested_neighbor_mode strictly rejects version 1 (downgrade protection)
            ok_live, diag_live, _ = runner.validate_nlf_configuration("m2", "none", hist_out, True, "buffer")
            self.assertFalse(ok_live)
            self.assertIn("Missing initialized current neighbor configuration identity", diag_live)
