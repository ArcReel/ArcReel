"""Streamable-HTTP adapter for ArcReel's host-independent tools."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import AnyHttpUrl, BaseModel
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.project_manager import ProjectManager, get_project_manager
from lib.source_revision import SourceScope
from lib.workflow_plan import NarrationDelivery, WorkflowPlanRequest
from server.auth import API_KEY_PREFIX, _verify_api_key
from server.services import workflow_planner
from server.tool_runtime import (
    CallerContext,
    CompleteAssetInventoryRequest,
    CompleteStep1RebuildRequest,
    PatchEpisodeMetaRequest,
    PatchProjectRequest,
    PlanEpisodesRequest,
    ProjectScope,
    RenameAssetRequest,
    ResetEpisodePlanningRequest,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    complete_asset_inventory,
    complete_step1_rebuild,
    get_video_capabilities,
    get_workflow_plan,
    patch_episode_meta,
    patch_project,
    plan_episodes,
    rename_asset,
    reset_episode_planning,
    retry_project_migration,
)

_LOCAL_HOSTS = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"]
_LOCAL_ORIGINS = [
    "http://127.0.0.1",
    "http://127.0.0.1:*",
    "http://localhost",
    "http://localhost:*",
    "http://[::1]",
    "http://[::1]:*",
]


class ArcApiKeyVerifier(TokenVerifier):
    """Bridge MCP Bearer auth to ArcReel's existing API Key verifier."""

    def __init__(self, verify_api_key: Callable[[str], Awaitable[dict[str, Any] | None]] = _verify_api_key) -> None:
        self._verify_api_key = verify_api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(API_KEY_PREFIX):
            return None
        payload = await self._verify_api_key(token)
        if payload is None:
            return None
        return AccessToken(token=token, client_id=payload["sub"], scopes=["arcreel"])


def _csv_env(name: str, default: list[str]) -> list[str]:
    configured = [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]
    return configured or default


def _to_mcp_result(domain_key: str, outcome: ToolOutcome[Any]) -> CallToolResult:
    if outcome.problem is not None:
        structured = {"problem": outcome.problem.model_dump(mode="json")}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
            structuredContent=structured,
            isError=True,
        )
    value = outcome.value
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    structured = {domain_key: payload}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
        structuredContent=structured,
    )


def _project_scope(project: str, projects: ProjectManager) -> ProjectScope:
    project_name = projects.normalize_project_name(project)
    projects.get_project_path(project_name)
    if not projects.project_exists(project_name):
        raise FileNotFoundError(f"项目 '{project_name}' 缺少 project.json")
    return ProjectScope(project_name=project_name, projects_root=projects.projects_root)


def _default_services(projects: ProjectManager) -> Services:
    return Services(
        projects=projects,
        workflow_planner=workflow_planner.get_workflow_planner(projects),
        capabilities=ConfigResolver(async_session_factory),
    )


def build_remote_mcp_server(
    *,
    projects: ProjectManager | None = None,
    services: Services | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastMCP:
    """Build one restart-safe MCP server instance for the host lifespan."""
    projects = projects or get_project_manager()
    services = services or _default_services(projects)
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")
    public_url = AnyHttpUrl(os.environ.get("MCP_PUBLIC_URL", "http://localhost:1241/mcp"))
    server = FastMCP(
        "arcreel",
        token_verifier=token_verifier or ArcApiKeyVerifier(),
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            required_scopes=["arcreel"],
        ),
        stateless_http=True,
        streamable_http_path="/",
        json_response=False,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS", _LOCAL_HOSTS),
            allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS", _LOCAL_ORIGINS),
        ),
    )

    @server.tool(name="get_workflow_plan", structured_output=False)
    async def remote_workflow_plan(
        project: str,
        episode: int | None = None,
        narration_delivery: NarrationDelivery | None = None,
        confirmed_request_durations: dict[str, int] | None = None,
    ) -> CallToolResult:
        """Return the authoritative next-step plan for one explicit ArcReel project."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("workflow_plan", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        try:
            request = WorkflowPlanRequest(
                episode=episode,
                narration_delivery=narration_delivery,
                confirmed_request_durations=confirmed_request_durations or {},
            )
        except ValueError as exc:
            return _to_mcp_result("workflow_plan", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result("workflow_plan", await get_workflow_plan(ToolRequest(request), scope, caller, services))

    @server.tool(name="get_video_capabilities", structured_output=False)
    async def remote_video_capabilities(project: str) -> CallToolResult:
        """Return video capabilities for one explicit ArcReel project."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("video_capabilities", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "video_capabilities", await get_video_capabilities(ToolRequest(None), scope, caller, services)
        )

    @server.tool(name="plan_episodes", structured_output=False)
    async def remote_plan_episodes(project: str, instructions: str | None = None) -> CallToolResult:
        """Plan the next source window for one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = PlanEpisodesRequest(instructions=instructions)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_plan", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result("episode_plan", await plan_episodes(ToolRequest(request), scope, caller, services))

    @server.tool(name="reset_episode_planning", structured_output=False)
    async def remote_reset_episode_planning(
        project: str, from_episode: int, confirm_consumed: bool = False
    ) -> CallToolResult:
        """Reset episode planning from one episode while preserving transactional safeguards."""
        try:
            scope = _project_scope(project, projects)
            request = ResetEpisodePlanningRequest(from_episode=from_episode, confirm_consumed=confirm_consumed)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_reset", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "episode_reset", await reset_episode_planning(ToolRequest(request), scope, caller, services)
        )

    @server.tool(name="patch_project", structured_output=False)
    async def remote_patch_project(
        project: str,
        table: str | None = None,
        entries: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        overview: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Atomically patch project assets, settings, or overview for one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = PatchProjectRequest(table=table, entries=entries, settings=settings, overview=overview)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_patch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result("project_patch", await patch_project(ToolRequest(request), scope, caller, services))

    @server.tool(name="patch_episode_meta", structured_output=False)
    async def remote_patch_episode_meta(
        project: str, script: str, field: Literal["title"], value: str
    ) -> CallToolResult:
        """Atomically patch episode-level metadata for one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = PatchEpisodeMetaRequest(script=script, field=field, value=value)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_meta_patch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "episode_meta_patch", await patch_episode_meta(ToolRequest(request), scope, caller, services)
        )

    @server.tool(name="rename_asset", structured_output=False)
    async def remote_rename_asset(project: str, table: str, old_name: str, new_name: str) -> CallToolResult:
        """Transactionally rename an asset and all project-local references."""
        try:
            scope = _project_scope(project, projects)
            request = RenameAssetRequest(table=table, old_name=old_name, new_name=new_name)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("asset_rename", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result("asset_rename", await rename_asset(ToolRequest(request), scope, caller, services))

    @server.tool(name="retry_project_migration", structured_output=False)
    async def remote_retry_project_migration(project: str) -> CallToolResult:
        """Retry the project migration chain and return the current workflow plan."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("migration_retry", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "migration_retry", await retry_project_migration(ToolRequest(None), scope, caller, services)
        )

    @server.tool(name="complete_asset_inventory", structured_output=False)
    async def remote_complete_asset_inventory(
        project: str,
        scope: SourceScope,
        expected_source_revision: str,
        entries: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Atomically commit an asset inventory against a source revision."""
        try:
            project_scope = _project_scope(project, projects)
            request = CompleteAssetInventoryRequest(
                scope=scope,
                expected_source_revision=expected_source_revision,
                entries=entries,
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("asset_inventory", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "asset_inventory",
            await complete_asset_inventory(ToolRequest(request), project_scope, caller, services),
        )

    @server.tool(name="complete_step1_rebuild", structured_output=False)
    async def remote_complete_step1_rebuild(
        project: str, episode: int, expected_stale_step1_revision: str | None
    ) -> CallToolResult:
        """Record completion of a stale step1 rebuild using its expected revision."""
        try:
            scope = _project_scope(project, projects)
            request = CompleteStep1RebuildRequest(
                episode=episode, expected_stale_step1_revision=expected_stale_step1_revision
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("step1_rebuild", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "step1_rebuild", await complete_step1_rebuild(ToolRequest(request), scope, caller, services)
        )

    return server


class RemoteMCPHost:
    """Stable ASGI mount whose one-shot SDK manager is rebuilt per host lifespan."""

    def __init__(self, server_factory: Callable[[], FastMCP] = build_remote_mcp_server) -> None:
        self._server_factory = server_factory
        self._app: Any | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            await PlainTextResponse("MCP server is not running", status_code=503)(scope, receive, send)
            return
        await self._app(scope, receive, send)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        server = self._server_factory()
        child_app = server.streamable_http_app()
        async with server.session_manager.run():
            self._app = child_app
            try:
                yield
            finally:
                self._app = None


remote_mcp_host = RemoteMCPHost()


__all__ = ["ArcApiKeyVerifier", "RemoteMCPHost", "build_remote_mcp_server", "remote_mcp_host"]
