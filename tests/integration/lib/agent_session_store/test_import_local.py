"""Startup hook: migrate local SDK jsonl transcripts into store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import project_key_for_directory

from lib.agent_session_store.store import DbSessionStore


def _write_fake_local_transcript(project_cwd: Path, session_id: str, sdk_root: Path):
    """Mimic the SDK on-disk layout: <CLAUDE_CONFIG_DIR>/projects/<sanitized>/<session_id>.jsonl."""
    sanitized = project_key_for_directory(str(project_cwd))
    sdk_dir = sdk_root / "projects" / sanitized
    sdk_dir.mkdir(parents=True, exist_ok=True)
    jsonl = sdk_dir / f"{session_id}.jsonl"
    jsonl.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "user",
                    "uuid": f"{session_id}-u1",
                    "timestamp": "2026-05-01T00:00:00Z",
                    "message": {"content": "hi"},
                },
                {
                    "type": "assistant",
                    "uuid": f"{session_id}-u2",
                    "timestamp": "2026-05-01T00:00:01Z",
                    "message": {"content": "hello"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def fake_sdk_home(tmp_path: Path, monkeypatch):
    """Redirect SDK to a tmp config dir for the duration of one test."""
    sdk_home = tmp_path / "claude_home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(sdk_home))
    return sdk_home


@pytest.mark.asyncio
async def test_migrate_imports_local_jsonl(tmp_path, fake_sdk_home, session_factory):
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    proj = projects_root / "demo"
    proj.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-0000000000aa"
    _write_fake_local_transcript(proj, sid, fake_sdk_home)

    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stats = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)

    assert stats["imported"] == 1
    assert stats["skipped"] == 0
    assert stats["failed"] == 0

    loaded = await store.load({"project_key": project_key_for_directory(str(proj)), "session_id": sid})
    assert loaded is not None and len(loaded) == 2
    assert (data_dir / ".session_store_migration_done").exists()


@pytest.mark.asyncio
async def test_migrate_is_idempotent_via_marker(tmp_path, fake_sdk_home, session_factory):
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    proj = projects_root / "demo"
    proj.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-0000000000bb"
    _write_fake_local_transcript(proj, sid, fake_sdk_home)
    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    s1 = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    s2 = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)

    assert s1["imported"] == 1
    assert s2["imported"] == 0 and s2.get("skipped_via_marker") is True


@pytest.mark.asyncio
async def test_migrate_skips_already_in_store_when_marker_missing(
    tmp_path,
    fake_sdk_home,
    session_factory,
):
    """Marker误删后重启应通过 store.load 探测跳过已迁会话。"""
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    proj = projects_root / "demo"
    proj.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-0000000000cc"
    _write_fake_local_transcript(proj, sid, fake_sdk_home)
    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    (data_dir / ".session_store_migration_done").unlink()

    s2 = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    assert s2["imported"] == 0
    assert s2["skipped"] == 1
    assert s2["failed"] == 0


@pytest.mark.asyncio
async def test_migrate_zero_data_user(tmp_path, fake_sdk_home, session_factory):
    """No projects + no SDK dir → marker still written, migration succeeds."""
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stats = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    assert stats == {"imported": 0, "skipped": 0, "failed": 0}
    assert (data_dir / ".session_store_migration_done").exists()
