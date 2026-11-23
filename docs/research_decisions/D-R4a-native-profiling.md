# Research Decision Record: D-R4a — Bare M2 Native Profiling, Baseline Measurement & Evidence-Driven Pilot

**Document ID:** `docs/research_decisions/D-R4a-native-profiling.md`  
**Milestone:** Phase P4a  
**Parent Issue:** [Issue #36](https://github.com/iamqthanq/Seminar_RapidFlow/issues/36)  
**Target Integration Branch:** `QuangThang`  
**Base Commit:** `b5a5fe44fe594f9c550dcc44f04b9f4fc9a54245` (verified merged PR #35)  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)  
**Status:** VERIFIED & COMPLETED  
**Final Decision Verdict:** `EMPIRICAL_PROFILING_COMPLETE_OPTION_B_AND_A_RECOMMENDED_FOR_P4B`  

---

## 1. Executive Summary & Review Scope

Phase P4a completes the native C++ nanosecond profiling and empirical baseline measurement for the Bare M2 native event engine (`ApxM2EventEngine`) against the M1-enum baseline (`ApxM1EnumEngine`) under strict Domain D-R conditions.

Across 90 paired comparisons (30 canonical workloads $\times$ 3 repetitions $\times$ 2 engines = 180 slots), zero correctness violations were observed against the independent Python `DStarDynamicOracle`.

### Primary Empirical Findings:
1. **Search Speedup ($T_{\text{M1}} / T_{\text{M2}}$):**
   - **Median Cumulative Search Speedup:** **2.16x** (Mean: **2.60x**, Max: **9.83x**).
   - **Candidate Evaluation Reduction:** Bare M2 evaluated **245,055** candidates compared to M1's **455,148** candidates (a **46.2% reduction** in search space).
2. **Streaming Execution Speedup:**
   - **Median Streaming Speedup:** **1.12x** (Mean: **1.21x**, Max: **2.03x**).
   - Scaling with tolerance $k$: Speedup increases monotonically from $k=0$ (**1.09x**) to $k=1$ (**1.16x**) to $k=2$ (**1.22x**).
   - High-ambiguity uniform regimes achieve **1.20x median** (mean **1.27x**, max **1.95x**).
3. **Stage Breakdown & Bottleneck Identification:**
   - Within streaming steps, Search Core occupies **67.60%** (median) and Neighbor Retrieval occupies **16.35%** (median).
   - Within search time specifically, `ApxAuthoritativeGraph::get_neighbors(u)` accounts for **22.60%** of total search time due to `std::vector<uint32_t>` dynamic memory allocations, overlay probing, and sorting.
4. **P4b Architectural Decision:**
   - Hypothesis $H_1$ (Streaming speedup > 1.0x) is **CONFIRMED** (median 1.12x, search median 2.16x).
   - Hypothesis $H_2$ (Neighbor retrieval is a significant bottleneck $\ge 15\%$) is **CONFIRMED** (22.60% of search).
   - Recommendation for Phase P4b: **OPTION B & A** (Authoritative Graph Neighbor Retrieval Optimization paired with Relaxed NLF).

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
| `ApxStreamingEngine` Profiling Seams | **NEW** | Monotonic `std::chrono::steady_clock` profiling records macro stages and cumulative micro step stages. | Validated in `test_rapidflow_apx_p4a.py` with non-negative, monotonic nanosecond values. |
| Behavioral Invariance Guard | **KEEP** | Profiled and unprofiled runs produce bit-for-bit identical tuple sequences and counter values. | Verified by `test_03_profiler_behavioral_invariance`. |
| Schema Backward Compatibility | **KEEP** | Without `--profile`, CLI JSON output matches P3 schema 1.1.0 with 0 profiling keys. | Verified by `test_04_profiler_schema_isolation`. |
| Bounded 30-Workload Manifest | **NEW** | Replaces arbitrary uncalibrated configurations with 30 controlled workloads yielding 12,384 ground truth events. | 180 execution slots completed with 0 errors or timeouts. |
| Alternating AB/BA Paired Repetitions | **NEW** | Reverses execution order across repetitions to cancel OS scheduling, CPU boost, and cache warm-up bias. | Standard deviation of speedup across repetitions is small ($\sigma = 0.25$). |
| Disposable Warm-Up Replay | **NEW** | Discards initial run state to eliminate cold disk/binary loading noise before timed measurement. | Eliminates initial cache miss anomalies. |
| Relaxed NLF Implementation | **DEFER** | Deferred to Phase P4b; justified by search core occupying 77.4% of search time. | Cleanly isolates baseline measurement in P4a. |
| AuthoritativeGraph CSR Neighbor Optimization | **DEFER** | Deferred to Phase P4b; justified by neighbor retrieval occupying 22.60% of search time. | Pinpointed exact bottleneck without speculative changes. |
| Raising Work Budget Cap | **STOP** | The canonical work cap `CANONICAL_MAX_WORK_CAP = 500000` must remain strictly enforced. | All workloads completed under budget; cap remains 500,000. |
| Unbounded Scaling ($N > 24$) | **STOP** | Scalability testing beyond Oracle verification bounds is strictly prohibited in P4a. | Workloads bounded to $N \le 16$. |

---

## 4. Phase P4b Architectural Recommendation & Execution Path

### Empirical Verdict: OPTION B & A (Hybrid Optimization)
The profiling data conclusively demonstrates two concurrent optimization opportunities:
1. **Option B (Authoritative Graph Neighbor Retrieval Optimization):**
   - Current implementation in `authoritative_graph.cpp:130-160` instantiates a new `std::vector<uint32_t>` on every `get_neighbors(u)` call, performing set lookups, insertions, and `std::sort` + `std::unique`.
   - This consumes **22.60%** of search time across the pilot matrix.
   - Optimizing `get_neighbors` (e.g. stack-allocated buffers, reusable scratchpad vectors, or direct CSR span iteration) will immediately yield an estimated $15-20\%$ speedup across both M1 and M2.
2. **Option A (Relaxed NLF Pruning):**
   - Candidate evaluation and search core DFS account for **77.40%** of search time.
   - In uniform label regimes, M2 evaluated 245,055 candidates for 37,152 events (pruning efficiency 84.84%).
   - Relaxed NLF ($\delta'$-filtering) will eliminate invalid candidates before recursive DFS exploration, providing compound acceleration.

**Milestone P4a is declared COMPLETE and ready for review.**
