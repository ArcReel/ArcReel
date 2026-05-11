"""FastAPI 权限依赖 — fork 私有

提供基于角色的依赖：``require_admin`` / ``AdminUser``、``require_project_access``。
登录态校验仍复用 :mod:`server.auth`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from lib.fork_permissions import can_access_project, is_admin
from server.auth import CurrentUser, CurrentUserFlexible, CurrentUserInfo


async def require_admin(user: CurrentUser) -> CurrentUserInfo:
    """403 if the caller is not an admin."""
    if not is_admin(user.role):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


AdminUser = Annotated[CurrentUserInfo, Depends(require_admin)]


# 项目类路由的 path param 名称在上游各 router 不一致：
# - projects.py 用 ``name``
# - generate.py / files.py / versions.py / tasks.py / 资产工厂 / grids 等用 ``project_name``
# 这里按这两个字面量 fallback 取，缺失视为非项目作用域端点（如全局列表 / 全局
# 资产 / 任务 stats），直接放行；列表端点的 owner 过滤由 handler 自己处理。
_PROJECT_PATH_PARAM_KEYS = ("name", "project_name")


async def require_project_access(request: Request, user: CurrentUser) -> CurrentUserInfo:
    """403 if the caller cannot access the project named in path params.

    若路径里不含项目名（如 `/projects` 列表、`/global-assets/...`、`/tasks/stats`），
    则放行；handler 内部按需做角色过滤。
    """
    project_name: str | None = None
    for key in _PROJECT_PATH_PARAM_KEYS:
        candidate = request.path_params.get(key)
        if candidate:
            project_name = candidate
            break
    if project_name is None:
        return user
    if not can_access_project(user.role, user.sub, project_name):
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return user


# SSE / 浏览器原生下载兼容：依赖 CurrentUserFlexible（支持 ?token= query），
# 适用于 project_events 这种 EventSource 客户端不能加 Authorization header 的场景。
async def require_project_access_flexible(
    request: Request,
    user: CurrentUserFlexible,
) -> CurrentUserInfo:
    project_name: str | None = None
    for key in _PROJECT_PATH_PARAM_KEYS:
        candidate = request.path_params.get(key)
        if candidate:
            project_name = candidate
            break
    if project_name is None:
        return user
    if not can_access_project(user.role, user.sub, project_name):
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return user
