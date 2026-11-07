"""Check provider asset links in request projections: read/execution implementations share selection rules.

One selection policy (:mod:`lib.asset_library.generation_inputs`): read-side task admission/batch previews/quotes
use current-configuration backends to resolve library scope; execution uses the actual backend. Identical
configuration/images give identical conclusions. Applicable links pending/unavailable or failed upload report the
same codes/parameters before submission and execution. Recheck actual bytes after reference staging.

Checks produce blocking problems only, changing no other projection facts and entering no artifact-currency basis.
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
    """Convert unavailable links to blocking projection problems, preserving codes/parameters consistently with execution failures."""
    return ProjectionProblem(code=error.code, blocking=True, params=tuple(error.params.items()))


async def _sources(project_path: Path, assets: Sequence[ResolvedReferenceAsset]) -> list[ReferenceImageSource] | None:
    """Identify outgoing reference images by their current bytes; return None if an image moved after availability assessment, leaving missing-image assessment to report it."""
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
    """Execution check using actual backend selection; ``choose`` is ``MediaGenerator.choose_reference_image_assets``."""

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
    """Read-side check: assemble the projected bucket's effective backend from current configuration to resolve library scope.

    Assemble each effective model once per check, shared across batches. Missing credentials/unavailable models
    yield no scope, with those failures reported by request facts or execution assembly. No scope means no image hashing.
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
