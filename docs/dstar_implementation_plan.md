# D* Implementation and Validation Plan — Roadmap from M0 to M2

**Date:** 2026-11-17  
**Context:** Seminar RapidFlow / CSM — D* Feasibility Route  
**Current Phase:** DSTAR-01 (Issue #22) Foundation Complete  
**Next Phase:** Gate 0A (Advisor Confirmation) & Gate 0B-1 (RapidFlow Binary Audit)

---

## 1. Executive Roadmap Overview

The D* research direction adopts a phased, evidence-driven validation methodology. The objective is to evaluate whether single-pass event-anchored approximate matching (candidate algorithm **M2-Event-k**) can outperform decomposition baselines (**M1-count** and **M1-enum-Exact-k**) under strict Edge Recall and S2 connectivity.

```text
Gate 0A: Advisor Decisions (Scope, C++, COUNT vs Tuples, SCORE_CHANGED)
   │
   ▼
Gate 0B-1: RapidFlow Binary Audit (Raw ordered COUNT, Signed DELETE, Automorphism)
   │
   ▼
Gate 1: M0 Oracle & Correctness Foundation [COMPLETED IN DSTAR-01]
   │  ├── docs/dstar_problem_contract_v1.md
   │  ├── scripts/dstar_oracle.py (Independent M0, stdlib-only)
   │  ├── tests/fixtures/dstar01/ (Hand cases A–L)
   │  ├── tests/test_dstar_legacy_compatibility.py (56 configs, 224 updates PASS)
   │  └── reports/dstar01/correctness_summary.json
   │
   ▼
Gate 2: Baseline Implementation (M1-count & M1-enum-Exact-k)
   │  ├── M1-count: Inclusion-exclusion on RapidFlow Exact counts
   │  └── M1-enum-Exact-k: Enumeration on Q-D variants with shared primitive
   │
   ▼
Gate 3: M2-Event-k Correctness Prototype
   │  ├── Bare DFS with deterministic frontier (NO unproved pruning)
   │  └── 100% tuple-level equivalence against M0 Oracle (Diamond k=1, K4 k=1/2)
   │
   ▼
Gate 4: Fair Benchmark, Novelty & Scaling Evaluation
      ├── Hardware counters, work operations, latency, RSS
      ├── Scale to C6 (k=1) and K3,3 (k=2)
      └── Decision on C++ integration and thesis contribution
```

---

## 2. Completed Foundation: Gate 1 (DSTAR-01)

The following artifacts have been completed and verified under Issue #22:
1. **Working Problem Contract (`docs/dstar_problem_contract_v1.md`)**:
   - Locked definitions for query $Q$, data graph $G$, injective mapping $f$, and Edge Recall $S_E(f)$.
   - Explicit specification of S2 connectivity ($Q[E_Q \setminus R(f)]$ spanning connected).
   - Domain safety bound: $0 \le k < \lambda(Q)$, where $\lambda(Q)$ is global edge connectivity.
   - Event semantics: only `ENTERED` and `EXITED`; no `SCORE_CHANGED`.
   - Rejection rules and atomic fail-closed behavior on all invalid updates.
2. **Independent M0 Oracle (`scripts/dstar_oracle.py`)**:
   - Implemented in pure Python 3 standard library with zero third-party dependencies and zero coupling to legacy matching code.
   - Direct calculation of $\lambda(Q)$ via exhaustive cut enumeration.
   - Direct S2 connectivity evaluation on matched query edges.
   - Stateful incremental oracle computing true snapshot set differences.
3. **Hand-Calculated Microcases (`tests/fixtures/dstar01/`, Cases A–L)**:
   - Verified exact recovery ($k=0$), extra internal chords, external tails, injectivity rejection, vertex/edge label checks, sparse IDs, $K_4$ automorphism (24 tuples), Diamond event/no-event streams, unrelated updates, and S1 vs S2 distinction.
4. **Legacy Compatibility Matrix (`tests/test_dstar_legacy_compatibility.py`)**:
   - 56 configurations across $C_4$ ($k \in \{0, 1\}$), Diamond ($k \in \{0, 1\}$), and $K_4$ ($k \in \{0, 1, 2\}$) with 8 seeds each.
   - 224 valid dynamic updates executed step-by-step against legacy APX-02 Oracle (configured with $\varepsilon_m = k, \varepsilon_x = \text{None}, \text{STRICT}$).
   - 100% tuple-level agreement across all 224 updates (1,148 entered tuples, 978 exited tuples).
5. **Truthful Evidence Generation (`scripts/generate_dstar01_report.py`)**:
   - Generates structured summary `reports/dstar01/correctness_summary.json` with exact execution timestamps, base SHA, execution HEAD, and source file SHA-256 hashes.

---

## 3. Detailed Specifications for Future Gates

### 3.1. Gate 0A: Formal Scope Confirmation with Advisor
Before commencing Gate 2 implementation, the four foundational questions in `docs/dstar_problem_contract_v1.md` Section 7 must be reviewed with the thesis advisor:
- **Q1 (C++ Integration)**: Confirm whether direct modification of RapidFlow C++ source is mandatory or if a standalone research engine compared against RapidFlow is acceptable.
- **Q2 (Output Primary Metric)**: Confirm whether evaluation focuses on COUNT or explicit ordered tuples.
- **Q3 (Event Scope)**: Confirm that `SCORE_CHANGED` is excluded and only `ENTERED`/`EXITED` are emitted.
- **Q4 (MVP Domain)**: Confirm acceptance of Edge Recall on simple labeled graphs under $k < \lambda(Q)$ with pilot queries Diamond ($k=1$) and $K_4$ ($k \in \{1, 2\}$).

### 3.2. Gate 0B-1: RapidFlow Exact Binary & Source Audit
A rigorous audit of the pinned RapidFlow binary (`baselines/rapidflow/build/RapidFlow`) must be conducted:
1. **Raw Ordered Count vs Automorphism Factor**:
   - Inspect whether `RapidFlow` outputs the raw number of ordered embeddings $\sum_f 1$, or divides by $|\text{Aut}(Q)|$, or applies symmetry breaking / leaf shortcuts.
   - Compare `RapidFlow` exact count on $Q$ against M0 Oracle snapshot count for uniform vs labeled queries.
2. **Signed Delta on Deletion (`DELETE`)**:
   - RapidFlow streaming mode outputs signed delta values $\Delta N_t(Q)$ after an update.
   - On edge deletion, verify whether $\Delta N_t(Q) \le 0$ and whether `result_count_` reflects the exact snapshot count.
   - Construct two test scenarios:
     * *Scenario 1 (No threshold crossing)*: Initial graph has full Diamond (5 edges). Delete 1 edge. Verify that while exact count decreases, the approximate count at $k=1$ remains unchanged.
     * *Scenario 2 (Threshold crossing)*: Initial graph already lacks 1 edge. Delete a matched edge. Verify that the mapping exits at $k=1$.
3. **Exit Criteria for Gate 0B-1**:
   - RapidFlow binary confirmed to report compatible counts, or a proven mathematical adapter is implemented and verified.
   - If RapidFlow binary cannot be adapted for signed deletions or symmetry conventions, flag the project risk immediately and fallback to an independent Exact baseline.

### 3.3. Gate 2: Implementation of M1 Baselines
Two distinct baseline approaches must be implemented:
1. **M1-count (Counting Baseline)**:
   - For a query $Q$ with $m$ edges and budget $k < \lambda(Q)$:
     $$\text{APX\_COUNT}(G) = \sum_{|T| \le k} c_T \cdot N(Q - T)$$
     where $N(Q - T)$ is the exact count of $Q - T$ on $G$, and coefficients $c_T$ are derived via Möbius inversion / inclusion-exclusion:
     $$c_T = \sum_{j=|T|}^k (-1)^{j - |T|} \binom{m - |T|}{j - |T|}$$
   - Streaming delta count:
     $$\Delta \text{APX}_t = \sum_{|T| \le k} c_T \cdot \Delta N_t(Q - T)$$
   - Must be verified against M0 Oracle count deltas for both `INSERT` and `DELETE`.
2. **M1-enum-Exact-k (Enumeration Baseline)**:
   - For an update on edge $e$:
     Iterates over all subsets $D \subset E_Q \setminus \{q\}$ with $|D| = k$, where $q$ is compatible with $e$.
     Runs exact enumeration on $Q - D$ anchored to $e$.
     Filters matches to ensure $R(f) = D$ strictly (preventing duplicate emissions across different variants).
   - Must use the **identical** graph storage representation, edge lookup primitives, and tuple sink as M2 to ensure benchmarking isolates search strategy alone.

### 3.4. Gate 3: M2-Event-k Correctness Prototype
1. **Core DFS Search Engine**:
   - Implements Appendix G specification:
     * Step 1: Anchor $q = \{u, v\}$ to the updated data edge on the snapshot containing the edge.
     * Step 2: Injective state maintenance with status array `status[e] in {YES, NO, UNDECIDED}`.
     * Step 3: Frontier selection via a deterministic function of the state.
     * Step 4: Branch YES: maps unmapped endpoint to matching data neighbor, sets `status[e] = YES`.
     * Step 5: Branch NO: commits `status[e] = NO`, increments $|R_p|$, and **defers** the unmapped vertex (does not assign it).
     * Step 6: Terminal leaf checks injectivity, labels, $|R(f)| = k$, and emits canonical ordered tuple.
2. **No Unproved Pruning**:
   - Static NLF, dynamic NLF $\delta'$, and Local View must remain disabled during initial correctness verification.
3. **Exit Criteria for Gate 3**:
   - 100% tuple-level match against M0 Oracle on Diamond ($k=1$), $K_4$ ($k \in \{1, 2\}$), and $C_4$ ($k=1$) for both `INSERT` and `DELETE`.
   - Zero false positives, zero false negatives, zero duplicate tuples emitted.

### 3.5. Gate 4: Benchmarking and Scaling
1. **Fair Comparison Framework**:
   - Instrument hardware performance counters and work counters (state visits, edge lookups, candidate checks, emitted tuples).
   - Compare M1-enum-Exact-k against M2-Event-k using identical output sinks.
   - Compare M1-count against RapidFlow Exact counts separately.
2. **Topology Scaling**:
   - Evaluate scaling behavior on larger topologies: $C_6$ ($k=1, \lambda=2$) and $K_{3,3}$ ($k \in \{1, 2\}, \lambda=3$).
3. **Honest Scientific Evaluation**:
   - If M2 exhibits higher latency than M1 due to branch misprediction or deferred vertex tracking, report the bottleneck truthfully.
   - Formulate conclusions based on reproducible data rather than unverified speedup hypotheses.
