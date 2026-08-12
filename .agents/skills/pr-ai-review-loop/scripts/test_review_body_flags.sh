#!/usr/bin/env bash
# Regression tests for actionable findings delivered only in reviewer bodies.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
POLL_SH="$SCRIPT_DIR/poll.sh"
TESTDATA="$SCRIPT_DIR/testdata"

fail=0
CASES=(
  "has_outside_diff_body|coderabbit_outside_diff_pr1767.txt|true"
  "has_outside_diff_body|coderabbit_inline_review_pr1767.txt|false"
  "codex_body_has_finding|codex_body_finding_pr1727.txt|true"
  "codex_body_has_finding|codex_generic_review_pr1727.txt|false"
)

for tc in "${CASES[@]}"; do
  IFS='|' read -r function fixture expected <<<"$tc"
  definition=$(awk -v target="$function" '
    $0 ~ "^[[:space:]]*def " target ":" {flag=1}
    flag {print; if (/;[[:space:]]*$/) exit}
  ' "$POLL_SH")
  if [[ -z "$definition" ]]; then
    echo "FAIL $fixture: could not extract $function from $POLL_SH" >&2
    fail=1
    continue
  fi

  got=$(jq -n --rawfile body "$TESTDATA/$fixture" "$definition (\$body | $function)")
  if [[ "$got" == "$expected" ]]; then
    echo "PASS $fixture ($function=$got)"
  else
    echo "FAIL $fixture: expected $expected, got $got" >&2
    fail=1
  fi
done

exit "$fail"
