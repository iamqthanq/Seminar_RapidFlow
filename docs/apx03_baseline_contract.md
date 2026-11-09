# APX-03 — Baseline Contract & Operational Specification (Gate A2)

**Document ID:** `docs/apx03_baseline_contract.md`
**Status:** Approved Operational Specification
**Associated Issue:** #18
**Target Integration Branch:** `QuangThang`
**Target Executable:** `rapidflow_apx03_baseline`

---

## 1. Overview & Research Objective

APX-03 delivers the first **RapidFlow-derived C++ Approximate Continuous Subgraph Matching (CSM) baseline**.
The design strictly adheres to:
1. **Model C Formal Semantics:** Deviations $m(f)$, $x_{\text{in}}(f)$, $x_{\text{out}}(f)$, topological connectivity ($ConnOK$), and Acceptance criteria defined in APX-01 and validated by APX-02.
2. **Native RapidFlow Integration:** Genuinely compiles and links RapidFlow's pinned `Graph` CSR implementation (`baselines/rapidflow/source/graph/graph.cpp`).
3. **Correctness-First Baseline:** Implements the deterministic **RECOMPUTE_BASELINE** algorithm for dynamic streaming updates, providing an infallible ground truth in C++ before attempting complex incremental indexing.
4. **Concrete Embedding Retrieval:** Enumerates and outputs complete ordered mapping tuples $(f(0), \dots, f(k-1))$ with deviation scores, never collapsing embeddings into scalar counts.

---

## 2. CLI Interface & Operational Modes

### 2.1. Command Syntax
```bash
rapidflow_apx03_baseline [OPTIONS]
```

### 2.2. Options Reference
| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--query <path>` | Path | *(Required)* | File containing the query graph $Q$. |
| `--data <path>` | Path | *(Required)* | File containing the initial data graph $G_0$. |
| `--eps-m <int>` | Non-negative int | *(Required)* | Missing/mismatched query edge threshold $\varepsilon_m \ge 0$. |
| `--eps-x <int\|inf\|None>` | Value | `None` | External boundary cut threshold $\varepsilon_x$ (`None`/`inf` for unbounded). |
| `--mode <STRICT\|RELAXED>` | Enum | `STRICT` | Connectivity requirement mode (`STRICT` or `RELAXED_UNCONSTRAINED`). |
| `--stream <path>` | Path | `None` | Update stream file for dynamic streaming evaluation. |
| `--evaluate-mapping <tuple>` | String | `None` | Direct evaluation of a specific mapping (e.g. `--evaluate-mapping 0,1,2`). |
| `--max-candidates <int>` | Positive int | `10000000` | Safety budget cap on complete candidate evaluations. |
| `--max-search-steps <int>` | Positive int | `10000000` | Safety budget cap on intermediate search exploration steps. |
| `--output <path>` | Path | `None` | Path to serialize structured JSON output. |
| `--help` | Flag | - | Display usage information and exit. |

---

## 3. Input & Parser Specifications

### 3.1. Graph Files (`--query`, `--data`)
Graphs follow the standard vertex-edge adjacency format:
```text
# Vertex declaration: v <vertex_id> <vertex_label>
v 0 0
v 1 0
v 2 0

# Edge declaration: e <u_id> <v_id> <edge_label>
e 0 1 0
e 1 2 0
```
- **F01 Pre-declaration Constraint:** Every endpoint $u$ and $v$ appearing in an edge record `e` must be explicitly declared in a prior `v` record. Undeclared endpoints trigger `UnknownVertexException`.
- **Simple Graph Invariant:** Self-loops (`u == v`) and duplicate edges trigger explicit exceptions (`SelfLoopException`, `DuplicateEdgeInsertionException`).
- **Dense Remapping:** Sparse vertex IDs are mapped bidirectionally to dense indices $0 \dots |V|-1$ for RapidFlow CSR construction, preserving original IDs on output.

### 3.2. Stream Update Files (`--stream`)
Stream files contain atomic single-edge updates:
```text
e 0 2 0    # Insert edge {0, 2} with label 0
-e 0 2 0   # Delete edge {0, 2} with label 0
```
- **Insertion (`+` or `e`):** Both endpoints must exist in $V_0$. Duplicate edge insertion raises `DuplicateEdgeInsertionException`.
- **Deletion (`-` or `-e`):** Edge must exist with matching label, or `NonexistentEdgeDeletionException` / `EdgeLabelMismatchDeletionException` is raised.
- **Atomicity:** Any invalid update throws an exception leaving graph state strictly unchanged.

---

## 4. Execution Modes & JSON Output Schema

### 4.1. Static Full Enumeration Mode (No `--stream`)
Backtracks through all injective label-compatible mappings, applying safe early pruning on partial missing edges ($m_{\text{partial}} > \varepsilon_m$).

**Output Schema:**
```json
{
  "query": "query.graph",
  "data": "data.graph",
  "eps_m": 0,
  "eps_x": 0,
  "mode": "STRICT",
  "total_accepted": 1,
  "matches": [
    {
      "mapping": [1, 2, 3],
      "score": {
        "m": 0,
        "x_in": 0,
        "x_out": 0,
        "connected": true
      },
      "accepted": true
    }
  ]
}
```

### 4.2. Direct Mapping Evaluation Mode (`--evaluate-mapping`)
Bypasses combinatorial search to evaluate a single prescribed ordered tuple in $O(|E_Q| + \sum_{v \in S_f} \deg(v))$ time. Essential for high-degree probes (such as APX-01 Case 08 with a 1,000-degree hub).

**Output Schema:**
```json
{
  "query": "query.graph",
  "data": "data.graph",
  "eps_m": 0,
  "eps_x": 30,
  "mode": "STRICT",
  "evaluation_type": "DIRECT_MAPPING_EVALUATION",
  "target_mapping": [0, 1, 2],
  "score": {
    "m": 0,
    "x_in": 0,
    "x_out": 998,
    "connected": true
  },
  "accepted": false
}
```

### 4.3. Dynamic Stream Mode (`--stream`)
Implements **RECOMPUTE_BASELINE**:
1. Computes initial match set $M_0$.
2. For each update $t = 1 \dots T$: applies update to $G_t$, recomputes $M_t$, and computes exact delta partitions:
   - $\text{ENTERED}_t = M_t \setminus M_{t-1}$
   - $\text{EXITED}_t = M_{t-1} \setminus M_t$
   - $\text{SCORE\_CHANGED}_t = \{f \in M_{t-1} \cap M_t : (m_{t-1}, x_{\text{out},t-1}) \neq (m_t, x_{\text{out},t})\}$
   - $\text{UNCHANGED}_t = \{f \in M_{t-1} \cap M_t : (m_{t-1}, x_{\text{out},t-1}) == (m_t, x_{\text{out},t})\}$

**Output Schema:**
```json
{
  "query": "query.graph",
  "initial_data": "data.graph",
  "stream": "stream.update",
  "eps_m": 0,
  "eps_x": 0,
  "mode": "STRICT",
  "total_steps": 2,
  "history": [
    {
      "step": 2,
      "update": {"op": "+", "u": 1, "v": 2, "label": 0},
      "duration_ms": 0.0086,
      "total_active": 2,
      "delta": {
        "entered": {
          "(0, 1, 2)": {"m": 0, "x_in": 0, "x_out": 0, "connected": true},
          "(2, 1, 0)": {"m": 0, "x_in": 0, "x_out": 0, "connected": true}
        },
        "exited": {},
        "score_changed": {},
        "unchanged": {}
      }
    }
  ],
  "final_active_matches": 2
}
```

---

## 5. Exit Codes & Failure Modes

| Exit Code | Condition | Behavior |
| :---: | :--- | :--- |
| `0` | Success | Normal execution; full results serialized to stdout or `--output`. |
| `1` | Parameter / Format / Graph Exception | Invalid arguments, syntax error, unknown vertex, duplicate edge, etc. |
| `2` | Resource Limit Exceeded | Number of explored candidates or search steps exceeded `--max-candidates` or `--max-search-steps`. Fails closed. |

---

## 6. Known Limitations

1. **Recompute Complexity:** In dynamic streaming mode, RECOMPUTE_BASELINE recomputes matches from scratch per update step. It is designed for semantic ground truth and microcase verification, not for high-throughput million-edge NetFlow streams.
2. **Fixed Vertex Universe:** In accordance with APX-01/02 Scope B, dynamic updates operate on a fixed vertex universe ($V_t = V_0$). Dynamic vertex creation is deferred to future gates.
