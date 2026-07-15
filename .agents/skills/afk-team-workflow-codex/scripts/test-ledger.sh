#!/usr/bin/env bash
# Behavioral parity against the Claude ledger, excluding runtime and timestamp values.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../../.." && pwd)
ORIGINAL="$REPO_ROOT/.agents/skills/afk-team-workflow/scripts/ledger.sh"
CODEX="$SCRIPT_DIR/ledger.sh"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/afk-codex-ledger-test.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

ORIG_DIR="$TMP_ROOT/original"
CODEX_DIR="$TMP_ROOT/codex"
mkdir -p "$ORIG_DIR" "$CODEX_DIR"

append_pair() {
  (cd "$ORIG_DIR" && bash "$ORIGINAL" "$@" >/dev/null 2>&1)
  (cd "$CODEX_DIR" && bash "$CODEX" "$@" >/dev/null 2>&1)
}

append_pair batch-parity decision --scope-issues '3, 1,3,2' --detail $'plan "quoted"\nsecond line'
append_pair batch-parity authorization --detail 'merge + tail authorization'
append_pair batch-parity fault --issue 3 --pr 30 --detail 'quota'
append_pair batch-parity gap --issue 1 --detail 'spec gap'
append_pair batch-parity shelve --issue 2 --pr 20 --detail 'business choice'
append_pair batch-parity merge --issue 3 --pr 30 --detail 'squash'
append_pair batch-parity retrospective --issue 3 --pr 30 --detail 'none'
append_pair batch-parity decision --scope-spec 99 --detail 'scope expansion marker'
append_pair batch-parity closed --detail 'done'

ORIG_LEDGER="$ORIG_DIR/.afk/batch-parity.jsonl"
CODEX_LEDGER="$CODEX_DIR/.afk/batch-parity.jsonl"

jq -s 'map(.ts = "<TS>")' "$ORIG_LEDGER" > "$TMP_ROOT/original.normalized.json"
jq -s 'map(del(.runtime) | .ts = "<TS>")' "$CODEX_LEDGER" > "$TMP_ROOT/codex.normalized.json"
cmp "$TMP_ROOT/original.normalized.json" "$TMP_ROOT/codex.normalized.json"

jq -e -s '
  length == 9
  and all(.[]; .runtime == "codex")
  and all(.[]; .ts | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
  and .[0].scope == {issues:[3,1,2]}
  and .[7].scope == {spec:99}
  and .[8].kind == "closed"
' "$CODEX_LEDGER" >/dev/null

expect_pair_failure() {
  local label="$1"
  shift
  local original_dir="$TMP_ROOT/fail-$label-original"
  local codex_dir="$TMP_ROOT/fail-$label-codex"
  local original_status codex_status
  mkdir -p "$original_dir" "$codex_dir"

  set +e
  (cd "$original_dir" && bash "$ORIGINAL" "$@" >out 2>err)
  original_status=$?
  (cd "$codex_dir" && bash "$CODEX" "$@" >out 2>err)
  codex_status=$?
  set -e

  [[ "$original_status" -ne 0 && "$codex_status" -ne 0 ]]
  cmp "$original_dir/err" "$codex_dir/err"
}

expect_pair_failure missing-args
expect_pair_failure bad-batch 'bad/batch' decision --scope-spec 1
expect_pair_failure bad-kind batch nope --scope-spec 1
expect_pair_failure bad-issue batch decision --scope-spec 1 --issue x
expect_pair_failure bad-pr batch decision --scope-spec 1 --pr x
expect_pair_failure first-no-scope batch decision
expect_pair_failure both-scopes batch decision --scope-spec 1 --scope-issues 1,2
expect_pair_failure bad-scope-spec batch decision --scope-spec x
expect_pair_failure bad-scope-issues batch decision --scope-issues 1,x
expect_pair_failure unknown-arg batch decision --scope-spec 1 --wat

echo "ledger tests passed"
