# RapidFlow-APX Component Reuse Inventory (Direction D)

**Document ID:** `docs/rapidflow_apx_reuse_inventory.md`
**Date:** 2026-11-18
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D (D-INT-01 / P0–P1)
**Parent Document:** Issue #28
**Base Commit:** `03be8b56a9479609adf87d213122eabefc9162fb`
**Upstream RapidFlow SHA:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`

---

## 1. Executive Summary & Categorization Framework

Direction D mandates extending the original RapidFlow C++ codebase to support **Approximate Continuous Subgraph Matching (APX CSM)** under **Edge Recall** and **S2 connectivity** on dynamic labeled graphs ($0 \le k < \lambda(Q)$).

The overarching requirement is **genuine native integration**:
- Reusing original RapidFlow components (Graph representation, Global Views, memory-efficient data structures, and streaming pipelines).
- Constructing an integrated C++ approximate update-and-search pipeline.
- Materializing canonical ordered tuple embeddings rather than scalar counts.
- Verifying bit-for-bit correctness against the independent M0 Ground Truth Oracle.

To maintain strict scientific hygiene, components are classified into four mutually exclusive categories:
1. `KEEP`: Existing production code or ground-truth oracle retained as-is without semantic modification.
2. `ADAPT`: Components whose core data structures or algorithms are preserved but require explicit modification, extension, or wrapping to support APX semantics (missing edges, tuple materialization, rollback).
3. `HISTORICAL_ONLY`: Artifacts, reports, and legacy code from previous phases (Model C / APX-01..04, DSTAR-01..03 pilot) preserved immutably for provenance, benchmark evidence, and regression testing.
4. `NEW`: Components designed and implemented specifically for Direction D (audit harness, typed codec, tuple sinks, native M1/M2 kernels).

---

## 2. Comprehensive Inventory Table

| Component Path | Role / Function | Source / Commit | Semantics | Classification | Current Evidence | Key Limitations / Unknowns | Direction D Usage |
| :--- | :--- | :--- | :--- | :---: | :--- | :--- | :--- |
| `baselines/rapidflow/source/graph/graph.h`, `graph.cpp` | Static CSR graph representation, neighbor indexing, binary edge lookup | Upstream `2ea4f4c...` | Undirected labeled CSR; binary search over sorted neighbor arrays | `ADAPT` | Linked in APX-03; verified in RF-01 smoke and APX-03 test suite (46 tests). | Fixed-size immutable CSR buffer; assumes dense contiguous IDs ($0 \le v < |V|$); no in-place dynamic mutation. | Primary representation for query topology $Q$ and initial graph snapshots; wrapped by dynamic mutable layer. |
| `baselines/rapidflow/source/utility/relation/global_edge_view.h` | Dynamic sparse hash map of sorted neighbor vectors | Upstream `2ea4f4c...` | `sparse_hash_map<uint32_t, vector<uint32_t>>`; maintains sorted adjacency per vertex | `ADAPT` | Used in RapidFlow upstream streaming engine; audited in P1 Probe C. | Does NOT store edge labels internally (labels live in view mapping); vector mutation invalidates pointers/iterators. | Core container for dynamic mutable edge overlay in authoritative graph state. |
| `baselines/rapidflow/source/streaming/global_view_manager.*` | Multiset NLF stats, exact candidate sets, encoded NLF views | Upstream `2ea4f4c...` | Tracks NLF frequencies and exact candidate sets for query vertices | `ADAPT` (Partial) / `REPLACE` (NLF) | Audited in P1 Probe D; demonstrated pruning of valid APX candidates. | Exact NLF (`v_nlf >= u_nlf`) prunes valid approximate matches when $k \ge 1$; encoded candidate views assume exact match. | Raw view indexing (`views_`) is adaptable; exact NLF checking and encoded candidate filtering must be replaced. |
| `baselines/rapidflow/source/streaming/streaming_engine.*` | Streaming event loop, snapshot timing, performance counters | Upstream `2ea4f4c...` | INSERT: mutate views then search ($G_{\text{after}}$); DELETE: search then mutate views ($G_{\text{before}}$) | `ADAPT` | Verified in RF-01 and P1 Probe E. Correct snapshot sequencing for CSM events. | Relevance filter drops non-query edges (ignoring authoritative graph state); prints scalar count, no tuples; no rollback. | Architectural template for APX streaming engine: keep snapshot timing, replace relevance drop with full graph update, add rollback. |
| `baselines/rapidflow/source/streaming/search_engine.*` | Backtracking search engine on reduced query | Upstream `2ea4f4c...` | Exact backtracking search; leaf-level candidate count shortcut | `REPLACE` (Search) | Audited in RF-01/RF-02 and P1 Probe F. | Leaf shortcut (`result_count += count`) bypasses tuple generation; only counts, never materializes tuples. | Search engine must be replaced with native M1-enum and M2-Event DFS kernels supporting tuple emission. |
| `baselines/rapidflow/source/streaming/order_manager.*` | Query automorphism detection, reduced query order generation | Upstream `2ea4f4c...` | Automorphism factor scalar multiplication ($N \times \|Aut(Q)\|$) | `ADAPT` | Audited in RF-01/RF-02 and P1 Probe F. | Scalar multiplier is invalid for tuple output; all symmetric tuples must be explicitly emitted. | Automorphism detection algorithm can generate permutation matrices; search must iterate over full orbits or unfold automorphisms. |
| `baselines/rapidflow/source/streaming/local_view_manager.*` | Dynamic candidate set intersection across query edges | Upstream `2ea4f4c...` | Set intersection of candidate lists along query edges | `REPLACE` | Audited in APX-03 and P1. | Intersects candidate sets along all query edges; fails when edge is missing ($e \in R(f)$). | Replaced in bare M2 by dynamic DFS with PRESENT/MISSING branching; potential future adaptation in P4. |
| `extensions/apx03/` | C++ snapshot recompute baseline for Model C | Repo commit `814e7d5...` | Full snapshot recompute on every update ($M_t \setminus M_{t-1}$); Model C ($x_{\text{out}} \le \varepsilon_x$, S1) | `HISTORICAL_ONLY` | 46/46 unit tests passing; validates CMake linkage of `graph.cpp` and sparse ID codec. | Full recompute is $O(|M|)$ per update; Model C constraints incompatible with Edge Recall and S2. | Preserved as historical evidence; CMake linkage and sparse ID remapping techniques serve as references. |
| `scripts/dstar_oracle.py` | Ground truth M0 Oracle for Edge Recall and S2 | Repo commit `ba76437...` | Pure Python stdlib; exhaustive permutation search; rejects $k \ge \lambda(Q)$; verifies S2 | `KEEP` | 35 unit tests PASS; 840 corpus cases verified; 65 pilot workloads verified at 100% agreement. | Bounded search complexity; designed for small verification fixtures ($|V_Q| \le 8, |V_G| \le 10$). | Permanent ground-truth oracle for all Direction D verification gates. |
| `scripts/dstar_event_common.py` | Shared primitives, validation, EventSink with zero-dup assertion | Repo commit `3af671b...` | Immutable graph representation, edge updates, fail-closed validation, canonical tuple serialization | `KEEP` | Verified across DSTAR-02 and DSTAR-03 (390 pilot runs). | Python runtime overhead; not designed for high-throughput streaming. | Specification reference and Python test oracle foundation. |
| `scripts/dstar_m1_enum.py` | Python M1-enum-Exact-k baseline engine | Repo commit `3af671b...` | Decomposes approximate matching into $\binom{m-1}{k}$ exact searches on $Q - D$; requires $R(f) == D$ | `KEEP` | Verified on 840 corpus cases and 65 pilot workloads. | Python implementation. | Algorithmic specification for future native M1 engine. |
| `scripts/dstar_m2_event.py` | Python bare M2-Event-k single-pass DFS engine | Repo commit `3af671b...` | Anchored DFS with PRESENT / MISSING edge branching and deferred vertex mapping; leaf $|R| == k$ | `KEEP` | Verified on 840 corpus cases and 65 pilot workloads. | Python implementation; no NLF or dynamic ordering. | Algorithmic specification for future native M2 engine. |
| `tests/fixtures/dstar01/`, `corpus_840/`, `dstar03/` | Small graph and workload test fixtures | DSTAR-01..03 commits | Deterministic graph, query, and stream files | `KEEP` | Fully auditable across previous PRs. | Synthetic and micro-scale; NetFlow probes yielded zero events at $|S| \le 8$. | Standard regression and boundary test cases for native components. |
| `tests/native/rapidflow_apx_audit/` | Minimal C++ native audit harness | **NEW** (Issue #28) | Compiles and links `graph.cpp`; executes Probes A–F; emits structured JSON | `NEW` | Implemented and verified in P1. | Audit-only; does not implement full APX search engine. | Validates C++ build provenance, typed boundary, mutable views, NLF incompatibility, and rollback. |
| `scripts/run_rapidflow_apx_audit.py` | Fail-closed Python runner for audit harness | **NEW** (Issue #28) | Validates manifest, executes native binary, checks JSON schema, enforces resource limits | `NEW` | Implemented and verified in P1. | Runner for audit probes only. | Orchestrates reproducible verification of native components. |
| `rapidflow_apx` (Planned P2 Engine) | Native RapidFlow-APX foundation & tuple path | Planned (Issue P2) | Integrated C++ engine with authoritative graph, transactional updates, and tuple sink | `NEW` (Future) | Design locked in R1 decision record. | Not yet implemented in P1. | Primary target of Direction D. |

---

## 3. Boundary Demarcation: Historical vs Current Assets

### 3.1. Model C Artifacts (`HISTORICAL_ONLY`)
- `scripts/apx02_oracle.py`, `extensions/apx03/`, and `docs/apx03_rapidflow_integration_audit.md` were built under the Model C contract ($\varepsilon_m, \varepsilon_x$, S1 data-induced connectivity, `SCORE_CHANGED`).
- In Model C, external edges were penalized ($x_{\text{out}} \le \varepsilon_x$), leading to severe hub explosion on NetFlow.
- Under Direction D (Edge Recall and S2), extraneous external edges are **unpenalized**. Therefore, relevance filtering at the update anchor level is structurally valid for event detection, provided authoritative graph state is preserved.

### 3.2. DSTAR-01–03 Pilot Assets (`HISTORICAL_ONLY / EVIDENCE`)
- DSTAR-01 established the Edge Recall contract and independent M0 Oracle.
- DSTAR-02 implemented Python M1 and bare M2, proving the Event Boundary Lemma ($|R| == k$).
- DSTAR-03 completed the 65-workload paired pilot (390 runs). Metric `budget_units` confirmed that bare M2 reduces internal candidate evaluations on synthetic approximate workloads, while also highlighting the limitation of tiny NetFlow probes ($|S| \le 8$).
- DSTAR-03 is **complete**. The roadmap now transitions from Python exploratory prototypes to **native RapidFlow C++ integration (Direction D)**.
