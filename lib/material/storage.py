"""素材存储入口：内容版本在本地素材存储里的保存与读取（见 ``docs/adr/0109``）。

素材是项目内一个资源的版本历史（``MaterialRef``），内容版本是其中的一条版本记录，有三项彼此独立的事实：

- ``content_version_id``：内容版本在项目内的稳定身份。改选、恢复、资源改名都不改变它；
  不由内容哈希推出，同字节的两条版本记录（含不同项目里的副本）是两个内容版本。
- ``content_digest``：内容的裸 sha256 hexdigest，读取时据此校验。
- ``file``：项目内的版本快照路径。没有 ``storage`` 字段的记录，它就是本地素材存储里的持久定位。
- ``storage``：远端内容版本的持久定位（:class:`ObjectLocator`：所属对象存储连接、桶与对象 key），
  见 ``docs/adr/0116``。有这个字段时 ``file`` 只是本地工作文件的位置，可以清理、按需从远端取回。

资源的正式文件（如 ``characters/Alice.png``）是选中内容版本的本地工作副本，不是持久定位。
新内容版本的保存经 :class:`NewContentStorage`（默认本地，正式提交入口可换成远端，见
``lib.material.formal_objects``），版本选择前的读取经 :meth:`LocalMaterialStorage.open`；路径是否落在
受管快照桶内由版本管理器先行校验。远端内容版本的读取（取回、校验与使用保护）经 ``lib.material.work_files``。
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lib.infra.api_errors import ConflictError
from lib.infra.content_digest import sha256_file

#: 版本记录里内容版本身份的键。
CONTENT_VERSION_ID = "content_version_id"
#: 版本记录里内容摘要的键：该版本快照的裸 sha256 hexdigest，算法同 ``lib.infra.content_digest.sha256_file``。
CONTENT_DIGEST = "content_digest"
#: 版本记录里远端持久定位的键，值见 :meth:`ObjectLocator.to_record`；没有这个键的记录按本地定位解释。
STORAGE = "storage"
_OBJECT_STORAGE_KIND = "object_storage"


class VersionSnapshotCorruptedError(ConflictError):
    """A version snapshot no longer matches the content digest recorded when the version was created."""

    def __init__(self, resource_type: str, resource_id: str, version: object) -> None:
        super().__init__(
            "version_snapshot_corrupted", resource_type=resource_type, resource_id=resource_id, version=version
        )


@dataclass(frozen=True, slots=True)
class MaterialRef:
    """素材身份：项目内一个资源的版本历史。资产条目只是使用它的创作对象。"""

    resource_type: str
    resource_id: str


@dataclass(frozen=True, slots=True)
class LocalLocator:
    """本地素材存储里的持久定位：项目内相对路径的版本快照。"""

    path: str


@dataclass(frozen=True, slots=True)
class ObjectLocator:
    """对象存储里的持久定位：写入时的连接 ID、桶与对象 key。

    读取时只按这里记下的连接与桶取对象，连接被改成另一个桶后不重新解释，签名 URL 也不进入定位。
    """

    connection_id: int
    bucket: str
    key: str

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": _OBJECT_STORAGE_KIND,
            "connection_id": self.connection_id,
            "bucket": self.bucket,
            "key": self.key,
        }

    @classmethod
    def from_record(cls, value: object) -> ObjectLocator | None:
        """版本记录 ``storage`` 字段的值；缺省或形状不对时返回 ``None``（按本地定位解释）。"""
        if not isinstance(value, Mapping) or value.get("kind") != _OBJECT_STORAGE_KIND:
            return None
        connection_id, bucket, key = value.get("connection_id"), value.get("bucket"), value.get("key")
        if not isinstance(connection_id, int) or isinstance(connection_id, bool):
            return None
        if not isinstance(bucket, str) or not isinstance(key, str) or not bucket or not key:
            return None
        return cls(connection_id, bucket, key)


@dataclass(frozen=True, slots=True)
class ContentVersion:
    """素材的一个内容版本。

    ``id`` 与 ``digest`` 为 ``None`` 只出现在早于内容版本身份的版本记录上；需要引用这类记录时
    经 ``VersionManager.ensure_content_version`` 补写。``stored`` 不为空时持久定位在对象存储，
    ``locator`` 只是本地工作文件的位置。
    """

    material: MaterialRef
    version: int
    id: str | None
    digest: str | None
    locator: LocalLocator
    stored: ObjectLocator | None = None

    @property
    def persistent_locator(self) -> LocalLocator | ObjectLocator:
        return self.stored or self.locator


@dataclass(frozen=True, slots=True)
class PersistedContent:
    """一次内容版本保存的结果：保存后内容的摘要，以及远端保存时的持久定位。"""

    digest: str
    stored: ObjectLocator | None = None


class NewContentStorage(Protocol):
    """新内容版本的保存方式。版本管理器写每条新版本记录前调用 :meth:`persist`。

    实现必须在返回前完成持久保存（远端须已确认）；抛错时版本管理器回滚这次提交，``slot`` 上的半截快照
    由版本管理器删除。``slot`` 是这条记录的版本快照路径：本地保存时就是持久定位，远端保存时是工作文件。
    """

    def persist(self, project_path: Path, resource_type: str, source: Path, slot: LocalLocator) -> PersistedContent: ...


def new_content_version_id() -> str:
    """新内容版本的身份：随机生成，与内容无关。"""

    return uuid.uuid4().hex


class LocalMaterialStorage:
    """项目目录下的本地素材存储。方法都是同步磁盘操作，异步调用方在工作线程里调用。

    远端内容版本的 :meth:`open` 只读本地工作文件；工作文件不在时调用方先经
    ``lib.material.work_files.ensure_content_version_file`` 取回。
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path)

    def persist(self, source: Path, locator: LocalLocator) -> str:
        """把 ``source`` 的字节保存到 ``locator``，返回保存后内容的摘要。

        摘要取自保存后的快照而不是 ``source``，记录下的是持久定位上实际留存的字节。
        失败时可能留下不完整的快照，由调用方的回滚删除。
        """

        target = self.project_path / locator.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return sha256_file(target)

    def open(self, content_version: ContentVersion) -> Path:
        """返回可读取该内容版本的本地文件；内容与记录的摘要不符时拒绝。

        记录没有摘要（早期未选中的版本）时不校验。

        Raises:
            FileNotFoundError: 持久定位上没有文件。
            VersionSnapshotCorruptedError: 文件内容与记录的摘要不符。
        """

        path = self.project_path / content_version.locator.path
        if not path.is_file():
            raise FileNotFoundError(f"版本文件不存在: {path}")
        verify_content(content_version, path)
        return path


def verify_content(content_version: ContentVersion, path: Path) -> None:
    """按内容版本记录的摘要校验 ``path``；记录没有摘要时放行。"""

    expected = content_version.digest
    if expected is not None and sha256_file(path) != expected:
        material = content_version.material
        raise VersionSnapshotCorruptedError(material.resource_type, material.resource_id, content_version.version)


_NEW_CONTENT_STORAGE: ContextVar[NewContentStorage | None] = ContextVar("new_content_storage", default=None)


def active_new_content_storage() -> NewContentStorage | None:
    """当前正式提交指定的新内容版本保存方式；``None`` 表示本地。"""
    return _NEW_CONTENT_STORAGE.get()


@contextmanager
def use_new_content_storage(storage: NewContentStorage | None) -> Generator[None]:
    """在这段上下文里写入的新内容版本改用 ``storage`` 保存。

    上下文变量随 ``asyncio.to_thread`` 复制进工作线程，正式提交在线程里写版本记录时同样生效。
    只供正式提交入口（``lib.material.formal_objects.formal_commit``）使用，业务方不直接调用。
    """
    token = _NEW_CONTENT_STORAGE.set(storage)
    try:
        yield
    finally:
        _NEW_CONTENT_STORAGE.reset(token)


__all__ = [
    "CONTENT_DIGEST",
    "CONTENT_VERSION_ID",
    "STORAGE",
    "ContentVersion",
    "LocalLocator",
    "LocalMaterialStorage",
    "MaterialRef",
    "NewContentStorage",
    "ObjectLocator",
    "PersistedContent",
    "VersionSnapshotCorruptedError",
    "active_new_content_storage",
    "new_content_version_id",
    "use_new_content_storage",
    "verify_content",
]
