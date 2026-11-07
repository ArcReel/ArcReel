"""At startup, align assistant-session Agent context with storage keys derived from the current data root.

Keys derive from absolute project-directory paths (:func:`make_project_key`); CLI resumption
searches only the current directory’s key. After data-root moves (desktop machine changes,
white-label renaming, or ``ARCREEL_DATA_DIR`` changes), sessions remain under old-path keys and
cannot resume. Recompute keys from each assistant session’s project name/current directory and
use :class:`SessionRekey` to relocate sessions stored elsewhere.

Tombstoned sessions (deleted projects) align to original-name directory keys, since restoration
searches original directories under the current data root.

Already-aligned keys perform only a few read queries, without writes:

- With DB mirroring enabled, decide entirely in DB (one session-metadata/summary join); rewrite
  records and move SDK-local jsonl only for mismatches, using queried old keys without scanning SDK directories.
- With ``ARCREEL_SDK_SESSION_STORE=off``, no DB mirror exists. Check local jsonl under each
  current key first, scanning SDK-local directories only for missing sessions.
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
    """Alignment results: sessions whose DB records moved to current keys and whose local jsonl moved to current-key directories."""

    rewritten: frozenset[str] = frozenset()
    moved: frozenset[str] = frozenset()


async def align_session_keys(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    projects_dir: Path,
    sdk_config_dir: Path,
    store_enabled: bool,
) -> KeyAlignment:
    """Align assistant-session context with project keys derived under ``projects_dir``; return changed sessions. Reruns are no-ops."""
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
    """Session project name → expected Agent-context key; tombstones use original names, invalid names are skipped."""
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
            # Current-key records suffice for resumption; SessionRekey will not merge other copies, so leave them untouched.
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
    """Without DB mirroring, search other key directories and move sessions only if absent from their current-key directory."""
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
