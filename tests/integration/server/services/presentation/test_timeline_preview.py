"""剪辑视图预览的素材层：旁白版本取项目默认值，字幕与剪映草稿取自同一份呈现模型。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifacts.version_manager import VersionManager
from lib.bgm.service import BgmLibraryService
from lib.edit_timeline import EditTimelineService
from lib.edit_timeline.operations import InsertBgm
from server.services.presentation.timeline_preview import TimelinePreviewMedia, TimelinePreviewService
from tests.factories import wav_bytes
from tests.integration.server.services.presentation.timeline_render_support import (
    CREATOR,
    edited_timeline,
    narration_segment,
    setup_project,
    write_json,
)


def _rows(media: TimelinePreviewMedia) -> list[tuple[str, object, bool, list[tuple[str, float, float]]]]:
    return [
        (
            unit.unit_id,
            unit.narration_audio.model_dump() if unit.narration_audio else None,
            unit.subtitles_follow_narration,
            [(cue.text, cue.start, cue.duration) for cue in unit.subtitles],
        )
        for unit in media.units
    ]


async def test_tts_project_previews_narration_audio_and_subtitles_that_follow_it(tmp_path: Path) -> None:
    pm, _project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)

    media = await TimelinePreviewService(pm).media("demo", timeline_id)

    assert (media.timeline_id, media.revision, media.narration) == (timeline_id, 2, "with_narration")
    # 字幕从旁白起点算起、按旁白时长分布；S02 的旁白比视频长，字幕照样覆盖整段旁白
    assert _rows(media) == [
        ("E1S01", {"path": "audio/segment_E1S01.wav", "version": 1}, True, [("旁白一句", 0.0, 1.2)]),
        ("E1S02", {"path": "audio/segment_E1S02.wav", "version": 1}, True, [("第二段", 0.0, 2.0)]),
    ]


async def test_provider_audio_switch_follows_the_current_video_version_record(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = TimelinePreviewService(pm)

    assert [(unit.unit_id, unit.provider_audio) for unit in (await service.media("demo", timeline_id)).units] == [
        ("E1S01", True),
        ("E1S02", True),
    ]

    # 生成时关闭了供应商原声：文件里即使带音轨，成片与预览都不用
    VersionManager(project_path).update_version_metadata("videos", "E1S02", 1, execution_generate_audio=False)

    assert [(unit.unit_id, unit.provider_audio) for unit in (await service.media("demo", timeline_id)).units] == [
        ("E1S01", True),
        ("E1S02", False),
    ]


async def test_unit_without_video_version_counts_as_provider_audio_on(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"].append(narration_segment("E1S03", "还没有视频"))
    write_json(script_path, script)
    timeline_id = (
        await EditTimelineService(pm).create_from_script("demo", episode=1, name="新版", author=CREATOR)
    ).timeline.id

    media = await TimelinePreviewService(pm).media("demo", timeline_id)

    assert media.units[2].provider_audio is True


async def test_post_production_project_previews_without_narration(tmp_path: Path) -> None:
    pm, _project_path = setup_project(tmp_path, narration_delivery="post_production")
    timeline_id = await edited_timeline(pm)

    media = await TimelinePreviewService(pm).media("demo", timeline_id)

    assert media.narration == "without_narration"
    assert _rows(media) == [
        ("E1S01", None, False, [("旁白一句", 0.0, 2.0)]),
        ("E1S02", None, False, [("第二段", 0.0, 1.5)]),
    ]


async def test_unit_without_video_is_listed_without_subtitles(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"].append(narration_segment("E1S03", "还没有视频"))
    write_json(script_path, script)
    timeline_id = (
        await EditTimelineService(pm).create_from_script("demo", episode=1, name="新版", author=CREATOR)
    ).timeline.id

    media = await TimelinePreviewService(pm).media("demo", timeline_id)

    assert [unit.unit_id for unit in media.units] == ["E1S01", "E1S02", "E1S03"]
    assert _rows(media)[2] == ("E1S03", None, False, [])


async def test_bgm_on_the_track_is_previewed_with_its_loudness_gain(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    library = BgmLibraryService(pm)
    used = await library.upload("demo", filename="主题曲.wav", content=wav_bytes(1.0, tone_hz=330))
    await library.upload("demo", filename="备用.wav", content=wav_bytes(1.0, tone_hz=440))
    timeline_id = await edited_timeline(pm)
    revision = (await EditTimelineService(pm).read("demo", timeline_id)).revision
    await EditTimelineService(pm).edit(
        "demo",
        timeline_id,
        base_revision=revision,
        summary="加 BGM",
        operations=[InsertBgm(op="insert_bgm", bgm_id=used.id, start=0)],
        author=CREATOR,
    )

    media = await TimelinePreviewService(pm).media("demo", timeline_id)

    assert [item.model_dump() for item in media.bgm] == [{"bgm_id": used.id, "path": used.file, "gain": used.gain}]

    (project_path / used.file).unlink()
    assert (await TimelinePreviewService(pm).media("demo", timeline_id)).bgm == ()
