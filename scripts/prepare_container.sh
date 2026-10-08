#!/usr/bin/env bash
set -euo pipefail
# This script may only reset the disposable container's pre-change checkout.
test -f /.dockerenv
test -d /app/.git
expected_head="${1:?expected pre-change commit required}"
[[ "$expected_head" =~ ^[0-9a-f]{40}$ ]]
rm -rf /testbed

sanitize_repo() {
  local task_repo="$1" task_head="$2"
  cd "$task_repo"
  git checkout --detach --force "$task_head" >/dev/null
  git reset --hard "$task_head" >/dev/null
  git clean -fd >/dev/null
  git submodule foreach --quiet 'bash /opt/ir/prepare_container.sh "$sha1" "$PWD" submodule'
  while IFS= read -r remote; do git remote remove "$remote"; done < <(git remote)
  git for-each-ref --format='delete %(refname)' | git update-ref --stdin
  git reflog expire --expire=now --all
  git gc --prune=now >/dev/null 2>&1
  test "$(git rev-parse HEAD)" = "$task_head"
  test -z "$(git status --porcelain --untracked-files=all)"
  test -z "$(git for-each-ref)"
  test -z "$(git remote)"
  test -z "$(git fsck --unreachable --no-reflogs 2>/dev/null)"
}

if [ "${3:-}" = submodule ]; then
  sanitize_repo "${2:?submodule path required}" "$expected_head"
else
  sanitize_repo /app "$expected_head"
fi
