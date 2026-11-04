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
