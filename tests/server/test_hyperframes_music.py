"""Project-bound HyperFrames background music generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from server.services.hyperframes_music import (
    BACKGROUND_MUSIC_VOLUME,
    HyperframesMusicService,
    HyperframesMusicUnavailable,
)

pytestmark = pytest.mark.unit


class _Backend:
    def __init__(self) -> None:
        self.requests = []

    async def synthesize(self, request):
        self.requests.append(request)
        request.output_path.write_bytes(b"generated music")


def _workspace(tmp_path: Path) -> tuple[ProjectManager, Path]:
    projects = tmp_path / "projects"
    project = projects / "demo"
    workspace = project / "hyperframes" / "episode_01"
    (workspace / "media").mkdir(parents=True)
    (project / "project.json").write_text("{}", encoding="utf-8")
    (workspace / "index.html").write_text("<html></html>", encoding="utf-8")
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "units": [
                    {"video": {"duration_microseconds": 4_500_000}},
                    {"video": {"duration_microseconds": 5_500_000}},
                ]
            }
        ),
        encoding="utf-8",
    )
    return ProjectManager(projects), workspace


async def test_music_generation_is_instrumental_continuous_and_project_local(tmp_path: Path) -> None:
    pm, workspace = _workspace(tmp_path)
    backend = _Backend()

    async def backend_factory():
        return backend

    async def duration_probe(_path: Path) -> float:
        return 10.0

    music = await HyperframesMusicService(
        pm,
        backend_factory=backend_factory,
        duration_probe=duration_probe,
    ).generate(
        "demo",
        1,
        direction="Warm cinematic folk, 88 BPM, bamboo flute and soft strings",
    )

    assert music.path.is_relative_to(workspace)
    assert music.path.read_bytes() == b"generated music"
    assert music.duration_seconds == 10.0
    assert music.volume == BACKGROUND_MUSIC_VOLUME == 0.15
    assert 'data-volume="0.150"' in music.html_snippet
    assert 'data-duration="10.000000"' in music.html_snippet
    assert 'data-audio-group="music"' in music.html_snippet
    request = backend.requests[0]
    assert request.max_duration == 10.0
    assert request.lyrics == ""
    assert request.output_format == "mp3"
    assert request.client_job_id.startswith("arcreel:hyperframes:bgm:")
    assert "Strictly instrumental" in request.text
    assert "No vocals" in request.text
    metadata = json.loads((workspace / music.metadata_path).read_text(encoding="utf-8"))
    assert metadata["instrumental"] is True
    assert metadata["volume"] == 0.15

    same = await HyperframesMusicService(
        pm,
        backend_factory=backend_factory,
        duration_probe=duration_probe,
    ).generate(
        "demo",
        1,
        direction="Warm cinematic folk, 88 BPM, bamboo flute and soft strings",
    )
    assert same.path == music.path
    assert len(backend.requests) == 1


async def test_music_generation_rejects_a_track_shorter_than_the_episode(tmp_path: Path) -> None:
    pm, _workspace_path = _workspace(tmp_path)
    backend = _Backend()

    async def backend_factory():
        return backend

    async def duration_probe(_path: Path) -> float:
        return 3.0

    with pytest.raises(HyperframesMusicUnavailable, match="shorter than the episode"):
        await HyperframesMusicService(
            pm,
            backend_factory=backend_factory,
            duration_probe=duration_probe,
        ).generate("demo", 1, direction="Minimal ambient score")
