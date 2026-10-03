#!/usr/bin/env bash
# The release's quick checks (ruff, vulture, mypy), at the versions release.yml pins.
# With a commit, checks that commit's files, not the working tree, so other sessions'
# uncommitted edits in a shared checkout don't change the answer. Takes a few seconds.
#   scripts/check.sh            # the working tree
#   scripts/check.sh <commit>   # exactly what that commit holds (the pre-push hook)
set -euo pipefail

root=$(git rev-parse --show-toplevel)
dir=$root
if [ $# -gt 0 ]; then
    dir=$(mktemp -d)
    trap 'rm -rf "$dir"' EXIT
    git -C "$root" archive "$1" | tar -x -C "$dir"
fi
cd "$dir"

status=0
uvx ruff==0.16.9 check --quiet cps scripts tests || status=1
uvx vulture==2.16 || status=1
uvx mypy==2.3.1 --no-error-summary || status=1
exit $status
