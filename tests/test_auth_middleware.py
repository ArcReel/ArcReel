"""
Authentication dependency injection integration tests

Tests that verify the correct authentication behavior on various paths after middleware replacement.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server.auth as auth_module


@pytest.fixture(autouse=True)
def _auth_env():
    """Set fixed authentication environment variables for all tests, clean up cache after each test."""
    auth_module._cached_token_secret = None
    auth_module._cached_password_hash = None
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
    auth_module._cached_password_hash = None


@pytest.fixture()
def client():
    """Create a test client using the real app."""
    from server.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _login(client: TestClient) -> str:
    """Helper function: log in and return access_token."""
    resp = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


class TestAuthIntegration:
    def test_health_no_auth(self, client):
        """GET /health 不需要认证，返回 200"""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_login_no_auth(self, client):
        """POST /api/v1/auth/token 不需要认证"""
        resp = client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_api_without_token(self, client):
        """GET /api/v1/projects 缺少 token 返回 401"""
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 401

    def test_api_with_valid_token(self, client):
        """先登录获取 token，再带 token 访问 API，不应返回 401"""
        token = _login(client)
        resp = client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 401

    def test_api_with_invalid_token(self, client):
        """带无效 token 访问返回 401"""
        resp = client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer invalid-token-value"},
        )
        assert resp.status_code == 401

    def test_docs_page_accessible(self, client):
        """/docs Swagger UI 应可访问"""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_frontend_path_no_auth(self, client):
        """前端路径（非 /api/ 开头）不需要认证"""
        resp = client.get("/app/projects")
        assert resp.status_code != 401
