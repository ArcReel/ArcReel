"""剪辑时间线工具：修订按调用方记录作者与所属 Agent 轮次。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import ProjectManager
from server.agent_toolset.edit_timelines import CREATE_TIMELINE, LIST_TIMELINES
from server.tool_runtime import CallerContext
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


@pytest.mark.parametrize(
    ("caller", "author", "agent_turn"),
    [
        (
            CallerContext(user_id=DEFAULT_USER_ID, source="embedded", agent_turn=lambda: "user-turn-1"),
            "arcreel_agent",
            "user-turn-1",
        ),
        (CallerContext(user_id=DEFAULT_USER_ID, source="mcp"), "external_agent", None),
    ],
)
async def test_created_revision_records_author_and_agent_turn(
    tmp_path: Path, caller: CallerContext, author: str, agent_turn: str | None
) -> None:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", {"episode": 1, "title": "E1", "content_mode": "narration", "segments": []}, "episode_1.json")
    ctx = ToolHarness("demo", tmp_path, pm, caller=caller)

    created = await run_declared_tool(CREATE_TIMELINE, ctx, {"from": "script", "episode": 1, "name": "完整版"})
    listed = await run_declared_tool(LIST_TIMELINES, ctx, {"episode": 1})

    assert created.problem is None
    assert listed.value is not None
    [summary] = listed.value
    assert (summary.updated_by.kind, summary.agent_turn) == (author, agent_turn)
