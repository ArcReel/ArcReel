"""Tests for build_arcreel_mcp_server."""

from __future__ import annotations

import json
from pathlib import Path

from mcp import types

from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS, build_arcreel_mcp_server

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


async def test_session_server_routes_factory_registered_tools_to_their_handler(tmp_path: Path) -> None:
    server = build_arcreel_mcp_server(project_name="demo", data_root=tmp_path)["instance"]
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="list_projects", arguments={}))

    result = (await server.request_handlers[types.CallToolRequest](request)).root

    assert isinstance(result, types.CallToolResult)
    assert result.isError is False
    assert isinstance(result.content[0], types.TextContent)
    assert json.loads(result.content[0].text) == {"projects": []}


def test_generate_narration_audio_registered() -> None:
    """旁白配音工具必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "generate_narration_audio" in ARCREEL_MCP_TOOL_IDS


def test_retired_tool_names_are_not_registered() -> None:
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

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
