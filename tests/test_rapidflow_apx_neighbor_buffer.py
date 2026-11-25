"""Canonical CLI identity, transaction and work controls for M2 BUFFER."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import MappingProxyType
from typing import Optional
import unittest
from unittest.mock import patch

from scripts import run_rapidflow_apx_engines as runner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rapidflow_apx_foundation_audit import edge, independent_snapshots, update


ROOT = runner.REPO_ROOT
BINARY = ROOT / "tests/native/rapidflow_apx_engines/build/rapidflow_apx_native"
C4 = ROOT / "tests/fixtures/rapidflow_apx_p4b_residual/residual-deferred.json"
FAR = ROOT / "tests/fixtures/rapidflow_apx_foundation_audit"
P4B2A_BIN_SHA = "d21575c1c70d1f7bc2380d8172ae4044dacfc3f6ec464d06884555c284d36b84"
P4B2A_COMMIT_SHA = "d2108ec6fee083ce0716e2935e07750e56461a72"
P4B2B_BIN_SHA = "260e5627799b624bf010c4631011db8874ebf769bf4b4cdd62831f1ad25a9608"
P4B2B_COMMIT_SHA = "bdb1fd2eda4d7881041e6e32500d62af6da1505d"
P4B2A_ARCHIVE_SHA = "94833a8876a413fc34dce53ba3a037aea4c0f9a2926a59eb505cb2817b0c10cd"
R112_MANIFEST_SHA = "bcd359cdb36f19e2343df75ba19c2913f918bf184e11818e04b8f9adcae8f82b"
ADDITIONS = set(runner.NEIGHBOR_COUNTER_FIELDS) | set(runner.NEIGHBOR_MERGE_COUNTER_FIELDS) | {
    "neighbor_configuration_version", "requested_neighbor_mode", "effective_neighbor_mode",
}


def _find_historical_binary(filename: str) -> Optional[Path]:
    patterns = (
        f"/tmp/rapidflow-p4b2b-r1-1-2/binaries/{filename}",
        f"/tmp/rapidflow-p4b2b-r1-1/binaries/{filename}",
        f"/tmp/rapidflow-p4b2b-r1-ot7fz8/binaries/{filename}",
    )
    for raw in patterns:
        p = Path(raw)
        if p.is_file():
            return p
    for p in sorted(Path("/tmp").glob(f"rapidflow-p4b2b-r1-1-3-*/binaries/{filename}")):
        if p.is_file():
            return p
    return None


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
        # Historical mode with version 1 requires genuinely verified provenance (caller-constructed dataclass rejected):
        self.assertFalse(runner.validate_nlf_configuration("m2", "none", {**base, "neighbor_configuration_version": 1}, True)[0])
        unverified_auth = runner.VerifiedHistoricalArtifact(
            binary_sha256=P4B2A_BIN_SHA,
            commit_sha=P4B2A_COMMIT_SHA,
            historical_profile="buffer",
            schema_version=1,
        )
        self.assertFalse(runner.validate_nlf_configuration("m2", "none", {**base, "neighbor_configuration_version": 1}, True, historical_auth=unverified_auth)[0])
        # Verify genuine replay against frozen binary if present, else hermetic verified receipt binding:
        bin_2a = _find_historical_binary("p4b2a-baseline-native")
        if bin_2a is not None:
            hist_res, _, hist_ok, hist_receipt = runner.run_verified_historical_binary(
                bin_2a, "m2", self.workload(), neighbor_mode="buffer", expected_commit_sha=P4B2A_COMMIT_SHA
            )
            self.assertTrue(hist_ok)
            self.assertIsNotNone(hist_receipt)
            self.assertTrue(runner.validate_nlf_configuration("m2", "none", hist_res, True, "buffer", historical_auth=hist_receipt)[0])
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

        # 7. Historical version 1 schema validation matrix (R1-RV04)
        res_v1_b = {"neighbor_configuration_version": 1, "effective_neighbor_mode": "buffer"}
        c_v1_b = {
            "neighbor_buffer_retrieval_calls": 8,
            "neighbor_buffer_growth_calls": 2,
            "neighbor_buffer_no_growth_calls": 6,
        }
        self.assertEqual(runner.validate_neighbor_counters(c_v1_b, res_v1_b), "")
        for f in runner.NEIGHBOR_COUNTER_FIELDS:
            bad_v1 = {k: v for k, v in c_v1_b.items() if k != f}
            self.assertIn("Missing required neighbor counter", runner.validate_neighbor_counters(bad_v1, res_v1_b))
            self.assertIn("Invalid neighbor counter", runner.validate_neighbor_counters({**c_v1_b, f: True}, res_v1_b))
        for mf in runner.NEIGHBOR_MERGE_COUNTER_FIELDS:
            self.assertIn("Nonzero or invalid merge counter", runner.validate_neighbor_counters({**c_v1_b, mf: 1}, res_v1_b))

        res_v1_m = {"neighbor_configuration_version": 1, "effective_neighbor_mode": "merge"}
        c_v1_m = dict(c)
        self.assertEqual(runner.validate_neighbor_counters(c_v1_m, res_v1_m), "")
        for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS:
            bad_v1_m = {k: v for k, v in c_v1_m.items() if k != f}
            self.assertIn("Missing required neighbor counter", runner.validate_neighbor_counters(bad_v1_m, res_v1_m))
            self.assertIn("Invalid neighbor counter", runner.validate_neighbor_counters({**c_v1_m, f: False}, res_v1_m))

        res_bad_ver = {"neighbor_configuration_version": 3, "effective_neighbor_mode": "merge"}
        self.assertIn("Unknown or invalid neighbor configuration version", runner.validate_neighbor_counters(c, res_bad_ver))

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

        # Mutation 5: Historical version 1 under live run triggers downgrade protection rejection (R1-RV04)
        def downgrade_version(cmd, **kwargs):
            res = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            body["neighbor_configuration_version"] = 1
            out.write_text(json.dumps(body))
            return res

        with patch.object(runner, "execute_contained_subprocess", side_effect=downgrade_version):
            result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode="merge", historical_auth=None)
        self.assertFalse(accepted)
        self.assertIn("Missing initialized current neighbor configuration identity", get_diag(result))

        # Mutation 6: Live run with boolean bypass rejected (R1-RV04)
        result, _, accepted = runner.run_workload_on_binary(BINARY, "m2", self.workload(), neighbor_mode="merge", historical_auth=True)
        self.assertFalse(accepted)
        self.assertIn("boolean bypass rejected", get_diag(result))

    def test_historical_p4b2a_baseline_compatibility(self):
        baseline_bin = _find_historical_binary("p4b2a-baseline-native")
        if baseline_bin is None:
            self.skipTest("Historical baseline binary not found")
        w = self.workload()
        # Run workload directly on historical baseline binary
        with tempfile.TemporaryDirectory() as tmp:
            inp, out = Path(tmp) / "in.json", Path(tmp) / "out.json"
            inp.write_text(json.dumps(w))
            res = subprocess.run([str(baseline_bin), "--engine", "m2", "--neighbor-mode", "buffer",
                                  "--workload", str(inp), "--output", str(out)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            hist_out = json.loads(out.read_text())
            self.assertEqual(hist_out.get("neighbor_configuration_version"), 1)
            # Live validation strictly rejects version 1 (downgrade protection)
            ok_live, diag_live, _ = runner.validate_nlf_configuration("m2", "none", hist_out, True, "buffer")
            self.assertFalse(ok_live)
            self.assertIn("Missing initialized current neighbor configuration identity", diag_live)

            # Unverified caller-constructed VerifiedHistoricalArtifact is rejected (R113-B03)
            fake_auth = runner.VerifiedHistoricalArtifact(
                binary_sha256=P4B2A_BIN_SHA,
                commit_sha=P4B2A_COMMIT_SHA,
                historical_profile="buffer",
                schema_version=1,
            )
            ok_fake, diag_fake, _ = runner.validate_historical_artifact_configuration(hist_out, fake_auth, "m2", "none", "buffer")
            self.assertFalse(ok_fake)
            self.assertIn("HISTORICAL_PROVENANCE_ERROR", diag_fake)

        # Workflow A: verified historical replay returns attested receipt
        replay_res, _, acc_replay, receipt = runner.run_verified_historical_binary(
            baseline_bin, "m2", w, neighbor_mode="buffer",
            expected_commit_sha=P4B2A_COMMIT_SHA, expected_binary_sha256=P4B2A_BIN_SHA,
        )
        self.assertTrue(acc_replay, replay_res.get("error_message"))
        self.assertIsNotNone(receipt)
        ok_hist, diag_hist, _ = runner.validate_historical_artifact_configuration(
            replay_res, receipt, "m2", "none", "buffer", workload=w, native_exit_code=0,
        )
        self.assertTrue(ok_hist, diag_hist)

        # Also verify run_workload_on_binary accepts HistoricalBinaryRequest against actual frozen binary
        req = runner.HistoricalBinaryRequest(
            binary_sha256=P4B2A_BIN_SHA,
            commit_sha=P4B2A_COMMIT_SHA,
            historical_profile="buffer",
            schema_version=1,
        )
        res_dict, _, acc = runner.run_workload_on_binary(baseline_bin, "m2", w, neighbor_mode="buffer", historical_auth=req)
        self.assertTrue(acc, res_dict.get("error_message"))


class TestHistoricalSchemaAuthorizationMatrix(unittest.TestCase):
    """Exhaustive test suite for RV04-T01 through RV04-T19 (updated for R113-A/B provenance binding)."""

    def setUp(self):
        self.p4b2a_sha = P4B2A_BIN_SHA
        self.p4b2a_commit = P4B2A_COMMIT_SHA
        self.p4b2b_sha = P4B2B_BIN_SHA
        self.p4b2b_commit = P4B2B_COMMIT_SHA

    def _base_v1_artifact_dict(self, profile_sha: str, mode: str) -> dict:
        counters = {
            "raw_emissions": 1, "unique_emissions": 1, "committed_emissions": 1, "discarded_emissions": 0,
            "work_units": 1, "max_work": 500000, "anchors_started": 1, "dfs_states": 1,
            "candidate_evaluations": 1, "candidate_extensions": 1, "edge_lookups": 1, "vertex_label_checks": 2,
            "edge_decisions": 1, "missing_branches": 0, "commitment_checks": 0, "commitment_rejections": 0,
            "neighbor_buffer_retrieval_calls": 0, "neighbor_buffer_growth_calls": 0, "neighbor_buffer_no_growth_calls": 0,
        }
        if profile_sha == self.p4b2b_sha:
            for k in runner.NEIGHBOR_MERGE_COUNTER_FIELDS:
                counters[k] = 0
        raw_w = {
            "case_id": "hermetic_v1", "k": 0,
            "query": {"vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}], "edges": [{"u": 1, "v": 2, "label": 0}]},
            "initial_graph": {"vertices": [{"id": 1, "label": 0}, {"id": 2, "label": 0}], "edges": []},
            "updates": [{"op": "+", "u": 1, "v": 2, "label": 0}],
        }
        in_sha = hashlib.sha256(json.dumps(raw_w).encode("utf-8")).hexdigest()
        return {
            "schema_version": "1.1.0", "semantics_version": "D-R-1.0", "status": "SUCCESS", "error_category": "NONE",
            "engine": "m2", "engine_name": "m2", "case_id": "hermetic_v1", "raw_input_sha256": in_sha,
            "requested_nlf_mode": "none", "effective_nlf_mode": "none", "configuration_status": "SUCCESS",
            "nlf_memory_limit_bytes": 1048576, "nlf_index_logical_bytes": 0,
            "configuration_initialized": True, "neighbor_configuration_version": 1,
            "requested_neighbor_mode": mode, "effective_neighbor_mode": mode,
            "work_budget": {"default_limit": 500000, "canonical_cap": 500000,
                            "workload_requested_limit": 500000, "cli_requested_limit": 500000, "effective_limit": 500000},
            "execution": {"total_steps": 1, "attempted_steps": 1, "completed_steps": 1, "failed_steps": 0, "unattempted_steps": 0},
            "steps": [{
                "step_index": 0, "status": "SUCCESS", "commit_status": "COMMITTED", "error_category": "NONE",
                "op": "+", "u": 1, "v": 2, "label": 0, "entered_tuples": [[1, 2]], "exited_tuples": [],
                "step_counters": dict(counters),
            }],
            "query_info": {"k": 0, "query_canonical_order": [1, 2], "computed_lambda": 1},
            "counters": counters,
        }, raw_w

    def _verify_hermetic_v1(self, bin_sha: str, mode: str):
        out_dict, raw_w = self._base_v1_artifact_dict(bin_sha, mode)
        raw_out_str = json.dumps(out_dict)
        raw_in_str = json.dumps(raw_w)
        rec = {
            "workload_family": "hermetic_test",
            "case_id": "hermetic_v1",
            "input_sha256": hashlib.sha256(raw_in_str.encode("utf-8")).hexdigest(),
            "raw_input": raw_in_str,
            "native_binary_sha256": bin_sha,
            "engine": "m2", "nlf_mode": "none", "neighbor_mode": mode, "exit_code": 0,
            "raw_output": raw_out_str,
        }
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            ledger_p = tdp / "ledger.jsonl.gz"
            ledger_bytes = gzip.compress((json.dumps(rec) + "\n").encode("utf-8"))
            ledger_p.write_bytes(ledger_bytes)
            manifest_obj = {"files": [{"path": "ledger.jsonl.gz", "size_bytes": len(ledger_bytes),
                                       "sha256": hashlib.sha256(ledger_bytes).hexdigest()}]}
            man_p = tdp / "manifest.json"
            man_bytes = json.dumps(manifest_obj).encode("utf-8")
            man_p.write_bytes(man_bytes)
            man_sha = hashlib.sha256(man_bytes).hexdigest()
            anchors = MappingProxyType({man_sha: MappingProxyType({
                "anchor_id": "hermetic_anchor", "kind": "manifest_json",
                "allowed_binary_sha256s": (bin_sha,), "provenance_origin": "hermetic unit test anchor",
            })})
            with patch.object(runner, "TRUSTED_HISTORICAL_ANCHORS", anchors):
                return runner.verify_historical_artifact(
                    man_p, man_sha, "ledger.jsonl.gz", case_id="hermetic_v1",
                    expected_binary_sha256=bin_sha,
                    expected_commit_sha=runner.AUTHORIZED_HISTORICAL_BINARIES[bin_sha]["commit_sha"],
                    expected_engine="m2", expected_nlf_mode="none", expected_neighbor_mode=mode,
                    expected_workload=raw_w,
                )

    def make_v2_result(self, mode="merge"):
        return {
            "status": "SUCCESS",
            "engine": "m2",
            "engine_name": "m2",
            "requested_nlf_mode": "none",
            "effective_nlf_mode": "none",
            "configuration_status": "SUCCESS",
            "nlf_memory_limit_bytes": 1048576,
            "nlf_index_logical_bytes": 0,
            "configuration_initialized": True,
            "neighbor_configuration_version": 2,
            "requested_neighbor_mode": mode,
            "effective_neighbor_mode": mode,
            "counters": {
                "raw_emissions": 0,
                "unique_emissions": 0,
                "committed_emissions": 0,
                "discarded_emissions": 0,
                "work_units": 0,
                "max_work": 1000000,
                "anchors_started": 0,
                "dfs_states": 0,
                "candidate_evaluations": 0,
                "candidate_extensions": 0,
                "edge_lookups": 0,
                "vertex_label_checks": 0,
                "edge_decisions": 0,
                "missing_branches": 0,
                "commitment_checks": 0,
                "commitment_rejections": 0,
                "neighbor_buffer_retrieval_calls": 10,
                "neighbor_buffer_growth_calls": 2,
                "neighbor_buffer_no_growth_calls": 8,
                "neighbor_merge_retrieval_calls": 10,
                "neighbor_merge_linear_calls": 6,
                "neighbor_merge_fast_path_calls": 4,
                "neighbor_merge_fallback_calls": 0,
            },
        }

    def make_v1_result(self, mode="buffer"):
        res = self.make_v2_result(mode)
        res["neighbor_configuration_version"] = 1
        if mode == "buffer":
            for k in runner.NEIGHBOR_MERGE_COUNTER_FIELDS:
                res["counters"][k] = 0
        return res

    def test_rv04_t01_live_v2_valid_passes(self):
        res = self.make_v2_result("merge")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge")
        self.assertTrue(ok, diag)

    def test_rv04_t02_live_v1_without_auth_fails_downgrade(self):
        res = self.make_v1_result("merge")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge")
        self.assertFalse(ok)
        self.assertIn("downgrade rejected", diag)

    def test_rv04_t03_live_missing_configuration_initialized_fails(self):
        res = self.make_v2_result("merge")
        del res["configuration_initialized"]
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge")
        self.assertFalse(ok)
        self.assertIn("Missing initialized current neighbor configuration identity", diag)

    def test_rv04_t04_live_marker_false_fails(self):
        res = self.make_v2_result("merge")
        res["configuration_initialized"] = False
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge")
        self.assertFalse(ok)
        self.assertIn("Uninitialized configuration contradicts native execution evidence", diag)

    def test_rv04_t05_version1_without_provenance_fails(self):
        res = self.make_v1_result("buffer")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "buffer", historical_auth=None)
        self.assertFalse(ok)
        self.assertIn("downgrade rejected", diag)

    def test_rv04_t06_unauthorized_binary_sha_fails(self):
        bad_auth = runner.VerifiedHistoricalArtifact(
            binary_sha256="0123456789abcdef" * 4,
            commit_sha="abcdef0123456789",
            historical_profile="buffer",
            schema_version=1,
        )
        res = self.make_v1_result("buffer")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "buffer", historical_auth=bad_auth)
        self.assertFalse(ok)
        self.assertIn("Unauthorized historical binary SHA-256", diag)

    def test_rv04_t07_profile_mismatch_fails(self):
        # P4b-2A baseline is only allowed 'buffer' and 'legacy', not 'merge'
        auth = runner.VerifiedHistoricalArtifact(
            binary_sha256=self.p4b2a_sha,
            commit_sha=self.p4b2a_commit,
            historical_profile="merge",
            schema_version=1,
        )
        res = self.make_v1_result("merge")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge", historical_auth=auth)
        self.assertFalse(ok)
        self.assertIn("not permitted for binary", diag)

    def test_rv04_t08_commit_sha_mismatch_fails(self):
        auth = runner.VerifiedHistoricalArtifact(
            binary_sha256=self.p4b2a_sha,
            commit_sha="0000000000000000000000000000000000000000",
            historical_profile="buffer",
            schema_version=1,
        )
        res = self.make_v1_result("buffer")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "buffer", historical_auth=auth)
        self.assertFalse(ok)
        self.assertIn("Historical commit SHA mismatch", diag)

    def test_rv04_t09_v1_buffer_historical_valid_passes(self):
        ok, diag, receipt, parsed = self._verify_hermetic_v1(self.p4b2a_sha, "buffer")
        self.assertTrue(ok, diag)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.commit_sha, self.p4b2a_commit)

    def test_rv04_t10_v1_merge_historical_valid_passes(self):
        ok, diag, receipt, parsed = self._verify_hermetic_v1(self.p4b2b_sha, "merge")
        self.assertTrue(ok, diag)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.commit_sha, self.p4b2b_commit)

    def test_rv04_t11_v1_legacy_historical_valid_passes(self):
        ok, diag, receipt, parsed = self._verify_hermetic_v1(self.p4b2a_sha, "legacy")
        self.assertTrue(ok, diag)
        self.assertIsNotNone(receipt)

    def test_rv04_t12_v2_null_counter_fails(self):
        res = self.make_v2_result("merge")
        res["counters"]["neighbor_merge_linear_calls"] = None
        err = runner.validate_neighbor_counters(res["counters"], res)
        self.assertIn("Invalid neighbor counter neighbor_merge_linear_calls", err)

    def test_rv04_t13_v2_bool_counter_fails(self):
        res = self.make_v2_result("merge")
        res["counters"]["neighbor_merge_linear_calls"] = True
        err = runner.validate_neighbor_counters(res["counters"], res)
        self.assertIn("Invalid neighbor counter neighbor_merge_linear_calls", err)

    def test_rv04_t14_mode_mismatch_fails(self):
        res = self.make_v2_result("merge")
        res["effective_neighbor_mode"] = "buffer"
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge")
        self.assertFalse(ok)
        self.assertIn("requires effective 'merge'", diag)

    def test_rv04_t15_boolean_historical_auth_fails(self):
        res = self.make_v2_result("merge")
        ok, diag, meta = runner.validate_nlf_configuration("m2", "none", res, True, "merge", historical_auth=True)
        self.assertFalse(ok)
        self.assertIn("boolean bypass rejected", diag)

    def test_rv04_t16_candidate_binary_historical_auth_sha_mismatch(self):
        req = runner.HistoricalBinaryRequest(
            binary_sha256=self.p4b2a_sha,
            commit_sha=self.p4b2a_commit,
            historical_profile="buffer",
            schema_version=1,
        )
        w = json.loads(C4.read_bytes())
        w["case_id"] = "buffer_deferred"
        res, _, acc = runner.run_workload_on_binary(BINARY, "m2", w, neighbor_mode="buffer", historical_auth=req)
        self.assertFalse(acc)
        self.assertEqual(res.get("status"), "AUTHORIZATION_ERROR")
        self.assertIn("does not match historical authorization SHA", res.get("error_message", ""))

    def test_rv04_t17_historical_missing_buffer_counter_fails(self):
        res = self.make_v1_result("buffer")
        del res["counters"]["neighbor_buffer_retrieval_calls"]
        err = runner.validate_neighbor_counters(res["counters"], res)
        self.assertIn("Missing required neighbor counter 'neighbor_buffer_retrieval_calls' in historical buffer profile", err)

    def test_rv04_t18_historical_buffer_nonzero_merge_counter_fails(self):
        res = self.make_v1_result("buffer")
        res["counters"]["neighbor_merge_linear_calls"] = 5
        err = runner.validate_neighbor_counters(res["counters"], res)
        self.assertIn("Nonzero or invalid merge counter 'neighbor_merge_linear_calls' in historical buffer profile", err)

    def test_rv04_t19_historical_merge_partition_violation_fails(self):
        res = self.make_v1_result("merge")
        res["counters"]["neighbor_merge_linear_calls"] += 1
        err = runner.validate_neighbor_counters(res["counters"], res)
        self.assertIn("Total merge retrievals differ from sum of linear, fast-path, and fallback calls", err)


class TestR113ProvenanceClosure(unittest.TestCase):
    """Focused regression matrix for R113-A, R113-B, R113-C, and strict live invariants."""

    def _c4_workload(self):
        w = json.loads(C4.read_bytes())
        w["case_id"] = "r113_c4_witness"
        return w

    # --- Group A: Historical source identity (R113-A01..A05) ---

    def test_r113_a01_to_a05_historical_git_identities(self):
        # R113-A01 & A02: Verify real Git commits match registry
        for sha, exp_commit in ((P4B2A_BIN_SHA, P4B2A_COMMIT_SHA), (P4B2B_BIN_SHA, P4B2B_COMMIT_SHA)):
            entry = runner.AUTHORIZED_HISTORICAL_BINARIES[sha]
            self.assertEqual(entry["commit_sha"], exp_commit)
            res = subprocess.run(["git", "cat-file", "-t", exp_commit], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "commit")

        # R113-A03 & A04: Incorrect / truncated commits rejected
        bad_commits = (
            "d21575c1c70d1f7bc2380d8172ae4044dacfc3f",  # 39-char truncated binary SHA
            "bdb1fd26c92d56d11f71477708eb107e371a5330", # Invalid commit ID
        )
        for bad in bad_commits:
            self.assertNotEqual(runner.AUTHORIZED_HISTORICAL_BINARIES[P4B2A_BIN_SHA]["commit_sha"], bad)
            self.assertNotEqual(runner.AUTHORIZED_HISTORICAL_BINARIES[P4B2B_BIN_SHA]["commit_sha"], bad)
            b2a = _find_historical_binary("p4b2a-baseline-native")
            if b2a is not None:
                res, _, acc, rec = runner.run_verified_historical_binary(b2a, "m2", self._c4_workload(), expected_commit_sha=bad)
                self.assertFalse(acc)
                self.assertIsNone(rec)
                self.assertEqual(res.get("error_category"), "HISTORICAL_AUTHORIZATION_ERROR")

        # R113-A05: Valid P4b-2A commit paired with wrong binary hash (P4b-2B binary) rejected
        b2b = _find_historical_binary("p4b2b-release-native")
        if b2b is not None:
            res, _, acc, rec = runner.run_verified_historical_binary(
                b2b, "m2", self._c4_workload(), neighbor_mode="merge", expected_commit_sha=P4B2A_COMMIT_SHA,
            )
            self.assertFalse(acc)
            self.assertIsNone(rec)
            self.assertIn("does not match authoritative binary source commit", res.get("error_message", ""))

    # --- Group B: Artifact provenance (R113-B01..B12) ---

    def test_r113_b02_b03_b11_forged_witness_and_caller_spoof_rejected(self):
        # Mandatory witness (R113-B02): Real current MERGE output forged to version 1 + caller metadata
        w = self._c4_workload()
        curr_res, _, curr_ok = runner.run_workload_on_binary(BINARY, "m2", w, neighbor_mode="merge")
        self.assertTrue(curr_ok, curr_res.get("error_message"))

        forged = copy.deepcopy(curr_res)
        forged["neighbor_configuration_version"] = 1
        caller_auth = runner.VerifiedHistoricalArtifact(
            binary_sha256=P4B2B_BIN_SHA,
            commit_sha=P4B2B_COMMIT_SHA,
            historical_profile="merge",
            schema_version=1,
            artifact_id="forged_r113_b02",
            manifest_id="forged_manifest",
        )
        ok_cert, diag_cert, _ = runner.validate_historical_artifact_configuration(
            forged, caller_auth, "m2", "none", "merge", workload=w,
        )
        self.assertFalse(ok_cert, "Forged current v2->v1 output MUST be rejected")
        self.assertIn("HISTORICAL_PROVENANCE_ERROR", diag_cert)

        ok_nlf, diag_nlf, _ = runner.validate_nlf_configuration("m2", "none", forged, True, "merge", historical_auth=caller_auth)
        self.assertFalse(ok_nlf)
        self.assertIn("HISTORICAL_PROVENANCE_ERROR", diag_nlf)

        # R113-B03: Even when native_result IS version 1, caller-constructed auth without verification seal fails
        forged_with_all_fields = runner.VerifiedHistoricalArtifact(
            binary_sha256=P4B2B_BIN_SHA, commit_sha=P4B2B_COMMIT_SHA, historical_profile="merge", schema_version=1,
            artifact_id="x", manifest_id="y", manifest_sha256=R112_MANIFEST_SHA, artifact_sha256="a"*64,
            artifact_size=10, workload_input_sha256="b"*64, engine="m2", nlf_mode="none", neighbor_mode="merge",
            native_output_sha256="c"*64, native_exit_status=0, origin_type="offline_trusted_manifest_artifact",
            provenance_origin="spoof", parsed_result_sha256="d"*64,
        )
        ok_b03, diag_b03, _ = runner.validate_historical_artifact_configuration(
            forged, forged_with_all_fields, "m2", "none", "merge",
        )
        self.assertFalse(ok_b03)
        self.assertIn("HISTORICAL_PROVENANCE_ERROR", diag_b03)

    def test_r113_b01_b04_to_b12_offline_trusted_anchors_and_mutations(self):
        # Create hermetic verified ledger & manifest PLUS test real P4b-2A tar / R1.1.2 manifest if present
        p4b2a_tar = Path("/tmp/rapidflow-p4b2a-4TsBiw/review-evidence.tar.gz")
        r112_man = Path("/tmp/rapidflow-p4b2b-r1-1-2/evidence-manifest.json")
        hand0 = runner.build_hand_workloads()[0]

        if p4b2a_tar.is_file():
            # R113-B01 on real P4b-2A accepted archive
            ok_2a, diag_2a, art_2a, out_2a = runner.verify_historical_artifact(
                p4b2a_tar, P4B2A_ARCHIVE_SHA, "canonical-replays.jsonl.gz",
                stage="m2_none_buffer_hand", case_id="CASE_HAND_01",
                expected_binary_sha256=P4B2A_BIN_SHA, expected_commit_sha=P4B2A_COMMIT_SHA,
                expected_engine="m2", expected_nlf_mode="none", expected_neighbor_mode="buffer",
                expected_workload=hand0,
            )
            self.assertTrue(ok_2a, diag_2a)
            self.assertIsNotNone(art_2a)

            # R113-B11: Valid verified receipt + substituted output dictionary -> REJECTED!
            sub_out = copy.deepcopy(out_2a)
            sub_out["counters"]["work_units"] += 1
            ok_b11, diag_b11, _ = runner.validate_historical_artifact_configuration(
                sub_out, art_2a, "m2", "none", "buffer", workload=hand0,
            )
            self.assertFalse(ok_b11)
            self.assertIn("does not match verified historical artifact payload digest", diag_b11)

            # R113-B09 & B12: Wrong profile / incompatible modes against real P4b-2A archive
            ok_b09, diag_b09, _, _ = runner.verify_historical_artifact(
                p4b2a_tar, P4B2A_ARCHIVE_SHA, "canonical-replays.jsonl.gz",
                stage="m2_none_buffer_hand", case_id="CASE_HAND_01",
                expected_binary_sha256=P4B2B_BIN_SHA, expected_engine="m2", expected_nlf_mode="none",
                expected_neighbor_mode="buffer",
            )
            self.assertFalse(ok_b09)
            ok_b12, diag_b12, _, _ = runner.verify_historical_artifact(
                p4b2a_tar, P4B2A_ARCHIVE_SHA, "canonical-replays.jsonl.gz",
                stage="m2_none_buffer_hand", case_id="CASE_HAND_01",
                expected_engine="m2", expected_nlf_mode="none", expected_neighbor_mode="merge",
            )
            self.assertFalse(ok_b12)

            # R113-B10: Wrong workload paired with valid archive record
            wrong_w = copy.deepcopy(hand0)
            wrong_w["k"] = 99
            ok_b10, diag_b10, _, _ = runner.verify_historical_artifact(
                p4b2a_tar, P4B2A_ARCHIVE_SHA, "canonical-replays.jsonl.gz",
                stage="m2_none_buffer_hand", case_id="CASE_HAND_01",
                expected_engine="m2", expected_nlf_mode="none", expected_neighbor_mode="buffer",
                expected_workload=wrong_w,
            )
            self.assertFalse(ok_b10)
            self.assertIn("does not match expected_workload", diag_b10)

        if r112_man.is_file():
            # R113-B01 on R1.1.2 manifest (frozen P4b-2B MERGE replay)
            ok_2b, diag_2b, art_2b, _ = runner.verify_historical_artifact(
                r112_man, R112_MANIFEST_SHA, "canonical-replays-r1-1-2.jsonl.gz",
                workload_family="hand", case_id="CASE_HAND_01",
                expected_binary_sha256=P4B2B_BIN_SHA, expected_commit_sha=P4B2B_COMMIT_SHA,
                expected_engine="m2", expected_nlf_mode="none", expected_neighbor_mode="merge",
                expected_workload=hand0,
            )
            self.assertTrue(ok_2b, diag_2b)
            self.assertIsNotNone(art_2b)

            # Temporary manifest/artifact tampering checks (Section 11.2, originals untouched!)
            with tempfile.TemporaryDirectory() as td:
                tdp = Path(td)
                man_data = json.loads(r112_man.read_text(encoding="utf-8"))
                # R113-B04: Missing manifest SHA
                ok_b04, _, _, _ = runner.verify_historical_artifact(r112_man, "", "canonical-replays-r1-1-2.jsonl.gz")
                self.assertFalse(ok_b04)

                # R113-B06: Altered manifest copy vs trusted R112_MANIFEST_SHA
                bad_man_data = copy.deepcopy(man_data)
                bad_man_data["description"] = "tampered"
                bad_man = tdp / "bad_manifest.json"
                bad_man.write_text(json.dumps(bad_man_data), encoding="utf-8")
                ok_b06, diag_b06, _, _ = runner.verify_historical_artifact(bad_man, R112_MANIFEST_SHA, "canonical-replays-r1-1-2.jsonl.gz")
                self.assertFalse(ok_b06)
                self.assertIn("mismatch", diag_b06)

                # R113-B07: Altered artifact bytes against trusted unmodified manifest
                unmod_man = tdp / "manifest.json"
                unmod_man.write_bytes(r112_man.read_bytes())
                tampered_art = tdp / "canonical-replays-r1-1-2.jsonl.gz"
                tampered_art.write_bytes(b"corrupted-bytes")
                ok_b07, diag_b07, _, _ = runner.verify_historical_artifact(unmod_man, R112_MANIFEST_SHA, "canonical-replays-r1-1-2.jsonl.gz")
                self.assertFalse(ok_b07)
                self.assertIn("Artifact bytes tampered", diag_b07)

                # R113-B05: Recomputed manifest digest over tampered artifact (not in trusted anchors!)
                fake_entry_sha = hashlib.sha256(b"corrupted-bytes").hexdigest()
                recomp_man = copy.deepcopy(man_data)
                for f_item in recomp_man["files"]:
                    if f_item["path"] == "canonical-replays-r1-1-2.jsonl.gz":
                        f_item["sha256"] = fake_entry_sha
                        f_item["size_bytes"] = len(b"corrupted-bytes")
                recomp_bytes = json.dumps(recomp_man).encode("utf-8")
                recomp_man_path = tdp / "recomputed_manifest.json"
                recomp_man_path.write_bytes(recomp_bytes)
                recomp_sha = hashlib.sha256(recomp_bytes).hexdigest()
                ok_b05, diag_b05, _, _ = runner.verify_historical_artifact(recomp_man_path, recomp_sha, "canonical-replays-r1-1-2.jsonl.gz")
                self.assertFalse(ok_b05)
                self.assertIn("not in trusted anchors", diag_b05)

                # R113-B08: Artifact absent from trusted manifest
                ok_b08, diag_b08, _, _ = runner.verify_historical_artifact(r112_man, R112_MANIFEST_SHA, "nonexistent_artifact.jsonl.gz")
                self.assertFalse(ok_b08)
                self.assertIn("absent from trusted manifest", diag_b08)

    # --- Group C: Actual historical replay (R113-C01..C06) ---

    def test_r113_c01_to_c06_actual_historical_replay_and_counter_enforcement(self):
        b2a = _find_historical_binary("p4b2a-baseline-native")
        b2b = _find_historical_binary("p4b2b-release-native")
        w = self._c4_workload()

        # R113-C03: Current candidate binary cannot execute under historical replay path
        res_c03, _, acc_c03, rec_c03 = runner.run_verified_historical_binary(BINARY, "m2", w, neighbor_mode="merge")
        self.assertFalse(acc_c03)
        self.assertIsNone(rec_c03)
        self.assertIn("not an authorized frozen historical binary", res_c03.get("error_message", ""))

        if b2a is not None:
            # R113-C01: Genuine P4b-2A replay (legacy and buffer)
            for nbr in ("legacy", "buffer"):
                r2a, _, ok2a, rec2a = runner.run_verified_historical_binary(
                    b2a, "m2", w, neighbor_mode=nbr, expected_binary_sha256=P4B2A_BIN_SHA, expected_commit_sha=P4B2A_COMMIT_SHA,
                )
                self.assertTrue(ok2a, r2a.get("error_message"))
                self.assertIsNotNone(rec2a)

            # R113-C04: P4b-2A binary rejected if requested as MERGE
            r_c04, _, ok_c04, rec_c04 = runner.run_verified_historical_binary(b2a, "m2", w, neighbor_mode="merge")
            self.assertFalse(ok_c04)
            self.assertIsNone(rec_c04)
            self.assertIn("unauthorized", r_c04.get("error_message", ""))

            # R113-C05: Genuine historical expected negative (DOMAIN_ERROR & PREVALIDATION_ERROR)
            hand = runner.build_hand_workloads()
            h21 = next(x for x in hand if x["case_id"] == "CASE_HAND_21")
            r_h21, _, ok_h21, rec_h21 = runner.run_verified_historical_binary(b2a, "m2", h21, neighbor_mode="legacy")
            self.assertFalse(ok_h21)
            self.assertEqual(r_h21.get("error_category"), "DOMAIN_ERROR")
            self.assertIsNotNone(rec_h21)
            ok_v21, diag_v21, _ = runner.validate_historical_artifact_configuration(
                r_h21, rec_h21, "m2", "none", "legacy", is_success_run=False, workload=h21, native_exit_code=3,
            )
            self.assertTrue(ok_v21, diag_v21)

            h22 = next(x for x in hand if x["case_id"] == "CASE_HAND_22")
            r_h22, _, ok_h22, rec_h22 = runner.run_verified_historical_binary(b2a, "m2", h22, neighbor_mode="buffer")
            self.assertFalse(ok_h22)
            self.assertEqual(r_h22.get("error_category"), "PREVALIDATION_ERROR")
            self.assertIsNotNone(rec_h22)

        if b2b is not None:
            # R113-C02: Genuine P4b-2B replay (legacy, buffer, merge)
            for nbr in ("legacy", "buffer", "merge"):
                r2b, _, ok2b, rec2b = runner.run_verified_historical_binary(
                    b2b, "m2", w, neighbor_mode=nbr, expected_binary_sha256=P4B2B_BIN_SHA, expected_commit_sha=P4B2B_COMMIT_SHA,
                )
                self.assertTrue(ok2b, r2b.get("error_message"))
                self.assertIsNotNone(rec2b)

        # R113-C06: Profile-specific mandatory counter enforcement on both P4b-2A (legacy/buffer) and P4b-2B (legacy/buffer/merge)
        p2a_prof = runner.AUTHORIZED_HISTORICAL_BINARIES[P4B2A_BIN_SHA]
        p2b_prof = runner.AUTHORIZED_HISTORICAL_BINARIES[P4B2B_BIN_SHA]
        c_leg = {f: 0 for f in runner.NEIGHBOR_COUNTER_FIELDS}
        del c_leg["neighbor_buffer_retrieval_calls"]
        err_c06a = runner.validate_neighbor_counters(c_leg, {"neighbor_configuration_version": 1, "effective_neighbor_mode": "legacy"}, historical_profile=p2a_prof)
        self.assertIn("Missing required neighbor counter 'neighbor_buffer_retrieval_calls'", err_c06a)

        c_2b_buf = {f: 0 for f in runner.NEIGHBOR_COUNTER_FIELDS} # Missing 4 merge counters expected on P4b-2B transitional schema
        err_c06b = runner.validate_neighbor_counters(c_2b_buf, {"neighbor_configuration_version": 1, "effective_neighbor_mode": "buffer"}, historical_profile=p2b_prof)
        self.assertIn("Missing required neighbor counter", err_c06b)

        # P4b-2A forbids merge counter fields even if zero
        c_2a_extra = {f: 0 for f in runner.ALL_NEIGHBOR_COUNTER_FIELDS}
        err_c06c = runner.validate_neighbor_counters(c_2a_extra, {"neighbor_configuration_version": 1, "effective_neighbor_mode": "buffer"}, historical_profile=p2a_prof)
        self.assertIn("Forbidden neighbor counter", err_c06c)

    # --- Group D: Strict live protection invariants (R113-D06/D07) ---

    def test_r113_d06_d07_live_negative_boundaries(self):
        # R113-D06: Legitimate uninitialized v2 DOMAIN_ERROR preserved on live run
        early_v2 = {"status": "FAIL", "configuration_initialized": False, "neighbor_configuration_version": 2,
                    "error_category": "DOMAIN_ERROR", "exit_code": 3}
        ok_d06, diag_d06, _ = runner.validate_nlf_configuration("m2", "none", early_v2, False, "merge")
        self.assertTrue(ok_d06, diag_d06)
        # Early v1 DOMAIN_ERROR rejected on live run without verified provenance
        early_v1 = {**early_v2, "neighbor_configuration_version": 1}
        ok_v1, _, _ = runner.validate_nlf_configuration("m2", "none", early_v1, False, "merge")
        self.assertFalse(ok_v1)

        # R113-D07: Initialized negative with malformed counters rejected on live run
        hand = runner.build_hand_workloads()
        h22 = next(x for x in hand if x["case_id"] == "CASE_HAND_22")
        actual = runner.execute_contained_subprocess
        def corrupt_neg_counter(cmd, **kwargs):
            res = actual(cmd, **kwargs)
            out = Path(cmd[cmd.index("--output") + 1])
            body = json.loads(out.read_text())
            body["counters"]["neighbor_merge_linear_calls"] = None
            out.write_text(json.dumps(body))
            return res
        with patch.object(runner, "execute_contained_subprocess", side_effect=corrupt_neg_counter):
            res_d07, _, acc_d07 = runner.run_workload_on_binary(BINARY, "m2", h22, neighbor_mode="merge")
        self.assertFalse(acc_d07)
        self.assertFalse(res_d07.get("schema_valid"))
        self.assertIn("Invalid neighbor counter neighbor_merge_linear_calls", res_d07.get("schema_error", ""))
