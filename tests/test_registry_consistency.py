"""Registry consistency — 三张资源注册表的 keys 必须互相一致。

断言 MediaGenerator.OUTPUT_PATTERNS / VersionManager.RESOURCE_TYPES /
VersionManager.EXTENSIONS 三者的 keys 集合相等。

不预置 canonical 白名单：任何未来新增的资源类型只要三处都登记，测试自动通过；
只登记到 1~2 处（漏登记）会立刻红。这样新增资源类型不需要修改本测试。
"""

from __future__ import annotations

import pytest

from lib.media_generator import MediaGenerator
from lib.version_manager import VersionManager


@pytest.mark.unit
def test_resource_registries_have_consistent_keys() -> None:
    patterns_keys = set(MediaGenerator.OUTPUT_PATTERNS.keys())
    types_keys = set(VersionManager.RESOURCE_TYPES)
    ext_keys = set(VersionManager.EXTENSIONS.keys())

    assert patterns_keys == types_keys == ext_keys, (
        "资源注册表 keys 漂移：\n"
        f"  MediaGenerator.OUTPUT_PATTERNS keys = {sorted(patterns_keys)}\n"
        f"  VersionManager.RESOURCE_TYPES     = {sorted(types_keys)}\n"
        f"  VersionManager.EXTENSIONS keys    = {sorted(ext_keys)}\n"
        "新增资源类型时三处都要同步登记。"
    )
