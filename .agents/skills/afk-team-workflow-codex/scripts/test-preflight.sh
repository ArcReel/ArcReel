#!/usr/bin/env bash
# Isolated behavior tests: no live GitHub, connector, automation, or remote branch writes.
# shellcheck disable=SC2016  # Single-quoted fixture scripts must expand variables only when invoked.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREFLIGHT="$SCRIPT_DIR/preflight.sh"
REAL_GIT=$(command -v git)
export REAL_GIT
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/afk-codex-preflight-test.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

ORIGIN="$TMP_ROOT/origin.git"
REPO="$TMP_ROOT/repo"
FAKEBIN="$TMP_ROOT/bin"
mkdir -p "$FAKEBIN"

git init --bare "$ORIGIN" >/dev/null
git init -b main "$REPO" >/dev/null
git -C "$REPO" config user.name "AFK Test"
git -C "$REPO" config user.email "afk-test@example.invalid"
printf 'fixture\n' > "$REPO/fixture.txt"
git -C "$REPO" add fixture.txt
git -C "$REPO" commit -m "test: seed" >/dev/null
git -C "$REPO" remote add origin "$ORIGIN"
git -C "$REPO" push -u origin main >/dev/null
touch "$REPO/.afk-fixture"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'for arg in "$@"; do' \
  '  if [[ "$arg" == "--path-format=absolute" ]]; then echo "fixture old git rejects --path-format" >&2; exit 45; fi' \
  'done' \
  'if [[ -n "${GIT_ARGS_LOG:-}" ]]; then printf "%s\\n" "$*" >> "$GIT_ARGS_LOG"; fi' \
  'if [[ "${GIT_LEAVE_PROBE_ROOT_FILE:-0}" == "1" && "$1" == "worktree" && "$2" == "add" ]]; then' \
  '  "$REAL_GIT" "$@" || exit $?' \
  '  printf "fixture residue\\n" > "$(dirname "$4")/.fixture-residue"' \
  '  exit 0' \
  'fi' \
  'exec "$REAL_GIT" "$@"' > "$FAKEBIN/git"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [[ "${GH_FAIL:-0}" == "1" ]]; then echo "fixture gh denied" >&2; exit 41; fi' \
  'if [[ "$1" == "repo" && "$2" == "view" ]]; then printf "ArcReel/ArcReel\\n"; exit 0; fi' \
  'echo "unexpected gh invocation: $*" >&2; exit 42' > "$FAKEBIN/gh"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [[ "$1" == "review" && "$2" == "--help" ]]; then printf "Usage: codex review --base BRANCH\\n"; exit 0; fi' \
  'if [[ "$1" == "exec" ]]; then' \
  '  shift' \
  '  probe_dir=""' \
  '  while [[ $# -gt 0 ]]; do' \
  '    if [[ "$1" == "-C" ]]; then probe_dir="$2"; shift 2; else shift; fi' \
  '  done' \
  '  if [[ "${CODEX_LEAVE_FILE:-0}" == "1" ]]; then' \
  '    printf "%s\\n" "$probe_dir" > "$CODEX_PROBE_LOG"' \
  '    printf "fixture residue\\n" > "$probe_dir/residue.txt"' \
  '  fi' \
  '  if [[ "${CODEX_FAIL:-0}" == "1" ]]; then echo "fixture codex denied" >&2; exit 44; fi' \
  '  printf "AFK_CODEX_AUTH_OK\\n"; exit 0' \
  'fi' \
  'echo "unexpected codex invocation: $*" >&2; exit 43' > "$FAKEBIN/codex"
chmod +x "$FAKEBIN/git" "$FAKEBIN/gh" "$FAKEBIN/codex"

GIT_ARGS_LOG="$TMP_ROOT/git-args.log" GIT_LEAVE_PROBE_ROOT_FILE=1 CODEX_LEAVE_FILE=1 \
  CODEX_PROBE_LOG="$TMP_ROOT/success-codex-probe-dir" PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" \
  --github-connector-ok \
  --heartbeat-ok \
  --codex-bin "$FAKEBIN/codex" > "$TMP_ROOT/success.json"

grep -q '^push --dry-run --porcelain origin HEAD:refs/heads/issue/afk-preflight-probe-' "$TMP_ROOT/git-args.log"
[[ -s "$TMP_ROOT/success-codex-probe-dir" ]]
[[ ! -e "$(cat "$TMP_ROOT/success-codex-probe-dir")" ]]

jq -e '
  .ok == true
  and .github_connector == true
  and .heartbeat == true
  and .gh_read == true
  and .git_fetch == true
  and .git_push_dry_run == true
  and .worktree_write == true
  and .codex_review_base == true
  and .codex_authenticated == true
' "$TMP_ROOT/success.json" >/dev/null

if PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" --heartbeat-ok --codex-bin "$FAKEBIN/codex" \
  > "$TMP_ROOT/missing.out" 2> "$TMP_ROOT/missing.err"; then
  echo "expected missing connector attestation to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: GitHub connector probe was not attested' "$TMP_ROOT/missing.err"

if GH_FAIL=1 PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" --github-connector-ok --heartbeat-ok --codex-bin "$FAKEBIN/codex" \
  > "$TMP_ROOT/gh.out" 2> "$TMP_ROOT/gh.err"; then
  echo "expected gh permission failure to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: gh read-only repository probe failed: fixture gh denied' "$TMP_ROOT/gh.err"

if CODEX_FAIL=1 CODEX_LEAVE_FILE=1 CODEX_PROBE_LOG="$TMP_ROOT/codex-probe-dir" \
  PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" --github-connector-ok --heartbeat-ok --codex-bin "$FAKEBIN/codex" \
  > "$TMP_ROOT/codex.out" 2> "$TMP_ROOT/codex.err"; then
  echo "expected codex authentication failure to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: codex authentication/service probe failed: fixture codex denied' "$TMP_ROOT/codex.err"
[[ -s "$TMP_ROOT/codex-probe-dir" ]]
[[ ! -e "$(cat "$TMP_ROOT/codex-probe-dir")" ]]

LINKED="$TMP_ROOT/linked"
git -C "$REPO" worktree add --detach "$LINKED" HEAD >/dev/null
if PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$LINKED" --github-connector-ok --heartbeat-ok --codex-bin "$FAKEBIN/codex" \
  > "$TMP_ROOT/linked.out" 2> "$TMP_ROOT/linked.err"; then
  echo "expected linked worktree to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: --repo must be the main checkout, not a linked worktree:' "$TMP_ROOT/linked.err"
git -C "$REPO" worktree remove --force "$LINKED"

[[ "$(git -C "$REPO" worktree list --porcelain | grep -c '^worktree ')" -eq 1 ]]
echo "preflight tests passed"
