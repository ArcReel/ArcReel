"""Two symmetric REST route groups for Agent memory: user memory and project memory.

Both have identical interfaces, differing only in directory derivation: user memory uses ``CurrentUser.id``
(no user_id in the URL, currently always ``default``), while project memory uses the project directory.
Share handlers between both groups, each supplying its own ``AgentMemoryStore``.

Use two APIRouter instances: project-memory writes require ``require_project_migration_ok``, which resolves projects
from the ``project_name`` path parameter. Attaching it to user-memory routes without that parameter would raise RuntimeError."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from lib.agent.agent_memory_store import AgentMemoryStore
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser, CurrentUserInfo

user_router = APIRouter(prefix="/agent/memory")
project_router = APIRouter(prefix="/projects/{project_name}/agent-memory")

#: Memory-body media type. Writes do not validate Content-Type: the body is plain Markdown text,
#: and text/markdown versus text/plain does not affect persisted content.
_TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"


def _user_store(user: CurrentUserInfo) -> AgentMemoryStore:
    return AgentMemoryStore(get_project_manager().layout.user_memory_dir(user.id))


def _project_store(project_name: str) -> AgentMemoryStore:
    manager = get_project_manager()
    try:
        project_dir = manager.get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return AgentMemoryStore.for_project(project_dir)


async def _run(build_store: Callable[[], AgentMemoryStore], action: Callable[[AgentMemoryStore], Any]) -> Any:
    """Construct stores and perform disk operations in a thread: project-directory resolution and memory reads/writes are blocking I/O."""
    return await run_sync_transaction(lambda: action(build_store()))


def _text_response(content: bytes) -> Response:
    return Response(content=content, media_type=_TEXT_MEDIA_TYPE)


@user_router.get("")
async def list_user_memory(user: CurrentUser):
    """List the current user’s memory-directory contents."""
    return await _run(lambda: _user_store(user), lambda store: store.overview())


@user_router.get("/files/{filename}")
async def read_user_memory_file(filename: str, user: CurrentUser):
    """Read one memory file for the current user."""
    return _text_response(await _run(lambda: _user_store(user), lambda store: store.read(filename)))


@user_router.put("/files/{filename}")
async def write_user_memory_file(filename: str, request: Request, user: CurrentUser):
    """Idempotently write one memory file for the current user."""
    content = await request.body()
    await _run(lambda: _user_store(user), lambda store: store.write(filename, content))
    return {"name": filename}


@user_router.delete("/files/{filename}")
async def delete_user_memory_file(filename: str, user: CurrentUser):
    """Delete one memory file for the current user."""
    await _run(lambda: _user_store(user), lambda store: store.delete(filename))
    return {"name": filename}


@user_router.post("/clear")
async def clear_user_memory(user: CurrentUser):
    """Clear the current user’s entire memory directory."""
    await _run(lambda: _user_store(user), lambda store: store.clear())
    return {"cleared": True}


@project_router.get("")
async def list_project_memory(project_name: str):
    """List the project’s memory-directory contents."""
    return await _run(lambda: _project_store(project_name), lambda store: store.overview())


@project_router.get("/files/{filename}")
async def read_project_memory_file(project_name: str, filename: str):
    """Read one project-memory file."""
    return _text_response(await _run(lambda: _project_store(project_name), lambda store: store.read(filename)))


@project_router.put("/files/{filename}")
async def write_project_memory_file(project_name: str, filename: str, request: Request):
    """Idempotently write one project-memory file."""
    content = await request.body()
    await _run(lambda: _project_store(project_name), lambda store: store.write(filename, content))
    return {"name": filename}


@project_router.delete("/files/{filename}")
async def delete_project_memory_file(project_name: str, filename: str):
    """Delete one project-memory file."""
    await _run(lambda: _project_store(project_name), lambda store: store.delete(filename))
    return {"name": filename}


@project_router.post("/clear")
async def clear_project_memory(project_name: str):
    """Clear the project’s entire memory directory."""
    await _run(lambda: _project_store(project_name), lambda store: store.clear())
    return {"cleared": True}
