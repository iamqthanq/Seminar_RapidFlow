# APX-03 — RapidFlow Integration & Architecture Audit (Gate A2)

**Document ID:** `docs/apx03_rapidflow_integration_audit.md`
**Status:** Approved Architectural Specification
**Associated Issue:** #18
**Target Integration Branch:** `QuangThang`
**RapidFlow Pinned Commit:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`

---

## 1. Executive Summary

The objective of APX-03 (Gate A2) is to construct the first **fully enumerable, C++ Approximate Continuous Subgraph Matching (CSM) baseline** demonstrably derived from native RapidFlow components.

This audit evaluates the codebase in `baselines/rapidflow/source/` pinned at `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` to determine:
1. Which native RapidFlow components can be directly compiled, linked, and reused at runtime.
2. Which components are candidates for future incremental optimization phases.
3. Which components are mathematically and semantically **unsound** for Approximate CSM under Model C.
4. Which components must be newly authored to satisfy the APX-01/APX-02 correctness specifications.

---

## 2. RapidFlow Core Data Structures: Graph & CSR Analysis

RapidFlow implements its graph representation in `baselines/rapidflow/source/graph/graph.h` and `baselines/rapidflow/source/graph/graph.cpp`.

### 2.1. Structural Layout
- **Vertex Count & Storage:** `vertices_count_` (type `ui` / `uint32_t`). Vertex labels are stored in `LabelID* vertex_labels_` indexed by dense vertex ID $0 \le v < \text{vertices\_count\_}$.
- **Adjacency Offsets (CSR):** `ui* offsets_` of size `vertices_count_ + 1`. The neighbors of vertex $v$ are stored contiguously in `VertexID* neighbors_` from `offsets_[v]` to `offsets_[v+1]`.
- **Edge Count & Directedness:** Stored as an undirected symmetric CSR where each undirected edge $\{u, v\}$ is stored twice ($(u, v)$ and $(v, u)$). `neighbors_` has capacity $2 \times \text{edges\_count\_}$.
- **Edge Labels:** When `is_edge_labeled` is `true`, `LabelID* edge_labels_` of size $2 \times \text{edges\_count\_}$ stores edge labels parallel to `neighbors_`.
- **Neighbor Access:** `getVertexNeighbors(id, count)` returns pointer `neighbors_ + offsets_[id]` and sets `count = offsets_[id+1] - offsets_[id]`.
- **Edge Existence & Label Query:** `checkEdgeExistence(u, v)` and `getEdgeLabelByVertex(u, v)` use binary search over the sorted neighbor array of the lower-degree endpoint ($O(\log \deg(\min(u, v)))$).
- **Reverse Label Index:** `BuildReverseIndex()` creates `reverse_index_` and `reverse_index_offsets_` to retrieve all vertices bearing a specific label in $O(1)$ time.

### 2.2. Architectural Constraints and Dense ID Assumptions
- **Dense ID Assumption:** RapidFlow allocates contiguous arrays of size `vertices_count_`. If an input graph has sparse IDs (e.g. vertices 0, 10, 1000), allocating raw RapidFlow `Graph` with `vertices_count_ = 1001` wastes memory or causes out-of-bounds access if vertex IDs are non-contiguous.
- **CSR Immutability:** RapidFlow's native CSR buffer is strictly immutable once constructed. Dynamic updates in RapidFlow's `StreamingEngine` are managed via external auxiliary structures (Global View / Local View / auxiliary maps) rather than modifying the CSR array in-place.
- **Handling in APX-03:** APX-03 directly compiles and links RapidFlow's `Graph` (`graph.cpp`) to represent query graphs and initial snapshots. To handle dynamic streaming updates safely without mutating the immutable CSR, APX-03 introduces a mutable graph layer that wraps RapidFlow's `Graph` and maintains an adjacency overlay with bidirectional vertex ID remapping to preserve dense array guarantees.

---

## 3. Analysis of RapidFlow Exact Mechanisms Unsafe for Approximate CSM

A central requirement of APX-03 is avoiding the naive fallacy:
$$\text{RapidFlow Exact Matches} \xrightarrow{\text{filter}} \text{Approximate Matches}$$
This approach is fundamentally defective because it cannot discover any match where $m(f) > 0$ (missing or mislabeled query edges). Furthermore, multiple components in RapidFlow's native `StreamingEngine` and `SearchEngine` are mathematically unsound for Model C:

### 3.1. Edge-Anchored Relevance Filtering (`StreamingEngine::execute`)
- **Native RapidFlow Mechanism:** In `streaming_engine.cpp:95-98`, RapidFlow checks `it = gvm_.get_mapped_views(label_triple)`. If the update's label triple $(L(u), L(e), L(v))$ does not match any edge in $Q$, the update is immediately discarded (`is_relevant_ = false`, no search executed).
- **Why Unsound for Model C:** In Model C, an edge update whose label matches *no* query edge can connect to an image vertex $f(u) \in S_f$, increasing the external boundary deviation $x_{\text{out}}(f)$ from $\varepsilon_x$ to $\varepsilon_x + 1$. This causes a previously accepted match to become invalid, triggering an **EXITED** event. Conversely, deleting such an edge reduces $x_{\text{out}}(f)$, triggering an **ENTERED** event. RapidFlow's edge-anchored filter would silently drop these updates, causing false negatives.

### 3.2. Degree and NLF (Neighborhood Label Frequency) Pruning
- **Native RapidFlow Mechanism:** RapidFlow filters candidate data vertices by requiring:
  $$\deg_G(v) \ge \deg_Q(u) \quad \text{and} \quad \text{NLF}_G(v, \ell) \ge \text{NLF}_Q(u, \ell)$$
- **Why Unsound for Model C:** When missing edges are allowed ($m(f) \le \varepsilon_m$ with $\varepsilon_m \ge 1$), a data vertex $v = f(u)$ may have incident edges absent in $G$. Thus, $\deg_G(v)$ may be strictly less than $\deg_Q(u)$, and its neighborhood label frequencies may be lower than in $Q$. Applying exact NLF/degree pruning would prune valid approximate embeddings.

### 3.3. Automorphism Quotienting and COUNT_ONLY Execution
- **Native RapidFlow Mechanism:** RapidFlow by default operates in `EXECUTION_MODE = 0` (`COUNT_ONLY`), multiplying intermediate results by symmetry factors (automorphism group size) rather than materializing distinct ordered mappings.
- **Why Unsound for Model C:** The CSM problem specification requires returning concrete **ordered injective mapping tuples** $(f(0), f(1), \dots, f(|V_Q|-1))$. Symmetries must be fully enumerated as distinct ordered tuples, not quotiented out or counted via scalar multipliers.

### 3.4. Local View Intersection Assumption
- **Native RapidFlow Mechanism:** `SearchEngine` intersects candidate lists based on exact edge existence between query vertices.
- **Why Unsound for Model C:** If edge $\{u_i, u_j\} \in E_Q$ is missing in $G$ (which is permitted up to $\varepsilon_m$ times), candidate sets cannot be intersected along that edge. Candidates must be explored via backtracking even when the edge is absent.

---

## 4. Component Classification Matrix

| Component | Origin | Status in APX-03 | Architectural Role & Justification |
| :--- | :--- | :--- | :--- |
| `Graph` (`graph.h`, `graph.cpp`) | Native RapidFlow C++ | **Direct Reuse (Compiled & Linked)** | Core Graph representation used for query topology, CSR neighbor lists, vertex/edge label lookups, and initial graph ingestion. |
| `configuration/types.h` | Native RapidFlow C++ | **Direct Reuse** | Pinned primitive typedefs (`VertexID`, `LabelID`, `ui`). |
| `utility/graphoperations.*` | Native RapidFlow C++ | **Direct Reuse / Reference** | File loading helpers and degree calculation utilities. |
| `GlobalViewManager` (`global_view_manager.*`) | Native RapidFlow C++ | **Unsafe for Model C** | Relies on exact query edge-anchor matching; dropped in baseline. |
| `LocalViewManager` (`local_view_manager.*`) | Native RapidFlow C++ | **Unsafe for Model C** | Exact edge-intersection candidate lists; dropped in baseline. |
| `SearchEngine` (`search_engine.*`) | Native RapidFlow C++ | **Unsafe for Model C** | Uses NLF/degree filtering and automorphism compression; dropped in baseline. |
| `StreamingEngine` (`streaming_engine.*`) | Native RapidFlow C++ | **Replaced by RECOMPUTE_BASELINE** | Discards non-query edge updates; incompatible with external boundary scoring. |
| `ApxMutableGraph` | **New APX-03 Layer** | **Newly Authored** | Encapsulates RapidFlow `Graph` with mutable edge overlay, bidirectional vertex ID remapping, and incident degree tracking. |
| `ApxBacktrackingMatcher` | **New APX-03 Layer** | **Newly Authored** | Exhaustive ordered injective backtracking with safe pruning ($m_{\text{partial}} > \varepsilon_m$), calculating $m, x_{\text{in}}, x_{\text{out}}$, connectivity, and Acceptance. |
| `ApxDirectEvaluator` | **New APX-03 Layer** | **Newly Authored** | Evaluates a single prescribed mapping tuple $(f(0), \dots, f(k-1))$ in $O(|E_Q| + \sum \deg)$ for large-scale microcases (e.g. Case 08). |
| `ApxDynamicRecomputeEngine` | **New APX-03 Layer** | **Newly Authored** | Implements the **RECOMPUTE_BASELINE** state machine, tracking $M_{t-1} \to M_t$ and emitting exact sets of `ENTERED`, `EXITED`, and `SCORE_CHANGED`. |
| `ApxJsonFormatter` | **New APX-03 Layer** | **Newly Authored** | Deterministic JSON/JSONL output generator for complete mapping tuples and delta events. |

---

## 5. Hard Architecture Gate Verification

To fulfill the mandatory architecture requirement:
> *"The new C++ executable must genuinely build/link and use at least one pinned original native RapidFlow C++ source component (prefer graph/CSR)."*

1. **Build Linking:** The CMake target `rapidflow_apx03_baseline` links `baselines/rapidflow/source/graph/graph.cpp` directly into the binary without copying or modifying the source file.
2. **Runtime Invocation:** `Graph::loadGraphFromMemory` and `Graph` accessors (`getVerticesCount()`, `getVertexLabel()`, `getVertexNeighbors()`, `checkEdgeExistence()`, `getEdgeLabelByVertex()`) are invoked directly during query loading and static graph construction.
3. **Pristine Preservation:** The entire directory `baselines/rapidflow/source/` remains strictly pinned at `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` and unmodified.
