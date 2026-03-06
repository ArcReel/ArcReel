# 权限控制优化实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Agent Runtime 权限控制从自定义 Hook + Bash 全放行迁移到 SDK 声明式规则 + 简化 Hook + Bash 白名单

**Architecture:** 创建 `settings.json` 声明式权限规则，简化 `_is_path_allowed` 逻辑（删除 `_READONLY_DIRS` 循环，改为允许整个 `project_root` 的读取），修改 `canUseTool` 为默认拒绝，从 `DEFAULT_ALLOWED_TOOLS` 移除 Bash。

**Tech Stack:** Claude Agent SDK (Python), settings.json 权限规则

**设计文档:** `docs/plans/2026-03-06-permission-optimization-design.md`

---

### Task 1: 创建 settings.json 声明式权限规则

**Files:**
- Create: `agent_runtime_profile/.claude/settings.json`

**Step 1: 创建 settings.json**

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

**Step 2: 验证 JSON 格式**

Run: `python -c "import json; json.load(open('agent_runtime_profile/.claude/settings.json'))"`
Expected: 无输出（无解析错误）

**Step 3: Commit**

```bash
git add agent_runtime_profile/.claude/settings.json
git commit -m "feat: add declarative permission rules for agent runtime"
```

---

### Task 2: 修改 DEFAULT_ALLOWED_TOOLS 移除 Bash

**Files:**
- Modify: `server/agent_runtime/session_manager.py:199-202`
- Test: `tests/test_session_manager_project_scope.py`

**Step 1: 编写测试 — 验证 Bash 不在 DEFAULT_ALLOWED_TOOLS 中**

在 `tests/test_session_manager_project_scope.py` 的 `TestAllowedToolsAndConstants` 类中，
修改 `test_default_allowed_tools_matches_sdk` 方法：

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
    # Bash must NOT be in allowed_tools — controlled by settings.json whitelist
    assert "Bash" not in tools
    assert "MultiEdit" not in tools
    assert "LS" not in tools
    await engine.dispose()
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_session_manager_project_scope.py::TestAllowedToolsAndConstants::test_default_allowed_tools_matches_sdk -v`
Expected: FAIL — `assert "Bash" not in tools` 失败

**Step 3: 修改 DEFAULT_ALLOWED_TOOLS**

在 `server/agent_runtime/session_manager.py:199-202`，修改：

```python
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Grep", "Glob", "AskUserQuestion",
]
```

同时更新注释（`session_manager.py:205-207`），替换旧的 Bash 注释：

```python
# Bash is NOT in DEFAULT_ALLOWED_TOOLS — it is controlled by declarative
# allow rules in settings.json (whitelist approach, default deny).
# File access control for Read/Write/Edit/Glob/Grep uses PreToolUse hooks.
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_session_manager_project_scope.py::TestAllowedToolsAndConstants -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "refactor: remove Bash from DEFAULT_ALLOWED_TOOLS, use settings.json whitelist"
```

---

### Task 3: 修改 canUseTool 回调为默认拒绝

**Files:**
- Modify: `server/agent_runtime/session_manager.py:727-753`
- Test: `tests/test_session_manager_more.py:198-221`

**Step 1: 修改测试 — 验证非 AskUserQuestion 工具被拒绝**

在 `tests/test_session_manager_more.py` 的 `test_can_use_tool_callback_branches` 方法中，
修改第 203-206 行：

```python
@pytest.mark.asyncio
async def test_can_use_tool_callback_branches(self, session_manager, monkeypatch):
    monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
    monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

    allow_cb = await session_manager._build_can_use_tool_callback("unknown-session")
    # Non-AskUserQuestion tools should be denied (whitelist fallback)
    result = await allow_cb("Read", {"x": 1}, None)
    assert isinstance(result, _FakeDeny)
    assert "未授权" in result.message
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

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_session_manager_more.py::TestSessionManagerMore::test_can_use_tool_callback_branches -v`
Expected: FAIL — `assert isinstance(result, _FakeDeny)` 失败（当前返回 _FakeAllow）

**Step 3: 修改 canUseTool 回调**

在 `server/agent_runtime/session_manager.py`，修改 `_build_can_use_tool_callback` 方法
（第 727-753 行）：

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
            return PermissionResultDeny(message="未授权的工具调用")
        return PermissionResultAllow(updated_input=input_data)

    return _can_use_tool
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_session_manager_more.py::TestSessionManagerMore::test_can_use_tool_callback_branches -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "feat: canUseTool defaults to deny for unmatched tools (whitelist fallback)"
```

---

### Task 4: 简化 _is_path_allowed 并删除冗余代码

**Files:**
- Modify: `server/agent_runtime/session_manager.py:216-220, 623-660, 698-725`
- Test: `tests/test_session_manager_project_scope.py:280-289, 287-431`

**Step 1: 修改测试 — 适配新的路径检查逻辑**

在 `tests/test_session_manager_project_scope.py` 中：

1. 删除 `test_readonly_dirs_includes_agent_profile`（`_READONLY_DIRS` 已删除）

2. 修改 `test_file_access_hook_blocks_read_outside_project`：
   读取 `other_project` 下的文件应该被允许（因为在 `project_root` 内），
   但读取完全外部的路径应该被拒绝。

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

3. `test_file_access_hook_blocks_write_to_readonly_dir` — 保持不变（Write 到 lib/ 仍被拒绝，
   因为 lib/ 在 project_root 内但不在 project_cwd 内）

4. `test_file_access_hook_allows_bash_without_path_check` — 保持不变

5. `test_file_access_hook_allows_read_agent_profile` — 保持不变（agent_runtime_profile 在 project_root 内）

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_session_manager_project_scope.py -v`
Expected: FAIL — 旧测试 `test_readonly_dirs_includes_agent_profile` 引用已删除的 `_READONLY_DIRS`

**Step 3: 删除冗余代码，简化 _is_path_allowed**

在 `server/agent_runtime/session_manager.py` 中：

1. 删除 `_READONLY_DIRS` 和 `_READONLY_FILES`（第 216-220 行）

2. 简化 `_is_path_allowed`（第 623-660 行）：

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

3. 删除 `_deny_path_access` 方法（第 698-707 行）

4. 删除 `_check_file_access` 方法（第 709-725 行）

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_session_manager_project_scope.py tests/test_session_manager_more.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_project_scope.py
git commit -m "refactor: simplify file access hook, remove _READONLY_DIRS in favor of settings.json"
```

---

### Task 5: 运行完整测试套件

**Step 1: 运行全部测试**

Run: `python -m pytest -v`
Expected: ALL PASS

**Step 2: 如有失败，修复并重新运行**

---

### Task 6: 最终提交和清理

**Step 1: 验证 git 状态干净**

Run: `git status`
Expected: 无未提交的修改

**Step 2: 验证变更摘要**

Run: `git log --oneline -5`
Expected: 4 个新 commit:
1. `feat: add declarative permission rules for agent runtime`
2. `refactor: remove Bash from DEFAULT_ALLOWED_TOOLS, use settings.json whitelist`
3. `feat: canUseTool defaults to deny for unmatched tools (whitelist fallback)`
4. `refactor: simplify file access hook, remove _READONLY_DIRS in favor of settings.json`
