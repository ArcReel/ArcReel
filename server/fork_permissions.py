"""FastAPI 权限依赖 — fork 私有

提供基于角色的依赖：``require_admin`` / ``AdminUser``。
登录态校验仍复用 :mod:`server.auth`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from lib.fork_permissions import is_admin
from server.auth import CurrentUser, CurrentUserInfo


async def require_admin(user: CurrentUser) -> CurrentUserInfo:
    """403 if the caller is not an admin."""
    if not is_admin(user.role):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


AdminUser = Annotated[CurrentUserInfo, Depends(require_admin)]
