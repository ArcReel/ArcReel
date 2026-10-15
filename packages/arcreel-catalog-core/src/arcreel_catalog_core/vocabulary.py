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

#: 计量项的类别，决定上报了用量、价目表却没有这个计量项时怎样处理：
#:
#: - ``token``：一份 token 总量按互不重叠的计量项拆开上报（如未缓存输入与缓存读），缺了哪一项的单价，
#:   这部分 token 的费用就无从得知，整笔记为未定价。
#: - ``count``：按次数、时长、张数、字符数这类可数的量计，各是一条独立的计费轴（如视频的秒数与参考图
#:   张数）。价目表没有这一项，表示厂商不按它收费，这一项不计费。
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


#: 计量项的封闭集合。各计量项的用量互不重叠：未缓存输入、缓存读与两档缓存写之和等于总输入。
METERS: Final[Mapping[str, MeterSpec]] = MappingProxyType(
    {
        # token：文本模型的主计量；图片与音频模型的文字输入也按它计。
        "uncached_input_tokens": _token(("text", "image", "audio")),
        "cache_read_tokens": _token(("text", "image")),
        "cache_write_5m_tokens": _token(("text",)),
        "cache_write_1h_tokens": _token(("text",)),
        "output_tokens": _token(("text", "image")),
        "audio_input_tokens": _token(("text",)),
        # 图片
        "output_images": _count(("image",)),
        "input_images": _count(("image", "video")),
        "uncached_image_input_tokens": _token(("image",)),
        "image_cache_read_tokens": _token(("image",)),
        "image_output_tokens": _token(("image",)),
        # 视频。视频 token 由输出秒数与画面尺寸换算而来，与秒数是同一条计费轴的两种量法，归计数类：
        # 按秒计价的条目不收它，按 token 计价的条目不收秒数。
        "output_seconds": _count(("video",)),
        "video_tokens": _count(("video",), _PER_MILLION),
        "input_video_seconds": _count(("video",)),
        # 音频
        "characters": _count(("audio",), _PER_TEN_THOUSAND),
        "audio_output_tokens": _token(("audio",)),
        # 通用
        "requests": _count(MEDIA_TYPES),
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
#: - ``audio``（是否有声）、``input_video``（输入形态：是否含输入视频）、``reference_to_video``（输入形态：
#:   是否是参考生视频，即以参考图作主体参考，而不是首尾帧或纯文本）：布尔值。
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
        "reference_to_video": "flag",
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
