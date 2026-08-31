from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from server.services import narration_delivery_tasks


def _typed_video_metadata(
    project_path: Path,
    *,
    resource_id: str,
    artifact_path: str,
    visual_basis_digest: str,
    request_duration_seconds: int = 8,
    register: bool = True,
) -> dict[str, object]:
    is_reference = artifact_path.startswith("reference_videos/")
    visual = ArtifactBasis.build(
        "artifact-visual/video-reference" if is_reference else "artifact-visual/video-storyboard",
        kind_version=1,
        inputs=(
            {
                "unit_id": resource_id,
                "visual_lines": ["Run."],
                "style": "cinematic",
                "canvas": {"aspect_ratio": "9:16"},
                "request_references": [],
            }
            if is_reference
            else {
                "resource_id": resource_id,
                "visual_prompt": {"action": "Run.", "camera_motion": "Static"},
                "canvas": {"aspect_ratio": "9:16"},
                "frames": [{"role": "storyboard", "sha256": "a" * 64}],
            }
        ),
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = build_video_duration_basis(request_duration_seconds)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=request_duration_seconds,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8, 12),
        reference_image_limit=0 if is_reference else None,
        parent_version=0,
    )
    if register:
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register_descriptor(
            ArtifactKey.episode_video(1, resource_id),
            artifact_path=artifact_path,
            basis=currency.video_descriptor,
        )
    return {
        "execution_checkpoint_schema_version": 3,
        "execution_script_file": "episode_1.json",
        "execution_duration_seconds": request_duration_seconds,
        "execution_request_digest": "d" * 64,
        "execution_provider_media": [],
        "artifact_video_currency": currency.to_dict(),
        "visual_basis_digest": visual_basis_digest,
    }


class _IdleQueue:
    """在途任务恒为空的生成队列替身；只实现被测路径用到的那一个查询。"""

    async def get_active_tasks_for_resources(self, **_kwargs) -> list[dict]:
        return []


def _stub_current_narration_reload(monkeypatch, tmp_path: Path, *, narration, resource_id: str = "E1S01"):
    """把当前旁白交付重载的三个协作者换成替身，让重载协程本体照跑。

    ``_prepare_current_task_narration_delivery`` 自己的剧本定位、单元准入与在途 TTS 判定仍走
    真实代码，只有项目管理器、交付准备与生成队列由替身给出，用例因而不必替换被测模块的步骤。
    """
    pm = MagicMock()
    pm.load_project.return_value = {
        "name": "demo",
        "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
    }
    pm.get_project_path.return_value = tmp_path
    pm.load_script.return_value = {
        "episode": 1,
        "content_mode": "narration",
        "segments": [{"segment_id": resource_id, "narration": "旁白。", "duration_seconds": 8}],
    }
    monkeypatch.setattr(narration_delivery_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(
        narration_delivery_tasks,
        "prepare_current_narration_delivery",
        AsyncMock(return_value=narration),
    )
    monkeypatch.setattr(narration_delivery_tasks, "get_generation_queue", _IdleQueue)
    return pm


async def test_current_settings_use_canonical_provider_and_actual_backend_model(monkeypatch, tmp_path: Path) -> None:
    from lib.config.resolver import ProviderModel
    from server.services.generation_context import AudioLaneResult, GenerationContext

    ctx = GenerationContext(
        generator=MagicMock(),
        audio_lane=AudioLaneResult(
            provider_model=ProviderModel("custom-7", "configured-model"),
            backend_name="openai",
            backend_model="fallback-model",
            narration_voice="alloy",
            narration_speed=1.2,
            voices=(),
        ),
    )
    resolve = AsyncMock(return_value=ctx)
    monkeypatch.setattr(narration_delivery_tasks, "resolve_generation_context", resolve)

    project = {"name": "demo"}
    project_path = tmp_path / "demo"
    settings = await narration_delivery_tasks.CurrentTtsSettingsResolver(
        "demo",
        user_id="owner",
        project_path=project_path,
    ).resolve_tts_synthesis_settings(project)

    assert settings.provider_id == "custom-7"
    assert settings.model_id == "fallback-model"
    assert settings.voice == "alloy"
    assert settings.speed == 1.2
    resolve.assert_awaited_once_with(
        "demo",
        None,
        project=project,
        project_path=project_path,
        user_id="owner",
        audio=narration_delivery_tasks.AudioLaneRequest(),
    )


@pytest.mark.parametrize(
    ("actual_duration", "expected_code"),
    [(None, "video_duration_unavailable"), (6.1, "video_shorter_than_tts")],
)
async def test_generated_video_rejection_restores_previous_current_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    actual_duration: float | None,
    expected_code: str,
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarratedVideoDurationBlockedError,
        NarrationDeliveryPreparation,
        NarrationTtsStatus,
    )

    output_path = tmp_path / "videos" / "scene_E1S01.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"paid-video")
    versions = MagicMock()
    monkeypatch.setattr(
        narration_delivery_tasks,
        "probe_existing_media_duration_seconds",
        AsyncMock(return_value=actual_duration),
    )
    narration = NarrationDeliveryPreparation(
        delivery=USE_TTS,
        unit_id="E1S01",
        speech_mode=None,
        tts_status=NarrationTtsStatus.CURRENT,
        artifact_path="audio/segment_E1S01.wav",
        basis_digest="basis",
        actual_duration_seconds=6.2,
        problems=(),
    )
    _stub_current_narration_reload(monkeypatch, tmp_path, narration=narration)

    with pytest.raises(NarratedVideoDurationBlockedError) as exc_info:
        await narration_delivery_tasks.require_generated_video_covers_current_tts(
            project_name="demo",
            script_file="episode_1.json",
            request_duration_seconds=8,
            output_path=output_path,
            versions=versions,
            resource_type="videos",
            resource_id="E1S01",
            version=2,
        )

    assert exc_info.value.code == expected_code
    versions.reject_current_version.assert_called_once_with(
        "videos",
        "E1S01",
        rejected_version=2,
        current_file=output_path,
    )


async def test_active_tts_observation_spans_script_locator_spellings() -> None:
    queue = AsyncMock()

    async def _query(**kwargs):
        if kwargs["script_file"] == "scripts/episode_1.json":
            return [{"resource_id": "E1U1", "script_file": kwargs["script_file"]}]
        return []

    queue.get_active_tasks_for_resources.side_effect = _query
    active = await narration_delivery_tasks.active_tts_resource_ids(
        project_name="demo",
        resource_ids=("E1U1", "E1U1", ""),
        script_file="episode_1.json",
        queue=queue,
    )

    assert active == frozenset({"E1U1"})
    assert queue.get_active_tasks_for_resources.await_args_list == [
        call(
            project_name="demo",
            task_type="tts",
            resource_ids=["E1U1"],
            script_file=locator,
            user_id="default",
        )
        for locator in ("episode_1.json", "scripts/episode_1.json")
    ]


async def test_empty_tts_observation_does_not_open_the_queue() -> None:
    queue = AsyncMock()

    active = await narration_delivery_tasks.active_tts_resource_ids(
        project_name="demo",
        resource_ids=(),
        script_file="episode_1.json",
        queue=queue,
    )

    assert active == frozenset()
    queue.get_active_tasks_for_resources.assert_not_called()


async def test_active_narrated_video_observation_filters_post_production_tasks() -> None:
    queue = AsyncMock()

    async def _query(**kwargs):
        if kwargs["script_file"] != "episode_1.json":
            return []
        key = "narration_delivery_options" if kwargs["task_type"] == "video" else "reference_request_options"
        return [
            {
                "resource_id": "E1S01",
                "payload": {key: {"narration_delivery": "use_tts"}},
            },
            {
                "resource_id": "E1S02",
                "payload": {key: {"narration_delivery": "post_production"}},
            },
        ]

    queue.get_active_tasks_for_resources.side_effect = _query

    active = await narration_delivery_tasks.active_narrated_video_resource_ids(
        project_name="demo",
        resource_ids=("E1S01", "E1S02"),
        script_file="episode_1.json",
        queue=queue,
    )

    assert active == frozenset({"E1S01"})


async def test_reference_tts_materialization_resolves_episode_from_script_filename(monkeypatch, tmp_path: Path) -> None:
    from lib.narration_delivery import USE_TTS
    from lib.reference_video.request_projection import ReferenceRequestOptions

    captured: dict[str, object] = {}

    async def _materialize(**kwargs):
        captured.update(kwargs)
        return kwargs["options"]

    monkeypatch.setattr(narration_delivery_tasks, "materialize_current_reference_request_options", _materialize)
    options = ReferenceRequestOptions(narration_delivery=USE_TTS)

    result = await narration_delivery_tasks.prepare_current_reference_video_request_options(
        project={"episodes": [{"episode": 7, "script_file": "scripts/episode_7.json"}]},
        script={"video_units": []},
        script_file="scripts/episode_7.json",
        unit={"unit_id": "E7U1"},
        project_path=tmp_path,
        options=options,
        project_name="demo",
    )

    assert result == options
    assert captured["episode"] == 7


async def test_current_reference_task_narration_uses_video_units_when_ad_script_also_has_shots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel = MagicMock()
    pm = MagicMock()
    pm.load_project.return_value = {
        "name": "demo",
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
    }
    pm.get_project_path.return_value = tmp_path
    pm.load_script.return_value = {
        "episode": 1,
        "content_mode": "ad",
        "shots": [{"shot_id": "E1S01", "voiceover_text": "广告旁白"}],
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "镜头推进。\n{视频单元旁白。}",
                "duration_seconds": 8,
            }
        ],
    }
    prepare = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(narration_delivery_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(narration_delivery_tasks, "prepare_current_narration_delivery", prepare)
    monkeypatch.setattr(narration_delivery_tasks, "tts_task_in_progress", AsyncMock(return_value=False))

    result = await narration_delivery_tasks._prepare_current_task_narration_delivery(
        project_name="demo",
        script_file="episode_1.json",
        resource_type="reference_videos",
        resource_id="E1U1",
    )

    assert result is sentinel
    assert prepare.await_args.kwargs["preparation"].unit_id == "E1U1"


async def test_current_visual_is_reused_only_for_the_selected_trusted_duration_tier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from lib.version_manager import VersionManager

    current = tmp_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"paid-current-video")
    versions = VersionManager(tmp_path)
    version = versions.add_version(
        "videos",
        "E1S01",
        "prompt",
        source_file=current,
        duration_seconds=8,
        **_typed_video_metadata(
            tmp_path,
            resource_id="E1S01",
            artifact_path="videos/scene_E1S01.mp4",
            visual_basis_digest="current-visual-basis",
            request_duration_seconds=4,
        ),
    )
    item = {
        "generated_assets": {
            "status": "pending",
            "video_clip": "legacy/wrong-path.mp4",
            "video_uri": "provider://video/1",
        },
        "stale": True,
    }

    monkeypatch.setattr(
        narration_delivery_tasks,
        "probe_existing_media_duration_seconds",
        AsyncMock(return_value=7.9),
    )
    assert (
        await narration_delivery_tasks.current_selected_video_tier(
            project_path=tmp_path,
            versions=versions,
            item=item,
            resource_type="videos",
            resource_id="E1S01",
            visual_basis_digest="current-visual-basis",
        )
        == 8
    )
    result = await narration_delivery_tasks.reuse_current_video_for_tier(
        project_path=tmp_path,
        versions=versions,
        item=item,
        resource_type="videos",
        resource_id="E1S01",
        request_duration_seconds=8,
        minimum_actual_duration_seconds=6.2,
        visual_basis_digest="current-visual-basis",
        revalidate_visual_basis_digest=lambda: "current-visual-basis",
    )

    assert result == {
        "version": version,
        "file_path": "videos/scene_E1S01.mp4",
        "created_at": versions.get_versions("videos", "E1S01")["versions"][0]["created_at"],
        "resource_type": "videos",
        "resource_id": "E1S01",
        "video_uri": "provider://video/1",
        "reused_existing": True,
        "request_duration_seconds": 8,
    }

    changed = await narration_delivery_tasks.reuse_current_video_for_tier(
        project_path=tmp_path,
        versions=versions,
        item=item,
        resource_type="videos",
        resource_id="E1S01",
        request_duration_seconds=8,
        minimum_actual_duration_seconds=6.2,
        visual_basis_digest="current-visual-basis",
        revalidate_visual_basis_digest=lambda: "changed-visual-basis",
    )

    assert changed is None


async def test_current_visual_tier_is_retained_when_media_is_too_short_for_current_tts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationTtsStatus,
        prepare_narrated_video_duration,
    )
    from lib.version_manager import VersionManager

    current = tmp_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"paid-current-video")
    versions = VersionManager(tmp_path)
    versions.add_version(
        "videos",
        "E1S01",
        "prompt",
        source_file=current,
        duration_seconds=4,
        **_typed_video_metadata(
            tmp_path,
            resource_id="E1S01",
            artifact_path="videos/scene_E1S01.mp4",
            visual_basis_digest="current-visual-basis",
        ),
    )
    item = {
        "generated_assets": {
            "status": "completed",
            "video_clip": "videos/scene_E1S01.mp4",
        }
    }
    duration_probe = AsyncMock(return_value=4.0)
    monkeypatch.setattr(narration_delivery_tasks, "probe_existing_media_duration_seconds", duration_probe)

    current_tier = await narration_delivery_tasks.current_selected_video_tier(
        project_path=tmp_path,
        versions=versions,
        item=item,
        resource_type="videos",
        resource_id="E1S01",
        visual_basis_digest="current-visual-basis",
    )
    duration_probe.assert_not_awaited()
    projection = prepare_narrated_video_duration(
        narration=NarrationDeliveryPreparation(
            delivery=USE_TTS,
            unit_id="E1S01",
            speech_mode=None,
            tts_status=NarrationTtsStatus.CURRENT,
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="current-audio-basis",
            actual_duration_seconds=7.0,
            problems=(),
        ),
        planned_duration_seconds=8,
        supported_durations=(4, 8),
        confirmed_request_duration_seconds=None,
        current_visual_duration_seconds=current_tier,
    )

    assert current_tier == 4
    assert [problem.code for problem in projection.problems] == ["reference_duration_confirmation_required"]
    assert projection.problems[0].parameters()["current_visual_duration"] == 4


@pytest.mark.parametrize(
    "unsafe_state",
    [
        "missing_metadata",
        "missing_manifest",
        "wrong_tier",
        "unselected_bytes",
        "short_media",
        "unmeasurable_media",
        "visual_inputs_changed",
    ],
)
async def test_current_visual_without_reusable_current_media_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_state: str,
) -> None:
    from lib.version_manager import VersionManager

    current = tmp_path / "reference_videos" / "E1U1.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"paid-current-video")
    versions = VersionManager(tmp_path)
    metadata = (
        {}
        if unsafe_state == "missing_metadata"
        else {
            "duration_seconds": 8,
            **_typed_video_metadata(
                tmp_path,
                resource_id="E1U1",
                artifact_path="reference_videos/E1U1.mp4",
                visual_basis_digest="recorded-visual-basis",
                register=unsafe_state != "missing_manifest",
            ),
        }
    )
    versions.add_version("reference_videos", "E1U1", "prompt", source_file=current, **metadata)
    item = {
        "generated_assets": {
            "status": "completed",
            "video_clip": "reference_videos/E1U1.mp4",
        }
    }
    request_duration = 12 if unsafe_state == "wrong_tier" else 8
    if unsafe_state == "unselected_bytes":
        current.write_bytes(b"untracked-overwrite")
    measured = None if unsafe_state == "unmeasurable_media" else 6.1 if unsafe_state == "short_media" else 8.0
    monkeypatch.setattr(
        narration_delivery_tasks,
        "probe_existing_media_duration_seconds",
        AsyncMock(return_value=measured),
    )

    if unsafe_state != "wrong_tier":
        assert await narration_delivery_tasks.current_selected_video_tier(
            project_path=tmp_path,
            versions=versions,
            item=item,
            resource_type="reference_videos",
            resource_id="E1U1",
            visual_basis_digest=(
                "changed-visual-basis" if unsafe_state == "visual_inputs_changed" else "recorded-visual-basis"
            ),
        ) == (8 if unsafe_state in {"short_media", "unmeasurable_media"} else None)

    assert (
        await narration_delivery_tasks.reuse_current_video_for_tier(
            project_path=tmp_path,
            versions=versions,
            item=item,
            resource_type="reference_videos",
            resource_id="E1U1",
            request_duration_seconds=request_duration,
            minimum_actual_duration_seconds=6.2,
            visual_basis_digest=(
                "changed-visual-basis" if unsafe_state == "visual_inputs_changed" else "recorded-visual-basis"
            ),
        )
        is None
    )


async def test_restored_rejected_short_video_is_not_reused_for_current_tts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from lib.version_manager import VersionManager

    current = tmp_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"long-video")
    versions = VersionManager(tmp_path)
    previous = versions.add_version("videos", "E1S01", "long", source_file=current, duration_seconds=8)
    current.write_bytes(b"short-paid-video")
    rejected = versions.add_version("videos", "E1S01", "short", source_file=current, duration_seconds=8)
    assert versions.reject_current_version(
        "videos",
        "E1S01",
        rejected_version=rejected,
        restore_version=previous,
        current_file=current,
    )
    versions.restore_version("videos", "E1S01", rejected, current)
    item = {
        "generated_assets": {
            "status": "completed",
            "video_clip": "videos/scene_E1S01.mp4",
        }
    }
    monkeypatch.setattr(
        narration_delivery_tasks,
        "probe_existing_media_duration_seconds",
        AsyncMock(return_value=6.1),
    )

    assert (
        await narration_delivery_tasks.reuse_current_video_for_tier(
            project_path=tmp_path,
            versions=versions,
            item=item,
            resource_type="videos",
            resource_id="E1S01",
            request_duration_seconds=8,
            minimum_actual_duration_seconds=6.2,
        )
        is None
    )
