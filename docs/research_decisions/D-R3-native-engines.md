# Research Decision Record: D-R3 — Native Event Engines Integration & Verification

**Document ID:** `docs/research_decisions/D-R3-native-engines.md`
**Milestone:** D-INT-03 / Phase P3
**Parent Issue:** Issue #32
**Target Integration Branch:** `QuangThang`
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Status:** VERIFIED (Phase P3 Remediation Complete — Pending Independent Review)

---

## 1. Executive Summary & Review Scope

Phase P3 (Issue #32) realizes the integration of native event search engines on top of the RapidFlow-APX foundation validated in Phase P2.

The two core engines implemented in `extensions/rapidflow_apx`:
1. `ApxM1EnumEngine`: The native baseline algorithm performing exact subgraph matching across variant queries $Q - D$ where $|D| = k$.
2. `ApxM2EventEngine`: The bare M2 direct depth-first search engine branching on PRESENT vs MISSING edges, deferring unmapped vertices across cuts, and enforcing $|R(f)| = k$ at event leaves.

Both engines interact strictly through the shared native foundation (`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, and `ApxTupleSink`), producing explicit canonical ordered tuples $(f(u_1), \dots, f(u_n))$ without relying on Python prototypes, monolithic upstream counting shortcuts, or scalar automorphism factors.

---

## 2. Review S0 / R3-A: Contract & Architectural Alignment

Prior to implementation, the research team re-examined the project contract, master research plan, and integration map:
1. **Contract Synchronization**: `docs/rapidflow_apx_contract.md`, `docs/rapidflow_apx_research_plan.md`, and `docs/rapidflow_apx_integration_map.md` have been updated to reflect Phase P2 completion (PR #31 merged into `QuangThang`, Issue #30 closed) and Phase P3 active status. Historical provenance and earlier SHA references are preserved intact.
2. **Strict Domain D-R Enforcement**:
   - $0 \le k < \lambda(Q)$ where $\lambda(Q)$ is computed natively using exact Edmonds-Karp BFS minimum cut algorithms on $Q$.
   - Input cases with $k \ge \lambda(Q)$ (Domain D-G) or disconnected queries are rejected fail-closed.
3. **Event Boundary Correctness**:
   - Single-edge update events occur if and only if the updated edge is matched to an anchor query edge and $|R(f)| = k$ on the active snapshot ($G_{\text{after}}$ for INSERT, $G_{\text{before}}$ for DELETE).
   - Uniqueness theorem guarantees zero duplicate raw emissions across distinct anchors.
4. **Independent M0 Oracle Verification**:
   - The native engines contain zero dependencies on Python code or Oracle logic.
   - Verification is performed externally by the test runner comparing native output bit-for-bit against the independent Ground Truth Oracle across 840 corpus cases and 56 dynamic streams.

---

## 3. Decisions Table (KEEP / ADAPT / NEW / DEFER / STOP)

| Component / Mechanism | Action | Rationale |
| :--- | :---: | :--- |
| `ApxAuthoritativeGraph` | **KEEP** | Hybrid CSR + `GlobalEdgeView` overlay provides authoritative graph ground truth and fast neighbor iteration. |
| `ApxTypedCodec` | **KEEP** | Provides 64-bit ID decoding, typed label interning, and ascending query canonical ordering. |
| `ApxTransactionManager` | **KEEP** | Enforces ACID step execution, reentrancy protection, and zero-allocation rollback. |
| `ApxTupleSink` | **KEEP** | Enforces canonical tuple staging, injectivity checking, and duplicate rejection. |
| `ApxQueryPlan` | **NEW** | Natively computes $\lambda(Q)$, query edge indexing, compatibility tables, and M1 variant plans. |
| `ApxM1EnumEngine` | **NEW** | Natively implements M1-enum-Exact-$k$ baseline event enumeration. |
| `ApxM2EventEngine` | **NEW** | Natively implements bare M2-Event-$k$ direct DFS with deferred vertex commitments. |
| `ApxStreamingEngine` | **NEW** | Coordinates transaction execution, relevancy filtering, and publication. |
| `rapidflow_apx_native` CLI | **NEW** | Provides standalone execution harness with resource caps and fail-closed error handling. |
| Approximate NLF / $\delta'$ | **DEFER** | Deferred to Phase P4. |
| Large NetFlow Benchmarks | **DEFER** | Deferred to Phase P5. |
| Automorphism Scalar Multiplier | **STOP** | Disallow multiplying counts by automorphism group size; all valid embeddings must be explicitly emitted. |
| Python Prototypes in Production | **STOP** | Production native engine must be pure C++ with no runtime call to Python. |

---

## 4. Checkpoints & Review Milestones

### 4.1. S0 / R3-A: Contract & Architectural Alignment
- Synchronized all master coordination docs (`rapidflow_apx_contract.md`, `rapidflow_apx_research_plan.md`, `rapidflow_apx_integration_map.md`).
- Authored design specification `docs/rapidflow_apx_native_engines_design.md`.

### 4.2. R3-B: First Witness Validation Review
Both native C++ engines (`ApxM1EnumEngine` and `ApxM2EventEngine`) compiled cleanly and were evaluated against the independent Python M0 Ground Truth Oracle on the primary target witnesses:
1. **$K_4$ ($k=0$) Full Symmetry Witness**:
   - Both engines emitted exactly 24 ENTERED tuples on insertion of $(2, 3)$ and exactly 24 EXITED tuples on deletion of $(2, 3)$.
   - All 24 tuples matched the M0 Oracle bit-for-bit; zero duplicate emissions (`raw_emissions == unique_emissions == committed_emissions == 48`).
2. **$\text{Diamond}$ ($k=1$) Event Leaf Witness**:
   - Evaluated under the strict event boundary lemma $|R(f)| = k = 1$.
   - Both native engines matched the M0 Oracle bit-for-bit with exactly 8 ENTERED tuples.
   - M1 executed 40 variants with 15 raw exact results and 7 missing set rejections ($R(f) \ne D$).
   - M2 executed 54 edge decisions, 54 missing branches, and 23 commitment checks with zero commitment rejections.
3. **$C_4$ ($k=1$) Deferred Vertex Witness**:
   - When the search branch chose MISSING on frontier edge $(1, 2)$, vertex 2 was deferred and mapped later via $(3, 2)$.
   - Both native engines matched the M0 Oracle bit-for-bit with exactly 8 ENTERED tuples.

**Reviewer Verdict at R3-B (KEEP / CHANGE / DEFER / STOP)**:
- **KEEP**: The shared foundation APIs, zero-allocation rollback, and frontier indexing are proven sound and require no changes.
- **KEEP**: Work counters properly record attempted work (charging before candidate loops and edge decisions).
- **KEEP**: Both native paths operate strictly in C++ without any runtime call to Python.
- **PROCEED**: Proceed to full 840 corpus execution and 56-stream stateful matrix verification.

### 4.3. R3-C: Final Gate Review

The canonical clean build and complete verification runner were executed from commit `a7267e90c60723447e7acfc4613be2435b558c1b`:
- **Native Binary**: `tests/native/rapidflow_apx_engines/build/rapidflow_apx_native` (SHA-256: `1983466500e00570a15dcb8f28b8d1bdee51f1cb5657dd3f21413d2211593654`).
- **Git Provenance**: Repository commit `a7267e90c60723447e7acfc4613be2435b558c1b` (`CLEAN`), Upstream pinned `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (`PRISTINE`).
- **Hand Cases (24 hand + 1 adversarial witness = 25 total)**: 25/25 PASS (including fail-closed checks for $k \ge \lambda(Q)$, invalid transactions, disconnected queries, empty graphs).
- **840-Case Correctness Corpus (Real M0 Recomputed)**:
  - 840/840 cases M1 agreed bit-for-bit with independent M0 Oracle.
  - 840/840 cases M2 agreed bit-for-bit with independent M0 Oracle.
  - 8,002 total truth events (330 cases with events, 239 cases with $k \ge 1$).
  - Zero duplicate emissions across all cases (`m1_zero_duplicates`: 840, `m2_zero_duplicates`: 840).
- **56-Stream Stateful Dynamic Matrix (672 Updates)**:
  - 56 stream configurations across diverse query topologies ($K_3, K_4, C_4, C_5, \text{Diamond}, \text{House}, \text{Tree}_5, \text{Ladder}_6$), edge connectivity thresholds $k \in \{0, 1\}$, and stream rates.
  - 672/672 update steps matched M0 Oracle on M1; 672/672 on M2 (3,024 ENTERED events, 1,780 EXITED events).
  - Active-set reconstruction matched M0 snapshots 672/672 on both engines.
- **Repository Regression Suite**: Ran `python3 -m unittest discover tests` — 528 tests passed with 0 failures and 0 errors in 119.184s.

### 4.4. Round 1 Review Remediation Summary (P3-F01–P3-F09)

Following independent review of PR #33, nine targeted remediation items were identified and addressed:
1. **P3-F01 (BLOCKER — Work Budget Reset & Isolation)**: Enforced per-step budget reset (`step_counters` vs `cumulative_counters`), explicit boundary semantics (`max_work=0` rejects immediately, `max_work=1` halts before traversal), threshold preemption, and diagnostic counter retention on budget exhaustion.
2. **P3-F02 (BLOCKER — Strict Operation Parsing)**: Rejected arbitrary non-standard operation strings fail-closed with `std::invalid_argument` (accepting only `+`, `-`, `INSERT`, `DELETE`, `INS`, `DEL`).
3. **P3-F03 (BLOCKER — Threshold $k$ Range Validation)**: Strict integer type checking rejecting negative $k$, non-integers, and out-of-range values before the D-R guard.
4. **P3-F04 (MAJOR — Boolean Label Rejection)**: Disallowed implicit string coercion of JSON boolean labels (`true`/`false`), enforcing strict integer or string types.
5. **P3-F05 (MAJOR — Standardized Exit Code Contract)**: Mapped all termination conditions to standard exit codes (0=Success, 1=CLI, 2=Input/Schema, 3=Domain, 4=WorkBudget, 5=ResourceLimit, 6=StepFailure, 7=IO).
6. **P3-F06 (MAJOR — DFS State Counter Placement)**: Moved `dfs_states` counter increment to state entry in both M1 and M2 search loops; ensured M2 charges work on anchor initialization.
7. **P3-F07 (MAJOR — Edge Connectivity Algorithm Alignment)**: Clarified that $\lambda(Q)$ is computed via Edmonds-Karp BFS augmenting paths (retracting Dinic claims) and validated against an independent all-cuts oracle.
8. **P3-F08 (MAJOR — Resource Containment & CLI Override)**: Verified `setrlimit` return value fail-closed and enforced CLI `--max-work` precedence over workload definitions.
9. **P3-F09 (MAJOR — Deep Graph State Observability)**: Added detailed vertex attributes (id, label, degree), canonical sorted edge lists, neighbor maps, and codec state to step-level diagnostic outputs.
10. **Diff Hygiene**: Stripped trailing whitespace across documentation to ensure clean `git diff --check`.

**Final R3-C Verdict**: **ENGINES_VERIFIED_READY_FOR_P4_DESIGN (PENDING INDEPENDENT REVIEW)**
- Native M1-enum and bare M2 event search engines are mathematically and empirically proven bit-for-bit identical to the independent Ground Truth Oracle.
- Zero duplicate emissions, full transactional ACID isolation, and strict fail-closed boundary enforcement are validated.
- Milestone D-INT-03 / Phase P3 remediation is complete and ready for independent closure review.
