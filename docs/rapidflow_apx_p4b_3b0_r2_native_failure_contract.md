# RapidFlow-APX P4b-3B.0 R2 Native Failure-Path & Evidence Contract

## 1. Timing Unwind Contract (`NEW-FIND-01`)

- In `extensions/rapidflow_apx/src/streaming_engine.cpp`, `ApxStreamingEngine::execute_step` wraps transactional matching callback execution with a stack-allocated `SearchTimingGuard` (`noexcept` destructor).
- When `--execution-purpose diagnostic` is active (`PROFILE_ON`):
  - `search_ns` starts immediately before entering the traversal/matching callback and closes in `~SearchTimingGuard()`.
  - `search_core_ns` closes inside `~SearchTimingGuard()` if `search_core_active` was not already closed on normal exit.
  - Because `SearchTimingGuard` runs during stack unwind when `WorkLimitExceededException`, `InvariantViolationException`, or `std::bad_alloc` propagates, `search_ns >= search_core_ns` and `search_ns >= neighbor_retrieval_ns` hold on both completed steps and aborted/failed steps (`CALLBACK_ABORTED`, `RESOURCE_EXHAUSTED`).
- When `--execution-purpose performance` or `correctness` is active (`PROFILE_OFF`), both timer hooks are disabled and zero step/root `profiling` blocks are emitted.

## 2. Genuine OOM vs Fault Injection Contract (`NEW-FIND-02`)

| Origin | Native `StepStatus` | Native `error_category` | Native `error_message` | Process Exit Code | Validator / Session Classification |
|---|---|---|---|---|---|
| Pre-workload parse/init `std::bad_alloc` (`attempted_steps == 0`) | `PRE_STEP` (`steps == []`) | `RESOURCE_LIMIT_ERROR` | `"std::bad_alloc"` | `5` (`RESOURCE_LIMIT_ERROR`) | `CENSORED / MEMORY_LIMIT_FAILURE` (or `RESOURCE_UNAVAILABLE` if supervisor reports containment diagnostic) |
| Post-`attempted > 0` delivery OOM | N/A | `RESULT_DELIVERY_ERROR` | truncated diagnostic | `9` (`RESULT_DELIVERY_ERROR`) | `FAILED / RESULT_DELIVERY_ERROR` |
| Pre-execution transaction prep `std::bad_alloc` | `ERROR` (no `step_counters`) | `RESOURCE_LIMIT_ERROR` | `"std::bad_alloc"` | `5` (`RESOURCE_LIMIT_ERROR`) | `CENSORED / MEMORY_LIMIT_FAILURE` |
| In-step execution/callback `std::bad_alloc` | `RESOURCE_EXHAUSTED` (carries real `step_counters`; plus `profiling` in `diagnostic` mode) | `RESOURCE_LIMIT_ERROR` | `"std::bad_alloc"` | `5` (`RESOURCE_LIMIT_ERROR`) | `CENSORED / MEMORY_LIMIT_FAILURE` |
| Fault-hook `FaultPoint::ALLOCATION_FAILURE_MUTATION` | `FAULT_INJECTED` | `FAULT_INJECTED` | fault hook message | `6` (`FAULT_INJECTED`) | `FAILED / EXECUTION_FAILURE` (never censored as memory limit) |
| Unattributed stderr `"std::bad_alloc"` with exit `!= 5` | N/A | N/A | N/A | `!= 5` | `INVALID / MISSING_RESULT` or `FAILED / EXECUTION_FAILURE` (never censored as resource failure) |

- Rollback on `std::bad_alloc` executes via `abort_oom_noalloc` using fixed error string `"std::bad_alloc"` (14 chars, guaranteed within libstdc++ 15-byte SSO buffer) and `noexcept` transaction abort.

## 3. Invariant Violation & Session Poisoning Contract (`NEW-FIND-03`)

- `ApxTransactionManager` catches `const InvariantViolationException&` separately from `FaultInjectionException` and generic `std::exception` across both mutation (`apply_insert` / `apply_delete`) and matching callback paths.
- Any caught `InvariantViolationException` sets `session_poisoned_ = true`, triggers rollback, and returns `StepStatus::INVARIANT_VIOLATION`.
- `harness_runner.cpp` maps `StepStatus::INVARIANT_VIOLATION` to `error_category = "INVARIANT_VIOLATION"`, `ExitCode::STEP_FAILURE` (`7`), and terminates the step loop immediately even if `--continue-on-error` is requested.

## 4. AuthoritativeGraph Tentative Overlay Deletion Visibility (`NEW-FIND-04`)

- Pending overlay deletions inside an open transaction (`tx_pending_deletes_` + `pending_overlay_deletes_`) are consulted before any committed or static CSR edge is surfaced:
  - `has_edge` and `get_edge_label` return absent if `is_key_tentatively_deleted(...)` holds and no newer tentative insert overrides the key.
  - `get_neighbors` and `get_neighbors_into` filter out both committed overlay tombstones and tentatively deleted overlay keys in the fallback path.
  - `get_neighbors_merged_into` bypasses Fast Path 2 (`static_neighbor_slice` zero-copy shortcut) whenever `has_tentative_overlay_delete(u)` is true and skips tentatively deleted keys via `advance_overlay()` inside the 2-way sorted linear merge.
- Aborting a transaction resets `pending_overlay_deletes_` and rolls back mutations without dynamic allocation, restoring the committed overlay edge.

## 5. Malformed Evidence, Interrupted Parser & Profiling Leakage (`NEW-FIND-05`, `NEW-FIND-06`)

- `scripts/p4b_experiment_session.py` and `scripts/p4b_native_evidence.py` require exact container types (`dict`, `list`, `int` excluding `bool`, `str`) across `process.json`, `supervisor`, `resource_caps`, `execution`, `macro_timing`, `steps`, `step_counters`, and `profiling`. Malformed `null` / wrong-type fields fail structured validation as `INVALID / MEASUREMENT_ERROR`.
- `p4b_native_evidence.interrupted_output` parses interrupted streaming prefixes token-by-token using strict state expectations (`expect_step`) and rejects root/array trailing commas (`{"case_id": "X", "steps": [{"step_index": 0}, ]}` and `, ]}`) with `LedgerIntegrityError`.
- `_check_interrupted_output` and `_validate_native` reject any complete or partial `performance` / `correctness` native output carrying root `profiling` or step-level `profiling`.
