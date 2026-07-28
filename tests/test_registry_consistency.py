"""Registry consistency — 资源注册表派生自单一真相源。

`lib.resource_paths` 是「资源类型 → 路径/扩展名」的唯一真相源；VersionManager 的
`RESOURCE_TYPES` / `EXTENSIONS` 均从它派生。本测试断言派生未脱钩——若有人把
VersionManager 改回手写副本、与真相源漂移，立刻红。
"""

from __future__ import annotations

import pytest

from lib.resource_paths import RESOURCE_TYPES, resource_extension
from lib.version_manager import VersionManager


@pytest.mark.unit
def test_version_manager_derives_from_resource_paths() -> None:
    assert set(VersionManager.RESOURCE_TYPES) == set(RESOURCE_TYPES)
    assert VersionManager.EXTENSIONS == {rt: resource_extension(rt) for rt in RESOURCE_TYPES}
