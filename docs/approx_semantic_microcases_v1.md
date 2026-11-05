# APX-01: Micrograph Reference Test Cases — Hand-Reasoned Semantics & Acceptance

- **Document Version:** `1.0.0-PROPOSAL`
- **Gate:** Gate A0 (APX-01 — Semantics & Threshold Selection Protocol)
- **Status:** DRAFT WORKING SPECIFICATION / FOR PEER & ADVISOR REVIEW
- **Parent Issue:** [#12 — [APX-01] Gate A0: Approximate CSM Semantics & Threshold Selection Protocol](https://github.com/iamqthanq/Seminar_RapidFlow/issues/12)
- **Repository Target:** `iamqthanq/Seminar_RapidFlow` (Integration base: `QuangThang`)
- **Author:** Senior Graph Algorithms Researcher & Research Software Engineer

---

## 1. Methodology & Epistemic Separation

This catalog establishes **15 canonical micrograph test cases** designed for manual verification, automated oracle validation (APX-02), and edge-case benchmarking.

For every case, two strictly distinct conclusions are documented:
1. **Mathematical Formula Acceptance:** Does the candidate mapping $f$ satisfy the objective criteria $m_t(f) \le \varepsilon_m \land x_{\mathrm{out},t}(f) \le \varepsilon_x$?
2. **Semantic Relevance Judgment:** Does a domain specialist or research advisor consider this pattern structurally similar or relevant to query $Q$?

All graphs are undirected simple graphs. Unless explicitly noted, all vertex labels are $0$.

---

## 2. Comprehensive Micrograph Case Catalog

### Case 01: Exact Isomorphic Match
- **Query $Q$:** Path $P_3 = (u_1 - u_2 - u_3)$. $V_Q = \{u_1, u_2, u_3\}$, $E_Q = \{\{u_1, u_2\}, \{u_2, u_3\}\}$, edge labels all $0$.
- **Data Graph $G$:** $V_G = \{1, 2, 3\}$, $E_G = \{\{1, 2\}, \{2, 3\}\}$, edge labels all $0$.
- **Mapping $f$:** $f(u_1) = 1, f(u_2) = 2, f(u_3) = 3$. Image $S_f = \{1, 2, 3\}$.
- **Deviation Scores:**
  - $m(f) = 0$ (all query edges present with label 0).
  - $x_{\mathrm{in}}(f) = 0$ (no extra edges between $\{1, 2, 3\}$).
  - $x_{\mathrm{out}}(f) = 0$ (no edges leave $\{1, 2, 3\}$).
- **Formula Acceptance:**
  - $\varepsilon_m = 0, \varepsilon_x = 0$: **ACCEPTED**
  - $\varepsilon_m = 1, \varepsilon_x = 2$: **ACCEPTED**
- **Semantic Judgment:** Perfect structural match. Canonical positive ground truth.

---

### Case 02: Missing One Leaf Edge
- **Query $Q$:** Star $K_{1,3}$. Center $u_0$, leaves $u_1, u_2, u_3$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}, \{u_0, u_3\}\}$, label 0.
- **Data Graph $G$:** $V_G = \{0, 1, 2, 3\}$, $E_G = \{\{0, 1\}, \{0, 2\}\}$. (Edge $\{0, 3\}$ is absent).
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2, f(u_3) = 3$.
- **Deviation Scores:**
  - $m(f) = 1$ (edge $\{u_0, u_3\}$ missing between 0 and 3).
  - $x_{\mathrm{in}}(f) = 0$, $x_{\mathrm{out}}(f) = 0$.
- **Formula Acceptance:**
  - Exact ($\varepsilon_m = 0, \varepsilon_x = \infty$): **REJECTED** (not in exact matching set).
  - Approximate ($\varepsilon_m = 1, \varepsilon_x = 2$): **ACCEPTED** ($m=1 \le 1, x_{\mathrm{out}}=0 \le 2$).
- **Semantic Judgment:** Near-match. Two out of three communication links exist; candidate entity 3 is present but passive.

---

### Case 03: Missing Two Edges (Severe Omission)
- **Query $Q$:** Star $K_{1,3}$. Center $u_0$, leaves $u_1, u_2, u_3$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}, \{u_0, u_3\}\}$.
- **Data Graph $G$:** $V_G = \{0, 1, 2, 3\}$, $E_G = \{\{0, 1\}\}$. (Edges $\{0, 2\}$ and $\{0, 3\}$ are absent).
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2, f(u_3) = 3$.
- **Deviation Scores:**
  - $m(f) = 2$ (two missing edges).
  - $x_{\mathrm{in}}(f) = 0$, $x_{\mathrm{out}}(f) = 0$.
- **Formula Acceptance:**
  - $\varepsilon_m = 1, \varepsilon_x = 2$: **REJECTED** ($m=2 > 1$).
  - $\varepsilon_m = 2, \varepsilon_x = 2$: **ACCEPTED** ($m=2 \le 2$).
- **Semantic Judgment:** Structurally defective. Pattern has lost $66.7\%$ of its required connectivity; only a single edge remains.

---

### Case 04: Extra Internal Chord (Relaxed Induced)
- **Query $Q$:** Cycle $C_4 = (u_1 - u_2 - u_3 - u_4 - u_1)$. $|E_Q| = 4$.
- **Data Graph $G$:** Complete graph $K_4$ on vertices $\{1, 2, 3, 4\}$. $|E_G| = 6$.
- **Mapping $f$:** $f(u_i) = i$ for $i \in \{1, 2, 3, 4\}$.
- **Deviation Scores:**
  - $m(f) = 0$ (all 4 cycle edges exist).
  - $x_{\mathrm{in}}(f) = 2$ (cross-chords $\{1, 3\}$ and $\{2, 4\}$ exist in $G$).
  - $x_{\mathrm{out}}(f) = 0$ (no edges connect outside $\{1, 2, 3, 4\}$).
- **Formula Acceptance:**
  - Exact Non-Induced: **ACCEPTED** (non-induced matching ignores internal chords).
  - Exact Induced: **REJECTED** (chords violate induced condition).
  - Model C ($\varepsilon_m = 0, \varepsilon_x = 2$): **ACCEPTED** ($m=0, x_{\mathrm{out}}=0$).
- **Semantic Judgment:** Dense cluster. Contains the required cycle, but has higher internal communication density.

---

### Case 05: Extra External Tail: 1 Extra Branch (Advisor Target Example)
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}\}$.
- **Data Graph $G$:** Star $K_{1,3}$. Center $0$, leaves $1, 2, 4$. $E_G = \{\{0, 1\}, \{0, 2\}, \{0, 4\}\}$.
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$. Image $S_f = \{0, 1, 2\}$.
- **Deviation Scores:**
  - $m(f) = 0$ (query edges $\{0, 1\}$ and $\{0, 2\}$ both present).
  - $x_{\mathrm{in}}(f) = 0$ (no extra edges between $\{0, 1, 2\}$).
  - $x_{\mathrm{out}}(f) = 1$ (edge $\{0, 4\}$ connects mapped center $0 \in S_f$ to unmapped vertex $4 \notin S_f$).
- **Formula Acceptance:**
  - Exact Non-Induced: **ACCEPTED** ($m=0$).
  - Isolated Induced ($\varepsilon_x = 0$): **REJECTED** ($x_{\mathrm{out}} = 1 > 0$).
  - Clutter-Tolerant ($\varepsilon_m = 0, \varepsilon_x = 1$): **ACCEPTED** ($x_{\mathrm{out}} = 1 \le 1$).
- **Semantic Judgment:** Highly relevant. This is the exact primary positive example cited by the advisor: a star motif with one extra background tail.

---

### Case 06: Extra External Tail: 2 Extra Branches
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$.
- **Data Graph $G$:** Star $K_{1,4}$. Center $0$, leaves $1, 2, 4, 5$.
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$. $S_f = \{0, 1, 2\}$.
- **Deviation Scores:**
  - $m(f) = 0$.
  - $x_{\mathrm{in}}(f) = 0$.
  - $x_{\mathrm{out}}(f) = 2$ (edges $\{0, 4\}$ and $\{0, 5\}$ connect to external nodes).
- **Formula Acceptance:**
  - $\varepsilon_m = 0, \varepsilon_x = 1$: **REJECTED** ($x_{\mathrm{out}} = 2 > 1$).
  - $\varepsilon_m = 0, \varepsilon_x = 2$: **ACCEPTED** ($x_{\mathrm{out}} = 2 \le 2$).
- **Semantic Judgment:** Borderline. Two extra connections begin to dilate the pattern structure.

---

### Case 07: Extra External Tail: 30 Extra Branches (Noise Clutter)
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$.
- **Data Graph $G$:** Center $0$ connected to $1, 2$ and 30 additional peripheral nodes $4, \dots, 33$. ($\deg(0) = 32$).
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$.
- **Deviation Scores:**
  - $m(f) = 0$.
  - $x_{\mathrm{in}}(f) = 0$.
  - $x_{\mathrm{out}}(f) = 30$ (30 external edges incident to vertex $0$).
- **Formula Acceptance:**
  - $\varepsilon_m = 0, \varepsilon_x = 2$: **REJECTED** ($x_{\mathrm{out}} = 30 \gg 2$).
  - $\varepsilon_m = 0, \varepsilon_x = 30$: **ACCEPTED** ($x_{\mathrm{out}} \le 30$).
- **Semantic Judgment:** Irrelevant noise. The target 2-leaf star is submerged in a broad 32-node broadcast tree.

---

### Case 08: High-Degree NetFlow Hub
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$.
- **Data Graph $G$:** Vertex $0$ is a NetFlow core router interface with $\deg(0) = 1{,}000$, including edges to $1$ and $2$.
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$.
- **Deviation Scores:**
  - $m(f) = 0$.
  - $x_{\mathrm{out}}(f) = 998$ (998 external edges).
- **Formula Acceptance:**
  - $\varepsilon_x \le 30$: **REJECTED** ($x_{\mathrm{out}} = 998$).
  - Unbounded Clutter ($\varepsilon_x = \infty$): **ACCEPTED**.
- **Semantic Judgment:** Contextual conflict. Under exact non-induced semantics, this is a valid embedding. Under external degree penalty, hubs are completely disqualified.

---

### Case 09: Edge Label Mismatch
- **Query $Q$:** Path $P_3 = (u_1 - u_2 - u_3)$. Edge $\{u_1, u_2\}$ has label 1, edge $\{u_2, u_3\}$ has label 1.
- **Data Graph $G$:** Edge $\{1, 2\}$ has label 1, edge $\{2, 3\}$ has label **2**.
- **Mapping $f$:** $f(u_i) = i$.
- **Deviation Scores:**
  - $m(f) = 1$ (edge $\{2, 3\}$ exists in topology but label $2 \ne 1$).
  - $x_{\mathrm{in}}(f) = 0, x_{\mathrm{out}}(f) = 0$.
- **Formula Acceptance:**
  - Exact matching: **REJECTED**.
  - Approximate ($\varepsilon_m = 1, \varepsilon_x = 2$): **ACCEPTED** ($m=1 \le 1$).
- **Semantic Judgment:** Near-match. Protocol mismatch on one connection (e.g. TCP port 80 vs 8080).

---

### Case 10: Injectivity Violation (Hard Reject)
- **Query $Q$:** Path $P_3 = (u_1 - u_2 - u_3)$. $|V_Q| = 3$.
- **Data Graph $G$:** Edge $\{1, 2\}$ with label 0.
- **Mapping $f$:** $f(u_1) = 1, f(u_2) = 2, f(u_3) = 1$. (Vertex $u_3$ collapses onto data vertex 1).
- **Deviation Scores:** Injectivity is violated ($f(u_1) == f(u_3) == 1$).
- **Formula Acceptance:** **HARD REJECT (Cost $\infty$)** across all parameter thresholds.
- **Semantic Judgment:** Invalid. A path cannot collapse into an alternating bounce between two physical nodes.

---

### Case 11: Tree Query Disconnected by Missing Edge
- **Query $Q$:** Path $P_4 = (u_1 - u_2 - u_3 - u_4)$. $E_Q = \{\{u_1, u_2\}, \{u_2, u_3\}, \{u_3, u_4\}\}$.
- **Data Graph $G$:** Edges $\{\{1, 2\}, \{3, 4\}\}$. (Middle edge $\{2, 3\}$ is missing).
- **Mapping $f$:** $f(u_i) = i$. Mapped graph $G[S_f]$ consists of two isolated components: $\{1, 2\}$ and $\{3, 4\}$.
- **Deviation Scores:**
  - $m(f) = 1$ (missing middle edge).
  - Connectivity $\mathcal{C}(f) = \text{false}$ (2 disconnected components).
- **Formula Acceptance:**
  - Unconstrained Model C ($\varepsilon_m = 1, \varepsilon_x = 2$): **ACCEPTED** ($m=1$).
  - Strict Connectedness Model ($\mathcal{C}(f) == \text{true}$): **REJECTED**.
- **Semantic Judgment:** Structurally fragmented. Two disconnected pairwise flows do not form an end-to-end multi-hop communication trace.

---

### Case 12: Dynamic Stream Insertion — Pattern Enters Acceptance ($\text{ENTERED}$)
- **Query $Q$:** Star $K_{1,2}$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}\}$.
- **Initial Graph $G_0$:** Edge $\{0, 1\}$ exists. Edge $\{0, 2\}$ is absent. ($m_0(f) = 1$).
  - For exact matching ($\varepsilon_m = 0$), $f \notin \mathcal{M}_0$.
- **Stream Update $\Delta e_1$:** `+ e 0 2 0` (Insert edge $\{0, 2\}$ with label 0).
- **Post-Update Graph $G_1$:** Edges $\{\{0, 1\}, \{0, 2\}\}$.
  - $m_1(f) = 0, x_{\mathrm{out},1}(f) = 0$.
- **Dynamic Transition:**
  $$f \in \text{ENTERED}_1 = \mathcal{M}_1^{(0, 0)} \setminus \mathcal{M}_0^{(0, 0)}$$
- **Semantic Judgment:** Valid pattern completion. Arrival of edge $\{0, 2\}$ materializes the exact star.

---

### Case 13: Dynamic Stream Insertion — Boundary Clutter Evicts Pattern ($\text{EXITED}$)
- **Query $Q$:** Star $K_{1,2}$. Candidate mapping $f$ has $S_f = \{0, 1, 2\}$.
- **Initial Graph $G_0$:** Edges $\{\{0, 1\}, \{0, 2\}, \{0, 4\}\}$.
  - $m_0(f) = 0$, $x_{\mathrm{out},0}(f) = 1$.
  - Under threshold $\varepsilon_m = 0, \varepsilon_x = 1$, $f \in \mathcal{M}_0$.
- **Stream Update $\Delta e_1$:** `+ e 0 5 0` (External edge connecting center $0$ to new node $5$).
- **Post-Update Graph $G_1$:** $x_{\mathrm{out},1}(f) = 2$.
- **Dynamic Transition:**
  $$x_{\mathrm{out},1}(f) = 2 > \varepsilon_x = 1 \implies f \in \text{EXITED}_1 = \mathcal{M}_0 \setminus \mathcal{M}_1$$
- **Semantic Judgment:** Clutter eviction. Pattern exceeded its allowed boundary noise limit.

---

### Case 14: Dynamic Stream Deletion — Boundary Clutter Removal Restores Pattern ($\text{ENTERED}$)
- **Query $Q$:** Star $K_{1,2}$. Candidate mapping $f$ has $S_f = \{0, 1, 2\}$.
- **Initial Graph $G_0$:** Edges $\{\{0, 1\}, \{0, 2\}, \{0, 4\}, \{0, 5\}\}$.
  - $x_{\mathrm{out},0}(f) = 2$. Under threshold $\varepsilon_x = 1$, $f \notin \mathcal{M}_0$.
- **Stream Update $\Delta e_1$:** `- e 0 5 0` (Delete external clutter edge $\{0, 5\}$).
- **Post-Update Graph $G_1$:** $x_{\mathrm{out},1}(f) = 1$.
- **Dynamic Transition:**
  $$x_{\mathrm{out},1}(f) = 1 \le \varepsilon_x = 1 \implies f \in \text{ENTERED}_1 = \mathcal{M}_1 \setminus \mathcal{M}_0$$
- **Semantic Judgment:** Clean-up restoration. Removal of external noise brings pattern back into tolerance.

---

### Case 15: Dynamic Update Modifies Score Without Membership Change ($\text{SCORE\_CHANGED}$)
- **Query $Q$:** Star $K_{1,2}$. Operating under threshold $\varepsilon_m = 0, \varepsilon_x = 3$.
- **Initial Graph $G_0$:** Edges $\{\{0, 1\}, \{0, 2\}, \{0, 4\}\}$.
  - $m_0(f) = 0, x_{\mathrm{out},0}(f) = 1 \le 3 \implies f \in \mathcal{M}_0$.
- **Stream Update $\Delta e_1$:** `+ e 0 5 0` (External edge connecting center $0$ to node $5$).
- **Post-Update Graph $G_1$:**
  - $m_1(f) = 0, x_{\mathrm{out},1}(f) = 2 \le 3 \implies f \in \mathcal{M}_1$.
- **Dynamic Transition:**
  $$f \in \mathcal{M}_0 \cap \mathcal{M}_1, \quad \text{but } x_{\mathrm{out},1}(f) \ne x_{\mathrm{out},0}(f) \implies f \in \text{SCORE\_CHANGED}_1$$
- **Semantic Judgment:** Internal metric shift. Pattern remains valid, but its rank or distance score in a top-K priority index changes.
