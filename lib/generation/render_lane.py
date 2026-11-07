"""Local ``render`` lane: no provider, fixed global concurrency 1, not configurable (``docs/adr/0095``).

Video rendering/Jianying draft exports use ``render_`` task types here, invoking local bundled ffmpeg only,
without provider usage records. Peak memory reaches GBs, requiring concurrency 1.
"""

from __future__ import annotations

RENDER_MEDIA_TYPE = "render"
RENDER_PROVIDER_ID = "render"
RENDER_TASK_PREFIX = "render_"
RENDER_LANE_CONCURRENCY = 1


def is_render_task_type(task_type: str | None) -> bool:
    return isinstance(task_type, str) and task_type.startswith(RENDER_TASK_PREFIX)


__all__ = [
    "RENDER_LANE_CONCURRENCY",
    "RENDER_MEDIA_TYPE",
    "RENDER_PROVIDER_ID",
    "RENDER_TASK_PREFIX",
    "is_render_task_type",
]
