# RF-02 Semantics Contract: Exact Continuous Subgraph Matching (CSM)

This document freezes the mathematical semantics, graph data model, dynamic update rules, and verification criteria for **RF-02 (Gate G1: Independent Correctness Verification)** in the Seminar_RapidFlow research project.

---

## 1. Mathematical Definitions

### 1.1. Data Graph and Query Graph
A labeled simple undirected graph is defined as a 4-tuple:
$$G = (V, E, L_V, L_E)$$
where:
- $V \subset \mathbb{N}_0$ is a finite set of vertex identifiers (0-indexed contiguous integers $0, 1, \dots, |V|-1$).
- $E \subseteq \{\{u, v\} \mid u, v \in V, u \neq v\}$ is a set of undirected edges. There are no self-loops and no multi-edges.
- $L_V: V \to \mathbb{N}_0$ is a vertex labeling function assigning an integer label to each vertex.
- $L_E: E \to \mathbb{N}_0$ is an edge labeling function assigning an integer label to each undirected edge.

Let $Q = (V_Q, E_Q, L_{V_Q}, L_{E_Q})$ denote a fixed query graph, where $|V_Q| \ge 1$ and $Q$ is connected.
Let $G_t = (V_{G_t}, E_{G_t}, L_{V_{G_t}}, L_{E_{G_t}})$ denote the dynamic data graph at discrete update time step $t \in \{0, 1, \dots, m\}$.

### 1.2. Exact Non-Induced Subgraph Monomorphism
An **embedding** (or match) of $Q$ in $G_t$ is an injective function:
$$f: V_Q \to V_{G_t}$$
satisfying the following three conditions:

1. **Injectivity (Distinct Vertices):**
   $$\forall u, v \in V_Q, \; u \neq v \implies f(u) \neq f(v)$$
2. **Vertex Label Preservation:**
   $$\forall u \in V_Q, \; L_{V_Q}(u) = L_{V_{G_t}}(f(u))$$
3. **Query Edge and Edge Label Preservation:**
   $$\forall \{u, v\} \in E_Q, \; \{f(u), f(v)\} \in E_{G_t} \quad \text{and} \quad L_{E_Q}(\{u, v\}) = L_{E_{G_t}}(\{f(u), f(v)\})$$

#### Non-Induced Semantics
The matching semantics is **non-induced**. If $\{u, v\} \notin E_Q$, the presence or absence of $\{f(u), f(v)\} \in E_{G_t}$ has no effect on whether $f$ is a valid embedding. Extra edges in the data graph are explicitly permitted.

#### Symmetries and Automorphisms
Every distinct injective mapping function $f: V_Q \to V_{G_t}$ is counted as a distinct embedding.
No symmetry quotienting or equivalence-class reduction is applied to the reported embedding set.
Specifically, if $Q$ has non-trivial automorphisms $\operatorname{Aut}(Q)$, then for every valid embedding $f$ and every non-identity automorphism $\sigma \in \operatorname{Aut}(Q)$, the composition $f \circ \sigma$ is also a valid distinct embedding, and both $f$ and $f \circ \sigma$ must appear in the embedding set.

### 1.3. Canonical Representation of an Embedding
For a query graph with vertices $V_Q = \{q_0, q_1, \dots, q_{k-1}\}$ ordered naturally by their identifiers ($q_0 < q_1 < \dots < q_{k-1}$), an embedding $f$ is uniquely represented as an ordered tuple of data vertex identifiers:
$$\tau(f) = \big(f(q_0), f(q_1), \dots, f(q_{k-1})\big) \in V_{G_t}^k$$

Let $\mathcal{M}(Q, G_t)$ denote the set of canonical tuples representing all valid embeddings of $Q$ in $G_t$:
$$\mathcal{M}(Q, G_t) = \big\{\tau(f) \mid f \text{ is a valid non-induced subgraph monomorphism from } Q \text{ to } G_t\big\}$$

---

## 2. Dynamic Update Stream Semantics

At time step $t=0$, the system starts with initial data graph $G_0$.
The dynamic stream consists of an ordered sequence of $m$ single-edge updates:
$$\mathcal{S} = [u_1, u_2, \dots, u_m]$$
where each update $u_t$ is either an insertion (`+`) or a deletion (`-`):
$$u_t = (\text{op}_t, x_t, y_t, l_t), \quad \text{op}_t \in \{+, -\}, \; x_t, y_t \in V_G, \; x_t \neq y_t, \; l_t \in \mathbb{N}_0$$

### 2.1. Edge Insertion ($u_t = (+, x, y, l)$)
- **State Transition:**
  $$E_{G_t} = E_{G_{t-1}} \cup \{\{x, y\}\}, \quad L_{E_{G_t}}(\{x, y\}) = l$$
- **Precondition:** $\{x, y\} \notin E_{G_{t-1}}$. (Duplicate edge insertions are invalid).
- **Added Embeddings ($\Delta_t^+$):**
  $$\Delta_t^+ = \mathcal{M}(Q, G_t) \setminus \mathcal{M}(Q, G_{t-1})$$
  Because non-induced matching is monotonic under edge addition ($E_{G_{t-1}} \subset E_{G_t} \implies \mathcal{M}(Q, G_{t-1}) \subseteq \mathcal{M}(Q, G_t)$), every embedding in $\Delta_t^+$ must utilize the newly inserted edge $\{x, y\}$.
- **Removed Embeddings ($\Delta_t^-$):**
  $$\Delta_t^- = \emptyset$$

### 2.2. Edge Deletion ($u_t = (-, x, y, l)$)
- **State Transition:**
  $$E_{G_t} = E_{G_{t-1}} \setminus \{\{x, y\}\}$$
- **Precondition:** $\{x, y\} \in E_{G_{t-1}}$ with $L_{E_{G_{t-1}}}(\{x, y\}) = l$. (Deleting non-existent or mismatched edges is invalid).
- **Added Embeddings ($\Delta_t^+$):**
  $$\Delta_t^+ = \emptyset$$
- **Removed Embeddings ($\Delta_t^-$):**
  $$\Delta_t^- = \mathcal{M}(Q, G_{t-1}) \setminus \mathcal{M}(Q, G_t)$$
  Every embedding in $\Delta_t^-$ utilized the deleted edge $\{x, y\}$ in $G_{t-1}$.

---

## 3. RapidFlow Baseline Output Contract vs Oracle Delta

### 3.1. RapidFlow Aggregate Output
The pristine RapidFlow executable (`streaming_engine.cpp`) accumulates the delta result count across all stream updates into a single scalar counter `result_count_`:
- For insertion $u_t = (+, x, y, l)$, RapidFlow searches for all embeddings containing $\{x, y\}$ in $G_t$ and increments:
  $$\text{result\_count} \gets |\Delta_t^+|$$
- For deletion $u_t = (-, x, y, l)$, RapidFlow searches for all embeddings containing $\{x, y\}$ in $G_{t-1}$ (before removal) and increments:
  $$\text{result\_count} \gets |\Delta_t^-|$$
At the conclusion of processing a stream of length $m$, RapidFlow prints:
$$\text{Result count: } C_m = \sum_{t=1}^m \delta_t^{\text{RapidFlow}}$$
where $\delta_t^{\text{RapidFlow}} = |\Delta_t^+|$ for insertions and $|\Delta_t^-|$ for deletions.

### 3.2. Prefix Replay Differential Protocol
Because pristine RapidFlow prints only the aggregate sum $C_m$, per-update verification is executed using **Prefix Replay**:
For a stream of length $m$, the runner executes RapidFlow on prefixes $P_k = [u_1, \dots, u_k]$ for each $k \in \{1, \dots, m\}$ starting from the exact same initial state $G_0$.
Let $C_k$ be the `Result count` reported for prefix $P_k$, with $C_0 = 0$.
The inferred RapidFlow delta for update $k$ is:
$$\Delta_k^{\text{inferred}} = C_k - C_{k-1}$$

### 3.3. Correctness Verification Equalities
For each update $k \in \{1, \dots, m\}$:
- If $\text{op}_k = +$:
  $$\Delta_k^{\text{inferred}} \stackrel{?}{=} |\Delta_k^+| \quad \text{and} \quad |\Delta_k^-| = 0$$
- If $\text{op}_k = -$:
  $$\Delta_k^{\text{inferred}} \stackrel{?}{=} |\Delta_k^-| \quad \text{and} \quad |\Delta_k^+| = 0$$

---

## 4. File Formats and Scope Boundaries

### 4.1. Supported Graph File Format (`data.graph`, `query.graph`)
Text file containing:
- Vertex definition lines: `v <vertex_id> <vertex_label>`
  - `<vertex_id>`: unsigned integer, strictly contiguous $0, 1, \dots, |V|-1$.
  - `<vertex_label>`: unsigned integer.
- Edge definition lines: `e <src_id> <dst_id> <edge_label>`
  - `<src_id>`, `<dst_id>`: valid vertex IDs, $<src_id> \ne <dst_id>$.
  - `<edge_label>`: unsigned integer.
  - Represents an undirected edge between `<src_id>` and `<dst_id>`.

### 4.2. Supported Stream File Format (`stream.update`)
Text file containing one update per line:
- Insertion: `e <src_id> <dst_id> <edge_label>`
- Deletion: `-e <src_id> <dst_id> <edge_label>`

### 4.3. Explicitly Unsupported / Rejected Features
The following cases are **out of scope** for RF-02 and will be rejected:
1. Dynamic vertex additions or deletions (`v ...` or `-v ...` in stream).
2. Directed edge matching.
3. Multi-graphs (multiple edges between the same pair of vertices).
4. Self-loops (edges where $u = v$).
5. Disconnected query graphs $Q$ (unless studying reduced queries specifically isolated).
6. Non-contiguous vertex IDs or negative labels/IDs.
7. Invalid dynamic operations: inserting an already existing edge, or deleting a non-existent edge.
8. Induced subgraph matching.
9. Approximate / heuristic subgraph matching.

---

## 5. Verification Gates Definitions

- **Gate G1-A (COUNT-CORRECTNESS):**
  - Independent Oracle passes all self-tests.
  - For every supported fixture and every update step $k$, $\Delta_k^{\text{inferred}} = \text{expected\_delta}$.
  - Fail-closed execution: valid binary exit 0, updates num matches edge process count, no memory/time limits exceeded.
- **Gate G1-B (MAPPING-CORRECTNESS):**
  - Requires extracting the exact set of canonical tuples $\mathcal{M}_{\text{actual}}$ from RapidFlow and proving $\mathcal{M}_{\text{actual}} = \mathcal{M}_{\text{oracle}}$ at every step.
  - If pristine RapidFlow does not export mappings, G1-B must be declared **UNVERIFIED / BLOCKED** unless and until an approved diagnostic tool is authorized by the user.
- **Gate G1 OVERALL:**
  - Evaluated as `COUNT-PASS / MAPPING-UNVERIFIED` when G1-A passes and G1-B is blocked.
  - Must NOT be reported as an unconditional PASS without G1-B verification.
