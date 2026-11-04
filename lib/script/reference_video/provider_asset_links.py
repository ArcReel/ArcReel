"""请求投影里的供应商素材关联核对：读侧与执行侧各一份实现，共用同一套选择规则。

选择规则只有一份（:mod:`lib.asset_library.generation_inputs`）。读侧（接收任务、批量预览、报价）以当前配置装配的
调用通道求素材库范围，执行侧以实际调用通道求范围。同一份配置与同一批图片得到同一结论：适用的关联尚未可用或
入库失败时，两侧报同一个问题码与参数，提交前就能指出；执行时暂存参考图之后，再按实发字节核对一次。

核对只产生阻断问题，不改变请求投影的其余事实，也不进入产物时效依据。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.asset_library.bindings import AssetLibraryScope
from lib.asset_library.generation_inputs import (
    ProviderAssetLinkUnavailableError,
    ReferenceImageSource,
    backend_call_scope,
    choose_in_scope,
    reference_image_sources,
)
from lib.generation.video_request_facts import VideoRequestFacts
from lib.infra.path_safety import PathTraversalError
from lib.script.reference_video.request_projection import (
    ProjectionProblem,
    ReferenceAssetLinkCheck,
    ResolvedReferenceAsset,
)

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver

logger = logging.getLogger(__name__)


def asset_link_problem(error: ProviderAssetLinkUnavailableError) -> ProjectionProblem:
    """关联不可用折成投影的阻断问题：问题码与参数原样保留，提示与执行期失败一致。"""
    return ProjectionProblem(code=error.code, blocking=True, params=tuple(error.params.items()))


async def _sources(project_path: Path, assets: Sequence[ResolvedReferenceAsset]) -> list[ReferenceImageSource] | None:
    """将要发出的参考图按当下字节给出来源；有图片在可用性判定之后被移走时为 None，由缺图判定报出。"""
    images = [(asset.path, str(asset.reference.name)) for asset in assets]
    try:
        return await asyncio.to_thread(reference_image_sources, project_path, images)
    except (OSError, PathTraversalError):
        return None


async def _check_with(
    choose: Callable[[Sequence[ReferenceImageSource]], Awaitable[object]],
    project_path: Path,
    assets: Sequence[ResolvedReferenceAsset],
) -> ProjectionProblem | None:
    sources = await _sources(project_path, assets)
    if sources is None:
        return None
    try:
        await choose(sources)
    except ProviderAssetLinkUnavailableError as exc:
        return asset_link_problem(exc)
    return None


def lane_asset_link_check(
    *,
    choose: Callable[[Sequence[ReferenceImageSource]], Awaitable[object]],
    project_path: Path,
) -> ReferenceAssetLinkCheck:
    """执行侧：用实际调用通道的选择核对，``choose`` 即 ``MediaGenerator.choose_reference_image_assets``。"""

    async def check(facts: VideoRequestFacts, assets: Sequence[ResolvedReferenceAsset]) -> ProjectionProblem | None:
        del facts
        return await _check_with(choose, project_path, assets)

    return check


def configured_asset_link_check(
    *,
    project_path: Path,
    resolver: ConfigResolver,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ReferenceAssetLinkCheck:
    """读侧：按投影所落桶的执行模型，以当前配置装配调用通道求素材库范围。

    同一个核对内每个执行模型只装配一次，批量调用方整批共用一份。装配不出调用通道（凭证缺失、模型已不可用）时
    按没有范围处理，这类问题由视频请求事实或执行时的装配报出。没有范围时不计算参考图摘要。
    """
    scopes: dict[tuple[str, str], AssetLibraryScope | None] = {}

    def sessions() -> async_sessionmaker[AsyncSession]:
        if session_factory is not None:
            return session_factory
        from lib.db import async_session_factory

        return async_session_factory

    async def scope_for(facts: VideoRequestFacts) -> AssetLibraryScope | None:
        key = (facts.provider_id, facts.model_id)
        if key not in scopes:
            scopes[key] = await _configured_scope(facts, resolver, sessions())
        return scopes[key]

    async def check(facts: VideoRequestFacts, assets: Sequence[ResolvedReferenceAsset]) -> ProjectionProblem | None:
        scope = await scope_for(facts)
        if scope is None:
            return None

        async def choose(sources: Sequence[ReferenceImageSource]) -> object:
            return await choose_in_scope(
                sessions(), scope=scope, project_name=project_path.name, project_path=project_path, sources=sources
            )

        return await _check_with(choose, project_path, assets)

    return check


async def _configured_scope(
    facts: VideoRequestFacts, resolver: ConfigResolver, session_factory: async_sessionmaker[AsyncSession]
) -> AssetLibraryScope | None:
    from lib.backends.backend_assembly.assembler import assemble_backend

    try:
        backend = await assemble_backend(
            provider_id=facts.provider_id,
            media_type="video",
            model_id=facts.model_id,
            resolver=resolver,
            generation_type=facts.generation_type,
        )
    except Exception:
        logger.debug(
            "读侧装配视频调用通道失败，按没有素材库范围核对 provider=%s model=%s",
            facts.provider_id,
            facts.model_id,
            exc_info=True,
        )
        return None
    return await backend_call_scope(session_factory, backend)


__all__ = ["asset_link_problem", "configured_asset_link_check", "lane_asset_link_check"]
