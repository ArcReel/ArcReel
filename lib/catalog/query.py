"""目录查询：模型级信息的唯一读入口。

模型的显示名、媒体类型、能力、可选时长与分辨率、最大输出 token、价格，以及供应商在某媒体类型下的
默认模型，一律经这里读取。注册表（``lib.config.registry``）只登记供应商一级的代码事实（凭证形态、调用通道、
默认地址、并发默认），不含模型。

模型清单、条目事实与默认模型取自合成后的目录（``lib.catalog.composition``）：官方数据是随代码打包的出厂快照
（``lib/catalog/snapshot/``，加载时过 ``arcreel_catalog_core`` 的实例侧校验）与官方同步取回的数据
（``lib.catalog.official_sync``）中较新的一份，用户修改的条目副本整条替换官方条目。
价格是条目的价目表，见 :func:`provider_model_price`。
供应商模型一级的查询返回目录自己的值对象（:class:`CatalogModel`、:class:`CatalogSearchHit`）；按名字对应条目的
:func:`find_entry` 返回 ``arcreel_catalog_core`` 的 ``CatalogEntry``，调用方要的正是条目本身的键、事实与价目表。
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, assert_never, cast

from packaging.version import InvalidVersion, Version

from arcreel_catalog_core import (
    CATALOG_FILENAME,
    MANIFEST_FILENAME,
    MEDIA_TYPES,
    AudioFacts,
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
from lib.app_version import app_version
from lib.catalog.composition import ComposedCatalog, EntrySource, compose_catalog, merge_official
from lib.catalog.entry_matching import match_entry

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent / "snapshot"

#: 能力 token 的封闭词汇表，由条目事实推出：仅收录有消费方的 token，词汇与各媒体 backend 能力枚举
#: （TextCapability / ImageCapability / AudioCapability）同名同义。视频模型的输入模式、参考图上限与音轨形态不在
#: 这里，真相源是各 backend 的 VideoCapabilities 与请求期 gate。
ModelCapability = Literal[
    "text_generation",
    "structured_output",  # 消费点：文本 backend 结构化输出探测
    "vision",  # 消费点：文本解析的 vision 闸（lib/config/resolver.py）
    "text_to_image",  # 消费点：图片任务类型桶判定（lib/backends/generation_type_buckets.py）
    "image_to_image",  # 消费点：同上
    "text_to_speech",
]

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
    时用模型键）。价格不在这里，经 :func:`provider_model_price` 取。

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


# 官方同步取回的数据：由官方同步装入，见 install_synced_data。单元素列表当信箱。
_synced: list[CatalogData | None] = [None]

# 按（出厂快照, 同步数据）对象缓存的官方数据；同步数据换上新的一份时重建。
_official_cache: list[tuple[CatalogData, CatalogData, CatalogData]] = []


def install_synced_data(data: CatalogData | None) -> None:
    """换上官方同步取回的数据，之后的查询读它与出厂快照合成的官方数据（:func:`merge_official`）。

    应用启动时装入已存的数据，每次同步应用新数据集之后再装入；整份替换，读点要么看到旧数据、要么看到新数据。
    """
    _synced[0] = data


def official_data() -> CatalogData:
    """官方数据：出厂快照与官方同步中数据集版本较新的一份，保留另一份独有的条目；没有同步数据时即出厂快照。"""
    snapshot, synced = factory_snapshot(), _synced[0]
    if synced is None:
        return snapshot
    if not _official_cache or _official_cache[0][0] is not snapshot or _official_cache[0][1] is not synced:
        _official_cache[:] = [(snapshot, synced, merge_official(snapshot, synced))]
    return _official_cache[0][2]


def _to_catalog_model(entry: CatalogEntry, api_model_name: str, key: str, default: bool) -> CatalogModel:
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
        api_model_name=None if key == api_model_name else api_model_name,
        max_output_tokens=max_output_tokens,
    )


def _unmatched_catalog_model(user: UserAddedModel) -> CatalogModel:
    """对应不上条目的用户添加模型：只知道媒体类型，模型事实按运行时回落（docs/adr/0104）。

    文本与音频只有基本能力；图片的文生图与图生图不加约束；视频的可选时长取装入时给出的回落时长。
    """
    model_id, media_type = user.model_id, user.media_type
    match media_type:
        case "text":
            return CatalogModel(display_name=model_id, media_type=media_type, capabilities=["text_generation"])
        case "image":
            return CatalogModel(
                display_name=model_id, media_type=media_type, capabilities=["text_to_image", "image_to_image"]
            )
        case "video":
            return CatalogModel(
                display_name=model_id,
                media_type=media_type,
                capabilities=[],
                supported_durations=list(user.fallback_durations),
            )
        case "audio":
            return CatalogModel(display_name=model_id, media_type=media_type, capabilities=["text_to_speech"])
        case _:
            assert_never(media_type)


@dataclass(frozen=True)
class UserAddedModel:
    """内置供应商下用户添加的供应商模型，数据来自内置供应商模型表。

    ``model_id`` 是 API 模型名，也是设置里引用它的模型键；``entry_binding`` 是「模型厂商/官方模型名」。
    ``fallback_durations`` 是对应不上条目的视频模型在运行时回落的可选时长（按名字推断的时长预设），由装入方给出。
    ``capability_overrides`` 是用户的能力覆盖（docs/adr/0104），装入方已按媒体类型滤掉不合法的键值：模型事实维度
    在这里整维替换目录信息，通道事实维度经 :func:`user_added_capability_overrides` 交给调用通道一侧合并。
    """

    provider_id: str
    model_id: str
    media_type: MediaType
    multiplier: float = 1.0
    entry_binding: str | None = None
    fallback_durations: tuple[int, ...] = ()
    capability_overrides: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _IndexedModel:
    """索引里的一个供应商模型：对外的目录信息，加上它对应的条目（对应不上时为 ``None``）与供应商模型倍率。

    ``base_model`` 是未叠加能力覆盖的目录信息（条目的值或运行时回落）；只有用户添加的模型带覆盖，其余与 ``model`` 相同。
    """

    model: CatalogModel
    entry: CatalogEntry | None
    multiplier: float
    api_model_name: str
    official: bool
    base_model: CatalogModel | None = None
    capability_overrides: Mapping[str, object] = field(default_factory=dict)


def meets_min_app_version(required: str | None, current: str) -> bool:
    """应用版本 ``current`` 是否达到供应商模型标注的最低应用版本；没有标注时总是满足。

    任一侧判不出高低时视为不满足：宁可不展示，也不让旧实例调用它的调用通道不会编码的模型。
    """
    if required is None:
        return True
    try:
        return Version(current) >= Version(required)
    except InvalidVersion:
        return False


def _official_models(data: CatalogData, provider_id: str, current_version: str) -> dict[str, _IndexedModel]:
    listing = data.builtin_providers[provider_id]
    models: dict[str, _IndexedModel] = {}
    for model in listing.models:
        entry = data.entry_of(model)
        if entry is None:
            logger.warning(
                "内置供应商 %s 的模型 %s 引用不到条目 %s，已跳过", provider_id, model.api_model_name, model.entry
            )
            continue
        # 低于最低应用版本的实例不展示、也不启用它；它被指定为默认时，按没有默认处理（docs/adr/0103）。
        if not meets_min_app_version(model.min_app_version, current_version):
            continue
        key = _LEGACY_MODEL_KEYS.get((provider_id, model.api_model_name, entry.media_type), model.api_model_name)
        if key in models:
            logger.warning("内置供应商 %s 的模型键 %s 重复，已跳过后一个", provider_id, key)
            continue
        default = listing.defaults.get(entry.media_type) == model.api_model_name
        models[key] = _IndexedModel(
            _to_catalog_model(entry, model.api_model_name, key, default),
            entry,
            model.multiplier,
            model.api_model_name,
            official=True,
        )
    return models


def _toggle(capabilities: list[ModelCapability], capability: ModelCapability, value: object) -> list[ModelCapability]:
    if not isinstance(value, bool):
        return capabilities
    kept: list[ModelCapability] = [c for c in capabilities if c != capability]
    if value:
        kept.append(capability)
    return kept


def _with_model_fact_overrides(model: CatalogModel, overrides: Mapping[str, object]) -> CatalogModel:
    """把能力覆盖里的模型事实维度整维替换进目录信息（docs/adr/0104）。值已由装入方校验过形状。"""
    capabilities = list(model.capabilities)
    capabilities = _toggle(capabilities, "vision", overrides.get("image_input"))
    capabilities = _toggle(capabilities, "structured_output", overrides.get("structured_output"))
    capabilities = _toggle(capabilities, "text_to_image", overrides.get("text_to_image"))
    capabilities = _toggle(capabilities, "image_to_image", overrides.get("image_to_image"))
    max_output_tokens = overrides.get("max_output_tokens")
    durations = overrides.get("durations")
    resolutions = overrides.get("resolutions")
    constraints = overrides.get("duration_constraints")
    by_resolution = model.duration_resolution_constraints
    reference_durations = model.reference_image_durations
    if isinstance(constraints, Mapping):
        raw_by_resolution = constraints.get("by_resolution")
        raw_reference = constraints.get("with_reference_images")
        by_resolution = (
            {str(res).strip().lower(): [int(d) for d in allowed] for res, allowed in raw_by_resolution.items()}
            if isinstance(raw_by_resolution, Mapping)
            else {}
        )
        reference_durations = [int(d) for d in raw_reference] if isinstance(raw_reference, list) else []
    return replace(
        model,
        capabilities=capabilities,
        max_output_tokens=max_output_tokens if isinstance(max_output_tokens, int) else model.max_output_tokens,
        supported_durations=(
            sorted({int(d) for d in durations}) if isinstance(durations, list) else model.supported_durations
        ),
        resolutions=[str(r).strip() for r in resolutions] if isinstance(resolutions, list) else model.resolutions,
        duration_resolution_constraints=by_resolution,
        reference_image_durations=reference_durations,
    )


def _user_added_model(data: CatalogData, user: UserAddedModel) -> _IndexedModel:
    binding = binding_key(user.entry_binding, user.media_type)
    entry = match_entry(data.entries, user.model_id, media_type=user.media_type, binding=binding)
    base = (
        _to_catalog_model(entry, user.model_id, user.model_id, default=False)
        if entry is not None
        else _unmatched_catalog_model(user)
    )
    model = _with_model_fact_overrides(base, user.capability_overrides) if user.capability_overrides else base
    return _IndexedModel(
        model,
        entry,
        user.multiplier,
        user.model_id,
        official=False,
        base_model=base,
        capability_overrides=user.capability_overrides,
    )


def _build_index(
    data: CatalogData, user_models: tuple[UserAddedModel, ...], current_version: str
) -> dict[str, dict[str, _IndexedModel]]:
    index = {
        provider_id: _official_models(data, provider_id, current_version) for provider_id in data.builtin_providers
    }
    for user in user_models:
        models = index.setdefault(user.provider_id, {})
        # 官方数据收录了同一模型键时，换成官方版本，只保留启用状态（docs/adr/0099）。
        if user.model_id not in models:
            models[user.model_id] = _user_added_model(data, user)
    return index


# 内置供应商下用户添加的模型：由内置供应商模型表装入，见 install_user_added_models。单元素列表当信箱。
_user_added: list[tuple[UserAddedModel, ...]] = [()]

# 按快照对象缓存的索引：快照每进程只加载一次，索引随之只建一次；用户添加的模型变化时清空重建。
_index_cache: list[tuple[CatalogData, dict[str, dict[str, _IndexedModel]]]] = []

# 用户修改的条目副本：由用户条目表装入，见 install_user_entries。单元素列表当信箱。
_user_entries: list[tuple[tuple[CatalogEntry, ...], frozenset[EntryKey]]] = [((), frozenset())]

# 按官方数据对象缓存的合成结果；用户副本变化时清空重建。
_composed_cache: list[tuple[CatalogData, ComposedCatalog]] = []


def install_user_added_models(models: Iterable[UserAddedModel]) -> None:
    """换上内置供应商下用户添加的模型，之后的查询把它们并进各自供应商的模型清单。

    应用启动时与每次写入内置供应商模型表之后由写入方调用。读目录的代码（生成 worker、Agent 会话的工具、
    接口）都跑在应用进程里，进程内替换即对全部读点生效。
    """
    _user_added[0] = tuple(models)
    _index_cache.clear()


def install_user_entries(entries: Iterable[CatalogEntry], created: Iterable[EntryKey] = ()) -> None:
    """换上用户修改的条目副本，之后的查询（门面、能力、计价、按名字对应条目、搜索）都读合成后的条目。

    应用启动时与每次写入用户条目表之后由写入方调用；与 :func:`install_user_added_models` 同理，全部读点都在
    应用进程里，进程内替换即对全部读点生效。``created`` 是其中用户新建的条目身份。
    """
    _user_entries[0] = (tuple(entries), frozenset(created))
    _composed_cache.clear()
    _index_cache.clear()


def composed_catalog() -> ComposedCatalog:
    """合成后的目录：官方数据加上用户修改的条目副本。"""
    official = official_data()
    if not _composed_cache or _composed_cache[0][0] is not official:
        _composed_cache[:] = [(official, compose_catalog(official, *_user_entries[0]))]
    return _composed_cache[0][1]


def catalog_data() -> CatalogData:
    """运行时读的目录数据（合成后）。"""
    return composed_catalog().data


def entry_source(key: EntryKey) -> EntrySource:
    """条目由谁维护：跟随官方、用户改过，或官方没有、由用户填写。"""
    return composed_catalog().source_of(key)


def official_entry(key: EntryKey) -> CatalogEntry | None:
    """官方数据里这个身份的条目，用于显示官方值与恢复官方数据；用户新建的条目没有。"""
    return composed_catalog().official_entry(key)


def get_entry(key: EntryKey) -> CatalogEntry | None:
    """按身份取合成后的条目；目录里没有时为 ``None``。"""
    return next((entry for entry in catalog_data().entries if entry.key == key), None)


def _indexed_models(provider_id: str) -> dict[str, _IndexedModel]:
    data = catalog_data()
    if not _index_cache or _index_cache[0][0] is not data:
        _index_cache[:] = [(data, _build_index(data, _user_added[0], app_version()))]
    return _index_cache[0][1].get(provider_id, {})


def _provider_models(provider_id: str) -> dict[str, CatalogModel]:
    return {key: indexed.model for key, indexed in _indexed_models(provider_id).items()}


def reset_for_tests() -> None:
    """清空出厂快照、同步数据、索引、用户添加模型与用户条目副本的进程级状态，下次查询重新读取快照。"""
    factory_snapshot.cache_clear()
    _synced[0] = None
    _official_cache.clear()
    _user_added[0] = ()
    _user_entries[0] = ((), frozenset())
    _composed_cache.clear()
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


@dataclass(frozen=True)
class ProviderModelView:
    """供应商模型列表里的一个模型：目录信息、对应的条目（对应不上时为 ``None``），以及它是官方收录还是用户添加。"""

    model_id: str
    api_model_name: str
    model: CatalogModel
    entry: CatalogEntry | None
    official: bool
    #: 供应商模型倍率：官方收录的随官方数据下发，用户添加的由用户填写。
    multiplier: float
    #: 未叠加能力覆盖的目录信息：条目的值，对应不上条目时为运行时回落的值。
    base_model: CatalogModel | None = None
    #: 用户添加的模型的能力覆盖（已滤掉不合法的键值）；官方收录的模型没有。
    capability_overrides: Mapping[str, object] = field(default_factory=dict)


def provider_models(provider_id: str) -> list[ProviderModelView]:
    """列出内置供应商的全部供应商模型：官方收录的按清单顺序在前，用户添加的随后。

    低于最低应用版本的官方收录模型不在其中。启用状态不在目录里，由内置供应商模型表决定。
    """
    return [
        ProviderModelView(
            key,
            indexed.api_model_name,
            indexed.model,
            indexed.entry,
            indexed.official,
            indexed.multiplier,
            base_model=indexed.base_model or indexed.model,
            capability_overrides=indexed.capability_overrides,
        )
        for key, indexed in _indexed_models(provider_id).items()
    ]


def user_added_capability_overrides(provider_id: str, model_id: str) -> Mapping[str, object]:
    """内置供应商下用户添加的模型的能力覆盖；官方收录的模型、不在目录中的模型为空。

    模型事实维度已并进 :func:`get_model` 的目录信息；通道事实维度（输入模式、参考上限、音轨形态等）由调用通道
    一侧的能力声明合并（``lib.backends.backend_assembly.specs.builtin_video_capabilities_for_model``）。
    """
    indexed = _indexed_models(provider_id).get(model_id)
    return indexed.capability_overrides if indexed is not None else {}


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
    return match_entry(catalog_data().entries, model_name, media_type=media_type, binding=binding)


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
    return get_entry(key)


def entry_ref(entry: CatalogEntry) -> str:
    """条目绑定的存储形状：「模型厂商/官方模型名」。"""
    return entry.key.ref


@dataclass(frozen=True)
class ModelPrice:
    """内置供应商模型的计价依据：所对应条目的价目表（没有时为 ``None``）与这个供应商模型的倍率。

    ``entry_found`` 为假时，这是一个对应不上条目的用户添加模型。
    """

    price: PriceTable | None
    multiplier: float
    entry_found: bool = True


def provider_model_price(provider_id: str, model_id: str, media_type: str) -> ModelPrice | None:
    """内置供应商模型的价目表与供应商模型倍率。

    供应商或模型键不在目录中、或模型不是调用的 ``media_type``（与 :func:`get_model` 同一口径）时返回 ``None``，
    按对应不上条目计。
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    if indexed is None:
        return None
    entry = indexed.entry
    return ModelPrice(
        price=entry.price if entry is not None else None, multiplier=indexed.multiplier, entry_found=entry is not None
    )


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
    for index, entry in enumerate(catalog_data().entries):
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
