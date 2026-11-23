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
    """消息改写无法进行。"""


class RewriteAnchorError(MessageRewriteError):
    """锚点不是该会话内的一条用户消息。"""


class PendingQuestionError(MessageRewriteError):
    """会话有未决问答卡片，问答优先。"""


class QueuedMessagesPendingError(MessageRewriteError):
    """会话还有排队消息没进入对话：此刻分叉，它们会在被取代的原会话里继续执行。"""


class SessionSupersededError(MessageRewriteError):
    """会话已被一次改写取代，改写入口只对当前分支开放。"""


class RewriteUnavailableError(MessageRewriteError):
    """当前部署形态下无法改写（transcript 镜像未开启）。"""


class InterruptSettleTimeoutError(MessageRewriteError):
    """等待运行中轮次中断到终态超时。"""


class AssistantService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.layout = DataRootLayout.current()
        self.data_root = self.layout.root

        self.pm = ProjectManager(self.data_root)
        self.meta_store = SessionMetaStore()
        self.event_log_store = EventLogStore()
        self.session_manager = SessionManager(
            project_root=self.project_root,
            meta_store=self.meta_store,
            data_root=self.data_root,
            event_log_store=self.event_log_store,
        )
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
        self._new_session_client_keys: OrderedDict[str, str] = OrderedDict()
        self._new_session_client_keys_max = 256
        self._new_session_locks = KeyedLocks()
        self._rewrite_locks = KeyedLocks()
        self._admission_locks = KeyedLocks()
        self.stream_heartbeat_seconds = int(os.environ.get("ASSISTANT_STREAM_HEARTBEAT_SECONDS", "20"))

    async def startup(self, *, in_docker: bool = False, sandbox_enabled: bool = True) -> None:
        """Run async initialization (must be called from event loop).

        ``sandbox_enabled=False`` 时关闭 SDK SandboxSettings 并把 Bash 工具调用
        切到代码白名单路径（详见 ``SessionManager.configure_sandbox_runtime``）。
        默认 ``True`` 保持 macOS / Linux 现状不变。
        """
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

    def _prepare_content(
        self,
        content: str,
        images: list["ImageAttachment"] | None = None,
    ) -> str | list[dict[str, Any]]:
        """送入 CLI 的消息内容：纯文本为字符串，带图片时为图片块在前、文本块在后的列表。"""
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
        """构造用户消息的权威条目：身份在发送时分配，被 Agent 接纳时写入日志。"""
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

        已有会话：有轮次在跑时同样受理，响应携带排队消息（``queued_message``），被 Agent 接纳时
        才作为条目经 entry 流下发；同一 ``client_key`` 的重试若消息已入日志，响应携带权威条目
        （``entry``）。已被改写取代的会话不再受理消息（``SessionSupersededError``）。新会话：首条消息
        写入日志后才返回，响应携带权威条目。前端不渲染任何本地合成消息。
        """
        await asyncio.to_thread(self.pm.get_project_path, project_name)

        if session_id:
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
        if not client_key:
            return await self._create_new_session(project_name, content, images, locale, client_key)

        existing = await self._find_accepted_new_session(client_key, project_name)
        if existing is not None:
            return existing

        lock = self._new_session_locks.lock_for(client_key)
        async with lock:
            existing = await self._find_accepted_new_session(client_key, project_name)
            if existing is not None:
                return existing
            result = await self._create_new_session(project_name, content, images, locale, client_key)
            self._record_new_session_client_key(client_key, result["session_id"])
            return result

    async def _find_accepted_new_session(self, client_key: str, project_name: str) -> dict[str, Any] | None:
        """按幂等键定位已受理的新会话：进程内映射为快路径，事件日志跨会话
        查询兜底——进程重启 / LRU 淘汰后映射丢失，受理已落库的重试仍须命中
        既有会话而非重复建会话（重复执行同一 prompt、重复计费）。

        命中会话的项目归属须与调用方 ``project_name`` 一致：不一致则视为未命中
        （返回 None，由调用方在当前项目新建会话），不抛错——用户未指名任何
        会话，跨项目复用同一 client_key 时新会话意图应落在当前项目。两条查找
        路径口径一致，均在返回前做归属校验。"""
        mapped_session_id = self._new_session_client_keys.get(client_key)
        if mapped_session_id is not None:
            entry = await self.event_log_store.find_by_client_key(mapped_session_id, client_key)
            if entry is None:
                if self._new_session_client_keys.get(client_key) == mapped_session_id:
                    self._new_session_client_keys.pop(client_key, None)
            elif await self._new_session_matches_project(mapped_session_id, project_name):
                self._record_new_session_client_key(client_key, mapped_session_id)
                return {"status": "accepted", "session_id": mapped_session_id, "entry": entry, "queued_message": None}
        recovered = await self.event_log_store.find_new_session_by_client_key(client_key)
        if recovered is None:
            return None
        session_id, entry = recovered
        if not await self._new_session_matches_project(session_id, project_name):
            return None
        if self._new_session_client_keys.get(client_key) in (None, session_id):
            self._record_new_session_client_key(client_key, session_id)
        return {"status": "accepted", "session_id": session_id, "entry": entry, "queued_message": None}

    async def _new_session_matches_project(self, session_id: str, project_name: str) -> bool:
        """幂等命中的新会话是否属于当前调用项目。校验依据为会话 meta 的
        ``project_name``；meta 不存在（异常 / 已删）时不阻断命中，保持既有幂等
        语义——跨项目串号的前提是命中会话 meta 存在且项目不同。"""
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
        """实际创建新会话并投递首条消息，不涉及 client_key 幂等映射记账。"""
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
        """已有会话的受理响应：排队消息与权威条目二者恰有其一（条目在幂等重试命中已入日志的消息时给出）。"""
        return {
            "status": "accepted",
            "session_id": session_id,
            **extra,
            "entry": accepted.get("entry"),
            "queued_message": accepted.get("queued_message"),
        }

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
        """改写 ``session_id`` 中锚点处的那条用户消息，返回承接改写的新会话。

        编排顺序即拒绝的代价顺序：能拒的先拒（锚点、未决问答），代价大的后做
        （中断运行中的轮次、分叉、派发）——被拒的请求不该已经打断用户的轮次。
        分叉之后任何一步失败都是整次改写失败，分支整体撤回，原会话回到可再改写
        的状态。

        响应与发送端点同构：``client_key`` 为请求侧幂等键，重试不产生第二个分支
        会话；改写后的消息在新会话里先是排队消息（``queued_message``），被接纳后落进新会话的日志。
        """
        await asyncio.to_thread(self.pm.get_project_path, project_name)
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

        if meta.superseded_by is not None:
            replay = await self._replay_rewrite(meta.superseded_by, client_key)
            if replay is not None:
                return replay
            raise SessionSupersededError(f"session {session_id} has already been superseded by {meta.superseded_by}")

        prompt = self._prepare_content(content, images)

        project_cwd = await self._aresolve_project_cwd_safe(meta.project_name)
        anchor = await self.event_log.resolve_user_message_anchor(session_id, anchor_entry_uuid, project_cwd)
        if anchor is None:
            raise RewriteAnchorError(f"anchor {anchor_entry_uuid} is not a user message of session {session_id}")

        if await self.session_manager.get_pending_questions_snapshot(session_id):
            raise PendingQuestionError(f"session {session_id} has pending questions")
        async with self._admission_locks.lock_for(session_id):
            if self.session_manager.get_queued_messages_snapshot(session_id):
                raise QueuedMessagesPendingError(f"session {session_id} has queued messages")

            await self._settle_running_session(session_id)

            branched = await self._branch_or_reject(session_id, anchor_entry_uuid)
        new_session_id = branched.session_id
        try:
            new_meta = await self.meta_store.get(new_session_id)
            if branched.resumable:
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
        """幂等重放：给定 client_key 的改写是否已由 ``new_session_id`` 承接。"""
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
        """中断运行中的轮次并等它落到终态——运行中的会话分叉不出干净的前缀。

        对用户是一步操作：改写请求自带中断，不需要先点停止。终态由 inbox 任务
        在收到 SDK 的 result 消息后推导，只能观察状态；被中断轮次尾巴上的消息
        本就排在锚点之后、要随原分支作废，无需等它们落库。
        """
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
        """分叉，并把分支服务的异常翻译回编排层能分辨的拒绝理由。"""
        try:
            return await self.session_branch.branch(session_id, anchor_entry_uuid)
        except BranchAnchorError as exc:
            raise RewriteAnchorError(
                f"anchor {anchor_entry_uuid} is not a forkable user message of session {session_id}"
            ) from exc
        except SessionBranchError as exc:
            meta = await self.meta_store.get(session_id)
            if meta is not None and meta.superseded_by is not None:
                raise SessionSupersededError(
                    f"session {session_id} has already been superseded by {meta.superseded_by}"
                ) from exc
            raise

    async def _discard_branch(self, origin_session_id: str, new_session_id: str) -> None:
        """撤回一个没能承接住改写的分支：先断开它的运行时，再清数据。"""
        try:
            if new_session_id in self.session_manager.sessions:
                await self.session_manager.close_session(new_session_id, reason="message rewrite dispatch failed")
        except Exception:
            logger.exception("关闭未完成分支会话失败 session_id=%s", new_session_id)
        try:
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
        """编辑或删除一条排队消息：先向 CLI 撤回，撤回成功才移出排队，编辑时响应带回消息内容。

        ``outcome`` 为 ``withdrawn`` 时消息已移出排队（编辑时 ``message`` 是要退回输入框的内容）；
        为 ``accepted`` 时 Agent 已接收这条消息，它照常进入对话。
        """
        meta = await self.meta_store.get(session_id)
        if meta is None or meta.project_name != project_name:
            raise FileNotFoundError(f"session not found: {session_id}")
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
        """把一条「未发送」消息重新交给 CLI，必要时先复活会话；响应带回这条排队消息。"""
        async with self._admission_locks.lock_for(session_id):
            meta = await self.meta_store.get(session_id)
            if meta is None or meta.project_name != project_name:
                raise FileNotFoundError(f"session not found: {session_id}")
            queued = await self.session_manager.resend_queued_message(session_id, message_id, meta=meta, locale=locale)
        return {"session_id": session_id, "id": message_id, "queued_message": queued.to_payload()}

    async def send_queued_message_now(self, project_name: str, session_id: str, message_id: str) -> dict[str, Any]:
        """立即发送一条排队消息：Agent 打断当前轮先处理它。

        ``outcome`` 为 ``sent`` 时已以 ``now`` 优先级重新送入，它仍是托盘里的同一条消息；为
        ``accepted`` 时 Agent 已接收这条消息，不再重发，它照常进入对话。
        """
        meta = await self.meta_store.get(session_id)
        if meta is None or meta.project_name != project_name:
            raise FileNotFoundError(f"session not found: {session_id}")
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

    async def list_session_entries(
        self,
        session_id: str,
        *,
        meta: SessionMeta | None = None,
        after_seq: int = -1,
    ) -> dict[str, Any]:
        """冷读事件日志（历史回放 / 非 running 会话初始加载）。"""
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
        """SSE entry 流：事件 ``id`` 即 seq，断线重连按 cursor 续传、不整帧重算。

        生命周期跟随会话面板：开场依次下发 ``entry``×N（cursor 之后的存量）、
        ``draft``（流式累积态 + rev 过滤门槛）、``queue``（排队消息快照）、``question``×N
        （未决问题）和当前 ``status``，之后直播 entry / delta / 排队消息变化（``queue_upsert`` /
        ``queue_remove``，立即发送重排托盘时再发一次 ``queue``）/ question / status。``status`` 只更新
        状态、不关流；流只在客户端离开（或订阅者被溢出移除，即重连信号）时结束。
        会话不常驻时同样建流等待，之后由发送复活的会话照常推送。
        """
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
            for entry in await self.event_log.list_entries(session_id, project_cwd, after_seq=last_seq):
                last_seq = max(last_seq, self._entry_seq(entry))
                yield self._entry_sse_event(entry)

            yield self._draft_sse_event(session_id)

            yield self._sse_event(
                "queue",
                {"session_id": session_id, "messages": self.session_manager.get_queued_messages_snapshot(session_id)},
            )

            for question in await self.session_manager.get_pending_questions_snapshot(session_id):
                yield self._sse_event("question", {**question, "session_id": session_id})

            status: SessionStatus = await self.session_manager.get_status(session_id) or meta.status
            yield self._sse_event("status", self._build_status_event_payload(status=status, session_id=session_id))

            async for stream_event in stream:
                if request is not None and await request.is_disconnected():
                    break

                if isinstance(stream_event, Heartbeat):
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
                        removed = {k: v for k, v in message.items() if k in ("id", "withdrawn", "message")}
                        yield self._sse_event("queue_remove", {"session_id": session_id, **removed})
                    elif message.get("op") == "reorder":
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
        """不经 runtime_status 广播的状态变化。回到 running 时补一帧 draft 快照：会话若经驱逐后
        复活，新进程的 delta rev 从头计数，客户端要换用新的过滤门槛。"""
        events = [self._sse_event("status", self._build_status_event_payload(status=status, session_id=session_id))]
        if status == "running":
            events.append(self._draft_sse_event(session_id))
        return events

    async def stream_startup_failure_events(
        self,
        session_id: str,
        failure: dict[str, Any],
    ) -> AsyncIterator[ServerSentEvent]:
        """即时发送冷恢复启动失败；只落终态，不把故障详情写入历史。"""
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
        """entry 事件：SSE ``id`` 字段即 seq，前端流式客户端重连时以 Last-Event-ID 续传。"""
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
        """``_resolve_project_cwd_safe`` 的异步入口：项目目录探测会 stat，卸到线程。"""
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
        api_error_status = message.get("api_error_status")
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

    async def shutdown(self) -> None:
        """Shutdown service gracefully."""
        await self.session_manager.shutdown_gracefully()

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
