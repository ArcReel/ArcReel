"""DashScopeAudioBackend — 阿里百炼 Qwen3-TTS 语音合成后端（同步）。

走原生 multimodal-generation/generation 同步端点：请求体 ``input`` 直接携带
``text`` / ``voice`` / ``language_type``（区别于图像的 messages 结构），响应在
``output.audio.url`` 给出 wav 文件 URL（24kHz，24h 有效），再 HTTP GET 下载字节落盘。
TTS 同步调用不带 X-DashScope-Async 头（该头仅图像/视频异步两步式使用）。
schema 依据 docs/api-docs/providers/dashscope.md 所列一手官方文档。

百炼的其他同步语音合成端点（如 SpeechSynthesizer，见 ``dashscope_speech_synthesizer``）沿用同一套
「计费 POST 取 URL → 独立重试下载」流程，子类只换请求路径、请求体、音色表与用量解析。
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
from lib.speech.wav import normalize_wav_header

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-tts-flash"

_TTS_ENDPOINT = "/services/aigc/multimodal-generation/generation"


_QWEN3_TTS_FLASH = "qwen3-tts-flash"
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
    """200 但空响应体（瞬时代理/CDN 异常），视为瞬态触发下载重试。"""


def _write_audio(output_path: Path, content: bytes) -> None:
    output_path.write_bytes(normalize_wav_header(content))


class DashScopeAudioBackend:
    """阿里百炼语音合成后端（同步 multimodal 端点）。"""

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
        return list(_VOICE_CATALOG) if self._model == _QWEN3_TTS_FLASH else []

    def _synthesis_input(self, request: AudioSynthesisRequest) -> dict[str, Any]:
        """请求体 ``input`` 段。multimodal 端点只收文本、音色与语种。"""
        if request.speed is not None:
            logger.debug("DashScope 同步 TTS 不支持语速参数 speed=%s，已忽略", request.speed)
        return {
            "text": request.text,
            "voice": request.voice,
            "language_type": request.language_type,
        }

    def _usage_tokens(self, response: dict[str, Any]) -> tuple[int | None, int | None]:
        """从合成响应取 (输入 token, 输出音频 token)。qwen3-tts 按字符计价，不报 token。"""
        return None, None

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        response = await self._request_synthesis(request)
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
        """提交合成请求（计费段），返回响应体；响应缺 output.audio.url 时在此抛错。"""
        payload = {"model": self._model, "input": self._synthesis_input(request)}
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
            extract_audio_url(body)
            return body

    async def _download_audio(self, url: str, output_path: Path) -> None:
        """下载合成音频（非计费段），走共用的产物下载预算。

        下载是幂等 GET：HTTPStatusError 按 status_code 闸门（``should_retry_signed_download``，
        预签发 URL 的 4xx 是确定性错误一律 fail-fast），5xx/传输/网络错误重试，业务错误
        fail-fast；200-空体（``_EmptyDownloadError``）属瞬态另行重试。
        """
        content = await with_artifact_retry(
            lambda: self._fetch_audio(url),
            label="DashScope 音频",
            retry_if=lambda e: isinstance(e, _EmptyDownloadError) or should_retry_signed_download(e),
        )
        await run_noninterruptible_sync(_write_audio, output_path, content)

    async def _fetch_audio(self, url: str) -> bytes:
        safe_url = url.split("?", 1)[0]
        async with (
            artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code >= 400:
                logger.warning("DashScope 音频下载返回 %s: %s", resp.status_code, safe_url)
                raise httpx.HTTPStatusError(
                    f"DashScope 音频下载返回 {resp.status_code}: {safe_url}",
                    request=resp.request,
                    response=await buffered_error_response(resp),
                )
            content = await read_body_capped(resp, max_bytes=AUDIO_ARTIFACT_MAX_BYTES)
        if not content:
            raise _EmptyDownloadError(f"DashScope 音频下载返回空内容: {safe_url}")
        return content
