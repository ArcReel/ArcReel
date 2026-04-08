"""ArkImageBackend — Volcano Engine Ark Seedream image generation backend."""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

from lib.ark_shared import create_ark_client
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_ARK
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)


class ArkImageBackend:
    """Ark (Volcano Engine) Seedream image generation backend."""

    DEFAULT_MODEL = "doubao-seedream-5-0-lite-260128"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @with_retry_async()
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Asynchronously generate an image (T2I / I2I)."""
        # Build SDK parameters
        kwargs: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "response_format": "b64_json",
        }

        # I2I: read reference images and convert to base64 data URIs
        if request.reference_images:
            data_uris = [image_to_base64_data_uri(Path(ref.path)) for ref in request.reference_images]
            # Pass a string for a single image, a list for multiple
            kwargs["image"] = data_uris[0] if len(data_uris) == 1 else data_uris

        if request.seed is not None:
            kwargs["seed"] = request.seed

        # Wrap the synchronous SDK call with to_thread
        response = await asyncio.to_thread(
            self._client.images.generate,
            **kwargs,
        )

        # Decode and save
        image_data = base64.b64decode(response.data[0].b64_json)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(image_data)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
        )
