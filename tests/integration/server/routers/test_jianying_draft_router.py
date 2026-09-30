"""剪映草稿路由：提交入队到 render 车道、读取现状、凭下载 token 下载，以及领域错误到 HTTP 状态码的映射。"""

from __future__ import annotations

import zipfile
from collections.abc import AsyncIterator
from io import BytesIO

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import SetReason
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.project.project_manager import ProjectManager
from server.auth import create_download_token
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService
from tests.auth_deps import override_auth
from tests.factories import install_uploaded_video


def _install(timeline_project: ProjectManager, unit_id: str) -> None:
    install_uploaded_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, seconds=0.5)


async def _timeline(timeline_project: ProjectManager) -> str:
    readout = await EditTimelineService(timeline_project).create_from_script(
        "demo", episode=1, name="完整版", author=RevisionAuthor(kind="arcreel_agent")
    )
    return readout.timeline.id


@pytest.fixture
async def draft_client(file_db_factory, timeline_project: ProjectManager) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.include_router(edit_timelines.self_auth_router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_jianying_draft_service] = lambda: TimelineJianyingDraftService(
        timeline_project
    )
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=timeline_project)
    app.dependency_overrides[get_generation_queue] = lambda: queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _download_params(token: str, narration: str = "without_narration") -> dict[str, str]:
    return {"draft_path": "/Users/me/JianyingPro Drafts", "download_token": token, "narration": narration}


async def test_submit_queues_an_export_and_the_registered_draft_downloads_for_the_local_directory(
    timeline_project: ProjectManager, draft_client: AsyncClient
) -> None:
    _install(timeline_project, "E1U1")
    _install(timeline_project, "E1U2")
    timeline_id = await _timeline(timeline_project)
    url = f"/api/v1/projects/demo/edit-timelines/{timeline_id}/jianying-draft"
    download = f"{url}/download"
    token = create_download_token("testuser", "demo")

    submitted = await draft_client.post(url)
    again = await draft_client.post(url, json={"narration": "without_narration", "revision": 1})
    await EditTimelineService(timeline_project).edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="补充理由",
        operations=[SetReason(op="set_reason", clip="c1", reason="保留开场")],
        author=RevisionAuthor(kind="arcreel_agent"),
    )
    # 省略 revision 的请求按提交时的最新修订入队：时间线前进后不再去重到旧修订的任务。
    conflicting = await draft_client.post(url)
    before = await draft_client.get(url)
    not_exported = await draft_client.get(download, params=_download_params(token))

    assert submitted.status_code == 202
    assert submitted.json()["artifact_path"] == f"renders/episode_1/{timeline_id}/jianying_draft.without_narration.zip"
    assert again.json() == {**submitted.json(), "deduped": True}
    assert conflicting.status_code == 409
    assert submitted.json()["task_id"] in conflicting.json()["detail"]
    assert (before.json()["status"], before.json()["version"]) == ("missing", None)
    assert not_exported.status_code == 404

    await TimelineJianyingDraftService(timeline_project).render("demo", timeline_id, narration="without_narration")
    after = await draft_client.get(url)
    downloaded = await draft_client.get(download, params=_download_params(token))
    other_project = await draft_client.get(download, params=_download_params(create_download_token("testuser", "x")))

    assert (after.json()["status"], after.json()["version"]) == ("current", 1)
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        assert "Demo_第1集_完整版/draft_info.json" in archive.namelist()
    assert other_project.status_code == 403


async def test_submit_refusals_answer_before_anything_is_queued(
    timeline_project: ProjectManager, draft_client: AsyncClient
) -> None:
    _install(timeline_project, "E1U1")
    timeline_id = await _timeline(timeline_project)
    url = f"/api/v1/projects/demo/edit-timelines/{timeline_id}/jianying-draft"

    blocked = await draft_client.post(url)
    narration = await draft_client.post(url, json={"narration": "with_narration"})

    assert blocked.status_code == 409
    assert "E1U2" in blocked.json()["detail"]
    assert [issue["unit_id"] for issue in blocked.json()["diagnostic"]["issues"]] == ["E1U2"]
    assert narration.status_code == 422
    assert narration.json()["detail"]


async def test_a_draft_whose_project_media_is_gone_answers_409_on_download(
    timeline_project: ProjectManager, draft_client: AsyncClient
) -> None:
    _install(timeline_project, "E1U1")
    _install(timeline_project, "E1U2")
    timeline_id = await _timeline(timeline_project)
    await TimelineJianyingDraftService(timeline_project).render("demo", timeline_id, narration="without_narration")
    project_dir = timeline_project.get_project_path("demo")
    snapshot = VersionManager(project_dir).get_versions("reference_videos", "E1U1")["versions"][0]["file"]
    (project_dir / snapshot).unlink()

    response = await draft_client.get(
        f"/api/v1/projects/demo/edit-timelines/{timeline_id}/jianying-draft/download",
        params=_download_params(create_download_token("testuser", "demo")),
    )

    assert response.status_code == 409
    assert response.json()["detail"]
