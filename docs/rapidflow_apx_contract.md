# RapidFlow-APX Problem Contract & System Specifications (Direction D)

**Document ID:** `docs/rapidflow_apx_contract.md`
**Date:** 2026-11-18
**Contract Version:** 1.0 (Working Contract for Native RapidFlow Integration)
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D
**Associated Issue:** Issue #32 (`D-INT-03 / P3`; previously Issue #28 `P0–P1`, Issue #30 `P2` merged)
**Current Status:** Phase P2 merged into `QuangThang` (PR #31), Phase P3 active (`M1-enum` and `bare M2` native engines)
**Parent Contract:** `docs/dstar_problem_contract_v1.md` (Issue #22, Preserved Working Baseline)

---

## 1. Problem Formulation and Architectural Scope

This contract governs the design, implementation, and verification of **RapidFlow-APX**, an approximate continuous subgraph matching extension built directly upon native RapidFlow components.

### 1.1. Core Mathematical Model
We evaluate approximate matching under **Edge Recall** with **S2 connectivity** on dynamic labeled graphs:
1. **Query Graph $Q = (V_Q, E_Q)$**:
   - Simple, undirected, connected graph with $n = |V_Q| \ge 2$ and $m = |E_Q| \ge 1$.
   - Vertices possess immutable vertex labels $\ell_Q(u) \in \Sigma_V$.
   - Edges possess immutable edge labels $\ell_Q(e) \in \Sigma_E$.
   - Global edge connectivity $\lambda(Q)$ is the minimum size of an edge cut that disconnects $Q$.
2. **Data Graph $G_t = (V_G, E_{G,t})$**:
   - Simple, undirected graph evolving over discrete time steps $t \in \mathbb{N}_0$.
   - **Fixed Vertex Universe $V_0$**: Vertices are neither inserted nor deleted in the dynamic stream; $V_G = V_0$ for all $t$.
   - Vertices possess immutable vertex labels $\ell_G(v) \in \Sigma_V$.
   - Edges possess dynamic edge labels $\ell_G(e) \in \Sigma_E$.
3. **Injective Embedding $f: V_Q \to V_0$**:
   - Strictly injective: $\forall u \ne v \in V_Q, f(u) \ne f(v)$.
   - Hard vertex label match: $\forall u \in V_Q, \ell_G(f(u)) = \ell_Q(u)$.
   - Extraneous edges in $G$ (internal chords or external edges incident to $f(V_Q)$) do not penalize matches (non-induced matching).
4. **Missing Edge Set $R(f)$ and Edge Recall Score $S_E(f)$**:
   $$R(f) = \left\{ \{u, v\} \in E_Q \mid \{f(u), f(v)\} \notin E_{G,t} \lor \ell_G(\{f(u), f(v)\}) \ne \ell_Q(\{u, v\}) \right\}$$
   $$S_E(f) = \frac{m - |R(f)|}{m}$$
5. **Missing Edge Budget $k$**:
   For an external threshold $\tau \in (0, 1]$, the integer budget is:
   $$k = m - \lceil \tau \cdot m \rceil \quad (\text{using exact rational arithmetic})$$
   A candidate mapping satisfies the budget if and only if $|R(f)| \le k$.

---

## 2. Operating Domains: D-R (Active Scope) vs D-G (Deferred Extension)

### 2.1. Domain D-R: Primary Native Integration Scope
- **Domain Constraint**:
  $$0 \le k < \lambda(Q)$$
- **Automatic S2 Spanning Connectivity**:
  Because $k < \lambda(Q)$, removing any set $R(f)$ of size $\le k$ from $Q$ cannot disconnect $Q$. Therefore, the matched query subgraph:
  $$Q[E_Q \setminus R(f)] = (V_Q, E_Q \setminus R(f))$$
  is guaranteed to be spanning and connected on $V_Q$ for every mapping satisfying $|R(f)| \le k$.
- **Event Boundary Lemma (D-R)**:
  For any single-edge update $e = \{x, y\}$:
  - $\text{ENTERED} = M_t \setminus M_{t-1}$: Search executes on $G_{\text{after}}$ anchored at $\{x, y\}$ with leaf condition:
    $$|R(f)| == k$$
  - $\text{EXITED} = M_{t-1} \setminus M_t$: Search executes on $G_{\text{before}}$ anchored at $\{x, y\}$ with leaf condition:
    $$|R(f)| == k$$
  - Snapshot enumeration (initialization or static check) uses $|R(f)| \le k$.

### 2.2. Domain D-G: General Threshold Extension ($k \ge \lambda(Q)$)
- **Problem Statement**:
  When $k \ge \lambda(Q)$, a mapping with $|R(f)| \le k$ may have a disconnected matched query subgraph $Q[E_Q \setminus R(f)]$. D-G requires explicit evaluation of the S2 condition ($Q[E_Q \setminus R(f)]$ must be connected).
- **Dynamic Event Characterization (D-G)**:
  In D-G, an event is not restricted to the boundary $|R(f)| == k$. An update can cause an embedding to enter or exit because of a **connectivity transition** even when $|R(f)| < k$.
  On the snapshot containing the update anchor $q = \{u, v\}$, letting $H = Q[E_Q \setminus R(f)]$ and $j = |R(f)|$, the necessary and sufficient event condition is:
  $$\text{$H$ is connected} \quad \land \quad j \le k \quad \land \quad (j == k \lor \text{$H - q$ is disconnected})$$
- **Governance**:
  D-G is formally specified for future theoretical work. **All production code, baseline engines, and validation harnesses in Direction D currently operate under D-R.** No guards rejecting $k \ge \lambda(Q)$ may be disabled without formal review.

---

## 3. Mandatory Integration Requirements

To qualify as a genuine RapidFlow extension, the implementation must satisfy four architectural criteria:

```text
+--------------------------------------------------------------------------------+
|                         RapidFlow-APX System Architecture                       |
+--------------------------------------------------------------------------------+
|  1. External Interface:                                                        |
|     - Typed Label Codec (preserves integer 0 != string "0")                    |
|     - Sparse / Negative ID Codec -> Dense Internal IDs [0, |V|-1]              |
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
|  2. Authoritative Graph Layer:                                                 |
|     - Pinned RapidFlow CSR (Graph::loadGraphFromMemory) for static baseline    |
|     - GlobalEdgeView mutable overlay for dynamic streaming additions/removals  |
|     - Uniform edge existence and label lookup across base + overlay            |
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
|  3. Transactional Update Engine:                                               |
|     - Prevalidation (detect invalid vertices, duplicate inserts, absent deletes)|
|     - Forward/Reverse view synchronization                                     |
|     - Isolated search invocation (INSERT on G_after, DELETE on G_before)        |
|     - Atomic Rollback: In case of exception, restore pre-update state          |
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
|  4. Tuple Enumeration Sink:                                                    |
|     - Replace leaf count shortcut with explicit candidate binding              |
|     - Materialize distinct ordered tuples (with optional orbit expansion)      |
|     - Canonical tuple ordering: sorted ascending by original query vertex IDs  |
+--------------------------------------------------------------------------------+
```

---

## 4. Output Contract & Tuple Canonicalization

1. **Concrete Tuple Representation**:
   Every qualifying embedding $f$ is emitted as an ordered tuple:
   $$T(f) = (f(u_1), f(u_2), \dots, f(u_n)) \quad \text{where } (u_1, u_2, \dots, u_n) = \text{sorted}(V_Q)$$
   The ordering of elements within the tuple is strictly governed by the **ascending numerical order of the original query vertex IDs**.
2. **Prohibition of DFS Visit Order**:
   The internal backtracking visit order of query vertices (e.g., matching order determined by degree or BFS) must NEVER be exposed as the tuple output order.
3. **Ordered Tuple Enumeration & Symmetry Preservation**:
   All distinct ordered mappings (including valid symmetric automorphisms) must be materialized and emitted. In the default Bare M2 design, full DFS directly enumerates all distinct ordered tuples $(f(u_1), \dots, f(u_n))$ without post-hoc expansion or scalar multiplication. If a reduced representative search space is used, representative orbit expansion must be applied to emit the complete set of valid embeddings. Scalar count quotienting (multiplying counts by `automorphism_size`) is strictly forbidden in the tuple output path.
4. **Event Distinction**:
   - Only `ENTERED` and `EXITED` events are emitted.
   - Intermediate score fluctuations where $|R(f)|$ changes within $[0, k-1]$ produce **zero events**.
   - `SCORE_CHANGED` events are NOT part of this contract.

---

## 5. Input Validation, Numeric Boundaries, and Transactional Semantics

### 5.1. Typed Identifier & Label Codec
- **External Query / Data Vertex IDs**:
  - May be sparse, negative, or large integers (e.g. 64-bit).
  - External IDs must be mapped bijectively to dense internal indices $0 \le v_{\text{dense}} < |V_0|$ for RapidFlow compatibility.
  - The external ID must be faithfully restored upon tuple emission.
- **Typed Label Separation**:
  - Vertex and edge labels may be integers or strings.
  - The codec must maintain strict separation between numerical `0` and string `"0"`. Using `str(label)` as a dictionary key is strictly prohibited as it conflates types.
- **Internal Native Limits**:
  - The internal C++ engine uses `uint32_t` (`VertexID` and `LabelID`) with max capacity $2^{32}-1$.
  - Any input exceeding internal limits must be cleanly rejected during pre-validation with `NumericLimitExceededError`.

### 5.2. Atomic Update Semantics & Fail-Closed Transaction
Every single-edge update must execute as an atomic transaction:
1. **Pre-Validation**:
   - Verify that endpoints $x, y \in V_0$ and $x \ne y$ (reject self-loops).
   - For `INSERT`: verify that $\{x, y\} \notin E_G$. Reject duplicate insertions (`DuplicateEdgeInsertionError`).
   - For `DELETE`: verify that $\{x, y\} \in E_G$ and that $\ell_G(\{x, y\}) == \ell_{\text{target}}$. Reject absent deletions (`NonexistentEdgeDeletionError` or `EdgeLabelMismatchDeletionError`).
2. **Mutation & Search Snapshot**:
   - `INSERT`: Edge added to authoritative overlay and forward/reverse GlobalEdgeViews $\to$ search executed on $G_{\text{after}}$.
   - `DELETE`: Search executed on $G_{\text{before}}$ $\to$ edge removed from authoritative overlay and forward/reverse GlobalEdgeViews.
3. **Rollback Guarantee**:
   - If an error or resource exhaustion occurs at any point during mutation, search, or formatting, all partial changes made during that step must be reverted, leaving authoritative graph and index state identical to the pre-update state.

---

## 6. Pending Governance Decisions (Gate 0A)

The following foundational questions remain pending advisor review:
1. Confirmation of Edge Recall ($S_E$) as the sole similarity metric (unpenalized external edges).
2. Confirmation of S2 connectivity and restriction to $k < \lambda(Q)$ as the primary problem domain.
3. Confirmation of pure event semantics (`ENTERED` and `EXITED` only; no `SCORE_CHANGED`).
4. Acceptance of canonical ordered tuples $(f(u_1), \dots, f(u_n))$ as the required output format.
