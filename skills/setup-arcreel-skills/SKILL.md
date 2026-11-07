---
name: setup-arcreel-skills
description: Connect the current Agent host to ArcReel remote MCP and verify access.
---

# Connect to ArcReel

Connect this Agent host to ArcReel and persist the same connection for the current workspace.

## Configuration

1. Inspect the host’s `arcreel` MCP configuration and workspace `.arcreel/settings.json`. Reuse identical connections; ask for missing values only. If any differs, ask the user to choose before synchronizing.
2. Verify the endpoint ends in `/mcp` and API key begins `arc-`. Use `https`; loopback endpoints may use `http`.
3. Persist a streamable HTTP service named `arcreel` using host-native configuration, with the API key as Bearer credentials (plaintext permitted). If the host supports MCP call timeouts, set at least `600` seconds.
4. Following local workspace configuration conventions, write `.arcreel/settings.json` with only the same connection’s `mcp_url` / `api_key`.
5. After writing keys, handle step-3/4 files inside Git repositories, such as Claude Code project `.mcp.json` and `.arcreel/settings.json`. Each must meet both requirements: a repository `.gitignore` rule excludes it, verified with `git check-ignore -v --no-index` (global excludes or `.git/info/exclude` alone do not count, as those rules do not travel with the repo); and it is untracked, since files listed by `git ls-files` require `git rm --cached` or changes remain committable. List noncompliant files/actions and remind the user to handle them, or use configuration outside the repo. Act for them only after explicit agreement; refusal/no answer means no change. Untracking does not clear history if the same key appeared in prior commits; remind users to revoke/recreate that token in ArcReel.

## Verification

Load configuration using host conventions, then call `list_projects` without arguments. Complete when a structured `projects` list returns and host MCP/workspace config point to the same instance; empty lists are valid. Do not rewrite existing compliant configuration.
