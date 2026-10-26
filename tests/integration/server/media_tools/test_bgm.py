"""list_bgm 工具：列出项目里已上传的 BGM 供 edit_timeline 引用。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib.bgm.service import BgmLibraryService
from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import ProjectManager
from server.agent_toolset.edit_timelines import IMPORT_BGM, LIST_BGM
from server.media_tools.bgm import BgmListItem
from server.tool_runtime import CallerContext
from tests.factories import wav_bytes
from tests.fakes import use_os_open
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


def _harness(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return ToolHarness("demo", tmp_path, pm, caller=CallerContext(user_id=DEFAULT_USER_ID, source="mcp"))


async def test_list_bgm_reports_id_name_and_duration_in_upload_order(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    service = BgmLibraryService(harness.pm)
    first = await service.upload("demo", filename="雨夜.wav", content=wav_bytes(1.0, tone_hz=330))
    second = await service.upload("demo", filename="追逐.mp3.wav", content=wav_bytes(2.0, tone_hz=440))

    listed = await run_declared_tool(LIST_BGM, harness, {})

    assert listed.problem is None
    assert listed.value == (
        BgmListItem(id=first.id, name="雨夜", duration=1.0),
        BgmListItem(id=second.id, name="追逐.mp3", duration=2.0),
    )
    assert LIST_BGM.summary is not None
    assert first.id in LIST_BGM.summary(listed.value)


async def test_empty_library_says_where_bgm_comes_from(tmp_path: Path) -> None:
    listed = await run_declared_tool(LIST_BGM, _harness(tmp_path), {})

    assert listed.value == ()
    assert LIST_BGM.summary is not None
    assert "BGM 轨" in LIST_BGM.summary(())


@pytest.mark.parametrize("without_nofollow", [False, True])
async def test_import_registers_existing_mix_without_changing_source_or_other_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, without_nofollow: bool
) -> None:
    if without_nofollow:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    harness = _harness(tmp_path)
    service = BgmLibraryService(harness.pm)
    first = await service.upload("demo", filename="original.wav", content=wav_bytes(1.0, tone_hz=330))
    project = harness.pm.get_project_path("demo")
    source = project / "audio" / "revised mix.wav"
    content = wav_bytes(2.0, tone_hz=440)
    source.write_bytes(content)

    result = await run_declared_tool(IMPORT_BGM, harness, {"source_path": "audio/revised mix.wav"})

    assert result.problem is None
    assert result.value is not None
    tracks = await service.list("demo")
    assert len(tracks) == 2
    assert tracks[0] == first
    track = tracks[1]
    assert result.value.id == track.id
    assert result.value.name == "revised mix"
    assert result.value.duration == 2.0
    assert result.value.gain_db == track.gain_db
    assert result.value.integrated_lufs == track.integrated_lufs
    assert source.read_bytes() == content
    assert (project / track.file).read_bytes() == content
    assert IMPORT_BGM.summary is not None
    assert track.id in IMPORT_BGM.summary(result.value)


@pytest.mark.parametrize(
    "source_path",
    [
        "../outside.wav",
        "/tmp/outside.wav",
        ".private/audio.wav",
        "audio/../outside.wav",
        "audio\\clip.wav",
        "audio/key.env",
        "missing.wav",
    ],
)
async def test_import_rejects_unsafe_or_missing_sources_without_library_mutation(
    tmp_path: Path, source_path: str
) -> None:
    harness = _harness(tmp_path)
    result = await run_declared_tool(IMPORT_BGM, harness, {"source_path": source_path})
    assert result.problem is not None
    assert await BgmLibraryService(harness.pm).list("demo") == ()


async def test_import_cannot_follow_symlink_to_another_project(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    harness.pm.create_project("other")
    outside = harness.pm.get_project_path("other") / "secret.wav"
    outside.write_bytes(wav_bytes(1.0, tone_hz=330))
    (harness.pm.get_project_path("demo") / "linked.wav").symlink_to(outside)

    result = await run_declared_tool(IMPORT_BGM, harness, {"source_path": "linked.wav"})

    assert result.problem is not None
    assert result.problem.code == "bgm_invalid_audio"
    assert await BgmLibraryService(harness.pm).list("demo") == ()


@pytest.mark.parametrize(
    ("content", "code"),
    [(b"not audio", "bgm_invalid_audio"), (wav_bytes(1.0), "bgm_silent")],
    ids=["invalid", "silent"],
)
async def test_import_uses_real_upload_audio_validation(tmp_path: Path, content: bytes, code: str) -> None:
    harness = _harness(tmp_path)
    (harness.pm.get_project_path("demo") / "bad.wav").write_bytes(content)

    result = await run_declared_tool(IMPORT_BGM, harness, {"source_path": "bad.wav"})

    assert result.problem is not None
    assert result.problem.code == code
    assert await BgmLibraryService(harness.pm).list("demo") == ()


async def test_import_bounds_source_read_before_probing(tmp_path: Path) -> None:
    from lib.bgm.library import BGM_MAX_BYTES

    harness = _harness(tmp_path)
    with (harness.pm.get_project_path("demo") / "oversized.wav").open("wb") as source:
        source.truncate(BGM_MAX_BYTES + 1)

    result = await run_declared_tool(IMPORT_BGM, harness, {"source_path": "oversized.wav"})

    assert result.problem is not None
    assert result.problem.code == "bgm_too_large"
    assert await BgmLibraryService(harness.pm).list("demo") == ()


async def test_import_rejects_file_replaced_by_symlink_during_open_without_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path)
    source = harness.pm.get_project_path("demo") / "mix.wav"
    source.write_bytes(wav_bytes(1.0, tone_hz=330))
    outside = tmp_path / "private.wav"
    outside.write_bytes(wav_bytes(1.0, tone_hz=440))
    open_file = os.open

    def replacing_open(path, flags, *args, **kwargs):
        if Path(path) == source:
            source.unlink()
            source.symlink_to(outside)
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    use_os_open(monkeypatch, replacing_open)

    result = await run_declared_tool(IMPORT_BGM, harness, {"source_path": "mix.wav"})

    assert result.problem is not None
    assert result.problem.code == "bgm_invalid_audio"
    assert await BgmLibraryService(harness.pm).list("demo") == ()
