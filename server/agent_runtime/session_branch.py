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
from server.agent_runtime.event_log import EventLogService
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
        event_log: EventLogService,
        resolve_project_cwd: Callable[[str], Path | None],
    ) -> None:
        self._store = store
        self._meta_store = meta_store
        self._event_log = event_log
        self._resolve_project_cwd = resolve_project_cwd

    async def branch(self, session_id: str, anchor_user_entry_uuid: str) -> BranchedSession:
        """从 ``anchor_user_entry_uuid`` 处分叉 ``session_id``，返回新会话。

        ``anchor_user_entry_uuid`` 是事件日志里那条用户条目的 uuid，由事件日志
        解析成 transcript 域的锚点；解析不出即拒绝——锚点必须是本会话自己的
        一条用户消息。本服务不感知两个 uuid 域的差异。
        """
        store = self._store
        if store is None:
            raise SessionBranchError(
                "session branching requires the DB transcript store (ARCREEL_SDK_SESSION_STORE=db)"
            )

        meta = await self._meta_store.get(session_id)
        if meta is None:
            raise SessionBranchError(f"session {session_id} not found")

        project_cwd = self._resolve_project_cwd(meta.project_name)
        if project_cwd is None:
            raise SessionBranchError(f"project {meta.project_name} of session {session_id} is unavailable")

        anchor_uuid = await self._event_log.resolve_user_message_anchor(session_id, anchor_user_entry_uuid, project_cwd)
        if anchor_uuid is None:
            raise SessionBranchError(f"anchor {anchor_user_entry_uuid} is not a user message of session {session_id}")

        # SDK 以 session_id 作路径分量并校验 UUID 形态，非 UUID 会被静默当作
        # 无此会话；新 id 必须是规范 uuid4 字符串。
        new_session_id = str(uuid4())
        project_key = make_project_key(project_cwd)
        # 从这里到落指针为止是一次要么整体成立、要么整体撤回的转换：store 的每次
        # append 各自提交，中途失败会留下半份 transcript，而 transcript 行不依赖
        # 元数据行就能被 store 的会话枚举看见。
        try:
            copied = await copy_session_prefix(
                store,
                project_key=project_key,
                session_id=session_id,
                anchor_uuid=anchor_uuid,
                new_session_id=new_session_id,
            )
            # 先建新会话行再落指针，指针任何时刻都指向已存在的会话。分叉血统随
            # 建行落库：它是 branch 时刻的不可变事实，与 superseded_by 表达的
            # 「原会话被谁取代」互为反向，后者可被删除接手改写，前者不会。
            await self._meta_store.create(
                meta.project_name,
                new_session_id,
                fork_parent_session_id=session_id,
                fork_anchor_uuid=anchor_uuid,
            )
            if not await self._meta_store.mark_superseded(session_id, new_session_id):
                raise SessionBranchError(f"session {session_id} has already been superseded by another branch")
        except InvalidAnchorError as exc:
            # 锚点校验在任何写入之前，没有可撤回的东西。
            raise SessionBranchError(
                f"anchor {anchor_user_entry_uuid} is not a user message of session {session_id}: {exc}"
            ) from exc
        except BaseException:
            await self._discard(store, project_key, session_id, new_session_id)
            raise

        logger.info(
            "branch session: origin=%s new=%s entries=%d subagents=%d",
            session_id,
            new_session_id,
            copied.entries_copied,
            len(copied.subagent_subpaths),
        )
        return BranchedSession(session_id=new_session_id, resumable=copied.entries_copied > 0)

    async def discard(self, origin_session_id: str, new_session_id: str) -> None:
        """撤回一次已发布的分支，让原会话回到可再次改写的状态。

        供编排层在「分支已建好、改写后的消息却没能派发出去」时收尾：那一步失败
        即整次改写失败，分支不该留下——原会话的 superseded 指针不撤，用户就再也
        改写不了它，而那个空转的新会话会顶替它出现在会话列表里。
        """
        store = self._store
        if store is None:
            return
        meta = await self._meta_store.get(new_session_id)
        project_cwd = self._resolve_project_cwd(meta.project_name) if meta is not None else None
        project_key = make_project_key(project_cwd) if project_cwd is not None else None
        await self._discard(store, project_key, origin_session_id, new_session_id)

    async def _discard(
        self, store: SessionStoreLike, project_key: str | None, origin_session_id: str, new_session_id: str
    ) -> None:
        """撤回半成品分支：原会话的指针、新会话的 transcript 与元数据行都清掉。

        清理自身的失败只记日志，不掩盖触发撤回的原始错误。先撤指针再删数据：
        指针若已落库（例如提交后当场被取消），留着会让原会话指向一个即将不存在
        的会话，且自己也从会话列表里消失。
        """
        try:
            await self._meta_store.clear_superseded(origin_session_id, new_session_id)
        except Exception:
            logger.exception("failed to discard superseded pointer of session %s", origin_session_id)
        if project_key is not None:
            try:
                # 不带 subpath 的 delete 连子代理子路径与 summary 一并清除。
                await store.delete({"project_key": project_key, "session_id": new_session_id})
            except Exception:
                logger.exception("failed to discard transcript of incomplete branch %s", new_session_id)
        try:
            await self._meta_store.delete(new_session_id)
        except Exception:
            logger.exception("failed to discard metadata of incomplete branch %s", new_session_id)
