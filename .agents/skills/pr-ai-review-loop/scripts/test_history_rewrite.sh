#!/usr/bin/env bash
# Fixture: testdata/history_rewrite_pr3813.json, the REST state of a real PR after its
# second force push — fixes squashed into one commit on top of two pre-PR commits. Earlier
# reviews and Codex receipts are anchored on commits the rewrite orphaned (d3112dfb,
# 7cb203e2, 156ff7ef, aecc4a58); the last round is anchored on the rewritten head f250154a.
# Captured with `gh api`, trimmed to the fields poll.sh reads: review and inline bodies cut
# to 200 chars, human comments reduced to the trigger commands with the author renamed.
# The walkthrough body is the full capture in coderabbit_walkthrough_force_push_pr3813.txt
# (scope token redacted). Earlier states are derived in-test from that one capture.

# shellcheck disable=SC2016 # jq filters are single-quoted on purpose; values go in via --arg.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
POLL_SH="$SCRIPT_DIR/poll.sh"
TESTDATA="$SCRIPT_DIR/testdata"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir "$TMP_ROOT/bin" "$TMP_ROOT/repo"
git -C "$TMP_ROOT/repo" init -q

cat > "$TMP_ROOT/bin/gh" <<'EOF'
#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq filters are single-quoted on purpose; values go in via --arg.
set -euo pipefail
[[ "$1" == api && "$*" != *graphql* && "$*" != *--paginate* ]] || {
  echo 'HTTP 403: only named REST routes are available' >&2
  exit 1
}
endpoint="$2"
case "$endpoint" in
  'repos/{owner}/{repo}') echo ArcReel/ArcReel ;;
  repos/ArcReel/ArcReel/pulls/3813) jq '.pr' "$HR_STATE" ;;
  *'&page=2'|*'?per_page=100&page=2') echo '[]' ;;
  */pulls/3813/commits*) jq '.commits' "$HR_STATE" ;;
  */pulls/3813/reviews*) jq '.reviews' "$HR_STATE" ;;
  */pulls/3813/comments*) jq '.inline' "$HR_STATE" ;;
  */issues/3813/comments*) jq '.comments' "$HR_STATE" ;;
  */issues/3813/reactions*) jq '.reactions' "$HR_STATE" ;;
  */issues/comments/*/reactions*) echo '[]' ;;
  */check-runs*) echo '{"check_runs":[]}' ;;
  */code-scanning/alerts*) echo '[]' ;;
  *) echo "unexpected gh invocation: $*" >&2; exit 99 ;;
esac
EOF
chmod +x "$TMP_ROOT/bin/gh"

HEAD=f250154adca3b572cdde28c4fea5acbb58097b84
OLD_HEAD=aecc4a58628b4357612e3612c6a8eea12900e35b
HEAD_COMMITTED_AT=2026-11-12T21:11:37Z

FINAL="$TMP_ROOT/final.json"
jq --rawfile walkthrough "$TESTDATA/coderabbit_walkthrough_force_push_pr3813.txt" \
  '(.comments[] | select(.body == "@@WALKTHROUGH@@") | .body) = $walkthrough' \
  "$TESTDATA/history_rewrite_pr3813.json" > "$FINAL"
jq -e --arg head "$HEAD" '.pr.head.sha == $head and (.commits | last | .sha) == $head
  and (.comments[0].body | contains("change_assessment_commit"))' "$FINAL" >/dev/null \
  || fail "fixture does not have the expected shape"

just_pushed() {
  jq --arg at "$HEAD_COMMITTED_AT" --arg head "$HEAD" --arg old "$OLD_HEAD" --arg updated "$1" '
    .reviews |= map(select(.submitted_at <= $at))
    | .inline |= map(select(.created_at <= $at))
    | .reactions |= map(select(.created_at <= $at))
    | .comments |= (map(select(.created_at <= $at))
        | map(if .updated_at > $at
              then .updated_at = $updated
                   | .body |= (gsub($head; $old) | gsub($head[0:7]; $old[0:7]))
              else . end))
  ' "$FINAL"
}

poll() {
  local name="$1" state="$2"
  mkdir -p "$TMP_ROOT/tmp-$name"
  HR_STATE="$state" TMPDIR="$TMP_ROOT/tmp-$name" PATH="$TMP_ROOT/bin:$PATH" \
    bash "$POLL_SH" --repo-root "$TMP_ROOT/repo" 3813 > "$TMP_ROOT/$name.out" \
    || fail "$name: poll.sh failed"
  local index
  index="$TMP_ROOT/tmp-$name/pr-ai-review-loop-$(id -u)/poll-ArcReel-ArcReel-3813.index.json"
  jq '.index' "$index" > "$TMP_ROOT/$name.index.json"
  printf '%s\n' "$TMP_ROOT/$name.index.json"
}

check() {
  local label="$1" index="$2" filter="$3"
  shift 3
  jq -e "$@" "$filter" "$index" >/dev/null || {
    jq -c '{head, last_push_at, walkthrough: .coderabbit.walkthrough, codex: .codex,
            inline_new: (.inline_new_by_user | map_values(map(.id))),
            cr_reviews: (.coderabbit.reviews | map(.id))}' "$index" >&2
    fail "$label"
  }
  echo "PASS: $label"
}

INDEX=$(poll final "$FINAL")
check "last_push_at is the committer date of the rewritten head" "$INDEX" \
  '.head == $head and .last_push_at == $at' --arg head "$HEAD" --arg at "$HEAD_COMMITTED_AT"
check "the index carries no field that compares commit dates with the PR creation time" "$INDEX" \
  '(has("base_oid") or has("commits_since_pr_created")) | not'
check "the walkthrough anchored on the rewritten head reads as reviewed" "$INDEX" \
  '.coderabbit.walkthrough | .reviewed_current_head and .walkthrough_head == $head' --arg head "$HEAD"
check "only the review submitted after the rewrite is new" "$INDEX" \
  '[.coderabbit.reviews[].id] == ["PRR_kwDORKpYL88AAAABXptnzQ", "PRR_kwDORKpYL88AAAABXpvjOw"]'
check "inline comments older than the rewrite stay old although GitHub re-pointed their commit_id at the head" "$INDEX" \
  '[.inline_new_by_user["coderabbitai[bot]"][].id] == [4556501622, 4556528109]
   and .inline_history_by_user["coderabbitai[bot]"].total == 5'
check "only the Codex receipt naming the rewritten head is a current-HEAD pass" "$INDEX" \
  '[.codex.comments_new[] | select(.reviewed_current_head)]
   | length == 1 and .[0].has_pass_marker and (.[0].reviewed_commit | . == $head[0:length])' --arg head "$HEAD"
check "receipts for orphaned commits are folded into history" "$INDEX" \
  '.codex.comments_history.total == 5'
check "Codex has started on the rewritten head" "$INDEX" '.codex.started_current_head and (.codex.failed_current_head | not)'

just_pushed 2026-11-12T21:09:50Z > "$TMP_ROOT/pushed.json"
INDEX=$(poll pushed "$TMP_ROOT/pushed.json")
check "the watermark moves with the force push before any reviewer reacts" "$INDEX" \
  '.last_push_at == $at' --arg at "$HEAD_COMMITTED_AT"
check "nothing posted on the previous head is new" "$INDEX" \
  '(.coderabbit.reviews | length) == 0
   and ([.inline_new_by_user[][]] | length) == 0
   and ([.codex.comments_new[] | select(.is_new)] | length) == 0'
check "a walkthrough last rewritten before the force push is not a review of the head" "$INDEX" \
  '.coderabbit.walkthrough | (.reviewed_current_head | not) and .walkthrough_head == $old' --arg old "$OLD_HEAD"
check "a receipt for the orphaned previous head is not a current-HEAD pass" "$INDEX" \
  '([.codex.comments_new[] | select(.reviewed_current_head)] | length) == 0'
check "Codex has not started on the rewritten head, earlier triggers notwithstanding" "$INDEX" \
  '(.codex.started_current_head | not) and ([.own_trigger_comments[] | select(.command == "@codex review")] | length) == 4'

just_pushed 2026-11-12T21:12:30Z > "$TMP_ROOT/slow.json"
INDEX=$(poll slow "$TMP_ROOT/slow.json")
check "a walkthrough rewritten after the force push but anchored on the orphaned head is not a review of the head" "$INDEX" \
  '.coderabbit.walkthrough | .updated_at > $at and (.reviewed_current_head | not)' --arg at "$HEAD_COMMITTED_AT"
check "the Codex summary still naming the orphaned head is not a start signal" "$INDEX" \
  '.codex.started_current_head | not'

jq '.comments[1].created_at = "2026-11-12T21:12:30Z"' "$TMP_ROOT/slow.json" > "$TMP_ROOT/late-summary.json"
INDEX=$(poll late-summary "$TMP_ROOT/late-summary.json")
check "a Codex summary created after the force push but naming the orphaned head is neither a review nor a start signal" "$INDEX" \
  '(.codex.started_current_head | not) and ([.codex.comments_new[] | select(.reviewed_current_head)] | length) == 0'

jq --rawfile failed "$SCRIPT_DIR/testdata/codex_summary_failed_pr3850.txt" --arg head "$HEAD" '
  .comments[1].body = ($failed | gsub("00259c60c4d473b0639d7e794faeb26c42a54aad"; $head))
' "$TMP_ROOT/slow.json" > "$TMP_ROOT/failed.json"
INDEX=$(poll failed "$TMP_ROOT/failed.json")
check "a failed Codex review of the head is reported and is not a start signal" "$INDEX" \
  '.codex.failed_current_head and (.codex.started_current_head | not)'

jq --arg head "$HEAD" '
  .commits |= map(if .sha == $head then . else .sha = ("0000000" + .sha[7:]) end)
  | .commits[0].commit.committer.date = "2026-11-12T21:11:35Z"
  | .commits[1].commit.committer.date = "2026-11-12T21:11:36Z"
  | .commits[2].commit.committer.date = "2026-11-12T21:11:37Z"
' "$FINAL" > "$TMP_ROOT/rebased.json"
INDEX=$(poll rebased "$TMP_ROOT/rebased.json")
jq -S 'del(.snapshot_file)' "$TMP_ROOT/final.index.json" > "$TMP_ROOT/final.cmp"
jq -S 'del(.snapshot_file)' "$INDEX" > "$TMP_ROOT/rebased.cmp"
cmp -s "$TMP_ROOT/final.cmp" "$TMP_ROOT/rebased.cmp" \
  || fail "expected a rebase of the earlier commits to leave the index unchanged: $(diff "$TMP_ROOT/final.cmp" "$TMP_ROOT/rebased.cmp" | head -5)"
echo "PASS: rebasing the commits under the head changes nothing in the index"

jq '.commits |= reverse' "$FINAL" > "$TMP_ROOT/reordered.json"
INDEX=$(poll reordered "$TMP_ROOT/reordered.json")
check "last_push_at follows the head sha, not the list order" "$INDEX" \
  '.last_push_at == $at' --arg at "$HEAD_COMMITTED_AT"

jq 'del(.inline[] | select(.id == 4556215000))' "$TMP_ROOT/pushed.json" > "$TMP_ROOT/straggler-before.json"
poll straggler "$TMP_ROOT/straggler-before.json" > /dev/null
INDEX=$(poll straggler "$FINAL")
check "an unseen comment from before the force push surfaces as new" "$INDEX" \
  '[.inline_new_by_user["coderabbitai[bot]"][].id] == [4556215000, 4556501622, 4556528109]'
