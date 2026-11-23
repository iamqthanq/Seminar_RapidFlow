# Experimental Protocol: Phase P4a Native Profiling, Baseline Measurement & Evidence-Driven Pilot

**Document ID:** `docs/rapidflow_apx_p4a_protocol.md`
**Protocol Revision:** `1.2.0` (Updated for Round 1 Independent Review Remediation)
**Milestone:** Phase P4a
**Parent Issue:** [Issue #36](https://github.com/iamqthanq/Seminar_RapidFlow/issues/36)
**Target Integration Branch:** `QuangThang`
**Pre-P4 Base Commit:** `b5a5fe44fe594f9c550dcc44f04b9f4fc9a54245`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)
**Status:** ACTIVE PROTOCOL SPECIFICATION (REVISION 1.2.0)

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

## 4. Measurement Methodology & Experimental Safeguards (Revision 1.2.0)

### 4.1 Cryptographic Execution Identity Binding (P4A-F01)
To prevent conflation of runs across different commits or configurations, every record binds a deterministic `execution_identity`:
$$\text{ExecutionIdentity} = \text{SHA256}(\text{ProtocolRevision}, \text{ProtocolSHA}, \text{ManifestSHA}, \text{SourceCommit}, \text{DirtyState}, \text{BinarySHA}, \text{WorkCap}, \text{Timeout}, \text{MemoryLimit})$$
Resume validation strictly enforces that slots in an existing ledger match the active `execution_identity`. Any slot with mismatched identity is rejected for resume.

### 4.2 Durable Append-Only Ledger Contract (P4A-F02)
- Raw experiment logs are written in append-only JSONL format.
- Every record append calls `flush()` followed by `os.fsync(fd)` before proceeding to the next slot.
- Corrupted lines in a ledger trigger immediate fail-closed termination (`CorruptedLedgerError`).
- Duplicate attempt lineages are preserved without silent overwrite.

### 4.3 Fail-Closed M0 Dynamic Oracle Gate (P4A-F03)
- If `compute_m0_ground_truth` raises an exception or fails, the slot is marked `status = "ORACLE_FAILED"`, `performance_admission_status = "REJECTED"`, and `m0_agreed = False`.
- In native output verification, raw tuples are checked for duplicates **before** set conversion; duplicates trigger `DUPLICATE_EMISSION` and rejection.
- Only slots with `status == "COMPLETED_VERIFIED"` and `performance_admission_status == "ACCEPTED"` are admitted into paired speedup calculations.

### 4.4 Four Explicit, Distinct Speedup Scopes (P4A-F05)
To eliminate ambiguity, performance speedups are partitioned into four distinct measurement scopes:
1. **Search Speedup ($S_{\text{search}}$):** $T_{M1, \text{search}} / T_{M2, \text{search}}$ measured inside the search callback.
2. **Native Step Speedup ($S_{\text{step}}$):** $T_{M1, \text{step\_total}} / T_{M2, \text{step\_total}}$ measured across `execute_step()` invocations.
3. **Streaming Loop Speedup ($S_{\text{loop}}$):** $T_{M1, \text{stream\_loop}} / T_{M2, \text{stream\_loop}}$ measured across the C++ streaming loop in `main.cpp`.
4. **Outer Wall Speedup ($S_{\text{outer}}$):** $T_{M1, \text{outer}} / T_{M2, \text{outer}}$ measured as total subprocess wall time by the Python runner.

### 4.5 Subprocess Containment & Real Timeout Termination (P4A-F06)
- RLIMIT_AS enforces an address space cap of 512 MiB per child process.
- Each child process is placed in a dedicated process group (`os.setpgrp()`).
- On `TimeoutExpired`, the runner sends `SIGKILL` to the entire process group (`os.killpg`), preventing orphan processes, and records `status = "TIMEOUT"`.
- Resource limit failures are classified accurately without speculative OOM inference.

### 4.6 Mathematically Grounded Candidate Metrics (P4A-F04)
- Removes invalid `pruning_efficiency_pct = 1 - events/candidates`.
- Tracks raw physical counters: `candidate_evaluations`, `committed_emissions`, `steps`.
- Reports descriptive ratio: $\text{events\_per\_candidate\_eval} = N_{\text{events}} / N_{\text{cand}}$.
- Reports relative candidate evaluation reduction: $R = 1 - (C_{M2} / C_{M1})$.

---

## 5. Decision Gate & Phase P4b Transition Criteria (P4A-F08)

The verdict for Milestone P4a is strictly confined to:
- `R4A_PROFILE_READY_FOR_P4B_REVIEW`: Profiling is empirically validated, cost centers are quantified, and evidence is ready for independent review.
- `R4A_INCONCLUSIVE`: Measurements exhibit excessive noise or lack statistical confidence.
- `R4A_FIX_REQUIRED`: Further experimental fixes required.

**P4b Recommendation Protocol:**
Cost centers identified in P4a (Search Core at 77.4% and Neighbor Retrieval at 22.6%) formulate testable hypotheses for P4b:
- **Option A (Relaxed NLF):** Reduces candidate evaluations in Search Core.
- **Option B (Authoritative Graph Neighbor Optimization):** Replaces dynamic vector allocations in `get_neighbors(u)`.
- **Option B+A (Hybrid):** Combines both optimizations if isolated tests justify both.
- **Option C (Pruning Reordering) / Option D (Re-scope).**

No speculative speedup numbers are claimed before isolated implementation and measurement in Phase P4b.
