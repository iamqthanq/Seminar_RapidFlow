# Research Decision Record R1: RapidFlow Native Integration Architecture

**Decision ID:** `D-R1-native-integration`
**Date:** 2026-11-18
**Plan Version Before:** 1.0
**Plan Version After:** 1.1 (Phase P2 Architecture Specifications Finalized)
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D (Issue #28)
**Parent Document:** Issue #28 (`D-INT-01 / P0–P1`)
**Base Commit:** `03be8b56a9479609adf87d213122eabefc9162fb`
**Verdict:** `READY_FOR_P2_DESIGN`

---

## 1. Executive Summary & Verdict Rationale

Based on the line-by-line audit of original RapidFlow source code (`2ea4f4c...`), the empirical execution of native validation Probes A through F, and mathematical analysis of the Edge Recall / S2 problem contract:

**Verdict:** **`READY_FOR_P2_DESIGN`**

### Verdict Meaning & Non-Claims
- `READY_FOR_P2_DESIGN` certifies that sufficient architectural and runtime evidence exists to proceed with the formal design and initial construction of Phase P2 (`extensions/rapidflow_apx/`).
- It does **NOT** claim that native integration is already complete, nor does it claim that native APX is faster than M1 or baselines.
- The audit affirmatively proved both viable integration paths (Graph CSR, GlobalEdgeView, streaming snapshot sequencing) and fatal exact mechanisms that must be replaced (exact NLF pruning, leaf count shortcut, automorphism factor multiplication).

---

## 2. Answers to Mandatory Research Questions (1–14)

### 1. Which original RapidFlow modules are KEPT?
- **`Graph` (`graph/graph.h`, `graph/graph.cpp`)**: KEPT (`KEEP`). Used for immutable query representation $Q$ and initial data graph snapshots. Accessors (`getVerticesCount()`, `getVertexNeighbors()`, `checkEdgeExistence()`, `getEdgeLabelByVertex()`) are directly called.
- **`GlobalEdgeView` (`utility/relation/global_edge_view.h`)**: KEPT (`KEEP`). The sorted vector adjacency container with `lower_bound` binary search provides high-performance mutable overlay storage.
- **`types.h` / `config.h` (`configuration/`)**: KEPT (`KEEP`). Primitive typedefs (`VertexID`, `LabelID`, `ui`).
- **`graphoperations.*` (`utility/`)**: KEPT (`KEEP`). Graph I/O utilities.

### 2. Which modules must be ADAPTED or REPLACED?
- **`streaming/global_view_manager.*`**: ADAPTED (`CHANGE`). Retain the raw view mapping table (`views_`), but REPAIR candidate generation: completely bypass `candidates_` and `encoded_candidates_` which rely on exact `nlf_check`.
- **`streaming/streaming_engine.*`**: ADAPTED (`CHANGE`). Retain the snapshot sequencing (INSERT on $G_{\text{after}}$, DELETE on $G_{\text{before}}$). Remove relevance filtering from graph state maintenance (all updates must update authoritative state). Add prevalidation and transactional rollback.
- **`streaming/search_engine.*`**: REPLACED (`CHANGE`). The exact backtracking engine with leaf candidate count shortcut cannot emit mapping tuples. Replaced by native M1/M2 DFS kernels with explicit candidate binding.
- **`streaming/order_manager.*`**: ADAPTED (`CHANGE`). Automorphism symmetry detection is retained to find generators, but scalar multiplication ($N \times |Aut(Q)|$) is replaced by explicit automorphism permutation iteration.
- **`streaming/local_view_manager.*`**: REPLACED for Bare M2 (`CHANGE`). Exact candidate set intersection along query edges is unsound when edges can be missing. Bare M2 uses direct DFS branching.

### 3. How do the authoritative current graph and mutable views cooperate?
- **Authoritative State Architecture**: A two-tier hybrid structure `ApxAuthoritativeGraph`:
  - Tier 1: Pinned RapidFlow CSR `Graph` stores the immutable baseline snapshot $G_0$.
  - Tier 2: `GlobalEdgeView` mutable overlays (`fwd_overlay_`, `rev_overlay_`) store dynamic additions, while a dynamic tombstone bitset/set tracks deletions of CSR base edges.
- **Unified Lookup**:
  - `has_edge(u, v)`: Checks overlay first. If present, returns true; if tombstoned, returns false; otherwise queries CSR `checkEdgeExistence(u, v)`.
  - `get_edge_label(u, v)`: Checks overlay label table first; falls back to CSR `getEdgeLabelByVertex(u, v)`.
  - Neighbor iteration: Streams CSR neighbors filtering tombstones, merged with overlay neighbors.
- Non-query edges are recorded in authoritative state so topological integrity is never corrupted.

### 4. Where does the APX search pipeline hook into reused components?
- The APX search engine hooks directly into `GlobalEdgeView` and `ApxAuthoritativeGraph`:
  - Search receives `query_graph` (RapidFlow `Graph`).
  - Search queries candidate neighbors using `ApxAuthoritativeGraph::iterate_neighbors`.
  - Search checks edge presence and labels using `ApxAuthoritativeGraph::has_edge` and `get_edge_label`.
  - The search loop is an integrated C++ routine that walks the query structure, branching on PRESENT vs MISSING edges (M2) or iterating variant queries $Q - D$ (M1).

### 5. How are transactions and rollback designed?
- Prevalidation runs before any state mutation: verifies endpoints exist in $V_0$, $x \ne y$, prevents duplicate insertion and absent deletion.
- For INSERT:
  1. Forward insert into `fwd_overlay_` and forward `GlobalEdgeView`.
  2. Reverse insert into `rev_overlay_` and reverse `GlobalEdgeView`.
  3. Search executed on $G_{\text{after}}$.
  4. If search or sink throws an exception, rollback removes both edges and restores pre-step cardinality.
- For DELETE:
  1. Search executed on $G_{\text{before}}$.
  2. Forward and reverse edges removed/tombstoned.
  3. If an exception occurs during search, state is untouched.

### 6. How are external IDs, typed labels, and tuple order handled?
- **External ID Codec**: External IDs (which may be negative, sparse, or 64-bit integers) are bijectively mapped to dense `uint32_t` indices $0 \le v < |V_0|$ for RapidFlow compatibility.
- **Typed Label Separation**: Integer `0` and string `"0"` are stored with distinct type tags in the codec, preventing hash/key collisions.
- **Tuple Order**: Tuples are emitted sorted strictly by original query vertex IDs: $(f(u_1), \dots, f(u_n))$ where $u_1 < \dots < u_n$. The internal DFS matching order is strictly hidden.

### 7. Which Exact components will native M1 reuse?
- Native M1 decomposes approximate matching into $\binom{m-1}{k}$ exact queries $Q_D = (V_Q, E_Q \setminus D)$.
- Each $Q_D$ is represented as an anchored query using RapidFlow's `Graph`.
- The matching order generation from `OrderManager` can be applied directly to $Q_D$.
- Adjacency intersections along existing edges can reuse RapidFlow candidate intersection routines.

### 8. How does the tuple path eliminate leaf count shortcuts and automorphism factorization?
- **Leaf Unrolling**: In `SearchEngine`, the line `result_count += local_idx_[next_depth].second;` is replaced by:
  ```cpp
  for (uint32_t i = 0; i < local_idx_[next_depth].second; ++i) {
      embedding_[order[next_depth]] = local_candidates_store_[next_depth][i];
      emit_tuple(embedding_);
  }
  ```
- **Automorphism Expansion**: Instead of multiplying count by `automorphism_size`, the automorphism group $\operatorname{Aut}(Q)$ is materialized as a list of permutations. For each base embedding found, all distinct permutations $\sigma(f)$ are generated and emitted.

### 9. Where is bare M2 integrated?
- Bare M2 is integrated as a native search engine class `ApxM2SearchEngine`.
- It anchors at the updated data edge $\{x, y\}$ and query edge $q = \{u, v\}$.
- It branches recursively on PRESENT and MISSING edges.
- Unassigned vertices are deferred until an incident edge is resolved.
- Leaf evaluation enforces $|R(f)| == k$.

### 10. Are build flags, compilers, and dependencies feasible?
- Fully feasible. GCC 13.3.0 and CMake 3.28.3 compile the codebase cleanly under `-O2 -Wall -Wextra -pthread -std=c++17`.
- No third-party dependencies beyond the standard C++ library and the included header-only `sparsepp` library.

### 11. What is in scope and out of scope for Phase P2?
- **In Scope for P2**:
  - `ApxTypedCodec`: external-to-internal ID and typed label mapping.
  - `ApxAuthoritativeGraph`: hybrid CSR + `GlobalEdgeView` mutable overlay.
  - `ApxTransactionManager`: prevalidation, mutation sequencing, rollback.
  - `ApxTupleSink`: canonical tuple serialization, automorphism permutation expansion.
- **Out of Scope for P2**:
  - Full production M1/M2 search engines (scheduled for P3).
  - NLF and dynamic pruning $\delta'$ (scheduled for P4).
  - Large NetFlow benchmarks (scheduled for P5).

### 12. Were any plan assumptions modified based on probe evidence?
- **Yes (Major Finding)**: Probe D empirically proved that RapidFlow's exact NLF condition ($v\_nlf \ge u\_nlf$) prunes valid approximate matches. The plan was updated to explicitly ban exact NLF candidate filtering in P2/P3 and defer relaxed NLF to Phase P4.

### 13. Is D-G deferred or proposed for immediate implementation?
- **Strictly Deferred (`DEFER`)**: D-G ($k \ge \lambda(Q)$) requires dynamic S2 connectivity checks on partial and full mappings. Attempting D-G prematurely would jeopardize native D-R integration. D-R remains the exclusive active scope.

### 14. Which claims remain unproven and must not be asserted?
- No claim of speedup or memory efficiency over Python M1/M2.
- No claim that M2 outperforms M1 natively.
- No claim that RapidFlow-APX is ready for production NetFlow workloads.
- No claim that $W_{\text{M1}}/W_{\text{M2}} \approx 1.52$ represents proven search work reduction.

---

## 3. Classification of Core Decisions

| Decision Area | Proposed Action | Classification | Rationale |
| :--- | :--- | :---: | :--- |
| Core Graph CSR | Reuse `graph.h`/`graph.cpp` for queries and initial snapshot | `KEEP` | Proven high-performance, compact memory, binary search neighbor access. |
| Dynamic Adjacency | Reuse `GlobalEdgeView` for mutable edge overlay | `KEEP` | High-performance sorted vector adjacency; tested in Probe C. |
| Exact NLF Check | Ban `nlf_check` from candidate filtering for $k \ge 1$ | `CHANGE` | Proven unsound in Probe D; prunes valid approximate embeddings. |
| Leaf Count Shortcut | Unroll candidate loop into explicit tuple binding | `CHANGE` | CSM contract mandates explicit ordered tuple mappings. |
| Automorphism Factorization | Expand automorphism group into concrete permutation orbits | `CHANGE` | Distinct symmetric mappings must be individually emitted. |
| Update Relevance Filtering | Retain for search triggering; bypass for authoritative graph update | `ADAPT` | Safe in D-R for search triggering, but graph state must remain authoritative. |
| General Threshold D-G | Defer until D-R native integration is complete | `DEFER` | Premature complexity; requires dynamic connectivity checking. |
| Standalone C++ Rewrite | Reject standalone rewrite uncoupled from RapidFlow | `STOP` | Violates core project mandate of genuine RapidFlow extension. |

---

## 4. Next Step Recommendation

Proceed to **Phase P2** (Issue formulation for Native Foundation & Tuple Materialization):
1. Lock Phase P2 issue scope to `extensions/rapidflow_apx/` foundation: `ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, and `ApxTupleSink`.
2. Do not code Phase P2 until Phase P0–P1 Draft PR is reviewed and merged by the user.
