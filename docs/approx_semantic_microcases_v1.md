# APX-01: Micrograph Reference Test Cases — Hand-Reasoned Semantics & Acceptance

- **Document Version:** `1.0.0-PROPOSAL`
- **Gate:** Gate A0 (APX-01 — Semantics & Threshold Selection Protocol)
- **Status:** DRAFT WORKING SPECIFICATION / FOR PEER & ADVISOR REVIEW
- **Parent Issue:** [#12 — [APX-01] Gate A0: Approximate CSM Semantics & Threshold Selection Protocol](https://github.com/iamqthanq/Seminar_RapidFlow/issues/12)
- **Repository Target:** `iamqthanq/Seminar_RapidFlow` (Integration base: `QuangThang`)
- **Author:** Senior Graph Algorithms Researcher & Research Software Engineer

---

## 1. Methodology & Epistemic Separation

This catalog establishes **canonical micrograph test cases** (15 original reference cases plus Case 16) designed for manual verification, automated oracle validation (APX-02), and edge-case benchmarking.

For every case, two strictly distinct conclusions are documented:
1. **Mathematical Formula Acceptance:** Does the candidate mapping $f$ satisfy the objective criteria $m_t(f) \le \varepsilon_m \land x_{\mathrm{out},t}(f) \le \varepsilon_x$? Evaluated under both **Strict Connectivity Mode** ($\mathcal{C}(f) == \text{true}$) and **Relaxed Connectivity Mode** ($\mathcal{C}(f) == \text{false}$).
2. **Semantic Relevance Judgment:** Does a domain specialist or research advisor consider this pattern structurally similar or relevant to query $Q$? Every qualitative judgment is explicitly tagged (`ADVISOR_CONFIRMED`, `TEAM_PROPOSED`, `UNVERIFIED`).

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
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Formula Acceptance:**
  - Strict Connectivity ($\mathcal{C}(f)=\text{true}, \varepsilon_m = 0, \varepsilon_x = 0$): **ACCEPTED**
  - Strict Connectivity ($\mathcal{C}(f)=\text{true}, \varepsilon_m = 1, \varepsilon_x = 2$): **ACCEPTED**
  - Relaxed Connectivity ($\mathcal{C}(f)=\text{false}$): **ACCEPTED**
- **Semantic Judgment:** `[ADVISOR_CONFIRMED]` Perfect structural match. Canonical positive ground truth.

---

### Case 02: Missing One Leaf Edge
- **Query $Q$:** Star $K_{1,3}$. Center $u_0$, leaves $u_1, u_2, u_3$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}, \{u_0, u_3\}\}$, label 0.
- **Data Graph $G$:** $V_G = \{0, 1, 2, 3\}$, $E_G = \{\{0, 1\}, \{0, 2\}\}$. (Edge $\{0, 3\}$ is absent).
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2, f(u_3) = 3$.
- **Deviation Scores:**
  - $m(f) = 1$ (edge $\{u_0, u_3\}$ missing between 0 and 3).
  - $x_{\mathrm{in}}(f) = 0$, $x_{\mathrm{out}}(f) = 0$.
  - Connectivity: $\mathcal{C}(f) = \text{false}$ (2 disconnected components: $\{0, 1, 2\}$ and isolated vertex $\{3\}$).
- **Formula Acceptance:**
  - Exact Non-Induced ($\varepsilon_m = 0, \varepsilon_x = \infty$): **REJECTED** ($m=1 > 0$, not in exact matching set).
  - **Strict Connectivity Mode ($\mathcal{C}(f) == \text{true}$):** **REJECTED** (mapped subgraph is disconnected; vertex 3 is an isolated node).
  - **Relaxed Connectivity Mode ($\mathcal{C}(f) == \text{false}, \varepsilon_m = 1, \varepsilon_x = 2$):** **ACCEPTED** ($m=1 \le 1, x_{\mathrm{out}}=0 \le 2$).
- **Semantic Judgment:** `[TEAM_PROPOSED]` Near-match under relaxed connectivity. Two out of three communication links exist; candidate entity 3 is present but passive.

---

### Case 03: Missing Two Edges (Severe Omission)
- **Query $Q$:** Star $K_{1,3}$. Center $u_0$, leaves $u_1, u_2, u_3$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}, \{u_0, u_3\}\}$.
- **Data Graph $G$:** $V_G = \{0, 1, 2, 3\}$, $E_G = \{\{0, 1\}\}$. (Edges $\{0, 2\}$ and $\{0, 3\}$ are absent).
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2, f(u_3) = 3$.
- **Deviation Scores:**
  - $m(f) = 2$ (two missing edges).
  - $x_{\mathrm{in}}(f) = 0$, $x_{\mathrm{out}}(f) = 0$.
  - Connectivity: $\mathcal{C}(f) = \text{false}$ (3 disconnected components: $\{0, 1\}$, isolated $\{2\}$, and isolated $\{3\}$).
- **Formula Acceptance:**
  - Exact Non-Induced: **REJECTED** ($m=2 > 0$).
  - **Strict Connectivity Mode ($\mathcal{C}(f) == \text{true}$):** **REJECTED** across all thresholds (mapped subgraph has 3 disconnected components).
  - **Relaxed Connectivity Mode ($\mathcal{C}(f) == \text{false}$):**
    - Under $\varepsilon_m = 1, \varepsilon_x = 2$: **REJECTED** ($m=2 > 1$).
    - Under $\varepsilon_m = 2, \varepsilon_x = 2$: **ACCEPTED** ($m=2 \le 2, x_{\mathrm{out}}=0 \le 2$).
- **Semantic Judgment:** `[TEAM_PROPOSED]` Structurally defective. Pattern has lost $66.7\%$ of its required connectivity; only a single edge remains.

---

### Case 04: Extra Internal Chord (Relaxed Induced)
- **Query $Q$:** Cycle $C_4 = (u_1 - u_2 - u_3 - u_4 - u_1)$. $|E_Q| = 4$.
- **Data Graph $G$:** Complete graph $K_4$ on vertices $\{1, 2, 3, 4\}$. $|E_G| = 6$.
- **Mapping $f$:** $f(u_i) = i$ for $i \in \{1, 2, 3, 4\}$.
- **Deviation Scores:**
  - $m(f) = 0$ (all 4 cycle edges exist).
  - $x_{\mathrm{in}}(f) = 2$ (cross-chords $\{1, 3\}$ and $\{2, 4\}$ exist in $G$).
  - $x_{\mathrm{out}}(f) = 0$ (no edges connect outside $\{1, 2, 3, 4\}$).
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Formula Acceptance:**
  - Exact Non-Induced: **ACCEPTED** (non-induced matching ignores internal chords).
  - Exact Induced: **REJECTED** (chords violate induced condition $x_{\mathrm{in}} = 0$).
  - Model C ($\varepsilon_m = 0, \varepsilon_x = 2$): **ACCEPTED** ($m=0 \le 0, x_{\mathrm{out}}=0 \le 2$).
- **Semantic Judgment:** `[TEAM_PROPOSED]` Dense cluster. Contains the required cycle, but has higher internal communication density.

---

### Case 05: Extra External Tail: 1 Extra Branch (Advisor Target Example)
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}\}$.
- **Data Graph $G$:** Star $K_{1,3}$. Center $0$, leaves $1, 2, 4$. $E_G = \{\{0, 1\}, \{0, 2\}, \{0, 4\}\}$.
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$. Image $S_f = \{0, 1, 2\}$.
- **Deviation Scores:**
  - $m(f) = 0$ (query edges $\{0, 1\}$ and $\{0, 2\}$ both present).
  - $x_{\mathrm{in}}(f) = 0$ (no extra edges between $\{0, 1, 2\}$).
  - $x_{\mathrm{out}}(f) = 1$ (edge $\{0, 4\}$ connects mapped center $0 \in S_f$ to unmapped vertex $4 \notin S_f$).
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Formula Acceptance:**
  - Exact Non-Induced: **ACCEPTED** ($m=0$).
  - Boundary-Free Isolation ($\varepsilon_x = 0$): **REJECTED** ($x_{\mathrm{out}} = 1 > 0$).
  - Clutter-Tolerant ($\varepsilon_m = 0, \varepsilon_x = 1$): **ACCEPTED** ($x_{\mathrm{out}} = 1 \le 1$).
- **Semantic Judgment:** `[ADVISOR_CONFIRMED]` Highly relevant. This is the exact primary positive example cited by the advisor: a star motif with one extra background tail.

---

### Case 06: Extra External Tail: 2 Extra Branches
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$.
- **Data Graph $G$:** Star $K_{1,4}$. Center $0$, leaves $1, 2, 4, 5$.
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$. $S_f = \{0, 1, 2\}$.
- **Deviation Scores:**
  - $m(f) = 0$.
  - $x_{\mathrm{in}}(f) = 0$.
  - $x_{\mathrm{out}}(f) = 2$ (edges $\{0, 4\}$ and $\{0, 5\}$ connect to external nodes).
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Formula Acceptance:**
  - $\varepsilon_m = 0, \varepsilon_x = 1$: **REJECTED** ($x_{\mathrm{out}} = 2 > 1$).
  - $\varepsilon_m = 0, \varepsilon_x = 2$: **ACCEPTED** ($x_{\mathrm{out}} = 2 \le 2$).
- **Semantic Judgment:** `[TEAM_PROPOSED]` Borderline. Two extra connections begin to dilate the pattern structure.

---

### Case 07: Extra External Tail: 30 Extra Branches (Noise Clutter)
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$.
- **Data Graph $G$:** Center $0$ connected to $1, 2$ and 30 additional peripheral nodes $4, \dots, 33$. ($\deg(0) = 32$).
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$.
- **Deviation Scores:**
  - $m(f) = 0$.
  - $x_{\mathrm{in}}(f) = 0$.
  - $x_{\mathrm{out}}(f) = 30$ (30 external edges incident to vertex $0$).
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Formula Acceptance:**
  - $\varepsilon_m = 0, \varepsilon_x = 2$: **REJECTED** ($x_{\mathrm{out}} = 30 \gg 2$).
  - $\varepsilon_m = 0, \varepsilon_x = 30$: **ACCEPTED** ($x_{\mathrm{out}} \le 30$).
- **Semantic Judgment:** `[TEAM_PROPOSED]` Irrelevant noise. The target 2-leaf star is submerged in a broad 32-node broadcast tree.

---

### Case 08: High-Degree NetFlow Hub
- **Query $Q$:** Star $K_{1,2}$. Center $u_0$, leaves $u_1, u_2$.
- **Data Graph $G$:** Vertex $0$ is a NetFlow core router interface with $\deg(0) = 1{,}000$, including edges to $1$ and $2$.
- **Mapping $f$:** $f(u_0) = 0, f(u_1) = 1, f(u_2) = 2$.
- **Deviation Scores:**
  - $m(f) = 0$.
  - $x_{\mathrm{out}}(f) = 998$ (998 external edges).
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Formula Acceptance:**
  - Boundary-Tolerant ($\varepsilon_x \le 30$): **REJECTED** ($x_{\mathrm{out}} = 998$).
  - Unbounded Boundary Clutter ($\varepsilon_x = \infty$): **ACCEPTED**.
- **Semantic Judgment:** `[TEAM_PROPOSED]` Contextual conflict. Under exact non-induced semantics, this is a valid embedding. Under external degree penalty, hubs are completely disqualified.

---

### Case 09: Edge Label Mismatch
- **Query $Q$:** Path $P_3 = (u_1 - u_2 - u_3)$. Edge $\{u_1, u_2\}$ has label 1, edge $\{u_2, u_3\}$ has label 1.
- **Data Graph $G$:** Edge $\{1, 2\}$ has label 1, edge $\{2, 3\}$ has label **2**.
- **Mapping $f$:** $f(u_i) = i$.
- **Deviation Scores:**
  - $m(f) = 1$ (edge $\{2, 3\}$ exists in topology but label $2 \ne 1$).
  - $x_{\mathrm{in}}(f) = 0, x_{\mathrm{out}}(f) = 0$. (Internal edge label mismatch never increments $x_{\mathrm{out}}$).
  - Connectivity: $\mathcal{C}(f) = \text{false}$ under label-strict matching (no matching edge between 2 and 3).
- **Formula Acceptance:**
  - Exact matching: **REJECTED**.
  - Strict Connectivity Mode ($\mathcal{C}(f) == \text{true}$): **REJECTED** (under label-strict connectivity).
  - Relaxed Connectivity Mode ($\mathcal{C}(f) == \text{false}, \varepsilon_m = 1, \varepsilon_x = 2$): **ACCEPTED** ($m=1 \le 1, x_{\mathrm{out}}=0 \le 2$).
- **Semantic Judgment:** `[TEAM_PROPOSED - HYPOTHETICAL ILLUSTRATION]` Near-match. Protocol mismatch on one connection (e.g. hypothetical TCP port 80 vs 8080).

---

### Case 10: Injectivity Violation (Hard Reject)
- **Query $Q$:** Path $P_3 = (u_1 - u_2 - u_3)$. $|V_Q| = 3$.
- **Data Graph $G$:** Edge $\{1, 2\}$ with label 0.
- **Mapping $f$:** $f(u_1) = 1, f(u_2) = 2, f(u_3) = 1$. (Vertex $u_3$ collapses onto data vertex 1).
- **Deviation Scores:** Injectivity is violated ($f(u_1) == f(u_3) == 1$).
- **Formula Acceptance:** **HARD REJECT (Cost $\infty$)** across all parameter thresholds.
- **Semantic Judgment:** `[TEAM_PROPOSED]` Invalid. A path cannot collapse into an alternating bounce between two physical nodes.

---

### Case 11: Tree Query Disconnected by Missing Edge
- **Query $Q$:** Path $P_4 = (u_1 - u_2 - u_3 - u_4)$. $E_Q = \{\{u_1, u_2\}, \{u_2, u_3\}, \{u_3, u_4\}\}$.
- **Data Graph $G$:** Edges $\{\{1, 2\}, \{3, 4\}\}$. (Middle edge $\{2, 3\}$ is missing).
- **Mapping $f$:** $f(u_i) = i$. Mapped graph $G[S_f]$ consists of two isolated components: $\{1, 2\}$ and $\{3, 4\}$.
- **Deviation Scores:**
  - $m(f) = 1$ (missing middle edge).
  - Connectivity $\mathcal{C}(f) = \text{false}$ (2 disconnected components).
- **Formula Acceptance:**
  - Unconstrained / Relaxed Mode ($\mathcal{C}(f) == \text{false}, \varepsilon_m = 1, \varepsilon_x = 2$): **ACCEPTED** ($m=1 \le 1, x_{\mathrm{out}}=0 \le 2$).
  - Strict Connectedness Model ($\mathcal{C}(f) == \text{true}$): **REJECTED** (mapped graph is disconnected into 2 fragments).
- **Semantic Judgment:** `[TEAM_PROPOSED]` Structurally fragmented. Two disconnected pairwise flows do not form an end-to-end multi-hop communication trace.

---

### Case 12: Dynamic Stream Insertion — Pattern Enters Acceptance ($\text{ENTERED}$)
- **Query $Q$:** Star $K_{1,2}$. $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}\}$.
- **Initial Graph $G_0$:** Edge $\{0, 1\}$ exists. Edge $\{0, 2\}$ is absent. ($m_0(f) = 1$).
  - For exact matching ($\varepsilon_m = 0$), $f \notin \mathcal{M}_0$.
- **Stream Update $\Delta e_1$:** `+ e 0 2 0` (Insert edge $\{0, 2\}$ with label 0).
- **Post-Update Graph $G_1$:** Edges $\{\{0, 1\}, \{0, 2\}\}$.
  - $m_1(f) = 0, x_{\mathrm{out},1}(f) = 0$.
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (1 component).
- **Dynamic Transition:**
  $$f \in \text{ENTERED}_1 = \mathcal{M}_1^{(0, 0)} \setminus \mathcal{M}_0^{(0, 0)}$$
- **Semantic Judgment:** `[TEAM_PROPOSED]` Valid pattern completion. Arrival of edge $\{0, 2\}$ materializes the exact star.

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
- **Semantic Judgment:** `[TEAM_PROPOSED]` Clutter eviction. Pattern exceeded its allowed boundary noise limit.

---

### Case 14: Dynamic Stream Deletion — Boundary Clutter Removal Restores Pattern ($\text{ENTERED}$)
- **Query $Q$:** Star $K_{1,2}$. Candidate mapping $f$ has $S_f = \{0, 1, 2\}$.
- **Initial Graph $G_0$:** Edges $\{\{0, 1\}, \{0, 2\}, \{0, 4\}, \{0, 5\}\}$.
  - $x_{\mathrm{out},0}(f) = 2$. Under threshold $\varepsilon_x = 1$, $f \notin \mathcal{M}_0$.
- **Stream Update $\Delta e_1$:** `- e 0 5 0` (Delete external clutter edge $\{0, 5\}$).
- **Post-Update Graph $G_1$:** $x_{\mathrm{out},1}(f) = 1$.
- **Dynamic Transition:**
  $$x_{\mathrm{out},1}(f) = 1 \le \varepsilon_x = 1 \implies f \in \text{ENTERED}_1 = \mathcal{M}_1 \setminus \mathcal{M}_0$$
- **Semantic Judgment:** `[TEAM_PROPOSED]` Clean-up restoration. Removal of external noise brings pattern back into tolerance.

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
- **Semantic Judgment:** `[TEAM_PROPOSED]` Internal metric shift. Pattern remains valid, but its rank or distance score in a top-K priority index changes.

---

### Case 16: Connected Near-Only Match ($m > 0, x_{\mathrm{out}} = 0, \mathcal{C}(f) = \text{true}$)
- **Query $Q$:** Star $K_{1,3}$. Center $u_0$, leaves $u_1, u_2, u_3$. $V_Q = \{u_0, u_1, u_2, u_3\}$, $E_Q = \{\{u_0, u_1\}, \{u_0, u_2\}, \{u_0, u_3\}\}$, edge labels all $0$.
- **Data Graph $G$:** $V_G = \{0, 1, 2, 3\}$, $E_G = \{\{0, 1\}, \{0, 2\}, \{2, 3\}\}$, edge labels all $0$.
- **Mapping $f$:** $f(u_i) = i$ for $i \in \{0, 1, 2, 3\}$. Image $S_f = \{0, 1, 2, 3\}$.
- **Deviation Scores:**
  - $m(f) = 1$ (query edge $\{u_0, u_3\}$ is missing between center 0 and leaf 3).
  - $x_{\mathrm{in}}(f) = 1$ (extra chord $\{2, 3\}$ between mapped leaves 2 and 3).
  - $x_{\mathrm{out}}(f) = 0$ (no edges connect outside $S_f$).
  - Connectivity: $\mathcal{C}(f) = \text{true}$ (the mapped edges $\{\{0, 1\}, \{0, 2\}, \{2, 3\}\}$ span all 4 vertices as a connected path $1 - 0 - 2 - 3$; exactly 1 connected component).
- **Formula Acceptance:**
  - Exact Non-Induced ($\varepsilon_m = 0, \varepsilon_x = \infty$): **REJECTED** ($m=1 > 0$, not an exact match).
  - **Strict Connectivity Mode ($\mathcal{C}(f) == \text{true}$):**
    - Under $(\varepsilon_m = 0, \varepsilon_x = 0)$: **REJECTED** ($m=1 > 0$).
    - Under $(\varepsilon_m = 1, \varepsilon_x = 0)$: **ACCEPTED** ($m=1 \le 1, x_{\mathrm{out}}=0 \le 0, \mathcal{C}(f)=\text{true}$).
  - **Relaxed Connectivity Mode ($\mathcal{C}(f) == \text{false}$):**
    - Under $(\varepsilon_m = 1, \varepsilon_x = 0)$: **ACCEPTED** ($m=1 \le 1, x_{\mathrm{out}}=0 \le 0$).
- **Semantic Judgment:** `[TEAM_PROPOSED]` High-relevance connected near-match. Candidate entity 3 is connected via neighbor 2 instead of center 0 (e.g. alternate network routing / path rerouting), preserving global component connectivity while missing the exact star center link. Conclusively proves that near-only matches ($m > 0, x_{\mathrm{out}} = 0$) CAN be strictly connected.
