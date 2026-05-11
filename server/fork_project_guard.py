"""fork-private: 项目级访问守卫 + owner 前缀注入。

设计目的：把"项目目录扁平 ``<owner>__<project>`` 命名 + admin/user 项目隔离"的
约束实现在 fork 私有文件里，**完全不修改上游 router / handler / project_manager 代码**。

工作方式：
- :func:`install_project_guards` 在所有 ``app.include_router`` 之后调用一次：
  1. 遍历 ``app.routes``，按 path param（``name`` / ``project_name``）识别项目
     级路由，按需把 :func:`require_project_access`（或 ``_flexible`` 变体）注入到
     route 的 dependants 链 — 这跟 router 启动时手写 ``dependencies=[...]`` 等
     效，但代码侵入归零。
  2. 注册一个 HTTP middleware：
     - ``GET /api/v1/projects`` 列表响应按 role 过滤 + 注入 ``owner`` 字段。
     - ``POST /api/v1/projects`` 请求 body 注入 ``<owner>__`` 前缀。
- ``/export``、``/export/jianying-draft`` 浏览器原生下载用 download_token 自带项目
  绑定，跳过 dep 注入和 middleware。
- ``<owner>__<project>`` 形式的项目名校验在 ``lib/project_manager.py`` 的
  ``PROJECT_NAME_PATTERN`` 内放宽（skill 脚本子进程也要走该校验，monkey-patch
  无法跨进程，详见该模块注释）。

漂移风险：
- 上游若改 path param 名（如把 ``project_name`` 改成 ``project``）→
  :mod:`tests.test_fork_project_guard` 中的 route 计数会断言失败，从而显式提醒
  维护者更新本文件。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response

from lib.fork_permissions import is_admin, make_project_name, parse_project_name
from server.fork_permissions import require_project_access, require_project_access_flexible

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 注入到路由 dep 链的判定
# ---------------------------------------------------------------------------

# path param 名 — 上游 router 在 ``{...}`` 里有这两个字面量之一即视为项目级路由
_PROJECT_PARAMS: tuple[str, ...] = ("name", "project_name")

# 跳过 dep 注入：浏览器原生下载，已用 download_token 自校验
_SKIP_PATH_RE: tuple[re.Pattern[str], ...] = (re.compile(r"^/api/v1/projects/\{name\}/export(?:/.*)?$"),)

# 静态资源 ``/files/{project_name}/...`` 走 ``<img>`` 标签，浏览器无法附加 Bearer。
# 跟随上游策略：不做强鉴权（路径不可枚举即为"准认证"）。
_FILE_PATH_RE: re.Pattern[str] = re.compile(r"^/api/v1/files/\{project_name\}/")

# SSE / EventSource 类路径用 flexible（支持 ``?token=`` 兜底）
_FLEXIBLE_PATH_RE: re.Pattern[str] = re.compile(r"/stream$|/events/stream$")


def _route_has_project_param(route: APIRoute) -> bool:
    return any(f"{{{p}}}" in route.path_format for p in _PROJECT_PARAMS)


def _should_skip(route: APIRoute) -> bool:
    if _FILE_PATH_RE.search(route.path_format):
        return True
    return any(p.match(route.path_format) for p in _SKIP_PATH_RE)


def _attach_dep(route: APIRoute) -> None:
    """把 require_project_access* 注入到指定 route 的 dependant 链。"""
    is_flexible = bool(_FLEXIBLE_PATH_RE.search(route.path_format))
    dep_func = require_project_access_flexible if is_flexible else require_project_access
    depends = Depends(dep_func)
    # 同时塞进 route.dependencies（用于 __repr__ / OpenAPI），并把 sub_dependant
    # 加入解析链头部，保证比 handler 自身参数更早执行。
    route.dependencies.append(depends)
    route.dependant.dependencies.insert(
        0,
        get_parameterless_sub_dependant(depends=depends, path=route.path_format),
    )


# ---------------------------------------------------------------------------
# Middleware：列表过滤 + 创建前缀
# ---------------------------------------------------------------------------

_PROJECTS_PATH = "/api/v1/projects"


async def _resolve_user(request: Request) -> tuple[str, str] | None:
    """从 Authorization header 解析 (sub, role)；失败返回 None。"""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        from server.auth import _verify_and_get_payload_async

        payload = await _verify_and_get_payload_async(token)
    except Exception:
        return None
    sub = payload.get("sub", "")
    role = "user" if payload.get("via") == "apikey" else (payload.get("role") or "")
    if not sub:
        return None
    return sub, role


async def _rewrite_create_body(request: Request) -> None:
    """``POST /api/v1/projects``：把 body 里的 ``name`` 前缀为 ``<owner>__``。"""
    user = await _resolve_user(request)
    if user is None:
        return
    sub, _ = user
    try:
        body = await request.body()
        data: dict[str, Any] = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return

    title = (data.get("title") or "").strip()
    manual_name = (data.get("name") or "").strip()
    if not title and not manual_name:
        return  # 让上游 handler 自己返回校验错误

    if manual_name:
        base = manual_name
    else:
        # 没有手填名时，预先生成 slug，再加前缀；handler 拿到 name 后会跳过自动生成。
        from lib import PROJECT_ROOT
        from lib.project_manager import ProjectManager

        base = ProjectManager(PROJECT_ROOT / "projects").generate_project_name(title)

    existing_owner, _ = parse_project_name(base)
    project_name = base if existing_owner == sub else make_project_name(sub, base)
    data["name"] = project_name
    new_body = json.dumps(data).encode("utf-8")

    # Starlette ``Request.body()`` 在首次读取后会缓存到 ``_body``；
    # 后续 FastAPI 解析 body 时复用这一缓存值，因此直接覆写即可。
    request._body = new_body  # type: ignore[attr-defined]

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": new_body, "more_body": False}

    request._receive = _receive  # type: ignore[attr-defined]
    # 同步更新 content-length，避免下游 body parser 误用旧值。
    headers = [(k, v) for k, v in request.scope.get("headers", []) if k != b"content-length"]
    headers.append((b"content-length", str(len(new_body)).encode("latin-1")))
    request.scope["headers"] = headers


async def _filter_list_response(request: Request, response: Response) -> Response:
    """``GET /api/v1/projects``：按 role 过滤 + 注入 ``owner``。"""
    if response.status_code != 200:
        return response
    user = await _resolve_user(request)
    if user is None:
        return response
    sub, role = user
    viewer_admin = is_admin(role)

    # 读完原始 body
    body_iter = getattr(response, "body_iterator", None)
    if body_iter is None:
        raw = getattr(response, "body", b"")
    else:
        chunks: list[bytes] = []
        async for chunk in body_iter:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        raw = b"".join(chunks)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(
            content=raw,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        return Response(
            content=raw,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    filtered = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        owner, _ = parse_project_name(item.get("name", ""))
        item["owner"] = owner
        if not viewer_admin and owner != sub:
            continue
        filtered.append(item)
    data["projects"] = filtered

    new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(
        content=new_body,
        status_code=response.status_code,
        headers=headers,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def install_project_guards(app: FastAPI) -> None:
    """挂载项目级访问守卫到 ``app``。幂等。"""
    if getattr(app.state, "_fork_project_guard_installed", False):
        return
    app.state._fork_project_guard_installed = True

    attached = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not _route_has_project_param(route):
            continue
        if _should_skip(route):
            continue
        _attach_dep(route)
        attached += 1
    logger.info("fork project guard 已挂载到 %d 条项目级路由", attached)

    @app.middleware("http")
    async def _list_create_middleware(  # noqa: ARG001 (FastAPI middleware signature)
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == _PROJECTS_PATH:
            if request.method == "POST":
                await _rewrite_create_body(request)
                return await call_next(request)
            if request.method == "GET":
                response = await call_next(request)
                return await _filter_list_response(request, response)
        return await call_next(request)


__all__ = ["install_project_guards"]
