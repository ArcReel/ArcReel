"""Tests for the ``generate_videos`` tool handler and its storyboard / reference admission helpers."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import resource_relative_path
from lib.script.script_skeleton import SkeletonRouteMismatchError
from server.media_tools import videos as enqueue_videos_mod
from server.media_tools.context import generation_is_error
from server.tool_runtime import ToolOutcome
from tests.factories import make_video_request_facts
from tests.fakes import fake_reference_request_facts
from tests.integration.server.agent_tool_support import (
    _CLAIMED_BASIS_DIGEST,
    ToolHarness,
    activate_unbound_project,
    fake_caps_resolver,
    fake_reference_projection,
    fake_scene_batch,
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


_EPISODE_1: dict[str, Any] = {"scope": "episode", "episode": 1}
_ALL: dict[str, Any] = {"scope": "all"}
# 越出项目根的成片路径：清单无从检查这份产物，它的状态既不是「缺失」也不是「可用」。
_UNREADABLE_CLIP = "../outside/E1S01.mp4"


def _scene(unit_id: str) -> dict[str, Any]:
    return {"scope": "scene", "ids": [unit_id]}


def _selected(*unit_ids: str) -> dict[str, Any]:
    return {"scope": "selected", "ids": list(unit_ids)}


def _is_error(out: ToolOutcome[Any]) -> bool:
    """两宿主置 isError 的同一判定：problem，或终态结果未全部成功（待确认档位不算）。"""

    return out.problem is not None or generation_is_error(out.value)


def _text(out: ToolOutcome[Any]) -> str:
    """调用方读到的人读文本：problem 的 detail，或终态结果的摘要。"""

    if out.problem is not None:
        return out.problem.detail
    assert isinstance(out.value, dict)
    return out.value["summary"]


def _recording_batch(enqueued: list[Any]):
    """记下每批入队的 spec，不产出任何终态。"""

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


class _MissingEverythingResolver:
    """An active Manifest that never admits a formal artifact as usable."""

    def compare(self, key, *, artifact_path=None):
        from lib.artifacts.artifact_manifest import ArtifactComparison

        return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path or "")


def _activated_project(project_dir: Path, storyboard_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """构造一个已迁移到当前 schema 的项目，并把点名的分镜图登记进产物清单。

    直接调用入队构造函数的用例不经 pm，项目形态得在这里补齐：清单是读取已生成产物的
    唯一口径，没有登记的分镜图不能作为视频输入。
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
            ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=_CLAIMED_BASIS_DIGEST),
        )
    return project


def _refused_problems(refused: list[Any]) -> dict[str, tuple[str, str]]:
    """Map each refused ticket's unit ID to its ``(code, action)`` pair."""

    return {ticket.unit_id: (problem.code, problem.action.value) for ticket in refused for problem in ticket.problems}


# ---------------------------------------------------------------------------
# generate_videos：请求校验
# ---------------------------------------------------------------------------


_VALID_REQUEST: dict[str, Any] = {
    "script": "episode_1.json",
    "target": _EPISODE_1,
    "narration_delivery": "post_production",
}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"target": {"scope": "episode"}}, id="episode-without-number"),
        pytest.param({"target": {"scope": "episode", "episode": 1, "ids": ["E1S01"]}}, id="episode-with-ids"),
        pytest.param({"target": {"scope": "episode", "episode": 1, "extra": True}}, id="episode-unknown-field"),
        pytest.param({"target": {"scope": "all", "episode": 1}}, id="all-with-episode"),
        pytest.param({"target": {"scope": "all", "ids": ["E1S01"]}}, id="all-with-ids"),
        pytest.param({"target": {"scope": "scene", "ids": []}}, id="scene-without-id"),
        pytest.param({"target": {"scope": "scene", "ids": ["E1S01", "E1S02"]}}, id="scene-with-two-ids"),
        pytest.param({"target": {"scope": "scene", "ids": ["E1S01"], "episode": 1}}, id="scene-with-episode"),
        pytest.param({"target": {"scope": "selected"}}, id="selected-without-ids"),
        pytest.param({"target": {"scope": "selected", "ids": []}}, id="selected-with-empty-ids"),
        pytest.param({"target": _ALL, "force": True}, id="force-on-all"),
        pytest.param({"force": True}, id="force-on-episode"),
        pytest.param({"narration_delivery": None}, id="delivery-null"),
        pytest.param({"narration_delivery": "post-production"}, id="delivery-misspelled"),
        pytest.param({"narration_delivery": "tts"}, id="delivery-unknown"),
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


async def test_generate_videos_refuses_an_omitted_narration_delivery(fake_ctx: ToolHarness) -> None:
    """缺省不折成后期配音——那会让整批按调用方没选过的交付方式准入并计费。"""
    enqueue = AsyncMock(return_value=([], []))
    arguments = {key: value for key, value in _VALID_REQUEST.items() if key != "narration_delivery"}

    out = await run_declared_tool("generate_videos", fake_ctx, arguments, batch_waiter=enqueue)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    assert "narration_delivery" in out.problem.detail
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("retired_param", sorted(enqueue_videos_mod._RETIRED_PARAMS))
async def test_generate_videos_refuses_a_retired_param_with_its_replacement(
    fake_ctx: ToolHarness, retired_param: str
) -> None:
    """已退役的参数名被拒，报错点名该参数并给出当下写法。"""

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

    out = await run_generate_videos(fake_ctx, {"scope": "episode", "episode": 2}, batch_waiter=enqueue)

    assert out.problem is not None
    assert "不一致" in out.problem.detail
    enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# generate_videos：分镜图生视频
# ---------------------------------------------------------------------------


async def test_generate_videos_episode_scope_happy(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_episode_scope_skips_current_clip(fake_ctx: ToolHarness) -> None:
    """整集调用复用清单已认领的旧片段。"""

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
    """整集调用里某片段的 Manifest 比对抛错（BLOCKED）时必须报 blocked，不能落入
    「既不可复用也不算 blocked」的空档而被当作缺失去付费重生——不可读不等于没有。"""

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
    """整集入队先按 generated_assets.video_clip 过滤已完成条目。容器被外部编辑损坏为非 dict
    时该过滤须按「未生成」处理，而不是在 pending 过滤阶段就抛未处理 AttributeError；随后该条目
    以自己的问题码拦住整批，本次不创建任何任务。"""
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

    # E1S01 的分镜图绑定不可用：整批准入不成立，零任务入队，合法条目也如实报告被搁置的原因。
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


@pytest.mark.parametrize(
    ("route", "target", "force"),
    [
        pytest.param("storyboard", _scene("E1S01"), False, id="storyboard-scene"),
        pytest.param("storyboard", _selected("E1S01"), False, id="storyboard-selected"),
        pytest.param("storyboard", _ALL, None, id="storyboard-all"),
        pytest.param("reference_video", _scene("E1U1"), False, id="reference-scene"),
        pytest.param("reference_video", _selected("E1U1"), False, id="reference-selected"),
        pytest.param("reference_video", _ALL, None, id="reference-all"),
        pytest.param("reference_video", _EPISODE_1, None, id="reference-episode"),
    ],
)
async def test_generate_videos_reuses_the_selected_manual_upload(
    fake_ctx: ToolHarness,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    target: dict[str, Any],
    force: bool | None,
) -> None:
    """选中的手动上传与 Manifest 认定的 current / stale 同样可复用：不强制时既不入队也不重生，
    只作为 skipped 报告。清单一律报缺失，复用只能来自手动上传这一条腿。

    分镜图生视频的 episode scope 只认 Manifest 的 current / stale，不在此矩阵内。
    """
    from server.media_tools import videos as mod

    fake_ctx.pm.project_payload.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    if route == "reference_video":
        use_reference_route(fake_ctx)
        fake_ctx.pm.script_payload = reference_video_script()
        unit, resource_type = fake_ctx.pm.script_payload["video_units"][0], "reference_videos"
    else:
        unit, resource_type = fake_ctx.pm.script_payload["segments"][0], "videos"
    unit_id = str(unit.get("unit_id") or unit.get("segment_id"))
    video_path = _select_manual_video(
        fake_ctx.project_path, resource_type=resource_type, resource_id=unit_id, content=b"manual-video"
    )
    unit.setdefault("generated_assets", {})["video_clip"] = video_path
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _MissingEverythingResolver())
    monkeypatch.setattr(mod, "artifact_is_usable", lambda *_args: False)
    enqueue = AsyncMock(return_value=([], []))
    extra: dict[str, Any] = {} if force is None else {"force": force}

    out = await run_generate_videos(fake_ctx, target, batch_waiter=enqueue, **extra)

    assert not _is_error(out), out
    result = read_generation_result(out)
    assert result.requested == []
    assert [entry.unit_id for entry in result.skipped] == [unit_id]
    enqueue.assert_not_awaited()
    assert (await fake_ctx.queue.list_tasks(project_name="demo"))["items"] == []


async def test_generate_videos_scene_scope_happy(fake_ctx: ToolHarness) -> None:
    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True, batch_waiter=fake_scene_batch)

    assert not _is_error(out), out


async def test_generate_videos_scene_scope_use_tts_requires_exact_tier_and_queues_only_request_facts(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    from lib.speech.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationTtsStatus,
        VideoRequestCostFacts,
        prepare_narrated_video_duration,
    )
    from server.services.admission.cost_estimation import VideoRequestQuote

    async def fake_prepare(**kwargs):
        narration = NarrationDeliveryPreparation(
            delivery=USE_TTS,
            unit_id="E1S01",
            speech_mode=None,
            tts_status=NarrationTtsStatus.CURRENT,
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="basis",
            actual_duration_seconds=9.5,
            problems=(),
        )
        return replace(
            prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=4,
                supported_durations=(4, 8, 12),
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            ),
            cost=VideoRequestCostFacts(make_video_request_facts(provider_id="openai", model_id="sora-2"), 12),
        )

    enqueue = AsyncMock(side_effect=fake_scene_batch)
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.prepare_current_storyboard_narrated_video_duration",
        fake_prepare,
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(1.2, "USD", "openai", "sora-2", 12)),
    )

    pending = await run_generate_videos(
        fake_ctx, _scene("E1S01"), force=True, narration_delivery="use_tts", batch_waiter=enqueue
    )
    assert not _is_error(pending), pending
    assert pending.value["batch_admission"]["decision"] == "confirmation_required"
    enqueue.assert_not_awaited()

    completed = await run_generate_videos(
        fake_ctx,
        _scene("E1S01"),
        force=True,
        narration_delivery="use_tts",
        confirmed_request_duration_seconds=12,
        batch_waiter=enqueue,
    )

    assert not _is_error(completed), completed
    payload = enqueue.await_args.kwargs["specs"][0].payload
    assert "duration_seconds" not in payload
    assert payload["narration_delivery_options"] == {
        "narration_delivery": "use_tts",
        "confirmed_request_duration_seconds": 12,
    }
    assert "basis_digest" not in payload["narration_delivery_options"]
    assert "actual_duration_seconds" not in payload["narration_delivery_options"]


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
    """分镜图生视频的整批入口同样过发声准入：一个混合发声条目扣下整批，零任务入队。"""
    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        (project_dir / "storyboards" / f"scene_{segment_id}.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [
        {
            "segment_id": "E1S01",
            "novel_text": "风吹过旷野。",
            # 旁白与角色台词同时出现：需要重规划，不是可以直接下单的条目。
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
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.get_active_tasks_for_resources", AsyncMock(return_value=[])
    )

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
    """机械转换出的条目 video_prompt 为 None：整批受阻、零任务入队，回执点名待生成的条目。"""
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
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.get_active_tasks_for_resources", AsyncMock(return_value=[])
    )

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
        123,  # 剧本 JSON 里的脏数据（非字符串）须可读失败而非未处理 TypeError
        "/etc/passwd",  # 绝对路径：越权引用项目外文件
        "../../outside.png",  # `..` 穿越出项目目录
    ],
)
async def test_generate_videos_scene_scope_rejects_invalid_storyboard_image(
    fake_ctx: ToolHarness, storyboard_value: object
) -> None:
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True)

    assert _is_error(out)
    # 锁定 resolve_storyboard_image_ref 抛出的 canonical 消息，而不是模糊子串或通用失败文本
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
# generate_videos：参考生视频
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
    """``video_units`` 非数组：生成模式闸门只问键在不在，容器校验落在入队侧，
    须报出可定位的结构错误而不是下传到 unit 迭代抛 TypeError。"""
    use_reference_route(fake_ctx)
    for malformed in (
        {"E1U1": {}},
        {},
        "",
        False,
        None,
    ):
        # 键在场即按类型判定，不看真值：``{}`` / ``""`` / ``False`` 同样是类型错误，
        # 报成「为空」会把成因埋掉。
        fake_ctx.pm.script_payload = reference_video_script(video_units=malformed)

        out = await run_generate_videos(fake_ctx, _EPISODE_1)

        assert out.problem is not None
        assert "video_units 必须是数组" in out.problem.detail


async def test_generate_videos_episode_scope_reference_duration_needs_confirmation(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """申请秒数与剧本总时长不一致时，首次调用不入队，逐 ID 结果给出机器可读的待确认结论。"""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_active_tasks(**_kwargs):
        return []

    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.get_active_tasks_for_resources", fake_active_tasks
    )

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert not _is_error(out), out
    assert enqueued == []
    # 待确认不是 prose-only 的死角：调用方能拿到机器可读结论，不必解析文本猜测。
    result = read_generation_result(out)
    assert result.blocked == ["E1U1"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "reference_duration_confirmation_required"
    assert item.problem.action == "confirm_request_duration"


async def test_generate_videos_episode_scope_reference_duration_confirm_enqueues(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """带精确申请档位的再次调用按取档结果入队并生成成功。"""
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

    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )

    out = await run_generate_videos(fake_ctx, _EPISODE_1, confirmed_request_duration_seconds=8, batch_waiter=fake_batch)

    assert not _is_error(out), out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_confirms_two_tiers_in_one_batch(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """一批里档位不止一个时按 unit 确认，原目标集合仍作为一批重发，各任务带自己那一档确认。"""
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
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )

    out = await run_generate_videos(
        fake_ctx,
        _EPISODE_1,
        confirmed_request_durations={"E1U1": 8, "E1U2": 12},
        batch_waiter=_recording_batch(enqueued),
    )

    assert out.problem is None, out
    assert sorted(spec.resource_id for spec in enqueued) == ["E1U1", "E1U2"]
    # 各任务带的是自己那一档确认：worker 重投影时读任务上的这份选项，
    # 只写整批共用的一份会让准入已接受的档位在执行期重新变成待确认。
    confirmed = {
        spec.resource_id: (spec.payload or {})["reference_request_options"]["confirmed_request_duration_seconds"]
        for spec in enqueued
    }
    assert confirmed == {"E1U1": 8, "E1U2": 12}


async def test_generate_videos_episode_scope_reference_honors_requested_narration_delivery(
    fake_ctx: ToolHarness,
    monkeypatch,
) -> None:
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()
    enqueued: list[Any] = []
    projected_deliveries: list[str] = []
    base_projection = fake_reference_projection()

    async def _capture_delivery(**kwargs):
        projected_deliveries.append(kwargs["options"].narration_delivery)
        return await base_projection(**kwargs)

    active_tts = AsyncMock(return_value=frozenset())
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request", _capture_delivery
    )
    monkeypatch.setattr("server.services.admission.video_batch_admission.active_tts_resource_ids", active_tts)

    completed = await run_generate_videos(
        fake_ctx, _EPISODE_1, narration_delivery="post_production", batch_waiter=_recording_batch(enqueued)
    )

    assert completed.problem is None, completed
    assert projected_deliveries == ["post_production"]
    # 后期配音不查 TTS 在途状态：该路径不以 TTS 为输入。
    active_tts.assert_not_awaited()
    assert enqueued[0].payload["reference_request_options"] == {
        "narration_delivery": "post_production",
    }

    projected_deliveries.clear()
    await run_generate_videos(
        fake_ctx, _EPISODE_1, narration_delivery="use_tts", batch_waiter=_recording_batch(enqueued)
    )
    assert projected_deliveries == ["use_tts"]
    active_tts.assert_awaited()


async def test_generate_videos_episode_scope_reference_duration_repeat_without_confirm_still_blocked(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """不带确认参数的重复调用仍不入队。"""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )

    await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))
    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    assert not _is_error(out), out
    assert enqueued == []


async def test_generate_videos_episode_scope_reference_duration_exact_enqueues_directly(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """总时长为档位成员时单次调用直接入队，行为与现状一致。"""
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

    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=fake_batch)

    assert not _is_error(out), out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_reference_duration_resolves_project_context_once(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """批量预检让每个可入队 unit 都经过公共 request projection。"""
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
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck, calls=context_calls),
    )

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch(enqueued))

    # 三个 unit 均 5 秒、申请 8 秒 → 都需确认，本批不入队；实际水合桶随每个结果可观察。
    assert not _is_error(out), out
    assert context_calls == ["r2v", "r2v", "i2v"]
    assert enqueued == []


async def test_generate_videos_episode_scope_reference_skips_duration_context_when_nothing_to_precheck(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """整批都没有可预检的 unit 时不解析项目能力——解析推迟到第一个真正要取档的 unit，
    重构不能让「全部已完成/全部被跳过」的批次凭空多付一轮 DB 往返。"""
    script = reference_video_script()
    for unit in script["video_units"]:
        unit["text"] = ""
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script

    projection_calls: list[str] = []
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(calls=projection_calls),
    )

    await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch([]))

    assert projection_calls == []


async def test_generate_videos_episode_scope_reference_skips_duration_context_when_prompt_blank(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """正文全空白时 build_specs 会拒绝该 unit——预检须复用同一份结构校验提前判定，
    不能先触发项目能力解析再让 build_specs 事后跳过。"""
    script = reference_video_script()
    for unit in script["video_units"]:
        unit["text"] = "   "
    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script

    projection_calls: list[str] = []
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(calls=projection_calls),
    )

    out = await run_generate_videos(fake_ctx, _EPISODE_1, batch_waiter=_recording_batch([]))

    assert projection_calls == []
    assert read_generation_result(out).blocked == ["E1U1"]


async def test_generate_videos_episode_scope_ad_reference_duration_needs_confirmation(
    ad_reference_ctx: ToolHarness, monkeypatch
) -> None:
    """广告/短片的参考生视频走同一条视频单元时长确认闸门。"""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    seen_units: list[dict[str, Any]] = []

    def fake_precheck(ctx, unit):
        seen_units.append(unit)
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )

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
    """reference 路径的整集与点名入口共用确认闸门：未确认不入队、确认后入队。"""
    from lib.script.reference_video.duration_slots import UP, DurationSlot

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_active_tasks(**_kwargs):
        return []

    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.get_active_tasks_for_resources", fake_active_tasks
    )
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


async def test_generate_videos_scene_scope_reference_use_tts_queues_only_after_the_tier_is_confirmed(
    fake_ctx: ToolHarness,
    monkeypatch,
) -> None:
    from lib.script.reference_video.duration_slots import EXACT, DurationSlot
    from server.services.admission.cost_estimation import VideoRequestQuote

    use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = reference_video_script()

    def fake_precheck(_ctx, _unit):
        return DurationSlot(seconds=8, total_seconds=8, adjustment=EXACT)

    async def _current_options(**kwargs):
        return replace(
            kwargs["options"],
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=4,
        )

    enqueued: list[Any] = []
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.prepare_current_reference_video_request_options",
        _current_options,
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.project_reference_unit_request",
        fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.get_active_tasks_for_resources", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "server.services.admission.video_batch_admission.quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(0.8, "USD", "fake", "fake-r2v", 8)),
    )

    pending = await run_generate_videos(
        fake_ctx, _scene("E1U1"), force=True, narration_delivery="use_tts", batch_waiter=_recording_batch(enqueued)
    )

    assert not _is_error(pending), pending
    assert pending.value["batch_admission"]["decision"] == "confirmation_required"
    assert enqueued == []

    accepted = await run_generate_videos(
        fake_ctx,
        _scene("E1U1"),
        force=True,
        narration_delivery="use_tts",
        confirmed_request_duration_seconds=8,
        batch_waiter=_recording_batch(enqueued),
    )
    assert accepted.problem is None, accepted
    assert enqueued[0].payload["reference_request_options"] == {
        "narration_delivery": "use_tts",
        "confirmed_request_duration_seconds": 8,
    }


def test_asset_description_gate_rejects_invalid_description() -> None:
    """空白 / 非字符串描述都拿不到可用 description，由调用方按逐 ID blocked 报告，
    不应抛错（.strip()）或漏到 from_request 而中断整批。"""
    from lib.project.asset_types import ASSET_SPECS
    from server.media_tools.assets import _description_of, asset_unit_id

    bucket = ASSET_SPECS["character"].bucket_key
    project = {
        bucket: {
            "Alice": {"description": "   "},  # 空白
            "Carol": {"description": {"x": 1}},  # 非字符串，.strip() 会抛 AttributeError
            "Bob": {"description": "勇士"},
        }
    }

    assert _description_of(project, "character", asset_unit_id("character", "Alice")) is None
    assert _description_of(project, "character", asset_unit_id("character", "Carol")) is None
    assert _description_of(project, "character", asset_unit_id("character", "Bob")) == "勇士"


def test_asset_requested_ids_resolve_nfd_registered_key() -> None:
    """Agent 给的名字与桶 key 形态可以不同：按坐标系解析后落到真实落盘 key 的 unit ID。"""

    from lib.project.asset_types import ASSET_SPECS
    from server.media_tools.assets import _requested_unit_ids, asset_unit_id

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    bucket = ASSET_SPECS["character"].bucket_key
    project = {bucket: {name_nfd: {"description": "存量 NFD 角色"}}}

    assert _requested_unit_ids(project, "character", [name_nfc]) == [asset_unit_id("character", name_nfd)]
    # 同一资产的两种拼写解析到同一个 unit ID，只入一次队。
    assert _requested_unit_ids(project, "character", [name_nfc, name_nfd]) == [asset_unit_id("character", name_nfd)]


def test_build_video_specs_does_not_validate_duration_at_enqueue(tmp_path) -> None:
    """duration 是能力维度，入队侧不再校验——任意 duration 都透传给执行层（见 ADR-0001）。"""
    from server.services.admission.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S01.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S01": "storyboards/scene_S01.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "他在旷野上奔跑。",
            "video_prompt": "一个奔跑的镜头",
            "duration_seconds": 7,  # 不属于任何典型 supported_durations
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

    # 未显式指定 duration 时不携带该键，留给执行层按 caps 收口默认。
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
        123,  # 剧本 JSON 里的脏数据（非字符串）
        "/etc/passwd",  # 绝对路径
        "../../outside.png",  # `..` 穿越出项目目录
    ],
)
def test_build_video_specs_skips_invalid_storyboard_image_without_aborting_batch(
    tmp_path: Path, storyboard_value: object
) -> None:
    """批量入队场景下，单个条目 storyboard_image 非法（脏数据/越界/绝对路径）只记为该 ID 的
    blocked，不应让 `project_dir / storyboard_image` 抛未处理异常中断整批。"""
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
    """generated_assets 容器本身被外部编辑损坏为非 dict（如 list）时按「没有分镜图」跳过，
    不应让 `.get("storyboard_image")` 在非 dict 上抛未处理 AttributeError 中断整批。"""
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
    """generated_assets 容器本身非 dict 时须走「没有分镜图」的可读拒绝分支，
    不应在单条路径上抛未处理 AttributeError。"""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = ["bad"]

    out = await run_generate_videos(fake_ctx, _scene("E1S01"), force=True)

    assert _is_error(out)
    assert "请先运行 generate_storyboards" in _text(out)


def _admitted_video_yaml(item: dict, **kwargs) -> dict:
    """准入渲染出口产出的 YAML 文档。

    该出口与执行路径共用 ``render_storyboard_video_prompt``，反向约束是其中的 ``Avoid`` 键。
    """
    import yaml

    from server.services.admission.video_batch_admission import storyboard_video_prompt

    return yaml.safe_load(storyboard_video_prompt(item, **kwargs))


def test_storyboard_video_prompt_drama_sources_dialogue_from_utterances() -> None:
    """drama：准入渲染出口从分镜级 dialogue-kind utterances 派生 video YAML 台词，
    voiceover-kind 不进；narration / ad（无 utterances 字段）原样渲染既有 video_prompt.dialogue。"""
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
    """drama：传入带非空 voice_style 的角色资产时 YAML 顶部出现 Voice_Profiles；
    voice_characters 缺省（既有调用点行为）不注入。"""
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
    """utterances 迁移前的存量 drama 剧本（无 utterances 字段，台词仍在
    video_prompt.dialogue）：改走 legacy 出口派生 Voice_Profiles，不因缺 utterances 静默丢失。"""
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
    """narration/ad（item 无 utterances 字段）剧本 video_prompt 自带 voice_profiles 时一律剥离：
    该声明段唯一来源是 build_drama_video_prompt 的机械派生，剧本残留值不得越权、绕过 C 类
    （真无声）门控直达 YAML。"""
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
    """narration/ad：不解析 voice_consistency，直接跳过（无 drama dialogue speaker 概念）。"""
    from server.services.admission.video_batch_admission import resolve_voice_context as _resolve_voice_context

    assert await _resolve_voice_context(fake_ctx.pm.project_payload, "narration") is None


async def test_resolve_voice_context_drama_reads_project_characters_and_gate(
    fake_ctx: ToolHarness, monkeypatch
) -> None:
    """drama：读项目角色资产，无声（C 类真无声、或本集关闭音频）时退回不注入。"""
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
    """参考生视频 prompt 只用于统一结构守卫，不冻结进任务 payload。"""
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
    """正文全空白的 unit 被跳过并告警，不漏到执行层（结构校验上移到守卫点）。"""
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
    # 发声准入的问题码原样透出，调用方不必读文本判断下一步。
    assert _refused_problems(refused) == {"E1U1": ("mixed_speech", "replan_unit")}


def test_screening_keeps_bad_unit_ids_out_of_spec_building(tmp_path) -> None:
    """unit_id 为空或键缺失（Agent 裸写 JSON 可致）在筛查处按位置记名拒收，健康的 unit 照常构造。"""
    from server.media_tools.videos import _build_reference_specs
    from server.services.admission.video_batch_admission import screen_script_entries

    entries = [
        {"unit_id": "", "text": "@张三 推门"},  # 空串
        {"text": "@王五 起身"},  # 缺 unit_id 键
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    units, tickets = screen_script_entries(entries, requested_ids=None)

    assert [ticket.unit_id for ticket in tickets] == ["video_units[0]", "video_units[1]"]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert refused == []


def test_build_reference_specs_handles_a_non_string_text(tmp_path) -> None:
    """text 为显式 null 的畸形 unit 不应崩溃整批，且不得把 'None' 注入 prompt。"""
    from server.media_tools.videos import _build_reference_specs

    units = [
        # text 显式 null → 被守卫点按「text 必须是字符串」拒收（不注入 'None'）。
        {"unit_id": "E1U1", "text": None},
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert all("None" not in (s.payload.get("prompt") or "") for s in specs)
    # 显式拒收与静默跳过在 specs 上不可分辨，问题码才锁得住守卫点确实拒了这一条。
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
            output = ctx.project_path / "reference_videos" / f"{spec.resource_id}.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\x00")
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
    """脏 unit 元素交给逐条校验拒绝，不在完成扫描、音频闸门或时长预检抛未处理异常。"""
    valid = ad_reference_ctx.pm.script_payload["video_units"][0]
    ad_reference_ctx.pm.script_payload["video_units"] = ["bad", {}, valid]
    enqueued: list[Any] = []

    out = await run_generate_videos(
        ad_reference_ctx, _EPISODE_1, batch_waiter=_successful_reference_batch(ad_reference_ctx, enqueued)
    )

    # 脏 unit 逐条记为 blocked（没有 unit_id 可寻址时按位置编号），并拦住整批。
    assert enqueued == []
    result = read_generation_result(out)
    assert sorted(result.blocked) == ["E1U1", "video_units[0]", "video_units[1]"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1U1"] == "generation_batch_admission_withheld"


async def test_generate_videos_episode_scope_ad_reference_enqueues_existing_video_units(
    ad_reference_ctx: ToolHarness,
) -> None:
    """广告/短片的参考生视频直接消费自包含 video_units，不派生或写入 reference_units。"""
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
    """同名文件没有 generated_assets 归属时仍须入队，不能把孤儿文件报告为成功。"""
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
    """整集参考生视频里某 unit 已有成片、但 Manifest 比对抛错（BLOCKED）时必须报
    blocked，不能让 ``artifact_is_usable`` 的 fail-loud 异常穿透成整批 tool_error——
    与 storyboard 整集路线的同一场判定必须同步处理（同一个不可读产物、两条路线）。
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
    """迁移保留的已归属视频不能绕过 needs_replan 生成闸门。"""
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
    """广告点名重做沿用统一 video_unit 路径。"""
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
    """六个组合各自派给正确的生成模式，且骨架闸门放行本组合应有的骨架。"""
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
    """骨架来自另一种生成模式时六个组合一律拒绝入队，不静默按剧本形态改派。"""
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
    """分镜图生视频项目下的 video_units 骨架剧本：四个入口一律结构报错 + 重拆指引。

    静默降档与悄悄换路径都不可构造——存量混排集的唯一出路是重拆重生成。
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
    """反向：参考生视频项目下的分镜骨架剧本同样被拒，指引重跑 unit 拆分。"""

    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"

    out = await run_generate_videos(fake_ctx, _EPISODE_1)

    assert out.problem is not None
    assert "generate_script_plan" in out.problem.detail
