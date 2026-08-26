---
name: setup-arcreel-skills
description: Connect this agent host to an ArcReel remote MCP server and verify access.
disable-model-invocation: true
---

# Set up ArcReel skills

Configure the current agent host to use ArcReel's remote MCP server. Change only the host's MCP configuration; do not modify an ArcReel project.

## Gather credentials

Ask the user for either missing value:

- The ArcReel MCP endpoint URL, ending in `/mcp`.
- An `arc-` API key from **Settings → API keys**. ArcReel shows a new key only once.

Treat the key as a secret. Keep it out of output, shell history, project files, and committed configuration. Send it only to the supplied MCP endpoint.

## Connect

1. Confirm that the endpoint uses `https`, ends in `/mcp`, and that the key starts with `arc-`. Permit `http` only for a loopback endpoint such as `localhost`, `127.0.0.1`, or `[::1]`.
2. Add an MCP server named `arcreel` with streamable HTTP transport and Bearer authentication, using the current host's native MCP configuration surface. Store the key through an environment-variable or secret reference when the host supports one. If the host can only store a literal header, explain where the secret will be written and get confirmation before writing it.
3. For Codex, use `ARCREEL_API_KEY` as `bearer_token_env_var` and set the `arcreel` server's `tool_timeout_sec` to at least `600`; long generation tools can exceed Codex's default timeout.
4. Reload the MCP configuration if the host requires it.

## Verify

Call the ArcReel MCP tool `list_projects` once with no arguments. Setup is complete when the call succeeds and returns a structured `projects` list; an empty list is valid.

On failure, report the failing boundary without exposing the key:

- Authentication failure: create or copy a valid `arc-` key and update the Bearer secret.
- Connection or host validation failure: verify the public `/mcp` URL and the server's MCP host/origin allowlists.
- Missing tool: reload the host's MCP configuration and confirm the server name is `arcreel`.
