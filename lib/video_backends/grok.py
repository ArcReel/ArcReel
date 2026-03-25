"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import base64
import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional, Set

import xai_sdk

from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    PROVIDER_GROK,
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
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        if not api_key:
            raise ValueError(
                "XAI_API_KEY 未设置\n"
                "请在系统配置页中配置 xAI API Key"
            )

        self._client = xai_sdk.AsyncClient(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: Set[VideoCapability] = {
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
    def capabilities(self) -> Set[VideoCapability]:
        return self._capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。"""
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "timeout": timedelta(minutes=15),
            "interval": timedelta(seconds=5),
        }

        if request.start_image and Path(request.start_image).exists():
            image_path = Path(request.start_image)
            suffix = image_path.suffix.lower()
            mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
            image_data = image_path.read_bytes()
            b64 = base64.b64encode(image_data).decode("ascii")
            generate_kwargs["image_url"] = f"data:{mime_type};base64,{b64}"

        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        response = await self._client.video.generate(**generate_kwargs)

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
