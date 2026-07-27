"""分集规划重置：把账本退回未规划状态的逃生口。

账本坐标绑定具体源文内容，源文被替换或账本被写坏后，规划入口会因坐标越界 /
范围无效而永久失败。全量重置（``from_episode=1``）是这种局面的唯一出路：
**零前置校验**——不读旧坐标、不解析范围，账本处于任何损坏状态都必须执行成功，
执行后 ``episodes`` 清空、``planning_cursor`` 置 null、源文指纹清除，
``plan_episodes`` 可从头重新规划。

部分重置（``from_episode > 1``）保留第 1..from_episode-1 集、只清除
from_episode 起的条目，与全量重置相反，走**前置校验**：全部已记录源文指纹须与
当前源文一致，且保留段每一集的 ``source_range`` 须结构完整、落在当前源文界内、
且连续无缺口——任一不满足都无法安全推算「保留到哪、退回到哪」，直接拒绝执行
（账本不改动）并指引改用全量重置。校验通过后 ``planning_cursor`` 退到第
from_episode-1 集原文范围末尾，源文指纹字段保留不动（已验证与当前源文一致）。

本模块刻意不依赖 :class:`lib.text_generator.TextGenerator`：重置不调模型，
逃生口不能因供应商未配置而失效。写入与 ``EpisodePlanner`` 共用同一把项目锁
（``ProjectManager.update_project``），提交纪律一致。

派生集文件按「是否可从账本重造」分流：账本条目带 ``source_range`` 的
``source/episode_N.txt`` 是派生物，直接删除；无 ``source_range`` 的集文件可能是
老项目原件（含手工内容，无坐标可重造），改名留底而非删除。下游产物（剧本 JSON、
step1 中间文件、媒体）一律不删。部分重置时上述处置只施于 from_episode 起的范围，
保留段的派生文件与下游产物不受影响。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_file_aliases,
    discover_product_episode_nums,
    discover_sources,
    has_downstream_products,
    mismatched_source_fingerprints,
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
    # exists() 对悬空符号链接返回 False（它会解引用到不存在的目标）：上一次重置把悬空
    # episode_N.txt 留底后，candidate 本身可能就是这样一个悬空链接，仅查 exists() 会漏判
    # 占用，导致 rename() 静默覆盖上一次的留底，违反本函数「同名时追加序号」的承诺
    while candidate.exists() or candidate.is_symlink():
        candidate = path.with_name(f"{base}.{index}.bak")
        index += 1
    return candidate


def _has_recreatable_source_range(entry: Mapping[str, Any]) -> bool:
    """entry 的 source_range 是否携带足以重造派生文件的坐标结构。

    只查字段类型（``source_file`` 是 str、``start``/``end`` 是非 bool 的 int），不校验
    数值是否越界——重置零前置校验，不读源文、不做范围合法性判断，那是 plan/replan
    的职责。空字典 ``{}`` 或缺字段的损坏映射满足 ``isinstance(..., Mapping)`` 但没有
    可用坐标，仍会被误判为「可从账本重造」进而永久删除本应留底的文件。
    """
    source_range = entry.get("source_range")
    if not isinstance(source_range, Mapping):
        return False
    rel = source_range.get("source_file")
    start = source_range.get("start")
    end = source_range.get("end")
    return (
        isinstance(rel, str)
        and isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
    )


def _scan(project_dir: Path, project: Mapping[str, Any], *, from_episode: int = 1) -> _ResetPlan:
    """扫描账本与磁盘得出处置计划。纯读：不改入参、不动文件。

    已消费判定取账本状态与磁盘产物的并集——账本损坏时 ``ledger_status`` 未必可信，
    磁盘上的剧本 / step1 产物才是「这一集已经被消费过」的硬证据。

    ``from_episode`` 限定处置范围（默认 1 即全量，与既有调用方行为逐字一致）：
    集号小于它的账本条目、孤儿派生文件、孤儿下游产物均视为保留段，不参与本次
    扫描——它们既不计入已消费判定，也不进入删除/留底候选。
    """
    raw_episodes = project.get("episodes")
    entries: dict[int, Mapping[str, Any]] = {}
    # 损坏账本可能对同一集号写出多条条目（如首条 planned、后条 consumed，或首条带
    # source_range、后条不带）：只取首条会丢失后条携带的证据，故已消费判定与
    # 「文件是否可从账本重造」判定都按集号聚合全部条目，而非只看被 setdefault 留下的
    # 那一条
    entries_by_num: dict[int, list[Mapping[str, Any]]] = {}
    consumed_by_ledger_status: set[int] = set()
    # episodes 容器本身可能被写坏成非列表值（如 truthy 标量）：重置承诺零前置校验，
    # 这种情形按空账本处理而非抛错，不能让逃生口本身崩溃
    for entry in raw_episodes if isinstance(raw_episodes, list) else []:
        if not isinstance(entry, Mapping):
            continue
        num = parse_episode_num(entry.get("episode"))
        if num is None:
            continue
        if entry.get("ledger_status") == "consumed":
            consumed_by_ledger_status.add(num)
        entries.setdefault(num, entry)
        entries_by_num.setdefault(num, []).append(entry)

    episode_file_aliases = discover_episode_file_aliases(project_dir)
    # 孤儿下游产物候选集号（账本无条目、无 source/episode_N.txt，但 scripts/ 或 drafts/
    # 仍有该集产物）本身就是「该集已消费」的直接证据：has_downstream_products 只认
    # episode_script_relpath 算出的规范路径，无法识别 discover_product_episode_nums 已
    # 靠正则宽松匹配到的 padding 别名文件名（如 episode_01.json），必须单独纳入判定
    product_nums = discover_product_episode_nums(project_dir)
    consumed: list[int] = []
    deletes: list[Path] = []
    archives: list[Path] = []
    # 账本条目、磁盘派生文件、磁盘下游产物三者取并集：
    # - 孤儿集文件（账本无对应条目）同样要处置，否则重置后它会被回填重新补建成账本
    #   条目，账本清空的承诺落空
    # - 孤儿下游产物同样要纳入已消费判定，否则会绕过确认直接清空
    for num in sorted(set(entries) | set(episode_file_aliases) | product_nums):
        if from_episode > 1 and num < from_episode:
            continue
        dupes = entries_by_num.get(num) or [{}]
        # 已消费判定同样要聚合全部重复条目：损坏账本可能首条指向缺失的规范剧本路径、
        # 后条的 script_file 指向实际存在的非规范路径（如 scripts/custom_name.json），
        # 只传被 setdefault 留下的首条给 has_downstream_products 会漏判
        is_consumed = (
            num in consumed_by_ledger_status
            or num in product_nums
            or any(has_downstream_products(project_dir, num, dupe) for dupe in dupes)
        )
        if is_consumed:
            consumed.append(num)
        paths = episode_file_aliases.get(num) or []
        if not paths:
            continue
        # 同一集号可能存在多个 padding 别名（episode_1.txt / episode_01.txt），
        # 全部按同一处置口径处理，否则未处理的别名会在下次回填中重新补建账本条目。
        # 判定是否可删除取该集号全部重复条目的可重造性交集：任一条目无法证明文件可
        # 从账本重造，都按无法重造处理——删除是不可逆操作，证据冲突时偏保守
        can_recreate = all(_has_recreatable_source_range(dupe) for dupe in dupes)
        target = deletes if can_recreate else archives
        target.extend(paths)
    episode_nums = [num for num in sorted(entries) if not (from_episode > 1 and num < from_episode)]
    return _ResetPlan(episode_nums=episode_nums, consumed=consumed, deletes=deletes, archives=archives)


def _apply_files(project_dir: Path, plan: _ResetPlan) -> tuple[list[str], list[tuple[str, str]]]:
    """落盘文件处置：删派生集文件、留底非派生集文件、清理余文文件。

    失败一律抛错中止提交（账本写回随之回滚）：残留的集文件会被回填重新认领成账本
    条目，「重置后账本为空」的承诺会被悄悄推翻，宁可整体失败让调用方重试。重置本身
    幂等，重跑即可自愈已完成的部分。
    """
    source_dir = project_dir / "source"
    # source/ 是符号链接或（Windows 原生）目录联接时拒绝处置：这类 reparse point 会让
    # source_dir 之下的路径解析穿透到项目外目录，unlink/rename 因此可能作用到外部文件；
    # is_junction() 3.12+ 可用、POSIX 上恒为 False，与 EpisodePlanner._reconcile_derived_files
    # 的同类校验保持一致。派生集文件自身是符号链接则不受影响——unlink/rename 操作的是链接
    # 条目本身、不跟随最终一段的链接目标，悬空链接同样能被安全清理
    if source_dir.is_symlink() or source_dir.is_junction():
        raise EpisodeResetError("source/ 不能是符号链接或目录联接，拒绝处置派生集文件")
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


def _validate_partial_reset(project_dir: Path, project: Mapping[str, Any], *, from_episode: int) -> tuple[str, int]:
    """校验部分重置（``from_episode > 1``）的前置条件，返回重置后 planning_cursor
    的 ``(source_file, offset)``（第 from_episode-1 集原文范围末尾）。

    任一不满足都会让「保留到哪、退回到哪」无法安全推算，抛 :class:`EpisodeResetError`
    并指引改用全量重置；调用方保证校验失败时账本不被改动：

    - 账本形状必须干净（``episodes`` 是列表、条目均为对象、集号均可解析且不重复）——
      部分重置与全量重置相反，不做「零前置校验」的损坏容忍，形状有问题直接指向
      更彻底的全量重置
    - ``from_episode`` 必须是既有集号，且第 1..from_episode-1 集须连续存在（无缺口）
    - 第 from_episode-1 集须带结构完整的 ``source_range``（regarding cursor 退回点）
    - 全部已记录源文指纹须与当前源文一致（``mismatched_source_fingerprints``，与
      ``EpisodePlanner`` 的提交门禁同一套逃生口）
    - 保留段（1..from_episode-1）每一集若带 ``source_range``，须结构完整且落在
      对应源文件当前长度界内（unanchored 保留集无坐标可越界，不参与本项检查）
    """
    raw_episodes = project.get("episodes")
    if not isinstance(raw_episodes, list):
        raise EpisodeResetError(
            "账本 episodes 字段形状异常，无法安全推算部分重置边界，请改用 from_episode=1 做全量重置"
        )

    entries_by_num: dict[int, Mapping[str, Any]] = {}
    for entry in raw_episodes:
        if not isinstance(entry, Mapping):
            raise EpisodeResetError(
                "账本存在非法条目（非对象），无法安全推算部分重置边界，请改用 from_episode=1 做全量重置"
            )
        num = parse_episode_num(entry.get("episode"))
        if num is None:
            raise EpisodeResetError(
                "账本存在无法解析集号的条目，无法安全推算部分重置边界，请改用 from_episode=1 做全量重置"
            )
        if num in entries_by_num:
            raise EpisodeResetError(
                f"账本存在重复集号 {num}，无法安全推算部分重置边界，请改用 from_episode=1 做全量重置"
            )
        entries_by_num[num] = entry

    if from_episode not in entries_by_num:
        raise EpisodeResetError(
            f"第 {from_episode} 集不在账本中，无法从此处部分重置（当前已规划集号：{sorted(entries_by_num)}）"
        )
    missing = [num for num in range(1, from_episode) if num not in entries_by_num]
    if missing:
        raise EpisodeResetError(
            f"账本缺少第 {missing} 集，保留段不连续，无法安全推算部分重置边界，请改用 from_episode=1 做全量重置"
        )

    retain_num = from_episode - 1
    retain_range = entries_by_num[retain_num].get("source_range")
    if not isinstance(retain_range, Mapping):
        raise EpisodeResetError(
            f"第 {retain_num} 集缺少原文范围记录（unanchored），无法确定部分重置后的规划起点，"
            "请改用 from_episode=1 做全量重置"
        )
    retain_rel = retain_range.get("source_file")
    retain_start = retain_range.get("start")
    retain_end = retain_range.get("end")
    if (
        not isinstance(retain_rel, str)
        or not isinstance(retain_start, int)
        or isinstance(retain_start, bool)
        or not isinstance(retain_end, int)
        or isinstance(retain_end, bool)
        or retain_start > retain_end
    ):
        raise EpisodeResetError(
            f"第 {retain_num} 集原文范围记录非法，无法确定部分重置后的规划起点，请改用 from_episode=1 做全量重置"
        )

    current_sources = discover_sources(project_dir)
    mismatched = mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), current_sources)
    if mismatched:
        raise EpisodeResetError(
            f"源文件已被修改或移除：{'、'.join(mismatched)}；账本坐标绑定的是修改前的原文内容，"
            "无法安全部分重置，请改用 from_episode=1 做全量重置"
        )

    text_by_rel = {doc.rel_path: doc.text for doc in current_sources}
    for num in range(1, from_episode):
        source_range = entries_by_num[num].get("source_range")
        if not isinstance(source_range, Mapping):
            continue  # unanchored 保留集没有坐标可越界，物理文件本身即其最终记录
        rel = source_range.get("source_file")
        start = source_range.get("start")
        end = source_range.get("end")
        if (
            not isinstance(rel, str)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            raise EpisodeResetError(f"第 {num} 集原文范围记录非法，无法安全部分重置，请改用 from_episode=1 做全量重置")
        text = text_by_rel.get(rel)
        if text is None or not (0 <= start <= end <= len(text)):
            length = len(text) if text is not None else 0
            raise EpisodeResetError(
                f"第 {num} 集原文范围越界（源文件 {rel} 当前长度 {length}，记录范围 [{start}, {end})），"
                "无法安全部分重置，请改用 from_episode=1 做全量重置"
            )

    return retain_rel, retain_end


def reset_episode_planning(
    project_path: str | Path,
    *,
    from_episode: int = 1,
    confirm_consumed: bool = False,
) -> EpisodeResetResult | ResetConfirmationRequired:
    """重置分集规划账本。

    ``from_episode=1``：全量重置，零前置校验，账本处于任何损坏状态都必须执行成功
    （见模块文档）。``from_episode > 1``：部分重置，保留第 1..from_episode-1 集，
    见 :func:`_validate_partial_reset` 的前置校验；校验不通过时指名具体原因并指引
    改用全量重置，账本不被改动。

    两种模式都对波及已消费集（账本标 consumed 或磁盘已有剧本 / step1 产物）且未
    ``confirm_consumed`` 时不执行，返回 :class:`ResetConfirmationRequired` 等待
    显式确认；确认后执行，下游产物一律保留。

    Raises:
        EpisodeResetError: ``from_episode`` 非正整数、部分重置前置校验未通过、或
            文件处置失败，均保证账本未被改动。
        EpisodeResetConflictError: 重置期间出现确认清单之外的已消费集。
    """
    if from_episode < 1:
        raise EpisodeResetError(f"from_episode 必须是正整数，收到 {from_episode}")

    project_dir = Path(project_path)
    pm = ProjectManager(str(project_dir.parent))
    project_name = project_dir.name

    # 锁外预扫描/前置校验只为二段确认与快速失败服务：校验不通过或需要确认时零写入返回，
    # 不进锁、不碰文件
    project = pm.load_project(project_name)
    if from_episode > 1:
        _validate_partial_reset(project_dir, project, from_episode=from_episode)
    plan = _scan(project_dir, project, from_episode=from_episode)
    if plan.consumed and not confirm_consumed:
        return ResetConfirmationRequired(
            consumed_episodes=plan.consumed,
            archived_files=[_rel(project_dir, path) for path in plan.archives],
        )

    # 结果只能在锁内（按锁内复扫的实际处置）拼出，用闭包变量带回锁外
    committed: EpisodeResetResult | None = None

    def _commit(p: dict[str, Any]) -> None:
        nonlocal committed
        # 锁内重新校验/重新扫描：确认清单与前置校验都是锁外读取时刻的快照，期间源文件
        # 可能被外部改动、也可能出现清单之外的新消费集
        cursor = _validate_partial_reset(project_dir, p, from_episode=from_episode) if from_episode > 1 else None
        current = _scan(project_dir, p, from_episode=from_episode)
        if any(num not in plan.consumed for num in current.consumed):
            raise EpisodeResetConflictError("重置期间出现新的已消费集，需重新确认后再执行")
        if cursor is not None:
            retained = [
                entry
                for entry in (p.get("episodes") or [])
                if isinstance(entry, Mapping) and (parse_episode_num(entry.get("episode")) or 0) < from_episode
            ]
            retained.sort(key=lambda entry: parse_episode_num(entry.get("episode")) or 0)
            p["episodes"] = retained
            p["planning_cursor"] = {"source_file": cursor[0], "offset": cursor[1]}
        else:
            p["episodes"] = []
            p["planning_cursor"] = None
            p.pop(SOURCE_FINGERPRINTS_KEY, None)
        deleted, archived = _apply_files(project_dir, current)
        committed = EpisodeResetResult(
            removed_episodes=current.episode_nums,
            deleted_files=deleted,
            archived_files=archived,
            consumed_episodes=current.consumed,
        )

    pm.update_project(project_name, _commit)
    if committed is None:  # pragma: no cover - update_project 必然调用 mutate_fn
        raise EpisodeResetError("重置未执行：账本更新回调未被调用")
    logger.info(
        "分集规划已%s重置：项目 %s，清空 %d 集，删除派生文件 %d 个，留底 %d 个",
        "全量" if from_episode == 1 else f"部分（从第 {from_episode} 集起）",
        project_name,
        len(committed.removed_episodes),
        len(committed.deleted_files),
        len(committed.archived_files),
    )
    return committed


__all__ = [
    "EpisodeResetConflictError",
    "EpisodeResetError",
    "EpisodeResetResult",
    "ResetConfirmationRequired",
    "reset_episode_planning",
]
