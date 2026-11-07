"""Database records retained for deleted projects.

Tasks, batches, call records, assistant sessions, provider asset links and formal object
references associate with projects only by name. After deletion, reassign these rows to
a tombstone name: costs remain attributed to the deleted project, same-named new/imported
projects cannot find them, and active-task deduplication cannot merge new requests into old tasks.
Tombstones contain ``#`` while project names allow only letters, digits and hyphens, preventing
collisions. On project restoration, reassign records from the tombstone to the restored project name.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.base import utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import FormalObjectRef
from lib.db.models.session import AgentSession
from lib.db.models.task import GenerationBatch, Task

_DELETED_MARKER = "#deleted-"
#: Records associated with projects by name. Provider asset links key on project-local content-version
#: identity; same-named new projects cannot inherit links for that identity. Reassign formal object references
#: with the project: restorable deleted projects retain objects; remove references by tombstone on permanent deletion.
_PROJECT_KEYED_MODELS = (Task, GenerationBatch, ApiCall, AgentSession, ProviderAssetLink, FormalObjectRef)
_DELETED_NAME_RE = re.compile(r"^([A-Za-z0-9-]+)#deleted-(\d{8}T\d{6}Z)$")


def _deleted_project_name(project_name: str, deleted_at: datetime) -> str:
    """Tombstone name for records after project deletion at ``deleted_at`` (UTC), for example ``demo#deleted-20261002T034309Z``.

    The frontend usage page parses original name and deletion date from this format;
    update ``frontend/src/components/usage/usage-record-format.ts`` alongside format changes.
    """
    return f"{project_name}{_DELETED_MARKER}{deleted_at:%Y%m%dT%H%M%SZ}"


def is_deleted_project_name(project_name: str) -> bool:
    """Whether ``project_name`` is a deleted-project tombstone; it is not a valid project name and cannot resolve to a project directory."""
    return _DELETED_MARKER in project_name


def parse_deleted_project_name(tombstone: str) -> tuple[str, datetime] | None:
    """Split a tombstone into original project name and UTC deletion time; return ``None`` for non-tombstones."""
    match = _DELETED_NAME_RE.fullmatch(tombstone)
    if match is None:
        return None
    return match[1], datetime.strptime(match[2], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


async def retire_project_records(session: AsyncSession, project_name: str) -> tuple[str, list[str]]:
    """Cancel queued project tasks and reassign all project records to a tombstone within the ``session`` transaction; return the tombstone and running task IDs.

    Do not commit: callers commit cancellation and renaming together so readers never see
    partially updated records. Running tasks cannot be cancelled; they finish normally,
    settling under the tombstone. Callers invalidate their project claims using returned IDs
    so they abandon persistence. Do not distinguish users: project directories have no user dimension.
    """
    now = utc_now()
    tombstone = _deleted_project_name(project_name, now)
    await session.execute(
        update(Task)
        .where(Task.project_name == project_name, Task.status == "queued")
        .values(status="cancelled", cancelled_by="user", finished_at=now, updated_at=now)
    )
    # Read after cancelling queued tasks: concurrent claims either already moved tasks to running or can only see them cancelled.
    running = await session.execute(
        select(Task.task_id).where(Task.project_name == project_name, Task.status == "running")
    )
    running_task_ids = list(running.scalars())
    for model in _PROJECT_KEYED_MODELS:
        stmt = update(model).where(model.project_name == project_name)
        if model is FormalObjectRef:
            # Keep in-progress commit reservations under the project name: overwrite import commits new images before retiring the old project, then settlement records new-project references by that name
            stmt = stmt.where(FormalObjectRef.state != "pending")
        await session.execute(stmt.values(project_name=tombstone))
    return tombstone, running_task_ids


async def restore_project_records(session: AsyncSession, tombstone: str, project_name: str) -> None:
    """Reassign all records under ``tombstone`` back to ``project_name``. Do not commit; tasks cancelled during deletion remain cancelled."""
    for model in _PROJECT_KEYED_MODELS:
        await session.execute(update(model).where(model.project_name == tombstone).values(project_name=project_name))


async def retired_agent_session_ids(session: AsyncSession, tombstone: str) -> list[str]:
    """SDK session IDs for assistant sessions under ``tombstone``."""
    rows = await session.scalars(select(AgentSession.sdk_session_id).where(AgentSession.project_name == tombstone))
    return list(rows)


__all__ = [
    "is_deleted_project_name",
    "parse_deleted_project_name",
    "restore_project_records",
    "retire_project_records",
    "retired_agent_session_ids",
]
