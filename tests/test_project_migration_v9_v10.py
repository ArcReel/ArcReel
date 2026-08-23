"""v9→v10 Unified Video Style prompt migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.project_migrations.runner import MIGRATORS, migrate_project_dir
from lib.project_migrations.v9_to_v10_video_style_prompt import migrate_v9_to_v10
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION

pytestmark = pytest.mark.unit


def _write_project(tmp_path: Path, video_style: object) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    payload = {
        "schema_version": 9,
        "source_language": "zh",
        "video_style": video_style,
    }
    (project_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _legacy_style() -> dict[str, object]:
    return {
        "visual_treatment": "写实乡村庭院与3D毛绒鳄鱼融合",
        "camera_language": "固定机位为主，缓慢推拉",
        "pacing": "长镜头，低动作密度",
        "sound_focus": "asmr",
        "music_policy": "custom",
        "music_description": "轻柔木琴",
        "sound_design": "突出研磨、流水与铜丝声",
        "additional_instructions": "保持竖屏构图",
        "source": "agent",
        "updated_at": "2026-08-23T00:00:00Z",
    }


def test_migration_preserves_every_dimension_and_metadata(tmp_path: Path) -> None:
    project_dir = _write_project(tmp_path, _legacy_style())

    assert migrate_project_dir(project_dir) is True

    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    style = project["video_style"]
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert set(style) == {"prompt", "source", "updated_at"}
    for value in (
        "写实乡村庭院与3D毛绒鳄鱼融合",
        "固定机位为主，缓慢推拉",
        "长镜头，低动作密度",
        "ASMR",
        "轻柔木琴",
        "突出研磨、流水与铜丝声",
        "保持竖屏构图",
    ):
        assert value in style["prompt"]
    assert style["source"] == "agent"
    assert style["updated_at"] == "2026-08-23T00:00:00Z"
    assert list(project_dir.glob("project.json.bak.v9-*"))


def test_migration_preserves_null_style(tmp_path: Path) -> None:
    project_dir = _write_project(tmp_path, None)

    migrate_v9_to_v10(project_dir)

    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert project["video_style"] is None
    assert project["schema_version"] == 10


def test_migration_rejects_invalid_legacy_style_before_bumping_version(tmp_path: Path) -> None:
    legacy = _legacy_style()
    legacy["music_description"] = ""
    project_dir = _write_project(tmp_path, legacy)

    with pytest.raises(ValueError, match="music_description"):
        migrate_v9_to_v10(project_dir)

    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert project["schema_version"] == 9
    assert project["video_style"] == legacy


def test_runner_registers_v9_migration() -> None:
    assert MIGRATORS[9] is migrate_v9_to_v10
