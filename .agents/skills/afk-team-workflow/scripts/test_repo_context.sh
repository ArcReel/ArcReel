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

if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" test-batch membership
) >"$TMP_ROOT/membership.out" 2>"$TMP_ROOT/membership.err"; then
  echo "FAIL: membership without an issue was accepted" >&2
  exit 1
fi
grep -q 'membership requires --issue N' "$TMP_ROOT/membership.err"

(
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" test-batch membership --issue 1777 \
    --detail cleanup
)
jq -e 'select(.kind == "membership") | .issue == 1777' "$LEDGER" >/dev/null

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

mkdir -p "$TMP_ROOT/.afk/reserved-batch"
if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" reserved-batch decision --scope-issues 1776
) >"$TMP_ROOT/reserved.out" 2>"$TMP_ROOT/reserved.err"; then
  echo "FAIL: reserved batch-id was reused" >&2
  exit 1
fi
grep -q 'batch-id path is already reserved' "$TMP_ROOT/reserved.err"

if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" spec1776-20260811-010101-abcdef decision --scope-spec 1776
) >"$TMP_ROOT/spec-id.out" 2>"$TMP_ROOT/spec-id.err"; then
  echo "FAIL: malformed Spec batch-id was accepted" >&2
  exit 1
fi
grep -q 'Spec batch-id must match spec-1776-' "$TMP_ROOT/spec-id.err"

if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT" merge-kind merge --scope-issues 1776
) >"$TMP_ROOT/merge.out" 2>"$TMP_ROOT/merge.err"; then
  echo "FAIL: derived merge fact was accepted by the thin ledger" >&2
  exit 1
fi
grep -q 'unknown kind: merge' "$TMP_ROOT/merge.err"

if (
  cd "$SCRIPT_DIR"
  bash ledger.sh --repo-root "$TMP_ROOT/missing" test-batch closed
) >"$TMP_ROOT/missing.out" 2>"$TMP_ROOT/missing.err"; then
  echo "FAIL: invalid --repo-root unexpectedly succeeded" >&2
  exit 1
fi
grep -q '^LEDGER_ERROR: not a Git worktree:' "$TMP_ROOT/missing.err"

echo "PASS: AFK ledger repository context and terminal batch-id"
