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
WARN_REFERENCE_AUDIO_OVERFLOW = "ref_warn_reference_audio_overflow"
WARN_SILENT_MODEL = "ref_warn_silent_model"


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


def build_script_preview(
    text: str,
    project: dict,
    *,
    voice_consistency: str = "soft",
    max_reference_audio: int = 0,
    model_id: str = "",
) -> ScriptPreview:
    """把书写文稿派生成 shots / references / utterances + 七条降级可见性 warning。

    ``voice_consistency`` 是服务端派生的三级标识（``native`` / ``soft`` / ``none``）：只有
    ``native``（A 类·原生音频参考）才谈得上参考音频的绑定与上限，故「未设参考音频」「超出
    段数上限」两条只在该档发出；``none``（真无声）时改发一条无声知会。
    """
    shots, mentions = parse_prompt(text)
    references, missing = resolve_references(mentions, project)

    warnings = [_warning(WARN_UNREGISTERED_MENTION, name=name) for name in missing]
    utterances, syntax_warnings = derive_utterances(shots)
    warnings.extend(syntax_warnings)

    characters: dict = project.get(BUCKET_KEY["character"]) or {}
    speakers: list[str] = []
    for entry in utterances:
        speaker = entry.utterance.speaker
        if speaker and speaker not in speakers:
            speakers.append(speaker)

    registered: list[str] = []
    for speaker in speakers:
        if speaker in characters:
            registered.append(speaker)
        else:
            warnings.append(_warning(WARN_UNREGISTERED_SPEAKER, name=speaker))

    if voice_consistency == "native":
        # 音频编号 = dialogue speaker 首现顺序，受 max_reference_audio 上限截断；
        # 与 reference_audio_files 的请求字段顺序同一口径。
        bound = 0
        for speaker in registered:
            if not (characters.get(speaker) or {}).get("reference_audio"):
                warnings.append(_warning(WARN_SPEAKER_WITHOUT_AUDIO, name=speaker))
            elif bound >= max_reference_audio:
                warnings.append(_warning(WARN_REFERENCE_AUDIO_OVERFLOW, limit=max_reference_audio, name=speaker))
            else:
                bound += 1
    elif voice_consistency == "none" and speakers:
        warnings.append(_warning(WARN_SILENT_MODEL, model=model_id))

    return ScriptPreview(shots=shots, references=references, utterances=utterances, warnings=warnings)
