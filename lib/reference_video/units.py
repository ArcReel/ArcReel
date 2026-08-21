"""参考生视频 unit 的查找与定桶纯映射。

可执行请求的实际定桶由 ``ReferenceUnitRequestProjector`` 先从正文派生参考图、再水合项目
文件完成；本模块只保留已给定布尔事实的纯映射与 unit 查找。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅类型导入：lib.project_manager 经 lib.reference_video 包初始化间接加载本模块，而
    # lib.config.resolver 又反向 import lib.project_manager，运行时导入会成环。
    from lib.config.resolver import VideoCapability


def reference_video_bucket(*, with_references: bool) -> VideoCapability:
    """视频单元的任务类型桶：有参考图 → r2v；无参考图 → i2v。

    本函数只对已给定的布尔事实做纯映射。预检、报价、Agent、队列与执行层须先由
    ``ReferenceUnitRequestProjector`` 从正文派生引用并水合实际可用资产，不能自行猜测。
    """
    return "r2v" if with_references else "i2v"


def find_reference_unit(script: dict, unit_id: str) -> dict | None:
    """在剧本的自包含 ``video_units`` 中定位单元。"""
    units = script.get("video_units") or []
    return next((u for u in units if isinstance(u, dict) and u.get("unit_id") == unit_id), None)
