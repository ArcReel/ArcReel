"""SessionManager._build_session_store reads ARCREEL_SDK_SESSION_STORE env."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_session_store.store import DbSessionStore
from server.agent_runtime.session_manager import SessionManager


async def _fake_provider_env(_self):
    """Stub: 跳过 DB 访问，返回空 dict（不影响 session_store/flush 字段断言）。"""
    return {}


def _build_sm(tmp_path: Path) -> SessionManager:
    """Construct a SessionManager with minimal valid args.

    Uses a stub meta_store since _build_session_store doesn't touch it.
    """

    class _NullMetaStore:
        async def get(self, *a, **kw):
            return None

        async def put(self, *a, **kw):
            return None

    return SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        meta_store=_NullMetaStore(),
    )


def test_store_enabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE", raising=False)
    sm = _build_sm(tmp_path)
    store = sm._build_session_store()
    assert isinstance(store, DbSessionStore)


def test_store_off_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "off")
    sm = _build_sm(tmp_path)
    store = sm._build_session_store()
    assert store is None


def test_store_db_explicit_returns_store(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "db")
    sm = _build_sm(tmp_path)
    store = sm._build_session_store()
    assert isinstance(store, DbSessionStore)


def test_store_uses_session_factory_seam(monkeypatch, tmp_path):
    """If sm._session_factory is set, _build_session_store uses it."""
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE", raising=False)
    sm = _build_sm(tmp_path)

    sentinel = object()
    sm._session_factory = sentinel  # type: ignore[attr-defined]
    sm._user_id = "test-user"  # type: ignore[attr-defined]

    store = sm._build_session_store()
    assert isinstance(store, DbSessionStore)
    # Test the user_id seam took effect
    assert store._user_id == "test-user"


@pytest.mark.asyncio
async def test_flush_mode_passed_to_options_default(monkeypatch, tmp_path):
    """No env → ClaudeAgentOptions.session_store_flush == 'eager'."""
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE_FLUSH", raising=False)
    monkeypatch.setattr(SessionManager, "_build_provider_env_overrides", _fake_provider_env)
    sm = _build_sm(tmp_path)

    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True)

    options = await sm._build_options(project_name="demo")
    assert options.session_store_flush == "eager"


@pytest.mark.asyncio
async def test_flush_mode_passed_to_options_batched(monkeypatch, tmp_path):
    """env=batched → options.session_store_flush == 'batched'."""
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "batched")
    monkeypatch.setattr(SessionManager, "_build_provider_env_overrides", _fake_provider_env)
    sm = _build_sm(tmp_path)
    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True)

    options = await sm._build_options(project_name="demo")
    assert options.session_store_flush == "batched"


@pytest.mark.asyncio
async def test_flush_mode_passed_to_options_when_store_off(monkeypatch, tmp_path):
    """store=off + default flush → options.session_store is None, flush still 'eager'.

    Locks the rollback combination: disabling the DB store must not prevent
    options construction, and flush mode is still propagated (SDK 0.1.73 accepts
    the field regardless of store presence).
    """
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "off")
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE_FLUSH", raising=False)
    monkeypatch.setattr(SessionManager, "_build_provider_env_overrides", _fake_provider_env)
    sm = _build_sm(tmp_path)

    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True)

    options = await sm._build_options(project_name="demo")
    assert options.session_store is None
    assert options.session_store_flush == "eager"
