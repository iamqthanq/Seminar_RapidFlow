# DSTAR-03 S4 Pilot Report: Paired M1/M2 Exploratory Evaluation (v2)

**Date:** 2026-11-18
**Issue:** [#26](https://github.com/iamqthanq/Seminar_RapidFlow/issues/26)
**Pull Request:** [#27](https://github.com/iamqthanq/Seminar_RapidFlow/pull/27)
**Execution Completeness:** `PILOT_COMPLETED` (390 of 390 required slots executed successfully)
**Correctness Audit:** `CORRECTNESS_VERIFIED` (100% bit-for-bit agreement against independent M0 across all 65 workloads)
**Sink Output Mode:** `COLLECT_VERIFY_SORT_V1`

---

## 1. Executive Summary & Scientific Verdict

The DSTAR-03 exploratory pilot evaluates the accepted **M1-enum-Exact-$k$** baseline against the candidate **M2-Event-$k$** algorithm under strictly identical conditions across 65 locked workloads (390 paired measured runs).

### Key Empirical Findings

1. **Independent Correctness Invariance:**
   Both M1 and M2 demonstrated 100% bit-for-bit agreement with the independent M0 Ground Truth Oracle across all 65 workloads and all 6,294 enumerated event tuples. Canonical lexicographical ordering, zero duplicates, and exact graph edge state tracking outside the timer were verified on every step.

2. **Budget Accounting Metric (`budget_units`):**
   The metric $W = \text{candidate\_evaluations} + \text{edge\_decisions} + \text{variants\_started}$ measures engine internal safety budget accounting, rather than an invariant theoretical work metric, because M1 variants and M2 edge decisions represent distinct structural operations.
   - In synthetic approximate settings ($k \ge 1$), M2 evaluated fewer candidate extensions and variants, yielding a median budget ratio $W_{\text{M1}} / W_{\text{M2}} = 1.8662$ across 24 workloads.
   - In exact controls ($k = 0$), M1 and M2 performed the exact same number of candidate evaluations (31,086 vs 31,086), while M2 incurred additional edge decisions (7,962), resulting in a median budget ratio of $0.9214$.

3. **Observed Latencies & Exploratory Observations:**
   - On the 40 synthetic workloads, paired speedup ratio $T_{\text{M1}} / T_{\text{M2}}$ had a median of **1.3581** (range $[0.6035, 2.6495]$). Speedup was more pronounced on higher-defect queries (e.g. $K_4, k=2$ median 1.7909) and close to parity on exact controls ($k=0$ median 0.9898).
   - On the 5 structured witnesses, latencies were sub-millisecond ($< 0.3\text{ ms}$), with a median speedup ratio of **0.9829** (range $[0.6111, 1.4109]$).
   - On the 20 NetFlow probes, all 20 probes produced zero events due to tiny subgraph horizons ($|S| \le 8$), with a median speedup ratio of **1.4680** (range $[0.7845, 4.5404]$).

4. **Exploratory Hypothesis on State Management:**
   Observed latency variations between M1 and M2 in this Python pilot may relate to state management overhead (such as dictionary allocations, defect tracking objects, and tuple construction). However, this pilot does not perform isolated compiler profiling, ablation, or cross-language comparisons; therefore, state management differences remain an exploratory hypothesis rather than a proven causal claim. Both M1 and M2 anchor search at updated edges.

5. **NetFlow Event Coverage Limitation:**
   The 20 NetFlow probe workloads yielded zero events (`ZERO_RESULT_COMPLETE`). This reflects an event coverage limitation in the 64-operation prefix when restricted to $|S| \le 8$ vertices, rather than proof of algorithmic superiority on real NetFlow graphs.

---

## 2. Experimental Setup & Protocol Adherence

### Workload Breakdown
| Family | Workloads | Updates / Workload | Total Steps | With Events | Zero Events | Sub-ms Noisy | Total Events |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **SYNTHETIC_MATRIX** | 40 | 12 | 480 | 22 | 18 | 15 | 6,284 |
| **STRUCTURED_WITNESS** | 5 | 4 | 20 | 5 | 0 | 5 | 10 |
| **NETFLOW_PROBE** | 20 | 1 | 20 | 0 | 20 | 18 | 0 |
| **Total** | **65** | — | **520** | **27** | **38** | **38** | **6,294** |

### Execution Protocol
- **Slots Executed:** 65 workloads $\times$ 3 pairs $\times$ 2 engines = **390 child subprocess runs** (100% completion).
- **Isolation:** Each repetition executed in an isolated child subprocess with hard address-space ceiling `resource.RLIMIT_AS = 512 MiB` and 60s wall-clock timeout.
- **Warm-Up Protocol:** Untimed warm-up replay on disposable $G_0$ clone, state reset to fresh $G_0$ and new engine instance, followed by single measured replay.
- **Timer Scope:** `time.perf_counter_ns()` placed strictly around discrete `engine.process_update(q, g, upd, k, limits)` calls, accumulated to `stream_engine_ns`.
- **Order Parity Rule:** Even case indices used $(M1 \to M2, M2 \to M1, M1 \to M2)$; odd case indices used $(M2 \to M1, M1 \to M2, M2 \to M1)$.

---

## 3. Results Breakdown by Segment

The following tables are generated directly from `reports/dstar03/pilot_results.csv`:

### 3.1 Family Summary
| Family | Workloads | Completed Slots | Median Speedup Ratio ($T_{\text{M1}}/T_{\text{M2}}$) | Speedup Range | Median Budget Ratio ($W_{\text{M1}}/W_{\text{M2}}$) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **SYNTHETIC_MATRIX** | 40 | 240 / 240 | **1.3581** | $[0.6035, 2.6495]$ | **1.5206** |
| **STRUCTURED_WITNESS** | 5 | 30 / 30 | **0.9829** | $[0.6111, 1.4109]$ | **1.3000** |
| **NETFLOW_PROBE** | 20 | 120 / 120 | **1.4680** | $[0.7845, 4.5404]$ | **1.2858** |

### 3.2 Segmented Breakdown: Approximate Defect vs Exact Controls
| Segment | Workloads | With Events | Zero Events | Sub-ms Noisy | Speedup Median | Speedup Range | Budget Ratio Median |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Synthetic $k \ge 1$ (Approximate)** | 24 | 16 | 8 | 7 | **1.5217** | $[0.9589, 2.6495]$ | **1.8662** |
| **Synthetic $k = 0$ (Exact Control)** | 16 | 6 | 10 | 8 | **0.9898** | $[0.6035, 1.8916]$ | **0.9214** |

### 3.3 Query Condition Breakdown (All 65 Workloads)
| Condition | Shape | $k$ | Workloads | With Events | Speedup Median | Speedup Range | Budget Ratio Median |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `diamond_k1` | Diamond | 1 | 13 | 7 | **1.2530** | $[0.9589, 4.0630]$ | **1.4579** |
| `k4_k1` | $K_4$ | 1 | 13 | 5 | **1.5095** | $[0.9829, 3.1346]$ | **1.9009** |
| `k4_k2` | $K_4$ | 2 | 13 | 7 | **1.7909** | $[1.3639, 4.5404]$ | **2.0599** |
| `diamond_k0` | Diamond | 0 | 13 | 5 | **0.9176** | $[0.7845, 1.8916]$ | **0.8784** |
| `k4_k0` | $K_4$ | 0 | 13 | 3 | **1.0409** | $[0.6035, 1.8411]$ | **0.9667** |

### 3.4 Label Regime Breakdown (Synthetic Matrix)
| Regime | Workloads | With Events | Zero Events | Sub-ms Noisy | Speedup Median | Speedup Range | Budget Ratio Median |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Uniform Labels** | 20 | 17 | 3 | 0 | **1.3581** | $[0.6035, 2.3797]$ | **1.4003** |
| **Mixed Labels** | 20 | 5 | 15 | 15 | **1.3254** | $[0.8605, 2.6495]$ | **1.6120** |

### 3.5 Disaggregated Algorithmic Counters
| Family | M1 Candidate Evals | M2 Candidate Evals | M2 Edge Decisions | M2 Missing Branches |
| :--- | :---: | :---: | :---: | :---: |
| **SYNTHETIC_MATRIX** | 313,566 | 171,984 | 49,629 | 49,629 |
| **STRUCTURED_WITNESS** | 324 | 198 | 84 | 84 |
| **NETFLOW_PROBE** | 13,164 | 7,704 | 2,844 | 2,844 |
| **Total** | **327,054** | **179,886** | **52,557** | **52,557** |

---

## 4. Deep-Dive Scientific Discussion

### 4.1 Budget Units Accounting
The metric `budget_units` aggregates structural counters:
$$W = \text{candidate\_evaluations} + \text{edge\_decisions} + \text{variants\_started}$$
These counters serve as an internal safety guardrail under `limits["max_work"]`. Because M1 and M2 organize search branches differently (M1 explores variant configurations around anchor edges; M2 tracks defect sets via edge decisions), their operations are not isomorphic.
- In approximate matching ($k \ge 1$), M2 pruned candidate extensions early, leading to fewer evaluations (140,898 vs 282,480 in synthetic $k \ge 1$).
- In exact control matching ($k = 0$), both engines performed identical candidate evaluations (31,086), while M2 tracked edge decisions (7,962).

### 4.2 Sub-Millisecond Measurement Noise
A significant portion of runs (38 of 65 workloads) exhibited median execution times below **1.0 millisecond**.
- In this regime, timer resolution, CPU frequency scaling, and interpreter dispatch introduce variance.
- All cases with median latencies below 1.0 ms are explicitly tagged with `SUB_MILLISECOND_NOISY` in `pilot_results.csv`.
- These observations are presented as exploratory measurements rather than conclusive evidence of asymptotic differences.

### 4.3 Structured Semantic Witnesses Ground Truth
The 5 structured witnesses tested isolated edge transitions:
1. Step 0 (Anchor INSERT): 1 ENTERED event `[(0, 1, 2, 3)]`.
2. Step 1 (Spectator INSERT): 0 events.
3. Step 2 (Spectator DELETE): 0 events.
4. Step 3 (Anchor DELETE): 1 EXITED event `[(0, 1, 2, 3)]`.
Both M1 and M2 matched this ground-truth sequence bit-for-bit across all 5 query configurations with zero discrepancies.

### 4.4 Real-Data NetFlow Event Coverage
All 20 NetFlow probe workloads completed with 0 events (`ZERO_RESULT_COMPLETE`).
- Under the pilot's safety ceiling ($|S| \le 8$ vertices), the extracted induced subgraphs from `Ndata.graph` lacked the necessary edge density and matching label configurations to satisfy Diamond or $K_4$ query topologies under $k < \lambda(Q)$.
- This result demonstrates the limitation of tiny induced random subgraphs for dynamic event capture and confirms that future real-world benchmarks require larger vertex budgets ($|S| \ge 64$).

---

## 5. Artifact Ledger

The following artifacts are validated and tracked in the repository:
- `configs/dstar03_pilot_manifest.json`: Manifest (v2) with 65 registered workloads, component hashes, and provenance.
- `reports/dstar03/attempts.jsonl`: Durable journal of all 390 executed slots (fresh v2 run).
- `reports/dstar03/attempts_v1_legacy.jsonl`: Preserved historical ledger from pilot v1.
- `reports/dstar03/containment_evidence.json`: Environmental preflight and RLIMIT_AS/timeout containment self-test evidence.
- `reports/dstar03/correctness_summary.json`: Independent M0 preflight audit confirming 100% correctness.
- `reports/dstar03/pilot_summary.json`: Reconciled execution summary with completeness metrics.
- `reports/dstar03/pilot_results.csv`: Per-workload tabular metrics (with strict LF line endings).
- `tests/fixtures/dstar03/workloads/*.json`: 65 deterministic workload test fixtures.
