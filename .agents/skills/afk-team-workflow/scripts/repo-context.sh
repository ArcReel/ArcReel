#!/usr/bin/env bash

# shellcheck disable=SC2034 # REPO_CONTEXT_SHIFT is returned to the sourcing script.

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
