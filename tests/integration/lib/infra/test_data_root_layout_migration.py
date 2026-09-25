"""数据根布局迁移入口：迁移后存量数据（调用记录、用户记忆、运行时标记）按当前布局可用，重跑不改变结果。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from claude_agent_sdk import project_key_for_directory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.agent_memory_store import AgentMemoryStore
from lib.agent.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent.agent_session_store.store import DbSessionStore
from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.infra.data_root_layout import DataRootLayout
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


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


@pytest.fixture
def sdk_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sdk_home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(sdk_home))
    return sdk_home


async def _migrate_data_root(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    await migrate_data_root_layout(data_root, session_factory=session_factory, sdk_config_dir=sdk_config_dir)


def _write_legacy_user_memory(data_root: Path, user_id: str, files: dict[str, str]) -> None:
    memory_dir = data_root / ".arcreel" / "users" / user_id / "memory"
    memory_dir.mkdir(parents=True)
    for name, body in files.items():
        (memory_dir / name).write_text(body, encoding="utf-8")


def _memory(data_root: Path, user_id: str) -> AgentMemoryStore:
    return AgentMemoryStore(DataRootLayout(data_root).user_memory_dir(user_id))


async def test_user_memory_stays_readable_after_migration_and_rerun(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_user_memory(data_root, "default", {"MEMORY.md": "- [偏好](style.md)\n", "style.md": "冷色调"})
    _write_legacy_user_memory(data_root, "u2", {"MEMORY.md": "- 另一位用户\n"})

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)
    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    assert _memory(data_root, "default").read("style.md").decode("utf-8") == "冷色调"
    assert _memory(data_root, "default").read("MEMORY.md").decode("utf-8") == "- [偏好](style.md)\n"
    assert _memory(data_root, "u2").read("MEMORY.md").decode("utf-8") == "- 另一位用户\n"
    assert ProjectManager(data_root).list_projects() == []


async def test_user_memory_move_resumes_after_interruption(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_user_memory(data_root, "default", {"MEMORY.md": "- 索引\n", "b.md": "后搬"})
    # 上次迁移中断在同一用户的记忆搬了一半时。
    moved = DataRootLayout(data_root).user_memory_dir("default")
    moved.mkdir(parents=True)
    (data_root / ".arcreel" / "users" / "default" / "memory" / "MEMORY.md").replace(moved / "MEMORY.md")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    assert _memory(data_root, "default").read("MEMORY.md").decode("utf-8") == "- 索引\n"
    assert _memory(data_root, "default").read("b.md").decode("utf-8") == "后搬"


async def test_completed_session_import_is_not_rerun_after_migration(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    ProjectManager(data_root).create_project("demo", content_mode="narration")
    project_dir = data_root / "demo"
    transcript_dir = sdk_config_dir / "projects" / project_key_for_directory(str(project_dir))
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "00000000-0000-0000-0000-0000000000aa.jsonl").write_text(
        json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-05-01T00:00:00Z", "message": {"content": "hi"}})
        + "\n",
        encoding="utf-8",
    )
    (data_root / ".session_store_migration_done").write_text("{}", encoding="utf-8")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)
    stats = await migrate_local_transcripts_to_store(
        DbSessionStore(session_factory, user_id="default"), data_root=data_root
    )

    assert stats["imported"] == 0
    assert stats.get("skipped_via_marker") is True
