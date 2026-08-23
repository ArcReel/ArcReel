from __future__ import annotations

from pathlib import Path

import pytest

from lib.config.resolver import ProviderModel
from server.services import character_voice_references as service
from server.services.generation_context import GenerationContext, VideoLaneResult

pytestmark = pytest.mark.unit


class _PM:
    def __init__(self, root: Path, character: dict):
        self.root = root
        self.project = {"source_language": "zh", "characters": {"艾莉": character}}

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.root


class _Queue:
    def __init__(self, latest=None):
        self.latest = latest
        self.enqueues: list[dict] = []

    async def get_latest_task_for_resource(self, **kwargs):
        return self.latest

    async def enqueue_task(self, **kwargs):
        self.enqueues.append(kwargs)
        return {"task_id": "voice-task", "status": "queued", "deduped": False}


def _context(durations=(5, 8, 10)):
    return GenerationContext(
        generator=object(),
        video_lane=VideoLaneResult(
            provider_model=ProviderModel("ark", "seedance-2-0-pro"),
            backend_name="ark",
            backend_model="seedance-2-0-pro",
            resolution="720p",
            resolution_or_fallback="720p",
            supported_durations=durations,
            max_duration=max(durations),
            max_reference_images=0,
            voice_consistency="native",
        ),
    )


async def test_video_candidate_uses_closest_duration_and_character_voice_style(tmp_path, monkeypatch):
    pm = _PM(tmp_path, {"description": "银发少女", "voice_style": "清亮、坚定"})
    queue = _Queue()

    async def _resolve(*args, **kwargs):
        assert kwargs.get("video") is not None
        return _context((4, 8, 12))

    monkeypatch.setattr(service, "resolve_generation_context", _resolve)
    result = await service.enqueue_character_voice_reference(
        "demo",
        "艾莉",
        manager=pm,
        queue=queue,
        strategy="video",
    )

    assert result["task_id"] == "voice-task"
    task = queue.enqueues[0]
    assert task["media_type"] == "video"
    assert task["payload"]["duration_seconds"] == 8
    assert task["payload"]["strategy"] == "video"
    assert "银发少女" in task["payload"]["prompt"]
    assert "清亮、坚定" in task["payload"]["prompt"]
    assert "no music" in task["payload"]["prompt"]


@pytest.mark.parametrize(
    "character",
    [
        {"description": "x", "voice_id": "Cherry"},
        {"description": "x", "reference_audio": "characters/refs_audio/艾莉.wav"},
        {"description": "x", "global_asset_id": "asset-1", "global_asset_voice_source": "voice_id"},
    ],
)
async def test_existing_effective_voice_is_never_touched(tmp_path, character):
    queue = _Queue()
    result = await service.enqueue_character_voice_reference(
        "demo",
        "艾莉",
        manager=_PM(tmp_path, character),
        queue=queue,
    )
    assert result["reason"] == "voice_exists"
    assert queue.enqueues == []


async def test_unconfirmed_succeeded_candidate_is_reused(tmp_path):
    audio = tmp_path / "audio" / "candidate.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"wav")
    queue = _Queue(
        {
            "task_id": "existing",
            "status": "succeeded",
            "result": {"file_path": "audio/candidate.wav"},
        }
    )
    result = await service.enqueue_character_voice_reference(
        "demo",
        "艾莉",
        manager=_PM(tmp_path, {"description": "x"}),
        queue=queue,
    )
    assert result == {
        "task_id": "existing",
        "status": "succeeded",
        "reason": "candidate_exists",
        "deduped": True,
    }
    assert queue.enqueues == []
