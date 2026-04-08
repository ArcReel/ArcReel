"""Core interface definitions for the image generation service layer."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from lib.video_backends.base import IMAGE_MIME_TYPES


def image_to_base64_data_uri(image_path: Path) -> str:
    """Convert a local image to a base64 data URI."""
    suffix = image_path.suffix.lower()
    mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
    image_data = image_path.read_bytes()
    b64 = base64.b64encode(image_data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


class ImageCapability(StrEnum):
    """Capabilities supported by an image backend."""

    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"


@dataclass
class ReferenceImage:
    """Reference image."""

    path: str
    label: str = ""


@dataclass
class ImageGenerationRequest:
    """Generic image generation request. Each Backend ignores unsupported fields."""

    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str = "1K"
    project_name: str | None = None
    seed: int | None = None


@dataclass
class ImageGenerationResult:
    """Generic image generation result."""

    image_path: Path
    provider: str
    model: str
    image_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    quality: str | None = None


class ImageBackend(Protocol):
    """Image generation backend protocol."""

    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[ImageCapability]: ...
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...
