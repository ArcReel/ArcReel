"""参考生视频 script_plan / prompt_authoring 扁平草稿结构的机械校验。

LLM 产出与人在编辑器写的是同一种格式，校验因此也落在同一份文本上；本模块是「parser
后校验」这一层的落点：schema 已卡死枚举与外层结构，剩下的语法与内容约束在这里逐 unit
判定，任一违约 fail-loud 抛 :class:`DraftViolation`，不把违规产物当成功结果写盘。

「不当成功结果写盘」不等于丢弃：调用侧用 :func:`collect_violations` 把逐 unit 的违约收齐成
一份报告，产物落待修复草稿（``lib.draft_quarantine``）等 Agent 修复后重判，不重抽。
每条违约带 ``code``（违约类）与 ``label``（unit 定位），报告因此可逐条定位、可按类断言。

违约条目类型与报告渲染（:class:`DraftViolation` / :func:`collect_violations` /
:func:`render_violation_report`）三条路线通用，定义在路线中立的 ``lib.draft_violation``；
本模块原样再导出它们，参考生视频的既有导入路径不变。

与编辑器侧（人写）的容忍口径分流：``lib.reference_video.script_preview`` 对同样的文本只
出 warning、照常落盘——那里有作者意图要保护；本模块面向机器产物，没有意图可保护，一律拒。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from lib.asset_types import BUCKET_KEY, normalize_asset_bucket
from lib.draft_violation import (
    DraftViolation,
    DraftViolations,
    collect_violations,
    render_violation_report,
    violation_items,
)
from lib.reference_video.text_parser import (
    SpeechMark,
    derive_references_from_text,
    find_malformed_mention,
    leading_mention_before_colon,
    line_speech_marks,
    speech_line_description,
    split_speech_line,
    strip_speech_marks,
)
from lib.script_models import ReferenceResource
from lib.speech_composition import SpeechProblem, admit_script_unit
from lib.speech_rate import estimate_spoken_seconds

#: 台词口播时长相对 unit 时长的宽容系数：估算超出 unit 时长这个比例才判超载。
#: 语速是统计估算（``lib.speech_rate``），逐字计数与真实配音节奏必然有出入；不留宽容会
#: 把「刚好写满」的正常产出判违约。与 drama 保存期上界 warning 的 20% 同量级——两处都是
#: 「同一套语速估算 vs 已定时长」的比对，宽容度没有理由不同。
SPEECH_OVERFLOW_TOLERANCE = 0.20


def _normalize_for_anchor(text: str) -> str:
    """Unicode NFC 归一后把连续空白折叠为单个空格，只消除编码与空白差异，不删除空白本身。

    与 narration 覆盖校验的 ``_normalize_for_coverage`` 同一口径：模型复制原文时换行与
    缩进的还原不可靠，但删字改字必须被抓住。NFC 与 ``lib.episode_ledger.normalize_source_text``
    定义的源文坐标系一致——带组合附加符的语种（如 vi）源文可能以 NFD 落盘、模型回写 NFC，
    纯编码形式差异不该被判成改写。
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


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
        )
    if anchor not in _normalize_for_anchor(novel_text):
        raise DraftViolation(
            f"{label} 的 source_text 不是小说原文的逐字片段（存在改写、翻译或杜撰）："
            f"{source_text.strip()[:40]!r}；请原样复制原文，不要转述",
            code="source_text_not_verbatim",
            label=label,
        )


#: 全角花括号。语法只认半角，但中文输入法下模型很容易写出全角形；行里出现全角花括号时
#: 该段不成发声记号，会被当成画面描述放行——台词静默降级成描述、说话人反而被派生成参考图。
#: 故在语法判定处显式识别并拒绝，不静默、也不代模型改写。
_FULLWIDTH_BRACES = "｛｝"


def _assert_line_syntax(label: str, text: str, characters: dict[str, Any]) -> None:
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
            )
        parts = split_speech_line(line)
        leads_with_speech = bool(parts) and isinstance(parts[0], SpeechMark) and bool(parts[0].speaker)
        # 只有登记角色 + 冒号才判成写坏的台词：场景 / 道具做小标题（``@[酒馆]：木门被风吹开``）
        # 是合法的画面描述写法，按同一形态一概判违约会把正常的 script_plan 产出拒掉。
        if not leads_with_speech and (leading_mention_before_colon(line) or "") in characters:
            raise DraftViolation(
                f"{label} 的台词写法不合法：{line.strip()[:40]!r}；"
                "台词须写成 `@[角色]{台词}`——说话人非空、台词由半角花括号成对包裹，"
                "否则这段会被当成画面描述、台词整句丢失",
                code="dialogue_line_syntax",
                label=label,
                line=idx,
            )
        # 只判记号之外的残余：一行里已识别的台词不因同行另有花括号被连坐。
        rest = speech_line_description(parts)
        if "{" not in rest and "}" not in rest:
            continue
        excerpt = line.strip()[:40]
        if rest.count("{") != rest.count("}"):
            raise DraftViolation(f"{label} 有未闭合的花括号：{excerpt!r}", code="unclosed_brace", label=label, line=idx)
        raise DraftViolation(
            f"{label} 在画面描述里使用了花括号：{excerpt!r}；"
            "花括号是发声保留语法，台词写作 `@[角色]{台词}`、画外音写作 `{画外音}`，"
            "说话人须非空、花括号不得嵌套",
            code="braces_in_description",
            label=label,
            line=idx,
        )


def _has_description_line(text: str) -> bool:
    """该单元是否有画面描述：某一行剥掉全部发声记号后仍有非空文本。"""
    return any(strip_speech_marks(line).strip() for line in text.splitlines())


def dialogue_speakers(text: str) -> list[str]:
    """按出现顺序取出台词记号的说话人（去重）——登记校验据此判说话人是否为登记角色。

    说话人取自 ``split_speech_line``，已在解析器入口归一到资产名比对坐标系
    （``lib.reference_video.text_parser`` 的 ``_normalize_source``），与资产表归一后的 key
    同形，本函数直接使用该结果，不再额外归一。
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

    台词与说话人已在解析器入口归一到 NFC（``lib.reference_video.text_parser`` 的
    ``_normalize_source``）：源文可能以 NFD 落盘而模型回写 NFC，两种形式肉眼同字、逐字比对
    却不等，保结构 diff 会把纯编码差异判成改写；口播时长估算同样要求 NFC（按词计的语种下
    组合附加符会把一个词拆成多个阅读单位）。归一在解析器一处完成，两个消费方口径天然一致。
    """
    result: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        for mark in line_speech_marks(line):
            result.append(("dialogue" if mark.speaker else "voiceover", mark.speaker, mark.text))
    return result


def validate_unit_text(
    label: str,
    text: str,
    project: dict[str, Any],
    *,
    unit_id: str | None = None,
    max_refs: int | None,
) -> list[ReferenceResource]:
    """校验一个 unit 的正文并机械派生参考图引用。

    覆盖四类阻断违约：正文为空或只有发声记号、引用语法误用（花括号、写坏的引用、缺花
    括号的台词）、``@[名称]`` 未登记（含台词记号的说话人位）、参考图数超模型上限。正文是
    唯一落盘物，派生结果只服务本次校验与能力判定，不写回。
    """
    if not text.strip():
        raise DraftViolation(f"{label} 的正文为空", code="empty_text", label=label)

    # 资产表的 key 归一到比对坐标系后再参与判定：正文一侧已由解析器入口归一，两侧同形才判得准。
    characters = normalize_asset_bucket(project.get(BUCKET_KEY["character"]))
    _assert_line_syntax(label, text, characters)

    # 只有台词与画外音的正文没有可生成的画面：整段非空时上面的空正文检查放不住它。
    if not _has_description_line(text):
        raise DraftViolation(
            f"{label} 没有画面描述；只有台词与画外音的单元没有可生成的画面",
            code="blank_description",
            label=label,
        )

    # 与编辑器预览共用 ``derive_references_from_text``：严格度分流（此处对 missing 与上限一律拒），
    # 派生口径不分流——否则同一份正文在两侧派生出不同的 `图N` 编号。
    refs, missing = derive_references_from_text(text, project)
    if missing:
        raise DraftViolation(
            f"{label} 引用了未登记的资产名: {missing}；资产名必须逐字取自 project.json 三张表",
            code="unregistered_asset",
            label=label,
        )

    bad_speakers = sorted({s for s in dialogue_speakers(text) if s not in characters})
    if bad_speakers:
        raise DraftViolation(
            f"{label} 的台词说话人未登记为角色资产: {bad_speakers}；说话人决定该句台词绑哪段参考音频，必须是登记角色",
            code="unregistered_speaker",
            label=label,
        )

    if max_refs is not None and len(refs) > max_refs:
        raise DraftViolation(
            f"{label} 的参考图数 {len(refs)} 超过模型上限 {max_refs}；请把次要角色融入背景描述（不用 `@` 引用）",
            code="refs_over_limit",
            label=label,
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
    )


def validate_dialogue_load(
    label: str,
    text: str,
    duration_seconds: int,
    language: str | None,
    speech_rate_override: float | None = None,
) -> None:
    """校验该 unit 的台词量念得完：口播估算超出 unit 时长（含宽容系数）即违约。

    时长就是计费，unit 时长在 script_plan 定稿；台词写超了意味着成片必然吞词或抢拍，且这在
    script_plan 阶段是可改的（重拆 unit 或删台词），拖到生成后才发现只能重来。
    ``speech_rate_override`` 是项目级语速覆盖，None 即回退语言默认——与 prompt 构造侧同源，
    prompt 给的下界与此处判的上界始终是同一把尺。
    """
    # language 取自 project.json，可能是非字符串脏数据；非字符串回退 None（按默认语速估算），
    # 与 prompt 构造侧同口径——否则 ``count_reading_units`` 的 ``language.strip()`` 会在一次
    # 已付费的调用之后抛 AttributeError，草稿一并丢失。
    language = language if isinstance(language, str) else None
    # 台词取自 normative_lines，已归一到 NFC：``count_reading_units`` 的 en / vi 分支按
    # ``\b\w+\b`` 数词，NFD 形式下组合附加符不算词字符，一个越南语词会被拆成数个单位
    # （9 词的句子计成 16 个），估算随之虚高、把念得完的 unit 判成超载。
    spoken = sum(estimate_spoken_seconds(line[2], language, speech_rate_override) for line in normative_lines(text))
    budget = duration_seconds * (1 + SPEECH_OVERFLOW_TOLERANCE)
    if spoken > budget:
        raise DraftViolation(
            f"{label} 的台词念完约需 {spoken:.1f} 秒，超过该 unit 的 {duration_seconds} 秒"
            f"（宽容 {SPEECH_OVERFLOW_TOLERANCE:.0%} 后上限 {budget:.1f} 秒）；"
            "请改取更长的时长档、把该 unit 拆开，或精简台词",
            code="dialogue_overload",
            label=label,
        )


def assert_dialogue_preserved(label: str, script_plan_text: str, prompt_authoring_text: str) -> None:
    """prompt_authoring 保结构 diff：发声记号的序列必须与 script_plan 逐字一致。

    prompt_authoring 的职责是提示词编写，台词属于 script_plan 已由用户完成内容确认的内容契约。改词、增删、
    重排一律响亮失败，不静默接受——台词不配画面时正确的出路是报错回到 script_plan，而不是让 prompt_authoring
    自行把台词改成好配的样子。
    """
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
        )
    for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
        if old != new:
            raise DraftViolation(
                f"{label} 第 {index} 条台词被改写（原：{old[1] or '画外音'}「{old[2]}」，"
                f"现：{new[1] or '画外音'}「{new[2]}」）；prompt_authoring 只做提示词编写，台词须逐字保留",
                code="dialogue_rewritten",
                label=label,
            )


__all__ = [
    "SPEECH_OVERFLOW_TOLERANCE",
    "DraftViolation",
    "DraftViolations",
    "assert_dialogue_preserved",
    "collect_violations",
    "dialogue_speakers",
    "normative_lines",
    "render_violation_report",
    "validate_dialogue_load",
    "validate_source_text_anchor",
    "validate_unit_text",
    "violation_items",
]
