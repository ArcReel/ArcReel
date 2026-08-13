from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.generation_queue import CompensableGenerationResult
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis
from lib.reference_video.execution_checkpoint import NarrationExecutionFacts
from lib.speech_artifact_provenance import (
    build_video_duration_basis,
    build_video_speech_basis,
    project_character_voice_evidence,
)
from lib.speech_composition import admit_script_unit
from lib.version_manager import PaidVersionCommit, VersionManager
from lib.video_artifact_facts import VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD, VideoArtifactCurrencyFacts
from lib.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from server.services import video_artifact_currency
from server.services.artifact_version_restore import is_typed_media_version_restorable
from server.services.video_artifact_currency import (
    VideoArtifactCommitter,
    build_current_video_artifact_basis,
    complete_video_artifact_commit,
    finalize_selected_video_result,
)

pytestmark = pytest.mark.integration


def _currency(
    label: str,
    *,
    request_duration: int = 8,
    duration_tiers: tuple[int, ...] = (4, 8),
    parent_version: int = 0,
) -> VideoArtifactCurrencyFacts:
    visual = ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": label,
            "visual_prompt": {"action": label, "camera_motion": "Static"},
            "canvas": {"aspect_ratio": "9:16"},
            "frames": [{"role": "storyboard", "sha256": "a" * 64}],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = build_video_duration_basis(request_duration)
    return VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=request_duration,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=duration_tiers,
        reference_image_limit=None,
        parent_version=parent_version,
    )


@pytest.mark.asyncio
async def test_shared_video_completion_returns_nonselected_paid_history_without_finalizing(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    paid = project_path / "paid.mp4"
    paid.parent.mkdir(parents=True)
    paid.write_bytes(b"paid")
    versions = VersionManager(project_path)
    version = versions.add_version("videos", "E1S01", "p", source_file=paid)
    committer = MagicMock()
    committer.outcome = PaidVersionCommit(version=version, selected=False)
    committer.selection_error = None
    finalize = AsyncMock()
    completed = MagicMock()

    result = await complete_video_artifact_commit(
        committer=committer,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        version=version,
        video_uri="provider://paid",
        finalize=finalize,
        on_completed=completed,
    )

    assert result["selected_current"] is False
    assert (project_path / str(result["file_path"])).read_bytes() == b"paid"
    finalize.assert_not_awaited()
    completed.assert_called_once_with()


@pytest.mark.asyncio
async def test_formal_selection_preparation_turns_execution_tts_validation_failure_into_history_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("generated video does not cover execution TTS")
    validate = AsyncMock(side_effect=failure)
    monkeypatch.setattr(video_artifact_currency, "validate_generated_video_covers_tts_duration", validate)
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
        "execution_narration": NarrationExecutionFacts(
            delivery="use_tts",
            tts_status="current",
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="sha256-v1:" + "a" * 64,
            actual_duration_seconds=6.2,
        ).to_dict(),
    }

    await committer.prepare_selection(staged, 8, metadata)

    assert committer.selection_error is failure
    validate.assert_awaited_once_with(
        resource_id="E1S01",
        request_duration_seconds=8,
        output_path=staged,
        tts_actual_duration_seconds=6.2,
    )


@pytest.mark.asyncio
async def test_formal_selection_validates_frozen_tts_when_current_tts_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paid result is judged against the TTS accepted at submission, not mutable current state."""

    current_projection = AsyncMock(side_effect=RuntimeError("current TTS became stale"))
    monkeypatch.setattr(
        "server.services.narration_delivery_tasks._prepare_current_task_narration_delivery",
        current_projection,
    )
    monkeypatch.setattr(
        "server.services.narration_delivery_tasks.probe_existing_media_duration_seconds",
        AsyncMock(return_value=8.0),
    )
    monkeypatch.setattr(
        video_artifact_currency.CurrentTtsSettingsResolver,
        "resolve_tts_synthesis_settings",
        AsyncMock(side_effect=ValueError("current TTS is no longer configured")),
    )
    project_manager = MagicMock()
    project_manager.load_project.return_value = {}
    committer = VideoArtifactCommitter(
        project_manager=project_manager,
        project_name="demo",
        project_path=tmp_path,
        versions=MagicMock(),
        resource_type="videos",
        resource_id="E1S01",
        prompt="p",
    )
    staged = tmp_path / "staged.mp4"
    staged.write_bytes(b"paid-video")
    narration = NarrationExecutionFacts(
        delivery="use_tts",
        tts_status="current",
        artifact_path="audio/segment_E1S01.wav",
        basis_digest="sha256-v1:" + "a" * 64,
        actual_duration_seconds=6.2,
    )

    await committer.prepare_selection(
        staged,
        8,
        {
            "artifact_video_currency": _currency("frozen").to_dict(),
            "execution_script_file": "episode_1.json",
            "execution_narration": narration.to_dict(),
        },
    )

    assert committer.selection_error is None
    current_projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_formal_selection_reloads_current_tts_settings_for_currency_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "demo"
    current = project_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")
    staged = current.with_name(".paid-staged.mp4")
    staged.write_bytes(b"paid-video")
    versions = VersionManager(project_path)
    old_version = versions.add_version("videos", "E1S01", "old", source_file=current)
    currency = _currency("frozen", parent_version=old_version)
    current_settings = TtsSynthesisSettings("new-provider", "new-model", "new-voice", 1.2)

    class _PM:
        @staticmethod
        def load_project(_name):
            return {}

        @contextmanager
        def locked_project_script_snapshot(self, *_args):
            yield {}, {}

    monkeypatch.setattr(
        video_artifact_currency,
        "validate_generated_video_covers_tts_duration",
        AsyncMock(),
    )
    resolve_settings = AsyncMock(return_value=current_settings)
    monkeypatch.setattr(
        video_artifact_currency.CurrentTtsSettingsResolver,
        "resolve_tts_synthesis_settings",
        resolve_settings,
    )

    def _current_basis(**kwargs):
        assert kwargs["current_tts_settings"] == current_settings
        return ArtifactBasisDescriptor.from_basis(build_video_duration_basis(12))

    monkeypatch.setattr(video_artifact_currency, "build_current_video_artifact_basis", _current_basis)
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
        "artifact_video_currency": currency.to_dict(),
        "execution_script_file": "episode_1.json",
        "execution_narration": NarrationExecutionFacts(
            delivery="use_tts",
            tts_status="current",
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="sha256-v1:" + "a" * 64,
            actual_duration_seconds=6.2,
        ).to_dict(),
    }

    await committer.prepare_selection(staged, 8, metadata)
    outcome = committer(staged, current, 8, metadata)

    assert outcome.selected is False
    resolve_settings.assert_awaited_once_with({})


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
    currency = _currency("frozen", parent_version=old_version)

    class _PM:
        @staticmethod
        def load_project(_name):
            return {}

        @contextmanager
        def locked_project_script_snapshot(self, *_args):
            yield {}, {}

    failure = RuntimeError("short output")
    monkeypatch.setattr(
        video_artifact_currency,
        "validate_generated_video_covers_tts_duration",
        AsyncMock(side_effect=failure),
    )
    monkeypatch.setattr(
        video_artifact_currency.CurrentTtsSettingsResolver,
        "resolve_tts_synthesis_settings",
        AsyncMock(return_value=TtsSynthesisSettings("p", "m", "v", None)),
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
        "artifact_video_currency": currency.to_dict(),
        "execution_checkpoint_schema_version": 3,
        "execution_duration_seconds": 8,
        "execution_request_digest": "d" * 64,
        "execution_script_file": "episode_1.json",
        "execution_narration": NarrationExecutionFacts(
            delivery="use_tts",
            tts_status="current",
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="sha256-v1:" + "a" * 64,
            actual_duration_seconds=6.2,
        ).to_dict(),
    }

    await committer.prepare_selection(staged, 8, metadata)
    outcome = committer(staged, current, 8, metadata)

    assert committer.selection_error is failure
    assert outcome.selected is False
    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    rejected = history["versions"][-1]
    assert (project_path / rejected["file"]).read_bytes() == b"short-paid-video"
    assert rejected[VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD] == "output_duration_unverified"
    assert is_typed_media_version_restorable("videos", rejected) is False


@pytest.mark.parametrize("script_rebound", [False, True])
def test_selected_video_cancellation_compensation_restores_media_manifest_and_only_video_asset_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script_rebound: bool,
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
    new_currency = _currency("new", parent_version=old_version)
    new_basis = new_currency.video_descriptor
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
        "artifact_video_currency": new_currency.to_dict(),
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
    if script_rebound:
        project["episodes"][0]["script_file"] = "scripts/rebound_episode_1.json"

    assert committer.compensate_selection() is True

    assert current.read_bytes() == b"old-current"
    assert versions.get_current_version("videos", "E1S01") == old_version
    manifest_entry = adapter.get_entry(ArtifactKey.episode_video(1, "E1S01"))
    assert manifest_entry is not None
    assert manifest_entry.basis_digest == old_basis.digest
    assert thumbnail.read_bytes() == b"old-thumbnail"
    if not script_rebound:
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


@pytest.mark.asyncio
async def test_terminal_cancellation_does_not_silently_ignore_incomplete_video_compensation() -> None:
    committer = MagicMock()
    committer.outcome = PaidVersionCommit(version=2, selected=True)
    committer.compensate_selection.return_value = False

    async def _finalize() -> dict[str, object]:
        return {"version": 2, "selected_current": True}

    result = await finalize_selected_video_result(committer=committer, finalize=_finalize)

    with pytest.raises(RuntimeError, match="remains selected"):
        result.compensate_cancelled()


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
    preparation = admit_script_unit("scenes", script["scenes"][0]).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt=script["scenes"][0]["video_prompt"],
        storyboard_image=storyboard,
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
    duration = build_video_duration_basis(4)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=("阿离",),
        duration_tiers=(4, 8),
        reference_image_limit=None,
        parent_version=0,
    )
    metadata: dict[str, object] = {
        "artifact_video_currency": currency.to_dict(),
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


def test_current_video_basis_rejects_an_episode_rebound_to_another_script(tmp_path: Path) -> None:
    project_path, project, script, metadata = _storyboard_state(tmp_path)
    project["episodes"] = [{"episode": 1, "script_file": "scripts/rebound_episode_1.json"}]

    assert (
        build_current_video_artifact_basis(
            project_path=project_path,
            project=project,
            script=script,
            resource_type="videos",
            resource_id="E1S01",
            versions=VersionManager(project_path),
            version_metadata=metadata,
        )
        is None
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
    metadata["execution_narration"] = {"delivery": "use_tts"}
    metadata["artifact_video_currency"] = _currency(
        "narration",
        request_duration=8,
        duration_tiers=(4, 8),
    ).to_dict()
    versions = VersionManager(project_path)
    audio = project_path / "audio" / "segment_E1S01.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    preparation = admit_script_unit("segments", script["segments"][0]).preparation
    settings = TtsSynthesisSettings(provider_id="p", model_id="m", voice="v", speed=1.0)
    audio_basis = build_narration_audio_basis(
        preparation,
        settings,
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
        current_tts_settings=settings,
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
        current_tts_settings=settings,
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
        current_tts_settings=settings,
    )
    stale_script = deepcopy(script)
    stale_script["segments"][0]["novel_text"] = "旁白已修改，当前配音不再 fresh。"
    stale_tts = build_current_video_artifact_basis(
        project_path=project_path,
        project=project,
        script=stale_script,
        resource_type="videos",
        resource_id="E1S01",
        versions=versions,
        version_metadata=metadata,
        current_tts_settings=settings,
    )
    ProjectArtifactManifestAdapter(project_path).delete_entry(ArtifactKey.episode_audio(1, "E1S01"))
    unavailable_tts = build_current_video_artifact_basis(
        project_path=project_path,
        project=project,
        script=script,
        resource_type="videos",
        resource_id="E1S01",
        versions=versions,
        version_metadata=metadata,
        current_tts_settings=settings,
    )

    assert long_basis == same_tier
    assert shorter_tier != long_basis
    assert stale_tts == long_basis
    assert unavailable_tts == long_basis
