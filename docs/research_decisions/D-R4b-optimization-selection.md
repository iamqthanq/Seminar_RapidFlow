# Research Decision Record: D-R4b — Optimization Selection after P4b-3B.1 Confirmatory Measurement

**Document ID:** `docs/research_decisions/D-R4b-optimization-selection.md`
**Milestone:** Phase P4b-3B.1 → R4b
**Parent Issue:** [Issue #38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38) / Draft PR #39
**Branch:** `feature/rf38-p4b-optimization` (target `QuangThang`)
**Status:** DRAFT — pending Coordinator sign-off
**Approval model (honest disclosure):** a **single person** (Thiện) acted as Coordinator *and* reviewer. There was **no independent second review**. The Gemini `STATIC_ACCEPT` in the handoff covers supplied static evidence only; it is not a review of this run.

---

## 1. Decision

| Variant | R4b disposition | Reason code (from the registered classifier) |
| :--- | :---: | :--- |
| A_STATIC | `INCONCLUSIVE` | `INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE` (priority 4) |
| A_RESIDUAL | `INCONCLUSIVE` | `INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE` (priority 4) |
| B_MERGE | `INCONCLUSIVE` | `INSUFFICIENT_COVERAGE_OR_EXCESSIVE_NOISE` (priority 4) |

- **Selected configuration for P5: BARE (M2, NLF none, legacy neighbor retrieval)**, the valid fallback. No evidence in this run supports enabling A or B.
- **P4b-4 (combined A+B): SKIPPED.** `conditional_ab_trigger_met = false` for all three variants (requires all priority 1..5 gates satisfied and S ≥ 1.05 with L > 1.00).
- The classifier outcome is reported as produced; thresholds were not changed after seeing data.

## 2. Identity of what was run

| Item | Value |
| :--- | :--- |
| Repo HEAD at run | `a3b1f7a` (anchor commit) on top of `9400a091d5077ac5011e899b0dc059bd9a0b90c7` |
| Implementation revision | `9f7701b960d5e62546cf34f61ae6ba8e1d275aff` |
| Upstream RapidFlow | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` (cloned locally by the agent; clean) |
| Native binary | `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876`, 970632 B |
| Registry (R2_CANDIDATE_BUNDLE) | `40a28b4d963d8c652bc49ab231aa2ccb6626e3de9b4badc3b3a13a03620fe0b4` |
| Reference archive | `8efd7c65856c675773249d19b20165106d92917af56913be3730b5a2de044464` |
| Protocol / manifest / schedule | `70836770…acbb` / `09c78158…4869` / `824d4b09…5e6f` |
| Activation envelope | `p4b-detached-activation-v2`, SHA-256 `d7a5843431da902650e35d03048fb2111bde4272b0189956e3c54bc25fd6094f`, experiment `p4b3b1_primary_001`, phases primary+diagnostic, `R1_REGISTERED` / `R1_REGISTERED` |
| Session path | `/home/thien408/Projects/2026-2027/seminar/p4b_session/p4b3b1_primary_001` (outside repo) |

**Host:** Arch Linux, kernel 7.2.14-arch1-1, Intel i5-13420H (8C/12T), governor `powersave` (kept by Coordinator decision), ~16 GB RAM, Python 3.14.9, g++ 16.2.1 (binary was prebuilt; not rebuilt). This differs from the handoff's reference host (Ubuntu 24.04, i5-8265U, Python 3.12.3). Results apply to this host only.

## 3. Execution accounting

| Phase | Command flag | Window (UTC) | Exit | Terminal records |
| :--- | :--- | :--- | :---: | :---: |
| Primary (200 warmup + 1000) | `--execute-primary` | 18:04:27 – 18:18:46 | 0 | 1200 (`SESSION_PAUSED`) |
| Diagnostic (250) | `--execute-diagnostic` | 18:21:29 – 18:29:17 | 0 | 1450 (`SESSION_COMPLETED`) |
| Official analysis | `--analyze-session` | 23:36:01 – 23:39:29 | 0 | 1450 raw attempts, 1450 unique slots |

Ledger: 1450 START / 1450 FINISH. No retries, interruptions, halts or incidents. stderr empty in all three runs. Raw commands, stdout/stderr and exit codes: `~/p4b_approval/run_primary|run_diagnostic|run_analyze/` (outside the repo). Hash manifests: `~/p4b_approval/SHA256SUMS_APPROVAL_AND_RUNS` and `~/p4b_approval/SHA256SUMS_SESSION`.

Analysis report: `~/p4b_approval/run_analyze/stdout.txt`, SHA-256 `2af7e9a877fd0541714d203b86b772778763db1376c5c6854711731b4a93caa3` (mode `CONFIRMATORY_ANALYSIS`).

## 4. Results (paired median speedup of variant vs BARE; >1 means faster)

| Variant | Eligible pairs | Median S | Geo-mean | 95% CI (bootstrap, unadjusted) | Noisy pairs | Worst RSS overhead |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| A_STATIC | 48 | 1.0176 | 1.0832 | 0.9995 – 1.0344 | 28 / 48 | 0.67% |
| A_RESIDUAL | 48 | 0.9521 | 1.0042 | 0.9381 – 0.9873 | 30 / 48 | 0.63% |
| B_MERGE | 48 | 1.0330 | 1.0359 | 0.9941 – 1.0595 | 25 / 48 | 0.71% |

- Centered-log-bootstrap p-values (unadjusted): A_STATIC 0.057, A_RESIDUAL 0.0015, B_MERGE 0.040. `holm_sensitivity.adjusted_p_values` is empty in the report, so no Holm-adjusted values are claimed here.
- Noise gate: the registered rule marks the result INCONCLUSIVE when ≥ 20% of eligible pairs are flagged (J = max/min across repetitions > 1.50). 25–30 of 48 (52%–63%) were flagged for every variant. **Outliers were not removed.**
- No variant reaches the ADVANTAGE threshold (S ≥ 1.15 with L > 1). A_RESIDUAL's CI lies entirely below 1 but the noise gate takes precedence under the registered priority order.
- All other gates passed for every variant: evidence integrity, overall/continuity/discrimination coverage, complete DISC blocks, no semantic mismatch, no missing RSS, no variant-only resource regression.

## 5. Coverage and censoring

- 48 / 50 workloads complete for BARE and each variant (continuity 30/30, discrimination 18/20, complete DISC blocks 9).
- 2 workloads (`DISC_07_K0`, `DISC_07_K1`) were censored as `WORK_BUDGET_EXCEEDED` (10 slots each for BARE and the variant). This matches the pre-lock disposition `RETAIN_DISC_07_UNCHANGED`; caps were not raised.
- Registry limits still apply: not all 250 candidate entries are oracle-certified (160 M0_VERIFIED, 64 DIFFERENTIAL_M2_VERIFIED, 16 CORRECTNESS_NOT_FULLY_CERTIFIED, 10 C3_BUDGET_UNAVAILABLE); C3 BARE is a limited control, not an independent oracle.

## 6. Diagnostic attribution

The report contains PROFILE_ON nested-timer counters per variant (`diagnostic_attribution`). They are descriptive only. Observer cost is not calibrated, nested scopes must not be summed, and no observer-free gain is inferred from them.

## 7. Limitations and what is NOT claimed

- Single approver; no independent review of the run or of this record.
- The cause of the high timing noise is **not established** by this session. Possible contributors (e.g. `powersave` governor, shared desktop host) are hypotheses, not findings.
- Host, Python version and kernel differ from the handoff reference host; results are not transferable.
- No claim of general speedup, scalability, NetFlow behavior or novelty. The A_STATIC (≈ +1.8%) and B_MERGE (≈ +3.3%) medians are point estimates inside noise.
- A negative/inconclusive outcome is a valid scientific result under the preregistration.

## 8. Options for the Coordinator (not decided here)

1. Accept INCONCLUSIVE and proceed to P5 with BARE.
2. Propose, as a *new* separately approved campaign, a re-measurement under a quieter, controlled host policy (changing environment requires a new envelope and authorization). This session's ledger must not be altered or re-used.

## 9. Sign-off

- Coordinator decision: [ticked] ACCEPT this record as the R4b outcome — Thiện, date: 7am, 26/11/2026
- Independent review: **NOT PERFORMED**
