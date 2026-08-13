"""Project adapter contracts for the shared presentation read model."""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis, canonical_narration_text
from lib.project_manager import ProjectManager
from lib.speech_artifact_provenance import (
    build_video_duration_basis,
    build_video_speech_basis,
    media_content_digest,
)
from lib.speech_composition import admit_script_unit
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from server.services.presentation_bundle import PresentationBundleService
from server.services.presentation_read_model import PresentationReadModelService, PresentationUnavailableError

pytestmark = pytest.mark.integration


class _SettingsResolver:
    def __init__(self, settings: TtsSynthesisSettings) -> None:
        self.settings = settings

    async def resolve_tts_synthesis_settings(self, _project: dict) -> TtsSynthesisSettings:
        return self.settings


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _setup_narrator_project(tmp_path: Path) -> tuple[ProjectManager, Path, TtsSynthesisSettings]:
    projects_root = tmp_path / "projects"
    project_path = projects_root / "demo"
    for subdir in ("scripts", "storyboards", "videos", "audio"):
        (project_path / subdir).mkdir(parents=True, exist_ok=True)
    project = {
        "title": "Demo",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "grid_storyboard": False,
        "aspect_ratio": "9:16",
        "default_duration": 8,
        "characters": {},
        "episodes": [{"episode": 1, "title": "One", "script_file": "scripts/episode_1.json"}],
    }
    item = {
        "segment_id": "E1S01",
        "duration_seconds": 8,
        "novel_text": "一\n二二",
        "video_prompt": {"action": "Clouds move", "camera_motion": "Static"},
        "generated_assets": {
            "storyboard_image": "storyboards/scene_E1S01.png",
            "video_clip": "videos/scene_E1S01.mp4",
            "narration_audio": "audio/segment_E1S01.wav",
        },
    }
    script = {"episode": 1, "content_mode": "narration", "segments": [item]}
    _write_json(project_path / "project.json", project)
    _write_json(project_path / "scripts" / "episode_1.json", script)
    storyboard = project_path / "storyboards" / "scene_E1S01.png"
    storyboard.write_bytes(b"storyboard")
    video = project_path / "videos" / "scene_E1S01.mp4"
    video.write_bytes(b"provider-video-v1")
    audio = project_path / "audio" / "segment_E1S01.wav"
    audio.write_bytes(b"tts-audio-v1")

    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt=item["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(8)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8, 12),
        reference_image_limit=None,
        parent_version=0,
    )
    versions = VersionManager(project_path)
    versions.add_version(
        "videos",
        "E1S01",
        "video",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_script_file="episode_1.json",
        execution_duration_seconds=8,
        execution_request_digest="d" * 64,
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=currency.to_dict(),
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_video(1, "E1S01"),
        artifact_path="videos/scene_E1S01.mp4",
        basis=currency.video_basis,
    )

    settings = TtsSynthesisSettings("openai", "tts-1", "alloy", 1.0)
    audio_basis = build_narration_audio_basis(preparation, settings)
    versions.add_version(
        "audio",
        "E1S01",
        canonical_narration_text(preparation),
        source_file=audio,
        execution_script_file="episode_1.json",
        artifact_episode=1,
        artifact_audio_basis=ArtifactBasisDescriptor.from_basis(audio_basis).to_dict(),
        tts_basis_digest=audio_basis.digest,
        tts_actual_duration_seconds=4.5,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_audio(1, "E1S01"),
        artifact_path="audio/segment_E1S01.wav",
        basis=audio_basis,
    )
    return ProjectManager(projects_root), project_path, settings


async def test_current_tts_presentation_materializes_manifest_and_actual_media_boundaries(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)

    # Version filenames contain timestamps, so probe by media suffix instead of a fixed basename.
    async def probe(path: Path) -> float | None:
        return 4.5 if path.suffix == ".wav" else 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )

    result = await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )

    assert result.presentation.video.duration_microseconds == 6_250_000
    assert result.presentation.narration_audio is not None
    assert result.presentation.narration_audio.duration_microseconds == 4_500_000
    assert [cue.end_microseconds for cue in result.presentation.subtitles] == [4_500_000]
    assert result.presentation.selection == "current"
    assert result.presentation.currency == "current"
    assert (project_path / result.subtitle_artifact_path).is_file()
    assert (project_path / result.presentation_artifact_path).is_file()
    adapter = ProjectArtifactManifestAdapter(project_path)
    assert adapter.get_entry(ArtifactKey.episode_subtitle(1, "E1S01", "use_tts")) is not None
    assert adapter.get_entry(ArtifactKey.episode_presentation(1, "E1S01", "use_tts")) is not None


async def test_video_and_audio_use_their_semantic_duration_probes(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    video_probe = AsyncMock(return_value=6.25)
    audio_probe = AsyncMock(return_value=4.5)
    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        video_duration_probe=video_probe,
        audio_duration_probe=audio_probe,
    )

    result = await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )

    assert result.presentation.video.duration_microseconds == 6_250_000
    assert result.presentation.narration_audio is not None
    assert result.presentation.narration_audio.duration_microseconds == 4_500_000
    video_probe.assert_awaited_once()
    audio_probe.assert_awaited_once()

    manual_versions_path = project_path / "versions" / "versions.json"
    manual_versions = json.loads(manual_versions_path.read_text(encoding="utf-8"))
    manual_record = manual_versions["videos"]["E1S01"]["versions"][0]
    manual_versions["videos"]["E1S01"]["versions"][0] = {
        key: value
        for key, value in manual_record.items()
        if key in {"version", "file", "prompt", "created_at", "_previous_current_version"}
    } | {"source": "manual_upload"}
    _write_json(manual_versions_path, manual_versions)
    ProjectArtifactManifestAdapter(project_path).delete_entry(ArtifactKey.episode_video(1, "E1S01"))
    video_probe.reset_mock()
    audio_probe.reset_mock()

    await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
    )

    video_probe.assert_awaited_once()
    audio_probe.assert_not_awaited()


async def test_current_media_content_identity_is_reused_after_manifest_verification(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    digest_calls: Counter[Path] = Counter()

    def digest(path: Path) -> str:
        digest_calls[path] += 1
        return media_content_digest(path)

    async def probe(_path: Path) -> float | None:
        return 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
        content_digest=digest,
    )
    await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
    )

    record = VersionManager(project_path).get_versions("videos", "E1S01")["versions"][0]
    selected_path = project_path / record["file"]
    canonical_path = project_path / "videos" / "scene_E1S01.mp4"
    assert digest_calls == Counter({selected_path: 1, canonical_path: 1})


async def test_stale_current_tts_remains_materializable_without_touching_paid_media(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)

    async def probe(path: Path) -> float | None:
        return 4.5 if path.suffix == ".wav" else 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][0]["novel_text"] = "新的旁白"
    _write_json(script_path, script)
    video_before = (project_path / "videos" / "scene_E1S01.mp4").read_bytes()
    audio_before = (project_path / "audio" / "segment_E1S01.wav").read_bytes()

    result = await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )

    assert result.presentation.selection == "current"
    assert result.presentation.currency == "stale"
    assert (project_path / "videos" / "scene_E1S01.mp4").read_bytes() == video_before
    assert (project_path / "audio" / "segment_E1S01.wav").read_bytes() == audio_before
    bundle = await PresentationBundleService(pm, presentation_reader=service).export_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )
    with zipfile.ZipFile(bundle) as archive:
        assert json.loads(archive.read("presentation.json"))["currency"] == "stale"


async def test_history_read_does_not_replace_current_presentation_or_manifest(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)

    async def probe(path: Path) -> float | None:
        return 4.5 if path.suffix == ".wav" else 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )
    current = await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
    )
    presentation_path = project_path / current.presentation_artifact_path
    before_bytes = presentation_path.read_bytes()
    before_entry = ProjectArtifactManifestAdapter(project_path).get_entry(
        ArtifactKey.episode_presentation(1, "E1S01", "post_production")
    )

    versions = VersionManager(project_path)
    record = versions.get_versions("videos", "E1S01")["versions"][0]
    source = project_path / record["file"]
    versions.add_version(
        "videos",
        "E1S01",
        "second",
        source_file=source,
        **{
            key: value
            for key, value in record.items()
            if key
            not in {"version", "file", "filename", "prompt", "created_at", "is_current", "file_url", "restored_from"}
        },
    )
    history = await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
        video_version=1,
    )

    assert history.presentation.selection == "history"
    assert history.presentation.video.media.version == 1
    assert presentation_path.read_bytes() == before_bytes
    assert (
        ProjectArtifactManifestAdapter(project_path).get_entry(
            ArtifactKey.episode_presentation(1, "E1S01", "post_production")
        )
        == before_entry
    )
    bundle = await PresentationBundleService(pm, presentation_reader=service).export_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
        video_version=1,
    )
    with zipfile.ZipFile(bundle) as archive:
        bundled_model = json.loads(archive.read("presentation.json"))
        assert bundled_model["selection"] == "history"
        assert bundled_model["video"]["version"] == 1


async def test_episode_materialization_skips_units_with_only_paid_history(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    history_only_item = {
        **script["segments"][0],
        "segment_id": "E1S02",
        "generated_assets": {"video_clip": "videos/scene_E1S02.mp4"},
    }
    script["segments"].append(history_only_item)
    _write_json(script_path, script)

    staged = project_path / "late-paid-result.mp4"
    staged.write_bytes(b"paid-history-only")
    versions = VersionManager(project_path)
    outcome = versions.commit_staged_paid_version(
        "videos",
        "E1S02",
        "late result",
        staged_file=staged,
        current_file=project_path / "videos" / "scene_E1S02.mp4",
        select_current=False,
    )
    assert outcome.selected is False
    assert versions.get_versions("videos", "E1S02")["current_version"] == 0

    async def probe(_path: Path) -> float | None:
        return 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )

    results = await service.materialize_episode(
        project_name="demo",
        episode=1,
        variant="post_production",
    )

    assert [result.presentation.unit_id for result in results] == ["E1S01"]


async def test_episode_tts_materialization_keeps_video_without_selected_narration(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    second_item = {
        **script["segments"][0],
        "segment_id": "E1S02",
        "generated_assets": {
            "storyboard_image": "storyboards/scene_E1S02.png",
            "video_clip": "videos/scene_E1S02.mp4",
            "narration_audio": "audio/segment_E1S02.wav",
        },
    }
    script["segments"].append(second_item)
    _write_json(script_path, script)
    storyboard = project_path / "storyboards" / "scene_E1S02.png"
    storyboard.write_bytes(b"storyboard-2")
    video = project_path / "videos" / "scene_E1S02.mp4"
    video.write_bytes(b"provider-video-v2")

    preparation = admit_script_unit("segments", second_item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S02",
        visual_prompt=second_item["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(8)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8, 12),
        reference_image_limit=None,
        parent_version=0,
    )
    VersionManager(project_path).add_version(
        "videos",
        "E1S02",
        "video",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_script_file="episode_1.json",
        execution_duration_seconds=8,
        execution_request_digest="e" * 64,
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=currency.to_dict(),
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_video(1, "E1S02"),
        artifact_path="videos/scene_E1S02.mp4",
        basis=currency.video_basis,
    )

    async def probe(path: Path) -> float | None:
        return 4.5 if path.suffix == ".wav" else 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )

    results = await service.materialize_episode(
        project_name="demo",
        episode=1,
        variant="use_tts",
    )

    assert [(result.presentation.unit_id, result.presentation.variant) for result in results] == [
        ("E1S01", "use_tts"),
        ("E1S02", "post_production"),
    ]


async def test_editable_bundle_contains_exact_selected_media_model_and_subtitles(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)

    async def probe(path: Path) -> float | None:
        return 4.5 if path.suffix == ".wav" else 6.25

    read_model = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )
    service = PresentationBundleService(pm, presentation_reader=read_model)
    video_before = (project_path / "videos" / "scene_E1S01.mp4").read_bytes()
    audio_before = (project_path / "audio" / "segment_E1S01.wav").read_bytes()

    bundle = await service.export_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )

    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "media/video.mp4",
            "media/narration.wav",
            "presentation.json",
            "subtitles.json",
            "subtitles.vtt",
        }
        model = json.loads(archive.read("presentation.json"))
        assert model["selection"] == "current"
        assert model["currency"] == "current"
        assert model["video"]["duration_microseconds"] == 6_250_000
        assert model["narration_audio"]["duration_microseconds"] == 4_500_000
        assert archive.read("media/video.mp4") == video_before
        assert archive.read("media/narration.wav") == audio_before
        assert "00:00:00.000 --> 00:00:04.500" in archive.read("subtitles.vtt").decode("utf-8")
    assert (project_path / "videos" / "scene_E1S01.mp4").read_bytes() == video_before
    assert (project_path / "audio" / "segment_E1S01.wav").read_bytes() == audio_before


async def test_overlong_selected_tts_is_unavailable_instead_of_clipped(tmp_path: Path) -> None:
    pm, _project_path, settings = _setup_narrator_project(tmp_path)

    async def probe(path: Path) -> float | None:
        return 7.0 if path.suffix == ".wav" else 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )

    with pytest.raises(PresentationUnavailableError, match="cannot form"):
        await service.materialize_unit(
            project_name="demo",
            resource_type="videos",
            resource_id="E1S01",
            variant="use_tts",
        )


async def test_manual_upload_uses_explicit_unverified_raw_presentation_everywhere(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    versions_path = project_path / "versions" / "versions.json"
    versions_value = json.loads(versions_path.read_text(encoding="utf-8"))
    record = versions_value["videos"]["E1S01"]["versions"][0]
    versions_value["videos"]["E1S01"]["versions"][0] = {
        key: value
        for key, value in record.items()
        if key in {"version", "file", "prompt", "created_at", "_previous_current_version"}
    } | {"source": "manual_upload"}
    _write_json(versions_path, versions_value)
    adapter = ProjectArtifactManifestAdapter(project_path)
    adapter.delete_entry(ArtifactKey.episode_video(1, "E1S01"))

    async def probe(_path: Path) -> float | None:
        return 6.25

    read_model = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )
    result = await read_model.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )

    assert result.persisted is False
    assert result.presentation.provenance == "unavailable"
    assert result.presentation.variant == "post_production"
    assert result.presentation.currency is None
    assert result.presentation.narration_audio is None
    assert result.presentation.subtitles == ()
    assert result.to_dict()["presentation_basis"] is None
    assert adapter.get_entry(ArtifactKey.episode_presentation(1, "E1S01", "post_production")) is None

    bundle = await PresentationBundleService(pm, presentation_reader=read_model).export_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="use_tts",
    )
    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {"media/video.mp4", "presentation.json"}
        assert archive.read("media/video.mp4") == b"provider-video-v1"
        assert json.loads(archive.read("presentation.json"))["provenance"] == "unavailable"


async def test_generated_video_without_typed_provenance_fails_closed(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    versions_path = project_path / "versions" / "versions.json"
    versions_value = json.loads(versions_path.read_text(encoding="utf-8"))
    record = versions_value["videos"]["E1S01"]["versions"][0]
    versions_value["videos"]["E1S01"]["versions"][0] = {
        key: value
        for key, value in record.items()
        if key in {"version", "file", "prompt", "created_at", "_previous_current_version"}
    }
    _write_json(versions_path, versions_value)

    read_model = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
    )

    with pytest.raises(PresentationUnavailableError, match="typed presentation provenance"):
        await read_model.materialize_unit(
            project_name="demo",
            resource_type="videos",
            resource_id="E1S01",
            variant="post_production",
        )


async def test_current_presentation_reselects_when_version_changes_during_media_probe(tmp_path: Path) -> None:
    pm, project_path, settings = _setup_narrator_project(tmp_path)
    versions = VersionManager(project_path)
    switched = False

    async def probe(path: Path) -> float | None:
        nonlocal switched
        if path.suffix == ".mp4" and not switched:
            switched = True
            current = versions.get_versions("videos", "E1S01")["versions"][0]
            versions.add_version(
                "videos",
                "E1S01",
                "replacement",
                source_file=project_path / current["file"],
                **{
                    key: value
                    for key, value in current.items()
                    if key
                    not in {
                        "version",
                        "file",
                        "filename",
                        "prompt",
                        "created_at",
                        "is_current",
                        "file_url",
                        "restored_from",
                        "_previous_current_version",
                    }
                },
            )
        return 6.25

    service = PresentationReadModelService(
        pm,
        settings_resolver_factory=lambda _project_name, _project_path: _SettingsResolver(settings),
        duration_probe=probe,
    )
    result = await service.materialize_unit(
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
    )

    assert result.presentation.video.media.version == 2
    assert result.presentation_artifact_path is not None
    persisted = json.loads((project_path / result.presentation_artifact_path).read_text(encoding="utf-8"))
    assert persisted["video"]["version"] == 2
