"""generate_videos 的整批准入：全有或全无、逐单元结论与按调用方隔离的在途任务。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.db.models.user import User
from server.tool_runtime import CallerContext, ToolOutcome
from tests.factories import make_video_request_facts
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    fake_reference_projection,
    read_generation_result,
    reference_video_script,
    run_generate_videos,
    use_reference_route,
)


@pytest.fixture(autouse=True)
def storyboard_request_facts(set_admission_video_request_facts) -> None:
    facts = make_video_request_facts(provider_id="fake", model_id="fake-video", audio_switch_controllable=True)
    set_admission_video_request_facts(facts)


_EPISODE_1 = {"scope": "episode", "episode_id": 1}
_ALL = {"scope": "all"}
# 越出项目根的成片路径：清单无从检查这份产物，它的状态既不是「缺失」也不是「可用」。
_UNREADABLE_CLIP = "../outside/E1S02.mp4"


def _admission_codes(out: ToolOutcome[Any]) -> dict[str, list[str]]:
    assert isinstance(out.value, dict)
    return {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
        for unit in out.value["batch_admission"]["units"]
    }


async def test_generate_videos_episode_scope_batch_is_all_or_nothing_when_a_unit_is_occupied(
    idle_fake_ctx: ToolHarness, concurrent_session_factory
) -> None:
    """在途任务冲突拦下整批：一个都不入队，其余 unit 报告自己是被谁扣下的。"""
    fake_ctx = idle_fake_ctx
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": "E1S01", "novel_text": "第一段旁白。", "video_prompt": "第一镜"},
        {"segment_id": "E1S02", "novel_text": "第二段旁白。", "video_prompt": "第二镜"},
    ]
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        image = project_dir / "storyboards" / f"scene_{segment_id}.png"
        image.write_bytes(b"png")
        for item in fake_ctx.pm.script_payload["segments"]:
            if item["segment_id"] == segment_id:
                item["generated_assets"] = {"storyboard_image": f"storyboards/scene_{segment_id}.png"}

    async with concurrent_session_factory() as session:
        session.add(User(id="tenant-user", username="tenant-user"))
        await session.commit()
    fake_ctx.caller = CallerContext(user_id="tenant-user", source="embedded")
    other_user = await fake_ctx.queue.enqueue_task(
        project_name="demo",
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        script_file="episode_1.json",
    )
    occupied = await fake_ctx.queue.enqueue_task(
        project_name="demo",
        task_type="video",
        media_type="video",
        resource_id="E1S02",
        script_file="episode_1.json",
        user_id="tenant-user",
    )
    caller_active = await fake_ctx.queue.get_active_tasks_for_resources(
        project_name="demo",
        task_type="video",
        resource_ids=["E1S01", "E1S02"],
        script_file="episode_1.json",
        user_id="tenant-user",
    )
    assert (await fake_ctx.queue.get_task(other_user["task_id"])) is not None
    assert [task["task_id"] for task in caller_active] == [occupied["task_id"]]

    out = await run_generate_videos(fake_ctx, _EPISODE_1)

    other_task = await fake_ctx.queue.get_task(other_user["task_id"])
    occupied_task = await fake_ctx.queue.get_task(occupied["task_id"])
    assert other_task is not None
    assert other_task["user_id"] == "default"
    assert occupied_task is not None
    assert occupied_task["user_id"] == "tenant-user"
    result = read_generation_result(out)
    assert sorted(result.blocked) == ["E1S01", "E1S02"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S02"] == "generation_active_task_conflict"
    assert codes["E1S01"] == "generation_batch_admission_withheld"


async def test_generate_reference_videos_ignores_narration_audio_state(
    idle_fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS 配音项目里旁白配音缺失或正在生成，都不影响参考生视频的准入与入队。"""
    fake_ctx = idle_fake_ctx
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(),
    )
    use_reference_route(fake_ctx)
    fake_ctx.pm.project_payload.update(
        {"narration_delivery": "use_tts", "audio_backend": "dashscope/qwen3-tts-flash", "narration_voice": "Cherry"}
    )
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    script = reference_video_script()
    script["video_units"][0]["text"] = "海面。\n{风从远方吹来。}"
    script["video_units"].append(
        {
            "unit_id": "E1U2",
            "text": "山谷。\n{回声渐渐远去。}",
            "duration_seconds": 5,
        }
    )
    fake_ctx.pm.script_payload = script
    await fake_ctx.queue.enqueue_task(
        project_name="demo",
        task_type="tts",
        media_type="audio",
        resource_id="E1U2",
        script_file="episode_1.json",
        payload={"text": "回声渐渐远去。"},
    )
    enqueued: list[Any] = []

    async def _batch(*, specs, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_batch)

    assert out.problem is None, out
    assert read_generation_result(out).blocked == []
    assert sorted(spec.resource_id for spec in enqueued) == ["E1U1", "E1U2"]


async def test_generate_videos_all_scope_creates_zero_tasks_when_one_artifact_state_is_unreadable(
    fake_ctx: ToolHarness,
) -> None:
    """产物状态读不出的场景属于这次请求：它带着自己的问题进准入，整批停下，健康的场景不入队计费。"""
    fake_ctx.pm.script_payload["segments"].append(
        {
            "segment_id": "E1S02",
            "image_prompt": "山道清晨",
            "novel_text": "清晨的山道上落着薄雾。",
            "video_prompt": {"action": "镜头推近", "camera_motion": "Push", "ambiance_audio": "鸟鸣"},
            "duration_seconds": 4,
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png", "video_clip": _UNREADABLE_CLIP},
        }
    )
    (fake_ctx.project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (fake_ctx.project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"\x89PNG")
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _ALL, batch_waiter=enqueue)

    assert out.value["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    codes = _admission_codes(out)
    assert codes["E1S02"] == ["generation_artifact_state_unavailable"]
    assert codes["E1S01"] == ["generation_batch_admission_withheld"]


async def test_generate_videos_all_scope_blocks_a_reference_gap_and_withholds_the_batch(
    fake_ctx: ToolHarness,
) -> None:
    """图生视频的整批准入与单条提交同判：引用有缺口的场景阻断，整批一个都不入队。"""

    fake_ctx.pm.script_payload["segments"].append(
        {
            "segment_id": "E1S02",
            "image_prompt": "山道清晨",
            "novel_text": "清晨的山道上落着薄雾。",
            "video_prompt": {"action": "镜头推近", "camera_motion": "Push", "ambiance_audio": "鸟鸣"},
            "duration_seconds": 4,
            "characters_in_segment": ["无名氏"],
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )
    (fake_ctx.project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (fake_ctx.project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"\x89PNG")

    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _ALL, batch_waiter=enqueue)

    assert out.value["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    units = {unit["unit_id"]: unit for unit in out.value["batch_admission"]["units"]}
    assert [problem["code"] for problem in units["E1S02"]["problems"]] == ["reference_asset_unregistered"]
    assert units["E1S02"]["problems"][0]["action"] == "generate_dependency"
    assert units["E1S02"]["problems"][0]["params"]["missing_text"] == "无名氏"
    assert [problem["code"] for problem in units["E1S01"]["problems"]] == ["generation_batch_admission_withheld"]


async def test_generate_videos_all_scope_admits_legacy_narration_stored_under_scenes(
    fake_ctx: ToolHarness,
) -> None:
    """narration 数据落在 scenes 键的历史剧本按实际骨架做发声准入，不被整批判成解析失败。"""

    fake_ctx.pm.script_payload = {
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
        from lib.generation.generation_queue_client import BatchTaskResult

        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id=f"t-{spec.resource_id}",
                status="succeeded",
                result={"file_path": f"videos/{spec.resource_id}.mp4"},
            )
            for spec in specs
        ], []

    out = await run_generate_videos(fake_ctx, _ALL, batch_waiter=fake_batch)

    assert out.problem is None, out.problem
    result = read_generation_result(out)
    assert list(result.succeeded) == ["E1S01"]


async def test_generate_videos_all_scope_reports_an_all_unreadable_selection_as_blocked(
    fake_ctx: ToolHarness,
) -> None:
    """全部目标的产物状态都读不出时不能报成空的成功：那会把每一条状态问题都藏起来。"""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"]["video_clip"] = _UNREADABLE_CLIP
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _ALL, batch_waiter=enqueue)

    assert out.value["batch_admission"]["decision"] == "blocked"
    enqueue.assert_not_awaited()
    assert _admission_codes(out) == {"E1S01": ["generation_artifact_state_unavailable"]}


async def test_generate_reference_episode_refuses_a_non_scalar_unit_id(
    fake_ctx: ToolHarness,
) -> None:
    """整集参考生成遇到非标量 unit_id：它按位置记名拒收，健康的兄弟条目不会独自入队计费。"""

    use_reference_route(fake_ctx)
    script = reference_video_script()
    healthy = script["video_units"][0]
    script["video_units"] = [{**healthy, "unit_id": ["U9"]}, healthy]
    fake_ctx.pm.script_payload = script
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=enqueue)

    enqueue.assert_not_awaited()
    codes = _admission_codes(out)
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert healthy["unit_id"] in codes


async def test_generate_reference_units_refuses_a_duplicated_named_unit(
    fake_ctx: ToolHarness,
) -> None:
    """点名的 unit 在剧本里有两份：无从判定要做哪一条，整批停在建任务之前。"""

    use_reference_route(fake_ctx)
    script = reference_video_script()
    script["video_units"] = [*script["video_units"], {**script["video_units"][0]}]
    fake_ctx.pm.script_payload = script
    duplicated_id = script["video_units"][0]["unit_id"]
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(
        fake_ctx, {"scope": "selected", "ids": [duplicated_id]}, batch_waiter=enqueue, force=True
    )

    enqueue.assert_not_awaited()
    assert _admission_codes(out) == {duplicated_id: ["generation_unit_request_invalid"]}
