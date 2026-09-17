"""安装记录：将校验通过的定义与记录原子写入（唯一约束守住并发安装），并判定已安装端点的两轴状态。

市场轴只比对安装记录版本与当前索引条目版本的字符串，不看本地 ``meta.version``；来源被禁用、被删除或
条目已从索引移除一律为 ``unavailable``。本地修改轴只比对当前定义摘要与安装摘要。两轴互不影响。
"""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import ConflictError, NotFoundError
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db.base import utc_now
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

from .entries import snapshot_entries
from .index import MarketIndexEntry


class InstallationState(StrEnum):
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    UNAVAILABLE = "unavailable"


class InstallationStatus(NamedTuple):
    state: InstallationState
    modified: bool


def definition_digest(definition: Mapping[str, Any]) -> str:
    """安装记录与本地修改判定共用的摘要：键排序、紧凑分隔符、不转义 Unicode 的 UTF-8 JSON。"""
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def available_entries(source: MarketSource | None) -> dict[str, MarketIndexEntry]:
    """按 slug 索引安装记录在市场中仍可用的条目：来源须存在、已启用，且快照里仍列出该 slug。"""
    if source is None or not source.is_enabled:
        return {}
    return {entry.slug: entry for entry in snapshot_entries(source)}


def installation_status(
    *, installed_version: str, installed_digest: str, definition: Mapping[str, Any], entry: MarketIndexEntry | None
) -> InstallationStatus:
    """``entry`` 为 None 表示条目在市场中不可用；索引版本更旧同样算可更新。"""
    if entry is None:
        state = InstallationState.UNAVAILABLE
    elif entry.version != installed_version:
        state = InstallationState.UPDATE_AVAILABLE
    else:
        state = InstallationState.CURRENT
    return InstallationStatus(state=state, modified=definition_digest(definition) != installed_digest)


def _author_and_name(definition: Mapping[str, Any]) -> tuple[object, object]:
    meta = definition.get("meta")
    return (meta.get("author"), meta.get("name")) if isinstance(meta, Mapping) else (None, None)


async def write_installation(
    session: AsyncSession,
    *,
    source_key: str,
    slug: str,
    definition: dict[str, Any],
    overwrite_endpoint_id: int | None,
) -> CustomEndpoint:
    """调用方负责校验与提交；任一失败回滚端点与记录两者。

    覆盖目标只能是持有本条目安装记录的端点，或没有安装记录、与定义同作者同名的端点。
    """
    repo = CustomEndpointRepository(session)
    mirror = derive_mirror_columns(definition)
    existing = await session.scalar(
        select(MarketInstallation).where(MarketInstallation.source_key == source_key, MarketInstallation.slug == slug)
    )
    if overwrite_endpoint_id is None:
        if existing is not None:
            raise ConflictError("market_entry_already_installed")
        endpoint = await repo.create(
            definition=definition,
            kind=mirror.kind,
            schema_version=mirror.schema_version,
            media_type=mirror.media_type,
            display_name=mirror.display_name,
        )
    else:
        target = await session.scalar(
            select(CustomEndpoint).where(CustomEndpoint.id == overwrite_endpoint_id).with_for_update()
        )
        if target is None:
            raise NotFoundError("custom_endpoint_not_found")
        if existing is not None and existing.custom_endpoint_id != overwrite_endpoint_id:
            raise ConflictError("market_entry_already_installed")
        target_record = await session.get(MarketInstallation, overwrite_endpoint_id)
        if target_record is not None and target_record is not existing:
            raise ConflictError("market_endpoint_already_installed")
        if target_record is None and _author_and_name(target.definition) != _author_and_name(definition):
            raise ConflictError("market_overwrite_target_not_duplicate")
        endpoint = await repo.update(
            overwrite_endpoint_id,
            definition=definition,
            kind=mirror.kind,
            schema_version=mirror.schema_version,
            media_type=mirror.media_type,
            display_name=mirror.display_name,
        )
        if endpoint is None:
            raise NotFoundError("custom_endpoint_not_found")
    record = existing or MarketInstallation(custom_endpoint_id=endpoint.id, source_key=source_key, slug=slug)
    record.installed_version = definition["meta"]["version"]
    record.installed_digest = definition_digest(definition)
    record.installed_at = utc_now()
    session.add(record)
    await session.flush()
    return endpoint
