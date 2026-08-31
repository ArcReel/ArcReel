"""Tests for build_arcreel_mcp_server."""

from __future__ import annotations

from pathlib import Path

from server.agent_runtime.sdk_tools import build_arcreel_mcp_server

# ---------------------------------------------------------------------------
# build_arcreel_mcp_server
# ---------------------------------------------------------------------------


def test_build_arcreel_mcp_server_contains_all_tools(tmp_path: Path) -> None:
    srv = build_arcreel_mcp_server(project_name="demo", projects_root=tmp_path)
    assert srv["name"] == "arcreel"
    # SDK exposes the registered tools on srv["instance"]; we just sanity-check
    # the type returned matches the spec contract.
    assert "instance" in srv


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
