# APX-04 — Runtime Feasibility & Computational Bottleneck Audit

**Document ID:** `docs/apx04_runtime_feasibility.md`
**Status:** Analytical Runtime Specification (`A3_00_READY_FOR_INDEPENDENT_REVIEW_ROUND_2`)
**Associated Issue:** [#20](https://github.com/iamqthanq/Seminar_RapidFlow/issues/20)
**Evaluated Target:** APX-03 C++ Baseline (`rapidflow_apx03_baseline`)
**Commit:** `1eabd3d82e59d29f168cd99f9c5da2bbdc6710f0`

---

## 1. Executive Summary

This document presents the **computational feasibility analysis** of executing the verified APX-03 C++ baseline on temporal graph streams.

The baseline employs **`RECOMPUTE_BASELINE`**: upon each streaming update, it applies the edge modification to the mutable adjacency representation, recomputes the entire accepted match set $M_t$ from scratch via recursive backtracking, and performs a dual-hash-map diff against $M_{t-1}$ to emit the state delta (`ENTERED`, `EXITED`, `SCORE_CHANGED`, `UNCHANGED`).

The critical computational insights identified at Gate A3-00 are:
1. **Initial Match Set Bottleneck ($M_0$):** The cost of enumerating $M_0$ on a graph with $3.11\text{M}$ vertices with uniform vertex labels threatens to overwhelm runtime before the first streaming update is even processed.
2. **Output Cardinality Dominance Hypothesis (`EMPIRICAL_HYPOTHESIS — NOT_MEASURED`):** Full concrete mapping materialization produces an information-theoretic lower bound of $\Omega(|M_t| \cdot k)$ bytes. If $|M_t| = 10^6$, serialization and memory overhead could consume significant execution time. However, the exact proportion of wall-clock time spent on serialization versus candidate exploration remains an empirical hypothesis to be quantified in future profiling rather than an established universal fact.
3. **Recompute Redundancy:** For updates with localized impact, recomputing the entire match set recalculates matches that did not change, suggesting a theoretical target for future incrementalization (Gate A4).

---

## 2. APX-03 Architectural Breakdown

The baseline execution pipeline consists of 13 distinct computational stages:

```
[1. File Ingest] ──> [2. Dense Remap] ──> [3. Native CSR Init] ──> [4. Mutable Adj Init]
                                                                            │
   ┌────────────────────────────────────────────────────────────────────────┘
   ▼
[5. Static M0 Backtrack] ──> [6. Boundary & Conn Eval] ──> [7. M0 Materialization]
   │
   ▼ (For each update t = 1..T)
[8. Stream Update Ingest] ──> [9. Mutable Graph Mutation]
   │
   ▼
[10. Full Recompute Mt] ──> [11. Snapshot Hash Diff] ──> [12. Delta Categorization]
   │
   ▼
[13. JSON Output Serialization]
```

### Stage Cost Breakdown & Bottleneck Hypotheses:
1. **Graph Loading (`graph_load_ms`):** Ingesting `Ndata.graph` ($87.5\,\text{MB}$, $3.11\text{M}$ vertices, $2.85\text{M}$ edges) into RAM.
2. **CSR Initialization (`csr_init_ms`):** RapidFlow native CSR construction.
3. **Mutable Adjacency Initialization (`adj_init_ms`):** Constructing hash maps `std::unordered_map<uint32_t, std::unordered_map<uint32_t, uint32_t>>` for $O(1)$ dynamic edge insertions/deletions. Hash map allocation on $3.11\text{M}$ vertices represents a primary memory driver.
4. **Initial Matching $M_0$ (`initial_matching_ms`):** Backtracking search on $G_0$.
5. **Candidate Generation:** Filtering vertices by label (ineffective on NetFlow due to single vertex label `0`).
6. **Partial Search Expansion:** Branching across candidates; early pruned if partial missing edges $m_{\text{partial}} > \varepsilon_m$.
7. **Complete Candidate Evaluation:** Computing $m(f)$ on all query edges.
8. **External Boundary Evaluation:** Computing $x_{\text{out}}(f)$ via boundary edges.
9. **Connectivity Evaluation:** BFS on induced subgraph $G[S_f]$ to verify single component.
10. **Dynamic Recomputation (`recompute_per_update_ms`):** Rerunning steps 5–9 on $G_t$ after edge insertion/deletion.
11. **Delta Snapshot Diffing (`diff_ms`):** Dual-map comparison between $M_{t-1}$ and $M_t$.
12. **JSON Serialization (`json_serialize_ms`):** Formatting and writing structured output.

---

## 3. Telemetry Specification & Metric Measurement Plan

The following telemetry framework is specified for future execution gates:

| Metric ID | Description | Unit | Source | Collection Method | Implementation Status |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `graph_load_wall_ms` | Time to read graph file into memory | ms | CLI Runner | High-resolution clock | Existing in runner |
| `csr_init_ms` | Native RapidFlow CSR construction | ms | C++ Binary | Internal timer | Proposed (A3-01) |
| `mutable_adj_init_ms` | Mutable adjacency hash map setup | ms | C++ Binary | Internal timer | Proposed (A3-01) |
| `initial_matching_wall_ms` | Full evaluation time of $M_0$ | ms | C++ Binary | Internal timer | Existing (`main.cpp`) |
| `search_steps` | Recursive backtracking call count | count | C++ Binary | Counter `search_steps_` | Existing (`ApxMatcher`) |
| `complete_candidates` | Complete injective assignments | count | C++ Binary | Counter `evaluated_candidates_` | Existing (`ApxMatcher`) |
| `accepted_m0_count` | Accepted matches in $M_0$ | count | C++ Binary | `matches.size()` | Existing (`main.cpp`) |
| `per_update_duration_ms` | Wall time per dynamic update step | ms | C++ Binary | High-resolution timer | Existing in history |
| `delta_entered_count` | Count of mappings entering $M_t$ | count | C++ Binary | `delta.entered.size()` | Existing in history |
| `delta_exited_count` | Count of mappings exiting $M_{t-1}$ | count | C++ Binary | `delta.exited.size()` | Existing in history |
| `delta_score_changed_count` | Count of active score changes | count | C++ Binary | `delta.score_changed.size()` | Existing in history |
| `delta_unchanged_count` | Count of active unchanged matches | count | C++ Binary | `delta.unchanged.size()` | Existing in history |
| `peak_rss_mb` | Maximum resident memory usage | MB | OS Supervisor | `getrusage` / `/proc` | Existing in runner |
| `output_bytes` | Size of emitted JSON payload | bytes | OS Supervisor | `stat(output_path)` | Existing in runner |
| `timeout_flag` | Wall-clock limit exceeded (60s) | bool | OS Supervisor | Subprocess timeout | Existing in runner |
| `resource_limit_flag` | Search step or candidate cap hit | bool | C++ Binary | Exit code 2 trap | Existing (`main.cpp`) |

> [!NOTE]
> All quantitative values for NetFlow full runs remain **`NOT_MEASURED`** at Gate A3-00. Recording fabricated or estimated runtimes without actual execution is strictly prohibited.

---

## 4. Output Cardinality vs. Avoidable Search Overhead

### 4.1. Intrinsic Output Complexity
- In exact COUNT-ONLY matching (such as RapidFlow `EXECUTION_MODE=0` in RF-03), a query may match $16.5 \times 10^6$ times, but the algorithm merely increments a 64-bit scalar counter (`sizeof(uint64_t) = 8` bytes).
- In Model C (APX-01/03), the output contract requires **full enumeration of concrete ordered mapping tuples** $(f(u_0), \dots, f(u_{k-1}))$ along with full 4-element score records $(m, x_{\text{in}}, x_{\text{out}}, \text{connected})$.
- For $10^6$ matches, serializing concrete JSON tuples requires:
  $$\approx 10^6 \times 120\,\text{bytes/mapping} \approx 120\,\text{MB of text}$$
- While the mandatory output lower bound is $\Omega(k |M_t|)$, whether serialization dominates total wall-clock execution time is classified strictly as `EMPIRICAL_HYPOTHESIS — NOT_MEASURED`. It depends heavily on candidate search depth, early pruning efficiency, and query selectivity, and must be empirically measured under instrumented telemetry in future benchmarking rather than asserted as fact.

### 4.2. Avoidable Search Overhead
Avoidable overhead consists of:
- Exploring partial branches that eventually exceed $\varepsilon_m$ or $\deg(v) > \varepsilon_x + k - 1$.
- Re-evaluating unchanged matches during dynamic updates when the updated edge is distant from their embeddings.

---

## 5. Resource Safety & Execution Readiness

### 5.1. Workstation Hardware Profile
- **Host OS:** Ubuntu 24.04.1 LTS (Linux Kernel 7.0.0-42-generic x86_64)
- **CPU:** Intel Core i5-8265U @ 1.60GHz (4 physical cores, 8 threads, max 3.90GHz)
- **Total RAM:** $11.53\,\text{GiB}$
- **Host Floor Requirement:** Available RAM $\ge 4.0\,\text{GiB}$ required prior to benchmark launch
- **Swap:** $4.0\,\text{GiB}$
- **Disk:** $111\,\text{GiB}$ free on NVMe SSD
- **Compilers & Build Tools:** GCC 13.3.0, CMake 3.28.3

### 5.2. Active Memory Enforcement & Safety Architecture
A rigorous distinction is maintained between:
1. **Audit Validity (`AUDIT_PASS`):** Verifies that the workload manifest, hashes, query simple-graph properties, stream provenance, cgroup headroom, and syntax are cryptographically and syntactically sound.
2. **Execution Readiness (`EXECUTION_READINESS = SAFETY_BLOCKED`):** Authorizing process execution of benchmarks is strictly blocked until an enforceable active memory containment harness and safe available RAM conditions are proven.
3. **Memory Concepts & Containment Distinction:**
   - **Virtual Address Space (`RLIMIT_AS`):** Capping `RLIMIT_AS` limits the virtual address space (`VIRT`), which includes memory-mapped files and uncommitted pages. It does **NOT** cap physical resident memory (`RSS`). Claiming that `RLIMIT_AS` guarantees a resident memory cap is technically false.
   - **Resident Set Size (RSS) Polling:** Polling `/proc/[pid]/statm` or `getrusage` periodically is an observational and reactive mechanism, **NOT** hard memory containment. Memory spikes occurring between polling intervals can trigger kernel thrashing or system freeze before the monitoring thread can intervene.
   - **cgroup Headroom Inspection:** Preflight implements read-only inspection of cgroup v2 (`memory.max`, `memory.current`, and ancestor slices) to compute effective available headroom ($\text{headroom} = \max(0, \text{memory.max} - \text{memory.current})$). When constrained, $\text{effective\_ram} = \min(\text{host\_available}, \text{headroom})$.
   - **Active Enforceable Containment:** Requires kernel-level enforcement such as a dedicated Linux `cgroup v2` slice with fail-closed kernel out-of-memory killing (`memory.max = 3GiB`), or an external supervisor with sub-millisecond hardware-level traps.
   - **Current Implementation State:** In Gate A3-00, no active benchmark runner has been implemented or executed. Therefore, active memory containment is classified as:
     $$\text{MEMORY\_ENFORCEMENT} = \text{NOT\_VERIFIED}$$
     $$\text{EXECUTION\_READINESS} = \text{SAFETY\_BLOCKED}$$

### 5.3. Mandatory Operational Safeguards for Future Execution
- **Wall-Clock Timeout:** $\le 60.0\,\text{s}$ hard cutoff per case.
- **Active Memory Budget:** $\le 3.0\,\text{GiB}$ resident budget.
- **Available RAM Floor:** $\ge 4.0\,\text{GiB}$ available memory required before starting any run.
- **Single Process Execution:** No concurrent benchmark execution.
- **Combinatorial Budget:** $\le 10^7$ candidate evaluations, $\le 10^7$ search exploration steps.

### 5.4. Formal Failure Classification Rubric
To eliminate ambiguity, every future benchmark attempt is mapped to exactly one terminal state:

| State Code | Meaning | Handling & Provenance Action |
| :--- | :--- | :--- |
| `PASS_COMPLETE` | All update steps finished within budgets | Valid result; record metrics in summary |
| `ZERO_RESULT_COMPLETE` | Execution completed, but accepted matches = 0 | Valid result; record as true negative |
| `TIMEOUT` | Subprocess exceeded 60s wall clock | Record timeout; do NOT treat as zero-match |
| `RESOURCE_LIMIT` | Exceeded candidate or search-step budget (exit 2) | Record bound exhaustion; fail closed |
| `OOM` | Process killed by OS or memory ceiling exceeded | Record out-of-memory failure; preserve logs |
| `ERROR` | Non-zero exit code (exit 1), syntax, graph exception | Record error details from stderr |
| `INCOMPLETE` | Stream updates partially processed before abort | Reject run; do NOT include partial counts |
| `INVALID_INPUT` | Input graph or query malformed | Preflight rejection; never execute |
| `SKIPPED_SAFETY` | Preflight floor $< 4.0\,\text{GiB}$ available RAM | Safe abort before execution starts |
