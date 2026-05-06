# SDK 0.1.73 eager session_store_flush 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 升级 `claude-agent-sdk` 到 0.1.73 + 默认 `session_store_flush="eager"`；附带 `_is_buffer_duplicate` 兜底增强（echo 与同 buffer 内 sdk UserMessage 去重），解决 R1 双显 / "user 消失" / 服务重启 partial 丢失三类问题。

**Architecture:** `ClaudeAgentOptions` 接受 `session_store_flush`，由 `lib.agent_session_store.session_store_flush_mode()` 解析 env 决定取值；`AssistantService._build_projector` 对 buffer 做 pre-scan，把 buffer 内真实 user 文本传给 `_is_buffer_duplicate` 作为 echo dedup 兜底路径（覆盖 DB 慢于 buffer 的窗口）。

**Tech Stack:** Python 3.12, claude-agent-sdk 0.1.73, FastAPI, SQLAlchemy async, pytest, uv

**Spec:** `docs/superpowers/specs/2026-05-06-sdk-eager-flush-design.md`

---

## File Structure

**Modify**:
- `pyproject.toml` — 升级 SDK 约束
- `uv.lock` — `uv lock --upgrade-package claude-agent-sdk` 生成
- `lib/agent_session_store/__init__.py` — 新增 `session_store_flush_mode()` + 导出
- `server/agent_runtime/session_manager.py` — `_build_options` 透传 `session_store_flush`
- `server/agent_runtime/service.py` — 新增 `_collect_buffer_real_user_texts`；`_is_buffer_duplicate` 加参数 + buffer 兜底分支；`_build_projector` 调用 pre-scan

**Create**:
- `tests/agent_session_store/test_flush_mode.py` — env 解析单测
- `tests/agent_runtime/test_dedup_user_echo.py` — R1 / "user 消失" 回归

**Modify (tests)**:
- `tests/agent_runtime/test_session_manager_store_injection.py` — flush 模式透传到 `ClaudeAgentOptions`
- `tests/agent_runtime/test_session_store_e2e.py` — crash durability + eager 多次 append 独立性

---

## Task 1: 升级 claude-agent-sdk 到 0.1.73

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: 收紧版本约束**

把 `pyproject.toml` 中：

```toml
"claude-agent-sdk>=0.1.59",
```

改为：

```toml
"claude-agent-sdk>=0.1.73",
```

- [ ] **Step 2: 重生成 lock**

Run: `uv lock --upgrade-package claude-agent-sdk`
Expected: `uv.lock` 中 `claude-agent-sdk` 版本变为 `0.1.73`（或更高）

- [ ] **Step 3: 同步虚拟环境**

Run: `uv sync`
Expected: 安装 `claude-agent-sdk 0.1.73`，无错误

- [ ] **Step 4: 验证 ClaudeAgentOptions 接受 session_store_flush**

Run:
```bash
uv run python -c "from claude_agent_sdk import ClaudeAgentOptions; o = ClaudeAgentOptions(session_store_flush='eager'); print(o.session_store_flush)"
```
Expected: 输出 `eager`，无 `TypeError` / `AttributeError`

- [ ] **Step 5: 跑现有 agent_runtime 测试确保升级未破坏**

Run: `uv run python -m pytest tests/agent_runtime/ tests/agent_session_store/ -v`
Expected: 全绿

- [ ] **Step 6: commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): bump claude-agent-sdk to 0.1.73 for session_store_flush"
```

---

## Task 2: 新增 session_store_flush_mode() env 解析器

**Files:**
- Modify: `lib/agent_session_store/__init__.py`
- Test: `tests/agent_session_store/test_flush_mode.py` (new)

- [ ] **Step 1: 写 failing test**

Create `tests/agent_session_store/test_flush_mode.py`:
```python
"""ARCREEL_SDK_SESSION_STORE_FLUSH env parser."""

from __future__ import annotations

import logging

from lib.agent_session_store import session_store_flush_mode


def test_default_is_eager(monkeypatch):
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE_FLUSH", raising=False)
    assert session_store_flush_mode() == "eager"


def test_explicit_batched(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "batched")
    assert session_store_flush_mode() == "batched"


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "Batched")
    assert session_store_flush_mode() == "batched"


def test_unknown_falls_back_to_eager_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "weird")
    with caplog.at_level(logging.WARNING, logger="arcreel.session_store"):
        assert session_store_flush_mode() == "eager"
    assert any(
        "ARCREEL_SDK_SESSION_STORE_FLUSH" in rec.message for rec in caplog.records
    )


def test_empty_treated_as_eager(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "")
    assert session_store_flush_mode() == "eager"


def test_eager_explicit(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "eager")
    assert session_store_flush_mode() == "eager"
```

- [ ] **Step 2: 跑测试，验证失败**

Run: `uv run python -m pytest tests/agent_session_store/test_flush_mode.py -v`
Expected: `ImportError` 或 `cannot import name 'session_store_flush_mode'`

- [ ] **Step 3: 实现解析器**

Edit `lib/agent_session_store/__init__.py`：

1. 在文件顶端 import 段加入 `import logging`（紧接现有 `import os`）
1. 紧接现有 `_VALID_MODES = frozenset({"db", "off", ""})` 之后添加：

```python
logger = logging.getLogger("arcreel.session_store")

_FLUSH_ENV_VAR = "ARCREEL_SDK_SESSION_STORE_FLUSH"
_VALID_FLUSH_MODES = frozenset({"eager", "batched"})
```

1. 在文件末尾 `__all__` 之前新增函数：

```python
def session_store_flush_mode() -> str:
    """Return SDK ClaudeAgentOptions.session_store_flush value.

    Defaults to "eager" so transcript writes are durable across crashes
    and visible mid-turn for reconnect snapshots. Set
    ARCREEL_SDK_SESSION_STORE_FLUSH=batched for the legacy end-of-turn
    flush behavior (rollback path).
    """
    raw = os.getenv(_FLUSH_ENV_VAR, "").strip().lower()
    if raw == "batched":
        return "batched"
    if raw and raw not in _VALID_FLUSH_MODES:
        logger.warning(
            "Unknown %s=%r; defaulting to eager",
            _FLUSH_ENV_VAR,
            raw,
        )
    return "eager"
```

1. 把 `session_store_flush_mode` 加入 `__all__`，最终 `__all__` 改为：

```python
__all__ = [
    "AgentSessionEntry",
    "AgentSessionSummary",
    "is_known_session_store_mode",
    "make_project_key",
    "session_store_enabled",
    "session_store_flush_mode",
    "session_store_mode",
]
```

- [ ] **Step 4: 跑测试，验证通过**

Run: `uv run python -m pytest tests/agent_session_store/test_flush_mode.py -v`
Expected: 6 个全绿

- [ ] **Step 5: lint**

Run: `uv run ruff check lib/agent_session_store/__init__.py tests/agent_session_store/test_flush_mode.py && uv run ruff format lib/agent_session_store/__init__.py tests/agent_session_store/test_flush_mode.py`
Expected: 无 issue

- [ ] **Step 6: commit**

```bash
git add lib/agent_session_store/__init__.py tests/agent_session_store/test_flush_mode.py
git commit -m "feat(session-store): add session_store_flush_mode env parser"
```

---

## Task 3: `_build_options` 透传 session_store_flush

**Files:**
- Modify: `server/agent_runtime/session_manager.py`
- Modify: `tests/agent_runtime/test_session_manager_store_injection.py`

- [ ] **Step 1: 写 failing test**

在 `tests/agent_runtime/test_session_manager_store_injection.py` 末尾追加：
```python
def test_flush_mode_passed_to_options_default(monkeypatch, tmp_path):
    """No env → ClaudeAgentOptions.session_store_flush == 'eager'."""
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE_FLUSH", raising=False)
    sm = _build_sm(tmp_path)

    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True)

    options = sm._build_options(project_name="demo")
    assert options.session_store_flush == "eager"


def test_flush_mode_passed_to_options_batched(monkeypatch, tmp_path):
    """env=batched → options.session_store_flush == 'batched'."""
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "batched")
    sm = _build_sm(tmp_path)
    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True)

    options = sm._build_options(project_name="demo")
    assert options.session_store_flush == "batched"
```

- [ ] **Step 2: 跑测试，验证失败**

Run: `uv run python -m pytest tests/agent_runtime/test_session_manager_store_injection.py::test_flush_mode_passed_to_options_default -v`
Expected: `AssertionError` 或 `AttributeError`（默认尚未传 flush 字段）

- [ ] **Step 3: 改 `_build_options`**

Edit `server/agent_runtime/session_manager.py`：

1. 在 `from lib.agent_session_store import ...` 已有 import 处补上 `session_store_flush_mode`（如果该处尚未 import 这个模块，则新增 `from lib.agent_session_store import session_store_flush_mode`）
2. 找到 `_build_options` 末尾的 `return ClaudeAgentOptions(...)` 调用（约 565 行起），在 `session_store=self._build_session_store(),` 这一行之后加：
```python
        session_store_flush=session_store_flush_mode(),
```

最终该 return 块形如：
```python
return ClaudeAgentOptions(
    cwd=str(project_cwd),
    setting_sources=self.DEFAULT_SETTING_SOURCES,
    allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
    max_turns=self.max_turns,
    system_prompt=SystemPromptPreset(...),
    include_partial_messages=True,
    resume=resume_id,
    can_use_tool=can_use_tool,
    hooks=hooks,
    session_store=self._build_session_store(),
    session_store_flush=session_store_flush_mode(),
)
```

- [ ] **Step 4: 跑测试，验证通过**

Run: `uv run python -m pytest tests/agent_runtime/test_session_manager_store_injection.py -v`
Expected: 全绿（4 已有 + 2 新增）

- [ ] **Step 5: lint**

Run: `uv run ruff check server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_store_injection.py && uv run ruff format server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_store_injection.py`
Expected: 无 issue

- [ ] **Step 6: commit**

```bash
git add server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_store_injection.py
git commit -m "feat(agent-runtime): propagate session_store_flush to ClaudeAgentOptions"
```

---

## Task 4: 新增 `_collect_buffer_real_user_texts` helper

**Files:**
- Modify: `server/agent_runtime/service.py`
- Test: `tests/agent_runtime/test_dedup_user_echo.py` (new)

- [ ] **Step 1: 创建测试文件并写 failing test**

Create `tests/agent_runtime/test_dedup_user_echo.py`:
```python
"""Reconnect dedup regression tests for echo / sdk UserMessage collisions.

Covers R1 (双显) and "user 消失" 现象的根因：_is_buffer_duplicate 之前
local_echo dedup 只查 DB transcript；eager flush + 此修复让 dedup 在
DB 滞后 buffer 时仍鲁棒。
"""

from __future__ import annotations

from server.agent_runtime.service import AssistantService


def test_collect_buffer_real_user_texts_excludes_local_echo(tmp_path):
    service = AssistantService(project_root=tmp_path)
    buffer = [
        {"type": "user", "content": "hello", "local_echo": True},
        {"type": "user", "content": "hello", "uuid": "u-real"},
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "user", "content": "world", "uuid": "u-real-2"},
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == {"hello", "world"}


def test_collect_buffer_real_user_texts_handles_image_only_user(tmp_path):
    """Image-only user (no plain text) should not poison the set."""
    service = AssistantService(project_root=tmp_path)
    buffer = [
        {
            "type": "user",
            "content": [{"type": "image", "source": {"data": "..."}}],
            "uuid": "u-img",
        },
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == set()


def test_collect_buffer_real_user_texts_handles_non_user_types(tmp_path):
    service = AssistantService(project_root=tmp_path)
    buffer = [
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "result", "subtype": "success"},
        {"type": "stream_event"},
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == set()


def test_collect_buffer_real_user_texts_skips_invalid_entries(tmp_path):
    service = AssistantService(project_root=tmp_path)
    buffer = [
        None,
        "not a dict",
        {"type": "user", "uuid": "u-real", "content": "ok"},
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == {"ok"}
```

- [ ] **Step 2: 跑测试，验证失败**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py -v`
Expected: `AttributeError: 'AssistantService' has no attribute '_collect_buffer_real_user_texts'`

- [ ] **Step 3: 实现 helper**

Edit `server/agent_runtime/service.py`，在 `AssistantService` 类内、紧接 `_extract_plain_user_content = staticmethod(extract_plain_user_content)` 这一行**之后**插入：
```python
    @staticmethod
    def _collect_buffer_real_user_texts(buffer: list[dict[str, Any]] | None) -> set[str]:
        """Pre-scan buffer for plain text of all real (non-echo) user messages.

        Used by _is_buffer_duplicate as a fallback dedup source when the DB
        transcript is momentarily behind the in-memory buffer (eager flush is
        fire-and-forget; SDK may coalesce frames under slow store).
        """
        texts: set[str] = set()
        for msg in buffer or []:
            if not isinstance(msg, dict):
                continue
            if msg.get("type") != "user" or msg.get("local_echo"):
                continue
            text = AssistantService._extract_plain_user_content(msg)
            if text:
                texts.add(text)
        return texts
```

- [ ] **Step 4: 跑测试，验证通过**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py -v`
Expected: 4 个全绿

- [ ] **Step 5: lint**

Run: `uv run ruff check server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py && uv run ruff format server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py`
Expected: 无 issue

- [ ] **Step 6: commit**

```bash
git add server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py
git commit -m "feat(agent-runtime): _collect_buffer_real_user_texts helper for dedup fallback"
```

---

## Task 5: `_is_buffer_duplicate` 加入 buffer 兜底分支

**Files:**
- Modify: `server/agent_runtime/service.py`
- Modify: `tests/agent_runtime/test_dedup_user_echo.py`

- [ ] **Step 1: 写 failing test**

在 `tests/agent_runtime/test_dedup_user_echo.py` 末尾追加：
```python
def test_echo_dedup_when_buffer_has_same_text_real_user(tmp_path):
    """eager 慢 store 兜底：history 还没本轮 user，buffer 已有 echo + sdk user。"""
    service = AssistantService(project_root=tmp_path)
    echo = {"type": "user", "content": "hi", "local_echo": True}
    is_dup = service._is_buffer_duplicate(
        echo,
        "user",
        transcript_uuids=set(),
        tail_fps=set(),
        history_messages=[],
        buffer_real_user_texts={"hi"},
    )
    assert is_dup is True


def test_echo_preserved_when_no_real_user_anywhere(tmp_path):
    """正向兜底：history 空 + buffer 不含真实 user → echo 必须保留。"""
    service = AssistantService(project_root=tmp_path)
    echo = {"type": "user", "content": "hi", "local_echo": True}
    is_dup = service._is_buffer_duplicate(
        echo,
        "user",
        transcript_uuids=set(),
        tail_fps=set(),
        history_messages=[],
        buffer_real_user_texts=set(),
    )
    assert is_dup is False


def test_existing_signature_backward_compat(tmp_path):
    """旧调用（5 个位置参数）保持工作 — 不破坏 test_assistant_service_more 回归。"""
    service = AssistantService(project_root=tmp_path)
    # uuid dedup 路径：transcript 已有 uuid → True
    assert (
        service._is_buffer_duplicate(
            {"uuid": "u1", "type": "user"}, "user", {"u1"}, set(), []
        )
        is True
    )
```

- [ ] **Step 2: 跑测试，验证失败**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py::test_echo_dedup_when_buffer_has_same_text_real_user -v`
Expected: `TypeError: _is_buffer_duplicate() got an unexpected keyword argument 'buffer_real_user_texts'`

- [ ] **Step 3: 改 `_is_buffer_duplicate`**

Edit `server/agent_runtime/service.py`，定位 `_is_buffer_duplicate` 方法（约第 654 行），整体替换为：
```python
    def _is_buffer_duplicate(
        self,
        msg: dict[str, Any],
        msg_type: str,
        transcript_uuids: set[str],
        tail_fps: set[str],
        history_messages: list[dict[str, Any]],
        buffer_real_user_texts: set[str] | None = None,
    ) -> bool:
        """Check if a groupable buffer message duplicates a transcript message.

        ``buffer_real_user_texts`` is a pre-scan of the same buffer the caller
        is iterating; an echo that lacks a transcript-side match still gets
        deduped if the buffer itself already carries a same-text real user
        (covers eager flush's DB-lag window when SDK coalesces frames under
        a slow store).
        """
        # 1. UUID dedup
        uuid = msg.get("uuid")
        if uuid and uuid in transcript_uuids:
            return True

        # 2. Local echo dedup — transcript first, buffer fallback
        if msg.get("local_echo"):
            if self._echo_in_transcript(msg, history_messages):
                return True
            if buffer_real_user_texts:
                echo_text = self._extract_plain_user_content(msg)
                if echo_text and echo_text in buffer_real_user_texts:
                    return True

        # 3. Content fingerprint dedup (fallback for UUID-less buffer messages)
        if not uuid and msg_type in {"assistant", "result"}:
            fp = self._fingerprint(msg)
            if fp and fp in tail_fps:
                return True

        return False
```

**关键点**：`buffer_real_user_texts` 默认值为 `None`，向后兼容已有 5-arg 调用。

- [ ] **Step 4: 跑测试，验证通过**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py -v`
Expected: 7 个全绿

- [ ] **Step 5: 回归保护**

Run: `uv run python -m pytest tests/test_assistant_service_more.py::TestAssistantServiceMore::test_merge_and_dedup_helpers -v`
Expected: PASS（默认参 None 兼容）

- [ ] **Step 6: lint**

Run: `uv run ruff check server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py && uv run ruff format server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py`
Expected: 无 issue

- [ ] **Step 7: commit**

```bash
git add server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py
git commit -m "feat(agent-runtime): echo dedup fallback against buffer real users"
```

---

## Task 6: `_build_projector` pre-scan + 调用新 dedup 签名

**Files:**
- Modify: `server/agent_runtime/service.py`
- Modify: `tests/agent_runtime/test_dedup_user_echo.py`

- [ ] **Step 1: 写 failing integration-style test**

在 `tests/agent_runtime/test_dedup_user_echo.py` 末尾追加：
```python
def test_build_projector_dedups_echo_when_buffer_has_real_user(tmp_path):
    """集成：history 空 + buffer = [echo, sdk_user_msg] → projector 单条 user。"""
    import asyncio

    from server.agent_runtime.models import SessionMeta

    service = AssistantService(project_root=tmp_path)

    class _StubAdapter:
        async def read_raw_messages(self, sid, project_cwd):
            return []  # transcript 空，模拟 batched 模式 turn 进行中

    service.transcript_adapter = _StubAdapter()  # type: ignore[assignment]

    buffer = [
        {"type": "user", "content": "你好", "local_echo": True},
        {"type": "user", "content": "你好", "uuid": "user-uuid-1"},
    ]

    class _SmStub:
        sessions: dict = {}

        def get_buffered_messages(self, sid):
            return buffer

    service.session_manager = _SmStub()  # type: ignore[assignment]

    meta = SessionMeta(
        id="sid-1",
        project_name="proj",
        title="",
        status="running",
        created_at="",
        updated_at="",
    )

    async def _go():
        return await service._build_projector(meta, "sid-1")

    projector = asyncio.run(_go())
    user_turns = [t for t in projector.turns if t.get("type") == "user"]
    assert len(user_turns) == 1, (
        f"expected 1 user turn, got {len(user_turns)}: {user_turns}"
    )
```

- [ ] **Step 2: 跑测试，验证失败**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py::test_build_projector_dedups_echo_when_buffer_has_real_user -v`
Expected: AssertionError（两条 user turns，因为 _build_projector 还没调 pre-scan）

- [ ] **Step 3: 改 `_build_projector`**

Edit `server/agent_runtime/service.py` 的 `_build_projector` 方法（约第 608 行）。定位到：
```python
        buffer = replayed_messages
        if buffer is None:
            buffer = self.session_manager.get_buffered_messages(session_id)

        for msg in buffer or []:
```

在 `for msg in buffer or []:` 之前插入 pre-scan：
```python
        # Pre-scan buffer for real (non-echo) user texts; used as dedup fallback
        # when the DB transcript momentarily lags the in-memory buffer (eager
        # flush is fire-and-forget + SDK coalesces frames under a slow store).
        buffer_real_user_texts = self._collect_buffer_real_user_texts(buffer or [])

```

然后把循环内的 `_is_buffer_duplicate` 调用从：
```python
            if not self._is_buffer_duplicate(msg, msg_type, transcript_uuids, tail_fps, history_messages):
```
改为：
```python
            if not self._is_buffer_duplicate(
                msg,
                msg_type,
                transcript_uuids,
                tail_fps,
                history_messages,
                buffer_real_user_texts,
            ):
```

- [ ] **Step 4: 跑测试，验证通过**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py -v`
Expected: 8 个全绿

Run: `uv run python -m pytest tests/test_assistant_service_more.py -v`
Expected: 全绿（回归保护）

- [ ] **Step 5: lint**

Run: `uv run ruff check server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py && uv run ruff format server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py`
Expected: 无 issue

- [ ] **Step 6: commit**

```bash
git add server/agent_runtime/service.py tests/agent_runtime/test_dedup_user_echo.py
git commit -m "fix(agent-runtime): _build_projector pre-scans buffer for echo dedup fallback"
```

---

## Task 7: 跨轮同文场景 + "user 消失" 回归

**Files:**
- Modify: `tests/agent_runtime/test_dedup_user_echo.py`

- [ ] **Step 1: 写场景测试**

在 `tests/agent_runtime/test_dedup_user_echo.py` 末尾追加：
```python
def test_echo_with_same_text_as_prior_round_round_aware(tmp_path):
    """上一轮 user 文本与本轮相同 → 取决于 transcript 是否含本轮 user。

    Case B: history 只有上一轮 + 已 result → echo 不应被 dedup
    Case A: history 含本轮 user (eager 已 flush) → echo 应被 dedup
    """
    service = AssistantService(project_root=tmp_path)
    echo = {
        "type": "user",
        "content": "继续",
        "local_echo": True,
        "timestamp": "2026-05-06T01:00:00Z",
    }

    # Case B: 上一轮已 result → echo 是新一轮，不应 dedup
    history_prior_complete = [
        {
            "type": "user",
            "content": "继续",
            "timestamp": "2026-05-06T00:00:00Z",
            "uuid": "old",
        },
        {
            "type": "assistant",
            "content": [{"type": "text", "text": "好"}],
            "uuid": "old-a",
        },
        {"type": "result", "subtype": "success"},
    ]
    assert (
        service._is_buffer_duplicate(
            echo,
            "user",
            transcript_uuids={"old", "old-a"},
            tail_fps=set(),
            history_messages=history_prior_complete,
            buffer_real_user_texts=set(),
        )
        is False
    )

    # Case A: history 含本轮 user (eager 已写入) → echo 应 dedup
    history_with_current = [
        *history_prior_complete,
        {
            "type": "user",
            "content": "继续",
            "timestamp": "2026-05-06T01:00:01Z",  # 比 echo 晚
            "uuid": "current",
        },
    ]
    assert (
        service._is_buffer_duplicate(
            echo,
            "user",
            transcript_uuids={"old", "old-a", "current"},
            tail_fps=set(),
            history_messages=history_with_current,
            buffer_real_user_texts=set(),
        )
        is True
    )


def test_user_message_not_lost_when_transcript_is_empty(tmp_path):
    """关键回归 - "user 消失"：transcript 为空 + buffer 只有 echo → echo 必须保留。"""
    service = AssistantService(project_root=tmp_path)
    echo = {"type": "user", "content": "你好", "local_echo": True}
    is_dup = service._is_buffer_duplicate(
        echo,
        "user",
        transcript_uuids=set(),
        tail_fps=set(),
        history_messages=[],
        buffer_real_user_texts=set(),  # buffer 还没收到 sdk user
    )
    assert is_dup is False, "echo must be preserved when no real user exists anywhere"
```

- [ ] **Step 2: 跑测试，验证通过**

Run: `uv run python -m pytest tests/agent_runtime/test_dedup_user_echo.py -v`
Expected: 10 个全绿（Task 5/6 实现已直接覆盖；本任务只补回归）

- [ ] **Step 3: lint**

Run: `uv run ruff check tests/agent_runtime/test_dedup_user_echo.py && uv run ruff format tests/agent_runtime/test_dedup_user_echo.py`
Expected: 无 issue

- [ ] **Step 4: commit**

```bash
git add tests/agent_runtime/test_dedup_user_echo.py
git commit -m "test(agent-runtime): regress R1 / user-disappear cross-round scenarios"
```

---

## Task 8: Crash durability + eager 多次 append 独立性 e2e

**Files:**
- Modify: `tests/agent_runtime/test_session_store_e2e.py`

- [ ] **Step 1: 写 e2e 测试**

在 `tests/agent_runtime/test_session_store_e2e.py` 末尾追加：
```python
@pytest.mark.asyncio
async def test_partial_transcript_visible_after_simulated_crash(
    session_factory, tmp_path: Path
):
    """eager flush durability：partial transcript 在进程"重启"后仍可读。

    模拟"服务进程崩溃"= 丢弃所有 in-memory 状态，仅保留 DB；新建 store
    实例（模拟新进程）继续读，验证之前 append 的 entries 完全可达。
    """
    store = DbSessionStore(session_factory, user_id="crash-recover")
    project_cwd = tmp_path / "projects" / "crash_demo"
    project_cwd.mkdir(parents=True)
    sid = "11111111-2222-3333-4444-555555555555"
    key = {"project_key": make_project_key(project_cwd), "session_id": sid}

    # 模拟"turn 进行中" eager flush 写入两条 entry（user + 部分 assistant）
    await store.append(
        key,
        [
            {
                "type": "user",
                "uuid": "1",
                "timestamp": "2026-05-06T10:00:00Z",
                "message": {"content": "long task"},
            },
        ],
    )
    await store.append(
        key,
        [
            {
                "type": "assistant",
                "uuid": "2",
                "parentUuid": "1",
                "timestamp": "2026-05-06T10:00:01Z",
                "message": {"content": "starting..."},
            },
        ],
    )

    # 模拟新进程：drop in-memory state, rebuild store
    store_after_restart = DbSessionStore(session_factory, user_id="crash-recover")

    raw = await store_after_restart.load(key)
    assert raw is not None and len(raw) == 2

    from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter

    adapter = SdkTranscriptAdapter(store=store_after_restart)
    msgs = await adapter.read_raw_messages(sid, project_cwd=str(project_cwd))
    assert len(msgs) == 2
    assert any(m.get("type") == "user" for m in msgs)
    assert any(m.get("type") == "assistant" for m in msgs)


@pytest.mark.asyncio
async def test_eager_persistence_independent_of_buffer(
    session_factory, tmp_path: Path
):
    """长 turn 跨 buffer 驱逐：DB 应有完整 user/assistant 序列。

    DbSessionStore 的 append 调用与 in-memory buffer 完全解耦。本测试
    验证多次单条 append（模拟 SDK eager 模式）后 load 能拼出完整序列。
    """
    store = DbSessionStore(session_factory, user_id="long-turn")
    project_cwd = tmp_path / "projects" / "long_demo"
    project_cwd.mkdir(parents=True)
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    key = {"project_key": make_project_key(project_cwd), "session_id": sid}

    # 模拟 SDK eager 模式分多次 append（每个完整 frame 一次）
    frames = []
    for i in range(20):
        if i == 0:
            frames.append(
                {
                    "type": "user",
                    "uuid": str(i),
                    "timestamp": f"2026-05-06T10:00:{i:02d}Z",
                    "message": {"content": f"f{i}"},
                }
            )
        else:
            frames.append(
                {
                    "type": "assistant",
                    "uuid": str(i),
                    "parentUuid": str(i - 1),
                    "timestamp": f"2026-05-06T10:00:{i:02d}Z",
                    "message": {"content": f"f{i}"},
                }
            )
    for f in frames:
        await store.append(key, [f])

    raw = await store.load(key)
    assert raw is not None and len(raw) == 20
```

- [ ] **Step 2: 跑测试**

Run: `uv run python -m pytest tests/agent_runtime/test_session_store_e2e.py -v`
Expected: 3 个全绿（已有 1 个 + 新 2 个）

- [ ] **Step 3: lint**

Run: `uv run ruff check tests/agent_runtime/test_session_store_e2e.py && uv run ruff format tests/agent_runtime/test_session_store_e2e.py`
Expected: 无 issue

- [ ] **Step 4: commit**

```bash
git add tests/agent_runtime/test_session_store_e2e.py
git commit -m "test(session-store): crash durability + eager multi-append independence"
```

---

## Task 9: 全套测试 + 手动验收

- [ ] **Step 1: 跑全套相关测试**

Run:
```bash
uv run python -m pytest tests/agent_runtime/ tests/agent_session_store/ tests/test_assistant_service_more.py -v
```
Expected: 全绿，含 §6 spec 列出的全部测试

- [ ] **Step 2: 完整 lint check**

Run:
```bash
uv run ruff check . && uv run ruff format --check .
```
Expected: 无 issue

- [ ] **Step 3: 手动验收 1 - turn 进行中 reload (R1 + user 消失)**

启动开发服务器：
```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
```

另一终端启动前端：
```bash
cd frontend && pnpm dev
```

操作：发送一个会触发长 turn 的消息（如让 agent 读多个文件），**立即 F5 刷新页面**。
Expected:
- 用户问题完整可见，**不消失**
- 不出现两条同文 user 消息（**不双显**）
- assistant turn 流式继续

- [ ] **Step 4: 手动验收 2 - 服务重启后 partial 可见**

发送一条会触发长 turn 的消息 → 等 turn 跑到一半 → `pkill -f "uvicorn server.app:app.*--port 1241"`（仅杀本次实例，避免误伤其他 uvicorn）→ 重启 server → 前端刷新进入会话。
Expected:
- 看到中断前已生成的 partial transcript
- 会话状态显示 `interrupted`

- [ ] **Step 5: 手动验收 3 - batched 回退路径**

```bash
ARCREEL_SDK_SESSION_STORE_FLUSH=batched uv run uvicorn server.app:app --reload-dir server --reload-dir lib --port 1241
```

操作：正常发送几条消息。
Expected: 行为与 0.1.72 一致（turn 内 reload 仍可能 R1，是 batched 现状的已知行为）。

- [ ] **Step 6: 完成**

如果 Step 1–5 全部 OK，向用户报告完成。如有失败，回到对应 task 修。
