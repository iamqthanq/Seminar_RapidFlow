# RF-03 Workload & Benchmark Protocol: Reproducible NetFlow COUNT-ONLY Baseline (Gate G2)

**Parent Roadmap:** [#1 — [ROADMAP] RapidFlow × NetFlow](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1)  
**Issue:** [#9 — [RF-03] Reproducible NetFlow COUNT-ONLY baseline and workload audit (G2)](https://github.com/iamqthanq/Seminar_RapidFlow/issues/9)  
**Previous Gate:** [#7 — [RF-02] Independent Correctness Verification (G1)](https://github.com/iamqthanq/Seminar_RapidFlow/issues/7) (PR [#8](https://github.com/iamqthanq/Seminar_RapidFlow/pull/8))  
**Worktree / Branch:** `feature/rf03-benchmark` (forked from `origin/QuangThang` at `e470ef3`)  
**Date:** 2026-11-04  
**Author:** AI Coding Agent / Senior Research Software Engineer  
**Status:** ACTIVE PROTOCOL SPECIFICATION  

---

## 1. Executive Summary & Research Scope

The objective of Gate G2 (RF-03) is to establish a rigorous, auditable, and reproducible **COUNT-ONLY baseline** for pristine RapidFlow on the public NetFlow dataset. This protocol establishes:
1. Complete provenance verification for the unmodified RapidFlow C++ source (`2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`) and executable (`abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6`).
2. An exhaustive syntactic and structural audit of the NewSP NetFlow archive, identifying format incompatibilities and invalid labels before execution.
3. A pre-declared, bounded benchmark matrix (10 query/stream combinations, 3 repeated runs each = 30 runs) designed with deterministic criteria before looking at execution runtimes, eliminating cherry-picking. (Note: pre-registration was conceptual; independent third-party timestamping prior to commit is unverified).
4. Resource safeguards (external timeout $\le 60\text{s}$, post-execution peak RSS budget check $3\,\text{GiB}$, safe memory floor $\ge 4\,\text{GiB}$) and fail-closed error handling.
5. Strict adherence to output semantics: RapidFlow operates exclusively in **COUNT-ONLY mode** (`EXECUTION_MODE=0`); Gate G1-B (explicit mapping sets) remains **UNVERIFIED**. No claims of algorithm novelty, speedup, or full enumeration correctness are permitted.

---

## 2. Fixed Provenance & Dataset Audit

### 2.1. RapidFlow Baseline Executable
- **Upstream Repository:** `https://github.com/shixuansun/RapidFlow`
- **Pinned Git Commit:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (verified clean working tree).
- **Executable Binary:** `baselines/rapidflow/source/build/streaming/RapidFlow.out`
- **Binary SHA-256:** `abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6` (identical to G0/G1 verified binary).
- **Access Rule:** Binary and source are accessed strictly **READ-ONLY** via absolute paths; no source modifications.

### 2.2. NetFlow Dataset Provenance
- **Distribution Source:** NewSP author repository (ICDE 2024), `https://github.com/hnuGraph/NewSP`, commit `c5c6eb05ce5059be0fa803d7a29d57cc26060a25`.
- **Archive File:** `data/netflow/source_archive/netflow.zip` (SHA-256: `19460c5a69723ba108c484de26da79a616edff915f76de3f6bca7f6a12f4e66a`).
- **Initial Data Graph:** `data/netflow/release_newsp/netflow/data_graph/Ndata.graph`
  - Vertices: $3{,}114{,}895$, Edges: $2{,}849{,}732$
  - Vertex labels: $1$ label (`0`); Edge labels: $8$ distinct labels (`0` through `7`)
  - Maximum undirected vertex degree: $49{,}014$
  - SHA-256: `bfedd2cab6d2c460141da945e77b0be39ae24c74d9a28b9394a38ee35b652fc6`
- **Dynamic Update Streams:**
  - `Ninsertion.graph`: $10{,}000$ edge insertions (SHA-256: `e45f2d908eaed5098653d4635076bef0152438014ad6e3aa03df522cef85ab05`).
  - `Ndeletion10.graph`: $10{,}000$ operations ($10\%$ deletions, $90\%$ insertions; SHA-256: `ab051a6514ac8bedb2454dfe3da6b6fd5422bffcc9565ccc1c9970a41e077372`).
  - `Ndeletion20` to `Ndeletion50.graph`: $10{,}000$ operations each with $20\%$ to $50\%$ deletions.

### 2.3. Critical Comparability Limitations
> [!WARNING]
> This dataset is the **NewSP NetFlow distribution**, which differs from the original RapidFlow PVLDB 2022 evaluation in two key aspects:
> 1. RapidFlow PVLDB 2022 Table 3 reports 7 edge labels; this release has 8 distinct edge labels (0..7).
> 2. RapidFlow PVLDB 2022 describes a 10% edge stream ($~280\text{k}$ updates), whereas this NewSP release provides fixed 10,000-operation streams.
> **Mandatory Rule:** Never claim exact paper replication or cross-paper speedup against PVLDB 2022 figures.

---

## 3. Query Corpus Audit & Exclusions

A comprehensive read-only audit across all 2,700 query files in `data/netflow/release_newsp/netflow/Nquery/` revealed the following structural properties:

### 3.1. Syntax Audit & Excluded Queries
1. **`Nquery/staticCaseStudyQuery` (30 files):**
   - Syntax: Begins with `t 7 10` followed by `v id label degree` and `e u v` without edge labels.
   - Status: **EXCLUDED**. Incompatible with RapidFlow parser (`utility/graph.cpp:55`). Passing these files causes immediate parser crash or segmentation fault.
2. **`Nquery/Nquery180_8/Q_136` (1 file):**
   - Syntax: Contains record `e 2 6 32513`.
   - Domain audit: NetFlow data graph only contains edge labels `0..7`. Edge label `32513` is an out-of-domain outlier.
   - Status: **EXCLUDED**. Original file remains unmodified.
3. **Valid RapidFlow Queries:** $2{,}669$ files in the archive adhere to the standard `v id label` and `e u v label` format.

### 3.2. Vertex Count Distribution & 4–6 Vertex Gap
Auditing all 2,669 valid query files by vertex count $|V|$:
- $|V| = 4$: **0 queries** (completely absent from author release).
- $|V| = 5$: **298 queries** ($150$ in `Nquery150_6`, $148$ in `Nquery180_6`).
- $|V| = 6$: **302 queries** ($32$ in `Nquery180_6`, $150$ in `Nquery150_8`, $120$ in `Nquery180_8`).
- $|V| = 7$: **1,079 queries**.
- $|V| \ge 8$: **990 queries** ($|V|=8$: 290, $|V|=9$: 347, $|V|=10$: 284, $|V|=11$: 66, $|V|=12$: 4).

> [!NOTE]
> Because $|V|=4$ queries are missing from the author release, a minimal 4-vertex star probe (`probe_star4_l0.graph`, $K_{1,3}$) is introduced under `data/queries/probes/` and explicitly tagged `category: synthetic_probe` to probe high-degree hub matching behavior. All other queries are author queries tagged `category: author_newsp`.

---

## 4. Pre-Declared Workload Matrix Specification

To prevent post-hoc cherry-picking, the benchmark workload was frozen in `configs/rf03_workload.json` before collecting execution metrics.

### 4.1. Selected Queries
| Query ID | Category | Rel. Path | $|V|$ | $|E|$ | Edge Labels | Degree Seq. | Selection Rationale |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `Q01_auth_5v_l1` | `author_newsp` | `Nquery/Nquery150_6/Q_0` | 5 | 6 | `[1]` | `[3,3,2,2,2]` | Minimal author query, chordal cycle, edge label 1 (low stream frequency, sparse/zero test). |
| `Q02_auth_5v_l0` | `author_newsp` | `Nquery/Nquery150_6/Q_11` | 5 | 6 | `[0]` | `[3,3,2,2,2]` | Minimal author query, chordal cycle, edge label 0 (prominent label in graph and updates). |
| `Q03_auth_6v_l2` | `author_newsp` | `Nquery/Nquery150_8/Q_3` | 6 | 8 | `[2]` | `[4,4,2,2,2,2]` | 6-vertex author query, dense structure, edge label 2 (comprises 73.7% of stream updates). |
| `Q04_auth_5v_l1_3` | `author_newsp` | `Nquery/Nquery150_6/Q_118` | 5 | 6 | `[1, 3]` | `[3,3,2,2,2]` | Heterogeneous multi-label query, includes label 3 (rare in stream, tests multi-label pruning). |
| `Q05_probe_star4_l0` | `synthetic_probe` | `data/queries/probes/probe_star4_l0.graph` | 4 | 3 | `[0]` | `[3,1,1,1]` | Synthetic 4-vertex star probe ($K_{1,3}$), hub matching probe under label 0. |

### 4.2. Selected Stream Prefixes
To ensure reproducibility and bounded execution time, stream prefixes of 100 updates are checksum-pinned under `data/streams/prefixes/`:
1. `insert_p100`: First 100 updates of `Ninsertion.graph` (SHA-256: `35e295ac5f1496590b1820546fd5e36a3f060f2e7c8110d09635b804aa0d5545`).
2. `delete10_p100`: First 100 updates of `Ndeletion10.graph` (SHA-256: `5516319644e94aad0c8d3c53952d5ab9f5bafd16b9757942d7c363f8cf37a4fa`).

### 4.3. Full Benchmark Matrix (10 Cases, 3 Repeats)
| Case ID | Query ID | Category | Stream ID | Stream Type | Updates | Repeats |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: |
| `c01_q01_insert_p100` | `Q01_auth_5v_l1` | `author_newsp` | `insert_p100` | Pure insertion | 100 | 3 |
| `c02_q01_del10_p100` | `Q01_auth_5v_l1` | `author_newsp` | `delete10_p100` | Mixed deletion (10%) | 100 | 3 |
| `c03_q02_insert_p100` | `Q02_auth_5v_l0` | `author_newsp` | `insert_p100` | Pure insertion | 100 | 3 |
| `c04_q02_del10_p100` | `Q02_auth_5v_l0` | `author_newsp` | `delete10_p100` | Mixed deletion (10%) | 100 | 3 |
| `c05_q03_insert_p100` | `Q03_auth_6v_l2` | `author_newsp` | `insert_p100` | Pure insertion | 100 | 3 |
| `c06_q03_del10_p100` | `Q03_auth_6v_l2` | `author_newsp` | `delete10_p100` | Mixed deletion (10%) | 100 | 3 |
| `c07_q04_insert_p100` | `Q04_auth_5v_l1_3` | `author_newsp` | `insert_p100` | Pure insertion | 100 | 3 |
| `c08_q04_del10_p100` | `Q04_auth_5v_l1_3` | `author_newsp` | `delete10_p100` | Mixed deletion (10%) | 100 | 3 |
| `c09_q05_insert_p100` | `Q05_probe_star4_l0` | `synthetic_probe` | `insert_p100` | Pure insertion | 100 | 3 |
| `c10_q05_del10_p100` | `Q05_probe_star4_l0` | `synthetic_probe` | `delete10_p100` | Mixed deletion (10%) | 100 | 3 |

---

## 5. Execution Safeguards & Runner Specifications

Runner script: `scripts/rf03_netflow_runner.py`
- **External Timeout:** $60.0\,\text{seconds}$ enforced via `subprocess.communicate(timeout=60)`. On timeout, SIGTERM followed by SIGKILL ensures no orphaned background processes.
- **Peak RSS Budget Check:** $3{,}072\,\text{MB}$ post-execution budget check tracked via `/usr/bin/time -v`. If exceeded after process exit, classified as `OOM` (indicating budget exceeded, not kernel OOM-killer). Note: No runtime memory limit or active leak prevention is enforced during execution.
- **Preflight Safe RAM Floor:** Requires at least $4.0\,\text{GiB}$ available memory in `/proc/meminfo` prior to launching.
- **Fail-Closed Parsing:** Exact single match required for `Result count:`, `Updates num:`, and `Edge process count:`. Any discrepancy flags `PARSE_ERROR`.
- **Repeat Consistency Verification:** Completed repeated runs of the identical case must yield **exact 100% agreement on `Result count`**. Any numerical divergence flags `COUNT_MISMATCH_ACROSS_REPEATS`.
- **Immutable Per-Run Logs:** Unique `run_id` (timestamp + UUID). Dedicated directory storing `stdout.log`, `stderr.log`, and `metadata.json` containing complete host telemetry and checksums. No historical logs are overwritten.
- **Durable Summary CSV & Incremental Checkpoints:** Incremental checkpoints written atomically after each attempt to `run_log_dir / checkpoint_runs.csv` and `summary_path`, preventing data loss on interruption. Canonical baseline records persisted to `results/summary/rf03_baseline_runs.csv`.

---

## 6. Output Semantics & Gate G1-B Dependency

As established in RF-01 (`docs/rf01_output_contract.md`) and RF-02 (`docs/rf02_correctness_report.md`):
1. RapidFlow output is **COUNT-ONLY stream-aggregated**: `Result count` $= \sum_{t=1}^{M} \Delta_t$.
2. It does **not** enumerate or output explicit vertex-to-vertex mappings ($u \mapsto v$).
3. Gate G1-A verified count correctness on small fixtures. Gate G1-B (explicit mapping set verification) remains **UNVERIFIED / BLOCKED** because pristine RapidFlow employs leaf-level candidate array addition (`search_engine.cpp:161`) and algebraic automorphism multiplication (`streaming_engine.cpp:181`).
4. All metrics recorded in Gate G2 are baseline measurements of this COUNT-ONLY implementation. They do not constitute proof of full-enumeration correctness or algorithmic superiority.
