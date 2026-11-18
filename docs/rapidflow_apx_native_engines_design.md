# RapidFlow-APX Native Engines Design Specification (Phase P3)

**Document ID:** `docs/rapidflow_apx_native_engines_design.md`  
**Milestone:** D-INT-03 / Phase P3  
**Parent Issue:** Issue #32  
**Target Integration Branch:** `QuangThang`  
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`  
**Status:** WORKING_DESIGN (R3-A Active)  

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
  - Computes exact edge connectivity $\lambda(Q)$ on the undirected query graph using Dinic / Edmonds-Karp maximum flow between all pairs $(s, t)$.
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
rapidflow_apx_native --engine <m1|m2> --workload <INPUT.json> --output <OUTPUT.json>
```
- **Process Resource Caps**:
  - Time limit: $\le 30$s per case.
  - Memory limit: `RLIMIT_AS` $\le 512$ MiB.
  - Work limit: max 500,000 work units per update.
- **Fail-Closed Verification**:
  - Validates schema, input graph sizes ($|V_Q| \le 6, |V_G| \le 8$), and budget $k < \lambda(Q)$.
  - Any invariant violation, resource exhaustion, or format corruption terminates fail-closed with structured error codes.

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
