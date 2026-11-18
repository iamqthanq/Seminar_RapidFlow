# Codex / Gemini handoff — workflow v2

- Repository `iamqthanq/Seminar_RapidFlow` only. Read `AGENTS.md`, `GEMINI.md`, and `docs/BRANCH_POLICY.md` first.
- Use one feature branch from `QuangThang` per approved Issue: `feature/rfNN-topic`.
- Commit locally, push **only** this feature branch with `bash scripts/push_issue_branch.sh`.
- Open one Draft PR to `QuangThang` using `bash scripts/open_issue_pr.sh <ISSUE_NUMBER>`; keep all fixes on this same PR. Never push to QuangThang/main and NEVER merge.
- Keep RapidFlow upstream pristine. Do not implement UACR or any other optimization unless explicitly accepted after profiling and prior-art review.
- Inventory sources and NetFlow provenance. No data mutation to hide failures. Don't mix 10k NewSP update streams with RapidFlow paper's 10% benchmark.
- Distinguish count-only from full explicit delta embeddings. Do not claim correctness or speedup from only a smoke count.
- Every PR requires complete attempted cases, input SHA-256, exact commands, CPU/RAM/compiler/flags, exit code, times, RSS, logs, limitations and independent oracle evidence when requested.
- Classify claims PAPER/BUILD/SMOKE/CORRECT/BENCHMARK/HYPOTHESIS. All phases need manager review, external AI critique and explicit approval before next gate.
- Current Active Direction: **Direction D (Issue #30 / Draft PR #31 / D-INT-02 / Phase P2)**. Objective: Implement and verify reusable native foundation for RapidFlow-APX (`ApxTypedCodec`, `ApxAuthoritativeGraph`, `ApxTransactionManager`, `ApxTupleSink`, `MicroExactMatcher` k=0).
- **Status of Phase P2:** Targeted closure remediation for findings P2-F01 through P2-F08 complete. All 8 canonical foundation cases, 35 foundation regression tests, and 499 repository-wide tests PASS. Exception-safe `ApxTypedCodec` with zero-allocation rollback, RAII `TransactionScope` eliminating container copy-assignments, reentrancy protection, strict JSON parsing and Unicode escape support, genuine `scripts.dstar_oracle` M0 Oracle integration with strict event_type derivation, step-by-step differential Python reference state model comparison, and D-R guard verified. Decision R2 and validation report updated. Pinned upstream `2ea4f4c...` remains 100% pristine.
- **Next action:** Review Draft PR #31, merge into `QuangThang`, and prepare Phase P3 (M1/M2 Native Engines). Do not implement production P3 algorithms or P4 approximation thresholds until approved. Model C artifacts remain historical.
