#!/usr/bin/env bash
# herdr-deliver.sh — submit a teammate's prompt through Herdr and confirm the agent took it.
#
# An agent that has just started can swallow its first prompt: the text never arrives, or it
# sits in the input box unsubmitted. The teammate then idles, waiting for work that never
# comes. `herdr agent prompt` reporting success only means the bytes were written, so this
# script treats a prompt as delivered once Herdr observes the agent working or blocked.
#
# USAGE
#   bash herdr-deliver.sh <agent-name> <prompt-file> --marker <text> [--attempts N]
#
#   <agent-name>   live Herdr agent name (or pane ID) of the teammate
#   <prompt-file>  file holding the full prompt text
#   --marker       a string unique to this prompt (the batch-id); used to tell an
#                  unsubmitted prompt still in the input box from a lost one
#   --attempts     delivery attempts before giving up (default 3)
#
# RECOVERY PER ATTEMPT
#   - no working/blocked within Herdr's 5 s window (agent_prompt_stalled):
#       marker visible in the pane -> the text is waiting in the input box: press Enter
#       marker absent             -> the text was lost: submit the prompt again
#
# OUTPUT / EXIT
#   stdout "DELIVERED <agent> status=<s>"                exit 0
#   stderr "PROMPT_NOT_DELIVERED <agent> status=<s>"     exit 1
#   stderr usage / herdr errors other than a stall        exit 2
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }

[[ $# -ge 2 ]] || usage
agent=$1 prompt_file=$2; shift 2
marker="" attempts=3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --marker) marker=${2:?--marker needs a value}; shift 2 ;;
    --attempts) attempts=${2:?--attempts needs a value}; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$marker" && -r "$prompt_file" ]] || usage
prompt=$(cat "$prompt_file")
grep -qF -- "$marker" <<<"$prompt" || { echo "marker not found in $prompt_file" >&2; exit 2; }

status() { herdr agent get "$agent" | jq -r '.result.agent.agent_status'; }

# Wait until working/blocked is observed. 0 = observed; 1 = stalled or timed out; 2 = other error.
observe() {
  local err
  if err=$("$@" --wait --until working --until blocked --timeout 15000 2>&1 >/dev/null); then return 0; fi
  case "$(jq -r '.error.code // empty' <<<"$err" 2>/dev/null)" in
    agent_prompt_stalled|timeout) return 1 ;;
    *) echo "$err" >&2; return 2 ;;
  esac
}

rc=0
observe herdr agent prompt "$agent" "$prompt" || rc=$?
for ((i = 1; i < attempts && rc == 1; i++)); do
  if herdr agent read "$agent" --source visible --lines 80 | grep -qF -- "$marker"; then
    herdr agent send-keys "$agent" enter >/dev/null
    rc=0; observe herdr agent wait "$agent" || rc=$?
  else
    rc=0; observe herdr agent prompt "$agent" "$prompt" || rc=$?
  fi
done

case $rc in
  0) echo "DELIVERED $agent status=$(status)" ;;
  1) echo "PROMPT_NOT_DELIVERED $agent status=$(status)" >&2; exit 1 ;;
  *) exit 2 ;;
esac
