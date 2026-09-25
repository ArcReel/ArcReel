"""会话 in-process MCP server 的装配：工具目录与会话项目根。"""

from __future__ import annotations

import json
from pathlib import Path

from mcp import types

from lib.project.project_manager import ProjectManager
from server.agent_runtime.arcreel_mcp import build_arcreel_mcp_server
from server.agent_toolset.toolset import ARCREEL_MCP_TOOL_IDS

# ---------------------------------------------------------------------------
# build_arcreel_mcp_server
# ---------------------------------------------------------------------------


def test_build_arcreel_mcp_server_contains_all_tools(tmp_path: Path) -> None:
    srv = build_arcreel_mcp_server(project_name="demo", data_root=tmp_path)
    assert srv["name"] == "arcreel"
    # SDK exposes the registered tools on srv["instance"]; we just sanity-check
    # the type returned matches the spec contract.
    assert "instance" in srv


async def test_session_server_lists_every_catalogued_tool(tmp_path: Path) -> None:
    server = build_arcreel_mcp_server(project_name="demo", data_root=tmp_path)["instance"]

    listed = (await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())).root

    assert isinstance(listed, types.ListToolsResult)
    assert sorted(tool.name for tool in listed.tools) == sorted(ARCREEL_MCP_TOOL_IDS)


async def _call(server, name: str, arguments: dict) -> types.CallToolResult:
    request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
    result = (await server.request_handlers[types.CallToolRequest](request)).root
    assert isinstance(result, types.CallToolResult)
    return result


async def test_session_entry_tools_share_the_session_projects_root(tmp_path: Path) -> None:
    projects = ProjectManager(tmp_path / "projects")
    server = build_arcreel_mcp_server(project_name="demo", data_root=projects.data_root)["instance"]

    created = await _call(server, "create_project", {"name": "demo", "title": "Demo"})
    listed = await _call(server, "list_projects", {})
    uploaded = await _call(server, "upload_source", {"filename": "novel.txt", "content": "hello"})

    assert [result.isError for result in (created, listed, uploaded)] == [False, False, False]
    assert isinstance(listed.content[-1], types.TextContent)
    assert [project["name"] for project in json.loads(listed.content[-1].text)["projects"]] == ["demo"]
    assert (projects.get_project_path("demo") / "source" / "novel.txt").read_text(encoding="utf-8") == "hello"


def test_generate_narration_audio_registered() -> None:
    """旁白配音工具必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    assert "generate_narration_audio" in ARCREEL_MCP_TOOL_IDS


def test_retired_tool_names_are_not_registered() -> None:
    assert "patch_episode_script" in ARCREEL_MCP_TOOL_IDS
    assert {
        "normalize_drama_script",
        "split_narration_segments",
        "split_reference_video_units",
        "insert_segment",
        "remove_segment",
        "split_segment",
        "open_script_plan_for_edit",
        "validate_and_promote_draft",
        "get_episode_script_revision",
    }.isdisjoint(ARCREEL_MCP_TOOL_IDS)
