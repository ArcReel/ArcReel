"""Web 端写入经真实项目事件服务按 ``webui`` 广播；Agent 的同类写入仍按原来源广播。

前端据来源区分自己的操作与外部改动：非 ``webui`` 的草稿新建会自动跳到对应集，其余变更弹通知、排队聚焦。
每个用例订阅事件流后发起一次写入，收齐这次写入引起的全部批次再断言来源：除了显式广播的那一批，
事件服务读盘补扫或 hint 唤醒扫描发现的连带变更（草稿消失、登记变化）同样要带上发起方的来源。

来源按扫描读盘前已到达的 hint 结算。一次写入若含多步落盘，前一步的 hint 唤醒的扫描可能读到后一步的
盘面，而后一步的 hint 尚未送达，这次变更就结算成 ``filesystem``；定时轮询落在写盘与 hint 之间同理。
这是来源按扫描窗口结算的已知窗口，不由本组用例覆盖：用例关掉定时轮询，并在写入在途期间扣住事件服务读盘，
写入返回后各步的 hint 均已送达，扫描才读盘。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator, Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.project.project_change_hints import emit_project_change_batch, project_change_source
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_path, write_quarantine
from server.draft_workflow import DraftContext, DraftWorkflow
from server.error_handlers import register_error_handlers
from server.routers import episode_drafts, files
from server.routers import project_events as project_events_router
from server.services.project.project_events import ProjectEventService, read_project_state
from tests.fakes import use_project_manager

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
_DOC_TYPE = "narration_script_plan"
# 定时轮询不参与：扫描只由写入出口的 hint 与显式批次唤醒，收齐事件由显式批次屏障确认。
_POLL_SECONDS = 3600.0


class _ConnectedRequest:
    def __init__(self, service: ProjectEventService):
        self.app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))

    async def is_disconnected(self) -> bool:
        return False


@dataclass
class _Env:
    pm: ProjectManager
    project_path: Path
    client: AsyncClient
    batches: asyncio.Queue[dict]
    reads_open: threading.Event

    @contextlib.contextmanager
    def reads_held(self) -> Generator[None]:
        """写入在途期间扣住事件服务读盘；退出时各步的 hint 均已送达，被唤醒的扫描才读盘。"""
        self.reads_open.clear()
        try:
            yield
        finally:
            self.reads_open.set()

    async def batches_until(self, predicate: Callable[[dict], bool]) -> list[dict]:
        """等待真实变更，再用串行重建的两条屏障批次收齐连带变更。"""
        collected: list[dict] = []

        async def _pull_until_match() -> None:
            while True:
                data = await self.batches.get()
                collected.append(data)
                if any(predicate(change) for change in data["changes"]):
                    return

        await asyncio.wait_for(_pull_until_match(), timeout=5)
        # 批次先广播自身再补扫；第二条到达证明第一条的补扫已结束。屏障不发 hint，不改变真实变更的来源。
        for marker in ("scan", "done"):
            emit_project_change_batch(
                "demo",
                [{"entity_type": "test_scan_barrier", "action": "updated", "entity_id": marker}],
                source="filesystem",
            )
        async with asyncio.timeout(5):
            while True:
                data = await self.batches.get()
                if data["changes"][0]["entity_type"] == "test_scan_barrier":
                    if data["changes"][0]["entity_id"] == "done":
                        return collected
                else:
                    collected.append(data)


def _is(entity_type: str, action: str, entity_id: str | None = None) -> Callable[[dict], bool]:
    def predicate(change: dict) -> bool:
        return (
            change["entity_type"] == entity_type
            and change["action"] == action
            and (entity_id is None or change["entity_id"] == entity_id)
        )

    return predicate


def _sources(batches: list[dict]) -> set[str]:
    return {batch["source"] for batch in batches}


def _changes(batches: list[dict]) -> list[tuple[str, str, str]]:
    return [(c["entity_type"], c["action"], c["entity_id"]) for batch in batches for c in batch["changes"]]


@pytest.fixture
async def env(tmp_path: Path, monkeypatch) -> AsyncIterator[_Env]:
    """已订阅 demo 项目事件流的旁白项目：第 1 集自带原文，已有正式脚本规划。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    pm.update_project("demo", lambda project: project["episodes"][0].update(source_origin="own"))
    project_path = pm.get_project_path("demo")
    (project_path / "source").mkdir(exist_ok=True)
    (project_path / "source" / "episode_1.txt").write_text(_NOVEL, encoding="utf-8")
    formal = project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    formal.parent.mkdir(parents=True)
    formal.write_text(json.dumps({"segments": [_SEGMENT]}, ensure_ascii=False), encoding="utf-8")

    use_project_manager(monkeypatch, pm)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(files.router)
    app.include_router(episode_drafts.router)

    reads_open = threading.Event()
    reads_open.set()

    def _read_state_when_open(project_name: str):
        assert reads_open.wait(timeout=5), "写入在途过久，事件服务读盘一直被扣住"
        return read_project_state(pm, project_name)

    service = ProjectEventService(tmp_path, poll_interval=_POLL_SECONDS, read_state=_read_state_when_open)
    await service.start()
    stream = project_events_router.stream_project_events("demo", _ConnectedRequest(service), service=service)
    assert (await anext(stream)).event == "snapshot"
    # 事件流由后台任务转进队列：对流本身设超时会取消生成器、关掉订阅。
    batches: asyncio.Queue[dict] = asyncio.Queue()

    async def _pump() -> None:
        async for event in stream:
            batches.put_nowait(event.data)

    pump = asyncio.create_task(_pump())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield _Env(pm, project_path, client, batches, reads_open)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        await stream.aclose()
        await service.shutdown()


def _write_narration_draft(project_path: Path, segment: dict) -> None:
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [segment]},
        violations=[],
        meta={"source": None},
    )


async def _draft_revision(env: _Env) -> str:
    response = await env.client.get(f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}")
    assert response.status_code == 200
    return response.json()["revision"]


# ---------------------------------------------------------------- files.py


async def test_creating_an_unregistered_source_file_broadcasts_as_webui(env: _Env) -> None:
    with env.reads_held():
        response = await env.client.put(
            "/projects/demo/source/novel.txt", content="甲乙丙丁", headers={"content-type": "text/plain"}
        )
    assert response.status_code == 200

    batches = await env.batches_until(lambda change: True)

    assert env.pm.load_project("demo")["whole_source_files"] == [{"source_file": "source/novel.txt"}]
    assert _sources(batches) == {"webui"}


async def test_deleting_an_episode_own_source_broadcasts_as_webui(env: _Env) -> None:
    with env.reads_held():
        response = await env.client.delete("/projects/demo/source/episode_1.txt")
    assert response.status_code == 200

    batches = await env.batches_until(_is("episode", "updated"))

    assert env.pm.load_project("demo")["episodes"][0]["source_origin"] == "none"
    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}


async def test_saving_a_new_plain_draft_broadcasts_as_webui(env: _Env) -> None:
    (env.project_path / "drafts" / "episode_1" / "script_plan_segments.json").unlink()

    with env.reads_held():
        response = await env.client.put(
            "/projects/demo/drafts/1/script_plan",
            content=json.dumps({"segments": [_SEGMENT]}, ensure_ascii=False),
            headers={"content-type": "text/plain"},
        )
    assert response.status_code == 200

    batches = await env.batches_until(_is("draft", "created", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


async def test_deleting_a_plain_draft_broadcasts_as_webui(env: _Env) -> None:
    with env.reads_held():
        response = await env.client.delete("/projects/demo/drafts/1/script_plan")
    assert response.status_code == 200

    batches = await env.batches_until(_is("trash", "updated"))

    assert _sources(batches) == {"webui"}


async def test_saving_a_reference_plan_broadcasts_as_webui(env: _Env) -> None:
    env.pm.update_project("demo", lambda project: project.update(generation_mode="reference_video"))
    await env.batches_until(lambda change: True)

    with env.reads_held():
        response = await env.client.put(
            "/projects/demo/drafts/1/script_plan",
            content=json.dumps({"units": [{"unit_id": "E1U01", "text": "镜头描述", "duration_seconds": 8}]}),
            headers={"content-type": "text/plain"},
        )
    assert response.status_code == 200
    assert response.json()["path"] == "drafts/episode_1/script_plan_reference_units.json"

    batches = await env.batches_until(_is("draft", "created", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- episode_drafts.py


async def test_saving_an_episode_draft_that_stays_a_draft_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    with env.reads_held():
        response = await env.client.put(
            f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}",
            json={
                "content": {"segments": [{**_SEGMENT, "characters_in_segment": ["李四"]}]},
                "base_revision": revision,
            },
        )
    assert response.status_code == 200
    assert response.json()["adopted"] is False

    batches = await env.batches_until(_is("draft", "updated", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


async def test_saving_an_episode_draft_that_is_adopted_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    with env.reads_held():
        response = await env.client.put(
            f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}",
            json={"content": {"segments": [{**_SEGMENT, "duration_seconds": 6}]}, "base_revision": revision},
        )
    assert response.status_code == 200
    assert response.json()["adopted"] is True

    batches = await env.batches_until(_is("draft", "deleted", "episode_1_script_plan"))

    assert not quarantine_path(env.project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).exists()
    assert _sources(batches) == {"webui"}


async def test_discarding_an_episode_draft_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    with env.reads_held():
        response = await env.client.delete(
            f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}", params={"base_revision": revision}
        )
    assert response.status_code == 200

    batches = await env.batches_until(_is("draft", "deleted", "episode_1_script_plan"))

    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- Agent 的同类写入


@pytest.mark.parametrize("source", ["filesystem", "worker"])
async def test_agent_draft_writes_keep_their_original_source(env: _Env, source) -> None:
    """Agent 或任务的草稿写入、改写与丢弃保留原来源，前端照常提醒创作者。"""
    workflow = DraftWorkflow(DraftContext(project_name="demo", data_root=env.pm.data_root, pm=env.pm))

    with project_change_source(source):
        with env.reads_held():
            opened = await workflow.open(1, _DOC_TYPE)
        created = await env.batches_until(_is("draft", "created"))
        with env.reads_held():
            await workflow.patch(1, _DOC_TYPE, {"segments": [{**_SEGMENT, "duration_seconds": 6}]}, opened["revision"])
        updated = await env.batches_until(_is("draft", "updated"))
        reread = await workflow.open(1, _DOC_TYPE)
        with env.reads_held():
            await workflow.discard(1, _DOC_TYPE, reread["revision"])
        discarded = await env.batches_until(_is("draft", "deleted"))

    assert ("trash", "updated", "trash") in _changes(discarded)
    assert _sources(created) == _sources(updated) == _sources(discarded) == {source}
