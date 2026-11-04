# RF-02 Mapping Feasibility Study: Embedding-Set Export (Gate G1-B)

**Author:** AI Coding Agent (Seminar_RapidFlow)  
**Date:** 2026-11-04  
**Context:** Gate G1 — Independent Correctness Verification (Issue #7)  
**Status:** **G1-B DECLARED UNVERIFIED / BLOCKED** (Awaiting explicit user authorization)

---

## 1. Executive Summary

Roadmap Issue #1 states that Continuous Subgraph Matching (CSM) requires tracking the exact sets of added and removed embeddings after each dynamic graph update:
$$\Delta_t^+ = \mathcal{M}(Q, G_t) \setminus \mathcal{M}(Q, G_{t-1}), \quad \Delta_t^- = \mathcal{M}(Q, G_{t-1}) \setminus \mathcal{M}(Q, G_t)$$

The independent Python Oracle developed in RF-02 (`scripts/rf02_oracle.py`) successfully produces these exact sets $\Delta_t^+$ and $\Delta_t^-$ for all updates.
However, **the pristine RapidFlow C++ baseline cannot export embedding mappings**.
Matching count equality alone ($\Delta_k^{\text{inferred}} = |\Delta_k^{\text{oracle}}|$) does **not** prove mapping-set equality ($\mathcal{M}_{\text{actual}} = \mathcal{M}_{\text{oracle}}$).

This report investigates the architectural obstacles within RapidFlow, defines the necessary design for a separate diagnostic mapping extractor, and confirms that **modifying C++ baseline code is blocked** without separate user authorization under repository rules.

---

## 2. Deep Architectural Analysis of Pristine RapidFlow

A rigorous audit of the pinned RapidFlow source (`baselines/rapidflow/source/` at commit `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`) reveals two fundamental architectural reasons why pristine RapidFlow does not and cannot emit embedding mappings without instrumentation:

### 2.1. Leaf-Level Count Shortcut (`search_engine.cpp:161`)
In `baselines/rapidflow/source/streaming/search_engine.cpp`:
```cpp
// search_engine.cpp lines 154-169
if (local_idx_[next_depth].second == 0) {
    visited_[v] = false;
} else if (next_depth == target_depth - 1) {
    result_count += local_idx_[next_depth].second; // <-- LEAF SHORTCUT
    if (result_count >= target_number) {
        ...
        return result_count;
    }
}
```
At the final depth of the backtracking search tree (`next_depth == target_depth - 1`), RapidFlow does **not** instantiate the embedding tuple or assign the final vertex to the candidate embedding. Instead, it directly reads `local_idx_[next_depth].second` (the count of valid candidates for the last vertex) and adds this integer scalar to `result_count`.

Because leaf candidates are never individually bound into the `embedding_` buffer during execution, individual match instances never exist in memory.

### 2.2. Automorphism Factorization (`streaming_engine.cpp:143` & `181`)
In `baselines/rapidflow/source/streaming/streaming_engine.cpp`:
```cpp
// streaming_engine.cpp lines 142-144 & 180-182
uint64_t local_result_count = sm_.search_on_reduced_query(query_graph, *orders, lvm_, gvm_);
result_count += local_result_count * automorphism_size;
```
RapidFlow groups symmetric query configurations using query automorphisms ($\operatorname{Aut}(Q)$). Rather than enumerating symmetric embeddings, RapidFlow executes the search on an automorphism-reduced query and algebraically multiplies `local_result_count` by the integer `automorphism_size`.

For example, on a triangle query ($K_3$) where $|Aut(K_3)| = 6$, RapidFlow searches a representative orientation and multiplies the result by 6. The other 5 symmetric permutations are never generated or visited.

### 2.3. Output Contract is Strictly Scalar Count
In `main.cpp` and `streaming_engine.cpp:411`:
```cpp
printf("Result count: %zu\n", result_count_);
```
The standard binary prints only scalar summary counters to stdout. Setting `EXECUTION_MODE=1` in `configuration/config.h` does not output mappings; it was merely an internal author development flag that did not alter the core search engine loops.

---

## 3. Feasibility of a Separate Diagnostic Mapping Extractor

To enable Gate G1-B verification, a separate diagnostic instrumented tool (e.g. `rapidflow_diagnostic.out`) could be constructed.

### 3.1. Architectural Requirements for Diagnostic Instrumentation
1. **Loop Unrolling at Leaf Depth:**
   Replace the scalar addition in `search_engine.cpp:161` with a loop iterating over all candidates in `local_candidates_store_[next_depth]`:
   ```cpp
   for (uint32_t i = 0; i < local_idx_[next_depth].second; ++i) {
       uint32_t last_cand = local_candidates_store_[next_depth][i];
       embedding_[last_vertex] = last_cand;
       emit_mapping(embedding_);
   }
   ```
2. **Automorphism Expansion:**
   In `streaming_engine.cpp`, rather than multiplying by `automorphism_size`, iterate over each automorphism permutation $\sigma \in \operatorname{Aut}(Q)$ to expand the base embedding into its $|Aut(Q)|$ concrete symmetric embeddings.
3. **Canonical Normalization:**
   Reorder the internal matching-order representation of the embedding into canonical sorted query-vertex order $(f(0), f(1), \dots, f(k-1))$ and emit each tuple as structured text or binary records.

### 3.2. Mandatory Boundary Separation
If such a tool is authorized:
- It **must NOT** modify `baselines/rapidflow/source/` (which must remain pristine at commit `2ea4f4c3...`).
- It must live in a separate tree (e.g. `diagnostics/rapidflow_mapping/`).
- It must never be used for timing or memory benchmarks, as mapping enumeration incurs substantial I/O overhead.

---

## 4. Current Governance Status and Verdict

Under `AGENTS.md` Rule 5:
> *"Keep baselines/rapidflow/source/ pristine at pinned 2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1."*
And Issue #7 mandate:
> *"STOP AND ASK USER BEFORE ANY DIAGNOSTIC C++ MODIFICATION/INSTRUMENTATION. Do not edit baselines/rapidflow/source. Without user authorization/feasible mapping extractor, declare G1-B BLOCKED/UNVERIFIED and overall G1 PARTIAL/INCONCLUSIVE."*

### Final Verdict:
1. **Gate G1-A (COUNT-CORRECTNESS):** **PASS** (100% agreement across all 31 dynamic update steps).
2. **Gate G1-B (MAPPING-CORRECTNESS):** **UNVERIFIED / BLOCKED** (Pristine baseline does not emit mappings; diagnostic C++ instrumentation cannot proceed without explicit user approval).
3. **Gate G1 OVERALL:** **PARTIAL / INCONCLUSIVE** (`COUNT-PASS / MAPPING-UNVERIFIED`).
