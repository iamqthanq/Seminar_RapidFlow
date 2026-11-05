# APX-01: Prior-Art Comparative Matrix & Literature Audit

- **Document Version:** `1.0.0-PROPOSAL`
- **Gate:** Gate A0 (APX-01 — Semantics & Threshold Selection Protocol)
- **Status:** DRAFT WORKING SPECIFICATION / FOR PEER & ADVISOR REVIEW
- **Parent Issue:** [#12 — [APX-01] Gate A0: Approximate CSM Semantics & Threshold Selection Protocol](https://github.com/iamqthanq/Seminar_RapidFlow/issues/12)
- **Repository Target:** `iamqthanq/Seminar_RapidFlow` (Integration base: `QuangThang`)
- **Author:** Senior Graph Algorithms Researcher & Research Software Engineer

---

## 1. Prior-Art Audit Scope & Standards

This document establishes a rigorous comparative matrix contrasting proposed Approximate CSM formulations against six seminal research foundations.

To maintain scientific integrity:
1. Every paper is cited with its formal publication venue and accessible URL.
2. We explicitly document what each paper **DOES support**, what it **DOES NOT support**, and how our proposed models differ.
3. Any assertion that cannot be verified directly from the publication text is explicitly marked as `UNVERIFIED`.
4. No formula proposed by the seminar research team (e.g. $x_{\mathrm{out}}$) is falsely attributed to external authors.

---

## 2. Comparative Prior-Art Matrix

| Dimension | SAPPER (PVLDB 2010) | NeMa (PVLDB 2013) | Fan et al. (SIGMOD 2011 / TODS 2013) | RapidFlow (PVLDB 2022) | NewSP (ICDE 2024) | Milo et al. (Science 2002) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Full Reference** | Zhang, Yang, Jin, *PVLDB* 3(1):1185–1194, 2010. [Paper URL](https://www.vldb.org/pvldb/vol3/R105.pdf) | Khan et al., *PVLDB* 6(11):1009–1020, 2013. [Paper URL](https://sites.cs.ucsb.edu/~xyan/papers/vldb13_nema.pdf) | Fan et al., *SIGMOD* 2011 & *ACM TODS* 38(3):1–45, 2013. [Paper URL](https://www.research.ed.ac.uk/en/publications/incremental-graph-pattern-matching-2/) | Sun et al., *PVLDB* 15(11):3275–3287, 2022. [Code URL](https://github.com/shixuansun/RapidFlow) | Li et al., *ICDE* 2024. [Code URL](https://github.com/hnuGraph/NewSP) | Milo et al., *Science* 298(5594):824–827, 2002. [Pubmed URL](https://pubmed.ncbi.nlm.nih.gov/12399590/) |
| **Problem Solved** | Approximate Subgraph Indexing & Querying | Fast Graph Search with Label Similarity | Incremental Graph Pattern Matching | Continuous Subgraph Matching (CSM) | Continuous Subgraph Matching (CSM) | Network Motifs (Statistical Superfamilies) |
| **Exact vs. Approximate** | **Approximate** (Missing edges) | **Approximate** (Fuzzy neighborhood cost) | **Exact Simulation** (Bounded path distance) | **Exact** (Subgraph Monomorphism) | **Exact** (Subgraph Monomorphism) | **Exact Counts** vs. Random Null Model |
| **Graph Dynamics** | **Static Graph** (One-time query) | **Static Graph** (Offline index) | **Dynamic Graph** (Edge updates $\Delta G$) | **Dynamic Graph** (Sequential updates) | **Dynamic Graph** (Sequential updates) | **Static Snapshots** (Biological networks) |
| **Injectivity Required?** | **Yes** (Subgraph isomorphism) | **No** (Many-to-one homomorphism) | **No** (Graph simulation relation) | **Yes** (Injective vertex mapping) | **Yes** (Injective vertex mapping) | **Yes** (Induced/non-induced subgraphs) |
| **Deviation Function** | Missing edge count: $m(f) \le \varepsilon_m$ | Neighborhood label distance + weight | Edge mapped to path of length $\le k$ | **None** ($d=0$, strict edge presence) | **None** ($d=0$, strict edge presence) | Statistical $Z$-score against null model |
| **Extraneous Edge Penalty?** | **No** (Ignores internal & external extra edges) | **No** (Permits arbitrary clustering) | **No** (Simulation ignores extra edges) | **No** (Non-induced allows extra edges) | **No** (Non-induced allows extra edges) | **Contextual** (Evaluates induced subgraphs) |
| **Threshold Selection** | Parameter $\varepsilon \in [0, 4]$ chosen empirically | Parameter $K$ (Top-K) and distance radius | Bounded distance $k$ (hop count) | N/A ($\varepsilon = 0$) | N/A ($\varepsilon = 0$) | Statistical significance threshold ($Z > 2.0$) |
| **Output Representation** | Full set of matching embeddings | Top-$K$ ranked embeddings | Graph relation (set of node pairs) | `COUNT_ONLY` ($\sum \Delta$) or `FULL_ENUM` | `COUNT_ONLY` ($\sum \Delta$) or `FULL_ENUM` | Motif concentration profile / $Z$-scores |
| **Primary Datasets** | AIDS, Yeast, Human, DBLP | DBLP, Freebase, WordNet | Synthetic, DBLP, YouTube | NetFlow, LiveJournal, Twitter | NetFlow, Amazon, LiveJournal | Transcription networks, C. elegans, WWW |

---

## 3. Deep Architectural Analysis per Research Foundation

### 3.1. SAPPER (Zhang, Yang, Jin — PVLDB 2010)
- **What it DOES Support:**
  - Proves that indexing static graphs under a missing-edge threshold $m(f) \le \varepsilon_m$ is mathematically tractable using $L$-neighborhood signatures.
  - Demonstrates that candidate space grows exponentially with $\varepsilon_m$, necessitating frequency-based filtering.
- **What it DOES NOT Support:**
  - Does NOT handle dynamic streaming edge updates ($\Delta e$).
  - Does NOT penalize external incident edges ($x_{\mathrm{out}}$) or internal chords ($x_{\mathrm{in}}$). SAPPER cannot model extraneous peripheral tails.
  - Does NOT provide threshold calibration guidelines for dynamic computer network telemetry.
- **Inheritable Components for APX:** The formalization of missing-edge deviation $m(f)$.
- **Incompatible Components:** SAPPER's static randomized index generation cannot be ported to high-velocity edge streams.

### 3.2. NeMa (Khan et al. — PVLDB 2013)
- **What it DOES Support:**
  - Establishes a comprehensive evaluation of neighborhood label similarity and top-$K$ scoring in graph search.
  - Formalizes precision, recall, and query answering quality using domain relevance labels.
- **What it DOES NOT Support:**
  - NeMa does NOT enforce injective vertex mappings (permits multiple query vertices to map to the same data vertex).
  - NeMa does NOT support continuous incremental updates over streaming graphs.
  - The NeMa distance metric is soft and heuristic, lacking an exact integer edge-omission interpretation.
- **Inheritable Components for APX:** The top-$K$ evaluation methodology and candidate score ranking concepts.
- **Incompatible Components:** Non-injective matching model; porting NeMa directly would violate the fundamental injectivity requirement of the seminar project.

### 3.3. Fan et al. (SIGMOD 2011 / ACM TODS 2013)
- **What it DOES Support:**
  - Rigorous foundational theory for **incremental graph pattern matching** over dynamic graphs with edge insertions and deletions.
  - Proves complexity bounds for incremental update tracking.
- **What it DOES NOT Support:**
  - Evaluates **graph simulation** and **bounded simulation**, NOT subgraph isomorphism. Simulation allows many-to-one relations and does not enforce one-to-one structural congruence.
  - Does NOT solve missing-edge or extraneous-edge bounded subgraph matching.
- **Inheritable Components for APX:** Formal state transition definitions for incremental graph maintenance.
- **Incompatible Components:** The simulation matching relation itself cannot serve as an oracle for injective subgraph embeddings.

### 3.4. RapidFlow (Sun et al. — PVLDB 2022) & NewSP (Li et al. — ICDE 2024)
- **What they DO Support:**
  - State-of-the-art baseline engines for exact continuous subgraph matching on dynamic streaming graphs.
  - Verified performance baselines and data structures on the NetFlow dataset (3.11M vertices, 2.85M edges).
- **What they DO NOT Support:**
  - Neither RapidFlow nor NewSP supports inexact or approximate matching.
  - Their pruning mechanisms (NLF degree filtering, Global Edge Views, Local View intersections) assume $100\%$ exact edge presence. Setting $m > 0$ invalidates their internal candidate filtering.
- **Inheritable Components for APX:**
  - Stream ingestion loop, graph CSR data structures, and timing harness.
  - Execution pipeline and automorphism orbit partitioning.
- **Incompatible Components:**
  - Exact NLF checks (`nlf_check()`) and leaf counting shortcuts (`result_count += candidates.size()`) cannot be applied directly when $m > 0$ or when external degree $x_{\mathrm{out}}$ must be evaluated.

### 3.5. Milo et al. (Science 2002)
- **What it DOES Support:**
  - The foundational biological and statistical definition of **network motifs**.
  - Establishes that recurring subgraphs require evaluation against randomized degree-preserving null models to assess statistical overrepresentation.
- **What it DOES NOT Support:**
  - Milo et al. do NOT propose an approximate matching algorithm or continuous stream processing engine.
  - It does NOT provide a structural deviation metric for query-driven search.
- **Scientific Boundary:** Our research addresses **Approximate Continuous Subgraph Matching (deterministic query evaluation)**, NOT statistical motif discovery.

---

## 4. Synthesis & Grounding Table for Seminar Project

| Research Question | Supported by Literature? | Literature Source | Research Status in Seminar Project |
| :--- | :---: | :--- | :--- |
| Can missing edges ($m \le \varepsilon_m$) be indexed efficiently? | **Yes** (Static) | SAPPER (PVLDB 2010) | Needs adaptation to dynamic streams (APX-04). |
| Does literature define external boundary clutter ($x_{\mathrm{out}} \le \varepsilon_x$)? | **No** | **Original Team Proposal** | Defined in APX-01; must be verified on NetFlow. |
| Is NetFlow an established benchmark for exact CSM? | **Yes** | NewSP (ICDE 2024), RapidFlow (PVLDB 2022) | Established for exact CSM; benchmark for approximate CSM must be constructed internally. |
| Does an off-the-shelf approximate CSM oracle exist for NetFlow? | **No** | Verified Absence | Must be constructed independently in APX-02. |
| Is $\varepsilon = 2$ a standard, verified threshold in literature? | **No** | Verified Absence | Reclassified as `FINAL_THRESHOLD_PENDING_VALIDATION`. |
