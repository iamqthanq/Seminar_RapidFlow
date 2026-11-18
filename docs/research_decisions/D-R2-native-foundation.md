# Research Decision Record: D-R2 — Native Foundation Verification & Architecture Review

**Document ID:** `docs/research_decisions/D-R2-native-foundation.md`
**Milestone:** D-INT-02 / Phase P2
**Parent Issue:** Issue #30
**Base Commit:** `8f6ebdacbf289e48e1c8a80a1c2e93720bcf633e`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Status:** READY_FOR_INDEPENDENT_CLOSURE_REVIEW (PENDING USER APPROVAL)

---

## 1. Executive Summary & Review Scope

During Phase P2 (Issue #30), we established and consolidated the native foundation for RapidFlow-APX, structured into 4 core C++ native components in `extensions/rapidflow_apx`:
1. `ApxTypedCodec`: Bidirectional mapping for signed 64-bit external vertex IDs (negative, zero, sparse, $>2^{53}$) and typed label intern dictionaries (integer `0` $\ne$ string `"0"`), equipped with zero-allocation `noexcept` checkpoint rollback.
2. `ApxAuthoritativeGraph`: Two-tier dynamic hybrid combining immutable RapidFlow CSR `Graph` (`graph.cpp`) for baseline snapshots ($G_0$) and mutable `GlobalEdgeView` (`global_edge_view.h`) overlays for dynamic streaming edge additions/deletions, with dynamic edge labels, shadow tombstones, and symmetric mutation rollback.
3. `ApxTransactionManager`: ACID transaction coordinator enforcing strict pre-validation, reentrancy prevention, snapshot sequencing (INSERT sees $G_{\text{after}}$, DELETE sees $G_{\text{before}}$), atomic multi-stage fault rollback, and staged output publication.
4. `ApxTupleSink`: Transactional output sink materializing canonical ordered tuples $(f(u_1), \dots, f(u_n))$ sorted strictly by ascending original query vertex IDs, enforcing injectivity, and guarding against duplicate raw emissions.

In addition, a minimal reference matcher (`MicroExactMatcher`) and fixed-mapping evaluator were provided to verify snapshot enumeration, dynamic deltas, and the D-R research contract ($0 \le k < \lambda(Q)$ and S2 spanning connectivity) without premature algorithm implementation.

---

## 2. Answers to Core Architectural Questions

### Q1: What is the architectural boundary between the authoritative graph and dynamic overlays?
- **Authoritative Graph Responsibility**: The authoritative graph (`ApxAuthoritativeGraph`) represents the sole ground-truth topological state of the graph. It owns the immutable baseline CSR graph (loaded via genuine RapidFlow `Graph::loadGraphFromMemory`) and maintains dynamic additions and deletions via forward and reverse `GlobalEdgeView` overlays, accompanied by dynamic edge label dictionaries and deletion tombstones.
- **Query Resolution Precedence**:
  1. *Dynamic Overlay*: If edge $(u, v)$ is present in `overlay_labels_`, it returns `true` with the overlay label.
  2. *Tombstones*: If edge $(u, v)$ is present in `tombstones_`, it returns `false` (masked out from baseline CSR).
  3. *Baseline CSR*: If edge $(u, v)$ exists in the baseline `Graph`, it returns `true` with the baseline label.
- **Decoupling from Search Indexes**: Production search indexes, NLF filters, candidate sets, and future dynamic pruning structures ($\delta'$) do not mutate the authoritative graph directly. They consume read-only snapshots through `ApxAuthoritativeGraph` views during transactional search callbacks.

### Q2: How is transaction atomicity and strong exception safety guaranteed?
- **Reentrancy Protection**: `ApxTransactionManager` maintains an atomic/RAII-guarded reentrancy flag (`is_in_transaction_`). Nested calls to `execute_update` (e.g. from inside a search callback) are rejected immediately with `StepStatus::REENTRANCY_BLOCKED`.
- **RAII Transaction Scope**: All transaction state transitions are wrapped in `TransactionScope`. Any scope exit without an explicit `commit()` (whether due to prevalidation failure, mutation failure, callback returning false, or any thrown exception such as `std::bad_alloc`) automatically triggers noexcept zero-allocation rollback before error string creation or exception propagation.
- **True Zero-Allocation Rollback (`noexcept`)**:
  - In `ApxTypedCodec`: check-point rollback pops newly added vector entries and erases them from hash tables (`std::vector::pop_back` and `std::unordered_map::erase`), which allocate zero memory and are strictly `noexcept`. Furthermore, all four `intern_*` methods maintain an exact lockstep: if vector insertion fails, the catch block immediately erases the inserted map entry, completely preventing codec ghost entries.
  - In `ApxAuthoritativeGraph`: rollback uses in-place mutation journal records and overlay removal (`GlobalEdgeView::remove` and `unordered_map::erase`). Container copy-assignment (`restore_checkpoint`) has been completely removed from the update path, ensuring that rollback never performs heap allocations.
- **Symmetric Mutations & Exception Boundaries**: In `apply_insert`, forward and reverse edges are inserted with strong exception handling. If reverse insertion fails or throws `std::bad_alloc`, forward edges are symmetrically erased, preventing topological leaks.
- **Output Staging & Clear on Abort**: Output tuples are buffered in `ApxTupleSink::pending_tuples_`. On any abort (including allocation failure during callback or error reporting), pending tuples are purged without affecting prior committed batches. Manager remains completely clean and ready to process subsequent valid transactions.

### Q3: How does the fixed-mapping evaluator enforce the D-R research contract?
- **Theoretical Domain Guard**: The D-R contract mandates $0 \le k < \lambda(Q)$, where $\lambda(Q)$ is the global edge connectivity of query graph $Q$. If $k \ge \lambda(Q)$, the approximation threshold could allow disconnected query components.
- **Exact Edge Connectivity**: `MicroExactMatcher::compute_edge_connectivity` computes exact $\lambda(Q)$ on the undirected query graph via min $s$-$t$ cut flow passes. If caller metadata claims a different $\lambda$, or if $k \ge \lambda(Q)$, the evaluator immediately rejects the mapping with `is_valid = false` and an explicit diagnostic message.
- **S2 Spanning Connectivity**: The evaluator constructs the subgraph of matched query edges $(V_Q, E_{\text{matched}})$. Using BFS, it strictly verifies that the matched edges form a single connected component that spans all $|V_Q|$ query vertices. Disconnected matchings or isolated query vertices are rejected.

### Q4: What is the evidence and provenance model for Phase P2?
- **Git Provenance**: `scripts/run_rapidflow_apx_foundation.py` validates fail-closed that repository HEAD matches the commit under evaluation and that upstream RapidFlow (`baselines/rapidflow/source/`) is pristine at commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`.
- **Preflight Host Resources**: Host system is checked for $\ge 2$ GiB available RAM (parsed from `/proc/meminfo` `MemAvailable`) and $\ge 1$ GiB free disk space.
- **Process Containment**: Build is constrained to $\le 120$s, 1024 MiB `RLIMIT_AS`, and `-j2`. Runtime cases are constrained to $\le 30$s, 512 MiB `RLIMIT_AS`, and process group SIGKILL on timeout.
- **Bit-for-Bit Independent M0 Oracle Cross-Validation**:
  - $K_4$ exhaustive enumeration (24 tuples) is validated bit-for-bit against `scripts.dstar_oracle.enumerate_snapshot(Q, G, 0)`.
  - Dynamic event transitions (24 ENTERED, 24 EXITED) are validated bit-for-bit against `scripts.dstar_oracle.DStarDynamicOracle`.
  - There is zero scalar count multiplication (`count * 24`); all tuples are explicitly materialized and verified.
- **Diagnostic Mode Isolation**: Subset runs via `--case <id>` write to `reports/rapidflow_apx_foundation/diagnostic_summary.json` with status `DIAGNOSTIC_SUBSET_RUN`, preventing premature claims or overwriting of the canonical summary.

---

## 3. Decisions Table (KEEP / CHANGE / DEFER / STOP)

| Decision Item | Action | Justification & Verification Evidence |
| :--- | :--- | :--- |
| **Two-tier Authoritative Graph** | **KEEP** | Hybrid CSR + `GlobalEdgeView` overlay + tombstones passed all lifecycle and mutation stream tests with 100% accuracy. |
| **ApxTypedCodec 64-bit & Typed Labels** | **KEEP** | Verified roundtrip across negative, sparse, $>2^{53}$ IDs, typed label isolation (`0` vs `"0"`), and zero-allocation rollback. |
| **Transaction Snapshot Sequencing** | **KEEP** | INSERT sees $G_{\text{after}}$, DELETE sees $G_{\text{before}}$ verified on dynamic transitions emitting 24 ENTERED / 24 EXITED tuples. |
| **Zero-Allocation Rollback & Reentrancy** | **KEEP** | Reentrancy guard, codec checkpointing, and symmetric overlay rollback verified under simulated bad_alloc and fault injection. |
| **Ascending Query Order Canonicalization** | **KEEP** | Decouples internal DFS exploration order from external representation; matches M0 Oracle bit-for-bit. |
| **Explicit Witness Tuple Materialization** | **KEEP** | All 24 tuples materialized and cross-checked against M0 Oracle; count-only equivalence is strictly rejected. |
| **Strict D-R Guard & S2 Spanning Check** | **KEEP** | Computes $\lambda(Q)$, enforces $0 \le k < \lambda(Q)$, and validates matched query edge spanning connectivity. |
| **Production M1 / Bare M2 Search Engine** | **DEFER** | Deferred to Phase P3 as specified in project milestone roadmap. |
| **Dynamic Pruning $\delta'$ & Approximate NLF** | **DEFER** | Deferred to Phase P4. |
| **NetFlow Large-Scale Benchmarks** | **DEFER** | Deferred to Phase P5. |
| **Scalar Count Multiplication** | **STOP** | Never multiply match counts by $|\operatorname{Aut}(Q)|$; all symmetric embeddings must be explicitly enumerated. |
| **Calling Reference Matcher "Bare M2"** | **STOP** | Distinguish minimal foundation verification matcher (`MicroExactMatcher`) from Phase P3 bare M2 engine. |
| **Premature Closure Claims** | **STOP** | Require user review and approval before declaring milestone closed or advancing to P3. |

---

## 4. Status and Recommendations

All findings P2-F01 through P2-F08 have been addressed in this consolidated remediation pass. The codebase, unit tests, and canonical runner pass 100%. This document and associated evidence are submitted for user review and approval to merge Draft PR #31 into `QuangThang`.
