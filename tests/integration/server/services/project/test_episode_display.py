"""Web 的诊断改写不改变机器定位、正文或 Agent 诊断原文。"""

import json

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
from tests.fakes import use_project_manager


def test_missing_draft_and_task_failure_use_ledger_title_without_changing_ids(tmp_path, monkeypatch) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "Anime", "narration")
    projects.add_episode("demo", 9, "雨夜", "scripts/episode_9.json")
    projects.add_episode("demo", 3, "", "scripts/episode_3.json")
    monkeypatch.setattr(episode_display, "get_project_manager", lambda: projects)
    use_project_manager(monkeypatch, projects)
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


def _write_script(projects: ProjectManager, filename: str, script: dict) -> None:
    path = projects.get_project_path("demo") / "scripts" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def test_item_refs_carry_current_position_excerpt_and_thumbnail(tmp_path) -> None:
    """条目指称带上它在本集数组里的当前位置、正文摘录与缩略图；条目已不在剧本里时位置为 None。"""
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "Anime", "narration")
    projects.add_episode("demo", 9, "雨夜", "scripts/episode_9.json")
    projects.add_episode("demo", 3, "", "scripts/episode_3.json")
    _write_script(
        projects,
        "episode_9.json",
        {
            "episode": 9,
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E9S04", "novel_text": "第一段"},
                {
                    "segment_id": "E9S01",
                    "novel_text": "风从城门外灌进来，吹灭了所有的灯笼和火把",
                    "generated_assets": {"storyboard_image": "storyboards/s1.png", "video_thumbnail": None},
                },
            ],
        },
    )
    _write_script(
        projects,
        "episode_3.json",
        {
            "episode": 3,
            "video_units": [
                {"unit_id": "E3U7", "text": "@[张三] 推门", "generated_assets": {"video_thumbnail": "thumbs/u7.jpg"}}
            ],
        },
    )
    tasks = [
        {"project_name": "demo", "resource_id": "E9S01"},
        {"project_name": "demo", "resource_id": "E9S02"},
        {"project_name": "demo", "resource_id": "E3U7"},
    ]

    refs = [
        task["resource_ref"]
        for task in with_episode_item_refs(tasks, id_field="resource_id", ref_field="resource_ref", projects=projects)
    ]

    assert refs[0] == {
        "episode_title": "雨夜",
        "episode_position": 1,
        "item_id": "S01",
        "item_position": 2,
        "item_excerpt": "风从城门外灌进来，吹灭了所有的灯…",
        "item_thumbnail": "storyboards/s1.png",
    }
    assert refs[1]["item_position"] is None
    assert refs[1]["item_excerpt"] == ""
    assert refs[2] == {
        "episode_title": "",
        "episode_position": 2,
        "item_id": "U7",
        "item_position": 1,
        "item_excerpt": "@张三 推门",
        "item_thumbnail": "thumbs/u7.jpg",
    }
