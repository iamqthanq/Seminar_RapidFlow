"""Focused R2.1.1 targeted verification tests closing R21-AUD-01 through R21-AUD-04."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import p4b_activation as auth
from scripts import p4b_correctness_registry as refs
from scripts import p4b_experiment_session as session
from scripts import run_rapidflow_apx_p4b as runner
from scripts import summarize_rapidflow_apx_p4b as stats
from tests.test_rapidflow_apx_p4b_3b0 import CANDIDATE_ARCHIVE
from tests.test_rapidflow_apx_p4b_3b0_r2_1 import (
    ResourceWitnessBackend,
    WITNESS_BINARY,
    _rewrite_tar_with_mutation,
)


class TestR211BundleAndActivation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.plan = list(runner.ordered_planned_slots(cls.schedule))

    def test_01_resolve_trusted_bundle_input_hardening(self):
        cand = refs.resolve_trusted_bundle("R2_CANDIDATE_BUNDLE")
        hist = refs.resolve_trusted_bundle("HISTORICAL_R1_BUNDLE")
        self.assertEqual(cand["bundle_id"], "R2_CANDIDATE_BUNDLE")
        self.assertEqual(cand["replay_revision"], refs.CANDIDATE_REPLAY_REV)
        self.assertEqual(hist["bundle_id"], "HISTORICAL_R1_BUNDLE")
        self.assertEqual(refs.resolve_trusted_bundle(None)["bundle_id"], "HISTORICAL_R1_BUNDLE")

        # Complete candidate dictionary resolves cleanly, and extra keys cannot override trusted fields
        with_extra = dict(refs.bundle_identity_payload(cand), trusted_root={"tampered": True}, expected_member_count=1)
        resolved = refs.resolve_trusted_bundle(with_extra)
        self.assertEqual(resolved, cand)
        self.assertEqual(resolved["expected_member_count"], 302)

        # Structured fail-closed rejections (zero raw KeyError / TypeError)
        bad_cases = [
            ("unknown_str", "UNKNOWN_BUNDLE", "UNREVIEWED_TRUST_BUNDLE"),
            ("missing_field", {"bundle_id": "R2_CANDIDATE_BUNDLE"}, "MISSING_BUNDLE_IDENTITY_FIELD"),
            ("wrong_hash", dict(refs.bundle_identity_payload(cand), reference_archive_sha256="f" * 64), "BUNDLE_IDENTITY_MISMATCH"),
            ("wrong_replay", dict(refs.bundle_identity_payload(cand), replay_revision="101272cf85087df621d5e77118ab12a774720a4e"), "BUNDLE_IDENTITY_MISMATCH"),
            ("wrong_size", dict(refs.bundle_identity_payload(cand), native_binary_size_bytes=cand["native_binary_size_bytes"] + 1), "BUNDLE_IDENTITY_MISMATCH"),
            ("wrong_scalar_bool", dict(refs.bundle_identity_payload(cand), native_binary_size_bytes=True), "INVALID_TRUST_BUNDLE_SHAPE"),
            ("root_list", [], "INVALID_TRUST_BUNDLE_SHAPE"),
            ("root_int", 42, "INVALID_TRUST_BUNDLE_SHAPE"),
            ("unhashable_list", {"bundle_id": []}, "INVALID_TRUST_BUNDLE_SHAPE"),
            ("unhashable_dict", {"bundle_id": {}}, "INVALID_TRUST_BUNDLE_SHAPE"),
            ("null_bundle_id", {"bundle_id": None}, "INVALID_TRUST_BUNDLE_SHAPE"),
        ]
        for label, payload, pattern in bad_cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(runner.LedgerIntegrityError, pattern):
                    refs.resolve_trusted_bundle(payload)

    @unittest.skipUnless(CANDIDATE_ARCHIVE.is_file() and runner.DEFAULT_BINARY_PATH.is_file(), "Candidate archive/binary missing")
    def test_02_r21_aud_01_incorrect_replay_revision_rejected_and_an07_separated(self):
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

            bad_cb = dict(refs.bundle_identity_payload(bundle), replay_revision="101272cf85087df621d5e77118ab12a774720a4e")
            doc = {
                "schema": auth.ENVELOPE,
                "approval_status": "MEASUREMENT_AUTHORIZED",
                "scope": "P4b-3B.1_CONFIRMATORY_MEASUREMENT",
                "experiment_id": "R211_REPLAY_REV_MUT",
                "approved_session_path": str(sess.resolve()),
                "reviewer_approval_ref": "test-rev",
                "coordinator_approval_ref": "test-coord",
                "protocol_sha256": runner.PROTOCOL_SHA256,
                "manifest_sha256": self.protocol["manifest_sha256"],
                "schedule_sha256": self.protocol["schedule_sha256"],
                "registry_sha256": bundle["registry_sha256"],
                "bundle_id": bundle["bundle_id"],
                "correctness_bundle": bad_cb,
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
            env_file.write_bytes(runner.canonical_json_bytes(doc))
            anchor = {runner.compute_sha256(env_file): {k: doc[k] for k in ("experiment_id", "reviewer_approval_ref", "coordinator_approval_ref")}}
            with patch.object(auth, "_reviewed_anchors", return_value=anchor), \
                 patch.object(auth, "environment_snapshot", return_value=env), \
                 patch.object(auth, "_source_state", return_value=source), \
                 patch.object(runner.subprocess, "Popen", side_effect=AssertionError("No native launch")):
                with self.assertRaisesRegex(runner.AuthorizationBlockedError, "ACTIVATION_IDENTITY_MISMATCH: correctness_bundle"):
                    auth.authorize(env_file, runner.DEFAULT_BINARY_PATH, refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, sess, phase="primary")

            # A-N07a vs A-N07b exact boundary checks + positive unmodified archive extraction control
            m_arch = parent / "mutated_manifest.tar.gz"
            def mod_man(e):
                e = dict(e)
                m = json.loads(e["evidence-manifest.json"])
                m["tampered"] = True
                e["evidence-manifest.json"] = runner.canonical_json_bytes(m)
                return e
            _rewrite_tar_with_mutation(CANDIDATE_ARCHIVE, m_arch, mod_man)
            with self.assertRaisesRegex(runner.LedgerIntegrityError, "^ARCHIVE_IDENTITY_MISMATCH$"):
                refs.unpack_references(m_arch, parent / "u_a", bundle="R2_CANDIDATE_BUNDLE")

            iso = copy.deepcopy(bundle)
            iso["bundle_id"] = "TEST_ISO_MANIFEST"
            iso["reference_archive_sha256"] = runner.compute_sha256(m_arch)
            iso["reference_archive_size_bytes"] = m_arch.stat().st_size
            with patch.dict(refs.TRUSTED_BUNDLES, {"TEST_ISO_MANIFEST": iso}):
                with self.assertRaisesRegex(runner.LedgerIntegrityError, "^MANIFEST_HASH_MISMATCH$"):
                    refs.unpack_references(m_arch, parent / "u_b", bundle="TEST_ISO_MANIFEST")

            pos_spec = refs.unpack_references(CANDIDATE_ARCHIVE, parent / "u_pos", bundle="R2_CANDIDATE_BUNDLE")
            self.assertEqual(pos_spec["bundle_id"], "R2_CANDIDATE_BUNDLE")


class TestR211PreciseNativeWitnesses(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol, cls.manifest, cls.schedule = runner.load_frozen_plan()
        cls.plan = list(runner.ordered_planned_slots(cls.schedule))
        cls.workloads = {row["workload_id"]: row for row in cls.manifest["workloads"]}

    @unittest.skipUnless(WITNESS_BINARY.is_file(), "R2.1.1 witness binary not built")
    def test_03_post_callback_delete_witness_bw03b(self):
        wit_sha = runner.compute_sha256(WITNESS_BINARY)
        with tempfile.TemporaryDirectory() as td:
            out_json = Path(td) / "bw03b.json"
            proc = subprocess.run(
                [str(WITNESS_BINARY), "--witness-mode", "delete-post-callback", "--output", str(out_json)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
            doc = json.loads(out_json.read_text())
            self.assertEqual(doc["witness_id"], "B-W03b")
            self.assertEqual(doc["witness_binary_sha256"], wit_sha)
            self.assertEqual(doc["production_binary_sha256"], runner.NATIVE_SHA256)
            self.assertTrue(doc["callback_reached"])
            self.assertTrue(doc["edge_visible_in_callback"])
            self.assertTrue(doc["callback_completed"])
            self.assertTrue(doc["allocation_armed_after_search"])
            self.assertGreater(doc["allocation_triggered"], 0)
            self.assertEqual(doc["identified_failure_phase"], "post_callback_apply_delete_tombstones_insert_key")
            self.assertTrue(doc["rollback_equal"])
            self.assertEqual(doc["status"], "RESOURCE_EXHAUSTED")
            self.assertFalse(doc["committed"])
            self.assertEqual(doc["error_message"], "std::bad_alloc")
            self.assertEqual(doc["positive_control_status"], "SUCCESS")
            self.assertFalse(doc["positive_control_graph_after"]["has_edge_02"])

    @unittest.skipUnless(WITNESS_BINARY.is_file() and CANDIDATE_ARCHIVE.is_file(), "R2.1.1 witness binary or archive missing")
    def test_04_late_search_cli_witness_end_to_end_diag_and_perf(self):
        wit_sha = runner.compute_sha256(WITNESS_BINARY)
        self.assertNotEqual(wit_sha, runner.NATIVE_SHA256)
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            late_bin = td_path / "rapidflow_apx_r21_late_resource_witness"
            late_bin.symlink_to(WITNESS_BINARY.resolve())
            self.assertEqual(runner.compute_sha256(late_bin), wit_sha)

            store = td_path / "store"
            store.mkdir()
            reg, _ = refs.validate_registry(
                refs.CANDIDATE_REGISTRY_PATH, CANDIDATE_ARCHIVE, store, self.protocol, self.manifest, bundle="R2_CANDIDATE_BUNDLE"
            )
            wit_reg = {
                k: {**v, "binary_sha256": wit_sha, "native_source_commit": runner.NATIVE_SOURCE_COMMIT}
                for k, v in reg.items()
                if k == ("P4B_CONT_01", "BARE")
            }
            entry = self.workloads["P4B_CONT_01"]
            wl = json.loads((runner.REPO_ROOT / entry["fixture_path"]).read_text())

            for purpose in ("diagnostic", "primary"):
                with self.subTest(purpose=purpose):
                    slot = next(
                        s for s in self.plan
                        if s["workload_id"] == "P4B_CONT_01" and s["variant_id"] == "BARE" and s["slot_purpose"] == purpose
                    )
                    sess_dir = td_path / f"sess_{purpose}"
                    backend = ResourceWitnessBackend(late_bin, wit_reg, store, self.protocol["resource_policy"])
                    res = session.session(sess_dir, self.protocol, self.manifest, [slot], backend, execute=True)
                    self.assertEqual(res["state"], "SESSION_COMPLETED", res)
                    rec = res["records"][0]
                    self.assertEqual(rec["attempt_outcome"], "CENSORED")
                    self.assertEqual(rec["censoring_reason"], "MEMORY_LIMIT_FAILURE")
                    self.assertEqual(rec["evidence_kind"], "VALIDATION_RESOURCE_WITNESS")
                    self.assertFalse(rec["measurement_eligible"])
                    self.assertEqual(session.session(sess_dir, self.protocol, self.manifest, [slot], backend), res)

                    native_out = json.loads((sess_dir / "attempts/slot-0000/attempt-001/native-output.json").read_text())
                    self.assertEqual(native_out["error_category"], "RESOURCE_LIMIT_ERROR")
                    self.assertEqual(native_out["error_message"], "std::bad_alloc")
                    self.assertEqual(native_out["first_failing_step_index"], 1)
                    self.assertEqual(len(native_out["steps"]), len(wl["updates"]))
                    self.assertEqual(native_out["steps"][0]["status"], "SUCCESS")
                    s1 = native_out["steps"][1]
                    self.assertEqual(s1["status"], "RESOURCE_EXHAUSTED")
                    self.assertEqual(s1["commit_status"], "NOT_COMMITTED")
                    self.assertEqual(s1["entered_tuples"], [])
                    self.assertEqual(s1["exited_tuples"], [])
                    self.assertGreater(s1["step_counters"]["work_units"], 0)

                    # Verify full update identity on committed prefix, failed step, and UNATTEMPTED suffix
                    for idx, step in enumerate(native_out["steps"]):
                        upd = wl["updates"][idx]
                        self.assertEqual(step["step_index"], idx)
                        self.assertEqual(step["op"], upd["op"])
                        self.assertEqual(step["u"], upd["u"])
                        self.assertEqual(step["v"], upd["v"])
                        self.assertEqual(step["label"], upd["label"])
                        if idx >= 2:
                            self.assertEqual(step["status"], "UNATTEMPTED")

                    if purpose == "diagnostic":
                        prof = s1["profiling"]
                        self.assertGreater(prof["neighbor_retrieval_ns"], 0)
                        self.assertGreater(prof["search_ns"], 0)
                        self.assertGreaterEqual(prof["search_ns"], prof["neighbor_retrieval_ns"])
                        self.assertGreaterEqual(prof["search_ns"], prof["search_core_ns"])
                        self.assertGreaterEqual(prof["step_total_ns"], prof["search_ns"])
                    else:
                        self.assertNotIn("profiling", native_out)
                        self.assertNotIn("profiling", s1)

                    with self.assertRaisesRegex(runner.LedgerIntegrityError, "CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED"):
                        stats.analyze_p4b_ledger(res["records"])
                    with self.assertRaisesRegex(runner.LedgerIntegrityError, "SLOT_IDENTITY_MISMATCH"):
                        stats.analyze_p4b_ledger(res["records"], allow_synthetic_mode=True)


if __name__ == "__main__":
    unittest.main()
