"""数据根布局迁移入口：旧布局的全局资产库迁移后照常可读，重跑不改变结果。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.repositories.asset_repo import AssetRepository
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from lib.project.project_manager import ProjectManager

_LEGACY_FILES = (
    "_global_assets/character/hero.png",
    "_global_assets/character/hero.wav",
    "_global_assets/character/hero-armor.png",
    "_global_assets/scene/palace.png",
)


@pytest.fixture
def projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    manager.create_project("demo", content_mode="narration")
    return manager


def _write_legacy_files(projects: ProjectManager) -> None:
    for relative in _LEGACY_FILES:
        path = projects.data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())


async def _seed_legacy_records(session_factory: async_sessionmaker[AsyncSession]) -> tuple[str, str]:
    async with session_factory() as session:
        repo = AssetRepository(session)
        hero = await repo.create(
            type="character",
            name="英雄",
            image_path="_global_assets/character/hero.png",
            audio_path="_global_assets/character/hero.wav",
        )
        await repo.replace_derivatives(hero.id, [("战甲", "黑甲", "_global_assets/character/hero-armor.png")])
        palace = await repo.create(type="scene", name="宫殿", image_path="_global_assets/scene/palace.png")
        await session.commit()
        return hero.id, palace.id


async def _stored_paths(session_factory: async_sessionmaker[AsyncSession], asset_ids: tuple[str, ...]) -> list[str]:
    async with session_factory() as session:
        repo = AssetRepository(session)
        paths: list[str] = []
        for asset_id in asset_ids:
            asset = await repo.get_by_id(asset_id)
            assert asset is not None
            paths.extend(path for path in (asset.image_path, asset.audio_path) if path)
            paths.extend(d.image_path for d in await repo.list_derivatives(asset_id) if d.image_path)
        return paths


async def _updated_at(session_factory: async_sessionmaker[AsyncSession], asset_ids: tuple[str, ...]) -> list[datetime]:
    async with session_factory() as session:
        repo = AssetRepository(session)
        stamps: list[datetime] = []
        for asset_id in asset_ids:
            asset = await repo.get_by_id(asset_id)
            assert asset is not None
            stamps.append(asset.updated_at)
            stamps.extend(d.updated_at for d in await repo.list_derivatives(asset_id))
        return stamps


async def _migrate(projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    await migrate_data_root_layout(
        projects.data_root, session_factory=session_factory, sdk_config_dir=tmp_path / "claude-config"
    )


def _assert_readable_from_global_library(projects: ProjectManager, stored_paths: list[str]) -> None:
    library = projects.get_global_assets_root()
    for stored in stored_paths:
        path = projects.data_root / stored
        assert path.is_file(), stored
        assert path.is_relative_to(library), stored


async def test_legacy_global_assets_are_readable_from_library_after_migration(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_files(projects)
    asset_ids = await _seed_legacy_records(session_factory)

    await _migrate(projects, session_factory, tmp_path)

    stored_paths = await _stored_paths(session_factory, asset_ids)
    assert len(stored_paths) == len(_LEGACY_FILES)
    _assert_readable_from_global_library(projects, stored_paths)
    assert projects.list_projects() == ["demo"]


async def test_rerunning_migration_leaves_global_assets_unchanged(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_files(projects)
    asset_ids = await _seed_legacy_records(session_factory)

    await _migrate(projects, session_factory, tmp_path)
    paths_after_first = await _stored_paths(session_factory, asset_ids)
    stamps_after_first = await _updated_at(session_factory, asset_ids)
    # 后续步骤失败、完成标记未写时，下次启动各步骤从头重跑。
    DataRootLayout(projects.data_root).layout_migration_marker_path.unlink()
    await _migrate(projects, session_factory, tmp_path)

    assert await _stored_paths(session_factory, asset_ids) == paths_after_first
    assert await _updated_at(session_factory, asset_ids) == stamps_after_first
    _assert_readable_from_global_library(projects, paths_after_first)


async def test_legacy_files_join_a_library_created_before_migration(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_files(projects)
    asset_ids = await _seed_legacy_records(session_factory)
    existing = projects.get_global_assets_root() / "prop" / "lamp.png"
    existing.write_bytes(b"lamp")

    await _migrate(projects, session_factory, tmp_path)

    _assert_readable_from_global_library(projects, await _stored_paths(session_factory, asset_ids))
    assert existing.read_bytes() == b"lamp"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows")
async def test_symlinked_legacy_library_is_renamed_as_link(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    external = tmp_path / "external-library"
    external.mkdir()
    (projects.data_root / "_global_assets").symlink_to(external, target_is_directory=True)
    _write_legacy_files(projects)
    asset_ids = await _seed_legacy_records(session_factory)

    await _migrate(projects, session_factory, tmp_path)

    assert projects.get_global_assets_root().is_symlink()
    _assert_readable_from_global_library(projects, await _stored_paths(session_factory, asset_ids))


async def test_conflicting_legacy_file_keeps_its_legacy_record(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_files(projects)
    asset_ids = await _seed_legacy_records(session_factory)
    occupant = projects.get_global_assets_root() / "scene" / "palace.png"
    occupant.parent.mkdir(parents=True, exist_ok=True)
    occupant.write_bytes(b"occupant")

    await _migrate(projects, session_factory, tmp_path)

    stored_paths = await _stored_paths(session_factory, asset_ids)
    assert "_global_assets/scene/palace.png" in stored_paths
    assert (projects.data_root / "_global_assets/scene/palace.png").read_bytes() == b"_global_assets/scene/palace.png"
    assert occupant.read_bytes() == b"occupant"
    migrated = [path for path in stored_paths if path != "_global_assets/scene/palace.png"]
    _assert_readable_from_global_library(projects, migrated)
