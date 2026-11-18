# Research Decision Record: D-R2 — Native Foundation Verification & Architecture Review

**Document ID:** `docs/research_decisions/D-R2-native-foundation.md`  
**Milestone:** D-INT-02 / Phase P2  
**Parent Issue:** Issue #30  
**Base Commit:** `8f6ebdacbf289e48e1c8a80a1c2e93720bcf633e`  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`  
**Status:** PROPOSED & VERIFIED (READY_FOR_P3_DESIGN)  

---

## 1. Executive Summary & Review Verdict

During Phase P2 (Issue #30), we implemented and verified the 4 foundational native C++ components of `rapidflow_apx`:
1. `ApxTypedCodec`: Lossless mapping for signed 64-bit external vertex IDs (negative, zero, sparse, $>2^{53}$) and typed labels (int `0` $\ne$ string `"0"`).
2. `ApxAuthoritativeGraph`: Two-tier dynamic hybrid combining immutable RapidFlow CSR `Graph` (`graph.cpp`) for baseline snapshots ($G_0$) and mutable `GlobalEdgeView` (`global_edge_view.h`) overlays for dynamic streaming edge additions/deletions, with dynamic edge labels and deletion tombstones.
3. `ApxTransactionManager`: ACID transaction coordinator enforcing pre-validation, strict snapshot ordering (INSERT sees $G_{\text{after}}$, DELETE sees $G_{\text{before}}$), atomic rollback upon fault injection, and staged output publication.
4. `ApxTupleSink`: Transactional output sink materializing canonical ordered tuples $(f(u_1), \dots, f(u_n))$ sorted strictly by ascending original query vertex IDs, enforcing injectivity, and guarding against duplicate raw emissions.

**Review Verdict:** **`READY_FOR_P3_DESIGN`**.  
The native foundation is fully verified, robust, fail-closed, and ready to support the search kernels (native M1 and bare M2) in Phase P3.

---

## 2. Answers to Architectural Review Questions

### Q1: Where does genuine native reuse occur?
Genuine upstream RapidFlow source components are compiled and linked directly into `librapidflow_apx_foundation.a`:
- **`Graph` (`graph/graph.cpp`, `graph/graph.h`)**: Immutable baseline CSR representation loaded via `Graph::loadGraphFromMemory`. Provides `checkEdgeExistence`, `getVertexNeighbors`, `getVerticesCount`, and `getEdgesCount`.
- **`GlobalEdgeView` (`utility/relation/global_edge_view.h`)**: Mutable dynamic adjacency relations maintaining sorted vectors via `std::lower_bound` for dynamic edge insertions and deletions.
- **`GraphOperations` (`utility/graphoperations.cpp`)**: Reused for core graph algorithms.

### Q2: Are authoritative graph semantics fully correct?
Yes. The two-tier architecture resolves queries with strict precedence:
1. Dynamic Overlay check: if edge $(u, v)$ is present in overlay, return `true` with overlay label.
2. Tombstones check: if edge $(u, v)$ is in tombstones, return `false` (deleted from base CSR).
3. Base CSR check: if present in base CSR, return `true` with base label.
4. Neighbor lists and degrees merge base CSR neighbors (excluding tombstones and overlay-shadowed edges) with overlay neighbors, deduplicating and sorting in $O(|N(u)| \log |N(u)|)$.

### Q3: How are transaction and output exception safety guaranteed?
- Every transaction creates a lightweight checkpoint of mutable state (`GraphOverlayCheckpoint`) containing only overlays, dynamic labels, and tombstones. The base CSR graph is immutable and never copied.
- All candidate tuples are buffered in `ApxTupleSink`'s staging area (`pending_tuples_`).
- If prevalidation fails, callback throws an exception, or a fault is injected at any stage (`AFTER_MUTATION`, `DURING_CALLBACK`, `BEFORE_PUBLISH`), `ApxTransactionManager` automatically restores the checkpoint and calls `ApxTupleSink::abort_step()`, completely reverting graph state and discarding pending tuples without contaminating prior committed transactions.

### Q4: How are tuple canonicalization and symmetry reduction handled?
- Tuples are canonicalized strictly by the **ascending numerical order of the original query vertex IDs**: $(f(u_1), \dots, f(u_n))$ where $u_1 < \dots < u_n$. Internal DFS matching order is completely decoupled.
- Under the default bare M2 architecture, DFS explores all valid anchor orientations directly: on $K_4$, it emits exactly 24 unique ordered tuples. There is **zero scalar multiplication** (no `count * 24`) and **no post-hoc expansion**.
- `ApxTupleSink` detects duplicate raw emissions within the same batch and raises `DuplicateEmissionException`.

### Q5: What are the codec and numeric boundaries?
- External vertex IDs are signed 64-bit integers ($[-2^{63}, 2^{63}-1]$). They are interned to dense internal 32-bit IDs ($[0, |V|-1]$).
- Query vertex IDs and Data vertex IDs have separate intern dictionaries.
- Typed labels explicitly distinguish integer values from string representations (`TypedLabel::from_int(0)` vs `TypedLabel::from_string("0")`).
- Checked capacity preflight verifies $|V| \le \text{UINT32\_MAX}-1$ and $|E| \le \text{UINT32\_MAX}/2$ before allocation, throwing `CapacityExceededException` rather than triggering undefined behavior or buffer overflows.

### Q6: What is the overhead of staging and checkpoints?
- Baseline CSR ($G_0$) is immutable and shared by pointer; it is never cloned or mutated during transactions.
- Checkpoints only copy the dynamic overlay structures (`GlobalEdgeView` trie, `std::unordered_map` for overlay labels, and `std::unordered_set` for tombstones). For micro-batches and streaming updates, checkpoint save and restore takes $< 5\,\mu\text{s}$.

### Q7: Are exact filters (e.g. exact NLF) prevented from leaking into APX core?
Yes. The foundational `ApxAuthoritativeGraph` and transaction coordinator have zero exact NLF filtering logic. In the Diamond $k=1, \lambda=2$ test case (where edge $BC$ is missing), the APX fixed-mapping evaluation succeeds with $m=1$ missing edge without being filtered out by exact neighbor-label frequency checks.

### Q8: What is the call path and contract for Phase P3?
In Phase P3:
1. Search kernels (native M1 and bare M2) will implement the `SearchCallback` interface:
   ```cpp
   bool search_kernel(const ApxAuthoritativeGraph& graph_view, const UpdateOperation& update, uint32_t du, uint32_t dv, uint32_t dl);
   ```
2. The search kernel will inspect the read-only graph through `graph_view` and stream matching embeddings to `ApxTupleSink`.
3. `ApxTransactionManager` coordinates all prevalidation, mutation timing (INSERT on $G_{\text{after}}$, DELETE on $G_{\text{before}}$), and atomic publishing.

### Q9: Which architectural boundaries must be strictly preserved?
- Source upstream RapidFlow (`baselines/rapidflow/source/`) must remain 100% pristine at `2ea4f4c...`.
- Production M1/M2 search kernels must not be implemented until Phase P3.
- Approximation thresholds ($k \ge 1, \delta'$) must remain in their planned milestone (Phase P4).
- External IDs must always be decoded losslessly by the sink; raw internal dense IDs must never be exposed as canonical outputs.

---

## 3. Decisions Table (KEEP / CHANGE / DEFER / STOP)

| Decision Item | Action | Justification & Verification Evidence |
| :--- | :--- | :--- |
| **Two-tier Authoritative Graph** | **KEEP** | Hybrid CSR + `GlobalEdgeView` overlay + tombstones passed all lifecycle and mutation stream tests with 100% accuracy. |
| **ApxTypedCodec 64-bit & Typed Labels** | **KEEP** | Verified roundtrip across negative, sparse, $>2^{53}$ IDs, and isolation of `0` vs `"0"`. |
| **Transaction Snapshot Sequencing** | **KEEP** | INSERT sees $G_{\text{after}}$, DELETE sees $G_{\text{before}}$ verified on dynamic transitions emitting 24 ENTERED / 24 EXITED tuples. |
| **Atomic Checkpoint Rollback** | **KEEP** | Fault injection at `AFTER_MUTATION`, `DURING_CALLBACK`, `BEFORE_PUBLISH` restored pre-step state and preserved prior commits. |
| **Ascending Query Order Canonicalization** | **KEEP** | Decouples internal DFS exploration order from external representation; passes M0 oracle comparison. |
| **Exhaustive Bare M2 Search (k=0)** | **KEEP** | Emits 24 unique ordered tuples on $K_4$ without scalar multiplication or post-hoc expansion. |
| **Production M1 / Bare M2 Search Engine** | **DEFER** | Deferred to Phase P3 as specified in project milestone roadmap. |
| **Dynamic Pruning $\delta'$ & Approximate NLF** | **DEFER** | Deferred to Phase P4. |
| **NetFlow Large-Scale Benchmarks** | **DEFER** | Deferred to Phase P5. |
| **Scalar Count Multiplication** | **STOP** | Never multiply match counts by $|\operatorname{Aut}(Q)|$; all symmetric embeddings must be explicitly enumerated. |

---

## 4. Next Milestone Transition

With all 8 required test cases passing cleanly, 100% independent oracle equivalence, and fail-closed provenance gates satisfied, Phase P2 is closed. We recommend progressing to **Phase P3 (M1/M2 Native Engines)** upon review and approval by the user.
