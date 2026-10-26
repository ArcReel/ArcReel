"""已删除项目换名恢复后，项目会话的 Agent 上下文跟着改到新目录派生的存储键下，续聊与界面历史照常可读。"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import pytest
from claude_agent_sdk import ClaudeAgentOptions, get_session_messages
from claude_agent_sdk._internal.session_resume import materialize_resume_session
from httpx import ASGITransport, AsyncClient

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent.agent_session_store.key_alignment import align_session_keys
from lib.agent.agent_session_store.store import DbSessionStore
from lib.db.repositories.session_repo import SessionRepository
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from lib.project.project_manager import ProjectManager
from server.agent_runtime.event_log import EventLogService, EventLogStore
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from tests.integration.server.routers.projects_router_support import build_projects_client

DELETED = "/api/v1/deleted-projects"
SID = "11111111-2222-4333-8444-555555555555"
OLD_ONLY_SID = "22222222-2222-4333-8444-555555555555"
NEW_DEMO_SID = "33333333-2222-4333-8444-555555555555"


def _transcript(sid: str, cwd: Path) -> list[dict]:
    return [
        {
            "type": "user",
            "uuid": f"{sid[:8]}-0000-4000-8000-000000000001",
            "parentUuid": None,
            "sessionId": sid,
            "cwd": str(cwd),
            "timestamp": "2026-10-25T00:00:00.000Z",
            "isSidechain": False,
            "userType": "external",
            "message": {"role": "user", "content": "记住暗号 42"},
        },
        {
            "type": "assistant",
            "uuid": f"{sid[:8]}-0000-4000-8000-000000000002",
            "parentUuid": f"{sid[:8]}-0000-4000-8000-000000000001",
            "sessionId": sid,
            "cwd": str(cwd),
            "timestamp": "2026-10-25T00:00:01.000Z",
            "isSidechain": False,
            "userType": "external",
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "model": "claude",
                "type": "message",
                "content": [{"type": "text", "text": "好的"}],
            },
        },
    ]


def _create(manager: ProjectManager, title: str) -> Path:
    manager.create_project("demo")
    manager.create_project_metadata("demo", title, "Anime", "narration")
    return manager.get_project_path("demo")


@asynccontextmanager
async def _client(monkeypatch, manager: ProjectManager, session_factory) -> AsyncGenerator[AsyncClient]:
    """与用例同一个事件循环发请求：PostgreSQL 连接池不能跨事件循环使用。"""
    app = build_projects_client(monkeypatch, manager, session_factory=session_factory).app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http


def _write_local_transcript(sdk_projects_dir: Path, key: str, sid: str, cwd: Path) -> None:
    key_dir = sdk_projects_dir / key
    (key_dir / sid / "subagents").mkdir(parents=True)
    lines = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in _transcript(sid, cwd))
    (key_dir / f"{sid}.jsonl").write_text(lines, encoding="utf-8")
    (key_dir / sid / "subagents" / "agent-1.jsonl").write_text("{}\n", encoding="utf-8")


async def test_renamed_restore_moves_session_transcripts_to_the_new_key(tmp_path, monkeypatch, session_factory):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    manager = ProjectManager(tmp_path / "data")
    old_dir = _create(manager, "Old")
    old_key = make_project_key(old_dir)

    store = DbSessionStore(session_factory)
    for sid in (SID, OLD_ONLY_SID):
        await store.append({"project_key": old_key, "session_id": sid}, _transcript(sid, old_dir))
        async with session_factory() as session:
            await SessionRepository(session).create("demo", sid, title="旧会话")
    adapter = SdkTranscriptAdapter(store)
    event_log = EventLogService(EventLogStore(session_factory=session_factory), adapter)
    # SID 已有事件日志（界面打开过）；OLD_ONLY_SID 只有 transcript（事件日志上线前的旧会话）
    assert len(await event_log.list_entries(SID, old_dir)) == 2

    async with _client(monkeypatch, manager, session_factory) as client:
        assert (await client.delete("/api/v1/projects/demo")).status_code == 200
        # 同名新项目占用同一个存储键，它的会话不随恢复搬走
        _create(manager, "New")
        await store.append({"project_key": old_key, "session_id": NEW_DEMO_SID}, _transcript(NEW_DEMO_SID, old_dir))
        async with session_factory() as session:
            await SessionRepository(session).create("demo", NEW_DEMO_SID, title="新会话")
        [entry] = (await client.get(DELETED)).json()["projects"]
        restored = await client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore")
        assert restored.json()["name"] == "demo-2"

    new_dir = manager.get_project_path("demo-2")
    new_key = make_project_key(new_dir)
    assert new_key != old_key

    for sid in (SID, OLD_ONLY_SID):
        assert len(await store.load({"project_key": new_key, "session_id": sid}) or []) == 2
        assert await store.load({"project_key": old_key, "session_id": sid}) is None
        assert len(await adapter.read_raw_messages(sid, new_dir)) == 2
    assert {summary["session_id"] for summary in await store.list_session_summaries(new_key)} == {SID, OLD_ONLY_SID}
    assert {summary["session_id"] for summary in await store.list_session_summaries(old_key)} == {NEW_DEMO_SID}
    assert {row["session_id"] for row in await store.list_sessions(new_key)} == {SID, OLD_ONLY_SID}
    # 新建的同名 demo 的会话原样留在旧键下
    assert len(await store.load({"project_key": old_key, "session_id": NEW_DEMO_SID}) or []) == 2
    assert await store.load({"project_key": new_key, "session_id": NEW_DEMO_SID}) is None

    # 界面历史：已有事件日志的仍完整；只有 transcript 的旧会话按新路径懒生成，同样读得到
    assert len(await event_log.list_entries(SID, new_dir)) == 2
    assert len(await event_log.list_entries(OLD_ONLY_SID, new_dir)) == 2

    # 续聊：SDK 按新路径派生的键从 store 物化 resume
    materialized = await materialize_resume_session(
        ClaudeAgentOptions(cwd=str(new_dir), resume=SID, session_store=store)
    )
    assert materialized is not None
    await materialized.cleanup()


async def test_renamed_restore_moves_local_sdk_transcripts(tmp_path, monkeypatch, session_factory):
    """``ARCREEL_SDK_SESSION_STORE=off`` 时 SDK 只读本地 jsonl，它们同样移到新键目录下。"""
    sdk_projects_dir = tmp_path / "cfg" / "projects"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "off")
    manager = ProjectManager(tmp_path / "data")
    old_dir = _create(manager, "Old")
    old_key = make_project_key(old_dir)
    _write_local_transcript(sdk_projects_dir, old_key, SID, old_dir)
    async with session_factory() as session:
        await SessionRepository(session).create("demo", SID, title="旧会话")

    async with _client(monkeypatch, manager, session_factory) as client:
        assert (await client.delete("/api/v1/projects/demo")).status_code == 200
        _create(manager, "New")
        _write_local_transcript(sdk_projects_dir, old_key, NEW_DEMO_SID, old_dir)
        async with session_factory() as session:
            await SessionRepository(session).create("demo", NEW_DEMO_SID, title="新会话")
        [entry] = (await client.get(DELETED)).json()["projects"]
        assert (await client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore")).json()["name"] == "demo-2"

    new_dir = manager.get_project_path("demo-2")
    new_key_dir = sdk_projects_dir / make_project_key(new_dir)
    assert len(get_session_messages(SID, directory=str(new_dir))) == 2
    assert (new_key_dir / SID / "subagents" / "agent-1.jsonl").is_file()
    assert sorted(path.name for path in (sdk_projects_dir / old_key).iterdir()) == [
        NEW_DEMO_SID,
        f"{NEW_DEMO_SID}.jsonl",
    ]
    assert get_session_messages(NEW_DEMO_SID, directory=str(new_dir)) == []


async def test_restore_under_the_original_name_keeps_the_session_key(tmp_path, monkeypatch, session_factory):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    manager = ProjectManager(tmp_path / "data")
    old_dir = _create(manager, "Old")
    old_key = make_project_key(old_dir)
    store = DbSessionStore(session_factory)
    await store.append({"project_key": old_key, "session_id": SID}, _transcript(SID, old_dir))
    async with session_factory() as session:
        await SessionRepository(session).create("demo", SID, title="旧会话")

    async with _client(monkeypatch, manager, session_factory) as client:
        assert (await client.delete("/api/v1/projects/demo")).status_code == 200
        [entry] = (await client.get(DELETED)).json()["projects"]
        assert (await client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore")).json()["name"] == "demo"

    assert len(await store.load({"project_key": old_key, "session_id": SID}) or []) == 2


@pytest.mark.parametrize("store_enabled", [True, False])
async def test_moved_data_root_then_renamed_restore_keeps_both_projects_sessions(
    tmp_path, monkeypatch, session_factory, store_enabled
):
    sdk_config_dir = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(sdk_config_dir))
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "db" if store_enabled else "off")
    old_root = tmp_path / "A"
    manager = ProjectManager(old_root)
    old_dir = _create(manager, "Old")
    old_key = make_project_key(old_dir)
    store = DbSessionStore(session_factory)
    _write_local_transcript(sdk_config_dir / "projects", old_key, SID, old_dir)
    if store_enabled:
        await store.append({"project_key": old_key, "session_id": SID}, _transcript(SID, old_dir))
    async with session_factory() as session:
        await SessionRepository(session).create("demo", SID)
    async with _client(monkeypatch, manager, session_factory) as client:
        assert (await client.delete("/api/v1/projects/demo")).status_code == 200

    new_root = tmp_path / "B"
    old_root.rename(new_root)
    manager = ProjectManager(new_root)
    occupied_dir = _create(manager, "New")
    occupied_key = make_project_key(occupied_dir)
    _write_local_transcript(sdk_config_dir / "projects", occupied_key, NEW_DEMO_SID, occupied_dir)
    if store_enabled:
        await store.append(
            {"project_key": occupied_key, "session_id": NEW_DEMO_SID}, _transcript(NEW_DEMO_SID, occupied_dir)
        )
    async with session_factory() as session:
        await SessionRepository(session).create("demo", NEW_DEMO_SID)

    # 启动迁移链使用真实实现：布局迁移 → 会话键对齐 → 本地 transcript 导入。
    await migrate_data_root_layout(new_root, session_factory=session_factory, sdk_config_dir=sdk_config_dir)
    await align_session_keys(
        session_factory,
        projects_dir=manager.layout.projects_dir,
        sdk_config_dir=sdk_config_dir,
        store_enabled=store_enabled,
    )
    if store_enabled:
        await migrate_local_transcripts_to_store(store, data_root=new_root)

    async with _client(monkeypatch, manager, session_factory) as client:
        [entry] = (await client.get(DELETED)).json()["projects"]
        restored = await client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore")
        assert restored.json()["name"] == "demo-2"

    restored_dir = manager.get_project_path("demo-2")
    restored_key = make_project_key(restored_dir)
    assert len(get_session_messages(SID, directory=str(restored_dir))) == 2
    assert len(get_session_messages(NEW_DEMO_SID, directory=str(occupied_dir))) == 2
    assert get_session_messages(SID, directory=str(occupied_dir)) == []
    assert (sdk_config_dir / "projects" / restored_key / SID / "subagents" / "agent-1.jsonl").is_file()
    async with session_factory() as session:
        repo = SessionRepository(session)
        assert (await repo.get(SID) or {})["project_name"] == "demo-2"
        assert (await repo.get(NEW_DEMO_SID) or {})["project_name"] == "demo"
    if store_enabled:
        assert len(await store.load({"project_key": restored_key, "session_id": SID}) or []) == 2
        assert await store.load({"project_key": old_key, "session_id": SID}) is None
        assert await store.load({"project_key": occupied_key, "session_id": SID}) is None
        assert len(await store.load({"project_key": occupied_key, "session_id": NEW_DEMO_SID}) or []) == 2
        materialized = await materialize_resume_session(
            ClaudeAgentOptions(cwd=str(restored_dir), resume=SID, session_store=store)
        )
        assert materialized is not None
        await materialized.cleanup()
