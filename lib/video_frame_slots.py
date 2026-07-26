"""视频生成的帧槽位规划：能力 gating + 参考图槽位组装。

从 ``MediaGenerator.generate_video_async`` 抽出的纯函数，不触碰记账、版本管理与
provider 调用，可独立导入并直接单测各能力组合分支。

槽位序位是与 ``lib.reference_compression`` 的契约：压缩器按 index 原样返回压缩后的
路径列表，调用方据 ``FrameSlotPlan`` 上的三个索引还原回 ``VideoGenerationRequest``
的 ``start_image`` / ``end_image`` / ``reference_images`` 三个字段。数组参考图恒排在
首/尾帧之后，故用一个起始索引即可切出全部数组项。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lib.video_backends.base import VideoCapabilities, VideoCapabilityError

if TYPE_CHECKING:
    from PIL import Image

    from lib.reference_compression import ReferenceSpec

__all__ = ["FrameSlotPlan", "plan_frame_slots", "resolve_video_capabilities"]


def resolve_video_capabilities(
    backend: Any,
    *,
    service_tier: str = "default",
    resolution: str | None = None,
) -> VideoCapabilities:
    """解析后端在本次请求档位下的视频能力。

    优先取 ``video_capabilities_for_tier``（若后端实现）：某些后端（如 Kling）的
    ``last_frame`` 仅在特定 ``service_tier`` 生效，无请求上下文的 ``video_capabilities``
    属性只能保守声明，会让合法档位的请求被误判为不支持。
    """
    tier_aware = getattr(backend, "video_capabilities_for_tier", None)
    if tier_aware is not None:
        return tier_aware(service_tier, resolution=resolution)
    return backend.video_capabilities


@dataclass(frozen=True)
class FrameSlotPlan:
    """压缩器输入 specs 及各请求字段在其中的序位。

    ``start_index`` / ``end_index`` 为 None 表示该槽位本次不下发；
    ``reference_start_index`` 为 None 表示无可压缩的数组参考项，调用方回落原
    ``reference_images``（保留 None / [] 语义）。
    """

    specs: "list[ReferenceSpec]"
    start_index: int | None = None
    end_index: int | None = None
    reference_start_index: int | None = None


def plan_frame_slots(
    *,
    caps: VideoCapabilities,
    provider: str,
    model: str,
    start_image: "str | Path | Image.Image | None" = None,
    end_image: Path | None = None,
    reference_images: "list[Path] | None" = None,
) -> FrameSlotPlan:
    """按后端能力组装帧/参考图槽位，能力不支持尾帧时硬失败。

    尾帧不做降级：参考图是与首帧互斥的独立路径，把尾帧转投参考图会丢掉首帧语义；
    静默丢弃尾帧则会照常生成并扣费、产出与用户意图不符的视频。故 ``caps.last_frame``
    为假而请求携带尾帧时抛 ``VideoCapabilityError``，由上层渲染成用户可读错误。

    ``start_image`` 仅 ``str`` / ``Path`` 文件源进压缩器；``PIL.Image`` 与 None 不入
    specs（对应请求字段保持 None），维持原有行为。
    """
    if end_image is not None and not caps.last_frame:
        raise VideoCapabilityError("video_last_frame_unsupported", provider=provider, model=model)

    from lib.reference_compression import ReferenceSpec, RefRole

    specs: list[ReferenceSpec] = []
    start_index: int | None = None
    end_index: int | None = None
    reference_start_index: int | None = None

    if isinstance(start_image, (str, Path)):
        start_index = len(specs)
        specs.append(ReferenceSpec(source=Path(start_image), label="", role=RefRole.FRAME))
    if end_image is not None:
        end_index = len(specs)
        specs.append(ReferenceSpec(source=Path(end_image), label="", role=RefRole.FRAME))
    if reference_images:
        reference_start_index = len(specs)
        specs.extend(ReferenceSpec(source=Path(r), label="", role=RefRole.ARRAY) for r in reference_images)

    return FrameSlotPlan(
        specs=specs,
        start_index=start_index,
        end_index=end_index,
        reference_start_index=reference_start_index,
    )
