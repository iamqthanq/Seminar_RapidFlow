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
All measurements were executed under fail-closed provenance validation, hard resource bounding ($\le 60\,\text{s}$ external timeout, $3\,\text{GiB}$ peak RSS cap, $\ge 4\,\text{GiB}$ free RAM floor), and deterministic pre-declared case selection to eliminate cherry-picking.

### Gate Verdicts & Assessment
| Metric / Gate | Result | Evidence & Epistemic Boundaries |
| :--- | :---: | :--- |
| **Workload Audit & Provenance** | **PASS** | 2,700 query files audited; 30 incompatible format files and 1 out-of-range label query (`Q_136`) cleanly identified and excluded. Dataset, binary, and source SHAs verified. |
| **Execution Safety & Boundedness** | **PASS** | 30/30 runs completed across 10 cases (3 repeats each). 0 timeouts, 0 OOMs, 0 crashes. Peak RSS safely bounded between $816.9\,\text{MB}$ and $1{,}047.0\,\text{MB}$. |
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

---

## 4. Benchmark Results Summary (10 Cases, 30 Runs)

Every single run completed with exit status 0. Below are median values across $n=3$ identical repeats for each case, with minimum and maximum wall clock ranges:

| Case ID | Query ID | Category | Stream ID | Updates | Result Count ($n=3$) | Wall Time (s) [min - max] | Peak RSS (MB) | Preprocess Time (s) | Stream Query Time (s) | Search Count | Relevant Updates |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `c01_q01_insert_p100` | `Q01_auth_5v_l1` | `author_newsp` | `insert_p100` | 100 | **0** | 7.31 [7.02–7.39] | 816.9 | 1.724 | 0.000015 | 0 | 1 |
| `c02_q01_del10_p100` | `Q01_auth_5v_l1` | `author_newsp` | `delete10_p100` | 100 | **0** | 7.01 [6.97–7.05] | 817.0 | 1.704 | 0.000007 | 0 | 0 |
| `c03_q02_insert_p100` | `Q02_auth_5v_l0` | `author_newsp` | `insert_p100` | 100 | **0** | 9.73 [9.73–9.76] | 971.6 | 2.794 | 0.000176 | 0 | 28 |
| `c04_q02_del10_p100` | `Q02_auth_5v_l0` | `author_newsp` | `delete10_p100` | 100 | **0** | 9.66 [9.65–9.68] | 971.7 | 2.775 | 0.000203 | 0 | 27 |
| `c05_q03_insert_p100` | `Q03_auth_6v_l2` | `author_newsp` | `insert_p100` | 100 | **0** | 10.57 [10.45–10.63] | 1039.4 | 3.189 | 0.000309 | 0 | 71 |
| `c06_q03_del10_p100` | `Q03_auth_6v_l2` | `author_newsp` | `delete10_p100` | 100 | **0** | 10.58 [10.51–10.67] | 1039.4 | 3.175 | 0.000309 | 0 | 73 |
| `c07_q04_insert_p100` | `Q04_auth_5v_l1_3` | `author_newsp` | `insert_p100` | 100 | **0** | 9.66 [9.58–9.67] | 1047.0 | 2.938 | 0.000013 | 0 | 1 |
| `c08_q04_del10_p100` | `Q04_auth_5v_l1_3` | `author_newsp` | `delete10_p100` | 100 | **0** | 9.62 [9.55–9.64] | 1046.9 | 2.936 | 0.000006 | 0 | 0 |
| `c09_q05_insert_p100` | `Q05_probe_star4_l0` | `synthetic_probe` | `insert_p100` | 100 | **16,514,940** | 7.38 [7.32–7.43] | 860.7 | 1.731 | 0.012648 | 15 | 28 |
| `c10_q05_del10_p100` | `Q05_probe_star4_l0` | `synthetic_probe` | `delete10_p100` | 100 | **3,996,936** | 7.31 [7.28–7.36] | 860.8 | 1.725 | 0.002628 | 18 | 27 |

- **Durable CSV Evidence:** [`results/summary/rf03_baseline_runs.csv`](../results/summary/rf03_baseline_runs.csv)
- **Immutable Log Directory:** `results/logs/rf03/20261104T125209Z_58757464/` (30 subdirectories, each with `stdout.log`, `stderr.log`, `metadata.json`).

---

## 5. Algorithmic Insights & Empirical Findings

### 5.1. Preprocessing Time Dominates Micro-Streams
In all 10 cases, initial graph reading from disk ($84\,\text{MB}$ `Ndata.graph`) took $~5.5\text{s}$ to $6.5\text{s}$, followed by `Preprocessing time` of $1.70\text{s}$ to $3.19\text{s}$ for global view and NLF index generation.
In contrast, total dynamic stream querying time (`Query time`) across 100 updates was less than $0.013\text{s}$. For short streams, end-to-end execution latency is overwhelmingly dominated by the static index build.

### 5.2. Relevance Filtering Acts as Fast Short-Circuit
The edge-label check at `streaming_engine.cpp:115` (`if (!query_graph->hasEdgeLabel(update.labels_.edge_label_)) return;`) effectively eliminates irrelevant updates before invoking graph search:
- Query `Q01_auth_5v_l1` (label 1): Only 1 of 100 insertion updates carries label 1 (`relevant_update_count = 1`). `Query time` was only $15\,\mu\text{s}$.
- Query `Q03_auth_6v_l2` (label 2): 71 of 100 updates carried label 2. `Query time` was $309\,\mu\text{s}$.

### 5.3. Structural Pruning vs. Hub Volume Explosion
- **Zero-Result Baseline Cases (`c01` through `c08`):** For chordal 5-vertex cycles (`Q01`, `Q02`, `Q04`) and dense 6-vertex diamond topologies (`Q03`), zero matches were formed during the first 100 stream updates (`result_count = 0`, `search_count = 0`). The neighborhood label frequency (NLF) view successfully pruned all candidates before launching backtracking search.
- **High-Volume Star Probe (`c09` & `c10`):** The 4-vertex star probe `Q05` ($K_{1,3}$) produced **$16{,}514{,}940$ matches** under insertion (`c09`) and **$3{,}996{,}936$ matches** under mixed deletion (`c10`).
- **Counting Efficiency:** Despite enumerating $16.5$ million embeddings, `c09` executed in only $7.38\text{s}$ total wall time ($12.6\,\text{ms}$ query time). This demonstrates the empirical power of leaf-level candidate addition (`search_engine.cpp:161`) and automorphism factorization ($|Aut(Q)| = 6$) in COUNT-ONLY mode.

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

---

## 7. Step-by-Step Reproduction Commands

To replicate all audit checks and benchmark results from scratch:
```bash
# 1. Run all unit and failure-preservation tests (62 tests)
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 2. Run benchmark runner preflight in dry-run mode
python3 scripts/rf03_netflow_runner.py --dry-run

# 3. Run pilot execution (3 cases, 1 repeat each)
python3 scripts/rf03_netflow_runner.py --pilot

# 4. Run full benchmark matrix (10 cases x 3 repeats = 30 runs)
python3 scripts/rf03_netflow_runner.py
```
