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


def _seed_unit(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "Shot 1 (3s): @张三 推门", "references": [{"type": "character", "name": "张三"}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["unit"]["unit_id"]


def test_patch_unit_prompt_recomputes_duration(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "Shot 1 (4s): @张三 推门\nShot 2 (6s): @酒馆 全景"},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["duration_seconds"] == 10
    # 注意：prompt 新增的 @酒馆 应由 caller 先 PATCH references 再 PATCH prompt；本端点仅按旧 references 映射
    assert len(unit["references"]) == 1


def test_patch_unit_references_only(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["unit"]["references"]) == 2


def test_patch_unit_rejects_unknown_reference(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [{"type": "prop", "name": "不存在"}]},
    )
    assert resp.status_code == 400


def test_patch_unknown_unit_404(client: TestClient):
    resp = client.patch(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9",
        json={"note": "hi"},
    )
    assert resp.status_code == 404


def test_delete_unit_removes_entry(client: TestClient):
    uid = _seed_unit(client)
    resp = client.delete(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}")
    assert resp.status_code == 204
    resp = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.json()["units"] == []


def test_delete_unknown_unit_404(client: TestClient):
    resp = client.delete("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9")
    assert resp.status_code == 404


def test_reorder_units_applies_new_order(client: TestClient):
    uid1 = _seed_unit(client)
    uid2 = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid2, uid1]},
    )
    assert resp.status_code == 200, resp.text
    units = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"]
    assert [u["unit_id"] for u in units] == [uid2, uid1]


def test_reorder_units_rejects_length_mismatch(client: TestClient):
    uid = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid, "E1U999"]},
    )
    assert resp.status_code == 400


def test_reorder_units_rejects_duplicates(client: TestClient):
    uid = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid, uid]},
    )
    assert resp.status_code == 400


def test_generate_unit_enqueues_task(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    uid = _seed_unit(client)

    enqueued: list[dict] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-xyz", "deduped": False}

    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())

    resp = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "task-xyz"
    assert enqueued[0]["task_type"] == "reference_video"
    assert enqueued[0]["media_type"] == "video"
    assert enqueued[0]["resource_id"] == uid


def test_generate_unit_missing_returns_404(client: TestClient):
    resp = client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9/generate")
    assert resp.status_code == 404
