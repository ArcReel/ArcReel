#!/usr/bin/env bash
#   - quota_alert_pr1115_no_stack.txt / quota_alert_pr1115_review_stack.txt: real
#     CodeRabbit rate-limit banners captured from the source PR's walkthrough
#     comment edit history (GraphQL userContentEdits — the live GitHub body has
#     since been overwritten by CodeRabbit's own later edits, so this is the only
#     way to recover the exact bytes). *_review_stack carries the change-stack
#     link banner that pushes the alert phrase past the old 500-char scan window —
#     this is the exact silent-miss regression this test guards against.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SH="$SCRIPT_DIR/poll.sh"
TESTDATA="$SCRIPT_DIR/testdata"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found on PATH" >&2
  exit 3
fi

QUOTA_DEF=$(awk '/^[[:space:]]*def quota_alerts:/{flag=1} flag{print; if (/\];[[:space:]]*$/) exit}' "$POLL_SH")
if [[ -z "$QUOTA_DEF" ]]; then
  echo "could not extract quota_alerts def from $POLL_SH" >&2
  exit 4
fi
WT_HEAD_DEF=$(awk '/^[[:space:]]*def cr_walkthrough_head:/{flag=1} flag{print; if (/;[[:space:]]*$/ && !/^[[:space:]]*def /) exit}' "$POLL_SH")
if [[ -z "$WT_HEAD_DEF" ]]; then
  echo "could not extract cr_walkthrough_head def from $POLL_SH" >&2
  exit 4
fi
WT_DEF=$(awk '/^[[:space:]]*def cr_walkthrough_rest:/{flag=1} flag{print; if (/;[[:space:]]*$/ && !/^[[:space:]]*def /) exit}' "$POLL_SH")
if [[ -z "$WT_DEF" ]]; then
  echo "could not extract cr_walkthrough_rest def from $POLL_SH" >&2
  exit 4
fi

LAST_PUSH="2026-07-13T00:30:00Z"
WT_UPDATED_AT="2026-07-13T01:00:00Z"

CASES=(
  "quota_alert_pr1115_no_stack.txt:coderabbitai[bot]:true:true"
  "quota_alert_pr1115_review_stack.txt:coderabbitai[bot]:true:true"
  "no_actionable_pr1115.txt:coderabbitai[bot]:false:false"
  "synthetic_non_alert_mentions_limit.txt:coderabbitai[bot]:false:false"
)

fail=0
for tc in "${CASES[@]}"; do
  IFS=":" read -r file login expect expect_rl <<<"$tc"
  body_file="$TESTDATA/$file"
  if [[ ! -f "$body_file" ]]; then
    echo "FAIL $file: fixture missing at $body_file" >&2
    fail=1
    continue
  fi

  got=$(jq -n -L "$SCRIPT_DIR" \
    --rawfile body "$body_file" \
    --arg login "$login" \
    "
    include \"signals\";
    [{user: {login: \$login}, created_at: \"2026-07-13T00:00:00Z\", body: \$body}] as \$sub_a
    | $QUOTA_DEF
      (quota_alerts | length) > 0
    ")

  got_rl=$(jq -n -L "$SCRIPT_DIR" \
    --rawfile body "$body_file" \
    "
    include \"signals\";
    \$body | cr_rate_limited_body
    ")

  if [[ "$expect_rl" == "true" ]]; then expect_wt="true:false"; else expect_wt="false:true"; fi
  got_wt=$(jq -rn -L "$SCRIPT_DIR" \
    --rawfile body "$body_file" \
    --arg login "$login" \
    --arg last_push "$LAST_PUSH" \
    --arg updated_at "$WT_UPDATED_AT" \
    "
    include \"signals\";
    [{id: 1, user: {login: \$login}, created_at: \"2026-07-13T00:00:00Z\",
      updated_at: \$updated_at, body: \$body}] as \$sub_a
    | {reviews: [], headRefOid: \"test-head\"} as \$main
    | {} as \$review_commit_by_id
    | def codex_commit_is_current_head: true;
      $WT_HEAD_DEF
      $WT_DEF
      (cr_walkthrough_rest | \"\\(.is_rate_limited):\\(.reviewed_current_head)\")
    ")

  if [[ "$got" == "$expect" && "$got_rl" == "$expect_rl" && "$got_wt" == "$expect_wt" ]]; then
    echo "PASS $file (quota_alerts triggered=$got, is_rate_limited=$got_rl, walkthrough=$got_wt)"
  else
    echo "FAIL $file: expected triggered=$expect/is_rate_limited=$expect_rl/walkthrough=$expect_wt, got=$got/$got_rl/$got_wt" >&2
    fail=1
  fi
done

exit "$fail"
