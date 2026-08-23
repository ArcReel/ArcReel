"""Project-local HyperFrames workspace contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.narration_delivery import USE_TTS
from lib.project_manager import ProjectManager
from lib.speech_artifact_provenance import SelectedMediaEvidence
from lib.speech_composition import (
    SpeechFieldLocation,
    SpeechMode,
    SpeechOwner,
    SpeechPreparation,
    SpeechUtterance,
)
from lib.speech_presentation import PresentationMedia, materialize_speech_presentation
from server.services.hyperframes_workspace import (
    HyperframesStudioManager,
    HyperframesStudioUnavailable,
    HyperframesWorkspaceService,
    HyperframesWorkspaceUnavailable,
)
from server.services.presentation_read_model import MaterializedEpisode, MaterializedPresentation

pytestmark = pytest.mark.unit


def _basis(kind: str) -> ArtifactBasisDescriptor:
    return ArtifactBasisDescriptor.from_basis(ArtifactBasis.build(kind, kind_version=1, inputs={"fixture": kind}))


def _media(path: Path, project_path: Path, *, kind: str, duration: float) -> PresentationMedia:
    return PresentationMedia(
        artifact_path=path.relative_to(project_path).as_posix(),
        version=1,
        selection="current",
        currency="current",
        evidence=SelectedMediaEvidence.from_file(
            basis=_basis(kind),
            path=path,
            actual_duration_seconds=duration,
        ),
    )


def _materialized(project_path: Path, *, unit_id: str = "E1U01") -> MaterializedEpisode:
    video = project_path / "versions" / "videos" / "current.mp4"
    audio = project_path / "versions" / "audio" / "current.wav"
    video.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    video.write_bytes(b"video fixture")
    audio.write_bytes(b"audio fixture")
    speech = SpeechPreparation(
        unit_id=unit_id,
        mode=SpeechMode.NARRATOR_VOICEOVER,
        utterances=(
            SpeechUtterance(
                owner=SpeechOwner.NARRATOR,
                speaker=None,
                text="田园时光",
                location=SpeechFieldLocation(("narration",)),
            ),
        ),
    )
    presentation = materialize_speech_presentation(
        speech,
        variant=USE_TTS,
        video=_media(video, project_path, kind="video", duration=2.0),
        narration_audio=_media(audio, project_path, kind="audio", duration=1.5),
        provider_audio_enabled=False,
    )
    value = MaterializedPresentation(
        episode=1,
        resource_type="reference_videos",
        script_file="scripts/episode_1.json",
        transition_to_next="cut",
        presentation=presentation,
        subtitle_artifact_path=None,
        presentation_artifact_path=None,
    )
    return MaterializedEpisode(
        project_snapshot={"title": "景泰蓝", "aspect_ratio": "9:16"},
        presentations=(value,),
    )


class _Reader:
    def __init__(self, value: MaterializedEpisode) -> None:
        self.value = value

    async def materialize_episode(self, **_kwargs) -> MaterializedEpisode:
        return self.value


def _project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    projects = tmp_path / "projects"
    project_path = projects / "demo"
    project_path.mkdir(parents=True)
    (project_path / "project.json").write_text(
        json.dumps({"title": "景泰蓝", "content_mode": "narration"}),
        encoding="utf-8",
    )
    return ProjectManager(projects), project_path


async def test_prepare_writes_complete_studio_project_only_inside_arcreel_project(
    tmp_path: Path,
) -> None:
    pm, project_path = _project(tmp_path)
    materialized = _materialized(project_path, unit_id="../../must-not-be-a-path")
    service = HyperframesWorkspaceService(pm, presentation_reader=_Reader(materialized))

    workspace = await service.prepare("demo", 1, variant=USE_TTS)

    assert workspace.path == project_path / "hyperframes" / "episode_01"
    assert workspace.path.is_relative_to(project_path)
    assert sorted(path.relative_to(workspace.path).as_posix() for path in workspace.path.rglob("*")) == [
        "DESIGN.md",
        "EDITING_PLAN.md",
        "index.html",
        "manifest.json",
        "media",
        "media/000-narration.wav",
        "media/000-video.mp4",
        "renders",
    ]
    assert not (tmp_path / "must-not-be-a-path").exists()
    (workspace.path / "media" / "000-video.mp4").write_bytes(b"studio-side edit")
    assert (project_path / "versions" / "videos" / "current.mp4").read_bytes() == b"video fixture"

    composition = (workspace.path / "index.html").read_text(encoding="utf-8")
    assert 'data-width="1080" data-height="1920"' in composition
    assert "data-no-timeline" in composition
    assert "muted playsinline" in composition
    assert 'data-track-index="2"' in composition
    assert 'data-audio-group="voiceover"' in composition
    assert "田园时光" in composition
    manifest = json.loads((workspace.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["units"][0]["unit_id"] == "../../must-not-be-a-path"
    assert manifest["units"][0]["video"]["staged"] == "media/000-video.mp4"
    assert manifest["script_file"] == "scripts/episode_1.json"
    assert manifest["editing_plan_file"] == "EDITING_PLAN.md"
    assert manifest["total_duration_microseconds"] == 2_000_000


async def test_prepare_preserves_an_existing_editable_workspace(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    materialized = _materialized(project_path)
    service = HyperframesWorkspaceService(pm, presentation_reader=_Reader(materialized))
    first = await service.prepare("demo", 1, variant=USE_TTS)
    (first.path / "index.html").write_text("user edit", encoding="utf-8")

    second = await service.prepare("demo", 1, variant=USE_TTS)

    assert second == first
    assert (second.path / "index.html").read_text(encoding="utf-8") == "user edit"


async def test_concurrent_prepare_calls_converge_on_one_workspace(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    materialized = _materialized(project_path)
    service = HyperframesWorkspaceService(pm, presentation_reader=_Reader(materialized))

    first, second = await asyncio.gather(
        service.prepare("demo", 1, variant=USE_TTS),
        service.prepare("demo", 1, variant=USE_TTS),
    )

    assert first == second
    assert (first.path / "manifest.json").is_file()


async def test_prepare_refuses_to_overwrite_an_incomplete_workspace(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    materialized = _materialized(project_path)
    incomplete = project_path / "hyperframes" / "episode_01"
    incomplete.mkdir(parents=True)

    with pytest.raises(HyperframesWorkspaceUnavailable, match="incomplete"):
        await HyperframesWorkspaceService(pm, presentation_reader=_Reader(materialized)).prepare(
            "demo", 1, variant=USE_TTS
        )


def test_studio_public_url_requires_explicit_https_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCREEL_HYPERFRAMES_PUBLIC_URL_TEMPLATE", raising=False)
    assert HyperframesStudioManager.public_url(43123, "http://localhost:1241/") == "http://localhost:43123"

    with pytest.raises(HyperframesStudioUnavailable, match="HTTPS deployments"):
        HyperframesStudioManager.public_url(43123, "https://arcreel.example/")

    monkeypatch.setenv(
        "ARCREEL_HYPERFRAMES_PUBLIC_URL_TEMPLATE",
        "https://hf-{port}.arcreel.example/",
    )
    assert HyperframesStudioManager.public_url(43123, "https://arcreel.example/") == "https://hf-43123.arcreel.example"
