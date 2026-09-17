"""将校验通过的定义与安装记录原子写入，唯一约束守住并发安装。"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import ConflictError, NotFoundError
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db.base import utc_now
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository


def definition_digest(definition: Mapping[str, Any]) -> str:
    """安装记录与本地修改判定共用的摘要：键排序、紧凑分隔符、不转义 Unicode 的 UTF-8 JSON。"""
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


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
