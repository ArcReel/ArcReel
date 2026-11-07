"""
Assistant service orchestration using ClaudeSDKClient.
"""

import asyncio
import copy
import logging
import os
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from claude_agent_sdk import (
    delete_session as sdk_delete_session,
)
from claude_agent_sdk import (
    delete_session_via_store,
    list_sessions_from_store,
)
from claude_agent_sdk import (
    list_sessions as sdk_list_sessions,
)

if TYPE_CHECKING:
    from server.routers.assistant import ImageAttachment

logger = logging.getLogger(__name__)

from fastapi import Request
from fastapi.sse import ServerSentEvent

from lib.agent.agent_profile import agent_profile_dir
from lib.agent.profile_frontmatter import FrontmatterError, parse_profile_metadata
from lib.agent.profile_manifest import VALID_CONTENT_MODES
from lib.i18n import DEFAULT_LOCALE
from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from server.agent_runtime.event_log import (
    EventLogService,
    EventLogStore,
    build_failure_entry,
    build_user_entry,
)
from server.agent_runtime.keyed_locks import KeyedLocks
from server.agent_runtime.models import (
    Heartbeat,
    LiveMessage,
    SessionMeta,
    SessionStatus,
    SubscriptionReady,
    WithdrawalIntent,
)
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.session_branch import (
    BranchAnchorError,
    BranchedSession,
    SessionBranchError,
    SessionBranchService,
)
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


class MessageRewriteError(RuntimeError):
    """Message rewriting cannot proceed."""


class RewriteAnchorError(MessageRewriteError):
    """The anchor is not a user message within this session."""


class PendingQuestionError(MessageRewriteError):
    """The session has a pending question card; answering takes priority."""


class QueuedMessagesPendingError(MessageRewriteError):
    """The session has queued messages not yet in the conversation; branching now would leave them executing in the superseded original session."""


class SessionSupersededError(MessageRewriteError):
    """A rewrite has superseded this session; rewriting is available only on the current branch."""


class RewriteUnavailableError(MessageRewriteError):
    """Rewriting is unavailable in this deployment (transcript mirroring is disabled)."""


class InterruptSettleTimeoutError(MessageRewriteError):
    """Timed out waiting for the interrupted running turn to reach a terminal state."""


class AssistantService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.layout = DataRootLayout.current()
        self.data_root = self.layout.root

        self.pm = ProjectManager(self.data_root)
        self.meta_store = SessionMetaStore()
        # Session event log: sole read source for the UI timeline. Store and SessionManager share one instance,
        # so live writes (entry pipeline) and readers (REST / SSE / lazy generation) use the same table.
        self.event_log_store = EventLogStore()
        self.session_manager = SessionManager(
            project_root=self.project_root,
            meta_store=self.meta_store,
            data_root=self.data_root,
            event_log_store=self.event_log_store,
        )
        # Shared with SessionManager (lazy-cached there) so reads via the
        # adapter and writes via SDK options use the same per-user namespace.
        # None when ARCREEL_SDK_SESSION_STORE=off.
        self._session_store = self.session_manager._build_session_store()
        self.transcript_adapter = SdkTranscriptAdapter(store=self._session_store)
        self.event_log = EventLogService(self.event_log_store, self.transcript_adapter)
        self.session_branch = SessionBranchService(
            store=self._session_store,
            meta_store=self.meta_store,
            event_log=self.event_log,
            resolve_project_cwd=self._resolve_project_cwd_safe,
        )
        self._startup_lock = asyncio.Lock()
        self._startup_done = False
        # New-session idempotency mapping: the client_key unique index partitions by (session_id, client_key),
        # so it cannot cover new-session acceptance before session_id exists. Retrying after a lost response
        # through the new-session branch would duplicate sessions and execution of the same prompt. In-process LRU is the fast path;
        # cross-session event-log queries provide fallback after restart / eviction (_find_accepted_new_session).
        self._new_session_client_keys: OrderedDict[str, str] = OrderedDict()
        self._new_session_client_keys_max = 256
        # Serialize concurrent creation requests with the same client_key to prevent duplicate sessions during the in-flight window.
        self._new_session_locks = KeyedLocks()
        # Serialize concurrent rewrites for the same (original session, client_key): branch idempotency prechecks read
        # the original session's superseded pointer; without serialization, two in-flight requests could each create a branch.
        self._rewrite_locks = KeyedLocks()
        # Existing-session message acceptance lock: sending and rewriting exclude each other. Rewriting holds it from queue check to branch publication;
        # sends arriving meanwhile wait, then see the original session superseded instead of queuing into a soon-superseded session.
        self._admission_locks = KeyedLocks()
        self.stream_heartbeat_seconds = int(os.environ.get("ASSISTANT_STREAM_HEARTBEAT_SECONDS", "20"))

    async def startup(self, *, in_docker: bool = False, sandbox_enabled: bool = True) -> None:
        """Run async initialization (must be called from event loop).

        With ``sandbox_enabled=False``, disable SDK SandboxSettings and route Bash calls through the
        code allowlist (see ``SessionManager.configure_sandbox_runtime``). The default ``True``
        preserves existing macOS / Linux behavior."""
        if self._startup_done:
            return
        async with self._startup_lock:
            if self._startup_done:
                return
            self.session_manager.configure_sandbox_runtime(
                in_docker=bool(in_docker),
                sandbox_enabled=bool(sandbox_enabled),
            )
            await self._interrupt_stale_running_sessions()
            self._startup_done = True

    # ==================== Session CRUD ====================

    async def _interrupt_stale_running_sessions(self) -> None:
        """On service restart, stale running sessions cannot safely resume."""
        interrupted_count = await self.meta_store.interrupt_running_sessions()
        if interrupted_count > 0:
            logger.warning(
                "服务启动时中断遗留运行中会话 count=%s",
                interrupted_count,
            )

    async def list_sessions(
        self,
        project_name: str | None = None,
        status: SessionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        """List sessions, injecting SDK summary as title when available."""
        sessions = await self.meta_store.list(project_name=project_name, status=status, limit=limit, offset=offset)
        if not sessions or not project_name:
            return sessions

        project_cwd = str(self.layout.projects_dir / project_name)
        sdk_sessions: list[Any] = []

        if self._session_store is not None and list_sessions_from_store is not None:
            try:
                sdk_sessions = await list_sessions_from_store(self._session_store, directory=project_cwd)
            except Exception:
                logger.warning(
                    "SDK list_sessions_from_store failed, titles will be empty",
                    exc_info=True,
                )
                return sessions
        elif sdk_list_sessions is not None:
            try:
                sdk_sessions = await asyncio.to_thread(
                    sdk_list_sessions, directory=project_cwd, include_worktrees=False
                )
            except Exception:
                logger.warning("SDK list_sessions failed, titles will be empty", exc_info=True)
                return sessions
        else:
            return sessions

        summary_map = {s.session_id: s.summary for s in sdk_sessions}
        return [SessionMeta(**{**s.model_dump(), "title": summary_map.get(s.id, s.title)}) for s in sessions]

    async def get_session(self, session_id: str) -> SessionMeta | None:
        """Get session by ID."""
        meta = await self.meta_store.get(session_id)
        if meta and session_id in self.session_manager.sessions:
            # Update status from live session
            managed = self.session_manager.sessions[session_id]
            meta = SessionMeta(**{**meta.model_dump(), "status": managed.status})
        return meta

    async def delete_session(self, session_id: str) -> bool:
        """Delete session and cleanup."""
        if session_id in self.session_manager.sessions:
            await self.session_manager.close_session(
                session_id,
                reason="session deleted",
            )

        if self._session_store is not None and delete_session_via_store is not None:
            # SDK derives project_key from `directory`; without it the key is
            # computed from server cwd and never matches inserted rows, so the
            # delete becomes a silent no-op. Resolve project cwd from meta.
            meta = await self.meta_store.get(session_id)
            project_cwd = str(self.layout.projects_dir / meta.project_name) if meta else None
            try:
                await delete_session_via_store(self._session_store, session_id, directory=project_cwd)
            except Exception:
                logger.warning(
                    "delete_session_via_store failed for %s",
                    session_id,
                    exc_info=True,
                )
        elif sdk_delete_session is not None:
            try:
                await run_sync_transaction(sdk_delete_session, session_id)
            except Exception:
                logger.warning("sdk delete_session failed for %s", session_id, exc_info=True)

        try:
            await self.event_log_store.delete_session(session_id)
        except Exception:
            logger.warning("删除会话事件日志失败 session_id=%s", session_id, exc_info=True)

        return await self.meta_store.delete(session_id)

    # ==================== Messages ====================

    def _prepare_content(
        self,
        content: str,
        images: list["ImageAttachment"] | None = None,
    ) -> str | list[dict[str, Any]]:
        """Message content sent to the CLI: a string for plain text, or a list with image blocks before the text block when images are present."""
        text = content.strip()
        if not text and not images:
            raise ValueError("消息内容不能为空")
        if not images:
            return text
        blocks: list[dict[str, Any]] = [self._image_block(img) for img in images]
        if text:
            blocks.append({"type": "text", "text": text})
        return blocks

    @staticmethod
    def _build_user_log_entry(content: str | list[dict[str, Any]]) -> dict[str, Any]:
        """Build the authoritative user-message entry: allocate its identity on send, and log it when the Agent accepts it."""
        return build_user_entry([{"type": "text", "text": content}] if isinstance(content, str) else content)

    async def send_or_create(
        self,
        project_name: str,
        content: str,
        *,
        session_id: str | None = None,
        images: list["ImageAttachment"] | None = None,
        locale: str = DEFAULT_LOCALE,
        client_key: str | None = None,
    ) -> dict[str, Any]:
        """Unified send: create new session or send to existing one.

        Existing sessions accept messages even while a turn is running, returning ``queued_message``;
        only Agent acceptance delivers them as entries through the entry stream. If a retry with the same
        ``client_key`` finds a logged message, return its authoritative ``entry``. Sessions superseded by
        rewriting no longer accept messages (``SessionSupersededError``). For new sessions, return only
        after logging the first message, with its authoritative entry. The frontend renders no locally synthesized messages."""
        await asyncio.to_thread(self.pm.get_project_path, project_name)  # Validate project

        if session_id:
            # Existing session
            async with self._admission_locks.lock_for(session_id):
                meta = await self.meta_store.get(session_id)
                if meta is None:
                    raise FileNotFoundError(f"session not found: {session_id}")
                if meta.project_name != project_name:
                    raise FileNotFoundError(f"session not found: {session_id}")
                if meta.superseded_by is not None:
                    raise SessionSupersededError(
                        f"session {session_id} has already been superseded by {meta.superseded_by}"
                    )
                prompt = self._prepare_content(content, images)
                # Lazily generate the old session first, ensuring this message follows history reconstructed by replay.
                await self.event_log.ensure_backfilled(
                    session_id, await self._aresolve_project_cwd_safe(meta.project_name)
                )
                accepted = await self.session_manager.send_message(
                    session_id,
                    prompt,
                    meta=meta,
                    locale=locale,
                    user_entry=self._build_user_log_entry(prompt),
                    client_key=client_key,
                )
            return self._accepted_response(session_id, accepted)
        # New session
        if not client_key:
            return await self._create_new_session(project_name, content, images, locale, client_key)

        existing = await self._find_accepted_new_session(client_key, project_name)
        if existing is not None:
            return existing

        # Serialize concurrent requests with the same client_key: while send_new_session is in flight,
        # later requests wait for the lock instead of creating sessions independently and duplicating prompt execution.
        lock = self._new_session_locks.lock_for(client_key)
        async with lock:
            # Double-check: the preceding request may have created the session for this client_key while we awaited the lock.
            existing = await self._find_accepted_new_session(client_key, project_name)
            if existing is not None:
                return existing
            result = await self._create_new_session(project_name, content, images, locale, client_key)
            self._record_new_session_client_key(client_key, result["session_id"])
            return result

    async def _find_accepted_new_session(self, client_key: str, project_name: str) -> dict[str, Any] | None:
        """Find an accepted new session by idempotency key: use the in-process mapping as a fast path and
        cross-session event-log queries as fallback. After process restart / LRU eviction loses the mapping,
        retries of persisted acceptances must still find the existing session instead of creating another
        (repeating the same prompt and billing).

        The matched session must belong to the caller's ``project_name``. A mismatch is a miss (return None
        so the caller creates a session in the current project), not an error: the user named no session, so
        reusing client_key across projects should create the new session in the current project. Both lookup
        paths check project ownership before returning."""
        mapped_session_id = self._new_session_client_keys.get(client_key)
        if mapped_session_id is not None:
            # Idempotent retry: initial acceptance already created the session and delivered; return that session's authoritative entry.
            entry = await self.event_log_store.find_by_client_key(mapped_session_id, client_key)
            if entry is None:
                # The mapped session entry no longer exists (e.g. session deletion); the mapping is stale.
                # Remove it and continue probing to avoid a ghost "accepted" response pointing at a deleted session:
                # the caller would connect to a nonexistent session and silently lose the message. During the preceding
                # await, another concurrent request may have updated this key's mapping. Remove it only if its current
                # value still equals the old value read here, preserving newer concurrent mappings (the DB fallback
                # query by client_key necessarily finds the same authoritative session, so mistaken deletion would only
                # waste a query; still use precise conditions to avoid this unnecessary churn).
                if self._new_session_client_keys.get(client_key) == mapped_session_id:
                    self._new_session_client_keys.pop(client_key, None)
            elif await self._new_session_matches_project(mapped_session_id, project_name):
                # Refresh LRU position on hits; otherwise frequently retried keys are evicted by insertion order
                # rather than access order, degrading to FIFO. During the preceding await,
                # concurrent eviction may have removed the key; directly calling
                # move_to_end on a missing key raises KeyError. Reuse
                # _record_new_session_client_key assignment semantics (insert if missing,
                # update in place if present), then explicitly move to most-recently-used; both cases are safe.
                self._record_new_session_client_key(client_key, mapped_session_id)
                return {"status": "accepted", "session_id": mapped_session_id, "entry": entry, "queued_message": None}
            # else: the mapped session belongs to another project; treat as a miss and continue to DB fallback / creation.
            # Keep the mapping because it remains valid for its original project; later creation in the current project uses
            # _record_new_session_client_key to replace the same client_key with this project's session.
        recovered = await self.event_log_store.find_new_session_by_client_key(client_key)
        if recovered is None:
            return None
        session_id, entry = recovered
        if not await self._new_session_matches_project(session_id, project_name):
            # The fallback session belongs to another project; treat as a miss and create in the current project.
            return None
        # Another concurrent request may have recorded a new mapping during the preceding await. Write only if
        # no mapping exists or it already has the same session_id, preventing this older lookup result
        # from overwriting a concurrently written mapping.
        if self._new_session_client_keys.get(client_key) in (None, session_id):
            self._record_new_session_client_key(client_key, session_id)
        return {"status": "accepted", "session_id": session_id, "entry": entry, "queued_message": None}

    async def _new_session_matches_project(self, session_id: str, project_name: str) -> bool:
        """Whether an idempotently matched new session belongs to the calling project. Check session metadata
        ``project_name``. Missing metadata (error / deletion) does not block the match, preserving existing
        idempotency semantics: cross-project misrouting requires existing metadata with a different project."""
        meta = await self.meta_store.get(session_id)
        return meta is None or meta.project_name == project_name

    def _record_new_session_client_key(self, client_key: str, session_id: str) -> None:
        self._new_session_client_keys[client_key] = session_id
        self._new_session_client_keys.move_to_end(client_key)
        while len(self._new_session_client_keys) > self._new_session_client_keys_max:
            self._new_session_client_keys.popitem(last=False)

    async def _create_new_session(
        self,
        project_name: str,
        content: str,
        images: list["ImageAttachment"] | None,
        locale: str,
        client_key: str | None,
    ) -> dict[str, Any]:
        """Actually create a session and deliver the first message, without accounting for the client_key idempotency mapping."""
        prompt = self._prepare_content(content, images)
        new_sdk_session_id = await self.session_manager.send_new_session(
            project_name,
            prompt,
            locale=locale,
            user_entry=self._build_user_log_entry(prompt),
            client_key=client_key,
        )
        managed = self.session_manager.sessions.get(new_sdk_session_id)
        entry = managed.initial_user_log_entry if managed is not None else None
        return {"status": "accepted", "session_id": new_sdk_session_id, "entry": entry, "queued_message": None}

    @staticmethod
    def _accepted_response(session_id: str, accepted: dict[str, Any], **extra: Any) -> dict[str, Any]:
        """Existing-session acceptance response: exactly one queued message or authoritative entry (the latter for idempotent retries of already logged messages)."""
        return {
            "status": "accepted",
            "session_id": session_id,
            **extra,
            "entry": accepted.get("entry"),
            "queued_message": accepted.get("queued_message"),
        }

    # ==================== Message rewriting (branch-session orchestration) ====================

    # Timeout and polling interval for interrupted running turns to reach terminal state: the inbox task derives it
    # after receiving the SDK result message; no event is available to await, so observe status.
    _INTERRUPT_SETTLE_TIMEOUT = 30.0
    _INTERRUPT_SETTLE_POLL = 0.05

    async def rewrite_message(
        self,
        project_name: str,
        session_id: str,
        *,
        anchor_entry_uuid: str,
        content: str,
        images: list["ImageAttachment"] | None = None,
        locale: str = DEFAULT_LOCALE,
        client_key: str | None = None,
    ) -> dict[str, Any]:
        """Rewrite the anchored user message in ``session_id`` and return the new session receiving the rewrite.

        Order orchestration by rejection cost: reject early when possible (anchor, pending questions), then
        perform expensive steps (interrupting the running turn, branching, dispatch). Rejected requests should
        not already have interrupted the user's turn. Failure at any step after branching fails the whole rewrite:
        withdraw the entire branch and restore the original session to a state permitting another rewrite.

        The response matches the send endpoint: ``client_key`` is the request idempotency key, so retries
        create no second branch session. The rewritten message initially appears in the new session as
        ``queued_message`` and enters its log once accepted."""
        await asyncio.to_thread(self.pm.get_project_path, project_name)  # Validate project
        if not client_key:
            return await self._rewrite_message_once(
                project_name,
                session_id,
                anchor_entry_uuid=anchor_entry_uuid,
                content=content,
                images=images,
                locale=locale,
                client_key=None,
            )
        async with self._rewrite_locks.lock_for(f"{session_id}:{client_key}"):
            return await self._rewrite_message_once(
                project_name,
                session_id,
                anchor_entry_uuid=anchor_entry_uuid,
                content=content,
                images=images,
                locale=locale,
                client_key=client_key,
            )

    async def _rewrite_message_once(
        self,
        project_name: str,
        session_id: str,
        *,
        anchor_entry_uuid: str,
        content: str,
        images: list["ImageAttachment"] | None,
        locale: str,
        client_key: str | None,
    ) -> dict[str, Any]:
        meta = await self.meta_store.get(session_id)
        if meta is None or meta.project_name != project_name:
            raise FileNotFoundError(f"session not found: {session_id}")

        if self._session_store is None:
            raise RewriteUnavailableError(
                "message rewrite requires the DB transcript store (ARCREEL_SDK_SESSION_STORE=db)"
            )

        # Original session superseded: retries with the same client_key claim their authoritative entry in the new branch;
        # other cases try rewriting an invalidated branch, so explicitly reject rather than branch again.
        if meta.superseded_by is not None:
            replay = await self._replay_rewrite(meta.superseded_by, client_key)
            if replay is not None:
                return replay
            raise SessionSupersededError(f"session {session_id} has already been superseded by {meta.superseded_by}")

        # Validate content before any side effects: empty messages must not interrupt running turns.
        prompt = self._prepare_content(content, images)

        project_cwd = await self._aresolve_project_cwd_safe(meta.project_name)
        anchor = await self.event_log.resolve_user_message_anchor(session_id, anchor_entry_uuid, project_cwd)
        if anchor is None:
            raise RewriteAnchorError(f"anchor {anchor_entry_uuid} is not a user message of session {session_id}")

        # Questions take priority: do not rewrite while a question card is pending. Cards live only in memory
        # (answers return to the running turn via future) and are canceled on eviction or process restart. Cold sessions
        # have no answerable questions, so memory is authoritative.
        if await self.session_manager.get_pending_questions_snapshot(session_id):
            raise PendingQuestionError(f"session {session_id} has pending questions")
        # Accept no new messages between queue check and branch publication: interruption stops only the current turn; queued messages would continue and would not appear in the branch.
        async with self._admission_locks.lock_for(session_id):
            if self.session_manager.get_queued_messages_snapshot(session_id):
                raise QueuedMessagesPendingError(f"session {session_id} has queued messages")

            await self._settle_running_session(session_id)

            branched = await self._branch_or_reject(session_id, anchor_entry_uuid)
        new_session_id = branched.session_id
        # Once the branch is published (superseded points at the new session), every subsequent step is compensatable:
        # without rollback on failure, the original is hidden, the new session lacks the rewritten message, and retries
        # hit "superseded". A failing send_message leaves no queued message and writes no entry
        # (entries are written only on Agent acceptance), so withdrawing everything loses no data.
        try:
            new_meta = await self.meta_store.get(new_session_id)
            if branched.resumable:
                # Lazy generation first: the rewritten message must follow copied prefix history.
                await self.event_log.ensure_backfilled(new_session_id, project_cwd)
            accepted = await self.session_manager.send_message(
                new_session_id,
                prompt,
                meta=new_meta,
                locale=locale,
                user_entry=self._build_user_log_entry(prompt),
                client_key=client_key,
                resumable=branched.resumable,
            )
        except BaseException:
            await self._discard_branch(session_id, new_session_id)
            raise

        return self._accepted_response(new_session_id, accepted, origin_session_id=session_id)

    async def _replay_rewrite(self, new_session_id: str, client_key: str | None) -> dict[str, Any] | None:
        """Idempotent replay: whether ``new_session_id`` already received the rewrite for the given client_key."""
        if not client_key:
            return None
        accepted = self.session_manager.find_queued_message_by_client_key(new_session_id, client_key)
        if accepted is None:
            entry = await self.event_log_store.find_by_client_key(new_session_id, client_key)
            if entry is None:
                return None
            accepted = {"entry": entry}
        meta = await self.meta_store.get(new_session_id)
        return self._accepted_response(
            new_session_id,
            accepted,
            origin_session_id=meta.fork_parent_session_id if meta is not None else None,
        )

    async def _settle_running_session(self, session_id: str) -> None:
        """Interrupt the running turn and wait for its terminal state; a running session cannot yield a clean prefix for branching.

        This is one user action: rewriting includes interruption, without requiring Stop first. The inbox task
        derives terminal state after receiving the SDK result message, so only status can be observed. Trailing
        messages of the interrupted turn follow the anchor and are invalidated with the original branch anyway;
        there is no need to wait for their persistence."""
        status = await self.session_manager.interrupt_session(session_id)
        if status != "running":
            return
        deadline = asyncio.get_running_loop().time() + self._INTERRUPT_SETTLE_TIMEOUT
        while status == "running":
            if asyncio.get_running_loop().time() >= deadline:
                raise InterruptSettleTimeoutError(f"session {session_id} did not settle after interrupt")
            await asyncio.sleep(self._INTERRUPT_SETTLE_POLL)
            status = await self.session_manager.get_status(session_id) or "idle"

    async def _branch_or_reject(self, session_id: str, anchor_entry_uuid: str) -> BranchedSession:
        """Branch and translate branch-service exceptions into rejection reasons distinguishable by the orchestration layer."""
        try:
            return await self.session_branch.branch(session_id, anchor_entry_uuid)
        except BranchAnchorError as exc:
            # Orchestration precheck allowed it, but slicing rejected it: the parsed uuid is absent from the transcript
            # (the anchor just emitted by a running turn has not yet been replayed by the SDK), or the entry contains
            # tool_result. Both are caller-correctable bad requests, distinct from branch failure.
            raise RewriteAnchorError(
                f"anchor {anchor_entry_uuid} is not a forkable user message of session {session_id}"
            ) from exc
        except SessionBranchError as exc:
            meta = await self.meta_store.get(session_id)
            if meta is not None and meta.superseded_by is not None:
                # Another rewrite won between precheck and branching (the conditional pointer update permits only one winner).
                raise SessionSupersededError(
                    f"session {session_id} has already been superseded by {meta.superseded_by}"
                ) from exc
            raise

    async def _discard_branch(self, origin_session_id: str, new_session_id: str) -> None:
        """Withdraw a branch that failed to receive the rewrite: disconnect its runtime first, then clear its data."""
        try:
            if new_session_id in self.session_manager.sessions:
                await self.session_manager.close_session(new_session_id, reason="message rewrite dispatch failed")
        except Exception:
            logger.exception("关闭未完成分支会话失败 session_id=%s", new_session_id)
        try:
            # Branching itself writes no event log, but dispatch may have already done so; clear the log too,
            # consistently with delete_session, to avoid orphaned entries after withdrawal.
            await self.event_log_store.delete_session(new_session_id)
        except Exception:
            logger.exception("删除未完成分支会话事件日志失败 session_id=%s", new_session_id)
        try:
            await self.session_branch.discard(origin_session_id, new_session_id)
        except Exception:
            logger.exception("撤回未完成分支失败 origin=%s new=%s", origin_session_id, new_session_id)

    @staticmethod
    def _image_block(img: "ImageAttachment") -> dict[str, Any]:
        """Build a single image content block dict."""
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img.media_type,
                "data": img.data,
            },
        }

    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
        *,
        meta: SessionMeta | None = None,
    ) -> dict[str, Any]:
        """Submit answers for a pending AskUserQuestion."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        await self.session_manager.answer_user_question(session_id, question_id, answers)
        return {"status": "accepted", "session_id": session_id, "question_id": question_id}

    async def withdraw_queued_message(
        self, project_name: str, session_id: str, message_id: str, *, intent: WithdrawalIntent
    ) -> dict[str, Any]:
        """Edit or delete a queued message: withdraw it from the CLI first, removing it from the queue only
        on success; editing returns the message content.

        ``outcome`` of ``withdrawn`` means the message left the queue (when editing, ``message`` is the
        content to return to the input box). ``accepted`` means the Agent already received it and it enters
        the conversation normally."""
        meta = await self.meta_store.get(session_id)
        if meta is None or meta.project_name != project_name:
            raise FileNotFoundError(f"session not found: {session_id}")
        # Share the send acceptance lock: unsent messages temporarily leave the session during send reconnection; withdrawal waits for connection restoration before looking them up.
        async with self._admission_locks.lock_for(session_id):
            outcome, queued = await self.session_manager.withdraw_queued_message(session_id, message_id, intent)
        return {
            "session_id": session_id,
            "id": message_id,
            "outcome": outcome,
            "message": queued.to_payload() if queued is not None and intent == "edit" else None,
        }

    async def resend_queued_message(
        self, project_name: str, session_id: str, message_id: str, *, locale: str = DEFAULT_LOCALE
    ) -> dict[str, Any]:
        """Resubmit an unsent message to the CLI, reviving the session first if needed; return the queued message in the response."""
        # Share the acceptance lock with sending and rewriting, preventing revival and CLI delivery from interleaving with them.
        async with self._admission_locks.lock_for(session_id):
            meta = await self.meta_store.get(session_id)
            if meta is None or meta.project_name != project_name:
                raise FileNotFoundError(f"session not found: {session_id}")
            queued = await self.session_manager.resend_queued_message(session_id, message_id, meta=meta, locale=locale)
        return {"session_id": session_id, "id": message_id, "queued_message": queued.to_payload()}

    async def send_queued_message_now(self, project_name: str, session_id: str, message_id: str) -> dict[str, Any]:
        """Send a queued message immediately: the Agent interrupts its current turn to handle it first.

        ``outcome`` of ``sent`` means resubmitted with ``now`` priority, still the same message in the tray.
        ``accepted`` means the Agent already received it; do not resend, and let it enter the conversation normally."""
        meta = await self.meta_store.get(session_id)
        if meta is None or meta.project_name != project_name:
            raise FileNotFoundError(f"session not found: {session_id}")
        # As with withdrawal, do not interleave with send reconnection; unsent messages also rebuild their own connection, serialized as for resending.
        async with self._admission_locks.lock_for(session_id):
            outcome = await self.session_manager.send_queued_message_now(session_id, message_id)
        return {"session_id": session_id, "id": message_id, "outcome": outcome}

    async def interrupt_session(self, session_id: str, *, meta: SessionMeta | None = None) -> dict[str, Any]:
        """Interrupt a running session."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        session_status = await self.session_manager.interrupt_session(session_id)
        return {
            "status": "accepted",
            "session_id": session_id,
            "session_status": session_status,
        }

    # ==================== Session event log (sole read source for UI timeline) ====================

    async def list_session_entries(
        self,
        session_id: str,
        *,
        meta: SessionMeta | None = None,
        after_seq: int = -1,
    ) -> dict[str, Any]:
        """Cold-read the event log (history replay / initial loading of non-running sessions)."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        status = await self.session_manager.get_status(session_id) or meta.status
        project_cwd = await self._aresolve_project_cwd_safe(meta.project_name)
        entries = await self.event_log.list_entries(session_id, project_cwd, after_seq=after_seq)
        draft_state = (
            self.session_manager.get_draft_state(session_id) if status == "running" else {"draft": None, "rev": 0}
        )
        return {
            "session_id": session_id,
            "status": status,
            "entries": entries,
            "draft": draft_state["draft"],
            "draft_rev": draft_state["rev"],
        }

    async def stream_entry_events(
        self,
        session_id: str,
        *,
        meta: SessionMeta | None = None,
        request: Request | None = None,
        after_seq: int = -1,
    ) -> AsyncIterator[ServerSentEvent]:
        """SSE entry stream: event ``id`` is seq; reconnect using the cursor without recomputing whole frames.

        Lifetime follows the session panel. Initially send ``entry``×N (stored entries after cursor),
        ``draft`` (accumulated streaming state + rev filter threshold), ``queue`` (queued-message snapshot),
        ``question``×N (pending questions), and current ``status``, in that order. Then stream entry / delta /
        queue changes (``queue_upsert`` / ``queue_remove``, plus another ``queue`` when immediate sending
        reorders the tray) / question / status. ``status`` updates state without closing the stream; it ends only
        when the client leaves (or subscriber overflow removes it, signaling reconnection). Establish and wait
        on the stream even for nonresident sessions; sessions revived by sending then push normally."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")

        project_cwd = await self._aresolve_project_cwd_safe(meta.project_name)
        last_seq = after_seq
        async with self.session_manager.stream_messages(
            session_id, idle_timeout=self.stream_heartbeat_seconds
        ) as stream:
            ready = await anext(stream, None)
            if not isinstance(ready, SubscriptionReady):
                return
            # Subscription is established first (no gap); filter duplicate entries delivered between subscription and DB read by seq
            # threshold: compare identity, not content.
            for entry in await self.event_log.list_entries(session_id, project_cwd, after_seq=last_seq):
                last_seq = max(last_seq, self._entry_seq(entry))
                yield self._entry_sse_event(entry)

            yield self._draft_sse_event(session_id)

            # Apply duplicate deltas between snapshot and subscription idempotently by queued-message ID; subscribing first guarantees eventual consistency.
            yield self._sse_event(
                "queue",
                {"session_id": session_id, "messages": self.session_manager.get_queued_messages_snapshot(session_id)},
            )

            for question in await self.session_manager.get_pending_questions_snapshot(session_id):
                yield self._sse_event("question", {**question, "session_id": session_id})

            status: SessionStatus = await self.session_manager.get_status(session_id) or meta.status
            yield self._sse_event("status", self._build_status_event_payload(status=status, session_id=session_id))

            # result marks only a turn ending, without status emission: a session leaves running when the CLI reports idle; the inbox
            # emits runtime_status after broadcasting all entries for this turn.
            async for stream_event in stream:
                if request is not None and await request.is_disconnected():
                    break

                if isinstance(stream_event, Heartbeat):
                    # Heartbeats reconcile status changes without broadcasts, such as eviction.
                    live_status = await self.session_manager.get_status(session_id) or status
                    if live_status != status:
                        status = live_status
                        for event in self._status_change_events(status, session_id):
                            yield event
                    continue

                if not isinstance(stream_event, LiveMessage):
                    continue

                message = stream_event.message
                msg_type = message.get("type", "")

                if (
                    msg_type in ("log_entry", "log_delta", "ask_user_question", "queued_message")
                    and status != "running"
                ):
                    # Sending and autonomous turns both switch to running before producing these messages (send queue registration and switching are synchronous):
                    # emit running first, so new-turn content and queued messages do not appear beneath an old terminal state.
                    live_status = await self.session_manager.get_status(session_id) or status
                    if live_status == "running":
                        status = live_status
                        for event in self._status_change_events(status, session_id):
                            yield event

                if msg_type == "log_entry":
                    entry = message.get("entry")
                    if isinstance(entry, dict):
                        seq = self._entry_seq(entry)
                        if seq > last_seq:
                            last_seq = seq
                            yield self._entry_sse_event(entry)
                    continue

                if msg_type == "log_delta":
                    yield self._sse_event("delta", {k: v for k, v in message.items() if k != "type"})
                    continue

                if msg_type == "queued_message":
                    if message.get("op") == "remove":
                        # User withdrawal includes intent (``withdrawn``) and, for editing, content returned to the input box (``message``).
                        removed = {k: v for k, v in message.items() if k in ("id", "withdrawn", "message")}
                        yield self._sse_event("queue_remove", {"session_id": session_id, **removed})
                    elif message.get("op") == "reorder":
                        # Immediate send moves a message first: replay the whole snapshot so the client reorders the tray.
                        yield self._sse_event("queue", {"session_id": session_id, "messages": message.get("messages")})
                    else:
                        yield self._sse_event(
                            "queue_upsert", {"session_id": session_id, "message": message.get("message")}
                        )
                    continue

                if msg_type == "ask_user_question":
                    yield self._sse_event(
                        "question",
                        await self._with_session_metadata(copy.deepcopy(message), session_id=session_id),
                    )
                    continue

                if msg_type == "runtime_status":
                    terminal = self._check_runtime_status_terminal(message, session_id)
                    if terminal is not None:
                        status = terminal.data["status"]
                        yield terminal
                    continue

    def _draft_sse_event(self, session_id: str) -> ServerSentEvent:
        return self._sse_event("draft", {"session_id": session_id, **self.session_manager.get_draft_state(session_id)})

    def _status_change_events(self, status: SessionStatus, session_id: str) -> list[ServerSentEvent]:
        """Status changes without runtime_status broadcasts. On returning to running, add a draft snapshot:
        if a session revives after eviction, the new process restarts delta rev counting, so the client must
        switch to the new filter threshold."""
        events = [self._sse_event("status", self._build_status_event_payload(status=status, session_id=session_id))]
        if status == "running":
            events.append(self._draft_sse_event(session_id))
        return events

    async def stream_startup_failure_events(
        self,
        session_id: str,
        failure: dict[str, Any],
    ) -> AsyncIterator[ServerSentEvent]:
        """Immediately send cold-restoration startup failure; persist only the terminal state, without failure details in history."""
        entry = build_failure_entry(failure)
        await self.meta_store.update_status(session_id, "error")
        yield self._sse_event("entry", entry)
        yield self._sse_event(
            "status",
            self._build_status_event_payload(status="error", session_id=session_id),
        )

    @staticmethod
    def _entry_seq(entry: dict[str, Any]) -> int:
        seq = entry.get("seq")
        return seq if isinstance(seq, int) else -1

    @staticmethod
    def _entry_sse_event(entry: dict[str, Any]) -> ServerSentEvent:
        """Entry event: SSE ``id`` is seq; the frontend streaming client resumes with Last-Event-ID on reconnect."""
        return ServerSentEvent(event="entry", data=entry, id=str(entry.get("seq")))

    _TERMINAL_STATUSES: ClassVar[frozenset[SessionStatus]] = frozenset(
        {"idle", "running", "completed", "error", "interrupted"}
    )

    def _check_runtime_status_terminal(self, message: dict[str, Any], session_id: str) -> ServerSentEvent | None:
        """Return a status SSE event if *message* carries a terminal runtime status."""
        runtime_status = str(message.get("status") or "").strip()
        if runtime_status in self._TERMINAL_STATUSES:
            return self._sse_event(
                "status",
                self._build_status_event_payload(
                    status=runtime_status,
                    session_id=session_id,
                    result_message=message,
                ),
            )
        return None

    @staticmethod
    def _sse_event(event: str, data: dict[str, Any]) -> ServerSentEvent:
        """Build an SSE event for FastAPI's EventSourceResponse."""
        return ServerSentEvent(event=event, data=data)

    def _resolve_project_cwd_safe(self, project_name: str) -> Path | None:
        """Resolve the project's working directory, returning None on failure.

        ``SdkTranscriptAdapter`` needs ``project_cwd`` to derive the
        per-project key when reading from the SessionStore. If the project
        directory is missing (deleted, never materialized in tests, etc.)
        we fall back to None — the store helper / SDK defaults handle that.
        """
        try:
            return self.pm.get_project_path(project_name)
        except (FileNotFoundError, ValueError):
            return None

    async def _aresolve_project_cwd_safe(self, project_name: str) -> Path | None:
        """Async entry point for ``_resolve_project_cwd_safe``: project-directory probing calls stat, so offload to a thread."""
        return await asyncio.to_thread(self._resolve_project_cwd_safe, project_name)

    @staticmethod
    def _build_status_event_payload(
        status: SessionStatus,
        session_id: str,
        result_message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build normalized status event payload."""
        message = result_message if isinstance(result_message, dict) else {}
        subtype = message.get("subtype")
        stop_reason = message.get("stop_reason")
        is_error = bool(message.get("is_error"))

        if status == "error" and subtype is None:
            subtype = "error"
        if status == "error":
            is_error = True

        payload: dict[str, Any] = {
            "status": status,
            "subtype": subtype,
            "stop_reason": stop_reason,
            "is_error": is_error,
            "session_id": session_id,
        }
        api_error_status = message.get("api_error_status")  # SDK 0.1.76+
        if api_error_status is not None:
            payload["api_error_status"] = api_error_status
        return payload

    async def _with_session_metadata(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
    ) -> dict[str, Any]:
        """Normalize outward-facing event payloads."""
        normalized = dict(payload)
        normalized["session_id"] = session_id
        normalized.pop("sdk_session_id", None)
        return normalized

    # ==================== Lifecycle ====================

    async def shutdown(self) -> None:
        """Shutdown service gracefully."""
        await self.session_manager.shutdown_gracefully()

    # ==================== Skills ====================

    # Lucide icon hint for each user-invocable skill. The display name is
    # **not** stored here — the frontend resolves it from i18n
    # ``dashboard:skill_name_<id>`` (single source of truth for skill labels
    # lives in ``frontend/src/i18n/{zh,en,vi}/dashboard.ts``).
    # ``tests/unit/test_frontend_skill_i18n.py`` cross-checks SKILL.md against
    # those keys so adding a user-invocable skill without translations fails CI.
    _SKILL_ICONS: ClassVar[dict[str, str]] = {
        "video-workflow": "clapperboard",
        "generate-storyboard": "images",
        "generate-grid": "grid-2x2",
        "generate-video": "film",
        "generate-narration-audio": "audio-lines",
        "generate-assets": "users",
        "edit-video": "scissors",
    }

    def list_available_skills(self, project_name: str | None = None) -> list[dict[str, str]]:
        """List available skills."""
        if project_name:
            self.pm.get_project_path(project_name)

        source_roots = {
            "agent": agent_profile_dir() / ".claude" / "skills",
        }

        skills: list[dict[str, str]] = []
        seen_keys: set[str] = set()

        for scope, root in source_roots.items():
            if not root.exists() or not root.is_dir():
                continue
            try:
                directories = sorted(root.iterdir())
            except OSError:
                continue

            for skill_dir in directories:
                if not skill_dir.is_dir():
                    continue
                skill_file = self._resolve_skill_entry_file(skill_dir)
                if skill_file is None:
                    continue

                try:
                    metadata = self._load_skill_metadata(skill_file, skill_dir.name)
                except OSError:
                    continue

                if metadata is None:
                    continue

                if not metadata["user_invocable"]:
                    continue

                key = f"{scope}:{metadata['name']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                skill_entry: dict[str, Any] = {
                    "name": metadata["name"],
                    "description": metadata["description"],
                    "scope": scope,
                    "path": str(skill_file),
                }
                icon = self._SKILL_ICONS.get(metadata["name"])
                if icon:
                    skill_entry["icon"] = icon
                skills.append(skill_entry)

        return skills

    @staticmethod
    def _resolve_skill_entry_file(skill_dir: Path) -> Path | None:
        # Profile content_mode variants (SKILL.narration.md / SKILL.drama.md) materialize as SKILL.md only when synced
        # into the project directory. A list endpoint scanning the profile directly must recognize variants itself,
        # or variant-only skills such as video-workflow will never be found.
        #
        # Keep the lookup contract consistent with tests/unit/test_frontend_skill_i18n.py:_find_skill_md:
        # strictly select files with is_file, explicitly enumerate valid modes using sorted(VALID_CONTENT_MODES),
        # verify common/variant mutual exclusion, complete variants, and consistent name/user-invocable across all variants.
        # Warn and return None for invalid layouts, preventing the list from randomly exposing a broken configuration for one mode.
        variants = [skill_dir / f"SKILL.{mode}.md" for mode in sorted(VALID_CONTENT_MODES)]
        existing = [v for v in variants if v.is_file()]
        common = skill_dir / "SKILL.md"
        if common.is_file():
            if existing:
                logger.warning("skill %s 同时存在 common 与 content_mode 变体，跳过", skill_dir.name)
                return None
            return common
        if not existing:
            return None
        if len(existing) != len(variants):
            missing = [path.name for path in variants if path not in existing]
            logger.warning("skill %s 的 content_mode 变体不完整，缺少 %s，跳过", skill_dir.name, missing)
            return None
        try:
            metadata = [AssistantService._load_skill_metadata(v, skill_dir.name) for v in existing]
        except OSError:
            return None
        if any(item is None for item in metadata):
            return None
        identities = {(item["name"], item["user_invocable"]) for item in metadata if item is not None}
        if len(identities) > 1:
            logger.warning(
                "skill %s 各 content_mode 变体的 name 或 user-invocable 不一致，跳过；"
                "请保证所有 SKILL.<mode>.md frontmatter 身份一致",
                skill_dir.name,
            )
            return None
        return existing[0]

    @staticmethod
    def _load_skill_metadata(skill_file: Path, fallback_name: str) -> dict[str, Any] | None:
        """Load skill metadata from SKILL.md frontmatter.

        Parsed fields: name, description, user-invocable.
        """
        try:
            content = skill_file.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            logger.warning("invalid skill encoding in %s: %s; skipping", skill_file, exc)
            return None
        if content.lstrip().startswith("---"):
            try:
                metadata = parse_profile_metadata(skill_file)
            except FrontmatterError as exc:
                logger.warning("invalid skill frontmatter in %s: %s; skipping", skill_file, exc)
                return None
            return {
                "name": metadata.name,
                "description": metadata.description,
                "user_invocable": metadata.user_invocable,
            }

        # Keep legacy body-only Skills readable; shipped profile files are required
        # to have YAML frontmatter by the static profile lint.
        name = fallback_name
        description = ""
        user_invocable = True
        for line in content.splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                description = text
                break

        return {
            "name": name,
            "description": description,
            "user_invocable": user_invocable,
        }
