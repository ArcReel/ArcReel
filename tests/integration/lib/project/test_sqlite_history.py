import json
import signal

import pytest

from lib.artifacts.version_manager import VersionManager


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
