"""剪辑时间线路由：只验证命令结果与领域错误到 HTTP 状态码的映射。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.edit_timeline import EditTimelineError, EditTimelineService
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from tests.auth_deps import override_auth


class _FailingService:
    def __init__(self, error: EditTimelineError) -> None:
        self.error = error

    async def list_timelines(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error

    async def create_from_script(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error

    async def read(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error


def _client(service: Any) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_edit_timeline_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (EditTimelineError("project_not_found", "", project="demo"), 404),
        (EditTimelineError("episode_not_found", "", episode=9), 404),
        (EditTimelineError("timeline_not_found", "", timeline_id="tl-0000abcd"), 404),
        (EditTimelineError("revision_not_found", "", timeline_id="tl-0000abcd", revision=9, latest_revision=2), 404),
        (EditTimelineError("timeline_name_conflict", "", episode=1, name="完整版"), 409),
        (EditTimelineError("timeline_name_invalid", "", name=""), 422),
        (EditTimelineError("script_invalid", "", episode=1), 422),
        (EditTimelineError("timeline_invalid", "", file="tl-0000abcd.json"), 422),
    ],
)
def test_domain_errors_map_to_status_codes(error: EditTimelineError, status: int) -> None:
    client = _client(_FailingService(error))

    responses = [
        client.get("/api/v1/projects/demo/edit-timelines"),
        client.post("/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"}),
        client.get("/api/v1/projects/demo/edit-timelines/tl-0000abcd"),
    ]

    assert [response.status_code for response in responses] == [status] * 3
    assert all(response.json()["detail"] for response in responses)


def test_create_answers_201_with_the_first_revision(tmp_path) -> None:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": []},
        "episode_1.json",
    )
    client = _client(EditTimelineService(pm))

    created = client.post("/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"})
    listed = client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})

    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["timelines"]] == ["完整版"]


def test_create_rejects_unknown_source() -> None:
    client = _client(_FailingService(EditTimelineError("episode_not_found", "", episode=1)))

    response = client.post("/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "blank", "name": "x"})

    assert response.status_code == 422
