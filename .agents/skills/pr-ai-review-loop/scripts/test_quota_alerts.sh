#!/usr/bin/env bash
# test_quota_alerts.sh — regression test for poll.sh's quota_alerts detection.
#
# USAGE
#   bash test_quota_alerts.sh
#
# Extracts the `quota_alerts` jq def verbatim out of poll.sh (so this test always
# exercises the function actually shipped, not a copy that can drift) and runs it
# against fixture comment bodies in testdata/:
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

# Pull the def verbatim: from `def quota_alerts:` through the line ending `];`
# that closes it.
QUOTA_DEF=$(awk '/^[[:space:]]*def quota_alerts:/{flag=1} flag{print; if (/\];[[:space:]]*$/) exit}' "$POLL_SH")
if [[ -z "$QUOTA_DEF" ]]; then
  echo "could not extract quota_alerts def from $POLL_SH" >&2
  exit 4
fi

# name : user login : expect (true/false)
CASES=(
  "quota_alert_pr1115_no_stack.txt:coderabbitai[bot]:true"
  "quota_alert_pr1115_review_stack.txt:coderabbitai[bot]:true"
  "no_actionable_pr1115.txt:coderabbitai[bot]:false"
  "synthetic_non_alert_mentions_limit.txt:coderabbitai[bot]:false"
)

fail=0
for tc in "${CASES[@]}"; do
  IFS=":" read -r file login expect <<<"$tc"
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
    | $QUOTA_DEF
      (quota_alerts | length) > 0
    ")

  if [[ "$got" == "$expect" ]]; then
    echo "PASS $file (quota_alerts triggered=$got)"
  else
    echo "FAIL $file: expected triggered=$expect, got=$got" >&2
    fail=1
  fi
done

exit "$fail"
