# APX-02: Independent Approximate CSM Ground-Truth Oracle — Technical Specification

- **Document Version:** `1.0.0-ORACLE-SPEC`
- **Gate:** Gate A1 (APX-02 — Ground-Truth Correctness Oracle)
- **Active Issue:** [#16 — [APX-02] Independent Approximate CSM Ground-Truth Oracle](https://github.com/iamqthanq/Seminar_RapidFlow/issues/16)
- **Implementation Branch:** `feature/rf16-apx02-oracle`
- **Integration Target:** `QuangThang`
- **Implementation Script:** `scripts/apx02_oracle.py`
- **Test Suite:** `tests/test_apx02_oracle.py`

---

## 1. Scientific Objective & Architectural Role

The **APX-02 Oracle** provides a completely independent, mathematically rigorous, pure Python 3 standard library reference implementation of **Model C Approximate Continuous Subgraph Matching (CSM)**.

Its fundamental purpose is to generate verified, uncompromised ground-truth answers for:
1. Validating correctness of future Approximate CSM algorithm implementations (APX-03 and beyond).
2. Proving semantic properties (monotonicity, strict/relaxed inclusion, reversibility, automorphism score invariance).
3. Verifying the 16 reference microcases defined in APX-01 without relying on heuristic pruning or RapidFlow C++ search code.

> [!IMPORTANT]
> **Priority Hierarchy:**
> $$\mathbf{CORRECTNESS > REPRODUCIBILITY > COMPLETENESS\ OF\ EVIDENCE > PERFORMANCE}$$
> The Oracle deliberately avoids degree-based candidate pruning, search-order heuristics, and automorphism quotienting. All ordered injective mappings are exhaustively enumerated or directly evaluated.

---

## 2. Formal Mathematical Contract (Model C)

### 2.1. Graph Representation
- **Query Graph:** $Q = (V_Q, E_Q, \ell_Q)$
- **Dynamic Data Graph:** $G_t = (V_t, E_t, \ell_t)$
- Graphs are undirected, simple, vertex-labeled ($\Sigma_V$), and edge-labeled ($\Sigma_E$).
- Edge keys are canonically ordered pairs $(u, v)$ with $u < v$.

### 2.2. Candidate Ordered Injective Mapping
For query vertices $V_Q = \{q_0, q_1, \dots, q_{k-1}\}$ sorted in canonical ascending ID order ($q_0 < q_1 < \dots < q_{k-1}$):
$$f = (f(q_0), f(q_1), \dots, f(q_{k-1})) \in V_t^k$$
- **Injectivity:** $\forall i \ne j, f(q_i) \ne f(q_j)$. Non-injective mappings are rejected with cost $\infty$.
- **Hard Vertex Labels:** $\forall q \in V_Q, \ell_t(f(q)) = \ell_Q(q)$. Vertex label mismatch is a hard reject.

### 2.3. Deviation Scores

#### Missing-Edge Deviation $m_t(f)$
Defined via the edge satisfaction predicate:
$$\text{required\_edge\_matches}(u, v, f, t) \iff \Big(\{f(u), f(v)\} \in E_t \;\land\; \ell_t(\{f(u), f(v)\}) = \ell_Q(\{u, v\})\Big)$$
$$m_t(f) = \sum_{\{u, v\} \in E_Q} \mathbf{1}\left[\neg \text{required\_edge\_matches}(u, v, f, t)\right]$$
- Absent edge $\implies +1$.
- Present edge with wrong label $\implies +1$.
- Present edge with matching label $\implies 0$.

#### Extraneous Internal Extra Chords $x_{\mathrm{in},t}(f)$ (Diagnostic Only)
$$x_{\mathrm{in},t}(f) = \left|\left\{\{a, b\} \in E_t \mid a, b \in S_f \;\land\; \{f^{-1}(a), f^{-1}(b)\} \notin E_Q\right\}\right|$$
- **Crucial Rule:** An edge in $E_Q$ that exists with a wrong label contributes to $m_t(f)$, but does **NOT** contribute to $x_{\mathrm{in},t}(f)$ or $x_{\mathrm{out},t}(f)$ (verified by Case 09).
- $x_{\mathrm{in},t}(f)$ is **never penalized** by Model C acceptance.

#### Extraneous External Boundary Deviation $x_{\mathrm{out},t}(f)$
$$x_{\mathrm{out},t}(f) = \left|\left\{\{a, b\} \in E_t \mid a \in S_f \;\land\; b \in V_t \setminus S_f\right\}\right| = \sum_{a \in S_f} |\{b \in \mathcal{N}_{G_t}(a) \mid b \notin S_f\}|$$
- Counts distinct undirected cut edges leaving $S_f$.
- Each external cut edge is counted exactly once.

#### Topological Connectivity $\mathcal{C}_t(f)$
$$\mathcal{C}_t(f) \iff \text{components}(G_t[S_f]) == 1$$
- Evaluated strictly on data graph topology: present edges connect mapped vertices regardless of label agreement.

### 2.4. Final Model C Acceptance Predicate
A candidate mapping $f$ is accepted at time step $t$ if and only if:
$$\operatorname{Accept}_t(f) \iff \begin{cases}
f \text{ is injective on } V_Q \\
\forall u \in V_Q, \ell_t(f(u)) = \ell_Q(u) \\
m_t(f) \le \varepsilon_m \\
x_{\mathrm{out},t}(f) \le \varepsilon_x \quad (\text{if } \varepsilon_x \ne \infty) \\
\operatorname{ConnOK}(f) == \text{true}
\end{cases}$$

#### Connectivity Modes:
1. **STRICT Mode (Technical Default):**
   $$\operatorname{ConnOK}_{\mathrm{strict}}(f) \equiv \mathcal{C}_t(f)$$
2. **RELAXED_UNCONSTRAINED Mode:**
   $$\operatorname{ConnOK}_{\mathrm{relaxed}}(f) \equiv \text{true}$$

---

## 3. Scope Preconditions (APX-02 v1)

1. **Fixed Vertex Universe (Scope A):**
   $$V_t = V_0, \quad \forall t \in \mathbb{N}$$
   - Vertex IDs and vertex labels remain invariant across stream processing.
   - Stream updates only insert or delete edges among existing vertices in $V_0$.
   - Degree-0 (isolated) vertices remain members of $V_t$.
2. **Valid Updates Only (Scope B):**
   - **Valid Insertion ($+e = (u, v, l)$):** Requires $u, v \in V_0, u \ne v, \{u, v\} \notin E_{t-1}, l \in \Sigma_E$.
   - **Valid Deletion ($-e = (u, v, l)$):** Requires $\{u, v\} \in E_{t-1}, \ell_{t-1}(\{u, v\}) = l$.
   - **Atomic Error Handling:** Any invalid update raises an explicit exception (`UnknownVertexError`, `DuplicateEdgeInsertionError`, `NonexistentEdgeDeletionError`, `EdgeLabelMismatchDeletionError`, `SelfLoopError`) without mutating graph state or oracle cache.

---

## 4. Dynamic Snapshot Recompute-and-Diff

For each discrete stream update $\Delta e_t$:
1. State before update: $\mathcal{M}_{t-1}$.
2. Atomically validate and apply $\Delta e_t \implies G_t$.
3. Recompute accepted mappings $\mathcal{M}_t$.
4. Classify differential event sets:
   $$\operatorname{ENTERED}_t = \mathcal{M}_t \setminus \mathcal{M}_{t-1}$$
   $$\operatorname{EXITED}_t = \mathcal{M}_{t-1} \setminus \mathcal{M}_t$$
   $$\operatorname{SCORE\_CHANGED}_t = \left\{f \in \mathcal{M}_{t-1} \cap \mathcal{M}_t \mid (m_{t-1}(f), x_{\mathrm{out},t-1}(f)) \ne (m_t(f), x_{\mathrm{out},t}(f))\right\}$$
   $$\operatorname{UNCHANGED}_t = \left\{f \in \mathcal{M}_{t-1} \cap \mathcal{M}_t \mid (m_{t-1}(f), x_{\mathrm{out},t-1}(f)) == (m_t(f), x_{\mathrm{out},t}(f))\right\}$$

> [!NOTE]
> `SCORE_CHANGED` compares strictly the Model C vector $(m, x_{\mathrm{out}})$. Changes to internal chords $x_{\mathrm{in}}$ alone do not trigger `SCORE_CHANGED`.

---

## 5. API Reference & CLI Usage

### Python API
```python
from scripts.apx02_oracle import (
    SimpleGraph,
    Update,
    ConnectivityMode,
    evaluate_mapping,
    find_approximate_matches,
    DynamicOracle,
)

# 1. Direct Evaluation of a single candidate mapping (handles large probe graphs like Case 08)
eval_res = evaluate_mapping(Q, G, mapping=(0, 1, 2), eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
print(eval_res.score.m, eval_res.score.x_out, eval_res.accepted)

# 2. Exhaustive Enumeration of all accepted ordered mappings
matches = find_approximate_matches(Q, G, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
for mapping, res in matches.items():
    print(mapping, res.score)

# 3. Dynamic Differential Stream Processing
dyn = DynamicOracle(Q, G0, eps_m=1, eps_x=2, mode=ConnectivityMode.STRICT)
delta = dyn.apply_update(Update("+", 0, 5, 0))
print(f"ENTERED: {len(delta.entered)}, EXITED: {len(delta.exited)}")
```

### CLI Commands
```bash
# Static evaluation
python3 scripts/apx02_oracle.py \
  --query tests/fixtures/rf02/c01_path3_insert/query.graph \
  --data tests/fixtures/rf02/c01_path3_insert/data.graph \
  --eps-m 1 --eps-x 2 --mode STRICT

# Dynamic stream evaluation with JSON output
python3 scripts/apx02_oracle.py \
  --query tests/fixtures/rf02/c01_path3_insert/query.graph \
  --data tests/fixtures/rf02/c01_path3_insert/data.graph \
  --eps-m 0 --eps-x 0 --mode STRICT \
  --stream tests/fixtures/rf02/c01_path3_insert/stream.update \
  --output /tmp/c01_output.json
```
