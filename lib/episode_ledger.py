"""分集账本：episodes[] 账本字段的数据模型与存量项目机械回填。

project.json 的 episodes 列表是分集单一真相源，条目在 episode/title/script_file
之外扩展账本字段（source_range / hook / outline / ledger_status），顶层增加
planning_cursor 标记下一批规划起点。物理 ``source/episode_N.txt`` 是派生物。

账本字段全部可缺失：缺失 = 旧式条目（旧拆分流程写入，尚未回填）。
``backfill_episode_ledger`` 是幂等纯函数，可在启动迁移之外重跑以吸收旧流程
继续产生的新集。注意 planning_cursor 仅在缺失或为 null 时推导：首次回填写出
非空值后，重跑只补齐新集的 source_range，不再前移游标——规划方应以
consumed/planned 范围末尾为准推进起点，游标前移由规划工具负责。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.path_safety import safe_exists

logger = logging.getLogger(__name__)

_STRICT_CONFIG = ConfigDict(extra="forbid")

LedgerStatus = Literal["planned", "consumed", "stale", "unanchored"]

LEDGER_STATUSES: tuple[str, ...] = get_args(LedgerStatus)

# 仅 ASCII 数字：\d 会放行全角等 Unicode 数字，把非流水线产物误判为派生集文件
_EPISODE_FILE_RE = re.compile(r"episode_([0-9]+)\.txt")

# 「什么后缀算源文本文件」的唯一定义，候选枚举与其他源文读取方共用
SOURCE_TEXT_SUFFIXES = {".txt", ".md"}


def _validate_rel_posix_path(value: str) -> str:
    """``source_file`` 的路径语义：项目根相对 POSIX 路径，拒绝绝对路径 / ``..`` / 反斜杠。

    形状校验放行这些值会让按路径读源文的消费方越出项目目录。
    """
    if not value or "\\" in value:
        raise ValueError("source_file 必须是项目内相对 POSIX 路径")
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute() or ".." in parts:
        raise ValueError("source_file 不能是绝对路径或包含 ..")
    return value


class SourceRange(BaseModel):
    """集对应的原文素材范围。

    偏移量落在 ``normalize_source_text`` 的归一化坐标系内（narration 为精确切分点，
    drama 为软素材范围）。``source_file`` 是项目根相对 POSIX 路径（如 ``source/novel.txt``）。
    """

    model_config = _STRICT_CONFIG

    source_file: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @field_validator("source_file")
    @classmethod
    def _check_source_file(cls, value: str) -> str:
        return _validate_rel_posix_path(value)

    @model_validator(mode="after")
    def _check_order(self) -> SourceRange:
        if self.start > self.end:
            raise ValueError("start 不能大于 end")
        return self


class EpisodeOutline(BaseModel):
    """drama 分集大纲：故事节点 + 下集预告语（由规划工具产出，机械回填不生成）。"""

    model_config = _STRICT_CONFIG

    story_beats: list[str] = Field(default_factory=list)
    next_episode_teaser: str | None = None


class PlanningCursor(BaseModel):
    """下一批规划起点（归一化坐标系字符偏移）。"""

    model_config = _STRICT_CONFIG

    source_file: str
    offset: int = Field(ge=0)

    @field_validator("source_file")
    @classmethod
    def _check_source_file(cls, value: str) -> str:
        return _validate_rel_posix_path(value)


def normalize_source_text(text: str) -> str:
    """账本坐标系的唯一归一化函数：Unicode NFC + 换行统一为 ``\\n``。

    source_range / planning_cursor 的偏移量全部落在本函数输出的坐标系内，
    任何按偏移切片源文的消费方必须先对源文执行本函数。
    """
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class _SourceDoc:
    """候选源文件：归一化全文 + 顺序先验游标（上一集匹配末尾）。"""

    rel_path: str
    text: str
    cursor: int = 0


def _read_text_or_none(path: Path) -> str | None:
    """读取文本文件；不可读/非 UTF-8 返回 None（回填容错降级，不让单文件拖垮整项目迁移）。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("回填读取文件失败，按缺失处理：%s: %s", path, exc)
        return None


def _episode_num(value: Any) -> int | None:
    """宽松解析条目集号：int（排除 bool——True 会与第 1 集同键碰撞）或纯数字
    字符串（历史手编数据），其余返回 None（条目原样保留，不参与回填）。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _discover_sources(project_dir: Path) -> list[_SourceDoc]:
    """枚举 source/ 直下一级的候选源文件（.txt/.md），按文件名排序。

    排除派生集文件（episode_N.txt）、下划线/点前缀文件（_remaining.txt 等）与
    子目录（source/raw/ 原格式备份天然不进候选）。
    """
    source_dir = project_dir / "source"
    if not source_dir.is_dir():
        return []
    docs: list[_SourceDoc] = []
    for path in sorted(source_dir.iterdir()):
        name = path.name
        if not path.is_file() or name.startswith(("_", ".")):
            continue
        if path.suffix.lower() not in SOURCE_TEXT_SUFFIXES or _EPISODE_FILE_RE.fullmatch(name):
            continue
        text = _read_text_or_none(path)
        if text is None:
            continue
        docs.append(_SourceDoc(rel_path=f"source/{name}", text=normalize_source_text(text)))
    return docs


def _discover_episode_files(project_dir: Path) -> dict[int, Path]:
    """枚举派生集文件 source/episode_N.txt → {集号: 路径}。"""
    source_dir = project_dir / "source"
    if not source_dir.is_dir():
        return {}
    result: dict[int, Path] = {}
    for path in sorted(source_dir.iterdir()):
        match = _EPISODE_FILE_RE.fullmatch(path.name)
        if match and path.is_file():
            result.setdefault(int(match.group(1)), path)
    return result


def _find_in_sources(
    sources: list[_SourceDoc], needle: str, preferred: _SourceDoc | None
) -> tuple[_SourceDoc, int, int] | None:
    """在候选源文件中精确匹配 needle，返回 (源文件, start, end)。

    两遍搜索：第一遍按局部性先验顺序（上一集命中的文件优先）从各文件游标
    （上一集末尾）起搜；全部落空再做第二遍全文搜索（覆盖用户重切过、起点早于
    上一集末尾的情形）。先验文件中游标前的重复段不会遮蔽其他文件的前向匹配。
    命中取第一个匹配保证确定性；只做精确子串匹配，不做模糊锚定。
    """
    ordered = sources if preferred is None else [preferred, *(d for d in sources if d is not preferred)]
    for doc in ordered:
        idx = doc.text.find(needle, doc.cursor)
        if idx >= 0:
            return doc, idx, idx + len(needle)
    for doc in ordered:
        idx = doc.text.find(needle)
        if idx >= 0:
            return doc, idx, idx + len(needle)
    return None


def _has_downstream(project_dir: Path, episode_num: int, entry: Mapping[str, Any]) -> bool:
    """该集是否已有下游产物（剧本 JSON / step1 中间文件；媒体必经剧本，剧本存在即覆盖）。"""
    script_file = entry.get("script_file")
    if isinstance(script_file, str) and safe_exists(project_dir, script_file):
        return True
    if (project_dir / "scripts" / f"episode_{episode_num}.json").is_file():
        return True
    drafts_dir = project_dir / "drafts" / f"episode_{episode_num}"
    return drafts_dir.is_dir() and any(drafts_dir.glob("step1_*.md"))


def _derive_cursor(
    project_dir: Path,
    sources: list[_SourceDoc],
    last_doc: _SourceDoc | None,
    last_end: tuple[str, int] | None,
) -> dict[str, Any] | None:
    """把滚动余文文件的起点换算为 planning_cursor。

    余文缺失/为空/匹配不上时回退到最后一个 anchored 集的末尾（同为精确证据）；
    完全无证据返回 None。空余文必须跳过匹配——``str.find("")`` 恒为 0，会把
    游标错锚到文件头。余文匹配位置若回退进最后锚定集所在文件的已消费范围
    （崩溃残留/陈旧余文），以锚定证据为准。文件本身保留：旧拆分流程仍以它为
    下一集源文件，物理废除随流程切换进行。
    """
    remaining = project_dir / "source" / "_remaining.txt"
    if remaining.is_file():
        raw = _read_text_or_none(remaining)
        rem_text = normalize_source_text(raw) if raw is not None else ""
        if rem_text:
            found = _find_in_sources(sources, rem_text, last_doc)
            if found is not None:
                doc, start, _end = found
                rewinds = last_end is not None and doc.rel_path == last_end[0] and start < last_end[1]
                if not rewinds:
                    return {"source_file": doc.rel_path, "offset": start}
    if last_end is not None:
        return {"source_file": last_end[0], "offset": last_end[1]}
    return None


def backfill_episode_ledger(project_dir: Path, project: Mapping[str, Any]) -> dict[str, Any]:
    """机械回填分集账本。纯函数：不修改入参，对文件系统只读零写入。

    对每个派生集文件按内容回源文做精确子串匹配反推 source_range；有下游产物的集标
    consumed，无下游标 planned；匹配不上/集文件缺失的集标 unanchored 并锁定
    （source_range 置 null，即使有下游产物——物理文件即其最终记录，不参与重排）。
    已带 ledger_status（非 null）的条目整条跳过（保护规划工具写入的状态），故可
    安全重跑；显式 null 视同缺失，正常回填。planning_cursor 仅在缺失或为 null 时
    推导，规划工具写入的非空值不触碰。
    """
    data = dict(project)
    raw_episodes = data.get("episodes", [])
    if not isinstance(raw_episodes, list):
        return data  # 形状异常留给 data_validator 报告，回填不处理

    episodes: list[Any] = [dict(e) if isinstance(e, dict) else e for e in raw_episodes]
    episode_files = _discover_episode_files(project_dir)

    by_num: dict[int, dict[str, Any]] = {}
    for entry in episodes:
        if isinstance(entry, dict):
            num = _episode_num(entry.get("episode"))
            if num is not None:
                by_num.setdefault(num, entry)  # 重复集号首见优先，其余原样保留

    # 孤儿派生文件（已拆但 episodes 无条目，如拆分后尚未生成剧本）补建条目；
    # script_file 填规范预期路径，剧本生成时 _apply_episode_sync 按集号命中本条目回填真实值
    for num in sorted(episode_files):
        if num not in by_num:
            entry = {"episode": num, "title": "", "script_file": f"scripts/episode_{num}.json"}
            episodes.append(entry)
            by_num[num] = entry

    if all(isinstance(e, dict) and _episode_num(e.get("episode")) is not None for e in episodes):
        episodes.sort(key=lambda e: _episode_num(e["episode"]) or 0)
    data["episodes"] = episodes

    pending = [num for num in by_num if by_num[num].get("ledger_status") is None]
    if not pending and data.get("planning_cursor") is not None:
        return data  # 无待回填条目且游标已有值：跳过源文读取（重跑快路径）

    sources = _discover_sources(project_dir)
    last_doc: _SourceDoc | None = None
    last_end: tuple[str, int] | None = None
    for num in sorted(by_num):
        entry = by_num[num]
        if entry.get("ledger_status") is not None:
            source_range = entry.get("source_range")
            if isinstance(source_range, Mapping):
                rel_path = source_range.get("source_file")
                end = source_range.get("end")
                if isinstance(rel_path, str) and isinstance(end, int) and not isinstance(end, bool):
                    last_end = (rel_path, end)
                    doc = next((d for d in sources if d.rel_path == rel_path), None)
                    if doc is not None:
                        # 已账条目只前推游标（max）；新锚定（下方直接赋值）才允许回退以跟随重切
                        doc.cursor = max(doc.cursor, end)
                        last_doc = doc
            continue

        anchored: tuple[_SourceDoc, int, int] | None = None
        path = episode_files.get(num)
        if path is not None:
            raw = _read_text_or_none(path)
            ep_text = normalize_source_text(raw) if raw is not None else ""
            if ep_text:  # 空集文件无定位信息（零长度范围无意义），落 unanchored
                anchored = _find_in_sources(sources, ep_text, last_doc)

        if anchored is None:
            entry["source_range"] = None
            entry["ledger_status"] = "unanchored"
            continue

        doc, start, end = anchored
        entry["source_range"] = {"source_file": doc.rel_path, "start": start, "end": end}
        entry["ledger_status"] = "consumed" if _has_downstream(project_dir, num, entry) else "planned"
        # 直接赋值（允许回退）：全文退化命中早于游标说明用户重切过，后续集跟随新布局
        doc.cursor = end
        last_doc = doc
        last_end = (doc.rel_path, end)

    if data.get("planning_cursor") is None:
        data["planning_cursor"] = _derive_cursor(project_dir, sources, last_doc, last_end)
    return data
