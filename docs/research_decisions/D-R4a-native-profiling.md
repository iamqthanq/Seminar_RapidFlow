# Research Decision Record: D-R4a — Bare M2 Native Profiling, Baseline Measurement & Evidence-Driven Pilot

**Document ID:** `docs/research_decisions/D-R4a-native-profiling.md`
**Milestone:** Phase P4a
**Parent Issue:** [Issue #36](https://github.com/iamqthanq/Seminar_RapidFlow/issues/36)
**Target Integration Branch:** `QuangThang`
**Base Commit:** `b5a5fe44fe594f9c550dcc44f04b9f4fc9a54245` (verified merged PR #35)
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)
**Status:** REMEDIATED & READY FOR INDEPENDENT REVIEW
**Final Decision Verdict:** `R4A_PROFILE_READY_FOR_P4B_REVIEW`

---

## 1. Executive Summary & Review Scope

Phase P4a completes the native C++ nanosecond profiling and empirical baseline measurement for the Bare M2 native event engine (`ApxM2EventEngine`) against the M1-enum baseline (`ApxM1EnumEngine`) under strict Domain D-R conditions.

Across 90 paired comparisons (30 canonical workloads $\times$ 3 repetitions $\times$ 2 engines = 180 slots), zero correctness violations were observed against the independent Python `DStarDynamicOracle`.

### Primary Empirical Findings:
1. **Search Speedup ($T_{\text{M1}} / T_{\text{M2}}$ across 4 Scopes):**
   - **Cumulative Search Speedup ($S_{\text{search}}$):** Median **2.16x** (Mean: **2.60x**, Max: **9.83x**).
   - **Native Step Speedup ($S_{\text{step}}$):** Median **1.82x** (Mean: **2.02x**, Max: **5.60x**).
   - **Streaming Loop Wall Speedup ($S_{\text{loop}}$):** Median **1.12x** (Mean: **1.21x**, Max: **2.03x**).
   - **Subprocess Outer Wall Speedup ($S_{\text{outer}}$):** Median **1.04x** (Mean: **1.05x**, Max: **1.44x**).
2. **Candidate Evaluation Reduction:**
   - Bare M2 evaluated **245,055** candidates compared to M1's **455,148** candidates (a **46.2% relative reduction** in candidates evaluated).
   - Both engines emitted exactly **37,152** events across 828 streaming steps with zero duplicate embeddings.
3. **Stage Breakdown & Bottleneck Identification:**
   - Within streaming steps, Search Core occupies **67.60%** (median) and Neighbor Retrieval occupies **16.35%** (median).
   - Within search time specifically, `ApxAuthoritativeGraph::get_neighbors(u)` accounts for **22.60%** of total search time due to dynamic vector allocations, overlay set probing, and sorting.
4. **P4b Architectural Decision:**
   - Hypothesis $H_1$ (Streaming speedup > 1.0x) is **CONFIRMED** across all 4 timing scopes.
   - Hypothesis $H_2$ (Neighbor retrieval is a significant bottleneck $\ge 15\%$) is **CONFIRMED** (22.60% of search).
   - Recommendation for Phase P4b: **R4A_PROFILE_READY_FOR_P4B_REVIEW**. Both Option A (Relaxed NLF) and Option B (Neighbor Retrieval Optimization) target verified empirical cost centers.

---

## 2. Review S0 / R4a-A: Contract & Architectural Alignment

1. **Upstream RapidFlow Immutability:** Upstream commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` remains untouched in `baselines/rapidflow/source`.
2. **Domain D-R Integrity:** Every tested workload strictly satisfies $0 \le k < \lambda(Q)$.
3. **Behavioral & Counter Invariance:** Validated bit-for-bit equivalence between `PROFILE_OFF` and `PROFILE_ON`.
4. **Ground Truth Consistency:** Exactly 37,152 event emissions across 828 streaming steps matched the `DStarDynamicOracle` 100%.

---

## 3. Decisions Table (KEEP / CHANGE / DEFER / STOP)

| Component / Mechanism | Action | Rationale | Concrete Evidence |
| :--- | :---: | :--- | :--- |
| `ApxAuthoritativeGraph` | **KEEP** | Authoritative hybrid CSR + overlay structure supplies exact graph snapshots without copying underlying structures. | Reused in all P4a workloads; passes 100% M0 cross-validation across 180 slots. |
| `ApxStreamingEngine` Profiling Seams | **KEEP** | Monotonic `std::chrono::steady_clock` profiling records macro stages and cumulative micro step stages. | Validated in `test_rapidflow_apx_p4a.py` with non-negative, monotonic nanosecond values. |
| Cryptographic Execution Identity | **CHANGE** | Every record binds deterministic hash of protocol version, manifest, commit, binary, and limits. | Enforces fail-closed resume and cross-binary pairing rejection. |
| Fail-Closed M0 Gate & Duplicate Checks | **CHANGE** | M0 exception immediately marks slot `ORACLE_FAILED` and rejects admission; raw tuples checked for duplicates before set conversion. | Tested in `test_09_m0_oracle_fail_closed_admission`. |
| Scientifically Valid Metrics | **CHANGE** | Removed invalid `pruning_efficiency_pct = 1 - events/candidates`; reported relative candidate reduction $R = 1 - (C_{M2}/C_{M1})$ and events/cand ratio. | Tested in `test_11_pruning_and_candidate_metrics_validity`. |
| Standardized 4 Speedup Scopes | **CHANGE** | Separated search, native step, streaming loop, and outer wall speedup metrics. | Tested in `test_03_profiler_timing_validity_and_scopes`. |
| `--profile-output` Safe Error Handling | **CHANGE** | Unwritable path or collision with primary output returns nonzero exit code (`IO_ERROR` or `CLI_ARG_ERROR`). | Tested in `test_06_profile_output_cli_handling`. |
| Relaxed NLF Implementation | **DEFER** | Deferred to Phase P4b; justified by search core occupying 77.4% of search time. | Cleanly isolates baseline measurement in P4a. |
| AuthoritativeGraph CSR Neighbor Optimization | **DEFER** | Deferred to Phase P4b; justified by neighbor retrieval occupying 22.60% of search time. | Pinpointed exact bottleneck without speculative changes. |
| Raising Work Budget Cap | **STOP** | The canonical work cap `CANONICAL_MAX_WORK_CAP = 500000` must remain strictly enforced. | All workloads completed under budget; cap remains 500,000. |
| Speculative P4b Speedup Claims | **STOP** | Disallow claiming unverified speedup numbers (e.g. "15-20% speedup") prior to implementation. | Replaced with grounded conditional hypotheses. |

---

## 4. Phase P4b Architectural Evaluation Matrix

Rather than prematurely locking a combined optimization path, the empirical profiling data establishes clear trade-offs across four options for Phase P4b:

| Option | Core Hypothesis | Attainable Scope | Correctness & Maintenance Risk | Verification Complexity |
| :--- | :--- | :--- | :--- | :--- |
| **Option A (Relaxed NLF)** | Filtering candidates using relaxed neighbor label frequencies will prune invalid recursive DFS branches early. | Targets Search Core (77.4% of search time). | High: Must provably avoid false negatives under $k$ missing edges and dynamic updates. | Requires exhaustive proof obligations and counterexample testing. |
| **Option B (Neighbor Retrieval Optimization)** | Replacing `std::vector` return by value with reusable scratchpad buffers / zero-copy span views reduces allocation overhead. | Targets Neighbor Retrieval (22.6% of search time). | Medium: Must preserve snapshot consistency across recursive DFS and overlay deletions. | Requires memory safety and reentrancy audits. |
| **Option B+A (Hybrid)** | Combining Option B and Option A addresses both major cost centers concurrently. | Maximum theoretical headroom. | High: Compounding algorithmic and memory architectural complexity. | High: Separate ablation runs required to isolate individual gains. |
| **Option C / D (Alternative / Pivot)** | Candidate reordering heuristics or scope re-evaluation. | Marginal or deferred. | Low. | Low. |

**Verdict:** `R4A_PROFILE_READY_FOR_P4B_REVIEW`. The final choice of path for Phase P4b will be made following independent review and explicit approval by the project coordinator.
