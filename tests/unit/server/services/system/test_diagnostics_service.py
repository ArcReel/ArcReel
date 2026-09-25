"""diagnostics.collect_diagnostics 行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import server.services.system.diagnostics as diag_mod
from lib.infra.app_data_dir import reset_for_tests
from lib.infra.data_root_layout import DataRootLayout


def test_collect_returns_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    reset_for_tests()

    text = diag_mod.collect_diagnostics()
    assert isinstance(text, str)
    assert "ArcReel diagnostics" in text
    assert "App version" in text
    assert "Python" in text
    assert "OS" in text
    assert "Data directory" in text
    assert "Log directory" in text


def test_collect_masks_db_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://arcuser:supersecretpassword@db.example.com:5432/arcreel",
    )
    reset_for_tests()

    text = diag_mod.collect_diagnostics()
    db_line = next(line for line in text.splitlines() if line.startswith("Database URL:"))
    assert "supersecretpassword" not in db_line
    # 精确匹配脱敏后的 URL：避免 substring 检查导致 CodeQL 误报 URL sanitization 不完整。
    assert db_line == "Database URL: postgresql+asyncpg://••••:••@db.example.com:5432/arcreel"


def test_collect_masks_db_query_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://host.example.com/arcreel?sslmode=require&password=topsecret&token=abc123",
    )
    reset_for_tests()

    text = diag_mod.collect_diagnostics()
    db_line = next(line for line in text.splitlines() if line.startswith("Database URL:"))
    assert "topsecret" not in db_line
    assert "abc123" not in db_line
    # 精确匹配脱敏后完整 URL（key 顺序由 parse_qsl→urlencode 保留输入顺序）。
    assert (
        db_line
        == "Database URL: postgresql://host.example.com/arcreel?sslmode=require&password=%E2%80%A2%E2%80%A2&token=%E2%80%A2%E2%80%A2"
    )


def test_collect_swallows_field_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    reset_for_tests()

    def boom() -> str:
        raise RuntimeError("simulated failure")

    text = diag_mod.collect_diagnostics(app_version=boom)
    assert "<unavailable" in text
    assert "Python" in text


def test_collect_reports_log_dir_under_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(tmp_path / "custom-logs"))
    reset_for_tests()

    text = diag_mod.collect_diagnostics()
    assert f"Log directory: {(tmp_path / 'data' / 'logs').resolve()}" in text
    assert "custom-logs" not in text


def test_collect_lists_data_root_entry_locations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    reset_for_tests()
    layout = DataRootLayout.current()

    reported = {line.split(": ", 1)[1] for line in diag_mod.collect_diagnostics().splitlines() if ": " in line}

    for location in (layout.root, layout.projects_dir, *layout.system_dirs):
        assert str(location) in reported


def test_collect_reports_default_sqlite_url_under_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL")
    reset_for_tests()

    text = diag_mod.collect_diagnostics()

    assert f"Database URL: sqlite+aiosqlite:///{DataRootLayout.current().root / 'arcreel.db'}" in text.splitlines()
