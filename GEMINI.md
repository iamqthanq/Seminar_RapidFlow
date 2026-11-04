# Gemini CLI — Mandatory GitHub workflow v2

Obey `AGENTS.md` and `docs/BRANCH_POLICY.md`.

- Only `iamqthanq/Seminar_RapidFlow` repository.
- Create ONE `feature/rfNN-topic` branch per approved implementation Issue, forked from `QuangThang`.
- ONLY push from that branch to the identical branch via `bash scripts/push_issue_branch.sh`.
- Immediately after first push, create ONE **Draft PR** into `QuangThang` using `bash scripts/open_issue_pr.sh <ISSUE_NUMBER>`; future pushes update the PR automatically.
- Never push to `QuangThang`, `main`, fork, or any other repository. Never open a PR targeting `main`. Never merge PRs or bypass Git hook/API restrictions.
- User alone approves PR merge into `QuangThang` and any future merge into `main`.
- Read Issue #1 and the explicitly assigned Issue. DO NOT start RF-02 without user approval. RF-01 already landed on QuangThang under old policy: preserve history.

Source RapidFlow must remain pristine. Require independent correctness, logs, hashes and output-mode honesty.
