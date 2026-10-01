"""剪辑时间线路由：命令结果、新建的准入，以及领域错误到 HTTP 状态码的映射。"""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.edit_timeline import EditTimelineError, EditTimelineService, RevisionAuthor
from lib.final_cut.overview import episode_edit_overview
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowStateService
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from tests.auth_deps import override_auth
from tests.factories import install_uploaded_video


class _FailingService:
    def __init__(self, error: EditTimelineError) -> None:
        self.error = error

    async def list_timelines(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error

    async def create_from_script(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error

    async def read(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error

    async def media(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error


class _NoAdmissionFacts:
    """制作状态读不出准入（如项目不存在）：新建交给命令自己报领域错误。"""

    def get_status(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(target=None, operations={})


def _client(service: Any, workflow: Any = None) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_edit_timeline_service] = lambda: service
    app.dependency_overrides[edit_timelines.get_workflow_state_service] = lambda: workflow or _NoAdmissionFacts()
    app.dependency_overrides[edit_timelines.get_timeline_preview_service] = lambda: service
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
        client.get("/api/v1/projects/demo/edit-timelines/tl-0000abcd/preview-media"),
    ]

    assert [response.status_code for response in responses] == [status] * 4
    assert all(response.json()["detail"] for response in responses)


def _install_available_video(pm: ProjectManager, unit_id: str) -> None:
    """给视频单元落一个可用视频：版本记录、脚本里的产物指针，再补录进产物清单。"""
    project_path = pm.get_project_path("demo")
    install_uploaded_video(project_path, "reference_videos", unit_id, seconds=0.5)
    script = pm.load_script("demo", "episode_1.json")
    for unit in script["video_units"]:
        if unit["unit_id"] == unit_id:
            unit["generated_assets"] = {"video_clip": f"reference_videos/{unit_id}.mp4"}
    pm.save_script("demo", script, "episode_1.json")
    activate_artifact_target_state(project_path, bump_schema=False)


def test_create_answers_201_with_the_first_revision(timeline_project: ProjectManager) -> None:
    _install_available_video(timeline_project, "E1U1")
    client = _client(EditTimelineService(timeline_project), WorkflowStateService(timeline_project))

    created = client.post("/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"})
    listed = client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})

    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["timelines"]] == ["完整版"]


def test_create_is_refused_until_the_episode_has_an_available_video(timeline_project: ProjectManager) -> None:
    client = _client(EditTimelineService(timeline_project), WorkflowStateService(timeline_project))

    refused = client.post("/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"})
    listed = client.get("/api/v1/projects/demo/edit-timelines", params={"episode": 1})

    assert refused.status_code == 422
    assert "no_available_video" not in refused.json()["detail"]
    assert "视频" in refused.json()["detail"]
    assert listed.json()["timelines"] == []


def test_create_leaves_a_missing_project_or_episode_to_the_command(timeline_project: ProjectManager) -> None:
    client = _client(EditTimelineService(timeline_project), WorkflowStateService(timeline_project))
    body = {"from": "script", "name": "完整版"}

    responses = [
        client.post("/api/v1/projects/ghost/episodes/1/edit-timelines", json=body),
        client.post("/api/v1/projects/demo/episodes/9/edit-timelines", json=body),
    ]

    assert [response.status_code for response in responses] == [404, 404]


def test_create_rejects_unknown_source() -> None:
    client = _client(_FailingService(EditTimelineError("episode_not_found", "", episode=1)))

    response = client.post("/api/v1/projects/demo/episodes/1/edit-timelines", json={"from": "blank", "name": "x"})

    assert response.status_code == 422


async def test_edit_overview_states_the_episode_timelines(timeline_project: ProjectManager) -> None:
    await EditTimelineService(timeline_project).create_from_script(
        "demo", episode=1, name="完整版", author=RevisionAuthor(kind="creator", user_id="u1")
    )
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_episode_edit_overview] = lambda: partial(
        episode_edit_overview, timeline_project
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/projects/demo/episodes/1/edit-overview")

    assert response.status_code == 200
    body = response.json()
    assert (body["timeline_count"], body["latest"]["name"], body["stale_final_cuts"]) == (1, "完整版", [])
