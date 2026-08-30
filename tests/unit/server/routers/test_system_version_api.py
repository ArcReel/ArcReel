from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.httpx_shared import shutdown_http_client, startup_http_client
from lib.i18n import get_translator
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
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
    """GitHub releases/latest 的响应体形状。"""
    return {
        "tag_name": f"v{version}",
        "name": version,
        "body": "## What's Changed\n- add about tab",
        "html_url": f"https://github.com/example/ArcReel/releases/tag/v{version}",
        "published_at": "2026-04-21T08:00:00Z",
    }


@pytest.fixture(autouse=True)
def _shared_http_client():
    """生产由 app lifespan 建共享客户端；这里自己建一个，respx 在 transport 层拦它。"""
    asyncio.run(startup_http_client())
    yield
    asyncio.run(shutdown_http_client())


@pytest.fixture(autouse=True)
def _clear_release_cache():
    """`_latest_release_cache` 与 `_read_app_version` 的 lru_cache 都是模块级进程内状态。"""
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
        # checked_at 是实际 fetch 时间，带时区
        assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None

    def test_sends_github_api_headers(self):
        """出站请求形状：GitHub 要求 Accept 与 User-Agent，缺 UA 会被 403。"""
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
        # 信息不再泄漏：返回固定 i18n 文案，详细错误只在日志
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
        """GitHub 真实场景：v0.10.0-rc1 这类 tag 不应触发 update_check_error。"""
        app = _make_app(lambda: "0.10.0")
        with capture_http() as http:
            http.get(_GITHUB_LATEST).respond(json=_release_body("0.10.0-rc1"))
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        # rc1 < 0.10.0 final，不视为新版本
        assert body["has_update"] is False
        assert body["update_check_error"] is None

    def test_invalid_remote_version_does_not_break_endpoint(self):
        """远端 tag 解析失败时退化为 has_update=False，不报错。"""
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
        # 同样不泄漏底层异常文本
        assert "missing version" not in str(body)


class TestLoadAppVersion:
    def test_reads_project_version_from_pyproject(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "arcreel"\nversion = "1.2.3"\n', encoding="utf-8")
        assert system_config._load_app_version(pyproject) == "1.2.3"

    def test_missing_file_propagates(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=r"No such file or directory"):
            system_config._load_app_version(tmp_path / "absent.toml")

    def test_empty_version_is_rejected(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "arcreel"\nversion = "  "\n', encoding="utf-8")
        with pytest.raises(RuntimeError):
            system_config._load_app_version(pyproject)


class TestGetLatestReleaseCache:
    async def test_cache_hit_within_ttl_skips_http_call(self):
        """5 分钟 TTL 内重复调用应只命中 HTTP 一次。"""
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            first = await system_config._get_latest_release()
            second = await system_config._get_latest_release()

        # payload 与 fetched_at 都来自同一次 fetch（缓存命中不重置时间戳）
        assert first == second
        assert route.call_count == 1

    def test_cached_endpoint_response_preserves_fetched_at(self):
        """端到端：连续两次调用 /system/version 时 checked_at 不变（缓存命中）。"""
        app = _make_app()
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            with TestClient(app) as client:
                first = client.get("/api/v1/system/version").json()
                second = client.get("/api/v1/system/version").json()

        assert route.call_count == 1
        assert first["checked_at"] == second["checked_at"]

    async def test_expired_cache_refetches(self):
        """TTL 过期后重新出站，fetched_at 随之前进。"""
        with capture_http(assert_all_called=True) as http:
            route = http.get(_GITHUB_LATEST).respond(json=_release_body("0.9.1"))
            _first, first_at = await system_config._get_latest_release(now=datetime(2026, 4, 21, 8, 0, tzinfo=UTC))
            _second, second_at = await system_config._get_latest_release(now=datetime(2026, 4, 21, 9, 0, tzinfo=UTC))

        assert route.call_count == 2
        assert second_at > first_at

    async def test_http_error_is_not_cached(self):
        """失败响应不写缓存，下一次调用仍然出站重试。"""
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
