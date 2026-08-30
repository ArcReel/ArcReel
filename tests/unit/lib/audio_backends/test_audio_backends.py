"""AudioBackend 家族测试：registry 注册/创建 + DashScopeAudioBackend（mock httpx，同步端点）
+ OpenAIAudioBackend（mock SDK client，/v1/audio/speech）+ extract_audio_url。"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import patch

import httpx
import pytest
import respx

from lib.audio_backends import (
    AudioCapability,
    AudioSynthesisRequest,
    create_backend,
    get_registered_backends,
    register_backend,
)
from lib.dashscope_shared import extract_audio_url
from lib.providers import PROVIDER_DASHSCOPE
from tests.fakes import captured_openai_clients
from tests.http_capture import capture_http, only_request, request_json


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
        with pytest.raises(RuntimeError, match=re.escape("audio.url")):
            extract_audio_url({"output": {}})

    def test_failure_reason_surfaced(self):
        with pytest.raises(RuntimeError, match="InvalidApiKey"):
            extract_audio_url({"code": "InvalidApiKey", "message": "bad key"})


_SYNTH_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
_AUDIO_URL = "https://x/out.wav"


class _DashScopeRoutes(NamedTuple):
    synthesize: respx.Route
    download: respx.Route


@contextmanager
def _dashscope_audio_routes(
    *,
    synth: httpx.Response | None = None,
    download: httpx.Response | list[httpx.Response | Exception] | None = None,
    audio_url: str = _AUDIO_URL,
    synth_url: str = _SYNTH_URL,
) -> Generator[_DashScopeRoutes]:
    """DashScope TTS 的两条出站流：合成 POST 与音频 GET。

    走 respx 在 transport 层拦截，断言对象是真实序列化后的请求（URL 拼接、鉴权头、body 编码
    都在断言范围内），而不是客户端替身记录的调用参数。
    """
    with capture_http() as router:
        synth_route = router.post(synth_url)
        synth_route.mock(return_value=synth or httpx.Response(200, json={"output": {"audio": {"url": audio_url}}}))
        download_route = router.get(audio_url)
        if isinstance(download, list):
            download_route.mock(side_effect=download)
        else:
            download_route.mock(return_value=download or httpx.Response(200, content=b"RIFFfakewav"))
        yield _DashScopeRoutes(synthesize=synth_route, download=download_route)


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

    def test_list_voices_returns_nonempty_catalog_with_unique_ids(self):
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        b = DashScopeAudioBackend(api_key="sk")
        voices = b.list_voices()
        assert voices
        ids = [v.id for v in voices]
        assert len(ids) == len(set(ids))
        assert all(v.label for v in voices)
        # 调用方（voices 端点）依赖每次返回独立列表，不能是同一份可变共享对象
        assert voices is not b.list_voices()

    async def test_synthesize_request_and_download(self, tmp_path: Path):
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(download=httpx.Response(200, content=b"RIFFwavbytes")) as routes:
            b = DashScopeAudioBackend(api_key="sk", model="qwen3-tts-flash", base_url="https://dashscope.aliyuncs.com")
            out = tmp_path / "o.wav"
            result = await b.synthesize(
                AudioSynthesisRequest(text="你好世界", output_path=out, voice="Cherry", language_type="Chinese")
            )

        submitted = only_request(routes.synthesize)
        body = request_json(submitted)
        assert body["model"] == "qwen3-tts-flash"
        assert body["input"] == {"text": "你好世界", "voice": "Cherry", "language_type": "Chinese"}
        # 同步 TTS 不带 async 头
        assert submitted.headers["Authorization"] == "Bearer sk"
        assert "X-DashScope-Async" not in submitted.headers
        # 端点：host 派生 /api/v1 + 多模态生成路径
        assert submitted.url.path == "/api/v1/services/aigc/multimodal-generation/generation"
        # 下载 URL 命中响应里的 audio.url
        assert str(only_request(routes.download).url) == _AUDIO_URL
        # 字节落盘 + 结果字段
        assert out.read_bytes() == b"RIFFwavbytes"
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.model == "qwen3-tts-flash"
        assert result.characters == len("你好世界")
        assert result.output_path == out

    async def test_speed_param_ignored(self, tmp_path: Path):
        # speed 仅 realtime 支持，同步模型忽略（不报错、请求体不带 speed）
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes() as routes:
            b = DashScopeAudioBackend(api_key="sk")
            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "s.wav", voice="Ethan", speed=1.5)
            )

        body = request_json(only_request(routes.synthesize))
        assert "speed" not in body["input"]
        assert "speech_rate" not in body["input"]

    async def test_http_error_raises(self, tmp_path: Path):
        # 4xx 透出 httpx.HTTPStatusError（与其余 backend 一致），不嵌响应体进异常消息；提交按状态码不可重试
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(synth=httpx.Response(400, text="bad request")) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError):
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="Cherry"))

        # 4xx 按 status_code fail-fast：计费的合成 POST 只发一次、不连带触发下载
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 0

    async def test_submit_4xx_with_transient_substring_no_retry(self, tmp_path: Path, poll_clock):
        # 4xx 错误消息带 "503" 子串（请求 URL/task_id）：旧字符串兜底会据此误判重试到超时，
        # 新状态码谓词只读 response.status_code，按 400 fail-fast——计费的合成 POST 只发一次、不连带下载。
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        # host 里带 503 让异常文本命中瞬态子串——真实形态是请求 URL / task_id 里出现的数字
        host = "https://dashscope-503.example.com"
        with _dashscope_audio_routes(
            synth=httpx.Response(400, text="bad request"),
            synth_url=f"{host}/api/v1/services/aigc/multimodal-generation/generation",
        ) as routes:
            b = DashScopeAudioBackend(api_key="sk", base_url=host)
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="Cherry"))

        # 异常字符串确实带瞬态子串（旧兜底据此误判重试的前提）；新谓词按状态码单次 fail-fast
        assert "503" in str(ei.value)
        assert ei.value.response.status_code == 400
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 0

    async def test_download_failure_does_not_rebill_synthesis(self, tmp_path: Path, poll_clock):
        # 下载瞬时失败只重试 GET，绝不回头重跑会再次计费的合成 POST。
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(
            download=[httpx.ConnectError("transient"), httpx.Response(200, content=b"ok")]
        ) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "d.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        # 合成 POST 只发一次（未被下载重试连带重跑 → 不重复计费），下载 GET 重试到第 2 次成功
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_empty_download_retried_then_rejected_no_file(self, tmp_path: Path, poll_clock):
        # 200 但空体视为瞬态：重试到共用的下载失败预算耗尽后失败，不写 0 字节 wav，
        # 合成 POST 不被重跑。
        from lib.audio_backends.dashscope import DashScopeAudioBackend
        from lib.video_backends.base import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        with _dashscope_audio_routes(download=httpx.Response(200, content=b"")) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空内容"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES
        assert not out.exists()

    async def test_empty_download_transient_recovers(self, tmp_path: Path, poll_clock):
        # 空体一次后恢复：重试拿到字节落盘，合成 POST 不被重跑
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(
            download=[httpx.Response(200, content=b""), httpx.Response(200, content=b"ok")]
        ) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "recover.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_download_http_error_raises(self, tmp_path: Path, poll_clock):
        # 下载 4xx：透出 httpx.HTTPStatusError 且不写文件、不被误判可重试、合成 POST 不被重跑；
        # 异常文本不携带预签名 query（有效期内等同下载凭证）
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        signed_url = "https://x/out.wav?Expires=1&Signature=topsecret"
        with _dashscope_audio_routes(audio_url=signed_url, download=httpx.Response(404)) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "err.wav"
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert "Signature" not in str(excinfo.value)
        assert "https://x/out.wav" in str(excinfo.value)
        assert excinfo.value.response.status_code == 404
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 1, "4xx 不可重试，下载 GET 不应被重试"
        assert not out.exists()

    async def test_download_write_settles_before_cancellation_propagates(self, tmp_path: Path):
        # 落盘走线程卸载，但 staging 文件的写入必须在取消传播前结算完：调用方
        # MediaGenerator.generate_audio_async 的 finally 会 unlink staging 路径，
        # 写入线程若仍在跑就会与该清理交错（POSIX 下写出孤儿文件，Windows 下 unlink 报错）。
        from lib.audio_backends.dashscope import DashScopeAudioBackend

        started = threading.Event()
        release = threading.Event()
        out = _GatedWritePath(tmp_path / "gated.wav").attach(started, release)

        with _dashscope_audio_routes(download=httpx.Response(200, content=b"RIFFwavbytes")):
            b = DashScopeAudioBackend(api_key="sk")
            task = asyncio.create_task(
                b.synthesize(AudioSynthesisRequest(text="你好", output_path=out, voice="Cherry"))
            )
            # 事件握手：写入线程已进入 write_bytes 且被闸门挡住
            await asyncio.to_thread(started.wait, 5)
            task.cancel()
            # 线程往返把控制权交还事件循环，可中断实现会在此结束
            await asyncio.to_thread(lambda: None)
            assert not task.done(), "取消不得在已启动的 staging 写入结算前返回"

            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert out.read_bytes() == b"RIFFwavbytes"


class _GatedWritePath(Path):
    """写入被闸门挡住的 ``output_path``：观测取消与线程写入的交错。

    作为请求的输入对象注入，不 patch 生产符号。
    """

    def attach(self, started: threading.Event, release: threading.Event) -> _GatedWritePath:
        self._started = started
        self._release = release
        return self

    def write_bytes(self, data) -> int:
        self._started.set()
        self._release.wait(5)
        return super().write_bytes(data)


class _RecordingSpeechClient:
    """OpenAI 兼容 TTS 客户端替身：记录每次 speech.create 的请求参数，回固定字节。

    上线参数、落盘格式这些契约都是「发出去的请求长什么样」，断言落在 ``requests`` 里的
    请求内容上，而不是替身的调用对象。
    """

    def __init__(self, content: bytes = b"RIFFwavbytes") -> None:
        self.requests: list[dict[str, Any]] = []
        self._response = SimpleNamespace(content=content)
        self.audio = SimpleNamespace(speech=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        return self._response


def _mock_speech_client(content: bytes = b"RIFFwavbytes") -> _RecordingSpeechClient:
    return _RecordingSpeechClient(content)


class TestOpenAIAudioBackend:
    async def test_synthesize_request_and_bytes(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", base_url="https://relay.example.com/v1", model="tts-1")
            out = tmp_path / "o.wav"
            result = await b.synthesize(AudioSynthesisRequest(text="你好世界", output_path=out, voice="alloy"))

        kwargs = mock_client.requests[-1]
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
        with captured_openai_clients():
            from lib.audio_backends.openai import OpenAIAudioBackend
            from lib.providers import PROVIDER_OPENAI

            b = OpenAIAudioBackend(api_key="sk", model="gpt-4o-mini-tts")
            assert b.name == PROVIDER_OPENAI
            assert b.model == "gpt-4o-mini-tts"
            assert b.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_provider_name_override(self):
        # 包装层（自定义供应商）可用真实 provider 记账
        with captured_openai_clients():
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1", provider_name="custom-7")
            assert b.name == "custom-7"

    def test_list_voices_returns_official_catalog(self):
        with captured_openai_clients():
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="gpt-4o-mini-tts")
            voices = b.list_voices()
            ids = {v.id for v in voices}
            assert {"alloy", "marin", "cedar"} <= ids
            assert len(ids) == len(voices)

    def test_list_voices_excludes_unsupported_ids_for_legacy_models(self):
        with captured_openai_clients():
            from lib.audio_backends.openai import OpenAIAudioBackend

            for legacy_model in ("tts-1", "tts-1-hd"):
                b = OpenAIAudioBackend(api_key="sk", model=legacy_model)
                ids = {v.id for v in b.list_voices()}
                assert ids.isdisjoint({"ballad", "verse", "marin", "cedar"})
                assert "alloy" in ids

    def test_list_voices_returns_full_catalog_for_custom_openai_tts_endpoint(self):
        """自定义 openai-tts endpoint 未落入官方 legacy 集合时不额外收窄，保持既有兼容口径。"""
        with captured_openai_clients():
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="fish-audio-v1", provider_name="custom-7")
            ids = {v.id for v in b.list_voices()}
            assert {"ballad", "verse", "marin", "cedar"} <= ids

    def test_list_voices_legacy_narrowing_only_applies_to_official_openai(self):
        """自定义供应商即使模型名恰好也叫 tts-1/tts-1-hd，也无法确定其继承官方同名模型的
        音色限制——legacy 收窄只对 provider_name 为官方 openai 时生效，避免对无法验证的
        第三方 endpoint 误收窄。"""
        with captured_openai_clients():
            from lib.audio_backends.openai import OpenAIAudioBackend

            for legacy_model in ("tts-1", "tts-1-hd"):
                b = OpenAIAudioBackend(api_key="sk", model=legacy_model, provider_name="custom-7")
                ids = {v.id for v in b.list_voices()}
                assert {"ballad", "verse", "marin", "cedar"} <= ids

    async def test_speed_passthrough_and_omitted_when_none(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "a.wav", voice="alloy"))
            assert "speed" not in mock_client.requests[-1]

            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "b.wav", voice="alloy", speed=1.5)
            )
            assert mock_client.requests[-1]["speed"] == 1.5

    async def test_language_type_not_sent(self, tmp_path: Path):
        # /v1/audio/speech 无语种字段（DashScope 特有），不应混入请求
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "c.wav", voice="alloy", language_type="Chinese")
            )
        kwargs = mock_client.requests[-1]
        assert "language_type" not in kwargs
        assert "language" not in kwargs

    async def test_unknown_suffix_falls_back_to_wav(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "x.bin", voice="alloy"))
        assert mock_client.requests[-1]["response_format"] == "wav"

    async def test_empty_body_rejected_no_file_no_rebill(self, tmp_path: Path):
        # 200 + 空体：不落 0 字节文件、不重试（重试 = 再次计费）
        mock_client = _mock_speech_client(content=b"")
        with captured_openai_clients(mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空响应体"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="alloy"))

        assert len(mock_client.requests) == 1
        assert not out.exists()

    async def test_write_failure_does_not_rebill_synthesis(self, tmp_path: Path, poll_clock):
        # 写盘瞬态失败（消息含可重试模式）不应回头重跑会再次计费的合成调用
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out_dir = tmp_path / "missing-dir"
            # 父目录不存在 → write_bytes 抛 OSError；伪造含 "timed out" 的消息走最坏路径
            req = AudioSynthesisRequest(text="hi", output_path=out_dir / "o.wav", voice="alloy")
            with (
                patch.object(type(req.output_path), "write_bytes", side_effect=OSError("Connection timed out")),
                pytest.raises(OSError, match="Connection timed out"),
            ):
                await b.synthesize(req)

        assert len(mock_client.requests) == 1, "写盘失败不得重跑计费的合成调用"
