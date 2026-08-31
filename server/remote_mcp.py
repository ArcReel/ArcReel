"""Streamable-HTTP adapter for ArcReel's host-independent tools."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from typing import Annotated, Any, Literal

from fastapi.responses import PlainTextResponse
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.tools import Tool as FastMCPTool
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import AnyHttpUrl, Field
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
from server.media_tools.assets import generate_assets_tool, list_pending_assets_tool
from server.media_tools.context import ToolContext
from server.media_tools.definition import ToolDefinition, json_value, media_outcome_payload
from server.media_tools.grid import generate_grid_tool
from server.media_tools.image_edits import edit_images_tool
from server.media_tools.narration_audio import generate_narration_audio_tool
from server.media_tools.storyboards import generate_storyboards_tool
from server.media_tools.videos import generate_videos_tool
from server.services import workflow_planner
from server.text_generation import TextGenerationRequest
from server.tool_runtime import (
    CallerContext,
    CompleteAssetInventoryRequest,
    CompleteScriptPlanRebuildRequest,
    CreateProjectToolRequest,
    GenerationBatchToolRequest,
    PatchEpisodeMetaRequest,
    PatchEpisodeScriptOperation,
    PatchEpisodeScriptRequest,
    PatchProjectRequest,
    PlanEpisodesRequest,
    ProjectScope,
    PromptPreviewRequest,
    RenameAssetRequest,
    ResetEpisodePlanningRequest,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    UploadSourceRequest,
    cancel_generation_batch,
    complete_asset_inventory,
    complete_script_plan_rebuild,
    confirm_script_review,
    create_project,
    discard_draft,
    generate_episode_script,
    generate_script_plan,
    get_episode_script,
    get_generation_batch,
    get_project_content,
    get_prompt_preview,
    get_script_plan_content,
    get_source_text,
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

# One decoded control byte may occupy six JSON bytes (``\u00XX``); leave 1 MiB for the MCP envelope.
_MAX_REQUEST_BODY_BYTES = SourceLoader.DEFAULT_MAX_BYTES * 6 + 1024 * 1024
_REMOTE_DURABLE_BATCH_MEDIA_TOOLS = frozenset(
    {"generate_assets", "generate_storyboards", "generate_grid", "edit_images", "generate_videos"}
)
_REMOTE_DURABLE_BATCH_DESCRIPTION = (
    " Remote MCP generation submissions return durable admission and durable generation_batch state immediately; "
    "follow poll_after_seconds "
    "by calling get_generation_batch until done=true, then read the terminal requested / succeeded / failed / blocked result."
)
_REMOTE_TEXT_DRY_RUN_DESCRIPTION = (
    " For dry_run=true, return the prompt immediately without a generation_batch; do not poll."
)


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


def _authenticated_caller() -> CallerContext:
    token = get_access_token()
    if token is None:
        raise RuntimeError("authenticated MCP request has no access token")
    # API-key subjects identify credentials; the supported single-operator model persists queue ownership as default.
    return CallerContext(user_id=DEFAULT_USER_ID, source="mcp")


def _to_mcp_result(domain_key: str, outcome: ToolOutcome[Any]) -> CallToolResult:
    if outcome.problem is not None:
        structured = {"problem": json_value(outcome.problem)}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
            structuredContent=structured,
            isError=True,
        )
    value = outcome.value
    payload = json_value(value)
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


def _media_outcome_to_mcp(definition: ToolDefinition, outcome: ToolOutcome[Any]) -> CallToolResult:
    structured, summary, is_error = media_outcome_payload(definition, outcome)
    return CallToolResult(
        content=[TextContent(type="text", text=summary or json.dumps(structured, ensure_ascii=False))],
        structuredContent=structured,
        isError=is_error,
    )


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
        with suppress(asyncio.CancelledError, Exception):
            await task


def _default_services(projects: ProjectManager) -> Services:
    return Services(
        projects=projects,
        workflow_planner=workflow_planner.get_workflow_planner(projects),
        capabilities=ConfigResolver(async_session_factory),
    )


def _remote_media_schema(definition: ToolDefinition) -> dict[str, Any]:
    schema = deepcopy(definition.input_schema)
    schema["properties"] = {
        "project": {"type": "string", "description": "ArcReel project name"},
        **schema.get("properties", {}),
    }
    schema["required"] = ["project", *schema.get("required", [])]
    schema["additionalProperties"] = False
    return schema


def _remote_media_description(definition: ToolDefinition) -> str:
    if definition.name in _REMOTE_DURABLE_BATCH_MEDIA_TOOLS:
        description = definition.description + _REMOTE_DURABLE_BATCH_DESCRIPTION
        if definition.name == "generate_grid":
            description += (
                " For list_only=true, the preview returns immediately without a generation_batch; do not poll."
            )
        return description
    return definition.description


MediaDefinitionFactory = Callable[[ToolContext], ToolDefinition]
MediaInvoker = Callable[[str, MediaDefinitionFactory, dict[str, Any]], Awaitable[CallToolResult]]


class _RemoteMediaTool(FastMCPTool):
    """Validate and forward media arguments through the shared JSON schema."""

    definition_factory: MediaDefinitionFactory = Field(exclude=True)
    media_invoker: MediaInvoker = Field(exclude=True)
    definition: ToolDefinition = Field(exclude=True)

    async def run(self, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        del context, convert_result
        try:
            validate_json(arguments, self.parameters)
        except JsonSchemaValidationError as exc:
            return _media_outcome_to_mcp(
                self.definition,
                ToolOutcome(problem=ToolProblem("invalid_request", exc.message)),
            )
        forwarded = dict(arguments)
        project = forwarded.pop("project")
        return await self.media_invoker(project, self.definition_factory, forwarded)


def _remote_media_tool(
    definition: ToolDefinition,
    definition_factory: MediaDefinitionFactory,
    media_invoker: MediaInvoker,
) -> FastMCPTool:
    async def unused() -> CallToolResult:
        raise RuntimeError("remote media tools override run")

    metadata = FastMCPTool.from_function(unused, structured_output=False)
    return _RemoteMediaTool(
        fn=unused,
        name=definition.name,
        title=None,
        description=_remote_media_description(definition),
        parameters=_remote_media_schema(definition),
        fn_metadata=metadata.fn_metadata,
        is_async=True,
        context_kwarg=None,
        annotations=None,
        definition_factory=definition_factory,
        media_invoker=media_invoker,
        definition=definition,
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

    def media_context(project: str) -> ToolContext:
        scope = _project_scope(project, projects)
        return ToolContext(
            project_name=scope.project_name,
            projects_root=scope.projects_root,
            pm=projects,
            config_resolver=services.capabilities,
            caller=_authenticated_caller(),
            queue=services.queue,
        )

    async def invoke_media(
        project: str,
        definition_factory: Callable[[ToolContext], ToolDefinition],
        args: dict[str, Any],
    ) -> CallToolResult:
        try:
            ctx = media_context(project)
            if definition_factory is not list_pending_assets_tool and (
                problem := await migration_gate(ctx.scope, services)
            ):
                return _to_mcp_result("generation_batch", ToolOutcome(problem=problem))
            definition = definition_factory(ctx)
            return _media_outcome_to_mcp(definition, await definition.invoke(args))
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("generation_batch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))

    schema_context = ToolContext(
        project_name="schema",
        projects_root=projects.projects_root,
        pm=projects,
        config_resolver=services.capabilities,
        caller=CallerContext(user_id=DEFAULT_USER_ID, source="mcp"),
        queue=services.queue,
    )
    media_tools: list[FastMCPTool] = []
    for definition_factory in (
        list_pending_assets_tool,
        generate_assets_tool,
        generate_storyboards_tool,
        edit_images_tool,
        generate_grid_tool,
        generate_videos_tool,
        generate_narration_audio_tool,
    ):
        definition = definition_factory(schema_context)
        media_tools.append(_remote_media_tool(definition, definition_factory, invoke_media))

    # MCP_PUBLIC_URL 只喂 RFC 9728 protected-resource metadata 与 401 challenge：ArcReel 只认
    # 静态 arc- API Key，ArcApiKeyVerifier 返回的 AccessToken 不带 resource，不参与任何校验。
    # Bearer 直连的客户端不读这两处，故该变量对常规接入可缺省。
    public_url = AnyHttpUrl(os.environ.get("MCP_PUBLIC_URL", "http://localhost:1241/mcp"))
    server = FastMCP(
        "arcreel",
        tools=media_tools,
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
        # 端点每请求强制 arc- API Key，且该 Key 从不以 cookie / session 形式存在于浏览器，
        # 重绑定到本端点的请求拿不到凭证、只能收 401——Host 白名单在此不构成安全边界，
        # 只会拦下合法部署；Host 归属由反向代理与部署形态承担。浏览器型客户端的跨源防护由
        # 应用级 CORSMiddleware（CORS_ORIGINS，见 server/cors_config.py）单点承担。
        # 关闭该开关不影响 SDK 对 POST 的 Content-Type 校验。
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    # 以下工具全部由 @server.tool 就地注册（闭包捕获 services），模块内无其它引用；basedpyright
    # 把函数作用域内的符号一律判为私有，逐个标注的 reportUnusedFunction 均为工具误报。
    @server.tool(name="list_projects", structured_output=False)
    async def remote_list_projects() -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """List ArcReel projects that can be addressed by subsequent tools."""
        return _to_mcp_result("projects", await list_projects(ToolRequest(None), _authenticated_caller(), services))

    @server.tool(name="create_project", structured_output=False)
    async def remote_create_project(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result("project", await create_project(ToolRequest(request), _authenticated_caller(), services))

    @server.tool(name="upload_source", structured_output=False)
    async def remote_upload_source(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result(
            "source", await upload_source(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="open_draft", structured_output=False)
    async def remote_open_draft(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result("draft", await open_draft(ToolRequest(request), scope, _authenticated_caller(), services))

    @server.tool(name="patch_draft", structured_output=False)
    async def remote_patch_draft(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result(
            "draft", await patch_draft(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="promote_draft", structured_output=False)
    async def remote_promote_draft(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result(
            "draft", await promote_draft(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="discard_draft", structured_output=False)
    async def remote_discard_draft(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result(
            "draft", await discard_draft(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(
        name="generate_episode_script",
        description=(
            "Generate an episode script." + _REMOTE_DURABLE_BATCH_DESCRIPTION + _REMOTE_TEXT_DRY_RUN_DESCRIPTION
        ),
        structured_output=False,
    )
    async def remote_generate_episode_script(  # pyright: ignore[reportUnusedFunction]
        project: str,
        episode: PositiveEpisode,
        context: Context,
        instructions: str | None = None,
        scope: Literal["stale", "all"] = "stale",
        entry_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> CallToolResult:
        """Generate an episode script, or return its prompt when dry_run is true.

        Existing scripts are rewritten incrementally: only entries whose script_plan content
        changed (``scope="stale"``, the default) or the entries named by ``entry_ids`` are
        re-authored; every other entry keeps its prompts, note, end frame and generated assets.
        """
        try:
            project_scope = _project_scope(project, projects)
            request = TextGenerationRequest(
                episode=episode,
                instructions=instructions,
                scope=scope,
                entry_ids=tuple(entry_ids or ()),
                dry_run=dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("text_generation", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(project_scope, services):
            return _to_mcp_result("text_generation", ToolOutcome(problem=problem))
        return _to_long_task_result(
            "text_generation",
            await _with_progress(
                generate_episode_script(ToolRequest(request), project_scope, _authenticated_caller(), services),
                context,
                "Generating episode script",
            ),
        )

    @server.tool(
        name="generate_script_plan",
        description=(
            "Generate the project-appropriate structured script_plan document."
            + _REMOTE_DURABLE_BATCH_DESCRIPTION
            + _REMOTE_TEXT_DRY_RUN_DESCRIPTION
        ),
        structured_output=False,
    )
    async def remote_generate_script_plan(  # pyright: ignore[reportUnusedFunction]
        project: str,
        episode: PositiveEpisode,
        context: Context,
        source: str | None = None,
        instructions: str | None = None,
        dry_run: bool = False,
    ) -> CallToolResult:
        """Generate the project-appropriate structured script_plan document."""
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
                generate_script_plan(ToolRequest(request), scope, _authenticated_caller(), services),
                context,
                "Generating script_plan",
            ),
        )

    @server.tool(name="confirm_script_review", structured_output=False)
    async def remote_confirm_script_review(project: str, episode: int) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Confirm one episode's script_plan review before visual generation."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("text_generation", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        if problem := await migration_gate(scope, services):
            return _to_mcp_result("text_generation", ToolOutcome(problem=problem))
        return _to_mcp_result(
            "text_generation",
            await confirm_script_review(ToolRequest(episode), scope, _authenticated_caller(), services),
        )

    @server.tool(name="patch_episode_script", structured_output=False)
    async def remote_patch_episode_script(  # pyright: ignore[reportUnusedFunction]
        project: str,
        script: str,
        base_revision: str,
        operations: Annotated[list[PatchEpisodeScriptOperation], Field(min_length=1)],
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
        return _to_mcp_result(
            "script_patch",
            await patch_episode_script(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="get_workflow_plan", structured_output=False)
    async def remote_workflow_plan(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result(
            "workflow_plan", await get_workflow_plan(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="get_generation_batch", structured_output=False)
    async def remote_get_generation_batch(project: str, batch_id: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Read durable member states, counts, polling guidance and the terminal generation result."""
        try:
            scope = _project_scope(project, projects)
            request = GenerationBatchToolRequest(batch_id=batch_id)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("generation_batch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "generation_batch",
            await get_generation_batch(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="cancel_generation_batch", structured_output=False)
    async def remote_cancel_generation_batch(project: str, batch_id: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
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
            await cancel_generation_batch(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="get_video_capabilities", structured_output=False)
    async def remote_video_capabilities(project: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Return video capabilities for one explicit ArcReel project."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("video_capabilities", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "video_capabilities",
            await get_video_capabilities(ToolRequest(None), scope, _authenticated_caller(), services),
        )

    @server.tool(
        name="plan_episodes",
        description="Plan the next source window for one explicit project." + _REMOTE_DURABLE_BATCH_DESCRIPTION,
        structured_output=False,
    )
    async def remote_plan_episodes(project: str, instructions: str | None = None) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Plan the next source window for one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = PlanEpisodesRequest(instructions=instructions)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_plan", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_long_task_result(
            "episode_plan", await plan_episodes(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="reset_episode_planning", structured_output=False)
    async def remote_reset_episode_planning(  # pyright: ignore[reportUnusedFunction]
        project: str, from_episode: int, confirm_consumed: bool = False
    ) -> CallToolResult:
        """Reset episode planning from one episode while preserving transactional safeguards."""
        try:
            scope = _project_scope(project, projects)
            request = ResetEpisodePlanningRequest(from_episode=from_episode, confirm_consumed=confirm_consumed)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_reset", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "episode_reset",
            await reset_episode_planning(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="patch_project", structured_output=False)
    async def remote_patch_project(  # pyright: ignore[reportUnusedFunction]
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
        return _to_mcp_result(
            "project_patch", await patch_project(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="patch_episode_meta", structured_output=False)
    async def remote_patch_episode_meta(  # pyright: ignore[reportUnusedFunction]
        project: str, script: str, field: Literal["title"], value: str
    ) -> CallToolResult:
        """Atomically patch episode-level metadata for one explicit project."""
        try:
            scope = _project_scope(project, projects)
            request = PatchEpisodeMetaRequest(script=script, field=field, value=value)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_meta_patch", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "episode_meta_patch",
            await patch_episode_meta(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="rename_asset", structured_output=False)
    async def remote_rename_asset(project: str, table: str, old_name: str, new_name: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Transactionally rename an asset and all project-local references."""
        try:
            scope = _project_scope(project, projects)
            request = RenameAssetRequest(table=table, old_name=old_name, new_name=new_name)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("asset_rename", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "asset_rename", await rename_asset(ToolRequest(request), scope, _authenticated_caller(), services)
        )

    @server.tool(name="retry_project_migration", structured_output=False)
    async def remote_retry_project_migration(project: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Retry the project migration chain and return the current workflow plan."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("migration_retry", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "migration_retry",
            await retry_project_migration(ToolRequest(None), scope, _authenticated_caller(), services),
        )

    @server.tool(name="complete_asset_inventory", structured_output=False)
    async def remote_complete_asset_inventory(  # pyright: ignore[reportUnusedFunction]
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
            await complete_asset_inventory(ToolRequest(request), project_scope, _authenticated_caller(), services),
        )

    @server.tool(name="complete_script_plan_rebuild", structured_output=False)
    async def remote_complete_script_plan_rebuild(  # pyright: ignore[reportUnusedFunction]
        project: str, episode: int, expected_stale_script_plan_revision: str | None
    ) -> CallToolResult:
        """Record completion of a stale script_plan rebuild using its expected revision."""
        try:
            scope = _project_scope(project, projects)
            request = CompleteScriptPlanRebuildRequest(
                episode=episode, expected_stale_script_plan_revision=expected_stale_script_plan_revision
            )
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("script_plan_rebuild", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "script_plan_rebuild",
            await complete_script_plan_rebuild(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="get_project_content", structured_output=False)
    async def remote_project_content(project: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Return project creative content and its canonical revision."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_content", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "project_content", await get_project_content(ToolRequest(None), scope, _authenticated_caller(), services)
        )

    @server.tool(name="get_prompt_preview", structured_output=False)
    async def remote_prompt_preview(project: str, script: str, item_id: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Return one shot's final image and video prompts, verbatim as generation would send them."""
        try:
            scope = _project_scope(project, projects)
            request = PromptPreviewRequest(script=script, item_id=item_id)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("prompt_preview", ToolOutcome(problem=ToolProblem("invalid_request", str(exc))))
        return _to_mcp_result(
            "prompt_preview",
            await get_prompt_preview(ToolRequest(request), scope, _authenticated_caller(), services),
        )

    @server.tool(name="list_source_files", structured_output=False)
    async def remote_source_files(project: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """List source text files with revision and etags."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("source_files", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "source_files", await list_source_files(ToolRequest(None), scope, _authenticated_caller(), services)
        )

    @server.tool(name="get_source_text", structured_output=False)
    async def remote_source_text(project: str, path: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Read one UTF-8 source text file and its revision."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("source_text", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "source_text", await get_source_text(ToolRequest(path), scope, _authenticated_caller(), services)
        )

    @server.tool(name="get_episode_script", structured_output=False)
    async def remote_episode_script(project: str, script: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Read an episode script body and the canonical revision used for patching."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("episode_script", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "episode_script", await get_episode_script(ToolRequest(script), scope, _authenticated_caller(), services)
        )

    @server.tool(name="get_script_plan_content", structured_output=False)
    async def remote_script_plan_content(project: str, episode: int) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Read the current formal script_plan body and its canonical revision."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("script_plan_content", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "script_plan_content",
            await get_script_plan_content(ToolRequest(episode), scope, _authenticated_caller(), services),
        )

    @server.tool(name="list_project_files", structured_output=False)
    async def remote_project_files(project: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """List the allowlisted project business files available for diagnostics."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_files", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "project_files", await list_project_files(ToolRequest(None), scope, _authenticated_caller(), services)
        )

    @server.tool(name="read_project_file", structured_output=False)
    async def remote_project_file(project: str, path: str) -> CallToolResult:  # pyright: ignore[reportUnusedFunction]
        """Read one allowlisted project business file and its revision/etag."""
        try:
            scope = _project_scope(project, projects)
        except (FileNotFoundError, ValueError) as exc:
            return _to_mcp_result("project_file", ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return _to_mcp_result(
            "project_file", await read_project_file(ToolRequest(path), scope, _authenticated_caller(), services)
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
    async def run(self) -> AsyncGenerator[None]:
        server = self._server_factory()
        child_app = server.streamable_http_app()
        async with server.session_manager.run():
            self._app = child_app
            try:
                yield
            finally:
                self._app = None


remote_mcp_host = RemoteMCPHost()


__all__ = [
    "ArcApiKeyVerifier",
    "RemoteMCPHost",
    "build_remote_mcp_server",
    "remote_mcp_host",
]
