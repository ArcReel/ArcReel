#!/usr/bin/env bash
# test_walkthrough_head.sh — regression test for poll.sh's CodeRabbit walkthrough commit
# anchor (cr_walkthrough_head) and the reviewed_current_head rule built on it (PITFALL 9).
#
# USAGE
#   bash test_walkthrough_head.sh
#
# CodeRabbit submits a review object only on the first pass; every later push is an
# incremental review that rewrites the walkthrough comment in place and submits no new
# review, so the latest review's commit stays pinned to the first reviewed HEAD while the
# walkthrough body carries the anchor that moves. This test pins both the parser and the
# rule to the exact bytes CodeRabbit ships. Defs are extracted verbatim from poll.sh so
# the shipped functions are exercised, not a copy. Fixtures (real bodies, captured via
# `gh api repos/<owner>/<repo>/issues/<pr>/comments`):
#   - coderabbit_walkthrough_incremental_pr2614.txt: a walkthrough rewritten by an
#     incremental review; carries both the "📥 Commits" range line and the
#     change_assessment_commit marker, both naming HEAD 1836151d, while the only review
#     object stays anchored on 6dfd561b.
#   - coderabbit_walkthrough_full_pr2620.txt: a first (full) review's walkthrough; no range
#     line at all, only the change_assessment_commit and final_review_risk_coverage markers
#     (HEAD e77c32c9).
# The marker-less shapes are derived in-test by stripping lines from those bodies.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SH="$SCRIPT_DIR/poll.sh"
TESTDATA="$SCRIPT_DIR/testdata"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found on PATH" >&2
  exit 3
fi

extract_def() {
  local name="$1" def
  def=$(awk -v target="$name" '
    $0 ~ "^[[:space:]]*def " target ":" {flag=1}
    flag {print; if (/;[[:space:]]*$/ && !/^[[:space:]]*def /) exit}
  ' "$POLL_SH")
  if [[ -z "$def" ]]; then
    echo "could not extract $name def from $POLL_SH" >&2
    exit 4
  fi
  printf '%s\n' "$def"
}

HEAD_MATCH_DEF=$(extract_def codex_commit_is_current_head)
RL_DEF=$(extract_def cr_rate_limited_body)
WT_HEAD_DEF=$(extract_def cr_walkthrough_head)
WT_DEF=$(extract_def cr_walkthrough_rest)

INCREMENTAL="$TESTDATA/coderabbit_walkthrough_incremental_pr2614.txt"
FULL="$TESTDATA/coderabbit_walkthrough_full_pr2620.txt"
for f in "$INCREMENTAL" "$FULL"; do
  [[ -f "$f" ]] || { echo "fixture missing at $f" >&2; exit 4; }
done

HEAD_2614="1836151d17c1da600b4897ebb44e3680f627d1d6"
HEAD_2620="e77c32c942b32a0efdea00525568eadf6aee5fc9"
FIRST_REVIEW_2614="6dfd561b0000000000000000000000000000abcd"

fail=0

# ---- cr_walkthrough_head: every observed anchor shape, in preference order ----
# name | fixture | drop lines matching this regex before parsing ("" = none) | expected
ANCHOR_CASES=(
  "marker+range|$INCREMENTAL||\"$HEAD_2614\""
  "range only|$INCREMENTAL|change_assessment_commit|\"$HEAD_2614\""
  "marker only|$FULL||\"$HEAD_2620\""
  "coverage only|$FULL|change_assessment_commit|\"$HEAD_2620\""
  "no anchor|$FULL|change_assessment_commit|final_review_risk_coverage|null"
)

for tc in "${ANCHOR_CASES[@]}"; do
  IFS='|' read -r name fixture drop1 drop2 expected <<<"$tc"
  # A 5-field row is "name|fixture|drop|drop|expected"; a 4-field row has one drop pattern.
  if [[ -z "$expected" ]]; then expected="$drop2"; drop2=""; fi
  got=$(jq -c \
    --rawfile body "$fixture" \
    --arg drop1 "$drop1" --arg drop2 "$drop2" \
    -n "
    $WT_HEAD_DEF
    \$body
    | split(\"\\n\")
    | map(select((\$drop1 == \"\" or (test(\$drop1) | not))
                 and (\$drop2 == \"\" or (test(\$drop2) | not))))
    | join(\"\\n\")
    | cr_walkthrough_head
    ")
  if [[ "$got" == "$expected" ]]; then
    echo "PASS anchor/$name ($got)"
  else
    echo "FAIL anchor/$name: expected $expected, got $got" >&2
    fail=1
  fi
done

# ---- cr_walkthrough_rest: the rule the loop actually reads ----
# The incremental-review shape: one review object pinned to the first reviewed HEAD,
# walkthrough rewritten (updated_at > last push) with an anchor on the current HEAD.
# name | head | reviews json | commit the R1 review is anchored on | expected reviewed_current_head
LAST_PUSH="2026-09-21T18:00:00Z"
WT_UPDATED_AT="2026-09-21T18:10:00Z"
ONE_REVIEW='[{"id":"R1","submittedAt":"2026-09-21T16:41:10Z","author":{"login":"coderabbitai"}}]'
OTHER_HEAD="abcdef0123456789abcdef0123456789abcdef01"
RULE_CASES=(
  "incremental review on current head|$HEAD_2614|$ONE_REVIEW|$FIRST_REVIEW_2614|true"
  "walkthrough anchored on an older head|$OTHER_HEAD|$ONE_REVIEW|$FIRST_REVIEW_2614|false"
  "review on current head, walkthrough anchored elsewhere|$OTHER_HEAD|$ONE_REVIEW|$OTHER_HEAD|false"
  "no review object, stale walkthrough|$OTHER_HEAD|[]|$FIRST_REVIEW_2614|false"
  "no review object, current walkthrough|$HEAD_2614|[]|$FIRST_REVIEW_2614|true"
)

for tc in "${RULE_CASES[@]}"; do
  IFS='|' read -r name head reviews review_commit expected <<<"$tc"
  got=$(jq -r \
    --rawfile body "$INCREMENTAL" \
    --arg head "$head" \
    --argjson reviews "$reviews" \
    --arg first_review_commit "$review_commit" \
    --arg last_push "$LAST_PUSH" \
    --arg updated_at "$WT_UPDATED_AT" \
    -n "
    [{id: 1, user: {login: \"coderabbitai[bot]\"}, created_at: \"2026-09-21T16:41:00Z\",
      updated_at: \$updated_at, body: \$body}] as \$sub_a
    | {reviews: \$reviews, headRefOid: \$head} as \$main
    | {R1: \$first_review_commit} as \$review_commit_by_id
    | $HEAD_MATCH_DEF
      $RL_DEF
      $WT_HEAD_DEF
      $WT_DEF
      (cr_walkthrough_rest | \"\\(.reviewed_current_head) walkthrough_head=\\(.walkthrough_head) latest_review_commit=\\(.latest_review_commit)\")
    ")
  if [[ "${got%% *}" == "$expected" ]]; then
    echo "PASS rule/$name ($got)"
  else
    echo "FAIL rule/$name: expected reviewed_current_head=$expected, got $got" >&2
    fail=1
  fi
done

exit "$fail"
