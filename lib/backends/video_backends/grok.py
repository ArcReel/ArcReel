"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import download_video, read_video_images
from lib.backends.grok_shared import create_grok_client
from lib.backends.providers import PROVIDER_GROK
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.billing.metering import Usage
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)


_REFERENCE_ROUTE_MAX_RESOLUTION = "720p"


class GrokVideoBackend(EntryBoundCapabilities):
    """xAI Grok 视频生成后端。"""

    endpoint_key = "grok-video-classic"
    mixed_frames_and_references = False

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
    def video_capabilities_for_model(model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """xAI 视频调用通道的声明（通道事实）—— 纯计算，不构造 SDK client（无需 api_key）。

        通道对任何型号都按同一份请求构造发出首帧（``image_url``）、尾帧（``last_frame_url``）与参考图
        （``reference_image_urls``），不限张数；参考音频只收预置 ``voice_id``，表达不了项目的参考音频文件，不声明。
        各型号接受哪些输入（如 1.5 lite 不收参考图、只有 1.5 收尾帧）、能接受几张参考图记在模型条目上，与本声明两侧
        求值（``lib.backends.video_capabilities``，docs/adr/0104）。

        随输入组合变化的限制仍在 ``generate`` 请求期校验（两种调用端点共用）：参考生视频路径分辨率上限 720p，
        经典版拒收首帧与参考图并存。

        音轨可开关：请求把音轨意图下发为 SDK 的 ``generate_audio``（缺省有声，``False`` 出无声
        视频），``generate`` 结算按同一下发值记录。
        """
        del model, entry
        return VideoCapabilities(last_frame=True, max_reference_images=None, audio_track=VideoAudioMode.CONTROLLABLE)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`）；结算的输出秒数取 xAI 回报的时长。

        各型号按同一组请求字段计量，不按型号或条目区分。
        """
        del model, entry
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=request.generate_audio,
        )

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        raise NotImplementedError("GrokVideoBackend 不支持 resume_video（同步型 API）")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频；黑盒生成不重试，只有已取得 URL 后的下载可以独立重试。"""
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        self._check_reference_route_resolution(request)
        self._check_classic_first_frame_with_references(request)
        if request.on_provider_resubmit_unsafe is not None:
            request.on_provider_resubmit_unsafe()
        response = await self._create_video(request)

        video_url = response.url
        raw_duration = getattr(response, "duration", None)
        actual_duration = request.duration_seconds
        try:
            if raw_duration is not None:
                parsed = float(raw_duration)
                if 0 < parsed <= MAX_BILLED_DURATION_SECONDS:
                    rounded = int(parsed + 0.5)
                    if rounded > 0:
                        actual_duration = rounded
        except (TypeError, ValueError, OverflowError):
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

    def _check_classic_first_frame_with_references(self, request: VideoGenerationRequest) -> None:
        """classic 模型拒收首帧与参考图并存（参考生视频页「Classic grok-imagine-video ... rejects combining
        ``image`` with reference inputs」），在付费调用前拒绝，而不是两者一并下发后由供应商判失败。

        是否允许混用由所选调用端点声明，与型号名及条目无关。"""
        if not self.mixed_frames_and_references and request.start_image and request.reference_images:
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)

    async def _create_video(self, request: VideoGenerationRequest):
        """通过不可判定收单边界的 SDK 调用生成视频。"""
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "generate_audio": request.generate_audio,
            "timeout": timedelta(seconds=request.poll_timeout_seconds),
            "interval": timedelta(seconds=5),
        }
        if request.resolution is not None:
            generate_kwargs["resolution"] = request.resolution

        generate_kwargs.update(await asyncio.to_thread(self._encode_image_inputs, request))

        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        return await self._client.video.generate(**generate_kwargs)

    def _encode_image_inputs(self, request: VideoGenerationRequest) -> dict[str, Any]:
        """首帧 / 尾帧 / 参考图 → SDK 的 data URI 参数（同步：读本地图片，调用方放线程里跑）。"""
        images = read_video_images(request, self._model)
        kwargs: dict[str, Any] = {}
        if request.start_image:
            kwargs["image_url"] = images[Path(request.start_image)].data_uri()
        if request.end_image:
            kwargs["last_frame_url"] = images[Path(request.end_image)].data_uri()
        if request.reference_images:
            kwargs["reference_image_urls"] = [images[Path(p)].data_uri() for p in request.reference_images]
        return kwargs


class GrokClassicVideoBackend(GrokVideoBackend):
    """经典协议拒绝首帧与参考图混用。"""

    endpoint_key = "grok-video-classic"
    mixed_frames_and_references = False


class GrokMixedVideoBackend(GrokVideoBackend):
    """允许首帧与参考图混用的请求契约。"""

    endpoint_key = "grok-video-mixed"
    mixed_frames_and_references = True


GROK_BACKENDS = {backend.endpoint_key: backend for backend in (GrokClassicVideoBackend, GrokMixedVideoBackend)}
