"""AdminUser 依赖访问控制测试 — fork 私有."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.fork_permissions import (
    PrincipalNameError,
    is_admin,
    validate_principal_name,
)
from server.auth import CurrentUserInfo, get_current_user
from server.fork_permissions import require_admin

# 本文件需要验证 require_admin 真实行为，退出 conftest 里的 autouse 短路
pytestmark = pytest.mark.real_admin_guard


def _build_app():
    from server.routers import fork_users as users_router

    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1")
    return app


class TestAdminGuard:
    """普通用户访问 admin-only 路由应得 403。"""

    def test_user_role_403(self):
        app = _build_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="bob", role="user")
        client = TestClient(app)
        assert client.get("/api/v1/users").status_code == 403

    def test_admin_role_passes_guard(self):
        """admin 通过守卫；用一个 _probe 路由仅断言依赖放行（避免触达 DB）。"""
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="admin", role="admin")

        @app.get("/_probe")
        async def _probe(_user=Depends(require_admin)):
            return {"ok": True}

        client = TestClient(app)
        assert client.get("/_probe").status_code == 200


class TestPermissionsHelpers:
    def test_is_admin(self):
        assert is_admin("admin") is True
        assert is_admin("user") is False
        assert is_admin(None) is False

    def test_validate_principal_name_ok(self):
        assert validate_principal_name("bob") == "bob"
        assert validate_principal_name("alice-1") == "alice-1"
        assert validate_principal_name("foo_bar") == "foo_bar"

    def test_validate_principal_name_rejects(self):
        cases = ["Bob", "ab", "x" * 33, "ali__ce", "admin", "user"]
        for name in cases:
            with pytest.raises(PrincipalNameError):
                validate_principal_name(name)
