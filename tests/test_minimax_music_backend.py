"""MiniMaxMusicBackend 单元测试（mock httpx，单步同步端点，不打真实 HTTP）。"""

from __future__ import annotations

import binascii
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.music_backends.base import MusicCapability, MusicCapabilityError, MusicGenerationRequest
from lib.providers import PROVIDER_MINIMAX


def _url_response(url: str = "https://x/out.mp3", status: int = 2) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {"status": status, "audio": url},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    return resp


def _hex_response(hex_audio: str, status: int = 2) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {"status": status, "audio": hex_audio},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    return resp


def _biz_error_response(status_code: int = 1004, msg: str = "invalid api key") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"base_resp": {"status_code": status_code, "status_msg": msg}}
    return resp


def _mock_client(resp: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://x/v1/music_generation")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"error {status_code}", request=request, response=response)


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "Request Entity Too Large"
    resp.raise_for_status = MagicMock(side_effect=_http_error(status_code, "Request Entity Too Large"))
    return resp


def _patches(client: AsyncMock, download: AsyncMock):
    return (
        patch("httpx.AsyncClient", return_value=client),
        patch("lib.music_backends.minimax.download_audio_to_path", download),
    )


class TestCapabilities:
    def test_generation_and_cover(self):
        from lib.music_backends.minimax import MiniMaxMusicBackend

        b = MiniMaxMusicBackend(api_key="sk", model="music-3.0")
        assert b.name == PROVIDER_MINIMAX
        assert b.model == "music-3.0"
        assert b.capabilities == {MusicCapability.MUSIC_GENERATION, MusicCapability.MUSIC_COVER}

    def test_default_model_when_unset(self):
        from lib.music_backends.minimax import MiniMaxMusicBackend

        assert MiniMaxMusicBackend(api_key="sk").model == "music-3.0"

    def test_registered_in_factory(self):
        from lib.music_backends import create_backend, get_registered_backends
        from lib.music_backends.minimax import MiniMaxMusicBackend

        assert PROVIDER_MINIMAX in get_registered_backends()
        assert isinstance(create_backend(PROVIDER_MINIMAX, api_key="sk"), MiniMaxMusicBackend)


class TestGeneration:
    async def test_request_build_and_endpoint(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk", model="music-3.0", base_url="https://api.minimax.io")
            result = await b.generate(
                MusicGenerationRequest(prompt="an upbeat pop song", output_path=tmp_path / "o.mp3")
            )

        body = client.post.call_args.kwargs["json"]
        assert body["model"] == "music-3.0"
        assert body["prompt"] == "an upbeat pop song"
        assert body["output_format"] == "url"
        # global 站：host 派生 /v1 + /music_generation
        assert client.post.call_args.args[0] == "https://api.minimax.io/v1/music_generation"
        assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk"
        assert result.provider == PROVIDER_MINIMAX
        assert result.model == "music-3.0"
        assert result.audio_uri == "https://x/out.mp3"
        download.assert_called_once()

    async def test_default_endpoint_is_domestic(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3"))

        assert client.post.call_args.args[0] == "https://api.minimaxi.com/v1/music_generation"

    async def test_optional_fields_passthrough(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            await b.generate(
                MusicGenerationRequest(
                    prompt="ballad",
                    output_path=tmp_path / "o.mp3",
                    lyrics="[verse]\nhello world",
                    is_instrumental=True,
                    lyrics_optimizer=True,
                    audio_setting={"sample_rate": 44100, "format": "mp3"},
                )
            )

        body = client.post.call_args.kwargs["json"]
        assert body["lyrics"] == "[verse]\nhello world"
        assert body["is_instrumental"] is True
        assert body["lyrics_optimizer"] is True
        assert body["audio_setting"] == {"sample_rate": 44100, "format": "mp3"}

    async def test_omits_unset_optionals(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3"))

        body = client.post.call_args.kwargs["json"]
        for absent in ("lyrics", "is_instrumental", "lyrics_optimizer", "audio_setting", "audio_url", "audio_base64"):
            assert absent not in body


class TestCover:
    async def test_audio_url_passthrough(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk", model="music-cover")
            await b.generate(
                MusicGenerationRequest(
                    prompt="",
                    output_path=tmp_path / "o.mp3",
                    audio_url="https://src/song.mp3",
                    cover_feature_id="feat-1",
                )
            )

        body = client.post.call_args.kwargs["json"]
        assert body["audio_url"] == "https://src/song.mp3"
        assert body["cover_feature_id"] == "feat-1"
        # 空 prompt 不写入
        assert "prompt" not in body

    async def test_audio_url_precedence_over_base64(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk", model="music-cover")
            await b.generate(
                MusicGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp3",
                    audio_url="https://src/song.mp3",
                    audio_base64="deadbeef",
                )
            )

        body = client.post.call_args.kwargs["json"]
        assert body["audio_url"] == "https://src/song.mp3"
        assert "audio_base64" not in body

    async def test_audio_base64_when_no_url(self, tmp_path: Path):
        client = _mock_client(_url_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk", model="music-cover")
            await b.generate(
                MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3", audio_base64="deadbeef")
            )

        body = client.post.call_args.kwargs["json"]
        assert body["audio_base64"] == "deadbeef"
        assert "audio_url" not in body


class TestResponseHandling:
    async def test_hex_response_decoded_and_saved(self, tmp_path: Path):
        raw = b"ID3fake-audio-bytes"
        hex_audio = binascii.hexlify(raw).decode("ascii")
        client = _mock_client(_hex_response(hex_audio))
        out = tmp_path / "o.mp3"
        # 不 patch download：hex 路径独立落盘
        with patch("httpx.AsyncClient", return_value=client):
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            result = await b.generate(MusicGenerationRequest(prompt="x", output_path=out, output_format="hex"))

        assert out.read_bytes() == raw
        # hex 路径无远端 URL
        assert result.audio_uri is None
        assert client.post.call_args.kwargs["json"]["output_format"] == "hex"

    async def test_business_error_raises_runtime(self, tmp_path: Path):
        client = _mock_client(_biz_error_response(1004, "invalid api key"))
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3"))
        assert "1004" in str(ei.value)
        # 业务错误不重试、不下载
        assert client.post.call_count == 1
        download.assert_not_called()

    async def test_in_progress_status_raises(self, tmp_path: Path):
        client = _mock_client(_url_response(status=1))
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3"))
        assert "status=1" in str(ei.value)
        download.assert_not_called()

    async def test_missing_audio_raises(self, tmp_path: Path):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"status": 2}, "base_resp": {"status_code": 0}}
        client = _mock_client(resp)
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3"))

    async def test_unsupported_output_format_raises(self, tmp_path: Path):
        from lib.music_backends.minimax import MiniMaxMusicBackend

        b = MiniMaxMusicBackend(api_key="sk")
        with pytest.raises(MusicCapabilityError) as ei:
            await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3", output_format="ogg"))
        assert ei.value.code == "music_output_format_unsupported"
        assert ei.value.params["output_format"] == "ogg"


class TestHttpErrors:
    async def test_400_surfaces_httpstatuserror_single_call(self, tmp_path: Path):
        client = _mock_client(_error_response(400))
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(MusicGenerationRequest(prompt="p", output_path=tmp_path / "o.mp3"))
        assert ei.value.response.status_code == 400
        assert client.post.call_count == 1
        download.assert_not_called()

    async def test_413_surfaces_httpstatuserror_no_retry(self, tmp_path: Path):
        client = _mock_client(_error_response(413))
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(MusicGenerationRequest(prompt="p", output_path=tmp_path / "o.mp3"))
        assert ei.value.response.status_code == 413
        assert client.post.call_count == 1
        download.assert_not_called()


class TestRetryScope:
    async def test_download_failure_does_not_retrigger_generation(self, tmp_path: Path, monkeypatch):
        # 下载阶段瞬态失败只在下载层重试，绝不回退到重跑非幂等的生成 POST（防重复生成 + 重复计费）。
        # 退避 sleep 打桩跳过，避免下载层重试真的等 DOWNLOAD_BACKOFF 秒级时间。
        from lib.retry import DOWNLOAD_MAX_ATTEMPTS

        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        client = _mock_client(_url_response())
        download = AsyncMock(side_effect=httpx.ConnectError("conn reset"))
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.music_backends.minimax import MiniMaxMusicBackend

            b = MiniMaxMusicBackend(api_key="sk")
            with pytest.raises(httpx.ConnectError):
                await b.generate(MusicGenerationRequest(prompt="x", output_path=tmp_path / "o.mp3"))
        # 生成 POST 恰好一次（计费一次）；重试全部发生在下载层
        assert client.post.call_count == 1
        assert download.call_count == DOWNLOAD_MAX_ATTEMPTS
