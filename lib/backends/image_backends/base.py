"""Core interfaces for image-generation services."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from arcreel_market_core.video_backend_contract import IMAGE_MIME_TYPES, ProviderResponseStage
from lib.backends.artifact_download_guard import IMAGE_ARTIFACT_MAX_BYTES, artifact_http_client
from lib.backends.backend_runtime import stream_to_file
from lib.backends.data_uri import image_to_data_uri as _image_to_data_uri
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry


def image_to_base64_data_uri(image_path: Path) -> str:
    """Convert local images to base64 data URIs."""
    return _image_to_data_uri(image_path, IMAGE_MIME_TYPES)


async def download_image_to_path(url: str, output_path: Path, *, timeout: int = 60) -> None:  # noqa: ASYNC109 -- forward httpx I/O timeouts, not async cancellation deadlines.
    """Stream image downloads asynchronously to local files under artifact destination/size guards."""
    async with artifact_http_client() as client:
        await stream_to_file(client, url, output_path, max_bytes=IMAGE_ARTIFACT_MAX_BYTES, timeout=timeout)


async def save_image_from_response_item(item, output_path: Path) -> None:
    """Extract images from compatible OpenAI SDK ``response.data[i]`` into local files.

    Prefer ``b64_json``, fall back to ``url`` downloads when empty, raise ``ValueError`` if both are empty.

    Providers already responded: decoding/writing belongs to backend.md noninterruptible postresponse
    accounting. Cancellation during writes lets threads finish and disappears; later caller transcoding /
    artifact commits continue. Reusing this helper satisfies that rule. URL network retrieval remains
    interruptible; final renames are not, as the standards explain.
    """
    b64 = getattr(item, "b64_json", None)
    if b64:

        def _decode_and_save() -> None:
            # Offload decoding/writes together, avoiding CPU-intensive base64 work on event loops.
            image_bytes = base64.b64decode(b64)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_bytes)

        await run_noninterruptible_sync(_decode_and_save)
        return
    url = getattr(item, "url", None)
    if url:
        await download_image_to_path(url, output_path)
        return
    raise ValueError("图片生成响应既无 b64_json 也无 url")


class ImageCapability(StrEnum):
    """Supported image-backend capability enum."""

    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"


#: Facts for channels implementing both T2I/I2I. Built-ins declare class ``CHANNEL_CAPABILITIES``,
#: readable without instances through ``registry.channel_capabilities``. Entries state model acceptance;
#: effective capabilities intersect both sides (``lib.backends.image_capabilities``).
BOTH_IMAGE_CAPABILITIES: frozenset[ImageCapability] = frozenset(
    {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
)


@dataclass
class ReferenceImage:
    """Reference image, identified as 图N by prompt array order; transport carries paths only."""

    path: str


@dataclass
class ImageGenerationRequest:
    """Common image-generation request; backends ignore unsupported fields."""

    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str | None = None
    project_name: str | None = None
    seed: int | None = None
    #: Provider-response diagnostics like ``VideoGenerationRequest``. Only callers needing stage snapshots
    #: such as endpoint tests supply callbacks. Production image calls keep ``None``.
    on_provider_response: Callable[[ProviderResponseStage, object], Awaitable[None]] | None = None


@dataclass(frozen=True)
class ImageRequestFacts:
    """Presubmission request facts for channel usage estimation, see built-in ``estimate_usage`` methods.

    ``reference_count`` is image count, ``None`` when batch estimates know I2I but not counts.
    ``entry`` identifies model entries, ``None`` when unmatched, from which channels read estimation
    facts such as output-token bases. :func:`lib.backends.image_usage.estimate_builtin_image_usage`
    and :meth:`lib.custom_provider.metering.CustomPriceSource.image_usage` populate them from execution's source.
    """

    model: str
    aspect_ratio: str
    image_size: str | None = None
    reference_count: int | None = 0
    entry: CatalogEntry | None = None


#: Built-in estimators derive sent fields/usage from request facts, sharing ``generate`` request transformations.
ImageUsageEstimator = Callable[[ImageRequestFacts], Usage]


@dataclass
class ImageGenerationResult:
    """Common image-generation result.

    ``usage`` reports metering items and selection dimensions under contracts (docs/adr/0100).
    Dimensions come from actually sent fields; meter only actual usage. Built-ins all report it;
    custom wrappers supplement generic values when wrapped channels omit it
    (:mod:`lib.custom_provider.metering`).
    """

    image_path: Path
    provider: str
    model: str
    image_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    # OpenAI GPT Image token splits; other backends default None.
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    usage: Usage | None = None


def per_image_usage(
    *,
    output_images: int | None,
    input_images: int | None,
    dimensions: dict[str, object] | None = None,
) -> Usage:
    """Per-image channel usage: output/reference counts and selection dimensions.

    Report zero reference counts too: missing metering prevents estimation. ``None`` means unknown
    counts, only in estimates, and is omitted.
    """
    quantities: dict[str, float] = {}
    if output_images is not None:
        quantities["output_images"] = output_images
    if input_images is not None:
        quantities["input_images"] = input_images
    return Usage(quantities=quantities, dimensions=dict(dimensions or {}))


class ImageBackend(Protocol):
    """Image-generation backend protocol."""

    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[ImageCapability]:
        """Channel-implemented text/image-to-image facts, excluding model facts."""
        ...

    async def reference_image_limit(self) -> int | None:
        """Channel per-request reference limit; ``None`` means channel-unbounded counts.

        Built-ins statically declare ``CHANNEL_REFERENCE_LIMIT`` and return it here. Model-specific limits
        belong to entries, never name-based channel branches. Effective minima come from
        ``lib.backends.image_capabilities``. Orchestration clips before rendering 图N indices
        (``lib.prompts.reference_image_numbering.clamp_reference_images``). Asynchronous because channels
        such as OpenRouter may query model catalogs for ``input_references``.
        """
        ...

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


class ImageOutputMissingError(RuntimeError):
    """Successful provider responses without images, often moderation filters or upstream anomalies.

    Like :class:`ImageCapabilityError`, carry stable codes/params only; readers render localized copy.
    """

    code = "image_output_missing"

    def __init__(self, *, model: str) -> None:
        self.params = {"model": model}
        super().__init__(f"{self.code}: model={model}")


class ImageOutputFormatError(RuntimeError):
    """Provider images cannot serve as bitmap artifacts, such as vector SVG; carry stable code/params."""

    code = "image_output_format_unsupported"

    def __init__(self, *, model: str, media_type: str) -> None:
        self.params = {"model": model, "media_type": media_type}
        super().__init__(f"{self.code}: model={model} media_type={media_type}")


class ImageCapabilityError(RuntimeError):
    """Image capability mismatch shared by endpoint matching/generator gates.

    Carry stable code/context params, never localized strings. Routers render _t(code, **params);
    Workers store code/params in task.error_message, leaving copy to readers using Accept-Language.
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)
