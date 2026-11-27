import copy
import gzip
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import p4b_activation as auth
from scripts import p4b_correctness_registry as refs
from scripts import p4b_experiment_session as exp
from scripts import run_rapidflow_apx_p4b as runner
from scripts import summarize_rapidflow_apx_p4b as stats
from tests.test_rapidflow_apx_p4b_statistics import _make_synthetic_eligible_ledger

DURABLE_ARCHIVE = Path("/home/iamqthanq/Desktop/P4b3A_R1_Independent_Review_2026-11-25/review-evidence-p4b3a-r1.tar.gz")
CANDIDATE_ARCHIVE = Path(
    "/home/iamqthanq/Desktop/P4b3B0_R2_1_Independent_Review_2026-11-25/candidate-reference-bundle/p4b-r2-correctness-reference-bundle.tar.gz"
)


class TestP4b3b0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.plan = list(runner.ordered_planned_slots(cls.schedule))
        cls.synthetic = {r["slot_id"]: r for r in _make_synthetic_eligible_ledger()}

    def make_synthetic(self, slot):
        return self.synthetic.get(slot["slot_id"], {"evidence_kind": runner.SYNTHETIC_KIND,
                                                   "attempt_outcome": "COMPLETED"})

    def test_shared_core_resumes_all_phases_and_ingests_synthetic_only(self):
        calls = []
        def backend(slot):
            calls.append(slot["slot_id"])
            return self.make_synthetic(slot)

        first_primary = next(i for i, row in enumerate(self.plan) if row["slot_purpose"] == "primary")
        first_diag = next(i for i, row in enumerate(self.plan) if row["slot_purpose"] == "diagnostic")
        checkpoints = [0, first_primary, first_primary + 17, first_diag // 2, first_diag, first_diag + 23, len(self.plan)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner.subprocess, "Popen") as spawn:
            root = Path(tmp)
            args = (root, self.protocol, self.manifest, self.plan, exp.SyntheticBackend(backend))
            for stop in checkpoints:
                result = exp.session(*args, execute=True, stop_after=stop)
                self.assertEqual(len(result["records"]), stop)
                self.assertEqual(len(calls), stop)
            self.assertEqual(result["state"], "SESSION_COMPLETED")
            self.assertEqual(calls, [s["slot_id"] for s in self.plan])
            self.assertEqual(len(set(calls)), 1450)
            self.assertEqual(exp.session(*args, execute=True), result)
            self.assertEqual(exp.session(*args), result)
            self.assertEqual(len((root / "ledger.jsonl").read_text().splitlines()), 2900)
            report = stats.analyze_p4b_ledger(result["records"], allow_synthetic_mode=True)
            self.assertEqual(report["analysis_mode"], "SYNTHETIC_FIXTURE_VERIFICATION")
            self.assertEqual(report["variants"]["A_STATIC"]["coverage"]["overall"], 50)
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "NOT_AUTHORIZED"):
                stats.analyze_p4b_ledger(result["records"])
            spawn.assert_not_called()

    def test_recovery_boundaries_never_relaunch_pending_attempt(self):
        checkpoints = ("before_start", "after_start", "before_launch", "after_exit", "after_raw",
                       "before_validation", "before_publication", "before_finish", "after_finish", "before_resume")
        for boundary in checkpoints:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                calls = []
                def simulate(slot):
                    calls.append(slot["slot_id"])
                    return self.make_synthetic(slot)
                def fault(spot):
                    if spot == boundary:
                        raise InterruptedError(spot)
                args = (root, self.protocol, self.manifest, self.plan[:2], exp.SyntheticBackend(simulate))
                with self.assertRaisesRegex(InterruptedError, boundary):
                    exp.session(*args, execute=True, fault=fault)
                resumed = exp.session(*args, execute=True)
                if boundary in ("before_start", "before_resume", "after_finish"):
                    self.assertEqual(resumed["state"], "SESSION_COMPLETED")
                    self.assertEqual(len(calls), 2)
                else:
                    self.assertEqual(resumed["state"], "SESSION_INTERRUPTED")
                    self.assertLessEqual(len(calls), 1)

    def test_ledger_corruption_lock_recovery_and_cross_session_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backend = exp.SyntheticBackend(self.make_synthetic)
            args = (root, self.protocol, self.manifest, self.plan[:2], backend)
            with exp.session_lock(root, True), self.assertRaisesRegex(runner.LedgerIntegrityError, "ALREADY_OWNED"):
                exp.session(*args, execute=True)
            exp.session(*args, execute=True)
            audit = root / "ledger.jsonl"
            original = audit.read_bytes()
            records = original.splitlines(keepends=True)
            for bad in (original + b'{"torn":', records[1] + b"".join(records[2:]),
                        records[0] + records[1] * 2 + b"".join(records[2:])):
                audit.write_bytes(bad)
                with self.assertRaises(runner.LedgerIntegrityError):
                    exp.session(*args)
            audit.write_bytes(original)
            # A stale cursor and a different registered plan cannot overwrite.
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "SESSION_IDENTITY_CHANGED"):
                exp.session(root, self.protocol, self.manifest, self.plan[1:3], backend, execute=True)
            crossed = root / "copied-session"
            import shutil
            shutil.copytree(root, crossed, ignore=shutil.ignore_patterns("copied-session"))
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "SESSION_IDENTITY_CHANGED"):
                exp.session(crossed, self.protocol, self.manifest, self.plan[:2], backend)
            raw = root / "attempts/slot-0000/attempt-001/native-output.json"
            raw.write_bytes(b'{"evidence_kind":"SYNTHETIC_VALIDATION"}')
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "FINISH_RAW_DIGEST_MISMATCH"):
                exp.session(*args)

    def test_os_lock_releases_after_process_exit_and_pending_child_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rfd, wfd = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(rfd)
                try:
                    with exp.session_lock(root, True):
                        os.write(wfd, b"locked")
                        signal.pause()
                finally:
                    os._exit(0)
            os.close(wfd)
            try:
                self.assertEqual(os.read(rfd, 6), b"locked")
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "SESSION_ALREADY_OWNED"):
                    with exp.session_lock(root, True):
                        self.fail("A second process obtained the lock")
            finally:
                os.close(rfd)
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            args = (root, self.protocol, self.manifest, self.plan[:1])
            def stop_after_orphan_marker(slot):
                attempt = root/"attempts/slot-0000/attempt-001"
                exp.write(attempt/"invocation.json", {"pid": child, "argv": ["synthetic-marker"]})
                raise InterruptedError("orphan marker")
            with self.assertRaises(InterruptedError):
                exp.session(*args, exp.SyntheticBackend(stop_after_orphan_marker), execute=True)
            recovered = exp.session(*args, exp.SyntheticBackend(lambda _: self.fail("relaunched")), execute=True)
            self.assertEqual(recovered["state"], "SESSION_INTERRUPTED")
            self.assertEqual(recovered["observed_process"]["pid"], child)

    @unittest.skipUnless(sys.platform == "linux", "Linux supervisor containment")
    def test_monitored_child_dies_when_supervisor_is_killed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dummy = root/"dummy-fixture-child"
            dummy.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
            dummy.chmod(0o700)
            fixture = runner.REPO_ROOT/"tests/fixtures/p4b/continuity/P4B_CONT_01.json"
            program = """
from pathlib import Path
import sys
from scripts.run_rapidflow_apx_p4b import execute_p4b_native_slot
binary, fixture, base = map(Path, sys.argv[1:])
execute_p4b_native_slot(binary, fixture, "BARE", "correctness", base/"unused.json", timeout_s=30,
    evidence={"environment": {"LANG":"C"}, "on_start": lambda pid, _: print(pid,flush=True),
              "save_streams": lambda *_: None})
"""
            supervisor = subprocess.Popen([sys.executable, "-c", program, dummy, fixture, root],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child = None
            try:
                import select
                self.assertTrue(select.select([supervisor.stdout], [], [], 5)[0], "missing child PID")
                child = int(supervisor.stdout.readline())
                self.assertGreater(child, 1)
                supervisor.kill()
                supervisor.wait(timeout=5)
                status = Path(f"/proc/{child}/stat")
                for _ in range(100):
                    try:
                        state = status.read_text().rsplit(")", 1)[1].split()[0]
                    except FileNotFoundError:
                        break
                    if state == "Z":
                        break  # reaped by the OS after reparenting
                    time.sleep(.01)
                else:
                    self.fail("native adapter child survived supervisor death")
            finally:
                if supervisor.poll() is None:
                    supervisor.kill()
                    supervisor.wait(timeout=5)
                supervisor.stdout.close()
                supervisor.stderr.close()

    def test_semantic_halt_cannot_be_retried_or_hidden_by_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def unsound(slot):
                return dict(self.make_synthetic(slot), attempt_outcome="FAILED",
                            correctness_status="SEMANTIC_UNSOUND", censoring_reason="CORRECTNESS_FAILURE")
            args = (root, self.protocol, self.manifest, self.plan[:2])
            halted = exp.session(*args, exp.SyntheticBackend(unsound), execute=True)
            self.assertEqual(halted["state"], "SESSION_HALTED")
            self.assertEqual(halted["incident"]["correctness_status"], "SEMANTIC_UNSOUND")
            resumed = exp.session(*args, exp.SyntheticBackend(lambda _: self.fail("relaunched")), execute=True)
            self.assertEqual(resumed, halted)
            (root / "ledger.jsonl").unlink()
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "MISSING_SESSION_LEDGER"):
                exp.session(*args, exp.SyntheticBackend(unsound), execute=True)

    def test_native_cannot_launch_from_direct_unverified_calls(self):
        adapter = exp.ProductionBackend(runner.DEFAULT_BINARY_PATH, {}, Path("."), self.protocol["resource_policy"])
        with patch.object(runner, "execute_p4b_native_slot") as spawn:
            with self.assertRaisesRegex(runner.AuthorizationBlockedError, "VERIFIED_PRODUCTION_CONTEXT_REQUIRED"):
                adapter.execute(self.plan[0], self.manifest["workloads"][0], Path("/not-used"))
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "session"
                with self.assertRaisesRegex(runner.AuthorizationBlockedError, "NOT_AUTHORIZED"):
                    exp.production_command("primary", None, runner.DEFAULT_BINARY_PATH, refs.REGISTRY_PATH,
                                           DURABLE_ARCHIVE, root)
                self.assertFalse(root.exists())
            spawn.assert_not_called()

        outside = next(entry for entry in self.manifest["workloads"] if entry["workload_id"] != exp.BOUNDED_CASE)
        fixture = runner.REPO_ROOT / outside["fixture_path"]
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner.subprocess, "Popen") as child:
            output = Path(tmp) / "native.json"
            for purpose in ("performance", "diagnostic"):
                with self.subTest(purpose=purpose), self.assertRaisesRegex(
                    runner.AuthorizationBlockedError, "outside bounded inventory"
                ):
                    runner.execute_p4b_native_slot(runner.DEFAULT_BINARY_PATH, fixture, "BARE", purpose, output)
            with self.assertRaisesRegex(runner.AuthorizationBlockedError, "OUTSIDE_ACTIVE_SESSION"):
                runner.execute_p4b_native_slot(runner.DEFAULT_BINARY_PATH, fixture, "BARE", "performance", output,
                                               production_attempt=adapter)
            with self.assertRaisesRegex(runner.AuthorizationBlockedError, "VERIFIED_PRODUCTION_CONTEXT_REQUIRED"):
                runner.execute_p4b_native_slot(runner.DEFAULT_BINARY_PATH, fixture, "BARE", "performance", output,
                                               production_attempt=object())
            self.assertFalse(output.exists())
            child.assert_not_called()

    def test_direct_cli_rejects_forged_detached_activation_without_opening_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            forged = parent / "locally-authored.json"
            forged.write_text(json.dumps({"execution_authorized": True, "approval_status": "MEASUREMENT_AUTHORIZED"}))
            root = parent / "no-session"
            for flag in ("--execute-primary", "--execute-diagnostic", "--analyze-session"):
                result = subprocess.run(
                    [sys.executable, runner.__file__, flag, "--activation", str(forged),
                     "--archive", str(DURABLE_ARCHIVE), "--session", str(root)], capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("MEASUREMENT_NOT_AUTHORIZED", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(root.exists())

    def _approved_test_context(self, parent):
        env = {"stable": {"fake": True}, "dynamic": {"filesystem_free_bytes": 20 << 30,
               "memory": {"MemAvailable": 1 << 30}}}
        source = {"code.py": "a" * 64}
        bundle_spec = refs.resolve_trusted_bundle(auth.ACTIVE_BUNDLE_ID)
        paths = (parent/"envelope.json", runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, parent/"session")
        doc = {
            "schema": auth.ENVELOPE, "approval_status": "MEASUREMENT_AUTHORIZED",
            "scope": "P4b-3B.1_CONFIRMATORY_MEASUREMENT", "experiment_id": "TEST_ONLY",
            "approved_session_path": str(paths[-1].resolve()),
            "reviewer_approval_ref": "unit test reference", "coordinator_approval_ref": "unit test reference",
            "protocol_sha256": runner.PROTOCOL_SHA256, "manifest_sha256": self.protocol["manifest_sha256"],
            "schedule_sha256": self.protocol["schedule_sha256"], "registry_sha256": bundle_spec["registry_sha256"],
            "bundle_id": bundle_spec["bundle_id"], "correctness_bundle": refs.bundle_identity_payload(bundle_spec),
            "native_binary_sha256": runner.NATIVE_SHA256,
            "native_source_commit": runner.NATIVE_SOURCE_COMMIT, "implementation_revision": "a" * 40,
            "source_file_sha256": source, "resource_policy": self.protocol["resource_policy"],
            "statistical_amendment_sha256": runner.compute_sha256(auth.AMENDMENT),
            "environment_sha256": exp.digest(env["stable"]), "correctness_policy": "M0_AND_SAME_VARIANT_DIFFERENTIAL",
            "c3_bare_policy": "ALLOW_LIMITED_BARE_CONTROL_NOT_ORACLE", "disc07_disposition": "RETAIN_DISC_07_UNCHANGED",
            "phases": ["primary"], "stratum_rule": "R1_REGISTERED", "resource_regression_rule": "SAME_CATEGORY",
        }
        return paths, doc, env, source

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file() and runner.DEFAULT_BINARY_PATH.is_file(), "Locked candidate R2 archive/binary unavailable")
    def test_detached_approval_requires_trusted_hash_and_all_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, doc, env, source = self._approved_test_context(Path(tmp))
            paths[0].write_bytes(runner.canonical_json_bytes(doc))
            with patch.object(auth, "_reviewed_anchors", return_value={}), self.assertRaisesRegex(
                runner.AuthorizationBlockedError, "NOT_AUTHORIZED"):
                auth.authorize(*paths)
            with patch.object(auth, "environment_snapshot", return_value=env), patch.object(
                auth, "_source_state", return_value=source), patch.object(auth, "_reviewed_anchors") as anchors:
                def approve_only_in_test(document):
                    paths[0].write_bytes(runner.canonical_json_bytes(document))
                    anchors.return_value = {runner.compute_sha256(paths[0]): {k: doc[k] for k in
                                            ("experiment_id", "reviewer_approval_ref", "coordinator_approval_ref")}}
                approve_only_in_test(doc)
                approved = auth.authorize(*paths, phase="primary")
                self.assertEqual(approved.experiment_id, "TEST_ONLY")
                with self.assertRaisesRegex(runner.AuthorizationBlockedError, "UNAUTHORIZED_EXECUTION_PHASE"):
                    auth.authorize(*paths, phase="diagnostic")
                with self.assertRaisesRegex(runner.AuthorizationBlockedError, "approved_session_path"):
                    auth.authorize(*paths[:-1], Path(tmp)/"crossed")
                for key, value in (
                    ("native_binary_sha256", "f"*64), ("registry_sha256", "f"*64),
                    ("schedule_sha256", "f"*64), ("manifest_sha256", "f"*64),
                    ("protocol_sha256", "f"*64), ("environment_sha256", "f"*64),
                    ("source_file_sha256", {}), ("experiment_id", "other"),
                    ("c3_bare_policy", "SELF_CERTIFIED"), ("reviewer_approval_ref", True),
                    ("approval_status", True),
                ):
                    with self.subTest(key=key):
                        approve_only_in_test(dict(doc, **{key: value}))
                        with self.assertRaises(runner.AuthorizationBlockedError):
                            auth.authorize(*paths)
                approve_only_in_test(doc)
                approved.recheck()
                paths[0].write_bytes(runner.canonical_json_bytes(dict(doc, additional_field=True)))
                with self.assertRaisesRegex(runner.AuthorizationBlockedError, "NOT_AUTHORIZED"):
                    approved.recheck()

    @unittest.skipUnless(DURABLE_ARCHIVE.is_file(), "Locked durable R1 archive unavailable")
    def test_registry_reconstructs_from_trusted_raw_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mapped, sha = refs.validate_registry(refs.REGISTRY_PATH, DURABLE_ARCHIVE, root, self.protocol, self.manifest)
            self.assertEqual(sha, runner.compute_sha256(refs.REGISTRY_PATH))
            self.assertEqual(len(mapped), 250)
            raw = refs.REGISTRY_PATH.read_bytes()
            same = runner.load_json_bytes(raw)
            names = ("workload_id", "variant_id", "binary_sha256", "native_source_commit", "fixture_sha256",
                     "compressed_output_sha256", "raw_output_sha256", "oracle_sha256", "certification_scope", "lifecycle")
            for name in names:
                modified = copy.deepcopy(same)
                modified["entries"][0][name] = "tampered"
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "DOES_NOT_MATCH"):
                    refs.require_canonical_registry(runner.canonical_json_bytes(modified), raw)
            for change in (same["entries"][:-1], same["entries"] + same["entries"][:1]):
                with self.assertRaises(runner.LedgerIntegrityError):
                    refs.require_canonical_registry(runner.canonical_json_bytes(dict(same, entries=change)), raw)
            crossover = mapped[exp.BOUNDED_CASE, "A_STATIC"]
            bare = mapped[exp.BOUNDED_CASE, "BARE"]
            path = root / bare["raw_output_relative_path"]
            path.write_bytes((root / crossover["raw_output_relative_path"]).read_bytes())
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "RAW_EVIDENCE_MISMATCH"):
                refs.read_reference(root, bare)
            fake = root/"fake.tar.gz"
            fake.write_bytes(b"caller-generated digest")
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "R1_ARCHIVE_IDENTITY_MISMATCH"):
                refs.unpack_references(fake, root)

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file() and runner.DEFAULT_BINARY_PATH.is_file(), "Locked candidate R2 archive/binary unavailable")
    def test_production_adapter_wiring_uses_only_fake_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            paths, doc, env, source = self._approved_test_context(parent)
            paths[0].write_bytes(runner.canonical_json_bytes(doc))
            anchor = {runner.compute_sha256(paths[0]): {k: doc[k] for k in (
                "experiment_id", "reviewer_approval_ref", "coordinator_approval_ref")}}
            store = parent / "refs"
            store.mkdir()
            refs.unpack_references(CANDIDATE_ARCHIVE, store, bundle=auth.ACTIVE_BUNDLE_ID)
            ref_doc = exp.read(refs.CANDIDATE_REGISTRY_PATH)
            ref_map = {(r["workload_id"], r["variant_id"]): r for r in ref_doc["entries"]}
            frozen_policy = self.protocol["resource_policy"]
            plan = [s for s in self.plan if s["slot_purpose"] != "diagnostic"]

            def fake(binary, fixture, variant, purpose, dest, **kwargs):
                slot = plan[0]
                ref = ref_map[slot["workload_id"], variant]
                limits = tuple(kwargs[name] for name in ("timeout_s", "as_limit_mib", "max_work", "max_nlf_memory_mib"))
                adapter = kwargs["production_attempt"]
                adapter.verify_launch(binary, fixture, ref["fixture_sha256"], variant, purpose, dest, limits)
                for modified in (("f" * 64, dest), (ref["fixture_sha256"], dest.with_name("wrong.json"))):
                    with self.assertRaisesRegex(runner.AuthorizationBlockedError, "DOES_NOT_MATCH_ACTIVE_ATTEMPT"):
                        adapter.verify_launch(binary, fixture, modified[0], variant, purpose, modified[1], limits)
                output = runner.load_json_bytes(refs.read_reference(store, ref))
                output.update(execution_purpose=purpose, deep_diagnostics_enabled=False)
                for step in output["steps"]:
                    step.pop("graph_state")
                    step.pop("codec_state")
                raw = runner.canonical_json_bytes(output)
                dest.write_bytes(raw)
                argv = [str(binary), "--engine", "m2", "--nlf-mode", ref["requested_nlf_mode"],
                        "--neighbor-mode", ref["requested_neighbor_mode"], "--max-nlf-memory-mib", "256",
                        "--max-work", "500000", "--execution-purpose", purpose, "--wall-timeout-seconds", "60",
                        "--workload", str(fixture), "--output", str(dest)]
                kwargs["evidence"]["on_start"](12345, argv)
                kwargs["evidence"]["save_streams"](b"", b"")
                timing = output["macro_timing"]
                outer = 1000 + sum(timing[key] for key in (
                    "input_parsing_ns", "initialization_wall_ns", "streaming_loop_wall_ns", "reporting_wall_ns"))
                return dict(variant_id=variant, execution_purpose=purpose, exit_code=0, process_signal=None, censoring_reason="NONE",
                            outer_wall_ns=outer, user_cpu_ns=1, system_cpu_ns=1, wait4_ru_maxrss_bytes=1024,
                            peak_rss_bytes=timing["child_vm_hwm_bytes"], peak_rss_original_value=timing["child_vm_hwm_kib"],
                            resource_measurement_method="linux_proc_self_status_vmhwm", peak_rss_original_unit="KiB",
                            fixture_sha256=ref["fixture_sha256"], binary_sha256=runner.NATIVE_SHA256, command=argv,
                            output_sha256=runner.sha256_bytes(raw), output_size_bytes=len(raw),
                            supervisor={"pid":12345, "wait_status":0, "deadline_fired":False,
                                        "post_execution_identity":{"binary_sha256":runner.NATIVE_SHA256,
                                                                   "fixture_sha256":ref["fixture_sha256"]},
                                        "started_monotonic_ns": 10, "reaped_monotonic_ns":10+outer,
                                        "wait4_user_seconds":1e-9, "wait4_system_seconds":1e-9, "wait4_maxrss_kib":1,
                                        "resource_caps":{"timeout_seconds":60.0, "address_space_mib":512,
                                                         "max_work":500000, "max_nlf_memory_mib":256}})

            with patch.object(auth, "_reviewed_anchors", return_value=anchor), patch.object(
                auth, "environment_snapshot", return_value=env), patch.object(
                auth, "_source_state", return_value=source), patch.object(
                runner, "execute_p4b_native_slot", side_effect=fake) as launch, patch.object(runner.subprocess, "Popen") as real:
                context = auth.authorize(*paths)
                backend = exp.ProductionBackend(paths[1], ref_map, store, frozen_policy, context)
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "OUTSIDE_ACTIVE_SESSION"):
                    backend.execute(plan[0], self.manifest["workloads"][0], parent/"outside")
                args = (paths[-1], self.protocol, self.manifest, plan, backend)
                completed = exp.session(*args, execute=True, stop_after=1)
                self.assertEqual(completed["records"][0]["attempt_outcome"], "COMPLETED")
                self.assertEqual(exp.session(*args), completed)
                self.assertIsNone(stats._analyze_reviewed_records(completed, self.protocol, self.manifest,
                                                                 self.schedule, context)["confirmatory_analysis"])
                self.assertEqual(launch.call_count, 1)
                real.assert_not_called()
                paths[0].write_bytes(b"{}")
                with self.assertRaisesRegex(runner.AuthorizationBlockedError, "NOT_AUTHORIZED"):
                    exp.session(*args, execute=True, stop_after=2)
                self.assertEqual(launch.call_count, 1)

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file() and runner.DEFAULT_BINARY_PATH.is_file(), "Locked candidate R2 archive/binary unavailable")
    def test_bounded_real_three_purposes_and_artifact_revalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_bounded_witness(Path(tmp), self.protocol, self.manifest, self.schedule, self)

    def test_statistical_candidate_boundaries_and_shared_failure(self):
        records = [dict(row) for row in self.synthetic.values()]
        # Mixed genuine resource categories do not trigger same-category regression.
        for row in records:
            if row["variant_id"] == "A_STATIC" and row["workload_id"] == "DISC_07_K0" and row["repeat_id"] in (1, 2):
                row.update(attempt_outcome="CENSORED", censoring_reason=(
                    "TIMEOUT" if row["repeat_id"] == 1 else "WORK_BUDGET_EXCEEDED"))
        report = stats._analyze_observations(records, self.protocol, self.manifest, self.schedule,
                                            stats._synthetic_identity, "SYNTHETIC_FIXTURE_VERIFICATION",
                                            same_category=True, conservative_strata=True)
        static = report["variants"]["A_STATIC"]
        self.assertNotEqual(static["decision"]["priority_triggered"], 3)
        self.assertNotIn("DISC_07_K1", static["primary_workload_ids"])
        self.assertEqual(static["coverage"]["complete_disc_blocks"], 9)
        self.assertTrue(all(not r["eligible_for_stratum_decision"] for r in static["strata"]
                            if r["name"].endswith("_descriptive_only")))
        for row in records:
            if row["variant_id"] == "BARE" and row["workload_id"] == "DISC_07_K0" and row["repeat_id"] == 1:
                row["correctness_status"] = "TUPLE_MISMATCH"
        shared = stats._analyze_observations(records, self.protocol, self.manifest, self.schedule,
                                            stats._synthetic_identity, "SYNTHETIC_FIXTURE_VERIFICATION",
                                            same_category=True)
        self.assertTrue(all(x["decision"]["priority_triggered"] == 2 for x in shared["variants"].values()))


    def test_symmetric_disc07_censor_coverage_and_exact_jitter(self):
        records = [dict(row) for row in self.synthetic.values()]
        for row in records:
            if row["workload_id"].startswith("DISC_07_"):
                row.update(attempt_outcome="CENSORED", censoring_reason="WORK_BUDGET_EXCEEDED")
            if row["variant_id"] == "A_STATIC" and row["workload_id"] == "P4B_CONT_01" and row["repeat_id"] == 1:
                row["streaming_loop_wall_ns"] = 12_000_000  # exactly 1.5x the other 8M reps
        result = stats.analyze_p4b_ledger(records, allow_synthetic_mode=True)["variants"]["A_STATIC"]
        self.assertEqual(result["coverage"]["complete_disc_blocks"], 9)
        self.assertEqual(result["coverage"]["discrimination"], 18)
        self.assertEqual(result["coverage"]["overall"], 48)
        self.assertEqual(result["coverage"]["noisy_pairs"], 0)
        self.assertEqual(result["censoring_table"]["both_incomplete"], 2)
        for row in records:
            if row["variant_id"] == "A_STATIC" and row["workload_id"] == "DISC_08_K0" and row["repeat_id"] == 1:
                row.update(attempt_outcome="CENSORED", censoring_reason="WORK_BUDGET_EXCEEDED")
        result = stats.analyze_p4b_ledger(records, allow_synthetic_mode=True)["variants"]["A_STATIC"]
        self.assertEqual(result["coverage"]["complete_disc_blocks"], 8)
        self.assertEqual(result["decision"]["priority_triggered"], 4)


def run_bounded_witness(parent, protocol, manifest, schedule, test):
    store = parent / "refs"
    store.mkdir()
    refs.unpack_references(CANDIDATE_ARCHIVE, store, bundle=auth.ACTIVE_BUNDLE_ID)
    entries = exp.read(refs.CANDIDATE_REGISTRY_PATH)["entries"]
    reference_map = {(r["workload_id"], r["variant_id"]): r for r in entries if r["workload_id"] == exp.BOUNDED_CASE}
    base = next(s for s in schedule["primary_slots"] if s["workload_id"] == exp.BOUNDED_CASE and s["variant_id"] == "BARE")
    slots = [dict(base, slot_id=f"FUNCTIONAL_{purpose}", slot_purpose="functional", execution_purpose=purpose)
             for purpose in ("correctness", "performance", "diagnostic")]
    backend = exp.NativeBackend(runner.DEFAULT_BINARY_PATH.resolve(), reference_map, store, protocol["resource_policy"])
    root = parent/"bounded"
    args = (root, protocol, manifest, slots, backend)
    result = exp.session(*args, execute=True)
    test.assertEqual(result["state"], "SESSION_COMPLETED", result.get("incident"))
    test.assertEqual(exp.session(*args), result)
    test.assertEqual(len(result["records"]), 3)
    test.assertTrue(all(not r["measurement_eligible"] for r in result["records"]))
    test.assertIsNotNone(result["records"][2]["diagnostic"])
    entry = next(w for w in manifest["workloads"] if w["workload_id"] == exp.BOUNDED_CASE)
    path = root/"attempts/slot-0001/attempt-001"
    artifact, envelope = path/"native-output.json", path/"process.json"
    original_raw, original_process = artifact.read_bytes(), envelope.read_bytes()
    original = runner.load_json_bytes(original_raw)
    for mutation, expected in (
        (lambda x: next(s for s in x["steps"] if len(s["entered_tuples"]) > 1)["entered_tuples"].reverse(), "FAILED"),
        (lambda x: x["execution"].update(completed_steps=True), "INVALID"),
        (lambda x: x["macro_timing"].update(child_vm_hwm_bytes=123), "INVALID"),
        (lambda x: x.update(requested_nlf_mode="static"), "INVALID"),
    ):
        changed = copy.deepcopy(original)
        mutation(changed)
        raw = runner.canonical_json_bytes(changed)
        artifact.write_bytes(raw)
        process = runner.load_json_bytes(original_process)
        process.update(output_sha256=runner.sha256_bytes(raw), output_size_bytes=len(raw))
        envelope.write_bytes(runner.canonical_json_bytes(process))
        identity = exp._identity(slots[1], protocol, exp.read(root/"session-manifest.json"))
        outcome = exp.validate_attempt(slots[1], entry, identity, backend, path)
        test.assertEqual(outcome["attempt_outcome"], expected, outcome)
        with test.assertRaisesRegex(runner.LedgerIntegrityError, "FINISH_RAW_DIGEST_MISMATCH"):
            exp.session(*args)
    artifact.write_bytes(original_raw)
    envelope.write_bytes(original_process)
    test.assertEqual(exp.session(*args), result)
    return result
