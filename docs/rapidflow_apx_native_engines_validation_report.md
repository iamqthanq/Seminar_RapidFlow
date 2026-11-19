# RapidFlow-APX Native Engines Validation Report (Phase P3 / D-INT-03)

**Document ID:** `docs/rapidflow_apx_native_engines_validation_report.md`
**Milestone:** D-INT-03 / Phase P3
**Parent Issue:** [Issue #32](https://github.com/iamqthanq/Seminar_RapidFlow/issues/32)
**Worktree:** `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT03`
**Feature Branch:** `feature/rf32-d-int03-native-engines`
**Integration Target:** `QuangThang`
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)
**Logic Commit SHA:** `5a265b31d130ae343102682cf6f5c96ddc374d2e`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `True`)
**Verification Status:** **P3_ENGINES_VERIFIED (ROUND 5 REMEDIATED)**
**Target Handoff Verdict:** **READY_FOR_INDEPENDENT_FINAL_REVIEW**

---

## 1. Executive Summary

Phase P3 (Issue #32) realizes the native C++ event search engines (`ApxM1EnumEngine` and `ApxM2EventEngine`) integrated directly on top of the RapidFlow-APX native foundation completed in Phase P2.

Key achievements:
1. **Mathematical Soundness**:
   - Implemented exact query edge connectivity $\lambda(Q)$ natively via max-flow (Edmonds-Karp BFS augmenting paths).
   - Enforced Domain D-R ($0 \le k < \lambda(Q)$) fail-closed; invalid or disconnected queries are rejected unconditionally.
2. **Native Event Search Engines**:
   - `ApxM1EnumEngine`: Native baseline anchored subgraph matching on $Q - D$ with exact missing set check $R(f) = D$.
   - `ApxM2EventEngine`: Bare direct DFS branching on PRESENT vs MISSING edges, deferring unmapped vertices, resolving incident edges, and enforcing $|R(f)| = k$ at event leaves.
   - `ApxStreamingEngine`: Stateful coordinator executing ACID transactions, interning labels, maintaining active sets, and suppressing duplicates.
3. **Rigorous Independent Verification & Round 5 Remediation**:
   - Verified 100% bit-for-bit against the independent Python M0 Ground Truth Oracle (`scripts.dstar_oracle`).
   - 25 Hand & Adversarial Witness Cases locked via `configs/rapidflow_apx_required_cases.json`: **25 / 25 PASS**.
   - 840 Correctness Corpus Cases: **840 / 840 M1 PASS**, **840 / 840 M2 PASS** (8,002 truth events, 330 cases with events, 239 with $k \ge 1$, zero duplicate emissions).
   - 56-Stream Dynamic Matrix locked via `configs/rapidflow_apx_stream_56_lock.json` (672 updates): **672 / 672 M1 PASS**, **672 / 672 M2 PASS**, active-set reconstruction **672 / 672 PASS**.
   - Round 5 Adversarial Mutation Suite: **76 / 76 injected mutations detected (100.0% detection rate)** across Level A (42 validator unit tests), Level B (4 stage integration tests), and Level C (30 genuine disk interception and subprocess tests).
   - Real active transaction rollback hook (`AFTER_MUTATION` and `BEFORE_PUBLISH`) verified bit-for-bit against reference model, proving zero published tuples, aborted staged tuples, and uncorrupted graph & codec states.
   - Full Repository Regression: **542 / 542 tests PASS (0 errors, 0 failures in 113.6s)**.

---

## 2. Provenance & Build Environment

| Metric / Parameter | Value | Verification |
| :--- | :--- | :--- |
| **Repository Commit** | `5a265b31d130ae343102682cf6f5c96ddc374d2e` | `git rev-parse HEAD` (Clean: `true`) |
| **Upstream Pinned SHA** | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` | Submodule/git check (Pristine: `true`) |
| **Operating System** | Linux 6.8.0-49-generic x86_64 | Clean local environment |
| **Compiler** | GNU GCC / G++ 13.3.0 | Standard Ubuntu 24.04 packages |
| **Build System** | CMake 3.28.3, Make 4.3 | Portable toolchain `external/tools` |
| **Harness Binary** | `tests/native/rapidflow_apx_engines/build/rapidflow_apx_native` | Rebuilt cleanly (`--clean-build`) |
| **Harness SHA-256** | `4013ec0eee08c3e2198efb02230dd060ce070d4344bfe430ca060b86b61384ac` | Verified bit-for-bit |
| **Required Cases Registry** | `configs/rapidflow_apx_required_cases.json` | Hash: `238e49a9e7b0d13ade92b89c67e9ba96e5a54aeaac4065ae6c0a476c847cd14a` |
| **Stream Lock Registry** | `configs/rapidflow_apx_stream_56_lock.json` | Hash: `34f9c78220a3d578b8fb5e582107203404fe7dd9858621f110cac737761aa451` |
| **Summary Artifact** | `reports/rapidflow_apx_engines/summary.json` | Hash: `1c2ff53c4eb042075ce9514badce0de6075e91b2fb6d13b34cbf14f7dbb713d9` |

---

## 3. Architecture & Algorithmic Design

### 3.1. Edge Connectivity & Domain D-R Enforcement
For any connected query graph $Q = (V_Q, E_Q)$, the exact edge connectivity $\lambda(Q)$ is computed via the minimum $(s, t)$-cut over all pairs $(s, t)$ with $s \ne t \in V_Q$.
Each query edge is assigned unit capacity. The maximum flow is found via the Edmonds-Karp algorithm (BFS augmenting paths on unit networks) between all pairs of vertices:
$$\lambda(Q) = \min_{s < t \in V_Q} \text{max\_flow}(s, t)$$

Domain D-R requires:
$$0 \le k < \lambda(Q)$$
Any case where $k \ge \lambda(Q)$ is rejected with `InvalidThresholdException` (fail-closed, exit code 3). Any query with $\lambda(Q) = 0$ (disconnected) or $|V_Q| < 3$ is rejected with `InvalidQueryException`.

### 3.2. ApxM1EnumEngine (Native Exact Decomposed Baseline)
Given a query $Q$ and update edge $e_{\text{update}} = (u_{\text{src}}, v_{\text{dst}})$, an update event occurs if and only if $e_{\text{update}}$ maps to some query edge $e^* = (u^*, v^*) \in E_Q$.
1. **Variant Enumeration**:
   - For each anchor query edge $e^* \in E_Q$, identify all combinations of $k$ distinct edges $D \subset E_Q \setminus \{e^*\}$.
   - If $Q - D$ is disconnected, prune immediately (since connected embeddings cannot exist).
2. **Anchored Subgraph Matching**:
   - Set fixed seed mapping: $f(u^*) = u_{\text{src}}, f(v^*) = v_{\text{dst}}$ (and symmetric orientation if query edge is undirected).
   - Execute depth-first search on $Q - D$ over the authoritative graph snapshot $G_{\text{snap}}$.
3. **Exact Leaf Filter**:
   - For every candidate embedding $f$ of $Q - D$, evaluate the missing edge set on $G_{\text{snap}}$:
     $$R(f) = \{ (u, v) \in E_Q : (f(u), f(v)) \notin E_{\text{snap}} \}$$
   - Accept $f$ if and only if $R(f) = D$.
   - Push canonical ordered tuple $(f(u_1), \dots, f(u_n))$ to `ApxTupleSink` (sorted by ascending query vertex IDs $u_1 < u_2 < \dots < u_n$).

### 3.3. ApxM2EventEngine (Bare Direct Event DFS)
Directly searches for embeddings on $Q$ anchored at $e^*$ by branching on edge states:
1. **Frontier & DFS Order**:
   - Orders query vertices such that anchor endpoints are placed first.
   - For each query edge $(u, v)$, classifies it as:
     - **Anchor edge**: Must match $e_{\text{update}}$ (PRESENT).
     - **Tree edge**: Connects mapped vertex $u$ to unmapped vertex $v$.
     - **Cross/Back edge**: Connects two already-mapped vertices.
2. **PRESENT vs MISSING Branching**:
   - For each frontier incident edge $(u, v)$:
     - **PRESENT**: Candidate data vertices $v' \in N_{G}(f(u))$ are enumerated. Edge is confirmed present in $G_{\text{snap}}$.
     - **MISSING**: Edge $(f(u), f(v))$ is skipped (tolerated missing edge). The missing edge counter increments: $m \leftarrow m + 1$. If $m > k$, branch prunes immediately.
3. **Deferred Vertex Commitments**:
   - When a tree edge is chosen MISSING, vertex $v$ remains unmapped (deferred).
   - When an unmapped vertex is reached later through alternative paths, or after all connected edges are processed, candidates are iterated over compatible label domain vertices.
4. **Incident Edge Resolution & Leaf Invariant Guard**:
   - Whenever an unmapped vertex is assigned, all back-edges to already-mapped vertices are resolved immediately against $G_{\text{snap}}$, incrementing $m$ for missing edges or pruning if $m > k$.
   - At full mapping leaf ($|f| = |V_Q|$), the invariant guard verifies:
     $$|R(f)| == k \quad \text{and} \quad (f(u^*), f(v^*)) == e_{\text{update}}$$
   - Canonical ordered tuple is staged and committed to `ApxTupleSink`.

### 3.4. ApxStreamingEngine
Coordinates stateful streaming over sequences of graph updates:
1. **Label Interning**: Queries and data graph labels are interned into the unified dictionary `ApxTypedCodec`.
2. **Transaction Isolation**:
   - Insertion ($+ u, v, l$): Update is executed inside an `ApxTransactionManager` transaction. If successful, search is executed on $G_{\text{after}}$.
   - Deletion ($- u, v, l$): Search is executed on $G_{\text{before}}$, then update is executed transactionally.
3. **Active Set Tracking**:
   - Maintains an in-memory set of currently active embeddings for each query.
   - For `ENTERED` tuples: adds to active set; asserts tuple was not already present.
   - For `EXITED` tuples: removes from active set; asserts tuple was previously present.
   - Reconstructs full graph state at every step and verifies active set bit-for-bit against M0 Oracle full snapshot recomputation.

---

## 4. Verification Evidence & Test Matrices

### 4.1. Hand & Adversarial Witness Cases (25 Locked Canonical Cases)

All cases are cross-checked against the immutable denominator in `configs/rapidflow_apx_required_cases.json`:

| Case ID | Case Name | Query | Data Graph | $k$ | Expected Result | M1 | M2 | Status |
| :--- | :--- | :--- | :--- | :---: | :--- | :---: | :---: | :---: |
| `CASE_HAND_01` | `diamond_distinct_labels` | Diamond (4v, 5e, distinct labels) | 4 vertices, 4 edges | 0 | 1 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_02` | `diamond_k0_insert` | Diamond (4v, 5e, uniform labels) | 4 vertices, 4 edges | 0 | 4 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_03` | `diamond_k0_delete` | Diamond (4v, 5e, uniform labels) | 4 vertices, 5 edges (-edge) | 0 | 0 ENTERED, 4 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_04` | `k4_k1` | $K_4$ (4v, 6e) | 4 vertices, 4 edges (+edge) | 1 | 24 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_05` | `k4_k2` | $K_4$ (4v, 6e) | 4 vertices, 3 edges (+edge) | 2 | 24 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_06_STEP1` | `diamond_2step_delete_step1` | Diamond (4v, 5e) | 4 vertices, 5 edges (-edge 0-3) | 1 | 0 ENTERED, 16 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_06_STEP2` | `diamond_2step_delete_step2` | Diamond (4v, 5e) | 4 vertices, 4 edges (-edge 1-2) | 1 | 0 ENTERED, 8 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_07` | `no_matching_label` | $K_3$ (3v, 3e, label 'X') | 3 vertices, 2 edges (label 'A') | 0 | 0 ENTERED, 0 EXITED (No Match) | MATCH | MATCH | **PASS** |
| `CASE_HAND_08` | `non_induced_extra_chords` | $C_4$ (4v, 4e, chordless) | 4 vertices, 4 edges (extra chord) | 0 | 8 ENTERED, 0 EXITED (Non-induced) | MATCH | MATCH | **PASS** |
| `CASE_HAND_09` | `edge_label_mismatch` | $K_3$ (3v, 3e) | 3 vertices, 2 edges (edge label mismatch) | 1 | 6 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_10` | `vertex_label_mismatch` | $P_2$ (2v, 1e, label 0/1) | 2 vertices, 0 edges (label mismatch) | 0 | 0 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_11` | `int_vs_str_labels` | $P_2$ (2v, 1e, int label 0) | 2 vertices, 0 edges (str label '0') | 0 | 0 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_12` | `sparse_negative_ids` | $K_3$ (3v, 3e) | Negative vertex IDs ({-10, -20, -30}) | 0 | 6 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_13` | `canonical_tuple_order` | $C_4$ (4v, 4e) | 4 vertices, 3 edges (+edge) | 0 | 1 ENTERED, 0 EXITED (Canonical Sort) | MATCH | MATCH | **PASS** |
| `CASE_HAND_14` | `uniform_k4_k0_automorphisms` | $K_4$ (4v, 6e, uniform) | 4 vertices, 5 edges (+edge) | 0 | 24 ENTERED, 0 EXITED (\|Aut\|=24) | MATCH | MATCH | **PASS** |
| `CASE_HAND_15` | `c4_k1_deferred_witness` | $C_4$ (4v, 4e) | 4 vertices, 2 edges (+edge) | 1 | 8 ENTERED, 0 EXITED (Deferred) | MATCH | MATCH | **PASS** |
| `CASE_HAND_16` | `missing_commitment_violation` | Diamond (4v, 5e) | 4 vertices, 4 edges (+edge) | 1 | 16 ENTERED, 0 EXITED (Missing Com.) | MATCH | MATCH | **PASS** |
| `CASE_HAND_17` | `no_double_charging` | $K_3$ (3v, 3e) | 3 vertices, 2 edges (+edge) | 0 | 6 ENTERED, 0 EXITED (No Double Charge) | MATCH | MATCH | **PASS** |
| `CASE_HAND_18` | `rollback_isolation` | $C_4$ (4v, 4e) | 5 vertices, 5 edges (+edge) | 0 | 8 ENTERED, 0 EXITED (Rollback Iso) | MATCH | MATCH | **PASS** |
| `CASE_HAND_19` | `reversed_endpoints` | Diamond (4v, 5e) | 4 vertices, 4 edges (+reversed edge) | 0 | 4 ENTERED, 0 EXITED (Undirected) | MATCH | MATCH | **PASS** |
| `CASE_HAND_20` | `caller_mutation_isolation` | $K_3$ (3v, 3e) | 3 vertices, 3 edges (3-step seq) | 0 | 6 ENTERED, 12 EXITED (Isolation) | MATCH | MATCH | **PASS** |
| `CASE_HAND_21` | `invalid_k_domain` | $P_4$ (4v, 3e, $\lambda=1$) | Domain D-R violation: $k=1 \ge \lambda=1$ | 1 | REJECTED (Exit 3, DOMAIN_ERROR) | MATCH | MATCH | **PASS** |
| `CASE_HAND_22` | `invalid_update_rollback` | $P_2$ (2v, 1e) | Self-loop prevalidation rejection (1-1) | 0 | REJECTED (Exit 6, PREVALIDATION_ERROR) | MATCH | MATCH | **PASS** |
| `CASE_HAND_23` | `query_size_5_k5_minus_edge` | $K_5 - e$ (5v, 9e, $\lambda=3$) | 5 vertices, 8 edges (+edge) | 1 | 96 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_24_Q6` | `c6_cycle_size_6` | $C_6$ (6v, 6e, $\lambda=2$) | 6 vertices, 4 edges (+edge) | 1 | 12 ENTERED, 0 EXITED | MATCH | MATCH | **PASS** |

### 4.2. 840-Case Correctness Corpus (Real M0 Recomputed)

All 840 configurations from seed 1 and seed 7 were evaluated against the independent Python Ground Truth Oracle:
- **Total Cases**: 840
- **Total M0 Truth Events**: 8,002
- **Cases with Non-Zero Events**: 330
- **Cases with Non-Zero Events and $k \ge 1$**: 239
- **M1 Agreement**: 840 / 840 (100.0%)
- **M2 Agreement**: 840 / 840 (100.0%)
- **M1 Zero Duplicates**: 840 / 840 (100.0%)
- **M2 Zero Duplicates**: 840 / 840 (100.0%)
- **Status**: **PASS**

### 4.3. 56-Stream Stateful Matrix (672 Updates)

Locked via `configs/rapidflow_apx_stream_56_lock.json` with embedded exact query $\lambda(Q)$:
- **Total Stream Configurations**: 56 (100% unique stream IDs)
- **Total Updates Evaluated**: 672 (12 sequential updates per stream)
- **Total ENTERED Events**: 3,024
- **Total EXITED Events**: 1,780
- **M1 Step Agreement**: 672 / 672 (100.0%)
- **M2 Step Agreement**: 672 / 672 (100.0%)
- **M1 Active-Set Reconstruction**: 672 / 672 (100.0%)
- **M2 Active-Set Reconstruction**: 672 / 672 (100.0%)
- **Status**: **PASS**

---

## 5. Round 5 Independent Review Findings & Remediation Matrix

The Round 5 comprehensive engineering remediation addresses findings R5-F01 through R5-F04:

| Finding ID | Severity | Description & Root Cause | Technical Resolution | Independent Verification & Evidence | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **R5-F01** | BLOCKER | Negative output verification lacked independent lifecycle error contracts, deep graph-state & codec-state validation on failing steps, and root-step error consistency enforcement. | Hardened `validate_native_output_schema` and `run_hand_and_witness_cases`: enforces root-step error consistency on failure (root status != SUCCESS, non-empty error_category, failing step error_category == root error_category, failing step commit_status == NOT_COMMITTED, zero tuple emissions). For all negative cases with attempted steps, validates graph state and codec state bit-for-bit against `DStarGraph` reference models before and after failed transactions. | Clean execution of `CASE_HAND_21` and `CASE_HAND_22` PASS; verified in Level C disk-interception mutations 67-72. | **RESOLVED** |
| **R5-F02** | BLOCKER | Registry v2 lacked strict downgrade resistance, mandatory 15-field validation, strict integer typing (preventing bool/float coercion), and per-step event locking. | Enforced strict `registry_version == "2.0.0"` in `validate_required_hand_cases` (rejecting "1.0", "1.0.0", etc.). Enforced all 15 mandatory fields across all 25 cases with strict types (`type(v) is int and not isinstance(v, bool)`), mandatory cryptographic workload SHA-256 verification, and per-step `expected_step_events` sum consistency. | Verified in unit tests and Level A mutations 35-42 (downgrade, missing fields, bool/float types, sum mismatches). | **RESOLVED** |
| **R5-F03** | MAJOR | Transaction safety tests only tested prevalidation rejection, not proving real active transaction rollback occurring after mutation and before commit. | Implemented native fault injection hook in `tests/native/rapidflow_apx_engines/src/main.cpp` via `streaming_engine.get_tx_manager().set_fault_injection()`. Added dedicated test suite verifying: (1) `AFTER_MUTATION` active rollback where mutated edges are undone, restoring graph state; (2) `BEFORE_PUBLISH` rollback where staged candidate tuples in `ApxTupleSink` are aborted (0 published); (3) post-abort state recovery; (4) output transport failure (exit code 7 IO_ERROR) distinct from transaction abort. | Verified in dedicated Group V unit tests (`test_active_transaction_rollback_after_mutation`, `test_active_transaction_rollback_before_publish_staged_tuples`, `test_active_transaction_recovery_subsequent_step`, `test_postcommit_output_error_classification`). | **RESOLVED** |
| **R5-F04** | MAJOR | Level C mutation test suite only modified in-memory Python dictionaries without executing actual subprocesses and intercepting raw output on disk. | Rebuilt Level C using `make_disk_intercepting_executor` that wraps `execute_contained_subprocess` to run real native C++ binary subprocesses and intercept output JSON on disk before runner parsing/validation. Added positive and negative baseline controls, 20 positive output disk corruptions, 6 negative output disk corruptions, stage-level interception, and CLI option rejection tests. | Suite achieves 100.0% detection rate across 76 injected mutations (0 false PASS). | **RESOLVED** |
| **R5-AUDIT-01..04** | AUDIT | Comprehensive re-audit across cross-layer verification consistency, transaction fault coverage, and canonical evidence provenance. | Re-audited full test suite, verified upstream RapidFlow pristine (`2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`), zero whitespace errors against base `88fbe200ff9217f4dccbad08bfa448d067d2ce4e`, clean Git status, and full test regression. | Full repository regression: 542 / 542 tests PASS (0 failures, 0 errors in 113.6s). | **RESOLVED** |

---

## 6. Round 4 Independent Review Findings & Remediation Matrix

The Round 4 comprehensive remediation addresses findings R4-F01 through R4-F08:

| Finding ID | Severity | Description & Root Cause | Technical Resolution | Independent Verification & Evidence | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **R4-F01** | BLOCKER | Output schema validation was bypassed when `schema_version` was missing or malformed rather than failing closed. | Refactored `run_workload_on_binary` and `validate_native_output_schema` to unconditionally enforce schema validation on all parsed JSON outputs, validating mandatory `schema_version == "1.1.0"` and `semantics_version == "D-R-1.0"`. | Reproducer missing `schema_version` confirmed rejected fail-closed; verified in Level C mutation tests 38-41. | **RESOLVED** |
| **R4-F02** | BLOCKER | Cross-layer execution & transaction integrity lacked strict integer typing, allowed loose step statuses on root SUCCESS, and lacked tuple distinctness/injectivity checks. | Enforced strict signed int64 types for `step_index`, `u`, `v`, `op`, `label` in `validate_native_output_schema`. Required that on SUCCESS, every step must have `status == "SUCCESS"`, `commit_status == "COMMITTED"`, `error_category == "NONE"`. Enforced injectivity and unique tuple sets on all event emissions. | Reproducers for Mutations A, B, C, D confirmed rejected fail-closed; verified in Level C mutation tests 42-49. | **RESOLVED** |
| **R4-F03** | BLOCKER | Negative stage allowed schema failures; negative test runs were accepted merely on exit code without enforcing fail-closed error payload schema invariants. | Updated `run_hand_and_witness_cases` to unconditionally require `eng_res.get("schema_valid", False) is True` on negative cases. Error JSON payloads must strictly satisfy lifecycle invariants, error category contracts, and unmutated graph state. | Reproducer `CASE_HAND_22` with corrupted `completed_steps = 123` confirmed rejected fail-closed; verified in Level C mutation tests 53-54. | **RESOLVED** |
| **R4-F04** | BLOCKER | The 25 hand witness cases lacked cryptographic locking of their actual workload definitions and expected step events. | Upgraded `configs/rapidflow_apx_required_cases.json` to v2.0.0, cryptographically locking each case with `canonical_workload_sha256`, `expected_step_events`, vertex/edge counts, and exact $\lambda(Q)$. Runner preflight asserts hashes bit-for-bit. | Reproducer with mutated query labels on `CASE_HAND_04` confirmed rejected fail-closed; verified in Level C mutation test 55. | **RESOLVED** |
| **R4-F05** | MAJOR | Stream lock registry lacked Cartesian completeness verification and explicit bijection between naming, query type, $k$, seed, and canonical input SHA-256. | Hardened `validate_stream_lock_registry` and `run_56_stream_matrix` enforcing 7 topologies $\times$ 8 seeds = 56 streams Cartesian product, naming template `stream_{q_type}_k{k}_{seed}_{label_mode}`, $\lambda(Q)$ consistency, and cross-checking generated workloads against lock item hashes. | Reproducer with mutated stream metadata confirmed rejected fail-closed; verified in Level C mutation test 56. | **RESOLVED** |
| **R4-F06** | MAJOR | `validate_graph_state` allowed extra neighbor keys (e.g. `"999999999": []`) and lacked strict typing on vertex degrees and edge endpoints. | Hardened `validate_graph_state`: enforces exact set equality `set(obs_neighbors.keys()) == {str(vid) for vid in expected_graph.vertices}`, signed int64 range bounds, strict `type(deg) is int and not isinstance(deg, bool)`, and strict label typing. | Reproducers with extra neighbor keys, keys > INT64_MAX, and float/bool degrees confirmed rejected fail-closed; verified in Level C mutation tests 50-52. | **RESOLVED** |
| **R4-F07** | MAJOR | Level C mutation test suite only tested 3 CLI options, failing to test runner interception and validation of real native binary outputs. | Rebuilt Level C mutation suite to intercept raw native outputs from binary runs with positive controls and inject 22 distinct corruptions across schema, lifecycle, typing, and graph state. Added dedicated `test_transaction_rollback_multistep_preservation`. | Suite achieves 100.0% mutation detection rate (59/59 detected); multi-step rollback preservation verified. | **RESOLVED** |
| **R4-F08** | MAJOR | Validation report Section 4.1 had obsolete cases (`CASE_HAND_ADV01`, `CASE_HAND_06`), mismatched names, and outdated $k$ descriptions. | Rebuilt Section 4.1 table matching `configs/rapidflow_apx_required_cases.json` 1:1 across all 25 cases (0 mismatched names, 0 mismatched k, 0 missing IDs, 0 obsolete IDs). Added automated consistency audit test `test_validation_report_consistency_audit`. | Consistency audit test verifies 0 discrepancies between report markdown and JSON registry. | **RESOLVED** |

---

## 7. Round 3 Independent Review Findings & Verification Matrix

The Round 3 remediation addresses findings R3-F01 through R3-F08:

| Finding ID | Severity | Description & Root Cause | Technical Resolution | Independent Verification & Evidence | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **R3-F01** | BLOCKER | Output schema and semantic validation lacked multi-layered invariants (identity, query metadata vs cut oracle, lifecycle invariants, step array structure, emission accounting). | Implemented `validate_native_output_schema` verifying schema identity, version tags, query vertex/edge metadata against independent Edmonds-Karp cut oracle, lifecycle consistency (`steps_executed == len(steps)`), step schema invariants, and committed tuple accounting (`sum(entered + exited) == committed_emissions`). | Exercised across all 25 hand cases, 840 corpus cases, and 56 streaming runs; 34 mutation tests in Level A assert that tampering with identity, cuts, counters, or steps fails closed. | **RESOLVED** |
| **R3-F02** | BLOCKER | Previous adversarial mutation test suite relied on tautological assertions, testing mock transformations against themselves rather than true validator rejection. | Completely rebuilt `test_group_u_adversarial_mutation_suite` with 40 distinct non-tautological test cases across Level A (34 validator unit tests), Level B (3 stage integration tests), and Level C (3 runner subprocess executions). All mutations assert strict fail-closed rejection. | Suite executes 40 injected mutations with 40 detected and 0 incorrectly accepted (100.0% detection rate). | **RESOLVED** |
| **R3-F03** | BLOCKER | Hand cases lacked independent immutable registry, allowing denominator to drift or self-authorize. | Created `configs/rapidflow_apx_required_cases.json` specifying exact 25 required cases, step breakdowns, expected exit codes, and categories. Enforced in `run_rapidflow_apx_engines.py` preflight. | Preflight checks require exact match to registry; missing or altered cases fail closed before execution. | **RESOLVED** |
| **R3-F04** | BLOCKER | Negative tests (cases 21 & 22) lacked end-to-end evidence validation and rollback state verification. | Negative cases explicitly verify exit code (Case 21: code 3, Case 22: code 6), error category, schema version, semantics version, and assert that rollback leaves graph state and codec state completely unmutated. | Validated in both unit tests (`test_group_j`, `test_group_p`) and canonical runner (`run_hand_and_witness_cases`). | **RESOLVED** |
| **R3-F05** | MAJOR | Stream lock registry lacked explicit $\lambda(Q)$ values, duplicate checking, and global consistency sums. | Updated `configs/rapidflow_apx_stream_56_lock.json` embedding exact `"lambda"` in every stream entry. Added `validate_stream_lock_registry` checking cardinality (56), uniqueness, metadata integrity, and verifying global consistency sums (672 updates, 3,024 entered, 1,780 exited). | Verified preflight and asserted in canonical full run. | **RESOLVED** |
| **R3-F06** | MAJOR | Graph state validation permitted loose types (e.g. floats for IDs, booleans for degrees) and did not check neighbor key round-trips. | Hardened `validate_graph_state`: strictly requires `type(v) is int` (rejects `bool`, `float`), validates int64 ranges, verifies string-to-int round-trip for neighbor keys (`str(int(k)) == k`), and verifies graph degree conservation $\sum \text{deg} = 2 |E|$. | Level A mutations verify rejection of float IDs, boolean degrees, and malformed neighbor keys. | **RESOLVED** |
| **R3-F07** | MAJOR | Work budget precedence could allow unbounded CLI overrides to enlarge budget beyond canonical safety limits. | Refactored `main.cpp` enforcing `effective_limit = std::min(workload_requested, cli_requested, canonical_cap)` where `canonical_cap = 500,000ULL`. Added structured `work_budget` observability reporting `default_limit`, `canonical_cap`, `workload_requested_limit`, `cli_requested_limit`, `effective_limit`. | Unit test `test_group_n` verifies that CLI cannot exceed canonical cap; output JSON records all budget tiers. | **RESOLVED** |
| **R3-F08** | MAJOR | Micro-workload containment guards were not asserted preflight, risking unbounded test execution. | Added `validate_micro_workload_bounds` checking $|V(Q)| \le 6, |V(G_0)| \le 8, \text{updates} \le 12, \text{max\_work} \le 500,000$. Clarified contract distinction in `rapidflow_apx_native_engines_design.md` between native engine capability and canonical micro-workload test bounds. | Enforced before every test run in `run_rapidflow_apx_engines.py`. | **RESOLVED** |

---

## 8. Previous Round 1 & Round 2 Summary

Prior findings from Round 1 (P3-F01 through P3-F09) addressed core execution mechanics:
- **P3-F01**: Separated `step_counters_` (isolated per step) from `cumulative_counters_`.
- **P3-F02**: Strict operation whitelist (`+`, `-`, `INSERT`, `DELETE`).
- **P3-F03**: Integer range and overflow validation for threshold $k$.
- **P3-F04**: Strict rejection of boolean/float/null labels in `parse_typed_label`.
- **P3-F05**: Standardized exit code contract (0: Success, 1: CLI, 2: Schema, 3: Domain D-R, 4: WorkBudget, 5: ResourceLimit, 6: StepFailure, 7: IO).
- **P3-F06**: DFS root node counting integrity in `dfs_states`.
- **P3-F07**: Corrected algorithmic attribution to Edmonds-Karp BFS max-flow.
- **P3-F08**: Checked `setrlimit` return code fail-closed.
- **P3-F09**: Deep graph state emission in step diagnostics.

All Round 1 and Round 2 fixes remain fully preserved and covered by regression tests.

---

## 9. Decision Summary

| Component | Status | Finding / Action |
| :--- | :---: | :--- |
| `ApxQueryPlan` | **NEW** | Natively computes $\lambda(Q)$ via Edmonds-Karp max-flow, validates D-R ($0 \le k < \lambda(Q)$), indexes incident edges. |
| `ApxM1EnumEngine` | **NEW** | Baseline exact $Q - D$ variant search with strict $R(f) = D$ filtering; bit-for-bit identical to M0. |
| `ApxM2EventEngine` | **NEW** | Bare direct DFS with PRESENT/MISSING branching, deferred vertices, incident edge resolution, and invariant guards; bit-for-bit identical to M0. |
| `ApxStreamingEngine` | **NEW** | Coordinates ACID transactions, label interning, duplicate suppression, and active-set tracking with per-step budget isolation. |
| `ApxAuthoritativeGraph` | **KEEP** | Unaltered P2 foundation provides reliable graph state with zero-allocation rollback. |
| `ApxTypedCodec` | **KEEP** | Decodes 64-bit vertex IDs, maps typed labels, and formats ascending query canonical tuples. |
| Automorphism Factor | **STOP** | Prohibited multiplying counts by scalar symmetry factors; every embedding must be explicitly enumerated. |
| Python in Production | **STOP** | Native engines are 100% C++17 with zero runtime dependency on Python. |

**Handoff Gate Verdict**: **READY_FOR_INDEPENDENT_FINAL_REVIEW**

---

## 10. Handoff & Next Steps

1. **Reviewer Handover**:
   - PR target: `QuangThang`.
   - Feature branch: `feature/rf32-d-int03-native-engines`.
   - Base commit: `88fbe200ff9217f4dccbad08bfa448d067d2ce4e`.
   - All tests passing, upstream pristine, zero git dirtiness, diff hygiene verified.
2. **Phase P4 Prerequisites**:
   - Both baseline M1 and bare M2 engines are ready to serve as algorithmic anchors for Phase P4 (approximate NLF filters and adaptive search orders).
