# RapidFlow-APX Native Foundation Validation Report (Phase P2)

**Document ID:** `docs/rapidflow_apx_foundation_validation_report.md`
**Milestone:** D-INT-02 / Phase P2
**Parent Issue:** Issue #30
**Target Integration Branch:** `QuangThang`
**Base Commit:** `8f6ebdacbf289e48e1c8a80a1c2e93720bcf633e`
**Feature Branch:** `feature/rf30-d-int02-native-foundation`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Execution Commit:** `d76909e2ce1e4ca5e4c49a8158b463facf88a673`
**Native Harness Binary SHA-256:** `10fd1bd4f9a5f93d43076c47658181e6c160568fef861d4fc6f84524b357f402`
**Overall Status:** **`PASS (P2_FOUNDATION_VERIFIED)`**

---

## 1. Executive Summary

Phase P2 delivers the authoritative C++ native foundation for RapidFlow-APX in `extensions/rapidflow_apx/`. All four core components—`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, and `ApxTupleSink`—have been fully implemented, compiled into `librapidflow_apx_foundation.a`, and linked with genuine upstream RapidFlow components (`Graph` CSR and `GlobalEdgeView`).

All 8 canonical foundation test cases in `configs/rapidflow_apx_foundation_cases.json` and 17 Python regression tests across 10 groups in `tests/test_rapidflow_apx_foundation.py` pass cleanly with zero failures and zero flakes.

The consolidated remediation addresses all findings P2-F01 through P2-F08:
- **P2-F01**: Strict manifest schema, ceiling enforcement (timeout <= 30s, AS <= 512 MiB), payload strict validation (exit code 0 matches PASS, types strict, reject string "false"), diagnostic subset mode for `--case` (writes to `diagnostic_summary.json`, never overwrites canonical report or claims `P2_FOUNDATION_VERIFIED`), real `--dry-run` validation.
- **P2-F02**: Transaction atomicity and strong exception safety: reentrancy guard (`StepStatus::REENTRANCY_BLOCKED`), codec checkpointing with zero-allocation rollback (`noexcept`), symmetric overlay mutations with zero-allocation rollback, shadow tombstones, output staging before commit, real fault tests (`std::bad_alloc`, between fwd/rev, callback abort, duplicate raw emission).
- **P2-F03**: Strict `json_mini.h` (throw on float/null/bool conversion, reject trailing garbage), ingestion validation in `load_baseline` (dense IDs [0, |V|-1] without duplicates or gaps, reject self-loops, reject duplicate undirected edges in both (u, v) and (v, u), reject out-of-range endpoints, capacity limits).
- **P2-F04**: Real M0 Oracle integration (`scripts/dstar_oracle.py`): replace permutation helper with genuine `enumerate_snapshot` and `DStarDynamicOracle`; output actual ordered tuples in dynamic events; full witness coverage.
- **P2-F05**: D-R guard in fixed-mapping evaluator: compute exact $\lambda(Q)$ via min s-t cut flow passes; enforce $0 \le k < \lambda(Q)$; reject $k \ge \lambda(Q)$ or metadata mismatch; enforce S2 spanning connectivity on matched query edges.
- **P2-F06**: Resource contract: build <= 120s, `RLIMIT_AS <= 1024` MiB, process group, -j2; runtime <= 30s, `RLIMIT_AS <= 512` MiB; preflight `MemAvailable` from `/proc/meminfo` >= 2 GiB, disk >= 1 GiB.
- **P2-F07**: Provenance and partial artifacts: skeleton initialized at start, stages tracked, atomic checkpointing after each case preserving partial results on error, full environment metadata.
- **P2-F08**: Symlink data dependencies resolved (`data/netflow/release_newsp`), trailing whitespace clean (`git diff --check`), update R2 decision without premature closure claims, update handoff docs.

---

## 2. Provenance & Execution Environment

| Metric / Property | Value / Verification Evidence |
| :--- | :--- |
| **Execution Commit** | `d76909e2ce1e4ca5e4c49a8158b463facf88a673` |
| **Repository Cleanliness** | Verified CLEAN via `git status --porcelain` |
| **Upstream RapidFlow Path** | `baselines/rapidflow/source/` (symlink in worktree) |
| **Upstream Pinned Commit** | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` |
| **Upstream Status** | Clean, pristine, zero uncommitted modifications |
| **Compiler & Tools** | GCC 13.3.0, CMake 3.28.3, Python 3.12.3 |
| **Compilation Options** | C++17, `-O3 -Wall -Wextra`, `-fPIC` |
| **Native Library Output** | `extensions/rapidflow_apx/build/librapidflow_apx_foundation.a` |
| **Native Test Harness** | `tests/native/rapidflow_apx_foundation/build/rapidflow_apx_foundation_harness` |
| **Harness SHA-256** | `10fd1bd4f9a5f93d43076c47658181e6c160568fef861d4fc6f84524b357f402` |
| **System Resources** | 5.11 GiB RAM (>= 2.0 GiB required), 110.0 GiB Free Disk (>= 1.0 GiB required) |

---

## 3. Test Cases & Execution Matrix

All cases were executed under fail-closed process containment with `RLIMIT_AS` memory ceilings and process-group `SIGKILL` timeout guards:

| Case ID | Category | Fixture SHA-256 | Status | Time | Key Observation / Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`codec_boundaries`** | Codec & Limits | `588d7395...` | **PASS** | 0.0039s | Verified negative, 0, sparse, $>2^{53}$ IDs; int `0` $\ne$ str `"0"`; capacity check throws; zero-allocation rollback verified. |
| **`lifecycle_stream`** | Authoritative Graph | `46f3a017...` | **PASS** | 0.0039s | CSR loading with dense ID validation, overlay insert/delete, tombstones, and overlay label precedence verified. |
| **`transaction_lifecycle`**| Transaction Manager | `8cf7fa0f...` | **PASS** | 0.0039s | Prevalidation rejection, reentrancy rejection, atomic rollback on std::bad_alloc, rollback between fwd and rev edges, committed state preserved. |
| **`tuple_sink_invariants`**| Output Sink | `875fd083...` | **PASS** | 0.0041s | Canonical sorting by ascending query IDs; duplicate raw emission detected and aborted; injectivity enforced. |
| **`k4_exact_enumeration`** | Micro Exact Matcher | `b2e95b3a...` | **PASS** | 0.0036s | Exactly 24 unique ordered tuples emitted on $K_4$; raw=24, unique=24; bit-for-bit M0 match without scalar multiplication. |
| **`event_transition_stream`**| Dynamic Events | `1198c242...` | **PASS** | 0.0047s | 0 baseline matches; INSERT missing edge $\to$ 24 ENTERED tuples; DELETE $\to$ 24 EXITED tuples; full tuples verified against DStarDynamicOracle. |
| **`diamond_k1_fixed`** | APX Interface | `6e5b741d...` | **PASS** | 0.0035s | Fixed-mapping evaluator correctly computes 4 matched / 1 missing edge; computes exact $\lambda(Q)=2$; enforces $k < \lambda$; verifies S2 spanning connectivity. |
| **`irrelevant_update_stream`**| Graph Fidelity | `0d6ed691...` | **PASS** | 0.0031s | Update with label not in query updates graph state and degree accurately with zero emitted search events. |

---

## 4. Independent Python Oracle Verification

- **K4 Tuple Bit-for-Bit Equivalence**:
  The 24 tuples produced by the native C++ `MicroExactMatcher` were cross-checked against genuine `scripts.dstar_oracle.enumerate_snapshot(Q, G, 0)`. The two sets of tuples are identical in count, ordering, and coordinates:
  ```json
  [
    [10, 20, 30, 40], [10, 20, 40, 30], [10, 30, 20, 40], [10, 30, 40, 20], [10, 40, 20, 30], [10, 40, 30, 20],
    [20, 10, 30, 40], [20, 10, 40, 30], [20, 30, 10, 40], [20, 30, 40, 10], [20, 40, 10, 30], [20, 40, 30, 10],
    [30, 10, 20, 40], [30, 10, 40, 20], [30, 20, 10, 40], [30, 20, 40, 10], [30, 40, 10, 20], [30, 40, 20, 10],
    [40, 10, 20, 30], [40, 10, 30, 20], [40, 20, 10, 30], [40, 20, 30, 10], [40, 30, 10, 20], [40, 30, 20, 10]
  ]
  ```
- **Dynamic Events Bit-for-Bit Equivalence**:
  Dynamic delta events ENTERED and EXITED on $K_4$ stream were cross-checked against genuine `scripts.dstar_oracle.DStarDynamicOracle`. All 24 materialized tuples for each event transition matched bit-for-bit.
- **Negative & Failure Rejection Checks**:
  - Malformed or tampered fixture SHA-256 is rejected fail-closed before execution.
  - Unknown case IDs exit with code 2 and structured error report.
  - Insufficient memory/disk triggers immediate fail-closed error.
  - Reentrancy triggers `StepStatus::REENTRANCY_BLOCKED`.
  - Non-injective or duplicate emissions in TupleSink trigger immediate error and rollback.
  - $k \ge \lambda(Q)$ or disconnected matched query edges trigger strict rejection under D-R guard.

---

## 5. Compliance with Definition of Done (Issue #30)

- [x] Four native components (`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, `ApxTupleSink`) have clear API designs and build into `librapidflow_apx_foundation.a` using genuine upstream RapidFlow.
- [x] Codec is lossless across 64-bit signed IDs, maintains typed labels isolation, and checks capacity preflight without undefined behavior; rollback is zero-allocation `noexcept`.
- [x] CSR + overlay + tombstones + dynamic labels + neighbor lists and degrees are verified across the entire test matrix with dense ID validation and duplicate edge rejection.
- [x] State and pending outputs are strictly transactional; fault injection at all stages verifies pre-step rollback and prior commit preservation with reentrancy guard.
- [x] Micro exact path emits exactly 24 unique ordered tuples on $K_4$; dynamic event transitions match genuine M0 Oracle; Diamond $k=1$ passes with exact $\lambda(Q)$ computation and S2 spanning connectivity.
- [x] Runner negative tests, containment, partial artifacts, and Git provenance gates verify cleanly through genuine entrypoints.
- [x] Canonical manifest is locked, independent state and tuple comparisons pass, zero unaccounted failures.
- [x] Decision record `docs/research_decisions/D-R2-native-foundation.md` documents architectural decisions; handoff and README updated; Phase P3 not yet implemented.
- [x] Focused tests and full unittest discovery (481 tests) pass cleanly.
- [x] Upstream RapidFlow `baselines/rapidflow/source/` remains pristine at `2ea4f4c...`; no binary or raw benchmark data tracked.
