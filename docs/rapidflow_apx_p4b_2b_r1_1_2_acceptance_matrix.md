# RapidFlow-APX P4b-2B R1.1.2 Acceptance Matrix
## Corrective Hardening, ODR Integrity, Historical Schema Authorization & Full Regression Verification

**Handoff Status:** `P4B_2B_R1_1_2_READY_FOR_INDEPENDENT_REVIEW`  
**Repository:** `iamqthanq/Seminar_RapidFlow`  
**Issue:** [#38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38)  
**Pull Request:** [#39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39) (OPEN / DRAFT)  
**Branch:** `feature/rf38-p4b-optimization`  
**Worktree:** `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B`  
**Candidate Binary SHA-256:** `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78`  

---

## 1. R1.1.2 Hardening Gates (RV03 & RV04)

| Gate ID | Requirement | Implementation & Verification Evidence | Status |
|---|---|---|---|
| `RV03-G01` | Token-identical class definition | Unconditional `struct ApxAuthoritativeGraphTestAccess;` and `friend struct ApxAuthoritativeGraphTestAccess;` inside `class ApxAuthoritativeGraph` across all translation units. | **PASS** |
| `RV03-G02` | Test access method encapsulation | Implementation of `ApxAuthoritativeGraphTestAccess::merge_neighbor_ranges` conditioned on `#ifdef APX_TEST_ACCESS`. | **PASS** |
| `RV03-G03` | Static library symbol purity | `nm rapidflow_apx_lib/librapidflow_apx_foundation.a \| grep -i TestAccess` returns 0 hits (no test symbols in production library). | **PASS** |
| `RV03-G04` | Negative compilation test | Direct compilation without `-DAPX_TEST_ACCESS=1` fails cleanly with incomplete type error. | **PASS** |
| `RV03-G05` | Positive test binary compilation | Test binary compiled with `-DAPX_TEST_ACCESS=1` compiles cleanly and executes merge test suite with 0 errors. | **PASS** |
| `RV04-G01` | Deprecate boolean historical bypass | `allow_historical_version: bool` removed from `validate_nlf_configuration` and `run_workload_on_binary`; boolean bypass strictly rejected. | **PASS** |
| `RV04-G02` | Cryptographic provenance authorization | Introduced `VerifiedHistoricalArtifact` dataclass requiring binary SHA-256, commit SHA, profile, and schema version. | **PASS** |
| `RV04-G03` | Immutable authorized binaries registry | `AUTHORIZED_HISTORICAL_BINARIES` defines permitted SHA-256, commit SHA, profiles, and versions for P4b-2A baseline and starting P4b-2B. | **PASS** |
| `RV04-G04` | Live execution downgrade prevention | Live execution path strictly enforces schema version 2 and `configuration_initialized=true`; rejects version 1 artifacts without authorization. | **PASS** |
| `RV04-G05` | On-disk binary SHA verification | `run_workload_on_binary` verifies on-disk binary SHA-256 against `historical_auth.binary_sha256` before execution. | **PASS** |
| `RV04-G06` | Dedicated historical validation API | `validate_historical_artifact_configuration` provides explicit, audited validation path for historical evidence artifacts. | **PASS** |

---

## 2. RV04 Verification Matrix (RV04-T01 through RV04-T19)

| Test ID | Description | Execution & Assertion | Status |
|---|---|---|---|
| `RV04-T01` | Live version 2 valid | Output with version 2, initialized true, all valid counters passes live validation. | **PASS** |
| `RV04-T02` | Live version 1 without auth fails | Version 1 output passed to live validation rejected with downgrade protection error. | **PASS** |
| `RV04-T03` | Live missing configuration_initialized fails | Version 2 output missing `configuration_initialized` marker rejected. | **PASS** |
| `RV04-T04` | Live marker false fails | Version 2 output with `configuration_initialized=false` on successful execution rejected. | **PASS** |
| `RV04-T05` | Version 1 without provenance fails | Historical artifact passed without `VerifiedHistoricalArtifact` rejected. | **PASS** |
| `RV04-T06` | Unauthorized binary SHA fails | Artifact with unrecognized binary SHA-256 rejected with authorization error. | **PASS** |
| `RV04-T07` | Historical profile mismatch fails | P4b-2A baseline binary attempting unauthorized `merge` profile rejected. | **PASS** |
| `RV04-T08` | Historical commit SHA mismatch fails | Correct binary SHA paired with wrong commit SHA rejected. | **PASS** |
| `RV04-T09` | Version 1 BUFFER historical valid passes | P4b-2A baseline artifact with `buffer` profile and valid counters passes validation. | **PASS** |
| `RV04-T10` | Version 1 MERGE historical valid passes | Starting P4b-2B artifact with `merge` profile and valid counters passes validation. | **PASS** |
| `RV04-T11` | Version 1 LEGACY historical valid passes | Artifact with `legacy` profile and zero neighbor counters passes validation. | **PASS** |
| `RV04-T12` | Version 2 null counter fails | Output with `None` counter value rejected with invalid counter error. | **PASS** |
| `RV04-T13` | Version 2 bool counter fails | Output with boolean counter value rejected with invalid counter error. | **PASS** |
| `RV04-T14` | Mode mismatch fails | Requested mode `merge` with effective mode `buffer` rejected. | **PASS** |
| `RV04-T15` | Boolean historical_auth fails | Passing `historical_auth=True` rejected with boolean bypass error. | **PASS** |
| `RV04-T16` | Candidate binary historical auth SHA mismatch | Running candidate release binary with historical authorization fails on binary SHA mismatch. | **PASS** |
| `RV04-T17` | Historical missing buffer counter fails | Version 1 buffer artifact missing mandatory counter rejected. | **PASS** |
| `RV04-T18` | Historical buffer nonzero merge counter fails | Version 1 buffer artifact with non-zero merge counter rejected. | **PASS** |
| `RV04-T19` | Historical merge partition violation fails | Version 1 merge artifact violating `retrieval == linear + fast + fallback` rejected. | **PASS** |

---

## 3. Regression & Parity Verification Gates

| Gate ID | Verification Domain | Expected Result | Actual Result | Status |
|---|---|---|---|---|
| `REG-01` | 921 Whole-Output 3-Way Parity | 921 workloads M2 LEGACY vs BUFFER vs MERGE exact identity | 921 / 921 matched, 0 discrepancies, `parity-921-differences.json = []` | **PASS** |
| `REG-02` | Starting P4b-2B Binary Parity | 921 workloads MERGE vs starting binary exact match | 921 / 921 matched, 0 discrepancies | **PASS** |
| `REG-03` | Frozen 5-Control Parity | 379 controls vs accepted P4b-2A baseline exact match | 379 / 379 matched (359 success + 20 negative), 0 discrepancies | **PASS** |
| `REG-04` | Six Canonical Suites | All stages (hand, corpus, stream) pass across 5 configurations | 15 / 15 stages passed, 0 failures | **PASS** |
| `REG-05` | Property-Based Testing (PBT) | 78 streams, 780 updates differential verification | Passed, 0 tuple/counter/duplicate discrepancies | **PASS** |
| `REG-06` | Traversal Trace Parity | Exact 3-way trace parity on 8 canonical fixtures | 8,541 rows exact match across modes, 20 fault seams PASS | **PASS** |
| `REG-07` | Combined ASan + UBSan + Trace | Unified build execution without memory or UB errors | Clean exit 0 under `ASAN_OPTIONS=detect_leaks=0` | **PASS** |
| `REG-08` | Upstream RapidFlow Source | pristine at pinned `2ea4f4c3...` | 0 modified, 0 untracked files | **PASS** |
| `REG-09` | Evidence Package Completeness | Package in `/tmp/rapidflow-p4b2b-r1-1-2/` | 195 files, `evidence-manifest.json`, `review-evidence-r1-1-2.tar.gz` | **PASS** |
