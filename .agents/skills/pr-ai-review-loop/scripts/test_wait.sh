#!/usr/bin/env bash
# test_wait.sh — regression test for wait.sh: which PR-state changes end the wait, and how
# probes behave under GitHub failures.
#
# USAGE
#   bash test_wait.sh
#
# gh, sleep and date are stubbed on PATH; the clock only advances through the stubbed
# sleep (and through gh in slow_probe mode), so every case is deterministic. The stub gh
# serves one PR whose shape is selected by three knobs:
#   WAIT_TEST_MODE    what changes from the second probe on (default: nothing)
#   WAIT_TEST_CHECKS  required checks on entry: running (default) | finished | empty
#   WAIT_TEST_RULES   base-branch rulesets: rules (default: ci-required + codeql-required
#                     are required) | none (HTTP 404) | empty (no required-checks rule)
#   WAIT_TEST_CODEX   started (default: a Codex reaction after the push) | silent
#   WAIT_TEST_PUSH_AT committer date of the head commit (default: the fake epoch, 0)
# Bot bodies with a fixed shape (Codex receipt, CodeRabbit rate-limit banner) are the real
# captures in testdata/.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
WAIT_SH="$SCRIPT_DIR/wait.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir "$TMP_ROOT/bin"
mkdir "$TMP_ROOT/tmp"
mkdir "$TMP_ROOT/repo"
git -C "$TMP_ROOT/repo" init -q
REPO_ARGS=(--repo-root "$TMP_ROOT/repo")
export TMPDIR="$TMP_ROOT/tmp"

cat > "$TMP_ROOT/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$WAIT_TEST_ROOT/gh-args"
if [[ "$*" == *graphql* || "$*" == *--paginate* || "$1" != api ]]; then
  echo 'HTTP 403: only named REST routes are available' >&2
  exit 1
fi

if [[ -n "${WAIT_TEST_FORBIDDEN_ROUTE:-}" && "$* " == *"$WAIT_TEST_FORBIDDEN_ROUTE"* ]]; then
  cat "$WAIT_TEST_DATA/security_forbidden.txt" >&2
  exit 1
fi

mode=${WAIT_TEST_MODE:-flat}
checks=${WAIT_TEST_CHECKS:-running}
if [[ "$mode" == "slow_probe" ]]; then
  clock=$(<"$WAIT_TEST_ROOT/clock")
  printf '%s\n' "$((clock + 5))" > "$WAIT_TEST_ROOT/clock"
fi

next_count() {
  local name="$1"
  local count_file="$WAIT_TEST_ROOT/${name}-count"
  local count=0
  [[ ! -f "$count_file" ]] || count=$(<"$count_file")
  count=$((count + 1))
  printf '%s\n' "$count" > "$count_file"
  printf '%s\n' "$count"
}

BEFORE='2026-08-11T00:00:00Z'
AFTER='2026-08-11T00:01:00Z'
CR='coderabbitai[bot]'
CODEX='chatgpt-codex-connector[bot]'

if [[ "$1 $2" == "api repos/{owner}/{repo}" ]]; then
  printf '%s\n' 'ArcReel/ArcReel'
elif [[ "$*" == *'/pulls/1767/reviews?'* ]]; then
  if [[ "$mode" == "paged_review_change" && "$*" == *'&page=1' ]]; then
    jq -cn '[range(1;101) | {id:.,submitted_at:"2026-08-11T00:00:00Z",updated_at:"2026-08-11T00:00:00Z",state:"COMMENTED",commit_id:"abc",body:"",user:{login:"coderabbitai[bot]"}}]'
    exit 0
  fi
  count=$(next_count review)
  if [[ "$mode" == "hung_probe" ]]; then
    exec /bin/sleep 2
  fi
  if [[ "$count" -gt 1 ]]; then
    case "$mode" in
      http_500_once)
        if [[ "$count" -eq 2 ]]; then
          echo 'HTTP 500: probe failed' >&2
          exit 1
        fi
        ;;
      http_500_always)
        echo 'HTTP 500: probe failed' >&2
        exit 1
        ;;
      auth_403)
        echo 'HTTP 403: Resource not accessible by personal access token' >&2
        exit 1
        ;;
      rate_limit_once)
        if [[ "$count" -eq 2 ]]; then
          printf 'HTTP/2.0 429 Too Many Requests\nRetry-After: 7\n\n'
          echo 'gh: secondary rate limit (HTTP 429)' >&2
          exit 1
        fi
        ;;
      network_once)
        if [[ "$count" -eq 2 ]]; then
          echo 'dial tcp: lookup api.github.com: no such host' >&2
          exit 1
        fi
        ;;
    esac
  fi
  submitted_at=$BEFORE
  if [[ ( "$mode" == "review_change" || "$mode" == "paged_review_change" ) && "$count" -gt 1 ]]; then
    submitted_at=$AFTER
  fi
  body='original review'
  if [[ "$mode" == "review_body_change" && "$count" -gt 1 ]]; then body='edited review'; fi
  extra='[]'
  if [[ "$count" -gt 1 ]]; then
    case "$mode" in
      new_review) extra='[{"id":"R2","user":"coderabbitai[bot]","body":"**Actionable comments posted: 1**"}]' ;;
      new_codex_review) extra='[{"id":"R3","user":"chatgpt-codex-connector[bot]","body":"### Codex Review"}]' ;;
      new_empty_review) extra='[{"id":"R4","user":"coderabbitai[bot]","body":""}]' ;;
    esac
  fi
  jq -cn --arg submitted_at "$submitted_at" --arg body "$body" --arg after "$AFTER" --argjson extra "$extra" '
    [{id:"R1",submitted_at:$submitted_at,state:"COMMENTED",commit_id:"abc",body:$body,user:{login:"coderabbitai[bot]"}}]
    + [$extra[] | {id,submitted_at:$after,state:"COMMENTED",commit_id:"abc",body,user:{login:.user}}]'
elif [[ "$*" == *'/issues/1767/comments?'* ]]; then
  count=$(next_count issue_comment)
  updated_at=$BEFORE
  reaction_count=0
  if [[ "$mode" == "issue_comment_change" && "$count" -gt 1 ]]; then
    updated_at=$AFTER
  fi
  if [[ "$mode" == "comment_reaction_change" && "$count" -gt 1 ]]; then
    reaction_count=1
  fi
  walkthrough_body='<!-- This is an auto-generated comment: summarize by coderabbit.ai -->'
  walkthrough_updated=$BEFORE
  extra_user=''
  extra_body=''
  if [[ "$count" -gt 1 ]]; then
    case "$mode" in
      walkthrough_rewrite)
        walkthrough_body+=' review in progress by coderabbit.ai'
        walkthrough_updated=$AFTER ;;
      walkthrough_paused)
        walkthrough_body+=' Automatic reviews are paused for this PR.'
        walkthrough_updated=$AFTER ;;
      walkthrough_rate_limited)
        walkthrough_body=$(<"$WAIT_TEST_DATA/quota_alert_pr1115_no_stack.txt")
        walkthrough_updated=$AFTER ;;
      walkthrough_skipped)
        walkthrough_body=$(<"$WAIT_TEST_DATA/coderabbit_walkthrough_skip_bot_pr3319.txt")
        walkthrough_updated=$AFTER ;;
      bot_comment)
        extra_user=$CR
        extra_body='Already reviewed the last commit.' ;;
      codex_quota)
        extra_user=$CODEX
        extra_body='You have reached your Codex usage limits for code reviews.' ;;
      codex_receipt)
        extra_user=$CODEX
        extra_body=$(<"$WAIT_TEST_DATA/codex_receipt_pr3813.txt") ;;
      codex_summary)
        extra_user=$CODEX
        extra_body=$(<"$WAIT_TEST_DATA/codex_summary_pr3813.txt") ;;
      codex_failed)
        extra_user=$CODEX
        extra_body=$(<"$WAIT_TEST_DATA/codex_summary_failed_pr3850.txt") ;;
    esac
  fi
  trigger_at=''
  if [[ "${WAIT_TEST_CODEX:-started}" == "silent" && "$mode" == "codex_triggered" ]]; then
    trigger_at=$BEFORE
  fi
  jq -cn --arg updated_at "$updated_at" --argjson reaction_count "$reaction_count" \
    --arg wb "$walkthrough_body" --arg wu "$walkthrough_updated" \
    --arg extra_user "$extra_user" --arg extra_body "$extra_body" --arg after "$AFTER" \
    --arg trigger_at "$trigger_at" '
    [{id:"C1",created_at:"1969-12-31T00:00:00Z",updated_at:$updated_at,reactions:{eyes:$reaction_count},
      user:{login:"someone"},body:"a human note that mentions @codex review mid-text"},
     {id:"W1",created_at:"1969-12-31T00:00:01Z",updated_at:$wu,reactions:{eyes:0},
      user:{login:"coderabbitai[bot]"},body:$wb}]
    + (if $extra_user == "" then [] else
        [{id:"C2",created_at:$after,updated_at:$after,reactions:{eyes:0},
          user:{login:$extra_user},body:$extra_body}] end)
    + (if $trigger_at == "" then [] else
        [{id:"C3",created_at:$trigger_at,updated_at:$trigger_at,reactions:{eyes:0},
          user:{login:"someone"},body:"@codex review"}] end)'
elif [[ "$*" == *'/pulls/1767' ]]; then
  count=$(<"$WAIT_TEST_ROOT/issue_comment-count")
  head_sha=${WAIT_TEST_HEAD:-abc}
  if [[ "$mode" == "head_change" && "$count" -gt 1 ]]; then head_sha=def; fi
  printf '{"head":{"sha":"%s"},"base":{"ref":"main"}}\n' "$head_sha"
elif [[ "$*" == *'/issues/1767/reactions'* ]]; then
  count=$(next_count reaction)
  if [[ "${WAIT_TEST_CODEX:-started}" == "silent" ]]; then
    printf '[]\n'
    exit 0
  fi
  content=eyes
  if [[ "$mode" == "reaction_change" && "$count" -gt 1 ]]; then
    content='+1'
  fi
  printf '[{"id":1,"content":"%s","created_at":"%s","user":{"login":"chatgpt-codex-connector[bot]"}}]\n' "$content" "$BEFORE"
elif [[ "$*" == *'/pulls/1767/comments'* ]]; then
  count=$(next_count inline)
  if [[ "$mode" == "eof_once" && "$count" -eq 1 ]]; then
    printf '%s' '[{"id":'
    exit 0
  fi
  updated_at=$BEFORE
  if [[ "$mode" == "inline_change" && "$count" -gt 1 ]]; then
    updated_at=$AFTER
  fi
  extra=''
  if [[ "$count" -gt 1 ]]; then
    case "$mode" in
      new_inline) extra=',{"id":5,"updated_at":"'$AFTER'","commit_id":"abc","in_reply_to_id":null,"user":{"login":"chatgpt-codex-connector[bot]"}}' ;;
      new_inline_reply) extra=',{"id":6,"updated_at":"'$AFTER'","commit_id":"abc","in_reply_to_id":2,"user":{"login":"coderabbitai[bot]"}}' ;;
      new_human_inline) extra=',{"id":7,"updated_at":"'$AFTER'","commit_id":"abc","in_reply_to_id":null,"user":{"login":"someone"}}' ;;
    esac
  fi
  if [[ "$mode" == "null_nested_fields" ]]; then
    printf '[{"id":1,"updated_at":"%s","commit_id":"abc","in_reply_to_id":null,"user":null},{"id":2,"updated_at":"%s","commit_id":"abc","in_reply_to_id":null,"user":{"login":"github-advanced-security[bot]"}}]\n' "$updated_at" "$updated_at"
  else
    printf '[{"id":2,"updated_at":"%s","commit_id":"abc","in_reply_to_id":null,"user":{"login":"github-advanced-security[bot]"}}%s]\n' "$updated_at" "$extra"
  fi
elif [[ "$*" == *'/check-runs?per_page=100'* ]]; then
  count=$(next_count checks)
  # Three runs: ci-required (the one that moves), codeql-required (always green) and a
  # non-required CodeRabbit run.
  ci='in_progress:null'
  codeql='completed:"success"'
  other='queued:null'
  [[ "$checks" != "finished" ]] || { ci='completed:"success"'; other='completed:"success"'; }
  if [[ "$count" -gt 1 ]]; then
    case "$mode" in
      check_change) other='completed:"neutral"' ;;
      checks_finish) ci='completed:"success"' ;;
      checks_fail) ci='completed:"failure"' ;;
      all_finish) ci='completed:"success"'; other='completed:"success"' ;;
    esac
  fi
  if [[ "$checks" == "empty" && ( "$mode" != "checks_appear" || "$count" -le 1 ) ]]; then
    printf '{"check_runs":[]}\n'
    exit 0
  fi
  [[ "$mode" != "checks_appear" ]] || ci='completed:"success"'
  if [[ "$mode" == "null_nested_fields" ]]; then
    app=null
  else
    app='{"slug":"github-advanced-security"}'
  fi
  # A superseded failed ci-required run stays listed; only the latest run per name counts.
  rerun=''
  if [[ "$mode" == "checks_rerun_finish" ]]; then
    ci='in_progress:null'
    [[ "$count" -le 1 ]] || ci='completed:"success"'
    rerun='{"id":9,"name":"ci-required","app":null,"status":"completed","conclusion":"failure","started_at":"2026-08-10T23:00:00Z","completed_at":"2026-08-10T23:30:00Z"},'
  fi
  printf '{"check_runs":[%s{"id":10,"name":"ci-required","app":null,"status":"%s","conclusion":%s,"started_at":"%s","completed_at":null},{"id":3,"name":"codeql-required","app":%s,"status":"%s","conclusion":%s,"started_at":"%s","completed_at":null},{"id":11,"name":"CodeRabbit","app":null,"status":"%s","conclusion":%s,"started_at":"%s","completed_at":null}]}\n' \
    "$rerun" "${ci%%:*}" "${ci#*:}" "$BEFORE" "$app" "${codeql%%:*}" "${codeql#*:}" "$BEFORE" "${other%%:*}" "${other#*:}" "$BEFORE"
elif [[ "$*" == *'/commits/'* ]]; then
  next_count commit > /dev/null
  printf '{"sha":"x","commit":{"committer":{"date":"%s"}}}\n' "${WAIT_TEST_PUSH_AT:-1970-01-01T00:00:00Z}"
elif [[ "$*" == *'/rules/branches/main?'* ]]; then
  next_count rules > /dev/null
  case "${WAIT_TEST_RULES:-rules}" in
    none) echo 'HTTP 404: Not Found' >&2; exit 1 ;;
    empty) printf '[{"type":"deletion"}]\n' ;;
    *) printf '[{"type":"deletion"},{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"ci-required"},{"context":"codeql-required"}]}}]\n' ;;
  esac
elif [[ "$*" == *'/code-scanning/alerts?'* ]]; then
  if [[ "$*" == *'ref=refs/pull/'* ]]; then
    alert_scope=security
  else
    alert_scope=base_security
  fi
  count=$(next_count "$alert_scope")
  if [[ "$mode" == "security_rate_limit_once" && "$alert_scope" == security && "$count" -eq 2 ]]; then
    echo 'HTTP 403: You have exceeded a secondary rate limit. Please wait a few minutes before you try again.' >&2
    exit 1
  fi
  if [[ "$mode" == "security_unavailable" ]]; then
    echo 'HTTP 404: no analysis found' >&2
    exit 1
  fi
  if [[ "$mode" == "null_nested_fields" ]]; then
    printf '[{"number":4,"state":"open","rule":null,"tool":null,"most_recent_instance":null}]\n'
  elif [[ ( "$mode" == "${alert_scope}_change" || "$mode" == "existing_alert_change" ) && "$count" -gt 1 ]]; then
    printf '[{"number":4,"state":"open","rule":{"id":"py/test"},"tool":{"name":"CodeQL"},"most_recent_instance":{"ref":"refs/pull/1767/merge","analysis_key":".github/workflows/codeql.yml","location":{"path":"server/app.py","start_line":10}}}]\n'
  else
    printf '[]\n'
  fi
else
  echo "unexpected gh invocation: $*" >&2
  exit 99
fi
EOF
chmod +x "$TMP_ROOT/bin/gh"

cat > "$TMP_ROOT/bin/sleep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$1" >> "$WAIT_TEST_ROOT/sleeps"
clock=$(<"$WAIT_TEST_ROOT/clock")
printf '%s\n' "$((clock + $1))" > "$WAIT_TEST_ROOT/clock"
EOF
chmod +x "$TMP_ROOT/bin/sleep"

cat > "$TMP_ROOT/bin/date" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$*" == "+%s" ]]
printf '%s\n' "$(<"$WAIT_TEST_ROOT/clock")"
EOF
chmod +x "$TMP_ROOT/bin/date"

reset_case() {
  rm -f "$TMP_ROOT"/*-count "$TMP_ROOT/sleeps" "$TMP_ROOT/result.out" "$TMP_ROOT/result.err"
  printf '0\n' > "$TMP_ROOT/clock"
}

run_wait() {
  local mode="$1"
  shift
  WAIT_TEST_MODE="$mode" WAIT_TEST_ROOT="$TMP_ROOT" WAIT_TEST_DATA="$SCRIPT_DIR/testdata" PATH="$TMP_ROOT/bin:$PATH" \
    bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 "$@" \
    > "$TMP_ROOT/result.out" 2> "$TMP_ROOT/result.err"
}

sleeps() { paste -sd, "$TMP_ROOT/sleeps"; }

# expect_wait <label> <mode> <expected first stdout line> [<expected sleeps>]
# Runs with --max 180; a WAIT_READY expectation also requires the return after exactly one
# probe interval unless a sleep list is given.
expect_wait() {
  local label="$1" mode="$2" expected="$3" expected_sleeps="${4:-}"
  reset_case
  run_wait "$mode" --max 180 || fail "$label: wait exited non-zero: $(<"$TMP_ROOT/result.err")"
  local got
  got=$(head -n 1 "$TMP_ROOT/result.out")
  if [[ "$expected" == WAIT_TIMEOUT ]]; then
    [[ "$got" == WAIT_TIMEOUT:* ]] || fail "$label: expected the wait to keep sleeping, got: $got"
    expected_sleeps=${expected_sleeps:-60,60,60}
  else
    [[ "$got" == "$expected" ]] || fail "$label: expected '$expected', got: $got"
    expected_sleeps=${expected_sleeps:-60}
  fi
  [[ "$(sleeps)" == "$expected_sleeps" ]] || fail "$label: expected sleeps $expected_sleeps, got $(sleeps)"
}

# ---- what ends the wait, by required-check state on entry ----
# mode | while required checks run | when they had already finished on entry
SIGNALS=(
  "head_change|WAIT_READY: head_changed|WAIT_READY: head_changed"
  "new_review|WAIT_READY: new_finding|WAIT_READY: new_finding"
  "new_codex_review|WAIT_READY: new_finding|WAIT_READY: new_finding"
  "new_inline|WAIT_READY: new_finding|WAIT_READY: new_finding"
  "security_change|WAIT_READY: new_finding|WAIT_READY: new_finding"
  "bot_comment|WAIT_READY: reviewer_notice|WAIT_READY: reviewer_notice"
  "codex_quota|WAIT_READY: reviewer_notice|WAIT_READY: reviewer_notice"
  "walkthrough_paused|WAIT_READY: reviewer_notice|WAIT_READY: reviewer_notice"
  "walkthrough_rate_limited|WAIT_READY: reviewer_notice|WAIT_READY: reviewer_notice"
  "walkthrough_skipped|WAIT_READY: reviewer_notice|WAIT_READY: reviewer_notice"
  "flat|WAIT_TIMEOUT|WAIT_TIMEOUT"
  "new_empty_review|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "new_inline_reply|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "new_human_inline|WAIT_TIMEOUT|WAIT_TIMEOUT"
  "existing_alert_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "base_security_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "codex_receipt|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "codex_summary|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "walkthrough_rewrite|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "review_body_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "review_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "paged_review_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "issue_comment_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "reaction_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "comment_reaction_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "inline_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
  "check_change|WAIT_TIMEOUT|WAIT_READY: state_changed"
)
for row in "${SIGNALS[@]}"; do
  IFS='|' read -r mode while_running when_done <<<"$row"
  WAIT_TEST_CHECKS=running expect_wait "$mode while checks run" "$mode" "$while_running"
  WAIT_TEST_CHECKS=finished expect_wait "$mode after checks finished" "$mode" "$when_done"
done
grep -q '/pulls/1767/comments' "$TMP_ROOT/gh-args" || fail "probe did not request inline review comments"
grep -q '/check-runs?per_page=100' "$TMP_ROOT/gh-args" || fail "probe did not request check runs"
grep -q '/code-scanning/alerts?' "$TMP_ROOT/gh-args" || fail "probe did not request code-scanning alerts"
grep -q '/rules/branches/main?' "$TMP_ROOT/gh-args" || fail "probe did not request the base branch rules"
echo "PASS: findings, reviewer notices and a head change always end the wait; other changes only once required checks had finished on entry"

# ---- required checks ----
expect_wait "required checks finish green" checks_finish "WAIT_READY: checks_passed"
expect_wait "a required check fails" checks_fail "WAIT_READY: checks_failed(ci-required)"
expect_wait "a rerun supersedes the earlier failure" checks_rerun_finish "WAIT_READY: checks_passed"
WAIT_TEST_CHECKS=empty expect_wait "no check run on a fresh head is unfinished" flat WAIT_TIMEOUT
WAIT_TEST_CHECKS=empty expect_wait "required runs appear finished" checks_appear "WAIT_READY: checks_passed"
echo "PASS: the wait ends when the required checks of the head all finish, and not before any of them exists"

reset_case
WAIT_TEST_CHECKS=finished run_wait flat --max 180
[[ "$(sleeps)" == "60,60,60" ]] || fail "expected finished checks on entry not to end the wait by themselves"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected a timeout with finished checks and no change"
[[ "$(<"$TMP_ROOT/rules-count")" == "1" ]] || fail "expected the required-check rules to be read once per run"
[[ "$(<"$TMP_ROOT/commit-count")" == "1" ]] || fail "expected the head commit to be read once per head"
echo "PASS: what already holds on entry never ends the wait"

for rules in none empty; do
  WAIT_TEST_RULES=$rules expect_wait "rules $rules: required-only finish is not enough" checks_finish WAIT_TIMEOUT
  WAIT_TEST_RULES=$rules expect_wait "rules $rules: every run finished" all_finish "WAIT_READY: checks_passed"
  WAIT_TEST_RULES=$rules WAIT_TEST_CHECKS=empty expect_wait "rules $rules: empty run list is unfinished" flat WAIT_TIMEOUT
  [[ ! -s "$TMP_ROOT/result.err" ]] || fail "expected unreadable rules not to report an error"
done
echo "PASS: without readable required-check rules every check run on the head stands in"

# ---- Codex trigger window ----
reset_case
WAIT_TEST_CODEX=silent run_wait flat --max 600
[[ "$(sleeps)" == "60,60,60,60,60" ]] || fail "expected the Codex window to close 300 seconds after the head commit, got $(sleeps)"
[[ "$(<"$TMP_ROOT/result.out")" == "WAIT_READY: codex_silent" ]] || fail "expected codex_silent, got: $(<"$TMP_ROOT/result.out")"
reset_case
WAIT_TEST_CODEX=silent WAIT_TEST_PUSH_AT=1969-12-31T23:50:00Z run_wait flat --max 600
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected a window already closed on entry not to end the wait"
reset_case
WAIT_TEST_CODEX=silent WAIT_TEST_PUSH_AT=1969-12-31T23:50:00Z run_wait checks_finish --max 600
[[ "$(<"$TMP_ROOT/result.out")" == "WAIT_READY: checks_passed" ]] || fail "expected only checks_passed with the window closed on entry"
reset_case
WAIT_TEST_CODEX=silent run_wait codex_triggered --max 600
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected a posted @codex review to close the window quietly"
for mode in codex_receipt codex_summary; do
  reset_case
  WAIT_TEST_CODEX=silent run_wait "$mode" --max 600
  [[ "$(<"$TMP_ROOT/result.out")" == "WAIT_READY: codex_silent" ]] \
    || fail "expected a $mode naming another commit not to count as a start signal, got: $(<"$TMP_ROOT/result.out")"
done
# A failed review of the head: reported at once, and never a start signal afterwards.
for codex in silent started; do
  reset_case
  WAIT_TEST_CODEX=$codex WAIT_TEST_HEAD=00259c60c4d473b0639d7e794faeb26c42a54aad run_wait codex_failed --max 600
  [[ "$(<"$TMP_ROOT/result.out")" == "WAIT_READY: reviewer_notice" && "$(sleeps)" == "60" ]] \
    || fail "expected a failed Codex review to end the wait at once ($codex), got: $(<"$TMP_ROOT/result.out")"
done
reset_case
WAIT_TEST_CODEX=silent run_wait codex_quota --max 600
grep -q '^WAIT_READY: reviewer_notice' "$TMP_ROOT/result.out" || fail "expected a Codex comment without a commit anchor to stay a notice"
reset_case
run_wait flat --max 600
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected a started Codex never to report codex_silent"
echo "PASS: the Codex trigger window ends the wait once, only when it closes during the wait with no start signal and no trigger"

# ---- budget, retries and failures ----
reset_case
run_wait flat --max 125
[[ "$(sleeps)" == "60,60,5" ]] || fail "expected 60-second probes capped at --max"
[[ "$(<"$TMP_ROOT/review-count")" == "3" ]] || fail "expected no probe after the deadline"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected an explicit timeout result"
echo "PASS: wait respects an explicit maximum and reports timeout"

reset_case
run_wait flat
[[ "$(wc -l < "$TMP_ROOT/sleeps" | tr -d ' ')" == "30" ]] || fail "expected 30 one-minute-or-less intervals"
[[ "$(head -n 29 "$TMP_ROOT/sleeps" | sort -u)" == "60" ]] || fail "expected one-minute polling intervals"
[[ "$(tail -n 1 "$TMP_ROOT/sleeps")" == "30" ]] || fail "expected a 30-second execution reserve"
grep -q '^WAIT_TIMEOUT:.*1770' "$TMP_ROOT/result.out" || fail "expected the default timeout to reserve 30 seconds"
echo "PASS: wait fills a 30-minute command budget with setup reserve"

reset_case
run_wait slow_probe --max 125
[[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected API time to reduce the remaining sleep budget"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected probe time to count toward timeout"
echo "PASS: API latency counts against the wait budget"

reset_case
run_wait hung_probe --max 1
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected a hung request to end as a clean timeout"
[[ ! -s "$TMP_ROOT/result.err" ]] || fail "expected no process-termination diagnostic on timeout"
echo "PASS: an in-flight GitHub request cannot outlive the wait deadline"

reset_case
run_wait eof_once --max 65
[[ "$(sleeps)" == "2,60,3" ]] || fail "expected EOF retry to consume the same wait budget"
[[ "$(<"$TMP_ROOT/review-count")" == "3" ]] || fail "expected the whole baseline probe to restart after EOF"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected successful recovery after EOF"
echo "PASS: truncated JSON retries the entire probe"

reset_case
run_wait http_500_once --max 70
[[ "$(sleeps)" == "60,2,8" ]] || fail "expected one bounded retry after HTTP 500"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected successful recovery after HTTP 500"
echo "PASS: a transient HTTP failure recovers"

reset_case
run_wait network_once --max 70
[[ "$(sleeps)" == "60,2,8" ]] || fail "expected one bounded retry after a Go network error"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected network recovery"
echo "PASS: common GitHub CLI network failures recover"

reset_case
if run_wait http_500_always --max 180; then
  fail "expected persistent HTTP 500 failures to fail"
fi
[[ "$(sleeps)" == "60,2,5,10" ]] || fail "expected three bounded retries"
grep -q '^WAIT_ERROR:' "$TMP_ROOT/result.err" || fail "expected a loud WAIT_ERROR after retry exhaustion"
echo "PASS: persistent transient failures exhaust a bounded retry budget"

reset_case
if run_wait auth_403 --max 180; then
  fail "expected a permission error to fail"
fi
[[ "$(sleeps)" == "60" ]] || fail "expected permission errors to skip retries"
grep -q '^WAIT_ERROR:' "$TMP_ROOT/result.err" || fail "expected a loud permission WAIT_ERROR"
echo "PASS: permission failures fail immediately"

reset_case
run_wait rate_limit_once --max 70
[[ "$(sleeps)" == "60,7,3" ]] || fail "expected Retry-After to control rate-limit backoff"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected rate-limit recovery"
echo "PASS: rate limits honor the server retry hint"

reset_case
run_wait null_nested_fields --max 1
[[ "$(<"$TMP_ROOT/review-count")" == "1" ]] || fail "expected a valid baseline with null nested fields"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected null nested fields to remain observable"
echo "PASS: nullable GitHub response fields do not invalidate a probe"

reset_case
run_wait security_unavailable --max 60
[[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected unavailable code scanning to remain a stable signal"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected unavailable code scanning not to abort waiting"
echo "PASS: an unavailable code-scanning endpoint remains observable without blocking the loop"

for route in '/code-scanning/alerts?' 'ref=refs/pull/' '/code-scanning/alerts?state=open' '/rules/branches/'; do
  for mode in new_review all_finish; do
    reset_case
    WAIT_TEST_FORBIDDEN_ROUTE="$route" run_wait "$mode" --max 180
    [[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected $mode to end the wait after one interval with forbidden $route"
    grep -q '^WAIT_READY:' "$TMP_ROOT/result.out" || fail "expected $mode to end the wait despite forbidden $route"
    [[ ! -s "$TMP_ROOT/result.err" ]] || fail "expected optional 403 not to report an error"
  done
done
echo "PASS: forbidden code scanning or rules routes still let findings and finished checks end the wait"

reset_case
run_wait security_rate_limit_once --max 70
[[ "$(sleeps)" == "60,2,8" ]] || fail "expected a rate-limited code-scanning 403 to retry"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected a rate-limited code-scanning 403 not to read as a security change"
echo "PASS: rate-limited code scanning retries instead of turning unavailable"

for route in '/pulls/1767 ' '/issues/1767/comments?' '/issues/1767/reactions' '/pulls/1767/reviews?' '/pulls/1767/comments' '/check-runs?' '/commits/abc '; do
  reset_case
  if WAIT_TEST_FORBIDDEN_ROUTE="$route" run_wait flat --max 180; then
    fail "expected required $route permission error to fail"
  fi
  [[ ! -s "$TMP_ROOT/sleeps" ]] || fail "expected required 403 to fail without retries"
  grep -q '^WAIT_ERROR:' "$TMP_ROOT/result.err" || fail "expected required 403 to report WAIT_ERROR"
done
echo "PASS: required endpoint permission errors still fail immediately"
