# Permission Control Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate Agent Runtime permission control from custom Hook + Bash full-allowlist to SDK declarative rules + simplified Hook + Bash allowlist

**Architecture:** Create `settings.json` declarative permission rules, simplify `_is_path_allowed` logic (remove `_READONLY_DIRS` loop, allow reading the entire `project_root` instead), change `canUseTool` to default deny, remove Bash from `DEFAULT_ALLOWED_TOOLS`.

**Tech Stack:** Claude Agent SDK (Python), settings.json permission rules

**Design doc:** `docs/plans/2026-03-06-permission-optimization-design.md`

---

### Task 1: Create settings.json declarative permission rules

**Files:**
- Create: `agent_runtime_profile/.claude/settings.json`

**Step 1: Create settings.json**

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

**Step 2: Verify JSON format**

Run: `python -c "import json; json.load(open('agent_runtime_profile/.claude/settings.json'))"`
Expected: No output (no parse errors)

**Step 3: Commit**

```bash
git add agent_runtime_profile/.claude/settings.json
git commit -m "feat: add declarative permission rules for agent runtime"
```

---

### Task 2: Modify DEFAULT_ALLOWED_TOOLS to remove Bash

**Files:**
- Modify: `server/agent_runtime/session_manager.py:199-202`
- Test: `tests/test_session_manager_project_scope.py`

**Step 1: Write test — verify Bash is not in DEFAULT_ALLOWED_TOOLS**

In the `TestAllowedToolsAndConstants` class in `tests/test_session_manager_project_scope.py`,
modify the `test_default_allowed_tools_matches_sdk` method:

```python
@pytest.mark.asyncio
async def test_default_allowed_tools_matches_sdk(self, tmp_path):
    """Verify allowed tools align with SDK documentation."""
    store, engine = await _make_store()
    manager = SessionManager(
        project_root=tmp_path, data_dir=tmp_path, meta_store=store,
    )
    tools = manager.DEFAULT_ALLOWED_TOOLS
    assert "Task" in tools
    assert "Skill" in tools
    assert "Read" in tools
    assert "AskUserQuestion" in tools
    # Bash must NOT be in allowed_tools — controlled by settings.json allowlist
    assert "Bash" not in tools
    assert "MultiEdit" not in tools
    assert "LS" not in tools
    await engine.dispose()
```

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_session_manager_project_scope.py::TestAllowedToolsAndConstants::test_default_allowed_tools_matches_sdk -v`
Expected: FAIL — `assert "Bash" not in tools` fails

**Step 3: Modify DEFAULT_ALLOWED_TOOLS**

In `server/agent_runtime/session_manager.py:199-202`, change to:

```python
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Grep", "Glob", "AskUserQuestion",
]
```

Also update the comment (`session_manager.py:205-207`), replacing the old Bash comment:

```python
# Bash is NOT in DEFAULT_ALLOWED_TOOLS — it is controlled by declarative
# allow rules in settings.json (whitelist approach, default deny).
# File access control for Read/Write/Edit/Glob/Grep uses PreToolUse hooks.
```

**Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_session_manager_project_scope.py::TestAllowedToolsAndConstants -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "refactor: remove Bash from DEFAULT_ALLOWED_TOOLS, use settings.json whitelist"
```

---

### Task 3: Modify canUseTool callback to default deny

**Files:**
- Modify: `server/agent_runtime/session_manager.py:727-753`
- Test: `tests/test_session_manager_more.py:198-221`

**Step 1: Modify test — verify non-AskUserQuestion tools are denied**

In the `test_can_use_tool_callback_branches` method in `tests/test_session_manager_more.py`,
modify lines 203-206:

```python
@pytest.mark.asyncio
async def test_can_use_tool_callback_branches(self, session_manager, monkeypatch):
    monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
    monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

    allow_cb = await session_manager._build_can_use_tool_callback("unknown-session")
    # Non-AskUserQuestion tools should be denied (whitelist fallback)
    result = await allow_cb("Read", {"x": 1}, None)
    assert isinstance(result, _FakeDeny)
    assert "Unauthorized" in result.message
    # AskUserQuestion still handled
    result2 = await allow_cb("AskUserQuestion", {"questions": []}, None)
    assert result2.updated_input == {"questions": []}

    managed = ManagedSession(session_id="s1", client=FakeSDKClient(), status="running")
    session_manager.sessions["s1"] = managed
    ask_cb = await session_manager._build_can_use_tool_callback("s1")

    task = asyncio.create_task(ask_cb("AskUserQuestion", {"questions": [{"question": "Q"}]}, None))
    await asyncio.sleep(0)
    assert managed.pending_questions
    managed.cancel_pending_questions("user interrupted")
    deny = await task
    assert deny.interrupt is True
    assert "user interrupted" in deny.message
```

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_session_manager_more.py::TestSessionManagerMore::test_can_use_tool_callback_branches -v`
Expected: FAIL — `assert isinstance(result, _FakeDeny)` fails (currently returns _FakeAllow)

**Step 3: Modify canUseTool callback**

In `server/agent_runtime/session_manager.py`, modify the `_build_can_use_tool_callback` method
(lines 727-753):

```python
async def _build_can_use_tool_callback(self, session_id: str):
    """Create per-session can_use_tool callback.

    Handles AskUserQuestion (async user interaction) and denies all
    other unmatched tool calls (whitelist fallback).  File access
    control is handled by the PreToolUse hook; Bash whitelist by
    settings.json allow rules.
    """

    async def _can_use_tool(
        tool_name: str,
        input_data: dict[str, Any],
        _context: Any,
    ) -> Any:
        if PermissionResultAllow is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        normalized_tool = str(tool_name or "").strip().lower()

        if normalized_tool == "askuserquestion":
            return await self._handle_ask_user_question(
                session_id, tool_name, input_data,
            )

        # Whitelist fallback: deny any tool that was not pre-approved
        # by allowed_tools or settings.json allow rules.
        if PermissionResultDeny is not None:
            return PermissionResultDeny(message="Unauthorized tool call")
        return PermissionResultAllow(updated_input=input_data)

    return _can_use_tool
```

**Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_session_manager_more.py::TestSessionManagerMore::test_can_use_tool_callback_branches -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "feat: canUseTool defaults to deny for unmatched tools (whitelist fallback)"
```

---

### Task 4: Simplify _is_path_allowed and remove redundant code

**Files:**
- Modify: `server/agent_runtime/session_manager.py:216-220, 623-660, 698-725`
- Test: `tests/test_session_manager_project_scope.py:280-289, 287-431`

**Step 1: Modify tests — adapt to new path check logic**

In `tests/test_session_manager_project_scope.py`:

1. Delete `test_readonly_dirs_includes_agent_profile` (`_READONLY_DIRS` has been removed)

2. Modify `test_file_access_hook_blocks_read_outside_project`:
   Reading files under `other_project` should be allowed (because it is within `project_root`),
   but reading a completely external path should be denied.

```python
@pytest.mark.asyncio
async def test_file_access_hook_allows_read_within_project_root(self, tmp_path):
    """Hook allows Read for any path within project_root (e.g. other projects, docs)."""
    own_project = tmp_path / "projects" / "alpha"
    own_project.mkdir(parents=True)
    other_project = tmp_path / "projects" / "beta"
    other_project.mkdir(parents=True)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    meta_store = SessionMetaStore(session_factory=factory, _skip_init_db=True)

    mgr = sm_mod.SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path,
        meta_store=meta_store,
    )

    hook = mgr._build_file_access_hook(own_project)

    # Read own project file — allowed (within project_cwd)
    result = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(own_project / "script.json")}},
        None, None,
    )
    assert result.get("continue_") is True

    # Read other project file — allowed (within project_root)
    result = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(other_project / "script.json")}},
        None, None,
    )
    assert result.get("continue_") is True

    # Read docs dir — allowed (within project_root)
    result = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(docs_dir / "guide.md")}},
        None, None,
    )
    assert result.get("continue_") is True

    # Read outside project_root — denied
    result = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}},
        None, None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    await engine.dispose()
```

3. `test_file_access_hook_blocks_write_to_readonly_dir` — unchanged (Write to lib/ is still denied,
   because lib/ is within project_root but not within project_cwd)

4. `test_file_access_hook_allows_bash_without_path_check` — unchanged

5. `test_file_access_hook_allows_read_agent_profile` — unchanged (agent_runtime_profile is within project_root)

**Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_session_manager_project_scope.py -v`
Expected: FAIL — old test `test_readonly_dirs_includes_agent_profile` references the removed `_READONLY_DIRS`

**Step 3: Delete redundant code, simplify _is_path_allowed**

In `server/agent_runtime/session_manager.py`:

1. Delete `_READONLY_DIRS` and `_READONLY_FILES` (lines 216-220)

2. Simplify `_is_path_allowed` (lines 623-660):

```python
def _is_path_allowed(
    self,
    file_path: str,
    tool_name: str,
    project_cwd: Path,
) -> bool:
    """Check if file_path is allowed for the given tool.

    Write tools: only project_cwd.
    Read tools: project_cwd + entire project_root (sensitive files
    protected by settings.json deny rules).
    """
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

3. Delete `_deny_path_access` method (lines 698-707)

4. Delete `_check_file_access` method (lines 709-725)

**Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_session_manager_project_scope.py tests/test_session_manager_more.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "refactor: simplify file access hook, remove _READONLY_DIRS in favor of settings.json"
```

---

### Task 5: Run full test suite

**Step 1: Run all tests**

Run: `python -m pytest -v`
Expected: ALL PASS

**Step 2: If there are failures, fix and re-run**

---

### Task 6: Final commit and cleanup

**Step 1: Verify git status is clean**

Run: `git status`
Expected: No uncommitted changes

**Step 2: Verify change summary**

Run: `git log --oneline -5`
Expected: 4 new commits:
1. `feat: add declarative permission rules for agent runtime`
2. `refactor: remove Bash from DEFAULT_ALLOWED_TOOLS, use settings.json whitelist`
3. `feat: canUseTool defaults to deny for unmatched tools (whitelist fallback)`
4. `refactor: simplify file access hook, remove _READONLY_DIRS in favor of settings.json`
