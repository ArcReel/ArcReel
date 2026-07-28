from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n import get_translator
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
from server.routers import system_config
from server.routers.system_config import _parse_version
from tests.conftest import make_translator

_FIXED_FETCHED_AT = datetime(2026, 4, 21, 8, 5, 0, tzinfo=UTC)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.dependency_overrides[get_config_service] = lambda: MagicMock()
    app.dependency_overrides[get_translator] = lambda: make_translator()
    app.include_router(system_config.router, prefix="/api/v1")
    return app


def _release(version: str) -> dict[str, str]:
    """构造 _get_latest_release 处理后的 payload（带 version 字段）。"""
    return {
        "version": version,
        "tag_name": f"v{version}",
        "name": version,
        "body": "## What's Changed\n- add about tab",
        "html_url": f"https://github.com/example/ArcReel/releases/tag/v{version}",
        "published_at": "2026-04-21T08:00:00Z",
    }


def _release_tuple(version: str) -> tuple[dict[str, str], datetime]:
    return _release(version), _FIXED_FETCHED_AT


def _reset_cache() -> None:
    system_config._latest_release_cache["expires_at"] = None
    system_config._latest_release_cache["payload"] = None
    system_config._latest_release_cache["fetched_at"] = None


class TestSystemVersionApi:
    def test_returns_current_and_latest_release(self):
        app = _make_app()
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch(
                "server.routers.system_config._get_latest_release",
                new=AsyncMock(return_value=_release_tuple("0.9.1")),
            ),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["current"]["version"] == "0.9.0"
        assert body["latest"]["version"] == "0.9.1"
        assert body["has_update"] is True
        assert body["update_check_error"] is None
        # checked_at 应反映实际 fetch 时间，而不是请求时间
        assert body["checked_at"] == _FIXED_FETCHED_AT.isoformat()

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
        # 信息不再泄漏：返回固定 i18n 文案，详细错误只在日志
        assert body["update_check_error"] == "检查更新失败，请稍后重试"
        assert "boom" not in body["update_check_error"]

    def test_handles_v_prefixed_tag_as_semver(self):
        app = _make_app()
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch(
                "server.routers.system_config._get_latest_release",
                new=AsyncMock(return_value=_release_tuple("0.9.0")),
            ),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["has_update"] is False
        assert body["update_check_error"] is None

    def test_handles_prerelease_tag_without_error(self):
        """GitHub 真实场景：v0.10.0-rc1 这类 tag 不应触发 update_check_error。"""
        app = _make_app()
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.10.0"),
            patch(
                "server.routers.system_config._get_latest_release",
                new=AsyncMock(return_value=_release_tuple("0.10.0-rc1")),
            ),
        ):
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
        broken_payload = {
            "version": "not-a-version",
            "tag_name": "weird",
            "name": "weird",
            "body": "",
            "html_url": "",
            "published_at": "",
        }
        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch(
                "server.routers.system_config._get_latest_release",
                new=AsyncMock(return_value=(broken_payload, _FIXED_FETCHED_AT)),
            ),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 200
        body = resp.json()
        assert body["has_update"] is False
        assert body["update_check_error"] is None

    def test_returns_500_when_local_version_cannot_be_read(self):
        app = _make_app()
        # _read_app_version 被 lru_cache 装饰，patch 会替换整个属性
        # 但需要保险起见 clear cache，避免之前测试 populate
        system_config._read_app_version.cache_clear()
        with patch("server.routers.system_config._read_app_version", side_effect=RuntimeError("missing version")):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/api/v1/system/version")

        assert resp.status_code == 500
        body = resp.json()
        # 同样不泄漏底层异常文本
        assert "missing version" not in str(body)


class TestGetLatestReleaseCache:
    def test_cache_hit_within_ttl_skips_http_call(self):
        """5 分钟 TTL 内重复调用应只命中 HTTP 一次。"""
        _reset_cache()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tag_name": "v0.9.1",
            "name": "0.9.1",
            "body": "",
            "html_url": "",
            "published_at": "",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            with patch("server.routers.system_config.get_http_client", return_value=mock_client):
                a = await system_config._get_latest_release()
                b = await system_config._get_latest_release()
            return a, b

        import asyncio

        a, b = asyncio.run(run())
        # payload 与 fetched_at 都来自同一次 fetch
        assert a == b
        assert a[1] == b[1]  # fetched_at 不变（关键：缓存命中不重置时间戳）
        assert mock_client.get.await_count == 1

    def test_cached_endpoint_response_preserves_fetched_at(self):
        """端到端：连续两次调用 /system/version 时 checked_at 不变（缓存命中）。"""
        _reset_cache()
        app = _make_app()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tag_name": "v0.9.1",
            "name": "0.9.1",
            "body": "",
            "html_url": "",
            "published_at": "",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with (
            patch("server.routers.system_config._read_app_version", return_value="0.9.0"),
            patch("server.routers.system_config.get_http_client", return_value=mock_client),
        ):
            with TestClient(app) as client:
                first = client.get("/api/v1/system/version").json()
                second = client.get("/api/v1/system/version").json()

        assert mock_client.get.await_count == 1
        assert first["checked_at"] == second["checked_at"]


class TestParseVersion:
    @pytest.mark.parametrize(
        "raw,is_valid",
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
