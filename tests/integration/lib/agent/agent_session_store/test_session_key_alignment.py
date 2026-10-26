"""数据根换了绝对路径后启动：会话的 Agent 上下文记录对齐到当前项目目录派生的存储键，续聊照常；键已一致时不写任何东西。"""

from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, get_session_messages
from claude_agent_sdk._internal.session_resume import materialize_resume_session
from sqlalchemy import event

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.key_alignment import align_session_keys
from lib.agent.agent_session_store.store import DbSessionStore
from lib.db.repositories.project_records import retire_project_records
from lib.db.repositories.session_repo import SessionRepository

SID = "11111111-2222-4333-8444-555555555555"
OTHER_SID = "22222222-2222-4333-8444-555555555555"
RETIRED_SID = "33333333-2222-4333-8444-555555555555"


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


def _write_local_transcript(sdk_config_dir: Path, project_dir: Path, sid: str) -> None:
    key_dir = sdk_config_dir / "projects" / make_project_key(project_dir)
    (key_dir / sid / "subagents").mkdir(parents=True)
    lines = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in _transcript(sid, project_dir))
    (key_dir / f"{sid}.jsonl").write_text(lines, encoding="utf-8")
    (key_dir / sid / "subagents" / "agent-1.jsonl").write_text("{}\n", encoding="utf-8")


def _make_project_dirs(root: Path, *names: str) -> Path:
    projects_dir = root / "projects"
    for name in names:
        (projects_dir / name).mkdir(parents=True)
    return projects_dir


async def _create_session(session_factory, project_name: str, sid: str) -> None:
    async with session_factory() as session:
        await SessionRepository(session).create(project_name, sid)


class _WriteCounter:
    """数一个引擎上发出的 INSERT / UPDATE / DELETE 语句。"""

    def __init__(self, session_factory) -> None:
        self.statements: list[str] = []
        self._engine = session_factory.kw["bind"].sync_engine
        event.listen(self._engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, _conn, _cursor, statement: str, *_args) -> None:
        if statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            self.statements.append(statement)

    def close(self) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


async def test_moved_data_root_sessions_resume_under_the_new_key(tmp_path, session_factory):
    sdk_config_dir = tmp_path / "cfg"
    old_projects = _make_project_dirs(tmp_path / "A", "demo", "other")
    store = DbSessionStore(session_factory)
    old_keys = {name: make_project_key(old_projects / name) for name in ("demo", "other")}
    for project_name, sid in (("demo", SID), ("other", OTHER_SID)):
        project_dir = old_projects / project_name
        await store.append({"project_key": old_keys[project_name], "session_id": sid}, _transcript(sid, project_dir))
        _write_local_transcript(sdk_config_dir, project_dir, sid)
        await _create_session(session_factory, project_name, sid)

    (tmp_path / "A").rename(tmp_path / "B")
    new_projects = tmp_path / "B" / "projects"
    result = await align_session_keys(
        session_factory, projects_dir=new_projects, sdk_config_dir=sdk_config_dir, store_enabled=True
    )

    assert result.rewritten == {SID, OTHER_SID}
    for project_name, sid in (("demo", SID), ("other", OTHER_SID)):
        new_dir = new_projects / project_name
        new_key = make_project_key(new_dir)
        assert len(await store.load({"project_key": new_key, "session_id": sid}) or []) == 2
        assert await store.load({"project_key": old_keys[project_name], "session_id": sid}) is None
        assert list((sdk_config_dir / "projects" / old_keys[project_name]).iterdir()) == []
        assert [row["session_id"] for row in await store.list_session_summaries(new_key)] == [sid]
        assert (sdk_config_dir / "projects" / new_key / sid / "subagents" / "agent-1.jsonl").is_file()
        materialized = await materialize_resume_session(
            ClaudeAgentOptions(cwd=str(new_dir), resume=sid, session_store=store)
        )
        assert materialized is not None
        await materialized.cleanup()

    # 再次启动：键已一致，不发任何写语句
    counter = _WriteCounter(session_factory)
    try:
        again = await align_session_keys(
            session_factory, projects_dir=new_projects, sdk_config_dir=sdk_config_dir, store_enabled=True
        )
    finally:
        counter.close()
    assert again.rewritten == set()
    assert again.moved == set()
    assert counter.statements == []


async def test_retired_sessions_follow_the_original_name_so_restore_still_finds_them(tmp_path, session_factory):
    old_projects = _make_project_dirs(tmp_path / "A", "demo")
    store = DbSessionStore(session_factory)
    old_dir = old_projects / "demo"
    await store.append(
        {"project_key": make_project_key(old_dir), "session_id": RETIRED_SID}, _transcript(RETIRED_SID, old_dir)
    )
    await _create_session(session_factory, "demo", RETIRED_SID)
    async with session_factory() as session:
        tombstone, _ = await retire_project_records(session, "demo")
        await session.commit()
    assert tombstone != "demo"

    (tmp_path / "A").rename(tmp_path / "B")
    new_projects = tmp_path / "B" / "projects"
    result = await align_session_keys(
        session_factory, projects_dir=new_projects, sdk_config_dir=tmp_path / "cfg", store_enabled=True
    )

    # 已删除项目恢复时按「当前数据根下的原目录」找旧键，所以挂墓碑名的会话对齐到原名目录的键
    assert result.rewritten == {RETIRED_SID}
    restore_source_key = make_project_key(new_projects / "demo")
    assert len(await store.load({"project_key": restore_source_key, "session_id": RETIRED_SID}) or []) == 2


async def test_sessions_with_records_under_the_current_key_are_left_alone(tmp_path, session_factory):
    projects_dir = _make_project_dirs(tmp_path / "data", "demo")
    store = DbSessionStore(session_factory)
    current_key = make_project_key(projects_dir / "demo")
    await store.append({"project_key": current_key, "session_id": SID}, _transcript(SID, projects_dir / "demo"))
    await store.append({"project_key": "stray-copy", "session_id": SID}, _transcript(SID, projects_dir / "demo"))
    await _create_session(session_factory, "demo", SID)

    counter = _WriteCounter(session_factory)
    try:
        result = await align_session_keys(
            session_factory, projects_dir=projects_dir, sdk_config_dir=tmp_path / "cfg", store_enabled=True
        )
    finally:
        counter.close()

    assert result.rewritten == set()
    assert counter.statements == []
    assert len(await store.load({"project_key": "stray-copy", "session_id": SID}) or []) == 2


async def test_store_off_moves_local_transcripts_of_a_moved_data_root(tmp_path, monkeypatch, session_factory):
    """``ARCREEL_SDK_SESSION_STORE=off`` 时数据库里没有镜像，SDK 只读本地 jsonl，它们移到新键目录下。"""
    sdk_config_dir = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(sdk_config_dir))
    old_projects = _make_project_dirs(tmp_path / "A", "demo")
    _write_local_transcript(sdk_config_dir, old_projects / "demo", SID)
    await _create_session(session_factory, "demo", SID)

    (tmp_path / "A").rename(tmp_path / "B")
    new_dir = tmp_path / "B" / "projects" / "demo"
    assert get_session_messages(SID, directory=str(new_dir)) == []
    result = await align_session_keys(
        session_factory, projects_dir=new_dir.parent, sdk_config_dir=sdk_config_dir, store_enabled=False
    )

    assert result.moved == {SID}
    assert len(get_session_messages(SID, directory=str(new_dir))) == 2
    assert (sdk_config_dir / "projects" / make_project_key(new_dir) / SID / "subagents" / "agent-1.jsonl").is_file()

    again = await align_session_keys(
        session_factory, projects_dir=new_dir.parent, sdk_config_dir=sdk_config_dir, store_enabled=False
    )
    assert again.moved == set()
