"""衍生资产图的级联清理：图、版本历史与产物清单条目一并消失。

衍生随本体条目内的一行登记而存在，删掉那行之后它的图没有任何入口能再被看到或还原，
留在盘上只会被重建的同名衍生接上一段不属于它的历史。与本体资产删除保留历史的口径
不同，这里三样一起清。

单列一个模块是因为它同时需要 :mod:`lib.artifact_registration`（清单）与
:mod:`lib.version_manager`（历史），而 :mod:`lib.asset_derivatives` 被产物规划反向依赖，
不能再引入这两者。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.artifact_registration import forget_current_resource_artifact
from lib.asset_derivatives import (
    derivative_artifact_id,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_table,
    derivative_version_dir,
)
from lib.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.version_manager import VersionManager

logger = logging.getLogger(__name__)


def purge_derivative_sheets(project_dir: Path, owner_name: str, derivative_names: Iterable[str]) -> None:
    """清掉这些衍生的清单条目、版本历史与当前图，最后收掉空掉的本体目录。

    先撤清单条目再删文件：中途失败留下的是「无人认领的字节」，而不是「指向不存在文件的
    正式产物」。文件删不掉只记日志——登记侧已经不再引用它。
    """
    versions = VersionManager(project_dir)
    for derivative_name in derivative_names:
        artifact_id = derivative_artifact_id(owner_name, derivative_name)
        forget_current_resource_artifact(
            project_dir,
            resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE,
            resource_id=artifact_id,
        )
        versions.purge_resource(CHARACTER_DERIVATIVE_RESOURCE_TYPE, artifact_id)
        _unlink(project_dir / derivative_sheet_relative_path(owner_name, derivative_name))
    _remove_if_empty(project_dir / derivative_sheet_dir(owner_name))
    _remove_if_empty(project_dir / derivative_version_dir(owner_name))


def purge_owner_derivative_sheets(project_dir: Path, owner_name: str, entry: Mapping[str, Any] | None) -> None:
    """删除本体时清掉它名下全部衍生的图、版本与清单条目。"""
    purge_derivative_sheets(project_dir, owner_name, derivative_table(entry))


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("衍生资产图删除失败，已解除登记", exc_info=True)


def _remove_if_empty(directory: Path) -> None:
    # 非空、不存在或无权限都不是错误：目录只是收纳，留着不影响任何登记。
    with contextlib.suppress(OSError):
        directory.rmdir()
