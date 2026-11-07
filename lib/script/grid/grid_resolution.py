"""Resolve T2I image resolution and gate grid tiers accordingly.

4×4 / 5×5 require 4K image resolution (``lib.script.grid.layout.large_grid_allowed``).
Preview, quoting, and enqueueing must share resolution or grid counts drift; all three use this module.
The same resolution supplies pricing tiers for grid and ordinary storyboard image estimation.
"""

from __future__ import annotations

import logging

from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.script.grid.layout import GRID_FALLBACK_RESOLUTION, large_grid_allowed

logger = logging.getLogger(__name__)


async def resolve_image_resolution(r: ConfigResolver, project: dict) -> str | None:
    """Effective image-resolution tier for the project's T2I slot; ``None`` means unconfigured (callers apply their own fallback tiers).

    Resolve identity through T2I: ``execute_grid_task`` uses I2I when reference images exist, so gating and actual rendering may resolve different tiers if the slots target different models. Gating must choose at enqueue time, before reference collection; use T2I as the orthogonal capability slot representing the main grid path. Use registry identity ``model_id`` rather than a constructed backend identity to avoid constructing a backend for one gate decision. Execution resolves tiers by backend identity; providers whose model identities diverge may therefore yield different tiers.

    Treat resolution failures (such as no configured image provider) as unconfigured: gating tightens, so an unknown tier never allows large grids. This fallback may silently return a configured 4K project to 3×3; retain a warning for diagnosis."""
    try:
        resolved = await r.resolve_image_backend(project, None, generation_type="t2i")
        return await r.resolve_resolution(project, resolved.provider_id, resolved.model_id or "")
    except Exception as exc:
        logger.warning("取不到 T2I 图像分辨率档，宫格门控按未配置收紧、计价按保底档: %s", exc)
        return None


def grid_image_size(image_resolution: str | None, provider_id: str, model_id: str) -> str | None:
    """Grid resolution shared by execution/quotes: configured value or ``GRID_FALLBACK_RESOLUTION``.

    If catalog tiers exclude the fallback, omit it (``None``, ``docs/adr/0019``), letting upstream choose defaults:
    channels forward tiers unchanged (``docs/adr/0104``); out-of-catalog fallback tiers cause rejection and lack prices.
    """
    if image_resolution:
        return image_resolution
    model = catalog.get_model(provider_id, model_id, media_type="image")
    if model is not None and model.resolutions and GRID_FALLBACK_RESOLUTION not in model.resolutions:
        return None
    return GRID_FALLBACK_RESOLUTION


async def resolve_large_grid_allowed(project: dict) -> bool:
    """Gate entry point opening its own session, for HTTP routes and grid submission planning.

    Callers already holding a :class:`ConfigResolver` session (cost estimation) use :func:`resolve_image_resolution` + ``large_grid_allowed`` instead, without another session."""
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    async with resolver.session() as r:
        return large_grid_allowed(await resolve_image_resolution(r, project))


__all__ = ["grid_image_size", "resolve_image_resolution", "resolve_large_grid_allowed"]
