"""DoubaoAudioBackend — 火山引擎豆包语音合成后端（seed-icl 大模型 TTS）。

走火山 TTS v3 HTTP Chunked 单向流式端点：POST /api/v3/tts/unidirectional，新版鉴权用
X-Api-Key + X-Api-Resource-Id header（resource_id 即模型版本，如 seed-icl-2.0 声音复刻）。
speaker 是复刻音色 ID（由 AudioSynthesisRequest.voice 承载），响应为流式 JSON，data 字段
携带 base64 音频分片，拼接解码后落盘 mp3。
"""

from __future__ import annotations

import base64
import json
import logging

import httpx

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.doubao_shared import doubao_base_url
from lib.providers import PROVIDER_DOUBAO
from lib.retry import with_retry_async
from lib.video_backends.base import should_retry_submit

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE_ID = "seed-icl-2.0"

_UNIDIRECTIONAL_ENDPOINT = "/tts/unidirectional"

# 成功结束码（中枢返回 code=0 携带音频分片，code=20000000 表示合成结束）。
_SUCCESS_END_CODE = 20000000


class DoubaoAudioBackend:
    """火山豆包语音合成后端（V3 流式）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        resource_id: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        if api_key is None or not api_key.strip():
            raise ValueError("请到系统配置页填写豆包 TTS API Key")
        self._api_key = api_key.strip()
        self._resource_id = (resource_id or "").strip() or DEFAULT_RESOURCE_ID
        self._base_url = doubao_base_url(base_url)
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_DOUBAO

    @property
    def model(self) -> str:
        return self._resource_id

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        # 声音复刻的音色由用户在控制台复刻得到，无内置目录；speaker 经 narration_voice 配置注入。
        return []

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        audio_bytes = await self._request_synthesis(request)
        request.output_path.write_bytes(audio_bytes)
        logger.info("豆包语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=PROVIDER_DOUBAO,
            model=self._resource_id,
            characters=len(request.text),
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_synthesis(self, request: AudioSynthesisRequest) -> bytes:
        """提交流式合成请求（计费段），拼接 base64 音频分片返回 mp3 字节。"""
        url = f"{self._base_url}{_UNIDIRECTIONAL_ENDPOINT}"
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "Content-Type": "application/json",
        }
        body = {
            "user": {"uid": "arcreel"},
            "req_params": {
                "text": request.text,
                "speaker": request.voice,
                "audio_params": {"format": "mp3", "sample_rate": 24000},
            },
        }

        logger.info(
            "调用 %s 语音合成 API resource_id=%s speaker=%s chars=%d",
            self.name,
            self._resource_id,
            request.voice,
            len(request.text),
        )

        chunks: list[bytes] = []
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    code = payload.get("code")
                    if code == _SUCCESS_END_CODE:
                        break
                    data = payload.get("data")
                    if code == 0 and isinstance(data, str) and data:
                        chunks.append(base64.b64decode(data))

        if not chunks:
            raise RuntimeError("豆包语音合成返回空音频")
        return b"".join(chunks)
