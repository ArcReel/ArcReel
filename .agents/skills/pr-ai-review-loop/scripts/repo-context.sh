#!/usr/bin/env bash

# shellcheck disable=SC2034 # REPO_CONTEXT_SHIFT is returned to the sourcing script.

# Source this file, call enter_repo_root <error-prefix> "$@", then shift by
# REPO_CONTEXT_SHIFT before parsing the script's own arguments.
#
# enter_snapshot_dir <error-prefix> sets SNAP_DIR to the user-private (0700) snapshot dir
# shared by poll.sh, query.sh and round.sh, creating it when absent. snapshot_file_for
# <owner/repo> <pr> prints the per-PR snapshot path inside it; sibling files (index, seen,
# rounds) derive from that path by replacing its .json suffix.
enter_repo_root() {
  local error_prefix="$1"
  shift

  local requested_root="."
  REPO_CONTEXT_SHIFT=0
  if [[ "${1:-}" == "--repo-root" ]]; then
    if [[ $# -lt 2 || -z "${2:-}" ]]; then
      echo "${error_prefix}: --repo-root needs a path" >&2
      return 2
    fi
    requested_root="$2"
    REPO_CONTEXT_SHIFT=2
  fi

  local resolved_root
  resolved_root=$(git -C "$requested_root" rev-parse --show-toplevel 2>/dev/null) || {
    echo "${error_prefix}: not a Git worktree: $requested_root" >&2
    return 2
  }
  cd "$resolved_root" || {
    echo "${error_prefix}: cannot enter Git worktree: $resolved_root" >&2
    return 2
  }
}

# A pre-planted symlink (mkdir -p would follow it) or a foreign-owned dir at the path
# aborts loudly before anything is written. Plain mkdir (no -p) never follows a symlink to
# create elsewhere; on EEXIST re-validate, including -L for a symlink raced in after the check.
# The dir is born 700 (umask 077 in a subshell) so a group-writable umask leaves no window
# before chmod in which another user could plant a symlink at a snapshot or ledger path.
enter_snapshot_dir() {
  local error_prefix="$1"
  local snap_base="${TMPDIR:-/tmp}"
  SNAP_DIR="${snap_base%/}/pr-ai-review-loop-$(id -u)"
  if [[ -L "$SNAP_DIR" ]]; then
    echo "${error_prefix}: snapshot dir is a symlink: $SNAP_DIR" >&2
    return 4
  fi
  if ! (umask 077; mkdir "$SNAP_DIR") 2>/dev/null; then
    if [[ -L "$SNAP_DIR" || ! -d "$SNAP_DIR" || ! -O "$SNAP_DIR" ]]; then
      echo "${error_prefix}: snapshot dir is a symlink, missing, or not owned by the current user: $SNAP_DIR" >&2
      return 4
    fi
  fi
  chmod 700 "$SNAP_DIR"
}

# The repo slug keeps same-numbered PRs from different repos apart.
snapshot_file_for() {
  local owner_repo="$1" pr="$2"
  printf '%s\n' "$SNAP_DIR/poll-${owner_repo//\//-}-${pr}.json"
}
