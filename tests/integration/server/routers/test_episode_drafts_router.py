"""草稿路由：视频模型配置阻断手修保存时，返回真实原因并保留草稿。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_path, write_quarantine
from server.error_handlers import register_error_handlers
from server.routers import episode_drafts


def test_web_save_blocked_by_the_video_model_reports_the_real_reason(
    tmp_path: Path, video_request_facts, set_video_request_facts, monkeypatch
) -> None:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "张三", "村民")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = pm.get_project_path("demo")
    novel = "张三在村口等人。"
    (project_path / "source" / "episode_1.txt").write_text(novel, encoding="utf-8")
    segment = {
        "segment_id": "E1S01",
        "novel_text": novel,
        "duration_seconds": 4,
        "segment_break": False,
        "characters_in_segment": ["张三"],
        "scenes": [],
        "props": [],
    }
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [{**segment, "characters_in_segment": ["王五"]}]},
        violations=[],
        meta={"source": None},
    )
    monkeypatch.setattr(episode_drafts, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.include_router(episode_drafts.router)
    register_error_handlers(app)
    with TestClient(app) as client:
        path = "/projects/demo/episodes/1/drafts/narration_script_plan"
        revision = client.get(path).json()["revision"]
        set_video_request_facts(
            VideoRequestFactsFailure("video_capability_missing_i2v", (("provider", "gemini"), ("model", "veo-x")))
        )

        response = client.put(
            path,
            json={"content": {"segments": [segment]}, "base_revision": revision},
            headers={"Accept-Language": "zh"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["diagnostic"] == {
        "code": "video_capability_missing_i2v",
        "params": {"provider": "gemini", "model": "veo-x"},
    }
    assert "「图生视频」" in body["detail"]
    assert "video_capability_missing_i2v" not in body["detail"]
    assert quarantine_path(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).exists()
