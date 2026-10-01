#!/usr/bin/env bash
# herdr-deliver.sh — submit a teammate's prompt through Herdr and confirm the agent took it.
#
# An agent that has just started can swallow its first prompt: the text never arrives, or it
# sits in the input box unsubmitted. The teammate then idles, waiting for work that never
# comes. `herdr agent prompt` reporting success only means the bytes were written, so this
# script treats a prompt as delivered once Herdr observes the agent working or blocked.
#
# USAGE
#   bash herdr-deliver.sh <agent-name> <prompt-file> [--attempts N]
#
#   <agent-name>   live Herdr agent name (or pane ID) of the teammate
#   <prompt-file>  file holding the full prompt text
#   --attempts     prompt submissions before giving up (default 3)
#
# RECOVERY AFTER A STALL (no working/blocked within Herdr's 5 s window, or 15 s overall)
#   1. Press Enter. It submits a prompt left in the input box; an empty box ignores it.
#      Enter comes first because the pane text cannot show the input box reliably:
#      Claude Code folds a long paste into "[Pasted text #N +M lines]".
#   2. Still no activity: submit the prompt again only while the agent is idle. Any other
#      status (done, unknown, ...) means delivery cannot be ruled out, so stop.
#
# OUTPUT / EXIT
#   stdout "DELIVERED <agent> status=<s>"                exit 0
#   stderr "PROMPT_NOT_DELIVERED <agent> status=<s>"     exit 1
#   stderr usage / herdr errors other than a stall        exit 2
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }

[[ $# -ge 2 ]] || usage
agent=$1 prompt_file=$2; shift 2
attempts=3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --attempts) [[ ${2:-} =~ ^[1-9][0-9]*$ ]] || usage; attempts=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ -r "$prompt_file" ]] || usage
prompt=$(cat "$prompt_file")

status() { herdr agent get "$agent" | jq -r '.result.agent.agent_status' || exit 2; }

# Run a Herdr wait. 0 = working/blocked observed; 1 = stalled or timed out; other errors exit 2.
observe() {
  local err
  if err=$("$@" --until working --until blocked --timeout 15000 2>&1 >/dev/null); then return 0; fi
  case "$(jq -r '.error.code // empty' <<<"$err" 2>/dev/null)" in
    agent_prompt_stalled|timeout) return 1 ;;
    *) echo "$err" >&2; exit 2 ;;
  esac
}

submit() { observe herdr agent prompt "$agent" "$prompt" --wait; }
press_enter() {
  herdr agent send-keys "$agent" enter >/dev/null || exit 2
  observe herdr agent wait "$agent"
}

for ((i = 1; ; i++)); do
  submit && break
  press_enter && break
  s=$(status)
  case $s in
    working|blocked) break ;;
    idle) ((i < attempts)) && continue ;;
  esac
  echo "PROMPT_NOT_DELIVERED $agent status=$s" >&2
  exit 1
done

# Delivery is settled; a failed status read must not turn it into an error.
s=$(status 2>/dev/null) || s=unknown
echo "DELIVERED $agent status=$s"
