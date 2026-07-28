"""MiniMaxAudioBackend — MiniMax（海螺）语音合成后端（同步 ``/v1/t2a_v2``）。

走原生 TTS 端点：单次 POST 直接在 ``data.audio`` 返回 hex 编码音频字节，解码落盘。
请求体携带 ``model`` / ``text`` / ``voice_setting`` / ``output_format``（必填 ``model`` 与
``text``），响应在 ``data.audio`` 给出 hex 音频，``base_resp.status_code`` 为 0 表成功。
schema 依据官方 API 参考核实。
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
from lib.minimax_shared import (
    extract_minimax_audio,
    minimax_audio_base_url,
    minimax_headers,
    resolve_minimax_api_key,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import with_retry_async
from lib.video_backends.base import should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "speech-2.8-hd"

_TTS_ENDPOINT = "/t2a_v2"

# t2a_v2 支持的输出格式（官方 schema），用于按落盘扩展名选 output_format。
_SUPPORTED_OUTPUT_FORMATS = frozenset({"mp3", "wav", "flac", "pcm"})
_FALLBACK_OUTPUT_FORMAT = "mp3"


def _output_format_for(output_path: Path) -> str:
    """按落盘扩展名选输出格式，保证文件内容与扩展名一致（资源路径约定 .wav）。"""
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_OUTPUT_FORMATS else _FALLBACK_OUTPUT_FORMAT


class MiniMaxAudioBackend:
    """MiniMax 语音合成后端（同步 ``/v1/t2a_v2`` 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_audio_base_url(base_url)
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
        # language_type 是 DashScope 特有字段，t2a_v2 无对应参数（语种经 language_boost 控制，
        # 由 voice_id 自带语种覆盖），不发送。speed 预留字段暂不透传。
        # 计费调用与写盘分离：重试只包 API 调用，写盘瞬态失败绝不回头重跑会再次计费的合成请求。
        hex_audio = await self._request_synthesis(request)
        await self._write_audio(hex_audio, request.output_path)

        logger.info("MiniMax 语音合成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=PROVIDER_MINIMAX,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_synthesis(self, request: AudioSynthesisRequest) -> str:
        """提交合成请求（计费段），返回 hex 编码音频。"""
        payload = {
            "model": self._model,
            "text": request.text,
            "voice_setting": {"voice_id": request.voice},
            "output_format": _output_format_for(request.output_path),
        }
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            payload["output_format"],
            len(request.text),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # 非幂等的「计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError
            # 终态失败避免重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit
            # 按状态码分流（4xx fail-fast、5xx/429 重试）。
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_TTS_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return extract_minimax_audio(resp.json())

    async def _write_audio(self, hex_audio: str, output_path: Path) -> None:
        """解码 hex 音频并写盘（解码 + 写盘 offload 到线程，避免事件循环内做 CPU 密集解码）。"""
        import asyncio

        def _decode_and_save() -> None:
            audio_bytes = bytes.fromhex(hex_audio)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio_bytes)

        await asyncio.to_thread(_decode_and_save)
