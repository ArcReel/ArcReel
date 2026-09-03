"""Agent 记忆的两根对称 REST 路由：用户记忆与项目记忆。

两级记忆的接口形状完全一致，差别只在记忆目录从哪里派生：用户记忆由
``CurrentUser.id`` 派生（URL 不带 user_id，当前恒为 ``default``），项目记忆由项目
目录派生。两组端点因此共用同一份处理逻辑，只各自提供一个 ``AgentMemoryStore``。

拆成两个 APIRouter 而不是一个：项目记忆的写方法要挂 ``require_project_migration_ok``，
而该依赖从路由的 ``project_name`` 路径参数取项目，挂到不带该参数的用户记忆路由上会
直接抛 RuntimeError。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from lib.agent_memory_paths import project_memory_dir, user_memory_dir
from lib.agent_memory_store import AgentMemoryStore
from lib.api_errors import BadRequestError, NotFoundError
from lib.project_manager import get_project_manager
from server.auth import CurrentUser, CurrentUserInfo

user_router = APIRouter(prefix="/agent/memory")
project_router = APIRouter(prefix="/projects/{project_name}/agent-memory")

#: 记忆正文的媒体类型。写入端不校验 Content-Type：正文是 Markdown 纯文本，
#: 客户端标 text/markdown 还是 text/plain 都不改变落盘内容。
_TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"


def _user_store(user: CurrentUserInfo) -> AgentMemoryStore:
    return AgentMemoryStore(user_memory_dir(get_project_manager().projects_root, user.id))


def _project_store(project_name: str) -> AgentMemoryStore:
    manager = get_project_manager()
    try:
        project_dir = manager.get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return AgentMemoryStore(project_memory_dir(project_dir))


async def _run(build_store: Callable[[], AgentMemoryStore], action: Callable[[AgentMemoryStore], Any]) -> Any:
    """在线程里完成建 store 与磁盘操作：项目目录解析与记忆读写都是阻塞 I/O。"""
    return await asyncio.to_thread(lambda: action(build_store()))


def _text_response(content: bytes) -> Response:
    return Response(content=content, media_type=_TEXT_MEDIA_TYPE)


@user_router.get("")
async def list_user_memory(user: CurrentUser):
    """列出当前用户的记忆目录内容。"""
    return await _run(lambda: _user_store(user), lambda store: store.overview())


@user_router.get("/files/{filename}")
async def read_user_memory_file(filename: str, user: CurrentUser):
    """读取当前用户的单个记忆文件。"""
    return _text_response(await _run(lambda: _user_store(user), lambda store: store.read(filename)))


@user_router.put("/files/{filename}")
async def write_user_memory_file(filename: str, request: Request, user: CurrentUser):
    """幂等写入当前用户的单个记忆文件。"""
    content = await request.body()
    await _run(lambda: _user_store(user), lambda store: store.write(filename, content))
    return {"name": filename}


@user_router.delete("/files/{filename}")
async def delete_user_memory_file(filename: str, user: CurrentUser):
    """删除当前用户的单个记忆文件。"""
    await _run(lambda: _user_store(user), lambda store: store.delete(filename))
    return {"name": filename}


@user_router.post("/clear")
async def clear_user_memory(user: CurrentUser):
    """清空当前用户的整个记忆目录。"""
    await _run(lambda: _user_store(user), lambda store: store.clear())
    return {"cleared": True}


@project_router.get("")
async def list_project_memory(project_name: str):
    """列出项目的记忆目录内容。"""
    return await _run(lambda: _project_store(project_name), lambda store: store.overview())


@project_router.get("/files/{filename}")
async def read_project_memory_file(project_name: str, filename: str):
    """读取项目的单个记忆文件。"""
    return _text_response(await _run(lambda: _project_store(project_name), lambda store: store.read(filename)))


@project_router.put("/files/{filename}")
async def write_project_memory_file(project_name: str, filename: str, request: Request):
    """幂等写入项目的单个记忆文件。"""
    content = await request.body()
    await _run(lambda: _project_store(project_name), lambda store: store.write(filename, content))
    return {"name": filename}


@project_router.delete("/files/{filename}")
async def delete_project_memory_file(project_name: str, filename: str):
    """删除项目的单个记忆文件。"""
    await _run(lambda: _project_store(project_name), lambda store: store.delete(filename))
    return {"name": filename}


@project_router.post("/clear")
async def clear_project_memory(project_name: str):
    """清空项目的整个记忆目录。"""
    await _run(lambda: _project_store(project_name), lambda store: store.clear())
    return {"cleared": True}
