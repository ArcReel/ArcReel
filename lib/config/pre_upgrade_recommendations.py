"""升级前的版本此刻会怎么选模型：升级固化、运行时回退与项目迁移据此还原升级前生成用的模型（docs/adr/0099）。

0.36 及更早的版本运行时读目录里的默认模型（现在叫推荐模型）：没有默认模型设置时按注册顺序挑第一个有凭证、
有可用默认的内置供应商，都没有再落到自定义供应商的默认模型；项目里只写了内置供应商的模型字段也按目录默认补全。
新版本不再这样读，升级时要把旧版本会用的模型写下来。新进程读到的目录与应用版本都已不是升级前的那一份，
所以按升级前的上下文重算，并在升级后首次启动时一次算定、存下（:func:`capture`）：

- 应用版本：实例不记录自己跑过的版本，按升级前库里的 alembic revision 推断（:data:`_RELEASES_BY_REVISION`）。
  revision 在 Alembic 改写它之前由迁移环境记下（:mod:`lib.db.upgrade_source`），从应用启动还是命令行迁移都一样，
  之后的步骤失败也能在下次启动读回；没有记录时不猜。同一个 revision 对应多个发布版时，每个发布版各算一次，结果一致的才算数。
- 目录数据：旧版本读出厂快照与官方同步数据中数据集版本较新的那份，再叠上用户修改过的条目副本。同步数据与
  用户副本存在库里、升级后仍在；旧出厂快照随旧镜像一起没有了，这里冻结了各发布版出厂快照里的默认模型与它们
  引用的条目。这些默认模型都没有最低应用版本与关停日期，冻结时不必再带这两项。
- 用户停用与凭证：升级前后是同一张表，在首次启动、实例开始接受请求之前读。被停用的默认在旧版本里按没有默认
  处理；有生效凭证的供应商集合随结果一起存下，之后的重试不重新读。

各候选结果不一致、revision 没有记录或对不上已知发布版（0.34 之前的版本、两个发布版之间的预览镜像、没有
revision 记录的旧库），库里存着同步数据、这次启动却没能读入，或默认模型引用的条目有读不出的用户副本时，
这一项记为还原不了：升级固化不替它挑模型，运行时也不落到自定义供应商默认模型，项目迁移不补全，由用户选择。

结果存进全局设置 ``pre_upgrade_recommendations``，之后只读不重算：升级后的官方同步会换掉同步数据，用户也会改
凭证，重算就不再是升级前的那一份。全新安装的实例没有升级前的状态，存的是此刻的推荐。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import (
    MEDIA_TYPES,
    CatalogData,
    CatalogEntry,
    CatalogFormatError,
    parse_dataset_version,
    parse_entry,
)
from lib.app_version import app_version
from lib.catalog import query as catalog
from lib.catalog.composition import compose_catalog
from lib.catalog.lifecycle import entry_lifecycle
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import SystemSettingRepository
from lib.config.service import ConfigService
from lib.db import upgrade_source
from lib.db.models.catalog_official_sync import CatalogOfficialSync
from lib.db.repositories.builtin_provider_model_repo import BuiltinProviderModelRepository
from lib.db.repositories.catalog_user_entry_repo import CatalogUserEntryRepository

logger = logging.getLogger(__name__)

#: 全局设置键：升级前实例会补全的推荐模型（:class:`RecommendationTable` 的 JSON）。
SETTING_KEY = "pre_upgrade_recommendations"

Key = tuple[str, str]

#: 条目身份：模型厂商、官方模型名、媒体类型。
Ident = tuple[str, str, str]


@dataclass(frozen=True)
class _Release:
    """一个运行时读目录默认模型的发布版：应用版本、出厂快照的数据集版本与快照里的默认模型。"""

    app_version: str
    snapshot_dataset_version: str
    snapshot_defaults: Mapping[str, Mapping[str, tuple[str, str]]]


# 0.34.0 与 0.35.0 出厂快照里的默认模型（两版相同）与它们引用的条目，取自各自发版 tag 的
# lib/catalog/snapshot/catalog.json。条目用来对上用户修改过的条目副本：副本带关停日期时，旧版本按已下线跳过。
_DEFAULTS_0_34: Mapping[str, Mapping[str, tuple[str, str]]] = {
    "agnes": {
        "image": ("agnes-image-2.5-flash", "agnes/agnes-image-2.5-flash"),
        "text": ("agnes-3.0-flash", "agnes/agnes-3.0-flash"),
        "video": ("agnes-video-2.5-flash", "agnes/agnes-video-2.5-flash"),
    },
    "ark": {
        "image": ("doubao-seedream-5-0-lite-260128", "bytedance/doubao-seedream-5-0-lite"),
        "text": ("doubao-seed-2-1-lite-260915", "bytedance/doubao-seed-2.1-lite"),
        "video": ("doubao-seedance-2-0-mini-260615", "bytedance/doubao-seedance-2.0-mini"),
    },
    "ark-agent-plan": {
        "image": ("doubao-seedream-5.0-lite", "bytedance/doubao-seedream-5-0-lite"),
        "text": ("doubao-seed-2.0-lite", "bytedance/doubao-seed-2.0-lite"),
        "video": ("doubao-seedance-2.0-mini", "bytedance/doubao-seedance-2.0-mini"),
    },
    "dashscope": {
        "audio": ("qwen3-tts-flash", "alibaba/qwen3-tts-flash"),
        "image": ("qwen-image-2.0", "alibaba/qwen-image-2.0"),
        "text": ("qwen-plus", "alibaba/qwen-plus"),
        "video": ("happyhorse-1.1-i2v", "alibaba/happyhorse-1.1-i2v"),
    },
    "gemini-aistudio": {
        "audio": ("gemini-3.8-flash-tts", "google/gemini-3.8-flash-tts"),
        "image": ("gemini-3.1-flash-image", "google/gemini-3.1-flash-image"),
        "text": ("gemini-3-flash-preview", "google/gemini-3-flash-preview"),
        "video": ("veo-3.1-lite-generate-preview", "google/veo-3.1-lite-generate-preview"),
    },
    "gemini-vertex": {
        "audio": ("gemini-3.8-flash-tts", "google/gemini-3.8-flash-tts"),
        "image": ("gemini-3.1-flash-image", "google/gemini-3.1-flash-image"),
        "text": ("gemini-3-flash-preview", "google/gemini-3-flash-preview"),
        "video": ("veo-3.1-fast-generate-001", "google/veo-3.1-fast-generate-001"),
    },
    "grok": {
        "image": ("grok-imagine-image", "xai/grok-imagine-image"),
        "text": ("grok-4.3", "xai/grok-4.3"),
        "video": ("grok-imagine-video", "xai/grok-imagine-video"),
    },
    "kling": {
        "image": ("kling-image-o1", "kuaishou/kling-image-o1"),
        "video": ("kling-v2-5-turbo", "kuaishou/kling-v2-5-turbo"),
    },
    "minimax": {
        "audio": ("speech-2.8-hd", "minimax/speech-2.8-hd"),
        "image": ("image-01", "minimax/image-01"),
        "text": ("MiniMax-M3", "minimax/MiniMax-M3"),
        "video": ("MiniMax-H3", "minimax/MiniMax-H3"),
    },
    "openai": {
        "image": ("gpt-image-2", "openai/gpt-image-2"),
        "text": ("gpt-5.4-mini", "openai/gpt-5.4-mini"),
    },
    "vidu": {
        "image": ("viduq2", "shengshu/viduq2"),
        "video": ("viduq3-turbo", "shengshu/viduq3-turbo"),
    },
}

# 0.36.0 出厂快照里的默认模型：ark 图片换成 pro，ark-agent-plan 的图片与文本、gemini-aistudio 的视频不再有默认。
_DEFAULTS_0_36: Mapping[str, Mapping[str, tuple[str, str]]] = {
    **_DEFAULTS_0_34,
    "ark": {
        **_DEFAULTS_0_34["ark"],
        "image": ("doubao-seedream-5-0-pro-260628", "bytedance/doubao-seedream-5-0-pro"),
    },
    "ark-agent-plan": {"video": _DEFAULTS_0_34["ark-agent-plan"]["video"]},
    "gemini-aistudio": {key: value for key, value in _DEFAULTS_0_34["gemini-aistudio"].items() if key != "video"},
}

#: 升级前库里的 alembic revision → 可能的发布版。0.34.0 与 0.35.0 的库结构相同，同一个 revision 对应两个发布版。
#: 0.36.0 之后到首个不读默认模型的版本之间没有新的 revision，从 main 构建的预览镜像报告的也是 0.36.0。
_RELEASES_BY_REVISION: Mapping[str, tuple[_Release, ...]] = {
    "b5d1e8f3a627": (
        _Release("0.34.0", "2026-10-24T06:01:38Z", _DEFAULTS_0_34),
        _Release("0.35.0", "2026-11-01T07:41:05Z", _DEFAULTS_0_34),
    ),
    "f6d2a8c4b931": (_Release("0.36.0", "2026-11-04T23:26:27Z", _DEFAULTS_0_36),),
}


#: 旧版本自动挑选的三种结果：选中内置模型、落到自定义供应商默认模型这一层（有就用、没有就报错）、还原不了。
AutoPickKind = Literal["builtin", "fallback", "unknown"]


@dataclass(frozen=True)
class AutoPick:
    """一个媒体类型在没有全局默认模型设置时，升级前的版本会怎么选。

    - ``builtin``：选中 ``provider_id`` 的 ``model``，升级固化把它写进全局默认模型。
    - ``fallback``：没有内置供应商可选，落到自定义供应商默认模型这一层；这一层新版本照旧保留。
    - ``unknown``：还原不了。不替用户挑模型，自定义供应商默认模型这一层也不用，生成报错并提示选择默认模型。
    """

    kind: AutoPickKind
    provider_id: str | None = None
    model: str | None = None


_UNKNOWN = AutoPick("unknown")
_FALLBACK = AutoPick("fallback")


@dataclass(frozen=True)
class RecommendationTable:
    """升级前的版本此刻的状态，升级后首次启动时一次算定。

    - ``models`` / ``unresolved``：每个内置供应商在各媒体类型下会补全的模型，用于补全只写了内置供应商的项目
      字段。``models`` 里没有、也不在 ``unresolved`` 里的，表示没有可用的默认（没有指定、被版本挡住、已下线或
      被停用），升级前会报错；``unresolved`` 是还原不了的项。
    - ``auto``：各媒体类型没有全局默认模型设置时的自动挑选结果（:class:`AutoPick`），升级固化与运行时回退只按它。
    - ``ready``：算定这一刻有生效凭证的内置供应商，升级固化按它记已知推荐，重试时不重新读凭证。
    """

    models: Mapping[Key, str] = field(default_factory=dict)
    unresolved: frozenset[Key] = frozenset()
    auto: Mapping[str, AutoPick] = field(default_factory=dict)
    ready: tuple[str, ...] = ()

    def model(self, provider_id: str, media_type: str) -> str | None:
        """可以补全的模型；升级前会报错或还原不了时为 ``None``。"""
        return self.models.get((provider_id, media_type))

    def is_unresolved(self, provider_id: str, media_type: str) -> bool:
        return (provider_id, media_type) in self.unresolved

    def auto_pick(self, media_type: str) -> AutoPick:
        """没有记录的媒体类型按还原不了处理。"""
        return self.auto.get(media_type, _UNKNOWN)

    def dump(self) -> str:
        models: dict[str, dict[str, str]] = {}
        for (provider_id, media_type), model in sorted(self.models.items()):
            models.setdefault(provider_id, {})[media_type] = model
        unresolved: dict[str, list[str]] = {}
        for provider_id, media_type in sorted(self.unresolved):
            unresolved.setdefault(provider_id, []).append(media_type)
        auto = {
            media_type: {"kind": pick.kind, "provider": pick.provider_id, "model": pick.model}
            for media_type, pick in sorted(self.auto.items())
        }
        payload = {"models": models, "unresolved": unresolved, "auto": auto, "ready": list(self.ready)}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def parse(cls, raw: str) -> RecommendationTable:
        """读回 :meth:`dump` 的结果。格式不对时抛 ``ValueError``。"""
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("pre-upgrade recommendations must be an object")
        models = {
            (str(provider_id), str(media_type)): str(model)
            for provider_id, by_media in dict(data.get("models", {})).items()
            for media_type, model in dict(by_media).items()
        }
        unresolved = frozenset(
            (str(provider_id), str(media_type))
            for provider_id, media_types in dict(data.get("unresolved", {})).items()
            for media_type in list(media_types)
        )
        auto: dict[str, AutoPick] = {}
        for media_type, pick in dict(data.get("auto", {})).items():
            kind = dict(pick).get("kind")
            if kind == "builtin":
                auto[str(media_type)] = AutoPick("builtin", str(pick["provider"]), str(pick["model"]))
            elif kind == "fallback":
                auto[str(media_type)] = _FALLBACK
            else:
                auto[str(media_type)] = _UNKNOWN
        return cls(models, unresolved, auto, tuple(str(provider_id) for provider_id in data.get("ready", [])))


class _Unresolved:
    """某个默认模型还原不了（:func:`_release_default` 的返回值之一）。"""


_UNRESOLVED = _Unresolved()

#: ``(provider_id, media_type)`` → 升级前会用的默认模型、``None``（没有可用的默认）或 :data:`_UNRESOLVED`。
DefaultOf = Callable[[str, str], "str | _Unresolved | None"]


def _all_keys() -> list[Key]:
    return [(provider_id, media_type) for provider_id in PROVIDER_REGISTRY for media_type in MEDIA_TYPES]


async def _disabled(session: AsyncSession) -> set[Key]:
    return {
        (row.provider_id, row.model_id)
        for row in await BuiltinProviderModelRepository(session).list_all()
        if not row.enabled
    }


async def _ready(session: AsyncSession) -> tuple[str, ...]:
    """有生效凭证的内置供应商，按注册顺序（旧版本自动挑选的优先级）。"""
    statuses = await ConfigService(session).get_all_providers_status()
    ready = {status.name for status in statuses if status.status == "ready"}
    return tuple(provider_id for provider_id in PROVIDER_REGISTRY if provider_id in ready)


@dataclass(frozen=True)
class _UserCopies:
    """用户修改过的条目副本，直接读自用户条目表；``unreadable`` 是读不出的副本的身份。"""

    entries: Mapping[Ident, CatalogEntry]
    unreadable: frozenset[Ident]


async def _user_copies(session: AsyncSession) -> _UserCopies:
    entries: dict[Ident, CatalogEntry] = {}
    unreadable: set[Ident] = set()
    for row in await CatalogUserEntryRepository(session).list_all():
        key = (row.maker, row.name, row.media_type)
        try:
            entry = parse_entry(row.entry)
        except CatalogFormatError:
            unreadable.add(key)
            continue
        if (entry.maker, entry.name, entry.media_type) != key:
            unreadable.add(key)
            continue
        entries.setdefault(key, entry)
    return _UserCopies(entries, frozenset(unreadable))


async def _stored_sync_not_loaded(session: AsyncSession) -> bool:
    """库里存着官方同步数据，这次启动却没有装入目录查询（读取或解析失败）。"""
    if catalog.synced_data() is not None:
        return False
    stored = await session.scalar(select(CatalogOfficialSync.id).where(CatalogOfficialSync.catalog.is_not(None)))
    return stored is not None


def _listing_default_entry(data: CatalogData, provider_id: str, media_type: str) -> Ident | None:
    """目录数据里供应商在该媒体类型下的默认模型所引用的条目身份。

    同一个 API 模型名可以在不同媒体类型下各列一个模型（如图片与视频共用名字的两栖模型），按条目的媒体类型取。
    """
    listing = data.builtin_providers.get(provider_id)
    api_model_name = listing.defaults.get(media_type) if listing is not None else None
    if listing is None or api_model_name is None:
        return None
    for item in listing.models:
        entry = data.entry_of(item) if item.api_model_name == api_model_name else None
        if entry is not None and entry.media_type == media_type:
            return (entry.maker, entry.name, entry.media_type)
    return None


def _release_default(
    release: _Release, synced: CatalogData | None, copies: _UserCopies, provider_id: str, media_type: str
) -> str | _Unresolved | None:
    """这个发布版的实例在升级前会用的默认模型（停用之前）。

    目录取旧出厂快照与同步数据中数据集版本较新的一份，再叠上用户修改过的条目副本：副本带关停日期、今天已下线时，
    旧版本按没有默认处理。默认模型引用的条目有读不出的副本时还原不了。
    """
    if synced is not None and parse_dataset_version(synced.dataset_version) > parse_dataset_version(
        release.snapshot_dataset_version
    ):
        if _listing_default_entry(synced, provider_id, media_type) in copies.unreadable:
            return _UNRESOLVED
        composed = compose_catalog(synced, copies.entries.values()).data if copies.entries else synced
        return catalog.default_model_in(composed, provider_id, media_type, release.app_version)
    frozen = release.snapshot_defaults.get(provider_id, {}).get(media_type)
    if frozen is None:
        return None
    model, entry_ref = frozen
    maker, _, name = entry_ref.partition("/")
    key = (maker, name, media_type)
    if key in copies.unreadable:
        return _UNRESOLVED
    copy = copies.entries.get(key)
    if copy is not None and entry_lifecycle(copy).discontinued:
        return None
    return model


def _current_default(provider_id: str, media_type: str) -> str | None:
    """此刻的推荐模型（当前目录、当前应用版本，已叠上用户副本，去掉下线的）。"""
    return catalog.default_model(provider_id, media_type)


def _auto_pick(default_of: DefaultOf, ready: tuple[str, ...], media_type: str) -> AutoPick:
    """旧版本的自动挑选：按注册顺序取第一个有生效凭证、有可用默认的内置供应商，都没有就落到自定义默认这一层。"""
    for provider_id in ready:
        model = default_of(provider_id, media_type)
        if model is _UNRESOLVED:
            return _UNKNOWN
        if isinstance(model, str):
            return AutoPick("builtin", provider_id, model)
    return _FALLBACK


def _with_disabled(default_of: DefaultOf, disabled: set[Key]) -> DefaultOf:
    """被用户停用的默认模型在旧版本里按没有默认处理。"""

    def _of(provider_id: str, media_type: str) -> str | _Unresolved | None:
        model = default_of(provider_id, media_type)
        if isinstance(model, str) and (provider_id, model) in disabled:
            return None
        return model

    return _of


def _table(candidates: list[DefaultOf], ready: tuple[str, ...]) -> RecommendationTable:
    """按每个候选发布版各算一次，结果一致的才算数，不一致的记为还原不了。"""
    models: dict[Key, str] = {}
    unresolved: set[Key] = set()
    for provider_id, media_type in _all_keys():
        found = {default_of(provider_id, media_type) for default_of in candidates}
        if len(found) > 1 or _UNRESOLVED in found:
            unresolved.add((provider_id, media_type))
            continue
        (model,) = found
        if isinstance(model, str):
            models[(provider_id, media_type)] = model
    auto: dict[str, AutoPick] = {}
    for media_type in MEDIA_TYPES:
        picks = {_auto_pick(default_of, ready, media_type) for default_of in candidates}
        auto[media_type] = picks.pop() if len(picks) == 1 else _UNKNOWN
    return RecommendationTable(models, frozenset(unresolved), auto, ready)


def _unknown(ready: tuple[str, ...]) -> RecommendationTable:
    return RecommendationTable(
        unresolved=frozenset(_all_keys()), auto=dict.fromkeys(MEDIA_TYPES, _UNKNOWN), ready=ready
    )


async def current_recommendations(session: AsyncSession) -> RecommendationTable:
    """此刻的推荐模型（当前目录、当前应用版本，去掉下线与停用的），没有还原不了的项。"""
    return _table([_with_disabled(_current_default, await _disabled(session))], await _ready(session))


async def _load_source(session: AsyncSession) -> upgrade_source.Source:
    return upgrade_source.parse(await SystemSettingRepository(session).get(upgrade_source.SOURCE_SETTING_KEY, ""))


async def compute(session: AsyncSession) -> RecommendationTable:
    """按升级前的库还原升级前的版本此刻的状态。

    全新的库算此刻的推荐；升级前的库没有记录、revision 对不上已知发布版、或存着的同步数据没能读入时全部还原不了。
    """
    source = await _load_source(session)
    ready = await _ready(session)
    if not source.known:
        logger.warning("没有升级前的库 revision 记录，升级前的推荐模型还原不了")
        return _unknown(ready)
    if source.revision is None:
        return await current_recommendations(session)
    releases = _RELEASES_BY_REVISION.get(source.revision)
    if releases is None:
        logger.warning("升级前的库 revision %r 对不上已知的发布版，升级前的推荐模型还原不了", source.revision)
        return _unknown(ready)
    if await _stored_sync_not_loaded(session):
        # 旧实例读的可能就是这份同步数据，不能拿出厂快照代替
        logger.warning("库里存着官方同步数据但没有读入，升级前的推荐模型还原不了")
        return _unknown(ready)
    synced = catalog.synced_data()
    copies = await _user_copies(session)
    disabled = await _disabled(session)

    def _release(release: _Release) -> DefaultOf:
        return _with_disabled(lambda p, m: _release_default(release, synced, copies, p, m), disabled)

    return _table([_release(release) for release in releases], ready)


async def load(session: AsyncSession) -> RecommendationTable | None:
    """已存的结果；还没有存过时为 ``None``。"""
    raw = await SystemSettingRepository(session).get(SETTING_KEY, "")
    return RecommendationTable.parse(raw) if raw else None


async def capture(session: AsyncSession) -> RecommendationTable:
    """升级后首次启动时算出并存下结果，已存过就直接读回。目录的三层数据须已装入。成功后提交事务。

    结果里有这一刻的凭证状态：调用方须在实例开始接受请求之前完成这一步，失败时中止启动，免得用户在此之后
    新加的凭证被当成升级前的状态。
    """
    stored = await load(session)
    if stored is not None:
        return stored
    table = await compute(session)
    await SystemSettingRepository(session).insert_if_absent(SETTING_KEY, table.dump())
    await session.commit()
    logger.info(
        "记下升级前的推荐模型：应用版本=%s 补全 %d 项、还原不了 %d 项，自动挑选 %s",
        app_version(),
        len(table.models),
        len(table.unresolved),
        {media_type: pick.kind for media_type, pick in table.auto.items()},
    )
    # 并发启动的另一个进程先存了一份时以先存的为准
    return await load(session) or table


__all__ = [
    "SETTING_KEY",
    "AutoPick",
    "RecommendationTable",
    "capture",
    "compute",
    "current_recommendations",
    "load",
]
