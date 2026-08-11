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
if [[ "$1 $2" == "repo view" ]]; then
  printf '%s\n' 'ArcReel/ArcReel'
elif [[ "$*" == *'reviews(first:100'* ]]; then
  count_file="$WAIT_TEST_ROOT/review-count"
  count=0
  [[ ! -f "$count_file" ]] || count=$(<"$count_file")
  count=$((count + 1))
  printf '%s\n' "$count" > "$count_file"
  if [[ "${WAIT_TEST_MODE:-change}" == "http_error" && "$count" -gt 1 ]]; then
    echo "HTTP ${WAIT_TEST_HTTP_CODE:-500}: probe failed" >&2
    exit 1
  elif (( count == 1 )) || [[ "${WAIT_TEST_MODE:-change}" =~ ^(flat|reaction_change)$ ]]; then
    submitted_at='2026-08-11T00:00:00Z'
  else
    submitted_at='2026-08-11T00:01:00Z'
  fi
  printf '{"data":{"repository":{"pullRequest":{"reviews":{"nodes":[{"submittedAt":"%s"}]}}}}}\n' "$submitted_at"
elif [[ "$*" == *'/issues/1767/reactions'* ]]; then
  count_file="$WAIT_TEST_ROOT/reaction-count"
  count=0
  [[ ! -f "$count_file" ]] || count=$(<"$count_file")
  count=$((count + 1))
  printf '%s\n' "$count" > "$count_file"
  content=eyes
  if [[ "${WAIT_TEST_MODE:-change}" == "reaction_change" && "$count" -gt 1 ]]; then
    content='+1'
  fi
  printf '[{"content":"%s","user":{"login":"chatgpt-codex-connector[bot]"}}]\n' "$content"
else
  printf '{"data":{"repository":{"pullRequest":{"headRefOid":"%s","comments":{"nodes":[{"updatedAt":"2026-08-11T00:00:00Z"}]}}}}}\n' "${WAIT_TEST_HEAD:-abc}"
fi
EOF
chmod +x "$TMP_ROOT/bin/gh"

cat > "$TMP_ROOT/bin/sleep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$1" >> "$WAIT_TEST_ROOT/sleeps"
EOF
chmod +x "$TMP_ROOT/bin/sleep"

WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 --max 180

[[ "$(<"$TMP_ROOT/review-count")" == "2" ]] || fail "expected baseline plus one changed probe"
[[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected one 60-second probe interval"
grep -q 'headRefOid' "$TMP_ROOT/gh-args" || fail "probe did not request the head SHA"
grep -q 'submittedAt' "$TMP_ROOT/gh-args" || fail "probe did not request review submitted_at"
grep -q 'updatedAt' "$TMP_ROOT/gh-args" || fail "probe did not request comment updated_at"

echo "PASS: wait exits early when a probe signal changes"

rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
WAIT_TEST_MODE=reaction_change WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" \
  bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 --max 180
[[ "$(<"$TMP_ROOT/reaction-count")" == "2" ]] || fail "expected two Codex reaction probes"
[[ "$(<"$TMP_ROOT/sleeps")" == "60" ]] || fail "expected reaction-only completion after one interval"
echo "PASS: wait exits early when the Codex PR reaction changes"

rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
WAIT_TEST_MODE=flat WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" \
  bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 --max 125
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,60,5" ]] || fail "expected 60-second probes capped at --max"
echo "PASS: wait respects the maximum delay"

rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
rm -f "$TMP_ROOT/tmp/pr-ai-review-loop-$(id -u)/wait-ArcReel-ArcReel-1767.head"
WAIT_TEST_MODE=flat WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" \
  bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,60,60,60,60,60" ]] \
  || fail "expected the first wait on a head to default to 360 seconds"
rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
WAIT_TEST_MODE=flat WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" \
  bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,60,60" ]] \
  || fail "expected later waits on the same head to default to 180 seconds"
rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
WAIT_TEST_MODE=flat WAIT_TEST_HEAD=def WAIT_TEST_ROOT="$TMP_ROOT" PATH="$TMP_ROOT/bin:$PATH" \
  bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767
[[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,60,60,60,60,60" ]] \
  || fail "expected a changed head to restore the 360-second default"
echo "PASS: wait defaults to 360 seconds on a new head and 180 seconds thereafter"

for code in 403 429; do
  rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
  WAIT_TEST_MODE=http_error WAIT_TEST_HTTP_CODE="$code" WAIT_TEST_ROOT="$TMP_ROOT" \
    PATH="$TMP_ROOT/bin:$PATH" bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 --max 180
  [[ "$(paste -sd, "$TMP_ROOT/sleeps")" == "60,120" ]] \
    || fail "expected HTTP $code to degrade to sleep-only mode"
done
echo "PASS: rate-limit responses degrade to sleep-only mode"

rm -f "$TMP_ROOT/review-count" "$TMP_ROOT/reaction-count" "$TMP_ROOT/sleeps"
if WAIT_TEST_MODE=http_error WAIT_TEST_HTTP_CODE=500 WAIT_TEST_ROOT="$TMP_ROOT" \
  PATH="$TMP_ROOT/bin:$PATH" bash "$WAIT_SH" "${REPO_ARGS[@]}" 1767 --max 180 \
  >"$TMP_ROOT/error.out" 2>"$TMP_ROOT/error.err"; then
  fail "expected a non-rate-limit GitHub error to fail"
fi
grep -q '^WAIT_ERROR:' "$TMP_ROOT/error.err" || fail "expected a loud WAIT_ERROR prefix"
echo "PASS: non-rate-limit probe errors fail loudly"
