"""ArkVideoBackend — Ark (Volcano Engine) video generation backend."""

from __future__ import annotations

import asyncio
import logging

from lib.ark_shared import create_ark_client
from lib.providers import PROVIDER_ARK
from lib.retry import BASE_RETRYABLE_ERRORS, _should_retry, with_retry_async
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class ArkVideoBackend:
    """Ark (Volcano Engine) video generation backend."""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    _MODEL_CAPABILITIES: dict[str, set[VideoCapability]] = {
        "doubao-seedance-2-0-260128": {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
            VideoCapability.SEED_CONTROL,
        },
        "doubao-seedance-2-0-fast-260128": {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
            VideoCapability.GENERATE_AUDIO,
            VideoCapability.SEED_CONTROL,
        },
    }

    _DEFAULT_CAPABILITIES: set[VideoCapability] = {
        VideoCapability.TEXT_TO_VIDEO,
        VideoCapability.IMAGE_TO_VIDEO,
        VideoCapability.GENERATE_AUDIO,
        VideoCapability.SEED_CONTROL,
        VideoCapability.FLEX_TIER,
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities = self._MODEL_CAPABILITIES.get(self._model, self._DEFAULT_CAPABILITIES)

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
        """Generate video. Task creation and polling use separate retry scopes to avoid re-creating tasks on transient errors."""
        task_id = await self._create_task(request)
        return await self._poll_until_done(task_id, request)

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> str:
        """Create an Ark video generation task (with retry protection)."""
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
        logger.info("Ark task created: %s", create_result.id)
        return create_result.id

    async def _poll_until_done(self, task_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Poll task status until complete; transient errors only retry the current poll request."""
        poll_interval = 10 if request.service_tier == "default" else 60
        max_wait_time = 600 if request.service_tier == "default" else 3600
        elapsed = 0

        while True:
            try:
                result = await asyncio.to_thread(
                    self._client.content_generation.tasks.get,
                    task_id=task_id,
                )
            except Exception as e:
                if _should_retry(e, BASE_RETRYABLE_ERRORS):
                    logger.warning("Ark polling error (will retry): %s - %s", type(e).__name__, str(e)[:200])
                    elapsed += poll_interval
                    if elapsed >= max_wait_time:
                        raise
                    await asyncio.sleep(poll_interval)
                    continue
                raise

            if result.status == "succeeded":
                break
            elif result.status in ("failed", "expired"):
                error_msg = getattr(result, "error", None) or "Unknown error"
                raise RuntimeError(f"Ark video generation failed: {error_msg}")

            elapsed += poll_interval
            if elapsed >= max_wait_time:
                raise TimeoutError(f"Ark video generation timed out ({max_wait_time}s)")

            logger.info(
                "Ark video generating... status: %s, elapsed: %d seconds",
                result.status,
                elapsed,
            )
            await asyncio.sleep(poll_interval)

        # Download video
        video_url = result.content.video_url
        await download_video(video_url, request.output_path)

        # Extract result metadata
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
