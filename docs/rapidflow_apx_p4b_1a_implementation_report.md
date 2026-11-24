# RapidFlow-APX Phase P4b-1A Implementation & Verification Report

**Document ID:** `docs/rapidflow_apx_p4b_1a_implementation_report.md`
**Milestone:** Phase P4b-1A — Native Transactional NLF Frequency Index & Sound Static Relaxed NLF Implementation
**Active Issue:** [Issue #38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38)
**Draft PR:** [Draft PR #39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39)
**Parent Roadmap:** [Issue #1](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)
**Repository:** `https://github.com/iamqthanq/Seminar_RapidFlow`
**Active Worktree:** `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B`
**Feature Branch:** `feature/rf38-p4b-optimization`
**Integration Target:** `QuangThang`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `True`)
**Historical P4a Evidence Integrity:** `7 / 7` artifacts verified bit-for-bit against `docs/evidence/p4a_evidence_manifest.json`
**Handoff Status:** **READY_FOR_INDEPENDENT_IMPLEMENTATION_REVIEW**

---

## 1. Executive Summary & Architectural Traceability

Phase P4b-1A delivers the production native C++ implementation of the **Transactional Neighborhood Label Frequency (NLF) Frequency Index** and the mathematically sound **Static Relaxed NLF Pruning Filter** for the M2 Event Engine. This implementation strictly realizes the formal specifications, invariant definitions, and rollback semantics defined in the accepted P4b-0 R1.1 design document (`docs/rapidflow_apx_p4b_design.md`) and hardens all eight findings identified in the Independent Implementation Review R1.

### 1.1 Core Objectives Achieved
1. **Mathematical Soundness Without False Negatives**:
   - The Static Relaxed NLF condition strictly compares the per-vertex static deficit against the total deviation parameter $k$:
     $$\delta_{\text{static}}(u, v) = \sum_{\sigma \in \Sigma_Q(u)} \max\bigl(0, N_Q(u, \sigma) - N_G(v, \sigma)\bigr) \le k$$
   - Anchor vertices are evaluated strictly and independently against $k$. Summing deficits across anchor vertices is strictly prohibited and avoided, eliminating the false dismissal flaw identified in Counterexample A1.
   - Evaluated candidate data vertices in DFS branch `PRESENT` are filtered before candidate list generation, while branch `MISSING` and leaf exact-k verification remain entirely untouched.
2. **ACID Transactional Safety & Zero-Allocation Rollback**:
   - Integrated into the existing `TransactionScope` architecture.
   - Frequency updates are staged before or after graph modifications in strict accordance with operation snapshot invariants:
     - **INSERT**: Search callback observes $G_{\text{after}} = G \cup \{e\}$ and post-insertion frequency counts.
     - **DELETE**: Search callback observes $G_{\text{before}} = G$ and pre-deletion frequency counts.
   - Rollback is mediated by a fixed-capacity 2-entry `NlfUndoRecord` with `armed` state flags. Rollback operations execute zero heap allocations, are marked `noexcept`, and are strictly idempotent under repeated invocations.
   - Comprehensive fault injection across all 7 transaction phases (`BEFORE_MUTATION`, `AFTER_NLF_PREPARE`, `AFTER_GRAPH_BEFORE_NLF`, `AFTER_FIRST_NLF_CELL`, `AFTER_MUTATION`, `DURING_CALLBACK`, `BEFORE_PUBLISH`) verified 100% complete rollback to the exact pre-transaction state without memory corruption or dangling cell increments.
   - Invariant violations caught during preparation fail-closed: mark the session poisoned (`session_poisoned_ = true`) and return `StepStatus::INVARIANT_VIOLATION`, rejecting any subsequent transactions on the session.
3. **Strict Numeric Safety & Resource Containment**:
   - Exact dense 1D storage using `uint32_t` (`NlfCount`) per $(v, \sigma)$ cell.
   - All arithmetic operations (multiplications for cell indexing, additions on edge insertion, subtractions on edge deletion) are protected by checked overflow/underflow invariants (`InvariantViolationException`).
   - Default configurable memory ceiling of 256 MiB (`268,435,456 bytes`), verified fail-closed before any heap allocation via checked 64-bit arithmetic. Excessive memory requests fail gracefully with `NlfMemoryLimitException` without crashing the process.
   - CLI flags `--max-nlf-memory-mib` and `--nlf-mode` enforce duplicate flag detection, positive non-zero validation, and reject overflows ($2^{44}$ MiB) with exit code 1 (`ExitCode::CLI_ARG_ERROR`).
4. **Diagnostic Counter Accuracy**:
   - Anchor evaluation counters (`nlf_anchor_checks` and `nlf_anchor_pruned`) increment strictly per evaluated anchor vertex. When the first anchor fails, the second anchor is never evaluated and counter overcount is eliminated.
   - In `NONE` mode, all NLF counters remain strictly zero. No NLF index or search operations occur in disabled configuration; performance effects unevaluated.
5. **Canonical Non-Regression & Frozen Baseline Preservation**:
   - Reference baseline commit `be136f3d4d0ea0b50d51329dbc9a61dd714d719f` was built in an isolated tree (`SHA-256: 4fd6f8a16422587e7b0f9c5cd181ef2c5b8b8dbc8e6b0926e9052bbe00bb80ea`) and compared against the candidate binary (`SHA-256: 14a8fd1641cdb7325222a3a3d33f691271a09a24b887bd6fc39c726c3f4a8a06`).
   - Bit-for-bit parity confirmed across all 25 Hand workloads and all 56 stateful Stream matrix workloads (672 updates): identical statuses, identical entered/exited tuple sets, and identical legacy engine counters (`work_units`, `candidate_evaluations`, `edge_lookups`, `vertex_label_checks`, etc.).
   - Property-based differential testing verified across 78 generated streams (780 updates) over seeds 1, 7, 42, covering K2, Triangle, C4, Diamond, K4, Triangular Prism with mixed typed labels and zero-event churn, demonstrating 100% agreement between M0 Oracle, Bare M2, and A-static.
   - AddressSanitizer and UndefinedBehaviorSanitizer passed cleanly with zero memory errors and zero undefined behavior.

---

## 2. Component Design & Source Traceability

```
                                  +------------------------------------+
                                  |         ApxStreamingEngine         |
                                  |     (Configures NlfMode: NONE/     |
                                  |    STATIC, 256 MiB Memory Limit)   |
                                  +-----------------+------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  |          TransactionScope          |
                                  |   - insert_edge / delete_edge      |
                                  |   - NlfUndoRecord (2 fixed cells)  |
                                  |   - Fail-Closed Session Poisoning  |
                                  |   - Fault Injection Hooks          |
                                  +--------+------------------+--------+
                                           |                  |
                       Graph Mutation      |                  |  Frequency Mutation
                                           v                  v
                 +---------------------------+      +---------------------------+
                 |   ApxAuthoritativeGraph   |      |        ApxNlfIndex        |
                 |  (Pristine G0 Validation) |      | (Dense 1D uint32_t Table) |
                 +---------------------------+      +---------------------------+
                                                              ^
                                                              | Lookups & Static Deficits
                                                              v
+-----------------------------+                     +---------------------------+
|      ApxQuerySignatures     |-------------------->|     ApxM2EventEngine      |
| - IncidentSignature         |                     | - static_nlf_allows()     |
| - SignatureRequirement      |                     | - PRESENT Candidate Prune |
| - QueryEdgeSignatures       |                     | - Per-Anchor Evaluation   |
+-----------------------------+                     +---------------------------+
```

### 2.1 Query Signature Registry
- **Headers & Source:** `extensions/rapidflow_apx/include/rapidflow_apx/nlf_signatures.h`, `extensions/rapidflow_apx/src/nlf_signatures.cpp`
- **Classes:**
  - `IncidentSignature`: Encodes pair `(target_vertex_label, edge_label)`.
  - `SignatureRequirement`: Maps `IncidentSignature` to required count $N_Q(u, \sigma)$ for query vertex $u$.
  - `QueryEdgeSignatures`: Encodes edge forward and reverse incident signatures.
  - `ApxQuerySignatures`: Compiled query registry built at initialization.
- **Invariants Enforced:**
  - Distinct query signatures $|\Sigma_Q| \le 2|E(Q)|$.
  - Per-vertex signature requirement lists are deterministically sorted by `IncidentSignature`. Lookups execute in $O(\log |\Sigma_Q|)$ via `std::lower_bound`.
  - Degree invariant verified fail-closed: $\sum_{\sigma} N_Q(u, \sigma) = \deg_Q(u)$.
  - Multi-edge deduplication verified: parallel edges between query vertices aggregate correctly into signature requirements.

### 2.2 Exact Dense NLF Frequency Index
- **Headers & Source:** `extensions/rapidflow_apx/include/rapidflow_apx/nlf_index.h`, `extensions/rapidflow_apx/src/nlf_index.cpp`
- **Class:** `ApxNlfIndex`
- **Storage Model:**
  - Row-major dense vector: `std::vector<NlfCount>` (`uint32_t`), dimensioned $|V(G)| \times |\Sigma_Q|$.
  - Flat index calculation: $\text{cell}(v, \sigma) = v \times |\Sigma_Q| + \sigma_{\text{dense\_id}}$.
- **Numeric & Allocation Safety:**
  - Checked multiplication: `num_vertices * num_signatures` checked for 64-bit integer overflow before allocation.
  - Allocation byte check: $\text{cells} \times \text{sizeof(uint32\_t)}$ checked against configurable memory ceiling (default 256 MiB / 268,435,456 bytes) and `std::vector::max_size()`. Exceeding limits throws `NlfMemoryLimitException`.
  - Atomic publication: Built into a local temporary vector during G0 scan; swapped atomically into member `table_` only upon complete successful verification.
  - Storage release: `clear()` guarantees storage deallocation via `std::vector<NlfCount>().swap(table_)` (noexcept).
  - Increment overflow protection: $c + 1 > \text{UINT32\_MAX}$ throws `InvariantViolationException`.
  - Decrement underflow protection: $c == 0$ on decrement throws `InvariantViolationException`.
  - Pristine Boundary Guard: `initialize()` rejects graphs where updates have already occurred (`!graph.is_pristine_initial_state()`).

### 2.3 Transactional Integration & Rollback Lifecycle
- **Headers & Source:** `extensions/rapidflow_apx/include/rapidflow_apx/transaction_manager.h`, `extensions/rapidflow_apx/src/transaction_manager.cpp`
- **Data Structures:**
  - `PreparedNlfUpdate`: Stores cell indices, old counts, and new counts for both endpoints of an edge update.
  - `NlfUndoRecord`: Fixed struct containing `uint32_t cell_index[2]`, `uint32_t old_value[2]`, `uint8_t count`, and `bool armed`. Requires zero heap allocation.
- **Fail-Closed Session Poisoning:**
  - If an invariant violation occurs during NLF preparation (e.g. index corruption, underflow), `session_poisoned_` is set to `true`, and all subsequent calls return `StepStatus::INVARIANT_VIOLATION`.
- **Operational Protocol:**
  - **Edge Insertion**:
    1. Pre-validate update.
    2. Prepare NLF mutation (`prepare_insert_edge`).
    3. Apply edge insertion to `ApxAuthoritativeGraph`.
    4. Apply prepared NLF mutations (`apply_prepared_update`), arming `NlfUndoRecord`.
    5. Execute search callback. Search callback observes $G_{\text{after}}$ and post-insert frequencies.
    6. Commit transaction: `undo_record.armed = false`.
  - **Edge Deletion**:
    1. Pre-validate update.
    2. Execute search callback. Search callback observes $G_{\text{before}}$ and pre-delete frequencies.
    3. Prepare NLF mutation (`prepare_delete_edge`).
    4. Apply edge deletion to `ApxAuthoritativeGraph`.
    5. Apply prepared NLF mutations (`apply_prepared_update`), arming `NlfUndoRecord`.
    6. Commit transaction: `undo_record.armed = false`.
  - **Rollback Protocol (`rollback_nlf`)**:
    - Marked `noexcept`. Iterates armed undo cells and assigns `table_[cell] = old_value`. Disarms undo record. Repeated invocations are safe no-ops.
  - **Fault Injection Framework**:
    - Seven deterministic injection points: `BEFORE_MUTATION`, `AFTER_NLF_PREPARE`, `AFTER_GRAPH_BEFORE_NLF`, `AFTER_FIRST_NLF_CELL`, `AFTER_MUTATION`, `DURING_CALLBACK`, `BEFORE_PUBLISH`.

### 2.4 Static Relaxed NLF Candidate & Anchor Pruning in M2
- **Headers & Source:** `extensions/rapidflow_apx/include/rapidflow_apx/m2_event_engine.h`, `extensions/rapidflow_apx/src/m2_event_engine.cpp`
- **Pruning Logic (`static_nlf_allows`)**:
  - For candidate data vertex $v$ being considered for query vertex $u$:
    $$\delta_{\text{static}}(u, v) = \sum_{\sigma \in \Sigma_Q(u)} \max\bigl(0, N_Q(u, \sigma) - N_G(v, \sigma)\bigr)$$
  - Returns `true` if $\delta_{\text{static}}(u, v) \le k$, otherwise `false` (pruned).
- **Per-Evaluation Anchor Pruning & Counter Accuracy**:
  - For edge event $(u_1, u_2) \mapsto (v_1, v_2)$, anchor evaluation occurs sequentially:
    1. Evaluate anchor 1 ($u_1 \mapsto v_1$): increment `nlf_anchor_checks`. If $\delta_{\text{static}}(u_1, v_1) > k$, increment `nlf_anchor_pruned` and abort immediately.
    2. Anchor 2 ($u_2 \mapsto v_2$) is evaluated ONLY if anchor 1 passes: increment `nlf_anchor_checks`. If it fails, increment `nlf_anchor_pruned` and abort.
  - Eliminates overcounting flaw where 2 checks were counted even when anchor 1 failed.
- **Candidate Filtering**:
  - Filter applied in DFS branch `PRESENT` after vertex label matching and injectivity check, strictly before candidate extensions.
  - Branch `MISSING` is untouched.
  - Leaf exact-k count verification ($|R(f)| = k$) remains untouched as authoritative filter.

### 2.5 Counter Instrumentation & Engine Coordination
- **Headers & Source:** `extensions/rapidflow_apx/include/rapidflow_apx/engine_counters.h`, `extensions/rapidflow_apx/include/rapidflow_apx/streaming_engine.h`, `extensions/rapidflow_apx/src/streaming_engine.cpp`
- **Counters Added:**
  1. `nlf_static_checks`: Number of candidate vertex evaluations against static deficit.
  2. `nlf_static_pruned`: Number of candidates rejected by static deficit filter.
  3. `nlf_signature_lookups`: Number of query signature lookup operations.
  4. `nlf_anchor_checks`: Number of anchor evaluations against static deficit.
  5. `nlf_anchor_pruned`: Number of search branches aborted due to anchor deficit.
  6. `nlf_index_cell_updates`: Total cell mutations executed on the index.
  7. `nlf_index_rollback_cells`: Total cell mutations rolled back during transaction aborts.
  8. `nlf_index_initialization_cells`: Total cells initialized during G0 index construction.

---

## 3. Complexity Analysis

| Operation / Component | Time Complexity | Space Complexity | Notes |
| :--- | :--- | :--- | :--- |
| **Signature Registry Construction** | $O(|E(Q)| \log |\Sigma_Q|)$ | $O(|V(Q)| + |\Sigma_Q|)$ | Built once during query compilation |
| **G0 Index Construction** | $O(|V(G_0)| \cdot |\Sigma_Q| + |E(G_0)| \log |\Sigma_Q|)$ | $O(|V(G_0)| \cdot |\Sigma_Q|)$ | Single sequential allocation, dense storage |
| **Update Preparation & Pre-validation** | $O(\log |\Sigma_Q|)$ | $O(1)$ | 2 signature lookups, zero heap allocation |
| **Index Mutation (Apply)** | $O(1)$ | $O(1)$ | 2 cell updates in flat vector |
| **Index Rollback** | $O(1)$ | $O(1)$ | 2 cell restores, `noexcept`, zero heap allocation |
| **Static Deficit Evaluation** | $O(|\Sigma_Q(u)|)$ | $O(1)$ | Linear scan over vertex signatures, direct indexing into data cell |
| **Candidate Pruning Overhead** | $O(|\Sigma_Q(u)|)$ per candidate | $O(1)$ | Early-out if deficit exceeds $k$ |

---

## 4. Mathematical Correctness & Counterexample Verification

### 4.1 Theoretical Correctness
**Theorem (Static Relaxed NLF Soundness):**
Let $f: V(Q) \to V(G)$ be any valid approximate subgraph isomorphism with at most $k$ missing edges ($|R(f)| \le k$). For any query vertex $u \in V(Q)$ and its mapped data vertex $v = f(u)$, the static deficit satisfies:
$$\delta_{\text{static}}(u, v) \le |R(f)| \le k$$
*Proof:* Every missing edge incident to $u$ in $Q$ can contribute at most 1 to the deficit of signature $\sigma = (\text{label}(u'), \text{label}(u, u'))$. Thus, the total missing incident edges is an upper bound on the signature count deficit: $\delta_{\text{static}}(u, v) \le \deg_{Q - f^{-1}(G)}(u) \le |R(f)| \le k$. Consequently, if $\delta_{\text{static}}(u, v) > k$, then $u$ cannot be mapped to $v$ in any embedding with $\le k$ missing edges. Pruning candidate $v$ when $\delta_{\text{static}}(u, v) > k$ produces zero false dismissals. $\blacksquare$

### 4.2 Resolution of Counterexamples A1, Chord, and Witness A7
The native verification suite (`p4b_tests.cpp`) explicitly evaluates the counterexamples that challenged earlier designs:

1. **Counterexample A1 (False Dismissal on Independent Anchors):**
   - Query: Diamond graph ($k=1$), query vertices $u_0, u_1, u_2, u_3$.
   - Stream update: Edge $(v_0, v_1)$ arrives as anchor. Both $u_0$ and $u_1$ have individual deficits $\le 1$, but summing them would yield $1 + 1 = 2 > 1$, falsely pruning the match.
   - Result in P4b-1A: Anchors evaluated independently against $k$. Candidate expansion allowed. 8 candidate branches entered, 8 tuples produced. **Zero false negatives.**
2. **Counterexample Chord (Dynamic Ordering Flaw Avoidance):**
   - Chord graph query with dense cross-edges.
   - Result in P4b-1A: 16 branches entered, 16 tuples produced identically to Bare M2 and M0. **Zero false negatives.**
3. **Witness A7 (Pruning Effectiveness Demonstration):**
   - Triangle query ($k=0$), data graph contains valid triangle plus dead-end degree-1 vertices with identical vertex labels.
   - Result in P4b-1A:
     - `nlf_static_checks`: 24
     - `nlf_static_pruned`: 6 candidates successfully pruned before extension.
     - Tuples produced: 6 (identical to Bare M2).
     - Execution verified: Pruning occurred with zero divergence from ground truth.

---

## 5. Test Results & Experimental Verification

### 5.1 Native Test Suite Execution (`rapidflow_apx_native --test-p4b all`)
```text
================================================================================
                 RapidFlow-APX Phase P4b-1A Native Verification Suite
================================================================================
  Running Signature Registry Tests (SIG-01 to SIG-10)...
  Running Numeric Safety Tests (NUM-01 to NUM-18 & Synthetic Boundary)...
  Running Actual High-Degree Star Graph Test (70,001 vertices, 70,000 edges)...
  Running Initialization Tests (INIT-01 to INIT-12)...
  Running Transaction & Rollback Tests (TX-01 to TX-25)...
  Running Anchor Diagnostic Counter Accuracy Tests (CNT-01 to CNT-04)...
  Running Counterexamples & Pruning Witness Tests (A1 to A7)...
    [A7 Witness] Static checks: 24, Static pruned: 6, Tuples: 6
================================================================================
  P4b-1A Native Tests Groups Executed: 7
  P4b-1A Native Tests Result: ALL TESTS PASSED
================================================================================
```

### 5.2 Property-Based Differential Testing (`tests/test_rapidflow_apx_p4b.py`)
- Executed over 78 randomly generated dynamic graph streams (780 stream updates) across seeds 1, 7, 42 covering K2 ($k=0$), Triangle ($k=0, 1$), C4 ($k=0, 1$), Diamond ($k=0, 1$), K4 ($k=0, 1, 2$), Triangular Prism ($k=0, 1, 2$) with mixed typed labels and zero-event churn.
- Result: **100% bit-for-bit event agreement across all 780 stream updates between M0 Oracle, Bare M2, and A-static.**
- Test suite: `9 / 9 PASS` (Native harness invocation, A1 fixture, Chord fixture, A7 witness, M1 configuration rejection, K4 k=2 coverage, Property-based differential stream test, Configuration integrity, Memory safety & CLI guards, Dispatcher selector fail-closed rejection).

### 5.3 Frozen Baseline Differential Verification (`be136f3` vs INT04B Candidate)
- Frozen baseline binary SHA-256: `4fd6f8a16422587e7b0f9c5cd181ef2c5b8b8dbc8e6b0926e9052bbe00bb80ea`
- INT04B candidate binary SHA-256: `14a8fd1641cdb7325222a3a3d33f691271a09a24b887bd6fc39c726c3f4a8a06`
- **Hand Cases (25/25)**: 100% bit-for-bit parity on statuses, entered/exited tuples, and legacy counters (`work_units`, `candidate_evaluations`, `edge_lookups`, `vertex_label_checks`, `anchors_started`, `committed_emissions`).
- **56 Stateful Stream Matrix (56 streams / 672 updates)**: 100% bit-for-bit parity on statuses, entered/exited tuples, and legacy counters.

### 5.4 Sanitizer Verification (AddressSanitizer + UndefinedBehaviorSanitizer)
- Binary compiled with `-fsanitize=address,undefined -g`.
- Native test suite executed under ASan/UBSan with `ASAN_OPTIONS=detect_leaks=0` (bypassing upstream baseline RapidFlow `buildEdgeIndex` allocation leak).
- Result: **Zero memory errors, zero out-of-bounds accesses, zero integer overflows, zero undefined behavior across all 7 native test groups.**

---

## 6. Traceability Matrix for All 110 Acceptance Gates

| Group | Gate IDs | Scope / Description | Verification Method | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Group A: Query Signatures** | `A-01` to `A-10` | Signature encoding, registry construction, sorted lookup, degree invariant, multi-edge deduplication, bound $|\Sigma_Q| \le 2|E(Q)|$. | Native unit tests `SIG-01` to `SIG-10` | **PASS (10/10)** |
| **Group B: Numeric Safety** | `B-01` to `B-18` | Checked index arithmetic, overflow/underflow protection, 256 MiB memory cap enforcement, vector max_size checks, 70,001-vertex actual star graph test. | Native unit tests `NUM-01` to `NUM-18`, high-degree star graph | **PASS (18/18)** |
| **Group C: G0 Initialization** | `C-01` to `C-12` | Pristine initial state boundary, atomic table publication via temporary vector, zero-copy move, G0 degree verification, rebuild rejection. | Native unit tests `INIT-01` to `INIT-12` | **PASS (12/12)** |
| **Group D: Transaction Safety** | `D-01` to `D-25` | 2-cell `NlfUndoRecord`, pre-validation, snapshot visibility (INSERT post-count, DELETE pre-count), zero-allocation rollback, 7 fault injection points, partial write rollback (`AFTER_FIRST_NLF_CELL`), repeated rollback idempotency, next update success, session poisoning on invariant failure. | Native unit tests `TX-01` to `TX-25` | **PASS (25/25)** |
| **Group E: Static Relaxed NLF** | `E-01` to `E-12` | Deficit $\delta_{\text{static}}(u, v) \le k$, independent anchor evaluations, candidate pruning before expansion in PRESENT, MISSING branch untouched, exact-k leaf check untouched, counterexamples A1, Chord, A7 witness. | Native tests `A1` to `A7`, differential stream testing | **PASS (12/12)** |
| **Group F: Engine Config & Fallback** | `F-01` to `F-15` | CLI `--nlf-mode`, `--max-nlf-memory-mib`, M1 rejection with exit 1, ALL rejection with exit 1, requested vs effective configuration integrity, fallback rejection from static certification, query-irrelevant updates bypass, memory ceiling fallback, clean lifecycle cleanup. | Python tests, CLI tests, runner integration | **PASS (15/15)** |
| **Group G: Diagnostic Counters** | `G-01` to `G-06` | Versioned 8 NLF counters, strict monotonicity, reset on step begin, per-evaluation anchor counting without overcount, all zeros on `NONE` mode, diagnostic reporting JSON. | Native counter tests, summary schema tests | **PASS (6/6)** |
| **Group H: Correctness & Non-Regression** | `H-01` to `H-12` | Hand cases (25/25), Corpus (840/840), Streams (56 streams / 672 updates), Bare M2 bit-for-bit invariance against frozen baseline `be136f3`, full regression 762 tests, historical P4a integrity (7/7 artifacts). | Canonical runner, unittest discover, SHA manifest audit | **PASS (12/12)** |

**Total Acceptance Gates Checked & Passed:** **110 / 110 (100%)**

---

## 7. Traceability Matrix for All 78 R1 Hardening Closure Gates

| Finding / Scope | Gate IDs | Scope / Description | Verification Method | Status |
| :--- | :--- | :--- | :--- | :--- |
| **P4B-1A-F01 (Config Integrity)** | `CFG-01` to `CFG-16` | Requested vs effective matching, fallback run rejection from static certification, reviewer subprocess substitution attack reproducer rejection, CLI fail-closed for M1 and ALL, negative case preservation. | `test_07_configuration_integrity`, runner integration | **PASS (16/16)** |
| **P4B-1A-F02 (Numeric & Memory Safety)** | `MEM-01` to `MEM-18` | 256 MiB default budget, $2^{44}$ MiB overflow rejection, zero budget rejection, negative budget rejection, duplicate flag guards, low-memory fallback witness, safe vector deallocation via swap. | `test_08_memory_safety_and_cli_guards`, native NUM tests | **PASS (18/18)** |
| **P4B-1A-F03 (Transaction Safety)** | `TXR-01` to `TXR-14` | Zero-cell, one-cell, two-cell matrix, callback snapshot inspection (G_after/G_before), partial write rollback on DELETE, fail-closed session poisoning on invariant failure (`session_poisoned_`). | Native TX tests, `test_transactions_and_fault_injection` | **PASS (14/14)** |
| **P4B-1A-F04 (Counter Accuracy)** | `CNT-01` to `CNT-06` | Sequential anchor evaluation, second anchor never checked when first fails, eliminated overcount, all zeros in NONE mode. | Native CNT tests, `test_anchor_counter_accuracy` | **PASS (6/6)** |
| **P4B-1A-F05 (High-Degree Fixture)** | `HD-01` to `HD-05` | Real 70,001-vertex / 70,000-edge star graph, center count 70,000, leaf count 1, delete/insert/rollback exact restoration. | Native HD tests, `test_actual_high_degree_star_graph` | **PASS (5/5)** |
| **P4B-1A-F06 (Test Dispatcher)** | `DISP-01` to `DISP-07` | Strict fail-closed rejection of unknown suite selectors with exit 1, valid selector dispatching, accurate executed group tracking. | Native dispatcher tests, `test_09_dispatcher_selector_rejection` | **PASS (7/7)** |
| **P4B-1A-F07 (Differential PBT)** | `PBT-01` to `PBT-08` | 78 streams / 780 stream updates across seeds [1, 7, 42], K2, Triangle, C4, Diamond, K4, Triangular Prism, mixed typed labels (`0`, `"0"`, `1`, `"1"`, `99`, `"irrel"`), zero-event churn, 100% agreement. | `test_06_property_based_differential` | **PASS (8/8)** |
| **P4B-1A-F08 (Documentation)** | `DOC-01` to `DOC-04` | Reconciled 110 original gates + 78 closure gates, corrected 256 MiB budget, documented actual star graph, eliminated all trailing whitespace (`git diff --check be136f3d..HEAD` exit 0). | Manual audit, git diff whitespace check | **PASS (4/4)** |

**Total R1 Closure Gates Checked & Passed:** **78 / 78 (100%)**

---

## 8. Frozen Baseline Conformance Statement

1. **Bare M2 Preservation**:
   - When `nlf_mode == NONE`, the event engine executes the pristine Bare M2 search path without evaluating NLF deficits, without signature lookups, and without updating NLF candidate counters.
   - All 25 hand cases, 840 corpus cases, and 56 dynamic stream matrices produce identical event orderings and identical Bare M2 baseline counters against the frozen baseline `be136f3`.
2. **Upstream Submodule Invariance**:
   - Pinned upstream submodule `baselines/rapidflow/source` remains pristine at commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (`upstream_pristine: True`).
   - No modifications have been made to third-party code.
3. **P4a Historical Evidence Invariance**:
   - All 7 historical artifacts listed in `docs/evidence/p4a_evidence_manifest.json` match their recorded SHA-256 and byte sizes bit-for-bit.

---

## 9. Handoff & Stop Condition Statement

Phase P4b-1A implementation hardening patch R1 is fully concluded. In strict adherence to `AGENTS.md` and repository policy:
- Implementation is complete and validated across all 110 acceptance gates and all 78 R1 closure gates.
- Work is restricted to the feature branch `feature/rf38-p4b-optimization`.
- Execution stops at the **Independent Implementation Review Gate**.
- Draft PR #39 remains in `DRAFT` status awaiting independent reviewer inspection. No merge will be attempted.
