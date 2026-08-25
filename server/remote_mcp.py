"""Streamable-HTTP adapter for ArcReel's host-independent tools."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import AnyHttpUrl, BaseModel
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation_batch import GenerationBatchReadModel
from lib.project_manager import ProjectManager, get_project_manager
from lib.script_batch_edit import ScriptBatchEditResult
from lib.source_loader import SourceLoader
from lib.source_revision import SourceScope
from lib.workflow_plan import NarrationDelivery, WorkflowPlanRequest
from server.auth import API_KEY_PREFIX, _verify_api_key
from server.draft_workflow import (
    DiscardDraftRequest,
    DraftDocType,
    DraftLocator,
    PatchDraftRequest,
    PositiveEpisode,
    PromoteDraftRequest,
)
from server.services import workflow_planner
from server.text_generation import TextGenerationRequest
from server.tool_runtime import (
    CallerContext,
    CompleteAssetInventoryRequest,
    CompleteStep1RebuildRequest,
    CreateProjectToolRequest,
    GenerationBatchToolRequest,
    PatchEpisodeMetaRequest,
    PatchEpisodeScriptRequest,
    PatchProjectRequest,
    PlanEpisodesRequest,
    ProjectScope,
    RenameAssetRequest,
    ResetEpisodePlanningRequest,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    UploadSourceRequest,
    cancel_generation_batch,
    complete_asset_inventory,
    complete_step1_rebuild,
    confirm_script_review,
    create_project,
    discard_draft,
    generate_episode_script,
    generate_step1,
    get_episode_script,
    get_generation_batch,
    get_project_content,
    get_source_text,
    get_step1_content,
    get_video_capabilities,
    get_workflow_plan,
    list_project_files,
    list_projects,
    list_source_files,
    migration_gate,
    open_draft,
    patch_draft,
    patch_episode_meta,
    patch_episode_script,
    patch_project,
    plan_episodes,
    promote_draft,
    read_project_file,
    rename_asset,
    reset_episode_planning,
    retry_project_migration,
    upload_source,
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
# One decoded control byte may occupy six JSON bytes (``\u00XX``); leave 1 MiB for the MCP envelope.
_MAX_REQUEST_BODY_BYTES = SourceLoader.DEFAULT_MAX_BYTES * 6 + 1024 * 1024


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
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    else:
        payload = value
    structured = {domain_key: payload}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
        structuredContent=structured,
        isError=isinstance(value, ScriptBatchEditResult) and not value.success,
    )


def _to_long_task_result(domain_key: str, outcome: ToolOutcome[Any]) -> CallToolResult:
    return _to_mcp_result(
        "generation_batch" if isinstance(outcome.value, GenerationBatchReadModel) else domain_key, outcome
    )


def _project_scope(project: str, projects: ProjectManager) -> ProjectScope:
    project_name = projects.normalize_project_name(project)
    projects.get_project_path(project_name)
    if not projects.project_exists(project_name):
        raise FileNotFoundError(f"项目 '{project_name}' 缺少 project.json")
    return ProjectScope(project_name=project_name, projects_root=projects.projects_root)


async def _with_progress[T](awaitable: Awaitable[T], context: Context, message: str) -> T:
    await context.report_progress(0, message=message)

    async def heartbeat() -> None:
        progress = 1
        while True:
            await asyncio.sleep(10)
            await context.report_progress(progress, message=message)
            progress += 1

    task = asyncio.create_task(heartbeat())
    try:
        return await awaitable
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


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
    if services is not None:
        if projects is not None and projects.projects_root.resolve() != services.projects.projects_root.resolve():
            raise ValueError("projects 与 services.projects 必须属于同一项目根")
        projects = services.projects
    else:
        projects = projects or get_project_manager()
        services = _default_services(projects)
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
        max_request_body_size=_MAX_REQUEST_BODY_BYTES,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS", _LOCAL_HOSTS),
            allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS", _LOCAL_ORIGINS),
        ),
    )

    @server.tool(name="list_projects", structured_output=False)
    async def remote_list_projects() -> CallToolResult:
        """List ArcReel projects that can be addressed by subsequent tools."""
        return _to_mcp_result("projects", await list_projects(ToolRequest(None), caller, services))

    @server.tool(name="create_project", structured_output=False)
    async def remote_create_project(
        name: str,
        title: str = "",
        content_mode: Literal["narration", "drama", "ad"] = "narration",
        source_kind: Literal["novel", "screenplay"] = "novel",
        generation_mode: Literal["storyboard", "reference_video"] = "storyboard",
        grid_storyboard: bool = False,
        aspect_ratio: str = "9:16",
        default_duration: int | None = None,
        target_duration: int | None = None,
        brief: str | None = None,
    ) -> CallToolResult:
        """Create a project with complete metadata for subsequent ArcReel tools."""
        try:
            request = CreateProjectToolRequest(
                name=name,
                title=title,
                content_mode=content_mode,
                source_kind=source_kind,
                generation_mode=generation_mode,
                grid_storyboard=grid_storyboard,
                aspect_ratio=aspect_ratio,
                default_duration=default_duration,
                target_duration=target_duration,
                brief=brief,
            )
        except ValueError as exc:
            return _to_mcp_result("project", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result("project", await create_project(ToolRequest(request), caller, services))

    @server.tool(name="upload_source", structured_output=False)
    async def remote_upload_source(
        project: str,
        filename: str,
        content: str,
        on_conflict: Literal["fail", "replace", "rename"] = "fail",
    ) -> CallToolResult:
        """Normalize a text source file to UTF-8 and store it in one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = UploadSourceRequest(filename=filename, content=content, on_conflict=on_conflict)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("source", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result("source", await upload_source(ToolRequest(request), scope, caller, services))

    @server.tool(name="open_draft", structured_output=False)
    async def remote_open_draft(
        project: str,
        episode: PositiveEpisode,
        doc_type: DraftDocType,
        source: str | None = None,
    ) -> CallToolResult:
        """Open a revisioned editing draft for one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = DraftLocator(episode=episode, doc_type=doc_type, source=source)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("draft", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("draft", ToolOutcome(problem=problem))
        return _to_mcp_result("draft", await open_draft(ToolRequest(request), scope, caller, services))

    @server.tool(name="patch_draft", structured_output=False)
    async def remote_patch_draft(
        project: str,
        episode: PositiveEpisode,
        doc_type: DraftDocType,
        content: dict[str, Any],
        base_revision: str,
        accept_formal_revision: str | None = None,
        accepts_formal_revision: bool = False,
        source: str | None = None,
        updates_source: bool = False,
    ) -> CallToolResult:
        """Atomically replace a draft body; presence flags permit explicit null updates."""
        try:
            scope = _project_scope(project, projects)
            request = PatchDraftRequest(
                episode=episode,
                doc_type=doc_type,
                content=content,
                base_revision=base_revision,
                accept_formal_revision=accept_formal_revision,
                accepts_formal_revision=accepts_formal_revision or accept_formal_revision is not None,
                source=source,
                updates_source=updates_source or source is not None,
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("draft", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("draft", ToolOutcome(problem=problem))
        return _to_mcp_result("draft", await patch_draft(ToolRequest(request), scope, caller, services))

    @server.tool(name="promote_draft", structured_output=False)
    async def remote_promote_draft(
        project: str, episode: PositiveEpisode, doc_type: DraftDocType, base_revision: str
    ) -> CallToolResult:
        """Validate and promote one editing draft into its formal document."""
        try:
            scope = _project_scope(project, projects)
            request = PromoteDraftRequest(episode=episode, doc_type=doc_type, base_revision=base_revision)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("draft", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("draft", ToolOutcome(problem=problem))
        return _to_mcp_result("draft", await promote_draft(ToolRequest(request), scope, caller, services))

    @server.tool(name="discard_draft", structured_output=False)
    async def remote_discard_draft(
        project: str, episode: PositiveEpisode, doc_type: DraftDocType, base_revision: str
    ) -> CallToolResult:
        """Discard one editing draft without changing its formal document."""
        try:
            scope = _project_scope(project, projects)
            request = DiscardDraftRequest(episode=episode, doc_type=doc_type, base_revision=base_revision)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("draft", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("draft", ToolOutcome(problem=problem))
        return _to_mcp_result("draft", await discard_draft(ToolRequest(request), scope, caller, services))

    @server.tool(name="generate_episode_script", structured_output=False)
    async def remote_generate_episode_script(
        project: str,
        episode: PositiveEpisode,
        context: Context,
        instructions: str | None = None,
        dry_run: bool = False,
    ) -> CallToolResult:
        """Generate an episode script, or return its prompt when dry_run is true."""
        try:
            scope = _project_scope(project, projects)
            request = TextGenerationRequest(episode=episode, instructions=instructions, dry_run=dry_run)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("text_generation", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("text_generation", ToolOutcome(problem=problem))
        return _to_long_task_result(
            "text_generation",
            await _with_progress(
                generate_episode_script(ToolRequest(request), scope, caller, services),
                context,
                "Generating episode script",
            ),
        )

    @server.tool(name="generate_step1", structured_output=False)
    async def remote_generate_step1(
        project: str,
        episode: PositiveEpisode,
        context: Context,
        source: str | None = None,
        instructions: str | None = None,
        dry_run: bool = False,
    ) -> CallToolResult:
        """Generate the project-appropriate structured step1 document."""
        try:
            scope = _project_scope(project, projects)
            request = TextGenerationRequest(
                episode=episode,
                source=source,
                instructions=instructions,
                dry_run=dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("text_generation", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("text_generation", ToolOutcome(problem=problem))
        return _to_long_task_result(
            "text_generation",
            await _with_progress(
                generate_step1(ToolRequest(request), scope, caller, services),
                context,
                "Generating step1",
            ),
        )

    @server.tool(name="confirm_script_review", structured_output=False)
    async def remote_confirm_script_review(project: str, episode: int) -> CallToolResult:
        """Confirm one episode's step1 review before visual generation."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("text_generation", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("text_generation", ToolOutcome(problem=problem))
        return _to_mcp_result(
            "text_generation", await confirm_script_review(ToolRequest(episode), scope, caller, services)
        )

    @server.tool(name="patch_episode_script", structured_output=False)
    async def remote_patch_episode_script(
        project: str,
        script: str,
        base_revision: str,
        operations: list[dict[str, Any]],
    ) -> CallToolResult:
        """Atomically apply revisioned update, insert, remove, or split operations."""
        try:
            scope = _project_scope(project, projects)
            request = PatchEpisodeScriptRequest.model_validate(
                {"script": script, "base_revision": base_revision, "operations": operations}
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("script_patch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("script_patch", ToolOutcome(problem=problem))
        return _to_mcp_result("script_patch", await patch_episode_script(ToolRequest(request), scope, caller, services))

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

    @server.tool(name="get_generation_batch", structured_output=False)
    async def remote_get_generation_batch(project: str, batch_id: str) -> CallToolResult:
        """Read durable member states, counts, polling guidance and the terminal generation result."""
        try:
            scope = _project_scope(project, projects)
            request = GenerationBatchToolRequest(batch_id=batch_id)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("generation_batch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "generation_batch",
            await get_generation_batch(ToolRequest(request), scope, caller, services),
        )

    @server.tool(name="cancel_generation_batch", structured_output=False)
    async def remote_cancel_generation_batch(project: str, batch_id: str) -> CallToolResult:
        """Cancel every non-terminal member through the normal queue cancellation path."""
        try:
            scope = _project_scope(project, projects)
            request = GenerationBatchToolRequest(batch_id=batch_id)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result(
                "generation_batch_cancellation",
                ToolOutcome(problem=ToolProblem("invalid_request", str(exc))),
            )
        return _to_mcp_result(
            "generation_batch_cancellation",
            await cancel_generation_batch(ToolRequest(request), scope, caller, services),
        )

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
        return _to_long_task_result("episode_plan", await plan_episodes(ToolRequest(request), scope, caller, services))

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

    @server.tool(name="get_project_content", structured_output=False)
    async def remote_project_content(project: str) -> CallToolResult:
        """Return project creative content and its canonical revision."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_content", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("project_content", await get_project_content(ToolRequest(None), scope, caller, services))

    @server.tool(name="list_source_files", structured_output=False)
    async def remote_source_files(project: str) -> CallToolResult:
        """List source text files with revision and etags."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("source_files", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("source_files", await list_source_files(ToolRequest(None), scope, caller, services))

    @server.tool(name="get_source_text", structured_output=False)
    async def remote_source_text(project: str, path: str) -> CallToolResult:
        """Read one UTF-8 source text file and its revision."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("source_text", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("source_text", await get_source_text(ToolRequest(path), scope, caller, services))

    @server.tool(name="get_episode_script", structured_output=False)
    async def remote_episode_script(project: str, script: str) -> CallToolResult:
        """Read an episode script body and the canonical revision used for patching."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_script", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("episode_script", await get_episode_script(ToolRequest(script), scope, caller, services))

    @server.tool(name="get_step1_content", structured_output=False)
    async def remote_step1_content(project: str, episode: int) -> CallToolResult:
        """Read the current formal step1 body and its canonical revision."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("step1_content", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("step1_content", await get_step1_content(ToolRequest(episode), scope, caller, services))

    @server.tool(name="list_project_files", structured_output=False)
    async def remote_project_files(project: str) -> CallToolResult:
        """List the allowlisted project business files available for diagnostics."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_files", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("project_files", await list_project_files(ToolRequest(None), scope, caller, services))

    @server.tool(name="read_project_file", structured_output=False)
    async def remote_project_file(project: str, path: str) -> CallToolResult:
        """Read one allowlisted project business file and its revision/etag."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_file", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result("project_file", await read_project_file(ToolRequest(path), scope, caller, services))

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
