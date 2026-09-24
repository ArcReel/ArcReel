"""Shared FastAPI dependency factories."""

from __future__ import annotations

import asyncio

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.infra.api_errors import BadRequestError
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_guard import assert_project_migration_ok

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_config_service(
    session: AsyncSession = Depends(get_async_session),
) -> ConfigService:
    return ConfigService(session)


def _project_name_param(connection: HTTPConnection) -> str | None:
    """The path parameter that names the project, if the matched route has one.

    ``{project_name}`` always names a project. ``{name}`` does only directly under
    ``/projects/``; elsewhere it names something else (a character, a template).
    """

    params = connection.path_params
    if "project_name" in params:
        return params["project_name"]
    route_path = getattr(connection.scope.get("route"), "path", "")
    if "/projects/{name}/" in f"{route_path}/":
        return params.get("name")
    return None


async def require_valid_project_name(connection: HTTPConnection) -> None:
    """Reject a malformed project name before any handler touches it.

    Mounted app-wide, so every route that addresses a project — including ones
    added later — answers a malformed name with 400 instead of failing deeper
    inside. Routes without a project path parameter pass through.
    """

    name = _project_name_param(connection)
    if name is None:
        return
    try:
        ProjectManager.normalize_project_name(name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=name) from exc


async def require_project_migration_ok(request: Request) -> None:
    """Refuse mutating calls on a project whose schema migration failed.

    Mounted on the routers that create or change production output, so reading a
    broken project — its scripts, its canvas, the artifacts already generated —
    keeps working while nothing new can be produced from inputs the migration
    itself refused. Enqueue-backed routes are also guarded inside the queue; this
    covers the entries that write or call a provider without queuing a task.

    The project is taken from the route's own path parameter. A guarded route
    that names its project some other way fails loud rather than slipping
    through unchecked — a silent pass would reopen the entry this guard exists
    to close, and only a mounting mistake can produce it.
    """

    if request.method in _READ_ONLY_METHODS:
        return
    name = request.path_params.get("project_name") or request.path_params.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"require_project_migration_ok 挂在了没有项目路径参数的路由上：{request.url.path}")
    await asyncio.to_thread(assert_project_migration_ok, name)
