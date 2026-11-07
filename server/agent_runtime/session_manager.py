"""
Manages ClaudeSDKClient instances with background execution and reconnection support.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional
from uuid import uuid4

from lib.agent.agent_memory_paths import project_memory_dir
from lib.agent.credential_tiers import AgentPricing
from lib.db.base import DEFAULT_USER_ID
from lib.i18n import DEFAULT_LOCALE
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.logging_utils import redact_diagnostic_text
from lib.infra.path_safety import PathTraversalError, safe_join
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.entry_pipeline import SessionEntryPipeline
from server.agent_runtime.event_log import (
    REPLAYED_USER_ECHO_ENTRY_UUID_KEY,
    REPLAYED_USER_ECHO_KEY,
    EventLogStore,
    build_user_entry,
)
from server.agent_runtime.failure_observation import (
    build_startup_failure_observation,
    failure_observation_json,
)
from server.agent_runtime.message_serialization import (
    is_main_turn_activity,
    message_to_dict,
    utc_now_iso,
)
from server.agent_runtime.models import (
    Heartbeat,
    LiveMessage,
    QueuedMessage,
    QueuedMessageState,
    SessionMeta,
    SessionStatus,
    SessionStreamEvent,
    SubscriptionReady,
    WithdrawalIntent,
)
from server.agent_runtime.options_assembler import OptionsAssembler, agent_pricing_of
from server.agent_runtime.result_status import resolve_result_status
from server.agent_runtime.sdk_frames import CommandLifecycle
from server.agent_runtime.session_actor import SessionActor, SessionCommand
from server.agent_runtime.session_store import SessionMetaStore
from server.agent_runtime.usage_extraction import ModelTurnUsage, TurnUsageMeter
from server.sse_channel import IDLE, EvictNonCriticalAndSignal, SseChannel

logger = logging.getLogger(__name__)

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    SettingSource,
)

from lib.backends.providers import PROVIDER_ANTHROPIC, CallStatus
from lib.billing.ledger import Ledger
from lib.billing.metering import Usage, token_usage
from lib.config.service import ConfigService
from lib.db import async_session_factory
from lib.db.repositories.usage_repo import AgentModelCall
from lib.i18n.display_names import agent_provider_id

SDK_AVAILABLE = True


# Inbox backlog warning threshold: 100 stream_event messages in ~1s (typical maximum streaming rate);
# sustained excess indicates blocked _process_inbox or very slow downstream I/O.
_INBOX_BACKLOG_WARN_THRESHOLD = 100
_INBOX_BACKLOG_RESET_THRESHOLD = 50  # Reset warnings only below this low-water mark, avoiding oscillation spam.


class _StartupStderrCollector:
    """Collect stderr losslessly until sdk_session_id is ready, then stop and release it."""

    def __init__(self) -> None:
        self._active = True
        self._lines: list[str] = []

    def __call__(self, line: str) -> None:
        logger.warning("claude_agent_sdk stderr: %s", redact_diagnostic_text(line))
        if self._active:
            self._lines.append(line)

    def render(self) -> str:
        return "\n".join(self._lines)

    def stop(self) -> None:
        self._active = False
        self._lines.clear()


class SessionCapacityError(Exception):
    """All concurrency slots are occupied by running sessions, preventing new connections."""


class UnrecordedMessageError(Exception):
    """An idempotent retry found an Agent-accepted message missing from the event log; do not resend to the CLI and execute it twice."""


class QueuedMessageNotFoundError(Exception):
    """The message was never queued in this session, or the session left memory and its queue was cleared."""


class QueuedMessageWithdrawalPendingError(Exception):
    """Withdrawal of this queued message still awaits the CLI response."""


WithdrawalOutcome = Literal["withdrawn", "accepted"]
"""Queued-message withdrawal result: ``withdrawn`` removed it from CLI and queue; ``accepted`` means the Agent received it and it enters conversation normally."""

SendNowOutcome = Literal["sent", "accepted"]
"""Immediate-send result: ``sent`` resubmitted it with now priority; ``accepted`` means the Agent already received it and it is not resent."""


class AgentStartupError(RuntimeError):
    """ClaudeSDKClient startup failure carrying SDK stderr.

    SDK ``ProcessError`` reports nonzero subprocess exits but hardcodes ``stderr`` to
    ``"Check stderr output for details"``; actual stderr is available only through the
    ``ClaudeAgentOptions.stderr`` callback. Bundle collected lines here and forward them to routers/UI,
    showing SDK installation guidance (e.g. Windows bash.exe / pwsh.exe download links).

    ``__str__`` returns the full message + stderr, so generic router ``except Exception: str(exc)``
    branches forward it automatically without path-specific catches."""

    def __init__(
        self,
        message: str,
        sdk_stderr: str = "",
        *,
        failure_observation: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.sdk_stderr = sdk_stderr
        self.failure_observation = failure_observation
        super().__init__(self._compose())

    def _compose(self) -> str:
        if self.sdk_stderr:
            return f"{self.message}\n\n{self.sdk_stderr}"
        return self.message


def _make_agent_startup_error(
    exc: BaseException,
    *,
    project_name: str,
    session_id: str | None,
    sdk_stderr: str = "",
) -> AgentStartupError:
    """Consistently retain, redact, and wrap actual startup observations at the runtime boundary."""
    failure = build_startup_failure_observation(
        exc,
        project_name=project_name,
        session_id=session_id,
        sdk_stderr=sdk_stderr,
    )
    logger.error(
        "Agent 启动失败 project_name=%s session_id=%s failure_observation=%s",
        project_name,
        session_id,
        failure_observation_json(failure),
    )
    return AgentStartupError(
        redact_diagnostic_text(exc),
        sdk_stderr=redact_diagnostic_text(sdk_stderr),
        failure_observation=failure,
    )


@dataclass
class PendingQuestion:
    """Tracks a pending AskUserQuestion request."""

    question_id: str
    payload: dict[str, Any]
    answer_future: asyncio.Future[dict[str, str]]


@dataclass(frozen=True)
class _ActorExitNotice:
    """Actor completion notice; inbox processors handle its exception in queue order."""

    error: BaseException | None = None


@dataclass(frozen=True)
class _CliIdleNotice:
    """CLI idle notice; ``epoch`` is the running-entry counter at frame read, handled by inbox after this turn's entries."""

    epoch: int


@dataclass(frozen=True)
class _CliBusyNotice:
    """CLI reports work starting and the session enters running; inbox persists it and emits autonomous-turn notifications."""


@dataclass(frozen=True)
class _QueuedMessageSettled:
    """CLI reports queued-message disposition (merged into a turn or discarded); inbox handles after preceding output."""

    lifecycle: CommandLifecycle
    preempted: bool = False
    """At CLI report time, an immediately sent message remains unaccepted: this discard belongs to the turn it interrupted."""


def _user_message_frame(
    content: str | list[dict[str, Any]], cli_uuid: str, *, priority: Literal["now"] | None = None
) -> dict[str, Any]:
    """User-message frame sent to the CLI. Only messages with uuid receive ``command_lifecycle`` frames; replay preserves that uuid.

    ``priority="now"`` interrupts the current turn to process this message first (immediate sending)."""
    frame: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
        "uuid": cli_uuid,
    }
    if priority is not None:
        frame["priority"] = priority
    return frame


def _agent_model_call(usage: ModelTurnUsage) -> AgentModelCall:
    """Convert one model's turn usage to accounting input: measure each request by its usage contract, with token columns summing requests."""
    total = usage.total
    return AgentModelCall(
        model=usage.model,
        requests=tuple(
            Usage(
                quantities=token_usage(
                    input_tokens=request.total_input,
                    output_tokens=request.output,
                    cache_read_tokens=request.cache_read,
                    cache_write_5m_tokens=request.cache_write_5m,
                    cache_write_1h_tokens=request.cache_write_1h,
                )
            )
            for request in usage.requests
        ),
        input_tokens=total.total_input,
        output_tokens=total.output,
        cache_read_tokens=total.cache_read,
        cache_write_5m_tokens=total.cache_write_5m,
        cache_write_1h_tokens=total.cache_write_1h,
    )


def _content_text(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(block.get("text", "")) for block in content if block.get("type") == "text")


def _make_session_channel() -> SseChannel:
    """Session broadcast channel with noncritical-message eviction plus overflow signals.

    Never silently discard critical result/runtime_status/log_entry/log_turn_complete/queued_message messages.
    End streams for subscribers whose queues cannot keep up; stream termination signals reconnection (docs/adr/0046)."""
    return SseChannel(
        overflow=EvictNonCriticalAndSignal(
            is_critical=lambda message: message.get("type") in ManagedSession._CRITICAL_MESSAGE_TYPES,
        ),
    )


@dataclass
class _DetachedQueue:
    """Queued messages and idempotency records taken by ``ManagedSession.detach_queued_messages``."""

    messages: list[QueuedMessage]
    sent_message_entries: dict[str, str]
    sent_client_keys: dict[str, str]


@dataclass
class ManagedSession:
    """A managed ClaudeSDKClient session."""

    session_id: str  # sdk_session_id for existing sessions, temporary UUID while new sessions wait.
    actor: "SessionActor"  # per-session actor owning the SDK client
    status: SessionStatus = "idle"
    project_name: str = ""  # Used by _register_new_session.
    sdk_id_event: asyncio.Event = field(default_factory=asyncio.Event)
    resolved_sdk_id: str | None = None  # Set by consumers, read by send_new_session.
    channel: SseChannel = field(default_factory=_make_session_channel)
    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    # Messages in send order, delivered to CLI but not accepted into conversation; started triggers logging/removal.
    queued_messages: list[QueuedMessage] = field(default_factory=list)
    # CLI message uuid → user-entry uuid, retained until cleanup for idempotent late replay suppression.
    sent_message_entries: dict[str, str] = field(default_factory=dict)
    # Idempotency key → user-entry uuid: retries return the same queue message until acceptance, then event logs take over.
    sent_client_keys: dict[str, str] = field(default_factory=dict)
    # Message uuids delivered at now priority but not yet accepted. While nonempty, aborted_* turn endings represent immediate-send interruptions;
    # handle as interrupted and redeliver CLI-discarded queued messages not yet in conversation.
    now_messages_in_flight: set[str] = field(default_factory=set)
    # Serialize sends in waiter arrival order, matching CLI delivery order to queue order.
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Event-log write-boundary pipeline (live writer for the UI timeline's sole read source).
    entry_pipeline: SessionEntryPipeline | None = None
    # Initial new-session user message: inbox logs seq 0 after sdk_session_id readiness,
    # assigning user-entry identity before any assistant entry.
    pending_initial_user_entry: dict[str, Any] | None = None
    initial_user_log_entry: dict[str, Any] | None = None
    # Initial log-write failure recorded by inbox; send_new_session reports it explicitly
    # on waking (event logs alone supply timelines; missing seq 0 is unacceptable).
    initial_user_entry_error: Exception | None = None
    # Accounting prompt: append accepted messages before this turn's result in order; acceptance after result starts another turn.
    last_user_prompt: str = ""
    turn_usage_recorded: bool = True
    # Agent credentials injected at connection, with their then-current multiplier/tier bindings, price this connection's turns (docs/adr/0017,0102).
    # System-setting fallback uses defaults: no credential, multiplier 1, no bindings.
    agent_pricing: AgentPricing = field(default_factory=AgentPricing)
    # Connection usage metering: differential baseline for stream requests/model_usage (docs/adr/0102).
    usage_meter: TurnUsageMeter = field(default_factory=lambda: TurnUsageMeter(resumed=False))
    interrupt_requested: bool = False
    last_activity: float | None = None  # updated on every send/receive
    # Last-turn outcome, recorded at result and consumed on leaving running; cleared on new turns.
    turn_outcome: SessionStatus | None = None
    # Latest result with session_status, forwarding subtype/other fields with status on leaving running.
    last_turn_result: dict[str, Any] | None = None
    # Increment running-entry counter on message delivery/nonidle CLI signals; idle frames capture its value.
    # If changed when inbox handles idle, newer delivery/work has made it stale.
    _running_epoch: int = 0
    # Running-entry count at latest idle read; equality to current means no subsequent running-entry signals.
    _cli_idle_epoch: int | None = None
    # Inbox processing stopped: actors still read, but work-start signals no longer enter running because nobody can finalize.
    _inbox_stopped: bool = False
    _cleanup_task: asyncio.Task | None = None  # current cleanup timer (idle TTL or terminal delay)
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue)  # async post-processing queue
    _inbox_warned: bool = False  # edge-triggered backlog warning state
    _process_task: asyncio.Task | None = None  # per-session async inbox processor
    _interrupting: bool = False  # send_interrupt re-entry guard (distinct from interrupt_requested)

    # Message types that must never be silently dropped from subscriber queues.
    _CRITICAL_MESSAGE_TYPES: ClassVar[set[str]] = {
        "result",
        "runtime_status",
        "log_entry",
        "log_turn_complete",
        "queued_message",
    }

    def enter_running(self) -> bool:
        """Message delivered to CLI or CLI work-start read: enter running. Return whether transitioning from non-running.

        Synchronous on the event loop (send path/actor callbacks): idle cleanup and eviction treat the session
        as active immediately, invalidating previously read idle signals."""
        self._running_epoch += 1
        self.last_activity = time.monotonic()
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None
        if self.status == "running":
            return False
        self.status = "running"
        self.turn_outcome = None
        self.last_turn_result = None
        return True

    def abandon_running(self, epoch: int) -> None:
        """An ``enter_running`` did not deliver to CLI; ``epoch`` is the counter before entry.

        Never decrement, or later entries reuse counts of previously read idle signals. With no intervening
        running-entry signal and CLI idle reported before/during this entry, add an idle notice at the current
        count: earlier idle may have been ignored as stale, leaving no later idle to settle the active turn."""
        if self._running_epoch != epoch + 1 or self._cli_idle_epoch not in (epoch, epoch + 1):
            return
        self._cli_idle_epoch = self._running_epoch
        self._inbox.put_nowait(_CliIdleNotice(epoch=self._running_epoch))

    def _on_actor_message(self, msg: dict[str, Any]) -> None:
        """SessionActor on_message callback: synchronous, in-memory, no await.

        Broadcast messages to subscribers.

        **Do not leave running here**: after CLI idle, inbox transitions only after processing every turn entry
        (SessionManager._settle_cli_idle), ensuring status never precedes the last entry.

        SessionManager._handle_special_message registers pending_questions."""
        self.channel.broadcast(msg)

    async def send_query(self, message: dict[str, Any], sdk_session_id: str = "default") -> None:
        """Return immediately after passing a user-message frame to CLI, which queues/merges/starts turns while the actor continuously reads in the background."""
        self.enter_running()
        cmd = SessionCommand(type="query", message=message, session_id=sdk_session_id)
        await self.actor.enqueue(cmd)
        await cmd.done.wait()
        if cmd.error is not None:
            self.status = "error"
            raise cmd.error

    def find_queued_message(
        self, *, cli_uuid: str | None = None, message_id: str | None = None
    ) -> QueuedMessage | None:
        for queued in self.queued_messages:
            if queued.cli_uuid == cli_uuid or queued.id == message_id:
                return queued
        return None

    def remove_queued_message(self, queued: QueuedMessage, **broadcast_fields: Any) -> None:
        """Remove from the queue and broadcast; no-op when absent. Include ``broadcast_fields`` in removal broadcasts."""
        if queued not in self.queued_messages:
            return
        self.queued_messages.remove(queued)
        self.channel.broadcast({"type": "queued_message", "op": "remove", "id": queued.id, **broadcast_fields})

    def move_queued_message_first(self, queued: QueuedMessage) -> None:
        """Move a queued message first and replay the whole snapshot, aligning tray order with immediate CLI processing; no-op when absent."""
        if queued not in self.queued_messages or self.queued_messages[0] is queued:
            return
        self.queued_messages.remove(queued)
        self.queued_messages.insert(0, queued)
        self.channel.broadcast(
            {
                "type": "queued_message",
                "op": "reorder",
                "messages": [item.to_payload() for item in self.queued_messages],
            }
        )

    def discard_queued_message(self, queued: QueuedMessage, **broadcast_fields: Any) -> None:
        """Unaccepted queued message leaves: remove it and release its idempotency key, permitting same-key retry delivery."""
        self.remove_queued_message(queued, **broadcast_fields)
        if queued.client_key is not None and self.sent_client_keys.get(queued.client_key) == queued.id:
            del self.sent_client_keys[queued.client_key]

    def withdraw_queued_message(self, queued: QueuedMessage) -> None:
        """User withdrawal removes a queued message: broadcast withdrawal intent and edited content for the initiating page's input box.

        Successful withdrawal responses and CLI cancelled frames both enter here, whichever arrives first;
        duplicate calls do nothing."""
        if queued not in self.queued_messages:
            return
        assert queued.withdrawal is not None
        queued.withdrawn = True
        fields: dict[str, Any] = {"withdrawn": queued.withdrawal}
        if queued.withdrawal == "edit":
            fields["message"] = queued.to_payload()
        self.discard_queued_message(queued, **fields)

    def was_sent(self, message_id: str) -> bool:
        """Whether this message was sent as a queued message to this session's CLI."""
        return message_id in self.sent_message_entries.values()

    def drop_queued_messages(self, *, state: QueuedMessageState | None = None) -> None:
        """Queued messages will no longer be processed: discard and broadcast; ``state`` filters which messages, omitted means all."""
        for queued in list(self.queued_messages):
            if state is None or queued.state == state:
                self.discard_queued_message(queued)

    def mark_queued_messages_unsent(self) -> None:
        """CLI exited: mark remaining queue messages unsent and broadcast, leaving users to resend, edit, or delete."""
        for queued in self.queued_messages:
            if queued.state != "queued":
                continue
            queued.state = "unsent"
            # Withdrawal intent matters only for later CLI cancelled; CLI is now gone.
            queued.withdrawal = None
            self.channel.broadcast({"type": "queued_message", "op": "upsert", "message": queued.to_payload()})

    @property
    def actor_exited(self) -> bool:
        task = self.actor.task
        return task is not None and task.done()

    def detach_queued_messages(self) -> _DetachedQueue:
        """Take queue/idempotency records without broadcasting for transfer to new session objects during reconnection, preserving trays."""
        detached = _DetachedQueue(
            messages=self.queued_messages,
            sent_message_entries=self.sent_message_entries,
            sent_client_keys=self.sent_client_keys,
        )
        self.queued_messages = []
        self.sent_message_entries = {}
        self.sent_client_keys = {}
        return detached

    def adopt_queued_messages(self, detached: _DetachedQueue) -> None:
        self.queued_messages[:0] = detached.messages
        self.sent_message_entries = {**detached.sent_message_entries, **self.sent_message_entries}
        self.sent_client_keys = {**detached.sent_client_keys, **self.sent_client_keys}

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
        cmd = SessionCommand(type="disconnect", interrupt_first=self.status == "running")
        await self.actor.enqueue(cmd)
        await cmd.done.wait()
        await self.actor.wait()
        # Disconnected running sessions retain state; session management finalizes by latest-turn outcome.
        if self.status != "running":
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


# Result fields forwarded with session status (entry status events use them to identify errors).
_RESULT_STATUS_FIELDS = ("subtype", "stop_reason", "is_error", "api_error_status")


def _cli_session_state(msg: dict[str, Any]) -> str | None:
    """State reported by CLI ``session_state_changed`` frames (idle / running / requires_action); None for other frames."""
    if msg.get("type") != "system" or msg.get("subtype") != "session_state_changed":
        return None
    data = msg.get("data")
    state = data.get("state") if isinstance(data, dict) else None
    return state if isinstance(state, str) else None


class SessionManager:
    """Manages all active ClaudeSDKClient instances."""

    DEFAULT_ALLOWED_TOOLS: ClassVar[list[str]] = [
        "Skill",
        "Task",
        # Bash tools (sandbox + autoAllowBashIfSandboxed=True jointly allow them).
        "Bash",
        "BashOutput",
        "KillBash",
        # Built-in SDK tools (still guarded by PreToolUse file fences + settings.json deny).
        "Read",
        "Write",
        "Edit",
        "Grep",
        "Glob",
        "WebFetch",
        "AskUserQuestion",
    ]
    DEFAULT_SETTING_SOURCES: ClassVar[list[SettingSource]] = ["project"]

    def __init__(
        self,
        project_root: Path,
        meta_store: SessionMetaStore,
        data_root: Path | None = None,
        in_docker: bool = False,
        sandbox_enabled: bool = True,
        event_log_store: EventLogStore | None = None,
        sdk_id_timeout: float = 60.0,
    ):
        self.event_log_store = event_log_store or EventLogStore()
        # Timeout for CLI init to report new-session sdk_session_id; expiry means startup failure.
        self._sdk_id_timeout = sdk_id_timeout
        self.project_root = Path(project_root)
        # Tests construct SessionManager directly without going through
        # AssistantService, so we fall back to the default data root
        # ``project_root/projects``. Production passes the configured app_data_dir() explicitly.
        # Resolve both roots to prevent symlink-related relative_to failures in _resolve_project_cwd
        # (project_cwd already resolved); strict=False tolerates missing directories.
        self.data_root = (
            Path(data_root).resolve(strict=False)
            if data_root is not None
            else (self.project_root / "projects").resolve()
        )
        self.layout = DataRootLayout(self.data_root)
        self.meta_store = meta_store
        self.sessions: dict[str, ManagedSession] = {}
        # Session channels registered by sdk_session_id. Entry-stream lifetime follows panels, not
        # CLI processes: subscribers retain channels after eviction; revived sessions reuse them.
        # Release only without resident sessions or subscribers.
        self._channels: dict[str, SseChannel] = {}
        # Notification when autonomous CLI turns return sessions to running (project name, session ID);
        # see _persist_cli_resumed.
        self._autonomous_turn_listener: Callable[[str, str], None] | None = None
        # Evicting session → completion event; reconnect cold only after eviction, without interleaving.
        self._disconnecting: dict[str, asyncio.Event] = {}
        # Graceful send_disconnect timeout; callers then use unbounded cancel fallback.
        self._session_actor_shutdown_timeout: float = 15.0
        self._connect_locks: dict[str, asyncio.Lock] = {}
        # Cache instance invariants to avoid repeated path resolution in access-policy construction.
        self._project_root_resolved = self.project_root.resolve()
        # Actual agent_runtime_profile location: ``ARCREEL_PROFILE_DIR`` environment override >
        # ``self.project_root / "agent_runtime_profile"``（test-friendly：
        # without reading global ``lib.infra.env_init.PROJECT_ROOT``).
        profile_override = os.getenv("ARCREEL_PROFILE_DIR", "").strip()
        if profile_override:
            self._agent_profile_root = Path(profile_override).expanduser().resolve(strict=False)
        else:
            self._agent_profile_root = (self._project_root_resolved / "agent_runtime_profile").resolve(strict=False)
        # Resolve environment/profile directories here; the access-rule source consumes only
        # resolved process roots for zero-I/O pure construction.
        self.access_policy = AgentAccessPolicy(
            project_root=self._project_root_resolved,
            data_root=self.data_root,
            agent_profile_root=self._agent_profile_root,
            sandbox_enabled=sandbox_enabled,
            in_docker=in_docker,
        )
        self._load_config()
        self.ledger = Ledger(session_factory=getattr(meta_store, "_session_factory", None))
        # Options assembler owns dependencies and permits I/O, asynchronously producing SDK options. access_policy /
        # max_turns / session_factory / user_id use provider callbacks: the first two reflect
        # configure_sandbox_runtime / refresh_config changes immediately for later sessions; the latter two
        # preserve pre-extraction lazy self-attribute timing, sharing _finalize_turn's current _user_id
        # source so store/accounting never diverge into different per-user namespaces. _resolve_project_cwd
        # (project-name validation/scope) stays in session management, injected as a dependency.
        self._options_assembler = OptionsAssembler(
            data_root=self.data_root,
            allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
            setting_sources=self.DEFAULT_SETTING_SOURCES,
            access_policy_provider=lambda: self.access_policy,
            max_turns_provider=lambda: self.max_turns,
            resolve_project_cwd=self._resolve_project_cwd,
            session_factory_provider=lambda: getattr(self, "_session_factory", None),
            user_id_provider=lambda: getattr(self, "_user_id", DEFAULT_USER_ID),
        )

    def configure_sandbox_runtime(self, *, in_docker: bool, sandbox_enabled: bool) -> None:
        """Inject platform facts during startup (Docker nesting, kernel sandbox availability).

        ``in_docker`` forwards SandboxSettings.enableWeakerNestedSandbox. ``sandbox_enabled=False``
        (Windows fallback) disables SDK SandboxSettings and routes Bash through code allowlists.
        AgentAccessPolicy is immutable; replace it wholesale rather than mutating fields. Hooks/options
        builders read ``self.access_policy`` per call, so replacement applies to all subsequent sessions/tools."""
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
        session_id: str | None = None,
    ) -> Any:
        """Delegate to ``OptionsAssembler.build``: SessionManager only calls the assembler, no longer building
        options/hooks directly. The assembler owns credential injection, prompt assembly, and hook factories."""
        return await self._options_assembler.build(
            project_name,
            resume_id=resume_id,
            can_use_tool=can_use_tool,
            locale=locale,
            stderr=stderr,
            session_id=session_id,
        )

    def _build_session_store(self):
        """Delegate to the assembler's singleton session store. AssistantService obtains the same store as SDK
        options (same per-user namespace), sharing read/write caches."""
        return self._options_assembler.build_session_store()

    def _resolve_project_cwd(self, project_name: str) -> Path:
        """Resolve and validate per-session project working directory."""
        try:
            project_cwd = safe_join(self.layout.projects_dir, project_name)
        except PathTraversalError as exc:
            raise ValueError("invalid project name") from exc
        if not project_cwd.exists() or not project_cwd.is_dir():
            raise FileNotFoundError(f"project not found: {project_name}")
        return project_cwd

    def _build_entry_pipeline(self, managed: "ManagedSession") -> SessionEntryPipeline:
        """Build a session's event-log write-boundary pipeline.

        Read session_id through a provider: None until new sdk_session_id is ready, so the pipeline skips
        automatically (only unlogged init system messages occur in that window)."""
        return SessionEntryPipeline(
            self.event_log_store,
            session_id_provider=lambda: managed.resolved_sdk_id,
            project_name_provider=lambda: managed.project_name,
            broadcast=managed.channel.broadcast,
        )

    def _make_actor_message_callback(
        self,
        managed_ref: list["ManagedSession | None"],
    ) -> Callable[[Any], None]:
        """Sync on_message callback shared by send_new_session and get_or_connect.

        Runs inside the actor task. Order is load-bearing:
        CLI replays of user messages skip broadcast but still queue the message
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
            entry_uuid = (
                managed.sent_message_entries.get(str(msg_dict.get("uuid"))) if msg_dict.get("type") == "user" else None
            )
            if entry_uuid is not None:
                # CLI replayed users: logging already happened at started acceptance. Replay only links transcript
                # identities; mark copies for write-boundary mapping then skip, including repeated same-uuid replay.
                msg_dict[REPLAYED_USER_ECHO_KEY] = True
                msg_dict[REPLAYED_USER_ECHO_ENTRY_UUID_KEY] = entry_uuid
                managed._inbox.put_nowait(msg_dict)
                return
            self._handle_special_message(managed, msg_dict)
            managed._on_actor_message(msg_dict)
            managed._inbox.put_nowait(msg_dict)
            cli_state = _cli_session_state(msg_dict)
            if cli_state == "idle":
                managed._cli_idle_epoch = managed._running_epoch
                managed._inbox.put_nowait(_CliIdleNotice(epoch=managed._running_epoch))
            elif (
                (cli_state in ("running", "requires_action") or is_main_turn_activity(msg_dict))
                and not managed._inbox_stopped
                and managed.enter_running()
            ):
                # CLI work-start or main-thread frames before a running report enter running on read, without waiting for inbox:
                # idle cleanup must not disconnect autonomous turns, and earlier idle signals become stale.
                managed._inbox.put_nowait(_CliBusyNotice())

        return _on_message

    @staticmethod
    def _make_command_lifecycle_callback(
        managed_ref: list["ManagedSession | None"],
    ) -> Callable[[CommandLifecycle], None]:
        """CLI queued-message disposition: enqueue for inbox handling after output read beforehand."""

        def _on_command_lifecycle(lifecycle: CommandLifecycle) -> None:
            managed = managed_ref[0]
            if managed is None or lifecycle.state in ("queued", "completed"):
                return
            # Frame order determines intent: interrupted-turn result/cancelled precede the priority message's started.
            preempted = bool(managed.now_messages_in_flight)
            managed.now_messages_in_flight.discard(lifecycle.command_uuid)
            if managed.find_queued_message(cli_uuid=lifecycle.command_uuid) is None:
                return
            managed._inbox.put_nowait(_QueuedMessageSettled(lifecycle, preempted=preempted))

        return _on_command_lifecycle

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
            error: BaseException | None = None
            if not task.cancelled():
                error = task.exception()
                if error is not None:
                    logger.warning(
                        "session actor 异常退出 session_id=%s: %s",
                        managed.session_id,
                        redact_diagnostic_text(error),
                    )
            try:
                managed._inbox.put_nowait(_ActorExitNotice(error=error))
            except Exception:
                logger.debug("inbox sentinel push failed", exc_info=True)

        return _on_done

    async def send_new_session(
        self,
        project_name: str,
        content: str | list[dict[str, Any]],
        *,
        locale: str = DEFAULT_LOCALE,
        user_entry: dict[str, Any] | None = None,
        client_key: str | None = None,
    ) -> str:
        """Create a new session via send-first: start actor, send query, wait for sdk_session_id.

        ``user_entry`` is this message's finalized event-log entry. Once sdk_session_id is ready, inbox writes
        seq 0 before releasing the wait; ``managed.initial_user_log_entry`` carries the authoritative entry
        for acceptance responses."""
        if not SDK_AVAILABLE:
            exc = RuntimeError("claude_agent_sdk is not installed")
            raise _make_agent_startup_error(exc, project_name=project_name, session_id=None) from exc

        await self._ensure_capacity()
        temp_id = uuid4().hex
        managed_ref: list[ManagedSession | None] = [None]

        # Callbacks live through sessions but collect only during startup; clear/stop immediately after actor.start succeeds,
        # preserving complete startup evidence without retaining long-session stderr.
        startup_stderr = _StartupStderrCollector()

        try:
            options = await self._build_options(
                project_name,
                resume_id=None,
                can_use_tool=await self._build_can_use_tool_callback(temp_id, managed_ref),
                locale=locale,
                stderr=startup_stderr,
            )
        except Exception as exc:
            sdk_stderr = startup_stderr.render()
            startup_stderr.stop()
            raise _make_agent_startup_error(
                exc,
                project_name=project_name,
                session_id=None,
                sdk_stderr=sdk_stderr,
            ) from exc
        actor = SessionActor(
            client_factory=lambda: ClaudeSDKClient(options=options),
            on_message=self._make_actor_message_callback(managed_ref),
            on_command_lifecycle=self._make_command_lifecycle_callback(managed_ref),
        )

        managed = ManagedSession(
            session_id=temp_id,
            actor=actor,
            status="running",
            project_name=project_name,
            agent_pricing=agent_pricing_of(options),
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
            sdk_stderr = startup_stderr.render()
            startup_error = _make_agent_startup_error(
                exc, project_name=project_name, session_id=None, sdk_stderr=sdk_stderr
            )
            self.sessions.pop(temp_id, None)
            startup_stderr.stop()
            raise startup_error from exc
        # Register done callback BEFORE spawning processor to avoid a race
        # where the actor task completes before add_done_callback is attached，
        # leaving the completion notice un-pushed and _process_inbox hanging.
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
            finish naturally), bounded by _session_actor_shutdown_timeout so an
            SDK-side hang does not stall this error-only cleanup path on the
            graceful wait — on timeout the actor is cancelled so it does not leak
            with the failed session; then belt-and-suspenders cancels the
            processor in case it is stuck elsewhere.
            """
            self.sessions.pop(temp_id, None)
            # sdk_session_id readiness swapped registration to the formal ID; clear both keys.
            self.sessions.pop(managed.session_id, None)
            self._release_channel(managed.session_id)
            try:
                await asyncio.wait_for(managed.send_disconnect(), timeout=self._session_actor_shutdown_timeout)
            except TimeoutError:
                logger.warning(
                    "send_disconnect on error path 超时，走 cancel 兜底 session_id=%s",
                    temp_id,
                )
                await managed.actor.cancel_and_wait()
            except Exception:
                logger.exception(
                    "send_disconnect on error path failed session_id=%s",
                    temp_id,
                )
            # Still running after disconnect (send_disconnect preserves it, including failures/timeouts);
            # canceling _process_task below makes _process_inbox CancelledError
            # write interrupted. Startup failures are not interruptions; set error first to close that branch.
            if managed.status == "running":
                managed.status = "error"
            if managed._process_task is not None and not managed._process_task.done():
                managed._process_task.cancel()
                await asyncio.gather(managed._process_task, return_exceptions=True)
            startup_stderr.stop()

        # Initial messages bypass queues: inbox logs seq 0 after SDK ID readiness.
        # Register uuid so CLI replay only links transcript identity, never logs twice.
        cli_uuid = str(uuid4())
        if user_entry is not None:
            managed.sent_message_entries[cli_uuid] = str(user_entry["uuid"])
        managed.last_user_prompt = _content_text(content)
        managed.turn_usage_recorded = False

        try:
            await managed.send_query(_user_message_frame(content, cli_uuid))
        except Exception as exc:
            sdk_stderr = startup_stderr.render()
            startup_error = _make_agent_startup_error(
                exc, project_name=project_name, session_id=None, sdk_stderr=sdk_stderr
            )
            await _cleanup_on_error()
            raise startup_error from exc

        # Wait for sdk_session_id with timeout. Monitor the inbox processor rather
        # than the actor task: actor may exit immediately after enqueueing init,
        # while the processor still has to persist that already-observed session id.
        # The done callback appends a completion notice after all actor messages, so processor
        # completion is the safe proof that no queued init remains to be consumed.
        event_task = asyncio.create_task(managed.sdk_id_event.wait())
        processor_task = managed._process_task
        assert processor_task is not None
        watch_tasks: set[asyncio.Task] = {event_task, processor_task}
        actor_task = actor.task
        try:
            await asyncio.wait(
                watch_tasks,
                timeout=self._sdk_id_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not event_task.done():
                event_task.cancel()

        if not managed.sdk_id_event.is_set():
            startup_exception = None
            if processor_task.done() and not processor_task.cancelled():
                try:
                    startup_exception = processor_task.result()
                except Exception as exc:
                    # Normal _process_inbox completion returns actor startup exceptions; unexpected task crashes
                    # rethrow from Task.result(). Both provide startup-failure evidence,
                    # requiring shared cleanup below before structured AgentStartupError wrapping.
                    startup_exception = exc
            if actor_task is not None and actor_task.done():
                logger.error("session actor 提前退出，未获得 sdk_session_id temp_id=%s", temp_id)
            else:
                logger.error("等待 sdk_session_id 超时 temp_id=%s", temp_id)
            managed.cancel_pending_questions("session creation timed out")
            sdk_stderr = startup_stderr.render()
            await _cleanup_on_error()
            if startup_exception is not None:
                raise _make_agent_startup_error(
                    startup_exception,
                    project_name=project_name,
                    session_id=None,
                    sdk_stderr=sdk_stderr,
                ) from startup_exception
            raise TimeoutError("SDK 会话创建超时")

        startup_stderr.stop()

        sdk_id = managed.resolved_sdk_id
        assert sdk_id is not None
        # Key swap already done in _on_sdk_session_id_received
        assert managed.session_id == sdk_id

        if managed.initial_user_entry_error is not None:
            # Initial log-write failure means acceptance failure: missing seq 0 can never render the timeline beginning,
            # whose sole source is event logs. Match ordinary acceptance semantics:
            # report failure explicitly, persist error status, and stop background
            # continuation. Clean before status persistence, since inbox CLI idle handling writes terminal state;
            # writing error after cleanup prevents concurrent overwrites.
            managed.cancel_pending_questions("initial user entry persist failed")
            # Set in-memory error early: _cleanup_on_error cancels _process_task, whose
            # _process_inbox CancelledError checks status == "running"
            # to write interrupted. Early error prevents redundant interrupted persistence/
            # broadcasts, while DB error persistence waits until cleanup finishes.
            managed.status = "error"
            await _cleanup_on_error()
            try:
                await self.meta_store.update_status(sdk_id, "error")
            except Exception:
                logger.exception("持久化 error 状态失败 session_id=%s", sdk_id)
            raise RuntimeError("新会话首条用户消息写入事件日志失败") from managed.initial_user_entry_error

        return sdk_id

    async def _process_inbox(self, managed: ManagedSession) -> BaseException | None:
        """Drain ManagedSession._inbox and run async post-processing.

        Replaces the async tail of _consume_messages. The synchronous bits
        (state machine, buffer add, broadcast, _handle_special_message,
        replay tagging) already ran inside the actor's on_message
        callback, so this coroutine only handles:
        - sdk_session_id capture (DB create, tag, key swap, event set)
        - writing a queued message to the log once the CLI starts it
        - _finalize_turn on result messages
        - leaving running when the CLI reports idle or the actor exits
        - terminal status on cancel/error
        """
        try:
            while True:
                msg_dict = await managed._inbox.get()
                if isinstance(msg_dict, _ActorExitNotice):
                    # CLI gone: leave remaining messages unsent in trays for users, without automatic resend.
                    managed.mark_queued_messages_unsent()
                    if msg_dict.error is not None:
                        if managed.resolved_sdk_id is not None:
                            await self._mark_session_terminal(managed, "error", "session actor failed")
                        else:
                            managed.status = "error"
                    elif managed.status == "running" and managed.resolved_sdk_id is not None:
                        # CLI exit without idle: use finalized turn outcome if available, otherwise interrupted.
                        await self._mark_session_terminal(
                            managed, managed.turn_outcome or "interrupted", "session actor exited"
                        )
                    return msg_dict.error
                if isinstance(msg_dict, _CliIdleNotice):
                    # Initial log failure short-circuits like result handling; send_new_session persists error.
                    if managed.initial_user_entry_error is None:
                        await self._settle_cli_idle(managed, msg_dict.epoch)
                    continue
                if isinstance(msg_dict, _CliBusyNotice):
                    await self._persist_cli_resumed(managed)
                    continue
                if isinstance(msg_dict, _QueuedMessageSettled):
                    await self._settle_queued_message(managed, msg_dict.lifecycle, preempted=msg_dict.preempted)
                    continue
                if msg_dict is None:
                    return None
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
                if is_main_turn_activity(msg_dict):
                    # Main-thread output starts a new turn, invalidating the previous outcome.
                    managed.turn_outcome = None
                # Event-log write boundary: normalize/log each message after sdk_session_id readiness.
                # handle_message catches exceptions internally without interrupting consumption.
                if managed.entry_pipeline is not None and managed.resolved_sdk_id is not None:
                    await managed.entry_pipeline.handle_message(msg_dict)
                managed.usage_meter.observe(msg_dict)
                if msg_dict.get("type") == "result":
                    if managed.initial_user_entry_error is not None:
                        # Initial log persistence failed; send_new_session cleanup
                        # will cancel this task. Skip finalization here to avoid broadcasting/persisting
                        # nonerror terminal states (e.g. completed) then replacing with error.
                        continue
                    try:
                        await self._finalize_turn(managed, msg_dict)
                    except Exception:
                        # Failed finalization may leave status/interrupt_requested/cleanup partially updated;
                        # use terminal fallback, not continued looping into inconsistent next-turn state.
                        logger.exception(
                            "_finalize_turn 失败，走 error 终态兜底 session_id=%s",
                            managed.session_id,
                        )
                        # Inbox stops here; ignore subsequent frames.
                        managed._inbox_stopped = True
                        with contextlib.suppress(Exception):
                            await self._mark_session_terminal(managed, "error", "finalize failed")
                        return None
        except asyncio.CancelledError:
            # Only mark interrupted if session was actually running. Cancel can
            # also happen during failed send_new_session cleanup or normal
            # shutdown, where the status is already terminal / error.
            if managed.status == "running":
                try:
                    await self._mark_session_terminal(
                        managed, managed.turn_outcome or "interrupted", "session interrupted"
                    )
                except Exception:
                    logger.exception(
                        "_mark_session_terminal 在 cancel 路径失败 session_id=%s",
                        managed.session_id,
                    )
            raise
        except Exception:
            logger.exception("_process_inbox 异常 session_id=%s", managed.session_id)
            managed._inbox_stopped = True
            try:
                await self._mark_session_terminal(managed, "error", "session error")
            except Exception:
                logger.debug("_mark_session_terminal cleanup failed", exc_info=True)
            raise

    async def get_or_connect(
        self,
        session_id: str,
        *,
        meta: SessionMeta | None = None,
        locale: str = DEFAULT_LOCALE,
        resumable: bool = True,
    ) -> ManagedSession:
        """Get existing managed session or spin up an actor for resumed session.

        ``locale`` shapes the system prompt when a fresh session starts.
        ``SystemPromptPreset.snapshot`` freezes the first-request system prompt,
        including appended instructions, across resume until compaction or a new
        session (CLI >= 2.1.257). Within that snapshot lifetime, resumed sessions
        retain the original language regulation. Already-resident sessions return
        from cache. Here locale matters for ``resumable=False`` sessions, which
        start fresh.

        ``resumable=False`` applies to sessions with metadata but empty transcripts (branches rewriting the
        first message). They have no resumable history, so start fresh with identity prespecified via
        ``session_id=``. After the first turn creates transcripts, revival resumes normally."""
        cached = self.sessions.get(session_id)
        if cached is not None and session_id not in self._disconnecting and not cached.actor_exited:
            return cached

        while True:
            # Per-session lock prevents concurrent connect() for the same session_id.
            lock = self._connect_locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                if self._connect_locks.get(session_id) is not lock:
                    # Eviction removed this lock while waiting; queue on the current lock, avoiding concurrent connections with its holder.
                    continue
                cached, detached = await self._retire_exited_session(session_id)
                if cached is not None:
                    return cached
                try:
                    managed = await self._connect_resumed_session(
                        session_id, meta=meta, locale=locale, resumable=resumable
                    )
                except BaseException:
                    channel = self._channels.get(session_id)
                    if detached is not None and channel is not None:
                        # Old session evicted, new connection failed: discard queues like eviction, broadcasting tray removals to remaining subscribers.
                        for queued in detached.messages:
                            channel.broadcast({"type": "queued_message", "op": "remove", "id": queued.id})
                    raise
                if detached is not None:
                    managed.adopt_queued_messages(detached)
                return managed

    async def _retire_exited_session(self, session_id: str) -> tuple[ManagedSession | None, _DetachedQueue | None]:
        """Call under the connection lock: return a usable resident session, otherwise queue records to transfer into a new connection.

        Wait for ongoing eviction, then rebuild as cold. If the actor exited (CLI gone, session not yet cleaned),
        wait for inbox exit-notice processing, take queue records without broadcasting (unsent messages remain
        in trays, never automatically resent), then evict."""
        detached: _DetachedQueue | None = None
        while (cached := self.sessions.get(session_id)) is not None:
            eviction = self._disconnecting.get(session_id)
            if eviction is not None:
                await eviction.wait()
                continue
            if not cached.actor_exited:
                return cached, None
            if cached._process_task is not None and not cached._process_task.done():
                # Exit notices mark messages unsent and sessions terminal; take queue records only afterward.
                await asyncio.wait({cached._process_task})
                continue
            # Inbox stopped before actor, so missed exit notices; CLI is still gone.
            cached.mark_queued_messages_unsent()
            detached = cached.detach_queued_messages()
            await self._evict_one(cached)
        return None, detached

    async def _connect_resumed_session(
        self, session_id: str, *, meta: SessionMeta | None, locale: str, resumable: bool
    ) -> ManagedSession:
        """Start/register a cold-resumed actor while the caller holds the connection lock."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")

        if not SDK_AVAILABLE:
            exc = RuntimeError("claude_agent_sdk is not installed")
            raise _make_agent_startup_error(exc, project_name=meta.project_name, session_id=session_id) from exc

        await self._ensure_capacity()
        managed_ref: list[ManagedSession | None] = [None]

        # As in send_new_session: lossless startup-only collection, released on success.
        startup_stderr = _StartupStderrCollector()

        try:
            options = await self._build_options(
                meta.project_name,
                meta.id if resumable else None,  # SessionMeta.id equals sdk_session_id.
                can_use_tool=await self._build_can_use_tool_callback(session_id, managed_ref),
                locale=locale,
                stderr=startup_stderr,
                session_id=None if resumable else meta.id,
            )
        except Exception as exc:
            sdk_stderr = startup_stderr.render()
            startup_stderr.stop()
            raise _make_agent_startup_error(
                exc,
                project_name=meta.project_name,
                session_id=session_id,
                sdk_stderr=sdk_stderr,
            ) from exc
        actor = SessionActor(
            client_factory=lambda: ClaudeSDKClient(options=options),
            on_message=self._make_actor_message_callback(managed_ref),
            on_command_lifecycle=self._make_command_lifecycle_callback(managed_ref),
        )

        resumed_status: SessionStatus = (
            meta.status if meta.status in ("idle", "running", "interrupted", "error", "closed") else "idle"
        )
        managed = ManagedSession(
            session_id=meta.id,  # Now sdk_session_id.
            actor=actor,
            status=resumed_status,
            project_name=meta.project_name,
            agent_pricing=agent_pricing_of(options),
            # Resumed CLI carries cumulative usage; use its first result as differential baseline.
            usage_meter=TurnUsageMeter(resumed=resumable),
            resolved_sdk_id=meta.id,  # Mark registered to prevent duplicate DB rows.
            channel=self._session_channel(meta.id),
        )
        managed.sdk_id_event.set()  # Existing sessions need not wait for sdk_id.
        managed.entry_pipeline = self._build_entry_pipeline(managed)
        managed_ref[0] = managed
        managed.last_activity = time.monotonic()
        self.sessions[session_id] = managed

        try:
            await actor.start()
        except Exception as exc:
            sdk_stderr = startup_stderr.render()
            startup_error = _make_agent_startup_error(
                exc,
                project_name=meta.project_name,
                session_id=session_id,
                sdk_stderr=sdk_stderr,
            )
            self.sessions.pop(session_id, None)
            self._release_channel(session_id)
            raise startup_error from exc
        finally:
            startup_stderr.stop()

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
        content: str | list[dict[str, Any]],
        *,
        meta: SessionMeta | None = None,
        locale: str = DEFAULT_LOCALE,
        user_entry: dict[str, Any] | None = None,
        client_key: str | None = None,
        resumable: bool = True,
    ) -> dict[str, Any]:
        """Deliver user messages immediately to session CLI even during running turns; CLI queues/merges them.

        Initially return ``{"queued_message": ...}``. Only when CLI starts processing (started in
        ``command_lifecycle``) write event logs and assign seq (``_settle_queued_message``). ``user_entry``
        is the accepted user entry, built from ``content`` if omitted. Same-``client_key`` retries never resend:
        return the same queued message while pending or authoritative ``{"entry": ...}`` once logged.

        ``locale`` is forwarded to ``get_or_connect``; it shapes the system
        prompt only when the revival starts a fresh session (see there).
        ``resumable`` forwards to ``get_or_connect``; see its documentation."""
        exited = self.sessions.get(session_id)
        if client_key is not None and exited is not None and exited.actor_exited:
            # CLI exited: return already accepted retries (e.g. unsent messages) unchanged, without revival merely for dedup.
            async with exited.send_lock:
                accepted = await self._find_sent_message(exited, session_id, client_key)
            if accepted is not None:
                return accepted

        managed = await self.get_or_connect(session_id, meta=meta, locale=locale, resumable=resumable)
        managed.last_activity = time.monotonic()

        # Dedup, registration, delivery, and failure rollback share a critical section; retries claim only completed deliveries.
        async with managed.send_lock:
            if client_key is not None:
                accepted = await self._find_sent_message(managed, session_id, client_key)
                if accepted is not None:
                    return accepted

            if user_entry is None:
                user_entry = build_user_entry(
                    [{"type": "text", "text": content}] if isinstance(content, str) else content
                )
            queued = QueuedMessage(entry=user_entry, cli_uuid=str(uuid4()), content=content, client_key=client_key)
            managed.queued_messages.append(queued)
            if client_key is not None:
                managed.sent_client_keys[client_key] = queued.id
            # Failed delivery means failed acceptance: remove queues/release keys, permitting same-key redelivery.
            await self._deliver_queued_message(managed, queued, on_failure=managed.discard_queued_message)
            return {"queued_message": queued.to_payload()}

    async def _deliver_queued_message(
        self,
        managed: ManagedSession,
        queued: QueuedMessage,
        *,
        on_failure: Callable[[QueuedMessage], None],
        priority: Literal["now"] | None = None,
    ) -> None:
        """Deliver registered queued messages to CLI; on failure call ``on_failure`` to withdraw registration before raising.
        ``priority="now"`` interrupts the current turn to process this message first.

        Callers hold ``managed.send_lock``."""
        session_id = managed.session_id
        cli_uuid = queued.cli_uuid
        managed.sent_message_entries[cli_uuid] = queued.id
        # Broadcast before delivery: started-triggered removals must not precede addition broadcasts.
        managed.channel.broadcast({"type": "queued_message", "op": "upsert", "message": queued.to_payload()})
        # Registration enters running, preventing cleanup/eviction from disconnecting before delivery.
        epoch = managed._running_epoch
        turn_in_progress = not managed.enter_running()

        delivering = False
        try:
            await self.meta_store.update_status(session_id, "running")
            live = self.sessions.get(session_id)
            if live is not managed:
                # Session evicted during persistence: this running write followed eviction's terminal state. Restore current truth:
                # new same-ID session state if present, otherwise eviction terminal state; then finalize failure.
                await self.meta_store.update_status(session_id, managed.status if live is None else live.status)
                raise RuntimeError("session evicted while the message was being delivered")
            delivering = True
            await managed.send_query(
                _user_message_frame(queued.content, cli_uuid, priority=priority), sdk_session_id=session_id
            )
        except Exception as exc:
            logger.error("会话消息处理失败: %s", redact_diagnostic_text(exc))
            on_failure(queued)
            if turn_in_progress:
                # Metadata failure means no CLI delivery; ongoing turns still settle through idle.
                # CLI write failure means broken pipes and actor exit; send_query/actor-exit paths persist error.
                if not delivering:
                    managed.abandon_running(epoch)
                raise
            if session_id in self._disconnecting or self.sessions.get(session_id) is not managed:
                # Evicting/already-evicted while awaiting metadata: eviction owns terminal persistence; do not race with error.
                raise
            managed.status = "error"
            try:
                await self.meta_store.update_status(session_id, "error")
            except Exception:
                logger.exception("持久化 error 状态失败 session_id=%s", session_id)
            # Subscribers may already see running with queue messages; no turn remains to emit idle, so explicitly broadcast terminal state.
            managed.channel.broadcast({"type": "runtime_status", "status": "error", "reason": "send failed"})
            raise

    async def _find_sent_message(
        self, managed: ManagedSession, session_id: str, client_key: str
    ) -> dict[str, Any] | None:
        """Find accepted messages by idempotency key: queued messages while pending, authoritative entries once logged, None if never accepted.

        Accepted messages whose log writes failed raise ``UnrecordedMessageError`` instead of being resent.

        Callers hold send_lock until delivery/failure rollback finishes; queues contain only previously completed deliveries."""
        entry_uuid = managed.sent_client_keys.get(client_key)
        if entry_uuid is not None:
            queued = managed.find_queued_message(message_id=entry_uuid)
            if queued is not None:
                return {"queued_message": queued.to_payload()}
        entry = await self.event_log_store.find_by_client_key(session_id, client_key)
        if entry is not None:
            return {"entry": entry}
        if entry_uuid is not None:
            # Keys release only when unaccepted messages leave queues; present key + absent log means postacceptance log failure.
            raise UnrecordedMessageError(f"message {entry_uuid} was accepted but not recorded")
        return None

    async def _settle_queued_message(
        self, managed: ManagedSession, lifecycle: CommandLifecycle, *, preempted: bool = False
    ) -> None:
        """CLI queued-message disposition: started means accepted; write event logs then remove from the queue.

        Run in inbox order after preceding output. Broadcast entries before removal, so when trays lose the
        message it already exists in the timeline. Logged messages leave queues; ignore later cancelled frames
        (also emitted for the interrupted turn's first message)."""
        queued = managed.find_queued_message(cli_uuid=lifecycle.command_uuid)
        if queued is None:
            return
        if lifecycle.state == "started":
            # Usage follows turns: delivery may queue behind running work; include in current accounting prompt only on acceptance.
            text = _content_text(queued.entry.get("content", []))
            managed.last_user_prompt = text if managed.turn_usage_recorded else f"{managed.last_user_prompt}\n{text}"
            managed.turn_usage_recorded = False
            if managed.entry_pipeline is not None:
                await managed.entry_pipeline.append_user_entry(queued.entry, client_key=queued.client_key)
            managed.remove_queued_message(queued)
            return
        if queued.withdrawal == "send_now":
            # Before/after immediate-send withdrawal response, CLI still did not process it: redeliver at now priority.
            await self._redeliver_in_inbox(managed, queued, lifecycle.command_uuid, priority="now")
            return
        if queued.withdrawal is not None:
            # After failed withdrawal (CLI took it), CLI still did not process it: settle by original user intent.
            managed.withdraw_queued_message(queued)
            return
        if preempted and lifecycle.state == "cancelled":
            # Immediate-send interruption discarded it before conversation entry; redeliver behind the priority message.
            await self._redeliver_in_inbox(managed, queued, lifecycle.command_uuid)
            return
        logger.warning(
            "排队消息未被 CLI 处理 session_id=%s state=%s message_id=%s",
            managed.session_id,
            lifecycle.state,
            queued.id,
        )
        managed.discard_queued_message(queued)

    async def withdraw_queued_message(
        self, session_id: str, message_id: str, intent: WithdrawalIntent
    ) -> tuple[WithdrawalOutcome, QueuedMessage | None]:
        """Withdraw queued messages for user editing/deletion through actor/CLI; remove only after successful withdrawal.

        ``("withdrawn", 消息)`` means withdrawn from CLI/queue and idempotency key released.
        ``("accepted", None)`` means CLI took it and it enters conversation normally; later cancelled frames
        settle using remembered ``intent`` (``_settle_queued_message``). Raise ``QueuedMessageNotFoundError``
        for messages never queued here, or ``QueuedMessageWithdrawalPendingError`` while the same message
        still awaits CLI withdrawal response."""
        located = self._locate_queued_message(session_id, message_id)
        if located is None:
            return "accepted", None
        managed, queued = located
        if queued.state == "unsent":
            # CLI gone, nothing to withdraw: remove directly by intent.
            queued.withdrawal = intent
            managed.withdraw_queued_message(queued)
            return "withdrawn", queued
        if await self._cancel_in_cli(managed, queued, intent):
            managed.withdraw_queued_message(queued)
        if queued.withdrawn:
            return "withdrawn", queued
        return "accepted", None

    async def send_queued_message_now(self, session_id: str, message_id: str) -> SendNowOutcome:
        """Immediately send a queued message: withdraw from CLI, then redeliver on success with a new uuid and ``now`` priority.

        CLI interrupts the turn to process it first. It remains the same queued message (unchanged id/key),
        logged normally on acceptance. ``"sent"`` means redelivered; ``"accepted"`` means already taken by
        CLI, so no resend and normal conversation entry. If CLI later reports cancelled, redeliver with ``now``.
        Exceptions match ``withdraw_queued_message``."""
        located = self._locate_queued_message(session_id, message_id)
        if located is None:
            return "accepted"
        managed, queued = located
        if queued.state == "unsent":
            # CLI exited with no interruptible turn; resend as unsent.
            await self.resend_queued_message(session_id, message_id)
            return "sent"
        cancelled_uuid = queued.cli_uuid
        if await self._cancel_in_cli(managed, queued, "send_now"):
            await self._redeliver_after_cancel(managed, queued, cancelled_uuid, priority="now")
        # cancelled arrived before withdrawal response; inbox already redelivered and changed uuid.
        return "sent" if queued.cli_uuid != cancelled_uuid else "accepted"

    def _locate_queued_message(self, session_id: str, message_id: str) -> tuple[ManagedSession, QueuedMessage] | None:
        """Find queued messages for withdrawal/immediate send; None when they already entered the conversation."""
        managed = self.sessions.get(session_id)
        queued = managed.find_queued_message(message_id=message_id) if managed is not None else None
        if managed is None or queued is None:
            if managed is not None and managed.was_sent(message_id):
                # Queue already left: action clicked before tray-removal delivery, but message entered conversation.
                return None
            raise QueuedMessageNotFoundError(f"queued message {message_id} not found in session {session_id}")
        if queued.withdrawing:
            raise QueuedMessageWithdrawalPendingError(f"queued message {message_id} is being withdrawn")
        return managed, queued

    @staticmethod
    async def _cancel_in_cli(
        managed: ManagedSession, queued: QueuedMessage, intent: WithdrawalIntent | Literal["send_now"]
    ) -> bool:
        """Record withdrawal intent and request CLI cancellation via actor; return whether CLI removed the message.

        If CLI already rejected an earlier withdrawal, do not ask again; update latest intent and return False."""
        previous_intent = queued.withdrawal
        queued.withdrawal = intent
        if previous_intent is not None:
            return False

        queued.withdrawing = True
        try:
            # Enqueue under send lock: withdrawal follows prior deliveries, so CLI already has the message.
            async with managed.send_lock:
                cmd = SessionCommand(type="cancel", message_uuid=queued.cli_uuid)
                await managed.actor.enqueue(cmd)
            await cmd.done.wait()
        finally:
            queued.withdrawing = False
        if cmd.error is not None:
            queued.withdrawal = None
            raise cmd.error
        return bool(cmd.cancelled)

    async def _redeliver_in_inbox(
        self,
        managed: ManagedSession,
        queued: QueuedMessage,
        cancelled_uuid: str,
        *,
        priority: Literal["now"] | None = None,
    ) -> None:
        """Inbox redelivery: failed delivery already marked messages unsent; do not let it terminate the inbox."""
        with contextlib.suppress(Exception):
            await self._redeliver_after_cancel(managed, queued, cancelled_uuid, priority=priority)

    async def _redeliver_after_cancel(
        self,
        managed: ManagedSession,
        queued: QueuedMessage,
        cancelled_uuid: str,
        *,
        priority: Literal["now"] | None = None,
    ) -> None:
        """CLI removed an unaccepted message: resend unchanged with a new uuid, keeping the same queued message.

        ``cancelled_uuid`` identifies the removed delivery. Both withdrawal responses and CLI cancelled frames
        trigger redelivery; the first synchronously changes uuid, making the other a no-op. Failure marks it unsent."""
        if queued.cli_uuid != cancelled_uuid or queued not in managed.queued_messages:
            return
        queued.cli_uuid = str(uuid4())
        queued.withdrawal = None

        def _not_delivered(failed: QueuedMessage) -> None:
            # No longer in CLI queue: mark unsent, leaving resend/edit/delete choices to users.
            managed.now_messages_in_flight.discard(failed.cli_uuid)
            failed.state = "unsent"
            managed.channel.broadcast({"type": "queued_message", "op": "upsert", "message": failed.to_payload()})

        # Share send lock so later withdrawals follow this delivery.
        async with managed.send_lock:
            if queued not in managed.queued_messages:
                return
            # Register only on confirmed delivery: it may leave queues while awaiting locks; stale registration would misclassify later aborted_* turns as priority interruptions.
            if priority == "now":
                managed.now_messages_in_flight.add(queued.cli_uuid)
                # CLI processes first; move tray entry first too.
                managed.move_queued_message_first(queued)
            await self._deliver_queued_message(managed, queued, on_failure=_not_delivered, priority=priority)

    async def resend_queued_message(
        self,
        session_id: str,
        message_id: str,
        *,
        meta: SessionMeta | None = None,
        locale: str = DEFAULT_LOCALE,
    ) -> QueuedMessage:
        """Resubmit an unsent message: same queued message (unchanged ``id``/key), new ``cli_uuid``.

        If CLI exited, disconnect the old connection and revive the session, transferring unsent messages
        without changing trays. Already redelivered (``queued``) messages return unchanged without another
        send. Failed delivery restores unsent state. Foreign queued messages raise ``QueuedMessageNotFoundError``."""
        managed = self.sessions.get(session_id)
        queued = managed.find_queued_message(message_id=message_id) if managed is not None else None
        if managed is None or queued is None:
            raise QueuedMessageNotFoundError(f"queued message {message_id} not found in session {session_id}")
        if queued.state != "unsent":
            return queued
        if managed.actor_exited:
            # Revive session, transferring unsent messages into the new connection (get_or_connect).
            managed = await self.get_or_connect(session_id, meta=meta, locale=locale)
            if managed.find_queued_message(message_id=message_id) is not queued or queued.state != "unsent":
                raise QueuedMessageNotFoundError(f"queued message {message_id} not found in session {session_id}")

        def _back_to_unsent(failed: QueuedMessage) -> None:
            failed.state = "unsent"
            managed.channel.broadcast({"type": "queued_message", "op": "upsert", "message": failed.to_payload()})

        async with managed.send_lock:
            if queued not in managed.queued_messages:
                raise QueuedMessageNotFoundError(f"queued message {message_id} not found in session {session_id}")
            if queued.state != "unsent":
                return queued
            queued.state = "queued"
            queued.cli_uuid = str(uuid4())
            await self._deliver_queued_message(managed, queued, on_failure=_back_to_unsent)
        return queued

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

        # Ordinary interruption stops only the current turn, preserving CLI queues to start subsequent turns normally.
        managed.interrupt_requested = True
        managed.cancel_pending_questions("session interrupted by user")

        try:
            await managed.send_interrupt()
        except Exception:
            logger.exception("发送 interrupt 命令失败 session_id=%s", session_id)
            managed.status = "error"
            return managed.status

        managed.last_activity = time.monotonic()
        # Turn result records interruption; CLI idle finalizes session as "interrupted".
        return managed.status

    def _handle_special_message(self, managed: ManagedSession, msg_dict: dict[str, Any]) -> None:
        """Handle result messages before broadcast."""
        if msg_dict.get("type") == "result":
            msg_dict["session_status"] = self._resolve_result_status(
                msg_dict,
                interrupt_requested=managed.interrupt_requested,
                preempted=bool(managed.now_messages_in_flight),
            )
            # Interrupt flags apply only to the next result and are consumed on frame arrival: queued-message turns
            # may finish before current finalization and must not inherit flags.
            managed.interrupt_requested = False
        elif msg_dict.get("type") == "system" and msg_dict.get("subtype") == "init":
            self._check_auto_memory_path(managed, msg_dict)

    def _check_auto_memory_path(self, managed: ManagedSession, init_msg: dict[str, Any]) -> None:
        """Check init-reported auto-memory directory against assembly-time redirected project memory.

        Log mismatches as errors but permit sessions: failed redirection merely stores notes in native-derived
        locations while creation works, making blocking much costlier than misplaced notes. Never follow the
        reported path in fences: compile expected allowances; trusting subprocess self-report would expand writes.

        ``memory_paths`` is CLI init's @internal field. Absence (old CLI / auto memory disabled) means no
        check; only an explicitly different reported directory is a mismatch."""
        raw = init_msg.get("data")
        data = raw if isinstance(raw, dict) else init_msg
        memory_paths = data.get("memory_paths")
        if not isinstance(memory_paths, dict):
            return
        reported = memory_paths.get("auto")
        if not reported:
            return
        try:
            expected = project_memory_dir(self._resolve_project_cwd(managed.project_name))
        except (ValueError, FileNotFoundError):
            return
        if Path(str(reported)).resolve(strict=False) == expected.resolve(strict=False):
            return
        logger.error(
            "auto memory 目录与装配预期不符，项目记忆笔记将落到别处",
            extra={
                "session_id": managed.session_id,
                "project_name": managed.project_name,
                "expected_auto_memory_dir": str(expected),
                "reported_auto_memory_dir": str(reported),
            },
        )

    def set_autonomous_turn_listener(self, listener: Callable[[str, str], None] | None) -> None:
        """Register notifications when autonomous CLI turns return sessions to running (project name, session ID)."""
        self._autonomous_turn_listener = listener

    async def _persist_cli_resumed(self, managed: ManagedSession) -> None:
        """Persist/notify return to running without sending (CLI reports work/new turns).

        Typically a CLI-autonomous turn after background completion. Open panels receive turns through
        resident entry streams; notifications inform session lists/other views of running state."""
        try:
            await self.meta_store.update_status(managed.session_id, "running")
        except Exception:
            # Runs inside inbox: exceptions would kill inbox while actors remain alive, leaving no finalizer.
            # Memory already changed; persistence is repaired by terminal-state writes on leaving running.
            logger.exception("持久化自主轮次 running 状态失败 session_id=%s", managed.session_id)
        # Resident entry streams immediately push running before visible output, avoiding panels stuck on previous terminal state.
        managed.channel.broadcast({"type": "runtime_status", "status": "running", "reason": "cli resumed"})
        listener = self._autonomous_turn_listener
        if listener is None:
            return
        try:
            listener(managed.project_name, managed.session_id)
        except Exception:
            logger.exception("自主轮次通知失败 session_id=%s", managed.session_id)

    async def _finalize_turn(self, managed: ManagedSession, result_msg: dict[str, Any]) -> None:
        """Finalize one turn's result: record outcome/usage without changing session state.

        result ends only one turn; CLI may immediately begin another. Leave running only on CLI idle
        (see _settle_cli_idle)."""
        managed.cancel_pending_questions("session completed")
        explicit = str(result_msg.get("session_status") or "").strip()
        outcome: SessionStatus = (
            explicit
            if explicit in ("completed", "error", "interrupted")
            else self._resolve_result_status(
                result_msg,
                interrupt_requested=managed.interrupt_requested,
            )
        )
        managed.turn_outcome = outcome
        managed.last_turn_result = result_msg
        managed.last_activity = time.monotonic()
        if outcome == "error":
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
            await self._record_assistant_usage(managed, result_msg, outcome)
        except Exception:
            logger.exception("记录 assistant usage 失败 session_id=%s", managed.session_id)
        managed.turn_usage_recorded = True
        managed.interrupt_requested = False

    async def _settle_cli_idle(self, managed: ManagedSession, epoch: int) -> None:
        """CLI idle: leave running using the latest turn's outcome.

        Run in inbox order after every turn entry. If a message arrives or CLI starts working after idle was
        read, the notice is stale; stay running until the next idle."""
        if managed.status != "running" or epoch != managed._running_epoch:
            return
        status: SessionStatus = managed.turn_outcome or "completed"
        managed.status = status
        managed.last_activity = time.monotonic()
        # Post-result interrupts have no turn to finalize; retaining them would misclassify next-turn failures as interruption.
        managed.interrupt_requested = False
        await self.meta_store.update_status(managed.session_id, status)
        if epoch != managed._running_epoch:
            # During persistence, new work/delivery reenters running; its write may precede this terminal write,
            # so rewrite running last. Never broadcast obsolete terminal states or schedule cleanup.
            if managed.status == "running":
                await self.meta_store.update_status(managed.session_id, "running")
            return
        result = managed.last_turn_result or {}
        managed.channel.broadcast(
            {
                "type": "runtime_status",
                "status": status,
                "reason": "cli idle",
                **{key: result[key] for key in _RESULT_STATUS_FIELDS if key in result},
            }
        )
        self._schedule_cleanup(managed.session_id)

    async def _record_assistant_usage(
        self,
        managed: ManagedSession,
        result_msg: dict[str, Any],
        final_status: SessionStatus,
    ) -> None:
        """Turn usage: one provider-call record per used model, priced by connection-injected Agent credentials and their then-current multipliers/bindings."""
        usages = managed.usage_meter.close_turn(result_msg.get("model_usage"))
        if not usages:
            return

        # User-interrupted turns count cancelled, not failed, preserving failure rates; costs still count:
        # completed requests before interruption are real spending, unlike zero-cost cancellation with no results/unknown cost.
        if final_status == "completed":
            status = CallStatus.SUCCESS
        elif final_status == "interrupted":
            status = CallStatus.CANCELLED
        else:
            status = CallStatus.FAILED

        pricing = managed.agent_pricing
        credential_id = pricing.credential_id
        await self.ledger.record_agent_turn(
            project_name=managed.project_name,
            session_id=managed.session_id,
            prompt=managed.last_user_prompt,
            user_id=getattr(self, "_user_id", DEFAULT_USER_ID),
            provider=agent_provider_id(credential_id) if credential_id is not None else PROVIDER_ANTHROPIC,
            pricing=pricing,
            status=status,
            calls=[_agent_model_call(usage) for usage in usages],
        )

    async def _mark_session_terminal(self, managed: ManagedSession, status: SessionStatus, reason: str) -> None:
        """Set terminal status on abnormal consumer exit."""
        # CLI-exit paths already marked queues unsent; other paths may retain CLI messages but have no finalizer.
        managed.drop_queued_messages(state="queued")
        managed.cancel_pending_questions(reason)
        managed.status = status
        managed.last_activity = time.monotonic()
        await self.meta_store.update_status(managed.session_id, status)
        managed.interrupt_requested = False

        # Backfill typed interrupt log entries: inbox already stopped on this path,
        # so SDK echoes cannot reach the write boundary. Tail-check dedup retains only one
        # alongside already logged racing echoes.
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
        if managed is None or session_id in self._disconnecting:
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
        if managed.status == "running":
            # Disconnecting CLI interrupts active turns and kills background subagents; reset timers on CLI idle.
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
        self._disconnecting[session_id] = asyncio.Event()
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
                await managed.actor.cancel_and_wait()
                managed.status = "interrupted"
            except Exception:
                logger.exception("actor 关停异常 session_id=%s", session_id)
                managed.status = "error"

            # Drain the inbox processor
            with contextlib.suppress(Exception):
                managed._inbox.put_nowait(None)
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

            # If closure leaves running status, persist terminal state to prevent restart deadlocks:
            # send_message already wrote running; otherwise get_or_connect revival would retain
            # running without any CLI idle to settle it.
            # CLI gone, queues cannot process; channels outlive eviction, so broadcast removals to remaining subscribers.
            managed.drop_queued_messages()
            if managed.resolved_sdk_id is not None:
                if managed.status == "running":
                    managed.status = managed.turn_outcome or "interrupted"
                if managed.status in ("completed", "interrupted", "error"):
                    with contextlib.suppress(BaseException):
                        await self.meta_store.update_status(managed.resolved_sdk_id, managed.status)
        finally:
            self.sessions.pop(session_id, None)
            self._release_channel(session_id)
            # Retain held connection locks: holders await eviction before rebuilding; removal would let newcomers acquire independent locks and connect concurrently.
            lock = self._connect_locks.get(session_id)
            if lock is not None and not lock.locked():
                del self._connect_locks[session_id]
            self._disconnecting.pop(session_id).set()

    async def _get_cleanup_delay(self) -> int:
        """Session cleanup delay in seconds, default 300 (5 minutes)."""
        try:
            async with async_session_factory() as session:
                svc = ConfigService(session)
                val = await svc.get_setting("agent_session_cleanup_delay_seconds", "300")
            return max(int(val), 10)
        except Exception:
            logger.warning("读取 cleanup delay 配置失败，使用默认值", exc_info=True)
            return 300

    async def _get_max_concurrent(self) -> int:
        """Maximum concurrent sessions, default 5."""
        try:
            async with async_session_factory() as session:
                svc = ConfigService(session)
                val = await svc.get_setting("agent_max_concurrent_sessions", "5")
            return max(int(val), 1)
        except Exception:
            logger.warning("读取 max_concurrent 配置失败，使用默认值", exc_info=True)
            return 5

    async def _ensure_capacity(self) -> None:
        """Ensure available concurrency slots, evicting least-recently-active non-running sessions if necessary."""
        max_concurrent = await self._get_max_concurrent()
        active = [s for s in self.sessions.values() if s.session_id not in self._disconnecting]

        if len(active) < max_concurrent:
            return

        # Evict only non-running sessions; until CLI idle, active turns/background subagents count as ongoing.
        # Prefer refusing new sessions to disconnecting them.
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

        # All sessions running: reject.
        raise SessionCapacityError(f"当前有{len(active)}个正在进行的会话，已达到最大上限，请稍后重试")

    _PATROL_INTERVAL = 300  # 5 minutes.

    async def _patrol_once(self) -> None:
        """One patrol: clean all timed-out non-running sessions."""
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
        """Periodic background patrol loop."""
        while True:
            await asyncio.sleep(self._PATROL_INTERVAL)
            try:
                await self._patrol_once()
            except Exception:
                logger.warning("巡检循环异常", exc_info=True)

    def start_patrol(self) -> None:
        """Start background patrol (call at application startup)."""
        self._patrol_task = asyncio.create_task(self._patrol_loop())

    @staticmethod
    def _resolve_result_status(
        result_message: dict[str, Any],
        interrupt_requested: bool = False,
        preempted: bool = False,
    ) -> SessionStatus:
        """Map SDK result subtype/is_error to runtime session status."""
        return resolve_result_status(result_message, interrupt_requested=interrupt_requested, preempted=preempted)

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
            return PermissionResultDeny(
                message=str(exc) or "session interrupted by user",
                interrupt=True,
            )
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
            normalized_tool = str(tool_name or "").strip().lower()

            if normalized_tool == "askuserquestion":
                managed = managed_ref[0] if managed_ref else self.sessions.get(session_id)
                return await self._handle_ask_user_question(
                    managed,
                    tool_name,
                    input_data,
                )

            # Windows fallback: disabled sandbox removes Bash tools from allowed_tools,
            # routing here through AgentAccessPolicy prefix allowlists.
            if not self.access_policy.sandbox_enabled and tool_name == "Bash":
                cmd = str((input_data or {}).get("command") or "").strip()
                if self.access_policy.is_bash_command_whitelisted(cmd):
                    return PermissionResultAllow(updated_input=input_data)
                return PermissionResultDeny(
                    message=self.access_policy.format_bash_whitelist_deny_message(cmd),
                )
            # BashOutput / KillBash are Bash-management tools, directly allowed in fallback mode.
            if not self.access_policy.sandbox_enabled and tool_name in ("BashOutput", "KillBash"):
                return PermissionResultAllow(updated_input=input_data)

            # Whitelist fallback: deny any tool that was not pre-approved
            # by allowed_tools or settings.json allow rules.
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
            # Session/project ownership lives only in meta_store. Persisted metadata permits listing/entry streams;
            # register channels by sdk_id first so subsequent subscribers join the actual broadcast channel; do not register nonresident sessions.
            registered = managed.session_id in self.sessions
            if registered:
                self._channels[sdk_id] = managed.channel
            try:
                await self.meta_store.create(managed.project_name, sdk_id)
            except BaseException:
                if registered and self._channels.get(sdk_id) is managed.channel:
                    del self._channels[sdk_id]
                raise
            await self.meta_store.update_status(sdk_id, "running")
            # Log initial user messages first (seq 0): inbox runs this serially before
            # any assistant-entry finalization, preserving timeline order. Set sdk_id_event
            # only after persistence, so waking send_new_session can return authoritative entries.
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
                except Exception as exc:
                    # Never silently swallow: record exceptions for send_new_session to explicitly report
                    # failure on waking (cleanup + error persistence + raising to caller).
                    managed.initial_user_entry_error = exc
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
        """Extract SDK session id from either serialized payload or raw object.

        CLI system messages (init / status / api_retry) place ``session_id`` inside ``data``, not top-level.
        If first API requests fail (invalid credentials causing CLI backoff), streams contain only these
        messages. Extract IDs from init or new sessions can only time out."""
        sdk_id = msg_dict.get("session_id") or msg_dict.get("sessionId")
        if sdk_id:
            return str(sdk_id)
        data = msg_dict.get("data")
        if isinstance(data, dict):
            nested = data.get("session_id") or data.get("sessionId")
            if nested:
                return str(nested)
        raw_sdk_id = getattr(message, "session_id", None) or getattr(message, "sessionId", None)
        if raw_sdk_id:
            return str(raw_sdk_id)
        return None

    def get_draft_state(self, session_id: str) -> dict[str, Any]:
        """Reconnect-first streaming-draft snapshot: accumulated state + rev filter threshold.

        Return ``rev`` even without active drafts: clients discard queued old deltas (rev <= threshold)
        between subscription/snapshot by rev, without content comparison."""
        managed = self.sessions.get(session_id)
        if managed is None or managed.entry_pipeline is None:
            return {"draft": None, "rev": 0}
        draft = managed.entry_pipeline.draft
        return {"draft": draft.snapshot(), "rev": draft.rev}

    def find_queued_message_by_client_key(self, session_id: str, client_key: str) -> dict[str, Any] | None:
        """Return ``{"queued_message": ...}`` if the idempotency-key message remains queued, otherwise None."""
        managed = self.sessions.get(session_id)
        if managed is None:
            return None
        entry_uuid = managed.sent_client_keys.get(client_key)
        queued = managed.find_queued_message(message_id=entry_uuid) if entry_uuid is not None else None
        return {"queued_message": queued.to_payload()} if queued is not None else None

    def get_queued_messages_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        """Queued-message snapshot in send order for entry-stream initialization; empty for nonresident sessions."""
        managed = self.sessions.get(session_id)
        if managed is None:
            return []
        return [queued.to_payload() for queued in managed.queued_messages]

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
        # SDK control requests may deliver questions before actors read CLI running reports.
        if managed.status != "running" and question_id not in managed.pending_questions:
            raise ValueError("会话未运行或无待回答问题")
        if not managed.resolve_pending_question(question_id, answers):
            raise ValueError("未找到待回答的问题")

    def _session_channel(self, session_id: str) -> SseChannel:
        """Get the session broadcast channel, registering one if absent."""
        channel = self._channels.get(session_id)
        if channel is None:
            channel = _make_session_channel()
            self._channels[session_id] = channel
        return channel

    def _release_channel(self, session_id: str) -> None:
        """Call after resident-session removal/subscriber departure; release the channel only when both are absent."""
        channel = self._channels.get(session_id)
        if channel is None or channel.has_subscribers or session_id in self.sessions:
            return
        del self._channels[session_id]

    def _subscribe(self, session_id: str) -> tuple[SseChannel, asyncio.Queue]:
        """Register a live-message queue for a session.

        Do not revive cold sessions: open panels must not consume CLI concurrency. Subscribe to the session
        channel, reused when sending later revives it, so subscribers receive broadcasts normally.

        Private: the only consumer is :meth:`stream_messages`, which owns the
        deterministic unsubscribe via its context-manager ``__aexit__``."""
        managed = self.sessions.get(session_id)
        channel = managed.channel if managed is not None else self._session_channel(session_id)
        return channel, channel.subscribe()

    async def _unsubscribe(self, channel: SseChannel, session_id: str, queue: asyncio.Queue) -> None:
        """Remove a queue from a session's subscriber channel."""
        await channel.unsubscribe(queue)
        self._release_channel(session_id)

    @contextlib.asynccontextmanager
    async def stream_messages(
        self, session_id: str, *, idle_timeout: float = 20.0
    ) -> AsyncGenerator[AsyncIterator[SessionStreamEvent]]:
        """Subscribe to a session's messages as a self-cleaning async iterator.

        Yields an async iterator producing semantic events, in order:

        - a :class:`SubscriptionReady` barrier (always the first event) marking
          that the subscription is established — broadcasts after it are gap-free,
        - a :class:`LiveMessage` per broadcast message,
        - a :class:`Heartbeat` whenever *idle_timeout* elapses with no message
          (consumers run liveness / disconnect self-checks on it).

        The subscription outlives the session's residency: it survives eviction
        and keeps receiving once the session is revived. The stream ends only
        when the subscriber queue is dropped under backpressure — stream end is
        the reconnect signal; no overflow event reaches consumers (the overflow
        sentinel is internal to :class:`SseChannel`).

        Subscription, queue draining and unsubscribe all live behind this seam;
        cleanup is carried deterministically by ``__aexit__`` (see ADR-0005).
        Consume as ``async with stream_messages(...) as stream: async for event
        in stream``.
        """
        channel, queue = self._subscribe(session_id)

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
            await self._unsubscribe(channel, session_id, queue)

    async def get_status(self, session_id: str) -> SessionStatus | None:
        """Get session status."""
        if session_id in self.sessions:
            return self.sessions[session_id].status
        meta = await self.meta_store.get(session_id)
        return meta.status if meta else None

    async def shutdown_gracefully(self) -> None:
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
