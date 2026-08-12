#!/usr/bin/env bash
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

mode=${WAIT_TEST_MODE:-review_change}
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

if [[ "$1 $2" == "repo view" ]]; then
  printf '%s\n' 'ArcReel/ArcReel'
elif [[ "$*" == *'reviews(first:100'* ]]; then
  count=$(next_count review)
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
          echo 'HTTP 429: secondary rate limit; Retry-After: 7' >&2
          exit 1
        fi
        ;;
    esac
  fi
  submitted_at='2026-08-11T00:00:00Z'
  if [[ "$mode" == "review_change" && "$count" -gt 1 ]]; then
    submitted_at='2026-08-11T00:01:00Z'
  fi
  printf '{"data":{"repository":{"pullRequest":{"reviews":{"nodes":[{"id":"R1","submittedAt":"%s","updatedAt":"%s","state":"COMMENTED","commit":{"oid":"abc"}}]}}}}}\n' "$submitted_at" "$submitted_at"
elif [[ "$*" == *'comments(first:100'* ]]; then
  count=$(next_count issue_comment)
  head_sha=${WAIT_TEST_HEAD:-abc}
  updated_at='2026-08-11T00:00:00Z'
  reaction_count=0
  if [[ "$mode" == "head_change" && "$count" -gt 1 ]]; then
    head_sha=def
  fi
  if [[ "$mode" == "issue_comment_change" && "$count" -gt 1 ]]; then
    updated_at='2026-08-11T00:01:00Z'
  fi
  if [[ "$mode" == "comment_reaction_change" && "$count" -gt 1 ]]; then
    reaction_count=1
  fi
  printf '{"data":{"repository":{"pullRequest":{"headRefOid":"%s","comments":{"nodes":[{"id":"C1","updatedAt":"%s","reactionGroups":[{"content":"EYES","reactors":{"totalCount":%s}}]}]}}}}}\n' \
    "$head_sha" "$updated_at" "$reaction_count"
elif [[ "$*" == *'/issues/1767/reactions'* ]]; then
  count=$(next_count reaction)
  content=eyes
  if [[ "$mode" == "reaction_change" && "$count" -gt 1 ]]; then
    content='+1'
  fi
  printf '[{"id":1,"content":"%s","user":{"login":"chatgpt-codex-connector[bot]"}}]\n' "$content"
elif [[ "$*" == *'/pulls/1767/comments'* ]]; then
  count=$(next_count inline)
  if [[ "$mode" == "eof_once" && "$count" -eq 1 ]]; then
    printf '%s' '[{"id":'
    exit 0
  fi
  updated_at='2026-08-11T00:00:00Z'
  if [[ "$mode" == "inline_change" && "$count" -gt 1 ]]; then
    updated_at='2026-08-11T00:01:00Z'
  fi
  printf '[{"id":2,"updated_at":"%s","commit_id":"abc","in_reply_to_id":null,"user":{"login":"github-code-quality[bot]"}}]\n' "$updated_at"
elif [[ "$*" == *'/check-runs?per_page=100'* ]]; then
  count=$(next_count checks)
  status=queued
  conclusion=null
  completed_at=null
  if [[ "$mode" == "check_change" && "$count" -gt 1 ]]; then
    status=completed
    conclusion='"success"'
    completed_at='"2026-08-11T00:01:00Z"'
  fi
  printf '{"check_runs":[{"id":3,"name":"CodeQL","app":{"slug":"github-advanced-security"},"status":"%s","conclusion":%s,"started_at":"2026-08-11T00:00:00Z","completed_at":%s}]}\n' \
    "$status" "$conclusion" "$completed_at"
elif [[ "$*" == *'/code-scanning/alerts?'* ]]; then
  count=$(next_count security)
  if [[ "$mode" == "security_unavailable" ]]; then
    echo 'HTTP 404: no analysis found' >&2
    exit 1
  fi
  if [[ "$mode" == "security_change" && "$count" -gt 1 ]]; then
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
  WAIT_TEST_MODE="$mode" WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" \
    bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 "$@" \
    > "$TMP_ROOT/result.out" 2> "$TMP_ROOT/result.err"
}

for signal in head review issue_comment reaction comment_reaction inline check security; do
  reset_case
  run_wait "${signal}_change" --max 180
  [[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected $signal change after one interval"
  grep -q '^WAIT_CHANGE:' "$TMP_ROOT/result.out" || fail "expected an explicit change result for $signal"
done
grep -q '/pulls/1767/comments' "$TMP_ROOT/gh-args" || fail "probe did not request inline review comments"
grep -q '/check-runs?per_page=100' "$TMP_ROOT/gh-args" || fail "probe did not request check runs"
grep -q '/code-scanning/alerts?' "$TMP_ROOT/gh-args" || fail "probe did not request code-scanning alerts"
echo "PASS: every decision-relevant signal wakes the wait early"

reset_case
run_wait flat --max 125
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,60,5" ]] || fail "expected 60-second probes capped at --max"
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
run_wait eof_once --max 65
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "2,60,3" ]] || fail "expected EOF retry to consume the same wait budget"
[[ "$(<"$TMP_ROOT/review-count")" == "4" ]] || fail "expected the whole baseline probe to restart after EOF"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected successful recovery after EOF"
echo "PASS: truncated JSON retries the entire probe"

reset_case
run_wait http_500_once --max 70
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,2,8" ]] || fail "expected one bounded retry after HTTP 500"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected successful recovery after HTTP 500"
echo "PASS: a transient HTTP failure recovers"

reset_case
if run_wait http_500_always --max 180; then
  fail "expected persistent HTTP 500 failures to fail"
fi
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,2,5,10" ]] || fail "expected three bounded retries"
grep -q '^WAIT_ERROR:' "$TMP_ROOT/result.err" || fail "expected a loud WAIT_ERROR after retry exhaustion"
echo "PASS: persistent transient failures exhaust a bounded retry budget"

reset_case
if run_wait auth_403 --max 180; then
  fail "expected a permission error to fail"
fi
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60" ]] || fail "expected permission errors to skip retries"
grep -q '^WAIT_ERROR:' "$TMP_ROOT/result.err" || fail "expected a loud permission WAIT_ERROR"
echo "PASS: permission failures fail immediately"

reset_case
run_wait rate_limit_once --max 70
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,7,3" ]] || fail "expected Retry-After to control rate-limit backoff"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected rate-limit recovery"
echo "PASS: rate limits honor the server retry hint"

reset_case
run_wait security_unavailable --max 60
[[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected unavailable code scanning to remain a stable signal"
grep -q '^WAIT_TIMEOUT:' "$TMP_ROOT/result.out" || fail "expected unavailable code scanning not to abort waiting"
echo "PASS: an unavailable code-scanning endpoint remains observable without blocking the loop"
