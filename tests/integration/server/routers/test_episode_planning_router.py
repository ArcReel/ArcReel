"""「AI 规划分集」的 Web 入口：POST /projects/{name}/episode-planning 的准入拒绝。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import episode_planning
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
def planning_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(pm.data_root))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    monkeypatch.setattr(episode_planning, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(episode_planning.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def test_planning_without_a_whole_source_is_refused(planning_client: TestClient) -> None:
    with planning_client:
        resp = planning_client.post("/api/v1/projects/demo/episode-planning", json={"instructions": "按章节"})

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("分集规划未能提交：项目还没有整本源文")


def test_instructions_are_the_only_accepted_field(planning_client: TestClient) -> None:
    with planning_client:
        resp = planning_client.post("/api/v1/projects/demo/episode-planning", json={"continue_to_end": False})

    # 请求体校验在准入之前：detail 是逐字段的校验错误列表，不是准入拒绝的文案
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert any("continue_to_end" in item["loc"] for item in detail)
