"""供应商产物下载入口：出站目的地校验与有上限的响应体读取。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx
import pytest

from lib.backends.artifact_download_guard import (
    ArtifactDestinationRejectedError,
    ArtifactTooLargeError,
    artifact_http_client,
    buffered_error_response,
    read_body_capped,
    stream_body_to_file,
)
from tests.http_capture import capture_http


def _resolver_returning(*addresses: str) -> Callable[[str, int], object]:
    async def resolve(host: str, port: int) -> Iterable[str]:
        return addresses

    return resolve


def _partial(output: Path) -> Path:
    return output.with_name(f"{output.name}.part")


async def _unresolvable(host: str, port: int) -> Iterable[str]:
    raise OSError("name or service not known")


class TestDestinationGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://169.254.1.1/a.mp4",
            "http://[fe80::1]/a.mp4",
            "http://[::ffff:169.254.169.254]/a.mp4",
            "http://[fd00:ec2::254]/a.mp4",
            "http://100.100.100.200/latest/meta-data/",
            "http://192.0.0.192/opc/v1/instance/",
        ],
    )
    async def test_rejects_link_local_and_metadata_literals(self, url: str):
        with capture_http() as router:
            route = router.get(url).mock(return_value=httpx.Response(200))
            async with artifact_http_client(resolver=_resolver_returning("93.184.216.34")) as client:
                with pytest.raises(ArtifactDestinationRejectedError):
                    await client.get(url)
        assert route.call_count == 0

    async def test_rejects_hostname_resolving_to_link_local(self):
        with capture_http() as router:
            route = router.get("https://cdn.example/a.mp4").mock(return_value=httpx.Response(200))
            resolver = _resolver_returning("93.184.216.34", "169.254.169.254")
            async with artifact_http_client(resolver=resolver) as client:
                with pytest.raises(ArtifactDestinationRejectedError):
                    await client.get("https://cdn.example/a.mp4")
        assert route.call_count == 0

    async def test_rejects_hostname_resolving_to_mapped_link_local(self):
        with capture_http() as router:
            router.get("https://cdn.example/a.mp4").mock(return_value=httpx.Response(200))
            async with artifact_http_client(resolver=_resolver_returning("::ffff:169.254.169.254")) as client:
                with pytest.raises(ArtifactDestinationRejectedError):
                    await client.get("https://cdn.example/a.mp4")

    async def test_rejects_redirect_hop_to_link_local(self):
        with capture_http() as router:
            router.get("https://cdn.example/a.mp4").mock(
                return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/"})
            )
            target = router.get("http://169.254.169.254/latest/").mock(return_value=httpx.Response(200))
            async with artifact_http_client(
                resolver=_resolver_returning("93.184.216.34"), follow_redirects=True
            ) as client:
                with pytest.raises(ArtifactDestinationRejectedError):
                    await client.get("https://cdn.example/a.mp4")
        assert target.call_count == 0

    async def test_rejects_non_http_scheme(self):
        async with artifact_http_client(resolver=_resolver_returning("93.184.216.34")) as client:
            with pytest.raises(ArtifactDestinationRejectedError):
                await client.get("ftp://cdn.example/a.mp4")

    @pytest.mark.parametrize(
        ("url", "resolved"),
        [
            ("http://127.0.0.1:8188/view", ()),
            ("http://192.168.1.20:8188/view", ()),
            ("http://[::1]:8188/view", ()),
            ("https://cdn.example/a.mp4", ("93.184.216.34",)),
            ("http://comfy.lan:8188/view", ("10.0.0.5",)),
            ("http://100.100.100.100:8188/view", ()),
        ],
    )
    async def test_allows_loopback_private_and_public(self, url: str, resolved: tuple[str, ...]):
        with capture_http() as router:
            route = router.get(url).mock(return_value=httpx.Response(200, content=b"ok"))
            async with artifact_http_client(resolver=_resolver_returning(*resolved)) as client:
                response = await client.get(url)
        assert response.status_code == 200
        assert route.call_count == 1

    async def test_unresolvable_host_is_left_to_the_transport(self):
        with capture_http() as router:
            route = router.get("https://cdn.example/a.mp4").mock(return_value=httpx.Response(200))
            async with artifact_http_client(resolver=_unresolvable) as client:
                await client.get("https://cdn.example/a.mp4")
        assert route.call_count == 1

    async def test_resolution_past_the_deadline_is_left_to_the_transport(self):
        never = asyncio.Event()

        async def hanging(host: str, port: int) -> Iterable[str]:
            await never.wait()
            return ()

        with capture_http() as router:
            route = router.get("https://cdn.example/a.mp4").mock(return_value=httpx.Response(200))
            async with artifact_http_client(resolver=hanging, resolve_timeout=0) as client:
                await client.get("https://cdn.example/a.mp4")
        assert route.call_count == 1


class TestCappedBody:
    async def _stream_to(self, response: httpx.Response, output: Path, *, max_bytes: int) -> None:
        with capture_http() as router:
            router.get("https://cdn.example/a.mp4").mock(return_value=response)
            async with (
                artifact_http_client(resolver=_resolver_returning("93.184.216.34")) as client,
                client.stream("GET", "https://cdn.example/a.mp4") as resp,
            ):
                await stream_body_to_file(resp, output, max_bytes=max_bytes)

    async def test_writes_body_within_limit(self, tmp_path: Path):
        output = tmp_path / "a.mp4"
        await self._stream_to(httpx.Response(200, content=b"x" * 100), output, max_bytes=100)
        assert output.read_bytes() == b"x" * 100
        assert not _partial(output).exists()

    async def test_aborts_when_actual_bytes_exceed_limit_despite_small_content_length(self, tmp_path: Path):
        output = tmp_path / "a.mp4"
        response = httpx.Response(200, headers={"Content-Length": "10"}, content=b"x" * 101)
        with pytest.raises(ArtifactTooLargeError):
            await self._stream_to(response, output, max_bytes=100)
        assert not output.exists()
        assert not _partial(output).exists()

    async def test_rejects_declared_content_length_over_limit(self, tmp_path: Path):
        output = tmp_path / "a.mp4"
        response = httpx.Response(200, headers={"Content-Length": "101"}, content=b"x" * 101)
        with pytest.raises(ArtifactTooLargeError):
            await self._stream_to(response, output, max_bytes=100)
        assert not output.exists()
        assert not _partial(output).exists()

    async def test_read_body_capped_returns_body_within_limit(self):
        with capture_http() as router:
            router.get("https://cdn.example/a.wav").mock(return_value=httpx.Response(200, content=b"RIFF"))
            async with (
                artifact_http_client(resolver=_resolver_returning("93.184.216.34")) as client,
                client.stream("GET", "https://cdn.example/a.wav") as resp,
            ):
                assert await read_body_capped(resp, max_bytes=4) == b"RIFF"

    async def test_read_body_capped_aborts_over_limit(self):
        with capture_http() as router:
            router.get("https://cdn.example/a.wav").mock(return_value=httpx.Response(200, content=b"RIFFx"))
            async with (
                artifact_http_client(resolver=_resolver_returning("93.184.216.34")) as client,
                client.stream("GET", "https://cdn.example/a.wav") as resp,
            ):
                with pytest.raises(ArtifactTooLargeError):
                    await read_body_capped(resp, max_bytes=4)

    async def test_error_response_body_is_truncated(self):
        with capture_http() as router:
            router.get("https://cdn.example/a.mp4").mock(
                return_value=httpx.Response(503, headers={"Retry-After": "7"}, content=b"e" * 200)
            )
            async with (
                artifact_http_client(resolver=_resolver_returning("93.184.216.34")) as client,
                client.stream("GET", "https://cdn.example/a.mp4") as resp,
            ):
                buffered = await buffered_error_response(resp, max_bytes=50)
        assert buffered.status_code == 503
        assert buffered.content == b"e" * 50
        assert buffered.headers["Retry-After"] == "7"
        assert str(buffered.request.url) == "https://cdn.example/a.mp4"
