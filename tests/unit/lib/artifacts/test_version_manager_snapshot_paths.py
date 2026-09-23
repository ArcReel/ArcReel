import json
from pathlib import Path

import pytest

from lib.artifacts.version_manager import UnmanagedSnapshotPathError, VersionManager
from lib.infra.api_errors import BadRequestError

_UNMANAGED_FILES = [
    "../outside/Hero_v1_20260101T000000.png",
    "versions/characters/../../../outside/Hero_v1_20260101T000000.png",
    "versions\\characters\\Hero_v1_20260101T000000.png",
    "versions/scenes/Hero_v1_20260101T000000.png",
    "versions/characters/Hero_v1_20260101T000000.txt",
    "project.json",
    "versions/characters/Hero\x00_v1_20260101T000000.png",
]


def _write_history(project: Path, resource_type: str, resource_id: str, records: list[dict], current: int) -> bytes:
    versions_file = project / "versions" / "versions.json"
    versions_file.parent.mkdir(parents=True, exist_ok=True)
    versions_file.write_text(
        json.dumps({resource_type: {resource_id: {"current_version": current, "versions": records}}}),
        encoding="utf-8",
    )
    return versions_file.read_bytes()


def _sentinel(tmp_path: Path) -> Path:
    sentinel = tmp_path / "outside" / "Hero_v1_20260101T000000.png"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b"sentinel")
    return sentinel


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    (project / "characters").mkdir(parents=True)
    (project / "project.json").write_text("{}", encoding="utf-8")
    return project


class TestResolveSnapshotPath:
    def test_resolves_a_managed_snapshot_inside_the_project(self, tmp_path):
        project = _project(tmp_path)
        rel = "versions/characters/Hero_v1_20260101T000000.png"

        resolved = VersionManager.resolve_snapshot_path(project, "characters", rel)

        assert resolved == project / rel

    @pytest.mark.parametrize("relative", [*_UNMANAGED_FILES, str(Path("/tmp/Hero_v1.png")), None, 3])
    def test_rejects_paths_outside_the_typed_history_bucket(self, tmp_path, relative):
        project = _project(tmp_path)

        with pytest.raises(UnmanagedSnapshotPathError) as exc_info:
            VersionManager.resolve_snapshot_path(project, "characters", relative)

        assert isinstance(exc_info.value, BadRequestError)
        assert exc_info.value.status_code == 400

    def test_rejects_a_bucket_symlink_that_leaves_the_project(self, tmp_path):
        project = _project(tmp_path)
        sentinel = _sentinel(tmp_path)
        (project / "versions").mkdir()
        (project / "versions" / "characters").symlink_to(sentinel.parent, target_is_directory=True)

        with pytest.raises(UnmanagedSnapshotPathError):
            VersionManager.resolve_snapshot_path(project, "characters", f"versions/characters/{sentinel.name}")

    def test_rejects_unknown_resource_types(self, tmp_path):
        with pytest.raises(UnmanagedSnapshotPathError):
            VersionManager.resolve_snapshot_path(_project(tmp_path), "clues", "versions/clues/Key_v1_x.png")


class TestSnapshotSinksRejectUnmanagedPaths:
    @pytest.mark.parametrize("snapshot", _UNMANAGED_FILES)
    def test_restore_rejects_unmanaged_snapshot_path(self, tmp_path, snapshot):
        project = _project(tmp_path)
        sentinel = _sentinel(tmp_path)
        current = project / "characters" / "Hero.png"
        current.write_bytes(b"current")
        before = _write_history(project, "characters", "Hero", [{"version": 1, "file": snapshot}], current=1)

        with pytest.raises(UnmanagedSnapshotPathError):
            VersionManager(project).restore_version("characters", "Hero", 1, current)

        assert current.read_bytes() == b"current"
        assert sentinel.read_bytes() == b"sentinel"
        assert (project / "versions" / "versions.json").read_bytes() == before

    @pytest.mark.parametrize("snapshot", _UNMANAGED_FILES)
    def test_reject_current_version_rejects_unmanaged_snapshot_path(self, tmp_path, snapshot):
        project = _project(tmp_path)
        sentinel = _sentinel(tmp_path)
        current = project / "characters" / "Hero.png"
        current.write_bytes(b"current")
        before = _write_history(
            project,
            "characters",
            "Hero",
            [
                {"version": 1, "file": snapshot},
                {"version": 2, "file": "versions/characters/Hero_v2_20260101T000000.png"},
            ],
            current=2,
        )

        with pytest.raises(UnmanagedSnapshotPathError):
            VersionManager(project).reject_current_version(
                "characters", "Hero", rejected_version=2, current_file=current, restore_version=1
            )

        assert current.read_bytes() == b"current"
        assert sentinel.read_bytes() == b"sentinel"
        assert (project / "versions" / "versions.json").read_bytes() == before
        assert sorted(path.name for path in (project / "characters").iterdir()) == ["Hero.png"]

    @pytest.mark.parametrize("snapshot", _UNMANAGED_FILES)
    def test_purge_rejects_unmanaged_snapshot_path(self, tmp_path, snapshot):
        project = _project(tmp_path)
        sentinel = _sentinel(tmp_path)
        managed = project / "versions" / "characters" / "Hero_v2_20260101T000000.png"
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_bytes(b"v2")
        before = _write_history(
            project,
            "characters",
            "Hero",
            [{"version": 1, "file": snapshot}, {"version": 2, "file": managed.relative_to(project).as_posix()}],
            current=2,
        )

        with pytest.raises(UnmanagedSnapshotPathError):
            VersionManager(project).purge_resource("characters", "Hero")

        assert sentinel.read_bytes() == b"sentinel"
        assert (project / "project.json").is_file()
        assert managed.read_bytes() == b"v2"
        assert (project / "versions" / "versions.json").read_bytes() == before

    @pytest.mark.parametrize("dry_run", [True, False])
    @pytest.mark.parametrize("snapshot", _UNMANAGED_FILES)
    def test_rename_rejects_unmanaged_snapshot_path(self, tmp_path, snapshot, dry_run):
        project = _project(tmp_path)
        sentinel = _sentinel(tmp_path)
        before = _write_history(project, "characters", "Hero", [{"version": 1, "file": snapshot}], current=1)

        with pytest.raises(UnmanagedSnapshotPathError):
            VersionManager(project).rename_resource("characters", "Hero", "Villain", dry_run=dry_run)

        assert sentinel.read_bytes() == b"sentinel"
        assert (project / "project.json").is_file()
        assert (project / "versions" / "versions.json").read_bytes() == before
        assert not (project / "versions" / "characters" / "Villain_v1_20260101T000000.png").exists()


class TestSnapshotSinksKeepManagedBehavior:
    def test_purge_removes_managed_snapshots_and_the_record(self, tmp_path):
        project = _project(tmp_path)
        vm = VersionManager(project)
        current = project / "characters" / "Hero.png"
        current.write_bytes(b"v1")
        vm.add_version("characters", "Hero", "p", source_file=current)
        snapshot = project / vm.get_versions("characters", "Hero")["versions"][0]["file"]

        assert vm.purge_resource("characters", "Hero") == 1

        assert not snapshot.exists()
        assert vm.get_versions("characters", "Hero") == {"current_version": 0, "versions": []}

    def test_rename_moves_managed_snapshots_to_the_new_id(self, tmp_path):
        project = _project(tmp_path)
        vm = VersionManager(project)
        current = project / "characters" / "Hero.png"
        current.write_bytes(b"v1")
        vm.add_version("characters", "Hero", "p", source_file=current)
        old_rel = vm.get_versions("characters", "Hero")["versions"][0]["file"]

        assert vm.rename_resource("characters", "Hero", "Villain") == 1

        new_rel = vm.get_versions("characters", "Villain")["versions"][0]["file"]
        assert new_rel == old_rel.replace("/Hero_v", "/Villain_v")
        assert (project / new_rel).read_bytes() == b"v1"
        assert not (project / old_rel).exists()

    def test_reject_current_version_restores_a_managed_previous_snapshot(self, tmp_path):
        project = _project(tmp_path)
        vm = VersionManager(project)
        current = project / "characters" / "Hero.png"
        current.write_bytes(b"v1")
        vm.add_version("characters", "Hero", "p", source_file=current)
        current.write_bytes(b"v2")
        vm.add_version("characters", "Hero", "p", source_file=current)

        assert vm.reject_current_version("characters", "Hero", rejected_version=2, current_file=current)

        assert current.read_bytes() == b"v1"
        assert vm.get_current_version("characters", "Hero") == 1


class TestSnapshotSinksActOnTheRecordedPath:
    @staticmethod
    def _linked_snapshot(project: Path) -> tuple[VersionManager, Path]:
        rel = "versions/characters/Hero_v1_20260101T000000.png"
        link = project / rel
        link.parent.mkdir(parents=True)
        link.symlink_to(project / "project.json")
        _write_history(project, "characters", "Hero", [{"version": 1, "file": rel}], current=1)
        return VersionManager(project), link

    def test_purge_removes_a_symlinked_snapshot_without_touching_its_target(self, tmp_path):
        project = _project(tmp_path)
        vm, link = self._linked_snapshot(project)

        assert vm.purge_resource("characters", "Hero") == 1

        assert not link.is_symlink()
        assert (project / "project.json").read_text(encoding="utf-8") == "{}"

    def test_rename_moves_a_symlinked_snapshot_without_moving_its_target(self, tmp_path):
        project = _project(tmp_path)
        vm, link = self._linked_snapshot(project)

        assert vm.rename_resource("characters", "Hero", "Villain") == 1

        renamed = project / vm.get_versions("characters", "Villain")["versions"][0]["file"]
        assert renamed.is_symlink()
        assert not link.is_symlink()
        assert (project / "project.json").read_text(encoding="utf-8") == "{}"


class TestSnapshotSinksRejectUnknownResourceTypes:
    def test_restore_rejects_unknown_resource_type(self, tmp_path):
        project = _project(tmp_path)
        _write_history(project, "clues", "Key", [{"version": 1, "file": "versions/clues/Key_v1_x.png"}], current=1)

        with pytest.raises(BadRequestError):
            VersionManager(project).restore_version("clues", "Key", 1, project / "props" / "Key.png")

    @pytest.mark.parametrize(
        "operation",
        [
            lambda vm, project: vm.purge_resource("clues", "Key"),
            lambda vm, project: vm.rename_resource("clues", "Key", "Lock"),
            lambda vm, project: vm.reject_current_version(
                "clues", "Key", rejected_version=1, current_file=project / "props" / "Key.png"
            ),
        ],
        ids=["purge", "rename", "reject"],
    )
    def test_mutating_sinks_reject_unknown_resource_type(self, tmp_path, operation):
        project = _project(tmp_path)
        before = _write_history(
            project, "clues", "Key", [{"version": 1, "file": "versions/clues/Key_v1_x.png"}], current=1
        )

        with pytest.raises(ValueError, match="clues"):
            operation(VersionManager(project), project)

        assert (project / "versions" / "versions.json").read_bytes() == before
