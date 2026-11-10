# APX-04 — Gate A3 Decision Framework & Prior-Art Synthesis

**Document ID:** `docs/apx04_gate_a3_decision.md`  
**Current Gate Status:** `A3_00_READY_FOR_INDEPENDENT_REVIEW`  
**Associated Issue:** [#20](https://github.com/iamqthanq/Seminar_RapidFlow/issues/20)  
**Parent Roadmap:** [#1](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)  
**Deliverable State:** Preflight & Feasibility Audit Complete (No Optimizer Implemented)

---

## 1. Prior-Art Matrix & Comparative Survey

To contextualize Approximate Continuous Subgraph Matching (Model C), the table below surveys existing literature across exact CSM, static approximate subgraph matching, and incremental graph processing:

| Paper & Venue | Core Problem Solved | Semantics & Deviation Model | Injective Mapping? | Dynamic Streaming? | Output Contract | Key Similarities to Model C | Key Differences from Model C | Verification Status |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| **RapidFlow**<br>(PVLDB 2022) | Exact Continuous Subgraph Matching | Exact ($m=0, x_{\text{in}}=0, x_{\text{out}}=\text{None}$) | Yes | Yes (edge insertions & deletions) | Count-only (`EXECUTION_MODE=0`) or partial mappings | Dynamic streaming CSM architecture; native CSR graph representation reused in APX-03 | RapidFlow uses NLF and degree pruning that unsoundly eliminates approximate matches; uses automorphism quotienting to output counts rather than ordered concrete mappings. | `VERIFIED` (Audited in APX-03) |
| **NewSP**<br>(ICDE 2024) | Exact Continuous Subgraph Matching with Non-backtracking Edge Joins | Exact ($m=0, x_{\text{in}}=0, x_{\text{out}}=\text{None}$) | Yes | Yes (multi-label NetFlow streams) | Match counts | Evaluated on the same NetFlow dataset release; uses path-based incremental joins | Focuses strictly on exact matching; candidate lists rely on edge existence; no boundary cut or missing edge support. | `VERIFIED` (Audited in RF-03) |
| **SJ-Tree / TurboFlux**<br>(SIGMOD 2014, TKDE 2018) | Exact Subgraph Matching on Streaming Graphs | Exact ($m=0, x_{\text{in}}=0$) | Yes | Yes (edge streams) | Enumeration or count | Streaming edge-triggered candidate propagation | Exact structural isomorphism only; lacks tolerance for missing edges or external clutter bounds. | `KNOWN` (Literature audited) |
| **Graph Edit Distance (GED)**<br>(Classic survey, TKDE 2016) | Static Approximate Graph Matching | Vertex/edge insertion, deletion, relabeling cost metric | Yes | No (static graphs) | Optimal mapping with minimum cost | Both penalize missing/mismatched edges ($m(f)$ aligns with edge deletion cost) | GED is NP-hard for pairs of graphs; static only; does not formalize external boundary cuts ($x_{\text{out}}$) relative to background streaming graphs. | `KNOWN` (Formal alignment in APX-01) |
| **TugGraph / Approximate Subgraph Extraction**<br>(VLDB 2012) | Subgraph extraction around seed nodes | Degree/edge cut thresholding around seeds | No (subgraph extraction) | No (static) | Vertex cluster | Both utilize boundary edge cuts to separate motif from background clutter | Focuses on clustering rather than injective pattern matching with label compatibility. | `KNOWN` (Literature audited) |
| **IncOpm / Incremental Motif Mining**<br>(TKDE 2019) | Mining frequent subgraphs in dynamic networks | Exact or statistical frequency | Injective or homomorphism | Yes (streaming) | Frequent pattern counts | Tracks motif emergence over dynamic edge updates | Solves pattern discovery (mining) rather than query-driven continuous matching under thresholded error bounds. | `KNOWN` (Literature audited) |

> [!NOTE]
> All prior-art entries are classified strictly as `VERIFIED` (directly audited in code/artifacts) or `KNOWN` (published academic literature). No claims of universal novelty are made at Gate A3-00.

---

## 2. Research Hypotheses & Status at Gate A3-00

| Hypothesis ID | Statement | Mathematical / Theoretical Status | Empirical Status on NetFlow | Action / Gate Path |
| :--- | :--- | :--- | :--- | :--- |
| **H1: Hub Exclusion** | Small $\varepsilon_x \le 30$ mathematically disqualifies vertices with degree $> 35$, eliminating enterprise hub IPs from accepted matches. | **PROVEN** (Theorem 1, Corollary 1: $\deg_G(v) \le \varepsilon_x + k - 1$). | `PENDING_PILOT` (A3-01 will quantify hub candidate elimination on NetFlow). | Retain $\varepsilon_x = \text{None}$ control in all pilot workloads. |
| **H2: Missing-Edge Dilation** | Uniform vertex labels (all label 0 on NetFlow) cause branching factor expansion when $\varepsilon_m \ge 1$. | **THEORETICALLY SOUND** (Zero label selectivity in search tree). | `PENDING_PILOT` (Measure search-step explosion ratio in A3-01). | Enforce mandatory search-step and candidate safety caps. |
| **H3: Recompute Overhead** | Recomputing $M_t$ from scratch when $|\Delta M_t| \ll |M_t|$ spends vast time recalculating unchanged embeddings. | **ARCHITECTURALLY CONFIRMED** (APX-03 `RECOMPUTE_BASELINE` design). | `PENDING_PILOT` (Measure ratio of unchanged to entered/exited in A3-01). | Candidate motivation for future Gate A4 optimization. |
| **H4: Output Dominance** | Emitting concrete JSON tuples dominates execution time when $|M_t| \ge 10^5$. | **INFORMATION-THEORETIC PROOF** (Materializing $O(k |M_t|)$ bytes). | `PENDING_PILOT` (Measure JSON serialization percentage in A3-01). | Distinguish intrinsic output cost from search overhead. |
| **H5: Subgraph Drift** | Evaluating Model C on sampled subgraphs systematically underestimates $x_{\text{out}}$ ($x_{\text{out}}^{G'} \le x_{\text{out}}^G$). | **PROVEN** (Theorem 3). | **VERIFIED ON MICROGRAPHS** (Microcases prove lost external cut edges). | Workloads must separate `FULL_GRAPH` from `INDUCED_SUBGRAPH_RELATIVE`. |
| **H6: Update Locality** | Edge updates $\{a, b\}$ with $\{a, b\} \cap S_f = \emptyset$ leave Model C scores strictly invariant. | **PROVEN** (Theorem 2 corollary). | **VERIFIED ON MICROGRAPHS** (APX-02 differential test step 12). | Theoretical foundation for update-local candidate indexing in Gate A4. |

---

## 3. Decision Framework & Transition Rubric (A3-00 ➔ A3-01)

### 3.1. Prerequisites to Proceed to A3-01 (Micro-Pilot Execution)
The project may transition from A3-00 to A3-01 ONLY IF all of the following conditions are met:
1. **P0 Feasibility Review Passed:** Independent reviewers (ChatGPT and Gemini) confirm the mathematical proofs (Theorem 1, 2, 3), workload design, and preflight tooling.
2. **User Explicit Authorization:** The user explicitly approves proceeding to A3-01. (Issue creation is NOT authorization to run).
3. **Preflight Clean Run:** `scripts/apx04_preflight.py` executes with exit code 0 on the host workstation.
4. **Hardware Floor Satisfied:** Available RAM $\ge 4.0\,\text{GiB}$ and disk $\ge 10\,\text{GiB}$ verified at preflight.

### 3.2. Mandatory STOP Conditions (Gate A3 Exit Rules)
Execution MUST HALT with a scientific verdict of `STOP` or `INCONCLUSIVE` if any of the following occur during A3-01/02:
- **Condition S1 (Systemic OOM / Safety Block):** Available workstation RAM drops below $3.0\,\text{GiB}$ during graph loading, or process hits `RLIMIT_AS` allocation cap.
- **Condition S2 (Universal Timeout on Initial Matching):** $M_0$ static matching exceeds 60s wall clock across all author query families at $\varepsilon_m = 0$.
- **Condition S3 (Trivial Semantic Degeneracy):** For all NetFlow queries, accepted matches are identically 0 under $\varepsilon_x \le 30$ due to hub exclusion, while $\varepsilon_x = \text{None}$ produces $> 10^7$ matches causing immediate budget exhaustion.
- **Condition S4 (Integrity Violation):** Any unverified modification to pinned RapidFlow source, Model C definitions, or ground-truth Oracle.

---

## 4. Current Gate A3-00 Verdict

$$\mathbf{VERDICT:}\quad \text{\textbf{A3\_00\_READY\_FOR\_INDEPENDENT\_REVIEW}}$$

- **Summary of Achievement:** Feasibility protocol, mathematical proofs of hub exclusion and boundary identities, query corpus audit, exploratory threshold grid, and resource safety plan have been fully established.
- **Recommended Next Action:** Antigravity commits and pushes Gate A3-00 deliverables on branch `feature/rf20-apx04-feasibility`, opens a Draft PR linked to Issue #20 targeting `QuangThang`, and awaits independent review by ChatGPT.
