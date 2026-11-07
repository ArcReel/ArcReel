"""
Login API route tests.

Cover server.routers.auth login/token-verification routes.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.auth as auth_module
from server.routers import auth as auth_router
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
def auth_router_client():
    """Create clients with fixed authentication environment."""
    auth_module._cached_token_secret = None
    auth_module._cached_password_hash = None
    with patch.dict(
        os.environ,
        {
            "AUTH_USERNAME": "testuser",
            "AUTH_PASSWORD": "testpass",
            "AUTH_TOKEN_SECRET": "test-router-secret-key-at-least-32-bytes-long",
        },
    ):
        app = FastAPI()
        app.include_router(auth_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        app.include_router(auth_router.public_router, prefix="/api/v1")
        with TestClient(app) as c:
            yield c


class TestLoginRoute:
    def test_login_success(self, auth_router_client):
        """Correct credentials return 200 + access_token."""
        resp = auth_router_client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert len(data["access_token"]) > 0

    def test_login_wrong_password(self, auth_router_client):
        """Wrong passwords return 401."""
        resp = auth_router_client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    def test_login_wrong_username(self, auth_router_client):
        """Wrong usernames return 401."""
        resp = auth_router_client.post(
            "/api/v1/auth/token",
            data={"username": "wronguser", "password": "testpass"},
        )
        assert resp.status_code == 401


class TestVerifyRoute:
    def test_verify_valid_token(self, auth_router_client):
        """Valid tokens verify."""
        login_resp = auth_router_client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "testpass"},
        )
        token = login_resp.json()["access_token"]

        resp = auth_router_client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["username"] == "testuser"

    def test_verify_no_token(self, auth_router_client):
        """Missing tokens return 401."""
        resp = auth_router_client.get("/api/v1/auth/verify")
        assert resp.status_code == 401

    def test_verify_invalid_token(self, auth_router_client):
        """Invalid tokens return 401."""
        resp = auth_router_client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
