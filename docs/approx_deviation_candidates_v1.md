# APX-01: Structural Deviation Models — Candidate Analysis & Evaluation

- **Document Version:** `1.0.0-PROPOSAL`
- **Gate:** Gate A0 (APX-01 — Semantics & Threshold Selection Protocol)
- **Status:** DRAFT WORKING SPECIFICATION / FOR PEER & ADVISOR REVIEW
- **Parent Issue:** [#12 — [APX-01] Gate A0: Approximate CSM Semantics & Threshold Selection Protocol](https://github.com/iamqthanq/Seminar_RapidFlow/issues/12)
- **Repository Target:** `iamqthanq/Seminar_RapidFlow` (Integration base: `QuangThang`)
- **Author:** Senior Graph Algorithms Researcher & Research Software Engineer

---

## 1. Scope & Analytical Objectives

This document provides a comparative mathematical, topological, and algorithmic analysis of candidate structural deviation models for **Approximate Continuous Subgraph Matching (Approximate CSM)**.

The central challenge is to translate the advisor's informal concepts ("patterns with extra tails", "patterns with slightly altered hub branches", "patterns close to query $Q$") into rigorous, computable objective functions that:
1. Avoid collapsing into a trivial subset of exact non-induced matchings;
2. Remain computationally feasible on dynamic graph streams;
3. Behave predictably across diverse query topologies (stars, trees, cycles) and vertex degree distributions (especially NetFlow hubs).

---

## 2. Comprehensive Catalog of Candidate Deviation Functions

### 2.1. Model M: Missing-Edge Deviation ($m_t(f)$)

#### Mathematical Formula
Let $Q = (V_Q, E_Q, \ell_Q)$ and $G_t = (V_t, E_t, \ell_t)$. For an injective mapping $f: V_Q \to V_t$:

$$m_t(f) = \sum_{\{u, v\} \in E_Q} \mathbf{1}\left[\{f(u), f(v)\} \notin E_t \;\lor\; \ell_t(\{f(u), f(v)\}) \ne \ell_Q(\{u, v\})\right]$$

- **Physical Units:** Integer count of missing or label-mismatched query edges.
- **Cost Domain:** $m_t(f) \in \{0, 1, 2, \dots, |E_Q|\}$.
- **Edge Label Convention:** A present edge with a mismatched edge label is counted as missing ($\mathbf{1}[\dots] = 1$).
- **Acceptance Rule:** $m_t(f) \le \varepsilon_m$.

#### Structural & Algorithmic Analysis
- **Exact Recovery:** For $\varepsilon_m = 0$, Model M requires all query edges to exist, matching exact non-induced subgraph isomorphism.
- **True Inexact Matching:** For $\varepsilon_m \ge 1$, Model M generates genuinely inexact embeddings that are **not** present in the exact matching set ($\mathcal{M}_t^{\varepsilon_m} \not\subseteq \mathcal{M}_t^{\text{exact}}$).
- **Disconnectivity Hazard:** If $Q$ is a tree ($|E_Q| = |V_Q| - 1$), the removal of any edge increases the number of connected components from 1 to 2. Without a secondary connectedness constraint $\mathcal{C}(f)$, setting $\varepsilon_m \ge 1$ causes the search space to explode with arbitrary pairs of disconnected components.
- **Prior-Art Grounding:**
  - *SAPPER (Zhang et al., PVLDB 2010):* Directly utilizes missing edge count $m(f) \le \varepsilon$ for approximate subgraph indexing on large static graphs. SAPPER demonstrates that missing-edge indexing is mathematically coherent, but SAPPER does not penalize extraneous edges.
- **RapidFlow Integration Feasibility:**
  - RapidFlow's `Neighborhood Label Frequency (NLF)` and `GlobalViewManager` assume that all query edges incident to $u$ must exist in $G$.
  - When $\varepsilon_m \ge 1$, NLF checks will prematurely prune valid approximate candidates. Adapting RapidFlow to Model M requires relaxing NLF degree bounds by at least $\varepsilon_m$.

---

### 2.2. Model $X_{\mathrm{in}}$: Internal Extra-Edge Deviation ($x_{\mathrm{in},t}(f)$)

#### Mathematical Formula
$$x_{\mathrm{in},t}(f) = \left|\left\{\{u, v\} \subset V_Q \mid \{u, v\} \notin E_Q \;\land\; \{f(u), f(v)\} \in E_t\right\}\right|$$

- **Physical Units:** Integer count of spurious edges strictly internal to the mapped vertex set $S_f = f(V_Q)$.
- **Cost Domain:** $x_{\mathrm{in},t}(f) \in \left\{0, 1, \dots, \binom{|V_Q|}{2} - |E_Q|\right\}$.
- **Acceptance Rule:** $x_{\mathrm{in},t}(f) \le \varepsilon_{\mathrm{in}}$.

#### Structural & Algorithmic Analysis
- **Relation to Induced Matching:** $x_{\mathrm{in},t}(f) = 0$ is the exact definition of an *induced* subgraph. Setting $x_{\mathrm{in},t}(f) \le \varepsilon_{\mathrm{in}}$ represents a relaxed induced subgraph matching.
- **Blindness to External Clutter:** $x_{\mathrm{in}}$ only inspects the subgraph $G_t[S_f]$. It completely ignores any edges connecting $S_f$ to vertices outside $S_f$.
- **Advisor Intent Disconnect:** If the data graph has a star pattern with 10 extra branches attached to the center vertex connecting to external nodes, $x_{\mathrm{in},t}(f)$ remains exactly $0$. It fails to model the advisor's primary example of extraneous peripheral connections.

---

### 2.3. Model $X_{\mathrm{out}}$: External Extra-Incident Deviation ($x_{\mathrm{out},t}(f)$) [Team-Proposed]

#### Mathematical Formula
$$x_{\mathrm{out},t}(f) = \left|\left\{\{a, b\} \in E_t \mid a \in S_f \;\land\; b \in V_t \setminus S_f\right\}\right|$$

Equivalently, expressed in terms of data vertex degrees:
$$x_{\mathrm{out},t}(f) = \sum_{v \in S_f} \deg_{G_t}(v) - 2 \cdot |E_t[S_f]|$$

- **Physical Units:** Integer count of cut edges incident to the mapped vertex set $S_f$.
- **Cost Domain:** $x_{\mathrm{out},t}(f) \in \mathbb{N}_0$.
- **Attribution & Origin:** This is an original measure proposed by the seminar research team specifically to operationalize the advisor's Week-9 comments. It must **never** be attributed to SAPPER (which ignores extra edges) or NeMa (which uses soft label distance).
- **Acceptance Rule:** $x_{\mathrm{out},t}(f) \le \varepsilon_x$.

#### Structural & Algorithmic Analysis
- **Direct Modeling of Peripheral Tails:** If query $Q$ is a 3-star ($K_{1,2}$, 1 center, 2 leaves) and data graph $G$ contains a 4-star ($K_{1,3}$), mapping $Q$ to the center and 2 leaves leaves 1 leaf unmapped. The edge connecting the center to the unmapped leaf is incident to $S_f$ and outside $S_f$. Thus, $x_{\mathrm{out}} = 1$. This matches the advisor's example.
- **Hub Penalization Vulnerability:**
  - NetFlow contains vertices with degree up to $49{,}014$. Over 414 vertices have $\deg \ge 1{,}000$.
  - If a query vertex maps to a NetFlow hub with $\deg = 20{,}000$, $x_{\mathrm{out},t}(f)$ will be at least $19{,}990$.
  - Under any small integer threshold (e.g. $\varepsilon_x \le 5$), **all embeddings touching NetFlow hubs will be categorically disqualified**.
  - *Scientific Implication:* If the runtime drops dramatically under Model $X_{\mathrm{out}}$, the speedup may simply be an artifact of empty result sets (pruning all hub-associated embeddings), rather than an algorithmic optimization.
- **Dynamic Invalidation Non-Locality:** An edge update anywhere in the graph incident to $S_f$ alters $x_{\mathrm{out}}$, even if the update is completely outside the query pattern.

---

### 2.4. Divergence Counterexample: $X_{\mathrm{in}}$ vs. $X_{\mathrm{out}}$

To prove mathematically that $X_{\mathrm{in}}$ and $X_{\mathrm{out}}$ are orthogonal and non-equivalent, consider the following concrete scenario:

```mermaid
flowchart LR
    subgraph Query["Query Q (Path P_3)"]
        QA((u1)) --- QB((u2))
        QB --- QC((u3))
    end
    
    subgraph Graph1["Data Graph G_A"]
        A1((1)) --- A2((2))
        A2 --- A3((3))
        A1 --- A3
    end
    
    subgraph Graph2["Data Graph G_B"]
        B1((1)) --- B2((2))
        B2 --- B3((3))
        B2 --- B4((4))
        B2 --- B5((5))
    end
```

Let query $Q$ be a 3-vertex path $P_3 = (u_1 - u_2 - u_3)$ with $|E_Q| = 2$.
Let candidate mapping be $f(u_1)=1, f(u_2)=2, f(u_3)=3$. Thus $S_f = \{1, 2, 3\}$.

1. **Scenario A (Graph $G_A$):**
   - $E(G_A) = \{\{1, 2\}, \{2, 3\}, \{1, 3\}\}$ (Triangle on $\{1, 2, 3\}$, no external vertices).
   - $x_{\mathrm{in}}(f) = |\{\{1, 3\}\}| = \mathbf{1}$.
   - $x_{\mathrm{out}}(f) = \mathbf{0}$ (no edges leave $\{1, 2, 3\}$).
   - **Result:** Accepted under $X_{\mathrm{out}} \le 0$, but REJECTED under $X_{\mathrm{in}} \le 0$.
2. **Scenario B (Graph $G_B$):**
   - $E(G_B) = \{\{1, 2\}, \{2, 3\}, \{2, 4\}, \{2, 5\}\}$ ($P_3$ on $\{1, 2, 3\}$, with vertices 4 and 5 connected to vertex 2).
   - $x_{\mathrm{in}}(f) = \mathbf{0}$ (no chordal edges within $\{1, 2, 3\}$).
   - $x_{\mathrm{out}}(f) = |\{\{2, 4\}, \{2, 5\}\}| = \mathbf{2}$.
   - **Result:** Accepted under $X_{\mathrm{in}} \le 0$, but REJECTED under $X_{\mathrm{out}} \le 0$.

This counterexample formally establishes that $X_{\mathrm{in}}$ and $X_{\mathrm{out}}$ evaluate disjoint structural phenomena.

---

### 2.5. Model $X_{\mathrm{norm}}$: Normalized Structural Deviation

To account for query size and avoid unbounded integer growth:

$$x_{\mathrm{norm},t}(f) = \frac{x_{\mathrm{out},t}(f)}{2|E_Q| + x_{\mathrm{out},t}(f)}$$

- **Properties:** Continuous range $[0, 1)$.
  - When $x_{\mathrm{out}} = 0 \implies x_{\mathrm{norm}} = 0$.
  - When $x_{\mathrm{out}} \to \infty \implies x_{\mathrm{norm}} \to 1$.
- **Limitations:** Continuous thresholds complicate branch-and-bound pruning in integer graph engines. In addition, $x_{\mathrm{norm}}$ does not satisfy metric triangle inequality across graph edit distances.

---

### 2.6. Model C: Decoupled Two-Threshold Model ($M_t^{(\varepsilon_m, \varepsilon_x)}$)

Instead of combining missing edges and external clutter via arbitrary linear coefficients ($\alpha m + \beta x$), Model C enforces independent constraints:

$$f \in \mathcal{M}_t^{(\varepsilon_m, \varepsilon_x)} \iff \begin{cases}
m_t(f) \le \varepsilon_m \\
x_{\mathrm{out},t}(f) \le \varepsilon_x \\
\mathcal{C}(f) == \text{true} \quad (\text{if strict connectivity is enabled})
\end{cases}$$

- **Advantage:** Maintains clean physical interpretations (e.g. "at most 1 missing query edge and at most 2 extraneous boundary connections").
- **Analysis Grid:** Enables a 2D parameter frontier $(\varepsilon_m, \varepsilon_x)$ to study algorithmic sensitivity and output volume independently.

---

## 3. Prior-Art Grounding & Conceptual Differentiation

| Model Component | Formal Name | Primary Literature Antecedents | Key Conceptual Differences |
| :--- | :--- | :--- | :--- |
| **$m(f)$** | Missing-Edge Deviation | **SAPPER** (Zhang et al., PVLDB 2010) | SAPPER handles static graph indexing with missing edges; it does NOT penalize external incident clutter ($x_{\mathrm{out}}$). |
| **$x_{\mathrm{in}}(f)$** | Internal Extra Edges | **Relaxed Induced Matching** | Classic graph theory relaxation; blind to external neighborhood connectivity. |
| **$x_{\mathrm{out}}(f)$** | External Boundary Clutter | **Team Proposal** (Inspired by Week-9 Advisor feedback) | NOT a standard literature formula; uniquely operationalizes peripheral tail noise. |
| **$x_{\mathrm{norm}}(f)$** | Normalized Cut Deviation | **Graph Cut / Conductance** | Re-scales boundary edges relative to query edge volume; exploratory. |
| **Bounded Distance** | Simulation / Paths | **Fan et al.** (SIGMOD 2011, TODS 2013) | Matches query edges to bounded-length paths; relation-based, non-injective. |
| **Soft Distance** | Neighborhood Similarity | **NeMa** (Khan et al., PVLDB 2013) | Top-K fuzzy matching using label and neighbor distance; non-injective; static. |

---

## 4. Assessment of RapidFlow Algorithmic Compatibility

| RapidFlow Core Component | Exact Assumption in Upstream Baseline | Impact of Model M ($m \le \varepsilon_m$) | Impact of Model $X_{\mathrm{out}}$ ($x_{\mathrm{out}} \le \varepsilon_x$) |
| :--- | :--- | :--- | :--- |
| **Neighborhood Label Frequency (`NLF`)** | Every data candidate $v$ must have $\ge \deg_Q(u, l)$ neighbors with label $l$. | **INVALIDATED:** If $m \ge 1$, data vertex can have fewer neighbors than query vertex. Must relax NLF filter by $\varepsilon_m$. | **COMPATIBLE:** Extra external edges only increase neighbor count, satisfying exact NLF lower bound. |
| **`GlobalViewManager` (`GVM`)** | Inverted trie storing only exact edges matching query edge labels. | **REQUIRES EXPANSION:** Mappings can bridge non-existent edges. | **COMPATIBLE:** Exact edge relations are preserved; external edges are counted separately. |
| **`LocalViewManager` (`LVM`)** | Candidate intersection across backward neighbors along matching order. | **BREAKS STRICT INTERSECTION:** When an edge is missing, candidate set must not be intersected with the missing neighbor. | **COMPATIBLE:** Intersections remain valid; degree bounds must be evaluated. |
| **Search Engine (`search_engine.cpp`)** | Recursive backtracking with leaf shortcut: `result_count += candidates.size()`. | **LEAF SHORTCUT INVALIDATED:** Inexact mappings require verifying overall $m(f)$ budget. | **LEAF SHORTCUT INVALIDATED:** External degree must be accumulated across all mapped vertices before accepting. |

> [!IMPORTANT]
> **Key Finding for Gate A0:**
> If we adopt **Model $X_{\mathrm{out}}$ with $\varepsilon_m = 0$**, RapidFlow's exact candidate generation and NLF filtering remain largely reusable, because all query edges are still required to exist. The primary modification occurs at the search leaf level, where embeddings must be validated against the clutter budget $x_{\mathrm{out}} \le \varepsilon_x$.
> Conversely, if we adopt **Model M with $\varepsilon_m \ge 1$**, RapidFlow's core pruning structures (NLF and local view intersections) are fundamentally compromised and must be redesigned from the ground up.
