"""成片路由：提交入队到 render 车道、读取现状与下载地址，以及成片错误到 HTTP 状态码的映射。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.model import BgmClip, TimelineRevision
from lib.edit_timeline.operations import SetReason
from lib.edit_timeline.store import EditTimelineStore
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutService
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from tests.auth_deps import override_auth
from tests.factories import install_current_video, make_test_clip


def _install(timeline_project: ProjectManager, tmp_path: Path, unit_id: str) -> None:
    source = tmp_path / "media" / f"{unit_id}.mp4"
    make_test_clip(source, size="160x90", fps=30, seconds=0.5, tone=True)
    install_current_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, source)


async def _timeline(timeline_project: ProjectManager) -> str:
    readout = await EditTimelineService(timeline_project).create_from_script(
        "demo", episode=1, name="完整版", author=RevisionAuthor(kind="arcreel_agent")
    )
    return readout.timeline.id


def _app(service: Any, queue: Any) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(edit_timelines.router, prefix="/api/v1")
    app.dependency_overrides[edit_timelines.get_final_cut_service] = lambda: service
    app.dependency_overrides[get_generation_queue] = lambda: queue
    return app


@pytest.fixture
async def final_cut_client(file_db_factory, timeline_project: ProjectManager) -> AsyncIterator[AsyncClient]:
    app = _app(
        FinalCutService(timeline_project),
        GenerationQueue(session_factory=file_db_factory, project_manager=timeline_project),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_submit_queues_a_render_task_and_status_reads_missing_until_rendered(
    tmp_path: Path, timeline_project: ProjectManager, final_cut_client: AsyncClient
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    _install(timeline_project, tmp_path, "E1U2")
    timeline_id = await _timeline(timeline_project)
    url = f"/api/v1/projects/demo/edit-timelines/{timeline_id}/final-cut"
    clean = {"subtitles": "no_subtitles"}

    submitted = await final_cut_client.post(url, json=clean)
    again = await final_cut_client.post(url, json=clean)
    pinned = await final_cut_client.post(url, json={**clean, "revision": 1})
    await EditTimelineService(timeline_project).edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="补充理由",
        operations=[SetReason(op="set_reason", clip="c1", reason="保留开场")],
        author=RevisionAuthor(kind="arcreel_agent"),
    )
    # 省略 revision 的请求按提交时的最新修订入队：时间线前进后不再去重到旧修订的任务。
    conflicting = await final_cut_client.post(url, json=clean)
    before = await final_cut_client.get(url, params=clean)

    assert submitted.status_code == 202
    assert again.json() == pinned.json() == {**submitted.json(), "deduped": True}
    assert conflicting.status_code == 409
    assert submitted.json()["task_id"] in conflicting.json()["detail"]
    assert (before.json()["status"], before.json()["download_url"]) == ("missing", None)

    rendered = await FinalCutService(timeline_project).render(
        "demo", timeline_id, narration="without_narration", subtitles="no_subtitles"
    )
    after = await final_cut_client.get(url, params=clean)

    assert after.json()["status"] == "current"
    assert after.json()["artifact_path"] == submitted.json()["artifact_path"] == rendered.artifact_path
    assert after.json()["download_url"] == f"/api/v1/files/demo/{rendered.artifact_path}?v=1"


async def test_omitted_options_follow_the_project_and_burn_subtitles_while_narration_needs_tts(
    tmp_path: Path, timeline_project: ProjectManager, final_cut_client: AsyncClient
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    _install(timeline_project, tmp_path, "E1U2")
    timeline_id = await _timeline(timeline_project)
    url = f"/api/v1/projects/demo/edit-timelines/{timeline_id}/final-cut"

    defaulted = await final_cut_client.post(url)
    status = await final_cut_client.get(url)
    narrated = await final_cut_client.post(url, json={"narration": "with_narration"})

    # 后期配音项目省略旁白版本时不带旁白；字幕默认烧入。
    assert defaulted.status_code == 202
    assert defaulted.json()["artifact_path"].endswith("/final_cut.without_narration.burned_subtitles.mp4")
    assert (status.json()["narration"], status.json()["subtitles"]) == ("without_narration", "burned_subtitles")
    assert narrated.status_code == 422
    assert narrated.json()["detail"]


async def test_blocking_issues_answer_409_naming_the_units(
    tmp_path: Path, timeline_project: ProjectManager, final_cut_client: AsyncClient
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    timeline_id = await _timeline(timeline_project)

    response = await final_cut_client.post(f"/api/v1/projects/demo/edit-timelines/{timeline_id}/final-cut")

    assert response.status_code == 409
    assert "未命名集 · U2" in response.json()["detail"]
    assert [issue["unit_id"] for issue in response.json()["diagnostic"]["issues"]] == ["E1U2"]


class _RefusingService:
    def __init__(self, error: FinalCutError) -> None:
        self.error = error

    async def check(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.error


def _add_bgm(timeline_project: ProjectManager, timeline_id: str) -> None:
    store = EditTimelineStore(timeline_project, "demo")
    document = store.find(timeline_id)
    latest = document.latest
    revision = TimelineRevision(
        number=latest.number + 1,
        parent=latest.number,
        author=RevisionAuthor(kind="creator", user_id="u1"),
        summary="加 BGM",
        created_at=datetime.now(UTC).isoformat(),
        content=latest.content.model_copy(
            update={"bgm": (BgmClip(id="b1", bgm_id="theme", start_us=0, in_us=0, out_us=500_000),)}
        ),
    )
    with store.locked_episode(document.episode):
        store.write(document.model_copy(update={"next_bgm_number": 2, "revisions": (*document.revisions, revision)}))


@pytest.mark.parametrize(("content", "status"), [("missing_bgm", 409), ("empty", 422)])
async def test_timelines_with_missing_bgm_or_no_clips_are_refused(
    content: str,
    status: int,
    tmp_path: Path,
    timeline_project: ProjectManager,
    final_cut_client: AsyncClient,
) -> None:
    from lib.edit_timeline.operations import DeleteClip

    for unit_id in ("E1U1", "E1U2"):
        _install(timeline_project, tmp_path, unit_id)
    timeline_id = await _timeline(timeline_project)
    if content == "missing_bgm":
        _add_bgm(timeline_project, timeline_id)
    else:
        await EditTimelineService(timeline_project).edit(
            "demo",
            timeline_id,
            base_revision=1,
            summary="调整片段",
            operations=[DeleteClip(op="delete", clip="c1"), DeleteClip(op="delete", clip="c2")],
            author=RevisionAuthor(kind="creator", user_id="u1"),
        )

    response = await final_cut_client.post(f"/api/v1/projects/demo/edit-timelines/{timeline_id}/final-cut")

    assert response.status_code == status
    assert response.json()["detail"]


async def test_unavailable_ffmpeg_answers_503() -> None:
    app = _app(_RefusingService(FinalCutError("final_cut_ffmpeg_unavailable", "")), queue=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/v1/projects/demo/edit-timelines/tl-0000abcd/final-cut")

    assert response.status_code == 503
    assert response.json()["detail"]
