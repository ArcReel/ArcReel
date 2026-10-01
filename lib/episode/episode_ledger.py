"""分集账本：episodes[] 账本字段的数据模型与账本读取工具。

project.json 的 episodes 列表是分集单一真相源，条目在 episode/title/script_file 之外扩展账本字段
（source_range / hook / outline / ledger_status），并记录集原文的来源（见
``lib.episode.episode_sources``）。切自整本源文的集的物理 ``source/episode_N.txt`` 是派生物；自带原文
的集文件就是该集的源文。

``source_range`` 是切出集唯一的位置真相：有它才能从源文重造派生文件、才能续接规划。旧拆分流程切出的
集没有它，照常消费，但规划入口会拒绝执行并指引全量重置（见 ``lib.episode.episode_planner`` 与
``lib.episode.episode_reset``）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.episode.episode_paths import episode_drafts_dir, episode_script_relpath
from lib.infra.path_safety import safe_exists
from lib.infra.text_utils import normalize_newlines
from lib.project.project_schema import parse_project_schema_version

_STRICT_CONFIG = ConfigDict(extra="forbid")

LedgerStatus = Literal["planned", "consumed", "stale"]

#: 当前状态集，供校验层识别「不在当前状态集内」的取值（容忍放行，仅记诊断日志）
LEDGER_STATUSES: tuple[str, ...] = get_args(LedgerStatus)

# 仅 ASCII 数字：\d 会放行全角等 Unicode 数字，把非流水线产物误判为派生集文件
_EPISODE_FILE_RE = re.compile(r"episode_([0-9]+)\.txt")
_SCRIPT_FILE_RE = re.compile(r"episode_([0-9]+)\.json")
_DRAFT_DIR_RE = re.compile(r"episode_([0-9]+)")

# 「什么后缀算源文本文件」的唯一定义，整本源文清单与其他源文读取方共用
SOURCE_TEXT_SUFFIXES = {".txt", ".md"}

# project.json 顶层源文指纹字段（源文相对路径 → 归一化文本 sha256）。账本坐标绑定
# 具体源文内容，指纹是「原文未变」的证据；全量重置把账本清空，指纹随之失效清除。
SOURCE_FINGERPRINTS_KEY = "source_fingerprints"


# 字段校验失败的原因以翻译键携带：Pydantic 只能把原因传成字符串，DataValidator 在
# 汇总报错时据此还原为可翻译片段，用户按请求语言看到失败原因（见
# ``lib.project.data_validator._pydantic_error_summary``）。
LEDGER_SOURCE_FILE_NOT_RELATIVE_KEY = "val_ledger_source_file_not_relative"
LEDGER_SOURCE_FILE_ESCAPES_KEY = "val_ledger_source_file_escapes"
LEDGER_START_AFTER_END_KEY = "val_ledger_start_after_end"


def _validate_rel_posix_path(value: str) -> str:
    """``source_file`` 的路径语义：项目根相对 POSIX 路径，拒绝绝对路径 / ``..`` / 反斜杠。

    形状校验放行这些值会让按路径读源文的消费方越出项目目录。
    """
    if not value or "\\" in value:
        raise ValueError(LEDGER_SOURCE_FILE_NOT_RELATIVE_KEY)
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute() or ".." in parts:
        raise ValueError(LEDGER_SOURCE_FILE_ESCAPES_KEY)
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
            raise ValueError(LEDGER_START_AFTER_END_KEY)
        return self


class EpisodeOutline(BaseModel):
    """drama 分集大纲：故事节点 + 下集预告语（由规划工具产出）。"""

    model_config = _STRICT_CONFIG

    story_beats: list[str] = Field(default_factory=list)
    next_episode_teaser: str | None = None


#: 从这一 schema 起，下集大纲取播出顺序中紧接的那一集，且没有规划数据时退为只给标题。
#: 更早的项目只在迁移链中出现：v15→v16 之前的激活沿用「集号 + 1、没有规划数据就不给」的
#: 口径，由 v15→v16 按新旧口径各规划一次，改写只因下集大纲变了的脚本规划登记。
NEXT_EPISODE_OUTLINE_BY_ORDER_SCHEMA_VERSION = 16


def episode_outline_context(
    project: Mapping[str, Any], episode: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """从分集账本提取 ``(本集大纲, 下集大纲)`` 作为剧本内容生成（script_plan）的规划输入。

    大纲 dict 含 ``title`` / ``hook`` / ``story_beats`` / ``next_episode_teaser``。本集条目无任何
    规划数据（旧式条目，规划工具尚未写入）时第一项为 None。下集取播出顺序中紧接的那一集：
    有规划数据给大纲，没有给标题，都没有、或本集是末集时第二项为 None。
    内容抽取前移后由 script_plan（normalize）消费——剧本内容（分镜边界 / 口播）须覆盖故事节点、
    末场落地集尾钩子；prompt_authoring 仅出视觉、不再需要大纲。
    """

    def _entry(ep_num: int) -> Mapping[str, Any]:
        return next(
            (e for e in (project.get("episodes") or []) if isinstance(e, Mapping) and e.get("episode") == ep_num),
            {},
        )

    def _context(entry: Mapping[str, Any]) -> dict[str, Any] | None:
        raw_outline = entry.get("outline")
        outline = raw_outline if isinstance(raw_outline, Mapping) else {}
        raw_beats = outline.get("story_beats")
        # 非 list 形状（手编损坏）按缺失处理；list 内非字符串项一并过滤——避免字符串被逐字符渲染、
        # 或数字 / None 等脏数据原样进 script_plan prompt（与 helper 的 fail-soft 同口径）。
        story_beats = [beat for beat in raw_beats if isinstance(beat, str)] if isinstance(raw_beats, list) else []
        ctx: dict[str, Any] = {
            "title": entry.get("title"),
            "hook": entry.get("hook"),
            "story_beats": story_beats,
            "next_episode_teaser": outline.get("next_episode_teaser"),
        }
        if not ctx["hook"] and not ctx["story_beats"] and not ctx["next_episode_teaser"]:
            return None
        return ctx

    if parse_project_schema_version(project) < NEXT_EPISODE_OUTLINE_BY_ORDER_SCHEMA_VERSION:
        return _context(_entry(episode)), _context(_entry(episode + 1))

    following: Mapping[str, Any] = {}
    entries = [e for e in (project.get("episodes") or []) if isinstance(e, Mapping)]
    for index, entry in enumerate(entries[:-1]):
        if entry.get("episode") == episode:
            following = entries[index + 1]
            break
    next_context = _context(following)
    title = following.get("title")
    if next_context is None and isinstance(title, str) and title.strip():
        next_context = {"title": title, "hook": None, "story_beats": [], "next_episode_teaser": None}
    return _context(_entry(episode)), next_context


def normalize_source_text(text: str) -> str:
    """账本坐标系的唯一归一化函数：Unicode NFC + 换行统一为 ``\\n``。

    source_range 的偏移量全部落在本函数输出的坐标系内，
    任何按偏移切片源文的消费方必须先对源文执行本函数。
    """
    return unicodedata.normalize("NFC", normalize_newlines(text))


@dataclass
class SourceDoc:
    """整本源文的一个文件：项目根相对 POSIX 路径 + 归一化全文。"""

    rel_path: str
    text: str


def parse_episode_num(value: Any) -> int | None:
    """宽松解析条目集号：int（排除 bool——True 会与第 1 集同键碰撞）或纯数字
    字符串（历史手编数据），其余返回 None（条目原样保留，不参与按集号的处置）。

    ``str.isdigit()`` 认可的字符集比 ``int()`` 能转换的更宽（如上标 ``²``、
    带圈数字 ``①``），损坏账本写入这类字符会让 ``isdigit()`` 放行但 ``int()`` 抛
    ``ValueError``；两者不一致时同样返回 None，不能让调用方（含零前置校验的重置
    逃生口）因此崩溃。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def parse_positive_episode_num(value: Any) -> int | None:
    """在 :func:`parse_episode_num` 基础上再要求正整数。

    0 与负数虽能被 ``parse_episode_num`` 解析，但账本消费方（如
    ``lib.episode.episode_reset`` 的零前置校验）一律视非正集号为损坏账本，不参与正常
    处置；判定「这是不是一条形状合法的账本条目」时须与该口径一致，否则 0/
    负数集号的畸形条目会被误当合法条目放行。
    """
    num = parse_episode_num(value)
    return num if num is not None and num > 0 else None


def is_derived_episode_name(name: str) -> bool:
    """文件名是否为派生集文件名 ``episode_N.txt``（仅 ASCII 数字）。"""
    return _EPISODE_FILE_RE.fullmatch(name) is not None


def compute_source_fingerprints(sources: list[SourceDoc]) -> dict[str, str]:
    """按源文件记录归一化文本的 sha256 指纹（源文相对路径 → hexdigest）。

    ``sources`` 取自 ``lib.episode.episode_sources.discover_sources``，其 ``text`` 已是 ``normalize_source_text`` 输出，
    故换行风格（CRLF/LF）差异不会体现在指纹上。
    """
    return {doc.rel_path: hashlib.sha256(doc.text.encode("utf-8")).hexdigest() for doc in sources}


def mismatched_source_fingerprints(recorded: Any, sources: list[SourceDoc]) -> list[str]:
    """比对记录指纹与当前源文，返回不一致的源文相对路径（按路径排序）。

    只比对「已记录」的文件：``recorded`` 非 ``Mapping`` 或某文件不在其中，视为存量项目 /
    新源文件尚未补记指纹，不参与比对（不阻塞首次规划）。已记录文件若当前指纹不同、或该
    文件已从整本源文中消失（被删除/移出清单），均判为不一致——账本坐标绑定的原文内容已不
    可信，唯一出路是全量重置。记录值形状损坏（非 str）按未记录处理，不让脏数据本身崩溃
    比对逻辑。
    """
    if not isinstance(recorded, Mapping):
        return []
    current = compute_source_fingerprints(sources)
    mismatched = {
        rel
        for rel, fingerprint in recorded.items()
        if isinstance(rel, str) and isinstance(fingerprint, str) and current.get(rel) != fingerprint
    }
    return sorted(mismatched)


def discover_episode_file_aliases(project_dir: Path) -> dict[int, list[Path]]:
    """枚举派生集文件的全部别名 source/episode_N.txt → {集号: [路径, ...]}（按文件名排序）。

    同一集号可能因命名 padding 不同（``episode_1.txt`` / ``episode_01.txt``）产生多个
    别名文件。大多数调用方只需其中一个代表路径（见 ``discover_episode_files``）；需要
    完整处置某集号全部派生文件的场景（如重置清理）用本函数取全部。

    悬空符号链接（目标不存在）同样纳入：``Path.is_file()`` 会因链接目标缺失而返回
    False，导致这类文件对发现逻辑完全不可见——处置类调用方（如重置）因此漏清它，
    残留的悬空链接会在下一次派生文件写入时被 ``EpisodePlanner`` 的符号链接校验硬
    拦截。``unlink()``/``rename()`` 只作用于链接条目本身、不跟随最终一段的链接目标，
    纳入悬空链接不会引入跟随写入的风险。
    """
    source_dir = project_dir / "source"
    if not source_dir.is_dir():
        return {}
    result: dict[int, list[Path]] = {}
    for path in sorted(source_dir.iterdir()):
        match = _EPISODE_FILE_RE.fullmatch(path.name)
        if match and (path.is_file() or path.is_symlink()):
            result.setdefault(int(match.group(1)), []).append(path)
    return result


def discover_episode_files(project_dir: Path) -> dict[int, Path]:
    """枚举派生集文件 source/episode_N.txt → {集号: 路径}（每号取一个可读的代表路径）。

    代表路径只从该集号别名中选可读的普通文件；全部别名都是悬空符号链接（无真实
    内容）时该集号整体不出现在结果里，而不是退而返回一个读不到内容的路径——
    ``discover_episode_file_aliases`` 按文件名排序、不区分悬空与否，若悬空别名
    （如 ``episode_01.txt``）恰好排在有效文件（``episode_1.txt``）之前，直接取
    排序首个会让按内容读派生文件的调用方读到空内容，也会给纯悬空、毫无真实内容
    的集号凭空补建一个幽灵条目。
    """
    result: dict[int, Path] = {}
    for num, paths in discover_episode_file_aliases(project_dir).items():
        readable = next((p for p in paths if p.is_file()), None)
        if readable is not None:
            result[num] = readable
    return result


def discover_product_episode_nums(project_dir: Path) -> set[int]:
    """枚举磁盘上有下游产物（剧本 JSON / script_plan 草稿目录）的集号，不依赖账本条目。

    账本丢失条目（写坏/手工误删）但 ``scripts/episode_N.json`` 或
    ``drafts/episode_N/`` 仍在磁盘时，仅从账本条目与 ``source/episode_N.txt`` 取候选
    集号会漏掉这类孤儿产物，使其消费状态判定被跳过。
    """
    nums: set[int] = set()
    scripts_dir = project_dir / "scripts"
    if scripts_dir.is_dir():
        for path in scripts_dir.iterdir():
            match = _SCRIPT_FILE_RE.fullmatch(path.name)
            if match and path.is_file():
                nums.add(int(match.group(1)))
    drafts_dir = project_dir / "drafts"
    if drafts_dir.is_dir():
        for path in drafts_dir.iterdir():
            match = _DRAFT_DIR_RE.fullmatch(path.name)
            # 目录存在不等于有产物：files.py::update_draft_content 会在校验草稿内容前
            # 先建目录，一次被拒绝的无效保存就会留下空目录；只有真正落了 script_plan_* 才算
            # 下游产物，与 has_downstream_products() 的口径保持一致
            if match and path.is_dir() and any(path.glob("script_plan_*")):
                nums.add(int(match.group(1)))
    return nums


def has_downstream_products(project_dir: Path, episode_num: int, entry: Mapping[str, Any]) -> bool:
    """该集是否已有下游产物（剧本 JSON / script_plan 中间文件；媒体必经剧本，剧本存在即覆盖）。

    磁盘证据优先于账本状态：账本损坏（状态缺失/错乱）时仍能判定该集是否已被消费。
    """
    script_file = entry.get("script_file")
    if isinstance(script_file, str) and safe_exists(project_dir, script_file):
        return True
    if (project_dir / episode_script_relpath(episode_num)).is_file():
        return True
    drafts_dir = episode_drafts_dir(project_dir, episode_num)
    # script_plan_* 匹配任意格式（结构化 .json / 旧版 .md / reference_units.md），format-agnostic 地
    # 覆盖所有 content_mode 的 script_plan 产物：只要拆过段就算已有下游，避免被重规划覆盖。
    return drafts_dir.is_dir() and any(drafts_dir.glob("script_plan_*"))


def episode_has_products(
    project_dir: Path, episode_num: int, entry: Mapping[str, Any], *, product_nums: Collection[int]
) -> bool:
    """一集有产物：账本标 consumed，或磁盘上已有剧本 / script_plan（含补零的剧本文件名）。

    ``product_nums`` 取自 :func:`discover_product_episode_nums`，由调用方一次算好。手工切分、重新规划与重置按同一口径
    判定被替换的旧切出集是转为无原文的集还是直接移除。
    """
    return (
        entry.get("ledger_status") == "consumed"
        or episode_num in product_nums
        or has_downstream_products(project_dir, episode_num, entry)
    )


def parse_source_range(entry: Mapping[str, Any]) -> tuple[str, int, int] | None:
    """解析条目的 ``source_range`` 坐标，结构不完整时返回 None。

    「这一集有没有位置记录」的唯一判据，plan 与重置两侧共用：只查字段类型
    （``source_file`` 是 str、``start`` / ``end`` 是非 bool 的 int），不校验数值是否
    越界——是否要求坐标落在源文界内由调用方按各自口径决定。空字典 ``{}`` 或缺字段的
    损坏映射满足 ``isinstance(..., Mapping)`` 但没有可用坐标，一律按无坐标处理。
    """
    source_range = entry.get("source_range")
    if not isinstance(source_range, Mapping):
        return None
    rel = source_range.get("source_file")
    start = source_range.get("start")
    end = source_range.get("end")
    if (
        isinstance(rel, str)
        and isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
    ):
        return rel, start, end
    return None
