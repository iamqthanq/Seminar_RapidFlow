# D* Component Reuse and Legacy Audit

**Date:** 2026-11-17  
**Issue:** #22 (DSTAR-01)  
**Status:** COMPLETE (Foundation Audit for M1/M2 Gates)  
**Scope:** Rigorous classification of repository components across RapidFlow upstream, RF-01..RF-03, Model C (APX-01..APX-04), and D* (DSTAR-01).

---

## 1. Executive Summary

This audit establishes a clear boundary between historical research artifacts and the new D* validation route.
Previous iterations explored **Model C** (APX-01 through APX-04), which defined an unpenalized core with bounded external boundary cut edges ($x_{\text{out}} \le \varepsilon_x$) and data subgraph topological connectivity (S1).
The D* orientation resets the contract to **Edge Recall** with **S2 connectivity** on the matched query edges under the mathematical condition $0 \le k < \lambda(Q)$.

> [!IMPORTANT]
> **Key Scientific Distinctions**:
> 1. **$x_{\text{out}}$ hub filter does NOT belong to D***: In Model C, $\varepsilon_x$ filtered out dense hubs. In D*, Edge Recall does not penalize external boundary edges.
> 2. **APX-03 is a Recompute Baseline, NOT M2**: The C++ implementation in `extensions/apx03` is a snapshot recompute engine for Model C, not an incremental single-pass DFS.
> 3. **M1-count Requirements**: RapidFlow Exact binary decomposition must be audited for raw ordered counts, symmetry breaking, automorphism factors, and signed `DELETE` updates before being certified as a valid baseline.
> 4. **M1-enum vs M2 Fairness**: Future M1-enum-Exact-k and M2-Event-k must share identical underlying graph primitives, edge iteration order, and output sinks to enable fair profiling.
> 5. **M2 is NOT Implemented**: M2-Event-k exists solely as an inductive proof sketch at the specification level (Google Doc Appendix G). No code, performance, or novelty claims exist.

---

## 2. Comprehensive Component Reuse Classification

| File / Component | Category | Current Evidence & Role | Required Changes for D* | Critical Limitations |
|---|---|---|---|---|
| `baselines/rapidflow/source` | `KEEP` | Pinned upstream commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`. Pure exact counting. | None (MUST remain pristine). | Source only counts mappings; does not materialize ordered embeddings. |
| `scripts/rf02_oracle.py` | `HISTORICAL_ONLY` | RF-02 ground truth for Exact CSM counting (12 fixtures, 31 updates). | None. Preserved as immutable historical evidence. | Exact matching only; cannot score approximate edge recall or S2. |
| `scripts/apx02_oracle.py` | `ADAPT` (Test-Only) | Reference Oracle for Model C ($\varepsilon_m, \varepsilon_x$, S1, `SCORE_CHANGED`). Validated across 16 microcases. | Used strictly via test adapter (`test_dstar_legacy_compatibility.py`) with $\varepsilon_m = k, \varepsilon_x = \text{None}, \text{STRICT}$. Ignore `SCORE_CHANGED`. | Computes S1 (data induced connectivity), not S2. Does not reject $k \ge \lambda(Q)$. Emits `SCORE_CHANGED`. |
| `extensions/apx03/` | `HISTORICAL_ONLY` | C++ Recompute Baseline for Model C (PR #19, 46/46 tests passing). | None for DSTAR-01. May serve as secondary C++ recompute reference if needed. | Full recompute on every update, not incremental. Tailored to Model C core/boundary definitions. |
| `docs/apx04_gate_a3_decision.md` | `HISTORICAL_ONLY` | Gate A3-00 feasibility decision (PR #21). Classified A3-01 as `NOT_RUN`. | None. Preserved as provenance for why Model C scaling stalled. | Confirms NetFlow scaling under Model C was blocked by hub explosion. |
| `scripts/dstar_oracle.py` | `NEW` | Independent stdlib-only M0 Oracle for Edge Recall and S2 under $k < \lambda(Q)$. 35 tests PASS. | Baseline for all future D* gates (M1, M2). | Bounded candidate search; designed for small verification graphs ($|V_Q| \le 8, |V_G| \le 10$). |
| `tests/fixtures/dstar01/` | `NEW` | 12 hand-calculated test cases (A–L) with explicit ordered tuples and dynamic deltas. | Primary regression fixtures for D*. | Small synthetic topologies designed for correctness, not benchmarking. |
| `configs/dstar01_cases.json` | `NEW` | Predeclared test case manifest and 56-configuration matrix specification. | Configuration for CI and report generation. | None. |
| `tests/test_dstar_legacy_compatibility.py` | `NEW` | Test adapter verifying 100% tuple-level equivalence between D* M0 and APX-02 under restricted domain. | Maintained for regression. | Valid only within $0 \le k < \lambda(Q)$ and $\varepsilon_x = \text{None}$. |
| `scripts/generate_dstar01_report.py` | `NEW` | Generates verified, reproducible JSON correctness evidence with exact provenance. | Executed prior to PR creation for evidence handoff. | None. |
| `M1-count` | `NEW` (Future Gate 2) | Mathematical inclusion-exclusion on RapidFlow Exact counts $N(T)$. | Must implement linear combination of signed deltas $\Delta N(T)$ and verify `DELETE`. | Requires verified RapidFlow binary; does not emit tuple identities. |
| `M1-enum-Exact-k` | `NEW` (Future Gate 2) | Exact enumeration baseline running on $Q - D$ for $|D| = k$. | Must share graph store and output sink with M2. | Multiple runs of Exact matcher per update. |
| `M2-Event-k` | `NEW` (Future Gate 3) | Single-pass DFS candidate with frontier branching and deferred vertices. | Must be implemented from scratch adhering to Appendix G rules. | Currently unverified. No implementation exists. |

---

## 3. Mathematical Equivalence and Divergence: Model C vs D*

### 3.1. When Model C and D* Agree
In the restricted domain where:
1. $0 \le k < \lambda(Q)$
2. $\varepsilon_m = k$
3. $\varepsilon_x = \text{None}$ (no boundary filtering)
4. $\text{mode} = \text{STRICT}$

**Equivalence Theorem:**
For any mapping $f: V_Q \to V_G$, $\text{Accept}_{\text{D*}}(f) \iff \text{Accept}_{\text{ModelC}}(f)$.

*Proof Sketch:*
- Injectivity and hard vertex label requirements are identical in both contracts.
- Missing edge count $m(f) = |R(f)| \le k$ is identical.
- In D*, S2 requires $Q[E_Q \setminus R(f)]$ to be spanning and connected. Because $k < \lambda(Q)$, removing $\le k$ edges cannot disconnect $Q$, so S2 is always satisfied.
- In Model C STRICT, S1 requires $G[f(V_Q)]$ to be topologically connected. Since all edges in $E_Q \setminus R(f)$ are matched in $G[f(V_Q)]$, the connected spanning subgraph $Q[E_Q \setminus R(f)]$ maps directly to a connected spanning subgraph of $G[f(V_Q)]$. Thus $G[f(V_Q)]$ is connected.
- With $\varepsilon_x = \text{None}$, Model C imposes no boundary constraints.
- Hence, the acceptance predicates are logically equivalent.

This theorem was empirically confirmed across **56 configurations and 224 dynamic updates** (1,148 entered tuples, 978 exited tuples) with 100% agreement.

### 3.2. Where Model C and D* Diverge
1. **$k \ge \lambda(Q)$**:
   - Model C STRICT may accept mappings where $G[f(V_Q)]$ is connected via extraneous data chords, even though $Q[E_Q \setminus R(f)]$ is disconnected.
   - D* explicitly **rejects** $k \ge \lambda(Q)$ as outside the certified MVP domain.
2. **Boundary Cut Edges ($x_{\text{out}}$)**:
   - Model C filters candidates where $x_{\text{out}} > \varepsilon_x$.
   - D* adopts standard non-induced Edge Recall: external edges do not penalize matches.
3. **Event Stream (`SCORE_CHANGED`)**:
   - Model C emits `SCORE_CHANGED` when an accepted mapping gains or loses matched edges while remaining within budget.
   - D* emits **only** `ENTERED` and `EXITED`. Intermediate score fluctuations produce no events.

---

## 4. Obligations for Future Gates

### 4.1. Gate 0B-1 / Gate 2: RapidFlow Exact Binary Audit
Before `M1-count` can be constructed:
1. Re-audit the pinned RapidFlow binary (`baselines/rapidflow/build/RapidFlow`).
2. Verify whether RapidFlow reports raw ordered embeddings or quotiented counts (automorphism normalization).
3. Test signed delta streaming on `DELETE` updates:
   - Case 1: Initial graph has all 5 edges of Diamond. Delete 1 edge. Expected: $N(Q)$ decreases, but approximate count for $k=1$ remains unchanged.
   - Case 2: Initial graph already lacks 1 edge. Delete a matched edge. Expected: mapping exits at $k=1$.
4. Verify that `result_count_` and signed delta flags match theoretical inclusion-exclusion.

### 4.2. Gate 2: M1-enum-Exact-k Baseline Construction
- Must instantiate exact matchers on $Q - D$ for each $|D| = k$.
- Must filter candidates to accept only mappings where $R(f) = D$ exactly (to avoid duplicate counting across variants).
- Must utilize the **exact same** internal graph storage, adjacency iterator, and output consumer as M2 to ensure benchmarking isolates algorithmic differences.

### 4.3. Gate 3: M2-Event-k Specification Compliance
- Must adhere strictly to the frontier selection function, deferred vertex handling, and branch commitment invariants detailed in Appendix G.
- Must demonstrate zero false positives, zero false negatives, and zero duplicate emissions against M0 Oracle before any benchmarking or optimization.
- Dynamic NLF $\delta'$ and Local View are deferred optimizations and must not be included in the correctness prototype.
