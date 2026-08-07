"""参考生视频 unit 的查找与镜头级定桶判据。

定桶判据供执行、入队预检、限流路由投影与费用估算共用（``docs/adr/0054``）：参考路线内按
镜头是否携带参考图分流——有参考图 → r2v；无参考图的退化镜头降级 → i2v，不送入拒空参考
的 r2v 桶模型（部分 r2v 模型对空 ``reference_images`` 抛 ``video_reference_images_required``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.reference_video.ad_units import ad_unit_references

if TYPE_CHECKING:
    # 仅类型导入：lib.project_manager 经 lib.reference_video 包初始化间接加载本模块，而
    # lib.config.resolver 又反向 import lib.project_manager，运行时导入会成环。
    from lib.config.resolver import VideoCapability


def reference_video_bucket(*, with_references: bool) -> VideoCapability:
    """参考生视频镜头的能力桶：有参考图 → r2v；无参考图的退化镜头 → i2v。

    「是否带参考图」的判据分两层：执行层按解析后的实际参考图判定（ad 的资产缺图按软口径
    跳过后可能为空）；入队预检 / 限流投影 / 费用估算等读侧按 unit 声明的 references 近似
    （与 ``precheck_unit`` 的近似同口径）。声明了参考却全部缺图的 ad 异常单元会被读侧按
    r2v 归桶、执行期按 i2v 生成——只影响预检指向与限流路由的精度，执行层独立判定。
    """
    return "r2v" if with_references else "i2v"


def reference_unit_video_bucket(unit: dict | None, *, ad_shots: list[dict] | None = None) -> VideoCapability:
    """unit 的能力桶（读侧近似判据，见 :func:`reference_video_bucket`）。

    ad 路径传入水合后的成员镜头（``ad_shots``），参考集从镜头现算——与执行侧、来源签名
    同源。索引里持久化的 ``references`` 只是展示缓存，镜头参考字段被编辑后未重新派生时
    它会落后于镜头，读侧照它定桶会让预检与执行分叉：给空缓存的 unit 补上参考后，一个
    合法的纯 r2v 配置会因「缺 i2v 能力」被拒；反过来删光参考后，预检与入队按 r2v 定桶
    并锁定供应商，执行期却按 i2v 生成，时长确认弹窗也会报出另一个桶的档位。

    narration/drama 的 unit 内容自包含（参考集就在 unit 上，无成员镜头可查），不传该参数。
    """
    if ad_shots is not None:
        return reference_video_bucket(with_references=bool(ad_unit_references(ad_shots)))
    return reference_video_bucket(with_references=bool((unit or {}).get("references")))


def find_reference_unit(script: dict, unit_id: str, *, is_ad: bool) -> dict | None:
    """在剧本中定位参考生视频 unit：ad 在 ``reference_units`` 派生索引，其余在 ``video_units``。"""
    units = (script.get("reference_units") if is_ad else script.get("video_units")) or []
    return next((u for u in units if isinstance(u, dict) and u.get("unit_id") == unit_id), None)
