# JSON File Write Validation: Design Document for Defending Against Agent File Corruption

**Date**: 2026-03-13
**Status**: Approved
**Branch**: `fix/json-validation-hook`

---

## Problem Background

When the Agent (Claude Agent SDK session) calls the `Edit` tool to modify a script JSON file, the generated `new_string` has an extra comma at the end that merges with an existing comma in the file, producing `},,` (double comma), making the file invalid JSON.

### Complete Cascade Failure Chain

```
Agent Edit episode_2.json
  → new_string has extra trailing comma → },, (invalid JSON)
  → project_events.py: gracefully skipped (WARNING + continue) ✓ no impact
  → routers/projects.py list_projects():
      → calculator.calculate_project_status(name, project)
          → _load_episode_script()
              → pm.load_script() → json.JSONDecodeError
              → only catches FileNotFoundError; JSON error propagates up!
      → broad except Exception catch → "Failed to load project metadata"
  → entire project appears broken/unavailable in the project lobby ✗
```

### Affected Code

- `server/agent_runtime/session_manager.py` — Agent's `Edit`/`Write` tools have no JSON validation
- `lib/status_calculator.py` — `_load_episode_script()` only catches `FileNotFoundError`; `json.JSONDecodeError` propagates up and causes cascade crashes

---

## Solution

Two independent defensive layers:

### Layer 1: Agent Side — `PostToolUse` JSON Validation Hook

**Location**: `server/agent_runtime/session_manager.py`, `_build_options()` method

**Mechanism**: The SDK `PostToolUse` hook triggers after each `Edit` or `Write` completes. The hook checks whether the target file is a `.json` file; if so, it attempts to read and `json.loads()` the file; if parsing fails, it injects a warning to the Agent via `systemMessage`, reporting the exact error location and repair steps, allowing the Agent to **self-detect and immediately fix** the issue.

**Implementation Details**:
- matcher is `Write|Edit` (matches both file-writing tools)
- checks whether `file_path` ends with `.json`
- reads the file with `pathlib.Path(file_path).read_text()`, then calls `json.loads()`
- on parse failure, returns `{"systemMessage": "⚠️ Warning: {file_path} contains invalid JSON, error: {e}. Please immediately Read that file, locate the issue (e.g., extra commas ,,), and Edit to fix it."}`
- `FileNotFoundError` / `PermissionError` are silently skipped (do not interfere with normal flow)
- encapsulated as a standalone method `_build_json_validation_hook()` returning an async callable
- appended to the end of the existing `hook_callbacks` list (chained hook, does not affect existing file access control hooks)

**Effect**: After the Agent completes a write operation, if invalid JSON was produced, the model immediately receives a contextual warning and can auto-fix in the next turn without human intervention.

### Layer 2: Service Read Side — `_load_episode_script` Defensive Fix

**Location**: `lib/status_calculator.py`, `_load_episode_script()` method

**Mechanism**: Additionally catches `(json.JSONDecodeError, ValueError)`, logs a WARNING, and returns `('generated', None)` to indicate that the file exists but is unreadable, allowing status calculation to degrade gracefully without crashing.

**Implementation Details**:
```python
except (json.JSONDecodeError, ValueError) as e:
    logger.warning(
        "Script JSON corrupted, skipping status calculation project=%s file=%s: %s",
        project_name, script_file, e
    )
    return 'generated', None
```

- returns `'generated'` instead of `'none'`: the file's existence indicates the script was generated before, it is simply currently corrupted
- downstream callers' handling of `script=None` confirmed compatible (confirmed: the call chains of `enrich_project` and `calculate_project_status` are safe with `None`)

**Effect**: A single corrupted episode JSON file no longer causes the entire project to crash in the lobby; the impact is contained to the status calculation fields for that episode.

---

## File Changes Summary

| File | Changes | Estimated Lines |
|------|---------|--------|
| `server/agent_runtime/session_manager.py` | Add `_build_json_validation_hook()` method; append to `hook_callbacks` in `_build_options()` | ~25 lines |
| `lib/status_calculator.py` | Add `json.JSONDecodeError` catch in `_load_episode_script()` | ~5 lines |

---

## Out of Scope

- **Dedicated JSON editing script (Option A)**: `settings.json` already has a reserved `edit-script-items` permission that can serve as a future enhancement; not in scope for this change
- **Log format improvements**: After the Layer 2 fix, the "Failed to load project metadata" error in `projects.py` should no longer be triggered by JSON errors; no additional changes needed
- **Frontend error handling**: This change focuses on the backend; the frontend already learns of project load failures via the `error` field
