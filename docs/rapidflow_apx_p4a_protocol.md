# Experimental Protocol: Phase P4a Native Profiling, Baseline Measurement & Evidence-Driven Pilot

**Document ID:** `docs/rapidflow_apx_p4a_protocol.md`  
**Milestone:** Phase P4a  
**Parent Issue:** [Issue #36](https://github.com/iamqthanq/Seminar_RapidFlow/issues/36)  
**Target Integration Branch:** `QuangThang`  
**Pre-P4 Base Commit:** `b5a5fe44fe594f9c550dcc44f04b9f4fc9a54245`  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)  
**Status:** ACTIVE PROTOCOL SPECIFICATION  

---

## 1. Executive Protocol Objectives

Phase P4a establishes the empirical measurement baseline for the RapidFlow-APX native event search engines (`ApxM1EnumEngine` and `ApxM2EventEngine`) under strict Domain D-R conditions.

The protocol provides definitive, reproducible evidence to answer three primary research questions:
1. **Stage Breakdown:** Where does Bare M2 consume execution time during incremental graph updates (update maintenance, search core, neighbor retrieval, tuple sink)?
2. **Speedup Distribution:** Does Bare M2 achieve wall-clock speedup over M1-enum across controlled topologies, label regimes, and tolerance thresholds $k$?
3. **P4b Optimization Justification:** Does the empirical profiling evidence justify implementing Relaxed NLF (or alternative optimizations like CSR neighbor retrieval caching) in Phase P4b?

---

## 2. Scientific Scope & Domain D-R Invariants

### 2.1 Theoretical Boundaries
All workloads are bound strictly to **Domain D-R** ($0 \le k < \lambda(Q)$):
- **Diamond Query:** $V(Q) = 4, E(Q) = 5$ (chord $(1, 2)$). Edge connectivity $\lambda(Q) = 2$.
  - Tolerances evaluated: $k = 0$ (exact control) and $k = 1$ (single-missing event leaf).
  - Condition verified: $k < 2$.
- **$K_4$ Query:** $V(Q) = 4, E(Q) = 6$ (complete 4-clique). Edge connectivity $\lambda(Q) = 3$.
  - Tolerances evaluated: $k = 0$ (exact control), $k = 1$, and $k = 2$.
  - Condition verified: $k < 3$.

### 2.2 Workload Topologies & Regimes
The pilot matrix spans 30 bounded canonical workloads (`configs/rapidflow_apx_p4a_manifest.json`):
- **Topologies:**
  1. `micro`: Minimal 8-vertex data graphs for boundary verification.
  2. `cycle`: Sparse cyclic 12-vertex graphs with low vertex degree.
  3. `hub`: Dense hub 16-vertex graphs with high-degree central star topologies.
  4. `scalefree`: Scale-free power-law 16-vertex graphs with skewed degree distribution.
  5. `zero_event`: Stream updates causing topological mutations but zero query event emissions.
  6. `delete_heavy`: Streams containing multiple edge deletions testing state rollbacks and reverse transitions.
- **Label Regimes:**
  1. `uniform`: All vertices and edges share label 1, maximizing structural ambiguity and search space.
  2. `mixed`: Diverse vertex labels $\{1, 2, 3\}$ and edge labels $\{0, 1\}$, testing label-based pruning efficiency.

---

## 3. Reassessment of the 7-Step Plan vs Google Docs Proposal

Prior informal proposals (e.g. Google Docs) suggested running arbitrary 180 configurations scaling up to $N = 256$ vertices. In Phase P4a, this plan was critically reassessed based on empirical data from previous milestones:

1. **DSTAR-03 Historical Precedent:** In DSTAR-03, NetFlow probes produced 0 events because $N \le 8$ was too small, while 37 out of 65 workloads finished in $<0.3$ ms, leading to high measurement variance dominated by OS scheduling and Python interpreter noise.
2. **Native C++ Performance Scale:** Native execution in C++ is roughly $100\times$ faster than Python prototypes. Unbounded graph sizes without calibrated stream updates risk either immediate timeout or trivial execution.
3. **Rigorous Correctness Verification:** Ground truth cross-validation against the independent Python `DStarDynamicOracle` requires graph bounds ($N \le 24$) where snapshot enumeration is provably complete and terminates in reasonable time.
4. **Controlled Design:** The 30 canonical workloads in `rapidflow_apx_p4a_manifest.json` yield exactly 12,384 ground truth event occurrences, ensuring every single run exercises genuine event emissions, zero-event pruning, and edge deletions under complete Oracle verification.
5. **Statistical Power:** Executing 30 distinct workloads in 3 paired repetitions (180 total runs) with alternating AB/BA execution provides robust paired speedup distributions while fully eliminating cache ordering bias.

---

## 4. Measurement Methodology & Experimental Safeguards

### 4.1 Paired AB/BA Execution
To eliminate systematic bias from warm CPU caches, dynamic frequency scaling, or Linux kernel page cache effects:
- Repetition 0: M1 executed first, then M2 ($A \to B$).
- Repetition 1: M2 executed first, then M1 ($B \to A$).
- Repetition 2: M1 executed first, then M2 ($A \to B$).

### 4.2 Disposable Warm-Up Replay
Prior to each timed execution slot, a disposable untimed warm-up run is executed on identical workload data to warm filesystem buffers and binary instruction caches. The engine state is then completely discarded, and a fresh authoritative graph instance is initialized from $G_0$.

### 4.3 Native Nanosecond Monotonic Profiling
The C++ harness (`tests/native/rapidflow_apx_engines/src/main.cpp`) uses `std::chrono::steady_clock` to record monotonic nanosecond intervals across two distinct scopes:
1. **Macro Stages (Run-Level):**
   - `input_parsing_ns`: Deserializing JSON workload input and validating schema.
   - `graph_init_ns`: Constructing initial graph CSR and overlay structures.
   - `query_prep_ns`: Building query plan, Edmonds-Karp min-cut, and variant structures.
   - `streaming_exec_ns`: Total time spent in the streaming update loop.
   - `total_wall_ns`: End-to-end process execution wall time.
2. **Micro Stages (Step-Level Cumulative):**
   - `update_maintenance_ns`: CSR/overlay edge insertion or deletion in `ApxTransactionManager`.
   - `search_core_ns`: Candidate extension, edge commitment verification, and pruning.
   - `neighbor_retrieval_ns`: Time spent inside `ApxAuthoritativeGraph::get_neighbors(u)`.
   - `tuple_sink_ns`: Canonical tuple sorting, vertex injectivity check, and deduplication.
   - `step_total_ns`: Total elapsed time for the streaming step.

### 4.4 Observer Overhead Minimization
To prevent timing instrumentation from perturbing innermost DFS execution loops:
- Individual edge lookups and vertex label comparisons are counted using software counters (`edge_lookups`, `candidate_evaluations`).
- High-resolution timers (`steady_clock::now()`) are restricted to subroutine boundaries (`get_neighbors()`, `execute_step()`, and stage transitions).
- Zero memory allocation occurs during profiling measurement.

### 4.5 Invariance & Schema Isolation
- **Behavioral Invariance:** Profiling flags (`PROFILE_ON` vs `PROFILE_OFF`) produce bit-for-bit identical tuple outputs, step counts, and counter values.
- **Schema Isolation:** When `--profile` is omitted, the CLI output strictly complies with P3 schema 1.1.0 with zero profiling keys. When `--profile` is supplied, profiling data is namespaced under `"profiling"`.

---

## 5. Pre-registered Hypotheses & P4b Decision Criteria

| Hypothesis | Description | Confirmation Condition | Architectural Consequence |
| :--- | :--- | :--- | :--- |
| **$H_1$** | Bare M2 achieves wall-clock streaming speedup over M1 in streaming regimes. | Paired speedup $S_{\text{stream}} = T_{M1} / T_{M2} > 1.0\times$ (median). | Validates event-driven direct search over variant recomputation. |
| **$H_2$** | Neighbor retrieval in `ApxAuthoritativeGraph` is a major search bottleneck. | $\text{neighbor\_retrieval\_ns} / \text{search\_ns} \ge 15.0\%$. | Requires CSR neighbor representation optimization or neighbor caching in P4b. |
| **$H_3$** | Candidate evaluation dominates search time in uniform label regimes. | $C_{\text{eval}} \gg C_{\text{match}}$ and search core $> 50\%$ of step time. | Justifies Relaxed NLF implementation for candidate filtering in P4b. |

### Decision Pathways for Phase P4b:
- **Option A (Relaxed NLF):** If candidate evaluation dominates and neighbor retrieval is modest ($<15\%$).
- **Option B & A (Hybrid Optimization):** If neighbor retrieval exceeds $15\%$ and candidate evaluations are high, implement both Authoritative Graph neighbor optimization and Relaxed NLF.
- **Option C (Pruning Reordering):** If commitment verification failure dominates.
- **Option D (Pivot / Re-scope):** If M2 cannot achieve speedup over M1 due to fundamental DFS overhead.
