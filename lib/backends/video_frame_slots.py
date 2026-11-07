"""Video request-time capability validation (``gate_video_request``) and frame-slot assembly (``plan_frame_slots``).

Pure functions extracted from ``MediaGenerator.generate_video_async``, independent of accounting,
version management, and provider calls, directly importable for capability-combination unit tests.

Slot positions contract with ``lib.references.reference_compression``: compressors return paths in
input-index order; callers restore ``VideoGenerationRequest`` ``start_image`` / ``end_image`` /
``reference_images`` using ``FrameSlotPlan``'s three indices. Array reference images always follow
first/last frames, so one start index selects all array entries.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError
from lib.backends.input_limits import InputLimit
from lib.backends.video_capabilities import reference_audio_count_limit, reference_image_limit

if TYPE_CHECKING:
    from PIL import Image

    from lib.references.reference_compression import ReferenceSpec

__all__ = [
    "FIRST_FRAME_ADAPTIVE_RATIO",
    "FrameSlotPlan",
    "VideoCapabilityProbe",
    "gate_video_request",
    "plan_frame_slots",
    "resolve_first_frame_aspect_ratio",
    "resolve_video_capabilities",
]

# Provider literal meaning follow first-frame image ratio, not user-fixed ratios; meaningful only for
# declaring ``first_frame_ratio_adaptive_only`` backends and unknown to general ratio resolvers.
FIRST_FRAME_ADAPTIVE_RATIO = "adaptive"


class VideoCapabilityProbe(Protocol):
    """Minimal backend interface for capability queries: read declarations without generate / resume.

    Intentionally narrower than ``VideoBackend``: callers/test doubles need only the used member, not
    the whole generation protocol. Optional tier-aware ``video_capabilities_for_tier`` is detected
    through ``resolve_video_capabilities``, outside this interface.
    """

    @property
    def video_capabilities(self) -> VideoCapabilities: ...


def resolve_video_capabilities(
    backend: VideoCapabilityProbe,
    *,
    service_tier: str = "default",
    resolution: str | None = None,
) -> VideoCapabilities:
    """Resolve backend video capabilities for this request's tier.

    Prefer implemented ``video_capabilities_for_tier``: backends such as Kling enable ``last_frame``
    only for specific ``service_tier`` values. Context-free ``video_capabilities`` must declare
    conservatively, otherwise rejecting legitimate tier-specific requests as unsupported.
    """
    tier_aware = getattr(backend, "video_capabilities_for_tier", None)
    if tier_aware is not None:
        return tier_aware(service_tier, resolution=resolution)
    return backend.video_capabilities


def resolve_first_frame_aspect_ratio(
    *,
    caps: VideoCapabilities | None,
    aspect_ratio: str,
    has_first_frame: bool,
) -> str:
    """Apply first-frame ratio overrides under ``caps.first_frame_ratio_adaptive_only``.

    Declaring backends accept only :data:`FIRST_FRAME_ADAPTIVE_RATIO` for image-to-video requests.
    Preserve the caller's original ``aspect_ratio`` user intent, still fully applied to storyboard images;
    choose only this actual video request's value. Return ``aspect_ratio`` unchanged for None ``caps``,
    undeclared constraints, or requests without first frames (text-only/references-only).

    Callers set ``has_first_frame`` from actual sent requests, never merely nonempty inputs:
    :func:`plan_frame_slots` accepts only ``str`` / ``Path`` file sources. Match actual
    ``VideoGenerationRequest.start_image`` to keep constraints aligned with sent requests.
    """
    if caps is not None and caps.first_frame_ratio_adaptive_only and has_first_frame:
        return FIRST_FRAME_ADAPTIVE_RATIO
    return aspect_ratio


@dataclass(frozen=True)
class FrameSlotPlan:
    """Compressor input specs and each request field's position.

    None ``start_index`` / ``end_index`` means omit that slot. None ``reference_start_index`` means no
    compressible array references; callers retain original ``reference_images``, preserving None / [] semantics.
    """

    specs: "list[ReferenceSpec]"
    start_index: int | None = None
    end_index: int | None = None
    reference_start_index: int | None = None


def gate_video_request(
    *,
    caps: VideoCapabilities | None,
    provider: str,
    model: str,
    prompt: str | None = None,
    has_image: bool = False,
    has_first_frame: bool = False,
    end_image: Path | None = None,
    reference_images: "list[Path] | None" = None,
    reference_audio_files: "list[Path] | None" = None,
    reference_audio_total_seconds: float | None = None,
) -> None:
    """Unified request capability preflight: raise ``VideoCapabilityError`` for violations, otherwise return silently.

    Centralize text-only support and four optional paths (first frame / last frame / image references /
    audio references), avoiding per-backend payload implementations that inconsistently fail or truncate.
    Silent truncation would still bill users for unintended output without notice. Reject only, never
    degrade: unsupported capabilities raise for user-readable upper-layer errors (``lib/i18n/*/errors.py``).

    Callers set ``has_image`` from actual image slots; False checks text-only support.
    Likewise ``has_first_frame`` reflects actual requests (``plan_frame_slots`` ``start_index``).

    None ``caps`` means capabilities were not queried. With none of the four paths, declarations cannot
    affect results and queries may be omitted. With any path input, None means reject unsupported,
    never invent permissive placeholder capabilities that would send unchecked requests.

    ``reference_audio_total_seconds`` is caller-probed combined reference duration. This function does
    no I/O/probing: metadata reads belong in assembly preflight with file access
    (:func:`lib.generation.media_generator.MediaGenerator.generate_video_async`). None means unknown
    from failed/unsupported probes; skip total-duration validation, never treat unknown as overflow,
    matching existing unavailable-media-probe permissive fallback.

    Unlike optional paths, ``prompt`` exists in every request. Skip length checks when ``caps`` does not
    declare ``max_prompt_chars``, including None ``caps``; undeclared limits never mean zero.

    Deliberately separate :func:`plan_frame_slots`: validation raises, assembly is pure, and timing differs.
    Validate before accounting scopes so hard failures incur neither charges nor failed ApiCall rows;
    assembly may happen afterward as needed.
    """
    prompt_limit = None if caps is None else caps.max_prompt_chars
    if prompt_limit is not None and prompt is not None and len(prompt) > prompt_limit:
        # Providers often silently truncate long prompts, such as wan2.7 documentation, without error codes,
        # still charging for unintended output. Refuse before paid requests, never permit them.
        raise VideoCapabilityError(
            "video_prompt_too_long",
            provider=provider,
            model=model,
            limit=prompt_limit,
            count=len(prompt),
        )

    if caps is not None and not has_image and not caps.text_to_video:
        raise VideoCapabilityError("video_capability_missing_t2v", provider=provider, model=model)

    if has_first_frame and (caps is None or not caps.first_frame):
        raise VideoCapabilityError("video_first_frame_unsupported", provider=provider, model=model)

    if end_image is not None and (caps is None or not caps.last_frame):
        raise VideoCapabilityError("video_last_frame_unsupported", provider=provider, model=model)

    if reference_images:
        # Effective entry/channel limit from lib.backends.video_capabilities: zero refuses, None unlisted,
        # leaving counts unchecked here and excess for upstream rejection.
        image_limit = InputLimit.unsupported() if caps is None else reference_image_limit(caps)
        if not image_limit.accepts_input:
            raise VideoCapabilityError("video_reference_images_unsupported", provider=provider, model=model)
        bound = image_limit.clamp_bound()
        if bound is not None and len(reference_images) > bound:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                provider=provider,
                model=model,
                limit=bound,
                count=len(reference_images),
            )

    if reference_audio_files:
        # Never silently drop audio for models without voice inputs: they would bill for random voices while users
        # assume character voices are controlled, discovering cross-clip inconsistency only during final composition.
        audio_limit = InputLimit.unsupported() if caps is None else reference_audio_count_limit(caps)
        if not audio_limit.accepts_input:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=provider, model=model)
        audio_bound = audio_limit.clamp_bound()
        if audio_bound is not None and len(reference_audio_files) > audio_bound:
            raise VideoCapabilityError(
                "video_reference_audio_exceeded",
                provider=provider,
                model=model,
                limit=audio_bound,
                count=len(reference_audio_files),
            )

        total_limit = None if caps is None else caps.max_reference_audio_total_seconds
        if (
            total_limit is not None
            and reference_audio_total_seconds is not None
            and reference_audio_total_seconds > total_limit
        ):
            raise VideoCapabilityError(
                "video_reference_audio_duration_exceeded",
                provider=provider,
                model=model,
                limit=total_limit,
                total=reference_audio_total_seconds,
            )


def plan_frame_slots(
    *,
    start_image: "str | Path | Image.Image | None" = None,
    end_image: Path | None = None,
    reference_images: "list[Path] | None" = None,
) -> FrameSlotPlan:
    """Assemble frame/reference slots from inputs: pure, without capability checks or exceptions.

    :func:`gate_video_request` validates capabilities. Here only flatten three request fields into
    compressor specs and record positions.

    Only ``str`` / ``Path`` ``start_image`` file sources enter compressors; ``PIL.Image`` and None
    stay outside specs, leaving corresponding request fields None.
    """
    from lib.references.reference_compression import ReferenceSpec, RefRole

    specs: list[ReferenceSpec] = []
    start_index: int | None = None
    end_index: int | None = None
    reference_start_index: int | None = None

    if isinstance(start_image, (str, Path)):
        start_index = len(specs)
        specs.append(ReferenceSpec(source=Path(start_image), role=RefRole.FRAME))
    if end_image is not None:
        end_index = len(specs)
        specs.append(ReferenceSpec(source=Path(end_image), role=RefRole.FRAME))
    if reference_images:
        reference_start_index = len(specs)
        specs.extend(ReferenceSpec(source=Path(r), role=RefRole.ARRAY) for r in reference_images)

    return FrameSlotPlan(
        specs=specs,
        start_index=start_index,
        end_index=end_index,
        reference_start_index=reference_start_index,
    )
