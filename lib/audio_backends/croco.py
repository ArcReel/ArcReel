"""CrocoAudioBackend — Croco GPU 音乐生成后端（MiniMax Music 3）。

走 Croco 统一任务协议：POST /api/v2/jobs（audio.generate）→ 轮询到终态 → 下载 audio 产物。
MiniMax Music 3 是音乐/BGM 生成，不接受输入素材；caption 由 AudioSynthesisRequest.text 承载。
"""

from __future__ import annotations

import logging

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.croco_shared import CrocoClient
from lib.providers import PROVIDER_CROCO

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "minimax-music-3"

_OPERATION = "audio.generate"
_CONTRACT_VERSION = "1"


class CrocoAudioBackend:
    """Croco 音乐后端（MiniMax Music 3，统一任务协议异步任务同步封装）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._client = CrocoClient(token=api_key, base_url=base_url, http_timeout=http_timeout)
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_CROCO

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        return []

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        parameters = {"caption": request.text}
        job = await self._client.submit_job(
            model_id=self._model,
            operation=_OPERATION,
            contract_version=_CONTRACT_VERSION,
            parameters=parameters,
        )
        job_id = job["job_id"]

        terminal = await self._client.wait_until_terminal(job_id)
        if terminal.get("status") != "succeeded":
            raise RuntimeError(f"Croco 音乐任务未成功: status={terminal.get('status')} error={terminal.get('error')}")

        outputs = await self._client.list_outputs(job_id)
        for item in outputs.get("items", []):
            if item.get("output_id") == "audio" and item.get("delivery_state") == "ready":
                break
        else:
            raise RuntimeError("Croco 音乐任务缺少 audio 产物")

        await self._client.download_output(job_id, "audio", request.output_path)
        logger.info("Croco 音乐生成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=PROVIDER_CROCO,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )
