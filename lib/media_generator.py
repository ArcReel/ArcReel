"""
MediaGenerator 中间层

封装 GeminiClient + VersionManager，提供"调用方无感"的版本管理。
调用方只需传入 project_path 和 resource_id，版本管理自动完成。

覆盖的 4 种资源类型：
- storyboards: 分镜图 (scene_E1S01.png)
- videos: 视频 (scene_E1S01.mp4)
- characters: 人物设计图 (姜月茴.png)
- clues: 线索设计图 (玉佩.png)
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, List, Union, Tuple
from PIL import Image

from lib.gemini_client import GeminiClient, RateLimiter, ReferenceImageInput
from lib.version_manager import VersionManager
from lib.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)


class MediaGenerator:
    """
    媒体生成器中间层

    封装 GeminiClient + VersionManager，提供自动版本管理。
    """

    # 资源类型到输出路径模式的映射
    OUTPUT_PATTERNS = {
        'storyboards': 'storyboards/scene_{resource_id}.png',
        'videos': 'videos/scene_{resource_id}.mp4',
        'characters': 'characters/{resource_id}.png',
        'clues': 'clues/{resource_id}.png',
    }

    def __init__(
        self,
        project_path: Path,
        rate_limiter: Optional[RateLimiter] = None,
        video_backend=None,
        *,
        image_backend_type: Optional[str] = None,
        video_backend_type: Optional[str] = None,
        video_generate_audio: Optional[bool] = None,
        gemini_api_key: Optional[str] = None,
        gemini_base_url: Optional[str] = None,
        gemini_image_model: Optional[str] = None,
        gemini_video_model: Optional[str] = None,
    ):
        """
        初始化 MediaGenerator

        Args:
            project_path: 项目根目录路径
            rate_limiter: 可选的限流器实例
            video_backend: 可选的 VideoBackend 实例（注入后将替代 GeminiClient 生成视频）
            image_backend_type: 图片后端类型（aistudio/vertex），None 时从环境变量读取
            video_backend_type: 视频后端类型（aistudio/vertex），None 时从环境变量读取
            video_generate_audio: 是否生成音频（Vertex 模式），None 时从环境变量读取
            gemini_api_key: Gemini API Key（透传给 GeminiClient）
            gemini_base_url: Gemini Base URL（透传给 GeminiClient）
            gemini_image_model: 图片模型名称（透传给 GeminiClient）
            gemini_video_model: 视频模型名称（透传给 GeminiClient）
        """
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self._rate_limiter = rate_limiter
        self.image_backend = (
            (image_backend_type or "").strip().lower()
            or (os.environ.get("GEMINI_IMAGE_BACKEND") or "").strip().lower()
            or "aistudio"
        )
        self._gemini_video_backend_type = (
            (video_backend_type or "").strip().lower()
            or (os.environ.get("GEMINI_VIDEO_BACKEND") or "").strip().lower()
            or "aistudio"
        )
        self._video_generate_audio = video_generate_audio
        self._gemini_api_key = gemini_api_key
        self._gemini_base_url = gemini_base_url
        self._gemini_image_model = gemini_image_model
        self._gemini_video_model = gemini_video_model
        self._video_backend = video_backend
        self._gemini_image: Optional[GeminiClient] = None
        self._gemini_video: Optional[GeminiClient] = None
        self.versions = VersionManager(project_path)

        # 初始化 UsageTracker（使用全局 async session factory）
        self.usage_tracker = UsageTracker()

    @staticmethod
    def _sync(coro):
        """Run an async coroutine from synchronous code (e.g. inside to_thread)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def _get_gemini_image(self) -> GeminiClient:
        if self._gemini_image is None:
            self._gemini_image = GeminiClient(
                api_key=self._gemini_api_key,
                rate_limiter=self._rate_limiter,
                backend=self.image_backend,
                base_url=self._gemini_base_url,
                image_model=self._gemini_image_model,
                video_model=self._gemini_video_model,
            )
        return self._gemini_image

    def _get_gemini_video(self) -> GeminiClient:
        if self._gemini_video is None:
            self._gemini_video = GeminiClient(
                api_key=self._gemini_api_key,
                rate_limiter=self._rate_limiter,
                backend=self._gemini_video_backend_type,
                base_url=self._gemini_base_url,
                image_model=self._gemini_image_model,
                video_model=self._gemini_video_model,
            )
        return self._gemini_video

    @staticmethod
    def _read_bool_env(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        normalized = str(raw).strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
        return default

    def _resolve_video_generate_audio(self) -> bool:
        """Resolve whether to generate audio for video.

        Uses the explicit constructor param if provided, otherwise falls back
        to the GEMINI_VIDEO_GENERATE_AUDIO environment variable.
        """
        if self._video_generate_audio is not None:
            return self._video_generate_audio
        return self._read_bool_env("GEMINI_VIDEO_GENERATE_AUDIO", True)

    def _get_output_path(self, resource_type: str, resource_id: str) -> Path:
        """
        根据资源类型和 ID 推断输出路径

        Args:
            resource_type: 资源类型 (storyboards, videos, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)

        Returns:
            输出文件的绝对路径
        """
        if resource_type not in self.OUTPUT_PATTERNS:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        pattern = self.OUTPUT_PATTERNS[resource_type]
        relative_path = pattern.format(resource_id=resource_id)
        output_path = (self.project_path / relative_path).resolve()
        try:
            output_path.relative_to(self.project_path.resolve())
        except ValueError:
            raise ValueError(f"非法资源 ID: '{resource_id}'")
        return output_path

    def _ensure_parent_dir(self, output_path: Path) -> None:
        """确保输出目录存在"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

    def generate_image(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images: Optional[List[ReferenceImageInput]] = None,
        aspect_ratio: str = "9:16",
        image_size: str = "1K",
        **version_metadata
    ) -> Tuple[Path, int]:
        """
        生成图片（带自动版本管理）

        版本管理逻辑：
        1. 检查 output_path 是否存在
        2. 若存在 → 调用 ensure_current_tracked() 确保旧文件被记录
        3. 调用 GeminiClient 生成新文件
        4. 调用 add_version() 记录新版本
        5. 返回结果

        Args:
            prompt: 图片生成提示词
            resource_type: 资源类型 (storyboards, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)
            reference_images: 参考图片列表（用于人物一致性）
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            image_size: 图片尺寸，默认 1K
            **version_metadata: 额外元数据（如 aspect_ratio）

        Returns:
            (output_path, version_number) 元组
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. 若已存在，确保旧文件被记录
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,  # 使用新 prompt 作为备用
                aspect_ratio=aspect_ratio,
                **version_metadata
            )

        # 2. 记录 API 调用开始
        client = self._get_gemini_image()
        call_id = self._sync(self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="image",
            model=client.IMAGE_MODEL,
            prompt=prompt,
            resolution=image_size,
            aspect_ratio=aspect_ratio,
        ))

        try:
            # 3. 调用 GeminiClient 生成新文件
            client.generate_image(
                prompt=prompt,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                output_path=output_path
            )

            # 4. 记录调用成功
            self._sync(self.usage_tracker.finish_call(
                call_id=call_id,
                status="success",
                output_path=str(output_path),
            ))
        except Exception as e:
            # 记录调用失败
            logger.exception("生成失败 (%s)", "image")
            self._sync(self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e),
            ))
            raise

        # 5. 记录新版本
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            aspect_ratio=aspect_ratio,
            **version_metadata
        )

        return output_path, new_version

    async def generate_image_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images: Optional[List[ReferenceImageInput]] = None,
        aspect_ratio: str = "9:16",
        image_size: str = "1K",
        **version_metadata
    ) -> Tuple[Path, int]:
        """
        异步生成图片（带自动版本管理）

        Args:
            prompt: 图片生成提示词
            resource_type: 资源类型 (storyboards, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)
            reference_images: 参考图片列表（用于人物一致性）
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            image_size: 图片尺寸，默认 1K
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number) 元组
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. 若已存在，确保旧文件被记录
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                **version_metadata
            )

        # 2. 记录 API 调用开始
        client = self._get_gemini_image()
        call_id = await self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="image",
            model=client.IMAGE_MODEL,
            prompt=prompt,
            resolution=image_size,
            aspect_ratio=aspect_ratio,
        )

        try:
            # 3. 调用 GeminiClient 异步生成新文件
            await client.generate_image_async(
                prompt=prompt,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                output_path=output_path
            )

            # 4. 记录调用成功
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="success",
                output_path=str(output_path),
            )
        except Exception as e:
            # 记录调用失败
            logger.exception("生成失败 (%s)", "image")
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e),
            )
            raise

        # 5. 记录新版本
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            aspect_ratio=aspect_ratio,
            **version_metadata
        )

        return output_path, new_version

    def generate_video(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: Optional[Union[str, Path, Image.Image]] = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str = "8",
        resolution: str = "1080p",
        negative_prompt: str = "background music, BGM, soundtrack, musical accompaniment",
        **version_metadata
    ) -> Tuple[Path, int, any, Optional[str]]:
        """
        生成视频（带自动版本管理）

        Args:
            prompt: 视频生成提示词
            resource_type: 资源类型 (videos)
            resource_id: 资源 ID (E1S01)
            start_image: 起始帧图片（image-to-video 模式）
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            duration_seconds: 视频时长，可选 "4", "6", "8"
            resolution: 分辨率，默认 "1080p"
            negative_prompt: 负面提示词
            **version_metadata: 额外元数据（如 duration_seconds）

        Returns:
            (output_path, version_number, video_ref, video_uri) 四元组
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. 若已存在，确保旧文件被记录
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                duration_seconds=duration_seconds,
                **version_metadata
            )

        # 2. 记录 API 调用开始
        try:
            duration_int = int(duration_seconds) if duration_seconds else 8
        except (ValueError, TypeError):
            duration_int = 8

        if self._video_backend:
            model_name = self._video_backend.model
            provider_name = self._video_backend.name
            effective_generate_audio = version_metadata.get("generate_audio", True)
        else:
            video_client = self._get_gemini_video()
            model_name = video_client.VIDEO_MODEL
            provider_name = "gemini"
            configured_generate_audio = self._resolve_video_generate_audio()
            effective_generate_audio = (
                configured_generate_audio if self._gemini_video_backend_type == "vertex" else True
            )

        call_id = self._sync(self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="video",
            model=model_name,
            prompt=prompt,
            resolution=resolution,
            duration_seconds=duration_int,
            aspect_ratio=aspect_ratio,
            generate_audio=effective_generate_audio,
            provider=provider_name,
        ))

        try:
            if self._video_backend:
                from lib.video_backends.base import VideoGenerationRequest

                request = VideoGenerationRequest(
                    prompt=prompt,
                    output_path=output_path,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_int,
                    resolution=resolution,
                    start_image=Path(start_image) if isinstance(start_image, (str, Path)) else None,
                    generate_audio=effective_generate_audio,
                    negative_prompt=negative_prompt,
                    project_name=self.project_name,
                    service_tier=version_metadata.get("service_tier", "default"),
                    seed=version_metadata.get("seed"),
                )

                result = self._sync(self._video_backend.generate(request))
                video_ref = None
                video_uri = result.video_uri

                # Track usage with provider info
                self._sync(self.usage_tracker.finish_call(
                    call_id=call_id,
                    status="success",
                    output_path=str(output_path),

                    usage_tokens=result.usage_tokens,
                    service_tier=version_metadata.get("service_tier", "default"),
                ))
            else:
                # Original GeminiClient path
                _, video_ref, video_uri = video_client.generate_video(
                    prompt=prompt,
                    start_image=start_image,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    resolution=resolution,
                    negative_prompt=negative_prompt,
                    generate_audio=effective_generate_audio,
                    output_path=output_path
                )

                # 4. 记录调用成功
                self._sync(self.usage_tracker.finish_call(
                    call_id=call_id,
                    status="success",
                    output_path=str(output_path),
                ))
        except Exception as e:
            # 记录调用失败
            logger.exception("生成失败 (%s)", "video")
            self._sync(self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e),
            ))
            raise

        # 5. 记录新版本
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            duration_seconds=duration_seconds,
            **version_metadata
        )

        return output_path, new_version, video_ref, video_uri

    async def generate_video_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: Optional[Union[str, Path, Image.Image]] = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str = "8",
        resolution: str = "1080p",
        negative_prompt: str = "background music, BGM, soundtrack, musical accompaniment",
        **version_metadata
    ) -> Tuple[Path, int, any, Optional[str]]:
        """
        异步生成视频（带自动版本管理）

        Args:
            prompt: 视频生成提示词
            resource_type: 资源类型 (videos)
            resource_id: 资源 ID (E1S01)
            start_image: 起始帧图片（image-to-video 模式）
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            duration_seconds: 视频时长，可选 "4", "6", "8"
            resolution: 分辨率，默认 "1080p"
            negative_prompt: 负面提示词
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number, video_ref, video_uri) 四元组
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. 若已存在，确保旧文件被记录
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                duration_seconds=duration_seconds,
                **version_metadata
            )

        # 2. 记录 API 调用开始
        try:
            duration_int = int(duration_seconds) if duration_seconds else 8
        except (ValueError, TypeError):
            duration_int = 8

        if self._video_backend:
            model_name = self._video_backend.model
            provider_name = self._video_backend.name
            effective_generate_audio = version_metadata.get("generate_audio", True)
        else:
            video_client = self._get_gemini_video()
            model_name = video_client.VIDEO_MODEL
            provider_name = "gemini"
            configured_generate_audio = self._resolve_video_generate_audio()
            effective_generate_audio = (
                configured_generate_audio if self._gemini_video_backend_type == "vertex" else True
            )

        call_id = await self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="video",
            model=model_name,
            prompt=prompt,
            resolution=resolution,
            duration_seconds=duration_int,
            aspect_ratio=aspect_ratio,
            generate_audio=effective_generate_audio,
            provider=provider_name,
        )

        try:
            if self._video_backend:
                from lib.video_backends.base import VideoGenerationRequest

                request = VideoGenerationRequest(
                    prompt=prompt,
                    output_path=output_path,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_int,
                    resolution=resolution,
                    start_image=Path(start_image) if isinstance(start_image, (str, Path)) else None,
                    generate_audio=effective_generate_audio,
                    negative_prompt=negative_prompt,
                    project_name=self.project_name,
                    service_tier=version_metadata.get("service_tier", "default"),
                    seed=version_metadata.get("seed"),
                )

                result = await self._video_backend.generate(request)
                video_ref = None
                video_uri = result.video_uri

                # Track usage with provider info
                await self.usage_tracker.finish_call(
                    call_id=call_id,
                    status="success",
                    output_path=str(output_path),

                    usage_tokens=result.usage_tokens,
                    service_tier=version_metadata.get("service_tier", "default"),
                )
            else:
                # Original GeminiClient path
                _, video_ref, video_uri = await video_client.generate_video_async(
                    prompt=prompt,
                    start_image=start_image,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    resolution=resolution,
                    negative_prompt=negative_prompt,
                    generate_audio=effective_generate_audio,
                    output_path=output_path
                )

                # 4. 记录调用成功
                await self.usage_tracker.finish_call(
                    call_id=call_id,
                    status="success",
                    output_path=str(output_path),
                )
        except Exception as e:
            # 记录调用失败
            logger.exception("生成失败 (%s)", "video")
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e),
            )
            raise

        # 5. 记录新版本
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            duration_seconds=duration_seconds,
            **version_metadata
        )

        return output_path, new_version, video_ref, video_uri
