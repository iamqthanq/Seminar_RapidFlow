# RapidFlow-APX Native Engines Design Specification (Phase P3)

**Document ID:** `docs/rapidflow_apx_native_engines_design.md`
**Milestone:** D-INT-03 / Phase P3
**Parent Issue:** Issue #32
**Target Integration Branch:** `QuangThang`
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Status:** IMPLEMENTED_AND_VERIFIED — PENDING INDEPENDENT ACCEPTANCE

---

## 1. Executive Summary & Design Goals

This document specifies the architecture, component design, semantic contracts, algorithmic flows, and mathematical proofs for **Phase P3** of the RapidFlow-APX continuous subgraph matching (CSM) system.

In Phase P2 (Issue #30, PR #31 merged), we established the native foundational infrastructure:
1. `ApxTypedCodec`: Bidirectional mapping for signed 64-bit external IDs and typed labels (`int64` vs `string`), with zero-allocation `noexcept` checkpoint rollback.
2. `ApxAuthoritativeGraph`: Hybrid CSR (`Graph::loadGraphFromMemory`) baseline and mutable `GlobalEdgeView` overlay with dynamic edge labels and tombstones.
3. `ApxTransactionManager`: ACID transaction executor enforcing snapshot sequencing and atomic multi-point fault rollback.
4. `ApxTupleSink`: Deterministic output sink emitting canonical ordered tuples $(f(u_1), \dots, f(u_n))$ sorted by ascending original query vertex IDs.

In **Phase P3**, we implement and integrate two native C++ event search engines operating directly on the shared P2 foundation:
1. **`ApxM1EnumEngine` (Native M1-enum-Exact-$k$)**: Decomposes approximate delta search around the update edge into exact subgraph matching across all $\binom{m-1}{k}$ connected variant queries $Q - D$, strictly filtering embeddings by $R(f) = D$.
2. **`ApxM2EventEngine` (Native Bare M2-Event-$k$)**: Direct depth-first search on $Q$ anchored at the update edge, branching on PRESENT vs MISSING edges, deferring unmapped vertices, and enforcing the $|R(f)| = k$ event boundary lemma.
3. **`ApxStreamingEngine`**: High-level coordinator linking the transaction manager, selected search engine, and tuple sink into an ACID streaming pipeline.
4. **`rapidflow_apx_native`**: Standalone native worker CLI executing structured JSON workloads under strict containment.

Both engines are verified bit-for-bit against the independent Python Ground Truth Oracle (M0) across an 840-case correctness corpus, a 56-stream dynamic matrix (672 transitions), and specialized witness and fault suites.

---

## 2. Semantic Contract & Mathematical Foundations

### 2.1. Graph and Query Definitions
- **Query Graph $Q = (V_Q, E_Q)$**: Simple, undirected, connected graph with $n = |V_Q| \ge 2$, $m = |E_Q| \ge 1$.
  - Vertices have immutable typed labels $\ell_Q(u) \in \Sigma_V$ (`int64` or `std::string`).
  - Edges have immutable typed labels $\ell_Q(e) \in \Sigma_E$ (`int64` or `std::string`).
  - Edge connectivity $\lambda(Q)$ is computed exactly using maximum flow / minimum cut algorithms; no metadata is trusted.
- **Data Graph $G_t = (V_0, E_{G,t})$**: Simple, undirected graph evolving over discrete time steps $t \in \mathbb{N}_0$.
  - Fixed vertex universe $V_0$: vertices are never inserted or deleted; $|V_0|$ is constant.
  - Vertices have immutable typed labels $\ell_G(v) \in \Sigma_V$.
  - Edges have dynamic typed labels $\ell_G(e) \in \Sigma_E$.
- **Mapping $f: V_Q \to V_0$**:
  - Strictly injective: $\forall u \ne v \in V_Q, f(u) \ne f(v)$.
  - Hard vertex label match: $\forall u \in V_Q, \ell_G(f(u)) = \ell_Q(u)$.
  - Non-induced: extraneous edges in $G_t$ do not invalidate a match.
- **Missing Edge Set $R(f)$**:
  $$R(f) = \left\{ \{u, v\} \in E_Q \mid \{f(u), f(v)\} \notin E_{G,t} \lor \ell_G(\{f(u), f(v)\}) \ne \ell_Q(\{u, v\}) \right\}$$
  An edge in $G$ with an incorrect label is treated as missing.
- **Budget $k$ and Domain D-R**:
  $$0 \le k < \lambda(Q)$$
  When an external threshold $\tau \in (0, 1]$ is provided, $k = m - \lceil \tau \cdot m \rceil$ is evaluated via exact rational arithmetic. Any case with $k \ge \lambda(Q)$ (Domain D-G) is rejected fail-closed.

### 2.2. Event Boundary Lemma & Snapshot Sequencing
For any single-edge update $\Delta_t = (\text{op}, u, v, l)$ where $e_{\text{upd}} = \{u, v\}$:
1. **INSERT (`+`)**:
   - Evaluated on $G_{\text{after}} = G_{t-1} \cup \{e_{\text{upd}}\}$.
   - A mapping $f$ enters the valid set ($\Delta \mathcal{M} = \mathcal{M}_t \setminus \mathcal{M}_{t-1}$) if and only if:
     $$|R_{G_{\text{after}}}(f)| = k \quad \text{and} \quad e_{\text{upd}} \in f(E_Q \setminus R_{G_{\text{after}}}(f))$$
     (i.e., $e_{\text{upd}}$ matches some query edge $q \in E_Q$ under $f$ with $\ell_G(e_{\text{upd}}) = \ell_Q(q)$).
   - If $e_{\text{upd}}$ were not used in $f(E_Q \setminus R(f))$, then $f$ would have had the exact same missing edge count in $G_{t-1}$, so $f \in \mathcal{M}_{t-1}$ and would not be an ENTERED event.
   - If $|R_{G_{\text{after}}}(f)| < k$, then even without $e_{\text{upd}}$, $|R_{G_{t-1}}(f)| \le k$, so $f$ was already valid in $G_{t-1}$.
   - Therefore, **event leaf condition for INSERT is strictly $|R_{G_{\text{after}}}(f)| = k$** with the update edge acting as an anchor.
2. **DELETE (`-`)**:
   - Evaluated on $G_{\text{before}} = G_{t-1}$.
   - A mapping $f$ exits the valid set ($\Delta \mathcal{M} = \mathcal{M}_{t-1} \setminus \mathcal{M}_t$) if and only if:
     $$|R_{G_{\text{before}}}(f)| = k \quad \text{and} \quad e_{\text{upd}} \in f(E_Q \setminus R_{G_{\text{before}}}(f))$$
   - Therefore, **event leaf condition for DELETE is strictly $|R_{G_{\text{before}}}(f)| = k$** on $G_{\text{before}}$ anchored at $e_{\text{upd}}$.
3. **No Duplicate Event Emissions (Uniqueness Theorem)**:
   For any single edge update $(u, v, l)$ in a simple graph, an injective embedding $f$ can map at most one query edge $q = \{qa, qb\}$ to $\{u, v\}$. Since $Q$ is simple, there is no second edge between $qa$ and $qb$. Furthermore, because $f$ is strictly injective and $u \ne v$, exactly one of $(f(qa)=u, f(qb)=v)$ or $(f(qa)=v, f(qb)=u)$ can hold. Thus, an embedding $f$ is enumerated under exactly one anchor edge and one orientation.

### 2.3. Canonical Tuple Representation
All emitted embeddings are materialized as canonical ordered tuples:
$$\operatorname{tuple}(f) = (f(u_1), f(u_2), \dots, f(u_n))$$
where $u_1 < u_2 < \dots < u_n$ are the original signed 64-bit query vertex IDs sorted in strictly ascending numerical order. Data coordinates are never sorted; full automorphism symmetry is explicitly preserved (no quotienting or scalar factors).

---

## 3. Architecture & Module Call Graph

```text
+---------------------------------------------------------------------------------+
|                               rapidflow_apx_native                              |
|           (CLI Parsing, JSON Workload Loader, Memory & Time Limits)             |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
|                               ApxStreamingEngine                                |
|   - Coordinates transaction steps on ApxTransactionManager                      |
|   - Relevancy filter (checks if update edge label matches any query edge)       |
|   - Snapshot sequencing: INSERT -> mutate then search; DELETE -> search then del|
|   - Pre-validation, execution containment, budget tracking                      |
+---------------------------------------------------------------------------------+
                   |                                           |
                   v                                           v
+------------------------------------+       +------------------------------------+
|          ApxM1EnumEngine           |       |          ApxM2EventEngine          |
|  - Anchors query edge to update    |       |  - Anchors query edge to update    |
|  - Iterates C(|E_Q|-1, k) variants |       |  - PRESENT / MISSING DFS branching |
|  - Connected order on Q - D        |       |  - Deferred unmapped vertices      |
|  - Exact matching & R(f) == D      |       |  - Invariant guard (k < lambda)    |
+------------------------------------+       +------------------------------------+
                   \                                           /
                    \                                         /
                     v                                       v
+---------------------------------------------------------------------------------+
|                       Shared Native P2 Foundation Components                    |
|  - ApxQueryPlan:        Query indexing, edge connectivity lambda(Q), incidence  |
|  - ApxAuthoritativeGraph: RapidFlow CSR Graph + GlobalEdgeView overlay + labels |
|  - ApxTypedCodec:       64-bit IDs, typed labels, ascending canonical ordering  |
|  - ApxTransactionManager: ACID transactions, multi-stage rollback, reentrancy   |
|  - ApxTupleSink:        Staging buffer, injectivity & duplicate raw checks      |
+---------------------------------------------------------------------------------+
```

---

## 4. Component Details & Algorithmic Design

### 4.1. `ApxQueryPlan`
- **Responsibilities**:
  - Computes exact edge connectivity $\lambda(Q)$ on the undirected query graph using Edmonds-Karp BFS augmenting path maximum flow between all pairs $(s, t)$.
  - Enforces the D-R contract $0 \le k < \lambda(Q)$; throws `std::invalid_argument` if violated.
  - Builds indexed query edge lists $E_Q = [(qa_0, qb_0), \dots, (qa_{m-1}, qb_{m-1})]$ with internal dense indices.
  - Builds fast label-to-edges lookup: maps each edge label ID to compatible query edge indices.
  - Precomputes incident edge tables for every query vertex.
  - For M1: precomputes variant subsets $D \subset E_Q \setminus \{q\}$ with $|D| = k$ and their deterministic connected search orders.

### 4.2. `ApxM1EnumEngine` (Native M1-enum-Exact-$k$)
- **Algorithm Flow**:
  1. For each query edge $q = (qa, qb) \in E_Q$ whose label matches the update edge:
  2. For both orientations $(pa, pb) \in \{(u, v), (v, u)\}$:
     - Check vertex label compatibility: $\ell_G(pa) == \ell_Q(qa)$ and $\ell_G(pb) == \ell_Q(qb)$.
  3. For each missing subset $D \subseteq E_Q \setminus \{q\}$ with $|D| = k$:
     - Let $Q - D$ be the query graph with edges in $D$ removed. Since $k < \lambda(Q)$, $Q - D$ is guaranteed to be connected and spanning.
     - Build a deterministic connected search order on $V_Q$ starting with $qa, qb$.
     - Perform exact subgraph matching of $Q - D$ on the snapshot graph $G$:
       - At each depth, candidates are neighbors of already-mapped vertices along kept edges.
       - Charge `candidate_evaluations` and check work limit before filtering.
       - Verify edge labels along all incident kept edges to previously mapped vertices.
     - At leaf (all vertices mapped):
       - **Strict Missing Edge Verification**: Verify that every edge in $D$ is strictly missing in $G$ (either absent or has a differing label). If any edge in $D$ is present with matching label, reject!
       - Materialize canonical ordered tuple and emit to `ApxTupleSink`.

### 4.3. `ApxM2EventEngine` (Native Bare M2-Event-$k$)
- **Algorithm Flow**:
  1. For each query edge $q = (qa, qb) \in E_Q$ matching update label:
  2. For both orientations $(pa, pb) \in \{(u, v), (v, u)\}$ matching vertex labels:
     - Initialize search state:
       - `f[v]` array initialized to `UNASSIGNED` for all $v \in V_Q$, except `f[qa] = pa`, `f[qb] = pb`.
       - `used_data` bitset with $pa, pb$ set.
       - `status[e]` array initialized to `UNKNOWN` for all $e \in E_Q$, except `status[q] = PRESENT`.
       - `missing_count = 0`.
     - Invoke recursive `dfs(f, status, missing_count)`.
  3. In `dfs`:
     - If `missing_count > k`: prune immediately.
     - If all query vertices are assigned:
       - Verify edge commitments on all query edges:
         - For $e = (u, v)$ with `status[e] == PRESENT`: ensure edge exists with matching label.
         - For $e = (u, v)$ with `status[e] == MISSING`: ensure edge does NOT exist with matching label.
       - If all commitments hold and `missing_count == k`:
         - Materialize canonical tuple $(f(u_1), \dots, f(u_n))$ and emit to sink.
       - Return.
     - **Frontier Identification**:
       - Scan for query edges $e = (a, b)$ where `status[e] == UNKNOWN` and exactly one endpoint is assigned.
       - **Invariant Guard**: If frontier is empty but unassigned query vertices remain and `missing_count <= k`:
         - Throws `InvariantViolationException`. (Since $k < \lambda(Q)$, any cut between assigned and unassigned vertices has $\ge \lambda(Q) > k$ edges. At most $k$ can be MISSING, so at least one must be UNKNOWN across the cut).
     - **Deterministic Edge Selection**: Pick $e = \min(\text{frontier})$.
       - Let $u$ be the assigned endpoint, $v$ be the unassigned endpoint.
     - **PRESENT Branch**:
       - Iterate over neighbors of $f[u]$ in $G$ with matching edge label.
       - Charge `candidate_evaluations` and check work limit.
       - Check neighbor is unused and matches vertex label of $v$.
       - Tentatively assign $f[v] = cand$, `status[e] = PRESENT`.
       - Resolve all incident edges to $v$ whose other endpoint is already mapped:
         - For each such edge $j$: check existence and label in $G$.
         - If $j$ was `MISSING` and is present: conflict $\to$ reject branch.
         - If $j$ was `PRESENT` and is missing: conflict $\to$ reject branch.
         - If $j$ was `UNKNOWN`: if present $\to$ mark `PRESENT`; if missing $\to$ mark `MISSING`, increment `missing_count`. If `missing_count > k` $\to$ reject branch.
       - If valid: recurse `dfs()`.
       - Rollback tentative assignments and status changes.
     - **MISSING Branch**:
       - Charge `missing_branches`.
       - Mark `status[e] = MISSING`, increment `missing_count`.
       - Defer assignment of $v$: $f[v]$ remains `UNASSIGNED`.
       - If `missing_count <= k`: recurse `dfs()`.
       - Rollback `status[e] = UNKNOWN`, decrement `missing_count`.

---

## 5. Streaming Transactions & Fault Recovery

The `ApxStreamingEngine` coordinates updates using `ApxTransactionManager`:
1. **Pre-Validation**: Checks vertex presence, self-loop rejection, edge presence/absence for op.
2. **Relevancy Filter**: Checks if update label matches any query edge.
   - If non-relevant: mutates authoritative graph and codec, skips search, publishes empty event.
3. **Snapshot Execution**:
   - `INSERT`: graph mutated $\to$ search on $G_{\text{after}}$ $\to$ tuples staged $\to$ transaction committed.
   - `DELETE`: search on $G_{\text{before}}$ $\to$ graph mutated $\to$ tuples staged $\to$ transaction committed.
4. **Fault Recovery**:
   - If search callback aborts, throws, or budget is exhausted:
     - Transaction rolls back: graph overlay is restored in-place, codec checkpoint is restored, staged tuples in sink are cleared.
     - Subsequent updates proceed cleanly on the restored state.

---

## 6. Native CLI & Containment Specification

Binary `rapidflow_apx_native`:
```bash
rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json> [--max-work <N>]
```
- **Process Resource Caps**:
  - Time limit: $\le 30$s per case (enforced via `alarm(30)`).
  - Memory limit: `RLIMIT_AS` $\le 512$ MiB.
  - Work limit: max 500,000 work units per update (supervisor canonical cap; cannot be enlarged).
- **Fail-Closed Verification**:
  - Validates schema, input graph sizes ($|V_Q| \le 6, |V_G| \le 8$), and budget $k < \lambda(Q)$.
  - Any invariant violation, resource exhaustion, or format corruption terminates fail-closed with structured error codes.

### 6.1. Contract Distinction: Engine Architectural Capability vs. Canonical Test Verification Scope (P3-R3-F08)

An explicit distinction is drawn between the native engine implementation capability and the Phase P3 canonical test verification regime:
1. **Native Engine Implementation Capability**:
   - The underlying C++ components (`ApxAuthoritativeGraph`, `ApxTypedCodec`, `ApxM1EnumEngine`, `ApxM2EventEngine`) are designed with dynamic standard library containers (`std::vector`, `sparsepp::sparse_hash_map`, `GlobalEdgeView`) and dynamic typed labels. They place no hard-coded compile-time limits on graph size, edge count, or update sequences, and are architected to scale to larger workloads in subsequent project phases.
2. **Canonical Phase P3 Acceptance & Containment Contract**:
   - Canonical Phase P3 acceptance is strictly and deliberately bounded to **micro-workloads**:
     - Query size: $|V(Q)| \le 6$, $|E(Q)| \ge 1$
     - Data graph size: $|V(G_0)| \le 8$, $|V(G_t)| \le 8$
     - Stream updates: $\le 12$ steps per stream
     - Candidate search space: $\le 100,000$
     - Max work limit: $\le 500,000$ units per update
     - Process containment: Wall clock timeout $\le 30$ seconds, address space $\le 512$ MiB (`RLIMIT_AS`)
   - **Rationale**:
     Micro-workloads enable exhaustive bit-for-bit differential comparison against the independent Python Ground Truth Oracle (M0) and exact all-cuts enumeration $\lambda(Q)$, ensuring complete automorphism group preservation and 100% deterministic reproducibility under CI resource guards without non-deterministic timeouts or heuristic approximations.
   - Any test workload submitted under Phase P3 canonical evaluation exceeding these containment boundaries is rejected preflight fail-closed with `CONTAINMENT_VIOLATION`.

---

## 7. Native Code Reuse Map

| Upstream Component | Source File | Action | Phase P3 Role |
| :--- | :--- | :---: | :--- |
| `Graph` CSR | `graph/graph.cpp` | **KEEP** | Underlying baseline data graph storage for $G_0$. |
| `GlobalEdgeView` | `utility/relation/global_edge_view.h` | **KEEP** | Dynamic edge overlay for streaming insertions/deletions. |
| `ApxTypedCodec` | `extensions/rapidflow_apx/` | **KEEP** | 64-bit ID decoding and typed label interning. |
| `ApxAuthoritativeGraph` | `extensions/rapidflow_apx/` | **KEEP** | Hybrid ground-truth graph state and neighbor iteration. |
| `ApxTransactionManager`| `extensions/rapidflow_apx/` | **KEEP** | ACID update coordination and zero-allocation rollback. |
| `ApxTupleSink` | `extensions/rapidflow_apx/` | **KEEP** | Canonical tuple staging, injectivity, and duplicate filtering. |
| `ApxQueryPlan` | `extensions/rapidflow_apx/` | **NEW** | Query indexing, $\lambda(Q)$ computation, and variant caching. |
| `ApxM1EnumEngine` | `extensions/rapidflow_apx/` | **NEW** | Native M1-enum-Exact-$k$ baseline event engine. |
| `ApxM2EventEngine` | `extensions/rapidflow_apx/` | **NEW** | Native Bare M2-Event-$k$ direct DFS event engine. |
| `ApxStreamingEngine` | `extensions/rapidflow_apx/` | **NEW** | End-to-end streaming update processor. |
| `GlobalViewManager::nlf_check` | `streaming/global_view_manager.h` | **REPLACE** | Replaced by direct branching; exact NLF is unsound for APX. |
| `SearchEngine` leaf count | `streaming/search_engine.cpp` | **REPLACE** | Replaced by explicit tuple materialization in `ApxTupleSink`. |
| Automorphism scalar | `streaming/streaming_engine.cpp` | **REPLACE** | Replaced by unrolled search preserving full automorphism group. |

---

## 8. Formal Correctness Arguments — M1-enum and Bare M2

This section provides the rigorous mathematical proofs of **Soundness**, **Completeness**, and **Uniqueness** for both native event engines (`ApxM1EnumEngine` and `ApxM2EventEngine`), establishes the **Event Boundary Theorem**, formulates the **DFS Invariant**, and proves the **Empty-Frontier Invariant** under Domain D-R.

### 8.1. Common Definitions & The Event Boundary Theorem

**Definition 8.1 (Query Graph and Domain D-R)**.
Let $Q = (V_Q, E_Q)$ be a connected, simple, undirected query graph with $n = |V_Q| \ge 2$ and $m = |E_Q| \ge 1$. Let $\lambda(Q)$ denote the exact edge connectivity of $Q$, defined as:
$$\lambda(Q) = \min_{\substack{S \subsetneq V_Q \\ S \ne \emptyset}} |\delta_Q(S)|$$
where $\delta_Q(S) = \{ \{u, v\} \in E_Q \mid u \in S, v \notin S \}$.
The approximate budget parameter $k \in \mathbb{N}_0$ is strictly restricted to Domain D-R:
$$0 \le k < \lambda(Q)$$

**Definition 8.2 (Injective Embedding and Missing Edge Set)**.
Let $G = (V_G, E_G)$ be a simple undirected data graph with vertex labels $\ell_G$ and edge labels $\ell_G$. A mapping $f: V_Q \to V_G$ is an injective embedding if $f(u) \ne f(v)$ for all $u \ne v \in V_Q$ and $\ell_G(f(u)) = \ell_Q(u)$ for all $u \in V_Q$.
The missing edge set of $f$ on $G$ is:
$$R_G(f) = \left\{ \{u, v\} \in E_Q \mid \{f(u), f(v)\} \notin E_G \lor \ell_G(\{f(u), f(v)\}) \ne \ell_Q(\{u, v\}) \right\}$$
An injective embedding $f$ is a $k$-approximate match of $Q$ in $G$ if and only if $|R_G(f)| \le k$. The match set is denoted $\mathcal{M}(Q, G, k) = \{ f \mid |R_G(f)| \le k \}$.

**Lemma 8.1 (Unit Variation of Missing Count)**.
Let $G' = G \oplus e_{\text{upd}}$ be obtained by inserting or deleting a single undirected edge $e_{\text{upd}} = \{u_{\text{data}}, v_{\text{data}}\}$ with label $l_{\text{upd}}$. For any injective mapping $f: V_Q \to V_G$:
$$||R_{G'}(f)| - |R_G(f)|| \le 1$$
Furthermore, $|R_{G'}(f)| \ne |R_G(f)|$ if and only if there exists a query edge $q = \{u_q, v_q\} \in E_Q$ such that $\{f(u_q), f(v_q)\} = e_{\text{upd}}$ and $\ell_Q(q) = l_{\text{upd}}$.

*Proof*.
Since $Q$ is simple, there is at most one edge between any pair of vertices $\{u_q, v_q\}$. Since $f$ is injective, each pair of data vertices $\{u_{\text{data}}, v_{\text{data}}\}$ can be the image of at most one query edge $q \in E_Q$.
If no query edge maps to $e_{\text{upd}}$ with matching label, the presence or absence of $e_{\text{upd}}$ does not affect the missing status of any edge in $E_Q$, so $R_{G'}(f) = R_G(f)$ and $|R_{G'}(f)| = |R_G(f)|$.
If exactly one query edge $q \in E_Q$ satisfies $\{f(u_q), f(v_q)\} = e_{\text{upd}}$ with $\ell_Q(q) = l_{\text{upd}}$:
- Upon INSERT ($G' = G \cup \{e_{\text{upd}}\}$): $q \in R_G(f)$ and $q \notin R_{G'}(f)$. All other query edges have identical status in $G$ and $G'$. Thus $R_G(f) \setminus R_{G'}(f) = \{q\}$, implying $|R_{G'}(f)| = |R_G(f)| - 1$.
- Upon DELETE ($G' = G \setminus \{e_{\text{upd}}\}$): $q \notin R_G(f)$ and $q \in R_{G'}(f)$. Thus $R_{G'}(f) \setminus R_G(f) = \{q\}$, implying $|R_{G'}(f)| = |R_G(f)| + 1$.
In all cases, the missing count changes by at most 1. $\blacksquare$

**Theorem 8.1 (Event Boundary Theorem)**.
Under a single-edge update $\Delta_t = (\text{op}, e_{\text{upd}}, l_{\text{upd}})$:
1. **ENTERED Events (INSERT)**:
   A mapping $f$ enters the match set ($\Delta \mathcal{M}_{\text{enter}} = \mathcal{M}(Q, G_{\text{after}}, k) \setminus \mathcal{M}(Q, G_{\text{before}}, k)$) if and only if:
   $$|R_{G_{\text{after}}}(f)| = k \quad \text{and} \quad \exists q \in E_Q \text{ s.t. } f(q) = e_{\text{upd}} \land \ell_Q(q) = l_{\text{upd}}$$
2. **EXITED Events (DELETE)**:
   A mapping $f$ exits the match set ($\Delta \mathcal{M}_{\text{exit}} = \mathcal{M}(Q, G_{\text{before}}, k) \setminus \mathcal{M}(Q, G_{\text{after}}, k)$) if and only if:
   $$|R_{G_{\text{before}}}(f)| = k \quad \text{and} \quad \exists q \in E_Q \text{ s.t. } f(q) = e_{\text{upd}} \land \ell_Q(q) = l_{\text{upd}}$$

*Proof*.
For INSERT: Suppose $f \in \Delta \mathcal{M}_{\text{enter}}$. Then $|R_{G_{\text{after}}}(f)| \le k$ and $|R_{G_{\text{before}}}(f)| > k$. By Lemma 8.1, $|R_{G_{\text{before}}}(f)| - |R_{G_{\text{after}}}(f)| \le 1$. Hence $|R_{G_{\text{before}}}(f)| = k + 1$ and $|R_{G_{\text{after}}}(f)| = k$. Furthermore, the change in missing status requires that $e_{\text{upd}}$ maps to some query edge $q \in E_Q$ with matching label. Conversely, if $|R_{G_{\text{after}}}(f)| = k$ and $f(q) = e_{\text{upd}}$, then in $G_{\text{before}}$, edge $q$ was missing, so $|R_{G_{\text{before}}}(f)| = k + 1 > k$, meaning $f \notin \mathcal{M}(Q, G_{\text{before}}, k)$ and $f \in \mathcal{M}(Q, G_{\text{after}}, k)$, so $f \in \Delta \mathcal{M}_{\text{enter}}$.
The proof for DELETE is symmetric: $|R_{G_{\text{before}}}(f)| = k \implies |R_{G_{\text{after}}}(f)| = k + 1 > k$, so $f$ exits the valid set. $\blacksquare$

### 8.2. M1-enum Soundness Proof

`ApxM1EnumEngine` decomposes event search around $e_{\text{upd}}$ into exact anchored subgraph matching over all combinations $D \subset E_Q \setminus \{q\}$ with $|D| = k$, filtering candidates by the exact missing set condition $R_{G_{\text{snap}}}(f) = D$.

**Theorem 8.2 (M1-enum Soundness)**.
Every tuple emitted by `ApxM1EnumEngine` corresponds to a genuine, valid, injective event embedding $f \in \Delta \mathcal{M}$.

*Proof*.
Let $t = (f(u_1), \dots, f(u_n))$ be an emitted tuple. We verify each required property:
1. **Full Query Vertex Coverage & Injectivity**: `ApxM1EnumEngine` executes DFS on $Q - D$. The recursion only terminates at a leaf when all $n = |V_Q|$ query vertices are assigned. At each extension, `used_data[cand]` is checked; a data vertex already in the mapping is rejected. Hence $f$ is strictly injective.
2. **Hard Vertex Labels**: Before assignment, `snapshot.get_vertex_label(cand) == query_plan.get_vertex_label(u_q)` is verified. All vertex labels match.
3. **Satisfaction of Kept Edges**: DFS only extends along edges present in $Q - D$, and verifies that `snapshot.get_edge_label(cand, other_data)` exists with matching label. Hence every edge in $E_Q \setminus D$ is present in $G_{\text{snap}}$ with matching label.
4. **Anchor Alignment**: The search is seeded with $f(qa) = u_{\text{upd}}, f(qb) = v_{\text{upd}}$ (or reversed) for a query edge $q = \{qa, qb\}$ with $\ell_Q(q) = l_{\text{upd}}$. Thus $e_{\text{upd}}$ is an anchor edge.
5. **Exact Missing Set $R_{G_{\text{snap}}}(f) = D$**: At every leaf, `ApxM1EnumEngine` explicitly inspects every edge $e \in D$ and checks if it exists in $G_{\text{snap}}$. If any edge in $D$ exists with matching label in $G_{\text{snap}}$, the candidate is rejected with `counters.missing_set_rejections++`. Since all edges in $E_Q \setminus D$ are confirmed present, and all edges in $D$ are confirmed missing, we have $R_{G_{\text{snap}}}(f) = D$.
6. **Strict Event Boundary Condition**: Since $|D| = k$ by construction, $R_{G_{\text{snap}}}(f) = D \implies |R_{G_{\text{snap}}}(f)| = |D| = k$. By Theorem 8.1, $f$ is a genuine ENTERED event (if $G_{\text{snap}} = G_{\text{after}}$) or EXITED event (if $G_{\text{snap}} = G_{\text{before}}$).

*Why $R(f) = D$ Cannot Be Relaxed to $R(f) \subseteq D$*:
If we only checked $R(f) \subseteq D$, an embedding where some edge $e \in D$ actually exists in $G_{\text{snap}}$ would have $R(f) \subsetneq D$, implying $|R(f)| < |D| = k$. By Theorem 8.1, any mapping with $|R_{G_{\text{after}}}(f)| < k$ already had $|R_{G_{\text{before}}}(f)| \le k$, and was therefore already in the match set prior to the update. Relaxing the check would emit false positive non-events. Thus $R(f) = D$ is mathematically necessary and sufficient. $\blacksquare$

### 8.3. M1-enum Completeness Proof

**Theorem 8.3 (M1-enum Completeness)**.
Every genuine event mapping $f \in \Delta \mathcal{M}$ is enumerated and emitted by `ApxM1EnumEngine`.

*Proof*.
Let $f \in \Delta \mathcal{M}$ be an arbitrary valid event mapping on snapshot $G_{\text{snap}}$.
1. By Theorem 8.1, $|R_{G_{\text{snap}}}(f)| = k$, and there exists a query edge $q = \{qa, qb\} \in E_Q$ such that $\{f(qa), f(qb)\} = e_{\text{upd}}$ and $\ell_Q(q) = l_{\text{upd}}$.
2. Because $Q$ is simple, $q$ is unique. Because $u_{\text{upd}} \ne v_{\text{upd}}$ and $f$ is injective, exactly one orientation $(f(qa)=u_{\text{upd}}, f(qb)=v_{\text{upd}})$ or $(f(qa)=v_{\text{upd}}, f(qb)=u_{\text{upd}})$ holds. `ApxM1EnumEngine` iterates over all candidate edges matching $l_{\text{upd}}$ and both orientations, so anchor $(q, \text{orient})$ is visited.
3. Let $D = R_{G_{\text{snap}}}(f)$. Since $|R_{G_{\text{snap}}}(f)| = k$, we have $|D| = k$. Since $q$ is present in $G_{\text{snap}}$, $q \notin D$, so $D \subset E_Q \setminus \{q\}$.
4. **Connectivity of $Q - D$**: Since $|D| = k$ and Domain D-R enforces $k < \lambda(Q)$, removing $D$ cannot disconnect $Q$. By definition of edge connectivity, any edge cut of $Q$ has size at least $\lambda(Q) > k$. Therefore, $Q - D$ is guaranteed to be a connected graph containing all $n$ vertices.
5. In `ApxQueryPlan::generate_m1_variants`, all $\binom{m-1}{k}$ subsets $D \subset E_Q \setminus \{q\}$ are generated. Since $Q - D$ is connected, this variant plan is retained.
6. The variant plan constructs a connected BFS search order starting from $\{qa, qb\}$. The standard DFS on $Q - D$ exhaustively explores all candidate data neighbors matching query vertex and edge labels. Since $f$ satisfies all edges in $Q - D$ by definition of $D = R(f)$, DFS will reach leaf $f$.
7. At the leaf, the filter verifies $R(f) = D$, which holds by definition of $D$. Hence $f$ is emitted. $\blacksquare$

### 8.4. M1-enum Uniqueness Proof

**Theorem 8.4 (M1-enum Uniqueness)**.
No event mapping $f$ is emitted more than once by `ApxM1EnumEngine` for a single update.

*Proof*.
Suppose for contradiction that $f$ is emitted at least twice:
1. An emission occurs under a triple $(q, \text{orient}, D)$ where $q$ is the anchor edge, $\text{orient} \in \{0, 1\}$, and $D$ is the chosen missing subset.
2. By Theorem 8.2, $D = R_{G_{\text{snap}}}(f)$. Since the snapshot $G_{\text{snap}}$ and mapping $f$ are fixed, the set of missing edges $R_{G_{\text{snap}}}(f)$ is uniquely determined. Hence $f$ cannot be emitted under two different subsets $D_1 \ne D_2$.
3. Since $f$ maps $e_{\text{upd}}$ to a query edge, and $Q$ is simple, there is at most one edge $q \in E_Q$ whose endpoints map to $\{u_{\text{upd}}, v_{\text{upd}}\}$. Hence $q$ is uniquely determined.
4. Since $u_{\text{upd}} \ne v_{\text{upd}}$ (self-loops rejected prevalidation) and $f$ is injective ($f(qa) \ne f(qb)$), the orientation mapping $qa$ to $u_{\text{upd}}$ and $qb$ to $v_{\text{upd}}$ is unique.
5. Within the DFS for a fixed triple $(q, \text{orient}, D)$, the search order is fixed and deterministic. At each vertex $u_i$, candidate neighbors are iterated over a set without duplicates, and `used_data` prevents duplicate assignment. The DFS tree is a tree of distinct partial mappings, each leaf representing a distinct injective function.
Therefore, $(q, \text{orient}, D)$ is unique, and DFS reaches $f$ exactly once.
*Role of Tuple Sink Guard*: The duplicate check in `ApxTupleSink` asserts uniqueness fail-closed; it is an invariant guard, not a repair mechanism for duplicate generation. $\blacksquare$

### 8.5. Bare M2 DFS Invariant & Soundness Proof

`ApxM2EventEngine` executes a direct DFS on $Q$ anchored at $e_{\text{upd}}$, maintaining partial vertex mapping $f: S \to V_G$ (where $S \subset V_Q$) and edge commitments $\operatorname{status}: E_Q \to \{\text{UNKNOWN}, \text{PRESENT}, \text{MISSING}\}$.

**Definition 8.3 (M2 DFS State & Invariant)**.
At every invocation of `dfs_m2(mapping, used_data, status, missing_count, assigned_count, ...)`:
1. **$I_1$ (Injectivity)**: $\forall u \ne v \in S, \text{mapping}[u] \ne \text{mapping}[v]$.
2. **$I_2$ (Used Data Tracking)**: $\forall v \in V_G, \text{used\_data}[v] = \text{true} \iff \exists u \in S \text{ s.t. } \text{mapping}[u] = v$.
3. **$I_3$ (Vertex Labels)**: $\forall u \in S, \ell_G(\text{mapping}[u]) = \ell_Q(u)$.
4. **$I_4$ (PRESENT Commitments)**: $\forall e = \{u, v\} \in E_Q$ with $\operatorname{status}[e] = \text{PRESENT}$: both $u, v \in S$, and $\{\text{mapping}[u], \text{mapping}[v]\} \in E_{\text{snap}}$ with $\ell_G = \ell_Q(e)$.
5. **$I_5$ (MISSING Commitments)**: $\forall e = \{u, v\} \in E_Q$ with $\operatorname{status}[e] = \text{MISSING}$: if both $u, v \in S$, then $\{\text{mapping}[u], \text{mapping}[v]\} \notin E_{\text{snap}} \lor \ell_G \ne \ell_Q(e)$.
6. **$I_6$ (Single Charge & Missing Count)**: $\text{missing\_count} = |\{ e \in E_Q \mid \operatorname{status}[e] = \text{MISSING} \}| \le k$. Each edge is committed at most once.
7. **$I_7$ (Anchor Invariant)**: The initial anchor edge $q$ has $\operatorname{status}[q] = \text{PRESENT}$ and maps to $e_{\text{upd}}$.

**Theorem 8.5 (M2 Soundness)**.
Every tuple emitted by `ApxM2EventEngine` corresponds to a genuine, valid, injective event embedding $f \in \Delta \mathcal{M}$.

*Proof*.
A tuple is emitted only when `assigned_count == num_q` (i.e. $S = V_Q$).
1. By $I_1$ and $I_3$, $f$ is fully defined on $V_Q$, strictly injective, and preserves all vertex labels.
2. At the leaf, the algorithm loops over all $j \in \{0, \dots, m-1\}$:
   - For edges with $\operatorname{status}[j] == \text{MISSING}$: verifies that the edge does NOT exist with matching label in $G_{\text{snap}}$. If it exists, `commitment_rejections++` and leaf returns without emitting.
   - For edges with $\operatorname{status}[j] == \text{PRESENT}$: verifies that the edge exists with matching label.
3. Next, the algorithm checks `missing_count == k`.
   - By $I_6$, `missing_count` equals the number of edges committed MISSING.
   - All other edges are committed PRESENT and verified to exist.
   - Hence exactly $k$ query edges are missing in $G_{\text{snap}}$, so $|R_{G_{\text{snap}}}(f)| = k$.
4. By $I_7$, the anchor edge $q$ is PRESENT and matches $e_{\text{upd}}$.
5. By Theorem 8.1, any injective mapping with $|R_{G_{\text{snap}}}(f)| = k$ anchored at $e_{\text{upd}}$ is a genuine ENTERED event (if $G_{\text{snap}} = G_{\text{after}}$) or EXITED event (if $G_{\text{snap}} = G_{\text{before}}$). $\blacksquare$

### 8.6. Bare M2 Completeness Proof

**Theorem 8.6 (M2 Completeness)**.
Every genuine event mapping $f \in \Delta \mathcal{M}$ is enumerated and emitted by `ApxM2EventEngine`.

*Proof*.
Let $f \in \Delta \mathcal{M}$ be an arbitrary valid event mapping on $G_{\text{snap}}$.
1. By Theorem 8.1, $|R_{G_{\text{snap}}}(f)| = k$, and there is a unique anchor query edge $q = \{qa, qb\}$ such that $f(q) = e_{\text{upd}}$. M2 seeds search with $(q, \text{orient})$, setting $\text{mapping}[qa] = f(qa), \text{mapping}[qb] = f(qb)$, $\operatorname{status}[q] = \text{PRESENT}$, and $\text{missing\_count} = 0$.
2. Suppose inductively that the current DFS state is compatible with $f$:
   - For all $u \in S$, $\text{mapping}[u] = f(u)$.
   - For all $e \in E_Q$ with $\operatorname{status}[e] = \text{PRESENT}$, $e \notin R_{G_{\text{snap}}}(f)$.
   - For all $e \in E_Q$ with $\operatorname{status}[e] = \text{MISSING}$, $e \in R_{G_{\text{snap}}}(f)$.
   - $\text{missing\_count} = |\{ e \in E_Q \mid \operatorname{status}[e] = \text{MISSING} \}| \le k$.
3. If $S = V_Q$, then by induction all edges are verified, $\text{missing\_count} = |R_{G_{\text{snap}}}(f)| = k$, and $f$ is emitted.
4. If $S \subsetneq V_Q$, by Theorem 8.8 (Empty-Frontier Invariant), there exists at least one UNKNOWN edge across the cut $\delta_Q(S)$. The deterministic selection finds the first such edge $e = \{u_q, v_q\}$ with $u_q \in S, v_q \notin S$.
5. In the target embedding $f$, the query edge $e$ is either:
   - **Case A: $e \notin R_{G_{\text{snap}}}(f)$ (Edge is PRESENT)**:
     Then the data edge $\{f(u_q), f(v_q)\}$ exists in $G_{\text{snap}}$ with matching label. Thus $f(v_q)$ is in the neighbor list of $f(u_q)$ in $G_{\text{snap}}$. Since $f$ is injective, $f(v_q)$ is not in $\text{used\_data}$. Since $f$ preserves vertex labels, $\ell_G(f(v_q)) = \ell_Q(v_q)$.
     Therefore, candidate $cand = f(v_q)$ will be reached in the PRESENT branch candidate loop.
     Incident resolution checks all edges between $v_q$ and other vertices in $S$; by compatibility with $f$, all incident edge lookups match $f$'s true status, and missing count will not exceed $k$. Thus M2 recursively calls `dfs_m2` with $v_q$ mapped to $f(v_q)$, maintaining inductive compatibility.
   - **Case B: $e \in R_{G_{\text{snap}}}(f)$ (Edge is MISSING)**:
     Then the MISSING branch is explored: $\operatorname{status}[e] \leftarrow \text{MISSING}$, $\text{missing\_count} \leftarrow \text{missing\_count} + 1$. Since $e \in R_{G_{\text{snap}}}(f)$ and $|R_{G_{\text{snap}}}(f)| = k$, the new missing count is $\le k$. M2 defers $v_q$ and recursively calls `dfs_m2`, maintaining inductive compatibility.
6. Since every branch compatible with $f$ is explored, DFS will reach the leaf corresponding to $f$ and emit it. $\blacksquare$

### 8.7. Bare M2 Uniqueness Proof

**Theorem 8.7 (M2 Uniqueness)**.
No event mapping $f$ is emitted more than once by `ApxM2EventEngine` for a single update.

*Proof*.
1. By Lemma 8.1 and the simple graph structure of $Q$, $f$ has a unique anchor query edge $q$ mapping to $e_{\text{upd}}$ with a unique orientation. Hence search branches from distinct $(q, \text{orient})$ pairs cannot both be compatible with $f$.
2. In the DFS tree rooted at $(q, \text{orient})$, at each decision step with frontier edge $e$, the two branches (PRESENT vs MISSING) are mutually exclusive: in a PRESENT branch, $e$ is committed PRESENT; in a MISSING branch, $e$ is committed MISSING. For mapping $f$, $e$ is either in $R_{G_{\text{snap}}}(f)$ or not. Exactly one of the two branches is compatible with $f$.
3. In the PRESENT branch, candidate data vertices $cand$ are iterated over $N_G(f(u_q))$. Since each $cand$ is a distinct data vertex, at most one candidate equals $f(v_q)$.
4. Rollback guarantees:
   - When a PRESENT branch unwinds: `status_undo` restores all incident edge commitments, `missing_count` is restored, `used_data[cand]` is reset to `false`, and $\text{mapping}[v_q]$ is reset to `UNASSIGNED`.
   - When a MISSING branch unwinds: $\operatorname{status}[e]$ is reset to `UNKNOWN`.
   No state leaks between sibling branches.
5. Therefore, there is a unique path from root to leaf in the DFS tree that matches $f$, and $f$ is emitted exactly once. $\blacksquare$

### 8.8. Empty-Frontier Invariant Proof under Domain D-R

**Theorem 8.8 (Empty-Frontier Invariant under Domain D-R)**.
Let $Q = (V_Q, E_Q)$ satisfy Domain D-R ($0 \le k < \lambda(Q)$). During M2 DFS, if unmapped query vertices remain ($S \subsetneq V_Q$, $S \ne \emptyset$) and $\text{missing\_count} \le k$, then there exists at least one query edge $e \in \delta_Q(S)$ with $\operatorname{status}[e] = \text{UNKNOWN}$.

*Proof*.
Let $S \subsetneq V_Q$ with $S \ne \emptyset$, and let $T = V_Q \setminus S \ne \emptyset$.
1. By definition of edge connectivity $\lambda(Q)$, the cut $\delta_Q(S)$ separating $S$ and $T$ has size:
   $$|\delta_Q(S)| \ge \lambda(Q)$$
2. Every edge $e \in \delta_Q(S)$ connects a mapped vertex in $S$ to an unmapped vertex in $T$.
   - By Definition 8.3 ($I_4$), an edge can have $\operatorname{status}[e] = \text{PRESENT}$ only if both endpoints are mapped. Since $T \cap S = \emptyset$, no edge in $\delta_Q(S)$ can have $\operatorname{status}[e] = \text{PRESENT}$.
   - Therefore, every edge in $\delta_Q(S)$ must have status either $\text{MISSING}$ or $\text{UNKNOWN}$.
3. By Definition 8.3 ($I_6$), the total number of edges in $E_Q$ with status $\text{MISSING}$ is $\text{missing\_count} \le k$.
4. Consequently, the number of edges in the cut $\delta_Q(S)$ with status $\text{MISSING}$ is bounded by $k$:
   $$|\{ e \in \delta_Q(S) \mid \operatorname{status}[e] = \text{MISSING} \}| \le \text{missing\_count} \le k$$
5. Subtracting the MISSING cut edges from the total cut size:
   $$|\{ e \in \delta_Q(S) \mid \operatorname{status}[e] = \text{UNKNOWN} \}| = |\delta_Q(S)| - |\{ e \in \delta_Q(S) \mid \operatorname{status}[e] = \text{MISSING} \}| \ge \lambda(Q) - k$$
6. Since Domain D-R strictly enforces $\lambda(Q) > k$, we have:
   $$\lambda(Q) - k \ge 1$$
7. Therefore, there exists at least one edge in $\delta_Q(S)$ with status $\text{UNKNOWN}$.
8. Hence, the frontier across the cut is never empty while unmapped vertices remain and $\text{missing\_count} \le k$. Any state where the frontier is empty under these conditions is a mathematical impossibility under D-R, justifying the `InvariantViolationException` fail-closed guard. $\blacksquare$

### 8.9. Transaction Rollback vs. DFS Branch Rollback Isolation

A fundamental architectural principle in RapidFlow-APX is the strict separation between **Transaction Rollback** and **DFS Branch Rollback**:

1. **DFS Branch Rollback (Algorithm-Internal, Non-Transactional)**:
   - Occurs millions of times during normal tree exploration inside `ApxM1EnumEngine` and `ApxM2EventEngine`.
   - Reverts tentative vertex assignments ($\text{mapping}[v] \leftarrow \text{UNASSIGNED}$, $\text{used\_data}[c] \leftarrow \text{false}$), restores edge commitment flags ($\operatorname{status}[e] \leftarrow \text{UNKNOWN}$), and restores `missing_count`.
   - **Key Property**: DFS branch rollback does NOT revert work counters (`work_units`, `dfs_states`, `candidate_evaluations`). Work counters are monotonically increasing search metrics reflecting all attempted computational effort.
   - Sibling branches receive clean data structures, preventing sibling contamination.

2. **Transaction Rollback (System-Level, ACID Invariant)**:
   - Occurs when an update step fails (prevalidation failure, fault injection, work budget exhaustion, or transport failure).
   - Coordinates multi-component atomic undo across:
     - `ApxAuthoritativeGraph`: Reverts dynamic CSR/overlay mutations back to the pre-transaction checkpoint.
     - `ApxTypedCodec`: Reverts string/label intern dictionaries to the checkpoint state.
     - `ApxTupleSink`: Aborts the active step batch, discarding all staged candidate tuples without publishing.
   - **Key Property**: Transaction rollback restores authoritative system state while preserving step and root diagnostics (`error_category`, execution counters, status `NOT_COMMITTED`). A subsequent transaction executed on the same engine instance proceeds on clean authoritative state without reallocating engine memory.

### 8.10. Algorithmic Assumptions & Limitations

1. **Simple Undirected Query Graphs**: Multigraph queries and self-loops are excluded by definition.
2. **Fixed Data Vertex Universe**: Vertices in $G$ are static; only single edge insertions and deletions are processed.
3. **Domain D-R Boundary**: Queries where $k \ge \lambda(Q)$ (Domain D-G) are not supported because the empty frontier invariant and connected $Q - D$ variant property do not hold; such queries are rejected preflight.
4. **Baseline Status**: M1-enum is a correctness baseline for Phase P3, not an optimized competitor to $B_{\text{iso}}$ or RapidFlow Exact. Formal performance evaluation is deferred to subsequent benchmark gates.
