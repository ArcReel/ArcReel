"""数据根布局迁移入口：迁移后旧布局的项目、会话与存量数据（调用记录、用户记忆、运行时标记）按当前布局可用，各步骤重跑不改变结果。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from claude_agent_sdk import (
    delete_session_via_store,
    fork_session_via_store,
    get_session_messages_from_store,
    list_sessions_from_store,
    project_key_for_directory,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.agent_memory_store import AgentMemoryStore
from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent.agent_session_store.store import DbSessionStore
from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import run_project_migrations


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
    # 后续步骤失败、完成标记未写时，下次启动各步骤从头重跑。
    DataRootLayout(projects.data_root).layout_migration_marker_path.unlink()
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
    # 后续步骤失败、完成标记未写时，下次启动各步骤从头重跑。
    DataRootLayout(data_root).layout_migration_marker_path.unlink()
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
    project_dir = ProjectManager(data_root).create_project("demo", content_mode="narration")
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


_SESSION_ID = "00000000-0000-0000-0000-0000000000bb"


def _write_legacy_project(data_root: Path, name: str, novel: str = "从前有座山") -> None:
    """旧布局的项目：直接放在数据根下。"""
    scratch = ProjectManager(data_root.parent / "scratch")
    scratch.create_project(name, content_mode="narration")
    project_dir = scratch.get_project_path(name)
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "novel.txt").write_text(novel, encoding="utf-8")
    project_dir.rename(data_root / name)


async def _record_session(store: DbSessionStore, project_dir: Path, session_id: str = _SESSION_ID) -> None:
    """按项目目录派生的会话存储键写入一段对话，与 SDK 在该目录下运行时写入的键一致。"""
    user_uuid = f"{session_id[:-4]}0001"
    await store.append(
        {"project_key": make_project_key(project_dir), "session_id": session_id},
        [
            {
                "type": "user",
                "uuid": user_uuid,
                "parentUuid": None,
                "sessionId": session_id,
                "timestamp": "2026-05-01T00:00:00Z",
                "message": {"role": "user", "content": "画一只猫"},
            },
            {
                "type": "assistant",
                "uuid": f"{session_id[:-4]}0002",
                "parentUuid": user_uuid,
                "sessionId": session_id,
                "timestamp": "2026-05-01T00:00:01Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "好的"}]},
            },
        ],
    )


async def _assert_session_usable(store: DbSessionStore, project_dir: Path) -> None:
    """会话可恢复、标题可见、可分支、可删除——都按项目当前目录定位。"""
    directory = str(project_dir)
    listed = {info.session_id: info for info in await list_sessions_from_store(store, directory=directory)}
    assert listed[_SESSION_ID].summary == "画一只猫"
    assert len(await get_session_messages_from_store(store, _SESSION_ID, directory=directory)) == 2

    forked = await fork_session_via_store(store, _SESSION_ID, directory=directory)
    assert len(await get_session_messages_from_store(store, forked.session_id, directory=directory)) == 2

    await delete_session_via_store(store, _SESSION_ID, directory=directory)
    remaining = {info.session_id for info in await list_sessions_from_store(store, directory=directory)}
    assert remaining == {forked.session_id}


def _novel(manager: ProjectManager, name: str) -> str:
    return (manager.get_project_path(name) / "source" / "novel.txt").read_text(encoding="utf-8")


async def test_legacy_projects_and_their_sessions_are_usable_after_migration(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_project(data_root, "demo")
    _write_legacy_project(data_root, "second", novel="第二个故事")
    store = DbSessionStore(session_factory, user_id="default")
    await _record_session(store, data_root / "demo")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo", "second"]
    assert manager.load_project("demo")
    assert _novel(manager, "second") == "第二个故事"
    await _assert_session_usable(store, manager.get_project_path("demo"))


async def test_second_migration_run_does_nothing(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_project(data_root, "demo")
    await _migrate_data_root(data_root, session_factory, sdk_config_dir)
    # 迁移完成之后数据根下再出现的旧布局形态目录不再被搬动。
    _write_legacy_project(data_root, "late")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    assert ProjectManager(data_root).list_projects() == ["demo"]
    assert (data_root / "late" / "project.json").is_file()


async def test_migration_resumes_when_projects_moved_but_session_keys_not_rewritten(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_project(data_root, "demo")
    store = DbSessionStore(session_factory, user_id="default")
    await _record_session(store, data_root / "demo")
    # 上次迁移中断在项目目录已搬完、会话存储键尚未改写时。
    projects_dir = DataRootLayout(data_root).projects_dir
    projects_dir.mkdir()
    (data_root / "demo").rename(projects_dir / "demo")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo"]
    await _assert_session_usable(store, manager.get_project_path("demo"))


async def test_project_named_projects_keeps_its_name_and_sessions(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_project(data_root, "projects", novel="与容器同名")
    _write_legacy_project(data_root, "demo")
    store = DbSessionStore(session_factory, user_id="default")
    await _record_session(store, data_root / "projects")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo", "projects"]
    assert _novel(manager, "projects") == "与容器同名"
    await _assert_session_usable(store, manager.get_project_path("projects"))


async def test_system_named_entries_are_split_between_projects_and_system_data(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    # 名为 users 的旧项目与旧位置的用户记忆同时存在：项目搬走后，同一次迁移里记忆就位。
    _write_legacy_project(data_root, "users", novel="与用户目录同名")
    _write_legacy_user_memory(data_root, "default", {"MEMORY.md": "- 偏好\n"})
    (data_root / "logs").mkdir()
    (data_root / "logs" / "arcreel.log").write_text("line\n", encoding="utf-8")
    (data_root / "notes").mkdir()
    (data_root / "notes" / "todo.txt").write_text("杂项", encoding="utf-8")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["users"]
    assert _novel(manager, "users") == "与用户目录同名"
    assert _memory(data_root, "default").read("MEMORY.md").decode("utf-8") == "- 偏好\n"
    assert (DataRootLayout(data_root).log_dir / "arcreel.log").read_text(encoding="utf-8") == "line\n"
    assert (manager.projects_dir / "notes" / "todo.txt").read_text(encoding="utf-8") == "杂项"


async def test_failed_migration_is_retried_on_next_run(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_project(data_root, "demo")
    blocking = DataRootLayout(data_root).projects_dir / "demo"
    blocking.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        await _migrate_data_root(data_root, session_factory, sdk_config_dir)
    blocking.rmdir()
    await _migrate_data_root(data_root, session_factory, sdk_config_dir)

    assert ProjectManager(data_root).list_projects() == ["demo"]


async def test_project_left_mid_schema_swap_is_reclaimed_after_migration(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession], sdk_config_dir: Path
) -> None:
    _write_legacy_project(data_root, "demo", novel="交换窗口里的项目")
    store = DbSessionStore(session_factory, user_id="default")
    await _record_session(store, data_root / "demo")
    # 旧版本的项目 schema 迁移停在目录交换的两次改名之间：项目目录只剩 rollback 目录。
    (data_root / "demo").rename(data_root / f".demo.v6-rollback-{'a' * 32}")

    await _migrate_data_root(data_root, session_factory, sdk_config_dir)
    run_project_migrations(DataRootLayout(data_root).projects_dir)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo"]
    assert _novel(manager, "demo") == "交换窗口里的项目"
    await _assert_session_usable(store, manager.get_project_path("demo"))
