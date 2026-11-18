# D* Implementation and Validation Plan — Roadmap from M0 to M2

**Date:** 2026-11-18 (Updated following 18/11/2026 Architectural Sync)
**Context:** Seminar RapidFlow / CSM — D* Feasibility Route
**Current Phase:** DSTAR-03 (Issue #26) Complete
**Next Phase:** DSTAR-04 (M2 Optimizations: NLF, $\delta'$, candidate ordering or C++ Port)

---

## 1. Executive Roadmap Overview

The D* research direction evaluates whether single-pass event-anchored approximate continuous subgraph matching (candidate algorithm **M2-Event-k**) can outperform decomposition baselines (**M1-count** and **M1-enum-Exact-k**) under strict Edge Recall and S2 connectivity.

Following the **18/11/2026 architectural decision**, M1-count (counting via inclusion-exclusion / Möbius inversion) has been decoupled and is no longer a blocking prerequisite for event enumeration. Direct event enumeration via M1-enum-Exact-k and bare M2-Event-k constitutes the primary path.

```text
Phase 1: M0 Oracle & Correctness Foundation [COMPLETED IN DSTAR-01 / Issue #22]
   │  ├── docs/dstar_problem_contract_v1.md
   │  ├── scripts/dstar_oracle.py (Independent M0, stdlib-only)
   │  ├── tests/fixtures/dstar01/ (Hand cases A–L)
   │  ├── tests/test_dstar_legacy_compatibility.py (56 configs, 224 updates PASS)
   │  └── reports/dstar01/correctness_summary.json
   │
   ▼
Phase 2: Event Enumeration: M1-enum, Bare M2 & Tuple Correctness [COMPLETED IN DSTAR-02 / Issue #24]
   │  ├── docs/dstar02_engine_contract.md (Frozen engine contract, Event Boundary Lemma)
   │  ├── scripts/dstar_event_common.py (Shared primitives, EventSink with zero-dup assertions)
   │  ├── scripts/dstar_m1_enum.py (Anchored Exact variants Q - D, R(f) == D)
   │  ├── scripts/dstar_m2_event.py (Bare M2 DFS: PRESENT/MISSING branches, deferred vertices)
   │  ├── scripts/dstar_event_cli.py (CLI interface with exit codes 0/1/2)
   │  ├── tests/fixtures/dstar02/corpus_840/ (840 reference cases, 8002 truth events)
   │  ├── tests/test_dstar02_engines.py (840 corpus, 56 streams x 12 updates, 23 hand cases)
   │  └── reports/dstar02/correctness_summary.json & docs/dstar02_validation_report.md
   │
   ▼
Phase 3: S4 Tiny Pilot — Paired M1/M2 Evaluation [COMPLETED IN DSTAR-03 / Issue #26]
   │  ├── docs/dstar03_pilot_protocol.md (Frozen 65-workload protocol, COLLECT_VERIFY_SORT_V1)
   │  ├── configs/dstar03_pilot_manifest.json (40 synthetic, 5 witness, 20 NetFlow probes)
   │  ├── scripts/generate_dstar03_workloads.py (Bounded 2-pass streaming, deterministic RNG)
   │  ├── scripts/dstar03_resources.py (RLIMIT_AS 512 MiB, 60s timeout, process group isolation)
   │  ├── scripts/dstar03_pilot_worker.py (Warm-up reset, perf_counter_ns engine-call timing)
   │  ├── scripts/run_dstar03_pilot.py (Resumable durable journal ledger, parity-offset order)
   │  ├── scripts/summarize_dstar03_pilot.py (Reconciles 390 slots, noise and coverage tagging)
   │  ├── reports/dstar03/attempts.jsonl, pilot_summary.json, pilot_results.csv
   │  └── docs/dstar03_pilot_report.md (390/390 completed, M0 verified, M2 ~34% work reduction)
   │
   ▼
Phase 4: M2 Algorithmic Optimizations & C++ Implementation [UPCOMING DSTAR-04]
   │  ├── Static Neighbor Label Frequency (NLF) and Dynamic delta' filtering
   │  ├── Candidate ordering and failure-driven branch pruning
   │  └── C++ native implementation of M1-enum and optimized M2
   │
   ▼
Phase 5: Full Workload Benchmarking & Thesis Evaluation [UPCOMING DSTAR-05]
      ├── Large dynamic graphs & NetFlow workload evaluation
      ├── Microbenchmarks: latency, work operations, RSS, scalability
      └── Final comparative assessment: M1-enum vs optimized M2
```

---

## 2. Completed Milestones

### 2.1. Foundation: DSTAR-01 (Issue #22)
1. **Working Problem Contract (`docs/dstar_problem_contract_v1.md`)**:
   - Locked definitions for query $Q$, data graph $G$, injective mapping $f$, and Edge Recall $S_E(f)$.
   - Explicit specification of S2 connectivity ($Q[E_Q \setminus R(f)]$ spanning connected).
   - Domain safety bound: $0 \le k < \lambda(Q)$, where $\lambda(Q)$ is global edge connectivity.
   - Event semantics: only `ENTERED` and `EXITED`; no `SCORE_CHANGED`.
2. **Independent M0 Oracle (`scripts/dstar_oracle.py`)**:
   - Pure Python 3 stdlib, exhaustive ordered permutation search, strict type validation.
3. **Legacy Model C Compatibility**:
   - 56 configurations, 224 dynamic updates, 100% agreement with legacy APX-02 Oracle.

### 2.2. Event Enumeration: DSTAR-02 (Issue #24)
1. **Engine Contract & Primitives (`docs/dstar02_engine_contract.md`, `scripts/dstar_event_common.py`)**:
   - Event Boundary Lemma: $|R_{\text{after}}| == k$ for INSERT, $|R_{\text{before}}| == k$ for DELETE.
   - Canonical tuple convention: ascending sorted order of original query vertex IDs.
   - Output sink with strict zero-duplicate assertion upon finalization.
2. **M1-enum-Exact-k Baseline (`scripts/dstar_m1_enum.py`)**:
   - Decomposes approximate matching into exact searches across all $\binom{m-1}{k}$ variant queries $Q - D$.
   - Strict equivalence filtering ($R(f) == D$) guarantees zero duplicate emissions.
3. **Bare M2-Event-k Engine (`scripts/dstar_m2_event.py`)**:
   - Anchored DFS with PRESENT and MISSING branches.
   - Deferral of vertex assignments on MISSING branch.
   - Two-endpoint commitment verification.
   - Empty frontier fail-closed guard (`InvariantViolationError` when $k < \lambda(Q)$ invariant is broken).
4. **Empirical Verification**:
   - Corpus 840: All 840 reference cases (8,002 truth events across 330 event cases) matched 100% across M0, M1, and M2 with 0 raw duplicates.
   - 56-Stream Matrix: 56 configurations $\times$ 12 updates = 672 updates evaluated with continuous step agreement and active-set reconstruction ($M_t = (M_{t-1} \setminus \text{exited}) \cup \text{entered} == M_0(G_t)$).
   - AST Independence Audit: Zero imports of oracle or legacy modules in production engines.

---

## 3. Specifications for Future Gates (DSTAR-03–05)

### 3.1. DSTAR-03: M2 Optimizations
- **Static NLF**: Prune query-data vertex candidate pairs using neighbor label frequency multisets.
- **Dynamic NLF $\delta'$**: Incremental maintenance of data vertex NLF upon edge insertions/deletions.
- **Candidate Ordering**: Dynamically select frontier edges to minimize branching factor.
- **Invariant**: All optimizations must preserve 100% tuple-level equivalence against M0 Oracle and M1-enum baseline.

### 3.2. DSTAR-04: Local View Integration & C++ Engine
- Inspect and adapt RapidFlow's Local View data structure for approximate event traversal.
- Port M1-enum and M2-Event-k to C++ adhering to RapidFlow binary interface conventions.
- Confirm raw ordered count and signed delta semantics under C++.

### 3.3. DSTAR-05: Benchmarks and Novelty Evaluation
- Comprehensive benchmark against NetFlow workloads and synthetic graph families ($C_6, K_{3,3}$).
- Measure latency, memory RSS, work counters (edge lookups, candidate extensions, branch decisions).
- Publish truthful empirical results determining the viability and novelty of M2 over M1.
