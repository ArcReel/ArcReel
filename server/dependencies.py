"""Shared FastAPI dependency factories."""

from __future__ import annotations

import asyncio

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.project_migration_guard import assert_project_migration_ok

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_config_service(
    session: AsyncSession = Depends(get_async_session),
) -> ConfigService:
    return ConfigService(session)


async def require_project_migration_ok(request: Request) -> None:
    """Refuse mutating calls on a project whose schema migration failed.

    Mounted on the routers that create or change production output, so reading a
    broken project — its scripts, its canvas, the artifacts already generated —
    keeps working while nothing new can be produced from inputs the migration
    itself refused. Enqueue-backed routes are also guarded inside the queue; this
    covers the entries that write or call a provider without queuing a task.
    """

    if request.method in _READ_ONLY_METHODS:
        return
    name = request.path_params.get("project_name") or request.path_params.get("name")
    if not isinstance(name, str) or not name:
        return
    await asyncio.to_thread(assert_project_migration_ok, name)
