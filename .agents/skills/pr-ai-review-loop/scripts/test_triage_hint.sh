#!/usr/bin/env bash
# test_triage_hint.sh — regression test for poll.sh's `triage_hint`: present exactly when a
# batch of new findings awaits judgment, and carrying the convergence reminder from round 3.
#
# USAGE
#   bash test_triage_hint.sh
#
# Defs are extracted verbatim from poll.sh so the shipped functions are exercised. Inputs
# are minimal snapshot shapes carrying only the fields has_new_findings reads.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SH="$SCRIPT_DIR/poll.sh"

extract_def() {
  local name="$1" def
  def=$(awk -v target="$name" '
    $0 ~ "^[[:space:]]*def " target "[:(]" {flag=1}
    flag {print; if (/;[[:space:]]*$/ && !/^[[:space:]]*def /) exit}
  ' "$POLL_SH")
  if [[ -z "$def" ]]; then
    echo "could not extract $name def from $POLL_SH" >&2
    exit 4
  fi
  printf '%s\n' "$def"
}

DEFS="$(extract_def has_new_findings)
$(extract_def triage_hint)"

fail=0
check() {
  local label="$1" snapshot="$2" rounds="$3" expected="$4" got
  got=$(jq -n --argjson s "$snapshot" --argjson rounds "$rounds" \
    "$DEFS"' $s | triage_hint($rounds) | if . == null then "none" elif test("收敛") then "repeat" else "triage" end' | tr -d '"')
  if [[ "$got" == "$expected" ]]; then
    echo "PASS $label ($got)"
  else
    echo "FAIL $label: expected $expected, got $got" >&2
    fail=1
  fi
}

EMPTY='{"inline_comments_by_user": {}, "coderabbit": {"reviews": []}, "codex": {"reviews": []}}'
NEW_INLINE='{"inline_comments_by_user": {"coderabbitai[bot]": [{"is_new": true, "is_ack": false}]}, "coderabbit": {"reviews": []}, "codex": {"reviews": []}}'
NEW_ACK='{"inline_comments_by_user": {"coderabbitai[bot]": [{"is_new": true, "is_ack": true}, {"is_new": false, "is_ack": false}]}, "coderabbit": {"reviews": []}, "codex": {"reviews": []}}'
NEW_CR_REVIEW='{"inline_comments_by_user": {}, "coderabbit": {"reviews": [{"is_new": true}]}, "codex": {"reviews": []}}'
CODEX_BODY='{"inline_comments_by_user": {}, "coderabbit": {"reviews": []}, "codex": {"reviews": [{"is_new": true, "has_body_finding": true}]}}'
CODEX_PASS='{"inline_comments_by_user": {}, "coderabbit": {"reviews": []}, "codex": {"reviews": [{"is_new": true, "has_body_finding": false}]}}'
NO_INLINE_KEY='{"coderabbit": {"reviews": []}, "codex": {"reviews": []}}'

check "nothing new" "$EMPTY" 0 none
check "missing inline map" "$NO_INLINE_KEY" 0 none
check "only acks and old inline" "$NEW_ACK" 0 none
check "codex review without a body finding" "$CODEX_PASS" 0 none
check "new non-ack inline" "$NEW_INLINE" 0 triage
check "new CodeRabbit review body" "$NEW_CR_REVIEW" 1 triage
check "new Codex body finding" "$CODEX_BODY" 2 triage
check "round 3 adds the convergence reminder" "$NEW_INLINE" 3 repeat
check "no findings stays silent at round 5" "$EMPTY" 5 none

exit "$fail"
