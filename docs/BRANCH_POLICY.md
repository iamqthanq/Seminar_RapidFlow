# Branch and remote safety policy

## Chosen model, 2026-11-04
The user explicitly requests **only remote pushes to** `iamqthanq/Seminar_RapidFlow:QuangThang`. `main` remains for user-approved integration only. This requirement overrides the earlier suggestion to push one feature branch per Issue.

- Local working branch: `QuangThang`.
- Remote: `origin = https://github.com/iamqthanq/Seminar_RapidFlow`.
- Authorized: `bash scripts/push_quangthang.sh` (explicit branch refspec).
- Prohibited: push to any other branch/repository; force push; automatic merge or PR creation; bypassing hook.
- Git `push.default=nothing`, `remote.origin.push=refs/heads/QuangThang:refs/heads/QuangThang`, and tracked `.githooks/pre-push` with `core.hooksPath=.githooks` help prevent accidents.
- Per-Issue changes are separate **commits** on `QuangThang`, with GitHub Issue comments pointing to commit SHAs, execution logs, and reviewer decisions.
- A PR from `QuangThang` targeting `main` can be opened as a **draft** only when authorized for review. It must never be merged until the user explicitly approves.

## Limits
Git hooks and text instructions are **local safeguards**, not absolute security controls. Someone using the same GitHub credential with repository admin rights can bypass Git hooks or use GitHub API / a different clone. Server-side branch protection/rulesets and **separate least-privilege credentials for an agent** are needed for stronger enforcement. This setup does not revoke permissions from the existing `gh` login.

## Debugging
- `git config --local --get core.hooksPath`
- `git config --local --get push.default`
- `git remote get-url --push origin`
- `git status --short --branch`
- `git ls-remote --heads origin QuangThang main`
