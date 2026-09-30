"""list_pending_assets 与 generate_assets handler 的 ``ToolOutcome``。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import ProjectManager
from server.services.admission.asset_sheet_batch import AssetSheetScope, load_asset_sheet_batch_plan
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    read_generation_result,
    run_declared_tool,
)


async def test_list_pending_assets_happy(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("list_pending_assets", fake_ctx, {})

    assert out.problem is None
    assert isinstance(out.value, str)
    assert "张三" in out.value
    assert "村口" in out.value
    assert "保温杯" in out.value


async def test_pending_asset_tools_include_an_unclaimed_schema8_sheet(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    project_dir = pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_scene("demo", "客厅", "宽敞的客厅")
    pm.update_scene_sheet("demo", "客厅", "scenes/客厅.png")
    (project_dir / "scenes" / "客厅.png").write_bytes(b"png")
    ctx = ToolHarness(project_name="demo", data_root=projects_root, pm=pm)

    listed = await run_declared_tool("list_pending_assets", ctx, {"type": "scene"})

    assert isinstance(listed.value, str)
    assert "客厅" in listed.value

    enqueued: list[str] = []

    async def _capture_batch(*, specs, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    await run_declared_tool("generate_assets", ctx, {"type": "scene"}, batch_waiter=_capture_batch)

    assert enqueued == ["客厅"]


async def test_list_pending_assets_error(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.mirror_to_disk()

    def boom(_name):
        raise RuntimeError("db down")

    fake_ctx.pm.load_project = boom

    out = await run_declared_tool("list_pending_assets", fake_ctx, {"type": "character"})

    assert out.problem is not None
    assert out.problem.code == "internal_error"


async def test_generate_assets_happy(fake_ctx: ToolHarness) -> None:
    async def fake_batch(*, specs, **_batch_kwargs):
        from lib.generation.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.unit_id or s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 1},
            )
            for s in specs
        ]
        return succ, []

    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("generate_assets", fake_ctx, {"type": "character"}, batch_waiter=fake_batch)

    # 李四 没有 description，作为 blocked 逐 ID 报告。
    result = read_generation_result(out)
    assert result.succeeded == ["character/张三"]
    assert result.blocked == ["character/李四"]


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"type": "character", "names": []}, id="empty-names"),
        pytest.param({"names": ["张三"]}, id="names-without-type"),
        pytest.param({"type": "character", "names": ["张三"], "all": True}, id="names-with-all"),
    ],
)
async def test_generate_assets_rejects_an_ambiguous_selection_before_enqueue(
    fake_ctx: ToolHarness, arguments: dict[str, Any]
) -> None:
    """含糊的选择是调用方错误，绝不能被当成「全部缺图资产」去扫全库付费。"""

    async def fail_batch(**_kwargs):
        raise AssertionError("含糊的选择不该入队任何任务")

    out = await run_declared_tool("generate_assets", fake_ctx, arguments, batch_waiter=fail_batch)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"


async def test_episode_scope_matches_the_web_episode_plan(tmp_path: Path) -> None:
    """Agent 按 episode_id 列出与生成的，与 Web 集层批量生成同一份规划。"""

    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    project_dir = pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_character("demo", "Alice", "勇敢的少女")
    pm.add_character("demo", "Bob", "沉默的剑客")
    pm.add_project_scene("demo", "客厅", "宽敞的客厅")
    pm.add_product("demo", "手机", "旗舰手机")

    def _derivative(project: dict) -> None:
        project["characters"]["Alice"]["derivatives"] = {"战损": {"description": "衣服破损"}}

    pm.update_project("demo", _derivative)
    (project_dir / "scripts").mkdir(exist_ok=True)
    script = {
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "characters_in_segment": ["Alice/战损"],
                "scenes": ["客厅"],
                "products_in_shot": ["手机"],
            }
        ],
    }
    (project_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    ctx = ToolHarness(project_name="demo", data_root=projects_root, pm=pm)

    listed = await run_declared_tool("list_pending_assets", ctx, {"episode_id": 1})
    submitted: list[tuple[str | None, str | None]] = []

    async def _capture_batch(*, specs, **_batch_kwargs):
        submitted.extend((spec.unit_id, spec.dependency_resource_id) for spec in specs)
        return [], []

    await run_declared_tool("generate_assets", ctx, {"episode_id": 1}, batch_waiter=_capture_batch)
    _project, plan = await load_asset_sheet_batch_plan(
        pm, ctx.queue, project_name="demo", scope=AssetSheetScope(episode_id=1), user_id=DEFAULT_USER_ID
    )

    assert isinstance(listed.value, str)
    assert "Bob" not in listed.value
    assert all(name in listed.value for name in ("Alice", "战损", "客厅", "手机"))
    assert [unit_id for unit_id, _dependency in submitted] == plan.target_ids
    assert submitted == [
        ("character/Alice", None),
        ("character/Alice/战损", "Alice"),
        ("scene/客厅", None),
        ("product/手机", None),
    ]


async def test_an_unknown_episode_is_reported_as_such(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    ctx = ToolHarness(project_name="demo", data_root=projects_root, pm=pm)

    out = await run_declared_tool("generate_assets", ctx, {"episode_id": 3})

    assert out.problem is not None
    assert out.problem.code == "episode_not_found"
