"""旧名默认 SQLite 库改名后，只在 ``-wal`` 里的已提交数据仍可读。"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from lib.db.engine import get_database_url
from lib.infra.app_data_dir import reset_for_tests


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(root))
    reset_for_tests()
    return root


def _write_legacy_db_with_uncheckpointed_row(data_root: Path) -> None:
    """在数据根下造一份旧名默认库：最后一次提交只在 ``-wal`` 里，主文件里还没有。"""
    live = data_root.parent / "live"
    live.mkdir()
    conn = sqlite3.connect(live / "db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE notes (body TEXT)")
        conn.execute("INSERT INTO notes VALUES ('kept')")
        conn.commit()
        # 连接仍开着时复制，``-wal`` / ``-shm`` 都还在，提交尚未回写主文件。
        for suffix in ("", "-wal", "-shm"):
            shutil.copyfile(live / f"db{suffix}", data_root / f".arcreel.db{suffix}")
    finally:
        conn.close()


@pytest.mark.parametrize("interrupted", [False, True], ids=["fresh", "sidecars-already-renamed"])
def test_legacy_default_db_is_adopted_with_its_wal(data_root: Path, interrupted: bool):
    _write_legacy_db_with_uncheckpointed_row(data_root)
    if interrupted:
        for suffix in ("-wal", "-shm"):
            (data_root / f".arcreel.db{suffix}").rename(data_root / f"arcreel.db{suffix}")

    url = get_database_url()

    conn = sqlite3.connect(Path(url.removeprefix("sqlite+aiosqlite:///")))
    try:
        assert conn.execute("SELECT body FROM notes").fetchall() == [("kept",)]
    finally:
        conn.close()
    assert not list(data_root.glob(".arcreel.db*"))
