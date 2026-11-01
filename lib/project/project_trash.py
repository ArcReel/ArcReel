"""项目回收站：项目内一切删除的唯一出口（``docs/adr/0107``）。

对外只有这几个操作：:func:`record_deletion` 记一次删除、:func:`list_records` 列出、:func:`restore` 恢复、
:func:`purge` 彻底删除、:func:`empty` 清空、:func:`purge_expired` 过期清理。删除记录是
``<项目>/.arcreel/trash/<记录 ID>/``：``record.json`` 记对象描述、被删文件、schema 版本、删除时间、发起方与
占用字节数，被删文件按原相对路径放在 ``files/`` 下；结构删除另把删除前的项目结构文件（:func:`structure_files`）
按原相对路径复制在 ``snapshot/`` 下，恢复时由 :mod:`lib.project.trash_restore` 的流水线处理。流水线降级恢复后，
记录只剩无法加回的结构文件，成为只能下载的记录（``download_only``）。目录在项目私有区，
因此不入归档、不入数据校验、文件视图也不列出；已删除项目整体移动时随项目一起走。

这里是项目数据真正被删掉的地方，``scripts/audit_conventions.py`` 的硬删除检查豁免本模块。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal

from lib.agent.agent_memory_paths import ARCREEL_DIRNAME
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_ledger import SOURCE_TEXT_SUFFIXES
from lib.infra.api_errors import ConflictError, NotFoundError
from lib.infra.json_io import atomic_write_json
from lib.project.project_change_hints import (
    emit_project_change_batch,
    emit_project_change_hint,
    get_project_change_source,
)
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import resource_of_path, resource_relative_path

__all__ = [
    "FILES_DIRNAME",
    "RETENTION",
    "SNAPSHOT_DIRNAME",
    "TRASH_DIRNAME",
    "TRASH_ENTITY_TYPE",
    "RestoreResult",
    "RestoredFile",
    "TrashRecord",
    "empty",
    "is_structure_file",
    "keep_for_download",
    "land_files",
    "list_records",
    "open_record_file",
    "purge",
    "purge_expired",
    "record_deletion",
    "restore",
    "structure_files",
    "trash_dir",
    "withdraw",
]

TRASH_DIRNAME = "trash"
#: 删除记录按删除时间保留 30 天。
RETENTION = timedelta(days=30)
#: 删除记录本身的格式版本；与项目 schema 版本（``schema_version``）无关。
RECORD_FORMAT = 1

_RECORD_FILE = "record.json"
FILES_DIRNAME = "files"
SNAPSHOT_DIRNAME = "snapshot"
_RECORD_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

Origin = Literal["user", "agent"]

#: 回收站变化的项目变更条目：与任务终态、记账结算一样是刷新信号，不进通知与聚焦。
TRASH_ENTITY_TYPE = "trash"


def trash_dir(project_dir: Path) -> Path:
    return project_dir / ARCREEL_DIRNAME / TRASH_DIRNAME


@dataclass(frozen=True, slots=True)
class TrashRecord:
    """一条删除记录。``object`` 是对象描述，至少含 ``kind``；``files`` 是被删文件删除前的项目内相对路径。"""

    id: str
    object: Mapping[str, Any]
    files: tuple[str, ...]
    schema_version: int
    deleted_at: datetime
    origin: Origin
    size: int
    has_snapshot: bool = False
    #: 降级恢复后剩下的结构文件只能下载，不能再恢复。
    download_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": dict(self.object),
            "files": list(self.files),
            "schema_version": self.schema_version,
            "deleted_at": self.deleted_at.isoformat(),
            "origin": self.origin,
            "size": self.size,
            "has_snapshot": self.has_snapshot,
            "download_only": self.download_only,
        }


@dataclass(frozen=True, slots=True)
class RestoredFile:
    """被恢复的一个文件落在哪里：``as_history`` 为真时它成了 ``resource`` 的一条历史版本。"""

    original: str
    path: str
    as_history: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"original": self.original, "path": self.path, "as_history": self.as_history}


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """一次恢复的结果。``object`` 是加回后的对象描述（换了名或 ID 时与记录里的不同）。

    ``degraded`` 为真时对象无法按原结构恢复，只加回了其中的文件；``download_only`` 是留在记录里、只能下载的结构文件。
    """

    object: Mapping[str, Any]
    restored: list[RestoredFile]
    degraded: bool = False
    download_only: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": dict(self.object),
            "restored": [item.to_dict() for item in self.restored],
            "degraded": self.degraded,
            "download_only": list(self.download_only),
        }


# ---------------------------------------------------------------- 记录

#: 结构快照收的单个文件：``project.json``、产物清单与版本历史记录。
_STRUCTURE_FILES = ("project.json", ".arcreel_artifacts.json", "versions/versions.json")
#: 结构快照收的目录与其中的文件后缀：剧本、规划草稿、剪辑时间线与源文文本。
_STRUCTURE_TREES: Mapping[str, frozenset[str]] = {
    "scripts": frozenset({".json"}),
    "drafts": frozenset({".json", ".md", ".txt"}),
    "edit_timelines": frozenset({".json"}),
    "source": frozenset(SOURCE_TEXT_SUFFIXES),
}


def is_structure_file(rel: str) -> bool:
    """项目内相对 POSIX 路径 ``rel`` 是否属于结构文件（与 :func:`structure_files` 同一口径）。"""
    if rel in _STRUCTURE_FILES:
        return True
    top, *rest = rel.split("/")
    suffixes = _STRUCTURE_TREES.get(top)
    return (
        suffixes is not None
        and bool(rest)
        and not any(part.startswith(".") for part in rest)
        and PurePosixPath(rel).suffix.lower() in suffixes
    )


def structure_files(project_dir: Path) -> list[str]:
    """项目当前的结构文件（项目内相对 POSIX 路径）：结构快照的内容，不含其余媒体。

    只收普通文件，不跟随符号链接；以 ``.`` 开头的锁与隐藏文件不收。
    """
    found = [rel for rel in _STRUCTURE_FILES if _is_plain_file(project_dir / rel)]
    for top, suffixes in _STRUCTURE_TREES.items():
        root = project_dir / top
        if root.is_symlink() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if not name.startswith(".") and path.suffix.lower() in suffixes and _is_plain_file(path):
                    found.append(path.relative_to(project_dir).as_posix())
    return found


def record_deletion(
    project_dir: Path,
    obj: Mapping[str, Any],
    files: Sequence[str],
    *,
    snapshot: bool = False,
    sources: Mapping[str, Path] | None = None,
    before: Mapping[str, bytes] | None = None,
) -> TrashRecord:
    """把 ``files``（项目内相对 POSIX 路径，必须是普通文件）移进一条新的删除记录。

    调用方持有项目锁，并已在同一临界区里确认这些文件可以删除；发起方取当前的变更来源。``snapshot``
    为真时先把删除前的结构文件复制进记录（结构删除用；被删文件本身不重复复制），恢复时按身份从中取出对象。
    文件实际不在该路径上时（例如生成结果的暂存文件），``sources`` 给出它的位置，记录里仍记原路径。
    调用方已在同一临界区里改写过的结构文件（如删集时的 ``project.json``），由 ``before`` 给出删除前的内容。
    结构删除的对象可以没有被删文件（它只存在于结构文件里）。
    任一文件移动失败时已移走的文件搬回原处，不留下半条记录；记录建立后提交失败时用 :func:`withdraw` 撤回。
    """
    if "kind" not in obj or not (files or snapshot):
        raise ValueError("删除记录需要对象类型与至少一个被删文件")
    record_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    record_dir = trash_dir(project_dir) / record_id
    stored = record_dir / FILES_DIRNAME
    moved: list[tuple[Path, Path]] = []
    try:
        size = 0
        stored.mkdir(parents=True)  # 没有被删文件的结构删除也有这个目录：恢复流水线从这里铺设
        if snapshot:
            deleted = set(files)
            for rel in structure_files(project_dir):
                if rel not in deleted:
                    target = record_dir / SNAPSHOT_DIRNAME / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if before is not None and rel in before:
                        target.write_bytes(before[rel])
                    else:
                        shutil.copy2(project_dir / rel, target)
                    size += target.stat().st_size
        for rel in files:
            source = (sources or {}).get(rel) or project_dir / _checked_rel(rel)
            if source.is_symlink() or not source.is_file():
                raise FileNotFoundError(rel)
            target = stored / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            size += source.stat().st_size
            os.replace(source, target)
            moved.append((target, source))
        record = TrashRecord(
            id=record_id,
            object=dict(obj),
            files=tuple(files),
            schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
            deleted_at=datetime.now(UTC),
            origin="user" if get_project_change_source() == "webui" else "agent",
            size=size,
            has_snapshot=snapshot,
        )
        # record.json 最后写：没有它的目录不算记录，列出时跳过。
        atomic_write_json(record_dir / _RECORD_FILE, {"format": RECORD_FORMAT, **record.to_dict()})
    except BaseException:
        for target, source in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, source)
        shutil.rmtree(record_dir, ignore_errors=True)
        raise
    _notify(project_dir)
    return record


def withdraw(project_dir: Path, record_id: str) -> None:
    """撤回同一临界区里刚建立的删除记录：被删文件搬回原处，记录消失。删除提交失败时补偿用；调用方持有项目锁。"""
    record = require_record(project_dir, record_id)
    record_dir = trash_dir(project_dir) / record.id
    for rel in record.files:
        target = project_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(record_dir / FILES_DIRNAME / rel, target)
    shutil.rmtree(record_dir)
    _notify(project_dir)


def list_records(project_dir: Path) -> list[TrashRecord]:
    """全部删除记录，新删除的在前。读不出的记录跳过。"""
    root = trash_dir(project_dir)
    if not root.is_dir():
        return []
    records = [record for entry in root.iterdir() if (record := _load(entry)) is not None]
    return sorted(records, key=lambda record: record.deleted_at, reverse=True)


# ---------------------------------------------------------------- 恢复与清理


def restore(project_dir: Path, record_id: str) -> RestoreResult:
    """把一条删除记录加回项目，成功后记录消失；失败时项目与记录都保持恢复前的样子。

    带结构快照的记录走恢复流水线（:mod:`lib.project.trash_restore`），对象无法按原结构恢复时降级为只加回文件。
    只涉及文件的记录按 :func:`land_files` 的规则加回。只能下载的记录不能恢复。
    """
    record = require_record(project_dir, record_id)
    if record.download_only:
        raise ConflictError("trash_restore_download_only", id=record.id)
    if record.has_snapshot:
        # 恢复流水线经各对象的落地规则依赖领域模块，顶层导入会成环。
        from lib.project.trash_restore import restore_structural

        result = restore_structural(project_dir, record)
        _notify(project_dir)
        return result
    with project_metadata_lock(project_dir):
        record = require_record(project_dir, record_id)
        record_dir = trash_dir(project_dir) / record.id
        restored = land_files(
            project_dir,
            [(record_dir / FILES_DIRNAME / rel, rel) for rel in record.files],
            record.object,
            then=lambda: shutil.rmtree(record_dir),
        )
    _notify(project_dir)
    return RestoreResult(record.object, restored)


def land_files(
    project_dir: Path, files: Sequence[tuple[Path, str]], obj: Mapping[str, Any], *, then: Callable[[], None]
) -> list[RestoredFile]:
    """把 ``(待加回的文件, 项目内相对路径)`` 逐个加回项目，全部成功后执行 ``then``；调用方持有项目锁。

    原路径空闲则回原路径；原路径已有文件时，它是某个资源的当前文件就作为该资源的历史版本加回，否则换一个新文件名。
    从不覆盖现有文件。任一步失败时已落回的文件搬回原处，项目回到加回之前。
    """
    for _source, rel in files:
        check_restore_target(project_dir, rel)
    moved: list[tuple[Path, Path]] = []
    with VersionManager(project_dir).history_transaction():
        try:
            restored = [_land_file(project_dir, source, rel, obj, moved) for source, rel in files]
            then()
        except BaseException:
            for source, target in reversed(moved):
                os.replace(target, source)
            raise
    return restored


def keep_for_download(project_dir: Path, record_id: str, files: Sequence[str]) -> None:
    """降级恢复后把记录改成只剩 ``files``（记录里的结构文件）、只能下载；调用方持有项目锁。"""
    record = require_record(project_dir, record_id)
    record_dir = trash_dir(project_dir) / record.id
    stored = record_dir / FILES_DIRNAME
    kept = replace(
        record,
        files=tuple(files),
        size=sum((stored / rel).stat().st_size for rel in files),
        has_snapshot=False,
        download_only=True,
    )
    # record.json 先改：之后清理中断时，记录照样只列出剩下的文件。
    atomic_write_json(record_dir / _RECORD_FILE, {"format": RECORD_FORMAT, **kept.to_dict()})
    shutil.rmtree(record_dir / SNAPSHOT_DIRNAME, ignore_errors=True)
    for rel in record.files:
        if rel not in kept.files:
            (stored / rel).unlink(missing_ok=True)


def open_record_file(project_dir: Path, record_id: str, rel: str) -> tuple[BinaryIO, os.stat_result]:
    """以不跟随符号链接的方式打开记录里的一个被删文件，供下载；调用方负责关闭句柄。"""
    # 文件视图依赖本模块，顶层导入会成环。
    from lib.project.file_view import open_project_file

    record = require_record(project_dir, record_id)
    if rel not in record.files:
        raise NotFoundError("file_not_found", path=rel)
    try:
        return open_project_file(trash_dir(project_dir) / record.id / FILES_DIRNAME, rel)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise NotFoundError("file_not_found", path=rel) from exc


def consume(project_dir: Path, record_id: str) -> None:
    """恢复流水线落地成功后删去记录；调用方持有项目锁。记录已被并发的恢复或清理拿走时抛 ``trash_record_not_found``。"""
    shutil.rmtree(trash_dir(project_dir) / require_record(project_dir, record_id).id)


def purge(project_dir: Path, record_id: str) -> None:
    """彻底删除一条记录。"""
    with project_metadata_lock(project_dir):
        record = require_record(project_dir, record_id)
        shutil.rmtree(trash_dir(project_dir) / record.id)
    _notify(project_dir)


def empty(project_dir: Path) -> int:
    """清空项目回收站，返回删掉的记录数。"""
    with project_metadata_lock(project_dir):
        records = list_records(project_dir)
        shutil.rmtree(trash_dir(project_dir), ignore_errors=True)
    _notify(project_dir)
    return len(records)


def purge_expired(project_dir: Path, *, now: datetime | None = None) -> int:
    """清除删除时间早于保留期的记录，返回清除数。"""
    if not trash_dir(project_dir).is_dir():
        return 0
    cutoff = (now or datetime.now(UTC)) - RETENTION
    with project_metadata_lock(project_dir):
        expired = [record for record in list_records(project_dir) if record.deleted_at <= cutoff]
        for record in expired:
            shutil.rmtree(trash_dir(project_dir) / record.id)
    if expired:
        _notify(project_dir)
    return len(expired)


# ---------------------------------------------------------------- 内部


def _notify(project_dir: Path) -> None:
    """广播回收站变化，并先替被删或加回的项目文件发一次同来源的 hint。

    这批只描述回收站自身；被删文件引起的项目变更（草稿消失、登记变化）由事件服务读盘补扫或轮询发现，
    来源按读盘前已到达的 hint 结算。hint 要先于这批送达，否则 Web 端的删除会被补扫成外部改动。
    """
    emit_project_change_hint(project_dir.name)
    change = {"entity_type": TRASH_ENTITY_TYPE, "action": "updated", "entity_id": "trash", "label": "trash"}
    emit_project_change_batch(project_dir.name, [{**change, "focus": None, "important": False}])


def _checked_rel(rel: str) -> str:
    path = PurePosixPath(rel)
    if not rel or "\\" in rel or path.is_absolute() or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise ValueError(f"非法的项目内路径: {rel!r}")
    return rel


def _load(record_dir: Path) -> TrashRecord | None:
    if not _RECORD_ID_RE.match(record_dir.name) or record_dir.is_symlink():
        return None
    try:
        data = json.loads((record_dir / _RECORD_FILE).read_text(encoding="utf-8"))
        return TrashRecord(
            id=record_dir.name,
            object=data["object"],
            files=tuple(_checked_rel(rel) for rel in data["files"]),
            schema_version=int(data["schema_version"]),
            deleted_at=datetime.fromisoformat(data["deleted_at"]),
            origin=data["origin"],
            size=int(data["size"]),
            has_snapshot=bool(data.get("has_snapshot", False)),
            download_only=bool(data.get("download_only", False)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def require_record(project_dir: Path, record_id: str) -> TrashRecord:
    """按 ID 读一条删除记录，不存在时抛 ``trash_record_not_found``。"""
    record = _load(trash_dir(project_dir) / record_id) if _RECORD_ID_RE.match(record_id) else None
    if record is None:
        raise NotFoundError("trash_record_not_found", id=record_id)
    return record


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def check_restore_target(project_dir: Path, rel: str) -> None:
    """恢复落点 ``rel`` 的途经目录不能是符号链接，否则抛 ``trash_restore_target_blocked``。"""
    target = project_dir / rel
    if any(parent.is_symlink() for parent in target.parents if parent.is_relative_to(project_dir)):
        # 删除之后有人把途经的目录换成了符号链接：不跟着它写到别处。
        raise ConflictError("trash_restore_target_blocked", path=rel)


def _land_file(
    project_dir: Path, stored: Path, rel: str, obj: Mapping[str, Any], moved: list[tuple[Path, Path]]
) -> RestoredFile:
    """加回一个文件；搬出记录的 ``(记录内位置, 落点)`` 追加到 ``moved``，供失败时搬回。"""
    target = project_dir / rel
    if obj.get("kind") == "version" and obj.get("resource_type") in VersionManager.RESOURCE_TYPES:
        # 被删的历史版本重新登记进版本历史：只把快照放回原路径，版本历史里不会有它。
        resource_type, resource_id = str(obj["resource_type"]), str(obj["resource_id"])
        path = VersionManager(project_dir).add_history_version(
            resource_type,
            resource_id,
            stored,
            current_file=project_dir / resource_relative_path(resource_type, resource_id),
            prompt=str(obj.get("prompt") or ""),
            metadata=obj["metadata"] if isinstance(obj.get("metadata"), dict) else None,
        )
        return RestoredFile(rel, path, as_history=True)
    if not (target.exists() or target.is_symlink()):
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stored, target)
        moved.append((stored, target))
        return RestoredFile(rel, rel)
    resource = resource_of_path(rel)
    if resource is not None and target.is_file() and not target.is_symlink():
        path = VersionManager(project_dir).add_history_version(*resource, stored, current_file=target)
        return RestoredFile(rel, path, as_history=True)
    path = PurePosixPath(rel)
    for n in range(1, 1000):
        suffix = f" ({n}){path.suffix}"
        stem = path.stem
        if obj.get("kind") == "memory":
            # 记忆存储依赖回收站，文件名约束在落地时读取，避免顶层循环依赖。
            from lib.agent.agent_memory_store import FILENAME_MAX_LENGTH

            stem = stem[: FILENAME_MAX_LENGTH - len(suffix)]
        candidate = path.with_name(f"{stem}{suffix}").as_posix()
        if not ((project_dir / candidate).exists() or (project_dir / candidate).is_symlink()):
            os.replace(stored, project_dir / candidate)
            moved.append((stored, project_dir / candidate))
            return RestoredFile(rel, candidate)
    raise FileExistsError(rel)
