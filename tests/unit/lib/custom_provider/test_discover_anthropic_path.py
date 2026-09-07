"""_discover_anthropic 的请求路径：base_url 原样保留，只在 path 上追加 /v1/models。"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from tests.http_capture import capture_http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.minimaxi.com/anthropic", "https://api.minimaxi.com/anthropic/v1/models"),
        ("https://api.deepseek.com/anthropic", "https://api.deepseek.com/anthropic/v1/models"),
        ("https://api.anthropic.com", "https://api.anthropic.com/v1/models"),
        ("https://open.bigmodel.cn/api/anthropic/", "https://open.bigmodel.cn/api/anthropic/v1/models"),
    ],
)
async def test_discover_keeps_subpath(base_url: str, expected: str) -> None:
    from lib.custom_provider.discovery import _discover_anthropic

    async with httpx.AsyncClient() as client:
        with capture_http() as router:
            route = router.get(expected).respond(json={"data": [{"id": "claude-x", "display_name": "X"}]})
            with patch("lib.custom_provider.discovery.get_http_client", return_value=client):
                models = await _discover_anthropic(base_url, "sk")

    assert route.call_count == 1
    assert str(route.calls[0].request.url) == expected
    assert models[0]["model_id"] == "claude-x"


@pytest.mark.asyncio
async def test_discover_retries_with_bearer_after_401() -> None:
    """只认 Authorization 的网关（火山方舟）：x-api-key 拿 401 后换 Bearer 重试。"""
    from lib.custom_provider.discovery import _discover_anthropic

    async with httpx.AsyncClient() as client:
        with capture_http() as router:
            route = router.get("https://ark.cn-beijing.volces.com/api/plan/v1/models").mock(
                side_effect=[
                    httpx.Response(401, text="unauthorized"),
                    httpx.Response(200, json={"data": [{"id": "doubao-seed-evolving"}]}),
                ]
            )
            with patch("lib.custom_provider.discovery.get_http_client", return_value=client):
                models = await _discover_anthropic("https://ark.cn-beijing.volces.com/api/plan", "sk")

    assert [m["model_id"] for m in models] == ["doubao-seed-evolving"]
    assert route.call_count == 2
    assert route.calls[0].request.headers["x-api-key"] == "sk"
    assert route.calls[1].request.headers["authorization"] == "Bearer sk"
    assert "x-api-key" not in route.calls[1].request.headers
