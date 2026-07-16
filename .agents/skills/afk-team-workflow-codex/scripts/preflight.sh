#!/usr/bin/env bash
# preflight.sh — fail-loud local/Git half of the Codex AFK hard start gate.

set -euo pipefail

die() { echo "AFK_PREFLIGHT_ERROR: $*" >&2; exit 1; }

usage() {
  cat >&2 <<'EOF'
AFK_PREFLIGHT_ERROR: usage: bash preflight.sh --repo <absolute-path> \
  --github-connector-ok --heartbeat-ok
EOF
  exit 2
}

REPO=""
GITHUB_CONNECTOR_OK=false
HEARTBEAT_OK=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 || usage ;;
    --github-connector-ok) GITHUB_CONNECTOR_OK=true; shift ;;
    --heartbeat-ok) HEARTBEAT_OK=true; shift ;;
    *) usage ;;
  esac
done

[[ -n "$REPO" ]] || die "--repo is required"
[[ "$REPO" = /* ]] || die "--repo must be an absolute path: $REPO"
[[ -d "$REPO" ]] || die "repository directory not found: $REPO"
REPO=$(cd "$REPO" && pwd -P)
[[ "$GITHUB_CONNECTOR_OK" == true ]] || die "GitHub connector probe was not attested"
[[ "$HEARTBEAT_OK" == true ]] || die "current-task heartbeat probe was not attested"

for cmd in git gh jq; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd not found on PATH"
done

cd "$REPO"
git rev-parse --show-toplevel >/dev/null 2>&1 || die "--repo is not a Git worktree: $REPO"
GIT_TOP=$(git rev-parse --show-toplevel)
GIT_TOP=$(cd "$GIT_TOP" && pwd -P)
[[ "$GIT_TOP" == "$REPO" ]] || die "--repo must point at the main checkout root"
GIT_DIR=$(cd "$(git rev-parse --git-dir)" && pwd -P)
GIT_COMMON_DIR=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
[[ "$GIT_DIR" == "$GIT_COMMON_DIR" ]] || die "--repo must be the main checkout, not a linked worktree: $REPO"
git remote get-url origin >/dev/null 2>&1 || die "git remote 'origin' is not configured"

GH_REPO=""
if ! GH_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>&1); then
  die "gh read-only repository probe failed: ${GH_REPO:0:300}"
fi
[[ -n "$GH_REPO" ]] || die "gh read-only repository probe returned no repository"
GH_CAN_WRITE=""
if ! GH_CAN_WRITE=$(gh api "repos/$GH_REPO" --jq '.permissions.push // false' 2>&1); then
  die "gh repository permission probe failed: ${GH_CAN_WRITE:0:300}"
fi
[[ "$GH_CAN_WRITE" == "true" ]] || die "gh identity lacks repository write permission: $GH_REPO"

FETCH_OUTPUT=""
if ! FETCH_OUTPUT=$(git fetch --dry-run origin 2>&1); then
  die "git fetch permission probe failed: ${FETCH_OUTPUT:0:300}"
fi

PROBE_REF="refs/heads/issue/afk-preflight-probe-$(date -u +%Y%m%d%H%M%S)-$$"
PUSH_OUTPUT=""
if ! PUSH_OUTPUT=$(git push --dry-run --porcelain origin "HEAD:${PROBE_REF}" 2>&1); then
  die "git push credential probe failed: ${PUSH_OUTPUT:0:300}"
fi

PROBE_ROOT=""
PROBE_WT=""
cleanup() {
  if [[ -n "$PROBE_WT" && -d "$PROBE_WT" ]]; then
    git -C "$REPO" worktree remove --force "$PROBE_WT" >/dev/null 2>&1 || true
  fi
  if [[ -n "$PROBE_ROOT" && -d "$PROBE_ROOT" ]]; then
    rm -rf -- "$PROBE_ROOT" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

TEMP_ROOT="${TMPDIR:-${TEMP:-${TMP:-}}}"
[[ -n "$TEMP_ROOT" && -d "$TEMP_ROOT" ]] || die "no usable system temp directory in TMPDIR, TEMP, or TMP"
PROBE_ROOT=$(mktemp -d "$TEMP_ROOT/afk-codex-preflight.XXXXXX")
PROBE_WT="$PROBE_ROOT/worktree"
WORKTREE_OUTPUT=""
if ! WORKTREE_OUTPUT=$(git worktree add --detach "$PROBE_WT" HEAD 2>&1); then
  die "worktree write probe failed: ${WORKTREE_OUTPUT:0:300}"
fi
git worktree remove --force "$PROBE_WT" >/dev/null 2>&1 || die "probe worktree cleanup failed: $PROBE_WT"
PROBE_WT=""
rm -rf -- "$PROBE_ROOT" >/dev/null 2>&1 || die "probe temp directory cleanup failed: $PROBE_ROOT"
PROBE_ROOT=""

jq -n \
  --arg repo "$REPO" \
  --arg github_repo "$GH_REPO" \
  '{
    ok: true,
    repo: $repo,
    github_repo: $github_repo,
    github_connector: true,
    heartbeat: true,
    gh_read: true,
    gh_write: true,
    jq: true,
    git_fetch: true,
    git_push_dry_run: true,
    worktree_write: true
  }'
