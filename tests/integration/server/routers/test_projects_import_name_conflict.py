import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import portalocker
import pytest

from lib.project.project_manager import ProjectManager
from server.services.project.project_archive import ProjectArchiveService
from tests.integration.server.routers.projects_router_support import build_projects_client

CHAPTER_PATH = "source/chapter.txt"


def _export_demo_archive(root: Path) -> bytes:
    source = ProjectManager(root / "source")
    source.create_project("demo")
    source.create_project_metadata("demo", "Archived", "Anime", "narration")
    (source.get_project_path("demo") / CHAPTER_PATH).write_text("archived chapter", encoding="utf-8")
    archive, _ = ProjectArchiveService(source).export_project("demo")
    return archive.read_bytes()


def _post_import(client, archive_bytes: bytes, *, policy: str):
    return client.post(
        "/api/v1/projects/import",
        files={"file": ("demo.zip", archive_bytes, "application/zip")},
        data={"conflict_policy": policy},
    )


def _post_create_demo(client):
    return client.post(
        "/api/v1/projects",
        json={"name": "demo", "title": "New", "generation_mode": "storyboard"},
    )


@pytest.mark.parametrize("policy", ["prompt", "rename", "overwrite"])
def test_create_claims_name_during_archive_import(tmp_path, monkeypatch, file_db_factory, policy):
    archive_bytes = _export_demo_archive(tmp_path)
    manager = ProjectManager(tmp_path / "destination")
    lock_path = manager.layout.project_names_lock_path
    target = manager.projects_dir / "demo"

    import_ready, resume_import = threading.Event(), threading.Event()
    claimed, finish_create = threading.Event(), threading.Event()
    real_enter = portalocker.Lock.__enter__
    real_mkdir = Path.mkdir

    def pause_import_before_name_lock(self):
        if Path(self.filename) == lock_path and not import_ready.is_set():
            import_ready.set()
            assert resume_import.wait(10)
        return real_enter(self)

    def pause_empty_project(path, *args, **kwargs):
        if path.parent == target and not claimed.is_set():
            claimed.set()
            assert finish_create.wait(10)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(portalocker.Lock, "__enter__", pause_import_before_name_lock)
    monkeypatch.setattr(Path, "mkdir", pause_empty_project)

    with (
        build_projects_client(monkeypatch, manager, session_factory=file_db_factory) as client,
        ThreadPoolExecutor(2) as pool,
    ):
        importing = pool.submit(_post_import, client, archive_bytes, policy=policy)
        try:
            assert import_ready.wait(10)
            creating = pool.submit(_post_create_demo, client)
            assert claimed.wait(10)
            with (
                pytest.raises(portalocker.exceptions.LockException),
                portalocker.Lock(lock_path, flags=portalocker.LOCK_EX | portalocker.LOCK_NB, timeout=0),
            ):
                pass
            resume_import.set()
            finish_create.set()
            response = importing.result(timeout=10)
        finally:
            resume_import.set()
            finish_create.set()
        created = creating.result(timeout=10)
        assert created.status_code == 200, created.text

    if policy == "overwrite":
        assert response.status_code == 200, response.text
        assert response.json()["conflict_resolution"] == "overwritten"
        assert manager.load_project("demo")["title"] == "Archived"
        assert (target / CHAPTER_PATH).read_text(encoding="utf-8") == "archived chapter"
        return

    assert manager.load_project("demo")["title"] == "New"
    assert not (manager.get_project_path("demo") / CHAPTER_PATH).exists()
    if policy == "prompt":
        assert response.status_code == 409, response.text
        assert response.json()["conflict_project_name"] == "demo"
    else:
        assert response.status_code == 200, response.text
        assert response.json()["conflict_resolution"] == "renamed"
        imported = response.json()["project_name"]
        assert imported != "demo"
        assert manager.load_project(imported)["title"] == "Archived"
        assert (manager.get_project_path(imported) / CHAPTER_PATH).read_text(encoding="utf-8") == "archived chapter"


@pytest.mark.parametrize("policy", ["prompt", "rename"])
def test_create_waits_for_archive_import_lock_before_rename(tmp_path, monkeypatch, file_db_factory, policy):
    archive_bytes = _export_demo_archive(tmp_path)
    manager = ProjectManager(tmp_path / "destination")
    lock_path = manager.layout.project_names_lock_path
    target = manager.projects_dir / "demo"

    before_rename, allow_rename = threading.Event(), threading.Event()
    create_waiting = threading.Event()
    real_rename = Path.rename
    real_enter = portalocker.Lock.__enter__

    def pause_before_install_rename(self, destination):
        if Path(destination) == target and not before_rename.is_set():
            before_rename.set()
            assert allow_rename.wait(10)
        return real_rename(self, destination)

    def observe_create_waiting_for_lock(self):
        if Path(self.filename) == lock_path and before_rename.is_set() and not (self.flags & portalocker.LOCK_NB):
            create_waiting.set()
        return real_enter(self)

    monkeypatch.setattr(Path, "rename", pause_before_install_rename)
    monkeypatch.setattr(portalocker.Lock, "__enter__", observe_create_waiting_for_lock)

    with (
        build_projects_client(monkeypatch, manager, session_factory=file_db_factory) as client,
        ThreadPoolExecutor(2) as pool,
    ):
        importing = pool.submit(_post_import, client, archive_bytes, policy=policy)
        try:
            assert before_rename.wait(10)
            with (
                pytest.raises(portalocker.exceptions.LockException),
                portalocker.Lock(lock_path, flags=portalocker.LOCK_EX | portalocker.LOCK_NB, timeout=0),
            ):
                pass

            creating = pool.submit(_post_create_demo, client)
            assert create_waiting.wait(10)
            assert not target.exists()
            assert not creating.done()
            allow_rename.set()
            response = importing.result(timeout=10)
        finally:
            allow_rename.set()
        created = creating.result(timeout=10)

    assert response.status_code == 200, response.text
    assert response.json()["project_name"] == "demo"
    assert response.json()["conflict_resolution"] == "none"
    assert created.status_code == 400, created.text
    assert created.json()["detail"] == "项目 'demo' 已存在"
    assert manager.load_project("demo")["title"] == "Archived"
    assert (manager.get_project_path("demo") / CHAPTER_PATH).read_text(encoding="utf-8") == "archived chapter"


def test_overwrite_import_holds_name_lock_across_replace_swap(tmp_path, monkeypatch, file_db_factory):
    archive_bytes = _export_demo_archive(tmp_path)
    manager = ProjectManager(tmp_path / "destination")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Old", "Anime", "narration")
    lock_path = manager.layout.project_names_lock_path
    target = manager.projects_dir / "demo"

    in_swap_gap, allow_install = threading.Event(), threading.Event()
    create_waiting = threading.Event()
    real_rename = Path.rename
    real_enter = portalocker.Lock.__enter__

    def pause_between_retire_and_install(self, destination):
        if Path(destination) == target and not in_swap_gap.is_set():
            in_swap_gap.set()
            assert allow_install.wait(10)
        return real_rename(self, destination)

    def observe_create_waiting_for_lock(self):
        if Path(self.filename) == lock_path and in_swap_gap.is_set() and not (self.flags & portalocker.LOCK_NB):
            create_waiting.set()
        return real_enter(self)

    monkeypatch.setattr(Path, "rename", pause_between_retire_and_install)
    monkeypatch.setattr(portalocker.Lock, "__enter__", observe_create_waiting_for_lock)

    with (
        build_projects_client(monkeypatch, manager, session_factory=file_db_factory) as client,
        ThreadPoolExecutor(2) as pool,
    ):
        importing = pool.submit(_post_import, client, archive_bytes, policy="overwrite")
        try:
            assert in_swap_gap.wait(10)
            assert not target.exists()
            with (
                pytest.raises(portalocker.exceptions.LockException),
                portalocker.Lock(lock_path, flags=portalocker.LOCK_EX | portalocker.LOCK_NB, timeout=0),
            ):
                pass

            creating = pool.submit(_post_create_demo, client)
            assert create_waiting.wait(10)
            assert not target.exists()
            assert not creating.done()
            allow_install.set()
            response = importing.result(timeout=10)
        finally:
            allow_install.set()
        created = creating.result(timeout=10)
        deleted = client.get("/api/v1/deleted-projects").json()["projects"]

    assert response.status_code == 200, response.text
    assert response.json()["project_name"] == "demo"
    assert response.json()["conflict_resolution"] == "overwritten"
    assert created.status_code == 400, created.text
    assert created.json()["detail"] == "项目 'demo' 已存在"
    assert manager.load_project("demo")["title"] == "Archived"
    assert (manager.get_project_path("demo") / CHAPTER_PATH).read_text(encoding="utf-8") == "archived chapter"
    assert len(deleted) == 1
    assert deleted[0]["title"] == "Old"


def test_overwrite_import_installs_cleanly_when_target_deleted_before_replace(tmp_path, monkeypatch, file_db_factory):
    archive_bytes = _export_demo_archive(tmp_path)
    manager = ProjectManager(tmp_path / "destination")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Old", "Anime", "narration")
    target = manager.projects_dir / "demo"

    resolved, deleted_done = threading.Event(), threading.Event()
    real_exists = Path.exists

    def pause_after_overwrite_resolution(self):
        exists = real_exists(self)
        if self == target and exists and not resolved.is_set():
            resolved.set()
            assert deleted_done.wait(10)
        return exists

    monkeypatch.setattr(Path, "exists", pause_after_overwrite_resolution)

    with (
        build_projects_client(monkeypatch, manager, session_factory=file_db_factory) as client,
        ThreadPoolExecutor(2) as pool,
    ):
        importing = pool.submit(_post_import, client, archive_bytes, policy="overwrite")
        try:
            assert resolved.wait(10)
            deleted_response = client.delete("/api/v1/projects/demo")
            assert deleted_response.status_code == 200, deleted_response.text
            deleted_done.set()
            response = importing.result(timeout=10)
        finally:
            deleted_done.set()
        deleted = client.get("/api/v1/deleted-projects").json()["projects"]

    assert response.status_code == 200, response.text
    assert response.json()["project_name"] == "demo"
    assert response.json()["conflict_resolution"] == "none"
    assert manager.load_project("demo")["title"] == "Archived"
    assert (manager.get_project_path("demo") / CHAPTER_PATH).read_text(encoding="utf-8") == "archived chapter"
    assert len(deleted) == 1
    assert deleted[0]["title"] == "Old"
