from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.control_plane import ProviderAssetDetail
from lib.asset_library.links import record_imported_link
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.user import User
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.task_repo import TaskRepository
from lib.generation.asset_admission import AssetResourceBusy, AssetTarget, require_asset_idle
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import batch_enqueue_and_wait
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import asset_sheets, assets, characters, generate
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload
from server.tool_runtime import CallerContext
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import (
    observe_file_lock_contention,
    use_assets_router_sessions,
    use_generation_queue,
    use_project_manager,
)
from tests.integration.server.agent_tool_support import ToolHarness, read_generation_result, run_declared_tool


@pytest.mark.parametrize(
    ("stored_project", "requested_project", "busy"),
    [
        ("demo", " demo ", True),
        ("\tdemo\u3000", "demo", True),
        ("demo-other", "demo", False),
    ],
)
async def test_asset_occupancy_compares_equivalent_project_names(
    file_db_factory, stored_project, requested_project, busy
):
    async with file_db_factory() as session:
        await TaskRepository(session).enqueue(
            project_name=stored_project, task_type="character", media_type="image", resource_id="Alice"
        )

    async with file_db_factory() as session:
        target = AssetTarget("character", "Alice", "image")
        if busy:
            with pytest.raises(AssetResourceBusy):
                await require_asset_idle(session, requested_project, target)
        else:
            await require_asset_idle(session, requested_project, target)


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


@pytest.fixture
async def apply_client(asset_project, file_db_factory, monkeypatch):
    use_project_manager(monkeypatch, asset_project)
    use_assets_router_sessions(monkeypatch, file_db_factory)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    for router in (assets.router, characters.router, generate.router):
        app.include_router(router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _library_sheet(client, name, content):
    response = await client.post(
        "/api/v1/assets",
        data={"type": "character", "name": name, "description": "library description"},
        files={"image": ("sheet.png", content, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset"]


async def _apply_library_sheets(client, policy, *asset_ids):
    return await client.post(
        "/api/v1/assets/apply-to-project",
        json={"asset_ids": asset_ids, "target_project": "demo", "conflict_policy": policy},
        headers={"Accept-Language": "en"},
    )


async def _project_links(factory):
    async with factory() as session:
        rows = await session.scalars(
            select(ProviderAssetLink).where(ProviderAssetLink.project_name == "demo").order_by(ProviderAssetLink.id)
        )
        return [tuple(getattr(row, col.key) for col in row.__table__.columns) for row in rows]


async def test_apply_rechecks_new_overwrite_after_concurrent_creation_and_admission(
    apply_client,
    asset_project,
    file_db_factory,
    monkeypatch,
):
    archer = await _library_sheet(apply_client, "Archer", b"incoming archer")
    mage = await _library_sheet(apply_client, "Mage", b"incoming mage")
    assert "Archer" not in asset_project.load_project("demo")["characters"]

    reading_inputs = asyncio.Event()
    release_apply = asyncio.Event()
    committing = asyncio.Event()
    release_enqueue = asyncio.Event()
    contended = observe_file_lock_contention(monkeypatch)
    ensure_global_image = assets.ensure_global_image

    async def hold_library_image(data_root, path):
        if path == archer["image_path"]:
            reading_inputs.set()
            await asyncio.wait_for(release_apply.wait(), 10)
        return await ensure_global_image(data_root, path)

    monkeypatch.setattr(assets, "ensure_global_image", hold_library_image)

    class PausedCommitSession(AsyncSession):
        async def commit(self):
            committing.set()
            await asyncio.wait_for(release_enqueue.wait(), 10)
            await super().commit()

    factory = async_sessionmaker(file_db_factory.kw["bind"], class_=PausedCommitSession, expire_on_commit=False)
    queue = GenerationQueue(session_factory=factory, project_manager=asset_project)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: queue)
    project_dir = asset_project.get_project_path("demo")
    sheet = project_dir / "characters" / "Archer.png"
    versions = VersionManager(project_dir)

    async with asyncio.TaskGroup() as group:
        applying = group.create_task(_apply_library_sheets(apply_client, "overwrite", archer["id"], mage["id"]))
        await asyncio.wait_for(reading_inputs.wait(), 10)
        created = await apply_client.post(
            "/api/v1/projects/demo/characters", json={"name": "Archer", "description": "project description"}
        )
        assert created.status_code == 200, created.text
        content_version = await asyncio.to_thread(
            install_manual_asset_sheet_upload,
            project_manager=asset_project,
            project_name="demo",
            asset_type="character",
            name="Archer",
            sheet_path="characters/Archer.png",
            content=b"original",
            original_filename="Archer.png",
        )
        assert content_version is not None
        entry = asset_project.load_project("demo")["characters"]["Archer"]
        history = versions.get_versions("characters", "Archer")
        async with file_db_factory() as session:
            account = await AssetLibraryRepository(session).create_account(
                site="volcengine",
                name="test",
                region="cn-beijing",
                project_name="film",
                access_key_id="TEST-ADMISSION-AK",
                secret_access_key="TEST-ADMISSION-SK",
            )
            await record_imported_link(
                session,
                project_name="demo",
                content_version=content_version,
                account=account,
                asset=ProviderAssetDetail(
                    id="existing-archer",
                    name="Archer",
                    asset_type="Image",
                    status="active",
                    group_id="group-1",
                    error_code=None,
                    error_message=None,
                ),
            )
            await session.commit()
        original_links = await _project_links(file_db_factory)
        assert len(original_links) == 1
        queued = group.create_task(apply_client.post("/api/v1/projects/demo/generate/character/Archer"))
        await asyncio.wait_for(committing.wait(), 10)
        release_apply.set()
        try:
            await asyncio.wait_for(contended.wait(), 5)
        finally:
            release_enqueue.set()

    enqueue_response = queued.result()
    assert enqueue_response.status_code == 200, enqueue_response.text
    assert enqueue_response.json()["task_id"]
    response = applying.result()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["succeeded"] == [{"id": mage["id"], "name": "Mage"}]
    assert result["skipped"] == []
    assert [(row["id"], row["reason"]) for row in result["failed"]] == [(archer["id"], "resource_busy")]
    assert asset_project.load_project("demo")["characters"]["Archer"] == entry
    assert sheet.read_bytes() == b"original"
    assert versions.get_versions("characters", "Archer") == history
    assert await _project_links(file_db_factory) == original_links
    assert (project_dir / "characters" / "Mage.png").read_bytes() == b"incoming mage"
    assert versions.get_current_version("characters", "Mage") is not None


@pytest.mark.parametrize(
    ("policy", "result_key", "name"),
    [("overwrite", "failed", None), ("skip", "skipped", "Héro"), ("rename", "succeeded", "Héro (2)")],
)
async def test_apply_conflict_policies_during_voice_sample(
    apply_client,
    asset_project,
    file_db_factory,
    policy,
    result_key,
    name,
):
    asset = await _library_sheet(apply_client, "Héro", b"library")
    path = asset_project.get_project_path("demo")
    asset_project.install_asset_sheet_bytes("character", "demo", "Héro", "characters/Héro.png", b"original")
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=asset_project)
    await queue.enqueue_task(
        project_name="demo",
        task_type="voice_sample",
        media_type="audio",
        resource_id="He\u0301ro",
        provider_id="test",
    )

    before = asset_project.load_project("demo")
    response = await _apply_library_sheets(apply_client, policy, asset["id"])

    assert response.status_code == 200, response.text
    result = response.json()
    assert {key for key, rows in result.items() if rows} == {result_key}
    if policy == "overwrite":
        assert asset_project.load_project("demo") == before
        [failure] = result["failed"]
        assert failure["id"] == asset["id"]
        assert failure["reason"] == "resource_busy"
        assert (
            failure["message"]
            == "The project asset has a queued or running task and cannot be overwritten. Try again when the task finishes."
        )
    else:
        assert result[result_key] == [{"id": asset["id"], "name": name}]
    assert (path / "characters" / "Héro.png").read_bytes() == b"original"
    if policy == "rename":
        assert (path / "characters" / "Héro (2).png").read_bytes() == b"library"


async def test_same_batch_derivative_queues_with_parent_while_independent_derivative_returns_409(
    asset_project, file_db_factory, monkeypatch
):
    use_project_manager(monkeypatch, asset_project)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=asset_project)
    await queue.acquire_or_renew_worker_lease(name="default", owner_id="test", ttl_seconds=60)
    use_generation_queue(monkeypatch, queue)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(asset_sheets.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        batch_response = await http.post("/api/v1/projects/demo/asset-sheets/batch", json={"asset_type": "character"})
        assert batch_response.status_code == 200, batch_response.text
        members = {member["unit_id"]: member for member in batch_response.json()["members"]}
        parent_task_id = members["character/Héro"]["task_id"]
        ete_task_id = members["character/Héro/Été"]["task_id"]
        assert parent_task_id
        assert ete_task_id
        ete_task = await queue.get_task(ete_task_id)
        assert ete_task is not None
        assert ete_task["dependency_task_id"] == parent_task_id

        await asyncio.to_thread(
            install_manual_asset_sheet_upload,
            project_manager=asset_project,
            project_name="demo",
            asset_type="character",
            name="Héro",
            sheet_path="characters/Héro.png",
            content=b"existing",
            original_filename="Héro.png",
        )
        asset_project.update_project(
            "demo",
            lambda project: project["characters"]["Héro"]["derivatives"].update(Autumn={"description": "autumn"}),
        )
        harness = ToolHarness(
            "demo",
            asset_project.data_root,
            asset_project,
            queue=queue,
            caller=CallerContext(user_id="default", source="mcp"),
        )
        independent = await run_declared_tool(
            "generate_assets", harness, {"type": "character", "names": ["Héro/Autumn"]}
        )
        assert independent.problem is not None
        assert independent.problem.code == "asset_resource_busy"


async def test_web_asset_batch_reports_busy_target_and_enqueues_idle_assets(
    asset_project, file_db_factory, monkeypatch
):
    asset_project.update_project(
        "demo",
        lambda project: project.update(
            characters={name: {"description": name} for name in ("Before", "Héro", "After")}
        ),
    )
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
    use_project_manager(monkeypatch, asset_project)
    use_generation_queue(monkeypatch, queue)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(asset_sheets.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/v1/projects/demo/asset-sheets/batch", json={"asset_type": "character"})

    assert response.status_code == 200, response.text
    members = {member["unit_id"]: member for member in response.json()["members"]}
    assert {unit_id for unit_id, member in members.items() if member["task_id"]} == {
        "character/Before",
        "character/After",
    }
    assert members["character/Héro"]["status"] == "failed"
    assert members["character/Héro"]["problem_code"] == "asset_resource_busy"


async def test_embedded_asset_batch_keeps_busy_reason_when_read_again(asset_project, file_db_factory):
    asset_project.add_project_character("demo", "Idle", "idle", "")
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=asset_project)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test", ttl_seconds=60)
    await queue.enqueue_task(
        project_name="demo",
        task_type="image_edit",
        resource_type="character",
        resource_id="Héro",
        media_type="image",
        provider_id="test",
    )
    harness = ToolHarness("demo", asset_project.data_root, asset_project, queue=queue)

    async def settle_batch(*, project_name, specs, batch_id, queue, user_id):
        enqueued = asyncio.Event()
        async with asyncio.TaskGroup() as group:
            waiting = group.create_task(
                batch_enqueue_and_wait(
                    project_name=project_name,
                    specs=specs,
                    batch_id=batch_id,
                    queue=queue,
                    user_id=user_id,
                    on_enqueued=enqueued.set,
                )
            )
            await asyncio.wait_for(enqueued.wait(), timeout=5)
            await queue.cancel_generation_batch(project_name=project_name, batch_id=batch_id, user_id=user_id)
        return waiting.result()

    submitted = await run_declared_tool(
        "generate_assets", harness, {"type": "character", "names": ["Héro", "Idle"]}, batch_waiter=settle_batch
    )
    result = read_generation_result(submitted)
    busy = next(item.problem for item in result.items if item.unit_id == "character/Héro")
    assert busy is not None
    assert busy.code == "asset_resource_busy"

    reread = await run_declared_tool("get_generation_batch", harness, {"batch_id": submitted.value["batch_id"]})
    assert reread.value is not None
    assert reread.value.done
    assert next(member.problem for member in reread.value.members if member.unit_id == "character/Héro") == busy
