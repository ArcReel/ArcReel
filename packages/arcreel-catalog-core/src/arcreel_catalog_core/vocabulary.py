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


@dataclass(frozen=True)
class MeterSpec:
    """一个计量项的定义。

    ``quantity_per_price`` 是单价对应的计数量：token 类按每百万 token 标价，字符数按每万字标价，
    其余按每张、每秒、每次标价。免费额度按计数单位本身计，与这个换算无关。
    """

    media_types: frozenset[MediaType]
    quantity_per_price: int


def _meter(media_types: tuple[MediaType, ...], quantity_per_price: int = 1) -> MeterSpec:
    return MeterSpec(frozenset(media_types), quantity_per_price)


#: 计量项的封闭集合。各计量项的用量互不重叠：未缓存输入、缓存读与两档缓存写之和等于总输入。
METERS: Final[Mapping[str, MeterSpec]] = MappingProxyType(
    {
        # token：文本模型的主计量；图片与音频模型的文字输入也按它计。
        "uncached_input_tokens": _meter(("text", "image", "audio"), _PER_MILLION),
        "cache_read_tokens": _meter(("text", "image"), _PER_MILLION),
        "cache_write_5m_tokens": _meter(("text",), _PER_MILLION),
        "cache_write_1h_tokens": _meter(("text",), _PER_MILLION),
        "output_tokens": _meter(("text", "image"), _PER_MILLION),
        "audio_input_tokens": _meter(("text",), _PER_MILLION),
        # 图片
        "output_images": _meter(("image",)),
        "input_images": _meter(("image", "video")),
        "uncached_image_input_tokens": _meter(("image",), _PER_MILLION),
        "image_cache_read_tokens": _meter(("image",), _PER_MILLION),
        "image_output_tokens": _meter(("image",), _PER_MILLION),
        # 视频
        "output_seconds": _meter(("video",)),
        "video_tokens": _meter(("video",), _PER_MILLION),
        "input_video_seconds": _meter(("video",)),
        # 音频
        "characters": _meter(("audio",), _PER_TEN_THOUSAND),
        "audio_output_tokens": _meter(("audio",), _PER_MILLION),
        # 通用
        "requests": _meter(MEDIA_TYPES),
    }
)

#: 唯一可以带「汉字计 2」的计量项。
CJK_DOUBLE_METER: Final = "characters"

ServiceTier = Literal["standard", "flex", "fast"]
SERVICE_TIERS: Final[tuple[ServiceTier, ...]] = ("standard", "flex", "fast")

DimensionKind = Literal["tier", "label", "flag", "seconds", "service_tier"]

#: 选择维度的封闭集合与取值形态。取值来自真正发给供应商的请求字段。
#:
#: - ``context_tier``（上下文档）：取值为该档的下界（不含），非负整数，首档写 0。单次请求的
#:   输入 token 总量（含缓存读写）大于某个取值、且不大于价目表中下一个更大的取值时落入该档，
#:   整个请求的各计量项都按这一档取单价。
#: - ``resolution``（分辨率）、``quality``（质量档）：非空字符串。
#: - ``audio``（是否有声）、``input_video``（输入形态：是否含输入视频）：布尔值。
#: - ``duration``（时长档）：正整数秒。
#: - ``service_tier``（服务档）：``standard`` / ``flex`` / ``fast``。
DIMENSIONS: Final[Mapping[str, DimensionKind]] = MappingProxyType(
    {
        "context_tier": "tier",
        "resolution": "label",
        "quality": "label",
        "audio": "flag",
        "service_tier": "service_tier",
        "duration": "seconds",
        "input_video": "flag",
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
