"""
Manages ClaudeSDKClient instances with background execution and reconnection support.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from lib.db.base import DEFAULT_USER_ID
from lib.i18n import DEFAULT_LOCALE
from lib.logging_config import resolve_log_dir
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.entry_pipeline import SessionEntryPipeline
from server.agent_runtime.event_log import (
    REPLAYED_USER_ECHO_KEY,
    EventLogStore,
)
from server.agent_runtime.message_serialization import (
    IMAGE_ONLY_SENTINEL,
    build_runtime_status_message,
    is_duplicate_user_echo,
    message_to_dict,
    utc_now_iso,
)
from server.agent_runtime.models import (
    Heartbeat,
    LiveMessage,
    SessionMeta,
    SessionStatus,
    SessionStreamEvent,
    SubscriptionReady,
)
from server.agent_runtime.options_assembler import OptionsAssembler
from server.agent_runtime.session_actor import SessionActor, SessionCommand
from server.agent_runtime.session_store import SessionMetaStore
from server.agent_runtime.usage_extraction import (
    extract_assistant_cost,
    extract_text_token_usage,
    resolve_assistant_model,
    resolve_configured_assistant_model,
)
from server.sse_channel import IDLE, EvictNonCriticalAndSignal, SseChannel

logger = logging.getLogger(__name__)

from claude_agent_sdk import ClaudeSDKClient, tag_session
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
)

from lib.config.service import ConfigService
from lib.db import async_session_factory
from lib.providers import PROVIDER_ANTHROPIC
from lib.usage_tracker import UsageTracker

SDK_AVAILABLE = True


# inbox 积压告警阈值：~1s 内 100 条 stream_event（典型流式频率上限）；
# 持续高于此值说明 _process_inbox 被阻塞或下游 I/O 超慢。
_INBOX_BACKLOG_WARN_THRESHOLD = 100
_INBOX_BACKLOG_RESET_THRESHOLD = 50  # 降至此水位以下才重置告警状态，避免抖动刷屏

# SDK stderr 缓冲上限（行）：actor.start() 失败时启动期 stderr 一般 <20 行；
# 上限主要为应对启动成功后 SDK 在会话存活期间持续输出 stderr 的场景，cap
# 在 200 行 × 平均行长，单会话最坏占用 <100KB，可控。
_SDK_STDERR_BUFFER_MAX = 200


class SessionCapacityError(Exception):
    """所有并发槽位已被 running 会话占满，无法创建新连接。"""

    pass


class AgentStartupError(RuntimeError):
    """ClaudeSDKClient 启动失败时携带 SDK stderr 的异常。

    SDK 内部用 ``ProcessError`` 抛子进程非 0 退出，但其 ``stderr`` 字段写死为
    ``"Check stderr output for details"`` —— 真实 stderr 只能通过
    ``ClaudeAgentOptions.stderr`` 回调拿到。本异常把回调收集的 stderr 行打包
    透传给 router/前端，让用户能看到 SDK 给出的安装指引（例如 Windows 缺
    bash.exe / pwsh.exe 时的下载链接）。

    ``__str__`` 直接返回 message + stderr 的完整拼接，让 router 的通用
    ``except Exception: str(exc)`` 分支也能自动透传，不需要每条路径都加专门
    捕获。
    """

    def __init__(self, message: str, sdk_stderr: str = "") -> None:
        self.message = message
        self.sdk_stderr = sdk_stderr
        super().__init__(self._compose())

    def _compose(self) -> str:
        if self.sdk_stderr:
            return f"{self.message}\n\n{self.sdk_stderr}"
        return self.message


@dataclass
class PendingQuestion:
    """Tracks a pending AskUserQuestion request."""

    question_id: str
    payload: dict[str, Any]
    answer_future: asyncio.Future[dict[str, str]]


def _make_session_channel() -> SseChannel:
    """会话订阅广播通道：溢出策略为「逐出非关键消息 + 溢出信号」。

    关键消息（result/runtime_status/user/assistant）不得静默丢弃；订阅者
    队列彻底跟不上时其流被结束，流结束即重连信号（见 docs/adr/0046）。
    """
    return SseChannel(
        overflow=EvictNonCriticalAndSignal(
            is_critical=lambda message: message.get("type") in ManagedSession._CRITICAL_MESSAGE_TYPES,
        ),
    )


@dataclass
class ManagedSession:
    """A managed ClaudeSDKClient session."""

    session_id: str  # sdk_session_id（已有会话）或临时 UUID（新会话等待中）
    actor: "SessionActor"  # per-session actor owning the SDK client
    status: SessionStatus = "idle"
    project_name: str = ""  # 用于 _register_new_session
    sdk_id_event: asyncio.Event = field(default_factory=asyncio.Event)
    resolved_sdk_id: str | None = None  # consumer 设置，send_new_session 读取
    channel: SseChannel = field(default_factory=_make_session_channel)
    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    pending_user_echoes: list[str] = field(default_factory=list)
    # 事件日志写入点管道（UI 时间线唯一读源的 live 写侧）。
    entry_pipeline: SessionEntryPipeline | None = None
    # 新会话首条用户消息：sdk_session_id 就绪后由 inbox 任务写入日志（seq 0），
    # 保证用户条目先于任何 assistant 条目分配身份。
    pending_initial_user_entry: dict[str, Any] | None = None
    initial_user_log_entry: dict[str, Any] | None = None
    last_user_prompt: str = ""
    assistant_model: str = ""
    interrupt_requested: bool = False
    last_activity: float | None = None  # updated on every send/receive
    _cleanup_task: asyncio.Task | None = None  # current cleanup timer (idle TTL or terminal delay)
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue)  # async post-processing queue
    _inbox_warned: bool = False  # edge-triggered backlog warning state
    _process_task: asyncio.Task | None = None  # per-session async inbox processor
    _interrupting: bool = False  # send_interrupt re-entry guard (distinct from interrupt_requested)

    # Message types that must never be silently dropped from subscriber queues.
    # 原始 assistant/result 仍是关键类型：同步 agent 对话端点直接消费它们收集回复。
    _CRITICAL_MESSAGE_TYPES = {"result", "runtime_status", "assistant", "log_entry", "log_turn_complete"}

    def _on_actor_message(self, msg: dict[str, Any]) -> None:
        """SessionActor 的 on_message 回调。同步，内存操作，不 await。

        职责：向订阅者广播消息。

        **状态转换不在此处做**——managed.status 由 _finalize_turn 在异步路径中
        统一设置。若在此提前切换为 idle/completed，`send_message` 的并发保护
        （拦截 status=="running"）会在 _finalize_turn 跑完前失效，下一轮消息
        可能进入，随后上一轮 finalize 回写/清理会误伤新一轮。

        pending_questions 注册由 SessionManager._handle_special_message 处理。
        """
        self.channel.broadcast(msg)

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

    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        meta_store: SessionMetaStore,
        projects_root: Path | None = None,
        in_docker: bool = False,
        sandbox_enabled: bool = True,
        event_log_store: EventLogStore | None = None,
    ):
        self.event_log_store = event_log_store or EventLogStore()
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
        # 实例不变量缓存：避免每次构建 access policy 都重做 path resolve。
        self._project_root_resolved = self.project_root.resolve()
        # agent_runtime_profile 实际位置：``ARCREEL_PROFILE_DIR`` env 覆盖 >
        # ``self.project_root / "agent_runtime_profile"``（test-friendly：
        # 不读 ``lib.env_init.PROJECT_ROOT`` 全局）。
        profile_override = os.getenv("ARCREEL_PROFILE_DIR", "").strip()
        if profile_override:
            self._agent_profile_root = Path(profile_override).expanduser().resolve(strict=False)
        else:
            self._agent_profile_root = (self._project_root_resolved / "agent_runtime_profile").resolve(strict=False)
        # 访问规则真相源：env 解析（profile / 日志目录）在此完成，policy 只消费
        # resolve 后的进程级根路径（零 I/O 纯构造）。用 resolve_log_dir() 拿日志
        # 真实路径，覆盖 ``ARCREEL_LOG_DIR`` 自定义场景——无论落在 repo 内还是外
        # 都必须 deny。
        self.access_policy = AgentAccessPolicy(
            project_root=self._project_root_resolved,
            projects_root=self.projects_root,
            agent_profile_root=self._agent_profile_root,
            log_dir=resolve_log_dir().resolve(),
            sandbox_enabled=sandbox_enabled,
            in_docker=in_docker,
        )
        self._load_config()
        self.usage_tracker = UsageTracker(session_factory=getattr(meta_store, "_session_factory", None))
        # Options 装配器：持依赖、允许 I/O，异步 build 产出 SDK options。access_policy /
        # max_turns / session_factory / user_id 一律用 provider 回调现取——前两者
        # configure_sandbox_runtime / refresh_config 换新后对后续会话立即生效；后两者
        # 保持析出前惰性读 self 属性的时点，与用量记录侧 _finalize_turn 实时读 _user_id
        # 同源，避免 store 与用量落到不同 per-user 命名空间。_resolve_project_cwd
        # （项目名校验/作用域）留在会话管理侧，作为依赖注入。
        self._options_assembler = OptionsAssembler(
            data_dir=self.data_dir,
            projects_root=self.projects_root,
            allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
            setting_sources=self.DEFAULT_SETTING_SOURCES,
            access_policy_provider=lambda: self.access_policy,
            max_turns_provider=lambda: self.max_turns,
            resolve_project_cwd=self._resolve_project_cwd,
            session_factory_provider=lambda: getattr(self, "_session_factory", None),
            user_id_provider=lambda: getattr(self, "_user_id", DEFAULT_USER_ID),
        )

    def configure_sandbox_runtime(self, *, in_docker: bool, sandbox_enabled: bool) -> None:
        """startup 期注入平台运行时事实（Docker 嵌套、内核沙箱可用性）。

        ``in_docker`` 透传 SandboxSettings.enableWeakerNestedSandbox；
        ``sandbox_enabled=False``（Windows 回退）关闭 SDK SandboxSettings 并把
        Bash 工具调用切到代码白名单路径。AgentAccessPolicy 不可变，整体换新
        而非戳改字段——hook / options 构建都在调用时读 ``self.access_policy``，
        换新即对后续所有会话与工具调用生效。
        """
        self.access_policy = replace(
            self.access_policy,
            in_docker=bool(in_docker),
            sandbox_enabled=bool(sandbox_enabled),
        )

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

    async def _build_options(
        self,
        project_name: str,
        resume_id: str | None = None,
        can_use_tool: Callable[[str, dict[str, Any], Any], Any] | None = None,
        locale: str = DEFAULT_LOCALE,
        stderr: Callable[[str], None] | None = None,
    ) -> Any:
        """委派给 ``OptionsAssembler.build``——SessionManager 不再直接构建 options 与
        hook，仅调用装配器；凭证注入、prompt 装配、hook 工厂均由装配器持有。"""
        return await self._options_assembler.build(
            project_name,
            resume_id=resume_id,
            can_use_tool=can_use_tool,
            locale=locale,
            stderr=stderr,
        )

    def _build_session_store(self):
        """委派给装配器的 session store 单例。AssistantService 经此拿到与 SDK options
        同一份 store 实例（同一 per-user 命名空间），读写共享缓存。"""
        return self._options_assembler.build_session_store()

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

    def _build_entry_pipeline(self, managed: "ManagedSession") -> SessionEntryPipeline:
        """构建会话的事件日志写入点管道。

        session_id 用 provider 现取：新会话在 sdk_session_id 就绪前为 None，
        管道自动跳过（该窗口内只有 init 系统消息，本就不入日志）。
        """
        return SessionEntryPipeline(
            self.event_log_store,
            session_id_provider=lambda: managed.resolved_sdk_id,
            broadcast=managed.channel.broadcast,
        )

    def _make_actor_message_callback(
        self,
        managed_ref: list["ManagedSession | None"],
    ) -> Callable[[Any], None]:
        """Sync on_message callback shared by send_new_session and get_or_connect.

        Runs inside the actor task. Order is load-bearing:
        duplicate-echo detection skips broadcast but still queues the message
        for async sdk_session_id capture; _handle_special_message must mutate
        result messages with `session_status` before subscribers see them via
        broadcast; _inbox hand-off last so async post-processing never
        observes a message that hasn't been broadcast yet.
        """

        def _on_message(raw_msg: Any) -> None:
            managed = managed_ref[0]
            if managed is None:
                return
            msg_dict = message_to_dict(raw_msg)
            if not isinstance(msg_dict, dict):
                return
            if is_duplicate_user_echo(managed.pending_user_echoes, msg_dict):
                # SDK 回放的用户消息副本：POST 受理时已写日志分配身份，
                # 打标让事件日志写入点跳过，不产生重复条目。
                msg_dict[REPLAYED_USER_ECHO_KEY] = True
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
                managed.channel.broadcast(build_runtime_status_message("error", managed.session_id))
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
        locale: str = DEFAULT_LOCALE,
        user_entry: dict[str, Any] | None = None,
        client_key: str | None = None,
    ) -> str:
        """Create a new session via send-first: start actor, send query, wait for sdk_session_id.

        ``user_entry`` 是本条用户消息的事件日志条目（写入点定型后的形态）；
        sdk_session_id 就绪后由 inbox 任务先写日志（seq 0）再放行等待，权威
        条目落在 ``managed.initial_user_log_entry`` 供受理响应回传。
        """
        if not SDK_AVAILABLE or ClaudeSDKClient is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        await self._ensure_capacity()
        temp_id = uuid4().hex
        managed_ref: list[ManagedSession | None] = [None]

        # SDK stderr 回调在整个会话存活期间都被 ClaudeAgentOptions 持有，
        # actor.start() 成功后仍会被调；用 deque(maxlen=) FIFO 自动裁剪老行，
        # 避免长会话期间因 SDK 持续输出 stderr 造成内存无界增长。
        # 启动失败场景下 stderr 通常远小于上限，关键提示不会被裁掉。
        stderr_lines: deque[str] = deque(maxlen=_SDK_STDERR_BUFFER_MAX)

        def _collect_stderr(line: str) -> None:
            stderr_lines.append(line)
            logger.warning("claude_agent_sdk stderr: %s", line)

        options = await self._build_options(
            project_name,
            resume_id=None,
            can_use_tool=await self._build_can_use_tool_callback(temp_id, managed_ref),
            locale=locale,
            stderr=_collect_stderr,
        )
        assistant_model = resolve_configured_assistant_model(getattr(options, "env", None))

        actor = SessionActor(
            client_factory=lambda: ClaudeSDKClient(options=options),
            on_message=self._make_actor_message_callback(managed_ref),
        )

        managed = ManagedSession(
            session_id=temp_id,
            actor=actor,
            status="running",
            project_name=project_name,
            assistant_model=assistant_model,
        )
        if user_entry is not None:
            managed.pending_initial_user_entry = {"entry": user_entry, "client_key": client_key}
        managed.entry_pipeline = self._build_entry_pipeline(managed)
        managed_ref[0] = managed
        managed.last_activity = time.monotonic()
        self.sessions[temp_id] = managed

        try:
            await actor.start()
        except Exception as exc:
            logger.exception("新会话 actor 启动失败 temp_id=%s", temp_id)
            self.sessions.pop(temp_id, None)
            raise AgentStartupError(str(exc), sdk_stderr="\n".join(stderr_lines)) from exc

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

        # 登记待回放的用户消息标识：SDK 会回放刚发送消息的副本，写入点凭此
        # 打标跳过（POST 受理时已写日志分配身份，回放副本不得二次落库）。
        display_text = echo_text or (prompt if isinstance(prompt, str) else "")
        dedup_key = display_text or (IMAGE_ONLY_SENTINEL if echo_content else "")
        if dedup_key:
            managed.pending_user_echoes.append(dedup_key)
        managed.last_user_prompt = display_text

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
                # 事件日志写入点：sdk_session_id 就绪后逐条定型入日志。
                # handle_message 内部吞异常，不会打断会话消费。
                if managed.entry_pipeline is not None and managed.resolved_sdk_id is not None:
                    await managed.entry_pipeline.handle_message(msg_dict)
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

    async def get_or_connect(
        self, session_id: str, *, meta: SessionMeta | None = None, locale: str = DEFAULT_LOCALE
    ) -> ManagedSession:
        """Get existing managed session or spin up an actor for resumed session.

        ``locale`` only matters when this call revives a cold session: the SDK's
        ``resume`` rebuilds the whole system prompt from current options, so the
        language regulation segment must reflect the caller's request locale. An
        already-resident session returns from cache and ``locale`` is ignored —
        the session-fixed system prompt stays unchanged.
        """
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

            # 见 send_new_session 同名注释：deque(maxlen=) 防长会话内存累积。
            stderr_lines: deque[str] = deque(maxlen=_SDK_STDERR_BUFFER_MAX)

            def _collect_stderr(line: str) -> None:
                stderr_lines.append(line)
                logger.warning("claude_agent_sdk stderr: %s", line)

            options = await self._build_options(
                meta.project_name,
                meta.id,  # SessionMeta.id 就是 sdk_session_id
                can_use_tool=await self._build_can_use_tool_callback(session_id, managed_ref),
                locale=locale,
                stderr=_collect_stderr,
            )
            assistant_model = resolve_configured_assistant_model(getattr(options, "env", None))

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
                assistant_model=assistant_model,
                resolved_sdk_id=meta.id,  # 标记为已注册，防止重复创建 DB 记录
            )
            managed.sdk_id_event.set()  # 已有会话不需要等待 sdk_id
            managed.entry_pipeline = self._build_entry_pipeline(managed)
            managed_ref[0] = managed
            managed.last_activity = time.monotonic()
            self.sessions[session_id] = managed

            try:
                await actor.start()
            except Exception as exc:
                logger.exception("恢复会话 actor 启动失败 session_id=%s", session_id)
                self.sessions.pop(session_id, None)
                raise AgentStartupError(str(exc), sdk_stderr="\n".join(stderr_lines)) from exc

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
        meta: SessionMeta | None = None,
        locale: str = DEFAULT_LOCALE,
        user_entry: dict[str, Any] | None = None,
        client_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Send a message via the session actor.

        ``locale`` is forwarded to ``get_or_connect`` so a cold-recovered
        session rebuilds its language regulation from the current request's
        locale rather than the default.

        ``user_entry`` 是本条用户消息的事件日志条目：先写日志分配身份（并发
        与容量校验之后、送入 SDK 之前），返回权威条目供受理响应回传；同一
        ``client_key`` 重试命中既有条目时不再重复送 SDK。
        """
        managed = await self.get_or_connect(session_id, meta=meta, locale=locale)
        managed.last_activity = time.monotonic()

        # 幂等预检先于 running 拦截：受理已成功（响应在网络层丢失）的重试
        # 应得到幂等成功响应，而非"会话正在处理中"的 400。
        if user_entry is not None and client_key is not None:
            existing = await self.event_log_store.find_by_client_key(session_id, client_key)
            if existing is not None:
                return existing

        # 取消待执行的 cleanup（会话恢复活跃）
        if managed._cleanup_task and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()
            managed._cleanup_task = None

        if managed.status == "running":
            raise ValueError("会话正在处理中，请等待当前回复完成后再发送新消息")

        log_entry: dict[str, Any] | None = None
        if user_entry is not None:
            log_entry, created = await self.event_log_store.append_user_entry(
                session_id,
                user_entry,
                client_key=client_key,
            )
            if not created:
                # 幂等重试：条目存在即上一次受理已（或正在）送入 SDK——投递
                # 失败的条目会被补偿删除，不会残留到这里。直接返回权威条目。
                return log_entry

        # 登记待回放的用户消息标识（写入点凭此给 SDK 回放副本打标跳过）。
        # 纯图片消息 display_text 为空：SDK 解析会丢弃 image 块，回放的
        # UserMessage 内容为空，用哨兵值让打标仍能匹配。
        display_text = echo_text or (prompt if isinstance(prompt, str) else "")
        dedup_key = display_text or (IMAGE_ONLY_SENTINEL if echo_content else "")
        if dedup_key:
            managed.pending_user_echoes.append(dedup_key)
            if len(managed.pending_user_echoes) > 20:
                managed.pending_user_echoes.pop(0)
        managed.last_user_prompt = display_text

        await self.meta_store.update_status(session_id, "running")

        # Send the query via the actor. send_query flips status to error on
        # cmd.error and re-raises; we ensure meta store reflects that too.
        try:
            await managed.send_query(prompt, sdk_session_id=session_id)
        except Exception:
            logger.exception("会话消息处理失败")
            managed.pending_user_echoes.clear()
            if log_entry is not None:
                # 补偿删除受理条目：投递失败即受理失败，条目残留会让同幂等键
                # 重试在预检处短路，prompt 永远不会送入 SDK。
                try:
                    await self.event_log_store.delete_entry(session_id, int(log_entry["seq"]))
                except Exception:
                    logger.exception("回滚受理条目失败 session_id=%s seq=%s", session_id, log_entry.get("seq"))
            try:
                await self.meta_store.update_status(session_id, "error")
            except Exception:
                logger.exception("持久化 error 状态失败 session_id=%s", session_id)
            raise
        if log_entry is not None:
            # send_query 确认投递成功后再广播：避免失败回滚已删条目后，
            # 在线 SSE 订阅者仍残留一条已撤销的用户消息。
            managed.channel.broadcast({"type": "log_entry", "session_id": session_id, "entry": log_entry})
        return log_entry

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

        # 不清 pending_user_echoes：SDK 可能尚未回放刚受理的用户消息副本，
        # 清空会让回放副本失去 REPLAYED_USER_ECHO 标记、被写入点当作新用户
        # 消息二次落库（append-only 日志无法自愈）。残留由 _finalize_turn /
        # _mark_session_terminal 在轮次终结时清理。
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
        """Handle result messages before broadcast."""
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
        if final_status == "error":
            logger.warning(
                "assistant session result error",
                extra={
                    "session_id": managed.session_id,
                    "subtype": result_msg.get("subtype"),
                    "is_error": result_msg.get("is_error"),
                    "api_error_status": result_msg.get("api_error_status"),  # SDK 0.1.76+
                    "stop_reason": result_msg.get("stop_reason"),
                },
            )
        try:
            await self._record_assistant_usage(managed, result_msg, final_status)
        except Exception:
            logger.exception("记录 assistant usage 失败 session_id=%s", managed.session_id)
        await self.meta_store.update_status(managed.session_id, final_status)
        managed.interrupt_requested = False
        if final_status != "running":
            self._schedule_cleanup(managed.session_id)

    async def _record_assistant_usage(
        self,
        managed: ManagedSession,
        result_msg: dict[str, Any],
        final_status: SessionStatus,
    ) -> None:
        input_tokens, output_tokens, usage_tokens = extract_text_token_usage(result_msg)
        total_cost_usd = extract_assistant_cost(result_msg)
        if input_tokens is None and output_tokens is None and total_cost_usd is None:
            return

        call_id = await self.usage_tracker.start_call(
            project_name=managed.project_name,
            call_type="text",
            model=resolve_assistant_model(result_msg, managed.assistant_model),
            prompt=managed.last_user_prompt[:500] if managed.last_user_prompt else None,
            provider=PROVIDER_ANTHROPIC,
            user_id=getattr(self, "_user_id", DEFAULT_USER_ID),
        )
        await self.usage_tracker.finish_call(
            call_id,
            status="success" if final_status == "completed" else "failed",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_tokens=usage_tokens,
            cost_amount=total_cost_usd,
            currency="USD" if total_cost_usd is not None else None,
        )

    async def _mark_session_terminal(self, managed: ManagedSession, status: SessionStatus, reason: str) -> None:
        """Set terminal status on abnormal consumer exit."""
        managed.pending_user_echoes.clear()
        managed.cancel_pending_questions(reason)
        managed.status = status
        managed.last_activity = time.monotonic()
        await self.meta_store.update_status(managed.session_id, status)
        managed.interrupt_requested = False

        # 事件日志侧补写 typed 中断条目：此路径下 inbox 处理已终止，
        # SDK 自己的回显不会再经写入点入日志。尾检去重保证与已入日志的
        # 回显（竞态双写）只留一条。
        if status == "interrupted" and managed.entry_pipeline is not None:
            try:
                await managed.entry_pipeline.append_interrupt()
            except Exception:
                logger.exception("中断条目写入事件日志失败 session_id=%s", managed.resolved_sdk_id)

        # Broadcast terminal status so SSE subscribers unblock immediately
        # instead of waiting for the heartbeat timeout.
        managed.channel.broadcast(
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
            "timestamp": utc_now_iso(),
        }
        pending = managed.add_pending_question(payload)
        managed.channel.broadcast(payload)

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

            # Windows 回退：sandbox 关闭时 Bash 系列不在 allowed_tools，
            # 落到这里走 AgentAccessPolicy 的前缀白名单。
            if not self.access_policy.sandbox_enabled and tool_name == "Bash":
                cmd = str((input_data or {}).get("command") or "").strip()
                if self.access_policy.is_bash_command_whitelisted(cmd):
                    return PermissionResultAllow(updated_input=input_data)
                if PermissionResultDeny is not None:
                    return PermissionResultDeny(
                        message=self.access_policy.format_bash_whitelist_deny_message(cmd),
                    )
            # BashOutput / KillBash 是 Bash 管理类工具，回退模式直接放行。
            if not self.access_policy.sandbox_enabled and tool_name in ("BashOutput", "KillBash"):
                return PermissionResultAllow(updated_input=input_data)

            # Whitelist fallback: deny any tool that was not pre-approved
            # by allowed_tools or settings.json allow rules.
            if PermissionResultDeny is not None:
                reason = getattr(_context, "decision_reason", None)  # SDK 0.1.74+
                reason_line = f"上游决策原因: {reason}\n" if reason else ""
                hint = (
                    f"未授权的工具调用: {tool_name}"
                    f"({json.dumps(input_data, ensure_ascii=False)[:200]})\n"
                    f"{reason_line}"
                    "请检查工具名是否正确，以及 file_path / 命令是否触发了 "
                    "settings.json 的 deny 规则或 PreToolUse hook（跨项目/cwd 外写/代码扩展名）。"
                )
                return PermissionResultDeny(message=hint)
            return PermissionResultAllow(updated_input=input_data)

        return _can_use_tool

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
            # 新会话首条用户消息先写日志分配身份（seq 0）：本方法在 inbox 任务
            # 内串行执行于任何 assistant 条目定型之前，保证时间线顺序；写入
            # 完成后才 set sdk_id_event，send_new_session 醒来即可拿到权威条目。
            pending_user = managed.pending_initial_user_entry
            if pending_user is not None:
                managed.pending_initial_user_entry = None
                try:
                    authoritative, _created = await self.event_log_store.append_user_entry(
                        sdk_id,
                        pending_user["entry"],
                        client_key=pending_user.get("client_key"),
                    )
                    managed.initial_user_log_entry = authoritative
                    managed.channel.broadcast({"type": "log_entry", "session_id": sdk_id, "entry": authoritative})
                except Exception:
                    logger.exception("新会话用户条目写入事件日志失败 session_id=%s", sdk_id)
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

    def get_draft_state(self, session_id: str) -> dict[str, Any]:
        """流式预览态（draft）重连首帧快照：当前累积态 + rev 过滤门槛。

        ``rev`` 在无活跃 draft 时也返回：订阅与快照之间已入队的旧 delta
        （rev <= 门槛）由客户端按 rev 丢弃，不做内容比对。
        """
        managed = self.sessions.get(session_id)
        if managed is None or managed.entry_pipeline is None:
            return {"draft": None, "rev": 0}
        draft = managed.entry_pipeline.draft
        return {"draft": draft.snapshot(), "rev": draft.rev}

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

    async def _subscribe(self, session_id: str, *, locale: str = DEFAULT_LOCALE) -> tuple[SseChannel, asyncio.Queue]:
        """Register a live-message queue for a session.

        ``locale`` is forwarded to ``get_or_connect`` so reviving a cold session
        through the stream path rebuilds its language regulation from the current
        request's locale, matching the send-message path.

        Private: the only consumer is :meth:`stream_messages`, which owns the
        deterministic unsubscribe via its context-manager ``__aexit__``.
        """
        managed = await self.get_or_connect(session_id, locale=locale)
        queue = managed.channel.subscribe()
        return managed.channel, queue

    async def _unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """Remove a queue from a session's subscriber channel."""
        if session_id in self.sessions:
            await self.sessions[session_id].channel.unsubscribe(queue)

    @contextlib.asynccontextmanager
    async def stream_messages(
        self, session_id: str, *, idle_timeout: float = 20.0, locale: str = DEFAULT_LOCALE
    ) -> AsyncIterator[AsyncIterator[SessionStreamEvent]]:
        """Subscribe to a session's messages as a self-cleaning async iterator.

        Yields an async iterator producing semantic events, in order:

        - a :class:`SubscriptionReady` barrier (always the first event) marking
          that the subscription is established — broadcasts after it are gap-free,
        - a :class:`LiveMessage` per broadcast message,
        - a :class:`Heartbeat` whenever *idle_timeout* elapses with no message
          (consumers run liveness / disconnect self-checks on it).

        The stream ends when the subscriber queue is dropped under backpressure —
        stream end is the reconnect signal; no overflow event reaches consumers
        (the overflow sentinel is internal to :class:`SseChannel`).

        Subscription, queue draining and unsubscribe all live behind this seam;
        cleanup is carried deterministically by ``__aexit__`` (see ADR-0005).
        Consume as ``async with stream_messages(...) as stream: async for event
        in stream``.

        ``locale`` only matters when this subscription revives a cold session; an
        already-resident session ignores it (session-fixed system prompt).
        """
        channel, queue = await self._subscribe(session_id, locale=locale)

        async def _iter() -> AsyncIterator[SessionStreamEvent]:
            # NOTE: intentionally NO ``finally: _unsubscribe`` here. Cleanup is owned
            # by the enclosing context manager's __aexit__ (ADR-0005): a bare async
            # generator's finally only runs at GC on break/disconnect, which is the
            # exact leak this design avoids. Do not add a finally to this inner gen.
            yield SubscriptionReady()
            async for item in channel.iterate(queue, idle_timeout=idle_timeout):
                yield Heartbeat() if item is IDLE else LiveMessage(message=item)

        try:
            yield _iter()
        finally:
            await self._unsubscribe(session_id, queue)

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
