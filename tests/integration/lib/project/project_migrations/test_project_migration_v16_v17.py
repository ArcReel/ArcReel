"""v16→v17 迁移：项目文本 backend 里已退役的 Flash-Lite preview ID 改为正式 ID。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.backends.text_backends.base import TextTaskType
from lib.config.resolver import ConfigResolver
from lib.infra.app_data_dir import app_data_dir
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from tests.legacy_project_shapes import write_legacy_retired_flash_lite_project, write_legacy_storyboard_project

_PROVIDERS = ["gemini-aistudio", "gemini-vertex"]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("provider_id", _PROVIDERS)
async def test_schema_16_project_with_retired_flash_lite_resolves_the_formal_id(provider_id: str, db_factory) -> None:
    project_dir = write_legacy_retired_flash_lite_project(app_data_dir() / "projects", provider_id=provider_id)
    legacy = _read_json(project_dir / "project.json")
    assert legacy["schema_version"] == 16

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "project.json")
    assert migrated == {
        **legacy,
        "default_text_backend": f"{provider_id}/gemini-3.1-flash-lite",
        "text_backend_simple": f"{provider_id}/gemini-3.1-flash-lite",
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    assert migrated["text_backend_complex"] == "gemini-aistudio/gemini-3-flash-preview"
    assert [path.name for path in project_dir.glob("project.json.bak.v16-*")]

    resolver = ConfigResolver(db_factory)
    assert await resolver.text_backend_for_task(TextTaskType.STYLE_ANALYSIS, project_dir.name) == (
        provider_id,
        "gemini-3.1-flash-lite",
    )
    assert await resolver.text_backend_for_task(TextTaskType.SCRIPT, project_dir.name) == (
        "gemini-aistudio",
        "gemini-3-flash-preview",
    )


@pytest.mark.parametrize("provider_id", _PROVIDERS)
def test_upgrade_from_an_early_schema_carries_the_rewrite_to_the_current_schema(
    tmp_path: Path, provider_id: str
) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    project_path = project_dir / "project.json"
    legacy = _read_json(project_path)
    retired = f"{provider_id}/gemini-3.1-flash-lite-preview"
    legacy.update({"default_text_backend": retired, "text_backend_complex": retired})
    project_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_path)
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert migrated["default_text_backend"] == f"{provider_id}/gemini-3.1-flash-lite"
    assert migrated["text_backend_complex"] == f"{provider_id}/gemini-3.1-flash-lite"
    assert "text_backend_simple" not in migrated
