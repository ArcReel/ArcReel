"""Tests for enqueue_storyboards."""

from __future__ import annotations

from typing import Any

import pytest

from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _activate_unbound_project,
    _call,
    _generation_result,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

# ---------------------------------------------------------------------------
# enqueue_storyboards
# ---------------------------------------------------------------------------


async def test_generate_storyboards_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{s.resource_id}.png"},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    # Strip storyboard_image to force selection
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}  # type: ignore[attr-defined]
    semantic_prompt = {
        "scene": "村口黄昏",
        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
    }
    fake_ctx.pm.script_payload["segments"][0]["image_prompt"] = semantic_prompt  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    assert captured[0].payload["prompt"] == semantic_prompt


async def test_generate_storyboards_legacy_project_reverifies_image_file_on_disk(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """预激活 Manifest 的旧项目：剧本登记了分镜图路径但文件不在磁盘上时判为缺口重生；
    文件真在时照旧复用，不重复付费。"""
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    # E1S01 的分镜图由 fixture 落在磁盘上；E1S02 只在剧本里登记路径，文件并不存在。
    fake_ctx.pm.script_payload["segments"].append(  # type: ignore[attr-defined]
        {
            "segment_id": "E1S02",
            "image_prompt": "村口清晨",
            "novel_text": "清晨的村口。",
            "video_prompt": {"action": "镜头平移", "camera_motion": "Pan", "ambiance_audio": "鸟鸣"},
            "duration_seconds": 4,
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )

    enqueued: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        enqueued.extend(spec.resource_id for spec in specs)
        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{spec.resource_id}.png"},
            )
            for spec in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert enqueued == ["E1S02"]
    assert result.succeeded == ["E1S02"]
    assert [entry.unit_id for entry in result.skipped] == ["E1S01"]


async def test_generate_storyboards_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    _activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}  # type: ignore[attr-defined]
    enqueued = False

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        nonlocal enqueued
        enqueued = True
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(mod.generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert enqueued is False


async def test_generate_storyboards_selects_item_with_corrupt_generated_assets(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{s.resource_id}.png"},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = "corrupt"  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]


async def test_generate_storyboards_rejects_mismatched_unit_script(fake_ctx: ToolContext) -> None:
    """失配剧本不能落进"✨ 所有分镜的分镜图都已生成"的假成功——报结构错误并指引重拆。"""
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text and "重新拆分" in text


async def test_generate_storyboards_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ValueError("bad script")

    fake_ctx.pm.load_script = boom  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True
