"""AudioBackend registry creation plus DashScope synchronous HTTP, OpenAI SDK /v1/audio/speech
substitutes, and extract_audio_url."""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import patch

import httpx
import pytest
import respx

from lib.backends.artifact_download_guard import (
    AUDIO_ARTIFACT_MAX_BYTES,
    ArtifactDestinationRejectedError,
    ArtifactTooLargeError,
)
from lib.backends.audio_backends import (
    AudioCapability,
    AudioSynthesisRequest,
    create_backend,
    get_registered_backends,
    register_backend,
)
from lib.backends.dashscope_shared import extract_audio_url
from lib.backends.providers import PROVIDER_DASHSCOPE
from tests.fakes import captured_openai_clients
from tests.http_capture import capture_http, only_request, request_json
from tests.unit.lib.backends.write_cancel_gate import GatedWritePath, WriteGate, cancel_during_write


class TestRegistry:
    def test_dashscope_auto_registered(self):
        assert PROVIDER_DASHSCOPE in get_registered_backends()

    def test_create_dashscope(self):
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        backend = create_backend(PROVIDER_DASHSCOPE, api_key="sk")
        assert isinstance(backend, DashScopeAudioBackend)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown audio backend"):
            create_backend("nope")

    def test_register_and_create_custom(self):
        from lib.backends.audio_backends import registry as audio_registry
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        marker = DashScopeAudioBackend(api_key="sk")
        try:
            register_backend("fake-audio-test", lambda **_: marker)
            assert create_backend("fake-audio-test") is marker
        finally:
            # Clear global registries to avoid polluting other registry readers.
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
    """DashScope TTS synthesis POST and audio GET.

    respx intercepts real serialized transport requests, covering URL joining/auth headers/body
    encoding rather than mocked-client argument records.
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
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        b = DashScopeAudioBackend(api_key="sk", model="qwen3-tts-flash")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.model == "qwen3-tts-flash"
        assert b.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_default_model(self):
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        b = DashScopeAudioBackend(api_key="sk")
        assert b.model == "qwen3-tts-flash"

    def test_list_voices_returns_nonempty_catalog_with_unique_ids(self):
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        b = DashScopeAudioBackend(api_key="sk")
        voices = b.list_voices()
        assert voices
        ids = [v.id for v in voices]
        assert len(ids) == len(set(ids))
        assert all(v.label for v in voices)
        # voices callers need independent lists on every call, never shared mutable objects.
        assert voices is not b.list_voices()

    async def test_synthesize_request_and_download(self, tmp_path: Path):
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

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
        # Synchronous TTS omits async headers.
        assert submitted.headers["Authorization"] == "Bearer sk"
        assert "X-DashScope-Async" not in submitted.headers
        # Derive /api/v1 and multimodal-generation paths from hosts.
        assert submitted.url.path == "/api/v1/services/aigc/multimodal-generation/generation"
        # Download audio.url from responses.
        assert str(only_request(routes.download).url) == _AUDIO_URL
        # Saved bytes and result fields
        assert out.read_bytes() == b"RIFFwavbytes"
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.model == "qwen3-tts-flash"
        assert result.text == "你好世界"
        assert result.output_path == out

    async def test_speed_param_ignored(self, tmp_path: Path):
        # speed applies only to realtime; synchronous models ignore it without errors or payload fields.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes() as routes:
            b = DashScopeAudioBackend(api_key="sk")
            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "s.wav", voice="Ethan", speed=1.5)
            )

        body = request_json(only_request(routes.synthesize))
        assert "speed" not in body["input"]
        assert "speech_rate" not in body["input"]

    async def test_http_error_raises(self, tmp_path: Path):
        # Propagate 4xx HTTPStatusError without response bodies, matching other backends; submissions are nonretryable by status.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(synth=httpx.Response(400, text="bad request")) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="Cherry"))

        # Status-based 4xx fail-fast sends billed synthesis POST once, without triggering downloads.
        assert type(excinfo.value) is httpx.HTTPStatusError
        assert "bad request" not in str(excinfo.value)
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 0

    async def test_submit_4xx_with_transient_substring_no_retry(self, tmp_path: Path, poll_clock):
        # URL/task_id 503 substrings in 4xx errors used to trigger mistaken retries until timeout;
        # new predicates inspect response.status_code only and fail fast at 400: one billed POST, no download.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        # Host 503 injects transient substrings into exception text, matching real URL/task_id numbers.
        host = "https://dashscope-503.example.com"
        with _dashscope_audio_routes(
            synth=httpx.Response(400, text="bad request"),
            synth_url=f"{host}/api/v1/services/aigc/multimodal-generation/generation",
        ) as routes:
            b = DashScopeAudioBackend(api_key="sk", base_url=host)
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.synthesize(AudioSynthesisRequest(text="x", output_path=tmp_path / "e.wav", voice="Cherry"))

        # Verify actual transient substrings, old fallback’s false-retry precondition; new predicates fail fast once by status.
        assert "503" in str(ei.value)
        assert ei.value.response.status_code == 400
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 0

    async def test_download_failure_does_not_rebill_synthesis(self, tmp_path: Path, poll_clock):
        # Transient download failures retry GET only, never paid synthesis POST.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(
            download=[httpx.ConnectError("transient"), httpx.Response(200, content=b"ok")]
        ) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "d.wav"
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        # One synthesis POST avoids duplicate billing; GET succeeds on its second attempt.
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 2
        assert out.read_bytes() == b"ok"

    async def test_empty_download_retried_then_rejected_no_file(self, tmp_path: Path, poll_clock):
        # Empty HTTP 200 bodies are transient: exhaust shared download budgets without zero-byte wav writes;
        # never replay synthesis POST.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
        from lib.backends.backend_runtime import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        with _dashscope_audio_routes(download=httpx.Response(200, content=b"")) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空内容"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="Cherry"))

        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES
        assert not out.exists()

    async def test_empty_download_transient_recovers(self, tmp_path: Path, poll_clock):
        # After one empty body, retry saves bytes without synthesis replay.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

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
        # Download 4xx propagates HTTPStatusError without files/retries or synthesis replay;
        # exception text omits presigned queries, equivalent to credentials during validity.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

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

    async def test_download_write_settles_through_cancellation(self, tmp_path: Path):
        # Provider returned audio bytes; cancellation during staged writes lets writes settle and consumes cancellation,
        # so MediaGenerator.generate_audio_async commits/finally cleanup occur only afterward.
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        gate = WriteGate()
        out = GatedWritePath(tmp_path / "gated.wav").attach(gate)
        written = tmp_path / "gated.wav"

        with _dashscope_audio_routes(download=httpx.Response(200, content=b"RIFFwavbytes")):
            b = DashScopeAudioBackend(api_key="sk")
            steps = await cancel_during_write(
                lambda: b.synthesize(AudioSynthesisRequest(text="你好", output_path=out, voice="Cherry")), gate
            )

        assert steps == ["follow-up"]
        assert written.read_bytes() == b"RIFFwavbytes"

    async def test_download_over_audio_limit_is_rejected_without_retry(self, tmp_path: Path, poll_clock):
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        oversized = httpx.Response(200, headers={"Content-Length": str(AUDIO_ARTIFACT_MAX_BYTES + 1)}, content=b"")
        with _dashscope_audio_routes(download=oversized) as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "big.wav"
            with pytest.raises(ArtifactTooLargeError):
                await b.synthesize(AudioSynthesisRequest(text="你好", output_path=out, voice="Cherry"))

        assert routes.download.call_count == 1
        assert not out.exists()

    async def test_download_to_link_local_address_is_rejected(self, tmp_path: Path, poll_clock):
        from lib.backends.audio_backends.dashscope import DashScopeAudioBackend

        with _dashscope_audio_routes(audio_url="http://169.254.169.254/out.wav") as routes:
            b = DashScopeAudioBackend(api_key="sk")
            out = tmp_path / "o.wav"
            with pytest.raises(ArtifactDestinationRejectedError):
                await b.synthesize(AudioSynthesisRequest(text="你好", output_path=out, voice="Cherry"))

        assert routes.download.call_count == 0
        assert not out.exists()


class _RecordingSpeechClient:
    """OpenAI-compatible TTS client substitute records streamed speech.create arguments and returns fixed bytes.

    Assert wire arguments/output formats through ``requests`` contents, never substitute callables.
    """

    def __init__(self, content: bytes = b"RIFFwavbytes", headers: dict[str, str] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._content = content
        self._headers = headers
        streaming = SimpleNamespace(create=self._create)
        self.audio = SimpleNamespace(speech=SimpleNamespace(with_streaming_response=streaming))

    @asynccontextmanager
    async def _create(self, **kwargs: Any) -> AsyncGenerator[SimpleNamespace]:
        self.requests.append(kwargs)
        yield SimpleNamespace(http_response=httpx.Response(200, headers=self._headers, content=self._content))


def _mock_speech_client(
    content: bytes = b"RIFFwavbytes", headers: dict[str, str] | None = None
) -> _RecordingSpeechClient:
    return _RecordingSpeechClient(content, headers)


class TestOpenAIAudioBackend:
    async def test_synthesize_request_and_bytes(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", base_url="https://relay.example.com/v1", model="tts-1")
            out = tmp_path / "o.wav"
            result = await b.synthesize(AudioSynthesisRequest(text="你好世界", output_path=out, voice="alloy"))

        kwargs = mock_client.requests[-1]
        assert kwargs["model"] == "tts-1"
        assert kwargs["input"] == "你好世界"
        assert kwargs["voice"] == "alloy"
        # Output format follows file extensions, canonically .wav.
        assert kwargs["response_format"] == "wav"
        # Saved bytes and result fields
        assert out.read_bytes() == b"RIFFwavbytes"
        assert result.model == "tts-1"
        assert result.text == "你好世界"
        assert result.output_path == out

    async def test_write_settles_through_cancellation(self, tmp_path: Path):
        # Synthesis billed/bytes received: cancellation during writes settles first and is consumed, allowing normal later artifact commits.
        gate = WriteGate()
        out = GatedWritePath(tmp_path / "o.wav").attach(gate)
        written = tmp_path / "o.wav"
        with captured_openai_clients(_mock_speech_client()):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            steps = await cancel_during_write(
                lambda: b.synthesize(AudioSynthesisRequest(text="你好", output_path=out, voice="alloy")), gate
            )

        assert steps == ["follow-up"]
        assert written.read_bytes() == b"RIFFwavbytes"

    def test_metadata(self):
        with captured_openai_clients():
            from lib.backends.audio_backends.openai import OpenAIAudioBackend
            from lib.backends.providers import PROVIDER_OPENAI

            b = OpenAIAudioBackend(api_key="sk", model="gpt-4o-mini-tts")
            assert b.name == PROVIDER_OPENAI
            assert b.model == "gpt-4o-mini-tts"
            # Forward speaking rates to /v1/audio/speech, supporting settings rate inputs.
            assert b.capabilities == {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}

    def test_provider_name_override(self):
        # Custom-provider wrappers can attribute accounting to real providers.
        with captured_openai_clients():
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1", provider_name="custom-7")
            assert b.name == "custom-7"

    def test_list_voices_returns_official_catalog(self):
        with captured_openai_clients():
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="gpt-4o-mini-tts")
            voices = b.list_voices()
            ids = {v.id for v in voices}
            assert {"alloy", "marin", "cedar"} <= ids
            assert len(ids) == len(voices)

    def test_list_voices_excludes_unsupported_ids_for_legacy_models(self):
        with captured_openai_clients():
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            for legacy_model in ("tts-1", "tts-1-hd"):
                b = OpenAIAudioBackend(api_key="sk", model=legacy_model)
                ids = {v.id for v in b.list_voices()}
                assert ids.isdisjoint({"ballad", "verse", "marin", "cedar"})
                assert "alloy" in ids

    def test_list_voices_returns_full_catalog_for_custom_openai_tts_endpoint(self):
        """Custom openai-tts endpoints outside official legacy sets retain compatibility without extra narrowing."""
        with captured_openai_clients():
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="fish-audio-v1", provider_name="custom-7")
            ids = {v.id for v in b.list_voices()}
            assert {"ballad", "verse", "marin", "cedar"} <= ids

    def test_list_voices_legacy_narrowing_only_applies_to_official_openai(self):
        """Custom tts-1/tts-1-hd names do not prove official voice restrictions. Apply legacy narrowing
        only to official provider_name openai, never unverifiable third-party endpoints."""
        with captured_openai_clients():
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            for legacy_model in ("tts-1", "tts-1-hd"):
                b = OpenAIAudioBackend(api_key="sk", model=legacy_model, provider_name="custom-7")
                ids = {v.id for v in b.list_voices()}
                assert {"ballad", "verse", "marin", "cedar"} <= ids

    async def test_speed_passthrough_and_omitted_when_none(self, tmp_path: Path):
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "a.wav", voice="alloy"))
            assert "speed" not in mock_client.requests[-1]

            await b.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "b.wav", voice="alloy", speed=1.5)
            )
            assert mock_client.requests[-1]["speed"] == 1.5

    async def test_language_type_not_sent(self, tmp_path: Path):
        # /v1/audio/speech has no DashScope-specific language field; never include it.
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

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
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            await b.synthesize(AudioSynthesisRequest(text="hi", output_path=tmp_path / "x.bin", voice="alloy"))
        assert mock_client.requests[-1]["response_format"] == "wav"

    async def test_empty_body_rejected_no_file_no_rebill(self, tmp_path: Path):
        # Empty HTTP 200 creates no zero-byte files and never retries, avoiding repeat billing.
        mock_client = _mock_speech_client(content=b"")
        with captured_openai_clients(mock_client):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out = tmp_path / "empty.wav"
            with pytest.raises(RuntimeError, match="空响应体"):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="alloy"))

        assert len(mock_client.requests) == 1
        assert not out.exists()

    async def test_write_failure_does_not_rebill_synthesis(self, tmp_path: Path, poll_clock):
        # Transient disk failures with retryable messages must not replay billed synthesis.
        mock_client = _mock_speech_client()
        with captured_openai_clients(mock_client):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out_dir = tmp_path / "missing-dir"
            # Missing parents make write_bytes raise OSError; inject "timed out" for the worst case.
            req = AudioSynthesisRequest(text="hi", output_path=out_dir / "o.wav", voice="alloy")
            with (
                patch.object(type(req.output_path), "write_bytes", side_effect=OSError("Connection timed out")),
                pytest.raises(OSError, match="Connection timed out"),
            ):
                await b.synthesize(req)

        assert len(mock_client.requests) == 1, "写盘失败不得重跑计费的合成调用"

    async def test_body_over_audio_limit_is_rejected_without_rebill(self, tmp_path: Path):
        mock_client = _mock_speech_client(headers={"Content-Length": str(AUDIO_ARTIFACT_MAX_BYTES + 1)})
        with captured_openai_clients(mock_client):
            from lib.backends.audio_backends.openai import OpenAIAudioBackend

            b = OpenAIAudioBackend(api_key="sk", model="tts-1")
            out = tmp_path / "big.wav"
            with pytest.raises(ArtifactTooLargeError):
                await b.synthesize(AudioSynthesisRequest(text="hi", output_path=out, voice="alloy"))

        assert len(mock_client.requests) == 1
        assert not out.exists()
