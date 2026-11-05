# APX-01: Research Decision Record — Gate A0 Architectural Recommendations

- **Document Version:** `1.0.0-PROPOSAL`
- **Gate:** Gate A0 (APX-01 — Semantics & Threshold Selection Protocol)
- **Status:** DRAFT WORKING SPECIFICATION / FOR PEER & ADVISOR REVIEW
- **Parent Issue:** [#12 — [APX-01] Gate A0: Approximate CSM Semantics & Threshold Selection Protocol](https://github.com/iamqthanq/Seminar_RapidFlow/issues/12)
- **Repository Target:** `iamqthanq/Seminar_RapidFlow` (Integration base: `QuangThang`)
- **Author:** Senior Graph Algorithms Researcher & Research Software Engineer

---

## 1. Summary of Architectural Decisions

This Decision Record synthesizes the analytical findings from `docs/approx_problem_definition_v1.md`, `docs/approx_deviation_candidates_v1.md`, `docs/approx_threshold_protocol_v1.md`, `docs/approx_semantic_microcases_v1.md`, and `docs/approx_prior_art_matrix_v1.md`.

It establishes the formal recommendations for Gate A0 and defines the boundaries for the subsequent implementation gate (APX-02).

---

## 2. Recommended Primary Semantic Model

### The Decoupled Two-Threshold Model $\mathcal{M}_t^{(\varepsilon_m, \varepsilon_x)}$

$$\mathbf{RECOMMENDED\ MODEL:\ M_t^{(\varepsilon_m, \varepsilon_x)} \quad \text{with} \quad m_t(f) \le \varepsilon_m \;\land\; x_{\mathrm{out},t}(f) \le \varepsilon_x}$$

### Rationale for Selection:
1. **Directly Reflects Advisor Intent:**
   - Parameter $\varepsilon_x$ penalizes extraneous boundary connections ($x_{\mathrm{out}}$), directly capturing the advisor's example of a star motif with unwanted peripheral tails.
   - Parameter $\varepsilon_m$ permits genuinely inexact matches ($m > 0$), allowing pattern discovery even when communications or edge updates are dropped.
2. **Decoupled Physical Units:**
   - Avoids artificial linear weighting ($\alpha m + \beta x$), which has no natural physical interpretation in computer networks.
   - Allows independent empirical tuning along both structural axes.
3. **Strict Backward Compatibility:**
   - Setting $\varepsilon_m = 0$ and $\varepsilon_x = \infty$ perfectly reproduces exact non-induced continuous subgraph matching (RapidFlow baseline).
   - Setting $\varepsilon_m = 0$ and $\varepsilon_x = 0$ enforces exact edge presence and strict external boundary-free isolation ($x_{\mathrm{out}} = 0$). Note: extra internal chords $x_{\mathrm{in}} \ge 0$ remain unconstrained under Model C; isolated induced matching additionally requires $x_{\mathrm{in}} = 0$ (e.g. $Q=C_4, G=K_4$ accepts under Model C with $\varepsilon_x=0$ but rejects under induced matching).

---

## 3. Disqualified & Deferred Models

| Model | Evaluation Verdict | Concrete Technical Rationale for Rejection / Deferral |
| :--- | :---: | :--- |
| **Model $X_{\mathrm{in}}$ (Internal Extra Edges Only)** | **DISQUALIFIED** | Fails to penalize external peripheral tails. Completely blind to any extraneous connections outside the mapped vertex set $S_f$. |
| **Generalized Graph Edit Distance (GED)** | **DEFERRED** | Highly non-trivial parameter tuning (costs for vertex insertion, deletion, edge substitution). Intractable for high-throughput continuous stream processing. |
| **Bounded Simulation (Fan et al.)** | **DISQUALIFIED** | Does not enforce injective vertex mappings; collapses structural congruence into relational path bounds. |
| **NeMa Fuzzy Neighborhood Distance** | **DISQUALIFIED** | Non-injective; relies on heuristic soft label distances rather than discrete topological bounds. |
| **Arbitrary Linear Sum ($\alpha m + \beta x$)** | **DISQUALIFIED** | Conflates heterogeneous structural phenomena; requires tuning arbitrary scalar weights without physical justification. |

---

## 4. Unresolved Questions for Academic Advisor

The following architectural questions remain open and will be presented to the advisor during the next milestone review:

1. **Hub vs. Peripheral Clutter:**
   *Question:* In NetFlow, network hubs (e.g. DNS servers, gateway routers) naturally possess tens of thousands of external incident edges ($x_{\mathrm{out}} \approx 30{,}000$).
   Does the advisor intend for the boundary penalty $x_{\mathrm{out}} \le \varepsilon_x$ to disqualify all hub-associated embeddings, or should boundary penalties only apply to non-hub vertices?
2. **Tree Disconnection Acceptance:**
   *Question:* When $\varepsilon_m \ge 1$ is applied to a tree query, the mapped data subgraph $G[S_f]$ becomes disconnected into isolated components.
   Should Approximate CSM strictly enforce the connectivity constraint $\mathcal{C}(f) == \text{true}$?
3. **Output Granularity on Dense Streams:**
   *Question:* When $\varepsilon_m \ge 1$, the number of approximate matches can increase by orders of magnitude.
   Does the advisor require complete embedding enumeration (`FULL_ENUM`), or is a bounded witness contract (`FIRST_K` or `TOP_K`) acceptable for online stream processing?
4. **TOP-K Tie-Breaking / Scalarization Policy:**
   *Question:* In the two-threshold vector score $(m, x_{\mathrm{out}})$, candidate points form a partial order with Pareto incomparable points (e.g. $(1, 0)$ vs $(0, 2)$).
   Should TOP-K ranking adopt lexicographic priority ($\langle m, x_{\mathrm{out}}\rangle$ vs $\langle x_{\mathrm{out}}, m\rangle$) or a weighted linear scalarization ($w_m m + w_x x_{\mathrm{out}}$)?

---

## 5. Risk Register & Failure Mode Analysis

| Risk ID | Failure Scenario | Impact | Mitigation Strategy |
| :--- | :--- | :---: | :--- |
| **RISK-01** | **Hub Result Collapse:** Setting small $\varepsilon_x$ rejects all embeddings touching NetFlow hubs, returning empty match sets. | High | Profile $x_{\mathrm{out}}$ CDF during Gate A1 calibration; evaluate normalized metric $x_{\mathrm{norm}}$ if necessary. |
| **RISK-02** | **Combinatorial Explosion for $m \ge 1$:** Relaxing edge requirements on trees triggers millions of candidate branches in DFS. | High | Impose strict search visit budgets ($N_{\text{visited}} \le 10^7$) and enforce $\mathcal{C}(f) == \text{true}$. |
| **RISK-03** | **Non-Local Update Overhead:** Invalidation of $x_{\mathrm{out}}$ triggered by external edge updates outside the mapped query. | Medium | Investigate boundary vertex index structures in APX-03; isolate update impacts. |
| **RISK-04** | **Circular Evaluation Trap:** Selecting $\varepsilon = 2$ and claiming high precision without independent semantic ground truth. | Critical | Strict separation of Correctness Truth (oracle) from Relevance Truth (held-out perturbation). |
| **RISK-05** | **Incremental Dual Matching Correctness:** RapidFlow relies on symmetric decomposition for exact CSM; under $m > 0$ or boundary constraints, its correctness is unproven. | High | Mandate formal proof or fallback to direct single-direction search before enabling Dual Matching in APX-03. |
| **RISK-06** | **NetFlow Multigraph Deduplication:** NetFlow data naturally contains duplicate flows between IP pairs, but RapidFlow's CSR loader in `graph.cpp` deduplicates to a simple graph. | Low | Explicitly document that CSM algorithms operate on the simple graph projection of NetFlow. |

---

## 6. Gate A0 Recommendation & Verdict

$$\mathbf{GATE\ A0\ VERDICT:\ GO\ (DOCUMENTATION\ READY\ FOR\ INDEPENDENT\ REVIEW)}$$

All required theoretical foundations, formal problem definitions, deviation models, candidate grids, and reference microcases for APX-01 are complete and mathematically reconciled.

---

## 7. Preconditions for Transition to APX-02 (Independent Oracle)

Transition to APX-02 (Implementation of Independent Approximate Oracle) requires:
1. User and independent ChatGPT review of all APX-01 documentation artifacts;
2. Formal acceptance of Gate A0;
3. Explicit user authorization to begin APX-02 implementation in a dedicated feature branch.
