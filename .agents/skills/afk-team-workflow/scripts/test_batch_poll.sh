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
      [{number: 1, title: "first", state: "open", state_reason: null, labels: [], assignees: [], body: "## Blocked by\nNone"}],
      [{number: 2, title: "second", state: "open", state_reason: null, labels: [], assignees: [], body: "## Blocked by\nNone"}]
    ]'
    return
  fi

  echo "unexpected gh call: $*" >&2
  return 1
}
export -f gh

OUTPUT=$(bash "$SCRIPT_DIR/batch-poll.sh" --repo-root "$TMP_ROOT" --spec 99)
jq -e '
  (.issues | map(.number)) == [1, 2]
  and .initial_frontier == [1, 2]
' <<<"$OUTPUT" >/dev/null

echo "PASS: AFK batch poll preserves paginated Spec sub-issues"
