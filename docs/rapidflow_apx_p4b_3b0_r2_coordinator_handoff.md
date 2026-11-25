# RapidFlow-APX P4b-3B.0 R2 Coordinator & Independent Review Handoff

**Current Handoff State:** `P4B_3B0_R2_READY_FOR_INDEPENDENT_REVIEW`  
**Confirmatory Execution State:** `P4B_3B_1_NOT_AUTHORIZED`

## External Gates Required Before Any P4b-3B.1 Activation

1. **`Independent Technical Acceptance` — `PENDING_INDEPENDENT_REVIEW`:** Independent Reviewer re-audits R2 commits `115ce2844d91f6a6588fe0ac50b1179bc3b38012` (C++), `101272cf85087df621d5e77118ab12a774720a4e` (Python/tests/candidate registry), docs commit `HEAD`, the evidence archive `review-evidence-p4b3b0-r2.tar.gz`, and the Gemini Web packet (`<= 9` `.txt` files).
2. **`Coordinator Execution Authorization` — `PENDING_COORDINATOR_APPROVAL`:** Following Independent Technical Acceptance, Coordinator decides whether to issue a detached activation envelope & anchor entry referencing the candidate R2 binary (`3fa35111...`, `970,632` bytes), `NATIVE_SOURCE_COMMIT = 115ce2844d91f6a6588fe0ac50b1179bc3b38012`, and `TESTED_LOGIC_COMMIT = 101272cf85087df621d5e77118ab12a774720a4e` (plus final `HEAD` containing only `docs/` additions).

## Locked Invariants Preserved by R2

- All `50` workloads, `DISC_07_K0` / `DISC_07_K1`, `500,000` work cap, `512 MiB` address-space cap, `60.0s` wall timeout, `256 MiB` NLF limit, `1,450`-slot Williams schedule, statistical decision thresholds, and `baselines/rapidflow/source` (`2ea4f4c3...`) remain unchanged.
- `configs/rapidflow_apx_p4b_activation_anchors.json` remains empty (`approved_envelopes: {}`).
- Draft PR `#39` remains OPEN / DRAFT and Issue `#38` remains OPEN.
