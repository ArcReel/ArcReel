"""fork-private: admin guard 路径白名单漂移检测。

如果上游 router 把路径改名/删除/迁移，本测试会失败，提醒维护者更新
:mod:`server.fork_admin_guard.ADMIN_PATH_PATTERNS`。
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from server.app import app
from server.fork_admin_guard import ADMIN_PATH_PATTERNS, _is_admin_path


def _all_route_paths() -> list[str]:
    return [r.path for r in app.routes if isinstance(r, APIRoute)]


@pytest.mark.real_admin_guard
class TestAdminGuardCoverage:
    """白名单 → 真实路由 的双向校验。"""

    def test_every_pattern_matches_a_real_route(self):
        paths = _all_route_paths()
        unmatched = [p.pattern for p in ADMIN_PATH_PATTERNS if not any(p.match(rp) for rp in paths)]
        assert not unmatched, (
            f"以下 admin guard 正则没有匹配到任何真实路由（很可能上游已重命名或删除）："
            f" {unmatched}\n请同步更新 server/fork_admin_guard.py::ADMIN_PATH_PATTERNS"
        )

    def test_known_admin_paths_are_guarded(self):
        # 列出本期明确希望落入 admin guard 的样本路径（覆盖每个正则）
        samples = [
            "/api/v1/providers",
            "/api/v1/providers/openai/config",
            "/api/v1/custom-providers",
            "/api/v1/system/config",
            "/api/v1/system/version",
            "/api/v1/api-keys",
            "/api/v1/users",
            "/api/v1/agent/credentials",
            "/api/v1/agent/credentials/1",
            "/api/v1/agent/credentials/1/activate",
            "/api/v1/agent/preset-providers",
            "/api/v1/agent/test-connection",
        ]
        for p in samples:
            assert _is_admin_path(p), f"{p} 应被 admin guard 覆盖"

    def test_normal_paths_not_guarded(self):
        for p in [
            "/api/v1/projects",
            "/api/v1/tasks",
            "/api/v1/auth/token",
            "/api/v1/users-export",  # 边界：不能误命中 /users
            "/api/v1/assets",
            # /agent/chat 是外部 Agent 对话端点，不应被锁成 admin-only
            "/api/v1/agent/chat",
        ]:
            assert not _is_admin_path(p), f"{p} 不应被 admin guard 覆盖"


@pytest.mark.real_admin_guard
class TestAdminGuardEnforcement:
    """端到端：未带 token 命中 admin 路径应 401，非 admin 角色应 403。"""

    def test_no_token_returns_401(self):
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/users")
            assert r.status_code == 401

    def test_non_admin_jwt_returns_403(self, monkeypatch):
        monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-key-that-is-at-least-32-bytes")
        from server import auth as auth_module

        auth_module._cached_token_secret = None
        token = auth_module.create_token("bob", role="user")
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403

    def test_non_admin_can_read_system_config(self, monkeypatch):
        """fork-private: 项目设置页需要 GET /system/config 拿默认模型；写仍 admin。"""
        monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-key-that-is-at-least-32-bytes")
        from server import auth as auth_module

        auth_module._cached_token_secret = None
        token = auth_module.create_token("bob", role="user")
        with TestClient(app, raise_server_exceptions=False) as c:
            # GET 放行（具体业务返回码不关心，只要 != 403）
            r = c.get("/api/v1/system/config", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code != 403
            # PUT 仍被拦
            r = c.put(
                "/api/v1/system/config",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 403

    def test_non_admin_can_list_providers(self, monkeypatch):
        """fork-private: 创建项目对话框 / 配置状态读取需要 GET /providers + /custom-providers。"""
        monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-key-that-is-at-least-32-bytes")
        from server import auth as auth_module

        auth_module._cached_token_secret = None
        token = auth_module.create_token("bob", role="user")
        with TestClient(app, raise_server_exceptions=False) as c:
            for path in ["/api/v1/providers", "/api/v1/custom-providers", "/api/v1/custom-providers/endpoints"]:
                r = c.get(path, headers={"Authorization": f"Bearer {token}"})
                assert r.status_code != 403, f"{path} should be readable by non-admin"
            # 写仍 admin
            r = c.patch(
                "/api/v1/providers/openai/config",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 403
