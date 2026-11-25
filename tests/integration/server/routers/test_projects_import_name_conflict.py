import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lib.project.project_manager import ProjectManager
from server.services.project.project_archive import ProjectArchiveService
from tests.integration.server.routers.projects_router_support import build_projects_client


@pytest.mark.parametrize("policy", ["prompt", "rename"])
def test_create_claims_name_during_archive_import(tmp_path, monkeypatch, file_db_factory, policy):
    source = ProjectManager(tmp_path / "source")
    source.create_project("demo")
    source.create_project_metadata("demo", "Archived", "Anime", "narration")
    chapter = "source/chapter.txt"
    (source.get_project_path("demo") / chapter).write_text("archived chapter")
    archive, _ = ProjectArchiveService(source).export_project("demo")
    manager = ProjectManager(tmp_path / "destination")
    ready, resume = threading.Event(), threading.Event()
    sync_profile = manager.sync_agent_profile

    def pause_prepared_import(path, **kwargs):
        profile = sync_profile(path, **kwargs)
        if path.name.startswith(".import-staging-"):
            ready.set()
            assert resume.wait(10)
        return profile

    monkeypatch.setattr(manager, "sync_agent_profile", pause_prepared_import)
    claimed, finish_create = threading.Event(), threading.Event()
    target = manager.projects_dir / "demo"
    mkdir = Path.mkdir

    def pause_empty_project(path, *args, **kwargs):
        if path.parent == target and not claimed.is_set():
            claimed.set()
            assert finish_create.wait(10)
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", pause_empty_project)
    with (
        build_projects_client(monkeypatch, manager, session_factory=file_db_factory) as client,
        ThreadPoolExecutor(2) as pool,
    ):
        importing = pool.submit(
            client.post,
            "/api/v1/projects/import",
            files={"file": ("demo.zip", archive.read_bytes(), "application/zip")},
            data={"conflict_policy": policy},
        )
        try:
            assert ready.wait(10)
            creating = pool.submit(
                client.post, "/api/v1/projects", json={"name": "demo", "title": "New", "generation_mode": "storyboard"}
            )
            assert claimed.wait(10)
            resume.set()
            response = importing.result(timeout=10)
        finally:
            resume.set()
            finish_create.set()
        created = creating.result(timeout=10)
        assert created.status_code == 200, created.text

    assert manager.load_project("demo")["title"] == "New"
    assert not (manager.get_project_path("demo") / chapter).exists()
    if policy == "prompt":
        assert response.status_code == 409, response.text
        assert response.json()["conflict_project_name"] == "demo"
    else:
        assert response.status_code == 200, response.text
        assert response.json()["conflict_resolution"] == "renamed"
        imported = response.json()["project_name"]
        assert imported != "demo"
        assert manager.load_project(imported)["title"] == "Archived"
        assert (manager.get_project_path(imported) / chapter).read_text() == "archived chapter"
