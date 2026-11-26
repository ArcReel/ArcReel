import asyncio
import builtins
import io
import os
import tracemalloc
from collections import Counter
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lib.config.resolver import ConfigResolver
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_plan import WorkflowPlan, WorkflowPlanRequest
from lib.workflow.workflow_state import WorkflowBlocker, WorkflowStatus
from server import tool_runtime
from server.agent_runtime import arcreel_mcp
from server.auth import CurrentUserInfo, get_current_user
from server.remote_mcp import ArcApiKeyVerifier, build_remote_mcp_server, mount_remote_mcp
from server.routers import projects
from server.services.project import workflow_planner
from server.services.project.coalesce import RequestCoalescer, application_reads
from tests.fakes import use_project_manager
from tests.integration.lib.workflow.test_workflow_state import _narration_project_with_confirmed_plan
from tests.integration.server.agent_runtime.test_arcreel_mcp import _call


def _app(pm, monkeypatch, db_factory):
    use_project_manager(monkeypatch, pm)
    queue = GenerationQueue(session_factory=db_factory, project_manager=pm)
    monkeypatch.setattr(workflow_planner, "get_generation_queue", lambda: queue)
    monkeypatch.setattr(tool_runtime, "get_generation_queue", lambda: queue)
    monkeypatch.setattr(tool_runtime, "get_project_manager", lambda: pm)
    monkeypatch.setattr(arcreel_mcp, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.include_router(projects.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="test")
    return app


@pytest.mark.parametrize("endpoint", ["workflow-plan", "workflow-status"])
@pytest.mark.parametrize("episode", [1, None])
def test_single_episode_opens_metadata_and_script_once(tmp_path, monkeypatch, db_factory, endpoint, episode):
    pm, _, _ = _narration_project_with_confirmed_plan(tmp_path, write_script=True)
    app = _app(pm, monkeypatch, db_factory)
    counts = Counter()

    def instrument(open_file):
        def counted(path, *args, **kwargs):
            if isinstance(path, (str, Path)) and (name := Path(path).name) in {"episode_1.json", "project.json"}:
                counts[name] += 1
            return open_file(path, *args, **kwargs)

        return counted

    def forbidden(*args, **kwargs):
        pytest.fail("HTTP response revalidated or dumped the domain model")

    with TestClient(app) as client:
        for module in (builtins, io, os):
            monkeypatch.setattr(module, "open", instrument(module.open))
        for model in (WorkflowStatus, WorkflowPlan):
            monkeypatch.setattr(model, "model_dump", forbidden)
            monkeypatch.setattr(model, "model_validate", forbidden)
        url = f"/projects/demo/{endpoint}"
        response = (
            client.post(url, json={"episode_id": episode})
            if endpoint == "workflow-plan"
            else client.get(url, params={"episode": episode} if episode else {})
        )
    assert response.status_code == 200, response.text
    assert counts == {"project.json": 1, "episode_1.json": 1}


class _Arrivals(RequestCoalescer):
    def __init__(self):
        super().__init__(asyncio.get_running_loop())
        self.keys = asyncio.Queue()

    async def _run(self, key, compute):
        self.keys.put_nowait(key)
        return await super()._run(key, compute)


async def test_mixed_hosts_share_two_rounds_and_independent_keys_progress(tmp_path, monkeypatch, db_factory):
    pm, _, _ = _narration_project_with_confirmed_plan(tmp_path, write_script=True)
    app = _app(pm, monkeypatch, db_factory)
    reads = app.state.project_reads = _Arrivals()
    token = application_reads.set(reads)
    try:
        embedded = arcreel_mcp.build_arcreel_mcp_server(project_name="demo", data_root=pm.data_root)["instance"]

        async def verify(token):
            return {"sub": "test"} if token == "arc-test" else None

        remote = build_remote_mcp_server(projects=pm, token_verifier=ArcApiKeyVerifier(verify))
        mount_remote_mcp(app, remote.streamable_http_app())
    finally:
        application_reads.reset(token)

    run = reads.run
    first_key = None
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    active = 0
    peak = 0

    async def observe(key, compute):
        nonlocal first_key
        if first_key is None:
            first_key = key

        async def gated():
            nonlocal calls, active, peak
            calls += 1
            active += 1
            peak = max(peak, active)
            try:
                read = await compute()
                started.set()
                await release.wait()
                read.value.blockers.append(WorkflowBlocker(code="test", path="script", reason="scripts/episode_1.json"))
                return read
            finally:
                active -= 1

        return await run(key, gated if key == first_key else compute)

    monkeypatch.setattr(reads, "run", observe)
    url = "/projects/demo/workflow-plan"
    args = {"episode_id": 1}
    async with (
        remote.session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-test"},
        ) as client,
        streamable_http_client("http://localhost/mcp", http_client=client) as (reader, writer, _),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        async with asyncio.timeout(20):
            first = asyncio.create_task(client.post(url, json=args))
            await started.wait()
            batch = asyncio.gather(
                *[
                    call
                    for _ in range(4)
                    for call in (
                        client.post(url, json=args, headers={"Accept-Language": "en"}),
                        asyncio.to_thread(lambda: asyncio.run(_call(embedded, "get_workflow_plan", args))),
                        session.call_tool("get_workflow_plan", {"project": "demo", **args}),
                    )
                ]
            )
            try:
                keys = [await reads.keys.get() for _ in range(13)]
                assert len(set(keys)) == 1
                await asyncio.to_thread(pm.update_project, "demo", lambda project: project.update(title="Updated"))
                independent, visible = await asyncio.gather(
                    client.post(url, json={**args, "confirmed_request_durations": {"E1S01": 4}}),
                    client.get("/projects/demo/workflow-status", params={"episode": 1}),
                )
                assert independent.status_code == visible.status_code == 200
            finally:
                release.set()
            first_result = await first
            results = await batch
        tracemalloc.start()
        try:
            many = []
            for index in range(14):
                options = {**args, "confirmed_request_durations": {"E1S01": index + 1}}
                if index % 3 == 0:
                    many.append(client.post(url, json=options))
                elif index % 3 == 1:
                    many.append(_call(embedded, "get_workflow_plan", options))
                else:
                    many.append(session.call_tool("get_workflow_plan", {"project": "demo", **options}))
            many.append(client.get("/projects/demo/workflow-status", params={"episode": 1}))
            responses = await asyncio.gather(*many)
            assert all(r.status_code == 200 if isinstance(r, httpx.Response) else not r.isError for r in responses)
            assert tracemalloc.get_traced_memory()[1] < 192 * 1024 * 1024
        finally:
            tracemalloc.stop()
    assert peak == 1
    assert calls == 2
    zh_reason = first_result.json()["blockers"][-1]["reason"]
    assert isinstance(results[0], httpx.Response)
    assert first_result.json()["status"]["project_revision"] != results[0].json()["status"]["project_revision"]
    en_reason = results[0].json()["blockers"][-1]["reason"]
    assert zh_reason != en_reason
    for result in results[1::3] + results[2::3]:
        assert not result.isError
        assert result.structuredContent["workflow_plan"]["blockers"][-1]["reason"] == "scripts/episode_1.json"


async def test_read_keys_isolate_users_windows_and_injected_sources(tmp_path, monkeypatch, db_factory):
    pm, _, _ = _narration_project_with_confirmed_plan(tmp_path, write_script=True)
    pm.add_episode("demo", 2, "第二集", "scripts/episode_2.json")
    app = _app(pm, monkeypatch, db_factory)
    reads = app.state.project_reads = _Arrivals()
    user = CurrentUserInfo(id="u1", sub="first")
    app.dependency_overrides[get_current_user] = lambda: user
    durations = {"E1S01": 4, "E1S02": 5}
    payload = {"episode_id": 1, "confirmed_request_durations": durations}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = "/projects/demo/workflow-plan"

        async def plan_key(body):
            result = await client.post(url, json=body)
            assert result.status_code == 200
            return await reads.keys.get()

        key = await plan_key(payload)
        reversed_key = await plan_key({**payload, "confirmed_request_durations": dict(reversed(durations.items()))})
        assert key == reversed_key
        assert await plan_key({"episode_id": 1}) != key
        assert await plan_key({"confirmed_request_durations": durations}) != key
        user = CurrentUserInfo(id="u2", sub="second")
        assert await plan_key(payload) != key
        user = CurrentUserInfo(id="u1", sub="first", role="viewer")
        assert await plan_key(payload) != key
        windows = await asyncio.gather(
            *[client.get("/projects/demo/workflow-status/episodes", params={"episodes": number}) for number in (1, 2)]
        )
        assert [[ep["episode"] for ep in result.json()["episodes"]] for result in windows] == [[1], [2]]
        assert await reads.keys.get() != await reads.keys.get()

    async def injected_key(manager, **kwargs):
        planner = workflow_planner.WorkflowPlanner(manager, reads)
        await planner.get_plan("demo", WorkflowPlanRequest(**payload), user_id="u1", **kwargs)
        return await reads.keys.get()

    assert await injected_key(pm) == key
    assert await injected_key(pm, queue=GenerationQueue(session_factory=db_factory, project_manager=pm)) != key
    assert await injected_key(pm, config_resolver=ConfigResolver(db_factory)) != key
    assert await injected_key(ProjectManager(pm.data_root)) != key
