# APX-04 — Gate A3 Feasibility Protocol & Preflight Specification

**Document ID:** `docs/apx04_feasibility_protocol.md`
**Status:** Approved Preflight Specification (`A3_00_READY_FOR_INDEPENDENT_REVIEW_ROUND_2`)
**Parent Roadmap:** [#1 — ROADMAP](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)
**Associated Issue:** [#20 — [APX-04] Gate A3 Feasibility & Workload Audit](https://github.com/iamqthanq/Seminar_RapidFlow/issues/20)
**Worktree / Branch:** `feature/rf20-apx04-feasibility` (forked from `origin/QuangThang` at `46a5640`)
**Reviewed Implementation Baseline:** APX-03 (PR #19 merged at `46a5640`, commit `1eabd3d82e59d29f168cd99f9c5da2bbdc6710f0`)
**Upstream Source Baseline:** `baselines/rapidflow/source/` pinned at `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine read-only)

---

## 1. Overview & Research Objective

Gate A3 (APX-04) serves as the **pre-optimization feasibility investigation and empirical workload audit** for Approximate Continuous Subgraph Matching (CSM) on real-world temporal network data (NetFlow).

Following the mathematical formalization of Model C in **APX-01** (Issues #12, #14), the independent Python ground-truth verification in **APX-02** (Issue #16), and the verified C++ baseline implementation in **APX-03** (Issue #18, PR #19), APX-04 investigates:
1. **Semantic Feasibility (RQ1):** Can Model C produce meaningful, discriminative approximate embeddings on real-world temporal graphs like NetFlow, or does the external boundary constraint ($\varepsilon_x$) systematically eliminate communication hubs, while the missing edge tolerance ($\varepsilon_m$) causes combinatorial search explosion?
2. **Computational Feasibility (RQ2):** Where are the true performance bottlenecks in executing the verified baseline (`RECOMPUTE_BASELINE`) on NetFlow-derived workloads? Does execution time reside in candidate search, graph ingestion, initial match set ($M_0$) materialization, boundary cut evaluation, or output JSON serialization?
3. **Optimization Direction (RQ3):** Is there justifiable empirical evidence that candidate generation or recomputation overhead can be avoided via update-locality or boundary pruning, justifying a dedicated optimization gate (A3-01/A3-02)?

> [!IMPORTANT]
> **Cardinal Research Rule:** Correctness was established at APX-03. Gate A3-00 is strictly a **read-only preflight, mathematical analysis, and experimental planning stage**. No optimizer algorithms are implemented, no large-scale benchmarks are executed, and Model C semantics remain frozen. Negative results are valid scientific findings.

---

## 2. Frozen Scientific Contract (Model C)

The mathematical problem formulation is frozen from APX-01 (`docs/approx_problem_definition_v1.md`) and validated by APX-02/03:

### 2.1. Input Graphs & Embeddings
- **Query Graph $Q = (V_Q, E_Q, L_Q, \Sigma_V, \Sigma_E)$:** Fixed, connected, simple, loop-free undirected graph.
- **Dynamic Data Graph $G_t = (V_t, E_t, L_{G_t})$:** Simple, loop-free undirected graph under a fixed vertex universe ($V_t = V_0$).
- **Ordered Injective Mapping:** An embedding is an injective function $f: V_Q \to V_t$ identified as an ordered tuple $(f(u_0), f(u_1), \dots, f(u_{k-1}))$ where $u_0 < u_1 < \dots < u_{k-1}$ are the query vertices sorted in ascending numerical order.
- **Hard Vertex Label Compatibility:** $\forall u \in V_Q, L_Q(u) = L_{G_t}(f(u))$.

### 2.2. Deviation Metrics
1. **Missing Edge Deviation $m(f)$:**
   $$m(f) = \sum_{\{u, v\} \in E_Q} \mathbf{1}\big[\{f(u), f(v)\} \notin E_t \lor L_{G_t}(\{f(u), f(v)\}) \neq L_Q(\{u, v\})\big]$$
   An edge present in $G_t$ with a mismatched label counts as a missing required edge ($m + 1$).
2. **External Boundary Deviation $x_{\text{out}}(f)$:**
   With vertex image $S_f = f(V_Q) \subseteq V_t$:
   $$x_{\text{out}}(f) = |\{ \{a, b\} \in E_t : a \in S_f, b \notin S_f \}|$$
   Each undirected boundary edge connecting $S_f$ to the background graph is counted exactly once.
3. **Internal Extraneous Chords $x_{\text{in}}(f)$ (Diagnostic Only):**
   $$x_{\text{in}}(f) = |\{ \{a, b\} \in E_t : a, b \in S_f, \{f^{-1}(a), f^{-1}(b)\} \notin E_Q \}|$$
   Under Model C, internal chords are purely diagnostic; they do **NOT** penalize acceptance. Exact non-induced matching permits internal chords ($x_{\text{in}} \ge 0$).
4. **Topological Connectivity $\mathcal{C}(f)$:**
   $\mathcal{C}(f) = \text{true}$ if the induced subgraph $G_t[S_f]$ contains a single connected component; $\text{false}$ otherwise.

### 2.3. Acceptance Criteria
$$Accept(f) \iff Injective(f) \land LabelsOK(f) \land m(f) \le \varepsilon_m \land x_{\text{out}}(f) \le \varepsilon_x \land ConnOK(f)$$
- **`STRICT` Mode:** $ConnOK(f) \iff \mathcal{C}(f) == \text{true}$.
- **`RELAXED_UNCONSTRAINED` Mode:** $ConnOK(f) \iff \text{true}$ (unconstrained; disconnected components permitted).

### 2.4. Dynamic State Delta
For active match set $M_t = \{ f : Accept_{G_t}(f) == \text{true} \}$:
- $\text{ENTERED} = M_t \setminus M_{t-1}$
- $\text{EXITED} = M_{t-1} \setminus M_t$
- $\text{SCORE\_CHANGED} = \{ f \in M_t \cap M_{t-1} : (m_t(f), x_{\text{out},t}(f)) \neq (m_{t-1}(f), x_{\text{out},t-1}(f)) \}$
- $\text{UNCHANGED} = \{ f \in M_t \cap M_{t-1} : (m_t(f), x_{\text{out},t}(f)) == (m_{t-1}(f), x_{\text{out},t-1}(f)) \}$

---

## 3. Mathematical Feasibility Theorems & Bounds

### 3.1. External Boundary Lower Bound Theorem
**Theorem 1 (Vertex-Level Boundary Lower Bound):**
Let $Q$ be a query with $k = |V_Q|$ vertices. For any injective mapping $f: V_Q \to V_G$ with image $S_f = f(V_Q)$ on a simple undirected graph $G$:
$$x_{\text{out}}(f) \ge \max_{v \in S_f} \max\big(0, \deg_G(v) - (k - 1)\big)$$

*Proof:*
Let $v \in S_f$. In a simple graph without multi-edges or self-loops, $v$ can share an edge with at most each of the other $k-1$ vertices in $S_f \setminus \{v\}$. Thus, the induced internal degree satisfies:
$$\deg_{G[S_f]}(v) \le k - 1$$
Every edge incident to $v$ in $G$ connects either to another vertex in $S_f \setminus \{v\}$ or to a background vertex in $V_G \setminus S_f$. Therefore:
$$\deg_G(v) = \deg_{G[S_f]}(v) + |\{ (v, w) \in E_G : w \notin S_f \}|$$
Because every edge connecting $v \in S_f$ to $w \notin S_f$ is an external boundary edge of $S_f$, we have:
$$x_{\text{out}}(f) \ge |\{ (v, w) \in E_G : w \notin S_f \}| = \deg_G(v) - \deg_{G[S_f]}(v) \ge \deg_G(v) - (k - 1)$$
Since $x_{\text{out}}(f) \ge 0$, $x_{\text{out}}(f) \ge \max(0, \deg_G(v) - (k-1))$. Taking the maximum over all $v \in S_f$ establishes the theorem. $\blacksquare$

### 3.2. Universal Hub-Exclusion Corollary
**Corollary 1 (Maximum Permissible Degree for Accepted Mappings):**
Under external boundary threshold $\varepsilon_x < \infty$, any vertex $v \in V_G$ whose degree satisfies:
$$\deg_G(v) > \varepsilon_x + k - 1$$
is mathematically disqualified from appearing in **ANY** accepted Model C embedding $f$.

*Proof:*
Assume for contradiction that $v \in S_f$ for an accepted mapping $f$. Then $x_{\text{out}}(f) \le \varepsilon_x$. By Theorem 1, $\deg_G(v) - (k - 1) \le x_{\text{out}}(f) \le \varepsilon_x \implies \deg_G(v) \le \varepsilon_x + k - 1$, contradicting the assumption. $\blacksquare$

**Degree Cutoff Table Across Operational Grid:**
| Query Size $k$ | $\varepsilon_x = 0$ | $\varepsilon_x = 2$ | $\varepsilon_x = 10$ | $\varepsilon_x = 30$ | $\varepsilon_x = \text{None}$ (Unbounded) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **$k = 4$** | **$\deg(v) \le 3$** | **$\deg(v) \le 5$** | **$\deg(v) \le 13$** | **$\deg(v) \le 33$** | No constraint ($\infty$) |
| **$k = 5$** | **$\deg(v) \le 4$** | **$\deg(v) \le 6$** | **$\deg(v) \le 14$** | **$\deg(v) \le 34$** | No constraint ($\infty$) |
| **$k = 6$** | **$\deg(v) \le 5$** | **$\deg(v) \le 7$** | **$\deg(v) \le 15$** | **$\deg(v) \le 35$** | No constraint ($\infty$) |

### 3.3. Boundary Cut Degree-Sum Identity & Internal Chord Nuance
**Theorem 2 (Exact Boundary Cut Identity):**
For any subset $S \subseteq V_G$ in a simple undirected graph $G$:
$$x_{\text{out}}(S) = \sum_{v \in S} \deg_G(v) - 2 |E_G[S]|$$

*Proof:*
Summing vertex degrees over $S$ counts each internal edge $\{u, w\} \subseteq S$ twice and each cut edge $\{u, z\}$ ($u \in S, z \notin S$) once:
$$\sum_{v \in S} \deg_G(v) = 2 |E_G[S]| + x_{\text{out}}(S) \implies x_{\text{out}}(S) = \sum_{v \in S} \deg_G(v) - 2 |E_G[S]| \quad \blacksquare$$

**Corollary (Chord Invariance on Boundary Cut):**
Adding or deleting an internal chord edge $e = \{u, w\} \subseteq S$ yields:
$$\Delta \left(\sum_{v \in S} \deg_G(v)\right) = 2 \Delta |E_G[S]| \implies \Delta x_{\text{out}}(S) = 0$$

> [!WARNING]
> **Crucial Counterexample (Internal Chord != No Event):**
> While an internal chord update strictly preserves $x_{\text{out}}$, it alters $x_{\text{in}}$ and can alter topological connectivity $\mathcal{C}(f)$.
> *Counterexample:* Consider query path $0-1-2$, data graph with edges $\{0, 1\}$ and chord $\{0, 2\}$, configuration $\varepsilon_m = 1, \varepsilon_x = \text{None}$, `STRICT`. The mapping $(0, 1, 2)$ is initially accepted ($m=1, x_{\text{in}}=1, x_{\text{out}}=0, \text{connected}=\text{True}$).
> When chord $\{0, 2\}$ is deleted, $G_t[S_f]$ loses the connection between $\{0, 1\}$ and vertex $2$. Node $2$ becomes isolated, rendering $G_t[S_f]$ disconnected.
> In `STRICT` mode, the mapping becomes rejected, triggering an **`EXITED`** transition event, even though $x_{\text{out}}$ remained $0$ throughout.

### 3.4. Theoretical Partial Mapping Lower Bounds (GEM-A3-NEW-01 Analysis)
During partial mapping search with matched prefix $P \subset V_Q$ ($|P| < k$):
- Global degree lower bound: $x_{\text{out}}(P) - (k - |P|) \Delta_{\max}$ is mathematically valid if $\Delta_{\max}$ is the global maximum degree, but on heavy-tailed graphs like NetFlow ($\Delta_{\max} = 49{,}014$) this bound is extremely loose.
- Per-vertex bound from Theorem 1: $\max_{v \in P} \max(0, \deg_G(v) - (k - 1))$ is significantly tighter and monotonic.
- Note: Partial mapping bounds are documented for theoretical completeness; **no early pruning is implemented in APX-03** in Gate A3-00.

### 3.5. Monotonicity Analysis: Static Sets vs Dynamic Event Sets (GEM-A3-NEW-02)
- **Static Monotonicity:** For static graphs, expanding thresholds expands accepted matches:
  $$\varepsilon_m \le \varepsilon'_m \land \varepsilon_x \le \varepsilon'_x \implies M_t(\varepsilon_m, \varepsilon_x) \subseteq M_t(\varepsilon'_m, \varepsilon'_x)$$
- **Dynamic Non-Monotonicity:** Dynamic transition event sets (`ENTERED`, `EXITED`) are **NOT** necessarily monotonic with respect to threshold expansion. An update may cause a mapping to transition from rejected to accepted at tight thresholds (`ENTERED`), while at wider thresholds the mapping was already accepted at $t-1$ (resulting in `UNCHANGED` or `SCORE_CHANGED` instead of `ENTERED`).

### 3.6. Subgraph Sampling Semantic Drift
**Theorem 3 (Boundary Cut Monotonicity under Subgraph Sampling):**
Let $G' = G[V']$ be an induced subgraph on $V' \subset V_G$. For any subset $S \subseteq V'$:
$$x_{\text{out}}^{G'}(S) \le x_{\text{out}}^G(S)$$

*Proof:*
Any edge $\{a, b\} \in E_{G'}$ with $a \in S, b \in V' \setminus S$ is also in $E_G$ with $b \in V_G \setminus S$. Thus $\partial_{G'}(S) \subseteq \partial_G(S)$, proving $x_{\text{out}}^{G'}(S) \le x_{\text{out}}^G(S)$. $\blacksquare$

---

## 4. Workload Design & Threshold Grid

### 4.1. Query Families
| Family | Purpose | Query Source | Representative Fixture |
| :--- | :--- | :--- | :--- |
| **Star** | Evaluates hub lower bounds and boundary cut sensitivity | `SYNTHETIC_PROBE` | `probe_star4_l0` ($K_{1,3}, k=4, |E|=3$) |
| **Path** | Evaluates missing edge distortion on linear sequences | `SYNTHETIC_PROBE` | `probe_path4_l0` ($P_4, k=4, |E|=3$) |
| **Tree** | Evaluates branching without cycles | `SYNTHETIC_PROBE` | `probe_tree5_l0` ($k=5, |E|=4$) |
| **Cycle** | Evaluates cyclic topological constraints | `AUTHOR_NEWSP` | `Q01_auth_5v_l1` ($k=5, |E|=6$, chordal cycle) |
| **Dense** | Evaluates dense diamond / chordal structures | `AUTHOR_NEWSP` | `Q03_auth_6v_l2` ($k=6, |E|=8$, dense diamond) |

### 4.2. Exploratory Threshold Grid
$$\varepsilon_m \in \{0, 1, 2\}, \quad \varepsilon_x \in \{0, 2, 10, 30, \text{None}\}$$
- **$\varepsilon_m = 0$:** Exact structural baseline.
- **$\varepsilon_m = 1$:** Single missing required edge.
- **$\varepsilon_m = 2$:** Severe omission (restricted to queries with $|E_Q| \ge 6$ per APX-01 AF3-02 protocol).
- **$\varepsilon_x = \text{None}$:** Unconstrained boundary control (exact non-induced equivalence when $\varepsilon_m = 0$).

### 4.3. Scientific Definition of Near-Only Workloads
$$M_{\text{near-only}}(\varepsilon_m, \varepsilon_x) = M_{\text{approx}}(\varepsilon_m, \varepsilon_x) \setminus M_{\text{exact}}(0, \text{None})$$
Where the exact non-induced comparator uses $\varepsilon_m = 0, \varepsilon_x = \text{None}$ under identical label and universe conditions.
- Any accepted near-only mapping strictly requires:
  $$m(f) \ge 1$$
- A mapping with $m(f) = 0$ and $x_{\text{out}}(f) > 0$ is an **exact non-induced mapping** (even if a tight clutter threshold rejects it). It is **never** a near-only mapping.

---

## 5. Research Hypotheses

- **H1 (Hub Exclusion):** For queries with $k \in \{4, 5, 6\}$, small boundary thresholds ($\varepsilon_x \le 30$) mathematically disqualify vertices with degree $> 35$, eliminating prominent NetFlow topological hubs from participating in accepted embeddings.
- **H2 (Missing-Edge Candidate Explosion):** On graphs with a single vertex label, increasing $\varepsilon_m \ge 1$ eliminates structural pruning, increasing partial candidate expansions exponentially.
- **H3 (Recompute Overhead):** When the delta $|\Delta M_t| \ll |M_t|$, recomputing $M_t$ from scratch incurs substantial avoidable overhead compared to theoretical update-local matching.
- **H4 (Output Cardinality Dominance Hypothesis):** When $|M_t|$ is large, the empirical cost of serializing concrete ordered tuples can dominate total wall-clock runtime. This is an empirical hypothesis to be evaluated through profiling telemetry.
- **H5 (Subgraph Sampling Semantic Drift):** Evaluating $x_{\text{out}}$ on sampled subgraphs causes systematic underestimation of boundary cuts ($x_{\text{out}}^{G'} \le x_{\text{out}}^G$), distorting Model C semantics.
- **H6 (Update Locality Invariant):** An edge update $\{a, b\}$ cannot alter the Model C score of any mapping $f$ whose image $S_f$ does not intersect $\{a, b\}$ ($\{a, b\} \cap S_f = \emptyset$).

---

## 6. Resource Safeguards & Stage Gates

### 6.1. Hardware Floor & Process Limits
- **CPU:** Single process execution (no concurrent benchmarking).
- **Timeout:** Maximum $60.0\,\text{s}$ wall-clock limit per run.
- **Active Memory Budget:** $\le 3.0\,\text{GiB}$ resident budget.
- **Preflight Available RAM Floor:** $\ge 4.0\,\text{GiB}$ free before launching any benchmark.
- **Execution Readiness:** Strictly `SAFETY_BLOCKED` in Gate A3-00.

### 6.2. Four-Stage Gating Workflow
- **A3-00 (Current):** Read-only feasibility audit, mathematical proofs, workload design, preflight tool, safety protocol. **No benchmarks executed.**
- **A3-01 (Requires User Approval):** Micro-pilot execution on 10-step stream prefixes and micrographs under strict resource supervision.
- **A3-02 (Requires User Approval):** Extended pilot on 100-step prefixes if A3-01 proves computational feasibility.
- **A3-03 (Requires User Approval):** Analysis, prior-art synthesis, and final Go/No-Go decision for incremental optimization (Gate A4).

Antigravity stops execution upon completion of Gate A3-00 at status `READY_FOR_CHATGPT_REVIEW_A3_00_ROUND_2`.
