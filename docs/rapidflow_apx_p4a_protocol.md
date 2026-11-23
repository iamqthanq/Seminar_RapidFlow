# Experimental Protocol: Phase P4a Native Profiling, Baseline Measurement & Evidence-Driven Pilot

**Document ID:** `docs/rapidflow_apx_p4a_protocol.md`
**Protocol Revision:** `1.4.0` (Updated for Round 5 End-to-End Canonical Evidence Certification & CLI Contract Recovery)
**Milestone:** Phase P4a
**Parent Issue:** [Issue #36](https://github.com/iamqthanq/Seminar_RapidFlow/issues/36)
**Target Integration Branch:** `QuangThang`
**Pre-P4 Base Commit:** `b5a5fe44fe594f9c550dcc44f04b9f4fc9a54245`
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine: `true`)
**Status:** ACTIVE PROTOCOL SPECIFICATION (REVISION 1.4.0)

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

### 4.7 Cryptographic Self-Consistency & Ledger Integrity Enforcement (R2-F01)
- Merely checking that a record claims `execution_identity == expected_identity` is insufficient if the internal fields can be modified.
- Every record is strictly validated against a comprehensive metadata schema (`validate_record_integrity`):
  - Mandatory fields: `protocol_version`, `protocol_sha256`, `manifest_sha256`, `source_commit`, `source_dirty_state`, `binary_sha256`, `workload_id`, `workload_sha256`, `engine`, `repetition`, `status`, `exit_code`, `slot_key`.
  - Format checks: 64-character lowercase hex strings for all SHA-256 hashes and identity.
  - Resource boundary bounds: `effective_max_work <= 500000`, `0 < effective_timeout <= 60.0`, `0 < effective_memory_limit_mib <= 512`.
  - Cryptographic self-consistency: The runner recomputes `derived_id` from internal metadata fields and strictly asserts `derived_id == record["execution_identity"]`.
  - Any record with tampered metadata or header mismatch raises `CorruptedLedgerError` fail-closed.

### 4.8 Ambiguous Duplicate Slot Lineage & Conflict Rejection (R2-F02)
- Replay runs or manual interventions may produce multiple attempts for the same slot.
- Rather than blindly replacing or averaging, the summarizer groups all attempts per slot:
  - If a slot has multiple attempts, it identifies whether a unique canonical verified attempt exists.
  - If conflicting attempts exist (e.g. differing wall-clock durations or different status), the slot is marked ambiguous, and the paired comparison is rejected fail-closed (`total_valid_pairs` drops, preventing data falsification).

### 4.9 Safe Speedup Calculation & Micro-Duration Censorship (R2-F03)
- Direct floating-point division $T_{M1} / T_{M2}$ is vulnerable to zero denominators ($T_{M2} = 0$), negative times, NaN, Inf, and non-numeric types.
- The summarizer enforces `compute_safe_speedup(t_m1, t_m2)`:
  - Rejects any pair where $T_{M1} \le 0$ or $T_{M2} \le 0$.
  - Rejects NaN, Inf, None, or boolean values.
  - Returns `None` (`RATIO_NOT_COMPUTABLE`), excluding the pair from that specific scope's speedup distribution rather than substituting an artificial 1.0x ratio.
  - In the canonical dataset, 6 micro-search runs in zero-event workloads (`P4A_WL_09` and `P4A_WL_21`) execute in 0 ns; these are excluded from $S_{\text{search}}$ ($N_{\text{search}} = 84$, median 2.13x) while being fully counted in $S_{\text{step}}$, $S_{\text{loop}}$, and $S_{\text{outer}}$ ($N = 90$).

### 4.10 Explicit Run Verdict & Process Exit Code Contract (R2-F04)
- The experiment runner emits an explicit top-level verdict:
  - `CANONICAL_PASS`: All scheduled slots executed, 100% verified against M0 Oracle, zero correctness violations, and zero timeouts/censorship. Exits with code `0`.
  - `CANONICAL_INCOMPLETE`: At least one slot timed out, ran out of work budget, exceeded memory limits, or was skipped due to warm-up failure. Exits with code `1`.
  - `CANONICAL_FAILED`: Correctness violation, Oracle failure, or unhandled exception. Exits with code `1`.
- The runner NEVER exits 0 when workers fail, time out, or produce unverified data.

### 4.11 Resource Limit CLI Hard Enforcement (R2-F08)
- CLI arguments `--max-work`, `--timeout`, `--as-limit-mib`, and `--repetitions` are validated against approved hard ceilings before invoking any native binary:
  - `--max-work`: Strictly capped at `CANONICAL_MAX_WORK_CAP = 500000`.
  - `--timeout`: Strictly capped at `60.0` seconds.
  - `--as-limit-mib`: Strictly capped at `512` MiB.
  - `--repetitions`: Strictly capped at `10`.
- Violating any limit immediately exits with code 1.

### 4.12 Disposable Warm-Up Protocol & Replay Containment (R2-F08)
- To stabilize branch predictor and cache warm-up without contaminating timed profiling metrics, each slot executes a disposable untimed warm-up run (`profile=False`) before the timed measured run (`profile=True`).
- If the disposable warm-up run fails, times out, or exceeds resource limits, the runner does NOT proceed to the measured run; it immediately marks the slot `WARMUP_FAILED` (or `TIMEOUT`), records admission status `REJECTED`, and increments `warmup_failed_slots`.

### 4.13 Dedicated Observer Overhead Protocol (R2-F07, R3-F04)
- Profiling overhead is measured using an independent, dedicated script `scripts/measure_observer_overhead_p4a.py`.
- Protocol specifications:
  - 16 balanced trials across 4 representative workloads (`P4A_WL_01`, `P4A_WL_03`, `P4A_WL_11`, `P4A_WL_14`).
  - Alternating execution order (`PROFILE_OFF` $\rightarrow$ `PROFILE_ON` vs `PROFILE_ON` $\rightarrow$ `PROFILE_OFF`).
  - Full functional and counter invariance verified between modes:
    - Step count equivalence.
    - Raw duplicate rejection per step.
    - Full coordinate tuple set equality for `entered_tuples` and `exited_tuples`.
    - Exact equality across all 6 algorithmic counters (`candidate_evaluations`, `committed_emissions`, `dfs_states`, `anchors_started`, `edge_lookups`, `vertex_label_checks`).
  - Persisted raw evidence: `results/logs/rf36-p4a/p4a_observer_overhead.jsonl`.
  - Persisted statistical metrics: `results/logs/rf36-p4a/p4a_observer_overhead_summary.json`.

### 4.14 Protocol Versioning Distinction & Historical Evidence Compatibility (R3-F05)
To ensure rigorous provenance without retroactively rewriting historical experimental logs, this project strictly distinguishes between three protocol version concepts:

1. **Measurement Protocol Version (`1.2.0`):**
   - The measurement execution contract under which the 180 canonical pilot slots were executed at commit `cf97a7840dff0c0f2bf791834263f3911545110c`.
   - Binds historical protocol SHA-256: `dd3f39cae4325b3577adec8fa19ff5f52844e9338b1d97aa542d58b6c09d4ebd`.
   - Every canonical record in `results/logs/rf36-p4a/p4a_raw_ledger.jsonl` records `protocol_version = "1.2.0"` and `protocol_sha256 = "dd3f39ca..."`.
2. **Evidence Analysis Version (`1.4.0`):**
   - The analytical contract version implemented by `scripts/p4a_evidence_contract.py` and `scripts/summarize_rapidflow_apx_p4a.py`.
   - Enforces cryptographic self-consistency, duplicate attempt lineage resolution, and fail-closed pair compatibility gating.
3. **Protocol Specification Revision (`1.4.0`):**
   - The current documentation revision of this document (`docs/rapidflow_apx_p4a_protocol.md`), reflecting review remediations, observer invariance contracts, and evidence manifest specifications.

**Historical Evidence Reuse Justification:**
The 180 canonical records generated under commit `cf97a7840dff...` are verified to be 100% compatible and reusable under Evidence Analysis Version 1.4.0 without re-running:
- **Binary Invariance:** Native harness binary SHA-256 is identically `6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b`.
- **Manifest Invariance:** Workload manifest SHA-256 is identically `f82bdcc0c89ce794439eeff3fd67fe78de733196286503c27de70ed2df5d3b18`, with all 30 case hashes matching 100%.
- **Environmental Parity:** Resource limits (`effective_max_work = 500000`, `timeout = 60.0`, `as_limit_mib = 512`) are identical.
- **Oracle Verification:** All 180 slots achieved bit-for-bit M0 Oracle verification (`status = COMPLETED_VERIFIED`, `m0_agreed = True`, `performance_admission_status = ACCEPTED`).
- Therefore, rewriting historical ledger logs to artificially alter `protocol_sha256` or `protocol_version` would violate research integrity. The ledger is preserved pristine, while the evidence contract verifies its self-consistency.

### 4.15 Three-Level Integrity Architecture & Canonical Completeness Certification (R5-F01, R5-F02, R5-F04, R5-F05)
To prevent certification leakage where partial or tampered experiment logs could claim canonical pass, Round 5 establishes a formal three-level integrity architecture:
1. **Level A: Record Structural & Cryptographic Self-Consistency (`validate_record_integrity`):**
   - Strictly validates internal record schema, required non-empty `attempt_id`, resource caps, and lowercase 64-hex hash formatting.
   - Strictly enforces that records with `status == "COMPLETED_VERIFIED"` must have `correctness_status == "M0_VERIFIED"` (rejecting None, missing, or mismatched values).
   - Recomputes cryptographic execution identity from record metadata to prevent forged provenance.
2. **Level B: Workload Pair Compatibility & Invariant Alignment (`validate_pair_compatibility`):**
   - Verifies slot key alignment between M1 and M2 runs across repetitions and workloads.
   - Enforces exact ground truth event count equality ($E_{M1} == E_{M2}$) and stream step count equality.
   - Validates non-negative wall clock and stage durations.
3. **Level C: Experiment Completeness & Raw Artifact Binding (`analyze_p4a_ledger`):**
   - Computes expected slot completeness matrix dynamically from the workload manifest ($W \times R \times 2 = 30 \times 3 \times 2 = 180$ slots).
   - Binds and verifies raw ledger whole-file SHA-256 and byte size against trusted evidence manifest (`docs/evidence/p4a_evidence_manifest.json`) via `verify_raw_artifact_integrity()`.
   - Detects alien records (`rec_id != exp_id`), logs them into `rejected_conflicts`, quarantines conflicting slots, and marks ledger `CANONICAL_CONTAMINATED` fail-closed.
   - Distinguishes `SCOPED_ANALYSIS_VALID` from `CANONICAL_PASS` / `CANONICAL_COMPLETE`.

### 4.16 Production CLI Contract Recovery & Mode Separation (R5-F03, R5-F06)
- The canonical summarizer CLI `scripts/summarize_rapidflow_apx_p4a.py` provides a production-grade, fail-closed entrypoint:
  - `--mode canonical`: Requires trusted evidence manifest binding, validates full raw artifact integrity, enforces slot matrix completeness (180/180 slots, 90/90 pairs), and emits `CANONICAL_PASS` (exit 0) only when all requirements are satisfied.
  - `--mode scoped`: Supports exploratory or subset evaluation without claiming canonical completeness, emitting `SCOPED_ANALYSIS_VALID` (exit 0) when valid.
  - Hardened observer verification checks that event coordinate tuples are non-negative integers matching query dimension, rejects duplicate embeddings, and accepts valid zero-event stream steps.

---

## 5. Decision Gate & Phase P4b Transition Criteria (P4A-F08)

The verdict for Milestone P4a is strictly confined to:
- `R4A_PROFILE_READY_FOR_P4B_REVIEW`: Profiling is empirically validated, cost centers are quantified, and evidence is ready for independent review.
- `R4A_INCONCLUSIVE`: Measurements exhibit excessive noise or lack statistical confidence.
- `R4A_FIX_REQUIRED`: Further experimental fixes required.

**P4b Recommendation Protocol:**
Cost centers identified in P4a (Search Core at 77.22% and Neighbor Retrieval at 22.78% within search) formulate testable hypotheses for P4b:
- **Option A (Relaxed NLF):** Reduces candidate evaluations in Search Core (77.22% of search time).
- **Option B (Authoritative Graph Neighbor Optimization):** Replaces dynamic vector allocations in `get_neighbors(u)` (22.78% of search time).
- **Option B+A (Hybrid):** Combines both optimizations if isolated tests justify both.
- **Option C (Pruning Reordering) / Option D (Re-scope).**

No speculative speedup numbers are claimed before isolated implementation and measurement in Phase P4b.
