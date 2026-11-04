# Repository-bound agent instructions — Branch policy v2

## Allowed repository and branch model

- Only repository: **https://github.com/iamqthanq/Seminar_RapidFlow**.
- Protected integration branch: **QuangThang**.
- Protected release branch: **main**. The USER ALONE decides any merge into main.
- For each approved Issue, agent creates **one work branch** named `feature/rfNN-topic` FROM the updated `origin/QuangThang`. Example: `feature/rf02-correctness`.
- Agent may commit and push **only its active feature branch to the same-named remote feature branch** in this repository.
- **The only permitted push command:** `bash scripts/push_issue_branch.sh`.
- After first push, open **one Draft PR into QuangThang** with `bash scripts/open_issue_pr.sh <ISSUE_NUMBER>`. Subsequent pushes update that SAME PR. Link relevant Issue and provide full logs/evidence. **Do not create a separate PR for every commit.**
- **Do NOT merge any PR**, do not push directly into `QuangThang` or `main`, and do not create a PR targeting `main`. Only the user approves merging to QuangThang and eventually to main.

## Absolute safety constraints
1. No push outside this repository, nor to `main`, `QuangThang`, other features, tags or forks.
2. No force push, deleting remote branches, `--all`, `--mirror`, or modifying remote URLs / hook configuration.
3. Never bypass `.githooks/pre-push` (`--no-verify`, `-c core.hooksPath=...`) or bypass the restriction through GitHub API/MCP, alternate checkout, or account credentials.
4. Respect GitHub Issue scope. One active implementation Issue at a time; the user approves moving to next gate.
5. Keep `baselines/rapidflow/source/` pristine at pinned `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`. Third-party source, NetFlow raw data, large logs and binaries remain out of Git.
6. Include all attempted cases, exact commands, input SHA-256, software/hardware/flags, exit/time/RSS, raw evidence and independent correctness where relevant. Never equate count-only with explicit embedding enumeration.
7. If instructions conflict, STOP and ask for user clarification; never relax limits by inference.

## Standard agent workflow
```bash
cd ~/Desktop/Seminar_RapidFlow
gh issue view 1 -R iamqthanq/Seminar_RapidFlow
# Read the current approved Issue, e.g. gh issue view 4 -R ...
git status --short --branch
git fetch origin QuangThang
git switch QuangThang
git pull --ff-only origin QuangThang
git switch -c feature/rf02-correctness
# Implement & test the explicitly approved Issue; commit with reproducibility evidence.
bash scripts/push_issue_branch.sh
bash scripts/open_issue_pr.sh 4
```

**Important:** RF-01 (Issue #2, commit `d358082`) is a *pre-v2 legacy exception*: it was already directly pushed to QuangThang before per-Issue PRs were adopted. Do not rewrite history to manufacture an RF-01 PR. Review RF-01 by commit evidence.

Further rules: `GEMINI.md`, `docs/BRANCH_POLICY.md` and `docs/CODEX_HANDOFF.md`.
