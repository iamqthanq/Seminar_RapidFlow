# RapidFlow-APX P4b-3B.0 R2.1.1 Final Targeted Closure Report

**Engineering Handoff Status:** `P4B_3B0_R2_1_1_READY_FOR_FINAL_INDEPENDENT_REVIEW`  
**Confirmatory Execution State:** `P4B_3B_1_NOT_AUTHORIZED`

---

## 1. Baseline, Commits & Binary Invariance

- **R2.1.1 Baseline (`R2.1 HEAD`):** `a7cbbd24c4ae3a0239501b1e76b84a7b3cf81926`
- **Production C++ Source Commit (`NATIVE_SOURCE_COMMIT`):** `115ce2844d91f6a6588fe0ac50b1179bc3b38012` (zero changes to `extensions/rapidflow_apx/`, `baselines/rapidflow/source/`, or `harness_runner.{h,cpp}`)
- **Tested Logic & Test Commit (`TESTED_LOGIC_COMMIT`):** `9f7701b960d5e62546cf34f61ae6ba8e1d275aff`
- **Production Release Binary (`build/` & `build-r2-candidate/` `rapidflow_apx_native`):** `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876` (`970,632` bytes — preserved bit-for-bit)
- **Historical R2.1 Witness Binary:** `fcdd9c5efd97bf59604d4d3204db03e28bb93f0b42488aa02d252b0a4aa8c7a9` (preserved untouched in Desktop historical backup)
- **New R2.1.1 Test-Only Witness Binary (`rapidflow_apx_r21_resource_witness`):** `86c23d7994de4bbdd5c6aedcb7a1572a6792081faf956a25e6b7ba96f5650247` (`build/` and `build-r2-candidate/` identical; test-only, `measurement_eligible = False`)
- **Upstream Pinned RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine)

---

## 2. Targeted RED Reproduction & Findings Closure (`R21-AUD-01 .. R21-AUD-04`)

| Finding | Severity | RED on `a7cbbd2` | Remediation & GREEN on `9f7701b` / `HEAD` |
|---|---|---|---|
| `R21-AUD-01` | MEDIUM | `docs/rapidflow_apx_p4b_3b0_r2_1_candidate_binding_contract.md` wrote `Replay source commit: 101272cf...` while `CANDIDATE_REPLAY_REV`, `TRUSTED_BUNDLES`, and all `250/250` `r2-ref/replay.jsonl` rows pin `115ce2844d91f6a6588fe0ac50b1179bc3b38012`. | Updated contract doc to `115ce2844d91f6a6588fe0ac50b1179bc3b38012`, clearly separating Native Source Commit (`115ce284`), Registered Replay Revision (`115ce284`), and R2 Tested Logic Commit (`101272cf`). Verified candidate archive (`8efd7c65...`) and registry (`40a28b4d...`) unchanged, and added `r2_1_1::test_02` proving `replay_revision = 101272cf...` fails with `ACTIVATION_IDENTITY_MISMATCH: correctness_bundle`. |
| `R21-AUD-02` | MEDIUM | Prior `B-W03` armed OOM before `execute_step` under `NlfMode::RESIDUAL`; CLI early-search hit at `work_units = 0`, `neighbor_retrieval_ns = 0`. | Added `B-W03b` using `ApxTransactionManager::execute_update(DELETE)` with `nlf_index_ = nullptr` (`NlfMode::NONE`) so callback verifies `G_before` (`callback_reached = true`, `callback_completed = true`), arms `g_oom_countdown = 1` immediately before returning `true`, bypasses NLF prepare, and hits `std::bad_alloc` inside `apply_delete() -> tombstones_.insert(key)`. Verified rollback across all 7 graph read APIs + positive control DELETE (`SUCCESS`, edge removed). Also added `CLI_LATE_SEARCH_ALLOCATION_WITNESS` on bounded `P4B_CONT_01` (`inject_after=60`: `work_units = 40`, `neighbor_retrieval_ns = 2356`, `search_ns = 101339 >= search_core_ns = 98983`, `step_total_ns = 112041`, `exit = 5`, `CENSORED / MEMORY_LIMIT_FAILURE` via shared `_validate_native`, plus `performance` counterpart omitting `profiling`). |
| `R21-AUD-03` | LOW | `gate_reconciliation.json` had call arithmetic drift (`R21-C02`), fixed ns constant (`R21-B02`), wrong recovery literal (`R2-J04`), `20`-scenario naming (`R21-C04`), single archive path (`R2-M05`/`R21-E07`). | Preserved original `a7cbbd2` matrix verbatim (`ac545baa...`), corrected all five items cleanly with errata lineage, renamed `test_09_journal_interruption_tamper_and_r2_failure_mutations` accurately (`13` direct scenarios + R1/R2 cross-refs), separated `CURRENT_TARGETED_VERIFIED` / `HISTORICAL_RUNTIME_VERIFIED` / `STATIC_RECONCILED` bases across all 98 gates, and created `docs/rapidflow_apx_p4b_3b0_r2_1_1_acceptance_matrix.json` (`46` technical PASS + `2` external `PENDING_EXTERNAL_APPROVAL`). |
| `R21-AUD-04` | LOW | Doc typo `UNSAFE_ARCHIVE_MEMBER` vs actual `UNSAFE_ARCHIVE_PATH`; `resolve_trusted_bundle({"bundle_id": "R2_CANDIDATE_BUNDLE"})` raised raw `KeyError`; unhashable `{"bundle_id": []}` raised raw `TypeError`; `A-N07` used `ARCHIVE_IDENTITY_MISMATCH\|MANIFEST_HASH_MISMATCH`. | Fixed doc literal; hardened `bundle_identity_payload` & `resolve_trusted_bundle` in `scripts/p4b_correctness_registry.py` (`INVALID_TRUST_BUNDLE_SHAPE`, `MISSING_BUNDLE_IDENTITY_FIELD`, `UNREVIEWED_TRUST_BUNDLE`, `BUNDLE_IDENTITY_MISMATCH`) preserving `bundle=None` default; split `A-N07a` (`^ARCHIVE_IDENTITY_MISMATCH$`) & `A-N07b` (`^MANIFEST_HASH_MISMATCH$` via isolated `patch.dict`), verified in `r2_1_1::test_01..02` & `r2_1::test_04`. |

---

## 3. Verification Provenance (Targeted vs Inherited)

- **Current Targeted Verification on Clean `TESTED_LOGIC_COMMIT` (`9f7701b960d5e62546cf34f61ae6ba8e1d275aff`):**
  - `tests.test_rapidflow_apx_p4b_3b0_r2_1_1` (`test_01..test_04`): `4/4 OK`.
  - `tests.test_rapidflow_apx_p4b_3b0_r2_1`: `test_01..test_07` + `test_09_journal_interruption_tamper_and_r2_failure_mutations`: `8/8 OK` (`test_08` deliberately excluded per impact analysis).
  - `tests.test_rapidflow_apx_p4b_3b0`: `test_direct_cli_rejects_forged_detached_activation_without_opening_session`, `test_detached_approval_requires_trusted_hash_and_all_bindings`, `test_registry_reconstructs_from_trusted_raw_archive`: `3/3 OK`.
  - Total executed in R2.1.1 targeted campaign: **`15` targeted tests PASS (`0` failures, `0` errors, `0` skips)** + standalone `rapidflow_apx_r21_resource_witness --verify-paths` (`B-W01`, `B-W02`, `B-W03a`, `B-W03b`, `B-W04` `PASS`) + CLI `--dry-run` / `--execute-primary` exit `2` + actual clean `_source_state("9f7701b960d5e62546cf34f61ae6ba8e1d275aff")` check after final docs commit.
- **Inherited Historical Verification (`NOT_RUN` in R2.1.1 per Impact Analysis):**
  - **Full repository regression:** `896 / 896 PASS` (`2816.922s`, `0` failures/errors/skips) executed at `39396c1463c2feabfd53cf7ee5480060755a4b47` (`log_sha256: b6879771...`). **Full regression rerun on R2.1.1: `NOT_RUN`** because production C++, upstream RapidFlow, candidate archive/registry, frozen workloads/schedule, statistical analyzer, and session execution logic are unchanged.
  - **Heavy native campaigns (`P3 5-config`, `250-run candidate replay`, `ASan/UBSan`, `1,450-slot rehearsal`):** `NOT_RUN` in R2.1.1; inherited via bit-for-bit unchanged `rapidflow_apx_native` (`3fa35111...`) and unchanged Python validator/statistics modules.

---

## 4. Known Remaining Limitations & Scope Boundaries

1. **Test-Only Resource Witness Scope:** `rapidflow_apx_r21_resource_witness` (`86c23d79...`) uses test-only `::operator new` failure injection and `VALIDATION_RESOURCE_WITNESS` reference adaptation; it cannot become confirmatory measurement data (`measurement_eligible = False`). Also, while `B-W03b` isolates post-callback `apply_delete() -> tombstones_.insert(key)` (`k=1` with `nlf_index_ = nullptr`), `B-W01` and `B-W04` establish transaction-level `INSERT` / post-emission failure + rollback without narrowing further inside individual sub-allocations (`graph` vs `codec` vs `sink` vector growth).
2. **Toolchain Assumption:** Zero-allocation OOM message assignment `"std::bad_alloc"` (`14` chars) in `ApxTransactionManager::abort_oom_noalloc` continues to rely on GNU libstdc++ 15-byte SSO (`g++ 13.3.0`).
3. **Host Governor Readiness:** Host CPU frequency governor observed during R2.1.1 preflight is `powersave` (left untouched per environment policy; future confirmatory execution readiness decision belongs to Coordinator/Technical Lead).
4. **Missing-Evidence Supplementation:** Historical R2 source files (`streaming_engine.cpp`, `transaction_manager.cpp`, `authoritative_graph.cpp`, `harness_runner.cpp`, `p4b_experiment_session.py`, `p4b_native_evidence.py`, `run_rapidflow_apx_p4b.py`, `replay_rapidflow_apx_p4b.py`, `summarize_rapidflow_apx_p4b.py`), P3 recertification summary, sanitizer log, compact 250-row registry projection, `final-nonconfirmatory-analysis.json`, and full 896-test historical inventory have been compiled into the R2.1.1 delta evidence package and Gemini Web review packet.
5. **Authorization Lock:** `configs/rapidflow_apx_p4b_activation_anchors.json` remains `approved_envelopes: {}`; `0` official benchmark slots executed; `P4B_3B_1_NOT_AUTHORIZED`.
