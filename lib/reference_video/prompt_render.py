"""参考生视频路径的三段论渲染：unit 内容 + 资产表 + 能力档 → 发给视频模型的 prompt。

第一段（主体绑定 + 声音声明）与第三段（风格锚定 + 画质/稳定/字幕/水印约束包）由本模块在
渲染期机械生成，不依赖 LLM 自觉。渲染是纯函数、结果不落盘，存量内容无需迁移即获得新渲染。

三段分工：

- **第一段**：``<X>@图片N`` 简式绑定（图片编号 = 随请求发出的参考图顺序）+ 声音声明集中
  声明区（``<X>的台词音色参考 @音频N，声音特征：…``）。A/B 类均注入声音特征，C 类不注入
- **第二段**：镜头分镜段 + 台词行（``<X>说 {台词}`` / ``画外音说 {台词}``）
- **第三段**：风格锚定 + 画质/稳定/字幕/水印约束包（本路径的反向约束全部由它承担，不另加
  尾词）；两个及以上角色参考图时补双胞胎兜底

narration/drama 与 ad 共用第一、三段与音频接线，第二段各自成文：narration/drama 的输入是
书写层自由文本，书写层只写第二段（``镜头N：`` 分镜段，首个 header 之前的开场定调折进镜头 1），
主体绑定经 ``@[X]`` mention 解析派生（:func:`render_unit_prompt`）；ad 的输入是结构化镜头
字段，主体记号取参考条目的展示 label、台词取 ``video_prompt.dialogue``
（:func:`render_ad_backend_prompt`）。

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
from lib.reference_video.ad_units import render_ad_unit_prompt
from lib.reference_video.script_preview import (
    WARN_UNREGISTERED_MENTION,
    ShotUtterance,
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
from lib.script_models import ReferenceResource, Utterance

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

    # 音频只能对齐到「同名且类型也是 character」的图：resolve_references 按
    # character → scene → prop 的优先级分派，故场景/道具可能与某个角色同名——名字键的字典
    # 若不先按类型过滤再建，两个同名的不同类型条目会互相覆盖（dict 同键取最后写入的那条），
    # 导致编号指向错误的图。先过滤类型再建 name → 序号映射，从根上避免该覆盖。
    character_image_no = {ref.name: i for i, ref in enumerate(references, start=1) if ref.type == "character"}

    characters: dict = project.get(BUCKET_KEY["character"]) or {}
    bindings = derive_voice_bindings(
        utterances,
        characters,
        voice_consistency=voice_consistency,
        max_reference_audio=max_reference_audio,
        model_id=model_id,
        audio_ready=audio_ready,
        require_reference_image=audio_requires_reference_image,
        speakers_with_reference_image=set(character_image_no),
    )
    warnings.extend(bindings.warnings)

    audio_no, audio_speaker_reference_index = _number_audio_speakers(bindings.audio_speakers, character_image_no)

    segments = [
        _render_segment_one(
            [ref.name for ref in references], bindings.speakers, audio_no, characters, voice_consistency
        ),
        _render_segment_two(shots, subjects, characters),
        _render_segment_three(sum(1 for ref in references if ref.type == "character"), style),
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


def _render_voice_declarations(
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    voice_consistency: str,
) -> list[str]:
    """声音声明行：``<X>的台词音色参考 @音频N，声音特征：…``。剧集与 ad 路径共用——两者的
    主体绑定行输入形态不同（前者是 mention 派生的 ``ReferenceResource``、后者是 ad 参考条目
    的展示 label），但声音声明只认「已登记的 dialogue speaker」，与主体绑定行解耦，可整段复用。

    C 类（真无声）不注入声音声明；A/B 类均注入声音特征——官方建议音色还原不佳时补描述。
    """
    if voice_consistency == "none":
        return []
    lines: list[str] = []
    for name in speakers:
        parts: list[str] = []
        if name in audio_no:
            parts.append(f"台词音色参考 @音频{audio_no[name]}")
        voice_style = str((characters.get(name) or {}).get("voice_style") or "").strip()
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
    voice_consistency: str,
) -> str:
    """主体绑定 + 声音声明。

    官方三段论第一段即参考来源声明区（人脸 / 运镜 / 音色参考同位），故音色参考与声音特征
    集中于此，台词行只留统一句式。声明遍历「有台词的已登记角色」而非参考图列表：纯画外角色
    没有参考图（speaker 位不计入参考图派生），但音色声明照常。

    ``labels`` 是主体记号文本，逐项对应本次随请求发出的参考图（narration/drama 传 mention
    派生的资产名，ad 传参考条目的展示 label）。图号按位置直接编号（非名字查表）：不同类型
    的资产允许同名（见 ``render_unit_prompt`` 的 ``character_image_no`` 注释），名字键的字典
    会把两个同名条目的图号互相覆盖，位置编号天然不受影响；空 label 占位不产出绑定行，编号
    照样前进，以免后续图号与请求顺序错位。
    """
    lines: list[str] = []
    bindings = "、".join(f"<{label}>@图片{i}" for i, label in enumerate(labels, start=1) if label)
    if bindings:
        lines.append(bindings + "。")
    lines.extend(_render_voice_declarations(speakers, audio_no, characters, voice_consistency))
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


def _derive_ad_utterances(shots: list[dict]) -> list[ShotUtterance]:
    """ad 结构化镜头的台词 utterances：``video_prompt.dialogue`` 已是判别式结构
    （``{"speaker": ..., "line": ...}``），不必像书写文稿一样经 ``parse_prompt`` /
    ``match_dialogue_line`` 正则识别自由文本。``voiceover_text`` 是独立字段、不产出台词行
    （与画面 prompt 的口径一致，见 ``lib.reference_video.ad_units._shot_prompt_text``）。有
    speaker 派生 ``dialogue`` utterance,无 speaker 的裸台词派生 ``voiceover`` utterance——与
    该镜头在画面 prompt 里渲染的 ``画外音说 {台词}`` 句式对应,使 ``derive_voice_bindings`` 的
    无声知会（``voice_consistency == "none"``）同样覆盖纯画外的 ad 台词。调用方传入的 ``shots``
    已由 ``resolve_ad_unit_shots`` 水合、保证逐项为 dict；脏 ``video_prompt`` / dialogue 条目
    （非 dict、非字符串 speaker 或 line）按空处理，与 ``_shot_prompt_text`` 的字符串专一
    强制口径一致，避免同一条脏数据在两处产生不一致的渲染结果。
    """
    utterances: list[ShotUtterance] = []
    for index, shot in enumerate(shots, start=1):
        video_prompt = shot.get("video_prompt")
        dialogue = video_prompt.get("dialogue") if isinstance(video_prompt, dict) else None
        if not isinstance(dialogue, list):
            continue
        for entry in dialogue:
            if not isinstance(entry, dict):
                continue
            raw_speaker = entry.get("speaker")
            raw_text = entry.get("line")
            speaker = raw_speaker.strip() if isinstance(raw_speaker, str) else ""
            text = raw_text.strip() if isinstance(raw_text, str) else ""
            if not text:
                continue
            if speaker:
                utterances.append(ShotUtterance(index, Utterance(kind="dialogue", speaker=speaker, text=text)))
            else:
                utterances.append(ShotUtterance(index, Utterance(kind="voiceover", text=text)))
    return utterances


def render_ad_backend_prompt(
    shots: list[dict],
    entries: list[dict],
    project: dict,
    *,
    voice_consistency: str = "soft",
    max_reference_audio: int = 0,
    model_id: str = "",
    style: str | None = None,
    audio_ready: Collection[str] | None = None,
    audio_requires_reference_image: bool = False,
) -> RenderedUnitPrompt:
    """把 ad 派生 unit 的结构化镜头渲染成三段论 backend prompt，与剧集路径
    （:func:`render_unit_prompt`）共用第一、三段与音频接线；差异只在输入形态——ad 无书写层
    自由文本，第一段的主体记号直接取 ``entries`` 的展示 label（产品区分 sheet / 原图两态，
    资产统一「设计图」态）而不引入 mention 语法：ad 镜头字段本就结构化枚举了画面主体
    （``characters_in_shot`` / ``products_in_shot`` / ...），无需从自由文本解析 ``@[X]``；
    dialogue 是结构化 ``video_prompt.dialogue`` 列表而非 ``@[角色]：{台词}`` 文本行，不经
    ``parse_prompt``（见 :func:`_derive_ad_utterances`）。

    第二段由 ``lib.reference_video.ad_units.render_ad_unit_prompt`` 渲染（``Shot N (Xs): ...``
    逐镜头行，时长挂在 shot 上——ad 的 unit 是派生分组，与 narration/drama 的 unit 级单时长
    不同源）；该函数同时独立服务于入队守卫的空提示词检查。本函数只接管第一、三段与音频接线，
    风格锚定因此从镜头段头行移到第三段，故此处按 ``style=None`` 调用。

    ``entries`` 是本次实际随请求发出的参考条目（已按能力上限裁剪），其顺序即 ``图片N``
    编号——与调用方组装的 ``reference_images`` 严格等长同序。

    Raises:
        ValueError: 成员镜头全无画面内容（第二段渲染为空）——同
            :func:`lib.reference_video.ad_units.render_ad_unit_prompt` 的口径，防止机器生成的
            第一/三段把空提示词撑成非空文本绕过 backend 的空值保护。
    """
    body = render_ad_unit_prompt(shots, style=None)
    if not body.strip():
        raise ValueError("reference video unit prompt is empty: all member shots have no visual content")

    characters: dict = project.get(BUCKET_KEY["character"]) or {}
    utterances = _derive_ad_utterances(shots)

    # 音频只能对齐到「同名且类型也是 character」的图：entries 里 character/scene/prop 的设计图
    # 共用 kind == "asset"，三类资产允许重名，只有 asset_type 能把角色的图与同名场景/道具的图
    # 分开——按名字判定会让同名场景的图被当成角色的图（并在名字键的字典里互相覆盖），
    # 使 speakers_with_reference_image 误判、reference_audio_targets 指向错图（与剧集路径
    # `character_image_no` 的同款过滤同一理由，见 `render_unit_prompt`）。
    character_image_no = {
        str(e["name"]): i
        for i, e in enumerate(entries, start=1)
        if e.get("asset_type") == "character" and isinstance(e.get("name"), str)
    }

    bindings = derive_voice_bindings(
        utterances,
        characters,
        voice_consistency=voice_consistency,
        max_reference_audio=max_reference_audio,
        model_id=model_id,
        audio_ready=audio_ready,
        require_reference_image=audio_requires_reference_image,
        speakers_with_reference_image=set(character_image_no),
    )

    audio_no, audio_speaker_reference_index = _number_audio_speakers(bindings.audio_speakers, character_image_no)

    segments = [
        _render_segment_one(
            [str(e.get("label") or "") for e in entries],
            bindings.speakers,
            audio_no,
            characters,
            voice_consistency,
        ),
        body,
        _render_segment_three(len(character_image_no), style),
    ]
    prompt = "\n\n".join(seg for seg in segments if seg)
    return RenderedUnitPrompt(
        prompt=prompt,
        audio_speakers=list(bindings.audio_speakers),
        audio_speaker_reference_index=audio_speaker_reference_index,
        warnings=list(bindings.warnings),
    )


def resolve_reference_audio_paths(project: dict, project_path: Path) -> dict[str, Path]:
    """项目内「参考音频确实可用」的角色 → 绝对路径映射。

    只收录字段指向 ``characters/refs_audio`` 内且文件确实存在的条目（越界路径由
    :func:`lib.audio_utils.resolve_audio_ref_path` 挡下——该字段可经资产 PATCH 写成项目内
    任意字符串）。渲染层据此判定绑定，编号与实际发出的音频段数因此严格等长。角色桶非 dict
    （如外部编辑/迁移写坏的 project.json）按空处理而非硬失败——校验器不拒绝该形态
    （见 ``lib.data_validator``），executor 不应因它崩溃。
    """
    audio_refs_dir = project_path / ASSET_AUDIO_SUBDIR
    resolved: dict[str, Path] = {}
    raw_characters = project.get(BUCKET_KEY["character"])
    characters = raw_characters if isinstance(raw_characters, dict) else {}
    for name, item in characters.items():
        if not isinstance(item, dict):
            continue
        path = resolve_audio_ref_path(project_path, audio_refs_dir, item.get("reference_audio"))
        if path is not None and path.exists():
            resolved[name] = path
    return resolved
