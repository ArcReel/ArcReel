from pathlib import Path

import pytest

from lib.version_manager import VersionManager, _get_versions_file_lock


class TestVersionManagerMore:
    def test_lock_is_reused_for_same_file(self, tmp_path):
        file_a = tmp_path / "a" / "versions.json"
        file_a.parent.mkdir(parents=True)
        lock1 = _get_versions_file_lock(file_a)
        lock2 = _get_versions_file_lock(file_a)
        assert lock1 is lock2

    def test_get_versions_invalid_type_and_helpers(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)

        with pytest.raises(ValueError):
            vm.get_versions("bad", "x")

        assert vm.get_current_version("characters", "Alice") == 0
        assert vm.get_version_file_url("characters", "Alice", 1) is None
        assert vm.get_version_prompt("characters", "Alice", 1) is None
        assert vm.has_versions("characters", "Alice") is False

    def test_add_backup_restore_paths(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)

        current = project / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"png-v1")

        assert vm.backup_current("characters", "Alice", current, "p1") == 1
        assert vm.ensure_current_tracked("characters", "Alice", current, "p2") is None

        # create v2
        current.write_bytes(b"png-v2")
        assert vm.add_version("characters", "Alice", "p2", source_file=current) == 2

        info = vm.get_versions("characters", "Alice")
        assert info["current_version"] == 2
        assert len(info["versions"]) == 2
        assert vm.get_version_file_url("characters", "Alice", 2)
        assert vm.get_version_prompt("characters", "Alice", 2) == "p2"
        assert vm.has_versions("characters", "Alice")

        restored = vm.restore_version("characters", "Alice", 1, current)
        assert restored["restored_version"] == 1
        assert restored["new_current_version"] == 3

    def test_restore_errors_and_missing_current(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "characters" / "Alice.png"

        assert vm.backup_current("characters", "Alice", current, "p") is None
        assert vm.ensure_current_tracked("characters", "Alice", current, "p") is None

        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"png")
        with pytest.raises(ValueError):
            vm.ensure_current_tracked("bad", "Alice", current, "p")

        with pytest.raises(ValueError):
            vm.restore_version("characters", "missing", 1, current)

        # create record and delete version file to hit FileNotFoundError branch
        vm.add_version("characters", "Alice", "p", source_file=current)
        version_file = project / vm.get_versions("characters", "Alice")["versions"][0]["file"]
        version_file.unlink()

        with pytest.raises(FileNotFoundError):
            vm.restore_version("characters", "Alice", 1, current)

        with pytest.raises(ValueError):
            vm.restore_version("characters", "Alice", 99, current)
