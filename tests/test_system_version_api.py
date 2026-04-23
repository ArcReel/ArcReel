from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n import get_translator
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
from server.routers import system_config
from tests.conftest import make_translator


def _make_app() -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.dependency_overrides[get_config_service] = lambda: MagicMock()
    app.dependency_overrides[get_translator] = lambda: make_translator()
    app.include_router(system_config.router, prefix="/api/v1")
    return app


class TestSystemVersionApi:
    def test_returns_current_and_latest_release(self):
        app = _make_app()
        release_payload = {
            "tag_name": "v0.9.1",
            "name": "0.9.1",
            "body": "## What's Changed\n- add about tab",
            "html_url": "https://github.com/example/ArcReel/releases/tag/v0.9.1",
            "published_at": "2026-04-21T08:00:00Z",
        }
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch("server.routers.system_config._get_latest_release", new=AsyncMock(return_value=release_payload)),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["current"]["version"] == "0.9.0"
        assert body["latest"]["version"] == "0.9.1"
        assert body["has_update"] is True
        assert body["update_check_error"] is None

    def test_returns_current_version_when_github_check_fails(self):
        app = _make_app()
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch("server.routers.system_config._get_latest_release", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["current"]["version"] == "0.9.0"
        assert body["latest"] is None
        assert body["has_update"] is False
        assert body["update_check_error"] == "boom"

    def test_handles_v_prefixed_tag_as_semver(self):
        app = _make_app()
        release_payload = {
            "tag_name": "v0.9.0",
            "name": "0.9.0",
            "body": "same version",
            "html_url": "https://github.com/example/ArcReel/releases/tag/v0.9.0",
            "published_at": "2026-04-21T08:00:00Z",
        }
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch("server.routers.system_config._get_latest_release", new=AsyncMock(return_value=release_payload)),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        assert resp.json()["has_update"] is False

    def test_returns_500_when_local_version_cannot_be_read(self):
        app = _make_app()
        with patch("server.routers.system_config._read_app_version", side_effect=RuntimeError("missing version")):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 500
