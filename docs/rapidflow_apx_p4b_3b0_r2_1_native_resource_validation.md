# RapidFlow-APX P4b-3B.0 R2.1 Native Resource Witness & Rehearsal Validation

## 1. Production Binary Invariance + Test-Only Resource Witness Architecture

Production binary source files under `extensions/rapidflow_apx/` and `tests/native/rapidflow_apx_engines/src/harness_runner.cpp` are **unchanged** in R2.1 (`NATIVE_SOURCE_COMMIT = 115ce2844d91f6a6588fe0ac50b1179bc3b38012`, binary SHA-256 `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876`).

To exercise genuine C++ `std::bad_alloc` inside active step transactions without mutating production code, R2.1 introduces a dedicated **test-only** executable target `rapidflow_apx_r21_resource_witness` (`tests/native/rapidflow_apx_engines/src/r21_resource_witness.cpp`, SHA-256 `fcdd9c5efd97bf59604d4d3204db03e28bb93f0b42488aa02d252b0a4aa8c7a9`):

- **Mechanism:** Overrides `::operator new` / `::operator new[]` (both throwing and `std::nothrow_t` forms) backed by an atomic allocation countdown armed cleanly via `ScopedOomAfter` and the existing `BoundaryObserverFn` (`MeasurementStage::STEP_EXECUTE_START` / `STEP_EXECUTE_END`) hook in `harness_runner`.
- **Direct Code Paths Witnessed (`--verify-paths`):**
  - `B-W01`: `ApxStreamingEngine::execute_step` `INSERT` mutation (`AuthoritativeGraph::apply_insert` + NLF signature/codec allocations) -> `StepStatus::RESOURCE_EXHAUSTED` (and pre-exec `ERROR` when heap allocation fails during `std::function` capture setup prior to `execute_update`), zero graph/codec drift.
  - `B-W02`: M2 traversal/matching callback `std::bad_alloc` inside active `SearchTimingGuard` -> unwind closes `search_ns` / `search_core_ns` (`search_ns >= search_core_ns`), rolls back graph state cleanly, sets `StepStatus::RESOURCE_EXHAUSTED`, and preserves exact rollback invariants.
  - `B-W03`: `DELETE` mutation/tombstone allocation failure inside transaction -> `AuthoritativeGraph` rollback restores committed edge visibility (`has_edge`, `get_edge_label`, `get_neighbors_merged_into`) without leaking tentative tombstone/overlay state.
  - `B-W04`: `ApxTupleSink::emit_tuple` staging vector reallocation OOM -> rollback clears pending tuples, leaving committed tuple streams empty on the aborted step.

## 2. Live CLI Witness & Shared Validator Ingestion (`test_05..test_07`)

- **CLI Witness (`P4B_CONT_01`, `BARE`, `inject step 1 M2 search`):**
  - **Diagnostic mode:** exit code `5` (`ExitCode::RESOURCE_LIMIT_ERROR`), `status = "FAIL"`, `error_category = "RESOURCE_LIMIT_ERROR"`, `error_message = "std::bad_alloc"`, `first_failing_step_index = 1`, `steps[0].status = "SUCCESS"`, `steps[1].status = "RESOURCE_EXHAUSTED"` with `commit_status = "NOT_COMMITTED"`, `entered_tuples = []`, valid `step_counters`, `profiling` (`search_ns = 38947`, `search_core_ns = 38947`, `step_total_ns = 47986`), and `steps[2..7].status = "UNATTEMPTED"`.
  - **Performance mode:** identical structured failure with root and step `profiling` keys absent.
- **Session & Replay Integration (`test_06`):** `ResourceWitnessBackend` (`evidence_kind = "VALIDATION_RESOURCE_WITNESS"`) runs the native witness executable through `session()` -> `_validate_native()`, classifying both `diagnostic` and `primary` attempts as `CENSORED / MEMORY_LIMIT_FAILURE` (`measurement_eligible = False`) with identical offline replay via `session(..., execute=False)`.
- **Admission Blocking & Negative Controls (`test_06`, `test_07`):** `analyze_p4b_ledger` raises `CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED` (default) and `SLOT_IDENTITY_MISMATCH` (`allow_synthetic_mode=True`) on `VALIDATION_RESOURCE_WITNESS`. All 8 adversarial resource mutations in `test_07` (wrong exit `0`/`6`, `COMMITTED` status, non-empty tuples, missing `step_counters`, missing diagnostic `profiling`, mismatched failure message, `FAULT_INJECTED` status, truncated suffix) are classified `INVALID`.

## 3. Full 1,450-Slot Native-Format Rehearsal & Controls

- **`ControlledR2Native` (`NativeBackend` subclass, `evidence_kind = "SYNTHETIC_VALIDATION"`):** Projects actual `R2_CANDIDATE_BUNDLE` reference outputs (`250` pairs) into full schema-compliant `native-output.json` (`steps`, `execution`, `macro_timing`, `counters`, purpose-gated `profiling`), `process.json`, `supervisor`, and `invocation.json` routed strictly through shared `_validate_native()`.
- **Durable Rehearsal Summary (`production-format-simulation/simulation-summary.json`):**
  - `1,450 / 1,450` scheduled slots (`200 warmup`, `1,000 primary`, `250 diagnostic`): `1,392 COMPLETED`, `58 CENSORED`, `0 INVALID`, `0 FAILED`.
  - Pause/resume verified across warm-up (`9`), warm-up/primary transition (`205`), primary/diagnostic transition (`1,205`), and completion (`1,450`), exercising `2,869` live/resume `_validate_native()` calls plus `1,450` offline replay `_validate_native()` calls (`offline_replay_equal = true`, `native_matching_subprocesses = 0`).
  - `test_08_full_1450_native_format_shared_validator_rehearsal` in CI/regression verifies stops `(9, 205, 1450)` (`1,664` exec + `1,450` replay `_validate_native` calls) and `test_09` verifies journal interruption (`after_start`, `after_raw`, `before_finish`), duplicate `FINISH`, hash chain tampering, missing/mutated artifacts, cross-binary provenance rejection, and malformed/leaked profiling mutations.
  - Statistical negative/positive resource controls confirm priority `2` (`INCONCLUSIVE`) on invalid resource mutation and priority `3` (`NO_GO / RESOURCE_FEASIBILITY_REGRESSION`) on valid external timeout censor, while confirmatory `analyze_p4b_ledger` raises `CONFIRMATORY_ANALYSIS_NOT_AUTHORIZED`.
