# Research Decision Record R1: RapidFlow Native Integration Architecture

**Decision ID:** `D-R1-native-integration`
**Date:** 2026-11-18
**Plan Version Before:** 1.0
**Plan Version After:** 1.1 (Phase P2 Architecture Specifications Finalized with Remediation F01–F06)
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D (Issue #28 / PR #29)
**Parent Document:** Issue #28 (`D-INT-01 / P0–P1`)
**Base Commit:** `03be8b56a9479609adf87d213122eabefc9162fb`
**Verdict:** `READY_FOR_P2_DESIGN (PENDING_USER_PR_CLOSURE)`

---

## 1. Executive Summary & Verdict Rationale

Based on the line-by-line audit of original RapidFlow source code (`2ea4f4c...`), the empirical execution of native validation Probes A through F using genuine RapidFlow components and locked JSON fixtures, and rigorous mathematical analysis of the Edge Recall / S2 problem contract:

**Verdict:** **`READY_FOR_P2_DESIGN (PENDING_USER_PR_CLOSURE)`**

### Verdict Meaning & Non-Claims
- `READY_FOR_P2_DESIGN (PENDING_USER_PR_CLOSURE)` certifies that sufficient architectural and runtime evidence exists to proceed with the formal design and initial construction of Phase P2 (`extensions/rapidflow_apx/`), pending final user review and merge of Draft PR #29.
- It does **NOT** claim that native integration is already complete, nor does it claim that native APX is faster than M1 or baselines.
- The audit affirmatively proved both viable integration paths (Graph CSR, GlobalEdgeView, streaming snapshot sequencing) and fatal exact mechanisms that must be replaced (exact NLF pruning, leaf count shortcut, naive full-group automorphism multiplication on complete search spaces).

---

## 2. Rigorous Evidence Level Classification

Every architectural statement, claim, and finding in this record is classified under one of five explicit evidence levels:
1. `SOURCE_INSPECTED`: Directly verified by inspecting original RapidFlow upstream code at pinned SHA `2ea4f4c...`.
2. `NATIVE_RUNTIME_VERIFIED`: Empirically observed and validated at runtime by compiling, linking, and executing genuine RapidFlow upstream code against real fixtures.
3. `NEW_AUDIT_ADAPTER_RUNTIME`: Empirically observed at runtime on new test harness adapter structures (not claiming upstream implementation).
4. `PROPOSED`: Proposed design for future phases (P2–P6).
5. `NOT_VERIFIED`: Explicitly unproven, rejected, or deferred.

| Component / Mechanism | Location | Evidence Level | Operational Finding |
| :--- | :--- | :---: | :--- |
| **`Graph` CSR Representation** | `graph/graph.cpp` | `NATIVE_RUNTIME_VERIFIED` | Compiles cleanly; accessors (`getVerticesCount`, `getVertexNeighbors`, `getEdgeLabelByVertex`, `checkEdgeExistence`) verified in Probe A. |
| **`GlobalEdgeView` Mutable Storage** | `utility/relation/global_edge_view.h` | `NATIVE_RUNTIME_VERIFIED` | Maintains sorted neighbor vectors via `std::lower_bound`; duplicate/absent return codes verified in Probe C. Container does not store edge labels. |
| **Exact `nlf_check` Pruning** | `streaming/global_view_manager.h:80-87` | `NATIVE_RUNTIME_VERIFIED` | Evaluated on real `Graph` objects in Probe D. Returns `false` for vertices 1 and 2 on Diamond ($k=1$), incorrectly pruning valid APX match `(0, 1, 2, 3)`. |
| **Candidate Set Exclusion** | `streaming/global_view_manager.cpp:135-144` | `NATIVE_RUNTIME_VERIFIED` | Evaluated in Probe D. `GlobalViewManager::create_views` completely excludes vertices 1 and 2 from `candidates_` when `nlf_check` fails. |
| **Streaming Snapshot Ordering** | `streaming/streaming_engine.cpp:143-157, 210-230` | `SOURCE_INSPECTED` | INSERT updates view then searches ($G_{\text{after}}$); DELETE searches then removes from view ($G_{\text{before}}$). |
| **Adapter Transactional Rollback** | `tests/native/rapidflow_apx_audit/src/audit_probes.cpp` | `NEW_AUDIT_ADAPTER_RUNTIME` | Verified in Probe E on adapter forward/reverse views under simulated fault injection (`INJECTED_TRANSACTION_ABORT`). |
| **Automorphism Edge Orbit Partitioning** | `streaming/order_manager.cpp:47-76` | `SOURCE_INSPECTED` | Partitions directed query edges into orbits; `get_automorphism_meta` returns `automorphism_edges_[id].size()` (12 on $K_4$), representing directed edge orbit size, NOT $|\operatorname{Aut}(Q)|$ (24). |
| **Scalar Automorphism Multiplication** | `streaming/streaming_engine.cpp:181` | `SOURCE_INSPECTED` | Multiplies count by `automorphism_size` (`result_count += local_result_count * automorphism_size`). Valid ONLY because search is restricted to representative space! |
| **Leaf Candidate Count Shortcut** | `streaming/search_engine.cpp:161` | `SOURCE_INSPECTED` | Exact SearchEngine skips leaf vertex binding: `result_count += local_idx_[next_depth].second`. |
| **Tuple Materialization Pipeline** | `extensions/rapidflow_apx/` | `PROPOSED` | Leaf candidate unrolling and representative orbit expansion proposed for Phase P2. |
| **Native M1 & M2 Search Kernels** | `extensions/rapidflow_apx/` | `PROPOSED` | Native variant query execution (M1) and defect-branching DFS (M2) proposed for Phase P3. |
| **Novelty / Speedup Claims** | Entire System | `NOT_VERIFIED` | Banned from assertion. Novelty is an unverified hypothesis requiring literature comparison; empirical performance requires rigorous benchmarking. |

---

## 3. Symmetry & Automorphism Architecture: The Directed Edge Orbit and K4 Analysis

A critical finding of this audit concerns RapidFlow's symmetry handling in `OrderManager` and `StreamingEngine`:

### 3.1. Original RapidFlow Mechanism: Directed Edge Orbits vs Full Automorphism Group
Inspecting `order_manager.cpp:47-49` reveals the exact definition of `automorphism_size`:
```cpp
inline auto get_automorphism_meta(uint32_t id) const {
    return std::make_tuple(automorphism_edges_[id][0], &orders_[id],
                           (uint32_t)automorphism_edges_[id].size());
}
```
1. `OrderManager::detect_automorphism_edges(const Graph *query_graph)` (`order_manager.cpp:51-76`):
   - Computes query automorphism group $\operatorname{Aut}(Q)$ via `GraphOperations::compute_automorphism`.
   - Partitions all **directed query edges** $(u, v)$ into equivalence orbits under $\operatorname{Aut}(Q)$.
   - For each edge orbit, selects only the first directed edge (`automorphism_edges_[id][0]`) as the canonical representative.
   - Generates matching and indexing orders **only for that representative directed edge**.
2. **Directed Edge Orbit Size (12) vs Full Automorphism Group Order (24) on $K_4$**:
   - For undirected $K_4$, there are 4 vertices and 6 undirected edges, corresponding to **12 directed edges**.
   - Under $\operatorname{Aut}(K_4)$ (where $|\operatorname{Aut}(K_4)| = 4! = 24$), all 12 directed edges form a single transitive orbit.
   - Therefore, `automorphism_edges_[id].size()` returns **12**, which is the size of the directed edge orbit, NOT $|\operatorname{Aut}(K_4)| = 24$.
3. `StreamingEngine::execute` (`streaming_engine.cpp:181`):
   - Searches only the representative order for the matched dynamic edge.
   - Scales the resulting count by `automorphism_size` (12):
     ```cpp
     result_count += local_result_count * automorphism_size;
     ```
   - This scalar factor 12 is mathematically sound in RapidFlow **strictly because the search was restricted to a single representative directed anchor edge**. The 12 directed edges in the orbit represent the 12 possible orientations of dynamic edge insertion/deletion.

### 3.2. The K4 Analysis: Why Bare M2 Requires Neither Multiplication Nor Full Expansion
Consider query $Q = K_4$ and data graph $G = K_4$:
- $K_4$ has $|\operatorname{Aut}(K_4)| = 4! = 24$ automorphisms.
- In $G = K_4$, there are exactly $4! = 24$ distinct subgraph isomorphism embeddings (ordered 4-tuples $(f(u_1), f(u_2), f(u_3), f(u_4))$).
- **Default Bare M2 Matching Path**:
  - Bare M2 anchors at the dynamic update and explores all valid anchor orientations/edges in $Q$.
  - It runs backtracking DFS over the full unreduced query space.
  - Because DFS enumerates all valid vertex assignments directly, it discovers each valid ordered tuple $(f(u_1), \dots, f(u_n))$ **exactly once**.
  - Therefore, bare M2 requires **NO post-hoc scalar multiplication and NO post-hoc automorphism expansion**.
- **Fatal Overcounting Trap**:
  - If an implementation executes full DFS across all valid anchor orientations (discovering all 24 distinct embeddings) and then naively applies a post-search expansion by the 24 automorphisms in $\operatorname{Aut}(Q)$, it would emit:
    $$24 \times 24 = 576 \text{ raw embeddings}$$
    producing an erroneous $24\times$ overcount!
  - Similarly, multiplying by `automorphism_size` (12) after full DFS would produce $24 \times 12 = 288$ tuples.

### 3.3. Tuple Canonicalization and Architectural Options for Phase P2
To emit explicit ordered tuples $(f(u_1), \dots, f(u_n))$ without duplication, Phase P2 establishes:
1. **Tuple Canonicalization Rule**:
   All emitted tuples must order coordinate positions strictly by original query vertex IDs: $(f(u_1), f(u_2), \dots, f(u_n))$ where $u_1 < u_2 < \dots < u_n$. The internal DFS exploration order (which varies per order) is completely abstracted away.
2. **Default Path (Bare M2 Exhaustive DFS)**:
   Search over all valid anchor orientations without automorphism scalar multiplication or post-hoc expansion. Each valid embedding is generated natively.
3. **Optimized Path (Representative Search with Orbit Expansion)**:
   Restrict the native DFS to search only canonical representative embeddings (enforcing symmetry-breaking constraints $f(u_a) < f(u_b)$ on symmetric vertices), and then expand each representative embedding across the coset / orbit generators of $\operatorname{Aut}(Q)$. Must be proven equivalent to Bare M2 before adoption.

---

## 4. Answers to Mandatory Research Questions (1–14)

### 1. Which original RapidFlow modules are KEPT?
- **`Graph` (`graph/graph.h`, `graph/graph.cpp`)** (`KEEP`, `NATIVE_RUNTIME_VERIFIED`): Used for immutable query representation $Q$ and initial data graph snapshots. Accessors (`getVerticesCount()`, `getVertexNeighbors()`, `checkEdgeExistence()`, `getEdgeLabelByVertex()`) are directly called.
- **`GlobalEdgeView` (`utility/relation/global_edge_view.h`)** (`KEEP`, `NATIVE_RUNTIME_VERIFIED`): The sorted vector adjacency container with `lower_bound` binary search provides high-performance mutable overlay storage.
- **`types.h` / `config.h` (`configuration/`)** (`KEEP`): Primitive typedefs (`VertexID`, `LabelID`, `ui`).
- **`graphoperations.*` (`utility/`)** (`KEEP`): Graph I/O utilities.

### 2. Which modules must be ADAPTED or REPLACED?
- **`streaming/global_view_manager.*`** (`ADAPT`, `NATIVE_RUNTIME_VERIFIED`): Retain the raw view mapping table (`views_`), but REPAIR candidate generation: completely bypass `candidates_` and `encoded_candidates_` which rely on exact `nlf_check`.
- **`streaming/streaming_engine.*`** (`ADAPT`, `SOURCE_INSPECTED`): Retain the snapshot sequencing (INSERT on $G_{\text{after}}$, DELETE on $G_{\text{before}}$). Remove relevance filtering from graph state maintenance (all updates must update authoritative state). Add prevalidation and transactional rollback.
- **`streaming/search_engine.*`** (`REPLACE`, `SOURCE_INSPECTED`): The exact backtracking engine with leaf candidate count shortcut cannot emit mapping tuples. Replaced by native M1/M2 DFS kernels with explicit candidate binding.
- **`streaming/order_manager.*`** (`ADAPT`, `SOURCE_INSPECTED`): Automorphism symmetry detection is retained to find generators, but scalar multiplication ($N \times |Aut(Q)|$) is replaced by explicit representative orbit expansion or symmetry-broken DFS.
- **`streaming/local_view_manager.*`** (`REPLACE` for Bare M2): Exact candidate set intersection along query edges is unsound when edges can be missing. Bare M2 uses direct DFS branching.

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
- **External ID Codec** (`NATIVE_RUNTIME_VERIFIED` in Probe B): External IDs (which may be negative, sparse, or 64-bit integers) are bijectively mapped to dense `uint32_t` indices $0 \le v < |V_0|$ for RapidFlow compatibility.
- **Typed Label Separation** (`NATIVE_RUNTIME_VERIFIED` in Probe B): Integer `0` and string `"0"` are stored with distinct type tags in the codec, preventing hash/key collisions.
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
- **Automorphism Handling in Tuple Path**:
  - **Default Bare M2 Path**: The native DFS explores all valid anchor orientations over the full unreduced query space. Under this default design, each valid ordered tuple $(f(u_1), \dots, f(u_n))$ is generated directly and uniquely. Therefore, bare M2 requires **NO post-hoc scalar multiplication and NO post-hoc automorphism expansion**.
  - **Optional Representative Search Path**: If search space reduction via representative anchor orbits is used, the search operates strictly on a symmetry-broken representative space and expands canonical embeddings across representative orbits, avoiding the 576-tuple K4 overcounting defect.

### 9. Where is bare M2 integrated?
- Bare M2 is integrated as a native search engine class `ApxM2SearchEngine`.
- It anchors at the updated data edge $\{x, y\}$ and query edge $q = \{u, v\}$.
- It branches recursively on PRESENT and MISSING edges.
- Unassigned vertices are deferred until an incident edge is resolved.
- Leaf evaluation enforces $|R(f)| == k$.

### 10. Are build flags, compilers, and dependencies feasible?
- Fully feasible (`NATIVE_RUNTIME_VERIFIED` in Probe A). GCC 13.3.0 and CMake 3.28.3 compile the codebase cleanly under `-O2 -Wall -Wextra -pthread -std=c++17`.
- No third-party dependencies beyond the standard C++ library and the included header-only `sparsepp` library.

### 11. What is in scope and out of scope for Phase P2?
- **In Scope for P2**:
  - `ApxTypedCodec`: external-to-internal ID and typed label mapping.
  - `ApxAuthoritativeGraph`: hybrid CSR + `GlobalEdgeView` mutable overlay.
  - `ApxTransactionManager`: prevalidation, mutation sequencing, rollback.
  - `ApxTupleSink`: canonical tuple serialization, representative automorphism expansion.
- **Out of Scope for P2**:
  - Full production M1/M2 search engines (scheduled for P3).
  - NLF and dynamic pruning $\delta'$ (scheduled for P4).
  - Large NetFlow benchmarks (scheduled for P5).

### 12. Were any plan assumptions modified based on probe evidence?
- **Yes (Major Finding, `NATIVE_RUNTIME_VERIFIED` in Probe D)**: Probe D empirically proved that RapidFlow's exact NLF condition ($v\_nlf \ge u\_nlf$) prunes valid approximate matches. The plan was updated to explicitly ban exact NLF candidate filtering in P2/P3 and defer relaxed NLF to Phase P4.

### 13. Is D-G deferred or proposed for immediate implementation?
- **Strictly Deferred (`DEFER`)**: D-G ($k \ge \lambda(Q)$) requires dynamic S2 connectivity checks on partial and full mappings. Attempting D-G prematurely would jeopardize native D-R integration. D-R remains the exclusive active scope.

### 14. Which claims remain unproven and must not be asserted?
- No claim of speedup or memory efficiency over Python M1/M2.
- No claim that M2 outperforms M1 natively.
- No claim that RapidFlow-APX is ready for production NetFlow workloads.
- No claim that $W_{\text{M1}}/W_{\text{M2}} \approx 1.52$ represents proven search work reduction.

---

## 5. Classification of Core Decisions

| Decision Area | Proposed Action | Classification | Evidence Level | Rationale |
| :--- | :--- | :---: | :---: | :--- |
| **Core Graph CSR** | Reuse `graph.h`/`graph.cpp` for queries and initial snapshot | `KEEP` | `NATIVE_RUNTIME_VERIFIED` | Proven high-performance, compact memory, binary search neighbor access. |
| **Dynamic Adjacency** | Reuse `GlobalEdgeView` for mutable edge overlay | `KEEP` | `NATIVE_RUNTIME_VERIFIED` | High-performance sorted vector adjacency; tested in Probe C. |
| **Exact NLF Check** | Ban `nlf_check` from candidate filtering for $k \ge 1$ | `CHANGE` | `NATIVE_RUNTIME_VERIFIED` | Proven unsound in Probe D; prunes valid approximate embeddings. |
| **Leaf Count Shortcut** | Unroll candidate loop into explicit tuple binding | `CHANGE` | `SOURCE_INSPECTED` | CSM contract mandates explicit ordered tuple mappings. |
| **Automorphism Factorization** | For bare M2, disable scalar multiplication and post-hoc expansion; for reduced search, expand representative orbits | `CHANGE` | `SOURCE_INSPECTED` | Bare M2 finds ordered tuples directly; naive post-hoc expansion on full DFS causes 24x overcounting (576 on K4). |
| **Update Relevance Filtering** | Retain for search triggering; bypass for authoritative graph update | `ADAPT` | `SOURCE_INSPECTED` | Safe in D-R for search triggering, but graph state must remain authoritative. |
| **General Threshold D-G** | Defer until D-R native integration is complete | `DEFER` | `NOT_VERIFIED` | Premature complexity; requires dynamic connectivity checking. |
| **Standalone C++ Rewrite** | Reject standalone rewrite uncoupled from RapidFlow | `STOP` | `NOT_VERIFIED` | Violates core project mandate of genuine RapidFlow extension. |

---

## 6. Next Step Recommendation

Proceed to **Phase P2** (Issue formulation for Native Foundation & Tuple Materialization):
1. Lock Phase P2 issue scope to `extensions/rapidflow_apx/` foundation: `ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, and `ApxTupleSink`.
2. Do not code Phase P2 until Phase P0–P1 Draft PR is reviewed and merged by the user.
