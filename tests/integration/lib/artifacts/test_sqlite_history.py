import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from lib.artifacts.formal_write import formal_write_transaction, project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.infra.project_write_journal import track_write
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from tests.version_history_contract_cases import VersionHistoryContractCases


class TestSqliteHistory(VersionHistoryContractCases):
    sqlite = True


def test_compensation_after_another_writer_selected(tmp_path):
    manager = VersionManager(tmp_path)
    manager.enable_sqlite_history()
    current = tmp_path / "current.mp4"
    selected = Event()
    replacement = Event()

    def commit(prompt):
        staged = tmp_path / f"{prompt}.mp4"
        staged.write_bytes(prompt.encode())
        return (
            VersionManager(tmp_path)
            .commit_staged_paid_version(
                "videos", "E1S01", prompt, staged_file=staged, current_file=current, select_current=True
            )
            .version
        )

    def later_failure():
        version = commit("first")
        selected.set()
        assert replacement.wait(10)
        return manager.reject_current_version("videos", "E1S01", rejected_version=version, current_file=current)

    with ThreadPoolExecutor() as pool:
        compensation = pool.submit(later_failure)
        try:
            assert selected.wait(10)
            second = commit("second")
        finally:
            replacement.set()
        assert not compensation.result()
    assert manager.get_current_version("videos", "E1S01") == second
    assert current.read_bytes() == b"second"


def test_append_never_opens_legacy_history(tmp_path):
    manager = VersionManager(tmp_path)
    manager.enable_sqlite_history()
    manager.versions_file.write_text("retired legacy history", encoding="utf-8")
    target = manager.versions_file.resolve()
    opened: list[Path] = []
    active = [True]

    def audit(event: str, args: tuple[object, ...]) -> None:
        if not active[0] or event != "open" or not args:
            return
        candidate = args[0]
        if isinstance(candidate, (str, os.PathLike)) and Path(candidate).resolve(strict=False) == target:
            opened.append(Path(candidate))

    sys.addaudithook(audit)
    try:
        assert manager.add_version("characters", "Alice", "one") == 1
    finally:
        active[0] = False
    assert opened == []
    assert manager.versions_file.read_text(encoding="utf-8") == "retired legacy history"


def test_sqlite_reads_succeed_without_project_metadata_lock(tmp_path):
    projects = ProjectManager(tmp_path)
    projects.create_project("demo")
    project = projects.get_project_path("demo")
    manager = VersionManager(project)
    manager.enable_sqlite_history()
    source = project / "sheet.png"
    source.write_bytes(b"alice-v1")
    assert manager.add_version("characters", "Alice", "v1", source) == 1

    entered = Event()
    release = Event()

    def hold_metadata_lock() -> None:
        with project_metadata_lock(project):
            track_write(source)
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_metadata_lock)
        try:
            assert entered.wait(10)
            reader = VersionManager(projects.get_project_path("demo"))
            assert reader.get_versions("characters", "Alice")["current_version"] == 1
            current = reader.get_current_record("characters", "Alice")
            assert current is not None
            assert current["prompt"] == "v1"
            assert list(reader.sqlite.resource_keys("characters")) == [("characters", "Alice")]
            assert isinstance(reader.sqlite.revision(), bytes)
        finally:
            release.set()
        holder.result(timeout=10)


def test_nested_formal_write_transaction_rolls_back_sqlite_mutations(tmp_path):
    manager = VersionManager(tmp_path)
    manager.enable_sqlite_history()
    source = tmp_path / "sheet.png"
    source.write_bytes(b"alice-v1")
    assert manager.add_version("characters", "Alice", "v1", source) == 1

    def detach_and_fail() -> None:
        with formal_write_transaction(manager.versions_file):
            manager.detach_resources([("characters", "Alice")])
            assert manager.get_versions("characters", "Alice")["versions"] == []
            raise RuntimeError("boom")

    with project_metadata_lock(tmp_path):
        with pytest.raises(RuntimeError, match="boom"):
            detach_and_fail()

        restored = manager.get_versions("characters", "Alice")
        assert restored["current_version"] == 1
        assert [row["prompt"] for row in restored["versions"]] == ["v1"]


def test_nested_version_commit_and_history_transaction_roll_back_sqlite_when_caught(tmp_path):
    manager = VersionManager(tmp_path)
    manager.enable_sqlite_history()
    current = tmp_path / "characters" / "Alice.png"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"v1")
    assert manager.add_version("characters", "Alice", "v1", current) == 1

    staged = tmp_path / "staged.png"
    staged.write_bytes(b"v2")

    with project_metadata_lock(tmp_path):
        with pytest.raises(RuntimeError, match="commit failed"):
            manager.commit_staged_version(
                "characters",
                "Alice",
                "v2",
                staged_file=staged,
                current_file=current,
                on_commit=lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
            )

        after_commit_fail = manager.get_versions("characters", "Alice")
        assert after_commit_fail["current_version"] == 1
        assert [row["prompt"] for row in after_commit_fail["versions"]] == ["v1"]
        assert current.read_bytes() == b"v1"

        extra = tmp_path / "extra.png"
        extra.write_bytes(b"restored-history")
        created: list[str] = []

        def add_and_fail() -> None:
            with manager.history_transaction():
                rel = manager.add_history_version("characters", "Alice", extra, current_file=current, prompt="old")
                created.append(rel)
                assert (tmp_path / rel).is_file()
                raise RuntimeError("history failed")

        with pytest.raises(RuntimeError, match="history failed"):
            add_and_fail()

        assert not (tmp_path / created[0]).exists()
        after_history_fail = manager.get_versions("characters", "Alice")
        assert after_history_fail["current_version"] == 1
        assert [row["prompt"] for row in after_history_fail["versions"]] == ["v1"]


def test_keep_for_download_rollback_restores_snapshot_and_removed_files(tmp_path):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    root = pm.get_project_path("demo")
    VersionManager(root).enable_sqlite_history()
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/episode_1.json").write_text('{"episode":1}', encoding="utf-8")
    media = root / "characters/Alice.png"
    media.parent.mkdir(exist_ok=True)
    media.write_bytes(b"sheet")

    with project_metadata_lock(root):
        record = project_trash.record_deletion(
            root,
            {"kind": "asset", "name": "Alice"},
            ["scripts/episode_1.json", "characters/Alice.png"],
            snapshot=True,
        )

    def keep_and_abort() -> None:
        with project_metadata_lock(root):
            project_trash.keep_for_download(root, record.id, ["scripts/episode_1.json"])
            raise RuntimeError("abort")

    with pytest.raises(RuntimeError, match="abort"):
        keep_and_abort()

    restored = project_trash.require_record(root, record.id)
    assert restored.has_snapshot is True
    assert restored.download_only is False
    assert set(restored.files) == {"scripts/episode_1.json", "characters/Alice.png"}
    record_dir = project_trash.trash_dir(root) / record.id
    assert (record_dir / project_trash.SNAPSHOT_DIRNAME / "project.json").is_file()
    assert (record_dir / project_trash.FILES_DIRNAME / "characters/Alice.png").read_bytes() == b"sheet"
