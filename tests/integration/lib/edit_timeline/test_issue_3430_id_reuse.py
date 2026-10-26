"""新增条目与覆盖剧本不复用编号，旧剪辑片段保持已删除。

每个用例：E1U1..E1U3 各有视频 v1 → 按脚本建时间线并裁切 c3（E1U3）→ 删掉/替换 E1U3 → 新单元登记 v1 → 读时间线。
断言写的是期望行为（不复用编号、旧片段判 unit_deleted）。编号由服务端在落盘前发出：批量插入请求里的编号
只是占位，新单元的编号从提交结果或落盘后的剧本读取。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import TimelineOperationAdapter
from lib.project.item_id_history import issue_landing_item_ids
from lib.project.project_manager import ProjectManager
from lib.script.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditor,
    ScriptBatchEditResult,
    script_revision,
)
from lib.script.script_editor import new_item_id
from server.agent_toolset.script_editing import PATCH_EPISODE_SCRIPT
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
NEW_UNIT_TEXT = "全新的内容：雨夜天台"


async def _timeline_with_trim_on_e1u3(service: EditTimelineService) -> str:
    created = await service.create_from_script("demo", episode=1, name="初剪", author=CREATOR)
    timeline_id = created.timeline.id
    await service.edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="裁切 E1U3",
        operations=[
            TimelineOperationAdapter.validate_python(
                {"op": "set_trim", "clip": "c3", "trim": {"source_in": 0.1, "source_out": 0.4}}
            )
        ],
        author=CREATOR,
    )
    readout = await service.read("demo", timeline_id)
    c3 = next(clip for clip in readout.clips if clip.id == "c3")
    assert (c3.unit_id, c3.status, c3.trim.basis_version) == ("E1U3", "ready", 1)
    return timeline_id


def _batch(pm: ProjectManager, operations: list[dict[str, Any]]) -> ScriptBatchEditResult:
    current = pm.load_script("demo", "episode_1.json")
    command = ScriptBatchEditCommand.model_validate(
        {"script": "episode_1.json", "expected_revision": script_revision(current), "operations": operations}
    )
    result = ScriptBatchEditor(pm).execute("demo", command)
    assert result.success, result.problems
    return result


async def _report(service: EditTimelineService, pm: ProjectManager, timeline_id: str, new_id: str) -> dict[str, Any]:
    readout = await service.read("demo", timeline_id)
    c3 = next(clip for clip in readout.clips if clip.id == "c3")
    return {
        "new_id": new_id,
        "new_unit_version": VersionManager(pm.get_project_path("demo")).get_current_version("reference_videos", new_id),
        "c3_unit": c3.unit_id,
        "c3_status": c3.status,
        "c3_video_version": c3.video_version,
        "c3_trim_basis": c3.trim.basis_version if c3.trim else None,
        "c3_duration": c3.duration,
    }


def _assert_not_reconnected(report: dict[str, Any]) -> None:
    print("REPORT", report)
    assert report["new_id"] != "E1U3", f"编号被复用: {report}"
    assert report["c3_status"] == "unit_deleted", f"旧片段接到了新单元上: {report}"


@pytest.mark.usefixtures("three_clips")
async def test_web_path_delete_last_unit_then_add(
    pm: ProjectManager, service: EditTimelineService, install_video: Callable[[str, float], None]
) -> None:
    """Web：删除走批量编辑 remove；新增照 reference_videos.add_unit，以 new_item_id(current) 为占位 insert_after，
    新编号取自提交结果。"""
    timeline_id = await _timeline_with_trim_on_e1u3(service)

    _batch(pm, [{"op": "remove", "id": "E1U3"}])
    after_remove = await service.read("demo", timeline_id)
    assert next(clip for clip in after_remove.clips if clip.id == "c3").status == "unit_deleted"

    current = pm.load_script("demo", "episode_1.json")
    placeholder = new_item_id(current)
    inserted = _batch(
        pm,
        [
            {
                "op": "insert_after",
                "after_id": "E1U2",
                "item": {"unit_id": placeholder, "text": NEW_UNIT_TEXT, "duration_seconds": 4},
            }
        ],
    )
    (new_id,) = inserted.affected_ids
    install_video(new_id, 2.0)

    _assert_not_reconnected(await _report(service, pm, timeline_id, new_id))


@pytest.mark.usefixtures("three_clips")
async def test_agent_path_delete_last_unit_then_add(
    pm: ProjectManager, service: EditTimelineService, install_video: Callable[[str, float], None]
) -> None:
    """Agent：patch_episode_script 的 remove 与 insert（insert 经 insert_segment → new_item_id 取号）。"""
    timeline_id = await _timeline_with_trim_on_e1u3(service)
    ctx = ToolHarness(project_name="demo", data_root=pm.projects_dir, pm=pm)

    async def patch(operations: list[dict[str, Any]]) -> None:
        revision = script_revision(pm.load_script("demo", "episode_1.json"))
        outcome = await run_declared_tool(
            PATCH_EPISODE_SCRIPT, ctx, {"script": "episode_1.json", "base_revision": revision, "operations": operations}
        )
        assert outcome.problem is None, outcome.problem
        assert outcome.value is not None
        assert outcome.value.success, outcome.value

    await patch([{"op": "remove", "id": "E1U3"}])
    await patch(
        [
            {
                "op": "insert",
                "after_id": "E1U2",
                "item": {"unit_id": "ignored", "text": NEW_UNIT_TEXT, "duration_seconds": 4},
            }
        ]
    )
    new_id = pm.load_script("demo", "episode_1.json")["video_units"][-1]["unit_id"]
    install_video(new_id, 2.0)

    _assert_not_reconnected(await _report(service, pm, timeline_id, new_id))


@pytest.mark.usefixtures("three_clips")
async def test_overwrite_formal_script_keeps_old_ids(
    pm: ProjectManager, service: EditTimelineService, install_video: Callable[[str, float], None]
) -> None:
    """覆盖正式剧本：照 script_generator（内容确认 / 广告整稿）在落盘前发号，再整份保存。"""
    timeline_id = await _timeline_with_trim_on_e1u3(service)

    script = pm.load_script("demo", "episode_1.json")
    script["video_units"] = [
        {"unit_id": f"E1U{n}", "text": f"{NEW_UNIT_TEXT}{n}", "duration_seconds": 4, "generated_assets": {}}
        for n in (1, 2, 3)
    ]
    issue_landing_item_ids(pm.get_project_path("demo"), pm.load_project("demo"), script)
    pm.save_script("demo", script, "episode_1.json")
    versions = VersionManager(pm.get_project_path("demo"))
    assert versions.get_current_version("reference_videos", "E1U3") == 0, "旧版本历史应已移进回收站"
    new_id = script["video_units"][2]["unit_id"]
    install_video(new_id, 2.0)

    _assert_not_reconnected(await _report(service, pm, timeline_id, new_id))
