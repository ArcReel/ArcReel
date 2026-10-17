"""注入的 transport 层叠在调用通道原有的 httpx 客户端之上：先改写请求，再由原客户端（含出站目的地校验）发出。"""

from __future__ import annotations

import httpx
import pytest

from lib.backends.artifact_download_guard import ArtifactDestinationRejectedError, artifact_http_client
from lib.backends.transport_layer import layer_async_client, layer_sync_client
from tests.http_capture import PathPrefixRewrite, capture_http, only_request


def test_without_layer_returns_the_original_clients() -> None:
    async_client = httpx.AsyncClient()
    sync_client = httpx.Client()
    assert layer_async_client(async_client, None) is async_client
    assert layer_sync_client(sync_client, None) is sync_client


async def test_async_layer_rewrites_path_and_keeps_client_settings() -> None:
    inner = httpx.AsyncClient(base_url="https://relay.example/v1", headers={"Authorization": "Bearer k"})
    with capture_http() as router:
        route = router.post("https://relay.example/relay/v2/chat").respond(200, json={"ok": True})
        async with layer_async_client(inner, PathPrefixRewrite("/v1/", "/relay/v2/")) as client:
            response = await client.post("/chat", json={})
    assert response.json() == {"ok": True}
    assert only_request(route).headers["Authorization"] == "Bearer k"
    assert inner.is_closed


def test_sync_layer_rewrites_path() -> None:
    with capture_http() as router:
        route = router.get("https://relay.example/relay/tasks/1").respond(200)
        with layer_sync_client(httpx.Client(), PathPrefixRewrite("/api/", "/relay/")) as client:
            client.get("https://relay.example/api/tasks/1")
    assert route.call_count == 1


async def test_destination_guard_checks_the_rewritten_request() -> None:
    layer = PathPrefixRewrite("/", "/")
    with capture_http() as router:
        sent = router.route().respond(200)
        async with artifact_http_client(transport_layer=layer) as client:
            with pytest.raises(ArtifactDestinationRejectedError):
                await client.get("http://169.254.169.254/latest/meta-data")
    assert layer.seen == ["http://169.254.169.254/latest/meta-data"]
    assert sent.call_count == 0


async def test_every_redirect_hop_passes_through_the_layer() -> None:
    layer = PathPrefixRewrite("/old/", "/new/")
    with capture_http() as router:
        router.get("https://relay.example/new/a").respond(302, headers={"Location": "/old/b"})
        final = router.get("https://relay.example/new/b").respond(200)
        async with artifact_http_client(transport_layer=layer, follow_redirects=True) as client:
            await client.get("https://relay.example/old/a")
    assert layer.seen == ["https://relay.example/old/a", "https://relay.example/old/b"]
    assert final.call_count == 1
