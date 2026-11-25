import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import run_rapidflow_apx_p4b as p4b_runner
from scripts.canonical_output_comparator import diff_canonical_structures
from scripts.p4a_evidence_contract import compute_sha256
from scripts.run_rapidflow_apx_engines import validate_native_output_schema

BINARY = REPO_ROOT / "tests" / "native" / "rapidflow_apx_engines" / "build" / "rapidflow_apx_native"


class TestP4bMeasurementInfrastructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not BINARY.is_file():
            raise unittest.SkipTest(f"Native binary not found at {BINARY}")

    def test_01_native_seam_boundary_and_diagnostic_exclusion_suite(self) -> None:
        res = subprocess.run(
            [str(BINARY), "--test-p4b-measurement"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, f"Native measurement seam test failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn('"status": "PASS"', res.stdout)

    def test_02_functional_smoke_tuple_parity_and_strict_measurement_validation(self) -> None:
        """Bounded functional check verifying schema & exact tuple equivalence (no comparative timing analysis)."""
        cont1_path = REPO_ROOT / "tests" / "fixtures" / "p4b" / "continuity" / "P4B_CONT_01.json"
        cont1_wl = json.loads(cont1_path.read_text(encoding="utf-8"))
        cont1_sha = compute_sha256(cont1_path)

        with tempfile.TemporaryDirectory(prefix="p4b_meas_smoke_") as td:
            td_path = Path(td)
            # Run reference BARE correctness mode
            corr_out_path = td_path / "corr_bare.json"
            corr_res = p4b_runner.execute_p4b_native_slot(
                binary_path=BINARY,
                fixture_path=cont1_path,
                variant_id="BARE",
                execution_purpose="correctness",
                output_path=corr_out_path,
                workload_data=cont1_wl,
            )
            self.assertEqual(corr_res["exit_code"], 0, corr_res["raw_stderr"])
            corr_json = corr_res["output_data"]
            ok_c, diag_c = validate_native_output_schema(
                corr_json, "m2", cont1_wl["case_id"], workload=cont1_wl, is_success=True, expected_raw_input_sha256=cont1_sha
            )
            self.assertTrue(ok_c, diag_c)
            self.assertIn("graph_state", corr_json["steps"][0])
            self.assertIn("codec_state", corr_json["steps"][0])

            # Test all 4 primary variants in PERFORMANCE mode + B_MERGE in DIAGNOSTIC mode
            for var_id in ("BARE", "A_STATIC", "A_RESIDUAL", "B_MERGE"):
                perf_out = td_path / f"perf_{var_id}.json"
                r = p4b_runner.execute_p4b_native_slot(
                    binary_path=BINARY,
                    fixture_path=cont1_path,
                    variant_id=var_id,
                    execution_purpose="performance",
                    output_path=perf_out,
                    workload_data=cont1_wl,
                )
                self.assertEqual(r["exit_code"], 0, f"{var_id} failed: {r['raw_stderr']}")
                self.assertEqual(r["censoring_reason"], "NONE")
                self.assertGreater(r["peak_rss_bytes"], 0)
                self.assertEqual(r["peak_rss_original_unit"], "KiB")
                self.assertEqual(r["resource_measurement_method"], "linux_proc_self_status_vmhwm")
                self.assertEqual(r["peak_rss_bytes"], r["peak_rss_original_value"] * 1024)
                self.assertGreater(r["outer_wall_ns"], 0)

                pj = r["output_data"]
                self.assertFalse(pj["profiling_enabled"])
                self.assertFalse(pj["deep_diagnostics_enabled"])
                self.assertEqual(pj["measurement_schema_version"], 1)
                self.assertEqual(pj["execution_purpose"], "performance")
                self.assertNotIn("profiling", pj)
                self.assertGreater(pj["macro_timing"]["streaming_loop_wall_ns"], 0)

                # Semantic tuple parity step by step against correctness mode
                self.assertEqual(len(pj["steps"]), len(corr_json["steps"]))
                for s_idx, (sp, sc) in enumerate(zip(pj["steps"], corr_json["steps"])):
                    self.assertNotIn("graph_state", sp)
                    self.assertNotIn("codec_state", sp)
                    self.assertEqual(sorted(sp["entered_tuples"]), sorted(sc["entered_tuples"]), f"{var_id} step {s_idx}")
                    self.assertEqual(sorted(sp["exited_tuples"]), sorted(sc["exited_tuples"]), f"{var_id} step {s_idx}")

                # Option B must also preserve exact legacy logical counters against BARE correctness
                if var_id == "B_MERGE":
                    for lf in ("work_units", "dfs_states", "candidate_evaluations", "candidate_extensions", "committed_emissions"):
                        self.assertEqual(pj["counters"][lf], corr_json["counters"][lf], f"B_MERGE counter {lf} drifted")

    def test_03_measurement_schema_validator_rejects_violations(self) -> None:
        cont1_path = REPO_ROOT / "tests" / "fixtures" / "p4b" / "continuity" / "P4B_CONT_01.json"
        cont1_wl = json.loads(cont1_path.read_text(encoding="utf-8"))
        cont1_sha = compute_sha256(cont1_path)

        with tempfile.TemporaryDirectory(prefix="p4b_meas_neg_") as td:
            out_path = Path(td) / "perf.json"
            r = p4b_runner.execute_p4b_native_slot(
                binary_path=BINARY,
                fixture_path=cont1_path,
                variant_id="BARE",
                execution_purpose="performance",
                output_path=out_path,
                workload_data=cont1_wl,
            )
            valid = r["output_data"]

            # 1. PROFILE_ON erroneously labeled as performance
            m1 = copy.deepcopy(valid)
            m1["profiling_enabled"] = True
            ok, diag, _ = p4b_runner.validate_measurement_output_schema(
                m1, "m2", cont1_wl["case_id"], "performance", "none", "legacy", cont1_wl, cont1_sha, True, 0
            )
            self.assertFalse(ok)
            self.assertIn("PROFILE_OFF", diag)

            # 2. Zero timing rejected on nonempty workload
            m2 = copy.deepcopy(valid)
            m2["macro_timing"]["streaming_loop_wall_ns"] = 0
            ok, diag, _ = p4b_runner.validate_measurement_output_schema(
                m2, "m2", cont1_wl["case_id"], "performance", "none", "legacy", cont1_wl, cont1_sha, True, 0
            )
            self.assertFalse(ok)
            self.assertIn("positive streaming_loop_wall_ns", diag)

            # 3. Leaked graph_state rejected in performance mode
            m3 = copy.deepcopy(valid)
            m3["steps"][0]["graph_state"] = {}
            ok, diag, _ = p4b_runner.validate_measurement_output_schema(
                m3, "m2", cont1_wl["case_id"], "performance", "none", "legacy", cont1_wl, cont1_sha, True, 0
            )
            self.assertFalse(ok)
            self.assertIn("leaked deep diagnostics", diag)

            # 4. Conflicting CLI flags --execution-purpose performance --profile rejected fail-closed
            bad_cli = subprocess.run(
                [
                    str(BINARY),
                    "--engine", "m2",
                    "--execution-purpose", "performance",
                    "--profile",
                    "--workload", str(cont1_path),
                    "--output", str(Path(td) / "bad_cli.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_cli.returncode, 1)

    def test_04_ledger_no_silent_overwrite_and_incomplete_history_preserved(self) -> None:
        rec1 = {"slot_id": "PRM_X_R1_BARE", "ledger_schema_version": p4b_runner.LEDGER_SCHEMA_VERSION, "measurement_eligible": False}
        rec2 = {"slot_id": "PRM_X_R1_BARE", "ledger_schema_version": p4b_runner.LEDGER_SCHEMA_VERSION, "measurement_eligible": True}
        res = p4b_runner.resolve_p4b_ledger_records([rec1, rec2])
        self.assertEqual(len(res["all_attempts_by_slot"]["PRM_X_R1_BARE"]), 2)
        self.assertTrue(res["canonical_slots"]["PRM_X_R1_BARE"]["measurement_eligible"])

        # Two conflicting eligible runs for the same slot MUST fail closed (no cherry-picking fastest)
        rec3 = {"slot_id": "PRM_X_R1_BARE", "ledger_schema_version": p4b_runner.LEDGER_SCHEMA_VERSION, "measurement_eligible": True}
        with self.assertRaises(p4b_runner.LedgerIntegrityError):
            p4b_runner.resolve_p4b_ledger_records([rec2, rec3])

    def test_05_wait4_per_child_peak_rss_independent_not_cumulative_and_diagnostic_mode(self) -> None:
        """Verify wait4 reports independent per-child RSS (small child after large child < large child) and diagnostic mode."""
        cont1_path = REPO_ROOT / "tests" / "fixtures" / "p4b" / "continuity" / "P4B_CONT_01.json"
        cont1_wl = json.loads(cont1_path.read_text(encoding="utf-8"))
        cont1_sha = compute_sha256(cont1_path)

        with tempfile.TemporaryDirectory(prefix="p4b_rss_indep_") as td:
            td_p = Path(td)
            big_py = td_p / "big.json"
            small_py = td_p / "small.json"
            big_py.write_text('{"case_id":"BIG","k":0,"query":{"vertices":[{"id":0,"label":1},{"id":1,"label":1}],"edges":[{"u":0,"v":1,"label":1}]},"initial_graph":{"vertices":[{"id":0,"label":1},{"id":1,"label":1}],"edges":[]},"updates":[{"op":"+","u":0,"v":1,"label":1}]}')
            small_py.write_text(big_py.read_text())

            # Allocate large RSS in child 1 then small RSS in child 2 from a clean ~15 MiB runner process
            # (isolating wait4 per-child semantics from any prior unittest heap growth in this process)
            rss_probe = subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, os, subprocess, sys; "
                        "p1=subprocess.Popen([sys.executable,'-c','b=bytearray(48*1024*1024); b[::4096]=[1]*(48*256)'],"
                        "stdout=subprocess.PIPE,stderr=subprocess.PIPE); p1.stdout.read(); p1.stderr.read(); "
                        "_,st1,ru1=os.wait4(p1.pid,0); p1.returncode=os.waitstatus_to_exitcode(st1); p1.stdout.close(); p1.stderr.close(); "
                        "p2=subprocess.Popen([sys.executable,'-c','x=sum(range(1000))'],"
                        "stdout=subprocess.PIPE,stderr=subprocess.PIPE); p2.stdout.read(); p2.stderr.read(); "
                        "_,st2,ru2=os.wait4(p2.pid,0); p2.returncode=os.waitstatus_to_exitcode(st2); p2.stdout.close(); p2.stderr.close(); "
                        "print(json.dumps({'r1': ru1.ru_maxrss, 'r2': ru2.ru_maxrss}))"
                    ),
                ],
                text=True,
            )
            ru_pair = json.loads(rss_probe)
            self.assertLess(ru_pair["r2"], ru_pair["r1"], "wait4 must measure per-child RSS, not parent RUSAGE_CHILDREN max")

            # Verify DIAGNOSTIC (PROFILE_ON) preserves tuples & fails strict performance validation if mislabeled
            diag_out = td_p / "diag_merge.json"
            dres = p4b_runner.execute_p4b_native_slot(
                binary_path=BINARY,
                fixture_path=cont1_path,
                variant_id="B_MERGE",
                execution_purpose="diagnostic",
                output_path=diag_out,
                workload_data=cont1_wl,
            )
            self.assertEqual(dres["exit_code"], 0, dres["raw_stderr"])
            dj = dres["output_data"]
            self.assertTrue(dj["profiling_enabled"])
            self.assertFalse(dj["deep_diagnostics_enabled"])
            self.assertIn("profiling", dj)
            ok_d, diag_d, _ = p4b_runner.validate_measurement_output_schema(
                dj, "m2", cont1_wl["case_id"], "diagnostic", "none", "merge", cont1_wl, cont1_sha, True, 0
            )
            self.assertTrue(ok_d, diag_d)
            # Mislabeled as performance MUST fail
            ok_mis, _, _ = p4b_runner.validate_measurement_output_schema(
                dj, "m2", cont1_wl["case_id"], "performance", "none", "merge", cont1_wl, cont1_sha, True, 0
            )
            self.assertFalse(ok_mis)

            # Verify child VmHWM isolates true native child peak RSS even under 64 MiB parent bloat
            # (proving immunity to Linux fork() -> preexec -> sig->maxrss parent inheritance)
            import gc
            parent_bloat = bytearray(64 * 1024 * 1024)
            parent_bloat[::4096] = b"\x01" * (64 * 256)
            try:
                bloat_out = td_p / "bloat_isolated.json"
                bres = p4b_runner.execute_p4b_native_slot(
                    binary_path=BINARY,
                    fixture_path=cont1_path,
                    variant_id="BARE",
                    execution_purpose="performance",
                    output_path=bloat_out,
                    workload_data=cont1_wl,
                )
                self.assertEqual(bres["exit_code"], 0)
                self.assertEqual(bres["resource_measurement_method"], "linux_proc_self_status_vmhwm")
                self.assertGreaterEqual(bres["wait4_ru_maxrss_bytes"], 60 * 1024 * 1024)
                self.assertLess(bres["peak_rss_bytes"], 32 * 1024 * 1024)
            finally:
                del parent_bloat
                gc.collect()


if __name__ == "__main__":
    unittest.main()
