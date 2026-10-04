"""v16→v17 迁移：只改写精确的退役文本模型引用；版本守卫与幂等。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.project.project_migrations.v16_to_v17_retired_text_model_ids import migrate_project_dict, migrate_v16_to_v17


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _load(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def test_values_other_than_the_exact_retired_references_are_kept() -> None:
    project = {
        "default_text_backend": "gemini-aistudio/gemini-3-flash-preview",
        "text_backend_simple": "openai/gemini-3.1-flash-lite-preview",
        "text_backend_complex": 3,
        "video_backend": "gemini-aistudio/gemini-3.1-flash-lite-preview",
    }

    assert migrate_project_dict(project) == project


def test_file_migration_rewrites_and_bumps_the_schema_then_is_a_noop(tmp_path: Path) -> None:
    project_dir = _write(
        tmp_path,
        {"schema_version": 16, "text_backend_complex": "gemini-vertex/gemini-3.1-flash-lite-preview"},
    )

    migrate_v16_to_v17(project_dir)
    migrated = _load(project_dir)
    assert migrated == {"schema_version": 17, "text_backend_complex": "gemini-vertex/gemini-3.1-flash-lite"}

    (project_dir / "project.json").write_text(
        json.dumps({**migrated, "default_text_backend": "gemini-aistudio/gemini-3.1-flash-lite-preview"}),
        encoding="utf-8",
    )
    migrate_v16_to_v17(project_dir)
    assert _load(project_dir)["default_text_backend"] == "gemini-aistudio/gemini-3.1-flash-lite-preview"
