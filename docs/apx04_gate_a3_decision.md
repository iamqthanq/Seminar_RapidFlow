# APX-04 — Gate A3 Decision Framework & Prior-Art Synthesis

**Document ID:** `docs/apx04_gate_a3_decision.md`
**Current Gate Status:** `A3_00_READY_FOR_INDEPENDENT_REVIEW_ROUND_5`
**Associated Issue:** [#20](https://github.com/iamqthanq/Seminar_RapidFlow/issues/20)
**Parent Roadmap:** [#1](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)
**Deliverable State:** Preflight & Feasibility Audit Complete (No Optimizer Implemented)

---

## 1. Prior-Art Matrix & Comparative Survey

To contextualize Approximate Continuous Subgraph Matching (Model C), prior literature is structured into four distinct categories: Exact Continuous Subgraph Matching (CSM), Static Approximate Subgraph Matching, Frequent Subgraph Mining (FSM), and Dynamic Graph Processing/Extraction:

### 1.1. Exact Continuous Subgraph Matching (CSM)

| Paper & Venue | Bibliographic Reference | Core Problem Solved | Semantics & Deviation Model | Injective Mapping? | Dynamic Streaming? | Output Contract | Relevance to Model C | Differences from Model C | Verification Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| **RapidFlow**<br>(PVLDB 2022) | Shixuan Sun, Xibo Sun, Bingsheng He, Qiong Luo.<br>*RapidFlow: An Efficient Approach to Continuous Subgraph Matching*.<br>PVLDB 15(11): 2415–2427 (2022).<br>DOI: [10.14778/3551793.3551803](https://doi.org/10.14778/3551793.3551803) | Exact Continuous Subgraph Matching | Exact non-induced ($m=0, x_{\text{in}} \ge 0, x_{\text{out}}=\text{None}$) | Yes | Yes (edge insertions & deletions) | Count-only (`EXECUTION_MODE=0`) or partial mappings | Streaming CSM architecture; native CSR graph representation reused in APX-03 baseline | RapidFlow uses NLF and degree pruning that unsoundly eliminates approximate matches; outputs scalar counts rather than concrete ordered mappings; non-induced semantics permit internal chords ($x_{\text{in}} \ge 0$). | `VERIFIED` (Audited in APX-03) |
| **NewSP**<br>(ICDE 2024) | Ziming Li, Youhuan Li, Xinhuan Chen, Lei Zou, Yang Li, Xiaofeng Yang, Hongbo Jiang.<br>*NewSP: A New Search Process for Continuous Subgraph Matching over Dynamic Graphs*.<br>IEEE ICDE 2024, pp. 3324–3337.<br>DOI: [10.1109/ICDE60146.2024.00257](https://doi.org/10.1109/ICDE60146.2024.00257) | Exact Continuous Subgraph Matching with Non-backtracking Edge Joins | Exact non-induced ($m=0, x_{\text{in}} \ge 0, x_{\text{out}}=\text{None}$) | Yes | Yes (multi-label NetFlow streams) | Match counts | Evaluated on the same NetFlow dataset release; uses path-based incremental joins | Focuses strictly on exact matching; candidate lists rely on edge existence; no boundary cut or missing edge support. | `VERIFIED` (Audited in RF-03) |
| **SJ-Tree / TurboFlux**<br>(SIGMOD 2014, TKDE 2018) | Hyoungmin Kim et al.<br>SIGMOD 2014; IEEE TKDE 30(5): 915–928 (2018) | Exact Subgraph Matching on Streaming Graphs | Exact non-induced ($m=0, x_{\text{in}} \ge 0$) | Yes | Yes (edge streams) | Enumeration or count | Streaming edge-triggered candidate propagation | Exact structural isomorphism only; lacks tolerance for missing edges or external clutter bounds. | `KNOWN` (Literature audited) |

### 1.2. Static Approximate Subgraph Matching

| Paper & Venue | Bibliographic Reference | Core Problem Solved | Semantics & Deviation Model | Injective Mapping? | Dynamic Streaming? | Output Contract | Relevance to Model C | Differences from Model C | Verification Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| **Graph Edit Distance (GED)**<br>(Classic survey, TKDE 2016) | Xinbo Gao et al., IEEE TKDE 22(9): 1209–1223; Z. Abu-Aisheh et al. (2016) | Static Approximate Graph Matching | Vertex/edge insertion, deletion, relabeling cost metric | Yes | No (static graphs) | Optimal mapping with minimum cost | Both penalize missing/mismatched edges ($m(f)$ aligns with edge deletion cost) | GED is NP-hard for pairs of graphs; static only; does not formalize external boundary cuts ($x_{\text{out}}$) relative to background streaming graphs. | `KNOWN` (Formal alignment in APX-01) |

### 1.3. Incremental Frequent Subgraph Mining (FSM)

| Paper & Venue | Bibliographic Reference | Core Problem Solved | Semantics & Deviation Model | Injective Mapping? | Dynamic Streaming? | Output Contract | Relevance to Model C | Differences from Model C | Verification Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| **IncGM+**<br>(TKDE 2017) | Ehab Abdelhamid, Mustafa Canim, Mohammad Sadoghi, Bishwaranjan Bhattacharjee, Yuan-Chi Chang, Panos Kalnis.<br>*Incremental Frequent Subgraph Mining on Large Evolving Graphs*.<br>IEEE TKDE 29(12): 2710–2723 (2017).<br>DOI: [10.1109/TKDE.2017.2743075](https://doi.org/10.1109/TKDE.2017.2743075) | Incremental Frequent Subgraph Mining (FSM) | Frequent subgraph pattern mining over dynamic graphs | Injective subgraph isomorphism | Yes (dynamic graphs) | Frequent subgraph support counts & patterns | Evaluates dynamic updates over streaming graph modifications using pattern verification trees | Solves frequent pattern discovery (mining) rather than continuous query matching; does not support $(\varepsilon_m, \varepsilon_x)$ bi-criteria error models. Categorized strictly as Incremental Frequent Subgraph Mining (FSM), NOT exact CSM. | `KNOWN` (Literature audited) |

### 1.4. Dynamic Graph Processing & Extraction / Visualization

| Paper & Venue | Bibliographic Reference | Core Problem Solved | Semantics & Deviation Model | Injective Mapping? | Dynamic Streaming? | Output Contract | Relevance to Model C | Differences from Model C | Verification Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| **TugGraph**<br>(IEEE TVCG 2011) | Daniel Archambault, Tamara Munzner, David Auber.<br>IEEE TVCG 17(3): 276–289 (2011) | Path-preserving hierarchies and subgraph extraction around seed nodes | Degree and boundary edge cut thresholding around seeds | No (subgraph extraction/clustering) | No (static) | Vertex cluster / hierarchy | Both utilize external boundary edge cuts to isolate motif from background clutter | Focuses on exploratory visualization and clustering rather than injective pattern matching with label compatibility. | `KNOWN` (Literature audited) |

> [!NOTE]
> All prior-art entries are classified strictly as `VERIFIED` (directly audited in code/artifacts), `KNOWN` (published academic literature with verified citations), or `UNVERIFIED`. No claims of universal novelty are made at Gate A3-00.

---

## 2. Research Hypotheses & Status at Gate A3-00

| Hypothesis ID | Statement | Mathematical / Theoretical Status | Empirical Status on NetFlow | Action / Gate Path |
| :--- | :--- | :--- | :--- | :--- |
| **H1: Hub Exclusion** | Small $\varepsilon_x \le 30$ mathematically disqualifies vertices with degree $> 35$, eliminating topological network hubs from accepted matches. | **PROVEN** (Theorem 1, Corollary 1: $\deg_G(v) \le \varepsilon_x + k - 1$). | `PENDING_PILOT` (A3-01 will quantify hub candidate elimination on NetFlow). | Retain $\varepsilon_x = \text{None}$ control in all pilot workloads. |
| **H2: Missing-Edge Dilation** | Uniform vertex labels (all label 0 on NetFlow) cause branching factor expansion when $\varepsilon_m \ge 1$. | **THEORETICALLY SOUND** (Zero label selectivity in search tree). | `PENDING_PILOT` (Measure search-step explosion ratio in A3-01). | Enforce mandatory search-step and candidate safety caps. |
| **H3: Recompute Overhead** | Recomputing $M_t$ from scratch when $|\Delta M_t| \ll |M_t|$ spends vast time recalculating unchanged embeddings. | **ARCHITECTURALLY CONFIRMED** (APX-03 `RECOMPUTE_BASELINE` design). | `PENDING_PILOT` (Measure ratio of unchanged to entered/exited in A3-01). | Candidate motivation for future Gate A4 optimization. |
| **H4: Output Dominance** | Emitting concrete JSON tuples can dominate execution time when $|M_t| \ge 10^5$. | **EMPIRICAL HYPOTHESIS** (Output lower bound $\Omega(k |M_t|)$ requires serialization). | `PENDING_PILOT` (Measure JSON serialization percentage in A3-01). | Distinguish intrinsic output cost from search overhead. |
| **H5: Subgraph Drift** | Evaluating Model C on sampled subgraphs systematically underestimates $x_{\text{out}}$ ($x_{\text{out}}^{G'} \le x_{\text{out}}^G$). | **PROVEN** (Theorem 3). | **VERIFIED ON MICROGRAPHS** (Microcases prove lost external cut edges). | Workloads must separate `FULL_GRAPH` from `INDUCED_SUBGRAPH_RELATIVE`. |
| **H6: Update Locality** | Edge updates $\{a, b\}$ with $\{a, b\} \cap S_f = \emptyset$ leave Model C scores strictly invariant. | **PROVEN** (Theorem 2 corollary). | **VERIFIED ON MICROGRAPHS** (APX-02 differential test step 12). | Theoretical foundation for update-local candidate indexing in Gate A4. |

---

## 3. Dataset & Query Corpus Census (NetFlow)

- **Initial Graph (`Ndata.graph`):** $3{,}114{,}895$ vertices, $2{,}849{,}732$ edges, single vertex label `0`, max degree $49{,}014$.
- **Author Query Corpus:** Exactly $2{,}700$ query files in the NewSP release distribution:
  - **Structurally parseable queries ($2{,}670$ files):** Verified simple graphs across vertex size distribution:
    - $|V| = 5$: $298$ queries
    - $|V| = 6$: $302$ queries
    - $|V| = 7$: $1{,}079$ queries
    - $|V| = 8$: $290$ queries
    - $|V| = 9$: $347$ queries
    - $|V| = 10$: $284$ queries
    - $|V| = 11$: $66$ queries
    - $|V| = 12$: $4$ queries
  - **Incompatible queries ($30$ files):** In `staticCaseStudyQuery`, containing malformed headers (`t 7 10`) and missing edge labels. Documented as exclusions.
  - **Out-of-domain query ($1$ file):** `Nquery180_8/Q_136` contains edge label $32513$ (valid graph domain is $0..7$). Documented as exclusion.
  - **Structurally and label-valid queries:** $2{,}669$ queries.
  - **Author Query Gap:** Exactly $0$ queries have $|V| = 4$, and $0$ queries are paths or trees ($|E| = |V| - 1$); all author queries contain cycles. Synthetic probes (`probe_star4_l0`, `probe_path4_l0`, `probe_tree5_l0`) bridge this structural gap.

---

## 4. Decision Framework & Transition Rubric (A3-00 ➔ A3-01)

### 4.1. Prerequisites to Proceed to A3-01 (Micro-Pilot Execution)
The project may transition from A3-00 to A3-01 ONLY IF all of the following conditions are met:
1. **Adjudicated Feasibility Review Passed:** Independent reviewers confirm the mathematical proofs (Theorems 1, 2, 3), workload manifest, provenance, and preflight tooling.
2. **User Explicit Authorization:** The user explicitly approves proceeding to A3-01. (Issue creation is NOT authorization to run).
3. **Preflight Clean Run:** `scripts/apx04_preflight.py` executes with exit code 0 (`AUDIT_PASS`).
4. **Verifiable Active Memory Containment Demonstrated:** A fail-closed active resident memory containment harness ($\le 3.0\,\text{GiB}$) is demonstrated on the host workstation.
5. **Hardware Floor Satisfied:** Host available RAM $\ge 4.0\,\text{GiB}$ and disk $\ge 10\,\text{GiB}$ verified prior to execution.

### 4.2. Mandatory STOP Conditions (Gate A3 Exit Rules)
Execution MUST HALT with a scientific verdict of `STOP` or `INCONCLUSIVE` if any of the following occur during A3-01/02:
- **Condition S1 (Systemic OOM / Safety Block):** Available workstation RAM drops below $4.0\,\text{GiB}$, or process violates the active $3.0\,\text{GiB}$ resident budget.
- **Condition S2 (Universal Timeout on Initial Matching):** $M_0$ static matching exceeds $60.0\,\text{s}$ wall clock across initial pilot cases.
- **Condition S3 (Trivial Semantic Degeneracy):** For all NetFlow queries, accepted matches are identically 0 under $\varepsilon_x \le 30$ due to hub exclusion, while $\varepsilon_x = \text{None}$ produces $> 10^7$ matches causing immediate budget exhaustion.
- **Condition S4 (Integrity Violation):** Any unverified modification to pinned RapidFlow source, Model C definitions, or ground-truth Oracle.

### 4.3. Registered Workload Identity Fingerprints & Trust Boundary

To prevent accidental workload drift or query substitution (such as rebinding a registered pilot case to an alternative label-compatible query with divergent Oracle cardinalities), all `INITIAL_SAFE_PILOT` cases require a cryptographic `registered_identity_sha256`.

#### Canonical Identity Fingerprint Specification
The fingerprint is computed over a canonical JSON serialization of the structural and semantic identification tuple:
```python
payload = {
    "case_id": case["case_id"],
    "eps_m": case["eps_m"],
    "eps_x": case["eps_x"],
    "graph_id": case["graph_source"],
    "graph_sha256": verified_graph_sha,
    "mode": case["mode"],
    "query_id": case["query_id"],
    "query_sha256": verified_query_sha,
    "semantic_class": case["semantic_class"],
    "stream_id": case["stream_id"],
    "stream_sha256": verified_stream_sha,
}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
registered_identity_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```
Component hashes (`graph_sha256`, `query_sha256`, `stream_sha256`) are strictly derived from verified on-disk content, not unverified manifest declarations.

#### Trust Boundary
- **What it guarantees:** Detects input drift, accidental misconfiguration, query/graph/stream cross-wiring, and threshold tampering.
- **What it does not guarantee:** Because the manifest is a mutable text file in the repository, a fingerprint is not an asymmetric digital signature against coordinated intentional modification of both the input and the fingerprint. True scientific immutability and provenance rely on Git commit history, pull request review, immutable artifact hashes, and bilateral adversarial adjudication.

#### Workload Versioning Policy
A registered workload cannot silently alter its inputs under the same `case_id`. Any intentional modification to graph, query, stream, thresholds, or execution mode strictly requires defining a new case ID / version, computing a new registered identity fingerprint, and establishing independent APX-02 Oracle ground truth.

---

## 5. Current Gate A3-00 Verdict

$$\mathbf{VERDICT:}\quad \text{\textbf{A3\_00\_READY\_FOR\_INDEPENDENT\_REVIEW\_ROUND\_5}}$$
$$\mathbf{EXECUTION\_READINESS:}\quad \text{\textbf{SAFETY\_BLOCKED}}$$

- **Summary of Achievement:** Feasibility protocol, mathematical proofs of hub exclusion and boundary cut identities, internal chord counterexample, dynamic event non-monotonicity analysis, query corpus census, initial safe pilot design with deferred full-graph cases, stream provenance verification, sequential G0 replay verification, independent pilot graph metadata validation, strict case binding, fail-closed stream dispatch, fixed vertex universe enforcement, registered workload identity fingerprint binding, and resource safety plan have been fully established.
- **Recommended Next Action:** Antigravity commits and pushes Round 5 remediation on branch `feature/rf20-apx04-feasibility`, updates Draft PR #21, and awaits independent review.
