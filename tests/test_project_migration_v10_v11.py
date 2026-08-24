from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.project_migration_failure import ProjectMigrationError
from lib.project_migrations.runner import MIGRATORS
from lib.project_migrations.v10_to_v11_reference_storyboard_sheet import migrate_v10_to_v11

pytestmark = pytest.mark.unit


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_reference_video_migration_seeds_stable_keyframe_and_step1_plan(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write(
        project_dir / "project.json",
        {
            "schema_version": 10,
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        },
    )
    _write(
        project_dir / "scripts/episode_1.json",
        {
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "text": "@[鳄鱼爸爸] 坐在木桌前。",
                    "duration_seconds": 8,
                    "generated_assets": {},
                }
            ]
        },
    )
    _write(
        project_dir / "drafts/episode_1/step1_reference_units.json",
        {
            "units": [
                {
                    "unit_id": "E1U01",
                    "text": "@[鳄鱼爸爸] 坐在木桌前。",
                    "source_text": "原文",
                    "duration_seconds": 8,
                }
            ]
        },
    )

    migrate_v10_to_v11(project_dir)

    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    script = json.loads((project_dir / "scripts/episode_1.json").read_text(encoding="utf-8"))
    step1 = json.loads((project_dir / "drafts/episode_1/step1_reference_units.json").read_text(encoding="utf-8"))
    unit = script["video_units"][0]
    assert project["schema_version"] == 11
    assert unit["keyframes"] == [
        {
            "keyframe_id": "E1U01K01",
            "description": "当前 Video Unit 开场场景的第一个稳定画面",
            "image_path": None,
        }
    ]
    assert unit["text"].startswith("@[关键分镜 E1U01K01]")
    assert step1["units"][0]["keyframe_plan"] == ["当前 Video Unit 开场场景的第一个稳定画面"]
    assert "storyboard_sheet" not in unit
    assert list(project_dir.glob("project.json.bak.v10-*"))
    assert list((project_dir / "scripts").glob("episode_1.json.bak.v10-*"))
    assert list((project_dir / "drafts/episode_1").glob("step1_reference_units.json.bak.v10-*"))


def test_non_reference_migration_only_advances_schema(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write(
        project_dir / "project.json",
        {"schema_version": 10, "generation_mode": "storyboard", "episodes": []},
    )

    migrate_v10_to_v11(project_dir)

    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert project["schema_version"] == 11
    assert MIGRATORS[10] is migrate_v10_to_v11


def test_reference_video_migration_preflights_all_files_before_any_write(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write(
        project_dir / "project.json",
        {
            "schema_version": 10,
            "generation_mode": "reference_video",
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
                {"episode": 2, "script_file": "scripts/episode_2.json"},
            ],
        },
    )
    _write(
        project_dir / "scripts/episode_1.json",
        {
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "text": "第一集",
                    "duration_seconds": 8,
                    "generated_assets": {},
                }
            ]
        },
    )
    broken_path = project_dir / "scripts/episode_2.json"
    broken_path.write_text("{broken", encoding="utf-8")
    before = {
        path: path.read_bytes()
        for path in (
            project_dir / "project.json",
            project_dir / "scripts/episode_1.json",
            broken_path,
        )
    }

    with pytest.raises(ProjectMigrationError):
        migrate_v10_to_v11(project_dir)

    assert {path: path.read_bytes() for path in before} == before
    assert not list(project_dir.rglob("*.bak.v10-*"))
