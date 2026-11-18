# RapidFlow-APX Native Engines Validation Report (Phase P3 / D-INT-03)

**Document ID:** `docs/rapidflow_apx_native_engines_validation_report.md`
**Milestone:** D-INT-03 / Phase P3
**Parent Issue:** [Issue #32](https://github.com/iamqthanq/Seminar_RapidFlow/issues/32)
**Worktree:** `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT03`
**Feature Branch:** `feature/rf32-d-int03-native-engines`
**Integration Target:** `QuangThang`
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)
**Logic Commit SHA:** `a7267e90c60723447e7acfc4613be2435b558c1b`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `True`)
**Verification Status:** **P3_ENGINES_VERIFIED (REMEDIATED)**
**Final Gate Verdict:** **ENGINES_VERIFIED_READY_FOR_P4_DESIGN (PENDING INDEPENDENT REVIEW)**

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
3. **Rigorous Independent Verification**:
   - Verified 100% bit-for-bit against the independent Python M0 Ground Truth Oracle (`scripts.dstar_oracle`).
   - 25 Hand & Adversarial Witness Cases: **25 / 25 PASS**.
   - 840 Correctness Corpus Cases: **840 / 840 M1 PASS**, **840 / 840 M2 PASS** (8,002 truth events, 330 cases with events, 239 with $k \ge 1$, zero duplicate emissions).
   - 56-Stream Dynamic Matrix (672 updates): **672 / 672 M1 PASS**, **672 / 672 M2 PASS**, active-set reconstruction **672 / 672 PASS**.
   - Full Repository Regression: **528 / 528 tests PASS** in 119.184s.

---

## 2. Provenance & Build Environment

| Metric / Parameter | Value | Verification |
| :--- | :--- | :--- |
| **Repository Commit** | `a7267e90c60723447e7acfc4613be2435b558c1b` | `git rev-parse HEAD` (Clean: `true`) |
| **Upstream Pinned SHA** | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` | Submodule/git check (Pristine: `true`) |
| **Operating System** | Linux 6.8.0-49-generic x86_64 | Clean local environment |
| **Compiler** | GNU GCC / G++ 13.3.0 | Standard Ubuntu 24.04 packages |
| **Build System** | CMake 3.28.3, Make 4.3 | Portable toolchain `external/tools` |
| **Harness Binary** | `tests/native/rapidflow_apx_engines/build/rapidflow_apx_native` | Rebuilt cleanly (`--clean-build`) |
| **Harness SHA-256** | `1983466500e00570a15dcb8f28b8d1bdee51f1cb5657dd3f21413d2211593654` | Verified bit-for-bit |
| **Summary Artifact** | `reports/rapidflow_apx_engines/summary.json` | Hash: `6f224fbb477697af0c9f7d9f438c3e85b6cfd349dda99614bcc4f2f763d6c41d` |

---

## 3. Architecture & Algorithmic Design

### 3.1. Edge Connectivity & Domain D-R Enforcement
For any connected query graph $Q = (V_Q, E_Q)$, the exact edge connectivity $\lambda(Q)$ is computed via the minimum $(s, t)$-cut over all pairs $(s, t)$ with $s \ne t \in V_Q$.
Each query edge is assigned unit capacity. The maximum flow is found via the Edmonds-Karp algorithm (BFS augmenting paths on unit networks) between all pairs of vertices:
$$\lambda(Q) = \min_{s < t \in V_Q} \text{max\_flow}(s, t)$$

Domain D-R requires:
$$0 \le k < \lambda(Q)$$
Any case where $k \ge \lambda(Q)$ is rejected with `InvalidThresholdException` (fail-closed). Any query with $\lambda(Q) = 0$ (disconnected) or $|V_Q| < 3$ is rejected with `InvalidQueryException`.

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

### 4.1. Hand & Adversarial Witness Cases (25 Cases)

| Case ID | Case Name | Query | Data Graph | $k$ | Expected Result | M1 | M2 | Status |
| :--- | :--- | :--- | :--- | :---: | :--- | :---: | :---: | :---: |
| `CASE_HAND_01` | `diamond_distinct_labels` | Diamond | 4 vertices, 5 edges | 0 | 1 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_02` | `diamond_uniform_labels` | Diamond | 4 vertices, 5 edges | 0 | 4 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_03` | `diamond_k1_single_missing` | Diamond | 4 vertices, 4 edges | 1 | 2 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_04` | `c4_k0_square` | $C_4$ | 4 vertices, 4 edges | 0 | 8 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_05` | `c4_k1_triangle_plus_edge` | $C_4$ | 4 vertices, 4 edges | 1 | 8 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_06` | `k4_k0_all_ones` | $K_4$ | 4 vertices, 6 edges | 0 | 24 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_07` | `k4_k1_all_ones` | $K_4$ | 4 vertices, 5 edges | 1 | 24 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_08` | `k4_k2_all_ones` | $K_4$ | 4 vertices, 4 edges | 2 | 24 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_09` | `house_k0` | House | 5 vertices, 6 edges | 0 | 2 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_10` | `house_k1` | House | 5 vertices, 5 edges | 1 | 6 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_11` | `irrelevant_label_update` | Diamond | Incompatible label | 0 | 0 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_12` | `disconnected_induced_k1` | Diamond | Missing cut edge | 1 | Handled | MATCH | MATCH | **PASS** |
| `CASE_HAND_13` | `delete_transition_k0` | Diamond | Remove edge (0, 1) | 0 | 1 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_14` | `delete_transition_k1` | Diamond | Remove edge (1, 2) | 1 | 2 EXITED | MATCH | MATCH | **PASS** |
| `CASE_HAND_15` | `empty_data_graph_insert` | $K_3$ | Empty $G_0$, insert edge | 0 | 0 ENTERED | MATCH | MATCH | **PASS** |
| `CASE_HAND_16` | `self_loop_rejected` | $K_3$ | Self loop (1, 1) | 0 | REJECTED | MATCH | MATCH | **PASS** |
| `CASE_HAND_17` | `multigraph_rejected` | $K_3$ | Duplicate edge | 0 | REJECTED | MATCH | MATCH | **PASS** |
| `CASE_HAND_18` | `budget_exceeded` | $K_4$ | Tight work budget | 1 | BUDGET_EXCEEDED | MATCH | MATCH | **PASS** |
| `CASE_HAND_19` | `non_injective_pruned` | Diamond | Small data graph | 0 | 0 non-injective | MATCH | MATCH | **PASS** |
| `CASE_HAND_20` | `caller_mutation_isolation` | Diamond | In-memory clone | 0 | Isolated | MATCH | MATCH | **PASS** |
| `CASE_HAND_21` | `invalid_k_domain` | $C_4$ | $k=2 \ge \lambda(C_4)=2$ | 2 | REJECTED | MATCH | MATCH | **PASS** |
| `CASE_HAND_22` | `invalid_update_rollback` | Diamond | Transaction rollback | 0 | Clean rollback | MATCH | MATCH | **PASS** |
| `CASE_HAND_23` | `query_size_5_k5_minus_edge` | $K_5 - e$ | 5 vertices, 9 edges | 1 | Verified | MATCH | MATCH | **PASS** |
| `CASE_HAND_24_Q6` | `c6_cycle_size_6` | $C_6$ | 6 vertices, 6 edges | 1 | Verified | MATCH | MATCH | **PASS** |
| `CASE_HAND_ADV01` | `witness_k4_delete` | $K_4$ | Delete edge in $K_4$ | 0 | 24 EXITED | MATCH | MATCH | **PASS** |

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
- **Execution Duration**: 15.51s
- **Status**: **PASS**

### 4.3. 56-Stream Stateful Matrix (672 Updates)

56 independent dynamic streaming runs across query topologies ($K_3, K_4, C_4, C_5, \text{Diamond}, \text{House}, \text{Tree}_5, \text{Ladder}_6$), edge connectivity thresholds $k \in \{0, 1\}$, and insert/delete ratios:
- **Total Stream Configurations**: 56
- **Total Updates Evaluated**: 672 (12 sequential updates per stream)
- **Total ENTERED Events**: 3,024
- **Total EXITED Events**: 1,780
- **M1 Step Agreement**: 672 / 672 (100.0%)
- **M2 Step Agreement**: 672 / 672 (100.0%)
- **M1 Active-Set Reconstruction**: 672 / 672 (100.0%)
- **M2 Active-Set Reconstruction**: 672 / 672 (100.0%)
- **Execution Duration**: 3.16s
- **Status**: **PASS**

### 4.4. Unit Test & Regression Suite

1. **Phase P3 Unit Tests (`tests/test_rapidflow_apx_engines.py`)**:
   - `test_group_a_preflight_and_git_clean`: PASS
   - `test_group_b_manifest_integrity`: PASS
   - `test_group_c_native_harness_compilation`: PASS
   - `test_group_d_dinic_edge_connectivity_lambda`: PASS (natively computes $\lambda(Q)$ via Edmonds-Karp BFS augmenting paths)
   - `test_group_e_hand_cases_exact_agreement_with_m0`: PASS
   - `test_group_f_witness_k4_exact_automorphism`: PASS
   - `test_group_g_witness_diamond_k1_boundary`: PASS
   - `test_group_h_witness_c4_deferred_vertex`: PASS
   - `test_group_i_duplicate_suppression_and_injectivity`: PASS
   - `test_group_j_transaction_lifecycle_and_rollback`: PASS
   - `test_group_k_stateful_stream_active_set_tracking`: PASS
   - `test_group_l_work_budget_preemption`: PASS
   - `test_group_m_real_corpus_smoke_sample`: PASS
   - `test_group_n_work_budget_lifecycle`: PASS (per-step budget reset, max_work boundary, step_counters vs cumulative_counters)
   - `test_group_o_strict_input_validation`: PASS (fail-closed rejection of boolean labels, non-standard ops, invalid k/max_work)
   - `test_group_p_error_propagation_exit_codes`: PASS (exit code contract: 0, 1, 2, 3, 4, 5, 6, 7)
   - `test_group_q_counter_integrity_dfs_states`: PASS (dfs_states increments at DFS state entry for both M1 and M2)
   - `test_group_r_deep_graph_state_differential`: PASS (deep graph state: vertices, edges, neighbors, codec_state)
   - `test_group_s_m1_m2_adversarial_witnesses`: PASS (diamond k=1 insertion, M1 and M2 identical to M0)
   - `test_group_t_query_connectivity_independent_cuts`: PASS (Edmonds-Karp vs independent all-cuts oracle)
   - Total: 20 test groups (21 tests), 0 failures, 0 errors in 0.645s.

2. **Full Repository Regression Suite (`python3 -m unittest discover tests`)**:
   - Total tests executed: **535**
   - Total failures: **0**
   - Total errors: **0**
   - Execution duration: 130.652s
   - Status: **OK**

---

## 5. Round 1 Independent Review — Findings and Remediation

Following the independent review of Draft PR #33, nine specific findings plus repository diff hygiene were addressed and verified:

| Finding ID | Severity | Root Cause | Fix / Implementation | Regression Test | Before Fix | After Fix | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **P3-F01** | BLOCKER | `work_units` in streaming engine accumulated across all steps without per-step reset; step 2 threw budget exceeded prematurely; `step_counters` missing in output JSON. | Separated `step_counters_` (reset before each step) and `cumulative_counters_` (accumulates step results). Added `step_counters` to `StreamStepResult` and step JSON. Handled `max_work=0` (rejects immediately), `max_work=1` (preempts before search), and preserved diagnostic counters on exception. | `test_group_n_work_budget_lifecycle` | Step 2 failed with budget exceeded even though its individual work was under budget. | Step 2 completes successfully with its own isolated budget; cumulative counters track total work. | **RESOLVED** |
| **P3-F02** | BLOCKER | Unrecognized operation strings silently fell through to `DELETE`. | Strict parser whitelist: only `+`, `-`, `INSERT`, `DELETE`, `INS`, `DEL` accepted; any other string throws `std::invalid_argument` (Exit Code 2). | `test_group_o_strict_input_validation` | Operation `"INVALID_OP"` treated as `DELETE` and exited with 0. | Throws `invalid_argument`, terminates fail-closed with Exit Code 2. | **RESOLVED** |
| **P3-F03** | BLOCKER | Threshold $k$ was parsed without range validation, allowing negative numbers, non-integers, and integer overflow before D-R guard. | Strict type and range validation: requires integer, $k \ge 0$, and fits `uint32_t` without truncation. | `test_group_o_strict_input_validation` | Negative $k$ or float $k=1.5$ caused silent truncation or unexpected behavior. | Rejects non-integers, negative values, and overflow with Exit Code 2. | **RESOLVED** |
| **P3-F04** | MAJOR | JSON boolean labels (`true`/`false`) were implicitly coerced to string `"true"`/`"false"`. | `parse_typed_label` strictly enforces integer or string; rejects boolean, float, and null fail-closed. | `test_group_o_strict_input_validation` | `{"label": true}` interned as `"true"`. | Throws `invalid_argument`, exits with Exit Code 2. | **RESOLVED** |
| **P3-F05** | MAJOR | Non-standard exit codes; process returned code 1 for domain/budget errors without differentiating error causes. | Formalized exit code contract: 0=Success, 1=CLI, 2=Input/Schema, 3=Domain D-R, 4=WorkBudget, 5=ResourceLimit, 6=StepFailure, 7=IO. | `test_group_p_error_propagation_exit_codes` | Work budget exceeded or domain violations exited with code 1. | Work budget returns 4, domain violation returns 3, input error returns 2. | **RESOLVED** |
| **P3-F06** | MAJOR | `dfs_states` counter was incremented inside child candidate loops rather than at DFS function entry, missing root search nodes. | Moved `counters.dfs_states++` to the entry of `match_variant_dfs` in M1 and `dfs_m2` in M2; charged work at `anchors_started++` in M2. | `test_group_q_counter_integrity_dfs_states` | DFS states undercounted search tree nodes by excluding root states. | DFS states strictly positive and monotonic with search space size. | **RESOLVED** |
| **P3-F07** | MAJOR | Documentation and comments claimed Dinic's algorithm while implementation used Edmonds-Karp BFS augmenting paths. | Corrected algorithm description throughout documentation and code comments to Edmonds-Karp; verified against independent cut oracle. | `test_group_t_query_connectivity_independent_cuts` | Discrepancy between documentation (Dinic) and code (Edmonds-Karp). | Accurate algorithmic attribution; 100% agreement with all-cuts oracle. | **RESOLVED** |
| **P3-F08** | MAJOR | CLI harness did not verify `setrlimit` return value; `--max-work` CLI flag did not consistently override workload JSON. | Checked return code of `setrlimit(RLIMIT_AS, ...)` fail-closed; ensured CLI `--max-work` strictly overrides workload configuration. | `test_group_o_strict_input_validation` | Unchecked `setrlimit` could silently fail; CLI flag ignored if workload had `max_work`. | `setrlimit` failure throws `runtime_error`; CLI flag takes precedence. | **RESOLVED** |
| **P3-F09** | MAJOR | Step diagnostic output emitted shallow graph summary rather than full graph state for differential verification. | Emitted deep graph state in each step: `vertices` (id, label, degree), canonical sorted `edges`, `neighbors` map, and `codec_state`. | `test_group_r_deep_graph_state_differential` | Inability to differentially inspect full graph state per update step. | Complete graph state observable in every step JSON. | **RESOLVED** |
| **HYGIENE** | MINOR | Trailing whitespace and blank lines at EOF caused `git diff --check` to fail. | Stripped trailing spaces and normalized EOF across all documentation files. | `git diff --check` | `git diff --check` reported whitespace errors with exit code 2. | `git diff --check` passes cleanly with exit code 0. | **RESOLVED** |

---

## 6. Decision R3 Summary

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

**Final Gate Verdict**: **ENGINES_VERIFIED_READY_FOR_P4_DESIGN (PENDING INDEPENDENT REVIEW)**

---

## 7. Handoff & Next Steps

1. **Reviewer Handover**:
   - PR target: `QuangThang`.
   - Feature branch: `feature/rf32-d-int03-native-engines`.
   - Base commit: `88fbe200ff9217f4dccbad08bfa448d067d2ce4e`.
   - All tests passing, upstream pristine, zero git dirtiness, diff hygiene verified.
2. **Phase P4 Prerequisites**:
   - Both baseline M1 and bare M2 engines are ready to serve as algorithmic anchors for Phase P4 (approximate NLF filters and adaptive search orders).
