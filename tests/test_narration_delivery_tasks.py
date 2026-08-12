from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from server.services import narration_delivery_tasks


@pytest.mark.unit
async def test_current_settings_use_canonical_provider_and_actual_backend_model(monkeypatch) -> None:
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

    settings = await narration_delivery_tasks.CurrentTtsSettingsResolver("demo").resolve_tts_synthesis_settings(
        {"name": "demo"}
    )

    assert settings.provider_id == "custom-7"
    assert settings.model_id == "fallback-model"
    assert settings.voice == "alloy"
    assert settings.speed == 1.2


@pytest.mark.parametrize(
    ("actual_duration", "expected_code"),
    [(None, "video_duration_unavailable"), (6.1, "video_shorter_than_tts")],
)
@pytest.mark.unit
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

    with pytest.raises(NarratedVideoDurationBlockedError) as exc_info:
        await narration_delivery_tasks.require_generated_video_covers_current_tts(
            narration=narration,
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


@pytest.mark.unit
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
        )
        for locator in ("episode_1.json", "scripts/episode_1.json")
    ]


@pytest.mark.unit
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


@pytest.mark.unit
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
    )
    item = {
        "generated_assets": {
            "status": "completed",
            "video_clip": "videos/scene_E1S01.mp4",
            "video_uri": "provider://video/1",
        }
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
            minimum_actual_duration_seconds=6.2,
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


@pytest.mark.unit
@pytest.mark.parametrize(
    "unsafe_state",
    ["missing_metadata", "stale", "wrong_tier", "unselected_bytes", "short_media", "unmeasurable_media"],
)
async def test_current_visual_without_trustworthy_current_tier_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_state: str,
) -> None:
    from lib.version_manager import VersionManager

    current = tmp_path / "reference_videos" / "E1U1.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"paid-current-video")
    versions = VersionManager(tmp_path)
    metadata = {} if unsafe_state == "missing_metadata" else {"duration_seconds": 8}
    versions.add_version("reference_videos", "E1U1", "prompt", source_file=current, **metadata)
    item = {
        "generated_assets": {
            "status": "completed",
            "video_clip": "reference_videos/E1U1.mp4",
        },
        "stale": unsafe_state == "stale",
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
        assert (
            await narration_delivery_tasks.current_selected_video_tier(
                project_path=tmp_path,
                versions=versions,
                item=item,
                resource_type="reference_videos",
                resource_id="E1U1",
                minimum_actual_duration_seconds=6.2,
            )
            is None
        )

    assert (
        await narration_delivery_tasks.reuse_current_video_for_tier(
            project_path=tmp_path,
            versions=versions,
            item=item,
            resource_type="reference_videos",
            resource_id="E1U1",
            request_duration_seconds=request_duration,
            minimum_actual_duration_seconds=6.2,
        )
        is None
    )


@pytest.mark.unit
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
