"""RED/GREEN evidence checks and production-format synthetic execution fixtures."""

import copy
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from functools import lru_cache
from unittest.mock import patch

from scripts import p4b_activation as auth
from scripts import p4b_correctness_registry as refs
from scripts import p4b_experiment_session as session
from scripts import p4b_native_evidence as evidence
from scripts import run_rapidflow_apx_p4b as runner
from scripts import summarize_rapidflow_apx_p4b as statistics
from tests.test_rapidflow_apx_p4b_3b0 import DURABLE_ARCHIVE


def add_profile(native):
    macro = native["macro_timing"]
    root = {field: macro[field] for field in ("input_parsing_ns", "graph_init_ns", "query_prep_ns", "streaming_loop_wall_ns")}
    root.update(enabled=True, streaming_exec_ns=macro["streaming_loop_wall_ns"],
                pre_output_wall_ns=2500, total_wall_ns=2500)
    total = dict.fromkeys(evidence.STEP_METRICS, 0)
    for step in native["steps"]:
        if step["status"] == "UNATTEMPTED" or step["error_category"] == "RESOURCE_LIMIT_ERROR":
            continue
        profile = dict(step_total_ns=12, update_maintenance_ns=6, search_ns=8, neighbor_retrieval_ns=3,
                       search_core_ns=5, tuple_commit_ns=4, tuple_sink_ns=4)
        step["profiling"] = profile
        for key, value in profile.items():
            total[key] += value
    root["cumulative"] = total
    root["native_step_total_ns"] = total["step_total_ns"]
    root.update({f"cumulative_{k}":v for k,v in total.items()})
    native["profiling"] = root


class ControlledNative(session.NativeBackend):
    """Test-only child with production-format files and non-confirmatory identity."""
    origin = runner.SYNTHETIC_KIND

    def __init__(self, registry, store, policy, mutation=None):
        super().__init__(runner.DEFAULT_BINARY_PATH.resolve(), registry, store, policy)
        self.mutation = mutation
        self.calls = []

    @lru_cache(maxsize=6)
    def _reference(self, key):
        return runner.load_json_bytes(refs.read_reference(self.store, self.registry[key]))

    def authorize(self, slot):
        if slot["variant_id"] not in runner.ALL_VARIANTS:
            raise AssertionError("Unknown test variant")

    def projected(self, slot):
        key = slot["workload_id"], slot["variant_id"]
        reference = self._reference(key)
        native = {k: copy.deepcopy(v) for k,v in reference.items() if k not in ("steps","macro_timing","profiling")}
        native["steps"] = [{k:copy.deepcopy(v) for k,v in step.items() if k not in ("graph_state","codec_state","profiling")}
                           for step in reference["steps"]]
        diagnostic = slot["execution_purpose"] == "diagnostic"
        native.update(execution_purpose=slot["execution_purpose"], deep_diagnostics_enabled=False,
                      profiling_enabled=diagnostic)
        native["macro_timing"] = dict(clock_source="std::chrono::steady_clock", streaming_loop_wall_ns=2000,
            input_parsing_ns=70, initialization_wall_ns=200, graph_init_ns=60, query_prep_ns=60,
            engine_and_nlf_init_ns=45, reporting_wall_ns=100, child_vm_hwm_kib=1024,
            child_vm_hwm_bytes=1024**2, child_rss_method="linux_proc_self_status_vmhwm")
        if diagnostic:
            add_profile(native)
        return native

    def execute(self, slot, entry, path):
        self.calls.append(slot["slot_id"])
        native = self.projected(slot)
        reference = self.registry[slot["workload_id"], slot["variant_id"]]
        exit_code = reference["native_exit_code"]
        mode = runner.ALL_VARIANTS[slot["variant_id"]]
        path = path.resolve()
        out = path/"native-output.json"
        argv = [str(self.binary), "--engine", mode["engine"], "--nlf-mode", mode["nlf_mode"],
                "--neighbor-mode", mode["neighbor_mode"], "--max-nlf-memory-mib", "256", "--max-work",
                "500000", "--execution-purpose", slot["execution_purpose"], "--wall-timeout-seconds", "60",
                "--workload", str(runner.REPO_ROOT/entry["fixture_path"]), "--output", str(out)]
        supervisor = dict(pid=12345, wait_status=exit_code << 8, deadline_fired=False,
            started_monotonic_ns=1000, reaped_monotonic_ns=11000, wait4_user_seconds=1e-6,
            wait4_system_seconds=1e-6, wait4_maxrss_kib=1024, resource_caps=dict(timeout_seconds=60.0,
                address_space_mib=512, max_work=500000, max_nlf_memory_mib=256),
            post_execution_identity=dict(binary_sha256=self.binary_sha, fixture_sha256=entry["fixture_sha256"]))
        process = dict(exit_code=exit_code, command=argv, process_signal=None, outer_wall_ns=10000,
            user_cpu_ns=1000, system_cpu_ns=1000, wait4_ru_maxrss_bytes=1024**2, supervisor=supervisor,
            peak_rss_original_unit="KiB", peak_rss_original_value=1024, peak_rss_bytes=1024**2,
            resource_measurement_method="linux_proc_self_status_vmhwm", binary_sha256=self.binary_sha,
            fixture_sha256=entry["fixture_sha256"], variant_id=slot["variant_id"],
            execution_purpose=slot["execution_purpose"],
            censoring_reason="WORK_BUDGET_EXCEEDED" if exit_code == 4 else "NONE")
        stderr = b""
        if self.mutation is not None:
            native, stderr = self.mutation(slot, native, process)
        raw = runner.canonical_json_bytes(native) if isinstance(native, dict) else native
        if raw is not None:
            session.publish(out, raw)
        process.update(output_size_bytes=len(raw or b""),
                       output_sha256=runner.sha256_bytes(raw) if raw is not None else None)
        session.write(path/"invocation.json", dict(pid=12345, argv=argv, environment=auth.NATIVE_ENV))
        session.publish(path/"stdout.log", b"")
        session.publish(path/"stderr.log", stderr)
        return process


def external_timeout(native, process, raw=b""):
    process.update(exit_code=-signal.SIGKILL, process_signal=signal.SIGKILL,
                   resource_measurement_method="linux_wait4_rusage_ru_maxrss", outer_wall_ns=60_000_000_000,
                   censoring_reason="TIMEOUT")
    process["supervisor"].update(wait_status=signal.SIGKILL, deadline_fired=True,
                                 reaped_monotonic_ns=1000+process["outer_wall_ns"])
    return raw, b""


def reported_exit(process, code, reason):
    process.update(exit_code=code, censoring_reason=reason)
    process["supervisor"]["wait_status"] = code << 8


def native_resource(native, process):
    # Pre-execution exception after identity capture and before execute_step.
    count = len(native["steps"])
    for index, step in enumerate(native["steps"]):
        step.pop("step_counters", None)
        step.pop("profiling", None)
        step.update(status="ERROR" if index == 0 else "UNATTEMPTED", commit_status="NOT_COMMITTED",
                    error_category="RESOURCE_LIMIT_ERROR" if index == 0 else "NONE",
                    error="std::bad_alloc" if index == 0 else "", entered_tuples=[], exited_tuples=[])
    native["execution"].update(attempted_steps=1, delivered_steps=1, unattempted_steps=count-1,
                               committed_steps=0, completed_steps=0, failed_steps=1)
    initialization_counter = "nlf_index_initialization_cells"
    native["counters"] = {key:(value if key in ("max_work",initialization_counter) else 0)
                          for key,value in native["counters"].items()}
    native.update(status="FAIL", error_category="RESOURCE_LIMIT_ERROR", error_message="std::bad_alloc",
                  first_failing_step_index=0, first_failure_category="RESOURCE_LIMIT_ERROR",
                  first_failure_message="std::bad_alloc")
    reported_exit(process, 5, "MEMORY_LIMIT_FAILURE")


def write_attempt(root, protocol, entry, slot, adapter):
    header = session._plan_identity(protocol, [slot], adapter, root)
    identity = session._identity(slot, protocol, header)
    process = adapter.execute(slot, entry, root)
    session.write(root/"process.json", process)
    return session.validate_attempt(slot, entry, identity, adapter, root)


class TestProductionEvidenceR1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.plan = list(runner.ordered_planned_slots(cls.schedule))
        cls.workloads = {row["workload_id"]:row for row in cls.manifest["workloads"]}
        cls.registry = {(r["workload_id"],r["variant_id"]):r for r in session.read(refs.REGISTRY_PATH)["entries"]}
        store = tempfile.TemporaryDirectory(prefix="p4b-r1-refs-")
        cls.addClassCleanup(store.cleanup)
        cls.store = Path(store.name)
        refs.unpack_references(DURABLE_ARCHIVE, cls.store)

    def slot(self, wid="P4B_CONT_01", variant="BARE", purpose="primary", repeat=None):
        return next(s for s in self.plan if s["workload_id"] == wid and s["variant_id"] == variant
                    and s["slot_purpose"] == purpose and (repeat is None or s["repeat_id"] == repeat))

    def adapter(self, mutation=None):
        return ControlledNative(self.registry, self.store, self.protocol["resource_policy"], mutation)

    def direct(self, slot, mutation=None):
        with tempfile.TemporaryDirectory() as tmp:
            return write_attempt(Path(tmp), self.protocol, self.workloads[slot["workload_id"]], slot, self.adapter(mutation))

    def assert_invalid(self, row, reason):
        self.assertEqual(row["attempt_outcome"], "INVALID", row)
        self.assertIn(reason, row["error_detail"], row)
        self.assertFalse(row["measurement_eligible"])
        self.assertNotIn(row["censoring_reason"], runner.RESOURCE_CENSORS)

    def test_f01_provenance_precedes_all_outcomes(self):
        slot = self.slot()
        self.assertEqual(self.direct(slot)["attempt_outcome"], "COMPLETED")
        for raw in ("valid", "resource", "partial", "empty", "missing"):
            def mutation(_, native, process):
                process["censoring_reason"] = "PROVENANCE_ERROR"
                if raw == "resource":
                    native_resource(native, process)
                    process["censoring_reason"] = "PROVENANCE_ERROR"
                return {"partial":b'{"case_id":', "empty":b"", "missing":None}.get(raw, native), b""
            with self.subTest(raw=raw):
                row = self.direct(slot, mutation)
                self.assert_invalid(row, "Supervisor reported")
                self.assertEqual(row["censoring_reason"], "PROVENANCE_ERROR")
        for field in ("binary_sha256","fixture_sha256"):
            def mutate_identity(_, native, process):
                process[field] = process["supervisor"]["post_execution_identity"][field] = "a"*64
                return native, b""
            with self.subTest(field=field):
                self.assert_invalid(self.direct(slot, mutate_identity), field)
        def post_mismatch(_, native, process):
            process["supervisor"]["post_execution_identity"]["fixture_sha256"] = None
            return native, b""
        self.assert_invalid(self.direct(slot, post_mismatch), "post-execution")
        def inconsistent(_, native, process):
            reported_exit(process, 6, "EXECUTION_FAILURE")
            return native, b""
        self.assert_invalid(self.direct(slot, inconsistent), "NATIVE_PROCESS_STATUS_CONTRADICTION")

    def test_f01_runner_observes_actual_post_launch_fixture_disappearance(self):
        slot = {**self.slot(), "execution_purpose":"correctness"}
        entry = self.workloads[slot["workload_id"]]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            fixture = path/"copy.json"
            fixture.write_bytes((runner.REPO_ROOT/entry["fixture_path"]).read_bytes())
            binary = path/"controlled-child"
            binary.write_text(f"#!{sys.executable}\nimport pathlib,sys\n"
                              "pathlib.Path(sys.argv[sys.argv.index('--workload')+1]).unlink()\n")
            binary.chmod(0o700)
            process = runner.execute_p4b_native_slot(binary, fixture, "BARE", "correctness", path/"native-output.json",
                evidence={"environment":auth.NATIVE_ENV, "on_start":lambda pid,argv: None, "save_streams":lambda a,b:None})
            self.assertEqual(process["censoring_reason"], "PROVENANCE_ERROR")
            self.assertIsNone(process["supervisor"]["post_execution_identity"]["fixture_sha256"])
            session.write(path/"process.json",process)
            row = session.validate_attempt(slot,entry,{},self.adapter(),path)
            self.assertEqual(row["censoring_reason"], "PROVENANCE_ERROR")

    def test_f02_timing_boundary_and_independent_supervisor_mutations(self):
        positive = self.direct(self.slot(purpose="diagnostic"))
        self.assertEqual(positive["attempt_outcome"],"COMPLETED")
        for field,value in (("streaming_loop_wall_ns",100000),("graph_init_ns",100000),
                            ("reporting_wall_ns",-1),("input_parsing_ns","70"),("query_prep_ns",True),
                            ("graph_init_ns",201)):
            def timing(_, native, process):
                native["macro_timing"][field]=value
                return native,b""
            with self.subTest(field=field, value=value):
                self.assert_invalid(self.direct(self.slot(),timing), f"{field}" if value!=201 else "subphases")
        def missing(_, native, process):
            native["macro_timing"].pop("graph_init_ns")
            return native,b""
        self.assert_invalid(self.direct(self.slot(),missing),"macro_timing.graph_init_ns")
        def non_finite(_, native, process):
            raw = runner.canonical_json_bytes(native).replace(b'"graph_init_ns": 60', b'"graph_init_ns": NaN')
            self.assertIn(b"NaN",raw)
            return raw,b""
        self.assert_invalid(self.direct(self.slot(),non_finite),"NaN")
        def boundary(delta):
            def change(_, native, process):
                native["macro_timing"]["reporting_wall_ns"] = 7730 + delta
                return native,b""
            return change
        self.assertEqual(self.direct(self.slot(),boundary(0))["attempt_outcome"],"COMPLETED")
        self.assert_invalid(self.direct(self.slot(),boundary(1)),"disjoint native phases")
        def cumulative_timing(_, native, process):
            native["profiling"]["cumulative"]["search_ns"] += 1
            native["profiling"]["cumulative_search_ns"] += 1
            return native,b""
        self.assert_invalid(self.direct(self.slot(purpose="diagnostic"),cumulative_timing),"TIMING_CUMULATIVE_MISMATCH")
        def step_timing(_, native, process):
            native["steps"][0]["profiling"]["search_ns"] = 13
            return native,b""
        self.assert_invalid(self.direct(self.slot(purpose="diagnostic"),step_timing),"exceeds step_total_ns")
        for field, reason in (("outer_wall_ns","SUPERVISOR_WALL"), ("user_cpu_ns","SUPERVISOR_CPU"),
                              ("system_cpu_ns","SUPERVISOR_CPU"), ("wait4_ru_maxrss_bytes","SUPERVISOR_RSS")):
            def bound(_, native, process):
                process[field] += 1
                return native,b""
            with self.subTest(field=field):
                self.assert_invalid(self.direct(self.slot(),bound),reason)
        def censored_timing(_, native, process):
            native["macro_timing"]["streaming_loop_wall_ns"] = 100001
            return native,b""
        self.assert_invalid(self.direct(self.slot("DISC_07_K0"),censored_timing),"TIMING_BOUNDARY")

    def test_f03_structured_resource_controls_and_mutations(self):
        slot = self.slot()
        def structured(_, native, process):
            native_resource(native,process)
            return native,b""
        result = self.direct(slot,structured)
        self.assertEqual((result["attempt_outcome"],result["censoring_reason"]),("CENSORED","MEMORY_LIMIT_FAILURE"),result)
        cases = {
            "mode":(lambda n,p:n.update(effective_neighbor_mode="merge"),"effective_neighbor_mode"),
            "variant":(lambda n,p:p.update(variant_id="B_MERGE"),"PROCESS_ARTIFACT_MISMATCH"),
            "workload":(lambda n,p:n.update(case_id="invalid"),"case_id"),
            "policy":(lambda n,p:p["supervisor"]["resource_caps"].update(address_space_mib=513),"RESOURCE_POLICY_MISMATCH"),
            "limit":(lambda n,p:n["work_budget"].update(effective_limit=499999),"work_budget"),
            "failure_index":(lambda n,p:n.update(first_failing_step_index=1),"RESOURCE_FAILURE_STEP_MISMATCH"),
            "invalid_work":(lambda n,p:n["steps"][0].update(step_counters={"work_units":1}),"must not report engine counters"),
            "provenance":(lambda n,p:p.update(censoring_reason="PROVENANCE_ERROR"),"Supervisor reported"),
            "unsupported-outcome":(lambda n,p:p.update(censoring_reason="TIMEOUT"),"SUPERVISOR_OUTCOME_MISMATCH"),
        }
        for name,(change,reason) in cases.items():
            def mutation(_, native, process):
                native_resource(native,process)
                change(native,process)
                return native,b""
            with self.subTest(case=name):
                self.assert_invalid(self.direct(slot,mutation),reason)
        def success_lifecycle(_, native, process):
            native.update(error_category="RESOURCE_LIMIT_ERROR")
            reported_exit(process,5,"RESOURCE_UNAVAILABLE")
            return native,b""
        self.assert_invalid(self.direct(slot,success_lifecycle),"NATIVE_PROCESS_STATUS_CONTRADICTION")
        def unknown(_,native,process):
            native.update(status="FAIL",error_category="RESOURCE_NEAR_LIMIT")
            reported_exit(process,5,"RESOURCE_UNAVAILABLE")
            return native,b""
        self.assert_invalid(self.direct(slot,unknown),"SUPERVISOR_OUTCOME_MISMATCH")
        def malformed(_,native,process):
            reported_exit(process,5,"RESOURCE_UNAVAILABLE")
            return b'{"error_category":"RESOURCE_LIMIT_ERROR",broken}',b""
        self.assertEqual(self.direct(slot,malformed)["attempt_outcome"],"INVALID")

    def test_f03_configuration_unavailable_matches_native_fallback_contract(self):
        slot = self.slot(variant="A_STATIC")
        def fallback(_, native, process):
            baseline = self.adapter().projected(self.slot())
            baseline.update(requested_nlf_mode="static",configuration_status="REQUESTED_CONFIGURATION_UNAVAILABLE",
                            fallback_reason="NLF_INDEX_MEMORY_LIMIT")
            process["censoring_reason"]="CONFIGURATION_UNAVAILABLE"
            return baseline,b""
        valid = self.direct(slot,fallback)
        self.assertEqual((valid["attempt_outcome"],valid["censoring_reason"]),("CENSORED","CONFIGURATION_UNAVAILABLE"),valid)
        self.assertFalse(valid["measurement_eligible"])
        for key,value,reason in (("requested_nlf_mode","residual","requested_nlf_mode"),
                                 ("fallback_reason","UNKNOWN","fallback_reason"),
                                 ("effective_nlf_mode","static","effective 'none'")):
            def mutation(s,n,p):
                result,_=fallback(s,n,p)
                result[key]=value
                return result,b""
            with self.subTest(field=key):
                self.assert_invalid(self.direct(slot,mutation),reason)
        def nonzero(s,n,p):
            result,_=fallback(s,n,p)
            reported_exit(p,5,"CONFIGURATION_UNAVAILABLE")
            return result,b""
        self.assert_invalid(self.direct(slot,nonzero),"NATIVE_PROCESS_STATUS_CONTRADICTION")

    def test_f03_external_resource_and_partial_output(self):
        slot = self.slot()
        def timeout(_,native,process):
            return external_timeout(native,process)
        expected = self.direct(slot,timeout)
        self.assertEqual((expected["attempt_outcome"],expected["raw_output_extent"]),("CENSORED","ABSENT"))
        def partial(s,n,p):
            step=n["steps"][0]
            root=runner.canonical_json_bytes({k:n[k] for k in ("case_id","raw_input_sha256","macro_timing")}).rstrip()
            raw=root[:-1]+b',"steps":['+runner.canonical_json_bytes(step).rstrip()+b',{"step_index":'
            return external_timeout(n,p,raw)
        self.assertEqual(self.direct(slot,partial)["raw_output_extent"],"PARTIAL")
        def partial_invalid(s,n,p):
            n["steps"][0]["op"]="invalid"
            return partial(s,n,p)
        self.assert_invalid(self.direct(slot,partial_invalid),"FROZEN_UPDATE_IDENTITY")
        def partial_time(s,n,p):
            n["macro_timing"]["streaming_loop_wall_ns"]=60_000_000_001
            return partial(s,n,p)
        self.assert_invalid(self.direct(slot,partial_time),"TIMING_BOUNDARY")
        def missing_deadline(s,n,p):
            external_timeout(n,p)
            p["supervisor"]["deadline_fired"]=False
            return b"",b""
        self.assert_invalid(self.direct(slot,missing_deadline),"SUPERVISOR_OUTCOME_MISMATCH")
        def cap_install(_,n,p):
            reported_exit(p,5,"RESOURCE_UNAVAILABLE")
            p["resource_measurement_method"]="linux_wait4_rusage_ru_maxrss"
            return b"", b"Resource containment error: controlled failure"
        self.assertEqual(self.direct(slot,cap_install)["censoring_reason"],"RESOURCE_UNAVAILABLE")
        def bare_kill(_,n,p):
            p.update(exit_code=-signal.SIGKILL,process_signal=signal.SIGKILL,censoring_reason="ENVIRONMENT_INTERRUPTION")
            p["supervisor"]["wait_status"]=signal.SIGKILL
            return b"",b"std::bad_alloc"
        self.assertEqual(self.direct(slot,bare_kill)["attempt_outcome"],"INTERRUPTED")
        def unsupported_badalloc(_,n,p):
            reported_exit(p,8,"MEMORY_LIMIT_FAILURE")
            return b"",b"std::bad_alloc"
        self.assert_invalid(self.direct(slot,unsupported_badalloc),"No structured native result")

    def test_f04_every_emitted_update_matches_frozen_identity(self):
        slot = self.slot("DISC_07_K0")
        for wid in ("DISC_07_K0","DISC_07_K1"):
            control = self.direct(self.slot(wid))
            self.assertEqual((control["attempt_outcome"],control["censoring_reason"]),("CENSORED","WORK_BUDGET_EXCEEDED"))
        for field,value in (("op","invalid"),("u",100000),("v",100000),("label","1"),("step_index",12)):
            def mutation(_, n,p):
                step = next(x for x in n["steps"] if x["status"]=="UNATTEMPTED")
                step[field] = value if value != step[field] else str(value)+"x"
                return n,b""
            with self.subTest(field=field):
                self.assert_invalid(self.direct(slot,mutation),f"FROZEN_UPDATE_IDENTITY: step 11 {field}")
        def work_drift(_, native, process):
            native["steps"][10]["step_counters"]["work_units"] += 9
            native["counters"]["work_units"] += 9
            return native,b""
        self.assert_invalid(self.direct(slot,work_drift),"INVALID_WORK_BUDGET")
        mutations = {
            "duplicate":(lambda s:s.insert(12,copy.deepcopy(s[11])),"FROZEN_UPDATE_INVENTORY"),
            "add":(lambda s:s.append({"step_index":len(s)}),"FROZEN_UPDATE_INVENTORY"),
            "swap":(lambda s:s.__setitem__(slice(11,13),[s[12],s[11]]),"FROZEN_UPDATE_IDENTITY"),
            "missing":(lambda s:s.pop(),"FROZEN_UPDATE_INVENTORY"),
            "aborted_committed":(lambda s:s[10].update(commit_status="COMMITTED"),"commit"),
            "suffix_committed":(lambda s:s[11].update(status="SUCCESS",commit_status="COMMITTED"),"LIFECYCLE"),
            "failure_op":(lambda s:s[10].update(op="invalid"),"FROZEN_UPDATE_IDENTITY"),
            "prefix_type":(lambda s:s[0].update(u=True),"FROZEN_UPDATE_IDENTITY"),
            "aborted_endpoint":(lambda s:s[10].update(v=9999),"FROZEN_UPDATE_IDENTITY"),
            "reversed_endpoints":(lambda s:s[11].update(u=s[11]["v"],v=s[11]["u"]),"FROZEN_UPDATE_IDENTITY"),
        }
        for name,(fn,reason) in mutations.items():
            def mutation(_,n,p):
                fn(n["steps"])
                return n,b""
            with self.subTest(case=name):
                row=self.direct(slot,mutation)
                self.assert_invalid(row,reason)

    def test_all_findings_remain_invalid_after_journal_ingestion_and_resume(self):
        def provenance(_, n,p):
            p["censoring_reason"]="PROVENANCE_ERROR"
            n["macro_timing"]["streaming_loop_wall_ns"]=10*p["outer_wall_ns"]
            return n,b""
        def impossible(_, n,p):
            n["macro_timing"]["streaming_loop_wall_ns"]=10*p["outer_wall_ns"]
            return n,b""
        def resource(_, n,p):
            native_resource(n,p)
            n["configuration_status"]="WRONG_VARIANT"
            return n,b""
        def suffix(_, n,p):
            next(s for s in n["steps"] if s["status"]=="UNATTEMPTED")["op"]="invalid"
            return n,b""
        for selected,mutation,reason in ((self.slot(),provenance,"Supervisor reported"),
            (self.slot(),impossible,"TIMING_BOUNDARY"),(self.slot(),resource,"Unknown configuration status"),
            (self.slot("DISC_07_K0"),suffix,"FROZEN_UPDATE_IDENTITY")):
            with self.subTest(case=mutation.__name__), tempfile.TemporaryDirectory() as tmp:
                backend = self.adapter(mutation)
                args=(Path(tmp),self.protocol,self.manifest,[selected],backend)
                first=session.session(*args,execute=True)
                self.assertEqual(first["state"],"SESSION_HALTED")
                self.assert_invalid(first["incident"],reason)
                self.assertEqual(session.session(*args),first)
                self.assertEqual(session.session(*args,execute=True),first)
                self.assertEqual(len(backend.calls),1)
                self.assertEqual(len((Path(tmp)/"ledger.jsonl").read_bytes().splitlines()),2)
                ok,rejection=statistics.is_slot_eligible(first["incident"],statistics._synthetic_identity(selected,self.protocol),
                                                        self.workloads[selected["workload_id"]])
                self.assertFalse(ok)
                self.assertNotIn(rejection,runner.RESOURCE_CENSORS)

    def test_production_format_fault_boundaries_keep_attempt_history(self):
        cases = (
            ("before_start", 0, "SESSION_COMPLETED"), ("after_start", 0, "SESSION_INTERRUPTED"),
            ("before_launch", 0, "SESSION_INTERRUPTED"), ("after_exit", 1, "SESSION_INTERRUPTED"),
            ("after_raw", 1, "SESSION_INTERRUPTED"), ("before_finish", 1, "SESSION_INTERRUPTED"),
            ("after_finish", 1, "SESSION_COMPLETED"), ("before_resume", 0, "SESSION_COMPLETED"),
        )
        for boundary, child_calls, expected in cases:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                slot = self.slot(purpose="diagnostic")
                backend = self.adapter()
                args = (Path(tmp), self.protocol, self.manifest, [slot], backend)

                def fail_at(point):
                    if point == boundary:
                        raise InterruptedError(point)

                with self.assertRaisesRegex(InterruptedError, boundary):
                    session.session(*args, execute=True, fault=fail_at)
                self.assertEqual(len(backend.calls), child_calls)
                reported = session.session(*args, execute=True)
                self.assertEqual(reported["state"], expected)
                self.assertEqual(session.session(*args), reported)
                events = (Path(tmp)/"ledger.jsonl").read_bytes().splitlines()
                self.assertEqual(len(events), 1 if expected=="SESSION_INTERRUPTED" else 2)
                self.assertEqual(len(backend.calls), child_calls if expected=="SESSION_INTERRUPTED" else 1)
                self.assertTrue(all(row["evidence_kind"]==runner.SYNTHETIC_KIND for row in reported["records"]))
