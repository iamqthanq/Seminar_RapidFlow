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

# Run specific probe (e.g. Probe D: Exact NLF counterexample)
./tests/native/rapidflow_apx_audit/build/rapidflow_apx_audit --probe d
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
- `tests/native/rapidflow_apx_audit/src/audit_probes.cpp`: Custom audit probe implementations.
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
