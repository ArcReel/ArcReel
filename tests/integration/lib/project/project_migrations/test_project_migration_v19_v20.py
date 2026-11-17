"""v19→v20 迁移：只写了内置供应商的项目模型字段，固化为升级前的版本会补全的模型。

期望值是升级前版本的解析结果：0.36.0 在没有更新的同步数据时读自己的出厂快照，裸供应商补全为快照里的默认模型；
默认模型被用户停用或被最低应用版本挡住时报错。迁移后用户看到的生成模型与升级前一致，升级前报错的仍报错。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.backends.text_backends.base import TextTaskType
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotSelectedError
from lib.config import pre_upgrade_recommendations
from lib.config.resolver import ConfigResolver
from lib.db import upgrade_source
from lib.db.models.builtin_provider_model import BuiltinProviderModelState
from lib.infra.app_data_dir import app_data_dir
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v19_to_v20_bare_provider_models import install_bare_provider_models
from lib.workflow.workflow_state import WorkflowStateService
from tests.catalog_support import (
    install_catalog,
    install_synced_catalog,
    with_dataset_version,
    with_min_app_version,
    with_recommendation,
)
from tests.legacy_project_shapes import write_legacy_bare_provider_project

#: 升级前库里的 alembic revision：0.36.0。
REVISION_0_36 = "f6d2a8c4b931"
#: 0.36.0 出厂快照里的默认模型（升级前裸供应商补全成的模型）。
VIDEO_0_36 = "doubao-seedance-2-0-mini-260615"
IMAGE_0_36 = "gpt-image-2"
TEXT_0_36 = "grok-4.3"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def _upgrade_start(sessions, *, disabled: tuple[tuple[str, str], ...] = ()) -> None:
    """升级后首次启动：记下升级前的推荐、装入项目迁移的补全来源，与启动流程一致。"""
    async with sessions() as session:
        for provider_id, model_id in disabled:
            session.add(BuiltinProviderModelState(provider_id=provider_id, model_id=model_id, enabled=False))
        await session.commit()
        await session.run_sync(lambda sync: upgrade_source.record(sync.connection(), REVISION_0_36))
        await session.commit()
        table = await pre_upgrade_recommendations.capture(session)
    install_bare_provider_models(table.model)


def _storyboard_counts(project_dir: Path) -> tuple[int, int]:
    summary = WorkflowStateService(ProjectManager(project_dir.parent.parent)).get_project_summary(project_dir.name)
    storyboards = summary.episodes[0].storyboards
    return storyboards.available, storyboards.stale


@pytest.mark.parametrize("schema_version", [7, 19])
async def test_bare_providers_resolve_to_the_models_used_before_the_upgrade(
    schema_version: int, session_factory, monkeypatch
) -> None:
    """从更早的版本或上一版升级：裸供应商固化为升级前补全成的模型，新目录的推荐不影响，之后也不随目录变化。"""
    snapshot = catalog.factory_snapshot()
    for provider_id, media_type, model in (
        ("ark", "video", "doubao-seedance-2-0-260128"),
        ("openai", "image", "gpt-image-2.5-flare"),
        ("grok", "text", "grok-4.5"),
    ):
        snapshot = with_recommendation(snapshot, provider_id, media_type, model)
    install_catalog(monkeypatch, snapshot)
    project_dir = write_legacy_bare_provider_project(app_data_dir() / "projects", schema_version=schema_version)
    await _upgrade_start(session_factory)

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "project.json")
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert migrated["video_backend"] == f"ark/{VIDEO_0_36}"
    assert migrated["image_provider_t2i"] == f"openai/{IMAGE_0_36}"
    assert migrated["text_backend_complex"] == f"grok/{TEXT_0_36}"
    # 升级前没有可补全的、自定义供应商的与完整值原样保留
    assert (migrated["video_provider_r2v"], migrated["audio_backend"], migrated["default_image_backend"]) == (
        "openai",
        "custom-3",
        "openai/gpt-image-2",
    )
    assert [path.name for path in project_dir.glob("project.json.bak.v19-*")]

    # 用户口径：工作台显示的视频模型、生成用的图片与文本模型与升级前一致，制作状态不变
    resolver = ConfigResolver(session_factory)
    caps = await resolver.video_capabilities_for_project(migrated, generation_type="i2v")
    assert (caps["provider_id"], caps["model"]) == ("ark", VIDEO_0_36)
    image = await resolver.resolve_image_backend(migrated, None, generation_type="t2i")
    assert (image.provider_id, image.model_id) == ("openai", IMAGE_0_36)
    assert await resolver.text_backend_for_task(TextTaskType.SCRIPT, project_dir.name) == ("grok", TEXT_0_36)
    assert _storyboard_counts(project_dir) == (2, 0)


async def test_a_bare_provider_that_failed_before_the_upgrade_still_asks_for_a_model(session_factory, request) -> None:
    """升级前报错的裸供应商迁移后仍报错：ark 的视频默认模型被用户停用，openai 的图片默认模型要求更高的应用版本。

    迁移不替它们补模型，项目设置里的值原样保留，用户看到「没有选定具体模型」并重新选择。
    """
    data = with_dataset_version(catalog.factory_snapshot(), "2026-11-08T00:00:00Z")
    gated = catalog.default_model("openai", "image")
    assert gated is not None
    install_synced_catalog(request, with_min_app_version(data, "openai", gated, "0.36.1"))
    project_dir = write_legacy_bare_provider_project(app_data_dir() / "projects", schema_version=19)
    await _upgrade_start(session_factory, disabled=(("ark", VIDEO_0_36),))

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "project.json")
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert (migrated["video_backend"], migrated["image_provider_t2i"]) == ("ark", "openai/")
    assert migrated["text_backend_complex"] == f"grok/{TEXT_0_36}"

    resolver = ConfigResolver(session_factory)
    with pytest.raises(ModelNotSelectedError) as video_error:
        await resolver.video_capabilities_for_project(migrated, generation_type="i2v")
    assert (video_error.value.code, video_error.value.params) == ("model_not_selected", {"provider": "ark"})
    with pytest.raises(ModelNotSelectedError):
        await resolver.resolve_image_backend(migrated, None, generation_type="t2i")
