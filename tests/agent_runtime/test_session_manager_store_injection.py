"""SessionManager._build_session_store reads ARCREEL_SDK_SESSION_STORE env."""

from __future__ import annotations

from pathlib import Path

from lib.agent_session_store.store import DbSessionStore
from server.agent_runtime.session_manager import SessionManager


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
