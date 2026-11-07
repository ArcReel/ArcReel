"""GeminiImageBackend: image-generation logic extracted from GeminiClient."""

from __future__ import annotations

import asyncio
import json as json_module
import logging
from pathlib import Path
from typing import ClassVar

from PIL import Image

from lib.backends.gemini_shared import (
    VERTEX_SCOPES,
    RateLimiter,
    gemini_http_options,
    get_shared_rate_limiter,
    resolve_gemini_api_key,
    with_retry_async,
)
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    per_image_usage,
)
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.config.system_config import resolve_vertex_credentials_path
from lib.config.url_utils import normalize_base_url
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)

# Default image model.
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"


class GeminiImageBackend:
    """Gemini image backend supporting AI Studio and Vertex AI."""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    # Channels impose no reference-count limit; generation-specific Gemini image maxima are model-entry facts.
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
        image_model: str | None = None,
        base_url: str | None = None,
        credentials_path: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        from google import genai as _genai
        from google.genai import types as _types

        self._types = _types
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._backend_type = backend_type.strip().lower()
        self._image_model = image_model or DEFAULT_IMAGE_MODEL

        if self._backend_type == "vertex":
            from google.oauth2 import service_account

            credentials_file: Path | None = None
            credentials_file = Path(credentials_path) if credentials_path else resolve_vertex_credentials_path()

            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file, encoding="utf-8") as f:
                creds_data = json_module.load(f)
            project_id = creds_data.get("project_id")

            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self._client = _genai.Client(
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
        else:
            api_key = resolve_gemini_api_key(api_key)
            effective_base_url = normalize_base_url(base_url)
            self._client = _genai.Client(
                api_key=api_key, http_options=gemini_http_options(effective_base_url, transport_layer)
            )

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._image_model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def _usage(image_size: str | None) -> Usage:
        """Report output image counts and requested ``image_size`` resolution, omitted when unsent.

        References bill as input tokens, never image counts, avoiding duplicate metering. Input/thinking
        tokens are currently absent from price tables and likewise unreported.
        """
        return per_image_usage(
            output_images=1,
            input_images=None,
            dimensions={"resolution": image_size} if image_size is not None else None,
        )

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        return GeminiImageBackend._usage(facts.image_size)

    @with_retry_async(max_attempts=5, backoff_seconds=(2, 4, 8, 16, 32))
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate images asynchronously."""
        # 1. Rate limiting.
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._image_model)

        # 2. Assemble contents: reference images first in array order, prompt last.
        #    Reference_Images prompt declarations identify images as 图N; insert no text labels between images.
        refs = request.reference_images
        # Opening/decoding references blocks I/O; offload individually and await concurrently without blocking event loops.
        contents: list = await asyncio.gather(*[asyncio.to_thread(self._load_image_detached, ref.path) for ref in refs])
        contents.append(request.prompt)

        image_config_kwargs: dict = {"aspect_ratio": request.aspect_ratio}
        if request.image_size is not None:
            image_config_kwargs["image_size"] = request.image_size

        config = self._types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=self._types.ImageConfig(**image_config_kwargs),
        )

        # 4. Call asynchronous API.
        logger.info(
            "调用 %s 图片 SDK payload=%s",
            self.name,
            format_kwargs_for_log(
                {"model": self._image_model, "contents": contents, "image_config": image_config_kwargs}
            ),
        )
        response = await self._client.aio.models.generate_content(
            model=self._image_model, contents=contents, config=config
        )

        # 5. Parse responses/save.
        # Providers returned: decoding/persistence cannot be interrupted by cancellation, per backend.md postresponse accounting rules.
        await run_noninterruptible_sync(self._process_image_response, response, request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._image_model,
            usage=self._usage(request.image_size),
        )

    @staticmethod
    def _load_image_detached(image_path: str | Path) -> Image.Image:
        """Load images from paths, detaching underlying file handles."""
        with Image.open(image_path) as img:
            return img.copy()

    @staticmethod
    def _process_image_response(response, output_path: Path) -> Image.Image:
        """Parse image-generation responses and save files."""
        for part in response.parts:
            if part.inline_data is not None:
                image = part.as_image()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(output_path)
                return image
        raise RuntimeError("API 未返回图片")
