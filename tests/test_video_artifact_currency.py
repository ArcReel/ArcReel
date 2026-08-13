from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.generation_queue import CompensableGenerationResult
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis
from lib.speech_artifact_provenance import (
    build_video_duration_basis,
    build_video_speech_basis,
    project_character_voice_evidence,
)
from lib.speech_composition import admit_script_unit
from lib.version_manager import PaidVersionCommit, VersionManager
from lib.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from server.services import video_artifact_currency
from server.services.video_artifact_currency import (
    VideoArtifactCommitter,
    build_current_video_artifact_basis,
    finalize_selected_video_result,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_formal_selection_preparation_turns_tts_validation_failure_into_history_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("generated video does not cover current TTS")
    validate = AsyncMock(side_effect=failure)
    monkeypatch.setattr(video_artifact_currency, "validate_generated_video_covers_current_tts", validate)
    committer = VideoArtifactCommitter(
        project_manager=MagicMock(),
        project_name="demo",
        project_path=tmp_path,
        versions=MagicMock(),
        resource_type="videos",
        resource_id="E1S01",
        prompt="p",
    )
    staged = tmp_path / "staged.mp4"
    staged.write_bytes(b"paid-video")
    metadata = {
        "execution_script_file": "episode_1.json",
        "execution_narration": {"delivery": "use_tts"},
    }

    await committer.prepare_selection(staged, 8, metadata)

    assert committer.selection_error is failure
    validate.assert_awaited_once_with(
        project_name="demo",
        script_file="episode_1.json",
        request_duration_seconds=8,
        output_path=staged,
        resource_type="videos",
        resource_id="E1S01",
    )


@pytest.mark.asyncio
async def test_failed_formal_selection_validation_archives_paid_video_without_current_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "demo"
    current = project_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")
    staged = current.with_name(".paid-staged.mp4")
    staged.write_bytes(b"short-paid-video")
    versions = VersionManager(project_path)
    old_version = versions.add_version("videos", "E1S01", "old", source_file=current)
    frozen = ArtifactBasisDescriptor.from_basis(
        compose_video_artifact_basis(
            visual=build_video_duration_basis(1),
            speech=build_video_duration_basis(2),
            duration=build_video_duration_basis(8),
        )
    )

    class _PM:
        @contextmanager
        def locked_project_script_snapshot(self, *_args):
            yield {}, {}

    failure = RuntimeError("short output")
    monkeypatch.setattr(
        video_artifact_currency,
        "validate_generated_video_covers_current_tts",
        AsyncMock(side_effect=failure),
    )
    committer = VideoArtifactCommitter(
        project_manager=_PM(),  # type: ignore[arg-type]
        project_name="demo",
        project_path=project_path,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        prompt="p",
    )
    metadata = {
        "artifact_episode": 1,
        "artifact_video_basis": frozen.to_dict(),
        "execution_script_file": "episode_1.json",
        "execution_narration": {"delivery": "use_tts"},
    }

    await committer.prepare_selection(staged, 8, metadata)
    outcome = committer(staged, current, 8, metadata)

    assert committer.selection_error is failure
    assert outcome.selected is False
    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert (project_path / history["versions"][-1]["file"]).read_bytes() == b"short-paid-video"


def test_selected_video_cancellation_compensation_restores_media_manifest_and_only_video_asset_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "demo"
    current = project_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")
    thumbnail = project_path / "thumbnails" / "scene_E1S01.jpg"
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b"old-thumbnail")
    staged = current.with_name(".new-paid.mp4")
    staged.write_bytes(b"new-paid")
    versions = VersionManager(project_path)
    old_version = versions.add_version("videos", "E1S01", "old", source_file=current)
    old_basis = ArtifactBasisDescriptor.from_basis(
        compose_video_artifact_basis(
            visual=build_video_duration_basis(1),
            speech=build_video_duration_basis(2),
            duration=build_video_duration_basis(4),
        )
    )
    new_basis = ArtifactBasisDescriptor.from_basis(
        compose_video_artifact_basis(
            visual=build_video_duration_basis(1),
            speech=build_video_duration_basis(2),
            duration=build_video_duration_basis(8),
        )
    )
    adapter = ProjectArtifactManifestAdapter(project_path)
    ArtifactManifest(adapter).register_descriptor(
        ArtifactKey.episode_video(1, "E1S01"),
        artifact_path="videos/scene_E1S01.mp4",
        basis=old_basis,
    )
    script = {
        "episode": 1,
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "n",
                "generated_assets": {
                    "video_clip": "videos/old.mp4",
                    "video_uri": "provider://old",
                    "video_thumbnail": "thumbnails/scene_E1S01.jpg",
                    "status": "completed",
                    "unrelated": "keep",
                },
            }
        ],
    }
    project = {"episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}]}

    class _PM:
        @contextmanager
        def locked_project_script_snapshot(self, *_args):
            yield project, script

        @contextmanager
        def locked_episode_script(self, _name, resolve_script_file, **kwargs):
            resolve_script_file(project)
            yield script
            if callback := kwargs.get("on_commit"):
                callback(project_path / "scripts" / "episode_1.json")

        @staticmethod
        def update_scene_status(item):
            item["generated_assets"]["status"] = "completed"

    monkeypatch.setattr(video_artifact_currency, "build_current_video_artifact_basis", lambda **_kwargs: new_basis)
    committer = VideoArtifactCommitter(
        project_manager=_PM(),  # type: ignore[arg-type]
        project_name="demo",
        project_path=project_path,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        prompt="new",
    )
    metadata = {
        "artifact_episode": 1,
        "artifact_video_basis": new_basis.to_dict(),
        "execution_script_file": "episode_1.json",
        "execution_narration": {"delivery": "post_production"},
    }

    outcome = committer(staged, current, 8, metadata)
    assert outcome.selected is True
    assets = script["segments"][0]["generated_assets"]
    assets.update(
        {
            "video_clip": "videos/scene_E1S01.mp4",
            "video_uri": "provider://new",
            "video_thumbnail": "thumbnails/scene_E1S01.jpg",
            "unrelated": "concurrent",
        }
    )
    thumbnail.write_bytes(b"new-thumbnail")

    assert committer.compensate_selection() is True

    assert current.read_bytes() == b"old-current"
    assert versions.get_current_version("videos", "E1S01") == old_version
    assert adapter.get_entry(ArtifactKey.episode_video(1, "E1S01")).basis_digest == old_basis.digest
    assert thumbnail.read_bytes() == b"old-thumbnail"
    assert assets == {
        "video_clip": "videos/old.mp4",
        "video_uri": "provider://old",
        "video_thumbnail": "thumbnails/scene_E1S01.jpg",
        "status": "completed",
        "unrelated": "concurrent",
    }


@pytest.mark.asyncio
async def test_selected_video_finalize_failure_is_compensated_before_reraising() -> None:
    failure = RuntimeError("finalize failed")
    committer = MagicMock()
    committer.outcome = PaidVersionCommit(version=2, selected=True)
    committer.compensate_selection.return_value = True

    async def _finalize() -> dict[str, object]:
        raise failure

    with pytest.raises(RuntimeError, match="finalize failed") as caught:
        await finalize_selected_video_result(committer=committer, finalize=_finalize)

    assert caught.value is failure
    committer.compensate_selection.assert_called_once_with()


@pytest.mark.asyncio
async def test_selected_video_finalize_result_compensates_once_when_terminal_cancellation_wins() -> None:
    committer = MagicMock()
    committer.outcome = PaidVersionCommit(version=2, selected=True)
    committer.compensate_selection.return_value = True

    async def _finalize() -> dict[str, object]:
        return {"version": 2, "selected_current": True}

    result = await finalize_selected_video_result(committer=committer, finalize=_finalize)

    assert isinstance(result, CompensableGenerationResult)
    assert result == {"version": 2, "selected_current": True}
    result.compensate_cancelled()
    result.compensate_cancelled()
    committer.compensate_selection.assert_called_once_with()


def _storyboard_state(tmp_path: Path) -> tuple[Path, dict, dict, dict[str, object]]:
    project_path = tmp_path / "demo"
    storyboard = project_path / "storyboards" / "scene_E1S01.png"
    storyboard.parent.mkdir(parents=True)
    storyboard.write_bytes(b"storyboard")
    project = {
        "content_mode": "drama",
        "aspect_ratio": {"videos": "16:9"},
        "characters": {"阿离": {"voice_style": "清亮"}},
    }
    script = {
        "content_mode": "drama",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 4,
                "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "快走。"}],
                "video_prompt": {"action": "她冲出门", "camera_motion": "Track"},
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }
    metadata: dict[str, object] = {
        "artifact_episode": 1,
        "artifact_voice_style_speakers": ["阿离"],
        "artifact_duration_tiers": [4, 8],
        "artifact_reference_image_limit": None,
        "execution_script_file": "episode_1.json",
        "execution_provider_media": [],
        "execution_narration": {
            "delivery": "post_production",
            "tts_status": "not_applicable",
            "artifact_path": "",
            "basis_digest": None,
            "actual_duration_seconds": None,
        },
    }
    return project_path, project, script, metadata


def test_storyboard_current_basis_tracks_speech_and_ignores_provider_metadata(tmp_path: Path) -> None:
    project_path, project, script, metadata = _storyboard_state(tmp_path)
    versions = VersionManager(project_path)
    preparation = admit_script_unit("scenes", script["scenes"][0]).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt=script["scenes"][0]["video_prompt"],
        storyboard_image=project_path / "storyboards" / "scene_E1S01.png",
        end_frame_image=None,
        aspect_ratio="16:9",
    )
    speech = build_video_speech_basis(
        preparation,
        voices=project_character_voice_evidence(
            preparation,
            characters=project["characters"],
            voice_style_speakers=("阿离",),
        ),
    )
    expected = ArtifactBasisDescriptor.from_basis(
        compose_video_artifact_basis(
            visual=visual,
            speech=speech,
            duration=build_video_duration_basis(4),
        )
    )

    assert (
        build_current_video_artifact_basis(
            project_path=project_path,
            project=project,
            script=script,
            resource_type="videos",
            resource_id="E1S01",
            versions=versions,
            version_metadata={**metadata, "execution_provider_id": "changed-provider"},
        )
        == expected
    )

    changed_project = deepcopy(project)
    changed_project["characters"]["阿离"]["voice_style"] = "低沉"
    assert (
        build_current_video_artifact_basis(
            project_path=project_path,
            project=changed_project,
            script=script,
            resource_type="videos",
            resource_id="E1S01",
            versions=versions,
            version_metadata=metadata,
        )
        != expected
    )


def test_current_selected_tts_changes_video_only_when_it_crosses_a_frozen_tier(tmp_path: Path) -> None:
    project_path, project, script, metadata = _storyboard_state(tmp_path)
    project["content_mode"] = "narration"
    project["characters"] = {}
    script = {
        "content_mode": "narration",
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "novel_text": "风吹过旷野。",
                "video_prompt": {"action": "荒野长风", "camera_motion": "Static"},
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }
    metadata["artifact_voice_style_speakers"] = []
    metadata["execution_narration"] = {"delivery": "use_tts"}
    versions = VersionManager(project_path)
    audio = project_path / "audio" / "segment_E1S01.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    preparation = admit_script_unit("segments", script["segments"][0]).preparation
    audio_basis = build_narration_audio_basis(
        preparation,
        TtsSynthesisSettings(provider_id="p", model_id="m", voice="v", speed=1.0),
    )
    descriptor = ArtifactBasisDescriptor.from_basis(audio_basis)
    versions.add_version(
        "audio",
        "E1S01",
        "wind",
        source_file=audio,
        artifact_audio_basis=descriptor.to_dict(),
        tts_actual_duration_seconds=7.0,
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register_descriptor(
        ArtifactKey.episode_audio(1, "E1S01"),
        artifact_path="audio/segment_E1S01.wav",
        basis=descriptor,
    )

    long_basis = build_current_video_artifact_basis(
        project_path=project_path,
        project=project,
        script=script,
        resource_type="videos",
        resource_id="E1S01",
        versions=versions,
        version_metadata=metadata,
    )

    versions.add_version(
        "audio",
        "E1S01",
        "wind shorter",
        source_file=audio,
        artifact_audio_basis=descriptor.to_dict(),
        tts_actual_duration_seconds=6.5,
    )
    same_tier = build_current_video_artifact_basis(
        project_path=project_path,
        project=project,
        script=script,
        resource_type="videos",
        resource_id="E1S01",
        versions=versions,
        version_metadata=metadata,
    )
    versions.add_version(
        "audio",
        "E1S01",
        "wind short",
        source_file=audio,
        artifact_audio_basis=descriptor.to_dict(),
        tts_actual_duration_seconds=3.5,
    )
    shorter_tier = build_current_video_artifact_basis(
        project_path=project_path,
        project=project,
        script=script,
        resource_type="videos",
        resource_id="E1S01",
        versions=versions,
        version_metadata=metadata,
    )

    assert long_basis == same_tier
    assert shorter_tier != long_basis
