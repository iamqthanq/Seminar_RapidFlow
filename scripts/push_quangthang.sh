#!/usr/bin/env bash
# Safe explicit push to the ONLY approved destination. No pull/merge/reset performed.
set -euo pipefail
cd "$(dirname "$0")/.."
expected="https://github.com/iamqthanq/Seminar_RapidFlow"
branch="$(git symbolic-ref -q --short HEAD || true)"
url="$(git remote get-url --push origin)"
if [[ "$branch" != "QuangThang" ]]; then
  echo "BLOCKED: current branch is '$branch'; only QuangThang is allowed" >&2
  exit 1
fi
if [[ "$url" != "$expected" && "$url" != "$expected.git" ]]; then
  echo "BLOCKED: origin push URL is not the approved repository: $url" >&2
  exit 1
fi
echo "Approved destination: $expected/tree/QuangThang"
git push origin refs/heads/QuangThang:refs/heads/QuangThang
