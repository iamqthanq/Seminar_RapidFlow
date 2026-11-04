# Branch / PR policy v2 (approved 2026-11-04)

## One Issue = one feature branch = one Draft PR
```text
main                  # stable, merge ONLY when user decides
└── QuangThang         # research integration, user approves merges
    ├── feature/rf02-correctness --> Draft PR into QuangThang
    ├── feature/rf03-benchmark   --> Draft PR into QuangThang
    └── feature/rf04-profiling   --> Draft PR into QuangThang
```
- **RF-01 historical exception:** Issue #2 was pushed directly as commit `d358082` on `QuangThang` before policy v2. It remains auditable via commit/compare/Issue; do NOT rewrite or reset history to fabricate a PR.
- Only the actively approved Issue can be implemented; next gate based on verified evidence, reviewer feedback, independent AI critique and manager's decision.
- An Issue gets **one PR**. Fixes are added as commits to the same feature branch; PR updates automatically.
- Base branch of Issue PR MUST be `QuangThang`, never `main`. Agent can open Draft PR but NEVER merge/close without manager authorization.
- Final integration PR `QuangThang -> main` is reserved for the **user's explicit decision**.

## Machine-local controls
- Repository remote: `origin = https://github.com/iamqthanq/Seminar_RapidFlow`; no other push remote.
- `git config core.hooksPath = .githooks`; `.githooks/pre-push` rejects unauthorized remote, branch, deletion and non-fast-forward updates.
- `git config push.default = nothing`; no implicit push refspec; script `scripts/push_issue_branch.sh` validates current branch and remote and pushes with an explicit same-name feature refspec.
- `scripts/open_issue_pr.sh <ISSUE_NUMBER>` opens Draft PR into QuangThang or prints existing open PR; never targets main.
- One-time code migration exception in the pre-push hook is tied to original `QuangThang` SHA `d358082` and strictly allowed governance paths; it expires as soon as policy v2 commits to `QuangThang`.

## GitHub-side branch protection
- Main and QuangThang should **require pull requests** for updates, disallow force pushes and branch deletion. Configuration/status should be audited separately.
- **Important limitation:** Gemini and the user currently share the SAME `iamqthanq` administrator GitHub credential. Neither instructions, local hooks nor branch protection can establish that a command came from the human, rather than the agent holding the same credential. Truly enforcing user-only merges requires separate least-privilege agent credentials and suitable repository rules/approvals.

## Review gates
1. Agent pushes `feature/rfNN-...` and creates linked Draft PR to QuangThang.
2. ChatGPT verifies diff, correctness, reproducibility evidence, time/memory/exit statistics and limitations.
3. Manager sends PR/evidence/review to an independent AI for critique.
4. FAILED/INCONCLUSIVE -> fix and rerun on SAME PR; PASS -> manager may merge to QuangThang.
5. Only the manager can authorize a PR into main and decide on merge.

**Do not change this policy without user's approval.**
