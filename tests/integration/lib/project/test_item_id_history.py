"""分镜与视频单元编号只分配、不复用：用过的编号从哪里认，新编号从哪里往后取。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager
from lib.project import project_trash
from lib.project.item_id_history import issue_landing_item_ids, used_item_numbers
from lib.project.project_manager import ProjectManager


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _segment(segment_id: str) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "duration_seconds": 4,
        "novel_text": "风吹过旷野。",
        "characters_in_segment": [],
        "image_prompt": None,
        "video_prompt": None,
        "generated_assets": {},
    }


def _narration_project(tmp_path: Path, ids: list[str]) -> ProjectManager:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="narration")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    script = {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": [_segment(i) for i in ids]}
    pm.save_script("demo", script, "episode_1.json")
    return pm


def _episode_entry(pm: ProjectManager, episode: int = 1) -> dict[str, Any]:
    return next(entry for entry in pm.load_project("demo")["episodes"] if entry["episode"] == episode)


def test_every_source_counts_as_used(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(project_dir / "scripts" / "episode_1.json", {"episode": 1, "segments": [{"segment_id": "E1S01"}]})
    _write_json(
        project_dir / "edit_timelines" / "t1" / "timeline.json",
        {"tracks": [{"clips": [{"id": "c1", "unit_id": "E2U7"}]}]},
    )
    versions_source = project_dir / "storyboards" / "scene_E1S04.png"
    versions_source.parent.mkdir(parents=True)
    versions_source.write_bytes(b"old")
    VersionManager(project_dir).add_version("storyboards", "E1S04", "旧画面", source_file=versions_source)
    ProjectArtifactManifestAdapter(project_dir).put_entry(
        ArtifactKey.episode_video(1, "E1S06_2"),
        ArtifactManifestEntry(artifact_path="videos/scene_E1S06_2.mp4", basis_digest=f"sha256-v1:{'a' * 64}"),
    )
    removed = project_dir / "audio" / "segment_E1S09.wav"
    removed.parent.mkdir(parents=True)
    removed.write_bytes(b"voice")
    project_trash.record_deletion(project_dir, {"kind": "script_entry", "id": "E1S09"}, ["audio/segment_E1S09.wav"])
    snapshot_record = project_trash.trash_dir(project_dir) / "20260101T000000Z-deadbeef"
    _write_json(snapshot_record / "record.json", {"object": {"kind": "episode", "id": 3}})
    _write_json(
        snapshot_record / "snapshot" / "scripts" / "episode_3.json",
        {"episode": 3, "video_units": [{"unit_id": "E3U12"}]},
    )

    assert used_item_numbers(project_dir) == {1: {1, 4, 6, 9}, 2: {7}, 3: {12}}


def test_unreadable_files_are_skipped(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "scripts" / "episode_1.json").write_text("{not json", encoding="utf-8")
    (project_dir / "versions").mkdir()
    (project_dir / "versions" / "versions.json").write_text("[]", encoding="utf-8")

    assert used_item_numbers(project_dir) == {}


def test_saving_a_script_records_the_highest_number_and_never_lowers_it(tmp_path: Path) -> None:
    pm = _narration_project(tmp_path, ["E1S01", "E1S05"])
    assert _episode_entry(pm)["item_id_high_water"] == 5

    with pm.locked_script("demo", "episode_1.json") as script:
        script["segments"] = script["segments"][:1]

    assert _episode_entry(pm)["item_id_high_water"] == 5


def test_reserved_ids_continue_after_the_highest_used_number_and_are_not_handed_out_twice(tmp_path: Path) -> None:
    pm = _narration_project(tmp_path, ["E1S01", "E1S05"])
    with pm.locked_script("demo", "episode_1.json") as script:
        script["segments"] = script["segments"][:1]

    first = pm.reserve_item_ids("demo", 1, "segments", 2)
    second = pm.reserve_item_ids("demo", 1, "video_units", 1)

    assert (first, second) == (["E1S06", "E1S07"], ["E1U8"])
    assert _episode_entry(pm)["item_id_high_water"] == 8


def test_landing_issuance_never_hands_out_a_used_number(tmp_path: Path) -> None:
    pm = _narration_project(tmp_path, ["E1S01", "E1S02", "E1S03"])
    project_dir = pm.get_project_path("demo")
    script = pm.load_script("demo", "episode_1.json")
    script["segments"] = script["segments"][:1]
    pm.save_script("demo", script, "episode_1.json")  # 移除的 E1S02、E1S03 留下删除记录
    timeline = project_dir / "edit_timelines" / "t1" / "timeline.json"
    _write_json(timeline, {"tracks": [{"clips": [{"id": "c1", "unit_id": "E1S04"}]}]})
    plan = {
        "episode": 1,
        "content_mode": "narration",
        # E1S02 已删除、E1S04 只在剪辑时间线里；E1S08 从没用过，E2S01 属于别的集，E1S08 重复出现
        "segments": [_segment(i) for i in ("E1S02", "E1S08", "E1S04", "E2S01", "E1S08")],
    }

    renamed = issue_landing_item_ids(project_dir, pm.load_project("demo"), plan)

    ids = [segment["segment_id"] for segment in plan["segments"]]
    assert ids == ["E1S05", "E1S06", "E1S07", "E1S08", "E1S09"]
    assert renamed["E1S02"] == ids[0]
    assert "E1S02" not in ids
    assert "E1S04" not in ids
    assert "E1S03" not in ids
