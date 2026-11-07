"""Tests for the ``generate_videos`` tool handler and its storyboard / reference admission helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import resource_relative_path
from lib.script.script_skeleton import SkeletonRouteMismatchError
from server.media_tools import videos as enqueue_videos_mod
from server.media_tools.context import generation_is_error
from server.tool_runtime import ToolOutcome
from tests.factories import make_video_request_facts
from tests.fakes import (
    fake_reference_request_facts,
    use_active_tasks_lookup,
    use_reference_projection,
    write_backend_output,
)
from tests.integration.server.agent_tool_support import (
    CLAIMED_BASIS_DIGEST,
    ToolHarness,
    activate_unbound_project,
    fake_caps_resolver,
    fake_reference_projection,
    read_generation_result,
    reference_video_script,
    run_declared_tool,
    run_generate_videos,
    use_reference_route,
)


@pytest.fixture(autouse=True)
def storyboard_request_facts(set_admission_video_request_facts) -> None:
    facts = make_video_request_facts(provider_id="fake", model_id="fake-video", audio_switch_controllable=True)
    set_admission_video_request_facts(facts)


async def fake_scene_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
    """Stand in for the queue: every scene spec lands its canonical mp4."""

    from lib.generation.generation_queue_client import BatchTaskResult

    return [
        BatchTaskResult(
            resource_id=spec.resource_id,
            task_id="t1",
            status="succeeded",
            result={"file_path": f"videos/scene_{spec.resource_id}.mp4"},
        )
        for spec in specs
    ], []


_EPISODE_1: dict[str, Any] = {"scope": "episode", "episode_id": 1}
_ALL: dict[str, Any] = {"scope": "all"}
# Outside-project final paths cannot be manifest-checked; their state is neither missing nor usable.
_UNREADABLE_CLIP = "../outside/E1S01.mp4"


def _scene(unit_id: str) -> dict[str, Any]:
    return {"scope": "scene", "ids": [unit_id]}


def _selected(*unit_ids: str) -> dict[str, Any]:
    return {"scope": "selected", "ids": list(unit_ids)}


def _is_error(out: ToolOutcome[Any]) -> bool:
    """Both hosts set isError identically: problems or terminal results not wholly successful, excluding tier confirmation pending."""

    return out.problem is not None or generation_is_error(out.value)


def _text(out: ToolOutcome[Any]) -> str:
    """Caller-readable text is problem detail or terminal-result summary."""

    if out.problem is not None:
        return out.problem.detail
    assert isinstance(out.value, dict)
    return out.value["summary"]


def _recording_batch(enqueued: list[Any]):
    """Record batch specs without producing terminal results."""

    async def _batch(*, specs, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    return _batch


def _select_manual_video(
    project_path: Path,
    *,
    resource_type: str,
    resource_id: str,
    content: bytes,
) -> str:
    artifact_path = resource_relative_path(resource_type, resource_id)
    staged = project_path / f".{resource_type}-{resource_id}.upload.mp4"
    staged.write_bytes(content)
    VersionManager(project_path).commit_staged_version(
        resource_type,
        resource_id,
        "",
        staged_file=staged,
        current_file=project_path / artifact_path,
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )
    return artifact_path


def _activated_project(project_dir: Path, storyboard_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """Construct a current-schema project and register named storyboard images in manifests.

    Cases calling enqueue builders directly bypass pm and need complete project shapes here; manifests alone
    determine generated-artifact presence, so unregistered storyboards cannot feed videos.
    """

    from lib.artifacts.artifact_manifest import (
        ArtifactKey,
        ArtifactManifest,
        ArtifactManifestEntry,
        ProjectArtifactManifestAdapter,
    )

    project: dict[str, Any] = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project_dir))
    for resource_id, artifact_path in (storyboard_ids or {}).items():
        manifest.register_entry_transactionally(
            ArtifactKey.episode_storyboard(1, resource_id),
            ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=CLAIMED_BASIS_DIGEST),
        )
    return project


def _refused_problems(refused: list[Any]) -> dict[str, tuple[str, str]]:
    """Map each refused ticket's unit ID to its ``(code, action)`` pair."""

    return {ticket.unit_id: (problem.code, problem.action.value) for ticket in refused for problem in ticket.problems}


# ---------------------------------------------------------------------------
# generate_videos request validation.
# ---------------------------------------------------------------------------


_VALID_REQUEST: dict[str, Any] = {
    "script": "episode_1.json",
    "target": _EPISODE_1,
}

# Both project narration-delivery choices receive identical video requests.
_POST_PRODUCTION_PROJECT: dict[str, Any] = {"narration_delivery": "post_production"}
_USE_TTS_PROJECT: dict[str, Any] = {
    "narration_delivery": "use_tts",
    "audio_backend": "dashscope/qwen3-tts-flash",
    "narration_voice": "Cherry",
}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"target": {"scope": "episode"}}, id="episode-without-number"),
        pytest.param({"target": {"scope": "episode", "episode_id": 1, "ids": ["E1S01"]}}, id="episode-with-ids"),
        pytest.param({"target": {"scope": "episode", "episode_id": 1, "extra": True}}, id="episode-unknown-field"),
        pytest.param({"target": {"scope": "all", "episode": 1}}, id="all-with-episode"),
        pytest.param({"target": {"scope": "all", "ids": ["E1S01"]}}, id="all-with-ids"),
        pytest.param({"target": {"scope": "scene", "ids": []}}, id="scene-without-id"),
        pytest.param({"target": {"scope": "scene", "ids": ["E1S01", "E1S02"]}}, id="scene-with-two-ids"),
        pytest.param({"target": {"scope": "scene", "ids": ["E1S01"], "episode": 1}}, id="scene-with-episode"),
        pytest.param({"target": {"scope": "selected"}}, id="selected-without-ids"),
        pytest.param({"target": {"scope": "selected", "ids": []}}, id="selected-with-empty-ids"),
        pytest.param({"target": _ALL, "force": True}, id="force-on-all"),
        pytest.param({"force": True}, id="force-on-episode"),
        pytest.param({"confirmed_request_duration_seconds": 0}, id="tier-zero"),
        pytest.param({"confirmed_request_duration_seconds": True}, id="tier-boolean"),
        pytest.param({"confirmed_request_duration_seconds": "12"}, id="tier-string"),
        pytest.param({"confirmed_request_durations": {"E1S01": 9.5}}, id="unit-tier-fraction"),
        pytest.param({"confirmed_request_durations": [8]}, id="unit-tiers-not-a-mapping"),
    ],
)
async def test_generate_videos_refuses_a_malformed_request_before_enqueuing(
    fake_ctx: ToolHarness, overrides: dict[str, Any]
) -> None:
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool("generate_videos", fake_ctx, {**_VALID_REQUEST, **overrides}, batch_waiter=enqueue)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("delivery", ["post_production", "use_tts"])
async def test_generate_videos_refuses_a_narration_delivery_without_enqueuing(
    fake_ctx: ToolHarness, delivery: str
) -> None:
    """Narration delivery belongs to projects; supplying it in requests rejects the call with guidance that videos request script-planned durations."""
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool(
        "generate_videos", fake_ctx, {**_VALID_REQUEST, "narration_delivery": delivery}, batch_waiter=enqueue
    )

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    assert "narration_delivery" in out.problem.detail
    assert "项目配置" in out.problem.detail
    enqueue.assert_not_awaited()
    assert (await fake_ctx.queue.list_tasks(project_name="demo"))["items"] == []


@pytest.mark.parametrize("retired_param", sorted(enqueue_videos_mod._RETIRED_PARAMS))
async def test_generate_videos_refuses_a_retired_param_with_its_replacement(
    fake_ctx: ToolHarness, retired_param: str
) -> None:
    """Retired parameter names reject with errors naming them and current syntax."""

    out = await run_declared_tool("generate_videos", fake_ctx, {**_VALID_REQUEST, retired_param: "dummy"})

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    assert retired_param in out.problem.detail
    assert "已不存在" in out.problem.detail
    if retired_param in {"shot_ids", "unit_id", "unit_ids"}:
        assert "target.ids" in out.problem.detail


async def test_generate_videos_refuses_an_episode_target_that_is_not_the_scripts_episode(
    fake_ctx: ToolHarness,
) -> None:
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, {"scope": "episode", "episode_id": 2}, batch_waiter=enqueue)

    assert out.problem is not None
    assert "不一致" in out.problem.detail
    enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# generate_videos storyboard-image-to-video.
# ---------------------------------------------------------------------------


async def test_generate_videos_episode_scope_happy(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_episode_scope_skips_current_clip(fake_ctx: ToolHarness) -> None:
    """Whole-episode calls reuse manifest-claimed legacy clips."""

    project = fake_ctx.pm.project_payload
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {
        "storyboard_image": "storyboards/scene_E1S01.png",
        "video_clip": "videos/scene_E1S01.mp4",
    }

    clip = fake_ctx.project_path / "videos" / "scene_E1S01.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"rendered-video")
    enqueued: list[Any] = []

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert not _is_error(out), out
    result = read_generation_result(out)
    assert enqueued == []
    assert result.succeeded == []
    assert [entry.unit_id for entry in result.skipped] == ["E1S01"]


async def test_generate_videos_episode_scope_blocks_a_clip_whose_manifest_state_is_unreadable(
    fake_ctx: ToolHarness,
) -> None:
    """Whole-episode manifest comparison failures report BLOCKED, never falling into neither-reusable-nor-blocked
    gaps that would pay to regenerate supposedly missing clips; unreadable is not absent."""

    project = fake_ctx.pm.project_payload
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1
    segments = fake_ctx.pm.script_payload["segments"]
    segments[0]["generated_assets"] = {
        "storyboard_image": "storyboards/scene_E1S01.png",
        "video_clip": _UNREADABLE_CLIP,
    }

    enqueued: list[Any] = []

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    result = read_generation_result(out)
    assert enqueued == []
    assert result.succeeded == []
    assert result.blocked == ["E1S01"]
    blocked_item = next(item for item in result.items if item.unit_id == "E1S01")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_artifact_state_unavailable"


async def test_generate_videos_episode_scope_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolHarness,
) -> None:
    activate_unbound_project(fake_ctx)
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=enqueue)

    assert out.problem is not None
    assert "not bound" in out.problem.detail
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_non_dict_generated_assets_does_not_abort_batch(
    fake_ctx: ToolHarness,
) -> None:
    """Whole-episode enqueue first filters generated_assets.video_clip; externally corrupted nondict containers
    count as ungenerated instead of raising unhandled AttributeError during filtering. Their own problem codes
    then block the entire batch without creating any tasks."""
    project_dir = fake_ctx.pm.get_project_path("demo")
    (project_dir / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [
        {
            "segment_id": "E1S01",
            "novel_text": "第一段旁白。",
            "video_prompt": "脏数据",
            "generated_assets": ["bad"],
        },
        {
            "segment_id": "E1S02",
            "novel_text": "第二段旁白。",
            "video_prompt": "合法条目",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        },
    ]
    enqueued: list[Any] = []

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    # Unavailable E1S01 storyboard bindings block admission with zero tasks; valid siblings truthfully report why deferred.
    assert enqueued == []
    result = read_generation_result(out)
    assert sorted(result.blocked) == ["E1S01", "E1S02"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S01"] == "generation_unit_input_unusable"
    assert codes["E1S02"] == "generation_batch_admission_withheld"
    assert _is_error(out)


async def test_generate_videos_episode_scope_error(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.script_payload = {"content_mode": "narration", "segments": [], "episode": 1}

    out = await run_generate_videos(fake_ctx, _EPISODE_1)

    assert _is_error(out)


async def test_generate_videos_ignores_legacy_batch_checkpoint_files(fake_ctx: ToolHarness) -> None:
    checkpoint = fake_ctx.project_path / "videos" / ".checkpoint_ep1.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("not-json", encoding="utf-8")

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out
    assert checkpoint.read_text(encoding="utf-8") == "not-json"
    assert list(checkpoint.parent.glob(".checkpoint_*.json")) == [checkpoint]


async def test_generate_videos_resubmits_only_remaining_ids_from_a_durable_batch(
    fake_ctx: ToolHarness,
) -> None:
    from lib.db.base import DEFAULT_USER_ID
    from server.tool_runtime import CallerContext

    segments = fake_ctx.pm.script_payload["segments"]
    segments.append(
        {
            **segments[0],
            "segment_id": "E1S02",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )
    (fake_ctx.project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
    queue = fake_ctx.queue
    fake_ctx.caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")

    first = await run_generate_videos(fake_ctx, _EPISODE_1)
    assert isinstance(first.value, GenerationBatchReadModel), first
    first_task = await queue.claim_next_task(media_type="video")
    assert first_task is not None, first
    await queue.mark_task_succeeded(first_task["task_id"], {"file_path": "videos/scene_E1S01.mp4"})
    second_task = await queue.claim_next_task(media_type="video")
    assert second_task is not None
    await queue.mark_task_failed(second_task["task_id"], "provider failed")
    video_path = _select_manual_video(
        fake_ctx.project_path,
        resource_type="videos",
        resource_id="E1S01",
        content=b"finished-video",
    )
    segments[0]["generated_assets"]["video_clip"] = video_path

    terminal = await queue.get_generation_batch(project_name="demo", batch_id=first.value.batch_id)
    assert terminal.done is True
    assert terminal.generation_result is not None
    assert terminal.generation_result.succeeded == ["E1S01"]
    assert terminal.generation_result.failed == ["E1S02"]

    retried = await run_generate_videos(fake_ctx, _selected("E1S01", "E1S02"), force=False)

    assert isinstance(retried.value, GenerationBatchReadModel), retried
    assert [item.unit_id for item in retried.value.skipped] == ["E1S01"]
    assert [(item.unit_id, item.status) for item in retried.value.members] == [("E1S02", "queued")]
    assert len((await queue.list_tasks(project_name="demo"))["items"]) == 3


async def test_generate_videos_scene_scope_happy(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_storyboard_request_is_independent_of_project_narration_delivery(
    fake_ctx: ToolHarness,
) -> None:
    """Storyboard-image-to-video TTS/postproduction projects receive identical admission decisions and queued requests."""

    async def _submit(project_config: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
        fake_ctx.pm.project_payload.update(project_config)
        enqueued: list[Any] = []

        async def _batch(*, specs, **batch_kwargs):
            enqueued.extend(specs)
            return await fake_scene_batch(specs=specs, **batch_kwargs)

        out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True, batch_waiter=_batch)
        assert not _is_error(out), out
        return read_generation_result(out).model_dump(mode="json"), [
            (spec.resource_id, spec.payload) for spec in enqueued
        ]

    post_production = await _submit(_POST_PRODUCTION_PROJECT)
    use_tts = await _submit(_USE_TTS_PROJECT)

    assert use_tts == post_production
    [(resource_id, payload)] = use_tts[1]
    assert resource_id == "E1S01"
    assert "narration_delivery_options" not in (payload or {})


async def test_generate_videos_scene_scope_accepts_legacy_drama_dialogue(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.project_payload["content_mode"] = "drama"
    fake_ctx.pm.script_payload = {
        "content_mode": "drama",
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

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_scene_scope_accepts_speech_free_legacy_drama(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.project_payload["content_mode"] = "drama"
    fake_ctx.pm.script_payload = {
        "content_mode": "drama",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "video_prompt": {
                    "action": "阿离转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                },
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_scene_scope_accepts_legacy_narration_string_prompt(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.project_payload["content_mode"] = "narration"
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "风吹过旷野。",
                "video_prompt": "Slow pan across the field",
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_episode_scope_storyboard_batch_blocks_on_mixed_speech(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Whole-batch storyboard-video admission checks speech; one mixed-speech item blocks everything without tasks."""
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        (project_dir / "storyboards" / f"scene_{segment_id}.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [
        {
            "segment_id": "E1S01",
            "novel_text": "风吹过旷野。",
            # Narration plus character dialogue requires replanning, not direct generation.
            "video_prompt": {"dialogue": [{"speaker": "阿离", "line": "快走。"}]},
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
        },
        {
            "segment_id": "E1S02",
            "novel_text": "他停下脚步。",
            "video_prompt": "第二镜",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        },
    ]
    enqueued: list[Any] = []
    use_active_tasks_lookup(monkeypatch, AsyncMock(return_value=[]))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert enqueued == []
    assert _is_error(out)
    result = read_generation_result(out)
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S01"] == "mixed_speech"
    assert codes["E1S02"] == "generation_batch_admission_withheld"


async def test_generate_videos_episode_scope_storyboard_batch_blocks_when_a_video_prompt_is_pending(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Mechanically derived video_prompt=None blocks whole batches with zero tasks and names pending items in receipts."""
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        (project_dir / "storyboards" / f"scene_{segment_id}.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [
        {
            "segment_id": "E1S01",
            "novel_text": "风吹过旷野。",
            "image_prompt": None,
            "video_prompt": None,
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
        },
        {
            "segment_id": "E1S02",
            "novel_text": "他停下脚步。",
            "video_prompt": "第二镜",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        },
    ]
    enqueued: list[Any] = []
    use_active_tasks_lookup(monkeypatch, AsyncMock(return_value=[]))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert enqueued == []
    assert _is_error(out)
    result = read_generation_result(out)
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S01"] == "generation_unit_request_invalid"
    assert codes["E1S02"] == "generation_batch_admission_withheld"


async def test_generate_videos_scene_scope_missing(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _scene("NO_SUCH"), force=True)

    assert _is_error(out)


@pytest.mark.parametrize(
    "storyboard_value",
    [
        123,  # Corrupt nonstring script JSON must fail readably, not unhandled TypeError.
        "/etc/passwd",  # Absolute paths reference unauthorized outside files.
        "../../outside.png",  # `..` escapes the project.
    ],
)
async def test_generate_videos_scene_scope_rejects_invalid_storyboard_image(
    fake_ctx: ToolHarness, storyboard_value: object
) -> None:
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True)

    assert _is_error(out)
    # Pin resolve_storyboard_image_ref canonical messages, not vague substrings/generic errors.
    assert f"invalid storyboard image path: {storyboard_value!r}" in _text(out)


async def test_generate_videos_all_scope_happy(fake_ctx: ToolHarness) -> None:
    async def fake_batch(*, specs, **_batch_kwargs):
        from lib.generation.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id, task_id="t1", status="succeeded", result={"file_path": "videos/x.mp4"}
            )
            for s in specs
        ]
        return succ, []

    out = await run_generate_videos(fake_ctx, _ALL, batch_waiter=fake_batch)

    assert not _is_error(out), out


async def test_generate_videos_all_scope_error(fake_ctx: ToolHarness) -> None:
    def boom(*a, **kw):
        raise RuntimeError("broken")

    fake_ctx.pm.load_script = boom

    out = await run_generate_videos(fake_ctx, _ALL)

    assert _is_error(out)


async def test_generate_videos_selected_scope_happy(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _selected("E1S01"), force=True, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_selected_scope_no_match(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _selected("NO_SUCH"), force=True)

    assert _is_error(out)


# ---------------------------------------------------------------------------
# generate_videos reference-to-video.
# ---------------------------------------------------------------------------


async def test_generate_reference_video_rejects_unbound_active_script_before_generation(
    fake_ctx: ToolHarness,
) -> None:
    activate_unbound_project(fake_ctx, generation_mode="reference_video")
    fake_ctx.pm.script_payload = reference_video_script()
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=enqueue)

    assert out.problem is not None
    assert "not bound" in out.problem.detail
    enqueue.assert_not_awaited()


async def test_generate_reference_video_legacy_unresolvable_episode_fails_before_generation(
    fake_ctx: ToolHarness,
) -> None:
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()
    fake_ctx.pm.script_payload.pop("episode")
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, script="draft.json", batch_waiter=enqueue)

    assert out.problem is not None
    assert "无法确定集号" in out.problem.detail
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_reference_rejects_malformed_unit_container(fake_ctx: ToolHarness) -> None:
    """Nonarray ``video_units`` passes key-presence mode gates, but enqueue container checks
    must locate structural errors before unit iteration raises TypeError."""
    use_reference_route(fake_ctx)
    for malformed in (
        {"E1U1": {}},
        {},
        "",
        False,
        None,
    ):
        # Existing keys validate types regardless of truthiness; ``{}`` / ``""`` / ``False`` are type errors too,
        # not empty containers, which would hide the cause.
        fake_ctx.pm.script_payload = reference_video_script(video_units=malformed)

        out = await run_generate_videos(fake_ctx, _EPISODE_1)

        assert out.problem is not None
        assert "video_units 必须是数组" in out.problem.detail


async def test_generate_videos_episode_scope_reference_duration_needs_confirmation(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Requested seconds differing from script totals do not enqueue initially; per-ID results expose machine-readable pending confirmation."""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_active_tasks(**_kwargs):
        return []

    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))
    use_active_tasks_lookup(monkeypatch, fake_active_tasks)

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert not _is_error(out), out
    assert enqueued == []
    # Pending confirmation is machine-readable, avoiding prose-only conclusions requiring guesses.
    result = read_generation_result(out)
    assert result.blocked == ["E1U1"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "reference_duration_confirmation_required"
    assert item.problem.action == "confirm_request_duration"
    # Summaries compare script-planned durations and state whether final videos get longer or shorter.
    assert "E1U1：剧本时长 5s，将申请 8s（成片更长 3s）" in _text(out)


async def test_generate_videos_episode_scope_reference_duration_confirm_enqueues(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Repeat calls with exact requested tiers enqueue those tiers and generate successfully."""
    from lib.generation.generation_queue_client import BatchTaskResult
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, specs, **_batch_kwargs):
        enqueued.extend(specs)
        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            for spec in specs
        ], []

    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, confirmed_request_duration_seconds=8, batch_waiter=fake_batch)

    assert not _is_error(out), out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_confirms_two_tiers_in_one_batch(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Multitier batches confirm per unit and resend original target sets as one batch; each task carries its own confirmation."""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script(
        video_units=[
            {
                "unit_id": "E1U1",
                "text": "@张三 推门",
                "duration_seconds": 5,
            },
            {
                "unit_id": "E1U2",
                "text": "@张三 回头",
                "duration_seconds": 6,
            },
        ]
    )
    tiers = {"E1U1": 8, "E1U2": 12}

    def fake_precheck(_ctx, unit):
        seconds = tiers[str(unit.get("unit_id"))]
        return DurationSlot(seconds=seconds, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []
    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))

    out = await run_generate_videos(
        fake_ctx,
        _EPISODE_1,
        confirmed_request_durations={"E1U1": 8, "E1U2": 12},
        batch_waiter=_recording_batch(enqueued),
    )

    assert out.problem is None, out
    assert sorted(spec.resource_id for spec in enqueued) == ["E1U1", "E1U2"]
    # Workers reproject using per-task tier confirmations;
    # one shared batch option would turn admission-accepted tiers back into confirmation-pending at execution.
    confirmed = {
        spec.resource_id: (spec.payload or {})["reference_request_options"]["confirmed_request_duration_seconds"]
        for spec in enqueued
    }
    assert confirmed == {"E1U1": 8, "E1U2": 12}


async def test_generate_videos_reference_request_is_independent_of_project_narration_delivery(
    fake_ctx: ToolHarness,
    set_video_request_facts,
) -> None:
    """Reference-to-video TTS/postproduction projects receive identical tiers, admission, quotes, and queued requests."""
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()
    fake_ctx.pm.script_payload["video_units"][0]["text"] = "推门"
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            provider_id="grok",
            model_id="grok-imagine-video",
            resolution="720p",
            supported_durations=(4, 8, 12),
            allowed_durations=(4, 8, 12),
        )
    )

    async def _submit(project_config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
        fake_ctx.pm.project_payload.update(project_config)
        enqueued: list[Any] = []
        pending = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))
        assert not _is_error(pending), pending
        assert enqueued == []
        confirmed = await run_generate_videos(
            fake_ctx, _EPISODE_1, confirmed_request_duration_seconds=8, batch_waiter=_recording_batch(enqueued)
        )
        assert confirmed.problem is None, confirmed
        return (
            pending.value["batch_admission"],
            confirmed.value["request_projections"],
            [(spec.resource_id, spec.payload) for spec in enqueued],
        )

    post_production = await _submit(_POST_PRODUCTION_PROJECT)
    use_tts = await _submit(_USE_TTS_PROJECT)

    assert use_tts == post_production
    admission, _projections, specs = use_tts
    assert admission["decision"] == "confirmation_required"
    [unit] = admission["units"]
    assert unit["request_duration_seconds"] == 8
    assert unit["request_cost"] == {
        "amount": pytest.approx(0.56),
        "currency": "USD",
        "provider_id": "grok",
        "model_id": "grok-imagine-video",
        "request_duration_seconds": 8,
        "unpriced_reason": None,
    }
    assert specs == [("E1U1", specs[0][1])]
    assert specs[0][1]["reference_request_options"] == {"confirmed_request_duration_seconds": 8}


async def test_generate_videos_episode_scope_reference_duration_repeat_without_confirm_still_blocked(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Repeated unconfirmed calls still do not enqueue."""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []
    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))

    await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))
    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert not _is_error(out), out
    assert enqueued == []


async def test_generate_videos_episode_scope_reference_duration_exact_enqueues_directly(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Total durations already in tiers enqueue directly in one call, preserving behavior."""
    from lib.generation.generation_queue_client import BatchTaskResult
    from lib.script.reference_video.duration_slots import EXACT, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, specs, **_batch_kwargs):
        enqueued.extend(specs)
        return [
            BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            for spec in specs
        ], []

    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=fake_batch)

    assert not _is_error(out), out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_reference_duration_resolves_project_context_once(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Batch prechecks send every enqueueable unit through public request projection."""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    script = reference_video_script()
    script["video_units"].append(
        {
            "unit_id": "E1U2",
            "text": "@张三 转身",
            "duration_seconds": 5,
        }
    )
    script["video_units"].append(
        {
            "unit_id": "E1U3",
            "text": "空镜转场",
            "duration_seconds": 5,
        }
    )
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script

    context_calls: list[Any] = []

    def fake_precheck(_ctx, _unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []
    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck, calls=context_calls))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    # Three 5-second units request 8: all need confirmation, none enqueue; each projection exposes its hydrated bucket.
    assert not _is_error(out), out
    assert context_calls == ["r2v", "r2v", "i2v"]
    assert enqueued == []


async def test_generate_videos_episode_scope_reference_skips_duration_context_when_nothing_to_precheck(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Batches without precheckable units never resolve project capabilities; defer until the first real tier query,
    avoiding extra DB round-trips for already-complete/all-skipped batches after refactoring."""
    script = reference_video_script()
    for unit in script["video_units"]:
        unit["text"] = ""
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script

    projection_calls: list[str] = []
    use_reference_projection(monkeypatch, fake_reference_projection(calls=projection_calls))

    await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch([]))

    assert projection_calls == []


async def test_generate_videos_episode_scope_reference_skips_duration_context_when_prompt_blank(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Whitespace bodies fail build_specs; prechecks share structural validation first,
    avoiding capability resolution followed by later skipping."""
    script = reference_video_script()
    for unit in script["video_units"]:
        unit["text"] = "   "
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script

    projection_calls: list[str] = []
    use_reference_projection(monkeypatch, fake_reference_projection(calls=projection_calls))

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch([]))

    assert projection_calls == []
    assert read_generation_result(out).blocked == ["E1U1"]


async def test_generate_videos_episode_scope_ad_reference_duration_needs_confirmation(
    ad_reference_ctx: ToolHarness, monkeypatch
) -> None:
    """Ad/short-film reference-to-video shares unit-duration confirmation gates."""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    seen_units: list[dict[str, Any]] = []

    def fake_precheck(ctx, unit):
        seen_units.append(unit)
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []
    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))

    out = await run_generate_videos(ad_reference_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert not _is_error(out), out
    assert enqueued == []
    assert [unit["unit_id"] for unit in seen_units] == ["E1U1"]


@pytest.mark.parametrize(
    ("target", "force"),
    [
        (_scene("E1U1"), True),
        (_ALL, None),
        (_selected("E1U1"), True),
    ],
    ids=["scene", "all", "selected"],
)
async def test_generate_video_reference_duration_confirmation_across_entries(
    fake_ctx: ToolHarness, monkeypatch, target: dict[str, Any], force: bool | None
) -> None:
    """Whole-episode/named reference entries share confirmation gates: enqueue only after confirmation."""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_active_tasks(**_kwargs):
        return []

    use_reference_projection(monkeypatch, fake_reference_projection(fake_precheck))
    use_active_tasks_lookup(monkeypatch, fake_active_tasks)
    extra: dict[str, Any] = {} if force is None else {"force": force}

    pending = await run_generate_videos(fake_ctx, target, batch_waiter=_recording_batch(enqueued), **extra)

    assert not _is_error(pending), pending
    assert enqueued == []
    assert pending.value["batch_admission"]["decision"] == "confirmation_required"
    assert "confirmed_request_duration_seconds" in _text(pending)

    confirmed = await run_generate_videos(
        fake_ctx,
        target,
        confirmed_request_duration_seconds=8,
        batch_waiter=_recording_batch(enqueued),
        **extra,
    )

    assert confirmed.problem is None, confirmed
    assert [s.resource_id for s in enqueued] == ["E1U1"]


def test_build_video_specs_does_not_validate_duration_at_enqueue(tmp_path) -> None:
    """duration is a capability dimension, no longer enqueue-validated; pass any duration to execution (ADR-0001)."""
    from server.services.admission.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S01.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S01": "storyboards/scene_S01.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "他在旷野上奔跑。",
            "video_prompt": "一个奔跑的镜头",
            "duration_seconds": 7,  # Outside typical supported_durations.
            "generated_assets": {"storyboard_image": "storyboards/scene_S01.png"},
        }
    ]
    specs, _refused = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert len(specs) == 1
    assert specs[0].payload["duration_seconds"] == 7

    # Absent explicit duration omits the key; execution applies caps defaults.
    items[0].pop("duration_seconds")
    specs2, _ = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert "duration_seconds" not in specs2[0].payload


@pytest.mark.parametrize(
    "storyboard_value",
    [
        123,  # Corrupt nonstring script JSON.
        "/etc/passwd",  # Absolute path.
        "../../outside.png",  # `..` escapes the project.
    ],
)
def test_build_video_specs_skips_invalid_storyboard_image_without_aborting_batch(
    tmp_path: Path, storyboard_value: object
) -> None:
    """Invalid storyboard_image (corrupt/out-of-bounds/absolute) blocks only that batch ID,
    without unhandled `project_dir / storyboard_image` exceptions aborting everything."""
    from server.services.admission.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S02.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S02": "storyboards/scene_S02.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "第一段旁白。",
            "video_prompt": "非法引用",
            "generated_assets": {"storyboard_image": storyboard_value},
        },
        {
            "segment_id": "S02",
            "novel_text": "第二段旁白。",
            "video_prompt": "合法引用",
            "generated_assets": {"storyboard_image": "storyboards/scene_S02.png"},
        },
    ]
    specs, refused = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert _refused_problems(refused) == {"S01": ("generation_unit_input_unusable", "generate_dependency")}


def test_build_video_specs_skips_non_dict_generated_assets_without_aborting_batch(tmp_path: Path) -> None:
    """Externally corrupted nondict generated_assets, such as lists, counts as no storyboard and skips,
    without unhandled `.get("storyboard_image")` AttributeError aborting the batch."""
    from server.services.admission.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S02.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S02": "storyboards/scene_S02.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "第一段旁白。",
            "video_prompt": "脏数据",
            "generated_assets": ["bad"],
        },
        {
            "segment_id": "S02",
            "novel_text": "第二段旁白。",
            "video_prompt": "合法引用",
            "generated_assets": {"storyboard_image": "storyboards/scene_S02.png"},
        },
    ]
    specs, refused = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert _refused_problems(refused) == {"S01": ("generation_unit_input_unusable", "generate_dependency")}


async def test_generate_videos_scene_scope_generated_assets_non_dict_readable_rejection(fake_ctx: ToolHarness) -> None:
    """Nondict generated_assets follows readable no-storyboard rejection,
    never unhandled AttributeError on single-item paths."""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = ["bad"]

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True)

    assert _is_error(out)
    assert "请先运行 generate_storyboards" in _text(out)


def _admitted_video_yaml(item: dict, **kwargs) -> dict:
    """YAML from admission rendering.

    This exit shares execution ``render_storyboard_video_prompt``; negative constraints occupy ``Avoid``.
    """
    import yaml

    from server.services.admission.video_batch_admission import storyboard_video_prompt

    return yaml.safe_load(storyboard_video_prompt(item, **kwargs))


def test_storyboard_video_prompt_drama_sources_dialogue_from_utterances() -> None:
    """Drama admission derives video YAML dialogue from dialogue-kind storyboard utterances, excluding
    voiceover; narration/ad without utterances renders existing video_prompt.dialogue unchanged."""
    drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [
            {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
            {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
        ],
    }
    parsed = _admitted_video_yaml(drama_item, content_mode="drama")
    assert parsed["Dialogue"] == [{"Speaker": "王", "Line": "你来了。"}]

    narration_item = {
        "segment_id": "E1S01",
        "video_prompt": {
            "action": "走",
            "camera_motion": "Static",
            "ambiance_audio": "脚步声",
            "dialogue": [{"speaker": "Alice", "line": "hello"}],
        },
    }
    parsed_narr = _admitted_video_yaml(narration_item, content_mode="narration")
    assert parsed_narr["Dialogue"] == [{"Speaker": "Alice", "Line": "hello"}]


def test_storyboard_video_prompt_injects_voice_profiles_when_characters_given() -> None:
    """Drama character assets with nonempty voice_style add YAML Voice_Profiles;
    absent voice_characters retains existing no-injection behavior."""
    drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [{"kind": "dialogue", "speaker": "王", "text": "你来了。"}],
    }
    characters = {"王": {"voice_style": "低沉沙哑"}}

    parsed = _admitted_video_yaml(drama_item, content_mode="drama", voice_characters=characters)
    assert parsed["Voice_Profiles"] == [{"Speaker": "王", "Voice_Style": "低沉沙哑"}]

    parsed_default = _admitted_video_yaml(drama_item, content_mode="drama")
    assert "Voice_Profiles" not in parsed_default

    parsed_no_style = _admitted_video_yaml(
        drama_item, content_mode="drama", voice_characters={"王": {"voice_style": ""}}
    )
    assert "Voice_Profiles" not in parsed_no_style


def test_storyboard_video_prompt_injects_voice_profiles_from_legacy_dialogue() -> None:
    """Legacy drama before utterances migration, with dialogue still in video_prompt.dialogue,
    derives Voice_Profiles through legacy exits instead of silently losing it."""
    legacy_drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {
            "action": "起身",
            "camera_motion": "Static",
            "ambiance_audio": "风声",
            "dialogue": [{"speaker": "王", "line": "你来了。"}],
        },
    }
    characters = {"王": {"voice_style": "低沉沙哑"}}

    parsed = _admitted_video_yaml(legacy_drama_item, content_mode="drama", voice_characters=characters)
    assert parsed["Voice_Profiles"] == [{"Speaker": "王", "Voice_Style": "低沉沙哑"}]
    assert parsed["Dialogue"] == [{"Speaker": "王", "Line": "你来了。"}]


def test_storyboard_video_prompt_strips_caller_supplied_voice_profiles_for_non_drama() -> None:
    """Strip narration/ad stored video_prompt.voice_profiles when items lack utterances;
    build_drama_video_prompt mechanical derivation is the sole declaration source. Leftovers cannot
    bypass class-C true-silence gates into YAML."""
    narration_item = {
        "segment_id": "E1S01",
        "video_prompt": {
            "action": "走",
            "camera_motion": "Static",
            "ambiance_audio": "脚步声",
            "voice_profiles": [{"Speaker": "赝品", "Voice_Style": "越权"}],
        },
    }
    parsed = _admitted_video_yaml(narration_item, content_mode="narration")
    assert "Voice_Profiles" not in parsed


async def test_resolve_voice_context_skips_non_drama(fake_ctx: ToolHarness) -> None:
    """Narration/ad skips voice_consistency resolution, lacking drama dialogue speakers."""
    from server.services.admission.video_batch_admission import resolve_voice_context as _resolve_voice_context

    assert await _resolve_voice_context(fake_ctx.pm.project_payload, "narration") is None


async def test_resolve_voice_context_drama_reads_project_characters_and_gate(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """Drama reads project character assets but omits injection for class-C silence or disabled episode audio."""
    from server.services.admission import video_batch_admission as admission_mod

    async def fake_not_silent(_project, _episode=None):
        return False

    monkeypatch.setattr(admission_mod, "resolve_project_is_silent", fake_not_silent)
    characters = await admission_mod.resolve_voice_context(fake_ctx.pm.project_payload, "drama")
    assert characters == fake_ctx.pm.project_payload["characters"]

    async def fake_silent(_project, _episode=None):
        return True

    monkeypatch.setattr(admission_mod, "resolve_project_is_silent", fake_silent)
    assert await admission_mod.resolve_voice_context(fake_ctx.pm.project_payload, "drama") is None


def test_build_reference_specs_routes_through_guard(tmp_path) -> None:
    """Reference prompts serve only shared structural guards, without freezing into task payloads."""
    from server.media_tools.videos import _build_reference_specs

    units = [
        {
            "unit_id": "E1U1",
            "text": "@张三 推门",
        }
    ]
    specs, _refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert len(specs) == 1
    assert specs[0].task_type == "reference_video"
    assert specs[0].resource_id == "E1U1"
    assert "prompt" not in specs[0].payload
    assert specs[0].payload["script_file"] == "episode_1.json"


def test_build_reference_specs_skips_blank_prompt(tmp_path) -> None:
    """Whitespace unit bodies skip with warnings before execution; structural validation moves to guards."""
    from server.media_tools.videos import _build_reference_specs

    units = [
        {"unit_id": "E1U1", "text": "   \n"},
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert _refused_problems(refused) == {"E1U1": ("generation_unit_request_invalid", "fix_input")}


def test_build_reference_specs_skips_mixed_speech_without_aborting_batch(tmp_path) -> None:
    from server.media_tools.videos import _build_reference_specs

    units = [
        {
            "unit_id": "E1U1",
            "text": "@[张三]：{快走。}\n{风吹过旷野。}",
        },
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]

    specs, refused = _build_reference_specs(
        units=units,
        script_filename="episode_1.json",
        skip_ids=None,
    )

    assert [spec.resource_id for spec in specs] == ["E1U2"]
    # Expose speech-admission codes unchanged, avoiding text parsing for next steps.
    assert _refused_problems(refused) == {"E1U1": ("mixed_speech", "replan_unit")}


def test_screening_keeps_bad_unit_ids_out_of_spec_building(tmp_path) -> None:
    """Absent/empty unit_id from raw Agent JSON rejects by array position; healthy units still construct normally."""
    from server.media_tools.videos import _build_reference_specs
    from server.services.admission.video_batch_admission import screen_script_entries

    entries = [
        {"unit_id": "", "text": "@张三 推门"},  # Empty string.
        {"text": "@王五 起身"},  # Missing unit_id.
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    units, tickets = screen_script_entries(entries, requested_ids=None)

    assert [ticket.unit_id for ticket in tickets] == ["video_units[0]", "video_units[1]"]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert refused == []


def test_build_reference_specs_handles_a_non_string_text(tmp_path) -> None:
    """Malformed text=null units must neither crash whole batches nor inject 'None' into prompts."""
    from server.media_tools.videos import _build_reference_specs

    units = [
        # Explicit text=null fails the text-must-be-string guard without injecting None.
        {"unit_id": "E1U1", "text": None},
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert all("None" not in (s.payload.get("prompt") or "") for s in specs)
    # Specs alone cannot distinguish rejection from silent skips; problem codes pin actual guard rejection.
    assert _refused_problems(refused) == {"E1U1": ("generation_unit_request_invalid", "fix_input")}


def _ad_reference_unit(**overrides: Any) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "unit_id": "E1U1",
        "duration_seconds": 5,
        "text": "@[保温杯] 置于桌面",
        "generated_assets": {},
    }
    unit.update(overrides)
    return unit


@pytest.fixture
def ad_reference_ctx(fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch) -> ToolHarness:
    fake_ctx.config_resolver = fake_caps_resolver(supported_durations=(5,), default_duration=5)
    request_facts = fake_reference_request_facts(durations=(5,), model_id="fake-video", max_reference_images=3)
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.configured_reference_request_facts",
        lambda project, resolver: request_facts,
    )

    pm = fake_ctx.pm
    pm.project_payload.update(
        {
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "style": "明亮写实",
            "products": {"保温杯": {"description": "主推商品", "reference_images": ["products/保温杯.png"]}},
            "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
        }
    )
    pm.script_payload = {
        "content_mode": "ad",
        "episode": 1,
        "title": "短片",
        "video_units": [_ad_reference_unit()],
    }
    product = fake_ctx.project_path / "products" / "保温杯.png"
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_bytes(b"product")
    return fake_ctx


def _successful_reference_batch(ctx: ToolHarness, enqueued: list[Any]):
    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation.generation_queue_client import BatchTaskResult

        successes: list[BatchTaskResult] = []
        for spec in specs:
            enqueued.append(spec)
            # Workers persist elsewhere, outside tool event loops.
            await write_backend_output(ctx.project_path / "reference_videos" / f"{spec.resource_id}.mp4", b"\x00")
            result = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            successes.append(result)
            if on_success:
                on_success(result)
        return successes, []

    return fake_batch


async def test_generate_videos_episode_scope_reference_skips_malformed_unit_entries(
    ad_reference_ctx: ToolHarness,
) -> None:
    """Validate malformed units individually, without unhandled completion-scan/audio-gate/duration-precheck exceptions."""
    valid = ad_reference_ctx.pm.script_payload["video_units"][0]
    ad_reference_ctx.pm.script_payload["video_units"] = ["bad", {}, valid]
    enqueued: list[Any] = []

    out = await run_generate_videos(
        ad_reference_ctx, _EPISODE_1, batch_waiter=_successful_reference_batch(ad_reference_ctx, enqueued)
    )

    # Block malformed units individually, using positions if IDs are unavailable, and stop the whole batch.
    assert enqueued == []
    result = read_generation_result(out)
    assert sorted(result.blocked) == ["E1U1", "video_units[0]", "video_units[1]"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1U1"] == "generation_batch_admission_withheld"


async def test_generate_videos_episode_scope_ad_reference_enqueues_existing_video_units(
    ad_reference_ctx: ToolHarness,
) -> None:
    """Ad/short-film reference generation consumes self-contained video_units without deriving/writing reference_units."""
    enqueued: list[Any] = []

    out = await run_generate_videos(
        ad_reference_ctx, _EPISODE_1, batch_waiter=_successful_reference_batch(ad_reference_ctx, enqueued)
    )

    assert not _is_error(out), out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]
    script = ad_reference_ctx.pm.script_payload
    assert [unit["unit_id"] for unit in script["video_units"]] == ["E1U1"]
    assert "reference_units" not in script


async def test_generate_videos_episode_scope_ad_reference_does_not_claim_orphan_file(
    ad_reference_ctx: ToolHarness,
) -> None:
    """Same-named files lacking generated_assets ownership still enqueue; do not report orphan files as success."""
    orphan = ad_reference_ctx.project_path / "reference_videos/E1U1.mp4"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    enqueued: list[Any] = []

    out = await run_generate_videos(
        ad_reference_ctx, _EPISODE_1, batch_waiter=_successful_reference_batch(ad_reference_ctx, enqueued)
    )

    assert not _is_error(out), out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_reference_blocks_a_clip_whose_manifest_state_is_unreadable(
    ad_reference_ctx: ToolHarness,
) -> None:
    """Existing reference videos whose manifests raise BLOCKED report blocked, not whole-batch tool_error from
    ``artifact_is_usable`` fail-loud errors. Handle the same unreadable-artifact judgment consistently
    across reference and storyboard whole-episode routes.
    """
    project_path = ad_reference_ctx.project_path
    ad_reference_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    artifact_path = "reference_videos/E1U1.mp4"
    output = project_path / artifact_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"\x00")
    ad_reference_ctx.pm.script_payload["video_units"][0]["generated_assets"] = {"video_clip": _UNREADABLE_CLIP}

    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(ad_reference_ctx, _EPISODE_1, batch_waiter=enqueue)

    result = read_generation_result(out)
    assert result.succeeded == []
    assert result.blocked == ["E1U1"]
    blocked_item = next(item for item in result.items if item.unit_id == "E1U1")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_artifact_state_unavailable"
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_ad_reference_replan_unit_cannot_reuse_owned_clip(
    ad_reference_ctx: ToolHarness,
) -> None:
    """Migration-retained owned videos cannot bypass needs_replan generation gates."""
    ad_reference_ctx.pm.script_payload["video_units"] = [
        _ad_reference_unit(
            needs_replan=True,
            migration_requires_content_replan=True,
            generated_assets={"video_clip": "reference_videos/E1U1.mp4"},
        )
    ]
    owned = ad_reference_ctx.project_path / "reference_videos/E1U1.mp4"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_bytes(b"legacy")
    enqueue = AsyncMock(return_value=([], []))

    out = await run_generate_videos(ad_reference_ctx, _EPISODE_1, batch_waiter=enqueue)

    assert _is_error(out)
    assert "E1U1" in _text(out)
    enqueue.assert_not_awaited()


async def test_generate_videos_selected_scope_ad_reference_regenerates_named_unit(
    ad_reference_ctx: ToolHarness,
) -> None:
    """Named ad regeneration uses the common video_unit path."""
    enqueued: list[Any] = []

    out = await run_generate_videos(
        ad_reference_ctx,
        _selected("E1U1"),
        force=True,
        batch_waiter=_successful_reference_batch(ad_reference_ctx, enqueued),
    )

    assert not _is_error(out), out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]


_SKELETON_BY_MODE_PAIR: dict[tuple[str, str], str] = {
    ("narration", "storyboard"): "segments",
    ("drama", "storyboard"): "scenes",
    ("ad", "storyboard"): "shots",
    ("narration", "reference_video"): "video_units",
    ("drama", "reference_video"): "video_units",
    ("ad", "reference_video"): "video_units",
}


def _video_call(ctx: ToolHarness) -> Any:
    return enqueue_videos_mod._VideoCall(scope=ctx.scope, caller=ctx.caller, services=ctx.services)


@pytest.mark.parametrize(("content_mode", "generation_mode"), sorted(_SKELETON_BY_MODE_PAIR))
def test_video_generation_dispatches_by_generation_mode_for_every_content_mode(
    fake_ctx: ToolHarness,
    content_mode: str,
    generation_mode: str,
) -> None:
    """All six combinations dispatch correct generation modes and pass their expected skeleton gates."""
    fake_ctx.pm.project_payload["content_mode"] = content_mode
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode
    skeleton = _SKELETON_BY_MODE_PAIR[(content_mode, generation_mode)]
    script = {"content_mode": content_mode, "episode": 1, skeleton: []}

    route = enqueue_videos_mod._resolve_reference_route(_video_call(fake_ctx), script)

    assert route == ("reference" if generation_mode == "reference_video" else None)


@pytest.mark.parametrize(("content_mode", "generation_mode"), sorted(_SKELETON_BY_MODE_PAIR))
def test_video_generation_refuses_a_script_from_the_other_generation_mode(
    fake_ctx: ToolHarness,
    content_mode: str,
    generation_mode: str,
) -> None:
    """All six combinations reject other-mode skeletons before enqueue, without silently rerouting by shape."""
    other_mode = "storyboard" if generation_mode == "reference_video" else "reference_video"
    fake_ctx.pm.project_payload["content_mode"] = content_mode
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode
    mismatched = _SKELETON_BY_MODE_PAIR[(content_mode, other_mode)]
    script = {"content_mode": content_mode, "episode": 1, mismatched: []}

    with pytest.raises(SkeletonRouteMismatchError):
        enqueue_videos_mod._resolve_reference_route(_video_call(fake_ctx), script)


@pytest.mark.parametrize(
    ("target", "force"),
    [
        (_EPISODE_1, None),
        (_scene("E1U1"), True),
        (_ALL, None),
        (_selected("E1U1"), True),
    ],
    ids=["episode", "scene", "all", "selected"],
)
async def test_generate_videos_rejects_mismatched_unit_script_on_storyboard_route(
    fake_ctx: ToolHarness, target: dict[str, Any], force: bool | None
) -> None:
    """video_units under storyboard-image-to-video projects yield structural errors/resplitting guidance at all four entries.

    Neither silent downgrade nor route substitution is constructible; legacy mixed episodes require resplitting/regeneration.
    """
    fake_ctx.pm.script_payload = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1", "text": "x", "duration_seconds": 5}],
    }
    extra: dict[str, Any] = {} if force is None else {"force": force}

    out = await run_generate_videos(fake_ctx, target, **extra)

    assert out.problem is not None
    assert "骨架" in out.problem.detail
    assert "重新拆分" in out.problem.detail


async def test_generate_videos_episode_scope_rejects_mismatched_storyboard_script_on_reference_route(
    fake_ctx: ToolHarness,
) -> None:
    """Inverse storyboard skeletons under reference projects also reject with unit-resplitting guidance."""

    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"

    out = await run_generate_videos(fake_ctx, _EPISODE_1)

    assert out.problem is not None
    assert "generate_script_plan" in out.problem.detail


# ---------------------------------------------------------------------------
# generate_videos precheck: quote only, no enqueue.
# ---------------------------------------------------------------------------


def _video_quote(out: ToolOutcome[Any]) -> dict[str, Any]:
    assert out.problem is None, out
    assert isinstance(out.value, dict)
    return out.value["video_quote"]


async def test_generate_videos_preview_quotes_the_reference_tier_without_enqueuing_and_the_quote_carries_through(
    fake_ctx: ToolHarness,
    set_video_request_facts,
) -> None:
    """Reference prechecks never enqueue; quotes/tiers match final admission, and submissions confirming quoted tiers need no further confirmation."""

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()
    fake_ctx.pm.script_payload["video_units"][0]["text"] = "推门"
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            provider_id="grok",
            model_id="grok-imagine-video",
            resolution="720p",
            supported_durations=(4, 8, 12),
            allowed_durations=(4, 8, 12),
        )
    )
    enqueued: list[Any] = []

    preview = await run_generate_videos(fake_ctx, _EPISODE_1, preview=True, batch_waiter=_recording_batch(enqueued))

    assert enqueued == []
    assert (await fake_ctx.queue.list_tasks(project_name=fake_ctx.project_name))["total"] == 0
    assert not _is_error(preview)
    quote = _video_quote(preview)
    [unit] = quote["units"]
    assert unit["outcome"] == "generate"
    assert (unit["script_duration_seconds"], unit["request_duration_seconds"], unit["tier_changed"]) == (5, 8, True)
    assert quote["submittable"] is True
    assert quote["confirmed_request_durations"] == {"E1U1": 8}
    assert quote["estimated_total"] == {"USD": pytest.approx(0.56)}

    unconfirmed = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))
    [admission_unit] = unconfirmed.value["batch_admission"]["units"]
    assert unit["estimated_cost"] == admission_unit["request_cost"]
    assert unit["request_duration_seconds"] == admission_unit["request_duration_seconds"]

    submitted = await run_generate_videos(
        fake_ctx,
        _EPISODE_1,
        confirmed_request_durations=quote["confirmed_request_durations"],
        batch_waiter=_recording_batch(enqueued),
    )

    assert submitted.problem is None, submitted
    assert "batch_admission" not in submitted.value
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]
    [projection] = submitted.value["request_projections"]
    assert projection["request_cost"] == unit["estimated_cost"]


async def test_generate_videos_preview_reports_blockers_per_unit(
    fake_ctx: ToolHarness,
    set_video_request_facts,
) -> None:
    """Whole-episode prechecks report actual blocked gaps and make batches unsubmittable, while still quoting other units."""

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script(
        video_units=[
            {"unit_id": "E1U1", "text": "推门", "duration_seconds": 8},
            {"unit_id": "E1U2", "text": "   ", "duration_seconds": 8},
        ]
    )
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video", provider_id="grok", model_id="grok-imagine-video", allowed_durations=(4, 8, 12)
        )
    )
    enqueued: list[Any] = []

    out = await run_generate_videos(fake_ctx, _EPISODE_1, preview=True, batch_waiter=_recording_batch(enqueued))

    assert enqueued == []
    quote = _video_quote(out)
    outcomes = {unit["unit_id"]: unit["outcome"] for unit in quote["units"]}
    assert outcomes == {"E1U1": "generate", "E1U2": "blocked"}
    assert quote["submittable"] is False
    assert quote["confirmed_request_durations"] == {}
    blocked = next(unit for unit in quote["units"] if unit["unit_id"] == "E1U2")
    assert [problem["code"] for problem in blocked["problems"]] == ["generation_unit_request_invalid"]
    generate = next(unit for unit in quote["units"] if unit["unit_id"] == "E1U1")
    assert generate["estimated_cost"] is not None


async def test_generate_videos_preview_reports_a_reused_clip_as_free(fake_ctx: ToolHarness) -> None:
    """Whole-episode prechecks mark usable videos reuse and exclude them from totals."""

    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {
        "storyboard_image": "storyboards/scene_E1S01.png",
        "video_clip": "videos/scene_E1S01.mp4",
    }
    clip = fake_ctx.project_path / "videos" / "scene_E1S01.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"rendered-video")
    enqueued: list[Any] = []

    out = await run_generate_videos(fake_ctx, _EPISODE_1, preview=True, batch_waiter=_recording_batch(enqueued))

    assert enqueued == []
    quote = _video_quote(out)
    assert [(unit["unit_id"], unit["outcome"]) for unit in quote["units"]] == [("E1S01", "reuse")]
    assert quote["estimated_total"] == {}
    assert quote["submittable"] is True


async def test_generate_videos_preview_quotes_storyboard_units_at_their_planned_duration(
    fake_ctx: ToolHarness,
    set_admission_video_request_facts,
) -> None:
    """Storyboard-video prechecks quote orchestration durations without enqueue or tier changes, matching Web batch-confirmation pricing."""

    from lib.db import async_session_factory
    from lib.generation.video_request_facts import VideoRequestCostFacts
    from server.services.admission.cost_estimation import load_video_price_source, quote_video_request_from_price

    facts = make_video_request_facts(
        provider_id="grok", model_id="grok-imagine-video", resolution="720p", audio_switch_controllable=True
    )
    set_admission_video_request_facts(facts)
    enqueued: list[Any] = []

    out = await run_generate_videos(
        fake_ctx, _selected("E1S01"), force=True, preview=True, batch_waiter=_recording_batch(enqueued)
    )

    assert enqueued == []
    quote = _video_quote(out)
    [unit] = quote["units"]
    assert unit["outcome"] == "generate"
    assert (unit["script_duration_seconds"], unit["request_duration_seconds"], unit["tier_changed"]) == (4, 4, False)
    async with async_session_factory() as session:
        source = await load_video_price_source(session, facts.provider_id, facts.model_id)
    expected = quote_video_request_from_price(
        VideoRequestCostFacts(facts, 4, aspect_ratio="9:16", has_first_frame=True), source
    )
    assert expected.amount is not None
    assert unit["estimated_cost"] == expected.to_payload()
    assert quote["estimated_total"] == {expected.currency: pytest.approx(expected.amount)}


async def test_generate_videos_preview_states_plainly_that_a_unit_is_unpriced(
    fake_ctx: ToolHarness,
    set_admission_video_request_facts,
) -> None:
    """Storyboard-video units without calculable prices return unpriced, never zero costs or underestimated totals."""

    set_admission_video_request_facts(
        make_video_request_facts(provider_id="grok", model_id="grok-model-without-a-price", resolution="720p")
    )

    out = await run_generate_videos(fake_ctx, _selected("E1S01"), force=True, preview=True)

    quote = _video_quote(out)
    [unit] = quote["units"]
    assert unit["outcome"] == "generate"
    assert unit["unpriced"] is True
    assert unit["estimated_cost"]["amount"] is None
    assert unit["estimated_cost"]["unpriced_reason"] == "no_entry"
    assert quote["unpriced_count"] == 1
    assert quote["estimated_total"] is None
    assert "未定价" in out.value["summary"]
    assert "费用未知" in out.value["summary"]
