"""视频单元正文的读时派生：utterances + 降级可见性 warning。

正文是唯一真相，utterances 与参考图一样机械派生、不落盘（见 ADR 0064）。

台词语法（与 :mod:`lib.reference_video.text_parser` 的行内切分原语同源）：

- ``@[角色]{台词}``（mention 与花括号之间允许空白或中英冒号）→ ``dialogue`` utterance
- 裸 ``{台词}`` → ``voiceover`` utterance
- 两者都可出现在行内任意位置，一行可有多条；花括号没被识别成发声记号时不派生、原样保留，
  只出 warning——不做「行内最近 mention 猜 speaker」启发式，推断错误会把台词静默绑到
  错误角色的参考音频上

warning 是 locale-neutral 的 ``{"key", "params"}`` 条目（同 ``result.warnings`` 既有形态），
由 router / 任务列表按请求语言渲染。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from lib.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.reference_video.keyframes import KEYFRAME_MENTION_PREFIX
from lib.reference_video.reference_declarations import unit_reference_declarations
from lib.reference_video.text_parser import (
    derive_references_from_text,
    extract_mentions,
    speech_line_description,
    split_speech_line,
)
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_models import Utterance

#: 未闭合花括号 warning 里回显的原行片段长度上限。
_EXCERPT_LEN = 30

WARN_UNREGISTERED_MENTION = "ref_warn_unregistered_mention"
WARN_UNCLOSED_BRACE = "ref_warn_unclosed_brace"
WARN_BRACES_NOT_SPEECH = "ref_warn_braces_not_speech"
WARN_UNREGISTERED_SPEAKER = "ref_warn_unregistered_speaker"
WARN_SPEAKER_WITHOUT_AUDIO = "ref_warn_speaker_without_audio"
WARN_SPEAKER_AUDIO_UNAVAILABLE = "ref_warn_speaker_audio_unavailable"
WARN_REFERENCE_AUDIO_OVERFLOW = "ref_warn_reference_audio_overflow"
WARN_SILENT_MODEL = "ref_warn_silent_model"
WARN_SILENT_EPISODE = "ref_warn_silent_episode"
WARN_SPEAKER_AUDIO_NEEDS_IMAGE = "ref_warn_speaker_audio_needs_image"


@dataclass(frozen=True)
class ScriptPreview:
    """一份视频单元正文的读时派生结果，即编辑器「解析预览面板」的内容源。"""

    utterances: list[Utterance]
    warnings: list[dict[str, Any]] = field(default_factory=list)


def _warning(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def derive_utterances(text: str) -> tuple[list[Utterance], list[dict[str, Any]]]:
    """逐行派生 utterances，并收集语法层 warning（未闭合花括号 / 未识别的花括号）。

    发声记号可出现在行内任意位置，一行可派生多条 utterance，顺序即正文出现顺序。warning
    只看**记号之外**的残余描述：行内已识别的记号不该因为同行还写着别的花括号而被吞掉；
    ``line`` 参数是 1-based 行号，让呈现层把 warning 锚回正文的具体一行。

    纯语法层：不认识项目资产表，speaker 是否登记由 :func:`build_script_preview` 另行判定。
    """
    utterances: list[Utterance] = []
    warnings: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        parts = split_speech_line(line)
        for part in parts:
            if isinstance(part, str):
                continue
            if part.speaker:
                utterances.append(Utterance(kind="dialogue", speaker=part.speaker, text=part.text))
            else:
                utterances.append(Utterance(kind="voiceover", text=part.text))
        rest = speech_line_description(parts)
        if rest.count("{") != rest.count("}"):
            warnings.append(_warning(WARN_UNCLOSED_BRACE, line=index, excerpt=line.strip()[:_EXCERPT_LEN]))
        elif "{" in rest or "}" in rest:
            warnings.append(_warning(WARN_BRACES_NOT_SPEECH, line=index))
    return utterances, warnings


@dataclass(frozen=True)
class VoiceBindings:
    """一份文稿的声音派生结果：谁在说话、谁绑到了第几段参考音频。

    ``speakers`` 是已登记的 dialogue speaker（首现顺序）——第一段声音特征声明按此顺序逐条发出。
    ``audio_speakers`` 是其中真正绑上参考音频的子集，**顺序即 ``@音频N`` 编号与
    ``VideoGenerationRequest.reference_audio_files`` 的字段顺序**（两者同一份派生，不各自重算）。
    """

    speakers: list[str]
    audio_speakers: list[str]
    warnings: list[dict[str, Any]]


def derive_voice_bindings(
    utterances: list[Utterance],
    characters: dict,
    settings: VoiceRenderSettings,
    *,
    speakers_with_reference_image: Collection[str] | None = None,
) -> VoiceBindings:
    """从 utterances 机械派生声音绑定：说话人顺序、参考音频编号与降级 warning。

    ``settings`` 收口本次渲染的声音输入档（见 :class:`~lib.reference_video.voice_settings
    .VoiceRenderSettings`）。只有 ``native``（A 类·原生音频参考）才谈得上参考音频的绑定与
    上限，故「未设参考音频」「超出段数上限」两条只在该档发出；``none``（真无声）时改发一条
    无声知会。

    ``settings.requested_generate_audio`` 为 False 时无论模型档位如何都不产出
    ``audio_speakers``，改发一条本集无声知会。参考音频只在这一处收口，prompt 的 ``@音频N``
    与 ``reference_audio_files`` 因此同源消失，不会出现文本承诺了音色参考、请求里却没有对应
    音频段的分叉；台词渲染与本开关无关（另见 :func:`lib.reference_video.prompt_render
    ._render_segment_two`），供应商可继续拿台词文本做口型参考。无声知会优先于 ``none`` 档的
    ``WARN_SILENT_MODEL``：两者都是「这一集听不到声音」，用户主动关掉音频时说模型不产音会指错
    排查方向。

    ``settings.audio_ready`` 为 None 时按角色资产的 ``reference_audio`` 字段非空判定；执行层
    传入已解析且确实存在的文件对应的角色名，让编号与实际随请求发出的音频段数严格等长——字段
    指向已删文件时编号若不同步，``@音频N`` 会指向不存在的段。两条路径共用本函数，避免预览
    承诺的绑定与生成实际发出的绑定分叉。非 None 时降级原因区分两种：角色 ``reference_audio``
    字段未设置发 ``WARN_SPEAKER_WITHOUT_AUDIO``；字段有值但不在集合内发
    ``WARN_SPEAKER_AUDIO_UNAVAILABLE``——前者该去角色设置里补音频，后者字段已填好，该去查
    它指向的音频本身，排查方向不同，不能合并成一条 warning。后者的成因不止一种（文件被删、
    字段值指到 ``characters/refs_audio`` 之外都会被
    :func:`lib.reference_video.prompt_render.resolve_reference_audio_paths` 排除），故文案
    只说「不可用」，不断言具体是哪一种。

    ``settings.requires_reference_image`` 为 True 时（目标 backend 的音频必须逐段挂在具体
    参考素材项上，如 wan2.7-r2v），纯画外（无参考图）speaker 即使有可用音频也不绑定——绑定后
    该角色的音频段数会算进 ``max_reference_audio``，但 backend 没有素材项可挂，要么错配给
    别的角色/场景要么硬失败。降级发一条独立 warning（而非复用「未设参考音频」，原因不同：
    这里音频确实可用，只是没有画面可挂）。``speakers_with_reference_image`` 是本次实际随
    请求发出的参考图对应的角色名集合，仅在该位为 True 时读取。

    角色表与两个按名字判定的集合（``audio_ready`` / ``speakers_with_reference_image``）都先归一
    到资产名比对坐标系（:func:`lib.asset_types.asset_name_comparison_key`）。少归一一侧的后果不是
    报错而是静默降级——该角色被判「未登记」而不发音色声明、或判「无可用音频」而不绑参考音频，
    用户拿到的是一条声音不对的成片加一条非阻断 warning。
    """
    warnings: list[dict[str, Any]] = []
    characters = normalize_asset_bucket(characters)

    seen: list[str] = []
    for entry in utterances:
        speaker = asset_name_comparison_key(entry.speaker or "")
        if speaker and speaker not in seen:
            seen.append(speaker)

    registered: list[str] = []
    for speaker in seen:
        if speaker in characters:
            registered.append(speaker)
        else:
            warnings.append(_warning(WARN_UNREGISTERED_SPEAKER, name=speaker))

    audio_speakers: list[str] = []
    if settings.is_silent:
        # 只要有台词就知会：画外音同样要渲染，纯画外的文稿在无声路径上也听不到声音。
        # 本集关闭音频的提示优先于模型不产音——前者是用户当下可改的开关。
        if utterances:
            if not settings.requested_generate_audio:
                warnings.append(_warning(WARN_SILENT_EPISODE))
            else:
                warnings.append(_warning(WARN_SILENT_MODEL, model=settings.model_id))
    elif settings.voice_consistency == "native":
        image_names = {asset_name_comparison_key(name) for name in speakers_with_reference_image or ()}
        audio_ready = (
            {asset_name_comparison_key(name) for name in settings.audio_ready}
            if settings.audio_ready is not None
            else None
        )
        # 音频编号 = dialogue speaker 首现顺序，受 max_reference_audio 上限截断。
        for speaker in registered:
            char_data = characters.get(speaker)
            audio_field_set = bool(char_data.get("reference_audio")) if isinstance(char_data, dict) else False
            has_audio = speaker in audio_ready if audio_ready is not None else audio_field_set
            if not has_audio:
                if audio_ready is not None and audio_field_set:
                    warnings.append(_warning(WARN_SPEAKER_AUDIO_UNAVAILABLE, name=speaker))
                else:
                    warnings.append(_warning(WARN_SPEAKER_WITHOUT_AUDIO, name=speaker))
            elif settings.requires_reference_image and speaker not in image_names:
                warnings.append(_warning(WARN_SPEAKER_AUDIO_NEEDS_IMAGE, name=speaker))
            elif len(audio_speakers) >= settings.max_reference_audio:
                warnings.append(
                    _warning(WARN_REFERENCE_AUDIO_OVERFLOW, limit=settings.max_reference_audio, name=speaker)
                )
            else:
                audio_speakers.append(speaker)

    return VoiceBindings(speakers=registered, audio_speakers=audio_speakers, warnings=warnings)


def build_script_preview(
    text: str,
    project: dict,
    settings: VoiceRenderSettings,
    *,
    max_reference_images: int | None = None,
    unit: dict[str, Any] | None = None,
) -> ScriptPreview:
    """把视频单元正文派生成 utterances + 降级可见性 warning。

    ``settings`` 必填无兜底（同 ``render_unit_prompt``），须与执行层拿到的同一份声音输入档同步，
    否则预览会显示音频已绑定、实际请求却不带音频段。其中 ``requires_reference_image``（目标 backend 是否
    要求音频逐段挂图）不涉及 IO，预览虽不碰文件系统也须一并同步，遗漏会让预览显示已绑定、
    执行时才降级，用户直到生成后才发现声音没生效。``settings.audio_ready`` 在预览侧留 None：
    预览不解析文件，按角色资产的 ``reference_audio`` 字段非空判定。

    ``max_reference_images`` 须同步执行层的能力上限（``VideoLaneResult.max_reference_images``）：
    执行期会把正文派生的参考图先按此上限裁剪、再渲染（保证 ``图片N`` 编号与实际发出的参考图
    严格等长，见 ``render_unit_prompt`` docstring）。预览若按未裁剪的全量派生结果判定
    ``character_image_names``，纯画外降级会在裁剪线之外才生效——超限角色的图被裁掉后，预览仍
    显示音频已绑定，执行时才补发 warning。``None`` 表示不裁（能力不可解析时的降级口径，与请求
    投影在能力不可解析时不裁参考图的口径一致）。

    ``unit`` 存在时，预览会按执行层同一套 unit-owned keyframe 规则解析
    ``@[关键分镜 ID]``。编辑器传入的 ``text`` 仍覆盖 unit 中已保存的正文，让未保存草稿也能
    正确绑定该 unit 已登记的关键分镜；不属于当前 unit 的关键分镜标签继续按未登记引用提示。

    未登记的 ``@[名称]`` 只发 warning、不阻断：正文是作者写的，预览没有可保护的机器契约。
    """
    if unit is None:
        references, missing = derive_references_from_text(text, project)
    else:
        draft_unit = {**unit, "text": text}
        references = list(unit_reference_declarations(project, draft_unit))
        resolved_mentions = {
            asset_name_comparison_key(
                f"{KEYFRAME_MENTION_PREFIX}{reference.name}"
                if reference.type == "keyframe"
                else reference.name
            )
            for reference in references
        }
        missing = [
            name
            for name in extract_mentions(text)
            if asset_name_comparison_key(name) not in resolved_mentions
        ]

    warnings = [_warning(WARN_UNREGISTERED_MENTION, name=name) for name in missing]
    utterances, syntax_warnings = derive_utterances(text)
    warnings.extend(syntax_warnings)

    # 音频只能对齐到 character 参考图。同时按能力上限裁剪后再判定，
    # 与执行层「先裁参考图再渲染」的口径对齐。
    clipped_references = references[:max_reference_images] if max_reference_images is not None else references
    character_image_names = {ref.name for ref in clipped_references if ref.type == "character"}

    bindings = derive_voice_bindings(
        utterances,
        project.get(BUCKET_KEY["character"]) or {},
        settings,
        speakers_with_reference_image=character_image_names,
    )
    warnings.extend(bindings.warnings)

    return ScriptPreview(utterances=utterances, warnings=warnings)
