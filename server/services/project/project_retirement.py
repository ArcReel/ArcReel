"""Shared retirement for project deletion/overwrite import, and record reassignment on restoring deleted projects.

Both deletion and overwrite import retire the existing project: cancel queued tasks, reassign tasks/batches/call records/assistant
sessions to a tombstone name, and invalidate running-task claims so they abandon persistence. Later projects using the name,
whether overwritten imports or projects created/imported after deletion, never inherit those records. Restoration reassigns
records from the tombstone to the restored name. If the original name is occupied, restored assistant-session Agent context
records move with the project directory to storage keys derived from the new directory."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.rekey import SessionRekey
from lib.db.repositories.project_records import (
    parse_deleted_project_name,
    restore_project_records,
    retire_project_records,
    retired_agent_session_ids,
)
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async, run_sync_transaction
from lib.material.formal_objects import retiring_project_references
from lib.project.task_project_claim import restore_task_project_claims, revoke_task_project_claims


@asynccontextmanager
async def retiring_project(session_factory: async_sessionmaker[AsyncSession], project_dir: Path) -> AsyncGenerator[str]:
    """Retire the existing project in ``project_dir``. The ``async with`` block receives its tombstone name and moves the directory to a deleted project with that name.

    On entry, one transaction first backfills references to formal objects actually owned by the project (published unsettled commits
    have reservations only), then cancels queued tasks, reassigns records and invalidates running claims. Commit only after normal
    block exit; once started, commit cannot be cancelled. Block/commit errors roll back the transaction and restore claims, retaining
    existing records; the caller restores the directory. From backfill until commit/rollback, new remote-image saves for the directory
    fail before upload, preventing tombstones from holding remote versions without owning references."""
    project_name = project_dir.name
    async with session_factory() as session, retiring_project_references(session, project_name, project_dir):
        tombstone, running_task_ids = await retire_project_records(session, project_name)
        revoke_task_project_claims(running_task_ids)
        try:
            yield tombstone
            await run_noninterruptible_async(session.commit())
        except BaseException:
            await session.rollback()
            restore_task_project_claims(running_task_ids)
            raise


async def restore_retired_project(
    session_factory: async_sessionmaker[AsyncSession],
    tombstone: str,
    project_dir: Path,
    *,
    sdk_config_dir: Path,
) -> None:
    """After restoring a deleted project to ``project_dir``, reassign ``tombstone`` records to the restored name and commit; once started, commit cannot be cancelled.

    If restored under another name, also change Agent-session storage keys derived from absolute project paths. In the same
    transaction, move this project’s database transcript mirrors to the new directory’s key; after commit, move SDK local records
    under ``sdk_config_dir``. The old key now belongs to the project occupying the original name, so move by session only."""
    async with session_factory() as session:
        rekey = await _session_rekey(session, tombstone, project_dir)
        if rekey is not None:
            await rekey.rewrite_records(session)
        await restore_project_records(session, tombstone, project_dir.name)
        await run_noninterruptible_async(session.commit())
    if rekey is not None:
        await run_sync_transaction(rekey.move_local_transcripts, sdk_config_dir)


async def _session_rekey(session: AsyncSession, tombstone: str, project_dir: Path) -> SessionRekey | None:
    parsed = parse_deleted_project_name(tombstone)
    if parsed is None or parsed[0] == project_dir.name:
        return None
    return SessionRekey.of(
        await retired_agent_session_ids(session, tombstone),
        make_project_key(project_dir),
        source_key=make_project_key(project_dir.parent / parsed[0]),
    )


type RetireProject = Callable[[Path], AbstractContextManager[str]]
"""Finalize an existing project in a worker thread, replacing its directory inside the ``with`` block; same semantics as :func:`retiring_project`."""


def retire_project_on(bridge: EventLoopBridge, session_factory: async_sessionmaker[AsyncSession]) -> RetireProject:
    """Retirement for synchronous import: enter/exit ``with`` on the ``bridge`` event loop while the block runs in the caller’s thread.

    Use only from worker threads."""

    @contextmanager
    def _retire(project_dir: Path) -> Generator[str]:
        retirement = retiring_project(session_factory, project_dir)
        tombstone = bridge.run(retirement.__aenter__())
        try:
            yield tombstone
        except BaseException as exc:
            bridge.run(retirement.__aexit__(type(exc), exc, exc.__traceback__))
            raise
        bridge.run(retirement.__aexit__(None, None, None))

    return _retire


__all__ = ["RetireProject", "restore_retired_project", "retire_project_on", "retiring_project"]
