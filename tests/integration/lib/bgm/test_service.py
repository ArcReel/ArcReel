"""BGM 登记：在真实临时项目上用随包 ffmpeg 实测响度，按字节登记为项目级产物。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.bgm.library import TARGET_LOUDNESS_LUFS, bgm_key
from lib.bgm.service import BgmError, BgmLibraryService
from lib.project.project_manager import ProjectManager
from tests.factories import wav_bytes


@pytest.fixture
def blank_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path / "projects"))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    return manager


def _leftovers(blank_pm: ProjectManager) -> list[str]:
    bgm_dir = blank_pm.get_project_path("demo") / "bgm"
    return sorted(path.name for path in bgm_dir.iterdir()) if bgm_dir.exists() else []


async def test_upload_measures_loudness_once_and_registers_the_bytes(blank_pm: ProjectManager) -> None:
    service = BgmLibraryService(blank_pm)

    track = await service.upload("demo", filename="雨夜钢琴.wav", content=wav_bytes(1.5, tone_hz=330))

    project_dir = blank_pm.get_project_path("demo")
    assert track.name == "雨夜钢琴"
    assert track.file == f"bgm/{track.id}.wav"
    assert (project_dir / track.file).read_bytes() == wav_bytes(1.5, tone_hz=330)
    assert track.duration == pytest.approx(1.5, abs=0.01)
    assert -30 < track.integrated_lufs < -5
    assert track.gain_db == pytest.approx(TARGET_LOUDNESS_LUFS - track.integrated_lufs, abs=0.01)
    stored = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["bgm"]
    assert stored == {track.id: track.model_dump(mode="json", exclude={"id"})}
    assert await service.list("demo") == (track,)
    assert _leftovers(blank_pm) == [f"{track.id}.wav"]
    status = ArtifactCurrencyResolver(project_dir).compare(bgm_key(track.id), artifact_path=track.file).status
    assert status is ArtifactStatus.CURRENT


async def test_replaced_bytes_make_the_registered_bgm_stale(blank_pm: ProjectManager) -> None:
    track = await BgmLibraryService(blank_pm).upload("demo", filename="a.wav", content=wav_bytes(1.0, tone_hz=330))
    project_dir = blank_pm.get_project_path("demo")
    (project_dir / track.file).write_bytes(wav_bytes(1.0, tone_hz=550))

    status = ArtifactCurrencyResolver(project_dir).compare(bgm_key(track.id), artifact_path=track.file).status

    assert status is ArtifactStatus.STALE


async def test_uploads_list_in_upload_order(blank_pm: ProjectManager) -> None:
    service = BgmLibraryService(blank_pm)
    first = await service.upload("demo", filename="一.wav", content=wav_bytes(1.0, tone_hz=330))
    second = await service.upload("demo", filename="二.wav", content=wav_bytes(1.0, tone_hz=440))

    assert [track.id for track in await service.list("demo")] == [first.id, second.id]


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("silence.wav", wav_bytes(1.0), "bgm_silent"),
        ("broken.mp3", b"not audio at all", "bgm_invalid_audio"),
        ("cover.png", wav_bytes(1.0, tone_hz=330), "bgm_unsupported_type"),
    ],
)
async def test_unusable_uploads_are_refused_without_leaving_files(
    blank_pm: ProjectManager, filename: str, content: bytes, code: str
) -> None:
    service = BgmLibraryService(blank_pm)

    with pytest.raises(BgmError) as refused:
        await service.upload("demo", filename=filename, content=content)

    assert refused.value.code == code
    assert _leftovers(blank_pm) == []
    assert await service.list("demo") == ()


async def test_unknown_project_is_reported(blank_pm: ProjectManager) -> None:
    with pytest.raises(BgmError) as refused:
        await BgmLibraryService(blank_pm).list("absent")

    assert refused.value.code == "project_not_found"
