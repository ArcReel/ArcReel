"""Shared real temporary projects/current-media registration for timeline service tests."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineService
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from tests.factories import make_test_video, wav_bytes


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    """Reference-video project: episode 1 has dialogue E1U1, voiceover E1U2, silent E1U3, each arranged for 4 seconds."""
    manager = ProjectManager(str(tmp_path / "projects"))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    manager.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))
    units: list[dict[str, Any]] = [
        {"unit_id": "E1U1", "text": "@[角色A]{你好}", "duration_seconds": 4},
        {"unit_id": "E1U2", "text": "{风起了}", "duration_seconds": 4},
        {"unit_id": "E1U3", "text": "推门进屋", "duration_seconds": 4},
    ]
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "summary": "摘要",
            "novel": {"title": "小说", "chapter": "第一章"},
            "video_units": units,
        },
        "episode_1.json",
    )
    return manager


@pytest.fixture
def service(pm: ProjectManager) -> EditTimelineService:
    return EditTimelineService(pm)


def _install_media(pm: ProjectManager, resource_type: str, unit_id: str, source: Path) -> None:
    """Register a new current version and place it at the formal path."""
    project_path = pm.get_project_path("demo")
    VersionManager(project_path).add_version(resource_type, unit_id, "prompt", source_file=source)
    target = project_path / resource_relative_path(resource_type, unit_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


@pytest.fixture
def install_video(pm: ProjectManager, tmp_path: Path) -> Callable[[str, float], None]:
    """Register a new current unit-video version with specified duration."""
    counter = iter(range(1, 1_000))

    def install(unit_id: str, seconds: float) -> None:
        source = tmp_path / "media" / f"{unit_id}-{next(counter)}.mp4"
        make_test_video(source, duration_sec=seconds)
        _install_media(pm, "reference_videos", unit_id, source)

    return install


@pytest.fixture
def three_clips(install_video: Callable[[str, float], None]) -> None:
    """Register E1U1/E1U2/E1U3 videos lasting 1/1.5/0.5 seconds; script-derived timeline totals 3 seconds."""
    install_video("E1U1", 1.0)
    install_video("E1U2", 1.5)
    install_video("E1U3", 0.5)


@pytest.fixture
def install_narration(pm: ProjectManager, tmp_path: Path) -> Callable[[str, float], None]:
    """Register current narration audio with specified duration for a video unit."""

    def install(unit_id: str, seconds: float) -> None:
        source = tmp_path / "media" / f"{unit_id}.wav"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(wav_bytes(seconds))
        _install_media(pm, "audio", unit_id, source)

    return install
