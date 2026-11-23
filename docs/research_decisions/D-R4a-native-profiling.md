# Research Decision Record: D-R4a — Bare M2 Native Profiling, Baseline Measurement & Evidence-Driven Pilot

**Document ID:** `docs/research_decisions/D-R4a-native-profiling.md`
**Milestone:** Phase P4a
**Parent Issue:** [Issue #36](https://github.com/iamqthanq/Seminar_RapidFlow/issues/36)
**Target Integration Branch:** `QuangThang`
**Base Commit:** `b5a5fe44fe594f9c550dcc44f04b9f4fc9a54245` (verified merged PR #35)
**Execution Commit:** `cf97a7840dff0c0f2bf791834263f3911545110c`
**Execution Identity:** `262279a9d9ea026e07231f3ba511863c383b98133db40e69f603a235ad792135`
**Harness Binary SHA-256:** `6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)
**Status:** REMEDIATED & READY FOR INDEPENDENT REVIEW
**Final Decision Verdict:** `R4A_PROFILE_READY_FOR_P4B_REVIEW`

---

## 1. Executive Summary & Review Scope

Phase P4a completes the native C++ nanosecond profiling and empirical baseline measurement for the Bare M2 native event engine (`ApxM2EventEngine`) against the M1-enum baseline (`ApxM1EnumEngine`) under strict Domain D-R conditions.

Across 90 paired comparisons (30 canonical workloads $\times$ 3 repetitions $\times$ 2 engines = 180 slots), zero correctness violations were observed against the independent Python `DStarDynamicOracle`.

### Primary Empirical Findings:
1. **Search Speedup ($T_{\text{M1}} / T_{\text{M2}}$ across 4 Scopes):**
   - **Cumulative Search Speedup ($S_{\text{search}}$):** Median **2.13x** (Mean: **2.71x**, Max: **9.74x** across 84 valid computable pairs; 6 zero-duration micro-search runs safely excluded from ratio).
   - **Native Step Speedup ($S_{\text{step}}$):** Median **1.80x** (Mean: **2.15x**, Max: **6.18x**).
   - **Streaming Loop Wall Speedup ($S_{\text{loop}}$):** Median **1.11x** (Mean: **1.21x**, Max: **2.48x**).
   - **Subprocess Outer Wall Speedup ($S_{\text{outer}}$):** Median **1.04x** (Mean: **1.06x**, Max: **1.71x**).
2. **Candidate Evaluation Reduction:**
   - Bare M2 evaluated **245,055** candidates compared to M1's **455,148** candidates (a **46.16% relative reduction** in candidates evaluated).
   - Both engines emitted exactly **37,152** events across 828 streaming steps with zero duplicate embeddings.
3. **Stage Breakdown & Bottleneck Identification:**
   - Within native step total, Search Core occupies **67.23%** (median), Neighbor Retrieval occupies **16.25%** (median), Update Maintenance occupies **3.12%** (median), and Tuple Commit occupies **1.38%** (median).
   - Within search duration specifically, `ApxAuthoritativeGraph::get_neighbors(u)` accounts for **22.78%** (median) of search time and Search Core accounts for **77.22%** (median).
4. **P4b Architectural Decision:**
   - Hypothesis $H_1$ (Streaming speedup > 1.0x) is **CONFIRMED** across all 4 timing scopes.
   - Hypothesis $H_2$ (Neighbor retrieval is a significant bottleneck $\ge 15\%$) is **CONFIRMED** (22.78% of search).
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
| Relaxed NLF Implementation | **DEFER** | Deferred to Phase P4b; justified by search core occupying 77.2% of search time. | Cleanly isolates baseline measurement in P4a. |
| AuthoritativeGraph CSR Neighbor Optimization | **DEFER** | Deferred to Phase P4b; justified by neighbor retrieval occupying 22.8% of search time. | Pinpointed exact bottleneck without speculative changes. |
| Raising Work Budget Cap | **STOP** | The canonical work cap `CANONICAL_MAX_WORK_CAP = 500000` must remain strictly enforced. | All workloads completed under budget; cap remains 500,000. |
| Speculative P4b Speedup Claims | **STOP** | Disallow claiming unverified speedup numbers (e.g. "15-20% speedup") prior to implementation. | Replaced with grounded conditional hypotheses. |
| Three-Level Integrity Architecture | **ADD** | Record-level (Level A), pair-level (Level B), and canonical completeness & raw artifact binding (Level C). | Enforces 180/180 slot completeness and exact raw artifact SHA-256 verification (Round 5). |
| Production CLI Contract Recovery | **ADD** | Restored fail-closed CLI entrypoint with `--mode canonical` and `--mode scoped`. | Tested across 16 CLI end-to-end integration scenarios E2E-01..E2E-16 (Round 5). |
| Alien Experiment Contamination Protection | **ADD** | Interleaved alien experiment records are quarantined in `rejected_conflicts`, failing ledger closed. | Tested in `test_44_e2e_10_mixed_experiments` (Round 5). |
| Unified Certification Boundary | **ADD** | Enforces fail-closed canonical API validation requiring authoritative expected header, slot matrix, and artifact binding. | Eliminates certification bypass; tested in `test_53_r6_f01` (Round 6). |
| Strict Tristate Raw Artifact Taxonomy | **ADD** | Tristate status: `NOT_CHECKED`, `VERIFIED`, `FAILED`; prevents synthetic records claiming false verification. | Tested across B01-B11 in `test_54_r6_f02` (Round 6). |
| Atomic Snapshot Loading & Data Binding | **ADD** | `load_verified_ledger` loads single byte snapshot and binds 1:1 against in-memory records. | Prevents in-memory payload tampering; tested in `test_55_r6_f03` (Round 6). |
| Decoupled Test Fixtures | **ADD** | Decouples unit and adversarial tests from unversioned raw ledger logs via deterministic synthetic fixtures. | Guarantees fresh-checkout test reproducibility; tested in `test_56_r6_f04` (Round 6). |
| Official Canonical Authority Lockdown | **ADD** | Single authoritative `OfficialCanonicalAuthority` binds human-approved historical revision `cf97a7840dff0c0f2bf791834263f3911545110c`, raw artifact SHA `34452fb4ee...`, workload manifest `f82bdcc0...`, and 180-slot plan. | Eliminates caller-defined authority bypasses; tested in `test_57_r7_f01` and `test_61_r7` (Round 7). |
| Immutable Expected Slot Matrix Enforcement | **ADD** | Caller-supplied expected matrix must deeply match the approved 180-slot, 90-pair experiment matrix; small matrices (e.g. 2 slots) rejected with `CANONICAL_PLAN_MISMATCH`. | Tested across T02-T09 in `test_57_r7_f01` (Round 7). |
| Artifact Checksum Trust Anchoring | **ADD** | Raw artifact checksums in canonical certification must match the approved reference; caller-supplied self-generated SHAs fail closed with `CANONICAL_INVALID_ARTIFACT`. | Tested across T10-T14 in `test_58_r7_f02` (Round 7). |
| Strict Provenance Non-Downgrade | **ADD** | Strict cryptographic validation is mandatory in canonical mode; disabling strict provenance is prohibited and fails closed with `CANONICAL_UNTRUSTED_CONTEXT`. | Tested across T15-T18 in `test_59_r7_f03` (Round 7). |
| CLI Manifest Authority Lockdown | **ADD** | CLI rejects unverified `--evidence-manifest` and `--manifest` overrides; custom experiments are restricted to `--mode scoped` and never issue historical READY recommendations. | Tested across T19-T27 in `test_60_r7_f04` (Round 7). |


---

## 4. Phase P4b Architectural Evaluation Matrix

Rather than prematurely locking a combined optimization path, the empirical profiling data establishes clear trade-offs across four options for Phase P4b:

| Option | Core Hypothesis | Attainable Scope | Correctness & Maintenance Risk | Verification Complexity |
| :--- | :--- | :--- | :--- | :--- |
| **Option A (Relaxed NLF)** | Filtering candidates using relaxed neighbor label frequencies will prune invalid recursive DFS branches early. | Targets Search Core (77.2% of search time). | High: Must provably avoid false negatives under $k$ missing edges and dynamic updates. | Requires exhaustive proof obligations and counterexample testing. |
| **Option B (Neighbor Retrieval Optimization)** | Replacing `std::vector` return by value with reusable scratchpad buffers / zero-copy span views reduces allocation overhead. | Targets Neighbor Retrieval (22.8% of search time). | Medium: Must preserve snapshot consistency across recursive DFS and overlay deletions. | Requires memory safety and reentrancy audits. |
| **Option B+A (Hybrid)** | Combining Option B and Option A addresses both major cost centers concurrently. | Maximum theoretical headroom. | High: Compounding algorithmic and memory architectural complexity. | High: Separate ablation runs required to isolate individual gains. |
| **Option C / D (Alternative / Pivot)** | Candidate reordering heuristics or scope re-evaluation. | Marginal or deferred. | Low. | Low. |

**Verdict:** `R4A_PROFILE_READY_FOR_P4B_REVIEW`. The final choice of path for Phase P4b will be made following independent review and explicit approval by the project coordinator.
