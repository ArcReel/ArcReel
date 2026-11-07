---
status: accepted
---

# Two Agent file-protection layers: kernel sandbox for Bash descendants, PreToolUse hooks for built-in tools

The kernel sandbox (`SandboxSettings.filesystem.denyRead/denyWrite`) constrains only Bash and all its descendants. SDK Read/Write/Edit/Glob/Grep run directly in the main process, outside Bash, and are not covered. Add an application PreToolUse hook (`_is_path_allowed`: sensitive files, cross-project reads/writes, code extensions, cwd escape) as the second layer; both project the same path rules. Intercept in PreToolUse, not `can_use_tool`: Read/Glob/Grep are accepted earlier by allow rules in the SDK permission chain and never reach `can_use_tool`.

## Consequences

- Review Agent file-access changes separately for Bash interception (sandbox profiles) and built-in tools (hooks). Either layer alone leaves a bypass.
- `docs/adr/0003` applies these same-source layers to project JSON writes (denyWrite and `_check_write_access`).
