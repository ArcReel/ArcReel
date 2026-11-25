"""OpenAIAudioBackend — OpenAI 兼容语音合成后端（同步 ``/v1/audio/speech``）。

请求体携带 ``model`` / ``input`` / ``voice``（必填）与可选 ``response_format`` / ``speed``，
响应直接返回音频字节（无需二段下载）。schema 依据 OpenAI 官方 API 参考核实。
主要服务自定义供应商通路：任意 OpenAI 兼容 TTS（Fish Audio、自托管 shim、中转站）
经 ``openai-tts`` endpoint 包装为 ``CustomAudioBackend`` 后接入。
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
from lib.backends.audio_profiles import AudioProfile, endpoint_profile
from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.config.url_utils import is_official_openai_base_url
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

_SUPPORTED_RESPONSE_FORMATS = frozenset({"mp3", "opus", "aac", "flac", "wav", "pcm"})
_FALLBACK_RESPONSE_FORMAT = "wav"


def _response_format_for(output_path: Path) -> str:
    """按落盘扩展名选输出格式，保证文件内容与扩展名一致（资源路径约定 .wav）。"""
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_RESPONSE_FORMATS else _FALLBACK_RESPONSE_FORMAT


class OpenAIAudioBackend:
    """OpenAI 兼容语音合成后端（同步 ``/v1/audio/speech``）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        model: str,
        provider_name: str = PROVIDER_OPENAI,
    ) -> None:
        self._client = create_openai_client(
            api_key=api_key, base_url=base_url, max_retries=0, transport_layer=transport_layer
        )
        self._official_host = is_official_openai_base_url(base_url)
        self._model = model
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

    def _profile(self) -> AudioProfile:
        return endpoint_profile("openai-tts", self._model, official_host=self._official_host)

    def list_voices(self) -> list[VoiceOption]:
        return list(self._profile().voices)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        self._profile().validate(request.voice, request.speed)
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
        """提交合成请求（计费段），返回音频字节。"""
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
            raise RuntimeError("OpenAI 兼容语音合成返回空响应体")
        return content
