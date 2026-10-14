"""目录数据读入后的类型。

一份目录数据含数据集版本、模型条目与内置供应商清单。模型条目描述模型本身的事实，身份是
「模型厂商/官方模型名」，两栖模型的两个条目再以媒体类型区分；内置供应商清单列出每个内置供应商
官方收录的供应商模型，以及按媒体类型指定的默认模型。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .vocabulary import CURRENCIES, DIMENSIONS, METERS, MediaType


@dataclass(frozen=True)
class TextFacts:
    image_input: bool = False
    structured_output: bool = False
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ImageFacts:
    text_to_image: bool = False
    image_to_image: bool = False
    resolutions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DurationConstraints:
    """在可选时长之上按条件收窄：指定分辨率时、使用参考图时各自允许的时长。"""

    by_resolution: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    with_reference_images: tuple[int, ...] = ()


@dataclass(frozen=True)
class VideoFacts:
    durations: tuple[int, ...] = ()
    resolutions: tuple[str, ...] = ()
    duration_constraints: DurationConstraints = field(default_factory=DurationConstraints)


@dataclass(frozen=True)
class AudioFacts:
    pass


ModelFacts = TextFacts | ImageFacts | VideoFacts | AudioFacts


@dataclass(frozen=True)
class PriceRow:
    """一行「条件 → 单价」。``when`` 中缺省的选择维度表示任意取值。"""

    when: Mapping[str, object]
    price: float


@dataclass(frozen=True)
class Meter:
    """价目表中的一个计量项：按 ``dims`` 列出的选择维度分档给出单价。

    ``defaults`` 是请求缺少某个维度取值时使用的取值；``free_allowance`` 是单请求免费额度，
    按计量项的计数单位计；``cjk_double`` 只用于字符数，表示汉字计 2 个字符。
    """

    meter: str
    dims: tuple[str, ...]
    rows: tuple[PriceRow, ...]
    defaults: Mapping[str, object] = field(default_factory=dict)
    free_allowance: float | None = None
    cjk_double: bool = False


@dataclass(frozen=True)
class PriceTable:
    """模型条目上的官方价格：单币种，由若干计量项组成。"""

    currency: str
    meters: tuple[Meter, ...]

    @property
    def unrecognized_keys(self) -> tuple[str, ...]:
        """当前代码不认识的币种、计量项与选择维度，按出现顺序去重。"""
        keys: list[str] = []
        if self.currency not in CURRENCIES:
            keys.append(self.currency)
        for meter in self.meters:
            if meter.meter not in METERS:
                keys.append(meter.meter)
            keys.extend(dim for dim in meter.dims if dim not in DIMENSIONS)
        return tuple(dict.fromkeys(keys))

    @property
    def is_priceable(self) -> bool:
        """为 False 时，这份价目表依赖当前代码不认识的内容，按它计价的调用记为未定价。"""
        return not self.unrecognized_keys


@dataclass(frozen=True)
class EntryKey:
    maker: str
    name: str
    media_type: MediaType

    @property
    def ref(self) -> str:
        """「模型厂商/官方模型名」，内置供应商清单按它引用条目。"""
        return f"{self.maker}/{self.name}"


@dataclass(frozen=True)
class CatalogEntry:
    maker: str
    name: str
    media_type: MediaType
    display_name: str
    facts: ModelFacts
    aliases: tuple[str, ...] = ()
    discontinued: bool = False
    context_length: int | None = None
    price: PriceTable | None = None

    @property
    def key(self) -> EntryKey:
        return EntryKey(self.maker, self.name, self.media_type)


@dataclass(frozen=True)
class BuiltinProviderModel:
    """内置供应商官方收录的一个供应商模型。

    ``entry`` 是「模型厂商/官方模型名」；只有引用两栖模型时才需要 ``media_type`` 指明是哪一个条目。
    ``multiplier`` 是供应商模型倍率；``min_app_version`` 之下的应用版本不展示、也不启用它。
    同一供应商下，供应商模型以 API 模型名与所引用条目的媒体类型区分。
    """

    api_model_name: str
    entry: str
    media_type: str | None = None
    multiplier: float = 1.0
    min_app_version: str | None = None


@dataclass(frozen=True)
class BuiltinProviderListing:
    """一个内置供应商的清单。``defaults`` 按媒体类型给出默认模型的 API 模型名。"""

    models: tuple[BuiltinProviderModel, ...]
    defaults: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogData:
    dataset_version: str
    entries: tuple[CatalogEntry, ...]
    builtin_providers: Mapping[str, BuiltinProviderListing]

    def entry_of(self, model: BuiltinProviderModel) -> CatalogEntry | None:
        """供应商模型引用的条目；引用不到或无法唯一确定（两栖模型未指明媒体类型）时返回 None。"""
        matches = matching_entries(self.entries, model.entry, model.media_type)
        return matches[0] if len(matches) == 1 else None


def matching_entries(entries: tuple[CatalogEntry, ...], ref: str, media_type: str | None) -> list[CatalogEntry]:
    """按「模型厂商/官方模型名」与可选的媒体类型找条目，身份相同的只取第一个。"""
    maker, _, name = ref.partition("/")
    found: dict[EntryKey, CatalogEntry] = {}
    for entry in entries:
        if entry.maker == maker and entry.name == name and media_type in (None, entry.media_type):
            found.setdefault(entry.key, entry)
    return list(found.values())
