"""本地渲染车道 ``render``：不绑定供应商，全局并发固定为 1、不开放配置（``docs/adr/0095``）。

渲染成片与导出剪映草稿都以 ``render_`` 开头的任务类型走这条车道。它们只在本机调用随包 ffmpeg，
不调用供应商，因此不写用量记录；渲染的峰值内存以 GB 计，并发只能是 1。
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
