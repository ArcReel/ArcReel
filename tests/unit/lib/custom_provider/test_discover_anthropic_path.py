"""_discover_anthropic 的请求路径：base_url 原样保留，只在 path 上追加 /v1/models。"""

from __future__ import annotations

import httpx
import pytest

from lib.backends.artifact_download_guard import ArtifactDestinationRejectedError
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

    with capture_http() as router:
        route = router.get(expected).respond(json={"data": [{"id": "claude-x", "display_name": "X"}]})
        models = await _discover_anthropic(base_url, "sk")

    assert route.call_count == 1
    assert str(route.calls[0].request.url) == expected
    assert models[0]["model_id"] == "claude-x"


@pytest.mark.asyncio
async def test_discover_retries_with_bearer_after_401() -> None:
    """只认 Authorization 的网关（火山方舟）：x-api-key 拿 401 后换 Bearer 重试。"""
    from lib.custom_provider.discovery import _discover_anthropic

    with capture_http() as router:
        route = router.get("https://ark.cn-beijing.volces.com/api/plan/v1/models").mock(
            side_effect=[
                httpx.Response(401, text="unauthorized"),
                httpx.Response(200, json={"data": [{"id": "doubao-seed-evolving"}]}),
            ]
        )
        models = await _discover_anthropic("https://ark.cn-beijing.volces.com/api/plan", "sk")

    assert [m["model_id"] for m in models] == ["doubao-seed-evolving"]
    assert route.call_count == 2
    assert route.calls[0].request.headers["x-api-key"] == "sk"
    assert route.calls[1].request.headers["authorization"] == "Bearer sk"
    assert "x-api-key" not in route.calls[1].request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254",
        "http://[fd00:ec2::254]",
        "http://100.100.100.200",
        "http://192.0.0.192",
    ],
)
async def test_discover_refuses_metadata_destinations(base_url: str) -> None:
    """模型发现与产物下载共用同一道出站目的地校验。"""
    from lib.custom_provider.discovery import _discover_anthropic

    with capture_http() as router:
        route = router.get(f"{base_url}/v1/models").respond(json={"data": []})
        with pytest.raises(ArtifactDestinationRejectedError, match="disallowed address"):
            await _discover_anthropic(base_url, "sk")

    assert route.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://127.0.0.1:11434", "http://192.168.24.7:8000"])
async def test_discover_keeps_loopback_and_private_destinations(base_url: str) -> None:
    """自建网关合法地跑在环回与私网上，这道校验不许把它们一并拦掉。"""
    from lib.custom_provider.discovery import _discover_anthropic

    with capture_http() as router:
        route = router.get(f"{base_url}/v1/models").respond(json={"data": [{"id": "claude-x"}]})
        models = await _discover_anthropic(base_url, "sk")

    assert route.call_count == 1
    assert [m["model_id"] for m in models] == ["claude-x"]
