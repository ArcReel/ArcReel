"""剧集参考路径的三段论渲染：书写文稿 + 资产表 + 能力档 → 发给视频模型的 prompt。

书写层只写第二段（``镜头N：`` 分镜段，首个 header 之前的开场定调折进镜头 1）；第一段
（主体绑定 + 声音声明）与第三段（风格锚定 + 画质/稳定/字幕/水印约束包）由本模块在渲染期
机械生成，不依赖 LLM 自觉。渲染是纯函数、结果不落盘，存量文稿无需迁移即获得新渲染。

三段分工：

- **第一段**：``<X>@图片N`` 简式绑定（图片编号 = ``references`` 顺序）+ 声音声明集中声明区
  （``<X>的台词音色参考 @音频N，声音特征：…``）。A/B 类均注入声音特征，C 类不注入
- **第二段**：``镜头N：`` + 描述行（``@[X]`` → ``<X>``）+ 台词行（``<X>说 {台词}`` /
  ``画外音说 {台词}``）
- **第三段**：风格锚定 + 画质/稳定/字幕/水印约束包（本路径的反向约束全部由它承担，不另加
  尾词）；两个及以上角色参考图时补双胞胎兜底

发给模型的文本不含绝对秒数（时长走请求字段），参考图指认全部由第一段的 ``<X>@图片N`` 绑定承担，
无独立对照表。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.audio_utils import resolve_audio_ref_path
from lib.prompt_utils import normalize_style
from lib.reference_video.script_preview import (
    WARN_UNREGISTERED_MENTION,
    derive_utterances,
    derive_voice_bindings,
)
from lib.reference_video.shot_parser import (
    match_dialogue_line,
    match_voiceover_line,
    parse_prompt,
    render_mentions_as_subjects,
    resolve_references,
)
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
    *,
    voice_consistency: str = "soft",
    max_reference_audio: int = 0,
    model_id: str = "",
    style: str | None = None,
    audio_ready: Collection[str] | None = None,
    audio_requires_reference_image: bool = False,
) -> RenderedUnitPrompt:
    """把一个 unit 的书写文稿渲染成三段论 backend prompt。

    ``references`` 是**本次实际随请求发出**的参考图列表（已按能力上限裁剪），其顺序即
    ``图片N`` 编号——与 ``reference_images`` 严格等长同序，被裁掉的名字退化为原文不产生
    悬空绑定。文稿派生出的参考图顺序（``@mention`` 首现、规范台词行的 speaker 位不计入）
    由上游持久化，本函数只消费不重算。

    ``audio_requires_reference_image`` 为 True 时（backend 要求音频逐段挂在具体参考素材项
    上），纯画外 speaker 不绑定音频（降级 + warning）——绑定后 ``@音频N`` 编号会写进 prompt
    文本，若随后才在 backend 层过滤会让文本承诺的绑定与实际发出的 ``reference_audio_files``
    分叉，必须在编号生成前就排除。

    warning 与解析预览面板同一批 ``{key, params}`` 条目，由调用方并入任务 ``result.warnings``。
    """
    shots, mentions = parse_prompt(text)
    utterances, warnings = derive_utterances(shots)

    registered, missing = resolve_references(mentions, project)
    warnings = [_warning_unregistered(name) for name in missing] + warnings
    # 主体记号按**资产表登记**判定，与参考图编号解耦：被能力上限裁掉的名字仍是画面主体，
    # 只是这次没随请求发图（纯画外角色同理——有主体、无图）。未登记的 mention 才留原文。
    subjects = {ref.name for ref in registered}

    image_no = {ref.name: i for i, ref in enumerate(references, start=1)}

    characters: dict = project.get(BUCKET_KEY["character"]) or {}
    bindings = derive_voice_bindings(
        utterances,
        characters,
        voice_consistency=voice_consistency,
        max_reference_audio=max_reference_audio,
        model_id=model_id,
        audio_ready=audio_ready,
        require_reference_image=audio_requires_reference_image,
        speakers_with_reference_image=image_no.keys(),
    )
    warnings.extend(bindings.warnings)

    audio_no = {name: i for i, name in enumerate(bindings.audio_speakers, start=1)}
    audio_speaker_reference_index = [
        (image_no[name] - 1) if name in image_no else None for name in bindings.audio_speakers
    ]

    segments = [
        _render_segment_one(references, image_no, bindings.speakers, audio_no, characters, voice_consistency),
        _render_segment_two(shots, subjects, characters),
        _render_segment_three(references, style),
    ]
    prompt = "\n\n".join(seg for seg in segments if seg)
    return RenderedUnitPrompt(
        prompt=prompt,
        audio_speakers=list(bindings.audio_speakers),
        audio_speaker_reference_index=audio_speaker_reference_index,
        warnings=warnings,
    )


def _warning_unregistered(name: str) -> dict[str, Any]:
    return {"key": WARN_UNREGISTERED_MENTION, "params": {"name": name}}


def _render_segment_one(
    references: list[ReferenceResource],
    image_no: dict[str, int],
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    voice_consistency: str,
) -> str:
    """主体绑定 + 声音声明。

    官方三段论第一段即参考来源声明区（人脸 / 运镜 / 音色参考同位），故音色参考与声音特征
    集中于此，台词行只留统一句式。声明遍历「有台词的已登记角色」而非参考图列表：纯画外角色
    没有参考图（speaker 位不计入参考图派生），但音色声明照常。
    """
    lines: list[str] = []
    bindings = "、".join(f"<{ref.name}>@图片{image_no[ref.name]}" for ref in references)
    if bindings:
        lines.append(bindings + "。")

    # C 类（真无声）不注入声音声明；A/B 类均注入声音特征——官方建议音色还原不佳时补描述。
    if voice_consistency == "none":
        return "\n".join(lines)

    for name in speakers:
        parts: list[str] = []
        if name in audio_no:
            parts.append(f"台词音色参考 @音频{audio_no[name]}")
        voice_style = str((characters.get(name) or {}).get("voice_style") or "").strip()
        if voice_style:
            parts.append(f"声音特征：{voice_style}")
        if parts:
            lines.append(f"<{name}>的" + "，".join(parts) + "。")
    return "\n".join(lines)


def _render_segment_two(shots: list[Any], subjects: Collection[str], characters: dict) -> str:
    """镜头分镜段：描述行做 mention 替换，规范台词行重组为官方句式。

    ``subjects`` 是已登记的 mention 名（未经能力上限裁剪）——主体记号 ``<X>`` 表达「画面里的
    这个人 / 物」，不指向图号，故与参考图编号解耦：裁掉图的名字照样是主体，只有未登记的
    mention 才留编辑器原文（配 ``ref_warn_unregistered_mention``）。

    台词行的说话人按**资产表**判定而非参考图列表：纯画外角色无参考图，台词行照常重组。
    未登记的说话人按原文发送（warning 已由 :func:`derive_voice_bindings` 发出），
    未闭合花括号行同样原样发送——不做剥除，作者能在成片里看见自己写坏的那一行。
    """
    blocks: list[str] = []
    for index, shot in enumerate(shots, start=1):
        body: list[str] = []
        for line in shot.text.splitlines():
            dialogue = match_dialogue_line(line)
            if dialogue is not None and dialogue[0] in characters:
                body.append(f"<{dialogue[0]}>说 {{{dialogue[1]}}}")
                continue
            voiceover = match_voiceover_line(line)
            if voiceover is not None:
                body.append(f"画外音说 {{{voiceover}}}")
                continue
            body.append(render_mentions_as_subjects(line, subjects))
        text = "\n".join(ln for ln in body if ln.strip())
        blocks.append(f"镜头{index}：\n{text}" if text else f"镜头{index}：")
    return "\n\n".join(blocks)


def _render_segment_three(references: list[ReferenceResource], style: str | None) -> str:
    """风格锚定 + 画质/稳定/字幕/水印约束包；两个及以上角色参考图时补双胞胎兜底。"""
    lines: list[str] = []
    normalized = normalize_style(style)
    if normalized:
        lines.append(f"整体视觉风格：{normalized}。")
    lines.append(_QUALITY_PACK + _STABILITY_PACK)
    lines.append(_SUBTITLE_PACK + _WATERMARK_PACK + _NO_BGM_PACK)
    if sum(1 for ref in references if ref.type == "character") >= 2:
        lines.append(_TWIN_PACK)
    return "\n".join(lines)


def resolve_reference_audio_paths(project: dict, project_path: Path) -> dict[str, Path]:
    """项目内「参考音频确实可用」的角色 → 绝对路径映射。

    只收录字段指向 ``characters/refs_audio`` 内且文件确实存在的条目（越界路径由
    :func:`lib.audio_utils.resolve_audio_ref_path` 挡下——该字段可经资产 PATCH 写成项目内
    任意字符串）。渲染层据此判定绑定，编号与实际发出的音频段数因此严格等长。
    """
    audio_refs_dir = project_path / ASSET_AUDIO_SUBDIR
    resolved: dict[str, Path] = {}
    for name, item in (project.get(BUCKET_KEY["character"]) or {}).items():
        if not isinstance(item, dict):
            continue
        path = resolve_audio_ref_path(project_path, audio_refs_dir, item.get("reference_audio"))
        if path is not None and path.exists():
            resolved[name] = path
    return resolved
