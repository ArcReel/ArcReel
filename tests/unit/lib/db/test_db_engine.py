"""Tests for lib.db.engine configuration."""

from pathlib import Path

import pytest

from lib.db.engine import get_database_url, is_sqlite_backend
from lib.infra.app_data_dir import reset_for_tests

_DB_FILE_SUFFIXES = ("", "-wal", "-shm")


@pytest.fixture
def default_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """未设置 ``DATABASE_URL``、数据根指向临时目录；返回数据根。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    reset_for_tests()
    return data_root


def _sqlite_file(url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    assert url.startswith(prefix)
    return Path(url.removeprefix(prefix))


def _write_db_files(directory: Path, name: str, suffixes: tuple[str, ...] = _DB_FILE_SUFFIXES) -> None:
    for suffix in suffixes:
        (directory / f"{name}{suffix}").write_bytes(f"{name}{suffix}".encode())


def _db_file_contents(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in directory.iterdir()}


class TestGetDatabaseUrl:
    def test_default_returns_sqlite_inside_data_root(self, default_sqlite: Path):
        assert _sqlite_file(get_database_url()) == default_sqlite.resolve() / "arcreel.db"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        assert get_database_url() == "postgresql+asyncpg://localhost/test"

    def test_legacy_default_db_files_are_renamed_together(self, default_sqlite: Path):
        _write_db_files(default_sqlite, ".arcreel.db")

        get_database_url()

        assert _db_file_contents(default_sqlite) == {
            f"arcreel.db{suffix}": f".arcreel.db{suffix}".encode() for suffix in _DB_FILE_SUFFIXES
        }

    def test_interrupted_rename_is_completed(self, default_sqlite: Path):
        # 上次改名中断在辅助文件已改、主文件未改时。
        _write_db_files(default_sqlite, ".arcreel.db", ("",))
        _write_db_files(default_sqlite, "arcreel.db", ("-wal", "-shm"))

        get_database_url()

        assert _db_file_contents(default_sqlite) == {
            "arcreel.db": b".arcreel.db",
            "arcreel.db-wal": b"arcreel.db-wal",
            "arcreel.db-shm": b"arcreel.db-shm",
        }

    def test_current_db_wins_over_legacy_db(self, default_sqlite: Path):
        _write_db_files(default_sqlite, "arcreel.db")
        _write_db_files(default_sqlite, ".arcreel.db")
        before = _db_file_contents(default_sqlite)

        get_database_url()

        assert _db_file_contents(default_sqlite) == before

    def test_explicit_database_url_leaves_legacy_db_untouched(
        self, default_sqlite: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_db_files(default_sqlite, ".arcreel.db")
        before = _db_file_contents(default_sqlite)
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        get_database_url()

        assert _db_file_contents(default_sqlite) == before


class TestIsSqliteBackend:
    def test_sqlite(self, default_sqlite: Path):
        assert is_sqlite_backend() is True

    def test_postgresql(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        assert is_sqlite_backend() is False
