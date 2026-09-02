#!/usr/bin/env bash
# test_round.sh — regression test for round.sh (per-PR round ledger).
#
# USAGE
#   bash test_round.sh
#
# gh is stubbed on PATH: `repo view` answers a fixed slug, `pr view` answers the head SHA
# from ROUND_TEST_HEAD. TMPDIR is redirected so the ledger lands in a throwaway dir.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROUND_SH="$SCRIPT_DIR/round.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir "$TMP_ROOT/bin" "$TMP_ROOT/tmp" "$TMP_ROOT/repo"
git -C "$TMP_ROOT/repo" init -q
REPO_ARGS=(--repo-root "$TMP_ROOT/repo")
export TMPDIR="$TMP_ROOT/tmp"

cat > "$TMP_ROOT/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "repo view" ]]; then
  printf '%s\n' 'ArcReel/ArcReel'
elif [[ "$1 $2" == "pr view" ]]; then
  printf '%s\n' "${ROUND_TEST_HEAD:-abc123}"
else
  echo "unexpected gh invocation: $*" >&2
  exit 99
fi
EOF
chmod +x "$TMP_ROOT/bin/gh"

run_round() {
  PATH="$TMP_ROOT/bin:$PATH" bash "$ROUND_SH" "${REPO_ARGS[@]}" 1767 "$@" \
    > "$TMP_ROOT/result.out" 2> "$TMP_ROOT/result.err"
}

LEDGER="$TMP_ROOT/tmp/pr-ai-review-loop-$(id -u)/poll-ArcReel-ArcReel-1767.rounds.json"

run_round show
[[ "$(jq -c . "$TMP_ROOT/result.out")" == '{"pr":1767,"rounds":[]}' ]] || fail "expected an empty ledger before any mark"
[[ ! -f "$LEDGER" ]] || fail "show must not create the ledger"
echo "PASS: show reports an empty ledger before the first mark"

ROUND_TEST_HEAD=aaa111 run_round mark --implemented 3 --pushback 1 --note "两条防御性检查驳回,一处重复合并"
[[ "$(jq -r .round "$TMP_ROOT/result.out")" == "1" ]] || fail "expected the first mark to be round 1"
[[ "$(jq -r .rounds "$TMP_ROOT/result.out")" == "1" ]] || fail "expected rounds total 1 after the first mark"
[[ "$(jq -r .head "$TMP_ROOT/result.out")" == "aaa111" ]] || fail "expected the head at mark time"
[[ "$(jq -r .note "$TMP_ROOT/result.out")" == "两条防御性检查驳回,一处重复合并" ]] || fail "expected the note to round-trip"
echo "PASS: mark appends round 1 with head and note"

ROUND_TEST_HEAD=aaa111 run_round mark --implemented 0 --pushback 4
[[ "$(jq -r .round "$TMP_ROOT/result.out")" == "2" ]] || fail "expected a pushback-only batch to count as round 2"
[[ "$(jq -r .note "$TMP_ROOT/result.out")" == "" ]] || fail "expected an empty note when --note is omitted"
[[ "$(jq -r '.rounds | map(.head) | unique | length' "$LEDGER")" == "1" ]] || fail "expected a pushback-only round to repeat the previous head"
echo "PASS: a pushback-only round is a round on the same head"

run_round show
[[ "$(jq -r '.rounds | length' "$TMP_ROOT/result.out")" == "2" ]] || fail "expected show to list both rounds"
[[ "$(jq -r '.rounds | map(.round) | join(",")' "$TMP_ROOT/result.out")" == "1,2" ]] || fail "expected rounds numbered in order"
echo "PASS: show lists the ledger in order"

if run_round mark --implemented x --pushback 1; then
  fail "expected a non-numeric count to fail"
fi
grep -q '^ROUND_ERROR:' "$TMP_ROOT/result.err" || fail "expected a loud ROUND_ERROR on a bad count"
if run_round mark --implemented 1; then
  fail "expected a missing --pushback to fail"
fi
grep -q '^ROUND_ERROR:' "$TMP_ROOT/result.err" || fail "expected a loud ROUND_ERROR on a missing count"
[[ "$(jq -r '.rounds | length' "$LEDGER")" == "2" ]] || fail "expected rejected marks to leave the ledger untouched"
echo "PASS: invalid marks fail loudly without touching the ledger"
