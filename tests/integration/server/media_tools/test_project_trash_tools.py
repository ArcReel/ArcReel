"""Agent 的回收站工具：删除只把未引用文件移进项目回收站并记为 Agent 发起，列出与恢复走同一个回收站模块。"""

from __future__ import annotations

from pathlib import Path

from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool

_SCRIPT = {
    "episode": 1,
    "title": "E1",
    "content_mode": "narration",
    "segments": [
        {
            "segment_id": "E1S01",
            "novel_text": "旁白",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
        }
    ],
}


def _harness(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", _SCRIPT, "episode_1.json", validate=False)
    ctx = ToolHarness("demo", tmp_path, pm)
    (ctx.project_path / "storyboards/scene_E1S01.png").write_bytes(b"in-use")
    (ctx.project_path / "storyboards/scene_E1S02.png").write_bytes(b"leftover")
    return ctx


async def test_agent_delete_moves_an_unreferenced_file_into_one_trash_record(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)

    outcome = await run_declared_tool("delete_project_file", ctx, {"path": "storyboards/scene_E1S02.png"})

    assert outcome.problem is None, outcome.problem
    assert not (ctx.project_path / "storyboards/scene_E1S02.png").exists()
    [record] = project_trash.list_records(ctx.project_path)
    assert record.files == ("storyboards/scene_E1S02.png",)
    assert record.origin == "agent"
    assert outcome.value["record"]["id"] == record.id


async def test_agent_cannot_delete_a_referenced_file(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)

    outcome = await run_declared_tool("delete_project_file", ctx, {"path": "storyboards/scene_E1S01.png"})

    assert problem_of(outcome).code == "file_view_delete_not_allowed"
    assert (ctx.project_path / "storyboards/scene_E1S01.png").read_bytes() == b"in-use"
    assert project_trash.list_records(ctx.project_path) == []


async def test_agent_lists_and_restores_a_deletion_record(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    await run_declared_tool("delete_project_file", ctx, {"path": "storyboards/scene_E1S02.png"})

    listed = await run_declared_tool("list_trash_records", ctx, {})
    [record] = listed.value["records"]
    restored = await run_declared_tool("restore_trash_record", ctx, {"record_id": record["id"]})

    assert restored.problem is None, restored.problem
    assert (ctx.project_path / "storyboards/scene_E1S02.png").read_bytes() == b"leftover"
    assert project_trash.list_records(ctx.project_path) == []
    again = await run_declared_tool("restore_trash_record", ctx, {"record_id": record["id"]})
    assert problem_of(again).code == "trash_record_not_found"
