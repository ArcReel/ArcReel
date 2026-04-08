"""
MediaGenerator middleware layer

Wraps GeminiClient + VersionManager to provide transparent version management.
Callers only need to pass project_path and resource_id; version management is automatic.

Covers 4 resource types:
- storyboards: storyboard images (scene_E1S01.png)
- videos: video clips (scene_E1S01.mp4)
- characters: character design sheets (character_name.png)
- clues: clue design sheets (clue_name.png)
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PIL import Image

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver
    from lib.image_backends.base import ImageBackend

from lib.db.base import DEFAULT_USER_ID
from lib.gemini_shared import RateLimiter
from lib.usage_tracker import UsageTracker
from lib.version_manager import VersionManager

logger = logging.getLogger(__name__)


class MediaGenerator:
    """
    MediaGenerator middleware layer

    Wraps GeminiClient + VersionManager to provide automatic version management.
    """

    # Mapping from resource type to output path pattern
    OUTPUT_PATTERNS = {
        "storyboards": "storyboards/scene_{resource_id}.png",
        "videos": "videos/scene_{resource_id}.mp4",
        "characters": "characters/{resource_id}.png",
        "clues": "clues/{resource_id}.png",
    }

    def __init__(
        self,
        project_path: Path,
        rate_limiter: RateLimiter | None = None,
        image_backend: Optional["ImageBackend"] = None,
        video_backend=None,
        *,
        config_resolver: Optional["ConfigResolver"] = None,
        user_id: str = DEFAULT_USER_ID,
    ):
        """
        Initialise MediaGenerator.

        Args:
            project_path: Project root directory path
            rate_limiter: Optional rate limiter instance
            image_backend: Optional ImageBackend instance (for image generation)
            video_backend: Optional VideoBackend instance (for video generation)
            config_resolver: ConfigResolver instance for reading runtime configuration
            user_id: User ID
        """
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self._rate_limiter = rate_limiter
        self._image_backend = image_backend
        self._video_backend = video_backend
        self._config = config_resolver
        self._user_id = user_id
        self.versions = VersionManager(project_path)

        # Initialise UsageTracker (using the global async session factory)
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

    def _get_output_path(self, resource_type: str, resource_id: str) -> Path:
        """
        Derive the output path from resource type and ID.

        Args:
            resource_type: Resource type (storyboards, videos, characters, clues)
            resource_id: Resource ID (e.g. E1S01, character_name, clue_name)

        Returns:
            Absolute path to the output file
        """
        if resource_type not in self.OUTPUT_PATTERNS:
            raise ValueError(f"Unsupported resource type: {resource_type}")

        pattern = self.OUTPUT_PATTERNS[resource_type]
        relative_path = pattern.format(resource_id=resource_id)
        output_path = (self.project_path / relative_path).resolve()
        try:
            output_path.relative_to(self.project_path.resolve())
        except ValueError:
            raise ValueError(f"Invalid resource ID: '{resource_id}'")
        return output_path

    def _ensure_parent_dir(self, output_path: Path) -> None:
        """Ensure the output directory exists."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

    def generate_image(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images=None,
        aspect_ratio: str = "9:16",
        image_size: str = "1K",
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        Generate an image (with automatic version management, synchronous wrapper).

        Args:
            prompt: Image generation prompt
            resource_type: Resource type (storyboards, characters, clues)
            resource_id: Resource ID (e.g. E1S01, character_name, clue_name)
            reference_images: List of reference images
            aspect_ratio: Aspect ratio, default 9:16 (portrait)
            image_size: Image size, default 1K
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number) tuple
        """
        return self._sync(
            self.generate_image_async(
                prompt=prompt,
                resource_type=resource_type,
                resource_id=resource_id,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                **version_metadata,
            )
        )

    async def generate_image_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images=None,
        aspect_ratio: str = "9:16",
        image_size: str = "1K",
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        Asynchronously generate an image (with automatic version management).

        Args:
            prompt: Image generation prompt
            resource_type: Resource type (storyboards, characters, clues)
            resource_id: Resource ID (e.g. E1S01, character_name, clue_name)
            reference_images: List of reference images
            aspect_ratio: Aspect ratio, default 9:16 (portrait)
            image_size: Image size, default 1K
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number) tuple
        """
        from lib.image_backends.base import ImageGenerationRequest, ReferenceImage

        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. If file already exists, ensure the old file is tracked
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                **version_metadata,
            )

        if self._image_backend is None:
            raise RuntimeError("image_backend not configured")

        # 2. Record API call start
        call_id = await self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="image",
            model=self._image_backend.model,
            prompt=prompt,
            resolution=image_size,
            aspect_ratio=aspect_ratio,
            provider=self._image_backend.name,
            user_id=self._user_id,
            segment_id=resource_id if resource_type in ("storyboards", "videos") else None,
        )

        try:
            # 3. Convert reference image format and call ImageBackend
            ref_images: list[ReferenceImage] = []
            if reference_images:
                for ref in reference_images:
                    if isinstance(ref, dict):
                        img_val = ref.get("image", "")
                        ref_images.append(
                            ReferenceImage(
                                path=str(img_val),
                                label=str(ref.get("label", "")),
                            )
                        )
                    elif hasattr(ref, "__fspath__") or isinstance(ref, (str, Path)):
                        ref_images.append(ReferenceImage(path=str(ref)))
                    # Unsupported types (e.g. PIL Image) are ignored

            request = ImageGenerationRequest(
                prompt=prompt,
                output_path=output_path,
                reference_images=ref_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                project_name=self.project_name,
            )
            result = await self._image_backend.generate(request)

            # 4. Record call success
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="success",
                output_path=str(output_path),
                quality=getattr(result, "quality", None),
            )
        except Exception as e:
            # Record call failure
            logger.exception("Generation failed (%s)", "image")
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e),
            )
            raise

        # 5. Record new version
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            aspect_ratio=aspect_ratio,
            **version_metadata,
        )

        return output_path, new_version

    def generate_video(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: str | Path | Image.Image | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str = "8",
        resolution: str = "1080p",
        negative_prompt: str = "background music, BGM, soundtrack, musical accompaniment",
        **version_metadata,
    ) -> tuple[Path, int, any, str | None]:
        """
        Generate a video (with automatic version management, synchronous wrapper).

        Args:
            prompt: Video generation prompt
            resource_type: Resource type (videos)
            resource_id: Resource ID (e.g. E1S01)
            start_image: Starting frame image (for image-to-video mode)
            aspect_ratio: Aspect ratio, default 9:16 (portrait)
            duration_seconds: Video duration, options "4", "6", "8"
            resolution: Resolution, default "1080p"
            negative_prompt: Negative prompt
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number, video_ref, video_uri) 4-tuple
        """
        return self._sync(
            self.generate_video_async(
                prompt=prompt,
                resource_type=resource_type,
                resource_id=resource_id,
                start_image=start_image,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
                resolution=resolution,
                negative_prompt=negative_prompt,
                **version_metadata,
            )
        )

    async def generate_video_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: str | Path | Image.Image | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str = "8",
        resolution: str = "1080p",
        negative_prompt: str = "background music, BGM, soundtrack, musical accompaniment",
        **version_metadata,
    ) -> tuple[Path, int, any, str | None]:
        """
        Asynchronously generate a video (with automatic version management).

        Args:
            prompt: Video generation prompt
            resource_type: Resource type (videos)
            resource_id: Resource ID (e.g. E1S01)
            start_image: Starting frame image (for image-to-video mode)
            aspect_ratio: Aspect ratio, default 9:16 (portrait)
            duration_seconds: Video duration, options "4", "6", "8"
            resolution: Resolution, default "1080p"
            negative_prompt: Negative prompt
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number, video_ref, video_uri) 4-tuple
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. If file already exists, ensure the old file is tracked
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                duration_seconds=duration_seconds,
                **version_metadata,
            )

        # 2. Record API call start
        try:
            duration_int = int(duration_seconds) if duration_seconds else 8
        except (ValueError, TypeError):
            duration_int = 8

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        model_name = self._video_backend.model
        provider_name = self._video_backend.name
        configured_generate_audio = (
            await self._config.video_generate_audio(self.project_name) if self._config else False
        )
        effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)

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
            user_id=self._user_id,
            segment_id=resource_id if resource_type in ("storyboards", "videos") else None,
        )

        try:
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
                generate_audio=result.generate_audio,
            )
        except Exception as e:
            # Record call failure
            logger.exception("Generation failed (%s)", "video")
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e),
            )
            raise

        # 5. Record new version
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            duration_seconds=duration_seconds,
            **version_metadata,
        )

        return output_path, new_version, video_ref, video_uri
