"""Fork-private: 多用户登录认证。

集中放置「不属于上游单用户模型」的认证逻辑，避免侵入 :mod:`server.auth`。
单用户共享 ``AUTH_PASSWORD`` 不变；新增的部分仅在于按 username 查 ``users`` 表
分配租户用户身份。
"""

from __future__ import annotations

import os
import secrets

from server.auth import _get_password_hash, _password_hash


async def authenticate(username: str, password: str) -> dict | None:
    """多用户登录校验。

    返回登录成功的用户 dict（``id`` / ``username`` / ``role``）或 ``None``。

    - **密码**：所有用户共享 ``AUTH_PASSWORD`` 单密码（schema 不变）
    - **AUTH_USERNAME 短路**：``username == AUTH_USERNAME`` 直接合成 admin（不查 DB）
    - **租户用户**：查 ``users`` 表且 ``is_active=True``
    - **时序安全**：无论用户名是否存在都执行哈希校验
    """
    from lib.db.base import DEFAULT_USER_ID

    pw_hash = _get_password_hash()
    password_ok = _password_hash.verify(password, pw_hash)
    if not password_ok:
        return None

    expected_username = os.environ.get("AUTH_USERNAME", "admin")
    if secrets.compare_digest(username, expected_username):
        return {"id": DEFAULT_USER_ID, "username": username, "role": "admin"}

    from lib.db import async_session_factory
    from lib.db.repositories.fork_user_repository import UserRepository

    async with async_session_factory() as session:
        async with session.begin():
            row = await UserRepository(session).get_by_username(username)
    if not row or not row.get("is_active"):
        return None
    return {"id": row["id"], "username": row["username"], "role": row.get("role") or "user"}
