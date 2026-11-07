"""
Authentication dependency-injection integration tests.

Verify route authentication after replacing middleware.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server.auth as auth_module


@pytest.fixture(autouse=True)
def auth_env():
    """Fix authentication environment for all tests; clear caches afterward."""
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


@pytest.fixture
def middleware_client():
    """Create clients against the real app."""
    from server.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _login(middleware_client: TestClient) -> str:
    """Log in and return access_token."""
    resp = middleware_client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


class TestAuthIntegration:
    def test_health_no_auth(self, middleware_client):
        """GET /health needs no authentication and returns 200."""
        resp = middleware_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_login_no_auth(self, middleware_client):
        """POST /api/v1/auth/token needs no authentication."""
        resp = middleware_client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_api_without_token(self, middleware_client):
        """GET /api/v1/projects without tokens returns 401."""
        resp = middleware_client.get("/api/v1/projects")
        assert resp.status_code == 401

    def test_api_with_valid_token(self, middleware_client):
        """Login tokens allow API access without 401."""
        token = _login(middleware_client)
        resp = middleware_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 401

    def test_api_with_invalid_token(self, middleware_client):
        """Invalid tokens return 401."""
        resp = middleware_client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer invalid-token-value"},
        )
        assert resp.status_code == 401

    def test_docs_page_accessible(self, middleware_client):
        """/docs Swagger UI remains accessible."""
        resp = middleware_client.get("/docs")
        assert resp.status_code == 200

    def test_frontend_path_no_auth(self, middleware_client):
        """Frontend paths outside /api/ need no authentication."""
        resp = middleware_client.get("/app/projects")
        assert resp.status_code != 401
