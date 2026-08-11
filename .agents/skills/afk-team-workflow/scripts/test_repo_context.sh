#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

git -C "$TMP_ROOT" init -q

(
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" test-batch decision \
    --scope-issues 1776 --detail repo-root-check
)

LEDGER="$TMP_ROOT/.afk/test-batch.jsonl"
[[ -s "$LEDGER" ]]
jq -e '.kind == "decision" and .scope.issues == [1776]' "$LEDGER" >/dev/null

(
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" test-batch closed
)

if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" test-batch decision --scope-issues 1776
) >"$TMP_ROOT/reuse.out" 2>"$TMP_ROOT/reuse.err"; then
  echo "FAIL: closed batch-id was reused" >&2
  exit 1
fi
grep -q 'batch-id is already closed' "$TMP_ROOT/reuse.err"

if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT/missing" test-batch closed
) >"$TMP_ROOT/missing.out" 2>"$TMP_ROOT/missing.err"; then
  echo "FAIL: invalid --repo-root unexpectedly succeeded" >&2
  exit 1
fi
grep -q '^LEDGER_ERROR: not a Git worktree:' "$TMP_ROOT/missing.err"

echo "PASS: AFK ledger repository context and terminal batch-id"
