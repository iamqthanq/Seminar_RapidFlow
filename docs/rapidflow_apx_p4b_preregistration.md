# RapidFlow-APX Phase P4b Experimental Preregistration Specification

**Document State:** `PREREGISTRATION_CANDIDATE` · `PENDING_INDEPENDENT_REVIEW` · `PENDING_COORDINATOR_APPROVAL`
**Execution Authorization:** `execution_authorized = false` (Phase P4b-3B execution strictly blocked fail-closed until independent Technical Lead review and explicit Coordinator approval)
**Machine-Readable Source of Truth:** [`configs/rapidflow_apx_p4b_protocol.json`](../configs/rapidflow_apx_p4b_protocol.json)
**Issue / Draft PR:** Issue `#38` · Draft PR `#39` targeting `QuangThang`

---

## 1. Research Question

**Primary Question:** Which optional isolated optimization (`A_STATIC`, `A_RESIDUAL`, `B_MERGE`) improves the runtime performance of native Bare M2 (`BARE`) streaming execution under the restricted D-R continuous approximate subgraph matching contract, across which predeclared workload strata, and at what peak memory (`peak_rss_bytes`) and engineering cost?

**Sub-Questions:**
- **RQ-P4B-01 (`A_STATIC`):** Does Sound Static Relaxed NLF prune enough failing candidate checks to outweigh per-candidate signature comparisons, `ApxNlfIndex` G0 initialization, transactional index maintenance/undo, and extra RAM?
- **RQ-P4B-02 (`A_RESIDUAL`):** Does Conservative Residual NLF (`m_cur + delta_res_cons <= k`) yield meaningful gains over `BARE` and `A_STATIC` on $k > 0$ workloads with committed missing edges (`m_cur > 0`), despite per-anchor `ApxResidualMissingState` construction and maintenance?
- **RQ-P4B-03 (`B_MERGE`):** Does depth-indexed neighbor buffer reuse combined with $O(d)$ sorted linear neighbor merge and empty-overlay fast path reduce retrieval overhead and streaming-loop wall time while preserving exact `BARE` search traversal and logical counters?
- **RQ-P4B-04 (Workload Sensitivity):** Do variant speedups vary materially across suite (`continuity` vs `discrimination`), tolerance budget ($k=0$ vs $k \ge 1$), graph size (`small <= 16`, `medium 17..64`, descriptive `large >= 65`), label regime (`uniform` vs `mixed`), and churn regime?
- **RQ-P4B-05 (Resource Feasibility):** Do any candidate variants violate the registered `+20%` worst-workload median peak RSS overhead threshold or suffer variant-only resource failures under 512 MiB `RLIMIT_AS` and 60s wall timeout?

## 2. Preregistered Hypotheses

- **H-A (`A_STATIC`):** `A_STATIC` will reduce candidate evaluations and runtime on label-selective mixed-regime or deficit-rich graphs while potentially adding overhead on small or uniform-label low-pruning graphs.
- **H-AR (`A_RESIDUAL`):** `A_RESIDUAL` may prune additional DFS branches when $k > 0$ and committed missing edges exist, subject to per-anchor residual tracking cost.
- **H-B (`B_MERGE`):** `B_MERGE` will lower physical neighbor-retrieval cost under high degree and mixed overlay/tombstone churn without altering DFS traversal order or logical counters.
- **H-COST (Overhead Symmetry):** Index/filter or merge setup overhead may yield `PARITY` or runtime regression (`S < 1.00`) on tiny/micro graphs; `PARITY`, `NO_GO`, `INCONCLUSIVE`, and `QUERY_DEPENDENT` are all scientifically valid outcomes.

## 3. Accepted Correctness Baseline

| Milestone / Baseline | Commit / Artifact SHA-256 | Accepted Scope |
|---|---|---|
| Upstream RapidFlow Source (`baselines/rapidflow/source/`) | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine) | Reference CSR / `GlobalEdgeView` layout |
| Accepted P4b-2B Pre-P4b-3A Starting Revision | `a4e9d41defa78a3cc1b34f27bff43aa98cf858db` | Baseline M1, Bare M2, A-static, A-residual, B-buffer, B-merge |
| Preserved Starting Binary (`accepted-p4b2b-native`) | `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78` | Pre-measurement infrastructure baseline |
| P4b-3A Tested Implementation Commit | `97d8636b9567d53d3e083c48d61b46feb40ae540` | Measurement seam, workload freeze, schedule, stats |
| P4b-3A Candidate Binary (`rapidflow_apx_native`) | `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75` | Built with `-O3 -Wall -Wextra -DAPX_TEST_ACCESS=1 -std=c++17` (`g++ 13.3.0`) |
| Historical P4a Raw Ledger (`D_INT03`, descriptive only) | `34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5` (`441,311` bytes) | Immutable historical pilot reference; never reused as P4b primary controls |

## 4. Configuration Registry

**Primary Confirmatory Quartet (4 Isolated Variants):**
- `BARE`: `--engine m2 --nlf-mode none --neighbor-mode legacy` (primary control)
- `A_STATIC`: `--engine m2 --nlf-mode static --neighbor-mode legacy` (isolated Option A static)
- `A_RESIDUAL`: `--engine m2 --nlf-mode residual --neighbor-mode legacy` (isolated Option A conservative residual)
- `B_MERGE`: `--engine m2 --nlf-mode none --neighbor-mode merge` (isolated Option B linear sorted merge)

**Auxiliary Attribution Variant (Excluded from Primary Confirmatory Comparisons):**
- `B_BUFFER`: `--engine m2 --nlf-mode none --neighbor-mode buffer` (strictly restricted to `DIAGNOSTIC` / `PROFILE_ON` slots for buffer-vs-merge decomposition)

## 5. Workload Inventory (50 Frozen Workloads)

- **Suite 1 — Continuity (`P4B_CONT_01` .. `P4B_CONT_30`, 30 workloads):** Semantically identical preservation of all 30 certified `P4A_WL_01` .. `P4A_WL_30` definitions from `configs/rapidflow_apx_p4a_manifest.json` (`f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18`), converted deterministically to unified UTF-8 sorted-key JSON bytes (`max_work = 500000`). Historical P4a per-run transient JSON payloads were deleted at run time by `run_rapidflow_apx_p4a.py` (`raw_execution_bytes_available = false`), but all 30 frozen dictionaries match historical `p4a_raw_ledger.jsonl` `workload_sha256` (`compute_dict_sha256`) with zero discrepancies.
- **Suite 2 — Discrimination (`DISC_01_K0` .. `DISC_10_K1`, 20 workloads across 10 matched groups):**
  - `DISC-01`: $|V_G|=16$, Erdős–Rényi $p=0.20$, seed `101`, `triangle` ($\lambda=2$), $k \in \{0,1\}$, 32 INS / 0 DEL, `uniform` (`int(1)`)
  - `DISC-02`: $|V_G|=32$, 4-regular configuration model (`degree=4`, `max_retries=1000`), seed `102`, `c4` ($\lambda=2$), $k \in \{0,1\}$, 50 INS / 0 DEL, `uniform`
  - `DISC-03`: $|V_G|=32$, Erdős–Rényi $p=0.40$, seed `103`, `diamond` ($\lambda=2$), $k \in \{0,1\}$, 50 INS / 0 DEL, `uniform`
  - `DISC-04`: $|V_G|=32$, two-cluster (`16+16`, `p_intra=0.45`, `p_inter=0.08`), seed `104`, `diamond` ($\lambda=2$), $k \in \{0,1\}$, 40 INS / 10 DEL, `mixed` (`V:{1,2,3}`, `E:{10,20,30}`)
  - `DISC-05`: $|V_G|=64$, Erdős–Rényi $p=0.35$, seed `105`, `k4` ($\lambda=3$), $k \in \{0,1\}$, 64 INS / 0 DEL, `uniform`
  - `DISC-06`: $|V_G|=64$, two-cluster bridge (`32+32`, `p_intra=0.30`, `p_inter=0.02`, fixed bridges `(30,32),(31,33)`), seed `106`, `house` ($\lambda=2$), $k \in \{0,1\}$, 48 INS / 16 DEL, `uniform`
  - `DISC-07`: $|V_G|=64$, Erdős–Rényi $p=0.30$, seed `107`, `triangular_prism` ($\lambda=3$), $k \in \{0,1\}$, 80 INS / 0 DEL, `uniform` *(see Section 30 pre-execution finding: both `DISC_07_K0` and `DISC_07_K1` hit `WORK_BUDGET_EXCEEDED` at `max_work = 500000`)*
  - `DISC-08`: $|V_G|=128$, high-churn Erdős–Rényi $p=0.15$, seed `108`, `k4` ($\lambda=3$), $k \in \{0,1\}$, 50 INS / 50 DEL, `uniform`
  - `DISC-09`: $|V_G|=128$, Barabási–Albert ($m=4$, $K_5$ init clique), seed `109`, `k4` ($\lambda=3$), $k \in \{0,1\}$, 80 INS / 20 DEL (50% hub-biased INSERT), `uniform`
  - `DISC-10`: $|V_G|=64$, adversarial deficit churn (`{0..31}` label `1` $p=0.22$; `{32..63}` label `2` $p=0.18$; cross $p=0.04$ with 87.5% distractor edge label `10` and 12.5% compatible label `20`), seed `110`, `triangle` ($\lambda=2$), $k \in \{0,1\}$, 32 INS / 32 DEL (75% cross-cluster biased), `mixed`

## 6. Exact Workload Generation Rules

Implemented in [`scripts/generate_rapidflow_apx_p4b_manifest.py`](../scripts/generate_rapidflow_apx_p4b_manifest.py) (`GENERATOR_VERSION = "p4b-wlgen-v1.0.0"`, Python `3.12.3` stdlib `random.Random` MT19937):
1. Five strictly decoupled random streams per group (`topology`, `vertex_labels`, `edge_labels`, `update_ops`, `update_edges`), seeded via first 8 big-endian bytes of `SHA256(f"{GENERATOR_VERSION}:{group_seed}:{stream_name}")`.
2. Canonical candidate-pair enumeration iterates sorted `0 <= u < v < n`; random choices sample exclusively from explicitly sorted lists (`O(n^2)` candidate sets or sorted present edge lists), guaranteeing 100% immunity to `PYTHONHASHSEED`.
3. `DISC` pairs ($k=0$ vs $k=1$) share identical `query`, `initial_graph`, `updates`, `max_work`, `regime`, `topology`, and `query_type`; every `DELETE` verifies exact authoritative edge existence and active label parity in the generator's maintained shadow graph.

## 7. Fixture Freeze and Hashes

All 50 JSON payloads are frozen under canonical UTF-8 sorted JSON (`indent=2`, `sort_keys=True`, `ensure_ascii=False`, LF trailing newline) inside `tests/fixtures/p4b/{continuity,discrimination}/` and indexed in [`configs/rapidflow_apx_p4b_manifest.json`](../configs/rapidflow_apx_p4b_manifest.json) (SHA-256 `09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869`). Independent regeneration across default and `PYTHONHASHSEED=424242` produced `0` byte discrepancies across `50 / 50` fixtures.

## 8. Correctness Tiers

- **Tier C1 (`M0_VERIFIED` — Exhaustive Independent Oracle):** `P4B_CONT_01` .. `P4B_CONT_30` ($|V_G| \le 16$, $|V_Q| \le 4$) and `DISC_01_K0`, `DISC_01_K1` ($|V_G|=16$, $|V_Q|=3$, $P(16,3)=3,360 \le 100,000$).
- **Tier C2 (Analytic Witness):** Native empty-frontier witness (`A2/A3` Diamond/C4/Chord), `A1..A7` regression witnesses, signature coercion (`0` vs `"0"`), TX rollback witnesses.
- **Tier C3 (`DIFFERENTIAL_M2_VERIFIED` — Oracle Unavailable Within Registered Budget):** `DISC_02_K0` .. `DISC_10_K1` ($|V_G| \in \{32, 64, 128\}$). Probed on `DISC_02_K0` raising `ORACLE_UNAVAILABLE_WITHIN_REGISTERED_BUDGET` (`Candidate injection bound 863040 exceeds limit 100000`). All completed runs require step-by-step `entered_tuples` and `exited_tuples` equality against `BARE`; `B_MERGE` & `B_BUFFER` must additionally match `BARE` recursively across all non-excluded fields including search logical counters (`diff_canonical_structures`, `is_3way=True`).

## 9. Measurement Boundaries

Implemented in [`tests/native/rapidflow_apx_engines/src/harness_runner.cpp`](../tests/native/rapidflow_apx_engines/src/harness_runner.cpp) (`std::chrono::steady_clock`):
- `input_parsing_ns` (`PARSE_START .. PARSE_END`): file I/O + input SHA-256 + JSON parse.
- `initialization_wall_ns` (`INIT_START .. INIT_END`): `graph_init_ns` + `query_prep_ns` + `engine_and_nlf_init_ns` (`ApxTupleSink`, `ApxStreamingEngine`, `ApxNlfIndex::initialize` G0 construction).
- `streaming_loop_wall_ns` (`PRIMARY_TIMER_START .. PRIMARY_TIMER_STOP`): **Primary decision metric.** Covers exclusively the `total_steps` invocations of `streaming_engine.execute_step()` and retention of compact `RetainedStepOutcome` C++ vectors (`entered_tuples`, `exited_tuples`, `step_counters`, status). Zero JSON DOM construction, zero disk I/O, zero `graph_state`/`codec_state` inside this interval.
- `reporting_wall_ns` (`REPORTING_START .. REPORTING_END`): post-timer JSON string conversion and step serialization.
- `outer_wall_ns`: Python runner `time.perf_counter_ns()` spanning `Popen` launch to child reap (`wait4`).

## 10. Profiling Isolation (`--execution-purpose`)

- **`correctness` (default if flag omitted):** Preserves exact historical P4b-2B JSON output schema and per-step `build_graph_state_json()` + `codec_state` reconstruction + TX/delivery fault injection hooks.
- **`performance` (`PROFILE_OFF`):** Strictly sets `profiling_enabled = false`, `deep_diagnostics_enabled = false`; rejects `--profile` / `--profile-output` with `CLI_ARG_ERROR` (exit `1`). Emits `measurement_schema_version = 1`.
- **`diagnostic` (`PROFILE_ON`):** Sets `profiling_enabled = true`, `deep_diagnostics_enabled = false`; enables fine-grained engine/step timers without building deep graph DOMs inside the timed loop; strictly disqualified from `primary` confirmatory slots.

## 11. Resource Limits

- Wall-clock timeout: `60.0` seconds (`--wall-timeout-seconds 60` + Python process-group reaper)
- Virtual address-space ceiling: `512` MiB (`RLIMIT_AS` enforced in both Python preexec and native `apply_process_caps()`)
- Logical per-step work cap: `500,000` `work_units` (`CANONICAL_MAX_WORK_CAP`)
- NLF index logical admission budget: `256` MiB (`--max-nlf-memory-mib 256`)

## 12. Environment Policy

Pinned host execution environment recorded in `environment-manifest.json`: `Ubuntu 24.04.5 LTS`, Linux `7.0.0-43-generic x86_64`, `Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz` (`4` cores / `8` threads), CPU governor `powersave`, `12,380,356,608` bytes total RAM (`4,730,716,160` bytes available at preflight), `g++ 13.3.0`, `cmake 3.28.3`, Python `3.12.3` (`numpy` and `scipy` absent; zero third-party statistical dependency). Confirmatory sessions require single-session execution, clean repo tree (`CLEAN`), and matched binary/manifest/schedule SHA-256 locks.

## 13. Warm-Up Policy

- `1` disposable warmup slot per `(workload, primary_variant)` (`50 × 4 = 200` warmup slots) executed immediately prior to Repetition 1 in the same sequence order and under identical `PROFILE_OFF` configuration and resource caps.
- Warmup timings never enter primary medians/ratios or bootstrap resamples; warmup failures are logged verbatim in the raw ledger without replacing failed warmups via unbounded retries.

## 14. Repeat Count

- **Proposed $R = 5$** repetitions per `(workload, primary_variant)` cell (`50 × 4 × 5 = 1,000` primary slots), subject to Coordinator sign-off prior to P4b-3B execution.
- Within-workload aggregation uses exact odd-sample median (`5` repetitions), resistant to single-run OS scheduling jitter (`1` contaminated repetition out of `5` does not corrupt the workload median, while `J = max/min > 1.50` flags noisy cells transparently).

## 15. Balanced Schedule (`configs/rapidflow_apx_p4b_schedule.json`)

- **Seed `380301` (`primary` + `warmup`):** Uses a `4×4` Williams crossover Latin square (`S1`: `BARE, A_STATIC, B_MERGE, A_RESIDUAL`; `S2`: `A_STATIC, A_RESIDUAL, BARE, B_MERGE`; `S3`: `A_RESIDUAL, B_MERGE, A_STATIC, BARE`; `S4`: `B_MERGE, BARE, A_RESIDUAL, A_STATIC`). Each workload traverses `{S1, S2, S3, S4}` once across repetitions `1..4` in randomized order, and repetition `5` assigns `S1..S4` in near-equal balance (`13 / 13 / 12 / 12`), yielding global variant execution positions balanced in `{62, 63}` out of `250` slots per position.
- **Seed `380303` (`diagnostic`):** Separate cyclic `5×5` rotated block (`250` slots: `50 × 5` including `B_BUFFER`), never interleaved with primary slots.

## 16. Primary Estimand

1. Within workload $W_i$ and variant $V$: $\widetilde{T}_{V,i} = \operatorname{median}(T_{V,i,1}, \ldots, T_{V,i,5})$ using `streaming_loop_wall_ns`.
2. Paired workload speedup: $S_{V,i} = \widetilde{T}_{\mathrm{BARE},i} / \widetilde{T}_{V,i}$ (only when **all `5 / 5`** repetitions for both `BARE` and $V$ pass strict eligibility).
3. Across-workload primary estimator: $S_{\mathrm{median},V} = \operatorname{median}(S_{V,1}, \ldots, S_{V,n})$.

## 17. Secondary Estimands

- Across-workload geometric mean: $S_{\mathrm{geo},V} = \exp\big(\frac{1}{n}\sum_{i=1}^n \ln S_{V,i}\big)$ (finite positive ratios required).
- Quantile spread (`min`, `q25`, `median`, `q75`, `max`), absolute medians (`bare_median_ns`, `variant_median_ns`, `abs_time_saved_ns`), `outer_wall_ns`, and `initialization_wall_ns`.

## 18. Bootstrap Method

- **Method:** Deterministic stratified block bootstrap in Python 3.12 stdlib (`random.Random(380302)`, `10,000` resamples).
- **Resampling Blocks:**
  - `continuity` suite: resamples eligible `workload_id` units with replacement (each workload holds equal weight `1`).
  - `discrimination` suite: resamples complete `group_id` blocks (preserving matched $k=0$ / $k=1$ pairs together). If one member of a `DISC` group fails eligibility, that workload may remain in per-workload tables, while block bootstrap uses complete `k=0`/`k=1` group pairs (`complete_disc_blocks >= 9`).
- **Confidence Interval:** Two-sided `[2.5%, 97.5%]` percentile interval using linear quantile interpolation (`linear_percentile`).

## 19. Multiplicity Limitations

Three primary variants (`A_STATIC`, `A_RESIDUAL`, `B_MERGE`) share `BARE` control comparisons. Primary decisions use per-comparison unadjusted `95%` bootstrap intervals; `scripts/summarize_rapidflow_apx_p4b.py` also computes supplementary Holm–Bonferroni sensitivity intervals (`alpha = 0.05 / 3`). Unadjusted `95%` intervals must NOT be claimed as simultaneous family-wise `95%` intervals. `A_RESIDUAL` vs `A_STATIC` is a secondary contrast only.

## 20. Coverage Policy

- **Minimum Paired Coverage:** $\ge 90\%$ overall (`>= 45 / 50`), $\ge 90\%$ Continuity (`>= 27 / 30`), $\ge 90\%$ Discrimination (`>= 18 / 20`), and $\ge 9$ complete `DISC` matched pairs (`complete_disc_blocks >= 9`).
- **Variant-Only Resource Regression:** If `BARE` completes `5 / 5` eligible reps on workload $W_i$ while variant $V$ suffers $\ge 2$ repetitions of variant-specific resource failure (`TIMEOUT`, `WORK_BUDGET_EXCEEDED`, `MEMORY_LIMIT_FAILURE`, `RESOURCE_UNAVAILABLE`, `CONFIGURATION_UNAVAILABLE`), Priority 3 classifies $V$ as `NO_GO` (`RESOURCE_FEASIBILITY_REGRESSION`).

## 21. Censoring Policy

Non-eligible outcomes (`TIMEOUT`, `WORK_BUDGET_EXCEEDED`, `RESOURCE_UNAVAILABLE`, `MEMORY_LIMIT_FAILURE`, `CONFIGURATION_UNAVAILABLE`, `SCHEMA_ERROR`, `MEASUREMENT_ERROR`) are never dropped silently, zeroed, or coerced to `60s`. Incomplete `5/5` pairs are tabulated explicitly in `censoring_table` (`both_complete`, `bare_complete_variant_incomplete`, `bare_incomplete_variant_complete`, `both_incomplete`) and excluded from successful timing medians.

## 22. Noise Policy

For each complete cell, jitter factor $J_{V,i} = \max_r T_{V,i,r} / \min_r T_{V,i,r}$ flags high-variance cells whenever $J > 1.50$. If $\ge 20\%$ of eligible workload pairs contain a flagged `BARE` or variant cell, Priority 4 classifies overall inference as `INCONCLUSIVE` (`INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE`). Outliers are never deleted post hoc.

## 23. Memory Policy

- **Primary Resource Metric:** Per-child post-`execve()` peak resident set size read from Linux `/proc/self/status` `VmHWM:` inside the native child after `REPORTING_END` (`linux_proc_self_status_vmhwm`, KiB $\times 1024$ bytes; `os.wait4` `ru_maxrss` kept as cross-check & abnormal-exit fallback, since Linux `fork() -> execve()` inherits parent RSS into `wait4` `ru_maxrss`).
- **Per-Workload Overhead:** $\widetilde{M}_{V,i} = \operatorname{median}(M_{V,i,1..5})$, $H_{V,i} = (\widetilde{M}_{V,i} / \widetilde{M}_{\mathrm{BARE},i}) - 1$.
- **Gate:** Every eligible workload in intended scope must satisfy $H_{V,i} \le 0.20$ (`worst_workload_rss_overhead <= 0.20`); exceeding `0.20` triggers Priority 5 `NO_GO` (`MEMORY_OVERHEAD_EXCEEDS_REGISTERED_LIMIT`). Missing valid RSS triggers Priority 2 `INCONCLUSIVE`.

## 24. Mutually Exclusive 11-Priority Decision Precedence (`classify_variant_decision`)

| Priority | Condition | Decision Outcome | Reason Code / Note |
|---:|---|---|---|
| 1 | `semantic_mismatch` | `UNSOUND` | `SEMANTIC_DISCREPANCY` |
| 2 | `!evidence_integrity_ok` or `missing_rss` or invalid CI | `INCONCLUSIVE` | `EVIDENCE_INTEGRITY_OR_MEASUREMENT_INCOMPLETE` |
| 3 | `variant_only_resource_regression` ($\ge 2$ reps vs complete `BARE`) | `NO_GO` | `RESOURCE_FEASIBILITY_REGRESSION` |
| 4 | `!coverage_ok` or `excessive_noise` ($\ge 20\%$ flagged $J>1.50$) | `INCONCLUSIVE` | `INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE` |
| 5 | `worst_rss_overhead > 0.20` | `NO_GO` | `MEMORY_OVERHEAD_EXCEEDS_REGISTERED_LIMIT` |
| 6 | Conflicting eligible predeclared strata ($S \ge 1.15, L > 1$ vs $S < 0.95, U < 1$) | `QUERY_DEPENDENT` | `CONFLICTING_ELIGIBLE_STRATA` |
| 7 | $S \ge 1.15$ and $L > 1.00$ | `ADVANTAGE_OBSERVED` | `PRACTICAL_ADVANTAGE_SUPPORTED` |
| 8 | $0.95 \le S \le 1.05$ and $L \ge 0.95$ and $U \le 1.05$ | `PARITY` | `STATISTICAL_EQUIVALENCE_WITHIN_BAND` (takes precedence over `MARGINAL` at $S=1.05$) |
| 9 | $S < 0.95$ and $U < 1.00$ | `NO_GO` | `CONFIRMED_RUNTIME_REGRESSION` |
| 10 | $1.05 \le S < 1.15$ and $L > 1.00$ (when not `PARITY`) | `MARGINAL` | Maps to R4b `INCONCLUSIVE_FOR_PRACTICAL_ADVANTAGE` (`POSITIVE_BUT_BELOW_PRACTICAL_THRESHOLD`) |
| 11 | All remaining cases | `INCONCLUSIVE` | `INDETERMINATE_INTERVAL_OR_EFFECT` |

## 25. Predeclared Strata (`predeclared_strata`)

All 50 workloads map cleanly prior to measurement into:
- **Suite:** `continuity: 30`, `discrimination: 20`
- **Tolerance $k$:** `k0: 15`, `k1: 27`, `k_ge_1: 35`, `k2_descriptive_only: 8` (`< 10`, descriptive only)
- **Label Regime:** `uniform: 35`, `mixed: 15`
- **Size Band:** `small_le_16: 32`, `medium_17_to_64: 14`, `large_ge_65_descriptive_only: 4` (`< 10`, descriptive only)
- **Churn Regime:** `insert_only: 10`, `has_deletes: 40`, `low_churn_del_le_25pct: 16`, `high_churn_del_gt_25pct: 34`

Any stratum with `< 10` eligible pairs (`k=2`, `large >= 65`) is reported strictly as descriptive, never triggering standalone Priority 6 inference.

## 26. Attribution Plan (`diagnostic` / `PROFILE_ON`)

Post-confirmatory `250`-slot diagnostic session (`BARE`, `A_STATIC`, `A_RESIDUAL`, `B_MERGE`, `B_BUFFER`) captures micro-stage timings (`update_maintenance_ns`, `search_ns`, `neighbor_retrieval_ns`, `search_core_ns`, `tuple_commit_ns`) and NLF/buffer/merge counters strictly for mechanistic decomposition. Never merged with primary `PROFILE_OFF` speedup inference.

## 27. Conditional A+B Gate (`P4b-4`)

Combined `A+B` (`A_STATIC+B_MERGE` or `A_RESIDUAL+B_MERGE`) is **not authorized** in P4b-3A or P4b-3B. Conditional eligibility (`conditional_ab_trigger_met = True`) requires ALL Priority `1..5` gates satisfied, `S >= 1.05` and `L > 1.00` on at least one isolated candidate (with clean correctness verification), PLUS a separate written technical proposal and explicit Coordinator approval prior to any P4b-4 implementation.

## 28. Evidence & Append-Only Ledger Schema

- Ledger version: `p4b-ledger-v1.0` (`results/logs/rf38-p4b/p4b_raw_ledger.jsonl`).
- Append-only JSONL recording every attempted primary, warmup, and diagnostic slot including `manifest_sha256`, `schedule_sha256`, `protocol_sha256`, `slot_id`, `workload_sha256`, `execution_purpose`, `profiling_enabled`, `macro_timing` components, `outer_wall_ns`, `user_cpu_ns`, `system_cpu_ns`, `peak_rss_bytes`, `peak_rss_original_value`, `peak_rss_original_unit`, `resource_measurement_method`, `wait4_ru_maxrss_bytes`, configuration fields, and `censoring_reason`.
- `resolve_p4b_ledger_records()` preserves all attempts without silent overwrite and raises `LedgerIntegrityError` on duplicate eligible primary slot attempts or missing/unplanned slots.

## 29. Stop Conditions

1. `approval_state != "MEASUREMENT_AUTHORIZED"` or `execution_authorized != True` blocks confirmatory execution fail-closed (exit `2`).
2. Any dirty git working tree, upstream modification, native build drift, or manifest/schedule/protocol SHA-256 mismatch aborts execution fail-closed.
3. Any semantic correctness discrepancy (`TUPLE_MISMATCH`, missing/extra/duplicate tuples, state corruption) triggers immediate `UNSOUND` halt.

## 30. Known Limitations & Pre-Lock Feasibility Findings

1. **Pre-Lock Finding on Frozen `DISC_07` (`Triangular Prism`, $|V_G|=64, p=0.30$):** Under canonical `max_work = 500,000` (`CANONICAL_MAX_WORK_CAP`), untimed correctness checks showed `DISC_07_K0` (`max_step_work > 500,000`) and `DISC_07_K1` (`500,001 > 500,000` on step 1) cleanly censor with `WORK_BUDGET_EXCEEDED` on `BARE` and all variants. All remaining `18 / 20` DISC workloads (`DISC_01..06`, `DISC_08..10`, including `DISC_06_K1` after streaming JSON memory optimization) and `30 / 30` continuity workloads passed without censoring (`48 / 50 = 96%` overall, `30 / 30 = 100%` continuity, `18 / 20 = 90%` discrimination, `9 / 10` complete DISC blocks). Per Sections XI.7 and XIII.3, frozen workloads were **not** altered post-check to remove censoring; `DISC_07` is retained unmodified awaiting explicit Coordinator disposition in Section 31.
2. **Oracle Scalability:** Exhaustive M0 covers `30/30` Continuity + `DISC_01_K0/K1` (`32/50`); larger graphs (`DISC_02..10`) exceed `DEFAULT_MAX_CANDIDATES = 100,000` and rely on Tier C3 differential parity vs verified `BARE` M2.
3. **Non-Random Workload Design:** The `30+20` suite evaluates controlled synthetic/continuity topologies, not a probability sample of arbitrary real-world NetFlow streams (reserved for Phase P5).
4. **Hardware Environment:** Shared laptop environment with `powersave` CPU governor; `J > 1.50` instability policy audits OS jitter honestly.
5. **Statistical Tooling:** `scipy` is unavailable; all statistics use deterministic pure Python stdlib linear-interpolated percentile bootstrap (`seed = 380302`, `10,000` resamples).
6. **Memory Measurement Boundary:** `VmHWM` reads process-level Linux `/proc/self/status` high-water RSS in KiB (`× 1024`), isolating native post-`execve()` footprint from Python parent `fork()` inheritance while still subject to kernel 4 KiB page-granularity accounting.

## 31. Hash Lock Summary & Coordinator Approval Checklist

### Core Artifact Hashes (as of P4b-3A Freeze Candidate)

| Artifact | Path | SHA-256 | Size (bytes) |
|---|---|---|---:|
| Machine-Readable Protocol | `configs/rapidflow_apx_p4b_protocol.json` | `70836770663c32a1a53e52e048be1dd8268fd2e3f02f66859737eef308c4acbb` | `5,137` |
| Workload Manifest (50 workloads) | `configs/rapidflow_apx_p4b_manifest.json` | `09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869` | `107,573` |
| Execution Schedule (1,450 slots) | `configs/rapidflow_apx_p4b_schedule.json` | `824d4b09f31432fde355597aee877f38cae3f267b66dd658d8fc82c3cac05e6f` | `716,354` |
| Workload Generator | `scripts/generate_rapidflow_apx_p4b_manifest.py` | `f5984223d8793b8c919ef25770aaa775fff8eca441548e7e953eecc67332d023` | `44,907` |
| Benchmark Runner | `scripts/run_rapidflow_apx_p4b.py` | `e640d94eacfba306f283c622c489374f3aa688ed3b10e3aa829cea435284ab40` | `35,417` |
| Statistical Analyzer | `scripts/summarize_rapidflow_apx_p4b.py` | `b266dcb8f7ec898cf83e7b301a8c561977ddcd4c6f5ee914ec7ecc61edb72772` | `29,834` |
| Native Binary | `tests/native/rapidflow_apx_engines/build/rapidflow_apx_native` | `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75` | `941,216` |
| Preregistration Document | `docs/rapidflow_apx_p4b_preregistration.md` | Recorded after file finalization in `protocol-hash-register.json` & P4b-3A Implementation Report §10 | — |

### Coordinator Explicit Approval Checklist (Required Prior to P4b-3B)

- [ ] Approve 50-workload inventory (`30` Continuity + `20` Discrimination) and generator SHA-256 hashes (`p4b-wlgen-v1.0.0`).
- [ ] Approve disposition of `DISC_07_K0` & `DISC_07_K1` (`Triangular Prism`, $N=64, p=0.30$): keep unmodified under `max_work = 500,000` (yielding `18/20` baseline DISC coverage and `9/10` complete blocks with zero spare margin) **or** issue explicit Coordinator modification/work-cap override before `MEASUREMENT_AUTHORIZED`.
- [ ] Approve four primary configurations (`BARE`, `A_STATIC`, `A_RESIDUAL`, `B_MERGE`) and `B_BUFFER` diagnostic-only isolation.
- [ ] Approve repetition proposal $R = 5$, `200` warmup slots, `250` diagnostic slots, and balanced Williams schedule (`1,000` primary slots, seeds `380301`/`380303`).
- [ ] Approve `PROFILE_OFF` primary timing seam (`streaming_loop_wall_ns`), exclusion of deep graph diagnostics, and `/proc/self/status` `VmHWM` per-child peak RSS accounting (`wait4` fallback).
- [ ] Approve stratified block bootstrap (`seed = 380302`, `10,000` resamples), unadjusted `95%` primary CI with Holm sensitivity bounds, `90%` coverage floors, `J > 1.50` / `< 20%` noise rule, and worst-workload `+20%` peak RSS threshold.
- [ ] Approve deterministic 11-priority mutually exclusive decision classifier and conditional `A+B` gate (`S >= 1.05, L > 1.00`, separate written approval).
- [ ] Approve resource caps (`60s` wall timeout, `512 MiB` `RLIMIT_AS`, `500,000` logical work units, `256 MiB` NLF admission).
- [ ] Authorize state transition `PREREGISTRATION_CANDIDATE -> MEASUREMENT_AUTHORIZED` (`execution_authorized = true`) after Independent Technical Lead review passes.
