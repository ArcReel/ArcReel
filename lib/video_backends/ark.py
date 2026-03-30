"""ArkVideoBackend — 火山方舟 Ark 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
import os

from lib.providers import PROVIDER_ARK
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class ArkVideoBackend:
    """Ark (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("ARK_API_KEY 环境变量未设置\n请在 .env 文件中添加：ARK_API_KEY=your-api-key")

        from volcenginesdkarkruntime import Ark

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
            VideoCapability.SEED_CONTROL,
            VideoCapability.FLEX_TIER,
        }

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。"""
        # 1. Build content list
        content = [{"type": "text", "text": request.prompt}]

        if request.start_image:
            from lib.image_backends.base import image_to_base64_data_uri

            data_uri = image_to_base64_data_uri(request.start_image)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                }
            )

        # 2. Build API params
        # Map aspect_ratio format: "9:16" -> "9:16" (same format, no conversion needed)
        create_params = {
            "model": self._model,
            "content": content,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "resolution": request.resolution,
            "generate_audio": request.generate_audio,
            "watermark": False,
            "service_tier": request.service_tier,
        }
        if request.seed is not None:
            create_params["seed"] = request.seed

        # 3. Create task (sync SDK call, run in executor)
        create_result = await asyncio.to_thread(
            self._client.content_generation.tasks.create,
            **create_params,
        )
        task_id = create_result.id
        logger.info("Ark 任务已创建: %s", task_id)

        # 4. Poll until done
        poll_interval = 10 if request.service_tier == "default" else 60
        max_wait_time = 600 if request.service_tier == "default" else 3600
        elapsed = 0

        while True:
            result = await asyncio.to_thread(
                self._client.content_generation.tasks.get,
                task_id=task_id,
            )

            if result.status == "succeeded":
                break
            elif result.status in ("failed", "expired"):
                error_msg = getattr(result, "error", None) or "Unknown error"
                raise RuntimeError(f"Ark 视频生成失败: {error_msg}")

            elapsed += poll_interval
            if elapsed >= max_wait_time:
                raise TimeoutError(f"Ark 视频生成超时（{max_wait_time}秒）")

            logger.info(
                "Ark 视频生成中... 状态: %s, 已等待 %d 秒",
                result.status,
                elapsed,
            )
            await asyncio.sleep(poll_interval)

        # 5. Download video
        video_url = result.content.video_url
        await download_video(video_url, request.output_path)

        # 6. Extract result metadata
        seed = getattr(result, "seed", None)
        usage_tokens = None
        if hasattr(result, "usage") and result.usage:
            usage_tokens = getattr(result.usage, "completion_tokens", None)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            seed=seed,
            usage_tokens=usage_tokens,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )
