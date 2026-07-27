"""分集规划重置：把账本退回未规划状态的逃生口。

账本坐标绑定具体源文内容，源文被替换或账本被写坏后，规划入口会因坐标越界 /
范围无效而永久失败。全量重置（``from_episode=1``）是这种局面的唯一出路：
**零前置校验**——不读旧坐标、不解析范围，账本处于任何损坏状态都必须执行成功，
执行后 ``episodes`` 清空、``planning_cursor`` 置 null、源文指纹清除，
``plan_episodes`` 可从头重新规划。

本模块刻意不依赖 :class:`lib.text_generator.TextGenerator`：重置不调模型，
逃生口不能因供应商未配置而失效。写入与 ``EpisodePlanner`` 共用同一把项目锁
（``ProjectManager.update_project``），提交纪律一致。

派生集文件按「是否可从账本重造」分流：账本条目带 ``source_range`` 的
``source/episode_N.txt`` 是派生物，直接删除；无 ``source_range`` 的集文件可能是
老项目原件（含手工内容，无坐标可重造），改名留底而非删除。下游产物（剧本 JSON、
step1 中间文件、媒体）一律不删。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_files,
    has_downstream_products,
    parse_episode_num,
)
from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


class EpisodeResetError(RuntimeError):
    """分集规划重置失败。"""


class EpisodeResetConflictError(EpisodeResetError):
    """重置期间账本被并发修改（出现确认清单之外的已消费集），提交被拒绝。"""


@dataclass
class ResetConfirmationRequired:
    """重置波及已消费集，需显式确认（``confirm_consumed=True``）后才执行。

    返回本对象时未发生任何写入，``archived_files`` 供调用方向用户交代留底去向。
    """

    consumed_episodes: list[int]
    archived_files: list[str] = field(default_factory=list)


@dataclass
class EpisodeResetResult:
    """重置执行结果：清掉的集号与文件处置去向（相对项目根的 POSIX 路径）。"""

    removed_episodes: list[int]
    deleted_files: list[str]
    archived_files: list[tuple[str, str]]  # (原路径, 留底路径)
    consumed_episodes: list[int]


@dataclass
class _ResetPlan:
    """一次扫描得出的处置计划：受影响集号、已消费集号、文件删除/留底清单。"""

    episode_nums: list[int]
    consumed: list[int]
    deletes: list[Path]
    archives: list[Path]


def _archive_path(path: Path) -> Path:
    """派生集文件的留底路径：下划线前缀 + ``.bak`` 尾缀，同名时追加序号。

    两处改动缺一不可地把文件挡在发现逻辑之外：下划线前缀让 ``discover_sources``
    跳过（不会被当成新源文），``.bak`` 后缀既不属于源文后缀白名单、也让
    ``discover_episode_files`` 的 ``episode_N.txt`` 全匹配落空（不会被当成派生集文件
    重新补建账本条目）。
    """
    base = f"_{path.name}"
    candidate = path.with_name(f"{base}.bak")
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{base}.{index}.bak")
        index += 1
    return candidate


def _scan(project_dir: Path, project: Mapping[str, Any]) -> _ResetPlan:
    """扫描账本与磁盘得出处置计划。纯读：不改入参、不动文件。

    已消费判定取账本状态与磁盘产物的并集——账本损坏时 ``ledger_status`` 未必可信，
    磁盘上的剧本 / step1 产物才是「这一集已经被消费过」的硬证据。
    """
    entries: dict[int, Mapping[str, Any]] = {}
    for entry in project.get("episodes") or []:
        if not isinstance(entry, Mapping):
            continue
        num = parse_episode_num(entry.get("episode"))
        if num is not None:
            entries.setdefault(num, entry)

    episode_files = discover_episode_files(project_dir)
    consumed: list[int] = []
    deletes: list[Path] = []
    archives: list[Path] = []
    # 账本条目与磁盘派生文件取并集：孤儿集文件（账本无对应条目）同样要处置，
    # 否则重置后它会被回填重新补建成账本条目，账本清空的承诺落空
    for num in sorted(set(entries) | set(episode_files)):
        entry = entries.get(num) or {}
        if entry.get("ledger_status") == "consumed" or has_downstream_products(project_dir, num, entry):
            consumed.append(num)
        path = episode_files.get(num)
        if path is None:
            continue
        if isinstance(entry.get("source_range"), Mapping):
            deletes.append(path)
        else:
            archives.append(path)
    return _ResetPlan(episode_nums=sorted(entries), consumed=consumed, deletes=deletes, archives=archives)


def _apply_files(project_dir: Path, plan: _ResetPlan) -> tuple[list[str], list[tuple[str, str]]]:
    """落盘文件处置：删派生集文件、留底非派生集文件、清理余文文件。

    失败一律抛错中止提交（账本写回随之回滚）：残留的集文件会被回填重新认领成账本
    条目，「重置后账本为空」的承诺会被悄悄推翻，宁可整体失败让调用方重试。重置本身
    幂等，重跑即可自愈已完成的部分。
    """
    deleted: list[str] = []
    archived: list[tuple[str, str]] = []
    for path in plan.deletes:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise EpisodeResetError(f"派生集文件删除失败，重置已中止：{path.name}: {exc}") from exc
        deleted.append(_rel(project_dir, path))
    for path in plan.archives:
        target = _archive_path(path)
        try:
            path.rename(target)
        except OSError as exc:
            raise EpisodeResetError(f"集文件留底改名失败，重置已中止：{path.name}: {exc}") from exc
        archived.append((_rel(project_dir, path), _rel(project_dir, target)))
    # 余文文件是旧流程的进度指针，留着会在下次规划的回填中被换算成 planning_cursor，
    # 让「从头规划」变成从余文起点续规划
    remaining = project_dir / "source" / "_remaining.txt"
    if remaining.is_file():
        try:
            remaining.unlink()
        except OSError as exc:
            raise EpisodeResetError(f"余文文件清理失败，重置已中止：{remaining.name}: {exc}") from exc
    return deleted, archived


def _rel(project_dir: Path, path: Path) -> str:
    return path.relative_to(project_dir).as_posix()


def reset_episode_planning(
    project_path: str | Path,
    *,
    from_episode: int = 1,
    confirm_consumed: bool = False,
) -> EpisodeResetResult | ResetConfirmationRequired:
    """重置分集规划账本。当前仅支持 ``from_episode=1`` 的全量重置。

    波及已消费集（账本标 consumed 或磁盘已有剧本 / step1 产物）且未
    ``confirm_consumed`` 时不执行，返回 :class:`ResetConfirmationRequired` 等待
    显式确认；确认后执行，下游产物一律保留。

    Raises:
        EpisodeResetError: ``from_episode > 1``（部分重置尚未支持）或文件处置失败，
            两种情形下账本均未被改动。
        EpisodeResetConflictError: 重置期间出现确认清单之外的已消费集。
    """
    if from_episode != 1:
        raise EpisodeResetError(
            f"暂不支持部分重置（from_episode={from_episode}）：当前只能用 from_episode=1 做全量重置"
            "（清空整个账本后重新规划）。账本未做任何改动。"
        )

    project_dir = Path(project_path)
    pm = ProjectManager(str(project_dir.parent))
    project_name = project_dir.name

    # 锁外预扫描只为二段确认服务：需要确认时零写入返回，不进锁、不碰文件
    plan = _scan(project_dir, pm.load_project(project_name))
    if plan.consumed and not confirm_consumed:
        return ResetConfirmationRequired(
            consumed_episodes=plan.consumed,
            archived_files=[_rel(project_dir, path) for path in plan.archives],
        )

    committed: dict[str, Any] = {}

    def _commit(p: dict[str, Any]) -> None:
        # 锁内重新扫描：确认清单是锁外读取时刻的快照，期间新消费的集不在用户确认范围内
        current = _scan(project_dir, p)
        if any(num not in plan.consumed for num in current.consumed):
            raise EpisodeResetConflictError("重置期间出现新的已消费集，需重新确认后再执行")
        p["episodes"] = []
        p["planning_cursor"] = None
        p.pop(SOURCE_FINGERPRINTS_KEY, None)
        deleted, archived = _apply_files(project_dir, current)
        committed["removed"] = current.episode_nums
        committed["consumed"] = current.consumed
        committed["deleted"] = deleted
        committed["archived"] = archived

    pm.update_project(project_name, _commit)
    logger.info(
        "分集规划已全量重置：项目 %s，清空 %d 集，删除派生文件 %d 个，留底 %d 个",
        project_name,
        len(committed["removed"]),
        len(committed["deleted"]),
        len(committed["archived"]),
    )
    return EpisodeResetResult(
        removed_episodes=committed["removed"],
        deleted_files=committed["deleted"],
        archived_files=committed["archived"],
        consumed_episodes=committed["consumed"],
    )


__all__ = [
    "EpisodeResetConflictError",
    "EpisodeResetError",
    "EpisodeResetResult",
    "ResetConfirmationRequired",
    "reset_episode_planning",
]
