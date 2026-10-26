"""启动时把助手会话的 Agent 上下文记录对齐到当前数据根派生的存储键。

存储键由项目目录的绝对路径派生（:func:`make_project_key`），CLI 续聊只按当前目录的键查找。数据根换了绝对路径
（桌面端换机器、白标改名、改 ``ARCREEL_DATA_DIR``）后，已有会话仍挂在旧路径派生的键下，续聊会找不到会话。
这里按每个助手会话的项目名与当前项目目录重算键，把挂在别处的会话用 :class:`SessionRekey` 改过来。

挂墓碑名的会话（项目已删除）对齐到原名目录的键：已删除项目恢复时按「当前数据根下的原目录」找它们。

键已一致时只读不写，开销是几次查询：

- 数据库镜像开启时，判断全在数据库里完成（会话元数据联摘要表一次查询），只对键不一致的会话按查到的旧键
  改写记录、移动 SDK 本地 jsonl，不遍历 SDK 本地目录。
- ``ARCREEL_SDK_SESSION_STORE=off`` 时数据库里没有镜像，逐个检查会话在当前键目录下有没有本地 jsonl，
  只有找不到的会话才遍历 SDK 本地目录去找。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.models import AgentSessionSummary
from lib.agent.agent_session_store.rekey import SessionRekey
from lib.db.models.session import AgentSession
from lib.db.repositories.project_records import parse_deleted_project_name
from lib.infra.data_root_layout import PROJECT_NAME_PATTERN

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyAlignment:
    """一次对齐的结果：数据库记录改到当前键下的会话，与本地 jsonl 移到当前键目录下的会话。"""

    rewritten: frozenset[str] = frozenset()
    moved: frozenset[str] = frozenset()


async def align_session_keys(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    projects_dir: Path,
    sdk_config_dir: Path,
    store_enabled: bool,
) -> KeyAlignment:
    """把助手会话的 Agent 上下文记录改到 ``projects_dir`` 下项目目录派生的键，返回改动的会话。重复执行是空操作。"""
    async with session_factory() as session:
        sessions = (await session.execute(select(AgentSession.sdk_session_id, AgentSession.project_name))).all()
    if not sessions:
        return KeyAlignment()
    home_keys = await asyncio.to_thread(_home_keys, projects_dir, {project_name for _, project_name in sessions})
    target_by_session = {sid: home_keys[name] for sid, name in sessions if name in home_keys}

    if store_enabled:
        alignment = await _align_store_records(session_factory, target_by_session, sdk_config_dir)
    else:
        alignment = KeyAlignment(
            moved=frozenset(await asyncio.to_thread(_align_local_only, target_by_session, sdk_config_dir))
        )
    if alignment.rewritten or alignment.moved:
        logger.info(
            "会话存储键对齐：%d 个会话的数据库记录、%d 个会话的本地记录改到 %s 下项目目录的键",
            len(alignment.rewritten),
            len(alignment.moved),
            projects_dir,
        )
    return alignment


def _home_keys(projects_dir: Path, project_names: Iterable[str]) -> dict[str, str]:
    """会话项目名 → 它的 Agent 上下文应在的存储键；墓碑名取原名，名字不合法的不处理。"""
    keys: dict[str, str] = {}
    for name in project_names:
        parsed = parse_deleted_project_name(name)
        home = parsed[0] if parsed is not None else name
        if PROJECT_NAME_PATTERN.fullmatch(home):
            keys[name] = make_project_key(projects_dir / home)
    return keys


async def _align_store_records(
    session_factory: async_sessionmaker[AsyncSession],
    target_by_session: dict[str, str],
    sdk_config_dir: Path,
) -> KeyAlignment:
    async with session_factory() as session:
        rows = await session.execute(
            select(AgentSessionSummary.session_id, AgentSessionSummary.project_key)
            .join(AgentSession, AgentSession.sdk_session_id == AgentSessionSummary.session_id)
            .distinct()
        )
        keys_by_session: dict[str, set[str]] = defaultdict(set)
        for session_id, project_key in rows:
            keys_by_session[session_id].add(project_key)

        sessions_by_move: dict[tuple[str, str], list[str]] = defaultdict(list)
        for session_id, keys in keys_by_session.items():
            target = target_by_session.get(session_id)
            # 当前键下已有记录即可续聊；别处的副本 SessionRekey 也不会合并，留着不动
            if target is None or target in keys:
                continue
            if len(keys) > 1:
                logger.warning(
                    "会话 %s 在多个存储键下都有记录（%s），保持原样，未改到 %s", session_id, sorted(keys), target
                )
                continue
            [source] = keys
            sessions_by_move[source, target].append(session_id)
        if not sessions_by_move:
            return KeyAlignment()

        rekeys = [
            SessionRekey.of(session_ids, target, source_key=source)
            for (source, target), session_ids in sessions_by_move.items()
        ]
        rewritten: set[str] = set()
        for rekey in rekeys:
            rewritten |= await rekey.rewrite_records(session)
        await session.commit()

    def _move_local() -> set[str]:
        moved: set[str] = set()
        for rekey in rekeys:
            moved |= rekey.move_local_transcripts(sdk_config_dir)
        return moved

    return KeyAlignment(rewritten=frozenset(rewritten), moved=frozenset(await asyncio.to_thread(_move_local)))


def _align_local_only(target_by_session: dict[str, str], sdk_config_dir: Path) -> set[str]:
    """数据库没有镜像时，当前键目录下找不到本地记录的会话才去别的键目录找来移过去。"""
    sdk_projects_dir = sdk_config_dir / "projects"
    if not sdk_projects_dir.is_dir():
        return set()
    missing: dict[str, list[str]] = defaultdict(list)
    for session_id, target in target_by_session.items():
        target_dir = sdk_projects_dir / target
        if not (target_dir / f"{session_id}.jsonl").exists() and not (target_dir / session_id).exists():
            missing[target].append(session_id)
    moved: set[str] = set()
    for target, session_ids in missing.items():
        moved |= SessionRekey.of(session_ids, target).move_local_transcripts(sdk_config_dir)
    return moved


__all__ = ["KeyAlignment", "align_session_keys"]
