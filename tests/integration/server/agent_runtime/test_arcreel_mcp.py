"""会话 in-process MCP server 的装配：工具目录与会话项目根。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from mcp import types

from lib.edit_timeline import EditTimelineService
from lib.project.project_manager import ProjectManager
from server.agent_runtime.arcreel_mcp import build_arcreel_mcp_server
from server.agent_runtime.event_log import EventLogStore, build_user_entry
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from server.agent_toolset.toolset import ARCREEL_MCP_TOOL_IDS
from tests.fakes import FakeSDKClient, result_frame, system_frame

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


async def test_create_project_writes_tts_snapshot(tmp_path: Path) -> None:
    projects = ProjectManager(tmp_path / "projects")
    server = build_arcreel_mcp_server(project_name="demo", data_root=projects.data_root)["instance"]

    created = await _call(
        server,
        "create_project",
        {
            "name": "voiced",
            "narration_delivery": "use_tts",
            "audio_backend": "dashscope/qwen3-tts-flash",
            "narration_voice": "Cherry",
            "narration_speed": None,
        },
    )
    rejected = await _call(
        server,
        "create_project",
        {
            "name": "broken",
            "narration_delivery": "use_tts",
            "audio_backend": "dashscope",
            "narration_voice": "Cherry",
            "narration_speed": None,
        },
    )

    assert created.isError is False
    project = projects.load_project("voiced")
    assert project["narration_delivery"] == "use_tts"
    assert project["audio_backend"] == "dashscope/qwen3-tts-flash"
    assert project["narration_voice"] == "Cherry"
    assert "narration_speed" not in project
    assert rejected.isError is True
    assert not (projects.projects_dir / "broken").exists()


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


@pytest.mark.parametrize("resume", [False, True], ids=["new", "resumed"])
async def test_timeline_revision_records_the_agent_author(tmp_path: Path, file_db_factory, resume: bool) -> None:
    projects = ProjectManager(tmp_path / "data")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "Anime", "narration")
    projects.save_script(
        "demo", {"episode": 1, "title": "E1", "content_mode": "narration", "segments": []}, "episode_1.json"
    )
    manager = SessionManager(
        project_root=tmp_path,
        data_root=projects.data_root,
        meta_store=SessionMetaStore(session_factory=file_db_factory),
        event_log_store=EventLogStore(session_factory=file_db_factory),
    )
    session_id = "sdk-timeline-turn"
    client = FakeSDKClient(
        frames=[
            system_frame("init", session_id=session_id, uuid="init-1"),
            result_frame(session_id=session_id, uuid="r-1"),
        ]
    )
    sdk_options: list[ClaudeAgentOptions] = []

    def sdk_client(options: ClaudeAgentOptions) -> FakeSDKClient:
        sdk_options.append(options)
        return client

    user_entry = build_user_entry([{"type": "text", "text": "开始剪辑"}])
    with patch("server.agent_runtime.session_manager.ClaudeSDKClient", sdk_client):
        if resume:
            meta = await manager.meta_store.create("demo", session_id)
            await manager.send_message(session_id, "开始剪辑", meta=meta, user_entry=user_entry)
        else:
            await manager.send_new_session("demo", "开始剪辑", user_entry=user_entry)
    try:
        servers = sdk_options[0].mcp_servers
        assert isinstance(servers, dict)
        config = servers["arcreel"]
        assert "type" in config
        assert config["type"] == "sdk"
        created = await _call(config["instance"], "create_timeline", {"from": "script", "episode": 1, "name": "完整版"})
        assert created.isError is False
        [summary] = await EditTimelineService(projects).list_timelines("demo", episode=1)
        assert summary.updated_by.kind == "arcreel_agent"
    finally:
        await manager.close_session(session_id)
