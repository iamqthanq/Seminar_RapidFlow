# D* Problem Contract v1 — Working Specification

**Status:** WORKING CONTRACT (Advisor Approval Pending at Gate 0A)  
**Date:** 2026-11-17  
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — D* Feasibility Route  
**Scope:** DSTAR-01 (Issue #22) Correctness Foundation  
**Reference Document:** RapidFlow-APX Research Direction & Algorithm Design (17/11/2026, Google Doc `1-xOypqAphEQ3VXxFPzQeABKhWh_WKePt_gu1uNPehVk`)

---

## 1. Executive Summary and Motivation

D* investigates incremental approximate continuous subgraph matching (approximate CSM) under **Edge Recall** and **S2 connectivity** on dynamic labeled graphs.
Previous work in this repository explored Model C (APX-01 to APX-04), which defined an unpenalized core with bounded external boundary cut edges ($x_{\text{out}} \le \varepsilon_x$) and topological connectivity of the induced data subgraph $G[f(V_Q)]$ (S1).
The D* orientation resets the contract to a cleaner formulation:
1. Pure **Edge Recall** scoring: extraneous internal chords and external boundary edges do not penalize or invalidate matches.
2. **S2 Connectivity**: the graph formed by the *matched query edges* under the mapping must be spanning and connected on $V_Q$.
3. **Connectivity Safety Guard ($k < \lambda(Q)$)**: the maximum missing edge budget $k$ is strictly constrained to be less than the global edge connectivity $\lambda(Q)$ of the query topology. Under this condition, S2 is automatically satisfied for every mapping with $|R(f)| \le k$.
4. **Pure Incremental Event Semantics**: dynamic updates emit only newly qualifying embeddings (`ENTERED`) and newly disqualified embeddings (`EXITED`). No `SCORE_CHANGED` events are emitted in the D* API.

> [!IMPORTANT]
> **Pending Gate 0A Decisions**:
> This document serves as the repository's immutable working contract for DSTAR-01 implementation. Formal approval of the four foundational advisor questions (Section 7) is currently pending. No claim is made that the advisor has confirmed these choices.

---

## 2. Graph and Mapping Definitions

### 2.1. Query Graph $Q$
- $Q = (V_Q, E_Q)$ is an undirected, simple, connected graph with $|V_Q| \ge 2$.
- Each vertex $u \in V_Q$ has an immutable vertex label $\ell_Q(u)$.
- Each edge $e = \{u, v\} \in E_Q$ has an edge label $\ell_Q(e)$.
- Query vertex IDs are arbitrary integers (not required to be contiguous or 0-indexed).
- Let $n = |V_Q|$ and $m = |E_Q|$.
- Global edge connectivity $\lambda(Q)$ is defined as the minimum size of an edge cut that partitions $V_Q$ into two non-empty subsets (ignoring vertex and edge labels).

### 2.2. Data Graph $G_t$
- $G_t = (V_G, E_G)$ at discrete step $t$ is an undirected, simple graph.
- **Fixed Vertex Universe $V_0$**: $V_G = V_0$ is fixed for all $t$. Vertices are neither added nor removed in the dynamic stream.
- **Fixed Vertex Labels**: each vertex $v \in V_0$ has an immutable vertex label $\ell_G(v)$.
- Each edge $\{x, y\} \in E_G$ has an edge label $\ell_G(\{x, y\})$.
- Data vertex IDs are arbitrary integers.

### 2.3. Embedding / Mapping $f$
- A mapping $f: V_Q \to V_G$ is an injective function: for all $u, v \in V_Q$ with $u \ne v$, $f(u) \ne f(v)$.
- **Hard Vertex Label Compatibility**: for all $u \in V_Q$, $\ell_G(f(u)) = \ell_Q(u)$.
- **Canonical Ordered Tuple Convention**:
  An embedding $f$ is represented as the ordered tuple:
  $$T(f) = (f(u_1), f(u_2), \dots, f(u_n))$$
  where $(u_1, u_2, \dots, u_n) = \text{sorted}(V_Q)$ in ascending numerical order of the *original* query vertex IDs.
- **Automorphisms**: Automorphism groups are not quotiented out; every valid distinct ordered injection is reported.

---

## 3. Edge Matching, Missing Edge Budget, and Recall

### 3.1. Matched Query Edges and Missing Edge Set $R(f)$
For a query edge $e = \{u, v\} \in E_Q$ with label $\ell_Q(e)$, $e$ is considered **matched** in $G$ under $f$ if and only if:
1. $\{f(u), f(v)\} \in E_G$, and
2. $\ell_G(\{f(u), f(v)\}) = \ell_Q(e)$.

The set of missing or mismatched query edges is:
$$R(f) = \left\{ \{u, v\} \in E_Q \mid \{f(u), f(v)\} \notin E_G \lor \ell_G(\{f(u), f(v)\}) \ne \ell_Q(\{u, v\}) \right\}$$

The count of matched query edges is $c(f) = m - |R(f)|$.

### 3.2. Edge Recall Metric
The Edge Recall score is:
$$S_E(f) = \frac{c(f)}{m} = \frac{m - |R(f)|}{m} = 1 - \frac{|R(f)|}{m}$$

Extra data edges between mapped vertices (internal chords not in $E_Q$) and edges leaving the mapped vertex set (external cut edges) do not reduce $S_E(f)$ and do not affect validity (non-induced matching).

### 3.3. Primary Parameter $k$ and Connectivity Safety Bound
The primary matching threshold is an integer $k$:
$$0 \le k < \lambda(Q)$$

A mapping satisfies the missing edge budget if and only if:
$$|R(f)| \le k$$

> [!NOTE]
> If a fractional similarity threshold $\tau \in (0, 1]$ is accepted by an external API, the integer budget must be computed as:
> $$k = m - \lceil \tau \cdot m \rceil$$
> using exact rational/decimal arithmetic (never binary floating-point rounding at threshold boundaries). The primary contract for D* is the integer budget $k$.

---

## 4. Connectivity Semantics: S1 vs S2

### 4.1. Definition of S2 (Matched-Query Connectivity)
The **S2 condition** requires that the subgraph of $Q$ formed by the *matched query edges*:
$$Q[E_Q \setminus R(f)] = (V_Q, E_Q \setminus R(f))$$
is **spanning** (contains all vertices in $V_Q$) and **connected** (consists of exactly 1 connected component).

### 4.2. Distinction from S1 (Data Subgraph Induced Connectivity)
- **S1 (Legacy Model C STRICT)** checks whether the induced data subgraph $G_t[f(V_Q)]$ is connected using data edges.
- In S1, $G_t[f(V_Q)]$ can be connected via extra internal chords or edges with mismatched labels, even if the matched query edges are disconnected.
- In S2, connectivity is evaluated strictly on query edges that match both topologically and in label.

### 4.3. Invariant under $k < \lambda(Q)$
**Lemma (Automatic S2 Satisfaction):**
Let $Q$ be a connected graph with global edge connectivity $\lambda(Q)$. For any mapping $f$ with $|R(f)| \le k < \lambda(Q)$, the matched query subgraph $Q[E_Q \setminus R(f)]$ is guaranteed to be spanning and connected.

*Proof:*
$Q[E_Q \setminus R(f)]$ has vertex set $V_Q$ by definition, so it is spanning. Suppose for contradiction that it is disconnected. Then there exists a non-trivial partition of $V_Q$ into $(S, V_Q \setminus S)$ with no edges of $E_Q \setminus R(f)$ crossing the cut. Thus, all query edges crossing $(S, V_Q \setminus S)$ must belong to $R(f)$. By definition of global edge connectivity, the number of query edges crossing any non-trivial cut is at least $\lambda(Q)$. Therefore, $|R(f)| \ge \lambda(Q) > k$, contradicting $|R(f)| \le k$. Hence $Q[E_Q \setminus R(f)]$ must be connected. $\square$

> [!IMPORTANT]
> The independent M0 Oracle **must explicitly check S2** during evaluation, verifying that $Q[E_Q \setminus R(f)]$ is spanning and connected, even though the $k < \lambda(Q)$ precondition guarantees it. Furthermore, any input specifying $k \ge \lambda(Q)$ must be rejected by the public API.

---

## 5. Acceptance Predicate

A candidate ordered tuple $T = (v_1, \dots, v_n)$ corresponds to mapping $f(u_i) = v_i$ for $(u_1, \dots, u_n) = \text{sorted}(V_Q)$.
$f$ is an **accepted embedding** at time $t$ if and only if all of the following hold:
1. **Length**: $|T| = |V_Q|$.
2. **Universe**: For all $v \in T$, $v \in V_0$.
3. **Injectivity**: All elements of $T$ are distinct ($|\text{set}(T)| = |V_Q|$).
4. **Vertex Labels**: For all $i$, $\ell_G(v_i) = \ell_Q(u_i)$.
5. **Budget**: $|R(f)| \le k$.
6. **S2 Connectivity**: $Q[E_Q \setminus R(f)]$ is spanning and connected.

---

## 6. Dynamic Update and Event Semantics

### 6.1. Valid Single-Edge Updates
The update stream consists of discrete single-edge modifications:
- $\text{INSERT}(x, y, \ell)$: Add edge $\{x, y\}$ with label $\ell$ to $E_G$.
  - Preconditions: $x, y \in V_0$, $x \ne y$, $\{x, y\} \notin E_G$.
- $\text{DELETE}(x, y, \ell)$: Remove edge $\{x, y\}$ with label $\ell$ from $E_G$.
  - Preconditions: $x, y \in V_0$, $x \ne y$, $\{x, y\} \in E_G$, and $\ell_G(\{x, y\}) = \ell$.

### 6.2. Atomic Update Validation and Fail-Closed Behavior
Any invalid update must raise a specific, typed exception and leave the graph state, active mapping set, and event stream completely unchanged (fail-closed atomic update):
- `UnknownVertexError`: Endpoint not in $V_0$.
- `SelfLoopError`: $x = y$.
- `DuplicateEdgeInsertionError`: Inserting an existing edge.
- `NonexistentEdgeDeletionError`: Deleting a non-existent edge.
- `EdgeLabelMismatchDeletionError`: Deleting an edge with incorrect label.
- `UnsupportedOperationError`: Batch updates, vertex insertions/deletions, or label modifications.

### 6.3. Event Semantics
Let $M_t$ denote the set of accepted ordered tuples at step $t$.
Following update $\Delta_t$:
- $\text{ENTERED}_t = M_t \setminus M_{t-1}$
- $\text{EXITED}_t = M_{t-1} \setminus M_t$

No other event types (such as `SCORE_CHANGED`) are emitted in the D* API. If an existing mapping changes score from $|R| = 0$ to $|R| = 1 \le k$, it remains in both $M_{t-1}$ and $M_t$; hence it produces neither an `ENTERED` nor an `EXITED` event.

### 6.4. Boundary Event Characterization (Diagnostic Property)
For mappings $f$ that are **anchored** to the updated data edge $\{x, y\}$ (i.e. there exists $\{u, v\} \in E_Q$ such that $\{f(u), f(v)\} = \{x, y\}$ and $\ell_Q(\{u, v\}) = \ell$):
- **On $\text{INSERT}$**: Evaluated on $G_t$ ($G_{\text{after}}$), $f \in \text{ENTERED}_t \iff |R_t(f)| = k$.
- **On $\text{DELETE}$**: Evaluated on $G_{t-1}$ ($G_{\text{before}}$), $f \in \text{EXITED}_t \iff |R_{t-1}(f)| = k$.

This property forms the theoretical basis for candidate event-driven search algorithms (such as M2), but M0 must compute true snapshot set differences as the ground truth.

---

## 7. Gate 0A: Pending Advisor Questions

The following four architectural questions are pending formal advisor determination:

| # | Question | Current Working Assumption in D* | Rationale / Fallback |
|---|---|---|---|
| **Q1** | Is direct modification of RapidFlow C++ source mandatory, or is a standalone Python/C++ engine evaluated against RapidFlow baseline acceptable? | Standalone verification first; RapidFlow baseline used for comparison. | If direct C++ modification is mandatory, porting M1/M2 to C++ becomes a critical milestone. |
| **Q2** | Is the primary research deliverable COUNT (number of matches) or explicit ordered tuples ($\text{ENTERED}/\text{EXITED}$)? | Explicit ordered tuples for correctness; COUNT evaluated separately for M1-count. | Never conflate count-only smoke tests with full embedding enumeration. |
| **Q3** | Is `SCORE_CHANGED` required in addition to `ENTERED`/`EXITED`? | No. Only `ENTERED` and `EXITED` are emitted. | Adding `SCORE_CHANGED` requires redesigning leaf predicates and boundary lemmas. |
| **Q4** | Are Edge Recall, simple labeled graphs, $k < \lambda(Q)$, and Diamond ($k=1$)/$K_4$ ($k \in \{1, 2\}$) accepted for MVP? | Yes. Working contract locks this domain. | Ensures mathematical guarantees for S2 and manageable enumeration space. |

---

## 8. JSON API and Serialization Schema

### 8.1. Static Snapshot Output Schema
```json
{
  "semantics_version": "dstar-v1.0",
  "status": "SUCCESS",
  "query_vertex_order": [1, 2, 3, 4],
  "k": 1,
  "m": 5,
  "lambda": 2,
  "snapshot": [
    [10, 20, 30, 40]
  ]
}
```

### 8.2. Dynamic Stream Output Schema
```json
{
  "semantics_version": "dstar-v1.0",
  "status": "SUCCESS",
  "query_vertex_order": [1, 2, 3, 4],
  "k": 1,
  "m": 5,
  "lambda": 2,
  "updates": [
    {
      "step": 1,
      "op": "INSERT",
      "edge": [20, 30],
      "label": 0,
      "entered": [
        [10, 20, 30, 40]
      ],
      "exited": []
    }
  ]
}
```
