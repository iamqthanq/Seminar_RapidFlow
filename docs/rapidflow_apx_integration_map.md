# RapidFlow-APX Source Code Audit & Integration Map (Gate P1)

**Document ID:** `docs/rapidflow_apx_integration_map.md`
**Date:** 2026-11-18
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D
**Associated Issue:** Issue #32 (`D-INT-03 / P3`; previously Issue #28 `P0–P1`, Issue #30 `P2` merged)
**Current Status:** Phase P2 merged into `QuangThang` (PR #31), Phase P3 completed & verified (`M1-enum` and `bare M2` native engines; Issue #32 / PR #33 — Ready for Independent Review)
**Pinned Upstream Commit:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)

---

## 1. Executive Summary

This document presents a granular, line-by-line architectural audit of the pinned RapidFlow source code (`baselines/rapidflow/source/`). Each critical function and data structure is evaluated against the requirements of **Approximate Continuous Subgraph Matching (APX CSM)** under **Edge Recall** and **S2 connectivity** ($0 \le k < \lambda(Q)$).

The audit establishes:
1. Exact line numbers, verified directly from the pinned checkout.
2. The exact operational semantics in original RapidFlow.
3. The specific semantic hazards and failure modes when applied to approximate matching.
4. Proposed adaptation strategies and target native modules for Phase P2.
5. Empirical evidence levels, verification probes, and remaining unknowns.

---

## 2. Granular Source Code Integration Matrix

| Upstream SHA | Source Path | Symbol / Function | Line Range | Verified Excerpt / Hash | Role in RapidFlow | Exact Behavior | APX Hazard / Incompatibility | Action | Target Derived Module | Linked Probe / Test | Evidence Level | Cost & Unknowns |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- | :--- | :---: | :--- | :--- | :---: | :--- |
| `2ea4f4c` | `graph/graph.h` | `checkEdgeExistence(u, v)` | 238–259 | Binary search over sorted neighbor array of $\min(\deg(u), \deg(v))$ | Fast edge presence check on static CSR | Returns boolean existence in initial snapshot | Does not reflect dynamic stream insertions or deletions (CSR is static and immutable) | `ADAPT` | `ApxAuthoritativeGraph::has_edge` | Probe A, Probe B | `CODE_AND_RUNTIME_VERIFIED` | $O(\log \deg)$ lookup. Must combine CSR check with mutable overlay tombstone/insertion check. |
| `2ea4f4c` | `graph/graph.h` | `getEdgeLabelByVertex(u, v)` | 167–191 | Binary search; returns `getEdgeLabelByLocalOffset(v, mid)` | Edge label retrieval | Returns label in static CSR or $\infty$ if absent | CSR edge labels are immutable; cannot reflect updated or newly inserted edge labels | `ADAPT` | `ApxAuthoritativeGraph::get_edge_label` | Probe A, Probe B | `CODE_AND_RUNTIME_VERIFIED` | Edge labels must be retrieved from dynamic overlay if modified. |
| `2ea4f4c` | `graph/graph.h` | `getVertexNeighbors(id, count)` | 193–196 | `count = offsets_[id+1] - offsets_[id]; return neighbors_ + offsets_[id];` | Contiguous neighbor array access | Returns raw pointer to sorted CSR neighbor array | Pointer does not include stream-inserted neighbors and includes deleted neighbors | `ADAPT` | `ApxAuthoritativeGraph::iterate_neighbors` | Probe A, Probe C | `CODE_AND_RUNTIME_VERIFIED` | Iteration must merge CSR sorted neighbors with `GlobalEdgeView` sorted overlay while filtering tombstones. |
| `2ea4f4c` | `graph/graph.cpp` | `Graph::loadGraphFromMemory` | 87–177 | `offsets_ = new uint32_t[vertices_count_ + 1]; neighbors_ = new VertexID[edges_count_ * 2];` | Memory-based graph ingestion | Allocates dense contiguous arrays; sorts neighbors | Assumes strictly dense IDs $0 \le v < |V|$; non-contiguous IDs cause memory waste or out-of-bounds segfault | `ADAPT` | `ApxTypedCodec` + `loadGraphFromMemory` | Probe A, Probe B | `CODE_AND_RUNTIME_VERIFIED` | Requires pre-mapping external IDs to dense internal indices $[0, n-1]$. |
| `2ea4f4c` | `utility/relation/global_edge_view.h` | `GlobalEdgeView::insert(u, v)` | 24–41 | `auto lb = lower_bound(it->second.begin(), it->second.end(), v); if (*lb != v) it->second.insert(lb, v);` | Dynamic adjacency insertion | Inserts $v$ maintaining sorted order; returns `true` if new, `false` if duplicate | Does NOT store edge labels; vector reallocation invalidates all existing pointers/iterators | `KEEP` / `ADAPT` | `ApxMutableOverlay` | Probe C | `CODE_AND_RUNTIME_VERIFIED` | Fast $O(\log \deg + \deg)$ insertion. Edge labels must be maintained in parallel map or key adapter. |
| `2ea4f4c` | `utility/relation/global_edge_view.h` | `GlobalEdgeView::remove(u, v)` | 43–55 | `auto lb = lower_bound(iter->second.begin(), iter->second.end(), v); if (*lb == v) iter->second.erase(lb);` | Dynamic adjacency deletion | Erases $v$; decrements cardinality; returns `true` if found, `false` if absent | Container mutation invalidates iterators; does not verify edge label compatibility | `KEEP` / `ADAPT` | `ApxMutableOverlay` | Probe C | `CODE_AND_RUNTIME_VERIFIED` | Erase cost $O(\deg)$ due to vector shift. Fine for bounded micro-benchmarks; evaluate memory pooling in P4. |
| `2ea4f4c` | `streaming/global_view_manager.h` | `GlobalViewManager::nlf_check(u, v)` | 80–87 | `for (auto &kv : u_nlf) if (v_nlf[kv.first] < kv.second) return false;` | Exact candidate pruning | Prunes candidate $v$ if data vertex has fewer incident labels than query vertex $u$ | **CRITICAL UNSOUND HAZARD**: Valid APX embeddings permit missing query edges; pruning when $v\_nlf < u\_nlf$ discards true matches | `REPLACE` | `ApxRelaxedNlfCheck` (Tolerance $k$) | Probe D | `NATIVE_RUNTIME_VERIFIED` | Counterexample proven in Probe D: Diamond query with missing edge $BC$ pruned by exact NLF on genuine GlobalViewManager. |
| `2ea4f4c` | `streaming/global_view_manager.cpp` | `GlobalViewManager::create_views` | 59–184 | Lines 135–144: `if (nlf_check(u, v)) candidates_[u][v] = encoded_id;` | Global view initialization | Builds raw views AND populates `candidates_` using exact `nlf_check` | Populates candidate sets only with exact matches; approximate matches excluded from candidate sets | `ADAPT` (Views) / `REPLACE` (NLF) | `ApxGlobalViewManager` | Probe D | `NATIVE_RUNTIME_VERIFIED` | Raw edge view indexing (`views_`) is sound; candidate filtering subsystem must be bypassed or relaxed. |
| `2ea4f4c` | `streaming/streaming_engine.cpp` | `StreamingEngine::execute` | 143–157 & 210–230 | `if (update.op_ == '+') { ... } else { ... }` | Streaming update sequencing | INSERT: mutate views $\to$ search; DELETE: search $\to$ mutate views | **Snapshot order is mathematically correct for CSM**: INSERT on $G_{\text{after}}$, DELETE on $G_{\text{before}}$. But relevance check drops non-query edges without updating authoritative state! | `ADAPT` | `ApxStreamingEngine::process_update` | Probe E | `SOURCE_INSPECTED` / `NEW_AUDIT_ADAPTER_RUNTIME` | Keep snapshot sequencing; decouple authoritative graph update from query relevance filtering; add rollback (verified on adapter). |
| `2ea4f4c` | `streaming/streaming_engine.cpp` | `StreamingEngine::execute` | 95–98 | `auto it = gvm_.get_mapped_views(label_triple); if (it != nullptr) ... else is_relevant_ = false;` | Relevance filtering | Drops updates whose label triple does not match any query edge | Sound in D-R for search triggering because non-query edges cannot anchor matches or change recall, BUT authoritative graph state must still be maintained | `ADAPT` | `ApxStreamingEngine::execute` | Probe E | `SOURCE_INSPECTED` | Relevance filtering is safe for triggering search in D-R, but graph state must always record the update. |
| `2ea4f4c` | `streaming/search_engine.cpp` | `SearchEngine::search_on_reduced_query` | 160–169 | `} else if (next_depth == target_depth - 1) { result_count += local_idx_[next_depth].second;` | Leaf-level candidate processing | Scalar addition of candidate count; skips candidate instantiation | **OUTPUT HAZARD**: Never instantiates embedding tuples; cannot emit ordered mapping sets | `REPLACE` | `ApxTupleSearchEngine` | Probe F | `SOURCE_INSPECTED` | Must unroll candidate loop: iterate over candidates, bind to `embedding_[u]`, emit canonical tuple. |
| `2ea4f4c` | `streaming/streaming_engine.cpp` | `StreamingEngine::execute` | 143 & 181 | `result_count += local_result_count * automorphism_size;` | Automorphism factor multiplication | Multiplies count by `automorphism_size` (directed edge orbit size, 12 on $K_4$, not $|\operatorname{Aut}(K_4)|=24$) on reduced query search | **FATAL OVERCOUNTING HAZARD**: On full DFS, post-hoc multiplication/expansion causes gross overcounting (576 on K4). Bare M2 requires no post-hoc multiplication or expansion. | `REPLACE` | `ApxTupleSink` / `ApxAutomorphismExpander` | Probe F | `SOURCE_INSPECTED` | Bare M2 enumerates tuples directly; reduced search requires representative orbit expansion. |
| `2ea4f4c` | `streaming/order_manager.cpp` | `OrderManager::detect_automorphism_edges` | 51–76 | Partitions query edges into automorphism orbits | Identifies symmetric query edges and picks canonical representative | Designed to reduce query for counting | Can be adapted to generate full automorphism group generators for representative tuple expansion | `ADAPT` | `ApxAutomorphismManager` | Probe F | `SOURCE_INSPECTED` | Partitions edges into orbits; search is performed only on representative orders. |
| `2ea4f4c` | `streaming/local_view_manager.cpp` | `LocalViewManager::create_view` | 14–80 | Intersects candidate sets along all query edges | Local view candidate intersection | Assumes every query edge exists in $G$; empty intersection if edge is absent | Fails when query edge is missing in approximate embedding ($e \in R(f)$); prunes valid approximate matches | `REPLACE` (P2) / `DEFER` (P4) | Direct M2 DFS (P2) / Relaxed Local View (P4) | Probe D | `SOURCE_INSPECTED` | Bare M2 DFS bypasses LocalViewManager by branching on PRESENT / MISSING edges. |

---

## 3. Re-Evaluation of Historical Model C Audit

The earlier Model C audit (`docs/apx03_rapidflow_integration_audit.md`) marked several RapidFlow mechanisms as "fundamentally unsafe". A critical reassessment under Direction D reveals:

1. **Relevance Filtering (`get_mapped_views`)**:
   - *Model C Verdict*: UNSAFE. In Model C, an update on an edge not matching any query edge could connect to an image vertex, increasing external cut edges $x_{\text{out}}$ beyond $\varepsilon_x$ and causing an `EXITED` event.
   - *Direction D Verdict*: **SAFE FOR EVENT ANCHORING**. Under Edge Recall, external edges are unpenalized. An update that matches no query edge can never form part of a new match, nor can it invalidate an existing match. Therefore, relevance filtering at the search-triggering level is mathematically sound in D-R.
   - *Crucial Caveat*: The edge must still be inserted/deleted in the **authoritative graph state** so subsequent edge lookups remain consistent.

2. **Degree and NLF Pruning**:
   - *Model C Verdict*: UNSAFE.
   - *Direction D Verdict*: **REMAINS UNSAFE IN EXACT FORM**. As verified in Probe D, requiring $\deg_G(v) \ge \deg_Q(u)$ or $\text{NLF}_G(v) \ge \text{NLF}_Q(u)$ discards valid approximate matches with missing edges. It must be replaced by a relaxed tolerance condition ($v\_nlf \ge u\_nlf - k$).

3. **Leaf Count Shortcut & Automorphism Factorization**:
   - *Model C & Direction D Verdict*: **UNANIMOUSLY UNSOUND FOR ENUMERATION**. Both contracts require explicit tuple materialization. The scalar multiplication and leaf-count shortcuts in RapidFlow must be replaced with explicit candidate binding and permutation orbit expansion.

---

## 4. APX-03 Heritage and Reuse Boundaries

The C++ codebase in `extensions/apx03/` was developed under Model C as a recompute baseline (`RECOMPUTE_BASELINE`):
- **Positive Heritage to Reuse**:
  1. CMake compilation and linkage of `baselines/rapidflow/source/graph/graph.cpp`.
  2. The bidirectional sparse-to-dense ID mapping pattern.
  3. Structured JSON/JSONL tuple serialization formatting.
  4. Memory-based graph ingestion utilities.
- **Components Rejected for Direction D**:
  1. Full snapshot recompute (`ApxDynamicRecomputeEngine`): recomputes $M(G_t)$ from scratch on every step, violating the incremental continuous matching mandate.
  2. Core/boundary scoring logic (`ApxBacktrackingMatcher`): bound to Model C parameters ($\varepsilon_m, \varepsilon_x$, S1 data-induced connectivity).
