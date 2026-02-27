"""
认证中间件集成测试

测试 app.py 中的 auth_middleware 对各类路径的认证行为。
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server.auth as auth_module


@pytest.fixture(autouse=True)
def _auth_env():
    """为所有测试设置固定的认证环境变量，测试结束后清理缓存。"""
    auth_module._cached_token_secret = None
    with patch.dict(
        os.environ,
        {
            "AUTH_USERNAME": "testuser",
            "AUTH_PASSWORD": "testpass",
            "AUTH_TOKEN_SECRET": "test-middleware-secret-key-at-least-32-bytes",
        },
    ):
        yield
    auth_module._cached_token_secret = None


@pytest.fixture()
def client():
    """创建使用真实 app（含认证中间件）的测试客户端。"""
    from server.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _login(client: TestClient) -> str:
    """辅助函数：登录并返回 token。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "testpass"},
    )
    assert resp.status_code == 200
    return resp.json()["token"]


class TestAuthMiddleware:
    def test_health_no_auth(self, client):
        """GET /health 不需要认证，返回 200"""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_login_no_auth(self, client):
        """POST /api/v1/auth/login 不需要认证"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_api_without_token(self, client):
        """GET /api/v1/projects 缺少 token 返回 401"""
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 401
        assert "缺少认证 token" in resp.json()["detail"]

    def test_api_with_valid_token(self, client):
        """先登录获取 token，再带 token 访问 API，不应返回 401"""
        token = _login(client)
        resp = client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
        # 不应该是 401（可能是 200 或其他业务状态码，但不会是认证失败）
        assert resp.status_code != 401

    def test_api_with_invalid_token(self, client):
        """带无效 token 访问返回 401"""
        resp = client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer invalid-token-value"},
        )
        assert resp.status_code == 401
        assert "token 无效或已过期" in resp.json()["detail"]

    def test_sse_with_token_query_param(self, client):
        """SSE 端点通过 ?token=xxx 传递 token，不应返回 401

        使用普通 API 端点测试 query param token，因为认证中间件对所有
        /api/ 路径的 token 提取逻辑相同（SSE 端点会无限流式响应，不适合直接测试）。
        """
        token = _login(client)
        resp = client.get(f"/api/v1/projects?token={token}")
        # 不应该是 401（中间件应接受 query param 中的 token）
        assert resp.status_code != 401

    def test_frontend_path_no_auth(self, client):
        """前端路径（非 /api/ 开头）不需要认证"""
        resp = client.get("/app/projects")
        # 可能返回 200（有前端构建产物）或 503（无构建产物），但不会是 401
        assert resp.status_code != 401

    def test_assets_path_no_auth(self, client):
        """静态资源路径 /assets 不需要认证（白名单）"""
        resp = client.get("/assets/nonexistent.js")
        # 可能 404（文件不存在）或 200，但不会是 401
        assert resp.status_code != 401
