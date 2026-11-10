# APX-04 — Threshold Feasibility & Mathematical Sensitivity Analysis

**Document ID:** `docs/apx04_threshold_feasibility.md`
**Status:** Analytical Feasibility Specification (`A3_00_READY_FOR_INDEPENDENT_REVIEW_ROUND_2`)
**Associated Issue:** [#20](https://github.com/iamqthanq/Seminar_RapidFlow/issues/20)
**Parent Semantics:** APX-01 Model C (`docs/approx_problem_definition_v1.md`, `docs/approx_threshold_protocol_v1.md`)
**Verified Baseline:** APX-03 C++ Baseline (`1eabd3d82e59d29f168cd99f9c5da2bbdc6710f0`)

---

## 1. Executive Summary

This document establishes the mathematical foundation and sensitivity boundaries for the Model C threshold grid $(\varepsilon_m, \varepsilon_x)$ prior to empirical execution on NetFlow.

The central findings established herein are:
1. **Mathematical Hub Elimination:** The external boundary constraint $x_{\text{out}}(f) \le \varepsilon_x$ imposes a strict upper bound on the maximum degree of any vertex that can appear in an accepted mapping: $\deg_G(v) \le \varepsilon_x + k - 1$. On NetFlow (where maximum degree is $49{,}014$), any threshold $\varepsilon_x \le 30$ mathematically disqualifies any vertex with degree $> 35$.
2. **Missing-Edge Combinatorial Dilation:** On graphs with a single vertex label (such as NetFlow's $3.11\text{M}$ vertices all having label 0), vertex label filtering provides zero selectivity. Allowing missing edges ($\varepsilon_m \ge 1$) disables structural edge pruning during early backtracking steps, resulting in potential exponential candidate expansion.
3. **Exact Boundary-Cut Invariance & Internal Chords:** Internal chord updates do not alter $x_{\text{out}}(f)$, but internal chord deletion can alter internal connectivity $\mathcal{C}(f)$, triggering `EXITED` events in `STRICT` mode.
4. **Near-Only Semantics:** A mapping is near-only if and only if $m(f) \ge 1$. Workloads with $\varepsilon_m = 0$ are exact non-induced matches under clutter filtering, never near-only.

---

## 2. Rigorous Proof of the Hub Degree Bound

### 2.1. Theorem & Proof
Let $Q = (V_Q, E_Q)$ be an undirected query with $k = |V_Q|$ vertices. Let $G = (V_G, E_G)$ be a simple, loop-free undirected data graph. For any injective mapping $f: V_Q \to V_G$, let $S_f = f(V_Q) \subset V_G$ denote the mapped vertex set ($|S_f| = k$).

**Theorem 1:** For every vertex $v \in S_f$:
$$x_{\text{out}}(f) \ge \max\big(0, \deg_G(v) - (k - 1)\big)$$

*Proof:*
In a simple graph, $v$ can be adjacent to at most each of the other $k - 1$ vertices in $S_f \setminus \{v\}$. Therefore, the internal degree within the induced subgraph $G[S_f]$ satisfies:
$$\deg_{G[S_f]}(v) = |\{ w \in S_f \setminus \{v\} : \{v, w\} \in E_G \}| \le k - 1$$
The total degree of $v$ in $G$ partitions into internal neighbors inside $S_f$ and external neighbors in $V_G \setminus S_f$:
$$\deg_G(v) = \deg_{G[S_f]}(v) + |\{ w \in V_G \setminus S_f : \{v, w\} \in E_G \}|$$
By definition of the external boundary cut $x_{\text{out}}(f)$:
$$x_{\text{out}}(f) = |\{ \{a, b\} \in E_G : a \in S_f, b \notin S_f \}| \ge |\{ w \in V_G \setminus S_f : \{v, w\} \in E_G \}|$$
Combining these relations:
$$x_{\text{out}}(f) \ge \deg_G(v) - \deg_{G[S_f]}(v) \ge \deg_G(v) - (k - 1)$$
Because $x_{\text{out}}(f)$ is a non-negative integer:
$$x_{\text{out}}(f) \ge \max\big(0, \deg_G(v) - (k - 1)\big)$$
$\blacksquare$

### 2.2. Hub Exclusion Threshold Table
Under Model C, a mapping $f$ is accepted only if $x_{\text{out}}(f) \le \varepsilon_x$. Consequently:
$$\deg_G(v) - (k - 1) \le x_{\text{out}}(f) \le \varepsilon_x \implies \deg_G(v) \le \varepsilon_x + k - 1$$

This yields the following exact degree upper bounds across the operational threshold grid:

| Query Vertices $k$ | $\varepsilon_x = 0$ (Boundary-Free) | $\varepsilon_x = 2$ (Strict Clutter) | $\varepsilon_x = 10$ (Moderate Clutter) | $\varepsilon_x = 30$ (Broad Clutter) | $\varepsilon_x = \text{None}$ (Unbounded) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **$k = 4$** | **$\deg(v) \le 3$** | **$\deg(v) \le 5$** | **$\deg(v) \le 13$** | **$\deg(v) \le 33$** | $\infty$ (No bound) |
| **$k = 5$** | **$\deg(v) \le 4$** | **$\deg(v) \le 6$** | **$\deg(v) \le 14$** | **$\deg(v) \le 34$** | $\infty$ (No bound) |
| **$k = 6$** | **$\deg(v) \le 5$** | **$\deg(v) \le 7$** | **$\deg(v) \le 15$** | **$\deg(v) \le 35$** | $\infty$ (No bound) |

---

## 3. Boundary-Cut Identity & Dynamic Invariants

### 3.1. Degree-Sum Identity Proof
**Theorem 2:** For any subset of vertices $S \subseteq V_G$:
$$x_{\text{out}}(S) = \sum_{v \in S} \deg_G(v) - 2 |E_G[S]|$$

*Proof:*
Summing the vertex degrees over $S$:
$$\sum_{v \in S} \deg_G(v) = \sum_{v \in S} \sum_{w \in V_G} \mathbf{1}_{\{v, w\} \in E_G}$$
Any edge $\{a, b\} \in E_G$ is counted:
- Twice if $a \in S$ and $b \in S$, contributing $2 |E_G[S]|$.
- Once if $a \in S$ and $b \notin S$, contributing $|\partial(S)| = x_{\text{out}}(S)$.
- Zero times if $a \notin S$ and $b \notin S$.
Therefore:
$$\sum_{v \in S} \deg_G(v) = 2 |E_G[S]| + x_{\text{out}}(S) \iff x_{\text{out}}(S) = \sum_{v \in S} \deg_G(v) - 2 |E_G[S]| \quad \blacksquare$$

### 3.2. Microcase Empirical Verification with APX-02 Oracle
To confirm mathematical correctness against ground truth, canonical microcases were evaluated directly against `scripts/apx02_oracle.py`:
1. **Microcase 1 ($P_3$ with 2 External Tails on Center):**
   - Query: $0 - 1 - 2$ ($k=3, |E_Q|=2$).
   - Data: Vertices $0..4$. Edges $\{0, 1\}, \{1, 2\}, \{1, 3\}, \{1, 4\}$.
   - Degrees: $\deg(0)=1, \deg(1)=4, \deg(2)=1 \implies \sum \deg = 6$.
   - Internal edges: $|E_G[S]| = 2$.
   - Formula: $x_{\text{out}} = 6 - 2(2) = 2$.
   - Oracle Output: `ev.score.x_out == 2` (**PASS**). Lower bound $\max(0, 4 - 2) = 2 \le 2$ (**PASS**).
2. **Microcase 2 ($C_4$ in Complete $K_4$ with 2 External Boundary Tails):**
   - Query: 4-cycle ($k=4, |E_Q|=4$).
   - Data: Complete $K_4$ on vertices $0..3$ ($|E_G[S]|=6$) plus external edges $\{0, 4\}$ and $\{2, 5\}$.
   - Degrees: $\deg(0)=4, \deg(1)=3, \deg(2)=4, \deg(3)=3 \implies \sum \deg = 14$.
   - Formula: $x_{\text{out}} = 14 - 2(6) = 2$.
   - Oracle Output: `ev.score.x_out == 2` (**PASS**).
3. **Microcase 3 ($K_{1,3}$ Star with 30 External Peripheral Tails):**
   - Query: $K_{1,3}$ ($k=4, |E_Q|=3$).
   - Data: Center $0$ connected to $1..33$ ($\deg(0)=33$).
   - Degrees: $\deg(0)=33, \deg(1)=\deg(2)=\deg(3)=1 \implies \sum \deg = 36$.
   - Formula: $x_{\text{out}} = 36 - 2(3) = 30$.
   - Oracle Output: `ev.score.x_out == 30` (**PASS**). Lower bound $\max(0, 33 - 3) = 30 \le 30$ (**PASS**).

---

## 4. Semantic Relevance & Scientific Risks

### 4.1. The Hub-Exclusion Dilemma (Hypothesis H1)
In NetFlow network traffic, high-degree topological hub vertices naturally emerge:
- Core routers and gateway nodes
- Centralized broadcast or infrastructure endpoints
- Heavily connected communication relays

Under Model C, the parameter $\varepsilon_x$ was conceptualized to penalize "clutter" and isolate clean communication patterns. However, Theorem 1 and Corollary 1 prove that **any small constant threshold $\varepsilon_x \le 30$ acts as a hard degree filter**, permanently disqualifying all vertices with $\deg_G(v) > \varepsilon_x + k - 1$.
- **Scientific Implication:** If a query motif involves a topological hub node (e.g. $\deg(v) > 35$ for $k=4$), Model C under $\varepsilon_x \le 30$ will return **exactly zero matches**, because the hub vertex is mathematically excluded by the boundary cut definition.
- **Protocol Decision:** To preserve semantic fidelity, the workload protocol MUST include $\varepsilon_x = \text{None}$ (unbounded boundary cut) as an essential control condition to measure the yield of embeddings that contain hub vertices.

### 4.2. Missing-Edge Combinatorial Dilation (Hypothesis H2)
In the NewSP NetFlow distribution:
- $V_G = 3{,}114{,}895$, and **every single vertex has identical label `0`**.
- In exact subgraph matching ($m = 0$), search depth is pruned aggressively whenever a required query edge is missing between candidates.
- When $\varepsilon_m = 1$, a partial mapping can proceed even if an edge is absent. Because all $3.11\text{M}$ vertices match label `0`, early candidate lists cannot be pruned by vertex labels.
- For queries with low edge density (e.g. paths and trees), allowing $\varepsilon_m = 1$ dilates the branching factor, potentially expanding millions of partial search paths before reaching the $k$-th vertex.
- **Protocol Decision:** Workloads must enforce a strict search-step safety budget ($10^7$ steps) and candidate budget ($10^7$ candidates), failing closed with exit code 2 rather than exhausting host RAM.

### 4.3. Monotonicity of Static Sets vs Dynamic Transition Events
- **Static Invariant:** The static match set $M_t(\varepsilon_m, \varepsilon_x)$ is monotonic with respect to threshold expansion:
  $$\varepsilon \le \varepsilon' \implies M_t(\varepsilon) \subseteq M_t(\varepsilon')$$
- **Dynamic Transition Nuance:** The sets of dynamic delta events (`ENTERED`, `EXITED`) are **NOT** necessarily monotonic across threshold changes. For example, at a narrow threshold, an edge insertion may cause a mapping to cross the acceptance line (`ENTERED`). At a wider threshold, that same mapping might already have been accepted prior to the update, resulting in an `UNCHANGED` or `SCORE_CHANGED` classification rather than an `ENTERED` event.

---

## 5. Planned Evaluation Metrics

### 5.1. Sensitivity Metrics for Future Piloting
1. **Match Count Sensitivity $N(\varepsilon_m, \varepsilon_x)$:** Total accepted embeddings across grid points.
2. **Near-Only Yield $\Delta_{\text{near}} = |M(\varepsilon_m, \varepsilon_x) \setminus M(0, \text{None})|$:** Genuine approximate embeddings discovered that have no exact non-induced counterpart ($m(f) \ge 1$).
3. **Hub Exclusion Ratio $R_{\text{hub}}(\varepsilon_x)$:** Fraction of candidates pruned strictly by the degree bound $\deg_G(v) > \varepsilon_x + k - 1$.
4. **Search Expansion Ratio $\rho = \frac{\text{search\_steps}}{\text{accepted\_matches}}$:** Search efficiency and pruning effectiveness.
