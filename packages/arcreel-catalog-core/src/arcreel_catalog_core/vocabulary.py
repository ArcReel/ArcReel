"""Closed catalog vocabularies: media types, currencies, meters, and selection dimensions.

These sets are code-defined; catalog data cannot introduce its own. When instances encounter unknown meters, selection dimensions, or currencies,
the containing price table is treated as unpriced; builds reject it outright.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

MediaType = Literal["text", "image", "video", "audio"]
MEDIA_TYPES: Final[tuple[MediaType, ...]] = ("text", "image", "video", "audio")

Currency = Literal["USD", "CNY"]
CURRENCIES: Final[tuple[Currency, ...]] = ("USD", "CNY")

_PER_MILLION: Final = 1_000_000
_PER_TEN_THOUSAND: Final = 10_000

#: Meter kind, determining what happens when usage is reported for a meter absent from the price table:
#:
#: - ``token``: total tokens are reported as disjoint meters (such as uncached input and cache reads); if any meter lacks a price,
#:   the cost of those tokens is unknown and the entire call is recorded as unpriced.
#: - ``count``: counted quantities such as requests, duration, images, and characters are independent billing axes (such as video seconds and reference image
#:   count). Absence from the price table means the maker does not charge for that meter, so it is not billed.
MeterKind = Literal["token", "count"]


@dataclass(frozen=True)
class MeterSpec:
    """Definition of one meter.

    ``quantity_per_price`` is the quantity per unit price: per million tokens for token meters, per ten thousand characters for character counts,
    and otherwise per image, second, or request. Free allowances use counting units directly, independent of this conversion. ``kind`` is the meter kind; see
    :data:`MeterKind`。
    """

    media_types: frozenset[MediaType]
    quantity_per_price: int
    kind: MeterKind


def _token(media_types: tuple[MediaType, ...]) -> MeterSpec:
    return MeterSpec(frozenset(media_types), _PER_MILLION, "token")


def _count(media_types: tuple[MediaType, ...], quantity_per_price: int = 1) -> MeterSpec:
    return MeterSpec(frozenset(media_types), quantity_per_price, "count")


#: Closed meter set. Usage does not overlap: uncached input, cache reads, and both cache-write tiers sum to total input.
METERS: Final[Mapping[str, MeterSpec]] = MappingProxyType(
    {
        # Tokens: the primary meter for text models; also used for text input to image and audio models.
        "uncached_input_tokens": _token(("text", "image", "audio")),
        "cache_read_tokens": _token(("text", "image")),
        "cache_write_5m_tokens": _token(("text",)),
        "cache_write_1h_tokens": _token(("text",)),
        "output_tokens": _token(("text", "image")),
        "audio_input_tokens": _token(("text",)),
        # Images
        "output_images": _count(("image",)),
        "input_images": _count(("image", "video")),
        "uncached_image_input_tokens": _token(("image",)),
        "image_cache_read_tokens": _token(("image",)),
        "image_output_tokens": _token(("image",)),
        # Video. Video tokens derive from output seconds and frame dimensions, measuring the same billing axis as seconds, so they are count meters:
        # entries priced per second do not charge video tokens, and entries priced per token do not charge seconds.
        "output_seconds": _count(("video",)),
        "video_tokens": _count(("video",), _PER_MILLION),
        "input_video_seconds": _count(("video",)),
        # Audio
        "characters": _count(("audio",), _PER_TEN_THOUSAND),
        "audio_output_tokens": _token(("audio",)),
        # General
        "requests": _count(MEDIA_TYPES),
    }
)

#: The only meter allowed to count each Han character as two.
CJK_DOUBLE_METER: Final = "characters"

ServiceTier = Literal["standard", "flex", "fast"]
SERVICE_TIERS: Final[tuple[ServiceTier, ...]] = ("standard", "flex", "fast")

DimensionKind = Literal["tier", "label", "flag", "seconds", "service_tier"]

#: Closed selection dimension set and value shapes. Values come from request fields actually sent to the provider.
#:
#: - ``context_tier``: the exclusive lower bound of the tier, a nonnegative integer, with 0 for the first tier. A request's
#:   total input tokens (including cache reads and writes) fall in the tier when above its bound and no greater than the next larger bound in the price table;
#:   all meters in the request use prices from this tier.
#: - ``resolution``, ``quality``: nonempty strings.
#: - ``audio`` (audio present), ``input_video`` (input includes video), ``reference_to_video`` (input form:
#:   reference video using images as subject references rather than first/last frames or text only): booleans.
#: - ``duration``: positive integer seconds.
#: - ``service_tier``: ``standard`` / ``flex`` / ``fast``.
DIMENSIONS: Final[Mapping[str, DimensionKind]] = MappingProxyType(
    {
        "context_tier": "tier",
        "resolution": "label",
        "quality": "label",
        "audio": "flag",
        "service_tier": "service_tier",
        "duration": "seconds",
        "input_video": "flag",
        "reference_to_video": "flag",
    }
)

#: Media types eligible for selection dimensions: conditional pricing in the price-table editor lists only dimensions available for the entry's media type. Values come from actual
#: provider request fields, so a dimension applies to media types with that field. This is editor guidance rather than a format constraint: neither builds
#: nor instance readers reject data on this basis.
DIMENSION_MEDIA_TYPES: Final[Mapping[str, frozenset[MediaType]]] = MappingProxyType(
    {
        "context_tier": frozenset({"text", "image", "audio"}),
        "resolution": frozenset({"image", "video"}),
        "quality": frozenset({"image"}),
        "audio": frozenset({"video"}),
        "service_tier": frozenset({"text", "image", "video"}),
        "duration": frozenset({"video"}),
        "input_video": frozenset({"video"}),
        "reference_to_video": frozenset({"video"}),
    }
)


def is_valid_dimension_value(dimension: str, value: object) -> bool:
    """Whether ``value`` is valid for the known selection dimension ``dimension``."""
    match DIMENSIONS.get(dimension):
        case "tier":
            return type(value) is int and value >= 0
        case "seconds":
            return type(value) is int and value > 0
        case "label":
            return isinstance(value, str) and value != ""
        case "flag":
            return type(value) is bool
        case "service_tier":
            return value in SERVICE_TIERS
        case None:
            return False
