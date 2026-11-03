#!/usr/bin/env bash
# Exercise the public script interfaces with REST-only, multi-page GitHub responses.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir "$TMP_ROOT/bin" "$TMP_ROOT/tmp" "$TMP_ROOT/repo"
git -C "$TMP_ROOT/repo" init -q
export TMPDIR="$TMP_ROOT/tmp" REST_TEST_ROOT="$TMP_ROOT"
cat > "$TMP_ROOT/bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == api && "$*" != *graphql* && "$*" != *--paginate* && "$2" != repositories/* ]] || {
  echo 'HTTP 403: GraphQL and numeric repository routes are unavailable' >&2
  exit 1
}
printf '%s\n' "$*" >> "$REST_TEST_ROOT/requests"
endpoint="$2"
case "$endpoint" in
  'repos/{owner}/{repo}') echo ArcReel/ArcReel ;;
  repos/ArcReel/ArcReel/pulls/1767)
    if [[ "$*" == *--jq* ]]; then echo abc123
    else echo '{"number":1767,"created_at":"2026-01-01T00:00:00Z","head":{"sha":"abc123"}}'; fi ;;
  *'?per_page=100&page=2'|*'&per_page=100&page=2')
    if [[ "${REST_TEST_FAIL_PAGE:-}" == 2 ]]; then echo 'HTTP 500: page failed' >&2; exit 1; fi
    case "$endpoint" in
      */reviews*) echo '[{"id":101,"node_id":"R101","user":{"login":"chatgpt-codex-connector[bot]"},"submitted_at":"2026-01-03T00:00:00Z","state":"COMMENTED","commit_id":"abc123","body":"Reviewed commit: abc123"}]' ;;
      */issues/1767/comments*) echo '[{"id":201,"node_id":"C201","user":{"login":"chatgpt-codex-connector[bot]"},"created_at":"2026-01-03T00:00:00Z","updated_at":"2026-01-03T00:00:00Z","body":"Codex Review: Didn\u0027t find any major issues. Reviewed commit: abc123"}]' ;;
      */commits*) echo '[{"sha":"abc123","commit":{"committer":{"date":"2026-01-02T00:00:00Z"}}}]' ;;
      *) echo '[]' ;;
    esac ;;
  */reviews*) jq -n '[range(1;101) | {id:.,node_id:("R"+tostring),user:{login:"coderabbitai[bot]"},submitted_at:"2026-01-01T00:00:00Z",state:"COMMENTED",commit_id:"old",body:""}]' ;;
  */issues/1767/comments*) jq -n '[range(1;101) | {id:.,node_id:("C"+tostring),user:{login:"human"},created_at:"2026-01-01T00:00:00Z",updated_at:"2026-01-01T00:00:00Z",body:""}]' ;;
  */pulls/1767/commits*) jq -n '[range(1;101) | {sha:("old"+tostring),commit:{committer:{date:"2026-01-01T00:00:00Z"}}}]' ;;
  */check-runs*) echo '{"check_runs":[]}' ;;
  *) echo '[]' ;;
esac
GH
chmod +x "$TMP_ROOT/bin/gh"
export PATH="$TMP_ROOT/bin:$PATH"
bash "$SCRIPT_DIR/poll.sh" --repo-root "$TMP_ROOT/repo" 1767 > "$TMP_ROOT/index"
snapshot=$(jq -r .snapshot_file "$TMP_ROOT/index")
jq -e '.head == "abc123" and .last_push_at == "2026-01-02T00:00:00Z" and
  (.commits | length) == 101 and (.coderabbit.reviews | length) == 100 and
  .codex.reviews[0].id == "R101" and .codex.reviews[0].reviewed_current_head and
  .codex.comments[0].id == "C201" and .codex.comments[0].has_pass_marker' "$snapshot" >/dev/null
bash "$SCRIPT_DIR/query.sh" --repo-root "$TMP_ROOT/repo" 1767 details R101 C201 > "$TMP_ROOT/details"
jq -e 'length == 2' "$TMP_ROOT/details" >/dev/null
bash "$SCRIPT_DIR/round.sh" --repo-root "$TMP_ROOT/repo" 1767 mark --implemented 1 --pushback 0 > "$TMP_ROOT/round"
jq -e '.head == "abc123" and .round == 1' "$TMP_ROOT/round" >/dev/null
echo 'PASS: REST-only poll/query/round preserve IDs, bot coverage, and data beyond page one'
cp "$snapshot" "$TMP_ROOT/before"
if REST_TEST_FAIL_PAGE=2 bash "$SCRIPT_DIR/poll.sh" --repo-root "$TMP_ROOT/repo" 1767 > "$TMP_ROOT/failed" 2> "$TMP_ROOT/error"; then
  echo 'FAIL: a failed later page must fail the poll' >&2; exit 1
fi
cmp "$snapshot" "$TMP_ROOT/before"
grep -q POLL_ERROR "$TMP_ROOT/error"
echo 'PASS: a failed later page preserves the previous snapshot and fails loudly'
