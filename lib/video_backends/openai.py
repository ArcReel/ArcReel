"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sora-2"

_SIZE_MAP: dict[tuple[str, str], str] = {
    ("720p", "9:16"): "720x1280",
    ("720p", "16:9"): "1280x720",
    ("1080p", "9:16"): "1080x1920",
    ("1080p", "16:9"): "1920x1080",
    ("1024p", "9:16"): "1024x1792",
    ("1024p", "16:9"): "1792x1024",
}
_DEFAULT_SIZE = "720x1280"


def _resolve_size(resolution: str, aspect_ratio: str) -> str:
    return _SIZE_MAP.get((resolution, aspect_ratio), _DEFAULT_SIZE)


class OpenAIVideoBackend:
    """OpenAI Sora 视频生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": _map_duration(request.duration_seconds),
            "size": _resolve_size(request.resolution, request.aspect_ratio),
        }

        if request.start_image and Path(request.start_image).exists():
            kwargs["input_reference"] = _encode_start_image(request.start_image)

        logger.info("OpenAI 视频生成开始: model=%s, seconds=%s", self._model, kwargs["seconds"])

        video = await self._client.videos.create_and_poll(**kwargs)

        if video.status == "failed":
            raise RuntimeError(f"Sora 视频生成失败: {video.error}")

        content = await self._client.videos.download_content(video.id)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(content.content)

        logger.info("OpenAI 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            duration_seconds=int(video.seconds),
            task_id=video.id,
        )


def _map_duration(seconds: int) -> str:
    if seconds <= 4:
        return "4"
    elif seconds <= 8:
        return "8"
    else:
        return "12"


def _encode_start_image(image_path: Path) -> dict:
    from lib.image_backends.base import image_to_base64_data_uri

    data_uri = image_to_base64_data_uri(Path(image_path))
    return {
        "type": "image_url",
        "image_url": data_uri,
    }
