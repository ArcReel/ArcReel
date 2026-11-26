"""按 Agent 工具集声明表驱动的双宿主一致性。

每条声明在 ArcReel Agent（内嵌 SDK server）与外部 Agent（远程 MCP）两侧投影出的名字、schema、
描述、迁移阻断与结果信封必须一致；允许的差异只有远程 schema 的 ``project`` 与长任务附注。
无 scope 声明（列出、创建项目）两侧都不带 ``project``。
领域行为在 handler 的 ``ToolOutcome`` 层测，这里的 fake handler 只用于观察 adapter 是否原样透传。
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
from server.agent_toolset.content_read import GET_PROJECT_CONTENT, GET_SCRIPT_PLAN_CONTENT
from server.agent_toolset.declaration import (
    MIGRATION_REFUSAL_NOTE,
    AgentToolDeclaration,
    Blocked,
    Exempt,
    ReadCheck,
    ToolDeclaration,
    UnscopedToolDeclaration,
    tool_description,
)
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.envelope import json_value
from server.agent_toolset.grid_storyboards import GENERATE_GRID
from server.agent_toolset.image_review import INSPECT_IMAGES
from server.agent_toolset.orientation import GET_PROMPT_PREVIEW
from server.agent_toolset.project_entry import LIST_PROJECTS
from server.agent_toolset.remote import LONG_TASK_NOTE, PROJECT_PROPERTY, remote_tool
from server.agent_toolset.script_authoring import (
    GENERATE_EPISODE_SCRIPT,
    GENERATE_SCRIPT_PLAN,
)
from server.agent_toolset.script_editing import PATCH_EPISODE_SCRIPT, PATCH_SCRIPT_PLAN
from server.agent_toolset.toolset import AGENT_TOOLSET, ARCREEL_MCP_TOOL_IDS, MIGRATION_BLOCKED_TOOL_IDS
from server.agent_toolset.video_review import INSPECT_VIDEO_UNITS
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

_TOOLS_BY_POLICY = {
    UnscopedToolDeclaration: {
        "create_project",
        "list_projects",
    },
    Blocked: {
        "complete_script_plan_rebuild",
        "confirm_script_review",
        "create_timeline",
        "delete_project_file",
        "edit_images",
        "edit_source_text",
        "edit_timeline",
        "export_jianying_draft",
        "generate_assets",
        "generate_episode_script",
        "generate_grid",
        "generate_narration_audio",
        "generate_script_plan",
        "generate_storyboards",
        "generate_videos",
        "get_script_plan_content",
        "inspect_images",
        "inspect_video_units",
        "list_bgm",
        "list_revisions",
        "list_timelines",
        "patch_episode_script",
        "patch_script_plan",
        "plan_episodes",
        "read_timeline",
        "rename_timeline",
        "render_final_cut",
        "reset_episode_planning",
        "restore_revision",
        "restore_trash_record",
        "select_provider_library_asset",
        "select_video_version",
        "split_grids",
        "upload_source",
        "upload_to_provider_asset_library",
    },
    ReadCheck: {
        "get_episode_script",
        "get_prompt_preview",
        "get_workflow_plan",
        "list_pending_assets",
    },
    Exempt: {
        "cancel_generation_batch",
        "get_generation_batch",
        "get_image_capabilities",
        "get_project_content",
        "get_provider_asset_links",
        "get_source_text",
        "get_video_capabilities",
        "list_project_files",
        "list_provider_library_assets",
        "list_source_files",
        "list_trash_records",
        "merge_asset",
        "patch_episode_meta",
        "patch_project",
        "read_project_file",
        "release_provider_asset_link",
        "rename_asset",
        "retry_project_migration",
    },
}


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


def _answering[DeclarationT: ToolDeclaration[Any, Any]](
    declaration: DeclarationT, outcome: ToolOutcome[Any], calls: list[object]
) -> DeclarationT:
    async def handler(request, _scope, _caller, _services) -> ToolOutcome[Any]:
        calls.append(request.value)
        return outcome

    return replace(declaration, handler=handler)


async def _call_server(server: Server, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """经 in-process server 的请求入口调用，拿到的就是模型能看到的 content 与 isError。"""
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


async def _embedded_listing(seeded_projects: ProjectManager) -> dict[str, types.Tool]:
    server = build_arcreel_mcp_server(project_name="demo", data_root=seeded_projects.data_root)["instance"]
    result = (await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))).root
    assert isinstance(result, types.ListToolsResult)
    return {tool.name: tool for tool in result.tools}


async def test_both_host_listings_match_every_declaration(seeded_projects: ProjectManager, services: Services) -> None:
    embedded = await _embedded_listing(seeded_projects)
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}
    declared = {declaration.name for declaration in AGENT_TOOLSET}
    names = set().union(*_TOOLS_BY_POLICY.values())
    assert len(names) == sum(len(bucket) for bucket in _TOOLS_BY_POLICY.values())
    assert set(embedded) == set(remote) == declared == set(ARCREEL_MCP_TOOL_IDS) == names
    assert _TOOLS_BY_POLICY[Blocked] == MIGRATION_BLOCKED_TOOL_IDS

    for declaration in AGENT_TOOLSET:
        name = declaration.name
        local, external = embedded[name], remote[name]
        assert local.inputSchema == declaration.input_schema, name
        assert "project" not in local.inputSchema["properties"], name
        assert all(spec.get("description") for spec in local.inputSchema["properties"].values()), name
        assert local.description == tool_description(declaration), name
        if isinstance(declaration, UnscopedToolDeclaration):
            assert name in _TOOLS_BY_POLICY[UnscopedToolDeclaration], name
            assert external.inputSchema == local.inputSchema, name
            assert external.description == declaration.description, name
            assert MIGRATION_REFUSAL_NOTE not in declaration.description, name
            output_schema = None
        else:
            assert name in _TOOLS_BY_POLICY[type(declaration.migration)], name
            assert external.inputSchema["properties"]["project"]["type"] == "string", name
            assert external.inputSchema == {
                **local.inputSchema,
                "properties": {"project": PROJECT_PROPERTY, **local.inputSchema["properties"]},
                "required": ["project", *local.inputSchema.get("required", [])],
            }, name
            assert external.description == local.description + (LONG_TASK_NOTE if declaration.long_task else ""), name
            assert local.description.endswith(MIGRATION_REFUSAL_NOTE) is (name in MIGRATION_BLOCKED_TOOL_IDS), name
            output_schema = (
                declaration.output_model.model_json_schema(mode="serialization") if declaration.output_model else None
            )
        assert local.outputSchema == external.outputSchema == output_schema, name


async def test_problems_pass_through_both_hosts_unchanged(services: Services) -> None:
    declaration = GET_PROJECT_CONTENT
    problem = ToolProblem("sentinel_problem", "原样透传", action="sentinel_action", params={"ids": ["E1S01"]})
    answering = _answering(declaration, ToolOutcome(problem=problem), [])

    embedded = await _call_embedded(answering, {}, services)
    remote = await _call_remote(answering, {"project": "demo"}, services)

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


async def test_invalid_arguments_are_rejected_as_the_same_invalid_request_by_both_hosts(
    seeded_projects: ProjectManager, services: Services
) -> None:
    declaration = GET_PROJECT_CONTENT
    arguments = {"unexpected": 1}
    session_server = build_arcreel_mcp_server(project_name="demo", data_root=seeded_projects.data_root)["instance"]

    embedded = await _call_server(session_server, declaration.name, arguments)
    remote = await _call_remote(declaration, {"project": "demo", **arguments}, services)

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "invalid_request"
    assert remote.structuredContent["problem"]["params"]["errors"][0]["loc"] == ["unexpected"]


async def test_a_blocked_declaration_refuses_a_migration_failed_project_at_both_entries(
    seeded_projects: ProjectManager, services: Services
) -> None:
    declaration = GET_SCRIPT_PLAN_CONTENT
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    calls: list[object] = []
    blocked = _answering(declaration, ToolOutcome(value={}), calls)

    embedded = await _call_embedded(blocked, {"episode_id": 1}, services)
    remote = await _call_remote(blocked, {"project": "demo", "episode_id": 1}, services)

    assert calls == []
    assert embedded.isError is remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert len(_texts(embedded)) == 2


async def test_unblocked_declarations_reach_the_handler_on_a_migration_failed_project(
    seeded_projects: ProjectManager, services: Services
) -> None:
    declaration = GET_PROJECT_CONTENT
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    answering = _answering(declaration, ToolOutcome(value={"answered": True}), [])

    embedded = await _call_embedded(answering, {}, services)
    remote = await _call_remote(answering, {"project": "demo"}, services)

    assert _embedded_json(embedded) == remote.structuredContent == {declaration.domain_key: {"answered": True}}


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
    arguments = {
        "script": "episode_9.json",
        "base_revision": "sha256-v1:" + "0" * 64,
        "operations": [{"op": "remove", "id": "E9S01"}],
    }

    embedded = await _call_embedded(PATCH_EPISODE_SCRIPT, arguments, services)
    remote = await _call_remote(
        PATCH_EPISODE_SCRIPT,
        {"project": "demo", **arguments},
        _twin_services(seeded_projects, tmp_path),
    )

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "script_not_found"


def _twin_services(seeded_projects: ProjectManager, tmp_path: Path) -> Services:
    """同一初始状态的另一份项目根，让写入类工具在两宿主各跑一次、互不影响。"""
    root = tmp_path / "twin"
    shutil.copytree(seeded_projects.data_root, root, symlinks=True)
    twin = ProjectManager(root)
    return Services(
        projects=twin, workflow_planner=WorkflowPlanner(twin), capabilities=ConfigResolver(async_session_factory)
    )


async def test_plan_patch_returns_the_same_revision_and_issues_in_both_hosts(
    seeded_projects: ProjectManager,
    services: Services,
    tmp_path: Path,
    video_request_facts,
) -> None:
    seeded_projects.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    twin = _twin_services(seeded_projects, tmp_path)
    read = await _call_embedded(GET_SCRIPT_PLAN_CONTENT, {"episode_id": 1}, services)
    arguments = {
        "episode_id": 1,
        "base_revision": _embedded_json(read)["script_plan_content"]["revision"],
        "operations": [{"op": "set_title", "title": "山门"}],
    }
    embedded = await _call_embedded(PATCH_SCRIPT_PLAN, arguments, services)
    remote = await _call_remote(PATCH_SCRIPT_PLAN, {"project": "demo", **arguments}, twin)

    assert not embedded.isError
    assert not remote.isError
    assert _embedded_json(embedded) == remote.structuredContent
    result = _embedded_json(embedded)["script_plan_patch"]
    assert result["revision"] != arguments["base_revision"]
    assert any(issue["code"] == "empty_script_plan" for issue in result["blocking_issues"])


async def test_list_projects_returns_the_same_unscoped_result_in_both_hosts(services: Services) -> None:
    embedded = await _call_embedded(LIST_PROJECTS, {}, services)
    remote = await _call_remote(LIST_PROJECTS, {}, services)

    assert embedded.isError is remote.isError is False
    assert remote.structuredContent is not None
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert [project["name"] for project in remote.structuredContent["projects"]] == ["demo"]


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
    declaration = GET_PROJECT_CONTENT
    summarized = replace(
        _answering(declaration, ToolOutcome(value={"title": "Demo"}), []),
        summary=lambda value: f"已读取《{value['title']}》",
    )

    embedded = await _call_embedded(summarized, {}, services)
    remote = await _call_remote(summarized, {"project": "demo"}, services)

    assert _texts(embedded) == _texts(remote) == ["已读取《Demo》", '{"project_content": {"title": "Demo"}}']
    assert remote.structuredContent == {"project_content": {"title": "Demo"}}


async def test_remote_missing_project_returns_invalid_project_before_the_handler(services: Services) -> None:
    calls: list[object] = []
    answering = _answering(GET_PROJECT_CONTENT, ToolOutcome(value={}), calls)

    result = await _call_remote(answering, {}, services)

    assert calls == []
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["problem"]["code"] == "invalid_project"


async def test_embedded_terminal_generation_result_has_the_shape_remote_polling_reads_at_the_terminal_state(
    seeded_projects: ProjectManager, services: Services, db_factory
) -> None:
    """长任务是唯一允许的结果差异：内嵌拿终态结果，远程拿批次句柄；两边的终态生成结果同形。"""
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

    terminal = _embedded_json(embedded)
    assert embedded.isError is True
    assert len(_texts(embedded)) == 2
    assert set(terminal) == {"generation_result", "batch_id"}
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
    """宫格预览不是生成结果：两宿主都立即拿到同一份规划文本，放在工具名下，没有批次可轮询。"""
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
    arguments = {"script": "episode_1.json", "list_only": True}

    embedded = await _call_embedded(GENERATE_GRID, arguments, services)
    remote = await _call_remote(GENERATE_GRID, {"project": "demo", **arguments}, services)

    assert embedded.isError is remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {GENERATE_GRID.name}
    assert "E1S01..E1S04" in remote.structuredContent[GENERATE_GRID.name]
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)


async def test_a_text_generation_dry_run_returns_the_same_prompt_in_both_hosts(
    seeded_projects: ProjectManager, services: Services, tmp_path: Path
) -> None:
    """长任务的 dry_run 不提交批次：两宿主都立即拿到同一份 ``text_generation``，没有批次句柄。"""
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
    arguments = {"episode_id": 1, "dry_run": True}

    embedded = await _call_embedded(GENERATE_EPISODE_SCRIPT, arguments, services)
    remote = await _call_remote(GENERATE_EPISODE_SCRIPT, {"project": "demo", **arguments}, twin)

    assert embedded.isError is remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {"text_generation"}
    assert "DRY RUN" in remote.structuredContent["text_generation"]["message"]
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)


@pytest.mark.parametrize(
    ("source", "dry_run"),
    [(None, True), ("source/episode_1.txt", True), ("source/custom.txt", True), ("source/custom.txt", False)],
)
async def test_script_plan_source_contract_in_both_hosts(
    seeded_projects, services, source, dry_run, video_request_facts
):
    seeded_projects.update_project(
        "demo", lambda project: project.update(episodes=[{"episode": 1, "source_origin": "own"}])
    )
    args = {"episode_id": 1, "dry_run": dry_run}
    if source is not None:
        args["source"] = source
    embedded = await _call_embedded(GENERATE_SCRIPT_PLAN, args, services)
    remote = await _call_remote(GENERATE_SCRIPT_PLAN, {"project": "demo", **args}, services)
    assert _embedded_json(embedded) == remote.structuredContent
    assert embedded.isError is remote.isError is (source == "source/custom.txt")
    if embedded.isError:
        assert "本集规范集原文路径" in remote.structuredContent["problem"]["detail"]
    else:
        assert "第一集原文" in remote.structuredContent["text_generation"]["message"]


@pytest.fixture
def reference_preview_project(seeded_projects):
    from tests.factories import activate_reference_project

    project_path = seeded_projects.get_project_path("demo")
    (project_path / "characters").mkdir(exist_ok=True)
    (project_path / "characters" / "甲.png").write_bytes(b"image")
    (project_path / "scenes").mkdir(exist_ok=True)
    (project_path / "scenes" / "庭院.png").write_bytes(b"image")
    script = {
        "episode": 1,
        "content_mode": "drama",
        "generation_mode": "reference_video",
        "video_units": [
            {"unit_id": "E1U01", "text": "@[甲] 走进 @[庭院]。@[甲]{你好。}", "duration_seconds": 8},
            {"unit_id": "E1U02", "text": "   ", "duration_seconds": 8},
            {"unit_id": "E1U03", "text": "@[未登记] 走来。", "duration_seconds": 8},
        ],
    }
    script["video_units"].append({**script["video_units"][0], "unit_id": "E1U04", "duration_seconds": 7})
    script["video_units"].append({"unit_id": "E1U05", "text": 123, "duration_seconds": 8})
    script["video_units"].append({**script["video_units"][0], "unit_id": "E1U06", "duration_seconds": "x"})
    (project_path / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False))
    activate_reference_project(
        project_path,
        {
            "content_mode": "drama",
            "characters": {
                "甲": {"description": "主角", "character_sheet": "characters/甲.png", "voice_style": "低沉"}
            },
            "scenes": {"庭院": {"description": "院子", "scene_sheet": "scenes/庭院.png"}},
        },
    )
    return project_path


@pytest.mark.parametrize(
    ("unit_id", "unavailable"),
    [
        ("E1U01", None),
        ("E1U01", "video_reference_images_exceeded"),
        ("E1U01", "reference_capability_unavailable"),
        ("E1U04", None),
        ("E1U02", "reference_prompt_preview_missing"),
        ("E1U03", "reference_asset_unregistered"),
        ("E1U05", "prompt_preview_invalid"),
        ("E1U06", "prompt_preview_invalid"),
        ("E1U99", "item_not_found"),
    ],
)
async def test_reference_prompt_preview_reaches_both_hosts(
    reference_preview_project,
    services,
    set_video_request_facts,
    respx_mock,
    unit_id,
    unavailable,
):
    import asyncio

    from lib.generation.video_request_facts import VideoRequestFactsFailure
    from lib.script.reference_video.request_projection import project_reference_unit_request
    from server.services.admission.reference_prompt_preview import render_reference_prompt_preview
    from tests.factories import make_display_names, make_video_request_facts

    set_video_request_facts(
        VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),))
        if unavailable == "reference_capability_unavailable"
        else make_video_request_facts(
            route="reference_video",
            generation_type="r2v",
            max_reference_images=1 if unavailable == "video_reference_images_exceeded" else 2,
        )
    )

    def snapshot():
        return {
            p.relative_to(reference_preview_project): p.read_bytes()
            for p in reference_preview_project.rglob("*")
            if p.is_file()
        }

    before = await asyncio.to_thread(snapshot)
    arguments = {"script": "episode_1.json", "item_id": unit_id}
    embedded = await _call_embedded(GET_PROMPT_PREVIEW, arguments, services)
    remote = await _call_remote(GET_PROMPT_PREVIEW, {"project": "demo", **arguments}, services)
    payload = _embedded_json(embedded)
    assert payload == remote.structuredContent
    assert embedded.isError is remote.isError is (unavailable == "item_not_found")
    if unavailable == "item_not_found":
        assert payload["problem"]["code"] == unavailable
    else:
        video = payload["prompt_preview"]["video"]
        assert video["unavailable"] == unavailable
        if unavailable:
            if unavailable == "reference_capability_unavailable":
                assert video["unavailable_params"] == {"capability": "r2v"}
            assert video["text"] is None
            assert video["warnings"] == []
        else:
            assert "<甲>@图片1" in video["text"]
            assert "@图片2" in video["text"]
            assert "你好。" in video["text"]
            assert "低沉" in video["text"]
            if unit_id == "E1U04":
                assert any(
                    warning["key"] == "reference_duration_confirmation_required" for warning in video["warnings"]
                )
            project = await asyncio.to_thread(services.projects.load_project, "demo")
            script = await asyncio.to_thread(services.projects.load_script, "demo", "episode_1.json")
            unit = next(unit for unit in script["video_units"] if unit["unit_id"] == unit_id)
            projection = await project_reference_unit_request(
                project=project,
                script=script,
                unit=unit,
                project_path=reference_preview_project,
            )
            shared = await asyncio.to_thread(
                render_reference_prompt_preview,
                project=project,
                unit=unit,
                project_path=reference_preview_project,
                projection=projection,
                translate=lambda key, **params: key,
                names=make_display_names(),
            )
            assert video["text"] == shared["text"]
            assert [warning["key"] for warning in video["warnings"]] == shared["warnings"]
    assert await asyncio.to_thread(snapshot) == before
    assert not respx_mock.calls
