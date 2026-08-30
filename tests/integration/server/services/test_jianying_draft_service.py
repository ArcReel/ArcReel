"""Jianying serialization contracts for the shared presentation read model."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.audio_utils import probe_existing_video_duration_seconds
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.project_manager import ProjectManager
from lib.speech_artifact_provenance import SelectedMediaEvidence
from lib.speech_composition import SpeechFieldLocation, SpeechMode, SpeechOwner, SpeechPreparation, SpeechUtterance
from lib.speech_presentation import (
    PresentationMedia,
    RawPresentationMedia,
    materialize_raw_video_presentation,
    materialize_speech_presentation,
)
from server.services.jianying_draft_service import JianyingDraftService, NoCompletedSegmentsError
from server.services.presentation_read_model import MaterializedEpisode, MaterializedPresentation
from tests.factories import make_test_video, make_test_video_with_audio_tail


def make_test_audio(path: Path, *, duration_sec: float = 1.0) -> None:
    """使用 ffmpeg 生成极短测试音频（正弦波 wav，pcm_s16le 为 ffmpeg 内置编码器）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_sec}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def _basis(kind: str, identity: str) -> ArtifactBasisDescriptor:
    return ArtifactBasisDescriptor.from_basis(ArtifactBasis.build(kind, kind_version=1, inputs={"id": identity}))


def _media(path: Path, project_path: Path, *, kind: str, duration: float, version: int = 1) -> PresentationMedia:
    return PresentationMedia(
        artifact_path=path.relative_to(project_path).as_posix(),
        version=version,
        selection="current",
        currency="current",
        evidence=SelectedMediaEvidence.from_file(
            basis=_basis(kind, str(version)),
            path=path,
            actual_duration_seconds=duration,
        ),
    )


def _speech(unit_id: str, mode: SpeechMode, *texts: str) -> SpeechPreparation:
    owner = SpeechOwner.CHARACTER if mode is SpeechMode.CHARACTER_SPEECH else SpeechOwner.NARRATOR
    return SpeechPreparation(
        unit_id=unit_id,
        mode=mode,
        utterances=tuple(
            SpeechUtterance(
                owner=owner,
                speaker="阿离" if owner is SpeechOwner.CHARACTER else None,
                text=value,
                location=SpeechFieldLocation(("utterances", index, "text")),
            )
            for index, value in enumerate(texts)
        ),
    )


def _result(
    project_path: Path,
    *,
    unit_id: str,
    video_path: Path,
    duration: float,
    variant: str = POST_PRODUCTION,
    audio_path: Path | None = None,
    audio_duration: float | None = None,
    provider_audio_enabled: bool = True,
    transition: str = "cut",
) -> MaterializedPresentation:
    mode = SpeechMode.NARRATOR_VOICEOVER
    audio = None
    if audio_path is not None and audio_duration is not None:
        audio = _media(audio_path, project_path, kind="audio", duration=audio_duration)
    presentation = materialize_speech_presentation(
        _speech(unit_id, mode, "甲", "乙乙"),
        variant=variant,
        video=_media(video_path, project_path, kind="video", duration=duration),
        narration_audio=audio,
        provider_audio_enabled=provider_audio_enabled,
    )
    return MaterializedPresentation(
        episode=1,
        resource_type="videos",
        script_file="episode_1.json",
        transition_to_next=transition,
        presentation=presentation,
        subtitle_artifact_path=None,
        presentation_artifact_path=None,
    )


class _Reader:
    def __init__(self, project_manager: ProjectManager, values: tuple[MaterializedPresentation, ...]) -> None:
        self.project_manager = project_manager
        self.values = values
        self.calls: list[tuple[str, int, str]] = []

    async def materialize_episode(self, *, project_name: str, episode: int, variant: str):
        self.calls.append((project_name, episode, variant))
        return MaterializedEpisode(
            project_snapshot=self.project_manager.load_project(project_name),
            presentations=self.values,
        )


def _project(tmp_path: Path, title: str = "测试项目", aspect_ratio: object = "9:16") -> tuple[ProjectManager, Path]:
    root = tmp_path / "projects"
    path = root / "demo"
    path.mkdir(parents=True)
    (path / "project.json").write_text(
        json.dumps(
            {
                "title": title,
                "content_mode": "narration",
                "generation_mode": "storyboard",
                "aspect_ratio": aspect_ratio,
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ProjectManager(root), path


def _read_draft_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        info_name = next(name for name in archive.namelist() if name.endswith("draft_info.json"))
        return json.loads(archive.read(info_name))


async def test_export_serializes_only_shared_track_gains_actual_boundaries_and_cues(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    audio = project_path / "versions" / "audio" / "E1S01_v1.wav"
    video.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    make_test_video(video, duration_sec=1.0)
    make_test_audio(audio, duration_sec=0.9)
    value = _result(
        project_path,
        unit_id="E1S01",
        video_path=video,
        duration=1.0,
        variant=USE_TTS,
        audio_path=audio,
        audio_duration=0.9,
        provider_audio_enabled=False,
    )
    archive = await JianyingDraftService(pm, presentation_reader=_Reader(pm, (value,))).export_episode_draft(
        "demo", 1, "/mock/JianyingDrafts", variant=USE_TTS
    )

    content = _read_draft_archive(archive)
    video_track = next(track for track in content["tracks"] if track.get("type") == "video")
    audio_track = next(track for track in content["tracks"] if track.get("type") == "audio")
    text_track = next(track for track in content["tracks"] if track.get("type") == "text")
    assert video_track["segments"][0]["target_timerange"] == {"start": 0, "duration": 1_000_000}
    assert video_track["segments"][0]["volume"] == pytest.approx(0.0)
    assert audio_track["segments"][0]["target_timerange"] == {"start": 0, "duration": 900_000}
    assert audio_track["segments"][0]["volume"] == pytest.approx(1.0)
    assert [segment["target_timerange"] for segment in text_track["segments"]] == [
        {"start": 0, "duration": 300_000},
        {"start": 300_000, "duration": 600_000},
    ]


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)
async def test_export_accepts_video_track_boundary_when_container_has_a_longer_audio_tail(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "tail.mp4"
    make_test_video_with_audio_tail(video)
    duration = await probe_existing_video_duration_seconds(video)
    assert duration is not None
    value = _result(project_path, unit_id="E1S01", video_path=video, duration=duration)

    archive = await JianyingDraftService(pm, presentation_reader=_Reader(pm, (value,))).export_episode_draft(
        "demo", 1, "/mock/JianyingDrafts"
    )

    content = _read_draft_archive(archive)
    video_track = next(track for track in content["tracks"] if track.get("type") == "video")
    assert video_track["segments"][0]["source_timerange"] == {"start": 0, "duration": 1_000_000}


async def test_export_uses_shared_transition_and_unity_provider_track(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    first = project_path / "versions" / "videos" / "first.mp4"
    second = project_path / "versions" / "videos" / "second.mp4"
    first.parent.mkdir(parents=True)
    make_test_video(first, duration_sec=1.0)
    make_test_video(second, duration_sec=1.0)
    one = _result(project_path, unit_id="one", video_path=first, duration=1.0, transition="fade")
    two = _result(project_path, unit_id="two", video_path=second, duration=1.0, transition="fade")
    archive = await JianyingDraftService(pm, presentation_reader=_Reader(pm, (one, two))).export_episode_draft(
        "demo", 1, "/mock/JianyingDrafts"
    )

    content = _read_draft_archive(archive)
    track = next(candidate for candidate in content["tracks"] if candidate.get("type") == "video")
    assert [segment["volume"] for segment in track["segments"]] == pytest.approx([1.0, 1.0])
    transitions = content.get("materials", {}).get("transitions", [])
    assert [transition["effect_id"] for transition in transitions] == ["321493"]


async def test_export_uses_reader_variant_and_packages_its_selected_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v3.mp4"
    audio = project_path / "versions" / "audio" / "E1S01_v2.wav"
    video.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    make_test_video(video, duration_sec=1.0)
    make_test_audio(audio, duration_sec=0.75)
    value = _result(
        project_path,
        unit_id="E1S01",
        video_path=video,
        duration=1.0,
        variant=USE_TTS,
        audio_path=audio,
        audio_duration=0.75,
    )
    reader = _Reader(pm, (value,))
    service = JianyingDraftService(pm, presentation_reader=reader)
    original_iterdir = Path.iterdir

    def mutation_sensitive_iterdir(path: Path):
        entries = list(original_iterdir(path))
        if path.name != "staging":
            return iter(entries)

        def iterate_staging():
            if not entries:
                return
            yield entries[0]
            if not entries[0].exists():
                return
            yield from entries[1:]

        return iterate_staging()

    monkeypatch.setattr(Path, "iterdir", mutation_sensitive_iterdir)

    zip_path = await service.export_episode_draft(
        "demo",
        1,
        "/mock/JianyingDrafts",
        variant=USE_TTS,
    )

    assert reader.calls == [("demo", 1, USE_TTS)]
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert any(name.endswith("/assets/E1S01_v3.mp4") for name in names)
        assert any(name.endswith("/assets/E1S01_v2.wav") for name in names)
        info_name = next(name for name in names if name.endswith("draft_info.json"))
        raw = archive.read(info_name).decode("utf-8")
        assert "/mock/JianyingDrafts" in raw
        assert str(project_path) not in raw


async def test_export_empty_shared_model_raises_completed_segments_error(tmp_path: Path) -> None:
    pm, _ = _project(tmp_path)
    service = JianyingDraftService(pm, presentation_reader=_Reader(pm, ()))

    with pytest.raises(NoCompletedSegmentsError):
        await service.export_episode_draft("demo", 1, "/mock/JianyingDrafts")


async def test_export_revalidates_shared_media_path_inside_project(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    selected = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    selected.parent.mkdir(parents=True)
    make_test_video(selected, duration_sec=1.0)
    value = _result(project_path, unit_id="E1S01", video_path=selected, duration=1.0)
    outside = tmp_path / "outside.mp4"
    make_test_video(outside, duration_sec=1.0)
    selected.unlink()
    selected.symlink_to(outside)
    service = JianyingDraftService(pm, presentation_reader=_Reader(pm, (value,)))

    with pytest.raises(ValueError, match="outside the project"):
        await service.export_episode_draft("demo", 1, "/mock/JianyingDrafts")


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ({"aspect_ratio": "9:16"}, (1080, 1920)),
        ({"aspect_ratio": {"video": "9:16"}}, (1080, 1920)),
        ({"aspect_ratio": "16:9"}, (1920, 1080)),
    ],
)
async def test_export_canvas_uses_project_aspect_ratio(
    tmp_path: Path,
    project: dict,
    expected: tuple[int, int],
) -> None:
    pm, project_path = _project(tmp_path, aspect_ratio=project["aspect_ratio"])
    video = project_path / "versions" / "videos" / "unit.mp4"
    video.parent.mkdir(parents=True)
    make_test_video(video, duration_sec=1.0)
    value = _result(project_path, unit_id="unit", video_path=video, duration=1.0)

    archive = await JianyingDraftService(pm, presentation_reader=_Reader(pm, (value,))).export_episode_draft(
        "demo", 1, "/mock/JianyingDrafts"
    )

    content = _read_draft_archive(archive)
    assert (content["canvas_config"]["width"], content["canvas_config"]["height"]) == expected


async def test_export_canvas_uses_project_snapshot_selected_during_materialization(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, aspect_ratio="16:9")
    video = project_path / "versions" / "videos" / "unit.mp4"
    video.parent.mkdir(parents=True)
    make_test_video(video, duration_sec=1.0)
    value = _result(project_path, unit_id="unit", video_path=video, duration=1.0)

    class _ProjectEditingReader(_Reader):
        async def materialize_episode(self, *, project_name: str, episode: int, variant: str):
            project = pm.load_project(project_name)
            project["aspect_ratio"] = "9:16"
            pm.save_project(project_name, project)
            return await super().materialize_episode(project_name=project_name, episode=episode, variant=variant)

    archive = await JianyingDraftService(
        pm, presentation_reader=_ProjectEditingReader(pm, (value,))
    ).export_episode_draft("demo", 1, "/mock/JianyingDrafts")

    content = _read_draft_archive(archive)
    assert (content["canvas_config"]["width"], content["canvas_config"]["height"]) == (1080, 1920)


async def test_export_keeps_unverified_manual_upload_raw_without_speech_tracks(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "manual.mp4"
    video.parent.mkdir(parents=True)
    make_test_video(video, duration_sec=1.0)
    presentation = materialize_raw_video_presentation(
        unit_id="manual",
        video=RawPresentationMedia(
            artifact_path=video.relative_to(project_path).as_posix(),
            version=4,
            selection="current",
            content_digest=f"sha256-v1:{'a' * 64}",
            actual_duration_seconds=1.0,
        ),
    )
    value = MaterializedPresentation(
        episode=1,
        resource_type="videos",
        script_file="episode_1.json",
        transition_to_next="cut",
        presentation=presentation,
        subtitle_artifact_path=None,
        presentation_artifact_path=None,
    )

    archive = await JianyingDraftService(pm, presentation_reader=_Reader(pm, (value,))).export_episode_draft(
        "demo", 1, "/mock/JianyingDrafts", variant=USE_TTS
    )

    content = _read_draft_archive(archive)
    assert [track["type"] for track in content["tracks"]] == ["video"]
    with zipfile.ZipFile(archive) as package:
        assert any(name.endswith("/assets/manual.mp4") for name in package.namelist())
