"""generate_narration_audio handler 的 ``ToolOutcome``。"""

from __future__ import annotations

from typing import Any

import pytest

from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    activate_unbound_project,
    read_generation_result,
    run_declared_tool,
)


def _narration_audio_script() -> dict[str, Any]:
    return {
        "content_mode": "narration",
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "却说天下大势，分久必合。",
                "video_prompt": {},
                "generated_assets": {},
            },
            {
                "segment_id": "E1S02",
                "novel_text": "话说周末七国分争。",
                "video_prompt": {},
                "generated_assets": {"narration_audio": "audio/segment_E1S02.wav"},
            },
        ],
    }


class _CapturingBatch:
    """内嵌批次等待器替身：记下入队的 spec，逐个报告成功。"""

    def __init__(self) -> None:
        self.specs: list[Any] = []

    @property
    def resource_ids(self) -> list[str]:
        return [spec.resource_id for spec in self.specs]

    async def __call__(self, *, specs, **_batch_kwargs):
        from lib.generation.generation_queue_client import BatchTaskResult

        self.specs.extend(specs)
        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"audio/segment_{spec.resource_id}.wav"},
            )
            for spec in specs
        ], []


async def _generate(fake_ctx: ToolContext, arguments: dict[str, Any], batch: Any = None):
    return await run_declared_tool(
        "generate_narration_audio", fake_ctx, arguments, batch_waiter=batch or _CapturingBatch()
    )


async def test_generate_narration_audio_enqueues_missing_segments(fake_ctx: ToolContext) -> None:
    """不传 segment_ids → 只为缺 narration_audio 的段入队 tts 任务，合成文本留给 worker 读取。"""
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == ["E1S01"]
    spec = batch.specs[0]
    assert spec.task_type == "tts"
    assert spec.media_type == "audio"
    assert spec.payload == {"prompt": None, "script_file": "episode_1.json"}
    result = read_generation_result(out)
    assert result.succeeded == ["E1S01"]
    assert result.items[0].artifact_path == "audio/segment_E1S01.wav"


def _drama_voiceover(fake_ctx: ToolContext, *, script_declares_mode: bool) -> None:
    fake_ctx.pm.project_payload["content_mode"] = "drama"
    script: dict[str, Any] = {
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "utterances": [{"kind": "voiceover", "speaker": None, "text": "夜幕降临。"}],
                "generated_assets": {},
            }
        ],
    }
    if script_declares_mode:
        script["content_mode"] = "drama"
    fake_ctx.pm.script_payload = script


def _reference_narrator_unit(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "海面\n{风从远方吹来。}",
                "duration_seconds": 8,
                "generated_assets": {},
            }
        ],
    }


@pytest.mark.parametrize(
    ("arrange", "expected"),
    [
        pytest.param(lambda ctx: _drama_voiceover(ctx, script_declares_mode=True), "E1S01", id="drama-scene"),
        pytest.param(
            lambda ctx: _drama_voiceover(ctx, script_declares_mode=False), "E1S01", id="drama-mode-from-project"
        ),
        pytest.param(_reference_narrator_unit, "E1U1", id="reference-video-unit"),
    ],
)
async def test_generate_narration_audio_takes_the_narrator_unit_of_every_skeleton(
    fake_ctx: ToolContext, arrange: Any, expected: str
) -> None:
    """入口按当前骨架取 narrator 拥有发声的单元，不限内容模式与生成模式。"""
    arrange(fake_ctx)
    batch = _CapturingBatch()

    await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == [expected]
    assert batch.specs[0].task_type == "tts"


async def test_generate_narration_audio_rejects_unbound_active_script_before_enqueue(fake_ctx: ToolContext) -> None:
    activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert out.problem is not None
    assert "not bound" in out.problem.detail
    assert batch.specs == []


async def test_generate_narration_audio_selects_item_with_corrupt_generated_assets(fake_ctx: ToolContext) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = "corrupt"
    fake_ctx.pm.script_payload = script
    batch = _CapturingBatch()

    await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == ["E1S01"]


async def test_generate_narration_audio_explicit_ids_regenerate(fake_ctx: ToolContext) -> None:
    """传 segment_ids → 即使该段已有 narration_audio 也重新入队（批量范围/单段重生语义）。"""
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    await _generate(fake_ctx, {"script": "episode_1.json", "segment_ids": ["E1S02"]}, batch)

    assert batch.resource_ids == ["E1S02"]


async def test_generate_narration_audio_blank_text_reported(fake_ctx: ToolContext) -> None:
    """novel_text 空白的段不能静默丢弃：扫描时不算缺口，显式点名时按错误上报。"""
    script = _narration_audio_script()
    script["segments"].append({"segment_id": "E1S03", "novel_text": "   ", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script

    # 扫描模式：空白段根本不是缺口，不进 requested，也不阻塞其余段
    batch = _CapturingBatch()
    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)
    assert batch.resource_ids == ["E1S01"]
    assert "E1S03" not in read_generation_result(out).requested

    # 显式点名空白段：该段按 blocked 上报，带稳定 code 与下一步动作
    batch = _CapturingBatch()
    out = await _generate(fake_ctx, {"script": "episode_1.json", "segment_ids": ["E1S03"]}, batch)
    assert batch.specs == []
    result = read_generation_result(out)
    assert result.requested == ["E1S03"]
    assert result.blocked == ["E1S03"]
    problem = result.items[0].problem
    assert problem is not None
    # 发声准入自己的问题码原样透出，调用方不必读文本判断下一步。
    assert problem.code == "parse_failed"
    assert problem.action.value == "fix_input"


async def test_generate_narration_audio_partial_unmatched_reported(fake_ctx: ToolContext) -> None:
    """部分 id 不命中不能静默丢弃：命中的照常入队，未命中的按 blocked 逐 ID 上报。"""
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json", "segment_ids": ["E1S01", "E1S99"]}, batch)

    assert batch.resource_ids == ["E1S01"]
    result = read_generation_result(out)
    assert sorted(result.requested) == ["E1S01", "E1S99"]
    assert result.succeeded == ["E1S01"]
    assert result.blocked == ["E1S99"]
    unmatched = next(item for item in result.items if item.unit_id == "E1S99")
    assert unmatched.problem is not None
    assert unmatched.problem.code == "generation_unit_not_found"


async def test_generate_narration_audio_rejects_mismatched_script(fake_ctx: ToolContext) -> None:
    """分镜图生视频项目下的 video_units 骨架剧本：结构报错 + 重拆指引，不静默换路径。"""
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }

    out = await _generate(fake_ctx, {"script": "episode_1.json"})

    assert out.problem is not None
    assert "骨架" in out.problem.detail
    assert "重新拆分" in out.problem.detail


@pytest.mark.parametrize(
    "arguments",
    [
        # 裸字符串会被逐字符迭代成 {'E','1','S'...}，必须显式拒绝。
        pytest.param({"script": "episode_1.json", "segment_ids": "E1S01"}, id="string-segment-ids"),
        pytest.param({"script": "episode_1.json", "segment_ids": []}, id="empty-segment-ids"),
        pytest.param({"script": "../etc/passwd"}, id="path-in-script"),
    ],
)
async def test_generate_narration_audio_rejects_a_malformed_request(
    fake_ctx: ToolContext, arguments: dict[str, Any]
) -> None:
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, arguments, batch)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    assert batch.specs == []


async def test_generate_narration_audio_skips_segment_without_id(fake_ctx: ToolContext) -> None:
    """缺 segment_id 的分镜不能让整批中断：无 ID 可寻址故不进契约，其余分镜照常入队。"""
    script = _narration_audio_script()
    # 两个分镜都缺配音：本用例的主题是无 ID 分镜的可寻址性，不掺入已有配音的复用判定。
    script["segments"][1]["generated_assets"] = {}
    script["segments"].append({"novel_text": "有文本但缺 id 的片段。", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == ["E1S01", "E1S02"]
    assert read_generation_result(out).requested == ["E1S01", "E1S02"]


async def test_generate_narration_audio_task_failures_surface(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.script_payload = _narration_audio_script()

    async def fake_batch(*, specs, **_batch_kwargs):
        from lib.generation.generation_queue_client import BatchTaskResult

        return [], [
            BatchTaskResult(resource_id=spec.resource_id, task_id="t1", status="failed", error="provider down")
            for spec in specs
        ]

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, fake_batch)

    result = read_generation_result(out)
    assert result.failed == ["E1S01"]
    problem = result.items[0].problem
    assert problem is not None
    assert "provider down" in problem.detail
