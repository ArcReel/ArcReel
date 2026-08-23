"""CrocoVideoBackend — Croco GPU 视频生成后端（MiniMax H3）。

走 Croco 统一任务协议：POST /api/v2/jobs（video.generate）→ 轮询到终态 → 下载 video 产物。
H3 V2 合同用 quality 同时表达画幅与清晰度：ArcReel 对外的 resolution + aspect_ratio
会在提交前转为中枢的 profile token。T2V / I2V（首帧）/ R2V（参考图 ≤9 +
参考音频 ≤3）三条路径共用统一任务信封，仅 mode 与 inputs 不同。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lib.croco_shared import CrocoClient
from lib.providers import PROVIDER_CROCO
from lib.task_execution_progress import (
    h3_execution_progress,
    h3_progress_from_provider,
    persist_h3_execution_progress,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ReferenceAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "minimax-h3"

_OPERATION = "video.generate"
_CONTRACT_VERSION = "2"

# Croco H3 的 quality 不是单纯分辨率，而是「画幅 × 清晰度」输出 profile。
# 公开给 ArcReel 用户的三档是 480p / 0.7M / 720p；中枢内部的 720p profile
# 以实际短边 768 命名，这是协议 token，不应直接暴露到项目设置。
_QUALITY_BY_ASPECT_AND_RESOLUTION = {
    "16:9": {
        "480p": "preview",
        "0.7m": "base_0_7mp",
        "720p": "base_768p",
    },
    "9:16": {
        "480p": "portrait_preview",
        "0.7m": "portrait_0_7mp",
        "720p": "portrait_768p",
    },
    "4:3": {
        "480p": "standard_480p",
        "0.7m": "standard_0_7mp",
        "720p": "standard_768p",
    },
    "3:4": {
        "480p": "standard_portrait_480p",
        "0.7m": "standard_portrait_0_7mp",
        "720p": "standard_portrait_768p",
    },
}
_RESOLUTION_ALIASES = {
    "480p": "480p",
    "0.7m": "0.7m",
    "0.7mp": "0.7m",
    ".7m": "0.7m",
    "720p": "720p",
    "768p": "720p",
}


def _resolve_quality(resolution: str | None, aspect_ratio: str) -> str:
    """ArcReel 分辨率 + 画幅 → Croco H3 quality；未显式选档时走中枢 0.7M 默认。"""
    raw_resolution = (resolution or "0.7M").strip()
    normalized_resolution = _RESOLUTION_ALIASES.get(raw_resolution.lower())
    profiles = _QUALITY_BY_ASPECT_AND_RESOLUTION.get(aspect_ratio)
    if normalized_resolution is None or profiles is None:
        raise VideoCapabilityError(
            "video_output_profile_unsupported",
            model=DEFAULT_MODEL,
            resolution=raw_resolution or "Auto",
            aspect_ratio=aspect_ratio,
            supported="480p, 0.7M, 720p @ 16:9/9:16/4:3/3:4",
        )
    return profiles[normalized_resolution]


class CrocoVideoBackend(ProviderJobIdPersistenceMixin):
    """Croco 视频后端（MiniMax H3，统一任务协议异步三阶段）。"""

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

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """H3 能力：首帧 + 参考图 ≤9 + 参考音频 ≤3（DIRECT），提示词 ≤20000 字符。"""
        return VideoCapabilities(
            first_frame=True,
            max_reference_images=9,
            reference_audio_mode=ReferenceAudioMode.DIRECT,
            max_reference_audio_count=3,
            max_prompt_chars=20000,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        mode, inputs = await self._build_mode_and_inputs(request)

        parameters = {
            "mode": mode,
            "prompt": request.prompt,
            "quality": _resolve_quality(request.resolution, request.aspect_ratio),
            "duration_seconds": request.duration_seconds,
        }
        job = await self._client.submit_job(
            model_id=self._model,
            operation=_OPERATION,
            contract_version=_CONTRACT_VERSION,
            parameters=parameters,
            inputs=inputs,
        )
        job_id = job["job_id"]

        # worker 路径持久化 job_id，重启可接续（resume_video 轮询 + 下载，不重新 submit）。
        await self._persist_provider_job_id(request, job_id, provider=PROVIDER_CROCO)
        await persist_h3_execution_progress(
            request.task_id,
            h3_progress_from_provider(job),
        )

        return await self._poll_and_download(job_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        return await self._poll_and_download(job_id, request)

    async def _poll_and_download(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        async def _on_update(job: dict, queue: dict | None) -> None:
            await persist_h3_execution_progress(
                request.task_id,
                h3_progress_from_provider(job, queue),
            )

        try:
            terminal = await self._client.wait_until_terminal(job_id, on_update=_on_update)
        except asyncio.CancelledError:
            await persist_h3_execution_progress(
                request.task_id,
                h3_execution_progress(
                    "cancelling",
                    provider_status="canceling",
                    can_cancel=False,
                ),
            )
            try:
                await asyncio.shield(self._client.cancel_job(job_id))
            except Exception:
                logger.exception("Croco 远端任务取消失败 job_id=%s", job_id)
            raise
        if terminal.get("status") != "succeeded":
            raise RuntimeError(f"Croco 视频任务未成功: status={terminal.get('status')} error={terminal.get('error')}")

        outputs = await self._client.list_outputs(job_id)
        video_uri = None
        seed = None
        for item in outputs.get("items", []):
            if item.get("output_id") == "video" and item.get("delivery_state") == "ready":
                video_uri = item.get("content_url")
                metadata = item.get("metadata")
                if isinstance(metadata, dict) and isinstance(metadata.get("seed"), int):
                    seed = metadata["seed"]
                break
        if video_uri is None:
            raise RuntimeError("Croco 视频任务缺少 video 产物")

        await self._client.download_output(job_id, "video", request.output_path)
        await persist_h3_execution_progress(
            request.task_id,
            h3_execution_progress(
                "completed",
                provider_status="succeeded",
                stage=terminal.get("stage") if isinstance(terminal.get("stage"), str) else None,
                progress=100,
                can_cancel=False,
            ),
        )
        logger.info("Croco 视频生成完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_CROCO,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_uri,
            seed=seed,
            generate_audio=True,
        )

    async def _build_mode_and_inputs(self, request: VideoGenerationRequest) -> tuple[str, list[dict]]:
        """按请求素材判定 mode，并上传素材构建 inputs 列表。

        优先级：有参考图/参考音频 → r2v；否则有首帧 → i2v；否则 t2v（H3 合同约束，见中枢文档 4.3）。
        """
        reference_images = request.reference_images or []
        reference_audio = request.reference_audio_files or []

        if reference_images or reference_audio:
            inputs: list[dict] = []
            for img in reference_images:
                asset_id = await self._client.upload_image(Path(img))
                inputs.append({"role": "reference_image", "asset_id": asset_id})
            for aud in reference_audio:
                asset_id = await self._client.upload_audio(Path(aud))
                inputs.append({"role": "reference_audio", "asset_id": asset_id})
            return "r2v", inputs

        if request.start_image is not None:
            asset_id = await self._client.upload_image(Path(request.start_image))
            return "i2v", [{"role": "first_frame", "asset_id": asset_id}]

        return "t2v", []
