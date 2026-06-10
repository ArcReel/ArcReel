"""AudioBackend 家族测试：registry 注册/创建 + DashScopeAudioBackend（mock httpx，同步端点）+ extract_audio_url。"""

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
    register_backend,
)
from lib.dashscope_shared import extract_audio_url
from lib.providers import PROVIDER_DASHSCOPE


class TestRegistry:
    def test_dashscope_auto_registered(self):
        assert PROVIDER_DASHSCOPE in get_registered_backends()

    def test_create_dashscope(self):
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        backend = create_backend(PROVIDER_DASHSCOPE, api_key="sk")
        assert isinstance(backend, DashScopeAudioBackend)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown audio backend"):
            create_backend("nope")

    def test_register_and_create_custom(self):
        marker = object()
        register_backend("fake-audio-test", lambda **_: marker)
        assert create_backend("fake-audio-test") is marker


class TestExtractAudioUrl:
    def test_valid(self):
        assert extract_audio_url({"output": {"audio": {"url": "https://x/y.wav"}}}) == "https://x/y.wav"

    def test_missing_raises(self):
        with pytest.raises(RuntimeError, match="audio.url"):
            extract_audio_url({"output": {}})

    def test_failure_reason_surfaced(self):
        with pytest.raises(RuntimeError, match="InvalidApiKey"):
            extract_audio_url({"code": "InvalidApiKey", "message": "bad key"})


def _synth_response(url: str = "https://x/out.wav") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"output": {"audio": {"url": url}}}
    return resp


def _download_response(content: bytes = b"RIFFfakewav") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    return resp


def _mock_client(post_resp: MagicMock, get_resp: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=post_resp)
    client.get = AsyncMock(return_value=get_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestDashScopeAudioBackend:
    def test_metadata(self):
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        b = DashScopeAudioBackend(api_key="sk", model="qwen3-tts-flash")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.model == "qwen3-tts-flash"
        assert b.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_default_model(self):
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        b = DashScopeAudioBackend(api_key="sk")
        assert b.model == "qwen3-tts-flash"

    async def test_synthesize_request_and_download(self, tmp_path: Path):
        client = _mock_client(_synth_response(), _download_response(b"RIFFwavbytes"))
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk", model="qwen3-tts-flash", base_url="https://dashscope.aliyuncs.com")
            out = tmp_path / "o.wav"
            result = await b.synthesize(
                AudioSynthesisRequest(text="你好世界", output_path=out, voice="Cherry", language_type="Chinese")
            )

        body = client.post.call_args.kwargs["json"]
        assert body["model"] == "qwen3-tts-flash"
        assert body["input"] == {"text": "你好世界", "voice": "Cherry", "language_type": "Chinese"}
        # 同步 TTS 不带 async 头
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk"
        assert "X-DashScope-Async" not in headers
        # 端点：host 派生 /api/v1 + 多模态生成路径
        assert client.post.call_args.args[0].endswith("/api/v1/services/aigc/multimodal-generation/generation")
        # 下载 URL 命中响应里的 audio.url
        assert client.get.call_args.args[0] == "https://x/out.wav"
        # 字节落盘 + 结果字段
        assert out.read_bytes() == b"RIFFwavbytes"
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.model == "qwen3-tts-flash"
        assert result.characters == len("你好世界")
        assert result.output_path == out

    async def test_speed_param_ignored(self, tmp_path: Path):
        # speed 仅 realtime 支持，同步模型忽略（不报错、请求体不带 speed）
        client = _mock_client(_synth_response(), _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "s.wav", voice="Ethan", speed=1.5)
            )
        body = client.post.call_args.kwargs["json"]
        assert "speed" not in body["input"]
        assert "speech_rate" not in body["input"]

    async def test_http_error_raises(self, tmp_path: Path):
        err_resp = MagicMock()
        err_resp.status_code = 400
        err_resp.text = "bad request"
        client = _mock_client(err_resp, _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            with pytest.raises(RuntimeError, match="返回 400"):
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="Cherry"))

    async def test_download_failure_does_not_rebill_synthesis(self, tmp_path: Path, monkeypatch):
        # 下载瞬时失败只重试 GET，绝不回头重跑会再次计费的合成 POST。
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        client = AsyncMock()
        client.post = AsyncMock(return_value=_synth_response())
        client.get = AsyncMock(side_effect=[httpx.ConnectError("transient"), _download_response(b"ok")])
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "d.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        # 合成 POST 只发一次（未被下载重试连带重跑 → 不重复计费），下载 GET 重试到第 2 次成功
        assert client.post.call_count == 1
        assert client.get.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_empty_download_rejected_no_file(self, tmp_path: Path, monkeypatch):
        # 200 但空体：不写 0 字节 wav，且合成 POST 不被重跑。
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        client = AsyncMock()
        client.post = AsyncMock(return_value=_synth_response())
        client.get = AsyncMock(return_value=_download_response(b""))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空内容"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert client.post.call_count == 1
        assert not out.exists()

    async def test_download_http_error_raises(self, tmp_path: Path, monkeypatch):
        # 下载 4xx：raise 且不写文件、合成 POST 不被重跑
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        err_resp = MagicMock()
        err_resp.status_code = 404
        client = _mock_client(_synth_response(), err_resp)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "err.wav"
            with pytest.raises(RuntimeError, match="音频下载返回 404"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert client.post.call_count == 1
        assert not out.exists()
