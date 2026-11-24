# Research Design Document: RapidFlow-APX Phase P4b-0
## Mathematical Design, Architectural Specification, Correctness Proofs & Experimental Protocol
### Evidence-Driven Optional Optimization of Native Bare M2

**Document ID:** `docs/rapidflow_apx_p4b_design.md`  
**Milestone:** Phase P4b-0  
**Parent Roadmap Issue:** [Issue #1](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)  
**Active Phase Issue:** [Issue #38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38)  
**Active Draft PR:** [Draft PR #39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39)  
**Target Integration Branch:** `QuangThang`  
**Target Release Branch:** `main` (User-approved release only)  
**Base Commit:** `7009daea477721c12eb52aa3d5db62f54f5b2ca4` (Merge PR #37 into `QuangThang`)  
**Design Starting Commit:** `11963e642d8df2bc8082336fe1c534c19b4c3b6e`  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)  
**Status:** COMPLETE & READY FOR COORDINATOR REVIEW  
**Proposed Design Verdict:** `P4B_DESIGN_READY_FOR_REVIEW`  

---

## 1. Executive Summary

Phase P4b-0 formalizes the mathematical principles, architectural specifications, correctness proofs, and experimental methodology for the evidence-driven optional optimization of the Bare M2 native event engine (`ApxM2EventEngine`) within RapidFlow-APX.

### 1.1. Context & Accepted P4a Empirical Foundation
Phase P4a completed the native profiling of Bare M2 against the enumeration baseline M1 across 180 canonical measurement slots (30 workloads $\times$ 3 repetitions $\times$ 2 engines = 90 paired observations), achieving 100% agreement with the independent Python ground truth oracle (`DStarDynamicOracle`) and zero duplicate embeddings. The certified findings established:
1. **Search Speedup ($T_{\text{M1}} / T_{\text{M2}}$ across 4 Scopes):**
   - Cumulative Search Speedup: Median **2.1269x** (Mean: 2.7092x, Max: 9.7401x across 84 valid computable pairs).
   - Native Step Speedup: Median **1.8021x** (Mean: 2.1542x, Max: 6.1823x across 90 pairs).
   - Streaming Loop Speedup: Median **1.1136x** (Mean: 1.2051x, Max: 2.4809x across 90 pairs).
   - Outer Wall Speedup: Median **1.0414x** (Mean: 1.0556x, Max: 1.7139x across 90 pairs).
2. **Candidate Evaluation Reduction:**
   - Bare M2 evaluated **245,055** candidates compared to M1's **455,148** candidates (a **46.16% relative reduction**).
3. **Micro-Stage Cost Centers in Bare M2:**
   - Within Search duration: **Search Core occupies 77.22%** (median) and **Neighbor Retrieval occupies 22.78%** (median).
   - Within Native Step duration: Search Core occupies 67.23% (median), Neighbor Retrieval occupies 16.25% (median), Update Maintenance occupies 3.12% (median), and Tuple Commit occupies 1.38% (median).

### 1.2. P4b Research Mission
The central research question of Phase P4b is:
> *Can the computational cost of Bare M2 be significantly reduced without losing any valid approximate event embeddings, violating transaction atomicity, or inflating memory overhead beyond practical bounds?*

To address this question scientifically, Phase P4b investigates two independent optimization avenues directly derived from empirical profiling:
- **Option A (Sound Relaxed NLF):** Pruning unviable candidate exploration branches early by establishing provably sound lower bounds on missing edges from incident neighbor label frequencies (targeting the 77.22% Search Core bottleneck).
- **Option B (Neighbor Retrieval Optimization):** Eliminating heap allocation overhead and vector sorting during neighbor expansion via depth-indexed reusable candidate scratchpads and linear two-pointer merging (targeting the 22.78% Neighbor Retrieval bottleneck).

### 1.3. Scientific & Governance Boundaries
In strict adherence to repository governance (`AGENTS.md`, `GEMINI.md`, and `docs/BRANCH_POLICY.md`):
- **Design Gate Restriction:** P4b-0 is strictly a design and formalization gate. **No production C++ optimization code is implemented in this phase**, and no performance benchmarks are executed.
- **Pristine Upstream:** The upstream RapidFlow submodule at `baselines/rapidflow/source` remains pristine at pinned commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`.
- **Domain Invariance:** The formal problem definition (Domain D-R: $0 \le k < \lambda(Q)$, injective matching, hard vertex label matching, event leaf condition $|R_G(f)| = k$, and explicit ordered tuple output) is strictly invariant.
- **Honest Fallback:** If neither Option A nor Option B demonstrates a statistically significant net performance advantage over Bare M2 under fair experimental conditions, Bare M2 remains the primary engine of RapidFlow-APX (**Option D: Safe Fallback**). A negative result is a scientifically valid and acceptable outcome.

---

## 2. Source & Evidence Inventory

### 2.1. Codebase & Document Artifact Audit
Every claim and design decision in this document is anchored in verified source code and documentation:

| Component / File Path | Key Inspected Entities | Audit Classification |
| :--- | :--- | :---: |
| `extensions/rapidflow_apx/src/m2_event_engine.cpp` | `search_event()`, `find_frontier_edge()`, `dfs_m2()` | `PROVEN_FROM_SOURCE` |
| `extensions/rapidflow_apx/include/rapidflow_apx/m2_event_engine.h` | `EdgeCommitment`, `ApxM2EventEngine` interface | `PROVEN_FROM_SOURCE` |
| `extensions/rapidflow_apx/src/authoritative_graph.cpp` | `get_neighbors()`, `get_degree()`, `begin/commit/rollback_transaction()` | `PROVEN_FROM_SOURCE` |
| `extensions/rapidflow_apx/include/rapidflow_apx/authoritative_graph.h` | Hybrid CSR + overlay storage, transaction records | `PROVEN_FROM_SOURCE` |
| `extensions/rapidflow_apx/src/transaction_manager.cpp` | `execute_insert()`, `execute_delete()`, transaction lifecycle | `PROVEN_FROM_SOURCE` |
| `extensions/rapidflow_apx/src/query_plan.cpp` | `get_incident_edges()`, `get_edges_with_label()`, edge connectivity | `PROVEN_FROM_SOURCE` |
| `extensions/rapidflow_apx/include/rapidflow_apx/engine_counters.h` | Legacy engine counters, `charge_work()`, work cap | `PROVEN_FROM_SOURCE` |
| `baselines/rapidflow/source/graph/graph.cpp` | `loadGraphFromMemory()` (L87-180), CSR neighbor sorting (L164) | `PROVEN_FROM_SOURCE` |
| `baselines/rapidflow/source/graph/graph.h` | `getVertexNeighbors()` (L193), binary search in `checkEdgeExistence()` | `PROVEN_FROM_SOURCE` |
| `baselines/rapidflow/source/utility/relation/global_edge_view.h` | `GlobalEdgeView::insert()` (L24-41), `remove()` (L43-55) | `PROVEN_FROM_SOURCE` |
| `docs/research_decisions/D-R4a-native-profiling.md` | P4a decision matrix, speedup scopes, bottleneck analysis | `PROVEN_FROM_SOURCE` |
| `docs/evidence/p4a_evidence_manifest.json` | 7 registered evidence artifacts, historical execution identity | `PROVEN_FROM_SOURCE` |
| `results/logs/rf36-p4a/p4a_raw_ledger.jsonl` | 180 canonical measurement records, SHA `34452fb4ee...` | `PROVEN_FROM_SOURCE` |

### 2.2. Critical Source Findings
1. **Upstream CSR Neighbors are Strictly Sorted:** Inspection of `baselines/rapidflow/source/graph/graph.cpp:164` reveals:
   ```cpp
   for (ui i = 0; i < vertices_count_; ++i) {
       std::sort(neighbors_ + offsets_[i], neighbors_ + offsets_[i + 1]);
   }
   ```
   Within the base CSR structure, all neighbors of every vertex are sorted in strictly ascending order of `VertexID` (`uint32_t`) with zero duplicate edges.
2. **Upstream Dynamic Overlay is Strictly Sorted:** Inspection of `baselines/rapidflow/source/utility/relation/global_edge_view.h:27-29` reveals:
   ```cpp
   auto lb = std::lower_bound(it->second.begin(), it->second.end(), v);
   if (lb == it->second.end() || *lb != v) {
       it->second.insert(lb, v);
   ```
   `GlobalEdgeView::insert()` performs binary search via `std::lower_bound` and inserts at `lb`, ensuring the overlay neighbor vector is invariant under strictly ascending order with zero duplicates.
3. **`get_degree()` is NEVER Called on the M2 Search Hot Path:** Inspection of all call sites reveals `ApxAuthoritativeGraph::get_degree()` is called only in test harness logging and foundation validation. Neither `dfs_m2` nor `search_event` calls `get_degree()`.
4. **MISSING Branch Advances Stack Depth Without Advancing `assigned_count`:** In `m2_event_engine.cpp:317-328`, when a frontier edge is committed as `MISSING`, `dfs_m2` recurses with `missing_count + 1` while `assigned_count` and `mapping` remain unchanged.

---

## 3. Formal Problem Contract

The formal semantics of RapidFlow-APX are immutable and enforced across all phases:

### 3.1. Problem Entities
- **Query Graph:** $Q = (V_Q, E_Q)$ is a simple, undirected, connected graph with vertex count $n = |V_Q| \ge 2$, edge count $m = |E_Q|$, vertex label function $\ell_Q: V_Q \to \mathcal{L}_V$, and edge label function $\ell_Q: E_Q \to \mathcal{L}_E$.
- **Dynamic Data Graph:** $G_t = (V_0, E_t)$ where $V_0$ is a fixed data vertex set, vertex labels $\ell_G: V_0 \to \mathcal{L}_V$ are static, and edge set $E_t \subseteq \binom{V_0}{2}$ changes via single-edge stream operations (`INSERT` or `DELETE`).
- **Injective Mapping:** $f: V_Q \to V_0$ satisfies:
  1. *Injectivity:* $\forall u, w \in V_Q, u \neq w \implies f(u) \neq f(w)$.
  2. *Hard Vertex Label Match:* $\forall u \in V_Q, \ell_G(f(u)) = \ell_Q(u)$.
- **Missing-Edge Set:**
  $$R_G(f) = \left\{ \{u, w\} \in E_Q : \{f(u), f(w)\} \notin E_G \;\lor\; \ell_G(f(u), f(w)) \neq \ell_Q(u, w) \right\}$$
  An edge that exists in $G$ but has a mismatched edge label is treated strictly as a missing query edge.
- **Missing-Edge Budget:** An embedding $f$ is admissible under tolerance $k$ if and only if $|R_G(f)| \le k$.
- **Domain D-R Guard:** Every tested workload strictly satisfies $0 \le k < \lambda(Q)$, where $\lambda(Q)$ is the edge connectivity of $Q$. Under Domain D-R, the matched query subgraph $Q[E_Q \setminus R_G(f)]$ is guaranteed to be connected.

### 3.2. Event Boundaries & Output Contract
- **Event Search Semantics:**
  - For `INSERT (u, v, label)`: Search is executed on $G_{\text{after}} = (V_0, E_{t-1} \cup \{e\})$.
  - For `DELETE (u, v, label)`: Search is executed on $G_{\text{before}} = (V_0, E_{t-1})$.
- **Anchor Condition:** The search is strictly anchored at the updated edge: the edge $(u, v)$ must map to a query edge $e \in E_Q$ with matching edge label under both valid orientations.
- **Event Leaf Condition:** An event is emitted if and only if $|R_G(f)| = k$.
- **Canonical Output Format:** Explicit ordered tuples $T(f) = (f(u_1), f(u_2), \ldots, f(u_n))$, ordered strictly by canonical query vertex ID. Symmetric automorphisms are explicitly enumerated; count-only approximation is prohibited.

---

## 4. Bare M2 Native Architecture & Invariants

### 4.1. Call Graph Architecture
```text
Native Test Runner / Streaming Harness
       |
       v
ApxStreamingEngine::execute_step()
       |
       v
ApxTransactionManager::execute_insert() / execute_delete()
       |
       +---> ApxAuthoritativeGraph::begin_transaction()
       |
       +---> [For INSERT]: ApxAuthoritativeGraph::insert_edge()
       |
       +---> ApxM2EventEngine::search_event(snapshot, u_upd, v_upd, label_upd, sink, counters)
       |           |
       |           +---> Anchor Initialization (orientations 0 and 1)
       |           |
       |           +---> ApxM2EventEngine::dfs_m2() [Recursive Search]
       |                       |
       |                       +---> find_frontier_edge()
       |                       |
       |                       +---> PRESENT Branch:
       |                       |        snapshot.get_neighbors(u_data)
       |                       |        Candidate verification (edge label, injectivity, vertex label)
       |                       |        Tentative assignment v_q -> cand
       |                       |        Two-endpoint incident edge resolution
       |                       |        dfs_m2(..., missing_count, assigned_count + 1)
       |                       |        Rollback assignment & commitments
       |                       |
       |                       +---> MISSING Branch:
       |                                status[frontier_edge] = MISSING
       |                                dfs_m2(..., missing_count + 1, assigned_count)
       |                                Rollback status[frontier_edge] = UNKNOWN
       |
       +---> [For DELETE]: ApxAuthoritativeGraph::delete_edge()
       |
       +---> ApxAuthoritativeGraph::commit_transaction() [or rollback_transaction() on error]
```

### 4.2. Invariant Specifications

| Invariant ID | Name | Source Location | Enforcement Mechanism | Failure Consequence |
| :--- | :--- | :--- | :--- | :--- |
| **INV-01** | Injective Data Mapping | `m2_event_engine.cpp:214` | `used_data[cand]` check before assignment | Corrupted non-injective embeddings |
| **INV-02** | Hard Vertex Label Match | `m2_event_engine.cpp:219` | `snapshot.get_vertex_label(cand) == v_q_label` | Semantically invalid embeddings |
| **INV-03** | Frontier Cut Existence | `m2_event_engine.cpp:34` | Throws `InvariantViolationException` if cut is empty and $m_{\text{cur}} \le k$ | Search prematurely terminates; lost embeddings |
| **INV-04** | Missing Budget Containment | `m2_event_engine.cpp:138` | Immediate return if `missing_count > k` | Search tree explosion; budget violation |
| **INV-05** | Incident Edge Consistency | `m2_event_engine.cpp:240-278` | Resolves all edges between newly mapped vertex and previously mapped vertices | Inconsistent commitment states |
| **INV-06** | Leaf Commitment Verification | `m2_event_engine.cpp:148-167` | Re-verifies every query edge in $G$; rejects if committed MISSING edge actually exists | False positive event emissions |
| **INV-07** | Event Leaf Boundary | `m2_event_engine.cpp:170` | Emits tuple if and only if `missing_count == k` | Incorrect event classification |
| **INV-08** | Transaction Snapshot Integrity | `transaction_manager.cpp:23-95` | INSERT searches $G_{\text{after}}$; DELETE searches $G_{\text{before}}$ | State corruption; inconsistent time travel |
| **INV-09** | Zero-Allocation Rollback | `authoritative_graph.cpp:206-258` | All rollback paths use `noexcept` container operations | Memory leak / crash during exception handling |

---

## 5. Option A: Sound Relaxed NLF Theory & Formal Proofs

### 5.1. The Failure of Exact NLF in Approximate Matching
In exact subgraph matching, Neighborhood Label Frequency (NLF) requires that for every label $\ell$, the number of $\ell$-labeled neighbors of a data vertex $v$ must be greater than or equal to that of the query vertex $u$:
$$\forall \ell \in \mathcal{L}_V, \quad |N_G(v, \ell)| \ge |N_Q(u, \ell)|$$
In approximate matching with missing budget $k$, an embedding $f$ is permitted to miss up to $k$ incident query edges. Applying the exact condition directly results in catastrophic false negatives (pruning valid approximate embeddings).

### 5.2. Formal Definition of Typed Incident Signatures
To capture both edge labels and vertex labels without type conflation:
- **Typed Signature:** A signature is an ordered pair $\sigma = (\ell_E, \ell_V) \in \mathcal{L}_E \times \mathcal{L}_V$.
- **Query Signature Frequency:** For $u \in V_Q$:
  $$N_Q(u, \sigma) = \left| \left\{ \{u, w\} \in E_Q : \ell_Q(u, w) = \ell_E \;\land\; \ell_Q(w) = \ell_V \right\} \right|$$
- **Data Signature Frequency:** For $v \in V_0$ in snapshot $G$:
  $$N_G(v, \sigma) = \left| \left\{ \{v, x\} \in E_G : \ell_G(v, x) = \ell_E \;\land\; \ell_G(x) = \ell_V \right\} \right|$$

### 5.3. Theorem 1: Soundness of Static Relaxed NLF
**Definition (Static Deficit):** For $u \in V_Q$ and $v \in V_0$:
$$\delta_{\text{static}}(u, v) = \sum_{\sigma \in \Sigma_Q(u)} \max\left(0, \; N_Q(u, \sigma) - N_G(v, \sigma)\right)$$
where $\Sigma_Q(u) = \{\sigma : N_Q(u, \sigma) > 0\}$.

**Theorem 1:** *Let $f: V_Q \to V_0$ be any valid approximate embedding of $Q$ in $G$ with $|R_G(f)| \le k$. If $f(u) = v$, then:*
$$\delta_{\text{static}}(u, v) \le |R_G(f) \cap I_Q(u)| \le |R_G(f)| \le k$$
*where $I_Q(u)$ is the set of query edges incident to $u$.*

**Proof:**
1. Let $I_Q(u, \sigma) = \{\{u, w\} \in E_Q : \ell_Q(u, w) = \ell_E \land \ell_Q(w) = \ell_V\}$. The sets $\{I_Q(u, \sigma)\}_{\sigma \in \Sigma_Q(u)}$ form a partition of $I_Q(u)$:
   $$\bigcup_{\sigma \in \Sigma_Q(u)} I_Q(u, \sigma) = I_Q(u) \quad \text{and} \quad I_Q(u, \sigma) \cap I_Q(u, \sigma') = \emptyset \;\; (\sigma \neq \sigma')$$
2. For each query edge $e = \{u, w\} \in I_Q(u, \sigma)$, let $e_G = \{f(u), f(w)\} = \{v, f(w)\}$. By the injectivity of $f$, for any two distinct query edges $\{u, w_1\}, \{u, w_2\} \in I_Q(u, \sigma)$ with $w_1 \neq w_2$, we have $f(w_1) \neq f(w_2)$. Hence, the corresponding data edges $\{v, f(w_1)\}$ and $\{v, f(w_2)\}$ are distinct.
3. A query edge $e \in I_Q(u, \sigma)$ is matched (not missing) in $G$ if and only if $\{v, f(w)\} \in E_G$ and $\ell_G(v, f(w)) = \ell_E$. Since $f$ preserves vertex labels, $\ell_G(f(w)) = \ell_Q(w) = \ell_V$. Thus, every matched edge in $I_Q(u, \sigma)$ corresponds to a distinct data neighbor of $v$ with signature $\sigma$.
4. The total number of such matched edges cannot exceed the total number of available $\sigma$-neighbors of $v$ in $G$, which is $N_G(v, \sigma)$:
   $$\left| I_Q(u, \sigma) \setminus R_G(f) \right| \le N_G(v, \sigma)$$
5. Therefore, the number of missing edges in $I_Q(u, \sigma)$ is bounded below by:
   $$\left| I_Q(u, \sigma) \cap R_G(f) \right| = |I_Q(u, \sigma)| - \left| I_Q(u, \sigma) \setminus R_G(f) \right| \ge N_Q(u, \sigma) - N_G(v, \sigma)$$
   Since the count of missing edges cannot be negative, we have:
   $$\left| I_Q(u, \sigma) \cap R_G(f) \right| \ge \max\left(0, \; N_Q(u, \sigma) - N_G(v, \sigma)\right)$$
6. Summing over all disjoint signatures $\sigma \in \Sigma_Q(u)$:
   $$|R_G(f) \cap I_Q(u)| = \sum_{\sigma \in \Sigma_Q(u)} \left| I_Q(u, \sigma) \cap R_G(f) \right| \ge \sum_{\sigma \in \Sigma_Q(u)} \max\left(0, \; N_Q(u, \sigma) - N_G(v, \sigma)\right) = \delta_{\text{static}}(u, v)$$
7. Since $I_Q(u) \subseteq E_Q$, we have $|R_G(f) \cap I_Q(u)| \le |R_G(f)| \le k$.
8. Hence, $\delta_{\text{static}}(u, v) > k \implies |R_G(f)| > k$, which proves that pruning candidate $v$ for query vertex $u$ when $\delta_{\text{static}}(u, v) > k$ is **strictly sound** (no false negatives). $\blacksquare$

### 5.4. Theorem 2: Soundness of Conservative Residual Relaxed NLF
During DFS, the search maintains a set of committed missing edges:
$$M_{\text{cur}} = \{e \in E_Q : \text{status}[e] = \text{MISSING}\}, \quad m_{\text{cur}} = |M_{\text{cur}}|$$

**The Double-Counting Pitfall:** Adding $m_{\text{cur}} + \delta_{\text{static}}(u, v) > k$ is **unsound**. If an edge $e \in I_Q(u)$ was already committed as `MISSING`, it contributed $1$ to $m_{\text{cur}}$, and its absence in $G$ may also create a deficit in $\delta_{\text{static}}(u, v)$. Adding both counts that single missing edge twice, causing false negatives (Counterexample A2).

**Definition (Committed Missing Incident Set):**
$$I_Q^{\text{miss}}(u, \sigma) = \{e \in I_Q(u, \sigma) : \text{status}[e] = \text{MISSING}\}$$

**Definition (Conservative Residual Deficit):**
$$\delta_{\text{res}}^{\text{cons}}(u, v) = \sum_{\sigma \in \Sigma_Q(u)} \max\left(0, \; N_Q(u, \sigma) - |I_Q^{\text{miss}}(u, \sigma)| - N_G(v, \sigma)\right)$$

**Theorem 2:** *Let $f: V_Q \to V_0$ be any completion consistent with current commitments (i.e. $M_{\text{cur}} \subseteq R_G(f)$ and $\text{status}[e] = \text{PRESENT} \implies e \notin R_G(f)$). If $f(u) = v$, then:*
$$m_{\text{cur}} + \delta_{\text{res}}^{\text{cons}}(u, v) \le |R_G(f)| \le k$$

**Proof:**
1. Decompose the total missing set into committed missing edges and uncommitted missing edges:
   $$R_G(f) = M_{\text{cur}} \;\dot\cup\; \left( R_G(f) \setminus M_{\text{cur}} \right)$$
   Hence, $|R_G(f)| = |M_{\text{cur}}| + |R_G(f) \setminus M_{\text{cur}}| = m_{\text{cur}} + |R_G(f) \setminus M_{\text{cur}}|$.
2. Consider the uncommitted query edges incident to $u$ with signature $\sigma$:
   $$I_Q^{\text{uncommitted}}(u, \sigma) = I_Q(u, \sigma) \setminus M_{\text{cur}}$$
   Its cardinality is $|I_Q^{\text{uncommitted}}(u, \sigma)| = N_Q(u, \sigma) - |I_Q^{\text{miss}}(u, \sigma)|$.
3. Any edge $e \in I_Q^{\text{uncommitted}}(u, \sigma)$ that is matched in $G$ must map to an available $\sigma$-neighbor of $v$ in $G$. Thus:
   $$\left| I_Q^{\text{uncommitted}}(u, \sigma) \setminus R_G(f) \right| \le N_G(v, \sigma)$$
4. The number of additional missing edges in $I_Q^{\text{uncommitted}}(u, \sigma)$ is therefore bounded below by:
   $$\left| I_Q^{\text{uncommitted}}(u, \sigma) \cap R_G(f) \right| \ge \max\left(0, \; |I_Q^{\text{uncommitted}}(u, \sigma)| - N_G(v, \sigma)\right) = \max\left(0, \; N_Q(u, \sigma) - |I_Q^{\text{miss}}(u, \sigma)| - N_G(v, \sigma)\right)$$
5. Since $I_Q^{\text{uncommitted}}(u, \sigma) \subseteq E_Q \setminus M_{\text{cur}}$, these additional missing edges are completely disjoint from $M_{\text{cur}}$.
6. Summing over all signatures $\sigma \in \Sigma_Q(u)$ gives a lower bound on missing edges in $E_Q \setminus M_{\text{cur}}$:
   $$\left| (E_Q \setminus M_{\text{cur}}) \cap R_G(f) \right| \ge \sum_{\sigma \in \Sigma_Q(u)} \left| I_Q^{\text{uncommitted}}(u, \sigma) \cap R_G(f) \right| \ge \delta_{\text{res}}^{\text{cons}}(u, v)$$
7. Adding $|M_{\text{cur}}| = m_{\text{cur}}$ yields:
   $$|R_G(f)| = m_{\text{cur}} + \left| (E_Q \setminus M_{\text{cur}}) \cap R_G(f) \right| \ge m_{\text{cur}} + \delta_{\text{res}}^{\text{cons}}(u, v)$$
8. Thus, if $m_{\text{cur}} + \delta_{\text{res}}^{\text{cons}}(u, v) > k$, then $|R_G(f)| > k$. Pruning is **strictly sound** and provably avoids double-counting. $\blacksquare$

---

### 5.5. Rigorous Counterexample Analysis (A1–A7)

```text
[A1: Exact NLF False Negative]
Query Q (Diamond, k=1):             Data Graph G (C4 after insert 2-3):
        0 (deg 2)                              0 (deg 2)
       / \                                    / \
      1---2 (deg 3)                          1   2 (deg 2)
       \ /                                    \ /
        3 (deg 2)                              3 (deg 2)
Exact NLF: Query v1 has deg 3 > all G vertices (deg 2) -> Prunes (0 embeddings, WRONG!)
Static Relaxed NLF: Delta_static = max(0, 3 - 2) = 1 <= k=1 -> Preserves (0,1,2,3) (SOUND!)

[A2: Residual Double-Counting Pitfall]
At DFS state: Edge 1-2 committed MISSING (m_cur = 1).
Examining vertex 1:
Naive bound: m_cur + Delta_static = 1 + 1 = 2 > k=1 -> Prunes (0,1,2,3) (WRONG!)
Conservative Residual: I_miss = 1 -> Remaining = 3 - 1 = 2 -> Delta_res = max(0, 2 - 2) = 0.
Combined: m_cur + Delta_res = 1 + 0 = 1 <= k=1 -> Preserves (0,1,2,3) (SOUND!)
```

- **Counterexample A1 (Exact NLF False Negative):**
  - *Setup:* Query $Q$ is Diamond ($V_Q=\{0,1,2,3\}$, edges $0-1, 0-2, 1-2, 1-3, 2-3$, all labels 0, $\lambda(Q)=2, k=1$). Data graph $G$ is $C_4$ (edges $0-1, 0-2, 1-3, 2-3$, missing only $1-2$).
  - *Exact NLF:* At query vertex 1 (degree 3), every data vertex has degree 2. Exact NLF rejects all mappings. Result: 0 embeddings emitted (false negative).
  - *Static Relaxed NLF:* $N_Q(1, \sigma)=3, N_G(v, \sigma)=2$. Deficit $\delta_{\text{static}}(1, v) = \max(0, 3 - 2) = 1$. Since $1 \le k=1$, the candidate is NOT pruned. The valid embedding $(0, 1, 2, 3)$ is found.
- **Counterexample A2 (Double-Counting in Residual NLF):**
  - *Setup:* Same Diamond graph. During DFS, edge $1-2$ is committed as `MISSING` ($m_{\text{cur}} = 1$). Next, candidate $v$ is evaluated for vertex 1.
  - *Naive Combined:* $m_{\text{cur}} + \delta_{\text{static}} = 1 + 1 = 2 > k=1$. Prunes candidate, missing valid completion $(0, 1, 2, 3)$.
  - *Conservative Residual:* Edge $1-2 \in I_Q^{\text{miss}}(1, \sigma)$, so $|I_Q^{\text{miss}}| = 1$. Remaining required: $3 - 1 = 2$. Available: $2$. Deficit $\delta_{\text{res}}^{\text{cons}} = \max(0, 2 - 2) = 0$. Combined bound: $1 + 0 = 1 \le k=1$. Candidate is NOT pruned.
- **Counterexample A3 (Typed Label Mismatch):**
  - *Setup:* Query edge label is integer `0`. Data edge label is string `"0"`.
  - *Resolution:* Signature $\sigma = (\text{int } 0, \text{int } 0) \neq (\text{str } 0, \text{int } 0)$. The data edge does not match the signature and is counted as missing. If $k=1$, it is tolerated; if $k=0$, it is correctly rejected.
- **Counterexample A4 (Deferred MISSING Commitments):**
  - *Setup:* A frontier edge is committed `MISSING`, deferring vertex assignment. Later, the vertex is assigned via another `PRESENT` edge.
  - *Resolution:* Computing $I_Q^{\text{miss}}(v_q, \sigma)$ iterates over all incident query edges and checks `status[inc_j] == MISSING`. Because commitments persist in `status`, deferred missing edges are accurately accounted for regardless of traversal order.
- **Counterexample A5 (Wrong-Label Edge Transitions):**
  - *Setup:* Stream executes `INSERT (u, v, label_A)`, then `DELETE (u, v, label_A)`, then `INSERT (u, v, label_B)`.
  - *Resolution:* `ApxNlfIndex` increments signature for $(\text{label}_A)$, decrements on delete, and increments $(\text{label}_B)$, perfectly tracking snapshot frequencies.
- **Counterexample A6 ($k=0$ Exact Equivalence):**
  - *Setup:* When $k=0$, $\delta_{\text{static}}(u, v) > 0 \implies \text{prune}$. This occurs if and only if $\exists \sigma: N_Q(u, \sigma) > N_G(v, \sigma)$, exactly recovering the classical Exact NLF necessary condition.
- **Counterexample A7 (Unsigned Underflow & Overflow):**
  - *Resolution:* Arithmetic must never compute $N_Q - N_G$ as unsigned before checking $> 0$. Guarded subtraction `(req > avail) ? (req - avail) : 0` avoids underflow.

---

## 6. Option A Architecture & Data-Side Index Design

### 6.1. Query Signature Registry (`ApxQueryPlan`)
During query plan construction:
1. Extract unique signatures $\Sigma_Q = \{(\ell_Q(e), \ell_Q(w)) : e = \{u, w\} \in E_Q\}$.
2. Assign dense 0-based signature IDs: $\text{SigID} \in [0, |\Sigma_Q| - 1]$.
3. For each $u \in V_Q$, precompute:
   - A compact list of requirements: `std::vector<std::pair<SigID, uint16_t>> signature_requirements`.
   - Incident edge signature lookup: `SigID edge_signature[u][inc_idx]`.

### 6.2. Data-Side Frequency Index (`ApxNlfIndex`)

#### Representation Trade-off Analysis:

| Representation | Storage Format | Memory Complexity ($|V_G|=10^5, |\Sigma_Q|=8$) | Lookup Complexity | Allocation on Update | Rollback Safety |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Dense 1D Vector** (Selected) | `std::vector<uint16_t> table_` ($|V_G| \cdot |\Sigma_Q|$) | **1.60 MB** | **$O(1)$ direct index** | **Zero** | **Zero-allocation `noexcept`** |
| Sparse Map Vector | `vector<sparse_hash_map<SigID, uint16_t>>` | 6.40 – 12.80 MB | $O(1)$ amortized hash | Dynamic on new sig | Heap allocation on insert |
| Dynamic 2D Jagged | `vector<vector<SigID, uint16_t>>` | 3.20 – 5.00 MB | $O(|\Sigma_Q|)$ linear scan | Dynamic on growth | Reallocation risk |

**Selected Architecture:** The **Dense 1D Vector** is selected. Indexed by `v * num_signatures + sig_id`, it provides guaranteed $O(1)$ lookup, optimal cache locality, zero allocation during streaming updates, and guaranteed zero-allocation rollback on transaction abort.

### 6.3. Memory Scaling Model
Total memory consumed by `ApxNlfIndex`:
$$M_{\text{dense}}(|V_G|, |\Sigma_Q|) = |V_G| \times |\Sigma_Q| \times \text{sizeof(uint16_t)} \quad \text{bytes}$$

| Data Graph Vertices ($|V_G|$) | Signatures ($|\Sigma_Q| = 4$) | Signatures ($|\Sigma_Q| = 8$) | Signatures ($|\Sigma_Q| = 16$) | Feasibility within 512 MiB Budget |
| :---: | :---: | :---: | :---: | :---: |
| $16$ (P4a small) | 128 bytes | 256 bytes | 512 bytes | $\ll 0.01\%$ |
| $64$ (P4b discrimination) | 512 bytes | 1,024 bytes | 2,048 bytes | $\ll 0.01\%$ |
| $1,000$ (P4b medium) | 8 KB | 16 KB | 32 KB | $< 0.01\%$ |
| $100,000$ (P5 NetFlow subset) | 800 KB | 1.6 MB | 3.2 MB | $0.31\%$ |
| $1,000,000$ (Large graph) | 8.0 MB | 16.0 MB | 32.0 MB | $3.12\%$ |

### 6.4. Transactional Snapshot Maintenance
To guarantee snapshot consistency without time-travel anomalies:

```text
========================================================================
INSERT TRANSACTION:
1. Prevalidation (vertex exists, edge does not exist).
2. Graph Mutation: authoritative_graph.insert_edge(u, v, label).
3. NLF Index Mutation:
     nlf_index.increment(u, sig(label, label_v));
     nlf_index.increment(v, sig(label, label_u));
4. Search Execution: search_event() on G_after using updated nlf_index.
5. Commit: graph.commit(), nlf_index.commit().
   [On Abort]: graph.rollback(), nlf_index.rollback() (decrements counts).
========================================================================
DELETE TRANSACTION:
1. Prevalidation (edge exists).
2. Search Execution: search_event() on G_before using UNMODIFIED nlf_index.
3. Graph Mutation: authoritative_graph.delete_edge(u, v, label).
4. NLF Index Mutation:
     nlf_index.decrement(u, sig(label, label_v));
     nlf_index.decrement(v, sig(label, label_u));
5. Commit: graph.commit(), nlf_index.commit().
   [On Abort]: graph.rollback(), nlf_index.rollback() (increments counts).
========================================================================
```

---

## 7. Option B Architecture: Source-Backed Neighbor Retrieval Audit

### 7.1. Deconstruction of Current Retrieval Bottleneck
In `extensions/rapidflow_apx/src/authoritative_graph.cpp:130-161`, `get_neighbors(uint32_t u)` performs:
1. `std::vector<uint32_t> neighbors;` (Heap allocation).
2. Loop over base CSR neighbors: performs two hash lookups per neighbor (`tombstones_.find()` and `overlay_labels_.find()`), pushing to vector.
3. Loop over dynamic overlay neighbors: pushes to vector.
4. `std::sort(neighbors.begin(), neighbors.end());` ($O(d \log d)$ sorting overhead).
5. `neighbors.erase(std::unique(...), neighbors.end());` (Deduplication).
6. Return by value (Move semantics / copy).

### 7.2. Source-Backed Sortedness Properties
- **Base CSR Sortedness:** `baselines/rapidflow/source/graph/graph.cpp:164` proves base neighbors are strictly sorted by `uint32_t` vertex ID:
  $$\text{base\_nbrs}[0] < \text{base\_nbrs}[1] < \ldots < \text{base\_nbrs}[c-1]$$
- **Overlay Sortedness:** `baselines/rapidflow/source/utility/relation/global_edge_view.h:27-29` proves overlay neighbors are strictly sorted by `uint32_t` vertex ID:
  $$\text{overlay\_nbrs}[0] < \text{overlay\_nbrs}[1] < \ldots < \text{overlay\_nbrs}[o-1]$$
- **Deduplication Invariant:** Filtering base edges via `overlay_labels_` ensures no vertex present in the overlay can also be accepted from the base CSR. Hence:
  $$\text{FilteredBase}(u) \cap \text{Overlay}(u) = \emptyset$$

### 7.3. Proposed API: `get_neighbors_into()`
```cpp
void ApxAuthoritativeGraph::get_neighbors_into(
    uint32_t u,
    std::vector<uint32_t>& out
) const;
```
- **Contract:** Clears `out` (retaining its allocated capacity), populates it with all effective neighbors of `u` in strictly ascending order without duplicates.
- **Fast Path (Empty Overlay & Tombstones):**
  If `tombstones_.empty()` and `fwd_overlay_.get_neighbor_num(u) == 0`:
  ```cpp
  out.assign(base_nbrs, base_nbrs + count);
  return;
  ```
  Executes as a single contiguous memory copy ($O(d_{\text{base}})$) with zero hash lookups and zero sorting.
- **Linear Two-Pointer Merge Path:**
  When overlay or tombstones are present:
  Linear two-pointer merge between the sorted filtered base array and the sorted overlay array in $O(d_{\text{base}} + d_{\text{overlay}})$ time, completely eliminating $O(d \log d)$ sorting.

### 7.4. Reusable Buffer Pool & Recursion Depth Proof
To eliminate heap allocations during DFS:

```text
DFS Frame 0 (Anchor Root)      ---> uses candidate_pool[0]  (size retained)
   |
   +---> DFS Frame 1 (Depth 1) ---> uses candidate_pool[1]  (size retained)
            |
            +---> DFS Frame 2  ---> uses candidate_pool[2]  (size retained)
```

**Theorem 3 (Maximum Recursion Depth Bound):**
*In `dfs_m2`, the maximum recursion depth from the anchor root is bounded by:*
$$\text{Depth}_{\max} = (n - 2) + k$$
*where $n = |V_Q|$ and $k$ is the missing budget.*

**Proof:**
1. The anchor call in `search_event` maps 2 query vertices ($qa, qb$), setting `assigned_count = 2` and `missing_count = 0`.
2. In each step of `dfs_m2`:
   - *PRESENT branch:* Assigns 1 unmapped query vertex, increasing `assigned_count` by 1. Since the search reaches the leaf when `assigned_count == n`, at most $n - 2$ PRESENT transitions can occur along any path.
   - *MISSING branch:* Commits the frontier edge as `MISSING`, increasing `missing_count` by 1 without assigning a vertex. Since recursion requires `missing_count + 1 <= k`, at most $k$ MISSING transitions can occur along any path.
3. Every step selects a frontier edge from the cut with status `UNKNOWN` and transitions it to `PRESENT` or `MISSING`. No edge is ever revisited or reverted along a descent path.
4. Therefore, the maximum number of recursive calls is $(n - 2) + k$.
5. The maximum number of active stack frames (including root) is $F_{\max} = (n - 2) + k + 1$. $\blacksquare$

**Corollaries on Buffer Stability:**
- Pre-allocating `candidate_pool` with $F_{\max}$ vectors indexed by recursion depth $d \in [0, (n-2)+k]$ guarantees that:
  - Frame $d$ accesses `candidate_pool[d]`.
  - Child frame $d+1$ accesses `candidate_pool[d+1]`.
  - Frame $d$'s candidate vector is **never aliased, resized, or overwritten** while child frames execute.
  - The pool is never resized during DFS, guaranteeing iterator and pointer validity.

### 7.5. Grounded Decision on Degree Index
- **Audit Result:** Call-site audit confirms `ApxAuthoritativeGraph::get_degree()` is **never called during M2 search**.
- **Decision:** **REJECT / DEFER** adding an incremental degree index in P4b. Adding an incremental degree index would consume $O(|V_G|)$ memory and add update/rollback overhead on every `INSERT` and `DELETE` for zero benefit to search performance.

---

## 8. Correctness Verification Master Design

### 8.1. Parity Obligations for Option B
Since Option B is purely an algorithmic acceleration of neighbor retrieval without changing candidate ordering or pruning logic:
$$\forall \text{ Workload } W, \quad \text{Counters}_{\text{Option B}}(W) \equiv \text{Counters}_{\text{Bare M2}}(W)$$
$$\forall \text{ Workload } W, \quad \text{Tuples}_{\text{Option B}}(W) \equiv \text{Tuples}_{\text{Bare M2}}(W)$$
Option B must match Bare M2 **bit-for-bit** across all legacy counters: `candidate_evaluations`, `candidate_extensions`, `dfs_states`, `edge_decisions`, `missing_branches`, `commitment_checks`, and emitted tuples.

### 8.2. Equivalence Obligations for Option A
Option A prunes unviable candidate branches early:
- `candidate_evaluations` remains unchanged (evaluated count before filter).
- `candidate_extensions` and `dfs_states` are strictly less than or equal to Bare M2.
- Emitted tuples must match the ground truth `DStarDynamicOracle` and Bare M2 **100% identically**:
  $$\text{Tuples}_{\text{Option A}}(W) \equiv \text{Tuples}_{\text{M0}}(W) \equiv \text{Tuples}_{\text{Bare M2}}(W)$$

### 8.3. Adversarial Test Scenarios (B1–B8)
- **B1 (Tombstoned Base Edge):** Delete base edge $0-1$. Verify $0-1$ is never returned by `get_neighbors_into` and does not cause stale candidate iteration.
- **B2 (Delete/Reinsert Edge):** Delete base edge $0-1$ (label 0), then insert $0-1$ (label 1). Verify exactly one neighbor is returned with updated label.
- **B3 (Overlay-Only Edge):** Insert new edge not present in base CSR. Verify neighbor is returned with zero loss.
- **B4 (Recursive Buffer Aliasing):** Deeply nested DFS with alternating PRESENT/MISSING branches. Verify parent candidate buffer is not mutated by child frames.
- **B5 (Transaction Rollback):** Abort transaction after graph insertion. Verify `get_neighbors_into` immediately reflects pre-transaction graph with zero ghost edges.
- **B6 (High-Degree Hub):** Vertex with 1,000 neighbors. Verify linear merge executes correctly without memory explosion.
- **B7 (Zero-Degree Isolated Vertex):** Vertex with no edges. Verify buffer is cleared and returns count 0 with no stale data.
- **B8 (Buffer Reuse Across Varying Degrees):** Sequence of calls with degree 100, then degree 2, then degree 0. Verify no stale suffix from prior calls leaks into subsequent results.

---

## 9. Workload Design & Suite Stratification

To evaluate optimizations under diverse topological and combinatorial conditions, the P4b benchmark suite is stratified into two complementary suites:

### 9.1. Suite 1: P4a Continuity Suite
Re-evaluates the 30 certified P4a canonical workloads under the P4b execution harness to anchor continuity with historical findings.

### 9.2. Suite 2: P4b Discrimination Suite
Designed specifically to discriminate between Search Core bottlenecks (Option A) and Neighbor Retrieval bottlenecks (Option B) across larger graphs and varied query topologies.

| Group ID | Target Graph Size ($|V_G|$) | Query Topology ($Q$) | Missing Budget ($k$) | Query Connectivity ($\lambda(Q)$) | Valid D-R? | Label Regime | Stream Updates | Primary Stress Target |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **DISC-01** | 16 | Triangle ($n=3, m=3$) | 0, 1 | 2 | Yes ($k < 2$) | Uniform | 32 | Baseline small continuity |
| **DISC-02** | 32 | C4 ($n=4, m=4$) | 0, 1 | 2 | Yes ($k < 2$) | Mixed-V | 50 | Cyclic search without chord |
| **DISC-03** | 32 | Diamond ($n=4, m=5$) | 0, 1 | 2 | Yes ($k < 2$) | Uniform | 50 | Search Core bottleneck (NLF stress) |
| **DISC-04** | 32 | Diamond ($n=4, m=5$) | 0, 1 | 2 | Yes ($k < 2$) | Mixed-V / Mixed-E | 50 | Signature discrimination stress |
| **DISC-05** | 64 | K4 ($n=4, m=6$) | 0, 1, 2 | 3 | Yes ($k < 3$) | Uniform | 64 | Deep recursion ($k=2$) & dense cut |
| **DISC-06** | 64 | House ($n=5, m=6$) | 0, 1 | 2 | Yes ($k < 2$) | Mixed-V | 64 | Asymmetric query frontier |
| **DISC-07** | 64 | Prism-6 ($n=6, m=9$) | 0, 1, 2 | 3 | Yes ($k < 3$) | Uniform | 80 | Larger query size ($n=6$) |
| **DISC-08** | 128 | K4 ($n=4, m=6$) | 0, 1 | 3 | Yes ($k < 3$) | High-churn | 100 | Overlay merge & retrieval stress |
| **DISC-09** | 128 | Scale-free Hub | 0, 1 | 2 | Yes ($k < 2$) | Mixed-V | 100 | High-degree hub buffer reuse |
| **DISC-10** | 64 | Disconnected Churn | 0, 1 | 2 | Yes ($k < 2$) | Fully Mixed | 64 | Negative event / empty search stress |

---

## 10. Experimental & Performance Measurement Protocol

### 10.1. Dual-Mode Measurement Architecture
To eliminate observer interference from high-frequency timer instrumentation:
1. **Primary Performance Run (`PROFILE_OFF`):**
   - Micro-stage timers are completely disabled at compile-time/run-time.
   - Measures only macro timings: **Streaming Loop Wall Time**, **Subprocess Outer Wall Time**, **Total CPU Time**, and **Peak RSS**.
   - Primary speedup metric is evaluated strictly under `PROFILE_OFF`.
2. **Attribution Run (`PROFILE_ON`):**
   - Nanosecond instrumentation enabled for: Search Core, Neighbor Retrieval, NLF Check, Index Update, and Tuple Commit.
   - Used exclusively to explain *where* gains or regressions occurred.

### 10.2. Speedup Ratio Convention
Across all reports, the speedup ratio $S$ is defined consistently as:
$$S = \frac{T_{\text{Bare M2}}}{T_{\text{Variant}}}$$
- $S > 1.0$: Variant is faster than Bare M2.
- $S = 1.0$: Performance parity.
- $S < 1.0$: Variant is slower than Bare M2 (regression).

### 10.3. Execution Protocol & Randomization
- **Blocked Paired Execution:** To prevent environmental drift, variants are executed in interleaved randomized blocks:
  $$\text{Block } b: \quad \text{RandomPermute}(\text{Bare M2}, \text{M2+A}, \text{M2+B}, \text{M2+A+B})$$
- **Repetitions:** $R = 5$ repetitions per workload per variant.
- **Statistical Aggregation:**
  - *Experimental Unit:* The individual workload $W$.
  - *Within-Workload Aggregation:* Median of the 5 repeated runs: $\widetilde{T}(W) = \text{median}_{r=1..5}(T_r(W))$.
  - *Paired Speedup for Workload $W$:* $S(W) = \widetilde{T}_{\text{Bare M2}}(W) / \widetilde{T}_{\text{Variant}}(W)$.
  - *Across-Workload Metric:* Geometric mean and median of $\{S(W_i)\}_{i=1}^N$ with 95% non-parametric bootstrap confidence intervals (1,000 resamples).
- **Censoring Handling:** Runs exceeding resource limits are explicitly recorded with status (`TIMEOUT`, `OOM_CENSORED`, `WORK_BUDGET_EXCEEDED`). Censored runs are never discarded silently or replaced with fake zero timings.

---

## 11. Decision Criteria (GO / NO-GO)

The decision to retain, combine, or reject optimizations is governed by pre-registered thresholds:

```text
                           CORRECTNESS GATE
                       (Zero tuple discrepancy)
                                  |
                +-----------------+-----------------+
                | PASS                              | FAIL
                v                                   v
        PERFORMANCE AUDIT                        REJECT
   (PROFILE_OFF Paired Speedup)                 (UNSOUND)
                |
    +-----------+-----------+-----------+
    |                       |           |
    v                       v           v
Median S >= 1.15x       0.95 <= S < 1.15x   Median S < 0.95x
CI_lower > 1.0          (Parity/Marginal)   (Regression)
Memory overhead <= 20%      |                   |
    |                       v                   v
    v                    RETAIN               REJECT
ADVANTAGE_OBSERVED       BARE M2             (NO-GO)
```

1. **Gate 1: Correctness (Zero Tolerance):**
   Any missing, extra, or duplicate tuple compared to $M_0$ or Bare M2 results in immediate classification as `UNSOUND` and disqualification.
2. **Gate 2: Meaningful Performance Advantage:**
   - Classification `ADVANTAGE_OBSERVED` requires:
     - Target-stratum median paired speedup $S \ge 1.15\times$ (at least 15% net speedup).
     - 95% bootstrap confidence interval lower bound $> 1.00\times$.
     - Peak RSS increase $\le 20\%$ relative to Bare M2.
3. **Gate 3: Query-Dependent Advantage:**
   If an optimization provides $S \ge 1.15\times$ on specific topological strata (e.g. dense queries) but regresses on others, it is classified as `QUERY_DEPENDENT`. A selector must be formally specified.
4. **Gate 4: Parity / Regression (NO-GO):**
   If $0.95 \le S < 1.15$, the optimization is classified as `PARITY` or marginal. If $S < 0.95$, it is classified as `REGRESSION`. In both cases, Bare M2 is retained as the default.

---

## 12. Proposed Implementation Roadmap

```text
Phase P4b-0: Design & Protocol Specification (CURRENT - Complete)
     |
     +---> Gate: Project Coordinator Review & Approval
     |
Phase P4b-1: Option A Implementation & Unit Verification
     |      |-- P4b-1A: ApxQueryPlan Signatures + Static Relaxed NLF Filter
     |      +-- P4b-1B: Conservative Residual NLF Filter + Dynamic ApxNlfIndex
     |
Phase P4b-2: Option B Implementation & Unit Verification
     |      |-- P4b-2A: get_neighbors_into + Depth-Indexed Reusable Buffer Pool
     |      +-- P4b-2B: Linear Two-Pointer Merge & Empty Fast Path
     |
Phase P4b-3: Comparative Benchmark & Attribution Analysis
     |      |-- Isolated Paired Benchmarks (Bare M2 vs M2+A vs M2+B)
     |      +-- Statistical Analysis & Verification against Decision Matrix
     |
Phase P4b-4: Conditional Combined Ablation (M2+A+B)
     |      (Executed ONLY if both A and B pass Gates 1 and 2 independently)
     |
Phase R4b: Final Research Synthesis & Decision Record
            (Handoff to Phase P5: NetFlow & Scaling)
```

---

## 13. Risk Register

| Risk ID | Severity | Trigger / Condition | Consequence | Mitigation Strategy | Verification Method |
| :--- | :---: | :--- | :--- | :--- | :--- |
| **RSK-01** | **HIGH** | Residual NLF double-counts committed missing edges | False negatives (prunes valid approximate embeddings) | Subtract $|I_Q^{\text{miss}}(u, \sigma)|$ from query requirements before deficit computation (Theorem 2) | Adversarial test A2; 100% M0 oracle cross-validation |
| **RSK-02** | **MEDIUM** | Dense NLF table memory explosion on large graphs | Out-of-memory crash during initialization | Verify memory limit ($B_{\text{dense}} \le 32 \text{ MB}$ for $10^6$ vertices); disable NLF if budget exceeded | Memory scaling unit test; RLIMIT_AS enforcement |
| **RSK-03** | **HIGH** | Child DFS frame mutates parent candidate buffer | Invalid candidate iteration; corrupted embeddings | Index buffer pool strictly by recursion depth $d \in [0, (n-2)+k]$ (Theorem 3) | Adversarial test B4; memory address assertion |
| **RSK-04** | **MEDIUM** | Base CSR neighbors assumed sorted but unsorted | Corrupted two-pointer merge; missing neighbors | Verified upstream source (`graph.cpp:164`); include fallback sort if unsorted | Unit test verifying two-pointer merge parity against `std::sort` |
| **RSK-05** | **MEDIUM** | Transaction rollback allocates heap memory during `bad_alloc` | Uncaught exception; process termination | All index and graph rollbacks use `noexcept` in-place decrements | Rollback fault-injection test (`M2FaultHook`) |
| **RSK-06** | **LOW** | Micro-stage profiler instrumentation distorts timing | False bottleneck identification | Enforce dual-mode evaluation: `PROFILE_OFF` for speedup, `PROFILE_ON` for attribution | Observer overhead validation study |

---

## 14. Proof Obligation Register

| Obligation ID | Formal Statement | Key Assumptions | Proof Status | Primary Evidence |
| :--- | :--- | :--- | :---: | :--- |
| **OBL-01** | Static Relaxed NLF deficit is a lower bound on incident missing edges | Injective mapping; typed vertex labels | **PROVEN** | Theorem 1 (Section 5.3) |
| **OBL-02** | Conservative Residual NLF does not double-count committed missing edges | $M_{\text{cur}} \subseteq R_G(f)$; disjoint signature partition | **PROVEN** | Theorem 2 (Section 5.4) |
| **OBL-03** | Max DFS recursion depth is strictly bounded by $(n - 2) + k$ | Domain D-R ($k < \lambda(Q)$); non-empty cut | **PROVEN** | Theorem 3 (Section 7.4) |
| **OBL-04** | Base CSR neighbor array is strictly sorted by vertex ID | Pristine RapidFlow `loadGraphFromMemory` | **PROVEN** | `graph.cpp:164` source audit |
| **OBL-05** | Dynamic overlay neighbor vector is strictly sorted by vertex ID | Pristine RapidFlow `GlobalEdgeView::insert` | **PROVEN** | `global_edge_view.h:27` source audit |
| **OBL-06** | Two-pointer merge produces exact deduplicated union of base and overlay | Deduplication invariant ($\text{Base} \cap \text{Overlay} = \emptyset$) | **PROVEN** | Section 7.3 merge logic |
| **OBL-07** | Option B preserves exact legacy counter parity against Bare M2 | Deterministic traversal order preserved | **SPECIFIED** | Section 8.1 parity obligation |
| **OBL-08** | Transaction rollback on NLF index executes with zero allocation | Pre-allocated dense array; bounded decrements | **PROVEN** | Section 6.4 rollback design |
| **OBL-09** | Option A preserves all valid embeddings for $k=0$ (exact matching) | Deficit $> 0 \iff$ missing neighbor exists | **PROVEN** | Section 5.5 (A6 analysis) |
| **OBL-10** | Domain D-R condition holds for all proposed discrimination workloads | Calculated edge connectivity $\lambda(Q) > k$ | **VERIFIED** | Section 9.2 workload matrix |

---

## 15. Design Decision Record (DDR)

| Component / Mechanism | Action | Rationale | Concrete Evidence |
| :--- | :---: | :--- | :--- |
| **Dense 1D Vector for `ApxNlfIndex`** | **KEEP** | Guarantees $O(1)$ lookup, optimal cache locality, zero allocation during streaming, and zero-allocation `noexcept` rollback. | Section 6.2 trade-off analysis; consumes only 1.6 MB for $10^5$ vertices. |
| **Conservative Residual NLF** | **KEEP** | Provides provably sound candidate pruning during DFS without double-counting previously committed missing edges. | Theorem 2 (Section 5.4) and Counterexample A2 resolution. |
| **Depth-Indexed Reusable Candidate Pool** | **KEEP** | Completely eliminates per-call `std::vector` heap allocation during DFS while guaranteeing zero parent buffer aliasing. | Theorem 3 (Section 7.4) depth bound proof. |
| **Sorted Two-Pointer Neighbor Merge** | **KEEP** | Replaces $O(d \log d)$ sort with $O(d)$ linear merge by exploiting source-backed sortedness in base CSR and dynamic overlay. | Source audits of `graph.cpp:164` and `global_edge_view.h:27`. |
| **Standalone Degree Index in P4b** | **REJECT** | `get_degree()` is never called on the M2 search hot path. An index adds update overhead and memory for zero search gain. | Call-site audit across the entire codebase (Section 7.5). |
| **Direct Combined A+B Implementation** | **DEFER** | Compounding optimizations before establishing isolated soundness and efficacy prevents scientific attribution of gains. | Section 12 roadmap; requires Gate 1 & 2 pass on A and B. |
| **Multi-Vertex Residual Deficit Aggregation** | **DEFER** | Summing residual deficits across multiple frontier vertices requires complex cut-edge disjointness proofs; deferred to P5. | Section 5.5 boundary limitations. |
| **Dynamic Frontier Ordering (Option C)** | **DEFER** | Frontier ordering alterations change candidate emission sequence and work-budget charging; retained as backup. | Section 1.3 fallback strategy. |

---

## 16. Open Questions for Project Coordinator

The following targeted decisions are submitted to the Project Coordinator prior to implementing Phase P4b-1:
1. **Approval of Dense NLF Index Architecture:** Do you approve the dense 1D vector representation (`table_[v * num_sig + sig]`) as the primary data-side NLF structure for P4b-1, with an automatic fallback to Bare M2 if graph vertex count exceeds memory limits?
2. **Approval of Discrimination Suite Scope:** Do you approve the proposed 10-group Discrimination Suite ($|V_G| \in [16, 128]$, $|V_Q| \in [3, 6]$, $k \in [0, 2]$) to serve as the benchmark workload matrix for P4b-3?
3. **Approval of Primary Decision Threshold ($S \ge 1.15\times$):** Do you approve the pre-registered 15% net speedup threshold ($S \ge 1.15\times$) with 95% bootstrap confidence interval lower bound $> 1.00\times$ as the criterion for classifying an optimization as `ADVANTAGE_OBSERVED`?
4. **Authorization to Proceed to P4b-1:** Following review of this design document, is authorization granted to open Phase P4b-1 (Implementation of Option A)?

---

## 17. Final P4b-0 Verdict

Based on formal proofs of soundness, source-backed architectural verification of upstream sortedness, zero-allocation transaction designs, and rigorous experimental protocols:

$$\mathbf{P4B\_DESIGN\_READY\_FOR\_REVIEW}$$

The design is fully specified, reviewable, and ready for independent verification by the Project Coordinator and Technical Lead.
