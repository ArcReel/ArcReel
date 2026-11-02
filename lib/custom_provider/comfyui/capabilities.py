"""从节点绑定推导 ComfyUI 端点的能力与参数约束。

ComfyUI 端点的定义里没有 ``capabilities`` 节（``docs/adr/0082``）：一份 workflow 恰是一个型号，
绑定表已是型号级的完整事实，再存一份能力声明只会与它打架。本模块把绑定表翻译成执行层与界面
共读的两类结论——

* **能力位**：首帧 / 尾帧 / 参考图上限三位看三项图绑定；绑定表缺条时回退认 workflow 上的标题标记
  （:func:`image_key_targets`），纯文生视频仍只看正式绑定表——标题标记是「可选增强格」，只补能力
  不夺纯文能力。音轨看产物那条链上 ``audio`` 入口有没有连线。各位都是纯函数，不看模型名——模型行
  换个名字不会改变这份 workflow 能做什么。本模块只回答「绑定表说了什么」，把结论装进 :class:`VideoCapabilities` /
  :class:`ImageCapability` 是 ``lib/custom_provider/endpoints.py`` 的事：子包受「不依赖声明式
  运行时」的 import 契约约束（``pyproject.toml``），而 backend 层的那两个类型间接够得到它。
* **参数约束**：尺寸（宽高）与时长这两维 ArcReel 驱不驱动得了，取决于对应的语义键有没有写目标：
  时长可被 ``frames`` 或 ``duration`` 任一通道驱动（后者认标题标记，:func:`duration_key_targets`）。
  驱动不了的维度对应的选择器在界面上禁用并明示，而不是照收用户的选择再静默丢掉。

推导只读定义，不读库、不发请求，也不写回定义。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from arcreel_market_core.comfyui.bindings import (
    align_down,
    align_frames,
    bound_fps,
    int_literal_of,
    literal_of,
    positive_number,
    step_of,
    targets_of,
)
from arcreel_market_core.comfyui.graph import class_type_of, link_of, node_title
from arcreel_market_core.comfyui.workflow import is_link, node_inputs

from .inference import node_id_order, parse_title_marker
from .inference_rules import AudioTrackSource, load_inference_rules

#: 决定「有没有图输入」的三项语义键。三项全空即这份 workflow 不吃任何图片素材。
_IMAGE_BINDING_KEYS = ("start_image", "end_image", "reference_images")

#: 音轨的两种结论，取值与 :class:`arcreel_market_core.video_backend_contract.VideoAudioMode` 的成员值逐字相同。
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
    """把一份视频端点定义翻译成能力位。纯函数：不看模型名，不读库。

    图输入三位认「正式绑定 ∪ 标题标记」（:func:`image_key_targets`）：标题标记是「可选增强格」，
    只补能力、不夺纯文能力——``text_to_video`` 仍只看正式绑定表，因为未绑定的节点不归构造层管，
    纯文提交时 workflow 保留自己的字面值，照样走得通。
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    has_formal_image = any(targets_of(bindings.get(key)) for key in _IMAGE_BINDING_KEYS)
    return BindingCapabilities(
        text_to_video=not has_formal_image,
        first_frame=bool(image_key_targets(workflow, bindings, "start_image")),
        last_frame=bool(image_key_targets(workflow, bindings, "end_image")),
        max_reference_images=len(image_key_targets(workflow, bindings, "reference_images")),
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
    （``docs/adr/0015``），端点因此只报它真正走得通的那一条。格子认「正式绑定 ∪ 标题标记」
    （:func:`image_key_targets`）。
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    return bool(image_key_targets(workflow, bindings, "reference_images"))


def image_key_targets(
    workflow: Mapping[str, Any], bindings: Mapping[str, Any], key: str
) -> tuple[Mapping[str, Any], ...]:
    """一个图语义键的图像目标：正式绑定条目优先，绑定表缺条时回退认标题标记。

    绑定表是型号级的完整事实；workflow 里的节点却可能用标题标记（``ARCREEL:<key>`` 前缀或
    ``(<key>)`` 括号形态，两种写法同约定，解析唯一处 :func:`.inference.parse_title_marker`）声明
    图输入。绑定表没有该键的条目时（导入期推断没认出、或定义是旧版本落的盘），标记节点即该键的
    目标。能力推导（本模块）与填值层（``request_builder``）读同一份目标：能力位不漏、图也不会在
    填值时静默丢掉。

    标题标记是「可选增强格」而非独占格：未绑定的节点不归构造层管，不给图时保留 workflow 自己的
    字面值，所以它只补能力、不夺纯文能力（:func:`derive_video_capabilities` 的 ``text_to_video``
    仍只看正式绑定表）。绑定表已有该键的条目时正式条目优先、不再看标题标记——与导入期信号分级
    「手动绑定优先于标题标记」一致。
    """
    formal = targets_of(bindings.get(key))
    if formal:
        return formal
    pseudo: list[Mapping[str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping):
            continue
        if parse_title_marker(node_title(node)) != key:
            continue
        field = _image_field_of(node)
        if field is not None:
            pseudo.append({"node": str(node_id), "input": field})
    return tuple(sorted(pseudo, key=lambda target: node_id_order(target["node"])))


def _image_field_of(node: Mapping[str, Any]) -> str | None:
    """节点上承载文件名的输入字段名：惯例的 ``image`` 字面值优先，否则第一个非连线输入。

    ``image`` 接的是连线时该节点的图由上游供给，没有可写的文件名落点，``None``——写一个文件名进去
    等于剪掉那条连线，标题标记不该产生这种目标。找不到任何非连线输入（或节点没有 ``inputs``）时
    同样 ``None``。
    """
    inputs = node_inputs(node)
    if not inputs:
        return None
    if "image" in inputs:
        return "image" if not is_link(inputs["image"]) else None
    for field_name, value in inputs.items():
        if not is_link(value):
            return field_name
    return None


# ── 参数约束 ──────────────────────────────────────────────────────


def duration_key_targets(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """秒数写目标：正式 ``duration`` 绑定条目优先，绑定表缺条时回退认标题标记。

    与图键同一套约定（:func:`image_key_targets`）：节点标题上的 ``(input:duration)`` 声明的就是
    秒数入口（MiniMax H3 一族的 workflow 普遍用 PrimitiveFloat 节点 + 这种标题挂时长）。能力推导
    （:func:`duration_is_fixed` / :func:`native_duration_literal`）与填值层（``request_builder``
    的 ``_write_duration``）读同一份目标，判「驱不驱动得了」与「写到哪」因此不会各说各话。

    标题标记是「可选增强格」而非独占格：未绑定节点上的字面值在不下发时长时原样保留，不产生破坏
    性改写。绑定表已有 ``duration`` 条目时正式条目优先、不再看标题标记——与导入期信号分级
    「手动绑定优先于标题标记」一致。
    """
    formal = targets_of(bindings.get("duration"))
    if formal:
        return formal
    pseudo: list[Mapping[str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping):
            continue
        if parse_title_marker(node_title(node)) != "duration":
            continue
        field = _duration_field_of(node)
        if field is not None:
            pseudo.append({"node": str(node_id), "input": field})
    return tuple(sorted(pseudo, key=lambda target: node_id_order(target["node"])))


def _duration_field_of(node: Mapping[str, Any]) -> str | None:
    """节点上承载秒数的输入字段：第一个非连线输入（PrimitiveFloat 节点上即 ``value``）。

    该输入接了连线时秒数由上游供给，没有可写落点，写一个数进去等于剪掉那条连线，标题标记不该
    产生这种目标；找不到任何非连线输入（或节点没有 ``inputs``）时同样 ``None``。
    """
    inputs = node_inputs(node)
    if not inputs:
        return None
    for field_name, value in inputs.items():
        if not is_link(value):
            return field_name
    return None


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


def duration_is_fixed(definition: Mapping[str, Any]) -> bool:
    """时长这一维 ArcReel 驱动不动：``frames`` 与 ``duration`` 两条通道都没有写目标。

    时长能被两种语义键驱动，各走各的填值路径：``frames`` 按时长 × 帧率换算成帧数写回，``duration``
    把秒数直写进节点输入。任一条有目标，这一维就是可驱动的——界面放开时长控件、提交时下发时长；
    两条都空才是「workflow 自己定死片长」，控件禁用。``duration`` 通道认「正式绑定 ∪ 标题标记」
    （:func:`duration_key_targets`）：填值层对两种来源写同一处，判「驱不驱动得了」必须与它同口径。

    frames↔fps↔seconds 的那套重建（:func:`native_duration` / :func:`keeps_its_own_frame_count` /
    :func:`frame_rate_is_missing`）特意只认 ``frames``，不把 ``duration`` 塞进去：那套逻辑是「从
    字面帧数 + 帧率反算出一档整秒时长」的逆向换算，整个围着 frames↔fps↔seconds 三者转。``duration``
    是直写秒、根本不参与帧率换算，它的原生档位走 :func:`native_duration_literal` 那条独立的推导线，
    再由 :func:`default_supported_durations` 按序取用。
    """
    workflow: Mapping[str, Any] = definition["workflow"]
    bindings: Mapping[str, Any] = definition["bindings"]
    return not (targets_of(bindings.get("frames")) or duration_key_targets(workflow, bindings))


def frame_rate_is_missing(definition: Mapping[str, Any]) -> bool:
    """``frames`` 绑了，却读不到帧率来源：既无 ``fps`` 只读绑定，也有帧数入口没手填帧率。

    与 :func:`duration_is_fixed` 互斥（那一支连 ``frames`` 都没绑），也与「帧率读得到、只是换算不出
    一档能原样写回的整秒时长」互斥。三支都让 :func:`default_supported_durations` 出空集，但说给用户
    听的话不同：只有这一支缺的是帧率来源本身，界面据此指向补哪里，而不是说这份 workflow 时长天生固定。
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    targets = targets_of(bindings.get("frames"))
    if not targets or bound_fps(workflow, bindings) is not None:
        return False
    return any(positive_number(target.get("fps")) is None for target in targets)


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


def native_duration_literal(definition: Mapping[str, Any]) -> int | None:
    """这份 workflow 的 ``duration`` 入口当前写着的字面秒数（整数），即直写秒通道自己那一档。

    ``duration`` 是直写秒通道：节点里的字面值就是这份 workflow 自己的时长，不需要帧率反算，因此
    这是 :func:`duration_is_fixed` 文档里留下的「另一条推导线」，不借用 :func:`native_duration` 的
    frames↔fps↔seconds 重建。写目标认「正式绑定 ∪ 标题标记」（:func:`duration_key_targets`）。

    档位报「原生」的标准与帧数重建同一尺度：选中之后不改图。每个入口的字面值都得是正整数秒
    （浮点字面值如 16.4 回写成整秒必然改图，同样不报）、多入口字面值一致、且经填值层的步长对齐
    能原样写回（:func:`~arcreel_market_core.comfyui.bindings.align_down` 与 ``request_builder`` 共读）。
    其中一个入口读不出字面值（接的是链接）时同样不报：填值层照样会写它，回写成什么无从判断。
    """
    workflow: Mapping[str, Any] = definition["workflow"]
    bindings: Mapping[str, Any] = definition["bindings"]
    targets = duration_key_targets(workflow, bindings)
    if not targets:
        return None
    literals: list[int] = []
    for target in targets:
        raw = literal_of(workflow, target)
        if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0 or raw != int(raw):
            return None
        seconds = int(raw)
        if align_down(seconds, step_of(target)) != seconds:
            return None
        literals.append(seconds)
    if len(set(literals)) != 1:
        return None
    return literals[0]


def default_supported_durations(definition: Mapping[str, Any]) -> list[int]:
    """ComfyUI 视频模型行的 ``supported_durations`` 默认集：只含这份 workflow 的原生时长。

    两条推导线按序取用：先 frames↔fps↔seconds 重建（:func:`native_duration`），推不出来再取
    ``duration`` 入口的字面秒数（:func:`native_duration_literal`）。不走
    ``duration_presets.infer_supported_durations`` 的模型名启发式：模型行的名字由用户随手填，
    与 workflow 实际能出多长毫无关系（``my-wan-workflow`` 这样的名字在预设表里会命中 ``[4, 8]``）。

    两条线都推不出来时才是空集——``frames`` 未绑、``duration`` 入口也不存在或读不出字面值。空集
    在这条通道上是合法状态而非缺陷（见 ``lib/config/resolver.py`` 对 ``kind: comfyui`` 的分叉）：
    时长这一维不由 ArcReel 驱动，界面禁用时长控件、提交时不下发时长，让 workflow 出它自己那一档。
    """
    native = native_duration(definition)
    if native is not None and native > 0:
        return [native]
    duration_native = native_duration_literal(definition)
    return [] if duration_native is None else [duration_native]
