"""参考生视频 step1 / step2 产出的机械校验（书写层扁平文本）。

LLM 产出与人在编辑器写的是同一种格式，校验因此也落在同一份文本上；本模块是「parser
后校验」这一层的落点：schema 已卡死枚举与外层结构，剩下的语法与内容约束在这里逐 unit
判定，任一违约 fail-loud 抛 :class:`DraftViolation`，不把违规产物当成功结果写盘。

与编辑器侧（人写）的容忍口径分流：``lib.reference_video.script_preview`` 对同样的文本只
出 warning、照常落盘——那里有作者意图要保护；本模块面向机器产物，没有意图可保护，一律拒。
"""

from __future__ import annotations

import re
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.reference_video.shot_parser import (
    match_dialogue_line,
    match_voiceover_line,
    parse_prompt,
    resolve_references,
    strip_shot_header,
)
from lib.reference_video.writing_syntax import MAX_SHOTS_PER_UNIT
from lib.script_models import ReferenceResource, Shot
from lib.speech_rate import estimate_spoken_seconds

#: 台词口播时长相对 unit 时长的宽容系数：估算超出 unit 时长这个比例才判超载。
#: 语速是统计估算（``lib.speech_rate``），逐字计数与真实配音节奏必然有出入；不留宽容会
#: 把「刚好写满」的正常产出判违约。与 drama 保存期上界 warning 的 20% 同量级——两处都是
#: 「同一套语速估算 vs 已定时长」的比对，宽容度没有理由不同。
SPEECH_OVERFLOW_TOLERANCE = 0.20


class DraftViolation(ValueError):
    """书写层产出违约。消息含 unit 定位与违约类，供工具错误信封原样回传给 agent。"""


def _normalize_for_anchor(text: str) -> str:
    """把连续空白折叠为单个空格，只消除空白的类型 / 数量差异，不删除空白本身。

    与 narration 覆盖校验的 ``_normalize_for_coverage`` 同一口径：模型复制原文时换行与
    缩进的还原不可靠，但删字改字必须被抓住。
    """
    return re.sub(r"\s+", " ", text).strip()


def validate_source_text_anchor(label: str, source_text: str, novel_text: str) -> None:
    """校验 ``source_text`` 是源文的逐字子串（空白归一后）。

    step1 的 unit 边界要能追溯回原文，锚失配意味着模型在拆分时改写或杜撰了原文——这是内容
    层的根本违约，比任何下游画面问题都更早需要被拦下。只判子串、不判顺序与完整覆盖：unit
    是画面单元不是朗读单元，允许原文中的对话提示语、转述段落不进任何 unit 的锚。
    """
    anchor = _normalize_for_anchor(source_text)
    if not anchor:
        raise DraftViolation(f"{label} 的 source_text 为空：每个 unit 必须摘录其所依据的原文片段作为追溯锚")
    if anchor not in _normalize_for_anchor(novel_text):
        raise DraftViolation(
            f"{label} 的 source_text 不是小说原文的逐字片段（存在改写、翻译或杜撰）："
            f"{source_text.strip()[:40]!r}；请原样复制原文，不要转述"
        )


def _content_lines(text: str) -> list[str]:
    """逐行剥掉 ``镜头N：`` header 后的正文行。

    与 ``extract_mentions`` 同口径：写在 header 同一行的台词在 ``parse_prompt`` 切分后
    就是独立的规范行，判定必须在剥 header 之后进行，否则同一行在切分前后两种结论。
    """
    return [strip_shot_header(line) for line in text.splitlines()]


def _assert_brace_syntax(label: str, text: str) -> None:
    """逐行判花括号用法：只允许整行包裹的台词行 / 画外音行，其余出现花括号即违约。"""
    for line in _content_lines(text):
        if "{" not in line and "}" not in line:
            continue
        if match_dialogue_line(line) is not None or match_voiceover_line(line) is not None:
            continue
        excerpt = line.strip()[:40]
        if line.count("{") != line.count("}"):
            raise DraftViolation(f"{label} 有未闭合的花括号：{excerpt!r}")
        raise DraftViolation(
            f"{label} 在画面描述行里使用了花括号：{excerpt!r}；"
            "花括号是台词保留语法，台词须独立成行写作 `@[角色]：{台词}` 或 `{画外音}`"
        )


def dialogue_speakers(text: str) -> list[str]:
    """按出现顺序取出规范台词行的说话人（去重）——音色声明与登记校验共用同一口径。"""
    seen: set[str] = set()
    speakers: list[str] = []
    for line in _content_lines(text):
        matched = match_dialogue_line(line)
        if matched is None:
            continue
        speaker = matched[0]
        if speaker not in seen:
            seen.add(speaker)
            speakers.append(speaker)
    return speakers


def normative_lines(text: str) -> list[tuple[str, str, str]]:
    """按出现顺序取出全部规范发声行：``(kind, speaker, 台词)``，``kind`` 为 dialogue / voiceover。

    step2 的保结构 diff 以此为比对项：画面描述可自由展开，发声行必须逐字不变。
    """
    result: list[tuple[str, str, str]] = []
    for line in _content_lines(text):
        dialogue = match_dialogue_line(line)
        if dialogue is not None:
            result.append(("dialogue", dialogue[0], dialogue[1]))
            continue
        voiceover = match_voiceover_line(line)
        if voiceover is not None:
            result.append(("voiceover", "", voiceover))
    return result


def validate_unit_text(
    label: str,
    text: str,
    project: dict[str, Any],
    *,
    max_refs: int | None,
) -> tuple[list[Shot], list[ReferenceResource]]:
    """校验一个 unit 的正文并机械派生 ``(shots, references)``。

    覆盖四类阻断违约：正文为空 / 镜头行数超上限、花括号语法误用、``@[名称]`` 未登记
    （含台词行的说话人位）、references 超模型上限。派生结果即落盘值——校验与派生同一次
    遍历，杜绝「校验看到的文本」与「落盘的 references」出自两套解析。
    """
    if not text.strip():
        raise DraftViolation(f"{label} 的正文为空")

    _assert_brace_syntax(label, text)

    shots, mentions = parse_prompt(text)
    if len(shots) > MAX_SHOTS_PER_UNIT:
        raise DraftViolation(
            f"{label} 有 {len(shots)} 个镜头行，超过单 unit 上限 {MAX_SHOTS_PER_UNIT}；"
            "请把多出的镜头按叙事顺序拆到新的 unit"
        )

    refs, missing = resolve_references(mentions, project)
    if missing:
        raise DraftViolation(f"{label} 引用了未登记的资产名: {missing}；资产名必须逐字取自 project.json 三张表")

    characters = project.get(BUCKET_KEY["character"]) or {}
    bad_speakers = sorted({s for s in dialogue_speakers(text) if s not in characters})
    if bad_speakers:
        raise DraftViolation(
            f"{label} 的台词行说话人未登记为角色资产: {bad_speakers}；说话人决定该句台词绑哪段参考音频，必须是登记角色"
        )

    if max_refs is not None and len(refs) > max_refs:
        raise DraftViolation(
            f"{label} 的 references 数 {len(refs)} 超过模型上限 {max_refs}；请把次要角色融入背景描述（不用 `@` 引用）"
        )
    return shots, refs


def validate_dialogue_load(label: str, text: str, duration_seconds: int, language: str | None) -> None:
    """校验该 unit 的台词量念得完：口播估算超出 unit 时长（含宽容系数）即违约。

    时长就是计费，unit 时长在 step1 定稿；台词写超了意味着成片必然吞词或抢拍，且这在
    step1 阶段是可改的（重拆 unit 或删台词），拖到生成后才发现只能重来。
    """
    spoken = sum(estimate_spoken_seconds(line[2], language) for line in normative_lines(text))
    budget = duration_seconds * (1 + SPEECH_OVERFLOW_TOLERANCE)
    if spoken > budget:
        raise DraftViolation(
            f"{label} 的台词念完约需 {spoken:.1f} 秒，超过该 unit 的 {duration_seconds} 秒"
            f"（宽容 {SPEECH_OVERFLOW_TOLERANCE:.0%} 后上限 {budget:.1f} 秒）；"
            "请改取更长的时长档、把该 unit 拆开，或精简台词"
        )


def assert_dialogue_preserved(label: str, step1_text: str, step2_text: str) -> None:
    """step2 保结构 diff：规范发声行的序列必须与 step1 逐字一致。

    step2 的职责是视觉展开，台词属于 step1 已与用户在 gate 上确认过的内容契约。改词、增删、
    重排一律响亮失败，不静默接受——台词不配画面时正确的出路是报错回到 step1，而不是让 step2
    自行把台词改成好配的样子。
    """
    before = normative_lines(step1_text)
    after = normative_lines(step2_text)
    if before == after:
        return
    if len(before) != len(after):
        raise DraftViolation(
            f"{label} 的台词行数被改动（step1 有 {len(before)} 行，step2 产出 {len(after)} 行）；"
            "step2 只做视觉展开，台词行须逐字保留"
        )
    for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
        if old != new:
            raise DraftViolation(
                f"{label} 第 {index} 条台词被改写（原：{old[1] or '画外音'}「{old[2]}」，"
                f"现：{new[1] or '画外音'}「{new[2]}」）；step2 只做视觉展开，台词行须逐字保留"
            )


__all__ = [
    "SPEECH_OVERFLOW_TOLERANCE",
    "DraftViolation",
    "assert_dialogue_preserved",
    "dialogue_speakers",
    "normative_lines",
    "validate_dialogue_load",
    "validate_source_text_anchor",
    "validate_unit_text",
]
