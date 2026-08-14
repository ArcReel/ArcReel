"""v7→v8 领域数据契约迁移：字段更名、统计去持久化、预检原子性。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.project_migrations.runner import CURRENT_SCHEMA_VERSION, migrate_project_dir
from lib.project_migrations.v7_to_v8_domain_contract import migrate_v7_to_v8

pytestmark = pytest.mark.integration


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _v7_project(
    tmp_path: Path,
    *,
    content_mode: str = "drama",
    source_kind: str = "screenplay",
    generation_mode: str = "storyboard",
    episodes: int = 1,
    extra_episode_fields: dict | None = None,
) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    episode_entries = []
    for number in range(1, episodes + 1):
        entry = {
            "episode": number,
            "title": f"第 {number} 集",
            "script_file": f"scripts/episode_{number}.json",
            "scenes_count": 9,
        }
        if extra_episode_fields:
            entry.update(extra_episode_fields)
        episode_entries.append(entry)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 7,
            "content_mode": content_mode,
            "source_kind": source_kind,
            "generation_mode": generation_mode,
            "episodes": episode_entries,
        },
    )
    return project_dir


def _storyboard_script(*, content_mode: str = "drama") -> dict:
    items_key = {"narration": "segments", "drama": "scenes", "ad": "shots"}[content_mode]
    id_field = {"narration": "segment_id", "drama": "scene_id", "ad": "shot_id"}[content_mode]
    return {
        "episode": 1,
        "title": "开端",
        "content_mode": content_mode,
        "metadata": {"total_scenes": 2, "estimated_duration_seconds": 16},
        items_key: [
            {id_field: "E1S1", "duration_seconds": 8, "generated_assets": {}},
            {id_field: "E1S2", "duration_seconds": 8, "generated_assets": {}},
        ],
    }


def _reference_script(*, content_mode: str = "narration") -> dict:
    return {
        "episode": 1,
        "title": "参考集",
        "content_mode": content_mode,
        "metadata": {"total_scenes": 1},
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "镜头1：@[英雄] 走进大厅"}],
                "references": [{"type": "character", "name": "英雄"}],
                "duration_seconds": 8,
                "generated_assets": {},
            }
        ],
    }


def test_renames_project_and_script_contract_fields(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == 8
    assert project["creation_type"] == "drama"
    assert project["source_file_type"] == "screenplay"
    assert "content_mode" not in project
    assert "source_kind" not in project
    assert "scenes_count" not in project["episodes"][0]

    script = _read_json(project_dir / "scripts/episode_1.json")
    assert script["creation_type"] == "drama"
    assert "content_mode" not in script
    assert "total_scenes" not in script.get("metadata", {})
    assert [item["scene_id"] for item in script["scenes"]] == ["E1S1", "E1S2"]
    assert list((project_dir / "scripts").glob("episode_1.json.bak.v7-*"))
    assert list(project_dir.glob("project.json.bak.v7-*"))


@pytest.mark.parametrize("mode", ["narration", "drama", "ad"])
def test_migrates_all_creation_types(tmp_path: Path, mode: str) -> None:
    project_dir = _v7_project(tmp_path, content_mode=mode, source_kind="novel")
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script(content_mode=mode))

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    script = _read_json(project_dir / "scripts/episode_1.json")
    assert project["creation_type"] == mode
    assert script["creation_type"] == mode


def test_materializes_missing_creation_and_source_fields(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 7,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    _write_json(
        project_dir / "scripts/episode_1.json",
        {"episode": 1, "title": "开端", "segments": [{"segment_id": "E1S1"}]},
    )

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    script = _read_json(project_dir / "scripts/episode_1.json")
    assert project["creation_type"] == "narration"
    assert project["source_file_type"] == "novel"
    assert script["creation_type"] == "narration"


def test_reference_video_keeps_shots_and_references(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path, content_mode="narration", generation_mode="reference_video")
    payload = _reference_script()
    _write_json(project_dir / "scripts/episode_1.json", payload)

    migrate_v7_to_v8(project_dir)

    script = _read_json(project_dir / "scripts/episode_1.json")
    assert script["creation_type"] == "narration"
    assert script["video_units"][0]["shots"] == payload["video_units"][0]["shots"]
    assert script["video_units"][0]["references"] == payload["video_units"][0]["references"]
    assert "text" not in script["video_units"][0]
    assert "reference_assets" not in script["video_units"][0]


def test_invalid_script_blocks_all_writes(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path, episodes=2)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "scripts" / "episode_2.json").write_text("{not-json", encoding="utf-8")
    original_project = (project_dir / "project.json").read_text(encoding="utf-8")
    original_script = (project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")

    with pytest.raises((json.JSONDecodeError, ValueError)):
        migrate_v7_to_v8(project_dir)

    assert (project_dir / "project.json").read_text(encoding="utf-8") == original_project
    assert (project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8") == original_script
    assert not list(project_dir.glob("project.json.bak.v7-*"))
    assert not list((project_dir / "scripts").glob("episode_1.json.bak.v7-*"))


def test_invalid_creation_type_blocks_rewrite(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path, content_mode="documentary")
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    original_project = (project_dir / "project.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="creation_type|content_mode"):
        migrate_v7_to_v8(project_dir)

    assert (project_dir / "project.json").read_text(encoding="utf-8") == original_project
    assert _read_json(project_dir / "project.json")["schema_version"] == 7


def test_idempotent_when_already_v8(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    migrate_v7_to_v8(project_dir)
    first = _read_json(project_dir / "project.json")
    migrate_v7_to_v8(project_dir)
    assert _read_json(project_dir / "project.json") == first


def test_runner_entry_upgrades_v7_to_current(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    assert migrate_project_dir(project_dir) is True
    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_SCHEMA_VERSION
    assert project["creation_type"] == "drama"
    assert migrate_project_dir(project_dir) is False


def test_profile_manifest_field_renamed(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(
        project_dir / ".arcreel_profile_manifest.json",
        {"schema_version": 1, "profile_id": "arcreel/builtin", "content_mode": "drama", "files": {}},
    )

    migrate_v7_to_v8(project_dir)

    manifest = _read_json(project_dir / ".arcreel_profile_manifest.json")
    assert manifest["creation_type"] == "drama"
    assert "content_mode" not in manifest
