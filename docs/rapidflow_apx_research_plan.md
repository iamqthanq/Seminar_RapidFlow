# RapidFlow-APX Research & Implementation Master Plan (Direction D)

**Document ID:** `docs/rapidflow_apx_research_plan.md`
**Date:** 2026-11-18
**Plan Version:** 1.1 (Realigned with Direction D Master Protocol & Remediation F01–F06)
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D
**Associated Issue:** Issue #32 (`D-INT-03 / P3`; previously Issue #28 `P0–P1`, Issue #30 `P2` merged)
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)
**Current Phase:** Phase P3: Native M1-enum and bare M2 integration (Completed & Verified; PR #33 Ready for Independent Review; Issue #32)
**Next Gate:** Independent Final Review & Acceptance of Draft PR #33 (Issue #32)

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
| Phase P3: Integrated Native Bare M1 & M2 Engines [COMPLETED & VERIFIED: Issue #32 / PR #33] |
| - Native M1-enum-Exact-k baseline on variant queries Q - D                      |
| - Native Bare M2-Event-k DFS (PRESENT/MISSING branching, deferred vertices)     |
| - Bit-for-bit M0 verification across 840 corpus cases and stream matrices        |
| - Gate Decision R3: Design review, corpus verification, and next-gate verdict   |
|   (Verdict: ENGINES_VERIFIED_READY_FOR_P4_DESIGN, Pending Independent Review)  |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase P4: Safe & Optional Approximate Pruning Optimizations                     |
| - Partial mapping defect bound delta'(f_p, free neighbors)                      |
| - Relaxed approximate NLF filter (optional: skip if overhead >= savings)        |
| - Dynamic candidate ordering for approximate branch reduction                   |
| - Continuous M0 invariant checking across all optimizations                     |
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

### 3.5. Phase P4: Safe & Optional Approximate Pruning
- **Scope**: Develop sound pruning strategies for approximate matching:
  1. Dynamic Defect Bound $\delta'$: Track committed missing edges $|R_p|$ and unassigned query edges; prune when $|R_p| + \text{unavoidable} > k$.
  2. Relaxed NLF: Construct neighbor label frequency multisets with tolerance $k$, avoiding false rejections.
  3. Dynamic Candidate Ordering: Select next frontier edge based on candidate set cardinality.
  4. **Strict Optional Pruning Principle & Bare M2 Fallback**: Candidate pruning (NLF, $\delta'$) is strictly an optional optimization. If the CPU and memory overhead of computing approximate NLF or maintaining dynamic candidate lists exceeds the backtracking search reduction, the engine must safely and unconditionally fall back to bare M2 without filtering.
- **Exit Criteria**: Pruned M2 produces identical tuple output to bare M2 while demonstrating positive net work reduction when pruning is enabled.

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
| **R4** | P4 | Are NLF/$\delta'$ pruning optimizations sound and effective? | Equivalence verification, operation counter reduction | `PRUNING_VALIDATED` / `PRUNING_UNSOUND` |
| **R5** | P5 | Does M2 outperform M1 on real-world NetFlow benchmarks? | Microbenchmarks, RSS, latency distributions, NetFlow logs | `ADVANTAGE_PROVEN` / `PARITY` / `QUERY_DEPENDENT` / `INCONCLUSIVE` / `NO_GO` |
| **R6** | P6 | Are all deliverables reproducible and ready for thesis defense? | Independent verification script, clean build, PR merge | `READY_FOR_DEFENSE` / `REVISE` |

---

## 5. Governance and Safety Protocols

1. **Repository Constraints**: Work exclusively inside `iamqthanq/Seminar_RapidFlow` on branch `feature/rfNN-topic` forked from `QuangThang`.
2. **Pristine Upstream**: `baselines/rapidflow/source/` must remain unmodified at pinned commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`.
3. **Draft PR Discipline**: Exactly one feature branch and one Draft PR per approved issue; pushes executed solely via `bash scripts/push_issue_branch.sh`; PRs created via `bash scripts/open_issue_pr.sh <ISSUE>`.
4. **No Premature Claims**: No claims of speedup, scalability, or novelty until Phase P5 benchmarks are independently reviewed and certified.
