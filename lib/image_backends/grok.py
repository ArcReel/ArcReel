"""GrokImageBackend — xAI Grok (Aurora) image generation backend."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.grok_shared import create_grok_client
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_GROK
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"

_SUPPORTED_ASPECT_RATIOS = {
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "auto",
}


def _validate_aspect_ratio(aspect_ratio: str) -> str:
    """Validate whether aspect_ratio is in the Grok-supported list; warn and pass through if not."""
    if aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
        logger.warning("Grok may not support aspect_ratio=%s, will pass through to API", aspect_ratio)
    return aspect_ratio


class GrokImageBackend:
    """xAI Grok (Aurora) image generation backend, supporting T2I and I2I."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @with_retry_async()
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate an image (T2I or I2I)."""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
            "resolution": _map_image_size_to_resolution(request.image_size),
        }

        # I2I: convert all reference images to base64 data URI list
        if request.reference_images:
            data_uris = []
            for ref in request.reference_images:
                ref_path = Path(ref.path)
                if ref_path.exists():
                    data_uris.append(image_to_base64_data_uri(ref_path))
            if data_uris:
                generate_kwargs["image_urls"] = data_uris
                logger.info("Grok I2I mode: %d reference image(s)", len(data_uris))

        logger.info("Grok image generation started: model=%s", self._model)
        response = await self._client.image.sample(**generate_kwargs)

        # Moderation check
        if not response.respect_moderation:
            raise RuntimeError("Grok image generation rejected by content moderation")

        # Download image to local file
        await _download_image(response.url, request.output_path)

        logger.info("Grok image download complete: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            image_uri=response.url,
        )


def _map_image_size_to_resolution(image_size: str) -> str:
    """Map the generic image_size (e.g. '1K', '2K') to the Grok resolution parameter."""
    mapping = {
        "1K": "1k",
        "2K": "2k",
        "1k": "1k",
        "2k": "2k",
    }
    return mapping.get(image_size, "1k")


async def _download_image(url: str, output_path: Path, *, timeout: int = 60) -> None:
    """Download an image from a URL to a local file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.get(url, timeout=timeout)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
