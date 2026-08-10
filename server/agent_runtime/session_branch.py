"""分支会话服务：消息改写的单一入口（见 ``docs/adr/0058``）。

给定原会话与锚点（该会话内某条用户消息），把锚点之前的 transcript 前缀复制
到新 session_id 下，原会话标记 superseded 并记录指向新会话的指针。调用方只
拿到一个可承接首条输入的新 session_id，不感知前缀是怎么拼出来的。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lib.agent_session_store import make_project_key
from lib.agent_session_store.prefix_fork import InvalidAnchorError, SessionStoreLike, copy_session_prefix
from server.agent_runtime.event_log import EventLogStore
from server.agent_runtime.session_store import SessionMetaStore

logger = logging.getLogger(__name__)


class SessionBranchError(RuntimeError):
    """分支会话无法建立。"""


@dataclass(frozen=True, slots=True)
class BranchedSession:
    """分支结果：一个已备好、等待首条输入的新会话。

    ``resumable`` 为真时前缀已落库，以 ``resume=session_id`` 启动；为假即空前缀
    （改写的是第一条消息），新会话就是一个全新会话，以 ``session_id=`` 预指定
    该 id 启动即可——两条路径的会话身份都是本字段。
    """

    session_id: str
    resumable: bool


class SessionBranchService:
    """把「从某条用户消息处分叉」收敛为一次调用。"""

    def __init__(
        self,
        *,
        store: SessionStoreLike | None,
        meta_store: SessionMetaStore,
        event_log_store: EventLogStore,
        resolve_project_cwd: Callable[[str], Path | None],
    ) -> None:
        self._store = store
        self._meta_store = meta_store
        self._event_log_store = event_log_store
        self._resolve_project_cwd = resolve_project_cwd

    async def branch(self, session_id: str, anchor_user_entry_uuid: str) -> BranchedSession:
        """从 ``anchor_user_entry_uuid`` 处分叉 ``session_id``，返回新会话。

        ``anchor_user_entry_uuid`` 是事件日志里那条用户条目的 uuid；它到
        transcript 条目的映射由身份映射表给出，查不到即拒绝——锚点必须是本会话
        自己的用户消息。
        """
        if self._store is None:
            raise SessionBranchError(
                "session branching requires the DB transcript store (ARCREEL_SDK_SESSION_STORE=db)"
            )

        meta = await self._meta_store.get(session_id)
        if meta is None:
            raise SessionBranchError(f"session {session_id} not found")

        project_cwd = self._resolve_project_cwd(meta.project_name)
        if project_cwd is None:
            raise SessionBranchError(f"project {meta.project_name} of session {session_id} is unavailable")

        anchor_uuid = await self._event_log_store.find_user_message_link(session_id, anchor_user_entry_uuid)
        if anchor_uuid is None:
            raise SessionBranchError(f"anchor {anchor_user_entry_uuid} is not a user message of session {session_id}")

        # SDK 以 session_id 作路径分量并校验 UUID 形态，非 UUID 会被静默当作
        # 无此会话；新 id 必须是规范 uuid4 字符串。
        new_session_id = str(uuid4())
        try:
            copied = await copy_session_prefix(
                self._store,
                project_key=make_project_key(project_cwd),
                session_id=session_id,
                anchor_uuid=anchor_uuid,
                new_session_id=new_session_id,
            )
        except InvalidAnchorError as exc:
            raise SessionBranchError(
                f"anchor {anchor_user_entry_uuid} is not a user message of session {session_id}: {exc}"
            ) from exc

        # 先建新会话行再落指针，指针任何时刻都指向已存在的会话。
        await self._meta_store.create(meta.project_name, new_session_id)
        await self._meta_store.mark_superseded(session_id, new_session_id)

        logger.info(
            "branch session: origin=%s new=%s entries=%d subagents=%d",
            session_id,
            new_session_id,
            copied.entries_copied,
            len(copied.subagent_subpaths),
        )
        return BranchedSession(session_id=new_session_id, resumable=copied.entries_copied > 0)
