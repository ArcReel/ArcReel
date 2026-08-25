"""Tests for enqueue_assets."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.media_tools.assets import (
    generate_assets_tool,
    list_pending_assets_tool,
)
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
    _generation_result,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

# ---------------------------------------------------------------------------
# enqueue_assets
# ---------------------------------------------------------------------------


async def test_list_pending_assets_happy(fake_ctx: ToolContext) -> None:
    tool_obj = list_pending_assets_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "张三" in text
    assert "村口" in text
    assert "保温杯" in text


async def test_pending_asset_tools_include_an_unclaimed_schema8_sheet(tmp_path: Path, monkeypatch) -> None:
    from server.media_tools import assets as mod

    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    project_dir = pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_scene("demo", "客厅", "宽敞的客厅")
    pm.update_scene_sheet("demo", "客厅", "scenes/客厅.png")
    (project_dir / "scenes" / "客厅.png").write_bytes(b"png")
    ctx = ToolContext(project_name="demo", projects_root=projects_root, pm=pm)

    listed = await _call(list_pending_assets_tool(ctx), {"type": "scene"})

    assert "客厅" in listed["content"][0]["text"]

    enqueued: list[str] = []

    async def _capture_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _capture_batch)

    await _call(generate_assets_tool(ctx), {"type": "scene"})

    assert enqueued == ["客厅"]


async def test_list_pending_assets_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(_name):
        raise RuntimeError("db down")

    fake_ctx.pm.get_pending_characters = boom  # type: ignore[attr-defined]
    tool_obj = list_pending_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"type": "character"})
    assert out.get("is_error") is True


async def test_generate_assets_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import assets as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 1},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = generate_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"type": "character"})
    # 李四 没有 description，作为 blocked 逐 ID 报告；缺口存在时整体判为 error，
    # 调用方不需要读文本就知道哪几个 ID 还没做成。
    result = _generation_result(out)
    assert result.succeeded == ["character/张三"]
    assert result.blocked == ["character/李四"]
    assert sorted(result.requested) == sorted(result.succeeded + result.blocked)
    assert out.get("is_error") is True


async def test_generate_assets_legacy_project_reverifies_sheet_file_on_disk(fake_ctx: ToolContext, monkeypatch) -> None:
    """预激活 Manifest 的旧项目：metadata 记了 sheet 路径但文件已被删/挪走时，
    missing-only 不能只信 metadata 就把它当复用，否则永远生不出真正缺失的资产图。"""
    from server.media_tools import assets as mod

    # 未设置当前 schema：resolver 走 legacy 分支（没有 active Manifest）。
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["李四"]["character_sheet"] = "characters/lisi.png"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["李四"]["description"] = "配角"  # type: ignore[attr-defined]
    # 只有张三的文件真的落盘；李四的 sheet 路径是失效元数据。
    sheet_path = fake_ctx.project_path / "characters" / "zhangsan.png"
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_path.write_bytes(b"fake-png")

    enqueued: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        enqueued.extend(s.resource_id for s in specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 1},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    out = await _call(generate_assets_tool(fake_ctx), {"type": "character"})

    result = _generation_result(out)
    # 张三：文件真实存在，missing-only 复用旧图，不重新生成。
    assert "张三" not in enqueued
    assert [entry.unit_id for entry in result.skipped] == ["character/张三"]
    # 李四：metadata 指向的文件已经不存在，必须被当作缺口重新生成，而不是静默复用。
    assert enqueued == ["李四"]
    assert result.succeeded == ["character/李四"]


async def test_generate_assets_rejects_an_explicitly_empty_name_list(fake_ctx: ToolContext, monkeypatch) -> None:
    """``names: []`` 是调用方错误，绝不能被当成「全部缺图资产」去扫全库付费。"""
    from server.media_tools import assets as mod

    async def fail_batch(**_kwargs):
        raise AssertionError("空选择不该入队任何任务")

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fail_batch)

    out = await _call(generate_assets_tool(fake_ctx), {"type": "character", "names": []})

    assert out.get("is_error") is True
    assert "不能为空数组" in out["content"][0]["text"]


async def test_generate_assets_names_without_type(fake_ctx: ToolContext) -> None:
    tool_obj = generate_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"names": ["张三"]})
    assert out.get("is_error") is True
