"""用户管理 API — fork 私有

仅 ``admin`` 可访问。本期不提供改密接口（所有用户共享 AUTH_PASSWORD）。

表中不含 AUTH_USERNAME 用户（那个走 env 短路，不入 DB），这里只管理租户用户。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lib.db import async_session_factory
from lib.db.repositories.fork_user_repository import UserRepository
from lib.fork_permissions import (
    ROLE_ADMIN,
    ROLE_USER,
    VALID_ROLES,
    PrincipalNameError,
    validate_principal_name,
)
from server.fork_permissions import AdminUser, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


# ==================== 模型 ====================


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None


class UserCreateBody(BaseModel):
    username: str = Field(..., description="用户名（小写字母数字 + _ -，3-32 位）")
    role: str = Field(default=ROLE_USER, description=f"角色，可选 {sorted(VALID_ROLES)}")


class UserUpdateRoleBody(BaseModel):
    role: str


# ==================== 路由 ====================


@router.get("/users", response_model=list[UserOut])
async def list_users(_user: AdminUser):
    async with async_session_factory() as session:
        async with session.begin():
            rows = await UserRepository(session).list_all()
    return [UserOut(**r) for r in rows]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreateBody, _user: AdminUser):
    try:
        username = validate_principal_name(body.username, kind="username")
    except PrincipalNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role 必须是 {sorted(VALID_ROLES)} 之一")

    async with async_session_factory() as session:
        async with session.begin():
            repo = UserRepository(session)
            existing = await repo.get_by_username(username)
            if existing is not None:
                raise HTTPException(status_code=409, detail=f"用户名 '{username}' 已存在")
            created = await repo.create(username=username, role=body.role, is_active=True)
    logger.info("admin %s 创建用户: %s (role=%s)", _user.sub, username, body.role)
    return UserOut(**created)


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def change_user_role(user_id: str, body: UserUpdateRoleBody, _user: AdminUser):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role 必须是 {sorted(VALID_ROLES)} 之一")

    async with async_session_factory() as session:
        async with session.begin():
            repo = UserRepository(session)
            target = await repo.get_by_id(user_id)
            if target is None:
                raise HTTPException(status_code=404, detail="用户不存在")
            # 安全栏：不允许把自己降级（避免误操作丢失唯一管理入口）
            if target["id"] == _user.id and body.role != ROLE_ADMIN:
                raise HTTPException(status_code=400, detail="不能修改自己的角色")
            await repo.update_role(user_id, body.role)
            updated = await repo.get_by_id(user_id)
    logger.info("admin %s 修改用户角色: %s -> %s", _user.sub, target["username"], body.role)
    return UserOut(**updated)  # type: ignore[arg-type]


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str, _user: AdminUser):
    async with async_session_factory() as session:
        async with session.begin():
            repo = UserRepository(session)
            target = await repo.get_by_id(user_id)
            if target is None:
                raise HTTPException(status_code=404, detail="用户不存在")
            if target["id"] == _user.id:
                raise HTTPException(status_code=400, detail="不能删除自己")
            await repo.delete(user_id)
    logger.info("admin %s 删除用户: %s", _user.sub, target["username"])
    return None
