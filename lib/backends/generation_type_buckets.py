"""Task-type bucket membership (t2i / i2i / i2v / r2v), translating existing capability declarations without duplicating them.

Each bucket has fixed evidence sources:

- Images: effective ``lib.backends.image_capabilities``, intersecting entry text/image-to-image model facts
  with channel declarations (built-in ``CHANNEL_CAPABILITIES``, custom endpoint-spec ``image_capabilities``).
  Custom overrides replace model dimensions; ComfyUI ignores entries. Match execution ``MediaGenerator``
  checks. Image-to-image additionally requires effective reference limits accepting input; explicit zero
  excludes models from i2i.
- Video: effective ``lib.backends.video_capabilities``, evaluating channel declarations (built-in
  ``VideoCapabilities``, system-determined custom endpoints) and model entries, then capability overrides.
  i2v uses ``first_frame``; r2v uses reference limits (unlisted or positive accepts, explicit zero excludes).
  ComfyUI ignores entries.

Buckets promise executable selected combinations, so share evaluated execution-gate results. Video's
two predicates come from ``lib.config.resolver.video_capability_satisfied``, also shared by resolution gates.

Unresolvable cases (retired endpoints, backends without capability functions) yield empty sets, never
all buckets. Prefer missing candidates over presenting combinations that inevitably fail execution.

Excluded models carry :class:`BucketFit` reasons: unmet dimensions from the same evaluation, named like
``lib.custom_provider.capability_view`` interface dimensions. Candidate APIs explain missing choices with these.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import (
    builtin_channel_capabilities,
    builtin_channel_reference_limit,
    custom_image_model_capabilities,
    effective_image_capabilities,
    entry_reference_limit,
    image_reference_limit,
    image_reference_override,
    intersect_image_capabilities,
)
from lib.backends.input_limits import LimitState
from lib.catalog.query import CatalogModel, user_added_capability_overrides
from lib.config.resolver import video_capability_satisfied
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.endpoints import (
    EndpointSpec,
    endpoint_reference_image_limit,
    endpoint_to_image_capabilities,
    endpoint_to_media_type,
    get_endpoint_spec,
)

GenerationTypeBucket = Literal["t2i", "i2i", "i2v", "r2v"]

#: Buckets per media_type; text/audio have none and are absent.
BUCKETS_BY_MEDIA_TYPE: dict[str, tuple[GenerationTypeBucket, ...]] = {
    "image": ("t2i", "i2i"),
    "video": ("i2v", "r2v"),
}


#: Membership dimensions, named like interface dimensions. ``max_reference_images`` means zero registered reference count
#: for otherwise supported image-to-image; for video r2v it means refused images, from absent channel support or zero entry counts.
BucketDimension = Literal["text_to_image", "image_to_image", "max_reference_images", "first_frame"]


@dataclass(frozen=True)
class BucketFit:
    """A model's membership determination across its media type's task buckets.

    ``unmet`` contains excluded buckets only, mapped to unmet dimensions. Empty tuples mean unresolved
    retired endpoints or missing backend capability functions, likewise excluding membership.
    Text/audio models have no buckets and empty ``unmet``.
    """

    media_type: str
    unmet: Mapping[GenerationTypeBucket, tuple[BucketDimension, ...]] = field(default_factory=dict)

    @property
    def buckets(self) -> frozenset[GenerationTypeBucket]:
        return frozenset(b for b in BUCKETS_BY_MEDIA_TYPE.get(self.media_type, ()) if b not in self.unmet)

    @classmethod
    def undetermined(cls, media_type: str) -> BucketFit:
        return cls(media_type, dict.fromkeys(BUCKETS_BY_MEDIA_TYPE.get(media_type, ()), ()))


def _image_fit(
    caps: Iterable[ImageCapability], *, entry_limit: int | None, channel_limit: int | None, override: int | None = None
) -> BucketFit:
    caps = frozenset(caps)
    unmet: dict[GenerationTypeBucket, tuple[BucketDimension, ...]] = {}
    if ImageCapability.TEXT_TO_IMAGE not in caps:
        unmet["t2i"] = ("text_to_image",)
    reference = image_reference_limit(
        capabilities=caps, entry_limit=entry_limit, channel_limit=channel_limit, override=override
    )
    if not reference.accepts_input:
        unmet["i2i"] = ("image_to_image",) if reference.state == LimitState.UNSUPPORTED else ("max_reference_images",)
    return BucketFit("image", unmet)


#: The sole dimension per video bucket, matching ``video_capability_satisfied`` predicates exactly.
_VIDEO_BUCKET_DIMENSION: dict[GenerationTypeBucket, BucketDimension] = {
    "i2v": "first_frame",
    "r2v": "max_reference_images",
}


def _video_fit(has_i2v: bool, max_reference_images: int | None) -> BucketFit:
    return BucketFit(
        "video",
        {
            generation_type: (_VIDEO_BUCKET_DIMENSION[generation_type],)
            for generation_type in ("i2v", "r2v")
            if not video_capability_satisfied(
                generation_type=generation_type, first_frame=has_i2v, max_reference_images=max_reference_images
            )
        },
    )


def builtin_model_buckets(provider_id: str, model_id: str, model_info: CatalogModel) -> frozenset[GenerationTypeBucket]:
    """Built-in model task buckets; text/audio models always yield empty sets."""
    return builtin_model_bucket_fit(provider_id, model_id, model_info).buckets


def builtin_model_bucket_fit(provider_id: str, model_id: str, model_info: CatalogModel) -> BucketFit:
    """Built-in model bucket membership with exclusion reasons."""
    if model_info.media_type == "image":
        try:
            channel = builtin_channel_capabilities(provider_id, model_id)
            channel_limit = builtin_channel_reference_limit(provider_id, model_id)
        except ValueError:
            return BucketFit.undetermined("image")
        return _image_fit(
            effective_image_capabilities(channel, model_id=model_id, builtin_provider=provider_id),
            entry_limit=model_info.max_reference_images,
            override=image_reference_override(user_added_capability_overrides(provider_id, model_id)),
            channel_limit=channel_limit,
        )
    if model_info.media_type != "video":
        return BucketFit(model_info.media_type)

    try:
        caps = builtin_video_capabilities_for_model(provider_id, model_id)
    except ValueError:
        return BucketFit.undetermined("video")
    return _video_fit(caps.first_frame, caps.max_reference_images)


def custom_model_buckets(
    *,
    endpoint: str,
    model_id: str,
    capability_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> frozenset[GenerationTypeBucket]:
    """Custom model task buckets; text/audio endpoints and unknown endpoints always yield empty sets."""
    return custom_model_bucket_fit(
        endpoint=endpoint,
        model_id=model_id,
        capability_overrides=capability_overrides,
        endpoint_spec=endpoint_spec,
        entry_binding=entry_binding,
    ).buckets


def custom_model_bucket_fit(
    *,
    endpoint: str,
    model_id: str,
    capability_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> BucketFit:
    """Custom model membership with exclusion reasons; unknown endpoints lack media types and therefore buckets."""
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else endpoint_to_media_type(endpoint)
    except ValueError:
        return BucketFit("")

    if media_type == "image":
        caps = (
            endpoint_spec.image_capabilities if endpoint_spec is not None else endpoint_to_image_capabilities(endpoint)
        )
        if caps is None:
            return BucketFit.undetermined("image")
        spec = endpoint_spec or get_endpoint_spec(endpoint)
        model_caps = custom_image_model_capabilities(
            model_id, overrides=capability_overrides, endpoint_kind=spec.kind, entry_binding=entry_binding
        )
        return _image_fit(
            intersect_image_capabilities(caps, model_caps),
            entry_limit=entry_reference_limit(
                model_id, builtin_provider=None, endpoint_kind=spec.kind, entry_binding=entry_binding
            ),
            channel_limit=endpoint_reference_image_limit(spec),
            override=image_reference_override(capability_overrides, endpoint_kind=spec.kind),
        )
    if media_type != "video":
        return BucketFit(media_type)

    try:
        video_caps = synthesize_video_capabilities(
            endpoint=endpoint,
            model_id=model_id,
            overrides=capability_overrides,
            endpoint_spec=endpoint_spec,
            entry_binding=entry_binding,
        )
    except ValueError:
        return BucketFit.undetermined("video")
    return _video_fit(video_caps.first_frame, video_caps.max_reference_images)
