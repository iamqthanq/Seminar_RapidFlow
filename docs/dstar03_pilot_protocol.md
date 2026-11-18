# DSTAR-03 S4 Pilot Protocol: Paired M1/M2 Exploratory Evaluation

**Version:** 2.0.0
**Issue:** [#26](https://github.com/iamqthanq/Seminar_RapidFlow/issues/26)
**Semantics Specification:** D* Semantics (S2, Simple Undirected Graphs, Edge-Defect $k < \lambda(Q)$, Full Tuple Output)
**Target Output Mode:** `COLLECT_VERIFY_SORT_V1` (identical raw tuple list, duplicate validation, canonical lexicographical sort)

---

## 1. Executive Summary & Scientific Purpose

The purpose of the DSTAR-03 S4 tiny pilot is to empirically measure the exploratory execution behavior of the accepted **M1-enum-Exact-$k$** algorithm against the candidate **M2-Event-$k$** bare event-enumeration algorithm under identical conditions:
1. **Identical Workloads:** Exact matching of query graph $Q$, initial data graph $G_0$, stream updates $U_{1..T}$, and defect threshold $k$.
2. **Identical Primitives & Output Sink:** Both engines internally execute in `COLLECT_VERIFY_SORT_V1` mode (full event tuples collected, verified for zero duplicates, and canonically sorted).
3. **Identical Resource Limits:** Tiny-only caps ($|V_{\text{data}}| \le 8$, $|V_Q| \le 6$, $c_{\max} \le 100{,}000$, $W_{\max} = 500{,}000$, $T \le 100$).
4. **Fair Paired Measurement:** Timed inside fresh, isolated worker subprocesses with an untimed warm-up replay on disposable state, followed by a state reset and a single measured execution.

### Scientific Integrity & Neutrality Principles
- **No Speedup Mandate:** There is no requirement or expectation that M2 must achieve a 1.2× speedup or a 20% work reduction.
- **Valid Scientific Outcomes:** An observed speedup in specific density/defect regimes, an observed slowdown, output/sorting dominance, noise dominance in sub-millisecond ranges, or insufficient event coverage in tiny real-world slices are all completely acceptable and valuable experimental findings.
- **No Fabrication or Optimization:** Production engine implementations from DSTAR-01/DSTAR-02 (`scripts/dstar_oracle.py`, `scripts/dstar_event_common.py`, `scripts/dstar_m1_enum.py`, `scripts/dstar_m2_event.py`) are strictly frozen and must not be modified.

---

## 2. Workload Taxonomy (65 Registered Workloads)

The evaluation suite defines **65 concrete workloads** across three distinct families:

### Family A: Synthetic Parameter Matrix (40 Workloads)
- **Goal:** Systematically evaluate the performance behavior across query topologies, defect tolerances $k$, data densities, and label diversity.
- **5 Query/k Configurations:**
  1. `diamond_k1`: Diamond query (4 vertices, 5 edges, $\lambda(Q) = 2$), $k = 1$.
  2. `k4_k1`: $K_4$ query (4 vertices, 6 edges, $\lambda(Q) = 3$), $k = 1$.
  3. `k4_k2`: $K_4$ query (4 vertices, 6 edges, $\lambda(Q) = 3$), $k = 2$.
  4. `diamond_k0`: Diamond query, $k = 0$ (exact control, $\lambda(Q) = 2$).
  5. `k4_k0`: $K_4$ query, $k = 0$ (exact control, $\lambda(Q) = 3$).
- **Data Graph Grid:**
  - Data vertices $n_{\text{data}} \in \{6, 8\}$
  - Initial edge probability $p \in \{0.25, 0.65\}$
  - Label regime $\in \{\text{"uniform"}, \text{"mixed"}\}$
  - $5 \times 2 \times 2 \times 2 = 40$ combinations.
- **Deterministic Generation Recipe:**
  - Case indices $0..39$, deterministic PRNG seed: `20261118 + case_index`.
  - Canonical parameter loop order: Query condition $\to$ $n_{\text{data}}$ $\to$ $p$ $\to$ label regime.
  - 12 valid updates per workload:
    - **Steps 1–4 (Toggle Cycle):** A fixed seed-derived pair $(u, v)$ is toggled for 4 steps. If present initially: `DELETE -> INSERT -> DELETE -> INSERT`. If absent initially: `INSERT -> DELETE -> INSERT -> DELETE`.
    - **Steps 5–12 (Seeded Churn):** 8 valid updates selected deterministically from available/present candidate pairs with uniform or mixed edge labels, strictly preserving labels on deletion.

### Family B: Structured Semantic Witnesses (5 Workloads)
- **Goal:** Provide positive, non-empty event witnesses with known ground truth under exact D* semantics.
- **5 Workloads:** One witness per query/k condition.
- **Construction:**
  - 4 query vertices with distinct vertex labels $\{A, B, C, D\}$ to eliminate automorphism symmetries and enforce exactly one ordered mapping.
  - Data graph contains 6 vertices: $\{0, 1, 2, 3\}$ for the query mapping and $\{4, 5\}$ as external spectator vertices ($E, F$).
  - Deterministic anchor $q = (0, 1)$ (first canonical query edge).
  - Edge defect set $D$: first $k$ canonical query edges excluding $q$.
  - Initial data graph $G_0$ contains all query edges except $D$ and $q$.
  - 4-step update stream:
    1. `INSERT` anchor edge $q \implies$ defect becomes $k \implies$ **1 ENTERED event**.
    2. `INSERT` external edge $(4, 5) \implies$ non-mapping edge $\implies$ **0 events**.
    3. `DELETE` external edge $(4, 5) \implies$ non-mapping edge $\implies$ **0 events**.
    4. `DELETE` anchor edge $q \implies$ defect becomes $k+1 \implies$ **1 EXITED event**.

### Family C: NetFlow-Relative Subgraph Probes (20 Workloads)
- **Goal:** Evaluate real-world network flow telemetry slices while strictly adhering to tiny-scale resource safety.
- **Dataset:** Official NewSP release `data/netflow/release_newsp/netflow/data_graph/` (`Ndata.graph` SHA-256 `bfedd2cab6d...`, `Ndeletion10.graph` SHA-256 `ab051a65...`).
- **4 Sample Universes:**
  - 2 seeds $\{20261118, 20261119\} \times 2$ target operations $\{\text{INSERT}, \text{DELETE}\}$.
  - Prefix: First 64 operation records of `Ndeletion10.graph`.
  - Target selection: Minimum SHA-256 of `f"{seed}:{op}:{source_op_index}"`.
  - Subgraph universe $S$ ($|S| = 8$ vertices): Target endpoints + up to 6 unique one-hop neighbors in $G_0$ by priority `SHA-256(seed, vertex_id)`. Any remaining slots filled with lowest-priority original vertices.
  - Bounded 2-Pass Streaming:
    - Pass 1: Select candidate vertices with $O(1)$ memory.
    - Pass 2: Extract vertex labels and all edges in $G_0$ with both endpoints in $S$.
  - Temporal Projection: $G_{\text{sample0}} = G_0[S]$, projected stream retains all prefix operations with both endpoints in $S$.
- **20 Probes:** 4 samples $\times$ 5 query/k configurations.
  - Query vertex labels: 0.
  - Query edge labels: Equal to target operation label.
  - Graph Mode: `SUBGRAPH_RELATIVE`.

---

## 3. Independent M0 Correctness Gate

Before any timed measurement, every concrete workload is executed against the independent Ground Truth Oracle (`dstar_oracle.py`, M0) in a preflight verification step:
1. **Tuple Array Equality:** Full arrays of `entered` and `exited` tuples must match M0 bit-for-bit.
2. **Canonical Ordering:** Tuples must be lexicographically ordered.
3. **Zero Duplicates:** No tuple may appear more than once in an event array.
4. **Active-Set Reconstruction:** For each step $t$:
   $$M_t = (M_{t-1} \setminus \text{exited}) \cup \text{entered} \equiv M_0(G_t)$$
5. **Fail-Closed Gate:** Any workload failing M0 verification immediately blocks execution with status `CORRECTNESS_FAIL` and preserves the concrete witness.

---

## 4. Measurement & Timing Protocol

### Subprocess Isolation
Each engine execution runs in a fresh, isolated child subprocess:
- **Warm-up:** Untimed execution of the full update stream on a throwaway clone of $G_0$.
- **State Reset:** Completely discard warm-up state and construct fresh engine instances on an independent clone of $G_0$.
- **Measured Replay:** Exactly one timed execution of the stream.

### Timer Boundary
- High-resolution monotonic timer: `time.perf_counter_ns()`.
- **Scope:** Placed strictly around each discrete call:
  ```python
  t0 = time.perf_counter_ns()
  result, next_graph = engine.process_update(query, current_graph, update, k, limits)
  t1 = time.perf_counter_ns()
  call_ns = t1 - t0
  ```
- **Included Costs:** Transaction handling, graph mutation, validation/min-cut, setup, DFS exploration, raw tuple collection, duplicate verification, and canonical sorting (`COLLECT_VERIFY_SORT_V1`).
- **Excluded Costs:** Worker startup, input file I/O, warm-up replay, test assertions, post-run verification against M0, and JSON serialization.

### Paired Repetitions & Ordering
- **3 Paired Repetitions per Workload:**
  - Case Index Even: Pair 1 (M1 $\to$ M2), Pair 2 (M2 $\to$ M1), Pair 3 (M1 $\to$ M2).
  - Case Index Odd: Pair 1 (M2 $\to$ M1), Pair 2 (M1 $\to$ M2), Pair 3 (M2 $\to$ M1).
- Total attempts per workload: 6 measured attempts ($3 \times \text{M1} + 3 \times \text{M2}$).

---

## 5. Resource Safety & Enforcement

- **Address-Space Ceiling:** Hard limit of **512 MiB** per worker subprocess enforced via `resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))`.
- **Wall Timeout:** Maximum **60 seconds** per worker subprocess.
- **Process Group Cleanup:** `os.setsid()` at child launch, terminated via `os.killpg(pid, signal.SIGKILL)` on timeout.
- **Environment Preflight:**
  - `MemAvailable >= 2 GiB` (checked via `/proc/meminfo`).
  - `DiskFree >= 1 GiB` (checked via `os.statvfs`).

---

## 6. Durable Ledger & Status Taxonomy

All execution attempts are durably logged to `reports/dstar03/attempts.jsonl`:
- `ATTEMPT_STARTED` recorded prior to child subprocess launch.
- Result record appended upon completion or timeout.

### Attempt Statuses
- `SUCCESS_NONEMPTY`: Step completed successfully with $\ge 1$ entered or exited event.
- `ZERO_RESULT_COMPLETE`: Step completed successfully with 0 entered and 0 exited events.
- `CORRECTNESS_FAIL`: Engine output deviated from independent M0 ground truth.
- `INPUT_INVALID`: Workload input violated contract constraints.
- `ERROR`: Unexpected Python exception or crash.
- `TIMEOUT`: Child process exceeded 60s wall clock limit.
- `RESOURCE_LIMIT`: Child process exceeded memory limit (RLIMIT_AS).
- `SAFETY_BLOCKED`: Preflight safety check failed.
- `PREPARATION_TIMEOUT`: Subgraph extraction exceeded time budget.
- `NOT_RUN_BUDGET`: Planned run omitted due to batch wall-time limit.
- `INTERRUPTED`: Process was aborted while running.
