# RapidFlow-APX Native Foundation Architecture & Design (Phase P2)

**Document ID:** `docs/rapidflow_apx_foundation_design.md`  
**Milestone:** D-INT-02 / Phase P2  
**Parent Issue:** Issue #30  
**Base Commit:** `8f6ebdacbf289e48e1c8a80a1c2e93720bcf633e`  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`  
**Status:** APPROVED ARCHITECTURE DESIGN  

---

## 1. Executive Summary & Architectural Scope

The goal of Phase P2 is to construct the **reusable native foundation** for RapidFlow-APX (`extensions/rapidflow_apx/`) that directly compiles, links, and operates on genuine upstream RapidFlow components. This foundation provides the four core modules required for the subsequent Phase P3 search kernels (native M1 and bare M2):

1. **`ApxTypedCodec`**: Bidirectional, lossless translation between arbitrary external signed 64-bit vertex IDs / typed labels (int64 vs string) and dense internal `uint32_t` IDs.
2. **`ApxAuthoritativeGraph`**: Two-tier dynamic graph combining RapidFlow's immutable CSR `Graph` (`graph.cpp`) for baseline snapshots ($G_0$) and mutable `GlobalEdgeView` overlays (`global_edge_view.h`) for streaming edge additions/deletions, with dynamic edge labels and deletion tombstones.
3. **`ApxTransactionManager`**: Transaction coordinator enforcing pre-validation, strict snapshot ordering (INSERT sees $G_{\text{after}}$, DELETE sees $G_{\text{before}}$), atomic rollback upon fault injection, and staged output publication.
4. **`ApxTupleSink`**: Transactional output sink materializing canonical ordered tuples $(f(u_1), \dots, f(u_n))$ sorted strictly by ascending original query vertex IDs, enforcing injectivity, and guarding against duplicate raw emissions.

---

## 2. Public API & Module Responsibilities

### 2.1. `ApxTypedCodec`
```cpp
namespace rapidflow_apx {

enum class LabelType : uint8_t { INT64 = 0, STRING = 1 };

struct TypedLabel {
    LabelType type;
    int64_t int_val{0};
    std::string str_val{};

    static TypedLabel from_int(int64_t val);
    static TypedLabel from_string(std::string val);
    bool operator==(const TypedLabel& other) const;
    bool operator!=(const TypedLabel& other) const;
};

class ApxTypedCodec {
public:
    ApxTypedCodec() = default;

    // Vertex ID Mapping
    uint32_t intern_query_vertex(int64_t external_id);
    uint32_t intern_data_vertex(int64_t external_id);
    int64_t decode_query_vertex(uint32_t dense_id) const;
    int64_t decode_data_vertex(uint32_t dense_id) const;
    bool has_query_vertex(int64_t external_id) const;
    bool has_data_vertex(int64_t external_id) const;

    // Typed Label Mapping (shared between Q and G)
    uint32_t intern_vertex_label(const TypedLabel& label);
    uint32_t intern_edge_label(const TypedLabel& label);
    TypedLabel decode_vertex_label(uint32_t internal_id) const;
    TypedLabel decode_edge_label(uint32_t internal_id) const;

    // Capacity Preflight Validation
    static void check_capacity_limits(size_t vertex_count, size_t edge_count);
};

} // namespace rapidflow_apx
```

### 2.2. `ApxAuthoritativeGraph`
```cpp
namespace rapidflow_apx {

struct EdgeLookupResult {
    bool exists{false};
    uint32_t label{0};
};

class ApxAuthoritativeGraph {
public:
    ApxAuthoritativeGraph() = default;
    ~ApxAuthoritativeGraph();

    // Baseline Ingestion via genuine RapidFlow CSR
    void load_baseline(
        const std::vector<std::pair<uint32_t, uint32_t>>& vertices,
        const std::vector<std::tuple<uint32_t, uint32_t, uint32_t>>& edges
    );

    // Topological & Label Queries
    bool has_vertex(uint32_t u) const;
    uint32_t get_vertex_label(uint32_t u) const;
    bool has_edge(uint32_t u, uint32_t v) const;
    EdgeLookupResult get_edge_label(uint32_t u, uint32_t v) const;
    std::vector<uint32_t> get_neighbors(uint32_t u) const;
    uint32_t get_degree(uint32_t u) const;
    uint32_t get_vertices_count() const;
    uint32_t get_edges_count() const;

    // Mutable Overlay Operations (Called by TransactionManager)
    bool apply_insert(uint32_t u, uint32_t v, uint32_t label);
    bool apply_delete(uint32_t u, uint32_t v, uint32_t label);

    // Snapshot / Checkpoint for Strong Exception Guarantee
    struct GraphOverlayCheckpoint {
        GlobalEdgeView fwd_overlay;
        GlobalEdgeView rev_overlay;
        std::unordered_map<uint64_t, uint32_t> overlay_labels;
        std::unordered_set<uint64_t> tombstones;
        uint32_t dynamic_edge_count;
    };
    GraphOverlayCheckpoint create_checkpoint() const;
    void restore_checkpoint(const GraphOverlayCheckpoint& cp);
};

} // namespace rapidflow_apx
```

### 2.3. `ApxTransactionManager`
```cpp
namespace rapidflow_apx {

enum class UpdateOp : char { INSERT = '+', DELETE = '-' };

struct UpdateOperation {
    UpdateOp op;
    int64_t ext_u;
    int64_t ext_v;
    TypedLabel label;
};

enum class StepStatus { SUCCESS, PREVALIDATION_FAILED, CALLBACK_ABORTED, FAULT_INJECTED };

struct StepResult {
    StepStatus status;
    std::string error_message;
    std::vector<std::vector<int64_t>> published_tuples;
};

using SearchCallback = std::function<bool(
    const ApxAuthoritativeGraph& graph_view,
    const UpdateOperation& update,
    uint32_t dense_u,
    uint32_t dense_v,
    uint32_t dense_label
)>;

class ApxTransactionManager {
public:
    ApxTransactionManager(ApxAuthoritativeGraph& graph, ApxTypedCodec& codec, ApxTupleSink& sink);

    StepResult execute_update(const UpdateOperation& update, SearchCallback callback);

    // Fault injection hooks for regression testing
    enum class FaultPoint { NONE, AFTER_MUTATION, DURING_CALLBACK, BEFORE_PUBLISH };
    void set_fault_injection(FaultPoint point);
};

} // namespace rapidflow_apx
```

### 2.4. `ApxTupleSink`
```cpp
namespace rapidflow_apx {

class ApxTupleSink {
public:
    ApxTupleSink(const ApxTypedCodec& codec, const std::vector<uint32_t>& query_vertex_order);

    // Staging Interface
    bool emit_tuple(const std::vector<uint32_t>& mapping_by_query_order);

    // Transaction Interface
    void start_step();
    std::vector<std::vector<int64_t>> commit_step();
    void abort_step();

    // Counters
    uint64_t raw_emissions() const;
    uint64_t unique_emissions() const;
    uint64_t committed_emissions() const;
    uint64_t discarded_emissions() const;
};

} // namespace rapidflow_apx
```

---

## 3. Object Ownership, Lifetime & Const Boundaries

```text
+---------------------------------------------------------------------------------+
|                                 Supervisor / Runner                             |
|                                                                                 |
|   owns:                                                                         |
|     - ApxTypedCodec                                                             |
|     - ApxAuthoritativeGraph                                                     |
|         owns: Graph* base_graph_ (RapidFlow CSR)                                |
|               GlobalEdgeView fwd_overlay_, rev_overlay_ (RapidFlow Relation)    |
|     - ApxTupleSink (references ApxTypedCodec)                                   |
|     - ApxTransactionManager (references Graph, Codec, TupleSink)                |
+---------------------------------------------------------------------------------+
                                      |
                                      | passes: const ApxAuthoritativeGraph&
                                      v
+---------------------------------------------------------------------------------+
|                             Search Engine / Callback                            |
|  - Read-only access to graph state                                              |
|  - Strictly forbidden from invoking graph mutations or nested transactions      |
|  - Emits candidate assignments to ApxTupleSink                                  |
+---------------------------------------------------------------------------------+
```

- **Const Boundaries**: During search callback execution, the search kernel receives a `const ApxAuthoritativeGraph&`. Mutation methods (`apply_insert`, `apply_delete`, `restore_checkpoint`) are strictly private or accessible only to `ApxTransactionManager`.
- **Lifetime**: The `Graph` CSR instance and `GlobalEdgeView` instances live as long as `ApxAuthoritativeGraph`. Checkpoints copy only the dynamic overlay and tombstones; the large baseline CSR graph is immutable and never copied.

---

## 4. RapidFlow Upstream Components Reused

| Component | Upstream Path | Role in RapidFlow-APX | Invocation Site |
| :--- | :--- | :--- | :--- |
| **`Graph`** | `graph/graph.cpp` | Ingestion and immutable CSR representation of $G_0$ and $Q$. | `ApxAuthoritativeGraph::load_baseline` calls `Graph::loadGraphFromMemory`. |
| **`checkEdgeExistence`** | `graph/graph.h:238` | Fast binary search edge existence on baseline snapshot. | Called in `ApxAuthoritativeGraph::has_edge` when edge is not in overlay or tombstones. |
| **`getEdgeLabelByVertex`** | `graph/graph.h:167` | Edge label retrieval from baseline CSR. | Called in `ApxAuthoritativeGraph::get_edge_label` for base edges. |
| **`getVertexNeighbors`** | `graph/graph.h:193` | Direct contiguous pointer access to baseline neighbor array. | Called in `ApxAuthoritativeGraph::get_neighbors` to stream CSR neighbors. |
| **`GlobalEdgeView`** | `utility/relation/global_edge_view.h` | Mutable dynamic adjacency lists maintaining sorted vectors via `std::lower_bound`. | `fwd_overlay_` and `rev_overlay_` in `ApxAuthoritativeGraph`. |

---

## 5. External vs Internal Spaces & Mapping Invariants

1. **Vertex IDs**:
   - **External**: Signed `int64_t` ($[-2^{63}, 2^{63}-1]$).
   - **Internal**: Unsigned dense `uint32_t` ($[0, |V|-1]$).
   - **Separation**: Query vertex IDs ($V_Q$) and Data vertex IDs ($V_G$) are maintained in separate mapping tables (`query_ext_to_dense_` and `data_ext_to_dense_`).
2. **Typed Labels**:
   - **External**: `TypedLabel` with explicit type discrimination (`INT64` vs `STRING`).
   - **Internal**: Unsigned dense `uint32_t` LabelID ($0, 1, 2, \dots$).
   - **Consistency**: The vertex-label intern dictionary is shared between $Q$ and $G$. The edge-label intern dictionary is shared between $Q$ and $G$.
   - **Type Safety**: Integer `0` and String `"0"` have different hash and equality keys, interned to distinct internal IDs.
3. **Capacity Limits**:
   - $|V| \le \text{UINT32\_MAX} - 1$
   - $|E| \le \text{UINT32\_MAX} / 2$ (ensuring $2|E|$ fits in `uint32_t` for directed adjacency).
   - Pre-validation fails closed with `CapacityExceededException` prior to allocation.

---

## 6. Graph Representation Invariants & Precedence Rules

For any vertex pair $(u, v)$ where $u < v$, let $k = (u \ll 32) \mid v$:

```text
       +-------------------------------------------------------+
       | Query has_edge(u, v) / get_edge_label(u, v)           |
       +-------------------------------------------------------+
                                  |
                                  v
                [ Is (u, v) present in overlay? ]
                   /                         \
                 YES                          NO
                 /                             \
     Return true, overlay_label           [ Is key in tombstones? ]
                                            /                  \
                                          YES                   NO
                                          /                      \
                                    Return false        [ Does CSR have edge? ]
                                                          /               \
                                                        YES                NO
                                                        /                   \
                                             Return true, CSR_label    Return false
```

- **Deletion of Base Edge**: Added to `tombstones_`. Base CSR is untouched.
- **Reinsertion of Base Edge (Same Label)**: Removed from `tombstones_`, added to overlay.
- **Reinsertion of Base Edge (Different Label)**: Removed from `tombstones_`, added to overlay with new label. Overlay label takes precedence over CSR.
- **Deletion of Reinserted Edge**: Removed from overlay; re-added to `tombstones_` so base CSR edge does not reappear.
- **Irrelevant Updates**: Updates with labels not appearing in $Q$ are accepted and stored in authoritative state, maintaining 100% graph fidelity.

---

## 7. Transaction State Machine & Rollback

```text
                       [ IDLE / COMMITTED ]
                                |
                                | Update received
                                v
                       [ PRE-VALIDATION ]
                       /                \
                   FAIL                  PASS
                   /                        \
         [ REJECT_UPDATE ]              [ STAGE MUTATIONS ]
         - Zero graph change            - Checkpoint saved
         - Zero output change           - INSERT: G_after prepared
                                        - DELETE: G_before retained
                                                |
                                                v
                                      [ EXECUTE CALLBACK ]
                                      /                  \
                                  EXCEPTION             SUCCESS
                                    /                      \
                        [ ROLLBACK STEP ]            [ COMMIT STEP ]
                        - Restore overlay            - DELETE: G_after finalized
                        - Drop pending tuples        - Publish pending tuples
                        - Re-throw / return FAIL     - Clear checkpoint
```

---

## 8. Tuple Canonicalization & Symmetry Policy

1. **Coordinate Order**: An embedding $f$ mapping query vertices to data vertices is represented as a tuple whose coordinates are ordered strictly by the **ascending numerical order of the original query vertex IDs**:
   $$T(f) = (f(u_1), f(u_2), \dots, f(u_n)) \quad \text{where } u_1 < u_2 < \dots < u_n$$
   The internal DFS exploration order (matching order) is strictly hidden.
2. **Exhaustive Bare M2 Policy**: Under the default bare M2 architecture, DFS explores all valid anchor orientations across the full unreduced query space, discovering each distinct ordered tuple directly.
   - **No Scalar Multiplication**: Never multiply counts by `automorphism_size`.
   - **No Post-hoc Expansion**: Never unconditionally expand embeddings by $|\operatorname{Aut}(Q)|$.
   - **No Quotienting**: Valid symmetric ordered embeddings are distinct elements and are preserved.
3. **Duplicate Detection Invariant**: If the search emits the exact same ordered tuple twice in the same batch, `ApxTupleSink` detects this as an invariant failure and aborts the transaction.

---

## 9. Micro Exact Matcher Scope (Gate P2)

- **Purpose**: Verify end-to-end integration of Codec $\to$ Authoritative Graph $\to$ Transaction Manager $\to$ Tuple Sink.
- **Constraint**: Operates strictly at $k = 0$ (exact matching). Rejects $k > 0$ with an explicit error.
- **Implementation**: Recursive backtracking search with candidate binding at all levels including leaves.
- **Validation**: Verified against independent Python M0 Oracle on $K_4$ and dynamic stream transitions.

---

## 10. Review Gate R2 Criteria

Phase P2 is verified and ready for Phase P3 design when:
1. All 4 native components compile into `librapidflow_apx_foundation.a` and pass all unit tests.
2. Micro exact matcher emits exactly 24 unique ordered tuples on $K_4$ matching M0 bit-for-bit.
3. Mid-stream transaction faults restore 100% of pre-step state and preserve prior commits.
4. Diamond $k=1$ fixed mapping is accepted without interference from exact NLF filtering.
5. All test cases in the locked manifest execute cleanly under strict resource limits.
