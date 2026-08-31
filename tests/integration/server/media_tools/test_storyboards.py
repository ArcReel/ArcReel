"""Tests for enqueue_storyboards."""

from __future__ import annotations

from typing import Any

from server.media_tools.context import ToolContext
from server.media_tools.storyboards import generate_storyboards_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    activate_unbound_project,
    call,
    read_generation_result,
)

# ---------------------------------------------------------------------------
# enqueue_storyboards
# ---------------------------------------------------------------------------


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
        out = _build_prompt(segment, "画风：真人电视剧风格", "Soft light", "segment_id")

        assert out.count("Style:") == 1
        assert "画风：" not in out
        assert "Style: 真人电视剧风格" in out
        assert out.startswith("Visual style: Soft light")

    def test_unstructured_keeps_style_prefix_normalized(self) -> None:
        from server.media_tools.storyboards import _build_prompt

        segment = {"segment_id": "E1S02", "image_prompt": "村口黄昏的长镜头"}
        out = _build_prompt(segment, "画风：真人电视剧风格", "", "segment_id")

        assert out.count("Style:") == 1
        assert "画风：" not in out
        assert out.startswith("Style: 真人电视剧风格")
        assert "\n\n村口黄昏的长镜头\n\n" in out
        assert out.endswith("画面避免：水印、多余文字、Logo。")


async def test_generate_storyboards_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import storyboards as mod

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
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}
    semantic_prompt = {
        "scene": "村口黄昏",
        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
    }
    fake_ctx.pm.script_payload["segments"][0]["image_prompt"] = semantic_prompt
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    assert captured[0].payload["prompt"] == semantic_prompt


async def test_generate_storyboards_legacy_project_reverifies_image_file_on_disk(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """预激活 Manifest 的旧项目：剧本登记了分镜图路径但文件不在磁盘上时判为缺口重生；
    文件真在时照旧复用，不重复付费。"""
    from server.media_tools import storyboards as mod

    # E1S01 的分镜图由 fixture 落在磁盘上；E1S02 只在剧本里登记路径，文件并不存在。
    fake_ctx.pm.script_payload["segments"].append(
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

    out = await call(generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert enqueued == ["E1S02"]
    assert result.succeeded == ["E1S02"]
    assert [entry.unit_id for entry in result.skipped] == ["E1S01"]


async def test_generate_storyboards_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import storyboards as mod

    activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}
    enqueued = False

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        nonlocal enqueued
        enqueued = True
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await call(mod.generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert enqueued is False


async def test_generate_storyboards_selects_item_with_corrupt_generated_assets(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    from server.media_tools import storyboards as mod

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
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = "corrupt"
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]


async def test_generate_storyboards_rejects_mismatched_unit_script(fake_ctx: ToolContext) -> None:
    """失配剧本不能落进"✨ 所有分镜的分镜图都已生成"的假成功——报结构错误并指引重拆。"""
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text
    assert "重新拆分" in text


async def test_generate_storyboards_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ValueError("bad script")

    fake_ctx.pm.load_script = boom
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


async def test_generate_storyboards_reports_a_partial_batch_per_id(fake_ctx: ToolContext, monkeypatch) -> None:
    """一批里有成有败时逐 ID 分账，失败项带稳定 code，不需要读文本判断重试。"""
    from server.media_tools import storyboards as mod

    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": "E1S01", "image_prompt": "村口黄昏", "generated_assets": {}},
        {"segment_id": "E1S02", "image_prompt": "山道清晨", "generated_assets": {}},
    ]

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult
        from lib.task_failure import encode_failure

        succeeded = [
            BatchTaskResult(
                resource_id="E1S01",
                task_id="t1",
                status="succeeded",
                result={"file_path": "storyboards/scene_E1S01.png"},
                task={"provider_id": "openai", "provider_job_id": "job-1"},
            )
        ]
        failed = [
            BatchTaskResult(
                resource_id="E1S02",
                task_id="t2",
                status="failed",
                error=encode_failure("video_capability_missing_i2v"),
                task={"provider_id": "openai", "provider_job_id": None},
            )
        ]
        return succeeded, failed

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await call(generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    result = read_generation_result(out)
    assert result.succeeded == ["E1S01"]
    assert result.failed == ["E1S02"]
    assert set(result.requested) == {"E1S01", "E1S02"}
    failed_item = next(item for item in result.items if item.unit_id == "E1S02")
    assert failed_item.problem is not None
    assert failed_item.problem.code == "video_capability_missing_i2v"
    assert failed_item.problem.action.value == "configure_provider"
    # 供应商提交与任务状态分开报告：这一条任务失败但从未提交给供应商。
    assert failed_item.task_state.value == "failed"
    assert failed_item.provider_checkpoint is not None
    assert failed_item.provider_checkpoint.submitted is False


async def test_generate_storyboards_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    """Agent 传带路径分隔符的 script 名必须被 handler 拒绝（共享 validate_script_filename 防御）。"""
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]
