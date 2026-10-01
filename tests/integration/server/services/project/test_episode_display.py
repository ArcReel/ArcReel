"""Web 的诊断改写不改变机器定位、正文或 Agent 诊断原文。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n import _
from lib.project.project_manager import ProjectManager
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration
from server.error_handlers import register_error_handlers
from server.routers import episode_drafts, grids
from server.services.project import episode_display
from server.services.project.episode_display import present_episode_diagnostics
from server.services.project.episode_item_refs import with_episode_item_refs


def test_missing_draft_and_task_failure_use_ledger_title_without_changing_ids(tmp_path, monkeypatch) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "Anime", "narration")
    projects.add_episode("demo", 9, "雨夜", "scripts/episode_9.json")
    projects.add_episode("demo", 3, "", "scripts/episode_3.json")
    monkeypatch.setattr(episode_drafts, "get_project_manager", lambda: projects)
    monkeypatch.setattr(episode_display, "get_project_manager", lambda: projects)
    monkeypatch.setattr(grids, "get_project_manager", lambda: projects)
    grid = GridGeneration.create(9, "episode_9.json", ["E9S01"], 2, 2, "grid_4", "p", "m", "16:9")
    grid.status = "failed"
    grid.error_message = "集（id=9）的 E9S01 编写失败"
    stored_grids = GridManager(projects.get_project_path("demo"))
    stored_grids.save(grid)
    app = FastAPI()
    app.include_router(episode_drafts.router)
    app.include_router(grids.router)
    register_error_handlers(app)
    with TestClient(app) as client:
        for locale in ("zh", "en", "vi"):
            response = client.get(
                "/projects/demo/episodes/9/drafts/narration_script_plan", headers={"Accept-Language": locale}
            )
            assert response.status_code == 404
            assert "雨夜" in response.json()["detail"]
            assert "id=9" not in response.json()["detail"]
            for path in ("grids", f"grids/{grid.id}"):
                response = client.get(f"/projects/demo/{path}", headers={"Accept-Language": locale})
                assert response.status_code == 200
                shown_grid = response.json()[0] if path == "grids" else response.json()
                assert shown_grid["error_message"] == "雨夜的 雨夜 · S01 编写失败"
                assert shown_grid["scene_ids"] == ["E9S01"]
                assert shown_grid["episode"] == 9
    assert stored_grids.get(grid.id).error_message == grid.error_message

    task = {
        "project_name": "demo",
        "resource_id": "E9S01",
        "error_message": "集（id=9）的 E9S01 编写失败",
        "error_params": {"episode": 9, "unit_id": "E9S01"},
        "result": {"warnings": ["Episode (id=3): E3S02"]},
    }
    [shown] = with_episode_item_refs([task], id_field="resource_id", ref_field="resource_ref", projects=projects)
    assert "雨夜 · S01" in shown["error_message"]
    assert shown["resource_id"] == task["resource_id"]
    assert shown["error_params"] == task["error_params"]
    assert shown["resource_ref"]["episode_position"] == 1
    assert shown["result"]["warnings"] == ["第 2 集: 第 2 集 · S02"]
    assert "id=9" in task["error_message"]


def test_diagnostic_rewrite_preserves_identifiers_and_user_text() -> None:
    value = {
        "unit_id": "E9S01",
        "repair_reason": "episode script scripts/episode_9.json is invalid",
        "affected_ids": ["E9S01"],
        "params": {"unit_id": "E9S01", "episode": 9},
        "content": {"text": "E9S01 是用户原文"},
        "problems": [{"reason": "集（id=9）: scripts/episode_9.json / E9S01", "unit_id": "E9S01"}],
    }
    project = {"episodes": [{"episode": 9, "title": "雨夜"}]}
    shown = present_episode_diagnostics(value, project, _)
    assert shown["unit_id"] == value["unit_id"]
    assert shown["affected_ids"] == value["affected_ids"]
    assert shown["repair_reason"] == "episode script 「雨夜」的剧本 is invalid"
    assert shown["params"] == value["params"]
    assert shown["content"] == value["content"]
    assert "雨夜 · S01" in shown["problems"][0]["reason"]
    assert "episode_9" not in shown["problems"][0]["reason"]
    assert shown["problems"][0]["unit_id"] == "E9S01"
