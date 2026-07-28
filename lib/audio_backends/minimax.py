"""MiniMaxAudioBackend — MiniMax (Hailuo) speech-2.8-hd TTS backend (synchronous /v1/t2a_v2).

Calls the native text-to-audio v2 endpoint: a single POST returns hex-encoded audio
bytes in ``data.audio`` (no separate download step). The response also carries
``base_resp.status_code`` (0 = success). The ``voice`` field maps to
``voice_setting.voice_id``; the output format is derived from the file suffix.
Schema verified against the official T2A v2 API reference.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
)
from lib.logging_utils import format_kwargs_for_log
from lib.minimax_shared import (
    extract_minimax_audio_hex,
    minimax_failure_reason,
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
    safe_body_for_log,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import with_retry_async
from lib.video_backends.base import should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "speech-2.8-hd"

_TTS_ENDPOINT = "/t2a_v2"

# /v1/t2a_v2 supported output formats (official schema).
_SUPPORTED_OUTPUT_FORMATS = frozenset({"mp3", "wav", "flac", "pcm"})
_FALLBACK_OUTPUT_FORMAT = "mp3"


def _output_format_for(output_path: Path) -> str:
    """Pick output format from the file suffix; fall back to mp3 for unknown extensions."""
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_OUTPUT_FORMATS else _FALLBACK_OUTPUT_FORMAT


class MiniMaxAudioBackend:
    """MiniMax speech synthesis backend (synchronous /v1/t2a_v2 endpoint)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # MiniMax T2A returns hex-encoded audio directly in the response body — no separate
        # download step (unlike DashScope which returns a URL). The synthesis POST is the billing
        # step; write failure does not trigger a re-bill of the synthesis call.
        audio_bytes = await self._request_synthesis(request)
        request.output_path.write_bytes(audio_bytes)

        logger.info("MiniMax speech synthesis complete: %s", request.output_path)

        return AudioSynthesisResult(
            provider=PROVIDER_MINIMAX,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_synthesis(self, request: AudioSynthesisRequest) -> bytes:
        """Submit synthesis request (billing step), return decoded audio bytes."""
        payload: dict = {
            "model": self._model,
            "text": request.text,
            "voice_setting": {
                "voice_id": request.voice,
            },
            "output_format": _output_format_for(request.output_path),
        }
        if request.language_type:
            payload["language_boost"] = request.language_type

        # safe_body_for_log only emits whitelisted scalars (model, output_format, language_boost);
        # the input text is never logged (CodeQL clear-text-logging false positive).
        logger.info(
            "Calling %s speech synthesis API model=%s voice=%s format=%s chars=%d body=%s",
            self.name,
            self._model,
            request.voice,
            payload["output_format"],
            len(request.text),
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # Non-idempotent billing POST: submit_post converts ambiguous transport errors into
            # AmbiguousSubmitError (terminal failure, no retry to avoid double billing); >=400
            # raises HTTPStatusError (status_code preserved) for should_retry_submit to gate
            # (4xx fail-fast, 5xx/429 retry).
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_TTS_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            data = resp.json()
            reason = minimax_failure_reason(data)
            if reason:
                raise RuntimeError(reason)
            hex_audio = extract_minimax_audio_hex(data)
            return bytes.fromhex(hex_audio)
