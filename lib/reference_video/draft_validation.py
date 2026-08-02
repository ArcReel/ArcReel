"""参考生视频 step1 / step2 产出的机械校验（书写层扁平文本）。

LLM 产出与人在编辑器写的是同一种格式，校验因此也落在同一份文本上；本模块是「parser
后校验」这一层的落点：schema 已卡死枚举与外层结构，剩下的语法与内容约束在这里逐 unit
判定，任一违约 fail-loud 抛 :class:`DraftViolation`，不把违规产物当成功结果写盘。

「不当成功结果写盘」不等于丢弃：调用侧用 :func:`collect_violations` 把逐 unit 的违约收齐成
一份报告，产物落隔离草稿（``lib.reference_video.quarantine``）等 agent 修复后重判，不重抽。
每条违约带 ``code``（违约类）与 ``label``（unit 定位），报告因此可逐条定位、可按类断言。

与编辑器侧（人写）的容忍口径分流：``lib.reference_video.script_preview`` 对同样的文本只
出 warning、照常落盘——那里有作者意图要保护；本模块面向机器产物，没有意图可保护，一律拒。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.reference_video.shot_parser import (
    derive_references_from_text,
    find_malformed_mention,
    leading_mention_before_colon,
    match_dialogue_line,
    match_voiceover_line,
    parse_prompt,
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
    """书写层产出违约。消息含 unit 定位与修复出路，供工具错误信封原样回传给 agent。

    ``code`` 是违约类的机读标识，``label`` 是 unit 定位（``unit E1U02`` 一类的前缀）：消息本身
    面向 agent、措辞可改，报告的分组与测试的按类断言不该挂在措辞上。两者均可为空——异常在
    模块外被构造时（如生成侧对镜头数对账的补充判定）只有消息。

    ``line`` 是该 unit 正文内 0-based 的原始行号（``text.splitlines()`` 坐标系，与前端
    ``toScriptLines`` 的 ``sourceLine`` 同一坐标系），仅在校验发生于具体某一行时才有意义
    （如语法误用）；unit 级、无自然行归属的违约（缺台词量超载、引用未登记等）留空，供
    呈现层区分「行内锚定」与「落卡内聚合区」两条路径。
    """

    def __init__(self, message: str, *, code: str = "", label: str = "", line: int | None = None):
        super().__init__(message)
        self.code = code
        self.label = label
        self.line = line


class DraftViolations(DraftViolation):
    """一次校验收集到的多条违约。消息即逐条报告，``items`` 保留结构化条目。

    继承 :class:`DraftViolation` 而非另立类型：既有调用方按 ``DraftViolation`` 捕获与断言，
    聚合体走同一分支才不会在「一条」与「多条」之间分叉出两套处置路径。
    """

    def __init__(self, items: Sequence[DraftViolation]):
        super().__init__(render_violation_report(items), code="multiple", label="")
        self.items: list[DraftViolation] = list(items)


def violation_items(exc: DraftViolation) -> list[DraftViolation]:
    """把单条或聚合的违约一律摊平成条目列表，供报告渲染与隔离草稿落盘取用。"""
    return list(exc.items) if isinstance(exc, DraftViolations) else [exc]


def collect_violations(checks: Iterable[Callable[[], Any]]) -> list[DraftViolation]:
    """依次执行各校验，收集 :class:`DraftViolation` 而不在首个违约处中断。

    单个校验函数内部仍是首个违约即抛（各判定共用一次遍历、后续判定以前面的结论为前提），
    故一次调用最多贡献一条；把「每 unit 的锚 / 正文 / 台词量」三个入口分别传进来，报告就能
    覆盖到所有 unit 而不是停在第一个坏 unit 上——agent 一轮就能看全要改什么。

    只吞 ``DraftViolation``：其余异常（解析器内部错误、脏数据引发的类型错误）照常上抛，
    不被伪装成一条内容违约。
    """
    found: list[DraftViolation] = []
    for check in checks:
        try:
            check()
        except DraftViolation as exc:
            found.extend(violation_items(exc))
    return found


def render_violation_report(violations: Sequence[DraftViolation]) -> str:
    """把违约条目渲染成逐条编号的报告文本（一行一条，带违约类标注）。"""
    lines: list[str] = []
    for index, violation in enumerate(violations, start=1):
        suffix = f"[{violation.code}] " if violation.code else ""
        lines.append(f"{index}. {suffix}{violation}")
    return "\n".join(lines)


def _nfc(text: str) -> str:
    """Unicode NFC 归一：与 ``lib.episode_ledger.normalize_source_text`` 定义的源文坐标系一致。"""
    return unicodedata.normalize("NFC", text)


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

    step1 的 unit 边界要能追溯回原文，锚失配意味着模型在拆分时改写或杜撰了原文——这是内容
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


def _content_lines(text: str) -> list[str]:
    """逐行剥掉 ``镜头N：`` header 后的正文行。

    与 ``extract_mentions`` 同口径：写在 header 同一行的台词在 ``parse_prompt`` 切分后
    就是独立的规范行，判定必须在剥 header 之后进行，否则同一行在切分前后两种结论。
    """
    return [strip_shot_header(line) for line in text.splitlines()]


#: 全角花括号。语法只认半角，但中文输入法下模型很容易写出全角形；行里出现全角花括号时
#: ``match_dialogue_line`` 不匹配，该行会被当成画面描述放行——台词静默降级成描述、说话人
#: 反而被派生成参考图。故在语法判定处显式识别并拒绝，不静默、也不代模型改写。
_FULLWIDTH_BRACES = "｛｝"


def _assert_line_syntax(label: str, text: str, characters: dict[str, Any]) -> None:
    """逐行判书写层语法：花括号用法、写坏的 ``@[`` 引用、缺花括号的台词行。

    三类共性是「解析器不报错、但派生结果与作者意图相反」：台词降级成画面描述、说话人反被
    派生成参考图、坏 token 原样进供应商请求。机器产物没有作者意图可保护，一律在语法判定处
    响亮拒绝，不静默、也不代模型改写。
    """
    for idx, line in enumerate(_content_lines(text)):
        if any(ch in line for ch in _FULLWIDTH_BRACES):
            raise DraftViolation(
                f"{label} 使用了全角花括号：{line.strip()[:40]!r}；"
                "台词与画外音的花括号必须是半角 `{}`，全角形不会被识别为台词行",
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
        is_dialogue = match_dialogue_line(line) is not None
        # 只有登记角色 + 冒号才判成写坏的台词：场景 / 道具做小标题（``@[酒馆]：木门被风吹开``）
        # 是合法的画面描述写法，按同一形态一概判违约会把正常的 step1 产出拒掉。
        if not is_dialogue and (leading_mention_before_colon(line) or "") in characters:
            raise DraftViolation(
                f"{label} 的台词行写法不合法：{line.strip()[:40]!r}；"
                "台词须写成 `@[角色]：{台词}`——说话人非空、台词由半角花括号整体包裹，"
                "否则这行会被当成画面描述、台词整句丢失",
                code="dialogue_line_syntax",
                label=label,
                line=idx,
            )
        if "{" not in line and "}" not in line:
            continue
        if is_dialogue or match_voiceover_line(line) is not None:
            continue
        excerpt = line.strip()[:40]
        if line.count("{") != line.count("}"):
            raise DraftViolation(f"{label} 有未闭合的花括号：{excerpt!r}", code="unclosed_brace", label=label, line=idx)
        raise DraftViolation(
            f"{label} 在画面描述行里使用了花括号：{excerpt!r}；"
            "花括号是台词保留语法，台词须独立成行写作 `@[角色]：{台词}` 或 `{画外音}`",
            code="braces_in_description",
            label=label,
            line=idx,
        )


def _has_description_line(shot_text: str) -> bool:
    """该镜头是否有画面描述行：非空、且既不是规范台词行也不是画外音行。"""
    for line in _content_lines(shot_text):
        if not line.strip():
            continue
        if match_dialogue_line(line) is None and match_voiceover_line(line) is None:
            return True
    return False


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

    台词与说话人一律归一到 NFC 后返回：源文可能以 NFD 落盘而模型回写 NFC，两种形式肉眼
    同字，逐字比对却不等；口播时长估算同样要求 NFC（按词计的语种下组合附加符会把一个词
    拆成多个阅读单位）。归一放在这一处，两个消费方口径天然一致。
    """
    result: list[tuple[str, str, str]] = []
    for line in _content_lines(text):
        dialogue = match_dialogue_line(line)
        if dialogue is not None:
            result.append(("dialogue", _nfc(dialogue[0]), _nfc(dialogue[1])))
            continue
        voiceover = match_voiceover_line(line)
        if voiceover is not None:
            result.append(("voiceover", "", _nfc(voiceover)))
    return result


def validate_unit_text(
    label: str,
    text: str,
    project: dict[str, Any],
    *,
    max_refs: int | None,
) -> tuple[list[Shot], list[ReferenceResource]]:
    """校验一个 unit 的正文并机械派生 ``(shots, references)``。

    覆盖四类阻断违约：正文为空 / 单镜头正文为空 / 镜头行数超上限、书写层语法误用（花括号、
    写坏的引用、缺花括号的台词行）、``@[名称]`` 未登记（含台词行的说话人位）、references
    超模型上限。派生结果即落盘值——校验与派生同一次遍历，杜绝「校验看到的文本」与「落盘的
    references」出自两套解析。
    """
    if not text.strip():
        raise DraftViolation(f"{label} 的正文为空", code="empty_text", label=label)

    characters = project.get(BUCKET_KEY["character"]) or {}
    _assert_line_syntax(label, text, characters)

    shots, _mentions = parse_prompt(text)
    # 镜头缺画面描述（``镜头1：`` 后无正文，或该镜头只有台词行 / 画外音行）：整段非空时上面的
    # 空正文检查放不住它，而画面正是 unit 要生成的东西。单镜头 unit 因此落盘后进不了队（视频
    # prompt 为空），多镜头 unit 则让 step2 对着空白镜头自行编内容。
    blank_shots = [index for index, shot in enumerate(shots, start=1) if not _has_description_line(shot.text)]
    if blank_shots:
        raise DraftViolation(
            f"{label} 的镜头 {blank_shots} 没有画面描述；"
            "每个 `镜头N：` 都要写该镜头拍什么，只有台词行 / 画外音行的镜头没有可生成的画面",
            code="blank_shot",
            label=label,
        )
    if len(shots) > MAX_SHOTS_PER_UNIT:
        raise DraftViolation(
            f"{label} 有 {len(shots)} 个镜头行，超过单 unit 上限 {MAX_SHOTS_PER_UNIT}；"
            "请把多出的镜头按叙事顺序拆到新的 unit",
            code="too_many_shots",
            label=label,
        )

    # 与编辑器回写共用 ``derive_references_from_text``：严格度分流（此处对 missing 与上限一律拒），
    # 派生口径不分流——否则同一份正文在两侧派生出不同的 `[图N]` 编号。
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
            f"{label} 的台词行说话人未登记为角色资产: {bad_speakers}；说话人决定该句台词绑哪段参考音频，必须是登记角色",
            code="unregistered_speaker",
            label=label,
        )

    if max_refs is not None and len(refs) > max_refs:
        raise DraftViolation(
            f"{label} 的 references 数 {len(refs)} 超过模型上限 {max_refs}；请把次要角色融入背景描述（不用 `@` 引用）",
            code="refs_over_limit",
            label=label,
        )
    return shots, refs


def validate_dialogue_load(label: str, text: str, duration_seconds: int, language: str | None) -> None:
    """校验该 unit 的台词量念得完：口播估算超出 unit 时长（含宽容系数）即违约。

    时长就是计费，unit 时长在 step1 定稿；台词写超了意味着成片必然吞词或抢拍，且这在
    step1 阶段是可改的（重拆 unit 或删台词），拖到生成后才发现只能重来。
    """
    # language 取自 project.json，可能是非字符串脏数据；非字符串回退 None（按默认语速估算），
    # 与 prompt 构造侧同口径——否则 ``count_reading_units`` 的 ``language.strip()`` 会在一次
    # 已付费的调用之后抛 AttributeError，草稿一并丢失。
    language = language if isinstance(language, str) else None
    # 台词取自 normative_lines，已归一到 NFC：``count_reading_units`` 的 en / vi 分支按
    # ``\b\w+\b`` 数词，NFD 形式下组合附加符不算词字符，一个越南语词会被拆成数个单位
    # （9 词的句子计成 16 个），估算随之虚高、把念得完的 unit 判成超载。
    spoken = sum(estimate_spoken_seconds(line[2], language) for line in normative_lines(text))
    budget = duration_seconds * (1 + SPEECH_OVERFLOW_TOLERANCE)
    if spoken > budget:
        raise DraftViolation(
            f"{label} 的台词念完约需 {spoken:.1f} 秒，超过该 unit 的 {duration_seconds} 秒"
            f"（宽容 {SPEECH_OVERFLOW_TOLERANCE:.0%} 后上限 {budget:.1f} 秒）；"
            "请改取更长的时长档、把该 unit 拆开，或精简台词",
            code="dialogue_overload",
            label=label,
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
            "step2 只做视觉展开，台词行须逐字保留",
            code="dialogue_line_count_changed",
            label=label,
        )
    for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
        if old != new:
            raise DraftViolation(
                f"{label} 第 {index} 条台词被改写（原：{old[1] or '画外音'}「{old[2]}」，"
                f"现：{new[1] or '画外音'}「{new[2]}」）；step2 只做视觉展开，台词行须逐字保留",
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
