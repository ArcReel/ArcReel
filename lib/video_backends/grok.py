"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from lib.data_uri import image_to_data_uri
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.grok_shared import create_grok_client
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_GROK
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoAudioMode,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


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

        当前全系模型能力一致，不按 model_id 分支；instance property 委托至此，
        保持 backend 为单一真相源。参考图上限取自第三方来源，官方文档未明确列出，
        不硬编当既成事实。

        音轨恒有声：SDK 调用不带音轨开关，成片必然带音轨（``generate`` 结算时直接写死
        ``generate_audio=True``），用户的关闭意图无处可下发。
        """
        return VideoCapabilities(max_reference_images=7, audio_track=VideoAudioMode.ALWAYS_ON)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Grok 同步型 API，无 job_id 可接续；orphan handler 据 NotImplementedError 标 [resume_unsupported]
        raise NotImplementedError("GrokVideoBackend 不支持 resume_video（同步型 API）")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频；黑盒生成不重试，只有已取得 URL 后的下载可以独立重试。"""
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

        await download_video(video_url, request.output_path)
        logger.info("Grok 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
            generate_audio=True,
        )

    async def _create_video(self, request: VideoGenerationRequest):
        """通过不可判定收单边界的 SDK 调用生成视频。"""
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            # 轮询在 SDK 内部，仍按请求快照里的全局超时收口，否则该设置独独对 Grok 不生效。
            "timeout": timedelta(seconds=request.poll_timeout_seconds),
            "interval": timedelta(seconds=5),
        }
        if request.resolution is not None:
            generate_kwargs["resolution"] = request.resolution

        if request.start_image and Path(request.start_image).exists():
            image_path = Path(request.start_image)
            generate_kwargs["image_url"] = await asyncio.to_thread(image_to_data_uri, image_path, IMAGE_MIME_TYPES)

        if request.reference_images:
            ref_paths = [Path(p) if not isinstance(p, Path) else p for p in request.reference_images]
            existing_paths = [p for p in ref_paths if p.exists()]
            if existing_paths:
                ref_urls = await asyncio.gather(
                    *[asyncio.to_thread(image_to_data_uri, p, IMAGE_MIME_TYPES) for p in existing_paths]
                )
                generate_kwargs["reference_image_urls"] = list(ref_urls)

        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        return await self._client.video.generate(**generate_kwargs)
