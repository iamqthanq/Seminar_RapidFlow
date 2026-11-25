# RapidFlow-APX P4b-3B.0 R2.1 Implementation & Audit Closure Report

**Handoff status:** `P4B_3B0_R2_1_READY_FOR_INDEPENDENT_REVIEW`  
**Phase authorization:** `P4B_3B_1_NOT_AUTHORIZED`

- **R2.1 starting baseline (`R2 HEAD`):** `90c059e337daaae62377d165d2b86e099a57c108`
- **Production C++ source commit (`NATIVE_SOURCE_COMMIT`):** `115ce2844d91f6a6588fe0ac50b1179bc3b38012` (zero edits to `extensions/rapidflow_apx/`)
- **Tested logic & verification commit (`TESTED_LOGIC_COMMIT`):** `39396c1463c2feabfd53cf7ee5480060755a4b47`
- **Pinned upstream baseline (`baselines/rapidflow/source`):** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine)
- **Production R2 candidate binary (`build/rapidflow_apx_native` & `build-r2-candidate/rapidflow_apx_native`):** `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876` (`970,632` bytes, unchanged bit-for-bit)
- **Test-only native witness binary (`rapidflow_apx_r21_resource_witness`):** `fcdd9c5efd97bf59604d4d3204db03e28bb93f0b42488aa02d252b0a4aa8c7a9` (`850,608` bytes)
- **Candidate reference archive (`p4b-r2-correctness-reference-bundle.tar.gz`):** `8efd7c65856c675773249d19b20165106d92917af56913be3730b5a2de044464` (`33,681,113` bytes, `302` members under `r2-ref/`)
- **Candidate correctness registry (`configs/rapidflow_apx_p4b_r2_1_candidate_registry.json`):** `40a28b4d963d8c652bc49ab231aa2ccb6626e3de9b4badc3b3a13a03620fe0b4` (`505,468` bytes; `160 M0_VERIFIED`, `64 DIFFERENTIAL_M2_VERIFIED`, `16 CORRECTNESS_NOT_FULLY_CERTIFIED`, `10 C3_BUDGET_UNAVAILABLE`)

## 1. Post-R2 Findings Remediation Summary (RED -> GREEN)

| Finding | Severity | RED on `90c059e` | Remediation & GREEN on `39396c1` |
|---|---|---|---|
| `R21-FIND-01` | HIGH | Production activation / registry bound `HISTORICAL_R1_BUNDLE` (`f02e6ff9...`), R2 review archive had incompatible `cand-replay-250/` layout (`34,864,264` bytes), and `NativeBackend(3fa35111...)` halted `PROVENANCE_ERROR` against R1 registry rows. | Added `TRUSTED_BUNDLES` (`HISTORICAL_R1_BUNDLE` + `R2_CANDIDATE_BUNDLE`), packed verified `p4b-r2-correctness-reference-bundle.tar.gz` (`8efd7c65...`), reconstructed `configs/rapidflow_apx_p4b_r2_1_candidate_registry.json` (`40a28b4d...`), upgraded activation schema to `p4b-detached-activation-v2` + `R2_CANDIDATE_BUNDLE` (rejecting `v1` and `r1_evidence`), and verified `test_01..test_04` (`A-N01..A-N20`). |
| `R21-FIND-02` | HIGH | R2 tested pre-step `RLIMIT_AS` init OOM (`steps == []`) and JSON mutation for in-step OOM without executing genuine C++ allocation failure during graph/NLF/M2/sink execution. | Added dedicated test-only CMake target `rapidflow_apx_r21_resource_witness` (`tests/native/rapidflow_apx_engines/src/r21_resource_witness.cpp`) leaving `rapidflow_apx_native` bit-for-bit untouched. Verified genuine `operator new -> std::bad_alloc` across `B-W01..B-W04` (`--verify-paths`) and CLI step-1 M2 search (`exit 5`, `RESOURCE_LIMIT_ERROR`, `std::bad_alloc`, `RESOURCE_EXHAUSTED`, `SearchTimingGuard` unwind, zero leaked mutations) ingested through `ResourceWitnessBackend -> session() -> _validate_native()`, plus 8-case negative resource controls (`test_05..test_07`). |
| `R21-FIND-03` | MEDIUM | R2 `test_08` used `session.SyntheticBackend` which bypassed `_validate_native()`. | Built `ControlledR2Native` (inheriting `NativeBackend`) projecting the `250` candidate reference artifacts into complete `native-output.json` and `process.json`/`supervisor` structures routed through `_validate_native()`. Verified full `1,450`-slot durable rehearsal (`1,392 COMPLETED`, `58 CENSORED`, `0 INVALID/FAILED`, `0` native subprocesses, `2,869` exec + `1,450` replay `_validate_native` calls across stops `9, 205, 1205, 1450`), self-contained `test_08` (`stops=(9, 205, 1450)`, `1,664 + 1,450` calls), `test_09`, and statistical priority `2` / `3` resource controls. |
| `R21-FIND-04` | MEDIUM | Historical `docs/rapidflow_apx_p4b_3b0_r2_acceptance_matrix.json` condensed engineering checks into `15` rows instead of all `59` R2 assignment gates. | Created `docs/rapidflow_apx_p4b_3b0_r2_1_gate_reconciliation.json` mapping every single original R2 gate (`R2-A01..R2-N05`, `59/59`), all R2.1 technical gates (`R21-A01..R21-E09`, `39/39`), and `2` external `PENDING_EXTERNAL_APPROVAL` gates with non-null verified artifact SHA-256 digests. |

## 2. Work Package E — R2 Documentation Consistency Audit

While leaving historical R1 and R2 files unmodified on disk (`2ea4f4c3...`, `22b5245d...`, `a122454b...`, `f1dcbaff...`, `docs/*_r2_*`), direct code inspection identified and formally clarifies five documentation drifts from R2:

1. **Profiling flags by execution purpose (`tests/native/rapidflow_apx_engines/src/harness_runner.cpp:545-559`):**
   - `performance`: forces `profiling_enabled = false`, `deep_diagnostics_enabled = false`, and rejects `--profile` / `--profile-output` with `ExitCode::CLI_ARG_ERROR` (`1`).
   - `diagnostic`: forces `profiling_enabled = true`, `deep_diagnostics_enabled = false`.
   - `correctness`: forces `deep_diagnostics_enabled = true` and preserves `profiling_enabled = opts.profiling_enabled` (`false` unless `--profile` is explicitly passed).
2. **`ExitCode` enum correction (`tests/native/rapidflow_apx_engines/src/harness_runner.h:13-24`):**
   - `docs/rapidflow_apx_p4b_3b0_r2_native_failure_contract.md` mistakenly wrote `ExitCode::STEP_FAILURE (7)`. Actual C++ enum values are `STEP_FAILURE = 6` and `IO_ERROR = 7` (`RESOURCE_LIMIT_ERROR = 5`, `WORK_BUDGET_EXCEEDED = 4`, `UNCAUGHT_ERROR = 8`, `RESULT_DELIVERY_ERROR = 9`).
3. **`AuthoritativeGraph` tentative overlay delete members (`extensions/rapidflow_apx/include/rapidflow_apx/authoritative_graph.h:169-188`):**
   - `docs/rapidflow_apx_p4b_3b0_r2_native_failure_contract.md` referenced illustrative names `tx_pending_deletes_` / `pending_overlay_deletes_`. Actual C++ members and helpers are `in_tx_`, `tx_record_` (`TxRecord`), `is_key_tentatively_deleted(uint64_t key)`, and `has_tentative_overlay_delete(uint32_t u)`.
4. **Zero-allocation OOM error payload scope (`extensions/rapidflow_apx/src/transaction_manager.cpp:104-114`):**
   - `abort_oom_noalloc` assigns `"std::bad_alloc"` (`14` chars) after `noexcept` `tx_scope.rollback_if_needed()`. Relying on 15-byte short-string optimization to avoid heap allocation is **specific to GNU libstdc++ (`g++ 13.3.0`)** verified in this toolchain and is documented as a toolchain assumption rather than a portable ISO C++ guarantee.
5. **R2 rehearsal scope & future research boundary (`docs/rapidflow_apx_p4b_3b0_r2_implementation_report.md`):**
   - Clarifies that R2 `test_08` used `SyntheticBackend` (closed in R2.1 via `ControlledR2Native` and `_validate_native`), while synthetic rehearsal retains `evidence_kind = SYNTHETIC_VALIDATION` and `0` official confirmatory slots. Any future architectural redesign (e.g., adaptive NLF or confirmatory execution optimization) remains strictly outside P4b-3B.0 R2.1 scope.

## 3. Verification Executed on Clean `TESTED_LOGIC_COMMIT` (`39396c1463c2feabfd53cf7ee5480060755a4b47`)

- **Full Repository Regression:** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py' -v` -> **Ran `896` tests in `2816.922s` (`OK`, `0` failures, `0` errors, `0` skips)**, including all R1, R2, and R2.1 (`test_01..test_09`) suites.
- **Native Binary Invariance After Standard CMake Build:** Both `tests/native/rapidflow_apx_engines/build/rapidflow_apx_native` and `build-r2-candidate/rapidflow_apx_native` retain exact SHA-256 `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876`.
- **Preservation Audit (`r21-preservation-result.json`):** `110` tracked historical/frozen paths verified, `0` unexpected drifts.
- **Authorization & Official Slots:** `--dry-run` -> `execution_authorized: false`, `launched_subprocess_count: 0`; `--execute-primary` -> exit `2` (`MEASUREMENT_NOT_AUTHORIZED: no detached activation`); `approved_envelopes: {}`; Draft PR `#39` OPEN/DRAFT; Issue `#38` OPEN; `0` official benchmark slots executed.
