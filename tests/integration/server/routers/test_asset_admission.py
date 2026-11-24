from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.user import User
from lib.generation.asset_admission import AssetResourceBusy, AssetTarget, require_asset_idle
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import assets, generate
from server.tool_runtime import CallerContext
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import observe_file_lock_contention, use_assets_router_sessions, use_project_manager
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


@pytest.mark.parametrize(("task_type", "status"), [("character", 409), ("image_edit", 409), ("voice_sample", 200)])
async def test_export_during_asset_task(assets_env, db_factory, task_type, status):
    client, pm = assets_env["client"], assets_env["pm"]
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "Héro", "new", "")
    target = client.post(
        "/api/v1/assets",
        data={"type": "character", "name": "Héro", "description": "old"},
        files={"image": ("old.png", b"old", "image/png")},
    ).json()["asset"]
    queue = GenerationQueue(project_manager=pm, session_factory=db_factory)
    await queue.enqueue_task(
        project_name="demo",
        task_type=task_type,
        resource_type="character" if task_type == "image_edit" else None,
        media_type="audio" if task_type == "voice_sample" else "image",
        resource_id="Héro",
        provider_id="test",
    )
    response = await asyncio.to_thread(
        client.post,
        "/api/v1/assets/from-project",
        json={"project_name": "demo", "resource_type": "character", "resource_id": "He\u0301ro", "overwrite": True},
    )
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["code"] == "asset_resource_busy"
        assert client.get(f"/api/v1/assets/{target['id']}").json()["asset"] == target
        assert (pm.data_root / target["image_path"]).read_bytes() == b"old"


@pytest.fixture
def asset_project(tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "Héro", "hero", "")
    pm.update_project(
        "demo",
        lambda project: project["characters"]["Héro"].update(
            derivatives={"Été": {"description": "summer"}, "Winter": {"description": "winter"}}
        ),
    )
    return pm


@pytest.mark.parametrize(
    ("kind", "resource_type", "resource", "busy_dimensions"),
    [
        ("character", None, "He\u0301ro", {"image", "derivative_image", "whole_asset"}),
        ("image_edit", "character", "He\u0301ro", {"image", "derivative_image", "whole_asset"}),
        ("character_derivative", None, "He\u0301ro/E\u0301te\u0301", {"image", "derivative_image", "whole_asset"}),
        ("image_edit", "character_derivative", "Héro/E\u0301te\u0301", {"image", "derivative_image", "whole_asset"}),
        ("character_derivative", None, "Héro/Winter", {"image", "whole_asset"}),
        ("voice_sample", None, "He\u0301ro", {"voice", "whole_asset"}),
    ],
)
async def test_cross_user_asset_occupancy(asset_project, db_factory, kind, resource_type, resource, busy_dimensions):
    async with db_factory() as session:
        session.add(User(id="another", username="another"))
        await session.commit()
    queue = GenerationQueue(session_factory=db_factory, project_manager=asset_project)
    await queue.enqueue_task(
        project_name="demo",
        task_type=kind,
        resource_type=resource_type,
        resource_id=resource,
        media_type="audio" if kind == "voice_sample" else "image",
        provider_id="test",
        user_id="another",
    )
    async with db_factory() as session:
        for dimension in ("image", "derivative_image", "voice", "whole_asset"):
            target = AssetTarget("character", "Héro", dimension, "Été" if dimension == "derivative_image" else None)
            if dimension in busy_dimensions:
                with pytest.raises(AssetResourceBusy):
                    await require_asset_idle(session, "demo", target)
            else:
                await require_asset_idle(session, "demo", target)


@pytest.mark.parametrize(
    ("first", "second", "statuses"),
    [
        ("export", "generate", (200, 200)),
        ("generate", "export", (200, 409)),
        ("edit", "generate", (200, 409)),
    ],
)
async def test_asset_admission_contention(asset_project, file_db_factory, monkeypatch, first, second, statuses):
    asset_project.add_project_scene("demo", "Harbor", "a port")
    use_project_manager(monkeypatch, asset_project)
    committing = asyncio.Event()
    contention = observe_file_lock_contention(monkeypatch)

    class PausedCommitSession(AsyncSession):
        async def commit(self):
            if not committing.is_set():
                committing.set()
                await asyncio.wait_for(contention.wait(), 15)
            await super().commit()

    factory = async_sessionmaker(file_db_factory.kw["bind"], class_=PausedCommitSession, expire_on_commit=False)
    use_assets_router_sessions(monkeypatch, factory)
    queue = GenerationQueue(session_factory=factory, project_manager=asset_project)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: queue)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    for router in (assets.router, generate.router):
        app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:

        async def submit(operation):
            if operation == "edit":
                await queue.enqueue_task(
                    project_name="demo",
                    task_type="image_edit",
                    resource_type="scene",
                    resource_id="Harbor",
                    media_type="image",
                    provider_id="test",
                )
                return 200
            if operation == "export":
                response = await http.post(
                    "/api/v1/assets/from-project",
                    json={"project_name": "demo", "resource_type": "scene", "resource_id": "Harbor"},
                )
            else:
                response = await http.post("/api/v1/projects/demo/generate/scene/Harbor")
            if response.status_code == 409:
                assert response.json()["code"] == "asset_resource_busy"
            return response.status_code

        async with asyncio.TaskGroup() as group:
            initial = group.create_task(submit(first))
            await asyncio.wait_for(committing.wait(), 15)
            contender = group.create_task(submit(second))
        assert (initial.result(), contender.result()) == statuses
        exported = (await http.get("/api/v1/assets")).json()["items"]
        assert [row["name"] for row in exported] == (["Harbor"] if first == "export" else [])


async def test_agent_asset_generation_reports_conflicting_edit(asset_project, file_db_factory):
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=asset_project)
    await queue.acquire_or_renew_worker_lease(name="default", owner_id="test", ttl_seconds=60)
    await queue.enqueue_task(
        project_name="demo",
        task_type="image_edit",
        resource_type="character",
        resource_id="Héro",
        media_type="image",
        provider_id="test",
    )
    harness = ToolHarness(
        "demo",
        asset_project.data_root,
        asset_project,
        queue=queue,
        caller=CallerContext(user_id="default", source="mcp"),
    )
    outcome = await run_declared_tool("generate_assets", harness, {"type": "character", "names": ["Héro"]})
    assert outcome.problem is not None
    assert outcome.problem.code == "asset_resource_busy"
