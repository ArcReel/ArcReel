from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 重定向 projects_root 到 tmp_path
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "T",
                "content_mode": "reference_video",
                "generation_mode": "reference_video",
                "style": "s",
                "characters": {"张三": {"description": "x"}},
                "scenes": {"酒馆": {"description": "x"}},
                "props": {},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "reference_video",
                "summary": "x",
                "novel": {"title": "t", "chapter": "c"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Patch project_manager 的根目录
    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "pm", custom_pm)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app)


def test_list_units_empty(client: TestClient):
    resp = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.status_code == 200
    assert resp.json() == {"units": []}


def test_list_units_404_for_unknown_project(client: TestClient):
    resp = client.get("/api/v1/projects/missing/reference-videos/episodes/1/units")
    assert resp.status_code == 404


def test_add_unit_creates_minimal_entry(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "Shot 1 (3s): @张三 推门", "references": [{"type": "character", "name": "张三"}]},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["unit"]["unit_id"].startswith("E1U")
    assert payload["unit"]["duration_seconds"] == 3
    assert payload["unit"]["references"] == [{"type": "character", "name": "张三"}]


def test_add_unit_rejects_unknown_asset_reference(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "Shot 1 (2s): @未知角色 出现", "references": [{"type": "character", "name": "未知角色"}]},
    )
    assert resp.status_code == 400
    assert "未知角色" in resp.json()["detail"]
