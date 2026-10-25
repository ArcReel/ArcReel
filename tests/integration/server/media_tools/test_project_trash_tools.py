"""Agent 的回收站工具：删除只把未引用文件或历史版本移进项目回收站并记为 Agent 发起，列出与恢复走同一个回收站模块。"""

from __future__ import annotations

from pathlib import Path

from lib.artifacts.version_manager import VersionManager
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from server.media_tools.project_trash import restore_trash_record_summary
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


async def test_agent_deletes_a_history_version_but_not_the_current_one(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    current = ctx.project_path / "storyboards/scene_E1S01.png"
    manager = VersionManager(ctx.project_path)
    manager.add_version("storyboards", "E1S01", "v1", source_file=current)
    manager.add_version("storyboards", "E1S01", "v2", source_file=current)
    old, latest = manager.get_versions("storyboards", "E1S01")["versions"]

    refused = await run_declared_tool("delete_project_file", ctx, {"path": latest["file"]})
    outcome = await run_declared_tool("delete_project_file", ctx, {"path": old["file"]})

    assert problem_of(refused).code == "version_delete_current"
    assert outcome.problem is None, outcome.problem
    assert [v["version"] for v in manager.get_versions("storyboards", "E1S01")["versions"]] == [latest["version"]]
    [record] = project_trash.list_records(ctx.project_path)
    assert record.object["kind"] == "version"
    assert record.origin == "agent"


async def test_restore_summary_tells_the_agent_that_the_asset_came_back_under_a_new_name(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    ctx.pm.upsert_assets("demo", "characters", {"阿岚": {"description": "少女"}})
    ctx.pm.delete_asset("demo", "characters", "阿岚")
    ctx.pm.upsert_assets("demo", "characters", {"阿岚": {"description": "新建的"}})
    [record] = project_trash.list_records(ctx.project_path)

    outcome = await run_declared_tool("restore_trash_record", ctx, {"record_id": record.id})

    assert outcome.problem is None, outcome.problem
    assert outcome.value["original_object"]["name"] == "阿岚"
    assert outcome.value["object"]["name"] == "阿岚 (1)"
    assert "名称已换：阿岚 → 阿岚 (1)" in restore_trash_record_summary(outcome.value)


def test_restore_summary_names_a_new_entry_id_and_stays_quiet_when_nothing_changed() -> None:
    renumbered = {
        "object": {"kind": "script_entry", "id": "E1S04"},
        "original_object": {"kind": "script_entry", "id": "E1S03"},
        "restored": [],
    }
    unchanged = {"object": {"kind": "file"}, "original_object": {"kind": "file"}, "restored": []}

    assert "编号已换：E1S03 → E1S04" in restore_trash_record_summary(renumbered)
    assert restore_trash_record_summary(unchanged).strip() == "已恢复："
