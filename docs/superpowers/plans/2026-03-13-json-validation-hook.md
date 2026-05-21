# JSON 写入验证 Hook 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 防止 Agent 的 Edit/Write 操作损坏 JSON 文件并级联崩溃项目大厅。

**Architecture:** 两层防御：Layer 1 在 Agent 完成写文件后用 `PostToolUse` hook 验证 JSON 合法性，失败时通过 `systemMessage` 通知 Agent 自我修复；Layer 2 在 `StatusCalculator._load_episode_script` 补充捕获 `json.JSONDecodeError`，防止单集文件损坏级联到项目级 API。

**Tech Stack:** Python `json` 标准库、Claude Agent SDK `PostToolUse` hook、pytest + `_FakePM` 测试模式（同 `tests/test_status_calculator.py`）

---

## Task 1：`StatusCalculator._load_episode_script` 防御性修复

**Files:**
- Modify: `lib/status_calculator.py:93-107`
- Test: `tests/test_status_calculator.py`

### Step 1：在现有测试文件末尾写失败测试

在 `tests/test_status_calculator.py` 的 `TestStatusCalculator` 类末尾添加：

```python
def test_load_episode_script_corrupted_json(self, tmp_path):
    """JSON 损坏时应降级返回 ('generated', None)，而不是上抛异常。"""
    import json

    class _CorruptPM(_FakePM):
        def load_script(self, project_name, filename):
            raise json.JSONDecodeError("Expecting value", "doc", 0)

    calc = StatusCalculator(_CorruptPM(tmp_path / "projects", {}, {}))
    status, script = calc._load_episode_script("demo", 1, "scripts/episode_1.json")
    assert status == "generated"
    assert script is None
```

### Step 2：运行确认测试失败

```bash
uv run pytest tests/test_status_calculator.py::TestStatusCalculator::test_load_episode_script_corrupted_json -v
```

预期：`FAILED` — `json.JSONDecodeError` 未被捕获，上抛报错。

### Step 3：修复 `_load_episode_script`

定位 `lib/status_calculator.py:97-107`，在 `except FileNotFoundError:` 块后追加新的 except：

```python
    def _load_episode_script(self, project_name: str, episode_num: int, script_file: str) -> tuple:
        """加载单集剧本，返回 (script_status, script|None)，避免重复读取文件。
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
                "剧本 JSON 损坏，跳过状态计算 project=%s file=%s: %s",
                project_name, script_file, e,
            )
            return 'generated', None
```

> **注意**：确认文件顶部已 `import json`（全局搜索 `import json` 确认）。

### Step 4：运行确认测试通过

```bash
uv run pytest tests/test_status_calculator.py -v
```

预期：所有测试 PASS。

### Step 5：提交

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "fix(status): catch JSONDecodeError in _load_episode_script to prevent cascade failure"
```

---

## Task 2：`PostToolUse` JSON 验证 Hook

**Files:**
- Modify: `server/agent_runtime/session_manager.py`
- Test: `tests/test_session_manager_more.py`（追加）

### Step 1：写失败测试

在 `tests/test_session_manager_more.py` 末尾追加（注意该文件已有 `import asyncio`）：

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
        assert "无效 JSON" in result["systemMessage"] or "invalid" in result["systemMessage"].lower()

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

### Step 2：运行确认测试失败

```bash
uv run pytest tests/test_session_manager_more.py::TestJsonValidationHook -v
```

预期：`FAILED` — `AttributeError: 'SessionManager' object has no attribute '_build_json_validation_hook'`

### Step 3：实现 `_build_json_validation_hook`

在 `session_manager.py` 的 `_build_file_access_hook` 方法之后（约 408 行），添加新方法：

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
                "Agent 写入了无效 JSON file=%s error=%s",
                file_path, exc,
            )
            return {
                "systemMessage": (
                    f"⚠️ 警告：你刚才操作的文件 {file_path} 现在包含无效 JSON。"
                    f"错误：{exc}。"
                    "请立即用 Read 工具读取该文件，定位问题（例如多余的逗号 ,, "
                    "或缺少引号），然后用 Edit 工具修复，确保文件是合法 JSON 后再继续。"
                )
            }

    return _json_validation_hook
```

确保文件顶部已有 `import json`（搜索确认，若无则在 `import os` 附近添加）。

### Step 4：在 `_build_options` 中注册 PostToolUse hook

定位 `_build_options` 方法中 `hooks` 字典（约 381 行），修改为：

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

### Step 5：运行确认测试通过

```bash
uv run pytest tests/test_session_manager_more.py::TestJsonValidationHook -v
```

预期：5 个测试全部 PASS。

### Step 6：运行全量测试确认无回归

```bash
uv run pytest --tb=short -q
```

预期：全部 PASS（498 个 + 新增 6 个）。

### Step 7：提交

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "feat(agent): add PostToolUse JSON validation hook to self-correct invalid edits"
```

---

## Task 3：端到端验证

### Step 1：手动验证级联失败已修复

```bash
# 模拟损坏文件场景：在测试中确认 calculate_project_status 不再上抛
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

预期：打印 `OK, phase = scripting`（或 `production`），无异常。

### Step 2：确认日志中不再出现误导性"元数据失败"

检查 `calculate_project_status` 调用链（`routers/projects.py:220`）：在 Task 1 修复后，`json.JSONDecodeError` 在 `_load_episode_script` 内部被捕获，不会再上抛到 `list_projects` 的宽泛 `except`，从而消除 "加载项目元数据失败" 的误导日志。

### Step 3：最终提交确认

```bash
git log --oneline -5
```

预期看到两个 fix commit：
```
feat(agent): add PostToolUse JSON validation hook to self-correct invalid edits
fix(status): catch JSONDecodeError in _load_episode_script to prevent cascade failure
docs: 新增 JSON 验证 hook 设计文档
```
