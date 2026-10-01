"""剪辑时间线渲染测试共用的项目：两个画外音分镜的视频、旁白配音与一条改过的剪辑时间线。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.model import ClipTrim, EditTimelineContent, TimelineRevision
from lib.edit_timeline.store import EditTimelineStore
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.speech.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis, canonical_narration_text
from lib.speech.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit
from tests.factories import make_test_video, wav_bytes

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
SETTINGS = TtsSynthesisSettings("openai", "tts-1", "alloy", 1.0)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def narration_segment(segment_id: str, text: str) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "duration_seconds": 4,
        "novel_text": text,
        "video_prompt": {"action": "Clouds move", "camera_motion": "Static"},
        "generated_assets": {
            "storyboard_image": f"storyboards/scene_{segment_id}.png",
            "video_clip": f"videos/scene_{segment_id}.mp4",
            "narration_audio": f"audio/segment_{segment_id}.wav",
        },
    }


def install_video(project_path: Path, item: dict[str, Any], seconds: float) -> None:
    segment_id = item["segment_id"]
    storyboard = project_path / "storyboards" / f"scene_{segment_id}.png"
    storyboard.parent.mkdir(parents=True, exist_ok=True)
    storyboard.write_bytes(f"storyboard-{segment_id}".encode())
    video = project_path / "videos" / f"scene_{segment_id}.mp4"
    make_test_video(video, duration_sec=seconds, fps=10)
    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id=segment_id,
        visual_prompt=item["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(4)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8),
        reference_image_limit=None,
        parent_version=0,
    )
    VersionManager(project_path).add_version(
        "videos",
        segment_id,
        "video",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_script_file="episode_1.json",
        execution_duration_seconds=4,
        execution_request_digest="d" * 64,
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=currency.to_dict(),
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_video(1, segment_id),
        artifact_path=f"videos/scene_{segment_id}.mp4",
        basis=currency.video_basis,
    )


def install_narration(project_path: Path, item: dict[str, Any], seconds: float) -> None:
    segment_id = item["segment_id"]
    audio = project_path / "audio" / f"segment_{segment_id}.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(wav_bytes(seconds))
    preparation = admit_script_unit("segments", item).preparation
    audio_basis = build_narration_audio_basis(preparation, SETTINGS)
    VersionManager(project_path).add_version(
        "audio",
        segment_id,
        canonical_narration_text(preparation),
        source_file=audio,
        execution_script_file="episode_1.json",
        artifact_episode=1,
        artifact_audio_basis=ArtifactBasisDescriptor.from_basis(audio_basis).to_dict(),
        tts_basis_digest=audio_basis.digest,
        tts_actual_duration_seconds=seconds,
        tts_provider_id=SETTINGS.provider_id,
        tts_model_id=SETTINGS.model_id,
        tts_voice=SETTINGS.voice,
        tts_speed=SETTINGS.speed,
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_audio(1, segment_id),
        artifact_path=f"audio/segment_{segment_id}.wav",
        basis=audio_basis,
    )


def setup_project(tmp_path: Path, *, narration_delivery: str = "use_tts") -> tuple[ProjectManager, Path]:
    """两个画外音分镜：S01 视频 2 秒、带 1.2 秒旁白配音，S02 视频 1.5 秒、没有旁白配音。"""
    project_path = tmp_path / "projects" / "demo"
    first, second = narration_segment("E1S01", "旁白一句"), narration_segment("E1S02", "第二段")
    write_json(
        project_path / "project.json",
        {
            "title": "Demo",
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": False,
            "aspect_ratio": "9:16",
            "default_duration": 4,
            "characters": {},
            "narration_delivery": narration_delivery,
            "audio_backend": "openai/tts-1",
            "narration_voice": "alloy",
            "narration_speed": 1.0,
            "episodes": [{"episode": 1, "title": "One", "script_file": "scripts/episode_1.json"}],
        },
    )
    write_json(
        project_path / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "narration", "segments": [first, second]},
    )
    install_video(project_path, first, 2.0)
    install_video(project_path, second, 1.5)
    install_narration(project_path, first, 1.2)
    return ProjectManager(tmp_path), project_path


def append_revision(pm: ProjectManager, timeline_id: str, content: EditTimelineContent) -> None:
    store = EditTimelineStore(pm, "demo")
    document = store.find(timeline_id)
    with store.locked_episode(document.episode):
        latest = document.latest
        revision = TimelineRevision(
            number=latest.number + 1,
            parent=latest.number,
            author=CREATOR,
            summary="调整",
            created_at=datetime.now(UTC).isoformat(),
            content=content,
        )
        store.write(document.model_copy(update={"revisions": (*document.revisions, revision)}))


async def edited_timeline(pm: ProjectManager) -> str:
    """c1 截取 0.5–1.5 秒（依据版本 1）、原声 0.5、定格 0.5 秒；c2 整段使用、原声取画外音默认值。"""
    created = await EditTimelineService(pm).create_from_script("demo", episode=1, name="完整版", author=CREATOR)
    content = EditTimelineStore(pm, "demo").find(created.timeline.id).latest.content
    first, second = content.clips
    edited = first.model_copy(
        update={
            "trim": ClipTrim(in_us=500_000, out_us=1_500_000, basis_version=1),
            "source_volume": 0.5,
            "hold_us": 500_000,
        }
    )
    append_revision(pm, created.timeline.id, EditTimelineContent(clips=(edited, second)))
    return created.timeline.id
