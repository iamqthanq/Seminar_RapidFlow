# Phase P4a Native Profiling & Baseline Measurement Report

## 1. Executive Summary

- **Total Workload Pairs Analyzed:** 90
- **Total Rejected Pairs:** 0
- **Median Search Speedup ($S_{search} = T_{M1} / T_{M2}$):** 2.1269x
- **Median Native Step Speedup ($S_{step} = T_{M1} / T_{M2}$):** 1.8021x
- **Median Streaming Loop Speedup ($S_{loop} = T_{M1} / T_{M2}$):** 1.1136x
- **Median Outer Wall Speedup ($S_{outer} = T_{M1} / T_{M2}$):** 1.0414x
- **M2 Neighbor Retrieval within Search:** 22.78%
- **Relative Candidate Reduction (M2 vs M1):** 46.16%
- **P4b Recommendation:** RECOMMENDATION: R4A_PROFILE_READY_FOR_P4B_REVIEW. Empirical data confirms that Search Core occupies 77.2% and Neighbor Retrieval occupies 22.8% of Bare M2 search time. Both Relaxed NLF (Option A) and Authoritative Graph CSR neighbor retrieval optimization (Option B) address measurable cost centers. Future isolated experiments in Phase P4b must quantify attainable end-to-end speedups without premature locking of combined Option B+A.

## 2. Paired Speedup Distribution Across 4 Scopes

| Timer Scope                      | Count | Min   | Q1 (25%) | Median | Q3 (75%) | Max   | Mean  | StdDev |
| -------------------------------- | ----- | ----- | -------- | ------ | -------- | ----- | ----- | ------ |
| Search (Callback Duration)       | 84    | 1.05x | 1.37x    | 2.13x  | 3.36x    | 9.74x | 2.71x | 1.75   |
| Native Step Total (execute_step) | 90    | 0.98x | 1.33x    | 1.80x  | 2.67x    | 6.18x | 2.15x | 1.06   |
| Streaming Loop (C++ main loop)   | 90    | 0.93x | 1.05x    | 1.11x  | 1.27x    | 2.48x | 1.21x | 0.26   |
| Outer Wall (Process Subprocess)  | 90    | 0.43x | 1.00x    | 1.04x  | 1.10x    | 1.71x | 1.06x | 0.13   |

## 3. Stratified Speedup Breakdown (Streaming Loop Scope)

### 3.1 By Query Topology and Tolerance $k$

| Query & Tolerance | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| ----------------- | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| diamond_k0        | 6     | 0.98x | 1.00x | 1.02x  | 1.05x | 1.11x | 1.03x |
| diamond_k1        | 24    | 0.93x | 1.04x | 1.07x  | 1.19x | 1.49x | 1.13x |
| k4_k0             | 9     | 0.93x | 1.03x | 1.05x  | 1.08x | 1.13x | 1.05x |
| k4_k1             | 27    | 0.98x | 1.07x | 1.17x  | 1.47x | 2.48x | 1.30x |
| k4_k2             | 24    | 1.02x | 1.12x | 1.15x  | 1.43x | 1.81x | 1.28x |

### 3.2 By Label Regime

| Label Regime | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| ------------ | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| mixed        | 33    | 0.93x | 1.03x | 1.07x  | 1.10x | 1.30x | 1.07x |
| uniform      | 57    | 0.93x | 1.07x | 1.18x  | 1.44x | 2.48x | 1.28x |

### 3.3 By Graph Stream Topology

| Topology  | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| --------- | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| cycle     | 18    | 1.07x | 1.13x | 1.23x  | 1.33x | 2.48x | 1.30x |
| hub       | 21    | 1.02x | 1.05x | 1.08x  | 1.13x | 1.24x | 1.09x |
| micro     | 45    | 0.93x | 1.03x | 1.08x  | 1.44x | 1.85x | 1.23x |
| scalefree | 6     | 1.09x | 1.11x | 1.14x  | 1.17x | 1.28x | 1.15x |

### 3.4 By Event Status

| Event Status  | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| ------------- | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| event_bearing | 45    | 0.94x | 1.09x | 1.18x  | 1.37x | 1.81x | 1.25x |
| zero_event    | 45    | 0.93x | 1.03x | 1.07x  | 1.13x | 2.48x | 1.16x |

## 4. Macro-Stage Execution Breakdown (% of Total Pre-Output Wall Time)

| Stage                                | M1 Median % | M1 Mean % | M2 Median % | M2 Mean % |
| ------------------------------------ | ----------- | --------- | ----------- | --------- |
| Input Parsing (JSON Deserialization) | 39.19%      | 41.13%    | 45.81%      | 44.33%    |
| Initial Graph CSR Construction       | 0.77%       | 0.74%     | 0.82%       | 0.75%     |
| Query Graph Index & Min-Cut Prep     | 0.34%       | 0.36%     | 0.39%       | 0.36%     |
| Streaming Execution Loop (main.cpp)  | 48.66%      | 49.30%    | 45.09%      | 45.44%    |
| Native execute_step() Sum            | 10.42%      | 19.66%    | 7.35%       | 13.91%    |

## 5. Micro-Step Stage Execution Breakdown (% of Step Total Time)

> [!NOTE]
> Micro-step components below are normalized as percentages of the native step total duration (`execute_step`).
> To evaluate search bottlenecks specifically, `Neighbor Retrieval within Search` is computed separately
> with the search callback duration ($T_{search}$) as its denominator (M2 median: 22.78%, vs 16.25% of step total).

| Step Component                                    | M1 Median % | M1 Mean % | M2 Median % | M2 Mean % |
| ------------------------------------------------- | ----------- | --------- | ----------- | --------- |
| Update Maintenance (TransactionMgr)               | 2.09%       | 7.13%     | 3.12%       | 11.22%    |
| Search Core (Search excluding Neighbor Retrieval) | 79.53%      | 71.42%    | 67.23%      | 59.76%    |
| Neighbor Retrieval (AuthoritativeGraph)           | 15.74%      | 13.62%    | 16.25%      | 15.94%    |
| Tuple Commit (sink.commit_step)                   | 0.62%       | 0.81%     | 1.38%       | 1.33%     |

### 5.1 Search-Normalized Breakdown (% of Search Duration)

| Search Subcomponent              | M1 Median % | M1 Mean % | M2 Median % | M2 Mean % |
| -------------------------------- | ----------- | --------- | ----------- | --------- |
| Search Core within Search        | 83.24%      | 84.24%    | 77.22%      | 78.09%    |
| Neighbor Retrieval within Search | 16.76%      | 15.76%    | 22.78%      | 21.91%    |

## 6. Engine Counters and Candidate Reduction

| Metric                                  | M1 (Enum-Diff)   | M2 (Bare Event-Triggered) |
| --------------------------------------- | ---------------- | ------------------------- |
| Total Candidates Evaluated              | 455148           | 245055                    |
| Total Events Emitted                    | 37152            | 37152                     |
| Total Stream Steps                      | 828              | 828                       |
| Candidates per Step                     | 549.70           | 295.96                    |
| Events per Step                         | 44.87            | 44.87                     |
| Events / Candidate Eval Ratio           | 0.0816           | 0.1516                    |
| Relative Candidate Reduction (M2 vs M1) | 0.00% (Baseline) | 46.16%                    |

## 7. Bottleneck Identification & P4b Architectural Evaluation

- **M2 Neighbor Retrieval within Search:** 22.78% of search time.
- **M2 Search Core within Search:** 77.22% of search time.
- **Neighbor Retrieval Threshold Exceeded (>= 15%):** `CONFIRMED`.
- **Search Core Dominates (>= 50%):** `CONFIRMED`.

**Verdict:** RECOMMENDATION: R4A_PROFILE_READY_FOR_P4B_REVIEW. Empirical data confirms that Search Core occupies 77.2% and Neighbor Retrieval occupies 22.8% of Bare M2 search time. Both Relaxed NLF (Option A) and Authoritative Graph CSR neighbor retrieval optimization (Option B) address measurable cost centers. Future isolated experiments in Phase P4b must quantify attainable end-to-end speedups without premature locking of combined Option B+A.
