# Research Decision Record: D-R3 — Native Event Engines Integration & Verification

**Document ID:** `docs/research_decisions/D-R3-native-engines.md`  
**Milestone:** D-INT-03 / Phase P3  
**Parent Issue:** Issue #32  
**Target Integration Branch:** `QuangThang`  
**Base Commit:** `88fbe200ff9217f4dccbad08bfa448d067d2ce4e` (contains accepted P2 HEAD `ebe2894752dc45e2aeb3109c260a34e2f87a9d06`)  
**Pinned Upstream RapidFlow:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`  
**Status:** IN_PROGRESS (R3-A Design & Contract Synchronization Completed)  

---

## 1. Executive Summary & Review Scope

Phase P3 (Issue #32) realizes the integration of native event search engines on top of the RapidFlow-APX foundation validated in Phase P2.

The two core engines implemented in `extensions/rapidflow_apx`:
1. `ApxM1EnumEngine`: The native baseline algorithm performing exact subgraph matching across variant queries $Q - D$ where $|D| = k$.
2. `ApxM2EventEngine`: The bare M2 direct depth-first search engine branching on PRESENT vs MISSING edges, deferring unmapped vertices across cuts, and enforcing $|R(f)| = k$ at event leaves.

Both engines interact strictly through the shared native foundation (`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, and `ApxTupleSink`), producing explicit canonical ordered tuples $(f(u_1), \dots, f(u_n))$ without relying on Python prototypes, monolithic upstream counting shortcuts, or scalar automorphism factors.

---

## 2. Review S0 / R3-A: Contract & Architectural Alignment

Prior to implementation, the research team re-examined the project contract, master research plan, and integration map:
1. **Contract Synchronization**: `docs/rapidflow_apx_contract.md`, `docs/rapidflow_apx_research_plan.md`, and `docs/rapidflow_apx_integration_map.md` have been updated to reflect Phase P2 completion (PR #31 merged into `QuangThang`, Issue #30 closed) and Phase P3 active status. Historical provenance and earlier SHA references are preserved intact.
2. **Strict Domain D-R Enforcement**:
   - $0 \le k < \lambda(Q)$ where $\lambda(Q)$ is computed natively using exact minimum cut algorithms on $Q$.
   - Input cases with $k \ge \lambda(Q)$ (Domain D-G) or disconnected queries are rejected fail-closed.
3. **Event Boundary Correctness**:
   - Single-edge update events occur if and only if the updated edge is matched to an anchor query edge and $|R(f)| = k$ on the active snapshot ($G_{\text{after}}$ for INSERT, $G_{\text{before}}$ for DELETE).
   - Uniqueness theorem guarantees zero duplicate raw emissions across distinct anchors.
4. **Independent M0 Oracle Verification**:
   - The native engines contain zero dependencies on Python code or Oracle logic.
   - Verification is performed externally by the test runner comparing native output bit-for-bit against the independent Ground Truth Oracle across 840 corpus cases and 56 dynamic streams.

---

## 3. Decisions Table (KEEP / ADAPT / NEW / DEFER / STOP)

| Component / Mechanism | Action | Rationale |
| :--- | :---: | :--- |
| `ApxAuthoritativeGraph` | **KEEP** | Hybrid CSR + `GlobalEdgeView` overlay provides authoritative graph ground truth and fast neighbor iteration. |
| `ApxTypedCodec` | **KEEP** | Provides 64-bit ID decoding, typed label interning, and ascending query canonical ordering. |
| `ApxTransactionManager` | **KEEP** | Enforces ACID step execution, reentrancy protection, and zero-allocation rollback. |
| `ApxTupleSink` | **KEEP** | Enforces canonical tuple staging, injectivity checking, and duplicate rejection. |
| `ApxQueryPlan` | **NEW** | Natively computes $\lambda(Q)$, query edge indexing, compatibility tables, and M1 variant plans. |
| `ApxM1EnumEngine` | **NEW** | Natively implements M1-enum-Exact-$k$ baseline event enumeration. |
| `ApxM2EventEngine` | **NEW** | Natively implements bare M2-Event-$k$ direct DFS with deferred vertex commitments. |
| `ApxStreamingEngine` | **NEW** | Coordinates transaction execution, relevancy filtering, and publication. |
| `rapidflow_apx_native` CLI | **NEW** | Provides standalone execution harness with resource caps and fail-closed error handling. |
| Approximate NLF / $\delta'$ | **DEFER** | Deferred to Phase P4. |
| Large NetFlow Benchmarks | **DEFER** | Deferred to Phase P5. |
| Automorphism Scalar Multiplier | **STOP** | Disallow multiplying counts by automorphism group size; all valid embeddings must be explicitly emitted. |
| Python Prototypes in Production | **STOP** | Production native engine must be pure C++ with no runtime call to Python. |

---

## 4. Checkpoints & Review Milestones

### 4.1. S0 / R3-A: Contract & Architectural Alignment
- Synchronized all master coordination docs (`rapidflow_apx_contract.md`, `rapidflow_apx_research_plan.md`, `rapidflow_apx_integration_map.md`).
- Authored design specification `docs/rapidflow_apx_native_engines_design.md`.

### 4.2. R3-B: First Witness Validation Review
Both native C++ engines (`ApxM1EnumEngine` and `ApxM2EventEngine`) compiled cleanly and were evaluated against the independent Python M0 Ground Truth Oracle on the primary target witnesses:
1. **$K_4$ ($k=0$) Full Symmetry Witness**:
   - Both engines emitted exactly 24 ENTERED tuples on insertion of $(2, 3)$ and exactly 24 EXITED tuples on deletion of $(2, 3)$.
   - All 24 tuples matched the M0 Oracle bit-for-bit; zero duplicate emissions (`raw_emissions == unique_emissions == committed_emissions == 48`).
2. **$\text{Diamond}$ ($k=1$) Event Leaf Witness**:
   - Evaluated under the strict event boundary lemma $|R(f)| = k = 1$.
   - Both native engines matched the M0 Oracle bit-for-bit with exactly 8 ENTERED tuples.
   - M1 executed 40 variants with 15 raw exact results and 7 missing set rejections ($R(f) \ne D$).
   - M2 executed 54 edge decisions, 54 missing branches, and 23 commitment checks with zero commitment rejections.
3. **$C_4$ ($k=1$) Deferred Vertex Witness**:
   - When the search branch chose MISSING on frontier edge $(1, 2)$, vertex 2 was deferred and mapped later via $(3, 2)$.
   - Both native engines matched the M0 Oracle bit-for-bit with exactly 8 ENTERED tuples.

**Reviewer Verdict at R3-B (KEEP / CHANGE / DEFER / STOP)**:
- **KEEP**: The shared foundation APIs, zero-allocation rollback, and frontier indexing are proven sound and require no changes.
- **KEEP**: Work counters properly record attempted work (charging before candidate loops and edge decisions).
- **KEEP**: Both native paths operate strictly in C++ without any runtime call to Python.
- **PROCEED**: Proceed to full 840 corpus execution and 56-stream stateful matrix verification.

### 4.3. R3-C: Final Gate Review (Pending Full Corpus Verification)

