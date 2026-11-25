# RapidFlow-APX P4b-2B R1.1.3 Implementation Report
## Minimal Provenance Closure: Correct Historical Identity, Verified Evidence Binding & Clean-Revision Final Regression

- **Proposed Handoff Verdict:** `P4B_2B_R1_1_3_READY_FOR_INDEPENDENT_REVIEW`
- **Repository:** `iamqthanq/Seminar_RapidFlow`
- **Issue:** [#38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38) (OPEN)
- **Pull Request:** [#39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39) (OPEN / DRAFT)
- **Feature Branch:** `feature/rf38-p4b-optimization`
- **Base Integration Branch:** `QuangThang`
- **Worktree:** `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B`

---

## 1. Executive Summary

This report documents the minimal targeted closure **Phase P4b-2B R1.1.3** following the independent technical lead verdict (`P4B_2B_CORE_CORRECTNESS_ACCEPTED`, `P4B_2B_R1_1_2_PROVENANCE_CLOSURE_REQUIRED`, `P4B_3_NOT_YET_AUTHORIZED`). R1.1.3 introduces zero algorithmic, native C++, or matching changes; it resolves strictly the two historical provenance defects (`R112-F01` and `R112-F02`), completes clean-revision full repository regression (`R113-F03`), and reconciles affected documentation and evidence digests (`R113-F04`).

| Identity / Artifact | Verified Value |
|---|---|
| **R1.1.3 Starting HEAD** | `873f5c89205cec09c5a4c838a634bfe196583cb7` |
| **Tested Implementation Commit** | `08f63fe218a8246710cc3039fec56a79df627d6c` (`fix(p4b): bind historical evidence to verified provenance (#38)`) |
| **Final Documentation Commit** | Child documentation-only commit introducing this report (recorded separately in push handoff log so no self-referential commit hash is embedded here) |
| **Native Binary Identity (Unchanged)** | `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78` (`854,160` bytes; 0 C++/CMake edits since `873f5c8`) |
| **Pinned Upstream RapidFlow** | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (Pristine, `git status --porcelain` empty) |
| **Clean-Revision Full Regression** | **827 discovered, 827 passed, 0 failed, 0 errors, 0 skipped, exit `0`** (289.801s on clean commit `08f63fe2...`) |
| **Proposed Handoff Verdict** | `P4B_2B_R1_1_3_READY_FOR_INDEPENDENT_REVIEW` |

---

## 2. Finding Closure Register

| Finding | Reproduction | Correction | Evidence | Verdict |
|---|---|---|---|---|
| `R112-F01` | `git cat-file -t` failed on R1.1.2 registry commits (`d21575c1c70d1f7bc2380d8172ae4044dacfc3f` was truncated P4b-2A binary SHA; `bdb1fd26c92d56d11f71477708eb107e371a5330` did not exist). | Corrected `AUTHORIZED_HISTORICAL_BINARIES` to actual verified Git commits (`d2108ec6...` for P4b-2A, `bdb1fd2e...` for P4b-2B) and recorded documentation handoff commits (`11e57bc3...`, `adf5f455...`) separately. | Verified via `git cat-file -t` (`commit`) and [run_rapidflow_apx_engines.py](file:///home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B/scripts/run_rapidflow_apx_engines.py); `source-identities.json`; `TestR113ProvenanceClosure::test_r113_a01_to_a05_historical_git_identities`. | **CLOSED** |
| `R112-F02` | Caller forged current MERGE v2 output to v1, constructed `VerifiedHistoricalArtifact` with registry metadata, and `validate_historical_artifact_configuration()` returned `True` (`""`). | Separated target execution selector `HistoricalBinaryRequest` from attested `VerifiedHistoricalArtifact`; bound `verify_historical_artifact()` and `run_verified_historical_binary()` to trusted manifest/archive anchors or actual on-disk binary execution bytes, raw workload hashes, canonical payload digests, and in-process HMAC seal verification. | Before/after reproduction on real native MERGE output (`before_873f5c8.accepted = true`, `after_r113.accepted = false`) in `forged-witness-before-after.json` and `test_r113_b02_b03_b11_forged_witness_and_caller_spoof_rejected`. | **CLOSED** |
| `R113-F03` | R1.1.2 `full-regression-r1-1-2.log` exited `1` (`822` tests, `821` passed, `1` failed on `test_group_a_provenance_and_build`); independent targeted rerun passed `20/20` on clean state. | Executed complete repository regression (`discover -s tests -v`) strictly after committing implementation (`08f63fe2...`) and confirming clean worktree (`git status --porcelain` empty). | `tested-revision.json`, `commands.log`, `full-regression-r1-1-3.log` (`827/827` passed, `0` skipped), `full-regression-r1-1-3.exit` (`0`), `check_git_provenance()` (`repo_clean=True`). | **CLOSED** |
| `R113-F04` | R1.1.2 report misstated P4b-2A/2B commits, overbroad "cryptographic provenance" wording, failed regression omission, trailing spaces, PR #39 stale body, and mismatched manifest SHA-256 table values (`86cae4da...` vs actual `648a4124...`). | Added superseding correction notices and normalized trailing whitespace in R1.1.2/R1.1 docs; updated PR #39 description; authored this source-backed report with verified manifest/archive SHA-256 values and honest terminology. | Updated [rapidflow_apx_p4b_2b_r1_1_2_implementation_report.md](file:///home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B/docs/rapidflow_apx_p4b_2b_r1_1_2_implementation_report.md) and sibling docs; `git diff --check 873f5c8..HEAD == 0`; PR #39 OPEN/DRAFT check. | **CLOSED** |

---

## 3. Historical Identity Register

| Baseline / Revision | Tested Source Commit | Documentation Handoff Commit | Native Binary SHA-256 | Schema Version | Supported Engine / NLF / Neighbor Modes |
|---|---|---|---|---|---|
| **P4b-2A Accepted Baseline** | `d2108ec6fee083ce0716e2935e07750e56461a72` | `11e57bc388618c59eab605f0f27e6f05dd9bbcc3` | `d21575c1c70d1f7bc2380d8172ae4044dacfc3f6ec464d06884555c284d36b84` | `1` (3 buffer counters; merge fields forbidden) | M1 (`none`/`legacy`), M2 (`none`/`static`/`residual` with `legacy`; `none` with `buffer`). **MERGE unsupported.** |
| **P4b-2B Starting Release** | `bdb1fd2eda4d7881041e6e32500d62af6da1505d` | `adf5f455e25180dd1e22a5b86446f73ec2cc8c8a` | `260e5627799b624bf010c4631011db8874ebf769bf4b4cdd62831f1ad25a9608` | `1` (7 transitional counters across all modes) | M1 (`none`/`legacy`), M2 (`none`/`static`/`residual` with `legacy`; `none` with `buffer` or `merge`). |
| **P4b-2B R1.1.2 Starting** | `873f5c89205cec09c5a4c838a634bfe196583cb7` | `873f5c89205cec09c5a4c838a634bfe196583cb7` | `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78` | `2` (7 strict counters across all modes) | Same canonical mode matrix under strict v2 validation. |
| **P4b-2B R1.1.3 Tested** | `08f63fe218a8246710cc3039fec56a79df627d6c` | Child documentation commit | `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78` | `2` (unchanged native binary) | Identical canonical matching matrix; hardened Python provenance verifier. |

Source-to-binary associations are established from documented accepted build logs (`/tmp/rapidflow-p4b2a-4TsBiw/release-binary.log`, `/tmp/rapidflow-p4b2a-4TsBiw/tested-revision.txt`) and preserved binaries in `/tmp/rapidflow-p4b2b-r1-1-2/binaries/` and `/tmp/rapidflow-p4b2b-r1-1-3-Iv5mog/binaries/`, not merely by finding commit objects in Git.

---

## 4. Historical Authorization & Verification Architecture

1. **Live Execution Boundary (`run_workload_on_binary` with `historical_auth=None`):**
   - Strictly enforces current schema version `2` and `configuration_initialized == True` for initialized executions.
   - Preserves legitimate early startup failures (`DOMAIN_ERROR` exit `3` with `configuration_initialized=False` and `version=2`, or `CLI_ARG_ERROR` exit `1` before initialization per FAR lifecycle rules), while rejecting any live attempt to pass `version=1`.
2. **Historical Binary Replay Boundary (`Workflow A`: `run_verified_historical_binary` & `HistoricalBinaryRequest`):**
   - Computes on-disk SHA-256 of `binary_path` before subprocess execution and matches against `AUTHORIZED_HISTORICAL_BINARIES` (`MappingProxyType` registry).
   - Verifies exact source `commit_sha`, permitted `(engine, nlf_mode, neighbor_mode)` tuple, and micro-containment bounds.
   - Captures exact raw subprocess JSON bytes, mints an attested `VerifiedHistoricalArtifact` (`origin_type="historical_binary_replay"`), and runs full profile-specific schema, counter, step/root reconciliation, and configuration validation (`validate_historical_artifact_configuration`).
   - Current/candidate binaries (`4b7902be...`) are rejected immediately from historical replay.
3. **Offline Trusted-Anchor Boundary (`Workflow B`: `verify_historical_artifact`):**
   - Anchors verification strictly to trusted anchor digests in `TRUSTED_HISTORICAL_ANCHORS`:
     - P4b-2A accepted review archive (`94833a8876a413fc34dce53ba3a037aea4c0f9a2926a59eb505cb2817b0c10cd`).
     - R1.1.2 reviewer-verified manifest (`bcd359cdb36f19e2343df75ba19c2913f918bf184e11818e04b8f9adcae8f82b`, containing frozen-binary replays of P4b-2A baseline and P4b-2B starting binaries; explicitly distinguished from the original P4b-2B session).
   - Verifies archive/manifest bytes against anchor SHA-256, verifies extracted member/file size and SHA-256, deterministically binds raw ledger/output records (`raw_input`, `input_sha256`, `binary_sha256`, CLI modes, raw output bytes), enforces profile-specific counter presence (`3` mandatory + merge forbidden for P4b-2A; `7` mandatory for P4b-2B transitional v1), and rejects substituted output dictionaries via canonical JSON digest verification (`parsed_result_sha256`).
4. **Honest Security & Integrity Scope:**
   - Binary/artifact SHA-256 verification establishes byte identity against established trust anchors; source association relies on accepted documented build evidence.
   - `MappingProxyType` entries, frozen dataclasses, and `_PROVENANCE_HMAC_KEY` provide in-process programming safeguards against accidental or unverified dataclass construction; they do not protect against an adversary with arbitrary code/memory control inside the same Python process.
   - Original P4b-2B (`adf5f45`) summary outputs under `/tmp/rapidflow-p4b2b-evidence/` lack a retained signed/trusted manifest or raw output archive and remain classified as unverified offline summaries; research parity for starting P4b-2B relies instead on re-executing frozen binary `260e5627...` (`historical_binary_replay`) and verified R1.1.2 replays (`bcd359cd...`).

---

## 5. Verification & Regression Results

- **Targeted Regression (`tests.test_rapidflow_apx_neighbor_buffer`, `test_canonical_output_comparator`, `test_rapidflow_apx_p4b`, `test_rapidflow_apx_residual_nlf`, `test_rapidflow_apx_foundation_audit`):** `74 / 74` passed, exit `0` (`53.948s`), including all `RV04-T01`..`RV04-T19` tests and `TestR113ProvenanceClosure` (`R113-A01`..`R113-D07`), plus PBT (`78` streams, `780` updates, `0` discrepancies).
- **Original Forged Witness (`R112-F02` / `R113-B02`):**
  - Before (`873f5c8`): real current `4b7902be...` MERGE output flipped `2 -> 1` accepted (`True`).
  - After (`08f63fe2...`): explicitly rejected (`False`, `[HISTORICAL_PROVENANCE_ERROR] Unverified or incomplete historical artifact (missing bound field 'artifact_id')`).
- **Offline Trusted Evidence & Tampering:** Genuine P4b-2A tar (`canonical-replays.jsonl.gz`) and R1.1.2 manifest (`canonical-replays-r1-1-2.jsonl.gz`) records pass full schema/config certification; missing manifest digest, altered manifest bytes, altered artifact bytes, missing manifest entries, recomputed untrusted manifest digests, wrong binary profile, wrong workload, incompatible engine/modes, and substituted output dicts are all rejected (`forged-witness-before-after.json`, `historical-artifact-verification.json`).
- **379 Frozen Control Comparisons (NEW R1.1.3 executions of `d21575c1...` vs `4b7902be...`):**
  - `M1 NONE LEGACY`: `31` (`27` succ, `4` neg), `0` diffs.
  - `M2 NONE LEGACY`: `87` (`83` succ, `4` neg), `0` diffs.
  - `M2 STATIC LEGACY`: `87` (`83` succ, `4` neg), `0` diffs.
  - `M2 RESIDUAL LEGACY`: `87` (`83` succ, `4` neg), `0` diffs.
  - `M2 NONE BUFFER`: `87` (`83` succ, `4` neg), `0` diffs.
  - **Total:** `379 / 379` (`359` successes + `20` expected negatives), **0 discrepancies** (`diff_canonical_structures` applied uniformly to ALL `379` cases including all `20` negative outcomes with step-level buffer counter parity, excluding only `neighbor_configuration_version` and `0`-asserted `neighbor_merge_*` additions).
- **921 Parity & Starting P4b-2B MERGE Comparison:**
  - Fresh execution of frozen P4b-2B starting binary (`260e5627...`) on all `921` canonical workloads (`919` successes + `2` negatives) with full 7-counter transitional v1 certification vs preserved R1.1.2 `4b7902be...` MERGE ledger: **0 discrepancies** (`16.43s`).
  - Full revalidation of all `921 x 3` (`LEGACY == BUFFER == MERGE`) preserved R1.1.2 ledger records (`canonical-replays-r1-1-2.jsonl.gz`, anchored to manifest `bcd359cd...`) through the updated strict v2 schema & config validator + `compare_3way_canonical_outputs`: **0 discrepancies** (`118,624` evaluations, `56,317` extensions, `55,123` DFS states, `203,695` work units, `13,100` emissions, `38,504` retrievals, `25,672` linear, `12,832` fast-path, `0` fallback).
- **Six Canonical Configurations (Fresh Live R1.1.3 Execution of `4b7902be...`):** All 15 stages (`hand` 25, `corpus` 840, `stream` 672 updates across `all/none/legacy`, `m2/static/legacy`, `m2/residual/legacy`, `m2/none/buffer`, `m2/none/merge`) passed `100%` (`canonical-suite-summary.json`).
- **Full Repository Regression History & Clean-Revision Result:**
  - Prior R1.1.2 (`full-regression-r1-1-2.log`): `822` run, `821` passed, `1` failed (`test_group_a_provenance_and_build`), exit `1`.
  - Independent targeted rerun on clean tree (`20` tests): `20 / 20` passed, exit `0`.
  - **Final R1.1.3 Clean-Revision Full Regression (`full-regression-r1-1-3.log` on clean commit `08f63fe2...`):** **827 discovered, 827 run, 827 passed, 0 failed, 0 errors, 0 skipped, exit `0` (`289.801s`)**, with `git status --porcelain` empty both before and after execution (`commands.log`).

---

## 6. Evidence Package Integrity

All R1.1.3 artifacts reside in isolated directory `/tmp/rapidflow-p4b2b-r1-1-3-Iv5mog/` (complete manifest & tarball hashes recorded in `evidence-manifest.json` after final push):

| File | Size (Bytes) | SHA-256 | Origin |
|---|---|---|---|
| `preflight.log` | 5,197 | `745c396a6c525877daeba912d7dfa199b2643916bf75738966a993c8907cbe81` | New R1.1.3 |
| `source-identities.json` | 1,312 | `d5e1114336d549e86dbc3dd09ca84218dff4d7e925213e389cc8015c41df2b78` | New R1.1.3 |
| `tested-revision.json` | 552 | `419cc1d4847c0f04fec67ae9717d6ec5f9fa3fa156e13bff6f09632daacbe03f` | New R1.1.3 |
| `historical-binary-verification.json` | 444 | `5013f856198a56eb50108c2426780353fe383fd9db10069091da4954fd75af69` | New R1.1.3 |
| `historical-profile-matrix.json` | 4,549 | `a6c1d60d8b44f358754d6a244079ee520f271aa00d7bfd1c20db9d059caac332` | New R1.1.3 replay |
| `historical-artifact-verification.json` | 4,488 | `4177f1d6dae8f4369a083955510ea183249f160f4545869250d01c3e337f71ce` | New R1.1.3 audit of historical |
| `forged-witness-before-after.json` | 833 | `7d3265d1148d136ecfe038a95bf08573339af64f92e6a62afa65ecd121223be1` | New R1.1.3 reproduction |
| `frozen-parity-summary.json` | 873 | `76687927541905cc58176024bbe2e240b398993426c8e462ef8860ee13741dbd` | New R1.1.3 execution |
| `starting-merge-parity-summary.json` | 414 | `55a196d1fb7a0fa25fa1fb7ac927eb5323fb97089d3b24763e9ca970eb95f1ef` | New R1.1.3 replay + R1.1.2 ref |
| `canonical-parity-summary.json` | 627 | `04c3eb7f6e5f2d844f4ef81258f278f7ad03ad5c59344074a3b28f35da3c07ac` | Revalidated R1.1.2 evidence |
| `canonical-suite-summary.json` | 1,383 | `c2d2de5a54be8788e8e6d013d026a7091fdfee04d6b63c91cac8c49d8caf9557` | New R1.1.3 live execution |
| `r113-replays.jsonl.gz` | 1,387,262 | `d3ac3b7c83b914ecc2a6f6665fbd7c523dcff5ff08f3804d284bc67e2d67359b` | New R1.1.3 replay ledger |
| `targeted-regression.log` | 12,739 | `9fec4a3f2fb40432a12095a3f9d32c9871d26c5a6aae23c1fcccef55e7509369` | New R1.1.3 targeted tests |
| `targeted-regression.exit` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | New R1.1.3 (`0`) |
| `full-regression-r1-1-3.log` | 213,270 | `6f0eb4f6b0b97075286899351a3f82ea7f76a8b4f61ffa1c66f3f4d60ed3ab6c` | New R1.1.3 clean-tree run |
| `full-regression-r1-1-3.exit` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | New R1.1.3 (`0`) |
| `historical-preservation.json` | 1,883 | `ca6d72273c9041983e1a64d90026b0f3e76aa10489a4eaaa927c5b3d6e9d7d57` | Audited historical P4a |
| `acceptance-matrix.json` | 6,560 | `69e5676a1c91e6d76208025ae7263685899d8a24acb52dd0d434488286104ef3` | New R1.1.3 gate matrix |

---

## 7. Native and Historical Preservation

- **Native C++ & Binary Unchanged:** `git diff 873f5c8..08f63fe -- extensions/ tests/native/ baselines/` is empty; binary SHA-256 `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78` is byte-identical. Prior R1.1.2 combined ASan+UBSan+Trace (`ASAN_OPTIONS=detect_leaks=0` due to documented upstream `Graph::buildEdgeIndex()` `spp::sparse_hash_map` leak) and standalone Trace (`8,541` rows across 8 fixtures) evidence remain valid for this unchanged native code and are reused without fresh native rebuilds.
- **Historical P4a Worktree (`/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT03`):** All 7 artifacts (`docs/evidence/p4a_evidence_manifest.json` SHA-256 `b6d7777d...`), `p4a_raw_ledger.jsonl` (`441,311` bytes, `34452fb4...`), native binary (`6e4537d0...`), and upstream (`2ea4f4c3...`) verified intact and unmodified.

---

## 8. Acceptance Matrix (24 Focused Gates)

| Gate | Requirement | Evidence | Status |
|---|---|---|---|
| `PC-A01` | Correct P4b-2A source commit | `d2108ec6fee083ce0716e2935e07750e56461a72` verified (`git cat-file -t` == `commit`) | **PASS** |
| `PC-A02` | Correct P4b-2B source commit | `bdb1fd2eda4d7881041e6e32500d62af6da1505d` verified (`git cat-file -t` == `commit`) | **PASS** |
| `PC-A03` | Full binary SHA-256 identities verified | `historical-binary-verification.json` (`d21575c1...`, `260e5627...`, `4b7902be...`) | **PASS** |
| `PC-A04` | Source/build associations documented accurately | Tested vs documentation handoff commits and historical logs distinguished | **PASS** |
| `PC-A05` | Incorrect active provenance references removed | Registry & tests fixed; superseding notices in prior docs | **PASS** |
| `PC-B01` | Actual historical binary identity checked | `run_verified_historical_binary` & `run_workload_on_binary` check on-disk SHA-256 | **PASS** |
| `PC-B02` | Offline artifact bound to trusted manifest | `verify_historical_artifact` verified P4b-2A tar (`94833a88...`) & R1.1.2 manifest (`bcd359cd...`) | **PASS** |
| `PC-B03` | Actual artifact bytes verified | Size + SHA-256 verified before JSON parse; unanchored P4b-2B summary marked unverified | **PASS** |
| `PC-B04` | Original forged historical witness rejected | `forged-witness-before-after.json` + `test_r113_b02_b03_b11` | **PASS** |
| `PC-B05` | Genuine verified historical evidence accepted | `historical-artifact-verification.json`, `historical-profile-matrix.json` | **PASS** |
| `PC-B06` | Manifest/artifact mutation rejected | `test_r113_b01_b04_to_b12` (`B04`–`B08` tamper suite) | **PASS** |
| `PC-B07` | Historical profile-specific counters validated | `P4b-2A` (3 mandatory, merge forbidden) vs `P4b-2B` (7 mandatory) enforced | **PASS** |
| `PC-B08` | Live downgrade protection preserved | Strict v2 + `configuration_initialized=true` enforced on live runs (`D01`–`D07`) | **PASS** |
| `PC-C01` | Existing 19 RV04 requirements preserved | `RV04-T01`..`RV04-T19` passing in `targeted-regression.log` & `full-regression-r1-1-3.log` | **PASS** |
| `PC-C02` | Actual historical replay verification | Live historical replays on `p4b2a-baseline-native` and `p4b2b-release-native` | **PASS** |
| `PC-C03` | 379 frozen comparisons including negatives | `frozen-parity-summary.json` (`379/379`: `359` succ + `20` neg via AST diff, `0` diffs) | **PASS** |
| `PC-C04` | 921 current and starting MERGE parity | `starting-merge-parity-summary.json` & `canonical-parity-summary.json` (`0` diffs) | **PASS** |
| `PC-C05` | Six canonical configurations | `canonical-suite-summary.json` (`15/15` fresh live stages passed) | **PASS** |
| `PC-C06` | Final full regression on committed clean revision | `full-regression-r1-1-3.log` (`827/827`, `0` skipped, exit `0`) on clean `08f63fe2` | **PASS** |
| `PC-C07` | Native implementation and historical evidence preserved | Unchanged native source/binary (`4b7902be...`), upstream pristine, P4a 7 artifacts intact | **PASS** |
| `PC-D01` | Accurate full regression and sanitizer reporting | Honest disclosure of R1.1.2 `821/822`, reused ASan/UBSan (`detect_leaks=0`) | **PASS** |
| `PC-D02` | Tested revision and evidence manifest complete | `tested-revision.json`, `evidence-manifest.json`, `review-evidence-r1-1-3.tar.gz` | **PASS** |
| `PC-D03` | Documentation corrections and clean affected diff | Superseding notices + normalized whitespace (`git diff --check 873f5c8..HEAD == 0`) | **PASS** |
| `PC-D04` | Authorized push, Issue OPEN, PR DRAFT, STOP | Pushed via `scripts/push_issue_branch.sh`; Issue #38 OPEN; PR #39 OPEN/DRAFT | **PASS** |

---

## 9. Governance & Deferred Research Obligations

- **Governance State:** PR #39 remains **OPEN** and **DRAFT** targeting `QuangThang` (description updated to reflect P4b-1A/1B/2A/2B implementation and R1.1.3 closure); Issue #38 remains **OPEN**. No merge executed; no force-push or hook bypass used; Phase P4b-3 has NOT started.
- **Deferred Audits Preserved:** `AUD-P4B-001` (Experimental decision matrix) and `AUD-P4B-002` (Workload freeze and macro timing) remain deferred to P4b-3 preregistration. Correctness acceptance establishes zero runtime performance advantage.

---

## 10. Remaining Limitations

1. Original P4b-2B offline summaries under `/tmp/rapidflow-p4b2b-evidence/` lack a retained trusted manifest/raw archive and cannot be offline-certified (`starting P4b-2B` provenance is instead established via live frozen-binary replay of `260e5627...` and R1.1.2's manifest `bcd359cd...`).
2. `MappingProxyType`, frozen dataclasses, and `_PROVENANCE_HMAC_KEY` protect against accidental/unverified Python caller forgery; they are not cryptographic security boundaries against arbitrary in-process code execution or host compromise.
3. Whole-system LeakSanitizer remains disabled (`detect_leaks=0`) on integrated runs due to the documented upstream RapidFlow leak in `Graph::buildEdgeIndex()` (`spp::sparse_hash_map`).
4. This phase evaluates only matching correctness, provenance, and schema invariants; P4b-3 runtime benchmarking (`PROFILE_OFF`, `AUD-P4B-001`, `AUD-P4B-002`) has not started.
