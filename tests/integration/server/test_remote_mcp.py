from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lib.draft_quarantine import QUARANTINE_KIND_DRAMA_STEP1, read_quarantine
from lib.project_manager import ProjectManager
from lib.project_migration_failure import MIGRATION_FAILURE_CODE, record_migration_failure
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import WorkflowStatus
from server.auth import create_download_token, create_token
from server.remote_mcp import ArcApiKeyVerifier, RemoteMCPHost, build_remote_mcp_server
from server.tool_runtime import Services


class _Planner:
    async def get_plan(self, project_name: str, request: WorkflowPlanRequest):
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


async def test_remote_mcp_returns_typed_workflow_plan_and_rejects_bad_project(remote_server) -> None:
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
    }
    listed = {tool.name: tool for tool in tools.tools}
    assert migrated | readers | drafts | text_and_script | batches <= listed.keys()
    assert retired.isdisjoint(listed)
    assert all(
        "project" in listed[name].inputSchema["required"]
        for name in migrated | readers | drafts | text_and_script | batches
    )
    assert all(listed[name].inputSchema["properties"]["episode"]["minimum"] == 1 for name in drafts)
    assert "base_revision" in listed["discard_draft"].inputSchema["required"]
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
