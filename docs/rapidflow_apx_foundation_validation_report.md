# RapidFlow-APX Native Foundation Validation Report (Phase P2)

**Document ID:** `docs/rapidflow_apx_foundation_validation_report.md`
**Milestone:** D-INT-02 / Phase P2
**Parent Issue:** Issue #30
**Target Integration Branch:** `QuangThang`
**Base Commit:** `8f6ebdacbf289e48e1c8a80a1c2e93720bcf633e`
**Feature Branch:** `feature/rf30-d-int02-native-foundation`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Native Harness Binary SHA-256:** `86c38d0eb8705eaf97ae8cc0feb6c6157017833a8a9294ff3dab2afb37ae7086`
**Overall Status:** **`PASS (P2_FOUNDATION_VERIFIED)`**

---

## 1. Executive Summary

Phase P2 delivers the authoritative C++ native foundation for RapidFlow-APX in `extensions/rapidflow_apx/`. All four core components—`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, and `ApxTupleSink`—have been fully implemented, compiled into `librapidflow_apx_foundation.a`, and linked with genuine upstream RapidFlow components (`Graph` CSR and `GlobalEdgeView`).

All 8 canonical foundation test cases in `configs/rapidflow_apx_foundation_cases.json` and 12 Python regression tests in `tests/test_rapidflow_apx_foundation.py` pass cleanly with zero failures and zero flakes.

---

## 2. Provenance & Execution Environment

| Metric / Property | Value / Verification Evidence |
| :--- | :--- |
| **Repository Commit** | `8f6ebdacbf289e48e1c8a80a1c2e93720bcf633e` (merged PR #29) |
| **Upstream RapidFlow Path** | `baselines/rapidflow/source/` (symlink in worktree) |
| **Upstream Pinned Commit** | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` |
| **Upstream Status** | Clean, pristine, zero uncommitted modifications |
| **Compiler & Tools** | GCC 13.3.0, CMake 3.28.3, Python 3.12.3 |
| **Compilation Options** | C++17, `-O3 -Wall -Wextra`, `-fPIC` |
| **Native Library Output** | `extensions/rapidflow_apx/build/librapidflow_apx_foundation.a` |
| **Native Test Harness** | `tests/native/rapidflow_apx_foundation/build/rapidflow_apx_foundation_harness` |
| **Harness SHA-256** | `86c38d0eb8705eaf97ae8cc0feb6c6157017833a8a9294ff3dab2afb37ae7086` |
| **System Resources** | 11.53 GiB RAM (>= 2.0 GiB required), 110.05 GiB Free Disk (>= 1.0 GiB required) |

---

## 3. Test Cases & Execution Matrix

All cases were executed under fail-closed process containment with `RLIMIT_AS` memory ceilings and process-group `SIGKILL` timeout guards:

| Case ID | Category | Fixture SHA-256 | Status | Time | Key Observation / Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`codec_boundaries`** | Codec & Limits | `588d7395...` | **PASS** | 0.003s | Verified negative, 0, sparse, $>2^{53}$ IDs; int `0` $\ne$ str `"0"`; capacity check throws. |
| **`lifecycle_stream`** | Authoritative Graph | `46f3a017...` | **PASS** | 0.004s | CSR loading, overlay insert/delete, tombstones, and overlay label precedence verified. |
| **`transaction_lifecycle`**| Transaction Manager | `8cf7fa0f...` | **PASS** | 0.003s | Prevalidation rejection, INSERT on $G_{\text{after}}$, DELETE on $G_{\text{before}}$, atomic rollback on fault injection, committed state preserved. |
| **`tuple_sink_invariants`**| Output Sink | `875fd083...` | **PASS** | 0.003s | Canonical sorting by ascending query IDs; duplicate raw emission detected and aborted; injectivity enforced. |
| **`k4_exact_enumeration`** | Micro Exact Matcher | `b2e95b3a...` | **PASS** | 0.003s | Exactly 24 unique ordered tuples emitted on $K_4$; raw=24, unique=24; bit-for-bit M0 match without scalar multiplication. |
| **`event_transition_stream`**| Dynamic Events | `1198c242...` | **PASS** | 0.004s | 0 baseline matches; INSERT missing edge $\to$ 24 ENTERED tuples; DELETE $\to$ 24 EXITED tuples. |
| **`diamond_k1_fixed`** | APX Interface | `6e5b741d...` | **PASS** | 0.003s | Fixed-mapping evaluator correctly computes 4 matched / 1 missing edge; accepts $\lambda=2$ without exact NLF interference. |
| **`irrelevant_update_stream`**| Graph Fidelity | `0d6ed691...` | **PASS** | 0.003s | Update with label not in query updates graph state and degree accurately with zero emitted search events. |

---

## 4. Independent Python Oracle Verification

- **K4 Tuple Bit-for-Bit Equivalence**:
  The 24 tuples produced by the native C++ `MicroExactMatcher` were cross-checked against the pure Python `python_m0_k4_oracle`. The two sets of tuples are identical in count, ordering, and coordinates:
  ```json
  [
    [10, 20, 30, 40], [10, 20, 40, 30], [10, 30, 20, 40], [10, 30, 40, 20], [10, 40, 20, 30], [10, 40, 30, 20],
    [20, 10, 30, 40], [20, 10, 40, 30], [20, 30, 10, 40], [20, 30, 40, 10], [20, 40, 10, 30], [20, 40, 30, 10],
    [30, 10, 20, 40], [30, 10, 40, 20], [30, 20, 10, 40], [30, 20, 40, 10], [30, 40, 10, 20], [30, 40, 20, 10],
    [40, 10, 20, 30], [40, 10, 30, 20], [40, 20, 10, 30], [40, 20, 30, 10], [40, 30, 10, 20], [40, 30, 20, 10]
  ]
  ```
- **Negative & Failure Rejection Checks**:
  - Malformed or tampered fixture SHA-256 is rejected fail-closed before execution.
  - Unknown case IDs exit with code 2 and structured error report.
  - Insufficient memory/disk triggers immediate fail-closed error.

---

## 5. Compliance with Definition of Done (Issue #30)

- [x] Four native components (`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, `ApxTupleSink`) have clear API designs and build into `librapidflow_apx_foundation.a` using genuine upstream RapidFlow.
- [x] Codec is lossless across 64-bit signed IDs, maintains typed labels isolation, and checks capacity preflight without undefined behavior.
- [x] CSR + overlay + tombstones + dynamic labels + neighbor lists and degrees are verified across the entire test matrix.
- [x] State and pending outputs are strictly transactional; fault injection at all stages verifies pre-step rollback and prior commit preservation.
- [x] Micro exact path emits exactly 24 unique ordered tuples on $K_4$; dynamic event transitions match M0; Diamond $k=1$ passes without exact NLF interference.
- [x] Runner negative tests, containment, partial artifacts, and Git provenance gates verify cleanly through genuine entrypoints.
- [x] Canonical manifest is locked, independent state and tuple comparisons pass, zero unaccounted failures.
- [x] Decision record `docs/research_decisions/D-R2-native-foundation.md` documents architectural decisions; handoff and README updated; Phase P3 not yet implemented.
- [x] Focused tests and full unittest discovery pass cleanly.
- [x] Upstream RapidFlow `baselines/rapidflow/source/` remains pristine at `2ea4f4c...`; no binary or raw benchmark data tracked.
