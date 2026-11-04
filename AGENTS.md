# Repository-bound agent policy (mandatory)

Repository: **iamqthanq/Seminar_RapidFlow** (only).
Approved *remote write* destination: **https://github.com/iamqthanq/Seminar_RapidFlow/tree/QuangThang**.
Workspace: **/home/iamqthanq/Desktop/Seminar_RapidFlow**.

## Non-negotiable rules
1. Work on local branch `QuangThang` only. Push **only** `refs/heads/QuangThang` to `refs/heads/QuangThang` at `origin`. Do not push to `main`, another branch, another fork, or another repository.
2. Explicit authorized push command: `bash scripts/push_quangthang.sh` (equivalent: `git push origin refs/heads/QuangThang:refs/heads/QuangThang`).
3. Never run `git push --all`, `git push --mirror`, `git push --force`, `git push --force-with-lease`, `git push --tags`, `git push origin main`, `git push origin HEAD:main`, or a `gh api`/GitHub MCP write action that changes another branch.
4. Never bypass or disable `.githooks/pre-push` or change `core.hooksPath`. No `--no-verify`, `-c core.hooksPath=...`, alternate repository clone for remote writes, or direct API push.
5. Never create, close, or merge PRs, or merge `QuangThang` into `main`, unless the user explicitly authorizes the exact operation. The user alone decides when to merge to `main`. If PR review is requested, propose a **draft** PR `QuangThang -> main` and require user permission before creating it. **Do not merge**.
6. Read Issue #1 (research roadmap) and Issue #2 (RF-01). Execute only the active Issue. Report missing dependencies and correctness uncertainties; no silent benchmark/algorithm changes.
7. Source RapidFlow under `baselines/rapidflow/source/` is a pristine third-party checkout, not the outer Git repository. Do not commit its huge data or derived binaries. NetFlow raw archives and `results/raw/` are Git-ignored.
8. Keep all result claims auditable with input hashes, source commit, flags, stdout/stderr, runtime, RSS, and failures. Do not confuse count-only and full enumeration.
9. When a command would violate any constraint, STOP, state the conflict, and ask the user to authorize an alternative. Never reinterpret these limits implicitly.

## Start (read only)
```bash
cd /home/iamqthanq/Desktop/Seminar_RapidFlow
git status --short --branch
git remote -v
gh issue view 1 -R iamqthanq/Seminar_RapidFlow
gh issue view 2 -R iamqthanq/Seminar_RapidFlow
```

## Review workflow under the one-branch restriction
After an Issue's commits are pushed to `QuangThang`, provide commit SHA(s), GitHub compare/permalink and evidence. ChatGPT can review the commit/diff and issue without a new PR. A PR that *targets* `QuangThang` would need a separate remote head branch, which is forbidden by the current rule. Use a draft PR `QuangThang -> main` **only if the user authorizes**; it is never self-merged.
