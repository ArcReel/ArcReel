# 智能体文件类型防护 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 限制智能体 Write/Edit 只能操作 `.json`/.md`/.txt` 文件，防止在项目目录内创建代码文件（.py 等）；同时在 prompt 层约束职责边界。

**Architecture:** 在 `SessionManager._is_path_allowed` 中新增扩展名白名单检查，返回值从 `bool` 改为 `tuple[bool, str | None]` 以携带 deny 原因。调用方 `_build_file_access_hook` 使用该原因作为 deny 消息。Prompt 层在 `_PERSONA_PROMPT` 和 `agent_runtime_profile/CLAUDE.md` 中补充职责边界约束。

**Tech Stack:** Python, pytest, Claude Agent SDK hooks

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `server/agent_runtime/session_manager.py` | Modify | `_WRITABLE_EXTENSIONS` 白名单 + `_is_path_allowed` 返回值改造 + `_build_file_access_hook` deny 消息 + `_PERSONA_PROMPT` 追加 |
| `agent_runtime_profile/CLAUDE.md` | Modify | 新增"职责边界"小节 |
| `tests/test_session_manager_more.py` | Modify | 新增扩展名拦截测试用例 |

---

### Task 1: `_is_path_allowed` 返回值改造 + 扩展名白名单

**Files:**
- Modify: `server/agent_runtime/session_manager.py:254` (新增类属性)
- Modify: `server/agent_runtime/session_manager.py:1538-1591` (`_is_path_allowed` 方法)
- Modify: `server/agent_runtime/session_manager.py:498-526` (`_build_file_access_hook` 方法)
- Test: `tests/test_session_manager_more.py`

- [ ] **Step 1: 写扩展名拦截的失败测试**

在 `tests/test_session_manager_more.py` 的 `TestFileAccessHook` class 中，在 `test_file_access_hook_allows_bash_without_path_check` 之后添加：

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
Expected: FAIL — `.py` 文件的 Write 会返回 `{"continue_": True}` 而非 deny

- [ ] **Step 3: 新增 `_WRITABLE_EXTENSIONS` 类属性**

在 `server/agent_runtime/session_manager.py` 的 `_WRITE_TOOLS` 之后（L254）添加：

```python
_WRITE_TOOLS = {"Write", "Edit"}
_WRITABLE_EXTENSIONS = {".json", ".md", ".txt"}
```

- [ ] **Step 4: 改造 `_is_path_allowed` 返回值和逻辑**

将 `_is_path_allowed` 方法（L1538-1591）的返回值从 `bool` 改为 `tuple[bool, str | None]`：

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
        return False, "访问被拒绝：无效的文件路径"

    # 1. Within project directory
    if resolved.is_relative_to(project_cwd):
        if tool_name in self._WRITE_TOOLS:
            ext = resolved.suffix.lower()
            if ext not in self._WRITABLE_EXTENSIONS:
                return False, (
                    f"不允许创建/编辑 {ext} 类型的文件。"
                    "Write/Edit 仅限 .json、.md、.txt 文件。"
                    "如果你需要执行数据处理，请使用现有的 skill 脚本。"
                )
        return True, None

    # 2. Write tools: only project directory allowed
    if tool_name in self._WRITE_TOOLS:
        return False, "访问被拒绝：不允许访问当前项目目录之外的路径"

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

    return False, "访问被拒绝：不允许访问当前项目和公共目录之外的路径"
```

- [ ] **Step 5: 更新 `_build_file_access_hook` 使用 deny_reason**

将 `_build_file_access_hook` 中 L511-522 改为：

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

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_session_manager_more.py::TestFileAccessHook -v`
Expected: ALL PASS（新测试 + 所有已有测试）

- [ ] **Step 7: 运行完整测试套件**

Run: `uv run python -m pytest tests/test_session_manager_more.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "feat: Write/Edit 文件类型白名单（.json/.md/.txt）"
```

---

### Task 2: Prompt 层职责边界约束

**Files:**
- Modify: `server/agent_runtime/session_manager.py:317-327` (`_PERSONA_PROMPT`)
- Modify: `agent_runtime_profile/CLAUDE.md:76-79` (关键约束之后)

- [ ] **Step 1: 在 `_PERSONA_PROMPT` 行为准则中追加一条**

在 `server/agent_runtime/session_manager.py` 的 `_PERSONA_PROMPT`（L317-327）的行为准则列表末尾追加：

```python
_PERSONA_PROMPT = """\
## 身份

你是 ArcReel 智能体，一个专业的 AI 视频内容创作助手。你的职责是将小说转化为可发布的短视频内容。

## 行为准则

- 主动引导用户完成视频创作工作流，而不仅仅被动回答问题
- 遇到不确定的创作决策时，向用户提出选项并给出建议，而不是自行决定
- 涉及多步骤任务时，使用 TodoWrite 跟踪进度并向用户汇报
- 你不能创建或编辑代码文件（.py/.js/.sh 等），Write/Edit 仅限 .json/.md/.txt
- 你是用户的视频制作搭档，专业、友善、高效"""
```

- [ ] **Step 2: 在 `agent_runtime_profile/CLAUDE.md` 关键约束之后新增职责边界小节**

在 `agent_runtime_profile/CLAUDE.md` 的 `### 关键约束`（L73-79）之后、`## 可用 Skills` 之前插入：

```markdown
### 职责边界

- **禁止编写代码**：不得创建或修改任何代码文件（.py/.js/.sh 等），数据处理必须通过现有 skill 脚本完成
- **代码 bug 上报**：如果明确判断 skill 脚本出现的是代码 bug（而非参数或环境问题），向用户报告错误并建议反馈给开发者
```

- [ ] **Step 3: Commit**

```bash
git add server/agent_runtime/session_manager.py agent_runtime_profile/CLAUDE.md
git commit -m "feat: prompt 层智能体职责边界约束"
```

---

### Task 3: Lint + 全量测试

- [ ] **Step 1: 运行 ruff lint + format**

Run: `uv run ruff check server/agent_runtime/session_manager.py tests/test_session_manager_more.py && uv run ruff format server/agent_runtime/session_manager.py tests/test_session_manager_more.py`
Expected: 无 lint 错误

- [ ] **Step 2: 运行全量测试**

Run: `uv run python -m pytest -v`
Expected: ALL PASS
