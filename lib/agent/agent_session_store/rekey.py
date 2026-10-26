"""按会话把 Agent 上下文记录改到另一个存储键下。

SDK transcript 按 ``(project_key, session_id)`` 存取，``project_key`` 由项目目录的绝对路径派生
（:func:`make_project_key`），CLI 续聊只按当前目录的键查找。项目目录换了绝对路径而会话仍归这个项目时，
用 :class:`SessionRekey` 把这些会话改到新目录的键下：数据库里的 transcript 镜像与摘要，以及 SDK 配置目录下
的本地 jsonl（``ARCREEL_SDK_SESSION_STORE=off`` 时唯一的会话来源）。

只动点名的会话：同一个键下可能还有别的项目的会话，例如占用了原名的同名新项目，整键改写会把它们一起带走。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.agent.agent_session_store.models import AgentSessionEntry, AgentSessionSummary

logger = logging.getLogger(__name__)

_TABLES = (AgentSessionEntry, AgentSessionSummary)


@dataclass(frozen=True)
class SessionRekey:
    """把 ``session_ids`` 这些会话的 Agent 上下文记录改到 ``target_key`` 下。

    ``source_key`` 给出时只搬这个键下的记录；为 ``None`` 时从目标键以外的任何键搬，适用于不知道旧路径的场合。
    一个会话在目标键下已有记录，或在多个非目标键下都有记录时，这个会话原样不动并告警，不合并两份 transcript。
    记录已在目标键下时什么也不写，重复执行是空操作。
    """

    session_ids: frozenset[str]
    target_key: str
    source_key: str | None = None

    @classmethod
    def of(cls, session_ids: Iterable[str], target_key: str, *, source_key: str | None = None) -> SessionRekey:
        return cls(frozenset(session_ids), target_key, source_key)

    def _is_noop(self) -> bool:
        return not self.session_ids or self.source_key == self.target_key

    async def rewrite_records(self, session: AsyncSession) -> set[str]:
        """在 ``session`` 的事务里改写数据库中的 transcript 镜像与摘要，返回改到目标键下的会话。不提交。"""
        if self._is_noop():
            return set()
        keys_by_session: dict[str, set[str]] = defaultdict(set)
        for table in _TABLES:
            rows = await session.execute(
                select(table.session_id, table.project_key).where(table.session_id.in_(self.session_ids)).distinct()
            )
            for session_id, project_key in rows:
                keys_by_session[session_id].add(project_key)

        moves: dict[str, str] = {}
        for session_id, keys in keys_by_session.items():
            sources = keys - {self.target_key}
            if self.source_key is not None:
                sources &= {self.source_key}
            if not sources:
                continue
            if self.target_key in keys or len(sources) > 1:
                logger.warning(
                    "会话 %s 在多个存储键下都有记录（%s），保持原样，未改到 %s",
                    session_id,
                    sorted(keys),
                    self.target_key,
                )
                continue
            moves[session_id] = sources.pop()

        sessions_by_source: dict[str, list[str]] = defaultdict(list)
        for session_id, source in moves.items():
            sessions_by_source[source].append(session_id)
        for source, session_ids in sessions_by_source.items():
            for table in _TABLES:
                await session.execute(
                    update(table)
                    .where(table.project_key == source, table.session_id.in_(session_ids))
                    .values(project_key=self.target_key)
                )
        return set(moves)

    def move_local_transcripts(self, sdk_config_dir: Path) -> set[str]:
        """把 SDK 配置目录下这些会话的本地 jsonl 与同名子目录移到目标键目录，返回移过去的会话。

        同步文件操作，由调用方放进线程。尽力而为：目标键目录下已有同一会话，或会话出现在多个来源目录时
        原样不动并告警；改名失败只告警。
        """
        if self._is_noop():
            return set()
        projects_dir = sdk_config_dir / "projects"
        target_dir = projects_dir / self.target_key
        found: dict[str, dict[Path, list[Path]]] = defaultdict(lambda: defaultdict(list))
        for source_dir in self._local_source_dirs(projects_dir):
            try:
                children = list(source_dir.iterdir())
            except OSError:
                logger.warning("SDK 会话目录 %s 读取失败", source_dir, exc_info=True)
                continue
            for child in children:
                session_id = child.name.removesuffix(".jsonl")
                if session_id in self.session_ids:
                    found[session_id][source_dir].append(child)

        moved: set[str] = set()
        for session_id, entries_by_dir in found.items():
            if len(entries_by_dir) > 1 or _has_local_session(target_dir, session_id):
                logger.warning(
                    "会话 %s 的 SDK 本地记录在多个目录下都有（%s），保持原样，未移到 %s",
                    session_id,
                    sorted(str(path) for path in [*entries_by_dir, target_dir]),
                    target_dir,
                )
                continue
            [entries] = entries_by_dir.values()
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                for entry in entries:
                    entry.rename(target_dir / entry.name)
            except OSError:
                logger.warning("会话 %s 的 SDK 本地记录移到 %s 失败", session_id, target_dir, exc_info=True)
                continue
            moved.add(session_id)
        return moved

    def _local_source_dirs(self, projects_dir: Path) -> list[Path]:
        if self.source_key is not None:
            source_dir = projects_dir / self.source_key
            return [source_dir] if source_dir.is_dir() else []
        try:
            return [entry for entry in projects_dir.iterdir() if entry.is_dir() and entry.name != self.target_key]
        except OSError:
            return []


def _has_local_session(key_dir: Path, session_id: str) -> bool:
    return (key_dir / f"{session_id}.jsonl").exists() or (key_dir / session_id).exists()


__all__ = ["SessionRekey"]
