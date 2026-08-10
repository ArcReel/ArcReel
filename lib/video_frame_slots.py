"""视频生成的请求期能力校验（``gate_video_request``）与帧槽位组装（``plan_frame_slots``）。

从 ``MediaGenerator.generate_video_async`` 抽出的纯函数，不触碰记账、版本管理与
provider 调用，可独立导入并直接单测各能力组合分支。

槽位序位是与 ``lib.reference_compression`` 的契约：压缩器按 index 原样返回压缩后的
路径列表，调用方据 ``FrameSlotPlan`` 上的三个索引还原回 ``VideoGenerationRequest``
的 ``start_image`` / ``end_image`` / ``reference_images`` 三个字段。数组参考图恒排在
首/尾帧之后，故用一个起始索引即可切出全部数组项。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities, VideoCapabilityError

if TYPE_CHECKING:
    from PIL import Image

    from lib.reference_compression import ReferenceSpec

__all__ = [
    "FIRST_FRAME_ADAPTIVE_RATIO",
    "FrameSlotPlan",
    "VideoCapabilityProbe",
    "gate_video_request",
    "plan_frame_slots",
    "resolve_first_frame_aspect_ratio",
    "resolve_video_capabilities",
]

# 供应商 SDK 接受的字面量，表示"跟随首帧图片自身比例"而非用户指定的固定比例。
FIRST_FRAME_ADAPTIVE_RATIO = "adaptive"


class VideoCapabilityProbe(Protocol):
    """能力查询实际需要的最小后端接口：只读能力声明，不碰 generate / resume。

    比 ``VideoBackend`` 窄是有意的——收窄到用得着的那一个成员，调用方与测试替身都不必
    为一次能力查询实现整个生成协议。档位感知的 ``video_capabilities_for_tier`` 不在此列，
    它是可选成员，由 ``resolve_video_capabilities`` 探测。
    """

    @property
    def video_capabilities(self) -> VideoCapabilities: ...


def resolve_video_capabilities(
    backend: VideoCapabilityProbe,
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


def resolve_first_frame_aspect_ratio(
    *,
    caps: VideoCapabilities | None,
    aspect_ratio: str,
    has_first_frame: bool,
) -> str:
    """按 ``caps.first_frame_ratio_adaptive_only`` 施加首帧任务的 ratio 覆盖。

    声明该约束的后端在带首帧（image-to-video）的请求上只接受
    :data:`FIRST_FRAME_ADAPTIVE_RATIO`——调用方原始持有的 ``aspect_ratio``（用户比例意图，仍
    完整作用于分镜图生成）不受影响，本函数只决定实际下发给该次视频请求的值。``caps`` 为 None
    或未声明该约束、或本次请求不带首帧（纯文生 / 仅参考图）时原样返回 ``aspect_ratio``。
    """
    if caps is not None and caps.first_frame_ratio_adaptive_only and has_first_frame:
        return FIRST_FRAME_ADAPTIVE_RATIO
    return aspect_ratio


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


def gate_video_request(
    *,
    caps: VideoCapabilities | None,
    provider: str,
    model: str,
    prompt: str | None = None,
    end_image: Path | None = None,
    reference_images: "list[Path] | None" = None,
    reference_audio_files: "list[Path] | None" = None,
    reference_audio_total_seconds: float | None = None,
) -> None:
    """统一的请求期能力前置校验：违约抛 ``VideoCapabilityError``，通过则静默返回。

    三条可选输入路径（尾帧 / 参考图 / 参考音频）在这里一处判定，而不是散在各 backend 的
    payload 组装里各写一套——散写的后果是同一种违约在不同供应商下有的硬失败、有的静默截断，
    用户拿到照常扣费但与意图不符的结果却无从得知。故本函数只做拒绝，不做降级：
    能力不支持一律抛错，由上层渲染成用户可读错误（文案见 ``lib/i18n/*/errors.py``）。

    ``caps`` 为 None 表示调用方未查询后端能力——三条路径都不走时能力声明不影响任何结果，
    调用方可省去这次查询。传 None 却带任一路径的输入一律按不支持拒绝，而不是放行：占位一份
    "支持"的假能力会让未经能力核实的请求下发出去，正是本函数要堵的降级。

    ``reference_audio_total_seconds`` 是调用方前置探测好的多段参考音频总时长（秒），本函数
    不做 I/O、不自行探测——探测需要读音频元数据，只能在能拿到文件的组装前置校验处完成
    （见 :func:`lib.media_generator.MediaGenerator.generate_video_async`）。传 None 表示总时长
    未知（探测失败/环境不支持），此时跳过总时长校验而不是当作超限拒绝——与本仓库 ffprobe
    不可用时降级放行的既有口径一致。

    ``prompt`` 与三条可选路径不同：它在每个请求上都存在，故 ``caps`` 未声明
    ``max_prompt_chars`` 时（含 ``caps`` 为 None）跳过该项——未声明约束不等于上限为 0。

    与 :func:`plan_frame_slots` 分离是有意的：校验会抛、组装是纯函数，两者调用时机不同——
    校验须先于记账括号（硬失败要不扣费、不留 failed ApiCall 行），组装则可在其后按需进行。
    """
    prompt_limit = None if caps is None else caps.max_prompt_chars
    if prompt_limit is not None and prompt is not None and len(prompt) > prompt_limit:
        # 供应商对超长 prompt 普遍是静默截断而非报错（如 wan2.7 文档原文「超过部分会自动截断」，
        # 错误码表无对应条目），照常扣费却产出与意图不符的成片——付费前拒绝，不放行。
        raise VideoCapabilityError(
            "video_prompt_too_long",
            provider=provider,
            model=model,
            limit=prompt_limit,
            count=len(prompt),
        )

    if end_image is not None and (caps is None or not caps.last_frame):
        raise VideoCapabilityError("video_last_frame_unsupported", provider=provider, model=model)

    if reference_images:
        limit = 0 if caps is None else caps.max_reference_images
        if limit <= 0:
            raise VideoCapabilityError("video_reference_images_unsupported", provider=provider, model=model)
        if len(reference_images) > limit:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                provider=provider,
                model=model,
                limit=limit,
                count=len(reference_images),
            )

    if reference_audio_files:
        # 不支持音色输入的模型收到音频不静默丢弃：静默丢弃会生成一段音色随机的视频并照常
        # 扣费，用户以为角色声音已受控，直到成片拼接才发现跨片段音色不一致。
        mode = ReferenceAudioMode.NONE if caps is None else caps.reference_audio_mode
        if mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=provider, model=model)
        audio_limit = 0 if caps is None else caps.max_reference_audio_count
        if len(reference_audio_files) > audio_limit:
            raise VideoCapabilityError(
                "video_reference_audio_exceeded",
                provider=provider,
                model=model,
                limit=audio_limit,
                count=len(reference_audio_files),
            )

        total_limit = None if caps is None else caps.max_reference_audio_total_seconds
        if (
            total_limit is not None
            and reference_audio_total_seconds is not None
            and reference_audio_total_seconds > total_limit
        ):
            raise VideoCapabilityError(
                "video_reference_audio_duration_exceeded",
                provider=provider,
                model=model,
                limit=total_limit,
                total=reference_audio_total_seconds,
            )


def plan_frame_slots(
    *,
    start_image: "str | Path | Image.Image | None" = None,
    end_image: Path | None = None,
    reference_images: "list[Path] | None" = None,
) -> FrameSlotPlan:
    """按输入组装帧/参考图槽位——纯函数，不判定能力、不抛异常。

    能力判定归 :func:`gate_video_request`；本函数只负责把三个请求字段铺进压缩器的 specs
    序列并记下各自序位。

    ``start_image`` 仅 ``str`` / ``Path`` 文件源进压缩器；``PIL.Image`` 与 None 不入
    specs（对应请求字段保持 None）。
    """
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
