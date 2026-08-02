"""分镜文稿的读时派生：utterances + 降级可见性 warning。

分镜文稿是唯一真相，utterances 机械派生、不落盘（同 ``references`` 从 ``@mention`` 派生的
先例）：存量文稿没有台词符号，派生结果自然为空，无迁移。

台词语法（与 :mod:`lib.reference_video.shot_parser` 的行匹配原语同源）：

- 规范台词行 ``@[角色]：{台词}`` 独立成行（中英冒号均可）→ ``dialogue`` utterance
- 裸 ``{台词}`` 行 → ``voiceover`` utterance
- 台词混写在描述行时不派生、原样保留，只出 warning——不做「行内最近 mention 猜 speaker」
  启发式，推断错误会把台词静默绑到错误角色的参考音频上

warning 是 locale-neutral 的 ``{"key", "params"}`` 条目（同 ``result.warnings`` 既有形态），
由 router / 任务列表按请求语言渲染。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.reference_video.shot_parser import (
    match_dialogue_line,
    match_voiceover_line,
    parse_prompt,
    resolve_references,
)
from lib.script_models import ReferenceResource, Shot, Utterance

#: 未闭合花括号 warning 里回显的原行片段长度上限。
_EXCERPT_LEN = 30

WARN_UNREGISTERED_MENTION = "ref_warn_unregistered_mention"
WARN_UNCLOSED_BRACE = "ref_warn_unclosed_brace"
WARN_DIALOGUE_INLINE = "ref_warn_dialogue_inline"
WARN_UNREGISTERED_SPEAKER = "ref_warn_unregistered_speaker"
WARN_SPEAKER_WITHOUT_AUDIO = "ref_warn_speaker_without_audio"
WARN_SPEAKER_AUDIO_FILE_MISSING = "ref_warn_speaker_audio_file_missing"
WARN_REFERENCE_AUDIO_OVERFLOW = "ref_warn_reference_audio_overflow"
WARN_SILENT_MODEL = "ref_warn_silent_model"
WARN_SPEAKER_AUDIO_NEEDS_IMAGE = "ref_warn_speaker_audio_needs_image"


@dataclass(frozen=True)
class ShotUtterance:
    """镜头级发声条目：``shot_index`` 是 1-based 镜头序号，``utterance`` 复用 drama 的类型。

    归属镜头级而非 unit 级：台词与镜头的时序对位由归属天然给出，渲染层无需另存位置。
    """

    shot_index: int
    utterance: Utterance


@dataclass(frozen=True)
class ScriptPreview:
    """一份分镜文稿的读时派生结果，即编辑器「解析预览面板」的内容源。"""

    shots: list[Shot]
    references: list[ReferenceResource]
    utterances: list[ShotUtterance]
    warnings: list[dict[str, Any]] = field(default_factory=list)


def _warning(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def derive_utterances(shots: list[Shot]) -> tuple[list[ShotUtterance], list[dict[str, Any]]]:
    """逐镜逐行派生 utterances，并收集语法层 warning（未闭合花括号 / 台词混写描述行）。

    纯语法层：不认识项目资产表，speaker 是否登记由 :func:`build_script_preview` 另行判定。
    """
    utterances: list[ShotUtterance] = []
    warnings: list[dict[str, Any]] = []
    for index, shot in enumerate(shots, start=1):
        for line in shot.text.splitlines():
            if line.count("{") != line.count("}"):
                warnings.append(_warning(WARN_UNCLOSED_BRACE, shot=index, excerpt=line.strip()[:_EXCERPT_LEN]))
                continue
            dialogue = match_dialogue_line(line)
            if dialogue is not None:
                speaker, text = dialogue
                utterances.append(ShotUtterance(index, Utterance(kind="dialogue", speaker=speaker, text=text)))
                continue
            voiceover = match_voiceover_line(line)
            if voiceover is not None:
                utterances.append(ShotUtterance(index, Utterance(kind="voiceover", text=voiceover)))
                continue
            if "{" in line:
                warnings.append(_warning(WARN_DIALOGUE_INLINE, shot=index))
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
    utterances: list[ShotUtterance],
    characters: dict,
    *,
    voice_consistency: str = "soft",
    max_reference_audio: int = 0,
    model_id: str = "",
    audio_ready: Collection[str] | None = None,
    require_reference_image: bool = False,
    speakers_with_reference_image: Collection[str] | None = None,
) -> VoiceBindings:
    """从 utterances 机械派生声音绑定：说话人顺序、参考音频编号与降级 warning。

    ``voice_consistency`` 是服务端派生的三级标识（``native`` / ``soft`` / ``none``）：只有
    ``native``（A 类·原生音频参考）才谈得上参考音频的绑定与上限，故「未设参考音频」「超出
    段数上限」两条只在该档发出；``none``（真无声）时改发一条无声知会。

    ``audio_ready`` 是「音频确实可用」的角色名集合：解析预览不碰文件系统，传 None 时按角色
    资产的 ``reference_audio`` 字段非空判定；执行层传入已解析且确实存在的文件对应的角色名，
    让编号与实际随请求发出的音频段数严格等长——字段指向已删文件时编号若不同步，``@音频N``
    会指向不存在的段。两条路径共用本函数，避免预览承诺的绑定与生成实际发出的绑定分叉。
    ``audio_ready`` 非 None 时降级原因区分两种：角色 ``reference_audio`` 字段未设置发
    ``WARN_SPEAKER_WITHOUT_AUDIO``；字段有值但不在 ``audio_ready`` 内（文件已被删除或迁移）
    发 ``WARN_SPEAKER_AUDIO_FILE_MISSING``——前者该去角色设置里补音频，后者该去检查文件是否
    还在，排查方向不同，不能合并成一条 warning。

    ``require_reference_image``：目标 backend 的音频必须逐段挂在具体参考素材项上（如
    wan2.7-r2v）时传 True，此时纯画外（无参考图）speaker 即使有可用音频也不绑定——绑定后
    该角色的音频段数会算进 ``max_reference_audio``，但 backend 没有素材项可挂，要么错配给
    别的角色/场景要么硬失败。降级发一条独立 warning（而非复用「未设参考音频」，原因不同：
    这里音频确实可用，只是没有画面可挂）。``speakers_with_reference_image`` 是本次实际随
    请求发出的参考图对应的角色名集合，仅在 ``require_reference_image`` 为 True 时读取。
    """
    warnings: list[dict[str, Any]] = []

    seen: list[str] = []
    for entry in utterances:
        speaker = entry.utterance.speaker
        if speaker and speaker not in seen:
            seen.append(speaker)

    registered: list[str] = []
    for speaker in seen:
        if speaker in characters:
            registered.append(speaker)
        else:
            warnings.append(_warning(WARN_UNREGISTERED_SPEAKER, name=speaker))

    audio_speakers: list[str] = []
    if voice_consistency == "native":
        image_names = speakers_with_reference_image or ()
        # 音频编号 = dialogue speaker 首现顺序，受 max_reference_audio 上限截断。
        for speaker in registered:
            audio_field_set = bool((characters.get(speaker) or {}).get("reference_audio"))
            has_audio = speaker in audio_ready if audio_ready is not None else audio_field_set
            if not has_audio:
                if audio_ready is not None and audio_field_set:
                    warnings.append(_warning(WARN_SPEAKER_AUDIO_FILE_MISSING, name=speaker))
                else:
                    warnings.append(_warning(WARN_SPEAKER_WITHOUT_AUDIO, name=speaker))
            elif require_reference_image and speaker not in image_names:
                warnings.append(_warning(WARN_SPEAKER_AUDIO_NEEDS_IMAGE, name=speaker))
            elif len(audio_speakers) >= max_reference_audio:
                warnings.append(_warning(WARN_REFERENCE_AUDIO_OVERFLOW, limit=max_reference_audio, name=speaker))
            else:
                audio_speakers.append(speaker)
    elif voice_consistency == "none" and utterances:
        # 只要有台词就知会：画外音同样要渲染，纯画外的文稿在无声模型上也听不到声音。
        warnings.append(_warning(WARN_SILENT_MODEL, model=model_id))

    return VoiceBindings(speakers=registered, audio_speakers=audio_speakers, warnings=warnings)


def build_script_preview(
    text: str,
    project: dict,
    *,
    voice_consistency: str = "soft",
    max_reference_audio: int = 0,
    model_id: str = "",
    audio_requires_reference_image: bool = False,
    max_reference_images: int | None = None,
) -> ScriptPreview:
    """把书写文稿派生成 shots / references / utterances + 降级可见性 warning。

    ``audio_requires_reference_image`` 须与执行层（``render_unit_prompt``）传的同一个 backend
    判定同步——预览不碰文件系统，但这一位不涉及 IO，只是「目标 backend 是否要求音频逐段挂图」
    的能力声明，遗漏会让预览显示已绑定、执行时才降级，用户直到生成后才发现声音没生效。

    ``max_reference_images`` 须同步执行层的能力上限（``VideoLaneResult.max_reference_images``）：
    执行期会把 ``unit.references`` 先按此上限裁剪、再渲染（保证 ``图片N`` 编号与实际发出的
    参考图严格等长，见 ``_render_unit_prompt`` docstring）。预览若按未裁剪的全量 references
    判定 ``character_image_names``，纯画外降级会在裁剪线之外才生效——超限角色的图被裁掉后，
    预览仍显示音频已绑定，执行时才补发 warning。``None`` 表示不裁（能力不可解析时的降级口径，
    与 ``_apply_provider_constraints`` 的 ``max_refs=None`` 一致）。
    """
    shots, mentions = parse_prompt(text)
    references, missing = resolve_references(mentions, project)

    warnings = [_warning(WARN_UNREGISTERED_MENTION, name=name) for name in missing]
    utterances, syntax_warnings = derive_utterances(shots)
    warnings.extend(syntax_warnings)

    # 音频只能对齐到「同名且类型也是 character」的图：scene/prop 可能与角色同名，image_no
    # 若按名字判定会把「这个名字有图」误判成「这个角色有图」。同时按能力上限裁剪后再判定，
    # 与执行层「先裁 references 再渲染」的口径对齐。
    clipped_references = references[:max_reference_images] if max_reference_images is not None else references
    character_image_names = {ref.name for ref in clipped_references if ref.type == "character"}

    bindings = derive_voice_bindings(
        utterances,
        project.get(BUCKET_KEY["character"]) or {},
        voice_consistency=voice_consistency,
        max_reference_audio=max_reference_audio,
        model_id=model_id,
        require_reference_image=audio_requires_reference_image,
        speakers_with_reference_image=character_image_names,
    )
    warnings.extend(bindings.warnings)

    return ScriptPreview(shots=shots, references=references, utterances=utterances, warnings=warnings)
