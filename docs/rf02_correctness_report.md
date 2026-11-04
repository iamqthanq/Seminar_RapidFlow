# RF-02 Correctness Report: Independent Correctness Verification (Gate G1)

**Issue:** [#7 — [RF-02] Independent Correctness Verification — Oracle & Per-Update Delta (G1)](https://github.com/iamqthanq/Seminar_RapidFlow/issues/7)  
**Parent Roadmap:** [#1 — [ROADMAP] RapidFlow × NetFlow](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)  
**Feature Branch:** `feature/rf02-correctness`  
**Integration Target:** `QuangThang`  
**Date:** 2026-11-04  
**Author:** AI Coding Agent / Senior Research Software Engineer

---

## 1. Executive Summary

In RF-02 / Gate G1, we developed an independent, pure-Python standard library ground truth Oracle (`scripts/rf02_oracle.py`) for exact Continuous Subgraph Matching (CSM) under non-induced injective monomorphism semantics.
We performed per-update differential testing on the unmodified RapidFlow C++ baseline binary using isolated Prefix Replays across 12 distinct fixtures and 31 dynamic update steps.

### Gate Verdicts
| Gate | Description | Status | Evidence / Notes |
| :--- | :--- | :--- | :--- |
| **G1-A** | **Count Correctness** | **PASS** | 31/31 update steps match Oracle added/removed delta count with 100% agreement on all tested fixtures. Differential runner hardened against failure modes (F1–F4). |
| **G1-B** | **Mapping Correctness** | **UNVERIFIED / BLOCKED** | Pristine RapidFlow does not export mappings due to leaf-level count shortcuts (`search_engine.cpp:161`) and algebraic automorphism multiplication (`streaming_engine.cpp:181`). Modification of C++ source is blocked pending user approval. |
| **OVERALL G1** | **Gate G1 Decision** | **PARTIAL / INCONCLUSIVE** | Accurate scientific classification: `COUNT-PASS / MAPPING-UNVERIFIED`. Awaiting user decision. |

---

## 2. Experimental Environment & Provenance

All tests were executed on the authorized local research workstation, verified via direct system diagnostics (`uname -a`, `lsb_release -a`, `python3 --version`, `g++ --version`, `lscpu`):
- **Operating System:** Linux 7.0.0-42-generic #42~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC x86_64
- **Distribution:** Ubuntu 24.04.5 LTS (Noble Numbat)
- **CPU Architecture:** x86_64, GenuineIntel, Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz (4 physical cores, 8 logical threads, 6 MiB L3 cache)
- **Python Runtime:** Python 3.12.3 (Standard Library only; no NetworkX or external packages)
- **C++ Compiler:** g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
- **RapidFlow Source Repository:** `baselines/rapidflow/source/`
  - Upstream Git Commit: `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine, verified clean status)
- **RapidFlow Binary Executable:** `baselines/rapidflow/source/build/streaming/RapidFlow.out`
  - SHA-256: `abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6` (identical to G0 verified binary)

---

## 3. Reproduction Commands

To replicate all results from scratch:
```bash
# 1. Run Independent Oracle Unit & Self-Validation Tests (15 tests)
python3 -m unittest tests/test_rf02_oracle.py

# 2. Run All Project Unit & Regression Tests (53 tests: Smoke + Oracle + Runner Failure & Portability Tests)
python3 -m unittest discover -s tests -p 'test_*.py' -v

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
- Execution time per step: ~15 to 80 ms (fast micro-benchmarks)
- Peak RSS per step: ~16.5 MB. Không quan sát thấy bất thường rõ rệt về mức sử dụng bộ nhớ trong những phép thử nhỏ đã thực hiện. Tuy nhiên, các phép thử ngắn này không đủ cơ sở để kết luận chương trình không có memory leak trong các kịch bản dài hạn hoặc quy mô lớn.
- Per-step summary CSV: [`results/summary/rf02_correctness.csv`](../results/summary/rf02_correctness.csv)
- Raw logs and metadata directory: [`results/logs/rf02/`](../results/logs/rf02/)

---

## 5. Review Findings Resolution (F1–F4)

Following the independent review by ChatGPT ([comment 6630198447](https://github.com/iamqthanq/Seminar_RapidFlow/pull/8#issuecomment-6630198447)), the harness was hardened against all identified failure modes:

1. **F1 — Error Handling & Failure Preservation:**
   - Explicitly initialized all per-step result variables before subprocess execution, completely eliminating `UnboundLocalError`.
   - Safely caught `subprocess.TimeoutExpired`, `OSError`, `OutputParseError`, and `InternalTimeoutError`, classifying them into `TIMEOUT` or `ERROR` without losing previously written CSV rows.
   - Implemented predecessor failure blocking: if prefix $P_{k-1}$ fails, step $k$ is marked `BLOCKED_PREDECESSOR` and no invalid delta is inferred.
   - Added integration-level synthetic/mock adversarial regression tests in `tests/test_rf02_runner.py` verifying that update count mismatch, subprocess timeout, internal timeout, malformed output, duplicate output, non-zero crash exits (signal 11), count mismatches, and predecessor failures produce durable CSV rows and return exit code 2.

2. **F2 — Provenance & Source Citations:**
   - Corrected actual operating environment based on physical host commands (`uname -a`, `lsb_release -a`, `python3 --version`, `g++ --version`, `lscpu`: Ubuntu 24.04.5 LTS, x86_64, Intel i5-8265U, Python 3.12.3, g++ 13.3.0).
   - Removed unverified "no memory leaks" claim, adding proper qualification.
   - Corrected source citations: `EXECUTION_MODE` is in `streaming/streaming_config.h:15`, and `main()` is in `streaming/streaming_engine.cpp:529`.
   - Normalized all file links to repository-relative Markdown links.

3. **F3 — Run Isolation & Log Preservation:**
   - Every runner execution receives a distinct `run_id` (UTC timestamp + UUID), e.g. `20261104T122525Z_cc8b092b`.
   - Each step retains `stdout.log`, `stderr.log`, `prefix.update`, and `metadata.json` containing exact input/prefix/binary checksums and command lines.
   - Prior logs are preserved without overwriting.

4. **F4 — Portable External Paths:**
   - Path resolution converts external directories (e.g. `/tmp/...`) gracefully to absolute paths when outside `workspace_root`, eliminating `ValueError: is not in the subpath`.
   - Verified via unit test `test_external_tmp_paths_success` in `TestRunnerFailurePreservation`.

---

## 6. Algorithmic Findings & Semantics Confirmation

1. **Deletion Semantics in RapidFlow:**
   Inspection of `streaming_engine.cpp:180-240` and verification via `c03_triangle_delete` confirms that when an edge deletion occurs, RapidFlow searches for embeddings in $G_{t-1}$ containing the edge prior to deletion, and adds the count of destroyed embeddings ($|\Delta_t^-|$) to `result_count_`.
   Therefore, Prefix Replay difference $\Delta_k = C_k - C_{k-1}$ precisely matches:
   $$\Delta_k = \begin{cases} |\Delta_k^+| & \text{if op is } + \\ |\Delta_k^-| & \text{if op is } - \end{cases}$$

2. **Non-Induced Monomorphism Semantics:**
   In `c04_cycle4_mixed` (step 2) and `c08_non_induced_k4` (step 1), adding an extra edge between already mapped vertices did not invalidate existing matches, validating non-induced behavior.

3. **Automorphism Handling:**
   RapidFlow factors out query automorphisms algebraically (`result_count += local_result_count * automorphism_size`). The inferred counts consistently matched the full un-quotiented automorphism count produced by the Oracle ($6$ for $K_3$, $8$ for $C_4$, $24$ for $K_4$).

---

## 7. G1-B Status: Why G1 Overall Is Inconclusive

As documented in [`docs/rf02_mapping_feasibility.md`](rf02_mapping_feasibility.md):
- Pristine RapidFlow uses a leaf-level shortcut (`search_engine.cpp:161`) and automorphism factorization (`streaming_engine.cpp:181`). It does not materialize mapping tuples.
- Under project governance (`AGENTS.md` Rule 5), `baselines/rapidflow/source/` must remain pristine.
- Consequently, we cannot declare Gate G1-B passed without building a separate diagnostic instrumented tool outside `baselines/`.
- We explicitly stop and request user guidance on whether to implement a diagnostic mapping tool or accept G1-A count correctness to proceed with roadmap planning.
