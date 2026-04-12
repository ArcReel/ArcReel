# Agent File Type Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the agent's Write/Edit operations to only `.json`, `.md`, and `.txt` files, preventing creation of code files (`.py`, etc.) inside the project directory; also constrain responsibility boundaries at the prompt layer.

**Architecture:** Add an extension whitelist check in `SessionManager._is_path_allowed`, changing the return type from `bool` to `tuple[bool, str | None]` to carry a deny reason. The caller `_build_file_access_hook` uses that reason as the deny message. At the prompt layer, add responsibility boundary constraints to `_PERSONA_PROMPT` and `agent_runtime_profile/CLAUDE.md`.

**Tech Stack:** Python, pytest, Claude Agent SDK hooks

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `server/agent_runtime/session_manager.py` | Modify | `_WRITABLE_EXTENSIONS` whitelist + `_is_path_allowed` return value refactor + `_build_file_access_hook` deny message + `_PERSONA_PROMPT` append |
| `agent_runtime_profile/CLAUDE.md` | Modify | Add "Responsibility Boundaries" section |
| `tests/test_session_manager_more.py` | Modify | Add extension interception test cases |

---

### Task 1: `_is_path_allowed` Return Value Refactor + Extension Whitelist

**Files:**
- Modify: `server/agent_runtime/session_manager.py:254` (add class attribute)
- Modify: `server/agent_runtime/session_manager.py:1538-1591` (`_is_path_allowed` method)
- Modify: `server/agent_runtime/session_manager.py:498-526` (`_build_file_access_hook` method)
- Test: `tests/test_session_manager_more.py`

- [ ] **Step 1: Write failing tests for extension interception**

In the `TestFileAccessHook` class in `tests/test_session_manager_more.py`, add the following after `test_file_access_hook_allows_bash_without_path_check`:

```python
@pytest.mark.asyncio
async def test_file_access_hook_blocks_write_non_whitelisted_ext(self, tmp_path):
    """Hook denies Write/Edit for non-whitelisted file extensions in project dir."""
    own_project = tmp_path / "projects" / "alpha"
    own_project.mkdir(parents=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    meta_store = SessionMetaStore(session_factory=factory)

    mgr = sm_mod.SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path,
        meta_store=meta_store,
    )

    hook = mgr._build_file_access_hook(own_project)

    # Write .py in project dir — denied
    result = await hook(
        {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "helper.py")}},
        None,
        None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert ".json" in result["hookSpecificOutput"]["permissionDecisionReason"]

    # Edit .sh in project dir — denied
    result = await hook(
        {"tool_name": "Edit", "tool_input": {"file_path": str(own_project / "run.sh")}},
        None,
        None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Write .json — allowed
    result = await hook(
        {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "project.json")}},
        None,
        None,
    )
    assert result.get("continue_") is True

    # Write .md — allowed
    result = await hook(
        {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "notes.md")}},
        None,
        None,
    )
    assert result.get("continue_") is True

    # Write .txt — allowed
    result = await hook(
        {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "episode.txt")}},
        None,
        None,
    )
    assert result.get("continue_") is True

    # Read .py — allowed (only write is restricted)
    result = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(own_project / "helper.py")}},
        None,
        None,
    )
    assert result.get("continue_") is True

    await engine.dispose()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_session_manager_more.py::TestFileAccessHook::test_file_access_hook_blocks_write_non_whitelisted_ext -v`
Expected: FAIL — Write for `.py` files will return `{"continue_": True}` instead of deny

- [ ] **Step 3: Add `_WRITABLE_EXTENSIONS` class attribute**

After `_WRITE_TOOLS` (L254) in `server/agent_runtime/session_manager.py`, add:

```python
_WRITE_TOOLS = {"Write", "Edit"}
_WRITABLE_EXTENSIONS = {".json", ".md", ".txt"}
```

- [ ] **Step 4: Refactor `_is_path_allowed` return value and logic**

Change the return type of the `_is_path_allowed` method (L1538-1591) from `bool` to `tuple[bool, str | None]`:

```python
def _is_path_allowed(
    self,
    file_path: str,
    tool_name: str,
    project_cwd: Path,
) -> tuple[bool, str | None]:
    """Check if file_path is allowed for the given tool.

    Returns (allowed, deny_reason).  deny_reason is a human-readable
    message when allowed is False, None otherwise.

    Write tools: only project_cwd, restricted to _WRITABLE_EXTENSIONS.
    Read tools: project_cwd + project_root + SDK session dir for
    this project (sensitive files protected by settings.json deny rules).
    """
    try:
        p = Path(file_path)
        resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
    except (ValueError, OSError):
        return False, "Access denied: invalid file path"

    # 1. Within project directory
    if resolved.is_relative_to(project_cwd):
        if tool_name in self._WRITE_TOOLS:
            ext = resolved.suffix.lower()
            if ext not in self._WRITABLE_EXTENSIONS:
                return False, (
                    f"Creating/editing {ext} files is not allowed. "
                    "Write/Edit is restricted to .json, .md, and .txt files. "
                    "If you need to perform data processing, use the existing skill scripts."
                )
        return True, None

    # 2. Write tools: only project directory allowed
    if tool_name in self._WRITE_TOOLS:
        return False, "Access denied: paths outside the current project directory are not allowed"

    # 3. Read tools: allow entire project_root for shared resources
    #    Sensitive files protected by settings.json deny rules
    if resolved.is_relative_to(self.project_root):
        return True, None

    # 4. Read tools: allow SDK tool-results for THIS project only.
    encoded = self._encode_sdk_project_path(project_cwd)
    sdk_project_dir = self._CLAUDE_PROJECTS_DIR / encoded
    if resolved.is_relative_to(sdk_project_dir) and "tool-results" in resolved.parts:
        return True, None

    # 5. Read tools: allow SDK task output files.
    _SDK_TMP_PREFIXES = ("/tmp/claude-", "/private/tmp/claude-")
    resolved_str = str(resolved)
    if resolved_str.startswith(_SDK_TMP_PREFIXES) and "tasks" in resolved.parts:
        return True, None

    return False, "Access denied: paths outside the current project and shared directories are not allowed"
```

- [ ] **Step 5: Update `_build_file_access_hook` to use deny_reason**

Change L511-522 in `_build_file_access_hook` to:

```python
if file_path:
    allowed, deny_reason = self._is_path_allowed(
        file_path,
        tool_name,
        project_cwd,
    )
    if not allowed:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            },
        }
```

- [ ] **Step 6: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_session_manager_more.py::TestFileAccessHook -v`
Expected: ALL PASS (new test + all existing tests)

- [ ] **Step 7: Run full test suite**

Run: `uv run python -m pytest tests/test_session_manager_more.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "feat: Write/Edit file type whitelist (.json/.md/.txt)"
```

---

### Task 2: Prompt Layer Responsibility Boundary Constraints

**Files:**
- Modify: `server/agent_runtime/session_manager.py:317-327` (`_PERSONA_PROMPT`)
- Modify: `agent_runtime_profile/CLAUDE.md:76-79` (after key constraints)

- [ ] **Step 1: Append a rule to the behavior guidelines in `_PERSONA_PROMPT`**

Append to the end of the behavior guidelines list in `_PERSONA_PROMPT` (L317-327) in `server/agent_runtime/session_manager.py`:

```python
_PERSONA_PROMPT = """\
## Identity

You are the ArcReel agent, a professional AI video content creation assistant. Your responsibility is to convert novels into publishable short video content.

## Behavior Guidelines

- Proactively guide users through the video creation workflow rather than just passively answering questions
- When facing uncertain creative decisions, present options and make recommendations to the user instead of deciding unilaterally
- For multi-step tasks, use TodoWrite to track progress and report to the user
- You must not create or edit code files (.py/.js/.sh, etc.); Write/Edit is restricted to .json/.md/.txt
- You are the user's video production partner — professional, friendly, and efficient"""
```

- [ ] **Step 2: Add a Responsibility Boundaries section to `agent_runtime_profile/CLAUDE.md`**

Insert the following after `### Key Constraints` (L73-79) and before `## Available Skills` in `agent_runtime_profile/CLAUDE.md`:

```markdown
### Responsibility Boundaries

- **No code writing**: Do not create or modify any code files (.py/.js/.sh, etc.); data processing must be done through existing skill scripts
- **Report code bugs**: If you clearly determine that a skill script has a code bug (rather than a parameter or environment issue), report the error to the user and suggest they provide feedback to the developer
```

- [ ] **Step 3: Commit**

```bash
git add server/agent_runtime/session_manager.py agent_runtime_profile/CLAUDE.md
git commit -m "feat: prompt layer agent responsibility boundary constraints"
```

---

### Task 3: Lint + Full Test Suite

- [ ] **Step 1: Run ruff lint + format**

Run: `uv run ruff check server/agent_runtime/session_manager.py tests/test_session_manager_more.py && uv run ruff format server/agent_runtime/session_manager.py tests/test_session_manager_more.py`
Expected: No lint errors

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest -v`
Expected: ALL PASS
