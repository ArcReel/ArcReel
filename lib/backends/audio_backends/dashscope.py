"""DashScopeAudioBackend: synchronous Alibaba Bailian Qwen3-TTS synthesis.

Use native synchronous multimodal-generation/generation: ``input`` directly carries ``text`` / ``voice`` /
``language_type``, unlike image messages. Responses return wav URLs in ``output.audio.url``, 24kHz and
24-hour lifetime, then HTTP GET downloads bytes for persistence. Synchronous TTS omits X-DashScope-Async,
used only for asynchronous image/video two-step requests. Official schema documentation is indexed in
docs/api-docs/providers/dashscope.md.

Other synchronous Bailian endpoints, such as SpeechSynthesizer in ``dashscope_speech_synthesizer``,
share billed POST URL retrieval followed by independently retried downloads. Subclasses replace only
request paths/bodies, voice tables, and usage parsing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from lib.backends.artifact_download_guard import (
    AUDIO_ARTIFACT_MAX_BYTES,
    artifact_http_client,
    buffered_error_response,
    read_body_capped,
)
from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.backend_runtime import (
    should_retry_signed_download,
    should_retry_submit,
    submit_post,
    with_artifact_retry,
)
from lib.backends.dashscope_shared import (
    dashscope_headers,
    dashscope_native_base_url,
    extract_audio_url,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.transport_layer import TransportLayer
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-tts-flash"

_TTS_ENDPOINT = "/services/aigc/multimodal-generation/generation"


# Qwen3-TTS preset ``voice`` subset; sources indexed in docs/api-docs/providers/dashscope.md,
# system preset voices table, sourced from
# https://help.aliyun.com/zh/model-studio/qwen-tts official documentation and Bailian console model markets.
# Other voices are not exposed: the document explicitly selects voices most relevant to ArcReel;
# full official lists have 48 voices. Omit unverified Chinese names/descriptions rather than invent them.
#
# label stores lib/i18n keys (``voice_label_dashscope_*``, lib/i18n/{zh,en,vi}/assets.py),
# never direct copy. Hardcoded Chinese endpoints would show Chinese descriptions to en/vi users, violating
# the convention of backend user-facing text injected through _t: Translator. get_audio_backend_voices
# renders actual localized labels through request-locale Translators.
_VOICE_CATALOG: tuple[VoiceOption, ...] = (
    VoiceOption(id="Cherry", label="voice_label_dashscope_cherry"),
    VoiceOption(id="Serena", label="voice_label_dashscope_serena"),
    VoiceOption(id="Ethan", label="voice_label_dashscope_ethan"),
    VoiceOption(id="Chelsie", label="voice_label_dashscope_chelsie"),
    VoiceOption(id="Nofish", label="voice_label_dashscope_nofish"),
    VoiceOption(id="Jennifer", label="voice_label_dashscope_jennifer"),
    VoiceOption(id="Ryan", label="voice_label_dashscope_ryan"),
    VoiceOption(id="Bellona", label="voice_label_dashscope_bellona"),
    VoiceOption(id="Neil", label="voice_label_dashscope_neil"),
    VoiceOption(id="Elias", label="voice_label_dashscope_elias"),
    VoiceOption(id="Momo", label="voice_label_dashscope_momo"),
    VoiceOption(id="Vivian", label="voice_label_dashscope_vivian"),
    VoiceOption(id="Moon", label="voice_label_dashscope_moon"),
    VoiceOption(id="Maia", label="voice_label_dashscope_maia"),
    VoiceOption(id="Kai", label="voice_label_dashscope_kai"),
    VoiceOption(id="Katerina", label="voice_label_dashscope_katerina"),
    VoiceOption(id="Bella", label="voice_label_dashscope_bella"),
    VoiceOption(id="Eldric Sage", label="voice_label_dashscope_eldric_sage"),
    VoiceOption(id="Vincent", label="voice_label_dashscope_vincent"),
)


class _EmptyDownloadError(RuntimeError):
    """200 with empty body, a transient proxy/CDN anomaly, triggering download retries."""


class DashScopeAudioBackend:
    """Synchronous Alibaba Bailian synthesis backend, multimodal endpoint."""

    #: Synthesis path relative to ``{host}/api/v1``.
    _endpoint_path = _TTS_ENDPOINT

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        return list(_VOICE_CATALOG)

    def _synthesis_input(self, request: AudioSynthesisRequest) -> dict[str, Any]:
        """Request ``input``: multimodal endpoints accept text, voice, and language only."""
        if request.speed is not None:
            # Synchronous qwen3-tts-flash lacks speech_rate, available only via realtime WebSockets; ignore it.
            logger.debug("DashScope 同步 TTS 不支持语速参数 speed=%s，已忽略", request.speed)
        return {
            "text": request.text,
            "voice": request.voice,
            "language_type": request.language_type,
        }

    def _usage_tokens(self, response: dict[str, Any]) -> tuple[int | None, int | None]:
        """Extract input/audio-output token counts; qwen3-tts charges characters and reports no tokens."""
        return None, None

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # Retry billed synthesis and downloads separately: transient downloads retry GET only, never repeat billed
        # synthesis POST, matching lib.infra.retry.DOWNLOAD_* guidance not to waste generation quota.
        response = await self._request_synthesis(request)
        # Read usage before downloads: failed downloads fail the whole call without accounting; extraction placement does not change pricing.
        input_tokens, audio_output_tokens = self._usage_tokens(response)
        await self._download_audio(extract_audio_url(response), request.output_path)

        logger.info("DashScope 语音合成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
            input_tokens=input_tokens,
            audio_output_tokens=audio_output_tokens,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_synthesis(self, request: AudioSynthesisRequest) -> dict[str, Any]:
        """Submit billed synthesis and return response bodies; missing output.audio.url raises here."""
        payload = {"model": self._model, "input": self._synthesis_input(request)}
        # safe_body_for_log emits whitelisted model + parameters only, never reads input.text;
        # synthesis text stays out of logs, making CodeQL clear-text-logging warnings false positives here.
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s language=%s chars=%d body=%s",
            self.name,
            self._model,
            request.voice,
            request.language_type,
            len(request.text),
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            # Billed synthesis POST is nonidempotent: submit_post converts ambiguous transport failures with possibly delivered requests
            # into terminal AmbiguousSubmitError, preventing double billing. >=400 logs bodies/raises HTTPStatusError with status_code,
            # then should_retry_submit routes statuses: 4xx fail fast; 5xx/429 retry.
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{self._endpoint_path}",
                    json=payload,
                    headers=dashscope_headers(self._api_key),
                ),
                provider=PROVIDER_DASHSCOPE,
                include_provider_reason=False,
            )
            body = resp.json()
            # Missing URLs, including top-level business failures, raise within the billed phase.
            extract_audio_url(body)
            return body

    async def _download_audio(self, url: str, output_path: Path) -> None:
        """Download synthesized audio, the nonbilling phase, under shared artifact-download budgets.

        GET is idempotent: HTTPStatusError uses ``should_retry_signed_download`` status gates; signed-URL
        4xx failures deterministically fail fast. Retry 5xx/transport/network failures, fail fast on business
        errors, and separately retry transient 200-empty-body ``_EmptyDownloadError``.
        """
        content = await with_artifact_retry(
            lambda: self._fetch_audio(url),
            label="DashScope 音频",
            retry_if=lambda e: isinstance(e, _EmptyDownloadError) or should_retry_signed_download(e),
        )
        # Persist outside retry wall-clock budgets; noninterruptible thread calls inside asyncio.timeout would defeat timeouts.
        # Cancellation during persistence lets threads finish and disappears. MediaGenerator.generate_audio_async finally staging cleanup
        # and later artifact commits happen after write settlement (docs/standards/backend.md,
        # provider postresponse accounting sections must be noninterruptible).
        await run_noninterruptible_sync(output_path.write_bytes, content)

    async def _fetch_audio(self, url: str) -> bytes:
        # Logs/exceptions include query-stripped URLs only; unexpired signed parameters are download credentials.
        safe_url = url.split("?", 1)[0]
        async with (
            artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code >= 400:
                logger.warning("DashScope 音频下载返回 %s: %s", resp.status_code, safe_url)
                # Avoid raise_for_status, whose errors contain complete signed URLs.
                # Construct errors manually, preserving types/.response.status_code while messages carry redacted URLs.
                raise httpx.HTTPStatusError(
                    f"DashScope 音频下载返回 {resp.status_code}: {safe_url}",
                    request=resp.request,
                    response=await buffered_error_response(resp),
                )
            content = await read_body_capped(resp, max_bytes=AUDIO_ARTIFACT_MAX_BYTES)
        if not content:
            # 200 with empty body: never write zero-byte wav.
            raise _EmptyDownloadError(f"DashScope 音频下载返回空内容: {safe_url}")
        return content
