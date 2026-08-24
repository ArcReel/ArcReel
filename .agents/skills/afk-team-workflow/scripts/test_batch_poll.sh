#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

git -C "$TMP_ROOT" init -q

gh() {
  if [[ "$1 $2" == "repo view" ]]; then
    echo "example/arc-reel"
    return
  fi

  if [[ "$1" == "api" && "$2" == "repos/example/arc-reel/issues/99/sub_issues" ]]; then
    [[ " $* " == *" --paginate "* ]]
    [[ " $* " == *" --slurp "* ]]
    jq -nc '[
      [
        {number: 1, title: "first", state: "open", state_reason: null, labels: [], assignees: [], body: "Blocked by: #9"},
        {number: 2, title: "native", state: "open", state_reason: null, labels: [], assignees: [], body: "Blocked by: #99"}
      ],
      [
        {number: 3, title: "fallback", state: "open", state_reason: null, labels: [], assignees: [], body: "Blocked by: #9"},
        {number: 4, title: "legacy", state: "open", state_reason: null, labels: [], assignees: [], body: "## Blocked by\n#1"},
        {number: 5, title: "none", state: "open", state_reason: null, labels: [], assignees: [], body: "Blocked by: None — related context #41"},
        {number: 6, title: "none-over-legacy", state: "open", state_reason: null, labels: [], assignees: [], body: "Blocked by: None\n## Blocked by\n#1"}
      ]
    ]'
    return
  fi

  if [[ "$1" == "api" && "$2" == repos/example/arc-reel/issues/*/dependencies/blocked_by ]]; then
    [[ " $* " == *" --paginate "* ]]
    [[ " $* " == *" --slurp "* ]]
    case "$2" in
      */issues/2/*) jq -nc '[[{number: 1}], [{number: 9}]]' ;;
      */issues/3/*|*/issues/4/*|*/issues/5/*|*/issues/6/*)
        echo 'gh: Not Found (HTTP 404)' >&2
        return 1
        ;;
      *)
        if [[ "${NATIVE_ERROR:-0}" == "1" ]]; then
          echo 'gh: service unavailable (HTTP 500)' >&2
          return 1
        fi
        jq -nc '[[]]'
        ;;
    esac
    return
  fi

  if [[ "$1" == "api" && "$2" == "repos/example/arc-reel/issues/9" ]]; then
    jq -nc '{number: 9, state: "closed", state_reason: "completed"}'
    return
  fi

  echo "unexpected gh call: $*" >&2
  return 1
}
export -f gh

OUTPUT=$(bash "$SCRIPT_DIR/batch-poll.sh" --repo-root "$TMP_ROOT" --spec 99)
jq -e '
  (.issues | map({number, blocked_by, blockers_completed})) == [
    {number: 1, blocked_by: [], blockers_completed: true},
    {number: 2, blocked_by: [1, 9], blockers_completed: false},
    {number: 3, blocked_by: [9], blockers_completed: true},
    {number: 4, blocked_by: [1], blockers_completed: false},
    {number: 5, blocked_by: [], blockers_completed: true},
    {number: 6, blocked_by: [], blockers_completed: true}
  ]
  and .initial_frontier == [1, 3, 5, 6]
' <<<"$OUTPUT" >/dev/null

if NATIVE_ERROR=1 bash "$SCRIPT_DIR/batch-poll.sh" --repo-root "$TMP_ROOT" --spec 99 \
  >"$TMP_ROOT/error.out" 2>"$TMP_ROOT/error.err"; then
  echo "FAIL: native dependency service error was silently downgraded" >&2
  exit 1
fi
grep -q 'native dependencies fetch failed for #1' "$TMP_ROOT/error.err"

echo "PASS: AFK batch poll preserves paginated issues and canonical dependencies"
