"""Focused R2.1 tests covering Candidate Bundle Binding, Native Resource Witnesses,
Production-Format 1,450-Slot Rehearsal, Journal/Resume Scenarios, and Acceptance Gates."""

import copy
import functools
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts import p4b_activation as auth
from scripts import p4b_correctness_registry as refs
from scripts import p4b_experiment_session as session
from scripts import p4b_native_evidence as evidence
from scripts import run_rapidflow_apx_p4b as runner
from scripts import summarize_rapidflow_apx_p4b as stats
from tests.test_rapidflow_apx_p4b_3b0 import CANDIDATE_ARCHIVE, DURABLE_ARCHIVE
from tests.test_rapidflow_apx_p4b_3b0_r1 import (
    ControlledNative,
    add_profile,
    external_timeout,
    native_resource,
    reported_exit,
    write_attempt,
)

_CAND_WITNESS = (
    runner.REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build-r2-candidate" / "rapidflow_apx_r21_resource_witness"
)
_STD_WITNESS = (
    runner.REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build" / "rapidflow_apx_r21_resource_witness"
)
WITNESS_BINARY = _CAND_WITNESS if _CAND_WITNESS.is_file() else _STD_WITNESS
HISTORICAL_R1_BINARY = Path(
    "/home/iamqthanq/Desktop/P4b3B0_R2_Independent_Review_2026-11-25/historical-r1-binary/rapidflow_apx_native"
)
RECONCILIATION_DOC = runner.REPO_ROOT / "docs" / "rapidflow_apx_p4b_3b0_r2_1_gate_reconciliation.json"


class ControlledR2Native(session.NativeBackend):
    """Production-format synthetic backend bound strictly to R2 candidate identity."""

    origin = runner.SYNTHETIC_KIND

    def __init__(self, registry, store, policy, mutation=None):
        super().__init__(
            runner.DEFAULT_BINARY_PATH.resolve(),
            registry,
            store,
            policy,
            binary_sha=runner.NATIVE_SHA256,
            source=runner.NATIVE_SOURCE_COMMIT,
        )
        self.mutation = mutation
        self.calls = []

    @functools.lru_cache(maxsize=256)
    def _reference(self, key):
        return runner.load_json_bytes(refs.read_reference(self.store, self.registry[key]))

    def authorize(self, slot):
        if slot["variant_id"] not in runner.ALL_VARIANTS:
            raise AssertionError(f"Unknown test variant: {slot['variant_id']}")

    def projected(self, slot):
        key = slot["workload_id"], slot["variant_id"]
        reference = self._reference(key)
        native = {k: copy.deepcopy(v) for k, v in reference.items() if k not in ("steps", "macro_timing", "profiling")}
        native["steps"] = [
            {k: copy.deepcopy(v) for k, v in step.items() if k not in ("graph_state", "codec_state", "profiling")}
            for step in reference["steps"]
        ]
        diagnostic = slot["execution_purpose"] == "diagnostic"
        native.update(
            execution_purpose=slot["execution_purpose"],
            deep_diagnostics_enabled=False,
            profiling_enabled=diagnostic,
        )
        native["macro_timing"] = dict(
            clock_source="std::chrono::steady_clock",
            streaming_loop_wall_ns=2000,
            input_parsing_ns=70,
            initialization_wall_ns=200,
            graph_init_ns=60,
            query_prep_ns=60,
            engine_and_nlf_init_ns=45,
            reporting_wall_ns=100,
            child_vm_hwm_kib=1024,
            child_vm_hwm_bytes=1024**2,
            child_rss_method="linux_proc_self_status_vmhwm",
        )
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
        out = path / "native-output.json"
        argv = [
            str(self.binary), "--engine", mode["engine"], "--nlf-mode", mode["nlf_mode"],
            "--neighbor-mode", mode["neighbor_mode"], "--max-nlf-memory-mib", "256",
            "--max-work", "500000", "--execution-purpose", slot["execution_purpose"],
            "--wall-timeout-seconds", "60", "--workload", str(runner.REPO_ROOT / entry["fixture_path"]),
            "--output", str(out),
        ]
        supervisor = dict(
            pid=12345,
            wait_status=exit_code << 8,
            deadline_fired=False,
            started_monotonic_ns=1000,
            reaped_monotonic_ns=11000,
            wait4_user_seconds=1e-6,
            wait4_system_seconds=1e-6,
            wait4_maxrss_kib=1024,
            resource_caps=dict(timeout_seconds=60.0, address_space_mib=512, max_work=500000, max_nlf_memory_mib=256),
            post_execution_identity=dict(binary_sha256=self.binary_sha, fixture_sha256=entry["fixture_sha256"]),
        )
        process = dict(
            exit_code=exit_code,
            command=argv,
            process_signal=None,
            outer_wall_ns=10000,
            user_cpu_ns=1000,
            system_cpu_ns=1000,
            wait4_ru_maxrss_bytes=1024**2,
            supervisor=supervisor,
            peak_rss_original_unit="KiB",
            peak_rss_original_value=1024,
            peak_rss_bytes=1024**2,
            resource_measurement_method="linux_proc_self_status_vmhwm",
            binary_sha256=self.binary_sha,
            fixture_sha256=entry["fixture_sha256"],
            variant_id=slot["variant_id"],
            execution_purpose=slot["execution_purpose"],
            censoring_reason="WORK_BUDGET_EXCEEDED" if exit_code == 4 else "NONE",
        )
        stderr = b""
        if self.mutation is not None:
            native, stderr = self.mutation(slot, native, process)
        raw = runner.canonical_json_bytes(native) if isinstance(native, dict) else native
        if raw is not None:
            session.publish(out, raw)
        process.update(
            output_size_bytes=len(raw or b""),
            output_sha256=runner.sha256_bytes(raw) if raw is not None else None,
        )
        session.write(path / "invocation.json", dict(pid=12345, argv=argv, environment=auth.NATIVE_ENV))
        session.publish(path / "stdout.log", b"")
        session.publish(path / "stderr.log", stderr)
        return process


def run_r21_simulation(out_root: Path, *, archive_path: Path = CANDIDATE_ARCHIVE, stops=(9, 205, 1450)):
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    protocol, manifest, schedule = runner.load_frozen_plan()
    plan = list(runner.ordered_planned_slots(schedule))
    counts = Counter(slot["slot_purpose"] for slot in plan)
    store = out_root / "trusted-references"
    store.mkdir()
    registry, _ = refs.validate_registry(
        refs.CANDIDATE_REGISTRY_PATH, archive_path, store, protocol, manifest, bundle="R2_CANDIDATE_BUNDLE"
    )
    native = ControlledR2Native(registry, store, protocol["resource_policy"])
    sess_dir = out_root / "session"
    args = (sess_dir, protocol, manifest, plan, native)
    planned_by_slot = {slot["slot_id"]: slot for slot in plan}
    entries = {w["workload_id"]: w for w in manifest["workloads"]}
    validate_calls = {"count": 0}
    orig_validate_native = session._validate_native

    def wrapped_validate_native(*vargs, **vkwargs):
        validate_calls["count"] += 1
        return orig_validate_native(*vargs, **vkwargs)

    stages = []
    with patch.object(runner.subprocess, "Popen", side_effect=AssertionError("No native launch allowed")) as popen, \
         patch.object(session, "_validate_native", side_effect=wrapped_validate_native):
        for stop in stops:
            out = session.session(*args, execute=True, stop_after=stop)
            stages.append({"stop": stop, "state": out["state"], "record_count": len(out["records"])})
        exec_validate_count = validate_calls["count"]
        readback = session.session(*args)
        assert readback == out
        replay_validate_count = validate_calls["count"] - exec_validate_count
        rows_by_slot = {}
        for record in readback["records"]:
            s = planned_by_slot[record["slot_id"]]
            key = record["workload_id"], record["variant_id"]
            registered = registry[key]
            expected = "COMPLETED" if registered["native_exit_code"] == 0 else "CENSORED"
            assert record["attempt_outcome"] == expected, (key, record)
            eligible, _ = stats.is_slot_eligible(record, stats._synthetic_identity(s, protocol), entries[key[0]])
            assert eligible == (expected == "COMPLETED" and s["slot_purpose"] == "primary"), record
            rows_by_slot[record["slot_id"]] = record
        final = stats.analyze_p4b_ledger(readback["records"], allow_synthetic_mode=True)
        try:
            stats.analyze_p4b_ledger(readback["records"])
        except runner.LedgerIntegrityError as error:
            assert "CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED" in str(error)
        else:
            raise AssertionError("Synthetic results elevated")
        stat_controls = {}
        targets = [
            s for s in plan
            if s["slot_purpose"] == "primary" and s["workload_id"] == "P4B_CONT_01"
            and s["variant_id"] == "A_STATIC" and s["repeat_id"] in (1, 2)
        ]
        for name in ("invalid-resource", "valid-resource"):
            replaced = dict(rows_by_slot)
            for idx, slot in enumerate(targets):
                def controlled(_, candidate, supervisor, _name=name):
                    if _name == "invalid-resource":
                        native_resource(candidate, supervisor)
                        candidate["requested_nlf_mode"] = "residual"
                        return candidate, b""
                    return external_timeout(candidate, supervisor)
                adapter = ControlledR2Native(registry, store, protocol["resource_policy"], controlled)
                case_dir = out_root / "stats" / name / f"r{idx + 1}"
                case_dir.parent.mkdir(parents=True, exist_ok=True)
                observed = session.session(case_dir, protocol, manifest, [slot], adapter, execute=True)
                replaced[observed["records"][0]["slot_id"]] = observed["records"][0]
            report = stats.analyze_p4b_ledger(list(replaced.values()), allow_synthetic_mode=True)
            stat_controls[name] = {"decision": report["variants"]["A_STATIC"]["decision"]}
        assert popen.call_count == 0
    return {
        "schedule_counts": dict(counts),
        "registry_entries": len(registry),
        "terminal_unique": len(rows_by_slot),
        "shared_validate_native_exec_calls": exec_validate_count,
        "shared_validate_native_replay_calls": replay_validate_count,
        "native_matching_subprocesses": 0,
        "offline_replay_equal": True,
        "outcomes": dict(Counter(r["attempt_outcome"] for r in readback["records"])),
        "resumed": stages,
        "analysis_mode": final["analysis_mode"],
    }, stat_controls


class ResourceWitnessBackend(session.NativeBackend):
    """Validation-only backend executing real native allocation witness binary."""

    origin = "VALIDATION_RESOURCE_WITNESS"

    def __init__(self, binary, registry, store, policy):
        super().__init__(
            binary,
            registry,
            store,
            policy,
            binary_sha=runner.compute_sha256(binary),
            source=runner.NATIVE_SOURCE_COMMIT,
        )


def _rewrite_tar_with_mutation(src_tar: Path, dst_tar: Path, mutator):
    entries = {}
    with tarfile.open(src_tar, "r:gz") as tf:
        for m in tf:
            entries[m.name] = tf.extractfile(m).read()
    entries = mutator(entries)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for name in sorted(entries):
                raw = entries[name]
                ti = tarfile.TarInfo(name=name)
                ti.size = len(raw)
                ti.mode = 0o644
                ti.mtime = 0
                tf.addfile(ti, io.BytesIO(raw))
    dst_tar.write_bytes(buf.getvalue())


class TestR21CandidateBinding(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.plan = list(runner.ordered_planned_slots(cls.schedule))

    @unittest.skipUnless(DURABLE_ARCHIVE.is_file(), "Locked durable R1 archive unavailable")
    def test_01_historical_r1_bundle_still_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            mapped, sha = refs.validate_registry(
                refs.REGISTRY_PATH, DURABLE_ARCHIVE, Path(td), self.protocol, self.manifest, bundle="HISTORICAL_R1_BUNDLE"
            )
            self.assertEqual(sha, refs.TRUSTED_BUNDLES["HISTORICAL_R1_BUNDLE"]["registry_sha256"])
            self.assertEqual(len(mapped), 250)
            self.assertTrue(all(r["binary_sha256"] == runner.HISTORICAL_R1_NATIVE_SHA256 for r in mapped.values()))

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file(), "Locked candidate R2 archive unavailable")
    def test_02_candidate_r2_bundle_validates_and_reconstructs(self):
        with tempfile.TemporaryDirectory() as td:
            mapped, sha = refs.validate_registry(
                refs.CANDIDATE_REGISTRY_PATH,
                CANDIDATE_ARCHIVE,
                Path(td),
                self.protocol,
                self.manifest,
                bundle="R2_CANDIDATE_BUNDLE",
            )
            self.assertEqual(sha, refs.CANDIDATE_REGISTRY_SHA)
            self.assertEqual(len(mapped), 250)
            self.assertTrue(all(r["binary_sha256"] == runner.NATIVE_SHA256 for r in mapped.values()))
            self.assertTrue(all(r["native_source_commit"] == runner.NATIVE_SOURCE_COMMIT for r in mapped.values()))

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file() and runner.DEFAULT_BINARY_PATH.is_file(), "R2 candidate binary/archive unavailable")
    def test_03_positive_isolated_activation_readiness_no_launch(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            env = {
                "stable": {"fake": True},
                "dynamic": {"filesystem_free_bytes": 20 << 30, "memory": {"MemAvailable": 1 << 30}},
            }
            bundle = refs.resolve_trusted_bundle("R2_CANDIDATE_BUNDLE")
            source = {p: "a" * 64 for p in auth.SOURCE_PATHS}
            sess_path = parent / "session"
            envelope = parent / "envelope.json"
            doc = {
                "schema": auth.ENVELOPE,
                "approval_status": "MEASUREMENT_AUTHORIZED",
                "scope": "P4b-3B.1_CONFIRMATORY_MEASUREMENT",
                "experiment_id": "R21_ISOLATED_READINESS_TEST",
                "approved_session_path": str(sess_path.resolve()),
                "reviewer_approval_ref": "test-only-isolated-reviewer-ref",
                "coordinator_approval_ref": "test-only-isolated-coordinator-ref",
                "protocol_sha256": runner.PROTOCOL_SHA256,
                "manifest_sha256": self.protocol["manifest_sha256"],
                "schedule_sha256": self.protocol["schedule_sha256"],
                "registry_sha256": bundle["registry_sha256"],
                "bundle_id": bundle["bundle_id"],
                "correctness_bundle": refs.bundle_identity_payload(bundle),
                "native_binary_sha256": bundle["native_binary_sha256"],
                "native_source_commit": bundle["native_source_commit"],
                "implementation_revision": "b" * 40,
                "source_file_sha256": source,
                "resource_policy": self.protocol["resource_policy"],
                "statistical_amendment_sha256": runner.compute_sha256(auth.AMENDMENT),
                "environment_sha256": session.digest(env["stable"]),
                "correctness_policy": "M0_AND_SAME_VARIANT_DIFFERENTIAL",
                "c3_bare_policy": "ALLOW_LIMITED_BARE_CONTROL_NOT_ORACLE",
                "disc07_disposition": "RETAIN_DISC_07_UNCHANGED",
                "phases": ["primary"],
                "stratum_rule": "CONSERVATIVE_3B0",
                "resource_regression_rule": "SAME_CATEGORY",
            }
            envelope.write_bytes(runner.canonical_json_bytes(doc))
            anchor = {
                runner.compute_sha256(envelope): {
                    k: doc[k] for k in ("experiment_id", "reviewer_approval_ref", "coordinator_approval_ref")
                }
            }
            with patch.object(auth, "_reviewed_anchors", return_value=anchor), \
                 patch.object(auth, "environment_snapshot", return_value=env), \
                 patch.object(auth, "_source_state", return_value=source), \
                 patch.object(runner.subprocess, "Popen", side_effect=AssertionError("No native launch")) as popen, \
                 patch.object(runner, "execute_p4b_native_slot", side_effect=AssertionError("No slot launch")):
                # Exercise real authorize + candidate bundle resolution + reference extraction + registry verification
                # right up to session execution pre-launch boundary (stop_after=0).
                ctx = auth.authorize(
                    envelope, runner.DEFAULT_BINARY_PATH.resolve(), refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, sess_path, phase="primary"
                )
                self.assertEqual(ctx.bundle_id, "R2_CANDIDATE_BUNDLE")
                with tempfile.TemporaryDirectory(dir=parent) as ref_store:
                    ref_map, reg_sha = refs.validate_registry(
                        refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, Path(ref_store), self.protocol, self.manifest, bundle=ctx.bundle_id
                    )
                    self.assertEqual(reg_sha, bundle["registry_sha256"])
                    ctx.recheck("primary")
                    backend = session.ProductionBackend(
                        runner.DEFAULT_BINARY_PATH.resolve(), ref_map, Path(ref_store), self.protocol["resource_policy"], ctx
                    )
                    slot0 = next(s for s in self.plan if s["slot_purpose"] == "primary")
                    backend.authorize(slot0)
                    # Stop at stop_after=0 cleanly writes session manifest without executing any subprocess
                    zero_res = session.session(sess_path, self.protocol, self.manifest, [s for s in self.plan if s["slot_purpose"] != "diagnostic"], backend, execute=True, stop_after=0)
                    self.assertEqual(zero_res["records"], [])
                popen.assert_not_called()

            # Confirm real anchors file remains empty
            real_anchors = json.loads((runner.REPO_ROOT / auth.ANCHORS).read_text())
            self.assertEqual(real_anchors["approved_envelopes"], {})

    @unittest.skipUnless(
        CANDIDATE_ARCHIVE.is_file() and DURABLE_ARCHIVE.is_file() and runner.DEFAULT_BINARY_PATH.is_file() and HISTORICAL_R1_BINARY.is_file(),
        "Required binaries or archives unavailable",
    )
    def test_04_negative_matrix_a_n01_through_a_n20(self):
        bundle = refs.resolve_trusted_bundle("R2_CANDIDATE_BUNDLE")
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            env = {
                "stable": {"fake": True},
                "dynamic": {"filesystem_free_bytes": 20 << 30, "memory": {"MemAvailable": 1 << 30}},
            }
            source = {p: "a" * 64 for p in auth.SOURCE_PATHS}
            sess = parent / "sess"
            env_file = parent / "env.json"

            def make_doc(**overrides):
                d = {
                    "schema": auth.ENVELOPE,
                    "approval_status": "MEASUREMENT_AUTHORIZED",
                    "scope": "P4b-3B.1_CONFIRMATORY_MEASUREMENT",
                    "experiment_id": "R21_NEG_TEST",
                    "approved_session_path": str(sess.resolve()),
                    "reviewer_approval_ref": "test-rev",
                    "coordinator_approval_ref": "test-coord",
                    "protocol_sha256": runner.PROTOCOL_SHA256,
                    "manifest_sha256": self.protocol["manifest_sha256"],
                    "schedule_sha256": self.protocol["schedule_sha256"],
                    "registry_sha256": bundle["registry_sha256"],
                    "bundle_id": bundle["bundle_id"],
                    "correctness_bundle": refs.bundle_identity_payload(bundle),
                    "native_binary_sha256": bundle["native_binary_sha256"],
                    "native_source_commit": bundle["native_source_commit"],
                    "implementation_revision": "b" * 40,
                    "source_file_sha256": dict(source),
                    "resource_policy": self.protocol["resource_policy"],
                    "statistical_amendment_sha256": runner.compute_sha256(auth.AMENDMENT),
                    "environment_sha256": session.digest(env["stable"]),
                    "correctness_policy": "M0_AND_SAME_VARIANT_DIFFERENTIAL",
                    "c3_bare_policy": "ALLOW_LIMITED_BARE_CONTROL_NOT_ORACLE",
                    "disc07_disposition": "RETAIN_DISC_07_UNCHANGED",
                    "phases": ["primary"],
                    "stratum_rule": "R1_REGISTERED",
                    "resource_regression_rule": "SAME_CATEGORY",
                }
                d.update(overrides)
                raw = runner.canonical_json_bytes(d)
                env_file.write_bytes(raw)
                return {runner.compute_sha256(env_file): {k: d[k] for k in ("experiment_id", "reviewer_approval_ref", "coordinator_approval_ref")}}

            def expect_auth_block(binary, registry, archive, expected_regex, doc_overrides=None, env_sources=None):
                anchor = make_doc(**(doc_overrides or {}))
                with patch.object(auth, "_reviewed_anchors", return_value=anchor), \
                     patch.object(auth, "environment_snapshot", return_value=env), \
                     patch.object(auth, "_source_state", return_value=(env_sources if env_sources is not None else source)), \
                     patch.object(runner.subprocess, "Popen", side_effect=AssertionError("No launch")):
                    with self.assertRaisesRegex(runner.AuthorizationBlockedError, expected_regex):
                        auth.authorize(env_file, binary, registry, archive, sess, phase="primary")

            # A-N01: R2 binary + R1 registry
            with self.subTest("A-N01"):
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.REGISTRY_PATH, CANDIDATE_ARCHIVE, "REGISTRY_BUNDLE_MISMATCH")
            # A-N02: R2 binary + R1 archive
            with self.subTest("A-N02"):
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, DURABLE_ARCHIVE, "ARCHIVE_HASH_MISMATCH")
            # A-N03: R1 binary + R2 registry
            with self.subTest("A-N03"):
                expect_auth_block(HISTORICAL_R1_BINARY, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "BINARY_HASH_MISMATCH")
            # A-N04: R1 binary + R2 archive
            with self.subTest("A-N04"):
                expect_auth_block(HISTORICAL_R1_BINARY, refs.REGISTRY_PATH, CANDIDATE_ARCHIVE, "BINARY_HASH_MISMATCH")
            # A-N05: R2 archive + wrong candidate registry
            with self.subTest("A-N05"):
                wrong_reg = runner.REPO_ROOT / "configs/rapidflow_apx_p4b_r2_candidate_registry.json"
                expect_auth_block(runner.DEFAULT_BINARY_PATH, wrong_reg, CANDIDATE_ARCHIVE, "REGISTRY_BUNDLE_MISMATCH")
            # A-N06: R2 registry + tampered archive hash
            with self.subTest("A-N06"):
                bad_arch = parent / "bad_size_or_hash.tar.gz"
                bad_arch.write_bytes(b"corrupted" * 100)
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, bad_arch, "ARCHIVE_HASH_MISMATCH")
            # A-N07: Archive matching member content but wrong manifest identity
            with self.subTest("A-N07"):
                m_arch = parent / "bad_manifest.tar.gz"
                def mod_man(e):
                    e = dict(e)
                    m = json.loads(e["evidence-manifest.json"])
                    m["tampered"] = True
                    e["evidence-manifest.json"] = runner.canonical_json_bytes(m)
                    return e
                _rewrite_tar_with_mutation(CANDIDATE_ARCHIVE, m_arch, mod_man)
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "ARCHIVE_IDENTITY_MISMATCH|MANIFEST_HASH_MISMATCH"):
                    refs.validate_registry(refs.CANDIDATE_REGISTRY_PATH, m_arch, parent / "s07", self.protocol, self.manifest, bundle="R2_CANDIDATE_BUNDLE")
            # A-N08: Wrong native source commit
            with self.subTest("A-N08"):
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "ACTIVATION_IDENTITY_MISMATCH: native_source_commit", {"native_source_commit": "0" * 40})
            # A-N09: Wrong binary size (padded copy of R2 binary)
            with self.subTest("A-N09"):
                pad_bin = parent / "padded_bin"
                pad_bin.write_bytes(runner.DEFAULT_BINARY_PATH.read_bytes() + b"\x00")
                expect_auth_block(pad_bin, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "BINARY_HASH_MISMATCH")
            # A-N10: Changed registry bytes
            with self.subTest("A-N10"):
                mod_reg = parent / "mod_reg.json"
                mod_reg.write_bytes(refs.CANDIDATE_REGISTRY_PATH.read_bytes().replace(b"P4B_CONT_01", b"P4B_CONT_99", 1))
                expect_auth_block(runner.DEFAULT_BINARY_PATH, mod_reg, CANDIDATE_ARCHIVE, "REGISTRY_BUNDLE_MISMATCH")
            # A-N11..A-N15: Archive member tampering (missing, modified compressed, modified raw, duplicate entry, extra member)
            for label, fn, pat in (
                ("A-N11_missing", lambda e: {k: v for k, v in e.items() if k != "r2-ref/P4B_CONT_01/BARE.json.gz"}, "ARCHIVE_MEMBER_INVENTORY_MISMATCH"),
                ("A-N12_compressed", lambda e: {**e, "r2-ref/P4B_CONT_01/BARE.json.gz": gzip.compress(b"{}", mtime=0)}, "ARCHIVE_MEMBER_IDENTITY_MISMATCH"),
                ("A-N15_extra", lambda e: {**e, "r2-ref/EXTRA_UNEXPECTED.txt": b"extra"}, "ARCHIVE_MEMBER_INVENTORY_MISMATCH"),
            ):
                with self.subTest(label):
                    tampered = parent / f"{label}.tar.gz"
                    _rewrite_tar_with_mutation(CANDIDATE_ARCHIVE, tampered, fn)
                    spec = copy.deepcopy(bundle)
                    spec["reference_archive_size_bytes"] = tampered.stat().st_size
                    spec["reference_archive_sha256"] = runner.compute_sha256(tampered)
                    with tarfile.open(tampered, "r:gz") as tf:
                        spec["reference_manifest_sha256"] = runner.sha256_bytes(tf.extractfile("evidence-manifest.json").read())
                    with patch.dict(refs.TRUSTED_BUNDLES, {"TEST_TAMPERED": spec}):
                        spec["bundle_id"] = "TEST_TAMPERED"
                        with self.assertRaisesRegex(runner.LedgerIntegrityError, pat):
                            refs.unpack_references(tampered, parent / f"u_{label}", bundle=spec)
            # A-N13: Modified raw reference post-unpack (both compressed digest & decompressed digest guards)
            with self.subTest("A-N13"):
                s13 = parent / "s13"
                s13.mkdir()
                refs.unpack_references(CANDIDATE_ARCHIVE, s13, bundle="R2_CANDIDATE_BUNDLE")
                reg13 = {(r["workload_id"], r["variant_id"]): r for r in json.loads(refs.CANDIDATE_REGISTRY_PATH.read_text())["entries"]}
                ent13 = dict(reg13[("P4B_CONT_01", "BARE")])
                raw_tamper = gzip.compress(b'{"tampered":true}', mtime=0)
                (s13 / ent13["raw_output_relative_path"]).write_bytes(raw_tamper)
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "RAW_EVIDENCE_MISMATCH"):
                    refs.read_reference(s13, ent13)
                ent13["compressed_output_sha256"] = runner.sha256_bytes(raw_tamper)
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "REFERENCE_RAW_IDENTITY_MISMATCH"):
                    refs.read_reference(s13, ent13)
            # A-N14: Duplicate workload/variant in store replay.jsonl
            with self.subTest("A-N14"):
                s14 = parent / "s14"
                s14.mkdir()
                refs.unpack_references(CANDIDATE_ARCHIVE, s14, bundle="R2_CANDIDATE_BUNDLE")
                rl = s14 / "r2-ref/replay.jsonl"
                lines = rl.read_bytes().splitlines()
                rl.write_bytes(b"\n".join(lines + [lines[0]]) + b"\n")
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "DUPLICATE_REFERENCE"):
                    refs.rebuild_from_store(s14, self.protocol, self.manifest, prefix_dir="r2-ref", binary_sha256=runner.NATIVE_SHA256, native_source_commit=runner.NATIVE_SOURCE_COMMIT, replay_rev=refs.CANDIDATE_REPLAY_REV, trusted_root=refs.CANDIDATE_ROOT)
            # A-N16: Mixed R1/R2 reference inventory row
            with self.subTest("A-N16"):
                s16 = parent / "s16"
                s16.mkdir()
                refs.unpack_references(CANDIDATE_ARCHIVE, s16, bundle="R2_CANDIDATE_BUNDLE")
                rl = s16 / "r2-ref/replay.jsonl"
                lines = rl.read_bytes().splitlines()
                row0 = json.loads(lines[0])
                row0["binary_sha256"] = runner.HISTORICAL_R1_NATIVE_SHA256
                lines[0] = json.dumps(row0).encode()
                rl.write_bytes(b"\n".join(lines) + b"\n")
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "REFERENCE_ROW_IDENTITY"):
                    refs.rebuild_from_store(s16, self.protocol, self.manifest, prefix_dir="r2-ref", binary_sha256=runner.NATIVE_SHA256, native_source_commit=runner.NATIVE_SOURCE_COMMIT, replay_rev=refs.CANDIDATE_REPLAY_REV, trusted_root=refs.CANDIDATE_ROOT)
            # A-N17: Changed source-lock dependency
            with self.subTest("A-N17"):
                mod_src = dict(source)
                mod_src["scripts/canonical_output_comparator.py"] = "f" * 64
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "ACTIVATION_IDENTITY_MISMATCH: source_file_sha256", env_sources=mod_src)
            # A-N18 & A-N19: Bundle selector mismatch & unapproved bundle identity
            with self.subTest("A-N18"):
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "UNREVIEWED_OR_MISMATCHED_BUNDLE_ID", {"bundle_id": "HISTORICAL_R1_BUNDLE"})
            with self.subTest("A-N19"):
                bad_cb = dict(refs.bundle_identity_payload(bundle), reference_archive_sha256="f" * 64)
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "ACTIVATION_IDENTITY_MISMATCH: correctness_bundle", {"correctness_bundle": bad_cb})
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "UNREVIEWED_TRUST_BUNDLE"):
                    refs.resolve_trusted_bundle("UNREVIEWED_CALLER_BUNDLE")
            # Deprecation checks: v1 schema & forbidden r1_evidence field
            with self.subTest("v1_deprecated"):
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "HISTORICAL_R1_ACTIVATION_DEPRECATED", {"schema": auth.LEGACY_R1_ENVELOPE})
            with self.subTest("r1_field_forbidden"):
                expect_auth_block(runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, "HISTORICAL_FIELD_NOT_ALLOWED_IN_V2", {"r1_evidence": refs.ROOT})
            # A-N20: Actual production activation anchors remain empty
            with self.subTest("A-N20"):
                self.assertEqual(json.loads((runner.REPO_ROOT / auth.ANCHORS).read_text())["approved_envelopes"], {})


class TestR21NativeResourceWitness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.plan = list(runner.ordered_planned_slots(cls.schedule))
        cls.workloads = {row["workload_id"]: row for row in cls.manifest["workloads"]}

    @unittest.skipUnless(WITNESS_BINARY.is_file(), "R2.1 resource witness binary not built")
    def test_05_real_native_in_step_alloc_witness_paths_bw01_to_bw04(self):
        proc = subprocess.run([str(WITNESS_BINARY), "--verify-paths"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        payload = json.loads(proc.stdout.strip())
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["witness_paths"], ["B-W01", "B-W02", "B-W03", "B-W04"])

    @unittest.skipUnless(WITNESS_BINARY.is_file() and CANDIDATE_ARCHIVE.is_file(), "R2.1 witness binary or archive missing")
    def test_06_real_native_in_step_resource_end_to_end_session_and_replay(self):
        wit_sha = runner.compute_sha256(WITNESS_BINARY)
        self.assertNotEqual(wit_sha, runner.NATIVE_SHA256)
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            store = td_path / "store"
            store.mkdir()
            reg, _ = refs.validate_registry(
                refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, store, self.protocol, self.manifest, bundle="R2_CANDIDATE_BUNDLE"
            )
            # Re-bind witness test registry entries strictly to witness binary hash under VALIDATION_RESOURCE_WITNESS
            wit_reg = {
                k: {**v, "binary_sha256": wit_sha, "native_source_commit": runner.NATIVE_SOURCE_COMMIT}
                for k, v in reg.items()
                if k == ("P4B_CONT_01", "BARE")
            }
            for purpose in ("diagnostic", "primary"):
                with self.subTest(purpose=purpose):
                    slot = next(
                        s for s in self.plan
                        if s["workload_id"] == "P4B_CONT_01" and s["variant_id"] == "BARE" and s["slot_purpose"] == purpose
                    )
                    sess_dir = td_path / f"sess_{purpose}"
                    backend = ResourceWitnessBackend(WITNESS_BINARY.resolve(), wit_reg, store, self.protocol["resource_policy"])
                    res = session.session(sess_dir, self.protocol, self.manifest, [slot], backend, execute=True)
                    self.assertEqual(res["state"], "SESSION_COMPLETED", res)
                    rec = res["records"][0]
                    self.assertEqual(rec["attempt_outcome"], "CENSORED")
                    self.assertEqual(rec["censoring_reason"], "MEMORY_LIMIT_FAILURE")
                    self.assertEqual(rec["binary_sha256"], wit_sha)
                    self.assertEqual(rec["evidence_kind"], "VALIDATION_RESOURCE_WITNESS")
                    self.assertFalse(rec["measurement_eligible"])
                    # Offline revalidation matches live outcome
                    self.assertEqual(session.session(sess_dir, self.protocol, self.manifest, [slot], backend), res)

                    # Check persisted native JSON produced by genuine C++ operator new -> std::bad_alloc
                    native_out = json.loads((sess_dir / "attempts/slot-0000/attempt-001/native-output.json").read_text())
                    self.assertEqual(native_out["error_category"], "RESOURCE_LIMIT_ERROR")
                    self.assertEqual(native_out["error_message"], "std::bad_alloc")
                    self.assertEqual(native_out["first_failing_step_index"], 1)
                    self.assertEqual(native_out["steps"][0]["status"], "SUCCESS")
                    self.assertEqual(native_out["steps"][1]["status"], "RESOURCE_EXHAUSTED")
                    self.assertEqual(native_out["steps"][1]["commit_status"], "NOT_COMMITTED")
                    self.assertEqual(native_out["steps"][1]["entered_tuples"], [])
                    self.assertIn("step_counters", native_out["steps"][1])
                    if purpose == "diagnostic":
                        prof1 = native_out["steps"][1]["profiling"]
                        self.assertGreater(prof1["search_ns"], 0)
                        self.assertGreaterEqual(prof1["search_ns"], prof1["search_core_ns"])
                    else:
                        self.assertNotIn("profiling", native_out)
                        self.assertNotIn("profiling", native_out["steps"][1])
                    self.assertTrue(all(s["status"] == "UNATTEMPTED" for s in native_out["steps"][2:]))

                    # Official statistical admission rejects VALIDATION_RESOURCE_WITNESS
                    with self.assertRaisesRegex(runner.LedgerIntegrityError, "CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED"):
                        stats.analyze_p4b_ledger(res["records"])
                    with self.assertRaisesRegex(runner.LedgerIntegrityError, "SLOT_IDENTITY_MISMATCH"):
                        stats.analyze_p4b_ledger(res["records"], allow_synthetic_mode=True)

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file(), "Candidate archive missing")
    def test_07_resource_witness_negative_controls(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            store = td_path / "store"
            store.mkdir()
            reg, _ = refs.validate_registry(
                refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, store, self.protocol, self.manifest, bundle="R2_CANDIDATE_BUNDLE"
            )
            r_reg = {k: {**v, "binary_sha256": runner.HISTORICAL_R1_NATIVE_SHA256, "native_source_commit": runner.HISTORICAL_R1_NATIVE_SOURCE_COMMIT} for k, v in reg.items()}
            # Reuse ControlledNative helper against unpacked candidate store cleanly with explicit binary hash
            adapter_base = lambda mut: ControlledNative(r_reg, store, self.protocol["resource_policy"], mut)
            slot_diag = next(s for s in self.plan if s["workload_id"] == "P4B_CONT_01" and s["variant_id"] == "BARE" and s["slot_purpose"] == "diagnostic")
            entry = self.workloads["P4B_CONT_01"]

            def mut_instep_valid(native, process):
                n = len(native["steps"])
                add_profile(native)
                for i in range(1, n):
                    native["steps"][i]["entered_tuples"] = []
                    native["steps"][i]["exited_tuples"] = []
                    native["steps"][i]["commit_status"] = "NOT_COMMITTED"
                    if i == 1:
                        native["steps"][i]["status"] = "RESOURCE_EXHAUSTED"
                        native["steps"][i]["error_category"] = "RESOURCE_LIMIT_ERROR"
                        native["steps"][i]["error"] = "std::bad_alloc"
                        native["steps"][i]["step_counters"] = dict(native["steps"][i]["step_counters"], work_units=1)
                    else:
                        native["steps"][i].pop("step_counters", None)
                        native["steps"][i].pop("profiling", None)
                        native["steps"][i]["status"] = "UNATTEMPTED"
                        native["steps"][i]["error_category"] = "NONE"
                        native["steps"][i]["error"] = ""
                native["execution"].update(attempted_steps=2, committed_steps=1, completed_steps=1, delivered_steps=2, failed_steps=1, unattempted_steps=n - 2)
                native.update(status="FAIL", error_category="RESOURCE_LIMIT_ERROR", error_message="std::bad_alloc",
                              first_failing_step_index=1, first_failure_category="RESOURCE_LIMIT_ERROR", first_failure_message="std::bad_alloc")
                reported_exit(process, 5, "MEMORY_LIMIT_FAILURE")

            def run_neg(name, extra_mut):
                def fn(_, native, process):
                    mut_instep_valid(native, process)
                    extra_mut(native, process)
                    return native, b""
                res = write_attempt(td_path / name, self.protocol, entry, slot_diag, adapter_base(fn))
                self.assertEqual(res["attempt_outcome"], "INVALID", (name, res))

            for d in ("neg1", "neg2", "neg3", "neg4", "neg5", "neg6", "neg7", "neg8"):
                (td_path / d).mkdir()
            run_neg("neg1", lambda n, p: reported_exit(p, 0, "MEMORY_LIMIT_FAILURE"))
            run_neg("neg2", lambda n, p: n["steps"][1].__setitem__("commit_status", "COMMITTED"))
            run_neg("neg3", lambda n, p: n["steps"][1].__setitem__("entered_tuples", [[0, 1, 2]]))
            run_neg("neg4", lambda n, p: n["steps"][1].pop("step_counters"))
            run_neg("neg5", lambda n, p: n["steps"][1].pop("profiling"))
            run_neg("neg6", lambda n, p: n.__setitem__("first_failure_message", "other"))
            run_neg("neg7", lambda n, p: (reported_exit(p, 6, "MEMORY_LIMIT_FAILURE"), n["steps"][1].__setitem__("status", "FAULT_INJECTED")))
            run_neg("neg8", lambda n, p: n.__setitem__("steps", n["steps"][:2]))


class TestR21FullRehearsalAndReconciliation(unittest.TestCase):
    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file(), "Candidate archive unavailable")
    def test_08_full_1450_native_format_shared_validator_rehearsal(self):
        with tempfile.TemporaryDirectory() as td:
            summary, stat_controls = run_r21_simulation(Path(td) / "sim", archive_path=CANDIDATE_ARCHIVE)
            self.assertEqual(summary["schedule_counts"], {"warmup": 200, "primary": 1000, "diagnostic": 250})
            self.assertEqual(summary["registry_entries"], 250)
            self.assertEqual(summary["terminal_unique"], 1450)
            self.assertEqual(summary["shared_validate_native_exec_calls"], 1450 + 9 + 205)
            self.assertEqual(summary["shared_validate_native_replay_calls"], 1450)
            self.assertEqual(summary["native_matching_subprocesses"], 0)
            self.assertTrue(summary["offline_replay_equal"])
            self.assertEqual(summary["outcomes"], {"COMPLETED": 1392, "CENSORED": 58})
            self.assertEqual(stat_controls["invalid-resource"]["decision"]["priority_triggered"], 2)
            self.assertEqual(stat_controls["valid-resource"]["decision"]["priority_triggered"], 3)

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file(), "Candidate archive unavailable")
    def test_09_journal_20_scenarios_and_r2_failure_mutations(self):
        protocol, manifest, schedule = runner.load_frozen_plan()
        plan = list(runner.ordered_planned_slots(schedule))
        entries = {w["workload_id"]: w for w in manifest["workloads"]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = root / "store"
            store.mkdir()
            reg, _ = refs.validate_registry(
                refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, store, protocol, manifest, bundle="R2_CANDIDATE_BUNDLE"
            )
            slots = [plan[0], plan[1]]
            # Journal interruption & tamper matrix on real ControlledR2Native session
            for fault_name in ("after_start", "after_raw", "before_finish"):
                with self.subTest(fault=fault_name):
                    sdir = root / fault_name
                    adapter = ControlledR2Native(reg, store, protocol["resource_policy"])
                    def trip(point, _fn=fault_name):
                        if point == _fn:
                            raise RuntimeError("injected_crash")
                    with self.assertRaisesRegex(RuntimeError, "injected_crash"):
                        session.session(sdir, protocol, manifest, slots, adapter, execute=True, fault=trip)
                    # Resume fails closed without relaunching (both readback & execute=True)
                    adapter_re = ControlledR2Native(reg, store, protocol["resource_policy"])
                    reopened = session.session(sdir, protocol, manifest, slots, adapter_re)
                    self.assertEqual(reopened["state"], "SESSION_INTERRUPTED")
                    self.assertEqual(reopened["recovery"], "STARTED_WITHOUT_FINISH_REVIEW_REQUIRED")
                    self.assertEqual(adapter_re.calls, [])
                    reexec = session.session(sdir, protocol, manifest, slots, adapter_re, execute=True)
                    self.assertEqual(reexec["state"], "SESSION_INTERRUPTED")
                    self.assertEqual(adapter_re.calls, [])

            # Complete session -> duplicate FINISH, missing artifact, changed digest, chain mutation, wrong identity
            clean_dir = root / "clean2"
            adapter_ok = ControlledR2Native(reg, store, protocol["resource_policy"])
            res = session.session(clean_dir, protocol, manifest, slots, adapter_ok, execute=True)
            self.assertEqual(res["state"], "SESSION_COMPLETED")
            orig_ledger = (clean_dir / "ledger.jsonl").read_bytes()

            for label, tamper in (
                ("duplicate_finish", lambda b: b + b.splitlines()[-1] + b"\n"),
                ("chain_mutation", lambda b: b.replace(b'"previous_sha256":"', b'"previous_sha256":"f', 1)),
            ):
                with self.subTest(label=label):
                    (clean_dir / "ledger.jsonl").write_bytes(tamper(orig_ledger))
                    with self.assertRaises(runner.LedgerIntegrityError):
                        session.session(clean_dir, protocol, manifest, slots, adapter_ok)
                    (clean_dir / "ledger.jsonl").write_bytes(orig_ledger)

            # Missing artifact & changed digest on clean readback
            out0 = clean_dir / "attempts/slot-0000/attempt-001/native-output.json"
            out0_orig = out0.read_bytes()
            out0.write_bytes(b"{}")
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "FINISH_RAW_DIGEST_MISMATCH"):
                session.session(clean_dir, protocol, manifest, slots, adapter_ok)
            out0.unlink()
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "MISSING_OR_UNSAFE_ARTIFACT"):
                session.session(clean_dir, protocol, manifest, slots, adapter_ok)
            out0.write_bytes(out0_orig)

            # Wrong reference binary identity injected into ControlledR2Native session
            bad_reg = copy.deepcopy(reg)
            bad_key = (slots[0]["workload_id"], slots[0]["variant_id"])
            bad_reg[bad_key]["binary_sha256"] = runner.HISTORICAL_R1_NATIVE_SHA256
            bad_sess = root / "bad_ref_id"
            bad_adapter = ControlledR2Native(bad_reg, store, protocol["resource_policy"])
            halted = session.session(bad_sess, protocol, manifest, [slots[0]], bad_adapter, execute=True)
            self.assertEqual(halted["state"], "SESSION_HALTED")
            self.assertEqual(halted["records"][0]["attempt_outcome"], "INVALID")
            self.assertEqual(halted["records"][0]["censoring_reason"], "PROVENANCE_ERROR")

            # Targeted R2 failure-path mutations (malformed supervisor/macro/profiling/separator/suffix)
            perf_slot = next(s for s in plan if s["workload_id"] == "P4B_CONT_01" and s["variant_id"] == "BARE" and s["slot_purpose"] == "primary")
            muts = [
                ("timing_violation", lambda _, n, p: (n["macro_timing"].__setitem__("streaming_loop_wall_ns", 99_999_999), (n, b""))[1]),
                ("failed_commit_contradiction", lambda _, n, p: (n["steps"][0].__setitem__("status", "FAILED_STEP"), (n, b""))[1]),
                ("null_supervisor", lambda _, n, p: (p.__setitem__("supervisor", None), (n, b""))[1]),
                ("null_macro", lambda _, n, p: (n.__setitem__("macro_timing", None), (n, b""))[1]),
                ("perf_prof_leak", lambda _, n, p: (n.__setitem__("profiling", {"enabled": True}), (n, b""))[1]),
            ]
            for mname, fn in muts:
                with self.subTest(mname=mname):
                    adir = root / f"mut_{mname}"
                    adir.mkdir()
                    r = write_attempt(adir, protocol, entries["P4B_CONT_01"], perf_slot, ControlledR2Native(reg, store, protocol["resource_policy"], fn))
                    self.assertEqual(r["attempt_outcome"], "INVALID", (mname, r))


if __name__ == "__main__":
    unittest.main()
