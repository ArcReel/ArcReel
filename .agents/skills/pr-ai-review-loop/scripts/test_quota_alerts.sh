#!/usr/bin/env bash
# test_quota_alerts.sh — regression test for poll.sh's quota_alerts detection and
# the CodeRabbit rate-limit banner match (cr_rate_limited_body, which also backs
# walkthrough.is_rate_limited / reviewed_current_head suppression).
#
# USAGE
#   bash test_quota_alerts.sh
#
# Extracts the `cr_rate_limited_body`, `quota_alerts` and `cr_walkthrough_rest`
# jq defs verbatim out of poll.sh (so this test always exercises the functions
# actually shipped, not a copy that can drift) and runs them against fixture
# comment bodies in testdata/. `cr_walkthrough_rest` is exercised because the
# banner match alone is not the guarantee that matters: what the loop reads is
# the walkthrough projection, and a rate-limited body must surface there as
# is_rate_limited=true AND reviewed_current_head=false even when updated_at is
# newer than the last push. The fixtures:
#   - quota_alert_pr1115_no_stack.txt / quota_alert_pr1115_review_stack.txt: real
#     CodeRabbit rate-limit banners captured from the source PR's walkthrough
#     comment edit history (GraphQL userContentEdits — the live GitHub body has
#     since been overwritten by CodeRabbit's own later edits, so this is the only
#     way to recover the exact bytes). *_review_stack carries the change-stack
#     link banner that pushes the alert phrase past the old 500-char scan window —
#     this is the exact silent-miss regression this test guards against.
#   - no_actionable_pr1115.txt: the same PR's final (non-alert) walkthrough body,
#     also real, sharing the same change-stack banner prefix — proves the fix
#     doesn't start matching on the banner alone.
#   - synthetic_non_alert_mentions_limit.txt: constructed (not captured from a
#     real PR) edge case where the body merely discusses rate limiting as a
#     topic, to check bare keyword mentions still don't trigger.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SH="$SCRIPT_DIR/poll.sh"
TESTDATA="$SCRIPT_DIR/testdata"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found on PATH" >&2
  exit 3
fi

# Pull each def verbatim: cr_rate_limited_body ends on its single expression line
# (`;`), quota_alerts on the line ending `];` that closes it.
RL_DEF=$(awk '/^[[:space:]]*def cr_rate_limited_body:/{flag=1} flag{print; if (/;[[:space:]]*$/ && !/^[[:space:]]*def /) exit}' "$POLL_SH")
if [[ -z "$RL_DEF" ]]; then
  echo "could not extract cr_rate_limited_body def from $POLL_SH" >&2
  exit 4
fi
QUOTA_DEF=$(awk '/^[[:space:]]*def quota_alerts:/{flag=1} flag{print; if (/\];[[:space:]]*$/) exit}' "$POLL_SH")
if [[ -z "$QUOTA_DEF" ]]; then
  echo "could not extract quota_alerts def from $POLL_SH" >&2
  exit 4
fi
# cr_walkthrough_rest closes on its `end;` line — the same terminator rule as above.
WT_DEF=$(awk '/^[[:space:]]*def cr_walkthrough_rest:/{flag=1} flag{print; if (/;[[:space:]]*$/ && !/^[[:space:]]*def /) exit}' "$POLL_SH")
if [[ -z "$WT_DEF" ]]; then
  echo "could not extract cr_walkthrough_rest def from $POLL_SH" >&2
  exit 4
fi

# The walkthrough fixture is stamped updated_at > LAST_PUSH on purpose: without the
# rate-limit suppression every case would read reviewed_current_head=true, so the
# expected false on the banner fixtures can only come from the suppression itself.
LAST_PUSH="2026-07-13T00:30:00Z"
WT_UPDATED_AT="2026-07-13T01:00:00Z"

# name : user login : expect quota_alerts triggered : expect cr_rate_limited_body
# (the two differ on *_no_stack-style bodies only if CodeRabbit ever drops its banner
# marker; today every marker-bearing body triggers both)
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

  got=$(jq -n \
    --rawfile body "$body_file" \
    --arg login "$login" \
    "
    [{user: {login: \$login}, created_at: \"2026-07-13T00:00:00Z\", body: \$body}] as \$sub_a
    | $RL_DEF
      $QUOTA_DEF
      (quota_alerts | length) > 0
    ")

  got_rl=$(jq -n \
    --rawfile body "$body_file" \
    "
    $RL_DEF
    \$body | cr_rate_limited_body
    ")

  # Rate-limited bodies must suppress reviewed_current_head; non-alert ones must not.
  if [[ "$expect_rl" == "true" ]]; then expect_wt="true:false"; else expect_wt="false:true"; fi
  got_wt=$(jq -rn \
    --rawfile body "$body_file" \
    --arg login "$login" \
    --arg last_push "$LAST_PUSH" \
    --arg updated_at "$WT_UPDATED_AT" \
    "
    [{id: 1, user: {login: \$login}, created_at: \"2026-07-13T00:00:00Z\",
      updated_at: \$updated_at, body: \$body}] as \$sub_a
    | $RL_DEF
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
