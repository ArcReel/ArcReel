"""script_plan / prompt_authoring 产出的草稿：落盘信封、违约报告与晋升口径。

生成一次要付费，产物违约时丢弃重抽既烧钱又不收敛（同一个模型对同一份原文大概率再犯同一
类错）。正式文件保持不动；未满足约束的产物连同逐条违约报告落到同目录的草稿，Agent 用
``open_draft`` 读取、``patch_draft`` 修 ``content``（或去补登记资产、改用登记名），再调晋升工具按
**同一个校验器**全量重判
——过则晋升为正式文件、草稿随之清除，不过则报告刷新、继续改。无收敛轮次上限：每一轮都
由 Agent 带着具体定位在改，不是重抽碰运气。

同一个草稿位还承担第二种用途：**编辑工位**。正式 script_plan 不可用 Write/Edit 直改（它与 Web 端
保存、迁移读改写、重拆分共享一把 per-path 锁，而 Agent 的文件工具取不到这把锁），要改已定稿的
script_plan 就先取回一份草稿、改完走同一条晋升通道写盘。两种用途共用一套信封与晋升口径：来路不同，
但「正文在草稿里、写盘只发生在持锁的晋升侧」这一位相同，分两套只会让 gate 与生成侧各认一半。

草稿装的是**该步模型输出那一层的形状**（LLM 面的形状），不是落盘形状：机器派生的字段
（参考生视频的 ``unit_id`` / ``shots`` / ``references``、drama 的 ``needs_replan``）一律不进草稿，
让 Agent 编辑派生物等于给漂移开口子。

信封形状::

    {"kind": ..., "episode": N, "meta": {..., "schema_version": V},
     "violations": [{"code","label","message"}, ...], "content": {...}}

``violations`` 是上一轮判定的快照，只供 Agent 阅读定位——晋升时一律按 ``content`` 现值重判，
不信任草稿里的这份记录。``meta`` 存重判所需、又无法从项目状态重新导出的上下文：script_plan 的源文
路径（晋升时按整个 ``source/`` 目录重解析会让原文锚的子串判定比产出时更松），以及产出 / 取回
时正式文件的内容指纹（``base_fingerprint``，晋升前的乐观并发基线）。

``meta`` 另有一个不属于重判上下文的键 ``schema_version``：信封自身的版本位。它由
``quarantine_envelope`` 统一写入、由 ``read_quarantine`` 解析成 ``QuarantinedDraft.schema_version``
后从 ``meta`` 里摘掉——消费方读解析结果，不去 ``meta`` 里摸裸键，``meta`` 也就只剩重判上下文
（``draft_revision`` 的取值范围随之不受版本位影响）。缺失即 v1，口径见
``resolve_schema_version``。本模块不带迁移框架：版本位只是改 ``meta`` 键语义时可依据的判据，
没有任何分支按它分流。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.content_digest import prefixed_canonical_json_digest
from lib.draft_violation import DraftViolation, render_violation_report
from lib.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    episode_drafts_dir,
)
from lib.json_io import atomic_write_json, load_json_or_none

#: 草稿的产出来源。``content`` 与该来源那一步的模型输出 schema 同形：参考生视频 script_plan 是
#: ``{units: [{duration_seconds, source_text, text}]}``、prompt_authoring 是 ``{title, units: [{text}]}``，
#: drama script_plan 是 ``{title, scenes: [...]}``（即 ``DramaNormalizedScript`` 去掉机器派生的
#: ``needs_replan``），narration script_plan 是 ``{segments: [...]}``（即 ``NarrationScriptPlanDraft``，
#: 该变体没有机器派生字段，草稿层与落盘层同形）。
QUARANTINE_KIND_SCRIPT_PLAN = "reference_video_script_plan"
QUARANTINE_KIND_PROMPT_AUTHORING = "reference_video_prompt_authoring"
QUARANTINE_KIND_DRAMA_SCRIPT_PLAN = "drama_script_plan"
QUARANTINE_KIND_NARRATION_SCRIPT_PLAN = "narration_script_plan"

_QUARANTINE_FILENAMES: dict[str, str] = {
    QUARANTINE_KIND_SCRIPT_PLAN: REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    QUARANTINE_KIND_PROMPT_AUTHORING: REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
}

#: 全部草稿文件名，供不关心 kind、只需按文件名定位草稿的消费方取用（如资产级联重命名的
#: 改写清单）。从上表派生而非另列一份：新增一种来源只在上表加一行，漏登记会让草稿在改写清单
#: 外静默漂移——草稿承载引用数组与 ``@[名称]`` 正文，漏改后晋升会卡在「引用未登记」上。
QUARANTINE_FILENAMES: frozenset[str] = frozenset(_QUARANTINE_FILENAMES.values())

#: 报告里「改哪个字段」的指引按来源分流：草稿正文的形状各不相同，指引落到不存在的字段名
#: 会把 Agent 引到它改不动的地方。与文件名同表登记，新增一种来源只在本模块加一行。
_QUARANTINE_REPORT_HINTS: dict[str, tuple[str, str]] = {
    QUARANTINE_KIND_SCRIPT_PLAN: (
        "script_plan 拆分",
        "content（按报告字段路径修复；视频单元级字段位于 content.units[i]）",
    ),
    QUARANTINE_KIND_PROMPT_AUTHORING: ("prompt_authoring 提示词编写", "content.units[i].text"),
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: (
        "script_plan 规范化",
        "content（分镜级字段位于 content.scenes[i]）",
    ),
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: (
        "script_plan 分镜拆分",
        "content（分镜级字段位于 content.segments[i]："
        "novel_text / duration_seconds / segment_break / characters_in_segment / scenes / props）",
    ),
}

#: 本代码写出的隔离草稿信封版本，落在 ``meta.schema_version``。改动任一现有 ``meta`` 键的语义
#: 时递增它，届时读侧才有据可依地分辨新旧草稿；只加键、不改既有键语义则不必动。
QUARANTINE_SCHEMA_VERSION = 1

_SCHEMA_VERSION_KEY = "schema_version"

#: 晋升工具名。报告里的处置指引要指名它，写死在这里而非各调用点各写一遍。
PROMOTE_TOOL_NAME = "promote_draft"

#: 取回正式 script_plan 供编辑的工具名。正式 script_plan 与 Web 端保存、迁移、重拆分共享一把 per-path 锁，
#: Agent 的 Write/Edit 在沙箱内跑、取不到这把锁，故对它的修改一律改走「取回草稿 → 改 → 晋升」：
#: 写盘只发生在晋升侧，与另三条路径同一把锁。写禁策略的拒绝消息也要指名它，故同样收在这里。
OPEN_DRAFT_TOOL_NAME = "open_draft"

DOC_TYPE_DRAMA_SCRIPT_PLAN = "drama_script_plan"
DOC_TYPE_NARRATION_SCRIPT_PLAN = "narration_script_plan"
DOC_TYPE_REFERENCE_SCRIPT_PLAN = "reference_script_plan"
DOC_TYPE_REFERENCE_PROMPT_AUTHORING = "reference_prompt_authoring"

DOC_TYPE_TO_QUARANTINE_KIND: dict[str, str] = {
    DOC_TYPE_DRAMA_SCRIPT_PLAN: QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    DOC_TYPE_NARRATION_SCRIPT_PLAN: QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    DOC_TYPE_REFERENCE_SCRIPT_PLAN: QUARANTINE_KIND_SCRIPT_PLAN,
    DOC_TYPE_REFERENCE_PROMPT_AUTHORING: QUARANTINE_KIND_PROMPT_AUTHORING,
}

QUARANTINE_KIND_TO_DOC_TYPE: dict[str, str] = {kind: doc_type for doc_type, kind in DOC_TYPE_TO_QUARANTINE_KIND.items()}


@dataclass(frozen=True)
class QuarantinedDraft:
    """一份读回内存的草稿。``content`` 是待重判的扁平产物，``violations`` 是上一轮的报告快照。

    ``schema_version`` 是解析后的信封版本（缺失即 ``1``，口径见 ``resolve_schema_version``），
    ``meta`` 里不再带这个键：版本位归这一个字段，重判上下文归 ``meta``。
    """

    kind: str
    episode: int
    content: dict[str, Any]
    violations: list[dict[str, Any]]
    meta: dict[str, Any]
    schema_version: int
    path: Path


def resolve_schema_version(meta: dict[str, Any]) -> int:
    """从落盘信封的 ``meta`` 原值解析出 schema 版本。版本位缺失或不可信时按 ``1`` 读。

    这是「哪一版信封」的唯一判据，读取信封的一方走它，其余消费方走
    ``QuarantinedDraft.schema_version``；两者都不各自去读 ``meta["schema_version"]``——口径散开后
    每个调用点都会自己发明一套降级规则。入参是盘上那份 ``meta``，不是 ``QuarantinedDraft.meta``：
    后者已摘掉版本位，传进来恒得 ``1``。

    口径：

    * **缺失** —— 不带版本位的信封解析为 ``1``；这些草稿的语义就是 v1。
    * **非整数**（``bool`` / 浮点 / 字符串数字等）与 **小于 1 的整数** —— 同样解析为 ``1``。
      版本位不可信时唯一确定的事实是「它不是本代码写出来的」，而只存在 v1 一种语义；
      为此拒读会把一份仍能被 Agent 改好再晋升的草稿变成「无草稿」，代价远大于按最早的语义读。
    * **大于 ``QUARANTINE_SCHEMA_VERSION`` 的未来版本** —— 原值返回，既不夹取到当前版本也不拒读。
      本模块不做迁移框架，没有任何分支按这个值分流；提前夹取只会把一份新版草稿伪装成当前版本，
      而真正需要降级读时，该拒读还是该兼容由那一次语义变更决定，不由此处预判。
    """
    version = meta.get(_SCHEMA_VERSION_KEY)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return 1
    return version


def quarantine_envelope(
    kind: str,
    episode: int,
    *,
    content: dict[str, Any],
    violations: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装可落盘的整份信封，顺带盖上当前的 ``meta.schema_version``。

    落盘信封只由这里构造：版本位写在哪一版、盖在哪个键上只此一处，写侧不必逐个记得补。
    传入的 ``meta`` 若自带 ``schema_version``（例如从读回的草稿改一改再写回），一律被当前
    版本覆盖——写出去的这份就是本代码这一版，沿用读到的旧值等于给旧版本贴新语义。
    """
    return {
        "kind": kind,
        "episode": episode,
        "meta": {**(meta or {}), _SCHEMA_VERSION_KEY: QUARANTINE_SCHEMA_VERSION},
        "violations": violations,
        "content": content,
    }


def draft_revision(draft: QuarantinedDraft) -> str:
    """Return the canonical optimistic-concurrency token for persisted draft state.

    摘要覆盖的字段是 ``quarantine_envelope`` 落盘形状去掉版本位后的那一份：``meta`` 取
    ``QuarantinedDraft.meta``（不含 ``schema_version``），令牌的取值范围因此与版本位无关。
    改动落盘形状时这里要一并跟上。
    """
    return prefixed_canonical_json_digest(
        {
            "kind": draft.kind,
            "episode": draft.episode,
            "meta": draft.meta,
            "violations": draft.violations,
            "content": draft.content,
        }
    )


def draft_payload(draft: QuarantinedDraft) -> dict[str, Any]:
    """Return the host-independent draft document exposed by MCP adapters."""
    return {
        "episode": draft.episode,
        "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[draft.kind],
        "content": draft.content,
        "violations": draft.violations,
        "revision": draft_revision(draft),
    }


def quarantine_path(project_path: Path, episode: int, kind: str) -> Path:
    """该集该阶段的草稿路径（与正式 script_plan 同目录）。"""
    return episode_drafts_dir(project_path, episode) / _QUARANTINE_FILENAMES[kind]


def violation_entries(violations: list[DraftViolation]) -> list[dict[str, Any]]:
    """违约异常 → 落盘 / 呈现用的结构化条目（违约类 + unit 定位 + 消息 + 可选行号）。"""
    entries: list[dict[str, Any]] = []
    for violation in violations:
        entry: dict[str, Any] = {
            "code": violation.code,
            "label": violation.label,
            "message": str(violation),
            "line": violation.line,
        }
        if violation.locations:
            entry["locations"] = list(violation.locations)
        if violation.reason is not None:
            entry["reason"] = violation.reason
        if violation.action is not None:
            entry["action"] = violation.action
        entries.append(entry)
    return entries


def write_quarantine(
    project_path: Path,
    episode: int,
    kind: str,
    *,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any] | None = None,
) -> Path:
    """把未满足约束的产物与报告写入草稿（原子写，整份覆盖），返回草稿路径。

    整份覆盖而非合并：重抽或重跑晋升产生的是一份新产物，与上一轮的残留合并只会让 Agent 对着
    半新半旧的正文改。目录可能尚不存在（该集从未产出过 script_plan），故先建目录。
    """
    path = quarantine_path(project_path, episode, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        quarantine_envelope(
            kind,
            episode,
            content=content,
            violations=violation_entries(violations),
            meta=meta,
        ),
    )
    return path


def read_quarantine(project_path: Path, episode: int, kind: str) -> QuarantinedDraft | None:
    """读回草稿；文件缺失 / 非法 JSON / 信封形状坏时返回 None。

    形状坏按「无草稿」处理而非抛错：存量文件或异常中断可能留下非法 JSON。
    调用方据此给出「重新拆分」而非内部错误——但 ``exists`` 仍为真，gate 与生成侧照常
    阻塞，坏掉的草稿不会被当成「没有草稿」而放行。

    ``kind`` / ``episode`` 须与所请求的这份草稿一致，缺失或对不上同样按形状坏处理：不校验就
    等于把这两个字段解析出来又丢掉，一份从别集拷过来的信封会带着它自己的 ``meta.source``
    过锚校验，再按本集的 unit_id 重建、覆盖本集的正式 script_plan。
    """
    path = quarantine_path(project_path, episode, kind)
    data = load_json_or_none(path)
    if not isinstance(data, dict):
        return None
    content = data.get("content")
    if not isinstance(content, dict):
        return None
    if data.get("kind") != kind:
        return None
    try:
        if int(data["episode"]) != episode:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    raw_violations = data.get("violations")
    violations = [v for v in raw_violations if isinstance(v, dict)] if isinstance(raw_violations, list) else []
    raw_meta = data.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    return QuarantinedDraft(
        kind=kind,
        episode=episode,
        content=content,
        violations=violations,
        meta={key: value for key, value in meta.items() if key != _SCHEMA_VERSION_KEY},
        schema_version=resolve_schema_version(meta),
        path=path,
    )


def quarantine_exists(project_path: Path, episode: int, kind: str) -> bool:
    """该集该阶段是否有草稿在场——gate 阻塞与生成侧拒绝的判据，不解析内容。"""
    return quarantine_path(project_path, episode, kind).exists()


def clear_quarantine(project_path: Path, episode: int, kind: str) -> None:
    """晋升成功后清除草稿。缺失时静默——晋升可能来自一次直接重跑，本就没有草稿要清。"""
    quarantine_path(project_path, episode, kind).unlink(missing_ok=True)


def render_report(draft: Path, kind: str, violations: list[DraftViolation], *, episode: int) -> str:
    """渲染回给 Agent 的违约报告：逐条定位 + 按处置路径写的修复指引。

    指引写「改哪个文件的哪个字段、改完调什么」而非泛泛的「请修正」：处置路径是本机制的全部
    要点，Agent 若不知道产物还在盘上，就会退回重抽。
    """
    stage, field = _QUARANTINE_REPORT_HINTS[kind]
    doc_type = QUARANTINE_KIND_TO_DOC_TYPE[kind]
    return (
        f"❌ {stage}产出有 {len(violations)} 处违约，已保存为待修复草稿（正式文件未被改动）：{draft}\n\n"
        f"{render_violation_report(violations)}\n\n"
        f'处置：调用 open_draft({{"episode": {episode}, "doc_type": "{doc_type}"}}) 读取正文与 revision；'
        f"修正 {field} 后用 patch_draft 携带 base_revision 提交；"
        "若违约是「资产名未登记」，也可改为在 project.json 登记该资产、或改用已登记的名称。\n"
        f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}, "doc_type": "{doc_type}", '
        '"base_revision": "<patch_draft 返回的 revision>"}) '
        "重新全量校验并晋升为正式文件；"
        "仍有违约时会返回刷新后的报告，可继续修改再晋升，无轮次上限。"
    )


def quarantine_and_report(
    project_path: Path,
    episode: int,
    kind: str,
    *,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any] | None = None,
) -> str:
    """违约处置的单一出口：落草稿 + 渲染报告，返回回给 Agent 的报告文本。

    落盘与报告成对出现——报告要指名草稿路径，路径由落盘决定；两步分开写在各调用点，迟早会
    出现「报告说去改某个文件、而那个文件没被写出来」的分叉。
    """
    path = write_quarantine(project_path, episode, kind, content=content, violations=violations, meta=meta)
    return render_report(path, kind, violations, episode=episode)


__all__ = [
    "DOC_TYPE_DRAMA_SCRIPT_PLAN",
    "DOC_TYPE_NARRATION_SCRIPT_PLAN",
    "DOC_TYPE_REFERENCE_PROMPT_AUTHORING",
    "DOC_TYPE_REFERENCE_SCRIPT_PLAN",
    "DOC_TYPE_TO_QUARANTINE_KIND",
    "OPEN_DRAFT_TOOL_NAME",
    "PROMOTE_TOOL_NAME",
    "QUARANTINE_FILENAMES",
    "QUARANTINE_KIND_DRAMA_SCRIPT_PLAN",
    "QUARANTINE_KIND_NARRATION_SCRIPT_PLAN",
    "QUARANTINE_KIND_PROMPT_AUTHORING",
    "QUARANTINE_KIND_SCRIPT_PLAN",
    "QUARANTINE_SCHEMA_VERSION",
    "QuarantinedDraft",
    "clear_quarantine",
    "draft_payload",
    "draft_revision",
    "quarantine_and_report",
    "quarantine_envelope",
    "quarantine_exists",
    "quarantine_path",
    "read_quarantine",
    "render_report",
    "resolve_schema_version",
    "violation_entries",
    "write_quarantine",
]
