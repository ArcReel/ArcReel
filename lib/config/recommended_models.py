"""内置供应商的推荐模型：写进用户自己的默认模型设置，推荐变化时只提示（docs/adr/0099、docs/adr/0103）。

模型目录里每个内置供应商按媒体类型给出的默认模型只作推荐，运行时解析不读它。推荐进入用户设置只有三个入口：

- 供应商首次配置（第一个凭证生效）时，这个媒体类型的全局默认模型还没有设置，就写入推荐模型（:func:`seed_provider_recommendations`）。
- 升级后首次启动时一次性固化：没有全局默认模型设置的媒体类型，写入旧版自动挑选在这一刻会选中的模型（:func:`freeze_effective_defaults`）。
- 用户在推荐变化的提示上选择改用（:func:`acknowledge_recommendation`）。

提示的判据是「已知推荐」：写入、固化或确认时记下每个供应商在各媒体类型下当时的推荐模型，存在全局设置
``recommended_models_seen`` 里。之后推荐与记下的不同，就在全局默认模型旁提示，改不改由用户决定
（:func:`recommendation_changes`）。这个键同时是固化迁移的完成标记。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import MEDIA_TYPES
from lib.catalog import builtin_models
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import SystemSettingRepository
from lib.config.service import ConfigService

logger = logging.getLogger(__name__)

#: 全局设置键：每个内置供应商在各媒体类型下已知的推荐模型（JSON ``{provider: {media_type: model}}``）。
SEEN_SETTING_KEY = "recommended_models_seen"

#: 各媒体类型的全局默认模型键：推荐模型只写进默认层，不写任务类型桶与文本档位（docs/adr/0051、docs/adr/0054）。
GLOBAL_DEFAULT_KEYS: dict[str, str] = {
    "text": "default_text_backend",
    "image": "default_image_backend",
    "video": "default_video_backend",
    "audio": "default_audio_backend",
}

Seen = dict[str, dict[str, str]]


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


def _parse_seen(raw: str | None) -> Seen:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        provider: {media: model for media, model in models.items() if isinstance(model, str)}
        for provider, models in data.items()
        if isinstance(provider, str) and isinstance(models, dict)
    }


async def _read_seen(repo: SystemSettingRepository) -> Seen:
    return _parse_seen(await repo.get(SEEN_SETTING_KEY, ""))


async def _write_seen(repo: SystemSettingRepository, seen: Seen) -> None:
    await repo.set(SEEN_SETTING_KEY, json.dumps(seen, ensure_ascii=False, sort_keys=True))


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


async def seed_provider_recommendations(session: AsyncSession, provider_id: str) -> None:
    """供应商首次配置：全局默认模型还没有设置的媒体类型写入它的推荐模型，并记下全部推荐。已有设置不覆盖。

    由调用方提交事务。
    """
    if provider_id not in PROVIDER_REGISTRY:
        return
    repo = SystemSettingRepository(session)
    recommendations = await _recommendations(session, provider_id)
    for media_type, model in recommendations.items():
        key = GLOBAL_DEFAULT_KEYS[media_type]
        if not _is_set(await repo.get(key, "")):
            await repo.set(key, f"{provider_id}/{model}")
    seen = await _read_seen(repo)
    seen[provider_id] = {**seen.get(provider_id, {}), **recommendations}
    await _write_seen(repo, seen)


async def freeze_effective_defaults(session: AsyncSession) -> None:
    """一次性固化：没有全局默认模型设置的媒体类型，写入旧版自动挑选此刻会选中的内置模型。

    旧版在没有默认模型设置时按注册顺序取第一个有凭证的内置供应商的目录默认模型（跳过已下线和被停用的），
    这里按同一口径写进用户设置，升级前后生成用的模型不变。旧版自动挑选落到自定义供应商默认模型的，
    运行时仍按同一规则挑选，不写入。同时记下所有已配置内置供应商当前的推荐，作为之后提示推荐变化的基准。

    ``recommended_models_seen`` 存在即已固化过，再次调用不做任何事。成功后提交事务。
    """
    repo = SystemSettingRepository(session)
    if await repo.get(SEEN_SETTING_KEY, "") != "":
        return
    ready = await _ready_builtin_providers(session)
    recommendations = {provider_id: await _recommendations(session, provider_id) for provider_id in ready}
    for media_type, key in GLOBAL_DEFAULT_KEYS.items():
        if _is_set(await repo.get(key, "")):
            continue
        for provider_id in ready:
            model = recommendations[provider_id].get(media_type)
            if model is not None:
                await repo.set(key, f"{provider_id}/{model}")
                logger.info("固化默认模型：%s = %s/%s", key, provider_id, model)
                break
    await _write_seen(repo, {provider_id: found for provider_id, found in recommendations.items() if found})
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
            if (
                recommended is not None
                and recommended != model
                and recommended != seen.get(provider_id, {}).get(media_type)
            ):
                changes.append(RecommendationChange(media_type, provider_id, recommended, current))
            continue
        if ready is None:
            ready = await _ready_builtin_providers(session)
        for provider_id in ready:
            recommended = await builtin_models.recommended_builtin_model(session, provider_id, media_type)
            if recommended is not None and recommended != seen.get(provider_id, {}).get(media_type):
                changes.append(RecommendationChange(media_type, provider_id, recommended, None))
                break
    return changes


class StaleRecommendationError(ValueError):
    """要确认的推荐模型已不是供应商当前的推荐（推荐在提示之后又变了）。"""


async def acknowledge_recommendation(
    session: AsyncSession, provider_id: str, media_type: str, model: str, *, apply: bool
) -> None:
    """用户处理了一条推荐变化：记下这个推荐；``apply`` 为真时同时把它设为这个媒体类型的全局默认模型。

    ``model`` 不是供应商当前的推荐时抛 :class:`StaleRecommendationError`，什么都不改。由调用方提交事务。
    """
    if media_type not in GLOBAL_DEFAULT_KEYS:
        raise StaleRecommendationError(media_type)
    recommended = await builtin_models.recommended_builtin_model(session, provider_id, media_type)
    if recommended is None or recommended != model:
        raise StaleRecommendationError(model)
    repo = SystemSettingRepository(session)
    if apply:
        await repo.set(GLOBAL_DEFAULT_KEYS[media_type], f"{provider_id}/{model}")
    seen = await _read_seen(repo)
    seen.setdefault(provider_id, {})[media_type] = model
    await _write_seen(repo, seen)


__all__ = [
    "GLOBAL_DEFAULT_KEYS",
    "SEEN_SETTING_KEY",
    "RecommendationChange",
    "StaleRecommendationError",
    "acknowledge_recommendation",
    "freeze_effective_defaults",
    "recommendation_changes",
    "seed_provider_recommendations",
]
