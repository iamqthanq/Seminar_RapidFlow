#!/usr/bin/env bash
# Open or show a Draft PR into QuangThang for an already-pushed feature branch.
# Usage: bash scripts/open_issue_pr.sh 4  (GitHub Issue number)
set -euo pipefail
cd "$(dirname "$0")/.."
repo="iamqthanq/Seminar_RapidFlow"
issue="${1:-}"
branch="$(git symbolic-ref --quiet --short HEAD || true)"
url="$(git remote get-url --push origin)"
if [[ ! "$issue" =~ ^[1-9][0-9]*$ ]]; then
  echo "Usage: bash scripts/open_issue_pr.sh <issue-number>" >&2
  exit 1
fi
if [[ ! "$branch" =~ ^feature/rf[0-9][0-9]-[a-z0-9][a-z0-9-]*$ ]]; then
  echo "BLOCKED: only feature/rfNN-* branches may open Issue PRs" >&2
  exit 1
fi
if [[ "$url" != "https://github.com/$repo" && "$url" != "https://github.com/$repo.git" ]]; then
  echo "BLOCKED: wrong repo $url" >&2
  exit 1
fi
issue_title="$(gh issue view "$issue" -R "$repo" --json title --jq '.title')"
existing="$(gh pr list -R "$repo" --state open --head "$branch" --base QuangThang --json url --jq '.[0].url // ""')"
if [[ -n "$existing" ]]; then
  echo "Existing PR: $existing"
  exit 0
fi
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<EOF
## Issue
References #$issue — $issue_title

## Scope
- Work branch: \`$branch\`
- Target branch: \`QuangThang\` (**never main**)
- What changed, why, and files affected: FILL IN BEFORE REVIEW

## Verification evidence
- Code/source SHA and environment: FILL IN
- Commands and input dataset/query/update hashes: FILL IN
- Full attempted test matrix; pass/fail/timeout; raw log references: FILL IN
- Counts vs actual embedding enumeration semantics: FILL IN
- Unexpected results and known limitations: FILL IN

## Reviewer gate
Draft until ChatGPT + external critique and manager approval.
**Do not merge this PR, and do not open any PR to main.**
EOF
gh pr create -R "$repo" --draft --base QuangThang --head "$branch" --title "$issue_title" --body-file "$tmp"
