"""数据根布局迁移入口：迁移后存量数据按当前布局可用，重跑不改变结果。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from lib.project.project_manager import ProjectManager


@pytest.fixture
def projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    manager.create_project("demo", content_mode="narration")
    return manager


def _write_artifact(projects: ProjectManager, relative: str) -> None:
    path = projects.get_project_path("demo") / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact")


async def _record_call(session_factory: async_sessionmaker[AsyncSession], output_path: str) -> int:
    async with session_factory() as session:
        repo = UsageRepository(session)
        call_id = await repo.start_call(project_name="demo", call_type="image", model="m")
        await repo.finish_call(
            call_id, status="success", settlement=SettlementInput(cost_amount=0.0), output_path=output_path
        )
    return call_id


async def _recorded_output_path(session_factory: async_sessionmaker[AsyncSession], call_id: int) -> str:
    async with session_factory() as session:
        record = await UsageRepository(session).get_record(call_id)
    assert record is not None
    return record["output_path"]


async def _recorded_updated_at(session_factory: async_sessionmaker[AsyncSession], call_id: int) -> datetime | None:
    async with session_factory() as session:
        return await session.scalar(select(ApiCall.updated_at).where(ApiCall.id == call_id))


async def _migrate(projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    await migrate_data_root_layout(
        projects.data_root, session_factory=session_factory, sdk_config_dir=tmp_path / "claude-config"
    )


async def test_historical_call_records_resolve_artifacts_within_project_after_migration(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_artifact(projects, "characters/婉儿.png")
    _write_artifact(projects, "storyboards/scene_E1S01.png")
    project_dir = projects.get_project_path("demo")
    at_current_location = await _record_call(session_factory, str(project_dir / "characters" / "婉儿.png"))
    # 记录写下后数据根被整体挪过：记录里的前缀已不是当前数据根。
    from_moved_data_root = await _record_call(
        session_factory, str(tmp_path / "old-root" / "demo" / "storyboards" / "scene_E1S01.png")
    )
    already_relative = await _record_call(session_factory, "storyboards/scene_E1S01.png")

    await _migrate(projects, session_factory, tmp_path)

    for call_id in (at_current_location, from_moved_data_root, already_relative):
        recorded = await _recorded_output_path(session_factory, call_id)
        assert not Path(recorded).is_absolute()
        assert (project_dir / recorded).is_file()


async def test_rerunning_migration_leaves_call_records_unchanged(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_artifact(projects, "videos/scene_E1S01.mp4")
    call_id = await _record_call(session_factory, str(projects.get_project_path("demo") / "videos" / "scene_E1S01.mp4"))

    await _migrate(projects, session_factory, tmp_path)
    after_first = await _recorded_output_path(session_factory, call_id)
    updated_after_first = await _recorded_updated_at(session_factory, call_id)
    await _migrate(projects, session_factory, tmp_path)

    assert await _recorded_output_path(session_factory, call_id) == after_first == "videos/scene_E1S01.mp4"
    assert await _recorded_updated_at(session_factory, call_id) == updated_after_first


async def test_moved_root_with_repeated_project_name_does_not_rewrite_ambiguous_path(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_artifact(projects, "scene.png")
    _write_artifact(projects, "data/demo/scene.png")
    stored = str(tmp_path / "old" / "demo" / "data" / "demo" / "scene.png")
    call_id = await _record_call(session_factory, stored)

    await _migrate(projects, session_factory, tmp_path)
    await _migrate(projects, session_factory, tmp_path)

    assert await _recorded_output_path(session_factory, call_id) == stored


async def test_path_escaping_the_project_dir_is_left_unchanged(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    stored = f"{projects.get_project_path('demo')}/../other/image.png"
    call_id = await _record_call(session_factory, stored)

    await _migrate(projects, session_factory, tmp_path)

    assert await _recorded_output_path(session_factory, call_id) == stored
