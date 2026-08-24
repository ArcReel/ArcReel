from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lib.project_manager import ProjectManager
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
def remote_server(tmp_path: Path):
    projects_root = tmp_path / "projects"
    manager = ProjectManager(projects_root)
    manager.create_project("demo", content_mode="ad")
    manager.create_project_metadata("demo", "Demo", "", "ad", target_duration=30)
    (projects_root / "empty").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.json").write_text("{}", encoding="utf-8")
    (projects_root / "escape").symlink_to(outside, target_is_directory=True)

    async def verify_api_key(token: str):
        return {"sub": "apikey:test", "via": "apikey"} if token == "arc-valid" else None

    services = Services(projects=manager, workflow_planner=_Planner(), capabilities=_Capabilities())
    return build_remote_mcp_server(
        projects=manager, services=services, token_verifier=ArcApiKeyVerifier(verify_api_key)
    )


def _mounted(server) -> FastAPI:
    app = FastAPI()
    app.mount("/mcp", server.streamable_http_app())
    return app


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
    listed = {tool.name: tool for tool in tools.tools}
    assert migrated <= listed.keys()
    assert all("project" in listed[name].inputSchema["required"] for name in migrated)
    assert result.structuredContent is not None
    assert result.structuredContent["workflow_plan"]["status"]["target"]["episode"] == 1
    assert capabilities.structuredContent == {
        "video_capabilities": {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}
    }
    assert patched.structuredContent is not None
    assert patched.structuredContent["project_patch"]["operation"] == "overview"
    assert missing.isError
    assert traversal.isError
    assert nonexistent.isError
    assert empty.isError
    assert escape.isError


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
