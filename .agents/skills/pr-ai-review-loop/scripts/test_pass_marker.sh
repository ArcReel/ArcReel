#!/usr/bin/env bash
# test_pass_marker.sh — regression test for poll.sh's has_pass_marker_body (Gemini review
# summary pass detection).
#
# USAGE
#   bash test_pass_marker.sh
#
# Extracts the `has_pass_marker_body` jq def verbatim out of poll.sh (so this test always
# exercises the function actually shipped, not a copy that can drift) and runs it against
# real Gemini review bodies captured from source PRs in testdata/:
#   - gemini_pass_additional_feedback_pr1249.txt: "I have no additional feedback to provide."
#     — an adjective sits between "no" and "feedback", which broke the old fixed-substring
#     match ("no feedback to provide"). This is one of the two silent-stuck regressions the
#     structural match guards against.
#   - gemini_pass_passive_provided_pr1250.txt: "...so no feedback is provided on reviewer
#     comments." — passive voice, also missed by the old substring. The other regression.
#   - gemini_pass_plain_pr1252.txt: "I have no feedback to provide." — already passed under
#     the old rule; proves the generalization doesn't regress the common phrasing.
#   - gemini_actionable_pr1244.txt: a real body describing a concrete reviewer suggestion —
#     the reverse case, must NOT be read as a pass.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SH="$SCRIPT_DIR/poll.sh"
TESTDATA="$SCRIPT_DIR/testdata"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found on PATH" >&2
  exit 3
fi

# Pull the def verbatim: from `def has_pass_marker_body:` through the first following line
# that ends in a semicolon (the def's closing line). Comment lines inside the def carry no
# trailing `;`, so they don't trip the exit early.
PASS_DEF=$(awk '/^[[:space:]]*def has_pass_marker_body:/{flag=1} flag{print; if (/;[[:space:]]*$/) exit}' "$POLL_SH")
if [[ -z "$PASS_DEF" ]]; then
  echo "could not extract has_pass_marker_body def from $POLL_SH" >&2
  exit 4
fi

# fixture : expect (true/false)
CASES=(
  "gemini_pass_additional_feedback_pr1249.txt:true"
  "gemini_pass_passive_provided_pr1250.txt:true"
  "gemini_pass_plain_pr1252.txt:true"
  "gemini_actionable_pr1244.txt:false"
)

fail=0
for tc in "${CASES[@]}"; do
  IFS=":" read -r file expect <<<"$tc"
  body_file="$TESTDATA/$file"
  if [[ ! -f "$body_file" ]]; then
    echo "FAIL $file: fixture missing at $body_file" >&2
    fail=1
    continue
  fi

  got=$(jq -n \
    --rawfile body "$body_file" \
    "
    $PASS_DEF
    (\$body | has_pass_marker_body)
    ")

  if [[ "$got" == "$expect" ]]; then
    echo "PASS $file (has_pass_marker=$got)"
  else
    echo "FAIL $file: expected has_pass_marker=$expect, got=$got" >&2
    fail=1
  fi
done

exit "$fail"
