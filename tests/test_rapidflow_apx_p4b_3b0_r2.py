import copy
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import p4b_activation as auth
from scripts import p4b_correctness_registry as refs
from scripts import p4b_experiment_session as session
from scripts import p4b_native_evidence as evidence
from scripts import replay_rapidflow_apx_p4b as replay
from scripts import run_rapidflow_apx_engines as eng_runner
from scripts import run_rapidflow_apx_p4b as p4b
from scripts import summarize_rapidflow_apx_p4b as statistics
from tests.test_rapidflow_apx_p4b_3b0_r1 import (
    ControlledNative,
    DURABLE_ARCHIVE,
    native_resource,
    reported_exit,
    write_attempt,
)

CANDIDATE_BINARY = p4b.CANDIDATE_R2_BINARY_PATH


def _skip_if_no_candidate():
    if not CANDIDATE_BINARY.is_file():
        raise unittest.SkipTest(f"R2 candidate binary not built at {CANDIDATE_BINARY}")


class TestP4b3b0R2(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol, cls.manifest, cls.schedule = p4b.load_frozen_plan()
        cls.plan = list(p4b.ordered_planned_slots(cls.schedule))
        cls.workloads = {row["workload_id"]: row for row in cls.manifest["workloads"]}
        cls.registry = {(r["workload_id"], r["variant_id"]): r for r in session.read(refs.REGISTRY_PATH)["entries"]}
        if DURABLE_ARCHIVE.is_file():
            store = tempfile.TemporaryDirectory(prefix="p4b-r2-refs-")
            cls.addClassCleanup(store.cleanup)
            cls.store = Path(store.name)
            refs.unpack_references(DURABLE_ARCHIVE, cls.store)
        else:
            cls.store = None

    def slot(self, wid="P4B_CONT_01", variant="BARE", purpose="primary", repeat=None):
        return next(
            s for s in self.plan
            if s["workload_id"] == wid and s["variant_id"] == variant
            and s["slot_purpose"] == purpose and (repeat is None or s["repeat_id"] == repeat)
        )

    def adapter(self, mutation=None):
        if self.store is None:
            self.skipTest("Durable archive unavailable")
        return ControlledNative(self.registry, self.store, self.protocol["resource_policy"], mutation)

    def direct(self, slot, mutation=None):
        with tempfile.TemporaryDirectory() as tmp:
            return write_attempt(Path(tmp), self.protocol, self.workloads[slot["workload_id"]], slot, self.adapter(mutation))

    def test_01_native_suites_cover_r2_findings(self):
        _skip_if_no_candidate()
        for flag in (
            ["--test-empty-frontier"],
            ["--test-p4b-measurement"],
            ["--test-p4b-neighbor", "graph"],
            ["--test-p4b", "transactions"],
        ):
            with self.subTest(flag=flag):
                res = subprocess.run([str(CANDIDATE_BINARY), *flag], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, res.stdout + "\n" + res.stderr)

    def test_02_new_find_01_real_native_diagnostic_work_cap_and_disc07(self):
        _skip_if_no_candidate()
        cand_sha = p4b.compute_sha256(CANDIDATE_BINARY)
        cont1_path = p4b.REPO_ROOT / self.workloads["P4B_CONT_01"]["fixture_path"]
        disc07_path = p4b.REPO_ROOT / self.workloads["DISC_07_K0"]["fixture_path"]

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # Bounded P4B_CONT_01 witness at max_work=2 under diagnostic mode
            out_cont = td_path / "cont01_diag_mw2.json"
            proc_cont = p4b.execute_p4b_native_slot(
                CANDIDATE_BINARY, cont1_path, "BARE", "diagnostic", out_cont,
                max_work=2, expected_binary_sha256=cand_sha,
            )
            self.assertEqual(proc_cont["exit_code"], 4)
            self.assertEqual(proc_cont["censoring_reason"], "WORK_BUDGET_EXCEEDED")
            native = proc_cont["output_data"]
            self.assertEqual(native["error_category"], "WORK_BUDGET_EXCEEDED")
            fidx = native["first_failing_step_index"]
            sp = native["steps"][fidx]["profiling"]
            self.assertGreater(sp["search_ns"], 0)
            self.assertLessEqual(sp["neighbor_retrieval_ns"], sp["search_ns"])
            self.assertLessEqual(sp["search_core_ns"], sp["search_ns"])
            self.assertLessEqual(sp["search_ns"], sp["step_total_ns"])
            evidence.profile_timings(native, native["macro_timing"], proc_cont["outer_wall_ns"])

            # Real DISC_07_K0 diagnostic execution under frozen cap=500,000
            out_d07 = td_path / "disc07_diag.json"
            proc_d07 = subprocess.run(
                [
                    str(CANDIDATE_BINARY), "--engine", "m2", "--nlf-mode", "none", "--neighbor-mode", "legacy",
                    "--max-work", "500000", "--execution-purpose", "diagnostic", "--wall-timeout-seconds", "60",
                    "--workload", str(disc07_path), "--output", str(out_d07),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(proc_d07.returncode, 4, proc_d07.stderr)
            d07 = json.loads(out_d07.read_text())
            f2 = d07["first_failing_step_index"]
            sp2 = d07["steps"][f2]["profiling"]
            self.assertGreater(sp2["neighbor_retrieval_ns"], 0)
            self.assertLessEqual(sp2["neighbor_retrieval_ns"], sp2["search_ns"])
            self.assertLessEqual(sp2["search_ns"], sp2["step_total_ns"])
            evidence.profile_timings(d07, d07["macro_timing"], 60 * 10**9)

    def test_03_new_find_02_resource_failure_contract_end_to_end(self):
        _skip_if_no_candidate()
        cand_sha = p4b.compute_sha256(CANDIDATE_BINARY)
        cont1_wl = json.loads((p4b.REPO_ROOT / self.workloads["P4B_CONT_01"]["fixture_path"]).read_text())

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # 1. Fault-injection ALLOCATION_FAILURE_MUTATION must exit 6 and NEVER become MEMORY_LIMIT_FAILURE
            fault_wl = copy.deepcopy(cont1_wl)
            fault_wl["updates"][0]["fault"] = "ALLOCATION_FAILURE_MUTATION"
            fwl_path = td_path / "fault_alloc.json"
            fwl_path.write_text(json.dumps(fault_wl))
            fout_path = td_path / "fault_alloc_out.json"
            proc_f = p4b.execute_p4b_native_slot(
                CANDIDATE_BINARY, fwl_path, "BARE", "correctness", fout_path,
                expected_binary_sha256=cand_sha,
            )
            self.assertEqual(proc_f["exit_code"], 6)
            self.assertEqual(proc_f["censoring_reason"], "EXECUTION_FAILURE")
            self.assertEqual(proc_f["output_data"]["error_category"], "FAULT_INJECTED")

            # 2. Real std::bad_alloc under 512 MiB RLIMIT_AS -> exit 5 / RESOURCE_LIMIT_ERROR / std::bad_alloc
            verts = [{"id": i, "label": 1} for i in range(1_200_000)]
            oom_wl = {
                "case_id": "R2_REAL_512M_INIT_OOM",
                "k": 0,
                "query": {
                    "vertices": [{"id": 0, "label": 1}, {"id": 1, "label": 1}, {"id": 2, "label": 1}],
                    "edges": [{"u": 0, "v": 1, "label": 1}, {"u": 1, "v": 2, "label": 1}, {"u": 0, "v": 2, "label": 1}],
                },
                "initial_graph": {"vertices": verts, "edges": []},
                "updates": [{"op": "+", "u": 0, "v": 1, "label": 1}],
            }
            oom_path = td_path / "oom_wl.json"
            oom_path.write_bytes(json.dumps(oom_wl, separators=(",", ":")).encode())
            oom_out = td_path / "oom_out.json"
            oom_proc = p4b.execute_p4b_native_slot(
                CANDIDATE_BINARY, oom_path, "BARE", "correctness", oom_out,
                expected_binary_sha256=cand_sha,
            )
            self.assertEqual(oom_proc["exit_code"], 5)
            self.assertEqual(oom_proc["censoring_reason"], "MEMORY_LIMIT_FAILURE")
            self.assertEqual(oom_proc["output_data"]["error_category"], "RESOURCE_LIMIT_ERROR")
            self.assertEqual(oom_proc["output_data"]["error_message"], "std::bad_alloc")

        # 3. Validate in-step RESOURCE_EXHAUSTED, startup pre-step OOM, and negative controls through _validate_native
        slot_diag = self.slot(purpose="diagnostic")
        def in_step_oom(_, native, process):
            native["status"] = "FAIL"
            native["error_category"] = "RESOURCE_LIMIT_ERROR"
            native["error_message"] = "std::bad_alloc"
            native["first_failing_step_index"] = 0
            native["first_failure_category"] = "RESOURCE_LIMIT_ERROR"
            native["first_failure_message"] = "std::bad_alloc"
            native["execution"].update(completed_steps=0, committed_steps=0, failed_steps=1, attempted_steps=1, delivered_steps=1, unattempted_steps=7)
            s0 = native["steps"][0]
            s0["status"] = "RESOURCE_EXHAUSTED"
            s0["commit_status"] = "NOT_COMMITTED"
            s0["error_category"] = "RESOURCE_LIMIT_ERROR"
            s0["error"] = "std::bad_alloc"
            s0["entered_tuples"] = []
            s0["exited_tuples"] = []
            s0["step_counters"]["discarded_emissions"] = s0["step_counters"]["unique_emissions"]
            s0["step_counters"]["committed_emissions"] = 0
            for s in native["steps"][1:]:
                s["status"] = "UNATTEMPTED"
                s["commit_status"] = "NOT_COMMITTED"
                s["error_category"] = "NONE"
                s["entered_tuples"] = []
                s["exited_tuples"] = []
                s.pop("step_counters", None)
                s.pop("profiling", None)
            native["counters"] = dict(s0["step_counters"])
            native["profiling"]["cumulative"] = dict(s0["profiling"])
            for k, v in s0["profiling"].items():
                native["profiling"][f"cumulative_{k}"] = v
            native["profiling"]["native_step_total_ns"] = s0["profiling"]["step_total_ns"]
            reported_exit(process, 5, "MEMORY_LIMIT_FAILURE")
            return native, b""

        res_in_step = self.direct(slot_diag, in_step_oom)
        self.assertEqual((res_in_step["attempt_outcome"], res_in_step["censoring_reason"]), ("CENSORED", "MEMORY_LIMIT_FAILURE"), res_in_step)

        # In-step RESOURCE_EXHAUSTED missing step_counters or missing diagnostic profiling MUST fail
        def in_step_missing_counters(_, native, process):
            n, err = in_step_oom(_, native, process)
            n["steps"][0].pop("step_counters")
            return n, err

        bad_cnt = self.direct(slot_diag, in_step_missing_counters)
        self.assertEqual(bad_cnt["attempt_outcome"], "INVALID")

        def in_step_missing_prof(_, native, process):
            n, err = in_step_oom(_, native, process)
            n["steps"][0].pop("profiling")
            return n, err

        bad_prof = self.direct(slot_diag, in_step_missing_prof)
        self.assertEqual(bad_prof["attempt_outcome"], "INVALID")

        # Fake std::bad_alloc in FAULT_INJECTED or bare stderr exit 6 MUST NOT pass
        def fake_stderr(_, native, process):
            reported_exit(process, 6, "MEMORY_LIMIT_FAILURE")
            return b"", b"std::bad_alloc"

        self.assertEqual(self.direct(self.slot(), fake_stderr)["attempt_outcome"], "INVALID")

    def test_04_new_find_05_structured_malformed_evidence(self):
        slot = self.slot()
        entry = self.workloads[slot["workload_id"]]
        backend = self.adapter()
        base_process = backend.execute(slot, entry, Path("/tmp")) if False else None

        mutations = [
            ("process_null", b"null", b"{}"),
            ("process_list", b"[]", b"{}"),
            ("process_str", b'"bad"', b"{}"),
            ("supervisor_null", json.dumps({"supervisor": None}).encode(), b"{}"),
            ("supervisor_list", json.dumps({"supervisor": []}).encode(), b"{}"),
            ("supervisor_str", json.dumps({"supervisor": "str"}).encode(), b"{}"),
        ]
        for name, proc_bytes, nav_bytes in mutations:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                (p / "process.json").write_bytes(proc_bytes)
                (p / "native-output.json").write_bytes(nav_bytes)
                (p / "invocation.json").write_bytes(b"{}")
                (p / "stderr.log").write_bytes(b"")
                res = session.validate_attempt(slot, entry, {"slot_id": slot["slot_id"]}, backend, p)
                self.assertEqual(res["attempt_outcome"], "INVALID", res)
                self.assertEqual(res["censoring_reason"], "MEASUREMENT_ERROR", res)

        # Nested object mutations on valid baseline
        nested_mutations = [
            ("macro_null", lambda n, p: n.__setitem__("macro_timing", None)),
            ("macro_list", lambda n, p: n.__setitem__("macro_timing", [])),
            ("exec_null", lambda n, p: n.__setitem__("execution", None)),
            ("step_null", lambda n, p: n["steps"].__setitem__(0, None)),
            ("caps_null", lambda n, p: p["supervisor"].__setitem__("resource_caps", None)),
        ]
        for name, mut in nested_mutations:
            def fn(_, native, process, _mut=mut):
                _mut(native, process)
                return native, b""
            with self.subTest(case=name):
                res = self.direct(slot, fn)
                self.assertEqual(res["attempt_outcome"], "INVALID", res)

    def test_05_new_find_06_interrupted_parser_and_profiling_purpose(self):
        bad_prefixes = [
            b'{"case_id": "X", "steps": [{"step_index": 0}, ]',
            b'{"case_id": "X", "steps": [{"step_index": 0}, ]}',
            b'{"case_id": "X", }',
            b'{"case_id": "X", "steps": [{"step_index": 0}] garbage',
            b'{"case_id": "X", "steps": [{"step_index": 0}, {"step_index": 1,,',
            b'{"case_id": "X", "case_id": "X", "steps": [',
            b'{"case_id": "\xff"',
        ]
        for idx, raw in enumerate(bad_prefixes):
            with self.subTest(idx=idx, raw=raw):
                with self.assertRaises(p4b.LedgerIntegrityError):
                    evidence.interrupted_output(raw)

        # Genuine real-native byte truncation prefixes across diagnostic & performance outputs
        _skip_if_no_candidate()
        cont1_path = p4b.REPO_ROOT / self.workloads["P4B_CONT_01"]["fixture_path"]
        with tempfile.TemporaryDirectory() as td:
            out_diag = Path(td) / "diag.json"
            proc = p4b.execute_p4b_native_slot(
                CANDIDATE_BINARY, cont1_path, "BARE", "diagnostic", out_diag,
                expected_binary_sha256=p4b.compute_sha256(CANDIDATE_BINARY),
            )
            self.assertEqual(proc["exit_code"], 0)
            raw_bytes = out_diag.read_bytes().rstrip(b"\n")
            steps_idx = raw_bytes.index(b'"steps":[')
            for cut in (0, 1, 15, steps_idx + 12, steps_idx + 80, len(raw_bytes) - 3):
                with self.subTest(cut=cut):
                    parsed, extent = evidence.interrupted_output(raw_bytes[:cut])
                    self.assertEqual(extent, "ABSENT" if cut == 0 else "PARTIAL")
                    self.assertIsInstance(parsed, dict)

            # Performance interrupted output containing leaked root or step profiling MUST fail
            slot_perf = self.slot(purpose="primary")
            entry = self.workloads["P4B_CONT_01"]
            wl = json.loads(cont1_path.read_text())
            ref = session.read(Path(td) / "ref.json") if False else json.loads(
                refs.read_reference(self.store, self.registry["P4B_CONT_01", "BARE"])
            )
            step0 = {k: v for k, v in ref["steps"][0].items() if k not in ("graph_state", "codec_state")}
            step0["profiling"] = {
                "step_total_ns": 100, "update_maintenance_ns": 10, "search_ns": 50,
                "neighbor_retrieval_ns": 10, "search_core_ns": 40, "tuple_commit_ns": 5, "tuple_sink_ns": 5,
            }
            leaked_step_prefix = (
                p4b.canonical_json_bytes({"case_id": ref["case_id"]}).rstrip()[:-1]
                + b',"steps":[' + json.dumps(step0).encode() + b',{"step_index":'
            )
            with self.assertRaisesRegex(p4b.LedgerIntegrityError, "unexpected step profiling"):
                session._check_interrupted_output(
                    leaked_step_prefix, slot_perf, entry, wl, p4b.ALL_VARIANTS["BARE"],
                    ref, self.protocol["resource_policy"], 10**9, self.adapter(),
                )

    def test_06_source_lock_covers_comparator_oracle_replay_and_contract(self):
        for rel in (
            "scripts/canonical_output_comparator.py",
            "scripts/p4a_evidence_contract.py",
            "scripts/replay_rapidflow_apx_p4b.py",
            "scripts/dstar_oracle.py",
        ):
            self.assertIn(rel, auth.SOURCE_PATHS)
            self.assertTrue((p4b.REPO_ROOT / rel).is_file())

    def test_07_real_native_bounded_session_resume_and_replay(self):
        _skip_if_no_candidate()
        cand_sha = p4b.compute_sha256(CANDIDATE_BINARY)
        cont1_entry = self.workloads["P4B_CONT_01"]
        cont1_path = p4b.REPO_ROOT / cont1_entry["fixture_path"]
        head_rev = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=p4b.REPO_ROOT, text=True).strip()

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # Generate real candidate correctness reference for P4B_CONT_01 BARE
            ref_file = td_path / "cand_cont01_bare.json"
            rec_ref = p4b.execute_p4b_native_slot(
                CANDIDATE_BINARY, cont1_path, "BARE", "correctness", ref_file,
                expected_binary_sha256=cand_sha,
            )
            self.assertEqual(rec_ref["exit_code"], 0)
            raw_ref = ref_file.read_bytes()
            gz_ref = gzip.compress(raw_ref, mtime=0)
            store = td_path / "store"
            (store / "pre-r09/P4B_CONT_01").mkdir(parents=True)
            (store / "pre-r09/P4B_CONT_01/BARE.json.gz").write_bytes(gz_ref)
            cand_registry = {
                ("P4B_CONT_01", "BARE"): {
                    **self.registry["P4B_CONT_01", "BARE"],
                    "binary_sha256": cand_sha,
                    "native_source_commit": head_rev,
                    "compressed_output_sha256": p4b.sha256_bytes(gz_ref),
                    "raw_output_sha256": p4b.sha256_bytes(raw_ref),
                    "raw_output_size": len(raw_ref),
                }
            }

            # 1. Real candidate performance + diagnostic completed slots via session() -> read-only replay
            sess1 = td_path / "sess_completed"
            backend1 = session.NativeBackend(
                CANDIDATE_BINARY, cand_registry, store, self.protocol["resource_policy"],
                binary_sha=cand_sha, source=head_rev,
            )
            slots1 = [self.slot(purpose="primary", repeat=1), self.slot(purpose="diagnostic")]
            res1 = session.session(sess1, self.protocol, self.manifest, slots1, backend1, execute=True)
            self.assertEqual(res1["state"], "SESSION_COMPLETED", res1)
            self.assertEqual([r["attempt_outcome"] for r in res1["records"]], ["COMPLETED", "COMPLETED"])
            self.assertEqual(session.session(sess1, self.protocol, self.manifest, slots1, backend1), res1)

            # 2. Real candidate diagnostic WORK_BUDGET_EXCEEDED end-to-end on real DISC_07_K0 (R2 NEW-FIND-01 E2E closure!)
            d07_entry = self.workloads["DISC_07_K0"]
            d07_path = p4b.REPO_ROOT / d07_entry["fixture_path"]
            ref_d07_file = td_path / "cand_disc07_bare.json"
            rec_d07_ref = p4b.execute_p4b_native_slot(
                CANDIDATE_BINARY, d07_path, "BARE", "correctness", ref_d07_file,
                expected_binary_sha256=cand_sha,
            )
            self.assertEqual(rec_d07_ref["exit_code"], 4)
            raw_d07 = ref_d07_file.read_bytes()
            gz_d07 = gzip.compress(raw_d07, mtime=0)
            (store / "pre-r09/DISC_07_K0").mkdir(parents=True)
            (store / "pre-r09/DISC_07_K0/BARE.json.gz").write_bytes(gz_d07)
            cand_registry["DISC_07_K0", "BARE"] = {
                **self.registry["DISC_07_K0", "BARE"],
                "binary_sha256": cand_sha,
                "native_source_commit": head_rev,
                "compressed_output_sha256": p4b.sha256_bytes(gz_d07),
                "raw_output_sha256": p4b.sha256_bytes(raw_d07),
                "raw_output_size": len(raw_d07),
            }

            class WitnessCandidateBackend(session.NativeBackend):
                def authorize(self, slot):
                    pass
                def execute(self, slot, entry, path):
                    out = p4b.execute_p4b_native_slot(
                        self.binary, p4b.REPO_ROOT / entry["fixture_path"], slot["variant_id"],
                        "correctness" if False else slot["execution_purpose"], path / "native-output.json",
                        timeout_s=self.policy["wall_timeout_seconds"], as_limit_mib=self.policy["address_space_limit_mib"],
                        max_work=self.policy["logical_work_cap"], max_nlf_memory_mib=self.policy["max_nlf_memory_mib"],
                        expected_binary_sha256=self.binary_sha, expected_fixture_sha256=entry["fixture_sha256"],
                        evidence={
                            "environment": auth.NATIVE_ENV,
                            "on_start": lambda pid, argv: session.write(path / "invocation.json", {
                                "pid": pid, "argv": argv, "environment": auth.NATIVE_ENV}),
                            "save_streams": lambda out, err: (
                                session.publish(path / "stdout.log", out), session.publish(path / "stderr.log", err)),
                        },
                    )
                    return {k: v for k, v in out.items() if k not in ("output_data", "raw_stdout", "raw_stderr")}

            sess2 = td_path / "sess_diag_budget"
            backend2 = WitnessCandidateBackend(
                CANDIDATE_BINARY, cand_registry, store, self.protocol["resource_policy"],
                binary_sha=cand_sha, source=head_rev,
            )
            slots2 = [self.slot(wid="DISC_07_K0", purpose="diagnostic")]
            with patch.object(p4b, "BOUNDED_FIXTURE_SHA256", d07_entry["fixture_sha256"]):
                res2 = session.session(sess2, self.protocol, self.manifest, slots2, backend2, execute=True)
                self.assertEqual(res2["state"], "SESSION_COMPLETED", res2)
                self.assertEqual(res2["records"][0]["attempt_outcome"], "CENSORED")
                self.assertEqual(res2["records"][0]["censoring_reason"], "WORK_BUDGET_EXCEEDED")
                self.assertEqual(session.session(sess2, self.protocol, self.manifest, slots2, backend2), res2)

    def test_08_full_1450_synthetic_rehearsal_isolation(self):
        from tests.test_rapidflow_apx_p4b_statistics import _make_synthetic_eligible_ledger
        synth = {r["slot_id"]: r for r in _make_synthetic_eligible_ledger()}
        calls = []

        def fake_sim(slot):
            calls.append(slot["slot_id"])
            return synth.get(
                slot["slot_id"],
                {"evidence_kind": p4b.SYNTHETIC_KIND, "attempt_outcome": "COMPLETED", "measurement_eligible": False},
            )

        with tempfile.TemporaryDirectory() as td, patch.object(p4b.subprocess, "Popen") as popen_mock:
            root = Path(td)
            res = session.session(root, self.protocol, self.manifest, self.plan, session.SyntheticBackend(fake_sim), execute=True)
            self.assertEqual(res["state"], "SESSION_COMPLETED")
            self.assertEqual(len(calls), 1450)
            self.assertEqual(len(res["records"]), 1450)
            self.assertTrue(all(
                r["synthetic_test_fixture"] is True and r["evidence_kind"] == p4b.SYNTHETIC_KIND
                for r in res["records"]
            ))
            popen_mock.assert_not_called()
            summary = statistics.analyze_p4b_ledger(res["records"], allow_synthetic_mode=True)
            self.assertEqual(summary["analysis_mode"], "SYNTHETIC_FIXTURE_VERIFICATION")
            with self.assertRaisesRegex(p4b.LedgerIntegrityError, "NOT_AUTHORIZED"):
                statistics.analyze_p4b_ledger(res["records"])


if __name__ == "__main__":
    unittest.main()
