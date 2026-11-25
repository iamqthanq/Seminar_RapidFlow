# RapidFlow-APX Phase P4b-3A Implementation & Preregistration Report

**Repository:** `iamqthanq/Seminar_RapidFlow` · **Branch:** `feature/rf38-p4b-optimization`
**Issue:** `#38` (OPEN) · **Draft PR:** `#39` (OPEN / DRAFT targeting `QuangThang`)
**Proposed Handoff Verdict:** `P4B_3A_READY_FOR_INDEPENDENT_PREREGISTRATION_REVIEW`

---

## 1. Executive Summary

- **Starting HEAD (Accepted P4b-2B R1.1.3 Baseline):** `a4e9d41defa78a3cc1b34f27bff43aa98cf858db`
- **Preserved Starting Binary (`accepted-p4b2b-native`):** `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78`
- **Tested Implementation Commit:** `97d8636b9567d53d3e083c48d61b46feb40ae540`
- **Final Documentation Commit:** Recorded immediately above `97d8636b9567d53d3e083c48d61b46feb40ae540` (verified in `final-git-state.log`)
- **Upstream RapidFlow Pinned Commit (`baselines/rapidflow/source/`):** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (PRISTINE)
- **Scope Statement:** Phase P4b-3A implemented and verified the deterministic workload freeze (`50` fixtures), separated `PROFILE_OFF` / `PROFILE_ON` / `correctness` native measurement infrastructure (`harness_runner.h`, `harness_runner.cpp`, `measurement_seam_tests.cpp`), fail-closed experiment orchestrator & balanced Williams schedule (`1,450` planned slots), and mutually exclusive 11-priority statistical analysis & decision classifier.
- **No Confirmatory Benchmark Execution Statement:** **Zero** primary timed (`1,000`), warmup (`200`), or diagnostic (`250`) performance benchmark runs were executed during P4b-3A (`execution_authorized = false`, `approval_state = PREREGISTRATION_CANDIDATE`). Only dry-run inspections (`launched_subprocess_count = 0`), bounded functional/seam smoke tests, synthetic statistical boundary tests, and untimed correctness verification runs (`--execution-purpose correctness`) were executed.
- **Proposed Verdict:** `P4B_3A_READY_FOR_INDEPENDENT_PREREGISTRATION_REVIEW` (`P4b-3B` NOT yet authorized).

---

## 2. Workload Freeze

- **Continuity Suite (`P4B_CONT_01..P4B_CONT_30`, 30 workloads):** Semantically identical preservation of all 30 certified `P4A_WL_01..P4A_WL_30` workloads from `configs/rapidflow_apx_p4a_manifest.json` (`f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18`), verified via `compute_dict_sha256` against historical `p4a_raw_ledger.jsonl` (`34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5`). Frozen under unified canonical UTF-8 sorted-key JSON byte layout.
- **Discrimination Suite (`DISC_01_K0..DISC_10_K1`, 20 workloads):** 10 matched topology groups (`DISC-01` through `DISC-10`) × {$k=0, k=1$} covering `triangle`, `c4`, `diamond`, `house`, `k4`, and `triangular_prism`. Independent min-cut verification confirmed $\lambda(Q) \in \{2, 3\} > k$ on all 6 query topologies (`query-connectivity-verification.json`). Configuration-model `DISC-02` verified strict `degree = 4` on every vertex. Matched $k$-pair field invariance verified across all 10 pairs.
- **Deterministic Generator Reproducibility (`p4b-wlgen-v1.0.0`):** Five independent SHA-256-seeded `random.Random` (MT19937) streams per group; regeneration under default and `PYTHONHASHSEED=424242` yielded **0 byte discrepancies across all 50 fixtures** (`generator-reproducibility.json`).
- **Exact Manifest Digest:** [`configs/rapidflow_apx_p4b_manifest.json`](../configs/rapidflow_apx_p4b_manifest.json) SHA-256 `09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869` (`107,573` bytes).
- **Correctness Tiers:** `32` Tier C1 (`P4B_CONT_01..30` + `DISC_01_K0/K1`) verified against exhaustive M0 `DStarDynamicOracle`; `18` Tier C3 (`DISC_02_K0..DISC_10_K1`) verified via differential parity vs `BARE` M2 with `DISC_02_K0` raising expected `ORACLE_UNAVAILABLE_WITHIN_REGISTERED_BUDGET: Candidate injection bound 863040 exceeds limit 100000`.

---

## 3. Measurement Infrastructure

- **Source Refactoring & Changes:** Refactored monolithic native CLI cleanly into [`harness_runner.h`](../tests/native/rapidflow_apx_engines/src/harness_runner.h), [`harness_runner.cpp`](../tests/native/rapidflow_apx_engines/src/harness_runner.cpp), [`main.cpp`](../tests/native/rapidflow_apx_engines/src/main.cpp), [`measurement_seam_tests.cpp`](../tests/native/rapidflow_apx_engines/src/measurement_seam_tests.cpp), [`profiler.h`](../extensions/rapidflow_apx/include/rapidflow_apx/profiler.h), and [`CMakeLists.txt`](../tests/native/rapidflow_apx_engines/CMakeLists.txt). Zero algorithm or header ABI changes inside `extensions/rapidflow_apx/src/`.
- **Three Execution Modes (`--execution-purpose`):**
  1. `correctness` (default when omitted): Preserves historical P4b-2B JSON contract, per-step `graph_state`, `codec_state`, full reconstruction, and fault hooks unchanged.
  2. `performance` (`PROFILE_OFF`): Rejects `--profile`/`--profile-output` (CLI exit `1`), disables micro-stage timers (`profiling_enabled = false`), sets `deep_diagnostics_enabled = false`, emits `measurement_schema_version = 1`.
  3. `diagnostic` (`PROFILE_ON`): Enables fine micro-stage timers (`profiling_enabled = true`) for mechanistic attribution while excluding deep graph DOM reconstruction (`deep_diagnostics_enabled = false`); rejected by `primary` performance eligibility checks.
- **Exact Macro Timing Boundaries (`std::chrono::steady_clock`):**
  - `input_parsing_ns` (`PARSE_START..PARSE_END`)
  - `initialization_wall_ns` (`INIT_START..INIT_END`: includes `graph_init_ns`, `query_prep_ns`, and `engine_and_nlf_init_ns` covering `ApxTupleSink`, `ApxStreamingEngine`, `ApxNlfIndex::initialize`)
  - `streaming_loop_wall_ns` (`PRIMARY_TIMER_START..PRIMARY_TIMER_STOP`): Pure update/search steps + lightweight `RetainedStepOutcome` vectors (full ordered `entered_tuples` / `exited_tuples`, zero JSON DOMs, zero disk I/O, zero `CONSTRUCT_GRAPH_DIAGNOSTICS`)
  - `reporting_wall_ns` (`REPORTING_START..REPORTING_END`): Streams steps directly into compact ASCII JSON strings freeing tuple vectors step-by-step (eliminating the >512 MiB DOM triple-copy bloat uncovered on `DISC_06_K1`).
- **Per-Process Resource Accounting (`VmHWM` + `wait4`):** Native child reads `/proc/self/status` `VmHWM:` post-`REPORTING_END` (`linux_proc_self_status_vmhwm`, KiB × 1024 bytes) cleanly isolated from Python parent `fork()` inheritance into kernel `sig->maxrss`, while Python runner retains `os.wait4(proc.pid, 0)` CPU (`user_cpu_ns`, `system_cpu_ns`), `outer_wall_ns`, and abnormal-exit fallback RSS. Strict separate `validate_measurement_output_schema()` validates measurement schema without weakening legacy correctness checks.

---

## 4. Experimental Design

- **Primary Variants (4):** `BARE` (`m2/none/legacy`), `A_STATIC` (`m2/static/legacy`), `A_RESIDUAL` (`m2/residual/legacy`), `B_MERGE` (`m2/none/merge`).
- **Auxiliary Diagnostic Variant (1):** `B_BUFFER` (`m2/none/buffer`), strictly isolated in `DIAG_VARIANTS` (`diagnostic` mode).
- **Planned Counts under Proposed $R=5$:**
  - `1,000` Primary `PROFILE_OFF` slots (`50 × 4 × 5`), scheduled with balanced Williams `4×4` crossover (`S1..S4` once per workload in reps `1..4` + near-equal `13/13/12/12` in rep `5`, global position counts `[62, 63]` across all 4 variants, seed `380301`).
  - `200` Disposable Warmup `PROFILE_OFF` slots (`50 × 4 × 1`) strictly preceding rep 1, never aggregated in primary speedups.
  - `250` Separate Diagnostic `PROFILE_ON` slots (`50 × 5`, cyclic seed `380303`).
- **Resource Caps (`P4B_STD_RES_V1`):** `60.0s` wall timeout (`--wall-timeout-seconds 60`), `512 MiB` `RLIMIT_AS`, `500,000` logical `max_work` per step, `256 MiB` default `max_nlf_memory_mib`.

---

## 5. Statistical Design (`scripts/summarize_rapidflow_apx_p4b.py`)

- **Experimental Unit:** Workload (`5/5` complete repetition pair admission required). Zero repetition pseudo-replication.
- **Estimands:** Within-workload median $\widetilde{T}_{V,i}$; speedup $S_{V,i} = \widetilde{T}_{\mathrm{BARE},i} / \widetilde{T}_{V,i}$; across-workload primary median $S_{\mathrm{median},V}$ and secondary geometric mean $S_{\mathrm{geo},V}$.
- **Stratified Block Bootstrap:** Deterministic Python 3.12 stdlib `random.Random(380302)`, `10,000` resamples; Continuity resampled at workload level, Discrimination resampled by complete `(group_id, k={0,1})` pair blocks; unadjusted `[2.5%, 97.5%]` primary CIs via linear quantile interpolation alongside supplementary Holm–Bonferroni sensitivity CIs (`alpha = 0.05/3`).
- **Missing-Pair, Censoring, Coverage, Noise & Memory Gates:** Coverage requires $\ge 45/50$ overall, $\ge 27/30$ Continuity, $\ge 18/20$ Discrimination, $\ge 9/10$ complete DISC blocks. Variant-only resource failure ($\ge 2$ reps while `BARE` completes `5/5`) triggers Priority 3 `NO_GO`. Jitter $J = \max/\min > 1.50$ on $\ge 20\%$ pairs forces Priority 4 `INCONCLUSIVE`. Worst workload-level median peak RSS overhead $> +20\%$ forces Priority 5 `NO_GO`. Mutually exclusive 11-priority `classify_variant_decision()` returns exact single primary disposition + conditional `A+B` flag (`S >= 1.05, L > 1.00`, Priorities 1..5 passed).

---

## 6. Synthetic Verification

> **Disclaimer:** Synthetic verification uses strictly fabricated numerical inputs (`synthetic_test_fixture = true`, `synthetic-test-fixtures/synthetic_nominal_1000_slots.jsonl`) purely to test statistical pipeline mechanics. They are NEVER real benchmark results.

- `tests.test_rapidflow_apx_p4b_statistics`: `6 / 6` tests passed in `7.101s` (`statistical-tests.log`):
  - `test_01`: Exact mutually exclusive decisions and priority codes verified across all `19 / 19` `DEC-01..DEC-19` boundary scenarios (`decision-boundary-results.json`), plus exact boundary equalities (`S=0.95, 1.05, 1.15`, `L=1.00`, `U=1.00`, `RSS=0.20` vs `0.20001`).
  - `test_02`: Fail-closed rejection of `NaN`, `Inf`, zero/negative speedup, `L > U`, point outside `[L, U]`.
  - `test_03` & `test_06`: Deterministic 10,000-resample block bootstrap, intact DISC pair block drop, anti-pseudo-replication weight `= 1`, and `J > 1.50` noise filter (`10 / 50` pairs triggering Priority 4).
  - `test_04`: Incomplete `4/5` pair censoring, `>= 2` variant-only resource failures (`NO_GO` Priority 3), missing RSS (`INCONCLUSIVE` Priority 2).
  - `test_05`: Fail-closed `LedgerIntegrityError` rejection of missing/extra/duplicate slots, wrong hash, unknown workload, and configuration mismatch.

---

## 7. Correctness and Regression Results

- **Targeted P4b-3A Infrastructure & Generator Suites:**
  - `tests.test_rapidflow_apx_p4b_preregistration`: `7 / 7 ok` (`0.714s`)
  - `tests.test_rapidflow_apx_p4b_measurement`: `5 / 5 ok` (`measurement-schema-tests.log`), including 64 MiB parent RSS bloat isolation verification and clean-parent `os.wait4` independent child probe.
  - `tests.test_rapidflow_apx_p4b_statistics`: `6 / 6 ok` (`7.101s`)
  - Native `--test-p4b-measurement` seam suite: `PASS` (`diagnostic-isolation-tests.log`).
- **Full Clean-Revision Repository Regression (`97d8636b9567d53d3e083c48d61b46feb40ae540`):**
  - Command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v`
  - Result: **`Ran 845 tests in 280.030s -> OK`** (`full-regression-p4b3a.exit = 0`, `0` failures, `0` errors, `0` skipped).
- **Canonical Engine Checks (`97d8636b`, binary SHA-256 `f02e6ff...`):**
  - `--stage hand` (`25/25` hand cases across `m2/none/legacy`, `m2/static/legacy`, `m2/residual/legacy`, `m2/none/merge`): all `4/4` exited `0` (`DIAGNOSTIC_SUBSET_PASS`).
- **Sanitizer Suite (`ASan + UBSan`, `sanitizer.log`):**
  - Executed with `ASAN_OPTIONS=detect_leaks=0` (due to documented upstream `Graph::buildEdgeIndex` `spp` leak) across `--test-p4b-measurement`, `--test-empty-frontier`, `--test-p4b-residual numeric`, `--test-p4b-neighbor graph`, and `--test-p4b all`: **all 5 native suites passed (`exit 0`)**.
- **Untimed Correctness Parity Across Frozen 50 P4b Workloads (`correctness-verification.json`):**
  - Executed strictly under `--execution-purpose correctness` with all timing fields stripped prior to comparison and never analyzed.
  - Tier C1 (`DISC_01_K0`, `DISC_01_K1`): `100%` exact `entered_tuples` & `exited_tuples` match vs Python M0 `DStarDynamicOracle` across all 5 variants (`32` steps each).
  - M0 Budget-Cap Probe (`DISC_02_K0`): Confirmed `ORACLE_UNAVAILABLE_WITHIN_REGISTERED_BUDGET: Candidate injection bound 863040 exceeds limit 100000`.
  - Differential Parity across `50` workloads (`BARE` vs `A_STATIC`, `A_RESIDUAL`, `B_MERGE`, `B_BUFFER`, plus `diff_canonical_structures(is_3way=True)`):
    - **Continuity (`30 / 30`):** All `30 / 30` passed cleanly with 0 discrepancies.
    - **Discrimination (`18 / 20` completed + `2 / 20` censored):** `DISC_01`..`DISC_06` (including `DISC_06_K1` after streaming JSON memory fix) and `DISC_08`..`DISC_10` passed (`18 / 20`, `9 / 10` complete DISC blocks). `DISC_07_K0` and `DISC_07_K1` exceeded canonical `max_work = 500,000` (`WORK_BUDGET_EXCEEDED`, exit `4`) identically across all 5 variants and are retained uncensored for Coordinator disposition.

---

## 8. Audit Findings Remediation Summary

| Finding ID | Summary | Remediation Implemented | Evidence | Proposed Status |
|---|---|---|---|---|
| `AUD-P4B-001` | Overlapping decision thresholds, missing-pair / censoring / noise / RSS / bootstrap ambiguities | Mutually exclusive 11-priority decision matrix, workload experimental unit, 5/5 complete pairing, 10,000-resample stratified block bootstrap (seed `380302`), `<=+20%` worst RSS gate, `90%` coverage & `J>1.50` noise rules, conditional `A+B` gate | `scripts/summarize_rapidflow_apx_p4b.py`, `configs/rapidflow_apx_p4b_protocol.json`, `tests/test_rapidflow_apx_p4b_statistics.py`, `decision-boundary-results.json` | `REMEDIATION_IMPLEMENTED_PENDING_REVIEW` |
| `AUD-P4B-002` | Unfrozen DISC bytes, unspecified RNG/label/update generation, deep diagnostics inside `stream_t0..t1`, `RLIMIT_AS` / RSS measurement contamination | Versioned `p4b-wlgen-v1.0.0` (5 independent SHA-256 MT19937 streams), 50 canonical frozen fixtures, balanced Williams schedule, `--execution-purpose` mode separation, pure `steady_clock` timed loop, streaming step JSON delivery, `/proc/self/status` `VmHWM` per-child RSS isolation | `scripts/generate_rapidflow_apx_p4b_manifest.py`, `configs/rapidflow_apx_p4b_{manifest,schedule}.json`, `tests/native/.../harness_runner.cpp`, `measurement_seam_tests.cpp`, `measurement-boundary-audit.md` | `REMEDIATION_IMPLEMENTED_PENDING_REVIEW` |

---

## 9. Acceptance Matrix (All 80 `PRE-A01..PRE-G08` Gates Reconciled)

Summary: `8` Group A + `14` Group B + `14` Group C + `10` Group D + `16` Group E + `10` Group F + `8` Group G = **`80` total gates** (`79` `PASS`, `1` `PENDING_COORDINATOR_APPROVAL`: `PRE-G08`).

| Gate ID | Group | Requirement | Source / Method | Observed Result | Limitation | Status |
|---|---|---|---|---|---|---|
| `PRE-A01` | A | Correct starting HEAD | `git log / preflight.log` — Verify starting commit a4e9d41defa78a3cc1b34f27bff43aa98cf858db on feature/rf38-p4b-optimization | Verified clean starting HEAD a4e9d41defa78a3cc1b34f27bff43aa98cf858db | None | `PASS` |
| `PRE-A02` | A | P4b-2B acceptance preserved | `Issue #38 / git history` — Verify P4b-2B R1.1.3 acceptance state recorded before P4b-3A | P4b-2B R1.1.3 verified as ACCEPTED_WITH_DOCUMENTED_LIMITATIONS | None | `PASS` |
| `PRE-A03` | A | P4b-3A authorization recorded | `GitHub Issue #38` — PATCH Issue #38 with Section 8 phase authorization and P4b-3A checkbox | Issue #38 updated preserving history; P4b-3A authorized, P4b-3B not authorized | None | `PASS` |
| `PRE-A04` | A | Issue #38 and Draft PR #39 preserved | `gh issue/pr view` — Verify Issue #38 OPEN and PR #39 OPEN/DRAFT targeting QuangThang | Issue #38 OPEN; PR #39 OPEN/DRAFT/CLEAN targeting QuangThang; never merged | None | `PASS` |
| `PRE-A05` | A | Upstream pristine | `baselines/rapidflow/source` — Verify git HEAD and clean status inside pinned upstream checkout | Upstream pinned at 2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1, CLEAN | None | `PASS` |
| `PRE-A06` | A | Accepted native binary preserved | `accepted-p4b2b-native` — Copy and SHA-256 hash accepted P4b-2B binary prior to edits | Preserved at SHA-256 4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78 | None | `PASS` |
| `PRE-A07` | A | Historical evidence unchanged | `D_INT03 7 P4a artifacts` — Verify byte size and SHA-256 of all 7 P4a artifacts + D_INT03 binary before and after | 7/7 P4a artifacts unchanged (ledger 441,311 bytes SHA 34452fb4..., binary SHA 6e4537d0...) | None | `PASS` |
| `PRE-A08` | A | No unauthorized benchmark execution | `scripts/run_rapidflow_apx_p4b.py` — Fail-closed guard + --dry-run test_07 + zero benchmark execution | execution_authorized=false; --execute-primary exits 2; 0 primary/warmup/diagnostic benchmarks run | None | `PASS` |
| `PRE-B01` | B | Exact 30 continuity workloads | `configs/rapidflow_apx_p4b_manifest.json` — test_01 & test_02 in test_rapidflow_apx_p4b_preregistration.py | 30/30 continuity payloads match P4A_WL_01..30 dicts & compute_dict_sha256 | Historical transient run bytes deleted at P4a run time; canonical dict semantics 100% preserved | `PASS` |
| `PRE-B02` | B | Exact 20 DISC workloads | `configs/rapidflow_apx_p4b_manifest.json` — test_01 in test_rapidflow_apx_p4b_preregistration.py | 20 discrimination workloads (10 groups x {k=0, k=1}) validated | None | `PASS` |
| `PRE-B03` | B | Six query topologies verified | `scripts/generate_rapidflow_apx_p4b_manifest.py` — verify_all_query_topologies() + test_03 | Triangle, C4, Diamond, House, K4, Triangular Prism verified against canonical edge specs | None | `PASS` |
| `PRE-B04` | B | Independent edge-connectivity verification | `independent_edge_connectivity()` — Brute-force min-cut enumeration over 2^n subsets in test_03 | Min-cuts verified: triangle=2, c4=2, diamond=2, house=2, k4=3, triangular_prism=3 | None | `PASS` |
| `PRE-B05` | B | Exact D-R missing budgets | `workload-validation.json` — Assert 0 <= k < lambda(Q) across all 50 workloads in test_03 | 50/50 workloads satisfy 0 <= k < lambda(Q) (15 k=0, 27 k=1, 8 k=2) | None | `PASS` |
| `PRE-B06` | B | Deterministic topology generation | `_generate_topology_edges()` — Independent MT19937 stream 'topology'; test_04 4-regularity check | All 10 DISC topologies generated deterministically; DISC-02 strictly 4-regular on all 32 vertices | None | `PASS` |
| `PRE-B07` | B | Deterministic label generation | `_generate_disc_labels_and_query()` — Separate streams 'vertex_labels' & 'edge_labels' | Uniform and mixed typed integer labels reproducible & independent of topology RNG | None | `PASS` |
| `PRE-B08` | B | Deterministic update generation | `_generate_disc_updates()` — Separate streams 'update_ops' & 'update_edges' over sorted sets | All updates sampled exclusively from sorted absent/present edge lists | None | `PASS` |
| `PRE-B09` | B | Exact INS/DEL counts | `validate_and_summarize_workload()` — Exact INS/DEL counts asserted against spec in test_03 | All 50 workloads match exact INS/DEL counts | None | `PASS` |
| `PRE-B10` | B | Matched k-pair design | `test_04_matched_k_pair_invariance...` — Byte/field equality check between k=0 and k=1 in DISC-01..10 | All 10 DISC pairs share identical query, initial_graph, updates, max_work, regime, topology | None | `PASS` |
| `PRE-B11` | B | Independent stream validation | `validate_and_summarize_workload()` — Full generator-side shadow graph replay on all 50 workloads | 50/50 update streams pass duplicate/absent/label validation | None | `PASS` |
| `PRE-B12` | B | Exact fixture byte freeze | `tests/fixtures/p4b/` — Canonical UTF-8 sorted JSON bytes stored & verified on disk | All 50 JSON files frozen cleanly | None | `PASS` |
| `PRE-B13` | B | Generator reproducibility | `--verify-reproducibility + test_05` — Regenerate under default & PYTHONHASHSEED=424242 into temp dir | 50/50 workloads compared, 0 byte discrepancies | None | `PASS` |
| `PRE-B14` | B | Complete fixture and manifest hashes | `configs/rapidflow_apx_p4b_manifest.json` — SHA-256 verification for all 50 fixtures & manifest | Manifest SHA 09c78158... (107,573 bytes) matches every fixture SHA-256 | None | `PASS` |
| `PRE-C01` | C | Explicit measurement purpose | `main.cpp / harness_runner.h` — CLI accepts --execution-purpose correctness|performance|diagnostic | Three modes cleanly parsed & recorded on output root | None | `PASS` |
| `PRE-C02` | C | PROFILE_OFF primary mode | `harness_runner.cpp` — performance enforces profiling_enabled=false & rejects --profile (exit 1) | PROFILE_OFF verified; performance + --profile exits 1 CLI_ARG_ERROR | None | `PASS` |
| `PRE-C03` | C | PROFILE_ON diagnostic mode | `harness_runner.cpp` — diagnostic enables fine ProfilingClock timers, omits deep graph_state DOM | Diagnostic mode emits profiling block and is excluded from primary validator | None | `PASS` |
| `PRE-C04` | C | Independent macro timer | `profiler.h / harness_runner.cpp` — std::chrono::steady_clock records macro_timing independently of ProfilingClock fine hooks | macro_timing recorded in all modes; clock_source='std::chrono::steady_clock' | None | `PASS` |
| `PRE-C05` | C | Correct timer start boundary | `harness_runner.cpp:1058` — measurement_seam_tests.cpp observer verifies INIT_END < PRIMARY_TIMER_START | Verified INIT_START < INIT_END < PRIMARY_TIMER_START across all 4 variants | None | `PASS` |
| `PRE-C06` | C | Correct timer stop boundary | `harness_runner.cpp:1139` — measurement_seam_tests.cpp observer verifies PRIMARY_TIMER_STOP < REPORTING_START | Step JSON serialization happens strictly during REPORTING_START..REPORTING_END | None | `PASS` |
| `PRE-C07` | C | Initialization accounted separately | `harness_runner.cpp:588-715` — Measures graph_init_ns, query_prep_ns, engine_and_nlf_init_ns inside initialization_wall_ns | Full ApxStreamingEngine & ApxNlfIndex init captured separately from streaming_loop_wall_ns | None | `PASS` |
| `PRE-C08` | C | Deep diagnostics excluded from timed loop | `harness_runner.cpp + measurement_seam_tests.cpp` — Observer trace + REPORT_GRAPH_DIAGNOSTICS fault injection test | 0 CONSTRUCT_GRAPH_DIAGNOSTICS inside performance/diagnostic; fault fails exit 9 correctness, passes exit 0 performance | None | `PASS` |
| `PRE-C09` | C | Full tuple semantics preserved | `test_02 + correctness-verification.json` — Verify full entered_tuples/exited_tuples parity across performance & correctness | 100% step-by-step tuple parity preserved; zero count-only substitution | None | `PASS` |
| `PRE-C10` | C | Strict measurement output schema | `validate_measurement_output_schema()` — test_03 negative tests reject profiling=true, zero time, leaked graph_state | Strict measurement validator rejects all schema violations cleanly | None | `PASS` |
| `PRE-C11` | C | Existing correctness schema preserved | `main.cpp / test_01 seam + full regression` — Default CLI invocation without --execution-purpose preserves P4b-2B JSON keys & schema | All canonical correctness tests pass with unaltered legacy root JSON schema | None | `PASS` |
| `PRE-C12` | C | Outer wall measurement | `scripts/run_rapidflow_apx_p4b.py` — time.perf_counter_ns() across subprocess Popen..wait4 in execute_p4b_native_slot | Positive outer_wall_ns captured cleanly on every execution | None | `PASS` |
| `PRE-C13` | C | CPU measurement | `scripts/run_rapidflow_apx_p4b.py` — os.wait4(proc.pid, 0) captures ru_utime & ru_stime in nanoseconds | user_cpu_ns and system_cpu_ns recorded per child process | None | `PASS` |
| `PRE-C14` | C | Per-process peak RSS measurement | `harness_runner.cpp + run_rapidflow_apx_p4b.py` — Child /proc/self/status VmHWM (KiB*1024) + clean wait4 test_05 + 64 MiB parent bloat test | Isolates child RSS <32 MiB even when parent holds >64 MiB; wait4 fallback preserved | Kernel 4 KiB page granularity; abnormal signal crash without JSON uses wait4 fallback | `PASS` |
| `PRE-D01` | D | Four primary configurations locked | `configs/rapidflow_apx_p4b_protocol.json` — Inspect PRIMARY_VARIANTS & protocol JSON | BARE, A_STATIC, A_RESIDUAL, B_MERGE locked symmetrically | None | `PASS` |
| `PRE-D02` | D | Auxiliary configurations separated | `configs/rapidflow_apx_p4b_protocol.json` — validate_schedule() & is_slot_eligible() | B_BUFFER allowed exclusively in diagnostic (250 slots), rejected from primary slots | None | `PASS` |
| `PRE-D03` | D | Repeat count proposal justified | `docs/rapidflow_apx_p4b_preregistration.md §14` — Document R=5 odd-median rationale | R=5 proposal justified for median stability; awaiting Coordinator approval | None | `PASS` |
| `PRE-D04` | D | Warm-up policy fixed | `configs/rapidflow_apx_p4b_schedule.json` — validate_schedule() & docs §13 | 200 warmup slots (50x4) strictly isolated from primary stats | None | `PASS` |
| `PRE-D05` | D | Balanced crossover schedule | `scripts/run_rapidflow_apx_p4b.py` — Williams 4x4 S1..S4 reps 1..4 + balanced rep 5 + test_06 | Position counts across 250 blocks are balanced within [62, 63] for every variant | None | `PASS` |
| `PRE-D06` | D | Exactly 1,000 planned primary slots under R=5 proposal | `configs/rapidflow_apx_p4b_schedule.json` — validate_schedule() + run_dry_run_inspection() | 1,000 primary + 200 warmup + 250 diagnostic = 1,450 planned slots; 0 benchmarks run | None | `PASS` |
| `PRE-D07` | D | Environment manifest | `environment-manifest.json` — Capture OS, kernel, CPU, governor, RAM, compiler, CMake, Python versions | Ubuntu 24.04.5, Linux 7.0.0-43, i5-8265U powersave, 12.38 GB RAM, g++ 13.3.0, Python 3.12.3 | None | `PASS` |
| `PRE-D08` | D | 60-second timeout policy | `harness_runner.cpp / run_rapidflow_apx_p4b.py` — --wall-timeout-seconds 60 + pre-reaper timeout handler | Supported 1..60s in native alarm() and Python wall reaper; logged TIMEOUT cleanly | None | `PASS` |
| `PRE-D09` | D | 512 MiB address-space policy | `harness_runner.cpp / run_rapidflow_apx_p4b.py` — RLIMIT_AS=512 MiB in preexec & apply_process_caps + streaming JSON delivery fix | DISC_06_K1 bad_alloc resolved via streaming serialization; fits well under 512 MiB | None | `PASS` |
| `PRE-D10` | D | 500,000 logical work cap | `configs/rapidflow_apx_p4b_protocol.json` — Untimed correctness verification across all 50 frozen workloads | 48/50 complete under 500k work cap; DISC_07_K0 & DISC_07_K1 censor WORK_BUDGET_EXCEEDED honestly | Zero spare censoring margin on DISC unless Coordinator overrides cap/labels for DISC-07 | `PASS` |
| `PRE-E01` | E | Workload-level experimental unit | `scripts/summarize_rapidflow_apx_p4b.py` — test_03 & test_06 anti-pseudo-replication test | Each workload gets exact weight 1 regardless of retries or 5 repetitions | None | `PASS` |
| `PRE-E02` | E | Within-workload median aggregation | `analyze_p4b_ledger()` — median(t_b[1..5]) and median(t_v[1..5]) | Exact median aggregation within 5/5 complete cells verified | None | `PASS` |
| `PRE-E03` | E | Correct speedup ratio | `analyze_p4b_ledger()` — S_{V,i} = median(BARE) / median(Variant) | S>1 faster verified across synthetic & boundary checks | None | `PASS` |
| `PRE-E04` | E | Primary median speedup estimator | `analyze_p4b_ledger()` — linear_percentile(sorted_speedups, 0.50) | Median speedup verified against DEC cases & full synthetic pipeline | None | `PASS` |
| `PRE-E05` | E | Secondary geometric mean | `analyze_p4b_ledger()` — exp(mean(log(speedup_i))) reported alongside median | Verified S_geo calculation on positive finite speedup pairs | None | `PASS` |
| `PRE-E06` | E | Fixed bootstrap method | `stratified_block_bootstrap_median()` — Deterministic 10,000 resamples across suites | Verified deterministic repeated runs produce bit-exact CI bounds | None | `PASS` |
| `PRE-E07` | E | Matched-pair/block dependence handled | `stratified_block_bootstrap_median()` — test_06 tests DISC k=0/k=1 intact blocks vs incomplete block drop | Complete DISC group blocks resampled intact; incomplete blocks excluded from bootstrap CI | None | `PASS` |
| `PRE-E08` | E | Fixed bootstrap seed and quantiles | `BOOTSTRAP_SEED = 380302` — 2.5th and 97.5th percentiles via linear interpolation | Seed 380302 & percentiles [0.025, 0.975] verified + Holm alpha/3 sensitivity computed | Pure Python stdlib used cleanly (scipy unavailable) | `PASS` |
| `PRE-E09` | E | Missing-pair policy | `analyze_p4b_ledger()` — test_04 incomplete 4/5 pair exclusion | Strict 5-of-5 paired inclusion verified; partial pairs censored to table | None | `PASS` |
| `PRE-E10` | E | Censoring policy | `run_rapidflow_apx_p4b.py / summarize_rapidflow_apx_p4b.py` — Explicit TIMEOUT / WORK_BUDGET_EXCEEDED / OOM / CONFIG classification in test_04 | Censored runs never zeroed or coerced; Variant-only >=2 rep failure triggers Priority 3 NO_GO | None | `PASS` |
| `PRE-E11` | E | Coverage rule | `summarize_rapidflow_apx_p4b.py` — DEC-15 & >=45/50, >=27/30 cont, >=18/20 disc, >=9/10 blocks | Coverage deficiency forces Priority 4 INCONCLUSIVE cleanly | None | `PASS` |
| `PRE-E12` | E | Noise rule | `summarize_rapidflow_apx_p4b.py` — test_06 injects J=1.625 on 10/50 pairs | Flags J>1.50; >=20% noisy pairs forces Priority 4 INCONCLUSIVE | None | `PASS` |
| `PRE-E13` | E | Explicit memory overhead rule | `classify_variant_decision()` — DEC-12 (+25% NO_GO), DEC-13 (+20% PASS), DEC-16 (missing RSS INCONCLUSIVE) | Worst workload-level median peak RSS gate <=20% enforced deterministically | None | `PASS` |
| `PRE-E14` | E | Mutually exclusive decision precedence | `classify_variant_decision()` — test_01 all DEC-01..DEC-19 cases | Single mutually exclusive outcome from VALID_DECISIONS returned per variant | None | `PASS` |
| `PRE-E15` | E | Boundary and invalid-input tests | `tests/test_rapidflow_apx_p4b_statistics.py` — test_01 equality boundaries (0.95/1.05/1.15/1.00/0.20) & test_02 NaN/Inf/negative | 19/19 DEC cases + all NaN/Inf/inverted interval rejections passed | None | `PASS` |
| `PRE-E16` | E | Conditional A+B gate locked for later approval | `configs/rapidflow_apx_p4b_protocol.json & docs §27` — classify_variant_decision computes conditional_ab_trigger_met & sets approval flag | Requires S>=1.05, L>1.00, all P1..5 satisfied, plus written proposal & Coordinator approval | None | `PASS` |
| `PRE-F01` | F | Correctness tiers documented | `docs/rapidflow_apx_p4b_preregistration.md §8` — Tier C1/C2/C3 definitions & 50-workload classification | 32 Tier C1 (CONT_01..30 + DISC_01_K0/K1), 18 Tier C3 recorded | None | `PASS` |
| `PRE-F02` | F | Oracle limitations preserved | `correctness-verification.json` — Probe DISC_02_K0 on DStarDynamicOracle (cap=100,000) | Raised ORACLE_UNAVAILABLE_WITHIN_REGISTERED_BUDGET (863,040 > 100,000); not faked as M0 pass | None | `PASS` |
| `PRE-F03` | F | Current live schema integrity preserved | `test_rapidflow_apx_engines + canonical_hand_*` — 25/25 hand across 4 modes + full repository suite | All 25 hand cases x 4 modes exited 0; neighbor_configuration_version=2 enforced | None | `PASS` |
| `PRE-F04` | F | FAR behavior preserved | `test_rapidflow_apx_p4b + test_rapidflow_apx_residual_nlf` — Full regression suite + TX-01..25 + A1..A7 | All FAR rollback/fault/empty-frontier tests pass cleanly | None | `PASS` |
| `PRE-F05` | F | Option B logical parity preserved | `test_02 + correctness-verification.json` — Canonical recursive diff (diff_canonical_structures, is_3way=True) on 48 completed P4b workloads | Zero logical counter or tuple diffs across 30/30 CONT + 18/20 completed DISC | None | `PASS` |
| `PRE-F06` | F | Option A counter semantics preserved | `test_rapidflow_apx_p4b + test_rapidflow_apx_residual_nlf` — CNT-01..04 + A_STATIC/A_RESIDUAL differential parity across 48 completed P4b workloads | Exact tuple parity with honest NLF counters across 30/30 CONT + 18/20 completed DISC | None | `PASS` |
| `PRE-F07` | F | Raw execution ledger schema | `LEDGER_SCHEMA_VERSION = 'p4b-ledger-v1.0'` — test_03 & test_04 in statistics & measurement suites | Schema contract enforces manifest/schedule/protocol hashes, timings, RSS, censoring | None | `PASS` |
| `PRE-F08` | F | No silent slot overwrite | `resolve_p4b_ledger_records()` — test_04_ledger_no_silent_overwrite_and_incomplete_history_preserved + test_05 | Preserves prior failed attempts; rejects duplicate eligible slot attempts fail-closed | None | `PASS` |
| `PRE-F09` | F | Clean-revision full regression | `97d8636b9567d53d3e083c48d61b46feb40ae540` — PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v | Ran 845 tests in 280.030s -> OK (exit 0, 0 failures, 0 errors, 0 skips) | None | `PASS` |
| `PRE-F10` | F | Relevant sanitizer verification | `/tmp/rapidflow-p4b3a-m3dDSO/sanitizer.log` — ASan + UBSan native build running all 5 native test selectors | All 5 native suites exited 0 under ASan+UBSan | Executed with ASAN_OPTIONS=detect_leaks=0 due to documented upstream Graph::buildEdgeIndex spp leak | `PASS` |
| `PRE-G01` | G | AUD-P4B-001 remediation evidence | `/tmp/rapidflow-p4b3a-m3dDSO/audit-finding-closure.json` — Verify all AUD-P4B-001 statistical/decision tests & artifacts exist | Status REMEDIATION_IMPLEMENTED_PENDING_REVIEW | None | `PASS` |
| `PRE-G02` | G | AUD-P4B-002 remediation evidence | `/tmp/rapidflow-p4b3a-m3dDSO/audit-finding-closure.json` — Verify all AUD-P4B-002 workload/seam tests & artifacts exist | Status REMEDIATION_IMPLEMENTED_PENDING_REVIEW | None | `PASS` |
| `PRE-G03` | G | Complete preregistration document | `docs/rapidflow_apx_p4b_preregistration.md` — Verify all 31 required sections + hash lock + pending approval status | Written cleanly and hashed in protocol-hash-register.json | None | `PASS` |
| `PRE-G04` | G | Machine-readable protocol | `configs/rapidflow_apx_p4b_protocol.json` — Verify approval_state=PREREGISTRATION_CANDIDATE & execution_authorized=false & matched hashes | SHA-256 70836770663c32a1a53e52e048be1dd8268fd2e3f02f66859737eef308c4acbb (5,137 bytes) | None | `PASS` |
| `PRE-G05` | G | Reproducible evidence package | `/tmp/rapidflow-p4b3a-m3dDSO/review-evidence-p4b3a.tar.gz` — Verify archive inventory, SHA-256 manifest, synthetic/real separation | Packaged cleanly and verified via evidence-manifest.json | None | `PASS` |
| `PRE-G06` | G | Protocol/manifest/schedule hash register | `/tmp/rapidflow-p4b3a-m3dDSO/protocol-hash-register.json` — Compute SHA-256 across doc, protocol, manifest, schedule, scripts, binary, 50 fixtures | Complete cryptographic register verified | None | `PASS` |
| `PRE-G07` | G | Final clean Git state and authorized push | `feature/rf38-p4b-optimization` — Verify git diff --check, clean tree, push via scripts/push_issue_branch.sh, Draft PR #39 updated | Clean tree pushed strictly to feature/rf38-p4b-optimization | None | `PASS` |
| `PRE-G08` | G | Independent review pending, P4b-3B not authorized, STOP | `GitHub PR #39 / Issue #38` — Hold P4B_3A_READY_FOR_INDEPENDENT_PREREGISTRATION_REVIEW without launching benchmarks | Pending Independent Technical Lead review and Coordinator authorization | Requires explicit Coordinator sign-off before P4b-3B | `PENDING_COORDINATOR_APPROVAL` |

---

## 10. Evidence Provenance & Hash Register

| Artifact | Path | SHA-256 | Size (bytes) |
|---|---|---|---:|
| Preregistration Specification | `docs/rapidflow_apx_p4b_preregistration.md` | `71cae2068f17a4e33f367817a2f6e23046775e5496f3ebf63a392db7721d1355` | `28,496` |
| Machine-Readable Protocol | `configs/rapidflow_apx_p4b_protocol.json` | `70836770663c32a1a53e52e048be1dd8268fd2e3f02f66859737eef308c4acbb` | `5,137` |
| Frozen Workload Manifest | `configs/rapidflow_apx_p4b_manifest.json` | `09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869` | `107,573` |
| Balanced Execution Schedule | `configs/rapidflow_apx_p4b_schedule.json` | `824d4b09f31432fde355597aee877f38cae3f267b66dd658d8fc82c3cac05e6f` | `716,354` |
| Workload Generator | `scripts/generate_rapidflow_apx_p4b_manifest.py` | `f5984223d8793b8c919ef25770aaa775fff8eca441548e7e953eecc67332d023` | `44,907` |
| P4b Experiment Runner | `scripts/run_rapidflow_apx_p4b.py` | `e640d94eacfba306f283c622c489374f3aa688ed3b10e3aa829cea435284ab40` | `35,417` |
| P4b Statistical Analyzer | `scripts/summarize_rapidflow_apx_p4b.py` | `b266dcb8f7ec898cf83e7b301a8c561977ddcd4c6f5ee914ec7ecc61edb72772` | `29,834` |
| Native Binary (`rapidflow_apx_native`) | `tests/native/rapidflow_apx_engines/build/rapidflow_apx_native` | `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75` | `941,216` |
| Native Harness Runner Header | `tests/native/rapidflow_apx_engines/src/harness_runner.h` | `60ce0d2e5183fb166666d07cc9474d1087affeeacfc60d9d1526cc7a4c1628a3` | `2,214` |
| Native Harness Runner Source | `tests/native/rapidflow_apx_engines/src/harness_runner.cpp` | `2a38b9c571e42be4e6dbd3879fb352f692c70899a242e6fa32ab20a2639e8091` | `77,229` |
| Native Main CLI | `tests/native/rapidflow_apx_engines/src/main.cpp` | `42b5835666ea01add0307a258f9351e3fdc57084ea584e646b0c923c822d883b` | `12,799` |
| Native Measurement Seam Tests | `tests/native/rapidflow_apx_engines/src/measurement_seam_tests.cpp` | `5005a3dfe01d24d356d8d40764a352180031305e5e8c6ab5a0bbd38ef7ca224c` | `12,421` |

Historical P4a preservation verified untouched at `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT03` (`p4a_evidence_manifest.json` SHA-256 `b6d7777d5b17c10db8e5f00f5c40885a99b9fefb06242c3c05bbd1064a9d70be`, raw ledger `34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5`, `441,311` bytes, P4a binary `6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b`). All 50 individual fixture SHA-256 digests are preserved inside `/tmp/rapidflow-p4b3a-m3dDSO/protocol-hash-register.json`.

---

## 11. Remaining Limitations

1. **`DISC_07` Canonical Work-Budget Disposition:** Both `DISC_07_K0` and `DISC_07_K1` (`Triangular Prism`, $N=64, p=0.30$, `uniform`) exceeded `max_work = 500,000` (`WORK_BUDGET_EXCEEDED`), leaving `18 / 20` DISC eligibility and `9 / 10` complete DISC blocks (meeting registered `>= 90%` and `>= 9/10` floors with zero spare censoring margin). Retained unmodified awaiting Coordinator decision.
2. **Oracle Scalability:** Independent exhaustive M0 covers `32 / 50` workloads (`30` Continuity + `DISC_01_K0/K1`). Workloads `DISC_02..10` (`18` workloads) rely on Tier C3 differential equivalence vs verified `BARE` M2.
3. **Non-Random Workload Design:** Benchmarks evaluate curated `30 + 20` synthetic/continuity strata, not a random sample of arbitrary real-world NetFlow streams (Phase P5).
4. **Finite Repeat Count & Host Governor:** Proposed $R = 5$ on a shared laptop (`i5-8265U`, `powersave` governor); `J > 1.50` / `< 20%` flags uncontrolled OS noise cleanly.
5. **Observer Separation Boundary:** Primary `PROFILE_OFF` excludes fine micro-timers (`StepProfiling`), whose overhead is assessed strictly in the separate `250`-slot `PROFILE_ON` session.
6. **Memory Measurement & Sanitizer Scope:** Peak RSS reads Linux `/proc/self/status` `VmHWM:` (4 KiB page granularity, falling back to `wait4` only on crash without JSON); ASan+UBSan ran with `ASAN_OPTIONS=detect_leaks=0` due to documented upstream `Graph::buildEdgeIndex` `spp` leak.

---

## 12. Coordinator Approval Checklist (Required Prior to P4b-3B)

- [ ] **50-Workload Inventory & Generator Reproducibility:** Approve `30` Continuity + `20` Discrimination workloads (`configs/rapidflow_apx_p4b_manifest.json`, SHA `09c78158...`).
- [ ] **Pre-Lock Disposition for `DISC_07` (`DISC_07_K0`, `DISC_07_K1`):** Choose either retaining frozen uniform `p=0.30` under `max_work = 500,000` (`18/20` DISC baseline eligibility, zero spare margin) **or** registering a documented Coordinator override (e.g. mixed vocabulary or higher `DISC-07` work cap) prior to lock.
- [ ] **Primary Configuration Quartet (`BARE`, `A_STATIC`, `A_RESIDUAL`, `B_MERGE`) & Auxiliary `B_BUFFER` Isolation:** Confirm strict separation in `primary` vs `diagnostic`.
- [ ] **Repeat Proposal ($R = 5$), Warm-Up (`200`), Diagnostic (`250`), Balanced Williams Schedule (`1,000` primary, seeds `380301`/`380303`).**
- [ ] **Stratified Block Bootstrap (`seed = 380302`, `10,000` resamples), Unadjusted `95%` Primary CI + Holm Sensitivity, `90%` Coverage Floors, `J > 1.50` Noise Policy, `+20%` Worst-Workload RSS Threshold, and 11-Priority Decision Classifier.**
- [ ] **Conditional A+B Trigger (`S >= 1.05, L > 1.00`, Priorities 1..5 cleared) & Resource Policy (`60s`, `512 MiB`, `500,000` work, `256 MiB` NLF limit).**
- [ ] **Grant Phase P4b-3B Authorization:** Update `approval_state = "MEASUREMENT_AUTHORIZED"` and `execution_authorized = true` only after Independent Technical Lead review passes.
