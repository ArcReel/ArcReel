"""参考生视频路径的三段论渲染：unit 内容 + 资产表 + 能力档 → 发给视频模型的 prompt。

第一段（主体绑定 + 声音声明）与第三段（风格锚定 + 画质/稳定/字幕/水印约束包）由本模块在
渲染期机械生成，不依赖 LLM 自觉。渲染是纯函数、结果不落盘，存量内容无需迁移即获得新渲染。

三段分工：

- **第一段**：``<X>@图片N`` 简式绑定（图片编号 = 随请求发出的参考图顺序）+ 声音声明集中
  声明区（``<X>的台词音色参考 @音频N，声音特征：…``）。听得到声音的 A/B 类均注入声音特征，
  两条无声路径（模型不产音的 C 类、本集关闭音频）都不注入
- **第二段**：单元正文 + 发声记号（``<X>说 {台词}`` / ``画外音说 {台词}``）
- **第三段**：风格锚定 + 画质/稳定/字幕/水印约束包（本路径的反向约束全部由它承担，不另加
  尾词）；两个及以上角色参考图时补双胞胎兜底

所有创作类型的输入均是 unit 正文这一段自由文本，正文只写第二段；主体绑定与参考图顺序都经
``@[X]`` mention 解析派生（:func:`render_unit_prompt`）。文本不含绝对秒数，时长走请求字段。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from lib.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.audio_utils import resolve_audio_ref_path
from lib.prompt_builders import append_product_fidelity_tail
from lib.prompt_utils import normalize_style
from lib.reference_video.script_preview import (
    WARN_UNREGISTERED_MENTION,
    derive_utterances,
    derive_voice_bindings,
)
from lib.reference_video.text_parser import (
    derive_references_from_text,
    extract_mentions,
    render_mentions_as_subjects,
    resolve_references,
    split_speech_line,
)
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_models import ReferenceResource

#: 角色参考音频的项目内固定目录（与上传 / TTS 样本落盘口径一致）。
ASSET_AUDIO_SUBDIR = "characters/refs_audio"

#: 第三段约束包。面向视频模型的提示词文本（非用户可见文案），按仓库口径豁免 i18n。
_QUALITY_PACK = "高清，细节丰富，电影质感，色彩自然，光影柔和。"
_STABILITY_PACK = "人物面部稳定不变形、五官清晰、动作连贯自然，不僵硬，无穿模无卡顿。"
_SUBTITLE_PACK = "保持无字幕，避免生成任何文字或字幕。"
_WATERMARK_PACK = "不要生成水印；不要生成 Logo。"
_NO_BGM_PACK = "禁止出现背景音乐。"
_TWIN_PACK = (
    "视频全程禁止出现外形、着装、配饰完全一致的人物，禁止生成同款分身、"
    "双胞胎效果，同一画面中仅保留单个对应人物，不出现人物重复复刻。"
)


def _character_bucket(project: dict) -> dict[str, Any]:
    """项目角色表，key 已归一到资产名比对坐标系；非 dict（外部编辑写坏的 project.json）按空处理。

    校验器只在该字段本身是 dict 时才校验内部条目（见 ``lib.data_validator``），字段整体
    非 dict 的畸形项目不会被拒绝；本模块多处按名字索引角色表，统一在取值处归一化，
    避免每个调用点各自补一遍 isinstance 判断。

    名字侧的归一同理落在这一处（见 :func:`lib.asset_types.normalize_asset_bucket`）：与角色表
    比对的说话人与 mention 名都出自解析器、已是归一形式，角色表以哪种形式落盘则不可控。
    """
    return normalize_asset_bucket(project.get(BUCKET_KEY["character"]))


@dataclass(frozen=True)
class RenderedUnitPrompt:
    """一个 unit 的渲染产物。

    ``audio_speakers`` 的顺序即 ``@音频N`` 编号，调用方须按同一顺序组装
    ``VideoGenerationRequest.reference_audio_files``——这是 prompt 文本与请求字段之间唯一的
    绑定契约（哪个角色对应哪段音频不进请求）。

    ``audio_speaker_reference_index`` 与 ``audio_speakers`` 等长同序：第 i 项是该 speaker
    对应的 ``reference_images`` 下标（0-based），没有参考图（纯画外角色）时为 None。参考音频
    的顺序（台词 speaker 首现顺序）与参考图的顺序（mention 首现顺序）各自独立派生，backend
    若要求音频逐段挂在具体参考素材项上（``VideoCapabilities.reference_audio_per_image``），
    调用方须按本字段组装 ``VideoGenerationRequest.reference_audio_targets``，不能假设两个
    列表天然同序。
    """

    prompt: str
    audio_speakers: list[str]
    audio_speaker_reference_index: list[int | None] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


def render_unit_prompt(
    text: str,
    project: dict,
    references: list[ReferenceResource],
    settings: VoiceRenderSettings,
    *,
    style: str | None = None,
) -> RenderedUnitPrompt:
    """把一个 unit 的书写文稿渲染成三段论 backend prompt。

    ``references`` 是**实际随请求发出**的参考图列表（已按能力上限裁剪），其顺序即
    ``图片N`` 编号——与 ``reference_images`` 严格等长同序，被裁掉的名字退化为原文不产生
    悬空绑定。参考图顺序由调用方从同一份正文派生（``@mention`` 首现、台词记号的 speaker 位
    不计入），本函数只消费不重算。

    ``settings`` 是渲染所用的声音输入档（见 :class:`~lib.reference_video.voice_settings
    .VoiceRenderSettings`），必填无兜底：这一档决定这一集听不听得到声音，缺省成任何一个方向都是
    替调用方猜——猜有声会给无声项目注入声音特征，漏传时报错才让新调用方在接线阶段就发现。
    ``requires_reference_image`` 为 True 时（backend 要求音频逐段挂在
    具体参考素材项上），纯画外 speaker 不绑定音频（降级 + warning）——绑定后 ``@音频N`` 编号会
    写进 prompt 文本，若随后才在 backend 层过滤会让文本承诺的绑定与实际发出的
    ``reference_audio_files`` 分叉，必须在编号生成前就排除。

    无声路径（``settings.is_silent``）不产出任何音频绑定：``@音频N`` 与「声音特征：…」都不进
    prompt、``audio_speakers`` 为空，调用方组装出的 ``reference_audio_files`` 随之为空。第二段的
    台词渲染不看这一位——无声视频里台词文本照常下发，供应商可用作口型参考。

    warning 与解析预览面板同一批 ``{key, params}`` 条目，由调用方并入任务 ``result.warnings``。
    """
    mentions = extract_mentions(text)
    utterances, warnings = derive_utterances(text)

    # ``references`` 是入参（上游持久化的派生结果），其名字以哪种编码形式落盘不可控；正文一侧
    # 出自解析器、已归一。两侧同形，主体记号与图号才对得上——不归一时该角色的绑定行会缺位、
    # 音频也挂不到图上，且全程不报错。
    references = [ReferenceResource(type=ref.type, name=asset_name_comparison_key(ref.name)) for ref in references]

    registered, missing = resolve_references(mentions, project)
    warnings = [_warning_unregistered(name) for name in missing] + warnings
    # 主体记号按**资产表登记**判定，与参考图编号解耦：被能力上限裁掉的名字仍是画面主体，
    # 只是这次没随请求发图（纯画外角色同理——有主体、无图）。未登记的 mention 才留原文。
    subjects = {ref.name for ref in registered}

    # 音频只能挂到 character 参考图；按类型过滤后建 name → 序号映射。
    character_image_no = {ref.name: i for i, ref in enumerate(references, start=1) if ref.type == "character"}

    characters = _character_bucket(project)
    bindings = derive_voice_bindings(
        utterances,
        characters,
        settings,
        speakers_with_reference_image=set(character_image_no),
    )
    warnings.extend(bindings.warnings)

    audio_no, audio_speaker_reference_index = _number_audio_speakers(bindings.audio_speakers, character_image_no)

    segments = [
        _render_segment_one([ref.name for ref in references], bindings.speakers, audio_no, characters, settings),
        _render_segment_two(text, subjects, characters),
        _render_segment_three(sum(1 for ref in references if ref.type == "character"), style),
    ]
    prompt = "\n\n".join(seg for seg in segments if seg)
    return RenderedUnitPrompt(
        prompt=prompt,
        audio_speakers=list(bindings.audio_speakers),
        audio_speaker_reference_index=audio_speaker_reference_index,
        warnings=warnings,
    )


def render_video_unit_prompt(
    unit: dict,
    project: dict,
    settings: VoiceRenderSettings,
    *,
    request_references: list[ReferenceResource] | None = None,
) -> RenderedUnitPrompt:
    """Render the exact reference-video prompt from one current projected unit."""

    raw_text = unit.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    if not text.strip():
        raise ValueError("reference video unit prompt is empty: text is blank")
    references = request_references
    if references is None:
        references, _missing = derive_references_from_text(text, project)
    rendered = render_unit_prompt(
        text,
        project,
        references,
        settings,
        style=project.get("style"),
    )
    product_names = list(dict.fromkeys(reference.name for reference in references if reference.type == "product"))
    return replace(rendered, prompt=append_product_fidelity_tail(rendered.prompt, product_names))


def _warning_unregistered(name: str) -> dict[str, Any]:
    return {"key": WARN_UNREGISTERED_MENTION, "params": {"name": name}}


def _render_voice_declarations(
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    settings: VoiceRenderSettings,
) -> list[str]:
    """声音声明行：``<X>的台词音色参考 @音频N，声音特征：…``。剧集与 ad 路径共用——两者的
    主体绑定行统一使用 mention 派生的 ``ReferenceResource``；声音声明只认「已登记的
    dialogue speaker」，与主体绑定行解耦，可整段复用。

    两条无声路径（``settings.is_silent``：模型不产音的 C 类、本集关闭音频）都不注入声音声明；
    听得到声音的 A/B 类均注入声音特征——官方建议音色还原不佳时补描述。台词渲染不受影响，
    照常进第二段（见 :func:`_render_segment_two`）。

    角色记录非 dict（外部编辑写坏的 project.json）按无声音特征处理，不索引脏值，保持
    软降级口径而非崩溃。
    """
    if settings.is_silent:
        return []
    lines: list[str] = []
    for name in speakers:
        parts: list[str] = []
        if name in audio_no:
            parts.append(f"台词音色参考 @音频{audio_no[name]}")
        char_data = characters.get(name)
        voice_style = str((char_data.get("voice_style") if isinstance(char_data, dict) else None) or "").strip()
        if voice_style:
            parts.append(f"声音特征：{voice_style}")
        if parts:
            lines.append(f"<{name}>的" + "，".join(parts) + "。")
    return lines


def _number_audio_speakers(
    audio_speakers: list[str], character_image_no: dict[str, int]
) -> tuple[dict[str, int], list[int | None]]:
    """``@音频N`` 编号与「每段音频对应哪张参考图」的下标，两条路径共用同一份派生口径。

    编号即 ``audio_speakers`` 的位置（speaker 首现顺序），也是 ``reference_audio_files``
    的请求字段顺序；下标列表与之等长同序，无参考图的纯画外 speaker 落 None。两者必须在
    同一处派生：分开算会让 prompt 文本里的 ``@音频N`` 与请求字段的下标各自漂移。
    """
    audio_no = {name: i for i, name in enumerate(audio_speakers, start=1)}
    reference_index = [
        (character_image_no[name] - 1) if name in character_image_no else None for name in audio_speakers
    ]
    return audio_no, reference_index


def _render_segment_one(
    labels: list[str],
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    settings: VoiceRenderSettings,
) -> str:
    """主体绑定 + 声音声明。

    官方三段论第一段即参考来源声明区（人脸 / 运镜 / 音色参考同位），故音色参考与声音特征
    集中于此，台词只留统一句式。声明遍历「有台词的已登记角色」而非参考图列表：纯画外角色
    没有参考图（speaker 位不计入参考图派生），但音色声明照常。

    ``labels`` 是 mention 派生的主体记号文本，逐项对应随请求发出的参考图。图号按位置
    直接编号（非名字查表）；空 label 占位不产出绑定行，编号照样前进，以免后续图号与请求
    顺序错位。
    """
    lines: list[str] = []
    bindings = "、".join(f"<{label}>@图片{i}" for i, label in enumerate(labels, start=1) if label)
    if bindings:
        lines.append(bindings + "。")
    lines.extend(_render_voice_declarations(speakers, audio_no, characters, settings))
    return "\n".join(lines)


def _render_segment_two(text: str, subjects: Collection[str], characters: dict) -> str:
    """单元正文段：画面描述做 mention 替换，发声记号就地重组为官方句式。

    ``subjects`` 是已登记的 mention 名（未经能力上限裁剪）——主体记号 ``<X>`` 表达「画面里的
    这个人 / 物」，不指向图号，故与参考图编号解耦：裁掉图的名字照样是主体，只有未登记的
    mention 才留编辑器原文（配 ``ref_warn_unregistered_mention``）。

    正文逐行原样渲染，不再重排或加分段前缀：正文是作者写下的唯一真相，行文顺序即传达顺序。
    记号可写在行内任意位置，重组按位置就地替换、描述部分留在原处。说话人按**资产表**判定
    而非参考图列表：纯画外角色无参考图，台词照常重组。未登记的说话人按原文发送（warning 已由
    :func:`derive_voice_bindings` 发出），未被识别成记号的花括号同样原样发送——不做剥除，
    作者能在成片里看见自己写坏的那一段。
    """
    lines: list[str] = []
    for line in text.splitlines():
        pieces: list[str] = []
        for part in split_speech_line(line):
            if isinstance(part, str):
                pieces.append(render_mentions_as_subjects(part, subjects))
            elif not part.speaker:
                pieces.append(f"画外音说 {{{part.text}}}")
            elif part.speaker in characters:
                pieces.append(f"<{part.speaker}>说 {{{part.text}}}")
            else:
                pieces.append(render_mentions_as_subjects(part.raw, subjects))
        lines.append("".join(pieces))
    return "\n".join(line for line in lines if line.strip())


def _render_segment_three(character_reference_count: int, style: str | None) -> str:
    """风格锚定 + 画质/稳定/字幕/水印约束包；两个及以上角色参考图时补双胞胎兜底。"""
    lines: list[str] = []
    normalized = normalize_style(style)
    if normalized:
        lines.append(f"整体视觉风格：{normalized}。")
    lines.append(_QUALITY_PACK + _STABILITY_PACK)
    lines.append(_SUBTITLE_PACK + _WATERMARK_PACK + _NO_BGM_PACK)
    if character_reference_count >= 2:
        lines.append(_TWIN_PACK)
    return "\n".join(lines)


def resolve_reference_audio_paths(project: dict, project_path: Path) -> dict[str, Path]:
    """项目内「参考音频确实可用」的角色 → 绝对路径映射。

    只收录字段指向 ``characters/refs_audio`` 内且文件确实存在的条目（越界路径由
    :func:`lib.audio_utils.resolve_audio_ref_path` 挡下——该字段可经资产 PATCH 写成项目内
    任意字符串）。渲染层据此判定绑定，编号与实际发出的音频段数因此严格等长。

    key 是归一后的角色名（``_character_bucket`` 已归一）：本映射作为 ``audio_ready`` 传给
    :func:`lib.reference_video.script_preview.derive_voice_bindings` 与说话人判等，两侧同形
    才不会把「音频确实可用」误判成「不可用」而静默不绑。
    """
    audio_refs_dir = project_path / ASSET_AUDIO_SUBDIR
    resolved: dict[str, Path] = {}
    for name, item in _character_bucket(project).items():
        if not isinstance(item, dict):
            continue
        path = resolve_audio_ref_path(project_path, audio_refs_dir, item.get("reference_audio"))
        if path is not None and path.exists():
            resolved[name] = path
    return resolved
