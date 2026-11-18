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
**Verification Status:** **P3_ENGINES_VERIFIED**  
**Final Gate Verdict:** **ENGINES_VERIFIED_READY_FOR_P4_DESIGN**  

---

## 1. Executive Summary

Phase P3 (Issue #32) realizes the native C++ event search engines (`ApxM1EnumEngine` and `ApxM2EventEngine`) integrated directly on top of the RapidFlow-APX native foundation completed in Phase P2.

Key achievements:
1. **Mathematical Soundness**:
   - Implemented exact query edge connectivity $\lambda(Q)$ natively via max-flow (Dinic / Edmonds-Karp).
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
Each query edge is assigned unit capacity. The maximum flow is found via Dinic's blocking flow algorithm ($O(V^{2/3} E)$ on unit networks) with Edmonds-Karp fallback:
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
   - `test_group_d_dinic_edge_connectivity_lambda`: PASS
   - `test_group_e_hand_cases_exact_agreement_with_m0`: PASS
   - `test_group_f_witness_k4_exact_automorphism`: PASS
   - `test_group_g_witness_diamond_k1_boundary`: PASS
   - `test_group_h_witness_c4_deferred_vertex`: PASS
   - `test_group_i_duplicate_suppression_and_injectivity`: PASS
   - `test_group_j_transaction_lifecycle_and_rollback`: PASS
   - `test_group_k_stateful_stream_active_set_tracking`: PASS
   - `test_group_l_work_budget_preemption`: PASS
   - `test_group_m_real_corpus_smoke_sample`: PASS
   - Total: 14 tests, 0 failures, 0 errors in 0.448s.

2. **Full Repository Regression Suite (`python3 -m unittest discover tests`)**:
   - Total tests executed: **528**
   - Total failures: **0**
   - Total errors: **0**
   - Execution duration: 119.184s
   - Status: **OK**

---

## 5. Decision R3 Summary

| Component | Status | Finding / Action |
| :--- | :---: | :--- |
| `ApxQueryPlan` | **NEW** | Natively computes $\lambda(Q)$ via Dinic/Edmonds-Karp max-flow, validates D-R ($0 \le k < \lambda(Q)$), indexes incident edges. |
| `ApxM1EnumEngine` | **NEW** | Baseline exact $Q - D$ variant search with strict $R(f) = D$ filtering; bit-for-bit identical to M0. |
| `ApxM2EventEngine` | **NEW** | Bare direct DFS with PRESENT/MISSING branching, deferred vertices, incident edge resolution, and invariant guards; bit-for-bit identical to M0. |
| `ApxStreamingEngine` | **NEW** | Coordinates ACID transactions, label interning, duplicate suppression, and active-set tracking. |
| `ApxAuthoritativeGraph` | **KEEP** | Unaltered P2 foundation provides reliable graph state with zero-allocation rollback. |
| `ApxTypedCodec` | **KEEP** | Decodes 64-bit vertex IDs, maps typed labels, and formats ascending query canonical tuples. |
| Automorphism Factor | **STOP** | Prohibited multiplying counts by scalar symmetry factors; every embedding must be explicitly enumerated. |
| Python in Production | **STOP** | Native engines are 100% C++17 with zero runtime dependency on Python. |

**Final Gate Verdict**: **ENGINES_VERIFIED_READY_FOR_P4_DESIGN**

---

## 6. Handoff & Next Steps

1. **Reviewer Handover**:
   - PR target: `QuangThang`.
   - Feature branch: `feature/rf32-d-int03-native-engines`.
   - Execution commit: `a7267e90c60723447e7acfc4613be2435b558c1b`.
   - All tests passing, upstream pristine, zero git dirtiness.
2. **Phase P4 Prerequisites**:
   - Both baseline M1 and bare M2 engines are ready to serve as algorithmic anchors for Phase P4 (approximate NLF filters and adaptive search orders).
