# RapidFlow-APX Build Recipe & Tooling Guide (Direction D)

**Document ID:** `docs/rapidflow_apx_build.md`
**Date:** 2026-11-18
**Context:** Seminar RapidFlow / Continuous Subgraph Matching (CSM) — Direction D (Issue #28)
**Parent Document:** Issue #28 (`D-INT-01 / P0–P1`)

---

## 1. Verified Audit Build Commands (Actually Executed)

The following commands have been executed and verified in the Linux environment (Ubuntu 24.04, GCC 13.3.0, CMake 3.28.3):

### 1.1. Prerequisites
- **Compiler**: `g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0` supporting C++17.
- **CMake**: Portable CMake 3.28.3 located at `external/tools/cmake-3.28.3-linux-x86_64/bin/cmake`.
- **System Libraries**: Standard C++ library (`libstdc++`), POSIX threads (`-pthread`). No external package managers or root/sudo privileges required.

### 1.2. Building the Native Audit Harness
From the workspace root directory (`~/Desktop/Seminar_RapidFlow` or sibling worktree):

```bash
# 1. Create build directory
mkdir -p tests/native/rapidflow_apx_audit/build
cd tests/native/rapidflow_apx_audit/build

# 2. Configure using portable CMake
../../../../external/tools/cmake-3.28.3-linux-x86_64/bin/cmake ..

# 3. Build executable
make -j2
```

The resulting binary is created at:
`tests/native/rapidflow_apx_audit/build/rapidflow_apx_audit`

### 1.3. Running the Audit Harness Directly
```bash
# Run all probes directly
./tests/native/rapidflow_apx_audit/build/rapidflow_apx_audit --probe all

# Run specific probe with fixture (e.g. Probe D: Exact NLF counterexample)
./tests/native/rapidflow_apx_audit/build/rapidflow_apx_audit --probe d --fixture tests/fixtures/rapidflow_apx_audit/diamond_k1_fixture.json
```

### 1.4. Running the Fail-Closed Audit Runner
```bash
python3 scripts/run_rapidflow_apx_audit.py \
  --manifest configs/rapidflow_apx_audit_cases.json \
  --output reports/rapidflow_apx_audit/summary.json
```

### 1.5. Running Focused Unit Tests
```bash
python3 -m unittest tests.test_rapidflow_apx_audit
```

---

## 2. Compiler Flags & Linkage Configuration

From `tests/native/rapidflow_apx_audit/CMakeLists.txt`:
```cmake
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -Wall -Wextra -pthread")
```

### Source File Provenance:
- `baselines/rapidflow/source/graph/graph.cpp`: Compiled directly in-place from pinned upstream commit `2ea4f4c3...`.
- `baselines/rapidflow/source/utility/graphoperations.cpp`: Compiled directly in-place.
- `baselines/rapidflow/source/streaming/global_view_manager.cpp`: Compiled directly in-place for genuine GlobalViewManager execution.
- `tests/native/rapidflow_apx_audit/src/audit_probes.cpp`: Custom audit probe implementations.
- `tests/native/rapidflow_apx_audit/src/simple_json.h`: Header-only JSON fixture parser.
- `tests/native/rapidflow_apx_audit/src/main.cpp`: CLI entry point.

---

## 3. Proposed Native Production CLI (Future Phases P2–P3)

> [!NOTE]
> The following CLI syntax is a **proposed design specification** for the future production engine (`rapidflow_apx`) in Phases P2 and P3. It is documented here for architectural planning and has **NOT** been implemented in Phase P1.

### Proposed CLI Interface (Draft):
```bash
# Proposed future command syntax:
./build/rapidflow_apx \
  -q <query_graph_path> \
  -d <initial_data_graph_path> \
  -u <update_stream_path> \
  -k <missing_edge_budget> \
  -engine <m1_enum|m2_bare|m2_opt> \
  -output_mode <tuples|count> \
  -out <output_event_log_path> \
  -time_limit <seconds> \
  -memory_limit <mib>
```

### Key Differences from Upstream RapidFlow CLI:
1. Adds `-k <int>` missing edge budget parameter.
2. Adds `-output_mode tuples` to stream concrete canonical mapping tuples $(f(u_1), \dots, f(u_n))$ rather than only scalar counts.
3. Adds `-engine` selection flag to choose between M1 baseline and M2 candidate engines.
4. Adopts fail-closed transactional updates with clean error exit codes (0 = success, 1 = validation/resource error, 2 = CLI error).

---

## 4. Architectural Boundaries, Upstream Limitations, and Occurrence Witnesses (Pre-P4 Hardening)

### 4.1. Three-Tier Architecture and Engine Interfaces
RapidFlow-APX establishes a clean architectural separation across three distinct operational layers:

1. **Upstream RapidFlow Baseline Engine (COUNT-ONLY)**:
   - Upstream RapidFlow computes exact subgraph matching counts on dynamic graphs using leaf candidate count shortcuts (`streaming/search_engine.cpp:161`) and algebraic automorphism multiplication (`streaming/streaming_engine.cpp:181`).
   - Output mode is strictly scalar count-only. It does NOT instantiate, track, or stream explicit embedding tuples $(f(u_1), \dots, f(u_n))$.

2. **Native RapidFlow-APX Event Core (`extensions/rapidflow_apx/`)**:
   - Implemented via `ApxStreamingEngine::execute_step()`, orchestrating `ApxM1EnumEngine` or `ApxM2EventEngine` within `ApxTransactionManager`.
   - Generates incremental ordered tuple deltas per streaming update (`UpdateOperation`), tracking embeddings through `ApxTupleSink`.
   - Step execution returns a structured `StreamStepResult` holding `entered_tuples`, `exited_tuples`, the internal execution state `status` (C++ `StepStatus` enum: `SUCCESS`, `ERROR`, etc.), and isolated per-step performance metrics in `EngineCounters`. In the CLI JSON output report (`steps[]`), `commit_status` indicates transactional persistence (`COMMITTED` if `status == StepStatus::SUCCESS`, else `NOT_COMMITTED`), distinct from `status`.
   - Strong exception containment and transactional rollback guarantee that aborted steps discard staged pending tuples (`discarded_emissions += pending_tuples.size()`) and restore graph/codec state to pre-step checkpoints without data contamination.

3. **Operational CLI Test Harness (`tests/native/rapidflow_apx_engines/src/main.cpp`)**:
   - The production test executable provides the operational batch interface:
     ```bash
     ./tests/native/rapidflow_apx_engines/build/rapidflow_apx_native \
       --engine <m1|m2> \
       --workload <INPUT_WORKLOAD.json> \
       --output <OUTPUT_BATCH.json> \
       [--max-work <N>] \
       [--continue-on-error]
     ```
   - Serializes the sequential sequence of `StreamStepResult` instances into a canonical batch JSON report (`steps[]`) upon workload completion.
   - The JSON file output is an external harness serialization artifact; the internal C++ engine core operates strictly as an online per-step event emitter.

### 4.2. Upstream Single-Edge Query Crash ($K_2$) and RapidFlow-APX Mitigation
Upstream RapidFlow contains an architectural limitation on single-edge queries ($K_2$, $|V_Q| = 2, |E_Q| = 1$):
- **Reproducible Upstream Failure (Return Code -11 / SIGSEGV)**:
  - Upstream Commit: `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (pristine upstream pin)
  - Binary SHA-256: `abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6` (`baselines/rapidflow/source/build/streaming/RapidFlow.out`)
  - Query fixture (`q.graph`):
    ```
    v 0 0
    v 1 0
    e 0 1 0
    ```
  - Data fixture (`d.graph`):
    ```
    v 0 0
    v 1 0
    v 2 0
    e 0 1 0
    ```
  - Stream fixture (`u.graph`):
    ```
    e 1 2 0
    ```
  - Command: `baselines/rapidflow/source/build/streaming/RapidFlow.out -d d.graph -q q.graph -u u.graph -num MAX`
  - Result: Process terminates abruptly with exit code `-11` (`SIGSEGV`).
  - **Root Cause**: In upstream `streaming/search_engine.cpp:59`, the search routine initializes:
    ```cpp
    uint32_t target_depth = order.size();
    uint32_t start_vertex = order[0];
    ```
    For $K_2$, both query vertices are consumed by the indexing order (`indexing_order_` of size 2), leaving the matching order empty (`matching_order_.size() == 0`). Accessing `order[0]` dereferences an empty `std::vector`, triggering a segmentation fault.
- **RapidFlow-APX Mitigation**:
  - Native RapidFlow-APX engines (`M1` and `M2`) handle single-edge $K_2$ queries natively without crashing.
  - **Label Symmetry Property**: In a homolabeled $K_2$ query (where both vertices share the same label), automorphism group order is $|Aut(K_2)| = 2$. Consequently, an inserted edge $(v_1, v_2)$ produces exactly two symmetric directed embeddings: $(u_0 \mapsto v_1, u_1 \mapsto v_2)$ and $(u_0 \mapsto v_2, u_1 \mapsto v_1)$. Conversely, in a heterolabeled $K_2$ query (where $\ell_Q(u_0) \ne \ell_Q(u_1)$), only a single orientation is label-compatible. The native engine strictly evaluates vertex/edge labels and publishes the exact valid tuple set for both INSERT and DELETE operations.

### 4.3. Explicit Occurrence Witnesses Ground Truth
The correctness of transactional delta emission across M1 and M2 is anchored by three canonical occurrence witnesses:

| Witness Case | Query | Initial Graph | Stream Operation | Expected Events | Invariants Verified |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Triangle ($k=0$)** | Triangle $K_3$ ($3$ vertices, $3$ edges, label $0$) | $2$ edges present: $(0,1)$, $(1,2)$ | Step 0: `+e(0, 2)`<br>Step 1: `-e(0, 2)` | Step 0: 6 `ENTERED`<br>Step 1: 6 `EXITED` | Complete automorphism symmetry ($|Aut(K_3)| = 6$). All 6 permutations of $(0, 1, 2)$ enter on INSERT and exit on DELETE. Zero duplicate tuples (`len == len(set)`). |
| **Diamond Chordal Cycle ($k=1$)** | Diamond ($4$ vertices, $5$ edges, labels $10, 20, 30, 40$) | $3$ edges present (missing chord and one perimeter edge) | Step 0: `+e(0, 1)`<br>Step 1: `+e(4, 5)` (spectator)<br>Step 2: `-e(4, 5)` (spectator)<br>Step 3: `-e(0, 1)` | Step 0: 1 `ENTERED` (`[0, 1, 2, 3]`)<br>Step 1: empty delta<br>Step 2: empty delta<br>Step 3: 1 `EXITED` (`[0, 1, 2, 3]`) | Chordal cycle with $k=1$ defect budget. Steps 1–2 verify spectator update isolation (delta strictly empty). Step 3 cleanly revokes mapping. |
| **Single-Edge $K_2$ ($k=0$)** | Edge $K_2$ ($2$ vertices, $1$ edge, homolabeled $0$) | $2$ isolated vertices: $10, 20$ | Step 0: `+e(10, 20)`<br>Step 1: `-e(10, 20)` | Step 0: 2 `ENTERED` (`[10, 20]` and `[20, 10]`)<br>Step 1: 2 `EXITED` (`[10, 20]` and `[20, 10]`) | Proves native immunity to upstream `search_engine.cpp:59` empty `matching_order_` crash and verifies two-way automorphism symmetry on both INSERT and DELETE. |
