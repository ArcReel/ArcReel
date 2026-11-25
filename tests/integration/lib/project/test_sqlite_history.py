import json
import signal
from pathlib import Path

import pytest

from lib.artifacts.version_manager import VersionManager


def test_sqlite_allocation_and_resource_identity(tmp_path):
    manager = VersionManager(tmp_path)
    manager.enable_sqlite_history()
    source = tmp_path / "picture.png"
    source.write_bytes(b"image")
    for key in ("Café", "Cafe\u0301"):
        assert manager.add_version("characters", key, "", source) == 1
    assert manager.add_version("characters", "Café", "", source) == 2
    assert VersionManager(tmp_path).get_current_version("characters", "Cafe\u0301") == 1
    assert not manager.versions_file.exists()


def _interrupt_paid_commit(root, boundary):
    import os
    import signal
    import sys

    from lib.infra.json_io import atomic_write_json

    def crash():
        os.kill(os.getpid(), signal.SIGKILL)

    staged = root / "staged.mp4"

    def before_replace(event, args):
        if event == "os.rename" and args[0] == str(staged):
            crash()

    def before_cleanup(event, args):
        if event in {"os.listdir", "os.scandir"} and args[0] == str(root / "versions/.history-undo"):
            crash()

    def select():
        if boundary == "after_history":
            crash()
        if boundary == "after_staging":
            sys.addaudithook(before_replace)
        return True

    def register():
        atomic_write_json(root / ".arcreel_artifacts.json", {"selected": 2})
        if boundary == "before_commit":
            crash()
        if boundary == "after_commit":
            sys.addaudithook(before_cleanup)

    VersionManager(root).commit_staged_paid_version(
        "videos",
        "E1S01",
        "paid",
        staged_file=staged,
        current_file=root / "current.mp4",
        select_current=select,
        on_select=register,
    )


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="requires SIGKILL")
@pytest.mark.parametrize("boundary", ["after_history", "after_staging", "before_commit", "after_commit"])
def test_recover_interrupted_paid_selection(tmp_path, boundary):
    import multiprocessing
    import signal

    from lib.project.project_manager import ProjectManager

    projects = ProjectManager(tmp_path)
    projects.create_project("demo")
    root = projects.get_project_path("demo")
    manager = VersionManager(root)
    manager.enable_sqlite_history()
    current = root / "current.mp4"
    current.write_bytes(b"original")
    manager.add_version("videos", "E1S01", "old", current)
    (root / "staged.mp4").write_bytes(b"paid")
    manifest = root / ".arcreel_artifacts.json"
    manifest.write_text('{"selected":1}', encoding="utf-8")
    child = multiprocessing.get_context("spawn").Process(target=_interrupt_paid_commit, args=(root, boundary))
    child.start()
    child.join(30)
    try:
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill()
            child.join()
    assert (root / "versions/.history-undo").is_dir()
    reopened = projects.get_project_path("demo")
    assert not (root / "versions/.history-undo").exists()
    committed = boundary == "after_commit"
    assert not (root / "staged.mp4").exists()
    assert not list(root.rglob("*.rollback"))
    assert current.read_bytes() == (b"paid" if committed else b"original")
    assert json.loads(manifest.read_bytes())["selected"] == (2 if committed else 1)
    history = VersionManager(reopened).get_versions("videos", "E1S01")
    assert history["current_version"] == (2 if committed else 1)
    assert [r["prompt"] for r in history["versions"]] == ["old", "paid"]
    assert (root / history["versions"][1]["file"]).read_bytes() == b"paid"


def test_compensation_after_another_writer_selected(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

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


def test_append_never_opens_legacy_history(tmp_path, monkeypatch):
    import builtins
    import io
    import os

    manager = VersionManager(tmp_path)
    manager.enable_sqlite_history()
    manager.versions_file.write_text("retired legacy history", encoding="utf-8")

    def guard(opener):
        def open_file(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and Path(file) == manager.versions_file:
                pytest.fail("append opened the retired JSON history")
            return opener(file, *args, **kwargs)

        return open_file

    with monkeypatch.context() as patches:
        patches.setattr(builtins, "open", guard(builtins.open))
        patches.setattr(io, "open", guard(io.open))
        assert manager.add_version("characters", "Alice", "one") == 1
    assert manager.versions_file.read_text(encoding="utf-8") == "retired legacy history"
