"""素材存储入口：内容版本在本地素材存储里的保存与读取（见 ``docs/adr/0109``）。

素材是项目内一个资源的版本历史（``MaterialRef``），内容版本是其中的一条版本记录，有三项彼此独立的事实：

- ``content_version_id``：内容版本在项目内的稳定身份。改选、恢复、资源改名都不改变它；
  不由内容哈希推出，同字节的两条版本记录（含不同项目里的副本）是两个内容版本。
- ``content_digest``：内容的裸 sha256 hexdigest，读取时据此校验。
- ``file``：内容在本地素材存储里的持久定位，即项目内的版本快照路径。

资源的正式文件（如 ``characters/Alice.png``）是选中内容版本的本地工作副本，不是持久定位。
版本记录写入前的保存经 :meth:`LocalMaterialStorage.persist`，版本选择前的读取经
:meth:`LocalMaterialStorage.open`；路径是否落在受管快照桶内由版本管理器先行校验。
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from lib.infra.api_errors import ConflictError
from lib.infra.content_digest import sha256_file

#: 版本记录里内容版本身份的键。
CONTENT_VERSION_ID = "content_version_id"
#: 版本记录里内容摘要的键：该版本快照的裸 sha256 hexdigest，算法同 ``lib.infra.content_digest.sha256_file``。
CONTENT_DIGEST = "content_digest"


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
class ContentVersion:
    """素材的一个内容版本。

    ``id`` 与 ``digest`` 为 ``None`` 只出现在早于内容版本身份的版本记录上；需要引用这类记录时
    经 ``VersionManager.ensure_content_version`` 补写。
    """

    material: MaterialRef
    version: int
    id: str | None
    digest: str | None
    locator: LocalLocator


def new_content_version_id() -> str:
    """新内容版本的身份：随机生成，与内容无关。"""

    return uuid.uuid4().hex


class LocalMaterialStorage:
    """项目目录下的本地素材存储。方法都是同步磁盘操作，异步调用方在工作线程里调用。"""

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


__all__ = [
    "CONTENT_DIGEST",
    "CONTENT_VERSION_ID",
    "ContentVersion",
    "LocalLocator",
    "LocalMaterialStorage",
    "MaterialRef",
    "VersionSnapshotCorruptedError",
    "new_content_version_id",
    "verify_content",
]
