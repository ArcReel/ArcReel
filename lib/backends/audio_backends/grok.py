"""GrokAudioBackend — xAI 同步语音合成后端（``POST https://api.x.ai/v1/tts``）。

请求体没有 ``model`` 字段，默认响应直接返回音频字节（无需二段下载），价格按字符计。
schema 依据 docs/api-docs/endpoints/grok-tts.md 所列官方文档。只服务内置 ``grok`` 供应商。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.audio_profiles import endpoint_profile
from lib.backends.backend_runtime import should_retry_submit, submit_post
from lib.backends.grok_shared import resolve_grok_api_key
from lib.backends.providers import PROVIDER_GROK
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

_TTS_URL = "https://api.x.ai/v1/tts"

DEFAULT_MODEL = "grok-tts"

_HTTP_TIMEOUT_SECONDS = 900.0


_SUPPORTED_CODECS = frozenset({"mp3", "wav", "pcm"})
_FALLBACK_CODEC = "wav"

_LANGUAGE_CODES = {
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "russian": "ru",
    "italian": "it",
}
_AUTO_LANGUAGE = "auto"


def _codec_for(output_path: Path) -> str:
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_CODECS else _FALLBACK_CODEC


def _language_for(request: AudioSynthesisRequest) -> str:
    """把内部语种名换成官方语言码；缺省值交给上游自动识别。"""
    return _LANGUAGE_CODES.get(request.language_type.strip().lower(), _AUTO_LANGUAGE)


def _ensure_audio_body(resp: httpx.Response) -> bytes:
    """取出响应里的音频字节；空响应或 JSON、文本响应抛 RuntimeError，不落成产物。"""
    content_type = resp.headers.get("content-type", "").lower()
    if content_type.startswith(("application/json", "text/")):
        raise RuntimeError(f"xAI 语音合成返回了非音频响应：{content_type}")
    audio = resp.content
    if not audio:
        raise RuntimeError("xAI 语音合成返回空响应体")
    return audio


class GrokAudioBackend:
    """xAI 同步语音合成后端（``/v1/tts``）。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = resolve_grok_api_key(api_key)
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}

    def list_voices(self) -> list[VoiceOption]:
        return list(endpoint_profile("grok-tts", self._model).voices)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        endpoint_profile("grok-tts", self._model).validate(request.voice, request.speed)
        audio = await self._request_speech(request)
        await run_noninterruptible_sync(request.output_path.write_bytes, audio)
        logger.info("xAI 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=PROVIDER_GROK,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_speech(self, request: AudioSynthesisRequest) -> bytes:
        payload: dict[str, Any] = {
            "text": request.text,
            "voice_id": request.voice,
            "language": _language_for(request),
            "output_format": {"codec": _codec_for(request.output_path)},
        }
        if request.speed is not None:
            payload["speed"] = request.speed
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s codec=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            payload["output_format"]["codec"],
            len(request.text),
        )
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await submit_post(
                lambda: client.post(
                    _TTS_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ),
                provider=self.name,
                include_provider_reason=False,
            )
        return _ensure_audio_body(resp)
