"""项目事件流对草稿的实时推送：Agent 写入、采用、丢弃草稿时，订阅方收到对应的草稿事件。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.project.project_manager import ProjectManager
from server.draft_workflow import DraftContext, DraftWorkflow
from server.routers import project_events as project_events_router
from server.services.project.project_events import ProjectEventService

pytestmark = pytest.mark.usefixtures("video_request_facts")

_NOVEL = "张三在村口等人。"
_SEGMENT = {
    "segment_id": "E1S01",
    "novel_text": _NOVEL,
    "duration_seconds": 4,
    "segment_break": False,
    "characters_in_segment": [],
    "scenes": [],
    "props": [],
}


class _ConnectedRequest:
    def __init__(self, service: ProjectEventService):
        self.app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))

    async def is_disconnected(self) -> bool:
        return False


@pytest.fixture
async def project_stream(tmp_path: Path):
    """已订阅 demo 项目事件流的旁白项目：返回 (Agent 的草稿命令, 事件流)。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = pm.get_project_path("demo")
    (project_path / "source").mkdir(exist_ok=True)
    (project_path / "source" / "episode_1.txt").write_text(_NOVEL, encoding="utf-8")
    formal = project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    formal.parent.mkdir(parents=True)
    formal.write_text(json.dumps({"segments": [_SEGMENT]}, ensure_ascii=False), encoding="utf-8")

    service = ProjectEventService(tmp_path, poll_interval=0.1)
    await service.start()
    stream = project_events_router.stream_project_events("demo", _ConnectedRequest(service), service=service)
    assert (await anext(stream)).event == "snapshot"
    workflow = DraftWorkflow(DraftContext(project_name="demo", data_root=pm.data_root, pm=pm))
    try:
        yield workflow, stream
    finally:
        await stream.aclose()
        await service.shutdown()


async def _next_draft_change(stream) -> dict:
    async def _pull() -> dict:
        while True:
            event = await anext(stream)
            drafts = [change for change in event.data["changes"] if change["entity_type"] == "draft"]
            if drafts:
                return drafts[0]

    return await asyncio.wait_for(_pull(), timeout=5)


async def test_agent_writing_a_draft_pushes_draft_events(project_stream) -> None:
    workflow, stream = project_stream

    opened = await workflow.open(1, "narration_script_plan")
    created = await _next_draft_change(stream)
    await workflow.patch(
        1, "narration_script_plan", {"segments": [{**_SEGMENT, "duration_seconds": 6}]}, opened["revision"]
    )
    updated = await _next_draft_change(stream)

    assert (created["action"], created["entity_id"], created["doc_type"], created["episode"]) == (
        "created",
        "episode_1_script_plan",
        "narration_script_plan",
        1,
    )
    assert (updated["action"], updated["entity_id"]) == ("updated", "episode_1_script_plan")


async def test_adopting_a_draft_pushes_a_draft_deleted_event(project_stream) -> None:
    workflow, stream = project_stream
    opened = await workflow.open(1, "narration_script_plan")
    await _next_draft_change(stream)

    await workflow.promote(1, "narration_script_plan", opened["revision"])

    change = await _next_draft_change(stream)
    assert (change["action"], change["entity_id"]) == ("deleted", "episode_1_script_plan")


async def test_discarding_a_draft_pushes_a_draft_deleted_event(project_stream) -> None:
    workflow, stream = project_stream
    opened = await workflow.open(1, "narration_script_plan")
    await _next_draft_change(stream)

    await workflow.discard(1, "narration_script_plan", opened["revision"])

    change = await _next_draft_change(stream)
    assert (change["action"], change["entity_id"]) == ("deleted", "episode_1_script_plan")
