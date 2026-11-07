"""Dual-host consistency driven by Agent tool declarations.

Each declaration projects identical names, schemas, descriptions, migration blocking, and result envelopes into ArcReel
Agent SDK servers and external remote MCP Agents. Only remote ``project`` schema fields and long-task notes may differ.
Unscoped list/create declarations expose no ``project`` on either host.
Test domain behavior at handler ``ToolOutcome``; fake handlers here only observe unchanged adapter passthrough.
"""

from __future__ import annotations

import base64
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import types
from mcp.server import Server
from PIL import Image

from lib.artifacts.artifact_activation import register_current_artifact
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.version_manager import VersionManager
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import GenerationBatchResult
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    RETRY_MIGRATION_ACTION,
    record_migration_failure,
)
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.arcreel_mcp import build_arcreel_mcp_server
from server.agent_toolset.asset_library import (
    LIST_PROVIDER_LIBRARY_ASSETS,
    RELEASE_PROVIDER_ASSET_LINK,
    SELECT_PROVIDER_LIBRARY_ASSET,
    UPLOAD_TO_PROVIDER_ASSET_LIBRARY,
)
from server.agent_toolset.declaration import (
    BLOCKED,
    MIGRATION_REFUSAL_NOTE,
    AgentToolDeclaration,
    Blocked,
    ToolDeclaration,
    UnscopedToolDeclaration,
    tool_description,
)
from server.agent_toolset.edit_timelines import (
    CREATE_TIMELINE,
    EDIT_TIMELINE,
    LIST_REVISIONS,
    READ_TIMELINE,
    RENAME_TIMELINE,
    RESTORE_REVISION,
)
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.envelope import json_value
from server.agent_toolset.generation_batches import CANCEL_GENERATION_BATCH, GET_GENERATION_BATCH
from server.agent_toolset.grid_storyboards import GENERATE_GRID, SPLIT_GRIDS
from server.agent_toolset.image_review import INSPECT_IMAGES
from server.agent_toolset.orientation import GET_PROMPT_PREVIEW, GET_VIDEO_CAPABILITIES
from server.agent_toolset.project_entry import CREATE_PROJECT, EDIT_SOURCE_TEXT
from server.agent_toolset.project_trash import DELETE_PROJECT_FILE, RESTORE_TRASH_RECORD
from server.agent_toolset.remote import LONG_TASK_NOTE, remote_tool
from server.agent_toolset.repair_channel import MERGE_ASSET
from server.agent_toolset.script_authoring import (
    CONFIRM_SCRIPT_REVIEW,
    GENERATE_EPISODE_SCRIPT,
    OPEN_DRAFT,
    PATCH_DRAFT,
    PROMOTE_DRAFT,
)
from server.agent_toolset.script_editing import PATCH_EPISODE_SCRIPT
from server.agent_toolset.toolset import AGENT_TOOLSET, ARCREEL_MCP_TOOL_IDS, MIGRATION_BLOCKED_TOOL_IDS
from server.agent_toolset.video_review import INSPECT_VIDEO_UNITS
from server.agent_toolset.video_versions import SELECT_VIDEO_VERSION
from server.agent_toolset.workflow_completion import COMPLETE_SCRIPT_PLAN_REBUILD
from server.remote_mcp import build_remote_mcp_server
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    GenerationBatchToolRequest,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    get_generation_batch,
)
from tests.factories import install_current_video, make_test_clip

_ABSENT_REVISION = "sha256-v1:" + "0" * 64

# Valid arguments per declaration; new declarations require entries here or parameterized cases fail with KeyError.
SAMPLE_ARGUMENTS: dict[str, dict[str, Any]] = {
    "list_projects": {},
    "create_project": {"name": "fresh", "title": "Fresh"},
    "upload_source": {"filename": "novel.txt", "content": "第一章\n你好", "on_conflict": "replace"},
    "edit_source_text": {"filename": "novel.txt", "text": "第一章\n改写"},
    "get_workflow_plan": {"episode_id": 1},
    "get_video_capabilities": {},
    "get_prompt_preview": {"script": "episode_1.json", "item_id": "E1S01"},
    "get_generation_batch": {"batch_id": "batch-absent"},
    "cancel_generation_batch": {"batch_id": "batch-absent"},
    "patch_project": {"overview": {"synopsis": "梗概"}},
    "patch_episode_meta": {"script": "episode_1.json", "field": "title", "value": "第一集"},
    "rename_asset": {"table": "characters", "old_name": "甲", "new_name": "乙"},
    "merge_asset": {"table": "characters", "source": "甲", "target": "乙", "dry_run": True},
    "retry_project_migration": {},
    "get_project_content": {},
    "list_source_files": {},
    "get_source_text": {"path": "source/episode_1.txt"},
    "get_episode_script": {"script": "episode_1.json"},
    "get_script_plan_content": {"episode_id": 1},
    "list_project_files": {},
    "read_project_file": {"path": "project.json"},
    "list_pending_assets": {"type": "character"},
    "generate_assets": {"type": "character", "names": ["张三"]},
    "generate_storyboards": {"script": "episode_1.json"},
    "edit_images": {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    "generate_narration_audio": {"script": "episode_1.json", "segment_ids": ["E1S01"]},
    "generate_episode_script": {"episode_id": 1, "dry_run": True},
    "generate_script_plan": {"episode_id": 1, "dry_run": True},
    "confirm_script_review": {"episode_id": 1},
    "open_draft": {"episode_id": 1, "doc_type": "drama_script_plan"},
    "patch_draft": {
        "episode_id": 1,
        "doc_type": "drama_script_plan",
        "content": {"title": "第一集", "scenes": []},
        "base_revision": _ABSENT_REVISION,
    },
    "promote_draft": {"episode_id": 1, "doc_type": "drama_script_plan", "base_revision": _ABSENT_REVISION},
    "discard_draft": {"episode_id": 1, "doc_type": "drama_script_plan", "base_revision": _ABSENT_REVISION},
    "patch_episode_script": {
        "script": "episode_9.json",
        "base_revision": "sha256-v1:" + "0" * 64,
        "operations": [{"op": "remove", "id": "E9S01"}],
    },
    "generate_grid": {"script": "episode_1.json", "list_only": True},
    "split_grids": {"grid_ids": ["grid_000000000000"]},
    "plan_episodes": {"instructions": "按章节对齐切分"},
    "reset_episode_planning": {},
    "complete_script_plan_rebuild": {"episode_id": 1, "expected_stale_script_plan_revision": None},
    "generate_videos": {"script": "episode_1.json", "target": {"scope": "all"}},
    "select_video_version": {"unit_id": "E1S01", "version": 1},
    "inspect_video_units": {"unit_ids": ["E1S01"]},
    "inspect_images": {"resource_type": "character", "ids": ["甲"]},
    "get_provider_asset_links": {"resource_type": "character", "resource_id": "甲"},
    "list_provider_library_assets": {"account_id": 1},
    "upload_to_provider_asset_library": {
        "resource_type": "character",
        "resource_id": "甲",
        "account_id": 1,
        "user_requested": True,
    },
    "select_provider_library_asset": {
        "resource_type": "character",
        "resource_id": "甲",
        "account_id": 1,
        "asset_id": "asset-absent",
    },
    "release_provider_asset_link": {"link_id": 1},
    "create_timeline": {"from": "script", "episode": 1, "name": "完整版"},
    "list_timelines": {"episode": 1},
    "read_timeline": {"timeline": "tl-0000abcd"},
    "list_bgm": {},
    "edit_timeline": {
        "timeline": "tl-0000abcd",
        "base_revision": 1,
        "summary": "压低开场原声",
        "operations": [{"op": "set_volume", "clip": "c1", "volume": 0.5}],
    },
    "rename_timeline": {"timeline": "tl-0000abcd", "name": "定稿"},
    "list_revisions": {"timeline": "tl-0000abcd"},
    "restore_revision": {"timeline": "tl-0000abcd", "revision": 1},
    "render_final_cut": {"timeline": "tl-0000abcd"},
    "export_jianying_draft": {"timeline": "tl-0000abcd"},
    "delete_project_file": {"path": "notes.txt"},
    "list_trash_records": {},
    "restore_trash_record": {"record_id": "20261025T000000Z-0000abcd"},
}

_DECLARATIONS = pytest.mark.parametrize("declaration", AGENT_TOOLSET, ids=lambda declaration: declaration.name)
_SCOPED_DECLARATIONS = pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if isinstance(declaration, ToolDeclaration)],
    ids=lambda declaration: declaration.name,
)


@pytest.fixture
def seeded_projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    root = manager.projects_dir
    manager.create_project("demo", content_mode="drama")
    manager.create_project_metadata("demo", "Demo", "", "drama")
    manager.upsert_assets("demo", "characters", {"甲": {"description": "主角"}})
    project_dir = root / "demo"
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("第一集原文", encoding="utf-8")
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "scripts" / "episode_1.json").write_text('{"episode":1,"scenes":[]}', encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_normalized_script.json").write_text('{"title":"第一集","scenes":[]}', encoding="utf-8")
    (root / "empty").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.json").write_text("{}", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    return manager


@pytest.fixture
def services(seeded_projects: ProjectManager) -> Services:
    return Services(
        projects=seeded_projects,
        workflow_planner=WorkflowPlanner(seeded_projects),
        capabilities=ConfigResolver(async_session_factory),
    )


def _scope(seeded_projects: ProjectManager) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=seeded_projects.data_root)


def _answering[DeclarationT: AgentToolDeclaration](
    declaration: DeclarationT, outcome: ToolOutcome[Any], calls: list[object]
) -> DeclarationT:
    if isinstance(declaration, UnscopedToolDeclaration):

        async def unscoped_handler(request, _caller, _services) -> ToolOutcome[Any]:
            calls.append(request.value)
            return outcome

        return replace(declaration, handler=unscoped_handler)

    async def handler(request, _scope, _caller, _services) -> ToolOutcome[Any]:
        calls.append(request.value)
        return outcome

    return replace(declaration, handler=handler)


def _remote_arguments(declaration: AgentToolDeclaration, arguments: dict[str, Any]) -> dict[str, Any]:
    """Equivalent external-Agent arguments include explicit ``project`` for project-scoped tools."""
    if isinstance(declaration, UnscopedToolDeclaration):
        return dict(arguments)
    return {"project": "demo", **arguments}


async def _call_server(server: Server, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """Call in-process server request entries to obtain model-visible content and isError."""
    request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
    result = (await server.request_handlers[types.CallToolRequest](request)).root
    assert isinstance(result, types.CallToolResult)
    return result


async def _call_embedded(
    declaration: AgentToolDeclaration, arguments: dict[str, Any], services: Services
) -> types.CallToolResult:
    server = embedded_server(
        [declaration],
        name="arcreel",
        version="1.0.0",
        scope=_scope(services.projects),
        caller=CallerContext(user_id="u1", source="embedded"),
        services=services,
    )["instance"]
    return await _call_server(server, declaration.name, arguments)


async def _call_remote(
    declaration: AgentToolDeclaration, arguments: dict[str, Any], services: Services
) -> types.CallToolResult:
    tool = remote_tool(
        declaration,
        projects=services.projects,
        services=services,
        caller=lambda: CallerContext(user_id="u1", source="mcp"),
    )
    result = await tool.run(arguments)
    assert isinstance(result, types.CallToolResult)
    return result


def _texts(result: types.CallToolResult) -> list[str]:
    return [block.text for block in result.content if isinstance(block, types.TextContent)]


def _embedded_json(result: types.CallToolResult) -> Any:
    return json.loads(_texts(result)[-1])


def _without_project(schema: dict[str, Any]) -> dict[str, Any]:
    stripped = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != "project"}}
    required = [name for name in schema.get("required", []) if name != "project"]
    if required:
        stripped["required"] = required
    else:
        stripped.pop("required", None)
    return stripped


async def _embedded_listing(seeded_projects: ProjectManager) -> dict[str, types.Tool]:
    server = build_arcreel_mcp_server(project_name="demo", data_root=seeded_projects.data_root)["instance"]
    result = (await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))).root
    assert isinstance(result, types.ListToolsResult)
    return {tool.name: tool for tool in result.tools}


@_SCOPED_DECLARATIONS
async def test_both_hosts_expose_the_declared_name_schema_and_description(
    declaration: ToolDeclaration[Any, Any], seeded_projects: ProjectManager, services: Services
) -> None:
    embedded = (await _embedded_listing(seeded_projects))[declaration.name]
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}[
        declaration.name
    ]

    assert declaration.name in ARCREEL_MCP_TOOL_IDS
    assert "project" not in embedded.inputSchema["properties"]
    assert remote.inputSchema["properties"]["project"]["type"] == "string"
    assert remote.inputSchema["required"][0] == "project"
    assert _without_project(remote.inputSchema) == embedded.inputSchema
    assert all(spec.get("description") for spec in embedded.inputSchema["properties"].values())
    assert embedded.description == tool_description(declaration)
    assert remote.description == tool_description(declaration) + (LONG_TASK_NOTE if declaration.long_task else "")
    assert embedded.description.endswith(MIGRATION_REFUSAL_NOTE) is isinstance(declaration.migration, Blocked)


@pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if isinstance(declaration, UnscopedToolDeclaration)],
    ids=lambda declaration: declaration.name,
)
async def test_both_hosts_expose_an_unscoped_declaration_without_project(
    declaration: UnscopedToolDeclaration[Any, Any], seeded_projects: ProjectManager, services: Services
) -> None:
    embedded = (await _embedded_listing(seeded_projects))[declaration.name]
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}[
        declaration.name
    ]

    assert declaration.name in ARCREEL_MCP_TOOL_IDS
    assert "project" not in remote.inputSchema["properties"]
    assert remote.inputSchema == embedded.inputSchema == declaration.input_schema
    assert all(spec.get("description") for spec in embedded.inputSchema["properties"].values())
    assert embedded.description == remote.description == declaration.description
    assert MIGRATION_REFUSAL_NOTE not in embedded.description


async def test_every_tool_both_hosts_expose_is_defined_by_a_declaration(
    seeded_projects: ProjectManager, services: Services
) -> None:
    """Both hosts register exactly the declared tool set, with no second definitions outside declarations.

    ``SAMPLE_ARGUMENTS`` enumerates every tool and doubles as the tool inventory.
    """
    embedded = set(await _embedded_listing(seeded_projects))
    remote = {tool.name for tool in await build_remote_mcp_server(services=services).list_tools()}
    declared = {declaration.name for declaration in AGENT_TOOLSET}

    assert embedded == remote == declared == set(ARCREEL_MCP_TOOL_IDS) == set(SAMPLE_ARGUMENTS)


@_DECLARATIONS
async def test_problems_pass_through_both_hosts_unchanged(
    declaration: AgentToolDeclaration, services: Services
) -> None:
    problem = ToolProblem("sentinel_problem", "原样透传", action="sentinel_action", params={"ids": ["E1S01"]})
    answering = _answering(declaration, ToolOutcome(problem=problem), [])

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, _remote_arguments(answering, SAMPLE_ARGUMENTS[declaration.name]), services)

    expected = {
        "problem": {
            "code": "sentinel_problem",
            "detail": "原样透传",
            "action": "sentinel_action",
            "params": {"ids": ["E1S01"]},
        }
    }
    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent == expected
    assert _texts(embedded) == _texts(remote)


@_DECLARATIONS
async def test_invalid_arguments_are_rejected_as_the_same_invalid_request_by_both_hosts(
    declaration: AgentToolDeclaration, seeded_projects: ProjectManager, services: Services
) -> None:
    arguments = {**SAMPLE_ARGUMENTS[declaration.name], "unexpected": 1}
    # Call the genuinely session-registered in-process server: MCP does no schema prevalidation; request models reject bad arguments.
    session_server = build_arcreel_mcp_server(project_name="demo", data_root=seeded_projects.data_root)["instance"]

    embedded = await _call_server(session_server, declaration.name, arguments)
    remote = await _call_remote(declaration, _remote_arguments(declaration, arguments), services)

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "invalid_request"
    assert remote.structuredContent["problem"]["params"]["errors"][0]["loc"] == ["unexpected"]


@_SCOPED_DECLARATIONS
async def test_a_blocked_declaration_refuses_a_migration_failed_project_at_both_entries(
    declaration: ToolDeclaration[Any, Any], seeded_projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    calls: list[object] = []
    blocked = _answering(replace(declaration, migration=BLOCKED), ToolOutcome(value={}), calls)

    embedded = await _call_embedded(blocked, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(blocked, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert calls == []
    assert embedded.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert len(_texts(embedded)) == 2


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if not (isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked))
    ],
    ids=lambda declaration: declaration.name,
)
async def test_unblocked_declarations_reach_the_handler_on_a_migration_failed_project(
    declaration: AgentToolDeclaration, seeded_projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    answering = _answering(declaration, ToolOutcome(value={"answered": True}), [])

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, _remote_arguments(answering, SAMPLE_ARGUMENTS[declaration.name]), services)

    assert _embedded_json(embedded) == remote.structuredContent == {declaration.domain_key: {"answered": True}}


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked)
    ],
    ids=lambda declaration: declaration.name,
)
async def test_declared_blocked_entries_refuse_a_migration_failed_project_before_the_handler(
    declaration: ToolDeclaration[Any, Any], seeded_projects: ProjectManager, services: Services
) -> None:
    """``BLOCKED`` declarations block at entry regardless of handler-internal migration fallbacks."""
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    calls: list[object] = []
    answering = _answering(declaration, ToolOutcome(value={}), calls)

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert declaration.name in MIGRATION_BLOCKED_TOOL_IDS
    assert calls == []
    assert _embedded_json(embedded) == remote.structuredContent
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION


async def test_episode_script_reader_reports_the_same_migration_problem_in_both_hosts(
    seeded_projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "get_episode_script")

    embedded = await _call_embedded(declaration, {"script": "episode_1.json"}, services)
    remote = await _call_remote(declaration, {"project": "demo", "script": "episode_1.json"}, services)

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


async def test_patch_episode_script_reports_a_missing_script_as_script_not_found_in_both_hosts(
    seeded_projects: ProjectManager, services: Services, tmp_path: Path
) -> None:
    arguments = SAMPLE_ARGUMENTS[PATCH_EPISODE_SCRIPT.name]

    embedded = await _call_embedded(PATCH_EPISODE_SCRIPT, arguments, services)
    remote = await _call_remote(
        PATCH_EPISODE_SCRIPT,
        _remote_arguments(PATCH_EPISODE_SCRIPT, arguments),
        _twin_services(seeded_projects, tmp_path),
    )

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "script_not_found"


def _twin_services(seeded_projects: ProjectManager, tmp_path: Path) -> Services:
    """Another project root with identical initial state lets writing tools run once per host without interference."""
    root = tmp_path / "twin"
    shutil.copytree(seeded_projects.data_root, root, symlinks=True)
    twin = ProjectManager(root)
    return Services(
        projects=twin, workflow_planner=WorkflowPlanner(twin), capabilities=ConfigResolver(async_session_factory)
    )


# Real handler outputs vary with call times and cannot compare verbatim; other fake-handler cases cover passthrough consistency.
_TIME_DEPENDENT_RESULTS = frozenset({CREATE_PROJECT.name, CREATE_TIMELINE.name})

# Declarations legitimately returning problems under sample arguments lack required objects/configuration; all others must succeed.
_PROBLEM_ON_SAMPLE = frozenset(
    {
        GET_VIDEO_CAPABILITIES.name,
        GET_PROMPT_PREVIEW.name,
        GET_GENERATION_BATCH.name,
        CANCEL_GENERATION_BATCH.name,
        # Existing formal scripts require overwrite acceptance and reject editing-copy retrieval; no present drafts can patch/promote.
        CONFIRM_SCRIPT_REVIEW.name,
        OPEN_DRAFT.name,
        PATCH_DRAFT.name,
        PROMOTE_DRAFT.name,
        PATCH_EPISODE_SCRIPT.name,
        SPLIT_GRIDS.name,
        SELECT_VIDEO_VERSION.name,
        INSPECT_VIDEO_UNITS.name,
        READ_TIMELINE.name,
        EDIT_TIMELINE.name,
        RENAME_TIMELINE.name,
        LIST_REVISIONS.name,
        RESTORE_REVISION.name,
        COMPLETE_SCRIPT_PLAN_REBUILD.name,
        # The project registers only one character, with no retained merge target.
        MERGE_ASSET.name,
        # No registered whole-source files.
        EDIT_SOURCE_TEXT.name,
        # Sample paths have no files, and trash has no deletion records.
        DELETE_PROJECT_FILE.name,
        RESTORE_TRASH_RECORD.name,
        # No material-library accounts/links exist, so upload/browse/select/unlink find no objects.
        LIST_PROVIDER_LIBRARY_ASSETS.name,
        UPLOAD_TO_PROVIDER_ASSET_LIBRARY.name,
        SELECT_PROVIDER_LIBRARY_ASSET.name,
        RELEASE_PROVIDER_ASSET_LINK.name,
    }
)


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if not (isinstance(declaration, ToolDeclaration) and declaration.long_task)
        and declaration.name not in _TIME_DEPENDENT_RESULTS
    ],
    ids=lambda declaration: declaration.name,
)
async def test_embedded_content_carries_the_same_json_as_remote_structured_content(
    declaration: AgentToolDeclaration, seeded_projects: ProjectManager, services: Services, tmp_path: Path
) -> None:
    twin = _twin_services(seeded_projects, tmp_path)
    embedded = await _call_embedded(declaration, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(declaration, _remote_arguments(declaration, SAMPLE_ARGUMENTS[declaration.name]), twin)

    assert embedded.isError is remote.isError is (declaration.name in _PROBLEM_ON_SAMPLE)
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {"problem" if remote.isError else declaration.domain_key}
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)


async def test_contact_sheets_reach_both_hosts_as_the_same_image_blocks(
    seeded_projects: ProjectManager, services: Services
) -> None:
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "segments": [{"segment_id": "E1S01", "novel_text": "旁白"}],
    }
    seeded_projects.save_script("demo", script, "episode_1.json", validate=False)
    project_path = seeded_projects.get_project_path("demo")
    clip = project_path / ".staging" / "E1S01.mp4"
    make_test_clip(clip, size="160x90", fps=25, seconds=1, tone=False)
    install_current_video(project_path, "videos", "E1S01", clip)
    arguments = {"unit_ids": ["E1S01"], "frames": 4}

    embedded = await _call_embedded(INSPECT_VIDEO_UNITS, arguments, services)
    remote = await _call_remote(INSPECT_VIDEO_UNITS, {"project": "demo", **arguments}, services)

    assert embedded.isError is remote.isError is False
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    embedded_images = [block for block in embedded.content if isinstance(block, types.ImageContent)]
    remote_images = [block for block in remote.content if isinstance(block, types.ImageContent)]
    assert len(embedded_images) == 1
    assert [(image.mimeType, image.data) for image in embedded_images] == [
        (image.mimeType, image.data) for image in remote_images
    ]
    assert embedded_images[0].mimeType == "image/jpeg"
    assert base64.b64decode(embedded_images[0].data).startswith(b"\xff\xd8")
    assert isinstance(embedded.content[-1], types.ImageContent)


async def test_inspected_images_reach_both_hosts_as_the_same_image_blocks(
    seeded_projects: ProjectManager, services: Services
) -> None:
    project_path = seeded_projects.get_project_path("demo")
    sheet = project_path / ".staging" / "甲.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), "red").save(sheet)
    VersionManager(project_path).add_version("characters", "甲", "主角", source_file=sheet)
    arguments = {"resource_type": "character", "ids": ["甲"]}

    embedded = await _call_embedded(INSPECT_IMAGES, arguments, services)
    remote = await _call_remote(INSPECT_IMAGES, {"project": "demo", **arguments}, services)

    assert embedded.isError is remote.isError is False
    assert _embedded_json(embedded) == remote.structuredContent
    embedded_images = [block for block in embedded.content if isinstance(block, types.ImageContent)]
    remote_images = [block for block in remote.content if isinstance(block, types.ImageContent)]
    assert len(embedded_images) == 1
    assert [(image.mimeType, image.data) for image in embedded_images] == [
        (image.mimeType, image.data) for image in remote_images
    ]
    assert base64.b64decode(embedded_images[0].data).startswith(b"\xff\xd8")
    assert isinstance(embedded.content[-1], types.ImageContent)


async def test_a_summary_precedes_the_structured_json_in_both_hosts(services: Services) -> None:
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "get_project_content")
    summarized = replace(
        _answering(declaration, ToolOutcome(value={"title": "Demo"}), []),
        summary=lambda value: f"已读取《{value['title']}》",
    )

    embedded = await _call_embedded(summarized, {}, services)
    remote = await _call_remote(summarized, {"project": "demo"}, services)

    assert _texts(embedded) == _texts(remote) == ["已读取《Demo》", '{"project_content": {"title": "Demo"}}']
    assert remote.structuredContent == {"project_content": {"title": "Demo"}}


@_SCOPED_DECLARATIONS
@pytest.mark.parametrize(
    "project",
    [pytest.param(None, id="missing"), 7, "", "   ", "../demo", "demo/..", "absent", "empty", "escape"],
)
async def test_remote_project_locating_failures_are_invalid_project(
    declaration: ToolDeclaration[Any, Any], project: object, services: Services
) -> None:
    calls: list[object] = []
    answering = _answering(declaration, ToolOutcome(value={}), calls)
    arguments = dict(SAMPLE_ARGUMENTS[declaration.name])
    if project is not None:
        arguments["project"] = project

    result = await _call_remote(answering, arguments, services)

    assert calls == []
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["problem"]["code"] == "invalid_project"


async def test_embedded_terminal_generation_result_has_the_shape_remote_polling_reads_at_the_terminal_state(
    seeded_projects: ProjectManager, services: Services, db_factory
) -> None:
    """Long tasks are the sole allowed result difference: embedded hosts return terminals, remote hosts batch handles; terminal generation results have matching shapes."""
    project = seeded_projects.load_project("demo")
    project["characters"] = {"李四": {"description": ""}}
    seeded_projects.save_project("demo", project)
    services = replace(services, queue=GenerationQueue(session_factory=db_factory, project_manager=seeded_projects))
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "generate_assets")
    arguments = {"type": "character", "names": ["李四"]}
    waiter = AsyncMock(return_value=([], []))
    embedded_caller = CallerContext(user_id="u1", source="embedded", batch_waiter=waiter)
    remote_caller = CallerContext(user_id="u1", source="mcp")

    session_server = embedded_server(
        [declaration],
        name="arcreel",
        version="1.0.0",
        scope=_scope(seeded_projects),
        caller=embedded_caller,
        services=services,
    )["instance"]
    embedded = await _call_server(session_server, declaration.name, arguments)
    remote = await remote_tool(
        declaration, projects=seeded_projects, services=services, caller=lambda: remote_caller
    ).run({"project": "demo", **arguments})
    assert isinstance(remote, types.CallToolResult)
    assert remote.structuredContent is not None
    handle = remote.structuredContent["generation_batch"]
    polled = await get_generation_batch(
        ToolRequest(GenerationBatchToolRequest(batch_id=handle["batch_id"])),
        _scope(seeded_projects),
        remote_caller,
        services,
    )

    # Embedded summary precedes terminal JSON; fully blocked batches are result-level failures.
    terminal = _embedded_json(embedded)
    assert embedded.isError is True
    assert len(_texts(embedded)) == 2
    assert set(terminal) == {"generation_result", "batch_id"}
    # Remote returns batch handles immediately; poll to terminal and read attached generation results.
    assert remote.isError is False
    assert set(remote.structuredContent) == {"generation_batch"}
    assert polled.value is not None
    assert polled.value.done is True
    assert polled.value.generation_result is not None
    polled_result = json_value(polled.value.generation_result)
    assert GenerationBatchResult.model_validate(terminal["generation_result"]).blocked == ["character/李四"]
    assert terminal["generation_result"] == polled_result
    waiter.assert_not_awaited()


async def test_grid_list_only_preview_is_the_same_json_in_both_hosts(
    seeded_projects: ProjectManager, services: Services
) -> None:
    """Grid previews are not generation results: both hosts immediately return identical plan text under the tool name, without pollable batches."""
    project = seeded_projects.load_project("demo")
    project.update(
        {
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        }
    )
    seeded_projects.save_project("demo", project)
    scenes = [{"scene_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)]
    (seeded_projects.get_project_path("demo") / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "content_mode": "drama", "scenes": scenes}), encoding="utf-8"
    )
    arguments = SAMPLE_ARGUMENTS[GENERATE_GRID.name]

    embedded = await _call_embedded(GENERATE_GRID, arguments, services)
    remote = await _call_remote(GENERATE_GRID, _remote_arguments(GENERATE_GRID, arguments), services)

    assert embedded.isError is remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {GENERATE_GRID.name}
    assert "E1S01..E1S04" in remote.structuredContent[GENERATE_GRID.name]
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)


async def test_a_text_generation_dry_run_returns_the_same_prompt_in_both_hosts(
    seeded_projects: ProjectManager, services: Services, tmp_path: Path
) -> None:
    """Long-task dry_run submits no batches; both hosts immediately return identical ``text_generation`` without batch handles."""
    pending_scene = {
        "scene_id": "E1S01",
        "duration_seconds": 4,
        "characters_in_scene": [],
        "scenes": [],
        "props": [],
        "image_prompt": None,
        "video_prompt": None,
        "pending_authoring": True,
        "generated_assets": {},
    }
    (seeded_projects.get_project_path("demo") / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "title": "第一集", "scenes": [pending_scene]}), encoding="utf-8"
    )
    seeded_projects.update_project(
        "demo", lambda project: project.update(episodes=[{"episode": 1, "script_file": "scripts/episode_1.json"}])
    )
    register_current_artifact(seeded_projects.get_project_path("demo"), ArtifactKey.episode_script(1))
    twin = _twin_services(seeded_projects, tmp_path)
    arguments = SAMPLE_ARGUMENTS[GENERATE_EPISODE_SCRIPT.name]

    embedded = await _call_embedded(GENERATE_EPISODE_SCRIPT, arguments, services)
    remote = await _call_remote(GENERATE_EPISODE_SCRIPT, {"project": "demo", **arguments}, twin)

    assert embedded.isError is remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {"text_generation"}
    assert "DRY RUN" in remote.structuredContent["text_generation"]["message"]
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
