"""分集规划重置：把账本中切自整本源文的集退回未规划状态的逃生口。

只动切出集：自带原文与无原文的集不占用整本源文，两种重置都原样保留它们的条目、集文件与下游产物，
相对顺序不变（见 ``docs/adr/0031``）。

账本坐标绑定具体源文内容，源文被替换或账本被写坏后，规划入口会因坐标越界 / 范围无效而永久失败。
全量重置（不指定集）是这种局面的唯一出路：**零前置校验**——不读旧坐标、不解析范围，账本处于任何
损坏状态都必须执行成功，执行后切出集全部移出账本、源文指纹与快照清除，``plan_episodes`` 可从头
重新规划。

部分重置（指定播出顺序中第一个切出集之外的某个切出集）保留账本里它之前的集、清除它及其后的切出集，
与全量重置相反，走**前置校验**：全部已记录源文指纹须与当前源文一致，且保留段的 ``source_range`` 须
落在当前源文界内、沿源文位置前进——任一不满足都无法安全推算「保留到哪、退回到哪」，直接拒绝执行
（账本不改动）并指引改用全量重置。接续规划的起点由账本推导，随之退到保留段最后一个切出集的结尾。

两种重置都不回退项目历史最高号（``lib.episode.episode_ids``）：被清除的集 ID 不再分配，
重新规划出的集取新 ID，旧 ID 的产物不会被新集认领。

本模块刻意不依赖 :class:`lib.backends.text_generator.TextGenerator`：重置不调模型，
逃生口不能因供应商未配置而失效。写入与 ``EpisodePlanner`` 共用同一把项目锁
（``ProjectManager.update_project``），提交纪律一致。

被清除的切出集的集文件按「是否可从账本重造」分流：带 ``source_range`` 的
``source/episode_N.txt`` 是派生物，直接删除；无 ``source_range`` 的集文件可能是
老项目原件（含手工内容，无坐标可重造），改名留底而非删除。下游产物（剧本 JSON、
script_plan 中间文件、媒体）一律不删。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.formal_write import formal_write_transaction
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_file_aliases,
    discover_product_episode_nums,
    has_downstream_products,
    mismatched_source_fingerprints,
    parse_episode_num,
    parse_source_range,
)
from lib.episode.episode_sources import (
    SOURCE_SNAPSHOTS_DIR,
    archive_episode_file_path,
    discover_sources,
    episode_entry,
    first_cut_episode_id,
    is_cut_episode,
    source_snapshot_path,
    sync_source_snapshots,
    whole_source_files,
)
from lib.project.project_manager import ProjectManager

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


_FULL_RESET_HINT = "请改用不带 episode_id 的全量重置"


def _has_recreatable_source_range(entry: Mapping[str, Any]) -> bool:
    """entry 的 source_range 是否携带足以重造派生文件的坐标结构。

    只看坐标结构是否完整、不读源文校验数值是否越界——重置零前置校验，范围合法性判断
    是 plan 的职责。坐标结构不完整的损坏条目按「不可重造」处理，其集文件走留底
    而非删除：删除不可逆，证据不足时偏保守。
    """
    return parse_source_range(entry) is not None


def _scan(project_dir: Path, project: Mapping[str, Any], *, retained: frozenset[int] = frozenset()) -> _ResetPlan:
    """扫描账本与磁盘得出处置计划。纯读：不改入参、不动文件。

    已消费判定取账本状态与磁盘产物的并集——账本损坏时 ``ledger_status`` 未必可信，
    磁盘上的剧本 / script_plan 产物才是「这一集已经被消费过」的硬证据。

    ``retained`` 是部分重置保留段的集 ID（全量重置为空）：保留段的账本条目、派生文件与
    下游产物不参与本次扫描——它们既不计入已消费判定，也不进入删除/留底候选。
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

    def _in_scope(num: int) -> bool:
        """该集号是否落在本次处置范围内。

        全量重置的保留段为空，任何可解析的集号（含损坏账本写出的 0 或负数）都在范围内，
        不会被悄悄留在「已清空」的账本里。
        """
        return num not in retained

    # 账本条目、磁盘派生文件、磁盘下游产物三者取并集：
    # - 孤儿集文件（账本无对应条目）同样要处置：它无法证明可从账本重造，按留底处理，
    #   重置后 source/ 里不留下与账本对不上的集文件
    # - 孤儿下游产物同样要纳入已消费判定，否则会绕过确认直接清空
    for num in sorted(set(entries) | set(episode_file_aliases) | product_nums):
        if not _in_scope(num):
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
        # 全部按同一处置口径处理，否则未处理的别名会作为残留留在 source/ 里。
        # 判定是否可删除取该集号全部重复条目的可重造性交集：任一条目无法证明文件可
        # 从账本重造，都按无法重造处理——删除是不可逆操作，证据冲突时偏保守
        can_recreate = all(_has_recreatable_source_range(dupe) for dupe in dupes)
        target = deletes if can_recreate else archives
        target.extend(paths)
    episode_nums = [num for num in sorted(entries) if _in_scope(num)]
    return _ResetPlan(episode_nums=episode_nums, consumed=consumed, deletes=deletes, archives=archives)


def _assert_source_directory_safe(project_dir: Path) -> None:
    """Reject a source directory whose children could resolve outside the project."""

    source_dir = project_dir / "source"
    if source_dir.is_symlink() or source_dir.is_junction():
        raise EpisodeResetError("source/ 不能是符号链接或目录联接，拒绝处置派生集文件")


def _apply_files(
    project_dir: Path,
    plan: _ResetPlan,
    *,
    archive_targets: Mapping[Path, Path] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """落盘文件处置：删派生集文件、留底非派生集文件、清理余文文件。

    失败一律抛错中止提交（账本写回随之回滚）：残留的集文件与账本对不上，宁可整体失败让调用方
    重试。重置本身幂等，重跑即可自愈已完成的部分。
    """
    # source/ 是符号链接或（Windows 原生）目录联接时拒绝处置：这类 reparse point 会让
    # source_dir 之下的路径解析穿透到项目外目录，unlink/rename 因此可能作用到外部文件；
    # is_junction() 3.12+ 可用、POSIX 上恒为 False，与 EpisodePlanner._reconcile_derived_files
    # 的同类校验保持一致。派生集文件自身是符号链接则不受影响——unlink/rename 操作的是链接
    # 条目本身、不跟随最终一段的链接目标，悬空链接同样能被安全清理
    _assert_source_directory_safe(project_dir)
    deleted: list[str] = []
    archived: list[tuple[str, str]] = []
    for path in plan.deletes:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise EpisodeResetError(f"派生集文件删除失败，重置已中止：{path.name}: {exc}") from exc
        deleted.append(_rel(project_dir, path))
    for path in plan.archives:
        target = archive_targets[path] if archive_targets is not None else archive_episode_file_path(path)
        try:
            path.rename(target)
        except OSError as exc:
            raise EpisodeResetError(f"集文件留底改名失败，重置已中止：{path.name}: {exc}") from exc
        archived.append((_rel(project_dir, path), _rel(project_dir, target)))
    # 余文文件不是源文，也不记录规划进度（起点由账本推导）；留着只会让用户在 source/ 下
    # 看到一份与账本无关的陈旧剩余正文，随重置一并清理
    remaining = project_dir / "source" / "_remaining.txt"
    if remaining.is_file():
        try:
            remaining.unlink()
        except OSError as exc:
            raise EpisodeResetError(f"余文文件清理失败，重置已中止：{remaining.name}: {exc}") from exc
    return deleted, archived


def _rel(project_dir: Path, path: Path) -> str:
    return path.relative_to(project_dir).as_posix()


@dataclass(frozen=True)
class _PartialReset:
    """部分重置的边界：保留段集 ID（按播出顺序）与保留段所引用源文的规范化全文。"""

    retained: tuple[int, ...]
    texts: Mapping[str, str]


def _resolve_partial_reset(project_dir: Path, project: Mapping[str, Any], *, episode_id: int) -> _PartialReset:
    """校验部分重置的前置条件，返回保留段。

    账本顺序即播出顺序：``episode_id`` 之前的条目是保留段，它及其后的切出集被清除。任一条件
    不满足都会让「保留到哪、退回到哪」无法安全推算，抛 :class:`EpisodeResetError` 并指引
    改用全量重置；调用方保证校验失败时账本不被改动：

    - 账本形状必须干净（``episodes`` 是列表、条目均为对象、集 ID 均可解析、为正整数且
      不重复）——部分重置与全量重置相反，不做「零前置校验」的损坏容忍
    - ``episode_id`` 必须在账本中，且不是播出顺序中的第一集（那是全量重置）
    - 全部已记录源文指纹须与当前源文一致（``mismatched_source_fingerprints``，与
      ``EpisodePlanner`` 的提交门禁同一套逃生口）
    - 保留段里带可信 ``source_range`` 的切出集须落在对应源文件当前长度界内且非空（``start < end``），
      并按播出顺序沿源文位置前进：同一源文件内后一条 ``start`` 不早于前一条 ``end``，跨源文件时
      切到整本源文清单中更靠后的文件。两集之间留下的未切分原文空段是合法的；重叠或倒退说明账本已损坏

    - 保留段里的切出集都须带结构完整的 ``source_range``：缺失的是旧流程留下的切出集，保留下来仍会拦住
      分集规划，部分重置解决不了

    自带原文与无原文的集不占源文位置，不参与坐标校验。
    """
    raw_episodes = project.get("episodes")
    if not isinstance(raw_episodes, list):
        raise EpisodeResetError(f"账本 episodes 字段形状异常，无法安全推算部分重置边界，{_FULL_RESET_HINT}")

    ordered: list[tuple[int, Mapping[str, Any]]] = []
    seen: set[int] = set()
    for entry in raw_episodes:
        if not isinstance(entry, Mapping):
            raise EpisodeResetError(f"账本存在非法条目（非对象），无法安全推算部分重置边界，{_FULL_RESET_HINT}")
        num = parse_episode_num(entry.get("episode"))
        if num is None:
            raise EpisodeResetError(f"账本存在无法解析集 ID 的条目，无法安全推算部分重置边界，{_FULL_RESET_HINT}")
        if num < 1:
            raise EpisodeResetError(
                f"账本存在非法集 ID {num}（须为正整数），无法安全推算部分重置边界，{_FULL_RESET_HINT}"
            )
        if num in seen:
            raise EpisodeResetError(f"账本存在重复集 ID {num}，无法安全推算部分重置边界，{_FULL_RESET_HINT}")
        seen.add(num)
        ordered.append((num, entry))

    boundary = next((index for index, (num, _entry) in enumerate(ordered) if num == episode_id), None)
    if boundary is None:
        raise EpisodeResetError(f"集 ID {episode_id} 不在账本中，无法从此处部分重置")
    retained = ordered[:boundary]

    current_sources = discover_sources(project_dir, project)
    mismatched = mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), current_sources)
    if mismatched:
        raise EpisodeResetError(
            f"源文件已被修改或移除：{'、'.join(mismatched)}；账本坐标绑定的是修改前的原文内容，"
            f"无法安全部分重置，{_FULL_RESET_HINT}"
        )

    text_by_rel = {doc.rel_path: doc.text for doc in current_sources}
    source_order = {doc.rel_path: idx for idx, doc in enumerate(current_sources)}
    prev: tuple[str, int] | None = None
    for num, entry in retained:
        if not is_cut_episode(entry):
            continue
        coords = parse_source_range(entry)
        if coords is None:
            raise EpisodeResetError(
                f"保留段里的集 ID {num} 是没有带可信原文范围记录（source_range）的切出集，部分重置后仍无法接续"
                f"分集规划，{_FULL_RESET_HINT}"
            )
        rel, start, end = coords
        text = text_by_rel.get(rel)
        if text is None or not (0 <= start < end <= len(text)):
            length = len(text) if text is not None else 0
            raise EpisodeResetError(
                f"集 ID {num} 的原文范围无效（源文件 {rel} 当前长度 {length}，记录范围 [{start}, {end})），"
                f"无法安全部分重置，{_FULL_RESET_HINT}"
            )
        if prev is not None:
            prev_rel, prev_end = prev
            advances = start >= prev_end if rel == prev_rel else source_order[rel] > source_order[prev_rel]
            if not advances:
                raise EpisodeResetError(
                    f"集 ID {num} 的原文范围与播出顺序中前一个切出集重叠或倒退，账本可能已损坏，"
                    f"无法安全部分重置，{_FULL_RESET_HINT}"
                )
        prev = (rel, end)

    return _PartialReset(retained=tuple(num for num, _entry in retained), texts=text_by_rel)


def _other_origin_episode_ids(project: Mapping[str, Any]) -> frozenset[int]:
    """自带原文与无原文的集 ID：两种重置都不动它们。账本损坏时按可解析的条目容忍计算。"""
    raw_episodes = project.get("episodes")
    return frozenset(
        num
        for entry in (raw_episodes if isinstance(raw_episodes, list) else [])
        if isinstance(entry, Mapping)
        and not is_cut_episode(entry)
        and (num := parse_episode_num(entry.get("episode"))) is not None
    )


def reset_episode_planning(
    project_path: str | Path,
    *,
    episode_id: int | None = None,
    confirm_consumed: bool = False,
) -> EpisodeResetResult | ResetConfirmationRequired:
    """重置分集规划账本。

    不给 ``episode_id``（或它是播出顺序中的第一个切出集）：全量重置，零前置校验，账本处于任何
    损坏状态都必须执行成功（见模块文档）。给出其他集：部分重置，保留播出顺序中它之前的集，
    见 :func:`_resolve_partial_reset` 的前置校验；校验不通过时指名具体原因并指引改用全量
    重置，账本不被改动。被清除的集 ID 不会被再次分配，重新规划出的集取历史最高号之后的新 ID。

    两种模式都对波及已消费集（账本标 consumed 或磁盘已有剧本 / script_plan 产物）且未
    ``confirm_consumed`` 时不执行，返回 :class:`ResetConfirmationRequired` 等待
    显式确认；确认后执行，下游产物一律保留。

    Raises:
        EpisodeResetError: ``episode_id`` 非正整数、部分重置前置校验未通过、或
            文件处置失败，均保证账本未被改动。
        EpisodeResetConflictError: 重置期间出现确认清单之外的已消费集。
    """
    if episode_id is not None and episode_id < 1:
        raise EpisodeResetError(f"episode_id 必须是正整数，收到 {episode_id}")

    project_dir = Path(project_path)
    pm = ProjectManager.for_project_dir(project_dir)
    project_name = project_dir.name

    # 锁外预扫描/前置校验只为二段确认与快速失败服务：校验不通过或需要确认时零写入返回，
    # 不进锁、不碰文件
    project = pm.load_project(project_name)
    if (
        episode_id is not None
        and (target := episode_entry(project, episode_id)) is not None
        and not is_cut_episode(target)
    ):
        raise EpisodeResetError(f"集（id={episode_id}）不是切自整本源文的集；分集规划重置只能从切出集起算")
    partial_from = None if episode_id is None or episode_id == first_cut_episode_id(project) else episode_id

    def _boundary(p: Mapping[str, Any]) -> _PartialReset | None:
        return None if partial_from is None else _resolve_partial_reset(project_dir, p, episode_id=partial_from)

    def _retained(p: Mapping[str, Any], boundary: _PartialReset | None) -> frozenset[int]:
        kept = _other_origin_episode_ids(p)
        return kept | frozenset(boundary.retained) if boundary is not None else kept

    boundary = _boundary(project)
    plan = _scan(project_dir, project, retained=_retained(project, boundary))
    if plan.consumed and not confirm_consumed:
        return ResetConfirmationRequired(
            consumed_episodes=plan.consumed,
            archived_files=[_rel(project_dir, path) for path in plan.archives],
        )

    # 结果只能在锁内（按锁内复扫的实际处置）拼出，用闭包变量带回锁外
    committed: list[EpisodeResetResult] = []
    commit_plan: _ResetPlan | None = None
    snapshot_texts: dict[str, str] = {}
    committed_project: dict[str, Any] = {}
    manifest_expected: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    manifest_removals: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    recover_unreadable_manifest = False

    def _commit(p: dict[str, Any]) -> None:
        nonlocal commit_plan, manifest_expected, manifest_removals, recover_unreadable_manifest, snapshot_texts
        # 锁内重新校验/重新扫描：确认清单与前置校验都是锁外读取时刻的快照，期间源文件
        # 可能被外部改动、也可能出现清单之外的新消费集
        locked_boundary = _boundary(p)
        retained = _retained(p, locked_boundary)
        current = _scan(project_dir, p, retained=retained)
        if any(num not in plan.consumed for num in current.consumed):
            raise EpisodeResetConflictError("重置期间出现新的已消费集，需重新确认后再执行")
        raw_episodes = p.get("episodes")
        p["episodes"] = [
            entry
            for entry in (raw_episodes if isinstance(raw_episodes, list) else [])
            if isinstance(entry, Mapping) and parse_episode_num(entry.get("episode")) in retained
        ]
        if locked_boundary is not None:
            snapshot_texts = dict(locked_boundary.texts)
        else:
            p.pop(SOURCE_FINGERPRINTS_KEY, None)
        try:
            snapshot = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
        except ArtifactManifestError:
            if locked_boundary is not None:
                raise
            # Full planning reset is the zero-precondition recovery path.
            # An unreadable Manifest proves no current claim, so replace its
            # complete state with an empty valid snapshot during commit.
            recover_unreadable_manifest = True
        else:
            manifest_expected = {
                key: entry
                for key, entry in snapshot.items()
                if key.episode_number is not None and key.episode_number not in retained
            }
            manifest_removals = dict.fromkeys(manifest_expected)
        commit_plan = current
        committed_project.update(p)

    def _commit_side_effects(_project_file: Path) -> None:
        if commit_plan is None:  # pragma: no cover - update_project calls mutate before on_commit
            raise EpisodeResetError("重置未执行：文件处置计划未生成")
        _assert_source_directory_safe(project_dir)
        archive_targets = {path: archive_episode_file_path(path) for path in commit_plan.archives}
        remaining = project_dir / "source" / "_remaining.txt"
        transaction_paths = [*commit_plan.deletes, remaining]
        for source, target in archive_targets.items():
            transaction_paths.extend((source, target))
        snapshot_dir = project_dir / SOURCE_SNAPSHOTS_DIR
        if snapshot_dir.is_dir() and not snapshot_dir.is_symlink():
            transaction_paths.extend(path for path in snapshot_dir.iterdir() if path.is_file())
        transaction_paths.extend(
            source_snapshot_path(project_dir, rel) for rel in whole_source_files(committed_project)
        )

        from lib.artifacts.artifact_activation import register_artifact_entries_atomically

        with formal_write_transaction(*transaction_paths):
            deleted, archived = _apply_files(
                project_dir,
                commit_plan,
                archive_targets=archive_targets,
            )
            sync_source_snapshots(project_dir, committed_project, snapshot_texts)
            if manifest_removals:
                register_artifact_entries_atomically(
                    project_dir,
                    manifest_removals,
                    expected_entries=manifest_expected,
                )
            elif recover_unreadable_manifest:
                ProjectArtifactManifestAdapter(project_dir).replace_unreadable_entries_atomically({})
        committed.append(
            EpisodeResetResult(
                removed_episodes=commit_plan.episode_nums,
                deleted_files=deleted,
                archived_files=archived,
                consumed_episodes=commit_plan.consumed,
            )
        )

    pm.update_project(project_name, _commit, on_commit=_commit_side_effects)
    if not committed:  # pragma: no cover - update_project 必然调用 mutate_fn
        raise EpisodeResetError("重置未执行：账本更新回调未被调用")
    result = committed[0]
    logger.info(
        "分集规划已%s重置：项目 %s，清空 %d 集，删除派生文件 %d 个，留底 %d 个",
        "全量" if partial_from is None else f"部分（从集 ID {partial_from} 起）",
        project_name,
        len(result.removed_episodes),
        len(result.deleted_files),
        len(result.archived_files),
    )
    return result


__all__ = [
    "EpisodeResetConflictError",
    "EpisodeResetError",
    "EpisodeResetResult",
    "ResetConfirmationRequired",
    "reset_episode_planning",
]
