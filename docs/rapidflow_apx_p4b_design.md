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
**Design Starting Commit:** `94325e14c7b8165eb1a9b50262ee497a6a3bfb92`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)
**Status:** COMPLETE & READY FOR COORDINATOR REVIEW (REVISION R1)
**Proposed Design Verdict:** `P4B_DESIGN_READY_FOR_REVIEW`

---

## 1. Executive Summary

Phase P4b-0 formalizes the mathematical principles, architectural specifications, correctness proofs, and experimental methodology for the evidence-driven optional optimization of the Bare M2 native event engine (`ApxM2EventEngine`) within RapidFlow-APX.

### 1.1. Context & Accepted P4a Empirical Foundation
Phase P4a completed native profiling of Bare M2 against the enumeration baseline M1 across 180 canonical measurement slots (30 workloads $\times$ 3 repetitions $\times$ 2 engines = 90 paired observations), achieving 100% agreement with the independent Python ground truth oracle (`DStarDynamicOracle`) and zero duplicate embeddings. The certified findings established:
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
4. **Baseline Outer Speedup Parity:**
   - Overall baseline outer wall speedup median was **1.0004x** (strictly `PARITY`).

### 1.2. P4b Research Mission
The central research question of Phase P4b is:
> *Can the computational cost of Bare M2 be significantly reduced without losing any valid approximate event embeddings, violating transaction atomicity, or inflating memory overhead beyond practical bounds?*

To address this question scientifically, Phase P4b investigates two mutually independent candidate optimization avenues directly derived from empirical profiling:
- **Option A (Sound Relaxed NLF):** Pruning unviable candidate exploration branches early during search by establishing provably sound lower bounds on missing edges from incident neighbor label frequencies (targeting the 77.22% Search Core bottleneck).
- **Option B (Neighbor Retrieval Optimization):** Reusing pre-allocated buffer capacity across search iterations, eliminating repeated per-call heap allocation in the common recursion path (reallocation occurs only on initial allocation or when degree exceeds pre-allocated capacity), and exploiting upstream sortedness via linear two-pointer merging (targeting the 22.78% Neighbor Retrieval bottleneck).

### 1.3. Scientific & Governance Boundaries
In strict adherence to repository governance (`AGENTS.md`, `GEMINI.md`, and `docs/BRANCH_POLICY.md`):
- **Design Gate Restriction:** P4b-0 is strictly a design and formalization gate. **No production C++ optimization code is implemented in this phase**, and no new benchmark measurements are executed.
- **Pristine Upstream:** The upstream RapidFlow submodule at `baselines/rapidflow/source` remains pristine at pinned commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`.
- **Domain Invariance:** The formal problem definition (Domain D-R: $0 \le k < \lambda(Q)$, injective matching, hard vertex label matching, event leaf condition $|R_G(f)| = k$, and explicit ordered tuple output) is strictly invariant.
- **Honest Fallback (Option D):** If neither Option A nor Option B demonstrates a statistically significant net performance advantage over Bare M2 under fair experimental conditions, Bare M2 remains the primary engine of RapidFlow-APX (**Option D: Safe Fallback**). A negative result is a scientifically valid and acceptable outcome.

---

## 2. Source & Evidence Inventory

### 2.1. Codebase & Document Artifact Audit
The P4b-0 design is grounded in comprehensive audit of existing production components and historical empirical ledgers:
1. `extensions/rapidflow_apx/src/authoritative_graph.cpp`:
   - `apply_insert(uint32_t u, uint32_t v, uint32_t label, bool simulate_rev_fail = false)` (lines 239-298): Transactional topological insertion with strong exception guarantee.
   - `apply_delete(uint32_t u, uint32_t v, uint32_t label)` (lines 300-343): Transactional topological deletion.
   - `get_neighbors(uint32_t u)` (lines 130-161): Base CSR retrieval, tombstone filtering, overlay collection, `std::sort`, and `std::unique`.
   - `get_degree(uint32_t u)` (lines 163-165): Currently calls `get_neighbors(u).size()`, incurring full neighbor materialization overhead.
2. `extensions/rapidflow_apx/src/transaction_manager.cpp`:
   - `execute_update(const UpdateOperation& update, SearchCallback callback)` (lines 57-240): Coordinates transaction lifecycle, fault injection points, mutation application, and tuple sink commitment.
3. `extensions/rapidflow_apx/src/streaming_engine.cpp`:
   - `execute_step(const UpdateOperation& update, uint32_t step_index)` (lines 24-52): Outer stream loop step driver invoking transaction manager.
4. `baselines/rapidflow/source/graph/graph.cpp`:
   - `loadGraphFromFile` / memory load (line 164): Executes `std::sort(neighbors_ + offsets_[i], neighbors_ + offsets_[i + 1])`, proving base CSR neighbor lists are strictly sorted by vertex ID. Upstream assumes simple graph inputs (no multi-edges).
5. `baselines/rapidflow/source/utility/relation/global_edge_view.h`:
   - `GlobalEdgeView::insert` (lines 27-29): Maintains dynamic overlay adjacency arrays in strictly sorted order.
6. `docs/evidence/p4a_evidence_manifest.json` (SHA-256: `b6d7777d5b17c10db8e5f00f5c40885a99b9fefb06242c3c05bbd1064a9d70be`):
   - Certified P4a canonical measurement records (180 total records, 90 paired comparisons).

### 2.2. Critical Source Findings
- **API Realities:** Production code uses `apply_insert` and `apply_delete` on `ApxAuthoritativeGraph`, `execute_update` on `ApxTransactionManager`, and `execute_step` on `ApxStreamingEngine`. Earlier informal terms (`insert_edge`, `delete_edge`, `execute_insert`, `execute_delete`) do not exist and are eliminated.
- **Sortedness vs Uniqueness:** Upstream `Graph::loadGraphFromFile` guarantees that base CSR neighbors are strictly sorted by `VertexID`. Multi-edge deduplication is not performed by upstream because input streams are contractually simple graphs. `ApxAuthoritativeGraph` deduplicates against overlay replacements via `overlay_labels_` map lookup.
- **Snapshot Visibility Realities:** In `ApxTransactionManager::execute_update`, for an `INSERT` operation, `graph_.apply_insert()` is executed *before* the search callback, meaning search operates on $G_{\text{after}}$. For a `DELETE` operation, search callback is executed *before* `graph_.apply_delete()`, meaning search operates on $G_{\text{before}}$. Any dynamic index must respect this exact asymmetry.

---

## 3. Formal Problem Contract

### 3.1. Problem Entities
- **Query Graph $Q = (V_Q, E_Q, \ell_V^Q, \ell_E^Q)$:** Connected, undirected, labeled graph with $|V_Q| = n$, $|E_Q| = m$, and edge connectivity $\lambda(Q) > k$.
- **Data Graph $G = (V_G, E_G, \ell_V^G, \ell_E^G)$:** Dynamic undirected labeled graph maintained in `ApxAuthoritativeGraph`.
- **Stream Update $\Delta = (\text{op}, u, v, \ell_E)$:** Edge insertion or deletion with $\text{op} \in \{\text{INSERT}, \text{DELETE}\}$.
- **Missing Edge Budget $k$:** Integer $0 \le k < \lambda(Q)$.
- **Candidate Embedding $f: V_Q \to V_G$:** Injective mapping ($f(u) = f(v) \implies u = v$) satisfying exact vertex label compatibility ($\forall u \in V_Q, \ell_V^G(f(u)) = \ell_V^Q(u)$).
- **Missing Edge Set:**
  $$R_G(f) = \{\{u, w\} \in E_Q : \{f(u), f(w)\} \notin E_G \lor \ell_E^G(f(u), f(w)) \neq \ell_E^Q(u, w)\}$$
- **Approximate Embedding:** Any injective mapping $f$ such that $|R_G(f)| \le k$.

### 3.2. Event Boundaries & Output Contract
- **Trigger Condition:** Embedding $f$ is an *event embedding* for stream update $\Delta = (\text{op}, u_0, v_0, \ell_e)$ if and only if:
  1. For $\text{op} = \text{INSERT}$: $f$ is valid in $G_{\text{after}}$ ($|R_{G_{\text{after}}}(f)| \le k$) and involves the newly inserted edge, i.e., $\exists \{qa, qb\} \in E_Q$ such that $\{f(qa), f(qb)\} = \{u_0, v_0\}$ and $\ell_E^Q(qa, qb) = \ell_e$.
  2. For $\text{op} = \text{DELETE}$: $f$ was valid in $G_{\text{before}}$ ($|R_{G_{\text{before}}}(f)| \le k$) and involves the deleted edge.
- **Leaf Output Invariant:** In Phase P4b, as established in P4a, search operates under Domain D-R where event leaf candidates satisfy $|R_G(f)| = k$.
- **Explicit Tuple Output:** Every verified event embedding is emitted to `ApxTupleSink` as an ordered tuple of vertex IDs $(f(0), f(1), \ldots, f(n-1))$. Count-only matching is strictly rejected.

---

## 4. Bare M2 Native Architecture & Invariants

### 4.1. Call Graph Architecture
The execution pipeline flows through well-defined transactional and search boundaries:

```text
ApxStreamingEngine::execute_step(update, step_index)
  |
  +--> ApxTransactionManager::execute_update(update, callback)
         |
         +--> [INSERT] ApxAuthoritativeGraph::apply_insert(u, v, label)
         |
         +--> SearchCallback -> ApxM2EventEngine::search(anchor_edge, update)
         |      |
         |      +--> ApxM2EventEngine::search_event(qa, qb, u, v)
         |             |
         |             +--> ApxM2EventEngine::dfs_m2(depth, assigned_count, missing_count)
         |                    |
         |                    +--> ApxAuthoritativeGraph::get_neighbors(curr_v)
         |                    +--> Candidate Filtering & Commitment Extension
         |                    +--> Recurse (PRESENT or MISSING)
         |                    +--> On Leaf: ApxTupleSink::push_tuple(f)
         |
         +--> [DELETE] ApxAuthoritativeGraph::apply_delete(u, v, label)
         |
         +--> ApxTransactionScope::commit()
         +--> ApxTupleSink::commit_step() -> Returns published tuples
```

### 4.2. Invariant Specifications
The native architecture enforces six mandatory system invariants:

| Invariant ID | Target Component | Formal Specification | Violation Action |
| :--- | :--- | :--- | :--- |
| **INV-01** | Transaction Scope | Graph mutation, search execution, and sink staging occur inside a single `ApxTransactionScope`. | Immediate rollback; no tuples published. |
| **INV-02** | Snapshot Isolation | INSERT search observes $G_{\text{after}}$ exclusively; DELETE search observes $G_{\text{before}}$ exclusively. | Fail prevalidation; reject dirty read. |
| **INV-03** | Injection Invariance | $\forall u \neq w \in V_Q, f(u) \neq f(w)$. Maintained via `visited_data_` bitset. | Candidate rejected; branch pruned. |
| **INV-04** | Label Invariance | $\forall u \in V_Q, \ell_V^G(f(u)) = \ell_V^Q(u)$ strictly. | Candidate rejected; zero missing allowance. |
| **INV-05** | Budget Invariance | At any DFS stack frame: $\text{missing\_count} \le k$. | Immediate branch cutoff; do not recurse. |
| **INV-06** | Sink Isolation | Uncommitted tuples reside in staging buffers; committed only if `execute_update` succeeds. | Clear staging buffer on transaction abort. |

---

## 5. Option A: Sound Relaxed NLF Theory & Formal Proofs

### 5.1. The Failure of Exact NLF in Approximate Matching
In exact subgraph matching ($k=0$), Neighbor Label Frequency (NLF) filtering enforces that for a data vertex $v \in V_G$ to match a query vertex $u \in V_Q$, $v$ must have at least as many neighbors of each label type as $u$:
$$\forall \sigma \in \Sigma_Q(u): \quad N_G(v, \sigma) \ge N_Q(u, \sigma)$$
In approximate subgraph matching ($k > 0$), applying Exact NLF is **catastrophically unsound**. A query edge incident to $u$ may be missing in $G$, causing $N_G(v, \sigma) < N_Q(u, \sigma)$ even when $v$ belongs to a perfectly valid approximate embedding (Counterexample A1). Exact NLF prunes such valid vertices, producing false negatives.

### 5.2. Formal Definition of Typed Incident Signatures
Let $\Sigma$ denote the universe of edge-vertex label pairs.
For an undirected edge $e = \{u, w\} \in E_Q$ with edge label $\ell_E^Q(e)$ and endpoint labels $\ell_V^Q(u), \ell_V^Q(w)$:
- From the perspective of endpoint $u$, the incident signature is:
  $$\sigma_{e, u} = (\ell_E^Q(e), \ell_V^Q(w))$$
- From the perspective of endpoint $w$, the incident signature is:
  $$\sigma_{e, w} = (\ell_E^Q(e), \ell_V^Q(u))$$
Notice that if $\ell_V^Q(u) \neq \ell_V^Q(w)$, then $\sigma_{e, u} \neq \sigma_{e, w}$.

The set of incident query edges at $u$ matching signature $\sigma$ is:
$$I_Q(u, \sigma) = \{e = \{u, w\} \in E_Q : \sigma_{e, u} = \sigma\}, \quad N_Q(u, \sigma) = |I_Q(u, \sigma)|$$
Similarly, for data vertex $v \in V_G$:
$$I_G(v, \sigma) = \{e = \{v, w\} \in E_G : (\ell_E^G(e), \ell_V^G(w)) = \sigma\}, \quad N_G(v, \sigma) = |I_G(v, \sigma)|$$

The active query signature set for the entire query is:
$$\Sigma_Q = \bigcup_{\{u, w\} \in E_Q} \{ (\ell_E^Q(u,w), \ell_V^Q(w)), \; (\ell_E^Q(u,w), \ell_V^Q(u)) \}$$
Notice that $|\Sigma_Q| \le 2 |E_Q|$.

---

### 5.3. Theorem 1: Soundness of Static Relaxed NLF

**Definition (Static Local Deficit):**
$$\delta_{\text{static}}(u, v) = \sum_{\sigma \in \Sigma_Q(u)} \max\left(0, \; N_Q(u, \sigma) - N_G(v, \sigma)\right)$$

**Theorem 1:** *Let $f: V_Q \to V_G$ be any injective candidate embedding with $f(u) = v$ satisfying exact vertex label compatibility. Then:*
$$|R_G(f)| \ge \delta_{\text{static}}(u, v)$$

**Proof:**
1. Consider the set of query edges incident to $u$: $I_Q(u) = \bigcup_{\sigma} I_Q(u, \sigma)$.
2. By definition of $R_G(f)$, a query edge $e = \{u, w\} \in I_Q(u, \sigma)$ belongs to $R_G(f)$ if and only if $\{v, f(w)\} \notin E_G$ or $\ell_E^G(v, f(w)) \neq \ell_E^Q(e)$.
3. The number of edges in $I_Q(u, \sigma)$ successfully matched in $G$ cannot exceed the number of available incident edges in $G$ with signature $\sigma$:
   $$\left| I_Q(u, \sigma) \setminus R_G(f) \right| \le N_G(v, \sigma)$$
4. Therefore, the number of missing edges in $I_Q(u, \sigma)$ is bounded below by:
   $$\left| I_Q(u, \sigma) \cap R_G(f) \right| = |I_Q(u, \sigma)| - |I_Q(u, \sigma) \setminus R_G(f)| \ge N_Q(u, \sigma) - N_G(v, \sigma)$$
5. Since the count of missing edges cannot be negative:
   $$\left| I_Q(u, \sigma) \cap R_G(f) \right| \ge \max\left(0, \; N_Q(u, \sigma) - N_G(v, \sigma)\right)$$
6. Because the sets $I_Q(u, \sigma)$ are mutually disjoint for distinct signatures $\sigma$:
   $$|R_G(f)| \ge |I_Q(u) \cap R_G(f)| = \sum_{\sigma \in \Sigma_Q(u)} |I_Q(u, \sigma) \cap R_G(f)| \ge \sum_{\sigma \in \Sigma_Q(u)} \max\left(0, \; N_Q(u, \sigma) - N_G(v, \sigma)\right) = \delta_{\text{static}}(u, v)$$
7. Thus, if $\delta_{\text{static}}(u, v) > k$, then $|R_G(f)| > k$. Pruning $v$ is **strictly sound**; no valid embedding can map $u$ to $v$. $\blacksquare$

---

### 5.4. Theorem 2: Soundness of Conservative Residual Relaxed NLF

During depth-first search, some query edges have already been committed as `PRESENT` or `MISSING`:
$$M_{\text{cur}} = \{e \in E_Q : \text{status}[e] = \text{MISSING}\}, \quad m_{\text{cur}} = |M_{\text{cur}}|$$

**The Double-Counting Pitfall:** Adding $m_{\text{cur}} + \delta_{\text{static}}(u, v) > k$ is **unsound**. If an edge $e \in I_Q(u)$ was already committed as `MISSING`, it contributed $1$ to $m_{\text{cur}}$, and its absence in $G$ may also create a deficit in $\delta_{\text{static}}(u, v)$. Adding both counts that single missing edge twice, causing false negatives (Counterexample A2).

**Definition (Committed Missing Incident Set):**
$$I_Q^{\text{miss}}(u, \sigma) = \{e \in I_Q(u, \sigma) : \text{status}[e] = \text{MISSING}\}$$

**Definition (Conservative Residual Deficit):**
$$\delta_{\text{res}}^{\text{cons}}(u, v) = \sum_{\sigma \in \Sigma_Q(u)} \max\left(0, \; N_Q(u, \sigma) - |I_Q^{\text{miss}}(u, \sigma)| - N_G(v, \sigma)\right)$$

**Theorem 2:** *Let $f: V_Q \to V_G$ be any completion consistent with current commitments (i.e. $M_{\text{cur}} \subseteq R_G(f)$ and $\text{status}[e] = \text{PRESENT} \implies e \notin R_G(f)$). If $f(u) = v$, then:*
$$m_{\text{cur}} + \delta_{\text{res}}^{\text{cons}}(u, v) \le |R_G(f)| \le k$$

**Proof:**
1. Decompose the total missing set into committed missing edges and uncommitted missing edges:
   $$R_G(f) = M_{\text{cur}} \;\dot\cup\; \left( R_G(f) \setminus M_{\text{cur}} \right)$$
   Hence, $|R_G(f)| = |M_{\text{cur}}| + |R_G(f) \setminus M_{\text{cur}}| = m_{\text{cur}} + |R_G(f) \setminus M_{\text{cur}}|$.
2. Consider uncommitted query edges incident to $u$ with signature $\sigma$:
   $$I_Q^{\text{uncommitted}}(u, \sigma) = I_Q(u, \sigma) \setminus M_{\text{cur}}$$
   Its cardinality is $|I_Q^{\text{uncommitted}}(u, \sigma)| = N_Q(u, \sigma) - |I_Q^{\text{miss}}(u, \sigma)|$.
3. Any edge $e \in I_Q^{\text{uncommitted}}(u, \sigma)$ matched in $G$ maps to an available $\sigma$-neighbor of $v$ in $G$. Thus:
   $$\left| I_Q^{\text{uncommitted}}(u, \sigma) \setminus R_G(f) \right| \le N_G(v, \sigma)$$
4. The number of additional missing edges in $I_Q^{\text{uncommitted}}(u, \sigma)$ is bounded below by:
   $$\left| I_Q^{\text{uncommitted}}(u, \sigma) \cap R_G(f) \right| \ge \max\left(0, \; N_Q(u, \sigma) - |I_Q^{\text{miss}}(u, \sigma)| - N_G(v, \sigma)\right)$$
5. Since $I_Q^{\text{uncommitted}}(u, \sigma) \subseteq E_Q \setminus M_{\text{cur}}$, these additional missing edges are completely disjoint from $M_{\text{cur}}$.
6. Summing over all signatures $\sigma \in \Sigma_Q(u)$ gives a lower bound on missing edges in $E_Q \setminus M_{\text{cur}}$:
   $$\left| (E_Q \setminus M_{\text{cur}}) \cap R_G(f) \right| \ge \sum_{\sigma \in \Sigma_Q(u)} \left| I_Q^{\text{uncommitted}}(u, \sigma) \cap R_G(f) \right| \ge \delta_{\text{res}}^{\text{cons}}(u, v)$$
7. Adding $|M_{\text{cur}}| = m_{\text{cur}}$ yields:
   $$|R_G(f)| = m_{\text{cur}} + \left| (E_Q \setminus M_{\text{cur}}) \cap R_G(f) \right| \ge m_{\text{cur}} + \delta_{\text{res}}^{\text{cons}}(u, v)$$
8. Thus, if $m_{\text{cur}} + \delta_{\text{res}}^{\text{cons}}(u, v) > k$, then $|R_G(f)| > k$. Pruning is **strictly sound** and provably avoids double-counting. $\blacksquare$

---

### 5.5. Rigorous Counterexample Analysis (A1–A7)

```text
[A1: Exact NLF False Negative & Independent Oracle Verification]
Query Q (Diamond, k=1):             Data Graph G (C4 after insert 1-2):
        0 (deg 2)                              0 (deg 2)
       / \                                    / \
      1---2 (deg 3)                          1   2 (deg 2)
       \ /                                    \ /
        3 (deg 2)                              3 (deg 2)
Exact NLF: Query v1 has deg 3 > all G vertices (deg 2) -> Prunes (0 embeddings, WRONG!)
Static Relaxed NLF: Delta_static = max(0, 3 - 2) = 1 <= k=1 -> Preserves (0,1,2,3) (SOUND!)
Verified M0 Oracle Run: Edge insert (1, 2) emits 8 ENTERED events; inverse delete emits 8 EXITED events.

[A2: Residual Double-Counting Pitfall]
At DFS state: Edge 1-2 committed MISSING (m_cur = 1).
Examining vertex 1:
Naive bound: m_cur + Delta_static = 1 + 1 = 2 > k=1 -> Prunes (0,1,2,3) (WRONG!)
Conservative Residual: I_miss = 1 -> Remaining = 3 - 1 = 2 -> Delta_res = max(0, 2 - 2) = 0.
Combined: m_cur + Delta_res = 1 + 0 = 1 <= k=1 -> Preserves (0,1,2,3) (SOUND!)
```

- **Counterexample A1 (Exact NLF False Negative & Independent Verification):**
  - *Setup:* Query $Q$ is Diamond ($V_Q=\{0,1,2,3\}$, edges $0-1, 0-2, 1-2, 1-3, 2-3$, all labels 0, $\lambda(Q)=2, k=1$). Data graph $G$ is $C_4$ (edges $0-1, 0-2, 1-3, 2-3$, missing only chord $1-2$).
  - *Exact NLF:* At query vertex 1 (degree 3), every data vertex has degree 2. Exact NLF rejects all mappings. Result: 0 embeddings emitted (false negative).
  - *Static Relaxed NLF:* $N_Q(1, \sigma)=3, N_G(v, \sigma)=2$. Deficit $\delta_{\text{static}}(1, v) = \max(0, 3 - 2) = 1$. Since $1 \le k=1$, candidate is NOT pruned.
  - *Independent Oracle Check:* M0 Oracle execution on this exact setup emits **8 ENTERED** events when chord edge $(1, 2)$ is inserted, and exactly **8 EXITED** events when chord edge $(1, 2)$ is inversely deleted. Net active embeddings: $8 - 8 = 0$. This confirms both mathematical soundness and dynamic event symmetry.
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

### 6.1. Numeric Representation & Overflow Safety
To eliminate numeric truncation and wrapping defects:
- **Representation Type:** The NLF frequency counter is defined as:
  ```cpp
  using NlfCount = uint32_t;
  ```
- **Justification for `uint32_t` over `uint16_t`:**
  `uint16_t` has a maximum representable value of $65,535$. In dense graph datasets or bursty streams, high-degree hubs can readily exceed $65,535$ incident edges matching a single query signature. If `uint16_t` is used:
  - If arithmetic wraps around ($65,536 \to 0$, $70,000 \to 4,464$), a vertex with high available degree will report an artificially tiny count, creating a large false deficit ($\delta > k$) and causing false-negative pruning (violating Theorem 1).
  - If arithmetic saturates at $65,535$, subsequent edge deletions cannot accurately decrement (decrementing from $65,535$ when true degree was $70,000$ leaves the count at $65,534$ instead of $69,999$, producing corrupt state).
  `uint32_t` safely counts up to $4,294,967,295$, well beyond any physical graph memory limits on 64-bit systems.

### 6.2. Checked Arithmetic Specifications
All NLF index operations enforce strict checked arithmetic guards:
1. **Checked Increment (Fail-Closed):**
   ```cpp
   inline void checked_increment(NlfCount& val) {
       if (val == std::numeric_limits<NlfCount>::max()) {
           throw ApxNlfOverflowException("NLF frequency counter overflowed UINT32_MAX");
       }
       ++val;
   }
   ```
2. **Checked Decrement (Underflow Invariant):**
   ```cpp
   inline void checked_decrement(NlfCount& val) {
       if (val == 0) {
           throw ApxNlfUnderflowException("NLF frequency counter underflowed below 0 on decrement");
       }
       --val;
   }
   ```
3. **Guarded Deficit Computation:**
   ```cpp
   inline uint32_t compute_deficit(uint32_t req, uint32_t avail, uint32_t k_rem) noexcept {
       const uint32_t effective_req = (k_rem < req) ? (req - k_rem) : 0;
       return (effective_req > avail) ? (effective_req - avail) : 0;
   }
   ```

### 6.3. Checked Allocation Size & Memory Scaling
The data-side frequency table is laid out as a contiguous dense 1D vector:
```cpp
std::vector<NlfCount> table_;
```
Cell index calculation:
$$\text{index}(v, \text{sig\_id}) = v \times |\Sigma_Q| + \text{sig\_id}$$
Before allocation, checked multiplication guards against `size_t` overflow:
```cpp
size_t total_cells = (size_t)max_vertices * (size_t)num_signatures;
if (max_vertices != 0 && total_cells / max_vertices != num_signatures) {
    throw ApxNlfOverflowException("Allocation size overflow in NLF table sizing");
}
```

#### Recalculated Dense Memory Scaling Table:
Dense table memory is strictly $|V_G| \times |\Sigma_Q| \times \text{sizeof(uint32_t)} = |V_G| \times |\Sigma_Q| \times 4$ bytes:

| Data Graph Vertices ($|V_G|$) | Signatures ($|\Sigma_Q| = 4$) | Signatures ($|\Sigma_Q| = 8$) | Signatures ($|\Sigma_Q| = 16$) | Signatures ($|\Sigma_Q| = 32$) | Memory Feasibility |
| :---: | :---: | :---: | :---: | :---: | :---: |
| $1,000$ | 15.62 KiB | 31.25 KiB | 62.50 KiB | 125.00 KiB | $\ll 0.01\%$ |
| $10,000$ | 156.25 KiB | 312.50 KiB | 625.00 KiB | 1.22 MiB | $< 0.01\%$ |
| $100,000$ | 1.53 MiB | 3.05 MiB | 6.10 MiB | 12.21 MiB | $0.60\%$ of 512 MiB |
| $1,000,000$ | 15.26 MiB | 30.52 MiB | 61.04 MiB | 122.07 MiB | $5.96\%$ of 512 MiB |
| **$3,114,895$ (NetFlow)** | **47.53 MiB** | **95.06 MiB** | **190.12 MiB** | **380.24 MiB** | **18.57% of 512 MiB** |

**NetFlow Estimate Verification:** For the certified NetFlow dataset with $|V_G| = 3,114,895$ and a typical query with $|\Sigma_Q| = 8$, the exact table size is $3,114,895 \times 8 \times 4 = 99,676,640$ bytes $\approx 95.06$ MiB. This sits comfortably below the default 512 MiB budget limit.

### 6.4. Memory Budget & Transparent Fallback
To protect system stability:
- Configurable budget limit: `max_nlf_memory_mb` (default 512 MB).
- **Pre-Allocation Fallback:** If `total_cells * sizeof(uint32_t) > max_nlf_memory_mb * 1024 * 1024`, the engine does NOT allocate the table and does NOT crash. Instead, it disables NLF optimization and falls back to Bare M2 (`NLF_MEMORY_BUDGET_EXCEEDED`).
- **Transparency:** The run metadata explicitly records:
  ```json
  {
    "requested_optimization": "option_a_nlf",
    "effective_optimization": "bare_m2_fallback",
    "fallback_reason": "NLF_MEMORY_BUDGET_EXCEEDED",
    "estimated_memory_mb": 620.5,
    "memory_budget_mb": 512.0
  }
  ```

---

### 6.5. Bidirectional Query Signature Registry
Given query edge $e = \{u, w\} \in E_Q$ with label $\ell_E^Q(e)$ and vertex labels $\ell_V^Q(u), \ell_V^Q(w)$:
- Directed signature from $u$: $\sigma_{e, u} = (\ell_E^Q(e), \ell_V^Q(w))$
- Directed signature from $w$: $\sigma_{e, w} = (\ell_E^Q(e), \ell_V^Q(u))$

The query plan precomputes:
1. `std::vector<std::pair<uint32_t, uint32_t>> distinct_signatures;` mapping dense `sig_id` $\in [0, |\Sigma_Q|-1]$.
2. For each query vertex $u$:
   `std::vector<std::pair<uint32_t, uint32_t>> query_sig_reqs[u];` containing pairs `(sig_id, required_count)`.
3. For fast lookups during stream updates:
   A lookup table mapping `(edge_label, neighbor_vertex_label) -> optional<sig_id>`.

---

### 6.6. Ownership, Lifetime & Transaction Architecture
- **Ownership:** `ApxNlfIndex` is owned by `ApxStreamingEngine` (or `ApxM2EventEngine` query execution context). The upstream `Graph` and `ApxAuthoritativeGraph` classes remain completely decoupled and unaware of NLF.
- **Lifetime:** Allocated upon query registration / streaming session start; destroyed with the engine.
- **Authoritative Initialization:**
  At startup, the table is initialized by iterating over all edges in `ApxAuthoritativeGraph`:
  ```cpp
  void ApxNlfIndex::initialize_from_graph(const ApxAuthoritativeGraph& graph) {
      table_.assign(max_vertices_ * num_signatures_, 0);
      for (uint32_t u = 0; u < graph.get_vertices_count(); ++u) {
          uint32_t count = 0;
          const auto* nbrs = graph.get_base_graph()->getVertexNeighbors(u, count);
          for (uint32_t i = 0; i < count; ++i) {
              uint32_t v = nbrs[i];
              if (u < v) { // Undirected: process each edge once
                  auto [has_lbl, lbl] = graph.get_edge_label(u, v);
                  if (has_lbl) apply_insert_edge(u, v, lbl);
              }
          }
      }
  }
  ```

---

### 6.7. Fixed-Capacity Undo Record & Transaction Sequences
To eliminate dynamic heap allocation during transaction execution:
```cpp
struct NlfUndoEntry {
    size_t cell_index = 0;
    NlfCount old_value = 0;
    bool applied = false;
};

struct NlfUndoRecord {
    std::array<NlfUndoEntry, 2> entries{};
    uint8_t count = 0;

    void record_mutation(size_t cell_idx, NlfCount old_val) noexcept {
        entries[count++] = {cell_idx, old_val, true};
    }
};
```

**Rollback Principle:** Rollback restores exact recorded `old_value` without relying on inverse arithmetic:
```cpp
void ApxNlfIndex::rollback_mutations(const NlfUndoRecord& undo) noexcept {
    for (int i = undo.count - 1; i >= 0; --i) {
        if (undo.entries[i].applied) {
            table_[undo.entries[i].cell_index] = undo.entries[i].old_value;
        }
    }
}
```

#### Transaction Execution Sequences:
1. **INSERT Sequence (INSERT-after snapshot):**
   - Step 1: Prevalidation (vertex bounds, edge non-existence).
   - Step 2: Prepare signatures $\sigma_{uv}$ and $\sigma_{vu}$. If neither matches $\Sigma_Q$, NLF is unaffected.
   - Step 3: Base graph mutation: `graph_.apply_insert(u, v, label)`.
   - Step 4: NLF mutation:
     - Mutate cell $u$: record `undo.record_mutation(idx_u, table_[idx_u])`, then `checked_increment(table_[idx_u])`.
     - Mutate cell $v$: record `undo.record_mutation(idx_v, table_[idx_v])`, then `checked_increment(table_[idx_v])`.
   - Step 5: Search callback runs on $G_{\text{after}}$ observing updated NLF.
   - Step 6: Commit transaction. (On abort: rollback NLF using `undo`, then rollback graph).
2. **DELETE Sequence (DELETE-before snapshot):**
   - Step 1: Prevalidation (edge exists).
   - Step 2: Search callback runs on $G_{\text{before}}$ observing UNMODIFIED NLF.
   - Step 3: Base graph mutation: `graph_.apply_delete(u, v, label)`.
   - Step 4: NLF mutation:
     - Mutate cell $u$: record `undo.record_mutation(idx_u, table_[idx_u])`, then `checked_decrement(table_[idx_u])`.
     - Mutate cell $v$: record `undo.record_mutation(idx_v, table_[idx_v])`, then `checked_decrement(table_[idx_v])`.
   - Step 5: Commit transaction. (On abort: rollback NLF using `undo`, then rollback graph).

---

### 6.8. Complete Failure Matrix (F01–F17)

| Failure ID | Phase | Trigger Event | NLF State | Graph State | Recovery Action | Post-Recovery Invariant |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **F01** | Prevalidation | Vertex ID $\ge V_{\max}$ | Pristine | Pristine | Abort step; emit PREVALIDATION_FAILED | Zero state drift |
| **F02** | Prevalidation | Edge already exists on INSERT | Pristine | Pristine | Abort step; emit PREVALIDATION_FAILED | Zero state drift |
| **F03** | Prevalidation | Edge does not exist on DELETE | Pristine | Pristine | Abort step; emit PREVALIDATION_FAILED | Zero state drift |
| **F04** | INSERT Graph | `apply_insert` returns false | Pristine | Partially inserted | Graph reverts overlay; emit STEP_FAILURE | Graph clean, NLF untouched |
| **F05** | INSERT Graph | `apply_insert` throws `bad_alloc` | Pristine | Partially inserted | Graph reverts overlay; rethrow / abort | Graph clean, NLF untouched |
| **F06** | INSERT NLF | Vertex $u$ NLF increment overflows | Cell $u$ pristine | Graph mutated | Graph `apply_delete`; emit STEP_FAILURE | Graph clean, NLF untouched |
| **F07** | INSERT NLF | Vertex $v$ NLF increment overflows | Cell $u$ modified, $v$ pristine | Graph mutated | Rollback $u$ from undo; Graph `apply_delete` | Exact state restoration |
| **F08** | INSERT Search | Callback aborts (returns false) | Both modified | Graph mutated | Rollback $u, v$ from undo; Graph `apply_delete` | Exact state restoration |
| **F09** | INSERT Search | Work budget exceeded in DFS | Both modified | Graph mutated | Rollback $u, v$ from undo; Graph `apply_delete` | Exact state restoration |
| **F10** | INSERT Sink | `sink.commit_step` throws | Both modified | Graph mutated | Rollback $u, v$ from undo; Graph `apply_delete` | Zero published tuples |
| **F11** | DELETE Search | Callback aborts (returns false) | Pristine | Pristine | Abort step; emit CALLBACK_ABORTED | Zero state drift |
| **F12** | DELETE Search | Work budget exceeded in DFS | Pristine | Pristine | Abort step; emit WORK_BUDGET_EXCEEDED | Zero state drift |
| **F13** | DELETE Graph | `apply_delete` returns false | Pristine | Mutated | Graph rollback; emit STEP_FAILURE | Zero state drift |
| **F14** | DELETE Graph | `apply_delete` throws `bad_alloc` | Pristine | Mutated | Graph rollback; rethrow / abort | Zero state drift |
| **F15** | DELETE NLF | Vertex $u$ NLF underflows | Cell $u$ pristine | Graph deleted | Graph `apply_insert`; emit STEP_FAILURE | Exact state restoration |
| **F16** | DELETE NLF | Vertex $v$ NLF underflows | Cell $u$ decremented, $v$ pristine | Graph deleted | Rollback $u$ from undo; Graph `apply_insert` | Exact state restoration |
| **F17** | DELETE Sink | `sink.commit_step` throws | Both decremented | Graph deleted | Rollback $u, v$ from undo; Graph `apply_insert` | Zero published tuples |

---

## 7. Option B Architecture: Source-Backed Neighbor Retrieval Audit

### 7.1. Deconstruction of Current Retrieval Bottleneck
In `extensions/rapidflow_apx/src/authoritative_graph.cpp:130-161`, `get_neighbors(uint32_t u)` performs:
1. `std::vector<uint32_t> neighbors;` (Heap allocation on every invocation).
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
- **Safe Pointer Range Check:**
  ```cpp
  if (base_graph_ == nullptr || !has_vertex(u)) {
      out.clear();
      return;
  }
  ```
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
To reuse buffer capacity across search iterations:
```cpp
std::vector<std::vector<uint32_t>> candidate_pool;
```
The pool is sized to $F_{\max}$ vectors at search initialization.

**Theorem 3 (Maximum Recursion Depth Bound):**
*In `dfs_m2`, the maximum recursion depth from the anchor root is bounded by:*
$$\text{Depth}_{\max} = (n - 2) + k$$
*where $n = |V_Q|$ and $k$ is the missing budget.*

**Proof:**
1. The anchor call in `search_event` maps 2 query vertices ($qa, qb$), setting `assigned_count = 2` and `missing_count = 0`.
2. In each step of `dfs_m2`:
   - *PRESENT branch:* Assigns 1 unmapped query vertex, increasing `assigned_count` by 1. Since search reaches leaf when `assigned_count == n`, at most $n - 2$ PRESENT transitions can occur along any path.
   - *MISSING branch:* Commits the frontier edge as `MISSING`, increasing `missing_count` by 1 without assigning a vertex. Since recursion requires `missing_count + 1 <= k`, at most $k$ MISSING transitions can occur along any path.
3. Every step selects a frontier edge from the cut with status `UNKNOWN` and transitions it to `PRESENT` or `MISSING`. No edge is ever revisited or reverted along a descent path.
4. Therefore, the maximum number of recursive calls is $(n - 2) + k$.
5. The maximum number of active stack frames (including root) is $F_{\max} = (n - 2) + k + 1$. $\blacksquare$

**Corollaries on Buffer Ownership & Stability:**
- Frame $d$ exclusively owns `candidate_pool[d]`.
- Frame $d+1$ exclusively owns `candidate_pool[d+1]`.
- Frame $d$'s candidate buffer is **never aliased, resized, or overwritten** while child frames execute.
- Scratchpad capacity is retained across search calls, eliminating repeated per-call heap allocation in the common recursion path. Allocation occurs only on initial sizing or if vertex degree exceeds pre-allocated scratchpad capacity.

### 7.5. Grounded Decision on Standalone Degree Index
Audit of `authoritative_graph.cpp:163-165` shows `get_degree(u)` is currently implemented as `get_neighbors(u).size()`.
However, `get_degree(u)` is **never invoked on the hot path of Bare M2 DFS**. DFS directly retrieves neighbor lists.
Introducing a standalone degree index in P4b would require additional maintenance during streaming updates for zero search benefit. Therefore, the standalone degree index is **firmly REJECTED**.

---

## 8. Correctness Verification Master Design

### 8.1. Legacy Counter Dictionary
To ensure continuity with Phase P3 and P4a, counter accounting is strictly preserved:

| Counter Name | Exact Definition | Option B Expectation | Option A Expectation |
| :--- | :--- | :--- | :--- |
| `candidate_evaluations` | Count of data vertices tested for candidate compatibility at any DFS step | $\equiv \text{Bare M2}$ | $\le \text{Bare M2}$ (Pruned branches reduce evaluations) |
| `candidate_extensions` | Count of candidates passing all filters and pushed to DFS stack | $\equiv \text{Bare M2}$ | $\le \text{Bare M2}$ |
| `dfs_states` | Total number of DFS stack frames entered | $\equiv \text{Bare M2}$ | $\le \text{Bare M2}$ |
| `edge_decisions` | Count of frontier edge decisions made (PRESENT or MISSING) | $\equiv \text{Bare M2}$ | $\le \text{Bare M2}$ |
| `missing_branches` | Count of MISSING edge transitions explored | $\equiv \text{Bare M2}$ | $\le \text{Bare M2}$ |
| `commitment_checks` | Count of back-edge consistency checks performed | $\equiv \text{Bare M2}$ | $\le \text{Bare M2}$ |

### 8.2. Actual P3 Test Suite Registries
Verification in Phase P4b builds upon the actual historical test inventories from Phase P3:
1. `configs/rapidflow_apx_required_cases.json`: **25 foundational test cases** verifying engine parity.
2. `tests/fixtures/dstar03/workloads/`: **56 stream workload cases** (40 synthetic + 16 NetFlow, total 672 streaming steps).
3. `tests/fixtures/dstar02/corpus_840/seed1_cases.jsonl`: **840 corpus cases** verifying topological diversity.

### 8.3. Correctness Evidence Hierarchy
Evidence gathered during verification is classified across four strict tiers:
- **Level 1 (Independent Oracle Certification):** 100% tuple-level agreement with the independent Python ground truth oracle `DStarDynamicOracle`.
- **Level 2 (Constructed Expected Results):** Analytic test cases with known, mathematically derived embedding sets.
- **Level 3 (Differential Equivalence):** Exact parity across candidate counters and emitted tuples against Bare M2 (`Tuples_Opt == Tuples_BareM2`).
- **Level 4 (Unverified / Censored):** Incomplete or timed-out runs; must never be claimed as valid evidence.

---

## 9. Workload Design & Suite Stratification

### 9.1. Concrete Query Registry
To guarantee mathematical validity under Domain D-R, all benchmark queries satisfy edge connectivity $\lambda(Q) > k$ for $k=1$:

| Query Name | $|V_Q|$ | $|E_Q|$ | Min-Cut $\lambda(Q)$ | Explicit Edge List | Verified $\lambda(Q) > 1$? |
| :--- | :---: | :---: | :---: | :--- | :---: |
| **Triangle ($C_3$)** | 3 | 3 | 2 | `(0,1), (1,2), (0,2)` | Yes |
| **4-Cycle ($C_4$)** | 4 | 4 | 2 | `(0,1), (1,2), (2,3), (0,3)` | Yes |
| **Diamond ($K_4 - e$)** | 4 | 5 | 2 | `(0,1), (0,2), (0,3), (1,2), (2,3)` | Yes |
| **House** | 5 | 6 | 2 | `(0,1), (1,2), (2,3), (3,0), (2,4), (3,4)` | Yes |
| **Complete Graph $K_4$** | 4 | 6 | 3 | `(0,1), (0,2), (0,3), (1,2), (1,3), (2,3)` | Yes |
| **Triangular Prism** | 6 | 9 | 3 | `(0,1), (1,2), (0,2), (3,4), (4,5), (3,5), (0,3), (1,4), (2,5)` | Yes |

### 9.2. Concrete Discrimination Workload Registry (DISC-01 to DISC-10)
Workload groups cleanly disentangle query topology from data stream topology:

| Group ID | Target Graph ($|V_G|$) | Data Stream Model | Query ($Q$) | $k$ | Stream Length | Edge Churn Ratio | Primary Target |
| :---: | :---: | :--- | :--- | :---: | :---: | :---: | :--- |
| **DISC-01** | 16 | Sparse Erdős-Rényi ($p=0.2$) | Triangle | 0, 1 | 32 | 100% Ins | Small graph baseline |
| **DISC-02** | 32 | Random Regular ($d=4$) | C4 | 0, 1 | 50 | 100% Ins | Chordless cycle search |
| **DISC-03** | 32 | Dense Erdős-Rényi ($p=0.4$) | Diamond | 0, 1 | 50 | 100% Ins | Search Core stress (NLF) |
| **DISC-04** | 32 | Mixed-Label Cluster Graph | Diamond | 0, 1 | 50 | 80% Ins / 20% Del | Signature discrimination |
| **DISC-05** | 64 | Dense Erdős-Rényi ($p=0.35$) | K4 | 0, 1 | 64 | 100% Ins | Deep recursion ($k=1$) |
| **DISC-06** | 64 | Two-Cluster Bridge Graph | House | 0, 1 | 64 | 75% Ins / 25% Del | Asymmetric query frontier |
| **DISC-07** | 64 | Dense Erdős-Rényi ($p=0.3$) | Triangular Prism | 0, 1 | 80 | 100% Ins | Larger query size ($n=6$) |
| **DISC-08** | 128 | High-Churn Random Graph | K4 | 0, 1 | 100 | 50% Ins / 50% Del | Overlay merge & retrieval |
| **DISC-09** | 128 | Scale-Free Power-Law (Barabási-Albert) | K4 | 0, 1 | 100 | 80% Ins / 20% Del | Hub vertex buffer reuse |
| **DISC-10** | 64 | Adversarial Deficit Churn Stream | Triangle | 0, 1 | 64 | 50% Ins / 50% Del | Negative event / empty search |

### 9.3. Workload & Measurement Slot Reconciliation
- **Suite 1 (P4a Continuity):** 30 workloads $\times$ 3 repetitions = 90 observation slots per engine.
- **Suite 2 (P4b Discrimination):** 10 workloads $\times$ 3 repetitions = 30 observation slots per engine.
- **Total per engine:** $90 + 30 = 120$ observation slots.
- **Feasibility Limits:** Stream lengths are capped at 32–100 updates to ensure ground truth oracle feasibility ($\le 30$ seconds per slot).

---

## 10. Experimental & Performance Measurement Protocol

### 10.1. Dual-Mode Measurement Architecture
To eliminate observer interference from high-frequency profiling hooks:
1. **Macro Timing Mode (`PROFILE_OFF`):**
   - High-frequency micro-stage timers are completely disabled.
   - Measures only macro timings: `streaming_loop_wall_ns`, `outer_wall_ns`, CPU time, and peak RSS.
   - All primary performance evaluation and decision thresholds are evaluated strictly under `PROFILE_OFF`.
2. **Detailed Diagnostic Mode (`PROFILE_ON`):**
   - Detailed micro-stage timings: Search Core, Neighbor Retrieval, NLF Check, Index Update, and Tuple Commit.
   - Used exclusively for post-hoc attribution and bottleneck verification.

### 10.2. Speedup Metric & Ratio Convention
Across all benchmarks, Speedup $S$ is consistently defined as:
$$S = \frac{T_{\text{Bare M2}}}{T_{\text{Opt}}}$$
- $S > 1.0$: Optimization is faster than Bare M2.
- $S = 1.0$: Performance parity.
- $S < 1.0$: Optimization is slower than Bare M2 (regression).

### 10.3. Statistical Aggregation & Bootstrap Specification
- **Experimental Unit:** The individual workload $W$.
- **Within-Workload Aggregation:** Median across the 3 repeated runs: $\widetilde{T}(W) = \text{median}_{r=1..3}(T_r(W))$.
- **Paired Workload Speedup:** $S(W) = \widetilde{T}_{\text{Bare M2}}(W) / \widetilde{T}_{\text{Opt}}(W)$.
- **Across-Workload Metric:** Median and geometric mean of $\{S(W_i)\}$ with 95% non-parametric BCa bootstrap confidence intervals (1,000 resamples).
- **Censoring:** Any run exceeding 60 seconds or 4 GB RSS is flagged (`TIMEOUT` / `OOM_CENSORED`) and never replaced with fake zero values.

---

## 11. Decision Criteria (GO / NO-GO)

```text
                           CORRECTNESS GATE
                  (100% agreement with Oracle & Bare M2)
                                  |
                +-----------------+-----------------+
                | PASS                              | FAIL
                v                                   v
        PERFORMANCE AUDIT                        REJECT
   (PROFILE_OFF Streaming Loop)                 (UNSOUND)
                |
    +-----------+-----------+-----------+
    |                       |           |
    v                       v           v
Median S >= 1.15x       0.95 <= S < 1.15x   Median S < 0.95x
CI_lower > 1.05         (Parity/Marginal)   (Regression)
Memory RSS <= +20%          |                   |
    |                       v                   v
    v                    RETAIN               REJECT
ADVANTAGE_OBSERVED       BARE M2             (NO-GO)
```

1. **Gate 1: Correctness (Zero Discrepancy):**
   Any missing, extra, or duplicate tuple compared to oracle or Bare M2 results in immediate rejection (`UNSOUND`).
2. **Gate 2: Meaningful Advantage (`ADVANTAGE_OBSERVED`):**
   Requires:
   - Median paired speedup $S \ge 1.15\times$ (at least 15% net speedup).
   - 95% bootstrap confidence interval lower bound $> 1.05\times$.
   - Peak RSS increase $\le 20\%$.
3. **Gate 3: Parity / Marginal:**
   - `PARITY`: $S \in [0.95, 1.05]$.
   - `MARGINAL`: $S \in [1.05, 1.15)$.
   In both cases, Bare M2 is retained as primary engine (Option D).
4. **Gate 4: Regression (`NO-GO`):**
   $S < 0.95\times$ with statistical significance. Optimization is rejected.
5. **Gate 5: Query-Dependent Advantage (`QUERY_DEPENDENT`):**
   $S \ge 1.15\times$ on specific topological strata (e.g. dense queries) but regression on others.

---

## 12. Conditional Combined A+B Gate

In strict alignment with Research Plan Section 1.2:
The combined variant (Option A + Option B) will **ONLY** be implemented and evaluated if:
1. Option A passes the 100% correctness gate.
2. Option B passes the 100% correctness gate.
3. At least **ONE** variant demonstrates a meaningful measured benefit ($S \ge 1.05\times$) without memory explosion.
4. Combining them addresses complementary bottlenecks (Option A on Search Core, Option B on Neighbor Retrieval).
5. Formal written proposal submitted to and approved by the Project Coordinator.

---

## 13. Proposed Implementation Roadmap

```text
Phase P4b-0: Design & Protocol Specification (CURRENT - Complete Revision R1)
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
     |      (Executed ONLY if Gates 1-5 of Section 12 are satisfied)
     |
Phase R4b: Final Research Synthesis & Decision Record
            (Handoff to Phase P5: NetFlow & Scaling)
```

---

## 14. Risk Register

| Risk ID | Severity | Trigger / Condition | Consequence | Mitigation Strategy | Verification Method |
| :--- | :---: | :--- | :--- | :--- | :--- |
| **RSK-01** | **HIGH** | Residual NLF double-counts committed missing edges | False negatives (prunes valid embeddings) | Conservative Residual formulation (Theorem 2) | Counterexample A2 unit tests; oracle comparison |
| **RSK-02** | **HIGH** | NLF table frequency counter overflows | Counter wraps to 0 or saturates, producing false deficits | `uint32_t` counter type + checked arithmetic | Arithmetic overflow test suite (V02) |
| **RSK-03** | **HIGH** | Transaction abort after partial NLF mutation | NLF index state desynchronized from graph | Fixed-capacity 2-entry undo record with exact old-value restoration | Transaction failure matrix tests (F01–F17) |
| **RSK-04** | **MEDIUM** | Dense NLF table memory exceeds RAM on massive graphs | Out-of-memory crash | Checked multiplication + memory budget fallback | Memory scaling audit; NetFlow estimate (V03) |
| **RSK-05** | **MEDIUM** | Query min-cut $\le k$ | Graph becomes disconnected; leaf condition fails | Enforce $\lambda(Q) > k$ for all queries | Query registry validation (V07) |
| **RSK-06** | **LOW** | Diagnostic profiler distorts streaming timings | Skewed speedup ratios | Primary speedup evaluated strictly under `PROFILE_OFF` | Dual-mode measurement protocol (Section 10.1) |

---

## 15. Proof Obligation Register

| Obligation ID | Claim / Invariant | Preconditions | Status | Supporting Section |
| :--- | :--- | :--- | :---: | :--- |
| **OBL-01** | Static Relaxed NLF produces zero false negatives | Domain D-R; exact vertex label matching | **PROVEN** | Theorem 1 (Section 5.3) |
| **OBL-02** | Conservative Residual Relaxed NLF avoids double-counting | Single-vertex incident residual bound | **PROVEN** | Theorem 2 (Section 5.4) |
| **OBL-03** | Max DFS recursion depth is strictly bounded by $(n - 2) + k$ | Domain D-R ($k < \lambda(Q)$); non-empty cut | **PROVEN** | Theorem 3 (Section 7.4) |
| **OBL-04** | Base CSR neighbor array is strictly sorted by vertex ID | Pristine RapidFlow `loadGraphFromFile` | **PROVEN** | `graph.cpp:164` source audit |
| **OBL-05** | Dynamic overlay neighbor vector is strictly sorted by vertex ID | Pristine RapidFlow `GlobalEdgeView::insert` | **PROVEN** | `global_edge_view.h:27` source audit |
| **OBL-06** | Two-pointer merge produces exact deduplicated union | Deduplication invariant ($\text{Base} \cap \text{Overlay} = \emptyset$) | **PROVEN** | Section 7.3 merge logic |
| **OBL-07** | Option B preserves exact legacy counter parity against Bare M2 | Deterministic traversal order preserved | **SPECIFIED** | Section 8.1 parity obligation |
| **OBL-08** | Transaction rollback on NLF index executes with zero allocation | Fixed-capacity 2-entry array; old-value restore | **PROVEN** | Section 6.7 rollback design |
| **OBL-09** | Option A preserves all valid embeddings for $k=0$ (exact matching) | Deficit $> 0 \iff$ missing neighbor exists | **PROVEN** | Section 5.5 (A6 analysis) |
| **OBL-10** | Domain D-R condition holds for all proposed discrimination workloads | Calculated edge connectivity $\lambda(Q) > k$ | **VERIFIED** | Section 9.1 & 9.2 workload matrix |

---

## 16. Design Decision Record (DDR)

| Component / Mechanism | Action | Rationale | Concrete Evidence |
| :--- | :---: | :--- | :--- |
| **`uint32_t` for NLF Counter** | **SELECT** | Prevents arithmetic overflow/wrapping on hub vertices ($>65,535$), ensuring exact counts and avoiding false-negative pruning. | Section 6.1 analysis; safe up to $4.29 \times 10^9$. |
| **Dense 1D Vector for `ApxNlfIndex`** | **SELECT** | Guarantees $O(1)$ lookup, optimal cache locality, zero allocation during streaming, and zero-allocation rollback. | Section 6.3 trade-off analysis; consumes only 95 MiB on NetFlow. |
| **Fixed-Capacity Undo Record (2 Entries)** | **SELECT** | Each edge update affects at most 2 cells ($u$ and $v$); array avoids heap allocation and guarantees exact rollback even under partial failure. | Section 6.7 rollback specification. |
| **Conservative Residual NLF** | **KEEP** | Provides provably sound candidate pruning during DFS without double-counting previously committed missing edges. | Theorem 2 (Section 5.4) and Counterexample A2 resolution. |
| **Depth-Indexed Reusable Candidate Pool** | **KEEP** | Reuses pre-allocated capacity, eliminating repeated heap allocations during DFS while guaranteeing zero parent buffer aliasing. | Theorem 3 (Section 7.4) depth bound proof. |
| **Sorted Two-Pointer Neighbor Merge** | **KEEP** | Replaces $O(d \log d)$ sort with $O(d)$ linear merge by exploiting source-backed sortedness in base CSR and dynamic overlay. | Source audits of `graph.cpp:164` and `global_edge_view.h:27`. |
| **Standalone Degree Index in P4b** | **REJECT** | `get_degree()` is never called on the M2 search hot path. An index adds update overhead and memory for zero search gain. | Call-site audit across the entire codebase (Section 7.5). |
| **Direct Combined A+B Implementation** | **DEFER** | Compounding optimizations before establishing isolated soundness and efficacy prevents scientific attribution of gains. | Section 12 roadmap; requires Gate 1 & 2 pass on A and B. |

---

## 17. Open Questions for Project Coordinator

The following targeted decisions are submitted to the Project Coordinator prior to implementing Phase P4b-1:
1. **Approval of Dense NLF Index Architecture:** Do you approve the `uint32_t` dense 1D vector representation (`table_[v * num_sig + sig]`) as the primary data-side NLF structure for P4b-1, with an automatic fallback to Bare M2 if graph vertex count exceeds memory limits?
2. **Approval of Discrimination Suite Scope:** Do you approve the proposed 10-group Discrimination Suite ($|V_G| \in [16, 128]$, $|V_Q| \in [3, 6]$, $k \in [0, 1]$) to serve as the benchmark workload matrix for P4b-3?
3. **Approval of Primary Decision Threshold ($S \ge 1.15\times$):** Do you approve the pre-registered 15% net speedup threshold ($S \ge 1.15\times$) with 95% bootstrap confidence interval lower bound $> 1.05\times$ as the criterion for classifying an optimization as `ADVANTAGE_OBSERVED`?
4. **Authorization to Proceed to P4b-1:** Following review of this design document Revision R1, is authorization granted to open Phase P4b-1 (Implementation of Option A)?

---

## 18. Final P4b-0 Verdict

Based on formal proofs of soundness, source-backed architectural verification of upstream sortedness, zero-allocation transaction designs, and rigorous experimental protocols:

$$\mathbf{P4B\_DESIGN\_READY\_FOR\_REVIEW}$$

The design is fully specified, reviewable, and ready for independent verification by the Project Coordinator and Technical Lead.
