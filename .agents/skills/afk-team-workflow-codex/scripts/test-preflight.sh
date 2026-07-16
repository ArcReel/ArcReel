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
  'if [[ "$1" == "api" && "$2" == "repos/ArcReel/ArcReel" ]]; then printf "true\\n"; exit 0; fi' \
  'echo "unexpected gh invocation: $*" >&2; exit 42' > "$FAKEBIN/gh"
chmod +x "$FAKEBIN/git" "$FAKEBIN/gh"

GIT_ARGS_LOG="$TMP_ROOT/git-args.log" GIT_LEAVE_PROBE_ROOT_FILE=1 \
  PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" \
  --github-connector-ok \
  --heartbeat-ok > "$TMP_ROOT/success.json"

grep -q '^push --dry-run --porcelain origin HEAD:refs/heads/issue/afk-preflight-probe-' "$TMP_ROOT/git-args.log"

jq -e '
  .ok == true
  and .github_connector == true
  and .heartbeat == true
  and .gh_read == true
  and .gh_write == true
  and .git_fetch == true
  and .git_push_dry_run == true
  and .worktree_write == true
' "$TMP_ROOT/success.json" >/dev/null

if PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" --heartbeat-ok \
  > "$TMP_ROOT/missing.out" 2> "$TMP_ROOT/missing.err"; then
  echo "expected missing connector attestation to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: GitHub connector probe was not attested' "$TMP_ROOT/missing.err"

if GH_FAIL=1 PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" --github-connector-ok --heartbeat-ok \
  > "$TMP_ROOT/gh.out" 2> "$TMP_ROOT/gh.err"; then
  echo "expected gh permission failure to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: gh read-only repository probe failed: fixture gh denied' "$TMP_ROOT/gh.err"

LINKED="$TMP_ROOT/linked"
git -C "$REPO" worktree add --detach "$LINKED" HEAD >/dev/null
if PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$LINKED" --github-connector-ok --heartbeat-ok \
  > "$TMP_ROOT/linked.out" 2> "$TMP_ROOT/linked.err"; then
  echo "expected linked worktree to fail" >&2
  exit 1
fi
grep -q '^AFK_PREFLIGHT_ERROR: --repo must be the main checkout, not a linked worktree:' "$TMP_ROOT/linked.err"
git -C "$REPO" worktree remove --force "$LINKED"

[[ "$(git -C "$REPO" worktree list --porcelain | grep -c '^worktree ')" -eq 1 ]]
echo "preflight tests passed"
