# RapidFlow-APX P4b-3B.0 R2.1 Candidate Bundle Binding & Activation Contract

## 1. Two-Bundle Separation Architecture

`scripts/p4b_correctness_registry.py` enforces an explicit reviewable registry of trusted bundles (`TRUSTED_BUNDLES`), keeping historical R1 provenance and candidate R2 provenance isolated without runtime self-reference:

1. **`HISTORICAL_R1_BUNDLE` (Preserved Read-Only Historical Baseline):**
   - **Binary:** `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75` (`941,216` bytes, C++ commit `97d8636b9567d53d3e083c48d61b46feb40ae540`)
   - **Archive:** `review-evidence-p4b3a-r1.tar.gz` (`22b5245d24d47b00fec6f3ab7b0960a6d6a27636759cc9c7c1259a343882cfe9`, `34,057,918` bytes, prefix `pre-r09/`, manifest `cdd7ae9e...`)
   - **Registry:** `configs/rapidflow_apx_p4b_correctness_registry.json` (`a122454b3cdd5b00c221a3f3d847aea372c7e1f9889c4fb1314017c16d4e2117`, `505,379` bytes)
2. **`R2_CANDIDATE_BUNDLE` (Active Candidate Measurement Target):**
   - **Binary:** `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876` (`970,632` bytes, C++ commit `115ce2844d91f6a6588fe0ac50b1179bc3b38012`)
   - **Archive:** `p4b-r2-correctness-reference-bundle.tar.gz` (`8efd7c65856c675773249d19b20165106d92917af56913be3730b5a2de044464`, `33,681,113` bytes, `302` members under `r2-ref/`, manifest `37d649b697212de0e0779a1d33e1fdc990cee5f6473b8cf2b351c365cd0fda15`)
   - **Registered replay revision (`CANDIDATE_REPLAY_REV` / all `250` `replay.jsonl` rows):** `115ce2844d91f6a6588fe0ac50b1179bc3b38012`
   - **Commit provenance roles:**
     - `Native source commit`: `115ce2844d91f6a6588fe0ac50b1179bc3b38012` (production C++ source build revision of `3fa35111...`).
     - `Registered replay revision`: `115ce2844d91f6a6588fe0ac50b1179bc3b38012` (provenance identifier stamped across all `250` `r2-ref/replay.jsonl` rows and `TRUSTED_BUNDLES["R2_CANDIDATE_BUNDLE"]`; identifies the target native C++ source revision associated with the reference bundle rather than claiming every Python orchestration script ran verbatim from commit `115ce284...`).
     - `R2 tested logic commit`: `101272cf85087df621d5e77118ab12a774720a4e` (Python/test commit certified by R2 full regression).
   - **Registry:** `configs/rapidflow_apx_p4b_r2_1_candidate_registry.json` (`40a28b4d963d8c652bc49ab231aa2ccb6626e3de9b4badc3b3a13a03620fe0b4`, `505,468` bytes)
   - **Certification breakdown (`250` entries):** `160 M0_VERIFIED`, `64 DIFFERENTIAL_M2_VERIFIED`, `16 CORRECTNESS_NOT_FULLY_CERTIFIED`, `10 C3_BUDGET_UNAVAILABLE`

> **Note:** Prior R2 preview artifacts (`configs/rapidflow_apx_p4b_r2_candidate_registry.json` = `90fc2f3e...` and `review-evidence-p4b3b0-r2.tar.gz` = `f1dcbaff...`) remain preserved on disk and are explicitly rejected as active production bundles.

## 2. Fail-Closed Archive & Registry Verification (`unpack_references` / `validate_registry`)

- `resolve_trusted_bundle(bundle)` rejects non-dict/non-string roots or non-string/unhashable `bundle_id` (`INVALID_TRUST_BUNDLE_SHAPE`), missing identity fields or invalid field scalar types (`MISSING_BUNDLE_IDENTITY_FIELD`, `INVALID_TRUST_BUNDLE_SHAPE`), unknown bundle identifiers (`UNREVIEWED_TRUST_BUNDLE`), and altered identity fields (`BUNDLE_IDENTITY_MISMATCH`), returning only the immutable trusted entry from `TRUSTED_BUNDLES`.
- `unpack_references` verifies archive byte size, archive SHA-256 (`ARCHIVE_IDENTITY_MISMATCH` / `R1_ARCHIVE_IDENTITY_MISMATCH`), `evidence-manifest.json` SHA-256 (`MANIFEST_HASH_MISMATCH` / `R1_MANIFEST_HASH_MISMATCH`), exact member inventory (`ARCHIVE_MEMBER_INVENTORY_MISMATCH`), path-traversal safety (`UNSAFE_ARCHIVE_PATH`), and per-member SHA-256 + byte sizes (`ARCHIVE_MEMBER_IDENTITY_MISMATCH`).
- `rebuild_from_store` independently re-computes M0 oracles against frozen workload fixtures (`ORACLE_RECOMPUTATION_MISMATCH`), checks every `replay.jsonl` row against pinned `binary_sha256`, `native_source_commit`, `replay_source_commit`, `protocol_sha256`, `manifest_sha256`, and `schedule_sha256` (`REFERENCE_ROW_IDENTITY`), verifies compressed and raw reference SHA-256s (`RAW_EVIDENCE_MISMATCH`, `REFERENCE_RAW_IDENTITY_MISMATCH`), and re-runs `replay.validate_replay` across all `250` workload/variant pairs.
- `require_canonical_registry` requires exact structural and content equality (`REGISTRY_DOES_NOT_MATCH_TRUSTED_ARCHIVE`).

## 3. Detached Activation Schema (`p4b-detached-activation-v2`)

`scripts/p4b_activation.py` enforces:
- Active envelope schema `ENVELOPE = "p4b-detached-activation-v2"` (`LEGACY_R1_ENVELOPE = "p4b-detached-activation-v1"` explicitly fails closed with `HISTORICAL_R1_ACTIVATION_DEPRECATED`; legacy `r1_evidence` field fails with `HISTORICAL_FIELD_NOT_ALLOWED_IN_V2`).
- Required bundle selector `bundle_id = "R2_CANDIDATE_BUNDLE"` and nested `correctness_bundle` matching `bundle_identity_payload(resolve_trusted_bundle("R2_CANDIDATE_BUNDLE"))`.
- Exact SHA-256 match for active native binary (`3fa35111...`), C++ source commit (`115ce284...`), candidate registry (`40a28b4d...`), protocol, manifest, schedule, statistical amendment, environment snapshot, and expanded `SOURCE_PATHS` (now including `configs/rapidflow_apx_p4b_r2_1_candidate_registry.json`, `scripts/canonical_output_comparator.py`, `scripts/p4a_evidence_contract.py`, `scripts/replay_rapidflow_apx_p4b.py`, `scripts/dstar_oracle.py`).
- Production CLI anchor store `configs/rapidflow_apx_p4b_activation_anchors.json` remains `approved_envelopes: {}` (`b74e48dc...`).

## 4. Negative Matrix Summary (`A-N01..A-N20`)

All `20` negative binding cases plus `v1` deprecation checks pass in `tests/test_rapidflow_apx_p4b_3b0_r2_1.py::TestR21CandidateBinding::test_04_negative_matrix_a_n01_through_a_n20`, confirming zero cross-bundle reuse between R1 and R2 binaries/registries/archives and positive readiness (`test_03`) without launching any native subprocess or populating production anchors.
