"""目录数据的封闭集合：媒体类型、币种、计量项与选择维度。

这些集合由代码定义，目录数据不能自创。实例侧遇到集合之外的计量项、选择维度或币种时，
所在价目表按未定价处理；构建侧直接拒绝。
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

MeterKind = Literal["token", "count"]


@dataclass(frozen=True)
class MeterSpec:
    """一个计量项的定义。

    ``quantity_per_price`` 是单价对应的计数量：token 类按每百万 token 标价，字符数按每万字标价，
    其余按每张、每秒、每次标价。免费额度按计数单位本身计，与这个换算无关。``kind`` 是计量项的类别，见
    :data:`MeterKind`。
    """

    media_types: frozenset[MediaType]
    quantity_per_price: int
    kind: MeterKind


def _token(media_types: tuple[MediaType, ...]) -> MeterSpec:
    return MeterSpec(frozenset(media_types), _PER_MILLION, "token")


def _count(media_types: tuple[MediaType, ...], quantity_per_price: int = 1) -> MeterSpec:
    return MeterSpec(frozenset(media_types), quantity_per_price, "count")


METERS: Final[Mapping[str, MeterSpec]] = MappingProxyType(
    {
        "uncached_input_tokens": _token(("text", "image", "audio")),
        "cache_read_tokens": _token(("text", "image")),
        "cache_write_5m_tokens": _token(("text",)),
        "cache_write_1h_tokens": _token(("text",)),
        "output_tokens": _token(("text", "image")),
        "audio_input_tokens": _token(("text",)),
        "output_images": _count(("image",)),
        "input_images": _count(("image", "video")),
        "uncached_image_input_tokens": _token(("image",)),
        "image_cache_read_tokens": _token(("image",)),
        "image_output_tokens": _token(("image",)),
        "output_seconds": _count(("video",)),
        "video_tokens": _count(("video",), _PER_MILLION),
        "input_video_seconds": _count(("video",)),
        "characters": _count(("audio",), _PER_TEN_THOUSAND),
        "audio_output_tokens": _token(("audio",)),
        "requests": _count(MEDIA_TYPES),
    }
)

CJK_DOUBLE_METER: Final = "characters"

ServiceTier = Literal["standard", "flex", "fast"]
SERVICE_TIERS: Final[tuple[ServiceTier, ...]] = ("standard", "flex", "fast")

DimensionKind = Literal["tier", "label", "flag", "seconds", "service_tier"]

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
    """``value`` 是否是已知选择维度 ``dimension`` 的合法取值。"""
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
