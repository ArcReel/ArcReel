"""从节点绑定推导 ComfyUI 端点的能力与参数约束。

ComfyUI 端点的定义里没有 ``capabilities`` 节（``docs/adr/0082``）：一份 workflow 恰是一个型号，
绑定表已是型号级的完整事实，再存一份能力声明只会与它打架。本模块把绑定表翻译成执行层与界面
共读的两类结论——

* **能力位**：首帧 / 尾帧 / 参考图上限 / 纯文生视频四位直接对应三项图绑定；音轨看产物那条链上
  ``audio`` 入口有没有连线。四位都是纯函数，不看模型名——模型行换个名字不会改变这份 workflow
  能做什么。本模块只回答「绑定表说了什么」，把结论装进 :class:`VideoCapabilities` /
  :class:`ImageCapability` 是 ``lib/custom_provider/endpoints.py`` 的事：子包受「不依赖声明式
  运行时」的 import 契约约束（``pyproject.toml``），而 backend 层的那两个类型间接够得到它。
* **参数约束**：宽高与帧数这两维 ArcReel 驱不驱动得了，取决于对应的语义键有没有绑定。驱动不了
  的维度对应的选择器在界面上禁用并明示，而不是照收用户的选择再静默丢掉。

推导只读定义，不读库、不发请求，也不写回定义。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .bindings import align_frames, bound_fps, int_literal_of, literal_of, positive_number, step_of, targets_of
from .graph import class_type_of, link_of
from .inference_rules import AudioTrackSource, load_inference_rules
from .workflow import is_link, node_inputs

#: 决定「有没有图输入」的三项语义键。三项全空即这份 workflow 不吃任何图片素材。
_IMAGE_BINDING_KEYS = ("start_image", "end_image", "reference_images")

#: 音轨的两种结论，取值与 :class:`lib.video_backends.base.VideoAudioMode` 的成员值逐字相同。
#: 永远不会是 ``controllable``：ComfyUI 侧没有可下发的音轨开关。
AudioTrack = Literal["always_on", "always_off"]


@dataclass(frozen=True)
class BindingCapabilities:
    """绑定表对「这份 workflow 能做什么」的全部回答，按 ``VideoCapabilities`` 的字段命名。

    ``last_frame`` 是「支持」而非「要求」：绑了尾帧的 workflow 在用户没给尾帧时照常出片，那一格
    由构造层按参考图同一套改图规则摘掉。

    参考音频三项与 ``max_prompt_chars`` 不在此列：绑定表里没有对应的语义键，声明任何值都是凭空
    捏造，投影处按 ``VideoCapabilities`` 的默认值留空。
    """

    text_to_video: bool
    first_frame: bool
    last_frame: bool
    max_reference_images: int
    audio_track: AudioTrack


def derive_video_capabilities(definition: Mapping[str, Any]) -> BindingCapabilities:
    """把一份视频端点定义的绑定表翻译成能力位。纯函数：不看模型名，不读库。"""
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    has_image_input = any(targets_of(bindings.get(key)) for key in _IMAGE_BINDING_KEYS)
    return BindingCapabilities(
        text_to_video=not has_image_input,
        first_frame=bool(targets_of(bindings.get("start_image"))),
        last_frame=bool(targets_of(bindings.get("end_image"))),
        max_reference_images=len(targets_of(bindings.get("reference_images"))),
        audio_track=derive_audio_track(workflow, bindings),
    )


def derive_audio_track(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> AudioTrack:
    """成片音轨的形态：产物那条链上的 ``audio`` 入口有连线即 ``always_on``，否则 ``always_off``。

    永不 ``controllable``：音轨开关在 ComfyUI 侧不是请求参数，而是 workflow 作者连不连那条线，
    ArcReel 没有可下发的开关，把它报成可控只会让用户的开 / 关意图无声落空。

    产物节点自己带 ``audio`` 入口（``VHS_VideoCombine``），或者音频入口在它上游一跳的成片节点上
    （``SaveVideo`` ← ``CreateVideo``），两种形态都写在 ``inference_rules.video.json`` 的
    ``audio_track_sources`` 里。名录之外的产物节点形态一律回退 ``always_off``：认不出来时报「无声」
    会让用户看见一个空的音轨开关去核对，报「恒有声」则是替一份读不懂的图打包票。
    """
    targets = targets_of(bindings.get("output"))
    if not targets:
        return "always_off"
    node = workflow.get(str(targets[0]["node"]))
    if not isinstance(node, Mapping):
        return "always_off"
    for source in load_inference_rules("video").audio_track_sources:
        if class_type_of(node) != source.class_type:
            continue
        carrier = _audio_carrier(workflow, node, source)
        if carrier is None:
            return "always_off"
        return "always_on" if is_link(node_inputs(carrier).get(source.audio_input)) else "always_off"
    return "always_off"


def _audio_carrier(
    workflow: Mapping[str, Any], node: Mapping[str, Any], source: AudioTrackSource
) -> Mapping[str, Any] | None:
    """带 ``audio`` 入口的那个节点：产物节点自身，或沿 ``through`` 入口往上一跳的成片节点。

    往上一跳只认 ``through_class_types`` 里的类型：``SaveVideo.video`` 接的若是别的什么节点，这份
    图的成片形态就不是名录描述的那一种，判不出音轨。
    """
    if source.through_input is None:
        return node
    link = link_of(node_inputs(node).get(source.through_input))
    if link is None:
        return None
    upstream = workflow.get(link[0])
    if not isinstance(upstream, Mapping) or class_type_of(upstream) not in source.through_class_types:
        return None
    return upstream


def takes_reference_images(definition: Mapping[str, Any]) -> bool:
    """这份 workflow 有没有参考图格子——图像端点的能力就由这一位决定。

    没有即仅文生图、有即仅图生图，两者互斥而不是并集（``docs/adr/0082``）：有格子却不给图，
    构造层要么删读图节点、要么提交一张空图——都不是「也支持文生图」。按桶严格分流、不回退
    （``docs/adr/0015``），端点因此只报它真正走得通的那一条。
    """
    return bool(targets_of(definition["bindings"].get("reference_images")))


# ── 参数约束 ──────────────────────────────────────────────────────


def size_is_fixed(bindings: Mapping[str, Any]) -> bool:
    """尺寸这一维 ArcReel 驱动不动：宽高两侧不是都有绑定。

    要求两侧齐备而不是任一侧有值：只绑一侧时派生出的宽高只写得进绑了的那一侧，另一侧仍是
    workflow 的固定值，产出的比例既不是 workflow 原生的也不是用户选的——比两侧都不绑更坏，因为
    用户会以为自己选的比例生效了。缺一侧即整维判为固定，分辨率选择器禁用并明示。
    """
    return not (targets_of(bindings.get("width")) and targets_of(bindings.get("height")))


def native_short_edge(definition: Mapping[str, Any]) -> int | None:
    """workflow 字面宽高的较小者，即这份图的原生短边。说不准时 ``None``。

    界面据它给分辨率选择器的空值占位写出「workflow 原生（480p）」这类文案——用户看得见「不选
    档位会得到什么」，而不是一个不知所指的「默认」。这句话说的是这份图自己那一档，因此宽高两侧
    各自的每个入口都要给得出同一个字面值：一侧绑了两个入口而字面值不一致（1024×576 与 512×512
    两路消费者）时，这份图没有「原生」那一档可言，报较小的那个等于替用户认下其中一路；其中一个
    入口读不出字面值（接的是链接）时同样不报——填值层照样会写它，回写一致性无从谈起。

    尺寸判为固定时同样没有这个值：``_write_size`` 在那一支直接返回，根本走不到字面短边那一步，
    说出的档位不对应任何实际行为。那一支的占位文案改由 ``size_fixed`` 那一位给出。

    构造层不选档位时仍按 ``request_builder._literal_short_edge`` 取所有字面值的较小者：它必须写
    出一个尺寸，图里那几个数中的较小者比一个全局兜底常量更贴近这份图；界面这一侧是「能不能称
    其为原生」，判据因此比它严。
    """
    workflow: Mapping[str, Any] = definition["workflow"]
    bindings: Mapping[str, Any] = definition["bindings"]
    if size_is_fixed(bindings):
        return None
    edges: list[int] = []
    for key in ("width", "height"):
        targets = targets_of(bindings.get(key))
        literals = [
            value for target in targets if (value := int_literal_of(workflow, target)) is not None and value > 0
        ]
        if len(literals) != len(targets) or len(set(literals)) != 1:
            return None
        edges.append(literals[0])
    return min(edges)


def duration_is_fixed(bindings: Mapping[str, Any]) -> bool:
    """时长这一维 ArcReel 驱动不动：``frames`` 没有绑定，帧数只能是 workflow 的字面值。"""
    return not targets_of(bindings.get("frames"))


def native_duration(definition: Mapping[str, Any]) -> int | None:
    """这份 workflow 自己那一档时长（秒），取整。

    ``round((字面 frames − 1) / fps)``：ComfyUI 的视频模型按「首帧 + 若干段间隔」计帧，与构造层
    ``frames = round(时长 × 帧率) + 1`` 是同一个换算的逆向，两处必须同源，否则默认档位一提交就
    被改成另一个帧数。

    两个来源缺一即 ``None``：字面帧数只能顺着 ``frames`` 绑定去图里读（未绑定时 ArcReel 没有任何
    指针指向帧数所在的那个输入），帧率取 ``fps`` 只读绑定读出的字面值、没有该绑定时取 ``frames``
    条目上手填的常量。推不出来就不推——凭一个猜出来的帧率给出一个默认时长，用户选了它却出一段
    别的长度的片，比让这一维明示「不由 ArcReel 驱动」更糟。

    选中之后会改掉图的档位同样 ``None``：81 帧 @ 24fps 折成 3 秒，而提交 3 秒会让构造层把帧数
    写成 73；绑了多个帧数入口而字面值不一致时，报出的那一档也会把其余入口一起改掉。一个名为
    「原生」、选中却改动原图的档位比没有档位更坏，故每个绑定入口都要能原样写回它现在的值——
    含各自的帧率来源与步长，判据与填值层共读 :func:`~.bindings.align_frames`。其中一个入口读不出
    字面帧数（接的是链接、或值本身不合法）时同样不报：填值层照样会写它，那一格回写成什么无从判断。
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    frames_targets = targets_of(bindings.get("frames"))
    literals = [
        (target, value)
        for target in frames_targets
        if (value := int_literal_of(workflow, target)) is not None and value > 1
    ]
    if not literals or len(literals) != len(frames_targets):
        return None
    bound = bound_fps(workflow, bindings)

    def fps_of(target: Mapping[str, Any]) -> float | None:
        return bound if bound is not None else positive_number(target.get("fps"))

    first_fps = fps_of(literals[0][0])
    if first_fps is None:
        return None
    seconds = round((literals[0][1] - 1) / first_fps)
    if seconds <= 0:
        return None
    for target, value in literals:
        fps = fps_of(target)
        if fps is None or align_frames(round(seconds * fps) + 1, step_of(target)) != value:
            return None
    return seconds


def keeps_its_own_frame_count(definition: Mapping[str, Any]) -> bool:
    """帧数入口都写着自己的帧数（字面值 > 1），而这些值凑不出一档原生时长。

    这种图对外的声明正是「时长不由 ArcReel 驱动」——``endpoint_durations`` 为空、界面只读禁用、
    规划层借一个默认秒数过关。填值层因此也不该按那个借来的时长改写帧数：81 帧 @ 24fps 换算成
    4 秒会被写成 97 帧，一边说不驱动一边把片长改掉。两处对同一件事要给同一个答案。

    ``length`` 还是 1 这类占位值不算「自己的帧数」：那种图没有片长可保，帧数照常由请求驱动。
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    targets = targets_of(bindings.get("frames"))
    if not targets:
        return False
    literals = [value for target in targets if (value := int_literal_of(workflow, target)) is not None and value > 1]
    return len(literals) == len(targets) and native_duration(definition) is None


def default_supported_durations(definition: Mapping[str, Any]) -> list[int]:
    """ComfyUI 视频模型行的 ``supported_durations`` 默认集：只含这份 workflow 的原生时长。

    不走 ``duration_presets.infer_supported_durations`` 的模型名启发式：模型行的名字由用户随手填，
    与 workflow 实际能出多长毫无关系（``my-wan-workflow`` 这样的名字在预设表里会命中 ``[4, 8]``）。

    原生时长推不出来时是空集——``frames`` 未绑定必然落在这一支（读不到字面帧数），绑了却没有帧率
    来源也落在这一支。空集在这条通道上是合法状态而非缺陷（见 ``lib/config/resolver.py`` 对
    ``kind: comfyui`` 的分叉）：时长这一维不由 ArcReel 驱动，界面禁用时长控件、提交时不下发时长，
    让 workflow 出它自己那一档。
    """
    native = native_duration(definition)
    return [] if native is None or native <= 0 else [native]


def fps_literals(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> list[float]:
    """全部 ``fps`` 只读绑定各自读出的正字面值，按绑定顺序。"""
    return [
        value for target in targets_of(bindings.get("fps")) if (value := positive_number(literal_of(workflow, target)))
    ]
