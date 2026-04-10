# JSON Write Validation Hook Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent the Agent's Edit/Write operations from corrupting JSON files and causing cascade crashes in the project lobby.

**Architecture:** Two defensive layers: Layer 1 uses a `PostToolUse` hook to validate JSON validity after the Agent finishes writing a file, notifying the Agent via `systemMessage` to self-repair on failure; Layer 2 adds a `json.JSONDecodeError` catch in `StatusCalculator._load_episode_script` to prevent a single episode file corruption from cascading to the project-level API.

**Tech Stack:** Python `json` standard library, Claude Agent SDK `PostToolUse` hook, pytest + `_FakePM` test pattern (same as `tests/test_status_calculator.py`)

---

## Task 1: `StatusCalculator._load_episode_script` Defensive Fix

**Files:**
- Modify: `lib/status_calculator.py:93-107`
- Test: `tests/test_status_calculator.py`

### Step 1: Write a Failing Test at the End of the Existing Test File

Append to the end of the `TestStatusCalculator` class in `tests/test_status_calculator.py`:

```python
def test_load_episode_script_corrupted_json(self, tmp_path):
    """On JSON corruption, should degrade and return ('generated', None) instead of raising an exception."""
    import json

    class _CorruptPM(_FakePM):
        def load_script(self, project_name, filename):
            raise json.JSONDecodeError("Expecting value", "doc", 0)

    calc = StatusCalculator(_CorruptPM(tmp_path / "projects", {}, {}))
    status, script = calc._load_episode_script("demo", 1, "scripts/episode_1.json")
    assert status == "generated"
    assert script is None
```

### Step 2: Run to Confirm Test Fails

```bash
uv run pytest tests/test_status_calculator.py::TestStatusCalculator::test_load_episode_script_corrupted_json -v
```

Expected: `FAILED` — `json.JSONDecodeError` not caught, exception propagates up.

### Step 3: Fix `_load_episode_script`

Locate `lib/status_calculator.py:97-107` and append a new except block after `except FileNotFoundError:`:

```python
    def _load_episode_script(self, project_name: str, episode_num: int, script_file: str) -> tuple:
        """Load a single episode script, returning (script_status, script|None) to avoid repeated file reads.
        script_status: 'generated' | 'segmented' | 'none'
        """
        try:
            script = self.pm.load_script(project_name, script_file)
            return 'generated', script
        except FileNotFoundError:
            project_dir = self.pm.get_project_path(project_name)
            try:
                safe_num = int(episode_num)
            except (ValueError, TypeError):
                return 'none', None
            draft_file = project_dir / f'drafts/episode_{safe_num}/step1_segments.md'
            return ('segmented' if draft_file.exists() else 'none'), None
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "Script JSON corrupted, skipping status calculation project=%s file=%s: %s",
                project_name, script_file, e,
            )
            return 'generated', None
```

> **Note**: Confirm that `import json` is already present at the top of the file (search globally for `import json` to verify).

### Step 4: Run to Confirm Tests Pass

```bash
uv run pytest tests/test_status_calculator.py -v
```

Expected: all tests PASS.

### Step 5: Commit

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "fix(status): catch JSONDecodeError in _load_episode_script to prevent cascade failure"
```

---

## Task 2: `PostToolUse` JSON Validation Hook

**Files:**
- Modify: `server/agent_runtime/session_manager.py`
- Test: `tests/test_session_manager_more.py` (append)

### Step 1: Write Failing Tests

Append to the end of `tests/test_session_manager_more.py` (note: the file already has `import asyncio`):

```python
class TestJsonValidationHook:
    """Tests for the PostToolUse JSON validation hook."""

    def _make_manager(self, tmp_path):
        """Build a SessionManager with minimal fakes (SDK not required)."""
        from server.agent_runtime.session_manager import SessionManager
        from server.agent_runtime.session_store import SessionMetaStore
        return SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path / "data",
            meta_store=SessionMetaStore(),
        )

    async def _call_hook(self, manager, file_path: str, tool_name: str = "Edit"):
        """Helper: invoke the JSON validation hook callback directly."""
        hook_fn = manager._build_json_validation_hook()
        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
        }
        return await hook_fn(input_data, tool_use_id=None, context=None)

    async def test_valid_json_returns_empty(self, tmp_path):
        """Hook returns {} for valid JSON — no systemMessage injected."""
        json_file = tmp_path / "episode_1.json"
        json_file.write_text('{"segments": []}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(manager, str(json_file))
        assert result == {}

    async def test_invalid_json_injects_system_message(self, tmp_path):
        """Hook returns systemMessage when JSON is invalid."""
        json_file = tmp_path / "episode_2.json"
        json_file.write_text('{"a": 1,,}')  # double comma
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(manager, str(json_file))
        assert "systemMessage" in result
        assert str(json_file) in result["systemMessage"]
        assert "invalid JSON" in result["systemMessage"] or "invalid" in result["systemMessage"].lower()

    async def test_non_json_file_returns_empty(self, tmp_path):
        """Hook ignores non-.json files."""
        md_file = tmp_path / "notes.md"
        md_file.write_text("not json at all {{{{")
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(manager, str(md_file))
        assert result == {}

    async def test_missing_file_returns_empty(self, tmp_path):
        """Hook silently skips if the file doesn't exist."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(manager, str(tmp_path / "ghost.json"))
        assert result == {}

    async def test_non_write_tool_returns_empty(self, tmp_path):
        """Hook ignores tools other than Write/Edit (e.g. Bash)."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(manager, "/some/file.json", tool_name="Bash")
        assert result == {}
```

### Step 2: Run to Confirm Test Fails

```bash
uv run pytest tests/test_session_manager_more.py::TestJsonValidationHook -v
```

Expected: `FAILED` — `AttributeError: 'SessionManager' object has no attribute '_build_json_validation_hook'`

### Step 3: Implement `_build_json_validation_hook`

In `session_manager.py`, add the new method after `_build_file_access_hook` (around line 408):

```python
def _build_json_validation_hook(self) -> Callable[..., Any]:
    """Build a PostToolUse hook that validates JSON files after Write/Edit.

    When Edit or Write produces an invalid JSON file, injects a systemMessage
    so the agent immediately knows to read and fix the file.
    """

    async def _json_validation_hook(
        input_data: dict[str, Any],
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Write", "Edit"):
            return {}

        file_path = input_data.get("tool_input", {}).get("file_path", "")
        if not file_path or not file_path.endswith(".json"):
            return {}

        try:
            content = Path(file_path).read_text(encoding="utf-8")
            json.loads(content)
            return {}
        except (FileNotFoundError, PermissionError, OSError):
            return {}
        except json.JSONDecodeError as exc:
            logger.warning(
                "Agent wrote invalid JSON file=%s error=%s",
                file_path, exc,
            )
            return {
                "systemMessage": (
                    f"⚠️ Warning: the file {file_path} you just operated on now contains invalid JSON. "
                    f"Error: {exc}. "
                    "Please immediately use the Read tool to read that file, locate the issue (e.g., extra commas ,, "
                    "or missing quotes), then use the Edit tool to fix it, and ensure the file is valid JSON before continuing."
                )
            }

    return _json_validation_hook
```

Ensure `import json` is already present at the top of the file (search to confirm; if not, add it near `import os`).

### Step 4: Register the PostToolUse Hook in `_build_options`

Locate the `hooks` dictionary in the `_build_options` method (around line 381) and update to:

```python
        hooks = None
        if HookMatcher is not None:
            hook_callbacks: list[Any] = [
                self._build_file_access_hook(project_cwd),
            ]
            if can_use_tool is not None:
                hook_callbacks.insert(0, self._keep_stream_open_hook)
            hooks = {
                "PreToolUse": [
                    HookMatcher(matcher=None, hooks=hook_callbacks),
                ],
                "PostToolUse": [
                    HookMatcher(matcher="Write|Edit", hooks=[self._build_json_validation_hook()]),
                ],
            }
```

### Step 5: Run to Confirm Tests Pass

```bash
uv run pytest tests/test_session_manager_more.py::TestJsonValidationHook -v
```

Expected: all 5 tests PASS.

### Step 6: Run Full Test Suite to Confirm No Regressions

```bash
uv run pytest --tb=short -q
```

Expected: all PASS (498 existing + 6 new).

### Step 7: Commit

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "feat(agent): add PostToolUse JSON validation hook to self-correct invalid edits"
```

---

## Task 3: End-to-End Verification

### Step 1: Manually Verify Cascade Failure Is Fixed

```bash
# Simulate a corrupted file scenario: confirm calculate_project_status no longer raises
uv run python -c "
import json, tempfile, pathlib
from lib.status_calculator import StatusCalculator

class FakePM:
    def __init__(self, root):
        self._root = pathlib.Path(root)
    def get_project_path(self, name):
        return self._root / name
    def load_script(self, name, f):
        raise json.JSONDecodeError('bad', 'doc', 0)

with tempfile.TemporaryDirectory() as d:
    pm = FakePM(d)
    calc = StatusCalculator(pm)
    project = {
        'overview': {'synopsis': 'test'},
        'episodes': [{'episode': 1, 'script_file': 'scripts/episode_1.json'}],
        'characters': {}, 'clues': {},
    }
    # Should NOT raise, should degrade gracefully
    result = calc.calculate_project_status('demo', project)
    print('OK, phase =', result.get('current_phase'))
"
```

Expected: prints `OK, phase = scripting` (or `production`) with no exception.

### Step 2: Confirm No More Misleading "Metadata Failure" Logs

Check the `calculate_project_status` call chain (`routers/projects.py:220`): after the Task 1 fix, `json.JSONDecodeError` is caught inside `_load_episode_script` and will no longer propagate to the broad `except` in `list_projects`, eliminating the misleading "Failed to load project metadata" log.

### Step 3: Final Commit Verification

```bash
git log --oneline -5
```

Expected: two fix commits visible:
```
feat(agent): add PostToolUse JSON validation hook to self-correct invalid edits
fix(status): catch JSONDecodeError in _load_episode_script to prevent cascade failure
docs: add JSON validation hook design document
```
