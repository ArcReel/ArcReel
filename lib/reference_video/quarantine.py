"""违约的 step1 / step2 产出的隔离草稿：落盘信封、违约报告与晋升口径。

生成一次要付费，产物违约时丢弃重抽既烧钱又不收敛（同一个模型对同一份原文大概率再犯同一
类错）。改为：正式文件一步不动，违约产物连同逐条违约报告落到同目录的隔离草稿，agent 用文件
工具就地修 ``content``（或去补登记资产、改用登记名），再调晋升工具按**同一个校验器**全量重判
——过则晋升为正式文件、隔离草稿随之清除，不过则报告刷新、继续改。无收敛轮次上限：每一轮都
由 agent 带着具体定位在改，不是重抽碰运气。

隔离草稿装的是**扁平书写层产物**（LLM 面的形状），不是落盘形状：结构字段（``unit_id`` /
``shots`` / ``references``）一律机器派生，让 agent 编辑派生物等于给漂移开口子。

信封形状::

    {"kind": ..., "episode": N, "meta": {...}, "violations": [{"code","label","message"}, ...], "content": {...}}

``violations`` 是上一轮判定的快照，只供 agent 阅读定位——晋升时一律按 ``content`` 现值重判，
不信任草稿里的这份记录。``meta`` 存重判所需、又无法从项目状态重新导出的产出上下文（step1 的
源文路径：晋升时按整个 ``source/`` 目录重解析会让原文锚的子串判定比产出时更松）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_STEP2_QUARANTINE_FILENAME,
    episode_drafts_dir,
)
from lib.json_io import atomic_write_json, load_json_or_none
from lib.reference_video.draft_validation import DraftViolation, render_violation_report

#: 隔离草稿的两种产出来源。step1 的 ``content`` 是 ``{units: [{duration_seconds, source_text, text}]}``，
#: step2 的是 ``{title, units: [{text}]}``——各自与该阶段模型的输出 schema 同形。
QUARANTINE_KIND_STEP1 = "reference_video_step1"
QUARANTINE_KIND_STEP2 = "reference_video_step2"

_QUARANTINE_FILENAMES: dict[str, str] = {
    QUARANTINE_KIND_STEP1: REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME,
    QUARANTINE_KIND_STEP2: REFERENCE_VIDEO_STEP2_QUARANTINE_FILENAME,
}

#: 晋升工具名。报告里的处置指引要指名它，写死在这里而非各调用点各写一遍。
PROMOTE_TOOL_NAME = "validate_and_promote_reference_draft"

#: 取回正式 step1 供编辑的工具名。正式 step1 与 Web 端保存、迁移、重拆分共享一把 per-path 锁，
#: agent 的 Write/Edit 在沙箱内跑、取不到这把锁，故对它的修改一律改走「取回草稿 → 改 → 晋升」：
#: 写盘只发生在晋升侧，与另三条路径同一把锁。写禁策略的拒绝消息也要指名它，故同样收在这里。
STEP1_EDIT_TOOL_NAME = "open_reference_step1_for_edit"


@dataclass(frozen=True)
class QuarantinedDraft:
    """一份读回内存的隔离草稿。``content`` 是待重判的扁平产物，``violations`` 是上一轮的报告快照。"""

    kind: str
    episode: int
    content: dict[str, Any]
    violations: list[dict[str, Any]]
    meta: dict[str, Any]
    path: Path


def quarantine_path(project_path: Path, episode: int, kind: str) -> Path:
    """该集该阶段的隔离草稿路径（与正式 step1 同目录）。"""
    return episode_drafts_dir(project_path, episode) / _QUARANTINE_FILENAMES[kind]


def violation_entries(violations: list[DraftViolation]) -> list[dict[str, Any]]:
    """违约异常 → 落盘 / 呈现用的结构化条目（违约类 + unit 定位 + 消息 + 可选行号）。"""
    return [{"code": v.code, "label": v.label, "message": str(v), "line": v.line} for v in violations]


def write_quarantine(
    project_path: Path,
    episode: int,
    kind: str,
    *,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any] | None = None,
) -> Path:
    """把违约产物与报告写入隔离草稿（原子写，整份覆盖），返回草稿路径。

    整份覆盖而非合并：重抽或重跑晋升产生的是一份新产物，与上一轮的残留合并只会让 agent 对着
    半新半旧的正文改。目录可能尚不存在（该集从未产出过 step1），故先建目录。
    """
    path = quarantine_path(project_path, episode, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "kind": kind,
            "episode": episode,
            "meta": meta or {},
            "violations": violation_entries(violations),
            "content": content,
        },
    )
    return path


def read_quarantine(project_path: Path, episode: int, kind: str) -> QuarantinedDraft | None:
    """读回隔离草稿；文件缺失 / 非法 JSON / 信封形状坏时返回 None。

    形状坏按「无隔离草稿」处理而非抛错：这份文件正是给 agent 手改的，改坏 JSON 是可预期的
    中间态。调用方据此给出「重新拆分」而非内部错误——但 ``exists`` 仍为真，gate 与生成侧照常
    阻塞，坏掉的隔离草稿不会被当成「没有隔离草稿」而放行。

    ``kind`` / ``episode`` 须与所请求的这份草稿一致，缺失或对不上同样按形状坏处理：不校验就
    等于把这两个字段解析出来又丢掉，一份从别集拷过来的信封会带着它自己的 ``meta.source``
    过锚校验，再按本集的 unit_id 重建、覆盖本集的正式 step1。
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
    return QuarantinedDraft(
        kind=kind,
        episode=episode,
        content=content,
        violations=violations,
        meta=raw_meta if isinstance(raw_meta, dict) else {},
        path=path,
    )


def quarantine_exists(project_path: Path, episode: int, kind: str) -> bool:
    """该集该阶段是否有隔离草稿在场——gate 阻塞与生成侧拒绝的判据，不解析内容。"""
    return quarantine_path(project_path, episode, kind).exists()


def clear_quarantine(project_path: Path, episode: int, kind: str) -> None:
    """晋升成功后清除隔离草稿。缺失时静默——晋升可能来自一次直接重跑，本就没有草稿要清。"""
    quarantine_path(project_path, episode, kind).unlink(missing_ok=True)


def render_report(draft: Path, kind: str, violations: list[DraftViolation], *, episode: int) -> str:
    """渲染回给 agent 的违约报告：逐条定位 + 按处置路径写的修复指引。

    指引写「改哪个文件的哪个字段、改完调什么」而非泛泛的「请修正」：处置路径是本机制的全部
    要点，agent 若不知道产物还在盘上，就会退回重抽。
    """
    stage = "step1 拆分" if kind == QUARANTINE_KIND_STEP1 else "step2 视觉展开"
    field = "units[i].text / source_text / duration_seconds" if kind == QUARANTINE_KIND_STEP1 else "units[i].text"
    return (
        f"❌ {stage}产出有 {len(violations)} 处违约，已隔离到草稿（正式文件未被改动）：{draft}\n\n"
        f"{render_violation_report(violations)}\n\n"
        f"处置：直接编辑该草稿的 content.{field} 修正违约；"
        "若违约是「资产名未登记」，也可改为在 project.json 登记该资产、或改用已登记的名称。\n"
        f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 重新全量校验并晋升为正式文件；'
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
    """违约处置的单一出口：落隔离草稿 + 渲染报告，返回回给 agent 的报告文本。

    落盘与报告成对出现——报告要指名草稿路径，路径由落盘决定；两步分开写在各调用点，迟早会
    出现「报告说去改某个文件、而那个文件没被写出来」的分叉。
    """
    path = write_quarantine(project_path, episode, kind, content=content, violations=violations, meta=meta)
    return render_report(path, kind, violations, episode=episode)


__all__ = [
    "PROMOTE_TOOL_NAME",
    "QUARANTINE_KIND_STEP1",
    "QUARANTINE_KIND_STEP2",
    "STEP1_EDIT_TOOL_NAME",
    "QuarantinedDraft",
    "clear_quarantine",
    "quarantine_and_report",
    "quarantine_exists",
    "quarantine_path",
    "read_quarantine",
    "render_report",
    "violation_entries",
    "write_quarantine",
]
