# RF-02 Correctness Report: Independent Correctness Verification (Gate G1)

**Issue:** [#7 — [RF-02] Independent Correctness Verification — Oracle & Per-Update Delta (G1)](https://github.com/iamqthanq/Seminar_RapidFlow/issues/7)  
**Parent Roadmap:** [#1 — [ROADMAP] RapidFlow × NetFlow](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)  
**Feature Branch:** `feature/rf02-correctness`  
**Integration Target:** `QuangThang`  
**Date:** 2026-11-04  
**Author:** AI Coding Agent / Research Software Engineer  

---

## 1. Executive Summary

In RF-02 / Gate G1, we developed an independent, pure-Python standard library ground truth Oracle (`scripts/rf02_oracle.py`) for exact Continuous Subgraph Matching (CSM) under non-induced injective monomorphism semantics.
We performed per-update differential testing on the unmodified RapidFlow C++ baseline binary using isolated Prefix Replays across 12 distinct fixtures and 31 dynamic update steps.

### Gate Verdicts
| Gate | Description | Status | Evidence / Notes |
| :--- | :--- | :--- | :--- |
| **G1-A** | **Count Correctness** | **PASS** | 31/31 update steps match Oracle added/removed delta count with 100% agreement. |
| **G1-B** | **Mapping Correctness** | **UNVERIFIED / BLOCKED** | Pristine RapidFlow does not export mappings due to leaf-level count shortcuts and automorphism factorization. Modification of C++ source is blocked pending user approval. |
| **OVERALL G1** | **Gate G1 Decision** | **PARTIAL / INCONCLUSIVE** | Accurate classification: `COUNT-PASS / MAPPING-UNVERIFIED`. Awaiting user decision. |

---

## 2. Experimental Environment & Provenance

All tests were executed on the authorized local research workstation:
- **Operating System:** Linux 6.6.137+rpt-rpi-2712 (aarch64 / Linux environment)
- **Python Runtime:** Python 3.10.12 (Standard Library only; no NetworkX or external dependencies)
- **C++ Compiler:** GCC 11.4.0 (Ubuntu)
- **RapidFlow Source Repository:** `baselines/rapidflow/source/`
  - Upstream Git Commit: `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine, verified clean)
- **RapidFlow Binary Executable:** `baselines/rapidflow/source/build/streaming/RapidFlow.out`
  - SHA-256: `abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6` (identical to G0 verified binary)

---

## 3. Reproduction Commands

To replicate all results from scratch:
```bash
# 1. Run Independent Oracle Unit & Self-Validation Tests (15 tests)
python3 -m unittest tests/test_rf02_oracle.py

# 2. Run All Project Unit Tests (Smoke + Oracle + Runner tests)
python3 -m unittest discover tests

# 3. Run Differential Testing Runner in Pilot Mode (3 cases, 4 steps)
python3 scripts/rf02_differential_runner.py --pilot

# 4. Run Full Differential Suite across all 12 cases (31 update steps)
python3 scripts/rf02_differential_runner.py
```

---

## 4. Test Matrix & Results Summary

A total of 12 structured fixtures covering structural topologies, label variations, dynamic insertions/deletions, and symmetry automorphisms were tested.

| Case ID | Fixture Name | Category / Focus | Updates | Inferred Deltas vs Oracle | Result |
| :--- | :--- | :--- | :---: | :---: | :---: |
| `c01_path3_insert` | Path-3 sequential insertion | Group A (Paths) & Group D (Insert) | 2 | Step 1: 0, Step 2: 2 | **PASS** |
| `c02_triangle_insert` | Triangle K3 completion | Group A (Triangle) & Group E (Automorphism) | 1 | Step 1: 6 | **PASS** |
| `c03_triangle_delete` | Triangle K3 deletion | Group D (Delete) | 1 | Step 1: 6 (removed) | **PASS** |
| `c04_cycle4_mixed` | 4-cycle C4 mixed dynamics | Group A (Cycle) & Group C (Extra edge) | 3 | Step 1: 8, Step 2: 0, Step 3: 8 | **PASS** |
| `c05_star_hub` | Star K1,3 hub with distinct labels | Group A (Star) & Group B (Labels) | 2 | Step 1: 6, Step 2: 18 | **PASS** |
| `c06_hetero_vertex_labels` | Heterogeneous vertex labels | Group B (Vertex labels) | 2 | Step 1: 1, Step 2: 0 | **PASS** |
| `c07_hetero_edge_labels` | Heterogeneous edge labels | Group B (Edge labels) | 3 | Step 1: 0, Step 2: 0, Step 3: 1 | **PASS** |
| `c08_non_induced_k4` | Non-induced extra edges assembly | Group C (Non-induced) & Group E | 3 | Step 1: 0, Step 2: 6, Step 3: 12 | **PASS** |
| `c09_diamond_dynamics` | Diamond graph insertion/delete | Group A (Diamond) & Group D | 3 | Step 1: 4, Step 2: 0, Step 3: 4 | **PASS** |
| `c10_injectivity_conflict` | Injectivity conflict to triangle | Group C (Injectivity) | 3 | Step 1: 0, Step 2: 0, Step 3: 6 | **PASS** |
| `c11_multi_step_churn` | P4 path cycle churn | Group D (Multi-step churn) | 4 | Step 1: 2, Step 2: 2, Step 3: 6, Step 4: 6 | **PASS** |
| `c12_symmetric_k4_assembly` | High-symmetry K4 assembly/destroy | Group E (Symmetries, \|Aut\|=24) | 4 | Step 1: 0, Step 2: 0, Step 3: 24, Step 4: 24 | **PASS** |

**Summary Statistics:**
- Total test cases: 12
- Total dynamic update steps: 31
- Passed steps: **31 / 31 (100.0%)**
- Execution time per step: ~15 to 19 ms
- Peak RSS per step: ~16.5 MB (no memory leaks)
- Full per-step CSV log: [`results/summary/rf02_correctness.csv`](file:///home/iamqthanq/Desktop/Seminar_RapidFlow/results/summary/rf02_correctness.csv)
- Raw stdout/stderr execution logs: [`results/logs/rf02/`](file:///home/iamqthanq/Desktop/Seminar_RapidFlow/results/logs/rf02/)

---

## 5. Algorithmic Findings & Semantics Confirmation

1. **Deletion Semantics in RapidFlow:**
   Our inspection of `streaming_engine.cpp:180-240` and verification via `c03_triangle_delete` confirms that when an edge deletion occurs, RapidFlow searches for embeddings in $G_{t-1}$ containing the edge prior to deletion, and adds the count of destroyed embeddings ($|\Delta_t^-|$) to `result_count_`.
   Therefore, Prefix Replay difference $\Delta_k = C_k - C_{k-1}$ precisely matches:
   $$\Delta_k = \begin{cases} |\Delta_k^+| & \text{if op is } + \\ |\Delta_k^-| & \text{if op is } - \end{cases}$$

2. **Non-Induced Monomorphism Semantics:**
   In `c04_cycle4_mixed` (step 2) and `c08_non_induced_k4` (step 1), adding an extra edge between already mapped vertices did not invalidate existing matches, validating non-induced behavior.

3. **Automorphism Handling:**
   RapidFlow factors out query automorphisms algebraically (`result_count += local_result_count * automorphism_size`). The inferred counts consistently matched the full un-quotiented automorphism count produced by the Oracle ($6$ for $K_3$, $8$ for $C_4$, $24$ for $K_4$).

---

## 6. G1-B Status: Why G1 Overall Is Inconclusive

As documented in [`docs/rf02_mapping_feasibility.md`](file:///home/iamqthanq/Desktop/Seminar_RapidFlow/docs/rf02_mapping_feasibility.md):
- Pristine RapidFlow uses a leaf-level shortcut (`search_engine.cpp:161`) and automorphism factorization (`streaming_engine.cpp:181`). It does not materialize mapping tuples.
- Under project governance (`AGENTS.md` Rule 5), `baselines/rapidflow/source/` must remain pristine.
- Consequently, we cannot declare Gate G1-B passed without building a separate diagnostic instrumented tool outside `baselines/`.
- We explicitly stop and request user guidance on whether to implement a diagnostic mapping tool or accept G1-A count correctness to proceed with roadmap planning.
