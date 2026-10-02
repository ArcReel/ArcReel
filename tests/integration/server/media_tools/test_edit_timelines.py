"""剪辑时间线工具：修订按调用方记录作者与所属 Agent 轮次。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import ProjectManager
from server.agent_toolset.edit_timelines import (
    CREATE_TIMELINE,
    EDIT_TIMELINE,
    LIST_REVISIONS,
    LIST_TIMELINES,
    RENAME_TIMELINE,
    RESTORE_REVISION,
)
from server.media_tools.edit_timelines import CreateTimelineRequest
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


@pytest.fixture
def harness(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", {"episode": 1, "title": "E1", "content_mode": "narration", "segments": []}, "episode_1.json")
    caller = CallerContext(user_id=DEFAULT_USER_ID, source="embedded", agent_turn=lambda: "user-turn-2")
    return ToolHarness("demo", tmp_path, pm, caller=caller)


async def test_create_from_timeline_copies_into_the_same_episode(harness: ToolHarness) -> None:
    created = await run_declared_tool(CREATE_TIMELINE, harness, {"from": "script", "episode": 1, "name": "完整版"})
    assert created.value is not None
    source_id = created.value.timeline.id

    copied = await run_declared_tool(
        CREATE_TIMELINE, harness, {"from": "timeline", "timeline": source_id, "revision": 1, "name": "副本"}
    )
    duplicate = await run_declared_tool(
        CREATE_TIMELINE, harness, {"from": "timeline", "timeline": source_id, "name": "副本"}
    )
    missing_revision = await run_declared_tool(
        CREATE_TIMELINE, harness, {"from": "timeline", "timeline": source_id, "revision": 4, "name": "另一份"}
    )

    assert copied.problem is None
    assert copied.value is not None
    assert (copied.value.timeline.name, copied.value.timeline.episode, copied.value.revision) == ("副本", 1, 1)
    assert copied.value.timeline.id != source_id
    assert duplicate.problem is not None
    assert duplicate.problem.code == "timeline_name_conflict"
    assert missing_revision.problem is not None
    assert missing_revision.problem.code == "revision_not_found"


@pytest.mark.parametrize(
    "arguments",
    [
        {"from": "script", "name": "完整版"},
        {"from": "script", "episode": 1, "timeline": "tl-0000abcd", "name": "完整版"},
        {"from": "script", "episode": 1, "revision": 1, "name": "完整版"},
        {"from": "timeline", "name": "副本"},
        {"from": "timeline", "timeline": "tl-0000abcd", "episode": 1, "name": "副本"},
        {"from": "blank", "name": "副本"},
    ],
)
def test_create_request_requires_the_arguments_of_its_source(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CreateTimelineRequest.model_validate(arguments)


async def test_rename_list_revisions_and_restore_through_the_tools(harness: ToolHarness) -> None:
    created = await run_declared_tool(CREATE_TIMELINE, harness, {"from": "script", "episode": 1, "name": "完整版"})
    assert created.value is not None
    timeline_id = created.value.timeline.id

    renamed = await run_declared_tool(RENAME_TIMELINE, harness, {"timeline": timeline_id, "name": "定稿"})
    listed = await run_declared_tool(LIST_TIMELINES, harness, {"episode": 1})
    history = await run_declared_tool(LIST_REVISIONS, harness, {"timeline": timeline_id})
    unchanged = await run_declared_tool(RESTORE_REVISION, harness, {"timeline": timeline_id, "revision": 1})

    assert renamed.value is not None
    assert renamed.value.name == "定稿"
    assert listed.value is not None
    assert [item.name for item in listed.value] == ["定稿"]
    assert history.value is not None
    assert [(item.number, item.author.kind, item.agent_turn) for item in history.value.revisions] == [
        (1, "arcreel_agent", "user-turn-2")
    ]
    assert unchanged.problem is not None
    assert unchanged.problem.code == "revision_unchanged"


async def test_place_narration_moves_the_carrier_through_the_edit_tool(tmp_path: Path) -> None:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    segments = [
        {
            "segment_id": segment_id,
            "novel_text": text,
            "duration_seconds": 4,
            "characters_in_segment": [],
            "video_prompt": "Clouds move",
        }
        for segment_id, text in (("E1S01", "风起了"), ("E1S02", "雨停了"))
    ]
    pm.save_script(
        "demo", {"episode": 1, "title": "E1", "content_mode": "narration", "segments": segments}, "episode_1.json"
    )
    harness = ToolHarness("demo", tmp_path, pm, caller=CallerContext(user_id=DEFAULT_USER_ID, source="mcp"))
    created = await run_declared_tool(CREATE_TIMELINE, harness, {"from": "script", "episode": 1, "name": "完整版"})
    assert created.value is not None

    edited = await run_declared_tool(
        EDIT_TIMELINE,
        harness,
        {
            "timeline": created.value.timeline.id,
            "base_revision": 1,
            "summary": "旁白改挂到补插的镜头上",
            "operations": [
                {"op": "insert", "unit_id": "E1S01", "after": "c2"},
                {"op": "place_narration", "clip": "c3"},
            ],
        },
    )

    assert edited.problem is None
    assert edited.value is not None
    assert [(clip.id, clip.carries_narration) for clip in edited.value.clips] == [("c1", False), ("c3", True)]
