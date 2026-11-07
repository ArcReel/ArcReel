"""OpenAIAudioBackend: synchronous OpenAI-compatible synthesis (``/v1/audio/speech``).

Requests contain required ``model`` / ``input`` / ``voice`` and optional ``response_format`` / ``speed``.
Responses directly return audio bytes without separate downloads. Verify schema against official OpenAI
API references. Primarily serves custom providers: compatible TTS such as Fish Audio, self-hosted shims,
or relays enters through ``openai-tts`` endpoints wrapped as ``CustomAudioBackend``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.backends.artifact_download_guard import AUDIO_ARTIFACT_MAX_BYTES, read_body_capped
from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

# Official /v1/audio/speech formats, selecting response_format by output extension.
_SUPPORTED_RESPONSE_FORMATS = frozenset({"mp3", "opus", "aac", "flac", "wav", "pcm"})
_FALLBACK_RESPONSE_FORMAT = "wav"

# Official voices for gpt-4o-mini-tts, including tts-1/tts-1-hd legacy subsets; sources in
# docs/api-docs/endpoints/openai-tts.md official OpenAI documentation.
# Official docs omit gender/descriptions; label uses ids only, never invented facts.
# Third-party compatible services behind custom openai-tts endpoints may have different voice sets;
# see boundary explanations in the indexed documentation.
_VOICE_CATALOG: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=voice_id)
    for voice_id in (
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    )
)

# Legacy tts-1 / tts-1-hd exclude four voices supported only by gpt-4o-mini-tts; same sources as above.
# Exposing them for legacy models makes user selections fail deterministically at synthesis.
_LEGACY_MODELS = frozenset({"tts-1", "tts-1-hd"})
_LEGACY_UNSUPPORTED_VOICE_IDS = frozenset({"ballad", "verse", "marin", "cedar"})


def _response_format_for(output_path: Path) -> str:
    """Select formats by output extensions, keeping bytes/extensions consistent with .wav resource paths."""
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_RESPONSE_FORMATS else _FALLBACK_RESPONSE_FORMAT


class OpenAIAudioBackend:
    """Synchronous OpenAI-compatible synthesis backend (``/v1/audio/speech``)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        model: str,
        provider_name: str = PROVIDER_OPENAI,
    ) -> None:
        # Disable SDK retries; synthesize() centrally owns retry policy.
        self._client = create_openai_client(
            api_key=api_key, base_url=base_url, max_retries=0, transport_layer=transport_layer
        )
        self._model = model
        # Compatible providers, whose wrappers override name, must account under actual providers.
        self._provider_name = provider_name

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}

    def list_voices(self) -> list[VoiceOption]:
        # Apply legacy narrowing only to official OpenAI. Custom openai-tts providers with overwritten provider_name
        # may call models tts-1/tts-1-hd without inheriting official voice restrictions. Retain the full catalog,
        # the existing declared compatibility policy (top comments and _build_openai_tts call site).
        if self._provider_name == PROVIDER_OPENAI and self._model in _LEGACY_MODELS:
            return [v for v in _VOICE_CATALOG if v.id not in _LEGACY_UNSUPPORTED_VOICE_IDS]
        return list(_VOICE_CATALOG)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # Omit DashScope language_type: /v1/audio/speech has no such parameter and follows input language.
        # Separate billing calls/writes: API-only retries never repeat billed synthesis for transient write failures.
        audio_bytes = await self._request_speech(request)
        await run_noninterruptible_sync(request.output_path.write_bytes, audio_bytes)

        logger.info("OpenAI 兼容语音合成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _request_speech(self, request: AudioSynthesisRequest) -> bytes:
        """Submit billed synthesis requests, returning audio bytes."""
        kwargs: dict = {
            "model": self._model,
            "input": request.text,
            "voice": request.voice,
            "response_format": _response_format_for(request.output_path),
        }
        if request.speed is not None:
            kwargs["speed"] = request.speed

        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            kwargs["response_format"],
            len(request.text),
        )
        async with self._client.audio.speech.with_streaming_response.create(**kwargs) as response:
            content = await read_body_capped(response.http_response, max_bytes=AUDIO_ARTIFACT_MAX_BYTES)
        if not content:
            # Lenient shims may return 200 with empty bodies: never write zero-byte files or count success. Synthesis already
            # occurred upstream; retry would bill again, so raise for task failure handling (regeneration is inexpensive).
            raise RuntimeError("OpenAI 兼容语音合成返回空响应体")
        return content
