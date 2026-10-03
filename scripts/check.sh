#!/usr/bin/env bash
# The release's quick checks at the versions it uses; release.yml calls this too, so the
# pins live here only. They run side by side and take a few seconds.
#   scripts/check.sh [--commit <sha>] [ruff|vulture|mypy ...]   (default: all three)
# --commit checks that commit's files, not the working tree, so other sessions'
# uncommitted edits in a shared checkout don't change the answer (the pre-push hook).
set -uo pipefail

root=$(git rev-parse --show-toplevel)
dir=$root
if [ "${1:-}" = --commit ]; then
    dir=$(mktemp -d)
    trap 'rm -rf "$dir"' EXIT
    git -C "$root" archive "$2" | tar -x -C "$dir"
    shift 2
    # Caches from the checkout keep a temp tree's run warm (mypy checks file contents)
    export MYPY_CACHE_DIR=$root/.mypy_cache RUFF_CACHE_DIR=$root/.ruff_cache
fi
cd "$dir" || exit 1

run() {
    case $1 in
        ruff) uvx ruff==0.16.9 check --quiet cps scripts tests ;;
        vulture) uvx vulture==2.16 ;;
        mypy) uvx mypy==2.3.1 --no-error-summary ;;
        *) echo "check.sh: unknown check $1" >&2; return 2 ;;
    esac
}

checks=("$@")
[ ${#checks[@]} -gt 0 ] || checks=(ruff vulture mypy)
pids=()
for check in "${checks[@]}"; do run "$check" & pids+=($!); done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
exit $status
