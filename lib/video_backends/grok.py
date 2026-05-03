"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import timedelta
from pathlib import Path

from lib.grok_shared import create_grok_client, grok_should_retry
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_GROK
from lib.retry import with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapabilities,
    VideoCapability,
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
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(reference_images=True, max_reference_images=7)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。生成与下载分离重试，避免下载失败导致重新生成浪费额度。"""
        response = await self._create_video(request)

        video_url = response.url
        actual_duration = getattr(response, "duration", request.duration_seconds)

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

    @with_retry_async(retry_if=grok_should_retry)
    async def _create_video(self, request: VideoGenerationRequest):
        """创建视频生成任务（带独立重试）。"""
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "timeout": timedelta(minutes=15),
            "interval": timedelta(seconds=5),
        }
        if request.resolution is not None:
            generate_kwargs["resolution"] = request.resolution

        def _encode_to_data_uri(path: Path) -> str:
            suffix = path.suffix.lower()
            mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{b64}"

        if request.start_image and Path(request.start_image).exists():
            image_path = Path(request.start_image)
            generate_kwargs["image_url"] = await asyncio.to_thread(_encode_to_data_uri, image_path)

        if request.reference_images:
            ref_paths = [Path(p) if not isinstance(p, Path) else p for p in request.reference_images]
            existing_paths = [p for p in ref_paths if p.exists()]
            if existing_paths:
                ref_urls = await asyncio.gather(*[asyncio.to_thread(_encode_to_data_uri, p) for p in existing_paths])
                generate_kwargs["reference_image_urls"] = list(ref_urls)

        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        return await self._client.video.generate(**generate_kwargs)
