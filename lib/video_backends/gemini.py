"""GeminiVideoBackend — 从 GeminiClient 提取的视频生成逻辑。"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import Any

from PIL import Image

from lib.config.url_utils import normalize_base_url
from lib.gemini_shared import VERTEX_SCOPES, RateLimiter, get_shared_rate_limiter
from lib.providers import PROVIDER_GEMINI
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.system_config import resolve_vertex_credentials_path
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    poll_with_retry,
)

logger = logging.getLogger(__name__)


class GeminiVideoBackend:
    """Gemini (Veo) 视频生成后端。"""

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
        video_model: str | None = None,
        base_url: str | None = None,
    ):
        from google import genai as _genai
        from google.genai import types as _types

        self._types = _types
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._backend_type = backend_type.strip().lower()
        self._credentials = None
        self._project_id = None

        from lib.cost_calculator import cost_calculator

        self._video_model = video_model or os.environ.get("GEMINI_VIDEO_MODEL", cost_calculator.DEFAULT_VIDEO_MODEL)

        if self._backend_type == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            credentials_file = resolve_vertex_credentials_path(Path(__file__).parent.parent.parent)
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file) as f:
                creds_data = json_module.load(f)
            self._project_id = creds_data.get("project_id")

            self._credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
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

            effective_base_url = normalize_base_url(base_url or os.environ.get("GEMINI_BASE_URL"))
            http_options = {"base_url": effective_base_url} if effective_base_url else None
            self._client = _genai.Client(api_key=_api_key, http_options=http_options)

        # 缓存 capabilities，避免每次访问创建新 set
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.NEGATIVE_PROMPT,
            VideoCapability.VIDEO_EXTEND,
        }
        if self._backend_type == "vertex":
            self._capabilities.add(VideoCapability.GENERATE_AUDIO)

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._video_model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=3)

    @staticmethod
    def _normalize_duration(duration_seconds: int) -> str:
        """标准化为 Veo 支持的离散时长值: '4', '6', '8'。"""
        if duration_seconds <= 4:
            return "4"
        if duration_seconds <= 6:
            return "6"
        return "8"

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。任务创建和轮询阶段分离重试，避免瞬态错误导致重建任务。"""
        operation = await self._create_task(request)
        return await self._poll_until_done(operation, request)

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> Any:
        """创建 Gemini 视频生成任务（带重试保护）。"""
        # 1. 限流
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._video_model)

        # 2. duration 标准化为 Veo 支持的离散值并转字符串
        duration_str = self._normalize_duration(request.duration_seconds)

        # 3. 构建配置
        config_params: dict = {
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "duration_seconds": duration_str,
            "negative_prompt": request.negative_prompt or "music, BGM, background music, subtitles, low quality",
        }
        if self._backend_type == "vertex":
            config_params["generate_audio"] = request.generate_audio

        # end_image → last_frame（帧插值）
        if request.end_image is not None:
            config_params["last_frame"] = self._prepare_image_param(request.end_image)

        # reference_images → reference_images（参考图列表，type=ASSET）
        if request.reference_images:
            config_params["reference_images"] = [
                self._types.VideoGenerationReferenceImage(
                    image=self._prepare_image_param(img),
                    reference_type=self._types.VideoGenerationReferenceType.ASSET,
                )
                for img in request.reference_images
            ]

        config = self._types.GenerateVideosConfig(**config_params)

        # 4. 准备 source（prompt + 可选起始帧）
        image_param = self._prepare_image_param(request.start_image) if request.start_image else None
        source = self._types.GenerateVideosSource(prompt=request.prompt, image=image_param)

        # 5. 调用 API
        operation = await self._client.aio.models.generate_videos(model=self._video_model, source=source, config=config)
        op_name = getattr(operation, "name", "unknown")
        logger.info("视频生成已提交, operation=%s", op_name)
        return operation

    async def _poll_until_done(self, operation: Any, request: VideoGenerationRequest) -> VideoGenerationResult:
        """轮询任务状态直到完成，瞬态错误仅重试当次轮询请求。"""
        op_name = getattr(operation, "name", "unknown")
        logger.info("开始轮询 operation=%s ...", op_name)

        if not operation.done:
            operation = await poll_with_retry(
                # SDK 通过 operation.name 查询状态并返回新对象，闭包捕获初始 operation 即可
                poll_fn=lambda: self._client.aio.operations.get(operation),
                is_done=lambda op: op.done,
                is_failed=lambda op: None,  # Gemini 在轮询完成后检查失败
                poll_interval=20,  # 与 Google 官方推荐一致
                max_wait=600,
                label="Gemini",
                on_progress=lambda op, elapsed: logger.info(
                    "视频生成中... 已等待 %.0f 秒 (operation=%s)", elapsed, op_name
                ),
            )

        logger.info("视频生成完成 (operation=%s)", op_name)

        # 检查结果
        if not operation.response or not operation.response.generated_videos:
            error_detail = getattr(operation, "error", None)
            metadata = getattr(operation, "metadata", None)
            logger.error(
                "视频生成返回空结果: operation=%s, error=%s, metadata=%s",
                op_name,
                error_detail,
                metadata,
            )
            if error_detail:
                raise RuntimeError(f"视频生成失败: {error_detail}")
            raise RuntimeError("视频生成失败: API 返回空结果")

        # 提取并下载视频
        generated_video = operation.response.generated_videos[0]
        video_ref = generated_video.video
        video_uri = video_ref.uri if video_ref else None

        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        await self._download_video_with_retry(video_ref, request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._video_model,
            duration_seconds=request.duration_seconds,
            video_uri=video_uri,
            generate_audio=request.generate_audio if self._backend_type == "vertex" else True,
        )

    # ------------------------------------------------------------------
    # 内部辅助方法（从 GeminiClient 提取）
    # ------------------------------------------------------------------

    def _prepare_image_param(self, image: str | Path | Image.Image | None):
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
            return self._types.Image(image_bytes=image_bytes, mime_type=mime_type)
        elif isinstance(image, Image.Image):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()
            return self._types.Image(image_bytes=image_bytes, mime_type=mime_type_png)
        else:
            return image

    @with_retry_async(max_attempts=DOWNLOAD_MAX_ATTEMPTS, backoff_seconds=DOWNLOAD_BACKOFF_SECONDS)
    async def _download_video_with_retry(self, video_ref, output_path: Path) -> None:
        """下载视频（含瞬态错误重试）。"""
        await asyncio.to_thread(self._download_video, video_ref, output_path)

    def _download_video(self, video_ref, output_path: Path) -> None:
        """下载视频到本地文件 — 提取自 GeminiClient。"""
        if self._backend_type == "vertex":
            if video_ref and hasattr(video_ref, "video_bytes") and video_ref.video_bytes:
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
