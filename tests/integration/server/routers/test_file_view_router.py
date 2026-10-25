"""文件视图接口：按目录懒加载、标角色、隐藏基础设施文件，鉴权预览与下载不跟随符号链接。"""

from __future__ import annotations

import json
import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from server.dependencies import require_project_migration_ok
from server.error_handlers import register_error_handlers
from server.routers import file_view
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

BASE = "/api/v1/projects/demo/file-view"


@pytest.fixture
def file_view_project_dir(tmp_path):
    root = DataRootLayout(tmp_path / "data").projects_dir / "demo"
    (root / "storyboards").mkdir(parents=True)
    (root / ".arcreel/memory").mkdir(parents=True)
    (root / "project.json").write_text(
        json.dumps({"title": "demo", "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}]}),
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    (root / "scripts/episode_1.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"segment_id": "E1S01", "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"}}
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "storyboards/scene_E1S01.png").write_bytes(b"x" * 10)
    (root / "storyboards/scene_E1S02.png").write_bytes(b"y" * 5)
    (root / "storyboards/.scene_E1S01.png.lock").write_bytes(b"")
    (root / ".project.json.lock").write_bytes(b"")
    (root / "project.json.bak.v3-1790943188").write_text("{}", encoding="utf-8")
    (root / ".arcreel/memory/MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("agent", encoding="utf-8")
    return root


@pytest.fixture
def file_view_client(file_view_project_dir, monkeypatch):
    data_root = file_view_project_dir.parents[1]
    monkeypatch.setattr(file_view, "get_project_manager", lambda: ProjectManager(str(data_root)))
    app = FastAPI()
    app.include_router(
        file_view.router, prefix="/api/v1", dependencies=[*AUTH_DEPENDENCIES, Depends(require_project_migration_ok)]
    )
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


def _entries(response) -> dict[str, dict]:
    assert response.status_code == 200, response.text
    return {entry["name"]: entry for entry in response.json()["entries"]}


def test_root_lists_one_level_and_hides_infrastructure(file_view_client):
    entries = _entries(file_view_client.get(BASE))

    assert list(entries) == [".arcreel", "scripts", "storyboards", "CLAUDE.md", "project.json"]
    assert entries["storyboards"]["type"] == "directory"
    assert entries["storyboards"]["size"] == 15
    assert "role" not in entries["storyboards"]
    assert entries["project.json"]["role"] == "structure"
    assert entries["CLAUDE.md"]["role"] == "agent_config"
    assert {"name", "path", "type", "size", "modified_at"} <= set(entries["project.json"])


def test_directory_entries_carry_role_and_owner(file_view_client):
    entries = _entries(file_view_client.get(BASE, params={"path": "storyboards"}))

    assert list(entries) == ["scene_E1S01.png", "scene_E1S02.png"]
    assert entries["scene_E1S01.png"]["role"] == "referenced"
    assert entries["scene_E1S01.png"]["owner"] == {"kind": "segment", "id": "E1S01", "episode": 1}
    assert entries["scene_E1S01.png"]["path"] == "storyboards/scene_E1S01.png"
    assert entries["scene_E1S02.png"]["role"] == "unreferenced"
    assert entries["scene_E1S02.png"]["owner"] is None

    memory = _entries(file_view_client.get(BASE, params={"path": ".arcreel/memory"}))
    assert memory["MEMORY.md"]["owner"] == {"kind": "memory", "id": "MEMORY.md"}


def test_current_version_snapshot_is_marked_current(file_view_client, file_view_project_dir):
    (file_view_project_dir / "versions/storyboards").mkdir(parents=True)
    for name in ("E1S01_v1.png", "E1S01_v2.png"):
        (file_view_project_dir / "versions/storyboards" / name).write_bytes(b"v")
    (file_view_project_dir / "versions/versions.json").write_text(
        json.dumps(
            {
                "storyboards": {
                    "E1S01": {
                        "current_version": 2,
                        "versions": [
                            {"version": 1, "file": "versions/storyboards/E1S01_v1.png"},
                            {"version": 2, "file": "versions/storyboards/E1S01_v2.png"},
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    entries = _entries(file_view_client.get(BASE, params={"path": "versions/storyboards"}))

    assert entries["E1S01_v2.png"]["role"] == "history"
    assert entries["E1S01_v2.png"]["is_current"] is True
    assert "is_current" not in entries["E1S01_v1.png"]


def test_content_previews_text_and_downloads_as_attachment(file_view_client):
    preview = file_view_client.get(f"{BASE}/content", params={"path": "scripts/episode_1.json"})
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/plain")
    assert preview.headers["content-disposition"].startswith("inline")
    assert json.loads(preview.text)["segments"][0]["segment_id"] == "E1S01"

    download = file_view_client.get(
        f"{BASE}/content", params={"path": "storyboards/scene_E1S01.png", "download": "true"}
    )
    assert download.status_code == 200
    assert download.content == b"x" * 10
    assert download.headers["content-disposition"] == "attachment; filename*=utf-8''scene_E1S01.png"


@pytest.mark.parametrize(
    "path",
    [
        ".project.json.lock",
        "project.json.bak.v3-1790943188",
        "../demo/project.json",
        "/etc/hosts",
        "storyboards",
        "missing.txt",
    ],
)
def test_content_refuses_infrastructure_traversal_and_non_files(file_view_client, path):
    assert file_view_client.get(f"{BASE}/content", params={"path": path}).status_code == 404


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform has no symlink")
def test_symlinks_are_neither_listed_nor_followed(file_view_client, file_view_project_dir, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "key.txt").write_text("secret", encoding="utf-8")
    (file_view_project_dir / "storyboards/link.png").symlink_to(file_view_project_dir / "storyboards/scene_E1S01.png")
    (file_view_project_dir / "outside").symlink_to(outside, target_is_directory=True)

    assert "outside" not in _entries(file_view_client.get(BASE))
    assert "link.png" not in _entries(file_view_client.get(BASE, params={"path": "storyboards"}))
    assert file_view_client.get(BASE, params={"path": "outside"}).status_code == 404
    assert file_view_client.get(f"{BASE}/content", params={"path": "outside/key.txt"}).status_code == 404
    assert file_view_client.get(f"{BASE}/content", params={"path": "storyboards/link.png"}).status_code == 404


def test_unknown_project_is_not_found(file_view_client):
    assert file_view_client.get("/api/v1/projects/nope/file-view").status_code == 404
