"""恢复流水线：带结构快照的删除记录只经这一条路径恢复（``docs/adr/0107``）。

1. 在项目回收站目录下的临时目录里，把记录的结构快照与被删文件铺成一个临时项目；
2. 跑源文编码迁移与项目迁移链（与归档导入相同的两步，schema 相同时为空操作）。归档导入的目录树修复、
   数据校验与导入激活面向完整项目，在缺少其余媒体的临时项目上会误报，这里不跑；
3. 按对象类型的落地规则，按身份（不是删除时的原路径）从迁移后的临时项目取出对象；
4. 先取规则声明的领域锁、再取项目锁，再核对一次记录仍在，按落地规则把对象加回当前项目，并为加回的产物补录。
   落地失败时撤掉本次新建的文件，记录原样保留。

迁移失败、或迁移后的临时项目里取不出对象时降级（没有被删文件的记录不降级，记录保留）。读写故障（``OSError``，
文件缺失除外）不降级：磁盘满、权限不足等可以排除后重试，错误原样抛出，记录保留。降级时被删的媒体等文件按迁移后（迁移中途失败时为失败那一刻）的路径，
照只涉及文件的记录的规则加回（原路径空闲则回原路径，否则作为历史版本或换新名）；被删的结构文件不加回，
留在记录里只能下载。

临时项目迁移得出的项目级状态（如集 ID 高水位）留在临时目录里随之丢弃：落地规则只把取出的对象加回当前项目。

新的对象类型在 :func:`_rules` 里登记一个取出函数，返回描述落地的 :class:`Restoration`。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.api_errors import BadRequestError, ConflictError
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.json_io import atomic_write_json
from lib.project import project_trash
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_trash import RestoredFile, RestoreResult, TrashRecord
from lib.script.source_loader.migration import migrate_project_source_encoding

__all__ = ["Landing", "Restoration", "RestoreBlocked", "TakeRule", "restore_structural"]

logger = logging.getLogger(__name__)

#: 迁移步可能就地改写文本文件；这些后缀复制进临时项目，其余（媒体）用硬链接，免得大文件复制一遍。
_COPIED_SUFFIXES = frozenset({".json", ".md", ".txt"})
#: 迁移步留下的备份（``<名>.bak.v<N>-<ts>``）与规整时搁置的副本（``.kept.``），不是被删的文件。
_MIGRATION_COPY_RE = re.compile(r"\.(?:bak|kept)\.v\d+-\d+")


class RestoreBlocked(ConflictError):
    """对象此刻不能恢复、但之后可以（如所属的集也在项目回收站里）：取出规则抛它时不降级，记录原样保留。"""


class Landing:
    """一次恢复在当前项目里新建的文件与目录；:meth:`rollback` 撤掉它们，当前项目回到恢复前。从不覆盖现有文件。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.restored: list[RestoredFile] = []
        self._created: list[Path] = []

    def free(self, rel: str) -> bool:
        target = self.project_dir / rel
        return not (target.exists() or target.is_symlink())

    def place(self, staged: Path, rel: str, *, original: str) -> None:
        """把临时项目里的 ``staged`` 移到当前项目的 ``rel``；``rel`` 必须空闲。"""
        target = self._target(rel)
        os.replace(staged, target)
        self._done(target, rel, original)

    def write_json(self, rel: str, data: Any, *, original: str) -> None:
        target = self._target(rel)
        atomic_write_json(target, data)
        self._done(target, rel, original)

    def rollback(self) -> None:
        for path in reversed(self._created):
            if path.is_dir() and not path.is_symlink():
                # 新建的目录也撤掉：留下的空目录会让重试按身份冲突处理。期间被别处写进内容的目录留着。
                with suppress(OSError):
                    path.rmdir()  # hard-delete: rollback -- 只删本次新建且仍为空的目录
            else:
                # hard-delete: rollback -- 恢复落地失败时撤掉本次新建的文件，记录里的原件不动
                path.unlink(missing_ok=True)

    def _target(self, rel: str) -> Path:
        project_trash.check_restore_target(self.project_dir, rel)
        target = self.project_dir / rel
        if not self.free(rel):
            raise FileExistsError(rel)
        missing = [parent for parent in target.parents if not parent.exists()]
        target.parent.mkdir(parents=True, exist_ok=True)
        self._created.extend(reversed(missing))
        return target

    def _done(self, target: Path, rel: str, original: str) -> None:
        self._created.append(target)
        self.restored.append(RestoredFile(original, rel))


@dataclass(frozen=True, slots=True)
class Restoration:
    """从临时项目取出、待加回当前项目的一个对象。

    ``land`` 在 ``locks`` 与项目锁之内执行，经 :class:`Landing` 写文件，产物补录放在最后，返回加回后的对象描述
    （换了名或身份时）；它抛错时本次新建的文件被撤掉。``after`` 在锁外、落地成功后执行（发领域刷新信号等）。
    """

    land: Callable[[Landing], Mapping[str, Any] | None]
    locks: Callable[[], AbstractContextManager[object]] = nullcontext
    after: Callable[[], None] = field(default=lambda: None)


#: ``(当前项目目录, 迁移后的临时项目目录, 对象描述) -> Restoration``；对象已不在临时项目里时抛 ``trash_restore_failed``。
TakeRule = Callable[[Path, Path, Mapping[str, Any]], Restoration]


def _rules() -> Mapping[str, TakeRule]:
    # 各对象的取出与落地规则依赖本模块的 Landing / Restoration，顶层导入会成环。
    from lib.edit_timeline.trash import EDIT_TIMELINE_KIND, take_edit_timeline
    from lib.episode.episode_trash import EPISODE_KIND, take_episode
    from lib.episode.source_trash import SOURCE_FILE_KIND, take_source_file
    from lib.project import media_trash
    from lib.project.asset_trash import ASSET_DERIVATIVE_KIND, ASSET_KIND, take_asset, take_asset_derivative
    from lib.project.script_entry_trash import SCRIPT_ENTRY_KIND, take_script_entry
    from lib.script.planning_draft_trash import PLANNING_DRAFT_KIND, take_planning_draft

    return {
        EDIT_TIMELINE_KIND: take_edit_timeline,
        EPISODE_KIND: take_episode,
        media_trash.GRID_KIND: media_trash.take_grid,
        media_trash.END_FRAME_KIND: media_trash.take_end_frame,
        media_trash.REFERENCE_AUDIO_KIND: media_trash.take_reference_audio,
        SCRIPT_ENTRY_KIND: take_script_entry,
        SOURCE_FILE_KIND: take_source_file,
        PLANNING_DRAFT_KIND: take_planning_draft,
        ASSET_KIND: take_asset,
        ASSET_DERIVATIVE_KIND: take_asset_derivative,
    }


def restore_structural(project_dir: Path, record: TrashRecord) -> RestoreResult:
    """把一条带结构快照的删除记录经流水线加回 ``project_dir``，成功后记录消失；降级时记录只剩结构文件。调用方不持锁。"""
    rule = _rules().get(str(record.object.get("kind")))
    if rule is None:
        raise BadRequestError("trash_restore_unsupported")
    record_dir = project_trash.trash_dir(project_dir) / record.id
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=record_dir.parent) as tmp:
        # 临时项目按数据根布局摆放，落地规则可以在其中用 ProjectManager 走领域流程（如资产换名的改名级联）。
        staged = DataRootLayout(Path(tmp)).projects_dir / project_dir.name
        with project_metadata_lock(project_dir):
            # 铺设期间记录不能被彻底删除或被另一次恢复拿走。
            _require_unchanged(project_dir, record)
            _stage(record_dir, staged)
        try:
            migrate_project_source_encoding(staged)
            migrate_project_dir(staged)
            restoration = rule(project_dir, staged, record.object)
        except RestoreBlocked:
            raise
        except Exception as exc:
            if isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError):
                # 读写故障排除后重试就能按原结构恢复，降级会消耗记录、丢掉结构。文件缺失是永久状态，重试也不会成功，照常降级。
                raise
            if not record.files:
                # 只存在于结构里的对象降级后什么都加回不了，消耗记录就丢了它：记录保留，之后可以重试。
                logger.warning("删除记录 %s 无法按原结构恢复，记录保留", record.id, exc_info=True)
                raise ConflictError("trash_restore_unsupported") from exc
            # 失败原因记在日志里；创作者看到的是降级结果。
            logger.warning("删除记录 %s 无法按原结构恢复，降级为只恢复文件", record.id, exc_info=True)
            return _restore_files_only(project_dir, record, staged)
        landing = Landing(project_dir)
        with restoration.locks(), project_metadata_lock(project_dir):
            # 迁移期间锁已释放：并发的另一次恢复或到期清理可能已拿走记录，此时落地会留下没有记录的对象。
            _require_unchanged(project_dir, record)
            try:
                landed = restoration.land(landing)
                project_trash.consume(project_dir, record.id)
            except BaseException:
                landing.rollback()
                raise
    restoration.after()
    return RestoreResult(landed or record.object, landing.restored)


def _restore_files_only(project_dir: Path, record: TrashRecord, staged: Path) -> RestoreResult:
    """降级：临时项目里被删的非结构文件按它们此刻的路径加回，记录里的结构文件留着只能下载。"""
    files = [(staged / rel, rel) for rel in _non_structure_files(staged)]
    kept = tuple(rel for rel in record.files if project_trash.is_structure_file(rel))

    def settle() -> None:
        if kept:
            project_trash.keep_for_download(project_dir, record.id, kept)
        else:
            project_trash.consume(project_dir, record.id)

    with project_metadata_lock(project_dir):
        _require_unchanged(project_dir, record)
        restored = project_trash.land_files(project_dir, files, record.object, then=settle)
    return RestoreResult(record.object, restored, degraded=True, download_only=kept)


def _require_unchanged(project_dir: Path, record: TrashRecord) -> None:
    """记录仍在、且仍是这次恢复读到的样子；调用方持有项目锁。不在时抛 ``trash_record_not_found``。"""
    if project_trash.require_record(project_dir, record.id) != record:
        # 记录唯一的改写是并发的另一次降级恢复把它改成只能下载。
        raise ConflictError("trash_restore_download_only", id=record.id)


def _non_structure_files(staged: Path) -> list[str]:
    """临时项目里的非结构文件：结构快照只收结构文件，所以它们都是记录里被删的文件（可能已被迁移步挪了位置）。

    不收私有区、项目根下的迁移记录等隐藏文件、迁移备份与符号链接。
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(staged):
        top = Path(dirpath) == staged
        dirnames[:] = sorted(
            name for name in dirnames if not (top and name.startswith(".")) and not _MIGRATION_COPY_RE.search(name)
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(staged).as_posix()
            if (
                (top and name.startswith("."))
                or _MIGRATION_COPY_RE.search(name)
                or path.is_symlink()
                or not path.is_file()
                or project_trash.is_structure_file(rel)
            ):
                continue
            found.append(rel)
    return found


def _stage(record_dir: Path, staged: Path) -> None:
    snapshot = record_dir / project_trash.SNAPSHOT_DIRNAME
    if snapshot.is_dir():
        shutil.copytree(snapshot, staged, symlinks=True, dirs_exist_ok=True)
    shutil.copytree(
        record_dir / project_trash.FILES_DIRNAME, staged, symlinks=True, dirs_exist_ok=True, copy_function=_link_or_copy
    )


def _link_or_copy(source: str, target: str) -> None:
    if Path(source).suffix.lower() not in _COPIED_SUFFIXES:
        try:
            os.link(source, target)
            return
        except OSError:
            pass  # 文件系统不支持硬链接：退回复制
    shutil.copy2(source, target)
