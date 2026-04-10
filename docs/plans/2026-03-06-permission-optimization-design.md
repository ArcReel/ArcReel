# Permission Control Optimization Design

## Goal

Migrate the Agent Runtime permission control from custom Hook + Bash full-allowlist to an **SDK declarative rules + simplified Hook** system, achieving:

1. **Bash allowlist mechanism**: Change from automatic full-allowlist to a path-specific allowlist, deny by default
2. **Declarative rules replace static config**: Replace `_READONLY_DIRS` / `_READONLY_FILES` in code with `deny` rules in `settings.json`
3. **Simplified Hook code**: Hook only retains the dynamic "project scope" check, removing ~60 lines of static config code
4. **canUseTool default deny**: Tool calls that do not match any rule are denied

## Current Problems

### Bash Full-Allowlist (Greatest Security Risk)

`DEFAULT_ALLOWED_TOOLS` includes `Bash`, meaning all Bash commands are automatically allowed without any constraints.
The agent can execute dangerous commands such as `curl`, `wget`, and `pip install`.

### Custom Hook Has Too Many Responsibilities

`_build_file_access_hook` handles both:
- Project scope check (dynamic, depends on per-session `project_cwd`)
- Read-only directory check (static, can be replaced with settings.json)

### canUseTool Full-Allowlist

`_can_use_tool` callback returns `PermissionResultAllow` for all tools except `AskUserQuestion`,
effectively bypassing all unmatched permission checks.

### settings.json Not Used

`agent_runtime_profile/.claude/` has no `settings.json`, making no use of the SDK's declarative permission rules.

## Design

### Permission Evaluation Flow

```
Tool call
    │
    ▼
① PreToolUse Hook ←── First line of defense, applies to all tools (including auto-approved)
    │                   Checks whether the path for Read/Write/Edit/Glob/Grep is within
    │                   project_cwd (write) or project_root (read)
    │                   Invalid path → deny, flow terminates
    ▼
② settings.json deny rules ←── Read(//app/.env), Edit(//app/docs/**), etc.
    │                          Matches deny → reject, flow terminates
    ▼
③ Permission mode (default) ←── Not handled, continue
    │
    ▼
④ settings.json allow rules ←── Read, Grep, Glob, Bash(python .../script.py *), etc.
    │                           Matches → auto-allow, flow terminates
    ▼
⑤ canUseTool callback ←── Only reached if nothing above matched
    │                   AskUserQuestion → async user interaction
    │                   Other → PermissionResultDeny("Unauthorized tool call")
    ▼
   Deny
```

### 1. Create settings.json

**File**: `agent_runtime_profile/.claude/settings.json`

```json
{
  "permissions": {
    "deny": [
      "Edit(//app/docs/**)",
      "Edit(//app/lib/**)",
      "Edit(//app/agent_runtime_profile/**)",
      "Edit(//app/scripts/**)",
      "Edit(//app/alembic/**)",
      "Read(//app/.env)",
      "Read(//app/.env.*)",
      "Read(//app/vertex_keys/**)"
    ],
    "allow": [
      "Bash(python .claude/skills/generate-video/scripts/generate_video.py *)",
      "Bash(python .claude/skills/generate-storyboard/scripts/generate_storyboard.py *)",
      "Bash(python .claude/skills/generate-characters/scripts/generate_character.py *)",
      "Bash(python .claude/skills/generate-clues/scripts/generate_clue.py *)",
      "Bash(python .claude/skills/generate-script/scripts/generate_script.py *)",
      "Bash(python .claude/skills/compose-video/scripts/compose_video.py *)",
      "Bash(python .claude/skills/edit-script-items/scripts/edit_script_items.py *)",
      "Bash(ffmpeg *)",
      "Bash(ffprobe *)",
      "Read",
      "Grep",
      "Glob"
    ]
  }
}
```

**Design decisions**:

- **Bash allowlist**: Only allows Skill Python scripts and ffmpeg/ffprobe.
  File operations (ls, rm, cp, mv, etc.) do not go through Bash; instead they use SDK built-in tools
  (Read/Write/Edit/Glob/Grep), which are subject to Hook path checks.
- **deny rules**: Protect read-only directories (docs, lib, etc.) and sensitive files (.env, vertex_keys).
  Uses `//app/` absolute path prefix (fixed path inside Docker).
- **allow rules**: Python script paths are precise down to `.claude/skills/<skill>/scripts/<script>.py`
  (relative to agent cwd, resolved via symlink to `agent_runtime_profile/.claude/`).
- **Read/Grep/Glob global allow**: Read-only tools are auto-allowed, doubly protected by Hook (project scope) and deny
  rules (sensitive files).

### 2. Modify DEFAULT_ALLOWED_TOOLS

```python
# Before
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Bash", "Grep", "Glob", "AskUserQuestion",
]

# After — remove Bash (controlled by settings.json allowlist)
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Grep", "Glob", "AskUserQuestion",
]
```

### 3. Modify canUseTool callback

```python
# Before — full allowlist
async def _can_use_tool(tool_name, input_data, _context):
    if normalized_tool == "askuserquestion":
        return await self._handle_ask_user_question(...)
    return PermissionResultAllow(updated_input=input_data)

# After — default deny (allowlist fallback)
async def _can_use_tool(tool_name, input_data, _context):
    if normalized_tool == "askuserquestion":
        return await self._handle_ask_user_question(...)
    return PermissionResultDeny(message="Unauthorized tool call")
```

### 4. Simplify Hook

**Delete**:
- `_READONLY_DIRS` constant
- `_READONLY_FILES` constant
- `_check_file_access()` method
- `_deny_path_access()` method

**Simplify `_is_path_allowed()`**:

```python
_PATH_TOOLS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}
_WRITE_TOOLS = {"Write", "Edit"}

def _is_path_allowed(self, file_path: str, tool_name: str, project_cwd: Path) -> bool:
    try:
        p = Path(file_path)
        resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
    except (ValueError, OSError):
        return False

    # 1. Within project directory — full access (read + write)
    if resolved.is_relative_to(project_cwd):
        return True

    # 2. Write tools: only project directory allowed
    if tool_name in self._WRITE_TOOLS:
        return False

    # 3. Read tools: allow entire project_root for shared resources
    #    Sensitive files protected by settings.json deny rules
    if resolved.is_relative_to(self.project_root):
        return True

    return False
```

**Changes**:
- Remove `_READONLY_DIRS` loop → allow reading the entire `project_root` (`/app/`)
- Sensitive files are protected by settings.json deny rules (deny takes priority over Hook allow in permission evaluation)
- Write operations remain strictly limited to `project_cwd`

### 5. Local Development Environment

The `//app/` absolute path in settings.json does not match the local development environment (macOS),
so deny rules do not take effect. This is acceptable — the development environment is independently controlled by `.claude/settings.local.json`.

It is recommended to also update `.claude/settings.local.json` to use the new rule syntax
(remove the deprecated `:*` suffix).

## Impact Analysis

### Changed Files

| File | Change Type | Description |
|------|---------|------|
| `agent_runtime_profile/.claude/settings.json` | Create | Declarative permission rules |
| `server/agent_runtime/session_manager.py` | Modify | Simplify Hook, modify DEFAULT_ALLOWED_TOOLS, canUseTool default deny |
| `tests/test_session_manager_project_scope.py` | Modify | Adapt to new Hook logic |
| `tests/test_session_manager_more.py` | Modify | Adapt to canUseTool behavior change |

### Security Improvements

| Dimension | Before | After |
|------|--------|--------|
| Bash commands | All auto-allowed | Allowlist (precise to script path) |
| Read-only directories | Custom Hook check | settings.json deny + Hook |
| Sensitive files | No protection | settings.json deny rules |
| canUseTool fallback | All allowed | Default deny |
| File operations | Bash + SDK tools | SDK tools only (with path checks) |

### Risks

- **Bash allowlist too strict**: If new Skill scripts are added later, the allow rules in settings.json must be updated accordingly
- **Local deny rules do not take effect**: `//app/` path does not match in the development environment, relies on settings.local.json
- **ffmpeg/ffprobe can bypass Read deny rules**: `Bash(ffmpeg *)` allows arbitrary arguments, so in theory
  `ffmpeg -i /app/.env ...` could read sensitive files. The actual risk is manageable (non-media file processing will error),
  but complete protection requires OS-level Sandboxing filesystem isolation (see "Future Extensions" below)

## Future Extensions

### Sandboxing (Next Phase)

The current solution does not include OS-level Sandboxing. This can be enabled in the future:

```json
{
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": true,
    "filesystem": {
      "allowWrite": ["//tmp"]
    },
    "network": {
      "allowedDomains": ["*.googleapis.com"]
    }
  }
}
```

Needs verification:
1. Whether Agent SDK (Python) supports sandbox runtime
2. Docker requires `bubblewrap` + `socat` + `enableWeakerNestedSandbox`
3. Docker image needs to add Node.js (sandbox runtime is an npm package)
