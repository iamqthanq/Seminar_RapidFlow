# RapidFlow-APX Research & Implementation Master Plan (Direction D)

**Document ID:** `docs/rapidflow_apx_research_plan.md`
**Date:** 2026-11-24
**Plan Version:** 1.2 (P4a accepted; evidence-driven P4b planning revision)
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D
**Associated Issue:** Issue #38 (`D-INT-04b / P4b`); P4a Issue #36 / PR #37 closed and merged
**Base Commit:** `7009daea477721c12eb52aa3d5db62f54f5b2ca4` (P4a merge into `QuangThang`)
**Current Phase:** P4b-0 planning / design; P4a technical acceptance complete (`R4A_PROFILE_READY_FOR_P4B_REVIEW`). This revision updates the plan only.
**Next Gate:** Coordinator review of the P4b mathematical design, architecture and experimental protocol before any optimization implementation.

---

## 1. Research Objective & Mandatory Native Integration

The primary objective of this seminar thesis is to develop and validate an extension of the original **RapidFlow (PVLDB 2022)** continuous subgraph matching engine to support **Approximate Continuous Subgraph Matching (APX CSM)** under a missing edge threshold on dynamic labeled graphs.

### 1.1. Core Mathematical Scope
1. **D-R (Primary Scope)**: Edge Recall scoring with S2 connectivity under the safety bound $0 \le k < \lambda(Q)$, where $k$ is the integer missing edge budget and $\lambda(Q)$ is the global edge connectivity of the query graph $Q$. In this domain, any mapping with $|R(f)| \le k$ is guaranteed to preserve spanning connectivity on the matched query edges ($Q[E_Q \setminus R(f)]$).
2. **D-G (Deferred General Scope)**: General threshold matching ($k \ge \lambda(Q)$) subject to explicit S2 connectivity checking. On dynamic graph updates, an event may be triggered by connectivity transitions even when $|R(f)| < k$. D-G is formally documented as an extension but is strictly deferred until D-R native integration is verified.

### 1.2. Non-Negotiable Integration Mandate
The final research product must be a **demonstrably integrated C++ system** derived from the original RapidFlow codebase (`baselines/rapidflow/source/` pinned at `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`):
- **Genuine Component Reuse**: Real compilation, linking, and runtime invocation of RapidFlow's Graph CSR representation, Global Views, memory-efficient sparse relation layouts, and streaming execution loops.
- **Integrated Pipeline**: An end-to-end native update-and-search pipeline operating on shared state.
- **Concrete Tuple Materialization**: Emitting explicit canonical ordered tuples $(f(u_1), \dots, f(u_n))$ sorted by original query vertex IDs, preserving all valid symmetric automorphisms without duplication.
- **Fail-Closed Verification**: 100% bit-for-bit equivalence against the independent Python M0 Ground Truth Oracle across all test corpus cases.

**Unacceptable Non-Solutions**:
- A standalone Python engine (such as DSTAR-02/03 prototypes).
- A standalone C++ rewrite uncoupled from RapidFlow.
- Merely reusing file formats or CLI flags.
- Linking `graph.cpp` while executing search on a separate, undocumented graph structure.
- Calling a count-only binary "RapidFlow-APX".

---

## 2. Master Implementation Phases (P0 through P6)

```text
+---------------------------------------------------------------------------------+
| Phase P0: Inventory & Contract Synchronization                                   |
| - Audit repository assets (KEEP / ADAPT / HISTORICAL / NEW)                      |
| - Synchronize coordination docs; establish working contract                      |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P1: Integration Audit & Native Probes [COMPLETED: Issue #28 / PR #29]     |
| - RapidFlow source line-by-line audit (Graph, GlobalView, StreamingEngine, etc.)|
| - Build & run native harness: Probes A–F (build, typed, view, NLF, rollback, K4)|
| - Gate Decision R1: READY_FOR_P2_DESIGN (Approved)                              |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P2: Native Engine Foundation & Tuple Materialization [COMPLETED: PR #31]  |
| - Authoritative graph state (immutable CSR snapshot + mutable overlay)          |
| - Transactional pre-validation and atomic rollback wrapper                      |
| - External-to-internal dense ID / typed-label codec                             |
| - Canonical tuple sink with exact step-by-step verification                     |
| - Gate Decision R2: READY_FOR_P3_ENGINES (Approved & Merged)                    |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P3: Integrated Native Bare M1 & M2 Engines [COMPLETED & VERIFIED]         |
| - Native M1-enum-Exact-k baseline on variant queries Q - D                      |
| - Native Bare M2-Event-k DFS (PRESENT/MISSING branching, deferred vertices)     |
| - Bit-for-bit M0 verification across 840 corpus cases and stream matrices       |
| - P3 integration and pre-P4 hardening precede the accepted P4a baseline         |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P4a: Frozen Bare M2 Profiling [COMPLETED: Issue #36 / PR #37 MERGED]      |
| - Preserve 180 historical slots / 90 paired comparisons and certified evidence  |
| - Identify search-core and neighbor-retrieval cost centers                      |
| - R4a: R4A_PROFILE_READY_FOR_P4B_REVIEW                                         |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P4b: Evidence-Driven Optional Optimizations [PLANNING: Issue #38]         |
| - P4b-0: mathematical design, architecture, and pre-registered protocol         |
| - P4b-1: isolated A (sound static / conservative residual Relaxed NLF)          |
| - P4b-2: isolated B (neighbor retrieval); preserve candidate semantics          |
| - P4b-3: paired evaluation; P4b-4: conditional A+B ablation                     |
| - R4b: retain, gate, defer, or reject optimizations; keep Bare M2 fallback      |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P5: Pilot, Scaling & NetFlow Workload Evaluation                          |
| - S4 pilot replicated natively under RLIMIT_AS 512 MiB and wall timeouts        |
| - Proposed scaling exploration to larger horizons (|S| >= 64, dense windows)   |
| - Fair separation: natural continuous stream vs dense sliding window sampling   |
| - Measurement of latency, memory RSS, work counters, and throughput             |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P6: Comparative Evaluation, Defense Deliverables & Final Archival         |
| - Synthesis of empirical results: M1-enum vs bare M2 vs optimized M2            |
| - Formal defense report, provenance ledgers, and reproduction scripts           |
+---------------------------------------------------------------------------------+
```

---

## 3. Detailed Phase Specifications

### 3.1. Phase P0: Inventory & Contract Synchronization
- **Scope**: Re-audit repository code and artifacts; create `docs/rapidflow_apx_reuse_inventory.md`; align coordination docs (`README.md`, `AI_PROJECT_HANDOFF.md`, `CODEX_HANDOFF.md`, `dstar_implementation_plan.md`); establish `docs/rapidflow_apx_contract.md`.
- **Deliverables**: Inventory matrix, updated handoff documents, new working contract.
- **Status**: Completed in Issue #28.

### 3.2. Phase P1: RapidFlow Source Audit & Native Validation Probes
- **Scope**: Deep line-by-line audit of RapidFlow source code (`graph.cpp`, `global_edge_view.h`, `global_view_manager.cpp`, `streaming_engine.cpp`, `search_engine.cpp`, `order_manager.cpp`). Construct minimal native C++ audit harness (`tests/native/rapidflow_apx_audit/`).
- **Probes**:
  - **Probe A (Provenance)**: Compile and link `graph.cpp`; invoke runtime accessors; record binary SHA-256 and compiler flags.
  - **Probe B (Typed Boundary)**: Verify codec round-trip for sparse IDs, negative IDs, IDs $> 2^{32}-1$, and typed labels ($0 \ne \text{"0"}$).
  - **Probe C (Mutable GlobalEdgeView)**: Verify sorted neighbor insertion, deletion, duplicate/absent return values, and container label absence.
  - **Probe D (Exact NLF Incompatibility)**: Evaluate Diamond query ($k=1, \lambda=2$) against data graph missing edge $BC$; prove via genuine runtime invocation of `GlobalViewManager` that valid APX identity match is pruned by RapidFlow's exact NLF check.
  - **Probe E (Snapshot Order & Rollback)**: Verify INSERT search on $G_{\text{after}}$, DELETE search on $G_{\text{before}}$, and mid-stream rollback upon injected failure on adapter structures.
  - **Probe F (Count/Tuple Boundary)**: Evaluate $K_4$ query on 4-clique fixture; M0 enumerates 24 distinct ordered tuples; inspect RapidFlow source confirming leaf count shortcut (`search_engine.cpp:161`) and scalar factor multiplication (`streaming_engine.cpp:181`), and establish the tuple materialization architecture without overcounting.
- **Review Gate R1**: Publish `docs/research_decisions/D-R1-native-integration.md` with verdict (`READY_FOR_P2_DESIGN (PENDING_USER_PR_CLOSURE)`, `NEEDS_DESIGN_ADJUSTMENT`, or `BLOCKED_INTEGRATION`).
- **Status**: Active in Issue #28.

### 3.3. Phase P2: Native Engine Foundation & Tuple Materialization
- **Scope**: Build the native core foundation (`extensions/rapidflow_apx/`):
  1. Authoritative Graph State: Hybrid structure combining RapidFlow immutable CSR (for static base) and `GlobalEdgeView` mutable overlay for streaming updates.
  2. Transactional Update Pipeline: Pre-validation, forward/reverse view updates, search invocation, commit, and atomic rollback on error.
  3. External Typed Codec: Two-way bijection mapping arbitrary user IDs and typed labels to dense internal integers.
  4. Tuple Sink: Unroll leaf-level candidate loops in backtracking search to materialize canonical tuples $(f(u_1), \dots, f(u_n))$; for bare M2, DFS directly produces distinct ordered tuples without post-hoc expansion; if reduced representative search is pursued, implement representative orbit expansion to avoid the 576-tuple K4 overcounting defect; stream canonical tuples to consumer.
- **Exit Criteria**: Native micro-matcher enumerates 100% of tuples on small fixtures without count shortcuts.

### 3.4. Phase P3: Integrated Native Bare M1 & M2 Engines
- **Scope**: Implement native execution kernels:
  1. Native M1-enum-Exact-k: Generate $\binom{m-1}{k}$ variants $Q - D$; execute anchored exact matching using RapidFlow relation structures; filter by $R(f) == D$.
  2. Native Bare M2-Event-k: Single-pass anchored DFS with PRESENT and MISSING branches; unmapped vertex deferral; leaf condition $|R| == k$.
- **Exit Criteria**: 100% agreement with M0 Oracle on 840 corpus cases and stream regression matrix.

### 3.5. Phase P4: Evidence-Driven, Safe & Optional Approximate Optimizations

P4 is split into P4a (diagnose and freeze the baseline) and P4b (design, evaluate, and select optimizations). The original objectives of sound defect bounds, Relaxed NLF, optional ordering, and Bare M2 fallback remain; the execution order and the additional neighbor-retrieval hypothesis are grounded in P4a. This revision is a planning document, not evidence that P4b has been implemented or approved for execution.

#### 3.5.1. P4a — Accepted Frozen Profiling Baseline (R4a)

- **Status:** Issue #36 closed; PR #37 merged into `QuangThang` at `7009daea477721c12eb52aa3d5db62f54f5b2ca4`. Reviewed feature HEAD: `f8f04ece29788041bca97156536e68b5980137fb` (Round 10.1). No further P4a code-remediation round is required by the accepted review.
- **Sources of truth:** `docs/rapidflow_apx_p4a_protocol.md`, `docs/rapidflow_apx_p4a_profile_report.md`, `docs/research_decisions/D-R4a-native-profiling.md`, `docs/evidence/p4a_evidence_manifest.json`, and the registered historical artifacts.
- **Frozen evidence:** 30 workloads, 3 repetitions, 2 engines, 180 slots, 90 valid pairs, zero rejected or missing pairs; `CANONICAL_PASS`. Historical execution commit `cf97a7840dff0c0f2bf791834263f3911545110c`; raw SHA-256 `34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5`. Do not overwrite or rerun the historical measurements as a P4b prerequisite.
- **Observed paired medians (M1 / Bare M2):** Search 2.1269x (84 computable pairs; 6 zero-duration search pairs excluded from that ratio only); Native Step 1.8021x, Streaming Loop 1.1136x, Outer Wall 1.0414x (90 pairs each). Candidate evaluations 455,148 vs 245,055 (46.16% relative reduction), with 37,152 events and 828 steps per engine. Correctness rests on oracle verification, not equality of total counts alone.
- **Cost centers:** Bare M2 search-core median 77.2165% and neighbor-retrieval median 22.7835%, both normalized by Search time. Native step sum median 7.35% and input parsing median 45.81% are normalized by pre-output wall time, not Search.
- **Interpretation:** These are directional pilot observations, not statistically established general end-to-end speedup, scalability, or novelty. Three timing scopes have minima below 1x. Repetitions of the same workload are not independent workloads. Observer overhead was assessed over 16 paired trials on four workloads (outer-wall median -0.789%, mean +0.4338%, SD 10.396%); it does not rule out per-call micro-timer overhead. P4b must separate main performance measurement from attribution.
- **Acceptance boundary:** Independent review verified focused 73/73, fresh checkout 59 PASS + 14 justified historical skips, native 41/41 and 106/106 mutations detected, and 7/7 registered evidence artifacts. Full 753/753 repository regression at the final feature HEAD was reported by Antigravity, not independently rerun in that review. Multi-artifact publication provides per-file atomic replacement and compensating rollback for handled exceptions, with recovery backup retention on inconsistent rollback; it is not crash-proof multi-file ACID.

#### 3.5.2. P4b — Research question, scope and non-negotiable invariants

**Question:** Which optional optimization(s) improve the native Bare M2 cost while preserving complete D-R event enumeration, and on which workloads is the benefit real?

- **Unchanged mathematical contract:** `docs/rapidflow_apx_contract.md` remains authoritative: simple undirected connected Q, fixed data vertex universe and immutable vertex labels, typed vertex/edge labels, injective non-induced embeddings, Edge Recall, $0 \le k < \lambda(Q)$, single-edge INSERT/DELETE. Search the after snapshot for INSERT and before snapshot for DELETE, anchored in both valid orientations, with event leaf $|R(f)| = k$. A mismatched edge label consumes missing-edge budget. No D-G extension, RELABEL shortcut, vertex insert/delete, count-only substitution, loss of symmetric mappings, or duplicate tuples.
- **Native integration:** Preserve the pinned RapidFlow source and actual CSR / GlobalEdgeView reuse. APX search kernels, tuple materialization, typed codec and transaction adapters are APX code; do not claim that the original exact search kernel is being reused unchanged.
- **Controls:** Frozen Bare M2 is the main P4b comparison and always-available fallback; M0 remains the independent oracle and M1 the research baseline. Default flags must preserve Bare M2 behavior until R4b approval. No rewriting historical P4a evidence or comparing new runtime directly to historical runtime from a different execution identity.
- **Priority:** Option A is the primary algorithmic research direction; Option B is independent systems optimization. Option C (ordering) and stronger multi-vertex dynamic defect bounds are deferred unless justified. Option D (retain Bare M2) is a valid outcome.

#### 3.5.3. P4b-0 — Design gate (before implementation)

Create and review `docs/rapidflow_apx_p4b_design.md` with actual source references, mathematical proof obligations, counter definitions, data structure ownership/lifetime, transaction and failure behavior, configuration flags, bounded workload registry, paired measurement protocol and pre-registered decision criteria. Verify live source and upstream sortedness; do not assume it. Evaluate index memory cost and resource limits, including large G and varied Q, without promising unverified scale.

The coordinator must approve the design before P4b-1 or P4b-2. The current Issue #38 documentation update does not itself authorize code implementation or benchmark execution.

#### 3.5.4. P4b-1 — Option A: isolated sound Relaxed NLF

Use typed incident signatures $\sigma=(\ell_E,\ell_V)$ (edge label and neighboring vertex label). Let $N_Q(u,\sigma)$ and $N_G(v,\sigma)$ be incident signature counts for query vertex $u$ and data vertex $v$ in the authoritative search snapshot.

1. **Static necessary condition:** $\delta_{static}(u,v)=\sum_\sigma \max(0,N_Q(u,\sigma)-N_G(v,\sigma))$. Prune only if $\delta_{static}>k$. Injectivity implies this is a lower bound on missing incident query edges, not a sufficient matching condition.
2. **Conservative residual defect bound:** At a mapped-vertex state after resolving edges with both endpoints mapped, let $M_{cur}$ be committed MISSING edges and $m_{cur}=|M_{cur}|$. Let $I_Q^{miss}(u,\sigma)$ be the committed MISSING incident edges of the given signature (including deferred MISSING commitments). A candidate local bound is
   $$\delta_{res}^{cons}(u,f(u))=\sum_\sigma \max(0,N_Q(u,\sigma)-|I_Q^{miss}(u,\sigma)|-N_G(f(u),\sigma)).$$
   Prove $m_{cur}+\delta_{res}^{cons}(u,f(u))\le |R(f)|$ for every valid completion before use. Prune when the bound exceeds $k$. Do not add static deficits to already counted missing edges, or sum overlapping per-vertex deficits. Any later multi-vertex residual aggregation requires a separate disjoint-edge proof. No full free-neighbor scan inside DFS for the minimal variant.
3. **Index proposal:** Query-relevant signature IDs and sparse per-query-vertex requirements; a snapshot-consistent frequency index with dense or sparse representation selected by memory analysis. Update both endpoints consistently with the INSERT-after / DELETE-before search contract and restore exactly on transaction abort. Indexed lookup may be O(1); checking a vertex is O(number of relevant signatures), plus any missing-status accounting. Do not claim the entire filter is O(1) or ignore initialization / maintenance overhead.
4. **Isolation and instrumentation:** Proposed `--nlf-mode none|static|residual` (none = Bare M2). Retain original candidate_evaluations, dfs_states and work-unit definitions; add versioned NLF checks/prunes, index maintenance, and overhead metrics without manufacturing gains by moving counters. Evaluate static and residual components explicitly if the design requires.
5. **Proof and tests:** Include exact-NLF false negatives at $k>0$, double-counted MISSING edge commitments, deferred vertices, already-used neighbors / PRESENT accounting, int 0 versus string "0", wrong edge labels, both update directions and rollback/fault/work-budget paths. Preserve explicit ordered tuples and validate against M0 and Bare M2. Reject any unsound variant.

#### 3.5.5. P4b-2 — Option B: isolated neighbor retrieval optimization

Target `ApxAuthoritativeGraph::get_neighbors()` and the Bare M2 PRESENT branch, which currently materialize a vector, scan CSR with tombstone/overlay checks, append dynamic neighbors, sort and deduplicate. The measured 22.78% includes this combined cost and potential profiling overhead, not just copying.

1. Proposed `get_neighbors_into(u, out)` with reusable scratch vectors indexed by **actual recursive DFS depth**, not assigned vertex count. Pre-size the outer pool and prove the maximum depth accounting for MISSING deferral; preserve parent buffer lifetime and reentrancy.
2. Add an empty-overlay/tombstone fast path only when logically valid. Use sorted two-pointer merge only after proving actual upstream CSR and overlay ordering/uniqueness contracts; otherwise retain a correct sort/unique fallback. Preserve identical sorted unique neighbor sets including deletion, reinsertion, relabel-via-delete/insert and rollback. Do not expose raw CSR spans as the general dynamic adjacency.
3. Consider a constant-time degree index **only if** profiling or call-site audit shows material benefit; its memory and transactional rollback costs must be evaluated. Do not introduce snapshot caches or new persistent state without evidence.
4. Proposed `--neighbor-opt off|on` (off = Bare M2), independently switchable from NLF. For minimal B, preserve output, candidate traversal order and logical search counters on completed comparable runs; add separate retrieval/degree/overlay/tombstone and allocation attribution counters as justified.
5. Test base-edge deletion, overlay-only edges, delete/reinsert same or new label, duplicate adjacency, parent/child buffers including deferred MISSING recursion, transaction abort and continue-on-error, and equivalent logical work-budget behavior. Verify memory safety (sanitizer builds where supported) and differential neighbor-set equality. No upstream edits.

#### 3.5.6. P4b-3 — Isolated comparative experiment (pre-register before measurement)

- **Comparators:** Bare M2 versus A-only and B-only on the same input, machine, compiler/flags and build identity where practicable; M1 for research comparison, M0 for correctness. Test both optimizations independently, even when implemented on one feature branch.
- **Correctness gate:** Reuse the verified P3 hand/corpus/stream suites (25 hand cases, 840 corpus cases, 56 streams / 672 steps; verify actual registry), add A/B adversarial and seeded differential cases, and check tuple sets per update and raw duplicates. Require zero missing, extra, or duplicate ordered tuples for all completed correctness cases. Explicitly record oracle/resource failures rather than treating them as passes. Keep the frozen Bare M2 fallback tested.
- **Workloads:** Preserve the 30 P4a workloads as a new-run continuity set. Pre-register a separate bounded discrimination suite with mixed and uniform typed labels, diverse degree/density and hub/churn patterns, event and zero-event streams, and varied Q topology, Q size and valid k. Proposed exploration: G of 16/32/64 vertices, Q beyond Diamond/K4 where feasible, streams 32–100 updates. These are proposed pilot scales, not fixed supported limits or P5 NetFlow results. Aim to reduce harness dominance through longer/larger workloads; do not exclude results because native time fails a desired share such as 60%.
- **Measurement:** Main performance comparisons use PROFILE_OFF with a consistent low-overhead end-to-end/stream timing boundary; separate PROFILE_ON runs for native stage attribution and counters. Do not pretend disabled micro-timers supply Search/Step measurements. If those scopes are primary outcomes, pre-register an appropriate equivalent low-overhead measurement seam and measure its observer effect. Balance/randomize paired variant order with a fixed seed, warm up consistently, choose repeat count from pilot variance (initial proposal 5 repeats per workload), record environment, hashes, flags, statuses, wall/CPU time, peak RSS, counts and output volume. Measure index construction and maintenance, not just DFS time.
- **Statistics:** Report absolute times and paired speedups by scope; aggregate repeats within workload before across-workload summaries and workload-level confidence intervals (e.g. clustered bootstrap). Report relevant strata and all attempted cases; avoid pseudo-replication. Report TIMEOUT, OOM_CENSORED and WORK_BUDGET_EXCEEDED, and censored proportions, not zeros/infinities or silently discarded trials. Preserve initial bounded budget (60 s, 512 MiB, 500,000 canonical work units for P4a continuity unless a separately registered and approved P4b protocol justifies another limit). Resource guards are not a proof of maximum graph size.
- **Decision thresholds:** Final practical effect sizes, uncertainty, regression and memory tolerances must be locked in the design before running the experiment. Suggested starting criteria for review, not established results: target-stratum median speedup at least 1.15x with workload-level 95% interval above 1.0; report regressions beyond 5% and memory overhead above 20%; distinguish PARITY from uncertain results, and verify absolute memory ceiling. A query-dependent opt-in can be retained if its selector is justified and tested. No requirement that an optimization must produce a positive result.
- **Amdahl interpretation:** 77.22% and 22.78% are measured Search-time shares, not promised gains. For a hypothetical fixed share $p=0.227835$, eliminating that component gives $1/(1-p)\approx1.295$ in that same scope. Do not apply median shares across scopes or treat the illustration as a universal upper bound. A can reduce B's call volume; combined effects are non-additive.

#### 3.5.7. P4b-4 — Conditional ablation, alternatives and stop rule

Only after A and B pass their individual correctness gates and at least one has a meaningful measured benefit, decide whether a combined variant is worth testing. If authorized, compare Bare, A-only, B-only and A+B on the same pre-registered inputs with independent flags and full combined correctness gate. Do not assume A+B is fastest. Prefer a simpler variant if performance is comparable.

Dynamic frontier / candidate ordering (Option C) and stronger $\delta'$ bounds remain optional alternatives; pursue only with a documented hypothesis and coordinator approval if A/B fail or the evidence warrants them. Re-check completeness, duplication, counters and budget effects for any changed traversal order. Option D keeps Bare M2. Time-box P4b to the two minimal isolated variants and one justified combined ablation; no unbounded micro-optimization rounds.

#### 3.5.8. R4b — Acceptance and P5 handoff

- Record the design, configuration and code provenance, full correctness outcomes, actual benchmark ledger and reproducible commands, limitations, and a decision in proposed `docs/research_decisions/D-R4b-optimization-selection.md`. Keep historical artifacts separate from new P4b results.
- Classify each variant as ADVANTAGE_OBSERVED, QUERY_DEPENDENT, PARITY, INCONCLUSIVE, NO_GO or UNSOUND (correctness failure). An unsound variant is never enabled; it cannot support a performance claim. Use a gated/fallback configuration for negative or workload-dependent results, rather than silently requiring success.
- Select a justified configuration for P5 (Bare M2, A, B, or A+B) and retain reproducible controls. Require independent review and explicit coordinator approval before P5. P4b does not establish general scaling, real-world NetFlow advantage, or academic novelty. P5 and P6 retain their existing scope.

### 3.6. Phase P5: Pilot, Scaling & NetFlow Workload Evaluation
- **Scope**: Replicate the S4 pilot natively and scale to representative workloads:
  1. Bounded Native Pilot: Execute paired runs under RLIMIT_AS 512 MiB and wall-clock timeout.
  2. Scaling Exploration: Proposed exploration target $|S| \ge 64$ for stress-testing scalability (explicitly defined as a proposed exploratory stress-testing target, NOT an established mathematical minimum or thesis validity gate).
  3. Workload Separation: Natural continuous stream (event-by-event processing) is explicitly evaluated separately from dense sliding window snapshot sampling.
  4. Fair Metric Evaluation & Novelty Stance: Measure actual CPU runtime, memory RSS, physical operation counters, and cache locality. Scientific novelty is treated as an unverified working hypothesis requiring formal comparative analysis against established literature (e.g., dynamic continuous subgraph matching, approximate subgraph isomorphism, and fault-tolerant pattern matching). No claim of novelty is asserted as an established fact prior to peer evaluation.
  5. **Empirical Evaluation States**:
     Empirical benchmarks must be classified under one of five explicit evaluation states:
     - `ADVANTAGE_OBSERVED/PROVEN`: Statistically significant runtime speedup and/or memory RSS reduction with verified 100% bit-for-bit output equivalence against M0.
     - `PARITY`: Performance differences lie within statistical noise / margin of error ($\pm 5\%$).
     - `QUERY_DEPENDENT`: Performance advantageous on specific query topologies or edge densities, but neutral or disadvantageous on others.
     - `INCONCLUSIVE`: High variance across runs, unresolved resource contention, or inability to establish a clear trend across workloads.
     - `NO_GO`: Fundamental algorithmic overhead, severe memory blowup, or scaling bottlenecks rendering the method uncompetitive or impractical.
  6. **Rules for Censoring & Timeouts**:
     - Any run terminated by wall-clock timeout or memory ceiling must be explicitly flagged as `TIMEOUT` or `OOM_CENSORED`, along with the exact cutoff threshold.
     - Censored runs must NEVER be silently discarded from statistical summaries or replaced with arbitrary zeroes/infinities.
     - Both censored proportions and bounded quantile statistics must be reported transparently.
- **Exit Criteria**: Reproducible benchmark report with audited provenance and transparent statistical bounds.

### 3.7. Phase P6: Comparative Evaluation & Deliverables
- **Scope**: Final thesis analysis, documentation, and archival:
  1. Comparative assessment: M1-enum vs bare M2 vs optimized M2 across the 5 evaluation states.
  2. Complete thesis artifact package: code, test suites, manifests, and documentation.
  3. Formal defense slides and summary.

---

## 4. Review Gates Framework (R0 through R6)

| Gate | Associated Phase | Core Question | Evidence Required | Verdict Options |
| :---: | :---: | :--- | :--- | :---: |
| **R0** | P0 | Is the problem contract and inventory locked? | `rapidflow_apx_contract.md`, `reuse_inventory.md` | `APPROVED` / `REVISE` |
| **R1** | P1 | Is native RapidFlow integration feasible and architecturally sound? | Probes A–F execution, integration map, source audit | `READY_FOR_P2_DESIGN (PENDING_USER_PR_CLOSURE)` / `NEEDS_ADJUSTMENT` / `BLOCKED` |
| **R2** | P2 | Can native components materialize tuples and handle rollback? | Foundation unit tests, tuple sink verification, rollback tests | `READY_FOR_ENGINES` / `FIX_FOUNDATION` |
| **R3** | P3 | Do native M1 and M2 match M0 Oracle 100% on corpus? | 840 corpus verification, 56-stream matrix | `ENGINES_VERIFIED` / `DISCREPANCY_FOUND` |
| **R4a** | P4a | Is the frozen Bare M2 profiling baseline valid and useful for optimization hypotheses? | Canonical evidence, 180 slots / 90 pairs, four timing scopes, independent review | `R4A_PROFILE_READY_FOR_P4B_REVIEW` (accepted) |
| **R4b** | P4b | Which optional optimizations are sound and useful, if any? | Approved design, isolated A/B correctness and paired performance evidence, conditional ablation, explicit fallback and decision record | `PRUNING_VALIDATED` / `PRUNING_UNSOUND` for soundness; ADVANTAGE_OBSERVED / QUERY_DEPENDENT / PARITY / INCONCLUSIVE / NO_GO for empirical selection |
| **R5** | P5 | Does M2 outperform M1 on real-world NetFlow benchmarks? | Microbenchmarks, RSS, latency distributions, NetFlow logs | `ADVANTAGE_PROVEN` / `PARITY` / `QUERY_DEPENDENT` / `INCONCLUSIVE` / `NO_GO` |
| **R6** | P6 | Are all deliverables reproducible and ready for thesis defense? | Independent verification script, clean build, PR merge | `READY_FOR_DEFENSE` / `REVISE` |

---

## 5. Governance and Safety Protocols

1. **Repository Constraints**: Work exclusively inside `iamqthanq/Seminar_RapidFlow` on branch `feature/rfNN-topic` forked from `QuangThang`.
2. **Pristine Upstream**: `baselines/rapidflow/source/` must remain unmodified at pinned commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`.
3. **Draft PR Discipline**: Exactly one feature branch and one Draft PR per approved issue; pushes executed solely via `bash scripts/push_issue_branch.sh`; PRs created via `bash scripts/open_issue_pr.sh <ISSUE>`.
4. **No Premature Claims**: No claims of speedup, scalability, or novelty until Phase P5 benchmarks are independently reviewed and certified.
