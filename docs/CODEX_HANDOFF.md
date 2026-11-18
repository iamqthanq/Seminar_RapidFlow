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
- Current Active Direction: **Direction D (Issue #28 / D-INT-01 / P0–P1)**. Objective: Extend original RapidFlow C++ to support Approximate CSM under Edge Recall and S2 connectivity ($0 \le k < \lambda(Q)$).
- **Mandatory Native Integration:** Standalone Python or uncoupled C++ programs do not fulfill requirements. Final engine must genuinely integrate with RapidFlow native components (Graph CSR, Global Views, streaming pipeline), emit canonical ordered tuples, and demonstrate verified call paths.
- DSTAR Foundations (DSTAR-01..03) are complete historical validation assets. Pilot metric `budget_units` is an internal engine counter, not an invariant theoretical work metric; synthetic budget ratio ~1.52 does not prove 34% search work reduction; zero-event NetFlow probes ($|S| \le 8$) reflect tiny horizon coverage limits.
- Next action: complete P0–P1 (contract audit, source audit, probes A–F, Decision R1). Do not jump to native M1/M2 code or NLF/$\delta'$ before P2. Model C artifacts remain historical.
