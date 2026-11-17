"""内置供应商的推荐模型：写进用户自己的默认模型设置，推荐变化时只提示（docs/adr/0099、docs/adr/0103）。

模型目录里每个内置供应商按媒体类型给出的默认模型只作推荐，运行时解析不读它。推荐进入用户设置只有三个入口：

- 升级后首次启动时一次性固化：没有全局默认模型设置的媒体类型，写入升级前的版本在这一刻会自动选中的模型
  （:func:`freeze_effective_defaults`，升级前的推荐见 :mod:`lib.config.pre_upgrade_recommendations`）。
- 固化完成之后，供应商首次配置（第一个凭证生效）时，这个媒体类型的全局默认模型还没有设置，就写入推荐模型
  （:func:`seed_provider_recommendations`）。
- 用户在推荐变化的提示上选择改用（:func:`acknowledge_recommendation`）。

提示的判据是「已知推荐」：固化、首次配置或确认时，每个供应商在各媒体类型下记一条推荐模型，各占一个全局设置
（``recommendation_seen:<provider>:<media_type>``），互不覆盖。之后推荐与记下的不同，就在全局默认模型旁提示，
改不改由用户决定（:func:`recommendation_changes`）。固化是否完成另记在 ``default_models_frozen``，只由成功的固化写入，
值是升级前的选择还原不了的媒体类型：运行时这些媒体类型不落到自定义供应商默认模型（:func:`auto_fallback_allowed`）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import MEDIA_TYPES
from lib.catalog import builtin_models
from lib.config import pre_upgrade_recommendations
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import SystemSettingRepository
from lib.config.resolver import ProviderModel
from lib.config.service import ConfigService

logger = logging.getLogger(__name__)

#: 已知推荐的全局设置键前缀，完整的键是 ``<前缀><provider>:<media_type>``，值是模型键。
SEEN_KEY_PREFIX = "recommendation_seen:"

#: 全局设置键：升级固化已完成，值是升级前的选择还原不了的媒体类型（JSON 数组）。只由成功的固化写入。
FROZEN_SETTING_KEY = "default_models_frozen"

#: 各媒体类型的全局默认模型键：推荐模型只写进默认层，不写任务类型桶与文本档位（docs/adr/0051、docs/adr/0054）。
GLOBAL_DEFAULT_KEYS: dict[str, str] = {
    "text": "default_text_backend",
    "image": "default_image_backend",
    "video": "default_video_backend",
    "audio": "default_audio_backend",
}


@dataclass(frozen=True)
class RecommendationChange:
    """一处推荐变化：``provider_id`` 在 ``media_type`` 下现在推荐 ``recommended_model``。

    ``current`` 是这个媒体类型的全局默认模型（``provider/model``），没有设置时为 ``None``。
    """

    media_type: str
    provider_id: str
    recommended_model: str
    current: str | None


def _is_set(value: str | None) -> bool:
    """全局默认模型设置有值：只有 ``provider/model`` 完整形态算，与解析链的全局层同口径。"""
    return isinstance(value, str) and "/" in value


def _seen_key(provider_id: str, media_type: str) -> str:
    return f"{SEEN_KEY_PREFIX}{provider_id}:{media_type}"


async def _read_seen(repo: SystemSettingRepository) -> dict[tuple[str, str], str]:
    seen: dict[tuple[str, str], str] = {}
    for key, model in (await repo.get_prefixed(SEEN_KEY_PREFIX)).items():
        provider_id, _, media_type = key.removeprefix(SEEN_KEY_PREFIX).partition(":")
        seen[(provider_id, media_type)] = model
    return seen


async def _recommendations(session: AsyncSession, provider_id: str) -> dict[str, str]:
    """供应商当前各媒体类型下可用的推荐模型：已下线、被用户停用或低于最低应用版本的不算。"""
    found: dict[str, str] = {}
    for media_type in MEDIA_TYPES:
        model = await builtin_models.recommended_builtin_model(session, provider_id, media_type)
        if model is not None:
            found[media_type] = model
    return found


async def _ready_builtin_providers(session: AsyncSession) -> list[str]:
    """有生效凭证的内置供应商，按注册顺序（旧版自动挑选的优先级）。"""
    statuses = await ConfigService(session).get_all_providers_status()
    ready = {status.name for status in statuses if status.status == "ready"}
    return [provider_id for provider_id in PROVIDER_REGISTRY if provider_id in ready]


async def is_frozen(session: AsyncSession) -> bool:
    """升级固化是否已完成。"""
    return await SystemSettingRepository(session).get(FROZEN_SETTING_KEY, "") != ""


def auto_fallback_allowed(frozen_marker: str, media_type: str) -> bool:
    """没有任何默认模型设置时，这个媒体类型能否落到自定义供应商的默认模型。

    ``frozen_marker`` 是 :data:`FROZEN_SETTING_KEY` 的值。固化完成、且升级前的选择能还原时才可以：旧版本在这里会
    选内置模型的，固化已把它写进全局默认模型；会落到自定义默认的，新版本照旧。固化没完成或还原不了时，落到
    自定义默认可能就是换了模型，改为报错，由用户选择默认模型。
    """
    if not frozen_marker:
        return False
    try:
        unknown = json.loads(frozen_marker)
    except ValueError:
        return False
    return isinstance(unknown, list) and media_type not in unknown


async def seed_provider_recommendations(session: AsyncSession, provider_id: str) -> None:
    """供应商首次配置：全局默认模型还没有设置的媒体类型写入它的推荐模型，并为还没有记录的媒体类型记下推荐。

    已有设置不覆盖；已记下的推荐也不覆盖，凭证删除重建不会吞掉还没处理的推荐变化。升级固化还没完成时
    什么都不写：固化按升级前的口径挑模型，先写入的推荐会抢在它前面。由调用方提交事务。
    """
    if provider_id not in PROVIDER_REGISTRY:
        return
    if not await is_frozen(session):
        logger.warning("默认模型的升级固化还没完成，暂不写入 %s 的推荐模型", provider_id)
        return
    repo = SystemSettingRepository(session)
    for media_type, model in (await _recommendations(session, provider_id)).items():
        key = GLOBAL_DEFAULT_KEYS[media_type]
        if not _is_set(await repo.get(key, "")):
            await repo.upsert(key, ProviderModel(provider_id, model).pair_key)
        await repo.insert_if_absent(_seen_key(provider_id, media_type), model)


async def freeze_effective_defaults(session: AsyncSession) -> None:
    """一次性固化：没有全局默认模型设置的媒体类型，写入升级前的版本会自动选中的内置模型。

    只应用升级后首次启动时算定的结果（:mod:`lib.config.pre_upgrade_recommendations`），不重新读凭证、目录或停用
    状态：首次固化失败、重试之前用户新加的凭证不会抢在升级前的选择前面。旧版本会落到自定义供应商默认模型的，
    不写入，运行时仍按同一规则挑选；还原不了的留空，写进完成标记，运行时报错并提示选择默认模型。同时把算定时
    已配置供应商的推荐记为已知推荐，之后推荐与它不同就提示；还原不了的媒体类型不记，提示会建议设为默认。

    已完成过就不做任何事。升级前的结果还没记下时抛 ``RuntimeError``，下次启动重试。成功后写完成标记并提交事务。
    """
    repo = SystemSettingRepository(session)
    if await is_frozen(session):
        return
    table = await pre_upgrade_recommendations.load(session)
    if table is None:
        raise RuntimeError("升级前的推荐模型还没有记下，默认模型的固化留到下次启动")
    unknown: list[str] = []
    for media_type, key in GLOBAL_DEFAULT_KEYS.items():
        if _is_set(await repo.get(key, "")):
            # 已有全局默认模型：旧版本用的就是它，自动挑选不参与
            continue
        pick = table.auto_pick(media_type)
        if pick.kind == "unknown":
            unknown.append(media_type)
            logger.warning("还原不了升级前 %s 的自动选择，%s 留空，运行时也不落到自定义默认模型", media_type, key)
        elif pick.kind == "builtin" and pick.provider_id is not None and pick.model is not None:
            await repo.upsert(key, ProviderModel(pick.provider_id, pick.model).pair_key)
            logger.info("固化默认模型：%s = %s/%s", key, pick.provider_id, pick.model)
    for provider_id in table.ready:
        for media_type in MEDIA_TYPES:
            model = table.model(provider_id, media_type)
            if model is not None and media_type not in unknown:
                await repo.insert_if_absent(_seen_key(provider_id, media_type), model)
    await repo.upsert(FROZEN_SETTING_KEY, json.dumps(unknown))
    await session.commit()


async def recommendation_changes(session: AsyncSession) -> list[RecommendationChange]:
    """推荐变化的提示，每个媒体类型至多一条。

    - 全局默认模型是某个内置供应商的模型：这个供应商现在的推荐与它不同，且不是已知推荐时提示。
    - 没有全局默认模型：按注册顺序第一个已配置、推荐不是已知推荐的内置供应商提示。
    """
    repo = SystemSettingRepository(session)
    seen = await _read_seen(repo)
    settings = await repo.get_all()
    ready: list[str] | None = None
    changes: list[RecommendationChange] = []
    for media_type, key in GLOBAL_DEFAULT_KEYS.items():
        current = settings.get(key, "")
        if _is_set(current):
            provider_id, model = current.split("/", 1)
            if provider_id not in PROVIDER_REGISTRY:
                continue
            recommended = await builtin_models.recommended_builtin_model(session, provider_id, media_type)
            if recommended is not None and recommended != model and recommended != seen.get((provider_id, media_type)):
                changes.append(RecommendationChange(media_type, provider_id, recommended, current))
            continue
        if ready is None:
            ready = await _ready_builtin_providers(session)
        for provider_id in ready:
            recommended = await builtin_models.recommended_builtin_model(session, provider_id, media_type)
            if recommended is not None and recommended != seen.get((provider_id, media_type)):
                changes.append(RecommendationChange(media_type, provider_id, recommended, None))
                break
    return changes


class StaleRecommendationError(ValueError):
    """要确认的推荐模型已不是供应商当前的推荐（推荐在提示之后又变了）。"""


async def acknowledge_recommendation(
    session: AsyncSession, provider_id: str, media_type: str, model: str, *, apply: bool
) -> None:
    """用户处理了一条推荐变化：记下这个推荐；``apply`` 为真时同时把它设为这个媒体类型的全局默认模型。

    每条已知推荐各占一个设置、单条语句写入，并发确认不同媒体类型互不覆盖。``model`` 不是供应商当前的推荐时抛
    :class:`StaleRecommendationError`，什么都不改。由调用方提交事务。
    """
    if media_type not in GLOBAL_DEFAULT_KEYS:
        raise StaleRecommendationError(media_type)
    recommended = await builtin_models.recommended_builtin_model(session, provider_id, media_type)
    if recommended is None or recommended != model:
        raise StaleRecommendationError(model)
    repo = SystemSettingRepository(session)
    if apply:
        await repo.upsert(GLOBAL_DEFAULT_KEYS[media_type], ProviderModel(provider_id, model).pair_key)
    await repo.upsert(_seen_key(provider_id, media_type), model)


__all__ = [
    "FROZEN_SETTING_KEY",
    "GLOBAL_DEFAULT_KEYS",
    "SEEN_KEY_PREFIX",
    "RecommendationChange",
    "StaleRecommendationError",
    "acknowledge_recommendation",
    "auto_fallback_allowed",
    "freeze_effective_defaults",
    "is_frozen",
    "recommendation_changes",
    "seed_provider_recommendations",
]
