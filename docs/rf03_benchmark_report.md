# RF-03 Benchmark Report: Reproducible NetFlow COUNT-ONLY Baseline (Gate G2)

**Parent Roadmap:** [#1 — [ROADMAP] RapidFlow × NetFlow](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)  
**Parent Issue:** [#9 — [RF-03] Reproducible NetFlow COUNT-ONLY baseline and workload audit (G2)](https://github.com/iamqthanq/Seminar_RapidFlow/issues/9)  
**Previous Gate:** [#7 — [RF-02] Independent Correctness Verification (G1)](https://github.com/iamqthanq/Seminar_RapidFlow/issues/7) (PR [#8](https://github.com/iamqthanq/Seminar_RapidFlow/pull/8))  
**Worktree / Branch:** `feature/rf03-benchmark` (forked from `origin/QuangThang` at `e470ef3`)  
**Integration Target:** `QuangThang`  
**Date:** 2026-11-04  
**Author:** AI Coding Agent / Senior Research Software Engineer  
**Status:** READY FOR REVIEW (Gate G2 Baseline Established)  

---

## 1. Executive Summary

In Gate G2 (RF-03), we conducted a comprehensive workload audit and established a reproducible, fair, and auditable **COUNT-ONLY performance baseline** for pristine RapidFlow on the public NetFlow dataset.
All measurements were executed under fail-closed provenance validation, resource safeguards ($\le 60\,\text{s}$ external timeout, $3\,\text{GiB}$ post-execution peak RSS budget check, $\ge 4\,\text{GiB}$ free RAM floor), and deterministic pre-declared case selection.

### Gate Verdicts & Assessment
| Metric / Gate | Result | Evidence & Epistemic Boundaries |
| :--- | :---: | :--- |
| **Workload Audit & Provenance** | **PASS** | 2,700 query files audited; 30 incompatible format files and 1 out-of-range label query (`Q_136`) cleanly identified and excluded. Dataset, binary, and source SHAs verified. Pre-registration was conceptual; independent timestamping unverified. |
| **Execution Safety & Boundedness** | **PASS** | 30/30 runs completed across 10 cases (3 repeats each). 0 timeouts, 0 budget OOMs, 0 crashes. Observed Peak RSS safely bounded between $816.8\,\text{MB}$ and $1{,}047.1\,\text{MB}$ (< 1.05 GiB; note: post-execution check only, no runtime kill enforcement or leak prevention). |
| **Numerical Consistency** | **PASS** | 100.0% count agreement across all 3 repeats for every tested case (zero count divergence). |
| **Count Correctness (G1-A)** | **PASS (retained)** | Verified in RF-02 across 12 micro-fixtures and 31 update steps. |
| **Mapping Set Correctness (G1-B)**| **UNVERIFIED / BLOCKED** | Pristine RapidFlow does not export mappings due to leaf-level candidate counting (`search_engine.cpp:161`) and automorphism scaling (`streaming_engine.cpp:181`). C++ code remains pristine. |
| **OVERALL GATE G2 DECISION** | **READY FOR REVIEW** | Bounded baseline established; findings ready for independent AI review and human approval. |

---

## 2. Experimental Environment & Provenance

All benchmarks were executed on the authorized local research workstation:
- **Operating System:** Linux 7.0.0-42-generic #42~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC x86_64
- **Distribution:** Ubuntu 24.04.5 LTS (Noble Numbat)
- **CPU Architecture:** x86_64, GenuineIntel, Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz (4 physical cores, 8 logical threads, 6 MiB L3 cache)
- **Host Memory:** 11 GiB physical RAM, 4.0 GiB Swap. Available RAM before benchmark: $5.90\,\text{GiB}$.
- **Python Runtime:** Python 3.12.3
- **C++ Compiler:** g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
- **RapidFlow Source Repository:** `baselines/rapidflow/source/` (Commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`, verified clean status)
- **RapidFlow Binary Executable:** `baselines/rapidflow/source/build/streaming/RapidFlow.out` (SHA-256: `abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6`)
- **Initial Data Graph:** `data/netflow/release_newsp/netflow/data_graph/Ndata.graph`
  - $3{,}114{,}895$ vertices, $2{,}849{,}732$ edges, max degree $49{,}014$, 8 edge labels (`0`..`7`).
  - SHA-256: `bfedd2cab6d2c460141da945e77b0be39ae24c74d9a28b9394a38ee35b652fc6`
- **Dynamic Stream Prefixes:**
  - `insert_p100`: First 100 updates of `Ninsertion.graph` (SHA-256: `35e295ac5f1496590b1820546fd5e36a3f060f2e7c8110d09635b804aa0d5545`)
  - `delete10_p100`: First 100 updates of `Ndeletion10.graph` (SHA-256: `5516319644e94aad0c8d3c53952d5ab9f5bafd16b9757942d7c363f8cf37a4fa`)

---

## 3. Workload Design & Corpus Audit Findings

Detailed specification is preserved in [`docs/rf03_workload_protocol.md`](rf03_workload_protocol.md) and machine-readable [`configs/rf03_workload.json`](../configs/rf03_workload.json).

### 3.1. Syntax Audit & Exclusions
1. **`Nquery/staticCaseStudyQuery` (30 files):** Uses incompatible `t 7 10` format without edge labels. Excluded to avoid immediate baseline parser crash.
2. **`Nquery/Nquery180_8/Q_136` (1 file):** Contains out-of-domain edge label `32513` absent from NetFlow graph ($0..7$). Excluded as documented outlier.
3. **Corpus Size Distribution:** The author NewSP release contains **no $|V|=4$ queries** (smallest author queries have $|V|=5$, 298 queries, and $|V|=6$, 302 queries; $|V|=7$ has 1,079 queries; $|V| \ge 8$ has 990 queries).
4. **Synthetic Probe Inclusion:** To evaluate 4-vertex CSM behavior and high-degree hub interactions, a 4-vertex star query `probe_star4_l0.graph` ($K_{1,3}$) was created under `data/queries/probes/` and explicitly tagged `category: synthetic_probe`. All other queries are author queries tagged `category: author_newsp`.
5. **Workload Pre-Selection Provenance Boundary:** Workload design and selection criteria were formulated conceptually prior to benchmarking; however, because both `configs/rf03_workload.json` and the first results were committed in Git together in commit `8875ed9`, independent third-party timestamped pre-registration is not formally established.

---

## 4. Benchmark Results Summary (10 Cases, 30 Runs)

Every single run completed with exit status 0. Below are median values across $n=3$ identical repeats for each case from the canonical benchmark run (`20261104T132326Z_0786a355`), with minimum and maximum wall clock ranges:

| Case ID | Query ID | Category | Stream ID | Updates | Result Count ($n=3$) | Wall Time (s) [min - max] | Peak RSS (MB) | Preprocess Time (s) | Stream Query Time (s) | Search Count | Relevant Updates |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `c01_q01_insert_p100` | `Q01_auth_5v_l1` | `author_newsp` | `insert_p100` | 100 | **0** | 8.28 [8.20–8.72] | 817.0 | 1.935 | 0.000014 | 0 | 1 |
| `c02_q01_del10_p100` | `Q01_auth_5v_l1` | `author_newsp` | `delete10_p100` | 100 | **0** | 8.19 [8.15–8.22] | 816.8 | 1.896 | 0.000007 | 0 | 0 |
| `c03_q02_insert_p100` | `Q02_auth_5v_l0` | `author_newsp` | `insert_p100` | 100 | **0** | 11.29 [11.26–11.30] | 971.5 | 3.178 | 0.000202 | 0 | 28 |
| `c04_q02_del10_p100` | `Q02_auth_5v_l0` | `author_newsp` | `delete10_p100` | 100 | **0** | 11.27 [11.27–11.46] | 971.5 | 3.126 | 0.000229 | 0 | 27 |
| `c05_q03_insert_p100` | `Q03_auth_6v_l2` | `author_newsp` | `insert_p100` | 100 | **0** | 12.50 [12.29–13.03] | 1039.0 | 3.514 | 0.000330 | 0 | 71 |
| `c06_q03_del10_p100` | `Q03_auth_6v_l2` | `author_newsp` | `delete10_p100` | 100 | **0** | 12.44 [12.43–13.40] | 1039.2 | 3.617 | 0.000319 | 0 | 73 |
| `c07_q04_insert_p100` | `Q04_auth_5v_l1_3` | `author_newsp` | `insert_p100` | 100 | **0** | 11.24 [11.09–11.52] | 1046.8 | 3.267 | 0.000012 | 0 | 1 |
| `c08_q04_del10_p100` | `Q04_auth_5v_l1_3` | `author_newsp` | `delete10_p100` | 100 | **0** | 11.02 [10.99–11.07] | 1047.1 | 3.230 | 0.000007 | 0 | 0 |
| `c09_q05_insert_p100` | `Q05_probe_star4_l0` | `synthetic_probe` | `insert_p100` | 100 | **16,514,940** | 8.52 [8.49–8.82] | 860.6 | 1.898 | 0.013630 | 15 | 28 |
| `c10_q05_del10_p100` | `Q05_probe_star4_l0` | `synthetic_probe` | `delete10_p100` | 100 | **3,996,936** | 8.46 [8.43–8.50] | 860.6 | 1.878 | 0.002762 | 18 | 27 |

- **Durable Canonical CSV Evidence:** [`results/summary/rf03_baseline_runs.csv`](../results/summary/rf03_baseline_runs.csv) (SHA-256: `05bfeb2423983e2250f6d94d6e718c3906b9654a900258d87db7e15dc0ac0036`).
- **Canonical Run ID:** `20261104T132326Z_0786a355` (30 runs, exit code 0).
- **Immutable Log Directory:** `results/logs/rf03/20261104T132326Z_0786a355/` (30 subdirectories, each with `stdout.log`, `stderr.log`, and `metadata.json`).

> [!NOTE] Evidence Provenance, Checkpoint Durability, and Canonical Protection
> - **Historical Baseline Run (`20261104T132326Z_0786a355`):** Generated and audited under the initial runner implementation. It contains complete per-attempt raw directories (`stdout.log`, `stderr.log`, and `metadata.json`). Per-run incremental checkpointing (`checkpoint_runs.csv`) and `run_manifest.json` are runner features implemented for all subsequent executions and are not retroactively synthesized into the historical canonical directory.
> - **Canonical CSV Protection & Strict Promotion Protocol:** All executions (`--dry-run`, `--pilot`, `--case-id`, or full benchmark) strictly write run-level summaries to isolated run directories (`results/logs/rf03/<run_id>/summary_runs.csv` and `checkpoint_runs.csv`). Direct specification or targeting of `results/summary/rf03_baseline_runs.csv` during benchmark execution is unconditionally prohibited (whether canonical exists or not) and fails closed with `RuntimeError`. Updating or replacing the canonical baseline requires explicit execution of `--promote-run-id <RUN_ID>` (`promote_run_to_canonical()`), which validates full workload identity against `configs/rf03_workload.json` (exact 10 cases, distinct repeat indices {1, 2, 3}, full update process counts, verified run manifest, verified raw stdout/stderr SHA-256 hashes against recorded metadata), and archives the prior canonical baseline using unique non-destructive timestamp+UUID backups.
> - **Case-Level Verdict & Failure Reason Persistence:** PASS is stamped only when all intended repeats complete successfully with valid, invariant counts and zero update errors (`PASS_CONSISTENT`). Any failure (incomplete repeats, timeout, OOM, count mismatch) records all contributing failure reasons into `failure_reasons` within `<case_id>_verdict.json`, updates row statuses, and forces a non-zero exit code.
> - **Exact Subprocess Command Provenance:** In addition to wrapper arguments, each attempt records the exact RapidFlow invocation command (`rapidflow_command`) in its `metadata.json`.

### 4.1. Inter-Run Variability & Historical Run Disclosure

An earlier full execution run (`20261104T125209Z_58757464`) was conducted prior to the runner fix that verified and recorded `updates_num`. Both runs are preserved on disk for full provenance and auditability.

Comparative analysis between the earlier run and the canonical run:
1. **Result Count Invariance:** Across all 10 cases, the `Result count` was 100.0% identical between both runs (c09: 16,514,940; c10: 3,996,936; c01–c08: 0).
2. **Execution Timing Comparison:**
   - c01: 7.31s (earlier) vs 8.28s (canonical) [+13.2%]
   - c02: 7.01s (earlier) vs 8.19s (canonical) [+16.9%]
   - c03: 9.73s (earlier) vs 11.29s (canonical) [+16.1%]
   - c04: 9.66s (earlier) vs 11.27s (canonical) [+16.6%]
   - c05: 10.57s (earlier) vs 12.50s (canonical) [+18.2%]
   - c06: 10.58s (earlier) vs 12.44s (canonical) [+17.6%]
   - c07: 9.66s (earlier) vs 11.24s (canonical) [+16.3%]
   - c08: 9.62s (earlier) vs 11.02s (canonical) [+14.6%]
   - c09: 7.38s (earlier) vs 8.52s (canonical) [+15.5%]
   - c10: 7.31s (earlier) vs 8.46s (canonical) [+15.7%]
3. **Telemetry Boundary:** We do **not** assert thermal throttling, CPU frequency scaling, or background system load as explanatory causes because hardware core temperature and operating frequency telemetry were not continuously sampled during execution. The observed ~13%–18% timing difference represents standard wall-clock inter-run variance on a non-isolated general-purpose workstation.

---

## 5. Algorithmic Insights & Empirical Findings

### 5.1. Preprocessing Time Dominates Micro-Streams
In all 10 cases, initial graph reading from disk ($84\,\text{MB}$ `Ndata.graph`) took $~5.5\text{s}$ to $6.5\text{s}$, followed by `Preprocessing time` of $1.88\text{s}$ to $3.62\text{s}$ for global view and NLF index generation.
In contrast, total dynamic stream querying time (`Query time`) across 100 updates was less than $0.014\text{s}$. For short streams, end-to-end execution latency is overwhelmingly dominated by the static index build.

### 5.2. Relevance Filtering Acts as Fast Short-Circuit
The edge-label check at `streaming_engine.cpp:115` (`if (!query_graph->hasEdgeLabel(update.labels_.edge_label_)) return;`) effectively eliminates irrelevant updates before invoking graph search:
- Query `Q01_auth_5v_l1` (label 1): Only 1 of 100 insertion updates carries label 1 (`relevant_update_count = 1`). `Query time` was only $14\,\mu\text{s}$.
- Query `Q03_auth_6v_l2` (label 2): 71 of 100 updates carried label 2. `Query time` was $330\,\mu\text{s}$.

### 5.3. Structural Pruning vs. Hub Volume Explosion
- **Zero-Result Baseline Cases (`c01` through `c08`):** For chordal 5-vertex cycles (`Q01`, `Q02`, `Q04`) and dense 6-vertex diamond topologies (`Q03`), zero incremental matches were formed during the first 100 stream updates (`result_count = 0`, `search_count = 0`). This counter metric represents the incremental change ($\sum \Delta_t$) in match count during this 100-update slice; it does **not** prove that zero embeddings exist in the initial static data graph, nor does it prove that the full search space was explored or pruned. These cases validate that the pipeline correctly processes sparse stream updates without false positive increments, but cannot be used as evidence of matching-order bottlenecks.
- **High-Volume Star Probe (`c09` & `c10`):** RapidFlow recorded a stream-aggregated incremental count of **$16{,}514{,}940$ matches** under insertion (`c09`) and **$3{,}996{,}936$ matches** under mixed deletion (`c10`). RapidFlow does **not** materialize or output $16{,}514{,}940$ explicit embedding tuples $(u \mapsto v)$.
- **Counting Efficiency:** Despite registering $16.5$ million matching embeddings in count mode, `c09` completed in only $8.52\text{s}$ total wall time ($13.6\,\text{ms}$ query time). This demonstrates the empirical efficiency of leaf-level candidate addition (`search_engine.cpp:161`) and automorphism factorization ($|Aut(Q)| = 6$) in COUNT-ONLY mode. Rapid counting throughput must not be equated with the ability to materialize or enumerate explicit embeddings.

---

## 6. Epistemic Limitations & Constraints for Downstream Work

1. **COUNT-ONLY Semantics:**
   - RapidFlow output `Result count` is the cumulative sum of stream deltas ($\sum \Delta_t$). It does not list explicit embeddings ($u \mapsto v$).
   - High counting throughput cannot be equated with fast full-enumeration capability.
2. **Comparability with Prior Literature:**
   - The NewSP archive contains 8 edge labels (RapidFlow paper reports 7) and 10,000 updates (RapidFlow paper uses 10% edge stream).
   - Direct runtime comparison with PVLDB 2022 or ICDE 2024 paper tables is unverified and prohibited.
3. **No Optimization Claims:**
   - These findings establish a baseline only. No algorithmic modifications or search-space optimizations were performed.
   - For profiling hypotheses and next steps in Gate G3, refer to [`docs/rf03_candidate_bottlenecks.md`](rf03_candidate_bottlenecks.md).
4. **Post-Execution Budget Check vs Lack of Runtime Memory Enforcement:**
   - The runner evaluates Peak RSS post-execution via `/usr/bin/time -v`. It does not enforce active in-flight runtime memory limits (e.g., hard OS cgroup or `setrlimit` kills) during RapidFlow execution. Active runtime memory enforcement and in-flight termination remain an open scope limitation for manager acceptance prior to RF-04.
   - All 30 baseline runs safely recorded Peak RSS between $816.8\,\text{MB}$ and $1{,}047.1\,\text{MB}$, remaining well within the $3\,\text{GiB}$ budget check.

---

## 7. Step-by-Step Reproduction Commands

To replicate all audit checks and benchmark results from scratch:
```bash
# 1. Run all unit and regression tests (92 tests across RF-01, RF-02, and RF-03)
#    - RF-01: 18 tests (tests/test_rf01_smoke_runner.py)
#    - RF-02: 30 tests (tests/test_rf02_oracle.py: 15, tests/test_rf02_runner.py: 15)
#    - RF-03: 44 tests (tests/test_rf03_runner.py)
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 2. Run benchmark runner preflight in dry-run mode
python3 scripts/rf03_netflow_runner.py --dry-run

# 3. Run pilot execution (3 cases, 1 repeat each; output isolated to run dir)
python3 scripts/rf03_netflow_runner.py --pilot

# 4. Run full benchmark matrix (10 cases x 3 repeats = 30 runs; output isolated to run dir)
python3 scripts/rf03_netflow_runner.py
```
