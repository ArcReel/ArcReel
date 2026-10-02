"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from arcreel_market_core.video_backend_contract import (
    IMAGE_MIME_TYPES,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import download_video
from lib.backends.data_uri import image_to_data_uri
from lib.backends.grok_shared import create_grok_client
from lib.backends.providers import PROVIDER_GROK
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)

_MODEL_1_5 = "grok-imagine-video-1.5"

# 全系参考生视频（带参考图，或带尾帧）的分辨率上限：「Reference-to-video is capped at 720p」，且首帧与
# last_frame 组合即参考生视频（https://docs.x.ai/developers/model-capabilities/video/generation）。
# SDK 的分辨率枚举里高于该上限的只有 1080p。
_REFERENCE_ROUTE_MAX_RESOLUTION = "720p"


class GrokVideoBackend:
    """xAI Grok 视频生成后端。"""

    DEFAULT_MODEL = "grok-imagine-video"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 SDK client（无需 api_key）。

        instance property 委托至此，保持 backend 为单一真相源。能力取自 xAI 视频文档
        （https://docs.x.ai/developers/model-capabilities/video/generation 与
        https://docs.x.ai/developers/model-capabilities/video/reference-to-video.md）：

        - 全系支持参考生视频，该路径分辨率上限 720p，由 ``generate`` 请求期校验。参考图上限 7
          取自第三方来源，官方文档未明确列出，1.5 与 1.5-lite 沿用同一值。
        - ``grok-imagine-video-1.5``：另支持 ``last_frame`` 钉住尾帧。参考音频只收预置
          ``voice_id``，自备音频仅向受信合作方开放，表达不了项目的参考音频文件，不声明。
        - ``grok-imagine-video-1.5-lite``：首尾帧官方只写在 1.5 上，不声明尾帧。
        - ``grok-imagine-video``：官方写明拒收 ``last_frame``。

        音轨可开关：请求把音轨意图下发为 SDK 的 ``generate_audio``（缺省有声，``False`` 出无声
        视频），``generate`` 结算按同一下发值记录。
        """
        return VideoCapabilities(
            last_frame=model == _MODEL_1_5, max_reference_images=7, audio_track=VideoAudioMode.CONTROLLABLE
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Grok 同步型 API，无 job_id 可接续；orphan handler 据 NotImplementedError 标 [resume_unsupported]
        raise NotImplementedError("GrokVideoBackend 不支持 resume_video（同步型 API）")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频；黑盒生成不重试，只有已取得 URL 后的下载可以独立重试。"""
        self._check_reference_route_resolution(request)
        # The SDK combines submit and provider-side waiting in one opaque call. Once it starts, an exception
        # cannot prove the provider rejected the request before accepting a paid job, so close MediaGenerator's
        # reference-payload compression retry window before entering it.
        if request.on_provider_resubmit_unsafe is not None:
            request.on_provider_resubmit_unsafe()
        response = await self._create_video(request)

        video_url = response.url
        # SDK 响应字段未类型化，收窄为 int 才能作为实际计费时长落账本的 Integer 列；
        # 先经 float 接受 "15.0" 这类浮点字符串。缺失/不可解析（含 inf/nan）/非正/
        # 超出合理上限的值回落请求时长，保证结果恒为正且可落库。
        raw_duration = getattr(response, "duration", None)
        actual_duration = request.duration_seconds
        try:
            if raw_duration is not None:
                parsed = float(raw_duration)
                # 上下限基于取整前的原始数值判断：86400.9 已超 24h，不得因取整落回上限内被接受
                if 0 < parsed <= MAX_BILLED_DURATION_SECONDS:
                    # half-up 取整与 dashscope extract_billing_duration 同口径，避免截断少计费秒数；
                    # (0, 0.5) 取整到 0 时同样回落，保持结果恒为正
                    rounded = int(parsed + 0.5)
                    if rounded > 0:
                        actual_duration = rounded
        except (TypeError, ValueError, OverflowError):
            # 解析失败属预期内回落（SDK 字段未类型化），保留请求时长即可，无需上抛
            logger.debug("Grok 回报的 duration 无法解析: %r，回落请求时长 %s 秒", raw_duration, actual_duration)

        await download_video(video_url, request.output_path, label="Grok")
        logger.info("Grok 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
            generate_audio=request.generate_audio,
        )

    def _check_reference_route_resolution(self, request: VideoGenerationRequest) -> None:
        """参考生视频路径超出 720p 上限时在付费调用前拒绝，不交给供应商报错或静默降档。"""
        resolution = (request.resolution or "").strip().lower()
        if resolution != "1080p":
            return
        if request.reference_images or request.end_image:
            raise VideoCapabilityError(
                "video_reference_resolution_unsupported",
                model=self._model,
                resolution=resolution,
                max_resolution=_REFERENCE_ROUTE_MAX_RESOLUTION,
            )

    async def _create_video(self, request: VideoGenerationRequest):
        """通过不可判定收单边界的 SDK 调用生成视频。"""
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "generate_audio": request.generate_audio,
            # 轮询在 SDK 内部，仍按请求快照里的全局超时收口，否则该设置独独对 Grok 不生效。
            "timeout": timedelta(seconds=request.poll_timeout_seconds),
            "interval": timedelta(seconds=5),
        }
        if request.resolution is not None:
            generate_kwargs["resolution"] = request.resolution

        if request.start_image and Path(request.start_image).exists():  # noqa: ASYNC240 -- 首帧存在性检查，本地元数据；读图转 data URI 已 to_thread 卸载
            image_path = Path(request.start_image)
            generate_kwargs["image_url"] = await asyncio.to_thread(image_to_data_uri, image_path, IMAGE_MIME_TYPES)

        if request.end_image:
            # 能否带尾帧由 gate_video_request 按 video_capabilities.last_frame 前置判定，到这里只剩 1.5。
            end_path = Path(request.end_image)
            if not end_path.is_file():  # noqa: ASYNC240 -- 尾帧存在性检查，本地元数据；读图转 data URI 已 to_thread 卸载
                # 尾帧缺失不静默跳过：跳过后照常出片计费，成片却落不到分镜要求的结尾画面。
                raise VideoCapabilityError(
                    "video_end_image_unreadable", model=self._model, name=end_path.name or str(end_path)
                )
            generate_kwargs["last_frame_url"] = await asyncio.to_thread(image_to_data_uri, end_path, IMAGE_MIME_TYPES)

        if request.reference_images:
            ref_paths = list(request.reference_images)
            existing_paths = [p for p in ref_paths if p.exists()]
            if existing_paths:
                ref_urls = await asyncio.gather(
                    *[asyncio.to_thread(image_to_data_uri, p, IMAGE_MIME_TYPES) for p in existing_paths]
                )
                generate_kwargs["reference_image_urls"] = list(ref_urls)

        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        return await self._client.video.generate(**generate_kwargs)
