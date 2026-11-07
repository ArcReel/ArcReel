"""generate_storyboards handler ``ToolOutcome``."""

from __future__ import annotations

from typing import Any

from tests.integration.server.agent_tool_support import (
    ToolHarness,
    activate_unbound_project,
    read_generation_result,
    run_declared_tool,
)


async def _succeed_every_spec(*, specs, **_batch_kwargs):
    from lib.generation.generation_queue_client import BatchTaskResult

    return [
        BatchTaskResult(
            resource_id=spec.resource_id,
            task_id="t1",
            status="succeeded",
            result={"file_path": f"storyboards/scene_{spec.resource_id}.png"},
        )
        for spec in specs
    ], []


async def test_generate_storyboards_happy(fake_ctx: ToolHarness) -> None:
    captured: list[Any] = []

    async def fake_batch(*, specs, **batch_kwargs):
        captured.extend(specs)
        return await _succeed_every_spec(specs=specs, **batch_kwargs)

    # Strip storyboard_image to force selection
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}
    semantic_prompt = {
        "scene": "村口黄昏",
        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
    }
    fake_ctx.pm.script_payload["segments"][0]["image_prompt"] = semantic_prompt

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=fake_batch
    )

    assert read_generation_result(out).succeeded == ["E1S01"]
    assert captured[0].payload["prompt"] == semantic_prompt


async def test_generate_storyboards_rejects_unbound_active_script_before_enqueue(fake_ctx: ToolHarness) -> None:
    activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}

    async def unreachable_batch(**_batch_kwargs):
        raise AssertionError("未绑定的剧本不该走到入队")

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_batch
    )

    assert out.problem is not None
    assert "not bound" in out.problem.detail


async def test_generate_storyboards_selects_item_with_corrupt_generated_assets(fake_ctx: ToolHarness) -> None:
    """Nondict corrupt generated_assets, such as strings, counts as missing without AttributeError."""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = "corrupt"

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=_succeed_every_spec
    )

    assert read_generation_result(out).succeeded == ["E1S01"]


async def test_generate_storyboards_blocks_an_unregistered_reference(fake_ctx: ToolHarness) -> None:
    """Agent/Web submissions agree: unregistered references block that storyboard without tasks or charges."""

    async def unreachable_batch(**_batch_kwargs):
        raise AssertionError("引用有缺口时不该走到入队")

    fake_ctx.pm.script_payload["segments"][0]["characters_in_segment"] = ["无名氏"]
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_batch
    )

    result = read_generation_result(out)
    assert result.blocked == ["E1S01"]
    problem = next(item for item in result.items if item.unit_id == "E1S01").problem
    assert problem is not None
    assert (problem.code, problem.action) == ("reference_asset_unregistered", "generate_dependency")
    assert problem.params["missing_text"] == "无名氏"


async def test_generate_storyboards_rejects_mismatched_unit_script(fake_ctx: ToolHarness) -> None:
    """Mismatched scripts must not falsely report ✨ 所有分镜的分镜图都已生成; return structural errors and resplitting guidance."""
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }

    out = await run_declared_tool("generate_storyboards", fake_ctx, {"script": "episode_1.json"})

    assert out.problem is not None
    assert "骨架" in out.problem.detail
    assert "重新拆分" in out.problem.detail


async def test_generate_storyboards_error(fake_ctx: ToolHarness) -> None:
    def boom(*args, **kwargs):
        raise ValueError("bad script")

    fake_ctx.pm.load_script = boom

    out = await run_declared_tool("generate_storyboards", fake_ctx, {"script": "episode_1.json"})

    assert out.problem is not None
    assert out.problem.code == "internal_error"


async def test_generate_storyboards_rejects_path_in_script_arg(fake_ctx: ToolHarness) -> None:
    """Agent script names containing path separators reject during request validation."""
    out = await run_declared_tool("generate_storyboards", fake_ctx, {"script": "../etc/passwd"})

    assert out.problem is not None
    assert out.problem.code == "invalid_request"


async def test_generate_storyboards_blocks_only_the_entry_whose_prompt_is_pending(fake_ctx: ToolHarness) -> None:
    """Mechanically derived image_prompt=None blocks individual storyboards without charges; valid siblings enqueue normally."""
    enqueued: list[str] = []

    async def fake_batch(*, specs, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}
    fake_ctx.pm.script_payload["segments"].append(
        {
            "segment_id": "E1S02",
            "image_prompt": None,
            "novel_text": "他停下脚步。",
            "video_prompt": None,
            "duration_seconds": 4,
            "generated_assets": {},
        }
    )

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=fake_batch
    )

    result = read_generation_result(out)
    assert result.blocked == ["E1S02"]
    assert enqueued == ["E1S01"]
    problem = next(item for item in result.items if item.unit_id == "E1S02").problem
    assert problem is not None
    assert (problem.code, problem.action) == ("generation_unit_request_invalid", "fix_input")
    assert "E1S02" in problem.detail
