from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.app_version import load_app_version
from lib.infra.httpx_shared import shutdown_http_client, startup_http_client
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
from server.i18n import get_translator
from server.routers import system_config
from server.routers.system_config import _parse_version
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import make_translator
from tests.http_capture import capture_http

_GITHUB_LATEST = "https://api.github.com/repos/ArcReel/ArcReel/releases/latest"


def _make_app(version_reader: Callable[[], str] = lambda: "0.9.0") -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.dependency_overrides[get_config_service] = lambda: MagicMock()
    app.dependency_overrides[get_translator] = lambda: make_translator()
    app.dependency_overrides[system_config.get_app_version_reader] = lambda: version_reader
    app.include_router(system_config.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


def _release_body(version: str) -> dict[str, str]:
    """GitHub releases/latest response-body shape."""
    return {
        "tag_name": f"v{version}",
        "name": version,
        "body": "## What's Changed\n- add about tab",
        "html_url": f"https://github.com/example/ArcReel/releases/tag/v{version}",
        "published_at": "2026-04-21T08:00:00Z",
    }


@pytest.fixture(autouse=True)
def shared_http_client():
    """Production app lifespan creates shared clients; create one here for respx transport interception."""
    asyncio.run(startup_http_client())
    yield
    asyncio.run(shutdown_http_client())


@pytest.fixture(autouse=True)
def clear_release_cache():
    """`_latest_release_cache` and `_read_app_version` lru_cache are module-level process-local state."""
    system_config._latest_release_cache.update({"expires_at": None, "payload": None, "fetched_at": None})
    system_config._read_app_version.cache_clear()
    yield
    system_config._latest_release_cache.update({"expires_at": None, "payload": None, "fetched_at": None})
    system_config._read_app_version.cache_clear()


class TestSystemVersionApi:
    def test_returns_current_and_latest_release(self):
        app = _make_app()
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["current"]["version"] == "0.9.0"
        assert body["latest"]["version"] == "0.9.1"
        assert body["latest"]["tag_name"] == "v0.9.1"
        assert body["has_update"] is True
        assert body["update_check_error"] is None
        # checked_at is actual timezone-aware fetch time.
        assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None

    def test_sends_github_api_headers(self):
        """Outbound shape: GitHub requires Accept/User-Agent; missing UA yields 403."""
        app = _make_app()
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            with TestClient(app) as client:
                client.get("/api/v1/system/version")

        request = route.calls.last.request
        assert request.headers["accept"] == "application/vnd.github+json"
        assert request.headers["user-agent"] == "ArcReel"

    def test_returns_current_version_when_github_check_fails(self):
        app = _make_app()
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(status_code=502)
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["current"]["version"] == "0.9.0"
        assert body["latest"] is None
        assert body["has_update"] is False
        # No information leaks: fixed localized text returned, detailed errors logged only.
        assert body["update_check_error"] == "检查更新失败，请稍后重试"
        assert "502" not in body["update_check_error"]

    def test_handles_v_prefixed_tag_as_semver(self):
        app = _make_app()
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.0"))
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["latest"]["version"] == "0.9.0"
        assert body["has_update"] is False
        assert body["update_check_error"] is None

    def test_handles_prerelease_tag_without_error(self):
        """Real GitHub case: tags such as v0.10.0-rc1 must not trigger update_check_error."""
        app = _make_app(lambda: "0.10.0")
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(json=_release_body("0.10.0-rc1"))
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        # rc1 < 0.10.0 final, never considered newer.
        assert body["has_update"] is False
        assert body["update_check_error"] is None

    def test_invalid_remote_version_does_not_break_endpoint(self):
        """Unparseable remote tags degrade to has_update=False without errors."""
        app = _make_app()
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(json={"tag_name": "weird", "name": "weird"})
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["latest"]["version"] == "weird"
        assert body["has_update"] is False
        assert body["update_check_error"] is None

    def test_returns_500_when_local_version_cannot_be_read(self):
        def _unreadable() -> str:
            raise RuntimeError("missing version")

        app = _make_app(_unreadable)
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 500
        body = resp.json()
        # Likewise never leak underlying exception text.
        assert "missing version" not in str(body)


class TestLoadAppVersion:
    def test_reads_project_version_from_pyproject(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "arcreel"\nversion = "1.2.3"\n', encoding="utf-8")
        assert load_app_version(pyproject) == "1.2.3"

    def test_missing_file_propagates(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=r"No such file or directory"):
            load_app_version(tmp_path / "absent.toml")

    def test_empty_version_is_rejected(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "arcreel"\nversion = "  "\n', encoding="utf-8")
        with pytest.raises(RuntimeError):
            load_app_version(pyproject)


class TestGetLatestReleaseCache:
    async def test_cache_hit_within_ttl_skips_http_call(self):
        """Repeated calls within five-minute TTL hit HTTP once only."""
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            first = await system_config._get_latest_release()
            second = await system_config._get_latest_release()

        # payload and fetched_at come from one fetch; cache hits never reset timestamps.
        assert first == second
        assert route.call_count == 1

    def test_cached_endpoint_response_preserves_fetched_at(self):
        """End to end: two /system/version calls retain checked_at on cache hits."""
        app = _make_app()
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            with TestClient(app) as client:
                first = client.get("/api/v1/system/version").json()
                second = client.get("/api/v1/system/version").json()

        assert route.call_count == 1
        assert first["checked_at"] == second["checked_at"]

    async def test_expired_cache_refetches(self):
        """Expired TTL fetches outbound again, advancing fetched_at."""
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            _first, first_at = await system_config._get_latest_release(now=datetime(2026, 4, 21, 8, 0, tzinfo=UTC))
            _second, second_at = await system_config._get_latest_release(now=datetime(2026, 4, 21, 9, 0, tzinfo=UTC))

        assert route.call_count == 2
        assert second_at > first_at

    async def test_status_error_message_drops_query_credentials(self):
        """4xx raises redacted HTTPStatusError: request URL query strings never enter exception messages.

        Redirect-following clients send failures to downstream signed-query URLs, constructing failed requests whose
        URLs contain credentials. Bare raise_for_status includes whole URLs in messages, then endpoints record warning logs.
        """
        leaky = "https://objects.githubusercontent.com/release?api_key=sk-leaked-gh"
        with capture_http(assert_all_called=True) as http:
            http.get(_GITHUB_LATEST).respond(status_code=302, headers={"Location": leaky})
            http.get(url__startswith="https://objects.githubusercontent.com/release").respond(status_code=401)
            async with httpx.AsyncClient(follow_redirects=True) as client:
                with pytest.raises(httpx.HTTPStatusError) as exc_info:
                    await system_config._get_latest_release(http_client=client)

        assert "sk-leaked-gh" in str(exc_info.value.request.url)
        assert str(exc_info.value) == "401 response for https://objects.githubusercontent.com/release"
        assert exc_info.value.response.status_code == 401

    async def test_http_error_is_not_cached(self):
        """Failed responses never cache; next calls retry outbound."""
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(status_code=500)
            for _ in range(2):
                with pytest.raises(httpx.HTTPStatusError):
                    await system_config._get_latest_release()

        assert route.call_count == 2


class TestParseVersion:
    @pytest.mark.parametrize(
        ("raw", "is_valid"),
        [
            ("0.9.0", True),
            ("v0.10.0", True),
            ("0.10.0", True),
            ("v1.0.0a1", True),
            ("0.10.0-rc1", True),
            ("0.10.0.post1", True),
            ("invalid", False),
            ("", False),
            ("v", False),
        ],
    )
    def test_accepts_realistic_tags(self, raw: str, is_valid: bool):
        result = _parse_version(raw)
        if is_valid:
            assert result is not None, f"expected {raw!r} to parse"
        else:
            assert result is None, f"expected {raw!r} to be rejected"

    def test_orders_versions_correctly(self):
        assert _parse_version("0.10.0") > _parse_version("0.9.9")
        assert _parse_version("v0.10.0") == _parse_version("0.10.0")
        assert _parse_version("0.10.0-rc1") < _parse_version("0.10.0")
