"""宫格档位门控用的图像分辨率解析。

4×4 / 5×5 档只在图像分辨率档为 4K 时放行（见 ``lib.grid.layout.large_grid_allowed``）。
判定必须与实际入队、费用估算共用同一份解析，否则前端预览、估价与真正生成的宫格张数会漂移，
故三条路径都经本模块取档。
"""

from __future__ import annotations

import logging

from lib.config.resolver import ConfigResolver
from lib.grid.layout import large_grid_allowed

logger = logging.getLogger(__name__)


async def resolve_grid_image_resolution(r: ConfigResolver, project: dict) -> str | None:
    """宫格图执行期生效的图像分辨率档；``None`` 表示未配置（由调用方按保底档处理）。

    以 T2I 槽解析身份，与费用估算的图片计价同源；``execute_grid_task`` 在有参考图时走 I2I 槽，
    两槽解析到不同型号时门控与实际渲染可能取到不同档位——门控须在入队时就定档，那时还没有
    参考图收集结果，取正交能力槽中代表宫格主路径的 T2I。同理这里取 registry 身份
    ``model_id``（与估价一致）而非构造后的 backend 身份，避免为一次门控判定构造 backend。

    解析失败（未配置图像供应商等）按未配置处理：门控是收紧方向，取不到档位即不放行大宫格。
    这条降级会让已配置 4K 的项目静默退回 3×3，故留一条 warning 便于排查。
    """
    try:
        resolved = await r.resolve_image_backend(project, None, capability="t2i")
        return await r.resolve_resolution(project, resolved.provider_id, resolved.model_id or "")
    except Exception as exc:
        logger.warning("宫格档位门控取不到图像分辨率档，按未配置收紧: %s", exc)
        return None


async def resolve_large_grid_allowed(project: dict) -> bool:
    """自开 session 的门控入口，供路由与 SDK 工具使用。

    已持有 :class:`ConfigResolver` session 的调用方（费用估算）改用
    :func:`resolve_grid_image_resolution` + ``large_grid_allowed``，不额外开 session。
    """
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    async with resolver.session() as r:
        return large_grid_allowed(await resolve_grid_image_resolution(r, project))


__all__ = ["resolve_grid_image_resolution", "resolve_large_grid_allowed"]
