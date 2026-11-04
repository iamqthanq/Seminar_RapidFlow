#!/usr/bin/env bash
# Push ONLY the current feature/rfNN-* branch to the matching branch of
# iamqthanq/Seminar_RapidFlow. No automatic merge, rebase, reset or force.
set -euo pipefail
cd "$(dirname "$0")/.."
repo="iamqthanq/Seminar_RapidFlow"
url="$(git remote get-url --push origin)"
branch="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ ! "$branch" =~ ^feature/rf[0-9][0-9]-[a-z0-9][a-z0-9-]*$ ]]; then
  echo "BLOCKED: work on feature/rfNN-* only; current=$branch" >&2
  exit 1
fi
if [[ "$url" != "https://github.com/$repo" && "$url" != "https://github.com/$repo.git" ]]; then
  echo "BLOCKED: wrong repository: $url" >&2
  exit 1
fi
git merge-base --is-ancestor "origin/QuangThang" HEAD || {
  echo "BLOCKED: branch not based on local origin/QuangThang; fetch/review first" >&2
  exit 1
}
printf 'Approved: %s -> %s:%s\n' "$branch" "$repo" "$branch"
git push origin "refs/heads/$branch:refs/heads/$branch"
