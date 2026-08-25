"""Tests for enqueue_videos batch admission."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import ArtifactStatus
from server.agent_runtime.sdk_tools._context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
    _generation_result,
    _reference_video_script,
    _use_reference_route,
    _videos_tool_for_scope,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")


def _episode_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "episode")


def _all_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "all")


def _selected_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "selected")


async def test_generate_videos_episode_scope_reports_an_interrupted_batch_enqueue_per_id(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """入队中断逐 ID 报告：建成的算 succeeded，没轮到的带「入队中断」问题码且未计费。"""
    from lib.generation_queue_client import BatchTaskResult
    from server.media_tools import videos as mod

    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": "E1S01", "novel_text": "第一段旁白。", "video_prompt": "第一镜"},
        {"segment_id": "E1S02", "novel_text": "第二段旁白。", "video_prompt": "第二镜"},
    ]
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        image = project_dir / "storyboards" / f"scene_{segment_id}.png"
        image.write_bytes(b"png")
        for item in fake_ctx.pm.script_payload["segments"]:  # type: ignore[attr-defined]
            if item["segment_id"] == segment_id:
                item["generated_assets"] = {"storyboard_image": f"storyboards/scene_{segment_id}.png"}

    async def _interrupted(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        success = BatchTaskResult(
            resource_id="E1S01",
            task_id="t1",
            status="succeeded",
            result={"file_path": "videos/E1S01.mp4"},
        )
        if on_success is not None:
            on_success(success)
        return (
            [success],
            [
                BatchTaskResult(
                    resource_id="E1S02",
                    task_id="",
                    status="failed",
                    error="queue unavailable",
                    enqueue_interrupted=True,
                )
            ],
        )

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _interrupted)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    payload = out["generation_result"]
    assert payload["succeeded"] == ["E1S01"]
    assert payload["failed"] == ["E1S02"]
    failed_item = next(item for item in payload["items"] if item["unit_id"] == "E1S02")
    assert failed_item["problem"]["code"] == "generation_enqueue_interrupted"
    assert failed_item["task_state"] == "not_queued"
    assert failed_item["task_id"] is None


async def test_generate_videos_episode_scope_batch_is_all_or_nothing_when_a_unit_is_occupied(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """在途任务冲突拦下整批：一个都不入队，其余 unit 报告自己是被谁扣下的。"""
    from server.media_tools import videos as mod

    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": "E1S01", "novel_text": "第一段旁白。", "video_prompt": "第一镜"},
        {"segment_id": "E1S02", "novel_text": "第二段旁白。", "video_prompt": "第二镜"},
    ]
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        image = project_dir / "storyboards" / f"scene_{segment_id}.png"
        image.write_bytes(b"png")
        for item in fake_ctx.pm.script_payload["segments"]:  # type: ignore[attr-defined]
            if item["segment_id"] == segment_id:
                item["generated_assets"] = {"storyboard_image": f"storyboards/scene_{segment_id}.png"}

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    async def _active(**_kwargs):
        return [{"resource_id": "E1S02", "id": "task-running", "status": "running"}]

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr("server.services.video_batch_admission.get_active_tasks_for_resources", _active)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out["is_error"] is True
    assert enqueued == []
    result = _generation_result(out)
    assert sorted(result.blocked) == ["E1S01", "E1S02"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S02"] == "generation_active_task_conflict"
    assert codes["E1S01"] == "generation_batch_admission_withheld"


async def test_generate_videos_all_scope_creates_zero_tasks_when_one_artifact_state_is_unreadable(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """产物状态读不出的场景属于这次请求：它带着自己的问题进准入，整批停下，健康的场景不入队计费。"""
    from dataclasses import replace as dc_replace

    from lib.artifact_manifest import ArtifactBlocker
    from lib.generation_result import GenerationCandidate, GenerationTargetState
    from server.media_tools import videos as mod

    fake_ctx.pm.script_payload["segments"].append(  # type: ignore[attr-defined]
        {
            "segment_id": "E1S02",
            "image_prompt": "山道清晨",
            "novel_text": "清晨的山道上落着薄雾。",
            "video_prompt": {"action": "镜头推近", "camera_motion": "Push", "ambiance_audio": "鸟鸣"},
            "duration_seconds": 4,
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )
    (fake_ctx.project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (fake_ctx.project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"\x89PNG")

    select_targets = mod.select_generation_targets

    def _one_unavailable(**kwargs: Any):
        selection = select_targets(**kwargs)
        blocked = GenerationTargetState(
            candidate=GenerationCandidate(unit_id="E1S02"),
            status=ArtifactStatus.BLOCKED,
            blocker=ArtifactBlocker(code="artifact_manifest_unreadable", path="", detail="侧车读不出"),
        )
        return dc_replace(
            selection,
            targets=tuple(state for state in selection.targets if state.unit_id != "E1S02"),
            unavailable=(blocked,),
        )

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "select_generation_targets", _one_unavailable)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_all_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("batch_admission") is not None, out
    assert out["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes["E1S02"] == ["generation_artifact_state_unavailable"]
    assert codes["E1S01"] == ["generation_batch_admission_withheld"]


async def test_generate_videos_all_scope_admits_legacy_narration_stored_under_scenes(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """narration 数据落在 scenes 键的历史剧本按实际骨架做发声准入，不被整批判成解析失败。"""
    from server.media_tools import videos as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "video_prompt": {
                    "action": "阿离转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "张三", "line": "跟紧我。"}],
                },
                "voiceover": [],
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }
    (fake_ctx.project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (fake_ctx.project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"\x89PNG")

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id=f"t-{spec.resource_id}",
                status="succeeded",
                result={"file_path": f"videos/{spec.resource_id}.mp4"},
            )
            for spec in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(_all_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert list(result.succeeded) == ["E1S01"]


async def test_generate_videos_all_scope_reports_an_all_unreadable_selection_as_blocked(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全部目标的产物状态都读不出时不能报成空的成功：那会把每一条状态问题都藏起来。"""
    from dataclasses import replace as dc_replace

    from lib.artifact_manifest import ArtifactBlocker
    from lib.generation_result import GenerationCandidate, GenerationTargetState
    from server.media_tools import videos as mod

    select_targets = mod.select_generation_targets

    def _all_unavailable(**kwargs: Any):
        selection = select_targets(**kwargs)
        blocked = GenerationTargetState(
            candidate=GenerationCandidate(unit_id="E1S01"),
            status=ArtifactStatus.BLOCKED,
            blocker=ArtifactBlocker(code="artifact_manifest_unreadable", path="", detail="侧车读不出"),
        )
        return dc_replace(selection, targets=(), unavailable=(blocked,))

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "select_generation_targets", _all_unavailable)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_all_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("batch_admission") is not None, out
    assert out["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes == {"E1S01": ["generation_artifact_state_unavailable"]}


async def test_generate_reference_episode_refuses_a_non_scalar_unit_id(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整集参考生成遇到非标量 unit_id：它按位置记名拒收，健康的兄弟条目不会独自入队计费。"""
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    script = _reference_video_script()
    healthy = script["video_units"][0]
    script["video_units"] = [{**healthy, "unit_id": ["U9"]}, healthy]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert healthy["unit_id"] in codes


async def test_generate_reference_units_refuses_a_duplicated_named_unit(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名的 unit 在剧本里有两份：无从判定要做哪一条，整批停在建任务之前。"""
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    script = _reference_video_script()
    script["video_units"] = [*script["video_units"], {**script["video_units"][0]}]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    duplicated_id = script["video_units"][0]["unit_id"]
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        _selected_scope(fake_ctx),
        {"script": "episode_1.json", "scene_ids": [duplicated_id]},
    )

    enqueue.assert_not_awaited()
    codes = {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]] for unit in out["batch_admission"]["units"]
    }
    assert codes == {duplicated_id: ["generation_unit_request_invalid"]}
