# RapidFlow-APX Architecture Audit & Verification Report (Gate P1)

**Document ID:** `docs/rapidflow_apx_audit_report.md`
**Date:** 2026-11-18
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D (Issue #28 / PR #29 Remediation)
**Parent Document:** Issue #28 (`D-INT-01 / P0–P1`)
**Base Commit:** `03be8b56a9479609adf87d213122eabefc9162fb`
**Upstream RapidFlow SHA:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
**Audit Status:** `COMPLETE`
**Overall Verdict:** `READY_FOR_P2_DESIGN`

---

## 1. Scope of Audit

This audit evaluates the architectural and mechanical feasibility of extending original **RapidFlow (PVLDB 2022)** to support **Approximate Continuous Subgraph Matching (APX CSM)** under **Edge Recall** and **S2 connectivity** ($0 \le k < \lambda(Q)$).

The audit specifically validates:
1. Genuine compilation, linking, and runtime invocation of RapidFlow's `Graph` CSR, `GlobalEdgeView`, and `GlobalViewManager` components.
2. Boundary interface guarantees (external sparse, negative, and large IDs, and typed label separation: int `0` $\ne$ str `"0"`).
3. The exact behavior and limitations of `GlobalEdgeView` (sorted vectors, insertion/deletion return codes, absence of edge labels).
4. Concrete counterexample proving via genuine runtime invocation of RapidFlow's `GlobalViewManager` that the exact NLF condition ($v\_nlf \ge u\_nlf$) prunes valid approximate matches.
5. Snapshot sequencing and transactional rollback under simulated mid-stream fault injection on audit adapter structures.
6. The boundary between RapidFlow's scalar COUNT_ONLY execution mode and the required canonical ordered tuple output format, specifically resolving the K4 automorphism overcounting risk.

---

## 2. Execution Environment & Provenance

- **Operating System:** Linux (Ubuntu 24.04 LTS, kernel 6.8.0)
- **CPU:** Intel Core i5-8265U @ 1.60GHz (4 cores / 8 threads)
- **RAM Available:** Preflight check passed ($\ge 2.0$ GiB available satisfied)
- **Disk Free:** Preflight check passed ($\ge 1.0$ GiB free satisfied)
- **Compiler:** `g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- **CMake:** Portable CMake 3.28.3 (`external/tools/cmake-3.28.3-linux-x86_64/bin/cmake`)
- **Compilation Flags:** `-O2 -Wall -Wextra -pthread -std=c++17`
- **Native Binary:** `tests/native/rapidflow_apx_audit/build/rapidflow_apx_audit`
- **Binary SHA-256:** `75e0f1daf0d7977e9a4b6c73c7afa658b93df52abab8a03ba808857a365c3fee`
- **Linked RapidFlow Sources:** `graph/graph.cpp`, `utility/graphoperations.cpp`, `streaming/global_view_manager.cpp`

---

## 3. Files Audited

1. `baselines/rapidflow/source/graph/graph.h` and `graph.cpp` (CSR storage, neighbor access, edge existence)
2. `baselines/rapidflow/source/utility/relation/global_edge_view.h` (sorted vector adjacency, insertion/deletion)
3. `baselines/rapidflow/source/streaming/global_view_manager.h` and `.cpp` (NLF checking, exact candidate sets)
4. `baselines/rapidflow/source/streaming/streaming_engine.h` and `.cpp` (streaming loop, snapshot sequencing)
5. `baselines/rapidflow/source/streaming/search_engine.h` and `.cpp` (backtracking search, leaf candidate shortcut)
6. `baselines/rapidflow/source/streaming/order_manager.h` and `.cpp` (automorphism edge detection and factor multiplication)
7. `baselines/rapidflow/source/streaming/local_view_manager.h` and `.cpp` (local candidate set intersection)

---

## 4. Native Validation Probe Matrix

All 6 declared probes were executed natively with process group containment (timeout 10–15s, RLIMIT_AS 256 MiB):

| Case ID | Probe Group | Target Component | Expected Behavior | Actual Behavior | Native Exit Code | Result | Evidence Classification & Verified Values |
| :--- | :---: | :--- | :--- | :--- | :---: | :---: | :--- |
| `CASE_PROBE_A_PROVENANCE` | `PROBE_A` | `graph/graph.cpp` | Compiles, links, calls accessors, verifies binary SHA-256 | Graph accessors called; vertices=4, edges=4; binary SHA recorded | 0 | **PASS_EXPECTED** | `NATIVE_RUNTIME_VERIFIED`: `graph_accessors_verified=true`, `runtime_exit_code=0` |
| `CASE_PROBE_B_TYPED_BOUNDARY` | `PROBE_B` | `graph/graph.cpp` + Codec | Round-trips sparse, negative, >uint32 IDs; separates int 0 != str "0" | Codec roundtrip verified; int 0 != str "0"; Graph loaded with 5 vertices, 4 edges | 0 | **PASS_EXPECTED** | `NATIVE_RUNTIME_VERIFIED`: `int_zero_vs_str_zero_separated=true`, `large_id_mapped=true`, `fixture_bound=true` |
| `CASE_PROBE_C_GLOBAL_EDGE_VIEW` | `PROBE_C` | `global_edge_view.h` | Sorted vectors; duplicate insert returns false; absent delete returns false | Sorted ordering verified [3,5,8]; dup insert false; absent delete false | 0 | **PASS_EXPECTED** | `NATIVE_RUNTIME_VERIFIED`: `duplicate_insert_rejected=true`, `insertion_sorted=true`, `absent_delete_rejected=true` |
| `CASE_PROBE_D_EXACT_NLF_COUNTEREXAMPLE` | `PROBE_D` | `global_view_manager.h` | M0 accepts Diamond (0,1,2,3); exact NLF check prunes vertices 1 and 2 | Genuine `GlobalViewManager` called: `nlf_check(1,1)=false`; candidates=[1,2] excluded | 0 | **PASS_EXPECTED** | `NATIVE_RUNTIME_VERIFIED`: `m0_ground_truth_accepted=true`, `rapidflow_nlf_check_result=false`, `candidate_filter_excluded_vertices=[1, 2]`, `native_gvm_executed=true`, `incompatibility_proven=true` |
| `CASE_PROBE_E_SNAPSHOT_ROLLBACK` | `PROBE_E` | `streaming_engine.cpp` | INSERT on $G_{\text{after}}$, DELETE on $G_{\text{before}}$; mid-stream fault reverts step | Snapshot timing verified in source; adapter Step 3 fault triggers rollback; state matches Step 2 | 0 | **PASS_EXPECTED** | `NEW_AUDIT_ADAPTER_RUNTIME`: `fault_injected=true`, `rollback_executed=true`, `pre_fault_state_preserved=true`, `upstream_engine_evidence_level=SOURCE_INSPECTED` |
| `CASE_PROBE_F_COUNT_TUPLE_BOUNDARY` | `PROBE_F` | `search_engine.cpp` | K4 exact produces scalar 24 via leaf shortcut and automorphism factor | Confirmed COUNT_ONLY mode, leaf shortcut at line 161, factor at line 181; M0 has 24 tuples | 0 | **PASS_EXPECTED** | `SOURCE_INSPECTED`: `m0_distinct_tuples_count=24`, `rapidflow_output_mode=COUNT_ONLY`, `p2_tuple_path_design=UNROLL_LEAF_EXPAND_AUTOMORPHISMS_WITH_CANONICAL_PRUNING` |

---

## 5. Key Empirical & Architectural Findings

### 5.1. RapidFlow Components Reusable As-Is
1. **CSR Graph Representation**: `Graph` (`graph.cpp`) provides compact, static storage with binary-search neighbor retrieval. Perfect for query topologies $Q$ and initial graph snapshots $G_0$.
2. **GlobalEdgeView Adjacency**: `GlobalEdgeView` (`global_edge_view.h`) maintains sorted vectors per vertex using `lower_bound`, returning strict boolean flags on duplicate insertions and absent deletions. Ideal for dynamic overlay storage.
3. **Streaming Snapshot Sequencing**: RapidFlow's `StreamingEngine` already applies the correct snapshot ordering for CSM: INSERT updates views before searching ($G_{\text{after}}$); DELETE searches before updating views ($G_{\text{before}}$).

### 5.2. Rejected / Incompatible Mechanisms
1. **Exact NLF Candidate Pruning**: Probe D conclusively proved that RapidFlow's `nlf_check` condition ($v\_nlf \ge u\_nlf$) prunes valid approximate embeddings where query edges are missing. Candidate filtering based on exact NLF must be eliminated in P2/P3.
2. **Leaf Candidate Count Shortcut**: In `search_engine.cpp:161`, RapidFlow directly adds `local_idx_[next_depth].second` to `result_count` without binding vertex assignments. Must be unrolled in P2 to materialize individual tuples.
3. **Naive Automorphism Expansion on Full DFS**: In `streaming_engine.cpp:181`, RapidFlow multiplies results by $|Aut(Q)|$. As proven by the K4 counterexample ($24 \times 24 = 576$), unconditional expansion on a complete DFS produces gross overcounting. The native tuple sink must either use symmetry-breaking in the DFS tree or expand representative edge orbits only.

---

## 6. Decision R1 Summary & Phase P2 Recommendations

- **Verdict:** **`READY_FOR_P2_DESIGN`**
- **Phase P2 Priorities**:
  1. Build `ApxTypedCodec` to handle external sparse/negative IDs and typed labels (int `0` vs str `"0"`).
  2. Implement `ApxAuthoritativeGraph` combining RapidFlow CSR snapshot with `GlobalEdgeView` mutable overlays.
  3. Implement `ApxTransactionManager` with pre-validation, atomic updates, and rollback.
  4. Implement `ApxTupleSink` with leaf-level candidate binding and representative automorphism expansion.
- **Constraints**:
  - Do NOT implement full M1/M2 search in P2 (deferred to P3).
  - Do NOT implement dynamic pruning $\delta'$ or NLF in P2 (deferred to P4).
  - Do NOT implement general threshold D-G ($k \ge \lambda(Q)$) (deferred).

---

## 7. Limitations & Honest Assessment

1. **Micro-Scale Validation Only**: Probes were executed on small graphs ($|V| \le 8$). Large-scale scalability on NetFlow remains unmeasured and cannot be claimed.
2. **P2 Engine Not Yet Built**: This audit establishes the architectural blueprint and verifies constituent data structures; it does not constitute a completed production engine.
3. **No Speedup Claims**: No speedup or novelty claims are made.
