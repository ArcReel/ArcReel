"""项目回收站：文件视图只能删除未引用文件，删除成为一条记录；恢复从不覆盖现有文件；过期记录被清除。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.infra.data_root_layout import DataRootLayout
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from server.dependencies import require_project_migration_ok
from server.error_handlers import register_error_handlers
from server.routers import file_view
from server.routers import project_trash as trash_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

FILES = "/api/v1/projects/demo/file-view"
TRASH = "/api/v1/projects/demo/trash"


@pytest.fixture
def project_dir(tmp_path):
    root = DataRootLayout(tmp_path / "data").projects_dir / "demo"
    (root / "storyboards").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "project.json").write_text(
        json.dumps({"title": "demo", "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}]}),
        encoding="utf-8",
    )
    script = {
        "segments": [{"segment_id": "E1S01", "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"}}]
    }
    (root / "scripts/episode_1.json").write_text(json.dumps(script), encoding="utf-8")
    (root / "storyboards/scene_E1S01.png").write_bytes(b"in-use")
    (root / "storyboards/scene_E1S02.png").write_bytes(b"leftover")
    (root / "notes.txt").write_text("agent notes", encoding="utf-8")
    (root / "source").mkdir()
    (root / "source/extra.txt").write_text("not in the whole source", encoding="utf-8")
    return root


@pytest.fixture
def client(project_dir, monkeypatch):
    manager = ProjectManager(str(project_dir.parents[1]))
    monkeypatch.setattr(file_view, "get_project_manager", lambda: manager)
    monkeypatch.setattr(trash_router, "get_project_manager", lambda: manager)
    app = FastAPI()
    for module in (file_view, trash_router):
        app.include_router(
            module.router, prefix="/api/v1", dependencies=[*AUTH_DEPENDENCIES, Depends(require_project_migration_ok)]
        )
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


def _records(client) -> list[dict]:
    response = client.get(TRASH)
    assert response.status_code == 200, response.text
    return response.json()["records"]


def test_deleting_a_referenced_file_is_rejected(client, project_dir):
    # 未纳入整本源文的源文件同样拒绝：它交给分集视图处理
    for path in ("storyboards/scene_E1S01.png", "project.json", "scripts/episode_1.json", "source/extra.txt"):
        response = client.delete(FILES, params={"path": path})
        assert response.status_code == 409, path

    assert (project_dir / "storyboards/scene_E1S01.png").read_bytes() == b"in-use"
    assert _records(client) == []


def test_deleting_an_unreferenced_file_creates_one_record(client, project_dir):
    response = client.delete(FILES, params={"path": "storyboards/scene_E1S02.png"})
    assert response.status_code == 200, response.text

    assert not (project_dir / "storyboards/scene_E1S02.png").exists()
    [record] = _records(client)
    assert record["object"] == {"kind": "file", "path": "storyboards/scene_E1S02.png"}
    assert record["files"] == ["storyboards/scene_E1S02.png"]
    assert record["origin"] == "user"
    assert record["size"] == len(b"leftover")
    assert client.get(TRASH).json()["total_size"] == len(b"leftover")


def test_restore_returns_to_original_path_when_free(client, project_dir):
    client.delete(FILES, params={"path": "notes.txt"})
    [record] = _records(client)

    response = client.post(f"{TRASH}/{record['id']}/restore")

    assert response.status_code == 200, response.text
    assert response.json()["restored"] == [{"original": "notes.txt", "path": "notes.txt", "as_history": False}]
    assert (project_dir / "notes.txt").read_text(encoding="utf-8") == "agent notes"
    assert _records(client) == []


def test_restore_onto_an_occupied_resource_path_adds_a_history_version(client, project_dir):
    client.delete(FILES, params={"path": "storyboards/scene_E1S02.png"})
    (project_dir / "storyboards/scene_E1S02.png").write_bytes(b"regenerated")
    [record] = _records(client)

    [restored] = client.post(f"{TRASH}/{record['id']}/restore").json()["restored"]

    assert restored["as_history"] is True
    assert (project_dir / "storyboards/scene_E1S02.png").read_bytes() == b"regenerated"
    assert (project_dir / restored["path"]).read_bytes() == b"leftover"
    history = json.loads((project_dir / "versions/versions.json").read_text(encoding="utf-8"))["storyboards"]["E1S02"]
    current = next(v for v in history["versions"] if v["version"] == history["current_version"])
    assert (project_dir / current["file"]).read_bytes() == b"regenerated"


def test_restore_onto_an_occupied_plain_path_takes_a_new_name(client, project_dir):
    client.delete(FILES, params={"path": "notes.txt"})
    (project_dir / "notes.txt").write_text("newer", encoding="utf-8")
    [record] = _records(client)

    [restored] = client.post(f"{TRASH}/{record['id']}/restore").json()["restored"]

    assert restored["path"] == "notes (1).txt"
    assert (project_dir / "notes.txt").read_text(encoding="utf-8") == "newer"
    assert (project_dir / "notes (1).txt").read_text(encoding="utf-8") == "agent notes"


def test_purge_and_empty_remove_records(client):
    client.delete(FILES, params={"path": "notes.txt"})
    client.delete(FILES, params={"path": "storyboards/scene_E1S02.png"})
    first, second = _records(client)

    assert client.delete(f"{TRASH}/{first['id']}").status_code == 200
    assert [r["id"] for r in _records(client)] == [second["id"]]
    assert client.delete(TRASH).json() == {"purged": 1}
    assert _records(client) == []
    assert client.post(f"{TRASH}/{first['id']}/restore").status_code == 404


def test_records_are_kept_until_the_retention_period_ends(client, project_dir):
    client.delete(FILES, params={"path": "notes.txt"})
    [record] = project_trash.list_records(project_dir)
    expires_at = record.deleted_at + project_trash.RETENTION

    assert project_trash.purge_expired(project_dir, now=expires_at - timedelta(seconds=1)) == 0
    assert [r["id"] for r in _records(client)] == [record.id]
    assert project_trash.purge_expired(project_dir, now=expires_at) == 1
    assert _records(client) == []


def test_trash_is_hidden_from_the_file_view(client):
    client.delete(FILES, params={"path": "notes.txt"})

    names = {entry["name"] for entry in client.get(FILES, params={"path": ".arcreel"}).json()["entries"]}
    assert "trash" not in names
    assert client.get(FILES, params={"path": ".arcreel/trash"}).status_code == 404
