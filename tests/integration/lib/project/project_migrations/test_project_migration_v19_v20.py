"""v19→v20 迁移：只写了内置供应商的项目模型字段固化为升级这一刻的推荐模型，升级前后生成用的模型不变。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.backends.text_backends.base import TextTaskType
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.infra.app_data_dir import app_data_dir
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from tests.catalog_support import install_catalog, with_recommendation
from tests.legacy_project_shapes import write_legacy_bare_provider_project


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _recommended(provider_id: str, media_type: str) -> str:
    model = catalog.default_model(provider_id, media_type)
    assert model is not None
    return model


@pytest.mark.parametrize("schema_version", [7, 19])
async def test_bare_providers_resolve_to_the_models_used_before_the_upgrade(
    schema_version: int, db_factory, monkeypatch
) -> None:
    """从更早的版本或上一版升级：裸供应商固化为当时的推荐，此后目录推荐变化不改生成用的模型。"""
    project_dir = write_legacy_bare_provider_project(app_data_dir() / "projects", schema_version=schema_version)
    before = {
        "video": ProviderModel("ark", _recommended("ark", "video")),
        "image": ProviderModel("openai", _recommended("openai", "image")),
        "text": ("grok", _recommended("grok", "text")),
    }

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "project.json")
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert migrated["video_backend"] == "ark/" + before["video"].model_id
    assert migrated["image_provider_t2i"] == "openai/" + before["image"].model_id
    assert migrated["text_backend_complex"] == "grok/" + before["text"][1]
    # 没有推荐可固化的、自定义供应商的与完整值原样保留
    assert (migrated["video_provider_r2v"], migrated["audio_backend"], migrated["default_image_backend"]) == (
        "openai",
        "custom-3",
        "openai/gpt-image-2",
    )
    assert [path.name for path in project_dir.glob("project.json.bak.v19-*")]

    snapshot = catalog.factory_snapshot()
    for provider_id, media_type, model in (
        ("ark", "video", "doubao-seedance-2-0-260128"),
        ("openai", "image", "gpt-image-2.5-flare"),
        ("grok", "text", "grok-4.5"),
    ):
        snapshot = with_recommendation(snapshot, provider_id, media_type, model)
    install_catalog(monkeypatch, snapshot)

    resolver = ConfigResolver(db_factory)
    assert await resolver.resolve_video_backend(migrated, None, generation_type="i2v") == before["video"]
    assert await resolver.resolve_image_backend(migrated, None, generation_type="t2i") == before["image"]
    assert await resolver.text_backend_for_task(TextTaskType.SCRIPT, project_dir.name) == before["text"]
