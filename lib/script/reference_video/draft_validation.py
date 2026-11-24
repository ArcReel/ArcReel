"""参考生视频 script_plan / prompt_authoring 扁平草稿结构的机械校验。

LLM 产出与人在编辑器写的是同一种格式，校验因此也落在同一份文本上；本模块是「parser
后校验」这一层的落点：schema 已卡死枚举与外层结构，剩下的语法与内容约束在这里逐 unit
判定，任一违约 fail-loud 抛 :class:`DraftViolation`，不把违规产物当成功结果写盘。

「不当成功结果写盘」不等于丢弃：调用侧用 :func:`collect_violations` 把逐 unit 的违约收齐成
一份报告，产物落待修复草稿（``lib.script.draft_quarantine``）等 Agent 修复后重判，不重抽。
每条违约带 ``code``（违约类）与 ``label``（unit 定位），报告因此可逐条定位、可按类断言。

违约条目类型与报告渲染（:class:`DraftViolation` / :func:`collect_violations` /
:func:`render_violation_report`）三条路线通用，定义在路线中立的 ``lib.script.draft_violation``；
本模块原样再导出它们，参考生视频的既有导入路径不变。

与编辑器侧（人写）的容忍口径分流：``lib.script.reference_video.script_preview`` 对同样的文本只
出 warning、照常落盘——那里有作者意图要保护；本模块面向机器产物，没有意图可保护，一律拒。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Container
from typing import Any

from lib.project.asset_types import asset_name_comparison_key
from lib.references.reference_catalog import build_reference_catalog
from lib.script.draft_violation import (
    DraftViolation,
    DraftViolations,
    collect_violations,
    render_violation_report,
    violation_items,
)
from lib.script.reference_video.text_parser import (
    SpeechMark,
    derive_references_from_text,
    find_malformed_mention,
    leading_mention_before_colon,
    line_speech_marks,
    speech_line_description,
    speech_speaker_references,
    split_speech_line,
    strip_speech_marks,
)
from lib.script.script_models import ReferenceResource
from lib.speech.speech_composition import SpeechProblem, admit_script_unit
from lib.speech.speech_rate import estimate_spoken_seconds

SPEECH_OVERFLOW_TOLERANCE = 0.20


def _normalize_for_anchor(text: str) -> str:
    """Unicode NFC 归一后把连续空白折叠为单个空格，只消除编码与空白差异，不删除空白本身。

    与 narration 覆盖校验的 ``_normalize_for_coverage`` 同一口径：模型复制原文时换行与
    缩进的还原不可靠，但删字改字必须被抓住。NFC 与 ``lib.episode.episode_ledger.normalize_source_text``
    定义的源文坐标系一致——带组合附加符的语种（如 vi）源文可能以 NFD 落盘、模型回写 NFC，
    纯编码形式差异不该被判成改写。
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def is_verbatim_source_anchor(source_text: str, novel_text: str) -> bool:
    """``source_text`` 在空白归一后是否为源文的逐字子串（空锚不算命中）。"""
    anchor = _normalize_for_anchor(source_text)
    return bool(anchor) and anchor in _normalize_for_anchor(novel_text)


def validate_source_text_anchor(label: str, source_text: str, novel_text: str) -> None:
    """校验 ``source_text`` 是源文的逐字子串（空白归一后）。

    script_plan 的 unit 边界要能追溯回原文，锚失配意味着模型在拆分时改写或杜撰了原文——这是内容
    层的根本违约，比任何下游画面问题都更早需要被拦下。只判子串、不判顺序与完整覆盖：unit
    是画面单元不是朗读单元，允许原文中的对话提示语、转述段落不进任何 unit 的锚。
    """
    anchor = _normalize_for_anchor(source_text)
    if not anchor:
        raise DraftViolation(
            f"{label} 的 source_text 为空：每个 unit 必须摘录其所依据的原文片段作为追溯锚",
            code="source_text_empty",
            label=label,
            params={"label": label},
        )
    if not is_verbatim_source_anchor(source_text, novel_text):
        raise DraftViolation(
            f"{label} 的 source_text 不是小说原文的逐字片段（存在改写、翻译或杜撰）："
            f"{source_text.strip()[:40]!r}；请原样复制原文，不要转述",
            code="source_text_not_verbatim",
            label=label,
            params={"label": label, "excerpt": repr(source_text.strip()[:40])},
        )


_FULLWIDTH_BRACES = "｛｝"


def _assert_line_syntax(label: str, text: str, character_references: Container[str]) -> None:
    """逐行判引用语法：花括号用法、写坏的 ``@[`` 引用、缺花括号的台词。

    三类共性是「解析器不报错、但派生结果与作者意图相反」：台词降级成画面描述、说话人反被
    派生成参考图、坏 token 原样进供应商请求。机器产物没有作者意图可保护，一律在语法判定处
    响亮拒绝，不静默、也不代模型改写。
    """
    for idx, line in enumerate(text.splitlines()):
        if any(ch in line for ch in _FULLWIDTH_BRACES):
            raise DraftViolation(
                f"{label} 使用了全角花括号：{line.strip()[:40]!r}；"
                "台词与画外音的花括号必须是半角 `{}`，全角形不会被识别为台词",
                code="fullwidth_braces",
                label=label,
                line=idx,
                params={"label": label, "excerpt": repr(line.strip()[:40])},
            )
        malformed = find_malformed_mention(line)
        if malformed is not None:
            raise DraftViolation(
                f"{label} 有写坏的资产引用：{malformed!r}；"
                "引用须写成 `@[资产名]`，方括号要成对闭合、名称非空，否则既不进 references，"
                "又会原样进入视频请求",
                code="malformed_mention",
                label=label,
                line=idx,
                params={"label": label, "excerpt": repr(malformed)},
            )
        parts = split_speech_line(line)
        leads_with_speech = bool(parts) and isinstance(parts[0], SpeechMark) and bool(parts[0].speaker)
        if not leads_with_speech and (leading_mention_before_colon(line) or "") in character_references:
            raise DraftViolation(
                f"{label} 的台词写法不合法：{line.strip()[:40]!r}；"
                "台词须写成 `@[角色]{台词}`——说话人非空、台词由半角花括号成对包裹，"
                "否则这段会被当成画面描述、台词整句丢失",
                code="dialogue_line_syntax",
                label=label,
                line=idx,
                params={"label": label, "excerpt": repr(line.strip()[:40])},
            )
        rest = speech_line_description(parts)
        if "{" not in rest and "}" not in rest:
            continue
        excerpt = line.strip()[:40]
        if rest.count("{") != rest.count("}"):
            raise DraftViolation(
                f"{label} 有未闭合的花括号：{excerpt!r}",
                code="unclosed_brace",
                label=label,
                line=idx,
                params={"label": label, "excerpt": repr(excerpt)},
            )
        raise DraftViolation(
            f"{label} 在画面描述里使用了花括号：{excerpt!r}；"
            "花括号是发声保留语法，台词写作 `@[角色]{台词}`、画外音写作 `{画外音}`，"
            "说话人须非空、花括号不得嵌套",
            code="braces_in_description",
            label=label,
            line=idx,
            params={"label": label, "excerpt": repr(excerpt)},
        )


def _has_description_line(text: str) -> bool:
    """该单元是否有画面描述：某一行剥掉全部发声记号后仍有非空文本。"""
    return any(strip_speech_marks(line).strip() for line in text.splitlines())


def dialogue_speakers(text: str) -> list[str]:
    """按出现顺序取出台词记号的说话人（去重）——登记校验据此判说话人是否为登记角色。

    说话人取自 ``split_speech_line``，已在解析器入口归一到资产名比对坐标系
    （``lib.script.reference_video.text_parser`` 的 ``_normalize_source``），与资产表归一后的 key
    同形，本函数直接使用该结果，不再额外归一。

    说话人位写 ``@[角色/衍生]`` 时给出的是本体名：声音属于身份，衍生共享本体的参考音频与
    音色（见 ``docs/adr/0072``）。写下的那套外观见 ``speech_speaker_references``。
    """
    seen: set[str] = set()
    speakers: list[str] = []
    for line in text.splitlines():
        for mark in line_speech_marks(line):
            if mark.speaker and mark.speaker not in seen:
                seen.add(mark.speaker)
                speakers.append(mark.speaker)
    return speakers


def normative_lines(text: str) -> list[tuple[str, str, str]]:
    """按出现顺序取出全部发声记号：``(kind, speaker, 台词)``，``kind`` 为 dialogue / voiceover。

    prompt_authoring 的保结构 diff 以此为比对项：画面描述可自由展开，发声记号必须逐字不变。

    台词与说话人已在解析器入口归一到 NFC（``lib.script.reference_video.text_parser`` 的
    ``_normalize_source``）：源文可能以 NFD 落盘而模型回写 NFC，两种形式肉眼同字、逐字比对
    却不等，保结构 diff 会把纯编码差异判成改写；口播时长估算同样要求 NFC（按词计的语种下
    组合附加符会把一个词拆成多个阅读单位）。归一在解析器一处完成，两个消费方口径天然一致。
    """
    result: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        result.extend(
            ("dialogue" if mark.speaker else "voiceover", mark.speaker, mark.text) for mark in line_speech_marks(line)
        )
    return result


def validate_unit_text(
    label: str,
    text: str,
    project: dict[str, Any],
    *,
    unit_id: str | None = None,
    max_refs: int | None,
    max_speakers: int | None = None,
    tolerated_speakers: Collection[str] = (),
) -> list[ReferenceResource]:
    """校验一个 unit 的正文并机械派生参考图引用。

    覆盖的阻断违约：正文为空或只有发声记号、引用语法误用（花括号、写坏的引用、缺花
    括号的台词）、``@[名称]`` 未登记（含台词记号的说话人位与它写下的衍生）、参考图数超模型
    上限、说话人数超规划期上限。正文是唯一落盘物，派生结果只服务本次校验与能力判定，不写回。

    ``max_speakers`` 是规划期单个视频单元的说话人数上限，取
    :attr:`~lib.script.reference_video.voice_settings.VoiceRenderSettings.planning_speaker_limit`（参考音频
    会随请求挂出时才非 None）；None 不约束。说话人按 :func:`dialogue_speakers` 计数：按角色本体去重，
    角色未设参考音频时同样计入，口径比渲染层截断更严，补设参考音频后不必重新拆分。

    ``tolerated_speakers`` 里的说话人不要求已登记：正式正文里确认过的说话人可以是未登记的
    群演，执行时该行不绑声音、按原文发送。
    """
    if not text.strip():
        raise DraftViolation(
            f"{label} 的正文为空",
            code="empty_text",
            label=label,
            params={"label": label},
        )

    catalog = build_reference_catalog(project)
    _assert_line_syntax(label, text, catalog.reference_names("character"))

    if not _has_description_line(text):
        raise DraftViolation(
            f"{label} 没有画面描述；只有台词与画外音的单元没有可生成的画面",
            code="blank_description",
            label=label,
            params={"label": label},
        )

    refs, missing = derive_references_from_text(text, project)
    if missing:
        raise DraftViolation(
            f"{label} 引用了未登记的资产名: {missing}；资产名必须逐字取自 project.json 三张表",
            code="unregistered_asset",
            label=label,
            params={"label": label, "field": "text", "names": missing},
        )

    tolerated = {asset_name_comparison_key(name) for name in tolerated_speakers}
    registered_speakers = catalog.asset_names("character")
    bad_speakers = sorted({s for s in dialogue_speakers(text) if s not in registered_speakers and s not in tolerated})
    if bad_speakers:
        raise DraftViolation(
            f"{label} 的台词说话人未登记为角色资产: {bad_speakers}；说话人决定该句台词绑哪段参考音频，必须是登记角色",
            code="unregistered_speaker",
            label=label,
            params={"label": label, "names": bad_speakers},
        )

    character_references = catalog.reference_names("character")
    bad_forms = sorted(
        {name for name in speech_speaker_references(text) if name not in character_references and name not in tolerated}
    )
    if bad_forms:
        raise DraftViolation(
            f"{label} 的台词说话人写了该角色没有的衍生: {bad_forms}；"
            "衍生须先登记在该角色下，或改写成本体名 `@[角色]{台词}`",
            code="unregistered_speaker_derivative",
            label=label,
            params={"label": label, "names": bad_forms},
        )

    if max_refs is not None and len(refs) > max_refs:
        raise DraftViolation(
            f"{label} 的参考图数 {len(refs)} 超过模型上限 {max_refs}；请把次要角色融入背景描述（不用 `@` 引用）",
            code="refs_over_limit",
            label=label,
            params={"label": label, "count": len(refs), "limit": max_refs},
        )
    if max_speakers is not None and (speaker_count := len(dialogue_speakers(text))) > max_speakers:
        raise DraftViolation(
            f"{label} 的说话人数 {speaker_count} 超过模型参考音频段数上限 {max_speakers}；"
            "请按说话人拆分镜头，使每个视频单元的说话人数不超过上限",
            code="speakers_over_limit",
            label=label,
            params={"label": label, "count": speaker_count, "limit": max_speakers},
        )
    canonical_unit_id = unit_id if unit_id is not None else label.removeprefix("unit ").strip()
    admission = admit_script_unit("video_units", {"unit_id": canonical_unit_id, "text": text})
    if not admission.allowed:
        raise DraftViolations([_speech_problem_violation(problem) for problem in admission.problems])
    return refs


def _speech_problem_violation(problem: SpeechProblem) -> DraftViolation:
    locations = ", ".join(
        ".".join(str(part) for part in location.path) + (f" line {location.line}" if location.line is not None else "")
        for location in problem.locations
    )
    display_locations = ", ".join(
        ".".join(str(part) for part in location.path) + (f":{location.line + 1}" if location.line is not None else "")
        for location in problem.locations
    )
    line = problem.locations[0].line if len(problem.locations) == 1 else None
    return DraftViolation(
        f"unit {problem.unit_id} 发声准入未通过（{problem.reason.value}，定位：{locations}）；"
        f"下一步：{problem.action.value}",
        code=problem.code.value,
        label=f"unit {problem.unit_id}",
        line=line,
        locations=tuple({"path": list(location.path), "line": location.line} for location in problem.locations),
        reason=problem.reason.value,
        action=problem.action.value,
        params={"label": f"unit {problem.unit_id}", "location": display_locations},
    )


def dialogue_spoken_seconds(text: str, language: object, speech_rate_override: float | None = None) -> float:
    """该 unit 全部台词的口播估算秒数；``speech_rate_override`` 为 None 时按语言默认语速。"""
    language = language if isinstance(language, str) else None
    return sum(estimate_spoken_seconds(line[2], language, speech_rate_override) for line in normative_lines(text))


def validate_dialogue_load(
    label: str,
    text: str,
    duration_seconds: int,
    language: object,
    speech_rate_override: float | None = None,
) -> None:
    """校验该 unit 的台词量念得完：口播估算超出 unit 时长（含宽容系数）即违约。

    时长就是计费，unit 时长在 script_plan 定稿；台词写超了意味着成片必然吞词或抢拍，且这在
    script_plan 阶段是可改的（重拆 unit 或删台词），拖到生成后才发现只能重来。
    ``speech_rate_override`` 是项目级语速覆盖，None 即回退语言默认——与 prompt 构造侧同源，
    prompt 给的下界与此处判的上界始终是同一把尺。
    """
    spoken = dialogue_spoken_seconds(text, language, speech_rate_override)
    budget = duration_seconds * (1 + SPEECH_OVERFLOW_TOLERANCE)
    if spoken > budget:
        raise DraftViolation(
            f"{label} 的台词念完约需 {spoken:.1f} 秒，超过该 unit 的 {duration_seconds} 秒"
            f"（宽容 {SPEECH_OVERFLOW_TOLERANCE:.0%} 后上限 {budget:.1f} 秒）；"
            "请改取更长的时长档、把该 unit 拆开，或精简台词",
            code="dialogue_overload",
            label=label,
            params={
                "label": label,
                "spoken": f"{spoken:.1f}",
                "duration": duration_seconds,
                "tolerance": f"{SPEECH_OVERFLOW_TOLERANCE:.0%}",
                "budget": f"{budget:.1f}",
            },
        )


def assert_dialogue_preserved(label: str, script_plan_text: str, prompt_authoring_text: str) -> None:
    """prompt_authoring 保结构 diff：发声记号的序列必须与 script_plan 逐字一致。

    prompt_authoring 的职责是提示词编写，台词属于 script_plan 已由用户完成内容确认的内容契约。改词、增删、
    重排一律响亮失败，不静默接受——台词不配画面时正确的出路是报错回到 script_plan，而不是让 prompt_authoring
    自行把台词改成好配的样子。
    """

    def _speech_syntax(line: tuple[str, str, str]) -> str:
        _kind, speaker, spoken = line
        return f"@[{speaker}]{{{spoken}}}" if speaker else f"{{{spoken}}}"

    before = normative_lines(script_plan_text)
    after = normative_lines(prompt_authoring_text)
    if before == after:
        return
    if len(before) != len(after):
        raise DraftViolation(
            f"{label} 的台词条数被改动（script_plan 有 {len(before)} 条，prompt_authoring 产出 {len(after)} 条）；"
            "prompt_authoring 只做提示词编写，台词须逐字保留",
            code="dialogue_line_count_changed",
            label=label,
            params={"label": label, "before": len(before), "after": len(after)},
        )
    for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
        if old != new:
            raise DraftViolation(
                f"{label} 第 {index} 条台词被改写（原：{old[1] or '画外音'}「{old[2]}」，"
                f"现：{new[1] or '画外音'}「{new[2]}」）；prompt_authoring 只做提示词编写，台词须逐字保留",
                code="dialogue_rewritten",
                label=label,
                params={"label": label, "index": index, "before": _speech_syntax(old), "after": _speech_syntax(new)},
            )


__all__ = [
    "SPEECH_OVERFLOW_TOLERANCE",
    "DraftViolation",
    "DraftViolations",
    "assert_dialogue_preserved",
    "collect_violations",
    "dialogue_speakers",
    "dialogue_spoken_seconds",
    "is_verbatim_source_anchor",
    "normative_lines",
    "render_violation_report",
    "validate_dialogue_load",
    "validate_source_text_anchor",
    "validate_unit_text",
    "violation_items",
]
