"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from lib.openai_shared import create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sora-2"

_SIZE_MAP: dict[str, str] = {
    "9:16": "720x1280",
    "16:9": "1280x720",
}


class OpenAIVideoBackend:
    """OpenAI Sora 视频生成后端。"""

    def __init__(self, *, api_key=None, model=None, base_url=None):
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

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": _map_duration(request.duration_seconds),
            "size": _SIZE_MAP.get(request.aspect_ratio, "720x1280"),
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
    image_path = Path(image_path)
    suffix = image_path.suffix.lower()
    mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
    image_data = image_path.read_bytes()
    b64 = base64.b64encode(image_data).decode("ascii")
    data_uri = f"data:{mime_type};base64,{b64}"
    return {
        "type": "image_url",
        "image_url": data_uri,
    }
