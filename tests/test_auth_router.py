"""
登录 API 路由测试

测试 server.routers.auth 中的登录和 token 验证路由。
"""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.auth as auth_module
from server.routers import auth as auth_router


@pytest.fixture()
def client():
    """创建测试客户端，设置固定的认证环境变量"""
    auth_module._cached_token_secret = None
    with patch.dict(
        os.environ,
        {
            "AUTH_USERNAME": "testuser",
            "AUTH_PASSWORD": "testpass",
            "AUTH_TOKEN_SECRET": "test-router-secret-key-at-least-32-bytes-long",
        },
    ):
        app = FastAPI()
        app.include_router(auth_router.router, prefix="/api/v1")
        with TestClient(app) as c:
            yield c


class TestLoginRoute:
    def test_login_success(self, client):
        """正确凭据返回 200 + token"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["username"] == "testuser"
        # token 应该是有效的 JWT
        assert len(data["token"]) > 0

    def test_login_wrong_password(self, client):
        """错误密码返回 401"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    def test_login_wrong_username(self, client):
        """错误用户名返回 401"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "wronguser", "password": "testpass"},
        )
        assert resp.status_code == 401


class TestVerifyRoute:
    def test_verify_valid_token(self, client):
        """有效 token 验证通过"""
        # 先登录获取 token
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        token = login_resp.json()["token"]

        # 验证 token
        resp = client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["username"] == "testuser"

    def test_verify_no_token(self, client):
        """缺少 token 返回 401"""
        resp = client.get("/api/v1/auth/verify")
        assert resp.status_code == 401

    def test_verify_invalid_token(self, client):
        """无效 token 返回 401"""
        resp = client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
