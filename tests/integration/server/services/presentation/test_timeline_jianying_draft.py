"""由剪辑时间线生成剪映草稿：字段级映射、下载时代入本机目录与剪映版本、产物时效、登记版本与导出前阻断。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pyJianYingDraft import TransitionType as JianyingTransition

from lib.artifacts.artifact_activation import reconcile_artifact_target_claims
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.edit_timeline import EditTimelineService
from lib.edit_timeline.model import BgmClip, EditTimelineContent
from lib.edit_timeline.operations import SetHold, SetReason, SetTransition, SetVolume, TransitionSpec
from lib.edit_timeline.store import EditTimelineStore
from lib.jianying_draft.errors import JianyingDraftError
from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService
from tests.integration.server.services.presentation.timeline_render_support import (
    CREATOR,
    append_revision,
    edited_timeline,
    install_video,
    narration_segment,
    setup_project,
    write_json,
)

PLACEHOLDER = "{{ARCREEL_JIANYING_ASSETS}}/"


def _draft_content(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path) as archive:
        return json.loads(archive.read("draft/draft_content.json"))


def _track(content: dict[str, Any], kind: str, name: str | None = None) -> dict[str, Any]:
    return next(
        track for track in content["tracks"] if track["type"] == kind and (name is None or track.get("name") == name)
    )


def _materials(content: dict[str, Any], group: str) -> dict[str, dict[str, Any]]:
    return {material["id"]: material for material in content["materials"][group]}


def _timing(segment: dict[str, Any]) -> tuple[int, int]:
    return segment["target_timerange"]["start"], segment["target_timerange"]["duration"]


def _subtitles(content: dict[str, Any]) -> list[tuple[str, int, int, str]]:
    texts = _materials(content, "texts")
    rows = []
    for segment in _track(content, "text")["segments"]:
        material = json.loads(texts[segment["material_id"]]["content"])
        font = material["styles"][0]["font"]
        rows.append((material["text"], *_timing(segment), font["id"]))
    return rows


async def test_with_narration_draft_maps_trim_volume_hold_narration_and_subtitles(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)

    result = await TimelineJianyingDraftService(pm).render("demo", timeline_id, narration="with_narration")

    assert result.artifact_path == f"renders/episode_1/{timeline_id}/jianying_draft.with_narration.zip"
    assert (result.revision, result.duration) == (2, 3.0)
    content = _draft_content(project_path / result.artifact_path)
    assert (content["canvas_config"]["width"], content["canvas_config"]["height"]) == (1080, 1920)
    videos = _materials(content, "videos")
    main = _track(content, "video")["segments"]
    assert [
        (
            _timing(segment),
            (segment["source_timerange"]["start"], segment["source_timerange"]["duration"]),
            segment["volume"],
            videos[segment["material_id"]]["type"],
        )
        for segment in main
    ] == [
        ((0, 1_000_000), (500_000, 1_000_000), 0.5, "video"),
        ((1_000_000, 500_000), (0, 500_000), 1.0, "photo"),
        ((1_000_000 + 500_000, 1_500_000), (0, 1_500_000), 0.3, "video"),
    ]
    hold_material = videos[main[1]["material_id"]]
    assert hold_material["path"] == f"{PLACEHOLDER}hold_c1.png"

    narration = _track(content, "audio", "旁白")["segments"]
    audios = _materials(content, "audios")
    assert [_timing(segment) for segment in narration] == [(0, 1_200_000)]
    assert audios[narration[0]["material_id"]]["path"].startswith(PLACEHOLDER)
    # S01 的字幕跟随旁白；S02 没有旁白配音，按源素材时间保留
    assert _subtitles(content) == [
        ("旁白一句", 0, 1_200_000, "7265596643066516029"),
        ("第二段", 1_500_000, 1_500_000, "7265596643066516029"),
    ]
    assert _track(content, "text")["name"] == "字幕"
    assert all(material["path"].startswith(PLACEHOLDER) for material in [*videos.values(), *audios.values()])


async def test_without_narration_draft_has_no_narration_track_and_keeps_source_time_subtitles(
    tmp_path: Path,
) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)

    result = await TimelineJianyingDraftService(pm).render("demo", timeline_id, narration="without_narration")

    content = _draft_content(project_path / result.artifact_path)
    assert [track["type"] for track in content["tracks"]] == ["video", "text"]
    # S01 字幕按源素材 0–2 秒分布，只显示截取窗口 0.5–1.5 秒之内的部分
    assert [row[:3] for row in _subtitles(content)] == [("旁白一句", 0, 1_000_000), ("第二段", 1_500_000, 1_500_000)]


async def test_download_substitutes_local_draft_directory_and_jianying_version(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    exported = await service.render("demo", timeline_id, narration="with_narration")
    stored = project_path / exported.artifact_path
    original = stored.read_bytes()

    for draft_root, jianying_version, assets_prefix, content_name in (
        ("/Users/me/Movies/JianyingPro Drafts", "6", "/Users/me/Movies/JianyingPro Drafts", "draft_info.json"),
        ("C:\\Users\\me\\JianyingPro Drafts\\", "5", "C:\\Users\\me\\JianyingPro Drafts", "draft_content.json"),
    ):
        package, name = await service.package_download(
            "demo", timeline_id, narration="with_narration", draft_root=draft_root, jianying_version=jianying_version
        )
        try:
            assert name == "01_One_完整版_带旁白"
            assets_dir = f"{assets_prefix}/{name}/assets/"
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                content = json.loads(archive.read(f"{name}/{content_name}"))
            paths = [material["path"] for group in ("videos", "audios") for material in content["materials"][group]]
            assert len(paths) == 4
            assert all(path.startswith(assets_dir) for path in paths)
            assert {f"{name}/assets/{path.removeprefix(assets_dir)}" for path in paths} <= names
            assert f"{name}/draft_meta_info.json" in names
            assert {f"{name}/draft_info.json", f"{name}/draft_content.json"} & names == {f"{name}/{content_name}"}
            assert stored.read_bytes() == original
            status = await service.status("demo", timeline_id, narration="with_narration")
            assert (status.status, status.version) == (ArtifactStatus.CURRENT, 1)
        finally:
            shutil.rmtree(package.parent)


async def _status(service: TimelineJianyingDraftService, timeline_id: str, narration: Any) -> tuple[str, int | None]:
    status = await service.status("demo", timeline_id, narration=narration)
    return status.status.value, status.version


async def test_every_new_revision_makes_the_draft_stale_and_each_export_bumps_its_version(tmp_path: Path) -> None:
    pm, _project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    timelines = EditTimelineService(pm)

    assert await _status(service, timeline_id, "with_narration") == ("missing", None)
    first = await service.render("demo", timeline_id, narration="with_narration")
    assert (first.version, await _status(service, timeline_id, "with_narration")) == (1, ("current", 1))
    assert await _status(service, timeline_id, "without_narration") == ("missing", None)

    # 只改剪辑理由也产生新修订，旧修订导出的草稿随之过期
    await timelines.edit(
        "demo",
        timeline_id,
        base_revision=2,
        summary="补充理由",
        operations=[SetReason(op="set_reason", clip="c2", reason="保留完整动作")],
        author=CREATOR,
    )
    assert await _status(service, timeline_id, "with_narration") == ("stale", 1)

    # 改了音量再改回原值，内容与修订 3 相同，修订 3 导出的草稿仍然过期
    await service.render("demo", timeline_id, narration="with_narration")
    for revision, volume in ((3, 0.1), (4, 0.3)):
        await timelines.edit(
            "demo",
            timeline_id,
            base_revision=revision,
            summary="调整音量",
            operations=[SetVolume(op="set_volume", clip="c2", volume=volume)],
            author=CREATOR,
        )
    assert await _status(service, timeline_id, "with_narration") == ("stale", 2)

    old = await service.render("demo", timeline_id, narration="with_narration", revision=2)
    assert (old.revision, await _status(service, timeline_id, "with_narration")) == (2, ("stale", 3))
    latest = await service.render("demo", timeline_id, narration="with_narration")
    assert (latest.revision, await _status(service, timeline_id, "with_narration")) == (5, ("current", 4))


async def test_draft_stays_stale_and_claimed_while_a_new_video_has_no_presentation_yet(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    await service.render("demo", timeline_id, narration="with_narration")

    install_video(project_path, narration_segment("E1S02", "第二段"), 1.0)
    key = ArtifactKey.episode_jianying_draft(1, timeline_id, "with_narration")

    assert reconcile_artifact_target_claims(project_path, [key]) is False
    assert key in ProjectArtifactManifestAdapter(project_path).snapshot_entries()
    assert (await service.status("demo", timeline_id, narration="with_narration")).status is ArtifactStatus.STALE


async def test_export_is_refused_on_blocking_issues_and_unavailable_narration_variant(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path, narration_delivery="post_production")
    timeline_id = await edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)

    with pytest.raises(JianyingDraftError) as variant_refused:
        await service.check("demo", timeline_id, narration="with_narration")
    assert variant_refused.value.code == "jianying_draft_narration_unavailable"

    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"].append(narration_segment("E1S03", "还没有视频"))
    write_json(script_path, script)
    blocked_id = (
        await EditTimelineService(pm).create_from_script("demo", episode=1, name="新版", author=CREATOR)
    ).timeline.id

    with pytest.raises(JianyingDraftError) as blocked:
        await service.render("demo", blocked_id, narration="without_narration")
    assert blocked.value.code == "jianying_draft_blocked"
    assert [(issue["code"], issue["unit_id"]) for issue in blocked.value.params["issues"]] == [
        ("video_missing", "E1S03")
    ]
    assert not (project_path / "renders" / "episode_1" / blocked_id).exists()


def _transitions(content: dict[str, Any]) -> list[tuple[int, int, str, int, bool, str]]:
    """主视频轨上挂了转场的段：(段序号, 段起点, 转场名, 时长, 是否重叠, 效果 ID)。"""
    transitions = _materials(content, "transitions")
    rows = []
    for index, segment in enumerate(_track(content, "video")["segments"]):
        for ref in segment["extra_material_refs"]:
            if ref in transitions:
                material = transitions[ref]
                rows.append(
                    (
                        index,
                        segment["target_timerange"]["start"],
                        material["name"],
                        material["duration"],
                        material["is_overlap"],
                        material["effect_id"],
                    )
                )
    return rows


async def test_transitions_hang_on_the_last_segment_of_the_previous_clip(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    editor = EditTimelineService(pm)
    await editor.edit(
        "demo",
        timeline_id,
        base_revision=2,
        summary="加转场",
        operations=[
            SetTransition(op="set_transition", clip="c1", transition=TransitionSpec(type="push_left", duration=0.4))
        ],
        author=CREATOR,
    )

    with_hold = await service.render("demo", timeline_id, narration="without_narration")

    # c1 带 0.5 秒定格：转场挂在出点帧静帧这一段（主轨第 2 段）上；草稿总长不变。
    assert with_hold.duration == 3.0
    content = _draft_content(project_path / with_hold.artifact_path)
    assert _transitions(content) == [(1, 1_000_000, "向左", 400_000, False, JianyingTransition.向左.value.effect_id)]

    await editor.edit(
        "demo",
        timeline_id,
        base_revision=3,
        summary="去掉定格，改为叠化",
        operations=[
            SetHold(op="set_hold", clip="c1", hold=0),
            SetTransition(op="set_transition", clip="c1", transition=TransitionSpec(type="dissolve", duration=0.6)),
        ],
        author=CREATOR,
    )

    without_hold = await service.render("demo", timeline_id, narration="without_narration")

    content = _draft_content(project_path / without_hold.artifact_path)
    assert _transitions(content) == [(0, 0, "叠化", 600_000, True, JianyingTransition.叠化.value.effect_id)]
    assert [_timing(segment) for segment in _track(content, "video")["segments"]] == [
        (0, 1_000_000),
        (1_000_000, 1_500_000),
    ]


async def test_bgm_is_refused_like_the_final_cut(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    store = EditTimelineStore(pm, "demo")
    document = store.find(timeline_id)
    content = document.latest.content.model_copy(
        update={"bgm": (BgmClip(id="b1", bgm_id="theme", start_us=0, in_us=0, out_us=1_000_000),)}
    )
    with store.locked_episode(document.episode):
        store.write(document.model_copy(update={"next_bgm_number": 2}))
    append_revision(pm, timeline_id, content)

    with pytest.raises(JianyingDraftError) as bgm_refused:
        await TimelineJianyingDraftService(pm).check("demo", timeline_id, narration="without_narration")
    assert bgm_refused.value.code == "jianying_draft_content_unsupported"
    assert bgm_refused.value.params == {"bgm_ids": ["b1"]}
    assert not (project_path / "renders" / "episode_1" / timeline_id).exists()


async def test_edit_timeline_without_clips_is_refused_like_the_final_cut(tmp_path: Path) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    append_revision(pm, timeline_id, EditTimelineContent())

    with pytest.raises(JianyingDraftError) as refused:
        await TimelineJianyingDraftService(pm).render("demo", timeline_id, narration="without_narration")
    assert refused.value.code == "jianying_draft_empty"
    assert not (project_path / "renders" / "episode_1" / timeline_id).exists()


@pytest.mark.parametrize(
    ("narration_delivery", "expected"),
    [("use_tts", "with_narration"), ("post_production", "without_narration")],
)
async def test_omitted_narration_version_follows_the_project_narration_delivery(
    tmp_path: Path, narration_delivery: str, expected: str
) -> None:
    pm, _project_path = setup_project(tmp_path, narration_delivery=narration_delivery)
    timeline_id = await edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)

    check = await service.check("demo", timeline_id)
    await service.render("demo", timeline_id, narration=check.narration)
    status = await service.status("demo", timeline_id)

    assert check.narration == expected
    assert (status.narration, status.status) == (expected, ArtifactStatus.CURRENT)
