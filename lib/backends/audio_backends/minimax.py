"""MiniMaxAudioBackend: synchronous MiniMax synthesis (``POST {host}/v1/t2a_v2``).

Nonstreaming requests use ``output_format=hex``. Response ``data.audio`` holds hex-encoded bytes in
``audio_setting.format``; decode and persist without separate downloads. Business failures use
HTTP 200 with nonzero ``base_resp.status_code``. Official schema sources are indexed in
docs/api-docs/endpoints/minimax-tts.md. Built-in ``minimax`` and custom ``minimax-tts`` share this channel.
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.backend_runtime import should_retry_submit, submit_post
from lib.backends.minimax_shared import minimax_headers, minimax_text_base_url, resolve_minimax_api_key
from lib.backends.providers import PROVIDER_MINIMAX
from lib.backends.transport_layer import TransportLayer
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "speech-2.8-hd"

# Nonstreaming t2a_v2 format subset selected by output extensions to keep bytes/extensions consistent (.wav resource paths).
_SUPPORTED_FORMATS = frozenset({"mp3", "wav", "flac", "pcm"})
_FALLBACK_FORMAT = "wav"

# Basic Mandarin adult system voices (``voice_setting.voice_id``), sourced from
# docs/api-docs/endpoints/minimax-tts.md system-voice lists. Hundreds of multilingual entries exist; expose only
# common dubbing voices. label holds lib/i18n keys rendered by voice APIs in request locales.
_VOICE_CATALOG: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=f"voice_label_minimax_{voice_id.replace('-', '_')}")
    for voice_id in (
        "male-qn-qingse",
        "male-qn-jingying",
        "male-qn-badao",
        "male-qn-daxuesheng",
        "female-shaonv",
        "female-yujie",
        "female-chengshu",
        "female-tianmei",
    )
)


def _format_for(output_path: Path) -> str:
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_FORMATS else _FALLBACK_FORMAT


def _decode_audio(payload: object) -> bytes:
    """Extract/hex-decode ``data.audio``; raise RuntimeError for nonzero ``base_resp`` or missing audio."""
    body = payload if isinstance(payload, dict) else {}
    base = body.get("base_resp")
    base = base if isinstance(base, dict) else {}
    code = base.get("status_code")
    if code not in (None, 0):
        raise RuntimeError(f"MiniMax 语音合成失败 status_code={code}: {base.get('status_msg', '')}".strip())
    data = body.get("data")
    data = data if isinstance(data, dict) else {}
    audio_hex = data.get("audio")
    if not isinstance(audio_hex, str) or not audio_hex:
        raise RuntimeError("MiniMax 语音合成响应缺少 data.audio")
    try:
        return bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise RuntimeError("MiniMax 语音合成响应的 data.audio 不是合法 hex") from exc


class MiniMaxAudioBackend:
    """Synchronous MiniMax synthesis backend (``t2a_v2``)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider_name: str = PROVIDER_MINIMAX,
        http_timeout: float = 120.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        # Share text {host}/v1 normalization, accepting hosts with or without /v1 suffixes.
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        # Account custom-provider calls under actual providers.
        self._provider_name = provider_name
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

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
        return list(_VOICE_CATALOG)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # Omit DashScope-specific language_type; t2a_v2 detects languages from text.
        # Separate billing calls/writes: retries wrap API calls only; never repeat billed synthesis because persistence failed.
        audio = await self._request_speech(request)
        # Providers already billed; uninterrupted writes form artifacts despite cancellation.
        await run_noninterruptible_sync(request.output_path.write_bytes, audio)
        logger.info("MiniMax 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_speech(self, request: AudioSynthesisRequest) -> bytes:
        voice_setting: dict = {"voice_id": request.voice}
        if request.speed is not None:
            voice_setting["speed"] = request.speed
        payload = {
            "model": self._model,
            "text": request.text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": voice_setting,
            "audio_setting": {"format": _format_for(request.output_path)},
        }
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            payload["audio_setting"]["format"],
            len(request.text),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            # Billed POST: submit_post terminally fails ambiguous transport errors to avoid double billing; route >=400 by status.
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}/t2a_v2",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=self._provider_name,
                include_provider_reason=False,
            )
        # Providers already billed: run full hex parsing/decoding in threads without cancellation interruption.
        return await run_noninterruptible_sync(lambda: _decode_audio(resp.json()))
