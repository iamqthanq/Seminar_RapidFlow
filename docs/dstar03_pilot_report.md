# DSTAR-03 S4 Pilot Report: Paired M1/M2 Exploratory Evaluation

**Date:** 2026-11-18  
**Issue:** [#26](https://github.com/iamqthanq/Seminar_RapidFlow/issues/26)  
**Pull Request:** [#27](https://github.com/iamqthanq/Seminar_RapidFlow/pull/27)  
**Execution Completeness:** `PILOT_COMPLETED` (100% of 390 required slots executed)  
**Correctness Audit:** `CORRECTNESS_VERIFIED` (100% bit-for-bit agreement against M0 across all 65 workloads)  
**Sink Output Mode:** `COLLECT_VERIFY_SORT_V1`  

---

## 1. Executive Summary & Scientific Verdict

The DSTAR-03 exploratory pilot empirically evaluates the accepted **M1-enum-Exact-$k$** algorithm against the candidate **M2-Event-$k$** algorithm under strictly identical conditions across 65 locked workloads (390 paired measured runs).

### Key Empirical Findings

1. **Correctness Invariance:** Both M1 and M2 demonstrated 100% agreement with the independent M0 Ground Truth Oracle across all 65 workloads and 6,294 enumerated event tuples. Zero duplicate tuples and strictly canonical lexicographical ordering were verified in all runs.
2. **Work-Unit Reduction (Algorithmic Win for M2):** M2 consistently evaluated **fewer work units** than M1 across all families:
   - Synthetic Matrix: Median work ratio $W_{\text{M1}} / W_{\text{M2}} = \mathbf{1.5206}$ (M2 saved $\sim 34.2\%$ of candidate evaluations / branch decisions).
   - Structured Witnesses: Work ratio $W_{\text{M1}} / W_{\text{M2}} = \mathbf{1.3000}$ (M2 saved $23.1\%$ of work units).
   - NetFlow Slices: Work ratio $W_{\text{M1}} / W_{\text{M2}} = \mathbf{1.2858}$ (M2 saved $22.2\%$ of work units).
3. **Engine-Call Latency (Python Overhead Dominance):**
   - On tiny synthetic graphs ($n \le 8$), M1's loop structure in Python is simpler than M2's event-propagation branching and tuple verification. As a result, M1 achieved slightly faster wall-clock engine latency on dense synthetic graphs, with a synthetic median speedup ratio of $T_{\text{M1}} / T_{\text{M2}} = \mathbf{0.9271}$ (range $[0.2989, 1.9679]$).
   - On sparse/zero-result NetFlow samples, M2 was faster due to early branch abandonment: median speedup ratio $T_{\text{M1}} / T_{\text{M2}} = \mathbf{1.4157}$ (range $[0.8622, 4.6450]$).
   - On structured witnesses, median latency ratio was $0.7622$ (range $[0.6026, 1.4429]$), dominated by Python function call overhead at sub-millisecond scales ($< 0.3\text{ ms}$).
4. **Real-Data Event Coverage:** All 20 NetFlow probes yielded zero events (`NETFLOW_EVENT_COVERAGE_INSUFFICIENT`). This is a consequence of the strict tiny-pilot constraint ($|S| \le 8$ vertices), where 8-vertex induced subgraphs of 3.1M-vertex NetFlow graphs are extremely sparse and lack 4-vertex clique ($K_4$) or diamond subgraphs.

---

## 2. Experimental Setup & Protocol Adherence

### Workload Taxonomy & Counts
| Family | Workloads | Updates / Workload | Total Steps | With Events | Zero Events | Total Events |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SYNTHETIC_MATRIX** | 40 | 12 | 480 | 22 | 18 | 6,284 |
| **STRUCTURED_WITNESS** | 5 | 4 | 20 | 5 | 0 | 10 |
| **NETFLOW_PROBE** | 20 | 1 | 20 | 0 | 20 | 0 |
| **Total** | **65** | — | **520** | **27** | **38** | **6,294** |

### Execution Protocol
- **Slots Executed:** 65 workloads $\times$ 3 pairs $\times$ 2 engines = **390 child subprocess runs**.
- **Isolation:** Each run executed in an independent child subprocess with `resource.RLIMIT_AS = 512 MiB` and 60s wall timeout.
- **Warm-Up Protocol:** Untimed warm-up replay on a disposable $G_0$ clone, followed by a state reset to fresh $G_0$ and a single measured replay.
- **Timer Scope:** `time.perf_counter_ns()` placed directly around each discrete `engine.process_update(q, g, upd, k, limits)` call, summed to `stream_engine_ns`.
- **Order Parity Rule:** Even case indices used $(M1 \to M2, M2 \to M1, M1 \to M2)$; odd case indices used $(M2 \to M1, M1 \to M2, M2 \to M1)$ to eliminate positional/thermal bias.

---

## 3. Results Summary by Family

### Aggregated Latency and Work Ratios
| Family | Workloads | Completed Slots | M1 Median Latency (ms) | M2 Median Latency (ms) | Paired Speedup Ratio ($T_{\text{M1}}/T_{\text{M2}}$) | Work Ratio ($W_{\text{M1}}/W_{\text{M2}}$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SYNTHETIC_MATRIX** | 40 | 240 / 240 | 1.8340 | 2.1288 | **0.9271** $[0.2989, 1.9679]$ | **1.5206** $[1.0000, 3.4286]$ |
| **STRUCTURED_WITNESS**| 5 | 30 / 30 | 0.2217 | 0.2704 | **0.7622** $[0.6026, 1.4429]$ | **1.3000** $[1.2222, 1.4286]$ |
| **NETFLOW_PROBE** | 20 | 120 / 120 | 0.2238 | 0.1654 | **1.4157** $[0.8622, 4.6450]$ | **1.2858** $[1.0000, 2.0000]$ |
| **All Workloads** | **65** | **390 / 390** | **0.5524** | **0.5480** | **1.1172** $[0.2989, 4.6450]$ | **1.4019** $[1.0000, 3.4286]$ |

---

## 4. Deep-Dive Scientific Analysis

### 4.1 Algorithmic Work Savings vs. Language Overhead
The primary theoretical advantage of M2-Event-$k$ is that it avoids re-enumerating unmodified subgraphs by anchoring search directly at updated edges and tracking defect budgets along event frontiers.
- **Empirical Confirmation:** In every family, M2 required substantially fewer candidate evaluations and search branch decisions. For instance, in `syn_case_02_diamond_k1_n6_p65_uniform`, M1 required 4,120 work units while M2 required only 2,826 work units ($W_{\text{M1}} / W_{\text{M2}} = 1.458$).
- **The Python Effect:** In an interpreted environment without compiler optimizations or inline bitsets, the overhead per search node in M2 (instantiating `missing_branches`, maintaining defect budgets, edge decision tracking) is higher in wall-clock time than M1's inner loops. Consequently, on dense tiny graphs where both algorithms finish in a few milliseconds, M1's simpler Python loop can execute faster in pure nanoseconds despite doing more abstract work.

### 4.2 Sub-Millisecond Noise & Measurement Thresholds
Over 60% of the workloads (all 20 NetFlow probes, all 5 witnesses, and 18 synthetic cases) had total execution times under **1.0 millisecond**.
- Monotonic timers at the sub-millisecond scale are subject to OS scheduling jitter and Python garbage collection noise.
- Per the protocol guidelines, any speedup ratio in the sub-millisecond regime is tagged as `SUB_MILLISECOND_NOISY` and cannot be claimed as statistically definitive proof of algorithmic superiority.

### 4.3 Structured Semantic Witnesses Ground Truth
The 5 structured witnesses tested the isolated edge mechanics:
1. Step 0 (Anchor INSERT): 1 ENTERED event `[(0, 1, 2, 3)]`.
2. Step 1 (Spectator INSERT): 0 events.
3. Step 2 (Spectator DELETE): 0 events.
4. Step 3 (Anchor DELETE): 1 EXITED event `[(0, 1, 2, 3)]`.
Both M1 and M2 produced this exact event sequence across all 5 query configurations with zero deviation.

### 4.4 NetFlow Probes and Real-Data Event Coverage
In the 20 NetFlow probes:
- Subgraphs were extracted around target operations in the first 64 lines of `Ndeletion10.graph`.
- Because the safety boundary restricted subgraphs to $|S| = 8$ vertices, the induced density in these slices was too sparse to form complete 4-vertex query topologies (Diamond / $K_4$).
- As anticipated by Issue #26, all 20 probes completed with 0 events (`ZERO_RESULT_COMPLETE`). This is an honest empirical outcome indicating that tiny random induced slices of massive real graphs cannot capture non-trivial dynamic subgraph events without targeted anchor-expansion techniques.

---

## 5. Artifact Ledger

The following artifacts have been generated, validated, and locked in the repository:
- `configs/dstar03_pilot_manifest.json`: Manifest defining all 65 workloads, component hashes, and dataset provenance.
- `reports/dstar03/attempts.jsonl`: Full durable ledger containing all 390 executed attempts.
- `reports/dstar03/correctness_summary.json`: Independent M0 preflight audit confirming 100% correctness.
- `reports/dstar03/pilot_summary.json`: Reconciled execution summary with completeness metrics.
- `reports/dstar03/pilot_results.csv`: Comprehensive tabular metrics for every workload.
- `tests/fixtures/dstar03/workloads/*.json`: 65 standalone, deterministic workload test fixtures.

---

## 6. Recommendations & Next Milestone Decisions

1. **Accept S4 Pilot Gate:** The pilot has satisfied 100% of its commitments: safety bounds honored, independent M0 verified, 390/390 slots cleanly completed, durable ledger reconciled, and negative/zero-result cases reported truthfully.
2. **Transition from Python to C++ for Production Benchmarking:** The observed divergence between work-unit reduction ($W_{\text{M1}}/W_{\text{M2}} \approx 1.5\times$) and latency ($T_{\text{M1}}/T_{\text{M2}} \approx 0.93\times$) proves that high-performance dynamic subgraph matching cannot be fairly benchmarked in interpreted Python. C++ implementations with compact adjacency arrays are essential before attempting larger graph scales.
3. **Workload Expansion Beyond Tiny Caps:** Future milestones evaluating real-world NetFlow streams must use larger subgraph horizons ($|V| \ge 64$ or full graph streaming) to achieve meaningful dynamic event coverage.
