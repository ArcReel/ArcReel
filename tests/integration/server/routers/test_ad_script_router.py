"""广告/短片「AI 生成脚本」的 Web 入口：POST /projects/{name}/episodes/{n}/ad-script 的准入拒绝与覆盖确认。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import ad_script
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.usefixtures("video_request_facts")


@pytest.fixture
def ad_projects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(pm.data_root))
    pm.create_project("demo", content_mode="ad")
    pm.create_project_metadata("demo", "Demo", "", "ad")
    monkeypatch.setattr(ad_script, "get_project_manager", lambda: pm)
    return pm


@pytest.fixture
def ad_script_client(ad_projects: ProjectManager) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(ad_script.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def _shot(shot_id: str) -> dict:
    return {
        "shot_id": shot_id,
        "section": "opening",
        "duration_seconds": 4,
        "voiceover_text": "",
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": {
            "scene": "雨夜街角",
            "composition": {"shot_type": "Medium Shot", "lighting": "冷色路灯", "ambiance": "潮湿"},
        },
        "video_prompt": {"action": "两人走过街角", "camera_motion": "Static", "ambiance_audio": "雨声", "dialogue": []},
    }


def test_generation_without_a_brief_or_products_is_refused(ad_script_client: TestClient) -> None:
    with ad_script_client:
        resp = ad_script_client.post("/api/v1/projects/demo/episodes/1/ad-script", json={})

    assert resp.status_code == 422
    assert "创作灵感" in resp.json()["detail"]


def test_regenerating_an_existing_script_returns_the_loss_list_without_submitting(
    ad_script_client: TestClient, ad_projects: ProjectManager
) -> None:
    ad_projects.update_project("demo", lambda project: project.update({"brief": "雨夜里两个陌生人共用一把伞"}))
    ad_projects.save_script(
        "demo",
        {"episode": 1, "title": "共伞", "content_mode": "ad", "shots": [_shot("E1S01"), _shot("E1S02")]},
        "episode_1.json",
    )

    with ad_script_client:
        resp = ad_script_client.post("/api/v1/projects/demo/episodes/1/ad-script", json={"regenerate": True})

    assert resp.status_code == 409
    overwrite = resp.json()["diagnostic"]["script_overwrite"]
    assert [entry["id"] for entry in overwrite["entries"]] == ["E1S01", "E1S02"]
    assert overwrite["revision"]
    assert "2 条脚本条目全部移除" in overwrite["text"]


def test_unknown_fields_are_rejected(ad_script_client: TestClient) -> None:
    with ad_script_client:
        resp = ad_script_client.post("/api/v1/projects/demo/episodes/1/ad-script", json={"entry_ids": ["E1S01"]})

    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)
