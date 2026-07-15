#!/usr/bin/env bash
# Isolated behavior tests: no live GitHub, connector, automation, or remote branch writes.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREFLIGHT="$SCRIPT_DIR/preflight.sh"
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
  'if [[ "${GH_FAIL:-0}" == "1" ]]; then echo "fixture gh denied" >&2; exit 41; fi' \
  'if [[ "$1" == "repo" && "$2" == "view" ]]; then printf "ArcReel/ArcReel\\n"; exit 0; fi' \
  'echo "unexpected gh invocation: $*" >&2; exit 42' > "$FAKEBIN/gh"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [[ "$1" == "review" && "$2" == "--help" ]]; then printf "Usage: codex review --base BRANCH\\n"; exit 0; fi' \
  'echo "unexpected codex invocation: $*" >&2; exit 43' > "$FAKEBIN/codex"
chmod +x "$FAKEBIN/gh" "$FAKEBIN/codex"

PATH="$FAKEBIN:$PATH" bash "$PREFLIGHT" \
  --repo "$REPO" \
  --github-connector-ok \
  --heartbeat-ok \
  --codex-bin "$FAKEBIN/codex" > "$TMP_ROOT/success.json"

jq -e '
  .ok == true
  and .github_connector == true
  and .heartbeat == true
  and .gh_read == true
  and .git_fetch == true
  and .git_push_dry_run == true
  and .worktree_write == true
  and .codex_review_base == true
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

[[ "$(git -C "$REPO" worktree list --porcelain | grep -c '^worktree ')" -eq 1 ]]
echo "preflight tests passed"
