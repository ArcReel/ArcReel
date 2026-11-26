import json

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.project.project_manager import ProjectManager


class VersionHistoryContractCases:
    sqlite: bool

    @pytest.mark.parametrize(
        ("kind", "key"), [("character_derivatives", "Hero/Armor"), ("characters", "Café"), ("characters", "Cafe\u0301")]
    )
    def test_version_allocation_before(self, tmp_path, kind, key):
        pm = ProjectManager(tmp_path)
        pm.create_project("demo")
        path = pm.get_project_path("demo")
        manager = VersionManager(path)
        if self.sqlite:
            manager.enable_sqlite_history()
        assert manager.get_versions(kind, key) == {"versions": [], "current_version": 0}
        current = path / "current.png"
        current.write_bytes(b"image")
        assert manager.add_version(kind, key, "one", current) == 1
        assert manager.add_version(kind, key, "two", current) == 2
        manager.restore_version(kind, key, 1, current)
        second = manager.get_versions(kind, key)["versions"][1]
        assert key in second["file"]
        manager.delete_history_version(second["file"])
        assert manager.add_version(kind, key, "replacement", current) == 2

    def test_legacy_missing_selection(self, tmp_path):
        manager = VersionManager(tmp_path)
        if self.sqlite:
            manager.enable_sqlite_history({"characters": {"Hero": {"versions": []}}})
        else:
            manager.versions_file.parent.mkdir(parents=True)
            manager.versions_file.write_text('{"characters":{"Hero":{"versions":[]}}}', encoding="utf-8")
        assert manager.get_versions("characters", "Hero") == {"versions": [], "current_version": 0}

    def test_append_with_legacy_missing_pointer(self, tmp_path):
        manager = VersionManager(tmp_path)
        history = {
            "characters": {"Hero": {"versions": [{"version": 7, "file": "versions/characters/Hero_v7_old.png"}]}}
        }
        if self.sqlite:
            manager.enable_sqlite_history(history)
        else:
            manager.versions_file.parent.mkdir(parents=True)
            manager.versions_file.write_text(json.dumps(history), encoding="utf-8")
        current = tmp_path / "current.png"
        current.write_bytes(b"image")
        assert manager.add_version("characters", "Hero", "", current) == 8
        assert manager.get_current_version("characters", "Hero") == 8

    def test_unicode_resource_ids_remain_distinct(self, tmp_path):
        manager = VersionManager(tmp_path)
        if self.sqlite:
            manager.enable_sqlite_history()
        current = tmp_path / "current.png"
        current.write_bytes(b"image")
        for name in ("Café", "Cafe\u0301"):
            assert manager.add_version("characters", name, "", current) == 1
        assert manager.add_version("characters", "Café", "", current) == 2
        assert manager.get_current_version("characters", "Cafe\u0301") == 1
        if self.sqlite:
            assert not manager.versions_file.exists()
