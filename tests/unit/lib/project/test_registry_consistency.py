"""Registry consistency: derive resource registries from a single source of truth.

`lib.project.resource_paths` alone maps resource types to paths/extensions; VersionManager
`RESOURCE_TYPES` / `EXTENSIONS` derive from it. Assert that derivation remains connected: if someone
restores handwritten VersionManager copies that drift from the source, fail immediately.
"""

from __future__ import annotations

from lib.artifacts.version_manager import VersionManager
from lib.project.resource_paths import RESOURCE_TYPES, resource_extension


def test_version_manager_derives_from_resource_paths() -> None:
    assert set(VersionManager.RESOURCE_TYPES) == set(RESOURCE_TYPES)
    assert {rt: resource_extension(rt) for rt in RESOURCE_TYPES} == VersionManager.EXTENSIONS
