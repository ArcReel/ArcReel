"""generate_narration_audio handler ``ToolOutcome``."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.server.agent_tool_support import (
    ToolHarness,
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
    """Embedded batch-wait substitute records specs and reports success per item."""

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


@pytest.fixture(autouse=True)
def tts_project(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.project_payload.update(
        {"narration_delivery": "use_tts", "audio_backend": "dashscope/qwen3-tts-flash", "narration_voice": "Cherry"}
    )


async def _generate(fake_ctx: ToolHarness, arguments: dict[str, Any], batch: Any = None):
    return await run_declared_tool(
        "generate_narration_audio", fake_ctx, arguments, batch_waiter=batch or _CapturingBatch()
    )


async def test_generate_narration_audio_enqueues_missing_segments(fake_ctx: ToolHarness) -> None:
    """Absent segment_ids queues TTS only for missing narration_audio, leaving synthesis text for workers to read."""
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


@pytest.mark.parametrize(
    ("project_patch", "code"),
    [
        ({"narration_delivery": "post_production"}, "narration_delivery_post_production"),
        ({"narration_voice": ""}, "narration_tts_voice_required"),
    ],
)
async def test_generate_narration_audio_requires_a_tts_project_with_complete_snapshot(
    fake_ctx: ToolHarness, project_patch: dict[str, Any], code: str
) -> None:
    fake_ctx.pm.script_payload = _narration_audio_script()
    fake_ctx.pm.project_payload.update(project_patch)
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert out.problem is not None
    assert out.problem.code == code
    assert batch.resource_ids == []


def _drama_voiceover(fake_ctx: ToolHarness, *, script_declares_mode: bool) -> None:
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


def _reference_narrator_unit(fake_ctx: ToolHarness) -> None:
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
    fake_ctx: ToolHarness, arrange: Any, expected: str
) -> None:
    """Entry selects narrator-owned speech units by current skeleton, across all content/generation modes."""
    arrange(fake_ctx)
    batch = _CapturingBatch()

    await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == [expected]
    assert batch.specs[0].task_type == "tts"


async def test_generate_narration_audio_rejects_unbound_active_script_before_enqueue(fake_ctx: ToolHarness) -> None:
    activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert out.problem is not None
    assert "not bound" in out.problem.detail
    assert batch.specs == []


async def test_generate_narration_audio_selects_item_with_corrupt_generated_assets(fake_ctx: ToolHarness) -> None:
    """Nondict corrupt generated_assets, such as strings, counts as missing without AttributeError."""
    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = "corrupt"
    fake_ctx.pm.script_payload = script
    batch = _CapturingBatch()

    await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == ["E1S01"]


async def test_generate_narration_audio_explicit_ids_regenerate(fake_ctx: ToolHarness) -> None:
    """Explicit segment_ids requeue even existing narration_audio, matching batch-scope/single-segment regeneration semantics."""
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    await _generate(fake_ctx, {"script": "episode_1.json", "segment_ids": ["E1S02"]}, batch)

    assert batch.resource_ids == ["E1S02"]


async def test_generate_narration_audio_blank_text_reported(fake_ctx: ToolHarness) -> None:
    """Blank novel_text must not silently vanish: scans treat it as no gap; explicit requests report errors."""
    script = _narration_audio_script()
    script["segments"].append({"segment_id": "E1S03", "novel_text": "   ", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script

    # Scans exclude blank segments from requested without blocking siblings.
    batch = _CapturingBatch()
    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)
    assert batch.resource_ids == ["E1S01"]
    assert "E1S03" not in read_generation_result(out).requested

    # Explicit blank segments report blocked with stable codes/actions.
    batch = _CapturingBatch()
    out = await _generate(fake_ctx, {"script": "episode_1.json", "segment_ids": ["E1S03"]}, batch)
    assert batch.specs == []
    result = read_generation_result(out)
    assert result.requested == ["E1S03"]
    assert result.blocked == ["E1S03"]
    problem = result.items[0].problem
    assert problem is not None
    # Expose speech-admission codes unchanged; callers need no text parsing for next steps.
    assert problem.code == "parse_failed"
    assert problem.action.value == "fix_input"


async def test_generate_narration_audio_partial_unmatched_reported(fake_ctx: ToolHarness) -> None:
    """Missing IDs do not silently disappear: enqueue matches normally and report each unmatched ID blocked."""
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


async def test_generate_narration_audio_rejects_mismatched_script(fake_ctx: ToolHarness) -> None:
    """video_units in storyboard-image-to-video projects report structural errors/resplitting guidance without silent route changes."""
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
        # Bare strings iterate characters into sets such as E/1/S; reject explicitly.
        pytest.param({"script": "episode_1.json", "segment_ids": "E1S01"}, id="string-segment-ids"),
        pytest.param({"script": "episode_1.json", "segment_ids": []}, id="empty-segment-ids"),
        pytest.param({"script": "../etc/passwd"}, id="path-in-script"),
    ],
)
async def test_generate_narration_audio_rejects_a_malformed_request(
    fake_ctx: ToolHarness, arguments: dict[str, Any]
) -> None:
    fake_ctx.pm.script_payload = _narration_audio_script()
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, arguments, batch)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    assert batch.specs == []


async def test_generate_narration_audio_skips_segment_without_id(fake_ctx: ToolHarness) -> None:
    """Storyboards without segment_id are unaddressable and outside the contract, without aborting valid sibling enqueue."""
    script = _narration_audio_script()
    # Both lack voiceovers; test missing-ID addressability without mixing existing-voiceover reuse.
    script["segments"][1]["generated_assets"] = {}
    script["segments"].append({"novel_text": "有文本但缺 id 的片段。", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script
    batch = _CapturingBatch()

    out = await _generate(fake_ctx, {"script": "episode_1.json"}, batch)

    assert batch.resource_ids == ["E1S01", "E1S02"]
    assert read_generation_result(out).requested == ["E1S01", "E1S02"]


async def test_generate_narration_audio_task_failures_surface(fake_ctx: ToolHarness) -> None:
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
