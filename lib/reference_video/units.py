"""参考生视频 unit 的查找与镜头级定桶判据。

定桶判据供执行、入队预检、限流路由投影与费用估算共用（``docs/adr/0054``）：参考路线内按
镜头是否携带参考图分流——有参考图 → r2v；无参考图的退化镜头降级 → i2v，不送入拒空参考
的 r2v 桶模型（部分 r2v 模型对空 ``reference_images`` 抛 ``video_reference_images_required``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅类型导入：lib.project_manager 经 lib.reference_video 包初始化间接加载本模块，而
    # lib.config.resolver 又反向 import lib.project_manager，运行时导入会成环。
    from lib.config.resolver import VideoCapability


def reference_video_bucket(*, with_references: bool) -> VideoCapability:
    """参考生视频镜头的能力桶：有参考图 → r2v；无参考图的退化镜头 → i2v。

    入队预检 / 限流投影 / 费用估算等读侧按 unit 声明的 references 判定；执行层按成功解析
    的参考图判定。声明引用缺图会在解析时直接报错，不会静默换桶生成。
    """
    return "r2v" if with_references else "i2v"


def reference_unit_video_bucket(unit: dict | None) -> VideoCapability:
    """自包含 unit 的能力桶（读侧近似判据，见 :func:`reference_video_bucket`）。"""
    return reference_video_bucket(with_references=bool((unit or {}).get("references")))


def find_reference_unit(script: dict, unit_id: str) -> dict | None:
    """在剧本的自包含 ``video_units`` 中定位单元。"""
    units = script.get("video_units") or []
    return next((u for u in units if isinstance(u, dict) and u.get("unit_id") == unit_id), None)
