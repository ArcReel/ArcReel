"""参考生视频路径的 H3 渲染：unit 内容 + 资产表 + 能力档 → 发给视频模型的 prompt。

按 MiniMax H3 的参考生视频（Ref2VA）六段式产出，整段提示词由内置模版 ``reference_video/unit``
渲染，各段槽位值由本模块在渲染期机械生成，不依赖 LLM 自觉。渲染是纯函数、结果不落盘，存量
内容无需迁移即获得新渲染。

有参考图时的六段分工：

- **subject_definitions**：逐主体声明 ``<Subject N> is the {type} {名称}, shown in <Picture N>``
  （N = 随请求发出的参考图顺序，同一主体多张参考图全部列出）+ 同一角色的形态声明 + 声音声明
  （``<Subject N>的台词音色参考 @音频N，只取音色、语速与情绪，台词以正文为准，声音特征：…``）。
  听得到声音的 A/B 类均注入声音特征，两条无声路径（模型不产音的 C 类、本集关闭音频）都不注入。
- **summary**：``[reference generation]`` 任务类型 + 参考主体概述。
- **retention_analysis**：逐主体 ``fully_preserved``——参考图即该主体外观的真相，全部保留。
- **detailed_description**：单元正文，``@[名称]`` 换成 ``<Subject N>``、角色台词记号重组成
  ``<Subject N>说 {台词}``；无归属旁白（裸 ``{台词}``）不下发视频模型，由 TTS / 后期配音承担。
- **overall_soundscape**：环境 / 物理动作声概述（指向 detailed_description 写到的声音，不断言静音）。
- **non_diegetic_music**：``N/A``——本路径不加配乐，负向约束由 ``Avoid`` 行承担。

无参考图的单元退为基础三字段（``integrated_multimodal_description`` + ``overall_soundscape`` +
``non_diegetic_music``），不产出 subject_definitions / summary / retention_analysis。末尾始终保留
共享风格块 + ``Avoid`` 行（视频负向提示词）；画面里有两个及以上不同角色时追加分身或双胞胎排除项，
带商品参考图时其后再接商品高保真声明。

所有创作类型的输入均是 unit 正文这一段自由文本；主体绑定与参考图顺序都经 ``@[X]`` mention 解析
派生（:func:`render_unit_prompt`）。文本不含绝对秒数，时长走请求字段。
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.project.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.reference_video.script_preview import (
    WARN_UNREGISTERED_MENTION,
    derive_utterances,
    derive_voice_bindings,
)
from lib.script.reference_video.text_parser import (
    SpeechMark,
    derive_references_from_text,
    extract_mentions,
    render_mentions_with_label,
    resolve_references,
    split_speech_line,
)
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource
from lib.speech.audio_utils import resolve_audio_ref_path

#: 角色参考音频的项目内固定目录（与上传 / TTS 样本落盘口径一致）。
ASSET_AUDIO_SUBDIR = "characters/refs_audio"


def _character_bucket(project: dict) -> dict[str, Any]:
    """项目角色表，key 已归一到资产名比对坐标系；非 dict（外部编辑写坏的 project.json）按空处理。

    校验器只在该字段本身是 dict 时才校验内部条目（见 ``lib.project.data_validator``），字段整体
    非 dict 的畸形项目不会被拒绝；本模块多处按名字索引角色表，统一在取值处归一化，
    避免每个调用点各自补一遍 isinstance 判断。

    名字侧的归一同理落在这一处（见 :func:`lib.project.asset_types.normalize_asset_bucket`）：与角色表
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
) -> RenderedUnitPrompt:
    """把一个 unit 的书写文稿渲染成 H3 参考生视频 backend prompt。

    有参考图时产出 Ref2VA 六段（subject_definitions / summary / retention_analysis /
    detailed_description / overall_soundscape / non_diegetic_music）；无参考图时退为基础三字段
    （integrated_multimodal_description / overall_soundscape / non_diegetic_music）。

    ``references`` 是**实际随请求发出**的参考图列表（已按能力上限裁剪），其顺序即 ``<Picture N>``
    编号——与 ``reference_images`` 严格等长同序，被裁掉的名字退化为名字记号不产生悬空绑定。
    参考图顺序由调用方从同一份正文派生（``@mention`` 首现、台词记号的 speaker 位不计入），
    本函数只消费不重算。

    ``settings`` 是渲染所用的声音输入档（见 :class:`~lib.script.reference_video.voice_settings
    .VoiceRenderSettings`），必填无兜底：这一档决定这一集听不听得到声音，缺省成任何一个方向都是
    替调用方猜。``requires_reference_image`` 为 True 时（backend 要求音频逐段挂在具体参考素材项上），
    纯画外 speaker 不绑定音频（降级 + warning）——绑定后 ``@音频N`` 编号会写进 prompt 文本，若随后才在
    backend 层过滤会让文本承诺的绑定与实际发出的 ``reference_audio_files`` 分叉，必须在编号生成前排除。

    无声路径（``settings.is_silent``）不产出任何音频绑定：``@音频N`` 与「声音特征：…」都不进
    subject_definitions、``audio_speakers`` 为空，调用方组装出的 ``reference_audio_files`` 随之为空。
    detailed_description 的台词渲染不看这一位——无声视频里台词文本照常下发，供应商可用作口型参考。

    warning 与解析预览面板同一批 ``{key, params}`` 条目，由调用方并入任务 ``result.warnings``。

    风格块取 ``project`` 的 ``style`` 与 ``style_description``。分身排除项按正文中已登记的完整角色
    列表判断，不随供应商参考图上限裁剪；同一角色的本体与衍生仍合并计一个（见 ``docs/adr/0072``）。
    商品高保真声明只在商品参考图实际随请求发出时注入。
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
    catalog = build_reference_catalog(project)
    bindings = derive_voice_bindings(
        utterances,
        characters,
        settings,
        speakers_with_reference_image=set(character_image_no),
    )
    warnings.extend(bindings.warnings)

    audio_no, audio_speaker_reference_index = _number_audio_speakers(bindings.audio_speakers, character_image_no)

    request_character_forms = _character_forms_by_entity(references, catalog)
    co_present_characters = list(_character_forms_by_entity(registered, catalog))

    # H3 主体映射：每个随请求发出的参考图主体一个 ``<Subject N>``（N = 参考图首现顺序），
    # ``<Picture N>`` 与 references 下标严格对应；被能力上限裁掉的名字无图，正文里退为名字记号。
    subject_order = _distinct_subject_order(references)
    subject_label = {name: f"<Subject {i}>" for i, name in enumerate(subject_order, start=1)}
    subject_type = {ref.name: ref.type for ref in references if ref.name}
    subject_pictures = _subject_pictures(references)

    body = _render_body(text, _body_label(subject_label, subjects), characters, catalog)
    if references:
        subject_definitions = _render_subject_definitions(
            subject_order,
            subject_label,
            subject_type,
            subject_pictures,
            request_character_forms,
            bindings.speakers,
            audio_no,
            characters,
            settings,
        )
        summary = _render_summary(subject_label, subject_order)
        retention_analysis = _render_retention_analysis(subject_label, subject_order)
        body_label = "detailed_description"
    else:
        # 无参考图退为基础三字段：正文即 integrated_multimodal_description，不产出前两段。
        subject_definitions = ""
        summary = ""
        retention_analysis = ""
        body_label = "integrated_multimodal_description"

    prompt = builtin_templates.render(
        "reference_video/unit",
        subject_definitions=subject_definitions,
        summary=summary,
        retention_analysis=retention_analysis,
        body_label=body_label,
        body=body,
        overall_soundscape=_OVERALL_SOUNDSCAPE,
        non_diegetic_music="N/A",
        style=normalize_style_value(project.get("style")),
        style_description=normalize_style_value(project.get("style_description")),
        co_present_characters=co_present_characters if len(co_present_characters) >= 2 else [],
        products=list(dict.fromkeys(ref.name for ref in references if ref.type == "product" and ref.name)),
    )
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
    return render_unit_prompt(text, project, references, settings)


def _warning_unregistered(name: str) -> dict[str, Any]:
    return {"key": WARN_UNREGISTERED_MENTION, "params": {"name": name}}


#: H3 overall_soundscape 段的固定措辞：指向 detailed_description 写到的环境 / 物理声，不断言静音
#: （N/A 仅用于明确要求完全无声的场景，本路径默认有环境声）。
_OVERALL_SOUNDSCAPE = (
    "Scene-appropriate ambient and physical action sounds as implied by the detailed description above."
)

#: 参考主体在 subject_definitions 里的类型词（英文，与 H3 措辞一致）。
_H3_SUBJECT_TYPE_WORDS = {
    "character": "character",
    "scene": "scene",
    "prop": "prop",
    "product": "product",
}


def _distinct_subject_order(references: list[ReferenceResource]) -> list[str]:
    """references 里出现过的名字，按首现顺序去重——每个名字一个 H3 主体（``<Subject N>``）。"""
    order: list[str] = []
    for ref in references:
        if ref.name and ref.name not in order:
            order.append(ref.name)
    return order


def _subject_pictures(references: list[ReferenceResource]) -> dict[str, list[int]]:
    """每个主体对应的参考图位置（1-based，随 references 顺序）；同一主体多张参考图全部列出。"""
    pictures: dict[str, list[int]] = {}
    for i, ref in enumerate(references, start=1):
        if ref.name:
            pictures.setdefault(ref.name, []).append(i)
    return pictures


def _body_label(subject_label: dict[str, str], subjects: Collection[str]) -> Callable[[str], str | None]:
    """正文 mention 的标签函数：有参考图 → ``<Subject N>``；已登记但被裁掉 → ``<名称>``；未登记 → 留原文。"""

    def label(name: str) -> str | None:
        if name in subject_label:
            return subject_label[name]
        if name in subjects:
            return f"<{name}>"
        return None

    return label


def _render_voice_declarations(
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    settings: VoiceRenderSettings,
    subject_label: dict[str, str],
) -> list[str]:
    """声音声明行：``<Subject N>的台词音色参考 @音频N，只取音色、语速与情绪，台词以正文为准，声音特征：…``。

    剧集与 ad 路径共用——声音声明只认「已登记的 dialogue speaker」，与主体声明解耦，可整段复用。
    两条无声路径（``settings.is_silent``）都不注入；听得到声音的 A/B 类均注入声音特征。角色记录非
    dict（外部编辑写坏的 project.json）按无声音特征处理。说话人没有参考图（纯画外角色）时退为名字
    记号 ``<名称>``。
    """
    if settings.is_silent:
        return []
    lines: list[str] = []
    for name in speakers:
        parts: list[str] = []
        if name in audio_no:
            parts.append(f"台词音色参考 @音频{audio_no[name]}，只取音色、语速与情绪，台词以正文为准")
        char_data = characters.get(name)
        voice_style = str((char_data.get("voice_style") if isinstance(char_data, dict) else None) or "").strip()
        if voice_style:
            parts.append(f"声音特征：{voice_style}")
        if parts:
            lines.append(f"{subject_label.get(name, f'<{name}>')}的" + "，".join(parts) + "。")
    return lines


def _number_audio_speakers(
    audio_speakers: list[str], character_image_no: dict[str, int]
) -> tuple[dict[str, int], list[int | None]]:
    """``@音频N`` 编号与「每段音频对应哪张参考图」的下标，两条路径共用同一份派生口径。

    编号即 ``audio_speakers`` 的位置（speaker 首现顺序），也是 ``reference_audio_files`` 的
    请求字段顺序；下标列表与之等长同序，无参考图的纯画外 speaker 落 None。两者必须在
    同一处派生：分开算会让 prompt 文本里的 ``@音频N`` 与请求字段的下标各自漂移。
    """
    audio_no = {name: i for i, name in enumerate(audio_speakers, start=1)}
    reference_index = [
        (character_image_no[name] - 1) if name in character_image_no else None for name in audio_speakers
    ]
    return audio_no, reference_index


def _render_subject_definitions(
    subject_order: list[str],
    subject_label: dict[str, str],
    subject_type: dict[str, str],
    subject_pictures: dict[str, list[int]],
    character_forms: dict[str, list[str]],
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    settings: VoiceRenderSettings,
) -> str:
    """H3 subject_definitions 段：逐主体声明 + 同一角色形态声明 + 声音声明。

    主体行把 ``<Subject N>`` 与参考图 ``<Picture N>``（及类型、名称）绑在一起，是 prompt 文本与
    随请求发出的参考图之间唯一的归属契约；同一主体多张参考图全部列出。形态声明与声音声明紧随
    其后，都用 ``<Subject N>`` 记号。
    """
    lines: list[str] = []
    for name in subject_order:
        type_word = _H3_SUBJECT_TYPE_WORDS.get(subject_type.get(name, ""), "asset")
        pictures = "、".join(f"<Picture {p}>" for p in subject_pictures[name])
        lines.append(f"{subject_label[name]} is the {type_word} {name}, shown in {pictures}.")
    lines.extend(_render_form_declarations(character_forms, subject_label))
    lines.extend(_render_voice_declarations(speakers, audio_no, characters, settings, subject_label))
    return "\n".join(lines)


def _character_forms_by_entity(references: list[ReferenceResource], catalog: ReferenceCatalog) -> dict[str, list[str]]:
    """角色参考图按承载它的资产条目分组：``本体名 → [该条目出现的形态记号]``，顺序随参考图顺序。

    衍生与本体是两个引用名、两张参考图，指的却是同一个人（见 ``docs/adr/0072``）。subject_definitions
    段对实际随请求发出的参考图调用本函数，分身排除项（``Avoid`` 行）对正文中完整的已登记引用调用；
    两处都按同一项目目录合并角色身份。未登记的名字自成一条：它指不到任何条目，与别的名字不该合并。

    只看 ``character`` 类型的引用：跨类型重名的存量项目里，同名场景按名字查角色表会查出条目，
    把两张毫无关系的图说成同一角色的两套外观。
    """
    grouped: dict[str, list[str]] = {}
    for reference in references:
        if reference.type != "character" or not reference.name:
            continue
        entry = catalog.lookup("character", reference.name)
        grouped.setdefault(entry.asset_name if entry else reference.name, []).append(reference.name)
    return grouped


def _render_form_declarations(character_forms: dict[str, list[str]], subject_label: dict[str, str]) -> list[str]:
    """同一角色的多个形态同现时，声明它们是同一个人的不同外观（用 ``<Subject N>`` 记号）。

    角色的本体与衍生是各自独立的引用名、各带一张资产图（见 ``docs/adr/0072``），同现不设限。
    不声明时模型只看到两张长相相近的角色参考图，会当成两个人物同框，故在主体声明区就把归属讲清楚。
    只出现一个形态的角色不产出声明。
    """
    lines: list[str] = []
    for forms in character_forms.values():
        if len(forms) < 2:
            continue
        labels = [subject_label.get(form, f"<{form}>") for form in forms]
        lines.append("、".join(labels[:-1]) + f"与{labels[-1]}是同一角色的不同形态，各自按对应参考图呈现。")
    return lines


def _render_summary(subject_label: dict[str, str], subject_order: list[str]) -> str:
    """H3 summary 段：``[reference generation]`` 任务类型 + 参考主体概述。"""
    labels = [subject_label[name] for name in subject_order]
    if not labels:
        return ""
    if len(labels) == 1:
        joined = labels[0]
    elif len(labels) == 2:
        joined = f"{labels[0]} and {labels[1]}"
    else:
        joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    return f"[reference generation] The target video is a reference-to-video generation using {joined} as reference images."


def _render_retention_analysis(subject_label: dict[str, str], subject_order: list[str]) -> str:
    """H3 retention_analysis 段：逐主体 ``fully_preserved``——参考图即该主体外观的真相，全部保留。"""
    return "\n".join(
        f"{subject_label[name]} (appears in the target video): fully_preserved - {name} is retained as shown in its reference image."
        for name in subject_order
    )


#: 旁白记号被丢弃后，用于判定其两侧是否需要合并的分隔标点与空白（中英两形）。
#: 终止标点单列：它在行尾是合法收句，行中的连接标点在行尾则是悬空的。
_MARK_JOINERS = "，,、；;：:"
_MARK_TERMINATORS = "。.！!？?"
_MARK_SEPARATORS = _MARK_JOINERS + _MARK_TERMINATORS
_MARK_SPACES = " \t　"


def _render_body(
    text: str,
    label: Callable[[str], str | None],
    characters: dict,
    catalog: ReferenceCatalog,
) -> str:
    """单元正文段（H3 detailed_description / integrated_multimodal_description）：画面描述做 mention
    标签替换（``<Subject N>`` 或名字记号），发声记号就地重组为 ``<…>说 {台词}``。

    正文逐行原样渲染，不再重排或加分段前缀：正文是作者写下的唯一真相，行文顺序即传达顺序。
    记号可写在行内任意位置，重组按位置就地替换、描述部分留在原处。说话人按**资产表**判定而非参考图
    列表：纯画外角色无参考图，台词照常重组（记号退为名字 ``<名称>``）。未登记的 mention 留编辑器
    原文（warning 由 :func:`derive_voice_bindings` 发出），未被识别成记号的花括号同样原样发送——不做
    剥除，作者能在成片里看见自己写坏的那一段。

    说话人位写下的形态与描述位同形：``@[张三/劲装]{台词}`` 渲染成 ``<Subject N>说 {台词}``（有参考图时）或
    ``<张三/劲装>说 {台词}``（无参考图时），同一角色在一条 prompt 里只有一种记号。写下的衍生没登记时
    退回本体记号。无归属旁白（裸 ``{台词}``）整段丢弃、不进 prompt（ADR 0040 / 0061）。
    """
    lines: list[str] = []
    for line in text.splitlines():
        pieces: list[str | None] = []
        for part in split_speech_line(line):
            if isinstance(part, str):
                pieces.append(render_mentions_with_label(part, label))
            elif not part.speaker:
                pieces.append(None)
            elif part.speaker in characters:
                speaker = _speaker_subject(part, catalog)
                speaker_label = label(speaker) or f"<{speaker}>"
                pieces.append(f"{speaker_label}说 {{{part.text}}}")
            else:
                pieces.append(render_mentions_with_label(part.raw, label))
        lines.append(_join_line_pieces(pieces))
    return "\n".join(line for line in lines if line.strip())


def _speaker_subject(mark: SpeechMark, catalog: ReferenceCatalog) -> str:
    """说话人位的主体记号：写下且已登记的衍生取 ``本体/衍生``，否则取本体名。"""
    reference = mark.speaker_reference
    if mark.derivative and catalog.lookup("character", reference) is not None:
        return reference
    return mark.speaker


def _join_line_pieces(pieces: list[str | None]) -> str:
    """拼接一行的渲染片段；``None`` 是被丢弃的旁白记号，其两侧多出的分隔标点与空白在此合并。

    作者把旁白写在两段描述之间时（``推门，{夜风灌进来}，他按住剑``），记号左右各有一个分隔
    标点，直接拼接会留下「，，」。左侧已有分隔时丢掉右侧的，两侧都只是空白时并成一个。

    记号写在行首或行尾时只有一侧有描述，留下的分隔标点没有另一头可接：行首（``{夜风灌进来}，
    他按住剑``）丢掉记号右侧的分隔与空白，行尾（``推门，{夜风灌进来}``）丢掉左侧悬空的连接
    标点；行尾的终止标点（``推门。{夜风灌进来}``）是合法收句，保留。
    """
    rendered = ""
    dropped = False
    for piece in pieces:
        if piece is None:
            dropped = True
            continue
        if dropped:
            dropped = False
            head = rendered.rstrip()
            if not head or head[-1] in _MARK_SEPARATORS:
                rendered = head
                piece = piece.lstrip(_MARK_SEPARATORS + _MARK_SPACES)
            elif rendered[-1:].isspace():
                # 两侧都只是空白：并成一个，多个空白不该因为记号消失而留在正文里。
                rendered = head
                piece = piece.lstrip()
                if piece and piece[0] not in _MARK_SEPARATORS:
                    rendered += " "
        rendered += piece
    if dropped:
        rendered = rendered.rstrip(_MARK_JOINERS + _MARK_SPACES)
    return rendered


def resolve_reference_audio_paths(project: dict, project_path: Path) -> dict[str, Path]:
    """项目内「参考音频确实可用」的角色 → 绝对路径映射。

    只收录字段指向 ``characters/refs_audio`` 内且文件确实存在的条目（越界路径由
    :func:`lib.speech.audio_utils.resolve_audio_ref_path` 挡下——该字段可经资产 PATCH 写成项目内
    任意字符串）。渲染层据此判定绑定，编号与实际发出的音频段数因此严格等长。

    key 是归一后的角色名（``_character_bucket`` 已归一）：本映射作为 ``audio_ready`` 传给
    :func:`lib.script.reference_video.script_preview.derive_voice_bindings` 与说话人判等，两侧同形
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
