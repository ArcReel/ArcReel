"""generate_storyboards handler 的 ``ToolOutcome``。"""

from __future__ import annotations

from typing import Any

from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    activate_unbound_project,
    read_generation_result,
    run_declared_tool,
)


class TestBuildPrompt:
    def test_structured_no_duplicate_style(self) -> None:
        from server.media_tools.storyboards import _build_prompt

        segment = {
            "segment_id": "E1S01",
            "image_prompt": {
                "scene": "村口黄昏",
                "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
            },
        }
        out = _build_prompt(segment, "真人电视剧风格", "Soft light", "segment_id")

        assert out.count("Style:") == 1
        assert "Style: 真人电视剧风格" in out
        assert out.startswith("Style: 真人电视剧风格\nVisual style: Soft light")

    def test_unstructured_prompt_keeps_one_style_line(self) -> None:
        from server.media_tools.storyboards import _build_prompt

        segment = {"segment_id": "E1S02", "image_prompt": "村口黄昏的长镜头"}
        out = _build_prompt(segment, "真人电视剧风格", "", "segment_id")

        assert out.count("Style:") == 1
        assert out.startswith("Style: 真人电视剧风格")
        assert "\n\n村口黄昏的长镜头\n\n" in out
        assert out.endswith("\n\nAvoid: 水印、多余文字、Logo")


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


async def test_generate_storyboards_happy(fake_ctx: ToolContext) -> None:
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


async def test_generate_storyboards_rejects_unbound_active_script_before_enqueue(fake_ctx: ToolContext) -> None:
    activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}

    async def unreachable_batch(**_batch_kwargs):
        raise AssertionError("未绑定的剧本不该走到入队")

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_batch
    )

    assert out.problem is not None
    assert "not bound" in out.problem.detail


async def test_generate_storyboards_selects_item_with_corrupt_generated_assets(fake_ctx: ToolContext) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = "corrupt"

    out = await run_declared_tool(
        "generate_storyboards", fake_ctx, {"script": "episode_1.json"}, batch_waiter=_succeed_every_spec
    )

    assert read_generation_result(out).succeeded == ["E1S01"]


async def test_generate_storyboards_blocks_an_unregistered_reference(fake_ctx: ToolContext) -> None:
    """agent 入口与 Web 提交同判：未登记的引用阻断这条分镜，不建任务、不计费。"""

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


async def test_generate_storyboards_rejects_mismatched_unit_script(fake_ctx: ToolContext) -> None:
    """失配剧本不能落进"✨ 所有分镜的分镜图都已生成"的假成功——报结构错误并指引重拆。"""
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }

    out = await run_declared_tool("generate_storyboards", fake_ctx, {"script": "episode_1.json"})

    assert out.problem is not None
    assert "骨架" in out.problem.detail
    assert "重新拆分" in out.problem.detail


async def test_generate_storyboards_error(fake_ctx: ToolContext) -> None:
    def boom(*args, **kwargs):
        raise ValueError("bad script")

    fake_ctx.pm.load_script = boom

    out = await run_declared_tool("generate_storyboards", fake_ctx, {"script": "episode_1.json"})

    assert out.problem is not None
    assert out.problem.code == "internal_error"


async def test_generate_storyboards_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    """Agent 传带路径分隔符的 script 名在请求校验即被拒绝。"""
    out = await run_declared_tool("generate_storyboards", fake_ctx, {"script": "../etc/passwd"})

    assert out.problem is not None
    assert out.problem.code == "invalid_request"


async def test_generate_storyboards_blocks_only_the_entry_whose_prompt_is_pending(fake_ctx: ToolContext) -> None:
    """机械转换出的条目 image_prompt 为 None：逐条阻断该分镜、不计费，其余分镜照常入队。"""
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
