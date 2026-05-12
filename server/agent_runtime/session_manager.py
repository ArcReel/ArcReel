"""
Manages ClaudeSDKClient instances with background execution and reconnection support.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from lib.agent_session_store import session_store_flush_mode
from lib.agent_session_store.store import DbSessionStore
from lib.db.base import DEFAULT_USER_ID
from lib.db.engine import async_session_factory as default_async_session_factory
from lib.i18n import LOCALE_LANGUAGE_MAP
from server.agent_runtime.message_utils import extract_plain_user_content
from server.agent_runtime.models import SessionMeta, SessionStatus
from server.agent_runtime.session_actor import SessionActor, SessionCommand
from server.agent_runtime.session_store import SessionMetaStore

logger = logging.getLogger(__name__)

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import HookMatcher, PermissionResultAllow, SystemPromptPreset

    try:
        from claude_agent_sdk.types import PermissionResultDeny
    except ImportError:
        PermissionResultDeny = None
    try:
        from claude_agent_sdk import tag_session
    except ImportError:
        tag_session = None

    SDK_AVAILABLE = True
except ImportError:
    ClaudeSDKClient = None
    ClaudeAgentOptions = None
    HookMatcher = None
    PermissionResultAllow = None
    PermissionResultDeny = None
    tag_session = None
    SDK_AVAILABLE = False

try:
    from lib.config.service import ConfigService
    from lib.db import async_session_factory
except ImportError:
    async_session_factory = None  # type: ignore[assignment]
    ConfigService = None  # type: ignore[assignment]


# inbox 积压告警阈值：~1s 内 100 条 stream_event（典型流式频率上限）；
# 持续高于此值说明 _process_inbox 被阻塞或下游 I/O 超慢。
_INBOX_BACKLOG_WARN_THRESHOLD = 100
_INBOX_BACKLOG_RESET_THRESHOLD = 50  # 降至此水位以下才重置告警状态，避免抖动刷屏


class SessionCapacityError(Exception):
    """所有并发槽位已被 running 会话占满，无法创建新连接。"""

    pass


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class PendingQuestion:
    """Tracks a pending AskUserQuestion request."""

    question_id: str
    payload: dict[str, Any]
    answer_future: asyncio.Future[dict[str, str]]


@dataclass
class ManagedSession:
    """A managed ClaudeSDKClient session."""

    session_id: str  # sdk_session_id（已有会话）或临时 UUID（新会话等待中）
    actor: "SessionActor"  # per-session actor owning the SDK client
    status: SessionStatus = "idle"
    project_name: str = ""  # 用于 _register_new_session
    sdk_id_event: asyncio.Event = field(default_factory=asyncio.Event)
    resolved_sdk_id: str | None = None  # consumer 设置，send_new_session 读取
    message_buffer: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    buffer_max_size: int = 100
    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    pending_user_echoes: list[str] = field(default_factory=list)
    interrupt_requested: bool = False
    last_activity: float | None = None  # updated on every send/receive
    _cleanup_task: asyncio.Task | None = None  # current cleanup timer (idle TTL or terminal delay)
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue)  # async post-processing queue
    _inbox_warned: bool = False  # edge-triggered backlog warning state
    _process_task: asyncio.Task | None = None  # per-session async inbox processor
    _interrupting: bool = False  # send_interrupt re-entry guard (distinct from interrupt_requested)

    # Message types that must never be silently dropped from subscriber queues.
    _CRITICAL_MESSAGE_TYPES = {"result", "runtime_status", "user", "assistant"}
    # Transient types that are evicted first when buffer is full.
    _TRANSIENT_BUFFER_TYPES = {"stream_event"}

    def add_message(self, message: dict[str, Any]) -> None:
        """Add message to buffer and notify subscribers."""
        self.message_buffer.append(message)
        if len(self.message_buffer) > self.buffer_max_size:
            self._evict_oldest_buffer_entry()
        self._broadcast_to_subscribers(message)

    def _on_actor_message(self, msg: dict[str, Any]) -> None:
        """SessionActor 的 on_message 回调。同步，内存操作，不 await。

        职责：add_message 进行 buffer + broadcast。

        **状态转换不在此处做**——managed.status 由 _finalize_turn 在异步路径中
        统一设置。若在此提前切换为 idle/completed，`send_message` 的并发保护
        （拦截 status=="running"）会在 _finalize_turn 跑完前失效，下一轮消息
        可能进入，随后上一轮 finalize 回写/清理会误伤新一轮。

        pending_questions 注册由 SessionManager._handle_special_message 处理。
        """
        self.add_message(msg)

    async def send_query(self, prompt: str | AsyncIterable[dict], sdk_session_id: str = "default") -> None:
        """将 prompt 送入 SDK 后立即返回；整轮 receive_response 由 actor 后台 drain。

        只等 `cmd.sent`（prompt 已进 SDK）而非 `cmd.done`（整轮结束），以保持
        `/sessions/send` 原有的 "立即 accepted + SSE 异步消费" 语义。
        """
        self.status = "running"
        cmd = SessionCommand(type="query", prompt=prompt, session_id=sdk_session_id)
        await self.actor.enqueue(cmd)
        await cmd.sent.wait()
        if cmd.error is not None:
            self.status = "error"
            raise cmd.error

    async def send_interrupt(self) -> None:
        if self._interrupting:
            return
        self._interrupting = True
        try:
            cmd = SessionCommand(type="interrupt")
            await self.actor.enqueue(cmd)
            await cmd.done.wait()
            if cmd.error is not None:
                raise cmd.error
        finally:
            self._interrupting = False

    async def send_disconnect(self) -> None:
        cmd = SessionCommand(type="disconnect")
        await self.actor.enqueue(cmd)
        await cmd.done.wait()
        await self.actor.wait()
        self.status = "closed"

    def _evict_oldest_buffer_entry(self) -> None:
        """Evict one entry from buffer, preferring transient stream_events."""
        for i, m in enumerate(self.message_buffer[:-1]):
            if m.get("type") in self._TRANSIENT_BUFFER_TYPES:
                self.message_buffer.pop(i)
                return
        self.message_buffer.pop(0)

    def _broadcast_to_subscribers(self, message: dict[str, Any]) -> None:
        """Push message to all subscriber queues, evicting non-critical on overflow."""
        is_critical = message.get("type") in self._CRITICAL_MESSAGE_TYPES
        stale_queues: list[asyncio.Queue] = []
        for queue in self.subscribers:
            if not self._try_enqueue(queue, message, is_critical):
                stale_queues.append(queue)
        for q in stale_queues:
            # Drain the hopelessly full queue and inject a reconnect signal so
            # the SSE consumer loop terminates instead of blocking forever.
            self._drain_and_signal_reconnect(q)
            self.subscribers.discard(q)

    def _drain_and_signal_reconnect(self, queue: asyncio.Queue) -> None:
        """Empty *queue* and push a reconnect signal so the SSE loop exits.

        Uses a connection-level ``_queue_overflow`` type rather than
        ``runtime_status`` so the SSE consumer can close the stream without
        misrepresenting the session's actual status to the client.
        """
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(
                {
                    "type": "_queue_overflow",
                    "session_id": self.session_id,
                }
            )
        except asyncio.QueueFull:
            pass  # should never happen after drain

    def _try_enqueue(self, queue: asyncio.Queue, message: dict[str, Any], is_critical: bool) -> bool:
        """Try to put *message* into *queue*. Returns False if the queue should be discarded."""
        try:
            queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            if not is_critical:
                return True  # non-critical drop is acceptable
        # Critical message on a full queue — evict one non-critical to make room.
        self._evict_non_critical(queue)
        try:
            queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    @staticmethod
    def _evict_non_critical(queue: asyncio.Queue) -> bool:
        """Try to remove one non-critical message from *queue* to make room."""
        temp: list[dict[str, Any]] = []
        evicted = False
        while not queue.empty():
            try:
                msg = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not evicted and msg.get("type") not in ManagedSession._CRITICAL_MESSAGE_TYPES:
                evicted = True  # drop this one
                continue
            temp.append(msg)
        for msg in temp:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                break
        return evicted

    def clear_buffer(self) -> None:
        """Clear message buffer after session completes."""
        self.message_buffer.clear()

    def add_pending_question(self, payload: dict[str, Any]) -> PendingQuestion:
        """Register a pending AskUserQuestion payload."""
        question_id = str(payload.get("question_id") or f"aq_{uuid4().hex}")
        payload["question_id"] = question_id
        future: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()
        pending = PendingQuestion(
            question_id=question_id,
            payload=payload,
            answer_future=future,
        )
        self.pending_questions[question_id] = pending
        return pending

    def resolve_pending_question(self, question_id: str, answers: dict[str, str]) -> bool:
        """Resolve a pending AskUserQuestion with user answers."""
        pending = self.pending_questions.pop(question_id, None)
        if not pending:
            return False
        if not pending.answer_future.done():
            pending.answer_future.set_result(answers)
        return True

    def cancel_pending_questions(self, reason: str = "session closed") -> None:
        """Cancel all pending AskUserQuestion waiters."""
        for pending in list(self.pending_questions.values()):
            if not pending.answer_future.done():
                pending.answer_future.set_exception(RuntimeError(reason))
        self.pending_questions.clear()

    def get_pending_question_payloads(self) -> list[dict[str, Any]]:
        """Return unresolved AskUserQuestion payloads for reconnect snapshot."""
        return [pending.payload for pending in self.pending_questions.values()]


class SessionManager:
    """Manages all active ClaudeSDKClient instances."""

    DEFAULT_ALLOWED_TOOLS = [
        "Skill",
        "Task",
        # —— Bash 系列（sandbox 启用 + autoAllowBashIfSandboxed=True 协同放行）——
        "Bash",
        "BashOutput",
        "KillBash",
        # —— SDK 内置工具（仍走 PreToolUse hook 文件围栏 + settings.json deny）——
        "Read",
        "Write",
        "Edit",
        "Grep",
        "Glob",
        "AskUserQuestion",
    ]
    DEFAULT_SETTING_SOURCES = ["project"]
    _SDK_ID_TIMEOUT = 60.0

    # Sandbox 启用后 Bash 进入 allowed_tools；具体命令由 SDK Sandbox 自动放行
    # (autoAllowBashIfSandboxed=True)。文件访问控制走 settings.json deny rules
    # + PreToolUse hook 双重防线 (spec §4)。
    _PATH_TOOLS: dict[str, str] = {
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Glob": "path",
        "Grep": "path",
    }
    _WRITE_TOOLS = {"Write", "Edit"}
    _WRITABLE_EXTENSIONS = {".json", ".md", ".txt"}

    # Sentinel used in pending_user_echoes for image-only messages (no text).
    # The SDK parser drops image blocks, so the replayed UserMessage arrives
    # with empty content; this sentinel lets _is_duplicate_user_echo match it.
    _IMAGE_ONLY_SENTINEL = "__image_only__"

    # SDK message class name to type mapping
    _MESSAGE_TYPE_MAP = {
        "UserMessage": "user",
        "AssistantMessage": "assistant",
        "ResultMessage": "result",
        "SystemMessage": "system",
        "StreamEvent": "stream_event",
        "TaskStartedMessage": "system",
        "TaskProgressMessage": "system",
        "TaskNotificationMessage": "system",
    }

    # Typed task message subtypes for precise classification
    _TASK_MESSAGE_SUBTYPES = {
        "TaskStartedMessage": "task_started",
        "TaskProgressMessage": "task_progress",
        "TaskNotificationMessage": "task_notification",
    }

    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        meta_store: SessionMetaStore,
        projects_root: Path | None = None,
    ):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir)
        # Tests construct SessionManager directly without going through
        # AssistantService, so we fall back to the legacy ``project_root/projects``
        # convention. Production passes the configured app_data_dir() explicitly.
        # 两路都 resolve，避免符号链接场景下 _resolve_project_cwd 的 relative_to
        # 校验失败（project_cwd 已经 resolve 过）。strict=False 容忍目录不存在。
        self.projects_root = (
            Path(projects_root).resolve(strict=False)
            if projects_root is not None
            else (self.project_root / "projects").resolve()
        )
        self.meta_store = meta_store
        self.sessions: dict[str, ManagedSession] = {}
        self._disconnecting: set[str] = set()
        self._session_actor_shutdown_timeout: float = 15.0  # total budget for send_disconnect + cancel fallback
        self._connect_locks: dict[str, asyncio.Lock] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from environment (sync fallback)."""
        max_turns_env = os.environ.get("ASSISTANT_MAX_TURNS", "").strip()
        self.max_turns = int(max_turns_env) if max_turns_env else None

    async def refresh_config(self) -> None:
        """Reload configuration from ConfigService (DB), falling back to env."""
        try:
            from lib.config.service import ConfigService
            from lib.db import async_session_factory

            async with async_session_factory() as session:
                svc = ConfigService(session)
                raw = await svc.get_setting("assistant_max_turns", "")
                raw = raw.strip()
                if raw:
                    self.max_turns = int(raw)
                    return
        except Exception:
            logger.warning("从 DB 加载 assistant 配置失败，回退到环境变量", exc_info=True)
        # Fallback to env var
        self._load_config()

    _PERSONA_PROMPT = """\
## 身份

你是 ArcReel 智能体，一个专业的 AI 视频内容创作助手。你的职责是将小说转化为可发布的短视频内容。

## 行为准则

- 主动引导用户完成视频创作工作流，而不仅仅被动回答问题
- 遇到不确定的创作决策时，向用户提出选项并给出建议，而不是自行决定
- 涉及多步骤任务时，使用 TodoWrite 跟踪进度并向用户汇报
- 你不能创建或编辑代码文件（.py/.js/.sh 等），Write/Edit 仅限 .json/.md/.txt
- 你是用户的视频制作搭档，专业、友善、高效"""

    def _build_append_prompt(self, project_name: str, locale: str = "zh") -> str:
        """Build the append portion for SystemPromptPreset.

        Combines the ArcReel persona with project-specific context from
        project.json.  The base CLAUDE.md is auto-loaded by the SDK via
        setting_sources=["project"] and the CLAUDE.md symlink in the
        project cwd.
        """
        parts = [self._PERSONA_PROMPT]

        lang = LOCALE_LANGUAGE_MAP.get(locale, "中文")
        parts.append(
            f"\n## 语言规范\n\n"
            f"- **回答用户必须使用{lang}**：所有回复、思考过程、任务清单及计划文件，均须使用{lang}\n"
            f"- **视频内容语言**：所有生成的视频对话、旁白、字幕均使用{lang}\n"
            f"- **文档使用{lang}**：所有的 Markdown 文件均使用{lang}编写\n"
            f"- **Prompt 使用{lang}**：图片生成/视频生成使用的 prompt 应使用{lang}编写"
        )

        project_context = self._build_project_context(project_name)
        if project_context:
            parts.append(project_context)

        return "\n".join(parts)

    def _build_project_context(self, project_name: str) -> str:
        """Build project-specific context from project.json metadata."""
        try:
            project_cwd = self._resolve_project_cwd(project_name)
        except (ValueError, FileNotFoundError):
            return ""

        project_json = project_cwd / "project.json"
        if not project_json.exists():
            return ""

        try:
            config = json.loads(project_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read project.json for %s: %s", project_name, exc)
            return ""

        if not isinstance(config, dict):
            logger.warning("project.json for %s is not a JSON object", project_name)
            return ""

        parts = [
            "## 当前项目上下文",
            "",
        ]

        # TODO: 当前定位是自部署服务，这里直接拼接项目元数据以保持实现简单。
        # TODO: 若后续演进为 SaaS / 多租户服务，需要把 title/style/overview 等用户输入
        # TODO: 按“非指令上下文”做边界化或转义，降低 prompt injection 风险。
        parts.append(f"- 项目标识：{project_name}")
        if title := config.get("title"):
            parts.append(f"- 项目标题：{title}")
        if mode := config.get("content_mode"):
            parts.append(f"- 内容模式：{mode}")
        if style := config.get("style"):
            parts.append(f"- 视觉风格：{style}")
        if style_desc := config.get("style_description"):
            parts.append(f"- 风格描述：{style_desc}")
        parts.append(f"- 项目目录（即当前工作目录 cwd）：{project_cwd}")
        parts.append(
            "- Read/Edit/Write 等工具的 file_path 参数必须使用绝对路径，不要使用相对路径，也不要把项目标题当成目录名。"
        )
        parts.append(
            "- Bash 调用 skill 脚本时必须使用相对路径（如 `python .claude/skills/.../script.py`），不要转换为绝对路径。"
        )
        parts.append("- Bash 命令必须写在单行，禁止使用 `\\` 换行，JSON 参数使用紧凑格式。")

        self._append_overview_section(parts, config.get("overview", {}))

        return "\n".join(parts)

    @staticmethod
    def _append_overview_section(parts: list[str], overview: Any) -> None:
        """Append project overview fields to prompt parts."""
        if not isinstance(overview, dict) or not overview:
            return
        parts.append("")
        parts.append("### 项目概述")
        if synopsis := overview.get("synopsis"):
            parts.append(synopsis)
        if genre := overview.get("genre"):
            parts.append(f"- 题材：{genre}")
        if theme := overview.get("theme"):
            parts.append(f"- 主题：{theme}")
        if world := overview.get("world_setting"):
            parts.append(f"- 世界观：{world}")

    def _build_session_store(self) -> DbSessionStore | None:
        """Return a cached per-user DbSessionStore, or None when env disables it.

        Set ARCREEL_SDK_SESSION_STORE=off to roll back to SDK's filesystem path.
        The result is cached on first call so every session shares one instance
        instead of allocating a fresh store per ``_build_options`` invocation.
        """
        cached = getattr(self, "_cached_session_store", None)
        if cached is not None or getattr(self, "_session_store_resolved", False):
            return cached
        from lib.agent_session_store import (
            is_known_session_store_mode,
            session_store_mode,
        )

        mode = session_store_mode()
        store: DbSessionStore | None
        if mode == "off":
            store = None
        else:
            if not is_known_session_store_mode(mode):
                logger.warning("Unknown ARCREEL_SDK_SESSION_STORE=%r; defaulting to db", mode)
            factory = getattr(self, "_session_factory", None) or default_async_session_factory
            user_id = getattr(self, "_user_id", DEFAULT_USER_ID)
            store = DbSessionStore(factory, user_id=user_id)
        self._cached_session_store = store
        self._session_store_resolved = True
        return store

    async def _build_provider_env_overrides(self) -> dict[str, str]:
        """构造 options.env 注入字典 — spec §6.2。

        - ANTHROPIC_* 从 DB active credential 取真值
        - 其他 provider env 全部空值覆盖（防御性兜底）
        """
        from lib.config.env_keys import OTHER_PROVIDER_ENV_KEYS
        from lib.config.service import build_anthropic_env_dict
        from lib.db import async_session_factory

        async with async_session_factory() as session:
            anthropic_env = await build_anthropic_env_dict(session)

        result = dict(anthropic_env)
        for key in OTHER_PROVIDER_ENV_KEYS:
            result[key] = ""
        return result

    def _build_options(
        self,
        project_name: str,
        resume_id: str | None = None,
        can_use_tool: Callable[[str, dict[str, Any], Any], Any] | None = None,
        locale: str = "zh",
    ) -> Any:
        """Build ClaudeAgentOptions for a session."""
        if not SDK_AVAILABLE or ClaudeAgentOptions is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        transcripts_dir = self.data_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        project_cwd = self._resolve_project_cwd(project_name)

        # Build PreToolUse hooks — file access control MUST use hooks because
        # Read/Glob/Grep are matched by allow rules (step 4 in the SDK
        # permission chain) before reaching can_use_tool (step 5).  Hooks
        # (step 1) fire for ALL tool calls and can override allow rules.
        hooks = None
        if HookMatcher is not None:
            hook_callbacks: list[Any] = [
                self._build_file_access_hook(project_cwd),
            ]
            if can_use_tool is not None:
                # Official Python SDK guidance: keep stream open when using
                # can_use_tool.
                hook_callbacks.insert(0, self._keep_stream_open_hook)

            # Shared dict: PreToolUse saves file backup, PostToolUse restores
            # on corruption.  Keyed by tool_use_id.
            json_backups: dict[str, tuple[Path, str]] = {}

            hooks = {
                "PreToolUse": [
                    HookMatcher(matcher=None, hooks=hook_callbacks),
                    HookMatcher(
                        matcher="Write|Edit",
                        hooks=[
                            self._build_json_validation_hook(project_cwd, json_backups),
                        ],
                    ),
                ],
                "PostToolUse": [
                    HookMatcher(
                        matcher="Write|Edit",
                        hooks=[
                            self._build_json_post_validation_hook(project_cwd, json_backups),
                        ],
                    ),
                ],
            }

        return ClaudeAgentOptions(
            cwd=str(project_cwd),
            setting_sources=self.DEFAULT_SETTING_SOURCES,
            allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
            max_turns=self.max_turns,
            system_prompt=SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=self._build_append_prompt(project_name, locale=locale),
            ),
            include_partial_messages=True,
            resume=resume_id,
            can_use_tool=can_use_tool,
            hooks=hooks,
            session_store=self._build_session_store(),
            session_store_flush=session_store_flush_mode(),
        )

    @staticmethod
    async def _keep_stream_open_hook(
        _input_data: dict[str, Any], _tool_use_id: str | None, _context: Any
    ) -> dict[str, bool]:
        """Required keep-alive hook for Python can_use_tool callback."""
        return {"continue_": True}

    def _build_file_access_hook(
        self,
        project_cwd: Path,
    ) -> Callable[..., Any]:
        """Build a PreToolUse hook callback that enforces file access control.

        PreToolUse hooks are step 1 in the SDK permission chain and fire for
        **every** tool call, including Read/Glob/Grep which would otherwise
        be auto-approved by allow rules at step 4.
        """

        async def _file_access_hook(
            input_data: dict[str, Any],
            _tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            tool_name = input_data.get("tool_name", "")
            if tool_name not in self._PATH_TOOLS:
                return {"continue_": True}

            tool_input = input_data.get("tool_input", {})
            path_key = self._PATH_TOOLS[tool_name]
            file_path = tool_input.get(path_key)

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

            return {"continue_": True}

        return _file_access_hook

    def _build_json_validation_hook(
        self,
        project_cwd: Path,
        json_backups: dict[str, tuple[Path, str]] | None = None,
    ) -> Callable[..., Any]:
        """Build a PreToolUse hook that blocks Write/Edit when the result would
        produce invalid JSON.

        For Edit: reads the current file, simulates the string replacement, and
        validates the result with ``json.loads()``.
        For Write: validates the ``content`` parameter directly.

        When *json_backups* is provided, the hook saves the current file
        content before the edit so the PostToolUse hook can restore it if
        the actual result turns out to be invalid.

        Returns ``permissionDecision: "deny"`` to block the operation before it
        executes, giving the agent a chance to fix its input and retry.
        """

        async def _json_validation_hook(
            input_data: dict[str, Any],
            _tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            file_path = tool_input.get("file_path", "")
            if not file_path or not file_path.endswith(".json"):
                return {}

            # --- Reject curly/smart quotes that would corrupt JSON ---
            _CURLY_QUOTES = "\u201c\u201d\u201e\u201f"  # ""„‟

            def _has_curly_quotes(text: str) -> bool:
                """Return True if *text* contains Unicode curly/smart quotes."""
                return any(ch in _CURLY_QUOTES for ch in text)

            # --- Simulate the result without touching the file ---
            simulated: str | None = None

            if tool_name == "Write":
                simulated = tool_input.get("content")
                logger.info(
                    "JSON 校验 hook: tool=Write file=%s content_len=%s",
                    file_path,
                    len(simulated) if simulated else 0,
                )
            elif tool_name == "Edit":
                old_string = tool_input.get("old_string", "")
                new_string = tool_input.get("new_string", "")
                if not old_string:
                    logger.info(
                        "JSON 校验 hook: tool=Edit file=%s skip=old_string为空",
                        file_path,
                    )
                    return {}

                # Detect curly quotes early — Claude Code may normalise
                # old_string internally (allowing the edit to succeed) while
                # the hook's exact-match ``old_string not in current`` check
                # below would skip validation, letting curly quotes slip into
                # the file and corrupt JSON.
                if _has_curly_quotes(new_string):
                    curly_found = [f"U+{ord(ch):04X}" for ch in new_string if ch in _CURLY_QUOTES]
                    logger.warning(
                        "PreToolUse JSON 校验拦截(弯引号): file=%s curly=%s",
                        file_path,
                        curly_found[:5],
                    )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                "操作被阻止：new_string 包含弯引号"
                                "（\u201c 或 \u201d），"
                                "这会破坏 JSON 格式。"
                                "请将所有弯引号替换为标准 ASCII "
                                "双引号 (U+0022) 后重试。"
                            ),
                        },
                    }

                p = Path(file_path)
                resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
                try:
                    current = resolved.read_text(encoding="utf-8")
                except OSError as read_err:
                    logger.info(
                        "JSON 校验 hook: tool=Edit file=%s skip=读取失败 error=%s",
                        file_path,
                        read_err,
                    )
                    return {}

                # Save backup for PostToolUse restore on corruption
                if json_backups is not None and _tool_use_id:
                    json_backups[_tool_use_id] = (resolved, current)

                if old_string not in current:
                    # Edit tool will fail on its own; no need to intervene.
                    logger.info(
                        "JSON 校验 hook: tool=Edit file=%s skip=old_string未匹配 old_len=%d new_len=%d file_len=%d",
                        file_path,
                        len(old_string),
                        len(new_string),
                        len(current),
                    )
                    return {}

                replace_all = tool_input.get("replace_all", False)
                if replace_all:
                    simulated = current.replace(old_string, new_string)
                else:
                    simulated = current.replace(old_string, new_string, 1)

                logger.info(
                    "JSON 校验 hook: tool=Edit file=%s matched=True "
                    "old_len=%d new_len=%d simulated_len=%d replace_all=%s",
                    file_path,
                    len(old_string),
                    len(new_string),
                    len(simulated),
                    replace_all,
                )

            if simulated is None:
                return {}

            try:
                json.loads(simulated)
                logger.info(
                    "JSON 校验 hook: tool=%s file=%s result=valid",
                    tool_name,
                    file_path,
                )
                return {}
            except json.JSONDecodeError as exc:
                logger.warning(
                    "PreToolUse JSON 校验拦截: file=%s tool=%s error=%s",
                    file_path,
                    tool_name,
                    exc,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"操作被阻止：此次 {tool_name} 会导致 {file_path} "
                            f"变成无效 JSON。错误：{exc}。"
                            "请检查你的输入内容中是否包含未转义的双引号或其他"
                            "JSON 语法问题，修正后重试。"
                        ),
                    },
                }

        return _json_validation_hook

    def _build_json_post_validation_hook(
        self,
        project_cwd: Path,
        json_backups: dict[str, tuple[Path, str]],
    ) -> Callable[..., Any]:
        """Build a PostToolUse hook that validates JSON files after Write/Edit.

        This is a safety net for cases where the PreToolUse simulation fails
        to catch invalid edits (e.g. due to old_string mismatch or escaping
        differences between the hook simulation and the actual Edit tool).

        If the file is invalid JSON after the edit, the hook:
        1. Restores the file from the backup saved by the PreToolUse hook
        2. Returns ``additionalContext`` telling the agent what went wrong
        """

        async def _json_post_validation_hook(
            input_data: dict[str, Any],
            tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            # Top-level guard: unhandled exceptions in hooks interrupt the
            # agent (per SDK docs), so we catch everything and log.
            try:
                return await _json_post_validation_impl(
                    input_data,
                    tool_use_id,
                )
            except Exception:
                logger.exception("PostToolUse JSON 校验 hook 异常")
                return {}

        async def _json_post_validation_impl(
            input_data: dict[str, Any],
            tool_use_id: str | None,
        ) -> dict[str, Any]:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            file_path = tool_input.get("file_path", "")
            if not file_path or not file_path.endswith(".json"):
                return {}

            # Pop the backup regardless of outcome to avoid memory leaks
            backup = json_backups.pop(tool_use_id, None) if tool_use_id else None

            p = Path(file_path)
            resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()

            try:
                actual = resolved.read_text(encoding="utf-8")
            except OSError:
                return {}

            try:
                json.loads(actual)
                logger.info(
                    "PostToolUse JSON 校验: tool=%s file=%s result=valid",
                    tool_name,
                    file_path,
                )
                return {}
            except json.JSONDecodeError as exc:
                # File is corrupt — restore from backup if available
                restored = False
                if backup:
                    backup_path, backup_content = backup
                    try:
                        backup_path.write_text(backup_content, encoding="utf-8")
                        restored = True
                        logger.warning(
                            "PostToolUse JSON 校验拦截并恢复: file=%s tool=%s error=%s backup_restored=True",
                            file_path,
                            tool_name,
                            exc,
                        )
                    except OSError as write_err:
                        logger.error(
                            "PostToolUse JSON 备份恢复失败: file=%s error=%s",
                            file_path,
                            write_err,
                        )
                else:
                    logger.warning(
                        "PostToolUse JSON 校验拦截(无备份): file=%s tool=%s error=%s",
                        file_path,
                        tool_name,
                        exc,
                    )

                if restored:
                    ctx = (
                        f"⚠ JSON 损坏已检测并回滚：{tool_name} 导致 "
                        f"{file_path} 变成无效 JSON（{exc}）。"
                        "文件已恢复到编辑前状态，请修正后重试。"
                    )
                else:
                    ctx = (
                        f"⚠ JSON 损坏已检测但无法恢复：{tool_name} 导致 "
                        f"{file_path} 变成无效 JSON（{exc}）。"
                        "文件当前仍为损坏状态（无可用备份或恢复写入失败），"
                        "请先读取文件确认内容，再手动修正为合法 JSON。"
                    )

                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": ctx,
                    },
                }

        return _json_post_validation_hook

    def _resolve_project_cwd(self, project_name: str) -> Path:
        """Resolve and validate per-session project working directory."""
        projects_root = self.projects_root
        project_cwd = (projects_root / project_name).resolve()
        try:
            project_cwd.relative_to(projects_root)
        except ValueError as exc:
            raise ValueError("invalid project name") from exc
        if not project_cwd.exists() or not project_cwd.is_dir():
            raise FileNotFoundError(f"project not found: {project_name}")
        return project_cwd

    def _make_actor_message_callback(
        self,
        managed_ref: list["ManagedSession | None"],
    ) -> Callable[[Any], None]:
        """Sync on_message callback shared by send_new_session and get_or_connect.

        Runs inside the actor task. Order is load-bearing:
        duplicate-echo detection skips buffer-add but still queues the message
        for async sdk_session_id capture; _handle_special_message must mutate
        result messages with `session_status` before subscribers see them via
        add_message; _inbox hand-off last so async post-processing never
        observes a message that hasn't been broadcast yet.
        """

        def _on_message(raw_msg: Any) -> None:
            managed = managed_ref[0]
            if managed is None:
                return
            msg_dict = self._message_to_dict(raw_msg)
            if not isinstance(msg_dict, dict):
                return
            if self._is_duplicate_user_echo(managed, msg_dict):
                managed._inbox.put_nowait(msg_dict)
                return
            self._handle_special_message(managed, msg_dict)
            managed._on_actor_message(msg_dict)
            managed._inbox.put_nowait(msg_dict)

        return _on_message

    def _make_actor_done_callback(
        self,
        managed: "ManagedSession",
    ) -> Callable[[asyncio.Task], None]:
        """Actor task done_callback: push inbox sentinel + persist error state.

        On actor task exit the inbox processor is signalled via None sentinel
        so it can drain cleanly. If the actor died with an exception, we flip
        the session to `error` in memory and schedule a meta_store persist so
        the DB doesn't stay stuck on `running` after a crash.
        """

        def _on_done(task: asyncio.Task) -> None:
            try:
                managed._inbox.put_nowait(None)
            except Exception:
                logger.debug("inbox sentinel push failed", exc_info=True)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                return
            logger.warning(
                "session actor 异常退出 session_id=%s: %s",
                managed.session_id,
                exc,
            )
            managed.status = "error"
            try:
                managed.add_message(self._build_runtime_status_message("error", managed.session_id))
            except Exception:
                logger.debug("broadcast runtime_status after actor failure failed", exc_info=True)
            # Persist error state so DB doesn't stay at "running" after a crash.
            asyncio.create_task(self._persist_actor_error_status(managed.session_id))

        return _on_done

    async def _persist_actor_error_status(self, session_id: str) -> None:
        try:
            await self.meta_store.update_status(session_id, "error")
        except Exception:
            logger.exception("持久化 actor error 状态失败 session_id=%s", session_id)

    async def send_new_session(
        self,
        project_name: str,
        prompt: str | AsyncIterable[dict],
        *,
        echo_text: str | None = None,
        echo_content: list[dict[str, Any]] | None = None,
        locale: str = "zh",
    ) -> str:
        """Create a new session via send-first: start actor, send query, wait for sdk_session_id."""
        if not SDK_AVAILABLE or ClaudeSDKClient is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        await self._ensure_capacity()
        temp_id = uuid4().hex
        managed_ref: list[ManagedSession | None] = [None]

        options = self._build_options(
            project_name,
            resume_id=None,
            can_use_tool=await self._build_can_use_tool_callback(temp_id, managed_ref),
            locale=locale,
        )

        actor = SessionActor(
            client_factory=lambda: ClaudeSDKClient(options=options),
            on_message=self._make_actor_message_callback(managed_ref),
        )

        managed = ManagedSession(
            session_id=temp_id,
            actor=actor,
            status="running",
            project_name=project_name,
        )
        managed_ref[0] = managed
        managed.last_activity = time.monotonic()
        self.sessions[temp_id] = managed

        try:
            await actor.start()
        except Exception:
            logger.exception("新会话 actor 启动失败 temp_id=%s", temp_id)
            self.sessions.pop(temp_id, None)
            raise

        # Register done callback BEFORE spawning processor to avoid a race
        # where the actor task completes before add_done_callback is attached,
        # leaving the None sentinel un-pushed and _process_inbox hanging.
        actor.add_done_callback(self._make_actor_done_callback(managed))

        # Spawn inbox processor BEFORE sending query so we don't miss messages.
        managed._process_task = asyncio.create_task(
            self._process_inbox(managed),
            name=f"inbox-{temp_id}",
        )

        async def _cleanup_on_error() -> None:
            """Unified cleanup for failure paths after _process_task spawn.

            Runs send_disconnect first (which causes actor to exit and
            _on_actor_done to push the None sentinel, letting _process_inbox
            finish naturally), then belt-and-suspenders cancels the processor
            in case it is stuck elsewhere.
            """
            self.sessions.pop(temp_id, None)
            try:
                await managed.send_disconnect()
            except Exception:
                logger.exception(
                    "send_disconnect on error path failed session_id=%s",
                    temp_id,
                )
            if managed._process_task is not None and not managed._process_task.done():
                managed._process_task.cancel()
                await asyncio.gather(managed._process_task, return_exceptions=True)

        # Echo user message
        display_text = echo_text or (prompt if isinstance(prompt, str) else "")
        dedup_key = display_text or (self._IMAGE_ONLY_SENTINEL if echo_content else "")
        if dedup_key:
            managed.pending_user_echoes.append(dedup_key)
        managed.add_message(self._build_user_echo_message(display_text, echo_content))

        try:
            await managed.send_query(prompt)
        except Exception:
            logger.exception("新会话消息发送失败")
            await _cleanup_on_error()
            raise

        # Wait for sdk_session_id with timeout; also monitor actor task so we
        # fail fast if the background task crashes before the event fires.
        event_task = asyncio.create_task(managed.sdk_id_event.wait())
        watch_tasks: set[asyncio.Task] = {event_task}
        actor_task = actor.task
        if actor_task is not None:
            watch_tasks.add(actor_task)
        try:
            await asyncio.wait(
                watch_tasks,
                timeout=self._SDK_ID_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not event_task.done():
                event_task.cancel()

        if not managed.sdk_id_event.is_set():
            if actor_task is not None and actor_task.done():
                logger.error("session actor 提前退出，未获得 sdk_session_id temp_id=%s", temp_id)
            else:
                logger.error("等待 sdk_session_id 超时 temp_id=%s", temp_id)
            managed.cancel_pending_questions("session creation timed out")
            await _cleanup_on_error()
            raise TimeoutError("SDK 会话创建超时")

        sdk_id = managed.resolved_sdk_id
        assert sdk_id is not None
        # Key swap already done in _on_sdk_session_id_received
        assert managed.session_id == sdk_id

        return sdk_id

    async def _process_inbox(self, managed: ManagedSession) -> None:
        """Drain ManagedSession._inbox and run async post-processing.

        Replaces the async tail of _consume_messages. The synchronous bits
        (state machine, buffer add, broadcast, _handle_special_message,
        duplicate-echo dedup) already ran inside the actor's on_message
        callback, so this coroutine only handles:
        - sdk_session_id capture (DB create, tag, key swap, event set)
        - _finalize_turn on result messages
        - terminal status on cancel/error
        """
        try:
            while True:
                msg_dict = await managed._inbox.get()
                if msg_dict is None:
                    return
                depth = managed._inbox.qsize()
                if not managed._inbox_warned and depth >= _INBOX_BACKLOG_WARN_THRESHOLD:
                    managed._inbox_warned = True
                    logger.warning(
                        "inbox backlog 过深 session_id=%s depth=%d (async post-processing 跟不上)",
                        managed.session_id,
                        depth,
                    )
                elif managed._inbox_warned and depth <= _INBOX_BACKLOG_RESET_THRESHOLD:
                    managed._inbox_warned = False
                # Short-circuit once sdk_session_id is captured: stream_event
                # messages can be very high-frequency and _extract_sdk_session_id
                # only yields on the init system message.
                if managed.resolved_sdk_id is None:
                    try:
                        await self._on_sdk_session_id_received(managed, None, msg_dict)
                    except Exception:
                        logger.exception(
                            "sdk_session_id 处理失败 session_id=%s",
                            managed.session_id,
                        )
                if msg_dict.get("type") == "result":
                    try:
                        await self._finalize_turn(managed, msg_dict)
                    except Exception:
                        # finalize 失败意味着 status/interrupt_requested/cleanup 可能部分未完成；
                        # 走终态兜底而非继续循环——继续会让下一轮看到不一致的残留状态。
                        logger.exception(
                            "_finalize_turn 失败，走 error 终态兜底 session_id=%s",
                            managed.session_id,
                        )
                        with contextlib.suppress(Exception):
                            await self._mark_session_terminal(managed, "error", "finalize failed")
                        return
        except asyncio.CancelledError:
            # Only mark interrupted if session was actually running. Cancel can
            # also happen during failed send_new_session cleanup or normal
            # shutdown, where the status is already terminal / error.
            if managed.status == "running":
                try:
                    await self._mark_session_terminal(managed, "interrupted", "session interrupted")
                except Exception:
                    logger.exception(
                        "_mark_session_terminal 在 cancel 路径失败 session_id=%s",
                        managed.session_id,
                    )
            raise
        except Exception:
            logger.exception("_process_inbox 异常 session_id=%s", managed.session_id)
            try:
                await self._mark_session_terminal(managed, "error", "session error")
            except Exception:
                logger.debug("_mark_session_terminal cleanup failed", exc_info=True)
            raise

    async def get_or_connect(self, session_id: str, *, meta: Optional["SessionMeta"] = None) -> ManagedSession:
        """Get existing managed session or spin up an actor for resumed session."""
        if session_id in self.sessions and session_id not in self._disconnecting:
            return self.sessions[session_id]

        # Per-session lock prevents concurrent connect() for the same session_id.
        if session_id not in self._connect_locks:
            self._connect_locks[session_id] = asyncio.Lock()
        lock = self._connect_locks[session_id]

        async with lock:
            # Re-check after acquiring lock
            if session_id in self.sessions and session_id not in self._disconnecting:
                return self.sessions[session_id]

            if meta is None:
                meta = await self.meta_store.get(session_id)
                if meta is None:
                    raise FileNotFoundError(f"session not found: {session_id}")

            if not SDK_AVAILABLE or ClaudeSDKClient is None:
                raise RuntimeError("claude_agent_sdk is not installed")

            await self._ensure_capacity()
            managed_ref: list[ManagedSession | None] = [None]
            options = self._build_options(
                meta.project_name,
                meta.id,  # SessionMeta.id 就是 sdk_session_id
                can_use_tool=await self._build_can_use_tool_callback(session_id, managed_ref),
            )

            actor = SessionActor(
                client_factory=lambda: ClaudeSDKClient(options=options),
                on_message=self._make_actor_message_callback(managed_ref),
            )

            resumed_status: SessionStatus = (
                meta.status if meta.status in ("idle", "running", "interrupted", "error", "closed") else "idle"
            )
            managed = ManagedSession(
                session_id=meta.id,  # 现在就是 sdk_session_id
                actor=actor,
                status=resumed_status,
                project_name=meta.project_name,
                resolved_sdk_id=meta.id,  # 标记为已注册，防止重复创建 DB 记录
            )
            managed.sdk_id_event.set()  # 已有会话不需要等待 sdk_id
            managed_ref[0] = managed
            managed.last_activity = time.monotonic()
            self.sessions[session_id] = managed

            try:
                await actor.start()
            except Exception:
                logger.exception("恢复会话 actor 启动失败 session_id=%s", session_id)
                self.sessions.pop(session_id, None)
                raise

            # done_callback BEFORE processor spawn (avoids race where actor
            # completes before the callback attaches and the None sentinel
            # is never pushed).
            actor.add_done_callback(self._make_actor_done_callback(managed))

            managed._process_task = asyncio.create_task(
                self._process_inbox(managed),
                name=f"inbox-{session_id}",
            )
            return managed

    async def send_message(
        self,
        session_id: str,
        prompt: str | AsyncIterable[dict],
        *,
        echo_text: str | None = None,
        echo_content: list[dict[str, Any]] | None = None,
        meta: Optional["SessionMeta"] = None,
    ) -> None:
        """Send a message via the session actor."""
        managed = await self.get_or_connect(session_id, meta=meta)
        managed.last_activity = time.monotonic()
        # 取消待执行的 cleanup（会话恢复活跃）
        if managed._cleanup_task and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()
            managed._cleanup_task = None

        if managed.status == "running":
            raise ValueError("会话正在处理中，请等待当前回复完成后再发送新消息")

        self._prune_transient_buffer(managed)

        # Determine the display text for echo dedup (pending_user_echoes).
        # For image-only messages display_text is empty; use a sentinel so the
        # SDK-replayed empty-content user message can still be deduplicated.
        display_text = echo_text or (prompt if isinstance(prompt, str) else "")
        dedup_key = display_text or (self._IMAGE_ONLY_SENTINEL if echo_content else "")

        # Echo user input immediately so live SSE shows it even when the SDK
        # stream doesn't replay user messages in real time. Don't set status to
        # "running" manually — send_query does it inside the actor.
        if dedup_key:
            managed.pending_user_echoes.append(dedup_key)
            if len(managed.pending_user_echoes) > 20:
                managed.pending_user_echoes.pop(0)
        managed.add_message(self._build_user_echo_message(display_text, echo_content))

        # Persist status asynchronously — don't block the echo broadcast
        await self.meta_store.update_status(session_id, "running")

        # Send the query via the actor. send_query flips status to error on
        # cmd.error and re-raises; we ensure meta store reflects that too.
        try:
            await managed.send_query(prompt, sdk_session_id=session_id)
        except Exception:
            logger.exception("会话消息处理失败")
            managed.pending_user_echoes.clear()
            try:
                await self.meta_store.update_status(session_id, "error")
            except Exception:
                logger.exception("持久化 error 状态失败 session_id=%s", session_id)
            raise

    async def interrupt_session(self, session_id: str) -> SessionStatus:
        """Interrupt a running session via the actor."""
        meta = await self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        managed = self.sessions.get(session_id)
        if managed is None:
            if meta.status == "running":
                await self.meta_store.update_status(session_id, "interrupted")
                return "interrupted"
            return meta.status

        if managed.status != "running":
            return managed.status

        managed.pending_user_echoes.clear()
        managed.interrupt_requested = True
        managed.cancel_pending_questions("session interrupted by user")

        try:
            await managed.send_interrupt()
        except Exception:
            logger.exception("发送 interrupt 命令失败 session_id=%s", session_id)
            managed.status = "error"
            return managed.status

        managed.last_activity = time.monotonic()
        # status 由 _on_actor_message 在收到 ResultMessage(error_during_execution) 时推导为 "interrupted"
        return managed.status

    def _handle_special_message(self, managed: ManagedSession, msg_dict: dict[str, Any]) -> None:
        """Handle compact_boundary and result messages before broadcast."""
        if msg_dict.get("type") == "system" and msg_dict.get("subtype") == "compact_boundary":
            self._prune_transient_buffer(managed)

        if msg_dict.get("type") == "result":
            msg_dict["session_status"] = self._resolve_result_status(
                msg_dict,
                interrupt_requested=managed.interrupt_requested,
            )

    async def _finalize_turn(self, managed: ManagedSession, result_msg: dict[str, Any]) -> None:
        """Settle session state after a result message completes a turn."""
        managed.pending_user_echoes.clear()
        managed.cancel_pending_questions("session completed")
        explicit = str(result_msg.get("session_status") or "").strip()
        final_status: SessionStatus = (
            explicit  # type: ignore[assignment]
            if explicit in {"idle", "running", "completed", "error", "interrupted"}
            else self._resolve_result_status(
                result_msg,
                interrupt_requested=managed.interrupt_requested,
            )
        )
        managed.status = final_status
        managed.last_activity = time.monotonic()
        await self.meta_store.update_status(managed.session_id, final_status)
        managed.interrupt_requested = False
        self._prune_transient_buffer(managed)
        if final_status != "running":
            self._schedule_cleanup(managed.session_id)

    async def _mark_session_terminal(self, managed: ManagedSession, status: SessionStatus, reason: str) -> None:
        """Set terminal status on abnormal consumer exit."""
        managed.pending_user_echoes.clear()
        managed.cancel_pending_questions(reason)
        managed.status = status
        managed.last_activity = time.monotonic()
        await self.meta_store.update_status(managed.session_id, status)
        managed.interrupt_requested = False
        self._prune_transient_buffer(managed)

        # For interrupted sessions, broadcast a synthetic interrupt echo so the
        # SSE projector generates an interrupt_notice turn.  This keeps the live
        # path consistent with the historical path where the SDK transcript
        # contains the CLI-injected interrupt echo that the turn_grouper converts.
        # The consumer task is already cancelled at this point so the SDK's own
        # echo will never arrive through the normal message pipeline.
        if status == "interrupted":
            managed._broadcast_to_subscribers(
                {
                    "type": "user",
                    "content": "[Request interrupted by user]",
                    "uuid": f"interrupt-echo-{uuid4().hex}",
                    "timestamp": _utc_now_iso(),
                }
            )

        # Broadcast terminal status so SSE subscribers unblock immediately
        # instead of waiting for the heartbeat timeout.
        managed._broadcast_to_subscribers(
            {
                "type": "runtime_status",
                "status": status,
                "reason": reason,
            }
        )
        self._schedule_cleanup(managed.session_id)

    def _schedule_cleanup(self, session_id: str) -> None:
        """Schedule delayed cleanup for a non-running session."""
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        if managed._cleanup_task is not None and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()
        managed._cleanup_task = asyncio.create_task(self._cleanup_idle(session_id))

    async def _cleanup_idle(self, session_id: str) -> None:
        try:
            delay = await self._get_cleanup_delay()
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        if managed.status in ("idle", "interrupted", "error", "completed"):
            # Clear our own reference first so _evict_one's cleanup-task cancel doesn't self-cancel
            managed._cleanup_task = None
            await self._evict_one(managed)

    async def close_session(self, session_id: str, *, reason: str = "session closed") -> None:
        """Public close entry — gracefully tears down the actor and removes the session."""
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        managed.cancel_pending_questions(reason)
        await self._evict_one(managed)

    async def _evict_one(self, managed: ManagedSession) -> None:
        """Gracefully disconnect an actor, cancel as fallback, and remove from registry."""
        session_id = managed.session_id
        if session_id in self._disconnecting:
            return
        self._disconnecting.add(session_id)
        try:
            # Cancel any pending cleanup timer first
            if managed._cleanup_task is not None and not managed._cleanup_task.done():
                managed._cleanup_task.cancel()
                with contextlib.suppress(BaseException):
                    await managed._cleanup_task

            try:
                await asyncio.wait_for(
                    managed.send_disconnect(),
                    timeout=self._session_actor_shutdown_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "actor disconnect 超时，走 cancel 兜底 session_id=%s",
                    session_id,
                )
                if managed.actor is not None:
                    await managed.actor.cancel_and_wait()
                managed.status = "interrupted"
            except Exception:
                logger.exception("actor 关停异常 session_id=%s", session_id)
                managed.status = "error"

            # Drain the inbox processor
            try:
                managed._inbox.put_nowait(None)
            except Exception:
                pass
            if managed._process_task is not None and not managed._process_task.done():
                try:
                    await asyncio.wait_for(managed._process_task, timeout=5.0)
                except TimeoutError:
                    managed._process_task.cancel()
                    with contextlib.suppress(BaseException):
                        await managed._process_task
                except BaseException:
                    logger.exception(
                        "_process_inbox 退出异常 session_id=%s",
                        session_id,
                    )

            # 若会话关闭时仍被标记为 running，持久化为终态以防进程重启后卡死：
            # send_message 已把 DB 写成 running；缺少此步 get_or_connect 恢复
            # 后会拒绝新消息（SessionStatus == "running"）。
            if managed.resolved_sdk_id is not None:
                if managed.status == "running":
                    managed.status = "interrupted"
                if managed.status in ("interrupted", "error"):
                    with contextlib.suppress(BaseException):
                        await self.meta_store.update_status(managed.resolved_sdk_id, managed.status)
        finally:
            self.sessions.pop(session_id, None)
            self._connect_locks.pop(session_id, None)
            self._disconnecting.discard(session_id)

    async def _get_cleanup_delay(self) -> int:
        """返回会话清理延迟秒数，默认 300（5 分钟）。"""
        try:
            async with async_session_factory() as session:
                svc = ConfigService(session)
                val = await svc.get_setting("agent_session_cleanup_delay_seconds", "300")
            return max(int(val), 10)
        except Exception:
            logger.warning("读取 cleanup delay 配置失败，使用默认值", exc_info=True)
            return 300

    async def _get_max_concurrent(self) -> int:
        """返回最大并发会话数，默认 5。"""
        try:
            async with async_session_factory() as session:
                svc = ConfigService(session)
                val = await svc.get_setting("agent_max_concurrent_sessions", "5")
            return max(int(val), 1)
        except Exception:
            logger.warning("读取 max_concurrent 配置失败，使用默认值", exc_info=True)
            return 5

    async def _ensure_capacity(self) -> None:
        """确保有空余并发槽位，必要时淘汰最久未活跃的非 running 会话。"""
        max_concurrent = await self._get_max_concurrent()
        active = [s for s in self.sessions.values() if s.actor is not None and s.session_id not in self._disconnecting]

        if len(active) < max_concurrent:
            return

        # 可淘汰的会话：非 running 状态（idle / completed / error / interrupted）
        evictable = sorted(
            [s for s in active if s.status != "running"],
            key=lambda s: s.last_activity or 0,
        )

        if evictable:
            victim = evictable[0]
            logger.info(
                "并发上限，淘汰 session_id=%s (status=%s)",
                victim.session_id,
                victim.status,
            )
            try:
                await self._evict_one(victim)
            except Exception as exc:
                logger.error(
                    "淘汰会话失败，无法释放并发槽位 session_id=%s",
                    victim.session_id,
                    exc_info=True,
                )
                raise SessionCapacityError("存在未能关闭的空闲会话，当前无法释放并发槽位，请稍后重试") from exc
            return

        # 所有会话都在 running → 拒绝
        raise SessionCapacityError(f"当前有{len(active)}个正在进行的会话，已达到最大上限，请稍后重试")

    _PATROL_INTERVAL = 300  # 5 分钟

    async def _patrol_once(self) -> None:
        """单次巡检：清理所有超时的非 running 会话。"""
        cleanup_delay = await self._get_cleanup_delay()
        now = time.monotonic()
        for sid, managed in list(self.sessions.items()):
            if managed.status == "running" or sid in self._disconnecting:
                continue
            activity_age = now - (managed.last_activity or 0)
            if activity_age > cleanup_delay * 2:
                logger.info("巡检兜底清理会话 session_id=%s status=%s", sid, managed.status)
                try:
                    m = self.sessions.get(sid)
                    if m is not None:
                        await self._evict_one(m)
                except Exception:
                    logger.warning(
                        "巡检兜底清理失败 session_id=%s",
                        sid,
                        exc_info=True,
                    )

    async def _patrol_loop(self) -> None:
        """后台定期巡检循环。"""
        while True:
            await asyncio.sleep(self._PATROL_INTERVAL)
            try:
                await self._patrol_once()
            except Exception:
                logger.warning("巡检循环异常", exc_info=True)

    def start_patrol(self) -> None:
        """启动巡检后台任务（应在应用 startup 时调用）。"""
        self._patrol_task = asyncio.create_task(self._patrol_loop())

    @staticmethod
    def _resolve_result_status(
        result_message: dict[str, Any],
        interrupt_requested: bool = False,
    ) -> SessionStatus:
        """Map SDK result subtype/is_error to runtime session status."""
        subtype = str(result_message.get("subtype") or "").strip().lower()
        is_error = bool(result_message.get("is_error"))
        if interrupt_requested:
            if subtype in {"interrupted", "interrupt"}:
                return "interrupted"
            if is_error or subtype.startswith("error"):
                return "interrupted"
        if is_error or subtype.startswith("error"):
            return "error"
        return "completed"

    # Base directory where the SDK stores per-project session data.
    _CLAUDE_PROJECTS_DIR: Path = Path.home() / ".claude" / "projects"

    @staticmethod
    def _encode_sdk_project_path(project_cwd: Path) -> str:
        """Encode a project cwd the same way the SDK does for session storage.

        Uses the same scheme as transcript_reader.py and the SDK itself:
        replace ``/`` and ``.`` with ``-``.
        """
        return project_cwd.as_posix().replace("/", "-").replace(".", "-")

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
        #    When tool output exceeds the inline limit, the SDK saves the
        #    full result to ~/.claude/projects/{encoded-cwd}/{session}/
        #    tool-results/{id}.txt and instructs the agent to Read it.
        #    Only tool-results/ subdirectories are allowed — other SDK
        #    session data (transcripts, etc.) remains inaccessible.
        encoded = self._encode_sdk_project_path(project_cwd)
        sdk_project_dir = self._CLAUDE_PROJECTS_DIR / encoded
        if resolved.is_relative_to(sdk_project_dir) and "tool-results" in resolved.parts:
            return True, None

        # 5. Read tools: allow SDK task output files.
        #    Background tasks (Agent/Bash run_in_background) write their
        #    output to /tmp/claude-{N}/{encoded-cwd}/tasks/{id}.output.
        #    The SDK instructs the agent to Read the file after the task
        #    completes.  Only the tasks/ subdirectory is allowed.
        #    macOS: /tmp → /private/tmp symlink, so check both prefixes.
        _SDK_TMP_PREFIXES = ("/tmp/claude-", "/private/tmp/claude-")
        resolved_str = str(resolved)
        if resolved_str.startswith(_SDK_TMP_PREFIXES) and "tasks" in resolved.parts:
            return True, None

        return False, "访问被拒绝：不允许访问当前项目和公共目录之外的路径"

    async def _handle_ask_user_question(
        self,
        managed: Optional["ManagedSession"],
        tool_name: str,
        input_data: dict[str, Any],
    ) -> Any:
        """Handle AskUserQuestion tool invocation within can_use_tool callback."""
        if managed is None:
            return PermissionResultAllow(updated_input=input_data)

        raw_questions = input_data.get("questions")
        questions = raw_questions if isinstance(raw_questions, list) else []
        payload = {
            "type": "ask_user_question",
            "question_id": f"aq_{uuid4().hex}",
            "tool_name": tool_name,
            "questions": questions,
            "timestamp": _utc_now_iso(),
        }
        pending = managed.add_pending_question(payload)
        managed.add_message(payload)

        try:
            answers = await pending.answer_future
        except Exception as exc:
            if PermissionResultDeny is not None:
                return PermissionResultDeny(
                    message=str(exc) or "session interrupted by user",
                    interrupt=True,
                )
            raise
        merged_input = dict(input_data or {})
        merged_input["answers"] = answers
        return PermissionResultAllow(updated_input=merged_input)

    async def _build_can_use_tool_callback(
        self,
        session_id: str,
        managed_ref: list[Optional["ManagedSession"]] | None = None,
    ):
        """Create per-session can_use_tool callback (default-deny).

        This is step 5 (final fallback) in the SDK permission chain:
        Hooks → Deny rules → Permission mode → Allow rules → canUseTool.
        Only reached when prior steps don't resolve the decision.

        File access control uses the PreToolUse hook (step 1) because it
        fires for ALL tool calls.  Read/Glob/Grep are resolved by allow
        rules (step 4) and never reach this callback.

        This callback handles AskUserQuestion (async user interaction) and
        denies everything else as a whitelist fallback.

        Args:
            session_id: Initial session ID (may be temp_id for new sessions).
            managed_ref: Mutable single-element list holding the ManagedSession.
                When provided, the callback resolves the session via this
                reference instead of looking up session_id in self.sessions,
                so it survives the temp_id → sdk_id key swap.
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
                managed = managed_ref[0] if managed_ref else self.sessions.get(session_id)
                return await self._handle_ask_user_question(
                    managed,
                    tool_name,
                    input_data,
                )

            # Whitelist fallback: deny any tool that was not pre-approved
            # by allowed_tools or settings.json allow rules.
            if PermissionResultDeny is not None:
                reason = getattr(_context, "decision_reason", None)  # SDK 0.1.74+
                reason_line = f"上游决策原因: {reason}\n" if reason else ""
                hint = (
                    f"未授权的工具调用: {tool_name}"
                    f"({json.dumps(input_data, ensure_ascii=False)[:200]})\n"
                    f"{reason_line}"
                    "当前 Bash 白名单仅允许以下命令:\n"
                    "  - python .claude/skills/<skill>/scripts/<script>.py <args>（必须用相对路径）\n"
                    "  - ffmpeg / ffprobe\n"
                    "其他 Bash 命令均不可用。"
                    "请检查命令格式是否匹配白名单规则。"
                )
                return PermissionResultDeny(message=hint)
            return PermissionResultAllow(updated_input=input_data)

        return _can_use_tool

    def _message_to_dict(self, message: Any) -> dict[str, Any]:
        """Convert SDK message to dict for JSON serialization."""
        msg_dict = self._serialize_value(message)

        # Infer and add message type if not present
        if isinstance(msg_dict, dict) and "type" not in msg_dict:
            msg_type = self._infer_message_type(message)
            if msg_type:
                msg_dict["type"] = msg_type

        # Inject precise subtype for typed task messages
        if isinstance(msg_dict, dict):
            class_name = type(message).__name__
            subtype = self._TASK_MESSAGE_SUBTYPES.get(class_name)
            if subtype:
                msg_dict["subtype"] = subtype

        return msg_dict

    @staticmethod
    def _build_user_echo_message(
        text: str,
        content_blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build a synthetic user message for real-time UI echo.

        When content_blocks is provided (e.g. image + text blocks), the echo
        content is a list of blocks so the UI can render image thumbnails in
        the bubble.  If no blocks are provided, content is the plain text string.
        """
        content: Any = content_blocks if content_blocks is not None else text
        return {
            "type": "user",
            "content": content,
            "uuid": f"local-user-{uuid4().hex}",
            "timestamp": _utc_now_iso(),
            "local_echo": True,
        }

    @staticmethod
    def _prune_transient_buffer(managed: ManagedSession) -> None:
        """Drop stale messages that should not leak into next round snapshots.

        Removes:
        - stream_event / runtime_status: transient streaming artifacts
        - user / assistant / result: already persisted in SDK transcript;
          keeping them causes duplicate turns because buffer messages lack
          the uuid that transcript messages carry, so _merge_raw_messages
          cannot deduplicate them.
        """
        if not managed.message_buffer:
            return
        managed.message_buffer = [
            message
            for message in managed.message_buffer
            if message.get("type")
            not in {
                "stream_event",
                "runtime_status",
                "user",
                "assistant",
                "result",
            }
        ]

    @staticmethod
    def _build_runtime_status_message(
        status: SessionStatus,
        session_id: str,
    ) -> dict[str, Any]:
        """Build runtime-only status message for SSE wake-up."""
        return {
            "type": "runtime_status",
            "status": status,
            "subtype": status,
            "stop_reason": None,
            "is_error": status == "error",
            "session_id": session_id,
            "uuid": f"runtime-status-{uuid4().hex}",
            "timestamp": _utc_now_iso(),
        }

    _extract_plain_user_content = staticmethod(extract_plain_user_content)

    def _is_duplicate_user_echo(
        self,
        managed: ManagedSession,
        message: dict[str, Any],
    ) -> bool:
        """Skip SDK-replayed user message if it matches local echo queue."""
        if not managed.pending_user_echoes:
            return False
        incoming = self._extract_plain_user_content(message)
        expected = managed.pending_user_echoes[0].strip()

        # Image-only sentinel: the SDK parser drops image blocks, so the
        # replayed UserMessage arrives with empty content (incoming is None).
        if not incoming:
            if message.get("type") != "user" or expected != self._IMAGE_ONLY_SENTINEL:
                return False
            managed.pending_user_echoes.pop(0)
            return True

        if incoming != expected:
            return False
        managed.pending_user_echoes.pop(0)
        return True

    async def _on_sdk_session_id_received(
        self,
        managed: ManagedSession,
        message: Any,
        msg_dict: dict[str, Any],
    ) -> None:
        """Handle sdk_session_id from stream. For new sessions: create DB record + signal event."""
        sdk_id = self._extract_sdk_session_id(message, msg_dict)
        if not sdk_id:
            return
        if managed.resolved_sdk_id is not None:
            return  # Already registered

        managed.resolved_sdk_id = sdk_id

        # Only create DB record for new sessions (no existing meta)
        if not managed.sdk_id_event.is_set():
            # Run DB create and SDK tag in parallel (tag is independent file I/O)
            tag_coro = None
            if tag_session is not None:

                async def _tag() -> None:
                    try:
                        await asyncio.to_thread(tag_session, sdk_id, f"project:{managed.project_name}")
                    except Exception:
                        logger.warning("tag_session failed for %s", sdk_id, exc_info=True)

                tag_coro = _tag()
            await asyncio.gather(
                self.meta_store.create(managed.project_name, sdk_id),
                *([] if tag_coro is None else [tag_coro]),
            )
            await self.meta_store.update_status(sdk_id, "running")
            # Key swap: replace temp_id with real sdk_id in sessions dict
            # BEFORE signaling the event. This prevents _finalize_turn from
            # using the stale temp_id if it runs before send_new_session
            # completes its own key swap.
            old_id = managed.session_id
            if old_id != sdk_id and old_id in self.sessions:
                del self.sessions[old_id]
                managed.session_id = sdk_id
                self.sessions[sdk_id] = managed
            managed.sdk_id_event.set()

    @staticmethod
    def _extract_sdk_session_id(message: Any, msg_dict: dict[str, Any]) -> str | None:
        """Extract SDK session id from either serialized payload or raw object."""
        sdk_id = None
        if isinstance(msg_dict, dict):
            sdk_id = msg_dict.get("session_id") or msg_dict.get("sessionId")
        if sdk_id:
            return str(sdk_id)
        raw_sdk_id = getattr(message, "session_id", None) or getattr(message, "sessionId", None)
        if raw_sdk_id:
            return str(raw_sdk_id)
        return None

    def _infer_message_type(self, message: Any) -> str | None:
        """Infer message type from SDK message class name."""
        class_name = type(message).__name__
        return self._MESSAGE_TYPE_MAP.get(class_name)

    def _serialize_value(self, value: Any) -> Any:
        """Recursively serialize a value to JSON-safe types."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value

        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}

        if isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]

        # Pydantic models — mode="json" 一次产出 JSON 安全结构，避免再次递归
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")

        # Dataclasses or objects with __dict__
        if hasattr(value, "__dict__"):
            return {k: self._serialize_value(v) for k, v in value.__dict__.items() if not k.startswith("_")}

        # Fallback: convert to string
        return str(value)

    async def get_message_buffer_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        """Get current message buffer without creating a new SDK connection."""
        managed = self.sessions.get(session_id)
        if not managed:
            return []
        return list(managed.message_buffer)

    def get_buffered_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Sync helper for consumers that only need in-memory buffer state."""
        managed = self.sessions.get(session_id)
        if not managed:
            return []
        return list(managed.message_buffer)

    async def get_pending_questions_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        """Get unresolved AskUserQuestion payloads for reconnect."""
        managed = self.sessions.get(session_id)
        if not managed:
            return []
        return managed.get_pending_question_payloads()

    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
    ) -> None:
        """Resolve AskUserQuestion answers for a running session."""
        managed = self.sessions.get(session_id)
        if managed is None:
            raise ValueError("会话未运行或无待回答问题")
        if managed.status != "running":
            raise ValueError("会话未运行或无待回答问题")
        if not managed.resolve_pending_question(question_id, answers):
            raise ValueError("未找到待回答的问题")

    async def subscribe(self, session_id: str, replay_buffer: bool = True) -> asyncio.Queue:
        """Subscribe to session messages. Returns queue for SSE."""
        managed = await self.get_or_connect(session_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        if replay_buffer:
            # Replay buffered messages
            for msg in managed.message_buffer:
                try:
                    queue.put_nowait(msg)
                except asyncio.QueueFull:
                    break

        managed.subscribers.add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe from session messages."""
        if session_id in self.sessions:
            self.sessions[session_id].subscribers.discard(queue)

    async def get_status(self, session_id: str) -> SessionStatus | None:
        """Get session status."""
        if session_id in self.sessions:
            return self.sessions[session_id].status
        meta = await self.meta_store.get(session_id)
        return meta.status if meta else None

    async def shutdown_gracefully(self, timeout: float = 30.0) -> None:
        """Gracefully shutdown all sessions using the actor teardown path."""
        patrol = getattr(self, "_patrol_task", None)
        if patrol is not None and not patrol.done():
            patrol.cancel()
            with contextlib.suppress(BaseException):
                await patrol

        sessions = list(self.sessions.values())
        if not sessions:
            return
        await asyncio.gather(
            *[self._evict_one(s) for s in sessions],
            return_exceptions=True,
        )
