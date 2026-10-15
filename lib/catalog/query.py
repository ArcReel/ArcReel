"""目录查询：模型级信息的唯一读入口。

模型的显示名、媒体类型、能力、可选时长与分辨率、最大输出 token、价格，以及供应商在某媒体类型下的
默认模型，生产代码一律经这里读取，不直接遍历或索引注册表的 ``models``。注册表只向调用方提供
供应商一级的代码事实（凭证形态、调用通道、默认地址、并发默认）。

模型清单、条目事实与默认模型取自随代码打包的出厂快照（``lib/catalog/snapshot/``），加载时过
``arcreel_catalog_core`` 的实例侧校验。文本与音频按条目的价目表计价，见 :func:`provider_model_price`；图片与视频的价格
仍取自注册表的 ``ModelInfo.pricing``，见 :func:`model_pricing`。
供应商模型一级的查询返回目录自己的值对象（:class:`CatalogModel`、:class:`CatalogSearchHit`）；按名字对应条目的
:func:`find_entry` 返回 ``arcreel_catalog_core`` 的 ``CatalogEntry``，调用方要的正是条目本身的键、事实与价目表。
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, assert_never, cast

from arcreel_catalog_core import (
    CATALOG_FILENAME,
    MANIFEST_FILENAME,
    MEDIA_TYPES,
    AudioFacts,
    BuiltinProviderModel,
    CatalogData,
    CatalogEntry,
    EntryKey,
    ImageFacts,
    MediaType,
    PriceTable,
    TextFacts,
    VideoFacts,
    load_catalog,
    normalize_model_name,
)
from lib.catalog.entry_matching import match_entry
from lib.config import registry

if TYPE_CHECKING:
    from lib.billing.pricing.types import Pricing
    from lib.config.registry import ModelCapability

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent / "snapshot"

#: 两栖模型沿用的旧模型键。目录数据里供应商模型的身份是（API 模型名, 条目媒体类型），同一 API 模型名的
#: 两个供应商模型在这里各取一个键；不在表中的供应商模型以 API 模型名为键。设置与项目里已存的键靠它保持可解析。
_LEGACY_MODEL_KEYS: Mapping[tuple[str, str, str], str] = {
    ("kling", "kling-v3-omni", "image"): "kling-v3-omni-image",
}


@dataclass(frozen=True)
class CatalogModel:
    """供应商名下一个模型键在目录中的信息：模型条目的事实，加上这个供应商模型自己的事实。

    条目事实：``display_name``、``media_type``、``capabilities``、可选时长与时长约束、
    ``resolutions``、``max_output_tokens``、``hidden``（条目已下架，不出现在候选里）。供应商模型事实：
    ``default``（这个供应商在该媒体类型下的默认模型）、``api_model_name``（发给供应商的模型名，``None``
    时用模型键）。价格不在这里，经 :func:`model_pricing` 取。

    字段名与顺序即设置接口里模型信息的序列化形状（``dataclasses.asdict``）。
    """

    display_name: str
    media_type: str
    capabilities: list[ModelCapability]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
    reference_image_durations: list[int] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)
    hidden: bool = False
    api_model_name: str | None = None
    max_output_tokens: int | None = None


@functools.cache
def factory_snapshot() -> CatalogData:
    """随代码打包的出厂快照，进程内只读一次。快照无法通过校验时抛 ``CatalogFormatError``。"""
    return load_catalog(
        (SNAPSHOT_DIR / MANIFEST_FILENAME).read_bytes(),
        (SNAPSHOT_DIR / CATALOG_FILENAME).read_bytes(),
    )


def _to_catalog_model(entry: CatalogEntry, model: BuiltinProviderModel, key: str, default: bool) -> CatalogModel:
    capabilities: list[ModelCapability] = []
    durations: list[int] = []
    by_resolution: dict[str, list[int]] = {}
    reference_durations: list[int] = []
    resolutions: list[str] = []
    max_output_tokens: int | None = None
    facts = entry.facts
    match facts:
        case TextFacts():
            capabilities.append("text_generation")
            if facts.structured_output:
                capabilities.append("structured_output")
            if facts.image_input:
                capabilities.append("vision")
            max_output_tokens = facts.max_output_tokens
        case ImageFacts():
            if facts.text_to_image:
                capabilities.append("text_to_image")
            if facts.image_to_image:
                capabilities.append("image_to_image")
            resolutions = list(facts.resolutions)
        case VideoFacts():
            durations = list(facts.durations)
            resolutions = list(facts.resolutions)
            constraints = facts.duration_constraints
            by_resolution = {res: list(allowed) for res, allowed in constraints.by_resolution.items()}
            reference_durations = list(constraints.with_reference_images)
        case AudioFacts():
            capabilities.append("text_to_speech")
        case _:
            assert_never(facts)
    return CatalogModel(
        display_name=entry.display_name,
        media_type=entry.media_type,
        capabilities=capabilities,
        default=default,
        supported_durations=durations,
        duration_resolution_constraints=by_resolution,
        reference_image_durations=reference_durations,
        resolutions=resolutions,
        hidden=entry.discontinued,
        api_model_name=None if key == model.api_model_name else model.api_model_name,
        max_output_tokens=max_output_tokens,
    )


@dataclass(frozen=True)
class _IndexedModel:
    """索引里的一个供应商模型：对外的目录信息，加上它引用的条目与清单里的供应商模型。"""

    model: CatalogModel
    entry: CatalogEntry
    listing_model: BuiltinProviderModel


def _build_index(data: CatalogData) -> dict[str, dict[str, _IndexedModel]]:
    index: dict[str, dict[str, _IndexedModel]] = {}
    for provider_id, listing in data.builtin_providers.items():
        models: dict[str, _IndexedModel] = {}
        for model in listing.models:
            entry = data.entry_of(model)
            if entry is None:
                logger.warning(
                    "内置供应商 %s 的模型 %s 引用不到条目 %s，已跳过", provider_id, model.api_model_name, model.entry
                )
                continue
            key = _LEGACY_MODEL_KEYS.get((provider_id, model.api_model_name, entry.media_type), model.api_model_name)
            if key in models:
                logger.warning("内置供应商 %s 的模型键 %s 重复，已跳过后一个", provider_id, key)
                continue
            default = listing.defaults.get(entry.media_type) == model.api_model_name
            models[key] = _IndexedModel(_to_catalog_model(entry, model, key, default), entry, model)
        index[provider_id] = models
    return index


# 按快照对象缓存的索引：快照每进程只加载一次，索引随之只建一次。
_index_cache: list[tuple[CatalogData, dict[str, dict[str, _IndexedModel]]]] = []


def _indexed_models(provider_id: str) -> dict[str, _IndexedModel]:
    data = factory_snapshot()
    if not _index_cache or _index_cache[0][0] is not data:
        _index_cache[:] = [(data, _build_index(data))]
    return _index_cache[0][1].get(provider_id, {})


def _provider_models(provider_id: str) -> dict[str, CatalogModel]:
    return {key: indexed.model for key, indexed in _indexed_models(provider_id).items()}


def reset_for_tests() -> None:
    """清空出厂快照与索引的进程级缓存，下次查询重新读取快照；只清缓存，不改生产行为。"""
    factory_snapshot.cache_clear()
    _index_cache.clear()


def _indexed_model(provider_id: str, model_id: str, media_type: str | None) -> _IndexedModel | None:
    """按供应商与模型键取索引项；给了 ``media_type`` 时，模型不是这一媒体类型也视为不存在。"""
    indexed = _indexed_models(provider_id).get(model_id)
    if indexed is None or media_type not in (None, indexed.model.media_type):
        return None
    return indexed


def get_model(provider_id: str, model_id: str, *, media_type: str | None = None) -> CatalogModel | None:
    """按供应商与模型键取目录信息；供应商或模型不在目录中时返回 ``None``。

    给了 ``media_type`` 时，模型不是这一媒体类型也返回 ``None``：调用方要的是「这个供应商的这类模型」，
    模型键引用到别的媒体类型与不在清单里同样不可用。
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    return indexed.model if indexed is not None else None


def list_models(provider_id: str) -> dict[str, CatalogModel]:
    """列出供应商的全部模型（按清单顺序，含 ``hidden`` 的模型）；供应商不在目录中时为空。"""
    return _provider_models(provider_id)


def default_model(provider_id: str, media_type: str) -> str | None:
    """供应商在该媒体类型下的默认模型键；没有指定时返回 ``None``。"""
    for model_id, model in _provider_models(provider_id).items():
        if model.media_type == media_type and model.default:
            return model_id
    return None


def provider_media_types(provider_id: str) -> list[str]:
    """供应商的模型覆盖到的媒体类型，升序。"""
    return sorted({model.media_type for model in _provider_models(provider_id).values()})


def provider_capabilities(provider_id: str) -> list[str]:
    """供应商全部模型的能力并集，升序。"""
    return sorted({cap for model in _provider_models(provider_id).values() for cap in model.capabilities})


def find_entry(
    model_name: str, *, media_type: str | None = None, binding: EntryKey | None = None
) -> CatalogEntry | None:
    """模型名对应的模型条目（docs/adr/0105）：先看条目绑定，再按名字在当前目录里现查。

    供自定义供应商的模型、Agent 凭证的档位这类只有模型名的场景使用；内置供应商的模型直接引用条目，不走这里。
    ``media_type`` 取供应商模型调用端点的媒体类型，还没有调用端点时取按名字推断的媒体类型；``binding`` 是用户
    为这个模型指定的条目（尚无存储时传 ``None``）。对应不上、或两栖模型没给 ``media_type`` 时返回 ``None``。
    对应关系不缓存，目录变化（官方同步带来新条目或模型别名）后下次调用即得到新结果。规则见 :func:`match_entry`。
    """
    return match_entry(factory_snapshot().entries, model_name, media_type=media_type, binding=binding)


def binding_key(ref: str | None, media_type: str) -> EntryKey | None:
    """条目绑定存的「模型厂商/官方模型名」加上调用端点的媒体类型，还原为条目键；没有绑定或形状不对时为 ``None``。"""
    if not ref or media_type not in MEDIA_TYPES:
        return None
    maker, sep, name = ref.partition("/")
    if not sep or not maker or not name:
        return None
    return EntryKey(maker, name, cast("MediaType", media_type))


def find_bound_entry(ref: str, media_type: str) -> CatalogEntry | None:
    """条目绑定指向的条目；目录里没有这个条目（或媒体类型不符）时为 ``None``。供写入侧校验绑定使用。"""
    key = binding_key(ref, media_type)
    if key is None:
        return None
    return next((entry for entry in factory_snapshot().entries if entry.key == key), None)


def entry_ref(entry: CatalogEntry) -> str:
    """条目绑定的存储形状：「模型厂商/官方模型名」。"""
    return entry.key.ref


@dataclass(frozen=True)
class ModelPrice:
    """内置供应商模型的计价依据：所引用条目的价目表（没有时为 ``None``）与这个供应商模型的倍率。"""

    price: PriceTable | None
    multiplier: float


def provider_model_price(provider_id: str, model_id: str, media_type: str) -> ModelPrice | None:
    """内置供应商模型的价目表与供应商模型倍率。

    供应商或模型键不在目录中、或模型不是调用的 ``media_type``（与 :func:`get_model` 同一口径）时，视为对应不上
    条目，返回 ``None``。
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    if indexed is None:
        return None
    return ModelPrice(price=indexed.entry.price, multiplier=indexed.listing_model.multiplier)


def model_pricing(provider_id: str, model_id: str) -> Pricing | None:
    """模型的定价声明；模型不在注册表中或没有定价时返回 ``None``。

    价格读注册表的 ``ModelInfo.pricing``，只剩图片与视频的计价在用。
    """
    meta = registry.PROVIDER_REGISTRY.get(provider_id)
    info = meta.models.get(model_id) if meta is not None else None
    return info.pricing if info is not None else None


@dataclass(frozen=True)
class CatalogSearchHit:
    """目录搜索命中的一个模型条目。``maker`` 加 ``name`` 加 ``media_type`` 唯一确定条目（两栖模型有两个条目）。"""

    maker: str
    name: str
    media_type: str
    display_name: str
    discontinued: bool


def _search_keys(entry: CatalogEntry) -> list[str]:
    """条目可被搜索词命中的名称：官方模型名与模型别名按归一化后的写法，显示名只忽略大小写并视点号同连字符。"""
    names = [entry.name, *entry.aliases]
    return [normalize_model_name(name) for name in names] + [entry.display_name.lower().replace(".", "-")]


def search_entries(query: str = "", media_type: str | None = None) -> list[CatalogSearchHit]:
    """按名称与媒体类型搜索模型条目。

    搜索词按空白切成若干词，每个词都要命中条目的官方模型名、模型别名或显示名之一（忽略大小写，
    点号视同连字符，写成「厂商/模型名」时忽略厂商）。空搜索词列出全部。整句与名称相同的排最前，
    其次是以整句开头的，其余按目录顺序；已下架的条目一律排在后面。
    """
    tokens = [normalize_model_name(token) for token in query.split()]
    whole = normalize_model_name(query.strip())
    ranked: list[tuple[bool, int, int, CatalogSearchHit]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(factory_snapshot().entries):
        identity = (entry.maker, entry.name, entry.media_type)
        if identity in seen or (media_type is not None and entry.media_type != media_type):
            continue
        keys = _search_keys(entry)
        if not all(any(token in key for key in keys) for token in tokens):
            continue
        seen.add(identity)
        rank = 0 if whole in keys else 1 if any(key.startswith(whole) for key in keys) else 2
        hit = CatalogSearchHit(entry.maker, entry.name, entry.media_type, entry.display_name, entry.discontinued)
        ranked.append((entry.discontinued, rank, index, hit))
    ranked.sort(key=lambda item: item[:3])
    return [hit for *_, hit in ranked]
