"""恢复流水线：带结构快照的删除记录只经这一条路径恢复（``docs/adr/0107``）。

1. 在项目回收站目录下的临时目录里，把记录的结构快照与被删文件铺成一个临时项目；
2. 跑源文编码迁移与项目迁移链（与归档导入相同的两步，schema 相同时为空操作）。归档导入的目录树修复、
   数据校验与导入激活面向完整项目，在缺少其余媒体的临时项目上会误报，这里不跑；
3. 按对象类型的落地规则，按身份（不是删除时的原路径）从迁移后的临时项目取出对象；
4. 先取规则声明的领域锁、再取项目锁，按落地规则把对象加回当前项目，并为加回的产物补录。
   落地失败时撤掉本次新建的文件，记录原样保留。

临时项目迁移得出的项目级状态（如集 ID 高水位）留在临时目录里随之丢弃：落地规则只把取出的对象加回当前项目。

新的对象类型在 :func:`_rules` 里登记一个取出函数，返回描述落地的 :class:`Restoration`。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.api_errors import BadRequestError, ConflictError
from lib.infra.json_io import atomic_write_json
from lib.project import project_trash
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_trash import RestoredFile, TrashRecord
from lib.script.source_loader.migration import migrate_project_source_encoding

__all__ = ["Landing", "Restoration", "TakeRule", "restore_structural"]

#: 迁移步可能就地改写文本文件；这些后缀复制进临时项目，其余（媒体）用硬链接，免得大文件复制一遍。
_COPIED_SUFFIXES = frozenset({".json", ".md", ".txt"})


class Landing:
    """一次恢复在当前项目里新建的文件；:meth:`rollback` 撤掉它们，当前项目回到恢复前。从不覆盖现有文件。"""

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
            # hard-delete: rollback -- 恢复落地失败时撤掉本次新建的文件，记录里的原件不动
            path.unlink(missing_ok=True)

    def _target(self, rel: str) -> Path:
        project_trash.check_restore_target(self.project_dir, rel)
        target = self.project_dir / rel
        if not self.free(rel):
            raise FileExistsError(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _done(self, target: Path, rel: str, original: str) -> None:
        self._created.append(target)
        self.restored.append(RestoredFile(original, rel))


@dataclass(frozen=True, slots=True)
class Restoration:
    """从临时项目取出、待加回当前项目的一个对象。

    ``land`` 在 ``locks`` 与项目锁之内执行，经 :class:`Landing` 写文件，产物补录放在最后；它抛错时本次新建的
    文件被撤掉。``after`` 在锁外、落地成功后执行（发领域刷新信号等）。
    """

    land: Callable[[Landing], None]
    locks: Callable[[], AbstractContextManager[object]] = nullcontext
    after: Callable[[], None] = field(default=lambda: None)


#: ``(当前项目目录, 迁移后的临时项目目录, 对象描述) -> Restoration``；对象已不在临时项目里时抛 ``trash_restore_failed``。
TakeRule = Callable[[Path, Path, Mapping[str, Any]], Restoration]


def _rules() -> Mapping[str, TakeRule]:
    # 各对象的取出与落地规则依赖本模块的 Landing / Restoration，顶层导入会成环。
    from lib.edit_timeline.trash import EDIT_TIMELINE_KIND, take_edit_timeline

    return {EDIT_TIMELINE_KIND: take_edit_timeline}


def restore_structural(project_dir: Path, record: TrashRecord) -> list[RestoredFile]:
    """把一条带结构快照的删除记录经流水线加回 ``project_dir``，成功后记录消失。调用方不持锁。"""
    rule = _rules().get(str(record.object.get("kind")))
    if rule is None:
        raise BadRequestError("trash_restore_unsupported")
    record_dir = project_trash.trash_dir(project_dir) / record.id
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=record_dir.parent) as tmp:
        staged = Path(tmp) / project_dir.name
        with project_metadata_lock(project_dir):
            # 铺设期间记录不能被彻底删除或被另一次恢复拿走。
            project_trash.require_record(project_dir, record.id)
            _stage(record_dir, staged)
        try:
            migrate_project_source_encoding(staged)
            migrate_project_dir(staged)
        except Exception as exc:
            raise ConflictError("trash_restore_failed", id=record.id) from exc
        restoration = rule(project_dir, staged, record.object)
        landing = Landing(project_dir)
        with restoration.locks(), project_metadata_lock(project_dir):
            try:
                restoration.land(landing)
                project_trash.consume(project_dir, record.id)
            except BaseException:
                landing.rollback()
                raise
    restoration.after()
    return landing.restored


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
