# Phase P4a Native Profiling & Baseline Measurement Report

## 1. Executive Summary

- **Total Workload Pairs Analyzed:** 90
- **Median Streaming Speedup ($S_{stream} = T_{M1} / T_{M2}$):** 1.1203x
- **Median Outer Wall Speedup ($S_{outer} = T_{M1} / T_{M2}$):** 1.0419x
- **Median Search Speedup ($S_{search} = T_{M1} / T_{M2}$):** 2.1553x
- **M2 Neighbor Retrieval within Search:** 22.60%
- **P4b Recommendation:** OPTION B & A: Authoritative Graph Neighbor Retrieval Optimization paired with Relaxed NLF. Profiling demonstrates get_neighbors() accounts for significant search overhead due to dynamic vector allocation and sorting. Combining CSR neighbor retrieval with Relaxed NLF will yield maximal speedup.

## 2. Paired Speedup Distribution (M1 vs Bare M2)

| Metric Scope          | Count | Min   | Q1 (25%) | Median | Q3 (75%) | Max   | Mean  | StdDev |
| --------------------- | ----- | ----- | -------- | ------ | -------- | ----- | ----- | ------ |
| Streaming Exec (Core) | 90    | 0.83x | 1.02x    | 1.12x  | 1.35x    | 2.03x | 1.21x | 0.25   |
| Outer Wall (Process)  | 90    | 0.77x | 0.98x    | 1.04x  | 1.11x    | 1.44x | 1.05x | 0.11   |
| Cumulative Search     | 90    | 1.00x | 1.32x    | 2.16x  | 3.20x    | 9.83x | 2.60x | 1.69   |

## 3. Stratified Speedup Breakdown

### 3.1 By Query Topology and Tolerance $k$

| Query & Tolerance | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| ----------------- | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| diamond_k0        | 6     | 1.02x | 1.02x | 1.06x  | 1.12x | 1.52x | 1.13x |
| diamond_k1        | 24    | 0.94x | 1.02x | 1.10x  | 1.21x | 1.41x | 1.12x |
| k4_k0             | 9     | 0.87x | 0.99x | 1.09x  | 1.12x | 1.36x | 1.07x |
| k4_k1             | 27    | 0.83x | 1.03x | 1.16x  | 1.50x | 1.91x | 1.25x |
| k4_k2             | 24    | 0.90x | 1.11x | 1.22x  | 1.47x | 2.03x | 1.32x |

### 3.2 By Label Regime

| Label Regime | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| ------------ | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| mixed        | 33    | 0.87x | 1.01x | 1.07x  | 1.12x | 2.03x | 1.10x |
| uniform      | 57    | 0.83x | 1.09x | 1.20x  | 1.47x | 1.95x | 1.27x |

### 3.3 By Graph Stream Topology

| Topology  | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| --------- | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| cycle     | 18    | 0.92x | 1.07x | 1.14x  | 1.46x | 2.03x | 1.26x |
| hub       | 21    | 0.94x | 1.02x | 1.09x  | 1.16x | 1.26x | 1.09x |
| micro     | 45    | 0.83x | 1.02x | 1.13x  | 1.48x | 1.95x | 1.25x |
| scalefree | 6     | 1.00x | 1.07x | 1.18x  | 1.23x | 1.33x | 1.16x |

### 3.4 By Event Status

| Event Status  | Pairs | Min   | Q1    | Median | Q3    | Max   | Mean  |
| ------------- | ----- | ----- | ----- | ------ | ----- | ----- | ----- |
| event_bearing | 45    | 0.94x | 1.09x | 1.20x  | 1.38x | 1.95x | 1.25x |
| zero_event    | 45    | 0.83x | 1.00x | 1.09x  | 1.13x | 2.03x | 1.16x |

## 4. Macro-Stage Execution Breakdown (% of Total Wall Time)

| Stage                          | M1 Median % | M1 Mean % | M2 Median % | M2 Mean % |
| ------------------------------ | ----------- | --------- | ----------- | --------- |
| Input Parsing                  | 40.21%      | 40.83%    | 46.97%      | 44.13%    |
| Initial Graph CSR Construction | 0.74%       | 0.82%     | 0.79%       | 0.74%     |
| Query Graph Index Preparation  | 0.33%       | 0.33%     | 0.37%       | 0.36%     |
| Streaming Execution Loop       | 48.15%      | 49.34%    | 44.09%      | 45.70%    |

## 5. Micro-Step Stage Execution Breakdown (% of Step Total Time)

| Step Component                              | M1 Median % | M1 Mean % | M2 Median % | M2 Mean % |
| ------------------------------------------- | ----------- | --------- | ----------- | --------- |
| Update Maintenance (TransactionMgr)         | 2.07%       | 6.74%     | 3.20%       | 10.93%    |
| Search Core (Candidate Evaluation)          | 79.41%      | 71.52%    | 67.60%      | 59.22%    |
| Neighbor Retrieval (AuthoritativeGraph)     | 15.54%      | 13.54%    | 16.35%      | 15.83%    |
| Tuple Sink (Canonical Sort & Deduplication) | 0.63%       | 0.88%     | 1.30%       | 1.29%     |

## 6. Engine Counters and Pruning Metrics

| Metric                     | M1 (Enum-Diff) | M2 (Bare Event-Triggered) |
| -------------------------- | -------------- | ------------------------- |
| Total Candidates Evaluated | 455148         | 245055                    |
| Total Events Emitted       | 37152          | 37152                     |
| Total Stream Steps         | 828            | 828                       |
| Candidates per Step        | 549.70         | 295.96                    |
| Events per Step            | 44.87          | 44.87                     |
| Pruning Efficiency %       | 91.84%         | 84.84%                    |

## 7. Bottleneck Identification & P4b Architectural Decision

- **M2 Neighbor Retrieval within Search:** 22.60% of search time.
- **M2 Search Core within Search:** 77.40% of search time.
- **Significant Bottleneck Verdict:** `CONFIRMED`.

**Conclusion & Path Forward:** OPTION B & A: Authoritative Graph Neighbor Retrieval Optimization paired with Relaxed NLF. Profiling demonstrates get_neighbors() accounts for significant search overhead due to dynamic vector allocation and sorting. Combining CSR neighbor retrieval with Relaxed NLF will yield maximal speedup.
