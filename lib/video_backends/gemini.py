"""GeminiVideoBackend — 从 GeminiClient 提取的视频生成逻辑。"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import Optional, Set, Union

from PIL import Image

from lib.gemini_client import RateLimiter, get_shared_rate_limiter, with_retry_async
from lib.system_config import resolve_vertex_credentials_path
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore
    types = None  # type: ignore


class GeminiVideoBackend:
    """Gemini (Veo) 视频生成后端。"""

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        video_model: Optional[str] = None,
    ):
        from google import genai as _genai
        from google.genai import types as _types

        self._types = _types
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._backend_type = backend_type.strip().lower()
        self._credentials = None
        self._project_id = None

        from lib.cost_calculator import cost_calculator

        self._video_model = video_model or os.environ.get(
            "GEMINI_VIDEO_MODEL", cost_calculator.DEFAULT_VIDEO_MODEL
        )

        if self._backend_type == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            credentials_file = resolve_vertex_credentials_path(
                Path(__file__).parent.parent.parent
            )
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file) as f:
                creds_data = json_module.load(f)
            self._project_id = creds_data.get("project_id")

            VERTEX_SCOPES = [
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/generative-language",
            ]
            self._credentials = (
                service_account.Credentials.from_service_account_file(
                    str(credentials_file), scopes=VERTEX_SCOPES
                )
            )

            self._client = _genai.Client(
                vertexai=True,
                project=self._project_id,
                location="global",
                credentials=self._credentials,
            )
        else:
            _api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not _api_key:
                raise ValueError("GEMINI_API_KEY 环境变量未设置")

            base_url = os.environ.get("GEMINI_BASE_URL", "").strip() or None
            http_options = {"base_url": base_url} if base_url else None
            self._client = _genai.Client(
                api_key=_api_key, http_options=http_options
            )

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._video_model

    @property
    def capabilities(self) -> Set[VideoCapability]:
        caps = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.NEGATIVE_PROMPT,
            VideoCapability.VIDEO_EXTEND,
        }
        if self._backend_type == "vertex":
            caps.add(VideoCapability.GENERATE_AUDIO)
        return caps

    @staticmethod
    def _normalize_duration(duration_seconds: int) -> str:
        """标准化为 Veo 支持的离散时长值: '4', '6', '8'。"""
        if duration_seconds <= 4:
            return "4"
        if duration_seconds <= 6:
            return "6"
        return "8"

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8))
    async def generate(
        self, request: VideoGenerationRequest
    ) -> VideoGenerationResult:
        """生成视频（仅生成模式，不含延长模式）。"""
        # 1. 限流
        if self._rate_limiter:
            self._rate_limiter.acquire(self._video_model)

        # 2. duration 标准化为 Veo 支持的离散值并转字符串
        duration_str = self._normalize_duration(request.duration_seconds)

        # 3. 构建配置
        config_params: dict = {
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "duration_seconds": duration_str,
            "negative_prompt": request.negative_prompt
            or "music, BGM, background music, subtitles, low quality",
        }
        if self._backend_type == "vertex":
            config_params["generate_audio"] = request.generate_audio
        config = self._types.GenerateVideosConfig(**config_params)

        # 4. 准备 source（prompt + 可选起始帧）
        image_param = (
            self._prepare_image_param(request.start_image)
            if request.start_image
            else None
        )
        source = self._types.GenerateVideosSource(
            prompt=request.prompt, image=image_param
        )

        # 5. 调用 API
        operation = await self._client.aio.models.generate_videos(
            model=self._video_model, source=source, config=config
        )

        # 6. 轮询等待完成
        elapsed = 0
        poll_interval = 10
        max_wait_time = 600
        while not operation.done:
            if elapsed >= max_wait_time:
                raise TimeoutError(f"视频生成超时（{max_wait_time}秒）")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            operation = await self._client.aio.operations.get(operation)
            logger.info("视频生成中... 已等待 %d 秒", elapsed)

        # 7. 检查结果
        if not operation.response or not operation.response.generated_videos:
            if hasattr(operation, "error") and operation.error:
                raise RuntimeError(f"视频生成失败: {operation.error}")
            raise RuntimeError("视频生成失败: API 返回空结果")

        # 8. 提取并下载视频
        generated_video = operation.response.generated_videos[0]
        video_ref = generated_video.video
        video_uri = video_ref.uri if video_ref else None

        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_video(video_ref, request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider="gemini",
            model=self._video_model,
            duration_seconds=request.duration_seconds,
            video_uri=video_uri,
        )

    # ------------------------------------------------------------------
    # 内部辅助方法（从 GeminiClient 提取）
    # ------------------------------------------------------------------

    def _prepare_image_param(
        self, image: Optional[Union[str, Path, Image.Image]]
    ):
        """准备图片参数用于 API 调用 — 提取自 GeminiClient。"""
        if image is None:
            return None

        mime_type_png = "image/png"

        if isinstance(image, (str, Path)):
            with open(image, "rb") as f:
                image_bytes = f.read()
            suffix = Path(image).suffix.lower()
            mime_types = {
                ".png": mime_type_png,
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            mime_type = mime_types.get(suffix, mime_type_png)
            return self._types.Image(
                image_bytes=image_bytes, mime_type=mime_type
            )
        elif isinstance(image, Image.Image):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()
            return self._types.Image(
                image_bytes=image_bytes, mime_type=mime_type_png
            )
        else:
            return image

    def _download_video(self, video_ref, output_path: Path) -> None:
        """下载视频到本地文件 — 提取自 GeminiClient。"""
        if self._backend_type == "vertex":
            if (
                video_ref
                and hasattr(video_ref, "video_bytes")
                and video_ref.video_bytes
            ):
                with open(output_path, "wb") as f:
                    f.write(video_ref.video_bytes)
            elif video_ref and hasattr(video_ref, "uri") and video_ref.uri:
                import urllib.request

                urllib.request.urlretrieve(video_ref.uri, str(output_path))
            else:
                raise RuntimeError("视频生成成功但无法获取视频数据")
        else:
            # AI Studio 模式：使用 files.download
            self._client.files.download(file=video_ref)
            video_ref.save(str(output_path))
