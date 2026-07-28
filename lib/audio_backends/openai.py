"""OpenAIAudioBackend — OpenAI 兼容语音合成后端（同步 ``/v1/audio/speech``）。

请求体携带 ``model`` / ``input`` / ``voice``（必填）与可选 ``response_format`` / ``speed``，
响应直接返回音频字节（无需二段下载）。schema 依据 OpenAI 官方 API 参考核实。
主要服务自定义供应商通路：任意 OpenAI 兼容 TTS（Fish Audio、自托管 shim、中转站）
经 ``openai-tts`` endpoint 包装为 ``CustomAudioBackend`` 后接入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
)
from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

# /v1/audio/speech 支持的输出格式（官方 schema），用于按落盘扩展名选 response_format。
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
        model: str,
        provider_name: str = PROVIDER_OPENAI,
    ) -> None:
        # 禁用 SDK 内置重试，由本层 synthesize() 统一管理重试策略
        self._client = create_openai_client(api_key=api_key, base_url=base_url, max_retries=0)
        self._model = model
        # 复用 OpenAI 兼容协议的 provider（自定义供应商包装层覆盖 name）须用真实 provider 记账
        self._provider_name = provider_name

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # language_type 是 DashScope 特有字段，/v1/audio/speech 无对应参数（语种随输入文本），不发送。
        # 计费调用与写盘分离：重试只包 API 调用，写盘瞬态失败绝不回头重跑会再次计费的合成请求。
        audio_bytes = await self._request_speech(request)
        request.output_path.write_bytes(audio_bytes)

        logger.info("OpenAI 兼容语音合成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            characters=len(request.text),
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
        response = await self._client.audio.speech.create(**kwargs)
        if not response.content:
            # 宽松 shim 可能 200 + 空体；不落 0 字节文件、不计成功。该次合成已在供应商侧
            # 发生，重试等于再次计费，故直接抛错交由任务层失败（重生成廉价）。
            raise RuntimeError("OpenAI 兼容语音合成返回空响应体")
        return response.content
