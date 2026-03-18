"""SeedanceVideoBackend — 火山方舟 Seedance 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Set

from lib.video_backends.base import (
    PROVIDER_SEEDANCE,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class SeedanceVideoBackend:
    """Seedance (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        file_service_base_url: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError(
                "ARK_API_KEY 环境变量未设置\n"
                "请在 .env 文件中添加：ARK_API_KEY=your-api-key"
            )

        from volcenginesdkarkruntime import Ark

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or self.DEFAULT_MODEL
        self._file_service_base_url = file_service_base_url or os.environ.get(
            "FILE_SERVICE_BASE_URL", ""
        )
        self._capabilities: Set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
            VideoCapability.SEED_CONTROL,
            VideoCapability.FLEX_TIER,
        }

    @property
    def name(self) -> str:
        return PROVIDER_SEEDANCE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[VideoCapability]:
        return self._capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。"""
        # 1. Build content list
        content = [{"type": "text", "text": request.prompt}]

        if request.start_image:
            image_url = self._get_image_url(request.start_image, request.project_name)
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url},
            })

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
        logger.info("Seedance 任务已创建: %s", task_id)

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
                raise RuntimeError(f"Seedance 视频生成失败: {error_msg}")

            elapsed += poll_interval
            if elapsed >= max_wait_time:
                raise TimeoutError(f"Seedance 视频生成超时（{max_wait_time}秒）")

            logger.info(
                "Seedance 视频生成中... 状态: %s, 已等待 %d 秒",
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
            provider=PROVIDER_SEEDANCE,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            seed=seed,
            usage_tokens=usage_tokens,
            task_id=task_id,
        )

    def _get_image_url(self, image_path: Path, project_name: Optional[str] = None) -> str:
        """将本地图片路径转换为公网可访问的 URL。

        通过项目文件服务的静态资源路径构建 URL。
        文件服务路由为 /api/v1/files/{project_name}/{rel_path}。
        """
        if not self._file_service_base_url:
            raise ValueError(
                "使用 Seedance 供应商的图生视频功能需要设置 FILE_SERVICE_BASE_URL 环境变量\n"
                "部署环境必须可公网访问"
            )
        if not project_name:
            raise ValueError("project_name is required for image URL generation")
        # Walk up from the image to find the project directory,
        # avoiding false matches when project_name appears elsewhere in the path
        # (e.g. /home/demo/projects/demo/storyboards/scene_E1S01.png)
        image_path = Path(image_path)
        project_dir = None
        p = image_path.parent
        while p != p.parent:
            if p.name == project_name:
                project_dir = p
                break
            p = p.parent

        if not project_dir:
            raise ValueError(f"无法从路径中定位项目 '{project_name}': {image_path}")

        rel_path = image_path.relative_to(project_dir).as_posix()
        return f"{self._file_service_base_url}/api/v1/files/{project_name}/{rel_path}"
