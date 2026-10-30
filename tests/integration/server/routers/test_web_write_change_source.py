"""Web 端写入经真实项目事件服务按 ``webui`` 广播；Agent 的同类写入仍按原来源广播。

前端据来源区分自己的操作与外部改动：非 ``webui`` 的草稿新建会自动跳到对应集，其余变更弹通知、排队聚焦。
每个用例订阅事件流后发起一次写入，收齐这次写入引起的全部批次再断言来源：除了显式广播的那一批，
事件服务读盘补扫或轮询发现的连带变更（草稿消失、登记变化）同样要带上发起方的来源。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_path, write_quarantine
from server.draft_workflow import DraftContext, DraftWorkflow
from server.error_handlers import register_error_handlers
from server.routers import episode_drafts, files
from server.routers import project_events as project_events_router
from server.services.project.project_events import ProjectEventService
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
# 轮询间隔取服务允许的下限；静默窗口覆盖数个轮询周期，迟到的补扫或轮询批次都会被收进来。
_POLL_SECONDS = 0.1
_QUIET_SECONDS = 0.6


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

    async def batches_until(self, predicate: Callable[[dict], bool]) -> list[dict]:
        """收集批次直到某条变更满足 ``predicate``，再收完其后静默窗口内到达的批次。"""
        collected: list[dict] = []

        async def _pull_until_match() -> None:
            while True:
                data = await self.batches.get()
                collected.append(data)
                if any(predicate(change) for change in data["changes"]):
                    return

        await asyncio.wait_for(_pull_until_match(), timeout=5)
        while True:
            try:
                collected.append(await asyncio.wait_for(self.batches.get(), timeout=_QUIET_SECONDS))
            except TimeoutError:
                return collected


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

    service = ProjectEventService(tmp_path, poll_interval=_POLL_SECONDS)
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
            yield _Env(pm, project_path, client, batches)
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
    response = await env.client.put(
        "/projects/demo/source/novel.txt", content="甲乙丙丁", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 200

    batches = await env.batches_until(lambda change: True)

    assert env.pm.load_project("demo")["whole_source_files"] == [{"source_file": "source/novel.txt"}]
    assert _sources(batches) == {"webui"}


async def test_deleting_an_episode_own_source_broadcasts_as_webui(env: _Env) -> None:
    response = await env.client.delete("/projects/demo/source/episode_1.txt")
    assert response.status_code == 200

    batches = await env.batches_until(_is("episode", "updated"))

    assert env.pm.load_project("demo")["episodes"][0]["source_origin"] == "none"
    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}


async def test_saving_a_new_plain_draft_broadcasts_as_webui(env: _Env) -> None:
    (env.project_path / "drafts" / "episode_1" / "script_plan_segments.json").unlink()

    response = await env.client.put(
        "/projects/demo/drafts/1/script_plan",
        content=json.dumps({"segments": [_SEGMENT]}, ensure_ascii=False),
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 200

    batches = await env.batches_until(_is("draft", "created", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


async def test_deleting_a_plain_draft_broadcasts_as_webui(env: _Env) -> None:
    response = await env.client.delete("/projects/demo/drafts/1/script_plan")
    assert response.status_code == 200

    batches = await env.batches_until(_is("trash", "updated"))

    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- episode_drafts.py


async def test_saving_an_episode_draft_that_stays_a_draft_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

    response = await env.client.put(
        f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}",
        json={"content": {"segments": [{**_SEGMENT, "characters_in_segment": ["李四"]}]}, "base_revision": revision},
    )
    assert response.status_code == 200
    assert response.json()["adopted"] is False

    batches = await env.batches_until(_is("draft", "updated", "episode_1_script_plan"))

    assert _sources(batches) == {"webui"}


async def test_saving_an_episode_draft_that_is_adopted_broadcasts_as_webui(env: _Env) -> None:
    _write_narration_draft(env.project_path, {**_SEGMENT, "characters_in_segment": ["王五"]})
    await env.batches_until(_is("draft", "created"))
    revision = await _draft_revision(env)

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

    response = await env.client.delete(
        f"/projects/demo/episodes/1/drafts/{_DOC_TYPE}", params={"base_revision": revision}
    )
    assert response.status_code == 200

    batches = await env.batches_until(_is("draft", "deleted", "episode_1_script_plan"))

    assert ("trash", "updated", "trash") in _changes(batches)
    assert _sources(batches) == {"webui"}


# ---------------------------------------------------------------- Agent 的同类写入


async def test_agent_draft_writes_keep_their_original_source(env: _Env) -> None:
    """Agent 的草稿命令不设来源：写入、改写与丢弃仍按 filesystem 广播，前端照常提醒创作者。"""
    workflow = DraftWorkflow(DraftContext(project_name="demo", data_root=env.pm.data_root, pm=env.pm))

    opened = await workflow.open(1, _DOC_TYPE)
    created = await env.batches_until(_is("draft", "created"))
    await workflow.patch(1, _DOC_TYPE, {"segments": [{**_SEGMENT, "duration_seconds": 6}]}, opened["revision"])
    updated = await env.batches_until(_is("draft", "updated"))
    reread = await workflow.open(1, _DOC_TYPE)
    await workflow.discard(1, _DOC_TYPE, reread["revision"])
    discarded = await env.batches_until(_is("draft", "deleted"))

    assert ("trash", "updated", "trash") in _changes(discarded)
    assert _sources(created) == _sources(updated) == _sources(discarded) == {"filesystem"}
