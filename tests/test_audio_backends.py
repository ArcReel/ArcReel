"""AudioBackend 家族测试：registry 注册/创建 + DashScopeAudioBackend（mock httpx，同步端点）
+ OpenAIAudioBackend（mock SDK client，/v1/audio/speech）+ extract_audio_url。"""

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
        from lib.audio_backends import registry as audio_registry
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        marker = DashScopeAudioBackend(api_key="sk")
        try:
            register_backend("fake-audio-test", lambda **_: marker)
            assert create_backend("fake-audio-test") is marker
        finally:
            # 清理全局注册表，避免污染读取注册表的其它测试
            audio_registry._BACKEND_FACTORIES.pop("fake-audio-test", None)


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


def _mock_client(post_resp: httpx.Response | MagicMock, get_resp: httpx.Response | MagicMock) -> AsyncMock:
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
        # 4xx 透出 httpx.HTTPStatusError（与其余 backend 一致），不嵌响应体进异常消息
        err_resp = httpx.Response(400, text="bad request", request=httpx.Request("POST", "https://x"))
        client = _mock_client(err_resp, _download_response())
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError):
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

    async def test_empty_download_retried_then_rejected_no_file(self, tmp_path: Path, monkeypatch):
        # 200 但空体视为瞬态：重试到下载上限后失败，不写 0 字节 wav，合成 POST 不被重跑。
        from lib.retry import DOWNLOAD_MAX_ATTEMPTS

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
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "recover.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert client.post.call_count == 1
        assert client.get.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_download_http_error_raises(self, tmp_path: Path, monkeypatch):
        # 下载 4xx：透出 httpx.HTTPStatusError 且不写文件、不被误判可重试、合成 POST 不被重跑；
        # 异常文本不携带预签名 query（有效期内等同下载凭证）
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        signed_url = "https://x/out.wav?Expires=1&Signature=topsecret"
        err_resp = httpx.Response(404, request=httpx.Request("GET", signed_url))
        client = _mock_client(_synth_response(signed_url), err_resp)
        with patch("httpx.AsyncClient", return_value=client):
            from lib.audio_backends.dashscope import DashScopeAudioBackend

            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "err.wav"
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert "Signature" not in str(excinfo.value)
        assert "https://x/out.wav" in str(excinfo.value)
        assert excinfo.value.response.status_code == 404
        assert client.post.call_count == 1
        assert client.get.call_count == 1, "4xx 不可重试，下载 GET 不应被重试"
        assert not out.exists()


def _mock_speech_client(content: bytes = b"RIFFwavbytes") -> AsyncMock:
    speech_resp = MagicMock()
    speech_resp.content = content
    client = AsyncMock()
    client.audio.speech.create = AsyncMock(return_value=speech_resp)
    return client


class TestOpenAIAudioBackend:
    async def test_synthesize_request_and_bytes(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", base_url="https://relay.example.com/v1", model="tts-1")
            out = tmp_path / "o.wav"
            result = await b.synthesize(AudioSynthesisRequest(text="你好世界", output_path=out, voice="alloy"))

        kwargs = mock_client.audio.speech.create.call_args.kwargs
        assert kwargs["model"] == "tts-1"
        assert kwargs["input"] == "你好世界"
        assert kwargs["voice"] == "alloy"
        # 输出格式跟随落盘扩展名（资源路径约定 .wav）
        assert kwargs["response_format"] == "wav"
        # 字节落盘 + 结果字段
        assert out.read_bytes() == b"RIFFwavbytes"
        assert result.model == "tts-1"
        assert result.characters == len("你好世界")
        assert result.output_path == out

    def test_metadata(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.audio_backends.openai import OpenAIAudioBackend
            from lib.providers import PROVIDER_OPENAI

            b = OpenAIAudioBackend(api_key="sk", model="gpt-4o-mini-tts")
            assert b.name == PROVIDER_OPENAI
            assert b.model == "gpt-4o-mini-tts"
            assert b.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_provider_name_override(self):
        # 包装层（自定义供应商）可用真实 provider 记账
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1", provider_name="custom-7")
            assert b.name == "custom-7"

    async def test_speed_passthrough_and_omitted_when_none(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "a.wav", voice="alloy"))
            assert "speed" not in mock_client.audio.speech.create.call_args.kwargs

            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "b.wav", voice="alloy", speed=1.5)
            )
            assert mock_client.audio.speech.create.call_args.kwargs["speed"] == 1.5

    async def test_language_type_not_sent(self, tmp_path: Path):
        # /v1/audio/speech 无语种字段（DashScope 特有），不应混入请求
        mock_client = _mock_speech_client()
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "c.wav", voice="alloy", language_type="Chinese")
            )
        kwargs = mock_client.audio.speech.create.call_args.kwargs
        assert "language_type" not in kwargs
        assert "language" not in kwargs

    async def test_unknown_suffix_falls_back_to_wav(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "x.bin", voice="alloy"))
        assert mock_client.audio.speech.create.call_args.kwargs["response_format"] == "wav"

    async def test_empty_body_rejected_no_file_no_rebill(self, tmp_path: Path):
        # 200 + 空体：不落 0 字节文件、不重试（重试 = 再次计费）
        mock_client = _mock_speech_client(content=b"")
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空响应体"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="alloy"))

        assert mock_client.audio.speech.create.call_count == 1
        assert not out.exists()

    async def test_write_failure_does_not_rebill_synthesis(self, tmp_path: Path, monkeypatch):
        # 写盘瞬态失败（消息含可重试模式）不应回头重跑会再次计费的合成调用
        monkeypatch.setattr("lib.retry.asyncio.sleep", AsyncMock())
        mock_client = _mock_speech_client()
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out_dir = tmp_path / "missing-dir"
            with pytest.raises(OSError):
                # 父目录不存在 → write_bytes 抛 OSError；伪造含 "timed out" 的消息走最坏路径
                req = AudioSynthesisRequest(text="hi", output_path=out_dir / "o.wav", voice="alloy")
                with patch.object(type(req.output_path), "write_bytes", side_effect=OSError("Connection timed out")):
                    await b.synthesize(req)

        assert mock_client.audio.speech.create.call_count == 1, "写盘失败不得重跑计费的合成调用"
