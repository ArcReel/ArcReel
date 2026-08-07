"""MiniMaxAudioBackend tests: registry registration + /v1/t2a_v2 request/response
+ download retry isolation + base_resp business error + audio format/voice mapping.

Mirrors TestDashScopeAudioBackend patterns: mock httpx.AsyncClient, assert the
non-idempotent synthesis POST is never retried when the idempotent download GET fails.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.audio_backends import (
    AudioCapability,
    AudioSynthesisRequest,
    create_backend,
    get_registered_backends,
)
from lib.providers import PROVIDER_MINIMAX


class TestRegistry:
    def test_minimax_auto_registered(self):
        assert PROVIDER_MINIMAX in get_registered_backends()

    def test_create_minimax(self):
        from lib.audio_backends.minimax import MiniMaxAudioBackend

        backend = create_backend(PROVIDER_MINIMAX, api_key="sk")
        assert isinstance(backend, MiniMaxAudioBackend)

    def test_default_model_is_speech_2_8_hd(self):
        from lib.audio_backends.minimax import MiniMaxAudioBackend

        assert MiniMaxAudioBackend(api_key="sk").model == "speech-2.8-hd"


class TestRegistryModels:
    def test_minimax_exposes_speech_models(self):
        from lib.config.registry import PROVIDER_REGISTRY, default_model_for_provider

        meta = PROVIDER_REGISTRY[PROVIDER_MINIMAX]
        assert "audio" in meta.media_types
        for model_id in (
            "speech-2.8-hd",
            "speech-2.8-turbo",
            "speech-2.6-hd",
            "speech-2.6-turbo",
            "speech-02-hd",
            "speech-02-turbo",
            "speech-01-hd",
            "speech-01-turbo",
        ):
            info = meta.models[model_id]
            assert info.media_type == "audio"
            assert "text_to_speech" in info.capabilities
        assert default_model_for_provider(PROVIDER_MINIMAX, "audio") == "speech-2.8-hd"


def _synth_response(url: str = "https://x/out.wav") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {"audio": url, "status": 2},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    return resp


def _download_response(content: bytes = b"RIFFfakewav") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    return resp


def _mock_client(post_resp: httpx.Response | MagicMock, get_resp: httpx.Response | MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=post_resp)
    client.get = AsyncMock(return_value=get_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestMiniMaxAudioBackend:
    def test_metadata(self):
        from lib.audio_backends.minimax import MiniMaxAudioBackend

        b = MiniMaxAudioBackend(api_key="sk", model="speech-2.8-hd")
        assert b.name == PROVIDER_MINIMAX
        assert b.model == "speech-2.8-hd"
        assert b.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_default_model(self):
        from lib.audio_backends.minimax import MiniMaxAudioBackend

        assert MiniMaxAudioBackend(api_key="sk").model == "speech-2.8-hd"

    async def test_synthesize_request_and_download(self, tmp_path: Path):
        client = _mock_client(_synth_response(), _download_response(b"RIFFwavbytes"))
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk", model="speech-2.8-hd", base_url="https://api.minimaxi.com")
            out = tmp_path / "o.wav"
            result = await b.synthesize(
                AudioSynthesisRequest(text="你好世界", output_path=out, voice="male-qn-qingse", language_type="Chinese")
            )

        body = client.post.call_args.kwargs["json"]
        assert body["model"] == "speech-2.8-hd"
        assert body["text"] == "你好世界"
        assert body["output_format"] == "url"
        assert body["voice_setting"] == {"voice_id": "male-qn-qingse"}
        assert body["audio_setting"]["format"] == "wav"
        assert body["language_boost"] == "Chinese"
        # 鉴权头 + 端点
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk"
        assert client.post.call_args.args[0].endswith("/v1/t2a_v2")
        # 下载 URL 命中响应里的 data.audio
        assert client.get.call_args.args[0] == "https://x/out.wav"
        # 字节落盘 + 结果字段
        assert out.read_bytes() == b"RIFFwavbytes"
        assert result.provider == PROVIDER_MINIMAX
        assert result.model == "speech-2.8-hd"
        assert result.characters == len("你好世界")
        assert result.output_path == out

    async def test_speed_passthrough_into_voice_setting(self, tmp_path: Path):
        client = _mock_client(_synth_response(), _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "s.wav", voice="v", speed=1.5))
        body = client.post.call_args.kwargs["json"]
        assert body["voice_setting"]["speed"] == 1.5

    async def test_speed_omitted_when_none(self, tmp_path: Path):
        client = _mock_client(_synth_response(), _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "s.wav", voice="v"))
        body = client.post.call_args.kwargs["json"]
        assert "speed" not in body["voice_setting"]

    async def test_audio_format_follows_extension(self, tmp_path: Path):
        client = _mock_client(_synth_response(), _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "o.mp3", voice="v"))
        assert client.post.call_args.kwargs["json"]["audio_setting"]["format"] == "mp3"

    async def test_unknown_suffix_falls_back_to_wav(self, tmp_path: Path):
        client = _mock_client(_synth_response(), _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "x.bin", voice="v"))
        assert client.post.call_args.kwargs["json"]["audio_setting"]["format"] == "wav"

    async def test_base_resp_error_raises(self, tmp_path: Path):
        # 200 + base_resp.status_code != 0 → 业务错误，不取音频、不下载
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {}, "base_resp": {"status_code": 1001, "status_msg": "bad voice"}}
        client = _mock_client(resp, _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            with pytest.raises(RuntimeError, match="语音合成失败"):
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="v"))
        # 业务错误不触发下载
        client.get.assert_not_called()

    async def test_missing_audio_url_raises(self, tmp_path: Path):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {}, "base_resp": {"status_code": 0, "status_msg": "success"}}
        client = _mock_client(resp, _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            with pytest.raises(RuntimeError, match="data.audio"):
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="v"))
        client.get.assert_not_called()

    async def test_http_error_raises(self, tmp_path: Path):
        # 4xx 透出 httpx.HTTPStatusError；计费的合成 POST 只发一次、不连带触发下载
        err_resp = httpx.Response(400, text="bad request", request=httpx.Request("POST", "https://x"))
        client = _mock_client(err_resp, _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError):
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="v"))
        assert client.post.call_count == 1
        client.get.assert_not_called()

    async def test_download_failure_does_not_rebill_synthesis(self, tmp_path: Path, monkeypatch):
        # 下载瞬时失败只重试 GET，绝不回头重跑会再次计费的合成 POST。
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        client = AsyncMock()
        client.post = AsyncMock(return_value=_synth_response())
        client.get = AsyncMock(side_effect=[httpx.ConnectError("transient"), _download_response(b"ok")])
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            out = tmp_path / "d.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="v"))

        # 合成 POST 只发一次（未被下载重试连带重跑 → 不重复计费），下载 GET 重试到第 2 次成功
        assert client.post.call_count == 1
        assert client.get.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_empty_download_retried_then_rejected_no_file(self, tmp_path: Path, monkeypatch):
        # 200 但空体视为瞬态：重试到下载上限后失败，不写 0 字节音频，合成 POST 不被重跑。
        from lib.retry import DOWNLOAD_MAX_ATTEMPTS

        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        client = AsyncMock()
        client.post = AsyncMock(return_value=_synth_response())
        client.get = AsyncMock(return_value=_download_response(b""))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空内容"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="v"))

        assert client.post.call_count == 1
        assert client.get.call_count == DOWNLOAD_MAX_ATTEMPTS
        assert not out.exists()

    async def test_empty_download_transient_recovers(self, tmp_path: Path, monkeypatch):
        # 空体一次后恢复：重试拿到字节落盘，合成 POST 不被重跑
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        client = AsyncMock()
        client.post = AsyncMock(return_value=_synth_response())
        client.get = AsyncMock(side_effect=[_download_response(b""), _download_response(b"ok")])
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            out = tmp_path / "recover.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="v"))

        assert client.post.call_count == 1
        assert client.get.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_download_http_error_raises(self, tmp_path: Path, monkeypatch):
        # 下载 4xx：透出 httpx.HTTPStatusError 且不写文件、不被误判可重试、合成 POST 不被重跑；
        # 异常文本不携带结果 URL query（有效期内等同下载凭证）
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        signed_url = "https://x/out.wav?Expires=1&Signature=topsecret"
        err_resp = httpx.Response(404, request=httpx.Request("GET", signed_url))
        client = _mock_client(_synth_response(signed_url), err_resp)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.minimax import MiniMaxAudioBackend

            b = MiniMaxAudioBackend(api_key="sk")
            out = tmp_path / "err.wav"
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="v"))

        assert "Signature" not in str(excinfo.value)
        assert "https://x/out.wav" in str(excinfo.value)
        assert excinfo.value.response.status_code == 404
        assert client.post.call_count == 1
        assert client.get.call_count == 1, "4xx 不可重试，下载 GET 不应被重试"
        assert not out.exists()
