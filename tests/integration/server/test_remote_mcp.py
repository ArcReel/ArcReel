from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lib.api_errors import ConflictError
from lib.artifact_activation import register_current_artifact_if_provable
from lib.artifact_manifest import ArtifactKey
from lib.draft_quarantine import QUARANTINE_KIND_DRAMA_STEP1, read_quarantine
from lib.generation_batch import GenerationBatchRequestSnapshot
from lib.generation_queue import GenerationQueue
from lib.generation_queue_client import submit_generation_batch
from lib.generation_result import GenerationSelectionMode
from lib.generation_worker import CapacityTable, GenerationWorker
from lib.project_manager import ProjectManager
from lib.project_migration_failure import MIGRATION_FAILURE_CODE, record_migration_failure
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import WorkflowStatus
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS
from server.agent_runtime.sdk_tools.text_generation import generate_episode_script_tool
from server.auth import create_download_token, create_token
from server.media_tools.assets import generate_assets_tool, list_pending_assets_tool
from server.media_tools.context import ToolContext
from server.media_tools.grid import generate_grid_tool
from server.media_tools.image_edits import edit_images_tool
from server.media_tools.narration_audio import generate_narration_audio_tool
from server.media_tools.storyboards import generate_storyboards_tool
from server.media_tools.videos import generate_videos_tool
from server.remote_mcp import ArcApiKeyVerifier, RemoteMCPHost, build_remote_mcp_server
from server.tool_runtime import Services
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import _call


class _Planner:
    async def get_plan(self, project_name: str, request: WorkflowPlanRequest, **_caller_scope):
        assert project_name == "demo"
        status = WorkflowStatus.model_validate(
            {
                "project_revision": "sha256-v1:project",
                "source_revision": None,
                "project": {"content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": False},
                "target": {
                    "episode": request.episode,
                    "script": "scripts/episode_1.json",
                    "script_filename": "episode_1.json",
                    "source": "source/episode_1.txt",
                },
                "state": "FINAL_SCRIPT",
                "blockers": [],
                "gates": {"step1_review": {"state": "not_applicable", "revision": None}},
                "artifacts": {
                    "asset_inventory": {"state": "not_applicable"},
                    "asset_sheets": {},
                    "step1": {"state": "not_applicable"},
                    "script": {"state": "missing"},
                    "storyboards": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                    "videos": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                    "audio": {"state": "not_applicable", "current_ids": [], "stale_ids": [], "missing_ids": []},
                },
                "next_action": {"type": "generate_script", "reason": "script missing"},
            }
        )
        return build_workflow_plan(status, narration_delivery=request.narration_delivery)


class _Capabilities:
    async def video_capabilities_for_project(self, project: dict, *, capability=None) -> dict:
        return {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}

    async def resolve_image_backend(self, _project: dict, _payload: object, *, capability: str) -> None:
        assert capability == "i2i"
        raise ValueError("image capability unavailable")


class _AvailableImageCapabilities(_Capabilities):
    async def resolve_image_backend(self, _project: dict, _payload: object, *, capability: str) -> None:
        assert capability == "i2i"


@pytest.fixture
def remote_projects(tmp_path: Path) -> ProjectManager:
    projects_root = tmp_path / "projects"
    manager = ProjectManager(projects_root)
    manager.create_project("demo", content_mode="drama")
    manager.create_project_metadata("demo", "Demo", "", "drama")
    project_dir = projects_root / "demo"
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("第一集原文", encoding="utf-8")
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "scripts" / "episode_1.json").write_text('{"episode":1,"scenes":[]}', encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_normalized_script.json").write_text(
        '{"title":"第一集","scenes":[{"scene_id":"E1S01","duration_seconds":4,'
        '"segment_break":false,"characters_in_scene":[],"scenes":[],"props":[],'
        '"scene_description":"山门前。","utterances":[],"source_text":"第一集原文"}]}',
        encoding="utf-8",
    )
    (projects_root / "empty").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.json").write_text("{}", encoding="utf-8")
    (projects_root / "escape").symlink_to(outside, target_is_directory=True)

    return manager


@pytest.fixture
def remote_server(remote_projects: ProjectManager):

    async def verify_api_key(token: str):
        return {"sub": "apikey:test", "via": "apikey"} if token == "arc-valid" else None

    services = Services(projects=remote_projects, workflow_planner=_Planner(), capabilities=_Capabilities())
    return build_remote_mcp_server(
        projects=remote_projects, services=services, token_verifier=ArcApiKeyVerifier(verify_api_key)
    )


@pytest.fixture
def remote_batch_server(remote_projects: ProjectManager, db_factory):
    async def verify_api_key(token: str):
        subjects = {
            "arc-valid": "apikey:test",
            "arc-user-a": "apikey:user-a",
            "arc-user-b": "apikey:user-b",
        }
        return {"sub": subjects[token], "via": "apikey"} if token in subjects else None

    queue = GenerationQueue(session_factory=db_factory, project_manager=remote_projects)
    services = Services(
        projects=remote_projects,
        workflow_planner=_Planner(),
        capabilities=_AvailableImageCapabilities(),
        queue=queue,
    )
    return (
        build_remote_mcp_server(
            projects=remote_projects,
            services=services,
            token_verifier=ArcApiKeyVerifier(verify_api_key),
        ),
        queue,
    )


def _mounted(server) -> FastAPI:
    app = FastAPI()
    app.mount("/mcp", server.streamable_http_app())
    return app


def test_remote_mcp_accepts_source_upload_sized_requests(remote_server) -> None:
    assert remote_server.settings.max_request_body_size > 300 * 1024 * 1024


def test_remote_mcp_rejects_mismatched_projects_roots(tmp_path: Path) -> None:
    projects = ProjectManager(tmp_path / "scope-projects")
    services = Services(
        projects=ProjectManager(tmp_path / "service-projects"),
        workflow_planner=_Planner(),
        capabilities=_Capabilities(),
    )

    with pytest.raises(ValueError, match="同一项目根"):
        build_remote_mcp_server(projects=projects, services=services)


async def _post_initialize(app: FastAPI, token: str | None = None) -> httpx.Response:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost", follow_redirects=True
    ) as client:
        return await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )


@pytest.mark.parametrize("auth_enabled", ["true", "false"])
async def test_remote_mcp_always_rejects_anonymous(remote_server, monkeypatch, auth_enabled: str) -> None:
    monkeypatch.setenv("AUTH_ENABLED", auth_enabled)

    response = await _post_initialize(_mounted(remote_server))

    assert response.status_code == 401


@pytest.mark.parametrize(
    "token_factory", [lambda: create_token("admin"), lambda: create_download_token("admin", "demo")]
)
async def test_remote_mcp_rejects_non_api_key_bearer_tokens(remote_server, token_factory) -> None:
    response = await _post_initialize(_mounted(remote_server), token_factory())

    assert response.status_code == 401


async def test_remote_mcp_returns_typed_workflow_plan_and_rejects_bad_project(
    remote_server, remote_projects: ProjectManager
) -> None:
    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool("get_workflow_plan", {"project": " demo ", "episode": 1})
                    capabilities = await session.call_tool("get_video_capabilities", {"project": "demo"})
                    patched = await session.call_tool(
                        "patch_project", {"project": "demo", "overview": {"synopsis": "远程更新"}}
                    )
                    project_content = await session.call_tool("get_project_content", {"project": "demo"})
                    source_files = await session.call_tool("list_source_files", {"project": "demo"})
                    source_text = await session.call_tool(
                        "get_source_text", {"project": "demo", "path": "source/episode_1.txt"}
                    )
                    script = await session.call_tool(
                        "get_episode_script", {"project": "demo", "script": "episode_1.json"}
                    )
                    step1 = await session.call_tool("get_step1_content", {"project": "demo", "episode": 1})
                    project_files = await session.call_tool("list_project_files", {"project": "demo"})
                    project_file = await session.call_tool(
                        "read_project_file", {"project": "demo", "path": "project.json"}
                    )
                    missing = await session.call_tool("get_workflow_plan", {"episode": 1})
                    traversal = await session.call_tool("get_workflow_plan", {"project": "../demo", "episode": 1})
                    nonexistent = await session.call_tool("get_workflow_plan", {"project": "absent", "episode": 1})
                    empty = await session.call_tool("get_workflow_plan", {"project": "empty", "episode": 1})
                    escape = await session.call_tool("get_workflow_plan", {"project": "escape", "episode": 1})

    assert not result.isError
    migrated = {
        "plan_episodes",
        "reset_episode_planning",
        "patch_project",
        "patch_episode_meta",
        "rename_asset",
        "retry_project_migration",
        "complete_asset_inventory",
        "complete_step1_rebuild",
    }
    readers = {
        "get_project_content",
        "list_source_files",
        "get_source_text",
        "get_episode_script",
        "get_step1_content",
        "list_project_files",
        "read_project_file",
    }
    drafts = {"open_draft", "patch_draft", "promote_draft", "discard_draft"}
    text_and_script = {
        "generate_episode_script",
        "generate_step1",
        "confirm_script_review",
        "patch_episode_script",
    }
    batches = {"get_generation_batch", "cancel_generation_batch"}
    retired = {
        "normalize_drama_script",
        "split_narration_segments",
        "split_reference_video_units",
        "insert_segment",
        "remove_segment",
        "split_segment",
        "open_step1_for_edit",
        "validate_and_promote_draft",
        "get_episode_script_revision",
        "generate_video_episode",
        "generate_video_scene",
        "generate_video_all",
        "generate_video_selected",
    }
    listed = {tool.name: tool for tool in tools.tools}
    assert set(listed) == set(ARCREEL_MCP_TOOL_IDS)
    assert migrated | readers | drafts | text_and_script | batches <= listed.keys()
    assert retired.isdisjoint(listed)
    assert all(
        "project" in listed[name].inputSchema["required"]
        for name in migrated | readers | drafts | text_and_script | batches
    )
    media_ctx = ToolContext("demo", remote_projects.projects_root, pm=remote_projects)
    definitions = {
        definition.name: definition
        for definition in (
            list_pending_assets_tool(media_ctx),
            generate_assets_tool(media_ctx),
            generate_storyboards_tool(media_ctx),
            edit_images_tool(media_ctx),
            generate_grid_tool(media_ctx),
            generate_videos_tool(media_ctx),
            generate_narration_audio_tool(media_ctx),
        )
    }
    remote_batch_tools = {"generate_assets", "generate_storyboards", "edit_images", "generate_grid"}
    for name, definition in definitions.items():
        remote_schema = listed[name].inputSchema
        assert remote_schema["properties"]["project"]["type"] == "string"
        assert {key: value for key, value in remote_schema["properties"].items() if key != "project"} == (
            definition.input_schema["properties"]
        )
        assert remote_schema["required"] == ["project", *definition.input_schema.get("required", [])]
        assert remote_schema["additionalProperties"] is False
        assert {
            key: value
            for key, value in remote_schema.items()
            if key not in {"properties", "required", "additionalProperties"}
        } == {key: value for key, value in definition.input_schema.items() if key not in {"properties", "required"}}
    for name in remote_batch_tools:
        remote_description = listed[name].description
        assert remote_description.startswith(definitions[name].description)
        assert "durable admission" in remote_description
        assert "durable generation_batch" in remote_description
        assert "immediately" in remote_description
        assert "poll_after_seconds" in remote_description
        assert "get_generation_batch" in remote_description
        assert "done=true" in remote_description
    assert all(listed[name].inputSchema["properties"]["episode"]["minimum"] == 1 for name in drafts)
    patch_schema = listed["patch_episode_script"].inputSchema
    operations_schema = patch_schema["properties"]["operations"]
    operation_defs = patch_schema["$defs"]
    operation_branches = [
        operation_defs[branch["$ref"].rsplit("/", 1)[1]] for branch in operations_schema["items"]["oneOf"]
    ]
    assert operations_schema["minItems"] == 1
    assert operations_schema["items"]["discriminator"]["propertyName"] == "op"
    assert {branch["properties"]["op"]["const"] for branch in operation_branches} == {
        "update",
        "insert",
        "remove",
        "split",
    }
    assert all(branch["additionalProperties"] is False for branch in operation_branches)
    video_properties = listed["generate_videos"].inputSchema["properties"]
    assert "resume" not in video_properties
    assert "confirmed_request_duration_seconds" in video_properties
    assert "confirmed_request_durations" in video_properties
    assert {"narration_voice", "narration_speed", "narration_volume"}.isdisjoint(video_properties)
    target_schema = video_properties["target"]
    target_defs = {branch["properties"]["scope"]["const"]: branch for branch in target_schema["oneOf"]}
    assert set(target_defs) == {"episode", "scene", "all", "selected"}
    assert target_defs["episode"]["required"] == ["scope", "episode"]
    assert target_defs["scene"]["required"] == ["scope", "ids"]
    assert target_defs["scene"]["properties"]["ids"]["maxItems"] == 1
    assert target_defs["selected"]["required"] == ["scope", "ids"]
    assert target_defs["selected"]["properties"]["ids"]["minItems"] == 1
    assert target_defs["all"]["required"] == ["scope"]
    assert all(definition["additionalProperties"] is False for definition in target_defs.values())
    assert "base_revision" in listed["discard_draft"].inputSchema["required"]
    narration_description = listed["generate_narration_audio"].description
    assert "remote MCP" in narration_description
    assert "get_generation_batch" in narration_description
    assert "poll_after_seconds" in narration_description
    assert result.structuredContent is not None
    assert result.structuredContent["workflow_plan"]["status"]["target"]["episode"] == 1
    assert capabilities.structuredContent == {
        "video_capabilities": {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}
    }
    assert patched.structuredContent is not None
    assert patched.structuredContent["project_patch"]["operation"] == "overview"
    for content_result in (
        project_content,
        source_files,
        source_text,
        script,
        step1,
        project_files,
        project_file,
    ):
        assert not content_result.isError
        assert content_result.structuredContent is not None
        assert next(iter(content_result.structuredContent.values()))["revision"].startswith("sha256-v1:")
    assert missing.isError
    assert traversal.isError
    assert nonexistent.isError
    assert empty.isError
    assert escape.isError


async def test_remote_grid_list_only_returns_preview_without_a_batch(
    remote_server, remote_projects: ProjectManager
) -> None:
    project = remote_projects.load_project("demo")
    project["grid_storyboard"] = True
    remote_projects.save_project("demo", project)

    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "generate_grid",
                        {"project": "demo", "script": "episode_1.json", "list_only": True},
                    )

    description = next(tool.description for tool in tools.tools if tool.name == "generate_grid")
    assert description is not None
    assert "generation submissions" in description
    assert "list_only=true, the preview returns immediately without a generation_batch; do not poll" in description
    assert not result.isError
    assert result.structuredContent is not None
    assert set(result.structuredContent) == {"generate_grid"}
    assert isinstance(result.structuredContent["generate_grid"], str)


async def test_media_errors_are_typed_in_embedded_and_remote_hosts(
    remote_server, remote_projects: ProjectManager
) -> None:
    definition = generate_assets_tool(ToolContext("demo", remote_projects.projects_root, pm=remote_projects))
    embedded = await definition.invoke({"names": ["张三"]})

    assert embedded.problem is not None
    assert embedded.problem.code == "invalid_request"

    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    remote = await session.call_tool("generate_assets", {"project": "demo", "names": ["张三"]})

    assert remote.isError
    assert remote.structuredContent == {"problem": embedded.problem.model_dump(mode="json")}


async def test_remote_media_runtime_validates_shared_schema_and_forwards_nested_instruction(
    remote_batch_server, remote_projects: ProjectManager
) -> None:
    """远程 adapter 按共享 schema 校验，并把嵌套编辑指令完整转发给 handler。"""
    remote_batch_server, queue = remote_batch_server
    instruction = "把头发改成红色，保留原有发型"
    args = {
        "resource_type": "character",
        "edits": [{"id": "张三", "instruction": instruction}],
    }
    project = remote_projects.load_project("demo")
    project["characters"] = {
        "张三": {"description": "黑衣剑客", "character_sheet": "characters/zhangsan.png"},
    }
    remote_projects.save_project("demo", project)
    project_dir = remote_projects.get_project_path("demo")
    (project_dir / "characters").mkdir(exist_ok=True)
    (project_dir / "characters" / "zhangsan.png").write_bytes(b"png")
    assert register_current_artifact_if_provable(project_dir, ArtifactKey.asset_sheet("character", "张三"))
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)

    app = _mounted(remote_batch_server)
    async with remote_batch_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "edit_images",
                        {"project": "demo", **args},
                    )
                    invalid = await session.call_tool(
                        "generate_assets",
                        {"project": "demo", "type": "not-an-asset-type"},
                    )

    assert not result.isError
    assert result.structuredContent is not None
    members = result.structuredContent["generation_batch"]["members"]
    assert [(member["unit_id"], member["status"]) for member in members] == [("张三", "queued")]
    tasks = (await queue.list_tasks(project_name="demo", task_type="image_edit"))["items"]
    assert len(tasks) == 1
    assert tasks[0]["payload"]["prompt"] == instruction

    assert invalid.isError
    assert invalid.structuredContent is not None
    assert invalid.structuredContent["problem"]["code"] == "invalid_request"


async def test_remote_media_submission_returns_complete_durable_batch_and_dedupes(
    remote_batch_server,
    remote_projects: ProjectManager,
) -> None:
    remote_batch_server, queue = remote_batch_server
    project = remote_projects.load_project("demo")
    project["characters"] = {
        "张三": {"description": "黑衣剑客"},
        "李四": {"description": ""},
    }
    remote_projects.save_project("demo", project)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)

    app = _mounted(remote_batch_server)
    async with remote_batch_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    first = await session.call_tool(
                        "generate_assets",
                        {"project": "demo", "type": "character", "names": ["张三", "李四"]},
                    )
                    second = await session.call_tool(
                        "generate_assets",
                        {"project": "demo", "type": "character", "names": ["张三", "李四"]},
                    )
                    first_batch = first.structuredContent["generation_batch"]
                    second_batch = second.structuredContent["generation_batch"]
                    cancelled = await session.call_tool(
                        "cancel_generation_batch",
                        {"project": "demo", "batch_id": second_batch["batch_id"]},
                    )
                    reread = await session.call_tool(
                        "get_generation_batch",
                        {"project": "demo", "batch_id": first_batch["batch_id"]},
                    )

    assert not first.isError and not second.isError
    assert first_batch["batch_id"] != second_batch["batch_id"]
    assert first_batch["poll_after_seconds"] > 0
    assert [(member["unit_id"], member["status"]) for member in first_batch["members"]] == [
        ("character/张三", "queued"),
        ("character/李四", "blocked"),
    ]
    assert first_batch["members"][0]["task_id"] == second_batch["members"][0]["task_id"]
    assert second_batch["members"][0]["deduped"] is True
    assert len((await queue.list_tasks(project_name="demo"))["items"]) == 1
    assert not cancelled.isError and not reread.isError
    assert reread.structuredContent["generation_batch"]["members"][0]["status"] == "cancelled"


async def test_remote_batch_submission_reads_migration_verdict_from_fixture_root(
    remote_batch_server,
    remote_projects: ProjectManager,
) -> None:
    _server, queue = remote_batch_server
    record_migration_failure(
        remote_projects.get_project_path("demo"),
        RuntimeError("blocked in fixture root"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )

    with pytest.raises(ConflictError) as raised:
        await submit_generation_batch(
            project_name="demo",
            operation="generate_assets",
            requested=GenerationBatchRequestSnapshot(
                selection=GenerationSelectionMode.MISSING_ONLY,
                requested=[],
            ),
            blocked=[],
            specs=[],
            source="mcp",
            queue=queue,
        )

    assert raised.value.key == MIGRATION_FAILURE_CODE


async def test_remote_api_keys_share_the_persisted_single_operator_owner(
    remote_batch_server,
    remote_projects: ProjectManager,
) -> None:
    remote_batch_server, queue = remote_batch_server
    project = remote_projects.load_project("demo")
    project["characters"] = {"张三": {"description": "黑衣剑客"}}
    remote_projects.save_project("demo", project)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)

    app = _mounted(remote_batch_server)

    async def call_as(token: str, tool: str, args: dict[str, object]):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(tool, args)

    async with remote_batch_server.session_manager.run():
        first = await call_as(
            "arc-user-a",
            "generate_assets",
            {"project": "demo", "type": "character", "names": ["张三"]},
        )
        first_batch = first.structuredContent["generation_batch"]
        foreign_read = await call_as(
            "arc-user-b",
            "get_generation_batch",
            {"project": "demo", "batch_id": first_batch["batch_id"]},
        )
        second = await call_as(
            "arc-user-b",
            "generate_assets",
            {"project": "demo", "type": "character", "names": ["张三"]},
        )
        foreign_cancel = await call_as(
            "arc-user-b",
            "cancel_generation_batch",
            {"project": "demo", "batch_id": first_batch["batch_id"]},
        )

    second_batch = second.structuredContent["generation_batch"]
    assert not first.isError and not second.isError
    assert not foreign_read.isError and not foreign_cancel.isError
    assert first_batch["members"][0]["deduped"] is False
    assert second_batch["members"][0]["deduped"] is True
    assert first_batch["members"][0]["task_id"] == second_batch["members"][0]["task_id"]
    assert len((await queue.list_tasks(project_name="demo"))["items"]) == 1


async def test_remote_mcp_entry_tools_share_one_projects_root(remote_server) -> None:
    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    created = await session.call_tool(
                        "create_project",
                        {
                            "name": "new-project",
                            "title": "New Project",
                            "content_mode": "narration",
                            "generation_mode": "storyboard",
                        },
                    )
                    projects = await session.call_tool("list_projects", {})
                    uploaded = await session.call_tool(
                        "upload_source",
                        {"project": "new-project", "filename": "novel.txt", "content": "hello"},
                    )

    assert created.structuredContent is not None
    assert created.structuredContent["project"]["name"] == "new-project"
    assert projects.structuredContent is not None
    assert {project["name"] for project in projects.structuredContent["projects"]} == {"demo", "new-project"}
    assert uploaded.structuredContent is not None
    assert uploaded.structuredContent["source"]["path"] == "source/novel.txt"


async def test_remote_mcp_draft_supports_multiple_patches_and_discard(remote_server) -> None:
    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    args = {"project": "demo", "episode": 1, "doc_type": "drama_step1"}
                    opened = await session.call_tool("open_draft", args)
                    first_content = opened.structuredContent["draft"]["content"]
                    first_content["title"] = "第一次修改"
                    first = await session.call_tool(
                        "patch_draft",
                        {
                            **args,
                            "content": first_content,
                            "base_revision": opened.structuredContent["draft"]["revision"],
                        },
                    )
                    second_content = first.structuredContent["draft"]["content"]
                    second_content["title"] = "第二次修改"
                    second = await session.call_tool(
                        "patch_draft",
                        {
                            **args,
                            "content": second_content,
                            "base_revision": first.structuredContent["draft"]["revision"],
                        },
                    )
                    discarded = await session.call_tool(
                        "discard_draft", {**args, "base_revision": second.structuredContent["draft"]["revision"]}
                    )
                    reopened = await session.call_tool("open_draft", args)
                    promoted = await session.call_tool(
                        "promote_draft",
                        {**args, "base_revision": reopened.structuredContent["draft"]["revision"]},
                    )

    assert not opened.isError
    assert not first.isError
    assert not second.isError
    assert second.structuredContent["draft"]["content"]["title"] == "第二次修改"
    assert discarded.structuredContent["draft"]["discarded"] is True
    assert not reopened.isError
    assert not promoted.isError
    assert promoted.structuredContent["draft"]["promoted"] is True


async def test_remote_mcp_text_generation_and_script_patch_return_structured_content(
    remote_server, remote_projects: ProjectManager
) -> None:
    remote_projects.create_project("ad-demo", content_mode="ad")
    remote_projects.create_project_metadata(
        "ad-demo",
        "Ad Demo",
        "",
        "ad",
        target_duration=30,
        brief="展示产品卖点",
    )
    progress_messages: list[str | None] = []

    async def record_progress(_progress: float, _total: float | None, message: str | None) -> None:
        progress_messages.append(message)

    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    step1 = await session.call_tool(
                        "generate_step1",
                        {
                            "project": "demo",
                            "episode": 1,
                            "source": "source/episode_1.txt",
                            "dry_run": True,
                        },
                        progress_callback=record_progress,
                    )
                    confirmed = await session.call_tool("confirm_script_review", {"project": "demo", "episode": 1})
                    script = await session.call_tool(
                        "generate_episode_script",
                        {"project": "ad-demo", "episode": 1, "dry_run": True},
                        progress_callback=record_progress,
                    )
                    patched = await session.call_tool(
                        "patch_episode_script",
                        {
                            "project": "demo",
                            "script": "episode_1.json",
                            "base_revision": "sha256-v1:" + "0" * 64,
                            "operations": [{"op": "remove", "id": "E1S01"}],
                        },
                    )

    assert not step1.isError
    assert step1.structuredContent["text_generation"]["message"]
    assert not confirmed.isError
    assert confirmed.structuredContent["text_generation"]["message"]
    assert not script.isError
    assert "DRY RUN" in script.structuredContent["text_generation"]["message"]
    assert progress_messages == ["Generating step1", "Generating episode script"]
    assert patched.isError
    assert patched.structuredContent["script_patch"]["problems"][0]["code"] == "revision_conflict"


async def test_text_task_is_shared_by_remote_and_embedded_hosts_and_cancelled_best_effort(
    tmp_path: Path, file_db_factory
) -> None:
    class RecordingQueue(GenerationQueue):
        def __init__(self, projects: ProjectManager) -> None:
            super().__init__(session_factory=file_db_factory, project_manager=projects)
            self.batch_ids: list[str] = []
            self.deduped = asyncio.Event()

        async def create_generation_batch(self, **kwargs) -> str:
            batch_id = await super().create_generation_batch(**kwargs)
            self.batch_ids.append(batch_id)
            return batch_id

        async def enqueue_task(self, **kwargs):
            result = await super().enqueue_task(**kwargs)
            if result["deduped"]:
                self.deduped.set()
            return result

    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo", content_mode="ad")
    projects.create_project_metadata("demo", "Demo", "", "ad", target_duration=30, brief="卖点")
    queue = RecordingQueue(projects)
    services = Services(projects=projects, workflow_planner=_Planner(), capabilities=_Capabilities(), queue=queue)

    async def verify_api_key(token: str):
        return {"sub": "apikey:test", "via": "apikey"} if token == "arc-valid" else None

    server = build_remote_mcp_server(
        projects=projects,
        services=services,
        token_verifier=ArcApiKeyVerifier(verify_api_key),
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def noninterruptible_text(_task, *, claimed_provider_id=None):
        del claimed_provider_id
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return {"message": "done"}

    async def text_provider(_task):
        return "text"

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        executor=noninterruptible_text,
        lanes=("text",),
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    queue.set_worker_cancel_callback(worker.request_cancel)
    await worker.start()

    app = _mounted(server)
    try:
        async with server.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer arc-valid"},
                follow_redirects=True,
            ) as client:
                async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        remote = await session.call_tool("generate_episode_script", {"project": "demo", "episode": 1})
                        await started.wait()
                        embedded_ctx = ToolContext(
                            project_name="demo",
                            projects_root=projects.projects_root,
                            pm=projects,
                            queue=queue,
                        )
                        embedded = asyncio.create_task(
                            _call(generate_episode_script_tool(embedded_ctx), {"episode": 1})
                        )
                        await queue.deduped.wait()

                        assert not embedded.done()
                        assert remote.structuredContent is not None
                        remote_batch = remote.structuredContent["generation_batch"]
                        assert remote_batch["members"][0]["deduped"] is False
                        assert len(queue.batch_ids) == 2

                        cancel_result = await session.call_tool(
                            "cancel_generation_batch", {"project": "demo", "batch_id": remote_batch["batch_id"]}
                        )
                        await cancelled.wait()
                        release.set()
                        embedded_result = await embedded
                        terminal = await session.call_tool(
                            "get_generation_batch", {"project": "demo", "batch_id": remote_batch["batch_id"]}
                        )

        assert cancel_result.structuredContent["generation_batch_cancellation"]["cancelling"] == [
            remote_batch["members"][0]["task_id"]
        ]
        assert embedded_result["problem"]["code"] == "generation_task_cancelled"
        assert terminal.structuredContent["generation_batch"]["done"] is True
        assert terminal.structuredContent["generation_batch"]["members"][0]["status"] == "cancelled"
        second = await queue.get_generation_batch(project_name="demo", batch_id=queue.batch_ids[1])
        assert second.members[0].task_id == remote_batch["members"][0]["task_id"]
        assert second.members[0].deduped is True
    finally:
        release.set()
        await worker.stop()
        queue.set_worker_cancel_callback(None)


@pytest.mark.parametrize("tool", ["generate_step1", "generate_episode_script"])
async def test_remote_mcp_generation_rejects_non_positive_episode(remote_server, tool: str) -> None:
    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    result = await session.call_tool(tool, {"project": "demo", "episode": 0, "dry_run": True})

    assert result.isError


async def test_remote_mcp_draft_preserves_explicit_null_updates(remote_server, remote_projects) -> None:
    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    args = {
                        "project": "demo",
                        "episode": 1,
                        "doc_type": "drama_step1",
                        "source": "source/episode_1.txt",
                    }
                    opened = await session.call_tool("open_draft", args)
                    (
                        remote_projects.get_project_path("demo") / "drafts/episode_1/step1_normalized_script.json"
                    ).unlink()
                    patched = await session.call_tool(
                        "patch_draft",
                        {
                            **args,
                            "content": opened.structuredContent["draft"]["content"],
                            "base_revision": opened.structuredContent["draft"]["revision"],
                            "accept_formal_revision": None,
                            "accepts_formal_revision": True,
                            "source": None,
                            "updates_source": True,
                        },
                    )

    draft = read_quarantine(remote_projects.get_project_path("demo"), 1, QUARANTINE_KIND_DRAMA_STEP1)
    assert not patched.isError
    assert draft is not None
    assert draft.meta["base_fingerprint"] is None
    assert draft.meta["source"] is None


async def test_remote_mcp_draft_respects_migration_failure_gate(remote_server, remote_projects) -> None:
    record_migration_failure(
        remote_projects.get_project_path("demo"),
        RuntimeError("blocked"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    app = _mounted(remote_server)
    async with remote_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer arc-valid"},
            follow_redirects=True,
        ) as client:
            async with streamable_http_client("http://localhost/mcp", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "discard_draft",
                        {
                            "project": "demo",
                            "episode": 1,
                            "doc_type": "drama_step1",
                            "base_revision": "sha256-v1:" + "0" * 64,
                        },
                    )

    assert result.isError
    assert result.structuredContent["problem"]["code"] == MIGRATION_FAILURE_CODE


async def test_remote_mcp_host_initializes_first_request_and_can_restart() -> None:
    async def verify_api_key(token: str):
        return {"sub": "apikey:test", "via": "apikey"} if token == "arc-valid" else None

    host = RemoteMCPHost(lambda: build_remote_mcp_server(token_verifier=ArcApiKeyVerifier(verify_api_key)))
    app = FastAPI()
    app.mount("/mcp", host)

    for _ in range(2):
        async with host.run():
            response = await _post_initialize(app, "arc-valid")
            assert response.status_code == 200
