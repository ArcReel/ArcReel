"""GrokVideoBackend — xAI Grok video generation backend."""

from __future__ import annotations

import base64
import logging
from datetime import timedelta
from pathlib import Path

from lib.grok_shared import create_grok_client
from lib.providers import PROVIDER_GROK
from lib.retry import with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class GrokVideoBackend:
    """xAI Grok video generation backend."""

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

    @with_retry_async()
    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Generate a video."""
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

        logger.info("Grok video generation started: model=%s, duration=%ds", self._model, request.duration_seconds)
        response = await self._client.video.generate(**generate_kwargs)

        video_url = response.url
        actual_duration = getattr(response, "duration", request.duration_seconds)

        await download_video(video_url, request.output_path)

        logger.info("Grok video download complete: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
            generate_audio=True,
        )
