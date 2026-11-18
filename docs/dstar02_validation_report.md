# DSTAR-02 Validation Report — D* Event Enumeration Engines & Independent Tuple Correctness

**Issue:** [#24](https://github.com/iamqthanq/Seminar_RapidFlow/issues/24) — `DSTAR-02 — D* event enumeration: M1-enum baseline, bare M2 và independent tuple correctness`
**Date:** 2026-11-18
**Author:** Research Implementation Engineer (Antigravity)
**Status:** READY_FOR_INDEPENDENT_REVIEW
**Base Commit:** `e6cc1dba15955ce9f7f05ec0ca2a1d749181218c` (QuangThang)
**Feature Branch:** `feature/rf24-dstar02-event-engines`
**Target Integration Branch:** `QuangThang` (**never main**)

---

## 1. Executive Summary

This report delivers the implementation, verification evidence, and correctness guarantees for **DSTAR-02 (Issue #24)**.
Following the 18/11/2026 algorithm design (Section 7, Appendices G.1–G.3 & H of the project design document), DSTAR-02 transitions D* from static snapshot oracle foundations to **direct incremental event enumeration** under the Edge Recall metric within budget $k < \lambda(Q)$.

### 1.1. Delivered Deliverables (S0–S3 Scope)
1. **Repository Orientation Synchronized**: Updated project governance and architectural orientation to reflect direct event enumeration as the primary feasibility route. M1-count is decoupled and no longer blocks M1-enum/M2.
2. **Frozen Engine Contract (`docs/dstar02_engine_contract.md`)**: Formally specified graph and update schemas, canonical query tuple order (`sorted(V_Q)`), snapshot timing, the Event Boundary Lemma ($|R| == k$), shared primitives, and work counters.
3. **Shared Primitives (`scripts/dstar_event_common.py`)**: Zero-dependency module providing `DStarEventGraph`, `DStarEventUpdate`, `EventCounters`, `EventResult`, and `EventSink` with built-in zero-duplicate verification assertions.
4. **M1-enum-Exact-k Baseline (`scripts/dstar_m1_enum.py`)**: Standalone engine decomposing approximate continuous matching into $\binom{m-1}{k}$ anchored exact variant queries $Q - D$.
5. **Bare M2-Event-k Engine (`scripts/dstar_m2_event.py`)**: Standalone engine implementing direct depth-first search on $Q$ branching on PRESENT vs MISSING edges, deferring vertex mapping, checking two-endpoint commitments, and failing closed if an empty frontier is encountered.
6. **Unified CLI (`scripts/dstar_event_cli.py`)**: Supports `--engine m1|m2`, `--query`, `--graph`, `--stream`, `--k`, `--max-work`, `--output`, with strict exit codes (0: SUCCESS, 1: ERROR, 2: RESOURCE_LIMIT).
7. **Complete Verification Corpus & Suite**:
   - **Corpus 840**: Ingested all 840 independent validation cases from `thuc_nghiem_m2_2026-11-18.zip` (8,002 truth event tuples across 330 event-triggering cases).
   - **56-Stream Stateful Matrix**: Executed 56 dynamic streams across $C_4$, Diamond, and $K_4$ (672 updates total) with step-by-step M1 == M2 == M0 agreement and active-set reconstruction.
   - **23 Hand & Adversarial Cases**: Unit-tested edge and vertex label variations, automorphisms, non-induced chords, negative IDs, reversed endpoints, and domain boundaries.
   - **Independence AST Audit**: Programmatically verified that production modules contain ZERO imports or calls to `dstar_oracle.py` or legacy modules.
   - **Fail-Closed Guards**: Verified `InvariantViolationError` on empty frontier hits and `ResourceLimitError` on cap breaches.
8. **Automated Evidence Generator (`scripts/generate_dstar02_report.py`) & Tests (`tests/test_dstar02_report.py`)**: Produced `reports/dstar02/correctness_summary.json` with 100% PASS across all 7 verification stages.

---

## 2. Core Mathematical Principles & Event Boundary Lemma

### 2.1. Mathematical Foundations
- **Simple Undirected Labeled Graphs**: $Q = (V_Q, E_Q)$ and $G = (V_G, E_G)$ with vertex labels $L_V$ and edge labels $L_E$.
- **Injective Mapping**: $f: V_Q \to V_G$ with $\forall u \ne v, f(u) \ne f(v)$ and $L_{V,G}(f(u)) = L_{V,Q}(u)$.
- **Missing Edge Set**:
  $$R(f) = \left\{ \{u, v\} \in E_Q \mid \{f(u), f(v)\} \notin E_G \lor L_{E,G}(\{f(u), f(v)\}) \ne L_{E,Q}(\{u, v\}) \right\}$$
- **Contractual Domain**:
  $$0 \le k < \lambda(Q)$$
  where $\lambda(Q)$ is the global edge-connectivity of $Q$. Under this condition, $Q[E_Q \setminus R(f)]$ is guaranteed to be spanning and connected (S2 connectivity).
- **Canonical Output Tuple**:
  $$T(f) = (f(u_1), f(u_2), \dots, f(u_n)) \quad \text{where } u_1 < u_2 < \dots < u_n \text{ in original query vertex IDs}$$

### 2.2. The Event Boundary Lemma
In continuous dynamic matching, an edge update $e = \{x, y\}$ alters the validity of embeddings:
- **For INSERT**: Evaluated on $G_{\text{after}}$ anchored at $\{x, y\}$:
  - In $G_{\text{before}}$, edge $\{x, y\}$ was absent, so $|R_{\text{before}}(f)| = |R_{\text{after}}(f)| + 1$.
  - An embedding enters at step $t$ if and only if $f$ was invalid before ($|R_{\text{before}}| > k$) and valid after ($|R_{\text{after}}| \le k$).
  - Therefore:
    $$|R_{\text{after}}(f)| + 1 > k \land |R_{\text{after}}(f)| \le k \iff |R_{\text{after}}(f)| == k$$
- **For DELETE**: Evaluated on $G_{\text{before}}$ anchored at $\{x, y\}$:
  - In $G_{\text{after}}$, edge $\{x, y\}$ becomes absent, so $|R_{\text{after}}(f)| = |R_{\text{before}}(f)| + 1$.
  - An embedding exits at step $t$ if and only if $f$ was valid before ($|R_{\text{before}}| \le k$) and invalid after ($|R_{\text{after}}| > k$).
  - Therefore:
    $$|R_{\text{before}}(f)| \le k \land |R_{\text{before}}(f)| + 1 > k \iff |R_{\text{before}}(f)| == k$$

> [!IMPORTANT]
> **No `|R| <= k` at Leaf Nodes in Event Enumeration**: Both M1 and M2 strictly enforce $|R| == k$ at leaf nodes. Testing $|R| \le k$ would erroneously emit embeddings that were already valid prior to the update, corrupting the delta event set.

---

## 3. Engine Architecture & Independence

```
                       +-----------------------------+
                       |  scripts/dstar_event_common |
                       |  - DStarEventGraph          |
                       |  - DStarEventUpdate         |
                       |  - EventCounters & Limits   |
                       |  - EventSink (zero-dup test)|
                       +--------------+--------------+
                                      |
               +----------------------+----------------------+
               |                                             |
+--------------v---------------+              +--------------v---------------+
|     scripts/dstar_m1_enum    |              |     scripts/dstar_m2_event   |
|  - M1EnumEngine              |              |  - M2EventEngine             |
|  - Anchored Exact variants   |              |  - Bare M2 DFS               |
|  - Match Q - D, R(f) == D    |              |  - PRESENT / MISSING branches|
|  - M1DynamicEngine (stream)  |              |  - M2DynamicEngine (stream)  |
+------------------------------+              +------------------------------+
```

### Strict Independence Enforcement
1. **Production Isolation**: Neither `dstar_m1_enum.py` nor `dstar_m2_event.py` imports `dstar_oracle.py`, `apx02_oracle.py`, or any APX-03 matcher. This was verified via AST inspection in `TestProductionIndependence` and Stage 1 of `generate_dstar02_report.py`.
2. **Direct Event Search**: Neither production engine computes deltas by taking full snapshot differences ($M_{\text{after}} \setminus M_{\text{before}}$); both search directly around the updated data edge.
3. **No Hash Deduplication**: The `EventSink` records raw emissions in a `list` and asserts `len(raw) == len(set(raw))` before returning sorted results. Zero duplicates were emitted across all 840 corpus cases and all 672 matrix updates.

---

## 4. Empirical Verification & Evidence Matrix

### 4.1. Verification Summary Table

| Stage | Verification Target | Test Cases / Steps | Criteria | Result | Status |
|---|---|---|---|---|---|
| **1** | Production Independence | 4 production files | Zero forbidden imports in AST | 0 violations | **PASS** |
| **2** | Hand & Adversarial | 24 targeted cases | Strict agreement across M0, M1, M2 | 24 / 24 | **PASS** |
| **3** | Corpus 840 Reference | 840 cases (seed 1 & seed 7) | 8,002 truth events, zero raw duplicates | 840 / 840 | **PASS** |
| **4** | 56-Stream Stateful Matrix | 56 configs $\times$ 12 updates = 672 steps | Step delta agreement & $M_t$ reconstruction | 672 / 672 | **PASS** |
| **5** | Invariant Violation Guard | Empty frontier test | Fail closed with `InvariantViolationError` | Verified | **PASS** |
| **6** | Resource Limit Guards | `max_work`, caps | Fail closed with `ResourceLimitError` | Verified | **PASS** |
| **7** | Manifest Reconciliation | `configs/dstar02_correctness_cases.json` | Exact bidirectional declaration match | Verified | **PASS** |

### 4.2. Corpus 840 Reference Analysis
The 840 validation cases originate from `thuc_nghiem_m2_2026-11-18.zip` (generated across random seed 1 and seed 7):
- **Queries**: $C_4$ ($m=4, \lambda=2$), Diamond ($m=5, \lambda=2$), $K_4$ ($m=6, \lambda=3$).
- **Thresholds**: $k \in \{0, 1, 2\}$ with $0 \le k < \lambda(Q)$.
- **Update Operations**: Both `INSERT` and `DELETE`.
- **Event Distribution**:
  - Total cases: 840
  - Cases triggering non-empty events: 330
  - Cases triggering non-empty events for $k \ge 1$: 239
  - Total ground-truth event tuples emitted: 8,002
- **Engine Agreement**:
  - M1 agreement: 840 / 840 (100.0%)
  - M2 agreement: 840 / 840 (100.0%)
  - Raw emission duplicate count: **0** across all cases.

### 4.3. 56-Stream Stateful Matrix & Active-Set Reconstruction
The 56-stream matrix stress-tests dynamic state over continuous time:
- **56 Distinct Graph Configurations**:
  - $C_4$: $k \in \{0, 1\} \times 8$ seeds = 16 configs.
  - Diamond: $k \in \{0, 1\} \times 8$ seeds = 16 configs.
  - $K_4$: $k \in \{0, 1, 2\} \times 8$ seeds = 24 configs.
  - Uniform vertex/edge labels alternated with mixed labels across seeds.
- **12 Discrete Updates per Stream**: 672 total updates applied.
- **Active-Set Reconstruction Invariant**:
  At each step $t$:
  $$M_t = (M_{t-1} \setminus \text{exited}_t) \cup \text{entered}_t$$
  The reconstructed active set $M_t$ was compared against $M_0(G_t)$ enumerated from scratch.
  - Total steps evaluated: 672
  - Agreement rate: 672 / 672 (100.0%)
  - Reconstructed active set matches: 672 / 672 (100.0%)

---

## 5. Algorithmic Invariants & Key Findings

### 5.1. Empty Frontier Invariant
In M2, when $k < \lambda(Q)$, any cut $(S, V_Q \setminus S)$ with $S \ne \emptyset$ and $S \ne V_Q$ has cut size $|E(S, V_Q \setminus S)| \ge \lambda(Q) > k$.
Since at most $k$ query edges can be committed to `MISSING` ($|R_p| \le k$), at least one crossing edge MUST NOT be missing. Therefore, the frontier of `UNKNOWN` edges across the cut cannot be empty.
- If an empty frontier is ever reached while unmapped vertices exist and $|R_p| \le k$, it indicates a violation of the domain contract.
- M2 detects this condition immediately, raises `InvariantViolationError`, and fails closed without emitting corrupted partial results.

### 5.2. Search Effort & Efficiency Comparison
From the 840 corpus execution:
- M1-enum evaluates $\binom{m-1}{k}$ exact variants per anchor orientation. For $K_4$ ($m=6, k=2$), this evaluates $\binom{5}{2} = 10$ exact variants per anchor.
- Bare M2 explores a single unified DFS search tree branching on PRESENT vs MISSING edges, dynamically pruning branches as soon as $|R_p| > k$.
- Both engines produced identical tuple sets with zero duplicates, validating the theoretical equivalence of M1 and M2 under $k < \lambda(Q)$.

---

## 6. Provenance & Reproducibility

- **Git Commit Provenance**:
  - Historical Base: `e6cc1dba15955ce9f7f05ec0ca2a1d749181218c` (QuangThang)
  - Active Branch: `feature/rf24-dstar02-event-engines`
  - Integration Target: `origin/QuangThang`
- **Tracked Source Hashes**:
  - `scripts/dstar_event_common.py`: `73798c9dcb6e85a085905877e3115e1c889115e9e76fb32072676ce9c3b8e9a8`
  - `scripts/dstar_m1_enum.py`: `1ad27a967b833f668423130ae52f87efe7f45f85f96d1068796c36e9dd9fdfba`
  - `scripts/dstar_m2_event.py`: `55c2eb15e4beca068e69123e5a02167fb72a773260dc1580f27de4e9eb47dadf`
  - `scripts/dstar_event_cli.py`: `3c756b614669d772e73d7a6c322e7934c4c4f4080b674df21f54e4c1cb537a52`
  - `scripts/generate_dstar02_report.py`: `3923ceb1d223fdc106df7b8fadbd04dd5423fe117247da8657c79ae1b947e8b2`
  - `configs/dstar02_correctness_cases.json`: `d886b624d22c77ee49ad3df128b3f0c4928d107ce6dfe0c3f98fdfe4f2703b5a`
  - `docs/dstar02_engine_contract.md`: `3dd6cdf49d1ab1361be1958fa0b670b09d7c7aa05991f26b51c21b8bc13fc9f6`
- **Reproduction Commands**:
  ```bash
  # Run unit test suite
  python3 -m unittest tests/test_dstar02_engines.py
  # Run report regression suite
  python3 -m unittest tests/test_dstar02_report.py
  # Re-generate official evidence report
  python3 scripts/generate_dstar02_report.py --output reports/dstar02/correctness_summary.json
  # Run full repository regression
  python3 -m unittest discover tests
  ```

---

## 7. Next Steps & Handoff

DSTAR-02 (Issue #24) has achieved all objectives for S0–S3.
Next steps in the D* roadmap (requiring separate user authorization):
- **DSTAR-03 (M2 Optimizations)**: Static NLF filtering, dynamic NLF $\delta'$, and candidate order pruning.
- **DSTAR-04 (Local View & Native Integration)**: RapidFlow Local View integration and C++ porting.
- **DSTAR-05 (Evaluation & Benchmarks)**: Full-scale NetFlow dynamic stream evaluation.
