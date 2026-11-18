# DSTAR-02 Engine Contract & Specification

**Status:** FROZEN ENGINE CONTRACT
**Date:** 2026-11-18
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — D* Feasibility Route
**Scope:** DSTAR-02 (Issue #24) — M1-enum baseline, bare M2 event engine, and independent tuple correctness.
**Parent Working Contract:** `docs/dstar_problem_contract_v1.md` (Issue #22 / DSTAR-01)
**Design Reference:** RapidFlow-APX Research Direction & Algorithm Design (18/11/2026, Google Doc `1-xOypqAphEQ3VXxFPzQeABKhWh_WKePt_gu1uNPehVk`, Section 7 & Appendices G/H)

---

## 1. Overview and Problem Definition

DSTAR-02 transitions D* from static snapshot oracle foundations to **direct incremental event enumeration**.
Given a dynamic graph stream $G_t$ undergoing single edge insertions and deletions, the goal is to emit the exact set of newly qualifying embeddings (`ENTERED`) and newly disqualified embeddings (`EXITED`) for a query graph $Q$ under **Edge Recall** within distance budget $k$.

### 1.1. Core Mathematical Properties
1. **Simple Undirected Labeled Graphs**: Both $Q = (V_Q, E_Q)$ and $G = (V_G, E_G)$ are simple undirected graphs with explicit vertex labels ($L_V$) and edge labels ($L_E$).
2. **Fixed Data Universe $V_0$**: Data graph vertices $V_G = V_0$ and their labels $L_{V,G}$ are fixed for the entire stream lifetime.
3. **Injective Mapping**: $f: V_Q \to V_G$ is strictly injective: $\forall u \ne v \in V_Q, f(u) \ne f(v)$.
4. **Vertex Invariance**: $\forall u \in V_Q, L_{V,G}(f(u)) = L_{V,Q}(u)$.
5. **Missing Edge Set**:
   $$R(f) = \left\{ \{u, v\} \in E_Q \mid \{f(u), f(v)\} \notin E_G \lor L_{E,G}(\{f(u), f(v)\}) \ne L_{E,Q}(\{u, v\}) \right\}$$
6. **Domain Constraint**:
   $$0 \le k < \lambda(Q)$$
   where $\lambda(Q)$ is the global edge-connectivity of $Q$. In this domain, $Q[E_Q \setminus R(f)]$ is guaranteed to be spanning and connected (S2 connectivity).
7. **Canonical Ordered Tuple Convention**:
   Every embedding $f$ is uniquely serialized as:
   $$T(f) = (f(u_1), f(u_2), \dots, f(u_n)) \quad \text{where } (u_1, u_2, \dots, u_n) = \text{sorted}(V_Q)$$
   Ascending numerical order of the *original* query vertex IDs is strictly enforced across all engines. DFS internal visit order is never used as the output tuple order.
8. **Automorphisms Preserved**: All symmetric embeddings are emitted as distinct ordered tuples. No quotienting or isomorphism collapse is performed.

---

## 2. Event Semantics and Snapshot Timing

For any discrete edge update $e = \{x, y\}$ with label $L$:

### 2.1. INSERT Event Search
- **Update Definition**: Edge $\{x, y\}$ with label $L$ is inserted into $G$.
  $$G_{\text{after}} = G_{\text{before}} \cup \{(\{x, y\}, L)\}$$
- **Snapshot Difference**:
  $$\text{ENTERED} = M(G_{\text{after}}) \setminus M(G_{\text{before}}), \quad \text{EXITED} = \emptyset$$
- **Event Boundary Lemma**:
  An embedding $f$ belongs to `ENTERED` if and only if:
  1. $f$ maps some query edge $q = \{u, v\} \in E_Q$ (with $L_{E,Q}(q) = L$) to the inserted data edge $\{x, y\}$.
  2. $|R_{\text{after}}(f)| == k$.
  *(Proof: In $G_{\text{before}}$, $q$ was missing, so $|R_{\text{before}}(f)| = |R_{\text{after}}(f)| + 1$. If $|R_{\text{after}}(f)| < k$, then $|R_{\text{before}}(f)| \le k$, meaning $f$ was already valid in $G_{\text{before}}$ and thus did not enter at step $t$. Hence, $|R_{\text{after}}(f)| == k$ is a necessary and sufficient boundary condition).*
- **Search Execution**: Runs on $G_{\text{after}}$ anchored at $\{x, y\}$, requiring leaf condition $|R(f)| == k$.

### 2.2. DELETE Event Search
- **Update Definition**: Edge $\{x, y\}$ with label $L$ is removed from $G$.
  $$G_{\text{after}} = G_{\text{before}} \setminus \{(\{x, y\}, L)\}$$
- **Snapshot Difference**:
  $$\text{ENTERED} = \emptyset, \quad \text{EXITED} = M(G_{\text{before}}) \setminus M(G_{\text{after}})$$
- **Event Boundary Lemma**:
  An embedding $f$ belongs to `EXITED` if and only if:
  1. $f$ mapped some query edge $q = \{u, v\} \in E_Q$ (with $L_{E,Q}(q) = L$) to the deleted data edge $\{x, y\}$.
  2. $|R_{\text{before}}(f)| == k$.
  *(Proof: In $G_{\text{after}}$, $q$ becomes missing, so $|R_{\text{after}}(f)| = |R_{\text{before}}(f)| + 1$. If $|R_{\text{before}}(f)| < k$, then $|R_{\text{after}}(f)| \le k$, meaning $f$ remains valid in $G_{\text{after}}$ and thus did not exit at step $t$. Hence, $|R_{\text{before}}(f)| == k$ is a necessary and sufficient boundary condition).*
- **Search Execution**: Runs on $G_{\text{before}}$ anchored at $\{x, y\}$, requiring leaf condition $|R(f)| == k$.

> [!IMPORTANT]
> **No `|R| <= k` Leaf Condition in Event Search**: Event engines enforce $|R| == k$ at leaf evaluation. Evaluating $|R| \le k$ at leaves would falsely include embeddings that were already valid before the update, corrupting the delta event set.

---

## 3. Engine Architecture & Independence

Two production event engines are provided:

```
                            +-----------------------------+
                            |  scripts/dstar_event_common |
                            |  - Graph representation     |
                            |  - Adjacency & edge lookup  |
                            |  - Update validation        |
                            |  - Event sink & counters    |
                            |  - Resource limits          |
                            +--------------+--------------+
                                           |
                    +----------------------+----------------------+
                    |                                             |
     +--------------v---------------+              +--------------v---------------+
     |     scripts/dstar_m1_enum    |              |     scripts/dstar_m2_event   |
     |  - Anchored Exact variants   |              |  - Bare M2 DFS               |
     |  - For each D subset of E_Q  |              |  - PRESENT / MISSING branches|
     |  - Match Q - D, R(f) == D    |              |  - Deferred vertex mapping   |
     +------------------------------+              +------------------------------+
```

### 3.1. Strict Independence Constraints
1. **Production Isolation**: Neither `dstar_m1_enum.py` nor `dstar_m2_event.py` imports or calls `scripts/dstar_oracle.py`, `scripts/apx02_oracle.py`, or any APX-03 native baseline.
2. **No Recompute-and-Diff**: Production engines never enumerate full snapshots to compute deltas; they search for event tuples directly around the updated data edge.
3. **Shared Primitives**: Both engines share identical graph storage, labeled adjacency lookup, vertex/edge validation, output sinks, and work counter abstractions in `scripts/dstar_event_common.py`. Their distinct search strategies (DFS) are the sole subject of experimental investigation.

---

## 4. M1-enum-Exact-k Algorithm Specification

`M1EnumEngine` decomposes approximate continuous matching into an exact baseline over variant queries.
For an edge update $\{x, y\}$ with label $L$:

1. **Anchoring**:
   For each query edge $q = \{u, v\} \in E_Q$ with $L_{E,Q}(q) = L$:
   - Consider both orientations: $(u \to x, v \to y)$ and $(u \to y, v \to x)$.
   - Filter out orientations where vertex labels mismatch: $L_{V,Q}(u) \ne L_{V,G}(x)$ or $L_{V,Q}(v) \ne L_{V,G}(y)$.
2. **Exact Variant Generation**:
   For each subset $D \subseteq E_Q \setminus \{q\}$ such that $|D| = k$:
   - Form variant query $Q_D = (V_Q, E_Q \setminus D)$.
   - Because $k < \lambda(Q)$, $Q_D$ is guaranteed connected and spanning on $V_Q$.
   - For $k = 0$, $D = \emptyset$, yielding exactly one variant ($Q$ itself).
3. **Anchored Exact Subgraph Matching**:
   - Establish a deterministic connected search order on $V_Q$ beginning with anchor vertices $u, v$.
   - For each unmapped vertex $w$ along the search order:
     - Generate candidates $C(w)$ by intersecting the labeled adjacency lists of all already-mapped neighbors in $Q_D$.
     - Exclude already-used data vertices ($z \notin \text{used}$).
     - Verify vertex label compatibility: $L_{V,G}(z) == L_{V,Q}(w)$.
4. **Exact Equivalence Verification**:
   For each complete mapping $f: V_Q \to V_G$:
   - Verify that every edge in $D$ is strictly missing in $G$: $R(f) == D$.
   - If and only if $R(f) == D$, emit $T(f)$ to the output sink.
5. **Uniqueness Guarantee**:
   Each valid event embedding $f$ has a unique query edge $q$ mapping to the data update edge, a unique orientation, and a unique missing edge set $D = R(f)$. Hence, no duplicate emissions occur across variants, and no hash deduplication is needed.

---

## 5. Bare M2-Event-k Algorithm Specification

`M2EventEngine` performs direct depth-first search on $Q$ around the updated edge, branching on PRESENT vs MISSING edges and deferring unmapped vertices.

### 5.1. Search State
- `f_p`: Partial mapping from $V_Q \to V_G \cup \{\text{None}\}$.
- `used`: Set of data vertices currently in use ($\text{used} = \{f_p(u) \mid f_p(u) \ne \text{None}\}$).
- `status`: Map from query edge $e \in E_Q$ to `EdgeStatus` $\in \{\text{UNKNOWN}, \text{PRESENT}, \text{MISSING}\}$.
- `R_p`: Set of query edges currently committed to `MISSING`.

### 5.2. Search Pipeline
1. **Anchoring**:
   For each query edge $q = \{u, v\} \in E_Q$ with $L_{E,Q}(q) = L$:
   - For each label-compatible orientation ($u \to x, v \to y$ and $u \to y, v \to x$):
     - Initialize $f_p = [u \mapsto x, v \mapsto y]$, $\text{used} = \{x, y\}$.
     - Initialize $\text{status}[q] = \text{PRESENT}$, all other $\text{status}[e] = \text{UNKNOWN}$, $R_p = \emptyset$.
     - Resolve any additional query edges whose endpoints are both in $\{u, v\}$ (if any).
     - Recurse into DFS.
2. **Two-Endpoint Edge Resolution**:
   Whenever a query vertex $w$ is newly assigned, inspect every incident query edge $e = \{w, z\}$ where $z$ is already mapped:
   - If $\text{status}[e] == \text{MISSING}$: Verify that $\{f_p(w), f_p(z)\}$ is NOT present in $G$ with matching label. If present, prune branch.
   - If $\text{status}[e] == \text{PRESENT}$: Verify that $\{f_p(w), f_p(z)\}$ IS present in $G$ with matching label. If absent, prune branch.
   - If $\text{status}[e] == \text{UNKNOWN}$:
     - If $\{f_p(w), f_p(z)\}$ exists in $G$ with matching label: $\text{status}[e] \leftarrow \text{PRESENT}$.
     - Otherwise: $\text{status}[e] \leftarrow \text{MISSING}$, $R_p \leftarrow R_p \cup \{e\}$. If $|R_p| > k$, prune branch.
3. **Pruning & Leaf Checks**:
   - If $|R_p| > k$: Prune branch.
   - If all vertices in $V_Q$ are mapped:
     - Verify all query edges are resolved and commitments satisfied.
     - Check leaf condition: $|R_p| == k$.
     - If $|R_p| == k$, emit canonical ordered tuple $T(f)$ to sink.
     - Return.
4. **Frontier Selection**:
   If unmapped vertices remain:
   - Identify candidate frontier edges:
     $$\text{Frontier} = \{ e = \{u, v\} \in E_Q \mid \text{status}[e] == \text{UNKNOWN} \land (f_p(u) \ne \text{None}) \ne (f_p(v) \ne \text{None}) \}$$
   - **Deterministic Selection**: Select the edge $e = \{u, v\}$ with minimum canonical edge index.
   - **Empty Frontier Invariant Guard**:
     If $\text{Frontier} == \emptyset$ while unmapped vertices exist and $|R_p| \le k$:
     - Under $k < \lambda(Q)$, the cut between mapped and unmapped vertices has at least $\lambda(Q) > k$ edges. They cannot all be MISSING.
     - Raise `InvariantViolationError` and fail closed! Never emit an empty result or proceed.
5. **Branching**:
   Let $u$ be the mapped endpoint and $v$ be the unmapped endpoint of frontier edge $e$:
   - **PRESENT (CÓ) Branch**:
     - For each data neighbor $y \in \text{Adj}_G(f_p(u))$ with $L_{E,G}(\{f_p(u), y\}) == L_{E,Q}(e)$, $y \notin \text{used}$, and $L_{V,G}(y) == L_{V,Q}(v)$:
       - Assign $v \mapsto y$, add $y$ to $\text{used}$, set $\text{status}[e] \leftarrow \text{PRESENT}$.
       - Resolve all newly fully-mapped query edges incident to $v$.
       - Recurse into DFS.
       - Rollback state.
   - **MISSING (THIẾU) Branch**:
     - Set $\text{status}[e] \leftarrow \text{MISSING}$, add $e$ to $R_p$.
     - Do NOT assign $v$ (defer vertex assignment).
     - If $|R_p| \le k$:
       - Recurse into DFS to choose a different frontier edge.
     - Rollback state.

---

## 6. Output Sink and Duplicate Elimination Contract

The output sink (`EventSink`) enforces strict uniqueness at emission time:

```python
class EventSink:
    def __init__(self):
        self.raw_emissions: List[Tuple[int, ...]] = []

    def emit(self, mapping_tuple: Tuple[int, ...]) -> None:
        self.raw_emissions.append(mapping_tuple)

    def finalize(self) -> List[Tuple[int, ...]]:
        # Correctness assertion: raw emissions must contain ZERO duplicates
        assert len(self.raw_emissions) == len(set(self.raw_emissions)), (
            f"DUPLICATE EMISSION DETECTED: {len(self.raw_emissions)} raw vs "
            f"{len(set(self.raw_emissions))} unique"
        )
        return sorted(self.raw_emissions)
```

No engine is permitted to use `set()` internally to mask duplicate emissions. If duplicate tuples are emitted, the sink fails immediately.

---

## 7. Work Counters and Resource Limits

### 7.1. Counters Definition
- **Common Counters**:
  - `anchors_started`: Number of matching query-edge anchor orientations initialized.
  - `candidate_extensions`: Number of candidate vertex assignments attempted.
  - `edge_lookups`: Number of data edge existence and label lookups performed.
  - `vertex_label_checks`: Number of vertex label compatibility comparisons.
  - `emitted_tuples`: Number of event tuples emitted to sink.
- **M1 Specific**:
  - `variants_started`: Number of exact query variants evaluated ($Q - D$).
  - `raw_exact_results`: Number of complete exact embeddings found before $R(f) == D$ filtering.
- **M2 Specific**:
  - `edge_decisions`: Number of frontier PRESENT/MISSING decisions made.
  - `missing_branches`: Number of times the MISSING branch was taken.
  - `commitment_checks`: Number of times deferred MISSING commitments were verified.

### 7.2. Resource Limits & Fail-Closed Guard
- `max_data_vertices`: 8 (default)
- `max_query_vertices`: 6 (default)
- `max_candidates`: 100,000 (candidate space upper bound $P(|V_G|, |V_Q|)$)
- `max_work`: 500,000 (maximum allowed `candidate_extensions` + `edge_decisions` per update)
- `max_stream_steps`: 100

Exceeding `max_work` or graph vertex caps raises `ResourceLimitError` and results in CLI exit code 2.

---

## 8. CLI & Status Code Specification

Command:
```bash
python3 scripts/dstar_event_cli.py \
  --engine m1|m2 \
  --query PATH \
  --graph PATH \
  --stream PATH \
  --k K \
  [--max-work N] \
  [--output PATH]
```

### 8.1. JSON Output Schema
```json
{
  "semantics_version": "dstar-v1.0",
  "engine": "m1" | "m2",
  "engine_version": "dstar-m1-v1.0" | "dstar-m2-v1.0",
  "query_vertex_order": [1, 2, 3, 4],
  "k": 1,
  "lambda": 2,
  "steps": [
    {
      "step_index": 0,
      "operation": "INSERT",
      "edge": [2, 3],
      "label": 0,
      "entered": [[1, 2, 3, 4]],
      "exited": [],
      "counters": { ... }
    }
  ],
  "total_entered": 1,
  "total_exited": 0,
  "counters": { ... },
  "status": "SUCCESS" | "ERROR" | "RESOURCE_LIMIT"
}
```

### 8.2. Process Exit Codes
- `0`: Execution completed successfully (`status: "SUCCESS"`).
- `1`: Invalid input schema, domain violation ($k \ge \lambda(Q)$), invariant violation, or runtime exception (`status: "ERROR"`).
- `2`: Resource limit exceeded (`status: "RESOURCE_LIMIT"`).
