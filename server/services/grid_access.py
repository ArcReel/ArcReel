"""宫格写操作的项目级准入判定。

重生成 / 切分 / 上传 / 版本还原四条写入口共用这一份判定：任一入口漏掉它，
被封禁项目的残留 grid 记录就能从那条路被改写。项目的加载留给各调用方，
本模块只承担「这个项目此刻允不允许改写宫格」的策略。
"""

from __future__ import annotations

from typing import Any

from lib.api_errors import BadRequestError
from lib.project_manager import grid_storyboard_enabled
from server.services.project_schema import require_current_schema


def ensure_grid_writable(project: dict[str, Any], *, project_name: str) -> None:
    """项目不允许改写宫格时抛错。

    - 未完成数据升级的项目一律拒绝（409）：下面两条判定按新契约取字段，旧形态项目
      读不到 ``creation_type``，广告项目会被当成剧集放行，从而经付费重生成改写宫格；
    - 广告/短片项目不开放宫格分镜，残留的历史 grid 记录不可再被改写；
    - 宫格开关关闭后同理（含 reference_video 路线）——历史 grid 不再可
      重生成/切分/上传/还原。
    """
    require_current_schema(project, name=project_name)
    if project.get("creation_type") == "ad":
        raise BadRequestError("ad_grid_not_supported")
    if not grid_storyboard_enabled(project):
        raise BadRequestError("grid_storyboard_not_enabled")
