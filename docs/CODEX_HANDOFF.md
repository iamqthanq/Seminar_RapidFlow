# Codex/Gemini handoff rules

- Work in `Seminar_RapidFlow`, on local branch `QuangThang` ONLY; push exclusively to `iamqthanq/Seminar_RapidFlow:QuangThang`, using `bash scripts/push_quangthang.sh`. Read `AGENTS.md` and `GEMINI.md`. Never push/commit/merge to main.
- Do NOT implement UACR or other proposed optimization unless explicitly authorized.
- First inventory existing source and dataset. Never silently clone/download duplicate copies.
- Do NOT change source semantics, output mode, graph labels, query/update ordering.
- Keep binary outputs and large raw datasets outside version control.
- Before benchmark: provide evidence of independent correctness and exact invocation.
- For every PR: original commit, git diff, commands, environment, dataset/query/stream hashes, full result files, limitations.
- Classify claims as PAPER / BUILD / SMOKE / CORRECT / BENCHMARK; do not conflate.
- Changes to matching logic require an independent oracle comparison and regression tests.
- Never fabricate data or infer a performance gain from mismatched output formats.
- Stop for review after each stage; push solely to `QuangThang` under the active Issue. No PR creation or merge without explicit user authorization. A PR from `QuangThang` to `main` must remain draft and unmerged until the user approves.
